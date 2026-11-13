"""Pure state/ownership tests, plus explicit Windows-only native smoke gates.

Fake tests do not establish native filesystem safety. Windows classes retain
the same discovered IDs on Linux, where they are explicitly skipped. Critical
reparse/mapping/process-crash tests listed in the inventory remain unimplemented.
"""
from __future__ import annotations

import ctypes as C
import hashlib
from itertools import product
from types import SimpleNamespace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import calendar_windows_io as win

from engine.calendar_windows_writer import (
    Acquisition, CHUNK_SIZE, NativeCreationError, NativeWindowsAdapter,
    ObjectState, SnapshotWriteError, WindowsDirectSnapshot,
)


class FakeHandle:
    def __init__(self, log, name, close_error=False):
        self.log, self.name = log, name
        self.close_error = close_error
        self.closed = 0

    def close(self):
        self.closed += 1
        self.log.append(("close", self.name))
        if isinstance(self.close_error, BaseException):
            raise self.close_error
        if self.close_error:
            raise OSError("injected close failure")


class FakeParent(FakeHandle):
    identity = (11, b"parent")

    def __init__(self, log):
        super().__init__(log, "parent")
        self.check_error = False

    def check(self):
        self.log.append(("parent_check",))
        if self.check_error:
            raise OSError("injected parent check failure")


class FakeAdapter:
    def __init__(self):
        self.log = []
        self.parent = FakeParent(self.log)
        self.file = FakeHandle(self.log, "file")
        self.content = bytearray()
        self.position = 0
        self.short = None
        self.write_result = None
        self.fail = None
        self.acquisition = (True, True)
        self.state = dict(identity=(11, b"new-file"), directory=False, links=1,
                          attributes=0, reparse_tag=0, delete_pending=False,
                          change_stamp=(1,))
        self.read_override = None
        self.read_calls = 0

    def open_existing_parent(self, path):
        self.log.append(("open_existing_parent", path))
        if self.fail == "open":
            raise OSError("missing or unsupported parent")
        return self.parent

    def create_new(self, parent, leaf):
        self.log.append(("create_new", leaf))
        if self.fail == "collision":
            raise NativeCreationError("existing final name", creation="conflict", resource_state="not_acquired")
        if self.fail == "unknown_create":
            raise NativeCreationError("uncertain creation")
        return Acquisition(self.file, *self.acquisition)

    def ensure_noninheritance(self, handle):
        self.log.append(("noninherit",))
        if self.fail == "noninherit":
            raise OSError("injected noninheritance failure")

    def inspect(self, handle):
        self.log.append(("inspect",))
        if self.fail == "inspect":
            raise OSError("injected query failure")
        return ObjectState(size=len(self.content), **self.state)

    def write(self, handle, data):
        self.log.append(("write", bytes(data)))
        if self.fail == "write":
            raise OSError("injected disk/quota failure")
        if self.write_result is not None:
            return self.write_result
        count = min(len(data), self.short) if self.short is not None else len(data)
        self.content.extend(data[:count])
        return count

    def flush(self, handle):
        self.log.append(("flush",))
        if self.fail == "flush":
            raise OSError("injected flush failure")

    def rewind(self, handle):
        self.log.append(("rewind",))
        self.position = 0
        if self.fail == "rewind":
            raise OSError("injected rewind failure")

    def read(self, handle, amount):
        self.log.append(("read", amount))
        self.read_calls += 1
        if self.fail == "read":
            raise OSError("injected read failure")
        if self.read_override is not None:
            return self.read_override(amount)
        data = bytes(self.content[self.position:self.position + amount])
        self.position += len(data)
        return data


