"""Include the shipped browser controller's retry regression in the offline gate."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js is required for browser controller tests")
class FactWorkspaceControllerTests(unittest.TestCase):
    def test_response_loss_draft_and_history_boundaries(self):
        script = Path(__file__).with_suffix(".js")
        result = subprocess.run([shutil.which("node"), "--test", str(script)],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
