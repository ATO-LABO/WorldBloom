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

    def test_world_summary_reports_goals_only(self) -> None:
        summary = world_graph.world_summary(
            self.subjects, protagonist="桃太郎", antagonist="鬼",
        )
        self.assertIn('<dl class="world-summary">', summary)
        self.assertIn("鬼ヶ島の宝物", summary)
        self.assertIn("→ 村へ", summary)
        self.assertIn("障害: 鬼", summary)
        # period/places moved to zone_svg/day_cycle_svg/calendar_grid_html.
        self.assertNotIn("16日", summary)
        self.assertNotIn("<dt>場所</dt>", summary)

    def test_zone_list_html_reports_name_and_note(self) -> None:
        world_yaml = self._world_yaml()
        listing = world_graph.zone_list_html(world_yaml["zones"])
        self.assertIn("鬼ヶ島", listing)
        self.assertIn("鬼が宝物を守る固定の島", listing)

    def test_zone_svg_structure(self) -> None:
        world_yaml = self._world_yaml()
        svg = world_graph.zone_svg(world_yaml["zones"], world_yaml["routes"])
        self.assertIn('<svg class="relation-graph zone-graph"', svg)
        self.assertEqual(svg.count('class="node'), 5)
        # 海<->鬼ヶ島 is the only routed pair carrying a cost/requires_item.
        self.assertIn("要: 船", svg)

    def test_zone_svg_is_deterministic(self) -> None:
        world_yaml = self._world_yaml()
        first = world_graph.zone_svg(world_yaml["zones"], world_yaml["routes"])
        second = world_graph.zone_svg(world_yaml["zones"], world_yaml["routes"])
        self.assertEqual(first, second)

    def test_zone_svg_empty_zones(self) -> None:
        self.assertIn("場所がありません", world_graph.zone_svg([], {}))

    def test_day_cycle_svg_has_one_wedge_per_slot(self) -> None:
        svg = world_graph.day_cycle_svg(["朝", "昼", "夕方", "夜"])
        self.assertIn('<svg class="day-cycle"', svg)
        self.assertEqual(svg.count("<circle"), 4)
        self.assertIn("夕方", svg)

    def test_day_cycle_svg_empty_slots(self) -> None:
        self.assertIn("時間帯がありません", world_graph.day_cycle_svg([]))

    def test_calendar_grid_html_has_one_cell_per_day(self) -> None:
        grid = world_graph.calendar_grid_html(8)
        self.assertEqual(grid.count("calendar-day"), 8)
        self.assertIn(">8<", grid)

    def test_calendar_grid_html_caps_absurd_day_counts(self) -> None:
        # world.yaml's time.days is user-editable via this app's own file
        # editor -- a typo'd extra zero must not render one div per day.
        grid = world_graph.calendar_grid_html(100000)
        self.assertNotIn("calendar-day", grid)
        self.assertIn("100000", grid)

    def test_calendar_grid_html_zero_days(self) -> None:
        self.assertIn("期間がありません", world_graph.calendar_grid_html(0))

    @staticmethod
    def _world_yaml():
        import yaml

        return yaml.safe_load((MOMOTARO / "world.yaml").read_text(encoding="utf-8"))


class CharacterTableEscapeTests(unittest.TestCase):
    def test_companions_are_escaped(self):
        subjects = [{"id": "a", "companions": ["<script>alert(1)</script>"]}]
        table = world_graph.character_table(subjects, protagonist=None, antagonist=None)
        self.assertNotIn("<script>", table)
        self.assertIn("&lt;script&gt;", table)


if __name__ == "__main__":
    unittest.main()
