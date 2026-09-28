"""Phase B (labor/buy economy) regression tests for momotaro_plus3, added
per Opus review of the Phase B implementation: the review found the engine
change itself correct but noted there were no tests that would actually
fail if labor/buy/ineffective_for regressed later."""

from __future__ import annotations

import random
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.actions import Action, candidates
from engine.subject import Subject
from engine.verbs import VerbEngine
from engine.world import World

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "momotaro_plus3"
TEMPLATE = ROOT / "templates" / "momotaro_plus3"


def load_fixture() -> tuple[World, dict[str, Subject]]:
    world = World.from_yaml(
        PROJECT / "world.yaml",
        action_graph_path=TEMPLATE / "action_graph.yaml",
    )
    subjects = [
        Subject.from_yaml(path)
        for path in sorted((PROJECT / "subjects").glob("*.yaml"), key=lambda v: v.name)
    ]
    values = {subject.id: subject for subject in subjects}
    world.bind_subjects(values)
    return world, values


class WorldLoadTests(unittest.TestCase):
    def test_world_and_subjects_load(self) -> None:
        world, subjects = load_fixture()
        self.assertEqual(world.name, "桃太郎＋3")
        self.assertIn("街", world.zones)
        self.assertIn("用心棒", subjects)

    def test_koban_is_labor_only_no_road_shortcut(self) -> None:
        world, _subjects = load_fixture()
        sources = world.items["小判"]["sources"]
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["type"], "labor")
        self.assertEqual(sources[0]["zone"], "街")

    def test_priced_items_are_not_registered_as_recipes(self) -> None:
        world, _subjects = load_fixture()
        self.assertNotIn("鉄砲", world.recipes)
        self.assertNotIn("きびだんご", world.recipes)


class LaborTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.momotaro.zone = "街"

    def test_labor_costs_stamina_and_respects_count_and_max(self) -> None:
        engine = VerbEngine(self.world, random.Random(1))
        start_stamina = self.momotaro.stamina
        gained = 0
        for _ in range(2):
            result, details, _markers = engine.execute(
                self.momotaro, Action("labor"), turn=1, day=1
            )
            self.assertEqual(result, "labored")
            gained += sum(1 for item in details["gathered"] if item["item"] == "小判")
        # count=2 per source: two labor actions -> exactly one 小判.
        self.assertEqual(gained, 1)
        self.assertEqual(start_stamina - self.momotaro.stamina, 2 * 3.0)

    def test_labor_stops_once_max_is_reached(self) -> None:
        engine = VerbEngine(self.world, random.Random(1))
        for _ in range(16):  # far more than needed to hit max=8
            engine.execute(self.momotaro, Action("labor"), turn=1, day=1)
        self.assertEqual(self.momotaro.inventory.get("小判", 0), 8)
        result, details, _markers = engine.execute(
            self.momotaro, Action("labor"), turn=1, day=1
        )
        self.assertEqual(result, "invalid")
        self.assertEqual(details["reason"], "no_labor_here")

    def test_labor_candidate_absent_outside_the_labor_zone(self) -> None:
        self.momotaro.zone = "道中"
        weighted = candidates(self.momotaro, self.world, SimpleNamespace(day=1, turn=1))
        self.assertFalse(any(action.verb == "labor" for action, _weight in weighted))


class BuyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.momotaro.zone = "街"

    def test_buy_candidate_requires_full_price(self) -> None:
        weighted = candidates(self.momotaro, self.world, SimpleNamespace(day=1, turn=1))
        self.assertFalse(
            any(action.verb == "buy" and action.args == ("鉄砲",) for action, _w in weighted)
        )
        self.momotaro.add_item("小判", 3)
        weighted = candidates(self.momotaro, self.world, SimpleNamespace(day=1, turn=1))
        self.assertTrue(
            any(action.verb == "buy" and action.args == ("鉄砲",) for action, _w in weighted)
        )

    def test_buy_spends_currency_and_grants_item(self) -> None:
        self.momotaro.add_item("小判", 3)
        engine = VerbEngine(self.world, random.Random(1))
        result, details, _markers = engine.execute(
            self.momotaro, Action("buy", ("鉄砲",), {"item": "鉄砲"}), turn=1, day=1
        )
        self.assertEqual(result, "bought")
        self.assertEqual(self.momotaro.inventory.get("小判", 0), 0)
        self.assertEqual(self.momotaro.inventory.get("鉄砲", 0), 1)

    def test_buy_fails_with_insufficient_funds(self) -> None:
        self.momotaro.add_item("小判", 2)
        engine = VerbEngine(self.world, random.Random(1))
        result, details, _markers = engine.execute(
            self.momotaro, Action("buy", ("鉄砲",), {"item": "鉄砲"}), turn=1, day=1
        )
        self.assertEqual(result, "invalid")
        self.assertEqual(details["reason"], "insufficient_funds")
        self.assertEqual(self.momotaro.inventory.get("小判", 0), 2)

    def test_buy_fails_outside_the_shop_zone(self) -> None:
        self.momotaro.zone = "道中"
        self.momotaro.add_item("小判", 3)
        engine = VerbEngine(self.world, random.Random(1))
        result, details, _markers = engine.execute(
            self.momotaro, Action("buy", ("鉄砲",), {"item": "鉄砲"}), turn=1, day=1
        )
        self.assertEqual(result, "invalid")
        self.assertEqual(details["reason"], "wrong_zone")
        self.assertEqual(self.momotaro.inventory.get("小判", 0), 3)


class IneffectiveForTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.momotaro.zone = "街"
        self.momotaro.add_item("小判", 1)

    def test_koban_is_rejected_by_an_animal_companion_without_being_consumed(self) -> None:
        dog = self.subjects["犬"]
        dog.zone = "街"
        self.assertIn("animal", dog.tags)
        engine = VerbEngine(self.world, random.Random(1))
        result, details, _markers = engine.execute(
            self.momotaro,
            Action("give_item", ("犬", "小判"), {"target": "犬", "item": "小判"}),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "invalid")
        self.assertEqual(details["reason"], "ineffective_receiver")
        self.assertEqual(self.momotaro.inventory.get("小判", 0), 1)
        self.assertEqual(dog.inventory.get("小判", 0), 0)

    def test_koban_is_accepted_by_a_human_and_raises_stance(self) -> None:
        yojinbo = self.subjects["用心棒"]
        yojinbo.zone = "街"
        self.assertNotIn("animal", yojinbo.tags)
        before = self.world.relations.stance("用心棒", "桃太郎")
        engine = VerbEngine(self.world, random.Random(1))
        result, _details, _markers = engine.execute(
            self.momotaro,
            Action("give_item", ("用心棒", "小判"), {"target": "用心棒", "item": "小判"}),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "given")
        self.assertEqual(self.momotaro.inventory.get("小判", 0), 0)
        self.assertEqual(yojinbo.inventory.get("小判", 0), 1)
        after = self.world.relations.stance("用心棒", "桃太郎")
        self.assertGreater(after, before)

    def test_dog_is_excluded_from_koban_give_candidates(self) -> None:
        dog = self.subjects["犬"]
        dog.zone = "街"
        weighted = candidates(self.momotaro, self.world, SimpleNamespace(day=1, turn=1))
        self.assertFalse(
            any(
                action.verb == "give_item" and action.args == ("犬", "小判")
                for action, _weight in weighted
            )
        )


if __name__ == "__main__":
    unittest.main()
