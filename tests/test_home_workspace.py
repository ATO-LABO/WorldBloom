from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import tempfile
import unittest
import yaml

from execution.library import LibraryStore
from viewer import data, pages, home_pages, server


class HomeWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.world = self.root / "projects" / "original"
        self.world.mkdir(parents=True)
        self.file = self.world / "world.yaml"
        self.file.write_text(yaml.safe_dump({"name": "同じ世界", "overview": "保存した紹介 <script>危険</script>",
            "protagonist": "花子", "antagonist": "太郎", "zones": [{"name": "村"}],
            "time": {"days": 7}}, allow_unicode=True), encoding="utf-8")
        (self.world / "subjects").mkdir()
        (self.world / "subjects" / "dog.yaml").write_text("id: 犬", encoding="utf-8")
        self.store = LibraryStore(self.root)
        (self.root / "runs").mkdir()
        self.repository = data.RunRepository(self.root / "runs")
        self.jobs_root = self.root / "jobs"
        self.jobs_root.mkdir()
        self.configs = SimpleNamespace(repo=self.root, get=lambda cid: {"preview": {"world_name": "実行時の世界"}})
        self.jobs = SimpleNamespace(root=self.jobs_root, configs=self.configs,
            list=lambda: self.fail("Home must not reconcile jobs"), get=lambda _: self.fail("Home must not reconcile jobs"))

    def job(self, jid, stamp, **kwargs):
        folder = self.jobs_root / jid
        folder.mkdir()
        (folder / "job.json").write_text(json.dumps(dict(job_id=jid, created_at=stamp, kind="evolve", config_id="cfg-test", **kwargs)), encoding="utf-8")

    def test_saved_overview_escaped_and_world_link_not_execution(self):
        page = home_pages.render(self.repository, job_store=self.jobs)
        self.assertIn("保存した紹介 &lt;script&gt;", page)
        self.assertNotIn("<script>危険", page)
        self.assertIn('href="/worlds/original"', page)
        self.assertNotIn('data-quick-start', page)
        self.assertIn("場所 1か所", page)
        self.assertIn("7日間", page)
        self.assertIn(' 犬" data-genre', page)
        self.assertIn('data-search="同じ世界 original ジャンル未設定 花子 太郎', page)

    def test_blank_overview_is_distinct_from_missing(self):
        world = self.store.worlds()[0]
        self.file.write_text('overview: ""\nzones: [{name: 村}]\ntime: {days: true}', encoding="utf-8")
        desc, places, period = home_pages.world_details(self.store, world)
        self.assertIn("まだ設定", desc)
        self.assertEqual(period, "期間 未設定")
        self.file.write_text('zones: [{name: 村}]\ntime: {days: 9}', encoding="utf-8")
        self.assertEqual(home_pages.world_details(self.store, world), ("舞台：村。", 1, "9日間"))

    def test_same_names_keep_distinct_world_identity(self):
        other = self.root / "projects" / "second"
        other.mkdir()
        (other / "world.yaml").write_bytes(self.file.read_bytes())
        page = home_pages.render(self.repository, job_store=self.jobs)
        self.assertIn('href="/worlds/original"', page)
        self.assertIn('href="/worlds/second"', page)

    def test_recent_uses_saved_time_not_id_and_does_not_mutate(self):
        self.job("job-z", 100)
        self.job("job-a", 200)
        self.job("job-unknown", None)
        self.job("job-infinite", float("inf"))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()}
        page = home_pages.render(self.repository, job_store=self.jobs)
        self.assertIn('href="/jobs/job-a"', page)
        self.assertNotIn('href="/jobs/job-z"', page)
        self.assertIn("実行時の世界", page)
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_recent_ignores_generation_and_malformed_receipts(self):
        folder = self.jobs_root / "job-bad"
        folder.mkdir()
        (folder / "job.json").write_text("[]", encoding="utf-8")
        folder = self.jobs_root / "job-output"
        folder.mkdir()
        (folder / "job.json").write_text('{"job_id":"job-output","kind":"narrate","created_at":300}', encoding="utf-8")
        self.assertEqual(home_pages.recent_execution(self.jobs), "")
        self.assertEqual(home_pages.recent_execution(SimpleNamespace()), "")

    def test_readonly_mode_and_empty_state(self):
        with patch.object(data, "ROOT", self.root):
            page = home_pages.render(self.repository)
        self.assertIn("閲覧専用", page)
        self.assertNotIn('href="/worlds/new"', page)
        with patch.object(LibraryStore, "worlds", return_value=[]), patch.object(LibraryStore, "genres", return_value=[]):
            page = home_pages.render(self.repository, job_store=self.jobs)
        self.assertIn("まだ世界がありません", page)
        self.assertIn("ジャンルがありません", page)
        self.assertIn('href="/worlds/new"', page)

    def test_unreadable_world_settings_do_not_invent_content(self):
        self.file.write_text("[broken", encoding="utf-8")
        desc, places, period = home_pages.world_details(self.store, {"id": "original"})
        self.assertEqual(places, 0)
        self.assertEqual(period, "期間 未設定")
        self.assertNotIn("桃太郎", desc)

    def test_shared_header_has_labeled_icons_in_same_order_everywhere(self):
        for home in (False, True):
            page = pages.document("test", "", is_home=home, run="x"*200)
            head = page.split('<header class="site-header">', 1)[1].split("</header>", 1)[0]
            local_status = head.index('data-sheet="local-status-dialog"')
            history = head.index('href="/history"')
            settings = head.index('href="/configs"')
            self.assertLess(local_status, history)
            self.assertLess(history, settings)
            self.assertIn("<span>動作状況</span>", head)
            self.assertIn("<span>実行履歴</span>", head)
            self.assertIn("<span>設定</span>", head)
            self.assertEqual(head.count('aria-hidden="true" focusable="false"'), 3)
            self.assertIn('id="local-status-dialog"', head)
            self.assertIn('data-fetch="/api/status/local"', head)
        self.assertIn("home-workspace.js", server.STATIC_FILES)
        self.assertIn("home-workspace.css", server.STATIC_FILES)