class PureWriterTests(unittest.TestCase):
    def writer(self, adapter=None, data=b"complete container bytes", **kwargs):
        adapter = adapter or FakeAdapter()
        writer = WindowsDirectSnapshot("existing-parent", "new.snapshot", expected_size=len(data),
                                       expected_sha256=hashlib.sha256(data).hexdigest(),
                                       max_bytes=len(data), adapter=adapter, **kwargs)
        return adapter, writer

    def assert_closed_once(self, adapter, file=True):
        self.assertEqual(adapter.parent.closed, 1)
        self.assertEqual(adapter.file.closed, int(file))

    def test_complete_expected_bytes_and_release_receipt(self):
        adapter, writer = self.writer()
        with writer:
            writer.write_all([b"complete ", b"container bytes"])
            sealed = writer.seal()
            self.assertEqual(sealed.phase, "sealed")
            self.assertEqual(sealed.resources_released, "held")
        current = writer.receipt
        self.assertEqual(current.phase, "released")
        self.assertTrue(current.expected_bytes_verified)
        self.assertEqual(current.commit_mode, "validity_based")
        self.assertEqual(current.visibility_mode, "visible_during_write")
        self.assertEqual(current.resources_released, "confirmed")
        self.assertEqual(current.native_validation, "NOT_RUN")
        self.assertEqual(current.codec_validation, "caller_responsibility")
        self.assertEqual(sealed.resources_released, "held")  # Immutable observation, not a live promise.
        self.assert_closed_once(adapter)
        self.assertEqual(adapter.log[-2:], [("close", "file"), ("close", "parent")])

    def test_empty_encoding_has_bounded_eof_check(self):
        adapter, writer = self.writer(data=b"")
        with writer:
            writer.write_all([])
            writer.seal()
        self.assertIn(("read", 1), adapter.log)
        self.assertTrue(writer.receipt.expected_bytes_verified)

    def test_short_writes_are_completed_without_dropping_bytes(self):
        data = b"abcdefghijk"
        adapter, writer = self.writer(data=data)
        adapter.short = 2
        with writer:
            writer.write_all([data])
            writer.seal()
        self.assertEqual(bytes(adapter.content), data)
        self.assertEqual(writer.receipt.bytes_written, len(data))

    def test_native_chunks_and_readback_are_bounded(self):
        data = b"x" * (CHUNK_SIZE * 2 + 5)
        adapter, writer = self.writer(data=data)
        with writer:
            writer.write_all([data])
            writer.seal()
        self.assertLessEqual(max(len(x[1]) for x in adapter.log if x[0] == "write"), CHUNK_SIZE)
        self.assertLessEqual(max(x[1] for x in adapter.log if x[0] == "read"), CHUNK_SIZE)

    def test_zero_negative_boolean_and_oversize_write_counts_fail(self):
        for count in (0, -1, True, 1000):
            with self.subTest(count=count):
                adapter, writer = self.writer(data=b"abc")
                adapter.write_result = count
                with self.assertRaises(SnapshotWriteError):
                    with writer:
                        writer.write_all([b"abc"])
                self.assert_closed_once(adapter)
                self.assertFalse(writer.receipt.expected_bytes_verified)

    def test_oversized_chunk_rejected_before_native_write(self):
        adapter, writer = self.writer(data=b"ab")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"abc"])
        self.assertFalse(any(event[0] == "write" for event in adapter.log))

    def test_mutable_chunk_refused(self):
        adapter, writer = self.writer(data=b"ab")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([bytearray(b"ab")])
        self.assert_closed_once(adapter)

    def test_short_total_cannot_seal(self):
        adapter, writer = self.writer(data=b"abc")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"ab"])
                writer.seal()
        self.assertFalse(any(event[0] == "flush" for event in adapter.log))

    def test_independent_expected_digest_not_mutable_self_report(self):
        adapter, writer = self.writer(data=b"abc")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"def"])
                writer.seal()
        self.assertFalse(writer.receipt.expected_bytes_verified)

    def test_readback_rejects_growth_beyond_expected_size(self):
        adapter, writer = self.writer(data=b"abc")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"abc"])
                adapter.read_override = lambda amount: b"x" * amount
                writer.seal()
        self.assertEqual(adapter.read_calls, 1)

    def test_readback_rejects_short_eof(self):
        adapter, writer = self.writer(data=b"abc")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"abc"])
                adapter.read_override = lambda amount: b""
                writer.seal()

    def test_readback_rejects_native_overread(self):
        adapter, writer = self.writer(data=b"abc")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"abc"])
                adapter.read_override = lambda amount: b"x" * (amount + 1)
                writer.seal()

    def test_unknown_creation_outcome_is_not_reported_absent(self):
        adapter, writer = self.writer()
        adapter.fail = "unknown_create"
        with self.assertRaises(SnapshotWriteError) as caught:
            with writer:
                self.fail("must not enter")
        self.assertEqual(caught.exception.receipt.creation, "unknown")
        self.assertTrue(caught.exception.receipt.named_output_may_remain)
        self.assert_closed_once(adapter, file=False)

    def test_existing_name_conflict_never_opens_or_writes_existing_file(self):
        adapter, writer = self.writer()
        adapter.fail = "collision"
        with self.assertRaises(SnapshotWriteError) as caught:
            with writer:
                self.fail("must not enter")
        self.assertEqual(caught.exception.receipt.creation, "conflict")
        self.assertFalse(any(x[0] in ("inspect", "write") for x in adapter.log))
        self.assert_closed_once(adapter, file=False)

    def test_open_parent_failure_never_attempts_creation(self):
        adapter, writer = self.writer()
        adapter.fail = "open"
        with self.assertRaises(SnapshotWriteError):
            with writer:
                self.fail("must not enter")
        self.assertEqual(writer.receipt.creation, "not_attempted")
        self.assertEqual(writer.receipt.resources_released, "unconfirmed")
        self.assertEqual(adapter.parent.closed, 0)
        self.assertFalse(any(x[0] == "create_new" for x in adapter.log))

    def test_partial_parent_acquisition_close_failure_is_unconfirmed(self):
        adapter, writer = self.writer()
        hidden = FakeHandle(adapter.log, "unreturned-parent", close_error=True)
        def partial_open(path):
            hidden.close()
        adapter.open_existing_parent = partial_open
        with self.assertRaises(SnapshotWriteError) as caught:
            with writer:
                self.fail("must not enter")
        self.assertEqual(hidden.closed, 1)
        self.assertEqual(caught.exception.receipt.resources_released, "unconfirmed")
        self.assertEqual(caught.exception.receipt.creation, "not_attempted")
        self.assertIn("injected close failure", str(caught.exception.__cause__))

    def test_created_but_unreturned_file_with_failed_close_remains_uncertain(self):
        adapter, writer = self.writer()
        hidden = FakeHandle(adapter.log, "unreturned-file", close_error=True)
        def create_without_return(parent, leaf):
            try:
                hidden.close()
            except OSError as exc:
                raise NativeCreationError("created before verification/close failed", creation="created") from exc
        adapter.create_new = create_without_return
        with self.assertRaises(SnapshotWriteError) as caught:
            with writer:
                self.fail("must not enter")
        self.assertEqual(hidden.closed, 1)
        self.assertEqual(adapter.parent.closed, 1)
        self.assertEqual(caught.exception.receipt.resources_released, "unconfirmed")
        self.assertEqual(caught.exception.receipt.creation, "created")
        self.assertTrue(caught.exception.receipt.named_output_may_remain)

    def test_post_create_noninheritance_failure_closes_acquired_handles(self):
        adapter, writer = self.writer()
        adapter.fail = "noninherit"
        with self.assertRaises(SnapshotWriteError):
            with writer:
                self.fail("must not enter")
        self.assertEqual(writer.receipt.creation, "created")
        self.assert_closed_once(adapter)

    def test_unexpected_native_success_never_writes_or_adopts_existing(self):
        for flags in ((False, True), (True, False)):
            with self.subTest(flags=flags):
                adapter, writer = self.writer()
                adapter.acquisition = flags
                with self.assertRaises(SnapshotWriteError):
                    with writer:
                        self.fail("must not enter")
                self.assertEqual(writer.receipt.creation, "unknown")
                self.assert_closed_once(adapter)
                self.assertFalse(any(x[0] == "write" for x in adapter.log))

    def test_wrong_type_reparse_links_volume_and_delete_state_refused(self):
        for update in ({"directory": True}, {"links": 2}, {"attributes": 0x400},
                       {"reparse_tag": 1}, {"delete_pending": True},
                       {"identity": (99, b"outside-volume")}):
            with self.subTest(update=update):
                adapter, writer = self.writer()
                adapter.state.update(update)
                with self.assertRaises(SnapshotWriteError):
                    with writer:
                        self.fail("must not enter")
                self.assert_closed_once(adapter)

    def test_native_created_file_not_empty_is_refused(self):
        adapter, writer = self.writer()
        adapter.content.extend(b"unexpected bytes")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                self.fail("must not enter")
        self.assert_closed_once(adapter)

    def test_file_identity_change_before_seal_refused(self):
        adapter, writer = self.writer(data=b"abc")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"abc"])
                adapter.state["identity"] = (11, b"other")
                writer.seal()

    def test_metadata_change_during_readback_refused(self):
        adapter, writer = self.writer(data=b"abc")
        original = adapter.read
        def read_and_change(handle, amount):
            block = original(handle, amount)
            adapter.state["change_stamp"] = (2,)
            return block
        adapter.read = read_and_change
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"abc"])
                writer.seal()

    def test_each_io_failure_preserves_close_only_behavior(self):
        for operation in ("write", "flush", "rewind", "read", "inspect"):
            with self.subTest(operation=operation):
                adapter, writer = self.writer(data=b"abc")
                with self.assertRaises(SnapshotWriteError):
                    with writer:
                        if operation == "write":
                            adapter.fail = operation
                        writer.write_all([b"abc"])
                        adapter.fail = operation
                        writer.seal()
                self.assert_closed_once(adapter)
                self.assertFalse(writer.receipt.expected_bytes_verified)

    def test_parent_revalidation_failure_refused(self):
        adapter, writer = self.writer(data=b"abc")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"abc"])
                adapter.parent.check_error = True
                writer.seal()
        self.assert_closed_once(adapter)

    def test_cancellation_closes_every_resource(self):
        adapter, writer = self.writer(data=b"abc")
        with self.assertRaises(KeyboardInterrupt):
            with writer:
                writer.write_all([b"a"])
                raise KeyboardInterrupt()
        self.assert_closed_once(adapter)
        self.assertEqual(writer.receipt.phase, "failed")

    def test_unsealed_normal_exit_is_not_success(self):
        adapter, writer = self.writer(data=b"abc")
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"abc"])
        self.assert_closed_once(adapter)

    def test_sealed_object_rejects_later_write_or_second_seal(self):
        adapter, writer = self.writer(data=b"abc")
        with writer:
            writer.write_all([b"abc"])
            writer.seal()
            with self.assertRaises(ValueError):
                writer.write_all([b"x"])
            with self.assertRaises(ValueError):
                writer.seal()
        self.assertEqual(writer.receipt.phase, "released")

    def test_close_failure_does_not_skip_other_resource_or_retry(self):
        adapter, writer = self.writer(data=b"abc")
        adapter.file.close_error = True
        with self.assertRaises(SnapshotWriteError) as caught:
            with writer:
                writer.write_all([b"abc"])
                writer.seal()
        self.assert_closed_once(adapter)
        self.assertEqual(caught.exception.receipt.resources_released, "unconfirmed")
        self.assertTrue(caught.exception.receipt.expected_bytes_verified)
        self.assertEqual(caught.exception.receipt.phase, "failed_after_seal")
        writer._close_resources()
        self.assert_closed_once(adapter)

    def _assert_cleanup_case(self, phase, primary, cleanups, *, sealed=True, diagnostics=True):
        adapter, writer = self.writer(data=b"abc")
        adapter.file.close_error, adapter.parent.close_error = cleanups
        if phase == "acquire":
            def interrupted(handle):
                raise primary
            adapter.ensure_noninheritance = interrupted
        caught = None
        try:
            with writer:
                writer.write_all([b"abc"])
                if sealed:
                    writer.seal()
                if primary is not None:
                    raise primary
        except BaseException as error:
            caught = error
        candidates = [e for e in (primary, *cleanups) if e is not None]
        expected = next((e for e in candidates if not isinstance(e, Exception)),
                        primary if primary is not None else next(iter(candidates), None))
        if expected is None:
            if sealed:
                self.assertIsNone(caught)
            else:
                self.assertIsInstance(caught, SnapshotWriteError)
        elif not isinstance(expected, Exception) or (phase != "acquire" and expected is primary):
            self.assertIs(caught, expected)
        else:
            self.assertIsInstance(caught, SnapshotWriteError)
            self.assertIs(caught.__cause__, expected)
        if caught is not None:
            self.assertIsNot(caught.__cause__, caught)
        if diagnostics and expected is not None and not isinstance(expected, Exception):
            self.assertEqual(expected.receipt, writer.receipt)
        failed_close = any(e is not None for e in cleanups)
        self.assertEqual(writer.receipt.resources_released, "unconfirmed" if failed_close else "confirmed")
        self.assert_closed_once(adapter)
        writer._close_resources()
        self.assert_closed_once(adapter)
        self.assertEqual(writer.receipt.resources_released, "unconfirmed" if failed_close else "confirmed")
        expected_phase = ("failed" if primary is not None or not sealed else
                          "failed_after_seal" if failed_close else "released")
        self.assertEqual(writer.receipt.phase, expected_phase)
        return caught

    def test_sealed_cleanup_cancellation_is_not_wrapped(self):
        self._assert_cleanup_case("context", None, (KeyboardInterrupt("file close"), None))

    def test_cleanup_cancellation_overrides_ordinary_body_error(self):
        self._assert_cleanup_case("context", ValueError("body"), (KeyboardInterrupt("file close"), None))

    def test_cleanup_cancellation_overrides_ordinary_acquisition_error(self):
        self._assert_cleanup_case("acquire", OSError("acquisition"), (None, KeyboardInterrupt("parent close")))

    def test_acquisition_cleanup_full_priority_matrix(self):
        kinds = (None, OSError, KeyboardInterrupt, SystemExit, GeneratorExit)
        for pk, fk, rk in product(kinds[1:], kinds, kinds):
            with self.subTest(primary=pk, file=fk, parent=rk):
                self._assert_cleanup_case("acquire", pk("primary"),
                                          (fk("file") if fk else None, rk("parent") if rk else None))

    def test_context_cleanup_full_priority_matrix(self):
        kinds = (None, OSError, KeyboardInterrupt, SystemExit, GeneratorExit)
        for pk, fk, rk in product(kinds, kinds, kinds):
            with self.subTest(primary=pk, file=fk, parent=rk):
                self._assert_cleanup_case("context", pk("primary") if pk else None,
                                          (fk("file") if fk else None, rk("parent") if rk else None))

    def test_same_cancellation_identity_never_becomes_self_cause(self):
        for phase in ("acquire", "context"):
            with self.subTest(phase=phase):
                error = KeyboardInterrupt("same instance")
                self._assert_cleanup_case(phase, error, (error, error))

    def test_unsealed_cleanup_cancellation_still_propagates(self):
        self._assert_cleanup_case("context", None, (OSError("file"), GeneratorExit("parent")), sealed=False)

    def test_cancellation_rejecting_diagnostics_is_never_replaced(self):
        class RejectDiagnostics(KeyboardInterrupt):
            def __setattr__(self, name, value):
                if name == "receipt":
                    raise AttributeError("receipt rejected")
                super().__setattr__(name, value)
            def add_note(self, note):
                raise AttributeError("note rejected")
        for phase in ("acquire", "context"):
            for primary_cancel in (False, True):
                with self.subTest(phase=phase, primary_cancel=primary_cancel):
                    cancellation = RejectDiagnostics("original cancellation")
                    primary = cancellation if primary_cancel else OSError("primary")
                    cleanup = OSError("close") if primary_cancel else cancellation
                    self._assert_cleanup_case(phase, primary, (cleanup, None), diagnostics=False)

    def test_native_adapter_parent_cleanup_priority_without_native_calls(self):
        kinds = (OSError, KeyboardInterrupt, SystemExit, GeneratorExit)
        for pk, ck in product(kinds, (None, *kinds)):
            with self.subTest(primary=pk, cleanup=ck):
                primary, cleanup = pk("acquisition"), ck("cleanup") if ck else None
                calls = []
                class Parent:
                    def __enter__(self):
                        raise primary
                    def close(self):
                        calls.append("close")
                        if cleanup is not None:
                            raise cleanup
                adapter = object.__new__(NativeWindowsAdapter)
                adapter.reader = SimpleNamespace(WindowsProjectDirectory=lambda *a, **kw: Parent())
                expected = primary if not isinstance(primary, Exception) or cleanup is None or isinstance(cleanup, Exception) else cleanup
                with self.assertRaises(type(expected)) as caught:
                    adapter.open_existing_parent("fake-owned-parent")
                self.assertIs(caught.exception, expected)
                self.assertIsNot(caught.exception.__cause__, caught.exception)
                self.assertEqual(calls, ["close"])

    def test_native_parent_diagnostic_rendering_and_hooks_preserve_selection(self):
        renders = []
        class BadRenderError(OSError):
            def __repr__(self):
                renders.append("repr")
                raise RuntimeError("repr rejected")
            def __str__(self):
                renders.append("str")
                raise RuntimeError("str rejected")
        class BadRenderCancel(SystemExit):
            def __repr__(self):
                renders.append("repr")
                raise RuntimeError("repr rejected")
            def __str__(self):
                renders.append("str")
                raise RuntimeError("str rejected")
        class RejectNoteError(OSError):
            def add_note(self, note):
                raise AttributeError("note rejected")
        class RejectNoteCancellation(OSError):
            def add_note(self, note):
                raise KeyboardInterrupt("diagnostic interruption")
        for pk, ck in product((OSError, KeyboardInterrupt, SystemExit, RejectNoteError, RejectNoteCancellation),
                              (BadRenderError, BadRenderCancel)):
            with self.subTest(primary=pk.__name__, cleanup=ck.__name__):
                primary, cleanup = pk("primary"), ck("cleanup")
                calls = []
                class Parent:
                    def __enter__(self):
                        raise primary
                    def close(self):
                        calls.append("close")
                        raise cleanup
                adapter = object.__new__(NativeWindowsAdapter)
                adapter.reader = SimpleNamespace(WindowsProjectDirectory=lambda *a, **kw: Parent())
                expected = primary if not isinstance(primary, Exception) or isinstance(cleanup, Exception) else cleanup
                with self.assertRaises(type(expected)) as caught:
                    adapter.open_existing_parent("fake-owned-parent")
                self.assertIs(caught.exception, expected)
                self.assertIsNot(caught.exception.__cause__, caught.exception)
                self.assertEqual(calls, ["close"])
                self.assertEqual(renders, [])

    def test_ordinary_body_diagnostic_hooks_do_not_replace_primary(self):
        for failure_kind in (AttributeError, KeyboardInterrupt):
            with self.subTest(diagnostic_failure=failure_kind.__name__):
                class RejectNote(OSError):
                    def add_note(self, note):
                        raise failure_kind("note rejected")
                self._assert_cleanup_case("context", RejectNote("primary"), (OSError("file"), None))

    def test_names_and_size_digest_validated_before_any_native_call(self):
        for name in ("", ".", "..", "a/b", "a\\b", "a:b", "CON", "NUL.txt", "a.", "a ", "\ud800"):
            with self.subTest(name=repr(name)), self.assertRaises(ValueError):
                WindowsDirectSnapshot("parent", name, expected_size=0,
                                      expected_sha256=hashlib.sha256(b"").hexdigest(), max_bytes=0)
        for size, budget, digest in ((True, 1, "0"*64), (2, 1, "0"*64),
                                     (-1, 1, "0"*64), (1, 1, "X"*64)):
            with self.subTest(size=size, budget=budget, digest=digest), self.assertRaises(ValueError):
                WindowsDirectSnapshot("parent", "x", expected_size=size,
                                      expected_sha256=digest, max_bytes=budget)

    def test_native_adapter_fails_closed_off_windows(self):
        if os.name == "nt":
            self.skipTest("This test specifically asserts non-Windows refusal")
        with self.assertRaises(OSError):
            NativeWindowsAdapter()


