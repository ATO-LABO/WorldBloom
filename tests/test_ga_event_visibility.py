"""Read complete GA events under real Windows sharing denial; fail closed."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from execution import evolution_worker as worker


class GaEventVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wb-event-read-')
        self.addCleanup(self.temp.cleanup)
        self.observer = worker.EvolutionObserver(Path(self.temp.name), 'run-event-read', {})
        self.event = self.observer.events / '0-protagonist-7-32-completed.json'
        self.payload = {'event_id': '0-protagonist-7-32', 'kind': 'completed'}
        self.data = json.dumps(self.payload).encode('utf-8')
        self.event.write_bytes(self.data)

    def deny_reads(self):
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        handle = api.CreateFileW(str(self.event), 0x80000000, 6, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return api, handle

    @unittest.skipUnless(os.name == 'nt', 'real Windows file sharing')
    def test_transient_real_read_denial_keeps_complete_event(self):
        api, handle = self.deny_reads()
        denied, closed = threading.Event(), threading.Event()
        original_read = worker.read_json
        failures = []

        def observe_read(path):
            try:
                return original_read(path)
            except PermissionError as error:
                failures.append(error.errno)
                denied.set()
                raise

        def release():
            try:
                denied.wait(3)
            finally:
                api.CloseHandle(handle)
                closed.set()

        thread = threading.Thread(target=release)
        thread.start()
        try:
            with mock.patch.object(worker, 'read_json', side_effect=observe_read):
                self.observer._drain()
        finally:
            denied.set()
            thread.join(4)
        self.assertTrue(closed.is_set())
        self.assertTrue(failures, 'test must observe an actual OS denial')
        self.assertEqual(self.observer.completed, {self.payload['event_id']: self.payload})
        self.assertEqual(self.observer.seen, {self.event.name})
        self.observer._drain()
        self.assertEqual(len(self.observer.completed), 1)
        self.assertEqual(self.event.read_bytes(), self.data)

    @unittest.skipUnless(os.name == 'nt', 'real Windows file sharing')
    def test_persistent_real_read_denial_remains_an_error(self):
        api, handle = self.deny_reads()
        try:
            with self.assertRaises(PermissionError):
                self.observer._drain()
        finally:
            api.CloseHandle(handle)
        self.assertEqual(self.observer.seen, set())
        self.assertEqual(self.observer.completed, {})
        self.assertEqual(self.event.read_bytes(), self.data)

    def test_corrupt_json_remains_an_error_and_unseen(self):
        self.event.write_bytes(b'{broken')
        with self.assertRaises(json.JSONDecodeError):
            self.observer._drain()
        self.assertEqual(self.observer.seen, set())
        self.assertEqual(self.observer.completed, {})
        self.assertEqual(self.event.read_bytes(), b'{broken')

    def test_unrelated_io_failure_remains_an_error_and_unseen(self):
        with mock.patch.object(worker, 'read_json', side_effect=OSError(5, 'I/O error')):
            with self.assertRaises(OSError):
                self.observer._drain()
        self.assertEqual(self.observer.seen, set())

    def test_non_windows_permission_failure_remains_an_error(self):
        error = PermissionError(13, 'denied')
        with mock.patch.object(worker.os, 'name', 'posix'):
            with mock.patch.object(worker, 'read_json', side_effect=error):
                with mock.patch.object(worker.time, 'sleep') as delay:
                    with self.assertRaises(PermissionError):
                        self.observer._drain()
                    delay.assert_not_called()
        self.assertEqual(self.observer.seen, set())

    def test_valid_event_is_unchanged_and_counted_once(self):
        self.observer._drain()
        self.observer._drain()
        self.assertEqual(self.observer.completed, {self.payload['event_id']: self.payload})
        self.assertEqual(self.observer.seen, {self.event.name})
        self.assertEqual(self.event.read_bytes(), self.data)
