"""Run the offline JS controller behavior tests; no package installation."""
from pathlib import Path
import shutil
import subprocess
import unittest


class DraftControllerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_offline_controller(self):
        result = subprocess.run([shutil.which("node"), str(Path(__file__).with_suffix(".js"))],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("PASS "), 17)
