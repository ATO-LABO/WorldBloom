"""Bounded unconnected native I/O foundation witnesses, not application coverage.

Every class is discovered on every platform. Linux native tests skip on Windows;
Windows reader/writer/spool native cases live in their own discoverable modules.
"""
from concurrent.futures import ThreadPoolExecutor
import errno
import gc
import hashlib
from itertools import product
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import weakref

from engine import calendar_fs as fs
from engine import calendar_spool as spool_module
from engine import calendar_container_linux as linux

LINUX = sys.platform == 'linux' and hasattr(os, 'O_TMPFILE')
DATA = b'new immutable output\n' * 100


class FoundationPureTests(unittest.TestCase):
    def test_relative_paths_reject_aliases(self):
        for value in ('', '../x', '/x', 'x/../y', 'C:x', 'x\\y', 'x//y', 'x/./y', 'x\x00y', None):
            with self.subTest(value=value), self.assertRaises(ValueError): fs._relative(value)
        self.assertEqual(fs._relative('inputs/world.yaml'), 'inputs/world.yaml')

    def test_integer_bounds_reject_bool_and_overflow(self):
        for value in (True, -1, 11, 1.0, None):
            with self.subTest(value=value), self.assertRaises(ValueError): spool_module.integer(value, 10, 'test')
        self.assertEqual(spool_module.integer(10, 10, 'test'), 10)


