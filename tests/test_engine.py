"""D1 acceptance tests 1–5 from implementation plan §5 and §6."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from engine.actions import Action, candidates
from engine.sim import Simulation
from engine.subject import Modifier, Subject
from engine.verbs import VerbEngine
from engine.vitality import down, tick
from engine.world import World


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "momotaro"
WORLD_PATH = PROJECT / "world.yaml"
SUBJECTS_DIR = PROJECT / "subjects"


class FixedRandom(random.Random):
    """Return a fixed sequence for direct contest tests."""

    def __init__(self, values: list[float]) -> None:
        super().__init__(0)
        self.values = iter(values)

    def random(self) -> float:
        return next(self.values)


def load_fixture() -> tuple[World, dict[str, Subject]]:
    world = World.from_yaml(WORLD_PATH)
    subjects = {
        subject.id: subject
        for subject in (
            Subject.from_yaml(path)
            for path in sorted(SUBJECTS_DIR.glob("*.yaml"))
        )
    }
    world.bind_subjects(subjects)
    return world, subjects


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


class EngineTests(unittest.TestCase):
    def test_observe_then_neutralize_transfers_item_modifier(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        simulation_state = SimpleNamespace(day=4, turn=1)

        before_observe = candidates(
            momotaro,
            world,
            simulation_state,
        )
        self.assertEqual(
            [
                action
                for action, _ in before_observe
                if action.verb == "neutralize"
            ],
            [],
        )

        engine = VerbEngine(world, random.Random(1))
        observe = Action("observe", ("鬼",))
        engine.execute(
            momotaro,
            observe,
            turn=1,
            day=4,
        )
        result, details, _ = engine.execute(
            momotaro,
            observe,
            turn=2,
            day=4,
        )
        self.assertEqual(result, "observed")
        self.assertEqual(details["revealed"], ["金棒"])
        self.assertIn(
            "金棒",
            momotaro.beliefs_about["鬼"].known_modifiers,
        )
        self.assertNotIn(
            "item:金棒",
            momotaro.beliefs_about["鬼"].known_modifiers,
        )

        after_observe = candidates(
            momotaro,
            world,
            SimpleNamespace(day=4, turn=2),
        )
        neutralize_actions = [
            action
            for action, _ in after_observe
            if action.verb == "neutralize"
            and action.args == ("鬼", "金棒")
        ]
        self.assertEqual(len(neutralize_actions), 1)
        self.assertEqual(
            neutralize_actions[0].meta,
            {
                "target": "鬼",
                "source": "金棒",
                "stance_sign": -1,
                "risk": "risky",
            },
        )

        result, details, markers = engine.execute(
            momotaro,
            neutralize_actions[0],
            turn=2,
            day=4,
        )
        self.assertEqual(result, "neutralized")
        self.assertEqual(
            details["neutralized"],
            {
                "target": "鬼",
                "source": "金棒",
                "transferred": True,
            },
        )
        self.assertEqual(markers, [])
        self.assertFalse(oni.has_item("金棒"))
        self.assertTrue(momotaro.has_item("金棒"))

        oni_modifiers = [
            modifier
            for modifier in oni.all_modifiers(
                world,
                world.present_subjects("鬼ヶ島"),
            )
            if modifier.source == "金棒"
        ]
        self.assertEqual(len(oni_modifiers), 1)
        self.assertFalse(oni_modifiers[0].active)

        momotaro_modifiers = [
            modifier
            for modifier in momotaro.all_modifiers(
                world,
                world.present_subjects("鬼ヶ島"),
            )
            if modifier.source == "金棒"
        ]
        self.assertEqual(len(momotaro_modifiers), 1)
        self.assertTrue(momotaro_modifiers[0].active)
        self.assertEqual(
            world.relations.stance("鬼", "桃太郎"),
            -0.7,
        )
        self.assertEqual(
            world.relations.awareness("鬼", "桃太郎"),
            0.9,
        )

    def test_same_seed_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            world_a, subjects_a = load_fixture()
            first = Simulation(
                153,
                world_a,
                subjects_a,
                root / "first",
            ).run()

            world_b, subjects_b = load_fixture()
            second = Simulation(
                153,
                world_b,
                subjects_b,
                root / "second",
            ).run()

            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_vitality_down_revive_ally_speedup_and_lethal(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        momotaro.base = 200
        oni.base = 1
        oni.remove_item("金棒", 1)
        world.vitality["lethal_exempt"].add("鬼")

        engine = VerbEngine(world, FixedRandom([0.0]))
        result, _, markers = engine.execute(
            momotaro,
            Action("fight", ("鬼",)),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "won")
        self.assertEqual(oni.vitality, "downed")
        self.assertIn("downed", [marker["verb"] for marker in markers])

        self.assertEqual(
            tick(
                oni,
                world,
                turn=4,
                present=world.present_subjects("鬼ヶ島"),
            ),
            [],
        )
        revived = tick(
            oni,
            world,
            turn=5,
            present=world.present_subjects("鬼ヶ島"),
        )
        self.assertEqual(oni.vitality, "revived")
        self.assertEqual([marker["verb"] for marker in revived], ["revived"])

        ally_world, ally_subjects = load_fixture()
        dog = ally_subjects["犬"]
        ally = ally_subjects["桃太郎"]
        dog.zone = "道中"
        ally.zone = "道中"
        ally_world.relations.change(
            ally.id,
            dog.id,
            affinity=0.9,
        )
        down(dog, ally_world, turn=1)

        self.assertEqual(
            tick(
                dog,
                ally_world,
                turn=3,
                present=ally_world.present_subjects("道中"),
            ),
            [],
        )
        tick(
            dog,
            ally_world,
            turn=4,
            present=ally_world.present_subjects("道中"),
        )
        self.assertEqual(dog.vitality, "revived")

        lethal_world, lethal_subjects = load_fixture()
        lethal_actor = lethal_subjects["桃太郎"]
        lethal_target = lethal_subjects["鬼"]
        lethal_actor.zone = "鬼ヶ島"
        lethal_actor.base = 200
        lethal_target.base = 1
        lethal_target.remove_item("金棒", 1)
        lethal_actor.modifiers.append(
            Modifier(
                id="test:lethal",
                source="test",
                value=0,
                kind="test",
                lethal=True,
                lethal_chance=1.0,
            )
        )
        lethal_engine = VerbEngine(
            lethal_world,
            FixedRandom([0.0, 0.0]),
        )
        lethal_engine.execute(
            lethal_actor,
            Action("fight", ("鬼",)),
            turn=1,
            day=1,
        )
        self.assertEqual(lethal_target.vitality, "dead")
        self.assertEqual(lethal_target.zone, "鬼ヶ島")

        exempt_world, exempt_subjects = load_fixture()
        exempt_actor = exempt_subjects["桃太郎"]
        exempt_target = exempt_subjects["鬼"]
        exempt_actor.zone = "鬼ヶ島"
        exempt_actor.base = 200
        exempt_target.base = 1
        exempt_target.remove_item("金棒", 1)
        exempt_actor.modifiers.append(
            Modifier(
                id="test:lethal",
                source="test",
                value=0,
                kind="test",
                lethal=True,
                lethal_chance=1.0,
            )
        )
        exempt_world.vitality["lethal_exempt"].add("鬼")
        exempt_engine = VerbEngine(
            exempt_world,
            FixedRandom([0.0]),
        )
        exempt_engine.execute(
            exempt_actor,
            Action("fight", ("鬼",)),
            turn=1,
            day=1,
        )
        self.assertEqual(exempt_target.vitality, "downed")

    def test_ending_is_last_row_on_arrival_slot(self) -> None:
        world, subjects = load_fixture()
        world.days = 1
        world.slots = ("朝",)
        world.daily_events = ()
        world.daily_event_chance = 0.0
        world.scheduled_events = (
            {
                "id": "test_homecoming",
                "day": 1,
                "slot": "朝",
                "targets": ["桃太郎"],
                "label": "桃太郎が村へ着いた",
                "move_to": "村",
            },
        )

        momotaro = subjects["桃太郎"]
        momotaro.zone = "道中"
        momotaro.verbs = {"rest"}
        subjects["鬼"].remove_item("鬼ヶ島の宝物", 1)
        momotaro.add_item("鬼ヶ島の宝物", 1)
        for subject_id, subject in subjects.items():
            if subject_id != "桃太郎":
                subject.vitality = "dead"

        with tempfile.TemporaryDirectory() as temporary:
            path = Simulation(
                1,
                world,
                subjects,
                Path(temporary),
            ).run()
            rows = read_rows(path)

        ending_indices = [
            index
            for index, row in enumerate(rows)
            if row["kind"] == "event"
            and row["verb"] == "ending"
            and row["id"] == "homecoming"
        ]
        self.assertEqual(ending_indices, [len(rows) - 1])
        self.assertEqual(rows[-1]["turn"], 1)
        self.assertEqual(rows[-1]["day"], 1)
        self.assertEqual(rows[-1]["slot"], "朝")

    def test_requires_item_and_recipe_prerequisites(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "海"
        momotaro.inventory = {"木材": 1, "縄": 1}
        simulation_state = SimpleNamespace(day=4, turn=1)

        without_ship = candidates(
            momotaro,
            world,
            simulation_state,
        )
        island_moves = [
            action
            for action, _ in without_ship
            if action.verb == "move"
            and action.meta.get("dest") == "鬼ヶ島"
        ]
        craft_actions = [
            action
            for action, _ in without_ship
            if action.verb == "craft"
            and action.args == ("船",)
        ]
        self.assertEqual(island_moves, [])
        self.assertEqual(craft_actions, [])

        momotaro.add_item("木材", 1)
        with_materials = candidates(
            momotaro,
            world,
            simulation_state,
        )
        craft_actions = [
            action
            for action, _ in with_materials
            if action.verb == "craft"
            and action.args == ("船",)
        ]
        self.assertEqual(len(craft_actions), 1)

        engine = VerbEngine(world, random.Random(1))
        result, _, _ = engine.execute(
            momotaro,
            craft_actions[0],
            turn=1,
            day=4,
        )
        self.assertEqual(result, "crafted")
        self.assertTrue(momotaro.has_item("船"))

        with_ship = candidates(
            momotaro,
            world,
            simulation_state,
        )
        island_moves = [
            action
            for action, _ in with_ship
            if action.verb == "move"
            and action.meta.get("dest") == "鬼ヶ島"
        ]
        self.assertEqual(len(island_moves), 1)

    def test_layers_jsonl_contract_and_vector(self) -> None:
        world, subjects = load_fixture()
        world.days = 1
        world.slots = ("朝",)
        world.daily_events = ()
        world.daily_event_chance = 0.0
        world.scheduled_events = ()

        protagonist = subjects["桃太郎"]
        protagonist.verbs = {"rest"}
        for subject_id, subject in subjects.items():
            if subject_id != protagonist.id:
                subject.vitality = "dead"

        with tempfile.TemporaryDirectory() as temporary:
            path = Simulation(
                7,
                world,
                subjects,
                Path(temporary),
            ).run()
            rows = read_rows(path)

        self.assertTrue(rows)
        self.assertEqual(rows[0]["kind"], "header")
        self.assertTrue(all("kind" in row for row in rows))

        decisions = [
            row for row in rows if row["kind"] == "decision"
        ]
        self.assertTrue(decisions)
        for row in decisions:
            self.assertIn("effective", row)
            self.assertIn("delta", row)
            self.assertIn("policy", row)
            self.assertIn("classification", row)

        snapshots = [
            row for row in rows if row["kind"] == "snapshot"
        ]
        self.assertEqual(len(snapshots), 1)
        vector = snapshots[0]["vector"]
        self.assertEqual(len(vector), 11)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in vector))


if __name__ == "__main__":
    unittest.main()
