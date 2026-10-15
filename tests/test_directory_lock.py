"""A competing first writer cannot initialize an already locked empty file."""
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest

from execution.provenance import ConfigError, directory_lock


def attempt_lock(root, queue):
    try:
        with directory_lock(Path(root)):
            queue.put("acquired")
    except ConfigError as exc:
        queue.put(exc.code)
    except Exception as exc:
        queue.put(type(exc).__name__)


class DirectoryLockFirstWriteTests(unittest.TestCase):
    def test_locked_empty_file_reports_conflict_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lockfile = root / ".write.lock"
            context = multiprocessing.get_context("spawn")
            queue = context.Queue()
            process = context.Process(target=attempt_lock, args=(str(root), queue))
            with lockfile.open("a+b") as handle:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    process.start()
                    self.assertEqual(queue.get(timeout=20), "conflict")
                    process.join(timeout=20)
                    self.assertEqual(process.exitcode, 0)
                    self.assertEqual(lockfile.stat().st_size, 0)
                finally:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=20)
                    process.close()
                    queue.close()
                    queue.join_thread()
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle, fcntl.LOCK_UN)
            with directory_lock(root):
                pass
            self.assertEqual(lockfile.read_bytes(), b"0")
