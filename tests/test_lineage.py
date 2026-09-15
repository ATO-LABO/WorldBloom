"""Unit tests for gapengine.lineage (WB-LINEAGE-002).

Most cases use a synthetic experiment (hand-written results.json/archive.json,
no simulation) to pin down ref-resolution and turning-point rules precisely
and fast. `RealRunLineageTests` at the bottom runs an actual small evolve()
to prove the rerun path is byte-deterministic against a still-present
original, and that a pruned/damaged ancestor degrades gracefully instead of
raising.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from test_viewer import _write_json

from gapengine import lineage
from gapengine.genome import CATEGORIES, Genome
from gapengine.qd import Archive, Descriptor, Elite
from viewer.data import RunRepository


def _genome(weights: dict[str, float] | None = None, **scalars: float) -> dict[str, Any]:
    category_weight = {category: 0.5 for category in CATEGORIES}
    if weights:
        category_weight.update(weights)
    return {
        "category_weight": category_weight,
        "risk_tolerance": scalars.get("risk_tolerance", 0.5),
        "stance_shift_bias": scalars.get("stance_shift_bias", 0.0),
        "novelty_drive": scalars.get("novelty_drive", 0.0),
    }


def _run(seed: int, *, quality: float = 0.5, reached: bool = True) -> dict[str, Any]:
    return {
        "allies_at_contest": None,
        "allies_final": 0,
        "category": None,
        "contest_turn": None,
        "effective_sequence": [],
        "engine_hash": "engine",
        "layers_path": f"g0/ind-0/seed-{seed}/layers.jsonl",
        "precedent_hash": "precedent",
        "quality": quality,
        "reached": reached,
        "seed": seed,
        "shaped": quality,
        "volatility": 0.0,
    }


def _result(
    index: int,
    genome: dict[str, Any],
    parents: list[str],
    shaped: float,
    cell: list[str] | None,
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "cell": list(cell) if cell else None,
        "classification_status": "classified" if cell else "not_reached",
        "genome": genome,
        "index": index,
        "parents": list(parents),
        "reach_rate": (
            sum(bool(run["reached"]) for run in runs) / len(runs) if runs else 0.0
        ),
        "runs": runs,
        "shaped": shaped,
    }


def _decision(
    turn: int,
    verb: str,
    args: list[str],
    effective: bool,
    *,
    explanation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "kind": "decision",
        "subject": "主人公",
        "turn": turn,
        "verb": verb,
        "args": args,
        "effective": effective,
    }
    if explanation is not None:
        row["explanation"] = explanation
    return row


class LineageResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "runs"
        self.experiment = self.runs_root / "exp"
        _write_json(self.experiment / "archive.json", {"cells": {}, "volatility_thresholds": {}})
        self.repository = RunRepository(self.runs_root)

    def _write_results(self, generation: int, entries: list[dict[str, Any]]) -> None:
        path = self.experiment / f"g{generation}" / "results.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

    def test_resolve_ind_ref_matches_by_index_field_not_list_position(self) -> None:
        # Deliberately out of order: index 5 first, index 2 second.
        entry_5 = _result(5, _genome(), [], 0.4, None, [_run(0, reached=False)])
        entry_2 = _result(2, _genome(), [], 0.9, ["I", "low"], [_run(0, quality=0.9)])
        self._write_results(3, [entry_5, entry_2])

        node = lineage.resolve_ref(self.repository, self.experiment, "g3/ind-2")
        self.assertEqual(node["index"], 2)
        self.assertEqual(node["quality"], 0.9)
        self.assertEqual(node["cell"], ["I", "low"])

    def test_resolve_archive_ref_picks_highest_quality_across_generations(self) -> None:
        low_g0 = _result(0, _genome(), [], 0.3, ["III", "high"], [_run(0, quality=0.4)])
        high_g0 = _result(1, _genome(), [], 0.3, ["III", "high"], [_run(0, quality=0.9)])
        self._write_results(0, [low_g0, high_g0])
        lower_g1 = _result(0, _genome(), [], 0.3, ["III", "high"], [_run(0, quality=0.5)])
        self._write_results(1, [lower_g1])

        # Even though g1 also has a matching cell, the g0 individual with
        # quality 0.9 wins -- an earlier generation can still be the answer.
        node = lineage.resolve_ref(self.repository, self.experiment, "g1/archive/III-high")
        self.assertEqual((node["generation"], node["index"]), (0, 1))

    def test_resolve_archive_ref_tie_break_prefers_earlier_generation_then_lower_index(
        self,
    ) -> None:
        # Same quality at g0/ind-3 and g1/ind-1: earlier generation wins.
        self._write_results(0, [_result(3, _genome(), [], 0.3, ["II", "low"], [_run(0, quality=0.7)])])
        self._write_results(1, [_result(1, _genome(), [], 0.3, ["II", "low"], [_run(0, quality=0.7)])])

        node = lineage.resolve_ref(self.repository, self.experiment, "g1/archive/II-low")
        self.assertEqual((node["generation"], node["index"]), (0, 3))

    def test_archive_ref_resolution_diverges_from_insert_reach_rate_tiebreak(self) -> None:
        """Documents where gapengine/lineage.py's archive-ref tie-break (earliest
        generation, then lowest index) differs from what Archive.insert()
        actually does at run time (reach_rate breaks a quality tie): the task
        asked for exactly this cross-check. We keep the design's simpler rule
        (see the comment in _resolve_archive_ref) rather than change
        Archive.insert."""

        archive = Archive()
        archive.freeze_thresholds([0.0, 0.2, 0.8])
        descriptor = Descriptor("I", 0.1, archive.bin_for(0.1))
        genome = Genome.neutral()
        earlier = Elite(
            genome=genome, quality=0.5, descriptor=descriptor, reach_rate=0.5,
            exemplar={"layers_path": "g0/ind-0/seed-0/layers.jsonl", "seed": 0},
            generation=0,
        )
        later_same_quality_better_reach = Elite(
            genome=genome, quality=0.5, descriptor=descriptor, reach_rate=1.0,
            exemplar={"layers_path": "g1/ind-0/seed-0/layers.jsonl", "seed": 0},
            generation=1,
        )
        self.assertTrue(archive.insert(earlier))
        self.assertTrue(archive.insert(later_same_quality_better_reach))
        # Archive.insert really did replace g0 with the later, better-reach elite.
        self.assertEqual(archive.cells[("I", "low")].generation, 1)

        genome_dict = genome.to_dict()
        self._write_results(0, [_result(0, genome_dict, [], 0.3, ["I", "low"], [_run(0, quality=0.5)])])
        self._write_results(1, [_result(0, genome_dict, [], 0.3, ["I", "low"], [_run(0, quality=0.5)])])

        node = lineage.resolve_ref(self.repository, self.experiment, "g1/archive/I-low")
        # ...but our resolver picks the EARLIER generation on this tie: a
        # confirmed, deliberate divergence from Archive.insert's own rule.
        self.assertEqual(node["generation"], 0)

    def test_primary_lineage_prefers_nearer_parent_by_genome_distance(self) -> None:
        genome_near = _genome(weights={"I": 0.9})
        genome_far = _genome(weights={"I": 0.1})
        child_genome = _genome(weights={"I": 0.8})  # closer to genome_near

        parent_near = _result(0, genome_near, [], 0.3, None, [_run(0, reached=False)])
        parent_far = _result(1, genome_far, [], 0.3, None, [_run(0, reached=False)])
        self._write_results(0, [parent_near, parent_far])

        child = _result(
            0,
            child_genome,
            ["g0/ind-0", "g0/ind-1"],
            0.3,
            ["I", "low"],
            [_run(7, quality=0.6)],
        )
        self._write_results(1, [child])

        _write_json(
            self.experiment / "archive.json",
            {
                "cells": {
                    "I|low": {
                        "generation": 1,
                        "genome": child_genome,
                        "parents": ["g0/ind-0", "g0/ind-1"],
                        "exemplar": {
                            "layers_path": "g1/ind-0/seed-7/layers.jsonl",
                            "seed": 7,
                        },
                    }
                },
                "volatility_thresholds": {},
            },
        )

        chain = lineage.primary_lineage(self.repository, self.experiment, "I|low")
        self.assertEqual([node["ref"] for node in chain], ["g1/ind-0", "g0/ind-0"])

    def test_primary_lineage_stops_at_immigrant_with_no_parents(self) -> None:
        immigrant = _result(6, _genome(), [], 0.2, ["V", "high"], [_run(3, quality=0.2)])
        self._write_results(0, [immigrant])
        _write_json(
            self.experiment / "archive.json",
            {
                "cells": {
                    "V|high": {
                        "generation": 0,
                        "genome": _genome(),
                        "parents": [],
                        "exemplar": {
                            "layers_path": "g0/ind-6/seed-3/layers.jsonl",
                            "seed": 3,
                        },
                    }
                },
                "volatility_thresholds": {},
            },
        )
        chain = lineage.primary_lineage(self.repository, self.experiment, "V|high")
        self.assertEqual([node["ref"] for node in chain], ["g0/ind-6"])

    def test_missing_cell_raises_lineage_error_not_a_crash(self) -> None:
        with self.assertRaises(lineage.LineageError):
            lineage.primary_lineage(self.repository, self.experiment, "I|low")


class TurningPointTests(unittest.TestCase):
    """_find_turning is a pure function over decision rows -- exercised
    directly for precise control over the effective/ineffective edge cases
    (see the class docstring at the top of this file)."""

    def test_detects_first_effective_divergence_after_skipping_ineffective_one(
        self,
    ) -> None:
        parent = [
            _decision(1, "move", ["村"], True),
            _decision(2, "rest", [], False),  # ineffective divergence: skip
            _decision(3, "fight", ["鬼"], True),  # resyncs with child
            _decision(4, "rest", [], False),
        ]
        child = [
            _decision(1, "move", ["村"], True),
            _decision(2, "train", [], False),  # ineffective divergence: skip
            _decision(3, "fight", ["鬼"], True),  # resyncs with parent
            _decision(4, "withdraw", [], True),  # effective: this is the turning point
        ]

        found = lineage._find_turning(parent, child, "主人公")
        self.assertIsNotNone(found)
        parent_decision, child_decision = found
        self.assertEqual((parent_decision["turn"], parent_decision["verb"]), (4, "rest"))
        self.assertEqual((child_decision["turn"], child_decision["verb"]), (4, "withdraw"))

    def test_returns_none_when_decisions_match_exactly(self) -> None:
        rows = [
            _decision(1, "move", ["村"], True),
            _decision(2, "rest", [], False),
        ]
        self.assertIsNone(lineage._find_turning(rows, list(rows), "主人公"))

    def test_detects_effective_divergence_after_an_ineffective_one_that_never_resyncs(
        self,
    ) -> None:
        # design (WB-LINEAGE-002): an ineffective divergence is just skipped
        # (i += 1, j += 1) -- there is no window search trying to find some
        # later position where the two streams happen to line up again. The
        # same (verb, args) pair ("train") appears in both streams here, but
        # at offset positions (parent[1], child[0]): a nearest-match resync
        # search could wrongly pair those up and then run off the end of the
        # parent list before ever reaching child[1] -- the turning point
        # this test expects to be found.
        parent = [
            _decision(1, "rest", [], False),
            _decision(2, "train", [], False),
        ]
        child = [
            _decision(1, "train", [], False),
            _decision(2, "withdraw", [], True),
        ]
        found = lineage._find_turning(parent, child, "主人公")
        self.assertIsNotNone(found)
        parent_decision, child_decision = found
        self.assertEqual((parent_decision["turn"], parent_decision["verb"]), (2, "train"))
        self.assertEqual((child_decision["turn"], child_decision["verb"]), (2, "withdraw"))

    def test_returns_none_when_ineffective_divergence_runs_to_the_end(self) -> None:
        parent = [_decision(1, "rest", [], False)]
        child = [_decision(1, "train", [], False)]
        self.assertIsNone(lineage._find_turning(parent, child, "主人公"))

    def test_genome_shift_orders_by_absolute_delta_and_excludes_rule_bits(self) -> None:
        before = _genome(weights={"I": 0.5}, risk_tolerance=0.5)
        after = _genome(weights={"I": 0.55}, risk_tolerance=0.9)
        shifts = lineage.genome_shift(before, after)
        self.assertEqual(shifts[0]["key"], "risk_tolerance")
        self.assertAlmostEqual(shifts[0]["delta"], 0.4)
        self.assertEqual(len(shifts), 9)


def _fight_decision(
    *, subject: str, target: str, p_actor: float, result: str, turn: int = 5,
) -> dict[str, Any]:
    winner = subject if result == "won" else target
    loser = target if result == "won" else subject
    return {
        "kind": "decision",
        "subject": subject,
        "turn": turn,
        "verb": "fight",
        "args": [target],
        "result": result,
        "details": {"p_actor": p_actor, "winner": winner, "loser": loser},
    }


class WinProbabilityTests(unittest.TestCase):
    """_win_probability reads the decisive-contest fight's recorded p_actor
    (engine/verbs.py::_fight) directly instead of recomputing anything from
    strength_diff, and must work whichever side's decision row the contest
    turn out to be on (WB-LINEAGE-001: either side may have initiated it)."""

    def test_protagonist_initiated_contest_uses_p_actor_directly(self) -> None:
        rows = [_fight_decision(subject="主人公", target="鬼", p_actor=0.73, result="won")]
        self.assertAlmostEqual(
            lineage._win_probability(rows, "主人公", "鬼"), 0.73,
        )

    def test_antagonist_initiated_contest_uses_one_minus_p_actor(self) -> None:
        # This is the previously-broken path: the antagonist ("鬼") is the
        # fight's actor, so p_actor is *their* win chance and the
        # protagonist's chance is the complement -- the old implementation
        # required subject == protagonist and so always returned None here.
        rows = [_fight_decision(subject="鬼", target="主人公", p_actor=0.2, result="lost")]
        self.assertAlmostEqual(
            lineage._win_probability(rows, "主人公", "鬼"), 0.8,
        )

    def test_ignores_unrelated_decisions_and_non_contest_fights(self) -> None:
        rows = [
            {"kind": "decision", "subject": "主人公", "turn": 1, "verb": "move", "args": ["村"]},
            # A fight against a third party must not be mistaken for the
            # protagonist/antagonist contest.
            _fight_decision(subject="主人公", target="野盗", p_actor=0.99, result="won", turn=2),
            _fight_decision(subject="鬼", target="主人公", p_actor=0.4, result="lost", turn=6),
        ]
        self.assertAlmostEqual(lineage._win_probability(rows, "主人公", "鬼"), 0.6)

    def test_returns_none_when_no_contest_found(self) -> None:
        rows = [{"kind": "decision", "subject": "主人公", "turn": 1, "verb": "move", "args": ["村"]}]
        self.assertIsNone(lineage._win_probability(rows, "主人公", "鬼"))


def _momotaro_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    return root / "projects" / "momotaro", root / "templates" / "momotaro"


class RealRunLineageTests(unittest.TestCase):
    """One real (small) evolve() run, reused across sub-checks below, to keep
    total sim time down while still exercising: byte-deterministic rerun of
    an un-pruned ancestor, a genuine multi-generation ancestor chain (both
    nodes here happen to be `ind-` refs -- `LineageResolutionTests` above
    covers `archive/` refs against synthetic data), an immigrant/g0 cell with
    zero turning points, and a damaged ancestor (missing precedent.json)
    degrading instead of raising.

    ga_seed=1/generations=5/population=8/seeds=2 is pinned exactly because
    GA determinism makes its archive contents (which cells exist, their
    parents, the chosen lineage seed) reproducible -- verified once by hand
    before writing these assertions.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from gapengine.evolve import evolve

        project, template = _momotaro_paths()
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cls.runs_root = root / "runs"
        cls.archive = evolve(
            {
                "ga_seed": 1,
                "generations": 5,
                "keep": "all",  # nothing pruned: every original stays comparable
                "population": 8,
                "project": project,
                "seed_base": 11,
                "seeds": 2,
                "template": template,
                "out": cls.runs_root / "exp1",
                "processes": 1,
                # So the turning-point candidate tables below are exercised
                # against real recorded candidates, not always the empty
                # "record_explanations was off" branch (WB-LINEAGE-002 fix
                # 4). record_distribution never consumes randomness, so this
                # does not change which cells/parents/seed the GA produces.
                "record_explanations": True,
            }
        )
        cls.repository = RunRepository(cls.runs_root)
        cls.experiment = cls.repository.experiment("exp1")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_multi_generation_chain_byte_matches_and_finds_a_turning_point(self) -> None:
        chain = lineage.primary_lineage(self.repository, self.experiment, "II|high")
        self.assertEqual(
            [(node["ref"], node["generation"]) for node in chain],
            [("g2/ind-5", 2), ("g1/ind-0", 1)],
        )

        report = lineage.build_lineage_report(self.repository, self.experiment, "II|high")
        self.assertEqual(report["seed"], 11)
        for entry in report["ancestry"]:
            self.assertIsNone(entry["rerun_error"])
        self.assertEqual(len(report["turnings"]), 1)
        self.assertIsNotNone(report["first_reach_index"])
        self.assertNotIn("personality_series", report)

        # WB-LINEAGE-002 Fable fix: each ancestry node carries its own 9
        # absolute scalars (not just deltas), and each turning's trait_series
        # is that turning's leading gene traced across the *whole* lineage
        # (one value per ancestry node), not one mixed-gene point per step.
        for entry in report["ancestry"]:
            self.assertEqual(set(entry["scalars"]), set(lineage._SCALAR_LABELS))
        turning = report["turnings"][0]
        trait_series = turning["trait_series"]
        self.assertEqual(trait_series["key"], turning["gene_shift"][0]["key"])
        self.assertEqual(trait_series["label"], lineage._SCALAR_LABELS[trait_series["key"]])
        self.assertEqual(len(trait_series["values"]), len(report["ancestry"]))
        self.assertEqual(
            trait_series["values"],
            [entry["scalars"][trait_series["key"]] for entry in report["ancestry"]],
        )

        # WB-LINEAGE-002 fix 4: with record_explanations on, the turning
        # point's candidate tables must show real recorded candidates, not
        # just the empty "not recorded" branch every prior test exercised.
        turning = report["turnings"][0]
        for candidates in (turning["parent_candidates"], turning["child_candidates"]):
            self.assertTrue(candidates)
            for candidate in candidates:
                self.assertIn("verb", candidate)
                self.assertIn("args", candidate)
                self.assertIn("probability", candidate)
                self.assertIn("selected", candidate)

        # keep="all" pruned nothing, so record_explanations was correctly
        # recovered from the still-present original for every node: the
        # rerun must be byte-identical to it (gapengine/evolve.py's
        # determinism guarantee this feature depends on).
        for node in chain:
            rerun_path = (
                self.experiment
                / "lineage"
                / lineage._safe_ref_name(node["ref"])
                / f"seed-{report['seed']}"
                / "layers.jsonl"
            )
            original_path = (
                self.experiment
                / f"g{node['generation']}"
                / f"ind-{node['index']}"
                / f"seed-{report['seed']}"
                / "layers.jsonl"
            )
            self.assertEqual(
                rerun_path.read_bytes(),
                original_path.read_bytes(),
                f"rerun of {node['ref']} was not byte-identical to the original",
            )

    def test_immigrant_cell_has_single_node_chain_and_no_turning_points(self) -> None:
        chain = lineage.primary_lineage(self.repository, self.experiment, "V|high")
        self.assertEqual([node["ref"] for node in chain], ["g0/ind-6"])

        report = lineage.build_lineage_report(self.repository, self.experiment, "V|high")
        self.assertEqual(report["turnings"], [])
        self.assertEqual(len(report["ancestry"]), 1)
        self.assertIsNone(report["ancestry"][0]["rerun_error"])

    def test_missing_precedent_degrades_that_ancestor_without_raising(self) -> None:
        # Simulate g1's precedent.json having been lost, and force a fresh
        # rerun attempt (clear any cached report/rerun output from the
        # earlier sub-test) rather than reading back a previous success.
        precedent_path = self.experiment / "g1" / "precedent.json"
        backup = precedent_path.with_suffix(".json.bak")
        shutil.copyfile(precedent_path, backup)
        self.addCleanup(shutil.copyfile, backup, precedent_path)
        precedent_path.unlink()

        cache_path = self.experiment / "lineage" / "II-high-cache.json"
        cache_path.unlink(missing_ok=True)
        # This test's own (damaged) report must not poison the cache for
        # whichever test runs next (unittest does not guarantee method order
        # stays test-file order, and both tests share one class fixture).
        self.addCleanup(cache_path.unlink, missing_ok=True)
        rerun_dir = self.experiment / "lineage" / "g1-ind-0"
        if rerun_dir.is_dir():
            shutil.rmtree(rerun_dir)

        report = lineage.build_lineage_report(self.repository, self.experiment, "II|high")
        by_ref = {entry["ref"]: entry for entry in report["ancestry"]}
        self.assertIsNotNone(by_ref["g1/ind-0"]["rerun_error"])
        self.assertIsNone(by_ref["g2/ind-5"]["rerun_error"])
        # The one turning point spans exactly the damaged ancestor, so it can
        # no longer be computed -- reported as "none found", not a crash.
        self.assertEqual(report["turnings"], [])

    def test_stale_cache_missing_trait_series_is_recomputed(self) -> None:
        # A cache written before the Fable fix has ancestry entries with no
        # "scalars" and turnings with no "trait_series" -- it must be treated
        # as stale and recomputed, not returned as-is (gapengine/lineage.py::
        # build_lineage_report's cache-validity check).
        cache_path = self.experiment / "lineage" / "II-high-cache.json"
        good_report = lineage.build_lineage_report(self.repository, self.experiment, "II|high")
        self.addCleanup(
            cache_path.write_text,
            json.dumps(good_report, ensure_ascii=False),
            encoding="utf-8",
        )

        stale = json.loads(json.dumps(good_report))
        for entry in stale["ancestry"]:
            entry.pop("scalars", None)
        for turning in stale["turnings"]:
            turning.pop("trait_series", None)
        cache_path.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")

        recomputed = lineage.build_lineage_report(self.repository, self.experiment, "II|high")
        for entry in recomputed["ancestry"]:
            self.assertIn("scalars", entry)
        for turning in recomputed["turnings"]:
            self.assertIn("trait_series", turning)


