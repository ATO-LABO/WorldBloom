"""WB-GROWTH-001: acquired personality growth from outcome-scope policy
rules (S0: record-only "growth" rows; S1: genome.plasticity actually feeds
the acquired shift back into reweight() via Policy.current_genome())."""

from __future__ import annotations

import dataclasses
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from engine.actions import Action
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine.evolve import _rule_ids, evolve
from gapengine.genome import Genome
from gapengine.policy import Policy
from gapengine.seed_genomes import reconcile as reconcile_seed_genome

ROOT = Path(__file__).resolve().parents[1]


def load_fixture(project: Path, template: Path) -> tuple[World, dict[str, Subject]]:
    action_graph = template / "action_graph.yaml"
    world = World.from_yaml(
        project / "world.yaml",
        action_graph_path=action_graph if action_graph.is_file() else None,
    )
    subjects = [
        Subject.from_yaml(path)
        for path in sorted((project / "subjects").glob("*.yaml"), key=lambda v: v.name)
    ]
    values = {subject.id: subject for subject in subjects}
    world.bind_subjects(values)
    return world, values


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


MOMOTARO_PROJECT = ROOT / "projects" / "momotaro"
MOMOTARO_TEMPLATE = ROOT / "templates" / "momotaro"
PLUS2_PROJECT = ROOT / "projects" / "momotaro_plus2"
PLUS2_TEMPLATE = ROOT / "templates" / "momotaro_plus2"

FIGHT_LOST_RULE = {
    "id": "g_fight_lost",
    "scope": "outcome",
    "when": "actor_is_self and verb == 'fight' and result == 'lost'",
    "adjust": {"risk_tolerance": -0.15},
    "description": "敗北で慎重になる",
}


def _synthetic_fight_lost_row(subject_id: str, target_id: str) -> dict[str, Any]:
    return {
        "kind": "decision",
        "turn": 3,
        "day": 1,
        "subject": subject_id,
        "verb": "fight",
        "args": [target_id],
        "result": "lost",
        "delta": {"actor": {}, "targets": {}, "relations": [], "objective": None},
        "classification": {
            "category": "I",
            "subtype": "attack",
            "risk_class": "risky",
            "stance_sign": 1,
            "target_role": "hostile",
        },
        "effective": True,
    }


class GenomePlasticityTests(unittest.TestCase):
    def test_default_is_neutral_and_omitted_from_to_dict(self) -> None:
        genome = Genome.neutral()
        self.assertEqual(genome.plasticity, 0.0)
        self.assertNotIn("plasticity", genome.to_dict())
        self.assertTrue(genome.is_neutral())

    def test_to_dict_from_dict_round_trip_when_nonzero(self) -> None:
        genome = dataclasses.replace(Genome.neutral(), plasticity=0.3)
        encoded = genome.to_dict()
        self.assertEqual(encoded["plasticity"], 0.3)
        self.assertEqual(Genome.from_dict(encoded).plasticity, 0.3)

    def test_from_dict_without_plasticity_key_defaults_zero(self) -> None:
        raw = Genome.neutral().to_dict()
        self.assertNotIn("plasticity", raw)
        self.assertEqual(Genome.from_dict(raw).plasticity, 0.0)

    def test_random_without_plastic_consumes_no_extra_randomness(self) -> None:
        rng_a = random.Random(7)
        rng_b = random.Random(7)
        genome = Genome.random(rng_a)
        for category in genome.category_weight:
            self.assertGreaterEqual(genome.category_weight[category], 0.0)
        self.assertEqual(genome.plasticity, 0.0)
        # Same seed, same number of draws consumed -> next draw matches.
        for _ in range(9):
            rng_b.random()
        self.assertEqual(rng_a.random(), rng_b.random())

    def test_random_with_plastic_draws_exactly_one_more(self) -> None:
        rng_a = random.Random(7)
        rng_b = random.Random(7)
        genome = Genome.random(rng_a, plastic=True)
        self.assertNotEqual(genome.plasticity, 0.0)
        for _ in range(10):
            rng_b.random()
        self.assertEqual(rng_a.random(), rng_b.random())

    def test_crossover_and_mutate_without_plastic_draw_nothing_extra(self) -> None:
        # `plastic and rng.random() < p` short-circuits before ever calling
        # rng.random() when plastic=False, so no extra draw is possible by
        # construction; this just pins plasticity staying 0 either way.
        first = Genome.random(random.Random(1))
        second = Genome.random(random.Random(2))
        child = Genome.crossover(first, second, random.Random(9))
        self.assertEqual(child.plasticity, 0.0)
        mutated = Genome.mutate(child, random.Random(11))
        self.assertEqual(mutated.plasticity, 0.0)


class PolicyOutcomeRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture(MOMOTARO_PROJECT, MOMOTARO_TEMPLATE)

    def test_no_outcome_rules_observe_is_noop(self) -> None:
        policy = Policy(Genome.neutral(), precedent=None, rules=[])
        subject = self.subjects["桃太郎"]
        row = _synthetic_fight_lost_row("桃太郎", "鬼")
        self.assertEqual(policy.observe(subject, self.world, [], row), [])
        self.assertEqual(policy.acquired, {})

    def test_growth_marker_scales_with_plasticity_and_accumulates(self) -> None:
        genome = dataclasses.replace(Genome.neutral(), plasticity=1.0)
        policy = Policy(genome, precedent=None, rules=[FIGHT_LOST_RULE])
        subject = self.subjects["桃太郎"]
        row = _synthetic_fight_lost_row("桃太郎", "鬼")

        markers = policy.observe(subject, self.world, [], row)
        self.assertEqual(len(markers), 1)
        marker = markers[0]
        self.assertEqual(marker["verb"], "growth")
        self.assertEqual(marker["subject"], "桃太郎")
        details = marker["details"]
        self.assertEqual(details["rule"], "g_fight_lost")
        self.assertEqual(details["shift"], {"risk_tolerance": -0.15})
        self.assertEqual(details["plasticity"], 1.0)
        self.assertEqual(details["acquired_after"], {"risk_tolerance": -0.15})
        self.assertEqual(policy.acquired, {"risk_tolerance": -0.15})

        # A second loss accumulates rather than replacing.
        policy.observe(subject, self.world, [], row)
        self.assertAlmostEqual(policy.acquired["risk_tolerance"], -0.30)

    def test_zero_plasticity_shift_is_a_noop_and_writes_no_marker(self) -> None:
        policy = Policy(Genome.neutral(), precedent=None, rules=[FIGHT_LOST_RULE])
        subject = self.subjects["桃太郎"]
        row = _synthetic_fight_lost_row("桃太郎", "鬼")
        self.assertEqual(policy.observe(subject, self.world, [], row), [])
        self.assertEqual(policy.acquired, {})
        self.assertIs(policy.current_genome(), policy.genome)

    def test_unknown_binding_name_raises(self) -> None:
        rule = {
            "id": "bad_rule",
            "scope": "outcome",
            "when": "not_a_real_binding == 1",
            "adjust": {"risk_tolerance": 0.1},
        }
        genome = dataclasses.replace(Genome.neutral(), plasticity=1.0)
        policy = Policy(genome, precedent=None, rules=[rule])
        subject = self.subjects["桃太郎"]
        row = _synthetic_fight_lost_row("桃太郎", "鬼")
        with self.assertRaises(ValueError):
            policy.observe(subject, self.world, [], row)

    def test_outcome_scope_does_not_enter_turn_candidate_rules(self) -> None:
        genome = dataclasses.replace(Genome.neutral(), plasticity=1.0)
        policy = Policy(genome, precedent=None, rules=[FIGHT_LOST_RULE])
        self.assertEqual(policy.rules, ())
        self.assertEqual(len(policy.outcome_rules), 1)

    def test_untargeted_row_does_not_involve_a_bystander(self) -> None:
        # Review fix (should #2): an untargeted row (no args, no
        # details.target/ally) used to default target to subject.id, which
        # made involves_self true for *every* subject observing it. It must
        # now be false for anyone who isn't the row's own actor.
        rule = {
            "id": "g_witness",
            "scope": "outcome",
            "when": "involves_self",
            "adjust": {"risk_tolerance": 0.01},
        }
        genome = dataclasses.replace(Genome.neutral(), plasticity=1.0)
        policy = Policy(genome, precedent=None, rules=[rule])
        row = {
            "kind": "decision",
            "turn": 1,
            "day": 1,
            "subject": "桃太郎",
            "verb": "rest",
            "args": [],
            "result": "rested",
            "delta": {"actor": {}, "targets": {}, "relations": [], "objective": None},
            "classification": {
                "category": None,
                "subtype": "rest",
                "risk_class": "neutral",
                "stance_sign": 0,
                "target_role": "none",
            },
            "effective": False,
        }
        # The actor itself is involved (actor_is_self).
        self.assertEqual(len(policy.observe(self.subjects["桃太郎"], self.world, [], row)), 1)
        # A bystander with no stake in an untargeted row is not.
        self.assertEqual(policy.observe(self.subjects["鬼"], self.world, [], row), [])

    def test_stress_after_binding_is_none_on_marker_rows_and_a_value_on_decisions(
        self,
    ) -> None:
        # Review fix (should #3): renamed from "stress_delta" (it was
        # always the post-change value, never a difference, and a
        # marker/event row -- delta={} always -- could never carry one).
        genome = dataclasses.replace(Genome.neutral(), plasticity=1.0)
        marker_probe = {
            "id": "probe",
            "scope": "outcome",
            "when": "stress_after == None",
            "adjust": {"risk_tolerance": 0.01},
        }
        policy = Policy(genome, precedent=None, rules=[marker_probe])
        marker_row = {
            "kind": "event",
            "turn": 1,
            "day": 1,
            "subject": "桃太郎",
            "verb": "downed",
            "args": [],
            "result": "applied",
            "delta": {},
            "details": {"downed_since": 1},
        }
        self.assertEqual(
            len(policy.observe(self.subjects["桃太郎"], self.world, [], marker_row)), 1
        )

        decision_probe = {
            "id": "probe2",
            "scope": "outcome",
            "when": "stress_after != None and stress_after > 0.5",
            "adjust": {"risk_tolerance": 0.01},
        }
        policy2 = Policy(genome, precedent=None, rules=[decision_probe])
        decision_row = dict(_synthetic_fight_lost_row("桃太郎", "鬼"))
        decision_row["delta"] = {
            "actor": {"stress": 1.2},
            "targets": {},
            "relations": [],
            "objective": None,
        }
        self.assertEqual(
            len(policy2.observe(self.subjects["桃太郎"], self.world, [], decision_row)), 1
        )

    def test_current_genome_feeds_reweight_effective_genome_and_meta(self) -> None:
        genome = dataclasses.replace(Genome.neutral(), plasticity=1.0)
        policy = Policy(genome, precedent=None, rules=[FIGHT_LOST_RULE], cfg={"nodes": [], "edges": []})
        subject = self.subjects["桃太郎"]
        row = _synthetic_fight_lost_row("桃太郎", "鬼")
        policy.observe(subject, self.world, [], row)

        action = Action("rest")
        weighted = policy.reweight(subject, self.world, [], [(action, 1.0)], turn=3, day=1)
        self.assertEqual(len(weighted), 1)
        meta = action.meta["policy"]
        self.assertAlmostEqual(meta["effective_genome"]["risk_tolerance"], 0.35)
        self.assertEqual(meta["acquired"], {"risk_tolerance": -0.15})


