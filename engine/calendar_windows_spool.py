"""Isolated Windows owned-handle spool candidate; no native qualification.

Only a newly FILE_CREATE-acquired spool uses FILE_DELETE_ON_CLOSE, specified in
that original create. Persistent snapshot outputs never use this helper. There
is no name-based unlink/rmtree fallback, permission change, or existing-file
adoption. Spool inspection intentionally differs from the sibling input reader's
delete-pending rejection and does not change that reader.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import uuid

from engine.calendar_windows_writer import (
    Acquisition, NativeCreationError, NativeWindowsAdapter, ObjectState, _integer, _leaf,
)


FILE_LIMIT = 8 * 1024 * 1024
MAX_MEMBERS = 1004
MAX_CAPTURE = MAX_MEMBERS * FILE_LIMIT
CHUNK = 65536


@dataclass(frozen=True)
class SpoolStatus:
    creation: str
    handle_release: str
    delete_on_close_requested: bool
    deletion_observed: str
    named_residue_possible: bool
    size: int
    sealed: bool
    source_capture_release: str
    native_validation: str = "NOT_RUN"


class SpoolError(OSError):
    def __init__(self, message, status):
        super().__init__(message)
        self.spool_status = status


class WindowsPrivateSpool:
    def __init__(self, *, max_bytes=MAX_CAPTURE, parent=None, adapter=None):
        self._lock = threading.RLock()
        self._file = self._parent = None
        self._adapter = adapter
        self._sealed = False
        self._length = 0
        self._hash = hashlib.sha256()
        self._identity = None
        self._sealed_state = None
        self._release_uncertain = False
        self._released = "not_acquired"
        self._creation = "not_attempted"
        self._capture_release = "not_applicable"
        self.max_bytes = _integer(max_bytes, "spool byte limit")
        self._parent_path = os.fspath(tempfile.gettempdir() if parent is None else parent)
        if not isinstance(self._parent_path, str) or not self._parent_path:
            raise ValueError("Existing Unicode spool parent required")
        self._leaf = "worldbloom-spool-" + uuid.uuid4().hex + ".tmp"
        try:
            if self._adapter is None:
                self._adapter = NativeWindowsSpoolAdapter()
            self._release_uncertain = True
            self._released = "unconfirmed"
            self._parent = self._adapter.open_existing_parent(self._parent_path)
            self._release_uncertain = False
            self._released = "held"
            self._parent.check()
            self._creation = "unknown"
            self._release_uncertain = True
            acquired = self._adapter.create_spool(self._parent, self._leaf)
            self._file = acquired.handle
            if self._file is None:
                raise OSError("Spool creation returned no owned handle")
            self._release_uncertain = False
            if not acquired.final_success or not acquired.newly_created:
                raise OSError("Spool creation did not prove a synchronous new object")
            self._creation = "created"
            self._adapter.ensure_noninheritance(self._file)
            state = self._inspect()
            if state.size != 0:
                raise OSError("Newly acquired spool was not empty")
            self._identity = state.identity
            self._parent.check()
        except BaseException as error:
            if isinstance(error, NativeCreationError):
                self._creation = error.creation
                if error.resource_state == "not_acquired":
                    self._release_uncertain = False
            self._abort(error)

    @property
    def status(self):
        return SpoolStatus(self._creation, self._released, True, "not_established",
                           self._creation in ("created", "unknown"), self._length, self._sealed,
                           self._capture_release)

    def _abort(self, error):
        try:
            self.close()
        except BaseException as cleanup:
            error.add_note("Owned spool cleanup also failed: " + str(cleanup))
            if isinstance(error, Exception) and not isinstance(cleanup, Exception):
                raise cleanup from error
        if not isinstance(error, Exception):
            error.spool_status = self.status
            raise error
        raise SpoolError(str(error) + "; no pathname cleanup attempted", self.status) from error

    def _alive(self):
        if self._file is None:
            raise ValueError("Spool is closed")

    def _inspect(self):
        state = self._adapter.inspect_spool(self._file)
        if (state.directory or state.links != 1 or state.reparse_tag
                or state.attributes & (0x10 | 0x40 | 0x400)
                or type(state.size) is not int or state.size < 0
                or not state.identity or state.identity[0] != self._parent.identity[0]):
            raise ValueError("Spool type, reparse, link, volume or size check failed")
        if self._identity is not None and state.identity != self._identity:
            raise ValueError("Owned spool identity changed")
        # DeletePending is permitted ONLY in this owned delete-on-close spool
        # path. No generic/input/output reader policy is relaxed.
        return state

    @property
    def size(self):
        with self._lock:
            return self._length

    def append(self, data):
        if type(data) is not bytes:
            raise ValueError("Spool append requires immutable bytes")
        with self._lock:
            self._alive()
            if self._sealed or len(data) > self.max_bytes - self._length:
                raise ValueError("Sealed or oversized spool")
            offset = self._length
            try:
                self._adapter.seek(self._file, offset)
                position = 0
                while position < len(data):
                    chunk = data[position:position + CHUNK]
                    count = self._adapter.write(self._file, chunk)
                    if type(count) is not int or not 0 < count <= len(chunk):
                        raise OSError("Invalid native spool write byte count")
                    position += count
                self._hash.update(data)
                self._length += len(data)
                return offset
            except BaseException as error:
                self._abort(error)

    def add(self, data, *, source_identity=None):
        if type(data) is not bytes or len(data) > FILE_LIMIT:
            raise ValueError("Member must be bytes within the existing 8 MiB bound")
        offset = self.append(data)
        return SpoolRecord(self, offset, len(data), hashlib.sha256(data).hexdigest(), source_identity, None)

    def seal(self):
        with self._lock:
            self._alive()
            if self._sealed:
                return self
            try:
                self._parent.check()
                if self._inspect().size != self._length:
                    raise ValueError("Spool size changed")
                self._adapter.flush(self._file)
                before = self._inspect()
                self._adapter.seek(self._file, 0)
                digest, position = hashlib.sha256(), 0
                while position <= self._length:
                    allowance = min(CHUNK, self._length + 1 - position)
                    block = self._adapter.read(self._file, allowance)
                    if type(block) is not bytes or len(block) > allowance:
                        raise OSError("Spool read exceeded its bound")
                    if not block:
                        break
                    digest.update(block)
                    position += len(block)
                after = self._inspect()
                self._parent.check()
                if position != self._length or digest.digest() != self._hash.digest() or before != after:
                    raise ValueError("Spool bytes or metadata changed")
                self._sealed_state = after
                self._sealed = True
                return self
            except BaseException as error:
                self._abort(error)

    def read_at(self, offset, size):
        _integer(offset, "slice offset")
        _integer(size, "slice size")
        if size > FILE_LIMIT:
            raise ValueError("Spool slice exceeds existing 8 MiB member bound")
        with self._lock:
            self._alive()
            if not self._sealed or offset > self._length or size > self._length - offset:
                raise ValueError("Unsealed or out-of-bounds spool slice")
            try:
                if self._inspect() != self._sealed_state:
                    raise ValueError("Sealed spool metadata changed")
                self._adapter.seek(self._file, offset)
                result = bytearray()
                while len(result) < size:
                    allowance = min(CHUNK, size - len(result))
                    block = self._adapter.read(self._file, allowance)
                    if type(block) is not bytes or not block or len(block) > allowance:
                        raise ValueError("Invalid or truncated spool slice")
                    result.extend(block)
                if self._inspect() != self._sealed_state:
                    raise ValueError("Spool metadata changed during slice read")
                return bytes(result)
            except BaseException as error:
                self._abort(error)

    @property
    def sha256(self):
        with self._lock:
            self._alive()
            if not self._sealed:
                raise ValueError("Spool is not sealed")
            return self._hash.hexdigest()

    def close(self):
        with self._lock:
            file, parent = self._file, self._parent
            self._file = self._parent = None
            failures = []
            for owner in (file, parent):
                if owner is not None:
                    try:
                        owner.close()
                    except BaseException as error:
                        failures.append(error)
            if self._release_uncertain or failures:
                self._released = "unconfirmed"
            elif file is not None or parent is not None:
                self._released = "confirmed"
            if failures:
                error = next((e for e in failures if not isinstance(e, Exception)), failures[0])
                if not isinstance(error, Exception):
                    error.spool_status = self.status
                    raise error
                raise SpoolError("Owned spool release failed; no pathname cleanup attempted", self.status) from error

    def __enter__(self):
        self._alive()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except BaseException as cleanup:
            if exc is None or (isinstance(exc, Exception) and not isinstance(cleanup, Exception)):
                raise
            exc.add_note("Owned spool release also failed: " + str(cleanup))
        return False

    def __del__(self):
        if getattr(self, "_file", None) is not None or getattr(self, "_parent", None) is not None:
            try:
                self.close()
            except BaseException:
                pass  # Explicit close/context reports failures; destructor cannot.


@dataclass(frozen=True)
class SpoolRecord:
    owner: WindowsPrivateSpool
    offset: int
    size: int
    sha256: str
    source_identity: tuple | None
    member_identity: tuple | None

    @property
    def identity(self):
        return self.source_identity if self.source_identity is not None else self.member_identity

    @property
    def data(self):
        data = self.owner.read_at(self.offset, self.size)
        if hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError("Captured member digest mismatch")
        return data

    def iter_chunks(self):
        self.owner.read_at(self.offset, 0)  # Even an empty record needs a live sealed owner.
        digest = hashlib.sha256()
        for offset in range(0, self.size, CHUNK):
            chunk = self.owner.read_at(self.offset + offset, min(CHUNK, self.size - offset))
            digest.update(chunk)
            yield chunk
        if digest.hexdigest() != self.sha256:
            raise ValueError("Captured member digest mismatch")

    def manifest(self):
        return {"size_bytes": self.size, "sha256": self.sha256}


class NativeWindowsSpoolAdapter(NativeWindowsAdapter):
    """Separate pending-deletion-aware native spool path; still UNVALIDATED."""
    def create_spool(self, parent, leaf):
        r = self.reader
        encoded = r._component(_leaf(leaf)).encode("utf-16-le", errors="strict")
        buffer = C.create_string_buffer(encoded + b"\0\0")
        string = r._UnicodeString(len(encoded), len(encoded) + 2, C.addressof(buffer))
        attrs = r._ObjectAttributes(C.sizeof(r._ObjectAttributes), parent.value,
                                    C.pointer(string), r._OBJ_CASE_INSENSITIVE, None, None)
        result, iosb = r._HANDLE(), r._IoStatus()
        # Synchronous NtCreateFile requires explicit SYNCHRONIZE in addition
        # to GENERIC_READ | GENERIC_WRITE | DELETE for this owned spool.
        status = self.native.ntdll.NtCreateFile(
            C.byref(result), 0xC0010000 | r._SYNCHRONIZE, C.byref(attrs), C.byref(iosb), None,
            0x100, 0, 2, 0x40 | 0x1000 | r._FILE_SYNCHRONOUS_IO_NONALERT | r._FILE_OPEN_REPARSE_POINT,
            None, 0)
        if status < 0:
            code = self.native.ntdll.RtlNtStatusToDosError(status)
            cause = self.native._error("NtCreateFile(owned delete-on-close spool), NTSTATUS 0x%08x" %
                                       (status & 0xFFFFFFFF), code)
            creation = "conflict" if status & 0xFFFFFFFF == 0xC0000035 else "unknown"
            resource = "not_acquired" if not result.value else "unconfirmed"
            raise NativeCreationError(str(cause), creation=creation, resource_state=resource) from cause
        if not result.value or result.value == r._INVALID_HANDLE_VALUE:
            raise NativeCreationError("Native spool creation returned no usable handle")
        return Acquisition(r._OwnedHandle(self.native, result.value),
                           status == 0 and iosb.result.Status == 0, int(iosb.Information) == 2)

    def inspect_spool(self, handle):
        r, native = self.reader, self.native
        if native.kernel.GetFileType(handle.value) != 1:
            raise ValueError("Spool is not a disk file")
        basic = native._query(handle.value, 0, r._BasicInfo)
        standard = native._query(handle.value, 1, r._StandardInfo)
        tag = native._query(handle.value, 9, r._AttributeTagInfo)
        identity = native._query(handle.value, 18, r._IdInfo)
        attributes = int(tag.FileAttributes)
        if not any(identity.FileId) or attributes != basic.FileAttributes:
            raise ValueError("Spool identity/attribute query was inconsistent")
        if (bool(standard.Directory) != bool(attributes & 0x10)
                or standard.EndOfFile < 0 or standard.AllocationSize < 0):
            raise ValueError("Spool type or size metadata was inconsistent")
        return ObjectState((int(identity.VolumeSerialNumber), bytes(identity.FileId)),
                           bool(standard.Directory), int(standard.EndOfFile), int(standard.NumberOfLinks),
                           attributes, int(tag.ReparseTag), bool(standard.DeletePending),
                           (int(basic.CreationTime), int(basic.LastWriteTime), int(basic.ChangeTime)))

    def seek(self, handle, offset):
        _integer(offset, "native spool offset")
        position = C.c_int64()
        if not self.native.kernel.SetFilePointerEx(handle.value, offset, C.byref(position), 0):
            raise self.native._error("SetFilePointerEx(spool)")
        if position.value != offset:
            raise OSError("Native spool seek returned the wrong position")

    def open_input(self, parent, leaf):
        return self.native.open_relative(parent.value, self.reader._component(_leaf(leaf)))

    def inspect_input(self, handle):
        return self.native.inspect(handle.value)


def capture_windows_file(path, *, limit, spool_parent=None, adapter=None):
    """Stream a bounded outer container into one owned spool, never full-buffer it.

    Integration with the common codec is still separate. This does not change
    the sibling reader or simply raise its eager-read per-file limit.
    """
    limit = _integer(limit, "outer container byte limit")
    adapter = adapter or NativeWindowsSpoolAdapter()
    source = Path(path).absolute()
    spool = WindowsPrivateSpool(max_bytes=limit, parent=spool_parent, adapter=adapter)
    parent = file = None
    failure = None
    unreturned_source = False
    try:
        unreturned_source = True
        spool._capture_release = "unconfirmed"
        parent = adapter.open_existing_parent(source.parent)
        unreturned_source = False
        parent.check()
        unreturned_source = True
        file = adapter.open_input(parent, _leaf(source.name))
        unreturned_source = False
        before = adapter.inspect_input(file)
        if before.directory or before.identity[0] != parent.identity[0] or before.size > limit:
            raise ValueError("Outer container is not a bounded regular file in the selected volume")
        if before.links != 1:
            raise ValueError("Multiply-linked calendar containers are refused; create an independent copy, not a hard link")
        position = 0
        while position <= limit:
            allowance = min(CHUNK, limit + 1 - position)
            block = adapter.read(file, allowance)
            if type(block) is not bytes or len(block) > allowance:
                raise OSError("Outer container read exceeded its allowance")
            if not block:
                break
            position += len(block)
            if position > limit:
                raise ValueError("Outer container exceeded its derived byte bound")
            spool.append(block)
        after = adapter.inspect_input(file)
        parent.check()
        if before != after or position != after.size:
            raise ValueError("Outer container changed during capture")
        spool.seal()
    except BaseException as error:
        failure = error
    for owner in (file, parent):
        if owner is not None:
            try:
                owner.close()
            except BaseException as cleanup:
                unreturned_source = True
                if failure is None or (isinstance(failure, Exception) and not isinstance(cleanup, Exception)):
                    failure = cleanup
                else:
                    failure.add_note("Source capture close also failed: " + str(cleanup))
    spool._capture_release = "unconfirmed" if unreturned_source else "confirmed"
    if failure is not None:
        spool._abort(failure)
    return spool
