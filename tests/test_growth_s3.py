"""WB-GROWTH-001 S3: a third QD descriptor axis for the size of the
protagonist's personality change over a run ("arc"), opt-in via the
template's qd.yaml (`arc_bins: [none, small, large]`), kept out of the
quality score itself and byte-compatible with every template that does not
declare the axis."""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import yaml

from execution import worker
from execution.provenance import atomic_json, canonical, sha256, write_bytes

from gapengine.evolve import evolve
from gapengine.genome import Genome
from gapengine.qd import Archive, Descriptor, Elite, arc
from gapengine.seed_genomes import from_archive as seed_genomes_from_archive
from test_evolution_execution import EvolutionHttpPersonalityGrowthTests, cleanup_http_fixture
from test_ga_replay import _genome as _ga_replay_genome, _publish_revision
from test_output import _write_rows as _write_narrate_rows
from viewer import data, ga_replay, run_workspace
from viewer.data import RunRepository


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


class NarrateThreeElementCellTests(unittest.TestCase):
    """WB-GROWTH-001 S3 review fix (must #3): scripts/narrate.py's "is this
    selected cell actually in the archive" check required exactly 2 "|"-
    separated parts, so a selection naming a 3-element (arc-enabled) cell
    was always rejected as "not present in archive" even when it plainly
    was."""

    def test_three_element_cell_key_resolves_against_the_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "archive.json"
            layers_path = root / "g0" / "ind-0" / "seed-7" / "layers.jsonl"
            selection_path = root / "selection.json"
            stories_dir = root / "stories"

            _write_narrate_rows(layers_path)
            archive = Archive()
            archive.volatility_thresholds = {"low_max": 0.1, "mid_max": 0.2}
            archive.arc_thresholds = {"split": 0.1}
            archive.cells[("III", "high", "large")] = Elite(
                genome=Genome.neutral(),
                quality=0.75,
                descriptor=Descriptor(
                    category="III", volatility=0.4, volatility_bin="high",
                    arc=0.5, arc_bin="large",
                ),
                reach_rate=1.0,
                exemplar={
                    "engine_hash": "test",
                    "layers_path": "g0/ind-0/seed-7/layers.jsonl",
                    "precedent_hash": "test",
                    "seed": 7,
                },
                generation=3,
            )
            archive.save(archive_path)
            selection_path.write_text(
                json.dumps({"selected": ["III|high|large"]}, ensure_ascii=False) + "\n",
                encoding="utf-8", newline="\n",
            )

            from scripts.narrate import main as narrate_main

            exit_code = narrate_main([
                "--archive", str(archive_path),
                "--runs", str(root),
                "--selection", str(selection_path),
                "--out", str(stories_dir),
                "--backend", "none",
            ])
            self.assertEqual(exit_code, 0)

            index = json.loads((stories_dir / "index.json").read_text(encoding="utf-8"))
            entry = index["entries"][0]
            self.assertEqual(entry["cell"], "III|high|large")
            self.assertNotEqual(entry.get("status"), "error")


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

    def test_growth_off_momotaro_plus2_matches_pre_s3_shape(self) -> None:
        """WB-GROWTH-001 S3 review fix (should #2): qd.yaml declaring
        arc_bins is not enough on its own -- the axis needs
        personality_growth on too. A growth-off run on momotaro_plus2 (whose
        qd.yaml now permanently has arc_bins) must produce the exact same
        archive/results shape as the pre-S3 template that had no arc_bins
        key at all: 2-element cells, grid_size 18, no arc_thresholds."""
        old_qd_yaml = subprocess.run(
            ["git", "show", "83dd57b:templates/momotaro_plus2/qd.yaml"],
            cwd=ROOT, capture_output=True, check=True, text=True,
        ).stdout
        self.assertNotIn("arc_bins", old_qd_yaml)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_template = root / "template_pre_s3"
            shutil.copytree(PLUS2_TEMPLATE, old_template)
            (old_template / "qd.yaml").write_text(old_qd_yaml, encoding="utf-8")

            common = {
                "ga_seed": 4, "generations": 2, "keep": "all", "population": 4,
                "processes": 1, "project": PLUS2_PROJECT, "seed_base": 0, "seeds": 1,
            }
            pre_s3 = evolve({**common, "template": old_template, "out": root / "pre_s3"})
            growth_off = evolve({**common, "template": PLUS2_TEMPLATE, "out": root / "growth_off"})

            self.assertIsNone(growth_off.arc_thresholds)
            for cell in growth_off.cells:
                self.assertEqual(len(cell), 2)

            def strip_engine_hash(archive: dict) -> dict:
                for elite in archive["cells"].values():
                    elite["exemplar"].pop("engine_hash", None)
                return archive

            pre_s3_archive = strip_engine_hash(
                json.loads((root / "pre_s3" / "archive.json").read_text(encoding="utf-8"))
            )
            growth_off_archive = strip_engine_hash(
                json.loads((root / "growth_off" / "archive.json").read_text(encoding="utf-8"))
            )
            self.assertEqual(pre_s3_archive, growth_off_archive)

            def strip_run_engine_hash(results: list) -> list:
                for individual in results:
                    for run in individual["runs"]:
                        run.pop("engine_hash", None)
                return results

            for generation in range(2):
                pre_results = strip_run_engine_hash(json.loads(
                    (root / "pre_s3" / f"g{generation}" / "results.json").read_text(encoding="utf-8")
                ))
                off_results = strip_run_engine_hash(json.loads(
                    (root / "growth_off" / f"g{generation}" / "results.json").read_text(encoding="utf-8")
                ))
                self.assertEqual(pre_results, off_results)

            repository = data.RunRepository(root)
            meta = data.experiment_meta(repository, repository.experiment("growth_off"))
            self.assertEqual(meta["grid_size"], 18)
            self.assertEqual(meta["arc_bins"], [])

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


