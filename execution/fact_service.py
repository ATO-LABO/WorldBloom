"""Pinned fact packets are built and checked from each job's captured runtime."""
import json
from execution.provenance import ConfigError, canonical, contained, read_json, sha256, write_bytes, directory_lock
from execution.output_store import verified
from execution.fact_store import verify_sources
from gapengine.fact_contract import verify_package, packet
from gapengine.fact_validation import inspect_output


def pinned_package(root, cid, request):
    package = read_json(contained(root, "inputs/facts/" + cid + "/package.json"))
    verify_package(package)
    ref = request["fact_refs"][cid]
    source = package["supplement"]["source_binding"]
    logsha = next(s["source_log_sha256"] for s in request["sources"] if s["candidate_id"] == cid)
    if (ref != {"package_id": package["id"], "package_sha256": package["sha256"]}
            or any(source.get(k) != v for k, v in {"run_id": request["run_id"], "candidate_id": cid,
                    "source_log_sha256": logsha, "config_sha256": request["config_sha256"],
                    "input_manifest_sha256": request["input_manifest_sha256"]}.items())):
        raise ConfigError("facts", "固定した入力の参照が一致しません", code="snapshot_changed")
    raw = contained(root, "inputs/candidates/" + cid + "/layers.jsonl").read_bytes()
    inputs = {p: contained(root, "inputs/" + p).read_bytes() for p in source["input_files"]}
    verify_sources(package, raw, inputs)
    if read_json(contained(root, "inputs/facts/" + cid + "/packet.json")) != packet(package, request["kind"]):
        raise ConfigError("facts", "固定した選択入力が一致しません", code="snapshot_changed")
    return package


def process_fact_output(store, output_id, cid):
    request = store.request(output_id)
    if request.get("pipeline") != "facts_v1":
        raise ConfigError("pipeline", "事実保持経路の生成結果が必要です")
    store.verify_artifacts(output_id)
    sink = store.sink(output_id, cid)
    item = sink.current()
    if item is None or item["status"] != "ok":
        return None
    raw = verified(contained(store.folder(output_id), item["text_ref"]), item["text_sha256"]).decode("utf-8")
    package = pinned_package(store.folder(output_id), cid, request)
    checked = inspect_output(raw, package, request["kind"])
    path = sink.folder / ("facts-validation-" + sha256(canonical(checked)) + ".json")
    with directory_lock(sink.folder):
        if not path.exists():
            write_bytes(path, canonical(checked))
        elif json.loads(path.read_bytes()) != checked:
            raise ConfigError("validation", "保存済み検証結果が一致しません")
    return checked


def synopsis_from_output(store, output_id, cid):
    request = store.request(output_id)
    if request.get("pipeline") != "facts_v1" or request["kind"] != "synopsize":
        raise ConfigError("synopsis", "同じ事実保持経路のあらすじが必要です")
    checked = process_fact_output(store, output_id, cid)
    if (checked is None or checked["validation"]["structural_status"] != "passed"
            or checked["validation"]["typed_meaning_status"] != "passed"):
        raise ConfigError("synopsis", "あらすじの構造または型付き意味を確認できません")
    item = store.sink(output_id, cid).current()
    return {"text": checked["text"], "output_id": output_id, "candidate_id": cid,
            "attempt_id": item["attempt_id"], "raw_text_sha256": item["text_sha256"],
            "text_sha256": sha256(checked["text"].encode("utf-8"))}
