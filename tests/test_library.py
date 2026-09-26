"""WB-UI-010 Stage 2: LibraryStore file I/O/validation and its POST boundary.

LibraryStore never re-implements semantic validation: validate() must call
straight through to the already-tested execution.configs.ConfigStore.preview.
"""
from __future__ import annotations

from pathlib import Path
import http.client
import io
import json
import shutil
import tempfile
import threading
import unittest
import zipfile
import zlib
from unittest.mock import patch

import yaml

from execution.configs import ConfigStore
from execution.jobs import JobStore
from execution.library import MAX_ZIP_ENTRIES, MAX_ZIP_TOTAL, LibraryStore, zip_to_files
from execution.output_store import OutputStore
from execution.provenance import ConfigError, sha256
from viewer.data import RunRepository
from viewer.server import ViewerHandler, ViewerServer

ROOT = Path(__file__).resolve().parents[1]


def _make_zip(entries: dict) -> bytes:
    """entries: {path_in_zip: str_or_bytes}."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content.encode("utf-8") if isinstance(content, str) else content)
    return buffer.getvalue()


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

    def test_create_world_does_not_copy_patches(self):
        # WB-WORLDGROW-001 段階5d D10: a copied world's patches/ (stack.json +
        # approved patch yaml/gate) recorded a base_inputs_digest against the
        # *source* world.yaml -- carrying it over verbatim would make the
        # copy's own expand/applicable_snapshot refuse immediately (digest
        # mismatch, since the copy's name/content already differ). Genre
        # assets are what the copy should pick expansions up from instead.
        patches_dir = self.repo / "projects/momotaro/patches"
        patches_dir.mkdir()
        (patches_dir / "stack.json").write_text("{}", encoding="utf-8")
        self.store.create_world("clone-nopatches", from_id="momotaro", genre_id="momotaro", name="複製先")
        self.assertFalse((self.repo / "projects/clone-nopatches/patches").exists())

    # ------------------------------------------------------------- genres()

    def test_genres_reports_expansion_asset_count(self):
        from execution.world_patch_library import library_dir
        genres = {g["id"]: g for g in self.store.genres()}
        self.assertEqual(genres["momotaro"]["expansions"], 0)
        folder = library_dir(self.repo / "templates/momotaro")
        folder.mkdir(parents=True)
        (folder / "p-00000001.yaml").write_text(
            yaml.safe_dump({"schema_version": 1, "patch": {"id": "p-00000001"}, "provenance": {}},
                          allow_unicode=True), encoding="utf-8")
        genres = {g["id"]: g for g in self.store.genres()}
        self.assertEqual(genres["momotaro"]["expansions"], 1)

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


class ZipImportTests(unittest.TestCase):
    """execution.library.zip_to_files and LibraryStore.import_world (WB-WORLD-IMPORT-001)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-zip-import-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        for name in ("projects", "templates"):
            shutil.copytree(ROOT / name, self.repo / name)
        self.store = LibraryStore(self.repo)
        self.configs = ConfigStore(self.repo, Path(self.temp.name) / "control", Path(self.temp.name) / "runs")

    # ------------------------------------------------------------ zip_to_files

    def test_zip_to_files_reads_flat_layout(self):
        raw = _make_zip({"world.yaml": "name: x", "subjects/01_a.yaml": "id: a"})
        self.assertEqual(zip_to_files(raw), {"world.yaml": "name: x", "subjects/01_a.yaml": "id: a"})

    def test_zip_to_files_strips_a_wrapping_folder(self):
        raw = _make_zip({"myworld/world.yaml": "name: x", "myworld/subjects/01_a.yaml": "id: a"})
        self.assertEqual(zip_to_files(raw), {"world.yaml": "name: x", "subjects/01_a.yaml": "id: a"})

    def test_zip_to_files_ignores_noise_entries(self):
        raw = _make_zip({
            "myworld/world.yaml": "name: x",
            "myworld/README.md": "hello",
            "__MACOSX/myworld/world.yaml": "junk",
            "myworld/.DS_Store": "junk",
        })
        self.assertEqual(zip_to_files(raw), {"world.yaml": "name: x"})

    def test_zip_to_files_accepts_backslash_separators(self):
        info = zipfile.ZipInfo("myworld\\world.yaml")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(info, "name: x".encode("utf-8"))
            archive.writestr("myworld\\subjects\\01_a.yaml", "id: a".encode("utf-8"))
        self.assertEqual(zip_to_files(buffer.getvalue()), {"world.yaml": "name: x", "subjects/01_a.yaml": "id: a"})

    def test_zip_to_files_strips_leading_bom(self):
        raw = _make_zip({"world.yaml": "﻿name: x"})
        self.assertEqual(zip_to_files(raw)["world.yaml"], "name: x")

    def test_zip_to_files_rejects_missing_world_yaml(self):
        raw = _make_zip({"subjects/01_a.yaml": "id: a"})
        with self.assertRaises(ConfigError) as ctx:
            zip_to_files(raw)
        self.assertIn("zip_base64", ctx.exception.field_errors)

    def test_zip_to_files_rejects_unruly_subject_name(self):
        raw = _make_zip({"world.yaml": "name: x", "subjects/not a valid name.yaml": "id: a"})
        with self.assertRaises(ConfigError):
            zip_to_files(raw)

    def test_zip_to_files_rejects_oversized_entry(self):
        raw = _make_zip({"world.yaml": "name: x", "subjects/01_a.yaml": "x: " + "a" * (260 * 1024)})
        with self.assertRaises(ConfigError):
            zip_to_files(raw)

    def test_zip_to_files_rejects_exact_duplicate(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("world.yaml", b"name: x")
            archive.writestr("world.yaml", b"name: y")
        with self.assertRaises(ConfigError):
            zip_to_files(buffer.getvalue())

    def test_zip_to_files_rejects_casefold_duplicate(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("world.yaml", b"name: x")
            archive.writestr("subjects/01_a.yaml", b"id: a")
            archive.writestr("subjects/01_A.yaml", b"id: a")
        with self.assertRaises(ConfigError):
            zip_to_files(buffer.getvalue())

    def test_zip_to_files_rejects_non_utf8(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("world.yaml", "name: x".encode("utf-8"))
            archive.writestr("subjects/01_a.yaml", "id: あ".encode("shift_jis"))
        with self.assertRaises(ConfigError):
            zip_to_files(buffer.getvalue())

    def test_zip_to_files_rejects_corrupt_archive(self):
        with self.assertRaises(ConfigError):
            zip_to_files(b"not a zip file")

    def test_zip_to_files_rejects_corrupt_deflate_stream(self):
        # zipfile.ZipFile() itself opens fine (the central directory is
        # intact); it's decompressing a corrupt deflate stream inside one
        # entry that fails, with zlib.error -- a different exception than
        # zipfile.BadZipFile, and previously uncaught here. Reproducing a
        # byte-exact corrupt deflate stream is fragile (zlib.error depends on
        # internal decompressor state), so this patches the read path to
        # raise it directly, which is what zip_to_files must translate into
        # a ConfigError (400) rather than letting it escape as a bare 500.
        raw = _make_zip({"world.yaml": "name: x"})
        with patch.object(zipfile.ZipFile, "open",
                          side_effect=zlib.error("Error -3 while decompressing data: invalid distance too far back")):
            with self.assertRaises(ConfigError):
                zip_to_files(raw)

    def test_zip_to_files_rejects_ambiguous_root(self):
        raw = _make_zip({"a/world.yaml": "name: x", "b/world.yaml": "name: y"})
        with self.assertRaises(ConfigError):
            zip_to_files(raw)

    def test_zip_to_files_rejects_subject_path_escape(self):
        raw = _make_zip({"world.yaml": "name: x", "subjects/../../evil.yaml": "x: 1"})
        with self.assertRaises(ConfigError):
            zip_to_files(raw)

    def test_zip_to_files_rejects_too_many_entries(self):
        entries = {"world.yaml": "name: x"}
        for i in range(MAX_ZIP_ENTRIES):
            entries[f"subjects/s{i:04d}.yaml"] = "id: a"
        with self.assertRaises(ConfigError):
            zip_to_files(_make_zip(entries))

    def test_zip_to_files_rejects_oversized_total(self):
        entries = {"world.yaml": "name: x"}
        chunk = "x: " + ("a" * (200 * 1024))  # under the 256KB per-entry cap
        needed = MAX_ZIP_TOTAL // len(chunk.encode("utf-8")) + 2
        for i in range(min(needed, MAX_ZIP_ENTRIES - 1)):
            entries[f"subjects/s{i:04d}.yaml"] = chunk
        with self.assertRaises(ConfigError):
            zip_to_files(_make_zip(entries))

    # ------------------------------------------------------------ import_world

    def _momotaro_files(self):
        base = ROOT / "projects/momotaro"
        files = {"world.yaml": (base / "world.yaml").read_text(encoding="utf-8")}
        for path in sorted((base / "subjects").glob("*.yaml")):
            files[f"subjects/{path.name}"] = path.read_text(encoding="utf-8")
        return files

    def test_import_world_uses_zip_name_when_form_name_is_blank(self):
        files = {"world.yaml": "name: ZIPの名前\noverview: x"}
        self.store.import_world("imported1", files=files, name="")
        world = yaml.safe_load((self.repo / "projects/imported1/world.yaml").read_text(encoding="utf-8"))
        self.assertEqual(world["name"], "ZIPの名前")

    def test_import_world_form_name_overrides_zip_name(self):
        files = {"world.yaml": "name: ZIPの名前"}
        self.store.import_world("imported2", files=files, name="フォームの名前")
        world = yaml.safe_load((self.repo / "projects/imported2/world.yaml").read_text(encoding="utf-8"))
        self.assertEqual(world["name"], "フォームの名前")

    def test_import_world_rejects_when_both_names_are_blank(self):
        files = {"world.yaml": "overview: x"}
        with self.assertRaises(ConfigError) as ctx:
            self.store.import_world("imported3", files=files, name="")
        self.assertIn("name", ctx.exception.field_errors)
        self.assertFalse((self.repo / "projects/imported3").exists())

    def test_import_world_without_gapengine_leaves_genre_unset(self):
        files = {"world.yaml": "name: x"}
        self.store.import_world("imported4", files=files, name="")
        world = yaml.safe_load((self.repo / "projects/imported4/world.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("gapengine", world)
        genres = {w["id"]: w["genre"] for w in self.store.worlds()}
        self.assertIsNone(genres["imported4"])

    def test_import_world_with_null_gapengine_does_not_persist_a_literal_null(self):
        files = {"world.yaml": "name: x\ngapengine: null\n"}
        self.store.import_world("imported4b", files=files, name="")
        raw = (self.repo / "projects/imported4b/world.yaml").read_text(encoding="utf-8")
        self.assertNotIn("gapengine", raw)
        world = yaml.safe_load(raw)
        self.assertNotIn("gapengine", world)

    def test_import_world_with_valid_gapengine_is_kept(self):
        files = {"world.yaml": "name: x\ngapengine:\n  action_graph: templates/momotaro/action_graph.yaml\n  effects: templates/momotaro/effects.yaml\n"}
        self.store.import_world("imported5", files=files, name="")
        world = yaml.safe_load((self.repo / "projects/imported5/world.yaml").read_text(encoding="utf-8"))
        self.assertEqual(world["gapengine"], {"action_graph": "templates/momotaro/action_graph.yaml",
                                              "effects": "templates/momotaro/effects.yaml"})
        genres = {w["id"]: w["genre"] for w in self.store.worlds()}
        self.assertEqual(genres["imported5"], "momotaro")

    def test_import_world_rejects_gapengine_path_escape(self):
        files = {"world.yaml": "name: x\ngapengine:\n  action_graph: ../../templates/momotaro/action_graph.yaml\n  effects: templates/momotaro/effects.yaml\n"}
        with self.assertRaises(ConfigError):
            self.store.import_world("imported6", files=files, name="")
        self.assertFalse((self.repo / "projects/imported6").exists())

    def test_import_world_rejects_mismatched_genre_ids(self):
        files = {"world.yaml": "name: x\ngapengine:\n  action_graph: templates/momotaro/action_graph.yaml\n  effects: templates/basic/effects.yaml\n"}
        with self.assertRaises(ConfigError):
            self.store.import_world("imported7", files=files, name="")

    def test_import_world_rejects_extra_gapengine_keys(self):
        files = {"world.yaml": "name: x\ngapengine:\n  action_graph: templates/momotaro/action_graph.yaml\n  effects: templates/momotaro/effects.yaml\n  extra: 1\n"}
        with self.assertRaises(ConfigError):
            self.store.import_world("imported8", files=files, name="")

    def test_import_world_rejects_missing_genre_directory(self):
        files = {"world.yaml": "name: x\ngapengine:\n  action_graph: templates/no-such-genre/action_graph.yaml\n  effects: templates/no-such-genre/effects.yaml\n"}
        with self.assertRaises(ConfigError):
            self.store.import_world("imported9", files=files, name="")
        self.assertFalse((self.repo / "projects/imported9").exists())

    def test_import_world_rejects_existing_id(self):
        with self.assertRaises(ConfigError) as ctx:
            self.store.import_world("momotaro", files={"world.yaml": "name: x"}, name="")
        self.assertEqual(ctx.exception.code, "conflict")

    def test_import_world_leaves_no_debris_on_failure(self):
        before = {p.name for p in (self.repo / "projects").iterdir()}
        with self.assertRaises(ConfigError):
            self.store.import_world("imported-fail", files={"world.yaml": "not: [closed"}, name="x")
        after = {p.name for p in (self.repo / "projects").iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(list(self.repo.glob(".world-create-*")), [])

    def test_import_world_writes_subjects_byte_for_byte(self):
        files = self._momotaro_files()
        self.store.import_world("imported-clone", files=files, name="複製太郎")
        for rel, text in files.items():
            if rel == "world.yaml":
                continue
            self.assertEqual((self.repo / "projects/imported-clone" / rel).read_bytes(), text.encode("utf-8"))

    def test_import_world_writes_subjects_without_line_ending_translation(self):
        # write_text() re-translates "\n" to os.linesep on Windows, turning a
        # CRLF source ("\r\n") into "\r\r\n" -- a blank line inside every
        # folded/literal YAML block scalar. subjects/ must go through
        # write_bytes() instead, so a CRLF-authored file round-trips exactly.
        files = {"world.yaml": "name: x", "subjects/01_a.yaml": "id: a\r\ndescription: |\r\n  line one\r\n  line two\r\n"}
        self.store.import_world("imported-crlf", files=files, name="")
        written = (self.repo / "projects/imported-crlf/subjects/01_a.yaml").read_bytes()
        self.assertEqual(written, files["subjects/01_a.yaml"].encode("utf-8"))
        self.assertNotIn(b"\r\r\n", written)

    def test_import_world_then_preview_succeeds(self):
        # WB-WORLD-IMPORT-001 §5(a): a ZIP of a real world round-trips through
        # ConfigStore.preview exactly like any other world/genre pair.
        files = self._momotaro_files()
        self.store.import_world("imported-preview", files=files, name="")
        result = self.store.validate(self.configs, world_id="imported-preview", genre_id="momotaro")
        self.assertEqual(result["world_name"], "桃太郎")
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
        self.assertIn('role="tablist" aria-label="ライブラリー"', body)
        self.assertIn('id="genres"', body)
        self.assertIn('href="/worlds/new"', body)
        self.assertIn('href="/genres/new"', body)
        self.assertIn('class="world-card"', body)

    def test_home_is_worlds_hub(self):
        status, body = self.get("/")
        self.assertEqual(status, 200, body)
        self.assertIn("桃太郎", body)
        self.assertIn('role="tablist" aria-label="ライブラリー"', body)
        self.assertIn('id="genres"', body)
        self.assertIn('href="/worlds/new"', body)
        self.assertIn('href="/genres/new"', body)
        self.assertIn('class="world-card"', body)
        # The lead names the engine/genre/world layering; the genre tab's own
        # lead explains what a genre is, once, without a redundant <h2>.
        self.assertIn("世界を開いて、人物や場所、物語の始まりを確かめましょう", body)
        self.assertIn("ジャンルは、行動とその結果を決める共通のルール", body)
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
        status, body = self.get("/worlds/momotaro?view=advanced")
        self.assertEqual(status, 200, body)
        self.assertIn('data-world-advanced', body)
        self.assertIn('&quot;world.yaml&quot;', body)
        self.assertIn('subjects/03_momotaro.yaml', body)
        self.assertIn('data-advanced-save', body)
        self.assertIn('href="/worlds/momotaro"', body)

    def test_world_detail_page_has_four_sections(self):
        status, body = self.get("/worlds/momotaro?view=advanced")
        self.assertEqual(status, 200, body)
        for section in ('roles','canon','actions','files'):
            self.assertIn(f'data-advanced-section="{section}"',body)
        self.assertNotIn('class="tab-input"',body)

    def test_world_detail_action_catalog(self):
        # WB-UI-025: the 行動図鑑 tab renders this world's verb status
        # (active/pruned/unused/unimplemented) with world-specific values
        # filled into the engine-level catalog text.
        status, body = self.get("/worlds/momotaro?view=advanced")
        self.assertEqual(status, 200, body)
        self.assertIn("catalog-kinds", body)
        self.assertIn("未実装（構想のみ）", body)
        self.assertIn("0.6", body)  # companionship_threshold filled in
        self.assertIn("<dl class=\"world-summary\">", body)

    def test_world_detail_overview_intro(self):
        # WB-UI-024: the 概要 tab leads with a one-sentence auto-generated
        # introduction, not just a bare metadata line.
        status, body = self.get("/worlds/momotaro?view=advanced")
        self.assertEqual(status, 200, body)
        self.assertIn('世界の詳細設定', body)
        self.assertIn('href="/worlds/momotaro"', body)
        self.assertIn('&quot;days&quot;: 16', body)
        self.assertIn('href="/genres/momotaro"', body)

    def test_world_detail_page_has_canon_and_readout(self):
        # WB-EXPLAIN-canon: the 初期物語 tab surfaces the canon precedent
        # decision table (phrased with this world's objective in the column
        # header, not folded into a per-row sentence) and the characters tab
        # the deterministic per-character readout (hidden item modifiers,
        # secrets, foreshadowing) end-to-end through the route.
        status, body = self.get("/worlds/momotaro?view=advanced")
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
        self.assertIn('data-genre-editor', body)
        self.assertIn('data-section="rules"', body)
        self.assertIn('data-validate', body)
        self.assertIn("momotaro", body)

    def test_new_forms(self):
        status, body = self.get("/worlds/new?from=momotaro&genre=momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn('data-world-create', body)
        self.assertIn('name="mode" value="copy" checked', body)
        self.assertIn('<option value="momotaro" selected>', body)

        status, body = self.get("/genres/new?from=momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn('data-genre-new', body)

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
        status, body = self.get("/worlds/momotaro?view=advanced")
        self.assertEqual(status, 200, body)
        self.assertIn('data-world-advanced', body)
        self.assertNotIn('data-action="save-file"', body)
        self.assertNotIn('data-wb="library"', body)
        self.assertIn('&quot;editable&quot;: false', body)

    def test_genre_detail_without_control_is_still_guidance(self):
        status, body = self.get("/genres/momotaro")
        self.assertEqual(status, 200, body)
        self.assertIn("実行管理は未設定です", body)


if __name__ == "__main__":
    unittest.main()
