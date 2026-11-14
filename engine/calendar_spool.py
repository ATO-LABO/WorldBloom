"""Unconnected foundation: owned Linux unnamed spool, not a public application API.

No pathname fallback. Records keep the owner alive; explicit close invalidates
all records. A lock covers each positional read and close to prevent fd reuse.
The immutability claim is the application API, not protection against debugger,
proc-fd access, descriptor theft or arbitrary same-authority process attacks.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sys
import threading

from engine.calendar_fs import PosixProjectDirectory, _relative, _stamp, _close_many

FILE_LIMIT = 8 * 1024 * 1024
MAX_MEMBERS = 1004  # world + contract + 1000 subjects + graph + manifest
MAX_CAPTURE = MAX_MEMBERS * FILE_LIMIT
CHUNK = 65536


def integer(value, maximum, label):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('Invalid ' + label)
    return value


def close_owners(owners):
    errors = []
    for owner in owners:
        try:
            owner.close()
        except BaseException as error:
            errors.append(error)
    if errors:
        raise next((e for e in errors if not isinstance(e, Exception)), errors[0])


class PrivateSpool:
    def __init__(self, *, max_bytes=MAX_CAPTURE, parent='/tmp'):
        self._fd = None
        self._lock = threading.RLock()
        self._sealed = False
        self._length = 0
        self._hash = hashlib.sha256()
        self.max_bytes = integer(max_bytes, (1 << 64) - 1, 'spool limit')
        if sys.platform != 'linux' or not hasattr(os, 'O_TMPFILE'):
            raise ValueError('Private spool requires supported Linux O_TMPFILE; no pathname fallback')
        try:
            with PosixProjectDirectory(parent, file_limit=FILE_LIMIT) as directory:
                self._fd = os.open('.', os.O_TMPFILE | os.O_RDWR | os.O_CLOEXEC, 0o600, dir_fd=directory.fd)
        except BaseException as error:
            self.close(_primary=error)
            raise

    def _alive(self):
        if self._fd is None:
            raise ValueError('Spool is closed')

    @property
    def size(self):
        return self._length

    def append(self, data):
        if type(data) is not bytes:
            raise ValueError('Spool append requires bytes')
        with self._lock:
            self._alive()
            if self._sealed or len(data) > self.max_bytes - self._length:
                raise ValueError('Sealed or oversized spool')
            offset = self._length
            remaining = memoryview(data)
            try:
                while remaining:
                    count = os.write(self._fd, remaining)
                    if count <= 0:
                        raise OSError('Short spool write')
                    remaining = remaining[count:]
                self._hash.update(data)
                self._length += len(data)
                return offset
            except BaseException as error:
                self.close(_primary=error)  # no reusable partially accounted append
                raise

    def add(self, data, *, source_identity=None):
        if len(data) > FILE_LIMIT:
            raise ValueError('Member exceeds existing 8 MiB bound')
        offset = self.append(data)
        return SpoolRecord(self, offset, len(data), hashlib.sha256(data).hexdigest(), source_identity, None)

    def seal(self):
        with self._lock:
            self._alive()
            if self._sealed:
                return self
            try:
                if os.fstat(self._fd).st_size != self._length:
                    raise ValueError('Spool size changed')
                os.fsync(self._fd)
                actual = hashlib.sha256()
                position = 0
                while position < self._length:
                    data = os.pread(self._fd, min(CHUNK, self._length - position), position)
                    if not data:
                        raise ValueError('Truncated spool')
                    actual.update(data)
                    position += len(data)
                if actual.digest() != self._hash.digest():
                    raise ValueError('Spool bytes changed')
                self._sealed = True
                return self
            except BaseException as error:
                self.close(_primary=error)
                raise

    def read_at(self, offset, size):
        integer(offset, (1 << 64) - 1, 'slice offset')
        integer(size, FILE_LIMIT, 'slice size')
        with self._lock:
            self._alive()
            if not self._sealed or offset > self._length or size > self._length - offset:
                raise ValueError('Unsealed or out-of-bounds spool slice')
            result = bytearray()
            while len(result) < size:
                chunk = os.pread(self._fd, min(CHUNK, size - len(result)), offset + len(result))
                if not chunk:
                    raise ValueError('Truncated spool slice')
                result.extend(chunk)
            return bytes(result)

    @property
    def sha256(self):
        with self._lock:
            self._alive()
            if not self._sealed:
                raise ValueError('Unsealed spool')
            return self._hash.hexdigest()

    def close(self, *, _primary=None):
        with self._lock:
            if self._fd is not None:
                descriptor, self._fd = self._fd, None
                # Preserve an active append/seal/construction error if release
                # also fails. Ownership is consumed before the one close call.
                _close_many([descriptor], primary=_primary)

    def __del__(self):
        if getattr(self, '_fd', None) is not None:
            try:
                self.close()
            except OSError:
                pass  # finalizer cannot report; explicit close/context can

    def __enter__(self):
        self._alive()
        return self

    def __exit__(self, exc_type=None, exc=None, tb=None):
        self.close(_primary=exc)


@dataclass(frozen=True)
class SpoolRecord:
    owner: PrivateSpool
    offset: int
    size: int
    sha256: str
    source_identity: tuple | None
    member_identity: tuple | None

    @property
    def identity(self):
        # Container members are logical ranges, not separate filesystem inodes.
        return self.source_identity if self.source_identity is not None else self.member_identity

    @property
    def data(self):
        data = self.owner.read_at(self.offset, self.size)
        if hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError('Captured member digest mismatch')
        return data

    def iter_chunks(self):
        self.owner.read_at(self.offset, 0)  # empty members still require a live sealed owner
        digest = hashlib.sha256()
        for offset in range(0, self.size, CHUNK):
            chunk = self.owner.read_at(self.offset + offset, min(CHUNK, self.size - offset))
            digest.update(chunk)
            yield chunk
        if digest.hexdigest() != self.sha256:
            raise ValueError('Captured member digest mismatch')

    def manifest(self):
        return {'size_bytes': self.size, 'sha256': self.sha256}


def capture_file(path, *, limit):
    """Capture one possibly large container through a pinned no-follow handle."""
    import stat
    source = Path(path).absolute()
    spool = PrivateSpool(max_bytes=limit)
    descriptor = None
    try:
        with PosixProjectDirectory(source.parent, file_limit=limit) as directory:
            descriptor = os.open(_relative(source.name), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                 dir_fd=directory.fd)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('Container must be a bounded regular file')
            if before.st_nlink != 1:
                raise ValueError('Multiply-linked calendar inputs (hardlinks) are rejected; use an ordinary independent file copy')
            length = 0
            while length <= limit:
                chunk = os.read(descriptor, min(CHUNK, limit + 1 - length))
                if not chunk:
                    break
                length += len(chunk)
                if length > limit:
                    raise ValueError('Container exceeds derived size bound')
                spool.append(chunk)
            after = os.fstat(descriptor)
            if _stamp(before) != _stamp(after) or length != after.st_size:
                raise ValueError('Container changed during capture')
            # Transfer spool ownership only after source and parent close. A
            # close error may release the fd number before reporting failure.
            owned, descriptor = descriptor, None
            os.close(owned)
        return spool.seal()
    except BaseException:
        spool.close()
        raise
    finally:
        if descriptor is not None:
            owned, descriptor = descriptor, None
            os.close(owned)
