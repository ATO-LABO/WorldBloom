"""Observer reads committed records; browsing never starts another execution."""
import json
import unittest
from unittest.mock import patch

import test_ga_replay as fixture
from viewer import ga_replay


class RunWorkspaceTests(unittest.TestCase):
    setUp = fixture.ReplayHttpTests.setUp
    get = fixture.ReplayHttpTests.get

    def read(self, query=""):
        status, text = self.get("/jobs/job-replay?view-data=1" + query)
        self.assertEqual(status, 200, text)
        return json.loads(text)

    def test_selected_snapshot_does_not_include_future_cells_or_nodes(self):
        result = self.read("&gen=0")["observation"]
        self.assertEqual((result["generation"], result["latest"]), (0, 1))
        self.assertEqual(set(result["cells"]), {"I|low"})
        self.assertEqual(result["cells"]["I|low"]["quality"], .5)
        self.assertTrue(result["river"]["nodes"])
        self.assertTrue(all(n["generation"] == 0 for n in result["river"]["nodes"]))
        self.assertEqual(result["river"]["edges"], [])

    def test_latest_snapshot_has_both_generations(self):
        result = self.read()["observation"]
        self.assertEqual(result["generation"], 1)
        self.assertEqual(set(result["cells"]), {"I|low", "II|mid"})
        self.assertEqual({n["generation"] for n in result["river"]["nodes"]}, {0, 1})
        self.assertEqual(result["candidate_count"], 0)
        self.assertTrue(result["river"]["edges"])

    def test_unpublished_files_are_not_presented_as_committed_results(self):
        self.fake._jobs["job-replay"].update(state="running", publication_revision=None)
        result = self.read()["observation"]
        self.assertIsNone(result["generation"])
        self.assertIsNone(result["replay"])
        self.assertIsNone(result["river"])
        self.assertFalse(result["cells"])
        self.assertIn("最初の世代", result["notice"])

    def test_missing_or_modified_snapshot_is_not_substituted_with_live_archive(self):
        target = self.runs / self.run_id / "published/1/archive.json"
        target.write_text('{"cells": {}}', encoding="utf-8")
        result = self.read("&gen=0")["observation"]
        self.assertIsNone(result["replay"])
        self.assertFalse(result["cells"])
        self.assertIn("読み込めません", result["notice"])

    def test_candidate_count_uses_candidate_entries_not_envelope_fields(self):
        original = ga_replay._snapshot
        def snapshot(*args, **kwargs):
            value = original(*args, **kwargs)
            value["candidates"]["candidates"] = [{"candidate_id": str(i)} for i in range(7)]
            return value
        with patch.object(ga_replay, "_snapshot", side_effect=snapshot):
            self.assertEqual(self.read()["observation"]["candidate_count"], 7)

    def test_queries_cannot_read_beyond_publication_boundary(self):
        self.assertEqual(self.read("&gen=100")["observation"]["generation"], 1)
        self.assertEqual(self.read("&gen=-1")["observation"]["generation"], 0)
        self.assertEqual(self.read("&gen=invalid")["observation"]["generation"], 1)

    def test_page_preserves_four_phases_and_exposes_four_observer_tabs(self):
        status, body = self.get("/jobs/job-replay")
        self.assertEqual(status, 200)
        self.assertEqual(body.count('role="tab"'), 4)
        for text in ("概要", "進化のリプレイ", "系譜の川", "世代の推移", "Sifting", "上映"):
            self.assertIn(text, body)
        self.assertIn('data-vessel="replay"', body)
        self.assertIn('data-rw-managed="true"', body)
        self.assertEqual(self.fake.submitted, [])

    def test_legacy_record_keeps_available_tabs_without_historical_replay(self):
        from test_viewer import _create_experiment
        experiment = _create_experiment(self.runs)
        status, raw = self.get(f"/exp/{experiment.name}/monitor?view-data=1")
        self.assertEqual(status, 200, raw)
        observed = json.loads(raw)["observation"]
        self.assertFalse(observed["historical"])
        self.assertIsNone(observed["replay"])
        self.assertTrue(observed["cells"])
        self.assertIn("旧実行", observed["notice"])

    def test_cancel_and_failure_retain_readable_explanations(self):
        self.fake._jobs["job-replay"].update(state="cancelled")
        self.assertIn("利用者の停止要求", self.get("/jobs/job-replay")[1])
        self.fake._jobs["job-replay"].update(state="failed", error={"code": "wall_timeout"})
        self.assertIn("実行時間の上限に達しました", self.get("/jobs/job-replay")[1])


if __name__ == "__main__":
    unittest.main()
