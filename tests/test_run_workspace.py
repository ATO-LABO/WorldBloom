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

    def test_page_preserves_four_phases_and_exposes_five_observer_tabs(self):
        status, body = self.get("/jobs/job-replay")
        self.assertEqual(status, 200)
        self.assertEqual(body.count('role="tab"'), 5)
        for text in ("概要", "進化のリプレイ", "系譜の川", "世代の推移", "世界の需要と拡張", "Sifting", "上映"):
            self.assertIn(text, body)
        self.assertIn('data-vessel="replay"', body)
        self.assertIn('data-rw-managed="true"', body)
        self.assertEqual(self.fake.submitted, [])

    def test_fifth_tab_shows_world_demand_panel_and_condition_row(self):
        status, body = self.get("/jobs/job-replay")
        self.assertEqual(status, 200)
        self.assertIn('id="rw-demand"', body)
        self.assertIn('aria-labelledby="rw-tab-demand"', body)
        # Panel-only text: the tab label alone would satisfy "世界の需要".
        self.assertIn("この実験は世界の需要を集計していません", body)
        # The row shows the chosen setting beside the world the run actually used.
        self.assertIn("<dt>世界の拡張</dt><dd>設定: ", body)
        self.assertIn("回った世界: ベース（拡張なし）", body)

    def test_sifting_sidebar_links_to_demand_for_a_catalogued_run(self):
        # A catalogued run has no top-level archive.json (its record lives under
        # published/N/), so the link must resolve through the catalog.
        from viewer import world_demand_view
        self.assertFalse((self.runs / self.run_id / "archive.json").exists())
        (self.runs / self.run_id / "world_demand.json").write_text(json.dumps({
            "schema_version": 1, "zones": {},
            "triggers": [{"zone": "海", "verb": "investigate", "count": 11, "whiffs": 11,
                          "wasted_share": 0.07}]}), encoding="utf-8")
        link = world_demand_view.sidebar_link(self.server.repository, self.run_id)
        self.assertIn("世界の需要（1件）", link)
        self.assertIn(f"/exp/{self.run_id}/monitor?tab=demand", link)

    def test_condition_row_links_to_demand_tab_when_expanded(self):
        (self.runs / self.run_id / "expanded-project").mkdir()
        (self.runs / self.run_id / "expanded-project" / "world.yaml").write_text(
            "name: x\nexpansion:\n  base: x\n  patches:\n"
            "    - id: p-1\n      title: t\n", encoding="utf-8")
        status, body = self.get("/jobs/job-replay")
        self.assertEqual(status, 200)
        self.assertIn("拡張あり（1件）", body)
        self.assertIn('href="/jobs/job-replay?tab=demand"', body)

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
