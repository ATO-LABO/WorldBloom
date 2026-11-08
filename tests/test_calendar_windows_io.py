"""Windows reader candidate tests, not evidence of native execution on Linux.

Pure checks exercise syntax/budget/ownership logic only. Every NativeWindowsReaderTests
test uses actual Windows handles and temporary fixture files. Deterministic
race hooks only arrange an operation boundary; they do not mock an NT result.
Capability skips explicitly say UNTESTED and never hide hardlink tests behind
symlink availability. Run on actual Windows/Python 3.12 and 3.13, then test the
packaged EXE separately. An all-skipped native section is not a security pass.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine import calendar_windows_io as win


class PureWindowsReaderContractTests(unittest.TestCase):
    """Platform-independent logic only, never native Windows coverage."""

    def test_relative_names_reject_aliases_and_traversal(self):
        for name in ("", "/world", "../world", "a/../world", "./world", "a//world",
                     "a/", "C:world", "a\\world", "world:stream", "NUL", "con.yaml",
                     "AUX.txt", "COM1.yaml", "LPT9", "COM¹.txt", "LPT².txt", "CONIN$",
                     "CONOUT$", "CON .yaml", "world.", "world ", "a\0b", "a\x1fb",
                     "a\x85b", "a?b", "a*b", 'a"b', "a<b", "a>b", "a|b",
                     "\ud800", "a" * 256, "😀" * 128, "a/" * 2048 + "z"):
            with self.subTest(name=repr(name)), self.assertRaises(ValueError):
                win._relative_parts(name)
        self.assertEqual(win._relative_parts("資料/世界🌸.yaml"), ("資料", "世界🌸.yaml"))
        self.assertEqual(win._relative_parts("com10/world.yaml"), ("com10", "world.yaml"))
        self.assertEqual(win._relative_parts("😀" * 127 + "a"), ("😀" * 127 + "a",))

    def test_selected_roots_preserve_dots_until_handle_traversal(self):
        with patch.object(os, "getcwd", return_value="C:\\work\\nested"):
            self.assertEqual(win._root_parts("../projects"),
                             ("C:", ("work", "nested", "..", "projects")))
            self.assertEqual(win._root_parts("."), ("C:", ("work", "nested", ".")))
            self.assertEqual(win._root_parts("C:\\work\\..\\project"),
                             ("C:", ("work", "..", "project")))
            self.assertEqual(win._root_parts("C:\\missing\\..\\project"),
                             ("C:", ("missing", "..", "project")))
            self.assertEqual(win._root_parts("\\\\?\\C:\\資料\\世界🌸"),
                             ("C:", ("資料", "世界🌸")))
            for name in ("C:project", "\\project", "\\\\host\\share\\project",
                         "\\\\.\\C:\\project", "\\\\?\\UNC\\host\\share\\project",
                         "C:\\NUL\\project", "C:\\project.\\world"):
                with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                    win._root_parts(name)

    def test_bounded_reader_caps_actual_consumption_even_if_source_grows(self):
        requests = []
        def growing_source(amount):
            requests.append(amount)
            return b"x" * amount
        with self.assertRaises(ValueError):
            win._bounded_bytes(growing_source, win._CHUNK + 7)
        self.assertEqual(sum(requests), win._CHUNK + 8)
        self.assertTrue(all(0 < n <= win._CHUNK for n in requests))

    def test_volume_anchor_rejects_subst_and_redirector_spellings(self):
        self.assertIsNotNone(win._VOLUME_DEVICE.fullmatch(r"\Device\HarddiskVolume3"))
        for name in (r"\??\C:\work", r"\Device\Mup\server\share",
                     r"\Device\HarddiskVolume3\directory", r"\Device\HarddiskVolume3suffix"):
            with self.subTest(name=name):
                self.assertIsNone(win._VOLUME_DEVICE.fullmatch(name))

    def test_bounded_reader_handles_empty_short_reads_and_zero_budget(self):
        source = iter((b"ab", b"c", b""))
        self.assertEqual(win._bounded_bytes(lambda _: next(source), 3), b"abc")
        self.assertEqual(win._bounded_bytes(lambda _: b"", 0), b"")
        with self.assertRaises(ValueError):
            win._bounded_bytes(lambda _: b"x", 0)
        with self.assertRaises(OSError):
            win._bounded_bytes(lambda _: b"excess", 0)
        for value in (True, -1, 0.5, "1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                win._limit(value, "budget")

    def test_record_is_immutable_and_hashes_original_binary_bytes(self):
        state = win._Metadata((7, b"i" * 16), 3, 4096, 1, False, 32, 0, 1, 2, 3)
        record = win._Record(b"x\r\n", state.identity, state)
        self.assertEqual(record.manifest(), {"size_bytes": 3,
                         "sha256": hashlib.sha256(b"x\r\n").hexdigest()})
        with self.assertRaises(FrozenInstanceError):
            record.data = b"new"

    def test_failed_context_entry_closes_all_owned_handles_without_gc(self):
        # Ownership-only fake: this test makes no native Windows claim.
        closed = []
        class Owner:
            def close(self, handle):
                closed.append(handle)
        reader = object.__new__(win.WindowsProjectDirectory)
        owner = Owner()
        reader._anchors = [(win._OwnedHandle(owner, n), (1, bytes([n]) * 16))
                           for n in (10, 11, 12)]
        class EntryFailure(BaseException):
            pass
        failure = EntryFailure("anchor validation failed")
        def fail():
            raise failure
        reader._check_anchors = fail
        with self.assertRaises(EntryFailure) as caught:
            reader.__enter__()
        self.assertIs(caught.exception, failure)
        self.assertEqual(closed, [12, 11, 10])
        self.assertEqual(reader._anchors, [])
        reader.close()
        self.assertEqual(closed, [12, 11, 10])

    def test_root_stack_ownership_and_constructor_failure_without_native_claim(self):
        class FakeNative:
            def __init__(self):
                self.states, self.opened, self.closed = {}, [], []
            def make(self, parent, component):
                if component == "missing":
                    raise FileNotFoundError(component)
                if component in (".", ".."):
                    raise AssertionError("A dot was passed to the native open")
                value = len(self.states) + 1
                self.states[value] = win._Metadata((7, bytes([value]) * 16), 0, 0,
                                                   1, True, 16, 0, 1, 1, 1)
                self.opened.append((parent, component, value))
                return win._OwnedHandle(self, value)
            def open_volume(self, drive):
                return self.make(None, drive)
            def open_relative(self, parent, component):
                return self.make(parent, component)
            def inspect(self, handle):
                return self.states[handle]
            def close(self, handle):
                self.closed.append(handle)
        native = FakeNative()
        with patch.object(win, "_Native", return_value=native):
            with win.WindowsProjectDirectory(r"C:\one\inner\..\.\two", file_limit=8) as reader:
                self.assertEqual(native.opened, [(None, "C:", 1), (1, "one", 2),
                                                 (2, "inner", 3), (2, "two", 4)])
                self.assertEqual(native.closed, [3])
                self.assertEqual([h.value for h, _ in reader._anchors], [1, 2, 4])
        self.assertEqual(native.closed, [3, 4, 2, 1])
        native = FakeNative()
        with patch.object(win, "_Native", return_value=native):
            with self.assertRaises(FileNotFoundError):
                win.WindowsProjectDirectory(r"C:\one\missing\..\two", file_limit=8)
        self.assertEqual(native.opened, [(None, "C:", 1), (1, "one", 2)])
        self.assertEqual(native.closed, [2, 1])
        native = FakeNative()
        with patch.object(win, "_Native", return_value=native):
            with win.WindowsProjectDirectory(r"C:\..\..\one", file_limit=8):
                self.assertEqual(native.opened, [(None, "C:", 1), (1, "one", 2)])
        self.assertEqual(native.closed, [2, 1])

    def _cleanup_fake_reader(self, failures, *, fail_deep_entries=False):
        class Native:
            def __init__(self):
                self.attempts = []
                self.read_once = False
                self.states = {number: win._Metadata((1, bytes([number]) * 16),
                    1 if number >= 4 else 0, 4096, 1, number < 4,
                    32 if number >= 4 else 16, 0, 1, 1, 1) for number in range(1, 6)}
            def inspect(self, handle): return self.states[handle]
            def open_relative(self, parent, component):
                number = {(1, 'a'): 2, (2, 'b'): 3, (3, 'file'): 4,
                          (1, 'execution-version.json'): 5}[(parent, component)]
                return win._OwnedHandle(self, number)
            def read(self, handle, amount):
                if self.read_once: return b''
                self.read_once = True
                return b'x'
            def entries(self, handle, expected):
                if handle == 3 and fail_deep_entries: raise ValueError('fixture tree failure')
                entries = {1: {'a': 2, 'execution-version.json': 5}, 2: {'b': 3}, 3: {'file': 4}}[handle]
                return {name: win._Entry(name, self.states[number].identity[1], number < 4)
                        for name, number in entries.items()}
            def close(self, handle):
                self.attempts.append(handle)
                if handle in failures: raise failures[handle]
        native = Native()
        reader = object.__new__(win.WindowsProjectDirectory)
        reader.file_limit = 8; reader._volume = 1; reader._native = native
        reader._anchors = [(win._OwnedHandle(native, 1), native.states[1].identity)]
        return reader, native

    def test_read_cancellation_cleanup_attempts_all_intermediate_handles(self):
        cancel = KeyboardInterrupt('read cleanup cancel')
        reader, native = self._cleanup_fake_reader({3: cancel, 2: OSError('later close failed')})
        with self.assertRaises(KeyboardInterrupt) as caught:
            reader.read('a/b/file')
        self.assertIs(caught.exception, cancel)
        self.assertEqual(native.attempts, [4, 3, 2])
        reader.close(); reader.close()
        self.assertEqual(native.attempts, [4, 3, 2, 1])

    def test_exact_tree_cleanup_preserves_cancellation_and_attempts_ancestors(self):
        cancel = KeyboardInterrupt('tree cleanup cancel')
        reader, native = self._cleanup_fake_reader({3: cancel, 2: OSError('ancestor close failed')}, fail_deep_entries=True)
        files = {'a/b/file': win._Record(b'x', native.states[4].identity, native.states[4])}
        version = win._Record(b'x', native.states[5].identity, native.states[5])
        with self.assertRaises(KeyboardInterrupt) as caught:
            reader.exact_tree(files, version)
        self.assertIs(caught.exception, cancel)
        self.assertEqual(native.attempts, [3, 2])
        self.assertTrue(any('release is uncertain' in note for note in cancel.__notes__))
        reader.close(); reader.close()
        self.assertEqual(native.attempts, [3, 2, 1])

    def _assert_exhaustive_close(self, failures, expected):
        # Ownership/error ordering fake only; no native release guarantee.
        attempts = []
        class Owner:
            def close(self, handle):
                attempts.append(handle)
                if handle in failures:
                    raise failures[handle]
        reader = object.__new__(win.WindowsProjectDirectory)
        native = Owner()
        handles = [win._OwnedHandle(native, number) for number in (10, 11, 12)]
        reader._anchors = [(handle, (1, b'i' * 16)) for handle in handles]
        with self.assertRaises(type(expected)) as caught:
            reader.close()
        self.assertIs(caught.exception, expected)
        self.assertEqual(attempts, [12, 11, 10])
        self.assertEqual(reader._anchors, [])
        self.assertTrue(all(handle._value is None for handle in handles))
        self.assertTrue(any('release is uncertain' in note for note in expected.__notes__))
        reader.close()
        for handle in handles: handle.close()
        self.assertEqual(attempts, [12, 11, 10])

    def test_close_ordinary_error_then_cancellation_attempts_all_once(self):
        cancel = KeyboardInterrupt('fixture cancellation')
        self._assert_exhaustive_close({12: OSError('first close failed'), 11: cancel}, cancel)

    def test_close_cancellation_then_ordinary_error_attempts_all_once(self):
        cancel = KeyboardInterrupt('fixture cancellation')
        self._assert_exhaustive_close({12: cancel, 11: OSError('later close failed')}, cancel)

    def test_close_multiple_ordinary_errors_preserves_first_without_retry(self):
        first = OSError('first close failed')
        self._assert_exhaustive_close({12: first, 11: OSError('later close failed')}, first)

    @unittest.skipIf(os.name == "nt", "Non-Windows fail-closed contract only")
    def test_native_backend_never_falls_back_on_non_windows(self):
        with self.assertRaises(OSError):
            win.WindowsProjectDirectory("C:\\project", file_limit=64)


# The attacker is a separate interpreter. The parent synchronizes at a real
# open/read boundary, waits for this attempted operation, then resumes it.
_ATTACKER = r'''
import ctypes as C, json, os, sys
p = json.loads(sys.argv[1])
try:
    if p['op'] == 'replace_symlink':
        os.replace(p['path'], p['backup'])
        try:
            os.symlink(p['target'], p['path'], target_is_directory=p['directory'])
        except BaseException:
            os.replace(p['backup'], p['path'])
            raise
    elif p['op'] == 'replace':
        os.replace(p['path'], p['backup'])
    elif p['op'] == 'hardlink':
        os.link(p['path'], p['target'])
    elif p['op'] == 'write':
        with open(p['path'], 'r+b') as f:
            f.write(bytes.fromhex(p['data']))
    elif p['op'] == 'native_open':
        k = C.WinDLL('kernel32', use_last_error=True)
        k.CreateFileW.argtypes = [C.c_wchar_p,C.c_uint32,C.c_uint32,C.c_void_p,
                                  C.c_uint32,C.c_uint32,C.c_void_p]
        k.CreateFileW.restype = C.c_void_p
        k.CloseHandle.argtypes = [C.c_void_p]
        k.CloseHandle.restype = C.c_int
        h = k.CreateFileW(p['path'], p['access'], 7, None, 3, 0, None)
        if h == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        k.CloseHandle(h)
    else:
        raise RuntimeError('Unknown fixture operation')
    print(json.dumps({'completed': True}))
except OSError as e:
    print(json.dumps({'completed': False, 'winerror': getattr(e, 'winerror', None),
                      'errno': e.errno, 'message': str(e)}))
'''


@unittest.skipUnless(os.name == "nt", "UNTESTED: real Windows native execution is required")
class NativeWindowsReaderTests(unittest.TestCase):
    """Actual native calls. Failure to open a normal local fixture is a failure."""

    @classmethod
    def setUpClass(cls):
        print("\nWindows reader native environment: " + json.dumps({
            "os": platform.platform(), "machine": platform.machine(),
            "python": sys.version, "pointer_bits": C.sizeof(C.c_void_p) * 8,
            "filesystem_required": "local NTFS", "filter_provider_state": "not independently inventoried",
            "packaged_exe": "not exercised by source tests"}, ensure_ascii=False))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-native-reader-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "world.yaml").write_bytes(b"OUTSIDE-FIXTURE")
        self.world = self.project / "world.yaml"
        self.world.write_bytes(b"APPROVED\r\n")
        self.version = self.project / "execution-version.json"
        self.version.write_bytes(b"{}\r\n")
        self.native = win._Native()
        self.kernel = self.native.kernel
        self._bind("CreateFileW", C.c_void_p, C.c_wchar_p, C.c_uint32, C.c_uint32,
                   C.c_void_p, C.c_uint32, C.c_uint32, C.c_void_p)
        self._bind("GetCurrentProcess", C.c_void_p)
        self._bind("GetProcessHandleCount", C.c_int, C.c_void_p, C.POINTER(C.c_uint32))
        self._bind("DeviceIoControl", C.c_int, C.c_void_p, C.c_uint32, C.c_void_p,
                   C.c_uint32, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32), C.c_void_p)

    def _bind(self, name, restype, *args):
        fn = getattr(self.kernel, name)
        fn.restype, fn.argtypes = restype, list(args)
        return fn

    def reader(self, root=None, limit=1024):
        return win.WindowsProjectDirectory(root or self.project, file_limit=limit)

    def attack(self, op, **kwargs):
        result = subprocess.run([sys.executable, "-c", _ATTACKER,
                                 json.dumps({"op": op, **{k: str(v) if isinstance(v, Path) else v
                                                           for k, v in kwargs.items()}})],
                                check=True, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout)

    def raw_handle(self, path, access, *, directory=False):
        h = self.kernel.CreateFileW(str(path), access, 7, None, 3,
                                    0x02200000 if directory else 0x00200000, None)
        if h == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        return win._OwnedHandle(self.native, h)

    def handle_count(self):
        count = C.c_uint32()
        self.assertTrue(self.kernel.GetProcessHandleCount(self.kernel.GetCurrentProcess(), C.byref(count)))
        return count.value

    def require_symlink(self, *, directory=False):
        probe = self.base / "symlink-probe"
        target = self.outside if directory else self.outside / "world.yaml"
        try:
            os.symlink(target, probe, target_is_directory=directory)
        except OSError as exc:
            self.fixture_unavailable("symlink", exc)
        if directory:
            os.rmdir(probe)
        else:
            probe.unlink()

    def fixture_unavailable(self, feature, exc):
        # Do not disguise malformed buffers/bad paths or unexpected API errors
        # as a missing capability. Privilege/settings changes are not attempted.
        if getattr(exc, "winerror", None) in (1, 5, 50, 1314):
            self.skipTest("UNTESTED: " + feature + " fixture unavailable: " + str(exc))
        raise exc

    def set_junction(self, handle, target):
        target = os.path.abspath(target)
        substitute = ("\\??\\" + target).encode("utf-16-le")
        display = target.encode("utf-16-le")
        names = substitute + b"\0\0" + display + b"\0\0"
        data = struct.pack("<IHHHHHH", 0xA0000003, 8 + len(names), 0,
                           0, len(substitute), len(substitute) + 2, len(display)) + names
        buffer, returned = C.create_string_buffer(data), C.c_uint32()
        if not self.kernel.DeviceIoControl(handle.value, 0x000900A4, buffer, len(data),
                                          None, 0, C.byref(returned), None):
            raise C.WinError(C.get_last_error())

    def make_junction(self, path, target):
        path.mkdir()
        try:
            with self.raw_handle(path, 0x100, directory=True) as handle:
                self.set_junction(handle, target)
        except OSError as exc:
            self.fixture_unavailable("junction", exc)

    def collect(self, reader):
        return {"world.yaml": reader.read("world.yaml")}, reader.read("execution-version.json")

    def test_native_binary_identity_and_exact_tree(self):
        with self.reader() as reader:
            files, version = self.collect(reader)
            self.assertEqual(files["world.yaml"].data, b"APPROVED\r\n")
            self.assertEqual(files["world.yaml"].identity[0], reader._volume)
            self.assertEqual(len(files["world.yaml"].identity[1]), 16)
            reader.exact_tree(files, version)
        self.assertEqual(self.world.read_bytes(), b"APPROVED\r\n")

    def test_optional_missing_leaf_only(self):
        with self.reader() as reader:
            self.assertIsNone(reader.read("absent.yaml", optional=True))
            with self.assertRaises(OSError):
                reader.read("absent.yaml")
            with self.assertRaises(OSError):
                reader.read("absent/leaf.yaml", optional=True)
            with self.assertRaises(ValueError):
                reader.read("../absent", optional=True)
            (self.project / "directory").mkdir()
            with self.assertRaises(ValueError):
                reader.read("directory", optional=True)

    def test_optional_does_not_hide_sharing_failure(self):
        with self.raw_handle(self.world, 0x40000000):
            with self.reader() as reader, self.assertRaises(OSError):
                reader.read("world.yaml", optional=True)

    def test_limits_zero_budget_and_exact_limit(self):
        data = b"x" * (win._CHUNK + 19)
        self.world.write_bytes(data)
        with self.reader(limit=len(data)) as reader:
            self.assertEqual(reader.read("world.yaml").data, data)
            with self.assertRaises(ValueError):
                reader.read("world.yaml", budget=len(data) - 1)
        with self.reader(limit=len(data) - 1) as reader, self.assertRaises(ValueError):
            reader.read("world.yaml")
        (self.project / "empty").write_bytes(b"")
        with self.reader(limit=0) as reader:
            self.assertEqual(reader.read("empty", budget=0).data, b"")
            with self.assertRaises(ValueError):
                reader.read("world.yaml")

    def test_hardlink_outside_is_rejected_without_symlink_capability(self):
        alias = self.project / "hardlinked.yaml"
        os.link(self.outside / "world.yaml", alias)
        with self.reader() as reader, self.assertRaises(ValueError):
            reader.read(alias.name)
        self.assertEqual((self.outside / "world.yaml").read_bytes(), b"OUTSIDE-FIXTURE")

    def test_duplicate_identity_is_rejected_independently(self):
        with self.reader() as reader:
            files, version = self.collect(reader)
            with self.assertRaisesRegex(ValueError, "duplicate native identities"):
                reader.exact_tree({**files, "alias.yaml": files["world.yaml"]}, version)

    def test_declared_hardlinked_roles_are_rejected(self):
        os.link(self.world, self.project / "alias.yaml")
        with self.reader() as reader:
            for name in ("world.yaml", "alias.yaml"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    reader.read(name)

    def test_new_hardlink_after_collection_fails_exact_tree(self):
        with self.reader() as reader:
            files, version = self.collect(reader)
            os.link(self.world, self.outside / "new-alias")
            with self.assertRaises(ValueError):
                reader.exact_tree(files, version)

    def test_preexisting_leaf_symlink(self):
        self.require_symlink()
        self.world.unlink()
        os.symlink(self.outside / "world.yaml", self.world)
        with self.reader() as reader, self.assertRaises((ValueError, OSError)):
            reader.read("world.yaml", optional=True)

    def test_preexisting_directory_symlink(self):
        self.require_symlink(directory=True)
        os.symlink(self.outside, self.project / "linked", target_is_directory=True)
        with self.reader() as reader, self.assertRaises((ValueError, OSError)):
            reader.read("linked/world.yaml")

    def test_preexisting_root_symlink(self):
        self.require_symlink(directory=True)
        os.symlink(self.outside, self.base / "linked-root", target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.reader(self.base / "linked-root")

    def test_preexisting_ancestor_symlink(self):
        self.require_symlink(directory=True)
        (self.outside / "nested").mkdir()
        os.symlink(self.outside, self.base / "linked-ancestor", target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.reader(self.base / "linked-ancestor" / "nested")

    def test_selected_root_parent_uses_retained_stack_not_native_dotdot(self):
        inner = self.project / "inner"
        inner.mkdir()
        original = win._Native.open_relative
        names = []
        def observed(native, parent, component):
            names.append(component)
            return original(native, parent, component)
        with patch.object(win._Native, "open_relative", observed):
            with self.reader(str(inner) + "\\..\\.") as reader:
                self.assertEqual(reader.read("world.yaml").data, b"APPROVED\r\n")
        self.assertIn("inner", names)
        self.assertNotIn("..", names)
        self.assertNotIn(".", names)

    def test_missing_root_component_before_parent_is_not_erased(self):
        baseline = self.handle_count()
        with self.assertRaises(OSError):
            self.reader(str(self.project / "missing") + "\\..")
        self.assertEqual(self.handle_count(), baseline)

    def test_file_root_component_before_parent_is_not_erased(self):
        baseline = self.handle_count()
        with self.assertRaises((ValueError, OSError)):
            self.reader(str(self.world) + "\\..")
        self.assertEqual(self.handle_count(), baseline)

    def test_root_symlink_before_parent_is_not_erased(self):
        self.require_symlink(directory=True)
        link = self.project / "linked"
        os.symlink(self.outside, link, target_is_directory=True)
        baseline = self.handle_count()
        with self.assertRaises((ValueError, OSError)):
            self.reader(str(link) + "\\..")
        self.assertEqual(self.handle_count(), baseline)

    def test_root_junction_before_parent_is_not_erased(self):
        link = self.project / "junction"
        self.make_junction(link, self.outside)
        baseline = self.handle_count()
        with self.assertRaises((ValueError, OSError)):
            self.reader(str(link) + "\\..")
        self.assertEqual(self.handle_count(), baseline)

    def test_constructor_to_entry_validation_failure_closes_native_handles(self):
        baseline = self.handle_count()
        reader = self.reader()
        with patch.object(reader, "_check_anchors", side_effect=ValueError("injected entry failure")):
            with self.assertRaises(ValueError):
                reader.__enter__()
        # Keep the object alive: destructor/GC is not the cleanup mechanism.
        self.assertEqual(reader._anchors, [])
        self.assertEqual(self.handle_count(), baseline)
        reader.close()

    def test_constructor_to_entry_reparse_failure_closes_native_handles(self):
        root = self.base / "entry-root"
        root.mkdir()
        with self.raw_handle(root, 0x100, directory=True) as attributes:
            baseline = self.handle_count()
            reader = self.reader(root)
            try:
                try:
                    self.set_junction(attributes, self.outside)
                except OSError as exc:
                    self.fixture_unavailable("constructor-to-entry reparse", exc)
                with self.assertRaises((ValueError, OSError)):
                    reader.__enter__()
                self.assertEqual(reader._anchors, [])
                self.assertEqual(self.handle_count(), baseline)
            finally:
                reader.close()

    def test_junction_leaf_parent_rejected(self):
        self.make_junction(self.project / "junction", self.outside)
        with self.reader() as reader, self.assertRaises((ValueError, OSError)):
            reader.read("junction/world.yaml")

    def test_junction_root_rejected(self):
        self.make_junction(self.base / "junction", self.outside)
        with self.assertRaises((ValueError, OSError)):
            self.reader(self.base / "junction")

    def test_junction_ancestor_rejected(self):
        (self.outside / "nested").mkdir()
        self.make_junction(self.base / "junction", self.outside)
        with self.assertRaises((ValueError, OSError)):
            self.reader(self.base / "junction" / "nested")

    def _swap_at_open(self, kind, *, after=False):
        directory = kind != "leaf"
        self.require_symlink(directory=directory)
        if kind == "leaf":
            source, target, root, relative = self.world, self.outside / "world.yaml", self.project, "world.yaml"
        elif kind == "parent":
            source = self.project / "inner"
            source.mkdir()
            (source / "world.yaml").write_bytes(b"APPROVED\r\n")
            target, root, relative = self.outside, self.project, "inner/world.yaml"
        elif kind == "root":
            source, target, root, relative = self.project, self.outside, self.project, "world.yaml"
        else:
            source = self.base / "ancestor"
            source.mkdir()
            root = source / "nested"
            root.mkdir()
            (root / "world.yaml").write_bytes(b"APPROVED\r\n")
            (self.outside / "nested").mkdir()
            (self.outside / "nested" / "world.yaml").write_bytes(b"OUTSIDE-FIXTURE")
            target, relative = self.outside, "world.yaml"
        original = win._Native.open_relative
        outcomes = []
        def interleave(native, parent, name):
            child = original(native, parent, name) if after else None
            if name == source.name and not outcomes:
                outcomes.append(self.attack("replace_symlink", path=source,
                                            backup=source.with_name(source.name + "-old"),
                                            target=target, directory=directory))
            return child if after else original(native, parent, name)
        with patch.object(win._Native, "open_relative", interleave):
            try:
                with self.reader(root) as reader:
                    self.assertEqual(reader.read(relative).data, b"APPROVED\r\n")
            except (ValueError, OSError):
                pass  # Explicit refusal is an accepted confined result.
        self.assertEqual(len(outcomes), 1)
        if after:
            self.assertFalse(outcomes[0]["completed"], outcomes)
            self.assertIn(outcomes[0].get("winerror"), (5, 32, 33), outcomes)
        else:
            self.assertTrue(outcomes[0]["completed"], outcomes)

    def test_leaf_swap_before_native_open(self):
        self._swap_at_open("leaf")

    def test_leaf_swap_after_native_open(self):
        self._swap_at_open("leaf", after=True)

    def test_parent_swap_before_native_open(self):
        self._swap_at_open("parent")

    def test_parent_swap_after_native_open(self):
        self._swap_at_open("parent", after=True)

    def test_root_swap_before_native_open(self):
        self._swap_at_open("root")

    def test_root_swap_after_native_open(self):
        self._swap_at_open("root", after=True)

    def test_ancestor_swap_before_native_open(self):
        self._swap_at_open("ancestor")

    def test_ancestor_swap_after_native_open(self):
        self._swap_at_open("ancestor", after=True)

    def test_existing_attribute_handle_can_attempt_in_place_reparse_change(self):
        # Attribute access is deliberately not presumed excluded by share mode.
        inner = self.project / "inner"
        inner.mkdir()
        with self.raw_handle(inner, 0x100, directory=True) as attributes:
            with self.reader() as reader:
                original = reader._native.open_relative
                attempted = []
                def convert_before_leaf(parent, name):
                    if name == "world.yaml" and not attempted:
                        attempted.append(True)
                        try:
                            self.set_junction(attributes, self.outside)
                        except OSError as exc:
                            self.fixture_unavailable("in-place reparse", exc)
                    return original(parent, name)
                with patch.object(reader._native, "open_relative", convert_before_leaf):
                    with self.assertRaises((ValueError, OSError)):
                        reader.read("inner/world.yaml")
                self.assertTrue(attempted)

    def test_existing_writer_handle_blocks_read(self):
        with self.raw_handle(self.world, 0x40000000):
            with self.reader() as reader, self.assertRaises(OSError):
                reader.read("world.yaml")

    def test_existing_delete_handle_blocks_read(self):
        with self.raw_handle(self.world, 0x00010000):
            with self.reader() as reader, self.assertRaises(OSError):
                reader.read("world.yaml")

    def _attempt_during_read(self, op, **kwargs):
        with self.reader() as reader:
            original = reader._native.read
            outcomes = []
            def interleave(handle, amount):
                if not outcomes:
                    outcomes.append(self.attack(op, path=self.world, **kwargs))
                return original(handle, amount)
            with patch.object(reader._native, "read", interleave):
                record = reader.read("world.yaml")
            self.assertEqual(record.data, b"APPROVED\r\n")
            self.assertEqual(len(outcomes), 1)
            return outcomes[0]

    def test_new_writer_handle_blocks_while_reading(self):
        outcome = self._attempt_during_read("native_open", access=0x40000000)
        self.assertFalse(outcome["completed"], outcome)
        self.assertIn(outcome.get("winerror"), (5, 32, 33), outcome)

    def test_new_delete_handle_blocks_while_reading(self):
        outcome = self._attempt_during_read("native_open", access=0x00010000)
        self.assertFalse(outcome["completed"], outcome)
        self.assertIn(outcome.get("winerror"), (5, 32, 33), outcome)

    def test_leaf_rename_during_read_without_symlink_capability(self):
        outcome = self._attempt_during_read("replace", backup=self.project / "moved-world")
        self.assertFalse(outcome["completed"], outcome)

    def test_retained_root_rename_without_symlink_capability(self):
        with self.reader() as reader:
            outcome = self.attack("replace", path=self.project, backup=self.base / "moved-project")
            self.assertFalse(outcome["completed"], outcome)
            self.assertEqual(reader.read("world.yaml").data, b"APPROVED\r\n")

    def test_retained_ancestor_rename_without_symlink_capability(self):
        ancestor = self.project / "ancestor"
        root = ancestor / "nested"
        root.mkdir(parents=True)
        (root / "world.yaml").write_bytes(b"APPROVED\r\n")
        with self.reader(root) as reader:
            outcome = self.attack("replace", path=ancestor, backup=self.project / "moved-ancestor")
            self.assertFalse(outcome["completed"], outcome)
            self.assertEqual(reader.read("world.yaml").data, b"APPROVED\r\n")

    def test_growth_writer_is_refused_during_actual_native_read(self):
        outcome = self._attempt_during_read("write", data=(b"GROWTH" * 400).hex())
        self.assertFalse(outcome["completed"], outcome)
        self.assertEqual(self.world.read_bytes(), b"APPROVED\r\n")

    def test_link_count_change_during_native_read_is_rejected_or_blocked(self):
        with self.reader() as reader:
            original = reader._native.read
            outcomes = []
            def link_during_read(handle, amount):
                if not outcomes:
                    outcomes.append(self.attack("hardlink", path=self.world,
                                                target=self.outside / "raced-link"))
                return original(handle, amount)
            record = None
            refused = False
            with patch.object(reader._native, "read", link_during_read):
                try:
                    record = reader.read("world.yaml")
                except (ValueError, OSError):
                    refused = True
            self.assertEqual(len(outcomes), 1)
            if outcomes[0]["completed"]:
                self.assertTrue(refused)
            elif not refused:
                self.assertEqual(record.data, b"APPROVED\r\n")

    def test_existing_writable_mapping_conflict_is_a_native_release_gate(self):
        create_mapping = self._bind("CreateFileMappingW", C.c_void_p, C.c_void_p,
                                    C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32, C.c_wchar_p)
        map_view = self._bind("MapViewOfFile", C.c_void_p, C.c_void_p, C.c_uint32,
                              C.c_uint32, C.c_uint32, C.c_size_t)
        unmap_view = self._bind("UnmapViewOfFile", C.c_int, C.c_void_p)
        file_handle = self.raw_handle(self.world, 0xC0000000)
        mapping = None
        view = None
        try:
            raw_mapping = create_mapping(file_handle.value, None, 4, 0, 0, None)  # PAGE_READWRITE
            if not raw_mapping:
                raise C.WinError(C.get_last_error())
            mapping = win._OwnedHandle(self.native, raw_mapping)
            view = map_view(mapping.value, 2, 0, 0, 0)  # FILE_MAP_WRITE
            if not view:
                raise C.WinError(C.get_last_error())
            file_handle.close()  # Test the mapping conflict, not its original handle.
            with self.reader() as reader, self.assertRaises(OSError):
                reader.read("world.yaml")
        finally:
            if view:
                self.assertTrue(unmap_view(view))
            if mapping:
                mapping.close()
            file_handle.close()

    def test_exact_tree_unexpected_missing_and_changed_identity(self):
        with self.reader() as reader:
            files, version = self.collect(reader)
            unexpected = self.project / "unexpected"
            unexpected.mkdir()
            (unexpected / "do-not-scan").write_bytes(b"x")
            with self.assertRaises(ValueError):
                reader.exact_tree(files, version)
            (unexpected / "do-not-scan").unlink()
            unexpected.rmdir()
            self.world.rename(self.project / "old-world")
            self.world.write_bytes(b"APPROVED\r\n")
            (self.project / "old-world").unlink()
            with self.assertRaises(ValueError):
                reader.exact_tree(files, version)
            self.world.unlink()
            with self.assertRaises(ValueError):
                reader.exact_tree(files, version)

    def test_exact_tree_same_size_mtime_rewrite_has_changed_metadata(self):
        with self.reader() as reader:
            files, version = self.collect(reader)
            old = self.world.stat()
            self.world.write_bytes(b"REPLACED\r\n")
            os.utime(self.world, ns=(old.st_atime_ns, old.st_mtime_ns))
            # ChangeTime is checked too. This is not an adversarial ABA proof.
            with self.assertRaises(ValueError):
                reader.exact_tree(files, version)

    def test_exact_tree_nested_handles_and_enumeration_race(self):
        nested = self.project / "subjects"
        nested.mkdir()
        (nested / "人物.yaml").write_bytes(b"subject")
        with self.reader() as reader:
            files, version = self.collect(reader)
            files["subjects/人物.yaml"] = reader.read("subjects/人物.yaml")
            reader.exact_tree(files, version)
            original = reader._native.entries
            attempted = []
            def change_after_enumeration(handle, expected):
                entries = original(handle, expected)
                if "world.yaml" in expected and not attempted:
                    attempted.append(self.attack("replace", path=self.world,
                                                  backup=self.project / "moved-world"))
                return entries
            with patch.object(reader._native, "entries", change_after_enumeration):
                with self.assertRaises((ValueError, OSError)):
                    reader.exact_tree(files, version)
            self.assertTrue(attempted[0]["completed"], attempted)

    def test_japanese_non_bmp_names_and_long_paths(self):
        names = ["資料🌸" + "a" * 55 for _ in range(5)]
        # Extended Win32 fixture spelling avoids requiring a system long-path
        # policy change. The production reader traverses components natively.
        long_base = "\\\\?\\" + str(self.project.absolute())
        for name in names:
            long_base += "\\" + name
            os.mkdir(long_base)
            self.addCleanup(os.rmdir, long_base)
        filename = "世界𠮷.yaml"
        with open(long_base + "\\" + filename, "wb") as output:
            output.write("長い道\r\n".encode("utf-8"))
        self.addCleanup(os.unlink, long_base + "\\" + filename)
        relative = "/".join([*names, filename])
        with self.reader() as reader:
            self.assertEqual(reader.read(relative).data, "長い道\r\n".encode("utf-8"))

    def test_case_alias_read_acceptance_and_native_duplicate_identity(self):
        with self.reader() as reader:
            actual = reader.read("world.yaml")
            alias = reader.read("WORLD.YAML")
            self.assertEqual(alias.data, actual.data)
            self.assertEqual(alias.identity, actual.identity)
            version = reader.read("execution-version.json")
            with self.assertRaisesRegex(ValueError, "duplicate native identities"):
                reader.exact_tree({"world.yaml": actual, "WORLD.YAML": alias}, version)
            with self.assertRaises(ValueError):
                reader.exact_tree({"WORLD.YAML": alias}, version)
            reader.exact_tree({"world.yaml": actual}, version)

    def test_short_name_alias_if_volume_supplies_one(self):
        long_file = self.project / "A very long calendar filename.yaml"
        long_file.write_bytes(b"long")
        get_short = self._bind("GetShortPathNameW", C.c_uint32, C.c_wchar_p,
                               C.c_wchar_p, C.c_uint32)
        buffer = C.create_unicode_buffer(32768)
        if not get_short(str(long_file), buffer, len(buffer)):
            self.skipTest("UNTESTED: volume did not provide an 8.3 alias")
        short = buffer.value.rsplit("\\", 1)[-1]
        if short == long_file.name:
            self.skipTest("UNTESTED: 8.3 name creation is disabled for this fixture")
        with self.reader() as reader:
            actual = reader.read(long_file.name)
            alias = reader.read(short)
            self.assertEqual(alias.data, actual.data)
            self.assertEqual(alias.identity, actual.identity)
            files, version = self.collect(reader)
            with self.assertRaisesRegex(ValueError, "duplicate native identities"):
                reader.exact_tree({**files, long_file.name: actual, short: alias}, version)
            with self.assertRaises(ValueError):
                reader.exact_tree({**files, short: alias}, version)
            reader.exact_tree({**files, long_file.name: actual}, version)

    def test_reserved_ads_and_trailing_aliases_fail_before_any_native_open(self):
        with self.reader() as reader:
            original = reader._native.open_relative
            calls = []
            def observed(parent, name):
                calls.append(name)
                return original(parent, name)
            with patch.object(reader._native, "open_relative", observed):
                for name in ("world.yaml:data", "CON", "world.yaml.", "world.yaml ", "a\\b"):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        reader.read(name)
            self.assertEqual(calls, [])

    def test_cleanup_noninheritance_and_read_failure(self):
        baseline = self.handle_count()
        for _ in range(12):
            with self.reader() as reader:
                for handle, _identity in reader._anchors:
                    flags = C.c_uint32()
                    self.assertTrue(self.kernel.GetHandleInformation(handle.value, C.byref(flags)))
                    self.assertFalse(flags.value & 1)
                steady = self.handle_count()
                for name in ("world.yaml", "absent"):
                    try:
                        reader.read(name)
                    except OSError:
                        pass
                # Failure injection tests cleanup, not native error semantics.
                with patch.object(reader._native, "read", side_effect=OSError("injected ReadFile failure")):
                    with self.assertRaises(OSError):
                        reader.read("world.yaml")
                original = reader._native.inspect
                def fail_leaf_query(handle):
                    state = original(handle)
                    if not state.directory:
                        raise OSError("injected leaf query failure")
                    return state
                with patch.object(reader._native, "inspect", fail_leaf_query):
                    with self.assertRaises(OSError):
                        reader.read("world.yaml")
                self.assertEqual(self.handle_count(), steady)
            reader.close()
            with self.assertRaises(ValueError):
                reader.read("world.yaml")
        self.assertEqual(self.handle_count(), baseline)

    def test_real_native_invalid_handle_errors(self):
        with self.assertRaises(OSError):
            self.native.read(0, 1)
        with self.assertRaises((OSError, ValueError)):
            self.native.inspect(0)

    def test_exact_tree_identity_query_failure_closes_child_handles(self):
        nested = self.project / "nested"
        nested.mkdir()
        (nested / "item").write_bytes(b"x")
        with self.reader() as reader:
            files, version = self.collect(reader)
            files["nested/item"] = reader.read("nested/item")
            baseline = self.handle_count()
            original = reader._native.entries
            def fail_inside(handle, expected):
                if "item" in expected:
                    raise OSError("injected enumeration failure")
                return original(handle, expected)
            with patch.object(reader._native, "entries", fail_inside):
                with self.assertRaises(OSError):
                    reader.exact_tree(files, version)
            self.assertEqual(self.handle_count(), baseline)


if __name__ == "__main__":
    unittest.main()
