from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from engine.subject import Subject
from engine.world import World
from gapengine.evolve import evolve
from gapengine.qd import read_rows, reached


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "romance"
TEMPLATE = ROOT / "templates" / "romance"


def load_subjects(directory: Path) -> dict[str, Subject]:
    subjects = [
        Subject.from_yaml(path)
        for path in sorted(
            directory.glob("*.yaml"),
            key=lambda value: value.name,
        )
    ]
    return {subject.id: subject for subject in subjects}


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class RomanceTemplateTests(unittest.TestCase):
    def test_world_and_five_subjects_load_without_objectives(
        self,
    ) -> None:
        world = World.from_yaml(
            PROJECT / "world.yaml",
            action_graph_path=(
                TEMPLATE / "action_graph.yaml"
            ),
        )
        subjects = load_subjects(
            PROJECT / "subjects"
        )
        self.assertEqual(
            set(subjects),
            {"A", "B", "C", "D", "E"},
        )

        world.bind_subjects(subjects)

        self.assertEqual(world.protagonist, "A")
        self.assertEqual(world.antagonist, "C")
        self.assertEqual(
            world.target_ending,
            "mutual",
        )
        self.assertEqual(
            tuple(
                ending["id"]
                for ending in world.target_endings()
            ),
            ("mutual",),
        )
        self.assertEqual(world.objectives, {})
        self.assertEqual(
            world.genres,
            frozenset({"romance"}),
        )
        self.assertFalse(
            world.genre_allows("fight")
        )
        self.assertFalse(
            world.genre_allows("sabotage")
        )
        self.assertEqual(
            set(world.effect_library),
            {
                "old_promise",
                "rival_rumor_exposed",
            },
        )

        defense = {
            modifier.source: modifier
            for modifier in subjects["B"].modifiers
        }["防衛"]
        self.assertFalse(defense.visible)
        self.assertTrue(defense.active)
        self.assertFalse(defense.lethal)
        self.assertEqual(
            defense.affinity_cap,
            0.35,
        )
        self.assertEqual(
            defense.affinity_cap_targets,
            ("A", "C"),
        )

        self.assertEqual(
            world.relations.stance("B", "A"),
            0.25,
        )
        world.relations.change(
            "B",
            "A",
            affinity=1.0,
        )
        self.assertEqual(
            world.relations.stance("B", "A"),
            0.35,
        )

        rumor = {
            modifier.source: modifier
            for modifier in subjects["C"].modifiers
        }["噂"]
        self.assertFalse(rumor.visible)
        self.assertTrue(rumor.active)
        self.assertFalse(rumor.lethal)
        self.assertIsNone(rumor.affinity_cap)

        self.assertIsNone(
            subjects["A"].goal.target
        )
        self.assertIsNone(
            subjects["C"].goal.target
        )
        self.assertEqual(
            subjects["A"].goal.outcome,
            "mutual",
        )
        self.assertEqual(
            subjects["C"].goal.outcome,
            "mutual",
        )
        self.assertTrue(
            subjects["A"].objective_claimant
        )
        self.assertTrue(
            subjects["C"].objective_claimant
        )

    def test_small_evolve_fixture_can_reach_mutual(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture_project = root / "romance"
            fixture_subjects = (
                fixture_project / "subjects"
            )
            fixture_subjects.mkdir(parents=True)

            world_definition = load_yaml(
                PROJECT / "world.yaml"
            )
            world_definition["time"] = {
                "days": 1,
                "slots": ["放課後"],
            }
            (
                fixture_project / "world.yaml"
            ).write_text(
                yaml.safe_dump(
                    world_definition,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
                newline="\n",
            )

            for source in sorted(
                (
                    PROJECT / "subjects"
                ).glob("*.yaml"),
                key=lambda value: value.name,
            ):
                definition = load_yaml(source)
                subject_id = str(definition["id"])

                if subject_id == "A":
                    definition["verbs"] = [
                        "persuade"
                    ]
                    definition["relations"]["B"] = {
                        "affinity": 0.59,
                        "awareness": 1.0,
                    }
                    definition["range"] = {
                        "zones": ["学校"],
                        "entry": "学校",
                    }
                elif subject_id == "B":
                    definition["verbs"] = ["rest"]
                    definition["relations"]["A"] = {
                        "affinity": 0.59,
                        "awareness": 1.0,
                    }
                    definition["range"] = {
                        "zones": ["学校"],
                        "entry": "学校",
                    }
                    for modifier in (
                        definition.get(
                            "modifiers",
                            [],
                        )
                        or []
                    ):
                        if (
                            modifier.get("source")
                            == "防衛"
                        ):
                            modifier["active"] = False
                else:
                    definition["verbs"] = ["rest"]
                    definition["range"] = {
                        "zones": ["Bの家"],
                        "entry": "Bの家",
                    }

                (
                    fixture_subjects / source.name
                ).write_text(
                    yaml.safe_dump(
                        definition,
                        allow_unicode=True,
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                    newline="\n",
                )

            output = root / "evolve"
            archive = evolve(
                {
                    "ga_seed": 11,
                    "generations": 1,
                    "keep": "all",
                    "out": output,
                    "population": 2,
                    "processes": 1,
                    "project": fixture_project,
                    "seed_base": 7,
                    "seeds": 1,
                    "template": TEMPLATE,
                    "target_ending": "mutual",
                }
            )

            self.assertGreaterEqual(
                len(archive.cells),
                1,
            )
            reached_elites = []
            for elite in archive.cells.values():
                rows = read_rows(
                    output
                    / str(
                        elite.exemplar[
                            "layers_path"
                        ]
                    )
                )
                if reached(rows, "mutual"):
                    reached_elites.append(elite)

            self.assertTrue(reached_elites)
            self.assertTrue(
                (
                    output / "archive.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    output / "summary.json"
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
