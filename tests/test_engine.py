"""D1 acceptance tests 1–5 from implementation plan §5 and §6."""

from __future__ import annotations

import hashlib
import json
import random
import tempfile
import unittest
import yaml
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from engine.actions import Action, candidates
from engine.predicate import compile_predicate
from engine.sim import Simulation
from engine.subject import (
    Belief,
    BeliefAbout,
    Modifier,
    Subject,
)
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


def normalized_layers_hash(path: Path) -> str:
    """Hash compact JSONL while normalizing the source-derived engine hash."""

    rows = read_rows(path)
    rows[0]["engine_hash"] = "<engine-hash>"
    payload = "".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for row in rows
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class EngineTests(unittest.TestCase):
    def test_lethal_modifier_is_snapshotted_before_loot(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        momotaro.base = 200
        oni.base = 1
        world.vitality["lethal_exempt"].discard(oni.id)

        self.assertTrue(oni.has_item("金棒"))
        engine = VerbEngine(world, FixedRandom([0.0]))
        result, details, _ = engine.execute(
            momotaro,
            Action("fight", (oni.id,)),
            turn=1,
            day=1,
        )

        self.assertEqual(result, "won")
        self.assertEqual(oni.vitality, "downed")
        self.assertNotEqual(oni.vitality, "dead")
        self.assertTrue(momotaro.has_item("金棒"))
        self.assertFalse(oni.has_item("金棒"))
        self.assertIsNone(details["lethal_chance"])
        self.assertIsNone(details["lethal_roll"])

    def test_vehicle_is_not_transferred_as_fight_loot(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        momotaro.base = 200
        oni.base = 1
        oni.add_item("船", 1)

        engine = VerbEngine(world, FixedRandom([0.0]))
        result, details, _ = engine.execute(
            momotaro,
            Action("fight", (oni.id,)),
            turn=1,
            day=1,
        )

        self.assertEqual(result, "won")
        self.assertTrue(oni.has_item("船"))
        self.assertFalse(momotaro.has_item("船"))
        self.assertNotIn("船", details["loot"])

    def test_hostile_permission_reduces_give_and_share_weights(
        self,
    ) -> None:
        world, subjects = load_fixture()
        world.action_permissions["give_item"] = {
            "hostile": "restricted",
            "neutral": "allow",
            "ally": "allow",
        }
        world.action_permissions["share_knowledge"] = {
            "hostile": "restricted",
            "neutral": "allow",
            "ally": "allow",
        }
        momotaro = subjects["桃太郎"]
        dog = subjects["犬"]
        dog.zone = momotaro.zone
        simulation_state = SimpleNamespace(day=1, turn=1)

        def selected_weights(
            affinity: float,
        ) -> dict[tuple[Any, ...], float]:
            current = world.relations.stance(
                momotaro.id,
                dog.id,
            )
            world.relations.change(
                momotaro.id,
                dog.id,
                affinity=affinity - current,
            )
            return {
                (action.verb, *action.args): weight
                for action, weight in candidates(
                    momotaro,
                    world,
                    simulation_state,
                )
                if action.verb
                in {"give_item", "share_knowledge"}
                and action.meta.get("target") == dog.id
            }

        neutral = selected_weights(0.0)
        hostile = selected_weights(-0.5)
        give_permission = world.permission(
            "give_item",
            "hostile",
        )
        share_permission = world.permission(
            "share_knowledge",
            "hostile",
        )

        give_key = ("give_item", "犬", "きびだんご")
        share_key = ("share_knowledge", "犬", "雑談")
        self.assertIn(give_key, neutral)
        self.assertIn(give_key, hostile)
        self.assertIn(share_key, neutral)
        self.assertIn(share_key, hostile)
        self.assertAlmostEqual(
            hostile[give_key],
            neutral[give_key] * give_permission,
        )
        self.assertAlmostEqual(
            hostile[share_key],
            neutral[share_key] * share_permission,
        )

    def test_compile_predicate_rejects_unknown_import(self) -> None:
        with self.assertRaises(ValueError):
            compile_predicate("__import__('os')")

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
        expected_meta = {
            "target": "鬼",
            "source": "金棒",
            "stance_sign": -1,
            "risk": "risky",
        }
        self.assertTrue(
            expected_meta.items()
            <= neutralize_actions[0].meta.items()
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
            for seed in (1, 153, 999):
                world_a, subjects_a = load_fixture()
                first = Simulation(
                    seed,
                    world_a,
                    subjects_a,
                    root / f"seed-{seed}-first",
                ).run()

                world_b, subjects_b = load_fixture()
                second = Simulation(
                    seed,
                    world_b,
                    subjects_b,
                    root / f"seed-{seed}-second",
                ).run()

                self.assertEqual(
                    first.read_bytes(),
                    second.read_bytes(),
                    f"seed {seed} was not byte deterministic",
                )

                if seed == 153:
                    self.assertEqual(
                        normalized_layers_hash(first),
                        "d6c13d81b772a3f080a61c2171c911f1"
                        "eb159f4b6254ed0dfa07fee7a682e660",
                    )

    def test_phase0_opt_in_removal_restores_old_seed_hash(
        self,
    ) -> None:
        raw_world = yaml.safe_load(
            WORLD_PATH.read_text(encoding="utf-8")
        )
        raw_world.pop("gapengine", None)
        raw_world.pop("phase1", None)
        raw_world.pop("disguises", None)
        raw_world.pop("trials", None)
        raw_world.pop("phase_rules", None)

        phase1_items = {"勾玉"}
        raw_world["items"] = [
            item
            for item in raw_world.get("items", [])
            if str(item["name"]) not in phase1_items
        ]

        valued_facts = {
            str(fact["id"])
            for fact in raw_world.get("facts", [])
            if fact.get("values") is not None
        }
        raw_world["facts"] = [
            fact
            for fact in raw_world.get("facts", [])
            if str(fact["id"]) not in valued_facts
            and not any(
                isinstance(fact.get(key), dict)
                and str(fact[key].get("fact")) in valued_facts
                for key in ("implies", "refutes")
            )
        ]
        raw_world["truth"] = {
            str(fact_id): value
            for fact_id, value in (
                raw_world.get("truth", {}) or {}
            ).items()
            if str(fact_id) not in valued_facts
        }

        world = World(raw_world, WORLD_PATH)
        subjects = {
            subject.id: subject
            for subject in (
                Subject.from_yaml(path)
                for path in sorted(
                    SUBJECTS_DIR.glob("*.yaml")
                )
            )
        }
        phase1_verbs = {
            "confront",
            "mislead",
            "sabotage",
            "sacrifice",
            "negotiate",
            "concede",
            "pledge",
            "persuade",
            "plant",
            "payoff",
            "disguise",
            "grand_gesture",
            "trial",
            "donate",
        }
        for subject in subjects.values():
            subject.verbs.difference_update(phase1_verbs)
            subject.initial_relations.pop("旅の商人", None)
            for item in sorted(phase1_items):
                subject.inventory.pop(item, None)
            for fact_id in sorted(valued_facts):
                subject.beliefs.pop(fact_id, None)
        world.bind_subjects(subjects)

        with tempfile.TemporaryDirectory() as temporary:
            path = Simulation(
                153,
                world,
                subjects,
                Path(temporary),
            ).run()
            self.assertEqual(
                normalized_layers_hash(path),
                "3e95ce8034428ba63fa833f39f6e87ff"
                "daeb9a8bae42af464ee421ddeaae958e",
            )

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
        world.scheduled_events = ()

        momotaro = subjects["桃太郎"]
        momotaro.zone = "道中"
        momotaro.range_zones = {"村", "道中"}
        momotaro.verbs = {"move"}
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

        protagonist_moves = [
            row
            for row in rows
            if row["kind"] == "decision"
            and row["subject"] == "桃太郎"
            and row["verb"] == "move"
        ]
        self.assertEqual(len(protagonist_moves), 1)
        self.assertEqual(protagonist_moves[0]["args"], ["村"])

        self.assertEqual(rows[-1]["kind"], "event")
        self.assertEqual(rows[-1]["verb"], "ending")
        self.assertEqual(rows[-1]["id"], "homecoming")
        self.assertEqual(rows[-1]["turn"], 1)
        self.assertEqual(rows[-1]["day"], 1)
        self.assertEqual(rows[-1]["slot"], "朝")
        self.assertEqual(
            rows[-1]["details"]["delivered"],
            {"鬼ヶ島の宝物": "村"},
        )
        self.assertEqual(
            rows[-1]["delta"]["objective"],
            {"鬼ヶ島の宝物": "村"},
        )
        self.assertEqual(world.holder("鬼ヶ島の宝物"), "村")

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
            raw_lines = path.read_text(encoding="utf-8").splitlines()
            rows = [json.loads(line) for line in raw_lines]

        self.assertTrue(rows)
        self.assertEqual(rows[0]["kind"], "header")
        self.assertTrue(all("kind" in row for row in rows))

        for raw_line, row in zip(raw_lines, rows, strict=True):
            self.assertEqual(
                raw_line,
                json.dumps(
                    row,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )

        logged_actions = [
            row
            for row in rows
            if row["kind"] in {"event", "decision"}
        ]
        self.assertTrue(logged_actions)
        for row in logged_actions:
            self.assertEqual(
                set(row["delta"]),
                {"actor", "targets", "relations", "objective"},
            )

        decisions = [
            row for row in rows if row["kind"] == "decision"
        ]
        self.assertTrue(decisions)
        for row in decisions:
            self.assertIn("effective", row)
            self.assertIn("policy", row)
            self.assertIn("classification", row)

        snapshots = [
            row for row in rows if row["kind"] == "snapshot"
        ]
        self.assertEqual(len(snapshots), 1)
        vector = snapshots[0]["vector"]
        self.assertEqual(len(vector), 11)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in vector))

    def test_valued_fact_evidence_hearsay_and_mislead(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        dog = subjects["犬"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        dog.zone = "鬼ヶ島"

        engine = VerbEngine(world, FixedRandom([]))
        result, details, markers = engine.execute(
            momotaro,
            Action("investigate", ("鬼ヶ島",)),
            turn=1,
            day=1,
        )

        self.assertEqual(result, "investigated")
        self.assertIn("金棒の由来", details["learned"])
        self.assertEqual(
            momotaro.beliefs["oni_weakness"].value,
            "金棒",
        )
        self.assertEqual(
            momotaro.beliefs["oni_weakness"].confidence,
            0.8,
        )
        learned_markers = [
            marker
            for marker in markers
            if marker["verb"] == "learn_fact"
            and marker["details"]["fact"] == "金棒の由来"
        ]
        self.assertEqual(len(learned_markers), 1)
        self.assertEqual(
            learned_markers[0]["details"]["beliefs"][0]["outcome"],
            "adopted",
        )

        result, details, markers = engine.execute(
            momotaro,
            Action(
                "share_knowledge",
                ("犬", "oni_weakness"),
            ),
            turn=2,
            day=1,
        )
        self.assertEqual(result, "shared")
        self.assertEqual(details["value"], "金棒")
        self.assertAlmostEqual(details["confidence"], 0.46)
        self.assertEqual(dog.beliefs["oni_weakness"].value, "金棒")
        self.assertAlmostEqual(
            dog.beliefs["oni_weakness"].confidence,
            0.46,
        )
        self.assertEqual(
            [marker["verb"] for marker in markers],
            ["learn_fact"],
        )

        mislead_actions = [
            action
            for action, _ in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "mislead"
            and action.meta.get("belief_kind") == "valued_fact"
            and action.meta.get("target") == "犬"
        ]
        self.assertEqual(
            {
                action.meta["value"]
                for action in mislead_actions
            },
            {"火", "塩"},
        )

        before = dog.beliefs["oni_weakness"].confidence
        result, details, _ = engine.execute(
            momotaro,
            next(
                action
                for action in mislead_actions
                if action.meta["value"] == "火"
            ),
            turn=3,
            day=1,
        )
        self.assertIn(result, {"misled", "ignored"})
        self.assertFalse(details["accurate"])
        if result == "misled":
            self.assertLessEqual(
                dog.beliefs["oni_weakness"].confidence,
                before,
            )

        oni_belief = oni.beliefs_about["桃太郎"]
        oni_belief.base_estimate = 20.0
        current_affinity = world.relations.stance(
            "鬼",
            "桃太郎",
        )
        world.relations.change(
            "鬼",
            "桃太郎",
            affinity=0.2 - current_affinity,
        )
        result, details, _ = engine.execute(
            momotaro,
            Action(
                "mislead",
                ("鬼", "桃太郎", 30.0),
                {
                    "target": "鬼",
                    "about": "桃太郎",
                    "value": 30.0,
                    "belief_kind": "strength",
                },
            ),
            turn=4,
            day=1,
        )
        self.assertEqual(result, "misled")
        self.assertFalse(details["accurate"])

        self.assertEqual(oni_belief.misled_by, "桃太郎")

        momotaro.vitality = "downed"
        oni.verbs.add("observe")
        observe_actions = [
            action
            for action, _ in candidates(
                oni,
                world,
                SimpleNamespace(day=1, turn=5),
            )
            if action.verb == "observe"
            and action.meta.get("target") == "桃太郎"
        ]
        self.assertEqual(len(observe_actions), 1)

        oni.traits["curiosity"] = 1.0
        result, details, markers = engine.execute(
            oni,
            observe_actions[0],
            turn=5,
            day=1,
        )
        self.assertEqual(result, "observed")
        self.assertEqual(details["exposed_estimate"], 26.0)
        self.assertEqual(
            [marker["verb"] for marker in markers],
            ["exposure"],
        )
        self.assertEqual(
            markers[0]["details"]["misled_by"],
            "桃太郎",
        )
        self.assertIsNone(oni_belief.misled_by)
        self.assertEqual(
            oni_belief.base_estimate,
            momotaro.base,
        )

    def test_confront_correct_and_misjudged(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]

        momotaro.zone = "村"
        engine = VerbEngine(world, FixedRandom([]))
        for turn in (1, 2):
            result, _, _ = engine.execute(
                momotaro,
                Action("investigate", ("村",)),
                turn=turn,
                day=1,
            )
            self.assertEqual(result, "investigated")

        self.assertEqual(
            momotaro.beliefs["treasure_thief"].value,
            "鬼",
        )
        self.assertEqual(
            momotaro.beliefs["treasure_thief"].confidence,
            0.5,
        )
        oni.zone = "村"
        fixture_confronts = [
            action
            for action, _ in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "confront"
            and action.args == ("鬼", "treasure_thief")
        ]
        self.assertEqual(len(fixture_confronts), 1)

        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"

        world.facts["culprit"] = {
            "id": "culprit",
            "values": ["犬", "鬼"],
            "act_threshold": 0.6,
        }
        world.truth["culprit"] = "鬼"
        momotaro.beliefs["culprit"] = Belief(
            value="鬼",
            confidence=0.8,
        )

        confront_actions = [
            action
            for action, _ in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=1),
            )
            if action.verb == "confront"
        ]
        self.assertEqual(len(confront_actions), 1)
        self.assertEqual(confront_actions[0].args, ("鬼", "culprit"))

        engine = VerbEngine(world, FixedRandom([]))
        before = world.relations.stance("鬼", "桃太郎")
        result, details, markers = engine.execute(
            momotaro,
            confront_actions[0],
            turn=1,
            day=1,
        )
        self.assertEqual(result, "exposed")
        self.assertTrue(details["correct"])
        self.assertAlmostEqual(
            world.relations.stance("鬼", "桃太郎"),
            before - 0.4,
        )
        self.assertEqual(
            [marker["verb"] for marker in markers],
            ["exposure"],
        )

        wrong_world, wrong_subjects = load_fixture()
        wrong_actor = wrong_subjects["桃太郎"]
        wrong_target = wrong_subjects["犬"]
        wrong_actor.zone = "道中"
        wrong_target.zone = "道中"
        wrong_world.facts["culprit"] = {
            "id": "culprit",
            "values": ["犬", "鬼"],
            "act_threshold": 0.6,
        }
        wrong_world.truth["culprit"] = "鬼"
        wrong_actor.beliefs["culprit"] = Belief(
            value="犬",
            confidence=0.8,
        )

        wrong_engine = VerbEngine(
            wrong_world,
            FixedRandom([]),
        )
        result, details, markers = wrong_engine.execute(
            wrong_actor,
            Action("confront", ("犬", "culprit")),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "misjudged")
        self.assertFalse(details["correct"])
        self.assertEqual(wrong_actor.reputation, -0.1)
        self.assertEqual(
            wrong_actor.beliefs["culprit"].confidence,
            0.4,
        )
        self.assertEqual(
            [marker["verb"] for marker in markers],
            ["misjudged"],
        )

    def test_prerequisite_permission_and_open_weight_total(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        dog = subjects["犬"]
        oni = subjects["鬼"]
        momotaro.zone = "道中"
        dog.zone = "道中"
        oni.zone = "道中"
        momotaro.verbs = {"sabotage", "fight"}

        world.action_graph["edges"] = [
            {
                "from": "observe",
                "to": "sabotage",
                "requires": "observed",
            }
        ]
        world.action_permissions = {
            "sabotage": {
                "hostile": "allow",
                "neutral": "restricted",
                "ally": "deny",
            },
            "fight": {
                "hostile": "allow",
                "neutral": "restricted",
                "ally": "deny",
            },
        }

        world.relations.change(
            "桃太郎",
            "犬",
            affinity=0.8,
        )
        dog_belief = momotaro.beliefs_about.setdefault(
            "犬",
            BeliefAbout(
                base_estimate=world.default_strength_prior
            ),
        )
        dog_belief.identity_seen = True

        before_observe = candidates(
            momotaro,
            world,
            SimpleNamespace(day=1, turn=1),
        )
        sabotage_targets = {
            action.meta["target"]
            for action, _ in before_observe
            if action.verb == "sabotage"
        }
        self.assertNotIn("犬", sabotage_targets)
        self.assertNotIn("鬼", sabotage_targets)

        direct_result, direct_details, _ = VerbEngine(
            world,
            FixedRandom([]),
        ).execute(
            momotaro,
            Action("sabotage", ("犬",)),
            turn=1,
            day=1,
        )
        self.assertEqual(direct_result, "invalid")
        self.assertEqual(
            direct_details["reason"],
            "permission_denied",
        )

        momotaro.beliefs_about["鬼"].identity_seen = True
        after_observe = candidates(
            momotaro,
            world,
            SimpleNamespace(day=1, turn=1),
        )
        sabotage_targets = {
            action.meta["target"]
            for action, _ in after_observe
            if action.verb == "sabotage"
        }
        self.assertIn("鬼", sabotage_targets)

        fight_weights = {
            str(action.meta["target"]): weight
            for action, weight in after_observe
            if action.verb == "fight"
        }
        base_weight = (
            0.1 + momotaro.traits["stubbornness"] * 0.4
        ) * (2.0 * momotaro.traits["temper"])

        eligible_permissions = {
            target.id: world.permission(
                "fight",
                world.target_role(momotaro, target),
            )
            for target in world.present_subjects(momotaro.zone)
            if target.id != momotaro.id
            and target.vitality in {"alive", "revived"}
            and world.permission(
                "fight",
                world.target_role(momotaro, target),
            )
            > 0.0
        }
        per_prior_mass = (
            base_weight
            * (1.0 + world.open_bonus)
            / len(eligible_permissions)
        )
        self.assertEqual(
            set(fight_weights),
            set(eligible_permissions),
        )
        for target_id, permission in eligible_permissions.items():
            self.assertAlmostEqual(
                fight_weights[target_id],
                per_prior_mass * permission,
            )
        self.assertAlmostEqual(
            sum(fight_weights.values()),
            per_prior_mass * sum(eligible_permissions.values()),
        )
        self.assertAlmostEqual(
            fight_weights["猿"] / fight_weights["鬼"],
            world.permission("fight", "neutral"),
        )

        monkey = subjects["猿"]
        monkey.zone = "道中"
        dog.vitality = "downed"
        momotaro.verbs = {
            "observe",
            "pledge",
            "negotiate",
            "confront",
            "rescue",
        }
        momotaro.beliefs["treasure_thief"] = Belief(
            value="鬼",
            confidence=0.5,
        )
        world.relations.change(
            "桃太郎",
            "猿",
            affinity=0.5
            - world.relations.stance("桃太郎", "猿"),
        )
        world.relations.change(
            "猿",
            "桃太郎",
            affinity=0.5
            - world.relations.stance("猿", "桃太郎"),
        )
        world.relations.change(
            "桃太郎",
            "犬",
            affinity=0.5
            - world.relations.stance("桃太郎", "犬"),
        )

        guarded_verbs = {
            "observe",
            "pledge",
            "negotiate",
            "confront",
            "rescue",
        }
        for verb in sorted(guarded_verbs):
            world.action_permissions[verb] = {
                "hostile": "allow",
                "neutral": "allow",
                "ally": "allow",
            }

        allowed = {
            action.verb
            for action, _ in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=2),
            )
        }
        self.assertTrue(guarded_verbs <= allowed)

        for verb in sorted(guarded_verbs):
            world.action_permissions[verb] = {
                "hostile": "deny",
                "neutral": "deny",
                "ally": "deny",
            }

        denied = {
            action.verb
            for action, _ in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=2),
            )
        }
        self.assertTrue(guarded_verbs.isdisjoint(denied))

        momotaro.vitality = "alive"
        world.offers[("桃太郎", "鬼")] = {
            "turn": 2,
            "objective": "鬼ヶ島の宝物",
            "assets": {"勾玉": 1},
        }
        oni.verbs = {"concede"}
        world.action_permissions["concede"] = {
            "hostile": "allow",
            "neutral": "allow",
            "ally": "allow",
        }
        allowed_concede = [
            action
            for action, _ in candidates(
                oni,
                world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "concede"
        ]
        self.assertEqual(len(allowed_concede), 1)

        world.action_permissions["concede"] = {
            "hostile": "deny",
            "neutral": "deny",
            "ally": "deny",
        }
        denied_concede = [
            action
            for action, _ in candidates(
                oni,
                world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "concede"
        ]
        self.assertEqual(denied_concede, [])

    def test_sabotage_and_both_sacrifice_kinds(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        momotaro.beliefs_about["鬼"].identity_seen = True

        engine = VerbEngine(world, FixedRandom([]))
        before_base = oni.base
        before_stress = oni.stress
        result, details, _ = engine.execute(
            momotaro,
            Action("sabotage", ("鬼",)),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "sabotaged")
        self.assertEqual(details["base_before"], before_base)
        self.assertEqual(oni.base, before_base - 6.0)
        self.assertEqual(oni.stress, before_stress + 1.0)

        oni.remove_item("金棒", 1)
        momotaro.add_item("金棒", 1)
        # 勾玉 (added to the fixture in D3c) is also a modifier asset; drop it so
        # the sacrificed asset is unambiguously 金棒 (Claude-side test fix).
        if momotaro.has_item("勾玉"):
            momotaro.remove_item("勾玉", momotaro.inventory.get("勾玉", 0))
        momotaro.verbs = {"sacrifice"}
        asset_actions = [
            action
            for action, _ in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "sacrifice"
            and action.args == ("asset",)
        ]
        self.assertEqual(len(asset_actions), 1)
        before_base = momotaro.base
        result, details, _ = engine.execute(
            momotaro,
            asset_actions[0],
            turn=2,
            day=1,
        )
        self.assertEqual(result, "sacrificed")
        self.assertEqual(details["kind"], "asset")
        self.assertFalse(momotaro.has_item("金棒"))
        self.assertEqual(momotaro.base, before_base + 8.0)

        bond_world, bond_subjects = load_fixture()
        bond_actor = bond_subjects["桃太郎"]
        dog = bond_subjects["犬"]
        bond_actor.zone = "道中"
        dog.zone = "道中"
        bond_actor.verbs = {"sacrifice"}
        bond_world.relations.change(
            "犬",
            "桃太郎",
            affinity=0.6,
        )
        bond_actions = [
            action
            for action, _ in candidates(
                bond_actor,
                bond_world,
                SimpleNamespace(day=1, turn=1),
            )
            if action.verb == "sacrifice"
            and action.args == ("bond",)
        ]
        self.assertEqual(len(bond_actions), 1)

        before_affinity = bond_world.relations.stance(
            "犬",
            "桃太郎",
        )
        bond_actor.stress = 5.0
        result, details, _ = VerbEngine(
            bond_world,
            FixedRandom([]),
        ).execute(
            bond_actor,
            bond_actions[0],
            turn=1,
            day=1,
        )
        self.assertEqual(result, "sacrificed")
        self.assertEqual(details["kind"], "bond")
        self.assertAlmostEqual(
            bond_world.relations.stance("犬", "桃太郎"),
            before_affinity - 0.5,
        )
        self.assertIn("決意", bond_actor.phase)
        self.assertEqual(bond_actor.stress, 2.0)

    def test_negotiate_concede_goodwill_and_trade(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        momotaro.remove_item("勾玉", 1)
        world.relations.change(
            "鬼",
            "桃太郎",
            affinity=1.0,
        )

        engine = VerbEngine(world, FixedRandom([]))
        result, details, _ = engine.execute(
            momotaro,
            Action("negotiate", ("鬼",)),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "offered")
        self.assertEqual(details["objective"], "鬼ヶ島の宝物")

        concede_actions = [
            action
            for action, _ in candidates(
                oni,
                world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "concede"
        ]
        self.assertEqual(len(concede_actions), 1)
        self.assertEqual(
            concede_actions[0].meta["mode"],
            "goodwill",
        )
        result, details, _ = engine.execute(
            oni,
            concede_actions[0],
            turn=2,
            day=1,
        )
        self.assertEqual(result, "conceded")
        self.assertEqual(details["mode"], "goodwill")
        self.assertTrue(momotaro.has_item("鬼ヶ島の宝物"))
        self.assertFalse(oni.has_item("鬼ヶ島の宝物"))

        trade_world, trade_subjects = load_fixture()
        trade_actor = trade_subjects["桃太郎"]
        trade_holder = trade_subjects["鬼"]
        trade_actor.zone = "鬼ヶ島"
        self.assertTrue(trade_actor.has_item("勾玉"))
        self.assertFalse(trade_holder.has_item("勾玉"))

        trade_engine = VerbEngine(
            trade_world,
            FixedRandom([]),
        )
        result, _, _ = trade_engine.execute(
            trade_actor,
            Action("negotiate", ("鬼",)),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "offered")

        trade_actions = [
            action
            for action, _ in candidates(
                trade_holder,
                trade_world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "concede"
        ]
        self.assertEqual(len(trade_actions), 1)
        self.assertEqual(
            trade_actions[0].meta["mode"],
            "trade",
        )
        result, details, _ = trade_engine.execute(
            trade_holder,
            trade_actions[0],
            turn=2,
            day=1,
        )
        self.assertEqual(result, "conceded")
        self.assertEqual(details["mode"], "trade")
        self.assertEqual(details["assets"], {"勾玉": 1})
        self.assertTrue(trade_actor.has_item("鬼ヶ島の宝物"))
        self.assertTrue(trade_holder.has_item("勾玉"))
        self.assertFalse(trade_actor.has_item("勾玉"))

    def test_pledge_then_fight_records_betrayal(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        dog = subjects["犬"]
        momotaro.zone = "道中"
        dog.zone = "道中"
        world.relations.change(
            "桃太郎",
            "犬",
            affinity=0.5,
        )
        world.relations.change(
            "犬",
            "桃太郎",
            affinity=0.5,
        )

        engine = VerbEngine(world, FixedRandom([0.0]))
        result, _, _ = engine.execute(
            momotaro,
            Action("pledge", ("犬",)),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "pledged")
        self.assertTrue(world.is_pledged("桃太郎", "犬"))
        self.assertIn("誓約:犬", momotaro.phase)
        self.assertIn("誓約:桃太郎", dog.phase)

        momotaro.base = 200.0
        result, details, markers = engine.execute(
            momotaro,
            Action(
                "fight",
                ("犬",),
                {
                    "target": "犬",
                    "betrayal": True,
                    "subtype": "betray",
                },
            ),
            turn=2,
            day=1,
        )
        self.assertEqual(result, "won")
        self.assertEqual(details["subtype"], "betray")
        self.assertEqual(momotaro.reputation, -0.5)
        betrayal = [
            marker
            for marker in markers
            if marker["verb"] == "betrayal"
        ]
        self.assertEqual(len(betrayal), 1)
        self.assertEqual(
            betrayal[0]["details"]["subtype"],
            "betray",
        )
        self.assertEqual(
            world.pledges[
                world.pledge_key("桃太郎", "犬")
            ]["broken_by"],
            "桃太郎",
        )

    def test_world_rejects_subject_zone_id_collision(self) -> None:
        world = World.from_yaml(WORLD_PATH)
        subjects = {
            subject.id: subject
            for subject in (
                Subject.from_yaml(path)
                for path in sorted(
                    SUBJECTS_DIR.glob("*.yaml")
                )
            )
        }
        world.zones["犬"] = {"name": "犬"}
        with self.assertRaisesRegex(
            ValueError,
            "collide with zone names",
        ):
            world.bind_subjects(subjects)

    def test_action_graph_override_path(self) -> None:
        explicit = (
            ROOT
            / "templates"
            / "momotaro"
            / "action_graph.yaml"
        )
        world = World.from_yaml(
            WORLD_PATH,
            action_graph_path=explicit,
        )
        self.assertTrue(world.action_graph_enabled)
        self.assertEqual(
            Path(world.action_graph["_source"]),
            explicit.resolve(),
        )

    def test_objective_change_captures_bystander_delta(
        self,
    ) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        dog = subjects["犬"]

        world.days = 1
        world.slots = ("朝",)
        world.daily_events = ()
        world.daily_event_chance = 0.0
        world.scheduled_events = ()

        for subject in subjects.values():
            subject.vitality = "dead"
        for subject in (momotaro, oni, dog):
            subject.vitality = "alive"
            subject.zone = "鬼ヶ島"

        with tempfile.TemporaryDirectory() as temporary:
            simulation = Simulation(
                17,
                world,
                subjects,
                Path(temporary),
            )
            # Simulation() re-binds the relation matrix from the subjects'
            # initial relations, so the goodwill stance must be set after
            # construction (Claude-side fix of the delivered test's ordering).
            current = simulation.world.relations.stance("鬼", "桃太郎")
            simulation.world.relations.change(
                "鬼",
                "桃太郎",
                affinity=0.5 - current,
            )
            simulation.world.offers[("桃太郎", "鬼")] = {
                "turn": 1,
                "objective": "鬼ヶ島の宝物",
                "assets": {},
            }

            def fixed_action(
                subject: Subject,
            ) -> tuple[Action, float | None]:
                if subject.id == "鬼":
                    return (
                        Action(
                            "concede",
                            ("桃太郎",),
                            {
                                "target": "桃太郎",
                                "objective": "鬼ヶ島の宝物",
                                "mode": "goodwill",
                            },
                        ),
                        1.0,
                    )
                return Action("rest"), 1.0

            simulation.choose_action = fixed_action
            path = simulation.run()
            rows = read_rows(path)

        concede = next(
            row
            for row in rows
            if row["kind"] == "decision"
            and row["subject"] == "鬼"
            and row["verb"] == "concede"
        )
        self.assertEqual(concede["result"], "conceded")
        self.assertEqual(
            concede["delta"]["targets"]["犬"]["objective"],
            {"鬼ヶ島の宝物": "桃太郎"},
        )


    def test_auto_effect_plants_and_pays_off_at_slot_end(
        self,
    ) -> None:
        world, subjects = load_fixture()
        world.days = 1
        world.slots = ("朝",)
        world.daily_events = ()
        world.daily_event_chance = 0.0
        world.scheduled_events = ()

        momotaro = subjects["桃太郎"]
        dog = subjects["犬"]
        oni = subjects["鬼"]
        for subject in subjects.values():
            subject.vitality = "dead"
        for subject in (momotaro, dog, oni):
            subject.vitality = "alive"
            subject.zone = "道中"

        momotaro.verbs = {"give_item"}
        dog.verbs = {"rest"}
        oni.verbs = {"rest"}

        with tempfile.TemporaryDirectory() as temporary:
            simulation = Simulation(
                101,
                world,
                subjects,
                Path(temporary),
            )

            def fixed_action(
                subject: Subject,
            ) -> tuple[Action, float | None]:
                if subject.id == momotaro.id:
                    return (
                        Action(
                            "give_item",
                            (dog.id, "きびだんご"),
                            {
                                "target": dog.id,
                                "item": "きびだんご",
                                "stance_sign": 1,
                            },
                        ),
                        1.0,
                    )
                return Action("rest"), 1.0

            simulation.choose_action = fixed_action
            path = simulation.run()
            rows = read_rows(path)

        planted = [
            row
            for row in rows
            if row["kind"] == "event"
            and row["verb"] == "planted"
            and row["details"]["library_id"]
            == "kibidango_loyalty"
        ]
        self.assertEqual(len(planted), 1)

        payoffs = [
            row
            for row in rows
            if row["kind"] == "event"
            and row["verb"] == "payoff"
            and row["details"]["library_id"]
            == "kibidango_loyalty"
        ]
        self.assertEqual(len(payoffs), 1)
        self.assertEqual(payoffs[0]["details"]["mode"], "auto")

        pending = next(
            effect
            for effect in world.pending_effects
            if effect["library_id"] == "kibidango_loyalty"
        )
        self.assertTrue(pending["resolved"])
        self.assertEqual(pending["resolved_turn"], 1)
        loyalty = [
            modifier
            for modifier in momotaro.modifiers
            if modifier.kind == "loyal"
        ]
        self.assertEqual(len(loyalty), 1)
        self.assertEqual(loyalty[0].source, dog.id)
        self.assertEqual(loyalty[0].value, 10.0)

    def test_chosen_effect_candidate_payoff_and_dangling_record(
        self,
    ) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        oni.zone = "鬼ヶ島"

        engine = VerbEngine(world, FixedRandom([]))
        observe = Action(
            "observe",
            (oni.id,),
            {"target": oni.id},
        )
        first_result, _, _ = engine.execute(
            momotaro,
            observe,
            turn=1,
            day=1,
        )
        second_result, _, second_markers = engine.execute(
            momotaro,
            observe,
            turn=2,
            day=1,
        )
        self.assertEqual(first_result, "observed")
        self.assertEqual(second_result, "observed")
        self.assertTrue(
            any(
                marker["verb"] == "planted"
                and marker["details"]["library_id"] == "oni_gap"
                for marker in second_markers
            )
        )

        pending = next(
            effect
            for effect in world.pending_effects
            if effect["library_id"] == "oni_gap"
        )
        self.assertFalse(pending["resolved"])
        payoff_actions = [
            action
            for action, _ in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "payoff"
        ]
        self.assertEqual(len(payoff_actions), 1)
        self.assertEqual(
            payoff_actions[0].args,
            ("oni_gap",),
        )

        result, details, markers = engine.execute(
            momotaro,
            payoff_actions[0],
            turn=3,
            day=1,
        )
        self.assertEqual(result, "paid_off")
        self.assertEqual(details["mode"], "chosen")
        self.assertTrue(pending["resolved"])
        self.assertEqual(pending["resolved_turn"], 3)
        self.assertTrue(
            any(
                marker["verb"] == "payoff"
                and marker["details"]["mode"] == "chosen"
                for marker in markers
            )
        )
        insights = [
            modifier
            for modifier in momotaro.modifiers
            if modifier.kind == "insight"
        ]
        self.assertEqual(len(insights), 1)
        self.assertEqual(insights[0].source, "見切り")
        self.assertEqual(insights[0].value, 15.0)

        dangling_world, dangling_subjects = load_fixture()
        dangling_world.days = 1
        dangling_world.slots = ("朝",)
        dangling_world.daily_events = ()
        dangling_world.daily_event_chance = 0.0
        dangling_world.scheduled_events = ()

        dangling_momotaro = dangling_subjects["桃太郎"]
        dangling_oni = dangling_subjects["鬼"]
        dangling_momotaro.zone = "鬼ヶ島"
        dangling_oni.zone = "鬼ヶ島"
        for subject in dangling_subjects.values():
            if subject.id not in {
                dangling_momotaro.id,
                dangling_oni.id,
            }:
                subject.vitality = "dead"
        dangling_momotaro.verbs = {"rest"}
        dangling_oni.verbs = {"rest"}

        with tempfile.TemporaryDirectory() as temporary:
            simulation = Simulation(
                102,
                dangling_world,
                dangling_subjects,
                Path(temporary),
            )
            observe = Action(
                "observe",
                (dangling_oni.id,),
                {"target": dangling_oni.id},
            )
            simulation.verb_engine.execute(
                dangling_momotaro,
                observe,
                turn=0,
                day=0,
            )
            simulation.verb_engine.execute(
                dangling_momotaro,
                observe,
                turn=0,
                day=0,
            )
            path = simulation.run()
            rows = read_rows(path)

        self.assertEqual(rows[-1]["verb"], "ending")
        self.assertEqual(rows[-1]["id"], "time_limit")
        self.assertEqual(rows[-1]["result"], "expired")
        self.assertEqual(
            rows[-1]["details"]["dangling_effects"],
            1,
        )

    def test_disguise_then_observe_exposes_true_relation(
        self,
    ) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "海"
        oni.zone = "海"

        disguise_actions = [
            action
            for action, _ in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=1),
            )
            if action.verb == "disguise"
        ]
        self.assertEqual(len(disguise_actions), 1)

        engine = VerbEngine(world, FixedRandom([]))
        result, details, _ = engine.execute(
            momotaro,
            disguise_actions[0],
            turn=1,
            day=1,
        )
        self.assertEqual(result, "disguised")
        self.assertEqual(details["displayed"], "旅の商人")
        self.assertEqual(
            world.perceived_name(oni.id, momotaro.id),
            "旅の商人",
        )
        self.assertEqual(
            world.relations.stance(oni.id, momotaro.id),
            0.0,
        )

        observe = Action(
            "observe",
            (momotaro.id,),
            {"target": momotaro.id},
        )
        engine.execute(
            oni,
            observe,
            turn=2,
            day=1,
        )
        _, _, markers = engine.execute(
            oni,
            observe,
            turn=3,
            day=1,
        )

        self.assertTrue(
            any(
                marker["verb"] == "exposure"
                and marker["details"].get("source") == "observe"
                for marker in markers
            )
        )
        self.assertEqual(
            world.perceived_name(oni.id, momotaro.id),
            momotaro.id,
        )
        self.assertTrue(
            oni.beliefs_about[momotaro.id].identity_seen
        )
        self.assertEqual(
            world.relations.stance(oni.id, momotaro.id),
            -0.5,
        )
        relation_targets = {
            row["target"]
            for row in world.relations.flat_rows()
            if row["observer"] == oni.id
        }
        self.assertNotIn("旅の商人", relation_targets)

    def test_grand_gesture_trial_and_donate(
        self,
    ) -> None:
        gesture_world, gesture_subjects = load_fixture()
        momotaro = gesture_subjects["桃太郎"]
        oni = gesture_subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        oni.zone = "鬼ヶ島"
        momotaro.knowledge.add("金棒の由来")

        gesture_actions = [
            action
            for action, _ in candidates(
                momotaro,
                gesture_world,
                SimpleNamespace(day=1, turn=1),
            )
            if action.verb == "grand_gesture"
        ]
        self.assertEqual(len(gesture_actions), 1)
        self.assertEqual(
            gesture_actions[0].meta["item"],
            "勾玉",
        )
        before_stance = gesture_world.relations.stance(
            oni.id,
            momotaro.id,
        )
        result, details, _ = VerbEngine(
            gesture_world,
            FixedRandom([]),
        ).execute(
            momotaro,
            gesture_actions[0],
            turn=1,
            day=1,
        )
        self.assertEqual(result, "grand_gesture")
        self.assertEqual(details["fact"], "金棒の由来")
        self.assertEqual(details["item"], "勾玉")
        self.assertFalse(momotaro.has_item("勾玉"))
        self.assertAlmostEqual(
            gesture_world.relations.stance(
                oni.id,
                momotaro.id,
            ),
            min(1.0, before_stance + 0.6),
        )
        self.assertEqual(momotaro.reputation, 0.1)

        trial_world, trial_subjects = load_fixture()
        trial_actor = trial_subjects["桃太郎"]
        grandfather = trial_subjects["おじいさん"]
        trial_actor.zone = "村"
        grandfather.zone = "村"

        trial_actions = [
            action
            for action, _ in candidates(
                trial_actor,
                trial_world,
                SimpleNamespace(day=1, turn=1),
            )
            if action.verb == "trial"
        ]
        self.assertEqual(len(trial_actions), 1)
        self.assertTrue(trial_actor.has_item("きびだんご"))
        before_affinity = trial_world.relations.stance(
            grandfather.id,
            trial_actor.id,
        )
        result, details, _ = VerbEngine(
            trial_world,
            FixedRandom([]),
        ).execute(
            trial_actor,
            trial_actions[0],
            turn=1,
            day=1,
        )
        self.assertEqual(result, "trial_completed")
        self.assertEqual(
            details["granted"],
            {"fact": "造船術"},
        )
        self.assertIn("造船術", trial_actor.knowledge)
        self.assertTrue(
            any(
                phase.startswith("trial:")
                for phase in trial_actor.phase
            )
        )
        self.assertAlmostEqual(
            trial_world.relations.stance(
                grandfather.id,
                trial_actor.id,
            ),
            min(1.0, before_affinity + 0.1),
        )
        self.assertEqual(
            [
                action
                for action, _ in candidates(
                    trial_actor,
                    trial_world,
                    SimpleNamespace(day=1, turn=2),
                )
                if action.verb == "trial"
            ],
            [],
        )

        donate_world, donate_subjects = load_fixture()
        donor = donate_subjects["桃太郎"]
        holder = donate_subjects["鬼"]
        holder.remove_item("鬼ヶ島の宝物", 1)
        donor.add_item("鬼ヶ島の宝物", 1)
        donor.zone = "村"

        donate_actions = [
            action
            for action, _ in candidates(
                donor,
                donate_world,
                SimpleNamespace(day=1, turn=1),
            )
            if action.verb == "donate"
        ]
        self.assertEqual(len(donate_actions), 1)
        result, details, _ = VerbEngine(
            donate_world,
            FixedRandom([]),
        ).execute(
            donor,
            donate_actions[0],
            turn=1,
            day=1,
        )
        self.assertEqual(result, "donated")
        self.assertEqual(details["holder"], "村")
        self.assertFalse(donor.has_item("鬼ヶ島の宝物"))
        self.assertEqual(
            donate_world.holder("鬼ヶ島の宝物"),
            "村",
        )
        self.assertEqual(donor.reputation, 0.5)

        shared = next(
            ending
            for ending in donate_world.endings
            if ending["id"] == "homecoming_shared"
        )
        self.assertTrue(
            donate_world.ending_reached(
                shared,
                donor,
                donate_world.present_subjects("村"),
                turn=1,
                day=1,
            )
        )
        self.assertEqual(
            donate_world.target_ending,
            "homecoming",
        )

    def test_phase_rules_apply_enable_and_disable(
        self,
    ) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "村"
        momotaro.phase.add("越境")
        momotaro.verbs = {
            "rest",
            "train",
            "observe",
        }

        rule = next(
            rule
            for rule in world.phase_rules
            if rule["id"] == "after_crossing"
        )
        rule["enable"] = ["rest", "train"]
        rule["disable"] = ["train"]

        phase_candidates = candidates(
            momotaro,
            world,
            SimpleNamespace(day=1, turn=2),
        )
        self.assertEqual(
            {action.verb for action, _ in phase_candidates},
            {"rest"},
        )

        momotaro.phase.remove("越境")
        unruled_candidates = candidates(
            momotaro,
            world,
            SimpleNamespace(day=1, turn=3),
        )
        self.assertIn(
            "train",
            {action.verb for action, _ in unruled_candidates},
        )


if __name__ == "__main__":
    unittest.main()