class PureNativeCreateContractTests(unittest.TestCase):
    """Inspect real adapter call arguments; no Windows API is executed here."""

    def _assert_create_contract(self, adapter_type, method, access, attributes, options):
        for leaf in ("new.snapshot", "世界🌸.snapshot"):
            with self.subTest(leaf=leaf):
                parent = SimpleNamespace(value=0x12345678)
                calls, closes = [], []
                def create(result, desired, obj, iosb, allocation, attrs, share,
                           disposition, create_options, ea, ea_length):
                    calls.append(True)
                    self.assertEqual(desired, access)
                    self.assertTrue(desired & win._SYNCHRONIZE)
                    self.assertEqual((attrs, share, disposition, create_options),
                                     (attributes, 0, 2, options))
                    self.assertEqual((allocation, ea, ea_length), (None, None, 0))
                    object_attributes = C.cast(obj, C.POINTER(win._ObjectAttributes)).contents
                    self.assertEqual(object_attributes.Length, C.sizeof(win._ObjectAttributes))
                    self.assertEqual(object_attributes.RootDirectory, parent.value)
                    self.assertEqual(object_attributes.Attributes, 0x40)
                    self.assertIsNone(object_attributes.SecurityDescriptor)
                    self.assertIsNone(object_attributes.SecurityQualityOfService)
                    name = object_attributes.ObjectName.contents
                    encoded = leaf.encode("utf-16-le")
                    self.assertEqual((name.Length, name.MaximumLength),
                                     (len(encoded), len(encoded) + 2))
                    self.assertEqual(C.string_at(name.Buffer, name.MaximumLength),
                                     encoded + b"\0\0")
                    C.cast(result, C.POINTER(win._HANDLE)).contents.value = 0x87654321
                    status = C.cast(iosb, C.POINTER(win._IoStatus)).contents
                    status.result.Status, status.Information = 0, 2
                    return 0
                adapter = object.__new__(adapter_type)
                adapter.reader = win
                adapter.native = SimpleNamespace(ntdll=SimpleNamespace(NtCreateFile=create),
                                                 close=closes.append)
                acquired = getattr(adapter, method)(parent, leaf)
                self.assertEqual(calls, [True])
                try:
                    self.assertTrue(acquired.final_success)
                    self.assertTrue(acquired.newly_created)
                    self.assertEqual(acquired.handle.value, 0x87654321)
                finally:
                    acquired.handle.close()
                self.assertEqual(closes, [0x87654321])

    def test_writer_create_requests_synchronize_without_delete_or_sharing(self):
        self._assert_create_contract(NativeWindowsAdapter, "create_new",
                                     0xC0100000, 0x80, 0x200060)

    def test_spool_create_requests_synchronize_and_owned_delete_on_close(self):
        from engine.calendar_windows_spool import NativeWindowsSpoolAdapter
        self._assert_create_contract(NativeWindowsSpoolAdapter, "create_spool",
                                     0xC0110000, 0x100, 0x201060)

    def test_native_create_binding_and_fixed_width_structure_layout(self):
        # A fake DLL exposes binding declarations, never emulates kernel success.
        class FakeDLL:
            def __init__(self):
                self.functions = {}
            def __getattr__(self, name):
                return self.functions.setdefault(name, lambda *args: None)
        kernel, ntdll = FakeDLL(), FakeDLL()
        with patch.object(win.os, "name", "nt"), patch.object(
                win.C, "WinDLL", side_effect=[kernel, ntdll], create=True):
            native = win._Native()
        function = native.ntdll.NtCreateFile
        self.assertIs(function.restype, C.c_int32)
        self.assertEqual(function.argtypes, [
            C.POINTER(win._HANDLE), C.c_uint32, C.POINTER(win._ObjectAttributes),
            C.POINTER(win._IoStatus), C.c_void_p, C.c_uint32, C.c_uint32,
            C.c_uint32, C.c_uint32, C.c_void_p, C.c_uint32])
        pointer_size = C.sizeof(C.c_void_p)
        self.assertIn(pointer_size, (4, 8))
        self.assertEqual(C.sizeof(win._NTSTATUS), 4)
        self.assertEqual(C.sizeof(win._DWORD), 4)
        self.assertEqual(C.sizeof(win._UnicodeString), 16 if pointer_size == 8 else 8)
        self.assertEqual(C.sizeof(win._ObjectAttributes), 48 if pointer_size == 8 else 24)
        self.assertEqual(C.sizeof(win._IoStatus), 2 * pointer_size)
        self.assertEqual(win._UnicodeString.Buffer.offset, pointer_size)
        self.assertEqual(win._ObjectAttributes.RootDirectory.offset, pointer_size)
        self.assertEqual(win._ObjectAttributes.ObjectName.offset, 2 * pointer_size)
        self.assertEqual(win._IoStatus.Information.offset, pointer_size)



