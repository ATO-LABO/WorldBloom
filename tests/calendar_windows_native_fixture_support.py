"""Windows-only operations for bounded, owned snapshot-test fixtures.

Import is safe on other OSes. No installation, privilege/ACL change, external
target, or fallback after a denied native operation. This is test code only.
"""
from __future__ import annotations

import ctypes as C
import json
import os
from pathlib import Path
import struct
import sys

# Dedicated fixture subprocess: resolve sibling engine modules from this tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


MOUNT_POINT = 0xA0000003
SYMLINK = 0xA000000C
SET_REPARSE = 0x000900A4
DELETE_REPARSE = 0x000900AC
READ_ATTRIBUTES = 0x80
WRITE_ATTRIBUTES = 0x100
SHARE_ALL = 7


class NativeFixtureOps:
    def __init__(self, root, token):
        if os.name != "nt":
            raise OSError("Native fixture operations require Windows")
        self.root = Path(root).absolute()
        if not self.root.name.startswith("worldbloom-windows-gate-"):
            raise ValueError("Only a dedicated test fixture root is allowed")
        if (self.root / ".fixture-owner").read_text(encoding="ascii") != token:
            raise ValueError("Fixture ownership token mismatch")
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self._bind("CreateFileW", C.c_void_p, C.c_wchar_p, C.c_uint32, C.c_uint32,
                   C.c_void_p, C.c_uint32, C.c_uint32, C.c_void_p)
        self._bind("CloseHandle", C.c_int, C.c_void_p)
        self._bind("DeviceIoControl", C.c_int, C.c_void_p, C.c_uint32,
                   C.c_void_p, C.c_uint32, C.c_void_p, C.c_uint32,
                   C.POINTER(C.c_uint32), C.c_void_p)
        self._bind("GetHandleInformation", C.c_int, C.c_void_p, C.POINTER(C.c_uint32))
        self._bind("GetCurrentProcess", C.c_void_p)
        self._bind("GetProcessHandleCount", C.c_int, C.c_void_p, C.POINTER(C.c_uint32))
        self._bind("GetFinalPathNameByHandleW", C.c_uint32, C.c_void_p, C.c_wchar_p,
                   C.c_uint32, C.c_uint32)

    def _bind(self, name, result, *args):
        function = getattr(self.kernel, name)
        function.restype, function.argtypes = result, list(args)

    def path(self, relative):
        if not isinstance(relative, str) or not relative:
            raise ValueError("Relative fixture path required")
        parts = relative.split("/")
        if any(p in ("", ".", "..") or any(c in p for c in "\\:\0") for p in parts):
            raise ValueError("Fixture paths cannot leave their owned root")
        return self.root.joinpath(*parts)

    def open(self, relative, access, *, sharing=SHARE_ALL, directory=False):
        flags = 0x00200000 | (0x02000000 if directory else 0)  # no-follow; directory semantics
        handle = self.kernel.CreateFileW(str(self.path(relative)), access, sharing, None, 3, flags, None)
        if handle in (None, C.c_void_p(-1).value):
            raise C.WinError(C.get_last_error())
        return handle

    def close(self, handle):
        if not self.kernel.CloseHandle(handle):
            raise C.WinError(C.get_last_error())

    def control(self, handle, code, data):
        buffer, count = C.create_string_buffer(data), C.c_uint32()
        if not self.kernel.DeviceIoControl(handle, code, buffer, len(data), None, 0,
                                           C.byref(count), None):
            raise C.WinError(C.get_last_error())

    def set_reparse(self, handle, target_relative, *, tag=MOUNT_POINT):
        target = str(self.path(target_relative))
        substitute = ("\\??\\" + target).encode("utf-16-le")
        printable = target.encode("utf-16-le")
        paths = substitute + b"\0\0" + printable + b"\0\0"
        lengths = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printable))
        payload = lengths + (struct.pack("<I", 0) if tag == SYMLINK else b"") + paths
        self.control(handle, SET_REPARSE, struct.pack("<IHH", tag, len(payload), 0) + payload)

    def delete_reparse(self, handle, *, tag=MOUNT_POINT):
        self.control(handle, DELETE_REPARSE, struct.pack("<IHH", tag, 0, 0))

    def remove_own_reparse(self, relative, *, tag=MOUNT_POINT, directory=True):
        handle = self.open(relative, READ_ATTRIBUTES | WRITE_ATTRIBUTES, directory=directory)
        try:
            self.delete_reparse(handle, tag=tag)
        finally:
            self.close(handle)

    def handle_flags(self, handle):
        flags = C.c_uint32()
        if not self.kernel.GetHandleInformation(handle, C.byref(flags)):
            raise C.WinError(C.get_last_error())
        return flags.value

    def handle_count(self):
        count = C.c_uint32()
        if not self.kernel.GetProcessHandleCount(self.kernel.GetCurrentProcess(), C.byref(count)):
            raise C.WinError(C.get_last_error())
        return count.value

    def final_path_for_observation(self, handle):
        # Test evidence only. Never used to reopen or mutate an object.
        buffer = C.create_unicode_buffer(32768)
        length = self.kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not length:
            raise C.WinError(C.get_last_error())
        if length >= len(buffer):
            raise OSError("Fixture handle pathname observation exceeded its bound")
        return buffer.value


