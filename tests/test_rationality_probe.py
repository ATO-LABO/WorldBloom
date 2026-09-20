"""WB-JEV-003: small regression tests for scripts/rationality_probe.py's
model bake-off helpers (--backend directory naming, --compare's metrics).
No model or GPU calls -- everything here is pure data-in/data-out."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import scripts.rationality_probe as probe


class OutDirNameTests(unittest.TestCase):
    def test_ollama_keeps_the_pre_existing_bare_name(self) -> None:
        # Old --method choice/noul runs must stay readable after this change.
        self.assertEqual(probe._out_dir_name("ollama", "qwen3.6:35b"), "qwen3.6_35b")

    def test_llama_server_gets_a_backend_prefix(self) -> None:
        self.assertEqual(
            probe._out_dir_name("llama-server", "bonsai2-27b"),
            "llama-server__bonsai2-27b",
        )


class CompareMetricsTests(unittest.TestCase):
    def test_metrics_match_hand_calculation_from_probe_jsonl_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            baseline_dir = Path(temporary) / "baseline"
            candidate_dir = Path(temporary) / "candidate"
            baseline_dir.mkdir()
            candidate_dir.mkdir()

            def write_rows(dir_path: Path, rows: list[dict]) -> None:
                (dir_path / "probe.jsonl").write_text(
                    "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                    encoding="utf-8",
                )

            write_rows(
                baseline_dir,
                [
                    {"method": "choice", "turn": "0", "candidate": "a", "p_choice": 0.1},
                    {"method": "choice", "turn": "0", "candidate": "b", "p_choice": 0.2},
                    {"method": "choice", "turn": "0", "candidate": "c", "p_choice": 0.9},
                    {"method": "choice", "turn": "0", "candidate": "d", "p_choice": 0.05},
                    # a "noul" row for the same pair must never be counted.
                    {"method": "noul", "turn": "0", "candidate": "a", "p_yes": 0.5},
                ],
            )
            write_rows(
                candidate_dir,
                [
                    {"method": "choice", "turn": "0", "candidate": "a", "p_choice": 0.3,
                     "elapsed_seconds": 1.0},
                    {"method": "choice", "turn": "0", "candidate": "b", "p_choice": 0.25},
                    {"method": "choice", "turn": "0", "candidate": "c", "p_choice": 0.35},
                    {"method": "choice", "turn": "0", "candidate": "d", "p_choice": 0.1,
                     "elapsed_seconds": 2.0},
                ],
            )

            baseline_rows = probe._read_probe_choice_rows(baseline_dir)
            candidate_rows = probe._read_probe_choice_rows(candidate_dir)
            self.assertEqual(len(baseline_rows), 4)  # the "noul" row is excluded

            baseline_scores = {
                (row["turn"], row["candidate"]): row["p_choice"] for row in baseline_rows
            }
            candidate_scores = {
                (row["turn"], row["candidate"]): row["p_choice"] for row in candidate_rows
            }
            points = [
                {
                    "point_id": "0",
                    "candidates": [("a", False), ("b", False), ("c", True), ("d", False)],
                }
            ]

            metrics = probe._compare_metrics(points, baseline_scores, candidate_scores)

            # baseline ranks (ascending value order d,a,b,c) -> a=2,b=3,c=4,d=1
            # candidate ranks (ascending value order d,b,a,c) -> a=3,b=2,c=4,d=1
            # Pearson correlation of rank vectors [2,3,4,1] and [3,2,4,1] == 0.8
            self.assertAlmostEqual(metrics["mean_spearman"], 0.8, places=9)
            self.assertEqual(metrics["spearman_n"], 1)
            # both baseline (0.9) and candidate (0.35) agree "c" is the top pick.
            self.assertEqual(metrics["top1_match_rate"], 1.0)
            self.assertEqual(metrics["top1_n"], 1)
            # baseline's 3 lowest are d(.05),a(.1),b(.2); candidate's own bottom
            # half (2 of 4, ascending) is d(.1),b(.25) -> hits: d yes, a no, b yes.
            self.assertAlmostEqual(metrics["bottom3_hit_rate"], 2 / 3, places=9)
            self.assertEqual(metrics["bottom3_n"], 3)

    def test_fewer_than_two_comparable_candidates_contributes_no_signal(self) -> None:
        points = [{"point_id": "0", "candidates": [("a", False), ("b", True)]}]
        # Only one candidate has a value in both dirs -- can't rank two things.
        metrics = probe._compare_metrics(points, {("0", "a"): 0.5}, {("0", "a"): 0.5})
        self.assertIsNone(metrics["mean_spearman"])
        self.assertEqual(metrics["spearman_n"], 0)
        self.assertIsNone(metrics["top1_match_rate"])
        self.assertIsNone(metrics["bottom3_hit_rate"])


if __name__ == "__main__":
    unittest.main()