def native_data_open_error(path, access):
    """Observe the actual Win32 data-open result; never accept generic errno.

    OPEN_EXISTING and full sharing avoid creating a file or introducing our own
    sharing conflict. Unexpected success is returned as None after closing the
    owned handle, so the caller's exact denial assertion must fail.
    """
    kernel = C.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [C.c_wchar_p, C.c_uint32, C.c_uint32, C.c_void_p,
                       C.c_uint32, C.c_uint32, C.c_void_p]
    create.restype = C.c_void_p
    close = kernel.CloseHandle
    close.argtypes, close.restype = [C.c_void_p], C.c_int
    handle = create(str(path), access, 7, None, 3, 0x80, None)
    if handle == C.c_void_p(-1).value:
        return C.get_last_error()  # Capture before any further Windows call.
    if handle is None:
        raise AssertionError("CreateFileW returned NULL rather than its documented failure sentinel")
    if not close(handle):
        raise C.WinError(C.get_last_error())
    return None


class PureNativeDataOpenProbeTests(unittest.TestCase):
    """Probe plumbing only; fake DLL outcomes are not native safety evidence."""

    def probe(self, handle, error, *, access=0x80000000, close_ok=True):
        calls, closes = [], []
        def create(*args):
            calls.append(args)
            return handle
        def close(value):
            closes.append(value)
            return close_ok
        kernel = SimpleNamespace(CreateFileW=create, CloseHandle=close)
        failure = OSError("owned probe handle close failed")
        with patch.object(C, "WinDLL", return_value=kernel, create=True), \
                patch.object(C, "get_last_error", return_value=error, create=True), \
                patch.object(C, "WinError", return_value=failure, create=True):
            try:
                result = native_data_open_error("owned-fixture-file", access)
            finally:
                self.assertEqual(calls, [("owned-fixture-file", access, 7, None, 3, 0x80, None)])
                self.assertEqual(create.argtypes, [C.c_wchar_p, C.c_uint32, C.c_uint32,
                                                   C.c_void_p, C.c_uint32, C.c_uint32, C.c_void_p])
                self.assertIs(create.restype, C.c_void_p)
                self.assertEqual(close.argtypes, [C.c_void_p])
                self.assertIs(close.restype, C.c_int)
        return result, closes

    def test_probe_preserves_exact_native_error_without_closing_invalid_handle(self):
        for access in (0x80000000, 0xC0000000):
            for error in (2, 5, 32, 303):
                with self.subTest(access=access, error=error):
                    result, closes = self.probe(C.c_void_p(-1).value, error, access=access)
                    self.assertEqual(result, error)
                    self.assertEqual(closes, [])

    def test_probe_success_closes_owned_handle_and_is_not_denial(self):
        result, closes = self.probe(0x12345678, 32)
        self.assertIsNone(result)
        self.assertEqual(closes, [0x12345678])

    def test_probe_null_handle_is_not_misclassified_as_sharing_denial(self):
        with self.assertRaisesRegex(AssertionError, "returned NULL"):
            self.probe(None, 32)

    def test_probe_close_failure_is_not_misclassified_as_sharing_denial(self):
        with self.assertRaisesRegex(OSError, "close failed"):
            self.probe(0x12345678, 6, close_ok=False)


