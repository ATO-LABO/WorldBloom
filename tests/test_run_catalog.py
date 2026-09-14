"""UI005 candidate provenance, immutable selection and legacy compatibility."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from execution.provenance import ConfigError, atomic_json, canonical, read_json, sha256
from execution.selections import SelectionStore, SelectionError
from execution.evolution_worker import EvolutionObserver
from gapengine.evolve import evolve
from viewer.run_catalog import RunCatalog, read_publication, validate_candidates
from viewer.data import RunRepository
from viewer.server import ViewerServer, ViewerHandler
from test_gapengine import make_reaching_project, TEMPLATE


def saved(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and p.name != ".write.lock"}


def process_selection(runs, control, rid, cid, queue):
    try:
        store = SelectionStore(RunCatalog(runs, control))
        store.update(rid, [{"candidate_id": cid, "state": "held"}], expected_revision=0)
        queue.put("saved")
    except ConfigError as error:
        import traceback
        queue.put({"code": error.code, "detail": error.as_dict(), "traceback": traceback.format_exc()})


def process_register(args):
    runs, control, name = args
    try:
        return {"status": "saved", "run_id": RunCatalog(runs, control).register_legacy(name)}
    except ConfigError as error:
        return error.as_dict()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui005-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.runs = self.base / "runs"; self.runs.mkdir()
        self.control = self.base / "control"
        self.catalog = RunCatalog(self.runs, self.control)
        self.store = SelectionStore(self.catalog)

    def legacy(self, name="old", missing=False):
        root = self.runs / name; root.mkdir()
        if not missing:
            (root / "a.jsonl").write_bytes(b'{"kind":"a"}\n')
        (root / "b.jsonl").write_bytes(b'{"kind":"b"}\n')
        archive = {"cells": {key: {"generation": g, "quality": 0.5, "reach_rate": 1.0,
            "exemplar": {"seed": g, "layers_path": path}, "parents": [], "genome": {}}
            for key, g, path in (("I|low", 0, "a.jsonl"), ("VI|high", 1, "b.jsonl"))}}
        atomic_json(root / "archive.json", archive)
        return root, self.catalog.register_legacy(name)

    def test_read_only_legacy_registration_and_history(self):
        root, rid = self.legacy()
        before = saved(root)
        first = self.catalog.snapshot(rid)
        self.assertEqual(self.catalog.register_legacy(root.name), rid)
        self.assertEqual(first, self.catalog.snapshot(rid))
        history = self.catalog.history()
        self.assertEqual(history[0]["state"], "legacy")
        self.assertIsNone(history[0]["settings"]["evolution"]["generations"])
        self.assertEqual(len(first["candidates"]["candidates"]), 2)
        self.assertEqual(set(first["archive"]["cells"]), {"I|low", "VI|high"})
        self.assertEqual(saved(root), before)
        self.assertFalse((root / ".write.lock").exists())

    def test_read_publication_caches_by_pointer_stat_and_invalidates_on_refresh(self):
        root, rid = self.legacy()
        folder = self.catalog.legacy / rid
        import viewer.run_catalog as module
        with patch.object(module, "verified_json", wraps=module.verified_json) as verified:
            first = read_publication(folder, rid)
            count_after_first = verified.call_count
            second = read_publication(folder, rid)
            self.assertEqual(second, first)
            self.assertEqual(verified.call_count, count_after_first)
        self.catalog.register_legacy(root.name, refresh=True)
        newer = read_publication(folder, rid)
        self.assertGreater(newer["revision"], first["revision"])

    def test_missing_import_never_promoted_without_explicit_refresh(self):
        root, rid = self.legacy(missing=True)
        old = self.catalog.snapshot(rid)
        cid = old["candidates"]["representatives"]["I|low"]
        original = next(c for c in old["candidates"]["candidates"] if c["candidate_id"] == cid)
        self.assertEqual(original["identity"]["scheme"], "legacy-missing-v1")
        self.assertIsNone(original["source_log_sha256"])
        self.store.update(rid, [{"candidate_id": cid, "state": "held", "note": "未記録"}], expected_revision=0)
        (root / "a.jsonl").write_bytes(b"found")
        self.catalog.register_legacy(root.name)
        later = self.catalog.snapshot(rid)
        self.assertEqual(later["revision"], old["revision"])
        self.assertEqual(later["candidates"]["representatives"]["I|low"], cid)
        self.assertFalse(next(c for c in later["candidates"]["candidates"] if c["candidate_id"] == cid)["screenable"])
        self.catalog.register_legacy(root.name, refresh=True)
        newest = self.catalog.snapshot(rid)
        new_id = newest["candidates"]["representatives"]["I|low"]
        self.assertNotEqual(new_id, cid)
        self.assertEqual(next(c for c in newest["candidates"]["candidates"] if c["candidate_id"] == new_id)["related_candidate_id"], cid)
        self.assertEqual(self.store.get(rid)["entries"][0]["candidate_id"], cid)

    def test_log_change_missing_and_permission_are_distinct(self):
        root, rid = self.legacy()
        snapshot = self.catalog.snapshot(rid)
        frozen = saved(self.catalog.legacy / rid)
        cid = snapshot["candidates"]["representatives"]["I|low"]
        source = next(c["source_log_sha256"] for c in snapshot["candidates"]["candidates"] if c["candidate_id"] == cid)
        (root / "a.jsonl").write_bytes(b"changed")
        stale = next(c for c in self.catalog.candidates(rid)["candidates"] if c["candidate_id"] == cid)
        self.assertEqual(stale["log"]["availability"], "stale")
        self.assertEqual(stale["source_log_sha256"], source)
        original = Path.read_bytes
        def denied(path):
            if path == root / "a.jsonl":
                raise PermissionError("denied")
            return original(path)
        with patch.object(Path, "read_bytes", denied), self.assertRaises(PermissionError):
            self.catalog.snapshot(rid)
        self.assertEqual(saved(self.catalog.legacy / rid), frozen)

    def test_archive_replacement_preserves_selection_and_reuses_frozen_import(self):
        root, rid = self.legacy()
        original = (root / "archive.json").read_bytes()
        first = self.catalog.snapshot(rid)
        old_id = first["candidates"]["representatives"]["I|low"]
        selected = self.store.update(rid, [{"candidate_id": old_id, "state": "adopted", "note": "old"}], expected_revision=0)
        pinned = (self.control / f"selections/{rid}/revisions/{selected['revision']}.json").read_bytes()
        archive = json.loads(original); archive["cells"]["I|low"]["quality"] = 0.9
        atomic_json(root / "archive.json", archive)
        self.catalog.register_legacy(root.name)
        second = self.catalog.snapshot(rid)
        self.assertNotEqual(second["candidates"]["representatives"]["I|low"], old_id)
        self.assertEqual(self.store.projected(rid), set())
        self.store.toggle(rid, "VI|high", True)
        self.assertEqual(next(e for e in self.store.get(rid)["entries"] if e["candidate_id"] == old_id)["note"], "old")
        self.assertEqual((self.control / f"selections/{rid}/revisions/{selected['revision']}.json").read_bytes(), pinned)
        (root / "archive.json").write_bytes(original)
        self.catalog.register_legacy(root.name)
        self.assertEqual(self.catalog.snapshot(rid)["candidates"]["representatives"]["I|low"], old_id)

    def test_patch_revision_conflict_and_old_toggle_preserves_notes_and_states(self):
        root, rid = self.legacy()
        snap = self.catalog.snapshot(rid)
        a = snap["candidates"]["representatives"]["I|low"]
        b = snap["candidates"]["representatives"]["VI|high"]
        first = self.store.update(rid, [{"candidate_id": a, "state": "held", "note": "比較"},
                                        {"candidate_id": b, "state": "rejected", "note": "除外理由"}], expected_revision=0)
        with self.assertRaises(SelectionError) as caught:
            self.store.update(rid, [{"candidate_id": a, "note": "古い更新"}], expected_revision=0)
        self.assertEqual(caught.exception.as_dict()["current_revision"], first["revision"])
        self.store.toggle(rid, "I|low", True)
        self.store.toggle(rid, "I|low", False)
        current = {e["candidate_id"]: e for e in self.store.get(rid)["entries"]}
        self.assertEqual(current[a], {"candidate_id": a, "state": "unclassified", "note": "比較"})
        self.assertEqual(current[b]["state"], "rejected")
        self.assertEqual(current[b]["note"], "除外理由")
        self.assertEqual(read_json(root / "selection.json"), {"selected": []})
        old = self.store.get(rid, revision=first["revision"], sha256=first["sha256"])
        self.assertEqual(old["entries"], first["entries"])

    def test_parallel_expected_revision_and_legacy_toggles(self):
        root, rid = self.legacy()
        ids = list(self.catalog.snapshot(rid)["candidates"]["representatives"].values())
        def write(cid):
            try:
                return self.store.update(rid, [{"candidate_id": cid, "state": "held"}], expected_revision=0)
            except ConfigError as e:
                return e
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(write, ids))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual(sum(isinstance(r, ConfigError) for r in results), 1)
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda cell: self.store.toggle(rid, cell, True), ["I|low", "VI|high"]))
        self.assertEqual(self.store.projected(rid), {"I|low", "VI|high"})

    def test_projection_failure_is_not_success_and_can_reconcile(self):
        root, rid = self.legacy()
        cid = self.catalog.snapshot(rid)["candidates"]["representatives"]["I|low"]
        import execution.selections as module
        original = module.atomic_json
        def fail(path, value):
            if path == root / "selection.json":
                raise OSError("disk")
            return original(path, value)
        with patch.object(module, "atomic_json", fail), self.assertRaises(SelectionError) as caught:
            self.store.update(rid, [{"candidate_id": cid, "state": "adopted"}], expected_revision=0)
        self.assertEqual(caught.exception.code, "projection_pending")
        self.assertEqual(self.store.get(rid)["revision"], 1)
        self.assertEqual(self.store.repair_projection(rid), {"I|low"})
        self.assertEqual(read_json(root / "selection.json"), {"selected": ["I|low"]})

    def test_pointer_failure_keeps_previous_and_preserves_orphan(self):
        root, rid = self.legacy()
        cid = self.catalog.snapshot(rid)["candidates"]["representatives"]["I|low"]
        first = self.store.update(rid, [{"candidate_id": cid, "note": "first"}], expected_revision=0)
        import execution.selections as module
        original = module.atomic_json
        def fail(path, value):
            if path.name == "current.json":
                raise OSError("pointer disk")
            return original(path, value)
        with patch.object(module, "atomic_json", fail), self.assertRaises(OSError):
            self.store.update(rid, [{"candidate_id": cid, "note": "orphan"}], expected_revision=1)
        self.assertEqual(self.store.get(rid), first)
        orphan = self.control / f"selections/{rid}/revisions/2.json"
        before = orphan.read_bytes()
        third = self.store.update(rid, [{"candidate_id": cid, "note": "third"}], expected_revision=1)
        self.assertEqual(third["revision"], 3)
        self.assertEqual(third["parent_revision"], 1)
        self.assertEqual(orphan.read_bytes(), before)
        with self.assertRaises(ConfigError):
            self.store.get(rid, revision=2, sha256=sha256(before))

    def test_invalid_adoption_and_patch_types(self):
        root, rid = self.legacy(missing=True)
        cid = self.catalog.snapshot(rid)["candidates"]["representatives"]["I|low"]
        for changes, expected in [([{"candidate_id": cid, "state": "adopted"}], 0),
                ([{"candidate_id": cid, "note": 1}], 0), ([{"candidate_id": cid, "note": ""}], True),
                ([{"candidate_id": "cand-unknown", "state": "held"}], 0),
                ([{"candidate_id": cid, "state": "unknown"}], 0),
                ([{"candidate_id": cid}, {"candidate_id": cid}], 0)]:
            with self.subTest(changes=changes, expected=expected), self.assertRaises(ConfigError):
                self.store.update(rid, changes, expected_revision=expected)
        self.assertFalse((self.control / f"selections/{rid}/current.json").exists())
        self.store.update(rid, [{"candidate_id": cid, "state": "held", "note": "欠損"}], expected_revision=0)
        self.assertFalse(self.store.tray()[0]["screenable"])

    def test_tampered_publication_and_paths_are_rejected(self):
        root, rid = self.legacy()
        base = self.catalog.legacy / rid
        path = base / "published/1/candidates.json"
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaises(ConfigError):
            self.catalog.snapshot(rid)
        path.write_bytes(original)
        pointer = read_json(base / "published/current.json")
        pointer["revision"] = True
        atomic_json(base / "published/current.json", pointer)
        with self.assertRaises(ConfigError):
            self.catalog.snapshot(rid)
        for name in ("../outside", "a/b", "a\\b"):
            with self.assertRaises(ConfigError):
                self.catalog.register_legacy(name)

    def test_tray_keeps_separate_run_ids(self):
        _, one = self.legacy("one")
        _, two = self.legacy("two")
        for rid in (one, two):
            self.store.toggle(rid, "I|low", True)
        tray = self.store.tray()
        self.assertEqual({c["run_id"] for c in tray}, {one, two})
        self.assertEqual(len({c["candidate_id"] for c in tray}), 2)

    def test_real_ui004_publication_and_terminal_gate(self):
        project = make_reaching_project(self.base)
        rid = "run-real"; root = self.runs / rid
        cfg = {"project": project, "template": TEMPLATE, "out": root, "generations": 2,
               "population": 2, "seeds": 2, "seed_base": 0, "ga_seed": 9,
               "coevolve": True, "processes": 1, "keep": "exemplar"}
        with EvolutionObserver(root, rid, cfg) as observer:
            evolve(cfg, observer=observer)
        manifest = {"schema_version": 1, "run_id": rid, "job_id": "job-real", "config_id": "cfg-test",
                    "evolution": {"generations": 2}}
        atomic_json(root / "manifest.json", manifest)
        atomic_json(root / "complete.json", {"manifest_sha256": sha256(canonical(manifest))})
        job = {"run_id": rid, "job_id": "job-real", "state": "running"}
        atomic_json(self.control / "jobs/job-real/job.json", job)
        snapshot = self.catalog.snapshot(rid)
        self.assertEqual(snapshot["revision"], 2)
        self.assertEqual(len(snapshot["candidates"]["candidates"]), 16)
        invalid_coordinate = deepcopy(snapshot["candidates"]["candidates"])
        invalid_coordinate[0]["generation"] = False
        with self.assertRaises(ConfigError):
            validate_candidates(invalid_coordinate, rid)
        self.assertEqual({c["role"] for c in snapshot["candidates"]["candidates"]}, {"protagonist", "antagonist"})
        self.assertTrue(any(c["log"]["availability"] == "pruned" for c in snapshot["candidates"]["candidates"]))
        reps = self.catalog.representatives(snapshot)
        self.assertTrue(reps)
        with self.assertRaises(SelectionError):
            self.store.toggle(rid, next(iter(reps)), True)
        job["state"] = "cancelled"; atomic_json(self.control / "jobs/job-real/job.json", job)
        pub_before = saved(root / "published")
        self.store.toggle(rid, next(iter(reps)), True)
        self.assertEqual(saved(root / "published"), pub_before)
        root_archive = read_json(root / "archive.json")
        atomic_json(root / "archive.json", {"cells": {"partial": {}}})
        repo = RunRepository(self.runs, control_root=self.control)
        self.assertEqual(repo.archive(root), root_archive)
        self.assertEqual(len(self.catalog.candidates(rid, role="antagonist")["candidates"]), 8)

    def test_queued_jobs_without_archive_are_in_history(self):
        class Jobs:
            def list(self):
                return [{"run_id": "new", "job_id": "j", "state": "queued", "phase": "preparing"},
                        {"run_id": "failed", "job_id": "j2", "state": "failed", "phase": "preparing"}]
        self.legacy()
        catalog = RunCatalog(self.runs, self.control, Jobs())
        self.assertEqual({r["state"] for r in catalog.history()}, {"queued", "failed", "legacy"})

    def assert_real_preparation_keeps_history_available(self, *, interrupt):
        from test_execution_configs import ConfigTests
        from execution.provenance import publish_directory

        config = ConfigTests()
        config.setUp()
        self.addCleanup(config.doCleanups)
        config.runtime()
        config.save()
        config.store.runs = self.runs
        root, rid = self.legacy()
        cid = self.catalog.snapshot(rid)["candidates"]["representatives"]["I|low"]
        self.store.update(rid, [{"candidate_id": cid, "state": "adopted", "note": "保持する"}],
                          expected_revision=0)
        original = saved(root)
        legacy_before = saved(self.catalog.legacy)
        job = {"run_id": "run-pending", "job_id": "job-pending", "state": "queued", "phase": "preparing"}
        atomic_json(self.control / "jobs/job-pending/job.json", job)
        server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        server.repository = RunRepository(self.runs, control_root=self.control)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02})
        thread.start()

        def request(path):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("GET", path, headers={"X-WorldBloom-Client": "1"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        def available(state):
            status, history = request("/api/runs")
            self.assertEqual(status, 200, history)
            records = {record["run_id"]: record for record in history["runs"]}
            self.assertEqual(set(records), {rid, "run-pending"})
            self.assertEqual(records["run-pending"]["state"], state)
            self.assertEqual(records["run-pending"]["phase"], "preparing")
            self.assertFalse(any(r["experiment_name"].startswith(".pending-") for r in records.values()))
            status, tray = request("/api/selected")
            self.assertEqual(status, 200, tray)
            self.assertEqual(len(tray["candidates"]), 1)
            return tray

        staged = []
        try:
            before = available("queued")

            def at_publish(staging, final):
                self.assertTrue(staging.name.startswith(".pending-"))
                self.assertTrue((staging / "complete.json").is_file())
                self.assertEqual(read_json(staging / "manifest.json")["run_id"], "run-pending")
                self.assertFalse(final.exists())
                staged.append(staging)
                pending_before = saved(staging)
                self.assertEqual(available("queued"), before)
                self.assertEqual(saved(staging), pending_before)
                if interrupt:
                    raise OSError("test publication interrupted")
                publish_directory(staging, final)

            with patch("execution.configs.publish_directory", side_effect=at_publish):
                if interrupt:
                    with self.assertRaisesRegex(OSError, "test publication interrupted"):
                        config.store.prepare_run("cfg-test", run_id="run-pending", job_id="job-pending")
                else:
                    config.store.prepare_run("cfg-test", run_id="run-pending", job_id="job-pending")
            self.assertEqual(len(staged), 1)
            self.assertEqual(staged[0].exists(), interrupt)
            if interrupt:
                self.assertFalse((self.runs / "run-pending").exists())
                job["state"] = "failed"
                atomic_json(self.control / "jobs/job-pending/job.json", job)
            self.assertEqual(available(job["state"]), before)
            self.assertEqual(saved(root), original)
            self.assertEqual(saved(self.catalog.legacy), legacy_before)
            entries = self.store.get(rid)["entries"]
            self.assertEqual(entries, [{"candidate_id": cid, "state": "adopted", "note": "保持する"}])
            if not interrupt:
                # Public manifests must still fail closed; only internal staging is excluded.
                manifest = self.runs / "run-pending/manifest.json"
                manifest.write_bytes(manifest.read_bytes() + b" ")
                for path in ("/api/runs", "/api/selected"):
                    status, error = request(path)
                    self.assertEqual(status, 422, error)
                    self.assertEqual(error["code"], "snapshot_changed")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_real_prepare_publication_keeps_history_and_tray_available(self):
        self.assert_real_preparation_keeps_history_available(interrupt=False)

    def test_interrupted_real_prepare_keeps_history_and_tray_available(self):
        self.assert_real_preparation_keeps_history_available(interrupt=True)

    def test_pending_archive_is_not_registered_as_a_legacy_experiment(self):
        self.legacy()
        pending = self.runs / ".pending-archive"; pending.mkdir()
        atomic_json(pending / "archive.json", {"cells": {}})
        before = saved(pending)
        registrations = saved(self.catalog.legacy)
        history = self.catalog.history()
        self.assertEqual([r["experiment_name"] for r in history], ["old"])
        self.assertEqual(saved(self.catalog.legacy), registrations)
        self.assertEqual(saved(pending), before)

    def test_legacy_star_http_with_control_preserves_new_notes(self):
        root, rid = self.legacy()
        snap = self.catalog.snapshot(rid)
        other = snap["candidates"]["representatives"]["VI|high"]
        self.store.update(rid, [{"candidate_id": other, "state": "held", "note": "保持する"}], expected_revision=0)
        server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        server.repository = RunRepository(self.runs, control_root=self.control)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02})
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("POST", "/exp/old/selection", json.dumps({"cell": "I|low", "selected": True}),
                                   {"Content-Type": "application/json"})
                response = connection.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, 200, body)
                self.assertEqual(body["selected_cells"], ["I|low"])
            finally:
                connection.close()
            entries = self.store.get(rid)["entries"]
            self.assertEqual(next(e for e in entries if e["candidate_id"] == other)["note"], "保持する")
            self.assertEqual(read_json(root / "selection.json"), {"selected": ["I|low"]})
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertFalse(thread.is_alive())


    def test_json_api_history_candidates_selection_conflict_and_boundary(self):
        root, rid = self.legacy()
        server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        server.repository = RunRepository(self.runs, control_root=self.control)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02})
        thread.start()
        def request(method, path, body=None, client=True):
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
        try:
            status, history = request("GET", "/api/runs")
            self.assertEqual(status, 200)
            self.assertEqual(history["runs"][0]["run_id"], rid)
            status, candidates = request("GET", f"/api/runs/{rid}/candidates?state=unclassified")
            self.assertEqual(status, 200)
            self.assertEqual(len(candidates["candidates"]), 2)
            cid = candidates["candidates"][0]["candidate_id"]
            body = {"expected_revision": 0, "changes": [{"candidate_id": cid, "state": "adopted", "note": "HTTP"}]}
            for _ in range(20):
                status, denied = request("POST", f"/api/runs/{rid}/selection", body, client=False)
                self.assertEqual(status, 403, denied)
            status, selected = request("POST", f"/api/runs/{rid}/selection", body)
            self.assertEqual(status, 200, selected)
            self.assertEqual(selected["revision"], 1)
            status, conflict = request("POST", f"/api/runs/{rid}/selection", body)
            self.assertEqual(status, 409)
            self.assertEqual(conflict["current_revision"], 1)
            self.assertEqual(request("GET", f"/api/runs/{rid}/selection")[1]["revision"], 1)
            self.assertEqual(len(request("GET", "/api/selected")[1]["candidates"]), 1)
            self.assertEqual(len(request("GET", f"/api/runs/{rid}/candidates?state=adopted")[1]["candidates"]), 1)
            self.assertEqual(request("GET", f"/api/runs/{rid}/candidates?generation=true")[0], 422)
            self.assertEqual(request("GET", f"/api/runs/{rid}/candidates?role=invalid")[0], 422)
            self.assertEqual(request("GET", f"/api/runs/{rid}/candidates?other=1")[0], 422)
            self.assertEqual(request("GET", "/api/runs/unknown/candidates")[0], 404)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_legacy_selected_import_and_unreached_notes(self):
        root, rid = self.legacy()
        atomic_json(root / "selection.json", {"selected": ["VI|high"]})
        cid = self.catalog.snapshot(rid)["candidates"]["representatives"]["VI|high"]
        self.assertEqual(self.store.get(rid)["entries"], [{"candidate_id": cid, "state": "adopted", "note": ""}])
        archive = read_json(root / "archive.json")
        archive["cells"]["I|low"]["reach_rate"] = 0
        atomic_json(root / "archive.json", archive)
        self.catalog.register_legacy(root.name)
        newer = self.catalog.snapshot(rid)
        unreached = newer["candidates"]["representatives"]["I|low"]
        self.store.update(rid, [{"candidate_id": unreached, "note": "未到達の比較"}], expected_revision=0)
        with self.assertRaises(SelectionError):
            self.store.update(rid, [{"candidate_id": unreached, "state": "adopted"}], expected_revision=1)
        self.assertEqual(len(self.catalog.candidates(rid, reached=False)["candidates"]), 1)

    def test_unpublished_manifest_stays_out_of_legacy_grid_but_in_history(self):
        root = self.runs / "run-preparing"; root.mkdir()
        manifest = {"schema_version": 1, "run_id": root.name, "job_id": "job-preparing"}
        atomic_json(root / "manifest.json", manifest)
        atomic_json(root / "complete.json", {"manifest_sha256": sha256(canonical(manifest))})
        atomic_json(root / "archive.json", {"cells": {"partial": {}}})
        atomic_json(self.control / "jobs/job-preparing/job.json",
                    {"run_id": root.name, "job_id": "job-preparing", "state": "running"})
        repo = RunRepository(self.runs, control_root=self.control)
        self.assertEqual(repo.experiments(), [])
        self.assertEqual(self.catalog.history()[0]["state"], "running")
        self.assertIsNone(self.catalog.history()[0]["publication_revision"])

    def test_processes_cannot_overwrite_the_same_selection_revision(self):
        import multiprocessing
        _, rid = self.legacy()
        cid = self.catalog.snapshot(rid)["candidates"]["representatives"]["I|low"]
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        processes = [ctx.Process(target=process_selection, args=(str(self.runs), str(self.control), rid, cid, queue))
                     for _ in range(2)]
        try:
            for process in processes:
                process.start()
            results = [queue.get(timeout=30) for _ in processes]
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sorted(r["code"] if isinstance(r, dict) else r for r in results),
                             ["conflict", "saved"], results)
            self.assertEqual(self.store.get(rid)["revision"], 1)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=10)
                process.close()
            queue.close(); queue.join_thread()

    def test_concurrent_first_legacy_registration_uses_one_frozen_import(self):
        import multiprocessing
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(2) as pool:
            for index in range(5):
                root = self.runs / f"first-{index}"; root.mkdir()
                atomic_json(root / "archive.json", {"cells": {"I|low": {"reach_rate": 0}}})
                before = saved(root)
                results = pool.map(process_register, [(str(self.runs), str(self.control), root.name)] * 2)
                self.assertTrue(any(r.get("status") == "saved" for r in results), results)
                self.assertTrue(all(r.get("status") == "saved" or r.get("code") == "conflict" for r in results), results)
                ids = {r["run_id"] for r in results if r.get("status") == "saved"}
                self.assertEqual(len(ids), 1)
                rid = ids.pop()
                self.assertEqual(self.catalog.register_legacy(root.name), rid)
                self.assertEqual(self.catalog.snapshot(rid)["revision"], 1)
                self.assertEqual(saved(root), before)


if __name__ == "__main__":
    unittest.main()
