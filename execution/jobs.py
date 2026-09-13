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
    "cancel_requested_at", "error", "exit_code", "progress", "reconciliation"})


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
            if sha256(Path(job["entrypoint"]).read_bytes()) != job["worker_sha256"]:
                return "unknown"
        except OSError:
            return "unknown"
        return worker.probe(identity)

    def _reconcile(self, job):
        if job["state"] in worker.TERMINAL:
            return job
        status = self._identity_status(job)
        if status == "dead":
            return self._save(job, state="interrupted", finished_at=time.time(),
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

    def submit(self, request):
        if not isinstance(request, dict):
            raise ConfigError("request", "オブジェクトを指定してください")
        if set(request) - {"request_id", "kind", "config_id"}:
            raise ConfigError("request", "未対応の要求項目があります")
        rid = identifier(request.get("request_id"), "request_id")
        kind = request.get("kind")
        if kind in ("synopsize", "narrate"):
            raise ConfigError("kind", "生成処理は未接続です", code="unavailable")
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
            run_id = "run-" + secrets.token_hex(16)
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
                return self.public(self._save(job, state="cancelled", cancel_requested_at=time.time(), finished_at=time.time()))
            if job.get("cancel_requested_at") is None:
                # The worker observes this before starting the GA, including when
                # the cancel request races its initial acknowledgement.
                job = self._save(job, state="stopping", cancel_requested_at=time.time())
            return self.public(job)

    def assert_run_idle(self, run_id):
        if not self.root.exists():
            return
        with self._lock():
            for job in self._all():
                if job["run_id"] == run_id and self._reconcile(job)["state"] not in worker.TERMINAL:
                    raise ConfigError("run_id", "実行中の結果は選定できません", code="conflict")
