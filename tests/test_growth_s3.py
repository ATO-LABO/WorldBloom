"""WB-GROWTH-001 S3: a third QD descriptor axis for the size of the
protagonist's personality change over a run ("arc"), opt-in via the
template's qd.yaml (`arc_bins: [none, small, large]`), kept out of the
quality score itself and byte-compatible with every template that does not
declare the axis."""

from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import yaml

from execution import worker

from gapengine.evolve import evolve
from gapengine.genome import Genome
from gapengine.qd import Archive, Descriptor, Elite, arc
from gapengine.seed_genomes import from_archive as seed_genomes_from_archive
from test_evolution_execution import EvolutionHttpPersonalityGrowthTests, cleanup_http_fixture
from viewer import data


ROOT = Path(__file__).resolve().parents[1]
PLUS2_PROJECT = ROOT / "projects" / "momotaro_plus2"
PLUS2_TEMPLATE = ROOT / "templates" / "momotaro_plus2"


def _growth_row(subject: str, acquired_after: dict[str, float]) -> dict[str, Any]:
    return {
        "kind": "event",
        "turn": 1,
        "day": 1,
        "subject": subject,
        "verb": "growth",
        "details": {
            "rule": "g_fight_won",
            "shift": dict(acquired_after),
            "plasticity": 1.0,
            "acquired_after": dict(acquired_after),
        },
    }


HEADER = {"kind": "header", "protagonist": "桃太郎", "antagonist": "鬼"}


class ArcCalculationTests(unittest.TestCase):
    def test_no_growth_rows_is_zero(self) -> None:
        rows = [HEADER, {"kind": "decision", "subject": "桃太郎", "verb": "fight"}]
        self.assertEqual(arc(rows), 0.0)

    def test_last_growth_row_wins_as_l1_sum(self) -> None:
        rows = [
            HEADER,
            _growth_row("桃太郎", {"risk_tolerance": 0.1, "category_weight.I": 0.1}),
            {"kind": "decision", "subject": "桃太郎", "verb": "rest"},
            _growth_row("桃太郎", {"risk_tolerance": -0.05, "category_weight.I": 0.1}),
        ]
        # Only the *last* growth row's acquired_after counts, not a sum
        # across every growth row.
        self.assertAlmostEqual(arc(rows), 0.15)

    def test_only_the_named_subject_counts(self) -> None:
        rows = [
            HEADER,
            _growth_row("鬼", {"risk_tolerance": 0.9}),
        ]
        self.assertEqual(arc(rows, subject="桃太郎"), 0.0)
        self.assertEqual(arc(rows, subject="鬼"), 0.9)

    def test_acquired_after_can_net_back_to_empty(self) -> None:
        rows = [HEADER, _growth_row("桃太郎", {})]
        self.assertEqual(arc(rows), 0.0)


