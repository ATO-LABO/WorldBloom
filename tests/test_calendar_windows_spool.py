"""Pure spool lifetime tests and separately skipped Windows native spool gates."""
from __future__ import annotations

import hashlib
import ctypes as C
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid

from test_calendar_windows_writer import FakeAdapter, FakeHandle, FakeParent, native_data_open_error
from engine.calendar_windows_writer import Acquisition, NativeCreationError, ObjectState
from engine.calendar_windows_spool import (
    CHUNK, FILE_LIMIT, NativeWindowsSpoolAdapter, SpoolError, WindowsPrivateSpool, capture_windows_file,
)


class SpoolFakeAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.state["delete_pending"] = True
        self.parents = []
        self.source = FakeHandle(self.log, "source")
        self.source_bytes = b""
        self.source_position = 0
        self.source_links = 1

    def open_existing_parent(self, path):
        self.log.append(("open_existing_parent", str(path)))
        if self.fail == "open":
            raise OSError("injected partial parent acquisition")
        parent = FakeParent(self.log)
        parent.name = "parent" + str(len(self.parents))
        self.parents.append(parent)
        self.parent = parent
        return parent

    def create_spool(self, parent, leaf):
        self.log.append(("create_spool_delete_on_close", leaf))
        if self.fail == "collision":
            raise NativeCreationError("existing temporary name", creation="conflict", resource_state="not_acquired")
        return Acquisition(self.file, *self.acquisition)

    def inspect_spool(self, handle):
        return self.inspect(handle)

    def seek(self, handle, offset):
        self.log.append(("seek", offset))
        self.position = offset

    def open_input(self, parent, leaf):
        self.log.append(("open_input", leaf))
        if self.fail == "source_open":
            raise OSError("source acquisition did not return an owner")
        return self.source

    def inspect_input(self, handle):
        return ObjectState((11, b"source"), False, len(self.source_bytes), self.source_links)

    def read(self, handle, amount):
        if handle is self.source:
            self.log.append(("source_read", amount))
            block = self.source_bytes[self.source_position:self.source_position + amount]
            self.source_position += len(block)
            return block
        return super().read(handle, amount)