def _result(exc=None, **values):
    if exc is not None:
        return {"ok": False, "type": type(exc).__name__, "message": str(exc),
                "winerror": getattr(exc, "winerror", None), **values}
    return {"ok": True, **values}


def _emit(value):
    print(json.dumps(value), flush=True)


def _go():
    if sys.stdin.readline().strip() != "go":
        raise RuntimeError("Fixture worker did not receive its bounded start signal")


def run_worker(config):
    ops = NativeFixtureOps(config["root"], config["token"])
    mode = config["mode"]
    if mode == "spool_stages":
        from engine.calendar_windows_spool import WindowsPrivateSpool
        spool = WindowsPrivateSpool(parent=ops.path(config["parent"]))
        try:
            _emit({"stage": "spool_created", "leaf": spool._leaf}); _go()
            spool.append(b"owned disposable spool data")
            _emit({"stage": "spool_appended", "leaf": spool._leaf}); _go()
            spool.seal()
            _emit({"stage": "spool_sealed", "leaf": spool._leaf}); _go()
        finally:
            spool.close()
        _emit({"stage": "spool_released", "leaf": spool._leaf}); _go()
        return
    if mode == "probe_handle":
        class IdInfo(C.Structure):
            _fields_ = [("volume", C.c_uint64), ("file_id", C.c_ubyte * 16)]
        function = ops.kernel.GetFileInformationByHandleEx
        function.argtypes, function.restype = [C.c_void_p, C.c_int, C.c_void_p, C.c_uint32], C.c_int
        _emit({"stage": "ready"}); _go()
        info = IdInfo()
        handle = int(config["handle"])
        if not 0 < handle < (1 << (8 * C.sizeof(C.c_void_p))):
            raise ValueError("Invalid bounded fixture handle number")
        success = function(handle, 18, C.byref(info), C.sizeof(info))
        _emit(_result(valid=bool(success), winerror=None if success else C.get_last_error(),
                      volume=int(info.volume), file_id=bytes(info.file_id).hex()))
        return
    if mode == "writer_stages":
        from engine.calendar_windows_writer import WindowsDirectSnapshot
        import hashlib
        data = bytes.fromhex(config["data"])
        if len(data) > 65536:
            raise ValueError("Process-interruption fixture is limited to 64 KiB")
        writer = WindowsDirectSnapshot(ops.path(config["parent"]), config["leaf"],
                                       expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest(),
                                       max_bytes=len(data))
        with writer:
            _emit({"stage": "created"}); _go()
            split = max(1, len(data) // 2)
            writer.write_all([data[:split]])
            _emit({"stage": "partial"}); _go()
            writer.write_all([data[split:]])
            _emit({"stage": "bytes_complete"}); _go()
            writer.seal()
            _emit({"stage": "sealed"}); _go()
        _emit({"stage": "released"}); _go()
        return

    held = None
    if mode in ("set_junction", "junction_aba"):
        held = ops.open(config["path"], READ_ATTRIBUTES | WRITE_ATTRIBUTES, directory=True)
    _emit({"stage": "ready"})
    _go()
    try:
        if mode == "file":
            with ops.path(config["path"]).open("xb") as stream:
                stream.write(b"competitor sentinel")
        elif mode == "directory":
            path = ops.path(config["path"])
            path.mkdir()
            (path / "keep").write_bytes(b"competitor sentinel")
        elif mode == "junction":
            ops.path(config["path"]).mkdir()
            handle = ops.open(config["path"], READ_ATTRIBUTES | WRITE_ATTRIBUTES, directory=True)
            try:
                ops.set_reparse(handle, config["target"])
            finally:
                ops.close(handle)
        elif mode == "rename":
            ops.path(config["path"]).rename(ops.path(config["target"]))
        elif mode == "set_junction":
            ops.set_reparse(held, config["target"])
        elif mode == "junction_aba":
            loops = config.get("loops", 1)
            if type(loops) is not int or not 1 <= loops <= 8:
                raise ValueError("Fixture mutation loops must be bounded by eight")
            for _ in range(loops):
                ops.set_reparse(held, config["target"])
                ops.delete_reparse(held)
        else:
            raise ValueError("Unknown bounded fixture operation")
        _emit(_result())
    except Exception as exc:
        _emit(_result(exc))
    finally:
        if held is not None:
            ops.close(held)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("One bounded fixture JSON argument is required")
        run_worker(json.loads(sys.argv[1]))
    except Exception as error:
        _emit(_result(error))
        raise SystemExit(1)
