from __future__ import annotations

import random
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from engine.actions import Action, candidates
from engine.subject import Subject
from engine.verbs import VerbEngine
from engine.world import World


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "detective"
TEMPLATE = ROOT / "templates" / "detective"

SUSPECTS = {
    "容疑者甲",
    "容疑者乙",
    "容疑者丙",
}


def load_subjects(
    directory: Path = PROJECT / "subjects",
) -> dict[str, Subject]:
    subjects = [
        Subject.from_yaml(path)
        for path in sorted(
            directory.glob("*.yaml"),
            key=lambda value: value.name,
        )
    ]
    return {
        subject.id: subject
        for subject in subjects
    }


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(
        path.read_text(encoding="utf-8")
    )


def load_case(
    seed: int,
) -> tuple[World, dict[str, Subject]]:
    world = World.from_yaml(
        PROJECT / "world.yaml",
        action_graph_path=(
            TEMPLATE / "action_graph.yaml"
        ),
    )
    world.resolve_truth(seed)
    subjects = load_subjects()
    world.bind_subjects(subjects)
    return world, subjects


class DetectiveTemplateTests(unittest.TestCase):
    def test_world_and_six_subjects_obey_detective_contract(
        self,
    ) -> None:
        world, subjects = load_case(0)

        self.assertEqual(
            set(subjects),
            {
                "探偵",
                "容疑者甲",
                "容疑者乙",
                "容疑者丙",
                "証人壱",
                "証人弐",
            },
        )
        self.assertEqual(world.protagonist, "探偵")
        self.assertEqual(
            world.target_ending,
            "solved",
        )
        self.assertEqual(
            tuple(
                ending["id"]
                for ending in world.target_endings()
            ),
            ("solved",),
        )
        self.assertEqual(world.objectives, {})
        self.assertEqual(
            world.genres,
            frozenset({"detective"}),
        )
        self.assertEqual(world.effect_library, {})

        self.assertEqual(
            world.facts["culprit"]["values"],
            ["容疑者甲", "容疑者乙", "容疑者丙"],
        )
        self.assertEqual(
            world.facts["weapon"]["values"],
            ["燭台", "ペーパーナイフ", "彫像"],
        )
        self.assertEqual(
            world.truth["weapon"],
            "燭台",
        )

        evidence = {
            fact_id: definition
            for fact_id, definition in world.facts.items()
            if fact_id.startswith("evidence_")
        }
        self.assertEqual(len(evidence), 6)

        decoys = {
            fact_id: definition
            for fact_id, definition in evidence.items()
            if definition.get("decoy", False)
        }
        self.assertEqual(
            set(decoys),
            {
                "evidence_01_red_thread",
                "evidence_02_muddy_footprint",
            },
        )
        self.assertEqual(
            {
                definition["implies"]["value"]
                for definition in decoys.values()
            },
            {"容疑者甲", "容疑者乙"},
        )

        weapon_evidence = {
            fact_id
            for fact_id, definition in evidence.items()
            if any(
                isinstance(definition.get(relation), dict)
                and definition[relation].get("fact")
                == "weapon"
                for relation in ("implies", "refutes")
            )
        }
        self.assertGreaterEqual(
            len(weapon_evidence),
            2,
        )

        for suspect_id in sorted(SUSPECTS):
            alibis = [
                modifier
                for modifier in subjects[
                    suspect_id
                ].modifiers
                if modifier.source == "アリバイ"
            ]
            self.assertEqual(len(alibis), 1)
            self.assertTrue(alibis[0].active)
            self.assertFalse(alibis[0].visible)
            self.assertEqual(
                alibis[0].kind,
                "alibi",
            )
            self.assertEqual(
                alibis[0].value,
                25.0,
            )
            self.assertEqual(
                subjects[suspect_id].base,
                45.0,
            )

        self.assertEqual(
            subjects["探偵"].base,
            72.0,
        )
        self.assertIn(
            "rethink",
            subjects["探偵"].verbs,
        )

    def test_truth_draw_covers_all_suspects_in_thirty_seeds(
        self,
    ) -> None:
        distribution = {
            suspect_id: 0
            for suspect_id in SUSPECTS
        }

        for seed in range(30):
            world = World.from_yaml(
                PROJECT / "world.yaml",
                action_graph_path=(
                    TEMPLATE / "action_graph.yaml"
                ),
            )
            drawn = world.resolve_truth(seed)
            selected = drawn["culprit"]
            self.assertIn(selected, SUSPECTS)
            distribution[selected] += 1

        self.assertEqual(
            sum(distribution.values()),
            30,
        )
        self.assertTrue(
            all(
                count > 0
                for count in distribution.values()
            )
        )

    def test_only_drawn_culprit_knows_truth_with_full_confidence(
        self,
    ) -> None:
        world, subjects = load_case(4)
        selected = world.truth["culprit"]

        for suspect_id in sorted(SUSPECTS):
            belief = subjects[
                suspect_id
            ].beliefs["culprit"]
            if suspect_id == selected:
                self.assertEqual(
                    belief.value,
                    selected,
                )
                self.assertEqual(
                    belief.confidence,
                    1.0,
                )
                self.assertFalse(belief.derived)
            else:
                self.assertEqual(
                    belief.confidence,
                    0.35,
                )
                self.assertFalse(belief.derived)

        self.assertTrue(
            all(
                "weapon" not in subject.beliefs
                for subject in subjects.values()
            )
        )

    def test_genre_pruning_permissions_and_roles_are_uniform(
        self,
    ) -> None:
        world, subjects = load_case(0)

        self.assertFalse(
            world.genre_allows("fight")
        )
        self.assertFalse(
            world.genre_allows("sabotage")
        )
        self.assertEqual(
            world.permission("fight", "hostile"),
            0.0,
        )
        self.assertEqual(
            world.permission("sabotage", "hostile"),
            0.0,
        )
        self.assertEqual(
            world.permission("mislead", "hostile"),
            1.0,
        )
        self.assertEqual(
            world.permission("mislead", "neutral"),
            0.15,
        )
        self.assertEqual(
            world.permission("mislead", "ally"),
            0.15,
        )

        roles = {
            world.target_role(
                subjects[suspect_id],
                subjects["探偵"],
            )
            for suspect_id in SUSPECTS
        }
        self.assertEqual(roles, {"hostile"})

        qd = load_yaml(
            TEMPLATE / "qd.yaml"
        )
        self.assertEqual(
            qd["categories"],
            ["I", "II", "III"],
        )

    def test_decoy_misjudgment_rethink_and_correct_confront(
        self,
    ) -> None:
        selected_seed: int | None = None
        for seed in range(100):
            probe = World.from_yaml(
                PROJECT / "world.yaml",
                action_graph_path=(
                    TEMPLATE / "action_graph.yaml"
                ),
            )
            if (
                probe.resolve_truth(seed)["culprit"]
                == "容疑者丙"
            ):
                selected_seed = seed
                break

        self.assertIsNotNone(selected_seed)
        assert selected_seed is not None

        world, subjects = load_case(
            selected_seed
        )
        detective = subjects["探偵"]
        suspect_a = subjects["容疑者甲"]
        suspect_c = subjects["容疑者丙"]
        suspect_a.zone = detective.zone

        for evidence_id in (
            "evidence_01_red_thread",
            "evidence_03_candlestick_wax",
            "evidence_06_reconstruction",
        ):
            detective.knowledge.add(evidence_id)
            detective.apply_evidence(
                evidence_id,
                world,
            )

        self.assertEqual(
            detective.beliefs["culprit"].value,
            "容疑者甲",
        )
        self.assertTrue(
            detective.beliefs["culprit"].derived
        )
        self.assertEqual(
            detective.beliefs["weapon"].value,
            "燭台",
        )
        self.assertTrue(
            detective.beliefs["weapon"].derived
        )

        verb_engine = VerbEngine(
            world,
            random.Random(91),
        )

        def observe_target(
            target: Subject,
            *,
            first_turn: int,
            day: int,
        ) -> None:
            for offset in range(2):
                result, _, _ = verb_engine.execute(
                    detective,
                    Action(
                        "observe",
                        (target.id,),
                    ),
                    turn=first_turn + offset,
                    day=day,
                )
                self.assertEqual(
                    result,
                    "observed",
                )

            belief_about = (
                detective.beliefs_about[target.id]
            )
            self.assertTrue(
                belief_about.identity_seen
                or bool(
                    belief_about.known_modifiers
                )
            )
            self.assertTrue(
                world.prerequisite_ok(
                    "confront",
                    detective,
                    target,
                )
            )

        observe_target(
            suspect_a,
            first_turn=3,
            day=1,
        )

        result, details, markers = (
            verb_engine.execute(
                detective,
                Action(
                    "confront",
                    ("容疑者甲", "culprit"),
                ),
                turn=5,
                day=2,
            )
        )

        self.assertEqual(result, "misjudged")
        self.assertFalse(details["correct"])
        self.assertEqual(
            [marker["verb"] for marker in markers],
            ["misjudged"],
        )

        detective.knowledge.add(
            "evidence_04_first_alibi"
        )
        detective.apply_evidence(
            "evidence_04_first_alibi",
            world,
        )

        rethink_actions = [
            action
            for action, _ in candidates(
                detective,
                world,
                SimpleNamespace(
                    turn=9,
                    day=3,
                    _last_fact_turn={
                        detective.id: 5
                    },
                ),
            )
            if action.verb == "rethink"
        ]
        self.assertEqual(
            len(rethink_actions),
            1,
        )

        result, details, markers = (
            verb_engine.execute(
                detective,
                rethink_actions[0],
                turn=9,
                day=3,
            )
        )

        self.assertEqual(result, "rethought")
        self.assertEqual(
            details["before"]["culprit"]["value"],
            "容疑者甲",
        )
        self.assertEqual(
            details["after"]["culprit"]["value"],
            "容疑者丙",
        )
        self.assertEqual(
            details["after"]["weapon"]["value"],
            "燭台",
        )
        self.assertEqual(
            [marker["verb"] for marker in markers],
            ["rethink"],
        )

        suspect_c.zone = detective.zone
        observe_target(
            suspect_c,
            first_turn=10,
            day=3,
        )

        result, details, _ = verb_engine.execute(
            detective,
            Action(
                "confront",
                ("容疑者丙", "culprit"),
            ),
            turn=12,
            day=3,
        )

        self.assertEqual(result, "exposed")
        self.assertTrue(details["correct"])
        self.assertIn(
            ("探偵", "culprit"),
            world.confront_successes,
        )

        ending = world.target_endings()[0]
        self.assertTrue(
            world.ending_reached(
                ending,
                detective,
                world.present_subjects(
                    detective.zone
                ),
                turn=12,
                day=3,
            )
        )


if __name__ == "__main__":
    unittest.main()
