"""D2 acceptance tests 6–10 from the Phase 0 implementation plan."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from engine.actions import Action
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine.classify import classify
from gapengine.evolve import evolve
from gapengine.genome import CATEGORIES, Genome
from gapengine.policy import Policy
from gapengine.precedent import PrecedentTable
from gapengine.qd import Archive, Descriptor, Elite


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "momotaro"
TEMPLATE = ROOT / "templates" / "momotaro"


def load_fixture() -> tuple[World, dict[str, Subject]]:
    world = World.from_yaml(PROJECT / "world.yaml")
    subjects = [
        Subject.from_yaml(path)
        for path in sorted(
            (PROJECT / "subjects").glob("*.yaml"),
            key=lambda value: value.name,
        )
    ]
    values = {subject.id: subject for subject in subjects}
    world.bind_subjects(values)
    return world, values


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def without_policy_fields(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized = json.loads(
        json.dumps(rows, ensure_ascii=False, sort_keys=True)
    )
    for row in normalized:
        if row.get("kind") == "header":
            row.pop("genome", None)
        row.pop("policy", None)
        row.pop("classification", None)
    return normalized


class GapEngineTests(unittest.TestCase):
    def test_neutral_genome_matches_policy_none(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)

            world_plain, subjects_plain = load_fixture()
            world_plain.days = 2
            plain_path = Simulation(
                153,
                world_plain,
                subjects_plain,
                output / "plain",
                policies=None,
            ).run()

            world_neutral, subjects_neutral = load_fixture()
            world_neutral.days = 2
            neutral_path = Simulation(
                153,
                world_neutral,
                subjects_neutral,
                output / "neutral",
                policies={
                    world_neutral.protagonist: Policy(
                        Genome.neutral(),
                        None,
                    )
                },
            ).run()

            self.assertEqual(
                without_policy_fields(read_rows(plain_path)),
                without_policy_fields(read_rows(neutral_path)),
            )

    def test_genome_operations_stay_in_range(self) -> None:
        rng = random.Random(7)
        first = Genome.random(rng)
        second = Genome.random(rng)
        child = Genome.crossover(first, second, rng)
        mutated = Genome.mutate(child, rng, p=1.0)

        for genome in (first, second, child, mutated):
            self.assertEqual(set(genome.category_weight), set(CATEGORIES))
            self.assertTrue(
                all(
                    0.05 <= value <= 1.0
                    for value in genome.category_weight.values()
                )
            )
            self.assertTrue(0.0 <= genome.risk_tolerance <= 1.0)
            self.assertTrue(-1.0 <= genome.stance_shift_bias <= 1.0)
            self.assertTrue(0.0 <= genome.novelty_drive <= 1.0)

        self.assertTrue(Genome.neutral().is_neutral())
        self.assertFalse(first.is_neutral())
        self.assertEqual(
            Genome.from_dict(mutated.to_dict()).to_dict(),
            mutated.to_dict(),
        )

    def test_classification_table(self) -> None:
        world, subjects = load_fixture()
        cfg = yaml.safe_load(
            (TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8")
        )
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        dog = subjects["犬"]
        momotaro.zone = "鬼ヶ島"
        oni.zone = "鬼ヶ島"
        dog.zone = "鬼ヶ島"
        present = world.present_subjects("鬼ヶ島")

        train = classify(Action("train"), momotaro, world, present, cfg)
        self.assertEqual(
            (train.category, train.subtype),
            ("I", "self_strengthen"),
        )

        fight = classify(
            Action(
                "fight",
                ("鬼",),
                {"target": "鬼", "outmatched": True},
            ),
            momotaro,
            world,
            present,
            cfg,
        )
        self.assertEqual(fight.category, "I")
        self.assertEqual(fight.target_role, "hostile")
        self.assertEqual(fight.risk_class, "risky")

        gift = classify(
            Action(
                "give_item",
                ("犬", "きびだんご"),
                {"target": "犬", "item": "きびだんご"},
            ),
            momotaro,
            world,
            present,
            cfg,
        )
        self.assertEqual(gift.category, "III")
        self.assertEqual(gift.target_role, "neutral")

        crossing = classify(
            Action(
                "move",
                ("鬼ヶ島",),
                {"dest": "鬼ヶ島", "crossing": True},
            ),
            momotaro,
            world,
            present,
            cfg,
        )
        self.assertEqual(
            (crossing.category, crossing.subtype),
            ("V", "crossing"),
        )

        resting = classify(
            Action("rest"),
            momotaro,
            world,
            present,
            cfg,
        )
        self.assertEqual(resting.risk_class, "safe_under_threat")

        neutralize = classify(
            Action(
                "neutralize",
                ("鬼", "item:金棒"),
                {"target": "鬼", "source": "item:金棒"},
            ),
            momotaro,
            world,
            present,
            cfg,
        )
        self.assertEqual(
            (
                neutralize.category,
                neutralize.subtype,
                neutralize.stance_sign,
            ),
            ("I", "weaken_indirect", -1),
        )

    def test_precedent_probability_and_json_round_trip(self) -> None:
        context = (("出発",), False, "hostile", "alive", "hostile")
        first = ("I", "fight", "hostile")
        second = ("III", "give_item", "neutral")
        candidates = {first, second}

        table = PrecedentTable()
        self.assertEqual(
            table.p(context, first, candidates),
            table.p(context, second, candidates),
        )

        table.add(context, first, 4)
        self.assertGreater(
            table.p(context, first, candidates),
            table.p(context, second, candidates),
        )

        restored = PrecedentTable.from_json(table.to_json())
        self.assertEqual(restored.hash, table.hash)
        self.assertEqual(restored.to_json(), table.to_json())

    def test_archive_tie_break_and_round_trip(self) -> None:
        archive = Archive()
        archive.freeze_thresholds([0.0, 0.2, 0.8])
        descriptor = Descriptor("I", 0.1, archive.bin_for(0.1))
        genome = Genome.neutral()

        first = Elite(
            genome=genome,
            quality=0.5,
            descriptor=descriptor,
            reach_rate=0.5,
            exemplar={
                "engine_hash": "engine",
                "layers_path": "g0/ind-0/seed-0/layers.jsonl",
                "precedent_hash": "precedent",
                "seed": 0,
            },
            generation=0,
        )
        better_reach = Elite(
            genome=genome,
            quality=0.5,
            descriptor=descriptor,
            reach_rate=1.0,
            exemplar={
                "engine_hash": "engine",
                "layers_path": "g0/ind-1/seed-0/layers.jsonl",
                "precedent_hash": "precedent",
                "seed": 0,
            },
            generation=0,
        )
        same = Elite(
            genome=genome,
            quality=0.5,
            descriptor=descriptor,
            reach_rate=1.0,
            exemplar={
                "engine_hash": "engine",
                "layers_path": "g0/ind-2/seed-0/layers.jsonl",
                "precedent_hash": "precedent",
                "seed": 0,
            },
            generation=0,
        )

        self.assertTrue(archive.insert(first))
        self.assertTrue(archive.insert(better_reach))
        self.assertFalse(archive.insert(same))
        self.assertEqual(
            archive.cells[("I", descriptor.volatility_bin)].exemplar[
                "layers_path"
            ],
            "g0/ind-1/seed-0/layers.jsonl",
        )

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "archive.json"
            archive.save(path)
            before = path.read_bytes()
            restored = Archive.load(path)
            restored.save(path)
            self.assertEqual(before, path.read_bytes())

    def test_small_evolve_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            common = {
                "ga_seed": 1,
                "generations": 2,
                "population": 6,
                "processes": 1,
                "project": PROJECT,
                "seeds": 1,
                "template": TEMPLATE,
            }

            evolve({**common, "out": root / "first"})
            evolve({**common, "out": root / "second"})

            self.assertEqual(
                (root / "first" / "archive.json").read_bytes(),
                (root / "second" / "archive.json").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
