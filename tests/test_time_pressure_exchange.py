"""No disk/HTTP/model use: prevent silent deadline-sidecar loss on ZIP import."""
import io
import unittest
import zipfile
from copy import deepcopy
from execution import time_pressure_settings as tp
from test_time_pressure_settings import TimePressureSettingsTests
from test_time_pressure_runtime import ConfigAndWiringTests

from execution.library import zip_to_files
from execution.provenance import ConfigError


def package(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


class PressureExchangeTests(unittest.TestCase):
    def test_ordinary_world_import_keeps_existing_file_shapes(self):
        files = {"world.yaml": "name: fixture\n", "subjects/hero.yaml": "id: hero\n"}
        self.assertEqual(zip_to_files(package(files)), files)

    def test_new_sidecar_stops_instead_of_being_silently_ignored(self):
        for prefix in ("", "world/"):
            for name in ("time-pressure.yaml", "TIME-PRESSURE.YAML"):
                files = {prefix + "world.yaml": "name: fixture\n", prefix + name: "version: 1\nsources: []\n"}
                with self.subTest(prefix=prefix, name=name), self.assertRaises(ConfigError) as caught:
                    zip_to_files(package(files))
                self.assertEqual(caught.exception.code, "unsupported")

    def test_unrelated_old_extra_file_behavior_is_unchanged(self):
        files = {"world.yaml": "name: fixture\n", "notes.txt": "old extra file"}
        self.assertEqual(zip_to_files(package(files)), {"world.yaml": files["world.yaml"]})


class PressureCloneTests(unittest.TestCase):
    setUp = TimePressureSettingsTests.setUp
    body = TimePressureSettingsTests.body

    def test_clone_keeps_sidecar_and_source_unchanged(self):
        (self.repo / "templates/basic").mkdir(parents=True)
        tp.save(self.store, "test", self.body())
        source = (self.base / tp.FILENAME).read_bytes()
        self.store.create_world("copied", from_id="test", genre_id="basic", name="copy")
        self.assertEqual((self.repo / "projects/copied" / tp.FILENAME).read_bytes(), source)
        self.assertEqual((self.base / tp.FILENAME).read_bytes(), source)
        self.assertTrue(tp.snapshot(self.store, "copied")["validation"]["valid"])

    def test_invalid_reference_never_publishes_clone(self):
        import json
        (self.repo / "templates/basic").mkdir(parents=True)
        bad = deepcopy(self.source)
        bad["response_goal_id"] = "missing"
        path = self.base / tp.FILENAME
        path.write_text(json.dumps({"version": 1, "sources": [bad]}))
        before = path.read_bytes()
        with self.assertRaises(ConfigError):
            self.store.create_world("copied", from_id="test", genre_id="basic", name="copy")
        self.assertFalse((self.repo / "projects/copied").exists())
        self.assertEqual(path.read_bytes(), before)


class PressureServiceCaptureTests(unittest.TestCase):
    setUp = ConfigAndWiringTests.setUp

    def test_editor_save_connects_to_capture_and_off_preserves_source(self):
        from execution.library import LibraryStore
        self.path.unlink()
        library = LibraryStore(self.repo)
        snapshot = tp.snapshot(library, "ship_planning")
        saved = tp.save(library, "ship_planning", {
            "revision": snapshot["revision"], "world_revision": snapshot["world_revision"],
            "settings": self.doc})
        self.assertEqual(saved["settings"], self.doc)
        before = self.path.read_bytes()
        self.store.save({**self.spec, "time_pressure": {"enabled": True}}, config_id="editor-on")
        manifest = self.store.prepare_run("editor-on", run_id="editor-run", job_id="editor-job")
        captured = self.base / "runs/editor-run/inputs/projects/ship_planning/time-pressure.yaml"
        self.assertEqual(captured.read_bytes(), before)
        self.assertTrue(manifest["time_pressure"]["enabled"])
        self.store.duplicate("editor-on", {"time_pressure": {"enabled": False}}, new_id="editor-off")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.base / "control/configs/editor-off/inputs/projects/ship_planning/time-pressure.yaml").exists())