class TraitSeriesTests(unittest.TestCase):
    """_trait_series traces one gene across the whole primary lineage; two
    turnings whose leading gene differs must produce distinct series (Fable
    fix: the old personality_series instead stitched together whichever gene
    moved most at each different step, mixing unrelated genes into one line).
    Synthetic data, since the real-run fixtures above have only one turning."""

    def test_two_turnings_with_different_leading_genes_get_distinct_series(self) -> None:
        g0 = _genome(risk_tolerance=0.2, novelty_drive=0.1)
        g1 = _genome(risk_tolerance=0.9, novelty_drive=0.1)  # step 1: risk_tolerance moved most
        g2 = _genome(risk_tolerance=0.9, novelty_drive=0.8)  # step 2: novelty_drive moved most

        ancestry_entries = [
            {"scalars": lineage.genome_scalars(genome)} for genome in (g0, g1, g2)
        ]
        shift_1 = lineage.genome_shift(g0, g1)
        shift_2 = lineage.genome_shift(g1, g2)

        series_1 = lineage._trait_series(ancestry_entries, shift_1)
        series_2 = lineage._trait_series(ancestry_entries, shift_2)

        self.assertEqual(series_1["key"], "risk_tolerance")
        self.assertEqual(series_2["key"], "novelty_drive")
        self.assertEqual(series_1["values"], [0.2, 0.9, 0.9])
        self.assertEqual(series_2["values"], [0.1, 0.1, 0.8])
        self.assertEqual(series_1["label"], lineage._SCALAR_LABELS["risk_tolerance"])
        self.assertEqual(series_2["label"], lineage._SCALAR_LABELS["novelty_drive"])


