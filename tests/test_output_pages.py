"""WB-UI-008: generation confirm/job/output-list/output-detail HTML pages.

No LLM, no real GA, no real generation job is ever started. Outputs are built
directly with execution.output_store.OutputStore (durable receipts), exactly
as the owned generation worker would leave them on disk; the fake job store
only supplies the job-ledger view (state/kind/output_id) that JobStore.output()
would normally attach.
"""
from __future__ import annotations

from contextlib import contextmanager
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
import uuid

from execution.configs import ConfigStore
from execution.generation import result as gen_result
from execution.output_store import OutputStore
from execution.provenance import ConfigError, atomic_json
from execution.worker import TERMINAL
from viewer import output_pages, workbench_pages
from viewer.data import RunRepository
from viewer.server import ViewerServer, ViewerHandler

ROOT = Path(__file__).resolve().parents[1]


class FakeJobStore:
    """Duck-typed job store: adds outputs()/output() on top of the UI-007 shape.

    outputs()/output() read the real, durable OutputStore -- the only faked
    piece is the job-ledger lookup (job_state), same as the real JobStore.output()
    attaching the owning job's state to a projected output.
    """

    def __init__(self, configs):
        self.configs = configs
        self.control = configs.control
        self._jobs = {}
        self.submitted = []
        self.outputs_raise = False
        self.broken_output_ids = set()

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
        raise ConfigError("worker", "テストでは生成/GAを起動しません", code="unavailable")

    def cancel(self, jid):
        raise ConfigError("worker", "テストでは停止できません", code="unavailable")

    @contextmanager
    def _lock(self):
        yield

    def _all(self):
        return list(self._jobs.values())

    def _reconcile(self, job):
        return dict(job)

    def output(self, output_id, *, recover=False):
        store = OutputStore(self.control)
        try:
            request = store.request(output_id)
        except FileNotFoundError as error:
            raise ConfigError("output_id", "生成版がありません", code="not_found") from error
        payload = store.project(output_id)
        # No fallback default: a genuinely unregistered job_id must surface as
        # job_state=None (coordinator fix #9), not silently read as "succeeded".
        job = self._jobs.get(request.get("job_id"))
        return {**payload, "request": request, "job_state": job.get("state") if job else None}

    def outputs(self):
        if self.outputs_raise:
            raise ValueError("boom")
        store = OutputStore(self.control)
        if not store.root.exists():
            return []
        listed = []
        for p in sorted(store.root.iterdir()):
            if not (p.is_dir() and p.name.startswith("out-")):
                continue
            if p.name in self.broken_output_ids:
                listed.append({"output_id": p.name,
                                "error": {"code": "snapshot_changed", "message": "保存済み生成記録のSHAが一致しません"}})
                continue
            try:
                listed.append(self.output(p.name))
            except ConfigError as error:
                listed.append({"output_id": p.name, "error": {"code": error.code, "message": str(error)}})
        return listed


def _gen_job(jid, run_id, output_id, kind, state, *, total=1, completed=1, counts=None, completion_kind=None):
    now = time.time()
    return {
        "schema_version": 1, "job_id": jid, "request_id": "req-" + jid, "kind": kind,
        "config_id": "cfg-test", "run_id": run_id, "state": state, "phase": "preparing", "revision": 1,
        "created_at": now, "updated_at": now, "started_at": now,
        "finished_at": now if state in TERMINAL else None, "heartbeat": now,
        "cancel_requested_at": None, "error": None, "exit_code": None,
        "progress": {"completed": completed, "total": total, "counts": counts or {}},
        "reconciliation": "confirmed", "publication_revision": None,
        "output_id": output_id, "completion_kind": completion_kind, "counts": counts or {},
    }


class OutputPagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui008-")
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
        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = RunRepository(self.runs, control_root=self.control, jobs=self.fake)
        self.server.job_store = self.fake
        self.server.settings_path = ROOT / "settings.json"
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self._cleanup_temp)

    def _cleanup_temp(self):
        for _ in range(50):
            try:
                self.temp.cleanup()
                return
            except PermissionError:
                time.sleep(0.1)
        self.temp.cleanup()

    def get_status(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.server.server_port}{path}", timeout=5) as response:
                return response.status, response.read().decode("utf-8"), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8"), error.headers

    def http(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json", "X-WorldBloom-Client": "1"}
        try:
            conn.request(method, path, None if body is None else json.dumps(body), headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
        finally:
            conn.close()

    # ---------------------------------------------------------------- setup

    def _legacy_run(self, name, count=5, *, reached=None):
        """A legacy run with `count` screenable candidates, cell keys C{i}|low."""
        root = self.runs / name
        root.mkdir()
        reached = reached or {i: True for i in range(count)}
        cells = {}
        for i in range(count):
            fname = f"log{i}.jsonl"
            (root / fname).write_bytes(f'{{"kind":"a{i}"}}\n'.encode())
            cells[f"C{i}|low"] = {"generation": 0, "quality": 0.5, "reach_rate": 1.0 if reached[i] else 0.0,
                                   "reached": reached[i], "exemplar": {"seed": i, "layers_path": fname},
                                   "parents": [], "genome": {}}
        atomic_json(root / "archive.json", {"cells": cells})
        catalog = self.server.repository.catalog
        rid = catalog.register_legacy(name)
        candidates = catalog.candidates(rid)["candidates"]
        by_cell = {c["cell_key"]: c["candidate_id"] for c in candidates}
        cids = [by_cell[f"C{i}|low"] for i in range(count)]
        return rid, cids

    def _create_output(self, *, kind, run_id, candidate_ids, config_id="cfg-test", selection_revision=0,
                        mode="missing_or_failed", backend="none", model=None, synopsis_refs=None,
                        output_id=None, job_id=None):
        output_id = output_id or ("out-" + uuid.uuid4().hex)
        job_id = job_id or ("job-" + uuid.uuid4().hex)
        request = {
            "schema_version": 1, "output_id": output_id, "request_id": "req-" + output_id, "job_id": job_id,
            "kind": kind, "run_id": run_id, "settings_provenance": "posthoc_generation_config",
            "config_id": config_id, "publication_revision": 1, "candidates_sha256": "0" * 64,
            "selection_revision": selection_revision, "selection_sha256": "0" * 64,
            "candidate_ids": sorted(candidate_ids), "sources": [],
            "backend": backend, "model": model,
            "limits": {"max_calls": 5, "call_timeout_seconds": 180, "wall_seconds": 240,
                       "max_saved_response_bytes": 128000},
            "mode": mode, "synopsis_refs": synopsis_refs or {cid: None for cid in candidate_ids},
            "input_manifest_sha256": "0" * 64, "config_sha256": "0" * 64, "runtime_manifest_sha256": "0" * 64,
            "acknowledge_unknown": False, "attempt_ids": [],
        }
        prompts = {cid: f"prompt for {cid}" for cid in candidate_ids}
        store = OutputStore(self.control)
        store.create(request, prompts)
        return output_id, job_id, store

    @staticmethod
    def _finish(store, output_id, cid, status, code, *, text=None, **kwargs):
        sink = store.sink(output_id, cid)
        record = gen_result(sink.identity, status, code, **kwargs)
        return sink.finish(record, text=text)

    # ------------------------------------------------------------ outputs list

    def test_outputs_list_filter_degraded_and_broken_row(self):
        rid_a, cids_a = self._legacy_run("exp-out-a", count=2)
        rid_b, cids_b = self._legacy_run("exp-out-b", count=1)
        oid_a, job_a, store_a = self._create_output(kind="synopsize", run_id=rid_a, candidate_ids=cids_a)
        self._finish(store_a, oid_a, cids_a[0], "ok", "completed", text="p1\n\np2")
        self._finish(store_a, oid_a, cids_a[1], "prompt_only", "prompt_saved")
        self.fake.add(_gen_job(job_a, rid_a, oid_a, "synopsize", "succeeded"))

        oid_b, job_b, store_b = self._create_output(kind="narrate", run_id=rid_b, candidate_ids=cids_b)
        self._finish(store_b, oid_b, cids_b[0], "ok", "completed", text="story")
        self.fake.add(_gen_job(job_b, rid_b, oid_b, "narrate", "succeeded"))

        status, body, _ = self.get_status("/outputs")
        self.assertEqual(status, 200, body)
        self.assertIn(rid_a, body)
        self.assertIn(rid_b, body)
        # run heading uses catalog.history()'s experiment_name, not the raw run_id.
        self.assertIn("run: exp-out-a", body)
        self.assertIn("run: exp-out-b", body)
        self.assertIn("あらすじ生成", body)
        self.assertIn("上映生成", body)
        self.assertIn(f"/outputs/{oid_a}", body)

        status, body, _ = self.get_status(f"/outputs?run={rid_a}")
        self.assertEqual(status, 200, body)
        self.assertIn(oid_a, body)
        self.assertNotIn(oid_b, body)

        # Broken row: one output the store considers damaged must not take the
        # rest of the listing down, and must be shown distinctly (§ coordinator note).
        self.fake.broken_output_ids.add(oid_b)
        status, body, _ = self.get_status("/outputs")
        self.assertEqual(status, 200, body)
        self.assertIn("破損・要確認", body)
        self.assertIn(oid_b, body)
        self.assertIn(oid_a, body)  # the healthy row still renders
        self.fake.broken_output_ids.clear()

        # Total failure degrades to a 200 message instead of a 500.
        self.fake.outputs_raise = True
        status, body, _ = self.get_status("/outputs")
        self.assertEqual(status, 200, body)
        self.assertIn("作品一覧を読み込めません", body)
        self.fake.outputs_raise = False

    # ---------------------------------------------------------- output detail

    def test_output_detail_entries_and_absent(self):
        rid, cids = self._legacy_run("exp-detail", count=5)
        oid, jid, store = self._create_output(kind="narrate", run_id=rid, candidate_ids=cids)
        self._finish(store, oid, cids[0], "ok", "completed", text="Once upon a time.\n\nThe end.")
        self._finish(store, oid, cids[1], "prompt_only", "prompt_saved")
        self._finish(store, oid, cids[2], "error", "response_invalid", stage="validate",
                     call_state="response_received", retry_policy="safe_new_request", cause_type="ValueError")
        self._finish(store, oid, cids[3], "unknown", "dispatch_unknown", stage="receive",
                     call_state="started", retry_policy="explicit_confirmation")
        self._finish(store, oid, cids[4], "error", "persistence_failed", stage="persist",
                     call_state="response_received", retry_policy="local_recovery_only", cause_type="OSError")
        self.fake.add(_gen_job(jid, rid, oid, "narrate", "succeeded"))

        status, body, _ = self.get_status(f"/outputs/{oid}")
        self.assertEqual(status, 200, body)
        self.assertIn("Once upon a time.", body)
        self.assertIn("The end.", body)
        self.assertIn("本文は未生成です", body)
        self.assertIn("prompt for " + cids[1], body)  # prompt_only detail shows the prompt
        self.assertIn(output_pages.RETRY_LABELS["safe_new_request"], body)
        self.assertIn(output_pages.RETRY_LABELS["explicit_confirmation"], body)
        self.assertIn(output_pages.RETRY_LABELS["local_recovery_only"], body)
        self.assertIn("結果不明の候補を確認して再生成", body)
        self.assertIn("ack=1", body)
        self.assertIn('data-action="recover"', body)
        # Connection-lost notice for the polling JS (§ coordinator fix #2).
        self.assertIn('<p class="warning" data-connection-status hidden>', body)
        # limits are shown, and cell_key is in the entry heading (fix #5).
        self.assertIn("max_calls:", body)
        self.assertIn("call_timeout_seconds:", body)
        self.assertIn("C0|low", body)
        # href query strings must escape & as &amp; (fix #10): a raw "&mode="
        # must never appear (only the escaped "&amp;mode=" form should).
        self.assertIn("&amp;mode=", body)
        self.assertNotIn("&mode=", body.replace("&amp;mode=", ""))

        status, body, _ = self.get_status("/outputs/out-doesnotexist" + uuid.uuid4().hex[:8])
        self.assertEqual(status, 404, body)
        payload = json.loads(body)
        self.assertIn("code", payload)

    def test_output_detail_without_job_state(self):
        """An output whose owning job is unknown to this job store degrades to
        terminal=True (no polling) with only the [別の稿を作る] recourse."""
        rid, cids = self._legacy_run("exp-detail-nojob", count=1)
        oid, _jid, store = self._create_output(kind="synopsize", run_id=rid, candidate_ids=cids)
        self._finish(store, oid, cids[0], "ok", "completed", text="text")
        # Deliberately do not register a fake job for this output's job_id.

        status, body, _ = self.get_status(f"/outputs/{oid}")
        self.assertEqual(status, 200, body)
        self.assertIn("所有ジョブの記録がありません", body)
        self.assertIn('data-terminal="true"', body)
        self.assertIn("別の稿を作る", body)
        self.assertNotIn("結果不明の候補を確認して再生成", body)
        self.assertNotIn('data-action="recover"', body)

    def test_output_detail_narrate_shows_synopsis_ref(self):
        rid, cids = self._legacy_run("exp-detail-syn", count=1)
        syn_oid, syn_jid, syn_store = self._create_output(kind="synopsize", run_id=rid, candidate_ids=[cids[0]])
        self._finish(syn_store, syn_oid, cids[0], "ok", "completed", text="a synopsis")
        self.fake.add(_gen_job(syn_jid, rid, syn_oid, "synopsize", "succeeded"))
        syn_entry = OutputStore(self.control).sink(syn_oid, cids[0]).current()

        nar_oid, nar_jid, nar_store = self._create_output(
            kind="narrate", run_id=rid, candidate_ids=[cids[0]],
            synopsis_refs={cids[0]: {"output_id": syn_oid, "attempt_id": syn_entry["attempt_id"],
                                      "text_sha256": syn_entry["text_sha256"]}},
        )
        self._finish(nar_store, nar_oid, cids[0], "ok", "completed", text="the story")
        self.fake.add(_gen_job(nar_jid, rid, nar_oid, "narrate", "succeeded"))

        status, body, _ = self.get_status(f"/outputs/{nar_oid}")
        self.assertEqual(status, 200, body)
        self.assertIn("入力あらすじ", body)
        self.assertIn("あらすじ稿を見る", body)
        self.assertIn(f"/outputs/{syn_oid}#entry-{cids[0]}", body)

    # ------------------------------------------------------- entry text/prompt

    def test_entry_text_and_prompt_endpoints(self):
        rid, cids = self._legacy_run("exp-text", count=2)
        oid, jid, store = self._create_output(kind="synopsize", run_id=rid, candidate_ids=cids)
        entry = self._finish(store, oid, cids[0], "ok", "completed", text="the body text")
        self._finish(store, oid, cids[1], "prompt_only", "prompt_saved")
        self.fake.add(_gen_job(jid, rid, oid, "synopsize", "succeeded"))

        status, body, headers = self.get_status(f"/outputs/{oid}/entries/{cids[0]}/text")
        self.assertEqual(status, 200, body)
        self.assertEqual(body, "the body text")
        self.assertIn("text/plain", headers.get("Content-Type", ""))

        status, body, _ = self.get_status(f"/outputs/{oid}/entries/{cids[0]}/prompt")
        self.assertEqual(status, 200, body)
        self.assertEqual(body, "prompt for " + cids[0])

        # prompt_only entry has no body to serve.
        status, body, _ = self.get_status(f"/outputs/{oid}/entries/{cids[1]}/text")
        self.assertEqual(status, 404, body)

        # Tamper with the stored body so the SHA no longer matches: must not
        # be served, and the status must stay in the 4xx/5xx range.
        body_path = store.folder(oid) / entry["text_ref"]
        body_path.write_bytes(b"tampered")
        status, body, _ = self.get_status(f"/outputs/{oid}/entries/{cids[0]}/text")
        self.assertGreaterEqual(status, 400)
        self.assertNotEqual(body, "tampered")

    # --------------------------------------------------------- generate confirm

    def test_generate_confirm_synopsize(self):
        rid, cids = self._legacy_run("exp-gen-syn", count=2)
        path = f"/runs/{rid}/generate?kind=synopsize&config=cfg-test&candidate={cids[0]}&candidate={cids[1]}"
        status, first, _ = self.get_status(path)
        self.assertEqual(status, 200, first)
        status, second, _ = self.get_status(path)
        self.assertEqual(status, 200, second)

        match = re.search(r'data-request="([^"]+)"', first)
        self.assertIsNotNone(match, first)
        request = json.loads(__import__("html").unescape(match.group(1)))
        self.assertEqual(request["kind"], "synopsize")
        self.assertEqual(sorted(request["candidate_ids"]), sorted(cids))
        self.assertEqual(request["backend"], "none")
        self.assertIsNone(request["model"])
        self.assertEqual(request["limits"]["max_calls"], 0)
        self.assertEqual(request["selection_revision"], 0)
        self.assertEqual(request["synopsis_refs"], {})

        id1 = request["request_id"]
        match2 = re.search(r'data-request="([^"]+)"', second)
        request2 = json.loads(__import__("html").unescape(match2.group(1)))
        self.assertNotEqual(id1, request2["request_id"])
        self.assertEqual(self.fake.submitted, [])

        # Fix #13: planned call count and the config's own label are shown.
        self.assertIn("予定呼出し数: 2 件", first)
        self.assertIn(">wb</a>", first)  # config["label"] saved in setUp

    def test_generate_confirm_request_fields_match_output_requests(self):
        from execution import output_requests

        rid, cids = self._legacy_run("exp-gen-fields", count=1)
        status, body, _ = self.get_status(
            f"/runs/{rid}/generate?kind=synopsize&config=cfg-test&candidate={cids[0]}"
        )
        self.assertEqual(status, 200, body)
        match = re.search(r'data-request="([^"]+)"', body)
        self.assertIsNotNone(match, body)
        request = json.loads(__import__("html").unescape(match.group(1)))
        self.assertEqual(set(request.keys()), output_requests.FIELDS)

    def test_generate_confirm_unavailable_backend_has_no_start_button(self):
        rid, cids = self._legacy_run("exp-gen-unavail", count=1)
        original = self.configs.check_generation
        self.configs.check_generation = lambda cid, *, settings_path=None: {
            "backend": "codex-cli", "model": "gpt-5.6-sol", "available": False,
            "authentication": "unverified", "reason": "executable_missing", "limits": {},
        }
        try:
            status, body, _ = self.get_status(
                f"/runs/{rid}/generate?kind=synopsize&config=cfg-test&candidate={cids[0]}"
            )
        finally:
            self.configs.check_generation = original
        self.assertEqual(status, 200, body)
        self.assertNotIn("data-request=", body)
        self.assertNotIn("<button type=\"submit\">この内容で生成を開始</button>", body)
        self.assertIn("不可", body)
        self.assertIn("executable_missing", body)

    def test_generate_confirm_narrate_requires_adoption_then_uses_adopted(self):
        rid, cids = self._legacy_run("exp-gen-nar", count=2)

        # Not adopted yet: explicitly targeting it is a page-internal error, no button.
        status, body, _ = self.get_status(f"/runs/{rid}/generate?kind=narrate&config=cfg-test&candidate={cids[0]}")
        self.assertEqual(status, 200, body)
        self.assertIn("採用候補だけです", body)
        self.assertNotIn("data-request=", body)

        status, payload = self.http(
            "POST", f"/api/runs/{rid}/selection",
            {"expected_revision": 0, "changes": [{"candidate_id": cids[0], "state": "adopted"}]},
        )
        self.assertEqual(status, 200, payload)

        # Prepare an ok synopsize output so synopsis_refs auto-selects it.
        oid, jid, store = self._create_output(kind="synopsize", run_id=rid, candidate_ids=[cids[0]])
        self._finish(store, oid, cids[0], "ok", "completed", text="a synopsis")
        self.fake.add(_gen_job(jid, rid, oid, "synopsize", "succeeded"))

        status, body, _ = self.get_status(f"/runs/{rid}/generate?kind=narrate&config=cfg-test")
        self.assertEqual(status, 200, body)
        match = re.search(r'data-request="([^"]+)"', body)
        self.assertIsNotNone(match, body)
        request = json.loads(__import__("html").unescape(match.group(1)))
        self.assertEqual(request["candidate_ids"], [cids[0]])
        self.assertEqual(request["synopsis_refs"][cids[0]]["output_id"], oid)

    def test_generate_confirm_ack_unknown(self):
        rid, cids = self._legacy_run("exp-gen-ack", count=1)
        oid, jid, store = self._create_output(kind="synopsize", run_id=rid, candidate_ids=[cids[0]])
        self._finish(store, oid, cids[0], "unknown", "dispatch_unknown", stage="receive",
                     call_state="started", retry_policy="explicit_confirmation")
        self.fake.add(_gen_job(jid, rid, oid, "synopsize", "succeeded"))

        status, body, _ = self.get_status(
            f"/runs/{rid}/generate?kind=synopsize&config=cfg-test&candidate={cids[0]}&ack=1&from_output={oid}"
        )
        self.assertEqual(status, 200, body)
        self.assertIn("二重に課金される可能性があります", body)
        self.assertIn("data-ack", body)
        self.assertIn("required", body)
        match = re.search(r'data-request="([^"]+)"', body)
        request = json.loads(__import__("html").unescape(match.group(1)))
        self.assertTrue(request["acknowledge_unknown"])
        self.assertGreater(len(request["attempt_ids"]), 0)

    def test_generate_confirm_kind_and_legacy_config_errors(self):
        rid, _cids = self._legacy_run("exp-gen-err", count=1)
        status, body, _ = self.get_status(f"/runs/{rid}/generate?kind=bogus")
        self.assertEqual(status, 200, body)
        self.assertIn("生成種別", body)

        status, body, _ = self.get_status(f"/runs/{rid}/generate?kind=synopsize")
        self.assertEqual(status, 200, body)
        self.assertIn("文章化用の設定", body)
        self.assertNotIn("data-request=", body)

    # ----------------------------------------------------------- candidates

    def test_candidates_page_generate_form_and_draft_column(self):
        rid, cids = self._legacy_run("exp-cand-gen", count=2)
        oid, jid, store = self._create_output(kind="synopsize", run_id=rid, candidate_ids=[cids[0]])
        self._finish(store, oid, cids[0], "ok", "completed", text="text")
        self.fake.add(_gen_job(jid, rid, oid, "synopsize", "succeeded"))

        status, body, _ = self.get_status(f"/runs/{rid}/candidates")
        self.assertEqual(status, 200, body)
        self.assertIn('id="generate-form"', body)
        self.assertIn('name="kind" value="synopsize"', body)
        self.assertIn('name="kind" value="narrate"', body)
        self.assertIn(f'name="candidate" value="{cids[0]}" form="generate-form"', body)
        self.assertIn("あらすじ ok 1 / 上映 ok 0", body)
        self.assertIn(f"/outputs?run={rid}", body)
        # Narrate has no adopted candidates yet.
        row = body[body.index(f'name="kind" value="narrate"'):]
        self.assertIn("disabled", row.split("</button>")[0])

    def test_candidates_page_output_summary_error_note(self):
        rid, _cids = self._legacy_run("exp-cand-err", count=1)
        self.fake.outputs_raise = True
        try:
            status, body, _ = self.get_status(f"/runs/{rid}/candidates")
        finally:
            self.fake.outputs_raise = False
        self.assertEqual(status, 200, body)
        self.assertIn("稿の記録を読み取れないため件数を表示できません", body)

    def test_candidates_page_narrate_button_reflects_unfiltered_adoption(self):
        rid, cids = self._legacy_run("exp-cand-adopt-filter", count=2)
        status, payload = self.http(
            "POST", f"/api/runs/{rid}/selection",
            {"expected_revision": 0, "changes": [{"candidate_id": cids[0], "state": "adopted"}]},
        )
        self.assertEqual(status, 200, payload)

        # A filter that hides the adopted candidate from the visible rows must
        # not disable narrate (fix #14: reflects the unfiltered selection).
        status, body, _ = self.get_status(f"/runs/{rid}/candidates?generation=999")
        self.assertEqual(status, 200, body)
        self.assertNotIn(f'data-candidate-id="{cids[0]}"', body)  # filtered out of the table
        row = body[body.index('name="kind" value="narrate"'):]
        self.assertNotIn("disabled", row.split("</button>")[0])

    # -------------------------------------------------------------- jobs

    def test_jobs_integration(self):
        rid, cids = self._legacy_run("exp-jobs-gen", count=1)
        oid, jid, store = self._create_output(kind="narrate", run_id=rid, candidate_ids=cids)
        self._finish(store, oid, cids[0], "ok", "completed", text="body")
        self.fake.add(_gen_job(jid, rid, oid, "narrate", "succeeded",
                                counts={"ok": 1}, completion_kind="generated"))

        status, body, _ = self.get_status(f"/jobs/{jid}")
        self.assertEqual(status, 200, body)
        self.assertIn("上映生成", body)
        self.assertIn(f"/outputs/{oid}", body)
        self.assertIn(output_pages.COMPLETION_LABELS["generated"], body)

        status, body, _ = self.get_status("/jobs")
        self.assertEqual(status, 200, body)
        self.assertIn("生成ジョブ", body)
        self.assertIn(jid, body)

    # --------------------------------------------------------------- CSP

    def test_csp_no_inline_handlers(self):
        rid, cids = self._legacy_run("exp-csp-out", count=1)
        oid, jid, store = self._create_output(kind="synopsize", run_id=rid, candidate_ids=cids)
        self._finish(store, oid, cids[0], "ok", "completed", text="text")
        self.fake.add(_gen_job(jid, rid, oid, "synopsize", "succeeded"))

        paths = [
            "/outputs", f"/outputs/{oid}", f"/runs/{rid}/candidates",
            f"/runs/{rid}/generate?kind=synopsize&config=cfg-test&candidate={cids[0]}",
            f"/jobs/{jid}", "/jobs",
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

    # ---------------------------------------------------------- vocabulary

    def test_entry_status_vocabulary(self):
        for status, label in output_pages.ENTRY_STATUS_LABELS.items():
            with self.subTest(status=status):
                self.assertIn(label, output_pages._entry_badge(status))

    def test_completion_vocabulary(self):
        for kind, label in output_pages.COMPLETION_LABELS.items():
            with self.subTest(kind=kind):
                job = _gen_job("job-vocab", "run-vocab", "out-vocab", "synopsize", "succeeded",
                                completion_kind=kind)
                self.assertIn(label, output_pages.render_generation_job(job))

    def test_retry_policy_vocabulary(self):
        store = OutputStore(self.control)
        for policy, label in output_pages.RETRY_LABELS.items():
            if not label:
                continue
            with self.subTest(policy=policy):
                entry = {"candidate_id": "cand-vocab", "status": "error", "retry_policy": policy,
                         "message": "m", "code": "c", "stage": "s", "cause_type": None, "usage": None}
                html = output_pages._entry_article(entry, {}, store, "out-vocab", "run-vocab")
                self.assertIn(label, html)

    def test_kind_vocabulary(self):
        for kind, label in output_pages.KIND_LABELS.items():
            with self.subTest(kind=kind):
                job = _gen_job("job-vocab2", "run-vocab", "out-vocab", kind, "running")
                self.assertIn(label, output_pages.render_generation_job(job))


if __name__ == "__main__":
    unittest.main()
