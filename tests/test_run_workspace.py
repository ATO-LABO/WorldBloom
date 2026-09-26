"""Observer reads committed records; browsing never starts another execution."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ga_replay as fixture
from viewer import ga_replay, run_workspace
from world_patch_fixtures import write_approved


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

    def test_page_preserves_four_phases_and_exposes_six_observer_tabs(self):
        status, body = self.get("/jobs/job-replay")
        self.assertEqual(status, 200)
        self.assertEqual(body.count('role="tab"'), 6)
        for text in ("概要", "進化のリプレイ", "系譜の川", "世代の推移", "世界の需要と拡張", "拡張の効果", "Sifting", "上映"):
            self.assertIn(text, body)
        self.assertIn('data-vessel="replay"', body)
        self.assertIn('data-rw-managed="true"', body)
        self.assertEqual(self.fake.submitted, [])

    def test_epoch_chain_strip_appears_only_when_this_config_is_the_chains_current_epoch(self):
        # WB-WORLDGROW-001 段階5c-2: fabricate a chain.json directly (this
        # fixture's FakeJobStore can't actually run an epoch chain end to
        # end) whose last epoch's own config_id is "cfg-replay" -- the same
        # config job-replay itself was submitted against.
        import time
        from execution.provenance import atomic_json, directory_lock
        # approval="manual" + gate_status="reviewable" makes the live server's
        # own background tick() (ViewerServer.service_actions polls every
        # 0.05s in this fixture) a no-op on "approve" -- it just re-confirms
        # state=="waiting" instead of crashing on a hand-fabricated chain
        # that skips fields a real EpochChain.start() would have filled in.
        control = self.fake.configs.control
        chain = {"schema_version": 1, "chain_id": "chain-test", "created_at": time.time(),
                 "updated_at": time.time(), "revision": 1, "base_config_id": "cfg-other-base",
                 "max_epochs": 3, "state": "waiting", "approval": "manual", "auto_retire": False,
                 "idle_streak": 0, "stop_requested_at": None, "error": None,
                 "epochs": [{"index": 0, "step": "approve", "config_id": "cfg-replay",
                             "run_job_id": "job-replay", "run_id": self.run_id, "patch_id": None,
                             "gate_status": "reviewable", "approved_rev": None, "retired": [],
                             "notes": ["提案を検査しました"]}]}
        with directory_lock(control / "epochs"):
            atomic_json(control / "epochs" / "chain-test" / "chain.json", chain)
        status, body = self.get("/jobs/job-replay")
        self.assertEqual(status, 200, body)
        self.assertIn("data-epoch-chain", body)
        self.assertIn("承認待ち", body)
        self.assertIn("epoch-chain.js", body)

    def test_no_chain_leaves_the_page_free_of_epoch_chain_markup(self):
        status, body = self.get("/jobs/job-replay")
        self.assertEqual(status, 200)
        self.assertNotIn("data-epoch-chain", body)
        self.assertNotIn("epoch-chain.js", body)

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

    def test_demand_tab_shows_propose_button_time_estimate_and_progress_panel(self):
        # job-replay is a catalogued run (job_store present, config with a
        # resolvable project) -- WB-WORLDGROW-001 段階3b-3's propose button
        # must appear, with the raw trigger index and the run's own name.
        (self.runs / self.run_id / "world_demand.json").write_text(json.dumps({
            "schema_version": 1, "zones": {},
            "triggers": [{"zone": "海", "verb": "investigate", "count": 11, "whiffs": 11,
                          "wasted_share": 0.07}]}), encoding="utf-8")
        status, body = self.get("/jobs/job-replay")
        self.assertEqual(status, 200, body)
        self.assertIn(
            f'data-patch-action="propose" data-run="{self.run_id}" data-trigger="0"', body)
        self.assertIn("6〜11分かかります", body)
        self.assertIn(f'<div class="card" data-patch-job data-run="{self.run_id}" hidden>', body)
        self.assertIn('data-patch-job-cancel', body)
        # No stray reason line when the button is actually offered.
        self.assertNotIn("提案できません", body)

    def test_demand_tab_shows_waiting_hint_when_this_epoch_is_awaiting_approval(self):
        # WB-WORLDGROW-001 段階5c-2: the same fabricated "waiting" chain as
        # test_epoch_chain_strip_appears_only_when_this_config_is_the_chains_current_epoch,
        # this time checking _proposals_html()'s own hint (epoch_view.waiting_hint()).
        import time
        from execution.provenance import atomic_json, directory_lock
        control = self.fake.configs.control
        chain = {"schema_version": 1, "chain_id": "chain-wait", "created_at": time.time(),
                 "updated_at": time.time(), "revision": 1, "base_config_id": "cfg-other-base",
                 "max_epochs": 3, "state": "waiting", "approval": "manual", "auto_retire": False,
                 "idle_streak": 0, "stop_requested_at": None, "error": None,
                 "epochs": [{"index": 0, "step": "approve", "config_id": "cfg-replay",
                             "run_job_id": "job-replay", "run_id": self.run_id, "patch_id": None,
                             "gate_status": "reviewable", "approved_rev": None, "retired": [],
                             "notes": []}]}
        with directory_lock(control / "epochs"):
            atomic_json(control / "epochs" / "chain-wait" / "chain.json", chain)
        status, body = self.get("/jobs/job-replay")
        self.assertEqual(status, 200, body)
        self.assertIn("第 1 エポック: 承認待ち", body)
        self.assertIn("次のエポックへ", body)

    def test_propose_reason_view_only_wins_even_with_a_usable_config(self):
        # Direct-call test (HTTP routing can't reach this combination: a
        # job_store-free experiment_page() falls back to a path that also
        # forces config=None -- see _propose_reason_missing_config_when_can_write
        # below for that branch). can_write is checked first regardless.
        from viewer import run_workspace

        class FakeServer:
            job_store = None

        class FakeHandler:
            server = FakeServer()

        propose_run, reason = run_workspace._propose_run_and_reason(
            FakeHandler(), {"config": {"project_id": "romance", "template_id": "romance"}, "run_name": "x"})
        self.assertIsNone(propose_run)
        self.assertEqual(reason, "閲覧モードでは提案できません")

    def test_propose_reason_missing_config_when_can_write(self):
        from viewer import run_workspace

        class FakeServer:
            job_store = object()

        class FakeHandler:
            server = FakeServer()

        propose_run, reason = run_workspace._propose_run_and_reason(
            FakeHandler(), {"config": None, "run_name": "x"})
        self.assertIsNone(propose_run)
        self.assertEqual(reason, "凍結入力の無い実験からは提案できません")

    def test_propose_reason_names_a_missing_world_separately(self):
        from unittest import mock
        from viewer import run_workspace

        class FakeServer:
            job_store = object()

        class FakeHandler:
            server = FakeServer()

        with mock.patch.object(run_workspace, "_expansion_project", return_value=None):
            propose_run, reason = run_workspace._propose_run_and_reason(
                FakeHandler(), {"config": {"project_id": "gone"}, "run_name": "x"})
        self.assertIsNone(propose_run)
        self.assertEqual(reason, "この実験の世界が見つからないため、提案できません")

    def test_propose_reason_none_when_view_is_none(self):
        from viewer import run_workspace
        self.assertEqual(run_workspace._propose_run_and_reason(object(), None), (None, None))

    def test_legacy_experiment_hides_propose_button_with_reason(self):
        # A legacy experiment has no config_id -- run_workspace._demand_html
        # must not offer a button the server would 422 on.
        from test_viewer import _create_experiment
        experiment = _create_experiment(self.runs)
        (experiment / "world_demand.json").write_text(json.dumps({
            "schema_version": 1, "zones": {},
            "triggers": [{"zone": "海", "verb": "investigate", "count": 11, "whiffs": 11,
                          "wasted_share": 0.07}]}), encoding="utf-8")
        status, body = self.get(f"/exp/{experiment.name}/monitor")
        self.assertEqual(status, 200, body)
        self.assertNotIn("data-patch-action=\"propose\"", body)
        self.assertIn("凍結入力の無い実験からは提案できません", body)

    def test_polling_response_excludes_propose_and_progress_markup(self):
        (self.runs / self.run_id / "world_demand.json").write_text(json.dumps({
            "schema_version": 1, "zones": {},
            "triggers": [{"zone": "海", "verb": "investigate", "count": 11, "whiffs": 11,
                          "wasted_share": 0.07}]}), encoding="utf-8")
        status, raw = self.get("/jobs/job-replay?view-data=1")
        self.assertEqual(status, 200, raw)
        self.assertNotIn("data-patch-action", raw)
        self.assertNotIn("data-patch-job", raw)
        json.loads(raw)  # still valid JSON, no HTML leaked into a field

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

    def test_broken_world_yaml_does_not_break_the_run_page(self):
        # M1 (viewer review): _proposals_html's yaml.safe_load(world.yaml)
        # raised a bare yaml.YAMLError, which READ_ERRORS didn't list --
        # do_GET had no handler for it, so the connection just dropped
        # instead of the page rendering. _expansion_project is monkeypatched
        # here (rather than corrupting projects/romance/world.yaml, which
        # this task must not touch) to point at an isolated broken world.
        temp = tempfile.TemporaryDirectory(prefix="wb-run-workspace-broken-world-")
        self.addCleanup(temp.cleanup)
        broken_project = Path(temp.name) / "brokenworld"
        broken_project.mkdir()
        (broken_project / "world.yaml").write_text("name: [unclosed", encoding="utf-8")
        original = run_workspace._expansion_project
        run_workspace._expansion_project = lambda handler, view: broken_project
        self.addCleanup(setattr, run_workspace, "_expansion_project", original)
        status, body = self.get("/jobs/job-replay")
        self.assertEqual(status, 200, body)
        self.assertIn('id="rw-demand"', body)
        # The world-demand display itself is untouched by the broken world.yaml...
        self.assertIn("この実験は世界の需要を集計していません", body)
        # ...and the proposals section degrades to a message instead of
        # taking the whole page down with it.
        self.assertIn("世界の拡張を読み込めませんでした", body)

    def test_proposals_html_survives_broken_world_yaml_directly(self):
        # Direct-call companion to the HTTP test above, in ReplayModelDirectTests'
        # style (tests/test_ga_replay.py) -- proves _proposals_html itself
        # returns a graceful string rather than raising.
        temp = tempfile.TemporaryDirectory(prefix="wb-run-workspace-broken-world-direct-")
        self.addCleanup(temp.cleanup)
        broken_project = Path(temp.name) / "brokenworld"
        broken_project.mkdir()
        (broken_project / "world.yaml").write_text("name: [unclosed", encoding="utf-8")
        original = run_workspace._expansion_project
        run_workspace._expansion_project = lambda handler, view: broken_project
        self.addCleanup(setattr, run_workspace, "_expansion_project", original)
        html = run_workspace._proposals_html(None, {"config": {}}, "exp-1")
        # WB-WORLDGROW-001 段階3b-3: the job-progress panel is now always
        # emitted once project_dir resolves, even if the rest of the state
        # can't be read.
        self.assertTrue(html.startswith('<div class="card" data-patch-job data-run="exp-1" hidden>'))
        self.assertIn('<p class="rw-empty">世界の拡張を読み込めませんでした。</p>', html)

    def test_usage_html_uses_repository_archive_not_a_top_level_archive_json(self):
        # M1 (Opus review, WB-WORLDGROW-001 段階5a): _usage_html must feed
        # patch_usage() with handler.repository.archive(experiment) -- a
        # ConfigStore-prepared (screen-run) experiment only ever has
        # published/<revision>/archive.json once the catalog has published
        # it, never a top-level archive.json. Proven directly (no HTTP, no
        # real catalog -- that machinery is covered by tests/
        # test_world_expansion_api.py's own M1 test): a fake repository
        # returns a hand-built archive dict, and the experiment directory on
        # disk deliberately has no archive.json at all -- the pre-fix code
        # (patch_usage(archive=None), which read experiment/archive.json)
        # would have raised here instead of rendering the panel.
        class _Configs:
            pass

        class _FakeJobStore:
            def __init__(self, repo):
                self.configs = _Configs()
                self.configs.repo = repo

        class _FakeServer:
            def __init__(self, job_store):
                self.job_store = job_store

        class _FakeRepository:
            def __init__(self, archive):
                self._archive = archive

            def archive(self, experiment):
                return self._archive

        class _FakeHandler:
            def __init__(self, repository, job_store):
                self.repository = repository
                self.server = _FakeServer(job_store)

        temp = tempfile.TemporaryDirectory(prefix="wb-usage-html-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        repo = base / "repo"
        project_dir = repo / "projects" / "testworld"
        patch = write_approved(project_dir, {"title": "使用表試験",
                                              "add": {"zones": [{"name": "小屋", "parent": "海"}]}})
        experiment = base / "runs" / "exp-usage"
        log_relative = "g0/ind-0/seed-1/layers.jsonl"
        log_path = experiment / log_relative
        log_path.parent.mkdir(parents=True)
        rows = [{"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
                 "delta": {"actor": {"zone": "小屋"}}}]
        log_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        self.assertFalse((experiment / "archive.json").exists())

        archive = {"cells": {"c0": {"exemplar": {"layers_path": log_relative}}}}
        handler = _FakeHandler(_FakeRepository(archive), _FakeJobStore(repo))
        view = {"config": {"project_id": "testworld", "template_id": "testworld",
                            "preview": {"protagonist": "桃太郎"}}}
        state = {"state": "expanded", "patches": [{"id": patch["id"]}]}
        html = run_workspace._usage_html(handler, view, state, experiment)
        self.assertIn('class="card we-usage-panel"', html)
        self.assertIn("この実験での拡張の使われ方", html)
        self.assertIn("使用表試験", html)
        self.assertIn("強い使用1体", html)


class _Configs:
    pass


class _FakeJobStore:
    def __init__(self, repo):
        self.configs = _Configs()
        self.configs.repo = repo


class _FakeServer:
    def __init__(self, job_store):
        self.job_store = job_store


class _FakeRepository:
    def __init__(self, archive):
        self._archive = archive

    def archive(self, experiment):
        return self._archive


class _FakeHandler:
    def __init__(self, repository, job_store):
        self.repository = repository
        self.server = _FakeServer(job_store)


class UsageHtmlExportButtonTests(unittest.TestCase):
    """WB-WORLDGROW-001 段階5d: _usage_html()'s「ジャンルの資産にする」ボタン
    の出し分け -- 強い使用の有無・すでに資産にした済みかどうか・閲覧モード。"""

    def _build(self, *, zone_hit="小屋"):
        temp = tempfile.TemporaryDirectory(prefix="wb-usage-export-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        repo = base / "repo"
        project_dir = repo / "projects" / "testworld"
        patch = write_approved(project_dir, {"title": "使用表試験",
                                              "add": {"zones": [{"name": "小屋", "parent": "海"}]}})
        experiment = base / "runs" / "exp-usage"
        log_relative = "g0/ind-0/seed-1/layers.jsonl"
        log_path = experiment / log_relative
        log_path.parent.mkdir(parents=True)
        rows = [{"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
                 "delta": {"actor": {"zone": zone_hit}}}]
        log_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        archive = {"cells": {"c0": {"exemplar": {"layers_path": log_relative}}}}
        return repo, experiment, patch, archive

    def _render(self, repo, archive, experiment, patch, *, job_store):
        handler = _FakeHandler(_FakeRepository(archive), job_store)
        view = {"config": {"project_id": "testworld", "template_id": "testworld",
                            "preview": {"protagonist": "桃太郎"}}}
        state = {"state": "expanded", "patches": [{"id": patch["id"]}]}
        return run_workspace._usage_html(handler, view, state, experiment)

    def test_strong_use_shows_an_enabled_button(self):
        repo, experiment, patch, archive = self._build()
        html = self._render(repo, archive, experiment, patch, job_store=_FakeJobStore(repo))
        self.assertIn("ジャンルの資産にする", html)
        segment = html[html.find('data-patch-action="export"'):html.find(">", html.find('data-patch-action="export"'))]
        self.assertNotIn("disabled", segment)

    def test_unused_patch_disables_the_button_with_a_reason(self):
        repo, experiment, patch, archive = self._build(zone_hit="別の場所")
        html = self._render(repo, archive, experiment, patch, job_store=_FakeJobStore(repo))
        segment = html[html.find('data-patch-action="export"'):html.find(">", html.find('data-patch-action="export"'))]
        self.assertIn("disabled", segment)
        self.assertIn('title="この実験では使われていません"', segment)

    def test_already_exported_shows_done_text_and_no_button(self):
        repo, experiment, patch, archive = self._build()
        expansions = repo / "templates" / "testworld" / "expansions"
        expansions.mkdir(parents=True)
        (expansions / f"{patch['id']}.yaml").write_text("schema_version: 1\npatch: {}\n", encoding="utf-8")
        html = self._render(repo, archive, experiment, patch, job_store=_FakeJobStore(repo))
        self.assertIn("資産にした済み", html)
        self.assertNotIn("ジャンルの資産にする</button>", html)

    def test_read_only_viewer_has_no_export_or_retire_ui(self):
        repo, experiment, patch, archive = self._build()
        html = self._render(repo, archive, experiment, patch, job_store=None)
        self.assertNotIn("ジャンルの資産にする", html)
        self.assertNotIn("data-patch-action", html)


class ProposalsHtmlLibraryEligibilityTests(unittest.TestCase):
    """WB-WORLDGROW-001 段階5d R1 (Opus review): a library-imported proposal
    (author.backend=="library") only appears -- under its own heading -- on
    a run whose own frozen world was built with exactly the currently-
    approved patches; otherwise its holdout check is bound to fail there."""

    def _build(self):
        temp = tempfile.TemporaryDirectory(prefix="wb-proposals-library-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        repo = base / "repo"
        project_dir = repo / "projects" / "testworld"
        project_dir.mkdir(parents=True)
        (project_dir / "world.yaml").write_text("name: test\nzones: []\n", encoding="utf-8")
        patch = write_approved(project_dir, {"title": "承認済み",
                                              "add": {"zones": [{"name": "小屋P", "parent": "海"}]}})
        proposed_dir = project_dir / "patches" / "_proposed"
        proposed_dir.mkdir(parents=True)
        import yaml
        lib_patch = {"id": "p-libimport1", "title": "取り込んだ拡張", "parent_digest": "x",
                    "trigger": {"zone": "海", "verb": "investigate"},
                    "author": {"backend": "library", "origin": {"world_name": "元の世界"}},
                    "add": {"zones": [{"name": "小屋Q", "parent": "海"}]}}
        (proposed_dir / "p-libimport1.yaml").write_text(
            yaml.safe_dump(lib_patch, allow_unicode=True), encoding="utf-8")
        return base, repo, patch

    def _frozen_world(self, base, patch_ids):
        experiment = base / "runs" / "exp-1"
        world_path = experiment / "inputs" / "projects" / "testworld" / "world.yaml"
        world_path.parent.mkdir(parents=True)
        import yaml
        world_path.write_text(yaml.safe_dump(
            {"expansion": {"patches": [{"id": pid} for pid in patch_ids]}}, allow_unicode=True), encoding="utf-8")
        return experiment

    def _proposals_html(self, repo, experiment, run_name="exp-1"):
        handler = _FakeHandler(_FakeRepository({}), _FakeJobStore(repo))
        view = {"config": {"project_id": "testworld", "template_id": "testworld"}}
        return run_workspace._proposals_html(handler, view, run_name, experiment=experiment)

    def test_eligible_when_frozen_matches_active_approved(self):
        base, repo, patch = self._build()
        experiment = self._frozen_world(base, [patch["id"]])
        html = self._proposals_html(repo, experiment)
        self.assertIn("取り込んだ拡張（この実験で検査できます）", html)
        self.assertIn("取り込んだ拡張", html)

    def test_hidden_when_frozen_world_does_not_match_active_approved(self):
        base, repo, patch = self._build()
        experiment = self._frozen_world(base, [])  # frozen with no patches applied -- mismatch
        html = self._proposals_html(repo, experiment)
        self.assertNotIn("取り込んだ拡張（この実験で検査できます）", html)

    def test_hidden_when_no_experiment_is_given(self):
        base, repo, patch = self._build()
        html = self._proposals_html(repo, experiment=None)
        self.assertNotIn("取り込んだ拡張（この実験で検査できます）", html)

    def test_own_heading_still_appears_on_the_error_branch(self):
        # R4 (Opus review): state["error"] must not swallow the always-first
        # heading -- keeps that branch byte-identical to the pre-5d version.
        base, repo, patch = self._build()
        (repo / "projects" / "testworld" / "patches" / "stack.json").write_text("not json", encoding="utf-8")
        html = self._proposals_html(repo, experiment=None)
        self.assertTrue(html.startswith(
            '<div class="card" data-patch-job data-run="exp-1" hidden><p role="status"></p>'
            '<button type="button" data-patch-job-cancel>停止</button></div>'
            "<h3>この実験から生まれた提案</h3>"))
        self.assertIn("拡張の記録を読み込めませんでした", html)


class SeedGenomesRunWorkspaceTests(unittest.TestCase):
    """WB-WORLDGROW-001 段階5b: run_workspace.river_payload()'s own
    "seedCell" field (run-workspace.js's parentLabel() reads it)."""
    setUp = fixture.SeedGenomesReplayTests.setUp
    get = fixture.SeedGenomesReplayTests.get

    def test_seed_cell_reaches_the_river_payload(self):
        status, text = self.get("/jobs/job-replay-seed?view-data=1&gen=0")
        self.assertEqual(status, 200, text)
        result = json.loads(text)["observation"]
        node0 = next(n for n in result["river"]["nodes"] if n["index"] == 0)
        self.assertEqual(node0["seedCell"], "II|mid")
        node1 = next(n for n in result["river"]["nodes"] if n["index"] == 1)
        # Opus review R4: seedCell is only present on a node that actually
        # has one -- a seed-less node carries no key at all.
        self.assertNotIn("seedCell", node1)


if __name__ == "__main__":
    unittest.main()