class ArchiveArcAxisTests(unittest.TestCase):
    def _elite(self, descriptor: Descriptor, *, quality: float = 0.5, reach_rate: float = 0.5) -> Elite:
        return Elite(
            genome=Genome.neutral(),
            quality=quality,
            descriptor=descriptor,
            reach_rate=reach_rate,
            exemplar={
                "engine_hash": "engine",
                "layers_path": "g0/ind-0/seed-0/layers.jsonl",
                "precedent_hash": "precedent",
                "seed": 0,
            },
            generation=0,
        )

    def test_freeze_uses_median_of_positive_values_only(self) -> None:
        archive = Archive()
        archive.freeze_arc_thresholds([0.0, 0.0, 0.2, 0.6])
        self.assertEqual(archive.arc_thresholds, {"split": 0.4})
        # Refreezing is a no-op, same as freeze_thresholds().
        archive.freeze_arc_thresholds([10.0])
        self.assertEqual(archive.arc_thresholds, {"split": 0.4})

    def test_freeze_falls_back_to_a_fixed_split_with_no_positive_values(self) -> None:
        archive = Archive()
        archive.freeze_arc_thresholds([0.0, 0.0])
        self.assertEqual(archive.arc_thresholds, {"split": 0.1})

    def test_arc_bin_for_none_small_large(self) -> None:
        archive = Archive()
        archive.freeze_arc_thresholds([0.2, 0.6])
        self.assertEqual(archive.arc_thresholds, {"split": 0.4})
        self.assertEqual(archive.arc_bin_for(0.0), "none")
        self.assertEqual(archive.arc_bin_for(0.2), "small")
        self.assertEqual(archive.arc_bin_for(0.4), "small")
        self.assertEqual(archive.arc_bin_for(0.41), "large")

    def test_bin_for_raises_before_freezing(self) -> None:
        with self.assertRaises(RuntimeError):
            Archive().arc_bin_for(0.1)

    def test_insert_omits_arc_from_cell_and_json_when_disabled(self) -> None:
        archive = Archive()
        archive.freeze_thresholds([0.1])
        descriptor = Descriptor("I", 0.1, archive.bin_for(0.1))
        self.assertTrue(archive.insert(self._elite(descriptor)))
        self.assertIn(("I", "low"), archive.cells)
        payload = archive.to_dict()
        self.assertNotIn("arc_thresholds", payload)
        self.assertNotIn("arc", payload["cells"]["I|low"])
        self.assertNotIn("arc_bin", payload["cells"]["I|low"])

    def test_insert_uses_three_element_cell_when_arc_enabled(self) -> None:
        archive = Archive()
        archive.freeze_thresholds([0.1])
        archive.freeze_arc_thresholds([0.2, 0.6])
        descriptor = Descriptor(
            "I", 0.1, archive.bin_for(0.1),
            arc=0.41, arc_bin=archive.arc_bin_for(0.41),
        )
        self.assertTrue(archive.insert(self._elite(descriptor)))
        self.assertIn(("I", "low", "large"), archive.cells)
        payload = archive.to_dict()
        self.assertEqual(payload["arc_thresholds"], {"split": 0.4})
        self.assertEqual(payload["cells"]["I|low|large"]["descriptor"]["arc_bin"], "large")
        self.assertEqual(payload["cells"]["I|low|large"]["descriptor"]["arc"], 0.41)

    def test_save_load_round_trip_with_arc_axis(self) -> None:
        archive = Archive()
        archive.freeze_thresholds([0.1])
        archive.freeze_arc_thresholds([0.5])
        descriptor = Descriptor("I", 0.1, "low", arc=0.5, arc_bin="large")
        archive.insert(self._elite(descriptor))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "archive.json"
            archive.save(path)
            before = path.read_bytes()
            restored = Archive.load(path)
            self.assertEqual(restored.arc_thresholds, {"split": 0.5})
            self.assertIn(("I", "low", "large"), restored.cells)
            restored.save(path)
            self.assertEqual(before, path.read_bytes())

    def test_load_legacy_two_element_archive_leaves_arc_thresholds_none(self) -> None:
        legacy = {
            "cells": {
                "I|low": {
                    "descriptor": {"category": "I", "volatility": 0.1, "volatility_bin": "low"},
                    "exemplar": {"engine_hash": "e", "layers_path": "p", "precedent_hash": "h", "seed": 0},
                    "generation": 0,
                    "genome": Genome.neutral().to_dict(),
                    "parents": [],
                    "quality": 0.5,
                    "reach_rate": 0.5,
                }
            },
            "volatility_thresholds": {"low_max": 0.2, "mid_max": 0.5},
        }
        restored = Archive.from_dict(legacy)
        self.assertIsNone(restored.arc_thresholds)
        self.assertIn(("I", "low"), restored.cells)
        self.assertIsNone(restored.cells[("I", "low")].descriptor.arc_bin)


class SeedGenomesArcCellTests(unittest.TestCase):
    def test_from_archive_preserves_three_element_cell_text(self) -> None:
        archive = Archive()
        archive.freeze_thresholds([0.1])
        archive.freeze_arc_thresholds([0.5])
        descriptor = Descriptor("I", 0.1, "low", arc=0.5, arc_bin="large")
        elite = Elite(
            genome=Genome.neutral(), quality=0.7, descriptor=descriptor, reach_rate=1.0,
            exemplar={"engine_hash": "e", "layers_path": "p", "precedent_hash": "h", "seed": 0},
            generation=0,
        )
        archive.insert(elite)
        entries = seed_genomes_from_archive(archive.to_dict())
        self.assertEqual(entries[0]["cell"], "I|low|large")


