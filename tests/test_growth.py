"""WB-GROWTH-001: acquired personality growth from outcome-scope policy
rules (S0: record-only "growth" rows; S1: genome.plasticity actually feeds
the acquired shift back into reweight() via Policy.current_genome())."""

from __future__ import annotations

import dataclasses
import json
import random
import tempfile
import unittest
from pathlib import Path
from typing import Any

from engine.actions import Action
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine.genome import Genome
from gapengine.policy import Policy

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

    def test_momotaro_plus2_same_seed_is_byte_identical_with_growth_rules(self) -> None:
        # templates/momotaro_plus2/rules.yaml now carries the WB-GROWTH-001
        # outcome rules alongside its existing turn/candidate ones.
        import yaml

        rules = list(
            yaml.safe_load((PLUS2_TEMPLATE / "rules.yaml").read_text(encoding="utf-8"))
        )
        self.assertTrue(any(rule.get("scope") == "outcome" for rule in rules))

        def run_once(out_dir: Path) -> bytes:
            world, subjects = load_fixture(PLUS2_PROJECT, PLUS2_TEMPLATE)
            genome = Genome.neutral()
            action_cfg = yaml.safe_load(
                (PLUS2_TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8")
            )
            policy = Policy(genome, precedent=None, rules=rules, cfg=action_cfg)
            path = Simulation(
                1,
                world,
                subjects,
                out_dir,
                policies={world.protagonist: policy},
            ).run()
            return path.read_bytes()

        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            self.assertEqual(run_once(Path(first)), run_once(Path(second)))


if __name__ == "__main__":
    unittest.main()
