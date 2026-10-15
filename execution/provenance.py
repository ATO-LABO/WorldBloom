"""Atomic storage and verified source snapshots. No job is started here."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import uuid

import yaml


def python_executable() -> str:
    """The interpreter to launch worker/generation subprocesses with.
    Normally sys.executable IS that interpreter (the CLI's own python.exe),
    but a frozen desktop build's sys.executable is the packaged GUI exe
    itself, which doesn't understand -I -B <script> <args>. WORLDBLOOM_PYTHON
    lets such a launcher point every subprocess call at a real interpreter
    instead. (execution/worker.py keeps its own copy of this function: it is
    stdlib-only by design, launched with -I, and cannot import this module.)"""

    return os.environ.get("WORLDBLOOM_PYTHON") or sys.executable


class ConfigError(ValueError):
    def __init__(self, field: str, message: str, *, code: str = "invalid_config"):
        super().__init__(message)
        self.code = code
        self.field_errors = {field: message}

    def as_dict(self):
        return {"code": self.code, "message": str(self),
                "field_errors": dict(self.field_errors), "retryable": False}


def identifier(value, field="id"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value):
        raise ConfigError(field, "識別子の形式が正しくありません")
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def is_link(path: Path) -> bool:
    """Include Windows junctions on Python 3.11, before Path.is_junction()."""
    if path.is_symlink():
        return True
    try:
        tag = getattr(path.lstat(), "st_reparse_tag", None)
    except FileNotFoundError:
        return False
    return tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def _check_root(root_str: str) -> Path:
    root = Path(root_str)
    for part in (root, *root.parents):
        if is_link(part):
            raise ConfigError("path", "リンクを経由した保存・読取はできません")
    return root.resolve()


# Root/ancestor checks remain cached on the normal success path. A link planted
# there after the first call can still go undetected on that path. A failed
# containment comparison below always rechecks them without this cache.
@lru_cache(maxsize=64)
def _checked_root(root_str: str) -> Path:
    return _check_root(root_str)


def contained(root: Path, relative: str) -> Path:
    root = Path(root).absolute()
    rel = Path(relative)
    if rel.is_absolute() or rel.drive or ".." in rel.parts or "\\" in relative:
        raise ConfigError("path", "保存範囲外のパスです")
    result = root / rel
    for attempt in range(2):
        resolved_root = (_checked_root(str(root)) if attempt == 0
                         else _check_root(str(root)))
        for part in (result, *result.parents):
            if part == root:
                break
            if is_link(part):
                raise ConfigError("path", "リンクを経由した保存・読取はできません")
        if result.resolve().is_relative_to(resolved_root):
            return result
        # On Windows, creating a missing parent during realpath can leave the
        # child in extended form while the earlier root used ordinary form.
        # Retry full validation once; never strip prefixes or accept a link.
    raise ConfigError("path", "保存範囲外のパスです")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@contextmanager
def open_atomic_read(path):
    """Read one published inode; on Windows allow its atomic replacement."""
    if os.name != "nt":
        with Path(path).open("r", encoding="utf-8") as stream:
            yield stream
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    create = api.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = api.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_READ, SHARE_READ|SHARE_WRITE|SHARE_DELETE, OPEN_EXISTING.
    # No DELETE_ON_CLOSE: the published file remains owned by the writer.
    handle = create(str(Path(path)), 0x80000000, 7, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        yield stream


def read_atomic_json(path):
    with open_atomic_read(path) as stream:
        return json.load(stream)


def write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    try:
        write_bytes(temporary, canonical(value))
        # A concurrent Windows reader can briefly deny replacement. Retain the
        # complete previous file while retrying; persistent denial still fails.
        for attempt in range(6):
            try:
                if path.name == "job.json":
                    from execution.worker import replace_job_snapshot
                    replace_job_snapshot(temporary, path)
                else:
                    os.replace(temporary, path)
                break
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


_mutex = threading.Lock()
_locks = {}


@contextmanager
def directory_lock(root: Path, *, operation="directory_lock"):
    from execution.worker import trace_lock
    started = time.monotonic()
    root = Path(root).absolute()
    contained(root, ".write.lock")
    root.mkdir(parents=True, exist_ok=True)
    with _mutex:
        lock = _locks.setdefault(str(root.resolve()), threading.RLock())
    with lock:
        trace_lock(root, operation, "thread_acquired", wait_seconds=time.monotonic()-started)
        with (root / ".write.lock").open("a+b") as handle:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                trace_lock(root, operation, "contended", errno=error.errno,
                           winerror=getattr(error, "winerror", None))
                raise ConfigError("storage", "別の保存処理が実行中です", code="conflict") from error
            acquired = time.monotonic()
            trace_lock(root, operation, "acquired", wait_seconds=acquired-started)
            try:
                # Windows permits locking beyond EOF. Acquire before the
                # first write: a competing initial writer may already hold
                # byte zero while our earlier size observation was empty.
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                trace_lock(root, operation, "released", held_seconds=time.monotonic()-acquired)


def materialize(root: Path, blobs: dict[str, bytes]):
    for relative, data in sorted(blobs.items()):
        write_bytes(contained(root, relative), data)


def verify_files(root: Path, records):
    expected = {r["path"] for r in records}
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != expected or len(expected) != len(records):
        raise ConfigError("snapshot", "保存済みファイル集合が変更されています", code="snapshot_changed")
    for record in records:
        data = contained(root, record["path"]).read_bytes()
        if len(data) != record["bytes"] or sha256(data) != record["sha256"]:
            raise ConfigError("snapshot", "保存済みファイルの整合性が失われています",
                              code="snapshot_changed")


def code_snapshot(repo: Path):
    """Capture the local Python closure, never settings, credentials or logs."""
    blobs = {}
    stamps = {}
    for package in ("engine", "gapengine", "scripts", "execution"):
        folder = contained(repo, package)
        if not folder.is_dir():
            raise ConfigError("runtime", f"実行コードがありません: {package}")
        for path in sorted(folder.rglob("*.py")):
            relative = path.relative_to(repo).as_posix()
            target = contained(repo, relative)
            stat = target.stat()
            stamps[relative] = (stat.st_size, stat.st_mtime_ns)
            blobs[relative] = target.read_bytes()
    target = contained(repo, "requirements.txt")
    stat = target.stat()
    stamps["requirements.txt"] = (stat.st_size, stat.st_mtime_ns)
    blobs["requirements.txt"] = target.read_bytes()
    # Fail if files changed during the capture rather than claiming a coherent version.
    for path in blobs:
        st = contained(repo, path).stat()
        if (st.st_size, st.st_mtime_ns) != stamps[path]:
            raise ConfigError("runtime", "固定中にコードが変更されました", code="conflict")
    def git(*args):
        # A packaged distribution's repo folder has no .git and may run on a
        # machine without git installed at all (FileNotFoundError) -- head/
        # dirty are provenance nice-to-haves, not required for GA execution.
        try:
            result = subprocess.run(["git", "-C", str(repo), *args],
                                    capture_output=True, text=True, encoding="utf-8",
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except FileNotFoundError:
            return None
        return result.stdout.strip() if result.returncode == 0 else None
    head = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain")
    records = [{"path": p, "sha256": sha256(b), "bytes": len(b),
                "source_path": str(repo / p)} for p, b in sorted(blobs.items())]
    return blobs, {"schema_version": 1, "head": head,
                   "dirty": dirty != "" if dirty is not None else None,
                   "files": records, "python": sys.version,
                   "python_executable": python_executable(), "pyyaml": yaml.__version__}


def publish_directory(staging: Path, destination: Path):
    # Caller holds the destination-parent OS lock. Never replace an existing version.
    if destination.exists():
        raise ConfigError("id", "同じIDの保存先が既にあります", code="conflict")
    os.rename(staging, destination)