class CoevolveLineageRerunTests(unittest.TestCase):
    """WB-LINEAGE-002 fix 3: a plain evolve() output (no manifest.json, like
    every fixture in this file) never looked coevolved to
    _resolve_world_context before this fix -- so a coevolve run's antagonist
    precedent was silently dropped on rerun. This run actually coevolves
    (gapengine/evolve.py::evolve's own "coevolve" cfg), so
    g0/precedent.antagonist.json exists on disk precisely when the fix's
    file-presence check is the only thing that can find it."""

    @classmethod
    def setUpClass(cls) -> None:
        from gapengine.evolve import evolve

        project, template = _momotaro_paths()
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cls.runs_root = root / "runs"
        cls.archive = evolve(
            {
                "coevolve": True,
                # Same scale as RealRunLineageTests above (known, by that
                # fixture, to reach archive cells for momotaro) -- coevolving
                # an antagonist alongside doesn't stop the protagonist from
                # reaching, and this test needs a real elite/chain, not just
                # a g0 individual, to exercise a rerun via build_lineage_report.
                "ga_seed": 1,
                "generations": 5,
                "keep": "all",
                "population": 8,
                "project": project,
                "seed_base": 11,
                "seeds": 2,
                "template": template,
                "out": cls.runs_root / "exp1",
                "processes": 1,
            }
        )
        cls.repository = RunRepository(cls.runs_root)
        cls.experiment = cls.repository.experiment("exp1")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_rerun_of_coevolved_ancestor_byte_matches_original(self) -> None:
        # Confirm this fixture actually coevolved (else the fix would go
        # untested).
        self.assertTrue(
            (self.experiment / "g1" / "precedent.antagonist.json").is_file()
        )

        cells = json.loads(
            (self.experiment / "archive.json").read_text(encoding="utf-8")
        ).get("cells") or {}
        self.assertTrue(cells)
        cell_key = next(iter(cells))

        report = lineage.build_lineage_report(self.repository, self.experiment, cell_key)
        seed = report["seed"]
        for entry in report["ancestry"]:
            self.assertIsNone(entry["rerun_error"])

        for entry in report["ancestry"]:
            rerun_path = (
                self.experiment
                / "lineage"
                / lineage._safe_ref_name(entry["ref"])
                / f"seed-{seed}"
                / "layers.jsonl"
            )
            original_path = (
                self.experiment
                / f"g{entry['generation']}"
                / f"ind-{entry['index']}"
                / f"seed-{seed}"
                / "layers.jsonl"
            )
            self.assertEqual(
                rerun_path.read_bytes(),
                original_path.read_bytes(),
                f"rerun of {entry['ref']} (coevolved run) did not byte-match "
                "the original -- the antagonist likely ran without its own "
                "precedent/action graph",
            )


if __name__ == "__main__":
    unittest.main()
