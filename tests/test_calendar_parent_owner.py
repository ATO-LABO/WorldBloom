"""Private run-parent lifetime tests. Fake Windows is not native qualification."""
import copy
import pickle
import hashlib
from itertools import product
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from engine import calendar_parent_owner as ownership
from engine import calendar_container_linux as linux
from engine import calendar_windows_io as windows_io
from engine.calendar_windows_writer import WindowsDirectSnapshot, SnapshotWriteError, NativeCreationError, NativeWindowsAdapter, _BorrowedReaderParent
from test_calendar_windows_writer import FakeAdapter, FakeHandle

DATA = b'run-owned generation\r\n'
META = dict(expected_size=len(DATA), expected_sha256=hashlib.sha256(DATA).hexdigest(), max_bytes=len(DATA))
LINUX = sys.platform == 'linux' and hasattr(os, 'O_TMPFILE')


class RejectDiagnostics(KeyboardInterrupt):
    def __setattr__(self, name, value):
        if name in ('receipt', 'parent_release_state'):
            raise RuntimeError('diagnostic setter failure')
        super().__setattr__(name, value)

    def add_note(self, note):
        raise SystemExit('diagnostic note failure')


class MultiAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.parent.value = 271
        self.files = []
        self.parents_used = []
        self.file_error = None

    def create_new(self, parent, leaf):
        self.parents_used.append((parent.value, parent.identity))
        self.file = FakeHandle(self.log, leaf, self.file_error)
        self.files.append(self.file)
        self.content = bytearray()
        self.position = 0
        return super().create_new(parent, leaf)


def fake_owner(adapter=None):
    adapter = adapter or MultiAdapter()
    return adapter, ownership._ExistingParentOwner('selected once', backend='windows', adapter=adapter)


