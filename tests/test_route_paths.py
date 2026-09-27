"""WB-ROUTE-PATHS-001: "最短経路を調査" -- tests for gapengine/route_paths.py.

route.py itself is never touched by this feature (see the module docstring
of route_paths.py) -- these tests only exercise the new, read-only survey
layer built on top of it.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import yaml

from unittest import mock

from execution.configs import ConfigStore
from gapengine.route import load_route_config, plan
from gapengine.route_paths import state_at, survey, timepoints, walk
from viewer import data as viewer_data
from viewer.data import RunRepository
from viewer.server import ViewerHandler, ViewerServer

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "momotaro_plus2"
TEMPLATE = ROOT / "templates" / "momotaro_plus2"


def _survey_script() -> str:
    return (
        "import sys, json\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "from gapengine.route_paths import survey\n"
        f"result = survey(Path({str(PROJECT)!r}), Path({str(TEMPLATE)!r}))\n"
        "sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))\n"
    )


class SurveyDeterminismTests(unittest.TestCase):
    def test_survey_called_twice_is_identical(self) -> None:
        first = survey(PROJECT, TEMPLATE)
        second = survey(PROJECT, TEMPLATE)
        self.assertEqual(first, second)

    def test_survey_is_byte_identical_across_two_hash_seeds(self) -> None:
        script = _survey_script()
        outputs = []
        for hash_seed in ("1", "2"):
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = hash_seed
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])

    def test_survey_does_not_mutate_project_yaml(self) -> None:
        world_yaml = PROJECT / "world.yaml"
        before = world_yaml.read_bytes()
        subject_files = {
            path: path.read_bytes() for path in sorted((PROJECT / "subjects").glob("*.yaml"))
        }
        survey(PROJECT, TEMPLATE)
        self.assertEqual(world_yaml.read_bytes(), before)
        for path, content in subject_files.items():
            self.assertEqual(path.read_bytes(), content)


class SurveyMomotaroPlus2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = survey(PROJECT, TEMPLATE)

    def test_status_ok(self) -> None:
        self.assertEqual(self.result["status"], "ok")
        self.assertEqual(len(self.result["timepoints"]), 1)

    def test_start_state(self) -> None:
        start = self.result["timepoints"][0]["start"]
        self.assertEqual(start["zone"], "道中")
        self.assertEqual(start["inventory"].get("縄"), 1)

    def test_multiple_distinct_routes(self) -> None:
        routes = self.result["timepoints"][0]["routes"]
        self.assertGreaterEqual(len(routes), 2)
        signatures = [tuple(map(tuple, route["signature"])) for route in routes]
        self.assertEqual(len(signatures), len(set(signatures)))

    def test_first_route_h_matches_plan(self) -> None:
        world, subjects = state_at(PROJECT, TEMPLATE, (1, "朝"))
        cfg = load_route_config(TEMPLATE)
        assert cfg is not None
        subject = subjects[world.protagonist]
        expected = plan(
            subject,
            world,
            holder_belief_fact=cfg["holder_belief_fact"],
            trial_reveal_facts=cfg["trial_reveal_facts"],
            min_win_prob=cfg["min_win_prob"],
        )
        route = self.result["timepoints"][0]["routes"][0]
        self.assertEqual(route["h"], expected["h"])
        if route["steps"] and not route["truncated"]:
            self.assertAlmostEqual(route["steps"][-1]["cumulative"], expected["h"], places=4)

    def test_steps_cumulative_is_non_decreasing_for_every_route(self) -> None:
        # S2 (Opus review): route["h"] is route.py's own upfront estimate,
        # computed once before any knockout-specific detour is actually
        # walked step by step -- it can legitimately differ from the walked
        # total once a route resolves a danger gate earlier or later than
        # the flat fallback cost assumed, so this only checks internal
        # consistency (cumulative never decreases, reaches the labeled
        # "cost" when completed) rather than route["h"] equality.
        for route in self.result["timepoints"][0]["routes"]:
            with self.subTest(route=route["label"]):
                cumulative = 0.0
                for step in route["steps"]:
                    self.assertGreaterEqual(step["cost"], 0.0)
                    cumulative += step["cost"]
                    self.assertAlmostEqual(step["cumulative"], cumulative, places=4)
                if not route["truncated"]:
                    self.assertTrue(route["steps"])
                    self.assertEqual(route["steps"][-1]["zone"], "村")
                    self.assertAlmostEqual(route["cost"], cumulative, places=4)

    def test_first_route_offers_the_free_magatama_not_the_gun(self) -> None:
        # M1 (Opus review): momotaro starts holding 勾玉, a free (h=0)
        # negotiate offer per route.py's own _best_offer -- 鉄砲 is only
        # ever fetched as a pure strength item (_first_strength_item), never
        # offered. walk() used to hand over whatever lootable+modifier item
        # it had most recently picked up for *any* reason, which could name
        # 鉄砲 instead.
        route = self.result["timepoints"][0]["routes"][0]
        negotiate_steps = [s for s in route["steps"] if s["kind"] == "negotiate"]
        self.assertTrue(negotiate_steps)
        text = negotiate_steps[0]["text"]
        self.assertIn("勾玉", text)
        self.assertNotIn("鉄砲", text)

    def test_a_fight_route_is_found(self) -> None:
        # S1 (Opus review): under momotaro_plus2's danger gate, a
        # danger-gated plan's `best` never carries a ("route", ...) tag, so
        # _knockouts_for used to never even try knocking out negotiate, and
        # _signature (keyed only on best tags) couldn't tell a fight-
        # resolving plan apart from a negotiate-resolving one with the same
        # tags anyway. Both are fixed now (route_cfg["route"] itself drives
        # both the knockout and the signature).
        routes = self.result["timepoints"][0]["routes"]
        self.assertTrue(any(route["route"] == "fight" for route in routes))

    def test_first_step_is_investigating_koban_at_michinaka(self) -> None:
        route = self.result["timepoints"][0]["routes"][0]
        self.assertTrue(route["steps"])
        first = route["steps"][0]
        self.assertEqual(first["zone"], "道中")
        self.assertIn("小判", first["text"])

    def test_last_step_reaches_village(self) -> None:
        route = self.result["timepoints"][0]["routes"][0]
        self.assertFalse(route["truncated"])
        last = route["steps"][-1]
        self.assertEqual(last["zone"], "村")


class StallDetectionTests(unittest.TestCase):
    """M2 (Opus review): a fixture with no fight verb, no way to craft the
    danger-gate boost item, and no trials at all used to make
    ``_apply_stance_leaf`` re-raise the exact same relation by the exact
    same delta forever -- observed as "○○と親しくなる" repeated ~53 times
    before the 60-step budget cut it off. Both the stance-threshold fix
    (M2a) and the state-fingerprint stall detector (M2b) are exercised
    here; either alone would have been enough to stop the loop, but not
    necessarily quickly -- this asserts it stops *fast*."""

    @staticmethod
    def _strip_to_dead_end(world, subjects) -> None:
        protagonist = world.protagonist
        subjects[protagonist].verbs.discard("fight")
        subjects[protagonist].inventory.pop("勾玉", None)
        world.recipes.pop("鉄砲", None)
        if "鉄砲" in world.items:
            world.items["鉄砲"]["sources"] = []
        world.trials.clear()

    def test_walk_does_not_loop_the_same_stance_raise_dozens_of_times(self) -> None:
        cfg = load_route_config(TEMPLATE)
        assert cfg is not None
        result = walk(PROJECT, TEMPLATE, (1, "朝"), cfg, mutate=self._strip_to_dead_end)
        # With the stance-threshold fix (M2a), this fixture actually
        # completes cleanly now (train -> negotiate with no offer item, a
        # single stance raise) rather than merely stalling faster -- but
        # what actually regressed was the *loop*, so assert on that
        # directly rather than on truncated/completed either way.
        stance_steps = [s for s in result["steps"] if s["kind"] == "stance"]
        self.assertLess(len(stance_steps), 5, stance_steps)
        self.assertLess(len(result["steps"]), 20, result["steps"])


class TimepointsTests(unittest.TestCase):
    def test_start_is_always_first(self) -> None:
        world, _ = state_at(PROJECT, TEMPLATE, (1, "朝"))
        tps = timepoints(world)
        self.assertEqual(tps[0], (1, "朝"))


class TemplatesWithoutRouteConfigTests(unittest.TestCase):
    def test_momotaro_is_no_route_config(self) -> None:
        result = survey(ROOT / "projects" / "momotaro", ROOT / "templates" / "momotaro")
        self.assertEqual(result["status"], "no_route_config")
        self.assertEqual(result["timepoints"], [])

    def test_detective_is_no_route_config(self) -> None:
        # WB-ROUTE-PATHS-001's own design expected detective to surface as
        # "no_goal" (a non-fetch-and-deliver goal shape); in practice
        # templates/detective has no route.yaml either, so load_route_config
        # already returns None before goal.target is ever inspected --
        # recorded here rather than forcing a goal shape that doesn't exist.
        self.assertIsNone(load_route_config(ROOT / "templates" / "detective"))
        result = survey(ROOT / "projects" / "detective", ROOT / "templates" / "detective")
        self.assertEqual(result["status"], "no_route_config")


class RoutesApiTests(unittest.TestCase):
    """GET /api/worlds/<id>/routes (viewer/library_pages.py:_worlds_routes)."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="wb-route-paths-http-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        for name in ("projects", "templates"):
            shutil.copytree(ROOT / name, self.repo / name)
        self.control = self.base / "control"
        self.runs = self.base / "runs"
        self.runs.mkdir()
        configs = ConfigStore(self.repo, self.control, self.runs)

        class FakeJobStore:
            def __init__(self, configs) -> None:
                self.configs = configs

            def list(self):
                return []

        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = RunRepository(self.runs, control_root=self.control, jobs=None)
        self.server.job_store = FakeJobStore(configs)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw)
        finally:
            conn.close()

    def test_momotaro_plus2_routes_ok(self) -> None:
        status, payload = self.get("/api/worlds/momotaro_plus2/routes")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["timepoints"])

    def test_unknown_world_is_a_json_error(self) -> None:
        status, payload = self.get("/api/worlds/does-not-exist/routes")
        self.assertEqual(status, 404, payload)
        self.assertEqual(payload["code"], "not_found")


class RoutesApiReadOnlyViewerTests(unittest.TestCase):
    """nice (Opus review): the endpoint's own docstring claims it works for
    a read-only Viewer (no job_store) too -- this exercises that path
    directly instead of only ever testing the job_store-present branch."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="wb-route-paths-viewer-")
        self.addCleanup(self.temp.cleanup)
        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = RunRepository(Path(self.temp.name))
        self.server.job_store = None
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw)
        finally:
            conn.close()

    def test_momotaro_plus2_routes_ok_without_job_store(self) -> None:
        with mock.patch.object(viewer_data, "ROOT", ROOT):
            status, payload = self.get("/api/worlds/momotaro_plus2/routes")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
