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
from execution.library import LibraryStore
from execution.provenance import ConfigError
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
        status, body = self.get("/worlds")
        self.assertEqual(status, 200, body)
        self.assertIn("桃太郎", body)
        self.assertNotIn('id="genres"', body)
        self.assertIn('href="/worlds/new"', body)
        self.assertNotIn('href="/genres/new"', body)
        self.assertIn('class="world-card"', body)

    def test_home_is_worlds_hub(self):
        status, body = self.get("/")
        self.assertEqual(status, 200, body)
        self.assertIn("桃太郎", body)
        self.assertNotIn('id="genres"', body)
        self.assertIn('href="/worlds/new"', body)
        self.assertNotIn('href="/genres/new"', body)
        self.assertIn('class="world-card"', body)

    def test_genres_live_on_settings_page(self):
        # Genre creation moved off the home hub: ⚙ 設定 (/configs) owns it.
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
        self.assertEqual(body.count('class="tab-input"'), 5)
        for index, label in enumerate(["概要", "登場人物", "初期物語", "場所", "期間"]):
            self.assertIn(f'<label class="tab-label" for="tab-world-{index}">{label}</label>', body)
        self.assertIn('class="relation-graph zone-graph"', body)
        self.assertIn('class="day-cycle"', body)
        self.assertIn('class="calendar-grid"', body)

    def test_world_detail_page_has_canon_and_readout(self):
        # WB-EXPLAIN-canon: the 初期物語 tab surfaces the canon precedent
        # table (WB-UI-020: phrased with this world's objective) and the
        # characters tab the deterministic per-character readout (hidden
        # item modifiers, secrets, foreshadowing) end-to-end through the route.
        status, body = self.get("/worlds/momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn('class="wb-table canon-table"', body)
        self.assertIn("鬼ヶ島の宝物を敵が持っている", body)
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
