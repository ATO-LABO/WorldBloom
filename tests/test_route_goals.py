"""Goal semantics, temporal search proofs/budgets and compatibility boundaries."""
from copy import deepcopy
from pathlib import Path
import random
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import yaml

from engine.actions import Action
from engine.subject import Subject
from engine.verbs import VerbEngine
from engine.world import World
from engine.world import Route as WorldRoute
from gapengine.route_goals import resolve_goal
from gapengine.route_paths import survey
from gapengine.route_search import search, UnsupportedModel

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples/route_goals/cultural_festival"


def fixture():
    world = World.from_yaml(SAMPLE / "world.yaml")
    people = {p.id: p for p in (Subject.from_yaml(f) for f in sorted((SAMPLE / "subjects").glob("*.yaml")))}
    world.bind_subjects(people)
    return world, people


def raw_goal(mode="at", slot="15:00", **extra):
    return {"actor": "主役", "all": [{"predicate": "at", "zone": "体育館"},
            {"predicate": "holds", "item": "衣装"}],
            "time": {"mode": mode, "day": 1, "slot": slot} if mode != "any" else {"mode": mode}, **extra}


class GoalTests(unittest.TestCase):
    def test_at_and_by_have_distinct_boundary_semantics(self):
        world, people = fixture()
        people["主役"].zone = "体育館"
        people["主役"].add_item("衣装", 1)
        exact = resolve_goal(raw_goal(), world, people)
        deadline = resolve_goal(raw_goal("by"), world, people)
        self.assertFalse(exact.satisfied(people, 1, "14:30"))
        self.assertTrue(exact.satisfied(people, 1, "15:00"))
        self.assertFalse(exact.satisfied(people, 1, "15:30"))
        self.assertTrue(deadline.satisfied(people, 1, "14:30"))
        self.assertTrue(deadline.satisfied(people, 1, "15:00"))
        self.assertFalse(deadline.satisfied(people, 1, "15:30"))
        people["主役"].inventory.clear()
        self.assertFalse(exact.satisfied(people, 1, "15:00"))

    def test_legacy_delivery_adapts_to_the_same_predicates(self):
        world, people = fixture()
        person = people["主役"]
        person.goal.target, person.goal.deliver_to = "衣装", "体育館"
        goal = resolve_goal(None, world, people)
        self.assertTrue(goal.legacy)
        self.assertEqual([c.kind for c in goal.conditions], ["holds", "at"])
        self.assertFalse(goal.satisfied(people))
        person.add_item("衣装", 1)
        self.assertFalse(goal.satisfied(people))
        person.zone = "体育館"
        self.assertTrue(goal.satisfied(people))

    def test_actor_can_be_another_person(self):
        world, people = fixture()
        raw = {"actor": "実行委員", "all": [{"predicate": "at", "zone": "体育館"}], "time": {"mode": "any"}}
        result = search(world, people, resolve_goal(raw, world, people))
        self.assertEqual(result["cost"], 0)
        self.assertEqual(result["steps"], [])

    def test_invalid_conditions_are_rejected(self):
        world, people = fixture()
        cases = [{"all": []}, {"all": [{"predicate": "wish", "zone": "体育館"}]},
                 {"all": [{"predicate": [], "zone": "体育館"}]},
                 {"all": [{"predicate": "holds", "item": "衣装", "quantity": 0}]},
                 {"all": [{"predicate": "holds", "item": "衣装", "quantity": True}]},
                 {"all": [{"predicate": "at", "zone": "不存在"}]},
                 {"all": [{"predicate": "at", "zone": "体育館", "subject": "不存在"}]},
                 raw_goal(slot="15:15"), raw_goal(mode="before"), raw_goal(actor="不存在"),
                 {**raw_goal(), "unknown": 1}]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                resolve_goal(raw, world, people)


