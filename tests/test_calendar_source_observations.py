"""Native POSIX witnesses and pure Windows token logic (not native Windows evidence)."""
from dataclasses import replace
import os
import sys
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from engine.calendar_fs import PosixProjectDirectory, SourceObservation, InputBytes
from engine import calendar_windows_io as win


@unittest.skipUnless(os.name == 'posix', 'POSIX native only')
class PosixObservations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'world.yaml').write_bytes(b'a: 1\n')
        self.reader = PosixProjectDirectory(self.root, file_limit=1024)
        self.addCleanup(self.reader.__exit__)
        self.record = self.reader.read('world.yaml')

    def test_successful_read_cleanup_ignores_unrelated_handled_exception(self):
        original_close = os.close
        cleanup = OSError('current close failure')
        attempts = []
        def close(fd):
            attempts.append(fd)
            original_close(fd)
            raise cleanup
        try:
            raise ValueError('unrelated previously handled error')
        except ValueError:
            with patch('engine.calendar_fs.os.close', side_effect=close):
                with self.assertRaises(OSError) as caught:
                    self.reader.read('world.yaml')
        self.assertIs(caught.exception, cleanup)
        self.assertEqual(len(attempts), 2)

    def test_read_preserves_primary_when_both_owned_closes_fail(self):
        for primary in (ValueError('read failed'), KeyboardInterrupt('read cancelled')):
            original_close = os.close
            attempts = []
            def failing_close(fd):
                attempts.append(fd)
                original_close(fd)
                raise OSError('release reported failure')
            with patch('engine.calendar_fs.os.read', side_effect=primary), \
                 patch('engine.calendar_fs.os.close', side_effect=failing_close):
                with self.assertRaises(type(primary)) as caught:
                    self.reader.read('world.yaml')
            self.assertIs(caught.exception, primary)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(len(set(attempts)), 2)
            self.assertIsInstance(primary.__cause__, OSError)
            self.assertTrue(any('release is uncertain' in n for n in primary.__notes__))

    def test_recursive_exact_metadata_failure_survives_child_close_failures(self):
        (self.root / 'nested').mkdir()
        (self.root / 'nested' / 'file.yaml').write_bytes(b'abc')
        token = self.reader.read('nested/file.yaml').observation
        (self.root / 'nested' / 'file.yaml').write_bytes(b'def')
        original_close = os.close
        attempts = []
        def failing_close(fd):
            attempts.append(fd)
            original_close(fd)
            raise OSError('release reported failure')
        with patch('engine.calendar_fs.os.close', side_effect=failing_close):
            with self.assertRaisesRegex(ValueError, 'metadata changed') as caught:
                self.reader.exact_entries({'world.yaml': self.record,
                                           'nested/file.yaml': token})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(set(attempts)), 2)
        self.assertIsInstance(caught.exception.__cause__, OSError)

    def test_token_and_legacy_record_exact_without_manifest(self):
        token = self.record.observation
        self.assertIsInstance(token, SourceObservation)
        self.assertEqual(token.native_identity, self.record.identity)
        self.assertEqual(token.size_bytes, len(self.record.data))
        self.reader.exact_entries({'world.yaml': token})
        self.reader.exact_entries({'world.yaml': self.record})

    def test_exact_tree_wrapper_accepts_tokens_and_rejects_late_extras(self):
        (self.root / 'execution-version.json').write_bytes(b'{}')
        manifest = self.reader.read('execution-version.json').observation
        self.reader.exact_tree({'world.yaml': self.record.observation}, manifest)
        (self.root / 'extra').mkdir()
        with self.assertRaises(ValueError):
            self.reader.exact_tree({'world.yaml': self.record.observation}, manifest)

    def test_same_size_mutation_preserving_mtime_is_rejected(self):
        before = (self.root / 'world.yaml').stat()
        (self.root / 'world.yaml').write_bytes(b'a: 2\n')
        os.utime(self.root / 'world.yaml', ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaises(ValueError):
            self.reader.exact_entries({'world.yaml': self.record.observation})

    def test_wrong_backend_generic_spool_and_unobserved_records_fail(self):
        for value in (replace(self.record.observation, backend='windows'),
                      SimpleNamespace(identity=self.record.identity, data=self.record.data,
                                      observation=self.record.observation),
                      InputBytes(self.record.data, self.record.identity)):
            with self.subTest(value=type(value)), self.assertRaises(ValueError):
                self.reader.exact_entries({'world.yaml': value})

    def test_aliases_prefix_collision_and_manifest_collision(self):
        for entries in ({'world.yaml': self.record, 'WORLD.yaml': self.record},
                        {'world.yaml': self.record, 'world.yaml/x': self.record}):
            with self.assertRaises(ValueError):
                self.reader.exact_entries(entries)
        with self.assertRaises(ValueError):
            self.reader.exact_tree({'execution-version.json': self.record}, self.record)

    def test_yaml_policy_ignores_regular_non_yaml_and_empty_dirs(self):
        (self.root / 'notes.txt').write_bytes(b'ignored')
        (self.root / 'empty').mkdir()
        self.reader.exact_entries({'world.yaml': self.record}, policy='yaml')
        with self.assertRaises(ValueError):
            self.reader.exact_entries({'world.yaml': self.record})
        (self.root / 'empty' / 'extra.yaml').write_bytes(b'no')
        with self.assertRaises(ValueError):
            self.reader.exact_entries({'world.yaml': self.record}, policy='yaml')

    def test_yaml_policy_rejects_non_yaml_symlinks_hardlinks_and_fifo(self):
        extra = self.root / 'extra.txt'
        for make in (lambda: extra.symlink_to('world.yaml'),
                     lambda: os.link(self.root / 'world.yaml', extra),
                     lambda: os.mkfifo(extra)):
            make()
            try:
                with self.assertRaises((OSError, ValueError)):
                    self.reader.exact_entries({'world.yaml': self.record}, policy='yaml')
            finally:
                extra.unlink()


class WindowsObservationLogic(unittest.TestCase):
    """Fake native operations exercise checks only, never platform behavior."""
    def setUp(self):
        self.state = win._Metadata((7, b'f' * 16), 3, 4096, 1, False, 32, 0, 1, 2, 3)
        self.directory = replace(self.state, identity=(7, b'd' * 16), directory=True)
        self.current = self.state
        outer = self
        class Native:
            def inspect(self, value):
                return outer.directory if value == 1 else outer.current
            def entries(self, value, expected):
                return {'world.yaml': win._Entry('world.yaml', outer.state.identity[1], False)}
            def open_relative(self, value, name):
                return win._OwnedHandle(self, 2)
            def close(self, value):
                pass
        self.reader = object.__new__(win.WindowsProjectDirectory)
        self.reader._native = Native()
        self.reader._volume = 7
        self.reader._anchors = [(win._OwnedHandle(self.reader._native, 1), self.directory.identity)]
        self.addCleanup(self.reader.close)
        self.record = win._Record(b'abc', self.state.identity, self.state)

    def test_successful_windows_read_cleanup_ignores_unrelated_exception(self):
        self.reader.file_limit = 100
        cleanup = OSError('current leaf close failure')
        try:
            raise ValueError('unrelated previously handled error')
        except ValueError:
            with patch.object(self.reader._native, 'read', side_effect=(b'abc', b''), create=True), \
                 patch.object(self.reader._native, 'close', side_effect=cleanup):
                with self.assertRaises(OSError) as caught:
                    self.reader.read('world.yaml')
        self.assertIs(caught.exception, cleanup)

    def test_read_body_exception_survives_leaf_close_error(self):
        self.reader.file_limit = 100
        for primary in (ValueError('read failed'), KeyboardInterrupt('read cancelled')):
            attempts = []
            def close(value):
                attempts.append(value)
                raise OSError('leaf close failed')
            with patch.object(self.reader._native, 'read', side_effect=primary, create=True), \
                 patch.object(self.reader._native, 'close', side_effect=close):
                with self.assertRaises(type(primary)) as caught:
                    self.reader.read('world.yaml')
            self.assertIs(caught.exception, primary)
            self.assertEqual(attempts, [2])
            self.assertIsInstance(primary.__cause__, OSError)

    def test_exact_metadata_error_survives_leaf_close_error(self):
        self.current = replace(self.state, change=self.state.change + 1)
        attempts = []
        def close(value):
            attempts.append(value)
            raise OSError('leaf close failed')
        with patch.object(self.reader._native, 'close', side_effect=close):
            with self.assertRaisesRegex(ValueError, 'metadata changed') as caught:
                self.reader.exact_entries({'world.yaml': self.record.observation})
        self.assertEqual(attempts, [2])
        self.assertIsInstance(caught.exception.__cause__, OSError)

    def test_token_and_record_check_full_native_metadata(self):
        for record in (self.record, self.record.observation):
            self.current = self.state
            self.reader.exact_entries({'world.yaml': record})
            self.current = replace(self.state, change=self.state.change + 1)
            with self.assertRaises(ValueError):
                self.reader.exact_entries({'world.yaml': record})

    def test_spool_like_record_and_wrong_backend_never_downgrade(self):
        for record in (SimpleNamespace(identity=self.state.identity, data=b'abc',
                                       observation=self.record.observation),
                       replace(self.record.observation, backend='posix')):
            with self.assertRaises(ValueError):
                self.reader.exact_entries({'world.yaml': record})


@unittest.skipUnless(sys.platform == 'linux', 'Linux O_TMPFILE only')
class LinuxSpoolCleanup(unittest.TestCase):
    def test_successful_spool_close_ignores_unrelated_handled_exception(self):
        from engine import calendar_spool as spool_module
        spool = spool_module.PrivateSpool()
        self.addCleanup(spool.close)
        original_close = os.close
        cleanup = OSError('current spool close failure')
        attempts = []
        def close(fd):
            attempts.append(fd)
            original_close(fd)
            raise cleanup
        try:
            raise ValueError('unrelated previously handled error')
        except ValueError:
            with patch.object(spool_module.os, 'close', side_effect=close):
                with self.assertRaises(OSError) as caught:
                    spool.close()
                spool.close()
        self.assertIs(caught.exception, cleanup)
        self.assertEqual(len(attempts), 1)

    def test_append_and_seal_preserve_primary_and_consume_fd_once(self):
        from engine import calendar_spool as spool_module
        for operation, failing_call in (('append', 'write'), ('seal', 'fsync')):
            for primary in (OSError('primary I/O error'), KeyboardInterrupt('primary cancellation')):
                with self.subTest(operation=operation, primary=type(primary).__name__):
                    spool = spool_module.PrivateSpool()
                    self.addCleanup(spool.close)
                    original_close = os.close
                    attempts = []
                    def failing_close(fd):
                        attempts.append(fd)
                        original_close(fd)
                        raise OSError('spool close failed')
                    with patch.object(spool_module.os, failing_call, side_effect=primary), \
                         patch.object(spool_module.os, 'close', side_effect=failing_close):
                        with self.assertRaises(type(primary)) as caught:
                            spool.append(b'abc') if operation == 'append' else spool.seal()
                        spool.close()  # A reported failure must not retry its fd.
                    self.assertIs(caught.exception, primary)
                    self.assertEqual(len(attempts), 1)
                    self.assertIsNone(spool._fd)
                    self.assertIsInstance(primary.__cause__, OSError)
                    self.assertTrue(any('release is uncertain' in n for n in primary.__notes__))

    def test_constructor_root_failure_survives_spool_close_failure(self):
        from engine import calendar_spool as spool_module
        for primary in (OSError('root close failed'), KeyboardInterrupt('root cancellation')):
            descriptor = os.open(tempfile.gettempdir(), os.O_RDONLY | os.O_DIRECTORY)
            class Root:
                fd = descriptor
                def __enter__(self): return self
                def __exit__(self, *_): raise primary
            owner = object.__new__(spool_module.PrivateSpool)
            original_close = os.close
            attempts = []
            def failing_close(fd):
                attempts.append(fd)
                original_close(fd)
                raise OSError('spool close failed')
            try:
                with patch.object(spool_module, 'PosixProjectDirectory', return_value=Root()), \
                     patch.object(spool_module.os, 'close', side_effect=failing_close):
                    with self.assertRaises(type(primary)) as caught:
                        owner.__init__()
                    owner.close()
                self.assertIs(caught.exception, primary)
                self.assertEqual(len(attempts), 1)
                self.assertIsNone(owner._fd)
                self.assertIsInstance(primary.__cause__, OSError)
            finally:
                original_close(descriptor)