@unittest.skipUnless(LINUX, 'Requires real Linux no-follow/O_TMPFILE/linkat operations')
class LinuxFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'; self.project.mkdir()
        self.inputs = self.project / 'inputs'; self.inputs.mkdir()
        self.source = self.inputs / 'world.yaml'; self.source.write_bytes(b'inside\r\n')
        self.outside = self.root / 'outside'; self.outside.mkdir()
        (self.outside / 'world.yaml').write_bytes(b'outside')

    def tearDown(self):
        self.temp.cleanup()

    def reader(self, limit=spool_module.FILE_LIMIT):
        return fs.PosixProjectDirectory(self.project, file_limit=limit)

    def writer(self, leaf='saved.wbs'):
        return linux.LinuxSnapshot(self.root, leaf, expected_size=len(DATA),
                                   expected_sha256=hashlib.sha256(DATA).hexdigest(), max_bytes=len(DATA))

    def test_regular_crlf_identity_and_optional_missing(self):
        with self.reader() as directory:
            record = directory.read('inputs/world.yaml')
            self.assertEqual(record.data, b'inside\r\n')
            info = self.source.stat(); self.assertEqual(record.identity, (info.st_dev, info.st_ino))
            self.assertEqual(record.manifest()['sha256'], hashlib.sha256(record.data).hexdigest())
            self.assertIsNone(directory.read('missing', optional=True))
            with self.assertRaises(FileNotFoundError): directory.read('missing-dir/missing', optional=True)

    def test_symlink_leaf_rejected_independently(self):
        self.source.unlink(); self.source.symlink_to(self.outside / 'world.yaml')
        with self.reader() as directory, self.assertRaises(OSError): directory.read('inputs/world.yaml')

    def test_symlink_parent_and_root_rejected(self):
        for path in (self.inputs, self.project):
            alias = self.root / ('alias-' + path.name); alias.symlink_to(path, target_is_directory=True)
            with self.subTest(path=path), self.assertRaises(OSError):
                fs.PosixProjectDirectory(alias, file_limit=1024)

    def test_hardlink_rejected_independently_and_data_unchanged(self):
        os.link(self.source, self.root / 'alias')
        with self.reader() as directory, self.assertRaisesRegex(ValueError, 'ordinary independent file copy'):
            directory.read('inputs/world.yaml')
        self.assertEqual((self.root / 'alias').read_bytes(), b'inside\r\n')
        self.assertEqual(self.source.read_bytes(), b'inside\r\n')

    def test_leaf_replaced_after_native_open_uses_retained_inode(self):
        original = fs._open
        def opened(name, flags, *args, **kwargs):
            descriptor = original(name, flags, *args, **kwargs)
            if name == 'world.yaml':
                self.source.rename(self.inputs / 'retained')
                self.source.symlink_to(self.outside / 'world.yaml')
            return descriptor
        with self.reader() as directory, patch.object(fs, '_open', side_effect=opened):
            self.assertEqual(directory.read('inputs/world.yaml').data, b'inside\r\n')

    def test_parent_replaced_after_open_uses_retained_directory(self):
        original = fs._open
        def opened(name, flags, *args, **kwargs):
            descriptor = original(name, flags, *args, **kwargs)
            if name == 'inputs':
                self.inputs.rename(self.project / 'retained')
                self.inputs.symlink_to(self.outside, target_is_directory=True)
            return descriptor
        with self.reader() as directory, patch.object(fs, '_open', side_effect=opened):
            self.assertEqual(directory.read('inputs/world.yaml').data, b'inside\r\n')

    def test_root_replaced_after_acquisition_uses_retained_root(self):
        with self.reader() as directory:
            self.project.rename(self.root / 'retained')
            self.project.symlink_to(self.outside, target_is_directory=True)
            self.assertEqual(directory.read('inputs/world.yaml').data, b'inside\r\n')

    def test_logical_dotdot_never_opens_new_native_parent(self):
        pivot = self.root / 'pivot'; pivot.mkdir()
        original = fs._open; calls = []
        def opened(name, flags, *args, **kwargs):
            calls.append(name)
            descriptor = original(name, flags, *args, **kwargs)
            if name == 'pivot': pivot.rename(self.outside / 'pivot')
            return descriptor
        with patch.object(fs, '_open', side_effect=opened):
            with fs.PosixProjectDirectory(pivot / '..' / 'project', file_limit=1024) as directory:
                self.assertEqual(directory.read('inputs/world.yaml').data, b'inside\r\n')
        self.assertNotIn('..', calls)

    def test_same_inode_mutation_during_read_is_rejected(self):
        original = os.read; changed = []
        def read(fd, amount):
            result = original(fd, amount)
            if result and not changed:
                changed.append(True); self.source.write_bytes(b'CHANGED!')
            return result
        with self.reader() as directory, patch.object(fs.os, 'read', side_effect=read), self.assertRaises(ValueError):
            directory.read('inputs/world.yaml')

    def test_actual_growth_read_bound_limit_plus_one(self):
        original = os.read; requested = []
        def read(fd, amount):
            requested.append(amount)
            if len(requested) == 1:
                with self.source.open('ab') as stream: stream.write(b'x' * 50)
            return original(fd, amount)
        with self.reader(limit=8) as directory, patch.object(fs.os, 'read', side_effect=read), self.assertRaises(ValueError):
            directory.read('inputs/world.yaml')
        self.assertLessEqual(sum(requested), 9)

    def test_fifo_nonblocking_type_rejection(self):
        os.mkfifo(self.project / 'pipe')
        with self.reader() as directory, self.assertRaisesRegex(ValueError, 'regular file'):
            directory.read('pipe')

    def test_unix_socket_is_rejected_if_fixture_available(self):
        try: sock = socket.socket(socket.AF_UNIX)
        except OSError as error: self.skipTest('UNTESTED: AF_UNIX fixture creation unavailable: ' + str(error))
        try:
            try: sock.bind(str(self.project / 'socket'))
            except OSError as error: self.skipTest('UNTESTED: AF_UNIX fixture unavailable: ' + str(error))
            with self.reader() as directory, self.assertRaises((OSError, ValueError)): directory.read('socket')
        finally:
            sock.close()

    def test_exact_tree_checks_set_and_identity(self):
        (self.project / fs.MANIFEST).write_bytes(b'{}')
        with self.reader() as directory:
            files = {'inputs/world.yaml': directory.read('inputs/world.yaml')}
            version = directory.read(fs.MANIFEST)
            directory.exact_tree(files, version)
            (self.project / 'extra').write_bytes(b'extra')
            with self.assertRaises(ValueError): directory.exact_tree(files, version)

    def test_reader_close_failure_consumed_without_numeric_retry(self):
        directory = self.reader(); descriptor = directory.fd; original = os.close; calls = []
        def close(fd):
            calls.append(fd); original(fd)
            if fd == descriptor: raise OSError(errno.EIO, 'released then failed')
        with patch.object(fs.os, 'close', side_effect=close), self.assertRaises(OSError): directory.__exit__()
        directory.__exit__(); self.assertEqual(calls, [descriptor]); self.assertIsNone(directory.fd)

    def test_spool_record_keeps_owner_until_last_reference(self):
        spool = spool_module.PrivateSpool(); record = spool.add(b'captured'); spool.seal()
        ref = weakref.ref(spool); descriptor = spool._fd
        del spool; gc.collect(); self.assertEqual(record.data, b'captured')
        del record; gc.collect(); self.assertIsNone(ref())
        with self.assertRaises(OSError): os.fstat(descriptor)

    def test_spool_closed_empty_record_and_append_rejected(self):
        with spool_module.PrivateSpool() as spool:
            record = spool.add(b''); spool.seal()
            with self.assertRaises(ValueError): spool.append(b'x')
        with self.assertRaises(ValueError): list(record.iter_chunks())

    def test_concurrent_spool_slices(self):
        with spool_module.PrivateSpool() as spool:
            records = [spool.add(bytes([n]) * 100001) for n in range(8)]; spool.seal()
            with ThreadPoolExecutor(max_workers=8) as pool:
                actual = list(pool.map(lambda r: r.data, records * 4))
            self.assertEqual(actual, [bytes([n]) * 100001 for n in range(8)] * 4)

    def test_spool_no_new_64mib_cap_and_single_member_bound(self):
        with spool_module.PrivateSpool() as spool:
            records = [spool.add(bytes([n]) * spool_module.FILE_LIMIT) for n in range(9)]
            spool.seal(); self.assertGreater(spool.size, 64 * 1024 * 1024)
            self.assertEqual(records[-1].data[:1], b'\x08')
        with spool_module.PrivateSpool() as spool, self.assertRaises(ValueError):
            spool.add(b'x' * (spool_module.FILE_LIMIT + 1))

    def test_spool_disk_full_closes_fd(self):
        spool = spool_module.PrivateSpool(); descriptor = spool._fd
        with patch.object(spool_module.os, 'write', side_effect=OSError(errno.ENOSPC, 'full')), self.assertRaises(OSError):
            spool.append(b'x')
        self.assertIsNone(spool._fd)
        with self.assertRaises(OSError): os.fstat(descriptor)

    def test_outer_capture_streams_larger_than_member_bound(self):
        path = self.root / 'outer'; data = b'x' * (spool_module.FILE_LIMIT + 1); path.write_bytes(data)
        original = os.read; requests = []
        def read(fd, amount): requests.append(amount); return original(fd, amount)
        with patch.object(spool_module.os, 'read', side_effect=read):
            with spool_module.capture_file(path, limit=len(data)) as spool:
                self.assertEqual(spool.sha256, hashlib.sha256(data).hexdigest())
                self.assertEqual(spool.size, len(data))
        self.assertLessEqual(max(requests), spool_module.CHUNK)
        self.assertGreater(len(requests), 100)

    def test_outer_capture_rejects_actual_growth(self):
        original = os.read; changed = []
        def read(fd, amount):
            if not changed:
                changed.append(True)
                with self.source.open('ab') as stream: stream.write(b'x' * 100)
            return original(fd, amount)
        with patch.object(spool_module.os, 'read', side_effect=read), self.assertRaises(ValueError):
            spool_module.capture_file(self.source, limit=8)

    def test_source_close_failure_explicitly_closes_spool(self):
        identity = (self.source.stat().st_dev, self.source.stat().st_ino)
        original_close, original_spool = os.close, spool_module.PrivateSpool
        owners, calls = [], []
        def create(**kwargs): owner = original_spool(**kwargs); owners.append(owner); return owner
        def close(fd):
            info = os.fstat(fd); original_close(fd)
            if (info.st_dev, info.st_ino) == identity:
                calls.append(fd); raise OSError(errno.EIO, 'source released then failed')
        with patch.object(spool_module, 'PrivateSpool', side_effect=create), patch.object(spool_module.os, 'close', side_effect=close):
            try: spool_module.capture_file(self.source, limit=1024)
            except OSError as error: retained_error = error
            else: self.fail('Expected source close error')
        self.assertIsNotNone(retained_error.__traceback__)
        self.assertEqual(len(calls), 1); self.assertIsNone(owners[0]._fd)

    def test_native_publish_retains_identity_and_is_not_visible_before(self):
        with self.writer() as writer:
            writer.write_all([DATA]); self.assertFalse((self.root / 'saved.wbs').exists())
            receipt = writer.seal(); info = (self.root / 'saved.wbs').stat()
            self.assertEqual(receipt.object_identity, (info.st_dev, info.st_ino))
        self.assertEqual(writer.receipt.commit_mode, 'atomic_namespace')
        self.assertEqual(writer.receipt.release, 'closed')
        self.assertEqual((self.root / 'saved.wbs').read_bytes(), DATA)

    def test_native_no_clobber_file_directory_symlink(self):
        for kind in ('file', 'directory', 'symlink'):
            path = self.root / kind
            if kind == 'file': path.write_bytes(b'KEEP')
            elif kind == 'directory': path.mkdir(); (path / 'keep').write_bytes(b'KEEP')
            else: path.symlink_to(self.source)
            with self.subTest(kind=kind), self.assertRaises(linux.PublicationError) as caught:
                with self.writer(kind) as writer: writer.write_all([DATA]); writer.seal()
            self.assertEqual(caught.exception.receipt.publication, 'conflict')
            self.assertEqual(caught.exception.receipt.release, 'closed')
            if kind == 'file': self.assertEqual(path.read_bytes(), b'KEEP')
            elif kind == 'directory': self.assertEqual((path / 'keep').read_bytes(), b'KEEP')
            else: self.assertEqual(os.readlink(path), str(self.source))

    def test_writer_parent_rename_keeps_selected_object(self):
        parent = self.root / 'save'; parent.mkdir(); retained = self.root / 'retained-save'
        with linux.LinuxSnapshot(parent, 'saved', expected_size=len(DATA), expected_sha256=hashlib.sha256(DATA).hexdigest(), max_bytes=len(DATA)) as writer:
            parent.rename(retained); parent.mkdir(); (parent / 'keep').write_bytes(b'KEEP')
            writer.write_all([DATA]); writer.seal()
        self.assertEqual((retained / 'saved').read_bytes(), DATA)
        self.assertEqual([p.name for p in parent.iterdir()], ['keep'])

    def test_writer_short_writes_and_readback_mismatch(self):
        original = os.write
        with self.writer() as writer:
            with patch.object(linux.os, 'write', side_effect=lambda fd, data: original(fd, data[:7])): writer.write_all([DATA])
            writer.seal()
        with self.assertRaises(linux.PublicationError):
            with self.writer('bad') as writer:
                writer.write_all([b'x' * len(DATA)]); writer.seal()
        self.assertFalse((self.root / 'bad').exists())

    def test_writer_disk_full_and_unsealed_exit_preserve_existing(self):
        keep = self.root / 'keep'; keep.write_bytes(b'KEEP')
        with self.assertRaises(OSError):
            with self.writer() as writer:
                with patch.object(linux.os, 'write', side_effect=OSError(errno.ENOSPC, 'full')): writer.write_all([DATA])
        with self.assertRaises(linux.PublicationError):
            with self.writer() as writer: writer.write_all([b'x'])
        self.assertFalse((self.root / 'saved.wbs').exists()); self.assertEqual(keep.read_bytes(), b'KEEP')

    def test_published_then_flush_failure_receipt(self):
        original = os.fsync
        with self.assertRaises(linux.PublicationError) as caught:
            with self.writer() as writer:
                parent_fd = writer._parent.fd; writer.write_all([DATA])
                def flush(fd):
                    if fd == parent_fd: raise OSError(errno.EIO, 'directory flush failed')
                    return original(fd)
                with patch.object(linux.os, 'fsync', side_effect=flush): writer.seal()
        self.assertEqual(caught.exception.receipt.publication, 'published')
        self.assertEqual(caught.exception.receipt.durability, 'failed_after_publication')
        self.assertEqual(caught.exception.receipt.release, 'closed')
        self.assertEqual((self.root / 'saved.wbs').read_bytes(), DATA)

    def test_native_publication_denial_has_no_fallback(self):
        with self.assertRaises(linux.PublicationError):
            with self.writer() as writer:
                writer.write_all([DATA])
                with patch.object(linux, '_link_unnamed', side_effect=PermissionError('fixture denial')): writer.seal()
        self.assertFalse((self.root / 'saved.wbs').exists())

    def test_cancel_during_close_attempts_every_owned_resource_once(self):
        writer = self.writer(); output_fd, parent_fd = writer._fd, writer._parent.fd
        original = os.close; calls = []
        def close(fd):
            calls.append(fd); original(fd)
            if fd == output_fd: raise KeyboardInterrupt('cancel after release')
        with patch.object(linux.os, 'close', side_effect=close), self.assertRaises(KeyboardInterrupt): writer.close()
        writer.close(); self.assertEqual(calls.count(output_fd), 1); self.assertIn(parent_fd, calls)

    def _assert_writer_cleanup_case(self, phase, primary, cleanups, *, sealed=True, diagnostics=True):
        original_close = os.close
        calls = []
        writer = None
        if phase == 'constructor':
            parent = fs.PosixProjectDirectory(self.root, file_limit=len(DATA))
            parent_fd = parent.fd
            def close(fd):
                which = int(fd == parent_fd)
                calls.append(which)
                original_close(fd)
                if cleanups[which] is not None:
                    raise cleanups[which]
            with patch.object(linux, 'PosixProjectDirectory', return_value=parent), \
                    patch.object(linux.os, 'fstat', side_effect=primary), \
                    patch.object(linux.os, 'close', side_effect=close):
                try:
                    self.writer()
                except BaseException as error:
                    caught = error
                else:
                    self.fail('Constructor must propagate the injected primary')
            self.assertIsNone(parent.fd)
        else:
            # Every matrix row gets a separate owned leaf. No rollback/deletion
            # is performed by the writer, including after successful publication.
            leaf = 'cleanup-' + str(self._cleanup_sequence)
            self._cleanup_sequence += 1
            writer = self.writer(leaf)
            file_fd, parent_fd = writer._fd, writer._parent.fd
            def close(fd):
                which = int(fd == parent_fd)
                self.assertIn(fd, (file_fd, parent_fd))
                calls.append(which)
                original_close(fd)
                if cleanups[which] is not None:
                    raise cleanups[which]
            caught = None
            with patch.object(linux.os, 'close', side_effect=close):
                try:
                    with writer:
                        writer.write_all([DATA])
                        if sealed:
                            writer.seal()
                        if primary is not None:
                            raise primary
                except BaseException as error:
                    caught = error
                writer.close()  # Ownership was consumed even when a close raised.
            self.assertIsNone(writer._fd)
            self.assertIsNone(writer._parent)
            self.assertEqual(writer.receipt.release, 'uncertain' if any(e is not None for e in cleanups) else 'closed')
            self.assertEqual(writer.receipt.publication, 'published' if sealed else 'not_attempted')
            self.assertEqual(writer.receipt.completion, 'sealed' if sealed else 'unsealed')
            self.assertEqual((self.root / leaf).exists(), sealed)
        candidates = [e for e in (primary, *cleanups) if e is not None]
        expected = next((e for e in candidates if not isinstance(e, Exception)),
                        primary if primary is not None else next(iter(candidates), None))
        if expected is None:
            if sealed:
                self.assertIsNone(caught)
            else:
                self.assertIsInstance(caught, linux.PublicationError)
        elif expected is primary or not isinstance(expected, Exception):
            self.assertIs(caught, expected)
        else:
            self.assertIsInstance(caught, linux.PublicationError)
            self.assertIs(caught.__cause__, expected)
        if caught is not None:
            self.assertIsNot(caught.__cause__, caught)
            if writer is not None and diagnostics:
                self.assertEqual(caught.receipt, writer.receipt)
        self.assertEqual(calls, [0, 1])

    def test_constructor_cancellation_survives_ordinary_cleanup_error(self):
        self._assert_writer_cleanup_case('constructor', KeyboardInterrupt('primary'), (None, OSError('parent')))

    def test_body_cancellation_precedes_later_cleanup_cancellation(self):
        self._cleanup_sequence = 0
        self._assert_writer_cleanup_case('context', KeyboardInterrupt('primary'), (SystemExit('file'), None))

    def test_constructor_cleanup_full_priority_matrix(self):
        kinds = (None, OSError, KeyboardInterrupt, SystemExit, GeneratorExit)
        for pk, fk, rk in product(kinds[1:], kinds, kinds):
            with self.subTest(primary=pk, file=fk, parent=rk):
                self._assert_writer_cleanup_case('constructor', pk('primary'),
                                                (fk('file') if fk else None, rk('parent') if rk else None))

    def test_context_cleanup_full_priority_matrix(self):
        self._cleanup_sequence = 0
        kinds = (None, OSError, KeyboardInterrupt, SystemExit, GeneratorExit)
        for pk, fk, rk in product(kinds, kinds, kinds):
            with self.subTest(primary=pk, file=fk, parent=rk):
                self._assert_writer_cleanup_case('context', pk('primary') if pk else None,
                                                (fk('file') if fk else None, rk('parent') if rk else None))

    def test_same_writer_cancellation_never_becomes_self_cause(self):
        self._cleanup_sequence = 0
        for phase in ('constructor', 'context'):
            with self.subTest(phase=phase):
                error = KeyboardInterrupt('same instance')
                self._assert_writer_cleanup_case(phase, error, (error, error))

    def test_writer_cancellation_rejecting_diagnostics_is_never_replaced(self):
        class RejectDiagnostics(KeyboardInterrupt):
            def __setattr__(self, name, value):
                if name == 'receipt':
                    raise AttributeError('receipt rejected')
                super().__setattr__(name, value)
            def add_note(self, note):
                raise AttributeError('note rejected')
        self._cleanup_sequence = 0
        for phase in ('constructor', 'context'):
            for primary_cancel in (False, True):
                with self.subTest(phase=phase, primary_cancel=primary_cancel):
                    cancellation = RejectDiagnostics('original cancellation')
                    primary = cancellation if primary_cancel else OSError('primary')
                    cleanup = OSError('close') if primary_cancel else cancellation
                    self._assert_writer_cleanup_case(phase, primary, (cleanup, None), diagnostics=False)

    def test_unsealed_writer_cleanup_cancellation_still_propagates(self):
        self._cleanup_sequence = 0
        self._assert_writer_cleanup_case('context', None, (OSError('file'), GeneratorExit('parent')), sealed=False)

    def test_writer_diagnostic_rendering_preserves_selected_failure(self):
        renders = []
        class BadRenderError(OSError):
            def __repr__(self):
                renders.append('repr')
                raise RuntimeError('repr rejected')
            def __str__(self):
                renders.append('str')
                raise RuntimeError('str rejected')
        class BadRenderCancel(SystemExit):
            def __repr__(self):
                renders.append('repr')
                raise RuntimeError('repr rejected')
            def __str__(self):
                renders.append('str')
                raise RuntimeError('str rejected')
        self._cleanup_sequence = 0
        for phase, pk, ck in product(('constructor', 'context'),
                                     (OSError, KeyboardInterrupt, SystemExit, GeneratorExit),
                                     (BadRenderError, BadRenderCancel)):
            with self.subTest(phase=phase, primary=pk.__name__, cleanup=ck.__name__):
                renders.clear()
                self._assert_writer_cleanup_case(phase, pk('primary'), (ck('cleanup'), None))
                self.assertNotIn('repr', renders)
                self.assertEqual(renders, ['str'] if ck is BadRenderError else [])

    def test_writer_diagnostic_hooks_do_not_replace_selected_failure(self):
        hooks = []
        class RejectDiagnostics(OSError):
            def __setattr__(self, name, value):
                if name == 'receipt':
                    hooks.append('receipt')
                    raise hook_failure('receipt rejected')
                super().__setattr__(name, value)
            def add_note(self, note):
                hooks.append('note')
                raise hook_failure('note rejected')
        self._cleanup_sequence = 0
        for phase, ck, hook_failure in product(('constructor', 'context'),
                                              (None, OSError, KeyboardInterrupt, SystemExit),
                                              (AttributeError, KeyboardInterrupt)):
            with self.subTest(phase=phase, cleanup=ck.__name__ if ck else 'none', hook=hook_failure.__name__):
                hooks.clear()
                cleanup = ck('cleanup') if ck else None
                self._assert_writer_cleanup_case(phase, RejectDiagnostics('primary'),
                                                 (cleanup, None), diagnostics=False)
                # The ordinary primary is not annotated before cleanup
                # cancellation wins; otherwise diagnostics are best effort.
                self.assertEqual(hooks, [] if ck in (KeyboardInterrupt, SystemExit) else
                                 ['receipt', 'note'] if ck is OSError else ['receipt'])

    def test_writer_ordinary_close_wrapper_survives_rejected_str(self):
        self._cleanup_sequence = 0
        for failure_kind, primary_kind in product((RuntimeError, KeyboardInterrupt), (None, OSError)):
            with self.subTest(render_failure=failure_kind.__name__, primary=primary_kind):
                class RejectStr(OSError):
                    def __str__(self):
                        raise failure_kind('message rejected')
                self._assert_writer_cleanup_case('context', primary_kind('primary') if primary_kind else None,
                                                 (RejectStr('close'), None))

    def test_repeated_failures_do_not_leak_fds(self):
        before = len(os.listdir('/proc/self/fd'))
        for _ in range(6):
            with self.assertRaises(linux.PublicationError):
                with self.writer() as writer: writer.seal()
            with self.assertRaises(ValueError): spool_module.capture_file(self.source, limit=1)
        gc.collect(); self.assertEqual(len(os.listdir('/proc/self/fd')), before)


if __name__ == '__main__':
    unittest.main()
