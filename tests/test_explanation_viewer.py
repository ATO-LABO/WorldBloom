import copy
import json
import tempfile
import os
from unittest.mock import patch
import unittest
from pathlib import Path
from test_viewer import _create_experiment, _write_json, _write_jsonl, _fixture_rows
from viewer import data, pages


class ExplanationViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"runs"
        self.exp=_create_experiment(self.root)
        self.repo=data.RunRepository(self.root)
        archive=json.loads((self.exp/"archive.json").read_text(encoding="utf-8"))
        archive["cells"]["I|low"]=copy.deepcopy(archive["cells"]["III|high"])
        _write_json(self.exp/"archive.json",archive)

    def test_turning_tags_partial_knowledge_and_unknown_verb_escape(self):
        from gapengine.explanations import explain_rows
        from viewer.explanation_ui import panel, short
        from test_explanations import header, decision
        row = decision(1, "<script>future</script>")
        explanation = explain_rows([header(), row])
        item = explanation["representative"]
        for confirmation, status, label in (("candidate","confirmed","候補"),
                                            ("confirmed","confirmed","確認済み"),
                                            ("absent","absent","該当なし"),
                                            ("unknown","unknown","不明")):
            item["turning"].update(confirmation=confirmation,status=status)
            doc = panel(explanation, details=False)
            self.assertIn(f'転機 <span class="tag">{label}</span>', doc)
            self.assertIn("知識の一覧は記録された範囲に限ります", doc)
            self.assertIn("未対応の行動（&lt;script&gt;future&lt;/script&gt;）", doc)
            self.assertNotIn("<script>", doc)
        self.assertIn("未対応の行動", short(explanation))
        item["cost"].update(status="confirmed",complete=False)
        item["turning"]["search"] = {"candidate_sources":[{"line":3}]}
        doc = panel(explanation, details=False)
        self.assertIn("ほかの即時の代償には不明な部分",doc)
        self.assertIn("転機候補イベント L3",doc)
        from engine.decision_record import record_distribution
        from engine.actions import Action
        item["choice"]["alternatives"] = {"status":"confirmed", **record_distribution([(Action(row["verb"]),1)], [1],0)}
        doc = panel(explanation)
        self.assertIn('<td>未対応の行動（&lt;script&gt;future&lt;/script&gt;）</td>',doc)
        self.assertNotIn("<script>",doc)

    def test_compare_detail_raw_and_same_story(self):
        doc=pages.compare_page(self.repo,"exp-viewer",["III|high","I|low"])
        for label in ("選択","根拠","即時の代償","転機","同じ筋","代替候補は未記録","遅延した代償は未確認"):
            self.assertIn(label,doc)
        self.assertIn('/raw?line=',doc)
        raw=pages.raw_page(self.repo,"exp-viewer","III|high")
        self.assertIn('id="L1"',raw)
        self.assertIn('SHA-256',raw)
        context=pages.raw_page(self.repo,"exp-viewer","III|high",2)
        self.assertIn('id="L2"',context)
        self.assertIn("全文",context)
        with self.assertRaises(data.BadRequest):
            pages.raw_page(self.repo,"exp-viewer","III|high",0)
        page=pages.cell_page(self.repo,"exp-viewer","III|high",view="all")
        self.assertIn("場面ごとの四項目",page)
        grid=pages.experiment_page(self.repo,"exp-viewer")
        self.assertIn('form="compare-cells"',grid)

    def test_compare_and_source_reject_invalid_selection_and_paths(self):
        for cells in ([], ["I|low"],["I|low","I|low"],["I|low"]*5):
            doc = pages.compare_page(self.repo,"exp-viewer",cells)
            self.assertIn('role="alert"', doc)
            self.assertIn("異なる候補を2〜4件", doc)
            self.assertIn('href="/exp/exp-viewer"', doc)
        with self.assertRaises(data.ForbiddenPath):
            pages.raw_page(self.repo,"exp-viewer","../escape")

    def test_all_implemented_action_labels_are_readable(self):
        from viewer.explanation_ui import VERBS, verb_label
        expected = {"concede":"譲歩", "craft":"作成", "disguise":"変装", "donate":"寄付",
                    "guard":"守り", "negotiate":"交渉", "plant":"伏線設置", "rescue":"救助",
                    "sabotage":"妨害", "sacrifice":"犠牲", "trial":"試練", "withdraw":"撤退"}
        from engine.verbs import HANDLED_VERBS, VerbEngine
        self.assertEqual(set(VERBS), set(HANDLED_VERBS))
        self.assertTrue(all(hasattr(VerbEngine, "_" + verb) for verb in HANDLED_VERBS))
        for verb, label in expected.items():
            self.assertEqual(verb_label(verb), label)

    def test_missing_exemplar_only_affects_its_grid_cell(self):
        archive = data._read_json(self.exp / "archive.json")
        archive["cells"]["I|low"]["exemplar"]["layers_path"] = "missing/layers.jsonl"
        _write_json(self.exp / "archive.json", archive)
        doc = pages.experiment_page(self.repo, "exp-viewer")
        self.assertIn("原ログが見つからないため", doc)
        self.assertIn("III|high", doc)
        self.assertIn("代償:", doc)
        with self.assertRaises(data.MissingResource):
            data.cell_explanation(self.repo, self.exp, "I|low")
        archive["cells"]["I|low"]["exemplar"]["layers_path"] = "../escape.jsonl"
        _write_json(self.exp / "archive.json", archive)
        with self.assertRaises(data.ForbiddenPath):
            pages.experiment_page(self.repo, "exp-viewer")

    def test_extraction_cache_invalidates_and_does_not_share_mutable_results(self):
        data._cached_explanation.cache_clear()
        with patch.object(data, "extract_explanation", wraps=data.extract_explanation) as extract:
            first = data.cell_explanation(self.repo, self.exp, "III|high")
            path = Path(first["source"]["layers_path"])
            first["representative"]["cost"]["text"] = "MUTATED"
            again = data.cell_explanation(self.repo, self.exp, "III|high")
            self.assertNotEqual(again["representative"]["cost"]["text"], "MUTATED")
            self.assertEqual(extract.call_count, 1)
            # Same size with a newer mtime must invalidate.
            stamp = path.stat()
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
            data.cell_explanation(self.repo, self.exp, "III|high")
            self.assertEqual(extract.call_count, 2)
            # Size alone must also invalidate, even if mtime is restored.
            stamp = path.stat()
            path.write_bytes(path.read_bytes().replace(b"\n", b" \n", 1))
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            updated = data.cell_explanation(self.repo, self.exp, "III|high")
            self.assertEqual(extract.call_count, 3)
            self.assertNotEqual(updated["source"]["sha256"], again["source"]["sha256"])

    def test_scene_dumps_only_for_representative_or_confirmed_turning(self):
        explanation = data.cell_explanation(self.repo, self.exp, "III|high")
        doc = pages.cell_page(self.repo, "exp-viewer", "III|high", view="all")
        detail_count = sum(d["line"] == explanation["representative"]["line"]
                           or d["turning"].get("confirmation") == "confirmed"
                           for d in explanation["decisions"])
        self.assertLess(detail_count, len(explanation["decisions"]))
        # Two dumps in the representative panel, then two per detailed scene.
        self.assertEqual(doc.count('class="explanation-dump"'), 2 * (1 + detail_count))
        self.assertIn("知識の一覧は記録された範囲", doc)


    def test_npc_decisions_without_representative_render_all_views(self):
        rows = _fixture_rows()
        for row in rows:
            if row.get("kind") == "decision":
                row["subject"] = "鬼"
        _write_jsonl(self.exp / "g0/ind-0/seed-7/layers.jsonl", rows)
        explanation = data.cell_explanation(self.repo, self.exp, "III|high")
        self.assertIsNone(explanation["representative"])
        self.assertTrue(explanation["decisions"])
        for view in ("all", "digest", "decisions"):
            doc = pages.cell_page(self.repo, "exp-viewer", "III|high", view=view)
            self.assertIn("四項目は記録不足で不明", doc)
            self.assertNotIn('class="explanation-dump"', doc)
            if view == "all":
                self.assertEqual(doc.count("候補・抽選条件（本人の根拠とは別）"), len(explanation["decisions"]))

    def test_compact_panel_retains_recorded_alternatives_without_json(self):
        from engine.decision_record import record_distribution
        from engine.actions import Action
        from gapengine.explanations import explain_rows
        from test_explanations import header, decision
        from viewer.explanation_ui import panel
        row = decision(1, "guard")
        row["explanation"] = {"selection": record_distribution(
            [(Action("guard"), 1)] + [(Action("rest"), 1)] * 11, [1] * 12, 0)}
        explanation = explain_rows([header(), row])
        short, full = panel(explanation, details=False), panel(explanation)
        import re
        self.assertEqual(re.findall(r"<table>.*?</table>", short, re.S),
                         re.findall(r"<table>.*?</table>", full, re.S))
        self.assertIn("記録 8 / 全 12 件。省略: あり", short)
        self.assertIn("確率は全候補に対する値", short)
        self.assertIn("<td>守り</td>", short)
        self.assertNotIn('class="explanation-dump"', short)
        self.assertEqual(full.count('class="explanation-dump"'), 2)

    def test_nonrepresentative_confirmed_turning_keeps_json_details(self):
        from test_explanations import decision
        rows = _fixture_rows()
        head, before = rows[0], copy.deepcopy(rows[2])
        before["turn"] = 0
        before["layers"]["valued_beliefs"] = {}
        first = {"culprit": {"value": "B", "confidence": .75}}
        second = {"culprit": {"value": "C", "confidence": .75}}
        actions = [decision(1,"rethink",actor={"valued_beliefs":first},details={"before":{},"after":first}),
                   decision(2,"confront",["B","culprit"]),
                   decision(3,"rethink",actor={"valued_beliefs":second},details={"before":first,"after":second}),
                   decision(4,"confront",["C","culprit"])]
        for row in actions:
            row.update(subject=head["protagonist"], day=1, slot="morning")
        _write_jsonl(self.exp / "g0/ind-0/seed-7/layers.jsonl", [head,before,*actions])
        explanation = data.cell_explanation(self.repo,self.exp,"III|high")
        confirmed = [d for d in explanation["decisions"] if d["turning"]["confirmation"] == "confirmed"]
        self.assertEqual(len(confirmed), 2)
        self.assertTrue(any(d["line"] != explanation["representative"]["line"] for d in confirmed))
        doc = pages.cell_page(self.repo,"exp-viewer","III|high",view="all")
        self.assertEqual(doc.count('class="explanation-dump"'), 6)
        self.assertEqual(doc.count("候補・抽選条件（本人の根拠とは別）"), 5)

    def test_grid_extraction_does_not_swallow_forbidden_path(self):
        with patch.object(data, "cell_explanation", side_effect=data.ForbiddenPath("outside")) as extract:
            with self.assertRaises(data.ForbiddenPath):
                pages.experiment_page(self.repo, "exp-viewer")
        extract.assert_called_once()

    def test_comparison_labels_and_dump_css_are_connected(self):
        import re
        doc = pages.experiment_page(self.repo, "exp-viewer")
        self.assertIn('<button type="submit">四項目で比較</button>', doc)
        self.assertIn('form="compare-cells"> 四項目で比較</label>', doc)
        css = (Path(pages.__file__).parent / "static/app.css").read_text(encoding="utf-8")
        rule = re.search(r"(?m)^\.explanation-dump\s*\{([^}]+)\}", css)
        self.assertIsNotNone(rule)
        for declaration in ("white-space: pre-wrap", "overflow-wrap: anywhere"):
            self.assertIn(declaration, rule[1])


if __name__ == "__main__":
    unittest.main()
