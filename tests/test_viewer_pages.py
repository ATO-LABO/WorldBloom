"""Unit tests for deterministic WorldBloom viewer data and pages."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from test_viewer import (
    _create_experiment,
    _fixture_rows,
    _write_jsonl,
)
from viewer import data, pages, server


class ViewerPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "runs"
        self.experiment = _create_experiment(self.runs_root)
        self.repository = data.RunRepository(self.runs_root)

    def test_resolve_genre(self) -> None:
        resolved = data.resolve_genre("桃太郎")
        self.assertEqual(
            resolved,
            (
                "momotaro",
                data.ROOT / "projects" / "momotaro",
                data.ROOT / "templates" / "momotaro",
            ),
        )
        self.assertIsNone(data.resolve_genre("存在しない世界"))

    def test_detective_axes(self) -> None:
        self.assertEqual(
            data.qd_axes(data.ROOT / "templates" / "detective"),
            (["I", "II", "III"], ["low", "mid", "high"]),
        )

    def test_experiment_meta(self) -> None:
        meta = data.experiment_meta(
            self.repository,
            self.experiment,
        )
        self.assertEqual(meta["world"], "桃太郎")
        self.assertEqual(meta["genre"], "momotaro")
        self.assertEqual(meta["generations"], 0)
        self.assertIsNone(meta["population"])
        self.assertEqual(meta["synopsis_ok"], 1)
        self.assertEqual(meta["story_ok"], 1)
        self.assertEqual(meta["selected"], 0)
        self.assertTrue(data.is_minor(meta))

    def test_cell_view(self) -> None:
        model = data.cell_view(
            self.repository,
            self.experiment,
            "III|high",
            view="digest",
        )
        self.assertGreaterEqual(len(model["scenes"]), 1)
        self.assertIn("桃太郎", model["scenes"][0]["events"][0])
        self.assertEqual(model["day_states"][1]["zone"], "村")
        self.assertEqual(
            sum(
                marker["kind"] == "ending"
                for marker in model["markers"]
            ),
            1,
        )

    def test_cell_page_structure(self) -> None:
        rendered = pages.cell_page(
            self.repository,
            "exp-viewer",
            "III|high",
            view="digest",
        )
        raw_start = rendered.index('<details class="raw"')
        raw_tag_end = rendered.index(">", raw_start)
        self.assertNotIn(" open", rendered[raw_start:raw_tag_end])
        self.assertIn('class="timeline"', rendered)
        self.assertIn(
            '<script src="/static/app.js" defer>',
            rendered,
        )
        self.assertNotIn("<script>", rendered)

    def test_index_includes_minor_run(self) -> None:
        rendered = pages.index_page(self.repository)
        self.assertIn("その他の短いラン", rendered)
        self.assertIn("exp-viewer", rendered)

    def test_detail_lines(self) -> None:
        rethink = data.detail_line(
            {
                "verb": "rethink",
                "details": {
                    "before": {
                        "culprit": {
                            "value": "乙",
                            "confidence": 0.72,
                        }
                    },
                    "after": {},
                },
            },
            {},
        )
        self.assertIsNotNone(rethink)
        self.assertIn("乙", rethink)
        self.assertIn("保留", rethink)

        confront = data.detail_line(
            {
                "verb": "confront",
                "details": {
                    "correct": True,
                    "confidence": 0.75,
                },
            },
            {},
        )
        self.assertIsNotNone(confront)
        self.assertIn("正解", confront)

        learned = data.detail_line(
            {
                "verb": "learn_fact",
                "details": {
                    "beliefs": [
                        {
                            "fact": "culprit",
                            "outcome": "adopted",
                            "before": None,
                            "after": {
                                "value": "乙",
                                "confidence": 0.72,
                            },
                        }
                    ]
                },
            },
            {},
        )
        self.assertIsNotNone(learned)
        self.assertIn("adopted", learned)
        self.assertIsNone(
            data.detail_line({"verb": "move"}, {})
        )

    def test_static_path_allowlist(self) -> None:
        self.assertEqual(
            server.static_path("app.css"),
            data.ROOT / "viewer" / "static" / "app.css",
        )
        with self.assertRaises(data.ForbiddenPath):
            server.static_path("../server.py")
        with self.assertRaises(data.MissingResource):
            server.static_path("nope.css")

    def test_tendency_is_distinguished_from_the_grid_row(self) -> None:
        # The row is the category the run actually chose most; the cell field
        # is what the genome weights. Both were called 主導 before.
        self.assertIn("傾向", pages.METRIC_HELP["tendency"])
        self.assertIn("主導カテゴリ", pages.METRIC_HELP["tendency"])
        self.assertIn("一致しない", pages.METRIC_HELP["tendency"])

    def test_term_and_glossary_share_term_help(self) -> None:
        # WB-UI-013: term() without a label falls back to the heading word
        # embedded in TERM_HELP[key] ("<heading>: <definition>"); glossary()
        # must render the same heading/definition split, from the same dict
        # (no separate copy to drift out of sync).
        self.assertIs(pages.METRIC_HELP, pages.TERM_HELP)
        rendered = pages.term("candidate_id")
        self.assertIn('title="', rendered)
        self.assertIn(">候補ID<", rendered)
        labelled = pages.term("candidate_id", "ID")
        self.assertIn(">ID<", labelled)
        block = pages.glossary(["candidate_id", "cell"])
        self.assertIn("<dt>候補ID</dt>", block)
        self.assertIn("<dt>セル</dt>", block)
        self.assertIn(pages.TERM_HELP["candidate_id"].split(": ", 1)[1], block)

    def test_document_lead_and_next_action(self) -> None:
        html = pages.document(
            "タイトル", "<p>本文</p>", lead="目的の説明。",
            next_action=("次へ →", "/somewhere"),
        )
        self.assertIn('<p class="page-lead">目的の説明。 '
                       '<a class="next-cta" href="/somewhere">次: 次へ →</a></p>', html)
        no_action = pages.document("タイトル", "<p>本文</p>", lead="目的のみ。")
        self.assertIn('<p class="page-lead">目的のみ。 </p>', no_action)
        neither = pages.document("タイトル", "<p>本文</p>")
        self.assertNotIn("page-lead", neither)

    def test_next_action_for_matches_home_and_grid(self) -> None:
        # WB-UI-012 §5.3: /exp/<run>'s "next:" must agree with the home
        # dashboard's for the same run -- both now call next_action_for().
        phases = {"world": True, "run": True, "sifting": False, "stage": False, "next": "sifting"}
        home_next = pages._next_command({"name": "exp-x"}, phases)
        grid_next = pages.next_action_for("exp-x", phases)
        self.assertEqual(home_next, grid_next)
        self.assertEqual(grid_next, ("Sifting で候補を選ぶ →", "/exp/exp-x"))

    def test_grade_ramp_and_gate_exclusion(self) -> None:
        self.assertEqual(pages._grade(1.0), "grade-strong")
        self.assertEqual(pages._grade(0.67), "grade-good")
        self.assertEqual(pages._grade(0.50), "grade-fair")
        self.assertEqual(pages._grade(0.30), "grade-weak")
        self.assertEqual(pages._grade(0.10), "grade-poor")
        self.assertEqual(pages._grade(None), "grade-none")
        self.assertEqual(pages._ratio(9, 9), 1.0)
        self.assertAlmostEqual(pages._ratio(7, 18), 7 / 18)
        self.assertIsNone(pages._ratio(1, 0))
        self.assertIsNone(pages._ratio("x", 9))

    def test_reach_is_never_graded(self) -> None:
        # Colouring the gate green at 100% would re-create the fixation the
        # muted treatment exists to prevent.
        markup = pages._experiment_card(
            {
                "name": "exp-x",
                "genre": "探偵",
                "world": "白椿館の密室",
                "generations": 20,
                "population": 100,
                "seeds": [2, 3, 4],
                "cells": 9,
                "grid_size": 9,
                "reach_series": [0.06, 1.0],
                "occupied_series": [1, 9],
                "quality_series": [0.49, 0.56],
                "dissimilarity_series": [0.76, 0.67],
                "synopsis_ok": 9,
                "selected": 0,
                "story_ok": 0,
                "engine_hash": "b7f7c2d06d2d",
                "archive_mtime": 1757000000.0,
                "target_ending": "solved",
                "truths": "甲/乙/丙",
            }
        )
        reach = markup[markup.index("到達率") :]
        reach = reach[: reach.index("</span>")]
        self.assertNotIn("grade-", reach)

    def test_metric_labels_carry_help_text(self) -> None:
        # Every metric name is explained on hover; the text lives in one place.
        for key in (
            "cells",
            "dissimilarity",
            "quality",
            "reach",
            "tendency",
            "generation",
            "seed",
            "parents",
            "layers",
            "elite_reach",
        ):
            rendered = pages._tip(key, "ラベル")
            self.assertIn('class="tip"', rendered)
            self.assertIn("title=", rendered)
            self.assertIn("ラベル", rendered)
        self.assertIn(
            "最大化する対象ではない",
            pages.METRIC_HELP["reach"],
        )

    def test_gate_figure_has_no_progress_arrow(self) -> None:
        # 到達率 admits a run to the archive; it is not what the search
        # maximises, so it must not be dressed as an improving KPI.
        self.assertEqual(pages._gate_text([0.06, 0.14, 0.20]), "20%")
        self.assertEqual(pages._gate_text([]), "—")

    def test_dissimilarity_note_only_when_it_fell(self) -> None:
        self.assertIn("低下", pages._dissimilarity_note([0.76, 0.67]))
        self.assertEqual(pages._dissimilarity_note([0.60, 0.67]), "")
        self.assertEqual(pages._dissimilarity_note([0.67]), "")

    def test_elite_reach_shown_as_seed_fraction(self) -> None:
        # The generation figure keeps the name 到達率; an elite is only run on
        # its own K seeds, so it reads as a fraction to avoid the confusion.
        self.assertEqual(pages._reached_seeds(2 / 3, 3), "2/3 シード")
        self.assertEqual(pages._reached_seeds(1.0, 3), "3/3 シード")
        self.assertEqual(pages._reached_seeds(0.0, 3), "0/3 シード")
        self.assertEqual(pages._reached_seeds(0.2, 0), "20.0%")

    def test_layer_points_use_documented_vector_indices(self) -> None:
        rows = [
            {
                "kind": "snapshot",
                "day": 1,
                "turn": 1,
                "vector": [
                    0.2,
                    0.6,
                    0.1,
                    0.2,
                    0.3,
                    0.4,
                    0.7,
                    0.8,
                    0.9,
                    1.0,
                    0.5,
                ],
                "layers": {
                    "pending": ["a", "b"],
                },
            },
            {
                "kind": "snapshot",
                "day": 2,
                "turn": 2,
                "vector": [
                    0.4,
                    0.8,
                    0.2,
                    0.4,
                    0.6,
                    0.8,
                    0.3,
                    0.1,
                    0.5,
                    0.7,
                    1.0,
                ],
                "layers": {
                    "pending": ["a"],
                },
            },
        ]

        points = data.layer_points(rows)

        for actual, expected in zip(
            points[0]["values"],
            [0.4, 0.8, 0.25, 0.7, 0.9, 1.0, 1.0],
            strict=True,
        ):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(
            points[1]["values"],
            [0.6, 0.1, 0.5, 0.3, 0.5, 0.7, 0.5],
            strict=True,
        ):
            self.assertAlmostEqual(actual, expected)

    def test_layers_svg_keeps_and_separates_markers(self) -> None:
        points = [
            {
                "day": 1,
                "turn": 1,
                "values": [0.0] * 7,
            },
            {
                "day": 2,
                "turn": 2,
                "values": [1.0] * 7,
            },
        ]
        markers = [
            {"day": 4, "turn": 8, "kind": "downed"},
            {"day": 4, "turn": 9, "kind": "revived"},
            {"day": 4, "turn": 10, "kind": "ending"},
        ]

        rendered = pages.layers_svg(points, markers)

        view_box = re.search(
            r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"',
            rendered,
        )
        self.assertIsNotNone(view_box)
        width = float(view_box.group(1))
        marker_x = [
            float(value)
            for value in re.findall(
                r'data-marker="true"[^>]*x="([0-9.]+)"',
                rendered,
            )
        ]
        self.assertEqual(len(marker_x), len(markers))
        self.assertTrue(
            all(0.0 <= value <= width for value in marker_x)
        )
        self.assertEqual(len(set(marker_x)), len(markers))

    def test_view_levels_are_cumulative_and_digest_keeps_turning_point(
        self,
    ) -> None:
        layers_path = (
            self.experiment
            / "g0"
            / "ind-0"
            / "seed-7"
            / "layers.jsonl"
        )
        rows = _fixture_rows()
        rows.append(
            {
                "args": [],
                "day": 2,
                "delta": {
                    "actor": {
                        "valued_beliefs": {
                            "culprit": None,
                        }
                    },
                    "objective": None,
                    "relations": [],
                    "targets": {},
                },
                "details": {
                    "before": {
                        "culprit": {
                            "value": "鬼",
                            "confidence": 0.72,
                        }
                    },
                    "after": {},
                    "evidence": ["村の証言"],
                },
                "effective": True,
                "kind": "decision",
                "result": "rethought",
                "slot": "朝",
                "subject": "桃太郎",
                "turn": 3,
                "verb": "rethink",
            }
        )
        _write_jsonl(layers_path, rows)

        digest = data.cell_view(
            self.repository,
            self.experiment,
            "III|high",
            view="digest",
        )
        decisions = data.cell_view(
            self.repository,
            self.experiment,
            "III|high",
            view="decisions",
        )
        all_rows = data.cell_view(
            self.repository,
            self.experiment,
            "III|high",
            view="all",
        )

        self.assertLessEqual(
            len(digest["scenes"]),
            len(decisions["scenes"]),
        )
        self.assertLessEqual(
            len(decisions["scenes"]),
            len(all_rows["scenes"]),
        )
        self.assertEqual(
            len(decisions["scenes"]),
            len(all_rows["scenes"]),
        )
        rethink_scene = next(
            scene
            for scene in digest["scenes"]
            if scene["turn"] == 3
        )
        self.assertTrue(rethink_scene["turning"])
        self.assertTrue(
            any("→" in detail for detail in rethink_scene["details"])
        )


if __name__ == "__main__":
    unittest.main()
