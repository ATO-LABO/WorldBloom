"""Shared app/worker lifetime leases and a nonblocking exclusive update lease."""
from contextlib import contextmanager
from pathlib import Path
import os

from .contract import is_link
from .errors import DeliveryError


@contextmanager
def lease(path, *, exclusive):
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        if is_link(part):
            raise DeliveryError("linked_lease")
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            import msvcrt

            class Overlapped(ctypes.Structure):
                _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                            ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                            ("hEvent", wintypes.HANDLE)]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
            kernel.LockFileEx.restype = wintypes.BOOL
            kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.DWORD, ctypes.POINTER(Overlapped)]
            kernel.UnlockFileEx.restype = wintypes.BOOL
            overlap = Overlapped()
            handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
            flags = 1 | (2 if exclusive else 0)  # FAIL_IMMEDIATELY; optional EXCLUSIVE.
            if not kernel.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlap)):
                code = ctypes.get_last_error()
                if code in (32, 33, 997):
                    raise DeliveryError("in_use")
                raise DeliveryError("lease_error", str(code))
            try:
                yield
            finally:
                if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap)):
                    raise DeliveryError("lease_unlock_error", str(ctypes.get_last_error()))
        else:
            import fcntl
            try:
                fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DeliveryError("in_use") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
    finally:
        stream.close()
