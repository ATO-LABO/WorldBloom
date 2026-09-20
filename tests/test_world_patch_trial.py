"""Trial-gate unit tests for gapengine.world_patch_trial (WB-WORLDGROW-001, stage 3b).

Uses the same small/fast fixture project as tests/test_world_patch_cli.py
(duplicated here rather than imported, to keep this file runnable on its own
under unittest discover's per-file module loading).
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from gapengine.evolve import evolve
from gapengine.world_patch_trial import run_trial

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "momotaro"
TEMPLATE = ROOT / "templates" / "momotaro"

VALID_ADD = {
    "zones": [{"name": "船大工の小屋", "parent": "海", "note": "船具を扱う小屋"}],
    "items": [{"name": "古びた帆布", "sources": [
        {"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 2}]}],
    "facts": [], "daily_events": [],
}


def _make_fast_project(root: Path) -> Path:
    """A 1-day, guaranteed-homecoming momotaro copy -- keeps this fixture
    experiment's own evolve() run fast and non-flaky."""
    project = root / "fast-project"
    shutil.copytree(PROJECT, project)
    world_path = project / "world.yaml"
    world_raw = yaml.safe_load(world_path.read_text(encoding="utf-8"))
    world_raw["time"] = {"days": 1, "slots": ["朝"]}
    world_raw["daily_events"] = None
    world_raw["scheduled_events"] = [{
        "id": "test_guaranteed_homecoming", "day": 1, "slot": "朝", "targets": ["桃太郎"],
        "label": "決定性テスト用の帰還", "grants_item": {"name": "鬼ヶ島の宝物", "count": 1}, "move_to": "村",
    }]
    world_path.write_text(yaml.safe_dump(world_raw, allow_unicode=True, sort_keys=False),
                           encoding="utf-8", newline="\n")
    return project


class RunTrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        fast_project = _make_fast_project(root)
        evolve({
            "ga_seed": 29, "generations": 2, "keep": "all", "population": 5, "processes": 1,
            "project": fast_project, "seed_base": 31, "seeds": 1, "template": TEMPLATE,
            "out": root / "experiment",
        })
        cls.experiment = root / "experiment"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_no_trigger_patch_runs_without_crashing(self) -> None:
        # No "trigger" key at all -- run_trial must not KeyError reading
        # patch["trigger"]["zone"]/["verb"] and must report trigger=None.
        patch = {"id": "p-notrigger1", "title": "きっかけなしのパッチ", "add": VALID_ADD}
        with tempfile.TemporaryDirectory() as work:
            result = run_trial(self.experiment, patch, work_dir=Path(work), max_runs=2, seeds_per_run=2)

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["total_runs"], 4)  # 2 individuals x 2 seeds, none failed
        self.assertEqual(result["tolerance"], 2)  # max(2, ceil(0.2*4)) == 2
        self.assertEqual(result["seeds_per_run"], 2)
        self.assertEqual(result["base_source"], "repository")  # no manifest.json for this fixture
        self.assertIsNone(result["trigger"])


if __name__ == "__main__":
    unittest.main()
