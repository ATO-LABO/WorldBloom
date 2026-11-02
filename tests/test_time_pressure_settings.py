"""Isolated world-sidecar save boundary and API contract tests (no HTTP)."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from execution import time_pressure_settings as tp
from execution.library import LibraryStore
from execution.provenance import ConfigError
from viewer import library_pages


class TimePressureSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.base = self.repo / "projects/test"
        (self.base / "subjects").mkdir(parents=True)
        self.world = {"name": "fixture", "protagonist": "actor", "time": {"days": 2, "slots": ["morning", "night"]},
                      "planning": {"version": 1, "actors": ["actor"]},
                      "goal_rules": {"version": 1, "definitions": [{"condition": "goal", "period": {"start": 1, "end": 4}}]},
                      "facts": [{"id": "clock_fact"}], "scheduled_events": [{"id": "event", "day": 2, "slot": "night"}]}
        self.path = self.base / "world.yaml"
        self.path.write_text(json.dumps(self.world))
        (self.base / "subjects/actor.yaml").write_text(json.dumps({"id": "actor", "knowledge": []}))
        self.store = LibraryStore(self.repo)
        self.source = {"id": "source", "anchor": {"kind": "goal_period", "id": "goal"}, "response_goal_id": "goal",
                       "actor_ids": ["actor"], "awareness": {"kind": "public"}}

    def body(self, sources=None):
        result = tp.snapshot(self.store, "test")
        return {"revision": result["revision"], "world_revision": result["world_revision"],
                "settings": {"version": 1, "sources": deepcopy([self.source] if sources is None else sources)}}

    def test_read_and_validation_do_not_write_and_expose_registered_catalog(self):
        before = {p.relative_to(self.base): p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        result = tp.snapshot(self.store, "test")
        self.assertEqual(result["settings"], {"version": 1, "sources": []})
        self.assertTrue(result["availability"]["available"])
        self.assertEqual(result["catalog"]["facts"], [{"id": "clock_fact", "label": "clock_fact"}])
        self.assertEqual(result["catalog"]["actors"], [{"id": "actor", "label": "actor"}])
        self.assertTrue(tp.validate(self.store, "test", self.body())["validation"]["valid"])
        after = {p.relative_to(self.base): p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_save_reload_separate_world_revision_and_empty_delete(self):
        before = self.path.read_bytes()
        first = tp.snapshot(self.store, "test")
        result = tp.save(self.store, "test", self.body())
        self.assertEqual(result["settings"]["sources"], [self.source])
        self.assertEqual(result, tp.snapshot(self.store, "test"))
        self.assertEqual(first["world_revision"], result["world_revision"])
        self.assertNotEqual(first["revision"], result["revision"])
        self.assertEqual(before, self.path.read_bytes())
        tp.save(self.store, "test", self.body([]))
        self.assertEqual(tp.snapshot(self.store, "test")["settings"]["sources"], [])

    def test_both_cas_boundaries_preserve_sidecar(self):
        stale = self.body()
        tp.save(self.store, "test", stale)
        original = (self.base / tp.FILENAME).read_bytes()
        with self.assertRaises(ConfigError) as error: tp.save(self.store, "test", stale)
        self.assertEqual(error.exception.code, "conflict")
        fresh = self.body()
        self.world["name"] = "elsewhere"
        self.path.write_text(json.dumps(self.world))
        with self.assertRaises(ConfigError) as error: tp.save(self.store, "test", fresh)
        self.assertEqual(error.exception.code, "conflict")
        self.assertEqual(original, (self.base / tp.FILENAME).read_bytes())

    def test_reject_unknown_duplicate_extra_fields_invalid_kinds_and_limits(self):
        bad = []
        for field, value in (("response_goal_id", "missing"), ("actor_ids", ["missing"]), ("id", "x\n")):
            source = deepcopy(self.source); source[field] = value; bad.append([source])
        source = deepcopy(self.source); source["anchor"] = {"kind": "scheduled_event", "id": "missing"}; bad.append([source])
        source = deepcopy(self.source); source["awareness"] = {"kind": "known_fact", "fact_id": "missing"}; bad.append([source])
        source = deepcopy(self.source); source["unknown"] = True; bad.append([source])
        bad += [[self.source, self.source], [self.source] * 33]
        for sources in bad:
            with self.subTest(sources=sources):
                with self.assertRaises(ConfigError): tp.save(self.store, "test", self.body(sources))
        self.assertFalse((self.base / tp.FILENAME).exists())

    def test_known_fact_and_scheduled_event_save_without_assuming_actor_knows(self):
        source = deepcopy(self.source)
        source["anchor"] = {"kind": "scheduled_event", "id": "event"}
        source["awareness"] = {"kind": "known_fact", "fact_id": "clock_fact"}
        result = tp.save(self.store, "test", self.body([source]))
        self.assertEqual(result["settings"]["sources"], [source])

    def test_corrupt_duplicate_yaml_size_and_path_refused(self):
        path = self.base / tp.FILENAME
        for content in (b"version: 1\nversion: 1\nsources: []", b"x" * (tp.MAX_BYTES + 1), b"version: 2\nsources: []"):
            path.write_bytes(content)
            with self.assertRaises(ConfigError): tp.snapshot(self.store, "test")
        with self.assertRaises((ConfigError, ValueError)): tp.snapshot(self.store, "../test")

    def test_failed_replace_preserves_previous_sidecar(self):
        tp.save(self.store, "test", self.body())
        previous = (self.base / tp.FILENAME).read_bytes()
        body = self.body([])
        with patch("execution.time_settings.os.replace", side_effect=OSError("disk fixture")):
            with self.assertRaises(OSError): tp.save(self.store, "test", body)
        self.assertEqual(previous, (self.base / tp.FILENAME).read_bytes())
        self.assertFalse(list(self.base.glob(".time-pressure.yaml-*")))

    def test_deleted_reference_stays_readable_and_can_be_repaired(self):
        source = deepcopy(self.source)
        source["awareness"] = {"kind": "known_fact", "fact_id": "clock_fact"}
        tp.save(self.store, "test", self.body([source]))
        self.world["facts"] = []
        self.path.write_text(json.dumps(self.world))
        loaded = tp.snapshot(self.store, "test")
        self.assertFalse(loaded["validation"]["valid"])
        self.assertFalse(loaded["availability"]["available"])
        self.assertEqual(loaded["settings"]["sources"][0], source)
        source["awareness"] = {"kind": "public"}
        repaired = tp.save(self.store, "test", self.body([source]))
        self.assertTrue(repaired["validation"]["valid"])

    def test_optional_actor_estimate_is_retained_and_unset_stays_unset(self):
        source = deepcopy(self.source)
        source["estimate"] = {"duration": 2, "fact_id": "clock_fact"}
        saved = tp.save(self.store, "test", self.body([source]))
        self.assertEqual(saved["settings"]["sources"][0]["estimate"], source["estimate"])
        for estimate in ({"duration": 0, "fact_id": "clock_fact"}, {"duration": 10001, "fact_id": "clock_fact"},
                         {"duration": True, "fact_id": "clock_fact"}, {"duration": 1, "fact_id": "unknown"}):
            source["estimate"] = estimate
            with self.assertRaises(ConfigError): tp.validate(self.store, "test", self.body([source]))
        saved = tp.save(self.store, "test", self.body())
        self.assertNotIn("estimate", saved["settings"]["sources"][0])

    def test_run_form_off_default_duplicate_restore_and_separate_fields(self):
        from types import SimpleNamespace
        from html.parser import HTMLParser
        from viewer import run_settings, workbench_pages
        class Fields(HTMLParser):
            def __init__(self, text):
                super().__init__(); self.pressure = None; self.feed(text)
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get("data-field") == "time_pressure.enabled": self.pressure = attrs
        store = SimpleNamespace(configs=SimpleNamespace(repo=self.repo, list=lambda: []), list=lambda: [])
        values = workbench_pages._new_config_values()
        values["project_id"] = "test"; values["template_id"] = "fixture"
        for parent, checked in ((None, False), ({"config_id": "saved", "time_pressure": {"enabled": True}}, True)):
            handler = SimpleNamespace(_send_html=lambda text: None)
            captured = {}
            def document(title, body, **kwargs):
                captured["body"] = body; captured["scripts"] = kwargs["scripts"]; return body
            with patch("viewer.workbench_pages._job_store", return_value=store), patch("viewer.run_settings.pages.document", side_effect=document):
                run_settings.render(handler, values, projects=["test"], templates=["fixture"], worlds=[{"id": "test", "name": "fixture"}], parent=parent)
            attrs = Fields(captured["body"]).pressure
            self.assertIsNotNone(attrs)
            self.assertEqual("checked" in attrs, checked)
            self.assertEqual(attrs["type"], "checkbox")
            self.assertIn('data-time-pressure-availability', captured["body"])
            self.assertIn('source_count&quot;: 0', captured["body"])
            self.assertNotIn('ui-help.js', captured["scripts"])
            self.assertLess(captured["body"].index('data-time-pressure-section'), captured["body"].index('class="cfg-adv"'))

    def test_api_routes_are_exact(self):
        for method, parts, name, args in [
            ("GET", ["api", "worlds", "test", "time-pressure"], "_time_pressure_get", ("test",)),
            ("POST", ["api", "worlds", "test", "time-pressure"], "_time_pressure_post", ("test", "save")),
            ("POST", ["api", "worlds", "test", "time-pressure", "validate"], "_time_pressure_post", ("test", "validate"))]:
            route = library_pages._resolve(parts, method)
            self.assertEqual(route[0].__name__, name)
            self.assertEqual(route[1], args)


if __name__ == "__main__": unittest.main()
