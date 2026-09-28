"""Phase B (labor/buy economy) regression tests for momotaro_plus3, added
per Opus review of the Phase B implementation: the review found the engine
change itself correct but noted there were no tests that would actually
fail if labor/buy/ineffective_for regressed later."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.actions import Action, candidates
from engine.log import LayersWriter
from engine.sim import Simulation
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


class DriftTests(unittest.TestCase):
    """Phase C: companionship.drift (犬/猿, per_slot: -0.03) rides along on
    Simulation._record_encounters's existing per-slot co-presence scan."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()

    def _run_encounters(self) -> None:
        with tempfile.TemporaryDirectory() as out_dir:
            sim = Simulation(1, self.world, self.subjects, Path(out_dir))
            sim.day = 1
            sim.turn = 1
            sim.slot = "朝"
            with LayersWriter(Path(out_dir) / "layers.jsonl") as writer:
                sim._record_encounters(writer)

    def test_drift_applies_both_directions_when_sharing_a_zone_for_one_slot(
        self,
    ) -> None:
        self.subjects["犬"].zone = "道中"
        self.subjects["猿"].zone = "道中"
        before_forward = self.world.relations.stance("犬", "猿")
        before_backward = self.world.relations.stance("猿", "犬")
        self._run_encounters()
        after_forward = self.world.relations.stance("犬", "猿")
        after_backward = self.world.relations.stance("猿", "犬")
        self.assertAlmostEqual(after_forward - before_forward, -0.03)
        self.assertAlmostEqual(after_backward - before_backward, -0.03)

    def test_drift_does_not_apply_across_different_zones(self) -> None:
        self.subjects["犬"].zone = "道中"
        self.subjects["猿"].zone = "森"
        before = self.world.relations.stance("犬", "猿")
        self._run_encounters()
        after = self.world.relations.stance("犬", "猿")
        self.assertEqual(after, before)


