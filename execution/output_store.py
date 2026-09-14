"""Durable generation attempts; receipts are authoritative, index is a projection."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import time
import uuid

from execution.provenance import (ConfigError, atomic_json, canonical, contained,
    directory_lock, identifier, read_json, sha256, write_bytes)
from execution.generation import Response, result, validate_response, aggregate

REQUEST_FIELDS = frozenset({"schema_version", "output_id", "request_id", "job_id", "kind", "run_id", "settings_provenance",
    "config_id", "publication_revision", "candidates_sha256", "selection_revision", "selection_sha256",
    "candidate_ids", "sources", "backend", "model", "limits", "mode", "synopsis_refs",
    "input_manifest_sha256", "config_sha256", "runtime_manifest_sha256", "acknowledge_unknown", "attempt_ids"})


def verified(path, digest):
    data = path.read_bytes()
    if not isinstance(digest, str) or sha256(data) != digest:
        raise ConfigError("output", "保存済み生成記録のSHAが一致しません", code="snapshot_changed")
    return data


class OutputStore:
    def __init__(self, control_root):
        self.root = contained(control_root, "outputs")

    def folder(self, output_id):
        return contained(self.root, identifier(output_id, "output_id"))

    def create(self, request, prompts, *, artifacts=None):
        if not isinstance(request, dict) or set(request) - REQUEST_FIELDS:
            raise ConfigError("request", "未対応の生成要求項目があります")
        ids = request.get("candidate_ids")
        if (not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or set(prompts) != set(ids)):
            raise ConfigError("candidate_ids", "空でない重複しない対象候補が必要です")
        for cid in ids:
            identifier(cid, "candidate_id")
        limits = request.get("limits")
        if (not isinstance(limits, dict) or set(limits) !=
                {"max_calls", "call_timeout_seconds", "wall_seconds", "max_saved_response_bytes"}
                or any(type(v) is not int or v < (0 if k == "max_calls" else 1) for k, v in limits.items())):
            raise ConfigError("limits", "生成上限が不正です")
        for prompt in prompts.values():
            if not isinstance(prompt, str) or not prompt.strip():
                raise ConfigError("prompt", "生成プロンプトが空です")
        final = self.folder(request["output_id"])
        self.root.mkdir(parents=True, exist_ok=True)
        with directory_lock(self.root):
            if final.exists():
                raise ConfigError("output_id", "生成版が既に存在します", code="conflict")
            from execution.provenance import publish_directory
            pending = contained(self.root, ".pending-" + uuid.uuid4().hex)
            pending.mkdir()
            doc = deepcopy(request)
            doc["schema_version"] = 1
            atomic_json(pending / "request.json", doc)
            item_hashes = {}
            for cid in ids:
                item = contained(pending, "items/" + cid)
                raw = prompts[cid].encode("utf-8")
                write_bytes(item / "prompt.txt", raw)
                atomic_json(item / "item.json", {"schema_version": 1, "output_id": request["output_id"],
                    "candidate_id": cid, "attempt_id": "attempt-" + uuid.uuid4().hex,
                    "prompt_sha256": sha256(raw)})
                item_hashes[cid] = sha256((item / "item.json").read_bytes())
            atomic_json(pending / "request-seal.json", {"schema_version": 1,
                        "sha256": sha256(canonical(doc)), "items": item_hashes,
                        "artifacts": {p: sha256(b) for p, b in (artifacts or {}).items()}})
            for path, data in (artifacts or {}).items():
                if path.split("/")[0] not in {"inputs", "runtime", "config.json", "input-manifest.json",
                        "runtime-manifest.json", "selection.json", "source-plan.json", "generation-archive.json"}:
                    raise ConfigError("artifacts", "保存対象外のファイルです")
                write_bytes(contained(pending, path), data)
            atomic_json(pending / "quota.json", {"schema_version": 1, "started": [], "created_at": time.time()})
            publish_directory(pending, final)
        self.project(request["output_id"])
        return self.request(request["output_id"])

    def request(self, output_id):
        folder = self.folder(output_id)
        seal = read_json(contained(folder, "request-seal.json"))
        import json
        doc = json.loads(verified(contained(folder, "request.json"), seal["sha256"]))
        if doc.get("output_id") != output_id:
            raise ConfigError("output_id", "生成版の識別子が一致しません", code="snapshot_changed")
        if set(seal.get("items", {})) != set(doc["candidate_ids"]):
            raise ConfigError("output", "候補集合が一致しません", code="snapshot_changed")
        for cid, digest in seal["items"].items():
            verified(contained(folder, "items/" + identifier(cid, "candidate_id") + "/item.json"), digest)
        return doc

    def verify_artifacts(self, output_id):
        folder = self.folder(output_id)
        self.request(output_id)
        seal = read_json(folder / "request-seal.json")
        for path, digest in seal.get("artifacts", {}).items():
            verified(contained(folder, path), digest)
        return seal

    def sink(self, output_id, candidate_id):
        return AttemptSink(self, output_id, candidate_id)

    def project(self, output_id):
        request = self.request(output_id)
        entries = []
        for cid in request["candidate_ids"]:
            sink = self.sink(output_id, cid)
            entry = sink.current()
            if entry is None:
                entry = {**sink.identity, "status": "running" if sink.started() else "pending"}
            entries.append(entry)
        payload = {"schema_version": 1, "output_id": output_id, "entries": entries}
        if not any(e["status"] in ("pending", "running") for e in entries):
            payload.update(aggregate(entries))
        atomic_json(contained(self.folder(output_id), "index.json"), payload)
        return payload

    def recover(self, output_id, *, owner_stopped=False, stopped="interrupted"):
        if not owner_stopped:
            raise ConfigError("output_id", "所有workerの停止確認が必要です", code="conflict")
        folder = self.folder(output_id)
        with directory_lock(folder):
            request = self.request(output_id)
            for cid in request["candidate_ids"]:
                self.sink(output_id, cid).recover(stopped=stopped)
            return self.project(output_id)


class AttemptSink:
    def __init__(self, store, output_id, candidate_id):
        self.store = store
        self.request = store.request(output_id)
        if candidate_id not in self.request["candidate_ids"]:
            raise ConfigError("candidate_id", "生成要求にない候補です")
        self.output = store.folder(output_id)
        self.folder = contained(self.output, "items/" + identifier(candidate_id, "candidate_id"))
        item = read_json(contained(self.folder, "item.json"))
        if item.get("output_id") != output_id or item.get("candidate_id") != candidate_id:
            raise ConfigError("candidate_id", "稿の識別子が一致しません", code="snapshot_changed")
        self.identity = {k: item[k] for k in ("output_id", "candidate_id", "attempt_id")}
        self.prompt_sha256 = item["prompt_sha256"]

    def call_request(self, credentials=None):
        return {**self.identity, "backend": self.request["backend"], "model": self.request["model"],
                "limits": deepcopy(self.request["limits"]), "prompt_sha256": self.prompt_sha256,
                "prompt": verified(self.folder / "prompt.txt", self.prompt_sha256).decode("utf-8"),
                "credentials": credentials or {}}

    def check(self, request):
        if self.current() is not None or self.started():
            raise ConfigError("attempt_id", "開始済みの生成は再送できません", code="conflict")
        expected = self.call_request()
        if any(request.get(k) != v for k, v in expected.items() if k != "credentials"):
            raise ConfigError("request", "固定された生成要求と一致しません", code="snapshot_changed")
        if self.current() is not None or self.started():
            raise ConfigError("attempt_id", "開始済みの生成は再送できません", code="conflict")

    def started(self):
        # quota reservation is also evidence of a possibly sent call.
        quota = read_json(contained(self.output, "quota.json"))
        return self.identity["attempt_id"] in quota["started"] or (self.folder / "call-started.json").exists()

    def reserve(self):
        with directory_lock(self.output):
            if self.current() is not None or self.started():
                raise ConfigError("attempt_id", "生成呼出しは既に予約されています", code="conflict")
            quota = read_json(self.output / "quota.json")
            limits = self.request["limits"]
            if (len(quota["started"]) >= min(limits["max_calls"], len(self.request["candidate_ids"]))
                    or time.time() - quota["created_at"] >= limits["wall_seconds"]):
                return False
            quota["started"].append(self.identity["attempt_id"])
            atomic_json(self.output / "quota.json", quota)
            atomic_json(self.folder / "call-started.json", {"schema_version": 1, **self.identity,
                        "stage": "dispatch", "call_state": "started", "started_at": time.time()})
        return True

    def receive(self, response):
        limit = self.request["limits"]["max_saved_response_bytes"]
        raw = response.raw[:limit]
        write_bytes(self.folder / "response.bin", raw)
        ref = "items/" + self.identity["candidate_id"] + "/response.bin"
        receipt = {"schema_version": 1, **self.identity, "response_ref": ref,
                   "response_sha256": sha256(raw), "truncated": response.truncated or len(response.raw) > limit}
        atomic_json(self.folder / "response.json", receipt)
        return {k: receipt[k] for k in ("response_ref", "response_sha256")}

    def received(self):
        try:
            receipt = read_json(self.folder / "response.json")
        except FileNotFoundError:
            return None
        if any(receipt.get(k) != v for k, v in self.identity.items()):
            raise ConfigError("receipt", "応答の識別子が一致しません", code="snapshot_changed")
        raw = verified(contained(self.output, receipt["response_ref"]), receipt["response_sha256"])
        return receipt, Response(raw, receipt["truncated"])

    def current(self):
        try:
            pointer = read_json(self.folder / "current.json")
        except FileNotFoundError:
            return None
        import json
        record = json.loads(verified(contained(self.folder, pointer["path"]), pointer["sha256"]))
        if any(record.get(k) != v for k, v in self.identity.items()):
            raise ConfigError("receipt", "結果の識別子が一致しません", code="snapshot_changed")
        if record.get("response_ref") is not None:
            verified(contained(self.output, record["response_ref"]), record["response_sha256"])
        if record.get("text_ref") is not None:
            verified(contained(self.output, record["text_ref"]), record["text_sha256"])
        return record

    def finish(self, record, *, text=None):
        record = deepcopy(record)
        if any(record.get(k) != v for k, v in self.identity.items()):
            raise ConfigError("receipt", "結果の識別子が一致しません")
        if text is not None:
            raw = text.encode("utf-8")
            if len(raw) > self.request["limits"]["max_saved_response_bytes"]:
                raise ValueError("body exceeds storage limit")
            body_name = "body-" + uuid.uuid4().hex + ".txt"
            write_bytes(self.folder / body_name, raw)
            record.update(text_ref="items/" + self.identity["candidate_id"] + "/" + body_name, text_sha256=sha256(raw))
        name = "receipts/" + uuid.uuid4().hex + ".json"
        atomic_json(contained(self.folder, name), record)
        atomic_json(self.folder / "current.json", {"schema_version": 1, "path": name, "sha256": sha256(canonical(record))})
        try:
            self.store.project(self.identity["output_id"])
        except OSError:
            # Durable success receipt is authoritative. Rebuild the index locally.
            pass
        return record

    def persistence_failure(self, error):
        received = self.received()
        if received:
            receipt, _ = received
            return self.finish(result(self.identity, "error", "persistence_failed", stage="persist",
                call_state="response_received", retry_policy="local_recovery_only", cause_type=type(error).__name__,
                **{k: receipt[k] for k in ("response_ref", "response_sha256")}))
        return self.finish(result(self.identity, "unknown", "persistence_outcome_unknown", stage="persist",
            call_state="started", retry_policy="explicit_confirmation", cause_type=type(error).__name__))

    def recover(self, *, stopped):
        current = self.current()
        if current is not None and current["retry_policy"] != "local_recovery_only":
            return current
        received = self.received()
        if received:
            receipt, response = received
            refs = {k: receipt[k] for k in ("response_ref", "response_sha256")}
            try:
                text = validate_response(self.request["backend"], response)
            except (ValueError, RuntimeError, KeyError, TypeError):
                return self.finish(result(self.identity, "error", "response_invalid", stage="validate",
                    call_state="response_received", retry_policy="safe_new_request", **refs))
            return self.finish(result(self.identity, "ok", "completed", call_state="response_received", **refs), text=text)
        if self.started():
            return self.finish(result(self.identity, "unknown", "dispatch_unknown", stage="receive",
                call_state="started", retry_policy="explicit_confirmation"))
        if stopped == "limit":
            return self.finish(result(self.identity, "skipped_limit", "limit_reached", retry_policy="new_budget_request"))
        return self.finish(result(self.identity, "skipped_cancelled" if stopped == "cancelled" else "skipped_interrupted",
            "cancelled_before_start" if stopped == "cancelled" else "interrupted_before_start", retry_policy="safe_new_request"))
