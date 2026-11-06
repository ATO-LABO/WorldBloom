from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine.genome import Genome
from gapengine.policy import Policy


DATE_KEYS = {"date", "weekday", "weekday_name", "is_weekend", "is_holiday", "is_non_working_day"}


def calendar(first="2028-02-28", last="2028-03-01", holidays=None):
    return {"version": 1, "start_date": first, "end_date": last,
            "weekend_days": [5, 6], "holidays": [] if holidays is None else holidays}


def fixture():
    world = {"name": "logical dates fixture", "story_genres": ["stamina"], "story_genres_version": 3,
             "time": {"days": 3, "slots": ["朝", "夜"], "author_note": "retain"},
             "protagonist": "a", "antagonist": "b", "zones": [{"name": "home"}],
             "routes": {}, "items": [], "ending": [{"id": "end", "when": "turn >= 99999"}],
             "target_ending": ["end"], "scheduled_events": [
                 {"id": "due", "label": "integer deadline", "day": 3, "slot": "夜",
                  "targets": ["a"], "stress_delta": 0.3}]}
    def person(ident):
        return {"id": ident, "traits": {k: .5 for k in
                ("social", "stubbornness", "curiosity", "diligence", "temper")},
                "base": 50, "range": {"entry": "home", "zones": ["home"]},
                "verbs": ["rest"], "goal": {}}
    return world, [person("a"), person("b")]


class CountingPolicy(Policy):
    def __init__(self):
        super().__init__(Genome.neutral(), None)
        self.reweight_calls = 0

    def reweight(self, *args, **kwargs):
        self.reweight_calls += 1
        return super().reweight(*args, **kwargs)


class LogicalDateRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="logical-date-runtime-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.world, self.people = fixture()

    def run_world(self, definition, folder, policy=None):
        source = self.repo / folder / "world.yaml"
        source.parent.mkdir()
        source.write_text(yaml.safe_dump(definition, allow_unicode=True), encoding="utf-8")
        world = World(deepcopy(definition), source)
        people = {p["id"]: Subject.from_definition(deepcopy(p)) for p in self.people}
        sim = Simulation(73, world, people, self.repo / (folder + "-output"),
                         policies=None if policy is None else {"a": policy})
        rows = [json.loads(line) for line in sim.run().read_text(encoding="utf-8").splitlines()]
        return sim, rows

    def test_ordinary_rounds_event_timing_and_state_equal_with_date_labels(self):
        ordinary, original = self.run_world(self.world, "ordinal")
        dated = deepcopy(self.world)
        dated["time"].update(date_calendar=calendar(holidays=["2028-02-29"]),
                              presentation={"calendar": "real", "advance": "bands"})
        simulation, labeled = self.run_world(dated, "dated")
        self.assertIsNone(simulation.slot_runtime)
        self.assertEqual(simulation.time_kernel.axis, ordinary.time_kernel.axis)
        self.assertEqual(simulation.rng.getstate(), ordinary.rng.getstate())
        def physical(rows):
            return [{k: v for k, v in row.items() if k not in DATE_KEYS}
                    for row in rows if row.get("kind") != "header"]
        self.assertEqual(physical(original), physical(labeled))
        decisions = [r for r in labeled if r.get("kind") == "decision"]
        self.assertEqual(len(decisions), 12, "2 actors × 3 days × 2 rounds")
        self.assertEqual({r["turn"] for r in decisions}, set(range(1, 7)))
        self.assertEqual({r["slot"] for r in decisions}, {"朝", "夜"})
        self.assertEqual(labeled[0]["date_calendar"], dated["time"]["date_calendar"])
        for row in labeled:
            if type(row.get("day")) is int and row["day"] > 0:
                self.assertEqual(row["date"], (date(2028, 2, 28) + timedelta(days=row["day"]-1)).isoformat())
                self.assertEqual(row["weekday"], date.fromisoformat(row["date"]).isoweekday())
        holiday_rows = [r for r in decisions if r["day"] == 2]
        self.assertTrue(all(r["is_holiday"] and r["is_non_working_day"] for r in holiday_rows))
        due = [r for r in labeled if r.get("id") == "due" and r.get("verb") == "scheduled_event"]
        self.assertTrue(due, "authored integer-day schedule must still fire")
        self.assertTrue(all(r["day"] == 3 and r["turn"] == 6 for r in due))

    def test_actual_ga_policy_is_accepted_and_executed_with_real_date_labels(self):
        dated = deepcopy(self.world)
        dated["time"].update(date_calendar=calendar(), presentation={"calendar": "real", "advance": "bands"})
        policy = CountingPolicy()
        simulation, rows = self.run_world(dated, "ga-policy", policy)
        self.assertIsNone(simulation.slot_runtime)
        self.assertGreater(policy.reweight_calls, 0)
        decisions = [r for r in rows if r.get("kind") == "decision" and r["subject"] == "a"]
        self.assertEqual(len(decisions), 6)
        self.assertTrue(all(r["date"] and r["policy"] is not None for r in decisions))

    def test_legacy_world_has_no_fabricated_date_or_holiday(self):
        simulation, rows = self.run_world(self.world, "legacy")
        self.assertIsNone(simulation.world.date_calendar)
        self.assertEqual(simulation.world.time_context(1), {})
        self.assertEqual(simulation.world.time_context(0), {})
        self.assertNotIn("date_calendar", rows[0])
        self.assertFalse(any(DATE_KEYS.intersection(row) for row in rows))
