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

    def test_canon_table_html_reports_situation_action_and_count(self) -> None:
        canon = self._genre_yaml("canon.yaml")
        table = world_graph.canon_table_html(canon)
        self.assertIn('<table class="wb-table canon-table">', table)
        self.assertEqual(table.count("<tr>"), len(canon["entries"]) + 1)  # +1 header row
        # ctx.phase=[出発, 越境] + act.verb=fight should read as Japanese text,
        # not raw YAML tokens.
        self.assertIn("出発・越境", table)
        self.assertIn("戦った", table)

    def test_canon_table_html_empty(self) -> None:
        self.assertIn("正典データがありません", world_graph.canon_table_html({}))

    def test_character_readout_html_surfaces_the_oni_kanabo_story(self) -> None:
        # 鬼's 金棒 (kanabo) is a hidden +40 strength modifier that 桃太郎
        # doesn't know about yet (his belief stays at 80), tied to a
        # secret_of=鬼 fact and an effects.yaml plant/payoff pair -- exactly
        # the "why is this number what it is" story WB-EXPLAIN-canon targets.
        # True strength = base(80) + 金棒(40) + epsilon(5.0)*temperament,
        # where temperament = 0.65*stubbornness(0.80) + 0.20*social(0.30)
        # + 0.15*curiosity(0.20) = 0.61 -> 80 + 40 + 3.05 = 123 (engine's
        # contest.strength(); believed_strength has no temperament term, so
        # 桃太郎's stale belief stays exactly 80).
        world_yaml = self._world_yaml()
        effects = self._genre_yaml("effects.yaml")
        readout = world_graph.character_readout_html(world_yaml, self.subjects, effects)
        self.assertIn("鬼", readout)
        self.assertIn("基礎の強さ 80", readout)
        self.assertIn("実際の強さ 123", readout)
        self.assertIn("金棒: +40", readout)
        self.assertIn("隠れた強化", readout)
        self.assertIn("桃太郎の当初の見積もり: 80（実際は123）", readout)
        self.assertIn("鬼の力は金棒に支えられている", readout)
        self.assertIn("金棒の隙を見切って仲間に合図を送る", readout)

    def test_character_readout_html_treats_missing_visible_as_true(self) -> None:
        # engine/subject.py and engine/world.py both default an omitted
        # `visible` to True -- a modifier without the key must NOT be shown
        # as hidden, or as a mismatch against an observer who has no reason
        # to be wrong about it (Opus review finding, WB-EXPLAIN-canon).
        world_yaml = {
            "items": [{"name": "杖", "modifier": {"value": 10}}],
            "facts": [],
        }
        subjects = [
            {"id": "A", "base": 50, "inventory": {"杖": 1}},
            {
                "id": "B",
                "beliefs_about": {"A": {"base_estimate": 50, "known_modifiers": []}},
            },
        ]
        readout = world_graph.character_readout_html(world_yaml, subjects, [])
        self.assertIn("杖: +10（他の登場人物にも見えている）", readout)
        self.assertNotIn("隠れた強化", readout)
        self.assertNotIn("Bの当初の見積もり", readout)

    def test_character_readout_html_includes_own_modifiers_and_temperament(self) -> None:
        # Subject-declared `modifiers:` (not just item-derived ones) and the
        # contest.epsilon*temperament term both feed engine/contest.py's
        # strength() and must not be silently dropped from the total.
        world_yaml = {"contest": {"epsilon": 5.0}, "items": [], "facts": []}
        subjects = [
            {
                "id": "A",
                "base": 50,
                "traits": {"stubbornness": 1.0, "social": 0.0, "curiosity": 0.0},
                "modifiers": [{"source": "training", "value": 8, "visible": True}],
            },
        ]
        readout = world_graph.character_readout_html(world_yaml, subjects, [])
        self.assertIn("training: +8", readout)
        self.assertIn("性格による小さな補正 +3.2", readout)
        self.assertIn("実際の強さ 61", readout)  # 50 + 8 + 5.0*0.65

    def test_character_readout_html_defaults_epsilon_like_the_engine(self) -> None:
        # engine/world.py defaults contest.epsilon to 5.0 when world.yaml
        # omits the `contest:` block entirely -- silently treating it as 0
        # would understate every character's real strength (Opus review
        # finding, second pass).
        world_yaml = {"items": [], "facts": []}  # no "contest" key at all
        subjects = [
            {"id": "A", "base": 50, "traits": {"stubbornness": 1.0, "social": 0.0, "curiosity": 0.0}},
        ]
        readout = world_graph.character_readout_html(world_yaml, subjects, [])
        self.assertIn("性格による小さな補正 +3.2", readout)  # 5.0*0.65
        self.assertIn("実際の強さ 53", readout)  # 50 + 5.0*0.65

    def test_character_readout_html_is_deterministic(self) -> None:
        world_yaml = self._world_yaml()
        effects = self._genre_yaml("effects.yaml")
        first = world_graph.character_readout_html(world_yaml, self.subjects, effects)
        second = world_graph.character_readout_html(world_yaml, self.subjects, effects)
        self.assertEqual(first, second)

    def test_character_readout_html_empty_inputs(self) -> None:
        self.assertIn("読み下せる設定がありません", world_graph.character_readout_html({}, [], []))

    @staticmethod
    def _world_yaml():
        import yaml

        return yaml.safe_load((MOMOTARO / "world.yaml").read_text(encoding="utf-8"))

    @staticmethod
    def _genre_yaml(filename):
        import yaml

        path = ROOT / "templates" / "momotaro" / filename
        return yaml.safe_load(path.read_text(encoding="utf-8"))


class CharacterTableEscapeTests(unittest.TestCase):
    def test_companions_are_escaped(self):
        subjects = [{"id": "a", "companions": ["<script>alert(1)</script>"]}]
        table = world_graph.character_table(subjects, protagonist=None, antagonist=None)
        self.assertNotIn("<script>", table)
        self.assertIn("&lt;script&gt;", table)


if __name__ == "__main__":
    unittest.main()
