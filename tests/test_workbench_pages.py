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
from viewer import workbench_pages
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
            "generation": {"backend": "none"},
            "evolution": {"generations": 1, "population": 1, "seeds": 1},
        }, config_id="cfg-test")
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
        server.settings_path = ROOT / "settings.json"
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
        for href, label in (("/", "実験一覧"), ("/configs", "設定"),
                             ("/jobs", "実行履歴"), ("/selected", "選定トレイ")):
            with self.subTest(href=href):
                self.assertIn(f'<a href="{href}">{label}</a>', body)
                self.assertIn(f'<a href="{href}">{label}</a>', configs_body)

        plain = self._start_server()
        for path in ("/configs", "/jobs", "/selected"):
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

        status, body, _ = self.get_status("/configs/cfg-test")
        self.assertEqual(status, 200, body)
        self.assertIn("予定評価数", body)
        self.assertIn("編集不可", body)
        self.assertIn("prompt_only", body)

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
            "execution_limits.wall_seconds", "generation.backend", "generation.model",
            "generation.limits.max_calls", "generation.limits.call_timeout_seconds",
            "generation.limits.wall_seconds", "generation.limits.max_saved_response_bytes",
        ):
            with self.subTest(field=field):
                self.assertIn(f'data-field="{field}"', body)
                self.assertIn(f'data-error-for="{field}"', body)
        self.assertIn('<option value="romance">romance</option>', body)

        status, body, _ = self.get_status("/configs/new?from=cfg-test")
        self.assertEqual(status, 200, body)
        self.assertIn('data-parent="cfg-test"', body)
        self.assertGreaterEqual(body.count('type="hidden"'), 2)

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

    def test_start_confirmation(self):
        status, first, _ = self.get_status("/configs/cfg-test/start")
        self.assertEqual(status, 200, first)
        status, second, _ = self.get_status("/configs/cfg-test/start")
        self.assertEqual(status, 200, second)
        pattern = re.compile(r'data-request-id="([A-Za-z0-9][A-Za-z0-9_-]{0,95})"')
        first_id = pattern.search(first).group(1)
        second_id = pattern.search(second).group(1)
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.fake.submitted, [])

    # ------------------------------------------------------------- jobs

    def test_jobs_list_and_detail(self):
        self.fake.add(_job("job-run", "run-a", "running"))
        self.fake.add(_job("job-fail", "run-b", "failed", error={"code": "worker_disappeared"}))
        self.fake.add(_job("job-ok", "run-c", "succeeded", publication_revision=1))
        self.fake.add(_job("job-unknown", "run-d", "running", reconciliation="unknown"))

        status, body, _ = self.get_status("/jobs")
        self.assertEqual(status, 200, body)
        running_at = body.index("<h2>進行中</h2>")
        history_at = body.index("<h2>履歴</h2>")
        self.assertGreater(history_at, running_at)
        self.assertLess(body.index("run-a"), history_at)
        self.assertGreater(body.index("run-b"), history_at)
        self.assertGreater(body.index("run-c"), history_at)
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
        self.assertIn("/configs/cfg-test/start", body)

        status, body, _ = self.get_status("/jobs/job-ok")
        self.assertEqual(status, 200, body)
        self.assertIn("確定結果を見る", body)
        self.assertIn("/exp/run-c", body)
        self.assertIn("/runs/run-c/candidates", body)

        status, body, _ = self.get_status("/jobs/job-unknown")
        self.assertEqual(status, 200, body)
        self.assertIn("状態確認中", body)

        status, body, _ = self.get_status("/jobs/absent")
        self.assertEqual(status, 404, body)

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

    def _hand_published_run(self, run_id, config_id, *, cell_key="I|low"):
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
            "summary": {},
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
                job = _job("job-phase", "run-phase", "running", phase=phase)
                html = workbench_pages.render_job_page(job)
                self.assertIn(label, html)
        for code, (message, next_step) in workbench_pages.ERROR_MESSAGES.items():
            with self.subTest(code=code):
                job = _job("job-error", "run-error", "failed", error={"code": code})
                html = workbench_pages.render_job_page(job)
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
            "/configs/cfg-test/start", "/jobs", "/jobs/job-x", "/selected",
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
        html = workbench_pages.render_job_page({**base, "state": "cancelled"})
        self.assertNotIn("エラー: None", html)
        self.assertIn("利用者の停止要求により停止しました", html)
        self.assertIn("同じ設定で新しく実行", html)
        html = workbench_pages.render_job_page({**base, "state": "interrupted"})
        self.assertNotIn("エラー: None", html)
        self.assertIn("エラー情報がありません", html)
        html = workbench_pages.render_job_page({**base, "state": "failed", "error": {"code": "wall_timeout"}})
        self.assertIn(workbench_pages.ERROR_MESSAGES["wall_timeout"][0], html)

    def test_state_vocabulary(self):
        for state, label in workbench_pages.STATE_LABELS.items():
            with self.subTest(state=state):
                self.assertIn(label, workbench_pages.state_badge(state))


if __name__ == "__main__":
    unittest.main()
