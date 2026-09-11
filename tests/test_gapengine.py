"""D2 acceptance tests 6–10 from the Phase 0 implementation plan."""

from __future__ import annotations

import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from engine.actions import Action, candidates
from engine.sim import Simulation
from engine.subject import BeliefAbout, Subject
from engine.verbs import VerbEngine
from engine.world import World
from gapengine.classify import classify
from gapengine.evolve import _prune_layers, evolve
from gapengine.genome import CATEGORIES, Genome
from gapengine.policy import Policy
from gapengine.precedent import PrecedentTable
from gapengine.qd import (
    Archive,
    Descriptor,
    Elite,
    _completed_prerequisite_chains,
    quality,
)
from scripts.evolve import build_parser as build_evolve_parser
from scripts.random_baseline import (
    build_parser as build_baseline_parser,
    main as baseline_main,
)


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


def make_reaching_project(root: Path) -> Path:
    project = root / "project"
    shutil.copytree(PROJECT, project)

    world_path = project / "world.yaml"
    world_raw = yaml.safe_load(world_path.read_text(encoding="utf-8"))
    world_raw["time"] = {"days": 1, "slots": ["朝"]}
    world_raw["daily_events"] = None
    world_raw["scheduled_events"] = [
        {
            "id": "test_guaranteed_homecoming",
            "day": 1,
            "slot": "朝",
            "targets": ["桃太郎"],
            "label": "決定性テスト用の帰還",
            "grants_item": {"name": "鬼ヶ島の宝物", "count": 1},
            "move_to": "村",
        }
    ]
    world_path.write_text(
        yaml.safe_dump(
            world_raw,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )

    momotaro_path = project / "subjects" / "03_momotaro.yaml"
    momotaro_raw = yaml.safe_load(
        momotaro_path.read_text(encoding="utf-8")
    )
    # train is an effective, categorised (I) decision. rest is categorised (V)
    # but never effective (it changes no layer), so a rest-only run has no
    # countable decision, descriptor() returns category=None and the archive
    # rejects every run (Claude-side fix).
    momotaro_raw["verbs"] = ["train", "rest"]
    momotaro_path.write_text(
        yaml.safe_dump(
            momotaro_raw,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return project


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
            action_cfg = yaml.safe_load(
                (TEMPLATE / "action_graph.yaml").read_text(
                    encoding="utf-8"
                )
            )

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
                        cfg=action_cfg,
                    )
                },
            ).run()

            plain_rows = read_rows(plain_path)
            neutral_rows = read_rows(neutral_path)
            self.assertEqual(
                without_policy_fields(plain_rows),
                without_policy_fields(neutral_rows),
            )

            protagonist_decisions = [
                row
                for row in neutral_rows
                if row.get("kind") == "decision"
                and row.get("subject") == world_neutral.protagonist
            ]
            self.assertTrue(protagonist_decisions)
            self.assertTrue(
                all(
                    row["classification"] is not None
                    and row["policy"] is not None
                    and row["policy"]["m_cat"] == 1.0
                    and row["policy"]["m_risk"] == 1.0
                    and row["policy"]["m_stance"] == 1.0
                    and row["policy"]["m_nov"] == 1.0
                    for row in protagonist_decisions
                )
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
            project = make_reaching_project(root)
            common = {
                "ga_seed": 1,
                "generations": 2,
                "keep": "all",
                "population": 6,
                "project": project,
                "seed_base": 11,
                "seeds": 1,
                "template": TEMPLATE,
            }

            serial = evolve(
                {
                    **common,
                    "out": root / "serial",
                    "processes": 1,
                }
            )
            parallel = evolve(
                {
                    **common,
                    "out": root / "parallel",
                    "processes": 2,
                }
            )

            self.assertGreaterEqual(len(serial.cells), 1)
            self.assertGreaterEqual(len(parallel.cells), 1)
            self.assertEqual(
                (root / "serial" / "archive.json").read_bytes(),
                (root / "parallel" / "archive.json").read_bytes(),
            )
            self.assertEqual(
                (root / "serial" / "summary.json").read_bytes(),
                (root / "parallel" / "summary.json").read_bytes(),
            )
            for generation in range(2):
                self.assertEqual(
                    (
                        root
                        / "serial"
                        / f"g{generation}"
                        / "results.json"
                    ).read_bytes(),
                    (
                        root
                        / "parallel"
                        / f"g{generation}"
                        / "results.json"
                    ).read_bytes(),
                )


class Phase1GapEngineTests(unittest.TestCase):
    def test_phase1_classification_and_meta_priority(self) -> None:
        world, subjects = load_fixture()
        cfg = yaml.safe_load(
            (TEMPLATE / "action_graph.yaml").read_text(
                encoding="utf-8"
            )
        )
        actor = subjects["桃太郎"]
        actor.zone = "鬼ヶ島"
        subjects["鬼"].zone = "鬼ヶ島"
        present = world.present_subjects("鬼ヶ島")

        cases = [
            (
                Action(
                    "sabotage",
                    ("鬼",),
                    {"target": "鬼"},
                ),
                ("I", "weaken_direct", "risky", -1),
            ),
            (
                Action("sacrifice", ("asset",)),
                ("I", "sacrifice", "risky", 0),
            ),
            (
                Action(
                    "mislead",
                    ("鬼", "桃太郎", 30.0),
                    {"target": "鬼"},
                ),
                ("II", "mislead", "neutral", -1),
            ),
            (
                Action(
                    "confront",
                    ("鬼", "oni_weakness"),
                    {"target": "鬼"},
                ),
                ("II", "exposure", "risky", -1),
            ),
            (
                Action(
                    "negotiate",
                    ("鬼",),
                    {"target": "鬼"},
                ),
                ("III", "negotiate", "neutral", 1),
            ),
            (
                Action(
                    "concede",
                    ("桃太郎",),
                    {"target": "桃太郎"},
                ),
                ("VI", "reward", "neutral", 1),
            ),
            (
                Action(
                    "persuade",
                    ("鬼",),
                    {"target": "鬼"},
                ),
                ("III", "persuade", "neutral", 1),
            ),
            (
                Action(
                    "pledge",
                    ("鬼",),
                    {"target": "鬼"},
                ),
                ("III", "pledge", "neutral", 1),
            ),
        ]

        for action, expected in cases:
            classified = classify(
                action,
                actor,
                world,
                present,
                cfg,
            )
            self.assertEqual(
                (
                    classified.category,
                    classified.subtype,
                    classified.risk_class,
                    classified.stance_sign,
                ),
                expected,
            )

        betrayal = classify(
            Action(
                "fight",
                ("鬼",),
                {
                    "target": "鬼",
                    "betrayal": True,
                    "subtype": "betray",
                    "under_threat": False,
                },
            ),
            actor,
            world,
            present,
            cfg,
        )
        self.assertEqual(betrayal.subtype, "betray")

        safe = classify(
            Action("rest", meta={"under_threat": False}),
            actor,
            world,
            present,
            cfg,
        )
        self.assertEqual(safe.risk_class, "neutral")

    def test_action_graph_prerequisite_and_permission_deny(
        self,
    ) -> None:
        world, subjects = load_fixture()
        actor = subjects["桃太郎"]
        target = subjects["鬼"]
        actor.zone = "鬼ヶ島"
        actor.verbs = {"neutralize"}

        before = candidates(
            actor,
            world,
            SimpleNamespace(day=1, turn=1),
        )
        self.assertFalse(
            any(action.verb == "neutralize" for action, _ in before)
        )

        actor.beliefs_about.setdefault(
            "鬼",
            BeliefAbout(
                base_estimate=world.default_strength_prior
            ),
        ).known_modifiers.add("金棒")
        allowed = candidates(
            actor,
            world,
            SimpleNamespace(day=1, turn=1),
        )
        self.assertTrue(
            any(
                action.verb == "neutralize"
                and action.args == ("鬼", "金棒")
                for action, _ in allowed
            )
        )

        world.relations.change(
            actor.id,
            target.id,
            affinity=2.0,
        )
        self.assertEqual(
            world.target_role(actor, target),
            "ally",
        )
        self.assertEqual(
            world.permission("neutralize", "ally"),
            0.0,
        )
        denied = candidates(
            actor,
            world,
            SimpleNamespace(day=1, turn=1),
        )
        self.assertFalse(
            any(action.verb == "neutralize" for action, _ in denied)
        )

    def test_completed_chains_and_dramatic_turns_raise_quality(
        self,
    ) -> None:
        base_rows: list[dict[str, Any]] = [
            {
                "kind": "header",
                "protagonist": "桃太郎",
            },
            {
                "kind": "decision",
                "subject": "桃太郎",
                "verb": "observe",
                "args": ["鬼"],
                "result": "observed",
                "delta": {},
                "details": {},
            },
            {
                "kind": "decision",
                "subject": "桃太郎",
                "verb": "pledge",
                "args": ["犬"],
                "result": "pledged",
                "delta": {},
                "details": {},
            },
            {
                "kind": "decision",
                "subject": "桃太郎",
                "verb": "negotiate",
                "args": ["鬼"],
                "result": "offered",
                "delta": {},
                "details": {},
            },
        ]
        completed_rows = [
            *base_rows,
            {
                "kind": "decision",
                "subject": "桃太郎",
                "verb": "neutralize",
                "args": ["鬼", "金棒"],
                "result": "neutralized",
                "delta": {},
                "details": {},
            },
            {
                "kind": "event",
                "subject": "桃太郎",
                "verb": "betrayal",
                "args": [],
                "result": "applied",
                "delta": {},
                "details": {"target": "犬"},
            },
            {
                "kind": "decision",
                "subject": "鬼",
                "verb": "concede",
                "args": ["桃太郎"],
                "result": "conceded",
                "delta": {
                    "objective": {
                        "鬼ヶ島の宝物": "桃太郎",
                    }
                },
                "details": {"mode": "goodwill"},
            },
        ]

        self.assertEqual(
            _completed_prerequisite_chains(completed_rows),
            3,
        )
        meta = {"protagonist": "桃太郎"}
        self.assertGreater(
            quality(completed_rows, meta),
            quality(base_rows, meta),
        )

    def _concede_details(self, trade: bool) -> dict[str, Any]:
        world, subjects = load_fixture()
        claimant = subjects["桃太郎"]
        holder = subjects["鬼"]
        claimant.zone = "鬼ヶ島"

        if trade:
            holder.remove_item("金棒", 1)
            claimant.add_item("金棒", 1)
            # keep 金棒 as the only tradeable asset (勾玉 added in D3c)
            if claimant.has_item("勾玉"):
                claimant.remove_item("勾玉", claimant.inventory.get("勾玉", 0))
        else:
            # 勾玉 (D3c fixture) would make the offer a trade; drop it so the
            # goodwill path is exercised (Claude-side test fix).
            if claimant.has_item("勾玉"):
                claimant.remove_item("勾玉", claimant.inventory.get("勾玉", 0))
            world.relations.change(
                holder.id,
                claimant.id,
                affinity=1.0,
            )

        engine = VerbEngine(world, random.Random(1))
        result, _, _ = engine.execute(
            claimant,
            Action("negotiate", (holder.id,)),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "offered")

        concede = next(
            action
            for action, _ in candidates(
                holder,
                world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "concede"
        )
        result, details, _ = engine.execute(
            holder,
            concede,
            turn=2,
            day=1,
        )
        self.assertEqual(result, "conceded")
        return details

    def test_concede_goodwill_and_trade_modes(self) -> None:
        goodwill = self._concede_details(trade=False)
        trade = self._concede_details(trade=True)

        self.assertEqual(goodwill["mode"], "goodwill")
        self.assertEqual(goodwill["assets"], {})
        self.assertEqual(trade["mode"], "trade")
        self.assertEqual(trade["assets"], {"金棒": 1})

    def test_betrayal_reduces_reputation(self) -> None:
        world, subjects = load_fixture()
        actor = subjects["桃太郎"]
        target = subjects["犬"]
        actor.zone = "道中"
        target.zone = "道中"
        world.relations.change(
            actor.id,
            target.id,
            affinity=2.0,
        )
        world.relations.change(
            target.id,
            actor.id,
            affinity=2.0,
        )
        actor.beliefs_about.setdefault(
            target.id,
            BeliefAbout(
                base_estimate=world.default_strength_prior
            ),
        ).identity_seen = True

        engine = VerbEngine(world, random.Random(1))
        result, _, _ = engine.execute(
            actor,
            Action("pledge", (target.id,)),
            turn=1,
            day=1,
        )
        self.assertEqual(result, "pledged")

        sabotage = next(
            action
            for action, _ in candidates(
                actor,
                world,
                SimpleNamespace(day=1, turn=2),
            )
            if action.verb == "sabotage"
            and action.meta.get("target") == target.id
        )
        action_cfg = yaml.safe_load(
            (TEMPLATE / "action_graph.yaml").read_text(
                encoding="utf-8"
            )
        )
        classification = classify(
            sabotage,
            actor,
            world,
            world.present_subjects(actor.zone),
            action_cfg,
        )
        self.assertTrue(sabotage.meta["betrayal"])
        self.assertEqual(
            classification.subtype,
            "betray",
        )

        before = actor.reputation
        result, _, markers = engine.execute(
            actor,
            sabotage,
            turn=2,
            day=1,
        )
        self.assertEqual(result, "sabotaged")
        self.assertEqual(actor.reputation, before - 0.5)
        self.assertEqual(
            [marker["verb"] for marker in markers],
            ["betrayal"],
        )

    def test_neutral_rules_do_not_force_annotation_only(self) -> None:
        world, subjects = load_fixture()
        actor = subjects["桃太郎"]
        present = world.present_subjects(actor.zone)

        plain_weighted = [(Action("train"), 1.0)]
        plain_policy = Policy(
            Genome.neutral(),
            precedent=None,
            cfg={"nodes": [], "edges": []},
        )
        self.assertIs(
            plain_policy.reweight(
                actor,
                world,
                present,
                plain_weighted,
            ),
            plain_weighted,
        )

        ruled_weighted = [(Action("train"), 1.0)]
        ruled_policy = Policy(
            Genome.neutral(),
            precedent=None,
            rules=({"id": "future-rule"},),
            cfg={"nodes": [], "edges": []},
        )
        ruled_output = ruled_policy.reweight(
            actor,
            world,
            present,
            ruled_weighted,
        )
        self.assertIsNot(ruled_output, ruled_weighted)
        self.assertIn(
            "classification",
            ruled_output[0][0].meta,
        )

    def test_keep_reached_retains_shaped_best_on_zero_reach(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary)
            results: list[dict[str, Any]] = []
            for index, shaped_value in ((0, 0.2), (1, 0.8)):
                runs: list[dict[str, Any]] = []
                for seed in (3, 4):
                    relative = (
                        f"g0/ind-{index}/seed-{seed}/layers.jsonl"
                    )
                    layer_path = out_dir / relative
                    layer_path.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )
                    layer_path.write_text(
                        "{}\n",
                        encoding="utf-8",
                    )
                    runs.append(
                        {
                            "layers_path": relative,
                            "reached": False,
                        }
                    )
                results.append(
                    {
                        "index": index,
                        "runs": runs,
                        "shaped": shaped_value,
                    }
                )

            _prune_layers(results, out_dir, "reached")

            for seed in (3, 4):
                self.assertTrue(
                    (
                        out_dir
                        / f"g0/ind-1/seed-{seed}/layers.jsonl"
                    ).is_file()
                )
                self.assertFalse(
                    (out_dir / f"g0/ind-0/seed-{seed}").exists()
                )

    def test_seed_base_parsers_reject_negative_values(self) -> None:
        required = [
            "--project",
            str(PROJECT),
            "--template",
            str(TEMPLATE),
            "--out",
            "unused",
            "--seed-base",
            "-1",
        ]
        with self.assertRaises(SystemExit):
            build_evolve_parser().parse_args(required)
        with self.assertRaises(SystemExit):
            build_baseline_parser().parse_args(required)

    def test_random_baseline_reports_all_distribution_and_seed_base(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = baseline_main(
                [
                    "--project",
                    str(PROJECT),
                    "--template",
                    str(TEMPLATE),
                    "--out",
                    str(output),
                    "--seeds",
                    "1",
                    "--seed-base",
                    "7",
                ]
            )
            self.assertEqual(result, 0)

            summary = json.loads(
                (output / "summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["seed_base"], 7)
            self.assertEqual(summary["runs"][0]["seed"], 7)
            self.assertEqual(
                sum(
                    summary[
                        "all_descriptor_distribution"
                    ].values()
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
