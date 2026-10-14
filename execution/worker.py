"""Standalone, copied supervisor for persistent jobs (Windows and Linux).

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
import select
import signal
import threading

TERMINAL = frozenset({"succeeded", "partial", "failed", "cancelled", "interrupted"})


def _python_executable() -> str:
    """See execution/provenance.py's python_executable() (duplicated, not
    imported -- this file is stdlib-only by design, launched with -I)."""

    return os.environ.get("WORLDBLOOM_PYTHON") or sys.executable


def kernel():
    if os.name != "nt":
        raise OSError("Windows process containment is required")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenJobObjectW": (wt.HANDLE, [wt.DWORD, wt.BOOL, wt.LPCWSTR]),
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
    if sys.platform == "linux":
        return _linux_identity(pid)
    api = kernel()
    handle = api.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if api.WaitForSingleObject(handle, 0) == 0:
            return None
        try:
            return {"pid": pid, **identity_from_handle(api, handle)}
        except OSError:
            # A process can exit between the first wait and the image query
            # (WinError 31). Confirm exit on the same open handle; never infer
            # death from a query error or reopen a potentially recycled PID.
            if api.WaitForSingleObject(handle, 100) == 0:
                return None
            raise
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
    if sys.platform == "linux":
        try:
            descriptor = os.pidfd_open(identity["pid"])
        except ProcessLookupError:
            return False
        try:
            if process_identity(identity["pid"]) != identity:
                return False
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass  # It exited after verification; the open descriptor remains authoritative.
            return bool(select.select([descriptor], [], [], 10)[0])
        finally:
            os.close(descriptor)
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


class _WindowsProcessTree:
    def __init__(self, *, include_self=False, name=None):
        self.api = kernel()
        self.handle = self.api.CreateJobObjectW(None, name)
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

    def launch(self, argv, cwd, *, capture=False):
        # The fixed script cannot run (or spawn its children) before assignment.
        gate = ("import sys,runpy; p=sys.argv[1]; sys.argv=sys.argv[1:]; "
                "token=sys.stdin.buffer.read(1); "
                "sys.exit(125) if token!=b'1' else None; runpy.run_path(p,run_name='__main__')")
        child = subprocess.Popen([argv[0], "-I", "-B", "-Xutf8", "-c", gate, *argv[3:]],
                                 cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            if not self.api.AssignProcessToJobObject(self.handle, int(child._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            child.stdin.write(b"1")
            child.stdin.flush()
            if not capture:
                child.stdin.close()
            return child
        except BaseException:
            try:
                child.kill()
                child.wait(timeout=10)
            except BaseException as error:
                raise RuntimeError("owned launch cleanup unconfirmed") from error
            for stream in (child.stdin,child.stdout,child.stderr):
                if stream is not None:
                    stream.close()
            child._handle.Close()
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


def _linux_stat(pid):
    # comm can contain spaces and parentheses; fields after its last ')' are fixed.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return fields[0], int(fields[1]), int(fields[19])


def _linux_identity(pid):
    try:
        state, _, created = _linux_stat(pid)
        if state in ("Z", "X"):
            return None
        image = os.readlink(f"/proc/{pid}/exe")
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return {"pid": pid, "created": created, "image": image, "boot_id": boot}
    except (FileNotFoundError, ProcessLookupError):
        return None


def _linux_descendants():
    """Subreaper-owned descendants, including children that called setsid()."""
    parents = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            state, parent, created = _linux_stat(int(entry.name))
            parents[int(entry.name)] = (parent, created)
        except (FileNotFoundError, ProcessLookupError):
            continue
    owned = {os.getpid()}
    while True:
        extra = {pid for pid, (parent, _) in parents.items() if parent in owned} - owned
        if not extra:
            return {pid: parents[pid][1] for pid in owned - {os.getpid()}}
        owned.update(extra)


def _linux_drain():
    """Kill by verified descriptors, adopt/reap orphans, and confirm an empty tree."""
    deadline = time.monotonic() + 10
    while True:
        descendants = _linux_descendants()
        for pid, created in descendants.items():
            try:
                identity = process_identity(pid)
                # /proc enumeration and descriptor opening are separate: never
                # signal a PID that has been recycled since the owned snapshot.
                if identity and identity["created"] == created and _linux_owned(pid, created):
                    terminate_verified(identity)
            except ProcessLookupError:
                pass
        while True:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break
        if not _linux_descendants():
            return
        if time.monotonic() >= deadline:
            raise OSError("owned process termination was not confirmed")
        time.sleep(0.01)


def _linux_owned(pid, created):
    """Recheck the live ancestry after a /proc snapshot, before descriptor signalling."""
    try:
        _, parent, actual_created = _linux_stat(pid)
        if actual_created != created:
            return False
        seen = {pid}
        while parent != os.getpid():
            if parent <= 1 or parent in seen:
                return False
            seen.add(parent)
            _, parent, _ = _linux_stat(parent)
        return True
    except (FileNotFoundError, ProcessLookupError):
        return False


def _linux_guard(registry, input_fd, output_fd):
    """The lifetime pipe closes on owner death, independently of HTTP/reconciliation.

    Establish orphan adoption before accepting any command. Only this guardian
    launches execution, so a crash between launch and acknowledgement cannot
    leave an unregistered child. The record is completed only after reaping.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "child subreaper is unavailable")
    record = {"identity": process_identity(os.getpid()), "stopped": False}
    path = Path(registry) if registry else None
    if path:
        atomic(path, record)
    def reply(value):
        sys.stdout.buffer.write((json.dumps(value) + "\n").encode())
        sys.stdout.buffer.flush()
    child = None
    returncode = None
    try:
        reply({"ready": True})
        for raw in sys.stdin.buffer:
            request = json.loads(raw)
            if request["action"] == "launch":
                if child is not None:
                    raise OSError("process tree already launched")
                argv = request["argv"]
                # -I ignores PYTHONUTF8/PYTHONIOENCODING. Match Windows's
                # gated interpreter and keep Japanese prompts/output portable.
                if argv[1:3] == ["-I", "-B"]:
                    argv = [*argv[:3], "-Xutf8", *argv[3:]]
                child = subprocess.Popen(argv, cwd=request["cwd"],
                    stdin=input_fd if input_fd >= 0 else subprocess.DEVNULL,
                    stdout=output_fd if output_fd >= 0 else subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
                for fd in (input_fd, output_fd):
                    if fd >= 0:
                        os.close(fd)
                input_fd = output_fd = -1
                reply({"pid": child.pid})
            elif request["action"] == "poll":
                returncode = child.poll() if returncode is None else returncode
                reply({"returncode": returncode})
            elif request["action"] == "finish":
                returncode = child.poll() if returncode is None else returncode
                _linux_drain()
                # waitpid in drain may have reaped the direct child too.
                if returncode is None:
                    returncode = -signal.SIGKILL
                if path:
                    atomic(path, {**record, "stopped": True})
                reply({"returncode": returncode})
                return 0
            else:
                raise OSError("unknown guardian command")
    finally:
        _linux_drain()
        if path:
            atomic(path, {**record, "stopped": True})
        for fd in (input_fd, output_fd):
            if fd >= 0:
                os.close(fd)
    return 0


class _LinuxChild:
    def __init__(self, tree, pid, stdin=None, stdout=None):
        self.tree, self.pid = tree, pid
        self.stdin, self.stdout = stdin, stdout
        self.returncode = None

    def poll(self):
        if self.returncode is None:
            self.returncode = self.tree._request({"action": "poll"})["returncode"]
        return self.returncode

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("WorldBloom execution", timeout)
            time.sleep(0.02)
        return self.returncode


class _LinuxProcessTree:
    def __init__(self, *, registry=None):
        # Probe capabilities before admission; never downgrade to unsafe PID kills.
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise OSError("Linux pidfd support is required")
        descriptor = os.pidfd_open(os.getpid())
        os.close(descriptor)
        mode = ctypes.c_int()
        if ctypes.CDLL(None, use_errno=True).prctl(37, ctypes.byref(mode), 0, 0, 0):
            raise OSError("Linux child subreaper support is required")
        self.registry, self.guard, self.child = registry, None, None
        self._mutex = threading.Lock()

    def _request(self, value=None):
        with self._mutex:
            if value is not None:
                self.guard.stdin.write((json.dumps(value) + "\n").encode())
                self.guard.stdin.flush()
            if not select.select([self.guard.stdout], [], [], 12)[0]:
                raise OSError("process guardian did not acknowledge")
            raw = self.guard.stdout.readline()
            if not raw:
                raise OSError("process guardian disappeared")
            return json.loads(raw)

    def launch(self, argv, cwd, *, capture=False):
        if self.guard is not None:
            raise OSError("process tree already launched")
        inputs, outputs = os.pipe() if capture else (-1, -1), os.pipe() if capture else (-1, -1)
        record = ""
        if self.registry:
            Path(self.registry).mkdir(parents=True, exist_ok=True)
            record = str(Path(self.registry) / (uuid.uuid4().hex + ".json"))
        try:
            self.guard = subprocess.Popen([_python_executable(), "-I", "-B", str(Path(__file__).resolve()),
                "--linux-guard", record, str(inputs[0]), str(outputs[1])],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                close_fds=True, pass_fds=tuple(fd for fd in (inputs[0], outputs[1]) if fd >= 0),
                start_new_session=True)
            for fd in (inputs[0], outputs[1]):
                if fd >= 0:
                    os.close(fd)
            inputs = (-1, inputs[1]); outputs = (outputs[0], -1)
            if not self._request().get("ready"):
                raise OSError("process guardian did not establish containment")
            launched = self._request({"action": "launch", "argv": list(argv), "cwd": str(cwd)})
            self.child = _LinuxChild(self, launched["pid"],
                os.fdopen(inputs[1], "wb") if capture else None,
                os.fdopen(outputs[0], "rb") if capture else None)
            return self.child
        except BaseException:
            if self.guard is not None:
                self.guard.stdin.close()  # EOF requests guardian cleanup, never abandon it.
                self.guard.wait(timeout=12)
                self.guard.stdout.close()
            for fd in (*inputs, *outputs):
                if fd >= 0:
                    os.close(fd)
            raise

    def terminate(self):
        self.finish()

    def finish(self):
        if self.guard is None:
            return
        response = self._request({"action": "finish"})
        if self.child is not None:
            self.child.returncode = response["returncode"]
        self.guard.stdin.close()
        code = self.guard.wait(timeout=12)
        self.guard.stdout.close()
        self.guard = None
        if code:
            raise OSError("owned process termination was not confirmed")

    def close(self):
        self.finish()


def ProcessTree(*, include_self=False, name=None, registry=None):
    if os.name == "nt":
        return _WindowsProcessTree(include_self=include_self, name=name)
    if sys.platform == "linux":
        return _LinuxProcessTree(registry=registry)
    raise OSError("process containment requires Windows or Linux")


def output_tree_stopped(job):
    """Query the named lifetime group, including descendants after owner death."""
    if sys.platform == "linux":
        try:
            root = Path(job["entrypoint"]).parent / "process-trees"
            for path in root.glob("*.json"):
                record = read(path)
                if not record["stopped"] or probe(record["identity"]) != "dead":
                    return False
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False
    if not job.get("output_id"):
        return True
    api = kernel()
    handle = api.OpenJobObjectW(4, False, "Local\\WorldBloom-" + job["nonce"])
    if not handle:
        return ctypes.get_last_error() == 2
    try:
        accounting = BasicAccounting()
        if not api.QueryInformationJobObject(handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            return False
        return accounting.active == 0
    finally:
        api.CloseHandle(handle)


def finish_output(args, job, *, stopped=None, limit_hit=False):
    # Called only after the owned execution tree is drained. This path performs
    # receipt recovery only, and has no generation or credential resolution call.
    output_root = Path(args.control) / "outputs" / job["output_id"]
    if not output_root.is_dir():
        return {}
    # Recovery consumes the same frozen code as generation, even if the source
    # checkout was edited while the job ran.
    sys.path.insert(0, str(output_root / "runtime"))
    from execution.output_store import OutputStore
    from execution.generation import aggregate
    outputs = OutputStore(args.control)
    payload = outputs.recover(job["output_id"], owner_stopped=True,
                              stopped=stopped or ("limit" if limit_hit else "interrupted"))
    summary = aggregate(payload["entries"], stopped=stopped)
    return {**summary, "progress": {"completed": len(payload["entries"]),
        "total": len(payload["entries"]), "counts": summary["counts"]}}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def replace_job_snapshot(source, target):
    """Publish job.json while shared-delete readers retain the old revision."""
    if os.name != "nt":
        os.replace(source, target)
        return
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    create = api.CreateFileW
    create.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                      wt.DWORD, wt.DWORD, wt.HANDLE]
    create.restype = wt.HANDLE
    rename = api.SetFileInformationByHandle
    rename.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    rename.restype = wt.BOOL
    close = api.CloseHandle
    close.argtypes = [wt.HANDLE]
    close.restype = wt.BOOL

    class RenameInfo(ctypes.Structure):
        _fields_ = [("flags", wt.DWORD), ("root", wt.HANDLE),
                    ("length", wt.DWORD), ("name", wt.WCHAR * 1)]

    if Path(source).absolute().parent != Path(target).absolute().parent:
        raise ValueError("Job snapshots must be replaced within the same directory")
    name = str(Path(target).absolute()).encode("utf-16-le")
    size = max(ctypes.sizeof(RenameInfo), RenameInfo.name.offset + len(name) + 2)
    buffer = ctypes.create_string_buffer(size)
    info = RenameInfo.from_buffer(buffer)
    info.flags = 3  # REPLACE_IF_EXISTS | POSIX_SEMANTICS; preserve old open handles.
    info.root = None
    info.length = len(name)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, name, len(name))
    handle = create(str(source), 0x10000, 7, None, 3, 0x80, None)  # DELETE, shared access.
    if handle == wt.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not rename(handle, 22, buffer, size):  # FileRenameInfoEx
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


