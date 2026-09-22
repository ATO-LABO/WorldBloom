"""WB-JEV-001 Stage 2 acceptance tests (design by Fable, 2026-09-18) for
gapengine/rationality.py's RationalityTable/Rationality and their wiring
into Policy/Simulation/evolve. Uses a FakeJudge (deterministic sha256-based
p, no network) throughout -- Ollama itself is out of scope here (GPU is
unavailable on this machine; see the Stage 2 plan's real-machine section,
skipped by instruction)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any, Sequence
from unittest.mock import patch

import yaml

from engine.actions import Action
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine import gpu_guard
from gapengine.evolve import _merge_rationality_table, evolve
from gapengine.genome import Genome
from gapengine.policy import Policy
from gapengine.rationality import (
    NullJudge,
    OllamaLogprobJudge,
    Rationality,
    RationalityTable,
    _even_chunks,
    _llama_server_call,
    _read_gpu_temperature,
    _top_logprobs,
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


def action_cfg() -> dict[str, Any]:
    return yaml.safe_load((TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8"))


def rules_cfg() -> list[dict[str, Any]]:
    return yaml.safe_load((TEMPLATE / "rules.yaml").read_text(encoding="utf-8"))


class FakeJudge:
    """Deterministic, network-free stand-in for OllamaLogprobJudge: p is a
    sha256 digest of (context, candidate), so two different renders never
    collide by accident and the same pair always yields the same p
    (mirrors Ollama's temperature-0 determinism). ``calls`` counts
    *invocations* of score() (one per decision point that needed a call);
    the returned ``calls_made`` (candidate-unit, Opus review R3) is what
    feeds Rationality's own judge_calls tally -- the two are deliberately
    different units, matching real judges (one Ollama call per candidate)."""

    backend_name = "fake"
    model = "fake-v1"

    def __init__(self, *, none_for: frozenset[str] = frozenset()) -> None:
        self.calls = 0
        self._none_for = none_for

    def score(
        self,
        context_text: str,
        candidates: Sequence[str],
        *,
        max_calls: int | None = None,
    ) -> tuple[list[float | None], int, bool]:
        self.calls += 1
        candidates = list(candidates)
        scored = len(candidates) if max_calls is None else min(len(candidates), max_calls)
        truncated = max_calls is not None and scored < len(candidates)
        results = [
            self._p(context_text, candidate) if index < scored else None
            for index, candidate in enumerate(candidates)
        ]
        return results, scored, truncated

    def _p(self, context_text: str, candidate: str) -> float | None:
        if any(marker in candidate for marker in self._none_for):
            return None
        digest = hashlib.sha256(f"{context_text}|{candidate}".encode("utf-8")).hexdigest()
        return (int(digest[:8], 16) % 1000) / 1000.0


class AlwaysNoneJudge:
    """Every call comes back fully empty (a stand-in for a systemically
    broken backend) -- used to drive Rationality's circuit breaker (P1)."""

    backend_name = "fake-always-none"
    model = None

    def __init__(self) -> None:
        self.calls = 0

    def score(
        self,
        context_text: str,
        candidates: Sequence[str],
        *,
        max_calls: int | None = None,
    ) -> tuple[list[float | None], int, bool]:
        self.calls += 1
        candidates = list(candidates)
        return [None] * len(candidates), len(candidates), False


class ExplodingJudge:
    """Fails the test loudly if ever called -- used to prove a code path
    that must be judge-call-free (kappa<=0, or a fully-cached table) really
    is one, instead of trusting a call counter that could itself have a
    bug."""

    backend_name = "test-exploding"
    model = None

    def score(
        self,
        context_text: str,
        candidates: Sequence[str],
        *,
        max_calls: int | None = None,
    ) -> tuple[list[float | None], int, bool]:
        raise AssertionError("judge.score must not be called on this path")


class RefusingJudge(FakeJudge):
    """Same backend_name/model as FakeJudge (so a fully-cached-table run's
    header stays byte-identical to the run that populated the table), but
    explodes if actually asked to score -- proves the second run makes zero
    judge calls without a call counter alone doing the proving."""

    def score(
        self,
        context_text: str,
        candidates: Sequence[str],
        *,
        max_calls: int | None = None,
    ) -> tuple[list[float | None], int, bool]:
        raise AssertionError("judge.score must not be called when the table already covers every key")


class RecordingJudge:
    """Records exactly the candidate list it was handed (order included) --
    used to prove the choice-mode prompt/label order doesn't depend on the
    incidental order Policy happened to list actions in (Opus review R5(a))."""

    backend_name = "fake"
    model = "fake-v1"

    def __init__(self) -> None:
        self.received: list[str] | None = None

    def score(
        self,
        context_text: str,
        candidates: Sequence[str],
        *,
        max_calls: int | None = None,
    ) -> tuple[list[float | None], int, bool]:
        self.received = list(candidates)
        return [0.5] * len(candidates), len(candidates), False


def _run(
    *,
    seed: int,
    out_dir: Path,
    rationality: Rationality | None,
    genome: Genome | None = None,
    rules: list[dict[str, Any]] = (),
) -> Path:
    world, subjects = load_fixture()
    world.days = 2
    policy = Policy(
        genome if genome is not None else Genome.neutral(),
        precedent=None,
        rules=rules,
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


def _three_candidates() -> tuple[Subject, World, list[Subject], list[tuple[Action, float]]]:
    world, subjects = load_fixture()
    actor = subjects[world.protagonist]
    present = world.present_subjects(actor.zone)
    weighted = [
        (Action("rest"), 1.0),
        (Action("move", ("森",)), 1.0),
        (Action("move", ("道中",)), 1.0),
    ]
    return actor, world, present, weighted


class RationalityMultiplierTests(unittest.TestCase):
    """Plan §2 item 2: the multiplier formula and meta recording."""

    def test_multiplier_matches_p_over_mean_formula(self) -> None:
        actor, world, present, weighted = _three_candidates()
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
        actor, world, present, weighted = _three_candidates()
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


class CandidateLabelsAndNegotiateOfferWiringTests(unittest.TestCase):
    """WB-JEV-004 Stage 4b addendum: Rationality threads candidate_labels
    and describe_negotiate_offer through to describe_candidate_coarse
    (multipliers()'s render step) exactly like describe_trial_grants --
    same default (empty dict / False) no-op, same pass-through. FakeJudge
    (network-free, deterministic) throughout -- no Ollama/GPU involved,
    matching this stage's "kappa=0 or backend fake only" verification
    rule."""

    def test_default_is_a_no_op(self) -> None:
        actor, world, present, _weighted = _three_candidates()
        judge = RecordingJudge()
        rationality = Rationality(
            kappa=1.0,
            table=RationalityTable(),
            judge=judge,
            method="noul",
        )
        action = Action("craft", ("鉄砲",), {"item": "鉄砲"})
        rationality.multipliers(actor, world, present, [action])
        self.assertEqual(judge.received, ["作った（鉄砲）"])

    def test_candidate_labels_replaces_the_rendered_description(self) -> None:
        actor, world, present, _weighted = _three_candidates()
        judge = RecordingJudge()
        rationality = Rationality(
            kappa=1.0,
            table=RationalityTable(),
            judge=judge,
            method="noul",
            candidate_labels={"craft:鉄砲": "道中の商人から小判3枚で鉄砲を買った"},
        )
        action = Action("craft", ("鉄砲",), {"item": "鉄砲"})
        rationality.multipliers(actor, world, present, [action])
        self.assertEqual(judge.received, ["道中の商人から小判3枚で鉄砲を買った"])

    def test_describe_negotiate_offer_extends_the_rendered_description(self) -> None:
        # _three_candidates()'s default present (桃太郎 starts in 村) never
        # includes 鬼 (entry 鬼ヶ島) -- put both in the same zone directly so
        # _peer_role_text can actually resolve 鬼's role.
        world, subjects = load_fixture()
        actor = subjects[world.protagonist]
        oni = subjects[world.antagonist]
        actor.zone = "鬼ヶ島"
        oni.zone = "鬼ヶ島"
        present = [actor, oni]
        # actor (桃太郎) holds 勾玉 by default (lootable, has a modifier);
        # 鬼 (the antagonist) doesn't have one -- so it is "差し出せる品".
        judge = RecordingJudge()
        rationality = Rationality(
            kappa=1.0,
            table=RationalityTable(),
            judge=judge,
            method="noul",
            describe_negotiate_offer=True,
        )
        action = Action(
            "negotiate",
            (world.antagonist,),
            {"target": world.antagonist, "objective": actor.goal.target},
        )
        rationality.multipliers(actor, world, present, [action])
        self.assertEqual(
            judge.received, ["宝を譲るよう交渉した（敵対、差し出せる品: 勾玉）"]
        )


class Momotaro2CandidateLabelYamlEvolveWiringTests(unittest.TestCase):
    """WB-JEV-004 Stage 4b: momotaro_plus2's own rationality.yaml declares
    candidate_labels/describe_negotiate_offer -- run evolve()'s whole
    cfg-resolution -> Rationality pipeline against it with backend "fake"
    (no Ollama/GPU, per this stage's verification rule) to prove
    gapengine.evolve actually loads and threads the two new knobs through
    without raising, the same way it already does for
    describe_trial_grants (see RationalityEvolveWiringTests above)."""

    def test_momotaro_plus2_evolve_run_with_fake_backend_makes_judge_calls(self) -> None:
        project = ROOT / "projects" / "momotaro_plus2"
        template = ROOT / "templates" / "momotaro_plus2"
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "out"
            evolve(
                {
                    "project": project,
                    "template": template,
                    "out": out_dir,
                    "generations": 1,
                    "population": 2,
                    "seeds": 1,
                    "keep": "all",
                    "rationality": {"kappa": 1.0, "backend": "fake"},
                }
            )
            summary_payload = json.loads(
                (out_dir / "summary.json").read_text(encoding="utf-8")
            )
            generation0 = summary_payload["generations"][0]
            self.assertGreater(generation0["rationality_judge_calls"], 0)


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

    def test_save_is_atomic_and_leaves_no_temp_file_on_success(self) -> None:
        """Stage 2 re-review item 1: ``save`` writes a same-directory temp
        file and ``os.replace``s it into place, so Stage 3's several
        experiments reading/writing one shared table never observe a
        partially written file."""

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rationality.json"
            RationalityTable({"k0": 0.1}).save(path)
            RationalityTable({"k0": 0.1, "k1": 0.4}).save(path)

            self.assertEqual(
                RationalityTable.load(path).to_dict(), {"k0": 0.1, "k1": 0.4}
            )
            leftovers = list(Path(temporary).glob("rationality.json.tmp-*"))
            self.assertEqual(leftovers, [])

    def test_save_failure_never_corrupts_the_existing_file(self) -> None:
        """A write that fails partway through (disk full, permission error,
        ...) must leave whatever was already on disk untouched -- the
        replace only happens after the temp file is fully written."""

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rationality.json"
            RationalityTable({"k0": 0.1}).save(path)
            original_bytes = path.read_bytes()

            with patch(
                "gapengine.rationality.Path.write_text",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(OSError):
                    RationalityTable({"k0": 0.1, "k1": 0.9}).save(path)

            self.assertEqual(path.read_bytes(), original_bytes)
            self.assertEqual(RationalityTable.load(path).to_dict(), {"k0": 0.1})

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

    def test_merge_includes_antagonist_subdirectory(self) -> None:
        """Opus review R4: a coevolve run's antagonist-evaluation pass
        re-scores the same protagonist genomes under its own Policy
        instance and writes its own rationality-new.jsonl under
        generation_dir/antagonist/ind-*/ -- the merge must fold that in
        too, not just the main ind-*/ jobs."""

        with tempfile.TemporaryDirectory() as temporary:
            generation_dir = Path(temporary) / "g0"
            (generation_dir / "ind-0").mkdir(parents=True)
            (generation_dir / "antagonist" / "ind-0").mkdir(parents=True)
            (generation_dir / "ind-0" / "rationality-new.jsonl").write_text(
                json.dumps({"key": "k1", "p": 0.4}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (generation_dir / "antagonist" / "ind-0" / "rationality-new.jsonl").write_text(
                json.dumps({"key": "k2", "p": 0.7}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            table_path = Path(temporary) / "rationality.json"
            merged = _merge_rationality_table(generation_dir, table_path)

            self.assertEqual(merged.to_dict(), {"k1": 0.4, "k2": 0.7})

    def test_merge_skips_malformed_lines(self) -> None:
        """Opus review P5: a broken line (partial write, corrupt JSON, a
        row missing "key"/"p") is skipped, not fatal to the whole merge."""

        with tempfile.TemporaryDirectory() as temporary:
            generation_dir = Path(temporary) / "g0"
            (generation_dir / "ind-0").mkdir(parents=True)
            (generation_dir / "ind-0" / "rationality-new.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"key": "k1", "p": 0.4}, ensure_ascii=False),
                        "{not valid json",
                        json.dumps({"key": "k2"}, ensure_ascii=False),  # missing "p"
                        json.dumps({"key": "k3", "p": 0.9}, ensure_ascii=False),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            table_path = Path(temporary) / "rationality.json"
            merged = _merge_rationality_table(generation_dir, table_path)

            self.assertEqual(merged.to_dict(), {"k1": 0.4, "k3": 0.9})


class RationalityBudgetTests(unittest.TestCase):
    """Opus review P3/P4 item 1: truncation, budget_exhausted, and
    candidate-unit counting (R3)."""

    def test_budget_truncates_missing_candidates_and_flags_exhausted(self) -> None:
        actor, world, present, weighted = _three_candidates()
        judge = FakeJudge()
        rationality = Rationality(
            kappa=1.0,
            table=RationalityTable(),
            judge=judge,
            method="noul",
            max_judge_calls=2,
        )
        actions = [action for action, _ in weighted]

        m_list, p_list = rationality.multipliers(actor, world, present, actions)

        self.assertEqual(rationality.meta["judge_calls"], 2)
        self.assertTrue(rationality.meta["budget_exhausted"])
        self.assertEqual(sum(p is None for p in p_list), 1)
        self.assertEqual(sum(m == 1.0 for m in m_list), 1)

    def test_budget_already_at_zero_skips_the_call_entirely(self) -> None:
        actor, world, present, weighted = _three_candidates()
        judge = ExplodingJudge()
        rationality = Rationality(
            kappa=1.0,
            table=RationalityTable(),
            judge=judge,
            method="noul",
            max_judge_calls=0,
        )
        actions = [action for action, _ in weighted]

        m_list, p_list = rationality.multipliers(actor, world, present, actions)

        self.assertTrue(all(p is None for p in p_list))
        self.assertTrue(all(m == 1.0 for m in m_list))
        self.assertTrue(rationality.meta["budget_exhausted"])
        self.assertEqual(rationality.meta["judge_calls"], 0)


class RationalityHeaderTests(unittest.TestCase):
    """Opus review P3/P4 item 2: the header carries only the static
    kappa/method/backend/model/table_hash_at_start subset (R1)."""

    def test_header_has_only_static_fields_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rationality = Rationality(
                kappa=1.0,
                table=RationalityTable(),
                judge=FakeJudge(),
                method="noul",
            )
            path = _run(seed=153, out_dir=Path(temporary), rationality=rationality)

            header = read_rows(path)[0]
            self.assertIn("rationality", header)
            self.assertEqual(
                set(header["rationality"]),
                {"kappa", "method", "backend", "model", "table_hash_at_start"},
            )
            self.assertNotIn("judge_calls", header["rationality"])
            self.assertNotIn("budget_exhausted", header["rationality"])
            self.assertNotIn("judge_disabled", header["rationality"])
            # Meanwhile Rationality's own .meta -- consulted by
            # run_individual/generation_summary, never by the header --
            # does carry it.
            self.assertGreater(rationality.meta["judge_calls"], 0)

    def test_num_ctx_appears_in_header_and_meta_only_when_set(self) -> None:
        """WB-JEV-003: a judge with no num_ctx override must render exactly
        the same header set as before this field existed."""
        with tempfile.TemporaryDirectory() as temporary:
            plain_judge = OllamaLogprobJudge(model="fake-model", method="noul")
            plain_rationality = Rationality(
                kappa=1.0, table=RationalityTable(), judge=plain_judge, method="noul",
            )
            self.assertNotIn("num_ctx", plain_rationality.meta)

            ctx_judge = OllamaLogprobJudge(model="fake-model", method="noul", num_ctx=2048)
            ctx_rationality = Rationality(
                kappa=1.0, table=RationalityTable(), judge=ctx_judge, method="noul",
            )
            empty_response = {"logprobs": [{"top_logprobs": [{"token": "yes", "logprob": -0.1}]}]}
            actor, world, present, weighted = _three_candidates()
            actions = [action for action, _ in weighted]
            with patch("gapengine.rationality._ollama_call", return_value=empty_response):
                path = _run(seed=153, out_dir=Path(temporary), rationality=ctx_rationality)
            self.assertEqual(ctx_rationality.meta["num_ctx"], 2048)
            header = read_rows(path)[0]
            self.assertEqual(header["rationality"]["num_ctx"], 2048)


class OllamaLogprobJudgeNumCtxTests(unittest.TestCase):
    """WB-JEV-003: num_ctx must reach every Ollama call an OllamaLogprobJudge
    makes (noul, choice, and the choice-mode top_logprobs measurement) so a
    judge never triggers a mid-run model reload from a changing context
    size, and must never be sent at all when unset (Ollama's own
    DEFAULT_OPTIONS num_ctx then applies, matching pre-WB-JEV-003 behavior)."""

    def test_ollama_call_puts_num_ctx_in_options_only_when_set(self) -> None:
        from gapengine.rationality import _ollama_call

        captured: dict[str, Any] = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def read(self):
                return json.dumps({"logprobs": []}).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse()

        with patch("gapengine.rationality.urllib.request.urlopen", side_effect=fake_urlopen):
            _ollama_call("m", "p", base_url="http://x", timeout=1.0, num_ctx=2048)
        self.assertEqual(captured["payload"]["options"]["num_ctx"], 2048)

        with patch("gapengine.rationality.urllib.request.urlopen", side_effect=fake_urlopen):
            _ollama_call("m", "p", base_url="http://x", timeout=1.0)
        # num_ctx=None never adds an override -- Ollama's own DEFAULT_OPTIONS
        # num_ctx (16384) still comes through build_request unchanged.
        from gapengine.ollama import DEFAULT_OPTIONS

        self.assertEqual(captured["payload"]["options"]["num_ctx"], DEFAULT_OPTIONS["num_ctx"])

    def test_noul_mode_passes_num_ctx_through(self) -> None:
        judge = OllamaLogprobJudge(model="fake-model", method="noul", num_ctx=2048)
        captured: dict[str, Any] = {}

        def fake_call(model, prompt, *, base_url, timeout, top_logprobs=10, num_ctx=None):
            captured["num_ctx"] = num_ctx
            return {"logprobs": [{"top_logprobs": [{"token": "yes", "logprob": -0.1}]}]}

        with patch("gapengine.rationality._ollama_call", side_effect=fake_call):
            judge.score("ctx", ["a"])
        self.assertEqual(captured["num_ctx"], 2048)

    def test_choice_mode_passes_num_ctx_to_both_the_measurement_and_scoring_calls(self) -> None:
        judge = OllamaLogprobJudge(model="fake-model", method="choice", num_ctx=2048)
        seen: list[int | None] = []

        def fake_call(model, prompt, *, base_url, timeout, top_logprobs=10, num_ctx=None):
            seen.append(num_ctx)
            return {
                "logprobs": [
                    {"top_logprobs": [{"token": "A", "logprob": -0.1}, {"token": "B", "logprob": -0.2}]}
                ]
            }

        with patch("gapengine.rationality._ollama_call", side_effect=fake_call):
            judge.score("ctx", ["c1", "c2"])
        self.assertEqual(seen, [2048, 2048])  # measurement call, then the scoring call

    def test_num_ctx_unset_never_reaches_the_ollama_call(self) -> None:
        judge = OllamaLogprobJudge(model="fake-model", method="noul")
        captured: dict[str, Any] = {}

        def fake_call(model, prompt, *, base_url, timeout, top_logprobs=10, num_ctx=None):
            captured["num_ctx"] = num_ctx
            return {"logprobs": [{"top_logprobs": [{"token": "yes", "logprob": -0.1}]}]}

        with patch("gapengine.rationality._ollama_call", side_effect=fake_call):
            judge.score("ctx", ["a"])
        self.assertIsNone(captured["num_ctx"])


class ChoiceJudgeTests(unittest.TestCase):
    """Opus review P3/P4 item 3: choice-mode key/label order independence,
    even chunking, and the unobserved-label floor (R5)."""

    def test_even_chunks_distributes_the_remainder(self) -> None:
        chunks = _even_chunks(list("ABCDEFG"), 3)
        self.assertEqual([len(chunk) for chunk in chunks], [3, 2, 2])
        self.assertEqual([item for chunk in chunks for item in chunk], list("ABCDEFG"))

    def test_choice_label_order_is_sorted_not_candidate_order(self) -> None:
        actor, world, present, weighted = _three_candidates()
        actions_forward = [action for action, _ in weighted]
        actions_reversed = list(reversed(actions_forward))

        judge_forward = RecordingJudge()
        Rationality(
            kappa=1.0, table=RationalityTable(), judge=judge_forward, method="choice"
        ).multipliers(actor, world, present, actions_forward)

        judge_reversed = RecordingJudge()
        Rationality(
            kappa=1.0, table=RationalityTable(), judge=judge_reversed, method="choice"
        ).multipliers(actor, world, present, actions_reversed)

        self.assertIsNotNone(judge_forward.received)
        self.assertEqual(judge_forward.received, judge_reversed.received)
        self.assertEqual(judge_forward.received, sorted(judge_forward.received))

    def test_floor_unobserved_labels_and_cross_chunk_scaling(self) -> None:
        judge = OllamaLogprobJudge(model="fake-model", method="choice")
        judge._chunk_size = 2  # skip the network measurement call
        candidates = ["c1", "c2", "c3", "c4", "c5"]
        responses = iter(
            [
                # chunk0 (c1,c2): only label A observed.
                {"logprobs": [{"top_logprobs": [{"token": "A", "logprob": -0.1}]}]},
                # chunk1 (c3,c4): both labels observed.
                {
                    "logprobs": [
                        {
                            "top_logprobs": [
                                {"token": "A", "logprob": -0.2},
                                {"token": "B", "logprob": -0.4},
                            ]
                        }
                    ]
                },
                # chunk2 (c5): its only label ("A") never observed at all.
                {"logprobs": [{"top_logprobs": [{"token": "Z", "logprob": -0.1}]}]},
            ]
        )

        with patch(
            "gapengine.rationality._ollama_call",
            side_effect=lambda *args, **kwargs: next(responses),
        ):
            scores, calls_made, truncated = judge.score("ctx", candidates)

        self.assertEqual(calls_made, 3)
        self.assertFalse(truncated)
        self.assertIsNone(scores[4])
        self.assertTrue(all(value is not None for value in scores[:4]))

        # chunk0: "B" is floored to half of "A"'s mass, so the ratio
        # between them is exactly 2:1 regardless of the mass's actual
        # value -- normalized shares of 2/3 and 1/3, each scaled by this
        # chunk's 2-of-5 share of the whole candidate set.
        self.assertAlmostEqual(scores[0], (2 / 3) * (2 / 5), places=6)
        self.assertAlmostEqual(scores[1], (1 / 3) * (2 / 5), places=6)
        self.assertAlmostEqual(scores[0] + scores[1], 2 / 5, places=6)

        # chunk1: both observed -- just check its overall scaled share.
        self.assertAlmostEqual(scores[2] + scores[3], 2 / 5, places=6)

        # chunk2 contributed nothing (fully unobserved) -- the total over
        # every *scored* candidate is 4/5, not 1.0.
        self.assertAlmostEqual(
            sum(value for value in scores if value is not None), 4 / 5, places=6
        )

    def test_budget_caps_chunks_not_candidates(self) -> None:
        judge = OllamaLogprobJudge(model="fake-model", method="choice")
        judge._chunk_size = 2
        candidates = ["c1", "c2", "c3", "c4", "c5"]
        response = {
            "logprobs": [
                {
                    "top_logprobs": [
                        {"token": "A", "logprob": -0.1},
                        {"token": "B", "logprob": -0.2},
                    ]
                }
            ]
        }

        with patch("gapengine.rationality._ollama_call", return_value=response):
            scores, calls_made, truncated = judge.score("ctx", candidates, max_calls=1)

        self.assertEqual(calls_made, 1)
        self.assertTrue(truncated)
        self.assertTrue(all(value is not None for value in scores[:2]))
        self.assertTrue(all(value is None for value in scores[2:]))


class RationalityEvolveWiringTests(unittest.TestCase):
    """Opus review P3/P4 item 4: evolve()'s cfg resolution order (yaml
    default -> cfg override) and generation_summary's two new fields, using
    the "fake" backend injection point so this needs no Ollama/GPU."""

    def test_cfg_override_enables_rationality_and_reports_summary_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "out"
            evolve(
                {
                    "project": PROJECT,
                    "template": TEMPLATE,
                    "out": out_dir,
                    "generations": 1,
                    "population": 2,
                    "seeds": 1,
                    "keep": "all",
                    "rationality": {"kappa": 1.0, "backend": "fake", "method": "choice"},
                }
            )

            summary_payload = json.loads(
                (out_dir / "summary.json").read_text(encoding="utf-8")
            )
            generation0 = summary_payload["generations"][0]
            self.assertGreater(generation0["rationality_judge_calls"], 0)
            self.assertGreater(generation0["rationality_table_size"], 0)

            table = RationalityTable.load(out_dir / "rationality.json")
            self.assertGreater(len(table), 0)

    def test_yaml_default_stays_disabled_without_a_cfg_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "out"
            evolve(
                {
                    "project": PROJECT,
                    "template": TEMPLATE,
                    "out": out_dir,
                    "generations": 1,
                    "population": 2,
                    "seeds": 1,
                    "keep": "all",
                }
            )

            summary_payload = json.loads(
                (out_dir / "summary.json").read_text(encoding="utf-8")
            )
            generation0 = summary_payload["generations"][0]
            self.assertNotIn("rationality_judge_calls", generation0)
            self.assertNotIn("rationality_budget_exhausted_runs", generation0)
            self.assertNotIn("rationality_judge_disabled_runs", generation0)
            self.assertFalse((out_dir / "rationality.json").exists())

    def test_budget_exhaustion_is_counted_in_the_generation_summary(self) -> None:
        """Stage 2 re-review item 2: run_individual already tallies
        rationality_budget_exhausted_runs/rationality_judge_disabled_runs
        per job; generation_summary must sum them across every job (main
        pass here, since this fixture doesn't coevolve) the same way it
        already does for rationality_judge_calls."""

        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "out"
            evolve(
                {
                    "project": PROJECT,
                    "template": TEMPLATE,
                    "out": out_dir,
                    "generations": 1,
                    "population": 2,
                    "seeds": 1,
                    "keep": "all",
                    "rationality": {
                        "kappa": 1.0,
                        "backend": "fake",
                        "method": "noul",
                        # 1: FakeJudge always returns real values for the
                        # candidates it scores, so a single-call budget
                        # truncates without ever making the "all candidates
                        # came back None" circuit-breaker condition true --
                        # isolating budget_exhausted from judge_disabled.
                        "max_judge_calls_per_run": 1,
                    },
                }
            )

            summary_payload = json.loads(
                (out_dir / "summary.json").read_text(encoding="utf-8")
            )
            generation0 = summary_payload["generations"][0]
            self.assertGreater(generation0["rationality_budget_exhausted_runs"], 0)
            self.assertEqual(generation0["rationality_judge_disabled_runs"], 0)


class RationalityNonNeutralGenomeTests(unittest.TestCase):
    """Opus review P3/P4 item 5: the kappa<=0 no-op guarantee, and a real
    trajectory change at kappa>0, must both hold for a genome that was
    never annotation-only to begin with (not just the neutral-genome case
    the earlier tests cover)."""

    def test_kappa_zero_is_byte_identical_for_non_neutral_genome_with_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            genome = Genome.random(random.Random(7))

            plain_path = _run(
                seed=153,
                out_dir=root / "plain",
                rationality=None,
                genome=genome,
                rules=rules_cfg(),
            )
            rationality = Rationality(
                kappa=0.0,
                table=RationalityTable(),
                judge=ExplodingJudge(),
                method="noul",
            )
            annotated_path = _run(
                seed=153,
                out_dir=root / "annotated",
                rationality=rationality,
                genome=genome,
                rules=rules_cfg(),
            )

            self.assertEqual(plain_path.read_bytes(), annotated_path.read_bytes())

    def test_kappa_one_changes_the_trajectory_for_non_neutral_genome_with_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            genome = Genome.random(random.Random(7))

            baseline_path = _run(
                seed=153,
                out_dir=root / "baseline",
                rationality=None,
                genome=genome,
                rules=rules_cfg(),
            )
            rationality = Rationality(
                kappa=1.0,
                table=RationalityTable(),
                judge=FakeJudge(),
                method="noul",
                common_knowledge=["きびだんごを渡すと相手は仲間になりやすい"],
            )
            rational_path = _run(
                seed=153,
                out_dir=root / "rational",
                rationality=rationality,
                genome=genome,
                rules=rules_cfg(),
            )

            self.assertNotEqual(baseline_path.read_bytes(), rational_path.read_bytes())
            self.assertGreater(rationality.meta["judge_calls"], 0)


class RationalityCircuitBreakerTests(unittest.TestCase):
    """Opus review P3/P4 item 6: 3 consecutive fully-empty judge calls
    disable it for the rest of the run (P1)."""

    def test_three_consecutive_empty_calls_disable_the_judge(self) -> None:
        world, subjects = load_fixture()
        actor = subjects[world.protagonist]
        present = world.present_subjects(actor.zone)
        judge = AlwaysNoneJudge()
        rationality = Rationality(
            kappa=1.0, table=RationalityTable(), judge=judge, method="noul"
        )
        actions = [Action("rest")]

        # Table never gets populated (every score is None), so each call
        # below genuinely finds "rest" missing again -- no need for 4
        # distinct decision points to exercise the breaker.
        for _ in range(4):
            rationality.multipliers(actor, world, present, actions)

        self.assertEqual(judge.calls, 3)
        self.assertTrue(rationality.meta["judge_disabled"])
        self.assertEqual(rationality.meta["judge_calls"], 3)

    def test_null_judge_never_trips_the_circuit_breaker(self) -> None:
        """Stage 2 re-review item 3: NullJudge (backend "none") always
        returns all-None by design -- that is the intended "layer enabled,
        no judge" control condition, not a systemic failure, so it must
        never set judge_disabled even after many decision points."""

        world, subjects = load_fixture()
        actor = subjects[world.protagonist]
        present = world.present_subjects(actor.zone)
        judge = NullJudge()
        rationality = Rationality(
            kappa=1.0, table=RationalityTable(), judge=judge, method="noul"
        )
        actions = [Action("rest")]

        for _ in range(10):
            m_list, p_list = rationality.multipliers(actor, world, present, actions)

        self.assertFalse(rationality.meta.get("judge_disabled", False))
        self.assertEqual(p_list, [None])
        self.assertEqual(m_list, [1.0])


class RationalityNeutralGenomeAppliesTests(unittest.TestCase):
    """Opus review P3/P4 item 7 (P2): a bare neutral genome with no rules
    at all still gets m_rat applied to its weight once rationality is
    enabled -- it is no longer treated as annotation-only just because the
    genome and rules happen to be inert."""

    def test_neutral_genome_without_rules_still_gets_m_rat(self) -> None:
        actor, world, present, weighted = _three_candidates()
        judge = FakeJudge()
        rationality = Rationality(
            kappa=1.0, table=RationalityTable(), judge=judge, method="noul"
        )
        policy = Policy(
            Genome.neutral(),
            precedent=None,
            rules=(),
            cfg=action_cfg(),
            rationality=rationality,
        )

        output = policy.reweight(actor, world, present, weighted, turn=0, day=0)

        self.assertEqual(judge.calls, 1)  # multipliers() actually ran
        for action, weight in output:
            m_rat = action.meta["policy"]["m_rat"]
            self.assertAlmostEqual(weight, 1.0 * m_rat, places=9)
        self.assertTrue(any(action.meta["policy"]["m_rat"] != 1.0 for action, _ in output))


class ThermalGuardTests(unittest.TestCase):
    """2026-09-19 thermal-guard addendum: this machine hit 86C and a
    GPU-lost event under sustained Ollama load, so OllamaLogprobJudge can
    pause between calls while the GPU is hot. Exercised entirely through a
    monkeypatched ``_read_gpu_temperature`` and a monkeypatched
    ``time.sleep`` -- no real GPU, no real wait, and no effect on rng or
    determinism (only wall-clock time)."""

    def test_disabled_by_default_never_reads_temperature(self) -> None:
        judge = OllamaLogprobJudge(model="fake-model", method="noul")
        with patch("gapengine.rationality._read_gpu_temperature") as mock_temp:
            judge._maybe_wait_for_thermal_guard()
        mock_temp.assert_not_called()
        self.assertEqual(judge.thermal_wait_seconds, 0.0)

    def test_checks_only_every_check_every_calls(self) -> None:
        judge = OllamaLogprobJudge(
            model="fake-model",
            method="noul",
            thermal_guard={"max_temp": 82, "cooldown_seconds": 45, "check_every": 3},
        )
        # Below max_temp, so once a check does happen it resolves in a
        # single read (this test is only about the check_every gate, not
        # the retry-until-cool loop -- that's covered separately below).
        with patch(
            "gapengine.rationality._read_gpu_temperature", return_value=50.0
        ) as mock_temp:
            judge._maybe_wait_for_thermal_guard()
            judge._maybe_wait_for_thermal_guard()
            self.assertEqual(mock_temp.call_count, 0)
            judge._maybe_wait_for_thermal_guard()
            self.assertEqual(mock_temp.call_count, 1)

    def test_sleeps_while_hot_and_stops_once_cool(self) -> None:
        judge = OllamaLogprobJudge(
            model="fake-model",
            method="noul",
            thermal_guard={"max_temp": 82, "cooldown_seconds": 45, "check_every": 1},
        )
        temperatures = iter([90.0, 85.0, 80.0])  # two hot reads, then cool
        sleeps: list[float] = []

        with patch(
            "gapengine.rationality._read_gpu_temperature",
            side_effect=lambda: next(temperatures),
        ), patch("gapengine.rationality.time.sleep", side_effect=sleeps.append):
            judge._maybe_wait_for_thermal_guard()

        self.assertEqual(sleeps, [45.0, 45.0])
        self.assertEqual(judge.thermal_wait_seconds, 90.0)

    def test_gives_up_after_ten_rounds_if_still_hot(self) -> None:
        judge = OllamaLogprobJudge(
            model="fake-model",
            method="noul",
            thermal_guard={"max_temp": 82, "cooldown_seconds": 1, "check_every": 1},
        )
        with patch(
            "gapengine.rationality._read_gpu_temperature", return_value=95.0
        ), patch("gapengine.rationality.time.sleep") as mock_sleep:
            judge._maybe_wait_for_thermal_guard()

        self.assertEqual(mock_sleep.call_count, 10)
        self.assertEqual(judge.thermal_wait_seconds, 10.0)

    def test_none_temperature_reading_is_treated_as_cool(self) -> None:
        judge = OllamaLogprobJudge(
            model="fake-model",
            method="noul",
            thermal_guard={"max_temp": 82, "cooldown_seconds": 45, "check_every": 1},
        )
        with patch(
            "gapengine.rationality._read_gpu_temperature", return_value=None
        ), patch("gapengine.rationality.time.sleep") as mock_sleep:
            judge._maybe_wait_for_thermal_guard()

        mock_sleep.assert_not_called()
        self.assertEqual(judge.thermal_wait_seconds, 0.0)

    def test_rationality_meta_reports_only_this_seeds_share(self) -> None:
        """The judge may be shared across a job's seeds (evolve.py builds
        it once per job); Rationality.meta must report only the delta
        accrued since *this* Rationality instance was built, not the
        judge's whole-job running total."""

        judge = OllamaLogprobJudge(
            model="fake-model",
            method="noul",
            thermal_guard={"max_temp": 82, "cooldown_seconds": 10, "check_every": 3},
        )
        judge.thermal_wait_seconds = 20.0  # an earlier seed already spent this
        rationality = Rationality(
            kappa=1.0, table=RationalityTable(), judge=judge, method="noul"
        )
        actor, world, present, weighted = _three_candidates()
        actions = [action for action, _ in weighted]
        empty_response = {"logprobs": [{"top_logprobs": []}]}

        with patch(
            "gapengine.rationality._read_gpu_temperature", side_effect=[95.0, 80.0]
        ), patch("gapengine.rationality.time.sleep"), patch(
            "gapengine.rationality._ollama_call", return_value=empty_response
        ):
            rationality.multipliers(actor, world, present, actions)

        self.assertEqual(rationality.meta["thermal_wait_seconds"], 10.0)


class RationalityCliMethodFlagTests(unittest.TestCase):
    """Stage 2 re-review item 4: ``--rationality-method`` lets an experiment
    pick noul vs choice without editing the template's rationality.yaml."""

    def test_rationality_method_flag_reaches_evolve_cfg(self) -> None:
        import scripts.evolve as evolve_script

        args = evolve_script.build_parser().parse_args(
            [
                "--project", str(PROJECT),
                "--template", str(TEMPLATE),
                "--out", "unused",
                "--rationality-method", "choice",
            ]
        )
        self.assertEqual(args.rationality_method, "choice")

        captured: dict[str, Any] = {}

        def fake_evolve(cfg, *, observer=None):
            captured.update(cfg)
            from gapengine.qd import Archive

            return Archive()

        with patch.object(evolve_script, "evolve", fake_evolve):
            evolve_script.main(
                [
                    "--project", str(PROJECT),
                    "--template", str(TEMPLATE),
                    "--out", "unused",
                    "--rationality-method", "choice",
                ]
            )

        self.assertEqual(captured["rationality"]["method"], "choice")

    def test_rationality_method_flag_defaults_to_none(self) -> None:
        import scripts.evolve as evolve_script

        args = evolve_script.build_parser().parse_args(
            [
                "--project", str(PROJECT),
                "--template", str(TEMPLATE),
                "--out", "unused",
            ]
        )
        self.assertIsNone(args.rationality_method)


class RationalityCoevolveWiringTests(unittest.TestCase):
    """Stage 2 re-review item 5: coevolve's antagonist-evaluation pass
    builds a run_individual job whose "genome" is a *protagonist* sample and
    "antagonist_genome" is the coevolving individual under evaluation (see
    evolve()'s antagonist_jobs construction) -- Rationality must attach to
    that job's protagonist-role Policy (Opus review R4), the same as a
    main-pass job.

    This drives run_individual directly with such a job instead of going
    through a full evolve() run: momotaro's default multi-day template
    already gives RationalityHeaderTests/RationalityNonNeutralGenomeTests
    etc. several genuine decision points per run, so starting from an empty
    table (nothing cached yet) deterministically forces real judge calls on
    the *first* run regardless of which genome/antagonist pairing is used --
    unlike a full evolve() generation 0, where the antagonist pass always
    re-visits exactly the same (genome, seed) pair the main pass already
    scored moments earlier under the same coarse-situation cache, so it
    reliably writes *zero* new entries (verified empirically; the coarse,
    name/number-free situation rendering is deliberate cache-sharing design,
    not a bug). Using the real out_dir path shape
    (`generation_dir/antagonist/ind-0/`) still exercises the exact file this
    test needs to prove exists, and folds it through the real
    _merge_rationality_table -- the same two functions evolve() itself
    calls for this wiring."""

    def test_antagonist_shaped_job_writes_and_merges_protagonist_rationality(
        self,
    ) -> None:
        from gapengine.evolve import run_individual
        from gapengine.precedent import PrecedentTable

        world, _subjects = load_fixture()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generation_dir = root / "g0"
            table_path = root / "rationality.json"  # doesn't exist yet: empty table

            job = {
                "action_cfg": action_cfg(),
                "action_graph_path": None,
                "antagonist": world.antagonist,
                "antagonist_action_cfg": action_cfg(),
                # The coevolving individual under evaluation this pass --
                # never given a Rationality (only the protagonist role is).
                "antagonist_genome": Genome.random(random.Random(11)).to_dict(),
                "antagonist_precedent_json": PrecedentTable().to_json(),
                # The protagonist-archive sample being re-scored.
                "genome": Genome.neutral().to_dict(),
                "index": 0,
                "logical_root": str(root),
                "out_dir": str(generation_dir / "antagonist" / "ind-0"),
                "parents": [],
                "precedent_json": PrecedentTable().to_json(),
                "protagonist": world.protagonist,
                "qd_cfg": {
                    "categories": ["I", "II", "III", "IV", "V", "VI"],
                    "volatility_bins": ["low", "mid", "high"],
                },
                "record_explanations": False,
                "rules": rules_cfg(),
                "seeds": [0],
                "subjects_dir": str(PROJECT / "subjects"),
                "target_ending": None,
                "world_path": str(PROJECT / "world.yaml"),
                "rationality_cfg": {
                    "kappa": 1.0,
                    "method": "noul",
                    "backend": "fake",
                    "common_knowledge": [],
                    "key_items": [],
                    "max_judge_calls": None,
                },
                "rationality_table_path": str(table_path),
            }

            result = run_individual(job)

            self.assertGreater(result["rationality_judge_calls"], 0)
            new_entries_path = (
                generation_dir / "antagonist" / "ind-0" / "rationality-new.jsonl"
            )
            self.assertTrue(
                new_entries_path.is_file(),
                "expected the antagonist pass's protagonist-sample job to "
                "have written rationality-new.jsonl",
            )
            new_rows = [
                json.loads(line)
                for line in new_entries_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertGreater(len(new_rows), 0)

            merged = _merge_rationality_table(generation_dir, table_path).to_dict()
            for row in new_rows:
                self.assertIn(row["key"], merged)
                self.assertEqual(merged[row["key"]], row["p"])


class GpuLeaseWiringTests(unittest.TestCase):
    """WB-JEV-001: evolve() must only take the machine-wide GPU lease
    (gapengine.gpu_guard.gpu_lease) when a real Ollama call is actually
    imminent (kappa > 0 and backend == "ollama"), never for kappa<=0,
    "none" or "fake" -- and a GpuBusy from that lease must surface as a
    plain RuntimeError, not propagate raw or get swallowed. Every test
    points WORLDBLOOM_GPU_LEASE_DIR at a throwaway directory so a real
    lease file is never touched, and none of them exercise a real Ollama
    call (_build_rationality_judge is patched wherever backend="ollama")."""

    def setUp(self) -> None:
        self._lease_tempdir = tempfile.TemporaryDirectory()
        self._env_patch = patch.dict(
            os.environ, {"WORLDBLOOM_GPU_LEASE_DIR": self._lease_tempdir.name}
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.addCleanup(self._lease_tempdir.cleanup)

    def _run_evolve(self, out_dir: Path, rationality_override: dict[str, Any] | None) -> None:
        cfg: dict[str, Any] = {
            "project": PROJECT,
            "template": TEMPLATE,
            "out": out_dir,
            "generations": 1,
            "population": 1,
            "seeds": 1,
            "keep": "all",
        }
        if rationality_override is not None:
            cfg["rationality"] = rationality_override
        evolve(cfg)

    def test_kappa_zero_never_takes_the_gpu_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("gapengine.evolve.gpu_guard.gpu_lease") as mock_lease:
                self._run_evolve(Path(temporary) / "out", None)
            mock_lease.assert_not_called()

    def test_backend_none_never_takes_the_gpu_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("gapengine.evolve.gpu_guard.gpu_lease") as mock_lease:
                self._run_evolve(
                    Path(temporary) / "out",
                    {"kappa": 1.0, "backend": "none"},
                )
            mock_lease.assert_not_called()

    def test_backend_fake_never_takes_the_gpu_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("gapengine.evolve.gpu_guard.gpu_lease") as mock_lease:
                self._run_evolve(
                    Path(temporary) / "out",
                    {"kappa": 1.0, "backend": "fake"},
                )
            mock_lease.assert_not_called()

    def test_backend_ollama_takes_the_gpu_lease_around_the_whole_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "out"
            with patch(
                "gapengine.evolve._build_rationality_judge", return_value=NullJudge()
            ), patch(
                "gapengine.evolve.gpu_guard.gpu_lease",
                return_value=contextlib.nullcontext(),
            ) as mock_lease:
                self._run_evolve(
                    out_dir,
                    {"kappa": 1.0, "backend": "ollama"},
                )
            mock_lease.assert_called_once()
            args, kwargs = mock_lease.call_args
            owner = args[0] if args else kwargs["owner"]
            self.assertEqual(owner, f"jev-ga:{out_dir.name}")
            self.assertEqual(kwargs["wait_seconds"], 600)

    def test_gpu_busy_surfaces_as_runtime_error_not_raw_or_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "out"
            with patch(
                "gapengine.evolve._build_rationality_judge", return_value=NullJudge()
            ), patch(
                "gapengine.evolve.gpu_guard.gpu_lease",
                side_effect=gpu_guard.GpuBusy({"owner": "output:some-job"}),
            ):
                with self.assertRaises(RuntimeError) as context:
                    self._run_evolve(
                        out_dir,
                        {"kappa": 1.0, "backend": "ollama"},
                    )
            self.assertNotIsInstance(context.exception, gpu_guard.GpuBusy)
            self.assertIn("output:some-job", str(context.exception))
            # No generation ever ran: preparing for the run must not leave a
            # generation directory behind when the lease could not be taken.
            self.assertFalse((out_dir / "g0").exists())


class ReadGpuTemperatureDelegationTests(unittest.TestCase):
    """WB-JEV-001: gapengine.rationality._read_gpu_temperature must delegate
    to gapengine.gpu_guard.read_gpu_temperature (one nvidia-smi readout
    implementation shared by the thermal guard and the GPU guard), while
    staying monkeypatchable under its own module-level name for
    OllamaLogprobJudge's existing thermal-guard tests."""

    def test_delegates_to_gpu_guard_read_gpu_temperature(self) -> None:
        with patch("gapengine.rationality.gpu_guard.read_gpu_temperature", return_value=71.5) as mock_read:
            self.assertEqual(_read_gpu_temperature(), 71.5)
        mock_read.assert_called_once_with()


class LlamaServerCallTests(unittest.TestCase):
    """WB-JEV-003: ``_llama_server_call`` is ``_ollama_call``'s twin against
    a local llama-server (OpenAI-compatible) endpoint instead. No network or
    GPU -- ``urllib.request.urlopen`` is mocked throughout."""

    class _FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def __enter__(self) -> "LlamaServerCallTests._FakeResponse":
            return self

        def __exit__(self, *exc_info: Any) -> bool:
            return False

        def read(self) -> bytes:
            return self._body

    def _response(self, payload: dict[str, Any]) -> "LlamaServerCallTests._FakeResponse":
        return self._FakeResponse(json.dumps(payload).encode("utf-8"))

    def test_payload_shape(self) -> None:
        captured: dict[str, Any] = {}

        def fake_urlopen(request: Any, timeout: float) -> "LlamaServerCallTests._FakeResponse":
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return self._response(
                {
                    "choices": [
                        {
                            "logprobs": {
                                "content": [
                                    {
                                        "top_logprobs": [
                                            {"token": "A", "logprob": -0.1},
                                            {"token": "B", "logprob": -2.0},
                                        ]
                                    }
                                ]
                            }
                        }
                    ]
                }
            )

        with patch(
            "gapengine.rationality.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            data = _llama_server_call(
                "bonsai2-27b",
                "prompt text",
                base_url="http://127.0.0.1:8089",
                timeout=5.0,
                top_logprobs=15,
            )

        self.assertEqual(captured["url"], "http://127.0.0.1:8089/v1/chat/completions")
        self.assertEqual(captured["timeout"], 5.0)
        payload = captured["payload"]
        self.assertEqual(payload["model"], "bonsai2-27b")
        self.assertEqual(
            payload["messages"], [{"role": "user", "content": "prompt text"}]
        )
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(payload["max_tokens"], 1)
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["seed"], 0)
        self.assertIs(payload["logprobs"], True)
        self.assertEqual(payload["top_logprobs"], 15)
        self.assertIs(payload["cache_prompt"], True)
        self.assertIs(payload["stream"], False)

        # Normalized into the same {"logprobs": [{"top_logprobs": [...]}]}
        # envelope _ollama_call returns, so _top_logprobs works unchanged.
        self.assertEqual(
            _top_logprobs(data),
            [{"token": "A", "logprob": -0.1}, {"token": "B", "logprob": -2.0}],
        )

    def test_malformed_response_normalizes_to_empty_top_logprobs(self) -> None:
        for body in ({"choices": []}, {"choices": [{"logprobs": None}]}, {}):
            with self.subTest(body=body):
                with patch(
                    "gapengine.rationality.urllib.request.urlopen",
                    return_value=self._response(body),
                ):
                    data = _llama_server_call(
                        "m", "p", base_url="http://x", timeout=1.0
                    )
                self.assertEqual(_top_logprobs(data), [])

    def test_http_error_is_a_urlerror_subclass_callers_already_catch(self) -> None:
        with patch(
            "gapengine.rationality.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("http://x", 400, "bad request", None, None),
        ):
            with self.assertRaises(urllib.error.URLError):
                _llama_server_call("m", "p", base_url="http://x", timeout=1.0)

    def test_invalid_json_raises_value_error(self) -> None:
        with patch(
            "gapengine.rationality.urllib.request.urlopen",
            return_value=self._FakeResponse(b"not json"),
        ):
            with self.assertRaises(ValueError):
                _llama_server_call("m", "p", base_url="http://x", timeout=1.0)


if __name__ == "__main__":
    unittest.main()


class LoopbackRewriteTests(unittest.TestCase):
    """Windows resolves ``localhost`` to ::1 first; Ollama listens on IPv4
    only, so each judge call paid a ~2 s connect fallback until the base URL
    was rewritten (measured 2026-09-20)."""

    def test_localhost_becomes_ipv4_loopback_and_others_are_untouched(self):
        from gapengine.rationality import _loopback

        cases = {
            "http://localhost:11434": "http://127.0.0.1:11434",
            "http://localhost": "http://127.0.0.1",
            "https://localhost/x": "https://127.0.0.1/x",
            "http://127.0.0.1:8089": "http://127.0.0.1:8089",
            "http://localhost.example.com:1": "http://localhost.example.com:1",
            "http://myhost:11434": "http://myhost:11434",
        }
        for given, expected in cases.items():
            self.assertEqual(_loopback(given), expected)
