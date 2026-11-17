"""Diagnostic-only guards: preserve existing close order and failure selection."""
from itertools import product
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine import calendar_fs as posix
from engine import calendar_windows_io as windows
from engine.calendar_parent_owner import _ExistingParentOwner
from engine.calendar_windows_writer import NativeWindowsAdapter


class RejectNoteError(OSError):
    def add_note(self, note):
        raise RuntimeError('note failed')


class RejectNoteCancellation(OSError):
    def add_note(self, note):
        raise KeyboardInterrupt('note cancellation')


class RejectCancellation(KeyboardInterrupt):
    def add_note(self, note):
        raise SystemExit('note replaces cancellation')


class RejectRepr(OSError):
    def __repr__(self):
        raise KeyboardInterrupt('repr cancellation')


KINDS = (None, OSError, KeyboardInterrupt, SystemExit, RejectNoteError,
         RejectNoteCancellation, RejectCancellation, RejectRepr)


class CleanupDiagnosticsTests(unittest.TestCase):
    def _matrix(self, backend):
        # 8 ** 3 combinations per backend, preserving primary/close ordering.
        for kinds in product(KINDS, repeat=3):
            with self.subTest(backend=backend, kinds=tuple(k.__name__ if k else None for k in kinds)):
                primary, first, second = [kind('injected') if kind else None for kind in kinds]
                closes = []
                failures = {3: first, 2: second}
                def close(value):
                    closes.append(value)
                    if failures.get(value) is not None: raise failures[value]
                errors = [e for e in (first, second) if e is not None]
                candidates = ([primary] if primary is not None else []) + errors
                expected = next((e for e in candidates if not isinstance(e, Exception)),
                                primary if primary is not None else next(iter(errors), None)) if errors else None
                escaped = None
                try:
                    if backend == 'posix':
                        descriptors = [1, 2, 3]
                        with patch.object(posix.os, 'close', side_effect=close):
                            posix._close_many(descriptors, primary=primary)
                        self.assertEqual(descriptors, [])
                    else:
                        native = SimpleNamespace(close=close)
                        handles = [windows._OwnedHandle(native, value) for value in (3, 2, 1)]
                        windows._close_owned(handles, primary=primary)
                except BaseException as error: escaped = error
                self.assertTrue(escaped is expected, 'Selected exception identity changed')
                self.assertEqual(closes, [3, 2, 1])
                if backend == 'posix':
                    self.assertEqual(descriptors, [])
                else:
                    self.assertTrue(all(handle._value is None for handle in handles))
                    for handle in handles: handle.close()
                    self.assertEqual(closes, [3, 2, 1])

    def test_posix_all_close_failure_diagnostic_combinations(self):
        self._matrix('posix')

    def test_windows_all_close_failure_diagnostic_combinations(self):
        self._matrix('windows')

    @unittest.skipUnless(sys.platform == 'linux', 'Actual POSIX acquisition test')
    def test_posix_mid_acquisition_cancel_with_failed_cleanup_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'selection'; target.mkdir()
            original_open, original_close = posix._open, os.close
            primary = RejectCancellation('original cancellation'); closed = []
            def opening(name, *args, **kwargs):
                if name == 'selection': raise primary
                return original_open(name, *args, **kwargs)
            def closing(fd):
                closed.append(fd); original_close(fd); raise OSError('cleanup')
            with patch.object(posix, '_open', side_effect=opening), patch.object(posix.os, 'close', side_effect=closing):
                with self.assertRaises(RejectCancellation) as caught:
                    _ExistingParentOwner(target, backend='linux')
            self.assertIs(caught.exception, primary)
            self.assertEqual(len(closed), len(set(closed)))
            self.assertGreater(len(closed), 0)
            self.assertEqual(primary.parent_release_state, 'RELEASE_UNCERTAIN')

    def test_windows_mid_acquisition_cancel_uses_existing_ledger_and_preserves_primary(self):
        primary = RejectCancellation('original cancellation'); closed = []; inspected = []
        metadata = SimpleNamespace(identity=(11, b'root'), directory=True)
        def close(value):
            closed.append(value); raise OSError('close anchor')
        def inspect(value):
            inspected.append(value); return metadata
        def child(*args): raise primary
        native = SimpleNamespace(close=close, inspect=inspect, open_relative=child)
        native.open_volume = lambda drive: windows._OwnedHandle(native, 71)
        adapter = NativeWindowsAdapter.__new__(NativeWindowsAdapter); adapter.reader = windows
        with patch.object(windows, '_Native', return_value=native), patch.object(windows, '_root_parts', return_value=('C:', ['child'])):
            with self.assertRaises(RejectCancellation) as caught:
                _ExistingParentOwner('fake native selection', backend='windows', adapter=adapter)
        self.assertIs(caught.exception, primary)
        self.assertEqual(closed, [71])
        self.assertEqual(inspected, [71, 71, 71])
        self.assertEqual(primary.parent_release_state, 'RELEASE_UNCERTAIN')


if __name__ == '__main__':
    unittest.main()
