"""WB-JEV-001 Stage 2 acceptance tests (design by Fable, 2026-09-18) for
gapengine/rationality.py's RationalityTable/Rationality and their wiring
into Policy/Simulation/evolve. Uses a FakeJudge (deterministic sha256-based
p, no network) throughout -- Ollama itself is out of scope here (GPU is
unavailable on this machine; see the Stage 2 plan's real-machine section,
skipped by instruction)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence

import yaml

from engine.actions import Action
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine.evolve import _merge_rationality_table
from gapengine.genome import Genome
from gapengine.policy import Policy
from gapengine.rationality import Rationality, RationalityTable

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


def action_cfg() -> dict[str, Any]:
    return yaml.safe_load((TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8"))


def rules_cfg() -> list[dict[str, Any]]:
    return yaml.safe_load((TEMPLATE / "rules.yaml").read_text(encoding="utf-8"))


class FakeJudge:
    """Deterministic, network-free stand-in for OllamaLogprobJudge: p is a
    sha256 digest of (context, candidate), so two different renders never
    collide by accident and the same pair always yields the same p
    (mirrors Ollama's temperature-0 determinism)."""

    backend_name = "fake"
    model = "fake-v1"

    def __init__(self, *, none_for: frozenset[str] = frozenset()) -> None:
        self.calls = 0
        self._none_for = none_for

    def score(self, context_text: str, candidates: Sequence[str]) -> list[float | None]:
        self.calls += 1
        return [self._p(context_text, candidate) for candidate in candidates]

    def _p(self, context_text: str, candidate: str) -> float | None:
        if any(marker in candidate for marker in self._none_for):
            return None
        digest = hashlib.sha256(f"{context_text}|{candidate}".encode("utf-8")).hexdigest()
        return (int(digest[:8], 16) % 1000) / 1000.0


class ExplodingJudge:
    """Fails the test loudly if ever called -- used to prove a code path
    that must be judge-call-free (kappa<=0, or a fully-cached table) really
    is one, instead of trusting a call counter that could itself have a
    bug."""

    backend_name = "test-exploding"
    model = None

    def score(self, context_text: str, candidates: Sequence[str]) -> list[float | None]:
        raise AssertionError("judge.score must not be called on this path")


class RefusingJudge(FakeJudge):
    """Same backend_name/model as FakeJudge (so a fully-cached-table run's
    header stays byte-identical to the run that populated the table), but
    explodes if actually asked to score -- proves the second run makes zero
    judge calls without a call counter alone doing the proving."""

    def score(self, context_text: str, candidates: Sequence[str]) -> list[float | None]:
        raise AssertionError("judge.score must not be called when the table already covers every key")


def _run(
    *,
    seed: int,
    out_dir: Path,
    rationality: Rationality | None,
) -> Path:
    world, subjects = load_fixture()
    world.days = 2
    policy = Policy(
        Genome.neutral(),
        precedent=None,
        cfg=action_cfg(),
        rationality=rationality,
    )
    return Simulation(
        seed,
        world,
        subjects,
        out_dir,
        policies={world.protagonist: policy},
    ).run()


class RationalityKappaZeroTests(unittest.TestCase):
    """Plan §2 item 1: kappa=0 must cost nothing and change nothing."""

    def test_kappa_zero_is_byte_identical_and_calls_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            plain_path = _run(seed=153, out_dir=root / "plain", rationality=None)

            judge = ExplodingJudge()
            rationality = Rationality(
                kappa=0.0,
                table=RationalityTable(),
                judge=judge,
                common_knowledge=["dummy"],
                method="noul",
            )
            annotated_path = _run(
                seed=153, out_dir=root / "annotated", rationality=rationality
            )

            self.assertEqual(plain_path.read_bytes(), annotated_path.read_bytes())
            self.assertEqual(rationality.meta["judge_calls"], 0)
            self.assertEqual(rationality.new_entries, {})

            rows = read_rows(annotated_path)
            self.assertNotIn("rationality", rows[0])
            for row in rows:
                if row.get("kind") != "decision":
                    continue
                policy_meta = row.get("policy")
                if policy_meta is not None:
                    self.assertNotIn("m_rat", policy_meta)
                    self.assertNotIn("p_rat", policy_meta)


class RationalityMultiplierTests(unittest.TestCase):
    """Plan §2 item 2: the multiplier formula and meta recording."""

    def _weighted_candidates(self) -> tuple[Subject, World, list[Subject], list[tuple[Action, float]]]:
        world, subjects = load_fixture()
        actor = subjects[world.protagonist]
        present = world.present_subjects(actor.zone)
        weighted = [
            (Action("rest"), 1.0),
            (Action("move", ("森",)), 1.0),
            (Action("move", ("道中",)), 1.0),
        ]
        return actor, world, present, weighted

    def test_multiplier_matches_p_over_mean_formula(self) -> None:
        actor, world, present, weighted = self._weighted_candidates()
        judge = FakeJudge()
        rationality = Rationality(
            kappa=1.0,
            table=RationalityTable(),
            judge=judge,
            method="noul",
        )
        # rules non-empty (but none of them match a self-targeted candidate
        # in this fixture's default state) keeps annotation_only False
        # without perturbing genome-neutral's m_cat/m_risk/m_stance/m_nov
        # away from 1.0 -- isolating m_rat's effect on the output weight.
        policy = Policy(
            Genome.neutral(),
            precedent=None,
            rules=rules_cfg(),
            cfg=action_cfg(),
            rationality=rationality,
        )

        output = policy.reweight(actor, world, present, weighted, turn=0, day=0)
        self.assertEqual(judge.calls, 1)

        by_verb = {action.verb + str(action.args): (action, weight) for action, weight in output}
        p_by_desc: dict[str, float] = {}
        for action, weight in output:
            policy_meta = action.meta["policy"]
            self.assertEqual(policy_meta["m_cat"], 1.0)
            self.assertEqual(policy_meta["m_risk"], 1.0)
            self.assertEqual(policy_meta["m_stance"], 1.0)
            self.assertEqual(policy_meta["m_nov"], 1.0)
            p_rat = policy_meta["p_rat"]
            self.assertIsNotNone(p_rat)
            p_by_desc[action.verb + str(action.args)] = p_rat

        mean = sum(p_by_desc.values()) / len(p_by_desc)
        for key, (action, weight) in by_verb.items():
            expected_m = p_by_desc[key] / mean
            self.assertAlmostEqual(action.meta["policy"]["m_rat"], expected_m, places=9)
            self.assertAlmostEqual(weight, 1.0 * expected_m, places=9)

    def test_none_candidate_gets_identity_multiplier(self) -> None:
        actor, world, present, weighted = self._weighted_candidates()
        judge = FakeJudge(none_for=frozenset({"休んだ"}))
        rationality = Rationality(
            kappa=1.0,
            table=RationalityTable(),
            judge=judge,
            method="noul",
        )
        policy = Policy(
            Genome.neutral(),
            precedent=None,
            rules=rules_cfg(),
            cfg=action_cfg(),
            rationality=rationality,
        )

        output = policy.reweight(actor, world, present, weighted, turn=0, day=0)
        rest_action = next(action for action, _ in output if action.verb == "rest")
        self.assertIsNone(rest_action.meta["policy"]["p_rat"])
        self.assertEqual(rest_action.meta["policy"]["m_rat"], 1.0)


class RationalityReproducibilityTests(unittest.TestCase):
    """Plan §2 item 3: same table -> zero new judge calls, byte-identical
    layers.jsonl."""

    def test_second_run_with_saved_table_makes_no_judge_calls(self) -> None:
        # Plan §1.5's reproducibility claim is for the *same* rationality
        # table: "同じ (world, genome, seed, precedent, 合理性表) で
        # layers.jsonl がバイト一致". A priming run (starting from an empty
        # table) legitimately differs from a later run in its header's
        # table_hash_at_start/judge_calls -- that field exists precisely to
        # show which table snapshot a run used. So the comparison here is
        # between two runs that both start from the *same already-full*
        # table (the common evolve() case: generation N+1 reads the table
        # generation N finished with) -- not between the priming run and a
        # later one.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            priming_judge = FakeJudge()
            priming_rationality = Rationality(
                kappa=1.0,
                table=RationalityTable(),
                judge=priming_judge,
                method="noul",
                common_knowledge=["きびだんごを渡すと相手は仲間になりやすい"],
            )
            _run(seed=153, out_dir=root / "priming", rationality=priming_rationality)
            self.assertGreater(priming_judge.calls, 0)
            self.assertGreater(len(priming_rationality.new_entries), 0)

            table_path = root / "rationality.json"
            RationalityTable(priming_rationality.table.to_dict()).save(table_path)

            def _run_from_saved_table(name: str) -> tuple[Path, Rationality]:
                rationality = Rationality(
                    kappa=1.0,
                    table=RationalityTable.load(table_path),
                    judge=RefusingJudge(),
                    method="noul",
                    common_knowledge=["きびだんごを渡すと相手は仲間になりやすい"],
                )
                path = _run(seed=153, out_dir=root / name, rationality=rationality)
                return path, rationality

            path_a, rationality_a = _run_from_saved_table("run_a")
            path_b, rationality_b = _run_from_saved_table("run_b")

            self.assertEqual(path_a.read_bytes(), path_b.read_bytes())
            self.assertEqual(rationality_a.meta["judge_calls"], 0)
            self.assertEqual(rationality_b.meta["judge_calls"], 0)
            self.assertEqual(rationality_a.new_entries, {})
            self.assertEqual(rationality_b.new_entries, {})


class RationalityTableRoundTripTests(unittest.TestCase):
    """Plan §2 item 4: table JSON round trip, and evolve()'s
    generation-end merge of rationality-new.jsonl files into the master
    table."""

    def test_load_save_hash_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rationality.json"
            self.assertEqual(RationalityTable.load(path).to_dict(), {})

            table = RationalityTable({"b": 0.2, "a": 0.1})
            table.save(path)
            reloaded = RationalityTable.load(path)
            self.assertEqual(reloaded.to_dict(), {"a": 0.1, "b": 0.2})
            self.assertEqual(reloaded.hash, table.hash)
            # sort_keys=True: byte-stable regardless of insertion order.
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                json.dumps({"a": 0.1, "b": 0.2}, ensure_ascii=False, sort_keys=True) + "\n",
            )

    def test_evolve_merges_per_individual_new_entries_into_master_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            generation_dir = Path(temporary) / "g0"
            (generation_dir / "ind-0").mkdir(parents=True)
            (generation_dir / "ind-1").mkdir(parents=True)
            (generation_dir / "ind-0" / "rationality-new.jsonl").write_text(
                json.dumps({"key": "k1", "p": 0.4}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (generation_dir / "ind-1" / "rationality-new.jsonl").write_text(
                "\n".join(
                    json.dumps(row, ensure_ascii=False)
                    for row in (
                        {"key": "k1", "p": 0.4},  # same key/value: harmless overlap
                        {"key": "k2", "p": 0.9},
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            table_path = Path(temporary) / "rationality.json"
            existing = RationalityTable({"k0": 0.1})
            existing.save(table_path)

            merged = _merge_rationality_table(generation_dir, table_path)

            self.assertEqual(merged.to_dict(), {"k0": 0.1, "k1": 0.4, "k2": 0.9})
            self.assertEqual(
                RationalityTable.load(table_path).to_dict(), merged.to_dict()
            )


if __name__ == "__main__":
    unittest.main()
