import json
import statistics
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import jev_stage3_report as report  # noqa: E402
from gapengine.genome import Genome  # noqa: E402
from gapengine.qd import Archive, Descriptor, Elite  # noqa: E402


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_layers(path: Path, protagonist: str, decisions: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"kind": "header", "protagonist": protagonist}]
    for decision in decisions:
        rows.append(
            {
                "kind": "decision",
                "subject": protagonist,
                "verb": decision["verb"],
                "policy": decision.get("policy"),
                "classification": decision.get("classification"),
            }
        )
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_archive(path: Path, qualities: list[float]) -> None:
    archive = Archive()
    archive.volatility_thresholds = {"low_max": 0.1, "mid_max": 0.5}
    bins = ["low", "mid", "high", "low", "mid", "high"]
    categories = ["I", "II", "III", "IV", "V", "VI"]
    for index, quality in enumerate(qualities):
        archive.insert(
            Elite(
                genome=Genome.neutral(),
                quality=quality,
                descriptor=Descriptor(
                    category=categories[index],
                    volatility=0.2,
                    volatility_bin=bins[index],
                ),
                reach_rate=0.5,
                exemplar={},
                generation=0,
            )
        )
    archive.save(path)


class JevStage3ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        # --- k0: kappa=0 baseline, no rationality activity -----------------
        self.k0_dir = self.root / "k0"
        _write_json(
            self.k0_dir / "g0" / "results.json",
            [
                {
                    "index": 0,
                    "runs": [
                        {
                            "seed": 0,
                            "reached": True,
                            "effective_sequence": [["II", "observe", "none"]],
                            "layers_path": "g0/ind-0/seed-0/layers.jsonl",
                        }
                    ],
                },
                {
                    "index": 1,
                    "runs": [
                        {
                            "seed": 0,
                            "reached": False,
                            "effective_sequence": [],
                            "layers_path": "g0/ind-1/seed-0/layers.jsonl",
                        }
                    ],
                },
            ],
        )
        _write_layers(
            self.k0_dir / "g0" / "ind-0" / "seed-0" / "layers.jsonl",
            "momotaro",
            [
                {
                    "verb": "observe",
                    "policy": {"ctx": [["phase"], True, "none", "alive", "neutral"]},
                    "classification": {"category": "II", "target_role": "none"},
                },
                {
                    "verb": "rest",
                    "policy": {"ctx": [["phase"], False, "none", "alive", "neutral"]},
                    "classification": None,
                },
                {
                    "verb": "fight",
                    "policy": {"ctx": [["phase"], True, "none", "alive", "neutral"]},
                    "classification": {"category": "IV", "target_role": "ally"},
                },
            ],
        )
        _write_layers(
            self.k0_dir / "g0" / "ind-1" / "seed-0" / "layers.jsonl",
            "momotaro",
            [
                {"verb": "disguise", "policy": None, "classification": None},
                {
                    "verb": "observe",
                    "policy": {"ctx": [["phase"], True, "none", "alive", "neutral"]},
                    "classification": {"category": "II", "target_role": "none"},
                },
            ],
        )
        _write_archive(self.k0_dir / "archive.json", [0.5, 0.3])
        _write_json(
            self.k0_dir / "summary.json",
            {"generations": [{"generation": 0, "reach_rate": 0.5, "occupied_cells": 2}]},
        )

        # --- k03: kappa=0.3, rationality layer active -----------------------
        self.k03_dir = self.root / "k03"
        _write_json(
            self.k03_dir / "g0" / "results.json",
            [
                {
                    "index": 0,
                    "runs": [
                        {
                            "seed": 0,
                            "reached": True,
                            "effective_sequence": [
                                ["II", "observe", "none"],
                                ["III", "persuade", "none"],
                            ],
                            "layers_path": "g0/ind-0/seed-0/layers.jsonl",
                        }
                    ],
                },
                {
                    "index": 1,
                    "runs": [
                        {
                            "seed": 0,
                            "reached": True,
                            "effective_sequence": [],
                            "layers_path": "g0/ind-1/seed-0/layers.jsonl",
                        }
                    ],
                },
                {
                    "index": 2,
                    "runs": [
                        {
                            "seed": 0,
                            "reached": False,
                            "effective_sequence": [["IV", "fight", "ally"]],
                            "layers_path": "g0/ind-2/seed-0/layers.jsonl",
                        }
                    ],
                },
            ],
        )
        _write_layers(
            self.k03_dir / "g0" / "ind-0" / "seed-0" / "layers.jsonl",
            "momotaro",
            [
                {
                    "verb": "observe",
                    "policy": {
                        "ctx": [["phase"], True, "none", "alive", "neutral"],
                        "m_rat": 1.2,
                        "p_rat": 0.7,
                    },
                    "classification": {"category": "II", "target_role": "none"},
                },
                {
                    "verb": "persuade",
                    "policy": {
                        "ctx": [["phase"], True, "none", "alive", "neutral"],
                        "m_rat": 0.9,
                        "p_rat": None,
                    },
                    "classification": {"category": "III", "target_role": "none"},
                },
            ],
        )
        _write_layers(
            self.k03_dir / "g0" / "ind-1" / "seed-0" / "layers.jsonl",
            "momotaro",
            [
                {
                    "verb": "rest",
                    "policy": {
                        "ctx": [["phase"], True, "none", "alive", "neutral"],
                        "m_rat": 1.0,
                        "p_rat": 0.5,
                    },
                    "classification": None,
                },
            ],
        )
        _write_layers(
            self.k03_dir / "g0" / "ind-2" / "seed-0" / "layers.jsonl",
            "momotaro",
            [
                {
                    "verb": "fight",
                    "policy": {
                        "ctx": [["phase"], True, "none", "alive", "neutral"],
                        "m_rat": 1.5,
                        "p_rat": 0.9,
                    },
                    "classification": {"category": "IV", "target_role": "ally"},
                },
            ],
        )
        _write_archive(self.k03_dir / "archive.json", [0.6, 0.8])
        _write_json(
            self.k03_dir / "summary.json",
            {
                "generations": [
                    {
                        "generation": 0,
                        "reach_rate": 2 / 3,
                        "occupied_cells": 2,
                        "rationality_judge_calls": 12,
                        "rationality_table_size": 30,
                        "rationality_thermal_wait_seconds": 4.5,
                    }
                ]
            },
        )

        self.project = self.root / "project"
        (self.project).mkdir()

    def test_reach_rate_and_weird_action_counts(self) -> None:
        k0 = report.compute_experiment("k0", self.k0_dir, self.project)

        self.assertEqual((1, 2), (k0["overall_reached"], k0["overall_total"]))
        self.assertEqual({0: (1, 2)}, k0["generation_reach"])
        self.assertEqual((1, 2), k0["final_reach"])

        weird = k0["weird"]
        self.assertEqual(5, weird["denominator"])
        self.assertEqual(1, weird["weird_a"])
        self.assertEqual(1, weird["weird_b"])
        self.assertEqual(1, weird["weird_c"])
        self.assertEqual(3, k0["weird_total"])

        # kappa=0: no policy carries m_rat/p_rat.
        self.assertEqual([], weird["m_rats"])
        self.assertEqual(0, weird["p_rat_denominator"])

        self.assertEqual(1, k0["distinct_sequences"])
        self.assertEqual(1, k0["reached_run_count"])

        self.assertIsNone(k0["rationality"])
        self.assertEqual(2, k0["archive"]["occupied_cells"])
        self.assertAlmostEqual(0.4, k0["archive"]["avg_quality"])
        self.assertAlmostEqual(0.5, k0["archive"]["max_quality"])

    def test_rationality_stats(self) -> None:
        k03 = report.compute_experiment("k03", self.k03_dir, self.project)

        self.assertEqual((2, 3), (k03["overall_reached"], k03["overall_total"]))

        weird = k03["weird"]
        self.assertEqual(4, weird["denominator"])
        self.assertEqual(0, weird["weird_a"])
        self.assertEqual(1, weird["weird_b"])
        self.assertEqual(0, weird["weird_c"])
        self.assertEqual(1, k03["weird_total"])

        m_rats = weird["m_rats"]
        self.assertEqual([1.2, 0.9, 1.0, 1.5], m_rats)
        self.assertAlmostEqual(1.1, statistics.median(m_rats))
        self.assertAlmostEqual(0.915, report._quantile(m_rats, 0.05))
        self.assertAlmostEqual(1.455, report._quantile(m_rats, 0.95))
        self.assertEqual(4, weird["p_rat_denominator"])
        self.assertEqual(3, weird["p_rat_present"])

        self.assertEqual(2, k03["distinct_sequences"])
        self.assertEqual(2, k03["reached_run_count"])

        rationality = k03["rationality"]
        self.assertEqual(12, rationality["judge_calls"])
        self.assertEqual(30, rationality["table_size"])
        self.assertAlmostEqual(4.5, rationality["thermal_wait_seconds"])

    def test_judgement_against_baseline(self) -> None:
        k0 = report.compute_experiment("k0", self.k0_dir, self.project)
        k03 = report.compute_experiment("k03", self.k03_dir, self.project)

        rows = {
            label: values
            for label, *values in report._judgement_rows([k0, k03], "k0")
        }
        # Neither fixture's runs carry a "shaped" field, so the 4th
        # (shaped-mean-vs-baseline) column is "-" for both.
        self.assertEqual(["FAIL", "PASS", "FAIL", "-"], rows["k0（基準線）"])
        # k03: reach 2/3 > 1/2, occupied cells 2 >= 0.8*2, weird rate 25% < 60%.
        self.assertEqual(["PASS", "PASS", "PASS", "-"], rows["k03"])

    def test_build_report_smoke(self) -> None:
        k0 = report.compute_experiment("k0", self.k0_dir, self.project)
        k03 = report.compute_experiment("k03", self.k03_dir, self.project)
        text = report.build_report([k0, k03], "k0")
        self.assertIn("Jev Stage 3", text)
        self.assertIn("k03", text)
        self.assertIn("PASS", text)

    def test_missing_directory_does_not_crash(self) -> None:
        stats = report.compute_experiment("missing", self.root / "does-not-exist", self.project)
        self.assertEqual(0, stats["overall_total"])
        self.assertIsNone(stats["archive"])
        self.assertIsNone(stats["rationality"])
        self.assertIsNone(stats["shaped"]["shaped_mean"])
        self.assertIsNone(stats["shaped"]["gen0_shaped_mean"])
        self.assertIsNone(stats["shaped"]["allies_at_contest_mean"])
        self.assertIsNone(stats["shaped"]["quality_mean"])