class RunWorkspaceArcAxisTests(unittest.TestCase):
    """WB-GROWTH-001 S3 re-review fix (must #1): run_workspace.observation()
    read the *captured template's* qd.yaml (inputs/templates/<id>/qd.yaml)
    to decide the river page's arc_bins, but qd.yaml merely declaring
    arc_bins is not enough (see gapengine/evolve.py) -- a growth-off run on
    momotaro_plus2 (whose qd.yaml permanently has arc_bins) reserved 54
    (18 x 3) all-empty river bands instead of 18, because band_order built
    3-element pairs that no 2-element archive cell could ever match. The
    run's own published archive (arc_thresholds present or not) must decide
    this, same as experiment_meta()'s and observation()'s own legacy-
    fallback branch already do."""

    class _Handler:
        def __init__(self, repository):
            self.repository = repository
            self.path = "/jobs/job-growth-off?view-data=1"

    def test_growth_off_river_reserves_no_more_than_the_real_grid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wb-run-workspace-arc-") as temporary:
            runs = Path(temporary) / "runs"
            runs.mkdir()
            repository = RunRepository(runs, control_root=Path(temporary) / "control")
            run_id = "run-growth-off"
            root = runs / run_id
            root.mkdir(parents=True)
            manifest = {
                "schema_version": 1, "run_id": run_id, "config_id": "cfg-growth-off",
                "evolution": {}, "target_endings": [],
            }
            manifest_bytes = canonical(manifest)
            write_bytes(root / "manifest.json", manifest_bytes)
            atomic_json(root / "complete.json", {"schema_version": 1, "manifest_sha256": sha256(manifest_bytes)})

            # A growth-off run's captured template still has arc_bins
            # declared (momotaro_plus2's own qd.yaml, unconditionally) --
            # only the published archive's own (absent) arc_thresholds says
            # the axis was actually off for this particular run.
            captured_template = root / "inputs" / "templates" / "momotaro_plus2"
            captured_template.mkdir(parents=True)
            shutil.copyfile(PLUS2_TEMPLATE / "qd.yaml", captured_template / "qd.yaml")
            self.assertIn("arc_bins", (captured_template / "qd.yaml").read_text(encoding="utf-8"))

            genome = _ga_replay_genome()
            g0 = [{
                "index": 0, "genome": genome, "parents": [],
                "cell": ["I", "low"], "classification_status": "classified",
                "shaped": 0.5, "reach_rate": 1.0,
                "runs": [{"reached": True, "quality": 0.5, "seed": 0}],
            }]
            (root / "g0").mkdir()
            (root / "g0" / "results.json").write_text(json.dumps(g0, ensure_ascii=False), encoding="utf-8")

            manifest1_bytes = _publish_revision(root, run_id, 1, {
                "I|low": {"generation": 0, "quality": 0.5, "genome": genome, "parents": []},
            })
            atomic_json(root / "published" / "current.json", {
                "schema_version": 1, "run_id": run_id, "revision": 1,
                "manifest_sha256": sha256(manifest1_bytes),
            })

            handler = self._Handler(repository)
            view = {
                "job": {
                    "run_id": run_id, "publication_revision": 1,
                    "state": "succeeded", "job_id": "job-growth-off",
                },
                "run_name": None,
                "config": {"template_id": "momotaro_plus2"},
                "axes": (
                    ["I", "II", "III", "IV", "V", "VI"], ["low", "mid", "high"],
                    ["none", "small", "large"],
                ),
            }
            result = run_workspace.observation(handler, view)

        self.assertEqual(result["arc_bins"], [])
        self.assertIsNotNone(result["river"])
        cell_bands = [b for b in result["river"]["bands"] if b["key"] != "__offmap_parent__"]
        self.assertLessEqual(len(cell_bands), 18)


