"""Validate explicit generation requests and freeze their source bindings.

Admission runs under the job-table lock. Expensive runtime copying and prompt
construction run later in the owned preparation process.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from execution.configs import generation_availability, _model
from execution.output_store import OutputStore, verified
from execution.provenance import (ConfigError, canonical, contained, directory_lock,
    identifier, read_json, sha256, code_snapshot)
from execution.worker import TERMINAL

FIELDS = {"schema_version", "request_id", "kind", "config_id", "run_id", "selection_revision",
          "candidate_ids", "backend", "model", "limits", "mode", "synopsis_refs",
          "acknowledge_unknown", "attempt_ids"}


def normalize(request):
    if not isinstance(request, dict) or set(request) - FIELDS:
        raise ConfigError("request", "未対応の生成要求項目があります")
    if request.get("schema_version", 1) != 1 or type(request.get("schema_version", 1)) is not int:
        raise ConfigError("schema_version", "未対応の版です")
    doc = {"schema_version": 1}
    for key in ("request_id", "config_id", "run_id"):
        doc[key] = identifier(request.get(key), key)
    if request.get("kind") not in ("synopsize", "narrate"):
        raise ConfigError("kind", "生成種別が不正です")
    doc["kind"] = request["kind"]
    revision = request.get("selection_revision")
    if type(revision) is not int or revision < 0:
        raise ConfigError("selection_revision", "選定版を指定してください")
    doc["selection_revision"] = revision
    for field in ("candidate_ids", "attempt_ids"):
        values = request.get(field, [] if field == "attempt_ids" else None)
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ConfigError(field, "識別子の配列を指定してください")
        if len(values) != len(set(values)) or (field == "candidate_ids" and not values):
            raise ConfigError(field, "空または重複した対象は指定できません")
        doc[field] = sorted(identifier(v, field) for v in values)
    backend = request.get("backend")
    from gapengine.synopsis import BACKENDS
    if not isinstance(backend, str) or backend not in BACKENDS or "model" not in request:
        raise ConfigError("backend", "生成方式と明示モデルが必要です")
    doc["backend"], doc["model"] = backend, _model(request["model"])
    if (backend == "none" and doc["model"] is not None) or (backend != "none" and doc["model"] is None):
        raise ConfigError("model", "生成方式に対応する明示モデルを指定してください")
    limits = request.get("limits")
    if (not isinstance(limits, dict) or set(limits) !=
        {"max_calls", "call_timeout_seconds", "wall_seconds", "max_saved_response_bytes"}
        or any(type(v) is not int or v < (0 if k == "max_calls" else 1) for k, v in limits.items())):
        raise ConfigError("limits", "実行上限をすべて整数で明示してください")
    doc["limits"] = deepcopy(limits)
    mode = request.get("mode")
    if mode not in ("missing_or_failed", "regenerate"):
        raise ConfigError("mode", "生成方法を指定してください")
    doc["mode"] = mode
    ack = request.get("acknowledge_unknown", False)
    if type(ack) is not bool or bool(doc["attempt_ids"]) != ack:
        raise ConfigError("attempt_ids", "結果不明の再生成には確認と対象試行IDが必要です")
    doc["acknowledge_unknown"] = ack
    refs = request.get("synopsis_refs", {})
    if not isinstance(refs, dict) or set(refs) - set(doc["candidate_ids"]):
        raise ConfigError("synopsis_refs", "候補ごとのあらすじ参照を指定してください")
    if doc["kind"] == "synopsize" and refs:
        raise ConfigError("synopsis_refs", "あらすじ生成には入力あらすじを指定できません")
    doc["synopsis_refs"] = {}
    for cid in doc["candidate_ids"]:
        ref = refs.get(cid)
        if ref is not None:
            if not isinstance(ref, dict) or set(ref) != {"output_id", "attempt_id", "text_sha256"}:
                raise ConfigError("synopsis_refs", "生成版・試行ID・本文SHAを指定してください")
            identifier(ref["output_id"], "output_id"); identifier(ref["attempt_id"], "attempt_id")
            if not isinstance(ref["text_sha256"], str) or len(ref["text_sha256"]) != 64:
                raise ConfigError("synopsis_refs", "本文SHAが不正です")
        doc["synopsis_refs"][cid] = deepcopy(ref)
    return doc


def eligible(store, request):
    """Never silently resend unknown or locally recoverable prior attempts."""
    history = {cid: [] for cid in request["candidate_ids"]}
    if store.root.exists():
        for folder in sorted(store.root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            prior = store.request(folder.name)
            if prior["run_id"] != request["run_id"] or prior["kind"] != request["kind"]:
                continue
            for cid in set(prior["candidate_ids"]) & set(history):
                sink = store.sink(folder.name, cid)
                item = sink.current()
                # A crash before receipt must first be reconciled, never treated as missing.
                history[cid].append(item or {**sink.identity,
                    "status": "unknown" if sink.started() else "pending",
                    "retry_policy": "explicit_confirmation" if sink.started() else "safe_new_request"})
    accepted, required = [], set()
    for cid, entries in history.items():
        unknown = {e["attempt_id"] for e in entries if e["status"] == "unknown"}
        blocked_local = any(e["retry_policy"] == "local_recovery_only" for e in entries)
        if blocked_local:
            continue
        if unknown:
            if not request["acknowledge_unknown"] or not unknown <= set(request["attempt_ids"]):
                continue
            required.update(unknown)
        elif request["mode"] == "missing_or_failed" and any(e["status"] in ("ok", "prompt_only") for e in entries):
            continue
        accepted.append(cid)
    if set(request["attempt_ids"]) != required:
        raise ConfigError("attempt_ids", "再生成対象の結果不明試行と確認IDが一致しません")
    if not accepted:
        raise ConfigError("candidate_ids", "生成可能な対象がありません。過去稿または復旧状態を確認してください")
    return accepted


def admit(jobs, request, *, settings_path=None):
    from execution.selections import SelectionStore
    from viewer.run_catalog import RunCatalog
    config = jobs.configs.get(request["config_id"])
    if any(request[k] != config["generation"][k] for k in ("backend", "model", "limits")):
        raise ConfigError("config_id", "生成設定が保存版と一致しません。変更は新しい設定版へ保存してください")
    if not generation_availability(config["generation"], settings_path)["available"]:
        raise ConfigError("backend", "明示モデル・実行環境・資格情報を確認してください", code="unavailable")
    catalog = RunCatalog(jobs.configs.runs, jobs.configs.control)
    root, legacy = catalog.resolve(request["run_id"])
    if not legacy:
        manifest = jobs.configs.verify_run(request["run_id"])
        if not any(j["job_id"] == manifest["job_id"] and j["state"] in TERMINAL for j in jobs._all()):
            raise ConfigError("run_id", "終了したGAジョブを確認できません", code="conflict")
        if manifest["input_manifest_sha256"] != config["input_manifest_sha256"]:
            raise ConfigError("config_id", "GA時と異なる入力は文章化に利用できません")
    lock_root = catalog.legacy / request["run_id"] if legacy else root
    with directory_lock(lock_root):
        selected = SelectionStore(catalog).get(request["run_id"],
            revision=request["selection_revision"] or None)
        if selected["revision"] != request["selection_revision"]:
            raise ConfigError("selection_revision", "選定版が更新されています", code="conflict")
        snapshot = catalog.snapshot(request["run_id"], revision=selected["source_publication_revision"],
            manifest_sha256=selected["source_manifest_sha256"])
        by_id = {c["candidate_id"]: c for c in snapshot["candidates"]["candidates"]}
        adopted = {e["candidate_id"] for e in selected["entries"] if e["state"] == "adopted"}
        for cid in request["candidate_ids"]:
            if cid not in by_id or not by_id[cid]["screenable"]:
                raise ConfigError("candidate_ids", "到達済みで原記録が一致する候補だけ生成できます")
            if request["kind"] == "narrate" and cid not in adopted:
                raise ConfigError("candidate_ids", "上映対象は固定選定版の採用候補だけです")
        accepted = eligible(OutputStore(jobs.configs.control), request)
        selection_doc = {k: v for k, v in selected.items() if k != "sha256"}
        return {"schema_version": 1, "request": deepcopy(request), "candidate_ids": accepted,
            "candidates": [by_id[cid] for cid in accepted], "archive": snapshot["archive"],
            "representatives": catalog.representatives(snapshot), "selection": selection_doc,
            "selection_sha256": sha256(canonical(selection_doc)),
            "publication_revision": snapshot["revision"], "publication_manifest_sha256": snapshot["manifest_sha256"],
            "candidates_sha256": snapshot["candidates_sha256"], "config_sha256": sha256(canonical(config)),
            "input_manifest_sha256": config["input_manifest_sha256"],
            "settings_provenance": "posthoc_generation_config" if legacy else "recorded_input"}


def prepare(configs, job, plan):
    """Copy fixed sources and code, then publish one complete output version."""
    from viewer.run_catalog import RunCatalog
    import tempfile
    from execution.provenance import materialize, verify_files
    request = plan["request"]
    config, inputs, cfg_root = configs._bundle(request["config_id"])
    if sha256(canonical(config)) != plan["config_sha256"]:
        raise ConfigError("config_id", "受付時の設定と一致しません", code="snapshot_changed")
    catalog = RunCatalog(configs.runs, configs.control)
    root, legacy = catalog.resolve(request["run_id"])
    snapshot = catalog.snapshot(request["run_id"], revision=plan["publication_revision"],
        manifest_sha256=plan["publication_manifest_sha256"], observe=False)
    if snapshot["candidates_sha256"] != plan["candidates_sha256"]:
        raise ConfigError("candidate_ids", "受付時の候補と一致しません", code="snapshot_changed")
    blobs = {"inputs/" + r["path"]: verified(contained(cfg_root / "inputs", r["path"]), r["sha256"])
             for r in inputs["files"]}
    sources = []
    for c in plan["candidates"]:
        path = "inputs/candidates/" + c["candidate_id"] + "/layers.jsonl"
        blobs[path] = verified(contained(root, c["log"]["relative_path"]), c["source_log_sha256"])
        sources.append({"candidate_id": c["candidate_id"], "source_log_sha256": c["source_log_sha256"],
                        "output_relative_path": path})
    code, runtime = code_snapshot(configs.repo)
    blobs.update({"runtime/" + p: b for p, b in code.items()})
    blobs.update({"config.json": canonical(config), "input-manifest.json": canonical(inputs),
        "runtime-manifest.json": canonical(runtime), "selection.json": canonical(plan["selection"]),
        "source-plan.json": canonical(plan)})
    refs = {}
    store = OutputStore(configs.control)
    for cid in plan["candidate_ids"]:
        ref = request["synopsis_refs"][cid]
        refs[cid] = ref
        if ref is None:
            continue
        prior = store.request(ref["output_id"])
        item = store.sink(ref["output_id"], cid).current()
        if (prior["kind"] != "synopsize" or prior["run_id"] != request["run_id"]
            or prior.get("input_manifest_sha256") != plan["input_manifest_sha256"] or item is None
            or item["status"] != "ok" or item["attempt_id"] != ref["attempt_id"]
            or item["text_sha256"] != ref["text_sha256"]):
            raise ConfigError("synopsis_refs", "固定候補・入力・あらすじ版が一致しません")
        raw = verified(contained(store.folder(ref["output_id"]), item["text_ref"]), ref["text_sha256"])
        blobs["inputs/synopses/" + cid + ".txt"] = raw
    # Prompt construction must execute from the captured runtime too. Importing
    # builders from a mutable checkout could otherwise mix two code versions.
    with tempfile.TemporaryDirectory(prefix="wb-output-prompts-") as temp:
        tmp = Path(temp)
        materialize(tmp, blobs)
        verify_files(tmp / "runtime", runtime["files"])
        built = subprocess.run([sys.executable, "-I", "-B",
            str(tmp / "runtime/execution/output_worker.py"), "--build", str(tmp)],
            cwd=tmp, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if built.returncode:
            raise ConfigError("prompt", "固定入力からのプロンプト構築に失敗しました")
        result = read_json(tmp / "build-result.json")
    prompts, archive = result["prompts"], result["archive"]
    blobs["generation-archive.json"] = canonical(archive)
    output = {**request, "output_id": job["output_id"], "job_id": job["job_id"],
        "candidate_ids": plan["candidate_ids"], "sources": sources, "synopsis_refs": refs,
        **{k: plan[k] for k in ("publication_revision", "candidates_sha256", "selection_sha256",
                                "input_manifest_sha256", "config_sha256", "settings_provenance")},
        "runtime_manifest_sha256": sha256(canonical(runtime))}
    store.create(output, prompts, artifacts=blobs)
    return {"schema_version": 1, "output_id": job["output_id"],
            "runtime_manifest_sha256": output["runtime_manifest_sha256"],
            "argv": [sys.executable, "-I", "-B",
                     str(store.folder(job["output_id"]) / "runtime/execution/output_worker.py"),
                     "--control", str(configs.control), "--output", job["output_id"]]}
