"""Unit tests for viewer/world_effect_view.py (WB-WORLDGROW-001 段階4a:
拡張あり／なしの比較)。対は保存しない設計なので、find_partners() の導出ロジック
と effect_html() の描画を実験ディレクトリのフィクスチャで検証する。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs

from test_viewer import _write_json, _write_jsonl
from viewer import data, world_effect_view

_SHA = "a" * 64
_OTHER_SHA = "b" * 64


def _write_experiment(runs_root: Path, name: str, *, project_id="momotaro", template_id="momotaro",
                       source_sha256=_SHA, patch_ids=(), evolution_extra=None, cells=("I|low",),
                       demand_triggers=None, reach_rate=0.5, occupied_cells=3,
                       protagonist=None, world_yaml=None, cell_exemplars=None,
                       expansion_added=None) -> Path:
    experiment = runs_root / name
    cell_entries = {}
    for cell in cells:
        entry = {"quality": 0.5}
        if cell_exemplars and cell in cell_exemplars:
            entry["exemplar"] = {"layers_path": cell_exemplars[cell]}
        cell_entries[cell] = entry
    _write_json(experiment / "archive.json", {"cells": cell_entries})
    _write_json(experiment / "summary.json", {"generations": [
        {"generation": 0, "reach_rate": reach_rate, "occupied_cells": occupied_cells},
    ]})
    evolution = {"seed_base": 1, "generations": 5, "population": 10, "seeds": 3,
                 "world_expansion": "expand" if patch_ids else "detect"}
    if evolution_extra:
        evolution.update(evolution_extra)
    config = {"config_id": f"cfg-{name}", "project_id": project_id, "template_id": template_id,
              "evolution": evolution}
    preview = {}
    if protagonist:
        preview["protagonist"] = protagonist
    if world_yaml is not None:
        preview["world"] = world_yaml
    if preview:
        config["preview"] = preview
    _write_json(experiment / "config.json", config)
    world_entry = {"path": f"projects/{project_id}/world.yaml", "source_sha256": source_sha256,
                   "sha256": "frozen-" + source_sha256}
    if patch_ids:
        world_entry["world_patches"] = [{"id": pid, "sha256": "x"} for pid in patch_ids]
    _write_json(experiment / "input-manifest.json", {"schema_version": 1, "files": [world_entry]})
    if demand_triggers is not None:
        _write_json(experiment / "world_demand.json", {
            "schema_version": 1, "triggers": demand_triggers, "zones": [],
        })
    if expansion_added is not None:
        # data.world_expansion_state() の summary.json フォールバック経路
        # （段階4b: この一覧を使って usage_counts の対象集合を作る）。
        summary_path = experiment / "summary.json"
        summary = data._read_json(summary_path)
        summary["world_patches"] = list(patch_ids) or ["p-1"]
        summary["world_expansion_patches"] = [{"id": "p-1", "title": "t", "added": expansion_added}]
        _write_json(summary_path, summary)
    return experiment


class FindPartnersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "runs"
        self.runs_root.mkdir(parents=True)
        self.repository = data.RunRepository(self.runs_root)

    def test_base_and_expand_runs_of_the_same_world_pair_up(self) -> None:
        _write_experiment(self.runs_root, "base-run", demand_triggers=[
            {"zone": "海", "verb": "investigate", "count": 10, "whiffs": 10},
        ])
        _write_experiment(self.runs_root, "expand-run", patch_ids=("p-1",))

        base_role, base_config, base_partners = world_effect_view.find_partners(self.repository, "base-run")
        self.assertEqual(base_role, "base")
        self.assertEqual([p["run_name"] for p in base_partners], ["expand-run"])
        self.assertEqual(base_partners[0]["warnings"], [])

        expand_role, expand_config, expand_partners = world_effect_view.find_partners(self.repository, "expand-run")
        self.assertEqual(expand_role, "expand")
        self.assertEqual([p["run_name"] for p in expand_partners], ["base-run"])

    def test_different_base_world_does_not_pair(self) -> None:
        _write_experiment(self.runs_root, "base-run", source_sha256=_SHA)
        _write_experiment(self.runs_root, "expand-run", source_sha256=_OTHER_SHA, patch_ids=("p-1",))
        _role, _config, partners = world_effect_view.find_partners(self.repository, "base-run")
        self.assertEqual(partners, [])

    def test_two_base_runs_do_not_pair_with_each_other(self) -> None:
        _write_experiment(self.runs_root, "base-a")
        _write_experiment(self.runs_root, "base-b")
        _role, _config, partners = world_effect_view.find_partners(self.repository, "base-a")
        self.assertEqual(partners, [])

    def test_mismatched_evolution_condition_is_flagged_not_excluded(self) -> None:
        _write_experiment(self.runs_root, "base-run", demand_triggers=[])
        _write_experiment(self.runs_root, "expand-run", patch_ids=("p-1",),
                           evolution_extra={"generations": 50})
        _role, _config, partners = world_effect_view.find_partners(self.repository, "base-run")
        self.assertEqual(len(partners), 1)
        self.assertIn("探索条件（世代数・個体数・seedなど）が異なります", partners[0]["warnings"])

    def test_base_without_world_demand_is_flagged(self) -> None:
        # demand_triggers=None -> world_demand.json は書かない (off で回した実験)
        _write_experiment(self.runs_root, "base-off")
        _write_experiment(self.runs_root, "expand-run", patch_ids=("p-1",))
        role, _config, partners = world_effect_view.find_partners(self.repository, "expand-run")
        self.assertEqual(role, "expand")
        self.assertIn("ベース側が「検知のみ」で回っていないため、きっかけの空振り率は比較できません",
                       partners[0]["warnings"])

    def test_legacy_run_without_config_cannot_be_compared(self) -> None:
        experiment = self.runs_root / "legacy-run"
        _write_json(experiment / "archive.json", {"cells": {}})
        role, config, partners = world_effect_view.find_partners(self.repository, "legacy-run")
        self.assertIsNone(role)
        self.assertIsNone(config)
        self.assertEqual(partners, [])


class EffectHtmlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "runs"
        self.runs_root.mkdir(parents=True)
        self.repository = data.RunRepository(self.runs_root)

    def _handler(self):
        class _Handler:
            pass
        handler = _Handler()
        handler.repository = self.repository
        handler.path = "/exp/expand-run/monitor?tab=effect"
        return handler

    def test_no_config_shows_guidance(self) -> None:
        experiment = self.runs_root / "legacy-run"
        _write_json(experiment / "archive.json", {"cells": {}})
        html = world_effect_view.effect_html(self._handler(), {"run_name": "legacy-run"}, {})
        self.assertIn("比較できません", html)

    def test_no_partner_links_to_new_run(self) -> None:
        _write_experiment(self.runs_root, "base-run")
        html = world_effect_view.effect_html(self._handler(), {"run_name": "base-run"}, {})
        self.assertIn("対になる実験がまだありません", html)
        self.assertIn("/configs/new?project=momotaro", html)

    def test_pair_renders_whiff_and_trend_comparison(self) -> None:
        _write_experiment(self.runs_root, "base-run", demand_triggers=[
            {"zone": "海", "verb": "investigate", "count": 100, "whiffs": 100},
        ], reach_rate=0.2, occupied_cells=2, cells=("I|low",))
        _write_experiment(self.runs_root, "expand-run", patch_ids=("p-1",), demand_triggers=[
            {"zone": "海", "verb": "investigate", "count": 100, "whiffs": 30},
        ], reach_rate=0.4, occupied_cells=3, cells=("I|low", "II|mid"))

        html = world_effect_view.effect_html(self._handler(), {"run_name": "expand-run"}, {})
        self.assertIn("拡張の効果", html)
        self.assertIn("100回中100回（100.0%）", html)  # ベース
        self.assertIn("100回中30回（30.0%）", html)   # 拡張後
        self.assertIn("拡張後だけに出た型: 1種類", html)
        self.assertIn("/exp/expand-run/cell/II%7Cmid", html)
        # 段階4設計メモ G7: 品質は比較しない（占有の有無だけ）。
        self.assertNotIn("quality", html)

    def test_with_query_selects_a_specific_partner(self) -> None:
        _write_experiment(self.runs_root, "base-a")
        _write_experiment(self.runs_root, "base-b")
        _write_experiment(self.runs_root, "expand-run", patch_ids=("p-1",))
        query = parse_qs("with=base-b")
        html = world_effect_view.effect_html(self._handler(), {"run_name": "expand-run"}, query)
        self.assertIn("<strong>base-b</strong>", html)

    def test_stage4b_badge_shown_for_a_new_cell_that_used_the_expansion(self) -> None:
        _write_jsonl(self.runs_root / "expand-run" / "g0/ind-0/seed-7/layers.jsonl", [
            {"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
             "delta": {"actor": {"zone": "船大工の小屋"}}},
        ])
        _write_experiment(self.runs_root, "base-run", cells=("I|low",))
        _write_experiment(self.runs_root, "expand-run", patch_ids=("p-1",), cells=("I|low", "II|mid"),
                           cell_exemplars={"II|mid": "g0/ind-0/seed-7/layers.jsonl"},
                           protagonist="桃太郎", expansion_added={"zones": ["船大工の小屋"]})
        html = world_effect_view.effect_html(self._handler(), {"run_name": "expand-run"}, {})
        self.assertIn("拡張要素: 使った", html)
        self.assertIn("新しい場所への移動 1回", html)

    def test_stage4b_badge_absent_when_new_cell_did_not_use_the_expansion(self) -> None:
        _write_jsonl(self.runs_root / "expand-run" / "g0/ind-0/seed-7/layers.jsonl", [
            {"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
             "delta": {"actor": {"zone": "村"}}},
        ])
        _write_experiment(self.runs_root, "base-run", cells=("I|low",))
        _write_experiment(self.runs_root, "expand-run", patch_ids=("p-1",), cells=("I|low", "II|mid"),
                           cell_exemplars={"II|mid": "g0/ind-0/seed-7/layers.jsonl"},
                           protagonist="桃太郎", expansion_added={"zones": ["船大工の小屋"]})
        html = world_effect_view.effect_html(self._handler(), {"run_name": "expand-run"}, {})
        self.assertIn("拡張要素: 使っていない", html)

    def test_stage4c_frozen_world_map_shown_when_preview_world_present(self) -> None:
        _write_experiment(self.runs_root, "base-run", cells=("I|low",))
        _write_experiment(self.runs_root, "expand-run", patch_ids=("p-1",),
                           world_yaml={"zones": [{"name": "海"}, {"name": "船大工の小屋"}], "routes": {}})
        html = world_effect_view.effect_html(self._handler(), {"run_name": "expand-run"}, {})
        self.assertIn("回った世界を地図で見る", html)
        self.assertIn("船大工の小屋", html)

    def test_stage4c_frozen_world_map_absent_without_preview_world(self) -> None:
        _write_experiment(self.runs_root, "base-run", cells=("I|low",))
        _write_experiment(self.runs_root, "expand-run", patch_ids=("p-1",))
        html = world_effect_view.effect_html(self._handler(), {"run_name": "expand-run"}, {})
        self.assertNotIn("回った世界を地図で見る", html)

    def test_multiple_partners_render_as_clickable_links_not_a_dead_select(self) -> None:
        # Opus review M1: <select data-effect-partner> だった旧実装は
        # run-workspace.js のどのハンドラも change を拾わず無反応だった。
        _write_experiment(self.runs_root, "base-a")
        _write_experiment(self.runs_root, "base-b")
        _write_experiment(self.runs_root, "expand-run", patch_ids=("p-1",))
        html = world_effect_view.effect_html(self._handler(), {"run_name": "expand-run"}, {})
        self.assertNotIn("<select", html)
        self.assertIn('<a href="/exp/expand-run/monitor?tab=effect&with=base-a">base-a</a>', html)
        self.assertIn('<a href="/exp/expand-run/monitor?tab=effect&with=base-b">base-b</a>', html)


if __name__ == "__main__":
    unittest.main()
