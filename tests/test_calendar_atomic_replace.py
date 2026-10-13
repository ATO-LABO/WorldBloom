"""Transient Windows replacement denial must not truncate authoritative state."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from engine.calendar_checkpoint import atomic_bytes
from execution.provenance import atomic_json


class AtomicReplaceTests(unittest.TestCase):
    def denied(self):
        error = PermissionError('transient Windows replacement denial')
        error.winerror = 5
        return error

    def test_transient_denial_retries_atomic_replace_without_exposing_partial_bytes(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'; path.write_bytes(b'old')
            real = os.replace; calls = []
            def replace(source, target):
                calls.append(target)
                self.assertEqual(path.read_bytes(), b'old')
                if len(calls) < 3: raise self.denied()
                return real(source, target)
            with patch('engine.calendar_checkpoint.os.replace', side_effect=replace), patch('engine.calendar_checkpoint.time.sleep'):
                atomic_bytes(path, b'new-complete')
            self.assertEqual(path.read_bytes(), b'new-complete'); self.assertEqual(len(calls), 3)

    def test_permanent_denial_is_bounded_and_preserves_previous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'; path.write_bytes(b'old')
            with patch('engine.calendar_checkpoint.os.replace', side_effect=self.denied()) as replace, patch('engine.calendar_checkpoint.time.sleep'), self.assertRaises(PermissionError):
                atomic_bytes(path, b'new')
            self.assertEqual(replace.call_count, 6); self.assertEqual(path.read_bytes(), b'old')
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ['state.json'])

    def test_other_failures_are_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            with patch('engine.calendar_checkpoint.os.replace', side_effect=OSError('disk error')) as replace, self.assertRaises(OSError):
                atomic_bytes(path, b'new')
            self.assertEqual(replace.call_count, 1)


class AtomicJsonReplaceTests(unittest.TestCase):
    def test_transient_windows_denial_keeps_old_json_until_complete_replacement(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'; atomic_json(path, {'completed': 1})
            real = os.replace; calls = []
            def replace(source, target):
                calls.append(target)
                self.assertEqual(path.read_text(), '{"completed":1}')
                if len(calls) < 3:
                    error = PermissionError('reader holds replacement'); error.winerror = 32
                    raise error
                return real(source, target)
            with patch('execution.provenance.os.replace', side_effect=replace), patch('execution.provenance.time.sleep'):
                atomic_json(path, {'completed': 2})
            self.assertEqual(path.read_text(), '{"completed":2}')
            self.assertEqual(len(calls), 3)

    def test_permanent_windows_denial_fails_bounded_without_partial_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'; atomic_json(path, {'completed': 1})
            error = PermissionError('persistent refusal'); error.winerror = 5
            with patch('execution.provenance.os.replace', side_effect=error) as replace, patch('execution.provenance.time.sleep'), self.assertRaises(PermissionError):
                atomic_json(path, {'completed': 2})
            self.assertEqual(replace.call_count, 6)
            self.assertEqual(path.read_text(), '{"completed":1}')
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ['progress.json'])

    def test_other_permission_failures_are_immediate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            with patch('execution.provenance.os.replace', side_effect=PermissionError('read-only directory')) as replace, self.assertRaises(PermissionError):
                atomic_json(path, {'completed': 2})
            self.assertEqual(replace.call_count, 1)
            self.assertFalse(path.exists())