class EvolveByteCompatibilityTests(unittest.TestCase):
    """A template whose qd.yaml has no arc_bins key must be entirely
    unaffected by WB-GROWTH-001 S3, even with --personality-growth on and
    even though momotaro_plus2's rules.yaml already has outcome rules."""

    def test_no_arc_bins_means_no_arc_keys_anywhere_even_with_growth_on(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stripped_template = root / "template_no_arc"
            shutil.copytree(PLUS2_TEMPLATE, stripped_template)
            qd = yaml.safe_load((PLUS2_TEMPLATE / "qd.yaml").read_text(encoding="utf-8"))
            self.assertIn("arc_bins", qd)
            del qd["arc_bins"]
            (stripped_template / "qd.yaml").write_text(
                yaml.safe_dump(qd, allow_unicode=True), encoding="utf-8",
            )

            archive = evolve({
                "ga_seed": 3, "generations": 2, "keep": "all", "population": 4,
                "processes": 1, "project": PLUS2_PROJECT, "seed_base": 5, "seeds": 1,
                "template": stripped_template, "out": root / "out",
                "personality_growth": True,
            })

            self.assertIsNone(archive.arc_thresholds)
            for cell in archive.cells:
                self.assertEqual(len(cell), 2)
            payload = json.loads((root / "out" / "archive.json").read_text(encoding="utf-8"))
            self.assertNotIn("arc_thresholds", payload)
            for cell_text, elite in payload["cells"].items():
                self.assertEqual(cell_text.count("|"), 1)
                self.assertNotIn("arc", elite["descriptor"])
                self.assertNotIn("arc_bin", elite["descriptor"])
            for generation in range(2):
                results = json.loads(
                    (root / "out" / f"g{generation}" / "results.json").read_text(encoding="utf-8")
                )
                for individual in results:
                    for run in individual["runs"]:
                        self.assertNotIn("arc", run)


class MomotaroPlus2ArcAxisTests(unittest.TestCase):
    """templates/momotaro_plus2/qd.yaml declares arc_bins, so this template
    (and only this one) actually exercises the third axis."""

    def _common(self, out: Path) -> dict[str, Any]:
        return {
            "ga_seed": 1, "generations": 5, "keep": "all", "population": 12,
            "processes": 8, "project": PLUS2_PROJECT, "seed_base": 0, "seeds": 3,
            "template": PLUS2_TEMPLATE, "out": out, "personality_growth": True,
        }

    def test_archive_gets_three_element_cells_with_a_non_none_arc_bin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = evolve(self._common(root / "first"))
            second = evolve(self._common(root / "second"))

            self.assertIsNotNone(first.arc_thresholds)
            non_none = [cell for cell in first.cells if len(cell) == 3 and cell[2] != "none"]
            self.assertGreaterEqual(
                len(non_none), 1,
                "no cell grew a non-'none' arc bin at population=12/generations=5/"
                "seeds=3 -- bump these if this ever regresses",
            )
            for cell in first.cells:
                self.assertEqual(len(cell), 3)

            self.assertEqual(
                (root / "first" / "archive.json").read_bytes(),
                (root / "second" / "archive.json").read_bytes(),
            )
            for generation in range(5):
                self.assertEqual(
                    (root / "first" / f"g{generation}" / "results.json").read_bytes(),
                    (root / "second" / f"g{generation}" / "results.json").read_bytes(),
                )

    def test_personality_growth_off_collapses_every_cell_to_the_none_bin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cfg = {
                "ga_seed": 2, "generations": 2, "keep": "all", "population": 4,
                "processes": 1, "project": PLUS2_PROJECT, "seed_base": 0, "seeds": 1,
                "template": PLUS2_TEMPLATE, "out": root / "out",
            }
            archive = evolve(cfg)
            for cell in archive.cells:
                self.assertEqual(len(cell), 3)
                self.assertEqual(cell[2], "none")

    def test_resume_keeps_the_frozen_arc_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = root / "out"
            cfg = {
                "ga_seed": 1, "generations": 1, "keep": "all", "population": 12,
                "processes": 4, "project": PLUS2_PROJECT, "seed_base": 0, "seeds": 3,
                "template": PLUS2_TEMPLATE, "out": out, "personality_growth": True,
            }
            first = evolve(cfg)
            self.assertIsNotNone(first.arc_thresholds)
            frozen = dict(first.arc_thresholds)

            resumed = evolve({**cfg, "generations": 2, "resume": True})
            self.assertEqual(resumed.arc_thresholds, frozen)


class ViewerArcAxisTests(unittest.TestCase):
    def test_qd_axes_reads_arc_bins_only_from_momotaro_plus2(self) -> None:
        _, _, arc_bins = data.qd_axes(PLUS2_TEMPLATE)
        self.assertEqual(arc_bins, ["none", "small", "large"])
        _, _, none_declared = data.qd_axes(ROOT / "templates" / "momotaro")
        self.assertEqual(none_declared, [])

    def test_ordered_axes_splits_three_element_keys(self) -> None:
        categories, bins, arc_bins = data._ordered_axes(
            ["I"], ["low"],
            {"I|low|none": {}, "II|mid|large": {}},
        )
        self.assertEqual(categories, ["I", "II"])
        self.assertEqual(bins, ["low", "mid"])
        self.assertEqual(arc_bins, ["large", "none"])

    def test_experiment_meta_and_cell_view_accept_three_element_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs_root = root / "runs"
            runs_root.mkdir()
            evolve({
                "ga_seed": 1, "generations": 1, "keep": "all", "population": 12,
                "processes": 4, "project": PLUS2_PROJECT, "seed_base": 0, "seeds": 3,
                "template": PLUS2_TEMPLATE, "out": runs_root / "growth-s3-unit",
                "personality_growth": True,
            })
            repository = data.RunRepository(runs_root)
            experiment = repository.experiment("growth-s3-unit")
            meta = data.experiment_meta(repository, experiment)
            self.assertTrue(meta["arc_bins"])

            archive = repository.archive(experiment)
            cell_key = next(iter(archive["cells"]))
            self.assertEqual(cell_key.count("|"), 2)
            view = data.cell_view(repository, experiment, cell_key, view="digest")
            self.assertEqual(view["cell"], cell_key)


class EvolutionHttpArcFilterTests(EvolutionHttpPersonalityGrowthTests):
    """WB-GROWTH-001 S3 regression: the grid page's own "?arc=" query
    parameter used to leak into sifting_view.load()'s catalog.candidates()
    filter kwargs (not excluded from filter_query) and raise ConfigError,
    which the server caught as a generic 500. Reuses
    EvolutionHttpPersonalityGrowthTests' setUp verbatim (a real
    momotaro_plus2/personality_growth job through the frozen adapter) --
    this is the only test in the suite that drives the grid page's arc
    toggle through an actual HTTP request."""

    def test_grid_page_accepts_every_arc_filter_value(self) -> None:
        status, job = self.http(
            "POST", "/api/jobs",
            {"request_id": "arc-filter-req", "kind": "evolve", "config_id": "cfg-growth-on"},
        )
        self.assertEqual(status, 202, job)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status, value = self.http("GET", "/api/jobs/" + job["job_id"])
            self.assertEqual(status, 200, value)
            if value["state"] in worker.TERMINAL:
                break
            time.sleep(0.05)
        else:
            self.fail("real GA did not reach terminal state")
        self.assertEqual(value["state"], "succeeded", value)

        run_id = job["run_id"]
        for arc_value in ("all", "none", "small", "large"):
            conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
            try:
                conn.request("GET", f"/exp/{run_id}?arc={arc_value}")
                response = conn.getresponse()
                body = response.read()
                self.assertEqual(response.status, 200, body[:2000])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