class SearchTests(unittest.TestCase):
    def run_search(self, raw=None, mutate=None, options=None):
        world, people = fixture()
        if mutate:
            mutate(world, people)
        return search(world, people, resolve_goal(raw or raw_goal(), world, people), options)

    def test_early_arrival_waits_until_exact_judgment(self):
        result = self.run_search()
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["cost"], 18)
        self.assertEqual(result["elapsed_minutes"], 540)
        self.assertEqual(result["reached"]["slot"], "15:00")
        self.assertEqual([s["kind"] for s in result["steps"][:3]], ["move", "investigate", "move"])
        self.assertTrue(all(s["kind"] == "wait" for s in result["steps"][3:]))
        self.assertTrue(result["shortest_guaranteed"])
        self.assertEqual(result["optimality_scope"], "elapsed_slots_in_model")

    def test_deadline_and_untimed_goals_stop_on_first_arrival(self):
        for mode in ("by", "any"):
            result = self.run_search(raw_goal(mode))
            self.assertEqual(result["cost"], 3)
            self.assertEqual(result["reached"]["slot"], "07:30")

    def test_insufficient_deadline_is_model_unreachable(self):
        result = self.run_search(raw_goal("by", "07:00"))
        self.assertEqual(result["status"], "unreachable")
        self.assertIsNone(result["cost"])
        self.assertIn("全世界", result["message"])

    def test_cutoff_does_not_claim_unreachable(self):
        result = self.run_search(options={"max_expansions": 1})
        self.assertEqual(result["status"], "cutoff")
        self.assertFalse(result["shortest_guaranteed"])

    def test_goal_check_precedes_expansion_budget_at_start(self):
        def change(world, people):
            people["主役"].zone = "体育館"
            people["主役"].add_item("衣装", 1)
        result = self.run_search(raw_goal(slot="06:00"), change, {"max_expansions": 1})
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["cost"], 0)
        self.assertEqual(result["expansions"], 0)

    def test_end_boundary_is_checked_even_with_no_remaining_actions(self):
        result = self.run_search(raw_goal(slot="17:00"))
        self.assertEqual(result["cost"], 22)
        self.assertEqual(result["reached"]["slot"], "17:00")

    def test_event_at_judgment_invalidates_early_arrival(self):
        def change(world, people):
            world.scheduled_events = ({"id": "closure", "day": 1, "slot": "15:00", "targets": ["主役"], "move_to": "教室"},)
        self.assertEqual(self.run_search(mutate=change)["status"], "unreachable")

    def test_scheduled_item_and_non_actor_location_conditions(self):
        def change(world, people):
            people["主役"].verbs.discard("investigate")
            world.scheduled_events = ({"id": "grant", "day": 1, "slot": "15:00", "targets": ["主役"], "grants_item": {"name": "衣装"}},)
        raw = raw_goal()
        raw["all"].append({"predicate": "at", "subject": "実行委員", "zone": "体育館"})
        self.assertEqual(self.run_search(raw, change)["status"], "found")

    def test_force_action_is_unsupported_not_ignored(self):
        def change(world, people):
            world.scheduled_events = ({"id": "force", "day": 1, "slot": "15:00", "targets": ["主役"], "force_action": {"verb": "move", "args": ["教室"]}},)
        with self.assertRaises(UnsupportedModel):
            self.run_search(mutate=change)

    def test_required_item_gates_range_and_verbs_are_respected(self):
        for change in (lambda w,p: p["主役"].verbs.discard("move"),
                       lambda w,p: p["主役"].range_zones.discard("体育館"),
                       lambda w,p: p["主役"].range_exclude.append({"zones": ["体育館"], "until_item": "衣装"}) or p["主役"].verbs.discard("investigate")):
            self.assertEqual(self.run_search(raw_goal("any"), change)["status"], "unreachable")

    def test_quantity_progress_and_crafting(self):
        world, people = fixture()
        world.items["布"] = {"name": "布", "sources": [{"type": "investigate", "zone": "衣装室", "count": 2, "max": 2}]}
        world.items["衣装"] = {"name": "衣装", "craft_zone": "衣装室"}
        world.recipes["衣装"] = {"布": 2}
        result = search(world, people, resolve_goal(raw_goal("any"), world, people))
        self.assertEqual(result["cost"], 7)  # move + 4 gather turns + craft + move
        self.assertEqual(sum(s["kind"] == "investigate" for s in result["steps"]), 4)

    def test_quantity_shortfall_is_not_treated_as_satisfied(self):
        raw = raw_goal("any")
        raw["all"][1]["quantity"] = 2
        self.assertEqual(self.run_search(raw)["status"], "unreachable")

    def test_required_item_edge_opens_only_after_acquisition(self):
        def change(world, people):
            world.routes["教室"] = (WorldRoute("教室", "体育館", requires_item="衣装"), WorldRoute("教室", "衣装室"))
            world.routes["衣装室"] = (WorldRoute("衣装室", "体育館", requires_item="衣装"),)
        result = self.run_search(raw_goal("any"), change)
        self.assertEqual(result["cost"], 3)
        self.assertEqual([s["kind"] for s in result["steps"]], ["move", "investigate", "move"])

    def test_multiday_calendar_is_explicitly_unsupported(self):
        with self.assertRaises(UnsupportedModel):
            self.run_search(mutate=lambda world, people: setattr(world, "days", 2))

    def test_knows_progress_matches_engine(self):
        world, people = fixture()
        world.facts["開演場所"] = {"sources": [{"type": "investigate", "zone": "教室", "count": 2}]}
        raw = {"all": [{"predicate": "knows", "fact": "開演場所"}]}
        result = search(world, people, resolve_goal(raw, world, people))
        self.assertEqual(result["cost"], 2)
        engine = VerbEngine(world, random.Random(0))
        person = people["主役"]
        for turn in range(2):
            engine.execute(person, Action("investigate", (person.zone,)), day=1, turn=turn)
        self.assertIn("開演場所", person.knowledge)

    def test_read_only_and_repeatable(self):
        world, people = fixture()
        before = deepcopy(people)
        goal = resolve_goal(raw_goal(), world, people)
        first = search(world, people, goal)
        self.assertEqual(people, before)
        self.assertEqual(first, search(world, people, goal))

    def test_generic_survey_is_byte_identical_across_hash_seeds(self):
        script = f"from gapengine.route_paths import survey; import json; print(json.dumps(survey({str(SAMPLE)!r}, {str(SAMPLE)!r}), sort_keys=True, ensure_ascii=False))"
        outputs = [subprocess.check_output([sys.executable, "-c", script], cwd=ROOT,
                   env={**os.environ, "PYTHONHASHSEED": seed}) for seed in ("1", "2")]
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(json.loads(outputs[0])["status"], "found")

    def test_shortest_route_uses_bypass_and_counts_waits(self):
        world, people = fixture()
        people["主役"].add_item("衣装", 1)
        result = search(world, people, resolve_goal(raw_goal("any"), world, people))
        self.assertEqual(result["cost"], 1)
        # A lower bound of one slot is tight: the start fails the goal, the
        # next boundary satisfies it. Exact-time cost is fixed by the clock.
        result = search(world, people, resolve_goal(raw_goal(), world, people))
        self.assertEqual(result["cost"], 18)


