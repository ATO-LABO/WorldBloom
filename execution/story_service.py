"""Adapters for Studio candidates and saved experiment candidates."""
from pathlib import Path
import json
import subprocess
import tempfile
from copy import deepcopy
from execution.provenance import (ConfigError, canonical, code_snapshot, contained,
    materialize, python_executable, read_json, sha256, verify_files, atomic_json)
from execution.output_store import OutputStore, verified
from execution.story_store import StoryStore
from gapengine.story_materials import read_inputs, extract_materials
from gapengine.story_plan import create_plan

def organize(control, repo, raw, inputs, binding, *, length=None):
    code, runtime = code_snapshot(Path(repo))
    binding = {**deepcopy(binding), "runtime_manifest_sha256": sha256(canonical(runtime))}
    artifacts = {"inputs/layers.jsonl": raw, **{"inputs/"+p:b for p,b in inputs.items()},
                 **{"runtime/"+p:b for p,b in code.items()}, "runtime-manifest.json":canonical(runtime)}
    # Run extraction from the same captured code as the provenance manifest.
    with tempfile.TemporaryDirectory(prefix="wb-story-material-") as tmp:
        root = Path(tmp)
        materialize(root, artifacts)
        atomic_json(root / "source-binding.json", binding)
        command = [python_executable(), "-I", "-B", str(root / "runtime/scripts/story_materials.py"),
                   "--extract-internal", str(root)]
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0)
        if result.returncode:
            raise ConfigError("materials", "固定ランタイムから素材を抽出できません")
        material = read_json(root / "materials.json")
    store = StoryStore(control)
    store.save_material(material, artifacts=artifacts)
    current = store.current(binding["run_id"], binding["candidate_id"])
    if current and current["plan"]["material_ref"]["material_id"] == material["material_id"]:
        return current
    plan = create_plan(material, length=length)
    revision = current["plan"]["revision"] if current else 0
    plan["revision"] = revision + 1
    from gapengine.story_plan import revise_identity
    plan = revise_identity(plan)
    return store.set_current(binding["run_id"], binding["candidate_id"], plan, expected_revision=revision)

def organize_studio(configs, catalog, run_id, candidate_id, config_id):
    config, manifest, cfg_root = configs._bundle(config_id)
    snapshot = catalog.snapshot(run_id)
    candidate = next((c for c in snapshot["candidates"]["candidates"] if c["candidate_id"] == candidate_id), None)
    if not candidate or not candidate["screenable"]:
        raise ConfigError("candidate_id", "到達済みの原記録が一致する候補が必要です")
    root, legacy = catalog.resolve(run_id)
    if not legacy and read_json(contained(root, "manifest.json"))["input_manifest_sha256"] != config["input_manifest_sha256"]:
        raise ConfigError("config_id", "実行時と異なる設定は使えません")
    raw = verified(contained(root, candidate["log"]["relative_path"]), candidate["source_log_sha256"])
    inputs = read_inputs(cfg_root / "inputs/projects" / config["project_id"],
                         cfg_root / "inputs/templates" / config["template_id"])
    binding = {k:candidate[k] for k in ("generation","individual","seed") if k in candidate}
    binding.update(run_id=run_id, candidate_id=candidate_id, source_log_sha256=candidate["source_log_sha256"],
        config_sha256=sha256(canonical(config)), input_manifest_sha256=config["input_manifest_sha256"])
    return organize(configs.control, configs.repo, raw, inputs, binding)

def organize_experiment(control, repo, selected, project, template, *, run_id="ait101-comparison", length=None):
    item = read_json(selected)
    raw = Path(item["layers_path"]).read_bytes()
    if sha256(raw) != item["layers_sha256"]:
        raise ConfigError("source", "実験の元ログが変更されています")
    binding = {"run_id":run_id,"candidate_id":item["internal"],
        "generation":item["generation"],"individual":item["individual"],"seed":item["seed"],
        "source_log_sha256":item["layers_sha256"],"blind_id":item["blind_id"]}
    return organize(control, repo, raw, read_inputs(project, template), binding, length=length)

def process_story_output(store, output_id, cid):
    """Local-only processing of a saved model response. Safe to repeat."""
    from gapengine.story_validation import inspect_story
    request = store.request(output_id)
    if request.get("pipeline") != "story_v1":
        return None
    store.verify_artifacts(output_id)
    item = store.sink(output_id, cid).current()
    if not item or item.get("status") != "ok":
        return None
    root = contained(store.folder(output_id), "inputs/story/" + cid)
    material, plan, _ = pinned_story(store.folder(output_id), cid, request)
    text = verified(contained(store.folder(output_id), item["text_ref"]), item["text_sha256"]).decode("utf-8")
    result = inspect_story(text, material, plan)
    folder = store.sink(output_id, cid).folder
    data = canonical(result)
    name = "story-result-" + sha256(data)[:24] + ".json"
    atomic_json(folder/name, result)
    atomic_json(folder/"story-result-pointer.json", {"path":name,"sha256":sha256(data)})
    return result