class GaReplayArcCollapseTests(unittest.TestCase):
    """WB-GROWTH-001 S3 review fix (must #1): viewer/static/ga_replay.js's
    cellRects/paintCell only ever know the 2D "category|bin" grid --
    replay_model() must fold a 3-element (arc-enabled) archive's cells,
    every individual's own cell_key, and final_cells/prev_cells all down to
    that pair (highest quality wins per pair) before handing them to the
    client, or the map painted nothing at all for such a run."""

    class _Handler:
        def __init__(self, repository):
            self.repository = repository

    def test_three_element_archive_cells_collapse_to_pairs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wb-ga-replay-arc-") as temporary:
            runs = Path(temporary) / "runs"
            runs.mkdir()
            repository = RunRepository(runs, control_root=Path(temporary) / "control")
            run_id = "run-ga-replay-arc"
            root = runs / run_id
            root.mkdir(parents=True)
            manifest = {
                "schema_version": 1, "run_id": run_id, "config_id": "cfg-arc",
                "evolution": {}, "target_endings": [],
            }
            manifest_bytes = canonical(manifest)
            write_bytes(root / "manifest.json", manifest_bytes)
            atomic_json(root / "complete.json", {"schema_version": 1, "manifest_sha256": sha256(manifest_bytes)})

            genome_a = _ga_replay_genome()
            genome_b = _ga_replay_genome({"I": 0.9}, risk_tolerance=0.9)

            def result(index, genome, cell, quality):
                return {
                    "index": index, "genome": genome, "parents": [],
                    "cell": list(cell), "classification_status": "classified",
                    "shaped": quality, "reach_rate": 1.0,
                    "runs": [{"reached": True, "quality": quality, "seed": 0}],
                }

            g0 = [
                # Same (category, volatility) pair, two different arc bins --
                # the losing one (lower quality) must not shadow the winner.
                result(0, genome_a, ("I", "high", "small"), 0.4),
                result(1, genome_b, ("I", "high", "large"), 0.9),
            ]
            (root / "g0").mkdir()
            (root / "g0" / "results.json").write_text(json.dumps(g0, ensure_ascii=False), encoding="utf-8")

            manifest1_bytes = _publish_revision(root, run_id, 1, {
                "I|high|small": {"generation": 0, "quality": 0.4, "genome": genome_a, "parents": []},
                "I|high|large": {"generation": 0, "quality": 0.9, "genome": genome_b, "parents": []},
            })
            atomic_json(root / "published" / "current.json", {
                "schema_version": 1, "run_id": run_id, "revision": 1,
                "manifest_sha256": sha256(manifest1_bytes),
            })

            handler = self._Handler(repository)
            job = {"run_id": run_id, "publication_revision": 1}
            model = ga_replay.replay_model(handler, job, (["I"], ["high"]))

        self.assertIsNotNone(model)
        # The 2 archive.json rows fold into exactly 1 map cell -- the
        # higher-quality (arc=large) entry, keyed by the 2D pair only. Both
        # individuals are genuinely their own exact cell's elite here (no
        # prior generation to have lost anything to), so both read "new" --
        # see ClassifyOutcomeArcAxisTests for the case where the pair's
        # non-best entry must NOT be misclassified as "rejected".
        self.assertEqual(model["final_cells"], {"I|high": 0.9})
        self.assertEqual(
            {individual["cell_key"] for individual in model["individuals"]},
            {"I|high"},
        )
        self.assertTrue(
            all(i["outcome"]["kind"] == "new" for i in model["individuals"])
        )


