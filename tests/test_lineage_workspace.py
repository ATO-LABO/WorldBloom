"""Display contracts independent of the real-run lineage extraction tests."""
import re
import unittest
from html.parser import HTMLParser
from viewer import lineage_pages as ui, server


def model(count=3):
    shifts = [{"key": "novelty_drive", "label": "前例を避ける度合い",
               "before": .2, "after": .4, "delta": .2}]
    nodes = [{"ref": f"g{i}/ind-0", "generation": i, "index": 0,
              "outcome": {"reached": True, "allies_final": 2}, "rerun_error": None,
              "gene_shift": shifts if i else None} for i in range(count)]
    t = {"parent_ref": nodes[0]["ref"], "child_ref": nodes[1]["ref"],
         "parent_index": 0, "child_index": 1, "parent_generation": 0, "child_generation": 1,
         "parent_turn": 3, "turn": 4, "present": ["桃太郎", "犬"],
         "parent_action": {"verb": "wait", "args": []},
         "child_action": {"verb": "move", "args": ["村"]},
         "parent_candidates": [
             {"verb": "wait", "args": [], "probability": .6, "selected": True},
             {"verb": "move", "args": ["村"], "probability": .4}],
         "child_candidates": [
             {"verb": "move", "args": ["村"], "probability": .8, "selected": True},
             {"verb": "wait", "args": [], "probability": .2}],
         "parent_candidate_stats": {"total_candidates": 2, "recorded_candidates": 2, "truncated": False},
         "child_candidate_stats": {"total_candidates": 2, "recorded_candidates": 2, "truncated": False},
         "gene_shift": shifts,
         "trait_series": {"label": shifts[0]["label"], "values": [.2] + [.4]*(count-1)},
         "outcome": nodes[1]["outcome"]}
    return {"ancestry": nodes, "turnings": [t], "first_reach_index": 0,
            "elite_ref": nodes[-1]["ref"], "seed": 11}


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items = []
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


