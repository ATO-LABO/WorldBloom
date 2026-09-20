"""WB-JEV-004: acceptance tests for scripts/jev_stage4_report.py's route
classifier and aggregation, against a hand-built fake experiment directory
(one run per route, plus one unreached run) so the expected numbers can be
checked by hand rather than trusted from a real GA run."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.jev_stage4_report import classify_rows, compute_experiment

GENOME_TEMPLATE = {
    "category_weight": {
        "I": 0.1,
        "II": 0.2,
        "III": 0.3,
        "IV": 0.4,
        "V": 0.5,
        "VI": 0.6,
    },
    "novelty_drive": 0.5,
    "risk_tolerance": 0.5,
    "stance_shift_bias": 0.0,
}


def _genome(**overrides) -> dict:
    genome = json.loads(json.dumps(GENOME_TEMPLATE))
    genome.update(overrides)
    return genome


def _write_layers(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


LETTER_TRADE_ROWS = [
    {
        "kind": "decision",
        "verb": "trial",
        "result": "trial_completed",
        "details": {"trial_id": "brother_letter_trial", "granted": {"item": "弟の手紙"}},
    },
    {
        "kind": "decision",
        "verb": "concede",
        "result": "conceded",
        "details": {"mode": "trade", "assets": {"弟の手紙": 1}},
    },
]

GUN_TRADE_ROWS = [
    {
        "kind": "decision",
        "verb": "craft",
        "result": "crafted",
        "details": {"item": "鉄砲", "consumed": {"小判": 3}},
    },
    {
        "kind": "decision",
        "verb": "concede",
        "result": "conceded",
        "details": {"mode": "trade", "assets": {"鉄砲": 1}},
    },
]

GUN_FIGHT_ROWS = [
    {
        "kind": "decision",
        "verb": "craft",
        "result": "crafted",
        "details": {"item": "鉄砲", "consumed": {"小判": 3}},
    },
    {
        "kind": "decision",
        "verb": "fight",
        "result": "won",
        "details": {"transferred": {"鬼ヶ島の宝物": 1}},
    },
]

GOODWILL_ROWS = [
    {
        "kind": "decision",
        "verb": "concede",
        "result": "conceded",
        "details": {"mode": "goodwill", "assets": {}},
    },
]

CLASSIC_ROWS = [
    {
        "kind": "decision",
        "verb": "fight",
        "result": "won",
        "details": {"transferred": {"鬼ヶ島の宝物": 1}},
    },
]

# Not reached, but did craft the gun and get the letter -- exercises (a)'s
# unconditional "crafted the gun"/"got the letter" counts, which count every
# run regardless of whether it reached the ending.
UNREACHED_BUSY_ROWS = [
    {
        "kind": "decision",
        "verb": "craft",
        "result": "crafted",
        "details": {"item": "鉄砲", "consumed": {"小判": 3}},
    },
    {
        "kind": "decision",
        "verb": "trial",
        "result": "trial_completed",
        "details": {"trial_id": "brother_letter_trial", "granted": {"item": "弟の手紙"}},
    },
]


class ClassifyRowsTests(unittest.TestCase):
    def test_letter_trade(self) -> None:
        route, crafted_gun, got_letter = classify_rows(LETTER_TRADE_ROWS, reached=True)
        self.assertEqual(route, "letter_trade")
        self.assertFalse(crafted_gun)
        self.assertTrue(got_letter)

    def test_gun_trade(self) -> None:
        route, crafted_gun, got_letter = classify_rows(GUN_TRADE_ROWS, reached=True)
        self.assertEqual(route, "gun_trade")
        self.assertTrue(crafted_gun)
        self.assertFalse(got_letter)

    def test_gun_fight(self) -> None:
        route, crafted_gun, got_letter = classify_rows(GUN_FIGHT_ROWS, reached=True)
        self.assertEqual(route, "gun_fight")
        self.assertTrue(crafted_gun)

    def test_goodwill(self) -> None:
        route, crafted_gun, got_letter = classify_rows(GOODWILL_ROWS, reached=True)
        self.assertEqual(route, "goodwill")

    def test_classic(self) -> None:
        route, crafted_gun, got_letter = classify_rows(CLASSIC_ROWS, reached=True)
        self.assertEqual(route, "classic")

    def test_unreached_run_has_no_route_but_still_flags(self) -> None:
        route, crafted_gun, got_letter = classify_rows(UNREACHED_BUSY_ROWS, reached=False)
        self.assertIsNone(route)
        self.assertTrue(crafted_gun)
        self.assertTrue(got_letter)

    def test_priority_letter_trade_over_gun_trade(self) -> None:
        # A run that pulled off both trades -- letter_trade wins per plan §3.
        rows = LETTER_TRADE_ROWS + GUN_TRADE_ROWS
        route, _crafted_gun, _got_letter = classify_rows(rows, reached=True)
        self.assertEqual(route, "letter_trade")


class ComputeExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.exp_dir = Path(self._tmp.name)

        specs = [
            ("letter_trade", LETTER_TRADE_ROWS, True, _genome(risk_tolerance=0.2)),
            ("gun_trade", GUN_TRADE_ROWS, True, _genome(risk_tolerance=0.4)),
            ("gun_fight", GUN_FIGHT_ROWS, True, _genome(risk_tolerance=0.6)),
            ("goodwill", GOODWILL_ROWS, True, _genome(risk_tolerance=0.8)),
            ("classic", CLASSIC_ROWS, True, _genome(risk_tolerance=1.0)),
            ("unreached", UNREACHED_BUSY_ROWS, False, _genome(risk_tolerance=0.5)),
        ]
        individuals = []
        for index, (label, rows, reached, genome) in enumerate(specs):
            layers_rel = f"g0/ind-{index}/seed-0/layers.jsonl"
            _write_layers(self.exp_dir / layers_rel, rows)
            individuals.append(
                {
                    "index": index,
                    "genome": genome,
                    "parents": [],
                    "runs": [
                        {
                            "seed": 0,
                            "reached": reached,
                            "layers_path": layers_rel,
                            "category": "I" if label != "gun_fight" else "II",
                            "volatility": 0.1 if label != "gun_fight" else 0.9,
                        }
                    ],
                }
            )
        (self.exp_dir / "g0").mkdir(exist_ok=True)
        (self.exp_dir / "g0" / "results.json").write_text(
            json.dumps(individuals, ensure_ascii=False), encoding="utf-8"
        )
        (self.exp_dir / "archive.json").write_text(
            json.dumps(
                {
                    "volatility_thresholds": {"low_max": 0.3, "mid_max": 0.6},
                    "cells": {
                        "I|low": {
                            "genome": _genome(),
                            "quality": 1.0,
                            "descriptor": {
                                "category": "I",
                                "volatility": 0.1,
                                "volatility_bin": "low",
                            },
                            "reach_rate": 1.0,
                            "exemplar": {"layers_path": "g0/ind-0/seed-0/layers.jsonl"},
                            "generation": 0,
                            "parents": [],
                        },
                        "II|high": {
                            "genome": _genome(),
                            "quality": 1.0,
                            "descriptor": {
                                "category": "II",
                                "volatility": 0.9,
                                "volatility_bin": "high",
                            },
                            "reach_rate": 1.0,
                            "exemplar": {"layers_path": "g0/ind-2/seed-0/layers.jsonl"},
                            "generation": 0,
                            "parents": [],
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_route_counts_and_unconditional_flags(self) -> None:
        stats = compute_experiment("fake", self.exp_dir)
        self.assertEqual(stats["total_runs"], 6)
        self.assertEqual(stats["reached_runs"], 5)
        self.assertEqual(
            dict(stats["route_counts"]),
            {"letter_trade": 1, "gun_trade": 1, "gun_fight": 1, "goodwill": 1, "classic": 1},
        )
        # crafted the gun: gun_trade, gun_fight, unreached -> 3 of 6 runs.
        self.assertEqual(stats["crafted_gun_runs"], 3)
        # got the letter: letter_trade, unreached -> 2 of 6 runs.
        self.assertEqual(stats["got_letter_runs"], 2)

    def test_route_genome_means_match_hand_calculation(self) -> None:
        stats = compute_experiment("fake", self.exp_dir)
        self.assertAlmostEqual(
            stats["route_genomes"]["letter_trade"][0]["risk_tolerance"], 0.2
        )
        self.assertAlmostEqual(
            stats["route_genomes"]["gun_trade"][0]["risk_tolerance"], 0.4
        )
        overall_mean = sum([0.2, 0.4, 0.6, 0.8, 1.0]) / 5
        self.assertAlmostEqual(
            sum(g["risk_tolerance"] for g in stats["reached_genomes"]) / 5, overall_mean
        )

    def test_qd_cross_table_places_each_route_in_its_cell(self) -> None:
        stats = compute_experiment("fake", self.exp_dir)
        cross = stats["qd_cross"]
        # letter_trade/gun_trade/goodwill/classic were all given category=I,
        # volatility=0.1 (bin "low" under thresholds low_max=0.3); gun_fight
        # was given category=II, volatility=0.9 (bin "high").
        self.assertEqual(cross[("I", "low")]["letter_trade"], 1)
        self.assertEqual(cross[("I", "low")]["gun_trade"], 1)
        self.assertEqual(cross[("I", "low")]["goodwill"], 1)
        self.assertEqual(cross[("I", "low")]["classic"], 1)
        self.assertEqual(cross[("II", "high")]["gun_fight"], 1)

    def test_archive_elite_routes_read_back_from_exemplars(self) -> None:
        stats = compute_experiment("fake", self.exp_dir)
        self.assertEqual(
            stats["archive_elite_routes"],
            {"I|low": "letter_trade", "II|high": "gun_fight"},
        )


if __name__ == "__main__":
    unittest.main()