def read_story_output(store, output_id, cid):
    folder = store.sink(output_id, cid).folder
    try:
        pointer = read_json(folder/"story-result-pointer.json")
    except FileNotFoundError:
        return None
    result = json.loads(verified(contained(folder,pointer["path"]),pointer["sha256"]))
    item = store.sink(output_id,cid).current()
    if not item or result["validation"]["raw_text_sha256"] != item.get("text_sha256"):
        raise ConfigError("story", "本文と検査記録の版が一致しません", code="snapshot_changed")
    material, plan, _ = pinned_story(store.folder(output_id),cid,store.request(output_id))
    if (result["validation"]["material_sha256"] != material["content_sha256"]
            or result["validation"]["plan_sha256"] != sha256(canonical(plan))):
        raise ConfigError("story", "検査記録と固定した骨格の版が異なります", code="snapshot_changed")
    return result

def prepare_standalone(control, plan_id, *, backend, model, limits, output_id=None):
    """CLI/evaluation adapter using the same OutputStore and transport as Studio."""
    import uuid
    from gapengine.story_narration import build_story_prompt
    store = StoryStore(control)
    ref = store.reference(plan_id)
    material, plan, confirmation = store.resolve(ref)
    cid, rid = material["source_binding"]["candidate_id"], material["source_binding"]["run_id"]
    _, material_blobs = store._read("materials", ref["material_id"])
    blobs = {p:b for p,b in material_blobs.items() if p != "materials.json"}
    story_path = "inputs/story/" + cid + "/"
    blobs.update({story_path+"materials.json":canonical(material),story_path+"plan.json":canonical(plan),
                  story_path+"confirmation.json":canonical(confirmation)})
    # The extractor's captured runtime also contains the writer; build under it.
    with tempfile.TemporaryDirectory(prefix="wb-story-prompt-") as tmp:
        root = Path(tmp)
        materialize(root, blobs)
        command = [python_executable(), "-I", "-B", str(root/"runtime/scripts/story_narrate.py"),
                   "--build-internal", str(root), "--candidate", cid]
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name=="nt" else 0)
        if result.returncode:
            raise ConfigError("prompt", "固定した素材からプロンプトを構築できません")
        prompt = (root/"built-prompt.txt").read_text(encoding="utf-8")
    oid = output_id or "out-story-" + uuid.uuid4().hex
    request = {"schema_version":2,"pipeline":"story_v1","story_refs":{cid:ref},
        "output_id":oid,"request_id":"req-"+uuid.uuid4().hex,"job_id":"cli-"+uuid.uuid4().hex,
        "kind":"narrate","run_id":rid,"candidate_ids":[cid],"backend":backend,"model":model,
        "limits":limits,"mode":"regenerate","synopsis_refs":{},"sources":[{"candidate_id":cid,
        "source_log_sha256":material["source_binding"]["source_log_sha256"]}],
        "runtime_manifest_sha256":material["source_binding"]["runtime_manifest_sha256"]}
    OutputStore(control).create(request,{cid:prompt},artifacts=blobs)
    return request


def pinned_story(root, cid, request):
    """Verify copied story references without consulting a mutable live head."""
    from gapengine.story_materials import verify_material
    from gapengine.story_plan import validate_plan
    prefix = contained(root, "inputs/story/" + cid)
    material, plan, confirmation = (read_json(prefix / name) for name in
                                   ("materials.json", "plan.json", "confirmation.json"))
    verify_material(material); validate_plan(material, plan)
    ref = request["story_refs"][cid]
    actual = {"material_id":material["material_id"],"material_sha256":material["content_sha256"],
              "plan_id":plan["plan_id"],"plan_sha256":sha256(canonical(plan)),
              "confirmation_sha256":sha256(canonical(confirmation))}
    binding = material["source_binding"]
    source = next((s for s in request["sources"] if s["candidate_id"] == cid),None)
    if (actual != ref or binding["candidate_id"] != cid or binding["run_id"] != request["run_id"]
            or not source or source["source_log_sha256"] != binding["source_log_sha256"]
            or confirmation.get("plan_id") != plan["plan_id"]
            or confirmation.get("plan_sha256") != actual["plan_sha256"]
            or confirmation.get("material_sha256") != material["content_sha256"]):
        raise ConfigError("story_refs", "固定した骨格の参照が一致しません", code="snapshot_changed")
    return material, plan, confirmation
