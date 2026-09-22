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
from gapengine.evolve import (
    _generation_lineage_summary,
    _lineage_stats,
    _prune_layers,
    evolve,
)
from gapengine.genome import CATEGORIES, Genome
from gapengine.policy import Policy
from gapengine.precedent import PrecedentTable, ctx_key
from gapengine.qd import (
    Archive,
    Descriptor,
    Elite,
    _completed_prerequisite_chains,
    antagonist_quality,
    quality,
    reached,
    shaped,
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
            rules=(
                {
                    "id": "future-rule",
                    "scope": "turn",
                    "when": "False",
                    "adjust": {},
                },
            ),
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


class Phase2GapEngineTests(unittest.TestCase):
    def test_phase2_classification_table(self) -> None:
        world, subjects = load_fixture()
        cfg = yaml.safe_load(
            (TEMPLATE / "action_graph.yaml").read_text(
                encoding="utf-8"
            )
        )
        actor = subjects["桃太郎"]
        actor.zone = "鬼ヶ島"
        subjects["鬼"].zone = "鬼ヶ島"
        present = world.present_subjects(actor.zone)

        cases = [
            (
                Action(
                    "plant",
                    ("village_promise",),
                    {
                        "effect_id": "village_promise",
                        "target": actor.id,
                    },
                ),
                ("II", "plant", "neutral", 0),
            ),
            (
                Action(
                    "payoff",
                    ("oni_gap",),
                    {
                        "effect_id": "oni_gap:鬼",
                        "target": "鬼",
                    },
                ),
                ("II", "payoff", "neutral", 0),
            ),
            (
                Action(
                    "disguise",
                    ("旅の商人",),
                    {
                        "disguise_id": "momotaro_merchant",
                        "displayed": "旅の商人",
                    },
                ),
                ("IV", "disguise", "neutral", 0),
            ),
            (
                Action(
                    "grand_gesture",
                    ("鬼",),
                    {"target": "鬼"},
                ),
                ("III", "grand_gesture", "risky", 1),
            ),
            (
                Action(
                    "trial",
                    ("おじいさん",),
                    {"target": "おじいさん"},
                ),
                ("III", "trial", "neutral", 0),
            ),
            (
                Action(
                    "donate",
                    ("鬼ヶ島の宝物",),
                    {
                        "item": "鬼ヶ島の宝物",
                        "zone": "村",
                    },
                ),
                ("VI", "donate", "neutral", 1),
            ),
        ]

        for action, expected in cases:
            classification = classify(
                action,
                actor,
                world,
                present,
                cfg,
            )
            self.assertEqual(
                (
                    classification.category,
                    classification.subtype,
                    classification.risk_class,
                    classification.stance_sign,
                ),
                expected,
            )

    def test_rules_record_turn_and_candidate_effective_genome(
        self,
    ) -> None:
        world, subjects = load_fixture()
        actor = subjects["桃太郎"]
        target = subjects["鬼"]
        actor.zone = "鬼ヶ島"
        target.zone = "鬼ヶ島"
        actor.phase.add("越境")

        cfg = yaml.safe_load(
            (TEMPLATE / "action_graph.yaml").read_text(
                encoding="utf-8"
            )
        )
        rules = yaml.safe_load(
            (TEMPLATE / "rules.yaml").read_text(
                encoding="utf-8"
            )
        )
        policy = Policy(
            Genome.neutral(),
            precedent=None,
            rules=rules,
            cfg=cfg,
        )
        train = Action("train")
        fight = Action(
            "fight",
            (target.id,),
            {
                "target": target.id,
                "under_threat": True,
            },
        )

        output = policy.reweight(
            actor,
            world,
            world.present_subjects(actor.zone),
            [(train, 1.0), (fight, 1.0)],
            turn=7,
            day=2,
        )
        by_verb = {
            action.verb: action.meta["policy"]["effective_genome"]
            for action, _ in output
        }

        self.assertEqual(
            by_verb["train"]["risk_tolerance"],
            0.7,
        )
        self.assertEqual(
            by_verb["train"]["category_weight"]["I"],
            0.5,
        )
        self.assertAlmostEqual(  # float sum of adjustments (Claude-side test fix)
            by_verb["fight"]["risk_tolerance"],
            0.8,
        )
        self.assertEqual(
            by_verb["fight"]["category_weight"]["I"],
            0.7,
        )

        ally = subjects["犬"]
        world.relations.change(
            actor.id,
            ally.id,
            affinity=1.0,
        )
        ally.vitality = "downed"
        ruled = Action("persuade", (target.id,), {"target": target.id})
        policy.reweight(
            actor,
            world,
            world.present_subjects(actor.zone),
            [(ruled, 1.0)],
            turn=8,
            day=2,
        )
        effective = ruled.meta["policy"]["effective_genome"]
        self.assertEqual(
            effective["category_weight"]["III"],
            0.8,
        )

    def test_precedent_context_records_disguise_and_reads_old_keys(
        self,
    ) -> None:
        old_context = (
            ("出発",),
            False,
            "hostile",
            "alive",
            "hostile",
        )
        action = ("I", "fight", "hostile")
        old_table = PrecedentTable()
        old_table.add(old_context, action, 4)

        restored = PrecedentTable.from_json(old_table.to_json())
        normalized_old = (
            ("出発",),
            False,
            "hostile",
            "alive",
            "hostile",
            False,
        )
        self.assertIn(normalized_old, restored.counts)

        world, subjects = load_fixture()
        actor = subjects["桃太郎"]
        present = world.present_subjects(actor.zone)
        visible_context = ctx_key(actor, world, present)
        self.assertFalse(visible_context[5])

        actor.identity_displayed = "旅の商人"
        disguised_context = ctx_key(actor, world, present)
        self.assertTrue(disguised_context[5])
        self.assertNotEqual(visible_context, disguised_context)

    def test_quality_penalizes_dangling_and_rewards_phase2_turns(
        self,
    ) -> None:
        shared = [
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
                "delta": {
                    "objective": {
                        "鬼ヶ島の宝物": "鬼",
                        "きびだんご": "犬",
                    }
                },
                "details": {},
            },
        ]
        settled = [
            *shared,
            {
                "kind": "event",
                "subject": "桃太郎",
                "verb": "ending",
                "details": {"dangling_effects": 0},
            },
        ]
        dangling = [
            *shared,
            {
                "kind": "event",
                "subject": "桃太郎",
                "verb": "ending",
                "details": {"dangling_effects": 1},
            },
        ]
        dramatic = [
            *shared,
            {
                "kind": "event",
                "subject": "鬼",
                "verb": "exposure",
                "details": {},
            },
            {
                "kind": "event",
                "subject": "桃太郎",
                "verb": "payoff",
                "details": {"mode": "chosen"},
            },
            {
                "kind": "event",
                "subject": "桃太郎",
                "verb": "ending",
                "details": {"dangling_effects": 0},
            },
        ]
        meta = {"protagonist": "桃太郎"}

        self.assertAlmostEqual(
            quality(settled, meta) - quality(dangling, meta),
            0.05,
        )
        self.assertGreater(
            quality(dramatic, meta),
            quality(settled, meta),
        )

    def test_prerequisite_chains_dedupe_and_use_protagonist(
        self,
    ) -> None:
        rows = [
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
            },
            {
                "kind": "decision",
                "subject": "桃太郎",
                "verb": "neutralize",
                "args": ["鬼", "金棒"],
                "result": "neutralized",
            },
            {
                "kind": "decision",
                "subject": "桃太郎",
                "verb": "neutralize",
                "args": ["鬼", "金棒"],
                "result": "neutralized",
            },
            {
                "kind": "decision",
                "subject": "犬",
                "verb": "observe",
                "args": ["鬼"],
                "result": "observed",
            },
            {
                "kind": "decision",
                "subject": "犬",
                "verb": "neutralize",
                "args": ["鬼", "金棒"],
                "result": "neutralized",
            },
        ]

        self.assertEqual(
            _completed_prerequisite_chains(rows),
            1,
        )


