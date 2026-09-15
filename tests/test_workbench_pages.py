"""WB-UI-007: the configuration/execution/Sifting HTML workbench.

No LLM and no real GA process is started anywhere in this module. Jobs are
supplied through a duck-typed fake job store; GA execution is exercised only
through the pre-existing, already-tested APIs (job_api.py / run_catalog.py).
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from execution.configs import ConfigStore
from execution.provenance import ConfigError, atomic_json, canonical, sha256, write_bytes
from execution.worker import TERMINAL
from viewer import data, workbench_pages
from viewer.data import RunRepository
from viewer.server import ViewerServer, ViewerHandler

from test_viewer import _create_experiment

ROOT = Path(__file__).resolve().parents[1]


class FakeJobStore:
    """Duck-typed job store.

    workbench_pages.py only calls .configs / .list() / .get() / .assert_run_idle().
    The private _lock()/_all()/_reconcile() methods exist only because the
    pre-existing, unmodified SelectionStore._guard() reaches them through
    RunCatalog.jobs when the real selection API (POST /api/runs/{id}/selection)
    is exercised end to end in these tests.
    """

    def __init__(self, configs):
        self.configs = configs
        self._jobs = {}
        self.submitted = []

    def add(self, job):
        self._jobs[job["job_id"]] = job

    def list(self):
        return [dict(job) for job in self._jobs.values()]

    def get(self, jid):
        if jid not in self._jobs:
            raise ConfigError("job_id", "ジョブがありません", code="not_found")
        return dict(self._jobs[jid])

    def assert_run_idle(self, run_id):
        for job in self._jobs.values():
            if job.get("run_id") == run_id and job["state"] not in TERMINAL:
                raise ConfigError("run_id", "実行中の結果は選定できません", code="conflict")

    def submit(self, request, *, settings_path=None):
        self.submitted.append(request)
        raise ConfigError("worker", "テストではGAを起動しません", code="unavailable")

    def cancel(self, jid):
        raise ConfigError("worker", "テストでは停止できません", code="unavailable")

    @contextmanager
    def _lock(self):
        yield

    def _all(self):
        return list(self._jobs.values())

    def _reconcile(self, job):
        return dict(job)


def _job(jid, run_id, state, *, phase="evaluating", config_id="cfg-test",
         error=None, publication_revision=None, reconciliation="confirmed"):
    now = time.time()
    return {
        "schema_version": 1, "job_id": jid, "request_id": "req-" + jid, "kind": "evolve",
        "config_id": config_id, "run_id": run_id, "state": state, "phase": phase, "revision": 1,
        "created_at": now, "updated_at": now, "started_at": now,
        "finished_at": now if state in TERMINAL else None,
        "heartbeat": now, "cancel_requested_at": None, "error": error, "exit_code": None,
        "progress": {"completed_individuals": 1, "completed_seeds": 1,
                     "total_individuals": 2, "total_seeds": 2, "detail_available": False},
        "reconciliation": reconciliation, "publication_revision": publication_revision,
    }


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui007-")
        self.base = Path(self.temp.name)
        self.runs = self.base / "runs"
        self.runs.mkdir()
        self.control = self.base / "control"
        self.configs = ConfigStore(ROOT, self.control, self.runs)
        self.configs.save({
            "label": "wb", "project_id": "romance", "template_id": "romance",
            "evolution": {"generations": 1, "population": 1, "seeds": 1},
        }, config_id="cfg-test")
        # WB-UI-021: a private, writable settings.json -- never the real repo's
        # (the /api/settings/output round trip below actually writes to it).
        self.settings_path = self.base / "settings.json"
        self.fake = FakeJobStore(self.configs)
        self.server = self._start_server(job_store=self.fake, control=self.control)
        # A test that launches a real supervisor process (test_candidates_running_lock_via_http)
        # can leave a Windows file handle on the job folder open for a brief moment after
        # the process exits; retry cleanup instead of failing on a transient PermissionError.
        self.addCleanup(self._cleanup_temp)

    def _cleanup_temp(self):
        for _ in range(50):
            try:
                self.temp.cleanup()
                return
            except PermissionError:
                time.sleep(0.1)
        self.temp.cleanup()

    def _start_server(self, *, job_store=None, control=None):
        server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        if control is not None:
            server.repository = RunRepository(self.runs, control_root=control, jobs=job_store)
        else:
            server.repository = RunRepository(self.runs)
        if job_store is not None:
            server.job_store = job_store
        server.settings_path = self.settings_path
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        # LIFO cleanup order: shutdown() (stop the poll loop) must run before
        # server_close() (release the socket), so register close first.
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def get_status(self, path, *, port=None):
        port = port or self.server.server_port
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                return response.status, response.read().decode("utf-8"), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8"), error.headers

    def http(self, method, path, body=None, headers=None, *, port=None):
        port = port or self.server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        defaults = {"Content-Type": "application/json", "X-WorldBloom-Client": "1"}
        if headers:
            defaults.update(headers)
        try:
            conn.request(method, path, None if body is None else json.dumps(body), headers=defaults)
            response = conn.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
        finally:
            conn.close()

    def _legacy_experiment(self, name):
        root = self.runs / name
        root.mkdir()
        (root / "a.jsonl").write_bytes(b'{"kind":"a"}\n{"kind":"b"}\n')
        (root / "b.jsonl").write_bytes(b'{"kind":"c"}\n')
        archive = {
            "cells": {
                "I|low": {"generation": 0, "quality": 0.5, "reach_rate": 1.0, "reached": True,
                          "exemplar": {"seed": 1, "layers_path": "a.jsonl"}, "parents": [], "genome": {}},
                "VI|high": {"generation": 1, "quality": 0.7, "reach_rate": 0.0, "reached": False,
                            "exemplar": {"seed": 2, "layers_path": "b.jsonl"}, "parents": [], "genome": {}},
            },
        }
        atomic_json(root / "archive.json", archive)
        return root

    # ---------------------------------------------------------------- nav

    def test_nav_and_unconfigured_guidance(self):
        status, body, _ = self.get_status("/")
        self.assertEqual(status, 200, body)
        status, configs_body, _ = self.get_status("/configs")
        self.assertEqual(status, 200, configs_body)
        # The header shell (home link or brand + 設定/実行履歴 shortcuts) is
        # common to every page; 実験一覧/選定トレイ no longer appear as global
        # nav links (home link replaces the former, Sifting トレイ is not
        # global anymore). Home leads with the brand (it already *is* home);
        # every other page leads with the ⌂ home link instead.
        self.assertNotIn('<a class="home-cell" href="/">⌂ ホーム</a>', body)
        self.assertIn('<a class="brand" href="/">WorldBloom</a>', body)
        self.assertIn('<a class="home-cell" href="/">⌂ ホーム</a>', configs_body)
        self.assertNotIn('<a class="brand" href="/">WorldBloom</a>', configs_body)
        for href, label, icon in (("/configs", "設定", "⚙"), ("/history", "実行履歴", "📝")):
            with self.subTest(href=href):
                link = f'<a href="{href}" title="{label}" aria-label="{label}">{icon}</a>'
                self.assertIn(link, body)
                self.assertIn(link, configs_body)

        plain = self._start_server()
        for path in ("/configs", "/jobs", "/history", "/selected"):
            with self.subTest(path=path):
                status, body, _ = self.get_status(path, port=plain.server_port)
                self.assertEqual(status, 200, body)
                self.assertIn("実行管理は未設定です", body)

        status, body, _ = self.get_status("/", port=plain.server_port)
        self.assertEqual(status, 200, body)
        _create_experiment(self.runs)
        status, body, _ = self.get_status("/exp/exp-viewer", port=plain.server_port)
        self.assertEqual(status, 200, body)
        status, payload = self.http(
            "POST", "/exp/exp-viewer/selection", {"cell": "III|high", "selected": True},
            port=plain.server_port,
        )
        self.assertEqual(status, 200, payload)

    # ---------------------------------------------------------- configs

    def test_configs_list_and_detail(self):
        status, body, _ = self.get_status("/configs")
        self.assertEqual(status, 200, body)
        self.assertIn("wb", body)
        self.assertIn("cfg-test", body)

        # WB-UI-021: /configs carries the single 文章生成 card, defaulted from
        # an absent settings.json (DEFAULT_BACKEND = codex-cli).
        self.assertIn('<section class="card" id="output">', body)
        self.assertIn('data-wb="output-settings"', body)
        self.assertIn(
            'type="radio" id="f-backend-codex-cli" name="backend" value="codex-cli" '
            'data-field="backend" checked',
            body,
        )

        status, body, _ = self.get_status("/configs/cfg-test")
        self.assertEqual(status, 200, body)
        self.assertIn("予定評価数", body)
        self.assertIn("編集不可", body)
        # WB-UI-021: generation moved off the execution config entirely --
        # nothing about it is shown on the config detail page any more.
        self.assertNotIn("生成設定", body)

        status, body, _ = self.get_status("/configs/absent")
        self.assertEqual(status, 404, body)

    def test_new_config_form(self):
        status, body, _ = self.get_status("/configs/new")
        self.assertEqual(status, 200, body)
        for field in (
            "label", "project_id", "template_id",
            "evolution.generations", "evolution.population", "evolution.seeds",
            "evolution.seed_base", "evolution.ga_seed", "evolution.processes",
            "evolution.keep", "evolution.coevolve", "evolution.meta_evolution",
            "evolution.record_explanations", "evolution.target_ending",
            "execution_limits.wall_seconds",
        ):
            with self.subTest(field=field):
                self.assertIn(f'data-field="{field}"', body)
                self.assertIn(f'data-error-for="{field}"', body)
        # WB-UI-021: no more generation.* fields on the run-config form --
        # text generation moved to the /configs 文章生成 card.
        self.assertNotIn('data-field="generation', body)
        self.assertIn('<option value="romance">romance</option>', body)
        self.assertIn('class="cfg-adv"', body)
        self.assertIn(
            'type="radio" id="f-evolution.keep-reached" name="evolution.keep" value="reached" '
            'data-field="evolution.keep" checked',
            body,
        )
        # The fixture's "romance" world resolves to the "romance" genre
        # (templates/romance exists), so its <option> carries data-genre.
        self.assertIn('data-genre="', body)
        self.assertIn('data-per-gen', body)

        status, body, _ = self.get_status("/configs/new?from=cfg-test")
        self.assertEqual(status, 200, body)
        self.assertIn('data-parent="cfg-test"', body)
        self.assertGreaterEqual(body.count('type="hidden"'), 2)
        self.assertIn('data-summary', body)

    def test_output_settings_api_round_trip(self):
        status, before = self.http("GET", "/api/settings/output")
        self.assertEqual(status, 200, before)
        self.assertEqual(before["backend"], "codex-cli")
        self.assertIsNone(before["model"])
        self.assertIn("availability", before)

        status, after = self.http("POST", "/api/settings/output", {
            "backend": "openai", "model": "gpt-secret",
            "limits": {"max_calls": 3, "call_timeout_seconds": 90,
                       "wall_seconds": 300, "max_saved_response_bytes": 4096},
        })
        self.assertEqual(status, 200, after)
        self.assertEqual(after["backend"], "openai")
        self.assertEqual(after["model"], "gpt-secret")
        self.assertEqual(after["limits"]["max_calls"], 3)
        self.assertNotIn("api_key", json.dumps(after))

        status, refetched = self.http("GET", "/api/settings/output")
        self.assertEqual(status, 200, refetched)
        self.assertEqual(refetched["backend"], "openai")
        self.assertEqual(refetched["model"], "gpt-secret")

        # A saved credential (api_key) in settings.json's own "openai" section
        # must survive a later write and never reach the API response.
        raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        raw["output"]["openai"]["api_key"] = "DO-NOT-LEAK"
        self.settings_path.write_text(json.dumps(raw), encoding="utf-8")
        status, after2 = self.http("POST", "/api/settings/output", {"model": "gpt-secret-2"})
        self.assertEqual(status, 200, after2)
        self.assertNotIn("DO-NOT-LEAK", json.dumps(after2))
        raw2 = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(raw2["output"]["openai"]["api_key"], "DO-NOT-LEAK")

        status, payload = self.http(
            "POST", "/api/settings/output", {"model": "x"}, headers={"X-WorldBloom-Client": ""},
        )
        self.assertEqual(status, 403, payload)

        status, payload = self.http("POST", "/api/settings/output", {"backend": "bogus"})
        self.assertEqual(status, 422, payload)

    def test_configs_page_survives_unreadable_settings_json(self):
        # WB-UI-021 review item 2: a broken settings.json must not 500 /configs.
        self.settings_path.write_text("{not json", encoding="utf-8")
        status, body, _ = self.get_status("/configs")
        self.assertEqual(status, 200, body)
        self.assertIn("settings.json を読めません", body)
        self.assertNotIn('data-wb="output-settings"', body)

    def test_duplicate_api(self):
        status, payload = self.http(
            "POST", "/api/configs/cfg-test/duplicate",
            {"changes": {"label": "copy", "evolution": {"generations": 2}}},
        )
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["parent_config_id"], "cfg-test")
        self.assertEqual(payload["evolution"]["generations"], 2)
        self.assertEqual(len(self.configs.list()), 2)

        status, payload = self.http(
            "POST", "/api/configs/cfg-test/duplicate", {"changes": {"project_id": "detective"}},
        )
        self.assertEqual(status, 422, payload)

        status, payload = self.http(
            "POST", "/api/configs/cfg-test/duplicate", {"changes": {"label": "x"}},
            headers={"X-WorldBloom-Client": ""},
        )
        self.assertEqual(status, 403, payload)

        status, payload = self.http("POST", "/api/configs/cfg-test/duplicate", {"nope": 1})
        self.assertEqual(status, 400, payload)

        status, payload = self.http(
            "POST", "/api/configs/absent/duplicate", {"changes": {"label": "x"}},
        )
        self.assertEqual(status, 404, payload)

    def test_start_redirects_to_run_page(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request("GET", "/configs/cfg-test/start")
            response = conn.getresponse()
            self.assertEqual(response.status, 302)
            self.assertEqual(response.getheader("Location"), "/jobs?world=romance&config=cfg-test")
            response.read()
        finally:
            conn.close()
        self.assertEqual(self.fake.submitted, [])

    # ------------------------------------------------------------- jobs

    def test_jobs_list_and_detail(self):
        self.fake.add(_job("job-run", "run-a", "running"))
        self.fake.add(_job("job-fail", "run-b", "failed", error={"code": "worker_disappeared"}))
        self.fake.add(_job("job-ok", "run-c", "succeeded", publication_revision=1))
        self.fake.add(_job("job-unknown", "run-d", "running", reconciliation="unknown"))

        status, body, _ = self.get_status("/history")
        self.assertEqual(status, 200, body)
        running_at = body.index("<h2>進行中</h2>")
        history_at = body.index("<h2>履歴</h2>")
        self.assertGreater(history_at, running_at)
        self.assertLess(body.index("run-a"), history_at)
        self.assertGreater(body.index("run-b"), history_at)
        self.assertGreater(body.index("<td>run-c</td>"), history_at)
        self.assertLess(body.index("run-d"), history_at)

        status, body, _ = self.get_status("/jobs/job-run")
        self.assertEqual(status, 200, body)
        self.assertIn('data-poll="1"', body)
        self.assertIn('data-terminal="false"', body)
        self.assertIn('data-action="cancel"', body)
        self.assertNotIn('data-action="cancel" disabled', body)

        status, body, _ = self.get_status("/jobs/job-fail")
        self.assertEqual(status, 200, body)
        self.assertIn("監視プロセスが消失しました", body)
        self.assertIn("同じ設定で新しく実行できます", body)
        self.assertIn("/jobs?world=romance&amp;config=cfg-test", body)

        status, body, _ = self.get_status("/jobs/job-ok")
        self.assertEqual(status, 200, body)
        self.assertIn("Sifting で候補を選ぶ", body)
        self.assertIn("/exp/run-c", body)
        self.assertIn("/runs/run-c/candidates", body)

        status, body, _ = self.get_status("/jobs/job-unknown")
        self.assertEqual(status, 200, body)
        self.assertIn("状態確認中", body)

        status, body, _ = self.get_status("/jobs/absent")
        self.assertEqual(status, 404, body)

    def test_pinned_target_running_wins_then_latest_config(self):
        self.assertIsNone(data.pinned_target(None))

        pin = data.pinned_target(self.fake)
        self.assertEqual(
            pin["world"],
            {"id": "romance", "name": self.configs.get("cfg-test")["preview"]["world_name"]},
        )
        self.assertIsNone(pin["run"])
        self.assertIsNone(pin["job"])

        self.fake.add(_job("job-ok", "run-c", "succeeded"))
        pin = data.pinned_target(self.fake)
        self.assertEqual(pin["run"], "run-c")
        self.assertIsNone(pin["job"])

        self.fake.add(_job("job-run", "run-a", "running"))
        pin = data.pinned_target(self.fake)
        self.assertEqual(pin["job"]["job_id"], "job-run")
        self.assertEqual(pin["run"], "run-c")  # still the last *finished* run

    def test_pinned_target_world_id_scopes_the_idle_fallback(self):
        # Bug: viewing world "momotaro" (no config of its own here) and
        # stepping into "2 実行" used to land on "romance"'s config just
        # because it was the newest config system-wide. world_id must keep
        # an idle, config-less world from borrowing another world's target.
        self.assertEqual(data.pinned_target(self.fake, world_id="romance")["world"]["id"], "romance")
        self.assertIsNone(data.pinned_target(self.fake, world_id="momotaro"))

        # A job actually running for "romance" is genuine system-wide state
        # (JobStore.submit() allows only one non-terminal job at a time), so
        # it still wins even when scoped to a different, idle world.
        self.fake.add(_job("job-run", "run-a", "running"))
        pin = data.pinned_target(self.fake, world_id="momotaro")
        self.assertEqual(pin["job"]["job_id"], "job-run")

    def test_jobs_page_world_query_does_not_leak_another_worlds_config(self):
        # romance's "cfg-test" ("wb") must not appear as momotaro's target
        # just because it's the only (and thus newest) config system-wide.
        status, body, _ = self.get_status("/jobs?world=momotaro")
        self.assertEqual(status, 200, body)
        self.assertNotIn("target-card", body)
        self.assertNotIn("/configs/cfg-test/start", body)
        self.assertIn('<option value="momotaro" selected>', body)
        # momotaro has no config of its own -> the idle prep card offers
        # "新しく作る" instead of a start form.
        self.assertNotIn('data-wb="start"', body)
        self.assertIn("新しく作る", body)

        status, body, _ = self.get_status("/worlds/momotaro")
        self.assertEqual(status, 200, body)
        band = body[body.index('<nav class="phase-band"'):body.index("</nav>")]
        self.assertIn('href="/jobs?world=momotaro"', band)

    # ----------------------------------------------------- run page (WB-UI-017)

    def test_run_page_idle_shows_prep_and_empty_vessels(self):
        status, body, _ = self.get_status("/jobs?world=romance")
        self.assertEqual(status, 200, body)
        self.assertIn('data-wb="start"', body)
        self.assertIn('<select name="config"', body)
        self.assertIn("この設定で GA を回す", body)
        # romance's QD axes are I/II/III x low/mid/high -- 9 cells, all empty
        # before any run has published.
        self.assertEqual(body.count("qd-cell empty"), 9)
        self.assertEqual(body.count("vbox ghost"), 3)
        self.assertNotIn("<h2>履歴</h2>", body)

    def test_run_page_blocked_by_other_world(self):
        self.configs.save({
            "label": "momo", "project_id": "momotaro", "template_id": "momotaro",
            "evolution": {"generations": 1, "population": 1, "seeds": 1},
        }, config_id="cfg-momo")
        self.fake.add(_job("job-run", "run-a", "running"))  # romance's cfg-test

        status, body, _ = self.get_status("/jobs?world=momotaro")
        self.assertEqual(status, 200, body)
        self.assertNotIn('data-wb="start"', body)
        self.assertIn("実行中です", body)
        self.assertIn("/jobs/job-run", body)

    def test_run_page_running_has_bars_and_cancel(self):
        self.fake.add(_job("job-run", "run-a", "running"))
        status, body, _ = self.get_status("/jobs/job-run")
        self.assertEqual(status, 200, body)
        self.assertIn('data-poll="1"', body)
        self.assertEqual(body.count("<progress"), 3)
        self.assertIn('data-action="cancel"', body)
        self.assertIn("data-revision=", body)

    def test_run_page_done_reads_publication(self):
        run_id = "run-handcrafted-done"
        self._hand_published_run(run_id, "cfg-test", summary={
            "generations": [
                {"occupied_cells": 1, "average_archive_quality": 0.5,
                 "reach_rate": 1.0, "archive_dissimilarity": 0.0},
            ],
        })
        self.fake.add(_job("job-ok2", run_id, "succeeded", publication_revision=1))

        status, body, _ = self.get_status("/jobs/job-ok2")
        self.assertEqual(status, 200, body)
        self.assertGreaterEqual(body.count("qd-cell f"), 1)
        self.assertIn('<b data-field="metric_occupied">1</b>', body)
        self.assertIn('class="exit on"', body)
        self.assertIn('data-revision="1"', body)

    def test_run_page_shows_generation_trend_table(self):
        # WB-LINEAGE-001: reach rate / allies-at-contest / the biggest
        # action-share swings, one row per closed generation.
        run_id = "run-lineage-trend"
        self._hand_published_run(run_id, "cfg-test", summary={
            "generations": [
                {
                    "generation": 0,
                    "occupied_cells": 1, "average_archive_quality": 0.5,
                    "reach_rate": 0.5, "archive_dissimilarity": 0.0,
                    "action_share": {"I/train/none": 0.5},
                    "allies_mean_at_contest": None,
                    "allies_mean_final": 0.5,
                    "contest_rate": 0.0,
                },
                {
                    "generation": 1,
                    "occupied_cells": 1, "average_archive_quality": 0.6,
                    "reach_rate": 1.0, "archive_dissimilarity": 0.0,
                    "action_share": {"I/train/none": 0.9, "II/give_item/ally": 0.75},
                    "allies_mean_at_contest": 1.5,
                    "allies_mean_final": 2.0,
                    "contest_rate": 1.0,
                },
            ],
        })
        self.fake.add(_job("job-trend", run_id, "succeeded", publication_revision=1))

        status, body, _ = self.get_status("/jobs/job-trend")
        self.assertEqual(status, 200, body)
        self.assertIn("世代の推移", body)
        self.assertIn("<td>g0</td>", body)
        self.assertIn("<td>g1</td>", body)
        self.assertIn("50%", body)
        self.assertIn("100%", body)
        self.assertIn("1.5", body)
        # g0 has no previous generation to diff against.
        first_row = body[body.index("<td>g0</td>"):body.index("<td>g1</td>")]
        self.assertIn("<td>—</td>", first_row)
        # g1: give_item moved further (+0.75) than train (+0.4), so it leads.
        second_row = body[body.index("<td>g1</td>"):]
        self.assertIn("譲渡→味方 0%→75%", second_row)
        self.assertIn("訓練 50%→90%", second_row)
        self.assertLess(second_row.index("譲渡"), second_row.index("訓練"))

    def test_run_page_distinguishes_same_verb_across_roles_in_trend_table(self):
        # Review fix: action_share keys only collide on verb once role is
        # dropped -- give_item/ally and give_item/hostile must not render as
        # the same "譲渡" label with contradictory percentages.
        run_id = "run-lineage-role-collision"
        self._hand_published_run(run_id, "cfg-test", summary={
            "generations": [
                {
                    "generation": 0,
                    "occupied_cells": 1, "average_archive_quality": 0.5,
                    "reach_rate": 0.5, "archive_dissimilarity": 0.0,
                    "action_share": {
                        "III/give_item/ally": 0.2,
                        "III/give_item/hostile": 0.6,
                    },
                    "allies_mean_at_contest": None,
                    "allies_mean_final": 0.5,
                    "contest_rate": 0.0,
                },
                {
                    "generation": 1,
                    "occupied_cells": 1, "average_archive_quality": 0.6,
                    "reach_rate": 1.0, "archive_dissimilarity": 0.0,
                    "action_share": {
                        "III/give_item/ally": 0.8,
                        "III/give_item/hostile": 0.1,
                    },
                    "allies_mean_at_contest": 1.5,
                    "allies_mean_final": 2.0,
                    "contest_rate": 1.0,
                },
            ],
        })
        self.fake.add(_job("job-role-collision", run_id, "succeeded", publication_revision=1))

        status, body, _ = self.get_status("/jobs/job-role-collision")
        self.assertEqual(status, 200, body)
        second_row = body[body.index("<td>g1</td>"):]
        self.assertIn("譲渡→味方 20%→80%", second_row)
        self.assertIn("譲渡→敵対相手 60%→10%", second_row)

    def test_run_page_hides_generation_trend_table_for_legacy_summary(self):
        # Backward compat: a summary.json from before WB-LINEAGE-001 has no
        # action_share -- render no heading and no table at all.
        run_id = "run-lineage-legacy"
        self._hand_published_run(run_id, "cfg-test", summary={
            "generations": [
                {"occupied_cells": 1, "average_archive_quality": 0.5,
                 "reach_rate": 1.0, "archive_dissimilarity": 0.0},
            ],
        })
        self.fake.add(_job("job-legacy", run_id, "succeeded", publication_revision=1))

        status, body, _ = self.get_status("/jobs/job-legacy")
        self.assertEqual(status, 200, body)
        self.assertNotIn("世代の推移", body)

    def test_history_page_lists_tables(self):
        status, body, _ = self.get_status("/history")
        self.assertEqual(status, 200, body)
        self.assertIn("<h2>進行中</h2>", body)
        self.assertIn("<h2>履歴</h2>", body)
        self.assertIn("<h2>生成ジョブ</h2>", body)

    def test_home_tabs_follow_pinned_world(self):
        self.fake.add(_job("job-ok", "run-c", "succeeded"))

        # Home has no phase band at all (WB feedback: showing 4 phase tabs on
        # the world-picker lobby invites clicking ahead by mistake) and no
        # header world/run picker either (the body already is the world/genre
        # hub, so a pinned-elsewhere picker up top would be redundant there).
        status, body, _ = self.get_status("/")
        self.assertEqual(status, 200, body)
        self.assertNotIn('<nav class="phase-band"', body)
        self.assertNotIn('data-wb="world-picker"', body)

        for path in ("/configs", "/jobs", "/worlds/romance"):
            status, body, _ = self.get_status(path)
            self.assertEqual(status, 200, body)
            band = body[body.index('<nav class="phase-band"'):body.index("</nav>")]
            self.assertIn('href="/exp/run-c"', band, path)
            self.assertIn('href="/outputs?run=run-c"', band, path)
            self.assertNotIn('href="/selected"', band, path)
            self.assertIn('data-wb="world-picker"', body, path)
            self.assertIn('<option value="romance" selected>', body, path)

        # /selected is a deliberate cross-world view: it must not be pinned.
        status, body, _ = self.get_status("/selected")
        self.assertEqual(status, 200, body)
        band = body[body.index('<nav class="phase-band"'):body.index("</nav>")]
        self.assertIn('href="/selected"', band)

    # ------------------------------------------------------- candidates

    def test_candidates_filter_and_selection(self):
        self._legacy_experiment("exp-cand")
        catalog = self.server.repository.catalog
        rid = catalog.register_legacy("exp-cand")

        status, body, _ = self.get_status(f"/runs/{rid}/candidates")
        self.assertEqual(status, 200, body)
        self.assertEqual(body.count('data-candidate-id="'), 2)
        self.assertIn('data-revision="0"', body)

        status, body, _ = self.get_status(f"/runs/{rid}/candidates?reached=true")
        self.assertEqual(status, 200, body)
        self.assertEqual(body.count('data-candidate-id="'), 1)

        status, body, _ = self.get_status(f"/runs/{rid}/candidates?reached=maybe")
        self.assertEqual(status, 422, body)
        self.assertIn("code", json.loads(body))

        # The filter form (§3.8) always submits every field, blank or not, so
        # an all-blank submission and a one-field submission must both 200.
        blank_query = "generation=&individual_index=&seed=&role=&reached=&availability=&state="
        status, body, _ = self.get_status(f"/runs/{rid}/candidates?{blank_query}")
        self.assertEqual(status, 200, body)
        self.assertEqual(body.count('data-candidate-id="'), 2)

        one_set_query = "generation=&individual_index=&seed=&role=&reached=true&availability=&state="
        status, body, _ = self.get_status(f"/runs/{rid}/candidates?{one_set_query}")
        self.assertEqual(status, 200, body)
        self.assertEqual(body.count('data-candidate-id="'), 1)

        candidates = catalog.candidates(rid)["candidates"]
        cid = next(c["candidate_id"] for c in candidates if c["cell_key"] == "I|low")
        status, payload = self.http(
            "POST", f"/api/runs/{rid}/selection",
            {"expected_revision": 0, "changes": [{"candidate_id": cid, "state": "adopted", "note": "ok"}]},
        )
        self.assertEqual(status, 200, payload)

        status, body, _ = self.get_status(f"/runs/{rid}/candidates")
        self.assertEqual(status, 200, body)
        row = body[body.index(f'data-candidate-id="{cid}"'):]
        self.assertIn('<option value="adopted" selected>', row)

    def test_candidates_sort_quality_desc_and_bogus_sort_ignored(self):
        self._legacy_experiment("exp-sort")
        catalog = self.server.repository.catalog
        rid = catalog.register_legacy("exp-sort")

        status, body, _ = self.get_status(f"/runs/{rid}/candidates?sort=quality&dir=desc")
        self.assertEqual(status, 200, body)
        self.assertIn('aria-sort="descending"', body)
        # VI|high (q=0.7) must render before I|low (q=0.5) in descending order.
        self.assertLess(body.index("VI|high"), body.index("I|low"))
        self.assertLess(body.index("0.7000"), body.index("0.5000"))

        status, asc_body, _ = self.get_status(f"/runs/{rid}/candidates?sort=quality&dir=asc")
        self.assertEqual(status, 200, asc_body)
        self.assertIn('aria-sort="ascending"', asc_body)
        self.assertLess(asc_body.index("I|low"), asc_body.index("VI|high"))

        status, bogus_body, _ = self.get_status(f"/runs/{rid}/candidates?sort=bogus")
        self.assertEqual(status, 200, bogus_body)
        self.assertNotIn("aria-sort", bogus_body)

    def _hand_published_run(self, run_id, config_id, *, cell_key="I|low", summary=None):
        """Build a genuine, non-legacy published run on disk: no GA, no
        subprocess, just the same file/hash shapes execution/evolution_worker.py's
        EvolutionObserver.publish() produces. A register_legacy()'d run's
        history() state is always forced to "legacy" (see run_catalog.py), so
        the running-lock can only ever be observed on a run like this one,
        whose state comes from the (fake) job store instead.
        """
        root = self.runs / run_id
        root.mkdir(parents=True)
        manifest = {"schema_version": 1, "run_id": run_id, "config_id": config_id,
                    "evolution": {}, "target_endings": []}
        manifest_bytes = canonical(manifest)
        write_bytes(root / "manifest.json", manifest_bytes)
        atomic_json(root / "complete.json", {"schema_version": 1, "manifest_sha256": sha256(manifest_bytes)})

        log_hash = hashlib.sha256(b"fake-log").hexdigest()
        layers_path = "g0/ind-0/seed-0/layers.jsonl"
        identity = {"scheme": "recorded-v1", "run_id": run_id, "role": "protagonist",
                    "generation": 0, "individual_index": 0, "seed": 0, "source_log_sha256": log_hash}
        candidate_id = "cand-" + sha256(canonical(identity))
        candidate = {
            "candidate_id": candidate_id, "identity": identity, "role": "protagonist",
            "generation": 0, "individual_index": 0, "seed": 0, "cell_key": cell_key,
            "reached": True, "source_log_sha256": log_hash,
            "log": {"relative_path": layers_path, "availability": "missing", "observed_sha256": None},
            "quality": 0.5, "parents": [], "genome": {},
        }
        payloads = {
            "archive": {"cells": {cell_key: {"generation": 0, "quality": 0.5, "reach_rate": 1.0,
                                              "exemplar": {"seed": 0, "layers_path": layers_path}}}},
            "summary": {} if summary is None else summary,
            "candidates": {"schema_version": 1, "run_id": run_id, "revision": 1, "candidates": [candidate]},
        }
        files = {}
        for name, value in payloads.items():
            data = canonical(value)
            write_bytes(root / "published" / "1" / f"{name}.json", data)
            files[name] = {"path": f"published/1/{name}.json", "sha256": sha256(data)}
        revision_manifest = {"schema_version": 1, "run_id": run_id, "revision": 1,
                              "completed_generations": 1, "files": files}
        revision_manifest_bytes = canonical(revision_manifest)
        write_bytes(root / "published" / "1" / "manifest.json", revision_manifest_bytes)
        atomic_json(root / "published" / "current.json", {
            "schema_version": 1, "run_id": run_id, "revision": 1,
            "manifest_sha256": sha256(revision_manifest_bytes),
        })
        return root

    def test_candidates_running_lock_via_http(self):
        run_id = "run-handcrafted1"
        self._hand_published_run(run_id, "cfg-test")
        self.fake.add(_job("job-running", run_id, "running"))

        status, body, _ = self.get_status(f"/runs/{run_id}/candidates")
        self.assertEqual(status, 200, body)
        self.assertIn("選定は保存できません", body)
        self.assertIn("disabled", body)

    def test_phase_and_error_vocabulary(self):
        for phase, label in workbench_pages.PHASE_LABELS.items():
            with self.subTest(phase=phase):
                jid = f"job-phase-{phase}"
                self.fake.add(_job(jid, f"run-phase-{phase}", "running", phase=phase))
                status, html, _ = self.get_status(f"/jobs/{jid}")
                self.assertEqual(status, 200, html)
                self.assertIn(label, html)
        for code, (message, next_step) in workbench_pages.ERROR_MESSAGES.items():
            with self.subTest(code=code):
                jid = f"job-error-{code}"
                self.fake.add(_job(jid, f"run-error-{code}", "failed", error={"code": code}))
                status, html, _ = self.get_status(f"/jobs/{jid}")
                self.assertEqual(status, 200, html)
                self.assertIn(message, html)
                self.assertIn(next_step, html)

    def test_candidates_running_disables_editing(self):
        candidate = {
            "candidate_id": "cand-x", "generation": 0, "individual_index": 0, "seed": 1,
            "role": "protagonist", "cell_key": "I|low", "reached": True,
            "log": {"availability": "present"}, "screenable": True, "state": "unclassified", "note": "",
        }
        html = workbench_pages.render_candidates_page(
            run_id="run-x", experiment_name="exp-x", config_id=None, revision=1, selection_revision=0,
            candidates=[candidate], representatives=set(), running=True, query={},
        )
        self.assertIn("選定は保存できません", html)
        self.assertIn("disabled", html)

    def test_candidates_table_is_nine_columns_with_detail_rows(self):
        # WB-UI-014 §3.1: 9 list columns (選択/候補ID/セル/q/到達/採用可/選定状態/
        # メモ/操作); 世代/個体/seed/役割/原記録/稿 move into a per-row detail
        # toggle instead of being spread across the table.
        self._legacy_experiment("exp-detail")
        catalog = self.server.repository.catalog
        rid = catalog.register_legacy("exp-detail")

        status, body, _ = self.get_status(f"/runs/{rid}/candidates")
        self.assertEqual(status, 200, body)
        thead = body[body.index("<thead>"):body.index("</thead>")]
        # "<thead>" itself contains "<th", so match the column tag precisely.
        self.assertEqual(len(re.findall(r"<th[ >]", thead)), 9)
        self.assertEqual(body.count('class="detail-row"'), 2)  # one per candidate
        self.assertEqual(body.count('class="row-toggle"'), 2)
        self.assertIn('aria-expanded="false"', body)
        self.assertIn('aria-controls="detail-', body)
        self.assertIn('<details class="glossary">', body)
        self.assertIn('<p class="page-lead">', body)

    def test_configs_and_jobs_pages_have_lead_and_next_cta(self):
        status, body, _ = self.get_status("/configs")
        self.assertEqual(status, 200, body)
        self.assertIn('<p class="page-lead">', body)
        self.assertIn('class="next-cta"', body)

        status, body, _ = self.get_status("/history")
        self.assertEqual(status, 200, body)
        self.assertIn('<p class="page-lead">', body)
        # No records yet -> the empty-jobs CTA from WB-UI-012 §2.3.
        self.assertIn('class="next-cta" href="/configs/new">次: 実行設定を作る →</a>', body)

    def test_raw_log(self):
        self._legacy_experiment("exp-raw")
        catalog = self.server.repository.catalog
        rid = catalog.register_legacy("exp-raw")
        candidates = catalog.candidates(rid)["candidates"]
        cid = next(c["candidate_id"] for c in candidates if c["cell_key"] == "I|low")

        status, body, _ = self.get_status(f"/runs/{rid}/candidates/{cid}/raw")
        self.assertEqual(status, 200, body)
        self.assertIn('id="L1"', body)

        (self.runs / "exp-raw" / "a.jsonl").write_bytes(b"changed")
        status, body, _ = self.get_status(f"/runs/{rid}/candidates/{cid}/raw")
        self.assertEqual(status, 404, body)

    def test_tray(self):
        self._legacy_experiment("exp-tray")
        catalog = self.server.repository.catalog
        rid = catalog.register_legacy("exp-tray")
        candidates = catalog.candidates(rid)["candidates"]
        cid = next(c["candidate_id"] for c in candidates if c["cell_key"] == "I|low")
        status, payload = self.http(
            "POST", f"/api/runs/{rid}/selection",
            {"expected_revision": 0, "changes": [{"candidate_id": cid, "state": "adopted"}]},
        )
        self.assertEqual(status, 200, payload)

        status, body, _ = self.get_status("/selected")
        self.assertEqual(status, 200, body)
        self.assertIn(f'data-candidate-id="{cid}"', body)
        row = body[body.index(f'data-candidate-id="{cid}"'):]
        self.assertIn("data-revision=", row)
        self.assertIn("外す", row)

    # -------------------------------------------------------------- CSP

    def test_csp_and_static_asset(self):
        self.fake.add(_job("job-x", "run-x", "running"))
        self._legacy_experiment("exp-csp")
        rid = self.server.repository.catalog.register_legacy("exp-csp")
        paths = [
            "/", "/configs", "/configs/new", "/configs/new?from=cfg-test", "/configs/cfg-test",
            "/history", "/jobs", "/jobs/job-x", "/selected",
            f"/runs/{rid}/candidates",
        ]
        for path in paths:
            with self.subTest(path=path):
                status, body, _ = self.get_status(path)
                self.assertEqual(status, 200, body)
                self.assertNotIn("onclick=", body)
                self.assertNotIn("onsubmit=", body)
                self.assertNotIn("onchange=", body)
                for tag in re.findall(r"<script[^>]*>", body):
                    self.assertIn("src=", tag)

        status, body, headers = self.get_status("/static/workbench.js")
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", headers.get("Content-Type", ""))

    # --------------------------------------------------------- vocabulary

    def test_cancelled_job_without_error_is_not_an_error(self):
        """UI-009 finding: a user-requested stop must not render "エラー: None"."""
        base = {"job_id": "job-stop", "phase": "evaluating", "error": None, "progress": {},
                "config_id": "cfg-test", "run_id": "run-stop", "created_at": 1.0, "started_at": 2.0, "finished_at": 5.0}

        self.fake.add({**base, "state": "cancelled"})
        status, html, _ = self.get_status("/jobs/job-stop")
        self.assertEqual(status, 200, html)
        self.assertNotIn("エラー: None", html)
        self.assertIn("利用者の停止要求により停止しました", html)
        self.assertIn("同じ設定で新しく実行", html)

        self.fake.add({**base, "job_id": "job-stop2", "run_id": "run-stop2", "state": "interrupted"})
        status, html, _ = self.get_status("/jobs/job-stop2")
        self.assertEqual(status, 200, html)
        self.assertNotIn("エラー: None", html)
        self.assertIn("エラー情報がありません", html)

        self.fake.add({**base, "job_id": "job-stop3", "run_id": "run-stop3",
                        "state": "failed", "error": {"code": "wall_timeout"}})
        status, html, _ = self.get_status("/jobs/job-stop3")
        self.assertEqual(status, 200, html)
        self.assertIn(workbench_pages.ERROR_MESSAGES["wall_timeout"][0], html)

    def test_job_page_has_eta_placeholder(self):
        self.fake.add(_job("job-eta", "run-eta", "running"))
        status, body, _ = self.get_status("/jobs/job-eta")
        self.assertEqual(status, 200, body)
        self.assertIn('<span data-field="eta">—</span>', body)
        self.assertIn('data-field="updated-at"', body)
        self.assertIn('data-field="delta"', body)

    def test_state_vocabulary(self):
        for state, label in workbench_pages.STATE_LABELS.items():
            with self.subTest(state=state):
                self.assertIn(label, workbench_pages.state_badge(state))

    def test_sort_candidates_two_pass_stable_sort(self):
        # Equal values arrive in reverse id order so the id tiebreak is exercised.
        candidates = [
            {"candidate_id": "cand-c", "quality": 0.5},
            {"candidate_id": "cand-a", "quality": 0.9},
            {"candidate_id": "cand-d", "quality": None},
            {"candidate_id": "cand-b", "quality": 0.5},
        ]
        asc = workbench_pages.sort_candidates(candidates, "quality", "asc", None)
        self.assertEqual(
            [c["candidate_id"] for c in asc], ["cand-b", "cand-c", "cand-a", "cand-d"]
        )
        desc = workbench_pages.sort_candidates(candidates, "quality", "desc", None)
        self.assertEqual(
            [c["candidate_id"] for c in desc], ["cand-a", "cand-b", "cand-c", "cand-d"]
        )


if __name__ == "__main__":
    unittest.main()
