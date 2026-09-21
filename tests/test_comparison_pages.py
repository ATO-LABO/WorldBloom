"""Comparison layout, stable identity and selection contracts on private fixtures."""
import copy
import json
import unittest
from urllib.parse import urlencode
from unittest.mock import patch
import test_workbench_pages as fixture
from test_viewer import _create_experiment
from viewer import compare_pages


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.host = fixture.WorkbenchTests("runTest")
        self.host.setUp()
        self.addCleanup(self.host.doCleanups)
        self.exp = _create_experiment(self.host.runs)
        archive = json.loads((self.exp / "archive.json").read_text(encoding="utf-8"))
        source = archive["cells"]["III|high"]
        for i, cell in enumerate(("I|low", "II|mid", "IV|low"), 1):
            elite = copy.deepcopy(source)
            elite["exemplar"]["seed"] = 7 + i
            elite["reached"] = i != 3
            elite["reach_rate"] = 0.0 if i == 3 else 1.0
            archive["cells"][cell] = elite
        (self.exp / "archive.json").write_text(json.dumps(archive), encoding="utf-8")
        self.catalog = self.host.server.repository.catalog
        self.rid = self.catalog.register_legacy("exp-viewer")
        self.snapshot = self.catalog.snapshot(self.rid)
        self.reps = self.catalog.representatives(self.snapshot)

    def url(self, cells=None, **changes):
        cells = cells or list(self.reps)[:3]
        query = {"cell": cells, "candidate": [self.reps[c] for c in cells],
                 "publication": [str(self.snapshot["revision"])]}
        query.update(changes)
        return "/exp/exp-viewer/compare?" + urlencode(query, doseq=True)

    def test_story_precedes_verdict_and_memo_without_generation(self):
        status, html, _ = self.host.get_status(self.url(["III|high", "I|low"]))
        self.assertEqual(status, 200, html)
        self.assertIn("data-comparison", html)
        self.assertIn("桃太郎は鬼を退け", html)
        self.assertIn("あらすじ未生成", html)
        for card in html.split('class="cp-card"')[1:]:
            self.assertLess(card.index('data-cp-story'), card.index('data-cp-state'))
            self.assertLess(card.index('data-cp-state'), card.index('data-cp-note'))
        self.assertEqual(self.host.fake.submitted, [])
        self.assertIn('class="cp-tabs" role="tablist"', html)
        self.assertIn("心理的な動機とは区別", html)
        self.assertIn("遅延した代償は未確認", html)

    def test_all_sizes_and_tampered_publication_are_checked(self):
        for n in (2, 3, 4):
            code, html, _ = self.host.get_status(self.url(list(self.reps)[:n]))
            self.assertEqual(code, 200, html)
            self.assertEqual(html.count('class="cp-card"'), n)
        for q in ({"candidate": list(reversed(list(self.reps.values())[:3]))},
                  {"publication": ["999"]}, {"cell": ["I|low", "I|low"]},
                  {"candidate": ["unknown"]}, {"cell": ["I|low"]}):
            code, _, _ = self.host.get_status(self.url(**q))
            self.assertEqual(code, 400, q)

    def test_two_cells_must_not_resolve_to_the_same_candidate(self):
        aliased = dict(self.reps)
        aliased["I|low"] = aliased["III|high"]
        with patch.object(self.catalog, "representatives", return_value=aliased):
            code, _, _ = self.host.get_status("/exp/exp-viewer/compare?cell=III%7Chigh&cell=I%7Clow")
        self.assertEqual(code, 400)

    def test_publication_change_during_extraction_is_rejected(self):
        original = compare_pages.data.cell_explanation
        changed = False
        def during(*args):
            nonlocal changed
            result = original(*args)
            if not changed:
                changed = True
                archive = json.loads((self.exp / "archive.json").read_text())
                archive["cells"]["I|low"]["quality"] = 0.123
                (self.exp / "archive.json").write_text(json.dumps(archive))
            return result
        with patch.object(compare_pages.data, "cell_explanation", side_effect=during):
            code, _, _ = self.host.get_status(self.url())
        self.assertEqual(code, 400)

    def test_selection_roundtrip_conflict_and_escape(self):
        endpoint = f"/api/runs/{self.rid}/selection"
        cid = self.reps["I|low"]
        note = '<script>alert("draft")</script> メモ'
        code, saved = self.host.http("POST", endpoint, {"expected_revision": 0, "changes": [
            {"candidate_id": cid, "state": "held", "note": note}]})
        self.assertEqual(code, 200, saved)
        code, html, _ = self.host.get_status(self.url())
        self.assertEqual(code, 200)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn(note, html)
        code, payload = self.host.http("POST", endpoint, {"expected_revision": 0, "changes": [
            {"candidate_id": cid, "note": "stale"}]})
        self.assertEqual(code, 409, payload)
        code, payload = self.host.http("GET", endpoint)
        self.assertEqual(next(e["note"] for e in payload["entries"] if e["candidate_id"] == cid), note)

    def test_running_and_unreached_cannot_be_adopted(self):
        code, html, _ = self.host.get_status(self.url(["I|low", "IV|low"]))
        self.assertEqual(code, 200)
        self.assertIn('value="adopted" data-cp-state disabled', html)
        self.host.fake.add(fixture._job("job-comparison", self.rid, "running"))
        code, html, _ = self.host.get_status(self.url())
        self.assertEqual(code, 200)
        self.assertIn("実行中のため判定・メモは保存できません", html)
        self.assertIn('data-cp-state disabled', html)
        endpoint = f"/api/runs/{self.rid}/selection"
        code, _ = self.host.http("POST", endpoint, {"expected_revision": 0, "changes": [
            {"candidate_id": self.reps["I|low"], "state": "adopted"}]})
        self.assertEqual(code, 409)

    def test_assets_and_no_llm_or_selection_writes_on_get(self):
        initial = self.host.server.repository.selections.get(self.rid)
        for name in ("comparison.js", "comparison.css"):
            code, _, _ = self.host.get_status("/static/" + name)
            self.assertEqual(code, 200)
        self.host.get_status(self.url())
        after = self.host.server.repository.selections.get(self.rid)
        self.assertEqual(initial, after)
        self.assertEqual(self.host.fake.submitted, [])


if __name__ == "__main__":
    unittest.main()