class InfightDesertionTests(unittest.TestCase):
    """Phase C: companionship.infight_desertion (-0.5). A shared leader
    (stance >= threshold from both combatants, co-located) sees the fight's
    loser desert once their affinity toward the leader drops back below the
    companionship threshold."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.dog = self.subjects["犬"]
        self.monkey = self.subjects["猿"]
        self.momotaro = self.subjects["桃太郎"]
        for subject in (self.dog, self.monkey, self.momotaro):
            subject.zone = "道中"
        self.world.relations.change("犬", "桃太郎", affinity=1.0)
        self.world.relations.change("猿", "桃太郎", affinity=1.0)

    def test_loser_deserts_the_shared_leader_and_emits_ally_lost(self) -> None:
        engine = VerbEngine(self.world, random.Random(1))
        _result, details, markers = engine.execute(
            self.dog, Action("fight", ("猿",)), turn=1, day=1
        )
        loser_id = details["loser"]
        stance_after = self.world.relations.stance(loser_id, "桃太郎")
        self.assertLess(stance_after, self.world.companionship["threshold"])
        ally_lost = [marker for marker in markers if marker["verb"] == "ally_lost"]
        self.assertEqual(len(ally_lost), 1)
        self.assertEqual(ally_lost[0]["subject"], loser_id)
        self.assertEqual(ally_lost[0]["details"]["ally"], "桃太郎")

    def test_no_ally_lost_without_a_co_located_shared_leader(self) -> None:
        self.momotaro.zone = "村"
        engine = VerbEngine(self.world, random.Random(1))
        _result, _details, markers = engine.execute(
            self.dog, Action("fight", ("猿",)), turn=1, day=1
        )
        self.assertFalse(any(marker["verb"] == "ally_lost" for marker in markers))

    def test_a_non_protagonist_bystander_never_counts_as_leader(self) -> None:
        # Opus review S1: scoring *any* mutually-liked bystander as a leader
        # fired for unrelated pairs (e.g. おじいさん losing おばあさん over
        # a fight between two other subjects). Only world.protagonist may
        # be the leader now.
        self.momotaro.zone = "村"
        yojinbo = self.subjects["用心棒"]
        yojinbo.zone = "道中"
        self.world.relations.change("犬", "用心棒", affinity=1.0)
        self.world.relations.change("猿", "用心棒", affinity=1.0)
        engine = VerbEngine(self.world, random.Random(1))
        _result, _details, markers = engine.execute(
            self.dog, Action("fight", ("猿",)), turn=1, day=1
        )
        self.assertFalse(any(marker["verb"] == "ally_lost" for marker in markers))

    def test_no_ally_lost_when_the_winner_does_not_follow_the_leader(self) -> None:
        # Fixed for seed 1: 犬 wins, 猿 loses. Desertion requires *both*
        # combatants to already be above threshold toward the leader.
        self.world.relations.change("犬", "桃太郎", affinity=-1.0)
        self.assertLess(
            self.world.relations.stance("犬", "桃太郎"),
            self.world.companionship["threshold"],
        )
        engine = VerbEngine(self.world, random.Random(1))
        _result, _details, markers = engine.execute(
            self.dog, Action("fight", ("猿",)), turn=1, day=1
        )
        self.assertFalse(any(marker["verb"] == "ally_lost" for marker in markers))

    def test_fight_consumes_the_same_random_rolls_with_or_without_desertion(
        self,
    ) -> None:
        with_desertion = VerbEngine(self.world, random.Random(1)).execute(
            self.dog, Action("fight", ("猿",)), turn=1, day=1
        )[1]
        world2, subjects2 = load_fixture()
        world2.companionship["infight_desertion"] = None
        dog2, monkey2, momotaro2 = subjects2["犬"], subjects2["猿"], subjects2["桃太郎"]
        for subject in (dog2, monkey2, momotaro2):
            subject.zone = "道中"
        world2.relations.change("犬", "桃太郎", affinity=1.0)
        world2.relations.change("猿", "桃太郎", affinity=1.0)
        without_desertion = VerbEngine(world2, random.Random(1)).execute(
            dog2, Action("fight", ("猿",)), turn=1, day=1
        )[1]
        self.assertEqual(with_desertion["roll"], without_desertion["roll"])

    def test_kibidango_can_restore_a_deserted_companion(self) -> None:
        # User-confirmed decision: desertion is not permanent -- giving
        # きびだんご again should cross the threshold back and re-fire
        # ally_gained (proving the pair was dropped from the one-shot
        # _ally_gained set rather than staying silenced forever).
        engine = VerbEngine(self.world, random.Random(1))
        _result, details, markers = engine.execute(
            self.dog, Action("fight", ("猿",)), turn=1, day=1
        )
        loser_id = details["loser"]
        self.assertTrue(any(marker["verb"] == "ally_lost" for marker in markers))
        loser = self.subjects[loser_id]
        loser.vitality = "revived"  # skip the multi-slot revive wait for this test
        _result, _details, regain_markers = engine.execute(
            self.momotaro,
            Action(
                "give_item",
                (loser_id, "きびだんご"),
                {"target": loser_id, "item": "きびだんご"},
            ),
            turn=2,
            day=1,
        )
        self.assertGreaterEqual(
            self.world.relations.stance(loser_id, "桃太郎"),
            self.world.companionship["threshold"],
        )
        self.assertTrue(
            any(marker["verb"] == "ally_gained" for marker in regain_markers)
        )


class SharedAffinityTests(unittest.TestCase):
    """Phase C: きびだんご's give.shared_affinity (0.15). A bystander who
    already follows the giver (stance >= threshold) also warms up to the
    receiver."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.dog = self.subjects["犬"]
        self.monkey = self.subjects["猿"]
        for subject in (self.momotaro, self.dog, self.monkey):
            subject.zone = "道中"
        self.momotaro.add_item("きびだんご", 1)
        self.world.relations.change("猿", "桃太郎", affinity=0.6)

    def test_give_also_warms_a_present_ally_toward_the_receiver(self) -> None:
        before_forward = self.world.relations.stance("猿", "犬")
        before_backward = self.world.relations.stance("犬", "猿")
        engine = VerbEngine(self.world, random.Random(1))
        result, _details, _markers = engine.execute(
            self.momotaro,
            Action(
                "give_item",
                ("犬", "きびだんご"),
                {"target": "犬", "item": "きびだんご"},
            ),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "given")
        after_forward = self.world.relations.stance("猿", "犬")
        after_backward = self.world.relations.stance("犬", "猿")
        self.assertAlmostEqual(after_forward - before_forward, 0.15)
        self.assertAlmostEqual(after_backward - before_backward, 0.15)


class DrillBondTests(unittest.TestCase):
    """Phase C: companionship.drill_bond (0.05). Training bonds the actor's
    present companions to each other (not the actor itself)."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.dog = self.subjects["犬"]
        self.monkey = self.subjects["猿"]
        for subject in (self.momotaro, self.dog, self.monkey):
            subject.zone = "道中"
        self.world.relations.change("犬", "桃太郎", affinity=1.0)
        self.world.relations.change("猿", "桃太郎", affinity=1.0)

    def test_train_bonds_present_companions_to_each_other(self) -> None:
        before_forward = self.world.relations.stance("犬", "猿")
        before_backward = self.world.relations.stance("猿", "犬")
        engine = VerbEngine(self.world, random.Random(1))
        result, _details, _markers = engine.execute(
            self.momotaro, Action("train"), turn=1, day=1
        )
        self.assertEqual(result, "trained")
        after_forward = self.world.relations.stance("犬", "猿")
        after_backward = self.world.relations.stance("猿", "犬")
        self.assertAlmostEqual(after_forward - before_forward, 0.05)
        self.assertAlmostEqual(after_backward - before_backward, 0.05)


if __name__ == "__main__":
    unittest.main()