class ParentOwnerPureTests(unittest.TestCase):
    def test_several_borrows_unregister_only_and_close_once(self):
        adapter, owner = fake_owner()
        first, second = owner._borrow('windows'), owner._borrow('windows')
        self.assertEqual(owner.active_borrows, 2)
        self.assertEqual(first.value, 271)
        self.assertEqual(first.identity, owner.identity)
        first.close(); first.close()
        self.assertEqual(owner.active_borrows, 1)
        self.assertEqual(adapter.parent.closed, 0)
        second.close()
        self.assertEqual(owner.close(), 'CLOSED')
        self.assertEqual(owner.close(), 'CLOSED')
        self.assertEqual(adapter.parent.closed, 1)

    def test_close_rejected_while_borrow_active_keeps_owner_open(self):
        adapter, owner = fake_owner()
        borrow = owner._borrow('windows')
        with self.assertRaisesRegex(ValueError, 'active borrows'):
            owner.close()
        self.assertEqual(owner.state, 'OPEN')
        self.assertEqual(adapter.parent.closed, 0)
        borrow.check(); borrow.close(); owner.close()

    def test_wrong_owner_backend_and_stale_epoch_rejected(self):
        adapter, owner = fake_owner()
        _, other = fake_owner()
        borrow = owner._borrow('windows')
        try:
            for operation in (lambda: borrow._require(other, 'windows'),
                              lambda: owner._validate(borrow, 'linux'),
                              lambda: other._validate(borrow, 'windows'),
                              lambda: borrow.fd):
                with self.subTest(operation=operation), self.assertRaises(ValueError):
                    operation()
            epoch = borrow._epoch
            borrow._epoch = object()
            with self.assertRaises(ValueError): borrow.check()
            with self.assertRaises(ValueError): borrow.close()
            self.assertEqual(owner.active_borrows, 1)
            borrow._epoch = epoch
        finally:
            borrow.close(); owner.close(); other.close()

    def test_forged_registration_rejected_even_with_owner_epoch(self):
        _, owner = fake_owner()
        forged = ownership._ParentBorrow(owner, owner._epoch)
        try:
            with self.assertRaises(ValueError): forged.check()
            with self.assertRaises(ValueError): forged.close()
            self.assertEqual(owner.active_borrows, 0)
        finally: owner.close()

    def test_released_and_closed_capabilities_reject_operations(self):
        _, owner = fake_owner()
        borrow = owner._borrow('windows'); borrow.close()
        for operation in (borrow.check, lambda: borrow.value, lambda: borrow.identity, lambda: borrow._adapter):
            with self.subTest(operation=operation), self.assertRaises(ValueError): operation()
        owner.close()
        with self.assertRaises(ValueError): owner._borrow('windows')
        with self.assertRaises(ValueError): owner.__enter__()

    def test_identity_backend_state_are_read_only(self):
        _, owner = fake_owner()
        try:
            for name in ('identity', 'backend', 'state', 'active_borrows'):
                with self.subTest(name=name), self.assertRaises(AttributeError): setattr(owner, name, None)
            self.assertFalse(hasattr(owner, '__fspath__'))
        finally: owner.close()

    def test_pid_mismatch_rejects_use_release_and_close_before_native_calls(self):
        adapter, owner = fake_owner(); borrow = owner._borrow('windows')
        before = list(adapter.log)
        with patch.object(ownership.os, 'getpid', return_value=owner._pid + 1):
            for operation in (borrow.check, borrow.close, lambda: borrow.value,
                              lambda: owner._borrow('windows'), owner._check_native, owner.close):
                with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, 'another process'):
                    operation()
        self.assertEqual(adapter.log, before)
        self.assertEqual(owner.active_borrows, 1)
        borrow.close(); owner.close()

    def test_close_uncertainty_sticky_no_numeric_handle_retry(self):
        for kind in (OSError, KeyboardInterrupt, SystemExit, RejectDiagnostics):
            with self.subTest(kind=kind):
                adapter, owner = fake_owner(); failure = kind('close')
                adapter.parent.close_error = failure
                with self.assertRaises(BaseException) as caught: owner.close()
                self.assertIs(caught.exception, failure)
                self.assertEqual(owner.state, 'RELEASE_UNCERTAIN')
                self.assertEqual(owner.close(), 'RELEASE_UNCERTAIN')
                self.assertEqual(adapter.parent.closed, 1)
                with self.assertRaises(ValueError): owner._borrow('windows')

    def test_close_observes_closing_and_detaches_before_callback(self):
        adapter, owner = fake_owner()
        def close():
            self.assertEqual(owner.state, 'CLOSING')
            self.assertIsNone(owner._parent)
            with self.assertRaises(ValueError): owner._borrow('windows')
            with self.assertRaises(ValueError): owner.close()
        with patch.object(adapter.parent, 'close', side_effect=close) as mocked:
            owner.close()
        mocked.assert_called_once()
        self.assertEqual(owner.state, 'CLOSED')

    def test_owner_context_preserves_primary_or_first_cancellation(self):
        for primary_kind, cleanup_kind in product((OSError, KeyboardInterrupt, SystemExit, RejectDiagnostics), repeat=2):
            with self.subTest(primary=primary_kind, cleanup=cleanup_kind):
                adapter, owner = fake_owner()
                primary, cleanup = primary_kind('body'), cleanup_kind('close')
                adapter.parent.close_error = cleanup
                expected = primary if not isinstance(primary, Exception) or isinstance(cleanup, Exception) else cleanup
                with self.assertRaises(BaseException) as caught:
                    with owner: raise primary
                self.assertIs(caught.exception, expected)
                self.assertEqual(owner.state, 'RELEASE_UNCERTAIN')
                owner.close(); self.assertEqual(adapter.parent.closed, 1)

    def test_constructor_check_failure_closes_returned_owner_once(self):
        for primary_kind, cleanup_kind in product((OSError, KeyboardInterrupt, RejectDiagnostics), (None, OSError, SystemExit)):
            with self.subTest(primary=primary_kind, cleanup=cleanup_kind):
                adapter = MultiAdapter(); primary = primary_kind('check')
                cleanup = cleanup_kind('close') if cleanup_kind else None
                adapter.parent.close_error = cleanup
                with patch.object(adapter.parent, 'check', side_effect=primary):
                    with self.assertRaises(BaseException) as caught: fake_owner(adapter)
                expected = cleanup if cleanup is not None and isinstance(primary, Exception) and not isinstance(cleanup, Exception) else primary
                self.assertIs(caught.exception, expected)
                self.assertEqual(adapter.parent.closed, 1)

    def test_constructor_unreturned_acquisition_reports_unknown(self):
        adapter = MultiAdapter(); error = OSError('uncertain acquisition')
        with patch.object(adapter, 'open_existing_parent', side_effect=error):
            with self.assertRaises(OSError) as caught: fake_owner(adapter)
        self.assertIs(caught.exception, error)
        self.assertEqual(error.parent_release_state, 'RELEASE_UNCERTAIN')
        self.assertEqual(adapter.parent.closed, 0)

    def test_failed_enter_consumes_owner_but_preserves_cancellation(self):
        adapter, owner = fake_owner(); primary = KeyboardInterrupt('enter')
        adapter.parent.close_error = OSError('close')
        with patch.object(adapter.parent, 'check', side_effect=primary):
            with self.assertRaises(KeyboardInterrupt) as caught: owner.__enter__()
        self.assertIs(caught.exception, primary)
        self.assertEqual(owner.state, 'RELEASE_UNCERTAIN')
        owner.close(); self.assertEqual(adapter.parent.closed, 1)

    def test_owner_and_borrow_reject_copy_pickle_and_transfer(self):
        _, owner = fake_owner(); borrow = owner._borrow('windows')
        try:
            for target, operation in product((owner, borrow), (copy.copy, copy.deepcopy, pickle.dumps)):
                with self.subTest(target=type(target), operation=operation), self.assertRaises(TypeError):
                    operation(target)
        finally: borrow.close(); owner.close()

    def test_native_windows_ledger_teardown_attempts_each_anchor_once(self):
        for first_kind, second_kind in product((OSError, RejectDiagnostics), (OSError, SystemExit)):
            with self.subTest(first=first_kind, second=second_kind):
                log = []
                failures = [first_kind('leaf'), second_kind('ancestor')]
                def close(value):
                    log.append(value)
                    if value == 103: raise failures[0]
                    if value == 102: raise failures[1]
                states = {value: SimpleNamespace(identity=(11, bytes([value])), directory=True)
                          for value in (101, 102, 103)}
                inspected = []
                def inspect(value):
                    inspected.append(value)
                    return states[value]
                native = SimpleNamespace(inspect=inspect, close=close)
                handles = [windows_io._OwnedHandle(native, value) for value in (101, 102, 103)]
                project = windows_io.WindowsProjectDirectory.__new__(windows_io.WindowsProjectDirectory)
                project._native, project._volume = native, 11
                project._anchors = [(h, states[h.value].identity) for h in handles]
                parent = _BorrowedReaderParent(project)
                adapter = NativeWindowsAdapter.__new__(NativeWindowsAdapter)
                adapter.open_existing_parent = lambda path: parent
                owner = ownership._ExistingParentOwner('fake-native-ledger', backend='windows', adapter=adapter)
                self.assertEqual(inspected, [101, 102, 103])  # Actual reader ledger check.
                states[102] = SimpleNamespace(identity=(12, b'wrong-volume'), directory=True)
                with self.assertRaises(ValueError): owner._borrow('windows')
                states[102] = SimpleNamespace(identity=(11, bytes([102])), directory=True)
                borrow = owner._borrow('windows'); borrow.check(); borrow.close()
                with self.assertRaises(BaseException) as caught: owner.close()
                expected = next((e for e in failures if not isinstance(e, Exception)), failures[0])
                self.assertIs(caught.exception, expected)
                self.assertEqual(owner.state, 'RELEASE_UNCERTAIN')
                self.assertEqual(project._anchors, [])
                self.assertTrue(all(h._value is None for h in handles))
                owner.close(); parent.close(); project.close()
                for handle in handles: handle.close()
                self.assertEqual(log, [103, 102, 101])

    def test_backend_and_native_identity_fail_closed(self):
        with self.assertRaises(ValueError): ownership._ExistingParentOwner('path', backend='other')
        with self.assertRaises(ValueError): ownership._ExistingParentOwner('path', backend='linux', adapter=object())
        adapter, owner = fake_owner()
        original = adapter.parent.identity
        adapter.parent.identity = (11, b'replaced')
        with self.assertRaisesRegex(ValueError, 'identity changed'): owner._borrow('windows')
        self.assertEqual(owner.active_borrows, 0)
        adapter.parent.identity = original; owner.close()


