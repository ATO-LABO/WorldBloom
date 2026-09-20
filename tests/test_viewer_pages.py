"""Unit tests for deterministic WorldBloom viewer data and pages."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import yaml

from test_viewer import (
    _create_experiment,
    _fixture_rows,
    _write_json,
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

    def test_experiment_page_without_world_demand_shows_guidance(self) -> None:
        rendered = pages.experiment_page(self.repository, "exp-viewer")
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
        rendered = pages.experiment_page(self.repository, "exp-viewer")
        self.assertIn("世界の需要", rendered)
        self.assertNotIn("この実験は世界の需要を集計していません", rendered)
        self.assertIn("investigate", rendered)
        self.assertIn("50.0%", rendered)
        self.assertIn("ゾーン別の詳細", rendered)

    def test_experiment_page_survives_a_malformed_world_demand_report(self) -> None:
        _write_json(
            self.experiment / "world_demand.json",
            {
                "schema_version": 1,
                "zones": [{"zone": "海", "verbs": [["investigate", 3], "junk", None]}, "junk"],
                "triggers": [{"zone": "海", "verb": "investigate", "wasted_share": None}, 7],
            },
        )
        rendered = pages.experiment_page(self.repository, "exp-viewer")
        self.assertIn("世界の需要", rendered)
        self.assertIn("investigate×3", rendered)

    # -- WB-WORLDGROW-001 stage 3a: expansion patches shown on the experiment page --

    def test_world_expansion_info_reads_config_json_project_layout(self) -> None:
        _write_json(self.experiment / "config.json", {"project_id": "momotaro"})
        world_dir = self.experiment / "inputs" / "projects" / "momotaro"
        world_dir.mkdir(parents=True)
        (world_dir / "world.yaml").write_text(
            yaml.safe_dump({"name": "桃太郎", "expansion": {
                "base": "桃太郎", "patches": [{"id": "p-x", "title": "t"}]}}, allow_unicode=True),
            encoding="utf-8")
        patches = data.world_expansion_info(self.repository, self.experiment)
        self.assertEqual(patches, [{"id": "p-x", "title": "t"}])

    def test_world_expansion_info_reads_expanded_project_cli_layout(self) -> None:
        (self.experiment / "expanded-project").mkdir()
        (self.experiment / "expanded-project" / "world.yaml").write_text(
            yaml.safe_dump({"name": "桃太郎", "expansion": {
                "base": "桃太郎", "patches": [{"id": "p-y", "title": "u"}]}}, allow_unicode=True),
            encoding="utf-8")
        patches = data.world_expansion_info(self.repository, self.experiment)
        self.assertEqual(patches, [{"id": "p-y", "title": "u"}])

    def test_world_expansion_info_missing_returns_empty(self) -> None:
        self.assertEqual(data.world_expansion_info(self.repository, self.experiment), [])

    def test_world_expansion_info_survives_malformed_world_yaml(self) -> None:
        (self.experiment / "expanded-project").mkdir()
        (self.experiment / "expanded-project" / "world.yaml").write_text(
            "not: [valid, yaml", encoding="utf-8")
        self.assertEqual(data.world_expansion_info(self.repository, self.experiment), [])

    def test_experiment_page_shows_base_world_line_without_patches(self) -> None:
        rendered = pages.experiment_page(self.repository, "exp-viewer")
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
        rendered = pages.experiment_page(self.repository, "exp-viewer")
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
        rendered = pages.experiment_page(self.repository, "exp-viewer")
        self.assertIn("この実験の世界: ベース（拡張なし）", rendered)

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
        # The home dashboard no longer lists individual runs (WB-UI-016):
        # it shows the world with a run count, and the run itself only
        # appears inside that world's own "この世界の実験" block.
        block, _count = pages.world_runs_block(self.repository, "桃太郎", None)
        self.assertIn("その他の短いラン", block)
        self.assertIn("exp-viewer", block)
        self.assertNotIn("run-delete", block)  # no delete button without --control

        rendered = pages.index_page(self.repository)
        self.assertIn("桃太郎", rendered)
        self.assertIn('class="world-card"', rendered)
        self.assertNotIn("exp-viewer", rendered)
        # Home already shows the full world/genre hub in the body, so the
        # header's world/run picker (useful elsewhere) would be redundant here.
        self.assertNotIn('data-wb="world-picker"', rendered)
        self.assertNotIn('<nav class="phase-band"', rendered)  # Home never shows the phase tabs

    def test_index_world_card_has_quick_start(self) -> None:
        # WB-UI-022's one-click "この世界で新しい実験を回す" button lives in
        # each home card's footer (merged from the card-grid redesign).
        rendered = pages.index_page(self.repository)
        card_start = rendered.index('class="world-card"')
        card_end = rendered.index("</article>", card_start)
        self.assertIn("data-quick-start", rendered[card_start:card_end])

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

    def test_quick_start_actions_needs_both_world_and_genre(self):
        # WB-UI-022: no genre (or no world) -> a single plain link to the full
        # form, no data-quick-start hook and no second "設定を変更して実行" link.
        for world_id, genre in (("momotaro", ""), ("", "romance")):
            html = pages.quick_start_actions(world_id, genre, "桃太郎", "/configs/new?project=momotaro")
            self.assertNotIn("data-quick-start", html)
            self.assertEqual(html.count("<a "), 1)

    def test_quick_start_actions_escapes_and_wires_data_attrs(self):
        html = pages.quick_start_actions(
            "momo\"taro", "roman<ce", "桃太郎 & 一味", "/configs/new?project=momotaro&template=romance",
        )
        self.assertIn("data-quick-start", html)
        self.assertIn('data-project="momo&quot;taro"', html)
        self.assertIn('data-template="roman&lt;ce"', html)
        self.assertIn('data-world-name="桃太郎 &amp; 一味"', html)
        self.assertNotIn('"roman<ce"', html)
        self.assertEqual(html.count("<a "), 2)


if __name__ == "__main__":
    unittest.main()
