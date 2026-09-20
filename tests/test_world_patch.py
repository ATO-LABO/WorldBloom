"""Acceptance boundaries for expansion patches (WB-WORLDGROW-001, stage 3a)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from engine.world import World
from gapengine.world_patch import (
    PatchError,
    apply_patch,
    apply_patches,
    approved_patches,
    validate_patch,
)

ROOT = Path(__file__).resolve().parents[1]
WORLD_PATH = ROOT / "projects" / "momotaro" / "world.yaml"
ACTION_GRAPH_PATH = ROOT / "templates" / "momotaro" / "action_graph.yaml"


def load_world() -> dict:
    return yaml.safe_load(WORLD_PATH.read_text(encoding="utf-8"))


def sample_patch(**overrides) -> dict:
    patch = {
        "id": "p-1a2b3c4d",
        "title": "海辺の船大工小屋",
        "rationale": "海での investigate が空振りし続けている",
        "parent_rev": [],
        "trigger": {"experiment": "run-xxxx", "zone": "海", "verb": "investigate",
                    "count": 228, "whiffs": 228},
        "add": {
            "zones": [{"name": "船大工の小屋", "parent": "海", "note": "船具を扱う小屋"}],
            "items": [{"name": "古びた帆布", "sources": [
                {"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 2}]}],
            "facts": [{"id": "船大工の噂", "label": "小屋の主は昔ながらの船大工らしい",
                       "secrecy": 0.2, "share_min_affinity": 0.1,
                       "sources": [{"type": "investigate", "zone": "船大工の小屋", "count": 1}]}],
            "daily_events": [{"id": "sail_repair", "label": "帆の繕いを手伝った",
                               "weight": 1, "stress_delta": -0.2}],
        },
    }
    patch.update(overrides)
    return patch


class WorldPatchTests(unittest.TestCase):
    def test_valid_patch_applies_cleanly_and_world_is_untouched(self):
        world = load_world()
        original = yaml.safe_load(WORLD_PATH.read_text(encoding="utf-8"))
        patch = sample_patch()
        self.assertEqual(validate_patch(world, patch), [])

        patched = apply_patch(world, patch)

        self.assertEqual(world, original)  # apply_patch must not mutate its input
        zone_names = {z["name"] for z in patched["zones"]}
        self.assertIn("船大工の小屋", zone_names)
        self.assertIn({"to": "船大工の小屋"}, patched["routes"]["海"])
        self.assertEqual(patched["routes"]["船大工の小屋"], [{"to": "海"}])
        item_names = {i["name"] for i in patched["items"]}
        self.assertIn("古びた帆布", item_names)
        fact_ids = {f["id"] for f in patched["facts"]}
        self.assertIn("船大工の噂", fact_ids)
        event_ids = {e["id"] for e in patched["daily_events"]["events"]}
        self.assertIn("sail_repair", event_ids)
        self.assertEqual(patched["movement"]["destination_weights"]["船大工の小屋"], 1.0)
        self.assertEqual(patched["expansion"]["base"], "桃太郎")
        self.assertEqual(len(patched["expansion"]["patches"]), 1)
        entry = patched["expansion"]["patches"][0]
        self.assertEqual(entry["id"], "p-1a2b3c4d")
        self.assertEqual(entry["trigger"], {"zone": "海", "verb": "investigate"})
        self.assertEqual(entry["added"]["zones"], ["船大工の小屋"])
        self.assertEqual(entry["added"]["items"], ["古びた帆布"])
        self.assertEqual(entry["added"]["facts"], ["船大工の噂"])
        self.assertEqual(entry["added"]["daily_events"], ["sail_repair"])

    def test_patched_world_loads_into_engine(self):
        world = load_world()
        patched = apply_patch(world, sample_patch())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "world.yaml"
            path.write_text(yaml.safe_dump(patched, allow_unicode=True, sort_keys=False), encoding="utf-8")
            loaded = World.from_yaml(path, action_graph_path=ACTION_GRAPH_PATH)
        self.assertIn("船大工の小屋", loaded.zones)
        self.assertIn("古びた帆布", loaded.items)
        self.assertIn("船大工の噂", loaded.facts)

    def test_apply_patches_chains_two_patches(self):
        world = load_world()
        first = sample_patch()
        second = sample_patch(
            id="p-2b3c4d5e", title="小屋裏の物置",
            add={"zones": [], "items": [{"name": "使い古しの縄",
                    "sources": [{"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 1}]}],
                 "facts": [], "daily_events": []},
        )
        result = apply_patches(world, [first, second])
        item_names = {i["name"] for i in result["items"]}
        self.assertIn("使い古しの縄", item_names)
        self.assertEqual(len(result["expansion"]["patches"]), 2)

    def test_apply_patches_empty_returns_world_unchanged(self):
        world = load_world()
        result = apply_patches(world, [])
        self.assertIs(result, world)
        self.assertNotIn("expansion", result)

    def test_apply_patches_raises_patch_error_with_id_prefix(self):
        world = load_world()
        bad = sample_patch(add={"zones": [], "items": [], "facts": [], "daily_events": []})
        with self.assertRaises(PatchError) as caught:
            apply_patches(world, [bad])
        self.assertTrue(str(caught.exception).startswith("p-1a2b3c4d:"))

    def assert_invalid(self, mutate):
        world = load_world()
        patch = sample_patch()
        mutate(patch)
        violations = validate_patch(world, patch)
        self.assertTrue(violations, patch)

    def test_unknown_top_level_key(self):
        self.assert_invalid(lambda p: p.update(unknown_field="x"))

    def test_unknown_add_key(self):
        self.assert_invalid(lambda p: p["add"].update(objective=True))

    def test_empty_add(self):
        self.assert_invalid(lambda p: p.update(add={"zones": [], "items": [], "facts": [], "daily_events": []}))

    def test_budget_exceeded(self):
        self.assert_invalid(lambda p: p["add"].update(zones=[
            {"name": f"新ゾーン{i}", "parent": "海"} for i in range(3)]))

    def test_name_collides_with_existing_zone(self):
        self.assert_invalid(lambda p: p["add"]["zones"][0].update(name="村"))

    def test_name_collides_with_subject_id(self):
        world = load_world()
        patch = sample_patch()
        violations = validate_patch(world, patch, subject_ids=["船大工の小屋"])
        self.assertTrue(violations)

    def test_zone_and_item_same_name_collide(self):
        self.assert_invalid(lambda p: p["add"]["items"][0].update(name="船大工の小屋"))

    def test_parent_does_not_exist(self):
        self.assert_invalid(lambda p: p["add"]["zones"][0].update(parent="存在しない場所"))

    def test_parent_is_new_zone_in_same_patch(self):
        self.assert_invalid(lambda p: p["add"].update(zones=[
            {"name": "船大工の小屋", "parent": "海"},
            {"name": "小屋の裏庭", "parent": "船大工の小屋"},
        ]))

    def test_item_with_objective_flag(self):
        self.assert_invalid(lambda p: p["add"]["items"][0].update(objective=True))

    def test_item_with_vehicle_flag(self):
        self.assert_invalid(lambda p: p["add"]["items"][0].update(vehicle=True))

    def test_modifier_with_lethal(self):
        self.assert_invalid(lambda p: p["add"]["items"][0].update(
            modifier={"id": "item:古びた帆布", "value": 1, "kind": "item", "visible": True, "lethal": True}))

    def test_modifier_value_out_of_range(self):
        self.assert_invalid(lambda p: p["add"]["items"][0].update(
            modifier={"id": "item:古びた帆布", "value": 11, "kind": "item", "visible": True}))

    def test_item_source_count_not_one(self):
        self.assert_invalid(lambda p: p["add"]["items"][0]["sources"][0].update(count=2))

    def test_item_source_with_agent(self):
        self.assert_invalid(lambda p: p["add"]["items"][0].update(
            sources=[{"type": "investigate", "agent": "鬼", "count": 1, "max": 1}]))

    def test_made_from_existing_item(self):
        self.assert_invalid(lambda p: p["add"]["items"][0].update(
            sources=None, made_from={"木材": 1}))

    def test_item_without_acquisition_method(self):
        self.assert_invalid(lambda p: p["add"]["items"][0].update(sources=None))

    def test_fact_with_values(self):
        self.assert_invalid(lambda p: p["add"]["facts"][0].update(values=["a", "b"]))

    def test_implies_confidence_too_high(self):
        self.assert_invalid(lambda p: p["add"]["facts"][0].update(
            implies={"fact": "oni_weakness", "value": "金棒", "confidence": 0.6}))

    def test_implies_fact_not_valued(self):
        self.assert_invalid(lambda p: p["add"]["facts"][0].update(
            implies={"fact": "造船術", "value": "x", "confidence": 0.3}))

    def test_daily_event_without_slot_in_world(self):
        world = load_world()
        world["daily_events"] = None
        patch = sample_patch()
        violations = validate_patch(world, patch)
        self.assertTrue(violations)

    def test_name_with_quote(self):
        self.assert_invalid(lambda p: p["add"]["zones"][0].update(name="船'大工"))

    def test_name_appears_in_ending_predicate(self):
        # "鬼ヶ島の宝物" (an existing item name referenced by the ending's
        # `deliver`/predicate) contains "宝物" as a substring.
        self.assert_invalid(lambda p: p["add"].update(
            zones=[], items=[], daily_events=[],
            facts=[{"id": "宝物", "label": "何かの噂",
                    "sources": [{"type": "investigate", "zone": "海", "count": 1}]}]))

    def test_approved_patches_ignores_proposed_and_sorts_by_seq(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            patches_dir = project / "patches"
            proposed_dir = patches_dir / "_proposed"
            proposed_dir.mkdir(parents=True)
            (patches_dir / "p-second-seq.yaml").write_text(
                yaml.safe_dump({**sample_patch(id="p-second-seq"), "approved_seq": 2}, allow_unicode=True),
                encoding="utf-8")
            (patches_dir / "p-first-seq.yaml").write_text(
                yaml.safe_dump({**sample_patch(id="p-first-seq"), "approved_seq": 1}, allow_unicode=True),
                encoding="utf-8")
            (proposed_dir / "p-pending.yaml").write_text(
                yaml.safe_dump(sample_patch(id="p-pending-seq"), allow_unicode=True), encoding="utf-8")
            patches = approved_patches(project)
        self.assertEqual([p["id"] for p in patches], ["p-first-seq", "p-second-seq"])

    def test_approved_patches_rejects_an_id_that_differs_from_the_file_name(self):
        with tempfile.TemporaryDirectory() as temp:
            patches_dir = Path(temp) / "patches"
            patches_dir.mkdir()
            (patches_dir / "p-renamed.yaml").write_text(
                yaml.safe_dump({**sample_patch(id="p-original"), "approved_seq": 1}, allow_unicode=True),
                encoding="utf-8")
            with self.assertRaises(PatchError):
                approved_patches(Path(temp))

    def test_approved_patches_missing_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(approved_patches(Path(temp)), [])

    def test_approved_patches_rejects_missing_seq(self):
        with tempfile.TemporaryDirectory() as temp:
            patches_dir = Path(temp) / "patches"
            patches_dir.mkdir()
            (patches_dir / "p-x.yaml").write_text(
                yaml.safe_dump(sample_patch(), allow_unicode=True), encoding="utf-8")
            with self.assertRaises(PatchError):
                approved_patches(Path(temp))


if __name__ == "__main__":
    unittest.main()
