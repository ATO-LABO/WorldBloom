"""Bounded real-operation Windows gates, unexecuted in the Linux authoring task.

All IDs are discovered on all platforms. Capability-specific skips remain
missing native evidence, never a passed security case. Every target is within
one test-owned temporary root. There are no privilege/permission changes.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import mmap
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from engine.calendar_windows_writer import NativeWindowsAdapter, SnapshotWriteError, WindowsDirectSnapshot
from calendar_windows_native_fixture_support import NativeFixtureOps, READ_ATTRIBUTES, WRITE_ATTRIBUTES, SYMLINK
from test_calendar_windows_writer import native_data_open_error


PAYLOAD = b"bounded native snapshot fixture\x00" * 4


class FixtureWorker:
    def __init__(self, root, token, **configuration):
        inherit_handles = configuration.pop("inherit_handles", False)
        config = {"root": str(root), "token": token, **configuration}
        script = Path(__file__).with_name("calendar_windows_native_fixture_support.py")
        self.process = subprocess.Popen([sys.executable, "-u", str(script), json.dumps(config)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                        close_fds=not inherit_handles, cwd=Path(__file__).resolve().parents[1])
        self.messages = queue.Queue()
        def collect():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({"ok": False, "message": line})
            self.messages.put({"eof": True})
        self.thread = threading.Thread(target=collect, daemon=True)
        self.thread.start()

    def read(self):
        try:
            result = self.messages.get(timeout=15)
        except queue.Empty as exc:
            raise AssertionError("Owned fixture subprocess did not reach its barrier within 15 seconds") from exc
        if result.get("eof"):
            raise AssertionError("Owned fixture subprocess exited before the expected barrier")
        return result

    def go(self):
        self.process.stdin.write("go\n")
        self.process.stdin.flush()

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.thread.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


class HookAdapter(NativeWindowsAdapter):
    def __init__(self, callback=None):
        super().__init__()
        self.callback = callback

    def create_new(self, parent, leaf):
        if self.callback is not None:
            self.callback(parent, leaf)
        return super().create_new(parent, leaf)


@unittest.skipUnless(os.name == "nt", "Native Windows gate: NOT RUN on this OS")
class NativeWindowsSecurityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="worldbloom-windows-gate-")
        self.addCleanup(temp.cleanup)  # Only this owned fixture; never writer rollback.
        self.root = Path(temp.name)
        self.token = uuid.uuid4().hex
        (self.root / ".fixture-owner").write_text(self.token, encoding="ascii")
        (self.root / "save").mkdir()
        (self.root / "redirected").mkdir()
        (self.root / "redirected" / "keep").write_bytes(b"untouched target sentinel")
        self.ops = NativeFixtureOps(self.root, self.token)
        self.adapter = NativeWindowsAdapter()
        # The borrowed parent contract is narrower than all Windows filesystems.
        try:
            parent = self.adapter.open_existing_parent(self.root / "save")
            parent.close()
        except OSError as exc:
            self.skipTest("Native parent fixture unavailable; no fallback: " + str(exc))

    def writer(self, *, adapter=None, parent="save", leaf="new.snapshot", data=PAYLOAD):
        return WindowsDirectSnapshot(self.ops.path(parent), leaf, expected_size=len(data),
                                     expected_sha256=hashlib.sha256(data).hexdigest(),
                                     max_bytes=len(data), adapter=adapter or self.adapter)

    def actor(self, **configuration):
        actor = FixtureWorker(self.root, self.token, **configuration)
        self.addCleanup(actor.stop)
        return actor

    def ready_actor(self, **configuration):
        actor = self.actor(**configuration)
        self.assertEqual(actor.read().get("stage"), "ready")
        return actor

    def finish_writer_or_refusal(self, writer):
        try:
            with writer:
                writer.write_all([PAYLOAD])
                writer.seal()
        except SnapshotWriteError:
            return False
        return True

    def assert_redirected_unchanged(self):
        self.assertEqual(sorted(x.name for x in (self.root / "redirected").iterdir()), ["keep"])
        self.assertEqual((self.root / "redirected" / "keep").read_bytes(), b"untouched target sentinel")

    def cleanup_known_reparse(self, relative, *, tag=0xA0000003, directory=True):
        # Only test-created names; workers must be stopped before this call.
        path = self.ops.path(relative)
        if path.lstat().st_file_attributes & 0x400:
            self.ops.remove_own_reparse(relative, tag=tag, directory=directory)

    def require_junction_capability(self):
        (self.root / "junction-probe").mkdir()
        handle = self.ops.open("junction-probe", READ_ATTRIBUTES | WRITE_ATTRIBUTES, directory=True)
        try:
            try:
                self.ops.set_reparse(handle, "redirected")
                self.ops.delete_reparse(handle)
            except OSError as exc:
                self.skipTest("Junction attribute-handle fixture unavailable; no permission change or fallback: " + str(exc))
        finally:
            self.ops.close(handle)

    def test_native_parent_reparse_before_final_create(self):
        self.require_junction_capability()
        actor = self.ready_actor(mode="set_junction", path="save", target="redirected")
        outcomes = []
        def convert(parent, leaf):
            actor.go()
            outcomes.append(actor.read())
        writer = self.writer(adapter=HookAdapter(convert))
        try:
            self.finish_writer_or_refusal(writer)
            self.assert_redirected_unchanged()
            self.assertEqual(len(outcomes), 1)
            if not outcomes[0].get("ok"):
                self.skipTest("Held-parent reparse mutation was not executed: " + str(outcomes[0]))
        finally:
            actor.stop()
            self.cleanup_known_reparse("save")

    def test_native_parent_reparse_during_create(self):
        self.require_junction_capability()
        # Bounded genuine subprocess overlap, not a claim to control a kernel
        # instruction interleaving. Each iteration has a fresh owned parent.
        completed_mutations = 0
        for index in range(4):
            relative = "overlap-" + str(index)
            self.ops.path(relative).mkdir()
            actor = self.ready_actor(mode="set_junction", path=relative, target="redirected")
            writer = self.writer(parent=relative, adapter=HookAdapter(lambda _p, _l: actor.go()))
            try:
                self.finish_writer_or_refusal(writer)
                outcome = actor.read()
                # 145 is a nonempty directory after the file won the race;
                # 32/5 are denied operations, not permission to try another API.
                self.assertTrue(outcome.get("ok") or outcome.get("winerror") in (145, 32, 5), outcome)
                completed_mutations += int(bool(outcome.get("ok")))
                print("NATIVE_FIXTURE_OUTCOME " + json.dumps({"test": self.id(), "iteration": index,
                                                             "mutation": outcome}), flush=True)
                self.assert_redirected_unchanged()
            finally:
                actor.stop()
                self.cleanup_known_reparse(relative)
        if completed_mutations == 0:
            self.skipTest("All bounded overlapping mutations were refused; successful overlap remains unexecuted")

    def test_native_parent_reparse_aba(self):
        self.require_junction_capability()
        actor = self.ready_actor(mode="junction_aba", path="save", target="redirected", loops=1)
        outcomes = []
        def aba(parent, leaf):
            actor.go()
            outcomes.append(actor.read())
        try:
            writer = self.writer(adapter=HookAdapter(aba))
            self.finish_writer_or_refusal(writer)
            self.assert_redirected_unchanged()
            self.assertEqual(len(outcomes), 1)
            if not outcomes[0].get("ok"):
                self.skipTest("Parent reparse ABA fixture not executed: " + str(outcomes[0]))
        finally:
            actor.stop()
            self.cleanup_known_reparse("save")

    def test_native_ancestor_rename_logical_parent(self):
        (self.root / "branch").mkdir()
        (self.root / "branch" / "pivot").mkdir()
        (self.root / "branch" / "save").mkdir()
        (self.root / "other").mkdir()
        (self.root / "other" / "save").mkdir()
        actor = self.ready_actor(mode="rename", path="branch/pivot", target="other/pivot")
        reader = self.adapter.reader
        original = reader._Native.open_relative
        outcomes = []
        def after_pivot_open(native, parent, component):
            result = original(native, parent, component)
            if component == "pivot":
                actor.go()
                outcomes.append(actor.read())
            return result
        selected = self.root / "branch" / "pivot" / ".." / "save"
        writer = WindowsDirectSnapshot(selected, "new.snapshot", expected_size=len(PAYLOAD),
                                       expected_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                                       max_bytes=len(PAYLOAD), adapter=self.adapter)
        try:
            with patch.object(reader._Native, "open_relative", after_pivot_open):
                success = self.finish_writer_or_refusal(writer)
            self.assertEqual(len(outcomes), 1)
            self.assertTrue(outcomes[0].get("ok") or outcomes[0].get("winerror") in (5, 32), outcomes)
            self.assertFalse((self.root / "other" / "save" / "new.snapshot").exists())
            if success:
                self.assertEqual((self.root / "branch" / "save" / "new.snapshot").read_bytes(), PAYLOAD)
        finally:
            actor.stop()

    def _competing_creator(self, mode):
        actor = self.ready_actor(mode=mode, path="save/new.snapshot", target="redirected")
        def create_first(parent, leaf):
            actor.go()
            result = actor.read()
            self.assertTrue(result.get("ok"), result)
        try:
            with self.assertRaises(SnapshotWriteError):
                with self.writer(adapter=HookAdapter(create_first)):
                    self.fail("Competing final name must not be adopted")
            path = self.root / "save" / "new.snapshot"
            if mode == "file":
                self.assertEqual(path.read_bytes(), b"competitor sentinel")
            elif mode == "directory":
                self.assertEqual((path / "keep").read_bytes(), b"competitor sentinel")
            else:
                self.assertTrue(path.lstat().st_file_attributes & 0x400)
            self.assert_redirected_unchanged()
        finally:
            actor.stop()
            if mode == "junction" and (self.root / "save" / "new.snapshot").exists():
                self.cleanup_known_reparse("save/new.snapshot")

    def test_native_racing_final_file_creator(self):
        self._competing_creator("file")

    def test_native_racing_final_directory_creator(self):
        self._competing_creator("directory")

    def test_native_racing_final_junction_creator(self):
        self.require_junction_capability()
        self._competing_creator("junction")

    def test_native_existing_file_symlink_missing_target(self):
        path = self.root / "save" / "new.snapshot"
        target = self.root / "redirected" / "absent-file"
        try:
            os.symlink(target, path)
        except OSError as exc:
            self.skipTest("File symlink fixture unavailable; junction/hardlink cases are separate: " + str(exc))
        with self.assertRaises(SnapshotWriteError):
            with self.writer():
                self.fail("Must not follow an existing symlink to create its missing target")
        self.assertFalse(target.exists())
        self.assertTrue(path.is_symlink())
        self.assert_redirected_unchanged()

    def test_native_share_matrix_after_acquisition(self):
        writer = self.writer()
        with writer:
            for label, access in (("read", 1), ("write", 2), ("append", 4), ("execute", 0x20), ("delete", 0x10000)):
                with self.subTest(access=label), self.assertRaises(OSError) as caught:
                    handle = self.ops.open("save/new.snapshot", access)
                    self.ops.close(handle)
                self.assertEqual(caught.exception.winerror, 32)
            # Metadata permissions are deliberately distinct from data sharing.
            # If the OS refuses one, record an actual denial rather than granting
            # access or treating it as proof that all metadata mutations are blocked.
            for access in (READ_ATTRIBUTES, WRITE_ATTRIBUTES, 8, 0x10):
                try:
                    handle = self.ops.open("save/new.snapshot", access)
                except OSError as exc:
                    self.assertIn(exc.winerror, (5, 32))
                else:
                    self.ops.close(handle)
            writer.write_all([PAYLOAD])
            writer.seal()

    def test_native_parent_share_matrix_before_acquisition(self):
        # A preexisting directory-add handle is a concrete conflicting parent
        # writer. A preexisting attribute-only handle tests the separate channel.
        parent_write = self.ops.open("save", 2, directory=True)
        try:
            with self.assertRaises(SnapshotWriteError):
                with self.writer():
                    self.fail("Conflicting parent handle must fail acquisition")
            self.assertFalse((self.root / "save" / "new.snapshot").exists())
        finally:
            self.ops.close(parent_write)
        attribute = self.ops.open("save", READ_ATTRIBUTES | WRITE_ATTRIBUTES, directory=True)
        try:
            with self.writer() as writer:
                writer.write_all([PAYLOAD]); writer.seal()
        finally:
            self.ops.close(attribute)

    def test_native_hardlink_alias_during_write(self):
        # Independent capability probe, never skipped because symlink privilege
        # was absent in another test.
        probe = self.root / "hardlink-probe"
        probe.write_bytes(b"probe")
        try:
            os.link(probe, self.root / "hardlink-probe-alias")
        except OSError as exc:
            self.skipTest("Hardlink fixture unsupported; no fallback: " + str(exc))
        alias = self.root / "alias"
        writer = self.writer()
        linked = False
        try:
            with writer:
                writer.write_all([PAYLOAD])
                try:
                    os.link(self.root / "save" / "new.snapshot", alias)
                except OSError as exc:
                    self.assertIn(exc.winerror, (5, 32))
                    writer.seal()
                else:
                    linked = True
                    with self.assertRaises(OSError) as denied:
                        alias.read_bytes()
                    self.assertEqual(native_data_open_error(alias, 0x80000000), 32)
                    with self.assertRaises(SnapshotWriteError):
                        writer.seal()
        except SnapshotWriteError:
            self.assertTrue(linked)
        self.assertEqual((self.root / "save" / "new.snapshot").read_bytes(), PAYLOAD)
        if linked:
            self.assertEqual(alias.read_bytes(), PAYLOAD)

    def test_native_preexisting_writable_mapping_collision(self):
        path = self.root / "save" / "new.snapshot"
        path.write_bytes(PAYLOAD)
        with path.open("r+b") as stream:
            mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE)
        try:
            with self.assertRaises(SnapshotWriteError):
                with self.writer():
                    self.fail("A preexisting mapped destination must not be adopted")
            self.assertEqual(mapping[:], PAYLOAD)
        finally:
            mapping.close()
        self.assertEqual(path.read_bytes(), PAYLOAD)

    def test_native_new_mapping_requires_denied_data_open(self):
        with self.writer() as writer:
            writer.write_all([PAYLOAD])
            for mode in ("rb", "r+b"):
                with self.subTest(mode=mode), self.assertRaises(OSError) as denied:
                    with (self.root / "save" / "new.snapshot").open(mode):
                        self.fail("No data handle should be available for a new mapping")
                access = 0x80000000 if mode == "rb" else 0xC0000000
                self.assertEqual(native_data_open_error(self.root / "save" / "new.snapshot", access), 32)
            writer.seal()
        with (self.root / "save" / "new.snapshot").open("rb") as stream:
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapping:
                self.assertEqual(mapping[:], PAYLOAD)

    def test_native_attribute_file_reparse_mutation(self):
        probe = self.root / "file-reparse-probe"
        probe.write_bytes(b"")
        target = self.root / "redirected" / "file-target"
        target.write_bytes(b"target sentinel")
        handle = self.ops.open("file-reparse-probe", READ_ATTRIBUTES | WRITE_ATTRIBUTES)
        try:
            try:
                self.ops.set_reparse(handle, "redirected/file-target", tag=SYMLINK)
                self.ops.delete_reparse(handle, tag=SYMLINK)
            except OSError as exc:
                self.skipTest("Attribute-only file-reparse fixture unavailable; no privilege change: " + str(exc))
        finally:
            self.ops.close(handle)
        writer = self.writer()
        mutation_set = False
        try:
            with writer:
                attribute = self.ops.open("save/new.snapshot", READ_ATTRIBUTES | WRITE_ATTRIBUTES)
                try:
                    try:
                        self.ops.set_reparse(attribute, "redirected/file-target", tag=SYMLINK)
                        mutation_set = True
                    except OSError as denied:
                        self.assertIn(denied.winerror, (5, 32))
                finally:
                    self.ops.close(attribute)
                try:
                    writer.write_all([PAYLOAD])
                    writer.seal()
                except SnapshotWriteError:
                    pass
                else:
                    self.assertFalse(mutation_set, "A still-reparse output must not pass same-handle seal")
        except SnapshotWriteError:
            pass
        finally:
            if mutation_set:
                self.cleanup_known_reparse("save/new.snapshot", tag=SYMLINK, directory=False)
        self.assertEqual(target.read_bytes(), b"target sentinel")

    def test_native_status_abi_noninheritance_and_handle_lifetime(self):
        reader = self.adapter.reader
        self.assertEqual(C.sizeof(reader._ObjectAttributes), 48 if C.sizeof(C.c_void_p) == 8 else 24)
        self.assertEqual(C.sizeof(reader._UnicodeString), 16 if C.sizeof(C.c_void_p) == 8 else 8)
        before = self.ops.handle_count()
        for index in range(8):
            writer = self.writer(leaf="handles-" + str(index))
            with writer:
                self.assertEqual(self.ops.handle_flags(writer._file.value) & 1, 0)
                writer.write_all([PAYLOAD]); writer.seal()
            self.assertEqual(writer.receipt.creation, "created")
            self.assertEqual(writer.receipt.resources_released, "confirmed")
        self.assertLessEqual(self.ops.handle_count(), before + 2, "Repeated native writes leaked handles beyond small runtime jitter")

    def test_native_noninheritable_file_absent_from_inheriting_child(self):
        with self.writer() as writer:
            expected_volume, expected_id = writer._file_id
            actor = self.ready_actor(mode="probe_handle", handle=writer._file.value, inherit_handles=True)
            try:
                actor.go()
                result = actor.read()
                self.assertTrue(result.get("ok"), result)
                self.assertFalse(result.get("valid") and result.get("volume") == expected_volume
                                 and result.get("file_id") == expected_id.hex(),
                                 "The snapshot file handle was inherited by a child")
            finally:
                actor.stop()
            writer.write_all([PAYLOAD]); writer.seal()

    def test_native_real_short_writes_and_injected_flush_failure(self):
        class ShortAndFault(NativeWindowsAdapter):
            def write(self, handle, data):
                return super().write(handle, data[:3])
            def flush(self, handle):
                raise OSError("deliberate test injection before native flush")
        writer = self.writer(adapter=ShortAndFault())
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([PAYLOAD]); writer.seal()
        self.assertEqual((self.root / "save" / "new.snapshot").read_bytes(), PAYLOAD)
        self.assertEqual(writer.receipt.bytes_written, len(PAYLOAD))
        self.assertFalse(writer.receipt.expected_bytes_verified)
        self.assertEqual(writer.receipt.resources_released, "confirmed")

    def test_native_process_termination_at_each_writer_stage(self):
        stages = ("created", "partial", "bytes_complete", "sealed", "released")
        for wanted in stages:
            relative = "crash-" + wanted
            self.ops.path(relative).mkdir()
            actor = self.actor(mode="writer_stages", parent=relative, leaf="new.snapshot", data=PAYLOAD.hex())
            try:
                for stage in stages:
                    self.assertEqual(actor.read().get("stage"), stage)
                    if stage == wanted:
                        break
                    actor.go()
            finally:
                actor.stop()
            path = self.ops.path(relative) / "new.snapshot"
            actual = path.read_bytes()
            self.assertEqual(actual, b"" if wanted == "created" else
                             PAYLOAD[:len(PAYLOAD)//2] if wanted == "partial" else PAYLOAD)
            with self.assertRaises(SnapshotWriteError):
                with self.writer(parent=relative):
                    self.fail("A terminated writer's named output must not be resumed or overwritten")
            self.assertEqual(path.read_bytes(), actual)

    def test_native_unicode_and_non_bmp_names(self):
        leaf = "世界の記録-🌱.snapshot"
        with self.writer(leaf=leaf) as writer:
            writer.write_all([PAYLOAD]); writer.seal()
        self.assertEqual((self.root / "save" / leaf).read_bytes(), PAYLOAD)

    def test_native_case_alias_existing_name_refused(self):
        path = self.root / "save" / "Existing.Snapshot"
        path.write_bytes(b"original case alias")
        with self.assertRaises(SnapshotWriteError):
            with self.writer(leaf="eXISTING.sNAPSHOT"):
                self.fail("Case-insensitive exclusive creation must refuse the alias")
        self.assertEqual(path.read_bytes(), b"original case alias")

    def test_native_shortname_alias_existing_name_refused(self):
        path = self.root / "save" / "long existing snapshot name.data"
        path.write_bytes(b"original shortname alias")
        function = self.ops.kernel.GetShortPathNameW
        function.argtypes, function.restype = [C.c_wchar_p, C.c_wchar_p, C.c_uint32], C.c_uint32
        buffer = C.create_unicode_buffer(32768)
        result = function(str(path), buffer, len(buffer))
        if not result:
            self.skipTest("8.3 name query unavailable: " + str(C.WinError(C.get_last_error())))
        self.assertLess(result, len(buffer))
        leaf = Path(buffer.value).name
        if leaf.casefold() == path.name.casefold():
            self.skipTest("No distinct 8.3 alias exists on this fixture volume")
        with self.assertRaises(SnapshotWriteError):
            with self.writer(leaf=leaf):
                self.fail("Existing shortname alias must not be overwritten")
        self.assertEqual(path.read_bytes(), b"original shortname alias")

    def test_native_long_full_path_without_changing_os_settings(self):
        leaf = "n" * 240 + ".wbs"
        with self.writer(leaf=leaf) as writer:
            writer.write_all([PAYLOAD]); writer.seal()
        selected = self.root / "save" / leaf
        extended = Path("\\\\?\\" + str(selected))
        self.assertEqual(extended.read_bytes(), PAYLOAD)


if __name__ == "__main__":
    unittest.main(verbosity=2)
