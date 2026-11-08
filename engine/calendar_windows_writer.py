"""Unconnected Windows direct-final writer foundation; native evidence pending.

NOT a connected application route and NOT natively qualified. Existing parent
required. Final name is visible during writing, not atomic-after-seal publication.
Failures retain named partial or complete-but-unconfirmed files and close owners.
The caller supplies container validation and independently trusted size/digest.

The native adapter uses sibling calendar_windows_io parent/metadata primitives;
this foundation reader includes explicit cleanup fixes beyond the old prototype.
Fake and real-operation native test definitions are under tests/. Passing pure
logic tests or skipping native cases does not establish Windows guarantees.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import hashlib
import importlib
import os
import re
import unicodedata


CHUNK_SIZE = 64 * 1024
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
_RESERVED |= {prefix + suffix for prefix in ("COM", "LPT") for suffix in "123456789¹²³"}


def _leaf(value: str) -> str:
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise ValueError("One nonempty ordinary final filename is required")
    if any(c in '<>:\\/?*|\"' or unicodedata.category(c) == "Cc" for c in value):
        raise ValueError("Final filename contains a Windows-special character")
    if value[-1] in " ." or value.split(".", 1)[0].rstrip(" .").upper() in _RESERVED:
        raise ValueError("Final filename is a Windows alias or device name")
    try:
        length = len(value.encode("utf-16-le", errors="strict"))
    except UnicodeError as exc:
        raise ValueError("Final filename is not valid Unicode") from exc
    if length > 510:
        raise ValueError("Final filename exceeds 255 UTF-16 code units")
    return value


def _integer(value, name: str) -> int:
    if type(value) is not int or not 0 <= value <= (1 << 63) - 1:
        raise ValueError(name + " must be a nonnegative signed-64-bit integer")
    return value


@dataclass(frozen=True)
class ObjectState:
    identity: tuple
    directory: bool
    size: int
    links: int
    attributes: int = 0
    reparse_tag: int = 0
    delete_pending: bool = False
    change_stamp: tuple = ()


@dataclass(frozen=True)
class Acquisition:
    # Adapter transfers sole ownership of handle to the writer immediately.
    handle: object
    final_success: bool
    newly_created: bool


@dataclass(frozen=True)
class SnapshotReceipt:
    commit_mode: str
    visibility_mode: str
    phase: str
    creation: str
    final_leaf: str
    parent_identity: tuple | None
    object_identity: tuple | None
    expected_size: int
    expected_sha256: str
    bytes_written: int
    expected_bytes_verified: bool
    data_flush: str
    resources_released: str
    named_output_may_remain: bool
    native_validation: str = "NOT_RUN"
    codec_validation: str = "caller_responsibility"
    namespace_durability: str = "not_established"


class SnapshotWriteError(OSError):
    def __init__(self, message: str, receipt: SnapshotReceipt):
        super().__init__(message)
        self.receipt = receipt


class NativeCreationError(OSError):
    def __init__(self, message: str, *, creation: str = "unknown", resource_state: str = "unconfirmed"):
        super().__init__(message)
        self.creation = creation
        self.resource_state = resource_state


def _failure_diagnostics(error, receipt, note=None):
    """Optional metadata must never replace the selected failure."""
    try:
        error.receipt = receipt
    except BaseException:
        pass
    if note is not None:
        _failure_note(error, note)


def _failure_note(error, note):
    try:
        error.add_note(note)
    except BaseException:
        pass


def _select_failure(primary, failures):
    """Keep the first cancellation, otherwise the primary ordinary failure."""
    for error in (primary, *failures):
        if error is not None and not isinstance(error, Exception):
            return error
    return primary if primary is not None else next(iter(failures), None)


class WindowsDirectSnapshot:
    """One new file, a held parent, strict byte checks, and close-only failure.

    Use ``with writer: writer.write_all(chunks); writer.seal()``. Read
    ``writer.receipt`` after context exit for final resource-release status.
    A receipt returned by seal() is an immutable pre-close observation.
    Native tests are required before this interface can be integrated/shipped.
    """

    def __init__(self, existing_parent, final_leaf: str, *, expected_size: int,
                 expected_sha256: str, max_bytes: int, adapter=None):
        self.parent_path = os.fspath(existing_parent)
        if not isinstance(self.parent_path, str) or not self.parent_path:
            raise ValueError("An existing Unicode parent path is required")
        self.final_leaf = _leaf(final_leaf)
        self.expected_size = _integer(expected_size, "expected_size")
        self.max_bytes = _integer(max_bytes, "max_bytes")
        if self.expected_size > self.max_bytes:
            raise ValueError("Expected size exceeds the caller's byte budget")
        if not isinstance(expected_sha256, str) or not re.fullmatch("[0-9a-f]{64}", expected_sha256):
            raise ValueError("A lowercase SHA-256 digest of the complete encoded file is required")
        self.expected_sha256 = expected_sha256
        self._adapter = adapter
        self._parent = self._file = None
        self._parent_id = self._file_id = None
        self._phase = "not_started"
        self._creation = "not_attempted"
        self._written = 0
        self._verified = False
        self._flush = "not_attempted"
        self._released = "not_acquired"
        self._unreturned_acquisition = False

    @property
    def receipt(self) -> SnapshotReceipt:
        return SnapshotReceipt(
            "validity_based", "visible_during_write", self._phase, self._creation, self.final_leaf,
            self._parent_id, self._file_id, self.expected_size, self.expected_sha256,
            self._written, self._verified, self._flush, self._released,
            self._creation in ("created", "unknown"))

    def _error(self, message, cause=None):
        self._phase = "failed"
        error = SnapshotWriteError(message + "; no deletion attempted; output may remain, possibly complete but unacknowledged",
                                   self.receipt)
        if cause is None:
            raise error
        raise error from cause

    def __enter__(self):
        if self._phase != "not_started":
            raise ValueError("A snapshot writer cannot be entered twice")
        self._phase = "acquiring"
        try:
            if self._adapter is None:
                self._adapter = NativeWindowsAdapter()
            # An adapter can acquire some native resources and then raise while
            # cleaning them up. Without a returned owner, release is unproven.
            self._unreturned_acquisition = True
            self._released = "unconfirmed"
            self._parent = self._adapter.open_existing_parent(self.parent_path)
            self._unreturned_acquisition = False
            self._released = "held"
            self._parent_id = self._parent.identity
            self._parent.check()
            self._creation = "unknown"  # Do not infer absence if the call's outcome is uncertain.
            self._unreturned_acquisition = True
            acquired = self._adapter.create_new(self._parent, self.final_leaf)
            self._file = acquired.handle  # Adopt before any subsequent fallible check.
            if self._file is None:
                self._error("Native creation returned no owned handle")
            self._unreturned_acquisition = False
            if not acquired.final_success or not acquired.newly_created:
                self._error("Native creation did not prove synchronous exclusive creation")
            self._creation = "created"
            self._adapter.ensure_noninheritance(self._file)
            initial = self._inspect_file()
            self._file_id = initial.identity
            if initial.size != 0:
                self._error("A newly created snapshot file was not empty")
            self._parent.check()
            self._phase = "writing"
            return self
        except BaseException as exc:
            if isinstance(exc, NativeCreationError):
                self._creation = exc.creation
                if exc.resource_state == "not_acquired":
                    self._unreturned_acquisition = False
            self._phase = "failed"
            failures = self._close_resources()
            selected = _select_failure(exc, failures)
            if not isinstance(selected, Exception):
                note = "Snapshot acquisition interrupted; resources closed where possible; no deletion attempted"
                if failures:
                    note += "; one or more owned-handle closes also failed; release is unconfirmed"
                _failure_diagnostics(selected, self.receipt, note)
                if selected is exc:
                    raise
                raise selected from exc
            if isinstance(exc, SnapshotWriteError):
                _failure_diagnostics(exc, self.receipt)
                if failures:
                    _failure_note(exc, "One or more owned-handle closes also failed")
                raise
            error = SnapshotWriteError("Snapshot acquisition failed; no deletion attempted", self.receipt)
            if failures:
                _failure_note(error, "One or more owned-handle closes also failed")
            raise error from exc

    def _inspect_file(self) -> ObjectState:
        state = self._adapter.inspect(self._file)
        if (state.directory or state.delete_pending or state.links != 1
                or state.attributes & (0x40 | 0x400) or state.reparse_tag
                or type(state.size) is not int or state.size < 0
                or not state.identity or not self._parent_id
                or state.identity[0] != self._parent_id[0]):
            self._error("Snapshot handle failed type, reparse, link, volume or size checks")
        if self._file_id is not None and state.identity != self._file_id:
            self._error("Snapshot handle identity changed")
        return state

    def _require_writing(self):
        if self._phase != "writing" or self._file is None:
            raise ValueError("Snapshot is not writable in its current state")

    def write_all(self, chunks) -> None:
        self._require_writing()
        try:
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    self._error("Snapshot chunks must be immutable bytes")
                if len(chunk) > self.expected_size - self._written:
                    self._error("Snapshot data exceeds the expected complete size")
                offset = 0
                while offset < len(chunk):
                    piece = chunk[offset:offset + CHUNK_SIZE]
                    count = self._adapter.write(self._file, piece)
                    if type(count) is not int or not 0 < count <= len(piece):
                        self._error("Native snapshot write returned an invalid byte count")
                    self._written += count
                    offset += count
        except SnapshotWriteError:
            raise
        except Exception as exc:
            self._error("Snapshot write failed", exc)

    def seal(self) -> SnapshotReceipt:
        self._require_writing()
        try:
            if self._written != self.expected_size:
                self._error("Snapshot byte count does not match the expected complete size")
            self._parent.check()
            initial = self._inspect_file()
            if initial.size != self.expected_size:
                self._error("Snapshot handle size differs before verification")
            self._flush = "attempted"
            self._adapter.flush(self._file)
            self._flush = "confirmed"
            before = self._inspect_file()  # Baseline after effects of our own write/flush.
            self._adapter.rewind(self._file)
            digest = hashlib.sha256()
            read_count = 0
            while read_count <= self.expected_size:
                allowance = min(CHUNK_SIZE, self.expected_size + 1 - read_count)
                block = self._adapter.read(self._file, allowance)
                if not isinstance(block, bytes) or len(block) > allowance:
                    self._error("Native verification read exceeded its allowance")
                if not block:
                    break
                read_count += len(block)
                digest.update(block)
            after = self._inspect_file()
            self._parent.check()
            if (read_count != self.expected_size or digest.hexdigest() != self.expected_sha256
                    or before != after):
                self._error("Snapshot bytes or metadata differ from the independently expected encoding")
            self._verified = True
            self._phase = "sealed"
            return self.receipt
        except SnapshotWriteError:
            raise
        except Exception as exc:
            self._error("Snapshot seal failed", exc)

    def _close_resources(self) -> list[BaseException]:
        file, parent = self._file, self._parent
        self._file = self._parent = None  # No second close attempt on a reused numeric value.
        failures = []
        for resource in (file, parent):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as exc:
                    failures.append(exc)
        if self._unreturned_acquisition:
            self._released = "unconfirmed"
        elif file is not None or parent is not None:
            self._released = "unconfirmed" if failures else "confirmed"
        return failures

    def __exit__(self, exc_type, exc, tb):
        unsealed = exc is None and self._phase != "sealed"
        if exc is not None or unsealed:
            self._phase = "failed"
        failures = self._close_resources()
        if failures and exc is None:
            self._phase = "failed_after_seal" if self._verified else "failed"
        selected = _select_failure(exc, failures)
        if selected is not None and not isinstance(selected, Exception):
            note = "One or more owned-handle closes also failed; release is unconfirmed" if failures else None
            _failure_diagnostics(selected, self.receipt, note)
            if selected is exc:
                return False
            if exc is None:
                raise selected
            raise selected from exc
        if exc is not None:
            if isinstance(exc, SnapshotWriteError):
                _failure_diagnostics(exc, self.receipt)
            if failures:
                _failure_note(exc, "One or more owned-handle closes also failed; release is unconfirmed")
            return False
        if failures:
            raise SnapshotWriteError("Snapshot resource release failed; no deletion attempted",
                                     self.receipt) from failures[0]
        if unsealed:
            raise SnapshotWriteError("Snapshot context exited without sealing; no deletion attempted",
                                     self.receipt)
        self._phase = "released"
        return False


class _BorrowedReaderParent:
    """Prototype-only dependency on the sibling reader's retained anchor ledger."""
    def __init__(self, project):
        self.project = project
        self.identity = project._anchors[-1][1]

    @property
    def value(self):
        return self.project._anchors[-1][0].value

    def check(self):
        self.project._check_anchors()

    def close(self):
        self.project.close()


