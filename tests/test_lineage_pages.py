"""Tests for the 系譜 (lineage) viewer page (WB-LINEAGE-002).

Reuses one small real evolve() run (see gapengine/lineage.py's own
RealRunLineageTests for why ga_seed=1/generations=5/population=8/seeds=2 is
pinned) so the page is exercised against genuine multi-generation ancestry,
an immigrant/g0 single-node lineage, and a deliberately damaged ancestor --
without paying for a second sim run.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from gapengine.evolve import evolve
from viewer import data, pages
from world_patch_fixtures import frozen_experiment


def _momotaro_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    return root / "projects" / "momotaro", root / "templates" / "momotaro"


class LineagePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        project, template = _momotaro_paths()
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cls.runs_root = root / "runs"
        evolve(
            {
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
                # So the candidate table below has real recorded candidates
                # to render, not just the empty branch (WB-LINEAGE-002 fix 4).
                "record_explanations": True,
            }
        )
        cls.repository = data.RunRepository(cls.runs_root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_cell_page_links_to_lineage(self) -> None:
        rendered = pages.cell_page(self.repository, "exp1", "III|mid", view="digest")
        self.assertIn('href="/exp/exp1/cell/III%7Cmid/lineage"', rendered)

    def test_lineage_page_multi_generation_shows_band_cards_and_detail(self) -> None:
        rendered = pages.lineage_page(self.repository, "exp1", "III|mid")
        self.assertIn('class="lineage-band"', rendered)
        # Two ancestors -> two <li> band entries.
        self.assertEqual(rendered.count("lineage-node"), 2)
        self.assertIn("出発点", rendered)
        self.assertIn("初到達", rendered)
        self.assertIn("転機", rendered)
        self.assertIn('class="turning-columns"', rendered)
        self.assertIn('aria-label="選んだ地点の詳細"', rendered)
        # Default selection is the first (only) turning point.
        self.assertIn('aria-current="page"', rendered)
        self.assertNotIn("転機が見つかりませんでした", rendered)

        # WB-LINEAGE-002 fix 4: with record_explanations on, the candidate
        # table must actually render recorded candidates and probabilities,
        # not just the "record_explanations 対象外" placeholder.
        self.assertIn('class="candidate-table"', rendered)
        self.assertNotIn("候補の記録がありません", rendered)
        candidate_table = rendered.split('class="candidate-table"', 1)[1]
        self.assertRegex(candidate_table, r"\d+%")

    def test_lineage_page_puts_trait_sparkline_in_turning_detail_not_band(self) -> None:
        # Fable fix: the "動いた性格の推移" sparkline moved from the 系譜
        # band (first section) to the selected turning's detail (third
        # section), and now traces that turning's own leading gene rather
        # than a mixed-gene personality_series.
        experiment = self.repository.experiment("exp1")
        model = data.lineage_view(self.repository, experiment, "III|mid")
        trait_series = model["turnings"][0]["trait_series"]

        rendered = pages.lineage_page(self.repository, "exp1", "III|mid")

        band_section = rendered.split('<section class="lw-overview">', 1)[1].split("</section>", 1)[0]
        self.assertNotIn('class="spark"', band_section)

        detail_section = rendered.split('id="lw-panel-traits"', 1)[1].split('id="lw-panel-records"', 1)[0]
        self.assertIn('class="spark"', detail_section)
        self.assertIn(f'{trait_series["label"]}の推移', detail_section)

    def test_lineage_page_selects_turning_from_query_param(self) -> None:
        # Only one turning exists for this cell; an out-of-range index must
        # not crash, and must fall back to the default (index 0) selection.
        rendered = pages.lineage_page(
            self.repository, "exp1", "III|mid", turning_index=99,
        )
        self.assertIn('aria-label="選んだ地点の詳細"', rendered)
        self.assertNotIn("転機が見つかりませんでした", rendered)

    def test_lineage_page_immigrant_cell_reports_no_turning_point(self) -> None:
        # V|high's elite is a g0 individual (parents == []): a single-node
        # lineage, so there is nothing to compare -- must render the "no
        # turning point" fact, not crash or silently show a stale detail.
        rendered = pages.lineage_page(self.repository, "exp1", "V|high")
        self.assertIn('class="lineage-band"', rendered)
        self.assertEqual(rendered.count("lineage-node"), 1)
        self.assertIn("転機が見つかりませんでした", rendered)

    def test_lineage_page_reports_missing_precedent_without_crashing(self) -> None:
        experiment = self.repository.experiment("exp1")
        precedent_path = experiment / "g0" / "precedent.json"
        backup = precedent_path.with_suffix(".json.bak")
        shutil.copyfile(precedent_path, backup)
        self.addCleanup(shutil.copyfile, backup, precedent_path)
        precedent_path.unlink()

        cache_path = experiment / "lineage" / "III-mid-cache.json"
        cache_path.unlink(missing_ok=True)
        self.addCleanup(cache_path.unlink, missing_ok=True)
        rerun_dir = experiment / "lineage" / "g0-archive-I-low"
        if rerun_dir.is_dir():
            shutil.rmtree(rerun_dir)

        rendered = pages.lineage_page(self.repository, "exp1", "III|mid")
        self.assertIn("再現できませんでした", rendered)
        self.assertIn("g0/archive/I-low", rendered)

    def test_data_lineage_view_rejects_unknown_cell(self) -> None:
        experiment = self.repository.experiment("exp1")
        with self.assertRaises(data.MissingResource):
            data.lineage_view(self.repository, experiment, "IX|nope")

    def test_data_lineage_view_rejects_malformed_cell_key(self) -> None:
        experiment = self.repository.experiment("exp1")
        with self.assertRaises(data.BadRequest):
            data.lineage_view(self.repository, experiment, "no-pipe-here")


class FrozenLineageInputSealTests(unittest.TestCase):
    def test_lineage_view_reports_a_broken_seal_as_bad_request_not_500(self) -> None:
        # R3 (WB-WORLDGROW-001): gapengine.lineage now routes through
        # resolve_experiment_inputs, so a frozen experiment whose sealed
        # inputs don't verify raises PatchError (fail closed -- correct).
        # Left uncaught, viewer.server's do_GET falls through its generic
        # except to an unexplained 500. viewer.data.lineage_view must
        # translate that into a BadRequest with an explanation instead.
        # Must run with no lineage/ cache present, or the corrupted read
        # never happens.
        with tempfile.TemporaryDirectory() as root:
            experiment, _project, _template = frozen_experiment(Path(root), explanations=False)
            self.assertFalse((experiment / "lineage").exists())
            repository = data.RunRepository(experiment.parent)
            archive = json.loads((experiment / "archive.json").read_text(encoding="utf-8"))
            cell_key = next(iter(archive["cells"]))

            frozen_world = experiment / "inputs/projects/momotaro/world.yaml"
            frozen_world.write_bytes(frozen_world.read_bytes() + b"# changed")

            with self.assertRaises(data.BadRequest) as caught:
                data.lineage_view(repository, experiment, cell_key)
        self.assertIn("封印", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