class LineageWorkspaceTests(unittest.TestCase):
    def render(self, data=None, **opts):
        return ui.render_model(data or model(), "exp1", "II|high", **opts)

    def test_parent_child_actions_are_aligned_even_in_different_record_order(self):
        html = self.render()
        tables = re.findall(r'<table class="candidate-table">(.*?)</table>', html)
        labels = [re.findall(r'<th scope="row">(.*?)(?: <span|</th>)', t) for t in tables]
        self.assertEqual(labels[0], labels[1])
        self.assertIn("60%", tables[0])
        self.assertIn("80%", tables[1])
        self.assertEqual(html.count('aria-label="実際の選択"'), 2)

    def test_missing_and_invalid_probabilities_are_never_presented_as_zero(self):
        data = model()
        t = data["turnings"][0]
        t["child_candidates"] = [{"verb": "move", "args": ["村"], "probability": float("nan")}]
        t["child_candidate_stats"] = None
        html = self.render(data)
        child = re.findall(r'<table class="candidate-table">(.*?)</table>', html)[1]
        self.assertIn("未記録", child)
        self.assertIn("不明", child)
        self.assertNotIn("0%", child)
        self.assertNotIn("nan", child)
        t["child_candidate_stats"] = {"truncated": False}
        self.assertIn("未記録", self.render(data))

    def test_complete_record_distinguishes_absent_action(self):
        data = model()
        t = data["turnings"][0]
        t["child_candidates"] = t["child_candidates"][:1]
        t["child_candidate_stats"] = {"total_candidates": 1, "recorded_candidates": 1, "truncated": False}
        self.assertIn("候補なし", self.render(data))

    def test_actual_low_probability_choice_is_not_hidden_by_top_three(self):
        data = model()
        t = data["turnings"][0]
        t["parent_candidates"] = [
            {"verb": f"action-{i}", "args": [], "probability": .3 if i<3 else .01,
             "selected": i==5} for i in range(6)]
        html = self.render(data)
        brief = html.split('<details class="lw-more">')[0]
        self.assertIn("action-5", brief)
        self.assertNotIn("action-4", brief)
        self.assertIn("action-4", html)

    def test_coincident_milestones_keep_distinct_heading_and_single_selection(self):
        for point, title in (("origin", "出発点"), ("reach", "初到達"), ("current", "現在の候補")):
            html = self.render(point=point)
            self.assertIn(f"<h2>{title}</h2>", html)
            self.assertEqual(html.count('aria-current="page"'), 1)

    def test_timeline_endpoints_use_named_milestones(self):
        self.assertIn("<h2>現在の候補</h2>", self.render(point="node-2"))
        self.assertIn("<h2>出発点</h2>", self.render(point="node-0"))

    def test_query_selection_preserves_context_and_invalid_input_falls_back(self):
        html = self.render(point="origin", tab="records", view="all")
        self.assertIn('id="lw-tab-records" role="tab" aria-controls="lw-panel-records" aria-selected="true"', html)
        self.assertIn("tab=records&amp;view=all&amp;elite=g2%2Find-0", html)
        html = self.render(point="<script>", tab="invalid", view="invalid", turning_index=99)
        self.assertIn("転機 1", html)
        self.assertIn('id="lw-tab-choices" role="tab" aria-controls="lw-panel-choices" aria-selected="true"', html)

    def test_large_lineage_is_bounded_but_all_nodes_remain_reachable(self):
        for mode in ("fit", "all"):
            html = self.render(model(2000), view=mode, point="node-1234")
            nodes = [a for tag,a in Tags(html).items if "data-lw-node" in a]
            self.assertEqual(len(nodes), 2000)
            visible = [n for n in nodes if "hidden" not in n]
            if mode == "fit":
                self.assertLessEqual(len(visible), 9)
                self.assertIn("1234", [n["data-lw-node"] for n in visible])
            else:
                self.assertEqual(len(visible), 2000)
            self.assertIn("point=node-1233", html)
            self.assertIn("point=node-1235", html)

    def test_elite_change_fails_closed(self):
        html = self.render(expected_ref="g99/ind-0")
        self.assertIn("代表候補が更新されています", html)
        self.assertNotIn('class="lineage-band"', html)
        self.assertNotIn('class="turning-columns"', html)

    def test_replay_failure_does_not_claim_no_divergence_or_complete_first_reach(self):
        data = model()
        data["ancestry"][0]["rerun_error"] = "<script>alert(1)</script>"
        data["ancestry"][0]["outcome"] = None
        data["turnings"] = []
        html = self.render(data, point="origin")
        self.assertIn("この区間の転機は不明", html)
        self.assertIn("初到達も確認できた範囲", html)
        self.assertIn("再現できなかった区間を含みます", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert", html)
        self.assertIn("再現できず不明", html)

    def test_action_and_reference_text_is_escaped(self):
        data = model()
        data["turnings"][0]["child_action"]["args"] = ['<img src=x onerror=alert(1)>']
        data["ancestry"][1]["ref"] = "<unsafe>"
        html = self.render(data, tab="records")
        self.assertIn("&lt;img", html)
        self.assertIn("&lt;unsafe&gt;", html)
        self.assertNotIn("<img src=x", html)

    def test_legacy_and_frozen_input_descriptions_are_distinct(self):
        self.assertIn("現在の世界・ジャンル設定を参照する旧形式", self.render(legacy=True))
        self.assertIn("実行に保存された世界・ジャンル設定", self.render(legacy=False))
        self.assertIn("実行当時と完全に一致することを保証する表示ではありません", self.render())

    def test_assets_are_explicitly_allowed(self):
        for name in ("lineage-workspace.css", "lineage-workspace.js"):
            self.assertTrue(server.static_path(name).is_file())

if __name__ == "__main__":
    unittest.main()
