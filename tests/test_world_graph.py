"""Unit tests for viewer/world_graph.py (WB-UI-016 relation graph)."""

from __future__ import annotations

import unittest
from pathlib import Path

from viewer import world_graph

ROOT = Path(__file__).resolve().parents[1]
MOMOTARO = ROOT / "projects" / "momotaro"


class WorldGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.subjects = world_graph.load_subjects(MOMOTARO)

    def test_load_subjects_returns_seven_momotaro_subjects(self) -> None:
        self.assertEqual(len(self.subjects), 7)
        self.assertEqual(
            [subject["id"] for subject in self.subjects],
            ["おじいさん", "おばあさん", "桃太郎", "犬", "猿", "キジ", "鬼"],
        )

    def test_relation_svg_structure(self) -> None:
        svg = world_graph.relation_svg(
            self.subjects, protagonist="桃太郎", antagonist="鬼",
        )
        self.assertIn('<svg class="relation-graph"', svg)
        self.assertEqual(svg.count('class="node'), 7)
        self.assertNotIn("旅の商人", svg)
        self.assertEqual(svg.count("is-protagonist"), 1)
        self.assertEqual(svg.count("is-antagonist"), 1)
        self.assertIn("桃太郎→鬼", svg)

    def test_relation_svg_is_deterministic(self) -> None:
        first = world_graph.relation_svg(self.subjects, protagonist="桃太郎", antagonist="鬼")
        second = world_graph.relation_svg(self.subjects, protagonist="桃太郎", antagonist="鬼")
        self.assertEqual(first, second)

    def test_relation_svg_empty_subjects(self) -> None:
        self.assertIn("人物がいません", world_graph.relation_svg([], protagonist=None, antagonist=None))

    def test_character_table_excludes_self_from_companions(self) -> None:
        table = world_graph.character_table(self.subjects, protagonist="桃太郎", antagonist="鬼")
        self.assertIn("主人公", table)
        self.assertIn("敵役", table)
        # 桃太郎's own companions list is [桃太郎, 猿, 犬, キジ]; the table must
        # drop the self-reference and show only the other three.
        self.assertIn("猿、犬、キジ", table)

    def test_world_summary_reports_period_places_and_goals(self) -> None:
        import yaml

        world_yaml = yaml.safe_load((MOMOTARO / "world.yaml").read_text(encoding="utf-8"))
        summary = world_graph.world_summary(
            world_yaml, self.subjects, protagonist="桃太郎", antagonist="鬼",
        )
        self.assertIn('<dl class="world-summary">', summary)
        self.assertIn("16日", summary)
        self.assertIn("村", summary)
        self.assertIn("鬼ヶ島の宝物", summary)
        self.assertIn("→ 村へ", summary)
        self.assertIn("障害: 鬼", summary)


class CharacterTableEscapeTests(unittest.TestCase):
    def test_companions_are_escaped(self):
        subjects = [{"id": "a", "companions": ["<script>alert(1)</script>"]}]
        table = world_graph.character_table(subjects, protagonist=None, antagonist=None)
        self.assertNotIn("<script>", table)
        self.assertIn("&lt;script&gt;", table)


if __name__ == "__main__":
    unittest.main()
