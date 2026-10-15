"""Run browser monitoring boundaries in the standard offline regression gate."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js is required for browser controller tests")
class GenerationControllerTests(unittest.TestCase):
    def test_preparation_output_creation_and_real_error_boundaries(self):
        result = subprocess.run(
            [shutil.which("node"), "--test", str(Path(__file__).with_suffix(".js"))],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
