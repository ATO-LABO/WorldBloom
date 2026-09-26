"""POST /api/worlds/<id>/patches/... (WB-WORLDGROW-001 段階3b-2): the boundary
(job_api.boundary / X-WorldBloom-Client) is exercised the way
tests/test_library.py's LibraryHttpBoundaryTests does; a genuinely reviewable
proposal is staged against the shared frozen momotaro fixture the way
tests/test_world_patch_r2.py's FrozenReplay._reviewable_patch does."""
import hashlib
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import yaml

from execution.world_patches import patch_lock
from gapengine.world_patch import EMPTY_STACK_DIGEST, patch_id_for, read_stack
from viewer.data import RunRepository
from viewer.server import ViewerHandler, ViewerServer
from world_patch_fixtures import frozen_experiment, write_approved


class _FakeConfigs:
    def __init__(self, repo):
        self.repo = repo


class _FakeJobStore:
    """Just enough of execution.jobs.JobStore for the boundary/dispatch code
    this API uses: `.configs.repo` and `.list()` (non-terminal-job check)."""

    def __init__(self, repo, jobs=()):
        self.configs = _FakeConfigs(repo)
        self._jobs = list(jobs)

    def list(self):
        return list(self._jobs)


class WorldExpansionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Expensive (runs a real, tiny GA) -- built once and reused read-only
        # by every test method, the same way FrozenReplay does in
        # tests/test_world_patch_r2.py.
        cls.temp = tempfile.TemporaryDirectory(prefix="wb-world-expansion-api-shared-")
        cls.experiment, cls.project, cls.template = frozen_experiment(Path(cls.temp.name), explanations=False)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.case_temp = tempfile.TemporaryDirectory(prefix="wb-world-expansion-api-case-")
        self.addCleanup(self.case_temp.cleanup)
        base = Path(self.case_temp.name)
        self.repo = base / "repo"
        (self.repo / "projects").mkdir(parents=True)
        (self.repo / "templates").mkdir(parents=True)
        # A fresh, patch-free copy per test -- each test's approve/reject/
        # reopen writes must not leak into the next test.
        shutil.copytree(self.project, self.repo / "projects/momotaro", ignore=shutil.ignore_patterns("patches"))
        shutil.copytree(self.template, self.repo / "templates/momotaro")
        self.project_dir = self.repo / "projects/momotaro"
        self.template_dir = self.repo / "templates/momotaro"
        runs = base / "runs"
        runs.mkdir()

        self.job_store = _FakeJobStore(self.repo)
        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = RunRepository(runs)
        self.server.job_store = self.job_store
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    # -- HTTP helper --------------------------------------------------

    def http(self, method, path, body=None, *, client_header=True, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        hdrs = {"Content-Type": "application/json"}
        if client_header:
            hdrs["X-WorldBloom-Client"] = "1"
        if headers:
            hdrs.update(headers)
        try:
            conn.request(method, path, json.dumps({} if body is None else body), headers=hdrs)
            response = conn.getresponse()
            raw = response.read()
            if not raw:
                return response.status, None
            try:
                return response.status, json.loads(raw)
            except ValueError:
                # ViewerHandler.do_POST's own ForbiddenPath/MissingResource
                # handlers (e.g. the path-traversal guard in _parts()) fall
                # back to BaseHTTPRequestHandler.send_error()'s plain HTML
                # page instead of this API's JSON error shape.
                return response.status, raw.decode("utf-8", "replace")
        finally:
            conn.close()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    # -- fixtures for staged proposals ---------------------------------

    def _stage_raw_proposal(self, patch, gate=None):
        """A hand-written proposal file pair, no real trial -- enough for
        reject/reopen/seen-mismatch tests, which never inspect gate contents."""
        raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
        proposed = self.project_dir / "patches" / "_proposed"
        proposed.mkdir(parents=True, exist_ok=True)
        (proposed / f"{patch['id']}.yaml").write_bytes(raw)
        gate_sha = None
        if gate is not None:
            gate_raw = json.dumps(gate, ensure_ascii=False).encode("utf-8")
            (proposed / f"{patch['id']}.gate.json").write_bytes(gate_raw)
            gate_sha = hashlib.sha256(gate_raw).hexdigest()
        return patch["id"], hashlib.sha256(raw).hexdigest(), gate_sha

    def _gated_patch(self, add, title, *, max_runs, seeds_per_run):
        """Statically + trial-gate a real patch against the shared frozen
        fixture and stage it in self.project_dir's _proposed/, the way
        `scripts.world_patch propose`+`check` would."""
        import scripts.world_patch as wpc
        from gapengine import lineage as lineage_engine

        repository = RunRepository(self.experiment.parent)
        ctx = lineage_engine._resolve_world_context(repository, self.experiment, template_dir=self.template)
        trigger = {"zone": add["zones"][0]["parent"], "verb": "investigate"}
        patch = {"id": patch_id_for(add), "title": title, "trigger": trigger,
                 "parent_digest": EMPTY_STACK_DIGEST, "add": add}
        subject_ids = wpc._subject_ids(ctx["subjects_dir"])
        gate = wpc._gate(self.experiment, self.project_dir, patch, ctx, subject_ids, skip_trial=False,
                          max_runs=max_runs, seeds_per_run=seeds_per_run, seed_set="holdout",
                          template_dir=self.template, give_available=True)
        raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
        proposed_dir = self.project_dir / "patches" / "_proposed"
        with patch_lock(self.project_dir):
            proposed_dir.mkdir(parents=True, exist_ok=True)
            (proposed_dir / f"{patch['id']}.yaml").write_bytes(raw)
        wpc._save_gate(self.project_dir, proposed_dir / f"{patch['id']}.yaml", raw, gate)
        gate_raw = (proposed_dir / f"{patch['id']}.gate.json").read_bytes()
        return patch, gate, hashlib.sha256(raw).hexdigest(), hashlib.sha256(gate_raw).hexdigest()

    # -- boundary -------------------------------------------------------

    def test_approve_requires_client_header(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000001/approve",
            {"reason": "確認しました確認しました", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}},
            client_header=False)
        self.assertEqual(status, 403, payload)

    def test_approve_without_job_store_is_503(self):
        self.server.job_store = None
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000001/approve",
            {"reason": "確認しました確認しました", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}})
        self.assertEqual(status, 503, payload)

    def test_extra_body_key_is_rejected(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000001/approve",
            {"reason": "確認しました確認しました", "status": "reviewable",
             "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}})
        self.assertEqual(status, 400, payload)

    def test_unknown_world_is_404(self):
        status, payload = self.http(
            "POST", "/api/worlds/no-such-world/patches/p-00000001/approve",
            {"reason": "確認しました確認しました", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}})
        self.assertEqual(status, 404, payload)

    def test_malformed_patch_id_is_400(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/UPPERCASE/approve",
            {"reason": "確認しました確認しました", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}})
        self.assertEqual(status, 400, payload)

    def test_extra_key_nested_inside_seen_is_rejected(self):
        # R4 ①: the extra-key rejection must also catch a key smuggled
        # *inside* seen, not just at the top level of the body.
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000001/approve",
            {"reason": "確認しました確認しました",
             "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64, "status": "reviewable"}})
        self.assertEqual(status, 400, payload)

    def test_reason_shorter_than_ten_chars_is_bad_request(self):
        # A2: the API's floor must match approve()'s own (len(reason.strip()) < 10).
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000001/approve",
            {"reason": "短い", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}})
        self.assertEqual(status, 400, payload)

    def test_origin_mismatch_is_403(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000001/approve",
            {"reason": "確認しました確認しました", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}},
            headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403, payload)

    def test_non_json_content_type_is_400(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000001/approve",
            {"reason": "確認しました確認しました", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}},
            headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 400, payload)

    def test_dotdot_world_id_is_403_via_path_traversal_guard(self):
        # R4 ⑥: %2e%2e decodes to ".." -- caught by ViewerHandler._parts()'s
        # traversal guard before this API's own world-id resolution runs.
        status, payload = self.http(
            "POST", "/api/worlds/%2e%2e/patches/p-00000001/approve",
            {"reason": "確認しました確認しました", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}})
        self.assertEqual(status, 403, payload)

    def test_dotdot_patch_id_is_403_via_path_traversal_guard(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/../approve",
            {"reason": "確認しました確認しました", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}})
        self.assertEqual(status, 403, payload)

    def test_repeated_large_body_403_and_503_never_reset_the_connection(self):
        # server.py's _drain_unread_body (do_POST's finally) exists because
        # closing a connection with the POST body still unread makes Windows
        # reset it now and then -- the client sees a dropped connection
        # instead of the error response. Exercise the two "the body never
        # gets read by the normal path" cases it guards:
        #  - a ForbiddenPath (path traversal) raised by _parts(), before any
        #    dispatch function (and so before any body reading) runs at all.
        #  - a 503 (no job_store) from library_pages.py's
        #    _approve_patch_action, which now reads+parses the body (the
        #    reordering fix) before finding there's no job_store.
        #
        # A body past MAX_POST_BYTES is rejected unread as well, so the drain
        # must go past that limit too: draining only the first 64KB of a 200KB
        # body still reset the connection about once in ten class runs.
        oversized_body = {"reason": "x" * 200000,
                          "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}}
        for i in range(20):
            with self.subTest(case="dotdot-oversized", i=i):
                status, payload = self.http(
                    "POST", "/api/worlds/momotaro/patches/../approve", oversized_body)
                self.assertEqual(status, 403, payload)

        under_limit_body = {"reason": "確認しました確認しました" + "x" * 60000,
                             "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}}
        self.assertLess(len(json.dumps(under_limit_body).encode("utf-8")), 64 * 1024)
        for i in range(20):
            with self.subTest(case="dotdot-under-limit", i=i):
                status, payload = self.http(
                    "POST", "/api/worlds/momotaro/patches/../approve", under_limit_body)
                self.assertEqual(status, 403, payload)

        under_limit_body = {"reason": "確認しました確認しました" + "x" * 60000,
                             "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}}
        self.assertLess(len(json.dumps(under_limit_body).encode("utf-8")), 64 * 1024)
        self.server.job_store = None
        for i in range(20):
            with self.subTest(case="no-job-store-under-limit", i=i):
                status, payload = self.http(
                    "POST", "/api/worlds/momotaro/patches/p-00000001/approve", under_limit_body)
                self.assertEqual(status, 503, payload)

    def test_running_job_blocks_approve(self):
        self.job_store._jobs = [{"job_id": "job-1", "state": "running"}]
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000001/approve",
            {"reason": "確認しました確認しました", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}})
        self.assertEqual(status, 409, payload)

    # -- seen freshness ---------------------------------------------------

    def test_seen_mismatch_on_approve_is_409(self):
        # No such proposal on disk at all -- as stale a "seen" as it gets.
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000001/approve",
            {"reason": "確認しました確認しました", "seen": {"patch_sha256": "a" * 64, "gate_sha256": "b" * 64}})
        self.assertEqual(status, 409, payload)

    def test_seen_mismatch_on_reject_is_409(self):
        patch = {"id": "p-000000ab", "title": "x", "add": {"zones": [{"name": "y", "parent": "海"}]}}
        _pid, patch_sha, gate_sha = self._stage_raw_proposal(patch)
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-000000ab/reject",
            {"seen": {"patch_sha256": "not-" + patch_sha, "gate_sha256": gate_sha}})
        self.assertEqual(status, 409, payload)
        self.assertTrue((self.project_dir / "patches/_proposed/p-000000ab.yaml").is_file())

    def test_seen_mismatch_on_reopen_is_409(self):
        add = {"zones": [{"name": "小屋Z", "parent": "海"}]}
        write_approved(self.project_dir, {"title": "t", "add": add})
        status, payload = self.http("POST", "/api/worlds/momotaro/patches/reopen", {"seen": {"head": "not-the-head"}})
        self.assertEqual(status, 409, payload)

    # -- reject -----------------------------------------------------------

    def test_reject_moves_proposal_to_rejected(self):
        patch = {"id": "p-000000cd", "title": "x", "add": {"zones": [{"name": "z", "parent": "海"}]}}
        gate = {"patch_id": "p-000000cd", "status": "trial_pending", "static": {"passed": True}, "trial": None}
        _pid, patch_sha, gate_sha = self._stage_raw_proposal(patch, gate)
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-000000cd/reject",
            {"seen": {"patch_sha256": patch_sha, "gate_sha256": gate_sha}})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertFalse((self.project_dir / "patches/_proposed/p-000000cd.yaml").exists())
        self.assertTrue((self.project_dir / "patches/_rejected/p-000000cd.yaml").is_file())
        self.assertTrue((self.project_dir / "patches/_rejected/p-000000cd.gate.json").is_file())

    def test_reject_with_no_gate_json_succeeds(self):
        # R4 ⑤: a proposal can be rejected before it's ever been gated --
        # gate_sha256 is None both in what the screen "saw" and on disk.
        patch = {"id": "p-000000ef", "title": "x", "add": {"zones": [{"name": "z2", "parent": "海"}]}}
        _pid, patch_sha, gate_sha = self._stage_raw_proposal(patch)
        self.assertIsNone(gate_sha)
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-000000ef/reject",
            {"seen": {"patch_sha256": patch_sha, "gate_sha256": None}})
        self.assertEqual(status, 200, payload)
        self.assertTrue((self.project_dir / "patches/_rejected/p-000000ef.yaml").is_file())
        self.assertFalse((self.project_dir / "patches/_rejected/p-000000ef.gate.json").exists())
        self.assertFalse((self.project_dir / "patches/_proposed/p-000000ef.yaml").exists())

    def test_reject_readonly_is_forbidden_by_lack_of_job_store(self):
        self.server.job_store = None
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-000000cd/reject",
            {"seen": {"patch_sha256": "a" * 64, "gate_sha256": None}})
        self.assertEqual(status, 503, payload)

    # -- reopen -------------------------------------------------------------

    def test_reopen_returns_the_last_approved_patch_to_proposed(self):
        add = {"zones": [{"name": "小屋Y", "parent": "海"}]}
        approved = write_approved(self.project_dir, {"title": "reopen試験", "add": add})
        head = read_stack(self.project_dir)["head"]
        status, payload = self.http("POST", "/api/worlds/momotaro/patches/reopen", {"seen": {"head": head}})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["patch_ids"], [approved["id"]])
        self.assertTrue((self.project_dir / f"patches/_proposed/{approved['id']}.yaml").is_file())
        self.assertFalse((self.project_dir / f"patches/{approved['id']}.yaml").exists())

    def test_reopen_with_nothing_approved_is_409(self):
        status, payload = self.http("POST", "/api/worlds/momotaro/patches/reopen",
                                    {"seen": {"head": "does-not-matter"}})
        # No approved revisions -> stack_head is EMPTY_STACK_DIGEST, so a
        # bogus seen head is caught as a mismatch before "nothing to reopen".
        self.assertEqual(status, 409, payload)

    # -- approve: real gate outcomes (slow: each stages a real trial) -------

    def test_insufficient_proposal_cannot_be_approved_and_stays_proposed(self):
        # Sources both the added branch and the trigger zone itself (海) --
        # check_trigger_coverage requires the trigger zone to also gain a
        # reason to investigate there, the way test_world_patch_r2.py's
        # FrozenReplay._reviewable_patch callers do (comment there: A1/A2).
        add = {"zones": [{"name": "小屋I", "parent": "海"}],
               "items": [{"name": "試験アイテムI", "sources": [
                   {"type": "investigate", "zone": "小屋I", "count": 1, "max": 1},
                   {"type": "investigate", "zone": "海", "count": 1, "max": 1}]}]}
        patch, gate, patch_sha, gate_sha = self._gated_patch(add, "不足試験", max_runs=1, seeds_per_run=4)
        self.assertEqual(gate["status"], "insufficient", gate)
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/approve",
            {"reason": "確認しましたが個体数が足りません", "seen": {"patch_sha256": patch_sha, "gate_sha256": gate_sha}})
        self.assertEqual(status, 422, payload)
        self.assertTrue((self.project_dir / f"patches/_proposed/{patch['id']}.yaml").is_file())
        self.assertFalse((self.project_dir / "patches/stack.json").exists())

    def test_hand_edited_gate_status_is_caught_and_stays_proposed(self):
        # R4 ③: rewriting gate.json's own "status" field to "reviewable"
        # must not fool approve() -- it re-derives status from the gate's
        # recorded trial evidence (gate_status()), which is still
        # "insufficient" for this same evidence, and that mismatch is a
        # PatchError (422), not the plain "not reviewable" one.
        add = {"zones": [{"name": "小屋H", "parent": "海"}],
               "items": [{"name": "試験アイテムH", "sources": [
                   {"type": "investigate", "zone": "小屋H", "count": 1, "max": 1},
                   {"type": "investigate", "zone": "海", "count": 1, "max": 1}]}]}
        patch, gate, _patch_sha, _gate_sha = self._gated_patch(add, "改竄試験", max_runs=1, seeds_per_run=4)
        self.assertEqual(gate["status"], "insufficient", gate)
        gate_path = self.project_dir / f"patches/_proposed/{patch['id']}.gate.json"
        tampered = json.loads(gate_path.read_text(encoding="utf-8"))
        tampered["status"] = "reviewable"
        tampered_raw = json.dumps(tampered, ensure_ascii=False).encode("utf-8")
        gate_path.write_bytes(tampered_raw)
        patch_sha = hashlib.sha256((self.project_dir / f"patches/_proposed/{patch['id']}.yaml").read_bytes()).hexdigest()
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/approve",
            {"reason": "改竄されたゲートを承認しようとする試験",
             "seen": {"patch_sha256": patch_sha, "gate_sha256": hashlib.sha256(tampered_raw).hexdigest()}})
        self.assertEqual(status, 422, payload)
        self.assertTrue((self.project_dir / f"patches/_proposed/{patch['id']}.yaml").is_file())
        self.assertFalse((self.project_dir / "patches/stack.json").exists())

    def test_exploration_seed_proposal_cannot_be_approved(self):
        # R4 ④: gate_status()/trial_state() don't look at seed_set at all,
        # so a fully "measured" trial on *exploration* seeds can still reach
        # status "reviewable" -- approve() has its own separate holdout
        # check (evidence["seed_set"] != "holdout"), which this exercises
        # end to end through the API.
        add = {"zones": [{"name": "小屋E", "parent": "海"}],
               "items": [{"name": "試験アイテムE", "sources": [
                   {"type": "investigate", "zone": "小屋E", "count": 1, "max": 1},
                   {"type": "investigate", "zone": "海", "count": 1, "max": 1}]}]}
        import scripts.world_patch as wpc
        from gapengine import lineage as lineage_engine
        repository = RunRepository(self.experiment.parent)
        ctx = lineage_engine._resolve_world_context(repository, self.experiment, template_dir=self.template)
        trigger = {"zone": "海", "verb": "investigate"}
        patch = {"id": patch_id_for(add), "title": "探索seed試験", "trigger": trigger,
                 "parent_digest": EMPTY_STACK_DIGEST, "add": add}
        subject_ids = wpc._subject_ids(ctx["subjects_dir"])
        gate = wpc._gate(self.experiment, self.project_dir, patch, ctx, subject_ids, skip_trial=False,
                          max_runs=5, seeds_per_run=4, seed_set="exploration",
                          template_dir=self.template, give_available=True)
        self.assertEqual(gate["status"], "reviewable", gate)
        self.assertEqual(gate["trial"]["evidence"]["seed_set"], "exploration")
        raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
        proposed_dir = self.project_dir / "patches" / "_proposed"
        with patch_lock(self.project_dir):
            proposed_dir.mkdir(parents=True, exist_ok=True)
            (proposed_dir / f"{patch['id']}.yaml").write_bytes(raw)
        wpc._save_gate(self.project_dir, proposed_dir / f"{patch['id']}.yaml", raw, gate)
        gate_raw = (proposed_dir / f"{patch['id']}.gate.json").read_bytes()
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/approve",
            {"reason": "探索seedのまま承認しようとする試験",
             "seen": {"patch_sha256": hashlib.sha256(raw).hexdigest(),
                      "gate_sha256": hashlib.sha256(gate_raw).hexdigest()}})
        self.assertEqual(status, 422, payload)
        self.assertTrue((self.project_dir / f"patches/_proposed/{patch['id']}.yaml").is_file())

    def test_approve_raises_stalepatch_when_gate_changes_after_the_seen_hash(self):
        # R3: approve()'s own expect_patch_sha256/expect_gate_sha256 check,
        # exercised directly (not through the API) -- simulates the API's
        # own pre-check having passed, then the gate.json being rewritten
        # (same content, different bytes) before approve() itself reads it.
        from execution.world_patch_approval import StalePatch, approve

        add = {"zones": [{"name": "小屋S", "parent": "海"}],
               "items": [{"name": "試験アイテムS", "sources": [
                   {"type": "investigate", "zone": "小屋S", "count": 1, "max": 1},
                   {"type": "investigate", "zone": "海", "count": 1, "max": 1}]}]}
        patch, gate, patch_sha, gate_sha = self._gated_patch(add, "StalePatch試験", max_runs=5, seeds_per_run=4)
        self.assertEqual(gate["status"], "reviewable", gate)
        gate_path = self.project_dir / f"patches/_proposed/{patch['id']}.gate.json"
        # Re-serialize with different whitespace: same structure, different
        # bytes/hash -- a "gate.json changed since the screen was loaded"
        # stand-in that stays valid JSON (approve() must get past json.loads()
        # to reach the new check).
        gate_path.write_bytes(json.dumps(json.loads(gate_path.read_bytes()), ensure_ascii=False, indent=2).encode("utf-8"))
        with self.assertRaises(StalePatch):
            approve(self.project_dir, self.template_dir, patch["id"], "湿った状態のままの承認",
                    expect_patch_sha256=patch_sha, expect_gate_sha256=gate_sha)
        self.assertTrue((self.project_dir / f"patches/_proposed/{patch['id']}.yaml").is_file())
        self.assertFalse((self.project_dir / "patches/stack.json").exists())

    def test_reviewable_holdout_proposal_can_be_approved_and_is_stacked(self):
        add = {"zones": [{"name": "小屋R", "parent": "海"}],
               "items": [{"name": "試験アイテムR", "sources": [
                   {"type": "investigate", "zone": "小屋R", "count": 1, "max": 1},
                   {"type": "investigate", "zone": "海", "count": 1, "max": 1}]}]}
        patch, gate, patch_sha, gate_sha = self._gated_patch(add, "承認試験", max_runs=5, seeds_per_run=4)
        self.assertEqual(gate["status"], "reviewable", gate)
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/approve",
            {"reason": "測定結果を確認して承認します", "seen": {"patch_sha256": patch_sha, "gate_sha256": gate_sha}})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["patch_id"], patch["id"])
        self.assertEqual(payload["rev"], 1)
        stack = read_stack(self.project_dir)
        self.assertEqual(len(stack["revisions"]), 1)
        self.assertEqual(stack["revisions"][0]["patch_id"], patch["id"])
        self.assertFalse((self.project_dir / f"patches/_proposed/{patch['id']}.yaml").exists())
        self.assertTrue((self.project_dir / f"patches/{patch['id']}.yaml").is_file())

    # -- retire (WB-WORLDGROW-001 段階5a) ----------------------------------

    def _write_experiment(self, name, *, protagonist="桃太郎", cells=None):
        """A minimal experiment under this server's runs_root, just enough
        for viewer.data.RunRepository.experiment()/world_demand_view.
        resolve_root() to accept it and for retire()'s server-side usage
        table to compute (possibly against zero readable exemplars)."""
        experiment = self.server.repository.runs_root / name
        experiment.mkdir(parents=True, exist_ok=True)
        (experiment / "archive.json").write_text(
            json.dumps({"cells": cells or {}}, ensure_ascii=False), encoding="utf-8")
        (experiment / "config.json").write_text(
            json.dumps({"preview": {"protagonist": protagonist}}, ensure_ascii=False), encoding="utf-8")
        return experiment

    def test_retire_without_job_store_is_503(self):
        self.server.job_store = None
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000000/retire",
            {"reason": "確認しました確認しました", "experiment": "exp1", "seen": {"head": EMPTY_STACK_DIGEST}})
        self.assertEqual(status, 503, payload)

    def test_retire_extra_body_key_is_rejected(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000000/retire",
            {"reason": "確認しました確認しました", "experiment": "exp1", "status": "reviewable",
             "seen": {"head": EMPTY_STACK_DIGEST}})
        self.assertEqual(status, 400, payload)

    def test_retire_reason_too_short_is_400(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000000/retire",
            {"reason": "短い", "experiment": "exp1", "seen": {"head": EMPTY_STACK_DIGEST}})
        self.assertEqual(status, 400, payload)

    def test_retire_seen_mismatch_is_409(self):
        add = {"zones": [{"name": "小屋淘汰1", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "淘汰試験1", "add": add})
        self._write_experiment("exp-retire-1")
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/retire",
            {"reason": "使われていないので枯らします", "experiment": "exp-retire-1",
             "seen": {"head": "not-the-real-head"}})
        self.assertEqual(status, 409, payload)
        self.assertTrue((self.project_dir / f"patches/{patch['id']}.yaml").is_file())
        self.assertFalse((self.project_dir / f"patches/{patch['id']}.retire.json").exists())

    def test_retire_unknown_experiment_is_400(self):
        add = {"zones": [{"name": "小屋淘汰2", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "淘汰試験2", "add": add})
        head = read_stack(self.project_dir)["head"]
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/retire",
            {"reason": "使われていないので枯らします", "experiment": "no-such-experiment",
             "seen": {"head": head}})
        self.assertEqual(status, 400, payload)

    def test_retire_unapproved_patch_is_422(self):
        self._write_experiment("exp-retire-3")
        head = read_stack(self.project_dir)["head"]
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000000/retire",
            {"reason": "存在しないパッチを枯らそうとします", "experiment": "exp-retire-3",
             "seen": {"head": head}})
        self.assertEqual(status, 422, payload)

    def test_retire_succeeds_and_writes_tombstone(self):
        add = {"zones": [{"name": "小屋淘汰4", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "淘汰試験4", "add": add})
        self._write_experiment("exp-retire-4")
        head = read_stack(self.project_dir)["head"]
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/retire",
            {"reason": "使われていないので枯らします", "experiment": "exp-retire-4",
             "seen": {"head": head}})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["patch_id"], patch["id"])
        self.assertEqual(payload["rev"], 2)
        self.assertTrue((self.project_dir / f"patches/{patch['id']}.yaml").is_file())
        retire_path = self.project_dir / f"patches/{patch['id']}.retire.json"
        self.assertTrue(retire_path.is_file())
        record = json.loads(retire_path.read_text(encoding="utf-8"))
        self.assertEqual(record["experiment"], "exp-retire-4")
        self.assertIn("usage", record)
        stack = read_stack(self.project_dir)
        self.assertEqual(stack["revisions"][-1]["kind"], "retire")

    def test_retire_dependent_patch_conflict_is_422(self):
        add_a = {"zones": [{"name": "小屋淘汰5", "parent": "海"}]}
        patch_a = write_approved(self.project_dir, {"title": "淘汰試験5A", "add": add_a})
        add_c = {"zones": [], "items": [{"name": "淘汰試験用の道具", "sources": [
            {"type": "investigate", "zone": "海", "count": 1, "max": 1}], "craft_zone": "小屋淘汰5"}]}
        write_approved(self.project_dir, {"title": "淘汰試験5C", "add": add_c})
        self._write_experiment("exp-retire-5")
        head = read_stack(self.project_dir)["head"]
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch_a['id']}/retire",
            {"reason": "依存されているのに枯らそうとします", "experiment": "exp-retire-5",
             "seen": {"head": head}})
        self.assertEqual(status, 422, payload)
        self.assertFalse((self.project_dir / f"patches/{patch_a['id']}.retire.json").exists())

    # -- M1 (Opus review): a catalogued (screen-run) experiment has no
    # top-level archive.json, only published/<revision>/archive.json --
    # both retire()'s own archive_sha256 hashing and the server's usage
    # computation must read through that layout instead of silently
    # producing a 500 / an always-empty usage table. --------------------

    def _write_catalog_experiment(self, name, *, cells, protagonist="桃太郎"):
        """A genuine (non-legacy), fully catalog-verified experiment with NO
        top-level archive.json -- built the same way tests/test_run_catalog.
        py's test_real_ui004_publication_and_terminal_gate and tests/
        test_ga_replay.py's _publish_revision hand-build one, minus the real
        GA run (this only needs the on-disk shape, not real individuals)."""
        from execution.provenance import atomic_json as _atomic_json, canonical, sha256, write_bytes
        root = self.server.repository.runs_root / name
        root.mkdir(parents=True)
        manifest = {"schema_version": 1, "run_id": name, "config_id": "cfg-usage",
                    "evolution": {}, "target_endings": []}
        manifest_bytes = canonical(manifest)
        write_bytes(root / "manifest.json", manifest_bytes)
        _atomic_json(root / "complete.json", {"schema_version": 1, "manifest_sha256": sha256(manifest_bytes)})
        (root / "config.json").write_text(
            json.dumps({"preview": {"protagonist": protagonist}}, ensure_ascii=False), encoding="utf-8")
        payloads = {"archive": {"cells": cells}, "summary": {},
                    "candidates": {"schema_version": 1, "run_id": name, "revision": 1, "candidates": []}}
        folder = root / "published" / "1"
        folder.mkdir(parents=True)
        files = {}
        for key, value in payloads.items():
            raw = canonical(value)
            write_bytes(folder / f"{key}.json", raw)
            files[key] = {"path": f"published/1/{key}.json", "sha256": sha256(raw)}
        pub_manifest = {"schema_version": 1, "run_id": name, "revision": 1,
                        "completed_generations": 1, "files": files}
        pub_manifest_bytes = canonical(pub_manifest)
        write_bytes(folder / "manifest.json", pub_manifest_bytes)
        _atomic_json(root / "published" / "current.json", {"schema_version": 1, "run_id": name,
                     "revision": 1, "manifest_sha256": sha256(pub_manifest_bytes)})
        self.assertFalse((root / "archive.json").exists())
        return root

    def test_retire_reads_usage_from_published_archive_when_no_top_level_archive(self):
        add = {"zones": [{"name": "小屋淘汰6", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "淘汰試験6", "add": add})
        log_relative = "g0/ind-0/seed-1/layers.jsonl"
        cells = {"c0": {"exemplar": {"layers_path": log_relative}}}
        root = self._write_catalog_experiment("exp-retire-6", cells=cells)
        (root / log_relative).parent.mkdir(parents=True, exist_ok=True)
        rows = [{"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
                 "delta": {"actor": {"zone": "小屋淘汰6"}}}]
        (root / log_relative).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        # M1: the server's own repository must have a catalog (control_root
        # given) to exercise viewer.data.RunRepository.archive()'s catalog
        # branch at all -- the plain setUp repository has none.
        self.server.repository = RunRepository(self.server.repository.runs_root,
                                                control_root=Path(self.case_temp.name) / "control-usage")
        head = read_stack(self.project_dir)["head"]
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/retire",
            {"reason": "公開版のみのアーカイブから枯らします", "experiment": "exp-retire-6",
             "seen": {"head": head}})
        self.assertEqual(status, 200, payload)
        record = json.loads((self.project_dir / f"patches/{patch['id']}.retire.json").read_text(encoding="utf-8"))
        self.assertIsNotNone(record["archive_sha256"])
        self.assertEqual(record["archive_source"], "published/1")
        self.assertIsNotNone(record["usage"])
        self.assertEqual(record["usage"]["elites_total"], 1)
        self.assertEqual(record["usage"]["elites_strong"], 1)

    # -- export / import (WB-WORLDGROW-001 段階5d "ジャンルの資産") ---------

    def _freeze_world_with_patches(self, root, patch_ids, *, project_id="momotaro"):
        """WB-WORLDGROW-001 段階5d M1: export_patch() now requires that
        `experiment`'s own *frozen* world (inputs/projects/<id>/world.yaml)
        was actually built with the exported patch applied -- mirrors
        execution.epoch_chain.EpochChain._auto_retire's own frozen-world
        read. The base world content itself does not matter for this check
        (only expansion.patches[].id does), so a minimal stand-in is enough."""
        world_path = root / "inputs" / "projects" / project_id / "world.yaml"
        world_path.parent.mkdir(parents=True, exist_ok=True)
        world = {"expansion": {"patches": [{"id": pid} for pid in patch_ids]}}
        world_path.write_text(yaml.safe_dump(world, allow_unicode=True), encoding="utf-8")

    def _strong_use_experiment(self, name, *, zone, patch_id=None):
        """A catalog-verified experiment (no top-level archive.json, same
        shape as _write_catalog_experiment) whose sole exemplar log shows the
        protagonist actually moving into `zone` -- enough for patch_usage()
        to report elites_strong=1 for a patch that added it. When `patch_id`
        is given, also freezes a world that actually includes it (M1)."""
        log_relative = "g0/ind-0/seed-1/layers.jsonl"
        cells = {"c0": {"exemplar": {"layers_path": log_relative}}}
        root = self._write_catalog_experiment(name, cells=cells)
        (root / log_relative).parent.mkdir(parents=True, exist_ok=True)
        rows = [{"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
                 "delta": {"actor": {"zone": zone}}}]
        (root / log_relative).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        if patch_id is not None:
            self._freeze_world_with_patches(root, [patch_id])
        self.server.repository = RunRepository(self.server.repository.runs_root,
                                                control_root=Path(self.case_temp.name) / f"control-{name}")
        return root

    def _second_world(self, name="momotaro2"):
        target = self.repo / "projects" / name
        shutil.copytree(self.project, target, ignore=shutil.ignore_patterns("patches"))
        return target

    def test_export_without_job_store_is_503(self):
        self.server.job_store = None
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000000/export", {"experiment": "exp1"})
        self.assertEqual(status, 503, payload)

    def test_export_extra_body_key_is_rejected(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000000/export",
            {"experiment": "exp1", "extra": 1})
        self.assertEqual(status, 400, payload)

    def test_export_unknown_experiment_is_400(self):
        add = {"zones": [{"name": "小屋輸出1", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "輸出試験1", "add": add})
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/export",
            {"experiment": "no-such-experiment"})
        self.assertEqual(status, 400, payload)

    def test_export_unapproved_patch_is_422(self):
        self._write_experiment("exp-export-2")
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000000/export",
            {"experiment": "exp-export-2"})
        self.assertEqual(status, 422, payload)

    def test_running_job_blocks_export(self):
        add = {"zones": [{"name": "小屋輸出3", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "輸出試験3", "add": add})
        self._write_experiment("exp-export-3")
        self.job_store._jobs = [{"job_id": "job-1", "state": "running"}]
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/export", {"experiment": "exp-export-3"})
        self.assertEqual(status, 409, payload)

    def test_export_without_strong_use_is_422(self):
        # Frozen world DOES include the patch (M1 satisfied) -- this test is
        # specifically about the strong-use rejection, not M1's.
        add = {"zones": [{"name": "小屋輸出4", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "輸出試験4", "add": add})
        root = self._write_experiment("exp-export-4")
        self._freeze_world_with_patches(root, [patch["id"]])
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/export", {"experiment": "exp-export-4"})
        self.assertEqual(status, 422, payload)
        self.assertFalse((self.template_dir / "expansions").exists())

    def test_export_without_frozen_world_is_422(self):
        # WB-WORLDGROW-001 段階5d M1: an experiment with no frozen world at
        # all (this test's _write_experiment writes only archive.json/
        # config.json, no inputs/) can never prove which patches it ran
        # under -- rejected even though usage would otherwise be strong.
        add = {"zones": [{"name": "小屋輸出M1a", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "輸出試験M1a", "add": add})
        self._strong_use_experiment("exp-export-m1a", zone="小屋輸出M1a")  # no patch_id -> no frozen world
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/export", {"experiment": "exp-export-m1a"})
        self.assertEqual(status, 422, payload)
        self.assertFalse((self.template_dir / "expansions").exists())

    def test_export_when_frozen_world_lacks_this_patch_is_422(self):
        # WB-WORLDGROW-001 段階5d M1: the frozen world exists but never
        # actually had this patch applied -- a same-named patch approved on
        # some other world (or any unrelated frozen world) must not be
        # accepted as evidence.
        add = {"zones": [{"name": "小屋輸出M1b", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "輸出試験M1b", "add": add})
        root = self._strong_use_experiment("exp-export-m1b", zone="小屋輸出M1b")
        self._freeze_world_with_patches(root, ["p-notthisone"])
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/export", {"experiment": "exp-export-m1b"})
        self.assertEqual(status, 422, payload)
        self.assertFalse((self.template_dir / "expansions").exists())

    def test_export_succeeds_and_writes_asset(self):
        add = {"zones": [{"name": "小屋輸出5", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "輸出試験5", "add": add})
        self._strong_use_experiment("exp-export-5", zone="小屋輸出5", patch_id=patch["id"])
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/export", {"experiment": "exp-export-5"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["patch_id"], patch["id"])
        asset_path = self.template_dir / "expansions" / f"{patch['id']}.yaml"
        self.assertTrue(asset_path.is_file())
        entry = yaml.safe_load(asset_path.read_text(encoding="utf-8"))
        self.assertEqual(entry["provenance"]["evidence"]["usage"]["elites_strong"], 1)
        self.assertEqual(entry["provenance"]["world_id"], "momotaro")

    def test_export_duplicate_is_409(self):
        # R3 (Opus review): exporting the same id twice is a conflict, not a
        # generic validation error.
        add = {"zones": [{"name": "小屋輸出6", "parent": "海"}]}
        patch = write_approved(self.project_dir, {"title": "輸出試験6", "add": add})
        self._strong_use_experiment("exp-export-6", zone="小屋輸出6", patch_id=patch["id"])
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/export", {"experiment": "exp-export-6"})
        self.assertEqual(status, 200, payload)
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/export", {"experiment": "exp-export-6"})
        self.assertEqual(status, 409, payload)

    def test_import_without_job_store_is_503(self):
        self.server.job_store = None
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/import",
            {"entry": "p-00000000", "seen": {"head": EMPTY_STACK_DIGEST}})
        self.assertEqual(status, 503, payload)

    def test_import_unknown_entry_is_422(self):
        head = read_stack(self.project_dir)["head"]
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/import",
            {"entry": "p-00000000", "seen": {"head": head}})
        self.assertEqual(status, 422, payload)

    def test_import_head_mismatch_is_409(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/import",
            {"entry": "p-00000000", "seen": {"head": "not-the-real-head"}})
        self.assertEqual(status, 409, payload)

    def test_running_job_blocks_import(self):
        head = read_stack(self.project_dir)["head"]
        self.job_store._jobs = [{"job_id": "job-1", "state": "running"}]
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/import",
            {"entry": "p-00000000", "seen": {"head": head}})
        self.assertEqual(status, 409, payload)

    def test_import_succeeds_and_stages_proposal(self):
        # An item sourcing from the new zone (not just the zone itself) --
        # fit()'s check_trigger_coverage needs at least one add.items/facts
        # entry sourcing from *some* zone to have anything to resolve
        # trigger.zone to at all (tests/test_world_patch_library.py's
        # ADD_HUT does the same).
        add = {"zones": [{"name": "小屋輸入1", "parent": "海"}], "items": [{"name": "輸入試験の道具", "sources": [
            {"type": "investigate", "zone": "小屋輸入1", "count": 1, "max": 2}]}]}
        patch = write_approved(self.project_dir, {"title": "輸入試験1", "add": add})
        self._strong_use_experiment("exp-import-1", zone="小屋輸入1", patch_id=patch["id"])
        status, payload = self.http(
            "POST", f"/api/worlds/momotaro/patches/{patch['id']}/export", {"experiment": "exp-import-1"})
        self.assertEqual(status, 200, payload)

        target = self._second_world()
        head = EMPTY_STACK_DIGEST
        status, payload = self.http(
            "POST", "/api/worlds/momotaro2/patches/import",
            {"entry": patch["id"], "seen": {"head": head}})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["patch_id"], patch["id"])
        proposed = target / "patches" / "_proposed" / f"{patch['id']}.yaml"
        self.assertTrue(proposed.is_file())
        self.assertFalse(proposed.with_suffix(".gate.json").exists())

    # -- R9 (Opus review): boundary/404 coverage for the two new routes ----

    def test_export_requires_client_header(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/p-00000001/export",
            {"experiment": "exp1"}, client_header=False)
        self.assertEqual(status, 403, payload)

    def test_export_unknown_world_is_404(self):
        status, payload = self.http(
            "POST", "/api/worlds/no-such-world/patches/p-00000001/export", {"experiment": "exp1"})
        self.assertEqual(status, 404, payload)

    def test_import_requires_client_header(self):
        status, payload = self.http(
            "POST", "/api/worlds/momotaro/patches/import",
            {"entry": "p-00000001", "seen": {"head": EMPTY_STACK_DIGEST}}, client_header=False)
        self.assertEqual(status, 403, payload)

    def test_import_unknown_world_is_404(self):
        status, payload = self.http(
            "POST", "/api/worlds/no-such-world/patches/import",
            {"entry": "p-00000001", "seen": {"head": EMPTY_STACK_DIGEST}})
        self.assertEqual(status, 404, payload)


class WorldExpansionStaticFileTests(unittest.TestCase):
    def setUp(self):
        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = None
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_world_expansion_js_is_served(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request("GET", "/static/world-expansion.js")
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200, body)
            self.assertIn(b"data-patch-action", body)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
