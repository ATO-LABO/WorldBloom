"""WB-UI-010 Stage 2: LibraryStore file I/O/validation and its POST boundary.

LibraryStore never re-implements semantic validation: validate() must call
straight through to the already-tested execution.configs.ConfigStore.preview.
"""
from __future__ import annotations

from pathlib import Path
import http.client
import json
import shutil
import tempfile
import threading
import unittest

import yaml

from execution.configs import ConfigStore
from execution.jobs import JobStore
from execution.library import LibraryStore
from execution.output_store import OutputStore
from execution.provenance import ConfigError, sha256
from viewer.data import RunRepository
from viewer.server import ViewerHandler, ViewerServer

ROOT = Path(__file__).resolve().parents[1]


class LibraryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui010-lib-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        for name in ("projects", "templates"):
            shutil.copytree(ROOT / name, self.repo / name)
        self.store = LibraryStore(self.repo)
        self.configs = ConfigStore(self.repo, Path(self.temp.name) / "control", Path(self.temp.name) / "runs")

    # ---------------------------------------------------------- create_world

    def test_create_world_copies_and_rewrites_only_name_and_gapengine(self):
        original = yaml.safe_load((self.repo / "projects/momotaro/world.yaml").read_text(encoding="utf-8"))
        original_subjects = sorted(p.name for p in (self.repo / "projects/momotaro/subjects").glob("*.yaml"))

        new_id = self.store.create_world("clone1", from_id="momotaro", genre_id="momotaro", name="クローン太郎")
        self.assertEqual(new_id, "clone1")

        dest = self.repo / "projects/clone1"
        self.assertTrue(dest.is_dir())
        copied = yaml.safe_load((dest / "world.yaml").read_text(encoding="utf-8"))
        self.assertEqual(copied["name"], "クローン太郎")
        self.assertEqual(copied["gapengine"]["action_graph"], "templates/momotaro/action_graph.yaml")
        self.assertEqual(copied["gapengine"]["effects"], "templates/momotaro/effects.yaml")
        # Every other top-level key is preserved verbatim.
        for key in original:
            if key in ("name", "gapengine"):
                continue
            self.assertEqual(copied[key], original[key], key)
        copied_subjects = sorted(p.name for p in (dest / "subjects").glob("*.yaml"))
        self.assertEqual(copied_subjects, original_subjects)

    def test_create_world_rejects_existing_id(self):
        with self.assertRaises(ConfigError) as ctx:
            self.store.create_world("momotaro", from_id="momotaro", genre_id="momotaro", name="x")
        self.assertEqual(ctx.exception.code, "conflict")
        self.assertFalse((self.repo / "projects/momotaro-tmp").exists())

    def test_create_world_rejects_invalid_id(self):
        with self.assertRaises(ConfigError):
            self.store.create_world("bad id!", from_id="momotaro", genre_id="momotaro", name="x")
        self.assertFalse((self.repo / "projects/bad id!").exists())

    def test_create_world_cleans_up_on_failure(self):
        # A genre that does not exist fails after the copy has already
        # happened; the half-made project directory must not survive.
        with self.assertRaises(ConfigError):
            self.store.create_world("clone-fail", from_id="momotaro", genre_id="no-such-genre", name="x")
        self.assertFalse((self.repo / "projects/clone-fail").exists())

    # -------------------------------------------------------------- write()

    def test_write_rejects_path_traversal(self):
        with self.assertRaises(ConfigError):
            self.store.write("world", "momotaro", "../evil.yaml", "x: 1")
        with self.assertRaises(ConfigError):
            self.store.write("world", "momotaro", "subjects/../../evil.yaml", "x: 1")

    def test_write_rejects_unlisted_file(self):
        with self.assertRaises(ConfigError):
            self.store.write("world", "momotaro", "not-allowed.yaml", "x: 1")
        with self.assertRaises(ConfigError):
            self.store.write("genre", "momotaro", "not-allowed.yaml", "x: 1")

    def test_write_rejects_oversized_content(self):
        huge = "x: " + ("a" * (260 * 1024))
        with self.assertRaises(ConfigError):
            self.store.write("world", "momotaro", "world.yaml", huge)

    def test_write_rejects_broken_yaml(self):
        with self.assertRaises(ConfigError):
            self.store.write("world", "momotaro", "world.yaml", "foo: [unclosed")

    def test_write_round_trips_valid_content(self):
        original = self.store.read("genre", "momotaro", "rules.yaml")
        written = self.store.write("genre", "momotaro", "rules.yaml", original)
        self.assertEqual(written, len(original.encode("utf-8")))
        self.assertEqual(self.store.read("genre", "momotaro", "rules.yaml"), original)

    # ------------------------------------------------------------ validate()

    def test_validate_calls_preview_and_surfaces_missing_protagonist(self):
        shutil.copytree(self.repo / "projects/momotaro", self.repo / "projects/broken")
        world = yaml.safe_load((self.repo / "projects/broken/world.yaml").read_text(encoding="utf-8"))
        world["protagonist"] = "誰でもない"
        (self.repo / "projects/broken/world.yaml").write_text(
            yaml.safe_dump(world, allow_unicode=True, sort_keys=False), encoding="utf-8")

        with self.assertRaises(ConfigError) as ctx:
            self.store.validate(self.configs, world_id="broken", genre_id="momotaro")
        self.assertIn("inputs.subjects", ctx.exception.field_errors)

    def test_validate_matches_preview_for_a_valid_world(self):
        result = self.store.validate(self.configs, world_id="momotaro", genre_id="momotaro")
        self.assertEqual(result["world_name"], "桃太郎")
        self.assertEqual(result["protagonist"], "桃太郎")
        self.assertEqual(result["antagonist"], "鬼")
        self.assertEqual(result["subjects"], 7)