def atomic(path, value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        if path.name == "job.json":
            replace_job_snapshot(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def trace_lock(folder, operation, stage, **values):
    """Opt-in CI diagnostics; omit paths, requests, settings and credentials."""
    evidence = os.environ.get("WB_REGRESSION_EVIDENCE_DIR")
    if not evidence or os.environ.get("WB_REGRESSION_DIAGNOSTICS") != "1":
        return
    try:
        root = Path(evidence) / "storage-locks"
        root.mkdir(parents=True, exist_ok=True)
        lock_id = hashlib.sha256(os.path.normcase(os.path.realpath(folder)).encode()).hexdigest()
        row = dict(time=time.time(), monotonic=time.monotonic(), pid=os.getpid(),
                   thread=threading.get_ident(), lock_id=lock_id,
                   operation=operation, stage=stage, **values)
        with (root / (str(os.getpid()) + ".jsonl")).open("a", encoding="utf-8") as log:
            log.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        # Diagnostic storage must not change the job ledger's outcome.
        pass


@contextmanager
def lock(folder, *, operation="worker.lock"):
    started = time.monotonic()
    if os.name == "nt":
        import msvcrt
    else:
        import fcntl
    with (folder / ".write.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0"); stream.flush()
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                trace_lock(folder, operation, "contended", errno=error.errno,
                           winerror=getattr(error, "winerror", None))
                time.sleep(0.05)
        acquired = time.monotonic()
        trace_lock(folder, operation, "acquired", wait_seconds=acquired-started)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
            trace_lock(folder, operation, "released", held_seconds=time.monotonic()-acquired)


def record_event(folder, job):
    event = {k: job[k] for k in ("schema_version", "job_id", "revision", "state", "phase", "updated_at")}
    with (folder / "events.jsonl").open("ab") as stream:
        stream.write((json.dumps(event, sort_keys=True) + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())


def change(jobs, folder, nonce, **updates):
    with lock(jobs, operation="worker.change"):
        started = time.monotonic()
        job = read(folder / "job.json")
        trace_lock(jobs, "worker.change", "read", seconds=time.monotonic()-started)
        if job["nonce"] != nonce or job["state"] in TERMINAL:
            return job
        job.update(updates, updated_at=time.time(), revision=job["revision"] + 1)
        started = time.monotonic()
        atomic(folder / "job.json", job)
        trace_lock(jobs, "worker.change", "atomic", seconds=time.monotonic()-started)
        started = time.monotonic()
        record_event(folder, job)
        trace_lock(jobs, "worker.change", "event", seconds=time.monotonic()-started)
        return job


def read_job(jobs, folder):
    with lock(jobs, operation="worker.read_job"):
        return read(folder / "job.json")


def prepare(args, job, folder):
    """Run potentially blocking imports/copy/git/verification inside the owned child."""
    try:
        sys.path.insert(0, args.repo)
        from execution.configs import ConfigStore
        configs = ConfigStore(args.repo, args.control, args.runs)
        request = read(folder / "request.json")
        if request["kind"] in ("synopsize", "narrate"):
            from execution.output_requests import prepare as prepare_output
            raw = (folder / "output-plan.json").read_bytes()
            if hashlib.sha256(raw).hexdigest() != job["output_plan_sha256"]:
                raise ValueError("output plan changed")
            manifest = prepare_output(configs, job, json.loads(raw))
        elif request["kind"] == "world_patch":
            from execution.world_patch_job import prepare as prepare_patch
            manifest = prepare_patch(configs, job, request)
        else:
            manifest = configs.prepare_run(request["config_id"], run_id=job["run_id"], job_id=args.job,
                                            processes=job.get("processes"))
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
        if now - last_heartbeat >= 5.0:
            change(jobs, folder, nonce, heartbeat=now)
            last_heartbeat = now
        time.sleep(0.25)
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
        lifetime_tree = ProcessTree(include_self=True,
            name="Local\\WorldBloom-" + args.nonce if job.get("output_id") else None)
        tree = ProcessTree(registry=folder / "process-trees")
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
        preparation_argv = [_python_executable(), "-I", "-B", str(entry), "--prepare"]
        for name in ("control", "runs", "repo", "job", "nonce"):
            preparation_argv.extend(["--" + name, getattr(args, name)])
        child = tree.launch(preparation_argv, folder)
        change(jobs, folder, args.nonce, preparation_identity=process_identity(child.pid))
        job, limit_hit = watch(child, tree, jobs, folder, args.nonce, deadline)
        tree = None
        cancelled = job.get("cancel_requested_at") is not None
        if cancelled or limit_hit or child.returncode:
            state = "cancelled" if cancelled else "failed"
            summary = finish_output(args, job, stopped="cancelled" if cancelled else "interrupted") if job.get("output_id") else {}
            summary.pop("state", None)
            change(jobs, folder, args.nonce, state=state, exit_code=child.returncode, **summary,
                   error=None if cancelled else {"code":"wall_timeout" if limit_hit else "preparation_failed"},
                   finished_at=time.time(), heartbeat=time.time())
            return 0 if cancelled else 1
        manifest = read(folder / "prepared.json")
        tree = ProcessTree(registry=folder / "process-trees")
        if job.get("output_id"):
            run_root = Path(args.control) / "outputs" / job["output_id"]
            launch_argv = manifest["argv"]
            handler = "output_worker"
            phase = "generating"
        elif job.get("kind") == "world_patch":
            # scripts/world_patch.py takes absolute --experiment/--project
            # paths and writes only scratch temp dirs, never cwd-relative
            # paths -- the job's own folder is a safe, disposable cwd.
            run_root = folder / "scratch"
            run_root.mkdir(exist_ok=True)
            launch_argv = manifest["argv"]
            handler = "world_patch_cli"
            phase = manifest["phase"]
        else:
            run_root = Path(args.runs) / job["run_id"]
            adapter = run_root / "runtime/execution/evolution_worker.py"
            launch_argv = ([manifest["argv"][0], "-I", "-B", str(adapter), "--run", str(run_root),
                            "--control", args.control, "--job", args.job]
                           if adapter.is_file() else manifest["argv"])
            handler = "evolution_worker" if adapter.is_file() else "legacy_evolve_cli"
            phase = "evaluating"
        atomic(folder / "launch.json", {"schema_version":1, "job_id":args.job,
               "run_id":job["run_id"], "argv":launch_argv,
               "runtime_manifest_sha256":manifest["runtime_manifest_sha256"], "handler":handler})
        child = tree.launch(launch_argv, run_root)
        change(jobs, folder, args.nonce, phase=phase, child_identity=process_identity(child.pid))
        job, limit_hit = watch(child, tree, jobs, folder, args.nonce, deadline)
        tree = None
        cancelled = job.get("cancel_requested_at") is not None
        state = "cancelled" if cancelled else "failed" if limit_hit or child.returncode else "succeeded"
        summary = {}
        if job.get("output_id"):
            summary = finish_output(args, job, stopped="cancelled" if cancelled else None, limit_hit=limit_hit)
            if not summary:
                raise ValueError("output result missing")
            # The entry set decides the state (contract §14); a limit that left every entry ok is not an error.
            state = summary.pop("state")
        change(jobs, folder, args.nonce, state=state, exit_code=child.returncode, **summary,
               error={"code":"wall_timeout"} if limit_hit and not cancelled and state != "succeeded" else ({"code":"process_failed"} if state == "failed" else None),
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
            latest = read_job(jobs, folder)
            summary = finish_output(args, latest, stopped="cancelled" if latest.get("cancel_requested_at") else None) if latest.get("output_id") else {}
            final_state = summary.pop("state", "failed")
            if final_state == "succeeded":
                final_state = "failed"
            change(jobs, folder, args.nonce, state=final_state, **summary,
                error={"code":"worker_failed", "exception_type":type(error).__name__}, finished_at=time.time())
        except BaseException:
            pass
        return 1
    finally:
        if tree is not None:
            tree.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--linux-guard":
        raise SystemExit(_linux_guard(sys.argv[2], int(sys.argv[3]), int(sys.argv[4])))
    raise SystemExit(main())
