"""Private Windows project-reader candidate; native Windows validation is pending.

This module intentionally has no pathname-open fallback. It pins a local NTFS
volume and every root ancestor, then uses one-component NtCreateFile calls
relative to retained handles. Parsing and hashing use bytes from the inspected
file handle. It does not implement a writer or an atomic multi-file snapshot.

The deliberately narrow *candidate* policy rejects reparse points (including
cloud placeholders), multiply-linked files, UNC/SUBST drives and non-NTFS
volumes. Ordinary reads accept case/short-name aliases and compare native
identities; fixed-version exact-tree validation still requires the enumerated
names to match the declared tree. The restrictions require owner review.

Share-read-only handles exclude ordinary conflicting data-write/delete opens,
not all attribute/reparse changes, memory mappings or filesystem/filter
behavior. Metadata comparisons detect ordinary changes; they do not prove
absence of adversarial ABA changes. A retained directory denotes an object,
not a promise that its textual ancestry can never change. Native ABI, option
contracts, supported Windows builds and the packaged EXE need real tests.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import errno
import hashlib
import ntpath
import os
import re
import sys
import unicodedata
from collections.abc import Mapping


# Fixed-width Windows types: ctypes.wintypes uses host C widths on non-Windows.
_DWORD = C.c_uint32
_USHORT = C.c_uint16
_HANDLE = C.c_void_p
_NTSTATUS = C.c_int32
_LARGE_INTEGER = C.c_int64


class _UnicodeString(C.Structure):
    _fields_ = [("Length", _USHORT), ("MaximumLength", _USHORT), ("Buffer", C.c_void_p)]


class _ObjectAttributes(C.Structure):
    _fields_ = [("Length", _DWORD), ("RootDirectory", _HANDLE),
                ("ObjectName", C.POINTER(_UnicodeString)), ("Attributes", _DWORD),
                ("SecurityDescriptor", C.c_void_p), ("SecurityQualityOfService", C.c_void_p)]


class _StatusUnion(C.Union):
    _fields_ = [("Status", _NTSTATUS), ("Pointer", C.c_void_p)]


class _IoStatus(C.Structure):
    _fields_ = [("result", _StatusUnion), ("Information", C.c_size_t)]


class _BasicInfo(C.Structure):
    _fields_ = [("CreationTime", _LARGE_INTEGER), ("LastAccessTime", _LARGE_INTEGER),
                ("LastWriteTime", _LARGE_INTEGER), ("ChangeTime", _LARGE_INTEGER),
                ("FileAttributes", _DWORD)]


class _StandardInfo(C.Structure):
    _fields_ = [("AllocationSize", _LARGE_INTEGER), ("EndOfFile", _LARGE_INTEGER),
                ("NumberOfLinks", _DWORD), ("DeletePending", C.c_ubyte),
                ("Directory", C.c_ubyte)]


class _AttributeTagInfo(C.Structure):
    _fields_ = [("FileAttributes", _DWORD), ("ReparseTag", _DWORD)]


class _IdInfo(C.Structure):
    _fields_ = [("VolumeSerialNumber", C.c_uint64), ("FileId", C.c_ubyte * 16)]


class _DirectoryEntryHeader(C.Structure):
    # FILE_ID_EXTD_DIR_INFO, excluding its variable WCHAR FileName[].
    _fields_ = [("NextEntryOffset", _DWORD), ("FileIndex", _DWORD),
                ("CreationTime", _LARGE_INTEGER), ("LastAccessTime", _LARGE_INTEGER),
                ("LastWriteTime", _LARGE_INTEGER), ("ChangeTime", _LARGE_INTEGER),
                ("EndOfFile", _LARGE_INTEGER), ("AllocationSize", _LARGE_INTEGER),
                ("FileAttributes", _DWORD), ("FileNameLength", _DWORD),
                ("EaSize", _DWORD), ("ReparsePointTag", _DWORD),
                ("FileId", C.c_ubyte * 16)]


_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_DEVICE = 0x40
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_READ_DATA = 0x1  # Also FILE_LIST_DIRECTORY.
_FILE_READ_ATTRIBUTES = 0x80
_SYNCHRONIZE = 0x100000
_FILE_SHARE_READ = 0x1
_FILE_OPEN = 1
_FILE_SYNCHRONOUS_IO_NONALERT = 0x20
_FILE_OPEN_REPARSE_POINT = 0x200000
_OBJ_CASE_INSENSITIVE = 0x40
_ERROR_NO_MORE_FILES = 18
_STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
_INVALID_HANDLE_VALUE = C.c_void_p(-1).value
_CHUNK = 64 * 1024
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
_RESERVED |= {prefix + suffix for prefix in ("COM", "LPT") for suffix in "123456789¹²³"}
_VOLUME_DEVICE = re.compile(r"\\Device\\HarddiskVolume[0-9]+\Z", re.IGNORECASE)


def _unsupported(message: str) -> OSError:
    return OSError(errno.ENOTSUP, "Windows project reader: " + message)


def _component(name: str) -> str:
    if not isinstance(name, str) or not name or name in (".", ".."):
        raise ValueError("Project paths require nonempty, ordinary components")
    if any(c in '<>:\\/?*|\"' or unicodedata.category(c) == "Cc" for c in name):
        raise ValueError("Project path contains a Windows-special or control character")
    if name[-1] in " .":
        raise ValueError("Project paths cannot have trailing dots or spaces")
    if name.split(".", 1)[0].rstrip(" .").upper() in _RESERVED:
        raise ValueError("Project paths cannot use Windows device names")
    try:
        encoded = name.encode("utf-16-le", errors="strict")
    except UnicodeError as exc:
        raise ValueError("Project path is not valid Unicode") from exc
    if len(encoded) > 510:
        raise ValueError("Project path component exceeds 255 UTF-16 code units")
    return name


def _relative_parts(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or not relative or len(relative) > 4096:
        raise ValueError("Project-relative path must contain 1 to 4096 characters")
    return tuple(_component(part) for part in relative.split("/"))


def _root_parts(path) -> tuple[str, tuple[str, ...]]:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw:
        raise ValueError("Project root must be a nonempty Unicode path")
    raw = raw.replace("/", "\\")
    if raw.startswith("\\\\?\\"):
        if not re.match(r"[A-Za-z]:\\", raw[4:]):
            raise ValueError("Only extended local drive paths are supported")
        raw = raw[4:]
    drive, tail = ntpath.splitdrive(raw)
    if not drive:
        if tail.startswith("\\"):
            raise ValueError("Drive-relative project roots are unsupported")
        # This obtains a location, not an I/O capability. Every resulting
        # component is subsequently opened and inspected without following it.
        cwd_drive, cwd_tail = ntpath.splitdrive(os.getcwd().replace("/", "\\"))
        if not re.fullmatch("[A-Za-z]:", cwd_drive) or not cwd_tail.startswith("\\"):
            raise _unsupported("the current directory is not on a local drive")
        drive, tail = cwd_drive, cwd_tail.rstrip("\\") + "\\" + tail
    if not re.fullmatch("[A-Za-z]:", drive) or not tail.startswith("\\"):
        raise ValueError("Only local drive-absolute project roots are supported")
    try:
        if len((drive + tail).encode("utf-16-le")) > 65520:
            raise ValueError("Project root exceeds the supported native path length")
    except UnicodeError as exc:
        raise ValueError("Project root is not valid Unicode") from exc
    # Preserve dots until traversal: link/../project and missing/../project
    # must not erase an unchecked component. Repeated separators are harmless
    # in a selected drive path, unlike a declared project-relative reference.
    parts = tuple(part for part in tail[1:].split("\\") if part)
    return drive.upper(), tuple(part if part in (".", "..") else _component(part)
                                for part in parts)


def _limit(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(label + " must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class _Metadata:
    identity: tuple[int, bytes]
    size: int
    allocation: int
    links: int
    directory: bool
    attributes: int
    tag: int
    creation: int
    write: int
    change: int


@dataclass(frozen=True)
class _Record:
    data: bytes
    identity: tuple[int, bytes]
    _metadata: _Metadata

    @property
    def observation(self):
        from engine.calendar_fs import SourceObservation
        return SourceObservation("windows", self.identity, self._metadata.size, self._metadata)

    def manifest(self) -> dict[str, int | str]:
        return {"size_bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}


@dataclass(frozen=True)
class _Entry:
    name: str
    file_id: bytes
    directory: bool


class _MissingLeaf(OSError):
    """Only STATUS_OBJECT_NAME_NOT_FOUND on a one-component open."""


class _OwnedHandle:
    def __init__(self, native: _Native, value: int):
        self.native = native
        self._value = value

    @property
    def value(self) -> int:
        if self._value is None:
            raise ValueError("Windows project handle is closed")
        return self._value

    def close(self) -> None:
        value, self._value = self._value, None
        if value is not None:
            self.native.close(value)

    def __enter__(self):
        self.value
        return self

    def __exit__(self, exc_type, exc, tb):
        _close_owned([self], primary=exc)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _close_owned(handles, *, primary=None) -> None:
    """Attempt every consumed owner exactly once, preserving cancellation."""
    active = primary
    failures = []
    for handle in handles:
        try:
            handle.close()
        except BaseException as error:
            failures.append(error)
    if failures:
        candidates = ([] if active is None else [active]) + failures
        failure = next((error for error in candidates if not isinstance(error, Exception)),
                       active if active is not None else failures[0])
        try:
            failure.add_note("Owned Windows handle release is uncertain; all remaining closes were attempted once")
        except BaseException:
            pass  # Optional diagnostics must not replace the selected failure.
        if len(failures) > 1:
            try:
                failure.add_note(str(len(failures)) + " owned-handle close operations reported failure")
            except BaseException:
                pass  # Optional diagnostics must not replace the selected failure.
        for error in failures:
            if error is not failure:
                try:
                    failure.add_note("Handle cleanup failure: " + repr(error))
                except BaseException:
                    pass  # Optional diagnostics must not replace the selected failure.
        cause = next((error for error in failures if error is not failure), None)
        if cause is not None:
            raise failure from cause
        raise failure


class _Native:
    """Small read-only adapter. No CRT descriptor ownership transfer occurs."""

    def __init__(self):
        if os.name != "nt":
            raise _unsupported("native Windows APIs are unavailable on this platform")
        sizes = ((_UnicodeString, 16 if C.sizeof(C.c_void_p) == 8 else 8),
                 (_ObjectAttributes, 48 if C.sizeof(C.c_void_p) == 8 else 24),
                 (_IoStatus, 16 if C.sizeof(C.c_void_p) == 8 else 8),
                 (_BasicInfo, 40), (_StandardInfo, 24), (_IdInfo, 24),
                 (_AttributeTagInfo, 8), (_DirectoryEntryHeader, 88))
        if C.sizeof(C.c_void_p) not in (4, 8) or any(C.sizeof(t) != n for t, n in sizes):
            raise _unsupported("unrecognized ctypes Windows structure layout")
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.ntdll = C.WinDLL("ntdll", use_last_error=True)
        self._bind(self.ntdll, "NtCreateFile", _NTSTATUS,
                   C.POINTER(_HANDLE), _DWORD, C.POINTER(_ObjectAttributes),
                   C.POINTER(_IoStatus), C.c_void_p, _DWORD, _DWORD, _DWORD,
                   _DWORD, C.c_void_p, _DWORD)
        self._bind(self.ntdll, "RtlNtStatusToDosError", _DWORD, _NTSTATUS)
        self._bind(self.kernel, "CloseHandle", C.c_int, _HANDLE)
        self._bind(self.kernel, "SetHandleInformation", C.c_int, _HANDLE, _DWORD, _DWORD)
        self._bind(self.kernel, "GetHandleInformation", C.c_int, _HANDLE, C.POINTER(_DWORD))
        self._bind(self.kernel, "GetFileType", _DWORD, _HANDLE)
        self._bind(self.kernel, "GetFileInformationByHandleEx", C.c_int,
                   _HANDLE, C.c_int, C.c_void_p, _DWORD)
        self._bind(self.kernel, "ReadFile", C.c_int, _HANDLE, C.c_void_p, _DWORD,
                   C.POINTER(_DWORD), C.c_void_p)
        self._bind(self.kernel, "QueryDosDeviceW", _DWORD, C.c_wchar_p, C.c_wchar_p, _DWORD)
        self._bind(self.kernel, "GetVolumeInformationByHandleW", C.c_int, _HANDLE,
                   C.c_wchar_p, _DWORD, C.POINTER(_DWORD), C.POINTER(_DWORD),
                   C.POINTER(_DWORD), C.c_wchar_p, _DWORD)

    @staticmethod
    def _bind(dll, name, restype, *argtypes):
        try:
            function = getattr(dll, name)
        except AttributeError as exc:
            raise _unsupported("required native operation is unavailable: " + name) from exc
        function.restype, function.argtypes = restype, list(argtypes)

    @staticmethod
    def _error(operation, code=None):
        code = C.get_last_error() if code is None else code
        error = C.WinError(code)
        error.strerror = operation + ": " + error.strerror
        return error

    def close(self, handle: int) -> None:
        if not self.kernel.CloseHandle(handle):
            raise self._error("CloseHandle")

    def _open(self, name: str, parent: int | None) -> _OwnedHandle:
        encoded = name.encode("utf-16-le", errors="strict")
        if len(encoded) > 65532:
            raise ValueError("Native object name is too long")
        buffer = C.create_string_buffer(encoded + b"\0\0")
        string = _UnicodeString(len(encoded), len(encoded) + 2, C.addressof(buffer))
        attributes = _ObjectAttributes(C.sizeof(_ObjectAttributes), parent,
                                       C.pointer(string), _OBJ_CASE_INSENSITIVE, None, None)
        result, status_block = _HANDLE(), _IoStatus()
        # No FILE_DIRECTORY_FILE combination and no undocumented use of
        # OBJ_DONT_REPARSE: type-neutral open, then inspect the returned handle.
        status = self.ntdll.NtCreateFile(
            C.byref(result), _FILE_READ_DATA | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
            C.byref(attributes), C.byref(status_block), None, 0, _FILE_SHARE_READ,
            _FILE_OPEN, _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT,
            None, 0)
        if status < 0:
            code = self.ntdll.RtlNtStatusToDosError(status)
            error = self._error("NtCreateFile (NTSTATUS 0x%08x)" % (status & 0xFFFFFFFF), code)
            if status & 0xFFFFFFFF == _STATUS_OBJECT_NAME_NOT_FOUND:
                raise _MissingLeaf(error.errno, error.strerror) from error
            raise error
        if not result.value or result.value == _INVALID_HANDLE_VALUE:
            raise _unsupported("NtCreateFile returned no usable handle")
        owned = _OwnedHandle(self, result.value)
        try:
            if status != 0 or status_block.result.Status != 0:
                raise _unsupported("NtCreateFile did not return a final synchronous success")
            # OBJ_INHERIT was not requested. Verify and explicitly clear it;
            # unsupported noninheritance is an error, never a best effort.
            if not self.kernel.SetHandleInformation(owned.value, 1, 0):
                raise self._error("SetHandleInformation")
            flags = _DWORD()
            if not self.kernel.GetHandleInformation(owned.value, C.byref(flags)):
                raise self._error("GetHandleInformation")
            if flags.value & 1:
                raise _unsupported("native handle is unexpectedly inheritable")
            return owned
        except BaseException as error:
            _close_owned([owned], primary=error)
            raise

    def open_volume(self, drive: str) -> _OwnedHandle:
        buffer = C.create_unicode_buffer(32768)
        if not self.kernel.QueryDosDeviceW(drive, buffer, len(buffer)):
            raise self._error("QueryDosDeviceW")
        # Take the current mapping (first MULTI_SZ string), not a remembered
        # pathname. A SUBST mapping or network redirector is not this anchor.
        device = buffer.value
        if not _VOLUME_DEVICE.fullmatch(device):
            raise _unsupported("only direct local HarddiskVolume drive mappings are supported")
        handle = self._open(device + "\\", None)
        try:
            fs_name, label = C.create_unicode_buffer(256), C.create_unicode_buffer(256)
            serial, component_length, flags = _DWORD(), _DWORD(), _DWORD()
            if not self.kernel.GetVolumeInformationByHandleW(
                    handle.value, label, len(label), C.byref(serial),
                    C.byref(component_length), C.byref(flags), fs_name, len(fs_name)):
                raise self._error("GetVolumeInformationByHandleW")
            if fs_name.value.upper() != "NTFS":
                raise _unsupported("this candidate supports only local NTFS volumes")
            return handle
        except BaseException as error:
            _close_owned([handle], primary=error)
            raise

    def open_relative(self, parent: int, component: str) -> _OwnedHandle:
        return self._open(_component(component), parent)

    def _query(self, handle: int, info_class: int, structure):
        result = structure()
        if not self.kernel.GetFileInformationByHandleEx(
                handle, info_class, C.byref(result), C.sizeof(result)):
            raise self._error("GetFileInformationByHandleEx(%d)" % info_class)
        return result

    def inspect(self, handle: int) -> _Metadata:
        if self.kernel.GetFileType(handle) != 1:  # FILE_TYPE_DISK
            raise ValueError("Project inputs must be disk files or directories")
        basic = self._query(handle, 0, _BasicInfo)
        standard = self._query(handle, 1, _StandardInfo)
        tag = self._query(handle, 9, _AttributeTagInfo)
        identity = self._query(handle, 18, _IdInfo)
        if not any(identity.FileId):
            raise _unsupported("the filesystem returned an unusable zero file identity")
        attributes = int(tag.FileAttributes)
        if attributes != basic.FileAttributes:
            raise ValueError("Project object attributes changed while being inspected")
        if attributes & (_FILE_ATTRIBUTE_REPARSE_POINT | _FILE_ATTRIBUTE_DEVICE) or tag.ReparseTag:
            raise ValueError("Project reparse points and device objects are unsupported")
        if standard.DeletePending:
            raise ValueError("Project object is pending deletion")
        directory = bool(standard.Directory)
        if directory != bool(attributes & _FILE_ATTRIBUTE_DIRECTORY):
            raise ValueError("Project object type metadata is inconsistent")
        if standard.EndOfFile < 0 or standard.AllocationSize < 0 or standard.NumberOfLinks < 1:
            raise ValueError("Project object has unsupported size or link metadata")
        return _Metadata((int(identity.VolumeSerialNumber), bytes(identity.FileId)),
                         int(standard.EndOfFile), int(standard.AllocationSize),
                         int(standard.NumberOfLinks), directory, attributes, int(tag.ReparseTag),
                         int(basic.CreationTime), int(basic.LastWriteTime), int(basic.ChangeTime))

    def read(self, handle: int, amount: int) -> bytes:
        if not 0 < amount <= _CHUNK:
            raise ValueError("Invalid native read size")
        buffer, count = C.create_string_buffer(amount), _DWORD()
        if not self.kernel.ReadFile(handle, buffer, amount, C.byref(count), None):
            raise self._error("ReadFile")
        if count.value > amount:
            raise _unsupported("ReadFile returned an invalid byte count")
        return buffer.raw[:count.value]

    def entries(self, handle: int, expected: set[str] | None) -> dict[str, _Entry]:
        """Fixed-buffer native enumeration; None selects filtered-inventory discovery.

        Exact all-files checks bound entry count by the declared tree. Filtered
        YAML discovery must examine all actual entries to find nested YAML;
        it retains per-directory names but never reads ignored file contents.
        """
        found: dict[str, _Entry] = {}
        dots: set[str] = set()
        first = True
        examined = 0
        while True:
            buffer = C.create_string_buffer(_CHUNK)
            info_class = 20 if first else 19  # Restart, then continue.
            first = False
            if not self.kernel.GetFileInformationByHandleEx(handle, info_class, buffer, len(buffer)):
                code = C.get_last_error()
                if code == _ERROR_NO_MORE_FILES:
                    break
                raise self._error("GetFileInformationByHandleEx(FileIdExtdDirectoryInfo)", code)
            raw = buffer.raw
            offset = 0
            while True:
                header_size = C.sizeof(_DirectoryEntryHeader)
                if offset + header_size > len(buffer):
                    raise _unsupported("truncated directory entry")
                header = _DirectoryEntryHeader.from_buffer_copy(raw, offset)
                size = int(header.FileNameLength)
                end = offset + header_size + size
                if not size or size % 2 or size > 510 or end > len(buffer):
                    raise _unsupported("invalid directory entry length")
                try:
                    name = raw[offset + header_size:end].decode("utf-16-le", errors="strict")
                except UnicodeError as exc:
                    raise _unsupported("invalid Unicode in directory enumeration") from exc
                examined += 1
                if expected is not None and examined > len(expected) + 2:
                    raise ValueError("Project directory changed or has unexpected entries")
                if name in (".", ".."):
                    if name in dots:
                        raise ValueError("Repeated directory enumeration entry")
                    dots.add(name)
                else:
                    _component(name)
                    if (expected is not None and name not in expected) or name in found:
                        raise ValueError("Unexpected or repeated project entry: " + name)
                    if (header.FileAttributes & (_FILE_ATTRIBUTE_REPARSE_POINT | _FILE_ATTRIBUTE_DEVICE)
                            or header.ReparsePointTag):
                        raise ValueError("Project directory contains a reparse or special entry")
                    found[name] = _Entry(name, bytes(header.FileId),
                                         bool(header.FileAttributes & _FILE_ATTRIBUTE_DIRECTORY))
                step = int(header.NextEntryOffset)
                if step == 0:
                    break
                if step % 8 or step < header_size + size or offset + step >= len(buffer):
                    raise _unsupported("invalid next directory entry offset")
                offset += step
        if expected is not None and set(found) != expected:
            raise ValueError("Project directory has missing declared entries")
        return found


def _bounded_bytes(read, limit: int) -> bytes:
    """The actual read allowance is limit + 1 even if metadata lies/grows."""
    data = bytearray()
    while len(data) <= limit:
        amount = min(_CHUNK, limit + 1 - len(data))
        block = read(amount)
        if not isinstance(block, bytes) or len(block) > amount:
            raise OSError(errno.EIO, "Invalid bounded native read result")
        if not block:
            break
        data.extend(block)
    if len(data) > limit:
        raise ValueError("Project input exceeds its byte limit")
    return bytes(data)


class WindowsProjectDirectory:
    """Retained-root reader; closing the context releases every owned handle."""

    def __init__(self, path, *, file_limit: int):
        self.file_limit = _limit(file_limit, "file_limit")
        self._anchors: list[tuple[_OwnedHandle, tuple[int, bytes]]] = []
        self._native = _Native()
        drive, parts = _root_parts(path)
        try:
            volume = self._native.open_volume(drive)
            try:
                state = self._native.inspect(volume.value)
                if not state.directory:
                    raise ValueError("Project volume root is not a directory")
                self._volume = state.identity[0]
                self._anchors.append((volume, state.identity))
            except BaseException as error:
                _close_owned([volume], primary=error)
                raise
            for component in parts:
                if component == ".":
                    continue
                if component == "..":
                    # The logical parent is the already retained directory,
                    # never a live '..' lookup after a possible directory move.
                    # Preceding ordinary components have already been checked.
                    if len(self._anchors) > 1:
                        popped, _ = self._anchors.pop()
                        popped.close()
                    continue
                child, state = self._open_child(self._anchors[-1][0], component, directory=True)
                self._anchors.append((child, state.identity))
        except BaseException as error:
            self.close(_primary=error)
            raise

    def __enter__(self):
        try:
            self._check_anchors()
        except BaseException as error:
            # Python does not call __exit__ when __enter__ fails. Explicitly
            # release every constructor-acquired handle while self is alive.
            self.close(_primary=error)
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close(_primary=exc)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def close(self, *, _primary=None) -> None:
        anchors, self._anchors = self._anchors, []
        _close_owned((handle for handle, _ in reversed(anchors)), primary=_primary)

    def _require(self, state: _Metadata, *, directory: bool) -> None:
        if state.identity[0] != self._volume:
            raise ValueError("Project objects cannot cross the pinned volume")
        if state.directory != directory:
            raise ValueError("Project object is not the required regular file/directory type")
        if not directory and state.links != 1:
            raise ValueError("Multiply-linked project files are rejected by the private candidate policy")

    def _check_anchors(self) -> None:
        if not self._anchors:
            raise ValueError("Windows project directory is closed")
        for handle, identity in self._anchors:
            state = self._native.inspect(handle.value)
            self._require(state, directory=True)
            if state.identity != identity:
                raise ValueError("Project ancestor identity changed")

    def _open_child(self, parent: _OwnedHandle, component: str, *,
                    directory: bool) -> tuple[_OwnedHandle, _Metadata]:
        before = self._native.inspect(parent.value)
        self._require(before, directory=True)
        try:
            handle = self._native.open_relative(parent.value, component)
        except BaseException:
            if self._native.inspect(parent.value) != before:
                raise ValueError("Project directory changed during a failed child open")
            raise
        try:
            state = self._native.inspect(handle.value)
            self._require(state, directory=directory)
            if self._native.inspect(parent.value) != before:
                raise ValueError("Project directory changed while a child was opened")
            return handle, state
        except BaseException as error:
            _close_owned([handle], primary=error)
            raise

    def read(self, relative: str, *, optional: bool = False, budget: int | None = None):
        parts = _relative_parts(relative)
        limit = self.file_limit if budget is None else min(self.file_limit, _limit(budget, "budget"))
        self._check_anchors()
        parent = self._anchors[-1][0]
        opened: list[tuple[_OwnedHandle, _Metadata]] = []
        primary = None
        try:
            for component in parts[:-1]:
                parent, state = self._open_child(parent, component, directory=True)
                opened.append((parent, state))
            try:
                leaf, before = self._open_child(parent, parts[-1], directory=False)
            except _MissingLeaf:
                if not optional:
                    raise
                # Recheck all acquired ancestors even for an absent optional
                # leaf. Other errors and missing intermediate dirs propagate.
                for directory, state in opened:
                    if self._native.inspect(directory.value) != state:
                        raise ValueError("Project directory changed during optional input lookup")
                self._check_anchors()
                return None
            with leaf:
                if before.size > limit:
                    raise ValueError("Project input exceeds its byte limit")
                data = _bounded_bytes(lambda amount: self._native.read(leaf.value, amount), limit)
                after = self._native.inspect(leaf.value)
                self._require(after, directory=False)
                if before != after or len(data) != before.size:
                    raise ValueError("Project input changed during its read")
                result = _Record(data, before.identity, before)
            for directory, state in opened:
                if self._native.inspect(directory.value) != state:
                    raise ValueError("Project directory changed during its read")
            self._check_anchors()
            return result
        except BaseException as error:
            primary = error
            raise
        finally:
            _close_owned((handle for handle, _ in reversed(opened)), primary=primary)

    def exact_tree(self, files: Mapping[str, _Record], version: _Record) -> None:
        manifest = "execution-version.json"
        if manifest in files:
            raise ValueError("Version manifest cannot also be a declared input")
        self.exact_entries({**files, manifest: version})

    def exact_entries(self, observations, *, policy="all") -> None:
        from engine.calendar_fs import _expected_observations
        expected = _expected_observations(observations, "windows", policy)
        self._check_anchors()
        observed = set()
        directories: dict[tuple[str, ...], dict[str, bool]] = {(): {}}
        identities = set()
        for relative, record in expected.items():
            parts = _relative_parts(relative)
            if record.native_identity in identities:
                raise ValueError("Declared project files have duplicate native identities")
            identities.add(record.native_identity)
            for depth, component in enumerate(parts):
                parent = parts[:depth]
                is_directory = depth < len(parts) - 1
                children = directories.setdefault(parent, {})
                if component in children and children[component] != is_directory:
                    raise ValueError("A declared file is also used as a directory")
                children[component] = is_directory
                if is_directory:
                    directories.setdefault(parts[:depth + 1], {})

        # Iterative DFS keeps only the active ancestry open. The all-files
        # policy rejects unexpected subtrees; YAML policy must inspect them.
        stack = []
        pending = ((), self._anchors[-1][0], False)
        primary = None
        try:
            while pending is not None or stack:
                if pending is not None:
                    path, handle, owned = pending
                    pending = None
                    try:
                        before = self._native.inspect(handle.value)
                        self._require(before, directory=True)
                        entries = self._native.entries(handle.value,
                            set(directories[path]) if policy == "all" else None)
                        if self._native.inspect(handle.value) != before:
                            raise ValueError("Project directory changed during enumeration")
                        stack.append((path, handle, owned, before, entries, iter(sorted(entries))))
                    except BaseException as error:
                        if owned:
                            _close_owned([handle], primary=error)
                        raise
                path, handle, owned, before, entries, names = stack[-1]
                name = next(names, None)
                if name is None:
                    if self._native.inspect(handle.value) != before:
                        raise ValueError("Project directory changed during tree validation")
                    stack.pop()
                    if owned:
                        handle.close()
                    continue
                directory = directories[path][name] if policy == "all" else entries[name].directory
                entry = entries[name]
                if entry.directory != directory:
                    raise ValueError("Project entry has the wrong type: " + name)
                child, state = self._open_child(handle, name, directory=directory)
                child_error = None
                try:
                    if state.identity != (self._volume, entry.file_id):
                        raise ValueError("Project entry identity changed after enumeration")
                    if directory:
                        pending = (path + (name,), child, True)
                        child = None  # Ownership transferred to pending/stack.
                    else:
                        relative = "/".join(path + (name,))
                        if policy == "all" or relative.endswith(".yaml"):
                            record = expected.get(relative)
                            if (record is None or state.identity != record.native_identity
                                    or state.size != record.size_bytes
                                    or state != record.post_read_metadata):
                                raise ValueError("Project file metadata changed or inventory differs after collection")
                            observed.add(relative)
                except BaseException as error:
                    child_error = error
                    raise
                finally:
                    if child is not None:
                        _close_owned([child], primary=child_error)
            self._check_anchors()
            if observed != set(expected):
                raise ValueError("Project directory has missing declared entries")
        except BaseException as error:
            primary = error
            raise
        finally:
            remaining = ([pending[1]] if pending is not None and pending[2] else [])
            remaining.extend(frame[1] for frame in reversed(stack) if frame[2])
            _close_owned(remaining, primary=primary)
