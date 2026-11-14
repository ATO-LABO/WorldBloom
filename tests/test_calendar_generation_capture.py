"""Native generation capture and store continuation; no path-based capture port."""
from contextlib import closing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.calendar_generation_capture import NativeGenerationCapture, _bounded_read
from engine.calendar_generation import GenerationError, MAX_GENERATION_BYTES
from engine.calendar_parent_owner import _ExistingParentOwner
from engine.calendar_run_store import RunStore, NativeWriterPort
from tests.test_calendar_generation import COMPAT, STREAM


class BoundedReadTests(unittest.TestCase):
    def test_exact_and_empty(self):
        chunks = iter([b'abc', b''])
        self.assertEqual(b'abc', _bounded_read(lambda n: next(chunks), 3))
        self.assertEqual(b'', _bounded_read(lambda n: b'', 0))

    def test_overflow_bad_type_and_overread(self):
        for value, limit in [(b'ab', 1), ('a', 1), (b'abc', 1)]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError): _bounded_read(lambda n: value, limit)


@unittest.skipUnless(sys.platform == 'linux' or os.name == 'nt', 'native Linux or Windows required')
class NativeGenerationCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'parent'; self.root.mkdir()
        self.backend = 'windows' if os.name == 'nt' else 'linux'
        self.owner = _ExistingParentOwner(self.root, backend=self.backend)
        self.addCleanup(self.owner.close)
        self.capture = NativeGenerationCapture()
        self.store = self.make_store(STREAM)

    def make_store(self, stream):
        store = RunStore(self.owner, NativeWriterPort(), self.capture, COMPAT, stream_id=stream)
        self.addCleanup(store.finish)
        return store

    def test_native_save_capture_and_continue_preserve_old_generation(self):
        original = self.store.initialize({'tick': 0, 'history': ['initial']})
        self.assertEqual(original.prepared, self.store.load_selected(original.selection()))
        second = self.store.save({'tick': 1, 'history': ['initial', 'next']})
        child = self.make_store('b' * 32)
        self.assertEqual({'tick': 1, 'history': ['initial', 'next']}, child.continue_from(second.selection()))
        self.assertIsNone(child.head)
        self.assertEqual(second.ref, child.selected_base)
        forked = child.save({'tick': 2})
        self.assertEqual(second.ref, forked.prepared.forked_from)
        self.assertIsNone(forked.prepared.previous)
        self.assertEqual(0, forked.ref.ordinal)
        self.assertEqual(original.prepared, self.store.load_selected(original.selection()))
        self.assertEqual(second.prepared, self.store.load_selected(second.selection()))
        self.assertEqual(2, self.owner.active_borrows)
        self.assertEqual('OPEN', self.owner.state)

    def test_borrow_remains_owned_by_caller_and_limit_stays_zero(self):
        saved = self.store.initialize({'tick': 0})
        lease = self.owner._borrow(self.backend)
        try:
            result = self.capture.capture(self.owner, saved.prepared.leaf, max_bytes=saved.ref.size, parent_borrow=lease)
            self.assertEqual(saved.prepared.data, result.data)
            self.assertEqual(2, self.owner.active_borrows)
            parent = lease._require(self.owner, self.backend)
            reader = parent if self.backend == 'linux' else parent.project
            self.assertEqual(0, reader.file_limit)
        finally:
            lease.close()
        self.assertEqual(1, self.owner.active_borrows)

    def test_wrong_owner_stale_and_nonborrow_rejected(self):
        other_root = Path(self.tmp.name) / 'other'; other_root.mkdir()
        with _ExistingParentOwner(other_root, backend=self.backend) as other:
            lease = other._borrow(self.backend)
            try:
                with self.assertRaises(ValueError):
                    self.capture.capture(self.owner, 'file', max_bytes=1, parent_borrow=lease)
            finally: lease.close()
        stale = self.owner._borrow(self.backend); stale.close()
        for lease in (stale, None, object()):
            with self.assertRaises(ValueError):
                self.capture.capture(self.owner, 'file', max_bytes=1, parent_borrow=lease)
        self.assertEqual(1, self.owner.active_borrows)

    def test_alias_and_invalid_limit_rejected_before_open(self):
        lease = self.owner._borrow(self.backend)
        try:
            for name, size in [('../file', 1), ('file/path', 1), ('NUL', 1), ('name:ads', 1), ('file', True), ('file', -1), ('file', MAX_GENERATION_BYTES + 1)]:
                with self.subTest(name=name, size=size):
                    with self.assertRaises(ValueError):
                        self.capture.capture(self.owner, name, max_bytes=size, parent_borrow=lease)
        finally: lease.close()

    def test_short_limit_rejects_without_leaking(self):
        saved = self.store.initialize({'tick': 0})
        lease = self.owner._borrow(self.backend)
        try:
            with self.assertRaisesRegex(ValueError, 'byte limit'):
                self.capture.capture(self.owner, saved.prepared.leaf, max_bytes=saved.ref.size - 1, parent_borrow=lease)
        finally: lease.close()
        self.assertEqual(1, self.owner.active_borrows)
        self.assertEqual(saved.prepared, self.store.load_selected(saved.selection()))

    def test_digest_and_compatibility_mismatch_rejected(self):
        saved = self.store.initialize({'tick': 0})
        other = RunStore(self.owner, NativeWriterPort(), self.capture, {'different': True}, stream_id='c' * 32)
        self.addCleanup(other.finish)
        with self.assertRaises(GenerationError): other.load_selected(saved.selection())
        self.assertIsNone(other.head)
        self.assertEqual(2, self.owner.active_borrows)

    def test_cancel_releases_capture_file_and_borrow(self):
        saved = self.store.initialize({'tick': 0})
        error = KeyboardInterrupt('cancel capture')
        with patch('engine.calendar_generation_capture._bounded_read', side_effect=error):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.store.load_selected(saved.selection())
        self.assertIs(error, caught.exception)
        self.assertEqual('STOPPED', self.store.state)
        self.assertEqual(1, self.owner.active_borrows)
        self.assertEqual('OPEN', self.owner.state)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux rename/no-follow semantics')
    def test_retained_parent_survives_path_replacement(self):
        saved = self.store.initialize({'tick': 0})
        retained = self.root.with_name('retained'); self.root.rename(retained); self.root.mkdir()
        (self.root / saved.prepared.leaf).write_bytes(b'wrong root')
        with patch('engine.calendar_fs.PosixProjectDirectory.__init__', side_effect=AssertionError('root reopened')):
            self.assertEqual(saved.prepared, self.store.load_selected(saved.selection()))
        self.assertEqual(b'wrong root', (self.root / saved.prepared.leaf).read_bytes())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux no-follow semantics')
    def test_symlink_and_fifo_rejected_without_blocking(self):
        target = Path(self.tmp.name) / 'outside'; target.write_bytes(b'x')
        (self.root / 'link').symlink_to(target); os.mkfifo(self.root / 'fifo')
        lease = self.owner._borrow(self.backend)
        try:
            for name in ('link', 'fifo'):
                with self.assertRaises((OSError, ValueError)):
                    self.capture.capture(self.owner, name, max_bytes=1, parent_borrow=lease)
        finally: lease.close()