class LibraryHttpBoundaryTests(unittest.TestCase):
    """viewer/library_pages.py: the /worlds and /genres HTML pages, and the
    POST boundary (X-WorldBloom-Client) on their JSON APIs."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui010-lib-http-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        for name in ("projects", "templates"):
            shutil.copytree(ROOT / name, self.repo / name)
        self.control = self.base / "control"
        self.runs = self.base / "runs"
        self.runs.mkdir()
        configs = ConfigStore(self.repo, self.control, self.runs)

        class FakeJobStore:
            def __init__(self, configs):
                self.configs = configs

            def list(self):
                return []

        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = RunRepository(self.runs, control_root=self.control, jobs=None)
        self.server.job_store = FakeJobStore(configs)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def http(self, method, path, body, *, client_header=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if client_header:
            headers["X-WorldBloom-Client"] = "1"
        try:
            conn.request(method, path, json.dumps(body), headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
        finally:
            conn.close()

    def test_post_worlds_requires_client_header(self):
        body = {"world_id": "no-header", "name": "x", "from_world_id": "momotaro", "template_id": "momotaro"}
        status, payload = self.http("POST", "/api/worlds", body, client_header=False)
        self.assertEqual(status, 403, payload)
        self.assertFalse((self.repo / "projects/no-header").exists())

    def test_post_worlds_with_client_header_succeeds(self):
        body = {"world_id": "with-header", "name": "x", "from_world_id": "momotaro", "template_id": "momotaro"}
        status, payload = self.http("POST", "/api/worlds", body)
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["world_id"], "with-header")
        self.assertTrue((self.repo / "projects/with-header").is_dir())

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            conn.close()

    def test_worlds_hub_page(self):
        # WB-UI-023: home carries a 世界/ジャンル tab pair so the genre layer
        # (previously reachable only from ⚙ 設定) is visible without leaving
        # the front page. 世界 stays the default (first) tab.
        status, body = self.get("/worlds")
        self.assertEqual(status, 200, body)
        self.assertIn("桃太郎", body)
        self.assertIn('class="tabs tabs-home"', body)
        self.assertIn('id="genres"', body)
        self.assertIn('href="/worlds/new"', body)
        self.assertIn('href="/genres/new"', body)
        self.assertIn('class="world-card"', body)

    def test_home_is_worlds_hub(self):
        status, body = self.get("/")
        self.assertEqual(status, 200, body)
        self.assertIn("桃太郎", body)
        self.assertIn('class="tabs tabs-home"', body)
        self.assertIn('id="genres"', body)
        self.assertIn('href="/worlds/new"', body)
        self.assertIn('href="/genres/new"', body)
        self.assertIn('class="world-card"', body)
        # The lead names the engine/genre/world layering; the genre tab's own
        # lead explains what a genre is, once, without a redundant <h2>.
        self.assertIn("1 つの物語エンジンに", body)
        self.assertIn("ジャンルは行動の文法", body)
        self.assertEqual(body.count("<h2>ジャンル</h2>"), 0)

    def test_genres_live_on_settings_page(self):
        # ⚙ 設定 (/configs) remains the genre editing surface; WB-UI-023
        # additionally surfaces a read/browse copy on the home hub's ジャンル
        # tab (test_home_is_worlds_hub), so this only pins /configs itself.
        status, body = self.get("/configs")
        self.assertEqual(status, 200, body)
        self.assertIn('id="genres"', body)
        self.assertIn('href="/genres/new"', body)
        self.assertIn('href="/genres/momotaro"', body)

    def test_world_detail_page(self):
        status, body = self.get("/worlds/momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn('data-wb="library"', body)
        self.assertIn('data-kind="world"', body)
        self.assertIn('data-file="world.yaml"', body)
        self.assertIn('data-file="subjects/03_momotaro.yaml"', body)
        self.assertIn("人物を追加", body)
        self.assertIn('class="relation-graph"', body)
        self.assertIn('id="experiments"', body)
        self.assertIn('<details class="editor-group">', body)

    def test_world_detail_page_has_four_tabs(self):
        # Regression guard for the world/genre/count/period tab split
        # (WB-UI-017): checks the tab wiring itself, not just that each
        # panel's content is present somewhere in the flat HTML.
        status, body = self.get("/worlds/momotaro")
        self.assertEqual(status, 200, body)
        self.assertEqual(body.count('class="tab-input"'), 6)
        for index, label in enumerate(["概要", "登場人物", "初期物語", "場所", "期間"]):
            self.assertIn(f'<label class="tab-label" for="tab-world-{index}">{label}</label>', body)
        self.assertIn('<label class="tab-label" for="tab-world-5">実行履歴 (', body)
        self.assertIn('class="relation-graph zone-graph"', body)
        self.assertIn('class="day-cycle"', body)
        self.assertIn('class="calendar-grid"', body)

    def test_world_detail_page_has_canon_and_readout(self):
        # WB-EXPLAIN-canon: the 初期物語 tab surfaces the canon precedent
        # decision table (phrased with this world's objective in the column
        # header, not folded into a per-row sentence) and the characters tab
        # the deterministic per-character readout (hidden item modifiers,
        # secrets, foreshadowing) end-to-end through the route.
        status, body = self.get("/worlds/momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn('class="wb-table canon-table"', body)
        self.assertIn("鬼ヶ島の宝物の所在", body)
        self.assertIn("敵が持っている", body)
        self.assertIn('class="character-readout"', body)
        self.assertIn("金棒: +40", body)
        self.assertIn("隠れた強化", body)
        self.assertIn("鬼の力は金棒に支えられている", body)

    def test_genre_detail_page(self):
        status, body = self.get("/genres/momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn('data-wb="library"', body)
        self.assertIn('data-kind="genre"', body)
        self.assertIn('data-file="rules.yaml"', body)
        self.assertIn("momotaro", body)

    def test_new_forms(self):
        status, body = self.get("/worlds/new?from=momotaro&genre=momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn('data-wb="library-create"', body)
        self.assertIn('data-kind="world"', body)
        self.assertIn('<option value="momotaro" selected>', body)

        status, body = self.get("/genres/new?from=momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn('data-kind="genre"', body)

    def test_unknown_world_is_404(self):
        status, body = self.get("/worlds/no-such-world")
        self.assertEqual(status, 404, body)

    def test_validate_world_via_http(self):
        status, payload = self.http("POST", "/api/worlds/momotaro/validate", {"template_id": "momotaro"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["world_name"], "桃太郎")
        self.assertEqual(payload["subjects"], 7)


class RunDeleteTests(unittest.TestCase):
    """execution.jobs.JobStore.delete_run and its POST /exp/<name>/delete
    route: cascade-deletes a run's job records, generated outputs and
    selections/legacy registration along with the run folder itself."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-run-delete-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        for name in ("projects", "templates"):
            shutil.copytree(ROOT / name, self.repo / name)
        self.control = self.base / "control"
        self.runs = self.base / "runs"
        self.runs.mkdir()
        self.configs = ConfigStore(self.repo, self.control, self.runs)
        self.jobs = JobStore(self.configs)

        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = RunRepository(self.runs, control_root=self.control, jobs=self.jobs)
        self.server.job_store = self.jobs
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def http(self, method, path, *, client_header=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"X-WorldBloom-Client": "1"} if client_header else {}
        try:
            conn.request(method, path, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                # _parts() rejects a traversal-ish segment (e.g. "..") before
                # routing, via BaseHTTPRequestHandler.send_error()'s HTML body.
                payload = raw.decode("utf-8", "replace")
            return response.status, payload
        finally:
            conn.close()

    def _write_legacy_run(self, name):
        experiment = self.runs / name
        experiment.mkdir()
        (experiment / "archive.json").write_text(json.dumps({"cells": {}}), encoding="utf-8")
        (self.runs / f"{name}.log").write_text("log", encoding="utf-8")
        return experiment

    def _write_job(self, job_id, *, run_id, state):
        folder = self.control / "jobs" / job_id
        folder.mkdir(parents=True)
        (folder / "worker.py").write_bytes(b"")
        request_bytes = json.dumps({"request_id": job_id, "kind": "evolve", "config_id": "cfg-x"}).encode("utf-8")
        (folder / "request.json").write_bytes(request_bytes)
        job = {"job_id": job_id, "entrypoint": str((folder / "worker.py").resolve()),
               "request_hash": sha256(request_bytes), "run_id": run_id, "state": state, "kind": "evolve"}
        (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
        return folder

    def _write_output(self, output_id, *, run_id):
        request = {"output_id": output_id, "candidate_ids": ["cand-1"], "run_id": run_id,
                   "limits": {"max_calls": 1, "call_timeout_seconds": 30, "wall_seconds": 60,
                              "max_saved_response_bytes": 1024}}
        OutputStore(self.configs.control).create(request, {"cand-1": "prompt text"})
        return self.control / "outputs" / output_id

    def test_delete_legacy_run_cascades(self):
        name = "legacy-run-1"
        self._write_legacy_run(name)
        rid = self.server.repository.catalog.register_legacy(name)
        legacy_dir = self.control / "legacy" / rid
        self.assertTrue(legacy_dir.is_dir())

        status, payload = self.http("POST", f"/exp/{name}/delete")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload, {"deleted": name, "run_id": rid})
        self.assertFalse((self.runs / name).exists())
        self.assertFalse((self.runs / f"{name}.log").exists())
        self.assertFalse(legacy_dir.exists())
        self.assertNotIn(rid, {r["run_id"] for r in self.server.repository.catalog.history()})

    def test_delete_manifest_run_removes_job_and_output(self):
        name = "run-x"
        experiment = self.runs / name
        experiment.mkdir()
        (experiment / "manifest.json").write_text(json.dumps({"run_id": name}), encoding="utf-8")
        job_folder = self._write_job("job-" + sha256(b"job-run-x"), run_id=name, state="succeeded")
        output_folder = self._write_output("out-" + sha256(b"output-run-x")[:32], run_id=name)

        status, payload = self.http("POST", f"/exp/{name}/delete")
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload, {"deleted": name, "run_id": name})
        self.assertFalse(experiment.exists())
        self.assertFalse(job_folder.exists())
        self.assertFalse(output_folder.exists())

    def test_delete_run_with_active_job_conflicts(self):
        name = "run-active"
        experiment = self.runs / name
        experiment.mkdir()
        (experiment / "manifest.json").write_text(json.dumps({"run_id": name}), encoding="utf-8")
        job_folder = self._write_job("job-" + sha256(b"job-run-active"), run_id=name, state="running")

        status, payload = self.http("POST", f"/exp/{name}/delete")
        self.assertEqual(status, 409, payload)
        self.assertTrue(experiment.exists())
        self.assertTrue(job_folder.exists())

    def test_delete_run_requires_client_header(self):
        name = "legacy-run-2"
        self._write_legacy_run(name)
        status, payload = self.http("POST", f"/exp/{name}/delete", client_header=False)
        self.assertEqual(status, 403, payload)
        self.assertTrue((self.runs / name).exists())

    def test_delete_unknown_run_is_404(self):
        status, payload = self.http("POST", "/exp/no-such-run/delete")
        self.assertEqual(status, 404, payload)

    def test_delete_run_rejects_path_traversal(self):
        status, payload = self.http("POST", "/exp/../delete")
        self.assertGreaterEqual(status, 400)
        self.assertLess(status, 500)


class LibraryGuidanceTests(unittest.TestCase):
    """Without --control (no job_store), /worlds and /worlds/<id> now fall
    back to a read-only listing/detail (WB-UI-016) instead of the guidance
    page; /worlds/new, /genres/new and /genres/<id> still need --control to
    mutate anything, so they keep showing guidance."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui010-lib-guidance-")
        self.addCleanup(self.temp.cleanup)
        runs = Path(self.temp.name) / "runs"
        runs.mkdir()
        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = RunRepository(runs)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            conn.close()

    def test_worlds_without_control_is_a_read_only_listing(self):
        status, body = self.get("/worlds")
        self.assertEqual(status, 200, body)
        self.assertIn("桃太郎", body)
        self.assertNotIn('href="/worlds/new"', body)

    def test_world_detail_without_control_is_read_only(self):
        status, body = self.get("/worlds/momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn('class="relation-graph"', body)
        self.assertNotIn('data-action="save-file"', body)

    def test_genre_detail_without_control_is_still_guidance(self):
        status, body = self.get("/genres/momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn("実行管理は未設定です", body)


if __name__ == "__main__":
    unittest.main()
