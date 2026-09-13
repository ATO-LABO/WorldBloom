"""Standalone, copied supervisor for persistent jobs (Windows).

Only this file is executed before the run snapshot exists. The GA entry point
waits on a gate until it belongs to the supervisor's kill-on-close Job Object.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

TERMINAL = frozenset({"succeeded", "partial", "failed", "cancelled", "interrupted"})


def kernel():
    if os.name != "nt":
        raise OSError("Windows process containment is required")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenProcess": (wt.HANDLE, [wt.DWORD, wt.BOOL, wt.DWORD]),
        "CloseHandle": (wt.BOOL, [wt.HANDLE]),
        "GetProcessTimes": (wt.BOOL, [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4),
        "QueryFullProcessImageNameW": (wt.BOOL, [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]),
        "WaitForSingleObject": (wt.DWORD, [wt.HANDLE, wt.DWORD]),
        "TerminateProcess": (wt.BOOL, [wt.HANDLE, wt.UINT]),
        "CreateJobObjectW": (wt.HANDLE, [ctypes.c_void_p, wt.LPCWSTR]),
        "SetInformationJobObject": (wt.BOOL, [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]),
        "AssignProcessToJobObject": (wt.BOOL, [wt.HANDLE, wt.HANDLE]),
        "TerminateJobObject": (wt.BOOL, [wt.HANDLE, wt.UINT]),
        "QueryInformationJobObject": (wt.BOOL, [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(api, name)
        fn.restype, fn.argtypes = result, args
    return api


def identity_from_handle(api, handle):
    times = [wt.FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    size = wt.DWORD(32768)
    name = ctypes.create_unicode_buffer(size.value)
    if not api.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"created": (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime,
            "image": os.path.normcase(os.path.abspath(name.value))}


def process_identity(pid):
    api = kernel()
    handle = api.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if api.WaitForSingleObject(handle, 0) == 0:
            return None
        return {"pid": pid, **identity_from_handle(api, handle)}
    finally:
        api.CloseHandle(handle)


def probe(identity):
    try:
        actual = process_identity(identity["pid"])
        return "dead" if actual is None or actual != identity else "alive"
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


def terminate_verified(identity):
    """Keep the verified handle open through termination; never kill by PID."""
    api = kernel()
    handle = api.OpenProcess(0x1000 | 0x100000 | 1, False, identity["pid"])
    if not handle:
        return False
    try:
        if identity_from_handle(api, handle) != {k: identity[k] for k in ("created", "image")}:
            return False
        if api.WaitForSingleObject(handle, 0) != 0:
            if not api.TerminateProcess(handle, 1):
                return False
        return api.WaitForSingleObject(handle, 10000) == 0
    finally:
        api.CloseHandle(handle)


class BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wt.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                ("active", wt.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", wt.DWORD), ("scheduling", wt.DWORD)]


class IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IOCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class BasicAccounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in
                ("user_time", "kernel_time", "period_user_time", "period_kernel_time")] + [
                (name, wt.DWORD) for name in ("page_faults", "total", "active", "terminated")]


class ProcessTree:
    def __init__(self, *, include_self=False):
        self.api = kernel()
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

        if include_self and not self.api.AssignProcessToJobObject(self.handle, wt.HANDLE(-1)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def launch(self, argv, cwd):
        # The fixed script cannot run (or spawn its children) before assignment.
        gate = ("import sys,runpy; p=sys.argv[1]; sys.argv=sys.argv[1:]; "
                "token=sys.stdin.buffer.read(1); "
                "sys.exit(125) if token!=b'1' else None; runpy.run_path(p,run_name='__main__')")
        child = subprocess.Popen([argv[0], "-I", "-B", "-c", gate, *argv[3:]],
                                 cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            if not self.api.AssignProcessToJobObject(self.handle, int(child._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            child.stdin.write(b"1")
            child.stdin.flush()
            child.stdin.close()
            return child
        except BaseException:
            child.kill()
            child.wait(timeout=10)
            raise

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def finish(self):
        """Confirm all owned processes exited before publishing a terminal state."""
        self.terminate()
        deadline = time.monotonic() + 10
        while True:
            accounting = BasicAccounting()
            if not self.api.QueryInformationJobObject(
                    self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if accounting.active == 0:
                self.close()
                return
            if time.monotonic() >= deadline:
                raise OSError("owned process termination was not confirmed")
            time.sleep(0.01)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic(path, value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def lock(folder):
    import msvcrt
    with (folder / ".write.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0"); stream.flush()
        while True:
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def record_event(folder, job):
    event = {k: job[k] for k in ("schema_version", "job_id", "revision", "state", "phase", "updated_at")}
    with (folder / "events.jsonl").open("ab") as stream:
        stream.write((json.dumps(event, sort_keys=True) + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())


def change(jobs, folder, nonce, **updates):
    with lock(jobs):
        job = read(folder / "job.json")
        if job["nonce"] != nonce or job["state"] in TERMINAL:
            return job
        job.update(updates, updated_at=time.time(), revision=job["revision"] + 1)
        atomic(folder / "job.json", job)
        record_event(folder, job)
        return job


def read_job(jobs, folder):
    with lock(jobs):
        return read(folder / "job.json")


def prepare(args, job, folder):
    """Run potentially blocking imports/copy/git/verification inside the owned child."""
    try:
        sys.path.insert(0, args.repo)
        from execution.configs import ConfigStore
        configs = ConfigStore(args.repo, args.control, args.runs)
        request = read(folder / "request.json")
        if request["kind"] != "evolve":
            raise ValueError("handler unavailable")
        manifest = configs.prepare_run(request["config_id"], run_id=job["run_id"], job_id=args.job)
        configs.verify_run(job["run_id"])
        atomic(folder / "prepared.json", manifest)
        return 0
    except BaseException:
        # Only the supervisor owns job transitions, after it has drained this tree.
        return 1


def watch(child, tree, jobs, folder, nonce, deadline):
    """The same independent watchdog covers preparation and GA execution."""
    last_heartbeat = 0
    while child.poll() is None:
        job = read_job(jobs, folder)
        now = time.time()
        cancel = job.get("cancel_requested_at")
        if (now >= deadline or job["state"] in TERMINAL
                or (cancel is not None and now - cancel >= job["cancel_grace_seconds"])):
            break
        if now - last_heartbeat >= 0.5:
            change(jobs, folder, nonce, heartbeat=now)
            last_heartbeat = now
        time.sleep(0.1)
    tree.finish()
    child.wait(timeout=10)
    return read_job(jobs, folder), time.time() >= deadline


def main(argv=None):
    parser = argparse.ArgumentParser()
    for name in ("control", "runs", "repo", "job", "nonce"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args(argv)
    jobs = Path(args.control) / "jobs"
    folder = jobs / args.job
    tree = None
    child = None
    try:
        job = read_job(jobs, folder)
        entry = Path(__file__).resolve()
        if (job["nonce"] != args.nonce or job["entrypoint"] != str(entry)
                or job["worker_sha256"] != hashlib.sha256(entry.read_bytes()).hexdigest()
                or hashlib.sha256((folder / "request.json").read_bytes()).hexdigest() != job["request_hash"]):
            return 2
        if job["state"] in TERMINAL:
            return 0
        if args.prepare:
            return prepare(args, job, folder)
        # Keep this outer handle until process exit. It contains the supervisor
        # and preparation subprocesses (including git), not just the GA child.
        # The inner tree can be closed before the terminal state is persisted.
        lifetime_tree = ProcessTree(include_self=True)
        tree = ProcessTree()
        identity = process_identity(os.getpid())
        receipt = {"nonce": args.nonce, "identity": identity, "entrypoint": str(entry),
                   "worker_sha256": job["worker_sha256"]}
        job = change(jobs, folder, args.nonce, state="running", phase="preparing",
                     receipt=receipt, started_at=time.time(), heartbeat=time.time())
        if job["state"] in TERMINAL:
            return 0
        deadline = job["created_at"] + job["wall_seconds"]
        if job.get("cancel_requested_at") is not None:
            change(jobs, folder, args.nonce, state="cancelled", finished_at=time.time())
            return 0
        if time.time() >= deadline:
            change(jobs, folder, args.nonce, state="failed", error={"code":"wall_timeout"}, finished_at=time.time())
            return 1
        preparation_argv = [sys.executable, "-I", "-B", str(entry), "--prepare"]
        for name in ("control", "runs", "repo", "job", "nonce"):
            preparation_argv.extend(["--" + name, getattr(args, name)])
        child = tree.launch(preparation_argv, folder)
        change(jobs, folder, args.nonce, preparation_identity=process_identity(child.pid))
        job, limit_hit = watch(child, tree, jobs, folder, args.nonce, deadline)
        tree = None
        cancelled = job.get("cancel_requested_at") is not None
        if cancelled or limit_hit or child.returncode:
            state = "cancelled" if cancelled else "failed"
            change(jobs, folder, args.nonce, state=state, exit_code=child.returncode,
                   error=None if cancelled else {"code":"wall_timeout" if limit_hit else "preparation_failed"},
                   finished_at=time.time(), heartbeat=time.time())
            return 0 if cancelled else 1
        manifest = read(folder / "prepared.json")
        tree = ProcessTree()
        run_root = Path(args.runs) / job["run_id"]
        adapter = run_root / "runtime/execution/evolution_worker.py"
        launch_argv = ([manifest["argv"][0], "-I", "-B", str(adapter), "--run", str(run_root),
                        "--control", args.control, "--job", args.job]
                       if adapter.is_file() else manifest["argv"])
        atomic(folder / "launch.json", {"schema_version":1, "job_id":args.job,
               "run_id":job["run_id"], "argv":launch_argv,
               "runtime_manifest_sha256":manifest["runtime_manifest_sha256"],
               "handler":"evolution_worker" if adapter.is_file() else "legacy_evolve_cli"})
        child = tree.launch(launch_argv, run_root)
        change(jobs, folder, args.nonce, phase="evaluating", child_identity=process_identity(child.pid))
        job, limit_hit = watch(child, tree, jobs, folder, args.nonce, deadline)
        tree = None
        cancelled = job.get("cancel_requested_at") is not None
        state = "cancelled" if cancelled else "failed" if limit_hit or child.returncode else "succeeded"
        change(jobs, folder, args.nonce, state=state, exit_code=child.returncode,
               error={"code":"wall_timeout" if limit_hit else "process_failed"} if state == "failed" else None,
               finished_at=time.time(), heartbeat=time.time())
        return 0
    except BaseException as error:
        if tree is not None:
            tree.finish(); tree = None
        if child is not None:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        try:
            change(jobs, folder, args.nonce, state="failed", error={"code":"worker_failed", "exception_type":type(error).__name__}, finished_at=time.time())
        except BaseException:
            pass
        return 1
    finally:
        if tree is not None:
            tree.close()


if __name__ == "__main__":
    raise SystemExit(main())
