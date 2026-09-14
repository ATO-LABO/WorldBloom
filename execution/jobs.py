"""Persistent requests and independently supervised jobs; no in-memory queue."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager, ExitStack
import threading
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from execution.configs import ConfigStore
from execution.provenance import (ConfigError, atomic_json, canonical, contained,
    directory_lock, identifier, publish_directory, read_json, sha256, write_bytes)
from execution import worker

PUBLIC_FIELDS = frozenset({"schema_version", "job_id", "request_id", "kind", "config_id", "run_id",
    "state", "phase", "revision", "created_at", "updated_at", "started_at", "finished_at", "heartbeat",
    "cancel_requested_at", "error", "exit_code", "progress", "reconciliation", "publication_revision",
    "output_id", "completion_kind", "counts"})

_ENTRY_DIGESTS: dict[tuple[str, int, int], str] = {}


def _file_digest(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    digest = _ENTRY_DIGESTS.get(key)
    if digest is None:
        digest = _ENTRY_DIGESTS[key] = sha256(path.read_bytes())
    return digest


class JobStore:
    def __init__(self, configs: ConfigStore, *, cancel_grace_seconds=30, startup_timeout=15):
        self.configs = configs
        self.root = configs.control / "jobs"
        self.cancel_grace = cancel_grace_seconds
        self.startup_timeout = startup_timeout

    @contextmanager
    def _lock(self):
        contained(self.configs.control, "jobs").mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            deadline = time.monotonic() + 2
            while True:
                try:
                    stack.enter_context(directory_lock(self.root))
                    break
                except ConfigError as error:
                    if error.code != "conflict" or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
            yield

    def _folder(self, jid):
        return contained(self.root, identifier(jid, "job_id"))

    def _read(self, jid):
        try:
            folder = self._folder(jid)
            job = read_json(folder / "job.json")
            if (job["job_id"] != jid or job["entrypoint"] != str((folder / "worker.py").resolve())
                    or sha256((folder / "request.json").read_bytes()) != job["request_hash"]):
                raise ConfigError("job_id", "保存済み要求の整合性が失われています", code="snapshot_changed")
            return job
        except FileNotFoundError as error:
            raise ConfigError("job_id", "ジョブがありません", code="not_found") from error

    def _save(self, job, **updates):
        if job.get("output_id") and updates.get("state") in worker.TERMINAL:
            if not worker.output_tree_stopped(job):
                return {**job, "reconciliation": "unknown"}
            from execution.output_store import OutputStore
            from execution.generation import aggregate
            outputs = OutputStore(self.configs.control)
            stopped = "cancelled" if job.get("cancel_requested_at") or updates["state"] == "cancelled" else "limit" if (updates.get("error") or {}).get("code") == "wall_timeout" else "interrupted"
            try:
                if not outputs.folder(job["output_id"]).is_dir():
                    summary = None
                else:
                    recovered = outputs.recover(job["output_id"], owner_stopped=True, stopped=stopped)
                    summary = aggregate(recovered["entries"], stopped=stopped if updates["state"] in ("cancelled", "interrupted") else None)
            except (ValueError, OSError, KeyError, TypeError):
                # A broken output must not block the job ledger; output(output_id) surfaces the error.
                updates["reconciliation"] = "unknown"
            else:
                if summary is not None:
                    updates.update(completion_kind=summary["completion_kind"], counts=summary["counts"],
                        progress={"completed": len(recovered["entries"]), "total": len(recovered["entries"]), "counts": summary["counts"]})
                    if updates["state"] not in ("cancelled", "interrupted"):
                        # The entry set decides the job state (contract §14). A wall limit that
                        # left every entry ok did not affect the output, so it is not an error.
                        updates["state"] = summary["state"]
                        if summary["state"] == "succeeded":
                            updates["error"] = None
        job.update(updates, updated_at=time.time(), revision=job["revision"] + 1)
        atomic_json(self._folder(job["job_id"]) / "job.json", job)
        worker.record_event(self._folder(job["job_id"]), job)
        return job

    def _identity_status(self, job):
        receipt = job.get("receipt")
        identity = job.get("launch_identity")
        if receipt:
            if (receipt.get("nonce") != job["nonce"] or receipt.get("entrypoint") != job["entrypoint"]
                    or receipt.get("worker_sha256") != job["worker_sha256"]):
                return "unknown"
            identity = receipt.get("identity")
        if not identity:
            return "unknown"
        if job.get("launch_identity") and identity != job["launch_identity"]:
            return "unknown"
        try:
            if _file_digest(Path(job["entrypoint"])) != job["worker_sha256"]:
                return "unknown"
        except OSError:
            return "unknown"
        return worker.probe(identity)

    def _reconcile(self, job):
        if job["state"] in worker.TERMINAL:
            return job
        status = self._identity_status(job)
        if status == "dead":
            return self._save(job, state="cancelled" if job.get("cancel_requested_at") is not None else "interrupted", finished_at=time.time(),
                              reconciliation="confirmed", error={"code":"worker_disappeared"})
        if status == "unknown":
            # A late supervisor must claim this job under the same lock before
            # doing work, so a never-launched request can safely be interrupted.
            if (job["state"] in ("queued", "stopping") and not job.get("launch_identity") and not job.get("receipt")
                    and time.time() - job["created_at"] >= self.startup_timeout):
                return self._save(job, state="interrupted", finished_at=time.time(),
                                  reconciliation="confirmed", error={"code":"launch_unconfirmed"})
            return {**job, "reconciliation":"unknown"}
        timed_out = time.time() - job["created_at"] >= job["wall_seconds"]
        if timed_out or (job.get("cancel_requested_at") is not None
                and time.time() - job["cancel_requested_at"] >= job["cancel_grace_seconds"]):
            identity = (job.get("receipt") or {}).get("identity") or job.get("launch_identity")
            if worker.terminate_verified(identity):
                return self._save(job, state="cancelled" if job.get("cancel_requested_at") is not None else "failed",
                                  error=None if job.get("cancel_requested_at") is not None else {"code":"wall_timeout"},
                                  finished_at=time.time(), reconciliation="confirmed")
            return {**job, "reconciliation":"unknown"}
        return {**job, "reconciliation":"confirmed"}

    @staticmethod
    def public(job):
        return deepcopy({k: v for k, v in job.items() if k in PUBLIC_FIELDS})

    def _all(self):
        if not self.root.exists():
            return []
        return [self._read(p.name) for p in sorted(self.root.iterdir())
                if p.is_dir() and p.name.startswith("job-")]

    def get(self, jid):
        identifier(jid, "job_id")
        if not self.root.exists():
            raise ConfigError("job_id", "ジョブがありません", code="not_found")
        with self._lock():
            return self.public(self._reconcile(self._read(jid)))

    def list(self):
        if not self.root.exists():
            return []
        with self._lock():
            return [self.public(self._reconcile(j)) for j in self._all()]

    def submit(self, request, *, settings_path=None):
        if not isinstance(request, dict):
            raise ConfigError("request", "オブジェクトを指定してください")
        kind = request.get("kind")
        generating = kind in ("synopsize", "narrate")
        if generating:
            from execution.output_requests import normalize
            request = normalize(request)
            rid, cid = request["request_id"], request["config_id"]
        else:
            if set(request) - {"request_id", "kind", "config_id"}:
                raise ConfigError("request", "未対応の要求項目があります")
            rid = identifier(request.get("request_id"), "request_id")
            if kind != "evolve":
                raise ConfigError("kind", "処理種別が不正です")
            cid = identifier(request.get("config_id"), "config_id")
            request = {"schema_version":1, "request_id":rid, "kind":kind, "config_id":cid}
        request_hash = sha256(canonical(request))
        jid = "job-" + hashlib.sha256(rid.encode()).hexdigest()
        with self._lock():
            final = self._folder(jid)
            if final.exists():
                prior = self._read(jid)
                if prior["request_hash"] != request_hash:
                    raise ConfigError("request_id", "同じ要求IDに異なる内容が指定されました", code="conflict")
                return self.public(self._reconcile(prior)), False
            config = self.configs.get(cid)
            for other in self._all():
                if self._reconcile(other)["state"] not in worker.TERMINAL:
                    raise ConfigError("jobs", "他の処理が実行中または状態確認中です", code="conflict")
            plan = None
            if generating:
                from execution.output_requests import admit
                plan = admit(self, request, settings_path=settings_path)
            # Probe the containment API before publishing a request.
            try:
                tree = worker.ProcessTree(); tree.close()
            except OSError as error:
                raise ConfigError("worker", "プロセス管理を利用できません", code="unavailable") from error
            nonce = secrets.token_hex(32)
            pending = contained(self.root, ".pending-" + nonce)
            pending.mkdir()
            entry = final / "worker.py"
            source = Path(worker.__file__).read_bytes()
            write_bytes(pending / "worker.py", source)
            run_id = request["run_id"] if generating else "run-" + secrets.token_hex(16)
            now = time.time()
            job = {"schema_version":1, "job_id":jid, "request_id":rid, "request_hash":request_hash,
                   "nonce":nonce, "kind":kind, "config_id":cid, "run_id":run_id,
                   "state":"queued", "phase":"preparing", "revision":0, "created_at":now,
                   "updated_at":now, "entrypoint":str(entry.resolve()), "worker_sha256":sha256(source),
                   "launch_identity":None, "receipt":None, "cancel_requested_at":None,
                   "cancel_grace_seconds":self.cancel_grace, "error":None,
                   "wall_seconds":config["execution_limits"]["wall_seconds"],
                   "progress":{"completed_individuals":0,"completed_seeds":0,
                       "total_individuals":config["preview"]["planned_individual_evaluations"],
                       "total_seeds":config["preview"]["planned_seed_evaluations"],
                       "detail_available":False}}
            if generating:
                job.update(output_id="out-" + secrets.token_hex(16),
                    output_plan_sha256=sha256(canonical(plan)),
                    settings_path=str(Path(settings_path).absolute()) if settings_path is not None else None,
                    wall_seconds=request["limits"]["wall_seconds"],
                    progress={"completed": 0, "total": len(plan["candidate_ids"]), "counts": {}})
                atomic_json(pending / "output-plan.json", plan)
            atomic_json(pending / "request.json", request)
            atomic_json(pending / "job.json", job)
            publish_directory(pending, final)
            argv = [sys.executable, "-I", "-B", str(entry), "--control", str(self.configs.control),
                    "--runs", str(self.configs.runs), "--repo", str(self.configs.repo),
                    "--job", jid, "--nonce", nonce]
            try:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
                threading.Thread(target=process.wait, daemon=True).start()
                identity = worker.process_identity(process.pid)
                self._save(job, launch_identity=identity)
            except OSError as error:
                self._save(job, state="failed", finished_at=time.time(), error={"code":"launch_failed"})
                raise ConfigError("worker", "監視プロセスを起動できません", code="unavailable") from error
            return self.public(job), True

    def cancel(self, jid):
        with self._lock():
            job = self._reconcile(self._read(jid))
            if job["state"] in worker.TERMINAL:
                return self.public(job)
            if job["state"] == "queued":
                now = time.time()
                saved = self._save(job, state="cancelled", cancel_requested_at=now, finished_at=now)
                if saved["state"] != "cancelled":
                    # _save declined the terminal write because the output tree is still alive.
                    saved = self._save(job, state="stopping", cancel_requested_at=now)
                return self.public(saved)
            if job.get("cancel_requested_at") is None:
                # The worker observes this before starting the GA, including when
                # the cancel request races its initial acknowledgement.
                job = self._save(job, state="stopping", cancel_requested_at=time.time())
            return self.public(job)

    def output(self, output_id, *, recover=False):
        from execution.output_store import OutputStore
        from execution.generation import aggregate
        with self._lock():
            outputs = OutputStore(self.configs.control)
            try:
                request = outputs.request(output_id)
            except FileNotFoundError as error:
                raise ConfigError("output_id", "生成版がありません", code="not_found") from error
            job = self._reconcile(self._read(request["job_id"]))
            stopped = job["state"] in worker.TERMINAL and worker.output_tree_stopped(job)
            if recover and not stopped:
                raise ConfigError("output_id", "所有workerの停止確認が必要です", code="conflict")
            if stopped:
                reason = "cancelled" if job["state"] == "cancelled" else "limit" if (job.get("error") or {}).get("code") == "wall_timeout" else "interrupted"
                payload = outputs.recover(output_id, owner_stopped=True, stopped=reason)
            else:
                payload = outputs.project(output_id)
            if job["state"] in ("cancelled", "interrupted"):
                payload.update(aggregate(payload["entries"], stopped=job["state"]))
            return {**payload, "request": request, "job_state": job["state"]}

    def outputs(self):
        from execution.output_store import OutputStore
        root = OutputStore(self.configs.control).root
        if not root.exists():
            return []
        listed = []
        for p in sorted(root.iterdir()):
            if not (p.is_dir() and p.name.startswith("out-")):
                continue
            # One damaged output must not hide the others; the row carries its own error.
            try:
                listed.append(self.output(p.name))
            except ConfigError as error:
                listed.append({"output_id": p.name, "error": {"code": error.code, "message": str(error)}})
            except (OSError, ValueError, KeyError, TypeError):
                listed.append({"output_id": p.name, "error": {"code": "storage_error", "message": "保存済み生成記録を処理できません"}})
        return listed

    def assert_run_idle(self, run_id):
        if not self.root.exists():
            return
        with self._lock():
            for job in self._all():
                if job["run_id"] == run_id and self._reconcile(job)["state"] not in worker.TERMINAL:
                    raise ConfigError("run_id", "実行中の結果は選定できません", code="conflict")
