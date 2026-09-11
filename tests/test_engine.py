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
from engine.contest import believed_strength, strength
from engine.phase2 import (
    apply_effect,
    configure_phase2,
)
from engine.predicate import compile_predicate, conjuncts
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
                        "c9c60c156c1ab227b15cd76e47b4f120"
                        "12a70d1fd133709bbb01c824b96d22df",
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
                # Phase-0 golden lineage: 3e95ce80...958e until the layer-vector identity
                # dimension was corrected to `displayed != id` (D6 review A-1). Forcing that
                # dimension back to 1.0 reproduces the old value exactly (verified).
                "29477d28196692e2551dd83f1dd24742"
                "eafacc63acf68d507b3def5133a1f498",
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
        present = world.present_subjects(momotaro.zone)
        actor_strength = strength(
            momotaro,
            world,
            present,
        )
        advantages = {
            target_id: min(
                1.5,
                max(
                    0.5,
                    1.0
                    + (
                        actor_strength
                        - believed_strength(
                            momotaro,
                            subjects[target_id],
                            world,
                            present,
                        )
                    )
                    / max(1.0, abs(actor_strength)),
                ),
            )
            for target_id in eligible_permissions
        }
        advantage_total = sum(advantages.values())
        opened_mass = base_weight * (1.0 + world.open_bonus)

        self.assertEqual(
            set(fight_weights),
            set(eligible_permissions),
        )
        for target_id, permission in eligible_permissions.items():
            self.assertAlmostEqual(
                fight_weights[target_id],
                opened_mass
                * advantages[target_id]
                / advantage_total
                * permission,
            )
        self.assertAlmostEqual(
            fight_weights["猿"] / fight_weights["鬼"],
            (
                advantages["猿"]
                * eligible_permissions["猿"]
            )
            / (
                advantages["鬼"]
                * eligible_permissions["鬼"]
            ),
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
        self.assertTrue(momotaro.has_item("勾玉"))
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
            "きびだんご",
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
        self.assertEqual(details["item"], "きびだんご")
        self.assertTrue(momotaro.has_item("勾玉"))
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
        monkey = trial_subjects["猿"]
        trial_actor.zone = "道中"
        monkey.zone = "道中"

        result, _, _ = VerbEngine(
            trial_world,
            FixedRandom([]),
        ).execute(
            trial_actor,
            Action(
                "give_item",
                (monkey.id, "きびだんご"),
                {
                    "target": monkey.id,
                    "item": "きびだんご",
                    "stance_sign": 1,
                },
            ),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "given")

        trial_actions = [
            action
            for action, _ in candidates(
                trial_actor,
                trial_world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "trial"
        ]
        self.assertEqual(len(trial_actions), 1)
        self.assertEqual(
            trial_actions[0].meta["target"],
            monkey.id,
        )
        before_affinity = trial_world.relations.stance(
            monkey.id,
            trial_actor.id,
        )
        result, details, _ = VerbEngine(
            trial_world,
            FixedRandom([]),
        ).execute(
            trial_actor,
            trial_actions[0],
            turn=2,
            day=1,
        )
        self.assertEqual(result, "trial_completed")
        self.assertEqual(
            details["granted"],
            {"fact": "猿の知恵"},
        )
        self.assertIn("猿の知恵", trial_actor.knowledge)
        self.assertAlmostEqual(
            trial_world.relations.stance(
                monkey.id,
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
                    SimpleNamespace(day=1, turn=3),
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
        donor.zone = "鬼ヶ島"

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
        self.assertEqual(details["phase"], "還元")
        self.assertEqual(details["holder"], donor.id)
        self.assertTrue(donor.has_item("鬼ヶ島の宝物"))
        self.assertIn("還元", donor.phase)
        self.assertEqual(donor.reputation, 0.3)

        donor.zone = "村"
        donate_world.days = 1
        donate_world.slots = ("朝",)
        donate_world.daily_events = ()
        donate_world.daily_event_chance = 0.0
        donate_world.scheduled_events = ()
        for subject in donate_subjects.values():
            if subject.id != donor.id:
                subject.vitality = "dead"
        donor.verbs = {"rest"}

        with tempfile.TemporaryDirectory() as temporary:
            simulation = Simulation(
                104,
                donate_world,
                donate_subjects,
                Path(temporary),
            )
            path = simulation.run()
            rows = read_rows(path)

        self.assertEqual(rows[-1]["verb"], "ending")
        self.assertEqual(rows[-1]["id"], "homecoming_shared")
        self.assertEqual(
            rows[-1]["details"]["delivered"],
            {"鬼ヶ島の宝物": "村"},
        )
        self.assertEqual(
            donate_world.holder("鬼ヶ島の宝物"),
            "村",
        )
        self.assertEqual(
            donate_world.target_ending,
            ("homecoming", "homecoming_shared"),
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
        }

        rule = next(
            rule
            for rule in world.phase_rules
            if rule["id"] == "after_crossing"
        )
        self.assertEqual(rule["enable"], [])
        self.assertEqual(rule["disable"], ["train"])

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


    def test_effect_placeholders_resolve_to_subject_ids(
        self,
    ) -> None:
        world, subjects = load_fixture()
        planter = subjects["桃太郎"]
        target = subjects["犬"]

        cases = (
            ("planter", planter.id),
            ("$planter", planter.id),
            ("self", planter.id),
            ("$self", planter.id),
            ("target", target.id),
            ("$target", target.id),
        )
        for index, (placeholder, expected) in enumerate(cases):
            pending = {
                "id": f"placeholder:{index}",
                "library_id": "placeholder_test",
                "planted_by": planter.id,
                "planted_turn": 1,
                "target": target.id,
                "condition": compile_predicate("turn >= 0"),
                "description": "placeholder test",
                "effect": {
                    "modifier": {
                        "target": placeholder,
                        "source": placeholder,
                        "value": 1,
                        "kind": "test",
                    }
                },
                "mode": "auto",
                "resolved": False,
                "resolved_turn": None,
            }
            details = apply_effect(
                world,
                pending,
                turn=2,
            )
            self.assertEqual(
                details["applied"]["target"],
                expected,
            )
            self.assertEqual(
                details["applied"]["source"],
                expected,
            )
            self.assertTrue(pending["resolved"])
            self.assertTrue(
                any(
                    modifier.id == f"effect:placeholder:{index}"
                    for modifier in subjects[expected].modifiers
                )
            )

    def test_phase2_fixture_runs_seeds_1_through_30(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for seed in range(1, 31):
                world, subjects = load_fixture()
                path = Simulation(
                    seed,
                    world,
                    subjects,
                    root / f"seed-{seed}",
                ).run()
                rows = read_rows(path)
                self.assertTrue(rows, f"seed {seed} produced no rows")
                self.assertIn(
                    rows[-1]["verb"],
                    {"ending", "aborted"},
                    f"seed {seed} did not terminate cleanly",
                )

    def test_accurate_strength_report_is_not_exposure(
        self,
    ) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        oni.zone = "鬼ヶ島"

        belief = oni.beliefs_about[momotaro.id]
        belief.base_estimate = momotaro.base
        belief.misled_by = momotaro.id
        belief.observe_progress = 1.0

        result, details, markers = VerbEngine(
            world,
            FixedRandom([]),
        ).execute(
            oni,
            Action(
                "observe",
                (momotaro.id,),
                {"target": momotaro.id},
            ),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "observed")
        self.assertIsNone(details["exposed_estimate"])
        self.assertIsNone(belief.misled_by)
        self.assertEqual(
            [
                marker
                for marker in markers
                if marker["verb"] == "exposure"
            ],
            [],
        )

    def test_keepsake_candidate_exclusions_and_trade_paths(
        self,
    ) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        dog = subjects["犬"]
        oni = subjects["鬼"]
        momotaro.zone = "道中"
        dog.zone = "道中"

        momotaro.verbs = {
            "give_item",
            "sacrifice",
        }
        weighted = candidates(
            momotaro,
            world,
            SimpleNamespace(day=1, turn=1),
        )
        keepsake_actions = [
            action
            for action, _ in weighted
            if action.meta.get("item") == "勾玉"
        ]
        self.assertEqual(keepsake_actions, [])

        momotaro.zone = "鬼ヶ島"
        oni.zone = "鬼ヶ島"
        momotaro.verbs = {"negotiate"}
        result, details, _ = VerbEngine(
            world,
            FixedRandom([]),
        ).execute(
            momotaro,
            Action(
                "negotiate",
                (oni.id,),
                {
                    "target": oni.id,
                    "objective": "鬼ヶ島の宝物",
                    "stance_sign": 1,
                },
            ),
            turn=2,
            day=1,
        )
        self.assertEqual(result, "offered")
        self.assertEqual(details["assets"]["勾玉"], 1)

        loot_world, loot_subjects = load_fixture()
        winner = loot_subjects["鬼"]
        loser = loot_subjects["桃太郎"]
        winner.zone = "鬼ヶ島"
        loser.zone = "鬼ヶ島"
        winner.base = 500.0
        loot_world.vitality["lethal_exempt"].add(loser.id)

        result, details, _ = VerbEngine(
            loot_world,
            FixedRandom([0.0]),
        ).execute(
            winner,
            Action(
                "fight",
                (loser.id,),
                {"target": loser.id},
            ),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "won")
        self.assertEqual(details["loot"]["勾玉"], 1)
        self.assertTrue(winner.has_item("勾玉"))
        self.assertFalse(loser.has_item("勾玉"))

    def test_confront_weights_preserve_confidence_ratio(
        self,
    ) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        dog = subjects["犬"]
        oni = subjects["鬼"]
        momotaro.zone = "道中"
        dog.zone = "道中"
        oni.zone = "道中"
        momotaro.verbs = {"confront"}

        world.facts["claim_low"] = {
            "id": "claim_low",
            "values": [dog.id, oni.id],
            "act_threshold": 0.5,
        }
        world.facts["claim_high"] = {
            "id": "claim_high",
            "values": [dog.id, oni.id],
            "act_threshold": 0.5,
        }
        momotaro.beliefs["claim_low"] = Belief(
            value=dog.id,
            confidence=0.6,
        )
        momotaro.beliefs["claim_high"] = Belief(
            value=oni.id,
            confidence=0.9,
        )
        world.action_permissions["confront"] = {
            "hostile": "allow",
            "neutral": "allow",
            "ally": "allow",
        }

        confront_weights = {
            action.meta["fact"]: weight
            for action, weight in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=1),
            )
            if action.verb == "confront"
        }
        low = (
            0.15
            + 0.6 * 1.2
            + momotaro.traits["temper"] * 0.3
        )
        high = (
            0.15
            + 0.9 * 1.2
            + momotaro.traits["temper"] * 0.3
        )
        self.assertAlmostEqual(
            confront_weights["claim_low"]
            / confront_weights["claim_high"],
            low / high,
        )
        self.assertAlmostEqual(
            sum(confront_weights.values()),
            (
                (low + high)
                / 2.0
                * (1.0 + world.open_bonus)
            ),
        )

    def test_non_betrayal_meta_omits_subtype(
        self,
    ) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        momotaro.zone = "鬼ヶ島"
        oni.zone = "鬼ヶ島"
        momotaro.verbs = {"fight"}

        action = next(
            action
            for action, _ in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=1),
            )
            if action.verb == "fight"
            and action.meta["target"] == oni.id
        )
        self.assertFalse(action.meta["betrayal"])
        self.assertNotIn("subtype", action.meta)


    def test_phase2_fixture_plant_paths_are_reachable(
        self,
    ) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        dog = subjects["犬"]

        momotaro.zone = "道中"
        promise_actions = [
            action
            for action, _ in candidates(
                momotaro,
                world,
                SimpleNamespace(day=1, turn=1),
            )
            if action.verb == "plant"
            and action.meta.get("effect_id")
            == "village_promise"
        ]
        self.assertEqual(len(promise_actions), 1)

        momotaro.zone = "森"
        result, _, markers = VerbEngine(
            world,
            FixedRandom([]),
        ).execute(
            momotaro,
            Action(
                "investigate",
                ("森",),
                {
                    "target": "森",
                    "gather": False,
                },
            ),
            turn=2,
            day=1,
        )
        self.assertEqual(result, "investigated")
        self.assertTrue(
            any(
                marker["verb"] == "planted"
                and marker["details"]["library_id"]
                == "forest_shortcut"
                for marker in markers
            )
        )

        momotaro.zone = "道中"
        dog.zone = "道中"
        # campfire_oath is planted only toward allies (to_role: [ally]) since the
        # Phase 2 review (B-1): raise the dog to companionship stance first.
        world.relations.change(
            momotaro.id,
            dog.id,
            affinity=1.0 - world.relations.stance(momotaro.id, dog.id),
        )
        result, _, markers = VerbEngine(
            world,
            FixedRandom([]),
        ).execute(
            momotaro,
            Action(
                "share_knowledge",
                (dog.id, "雑談"),
                {
                    "target": dog.id,
                    "topic": "雑談",
                    "stance_sign": 1,
                },
            ),
            turn=3,
            day=1,
        )
        self.assertEqual(result, "shared")
        self.assertTrue(
            any(
                marker["verb"] == "planted"
                and marker["details"]["library_id"]
                == "campfire_oath"
                for marker in markers
            )
        )

        chosen_ids = {
            effect["library_id"]
            for effect in world.pending_effects
            if effect["mode"] == "chosen"
        }
        self.assertTrue(
            {
                "campfire_oath",
                "forest_shortcut",
            }
            <= chosen_ids
        )

    def test_effect_payload_required_keys_are_validated(
        self,
    ) -> None:
        required = {
            "modifier": {
                "target": "planter",
                "source": "test",
                "value": 1,
                "kind": "test",
            },
            "neutralize": {
                "target": "target",
                "source": "test",
            },
            "stance": {
                "a": "planter",
                "b": "target",
                "delta": 0.1,
            },
            "reputation": {
                "target": "planter",
                "delta": 0.1,
            },
            "enable_verb": {
                "verb": "rest",
            },
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for kind, complete in sorted(required.items()):
                missing_key = sorted(complete)[0]
                body = {
                    key: value
                    for key, value in complete.items()
                    if key != missing_key
                }
                effect_path = root / f"{kind}.yaml"
                effect_path.write_text(
                    yaml.safe_dump(
                        [
                            {
                                "id": f"missing_{kind}",
                                "plant": {"verb": "plant"},
                                "payoff": {
                                    "condition": "turn >= 0",
                                    "description": "invalid",
                                    "effect": {kind: body},
                                    "mode": "auto",
                                },
                            }
                        ],
                        allow_unicode=True,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
                world, _ = load_fixture()
                with self.assertRaisesRegex(
                    ValueError,
                    "missing required keys",
                ):
                    configure_phase2(
                        world,
                        {
                            "gapengine": {
                                "effects": str(effect_path)
                            }
                        },
                        WORLD_PATH,
                    )

    def test_all_effect_payload_kinds_execute(
        self,
    ) -> None:
        world, subjects = load_fixture()
        planter = subjects["桃太郎"]
        target = subjects["鬼"]
        planter.zone = "鬼ヶ島"
        target.zone = "鬼ヶ島"

        def pending(
            effect_id: str,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            return {
                "id": effect_id,
                "library_id": "payload_test",
                "planted_by": planter.id,
                "planted_turn": 1,
                "target": target.id,
                "condition": compile_predicate("turn >= 0"),
                "description": "payload test",
                "effect": payload,
                "mode": "auto",
                "resolved": False,
                "resolved_turn": None,
            }

        neutralized = pending(
            "neutralize",
            {
                "neutralize": {
                    "target": "target",
                    "source": "金棒",
                }
            },
        )
        details = apply_effect(
            world,
            neutralized,
            turn=2,
        )
        self.assertEqual(details["applied"]["kind"], "neutralize")
        self.assertGreaterEqual(details["applied"]["count"], 1)
        self.assertTrue(
            any(
                modifier.source == "金棒"
                and not modifier.active
                for modifier in target.modifiers
            )
        )

        before_stance = world.relations.stance(
            planter.id,
            target.id,
        )
        stance_effect = pending(
            "stance",
            {
                "stance": {
                    "a": "planter",
                    "b": "target",
                    "delta": 0.2,
                }
            },
        )
        apply_effect(
            world,
            stance_effect,
            turn=3,
        )
        self.assertAlmostEqual(
            world.relations.stance(
                planter.id,
                target.id,
            ),
            min(1.0, before_stance + 0.2),
        )

        before_reputation = planter.reputation
        reputation_effect = pending(
            "reputation",
            {
                "reputation": {
                    "target": "planter",
                    "delta": 0.25,
                }
            },
        )
        apply_effect(
            world,
            reputation_effect,
            turn=4,
        )
        self.assertEqual(
            planter.reputation,
            round(before_reputation + 0.25, 4),
        )

        planter.verbs.discard("guard")
        enable_effect = pending(
            "enable",
            {
                "enable_verb": {
                    "verb": "guard",
                }
            },
        )
        apply_effect(
            world,
            enable_effect,
            turn=5,
        )
        self.assertIn("guard", planter.verbs)

    def test_time_limit_uses_any_phase2_configuration(
        self,
    ) -> None:
        world, subjects = load_fixture()
        world.effect_library = {}
        world.days = 1
        world.slots = ("朝",)
        world.daily_events = ()
        world.daily_event_chance = 0.0
        world.scheduled_events = ()

        protagonist = subjects["桃太郎"]
        protagonist.verbs = {"rest"}
        for subject in subjects.values():
            if subject.id != protagonist.id:
                subject.vitality = "dead"

        with tempfile.TemporaryDirectory() as temporary:
            path = Simulation(
                105,
                world,
                subjects,
                Path(temporary),
            ).run()
            rows = read_rows(path)

        self.assertTrue(world.disguises)
        self.assertEqual(rows[-1]["id"], "time_limit")
        self.assertEqual(
            rows[-1]["details"]["dangling_effects"],
            0,
        )

    def test_aborted_records_dangling_chosen_effects(
        self,
    ) -> None:
        world, subjects = load_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "layers.jsonl"
            simulation = Simulation(
                106,
                world,
                subjects,
                Path(temporary),
            )
            simulation.world.pending_effects.append(
                {
                    "id": "dangling:test",
                    "library_id": "dangling",
                    "planted_by": "桃太郎",
                    "planted_turn": 1,
                    "target": "鬼",
                    "condition": compile_predicate("turn >= 0"),
                    "description": "dangling test",
                    "effect": {
                        "reputation": {
                            "target": "planter",
                            "delta": 0.1,
                        }
                    },
                    "mode": "chosen",
                    "resolved": False,
                    "resolved_turn": None,
                }
            )
            from engine.log import LayersWriter

            with LayersWriter(path) as writer:
                simulation._write_aborted(writer)
            rows = read_rows(path)

        self.assertEqual(rows[-1]["verb"], "aborted")
        self.assertEqual(
            rows[-1]["details"]["dangling_effects"],
            1,
        )


class Phase4EngineTests(unittest.TestCase):
    def test_affinity_cap_clamps_initial_and_changed_affinity(
        self,
    ) -> None:
        world = World.from_yaml(WORLD_PATH)
        subjects = self._load_subjects()
        actor = subjects["桃太郎"]
        actor.initial_relations["鬼"]["affinity"] = 0.8
        actor.modifiers.append(
            Modifier(
                id="test:affinity-cap",
                source="警戒",
                value=0.0,
                kind="test",
                affinity_cap=0.2,
                affinity_cap_targets=("鬼",),
            )
        )

        world.bind_subjects(subjects)

        self.assertEqual(
            world.relations.stance("桃太郎", "鬼"),
            0.2,
        )
        delta = world.relations.change(
            "桃太郎",
            "鬼",
            affinity=0.5,
        )
        self.assertEqual(delta["affinity"], 0.0)
        self.assertEqual(
            world.relations.stance("桃太郎", "鬼"),
            0.2,
        )

    def test_affinity_cap_stops_after_modifier_is_inactive(
        self,
    ) -> None:
        world = World.from_yaml(WORLD_PATH)
        subjects = self._load_subjects()
        modifier = Modifier(
            id="test:temporary-affinity-cap",
            source="警戒",
            value=0.0,
            kind="test",
            affinity_cap=0.2,
            affinity_cap_targets=("鬼",),
        )
        subjects["桃太郎"].modifiers.append(modifier)
        world.bind_subjects(subjects)

        world.relations.change(
            "桃太郎",
            "鬼",
            affinity=1.0,
        )
        self.assertEqual(
            world.relations.stance("桃太郎", "鬼"),
            0.2,
        )

        modifier.active = False
        world.relations.change(
            "桃太郎",
            "鬼",
            affinity=0.4,
        )
        self.assertEqual(
            world.relations.stance("桃太郎", "鬼"),
            0.6,
        )

    def test_affinity_cap_targets_do_not_limit_other_targets(
        self,
    ) -> None:
        world = World.from_yaml(WORLD_PATH)
        subjects = self._load_subjects()
        subjects["桃太郎"].modifiers.append(
            Modifier(
                id="test:targeted-affinity-cap",
                source="警戒",
                value=0.0,
                kind="test",
                affinity_cap=0.2,
                affinity_cap_targets=("鬼",),
            )
        )
        world.bind_subjects(subjects)

        world.relations.change(
            "桃太郎",
            "犬",
            affinity=1.0,
        )

        self.assertEqual(
            world.relations.stance("桃太郎", "犬"),
            1.0,
        )

    def test_momotaro_affinity_cap_resolver_returns_none(
        self,
    ) -> None:
        world, _ = load_fixture()
        resolver = world.relations._affinity_cap_resolver

        self.assertIsNotNone(resolver)
        assert resolver is not None
        self.assertIsNone(
            resolver("桃太郎", "鬼")
        )

    def _load_subjects(self) -> dict[str, Subject]:
        return {
            subject.id: subject
            for subject in (
                Subject.from_yaml(path)
                for path in sorted(SUBJECTS_DIR.glob("*.yaml"))
            )
        }

    def test_conjuncts_recursively_flattens_top_level_and(
        self,
    ) -> None:
        predicate = compile_predicate(
            "holds(桃太郎, 鬼ヶ島の宝物) "
            "and (zone(桃太郎) == '村' "
            "and stance(桃太郎, 犬) >= 0.6)",
            {
                "holds",
                "zone",
                "stance",
                "桃太郎",
                "鬼ヶ島の宝物",
                "犬",
            },
        )

        parts = conjuncts(predicate)

        self.assertEqual(len(parts), 3)
        self.assertTrue(
            all(" and " not in part.source for part in parts)
        )
        self.assertEqual(
            [part.source.split("(", 1)[0] for part in parts],
            ["holds", "zone", "stance"],
        )

    def test_truth_draw_is_deterministic_and_uses_no_main_rng(
        self,
    ) -> None:
        raw_world = yaml.safe_load(
            WORLD_PATH.read_text(encoding="utf-8")
        )
        raw_world["truth"]["treasure_thief"] = {
            "candidates": {
                "鬼": 3.0,
                "猿": 1.0,
                "犬": 1.0,
            },
            "known_by": ["$truth"],
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world_path = root / "world.yaml"
            world_path.write_text(
                yaml.safe_dump(
                    raw_world,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
                newline="\n",
            )

            seed = 73
            first_world = World.from_yaml(world_path)
            first_simulation = Simulation(
                seed,
                first_world,
                self._load_subjects(),
                root / "first",
            )
            second_world = World.from_yaml(world_path)
            second_simulation = Simulation(
                seed,
                second_world,
                self._load_subjects(),
                root / "second",
            )

        expected_state = random.Random(seed).getstate()
        self.assertEqual(
            first_simulation.rng.getstate(),
            expected_state,
        )
        self.assertEqual(
            second_simulation.rng.getstate(),
            expected_state,
        )
        self.assertEqual(
            first_world.drawn_truth,
            second_world.drawn_truth,
        )

        selected = first_world.truth["treasure_thief"]
        self.assertEqual(
            selected,
            first_world.drawn_truth["treasure_thief"],
        )
        self.assertIn(selected, {"鬼", "猿", "犬"})
        self.assertEqual(
            first_simulation._header()["truth"],
            first_world.drawn_truth,
        )

        for subject_id in ("鬼", "猿", "犬"):
            belief = first_simulation.subjects[
                subject_id
            ].beliefs.get("treasure_thief")
            if subject_id == selected:
                self.assertIsNotNone(belief)
                assert belief is not None
                self.assertEqual(belief.value, selected)
                self.assertEqual(belief.confidence, 1.0)
                self.assertFalse(belief.derived)
            else:
                self.assertIsNone(belief)

    def test_rethink_requires_stagnation_and_replays_evidence(
        self,
    ) -> None:
        world, subjects = load_fixture()
        actor = subjects["桃太郎"]
        actor.verbs.add("rethink")
        actor.knowledge = {"金棒の由来"}
        actor.beliefs = {
            "treasure_thief": Belief(
                value="猿",
                confidence=0.7,
            ),
            "not_valued": Belief(
                value="既知",
                confidence=1.0,
            ),
        }
        world.facts["not_valued"] = {
            "id": "not_valued",
            "label": "値を列挙しない事実",
        }

        stalled_simulation = SimpleNamespace(
            day=1,
            turn=5,
            _last_fact_turn={actor.id: 2},
        )
        self.assertEqual(
            [
                action
                for action, _ in candidates(
                    actor,
                    world,
                    stalled_simulation,
                )
                if action.verb == "rethink"
            ],
            [],
        )

        actor.beliefs["oni_weakness"] = Belief(
            value="火",
            confidence=0.2,
            derived=True,
        )

        recent_simulation = SimpleNamespace(
            day=1,
            turn=4,
            _last_fact_turn={actor.id: 2},
        )
        self.assertEqual(
            [
                action
                for action, _ in candidates(
                    actor,
                    world,
                    recent_simulation,
                )
                if action.verb == "rethink"
            ],
            [],
        )

        rethink_actions = [
            (action, weight)
            for action, weight in candidates(
                actor,
                world,
                stalled_simulation,
            )
            if action.verb == "rethink"
        ]

        self.assertEqual(len(rethink_actions), 1)
        action, weight = rethink_actions[0]
        self.assertAlmostEqual(
            weight,
            0.1 + actor.traits["curiosity"] * 0.4,
        )
        self.assertEqual(
            action.meta["evidence"],
            ["金棒の由来"],
        )

        result, details, markers = VerbEngine(
            world,
            FixedRandom([]),
        ).execute(
            actor,
            action,
            turn=5,
            day=1,
        )

        self.assertEqual(result, "rethought")
        self.assertEqual(
            details["before"]["treasure_thief"]["value"],
            "猿",
        )
        self.assertEqual(
            details["after"]["treasure_thief"]["value"],
            "猿",
        )
        self.assertEqual(
            actor.beliefs["treasure_thief"].confidence,
            0.7,
        )
        self.assertFalse(
            actor.beliefs["treasure_thief"].derived
        )
        self.assertEqual(
            details["before"]["oni_weakness"]["value"],
            "火",
        )
        self.assertEqual(
            details["after"]["oni_weakness"]["value"],
            "金棒",
        )
        self.assertTrue(
            actor.beliefs["oni_weakness"].derived
        )
        self.assertEqual(
            [marker["verb"] for marker in markers],
            ["rethink"],
        )

        snapshot = actor.layer_snapshot(
            world,
            world.present_subjects(actor.zone),
        )
        self.assertNotIn(
            "derived",
            snapshot["valued_beliefs"]["oni_weakness"],
        )


if __name__ == "__main__":
    unittest.main()