class SurveyTests(unittest.TestCase):
    def test_already_achieved_legacy_delivery_has_zero_cost(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            shutil.copytree(SAMPLE, project, dirs_exist_ok=True)
            (project / "route.yaml").write_text("{}\n")
            path = project / "subjects/01_lead.yaml"
            person = yaml.safe_load(path.read_text())
            person.update(goal={"target": "衣装", "deliver_to": "体育館"}, inventory={"衣装": 1})
            person["range"]["entry"] = "体育館"
            path.write_text(yaml.safe_dump(person, allow_unicode=True))
            result = survey(project, SAMPLE)
            route = result["timepoints"][0]["routes"][0]
            self.assertEqual(result["status"], "ok")
            self.assertEqual(route["cost"], 0)
            self.assertEqual(route["steps"], [])
            self.assertFalse(route["truncated"])
            self.assertNotIn("打切り", route["label"])

    def test_project_route_works_without_template_route_and_preserves_files(self):
        before = {f: f.read_bytes() for f in SAMPLE.rglob("*.yaml")}
        result = survey(SAMPLE, SAMPLE)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["timepoints"][0]["routes"][0]["timing"], "model")
        self.assertEqual(before, {f: f.read_bytes() for f in SAMPLE.rglob("*.yaml")})

    def test_invalid_config_has_a_structured_result(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            shutil.copytree(SAMPLE, project, dirs_exist_ok=True)
            for raw in ([], ["bad"], {"goal": {"all": []}}):
                (project / "route.yaml").write_text(yaml.safe_dump(raw))
                result = survey(project, SAMPLE)
                self.assertEqual(result["status"], "invalid_config" if raw else "no_goal")

    def test_legacy_labels_never_promise_shortest(self):
        result = survey(ROOT / "projects/momotaro_plus2", ROOT / "templates/momotaro_plus2")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["shortest_guaranteed"])
        for route in result["timepoints"][0]["routes"]:
            self.assertFalse(route["shortest_guaranteed"])
            self.assertIn("最短保証なし", route["result_label"])
