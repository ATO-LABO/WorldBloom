"""Temporary, synthetic approval records for storage and freezing unit tests.

These are not approvals of real project patches; end-to-end tests use check.
"""
import hashlib
import json
from pathlib import Path
import yaml
from gapengine.world_patch import EMPTY_STACK_DIGEST, next_digest, patch_id_for, read_stack
from gapengine.world_patch_inputs import inputs_digest, read_subjects, resolve_references

ROOT = Path(__file__).resolve().parents[1]


def frozen_experiment(root, *, explanations=True, coevolve=False):
    import shutil
    from execution.configs import ConfigStore
    from gapengine.evolve import evolve
    root = Path(root)
    repo = root / "repo"
    project, template = repo / "projects/momotaro", repo / "templates/momotaro"
    shutil.copytree(ROOT / "projects/momotaro", project, ignore=shutil.ignore_patterns("patches"))
    shutil.copytree(ROOT / "templates/momotaro", template)
    for package in ("engine", "gapengine", "scripts", "execution"):
        shutil.copytree(ROOT / package, repo / package, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ROOT / "requirements.txt", repo / "requirements.txt")
    path = project / "world.yaml"
    world = yaml.safe_load(path.read_text(encoding="utf-8"))
    world["time"] = {"days": 1, "slots": ["朝", "昼", "夕"]}
    world["daily_events"] = None
    world["scheduled_events"] = [{"id": "fixture_end", "day": 1, "slot": "夕", "targets": ["桃太郎"],
        "label": "試験の帰還", "grants_item": {"name": "鬼ヶ島の宝物", "count": 1}, "move_to": "村"}]
    path.write_text(yaml.safe_dump(world, allow_unicode=True, sort_keys=False), encoding="utf-8")
    store = ConfigStore(repo, root / "control", root / "runs")
    spec = {"label": "パッチ試験", "project_id": "momotaro", "template_id": "momotaro",
            "evolution": {"generations": 2, "population": 12, "seeds": 2, "seed_base": 31,
                          "ga_seed": 29, "processes": 1, "keep": "all", "record_explanations": explanations,
                          "coevolve": coevolve}}
    store.save(spec, config_id="cfg-fixture")
    manifest = store.prepare_run("cfg-fixture", run_id="run-fixture", job_id="job-fixture")
    experiment = root / "runs/run-fixture"
    evolve({**manifest["evolution"], "out": experiment,
            "project": experiment / "inputs/projects/momotaro",
            "template": experiment / "inputs/templates/momotaro"})
    return experiment, project, template


def write_approved(project, patch, template=None, repo=None):
    project = Path(project)
    folder = project / "patches"
    folder.mkdir(parents=True, exist_ok=True)
    stack = read_stack(project)
    patch = dict(patch, id=patch_id_for(patch["add"]), parent_digest=stack["head"])
    patch.pop("approved_seq", None)
    patch.pop("parent_rev", None)
    raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    reason = {"reason": "単体試験専用の合成された承認記録です"}
    evidence = {"seed_set": "holdout", "patched_inputs_digest": "test-patched",
                "patch_rules_version": 2, "trial_rules_version": 3}
    gate = {"patch_id": patch["id"], "patch_sha256": sha, "status": "reviewable",
            "trial": {"evidence": evidence}, "approval": reason}
    gate_raw = json.dumps(gate, ensure_ascii=False).encode("utf-8")
    (folder / f"{patch['id']}.yaml").write_bytes(raw)
    (folder / f"{patch['id']}.gate.json").write_bytes(gate_raw)
    revision = {"rev": len(stack["revisions"]) + 1, "patch_id": patch["id"],
                "parent_digest": stack["head"], "digest": next_digest(stack["head"], sha),
                "patch_sha256": sha, "gate_sha256": hashlib.sha256(gate_raw).hexdigest(),
                "patched_inputs_digest": "test-patched", "rules_version": 2,
                "trial_rules_version": 3, "approved_at": "test", "approval": reason}
    stack["revisions"].append(revision)
    stack["head"] = revision["digest"]
    stack["base_inputs_digest"], stack["template_id"] = "test-base", "test-template"
    if template:
        world = yaml.safe_load((project / "world.yaml").read_text(encoding="utf-8"))
        refs = resolve_references(world, project, repo) if repo else resolve_references(world, project)
        stack["base_inputs_digest"] = inputs_digest(world, read_subjects(project / "subjects"), template, refs)
        stack["template_id"] = Path(template).name
    (folder / "stack.json").write_text(json.dumps(stack, ensure_ascii=False), encoding="utf-8")
    return patch
