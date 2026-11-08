"""Handle-based calendar input acquisition. Internal backend contract.

read(relative, optional=False, budget=None) returns immutable InputBytes (or
None only for a missing optional leaf). exact_tree(files, version) validates
names/types/identities relative to the same root capability. Every handle is
owned by its context and closed on success and exception. No pathname fallback.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat

MANIFEST = "execution-version.json"
_open = os.open  # native operation seam for deterministic race tests

@dataclass(frozen=True)
class InputBytes:
    data: bytes
    identity: tuple[int, int]

    def manifest(self):
        return {"size_bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}


def _close_many(descriptors):
    """Consume owned fd numbers before close; attempt each once, even on errors."""
    errors = []
    while descriptors:
        descriptor = descriptors.pop()
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as error:
                errors.append(error)
    if errors:
        raise next((e for e in errors if not isinstance(e, Exception)), errors[0])


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 4096 or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value) or PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).drive or any(p in ("", ".", "..") for p in value.split("/"))):
        raise ValueError("An explicit project-relative path without parent/drive aliases is required")
    return value



def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class PosixProjectDirectory:
    """POSIX capability owner. Never fall back to a checked pathname open."""
    def __init__(self, path, *, file_limit):
        self.file_limit = file_limit
        self.fd = None
        if (os.name != "posix" or not all(hasattr(os, flag) for flag in
                ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK"))
                or os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd):
            raise ValueError("Secure calendar filesystem backend is unsupported on this platform; no pathname fallback")
        self.path = Path(path).absolute()
        if self.path.anchor != "/":
            raise ValueError("Calendar root must use an absolute local path")
        ancestors = [_open("/", self._directory_flags())]
        try:
            # '..' selects the retained logical parent, never the current
            # filesystem parent of a directory that another writer may move.
            # A preceding link still has to pass the no-follow open first.
            for component in self.path.parts[1:]:
                if component == "..":
                    if len(ancestors) > 1:
                        os.close(ancestors.pop())
                else:
                    ancestors.append(_open(component, self._directory_flags(), dir_fd=ancestors[-1]))
            selected = ancestors.pop()
            try:
                _close_many(ancestors)
            except BaseException:
                _close_many([selected])
                raise
            self.fd = selected
        finally:
            _close_many(ancestors)

    @staticmethod
    def _directory_flags():
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            descriptor, self.fd = self.fd, None
            os.close(descriptor)

    def _parent(self, relative):
        parts = _relative(relative).split("/")
        descriptor = os.dup(self.fd)
        try:
            for component in parts[:-1]:
                following = _open(component, self._directory_flags(), dir_fd=descriptor)
                previous, descriptor = descriptor, following
                os.close(previous)
            return descriptor, parts[-1]
        except BaseException:
            os.close(descriptor)
            raise

    def read(self, relative, *, optional=False, budget=None):
        parent, leaf = self._parent(relative)
        descriptor = None
        try:
            try:
                descriptor = _open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                     dir_fd=parent)
            except FileNotFoundError:
                if optional:
                    return None
                raise
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Calendar input must be a regular file")
            if before.st_nlink != 1:
                raise ValueError("Multiply-linked calendar inputs (hardlinks) are rejected; use an ordinary independent file copy")
            limit = self.file_limit if budget is None else min(self.file_limit, budget)
            if before.st_size > limit:
                raise ValueError("Calendar input exceeds its file or total byte limit")
            chunks, length = [], 0
            while length <= limit:
                chunk = os.read(descriptor, min(65536, limit + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
            after = os.fstat(descriptor)
            if length > limit:
                raise ValueError("Calendar input exceeds its file or total byte limit")
            if _stamp(before) != _stamp(after) or length != after.st_size:
                raise ValueError("Calendar input changed during collection")
            return InputBytes(b"".join(chunks), (after.st_dev, after.st_ino))
        finally:
            _close_many([parent, descriptor])

    def exact_tree(self, files, version):
        expected = {**files, MANIFEST: version}
        directories = {p.as_posix() for name in expected for p in PurePosixPath(name).parents if p.as_posix() != "."}
        def walk(descriptor, prefix):
            wanted = {name[len(prefix):].split("/")[0] for name in expected if name.startswith(prefix)}
            before = os.fstat(descriptor)
            found = set()
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    if entry.name not in wanted or entry.name in found:
                        raise ValueError("Fixed version file/directory set differs from declared dependencies")
                    found.add(entry.name)
            if found != wanted:
                raise ValueError("Fixed version file/directory set differs from declared dependencies")
            for leaf in sorted(wanted):
                name = prefix + leaf
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
                if name in directories:
                    flags |= os.O_DIRECTORY
                child = _open(leaf, flags, dir_fd=descriptor)
                try:
                    info = os.fstat(child)
                    if name in directories:
                        walk(child, name + "/")
                    elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                          or (info.st_dev, info.st_ino) != expected[name].identity):
                        raise ValueError("Linked, replaced or unsupported fixed-version entry")
                finally:
                    os.close(child)
            if _stamp(before) != _stamp(os.fstat(descriptor)):
                raise ValueError("Fixed version directory changed during collection")
        walk(self.fd, "")




def open_project_directory(path, *, file_limit):
    if os.name == "nt":
        from engine.calendar_windows_io import WindowsProjectDirectory
        return WindowsProjectDirectory(path, file_limit=file_limit)
    return PosixProjectDirectory(path, file_limit=file_limit)