class SimulationGrowthWiringTests(unittest.TestCase):
    def test_momotaro_no_outcome_rules_produces_no_growth_rows(self) -> None:
        world, subjects = load_fixture(MOMOTARO_PROJECT, MOMOTARO_TEMPLATE)
        genome = Genome.neutral()
        rules = [
            {
                "id": "hostile_lean",
                "scope": "candidate",
                "when": "stance(self, target) < -0.3",
                "adjust": {"category_weight.I": 0.2, "risk_tolerance": 0.1},
            },
        ]
        policy = Policy(genome, precedent=None, rules=rules, cfg={"nodes": [], "edges": []})
        with tempfile.TemporaryDirectory() as out_dir:
            path = Simulation(
                1,
                world,
                subjects,
                Path(out_dir),
                policies={world.protagonist: policy},
            ).run()
            rows = read_rows(path)
        self.assertFalse(any(row.get("verb") == "growth" for row in rows))

    def _plus2_rules(self) -> list[dict[str, Any]]:
        return list(
            yaml.safe_load((PLUS2_TEMPLATE / "rules.yaml").read_text(encoding="utf-8"))
        )

    def _run_plus2(self, out_dir: Path, genome: Genome, rules: list[dict[str, Any]]) -> Path:
        world, subjects = load_fixture(PLUS2_PROJECT, PLUS2_TEMPLATE)
        action_cfg = yaml.safe_load(
            (PLUS2_TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8")
        )
        policy = Policy(genome, precedent=None, rules=rules, cfg=action_cfg)
        return Simulation(
            1,
            world,
            subjects,
            out_dir,
            policies={world.protagonist: policy},
        ).run()

    def test_momotaro_plus2_same_seed_is_byte_identical_with_growth_active(self) -> None:
        # Review fix (should #5): plasticity=0 (Genome.neutral()) can never
        # exercise reweight()'s acquired-shift path -- use a plastic genome
        # so this test actually covers growth *steering* weighting, not
        # just recording it.
        rules = self._plus2_rules()
        self.assertTrue(any(rule.get("scope") == "outcome" for rule in rules))
        genome = dataclasses.replace(Genome.neutral(), plasticity=1.0)

        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            path_a = self._run_plus2(Path(first), genome, rules)
            path_b = self._run_plus2(Path(second), genome, rules)
            self.assertEqual(path_a.read_bytes(), path_b.read_bytes())
            rows = read_rows(path_a)

        growth_rows = [row for row in rows if row.get("verb") == "growth"]
        self.assertGreaterEqual(len(growth_rows), 1)

        acquired_rows = [
            row
            for row in rows
            if row.get("kind") == "decision"
            and isinstance(row.get("policy"), dict)
            and "acquired" in row["policy"]
        ]
        self.assertGreaterEqual(len(acquired_rows), 1)

    def test_zero_plasticity_layers_are_byte_identical_with_or_without_outcome_rules(
        self,
    ) -> None:
        # Review fix (should #5): the absolute guarantee behind must #4 --
        # plasticity=0 must make outcome rules a complete no-op on the
        # written layers, not merely "close" -- pinned as an equality, not
        # just "no growth rows".
        rules_with_outcome = self._plus2_rules()
        rules_without_outcome = [
            rule for rule in rules_with_outcome if rule.get("scope") != "outcome"
        ]
        self.assertNotEqual(len(rules_with_outcome), len(rules_without_outcome))
        genome = Genome.neutral()

        with tempfile.TemporaryDirectory() as with_dir, tempfile.TemporaryDirectory() as without_dir:
            path_with = self._run_plus2(Path(with_dir), genome, rules_with_outcome)
            path_without = self._run_plus2(Path(without_dir), genome, rules_without_outcome)
            self.assertEqual(path_with.read_bytes(), path_without.read_bytes())


class MetaEvolutionRuleIdsTests(unittest.TestCase):
    """Review fix (must #1): meta_evolution's rule_bits genes must never
    include outcome-scope rules -- otherwise a template that merely *has*
    outcome rules (whether or not personality_growth is even on) consumes
    extra GA rng (mutate()'s per-bit flip draw) that a pre-WB-GROWTH-001
    template with the same turn/candidate rules would not, silently
    changing every meta_evolution run's genome/archive."""

    def test_rule_ids_excludes_outcome_scope(self) -> None:
        rules = list(
            yaml.safe_load((PLUS2_TEMPLATE / "rules.yaml").read_text(encoding="utf-8"))
        )
        outcome_count = sum(1 for rule in rules if rule.get("scope") == "outcome")
        self.assertGreater(outcome_count, 0)
        ids = _rule_ids(rules)
        self.assertEqual(
            set(ids),
            {"hostile_lean", "after_crossing", "when_downed_ally"},
        )
        self.assertEqual(len(ids), len(rules) - outcome_count)

    def test_small_meta_evolution_evolve_is_identical_with_or_without_outcome_rules(
        self,
    ) -> None:
        # Reproduces the reported symptom directly: pop 4 / gen 2 / seed 1,
        # meta_evolution=True, personality_growth left off (the default) --
        # a template with WB-GROWTH-001's 5 outcome rules appended must
        # produce a byte-identical archive.json to the same template with
        # those 5 rules stripped back out.
        with tempfile.TemporaryDirectory() as root_str:
            root = Path(root_str)
            stripped_template = root / "template_no_outcome"
            shutil.copytree(PLUS2_TEMPLATE, stripped_template)
            rules = yaml.safe_load(
                (PLUS2_TEMPLATE / "rules.yaml").read_text(encoding="utf-8")
            )
            stripped_rules = [r for r in rules if r.get("scope") != "outcome"]
            self.assertNotEqual(len(rules), len(stripped_rules))
            (stripped_template / "rules.yaml").write_text(
                yaml.safe_dump(stripped_rules, allow_unicode=True),
                encoding="utf-8",
            )

            common = {
                "ga_seed": 1,
                "generations": 2,
                "keep": "all",
                "meta_evolution": True,
                "population": 4,
                "project": PLUS2_PROJECT,
                "seed_base": 0,
                "seeds": 1,
                "processes": 1,
            }
            with_outcome = evolve(
                {**common, "template": PLUS2_TEMPLATE, "out": root / "with_outcome"}
            )
            without_outcome = evolve(
                {**common, "template": stripped_template, "out": root / "without_outcome"}
            )
            self.assertEqual(
                (root / "with_outcome" / "archive.json").read_bytes(),
                (root / "without_outcome" / "archive.json").read_bytes(),
            )
            # This tiny population/generation count need not actually reach
            # any QD cell -- the point is that both variants agree, not
            # that either one succeeds at the world.
            self.assertEqual(len(with_outcome.cells), len(without_outcome.cells))


class SeedGenomesGrowthGateTests(unittest.TestCase):
    def test_growth_disabled_zeroes_plasticity_even_if_the_source_archive_has_some(
        self,
    ) -> None:
        # Review fix (should #7): a genome seeded from a prior
        # --personality-growth experiment must not carry nonzero plasticity
        # into a run where personality_growth is off.
        raw = dataclasses.replace(Genome.neutral(), plasticity=0.7).to_dict()
        self.assertEqual(
            reconcile_seed_genome(raw, growth_enabled=False).plasticity, 0.0
        )
        self.assertEqual(
            reconcile_seed_genome(raw, growth_enabled=True).plasticity, 0.7
        )
        # Default matches the feature's own off-by-default convention.
        self.assertEqual(reconcile_seed_genome(raw).plasticity, 0.0)


class PersonalityGrowthCfgKeyTests(unittest.TestCase):
    def test_cfg_key_is_personality_growth_not_growth(self) -> None:
        # Review fix (must #6): "growth" is execution/configs.py's
        # WORLDGROW-002 world-growth cfg ({mode, epochs, auto_retire}) --
        # gapengine.evolve's own key must not collide with it.
        with tempfile.TemporaryDirectory() as root_str:
            root = Path(root_str)
            common = {
                "ga_seed": 1,
                "generations": 1,
                "keep": "all",
                "population": 2,
                "project": MOMOTARO_PROJECT,
                "seed_base": 0,
                "seeds": 1,
                "template": MOMOTARO_TEMPLATE,
                "processes": 1,
            }
            plain = evolve({**common, "out": root / "plain"})
            wrong_key = evolve(
                {**common, "out": root / "wrong_key", "growth": {"enabled": True}}
            )
            self.assertEqual(
                (root / "plain" / "archive.json").read_bytes(),
                (root / "wrong_key" / "archive.json").read_bytes(),
            )
            self.assertEqual(len(plain.cells), len(wrong_key.cells))


if __name__ == "__main__":
    unittest.main()