@unittest.skipUnless(os.name == "nt", "Native Windows execution required; not a passed native gate")
class NativeWindowsSmokeTests(unittest.TestCase):
    """Basic native gates only. Reparse/concurrency inventory is still incomplete."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="worldbloom-windows-prototype-")
        self.addCleanup(self.temp.cleanup)  # Fixture-owned test cleanup, not writer rollback.
        self.parent = Path(self.temp.name)
        self.adapter = NativeWindowsAdapter()

    def writer(self, leaf="new.snapshot", data=b"native fixture bytes"):
        return WindowsDirectSnapshot(self.parent, leaf, expected_size=len(data),
                                     expected_sha256=hashlib.sha256(data).hexdigest(),
                                     max_bytes=len(data), adapter=self.adapter)


    def test_native_data_open_probe_distinguishes_success_from_missing_file(self):
        path = self.parent / "ordinary-probe.dat"
        path.write_bytes(b"ordinary owned fixture")
        self.assertIsNone(native_data_open_error(path, 0x80000000))
        self.assertIsNone(native_data_open_error(path, 0xC0000000))
        self.assertEqual(path.read_bytes(), b"ordinary owned fixture")
        self.assertEqual(native_data_open_error(self.parent / "missing-probe.dat", 0x80000000), 2)

    def test_native_create_write_seal_read_after_close(self):
        data = b"native fixture bytes"
        writer = self.writer(data=data)
        with writer:
            writer.write_all([data])
            writer.seal()
        self.assertEqual((self.parent / "new.snapshot").read_bytes(), data)
        self.assertTrue(writer.receipt.expected_bytes_verified)

    def test_native_existing_file_collision_preserves_bytes(self):
        path = self.parent / "new.snapshot"
        path.write_bytes(b"preexisting protected bytes")
        with self.assertRaises(SnapshotWriteError):
            with self.writer():
                self.fail("existing file must not be acquired")
        self.assertEqual(path.read_bytes(), b"preexisting protected bytes")

    def test_native_existing_directory_collision_preserves_child(self):
        path = self.parent / "new.snapshot"
        path.mkdir()
        (path / "keep").write_bytes(b"keep")
        with self.assertRaises(SnapshotWriteError):
            with self.writer():
                self.fail("existing directory must not be acquired")
        self.assertEqual((path / "keep").read_bytes(), b"keep")

    def test_native_normal_read_denied_while_writer_retained(self):
        writer = self.writer()
        with writer:
            writer.write_all([b"native fixture bytes"])
            with self.assertRaises(OSError) as denied:
                (self.parent / "new.snapshot").read_bytes()
            self.assertEqual(native_data_open_error(self.parent / "new.snapshot", 0x80000000), 32,
                             "Expected an actual Windows sharing violation")
            writer.seal()
            with self.assertRaises(OSError) as denied:
                (self.parent / "new.snapshot").read_bytes()
            self.assertEqual(native_data_open_error(self.parent / "new.snapshot", 0x80000000), 32,
                             "Expected sharing denial even after seal, before close")

    def test_native_partial_file_remains_and_next_create_refuses(self):
        writer = self.writer()
        with self.assertRaises(SnapshotWriteError):
            with writer:
                writer.write_all([b"native"])
        path = self.parent / "new.snapshot"
        self.assertEqual(path.read_bytes(), b"native")
        with self.assertRaises(SnapshotWriteError):
            with self.writer():
                self.fail("must not resume an existing partial file")
        self.assertEqual(path.read_bytes(), b"native")


if __name__ == "__main__":
    unittest.main(verbosity=2)
