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