class NativeWindowsAdapter:
    """Actual native call shapes, UNEXECUTED on Windows in this design task.

    Uses the sibling engine.calendar_windows_io reader. Application routes are
    not connected; native qualification is still required before release.
    """
    def __init__(self, reader_module=None):
        if os.name != "nt":
            raise OSError("Native Windows prototype cannot run on this platform")
        self.reader = reader_module or importlib.import_module("engine.calendar_windows_io")
        self.native = self.reader._Native()
        bind = self.native._bind
        kernel = self.native.kernel
        handle, dword = self.reader._HANDLE, self.reader._DWORD
        bind(kernel, "WriteFile", C.c_int, handle, C.c_void_p, dword,
             C.POINTER(dword), C.c_void_p)
        bind(kernel, "FlushFileBuffers", C.c_int, handle)
        bind(kernel, "SetFilePointerEx", C.c_int, handle, C.c_int64,
             C.POINTER(C.c_int64), dword)

    def open_existing_parent(self, path):
        project = self.reader.WindowsProjectDirectory(path, file_limit=0)
        try:
            project.__enter__()
            return _BorrowedReaderParent(project)
        except BaseException as exc:
            failure = None
            try:
                project.close()
            except BaseException as cleanup:
                failure = cleanup
            if failure is not None:
                selected = _select_failure(exc, [failure])
                note = "Snapshot parent acquisition cleanup also failed; release is unconfirmed"
                if isinstance(selected, Exception):
                    _failure_note(selected, note)
                else:
                    _failure_diagnostics(selected, None, note)
                if selected is not exc:
                    raise selected from exc
            raise

    def create_new(self, parent, leaf):
        r = self.reader
        leaf = r._component(leaf)
        encoded = leaf.encode("utf-16-le", errors="strict")
        buffer = C.create_string_buffer(encoded + b"\0\0")
        string = r._UnicodeString(len(encoded), len(encoded) + 2, C.addressof(buffer))
        attributes = r._ObjectAttributes(C.sizeof(r._ObjectAttributes), parent.value,
                                        C.pointer(string), r._OBJ_CASE_INSENSITIVE, None, None)
        result, iosb = r._HANDLE(), r._IoStatus()
        # GENERIC_READ | GENERIC_WRITE, share zero, FILE_CREATE, file-only,
        # synchronous no-follow. No DELETE access, rename or delete-on-close.
        status = self.native.ntdll.NtCreateFile(
            C.byref(result), 0xC0000000, C.byref(attributes), C.byref(iosb),
            None, 0x80, 0, 2, 0x40 | r._FILE_SYNCHRONOUS_IO_NONALERT | r._FILE_OPEN_REPARSE_POINT,
            None, 0)
        if status < 0:
            code = self.native.ntdll.RtlNtStatusToDosError(status)
            cause = self.native._error("NtCreateFile(FILE_CREATE), NTSTATUS 0x%08x" %
                                       (status & 0xFFFFFFFF), code)
            creation = "conflict" if status & 0xFFFFFFFF == 0xC0000035 else "unknown"
            resource_state = "not_acquired" if not result.value else "unconfirmed"
            raise NativeCreationError(str(cause), creation=creation, resource_state=resource_state) from cause
        if not result.value or result.value == r._INVALID_HANDLE_VALUE:
            raise NativeCreationError("Native creation returned no usable handle")
        # Transfer a wrapper even for unexpected success metadata so the caller
        # can close it without ever writing or attempting deletion.
        owned = r._OwnedHandle(self.native, result.value)
        return Acquisition(owned, status == 0 and iosb.result.Status == 0,
                           int(iosb.Information) == 2)

    def ensure_noninheritance(self, handle):
        kernel = self.native.kernel
        if not kernel.SetHandleInformation(handle.value, 1, 0):
            raise self.native._error("SetHandleInformation")
        flags = self.reader._DWORD()
        if not kernel.GetHandleInformation(handle.value, C.byref(flags)):
            raise self.native._error("GetHandleInformation")
        if flags.value & 1:
            raise OSError("Native snapshot handle is unexpectedly inheritable")

    def inspect(self, handle):
        s = self.native.inspect(handle.value)
        # The reader inspect() rejects non-disk/reparse/delete-pending objects.
        return ObjectState(s.identity, s.directory, s.size, s.links, s.attributes,
                           s.tag, False, (s.creation, s.write, s.change))

    def write(self, handle, data):
        buffer = C.create_string_buffer(data)
        count = self.reader._DWORD()
        if not self.native.kernel.WriteFile(handle.value, buffer, len(data), C.byref(count), None):
            raise self.native._error("WriteFile")
        return int(count.value)

    def flush(self, handle):
        if not self.native.kernel.FlushFileBuffers(handle.value):
            raise self.native._error("FlushFileBuffers")

    def rewind(self, handle):
        position = C.c_int64()
        if not self.native.kernel.SetFilePointerEx(handle.value, 0, C.byref(position), 0):
            raise self.native._error("SetFilePointerEx")
        if position.value != 0:
            raise OSError("Native snapshot rewind did not reach offset zero")

    def read(self, handle, amount):
        return self.native.read(handle.value, amount)
