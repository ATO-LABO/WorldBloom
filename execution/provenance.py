"""Atomic storage and verified source snapshots. No job is started here."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid

import yaml


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


def contained(root: Path, relative: str) -> Path:
    root = Path(root).absolute()
    rel = Path(relative)
    if rel.is_absolute() or rel.drive or ".." in rel.parts or "\\" in relative:
        raise ConfigError("path", "保存範囲外のパスです")
    result = root / rel
    # Reject symlinks/junctions, including ancestors of configured roots.
    for part in (result, *result.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ConfigError("path", "リンクを経由した保存・読取はできません")
    if not result.resolve().is_relative_to(root.resolve()):
        raise ConfigError("path", "保存範囲外のパスです")
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    write_bytes(temporary, canonical(value))
    os.replace(temporary, path)


_mutex = threading.Lock()
_locks = {}


@contextmanager
def directory_lock(root: Path):
    root = Path(root).absolute()
    contained(root, ".write.lock")
    root.mkdir(parents=True, exist_ok=True)
    with _mutex:
        lock = _locks.setdefault(str(root.resolve()), threading.RLock())
    with lock:
        with (root / ".write.lock").open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ConfigError("storage", "別の保存処理が実行中です", code="conflict") from error
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


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
    for package in ("engine", "gapengine", "scripts", "execution"):
        folder = contained(repo, package)
        if not folder.is_dir():
            raise ConfigError("runtime", f"実行コードがありません: {package}")
        for path in sorted(folder.rglob("*.py")):
            relative = path.relative_to(repo).as_posix()
            blobs[relative] = contained(repo, relative).read_bytes()
    blobs["requirements.txt"] = contained(repo, "requirements.txt").read_bytes()
    # Fail if files changed during the capture rather than claiming a coherent version.
    for path, content in blobs.items():
        if contained(repo, path).read_bytes() != content:
            raise ConfigError("runtime", "固定中にコードが変更されました", code="conflict")
    def git(*args):
        result = subprocess.run(["git", "-C", str(repo), *args],
                                capture_output=True, text=True, encoding="utf-8",
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return result.stdout.strip() if result.returncode == 0 else None
    head = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain")
    records = [{"path": p, "sha256": sha256(b), "bytes": len(b),
                "source_path": str(repo / p)} for p, b in sorted(blobs.items())]
    return blobs, {"schema_version": 1, "head": head,
                   "dirty": dirty != "" if dirty is not None else None,
                   "files": records, "python": sys.version,
                   "python_executable": sys.executable, "pyyaml": yaml.__version__}


def publish_directory(staging: Path, destination: Path):
    # Caller holds the destination-parent OS lock. Never replace an existing version.
    if destination.exists():
        raise ConfigError("id", "同じIDの保存先が既にあります", code="conflict")
    os.rename(staging, destination)