class BorrowedWindowsWriterTests(unittest.TestCase):
    def test_sequential_writers_reuse_owner_adapter_and_never_open_path(self):
        adapter, owner = fake_owner()
        receipts = []
        with patch.object(adapter, 'open_existing_parent', side_effect=AssertionError('path reopen')):
            for number in range(3):
                writer = WindowsDirectSnapshot._from_parent_owner(owner, f'g{number}.wbs', **META)
                self.assertFalse(hasattr(writer, 'parent_path'))
                self.assertEqual(owner.active_borrows, 0)
                with writer:
                    self.assertEqual(owner.active_borrows, 1)
                    writer.write_all([DATA]); before = writer.seal()
                    self.assertEqual(before.borrow_release, 'held')
                receipts.append(writer.receipt)
                self.assertEqual(writer.receipt.release_scope, 'file_and_borrow')
                self.assertEqual(writer.receipt.file_release, 'confirmed')
                self.assertEqual(writer.receipt.borrow_release, 'released')
                self.assertTrue(writer.receipt.retained_parent)
                self.assertEqual(writer.receipt.resources_released, 'confirmed')
                self.assertEqual(owner.state, 'OPEN')
                self.assertEqual(owner.active_borrows, 0)
                self.assertEqual(adapter.parent.closed, 0)
        self.assertEqual(adapter.parents_used, [(271, owner.identity)] * 3)
        self.assertEqual([file.closed for file in adapter.files], [1, 1, 1])
        adapter.parent.close_error = OSError('session teardown')
        with self.assertRaises(OSError): owner.close()
        self.assertTrue(all(r.resources_released == 'confirmed' for r in receipts))
        self.assertEqual(owner.state, 'RELEASE_UNCERTAIN')

    def test_invalid_metadata_and_wrong_backend_never_acquire(self):
        adapter, owner = fake_owner()
        for invalid in ('../bad', 'CON', ''):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                WindowsDirectSnapshot._from_parent_owner(owner, invalid, **META)
        with self.assertRaises(ValueError): WindowsDirectSnapshot._from_parent_owner(object(), 'x', **META)
        self.assertEqual(owner.active_borrows, 0); self.assertEqual(adapter.files, [])
        owner.close()

    def test_owner_closed_between_factory_and_enter_does_not_reopen(self):
        adapter, owner = fake_owner()
        writer = WindowsDirectSnapshot._from_parent_owner(owner, 'new', **META)
        owner.close()
        with self.assertRaises(SnapshotWriteError): writer.__enter__()
        self.assertEqual(adapter.files, []); self.assertEqual(adapter.parent.closed, 1)
        self.assertEqual(writer.receipt.creation, 'not_attempted')
        self.assertEqual(writer.receipt.borrow_release, 'not_acquired')

    def test_failed_create_preserves_unknown_and_releases_only_borrow(self):
        for mode, creation, release in [('collision', 'conflict', 'confirmed'), ('unknown_create', 'unknown', 'unconfirmed')]:
            with self.subTest(mode=mode):
                adapter, owner = fake_owner(); adapter.fail = mode
                writer = WindowsDirectSnapshot._from_parent_owner(owner, 'new', **META)
                with self.assertRaises(SnapshotWriteError): writer.__enter__()
                self.assertEqual(writer.receipt.creation, creation)
                self.assertEqual(writer.receipt.resources_released, release)
                self.assertEqual(writer.receipt.borrow_release, 'released')
                self.assertEqual(adapter.parent.closed, 0)
                self.assertEqual(owner.active_borrows, 0)
                owner.close()

    def test_wrong_owner_borrow_and_adapter_affinity_rejected(self):
        adapter, owner = fake_owner(); other_adapter, other = fake_owner()
        writer = WindowsDirectSnapshot._from_parent_owner(owner, 'new', **META)
        writer.__enter__(); original = writer._parent
        foreign = other._borrow('windows')
        try:
            writer._parent = foreign
            with self.assertRaises(ValueError): writer.write_all([DATA])
            writer._parent = original
            writer._adapter = other_adapter
            with self.assertRaises(ValueError): writer.write_all([DATA])
            writer._adapter = adapter
            self.assertEqual(adapter.content, b'')
        finally:
            writer._parent = original; writer._adapter = adapter
            writer._close_resources(); foreign.close(); owner.close(); other.close()

    def test_release_exception_after_unregister_remains_uncertain(self):
        adapter, owner = fake_owner()
        writer = WindowsDirectSnapshot._from_parent_owner(owner, 'new', **META)
        original = owner._release
        def release(borrow):
            original(borrow); raise OSError('after unregister')
        with patch.object(owner, '_release', side_effect=release):
            with self.assertRaises(SnapshotWriteError):
                with writer: writer.write_all([DATA]); writer.seal()
        self.assertEqual(owner.active_borrows, 0)
        self.assertEqual(writer.receipt.borrow_release, 'unconfirmed')
        self.assertEqual(writer.receipt.resources_released, 'unconfirmed')
        writer._close_resources()
        self.assertEqual(writer.receipt.resources_released, 'unconfirmed')
        owner.close()

    def test_native_boundaries_fail_without_parent_close(self):
        for mode in ('noninherit', 'inspect', 'write', 'flush', 'rewind', 'read'):
            with self.subTest(mode=mode):
                adapter, owner = fake_owner(); adapter.fail = mode
                writer = WindowsDirectSnapshot._from_parent_owner(owner, 'new', **META)
                with self.assertRaises(SnapshotWriteError):
                    with writer: writer.write_all([DATA]); writer.seal()
                self.assertEqual(owner.active_borrows, 0)
                self.assertEqual(adapter.parent.closed, 0)
                self.assertEqual(adapter.files[0].closed, 1)
                self.assertEqual(writer.receipt.borrow_release, 'released')
                owner.close()

    def test_body_error_after_seal_not_normal_success(self):
        adapter, owner = fake_owner()
        writer = WindowsDirectSnapshot._from_parent_owner(owner, 'new', **META)
        primary = RuntimeError('body after seal')
        with self.assertRaises(RuntimeError) as caught:
            with writer:
                writer.write_all([DATA]); writer.seal(); raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(writer.receipt.phase, 'failed')
        self.assertTrue(writer.receipt.expected_bytes_verified)
        self.assertEqual(writer.receipt.borrow_release, 'released')
        self.assertEqual(adapter.parent.closed, 0); owner.close()

    def test_file_cleanup_cancellation_and_borrow_failure_preserve_selected(self):
        for primary_kind, close_kind in product((None, OSError, KeyboardInterrupt, RejectDiagnostics), (OSError, SystemExit, RejectDiagnostics)):
            with self.subTest(primary=primary_kind, close=close_kind):
                adapter, owner = fake_owner()
                primary = primary_kind('body') if primary_kind else None
                close = close_kind('file close'); adapter.file_error = close
                writer = WindowsDirectSnapshot._from_parent_owner(owner, 'new', **META)
                with self.assertRaises(BaseException) as caught:
                    with writer:
                        writer.write_all([DATA]); writer.seal()
                        if primary is not None: raise primary
                expected = primary if primary is not None and (not isinstance(primary, Exception) or isinstance(close, Exception)) else close
                if isinstance(expected, Exception) and primary is None:
                    self.assertIsInstance(caught.exception, SnapshotWriteError)
                else: self.assertIs(caught.exception, expected)
                self.assertEqual(writer.receipt.file_release, 'unconfirmed')
                self.assertEqual(writer.receipt.borrow_release, 'released')
                self.assertEqual(owner.active_borrows, 0)
                self.assertEqual(adapter.files[0].closed, 1)
                writer._close_resources(); self.assertEqual(adapter.files[0].closed, 1)
                self.assertEqual(adapter.parent.closed, 0); owner.close()

    def test_borrow_release_failure_is_sticky_and_blocks_owner_close(self):
        adapter, owner = fake_owner()
        writer = WindowsDirectSnapshot._from_parent_owner(owner, 'new', **META)
        writer.__enter__(); borrow = writer._parent
        writer.write_all([DATA]); writer.seal()
        with patch.object(owner, '_release', side_effect=OSError('unregister failure')):
            with self.assertRaises(SnapshotWriteError): writer.__exit__(None, None, None)
        self.assertEqual(writer.receipt.borrow_release, 'unconfirmed')
        self.assertEqual(writer.receipt.file_release, 'confirmed')
        self.assertEqual(owner.active_borrows, 1)
        with self.assertRaises(ValueError): owner.close()
        writer._close_resources()
        self.assertEqual(writer.receipt.borrow_release, 'unconfirmed')
        self.assertEqual(adapter.files[0].closed, 1)
        borrow.close(); owner.close()

    def test_early_borrow_release_and_pid_mismatch_rejected(self):
        adapter, owner = fake_owner()
        writer = WindowsDirectSnapshot._from_parent_owner(owner, 'new', **META); writer.__enter__()
        with patch.object(ownership.os, 'getpid', return_value=owner._pid + 1):
            with self.assertRaises(ValueError): writer.write_all([DATA])
            failures = writer._close_resources()
            self.assertEqual(len(failures), 1)
        self.assertEqual(adapter.files[0].closed, 0)
        self.assertEqual(owner.active_borrows, 1)
        writer._parent.close()
        with self.assertRaises(ValueError): writer.write_all([DATA])
        with self.assertRaises(ValueError): writer.seal()
        writer._close_resources(); owner.close()


