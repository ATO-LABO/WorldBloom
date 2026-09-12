"""Unit tests for deterministic WorldBloom viewer data and pages."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_viewer import _create_experiment
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
        self.assertEqual(meta["generations"], 1)
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


if __name__ == "__main__":
    unittest.main()
