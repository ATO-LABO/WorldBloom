"""Trial-gate unit tests for gapengine.world_patch_trial (WB-WORLDGROW-001, stage 3b).

Uses the same small/fast fixture project as tests/test_world_patch_cli.py
(duplicated here rather than imported, to keep this file runnable on its own
under unittest discover's per-file module loading).
"""
from __future__ import annotations

import json
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
    "facts": [],
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
        self.assertFalse(result["passed"])
        self.assertEqual(result["state"], "reference_only")
        self.assertEqual(result["seeds_per_run"], 2)
        self.assertEqual(result["base_source"], "repository")  # no manifest.json for this fixture
        self.assertIsNone(result["trigger"])


VALID_ADD_PLUS2 = {
    "zones": [{"name": "船大工の小屋", "parent": "海", "note": "船具を扱う小屋"}],
    "items": [{"name": "古びた帆布", "sources": [
        {"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 2}]}],
    "facts": [],
}


def _frozen_route_experiment(root: Path, *, kappa: float | None = None):
    """A ConfigStore-frozen momotaro_plus2 experiment with route_rho=1.0 --
    same shape as tests/world_patch_fixtures.py's frozen_experiment(), but
    parameterized for a route-carrying template/project (route.yaml exists
    under templates/momotaro_plus2 only) instead of momotaro's own. Needed
    (not the plain momotaro fixture above) because trial_state() only ever
    reports anything but "reference_only" for a "frozen_inputs" base_source
    (world_patch_trial.py's own gate), and WB-WORLDGROW-002 stage 0's fix is
    specifically about a route_cfg/rationality_cfg-carrying rerun reaching
    that state instead of failing reproduction.

    ``kappa`` (optional) also enables rationality, forced to
    rationality_backend="none" (never "ollama"/the template's own default)
    so this fixture never places a live network call -- NullJudge always
    returns None, so the table stays empty and multipliers()'s "missing"
    path always succeeds trivially (see TableOnlyJudgeTests in
    tests/test_rationality.py for backend="ollama"'s table-miss guard,
    which this fixture doesn't exercise). Still real enough to exercise the
    rationality_table_path/rationality_table_only wiring and the header's
    "rationality" key end to end."""
    from execution.configs import ConfigStore
    from gapengine.evolve import evolve, rationality_cfg_override, route_cfg_override

    root = Path(root)
    repo = root / "repo"
    project, template = repo / "projects/momotaro_plus2", repo / "templates/momotaro_plus2"
    shutil.copytree(ROOT / "projects/momotaro_plus2", project, ignore=shutil.ignore_patterns("patches"))
    shutil.copytree(ROOT / "templates/momotaro_plus2", template)
    for package in ("engine", "gapengine", "scripts", "execution", "viewer"):
        shutil.copytree(ROOT / package, repo / package, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ROOT / "requirements.txt", repo / "requirements.txt")
    store = ConfigStore(repo, root / "control", root / "runs")
    # Same generations/population/seeds as tests/test_lineage.py's
    # RouteLineageRerunTests (verified by hand to reach real archive cells
    # for this same rho=1.0 momotaro_plus2 fixture) -- a smaller pop/gens
    # here left archive.json with zero cells (nothing ever reached the
    # target ending), so run_trial's _select_cells() had nothing to pick.
    evolution = {"generations": 5, "population": 8, "seeds": 2, "seed_base": 11,
                 "ga_seed": 1, "processes": 1, "keep": "all", "record_explanations": True,
                 "coevolve": False, "route_rho": 1.0}
    if kappa is not None:
        evolution["kappa"] = kappa
        evolution["rationality_backend"] = "none"
    spec = {"label": "経路つき試験", "project_id": "momotaro_plus2", "template_id": "momotaro_plus2",
            "evolution": evolution}
    store.save(spec, config_id="cfg-route")
    manifest = store.prepare_run("cfg-route", run_id="run-route", job_id="job-route")
    experiment = root / "runs/run-route"
    evolve({**manifest["evolution"], "out": experiment,
            "project": experiment / "inputs/projects/momotaro_plus2",
            "template": experiment / "inputs/templates/momotaro_plus2",
            "rationality": rationality_cfg_override(manifest["evolution"]),
            "route": route_cfg_override(manifest["evolution"].get("route_rho"))})
    return experiment


class RouteTrialReproductionTests(unittest.TestCase):
    """WB-WORLDGROW-002 stage 0: before this fix, world_patch_trial's `_job`
    carried no route_cfg either, so its reproduction check (rerunning each
    exemplar with no patch and byte-comparing against the original) always
    mismatched for a rho>0 experiment -- trial_state() then reported
    "reference_only" no matter how good the patch was."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.experiment = _frozen_route_experiment(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_reproduction_matches_and_state_is_not_reference_only(self) -> None:
        patch = {"id": "p-route1", "title": "経路つき再現テスト", "add": VALID_ADD_PLUS2}
        with tempfile.TemporaryDirectory() as work:
            result = run_trial(self.experiment, patch, work_dir=Path(work), max_runs=3, seeds_per_run=2)

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["evidence"]["base_source"], "frozen_inputs")
        self.assertGreater(result["reproduction"]["checked"], 0)
        self.assertEqual(result["reproduction"]["mismatched"], [])
        self.assertEqual(
            result["reproduction"]["checked"], result["reproduction"]["identical"]
        )
        self.assertNotEqual(result["state"], "reference_only")


class RationalityTrialReproductionTests(unittest.TestCase):
    """WB-WORLDGROW-002 stage 0: same gap as RouteTrialReproductionTests
    above, but for rationality_cfg -- also exercises
    gapengine.world_patch_trial._reproduction_matches's
    table_hash_at_start allowance (see that function's docstring): kappa>0's
    shared rationality table keeps growing after any one individual's own
    seed finishes, so a strict byte comparison of the reproduction rerun
    against the original would otherwise report a mismatch even when every
    decision replayed identically."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.experiment = _frozen_route_experiment(Path(cls._tmp.name), kappa=1.0)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_reproduction_matches_and_state_is_not_reference_only(self) -> None:
        layer_files = sorted(self.experiment.glob("g0/ind-0/seed-*/layers.jsonl"))
        self.assertTrue(layer_files)
        header = json.loads(layer_files[0].read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("rationality", header)
        self.assertEqual(header["rationality"]["kappa"], 1.0)

        patch = {"id": "p-route-kappa1", "title": "合理性つき再現テスト", "add": VALID_ADD_PLUS2}
        with tempfile.TemporaryDirectory() as work:
            result = run_trial(self.experiment, patch, work_dir=Path(work), max_runs=3, seeds_per_run=2)

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["evidence"]["base_source"], "frozen_inputs")
        self.assertGreater(result["reproduction"]["checked"], 0)
        self.assertEqual(result["reproduction"]["mismatched"], [])
        self.assertEqual(
            result["reproduction"]["checked"], result["reproduction"]["identical"]
        )
        self.assertNotEqual(result["state"], "reference_only")


if __name__ == "__main__":
    unittest.main()