class Phase3GapEngineTests(unittest.TestCase):
    def test_antagonist_quality_is_non_zero_sum_and_reach_gated(
        self,
    ) -> None:
        rows = [
            {
                "kind": "header",
                "protagonist": "桃太郎",
            },
            {
                "kind": "decision",
                "turn": 2,
                "subject": "桃太郎",
                "details": {"strength_diff": -1.0},
            },
            {
                "kind": "decision",
                "turn": 4,
                "subject": "桃太郎",
                "details": {"strength_diff": 1.0},
            },
            {
                "kind": "decision",
                "turn": 5,
                "subject": "桃太郎",
                "details": {"strength_diff": -1.0},
            },
            {
                "kind": "event",
                "turn": 5,
                "verb": "ending",
                "id": "homecoming",
            },
        ]
        meta = {
            "max_turns": 10,
            "protagonist": "桃太郎",
            "target_ending": "homecoming",
            "vol_high": 1.0,
        }
        self.assertEqual(antagonist_quality(rows, meta), 0.5)

        unreachable = [
            row
            for row in rows
            if row.get("verb") != "ending"
        ]
        self.assertEqual(
            antagonist_quality(unreachable, meta),
            0.0,
        )

    def test_coevolve_parser_flag(self) -> None:
        args = build_evolve_parser().parse_args(
            [
                "--project",
                str(PROJECT),
                "--template",
                str(TEMPLATE),
                "--out",
                "unused",
                "--coevolve",
            ]
        )
        self.assertTrue(args.coevolve)

    def test_small_coevolve_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)

            antagonist_path = (
                project / "subjects" / "07_oni.yaml"
            )
            antagonist_raw = yaml.safe_load(
                antagonist_path.read_text(encoding="utf-8")
            )
            antagonist_raw["verbs"] = ["train"]
            antagonist_path.write_text(
                yaml.safe_dump(
                    antagonist_raw,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
                newline="\n",
            )

            common = {
                "coevolve": True,
                "ga_seed": 7,
                "generations": 2,
                "keep": "all",
                "population": 4,
                "project": project,
                "seed_base": 21,
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
            serial_antagonist = Archive.load(
                root / "serial" / "archive_antagonist.json"
            )
            parallel_antagonist = Archive.load(
                root / "parallel" / "archive_antagonist.json"
            )

            self.assertGreaterEqual(len(serial.cells), 1)
            self.assertGreaterEqual(len(parallel.cells), 1)
            self.assertGreaterEqual(
                len(serial_antagonist.cells),
                1,
            )
            self.assertGreaterEqual(
                len(parallel_antagonist.cells),
                1,
            )

            comparable = [
                "archive.json",
                "archive_antagonist.json",
                "summary.json",
            ]
            for relative_path in comparable:
                self.assertEqual(
                    (root / "serial" / relative_path).read_bytes(),
                    (root / "parallel" / relative_path).read_bytes(),
                )

            for generation in range(2):
                for filename in (
                    "population.json",
                    "population.antagonist.json",
                    "precedent.json",
                    "precedent.antagonist.json",
                    "results.json",
                    "results.antagonist.json",
                ):
                    self.assertEqual(
                        (
                            root
                            / "serial"
                            / f"g{generation}"
                            / filename
                        ).read_bytes(),
                        (
                            root
                            / "parallel"
                            / f"g{generation}"
                            / filename
                        ).read_bytes(),
                    )

            antagonist_header = read_rows(
                root
                / "serial"
                / "g0"
                / "antagonist"
                / "ind-0"
                / "seed-21"
                / "layers.jsonl"
            )[0]
            self.assertIsNotNone(antagonist_header["genome"])
            self.assertIsNotNone(
                antagonist_header["antagonist_genome"]
            )

            protagonist_elite = next(iter(serial.cells.values()))
            antagonist_elite = next(
                iter(serial_antagonist.cells.values())
            )
            for elite in (protagonist_elite, antagonist_elite):
                self.assertIn("genome", elite.exemplar)
                self.assertIn(
                    "antagonist_genome",
                    elite.exemplar,
                )


class D6bGapEngineTests(unittest.TestCase):
    def test_variant_endings_and_string_backward_compatibility(
        self,
    ) -> None:
        shared_rows = [
            {
                "kind": "header",
                "protagonist": "桃太郎",
            },
            {
                "kind": "event",
                "verb": "ending",
                "id": "homecoming_shared",
                "turn": 5,
            },
        ]

        self.assertTrue(
            reached(
                shared_rows,
                ["homecoming", "homecoming_shared"],
            )
        )
        self.assertTrue(
            reached(shared_rows, "homecoming_shared")
        )
        self.assertFalse(
            reached(shared_rows, "homecoming")
        )

        world, _ = load_fixture()
        self.assertEqual(
            world.target_ending,
            ("homecoming", "homecoming_shared"),
        )

        raw = yaml.safe_load(
            (PROJECT / "world.yaml").read_text(
                encoding="utf-8"
            )
        )
        raw["target_ending"] = "homecoming"
        legacy_world = World(
            raw,
            PROJECT / "world.yaml",
        )
        self.assertEqual(
            legacy_world.target_ending,
            "homecoming",
        )

        variant_meta = {
            "max_turns": 10,
            "protagonist": "桃太郎",
            "target_ending": [
                "homecoming",
                "homecoming_shared",
            ],
            "vol_high": 1.0,
        }
        self.assertGreater(
            antagonist_quality(shared_rows, variant_meta),
            0.0,
        )

    def test_target_ending_cli_accepts_multiple_values(
        self,
    ) -> None:
        common = [
            "--project",
            str(PROJECT),
            "--template",
            str(TEMPLATE),
            "--out",
            "unused",
            "--target-ending",
            "homecoming",
            "homecoming_shared",
        ]
        evolve_args = build_evolve_parser().parse_args(common)
        baseline_args = build_baseline_parser().parse_args(common)

        expected = ["homecoming", "homecoming_shared"]
        self.assertEqual(evolve_args.target_ending, expected)
        self.assertEqual(baseline_args.target_ending, expected)

    def test_disguised_obstacle_is_not_hostile_until_exposed(
        self,
    ) -> None:
        world, subjects = load_fixture()
        observer = subjects["鬼"]
        target = subjects["桃太郎"]

        observer.zone = "海"
        target.zone = "海"
        for subject in subjects.values():
            if subject.id not in {observer.id, target.id}:
                subject.vitality = "dead"

        target.identity_displayed = "旅の商人"
        belief = observer.beliefs_about[target.id]
        belief.identity_seen = False
        present = world.present_subjects("海")
        cfg = yaml.safe_load(
            (TEMPLATE / "action_graph.yaml").read_text(
                encoding="utf-8"
            )
        )
        fight = Action(
            "fight",
            (target.id,),
            {
                "target": target.id,
                "under_threat": False,
            },
        )

        hidden = classify(
            fight,
            observer,
            world,
            present,
            cfg,
        )
        hidden_context = ctx_key(observer, world, present)

        self.assertEqual(
            world.perceived_name(observer.id, target.id),
            "旅の商人",
        )
        self.assertEqual(hidden.target_role, "neutral")
        self.assertFalse(hidden_context[1])

        belief.identity_seen = True
        exposed = classify(
            fight,
            observer,
            world,
            present,
            cfg,
        )
        exposed_context = ctx_key(observer, world, present)

        self.assertEqual(
            world.perceived_name(observer.id, target.id),
            target.id,
        )
        self.assertEqual(exposed.target_role, "hostile")
        self.assertTrue(exposed_context[1])

    def test_candidate_category_adjustment_uses_turn_denominator(
        self,
    ) -> None:
        world, subjects = load_fixture()
        actor = subjects["桃太郎"]
        target = subjects["鬼"]
        actor.zone = "鬼ヶ島"
        target.zone = "鬼ヶ島"
        present = world.present_subjects(actor.zone)

        cfg = {
            "nodes": [
                {
                    "verb": "fight",
                    "category": "I",
                    "subtype": "fight",
                    "risk": "risky",
                    "sign": -1,
                },
                {
                    "verb": "persuade",
                    "category": "III",
                    "subtype": "persuade",
                    "risk": "neutral",
                    "sign": 1,
                },
            ],
            "edges": [],
        }
        rules = [
            {
                "id": "hostile_lean",
                "scope": "candidate",
                "when": "stance(self, target) < -0.3",
                "adjust": {
                    "category_weight.I": 0.2,
                },
            }
        ]
        policy = Policy(
            Genome.neutral(),
            precedent=None,
            rules=rules,
            cfg=cfg,
        )
        fight = Action(
            "fight",
            (target.id,),
            {"target": target.id},
        )
        persuade = Action(
            "persuade",
            (target.id,),
            {"target": target.id},
        )

        output = policy.reweight(
            actor,
            world,
            present,
            [(fight, 1.0), (persuade, 1.0)],
            turn=1,
            day=1,
        )
        by_verb = {
            action.verb: action.meta["policy"]
            for action, _ in output
        }

        self.assertEqual(
            by_verb["fight"]["effective_genome"][
                "category_weight"
            ]["I"],
            0.7,
        )
        self.assertEqual(
            by_verb["persuade"]["effective_genome"][
                "category_weight"
            ]["I"],
            0.7,
        )
        self.assertAlmostEqual(
            by_verb["fight"]["m_cat"],
            1.4,
        )
        self.assertAlmostEqual(
            by_verb["persuade"]["m_cat"],
            1.0,
        )


class Phase4GapEngineTests(unittest.TestCase):
    def test_rule_bits_off_preserves_legacy_genome_encoding_and_rng(
        self,
    ) -> None:
        self.assertNotIn(
            "rule_bits",
            Genome.neutral().to_dict(),
        )

        legacy_rng = random.Random(91)
        disabled_rng = random.Random(91)
        legacy = [
            Genome.random(legacy_rng).to_dict()
            for _ in range(8)
        ]
        disabled = [
            Genome.random(
                disabled_rng,
                rule_ids=(),
            ).to_dict()
            for _ in range(8)
        ]

        self.assertEqual(legacy, disabled)
        self.assertEqual(
            legacy_rng.getstate(),
            disabled_rng.getstate(),
        )
        self.assertTrue(
            all("rule_bits" not in genome for genome in disabled)
        )

    def test_disabled_rule_bit_prevents_policy_adjustment(
        self,
    ) -> None:
        world, subjects = load_fixture()
        actor = subjects["桃太郎"]
        present = world.present_subjects(actor.zone)
        rules = [
            {
                "id": "boost_train",
                "scope": "turn",
                "when": "True",
                "adjust": {
                    "category_weight.I": 0.4,
                },
            }
        ]
        cfg = {
            "nodes": [
                {
                    "verb": "train",
                    "category": "I",
                    "subtype": "self_strengthen",
                    "risk": "neutral",
                    "sign": 0,
                }
            ],
            "edges": [],
        }
        encoded = Genome.neutral(
            rule_ids=("boost_train",),
        ).to_dict()
        encoded["rule_bits"] = {"boost_train": False}
        genome = Genome.from_dict(encoded)
        policy = Policy(
            genome,
            precedent=None,
            rules=rules,
            cfg=cfg,
        )
        action = Action("train")

        output = policy.reweight(
            actor,
            world,
            present,
            [(action, 1.0)],
            turn=1,
            day=1,
        )

        self.assertEqual(policy.rules, ())
        self.assertEqual(len(output), 1)
        self.assertAlmostEqual(output[0][1], 1.0)
        self.assertEqual(
            action.meta["policy"]["effective_genome"][
                "category_weight"
            ]["I"],
            0.5,
        )
        self.assertEqual(
            action.meta["policy"]["effective_genome"][
                "rule_bits"
            ],
            {"boost_train": False},
        )

    def test_meta_evolution_crossover_and_mutation_are_deterministic(
        self,
    ) -> None:
        rule_ids = ("after_crossing", "hostile_lean")
        first = Genome.neutral(rule_ids=rule_ids)
        second_raw = first.to_dict()
        second_raw["rule_bits"] = {
            "after_crossing": False,
            "hostile_lean": False,
        }
        second = Genome.from_dict(second_raw)

        def produce() -> tuple[dict[str, Any], object]:
            rng = random.Random(117)
            child = Genome.crossover(
                first,
                second,
                rng,
            )
            mutated = Genome.mutate(
                child,
                rng,
                p=0.3,
                rule_p=0.1,
            )
            return mutated.to_dict(), rng.getstate()

        first_result, first_state = produce()
        second_result, second_state = produce()

        self.assertEqual(first_result, second_result)
        self.assertEqual(first_state, second_state)
        self.assertEqual(
            set(first_result["rule_bits"]),
            set(rule_ids),
        )

        flipped = Genome.mutate(
            first,
            random.Random(5),
            p=0.0,
            rule_p=1.0,
        )
        self.assertEqual(
            flipped.rule_bits,
            {
                "after_crossing": False,
                "hostile_lean": False,
            },
        )

    def test_genre_tags_prune_classification_and_candidates(
        self,
    ) -> None:
        world, subjects = load_fixture()
        actor = subjects["桃太郎"]
        actor.verbs = {"train"}
        world.genres = frozenset({"romance"})
        cfg = {
            "nodes": [
                {
                    "verb": "train",
                    "category": "I",
                    "subtype": "detective_training",
                    "genres": ["detective"],
                },
                {
                    "verb": "train",
                    "category": "III",
                    "subtype": "romance_training",
                    "genres": ["romance"],
                },
            ],
            "edges": [],
        }

        classification = classify(
            Action("train"),
            actor,
            world,
            world.present_subjects(actor.zone),
            cfg,
        )
        self.assertEqual(classification.category, "III")
        self.assertEqual(
            classification.subtype,
            "romance_training",
        )

        world.action_graph = {
            "nodes": [
                {
                    "verb": "train",
                    "genres": ["detective"],
                }
            ],
            "edges": [],
        }
        self.assertEqual(
            candidates(
                actor,
                world,
                SimpleNamespace(day=1, turn=1),
            ),
            [],
        )

        raw_world = yaml.safe_load(
            (PROJECT / "world.yaml").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world_path = root / "world.yaml"
            graph_path = root / "action_graph.yaml"
            world_path.write_text(
                yaml.safe_dump(
                    raw_world,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
                newline="\n",
            )

            for invalid_genres in ([1], [""]):
                with self.subTest(genres=invalid_genres):
                    graph_path.write_text(
                        yaml.safe_dump(
                            {
                                "genre": "romance",
                                "nodes": [
                                    {
                                        "verb": "train",
                                        "genres": invalid_genres,
                                    }
                                ],
                                "edges": [],
                            },
                            allow_unicode=True,
                            sort_keys=False,
                        ),
                        encoding="utf-8",
                        newline="\n",
                    )
                    with self.assertRaises(ValueError):
                        World.from_yaml(
                            world_path,
                            action_graph_path=graph_path,
                        )

    def test_generic_shaped_preserves_momotaro_delivery_score(
        self,
    ) -> None:
        world, subjects = load_fixture()
        protagonist = subjects["桃太郎"]
        holder = subjects["鬼"]
        rows: list[dict[str, Any]] = [
            {
                "kind": "header",
                "protagonist": protagonist.id,
            }
        ]

        self.assertEqual(shaped(rows, world), 0.3)

        holder.remove_item("鬼ヶ島の宝物", 1)
        protagonist.add_item("鬼ヶ島の宝物", 1)
        protagonist.zone = "道中"
        self.assertEqual(shaped(rows, world), 0.6)

        protagonist.zone = "村"
        rows.append(
            {
                "kind": "event",
                "verb": "ending",
                "id": "homecoming",
            }
        )
        self.assertEqual(shaped(rows, world), 1.0)

    def test_meta_evolution_off_archive_is_byte_identical(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = {
                "ga_seed": 29,
                "generations": 2,
                "keep": "all",
                "population": 5,
                "processes": 1,
                "project": project,
                "seed_base": 31,
                "seeds": 1,
                "template": TEMPLATE,
            }

            evolve(
                {
                    **common,
                    "out": root / "legacy",
                }
            )
            evolve(
                {
                    **common,
                    "meta_evolution": False,
                    "out": root / "explicit-off",
                }
            )
            evolve(
                {
                    **common,
                    "meta_evolution": True,
                    "out": root / "enabled",
                }
            )

            self.assertEqual(
                (
                    root
                    / "legacy"
                    / "archive.json"
                ).read_bytes(),
                (
                    root
                    / "explicit-off"
                    / "archive.json"
                ).read_bytes(),
            )

            legacy_summary = json.loads(
                (
                    root
                    / "legacy"
                    / "summary.json"
                ).read_text(encoding="utf-8")
            )
            disabled_summary = json.loads(
                (
                    root
                    / "explicit-off"
                    / "summary.json"
                ).read_text(encoding="utf-8")
            )
            enabled_summary = json.loads(
                (
                    root
                    / "enabled"
                    / "summary.json"
                ).read_text(encoding="utf-8")
            )

            self.assertNotIn(
                "meta_evolution",
                legacy_summary,
            )
            self.assertNotIn(
                "meta_evolution",
                disabled_summary,
            )
            self.assertIs(
                enabled_summary["meta_evolution"],
                True,
            )

    def test_world_expansion_detect_writes_report_and_off_is_byte_identical(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = {
                "ga_seed": 29,
                "generations": 2,
                "keep": "all",
                "population": 5,
                "processes": 1,
                "project": project,
                "seed_base": 31,
                "seeds": 1,
                "template": TEMPLATE,
            }

            evolve({**common, "out": root / "legacy"})
            evolve({**common, "world_expansion": "off", "out": root / "explicit-off"})
            evolve({**common, "world_expansion": "detect", "out": root / "detect"})

            def snapshot(name: str) -> dict[Path, bytes]:
                base = root / name
                return {
                    path.relative_to(base): path.read_bytes()
                    for path in base.rglob("*")
                    if path.is_file()
                }

            legacy_files = snapshot("legacy")
            off_files = snapshot("explicit-off")
            detect_files = snapshot("detect")

            world_demand_path = Path("world_demand.json")
            summary_path = Path("summary.json")

            # (1) off and unspecified are byte-identical everywhere, including
            # summary.json, and neither ever writes world_demand.json.
            self.assertEqual(legacy_files, off_files)
            self.assertNotIn(world_demand_path, legacy_files)
            legacy_summary = json.loads(legacy_files[summary_path].decode("utf-8"))
            self.assertNotIn("world_expansion", legacy_summary)

            # (2) detect writes a schema_version=1 report and records the
            # setting in summary.json.
            self.assertIn(world_demand_path, detect_files)
            report = json.loads(detect_files[world_demand_path].decode("utf-8"))
            self.assertEqual(report["schema_version"], 1)
            detect_summary = json.loads(detect_files[summary_path].decode("utf-8"))
            self.assertEqual(detect_summary["world_expansion"], "detect")

            # (3) detect adds exactly world_demand.json and touches only
            # summary.json otherwise -- every layers.jsonl, archive.json,
            # population.json, ... is byte-identical to the off run.
            self.assertEqual(
                set(legacy_files) | {world_demand_path},
                set(detect_files),
            )
            for key in legacy_files:
                if key == summary_path:
                    continue
                self.assertEqual(legacy_files[key], detect_files[key], key)

    def test_world_patches_recorded_in_summary_only_when_world_has_expansion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expanded_project = make_reaching_project(root / "expanded")
            world_path = expanded_project / "world.yaml"
            world_raw = yaml.safe_load(world_path.read_text(encoding="utf-8"))
            world_raw["expansion"] = {
                "base": "桃太郎",
                "patches": [{"id": "p-test1234", "title": "テスト拡張",
                             "added": {"zones": [], "items": [], "facts": [], "daily_events": []}}],
            }
            world_path.write_text(
                yaml.safe_dump(world_raw, allow_unicode=True, sort_keys=False),
                encoding="utf-8", newline="\n",
            )
            plain_project = make_reaching_project(root / "plain")

            common = {
                "ga_seed": 29, "generations": 1, "keep": "all", "population": 3,
                "processes": 1, "seed_base": 31, "seeds": 1, "template": TEMPLATE,
            }
            evolve({**common, "project": expanded_project, "out": root / "with-expansion"})
            evolve({**common, "project": plain_project, "out": root / "no-expansion"})

            with_summary = json.loads((root / "with-expansion" / "summary.json").read_text(encoding="utf-8"))
            no_summary = json.loads((root / "no-expansion" / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(with_summary["world_patches"], ["p-test1234"])
            self.assertNotIn("world_patches", no_summary)

    def test_meta_evolution_cli_flag(self) -> None:
        required = [
            "--project",
            str(PROJECT),
            "--template",
            str(TEMPLATE),
            "--out",
            "unused",
        ]

        default_args = build_evolve_parser().parse_args(required)
        enabled_args = build_evolve_parser().parse_args(
            [*required, "--meta-evolution"]
        )

        self.assertFalse(default_args.meta_evolution)
        self.assertTrue(enabled_args.meta_evolution)

    def test_rethink_belief_reversal_increases_quality(
        self,
    ) -> None:
        base_rows: list[dict[str, Any]] = [
            {
                "kind": "header",
                "protagonist": "桃太郎",
            }
        ]
        rethink_rows = [
            *base_rows,
            {
                "kind": "event",
                "verb": "rethink",
                "subject": "桃太郎",
                "details": {
                    "before": {
                        "treasure_thief": {
                            "value": "猿",
                            "confidence": 0.4,
                        },
                        "oni_weakness": {
                            "value": "火",
                            "confidence": 0.2,
                        },
                    },
                    "after": {
                        "treasure_thief": {
                            "value": "鬼",
                            "confidence": 0.5,
                        },
                        "oni_weakness": {
                            "value": "火",
                            "confidence": 0.8,
                        },
                    },
                },
            },
        ]
        world_meta = {"protagonist": "桃太郎"}

        self.assertGreater(
            quality(rethink_rows, world_meta),
            quality(base_rows, world_meta),
        )


class LineageStatsTests(unittest.TestCase):
    """WB-LINEAGE-001: ally/contest counting and generation-level aggregates,
    exercised against hand-built layer rows (no simulation run)."""

    def test_ally_gained_counts_only_protagonist_as_subject(self) -> None:
        rows = [
            {"kind": "header", "protagonist": "桃太郎"},
            {
                "kind": "event",
                "verb": "ally_gained",
                "subject": "鬼",
                "details": {"ally": "桃太郎"},
            },
            {
                "kind": "event",
                "verb": "ally_gained",
                "subject": "桃太郎",
                "details": {"ally": "犬"},
                "turn": 1,
            },
        ]
        stats = _lineage_stats(rows, "桃太郎", "鬼")
        self.assertEqual(stats["allies_final"], 1)

    def test_no_contest_leaves_contest_fields_none(self) -> None:
        rows = [
            {"kind": "header", "protagonist": "桃太郎"},
            {
                "kind": "event",
                "verb": "ally_gained",
                "subject": "桃太郎",
                "details": {"ally": "犬"},
                "turn": 1,
            },
            {
                "kind": "decision",
                "subject": "桃太郎",
                "verb": "fight",
                "args": ["鬼"],
                "result": "invalid",
                "turn": 2,
            },
        ]
        stats = _lineage_stats(rows, "桃太郎", "鬼")
        self.assertIsNone(stats["allies_at_contest"])
        self.assertIsNone(stats["contest_turn"])
        self.assertEqual(stats["allies_final"], 1)

    def test_contest_is_recorded_once_at_first_resolved_fight(self) -> None:
        rows = [
            {"kind": "header", "protagonist": "桃太郎"},
            {
                "kind": "event",
                "verb": "ally_gained",
                "subject": "桃太郎",
                "details": {"ally": "犬"},
                "turn": 1,
            },
            {
                "kind": "decision",
                "subject": "桃太郎",
                "verb": "fight",
                "args": ["鬼"],
                "result": "won",
                "turn": 5,
            },
            {
                "kind": "event",
                "verb": "ally_gained",
                "subject": "桃太郎",
                "details": {"ally": "猿"},
                "turn": 6,
            },
            {
                "kind": "decision",
                "subject": "桃太郎",
                "verb": "fight",
                "args": ["鬼"],
                "result": "lost",
                "turn": 9,
            },
        ]
        stats = _lineage_stats(rows, "桃太郎", "鬼")
        # allies gained after turn 5 must not count toward the contest snapshot,
        # and the second fight against the same antagonist must not overwrite it.
        self.assertEqual(stats["allies_at_contest"], 1)
        self.assertEqual(stats["contest_turn"], 5)
        self.assertEqual(stats["allies_final"], 2)

    def test_contest_is_recorded_when_antagonist_is_the_subject(self) -> None:
        # resolve(actor, target, ...) decides the winner symmetrically, so a
        # fight the antagonist initiated is just as decisive as one the
        # protagonist initiated (review fix: "decisive contest" must not
        # depend on who is `subject`).
        rows = [
            {"kind": "header", "protagonist": "桃太郎"},
            {
                "kind": "event",
                "verb": "ally_gained",
                "subject": "桃太郎",
                "details": {"ally": "犬"},
                "turn": 1,
            },
            {
                "kind": "decision",
                "subject": "鬼",
                "verb": "fight",
                "args": ["桃太郎"],
                "result": "won",
                "turn": 5,
            },
        ]
        stats = _lineage_stats(rows, "桃太郎", "鬼")
        self.assertEqual(stats["allies_at_contest"], 1)
        self.assertEqual(stats["contest_turn"], 5)

    def test_action_share_counts_a_run_once_for_repeated_actions(self) -> None:
        raw_results = [
            {
                "runs": [
                    {
                        "effective_sequence": [
                            ["I", "train", "none"],
                            ["I", "train", "none"],
                        ],
                        "allies_at_contest": None,
                        "allies_final": 0,
                        "contest_turn": None,
                    },
                ],
            },
        ]
        summary = _generation_lineage_summary(raw_results)
        self.assertEqual(summary["action_share"], {"I/train/none": 1.0})

    def test_allies_mean_at_contest_excludes_non_contest_runs(self) -> None:
        raw_results = [
            {
                "runs": [
                    {
                        "effective_sequence": [],
                        "allies_at_contest": 2,
                        "allies_final": 2,
                        "contest_turn": 5,
                    },
                    {
                        "effective_sequence": [],
                        "allies_at_contest": None,
                        "allies_final": 1,
                        "contest_turn": None,
                    },
                ],
            },
        ]
        summary = _generation_lineage_summary(raw_results)
        self.assertEqual(summary["allies_mean_at_contest"], 2.0)
        self.assertAlmostEqual(summary["allies_mean_final"], 1.5)
        self.assertEqual(summary["contest_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
