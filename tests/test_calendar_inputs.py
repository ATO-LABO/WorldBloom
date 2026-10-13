"""Declared project identities, provenance and snapshot publication boundaries."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

from engine.calendar_checkpoint import digest
from engine.calendar_inputs import (freeze_project, load_project, read_subjects, safe_project_path,
                                    subject_spec, validate_execution_config, validate_fixed_version)
from engine.calendar_v2 import compile_calendar


ROOT = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def resign(version):
    version["id"] = digest({k: v for k, v in version.items() if k != "id"})
    return version


class CalendarInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-calendar-inputs-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        shutil.copytree(ROOT / "projects/calendar_workshop_example", self.project)
        self.world = self.project / "world.yaml"

    def config(self):
        return read_yaml(self.world)["slot_execution"]

    def compiled(self, path=None):
        path = self.world if path is None else path
        world = read_yaml(path)
        contract = read_yaml(path.parent/world["slot_execution"]["contract"])
        return compile_calendar(contract["calendar"], timezone=contract["timezone"],
                                scheduled_events=world.get("scheduled_events", []))

    def fixed(self):
        destination = self.root / "fixed"
        version = freeze_project(self.world, destination)
        return destination, version

    def save_version(self, destination, version):
        (destination/"execution-version.json").write_text(json.dumps(resign(version)), encoding="utf-8")

    def test_configuration_schema_and_portable_relative_paths_are_strict(self):
        good = self.config()
        self.assertEqual(validate_execution_config(good), good)
        cases = []
        for key, value in [("mode", "other"), ("subjects", []), ("subjects", True), ("fixed_version", False),
                           ("contract", "/tmp/contract"), ("contract", "../contract.yaml"),
                           ("contract", "C:contract.yaml"), ("contract", "a\\b"),
                           ("contract", "a//b"), ("contract", "./contract.yaml"),
                           ("contract", "execution-version.json")]:
            config = deepcopy(good); config[key] = value; cases.append(config)
        for roster in [[good["subjects"][0]] * 2, [{"id": "Ada", "path": "contract.yaml"}],
                       [{"id": True, "path": "subjects/ada.yaml"}],
                       [{"id": "Ada", "path": "subjects/ada.yaml", "implicit": True}]]:
            config = deepcopy(good); config["subjects"] = roster; cases.append(config)
        extra = deepcopy(good); extra["implicit_subject_directory"] = "subjects"; cases.append(extra)
        for bad in cases:
            with self.subTest(config=bad), self.assertRaises(ValueError):
                validate_execution_config(bad)

    def test_loading_records_exact_declared_subject_specs_and_provenance(self):
        before = tree(self.project)
        world, people = load_project(self.world)
        self.assertEqual(world.calendar_subject_specs, {p: subject_spec(s) for p, s in people.items()})
        self.assertEqual(world.calendar_subject_sources, {
            record["id"]: hashlib.sha256((self.project/record["path"]).read_bytes()).hexdigest()
            for record in self.config()["subjects"]})
        self.assertIsNone(world.calendar_input_provenance["fixed_version_id"])
        altered = deepcopy(people["Ada"]); altered.traits["diligence"] = .9
        self.assertNotEqual(subject_spec(altered), world.calendar_subject_specs["Ada"])
        self.assertNotIn("policy", subject_spec(altered))
        self.assertEqual(tree(self.project), before)

    def test_same_size_mtime_rewrites_use_current_world_subject_contract_and_graph(self):
        # Exercise the real stale-cache condition, including a cache warmed by
        # another legacy reader. Authored values and provenance must describe
        # the same current bytes even when filesystem metadata is unchanged.
        from engine.yaml_cache import load_yaml

        graph = self.project/"graph.data"
        write_yaml(graph, {"nodes": [{"id": "rest", "verb": "rest", "label": "Before"}], "edges": []})
        raw = read_yaml(self.world)
        raw["gapengine"] = {"action_graph": graph.name}
        write_yaml(self.world, raw)
        paths = [self.world, self.project/"subjects/ada.yaml", self.project/"contract.yaml", graph]
        for path in paths:
            load_yaml(path)
        old_world, old_people = load_project(self.world)
        self.assertEqual(old_world.name, "Night workshop")
        self.assertEqual(old_people["Ada"].traits["diligence"], .7)
        self.assertEqual(old_world.slot_contract["tasks"]["pieces"]["minutes_per_unit"], 10)
        self.assertEqual(old_world.action_graph["nodes"][0]["label"], "Before")

        replacements = [(b"Night workshop", b"Light workshop"),
                        (b"diligence: 0.7", b"diligence: 0.8"),
                        (b"minutes_per_unit: 10", b"minutes_per_unit: 11"),
                        (b"label: Before", b"label: After!")]
        for path, (before, after) in zip(paths, replacements):
            raw_bytes = path.read_bytes()
            self.assertEqual(raw_bytes.count(before), 1)
            stat = path.stat()
            changed = raw_bytes.replace(before, after)
            self.assertEqual(len(changed), stat.st_size)
            path.write_bytes(changed)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertEqual((path.stat().st_size, path.stat().st_mtime_ns),
                             (stat.st_size, stat.st_mtime_ns))

        def assert_current(world, people):
            self.assertEqual(world.name, "Light workshop")
            self.assertEqual(people["Ada"].traits["diligence"], .8)
            self.assertEqual(world.slot_contract["tasks"]["pieces"]["minutes_per_unit"], 11)
            self.assertEqual(world.action_graph["nodes"][0]["label"], "After!")
            self.assertEqual(world.calendar_subject_specs["Ada"]["traits"]["diligence"], .8)

        world, people = load_project(self.world)
        assert_current(world, people)
        current_inputs = {path.relative_to(self.project).as_posix(): {
            "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        } for path in [*paths, self.project/"subjects/bo.yaml"]}
        self.assertEqual(world.calendar_input_provenance["inputs"], current_inputs)
        self.assertEqual(world.calendar_subject_sources["Ada"], current_inputs["subjects/ada.yaml"]["sha256"])
        destination, version = self.fixed()
        self.assertEqual(version["source_inputs"], current_inputs)
        self.assertEqual(version["source_world"].encode("utf-8"), self.world.read_bytes())
        self.assertEqual(version["id"], digest({k: v for k, v in version.items() if k != "id"}))
        for name, record in version["inputs"].items():
            self.assertEqual(record["sha256"], hashlib.sha256((destination/name).read_bytes()).hexdigest())
        frozen_world, frozen_people = load_project(destination/version["world"])
        assert_current(frozen_world, frozen_people)

    def test_id_roles_and_world_actor_references_must_agree(self):
        original = tree(self.project)
        changes = [("subjects/ada.yaml", lambda v: dict(v, id="Bo")),
                   ("world.yaml", lambda v: dict(v, protagonist="Unknown")),
                   ("contract.yaml", lambda v: dict(v, roles={"Ada": ["maker"]}))]
        for filename, mutate in changes:
            path = self.project/filename; write_yaml(path, mutate(read_yaml(path)))
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                read_subjects(self.world, self.config())
            path.write_bytes(original[filename])

    def test_ranges_are_validated_without_implicit_expansion(self):
        path = self.project/"subjects/ada.yaml"; raw = read_yaml(path)
        for ranges in [{"zones": ["Bench"], "entry": "Store"}, {"zones": ["Unknown"], "entry": "Unknown"},
                       {"zones": [], "entry": "Bench"}, {"zones": ["Bench", "Bench"], "entry": "Bench"},
                       {"zones": ["Bench"], "entry": "Bench", "exclude": [{"zones": ["Unknown"]}]}]:
            write_yaml(path, {**raw, "range": ranges})
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                read_subjects(self.world, self.config())
        write_yaml(path, {**raw, "range": {"zones": ["Bench"], "entry": "Bench"}})
        subject = read_subjects(self.world, self.config())["Ada"]
        self.assertEqual(subject.range_zones, {"Bench"})

    def test_existing_goal_item_delivery_place_obstacles_and_exclusion_semantics(self):
        raw_world = read_yaml(self.world)
        raw_world["items"] = [{"name": "parcel", "lootable": True}]
        write_yaml(self.world, raw_world)
        path = self.project/"subjects/ada.yaml"; raw = read_yaml(path)
        raw["goal"] = {"target": "parcel", "deliver_to": "Store", "obstacles": ["Bo"]}
        raw["inventory"] = {"parcel": 1}
        raw["range"]["exclude"] = [{"zones": ["Store"], "until_item": "parcel"}]
        write_yaml(path, raw)
        subject = read_subjects(self.world, self.config())["Ada"]
        self.assertEqual((subject.goal.target, subject.goal.deliver_to, subject.goal.obstacles), ("parcel", "Store", ["Bo"]))
        self.assertEqual(subject.range_exclude, [{"zones": ["Store"], "until_item": "parcel"}])
        for field, value in [("target", "Bo"), ("deliver_to", "Ada"), ("obstacles", ["Unknown"])]:
            changed = deepcopy(raw); changed["goal"][field] = value; write_yaml(path, changed)
            with self.subTest(field=field), self.assertRaises(ValueError):
                read_subjects(self.world, self.config())

    def test_unknown_and_noninteger_inventory_and_actor_references_rejected(self):
        path = self.project/"subjects/ada.yaml"; raw = read_yaml(path)
        for field, value in [("inventory", {"Unknown": 1}), ("inventory", {"Unknown": True}),
                             ("relations", {"Unknown": {}}), ("companions", ["Unknown"]),
                             ("knowledge", ["Unknown"]), ("traits", {"diligence": float("inf")})]:
            write_yaml(path, {**raw, field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                read_subjects(self.world, self.config())

    def test_malformed_reference_containers_explicitly_rejected_at_public_boundary(self):
        before = tree(self.project)
        cases = [("world.yaml", "protagonist", {}),
                 ("subjects/ada.yaml", "goal", {"target": {}}),
                 ("subjects/ada.yaml", "knowledge", [{}]),
                 ("subjects/ada.yaml", "companions", [{}]),
                 ("subjects/ada.yaml", "range", {"zones": ["Bench"], "entry": {}})]
        for filename, key, value in cases:
            path = self.project/filename; write_yaml(path, {**read_yaml(path), key: value})
            for public in (lambda: read_subjects(self.world, self.config()), lambda: load_project(self.world)):
                with self.subTest(filename=filename, key=key), self.assertRaises(ValueError):
                    public()
            path.write_bytes(before[filename])

    def test_metadata_and_final_payload_size_limits_refuse_without_partial_snapshot(self):
        with self.assertRaises(ValueError):
            freeze_project(self.world, self.root/"metadata-too-large", metadata="x"*(256*1024+1))
        self.assertFalse((self.root/"metadata-too-large").exists())
        destination = self.root/"payload-too-large"
        # Actual input files all fit this reduced test boundary. The manifest
        # additionally contains compiled calendar and source-world provenance.
        limit = max(len(data) for data in tree(self.project).values())*3
        metadata = "x"*(limit-1)
        with patch("engine.calendar_inputs.FILE_LIMIT", limit):
            with self.assertRaisesRegex(ValueError, "manifest exceeds"):
                freeze_project(self.world, destination, metadata=metadata)
        self.assertFalse(destination.exists())

    def test_component_symlinks_and_hardlinked_roles_are_rejected(self):
        for link, target in [(self.project/"alias.yaml", self.project/"contract.yaml"),
                             (self.project/"aliases", self.project/"subjects")]:
            try:
                link.symlink_to(target, target_is_directory=target.is_dir())
            except OSError:
                self.skipTest("Local symlink creation unavailable")
            relative = link.name if target.is_file() else link.name+"/ada.yaml"
            with self.assertRaises(ValueError):
                safe_project_path(self.world, relative)
        alias = self.project/"hardlink.yaml"
        try:
            os.link(self.project/"subjects/ada.yaml", alias)
        except OSError:
            self.skipTest("Local hardlink creation unavailable")
        raw = read_yaml(self.world); raw["slot_execution"]["subjects"][1]["path"] = alias.name
        write_yaml(self.world, raw)
        with self.assertRaises(ValueError):
            read_subjects(self.world, raw["slot_execution"])

    def test_declared_action_graph_dependency_is_included_and_escape_rejected(self):
        graph = self.project/"graph.data"; write_yaml(graph, {"nodes": [], "edges": []})
        raw = read_yaml(self.world); raw["gapengine"] = {"action_graph": graph.name}; write_yaml(self.world, raw)
        destination, version = self.fixed()
        self.assertIn(graph.name, version["inputs"])
        self.assertEqual((destination/graph.name).read_bytes(), graph.read_bytes())
        outside = self.root/"outside.graph"; write_yaml(outside, {"nodes": [], "edges": []})
        raw["gapengine"]["action_graph"] = "../outside.graph"; write_yaml(self.world, raw)
        with self.assertRaises(ValueError):
            load_project(self.world)

    def test_freeze_keeps_arbitrary_names_layout_crlf_source_and_ignores_unused_source_files(self):
        raw = read_yaml(self.world)
        new_world = self.project/"arbitrary world.scene"
        (self.project/"contract.yaml").rename(self.project/"rules.clock")
        (self.project/"subjects/ada.yaml").rename(self.project/"subjects/A.person")
        raw["slot_execution"]["contract"] = "rules.clock"
        raw["slot_execution"]["subjects"][0]["path"] = "subjects/A.person"
        write_yaml(new_world, raw); new_world.write_bytes(new_world.read_bytes().replace(b"\n", b"\r\n"))
        (self.project/"unrelated.txt").write_text("not an input", encoding="utf-8")
        before = tree(self.project)
        version = freeze_project(new_world, self.root/"named-fixed", metadata={"review": ["explicit", None, 3]})
        self.assertEqual(version["world"], new_world.name)
        self.assertEqual(set(version["inputs"]), {new_world.name, "rules.clock", "subjects/A.person", "subjects/bo.yaml"})
        self.assertEqual(version["source_world"].encode("utf-8"), new_world.read_bytes())
        self.assertEqual(before, tree(self.project))
        fixed_world, people = load_project(self.root/"named-fixed"/version["world"])
        self.assertEqual(set(people), {"Ada", "Bo"})
        self.assertEqual(fixed_world.calendar_input_provenance["fixed_version_id"], version["id"])

    def test_old_schema_bool_version_and_duplicate_json_keys_are_rejected(self):
        destination, version = self.fixed()
        path = destination/version["world"]; manifest = destination/"execution-version.json"
        for old in (2, True):
            bad = deepcopy(version); bad["schema_version"] = old; self.save_version(destination, bad)
            with self.subTest(version=old), self.assertRaises(ValueError):
                validate_fixed_version(path, bad["configuration"], self.compiled(path))
        manifest.write_text('{"schema_version":3,"schema_version":3}', encoding="utf-8")
        with self.assertRaises(ValueError):
            load_project(path)

    def test_source_world_hash_lie_or_semantic_rewrite_rejected_after_resigning(self):
        destination, version = self.fixed(); path = destination/version["world"]
        lied = deepcopy(version); lied["source_inputs"][version["world"]]["sha256"] = "0"*64
        self.save_version(destination, lied)
        with self.assertRaises(ValueError):
            load_project(path)
        lied = deepcopy(version); raw = yaml.safe_load(lied["source_world"]); raw["name"] = "different source"
        lied["source_world"] = yaml.safe_dump(raw)
        data = lied["source_world"].encode("utf-8")
        lied["source_inputs"][version["world"]] = {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.save_version(destination, lied)
        with self.assertRaises(ValueError):
            load_project(path)

    def test_explicit_input_set_cannot_be_weakened_even_with_valid_checksum(self):
        destination, version = self.fixed(); path = destination/version["world"]
        for change in ("omit", "parent", "negative", "bool"):
            bad = deepcopy(version)
            if change == "omit": bad["inputs"].pop("subjects/bo.yaml")
            if change == "parent": bad["inputs"]["../world.yaml"] = bad["inputs"].pop(version["world"])
            if change == "negative": bad["inputs"][version["world"]]["size_bytes"] = -1
            if change == "bool": bad["inputs"][version["world"]]["size_bytes"] = True
            self.save_version(destination, bad)
            with self.subTest(change=change), self.assertRaises(ValueError):
                load_project(path)

    def test_missing_tampered_added_file_and_empty_directory_refused_without_writes(self):
        destination, version = self.fixed(); path = destination/version["world"]
        before = tree(destination)
        for name in ("extra.txt", "extra-directory"):
            extra = destination/name
            extra.write_text("unexpected") if name.endswith("txt") else extra.mkdir()
            observed = tree(destination)
            with self.assertRaises(ValueError): load_project(path)
            self.assertEqual(tree(destination), observed)
            extra.unlink() if extra.is_file() else extra.rmdir()
        subject = destination/"subjects/ada.yaml"
        subject.write_bytes(subject.read_bytes()+b"\n# changed bytes\n")
        with self.assertRaises(ValueError): load_project(path)
        subject.write_bytes(before["subjects/ada.yaml"])
        subject.unlink()
        with self.assertRaises(ValueError): load_project(path)

    def test_existing_destinations_and_invalid_metadata_are_protected(self):
        for name in ("empty", "occupied"):
            destination = self.root/name; destination.mkdir()
            if name == "occupied": (destination/"keep.txt").write_text("keep")
            before = tree(destination)
            with self.assertRaises(ValueError): freeze_project(self.world, destination)
            self.assertEqual(tree(destination), before)
        for value in (float("nan"), {"x": float("inf")}, {1: "nonstringkey"}, {"x": object()}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                freeze_project(self.world, self.root/"bad-metadata", metadata=value)
            self.assertFalse((self.root/"bad-metadata").exists())

    def test_failed_manifest_publication_removes_only_own_destination(self):
        before = tree(self.project); destination = self.root/"failed"
        with patch("engine.calendar_inputs.os.replace", side_effect=OSError("injected commit failure")):
            with self.assertRaises(OSError): freeze_project(self.world, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(tree(self.project), before)

    def test_uncommitted_fixed_tree_cannot_be_loaded_before_manifest_publish(self):
        destination = self.root/"fixed"
        original = os.replace
        observed = []
        def replace(source, target):
            with self.assertRaises(ValueError): load_project(destination/self.world.name)
            observed.append(True)
            original(source, target)
        with patch("engine.calendar_inputs.os.replace", side_effect=replace):
            version = freeze_project(self.world, destination)
        self.assertEqual(observed, [True])
        load_project(destination/version["world"])


if __name__ == "__main__":
    unittest.main()
