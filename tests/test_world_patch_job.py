"""World-patch job admission, frozen argv, and the real Windows job pipeline
(WB-WORLDGROW-001, stage 3b-3): execution/world_patch_job.py's
normalize/admit/prepare, execution/jobs.py's third `kind`, and
POST /api/runs/<rid>/world-patch. No real LLM is ever called -- a disposable
copy of gapengine/synopsis.py's generate_text stands in for the backend, the
same technique tests/test_output_jobs.py uses for execution/generation.py.
"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from execution.configs import ConfigStore
from execution.jobs import JobStore
from execution.provenance import ConfigError
from execution import world_patch_job
from execution.worker import TERMINAL
from viewer.data import RunRepository
from viewer.server import ViewerServer, ViewerHandler

from world_patch_fixtures import frozen_experiment, write_approved
from test_world_patch_cli import COLLIDING_ADD, VALID_ADD

ROOT = Path(__file__).resolve().parents[1]


def _write_world_demand(experiment):
    """Two triggers: a non-investigate one at raw index 0 (must never be
    proposable) and the investigate one at raw index 1 -- so a request's raw
    `trigger` (the UI's data-trigger numbering) only resolves correctly if
    execution/world_patch_job.py's own conversion to scripts/world_patch.py's
    investigate-only numbering (prepare()'s _investigate_index) is exercised."""
    payload = {
        "schema_version": 1, "files": 1, "skipped_paths": 0, "subject_decisions": 50,
        "triggers": [
            {"zone": "海", "verb": "give_item", "count": 20, "whiffs": 20, "whiff_rate": 1.0,
             "wasted_share": 0.1, "zone_dwell_share": 0.2},
            {"zone": "海", "verb": "investigate", "count": 30, "whiffs": 30, "whiff_rate": 1.0,
             "wasted_share": 0.3, "zone_dwell_share": 0.5},
        ],
        "zones": [{"zone": "海", "decisions": 50, "dwell": 50, "dwell_share": 0.7,
                    "verbs": [["investigate", 30, 1.0], ["give_item", 20, 1.0]],
                    "repeat_rate": 0.0, "ineffective_rate": 1.0, "ineffective_reasons": [],
                    "mean_p_prec": None, "mean_m_nov": None, "mean_candidates": None}],
        "archive": None,
        "thresholds": {"whiff_rate_min": 0.5, "wasted_share_min": 0.02, "whiffs_min": 10},
        "verb_counts": {"海": {"investigate": [30, 30], "give_item": [20, 20]}},
    }
    (experiment / "world_demand.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@unittest.skipUnless(os.name == "nt", "Windows process supervision")
class WorldPatchJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="wb-worldpatch-job-")
        cls.root = Path(cls._tmp.name)
        # Heavy (a real, tiny GA run) -- built once for the whole class.
        cls.experiment, cls.project, cls.template = frozen_experiment(cls.root)
        _write_world_demand(cls.experiment)
        cls.repo, cls.control, cls.runs = cls.root / "repo", cls.root / "control", cls.root / "runs"
        # Shrink the trial (5x8 default -> 5x4) in the *cloned* repo only,
        # the same budget tests/test_world_patch_cli.py's --from-file tests
        # use to keep the gate's real GA trial fast.
        wpj_path = cls.repo / "execution" / "world_patch_job.py"
        wpj_path.write_text(
            wpj_path.read_text(encoding="utf-8") +
            '\nTRIAL_ARGS = ("--max-runs", "5", "--seeds-per-run", "4")\n',
            encoding="utf-8")
        cls.settings_path = cls.root / "settings.json"
        cls.settings_path.write_text(json.dumps({"output": {"default_backend": "codex-cli",
            "codex-cli": {"limits": {"wall_seconds": 600}}}}), encoding="utf-8")
        cls.settings_none = cls.root / "settings-none.json"
        cls.settings_none.write_text(json.dumps({"output": {"default_backend": "none"}}), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.configs = ConfigStore(self.repo, self.control, self.runs)
        self.jobs = JobStore(self.configs, cancel_grace_seconds=0.3)
        self._stub_propose(VALID_ADD)

    def tearDown(self):
        # Leave the shared control/jobs directory idle for the next test --
        # a lingering non-terminal job would trip the "one job at a time"
        # conflict for every test after it.
        if self.jobs.root.exists():
            for folder in list(self.jobs.root.glob("job-*")):
                try:
                    job = self.jobs.get(folder.name)
                except ConfigError:
                    continue
                if job["state"] not in TERMINAL:
                    self.jobs.cancel(folder.name)
                    self._wait(folder.name, TERMINAL, timeout=60)

    # -- fixtures --------------------------------------------------------

    def _stub_propose(self, add, *, title="海辺の船大工小屋",
                       rationale="海でのinvestigateが空振りし続けている"):
        """Rewrite the cloned repo's gapengine/synopsis.py (not this
        checkout's) so scripts/world_patch.py's real subprocess gets a fixed
        proposal instead of calling a real backend."""
        source = (ROOT / "gapengine" / "synopsis.py").read_text(encoding="utf-8")
        payload = json.dumps({"title": title, "rationale": rationale, "add": add}, ensure_ascii=False)
        source += ("\n\ndef generate_text(backend, prompt, *, settings_path=None, timeout=600):\n"
                   f"    return GenerationResult(status='ok', text={payload!r})\n")
        (self.repo / "gapengine" / "synopsis.py").write_text(source, encoding="utf-8")

    def _stub_propose_slow(self, add, *, sleep_seconds, title="海辺の船大工小屋",
                            rationale="海でのinvestigateが空振りし続けている"):
        """Same as _stub_propose, but generate_text sleeps first -- long
        enough that a test can observe progress.step=='generate' and cancel
        while the child is still inside it."""
        source = (ROOT / "gapengine" / "synopsis.py").read_text(encoding="utf-8")
        payload = json.dumps({"title": title, "rationale": rationale, "add": add}, ensure_ascii=False)
        source += ("\n\nimport time as _wb_test_time\n"
                   f"def generate_text(backend, prompt, *, settings_path=None, timeout=600):\n"
                   f"    _wb_test_time.sleep({sleep_seconds!r})\n"
                   f"    return GenerationResult(status='ok', text={payload!r})\n")
        (self.repo / "gapengine" / "synopsis.py").write_text(source, encoding="utf-8")

    def _stub_propose_cwd_marker(self, add, marker_path):
        """generate_text records the child's own os.getcwd() to an absolute,
        cwd-independent marker path -- the only way to observe the actual
        subprocess's working directory from the test process."""
        source = (ROOT / "gapengine" / "synopsis.py").read_text(encoding="utf-8")
        payload = json.dumps({"title": "cwdチェック", "rationale": "作業ディレクトリの確認用",
                              "add": add}, ensure_ascii=False)
        source += ("\n\nimport os as _wb_test_os\n"
                   f"def generate_text(backend, prompt, *, settings_path=None, timeout=600):\n"
                   f"    with open({str(marker_path)!r}, 'w', encoding='utf-8') as _wb_marker:\n"
                   f"        _wb_marker.write(_wb_test_os.getcwd())\n"
                   f"    return GenerationResult(status='ok', text={payload!r})\n")
        (self.repo / "gapengine" / "synopsis.py").write_text(source, encoding="utf-8")

    def _propose_request(self, rid, *, trigger=1):
        return {"schema_version": 1, "request_id": rid, "kind": "world_patch", "action": "propose",
                "config_id": "cfg-fixture", "run_id": "run-fixture", "trigger": trigger}

    def _check_request(self, rid, patch_id):
        return {"schema_version": 1, "request_id": rid, "kind": "world_patch", "action": "check",
                "config_id": "cfg-fixture", "run_id": "run-fixture", "patch_id": patch_id}

    def _wait(self, jid, states, *, timeout=180):
        deadline = time.monotonic() + timeout
        job = None
        while time.monotonic() < deadline:
            job = self.jobs.get(jid)
            if job["state"] in states:
                return job
            time.sleep(0.2)
        raise AssertionError(f"{jid} did not reach {states} in {timeout}s: {job}")

    def _submit_and_wait(self, request, *, timeout=180):
        with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
            job, created = self.jobs.submit(request, settings_path=self.settings_path)
        self.assertTrue(created, job)
        self.assertEqual(job["kind"], "world_patch")
        self.assertNotIn("output_id", job)
        return self._wait(job["job_id"], TERMINAL, timeout=timeout)

    def _cancel_and_wait(self, jid, *, timeout=90):
        self.jobs.cancel(jid)
        return self._wait(jid, TERMINAL, timeout=timeout)

    @contextmanager
    def _server(self):
        server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        server.job_store = self.jobs
        server.settings_path = self.settings_path
        server.repository = RunRepository(self.runs, control_root=self.control, jobs=self.jobs)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02})
        thread.start()
        try:
            yield server
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)

    def _http(self, server, method, path, body=None, *, client=True):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if client:
            headers["X-WorldBloom-Client"] = "1"
        try:
            connection.request(method, path, None if body is None else json.dumps(body), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    # -- 1/4: propose succeeds with a holdout gate; check re-gates it -----

    def test_propose_then_check_succeed_with_holdout_gate_and_progress(self):
        propose_job = self._submit_and_wait(self._propose_request("req-propose-1"))
        self.assertEqual(propose_job["state"], "succeeded", propose_job)
        self.assertEqual(propose_job["progress"]["step"], "done")
        self.assertEqual(propose_job["progress"]["status"], "reviewable")
        patch_id = propose_job["progress"]["patch_id"]

        proposed_dir = self.project / "patches" / "_proposed"
        # Other test methods share this project and leave their own
        # (unapproved, so harmless) proposals in _proposed/ -- only assert
        # this run's own patch landed there, not that it's the only one.
        self.assertIn(patch_id, [p.stem for p in proposed_dir.glob("*.yaml")])
        gate_path = proposed_dir / f"{patch_id}.gate.json"
        before = json.loads(gate_path.read_text(encoding="utf-8"))
        self.assertEqual(before["status"], "reviewable")
        self.assertEqual(before["trial"]["evidence"]["seed_set"], "holdout")
        self.assertEqual(before["holdout_checks"], 1)

        check_job = self._submit_and_wait(self._check_request("req-check-1", patch_id))
        self.assertEqual(check_job["state"], "succeeded", check_job)
        self.assertEqual(check_job["progress"]["step"], "done")
        self.assertEqual(check_job["progress"]["patch_id"], patch_id)
        after = json.loads(gate_path.read_text(encoding="utf-8"))
        self.assertEqual(after["trial"]["evidence"]["seed_set"], "holdout")
        self.assertEqual(after["holdout_checks"], 2)

    # -- 5: a static-gate violation still completes the job (succeeded) ---

    def test_propose_job_succeeds_even_when_static_gate_rejects_the_proposal(self):
        self._stub_propose(COLLIDING_ADD)
        job = self._submit_and_wait(self._propose_request("req-static-failed"), timeout=60)
        self.assertEqual(job["state"], "succeeded", job)
        self.assertEqual(job["progress"]["step"], "done")
        self.assertEqual(job["progress"]["status"], "static_failed")
        patch_id = job["progress"]["patch_id"]
        gate_path = self.project / "patches" / "_proposed" / f"{patch_id}.gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "static_failed")
        self.assertTrue(gate["static"]["violations"])
        self.assertIsNone(gate["trial"])  # never spent trial seeds on a rejected proposal
        # The worker discards the child's stdout, so the CLI keeps its own copy:
        # the only place the reason a proposal was sent back can be read later.
        log = self.control / "jobs" / job["job_id"] / "scratch" / "world_patch.log"
        self.assertTrue(log.is_file(), log)
        self.assertIn("static_failed", log.read_text(encoding="utf-8"))

    # -- 2: normalize() rejections ----------------------------------------

    def test_normalize_rejects_malformed_requests(self):
        base = self._propose_request("req-n1")
        with self.subTest("extra key"):
            with self.assertRaises(ConfigError):
                world_patch_job.normalize({**base, "extra": 1})
        with self.subTest("bool trigger"):
            with self.assertRaises(ConfigError):
                world_patch_job.normalize({**base, "trigger": True})
        with self.subTest("negative trigger"):
            with self.assertRaises(ConfigError):
                world_patch_job.normalize({**base, "trigger": -1})
        with self.subTest("bad patch_id"):
            check_req = self._check_request("req-n2", "BAD_ID")
            with self.assertRaises(ConfigError):
                world_patch_job.normalize(check_req)
        with self.subTest("unknown action"):
            with self.assertRaises(ConfigError):
                world_patch_job.normalize({**base, "action": "delete"})
        with self.subTest("propose body on a check-only field set"):
            with self.assertRaises(ConfigError):
                world_patch_job.normalize({**self._check_request("req-n3", "abc"), "trigger": 0})

    # -- A4: schema_version must be exactly the int 1 -----------------------

    def test_normalize_rejects_wrong_typed_schema_version(self):
        base = self._propose_request("req-schema")
        # Observed against the current implementation (request.get("schema_
        # version", 1) != 1 or type(...) is not int): a string "1" fails the
        # != check (a str never equals an int); 2 fails the != check; 1.0
        # equals 1 by value but fails the type check; True equals 1 by value
        # (bool <: int) but type(True) is bool, not int, so it also fails
        # the type check. All four are already rejected -- no code change
        # needed for this to hold.
        for label, value in (("string \"1\"", "1"), ("wrong int 2", 2), ("float 1.0", 1.0), ("bool True", True)):
            with self.subTest(label):
                with self.assertRaises(ConfigError):
                    world_patch_job.normalize({**base, "schema_version": value})

    # -- A3: raw-to-investigate index conversion -----------------------------

    def test_investigate_index_skips_intervening_non_investigate_triggers(self):
        triggers = [{"verb": "observe"}, {"verb": "investigate"}, {"verb": "move"}, {"verb": "investigate"}]
        self.assertEqual(world_patch_job._investigate_index(triggers, 1), 0)
        self.assertEqual(world_patch_job._investigate_index(triggers, 3), 1)

    # -- 3: admit() rejections ---------------------------------------------

    def test_admit_rejects_non_investigate_and_out_of_range_trigger(self):
        with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
            for label, trigger in (("non-investigate", 0), ("out of range", 99)):
                with self.subTest(label):
                    req = world_patch_job.normalize(self._propose_request(f"req-trig-{trigger}", trigger=trigger))
                    with self.assertRaises(ConfigError) as cm:
                        world_patch_job.admit(self.jobs, req, settings_path=self.settings_path)
                    self.assertIn("trigger", cm.exception.field_errors)

    def test_admit_rejects_none_backend(self):
        req = world_patch_job.normalize(self._propose_request("req-backend-none"))
        with self.assertRaises(ConfigError) as cm:
            world_patch_job.admit(self.jobs, req, settings_path=self.settings_none)
        self.assertIn("backend", cm.exception.field_errors)
        self.assertNotEqual(cm.exception.code, "unavailable")  # explicit 422, not a probe failure

    def test_admit_rejects_missing_run(self):
        req = world_patch_job.normalize(self._propose_request("req-missing-run"))
        req["run_id"] = "run-does-not-exist"
        with self.assertRaises(ConfigError) as cm:
            world_patch_job.admit(self.jobs, req, settings_path=self.settings_path)
        self.assertEqual(cm.exception.code, "not_found")

    def test_admit_rejects_version_mismatch(self):
        write_approved(self.project, {"title": "conflict-only",
            "add": {"zones": [], "items": [], "facts": [], "daily_events": []}})
        try:
            req = world_patch_job.normalize(self._propose_request("req-version-mismatch"))
            with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
                with self.assertRaises(ConfigError) as cm:
                    world_patch_job.admit(self.jobs, req, settings_path=self.settings_path)
            self.assertEqual(cm.exception.code, "conflict")
        finally:
            shutil.rmtree(self.project / "patches", ignore_errors=True)

    # -- A2: a broken world_demand.json is a ConfigError, never AttributeError etc.

    def test_admit_rejects_broken_world_demand_json(self):
        original = (self.experiment / "world_demand.json").read_text(encoding="utf-8")
        broken_cases = [
            ("not json at all", "{{{not json"),
            ("root is a list", "[]"),
            ("root is a string", '"hello"'),
            ("triggers is a string", '{"triggers": "nope"}'),
            ("trigger elements are strings", '{"triggers": ["x", "y"]}'),
        ]
        try:
            with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
                for i, (label, content) in enumerate(broken_cases):
                    with self.subTest(label):
                        (self.experiment / "world_demand.json").write_text(content, encoding="utf-8")
                        req = world_patch_job.normalize(self._propose_request(f"req-broken-{i}"))
                        # assertRaises(ConfigError) itself is the assertion that
                        # admit() never lets an AttributeError/TypeError/etc.
                        # escape instead -- such an error would fail this
                        # assertion with an unhandled-exception error, not a
                        # plain test failure.
                        with self.assertRaises(ConfigError):
                            world_patch_job.admit(self.jobs, req, settings_path=self.settings_path)
        finally:
            (self.experiment / "world_demand.json").write_text(original, encoding="utf-8")

    def test_broken_world_demand_json_returns_4xx_over_http_without_dropping_connection(self):
        original = (self.experiment / "world_demand.json").read_text(encoding="utf-8")
        try:
            with self._server() as server:
                with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
                    for i, content in enumerate(("[]", '{"triggers": ["x"]}')):
                        with self.subTest(content):
                            (self.experiment / "world_demand.json").write_text(content, encoding="utf-8")
                            status, payload = self._http(server, "POST", "/api/runs/run-fixture/world-patch",
                                {"action": "propose", "request_id": f"req-broken-http-{i}", "trigger": 1})
                            self.assertEqual(status, 422, payload)
        finally:
            (self.experiment / "world_demand.json").write_text(original, encoding="utf-8")

    # -- A6: wall_seconds is floored to MIN_WALL_SECONDS ---------------------

    def test_admit_enforces_minimum_wall_seconds(self):
        low = self.root / "settings-wall-low.json"
        low.write_text(json.dumps({"output": {"default_backend": "codex-cli",
            "codex-cli": {"limits": {"wall_seconds": 240}}}}), encoding="utf-8")
        high = self.root / "settings-wall-high.json"
        high.write_text(json.dumps({"output": {"default_backend": "codex-cli",
            "codex-cli": {"limits": {"wall_seconds": 6000}}}}), encoding="utf-8")
        with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
            result_low = world_patch_job.admit(self.jobs,
                world_patch_job.normalize(self._propose_request("req-wall-low")), settings_path=low)
            result_high = world_patch_job.admit(self.jobs,
                world_patch_job.normalize(self._propose_request("req-wall-high")), settings_path=high)
        self.assertEqual(result_low["wall_seconds"], 4500)
        self.assertEqual(result_high["wall_seconds"], 6000)

        # Integration: JobStore.submit() actually writes admit()'s
        # wall_seconds into job.json -- it isn't a PUBLIC_FIELDS entry, so
        # read the ledger file directly instead of jobs.get()'s public view.
        with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
            job, created = self.jobs.submit(self._propose_request("req-wall-wired"), settings_path=low)
        self.assertTrue(created, job)
        try:
            raw = json.loads((self.jobs._folder(job["job_id"]) / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["wall_seconds"], 4500)
        finally:
            self._cancel_and_wait(job["job_id"])

    # -- A5: the check action's own validation over HTTP --------------------

    def test_http_check_action_validates_patch_id_extra_key_and_missing_patch(self):
        with self._server() as server:
            with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
                status, bad_id = self._http(server, "POST", "/api/runs/run-fixture/world-patch",
                    {"action": "check", "request_id": "req-check-bad-id", "patch_id": "BAD_ID"})
                self.assertEqual(status, 422, bad_id)  # malformed patch_id -> normalize() rejects it

                status, extra = self._http(server, "POST", "/api/runs/run-fixture/world-patch",
                    {"action": "check", "request_id": "req-check-extra", "patch_id": "p-00000001", "trigger": 1})
                self.assertEqual(status, 400, extra)  # extra key caught by the route itself

                status, missing = self._http(server, "POST", "/api/runs/run-fixture/world-patch",
                    {"action": "check", "request_id": "req-check-missing", "patch_id": "p-00000001"})
                self.assertEqual(status, 404, missing)  # no such proposal on disk

    # -- 7: POST /api/runs/<rid>/world-patch: 202 new, 200 resend, 400/403/409

    def test_http_route_202_on_new_job_200_on_resend_400_403_409(self):
        # COLLIDING_ADD, not VALID_ADD: this test only cares about HTTP
        # status codes, and patch_id_for() is content-hashed -- a real,
        # reviewable VALID_ADD proposal would land on the exact same patch_id
        # (and increment the exact same gate.json's holdout_checks) as
        # test_propose_then_check_succeed_with_holdout_gate_and_progress,
        # corrupting that test's holdout_checks==1 assertion. A static-gate
        # rejection is also much cheaper (no trial).
        self._stub_propose(COLLIDING_ADD)
        with self._server() as server:
            job_id = None
            try:
                with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
                    status, denied = self._http(server, "POST", "/api/runs/run-fixture/world-patch",
                        {"action": "propose", "request_id": "req-http-403", "trigger": 1}, client=False)
                    self.assertEqual(status, 403, denied)

                    status, bad = self._http(server, "POST", "/api/runs/run-fixture/world-patch",
                        {"action": "propose", "request_id": "req-http-400", "trigger": 1, "extra": 1})
                    self.assertEqual(status, 400, bad)

                    new_body = {"action": "propose", "request_id": "req-http-200", "trigger": 1}
                    status, created = self._http(server, "POST", "/api/runs/run-fixture/world-patch", new_body)
                    self.assertEqual(status, 202, created)  # a genuinely new job just started
                    self.assertEqual(created["kind"], "world_patch")
                    self.assertEqual(created["run_id"], "run-fixture")
                    job_id = created["job_id"]

                    # The exact same request_id with the exact same body resends
                    # idempotently -- same job_id back, 200 (not 202: nothing new
                    # started), the same way POST /api/jobs behaves.
                    status, resent = self._http(server, "POST", "/api/runs/run-fixture/world-patch", new_body)
                    self.assertEqual(status, 200, resent)
                    self.assertEqual(resent["job_id"], job_id)

                    status, conflict = self._http(server, "POST", "/api/runs/run-fixture/world-patch",
                        {"action": "propose", "request_id": "req-http-409", "trigger": 1})
                    self.assertEqual(status, 409, conflict)
            finally:
                if job_id is not None:
                    self._cancel_and_wait(job_id)

    # -- A8: the child's cwd is the job's own scratch folder -----------------

    def test_child_cwd_is_the_jobs_scratch_folder(self):
        marker = self.control / "cwd-marker.txt"
        marker.unlink(missing_ok=True)
        # COLLIDING_ADD: generate_text (where the marker is written) runs
        # regardless of what it returns, and a static-gate rejection is much
        # cheaper (no trial) -- see the comment in
        # test_http_route_202_on_new_job_200_on_resend_400_403_409 for why
        # this must not be VALID_ADD (shared patch_id/gate.json).
        self._stub_propose_cwd_marker(COLLIDING_ADD, marker)
        try:
            job = self._submit_and_wait(self._propose_request("req-cwd-check"), timeout=60)
            self.assertEqual(job["state"], "succeeded", job)
            self.assertTrue(marker.is_file())
            cwd = Path(marker.read_text(encoding="utf-8"))
            self.assertEqual(cwd, self.jobs._folder(job["job_id"]) / "scratch")
        finally:
            marker.unlink(missing_ok=True)
            self._stub_propose(VALID_ADD)

    # -- A7: cancel mid-generation terminates the child and frees the lock --

    def test_cancel_during_generate_terminates_child_and_frees_the_patch_lock(self):
        self._stub_propose_slow(VALID_ADD, sleep_seconds=45)
        jid = None
        try:
            with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
                job, created = self.jobs.submit(self._propose_request("req-cancel-1"), settings_path=self.settings_path)
            self.assertTrue(created, job)
            jid = job["job_id"]

            deadline = time.monotonic() + 60
            seen_step = None
            current = None
            while time.monotonic() < deadline:
                current = self.jobs.get(jid)
                seen_step = (current.get("progress") or {}).get("step")
                if seen_step == "generate" or current["state"] in TERMINAL:
                    break
                time.sleep(0.2)
            self.assertEqual(seen_step, "generate", current)

            final = self._cancel_and_wait(jid, timeout=90)
            self.assertEqual(final["state"], "cancelled", final)
        finally:
            if jid is not None:
                # cleanup_jobs' own tearDown re-check is a no-op once this
                # already reached TERMINAL, but cancel again is harmless if
                # the assertion above failed before reaching that point.
                try:
                    self._cancel_and_wait(jid, timeout=90)
                except AssertionError:
                    pass
            # COLLIDING_ADD, not VALID_ADD: cheaper (no trial), and avoids
            # sharing a patch_id/gate.json with
            # test_propose_then_check_succeed_with_holdout_gate_and_progress.
            self._stub_propose(COLLIDING_ADD)

        # patches/.write.lock (and any other patch_lock()) must not be left
        # held by the killed child -- a fresh propose must still complete.
        retry = self._submit_and_wait(self._propose_request("req-cancel-retry"))
        self.assertEqual(retry["state"], "succeeded", retry)


if __name__ == "__main__":
    unittest.main()
