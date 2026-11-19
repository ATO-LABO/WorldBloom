"""Isolated authored-slot reference checks; no live worlds or simulations."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from execution.library import LibraryStore, MAX_BYTES
from execution import time_slot_references as refs


def goal(mode="at", slot="10:00", day=1):
    return {"goal": {"all": [{"predicate": "at", "zone": "村"}],
                     "time": {"mode": mode, "day": day, "slot": slot} if mode != "any" else {"mode": "any"}}}


class TimeSlotReferencesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.project = self.repo / "projects/story"
        self.template = self.repo / "templates/genre"
        self.project.mkdir(parents=True)
        self.template.mkdir(parents=True)
        self.store = LibraryStore(self.repo)
        self.world = {"protagonist": "桃太郎", "gapengine": {"action_graph": "templates/genre/action_graph.yaml"},
                      "time": {"days": 2, "slots": ["09:00", "10:00", "11:00"]},
                      "scheduled_events": [{"id": "departure", "label": "出発", "day": 1, "slot": "10:00"}]}

    def write(self, root, value):
        (root / "route.yaml").write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")

    def snap(self):
        return refs.snapshot(self.store, "story", self.world)

    def test_scheduled_names_null_and_unrelated_strings(self):
        self.world["scheduled_events"] += [{"id": "daily", "day": 1}, {"slot": None}, {"slot": ""}]
        self.world["note"] = "10:00"
        catalog = self.snap()
        self.assertFalse(catalog["errors"])
        self.assertEqual(len(catalog["references"]), 1)
        self.assertEqual(catalog["references"][0]["path"], "scheduled_events.0.slot")
        self.assertEqual(catalog["references"][0]["label"], "出発")

    def test_world_override_and_inactive_template(self):
        self.write(self.template, goal(slot="11:00"))
        self.write(self.project, goal())
        catalog = self.snap()
        self.assertEqual(catalog["references"][-1]["source"], "world")
        self.assertEqual(catalog["references"][-1]["slot"], "10:00")
        before = catalog["dependency_hash"]
        (self.template / "route.yaml").write_text("invalid: [", encoding="utf-8")
        self.assertEqual(self.snap()["dependency_hash"], before)
        self.assertFalse(self.snap()["errors"])

    def test_template_fallback_and_template_content_hash(self):
        self.write(self.template, goal(slot="11:00"))
        catalog = self.snap()
        self.assertEqual(catalog["references"][-1]["source"], "template")
        self.assertEqual(catalog["references"][-1]["path"], "templates/genre/route.yaml.goal.time.slot")
        self.write(self.template, goal(slot="10:00"))
        self.assertNotEqual(catalog["dependency_hash"], self.snap()["dependency_hash"])

    def test_official_same_name_genre_fallback(self):
        self.world.pop("gapengine")
        fallback = self.repo / "templates/story"
        fallback.mkdir()
        self.write(fallback, goal(slot="11:00"))
        catalog = self.snap()
        self.assertFalse(catalog["errors"])
        self.assertEqual(catalog["references"][-1]["path"], "templates/story/route.yaml.goal.time.slot")

    def test_historical_run_route_is_not_an_active_dependency(self):
        before = self.snap()["dependency_hash"]
        historical = self.repo / "runs/old/inputs/projects/story"
        historical.mkdir(parents=True)
        self.write(historical, goal(slot="23:59"))
        self.assertEqual(self.snap()["dependency_hash"], before)
        self.assertFalse(any(r["slot"] == "23:59" for r in self.snap()["references"]))

    def test_absence_creation_and_project_content_hash(self):
        empty = self.snap()["dependency_hash"]
        self.write(self.project, goal())
        created = self.snap()["dependency_hash"]
        self.assertNotEqual(empty, created)
        self.write(self.project, goal(slot="11:00"))
        self.assertNotEqual(created, self.snap()["dependency_hash"])

    def test_genre_selection_changes_hash_even_with_override(self):
        self.write(self.project, goal())
        before = self.snap()["dependency_hash"]
        self.world["gapengine"]["action_graph"] = "templates/other/action_graph.yaml"
        self.assertNotEqual(before, self.snap()["dependency_hash"])

    def test_no_genre_no_route_does_not_block_and_project_ref_is_retained(self):
        self.world.pop("gapengine")
        catalog = self.snap()
        self.assertFalse(catalog["errors"])
        self.assertFalse(catalog["warnings"])
        self.write(self.project, goal())
        catalog = self.snap()
        self.assertFalse(catalog["errors"])
        self.assertEqual(catalog["references"][-1]["kind"], "route_survey_goal")
        self.assertTrue(catalog["warnings"])

    def test_any_and_null_goal_have_no_named_survey_reference(self):
        for config, explicit in [(goal("any"), True), ({"goal": None}, False), ({}, False)]:
            with self.subTest(config=config):
                self.write(self.project, config)
                catalog = self.snap()
                self.assertFalse(catalog["errors"])
                self.assertEqual(len(catalog["references"]), 1)
                self.assertEqual(catalog["has_survey_goal"], explicit)
                self.assertFalse(catalog["has_timed_survey_goal"])

    def test_goal_parser_does_not_add_editor_only_condition_count_limit(self):
        config = goal()
        config["goal"]["all"] *= 101
        self.write(self.project, config)
        catalog = self.snap()
        self.assertFalse(catalog["errors"])
        self.assertTrue(catalog["has_timed_survey_goal"])

    def test_invalid_route_yaml_root_duplicate_and_time_are_errors(self):
        samples = ["bad: [", "[]", "", "goal: null\ngoal: null\n", "goal:\n  all: []\n",
                   yaml.safe_dump({"goal": {"all": [{"predicate": "at", "zone": "村"}], "time": {"mode": "at", "slot": None}}}),
                   yaml.safe_dump({"goal": {"all": [{"predicate": "at", "zone": "村"}], "time": {"mode": "any", "slot": "10:00"}}})]
        for text in samples:
            with self.subTest(text=text):
                (self.project / "route.yaml").write_text(text, encoding="utf-8")
                catalog = self.snap()
                self.assertTrue(catalog["errors"])
                self.assertNotIn(str(self.repo), json.dumps(catalog, ensure_ascii=False))

    def test_oversize_and_unreadable_are_errors(self):
        path = self.project / "route.yaml"
        path.write_bytes(b"x" * (MAX_BYTES + 1))
        self.assertTrue(self.snap()["errors"])
        self.write(self.project, goal())
        original = Path.open
        def refused(path, *args, **kwargs):
            if path == self.project / "route.yaml":
                raise PermissionError("private absolute path must not leak")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", refused):
            catalog = self.snap()
        self.assertTrue(catalog["errors"])
        self.assertNotIn("private absolute", json.dumps(catalog))

    def test_directory_link_and_identifier_safety(self):
        self.assertTrue(refs.snapshot(self.store, "../story", self.world)["errors"])
        (self.project / "route.yaml").mkdir()
        self.assertTrue(self.snap()["errors"])
        (self.project / "route.yaml").rmdir()
        from execution.provenance import ConfigError
        original = refs.contained
        def unsafe(root, relative):
            if relative == "route.yaml":
                raise ConfigError("path", "link forbidden")
            return original(root, relative)
        with patch.object(refs, "contained", unsafe):
            catalog = self.snap()
        self.assertTrue(catalog["errors"])
        self.assertNotIn(str(self.repo), json.dumps(catalog))

    def test_missing_names_and_route_day_are_rejected(self):
        self.write(self.project, goal(slot="11:00", day=2))
        catalog = self.snap()
        errors, _ = refs.validate(catalog, ["09:00"], 1)
        self.assertTrue(any("出発" in e["message"] for e in errors))
        self.assertTrue(any("経路調査" in e["message"] for e in errors))
        self.assertTrue(any("期間" in e["message"] for e in errors))

    def test_order_count_and_analysis_projection_warn_without_search(self):
        self.write(self.project, goal())
        catalog = self.snap()
        errors, warnings = refs.validate(catalog, ["11:00", "10:00", "09:00"], 1)
        self.assertFalse(errors)
        self.assertTrue(any("投影" in w["message"] for w in warnings))
        self.assertTrue(any("到達不可能とは判定" in w["message"] for w in warnings))
        self.write(self.project, goal("any"))
        self.assertTrue(refs.validate(self.snap(), ["09:00", "10:00", "朝"], 1)[1])

    def test_valid_unchanged_and_no_mutation_no_world_writes(self):
        self.write(self.project, goal())
        before_world = deepcopy(self.world)
        before_files = {p.relative_to(self.repo).as_posix(): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        catalog = self.snap()
        before_catalog = deepcopy(catalog)
        slots = list(self.world["time"]["slots"])
        self.assertEqual(refs.validate(catalog, slots, 2), ([], []))
        self.assertEqual(self.world, before_world)
        self.assertEqual(catalog, before_catalog)
        self.assertEqual(slots, self.world["time"]["slots"])
        self.assertEqual(before_files, {p.relative_to(self.repo).as_posix(): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()})

    def test_invalid_candidate_shape_does_not_crash(self):
        for slots, days in [("09:00", 1), ([], 1), (["09:00", "09:00"], 1), (["09:00"], True), ([None], 1)]:
            with self.subTest(slots=slots, days=days):
                self.assertTrue(refs.validate(self.snap(), slots, days)[0])


if __name__ == "__main__":
    unittest.main()
