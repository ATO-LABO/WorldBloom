import copy
import json
import tempfile
import unittest
from pathlib import Path
from test_viewer import _create_experiment, _write_json
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
        for cells in (["I|low"],["I|low","I|low"],["I|low"]*5):
            with self.assertRaises(data.BadRequest):
                pages.compare_page(self.repo,"exp-viewer",cells)
        with self.assertRaises(data.ForbiddenPath):
            pages.raw_page(self.repo,"exp-viewer","../escape")

if __name__ == "__main__":
    unittest.main()
