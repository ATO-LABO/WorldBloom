"""Exact compatibility oracle frozen before the quality breakdown refactor.

Source: gapengine/qd.py at e2f0ecc5cf62577d8bdc93a00da572e68083b2f2.
The legacy functions below intentionally duplicate the old implementation and
must not call production helpers: differential checks need an independent oracle.
"""
from __future__ import annotations

import copy
import json
import random
import unittest
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from gapengine.qd import quality, quality_breakdown

ROOT = Path(__file__).resolve().parents[1]

def _legacy_protagonist(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        if row.get("kind") == "header":
            value = row.get("protagonist")
            return str(value) if value is not None else None
    return None


def _legacy_sign_flips(values: Iterable[float]) -> int:
    prior: int | None = None
    flips = 0
    for value in values:
        current = 1 if value > 0.0 else -1 if value < 0.0 else 0
        if current == 0:
            continue
        if prior is not None and current != prior:
            flips += 1
        prior = current
    return flips


def _legacy_decision_target(row: Mapping[str, Any]) -> str:
    args = row.get("args", [])
    if isinstance(args, list) and args:
        return str(args[0])
    return "-"


def _legacy_repetition_penalty(
    decisions: Sequence[Mapping[str, Any]],
) -> float:
    penalty_count = 0
    run_key: tuple[str, str] | None = None
    run_length = 0
    for row in decisions:
        key = (str(row.get("verb")), _legacy_decision_target(row))
        if key == run_key:
            run_length += 1
        else:
            run_key = key
            run_length = 1
        if run_length == 3:
            penalty_count += 1
    return 0.05 * penalty_count


def _legacy_completed_prerequisite_chains(
    rows: Sequence[Mapping[str, Any]],
) -> int:
    protagonist = _legacy_protagonist(rows)
    if protagonist is None:
        return 0

    observed: set[tuple[str, str]] = set()
    completed_observations: set[tuple[str, str]] = set()
    pledged: set[frozenset[str]] = set()
    negotiated: set[tuple[str, str]] = set()
    completed_pledges: set[frozenset[str]] = set()
    completed_negotiations: set[tuple[str, str]] = set()
    completed = 0

    for row in rows:
        kind = row.get("kind")
        verb = str(row.get("verb", ""))
        subject = str(row.get("subject", ""))
        details = row.get("details")
        if not isinstance(details, Mapping):
            details = {}

        if kind == "decision":
            if row.get("result") == "invalid":
                continue
            target = _legacy_decision_target(row)

            if verb == "observe" and subject == protagonist:
                observed.add((subject, target))
            elif verb == "neutralize" and subject == protagonist:
                pair = (subject, target)
                if (
                    pair in observed
                    and pair not in completed_observations
                ):
                    completed += 1
                    completed_observations.add(pair)
            elif verb == "pledge" and subject == protagonist:
                pledged.add(frozenset((subject, target)))
            elif verb == "negotiate" and subject == protagonist:
                negotiated.add((subject, target))
            elif verb == "concede" and target == protagonist:
                pair = (target, subject)
                if (
                    pair in negotiated
                    and pair not in completed_negotiations
                ):
                    completed += 1
                    completed_negotiations.add(pair)

        elif (
            kind == "event"
            and verb == "betrayal"
            and subject == protagonist
        ):
            target = details.get("target")
            if not isinstance(target, str):
                continue
            pair = frozenset((subject, target))
            if pair in pledged and pair not in completed_pledges:
                completed += 1
                completed_pledges.add(pair)

    return completed


def _legacy_dangling_effects(
    rows: Sequence[Mapping[str, Any]],
) -> int:
    for row in reversed(rows):
        details = row.get("details")
        if isinstance(details, Mapping):
            value = details.get("dangling_effects")
            if value is not None:
                return max(0, int(value))

        value = row.get("dangling_effects")
        if value is not None:
            return max(0, int(value))

    return 0


def _legacy_quality(
    rows: Sequence[Mapping[str, Any]],
    world_meta: Mapping[str, Any],
) -> float:
    protagonist = str(
        world_meta.get("protagonist") or _legacy_protagonist(rows) or ""
    )
    objective_changes = 0
    affinity_change = 0.0
    revived = 0
    learning = 0
    dramatic_turns = 0
    strength_diffs: list[float] = []

    protagonist_decisions = [
        row
        for row in rows
        if row.get("kind") == "decision"
        and row.get("subject") == protagonist
    ]

    for row in rows:
        delta = row.get("delta")
        if isinstance(delta, Mapping):
            objective = delta.get("objective")
            if isinstance(objective, Mapping):
                objective_changes += len(objective)
            relations = delta.get("relations")
            if isinstance(relations, list):
                for relation in relations:
                    if not isinstance(relation, Mapping):
                        continue
                    if protagonist in {
                        str(relation.get("observer")),
                        str(relation.get("target")),
                    }:
                        affinity_change += abs(
                            float(relation.get("affinity", 0.0))
                        )

        if (
            row.get("kind") == "event"
            and row.get("verb") == "revived"
            and row.get("subject") == protagonist
        ):
            revived += 1

        if (
            row.get("kind") == "event"
            and row.get("verb") in {"betrayal", "exposure"}
        ):
            dramatic_turns += 1

        if (
            row.get("kind") == "event"
            and row.get("verb") == "learn_fact"
            and row.get("subject") == protagonist
        ):
            learning += 1

        if (
            row.get("kind") == "decision"
            and row.get("verb") == "observe"
            and row.get("subject") == protagonist
        ):
            learning += 1

        details = row.get("details")
        if isinstance(details, Mapping):
            if (
                row.get("kind") == "event"
                and row.get("verb") == "payoff"
                and details.get("mode") == "chosen"
            ):
                dramatic_turns += 1
            if (
                row.get("kind") == "decision"
                and row.get("verb") == "concede"
                and details.get("mode") == "goodwill"
                and row.get("result") != "invalid"
            ):
                dramatic_turns += 1
            if (
                row.get("kind") == "event"
                and row.get("verb") == "rethink"
                and row.get("subject") == protagonist
            ):
                before = details.get("before")
                after = details.get("after")
                if isinstance(before, Mapping) and isinstance(after, Mapping):
                    for fact_id in sorted(set(before) | set(after)):
                        before_belief = before.get(fact_id)
                        after_belief = after.get(fact_id)
                        before_value = (
                            before_belief.get("value")
                            if isinstance(before_belief, Mapping)
                            else None
                        )
                        after_value = (
                            after_belief.get("value")
                            if isinstance(after_belief, Mapping)
                            else None
                        )
                        if before_value != after_value:
                            dramatic_turns += 1
            if "strength_diff" in details:
                strength_diffs.append(
                    float(details["strength_diff"])
                )

    completed_chains = _legacy_completed_prerequisite_chains(rows)
    metrics = (
        min(1.0, objective_changes / 2.0),
        min(1.0, affinity_change / 3.0),
        min(1.0, revived / 2.0),
        min(1.0, _legacy_sign_flips(strength_diffs) / 2.0),
        min(1.0, learning / 3.0),
        min(1.0, dramatic_turns / 2.0),
        min(1.0, completed_chains / 2.0),
    )
    score = sum(metrics) / len(metrics)

    score -= _legacy_repetition_penalty(protagonist_decisions)
    if protagonist_decisions:
        stalls = sum(
            row.get("verb") in {"rest", "withdraw"}
            for row in protagonist_decisions
        )
        score -= (stalls / len(protagonist_decisions)) * 0.3

    score -= 0.05 * _legacy_dangling_effects(rows)
    return round(min(1.0, max(0.0, score)), 12)


def decision(verb, target="B", subject="A", **extra):
    return {"kind": "decision", "verb": verb, "subject": subject,
            "args": [target], **extra}


def event(verb, subject="A", **extra):
    return {"kind": "event", "verb": verb, "subject": subject, **extra}


def saturated_rows():
    return [
        {"kind": "header", "protagonist": "A"},
        {"delta": {"objective": {"x": 1, "y": 2, "z": 3},
                   "relations": [{"observer": "A", "affinity": -4}]}},
        event("revived"), event("revived"), event("revived"),
        *[{"details": {"strength_diff": n}} for n in (1, 0, -1, 0, 1, -1)],
        decision("observe"), decision("neutralize"),
        event("learn_fact"), event("learn_fact"), event("learn_fact"),
        event("betrayal"), event("exposure"), event("payoff", details={"mode": "chosen"}),
        decision("pledge"), event("betrayal", details={"target": "B"}),
        decision("negotiate"), decision("concede", target="A", subject="B"),
    ]


def synthetic_cases():
    rng = random.Random(731502)
    for _ in range(300):
        rows = [{"kind": "header", "protagonist": rng.choice(("A", "B", None))}]
        for _ in range(rng.randrange(65)):
            row = {"kind": rng.choice(("decision", "event", "snapshot", "other")),
                   "subject": rng.choice(("A", "B", None)),
                   "verb": rng.choice(("observe", "neutralize", "pledge", "betrayal",
                       "negotiate", "concede", "rest", "withdraw", "revived",
                       "learn_fact", "exposure", "payoff", "rethink", None)),
                   "args": rng.choice((["A"], ["B", "C"], [], None, "B", ("A",))),
                   "result": rng.choice(("invalid", "ok", None)),
                   "effective": rng.choice((True, False, None))}
            if rng.random() < 0.8:
                row["details"] = {"strength_diff": rng.choice((-2, -0.1, 0, 0.3, 4)),
                    "mode": rng.choice(("chosen", "goodwill", None)),
                    "target": rng.choice(("A", "B", None)),
                    "before": {"f": {"value": rng.choice((True, False, None))}},
                    "after": {"f": {"value": rng.choice((True, False, None))}}}
                if rng.random() < 0.2:
                    row["details"]["dangling_effects"] = rng.choice((-1, 0, 1, 3, None))
            else:
                row["details"] = rng.choice((None, [], ""))
            if rng.random() < 0.5:
                row["delta"] = {"objective": dict.fromkeys("xyz"[:rng.randrange(4)], None),
                    "relations": [None, {"observer": rng.choice(("A", "B")),
                        "target": "B", "affinity": rng.choice((-0.1, 0, 0.2, 2))}]}
            if rng.random() < 0.1:
                row["dangling_effects"] = rng.choice((-1, 0, 1, 2, None))
            rows.append(row)
        yield rows, {"protagonist": rng.choice(("A", "B", "", None))}


class QualityCharacterizationTests(unittest.TestCase):
    def assert_compatible(self, rows, meta):
        before = copy.deepcopy((rows, meta))
        expected = _legacy_quality(rows, meta)
        actual = quality(rows, meta)
        breakdown = quality_breakdown(rows, meta)
        self.assertIs(type(actual), float)
        self.assertEqual(actual, expected)
        self.assertEqual(breakdown.total, expected)
        score = breakdown.aggregate
        score -= breakdown.penalties.repetition
        if breakdown.counts.protagonist_decisions:
            score -= breakdown.penalties.stalls
        score -= breakdown.penalties.dangling_effects
        self.assertEqual(breakdown.penalized_total, score)
        self.assertEqual(breakdown.total, round(min(1.0, max(0.0, score)), 12))
        self.assertEqual((rows, meta), before)

    def test_empty_and_missing_values(self):
        for rows in ([], [{}], [{"kind": "header"}],
                     [{"delta": None, "details": None}],
                     [{"kind": "event", "verb": "Melos narrative action"}]):
            self.assertEqual(quality(rows, {}), 0.0)
            self.assert_compatible(rows, {})

    def test_each_metric_and_saturation(self):
        cases = [
            ([{"delta": {"objective": {"x": None}}}], 0.071428571429),
            ([{"delta": {"relations": [{"observer": "A", "affinity": -1.5}]}}], 0.071428571429),
            ([event("revived")], 0.071428571429),
            ([{"details": {"strength_diff": n}} for n in (1, 0, -1)], 0.071428571429),
            ([event("learn_fact")], 0.047619047619),
            ([event("exposure", subject="B")], 0.071428571429),
            ([decision("pledge"), event("betrayal", details={"target": "B"})], 0.142857142857),
        ]
        for tail, expected in cases:
            rows = [{"kind": "header", "protagonist": "A"}, *tail]
            self.assertEqual(quality(rows, {}), expected)
            self.assert_compatible(rows, {})
        self.assertEqual(quality(saturated_rows(), {}), 1.0)
        self.assert_compatible(saturated_rows(), {})

    def test_repetition_threshold_first_arg_and_stalls(self):
        for repeats in range(1, 8):
            rows = saturated_rows() + [decision("train", args=["B", str(i)],
                result="invalid", effective=False) for i in range(repeats)]
            self.assertEqual(quality(rows, {}), 1.0 if repeats < 3 else 0.95)
            self.assert_compatible(rows, {})
        for tail in ([decision("rest"), decision("withdraw")],
                     [decision("train", args=None)] * 5,
                     [decision("train", args=("B",))] * 5,
                     [decision("train")] * 3 + [decision("other")] + [decision("train")] * 3):
            self.assert_compatible(saturated_rows() + tail, {})
        self.assertEqual(quality([decision("rest", result="invalid")], {"protagonist": "A"}), 0.0)

    def test_chains_invalid_effective_and_protagonist_precedence(self):
        rows = [{"kind": "header", "protagonist": "A"},
            decision("observe", result="invalid", effective=False),
            decision("neutralize", effective=False),
            decision("observe", effective=False), decision("neutralize", effective=False),
            decision("neutralize"), decision("pledge"),
            event("betrayal", details={"target": "B"}),
            event("betrayal", details={"target": "B"}),
            decision("negotiate"), decision("concede", target="A", subject="B", result="invalid"),
            decision("concede", target="A", subject="B", details={"mode": "goodwill"})]
        self.assertEqual(_legacy_completed_prerequisite_chains(rows), 3)
        for meta in ({}, {"protagonist": "B"}, {"protagonist": None}):
            self.assert_compatible(rows, meta)
        # Chains still follow the header even when other scoring uses world_meta.
        self.assertEqual(quality(rows, {"protagonist": "C"}), 0.285714285714)

    def test_dangling_lookup_clamp_and_rounding(self):
        for tail, dangling in (([{"dangling_effects": 2, "details": {"dangling_effects": 1}}], 1),
            ([{"details": {"dangling_effects": 8}}, {"dangling_effects": 2}], 2),
            ([{"dangling_effects": 2}, {"details": {"dangling_effects": None}}], 2),
            ([{"details": {"dangling_effects": -1}}], 0),
            ([{"dangling_effects": "30"}], 30)):
            rows = saturated_rows() + tail
            self.assertEqual(quality(rows, {}), round(max(0.0, 1.0 - 0.05 * dangling), 12))
            self.assert_compatible(rows, {})

    def test_fixed_repository_logs(self):
        paths = sorted((ROOT / "samples").rglob("layers.jsonl"))
        self.assertEqual(len(paths), 25)
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                self.assert_compatible(rows, {})
                self.assert_compatible(rows, {"protagonist": "other"})

    def test_deterministic_synthetic_differential(self):
        for index, (rows, meta) in enumerate(synthetic_cases()):
            with self.subTest(case=index):
                self.assert_compatible(rows, meta)


class QualityBreakdownTests(unittest.TestCase):
    def test_named_metrics_below_saturation(self):
        cases = (
            ("objective_changes", [{"delta": {"objective": {"x": None}}}], 0.5),
            ("affinity_change", [{"delta": {"relations": [
                {"observer": "A", "affinity": -0.75}]}}], 0.25),
            ("revived", [event("revived")], 0.5),
            ("strength_reversals", [{"details": {"strength_diff": n}}
                                    for n in (1, 0, -1)], 0.5),
            ("learning", [event("learn_fact")], 1 / 3.0),
            ("dramatic_turns", [event("exposure", subject="B")], 0.5),
            ("completed_chains", [decision("negotiate"),
                decision("concede", target="A", subject="B")], 0.5),
        )
        for name, tail, expected in cases:
            with self.subTest(metric=name):
                result = quality_breakdown([
                    {"kind": "header", "protagonist": "A"}, *tail], {})
                self.assertEqual(getattr(result.metrics, name), expected)
                self.assertEqual(sum(result.metrics), expected)

    def test_named_raw_metrics_and_penalties(self):
        rows = saturated_rows() + [decision("rest")] * 5 + [
            {"dangling_effects": 2, "details": {"dangling_effects": 1}}]
        result = quality_breakdown(rows, {})
        self.assertEqual(result.counts.objective_changes, 3)
        self.assertEqual(result.counts.affinity_change, 4.0)
        self.assertEqual(result.counts.revived, 3)
        self.assertEqual(result.counts.strength_reversals, 3)
        self.assertEqual(result.counts.learning, 4)
        self.assertEqual(result.counts.dramatic_turns, 4)
        self.assertEqual(result.counts.completed_chains, 3)
        self.assertEqual(result.counts.repetition_runs, 1)
        self.assertEqual(result.counts.protagonist_decisions, 9)
        self.assertEqual(result.counts.stalls, 5)
        self.assertEqual(result.counts.dangling_effects, 1)
        self.assertEqual(tuple(result.metrics), (1.0,) * 7)
        self.assertEqual(result.aggregate, 1.0)
        self.assertEqual(result.penalties.repetition, 0.05)
        self.assertEqual(result.penalties.stalls, (5 / 9) * 0.3)
        self.assertEqual(result.penalties.dangling_effects, 0.05)
        self.assertEqual(result.total, _legacy_quality(rows, {}))

    def test_empty_breakdown_is_read_only_at_every_level(self):
        result = quality_breakdown([], {})
        self.assertEqual(tuple(result.metrics), (0.0,) * 7)
        self.assertEqual(tuple(result.penalties), (0.0,) * 3)
        self.assertEqual(result.aggregate, 0.0)
        for value in (result, result.counts, result.metrics, result.penalties):
            with self.assertRaises(AttributeError):
                setattr(value, value._fields[0], 99)
            with self.assertRaises(TypeError):
                value[0] = 99

    def test_filtering_differences_remain_visible(self):
        rows = [{"kind": "header", "protagonist": "A"},
                decision("observe", result="invalid", effective=False),
                decision("neutralize", effective=False),
                decision("concede", subject="B", details={"mode": "goodwill"}),
                event("rethink", details={"before": {"x": {"value": False}},
                                          "after": {"x": {"value": True}}})]
        result = quality_breakdown(rows, {})
        self.assertEqual(result.counts.learning, 1)
        self.assertEqual(result.counts.completed_chains, 0)
        self.assertEqual(result.counts.dramatic_turns, 2)
        # The chain helper takes the first header, independently of world_meta.
        rows[1].pop("result")
        result = quality_breakdown(rows, {"protagonist": "B"})
        self.assertEqual(result.counts.learning, 0)
        self.assertEqual(result.counts.completed_chains, 1)
        self.assertEqual(result.counts.protagonist_decisions, 1)
