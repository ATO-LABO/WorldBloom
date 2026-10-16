"""Historical comparisons fail closed if their frozen input changes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import historical_fixtures


class HistoricalFixtureTests(unittest.TestCase):
    def test_original_inputs_are_available_without_git(self):
        qd = historical_fixtures.load_historical_fixture("pre_growth_s3_qd.yaml")
        self.assertNotIn(b"arc_bins", qd)
        harness = historical_fixtures.load_historical_fixture("original_raw_workspace_ui.cjs")
        self.assertIn(b"assert", harness)

    def test_modified_input_is_rejected_without_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = (historical_fixtures.FIXTURES / "manifest.json").read_bytes()
            (root / "manifest.json").write_bytes(manifest)
            name = "pre_growth_s3_qd.yaml"
            (root / name).write_bytes(b"modified baseline\n")
            with patch.object(historical_fixtures, "FIXTURES", root):
                with self.assertRaisesRegex(ValueError, "fixture changed"):
                    historical_fixtures.load_historical_fixture(name)
