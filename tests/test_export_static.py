import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from scripts import export_static as es


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class NoRaiseParser(HTMLParser):
    """Fails the test if the HTML cannot even be tokenized."""


def _build_fixture(root: Path):
    runs = root / "runs"
    exp_dir = runs / "exp1-momotaro"

    _write_json(exp_dir / "archive.json", {
        "cells": {
            "I|low": {"quality": 0.5, "reach_rate": 0.25, "generation": 1},
        }
    })
    _write_json(exp_dir / "summary.json", {
        "final_archive_dissimilarity": 0.7,
        "seeds": [0, 1],
        "generations": [
            {"reach_rate": 0.1, "average_archive_quality": 0.2, "occupied_cells": 1},
            {"reach_rate": 0.3, "average_archive_quality": 0.4, "occupied_cells": 1},
        ],
    })
    _write_json(exp_dir / "synopses.json", {
        "world": "テスト世界<物語>",
        "backend": "ollama",
        "entries": [
            {"cell": "I|low", "status": "ok",
             "synopsis": "これはとても長いあらすじの本文です" * 3,
             "layers_path": "g1/ind-1/seed-0/layers.jsonl"},
        ],
    })
    _write_json(exp_dir / "selection.json", {"selected": ["I|low"]})
    _write_json(exp_dir / "stories" / "index.json", {
        "world": "テスト世界<物語>", "backend": "ollama",
        "entries": [{"cell": "I|low", "status": "ok", "story_path": "I-low.md"}],
    })
    (exp_dir / "stories" / "I-low.md").write_text(
        "むかしむかし、<危険>な鬼がいた。\n二行目の本文。\n", encoding="utf-8")

    # templates/momotaro/qd.yaml gives categories; fall back path if absent is fine too.
    qd_dir = root / "templates" / "momotaro"
    qd_dir.mkdir(parents=True, exist_ok=True)
    (qd_dir / "qd.yaml").write_text("categories: [I]\nvolatility_bins: [low, mid, high]\n",
                                     encoding="utf-8")
    return runs


class ExportStaticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runs = _build_fixture(self.root)
        self.out = self.root / "site"

    def test_export_produces_index_and_story_pages(self):
        result = es.export(self.root, self.runs, self.out, ["exp1-momotaro"])
        index_html = (self.out / "index.html").read_text(encoding="utf-8")

        self.assertIn("テスト世界&lt;物語&gt;", index_html)
        self.assertIn("I 力", index_html)
        self.assertIn("これはとても長いあらすじの本文です"[:40], index_html)
        self.assertIn("本文を読む", index_html)
        self.assertIn('href="stories/momotaro-I-low.html"', index_html)

        story_path = self.out / "stories" / "momotaro-I-low.html"
        self.assertTrue(story_path.exists())
        self.assertEqual(result["stories"], [story_path])

        story_html = story_path.read_text(encoding="utf-8")
        self.assertIn("むかしむかし、&lt;危険&gt;な鬼がいた。", story_html)
        self.assertNotIn("<危険>", story_html)

        # Every generated page must at least tokenize cleanly.
        for page in [self.out / "index.html", story_path]:
            NoRaiseParser().feed(page.read_text(encoding="utf-8"))

    def test_missing_experiment_data_renders_as_not_yet_generated(self):
        es.export(self.root, self.runs, self.out, ["exp1-momotaro", "exp-missing"])
        index_html = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn("未生成", index_html)


if __name__ == "__main__":
    unittest.main()
