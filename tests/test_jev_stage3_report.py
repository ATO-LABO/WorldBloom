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
        self.assertEqual(["FAIL", "PASS", "FAIL"], rows["k0（基準線）"])
        # k03: reach 2/3 > 1/2, occupied cells 2 >= 0.8*2, weird rate 25% < 60%.
        self.assertEqual(["PASS", "PASS", "PASS"], rows["k03"])

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


if __name__ == "__main__":
    unittest.main()