class JevStage3ShapedAlliesQualityTests(unittest.TestCase):
    """WB-JEV-001 Stage 3 addendum (2026-09-19): shaped fitness (overall and
    a paired generation-0 comparison against a baseline), allies-at-contest,
    contest rate, and mean quality across every run -- added because the
    momotaro target's overall reach rate is low enough (~2%) that a 100-run
    experiment's reach rate alone often shows no difference at all."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()

        # --- baseline: 2 individuals x 2 seeds at gen0, 1 individual at gen1.
        self.baseline_dir = self.root / "baseline"
        _write_json(
            self.baseline_dir / "g0" / "results.json",
            [
                {
                    "index": 0,
                    "runs": [
                        {"seed": 0, "reached": True, "effective_sequence": [],
                         "layers_path": "g0/ind-0/seed-0/layers.jsonl",
                         "shaped": 0.20, "quality": 0.5,
                         "allies_at_contest": 1.0, "contest_turn": 3},
                        {"seed": 1, "reached": False, "effective_sequence": [],
                         "layers_path": "g0/ind-0/seed-1/layers.jsonl",
                         "shaped": 0.40, "quality": 0.1,
                         "allies_at_contest": None, "contest_turn": None},
                    ],
                },
                {
                    "index": 1,
                    "runs": [
                        {"seed": 0, "reached": True, "effective_sequence": [],
                         "layers_path": "g0/ind-1/seed-0/layers.jsonl",
                         "shaped": 0.60, "quality": 0.9,
                         "allies_at_contest": 3.0, "contest_turn": 5},
                    ],
                },
            ],
        )
        for path in (
            "ind-0/seed-0", "ind-0/seed-1", "ind-1/seed-0",
        ):
            _write_layers(self.baseline_dir / "g0" / path / "layers.jsonl", "momotaro", [])
        _write_json(
            self.baseline_dir / "g1" / "results.json",
            [
                {
                    "index": 0,
                    "runs": [
                        {"seed": 0, "reached": False, "effective_sequence": [],
                         "layers_path": "g1/ind-0/seed-0/layers.jsonl",
                         "shaped": 9.99, "quality": 9.99,
                         "allies_at_contest": None, "contest_turn": None},
                    ],
                },
            ],
        )
        _write_layers(self.baseline_dir / "g1" / "ind-0" / "seed-0" / "layers.jsonl", "momotaro", [])
        _write_archive(self.baseline_dir / "archive.json", [0.5])
        _write_json(self.baseline_dir / "summary.json", {"generations": []})

        # --- experiment: same (index, seed) pairs at gen0 as the baseline,
        # each shaped value shifted by a known delta so the paired diff is
        # hand-computable: +0.10, -0.20, +0.05.
        self.experiment_dir = self.root / "experiment"
        _write_json(
            self.experiment_dir / "g0" / "results.json",
            [
                {
                    "index": 0,
                    "runs": [
                        {"seed": 0, "reached": True, "effective_sequence": [],
                         "layers_path": "g0/ind-0/seed-0/layers.jsonl",
                         "shaped": 0.30, "quality": 0.5,
                         "allies_at_contest": 2.0, "contest_turn": 4},
                        {"seed": 1, "reached": False, "effective_sequence": [],
                         "layers_path": "g0/ind-0/seed-1/layers.jsonl",
                         "shaped": 0.20, "quality": 0.1,
                         "allies_at_contest": None, "contest_turn": None},
                    ],
                },
                {
                    "index": 1,
                    "runs": [
                        {"seed": 0, "reached": True, "effective_sequence": [],
                         "layers_path": "g0/ind-1/seed-0/layers.jsonl",
                         "shaped": 0.65, "quality": 0.9,
                         "allies_at_contest": 3.0, "contest_turn": 5},
                    ],
                },
            ],
        )
        for path in ("ind-0/seed-0", "ind-0/seed-1", "ind-1/seed-0"):
            _write_layers(self.experiment_dir / "g0" / path / "layers.jsonl", "momotaro", [])
        _write_archive(self.experiment_dir / "archive.json", [0.5])
        _write_json(self.experiment_dir / "summary.json", {"generations": []})

    def test_shaped_mean_overall_and_generation0_only(self) -> None:
        baseline = report.compute_experiment("baseline", self.baseline_dir, self.project)

        # Overall: mean of all 4 runs (gen0's 3 + gen1's 1): (0.2+0.4+0.6+9.99)/4.
        self.assertAlmostEqual(
            (0.20 + 0.40 + 0.60 + 9.99) / 4, baseline["shaped"]["shaped_mean"]
        )
        # Generation 0 only: mean of the 3 gen0 runs, excluding gen1's 9.99.
        self.assertAlmostEqual(
            (0.20 + 0.40 + 0.60) / 3, baseline["shaped"]["gen0_shaped_mean"]
        )
        self.assertEqual(
            {(0, 0): 0.20, (0, 1): 0.40, (1, 0): 0.60},
            baseline["shaped"]["gen0_shaped_by_key"],
        )

    def test_allies_at_contest_and_contest_rate(self) -> None:
        baseline = report.compute_experiment("baseline", self.baseline_dir, self.project)

        # allies_at_contest: only the two non-None runs (1.0, 3.0) count.
        self.assertAlmostEqual(2.0, baseline["shaped"]["allies_at_contest_mean"])
        # contest_turn is not None for 2 of the 4 total runs.
        self.assertEqual(2, baseline["shaped"]["contest_runs"])
        self.assertEqual(4, baseline["shaped"]["total_runs"])

    def test_quality_mean_across_all_runs_regardless_of_reached(self) -> None:
        baseline = report.compute_experiment("baseline", self.baseline_dir, self.project)

        # quality: all 4 runs, including the unreached ones.
        self.assertAlmostEqual(
            (0.5 + 0.1 + 0.9 + 9.99) / 4, baseline["shaped"]["quality_mean"]
        )

    def test_paired_generation0_shaped_diff_against_baseline(self) -> None:
        baseline = report.compute_experiment("baseline", self.baseline_dir, self.project)
        experiment = report.compute_experiment("experiment", self.experiment_dir, self.project)

        mean_diff, positive_count, matched = report._paired_gen0_shaped_diff(
            experiment, baseline
        )
        # (0,0): 0.30-0.20=+0.10; (0,1): 0.20-0.40=-0.20; (1,0): 0.65-0.60=+0.05.
        self.assertEqual(3, matched)
        self.assertAlmostEqual((0.10 - 0.20 + 0.05) / 3, mean_diff)
        self.assertEqual(2, positive_count)  # 2 of 3 diffs are positive

    def test_judgement_row_flags_shaped_mean_improvement(self) -> None:
        baseline = report.compute_experiment("baseline", self.baseline_dir, self.project)
        experiment = report.compute_experiment("experiment", self.experiment_dir, self.project)

        rows = {
            label: values
            for label, *values in report._judgement_rows(
                [baseline, experiment], "baseline"
            )
        }
        # baseline gen0 mean = 0.4, experiment gen0 mean = (0.30+0.20+0.65)/3
        # = 0.383... -- lower than the baseline, so this column is FAIL.
        self.assertEqual("FAIL", rows["experiment"][-1])

    def test_metric_rows_include_new_shaped_allies_quality_metrics(self) -> None:
        baseline = report.compute_experiment("baseline", self.baseline_dir, self.project)
        experiment = report.compute_experiment("experiment", self.experiment_dir, self.project)

        labels = [label for label, _ in report._metric_rows([baseline, experiment], "baseline")]
        for expected in (
            "整形適応度 shaped: 全ラン平均",
            "整形適応度 shaped: 世代0平均",
            "決戦時の仲間数（平均）",
            "決戦に至ったランの割合",
            "quality平均（全ラン、到達不問）",
        ):
            self.assertIn(expected, labels)


if __name__ == "__main__":
    unittest.main()