class ClassifyOutcomeArcAxisTests(unittest.TestCase):
    """WB-GROWTH-001 S3 re-review fix (should #2): classify_outcome must be
    fed the *exact* archive cell (not the display-collapsed 2D pair), or an
    individual that legitimately is its own exact cell's current elite gets
    misjudged against a *different* arc bin's better entry that merely
    shares the same (category, volatility) pair -- wrongly "rejected" with
    the wrong (other bin's) incumbent_quality."""

    class _Handler:
        def __init__(self, repository):
            self.repository = repository

    def test_pair_non_best_individual_is_not_rejected_by_a_sibling_arc_bin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wb-ga-replay-arc-classify-") as temporary:
            runs = Path(temporary) / "runs"
            runs.mkdir()
            repository = RunRepository(runs, control_root=Path(temporary) / "control")
            run_id = "run-ga-replay-arc-classify"
            root = runs / run_id
            root.mkdir(parents=True)
            manifest = {
                "schema_version": 1, "run_id": run_id, "config_id": "cfg-arc",
                "evolution": {}, "target_endings": [],
            }
            manifest_bytes = canonical(manifest)
            write_bytes(root / "manifest.json", manifest_bytes)
            atomic_json(root / "complete.json", {"schema_version": 1, "manifest_sha256": sha256(manifest_bytes)})

            genome_a = _ga_replay_genome()  # wins/keeps I|high|small
            genome_b = _ga_replay_genome({"I": 0.9}, risk_tolerance=0.9)  # wins I|high|large (best of the pair)
            genome_c = _ga_replay_genome({"I": 0.2}, risk_tolerance=0.2)  # genuinely loses I|high|small

            def entry(index, genome, cell, quality):
                return {
                    "index": index, "genome": genome, "parents": [],
                    "cell": list(cell), "classification_status": "classified",
                    "shaped": quality, "reach_rate": 1.0,
                    "runs": [{"reached": True, "quality": quality, "seed": 0}],
                }

            g1 = [
                # This individual IS the exact archive elite for I|high|small
                # (replacing an earlier, lower-quality one there) -- it must
                # read "replaced", never "rejected", even though I|high|large
                # (a different arc bin, same pair) has higher quality.
                entry(0, genome_a, ("I", "high", "small"), 0.4),
                # The pair's overall best -- a brand new cell (no prior elite
                # at all, in any arc bin) -- reads "new".
                entry(1, genome_b, ("I", "high", "large"), 0.9),
                # Genuinely rejected: a different genome that did NOT win the
                # exact I|high|small cell. Its incumbent_quality must be
                # 0.4 (I|high|small's own elite), never 0.9 (the sibling
                # arc bin's higher quality).
                entry(2, genome_c, ("I", "high", "small"), 0.35),
            ]
            (root / "g1").mkdir()
            (root / "g1" / "results.json").write_text(json.dumps(g1, ensure_ascii=False), encoding="utf-8")

            _publish_revision(root, run_id, 1, {
                "I|high|small": {"generation": 0, "quality": 0.3, "genome": genome_c, "parents": []},
            })
            manifest2_bytes = _publish_revision(root, run_id, 2, {
                "I|high|small": {"generation": 1, "quality": 0.4, "genome": genome_a, "parents": []},
                "I|high|large": {"generation": 1, "quality": 0.9, "genome": genome_b, "parents": []},
            })
            atomic_json(root / "published" / "current.json", {
                "schema_version": 1, "run_id": run_id, "revision": 2,
                "manifest_sha256": sha256(manifest2_bytes),
            })

            handler = self._Handler(repository)
            job = {"run_id": run_id, "publication_revision": 2}
            model = ga_replay.replay_model(handler, job, (["I"], ["high"]))

        self.assertIsNotNone(model)
        by_index = {i["index"]: i for i in model["individuals"]}
        self.assertEqual(by_index[0]["outcome"]["kind"], "replaced")
        self.assertAlmostEqual(by_index[0]["outcome"]["prev_quality"], 0.3)
        self.assertEqual(by_index[1]["outcome"]["kind"], "new")
        self.assertEqual(by_index[2]["outcome"]["kind"], "rejected")
        self.assertAlmostEqual(by_index[2]["outcome"]["incumbent_quality"], 0.4)
        # Display painting still collapses to the pair's best (0.9), unaffected.
        self.assertEqual(model["final_cells"], {"I|high": 0.9})


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