@unittest.skipUnless(sys.platform == 'linux' or os.name == 'nt', 'native Linux or Windows required')
class NativeGenerationCaptureFixtureTests(unittest.TestCase):
    """Create hostile fixtures before any owner acquires their parent."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'parent'; self.root.mkdir()
        self.backend = 'windows' if os.name == 'nt' else 'linux'
        self.capture = NativeGenerationCapture()

    def test_preexisting_hardlinks_rejected(self):
        target = self.root / 'linked'; target.write_bytes(b'x')
        alias = self.root / 'alias'
        os.link(target, alias)
        a, b = target.stat(), alias.stat()
        self.assertEqual((a.st_dev, a.st_ino), (b.st_dev, b.st_ino))
        self.assertEqual((2, 2), (a.st_nlink, b.st_nlink))
        with _ExistingParentOwner(self.root, backend=self.backend) as owner:
            identity = owner.identity
            with closing(owner._borrow(self.backend)) as lease:
                for name in ('linked', 'alias'):
                    with self.subTest(name=name):
                        with self.assertRaisesRegex(ValueError, 'single-linked regular file'):
                            self.capture.capture(owner, name, max_bytes=1, parent_borrow=lease)
                        lease.check()
                        self.assertEqual(1, owner.active_borrows)
                        self.assertEqual(identity, owner.identity)
                parent = lease._require(owner, self.backend)
                self.assertEqual(0, (parent if self.backend == 'linux' else parent.project).file_limit)
                self.assertEqual('OPEN', owner.state)
            self.assertEqual(0, owner.active_borrows)
        self.assertEqual(b'x', target.read_bytes())
        self.assertEqual(b'x', alias.read_bytes())
        self.assertEqual((2, 2), (target.stat().st_nlink, alias.stat().st_nlink))
        self.assertEqual({'linked', 'alias'}, {p.name for p in self.root.iterdir()})

    def test_preexisting_directory_rejected(self):
        directory = self.root / 'directory'; directory.mkdir()
        sentinel = directory / 'keep'; sentinel.write_bytes(b'preserved directory sentinel')
        with _ExistingParentOwner(self.root, backend=self.backend) as owner:
            identity = owner.identity
            with closing(owner._borrow(self.backend)) as lease:
                with self.assertRaisesRegex(ValueError, 'single-linked regular file'):
                    self.capture.capture(owner, 'directory', max_bytes=1, parent_borrow=lease)
                lease.check()
                self.assertEqual(1, owner.active_borrows)
                self.assertEqual(identity, owner.identity)
                parent = lease._require(owner, self.backend)
                self.assertEqual(0, (parent if self.backend == 'linux' else parent.project).file_limit)
                self.assertEqual('OPEN', owner.state)
            self.assertEqual(0, owner.active_borrows)
        self.assertTrue(directory.is_dir())
        self.assertEqual({'keep'}, {p.name for p in directory.iterdir()})
        self.assertEqual(b'preserved directory sentinel', sentinel.read_bytes())
        self.assertEqual({'directory'}, {p.name for p in self.root.iterdir()})


class WindowsCaptureCleanupTests(unittest.TestCase):
    def test_failed_open_has_no_owned_handle_to_release(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        error = OSError('open failed')
        native = SimpleNamespace(open_relative=Mock(side_effect=error))
        close = Mock()
        borrow = SimpleNamespace(value=1, _adapter=SimpleNamespace(native=native, reader=SimpleNamespace(_close_owned=close)))
        with self.assertRaises(OSError) as caught:
            NativeGenerationCapture._windows(borrow, 'file', 1)
        self.assertIs(error, caught.exception)
        close.assert_called_once_with([], primary=error)
