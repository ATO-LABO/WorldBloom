"""Unit tests for viewer/world_usage_badge.py (WB-WORLDGROW-001 段階4b:
拡張要素を使った物語のバッジ)。usage_counts() は new_usage() を薄くラップした
だけなので、既存の tests/test_world_patch_r2.py の行フィクスチャと同じ形を使う。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_viewer import _create_experiment, _write_json, _write_jsonl
from viewer import data, world_usage_badge

_ZERO_COUNTS = dict.fromkeys(
    ("decisions_in_new_zones", "moves_into_new_zones", "gathered_new_items",
     "learned_new_facts", "shared_new_facts", "gave_new_items"), 0)


class UsageCountsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "runs"
        self.experiment = _create_experiment(self.runs_root)
        self.repository = data.RunRepository(self.runs_root)

    def _write_log(self, rows) -> str:
        _write_jsonl(self.experiment / "layers.jsonl", rows)
        return "layers.jsonl"

    def test_counts_match_new_usage_reference_fixture(self) -> None:
        # 同じ行フィクスチャを tests/test_world_patch_r2.py と共有(意図的な重複)。
        rows = [
            {"kind": "snapshot", "subject": "桃太郎", "layers": {"zone": "海"}},
            {"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
             "delta": {"actor": {"zone": "船大工の小屋"}}},
            {"kind": "decision", "subject": "桃太郎", "verb": "investigate", "result": "investigated",
             "details": {"learned": ["噂A"], "gathered": [{"item": "古びた帆布"}]}},
            {"kind": "event", "subject": "桃太郎", "verb": "learn_fact", "details": {"fact": "噂A"}},
            {"kind": "decision", "subject": "桃太郎", "verb": "give_item", "result": "invalid",
             "args": ["犬", "古びた帆布"]},
            {"kind": "decision", "subject": "桃太郎", "verb": "give_item", "result": "given",
             "args": ["犬", "古びた帆布"]},
        ]
        rel = self._write_log(rows)
        patches = [{"added": {"zones": ["船大工の小屋"], "items": ["古びた帆布"], "facts": ["噂A"]}}]
        counts = world_usage_badge.usage_counts(self.repository, self.experiment, rel, "桃太郎", patches)
        self.assertEqual(counts, {"decisions_in_new_zones": 3, "moves_into_new_zones": 1,
            "gathered_new_items": 1, "learned_new_facts": 1, "shared_new_facts": 0, "gave_new_items": 1})

    def test_patches_split_across_entries_are_combined(self) -> None:
        rows = [{"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
                  "delta": {"actor": {"zone": "枝"}}}]
        rel = self._write_log(rows)
        # 段階3の複数パッチが同時適用されている想定: ゾーンとアイテムが別パッチ由来。
        patches = [{"added": {"zones": ["枝"]}}, {"added": {"items": ["未使用の品"]}}]
        counts = world_usage_badge.usage_counts(self.repository, self.experiment, rel, "桃太郎", patches)
        self.assertEqual(counts["moves_into_new_zones"], 1)

    def test_no_added_elements_is_not_scored(self) -> None:
        rel = self._write_log([{"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved"}])
        self.assertIsNone(world_usage_badge.usage_counts(self.repository, self.experiment, rel, "桃太郎", []))
        self.assertIsNone(world_usage_badge.usage_counts(self.repository, self.experiment, rel, "桃太郎", None))
        self.assertIsNone(world_usage_badge.usage_counts(
            self.repository, self.experiment, rel, "桃太郎", [{"added": {}}]))

    def test_missing_protagonist_or_path_is_none(self) -> None:
        patches = [{"added": {"zones": ["X"]}}]
        self.assertIsNone(world_usage_badge.usage_counts(self.repository, self.experiment, "layers.jsonl", "", patches))
        self.assertIsNone(world_usage_badge.usage_counts(self.repository, self.experiment, None, "桃太郎", patches))

    def test_missing_log_file_is_none_not_an_error(self) -> None:
        patches = [{"added": {"zones": ["X"]}}]
        self.assertIsNone(world_usage_badge.usage_counts(self.repository, self.experiment, "nope.jsonl", "桃太郎", patches))

    def test_malformed_patch_entries_are_skipped(self) -> None:
        rel = self._write_log([{"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
                                 "delta": {"actor": {"zone": "枝"}}}])
        patches = ["not-a-dict", {"added": "not-a-dict-either"}, {"added": {"zones": ["枝"]}}]
        counts = world_usage_badge.usage_counts(self.repository, self.experiment, rel, "桃太郎", patches)
        self.assertEqual(counts["moves_into_new_zones"], 1)

    def test_same_log_different_patch_sets_are_not_confused_by_the_cache(self) -> None:
        # Opus review 推奨7: lru_cache のキーにパッチ集合(zones/items/facts)が
        # 含まれていることを、同じログ・違うパッチ集合で確認する。
        rel = self._write_log([{"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
                                 "delta": {"actor": {"zone": "枝"}}}])
        matching = world_usage_badge.usage_counts(
            self.repository, self.experiment, rel, "桃太郎", [{"added": {"zones": ["枝"]}}])
        unrelated = world_usage_badge.usage_counts(
            self.repository, self.experiment, rel, "桃太郎", [{"added": {"zones": ["別の場所"]}}])
        self.assertEqual(matching["moves_into_new_zones"], 1)
        self.assertEqual(unrelated["moves_into_new_zones"], 0)

    def test_returned_counts_are_a_copy_not_the_cached_object(self) -> None:
        # Opus review 推奨4: 呼び出し側が戻り値を書き換えても、次の呼び出しへ
        # 波及しない（lru_cache 内の共有オブジェクトを直接返していない）。
        rel = self._write_log([{"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
                                 "delta": {"actor": {"zone": "枝"}}}])
        patches = [{"added": {"zones": ["枝"]}}]
        first = world_usage_badge.usage_counts(self.repository, self.experiment, rel, "桃太郎", patches)
        first["moves_into_new_zones"] = 999
        second = world_usage_badge.usage_counts(self.repository, self.experiment, rel, "桃太郎", patches)
        self.assertEqual(second["moves_into_new_zones"], 1)


class BadgeHtmlTests(unittest.TestCase):
    def test_none_counts_render_empty(self) -> None:
        self.assertEqual(world_usage_badge.badge_html(None), "")

    def test_all_zero_counts_render_not_used(self) -> None:
        html = world_usage_badge.badge_html(_ZERO_COUNTS)
        self.assertIn("使っていない", html)
        self.assertNotIn("<details", html)

    def test_used_counts_render_details_with_escaped_text(self) -> None:
        counts = dict(_ZERO_COUNTS, decisions_in_new_zones=2)
        html = world_usage_badge.badge_html(counts)
        self.assertIn("<details", html)
        self.assertIn("使った", html)
        self.assertIn("新しい場所での決定 2回", html)
        # ゼロの項目は詳細に出ない
        self.assertNotIn("新しい場所への移動", html)


class CellBadgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "runs"
        self.experiment = _create_experiment(self.runs_root)
        self.repository = data.RunRepository(self.runs_root)

    def test_cell_without_exemplar_is_empty(self) -> None:
        self.assertEqual(world_usage_badge.cell_badge_html(self.repository, self.experiment, [], {}, "桃太郎"), "")
        self.assertEqual(world_usage_badge.cell_badge_html(self.repository, self.experiment, [], None, "桃太郎"), "")

    def test_cell_with_exemplar_delegates_to_usage_counts(self) -> None:
        _write_jsonl(self.experiment / "g0/ind-0/seed-7/layers.jsonl", [
            {"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
             "delta": {"actor": {"zone": "枝"}}},
        ])
        patches = [{"added": {"zones": ["枝"]}}]
        elite = {"exemplar": {"layers_path": "g0/ind-0/seed-7/layers.jsonl"}}
        html = world_usage_badge.cell_badge_html(self.repository, self.experiment, patches, elite, "桃太郎")
        self.assertIn("使った", html)
        self.assertIn("<details", html)  # cell_badge_html は非buttonの文脈=展開版


class BadgeForRunCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "runs"
        self.control_root = Path(self.temporary.name) / "control"
        self.experiment = _create_experiment(self.runs_root)
        _write_jsonl(self.experiment / "g0/ind-0/seed-7/layers.jsonl", [
            {"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
             "delta": {"actor": {"zone": "船大工の小屋"}}},
            {"kind": "decision", "subject": "桃太郎", "verb": "investigate", "result": "investigated",
             "details": {"gathered": [{"item": "古びた帆布"}]}},
        ])
        _write_json(self.experiment / "config.json", {"project_id": "nowhere", "preview": {"protagonist": "桃太郎"}})
        _write_json(self.experiment / "summary.json", {
            "world_patches": ["p-a"],
            "world_expansion_patches": [{"id": "p-a", "title": "t",
                "added": {"zones": ["船大工の小屋"], "items": ["古びた帆布"]}}],
        })
        self.repository = data.RunRepository(self.runs_root, control_root=self.control_root)
        self.rid = self.repository.catalog.register_legacy(self.experiment.name)
        snapshot = self.repository.catalog.snapshot(self.rid, observe=False)
        self.candidate_id = snapshot["candidates"]["representatives"]["III|high"]

    def test_renders_badge_for_expanded_experiment(self) -> None:
        html = world_usage_badge.badge_for_run_candidate(self.repository, self.rid, self.candidate_id)
        self.assertIn("使った", html)
        self.assertIn("<details", html)  # <dl>の中=展開版でよい

    def test_no_catalog_is_empty(self) -> None:
        readonly = data.RunRepository(self.runs_root)  # control_root なし = catalog None
        self.assertEqual(world_usage_badge.badge_for_run_candidate(readonly, self.rid, self.candidate_id), "")

    def test_unknown_candidate_is_empty(self) -> None:
        self.assertEqual(world_usage_badge.badge_for_run_candidate(self.repository, self.rid, "cand-nope"), "")

    def test_unknown_run_is_empty(self) -> None:
        self.assertEqual(world_usage_badge.badge_for_run_candidate(self.repository, "legacy-nope", self.candidate_id), "")

    def test_base_experiment_is_empty(self) -> None:
        _write_json(self.experiment / "summary.json", {})
        self.assertEqual(world_usage_badge.badge_for_run_candidate(self.repository, self.rid, self.candidate_id), "")


if __name__ == "__main__":
    unittest.main()