class PureWindowsSpoolTests(unittest.TestCase):
    def spool(self, adapter=None, **kwargs):
        adapter = adapter or SpoolFakeAdapter()
        return adapter, WindowsPrivateSpool(parent="existing-spool-parent", adapter=adapter, **kwargs)

    def test_append_offsets_add_manifest_and_sealed_random_reads(self):
        adapter, spool = self.spool(max_bytes=100)
        first = spool.add(b"alpha", source_identity=(11, b"first"))
        second = spool.add(b"beta")
        self.assertEqual((first.offset, second.offset), (0, 5))
        self.assertIs(spool.seal(), spool)
        self.assertEqual(first.data, b"alpha")
        self.assertEqual(second.data, b"beta")
        self.assertEqual(spool.read_at(2, 5), b"phabe")
        self.assertEqual(first.manifest(), {"size_bytes": 5, "sha256": hashlib.sha256(b"alpha").hexdigest()})
        self.assertEqual(first.identity, (11, b"first"))
        self.assertEqual(spool.sha256, hashlib.sha256(b"alphabeta").hexdigest())
        spool.close()
        self.assertEqual(adapter.file.closed, 1)
        self.assertEqual(adapter.parents[0].closed, 1)
        self.assertEqual(spool.status.deletion_observed, "not_established")

    def test_delete_pending_accepted_only_in_owned_spool_path(self):
        adapter, spool = self.spool()
        self.assertTrue(adapter.state["delete_pending"])
        spool.add(b"a"); spool.seal(); spool.close()
        self.assertEqual(spool.status.handle_release, "confirmed")

    def test_close_invalidates_even_empty_records_and_chunks(self):
        _adapter, spool = self.spool()
        record = spool.add(b"")
        spool.seal(); spool.close()
        with self.assertRaises(ValueError):
            _ = record.data
        with self.assertRaises(ValueError):
            list(record.iter_chunks())
        with self.assertRaises(ValueError):
            _ = spool.sha256

    def test_no_new_sixty_four_mib_cap(self):
        _adapter, spool = self.spool(max_bytes=80 * 1024 * 1024)
        self.assertEqual(spool.max_bytes, 80 * 1024 * 1024)
        spool.close()

    def test_member_and_read_bounds_preserve_eight_mib_limit(self):
        _adapter, spool = self.spool()
        with self.assertRaises(ValueError):
            spool.add(b"x" * (FILE_LIMIT + 1))
        spool.add(b"abc"); spool.seal()
        for offset, size in ((0, FILE_LIMIT + 1), (-1, 1), (True, 1), (0, True), (2, 2)):
            with self.subTest(offset=offset, size=size), self.assertRaises(ValueError):
                spool.read_at(offset, size)
        spool.close()

    def test_short_writes_completed_and_native_calls_bounded(self):
        adapter, spool = self.spool(max_bytes=CHUNK + 2)
        adapter.short = 1000
        data = b"x" * (CHUNK + 2)
        spool.append(data); spool.seal()
        self.assertEqual(spool.read_at(0, len(data)), data)
        self.assertLessEqual(max(len(x[1]) for x in adapter.log if x[0] == "write"), CHUNK)
        spool.close()

    def test_failed_partial_append_closes_and_cannot_be_reused(self):
        adapter, spool = self.spool()
        adapter.write_result = 0
        with self.assertRaises(SpoolError):
            spool.append(b"abc")
        with self.assertRaises(ValueError):
            spool.append(b"another")
        self.assertEqual(adapter.file.closed, 1)
        self.assertEqual(adapter.parents[0].closed, 1)

    def test_seal_failure_closes_all_owners(self):
        adapter, spool = self.spool()
        spool.append(b"abc")
        adapter.fail = "flush"
        with self.assertRaises(SpoolError):
            spool.seal()
        self.assertEqual(adapter.file.closed, 1)
        self.assertEqual(adapter.parents[0].closed, 1)

    def test_unsealed_reads_and_append_after_seal_are_refused(self):
        _adapter, spool = self.spool()
        with self.assertRaises(ValueError):
            spool.read_at(0, 0)
        spool.append(b"a"); spool.seal()
        with self.assertRaises(ValueError):
            spool.append(b"b")
        self.assertIs(spool.seal(), spool)
        spool.close()

    def test_size_or_digest_mutation_refuses_seal(self):
        for replacement in (b"def", b"longer"):
            with self.subTest(replacement=replacement):
                adapter, spool = self.spool()
                spool.append(b"abc")
                adapter.content[:] = replacement
                with self.assertRaises(SpoolError):
                    spool.seal()
                self.assertEqual(adapter.file.closed, 1)

    def test_reparse_or_identity_change_closes_without_named_cleanup(self):
        for update in ({"reparse_tag": 1}, {"identity": (11, b"other")}, {"links": 2}):
            with self.subTest(update=update):
                adapter, spool = self.spool()
                spool.append(b"abc")
                adapter.state.update(update)
                with self.assertRaises(SpoolError):
                    spool.seal()
                self.assertEqual(adapter.file.closed, 1)

    def test_close_failure_still_attempts_parent_and_never_retries_file(self):
        adapter, spool = self.spool()
        adapter.file.close_error = True
        with self.assertRaises(SpoolError) as caught:
            spool.close()
        self.assertEqual(caught.exception.spool_status.handle_release, "unconfirmed")
        self.assertEqual(adapter.parents[0].closed, 1)
        spool.close()
        self.assertEqual(adapter.file.closed, 1)

    def test_constructor_unreturned_parent_release_is_unconfirmed(self):
        adapter = SpoolFakeAdapter()
        adapter.fail = "open"
        with self.assertRaises(SpoolError) as caught:
            WindowsPrivateSpool(parent="parent", adapter=adapter)
        self.assertEqual(caught.exception.spool_status.handle_release, "unconfirmed")
        self.assertEqual(caught.exception.spool_status.creation, "not_attempted")

    def test_collision_never_adopts_existing_spool(self):
        adapter = SpoolFakeAdapter()
        adapter.fail = "collision"
        with self.assertRaises(SpoolError) as caught:
            WindowsPrivateSpool(parent="parent", adapter=adapter)
        self.assertEqual(caught.exception.spool_status.creation, "conflict")
        self.assertEqual(adapter.file.closed, 0)
        self.assertEqual(adapter.parents[0].closed, 1)

    def test_read_lock_blocks_close_until_slice_finishes(self):
        adapter, spool = self.spool()
        spool.append(b"abc"); spool.seal()
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        original = adapter.read
        failures, values = [], []
        def blocking(handle, amount):
            entered.set()
            if not release.wait(3):
                raise OSError("bounded lock test timed out")
            return original(handle, amount)
        adapter.read = blocking
        def read():
            try:
                values.append(spool.read_at(0, 3))
            except BaseException as exc:
                failures.append(exc)
        def close():
            try:
                spool.close()
            except BaseException as exc:
                failures.append(exc)
            finally:
                closed.set()
        reader = threading.Thread(target=read)
        closer = threading.Thread(target=close)
        reader.start()
        try:
            self.assertTrue(entered.wait(3))
            closer.start()
            self.assertFalse(closed.wait(0.05))
        finally:
            release.set()
            reader.join(3)
            if closer.ident is not None:
                closer.join(3)
        self.assertFalse(reader.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(values, [b"abc"])

    def test_capture_streams_outer_input_in_bounded_chunks(self):
        adapter = SpoolFakeAdapter()
        data = b"x" * (CHUNK * 3 + 7)
        adapter.source_bytes = data
        spool = capture_windows_file("source-container.wbs", limit=len(data), spool_parent="parent", adapter=adapter)
        self.assertEqual(spool.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(spool.read_at(0, len(data)), data)
        self.assertLessEqual(max(x[1] for x in adapter.log if x[0] == "source_read"), CHUNK)
        self.assertEqual(adapter.source.closed, 1)
        self.assertEqual(spool.status.source_capture_release, "confirmed")
        spool.close()
        self.assertEqual([p.closed for p in adapter.parents], [1, 1])

    def test_source_acquisition_uncertainty_not_hidden_by_spool_close(self):
        adapter = SpoolFakeAdapter()
        adapter.fail = "source_open"
        with self.assertRaises(SpoolError) as caught:
            capture_windows_file("source.wbs", limit=10, spool_parent="parent", adapter=adapter)
        self.assertEqual(caught.exception.spool_status.source_capture_release, "unconfirmed")
        self.assertEqual(adapter.file.closed, 1)
        self.assertEqual([p.closed for p in adapter.parents], [1, 1])

    def test_source_size_excess_refused_without_full_outer_read(self):
        adapter = SpoolFakeAdapter()
        adapter.source_bytes = b"too large"
        with self.assertRaises(SpoolError):
            capture_windows_file("source.wbs", limit=2, spool_parent="parent", adapter=adapter)
        self.assertFalse(any(x[0] == "source_read" for x in adapter.log))
        self.assertEqual(adapter.file.closed, 1)

    def test_outer_hardlink_refused_before_read_with_independent_copy_guidance(self):
        adapter = SpoolFakeAdapter()
        adapter.source_bytes = b"source"
        adapter.source_links = 2
        with self.assertRaisesRegex(SpoolError, "independent copy"):
            capture_windows_file("source.wbs", limit=10, spool_parent="parent", adapter=adapter)
        self.assertFalse(any(x[0] == "source_read" for x in adapter.log))
        self.assertEqual(adapter.file.closed, 1)
        self.assertEqual(adapter.source.closed, 1)


@unittest.skipUnless(os.name == "nt", "Native Windows spool gate: NOT RUN on this OS")
class NativeWindowsSpoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="worldbloom-windows-gate-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.token = uuid.uuid4().hex
        (self.parent / ".fixture-owner").write_text(self.token, encoding="ascii")

    def duplicate_owned_handle(self, spool):
        kernel = spool._adapter.native.kernel
        current = kernel.GetCurrentProcess
        current.argtypes, current.restype = [], C.c_void_p
        duplicate = kernel.DuplicateHandle
        duplicate.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p),
                              C.c_uint32, C.c_int, C.c_uint32]
        duplicate.restype = C.c_int
        handle = C.c_void_p()
        if not duplicate(current(), spool._file.value, current(), C.byref(handle), 0, False, 2):
            raise C.WinError(C.get_last_error())
        return spool._adapter.reader._OwnedHandle(spool._adapter.native, handle.value)

    def assert_name_gone_after_release(self, name):
        # Observe bounded ordinary cleanup, not a power-loss durability claim.
        for _ in range(50):
            if not name.exists():
                return
            time.sleep(0.02)
        self.fail("Delete-on-close name removal was not observed after all owned handles/views closed")

    def test_native_owned_spool_close_removes_only_its_name(self):
        keep = self.parent / "keep"
        keep.write_bytes(b"preexisting sentinel")
        spool = WindowsPrivateSpool(parent=self.parent)
        name = self.parent / spool._leaf
        try:
            spool.add(b"abc"); spool.seal()
            self.assertEqual(spool.read_at(0, 3), b"abc")
        finally:
            spool.close()
        self.assertFalse(name.exists(), "Native delete-on-close behavior not established")
        self.assertEqual(keep.read_bytes(), b"preexisting sentinel")

    def test_native_spool_name_cannot_be_opened_for_data_while_held(self):
        spool = WindowsPrivateSpool(parent=self.parent)
        try:
            spool.add(b"abc"); spool.seal()
            with self.assertRaises(OSError) as denied:
                (self.parent / spool._leaf).read_bytes()
            self.assertIn(native_data_open_error(self.parent / spool._leaf, 0x80000000), (5, 32, 303))
        finally:
            spool.close()

    def test_native_outer_capture_is_streamed_and_source_survives(self):
        path = self.parent / "outer.wbs"
        data = b"native outer bytes" * 10000
        path.write_bytes(data)
        spool = capture_windows_file(path, limit=len(data), spool_parent=self.parent)
        name = self.parent / spool._leaf
        try:
            self.assertEqual(spool.sha256, hashlib.sha256(data).hexdigest())
            self.assertEqual(spool.read_at(19, 71), data[19:90])
        finally:
            spool.close()
        self.assertFalse(name.exists())
        self.assertEqual(path.read_bytes(), data)

    def test_native_outer_hardlink_is_refused_and_both_names_survive(self):
        source = self.parent / "outer.wbs"
        alias = self.parent / "alias.wbs"
        source.write_bytes(b"original container fixture")
        try:
            os.link(source, alias)
        except OSError as exc:
            self.skipTest("Hardlink fixture unavailable; no fallback or linked symlink skip: " + str(exc))
        with self.assertRaisesRegex(SpoolError, "independent copy"):
            capture_windows_file(source, limit=100, spool_parent=self.parent)
        self.assertEqual(source.read_bytes(), b"original container fixture")
        self.assertEqual(alias.read_bytes(), b"original container fixture")

    def test_native_spool_process_termination_lifetime(self):
        from test_calendar_windows_native_gates import FixtureWorker
        for wanted in ("spool_created", "spool_appended", "spool_sealed", "spool_released"):
            folder = self.parent / wanted
            folder.mkdir()
            actor = FixtureWorker(self.parent, self.token, mode="spool_stages", parent=wanted)
            self.addCleanup(actor.stop)
            name = None
            try:
                for stage in ("spool_created", "spool_appended", "spool_sealed", "spool_released"):
                    event = actor.read()
                    self.assertEqual(event.get("stage"), stage)
                    name = folder / event["leaf"]
                    if stage == wanted:
                        break
                    actor.go()
            finally:
                actor.stop()
            self.assertIsNotNone(name)
            self.assert_name_gone_after_release(name)

    def test_native_spool_explicit_owned_duplicate_extends_handle_lifetime(self):
        spool = WindowsPrivateSpool(parent=self.parent)
        name = self.parent / spool._leaf
        duplicate = None
        try:
            spool.append(b"duplicate lifetime"); spool.seal()
            duplicate = self.duplicate_owned_handle(spool)
            spool.close()
            spool._adapter.seek(duplicate, 0)
            self.assertEqual(spool._adapter.read(duplicate, 18), b"duplicate lifetime")
        finally:
            if duplicate is not None:
                duplicate.close()
            spool.close()
        self.assert_name_gone_after_release(name)

    def test_native_spool_owned_mapping_lifetime(self):
        import mmap
        import msvcrt
        spool = WindowsPrivateSpool(parent=self.parent)
        name = self.parent / spool._leaf
        duplicate = descriptor = mapping = None
        try:
            spool.append(b"mapped lifetime"); spool.seal()
            duplicate = self.duplicate_owned_handle(spool)
            raw = duplicate.value
            descriptor = msvcrt.open_osfhandle(raw, os.O_RDONLY | os.O_BINARY)
            duplicate._value = None  # CRT descriptor is now the sole owner of this duplicate.
            try:
                mapping = mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ)
            except OSError as exc:
                self.skipTest("Owned spool mapping fixture unavailable; no alternate open/flags: " + str(exc))
            owned_descriptor, descriptor = descriptor, None
            os.close(owned_descriptor)
            spool.close()
            self.assertEqual(mapping[:], b"mapped lifetime")
        finally:
            try:
                if mapping is not None:
                    mapping.close()
            finally:
                try:
                    if descriptor is not None:
                        owned_descriptor, descriptor = descriptor, None
                        os.close(owned_descriptor)
                finally:
                    try:
                        if duplicate is not None:
                            duplicate.close()
                    finally:
                        spool.close()
        self.assert_name_gone_after_release(name)

    def test_native_spool_hardlink_alias_is_refused_or_detected_without_name_cleanup(self):
        probe = self.parent / "link-probe"
        probe.write_bytes(b"probe")
        try:
            os.link(probe, self.parent / "link-probe-alias")
        except OSError as exc:
            self.skipTest("Hardlink capability unavailable independently of symlinks: " + str(exc))
        spool = WindowsPrivateSpool(parent=self.parent)
        name, alias = self.parent / spool._leaf, self.parent / "spool-alias"
        linked = False
        try:
            spool.append(b"alias fixture")
            try:
                os.link(name, alias)
            except OSError as denied:
                self.assertIn(denied.winerror, (5, 32, 303))
                spool.seal()
            else:
                linked = True
                with self.assertRaises(SpoolError):
                    spool.seal()
        finally:
            spool.close()
        self.assert_name_gone_after_release(name)
        if linked:
            self.assertTrue(alias.exists(), "The spool must not remove a separately created alias by pathname")
            self.assertEqual(alias.read_bytes(), b"alias fixture")

    def test_native_spool_parent_reparse_does_not_redirect_creation_or_cleanup(self):
        from calendar_windows_native_fixture_support import NativeFixtureOps, READ_ATTRIBUTES, WRITE_ATTRIBUTES
        ops = NativeFixtureOps(self.parent, self.token)
        for name in ("save", "target", "probe"):
            (self.parent / name).mkdir()
        sentinel = self.parent / "target" / "keep"
        sentinel.write_bytes(b"preexisting target sentinel")
        probe = ops.open("probe", READ_ATTRIBUTES | WRITE_ATTRIBUTES, directory=True)
        try:
            try:
                ops.set_reparse(probe, "target"); ops.delete_reparse(probe)
            except OSError as exc:
                self.skipTest("Spool parent junction fixture unavailable; no fallback: " + str(exc))
        finally:
            ops.close(probe)
        parent_handle = ops.open("save", READ_ATTRIBUTES | WRITE_ATTRIBUTES, directory=True)
        mutation_errors = []
        observed_paths = []
        target_entries_while_held = []
        observation_errors = []
        class ParentMutation(NativeWindowsSpoolAdapter):
            def create_spool(self, parent, leaf):
                try:
                    ops.set_reparse(parent_handle, "target")
                except OSError as error:
                    mutation_errors.append(error)
                acquired = super().create_spool(parent, leaf)
                try:
                    # Observe before constructor cleanup can delete the owned
                    # temporary name and hide a transient redirected creation.
                    observed_paths.append(ops.final_path_for_observation(acquired.handle.value))
                    target_entries_while_held.append(sorted(p.name for p in (ops.root / "target").iterdir()))
                except BaseException as error:
                    observation_errors.append(str(error))
                    acquired.handle.close()
                    raise
                return acquired
        spool = None
        try:
            try:
                spool = WindowsPrivateSpool(parent=self.parent / "save", adapter=ParentMutation())
            except SpoolError:
                pass
            self.assertEqual(sorted(p.name for p in (self.parent / "target").iterdir()), ["keep"])
            self.assertEqual(sentinel.read_bytes(), b"preexisting target sentinel")
            self.assertEqual(observation_errors, [], "Creation-time observation did not complete")
            self.assertTrue(all(names == ["keep"] for names in target_entries_while_held),
                            "Target namespace gained a redirected spool while its handle was held")
            target_prefix = ("\\\\?\\" + str(self.parent / "target") + "\\").casefold()
            self.assertFalse(any(path.casefold().startswith(target_prefix) for path in observed_paths),
                             "Spool creation was redirected before delete-on-close hid its name")
            if mutation_errors:
                self.skipTest("Held spool-parent mutation did not execute: " + str(mutation_errors[0]))
        finally:
            if spool is not None:
                spool.close()
            if (self.parent / "save").lstat().st_file_attributes & 0x400:
                ops.delete_reparse(parent_handle)
            ops.close(parent_handle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
