"""Private integration: native Linux output, synthetic input; no native capture."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from engine.calendar_container_linux import LinuxSnapshot
from engine.calendar_generation import decode_generation
from engine.calendar_parent_owner import _ExistingParentOwner
from engine.calendar_run_store import RunStore, NativeWriterPort, ConcurrentStoreOperation
from tests.test_calendar_generation import COMPAT, STREAM, prepared

class NoCapture:
    def capture(self, *a, **kw):
        raise AssertionError('Native capture is deliberately not integrated')

@unittest.skipUnless(sys.platform == 'linux', 'native Linux required')
class NativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'parent'
        self.root.mkdir()
        self.owner = _ExistingParentOwner(self.root, backend='linux')
        self.addCleanup(self.owner.close)
        self.store = RunStore(self.owner, NativeWriterPort(), NoCapture(), COMPAT, stream_id=STREAM)
        self.addCleanup(self.store.finish)

    def test_repeated_generations_keep_old_bytes_and_release_borrows(self):
        first = self.store.initialize({'tick': 0})
        old = (self.root / first.prepared.leaf).read_bytes()
        second = self.store.save({'tick': 1})
        self.assertEqual(old, (self.root / first.prepared.leaf).read_bytes())
        self.assertEqual(first.ref, second.prepared.previous)
        self.assertEqual(1, self.owner.active_borrows)
        self.assertEqual('file_and_borrow', second.observation.release_scope)
        self.assertEqual('released', second.observation.borrow_release)
        self.assertEqual('namespace_flush_confirmed', second.observation.namespace_durability)
        for accepted in (first, second):
            data = (self.root / accepted.prepared.leaf).read_bytes()
            decoded = decode_generation(data, expected=accepted.ref, expected_compatibility=COMPAT)
            self.assertEqual(accepted.prepared, decoded)
        with self.assertRaises(ValueError): self.owner.close()
        self.store.finish()
        self.assertEqual(0, self.owner.active_borrows)
        self.assertEqual('OPEN', self.owner.state)

    def test_parent_rename_replacement_does_not_redirect_generation(self):
        retained = self.root.with_name('retained')
        self.root.rename(retained)
        self.root.mkdir()
        sentinel = self.root / 'untouched'; sentinel.write_bytes(b'new-parent')
        accepted = self.store.initialize({'tick': 0})
        self.assertEqual(accepted.prepared.data, (retained / accepted.prepared.leaf).read_bytes())
        self.assertEqual(['untouched'], sorted(p.name for p in self.root.iterdir()))
        self.assertEqual(b'new-parent', sentinel.read_bytes())

    def test_existing_leaf_never_overwritten_and_store_stops(self):
        generation = prepared()
        target = self.root / generation.leaf; target.write_bytes(b'existing')
        with self.assertRaises(OSError): self.store.initialize({'tick': 0})
        self.assertEqual(b'existing', target.read_bytes())
        self.assertEqual('STOPPED', self.store.state)
        self.assertIsNone(self.store.head)
        self.assertEqual(1, self.owner.active_borrows)

    def test_symlink_leaf_never_follows_or_overwrites_target(self):
        target = Path(self.tmp.name) / 'outside'; target.write_bytes(b'outside')
        leaf = self.root / prepared().leaf; leaf.symlink_to(target)
        with self.assertRaises(OSError): self.store.initialize({'tick': 0})
        self.assertTrue(leaf.is_symlink())
        self.assertEqual(b'outside', target.read_bytes())
        self.assertEqual(1, self.owner.active_borrows)

    def test_cancel_during_native_write_stops_and_releases_writer(self):
        error = KeyboardInterrupt('synthetic cancellation')
        with patch.object(LinuxSnapshot, 'write_all', side_effect=error):
            with self.assertRaises(KeyboardInterrupt) as caught: self.store.initialize({'tick': 0})
        self.assertIs(error, caught.exception)
        self.assertEqual('STOPPED', self.store.state)
        self.assertEqual([], list(self.root.iterdir()))
        self.assertEqual(1, self.owner.active_borrows)

    def test_missing_factory_fails_closed_with_real_owner(self):
        with patch.object(LinuxSnapshot, '_from_parent_owner', None):
            with self.assertRaisesRegex(ValueError, 'not integrated'): self.store.initialize({'tick': 0})
        self.assertEqual('STOPPED', self.store.state)
        self.assertEqual([], list(self.root.iterdir()))
        self.assertEqual(1, self.owner.active_borrows)

    def test_finish_and_second_save_rejected_while_writer_active(self):
        entered = threading.Event(); release = threading.Event(); outcomes = []
        original = LinuxSnapshot.write_all
        def blocked(writer, chunks):
            entered.set()
            if not release.wait(5): raise RuntimeError('test synchronization timeout')
            return original(writer, chunks)
        def save():
            try: outcomes.append(self.store.initialize({'tick': 0}))
            except BaseException as error: outcomes.append(error)
        with patch.object(LinuxSnapshot, 'write_all', blocked):
            thread = threading.Thread(target=save); thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(2, self.owner.active_borrows)
                with self.assertRaises(ConcurrentStoreOperation): self.store.finish()
                with self.assertRaises(ConcurrentStoreOperation): self.store.save({'tick': 1})
                with self.assertRaises(ValueError): self.owner.close()
            finally:
                release.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(outcomes))
        self.assertFalse(isinstance(outcomes[0], BaseException), outcomes)
        self.assertEqual('ACTIVE', self.store.state)
        self.assertEqual(1, self.owner.active_borrows)

    def test_late_root_close_does_not_rewrite_commit_observation(self):
        accepted = self.store.initialize({'tick': 0})
        observation = accepted.observation
        self.store.finish()
        self.owner.close()
        self.assertEqual('CLOSED', self.owner.state)
        self.assertEqual(observation, accepted.observation)
        self.assertEqual('closed', observation.file_release)