@unittest.skipUnless(LINUX, 'Requires Linux dir-fd/O_TMPFILE/linkat')
class BorrowedLinuxWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.parent = self.root / 'selected'; self.parent.mkdir()
        self.owner = ownership._ExistingParentOwner(self.parent, backend='linux')

    def tearDown(self):
        if self.owner.active_borrows == 0: self.owner.close()
        self.temp.cleanup()

    def writer(self, leaf='new.wbs'):
        return linux.LinuxSnapshot._from_parent_owner(self.owner, leaf, **META)

    def test_sequential_writes_after_parent_rename_never_reopen_path(self):
        moved = self.root / 'moved'; self.parent.rename(moved); self.parent.mkdir()
        (self.parent / 'new.wbs').write_bytes(b'competitor')
        with patch.object(linux, 'PosixProjectDirectory', side_effect=AssertionError('path reopen')):
            for leaf in ('new.wbs', 'next.wbs'):
                writer = self.writer(leaf)
                with writer:
                    self.assertEqual(self.owner.active_borrows, 1)
                    writer.write_all([DATA]); sealed = writer.seal()
                self.assertEqual(sealed.borrow_release, 'held')
                self.assertEqual(writer.receipt.release_scope, 'file_and_borrow')
                self.assertEqual(writer.receipt.borrow_release, 'released')
                self.assertEqual(writer.receipt.file_release, 'closed')
                self.assertEqual(writer.receipt.release, 'closed')
                self.assertTrue(writer.receipt.retained_parent)
                self.assertEqual(writer.receipt.parent_identity, self.owner.identity)
                self.assertEqual(self.owner.active_borrows, 0)
                self.assertEqual((moved / leaf).read_bytes(), DATA)
        self.assertEqual((self.parent / 'new.wbs').read_bytes(), b'competitor')
        self.assertFalse((self.parent / 'next.wbs').exists())

    def test_collision_never_overwrites_or_deletes_and_owner_survives(self):
        target = self.parent / 'new.wbs'; target.write_bytes(b'competitor')
        writer = self.writer()
        with self.assertRaises(linux.PublicationError):
            with writer: writer.write_all([DATA]); writer.seal()
        self.assertEqual(target.read_bytes(), b'competitor')
        self.assertEqual(writer.receipt.publication, 'conflict')
        self.assertEqual(writer.receipt.borrow_release, 'released')
        self.assertEqual(self.owner.state, 'OPEN')

    def test_owner_close_with_live_writer_is_rejected(self):
        writer = self.writer()
        with self.assertRaises(ValueError): self.owner.close()
        self.assertEqual(self.owner.state, 'OPEN')
        writer.close(); self.assertEqual(self.owner.active_borrows, 0)

    def test_file_acquisition_failure_releases_borrow_only(self):
        primary = KeyboardInterrupt('open file')
        with patch.object(linux.os, 'open', side_effect=primary):
            with self.assertRaises(KeyboardInterrupt) as caught: self.writer()
        self.assertIs(caught.exception, primary)
        self.assertEqual(self.owner.active_borrows, 0)
        self.assertEqual(self.owner.state, 'OPEN')
        borrow = self.owner._borrow('linux'); borrow.check(); borrow.close()

    def test_constructor_post_acquisition_check_cancellation_closes_file_once(self):
        original = os.fstat; file_fd = []
        def inspect(fd):
            info = original(fd)
            if info.st_mode & 0o170000 == 0o100000:
                file_fd.append(fd); raise RejectDiagnostics('file metadata')
            return info
        with patch.object(linux.os, 'fstat', side_effect=inspect):
            with self.assertRaises(RejectDiagnostics): self.writer()
        self.assertEqual(len(file_fd), 1)
        with self.assertRaises(OSError): os.fstat(file_fd[0])
        self.assertEqual(self.owner.active_borrows, 0)
        self.assertEqual(self.owner.state, 'OPEN')

    def test_failed_enter_closes_file_and_borrow(self):
        writer = self.writer(); descriptor = writer._fd
        with patch.object(self.owner, '_check_native', side_effect=KeyboardInterrupt('enter')):
            with self.assertRaises(KeyboardInterrupt): writer.__enter__()
        self.assertEqual(self.owner.active_borrows, 0)
        with self.assertRaises(OSError): os.fstat(descriptor)
        self.assertEqual(writer.receipt.borrow_release, 'released')

    def test_cleanup_cancellation_not_masked_and_no_reused_fd_retry(self):
        writer = self.writer(); descriptor = writer._fd; real_close = os.close; calls = []
        cancellation = RejectDiagnostics('close cancellation')
        def close(fd):
            calls.append(fd); real_close(fd)
            if fd == descriptor: raise cancellation
        with patch.object(linux.os, 'close', side_effect=close):
            with self.assertRaises(RejectDiagnostics) as caught:
                with writer: writer.write_all([DATA]); writer.seal()
            writer.close()
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(calls, [descriptor])
        self.assertEqual(writer.receipt.file_release, 'uncertain')
        self.assertEqual(writer.receipt.borrow_release, 'released')
        self.assertEqual(writer.receipt.release, 'uncertain')
        self.assertEqual(self.owner.state, 'OPEN')

    def test_body_error_after_seal_still_escapes_and_root_remains(self):
        writer = self.writer(); error = RuntimeError('body')
        with self.assertRaises(RuntimeError) as caught:
            with writer: writer.write_all([DATA]); writer.seal(); raise error
        self.assertIs(caught.exception, error)
        self.assertEqual(writer.receipt.completion, 'sealed')  # Observation is not acceptance.
        self.assertEqual(writer.receipt.borrow_release, 'released')
        self.assertEqual(self.owner.state, 'OPEN')

    def test_early_borrow_close_and_wrong_backend_rejected(self):
        writer = self.writer(); writer._parent.close()
        with self.assertRaises(ValueError): writer.write_all([DATA])
        with self.assertRaises(ValueError): writer.seal()
        writer.close()
        with self.assertRaises(ValueError): WindowsDirectSnapshot._from_parent_owner(self.owner, 'new', **META)
        _, other = fake_owner()
        try:
            with self.assertRaises(ValueError): linux.LinuxSnapshot._from_parent_owner(other, 'new', **META)
            self.assertEqual(other.active_borrows, 0)
        finally: other.close()

    def test_pid_mismatch_rejects_writer_file_io_and_close(self):
        writer = self.writer(); descriptor = writer._fd
        with patch.object(ownership.os, 'getpid', return_value=self.owner._pid + 1):
            with self.assertRaises(ValueError): writer.write_all([DATA])
            with self.assertRaises(ValueError): writer.close()
            writer.__del__()  # Finalization must not operate on inherited descriptors.
        self.assertEqual(writer._fd, descriptor); os.fstat(descriptor)
        self.assertEqual(self.owner.active_borrows, 1); writer.close()

    def test_owner_close_failure_stays_uncertain_and_does_not_retry_fd(self):
        descriptor = self.owner._parent.fd; real_close = os.close; calls = []
        def close(fd):
            calls.append(fd); real_close(fd); raise OSError('reported after close')
        with patch.object(ownership.os, 'close', side_effect=close):
            with self.assertRaises(OSError): self.owner.close()
            self.owner.close()
        self.assertEqual(calls, [descriptor]); self.assertEqual(self.owner.state, 'RELEASE_UNCERTAIN')

    def test_owner_native_close_cancellation_rejecting_diagnostics_preserved(self):
        descriptor = self.owner._parent.fd; real_close = os.close; calls = []
        cancellation = RejectDiagnostics('native parent close')
        def close(fd):
            calls.append(fd); real_close(fd); raise cancellation
        with patch.object(ownership.os, 'close', side_effect=close):
            with self.assertRaises(RejectDiagnostics) as caught: self.owner.close()
            self.owner.close()
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(calls, [descriptor])
        self.assertEqual(self.owner.state, 'RELEASE_UNCERTAIN')

    def test_wrong_owner_borrow_rejected_before_file_write(self):
        other = ownership._ExistingParentOwner(self.parent, backend='linux')
        writer = self.writer(); original = writer._parent; foreign = other._borrow('linux')
        try:
            writer._parent = foreign
            with self.assertRaises(ValueError): writer.write_all([DATA])
            self.assertEqual(os.fstat(writer._fd).st_size, 0)
        finally:
            writer._parent = original; writer.close(); foreign.close(); other.close()

    def test_borrow_unregister_failure_retains_registration_and_unknown_receipt(self):
        writer = self.writer(); borrow = writer._parent
        with patch.object(self.owner, '_release', side_effect=KeyboardInterrupt('release borrow')):
            with self.assertRaises(KeyboardInterrupt): writer.close()
        self.assertEqual(writer.receipt.file_release, 'closed')
        self.assertEqual(writer.receipt.borrow_release, 'uncertain')
        self.assertEqual(self.owner.active_borrows, 1)
        with self.assertRaises(ValueError): self.owner.close()
        writer.close()
        self.assertEqual(writer.receipt.borrow_release, 'uncertain')
        borrow.close()

    def test_owner_close_leaves_composed_reader_empty_and_safe_to_close(self):
        project = self.owner._parent; descriptor = project.fd; real_close = os.close; calls = []
        def close(fd):
            calls.append(fd); real_close(fd)
        with patch.object(ownership.os, 'close', side_effect=close):
            self.owner.close(); project.__exit__(); self.owner.close()
        self.assertEqual(calls, [descriptor]); self.assertIsNone(project.fd)

    def test_native_constructor_after_return_cancellation_and_cleanup_precedence(self):
        real_fstat, real_close = os.fstat, os.close
        for primary_kind, cleanup_kind in product((OSError, KeyboardInterrupt, RejectDiagnostics), (OSError, SystemExit)):
            with self.subTest(primary=primary_kind, cleanup=cleanup_kind):
                primary, cleanup = primary_kind('parent metadata'), cleanup_kind('parent release')
                selected_fd = []; calls = []
                def inspect(fd):
                    selected_fd.append(fd); raise primary
                def close(fd):
                    calls.append(fd); real_close(fd)
                    if fd in selected_fd: raise cleanup
                with patch.object(ownership.os, 'fstat', side_effect=inspect), patch.object(ownership.os, 'close', side_effect=close):
                    with self.assertRaises(BaseException) as caught:
                        ownership._ExistingParentOwner(self.parent, backend='linux')
                expected = cleanup if isinstance(primary, Exception) and not isinstance(cleanup, Exception) else primary
                self.assertIs(caught.exception, expected)
                self.assertEqual(len(selected_fd), 1)
                self.assertEqual(calls.count(selected_fd[0]), 1)
                with self.assertRaises(OSError): real_fstat(selected_fd[0])

    def test_actual_fork_cannot_use_or_close_parent_or_writer(self):
        writer = self.writer(); descriptor = writer._fd
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            passed = 0
            for operation in (lambda: self.owner._borrow('linux'), self.owner.close,
                              writer.close, lambda: writer.write_all([DATA])):
                try: operation()
                except ValueError: passed += 1
                except BaseException: pass
            os.write(write_fd, str(passed).encode()); os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        try: result = os.read(read_fd, 16)
        finally: os.close(read_fd)
        waited, status = os.waitpid(pid, 0)
        self.assertEqual((waited, status), (pid, 0))
        self.assertEqual(result, b'4')
        self.assertEqual(self.owner.state, 'OPEN'); os.fstat(descriptor)
        writer.close()

    def test_symlink_selection_fails_before_any_writer(self):
        alias = self.root / 'alias'; alias.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(OSError): ownership._ExistingParentOwner(alias, backend='linux')

    def test_invalid_metadata_does_not_register_borrow(self):
        for value in ('../bad', '/bad', ''):
            with self.subTest(value=value), self.assertRaises(ValueError): self.writer(value)
        self.assertEqual(self.owner.active_borrows, 0)


if __name__ == '__main__':
    unittest.main()
