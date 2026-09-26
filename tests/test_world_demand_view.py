"""Unit tests for viewer/world_demand_view.py (moved out of test_viewer_pages.py
when the '世界の需要' block moved from pages.experiment_page to
run_workspace.py's 5th tab -- WB-WORLDGROW-001 stage 2/3a follow-up)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from test_viewer import _create_experiment, _write_json
from viewer import data, world_demand_view


class WorldDemandViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "runs"
        self.experiment = _create_experiment(self.runs_root)
        self.repository = data.RunRepository(self.runs_root)

    def test_experiment_page_without_world_demand_shows_guidance(self) -> None:
        rendered = world_demand_view.demand_block(self.repository, self.experiment)
        self.assertIn("世界の需要", rendered)
        self.assertIn(
            "この実験は世界の需要を集計していません",
            rendered,
        )

    def test_experiment_page_with_world_demand_shows_triggers_and_zone_table(
        self,
    ) -> None:
        _write_json(
            self.experiment / "world_demand.json",
            {
                "schema_version": 1,
                "files": 1,
                "skipped_paths": 0,
                "subject_decisions": 4,
                "zones": [
                    {
                        "zone": "海",
                        "decisions": 3,
                        "dwell": 3,
                        "dwell_share": 1.0,
                        "verbs": [["investigate", 3, 1.0]],
                        "repeat_rate": 0.0,
                        "ineffective_rate": 1.0,
                        "ineffective_reasons": [["invalid", 3]],
                        "mean_p_prec": None,
                        "mean_m_nov": None,
                        "mean_candidates": None,
                    },
                ],
                "triggers": [
                    {
                        "zone": "海",
                        "verb": "investigate",
                        "count": 3,
                        "whiffs": 3,
                        "whiff_rate": 1.0,
                        "wasted_share": 0.5,
                        "zone_dwell_share": 1.0,
                    },
                ],
                "archive": None,
                "thresholds": {"whiff_rate_min": 0.5, "wasted_share_min": 0.02},
            },
        )
        rendered = world_demand_view.demand_block(self.repository, self.experiment)
        self.assertIn("世界の需要", rendered)
        self.assertNotIn("この実験は世界の需要を集計していません", rendered)
        self.assertIn("investigate", rendered)
        self.assertIn("50.0%", rendered)
        self.assertIn("ゾーン別の詳細", rendered)
        # WB-UI world-demand tab: whiff counts always carry their own trial-rate too.
        self.assertIn("3 回中 3 回が空振り（100.0%）", rendered)
        self.assertIn("100.0%（3回中）", rendered)

    def test_experiment_page_survives_a_malformed_world_demand_report(self) -> None:
        _write_json(
            self.experiment / "world_demand.json",
            {
                "schema_version": 1,
                "zones": [{"zone": "海", "verbs": [["investigate", 3], "junk", None]}, "junk"],
                "triggers": [{"zone": "海", "verb": "investigate", "wasted_share": None}, 7],
            },
        )
        rendered = world_demand_view.demand_block(self.repository, self.experiment)
        self.assertIn("世界の需要", rendered)
        self.assertIn("investigate×3", rendered)

    def test_experiment_page_shows_base_world_line_without_patches(self) -> None:
        rendered = world_demand_view.demand_block(self.repository, self.experiment)
        self.assertIn("この実験の世界: ベース（拡張なし）", rendered)

    def test_experiment_page_shows_expansion_patches(self) -> None:
        world = {
            "name": "桃太郎",
            "expansion": {
                "base": "桃太郎",
                "patches": [
                    {"id": "p-1a2b3c4d", "title": "海辺の船大工小屋",
                     "trigger": {"zone": "海", "verb": "investigate"},
                     "added": {"zones": ["船大工の小屋"], "items": ["古びた帆布"],
                               "facts": [], "daily_events": []}},
                ],
            },
        }
        (self.experiment / "expanded-project").mkdir()
        (self.experiment / "expanded-project" / "world.yaml").write_text(
            yaml.safe_dump(world, allow_unicode=True), encoding="utf-8")
        rendered = world_demand_view.demand_block(self.repository, self.experiment)
        self.assertIn("この実験の世界: 拡張あり", rendered)
        self.assertIn("海辺の船大工小屋", rendered)
        self.assertIn("p-1a2b3c4d", rendered)
        self.assertIn("海 で investigate", rendered)
        self.assertIn("船大工の小屋", rendered)
        self.assertIn("古びた帆布", rendered)

    def test_experiment_page_survives_malformed_expansion_shape(self) -> None:
        (self.experiment / "expanded-project").mkdir()
        (self.experiment / "expanded-project" / "world.yaml").write_text(
            "name: x\nexpansion: not-a-mapping\n", encoding="utf-8")
        rendered = world_demand_view.demand_block(self.repository, self.experiment)
        self.assertIn("この実験の世界: ベース（拡張なし）", rendered)

    def test_summary_text_matches_expansion_line_judgement(self) -> None:
        self.assertEqual(world_demand_view.summary_text({"state": "base", "patches": []}), "ベース（拡張なし）")
        self.assertEqual(
            world_demand_view.summary_text({"state": "expanded", "patches": [{"id": "p-1"}]}),
            "拡張あり（1件）",
        )
        self.assertEqual(
            world_demand_view.summary_text({"state": "unknown", "patches": []}),
            "不明（拡張の情報が欠けています）",
        )

    def _write_two_triggers(self) -> None:
        # observe is not investigate -- the button must only appear on the
        # investigate trigger, and data-trigger must stay the RAW index into
        # this list (1), not an investigate-only count (0).
        _write_json(
            self.experiment / "world_demand.json",
            {
                "schema_version": 1,
                "zones": [],
                "triggers": [
                    {"zone": "村", "verb": "observe", "count": 5, "whiffs": 2, "wasted_share": 0.1},
                    {"zone": "海", "verb": "investigate", "count": 3, "whiffs": 3, "wasted_share": 0.5},
                ],
            },
        )

    def test_propose_run_adds_button_only_to_investigate_trigger_with_raw_index(self) -> None:
        self._write_two_triggers()
        rendered = world_demand_view.demand_block(self.repository, self.experiment, propose_run="exp-viewer")
        self.assertIn(
            'data-patch-action="propose" data-run="exp-viewer" data-trigger="1"',
            rendered,
        )
        # The non-investigate (observe) trigger gets the note, not a button.
        self.assertIn("「investigate」＝調べる、の空振りにだけ拡張を提案できます", rendered)
        # Only one button total (for the investigate trigger).
        self.assertEqual(rendered.count('data-patch-action="propose"'), 1)
        # Time estimate line is present once, above the list.
        self.assertIn("6〜11分かかります", rendered)
        self.assertLess(rendered.index("6〜11分"), rendered.index("data-trigger=\"0\""))

    def test_raw_index_survives_a_non_mapping_trigger(self) -> None:
        # Entries that are not objects are skipped in the list but still count
        # toward the index -- the server converts this same raw index.
        _write_json(self.experiment / "world_demand.json", {
            "schema_version": 1, "zones": [],
            "triggers": [None, {"zone": "村", "verb": "observe", "count": 5, "whiffs": 2, "wasted_share": 0.1},
                         {"zone": "海", "verb": "investigate", "count": 3, "whiffs": 3, "wasted_share": 0.5}]})
        rendered = world_demand_view.demand_block(self.repository, self.experiment, propose_run="exp-viewer")
        self.assertIn('data-patch-action="propose" data-run="exp-viewer" data-trigger="2"', rendered)

    def test_ignorance_and_blocked_triggers_are_skipped_until_stage_2_4(self) -> None:
        # WB-WORLDGROW-002 S1: this card only ever renders "whiff" triggers
        # (verb/whiffs-shaped) -- an "ignorance"/"blocked" one from a
        # route-wired run must not blow up _trigger_li nor shift the whiff
        # trigger's own raw index (kept from test_raw_index_survives_a_
        # non_mapping_trigger's same "non-Mapping counts toward the index"
        # contract -- kind-skipped entries count the same way).
        _write_json(self.experiment / "world_demand.json", {
            "schema_version": 2, "zones": [],
            "triggers": [
                {"kind": "ignorance", "zone": "森", "count": 20, "share": 0.5},
                {"kind": "whiff", "zone": "海", "verb": "investigate", "count": 3, "whiffs": 3, "wasted_share": 0.5},
                {"kind": "blocked", "requirement": "reach:村", "count": 20, "share": 1.0, "zones": [["道中", 20]]},
            ]})
        rendered = world_demand_view.demand_block(self.repository, self.experiment, propose_run="exp-viewer")
        self.assertIn("investigate", rendered)
        self.assertNotIn("ignorance", rendered)
        self.assertNotIn("blocked", rendered)
        self.assertNotIn("reach:村", rendered)
        self.assertIn('data-patch-action="propose" data-run="exp-viewer" data-trigger="1"', rendered)

    def test_time_estimate_needs_an_investigate_trigger(self) -> None:
        _write_json(self.experiment / "world_demand.json", {
            "schema_version": 1, "zones": [],
            "triggers": [{"zone": "村", "verb": "observe", "count": 5, "whiffs": 2, "wasted_share": 0.1}]})
        rendered = world_demand_view.demand_block(self.repository, self.experiment, propose_run="exp-viewer")
        self.assertNotIn("6〜11分", rendered)
        self.assertNotIn('data-patch-action="propose"', rendered)

    def test_without_propose_run_no_button_or_note_appears(self) -> None:
        self._write_two_triggers()
        rendered = world_demand_view.demand_block(self.repository, self.experiment)
        self.assertNotIn("data-patch-action", rendered)
        self.assertNotIn("拡張を提案させる", rendered)
        self.assertNotIn("6〜11分", rendered)

    def test_propose_run_escapes_run_name(self) -> None:
        self._write_two_triggers()
        rendered = world_demand_view.demand_block(self.repository, self.experiment, propose_run='exp"<script>')
        self.assertNotIn('exp"<script>', rendered)
        self.assertIn("exp&quot;&lt;script&gt;", rendered)

    def test_sidebar_link_absent_without_report_present_with_trigger_count(self) -> None:
        self.assertEqual(world_demand_view.sidebar_link(self.repository, "exp-viewer"), "")
        _write_json(
            self.experiment / "world_demand.json",
            {"schema_version": 1, "zones": [], "triggers": [{"zone": "海", "verb": "investigate"}]},
        )
        link = world_demand_view.sidebar_link(self.repository, "exp-viewer")
        self.assertIn("世界の需要（1件）", link)
        self.assertIn('href="/exp/exp-viewer/monitor?tab=demand"', link)


if __name__ == "__main__":
    unittest.main()
