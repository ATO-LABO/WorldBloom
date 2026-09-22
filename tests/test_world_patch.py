"""Acceptance boundaries for expansion patches (WB-WORLDGROW-001, stage 3a)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml
from world_patch_fixtures import write_approved
from gapengine.world_patch import EMPTY_STACK_DIGEST

from engine.sim import Simulation
from engine.world import World
from gapengine.world_patch import (
    PatchError,
    absolutize_references,
    apply_patch,
    apply_patches,
    approved_patches,
    lottery_facts,
    materialize,
    patch_id_for,
    validate_patch,
)
from gapengine.world_patch_contract import contract_check
from gapengine.world_patch_inputs import read_subjects
from test_detective import PROJECT as DETECTIVE_PROJECT
from test_detective import SUSPECTS
from test_detective import TEMPLATE as DETECTIVE_TEMPLATE
from test_detective import load_subjects as load_detective_subjects

ROOT = Path(__file__).resolve().parents[1]
WORLD_PATH = ROOT / "projects" / "momotaro" / "world.yaml"
ACTION_GRAPH_PATH = ROOT / "templates" / "momotaro" / "action_graph.yaml"
DETECTIVE_WORLD_PATH = DETECTIVE_PROJECT / "world.yaml"
DETECTIVE_ACTION_GRAPH_PATH = DETECTIVE_TEMPLATE / "action_graph.yaml"


def load_world() -> dict:
    return yaml.safe_load(WORLD_PATH.read_text(encoding="utf-8"))


def sample_patch(**overrides) -> dict:
    patch = {
        "id": "p-1a2b3c4d",
        "title": "海辺の船大工小屋",
        "rationale": "海での investigate が空振りし続けている",
        "parent_digest": EMPTY_STACK_DIGEST,
        "trigger": {"experiment": "run-xxxx", "zone": "海", "verb": "investigate",
                    "count": 228, "whiffs": 228},
        "add": {
            "zones": [{"name": "船大工の小屋", "parent": "海", "note": "船具を扱う小屋"}],
            "items": [{"name": "古びた帆布", "sources": [
                {"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 2}]}],
            "facts": [{"id": "船大工の噂", "label": "小屋の主は昔ながらの船大工らしい",
                       "secrecy": 0.2, "share_min_affinity": 0.1,
                       "sources": [{"type": "investigate", "zone": "船大工の小屋", "count": 1}]}],
        },
    }
    patch.update(overrides)
    return patch


def _world_with_applied_modifier(value: float) -> dict:
    """A momotaro world with one earlier expansion patch already applied,
    having added a single item worth `value` modifier points."""
    world = load_world()
    name = "既存強化アイテム"
    world["items"] = list(world["items"]) + [{
        "name": name, "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}],
        "modifier": {"id": f"item:{name}", "value": value, "kind": "item", "visible": True},
    }]
    world["expansion"] = {"base": world.get("name"), "patches": [
        {"id": "p-existing1", "title": "既存パッチ",
         "added": {"zones": [], "items": [name], "facts": []}},
    ]}
    return world


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
        self.assertNotIn("sail_repair", event_ids)
        self.assertEqual(patched["movement"]["destination_weights"]["船大工の小屋"], 1.0)
        self.assertEqual(patched["expansion"]["base"], "桃太郎")
        self.assertEqual(len(patched["expansion"]["patches"]), 1)
        entry = patched["expansion"]["patches"][0]
        self.assertEqual(entry["id"], "p-1a2b3c4d")
        self.assertEqual(entry["trigger"], {"zone": "海", "verb": "investigate"})
        self.assertEqual(entry["added"]["zones"], ["船大工の小屋"])
        self.assertEqual(entry["added"]["items"], ["古びた帆布"])
        self.assertEqual(entry["added"]["facts"], ["船大工の噂"])
        self.assertEqual(entry["added"]["daily_events"], [])

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
                 "facts": []},
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
        bad = sample_patch(add={"zones": [], "items": [], "facts": []})
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
        self.assert_invalid(lambda p: p.update(add={"zones": [], "items": [], "facts": []}))

    def test_budget_exceeded(self):
        self.assert_invalid(lambda p: p["add"].update(zones=[
            {"name": f"新ゾーン{i}", "parent": "海"} for i in range(3)]))

    def test_modifier_budget_exceeded_within_single_patch(self):
        world = load_world()
        patch = sample_patch(add={
            "zones": [], "facts": [],
            "items": [
                {"name": "光る飾り玉その一", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}],
                 "modifier": {"id": "item:光る飾り玉その一", "value": 6, "kind": "item", "visible": True}},
                {"name": "光る飾り玉その二", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}],
                 "modifier": {"id": "item:光る飾り玉その二", "value": 5, "kind": "item", "visible": True}},
            ],
        })
        violations = validate_patch(world, patch)
        self.assertTrue(any("強化値の合計が上限（10）を超えています" in v for v in violations), violations)

    def test_modifier_budget_cumulative_exceeded(self):
        world = _world_with_applied_modifier(15)
        patch = sample_patch(add={
            "zones": [], "facts": [],
            "items": [{"name": "新しい強化アイテム", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}],
                       "modifier": {"id": "item:新しい強化アイテム", "value": 6, "kind": "item", "visible": True}}],
        })
        violations = validate_patch(world, patch)
        self.assertTrue(any("強化値の合計が上限（20）を超えています" in v for v in violations), violations)

    def test_modifier_budget_cumulative_exactly_at_limit_passes(self):
        world = _world_with_applied_modifier(14)
        patch = sample_patch(add={
            "zones": [], "facts": [],
            "items": [{"name": "新しい強化アイテム", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}],
                       "modifier": {"id": "item:新しい強化アイテム", "value": 6, "kind": "item", "visible": True}}],
        })
        self.assertEqual(validate_patch(world, patch), [])

    def test_give_budget_multiplies_by_source_max(self):
        # (0.2 + 0.05) x max:3 = 0.75, over MAX_GIVE_PER_PATCH (0.6).
        world = load_world()
        patch = sample_patch(add={
            "zones": [], "facts": [],
            "items": [{"name": "潮見の貝殻", "give": {"receiver_affinity": 0.2, "giver_affinity": 0.05},
                       "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 3}]}],
        })
        violations = validate_patch(world, patch)
        self.assertTrue(any("渡したときの効果の総量が上限を超えています" in v for v in violations), violations)

    def test_give_budget_within_limit_at_max_two_passes(self):
        # (0.2 + 0.05) x max:2 = 0.5, within MAX_GIVE_PER_PATCH (0.6).
        world = load_world()
        patch = sample_patch(add={
            "zones": [], "facts": [],
            "items": [{"name": "潮見の貝殻", "give": {"receiver_affinity": 0.2, "giver_affinity": 0.05},
                       "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 2}]}],
        })
        self.assertEqual(validate_patch(world, patch), [])

    def test_give_budget_excludes_keepsake_items(self):
        # keepsake items are never handed over (engine/actions.py's
        # _give_candidates excludes them), so their give never counts against
        # the budget even if `give` is present.
        world = load_world()
        patch = sample_patch(add={
            "zones": [], "facts": [],
            "items": [{"name": "潮見の貝殻", "keepsake": True,
                       "give": {"receiver_affinity": 0.5, "giver_affinity": 0.5},
                       "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 3}]}],
        })
        self.assertEqual(validate_patch(world, patch), [])

    def test_give_budget_is_not_charged_when_no_subject_can_give(self):
        # Same max:3 item that fails above: with no give_item verb in the
        # world, give never fires, so a plentiful item must not be rejected
        # for a budget it cannot spend.
        world = load_world()
        patch = sample_patch(add={
            "zones": [], "facts": [],
            "items": [{"name": "潮見の貝殻",
                       "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 3}]}],
        })
        self.assertTrue(validate_patch(world, patch))
        self.assertEqual(validate_patch(world, patch, give_available=False), [])

    def test_give_budget_survives_non_list_sources(self):
        # M1: item_give_count used to iterate item["sources"] unconditionally
        # -- a scalar (int/bool/float) there raised TypeError instead of
        # accumulating as a violation. validate_patch must never raise.
        world = load_world()
        for bad_sources in (5, True, 1.5):
            patch = sample_patch(add={
                "zones": [], "facts": [],
                "items": [{"name": "壊れた品", "give": {"receiver_affinity": 0.1, "giver_affinity": 0.0},
                           "sources": bad_sources}],
            })
            violations = validate_patch(world, patch)
            self.assertTrue(violations, (bad_sources, violations))

    def test_made_from_self_reference_cycle(self):
        world = load_world()
        patch = sample_patch(add={
            "zones": [], "facts": [],
            "items": [{"name": "甲片", "made_from": {"甲片": 1}}],
        })
        violations = validate_patch(world, patch)
        self.assertTrue(any("made_from が循環しています" in v for v in violations), violations)

    def test_made_from_two_item_cycle(self):
        world = load_world()
        patch = sample_patch(add={
            "zones": [], "facts": [],
            "items": [
                {"name": "甲片", "made_from": {"乙片": 1}},
                {"name": "乙片", "made_from": {"甲片": 1}},
            ],
        })
        violations = validate_patch(world, patch)
        self.assertTrue(any("made_from が循環しています" in v for v in violations), violations)

    def test_give_budget_excludes_made_from_items(self):
        # R1: made_from items are excluded from _give_candidates the same
        # way keepsake items are (engine/actions.py) -- a crafted recipe's
        # `give` must not count against the budget even if present.
        world = load_world()
        patch = sample_patch(add={
            "zones": [], "facts": [],
            "items": [{"name": "潮見の貝殻", "made_from": {"帆布片": 1},
                       "give": {"receiver_affinity": 0.5, "giver_affinity": 0.5},
                       "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 3}]},
                      {"name": "帆布片", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}]}],
        })
        violations = validate_patch(world, patch)
        self.assertFalse(any("渡したときの効果の総量が上限を超えています" in v for v in violations), violations)

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

    def test_parent_cannot_be_a_previously_added_branch(self):
        # R2: a branch is one hop off a *base* zone only -- contract_check's
        # admission tests never look past that one hop, so a branch of a
        # branch would ship with an unverified entry condition.
        world = load_world()
        world["zones"] = list(world["zones"]) + [{"name": "船大工の小屋"}]
        world["routes"]["海"] = list(world["routes"]["海"]) + [{"to": "船大工の小屋"}]
        world["routes"]["船大工の小屋"] = [{"to": "海"}]
        world["expansion"] = {"base": world.get("name"), "patches": [
            {"id": "p-existing1", "title": "既存パッチ",
             "added": {"zones": ["船大工の小屋"], "items": [], "facts": []}},
        ]}
        patch = sample_patch()
        patch["add"]["zones"][0].update(name="小屋の裏庭", parent="船大工の小屋")
        violations = validate_patch(world, patch)
        self.assertTrue(any("枝は1段まで" in v for v in violations), violations)

        # A previously added branch remains usable as an item/fact source
        # zone (not as a parent) -- this must stay unaffected.
        unaffected = sample_patch()
        unaffected["add"]["zones"] = []
        self.assertEqual(validate_patch(world, unaffected), [])

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
        patch["add"]["daily_events"] = [{"id": "forbidden"}]
        violations = validate_patch(world, patch)
        self.assertTrue(violations)

    def test_name_with_quote(self):
        self.assert_invalid(lambda p: p["add"]["zones"][0].update(name="船'大工"))

    def test_name_appears_in_ending_predicate(self):
        # "鬼ヶ島の宝物" (an existing item name referenced by the ending's
        # `deliver`/predicate) contains "宝物" as a substring.
        self.assert_invalid(lambda p: p["add"].update(
            zones=[], items=[],
            facts=[{"id": "宝物", "label": "何かの噂",
                    "sources": [{"type": "investigate", "zone": "海", "count": 1}]}]))

    def test_approved_patches_ignores_proposed_and_uses_manifest_order(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            first = write_approved(project, sample_patch())
            other = sample_patch(add={"zones": [{"name": "second", "parent": "海"}]})
            second = write_approved(project, other)
            proposed = project / "patches/_proposed"
            proposed.mkdir()
            (proposed / "p-pending.yaml").write_text("invalid", encoding="utf-8")
            self.assertEqual([p["id"] for p in approved_patches(project)], [first["id"], second["id"]])

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

    def test_patch_id_for_is_content_addressed(self):
        add = {"zones": [{"name": "船大工の小屋", "parent": "海"}]}
        self.assertEqual(patch_id_for(add), patch_id_for(dict(add)))
        self.assertNotEqual(patch_id_for(add), patch_id_for({"zones": []}))
        self.assertTrue(patch_id_for(add).startswith("p-"))

    def test_absolutize_references_prefers_project_relative_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp) / "project"
            repo_root = Path(temp) / "repo"
            (project_dir / "local").mkdir(parents=True)
            (project_dir / "local" / "action_graph.yaml").write_text("nodes: []\nedges: []\n", encoding="utf-8")
            repo_root.mkdir()
            world = {"gapengine": {"action_graph": "local/action_graph.yaml", "effects": "missing/effects.yaml"}}
            absolutize_references(world, project_dir, repo_root)
            self.assertEqual(
                Path(world["gapengine"]["action_graph"]),
                (project_dir / "local" / "action_graph.yaml").resolve(),
            )
            # Neither candidate exists for effects -- left untouched.
            self.assertEqual(world["gapengine"]["effects"], "missing/effects.yaml")

    # -- R6: malformed-type inputs must produce violations, never raise ----

    def test_item_source_zone_empty_list_does_not_raise(self):
        self.assert_invalid(lambda p: p["add"]["items"][0]["sources"][0].update(zone=[]))

    def test_item_source_zone_empty_dict_does_not_raise(self):
        self.assert_invalid(lambda p: p["add"]["items"][0]["sources"][0].update(zone={}))

    def test_fact_source_zone_empty_list_does_not_raise(self):
        self.assert_invalid(lambda p: p["add"]["facts"][0]["sources"][0].update(zone=[]))

    def test_requires_knowledge_empty_list_does_not_raise(self):
        self.assert_invalid(lambda p: p["add"]["items"][0].update(requires={"knowledge": []}))

    def test_parent_empty_dict_does_not_raise(self):
        self.assert_invalid(lambda p: p["add"]["zones"][0].update(parent={}))

    def test_made_from_material_key_is_numeric_does_not_raise(self):
        self.assert_invalid(lambda p: p["add"]["items"][0].update(sources=None, made_from={123: 1}))

    def test_item_source_count_true_is_rejected_not_accepted_as_one(self):
        self.assert_invalid(lambda p: p["add"]["items"][0]["sources"][0].update(count=True))

    def test_item_source_count_float_one_is_rejected(self):
        self.assert_invalid(lambda p: p["add"]["items"][0]["sources"][0].update(count=1.0))

    def test_fact_source_count_true_is_rejected(self):
        self.assert_invalid(lambda p: p["add"]["facts"][0]["sources"][0].update(count=True))

    def test_item_source_max_float_is_rejected(self):
        self.assert_invalid(lambda p: p["add"]["items"][0]["sources"][0].update(max=2.0))

    def test_implies_fact_is_list_does_not_raise(self):
        self.assert_invalid(lambda p: p["add"]["facts"][0].update(
            implies={"fact": ["oni_weakness"], "value": "金棒", "confidence": 0.3}))

    def test_unknown_top_level_key_numeric_does_not_raise(self):
        self.assert_invalid(lambda p: p.update({123: "x"}))

    def test_check_trigger_coverage_survives_malformed_add(self):
        from gapengine.world_patch_propose import check_trigger_coverage
        broken = {"items": {"name": "x"}, "facts": "not-a-list"}
        self.assertEqual(
            check_trigger_coverage(broken, {"zone": "海"}),
            ["きっかけの場所そのものに調べて得られるものが足されていません"])

    # -- R10: names reserved by the engine ----------------------------------

    def test_reserved_fact_id_gossip_is_rejected(self):
        world = load_world()
        patch = sample_patch(add={
            "zones": [], "items": [],
            "facts": [{"id": "雑談", "label": "他愛のない世間話",
                       "sources": [{"type": "investigate", "zone": "海", "count": 1}]}],
        })
        violations = validate_patch(world, patch)
        self.assertTrue(any("予約された名前は使えません" in v for v in violations), violations)

    def test_reserved_argument_name_is_rejected(self):
        world = load_world()
        patch = sample_patch(add={
            "zones": [{"name": "見張り台", "parent": "海"}], "items": [], "facts": [],
        })
        violations = validate_patch(world, patch, reserved=("見張り台",))
        self.assertTrue(any("予約された名前は使えません: 見張り台" in v for v in violations), violations)

    def test_template_identifiers_reads_verb_and_rule_ids_from_momotaro(self):
        from gapengine.world_patch import template_identifiers
        identifiers = template_identifiers(ROOT / "templates" / "momotaro")
        self.assertIn("investigate", identifiers)

    def test_template_identifiers_missing_dir_returns_empty(self):
        from gapengine.world_patch import template_identifiers
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(template_identifiers(Path(temp) / "nope"), set())

    def test_absolutize_references_falls_back_to_repo_root(self):
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp) / "project"
            project_dir.mkdir()
            repo_root = Path(temp) / "repo"
            (repo_root / "templates" / "x").mkdir(parents=True)
            (repo_root / "templates" / "x" / "effects.yaml").write_text("[]\n", encoding="utf-8")
            world = {"gapengine": {"effects": "templates/x/effects.yaml"}}
            absolutize_references(world, project_dir, repo_root)
            self.assertEqual(
                Path(world["gapengine"]["effects"]),
                (repo_root / "templates" / "x" / "effects.yaml").resolve(),
            )


def load_detective_world() -> dict:
    return yaml.safe_load(DETECTIVE_WORLD_PATH.read_text(encoding="utf-8"))


def detective_fact(**overrides) -> dict:
    fact = {
        "id": "怪しい足跡",
        "label": "書斎の窓辺に泥の足跡が残っていた",
        "secrecy": 0.2,
        "sources": [{"type": "investigate", "zone": "食堂", "count": 1}],
        "implies": {"fact": "culprit", "value": "$innocent:1", "confidence": 0.25},
    }
    fact.update(overrides)
    return fact


def detective_patch(facts: list[dict], **overrides) -> dict:
    patch = {"id": "p-lottery01", "title": "くじ試験", "add": {"zones": [], "items": [], "facts": facts}}
    patch.update(overrides)
    return patch


class LotteryTruthImpliesTests(unittest.TestCase):
    """WB-WORLD-DEMAND: implies against a per-seed-drawn fact (world["truth"]
    [fact].candidates, e.g. detective's culprit/weapon) must use $truth/
    $innocent:N instead of a fixed candidate name -- a fixed name would mean
    something different every seed instead of what the author intended."""

    def test_truth_and_innocent_tokens_pass(self):
        world = load_detective_world()
        patch = detective_patch([
            detective_fact(implies={"fact": "culprit", "value": "$innocent:1", "confidence": 0.25}),
            detective_fact(id="血のついた手袋", label="誰かの手袋に血の跡が付いていた",
                            sources=[{"type": "investigate", "zone": "客室", "count": 1}],
                            implies={"fact": "culprit", "value": "$truth", "confidence": 0.25}),
        ])
        self.assertEqual(validate_patch(world, patch), [])

    def test_fixed_name_against_lottery_fact_is_rejected(self):
        world = load_detective_world()
        patch = detective_patch([detective_fact(
            implies={"fact": "culprit", "value": "容疑者甲", "confidence": 0.25})])
        violations = validate_patch(world, patch)
        self.assertTrue(any("value は $truth か $innocent:N で書いてください" in v for v in violations), violations)

    def test_innocent_n_out_of_range_high(self):
        world = load_detective_world()
        patch = detective_patch([detective_fact(
            implies={"fact": "culprit", "value": "$innocent:3", "confidence": 0.25})])
        violations = validate_patch(world, patch)
        self.assertTrue(any("$innocent:N の N は1〜2で指定してください" in v for v in violations), violations)

    def test_innocent_n_is_capped_at_what_the_engine_knows(self):
        # engine/world.py's truth_tokens stops at $innocent:2, so a fourth
        # candidate must not make $innocent:3 pass the gate only to raise
        # when the engine builds the world.
        world = load_detective_world()
        world["truth"]["culprit"]["candidates"]["容疑者丁"] = 1
        patch = detective_patch([detective_fact(
            implies={"fact": "culprit", "value": "$innocent:3", "confidence": 0.25})])
        violations = validate_patch(world, patch)
        self.assertTrue(any("$innocent:N の N は1〜2で指定してください" in v for v in violations), violations)

    def test_innocent_token_must_be_the_exact_engine_spelling(self):
        # The engine matches tokens literally: a zero-padded or full-width N
        # parses to 1 but raises "Unknown truth-relative value" at world build.
        world = load_detective_world()
        for token in ("$innocent:01", "$innocent:001", "$innocent:１", "$innocent:1 ", " $truth"):
            patch = detective_patch([detective_fact(
                implies={"fact": "culprit", "value": token, "confidence": 0.25})])
            violations = validate_patch(world, patch)
            self.assertTrue(any("$truth か $innocent:N" in v or "$innocent:N の N は" in v for v in violations),
                            (token, violations))

    def test_label_and_id_may_not_name_any_lottery_candidate(self):
        # weapon is drawn per seed too, so naming a weapon in a culprit clue is
        # just as seed-dependent; the id reaches belief records like the label.
        world = load_detective_world()
        implies = {"fact": "culprit", "value": "$truth", "confidence": 0.25}
        by_label = detective_patch([detective_fact(label="燭台のそばに誰かの足跡が残っていた", implies=implies)])
        self.assertTrue(any("label に候補の名前は書けません" in v for v in validate_patch(world, by_label)))
        by_id = detective_patch([detective_fact(id="容疑者甲の足跡", implies=implies)])
        self.assertTrue(any("id に候補の名前は書けません" in v for v in validate_patch(world, by_id)))

    def test_innocent_n_out_of_range_zero(self):
        world = load_detective_world()
        patch = detective_patch([detective_fact(
            implies={"fact": "culprit", "value": "$innocent:0", "confidence": 0.25})])
        violations = validate_patch(world, patch)
        self.assertTrue(any("$innocent:N の N は1〜2で指定してください" in v for v in violations), violations)

    def test_innocent_non_numeric_suffix(self):
        world = load_detective_world()
        patch = detective_patch([detective_fact(
            implies={"fact": "culprit", "value": "$innocent:x", "confidence": 0.25})])
        violations = validate_patch(world, patch)
        self.assertTrue(any("$innocent:N の N は1〜2で指定してください" in v for v in violations), violations)

    def test_innocent_missing_suffix(self):
        world = load_detective_world()
        patch = detective_patch([detective_fact(
            implies={"fact": "culprit", "value": "$innocent:", "confidence": 0.25})])
        violations = validate_patch(world, patch)
        self.assertTrue(any("$innocent:N の N は1〜2で指定してください" in v for v in violations), violations)

    def test_wrong_case_truth_token(self):
        world = load_detective_world()
        patch = detective_patch([detective_fact(
            implies={"fact": "culprit", "value": "$Truth", "confidence": 0.25})])
        violations = validate_patch(world, patch)
        self.assertTrue(any("$innocent:N の N は1〜2で指定してください" in v for v in violations), violations)

    def test_label_naming_a_candidate_is_rejected(self):
        world = load_detective_world()
        patch = detective_patch([detective_fact(
            label="容疑者乙は書斎の窓辺に立っていたらしい",
            implies={"fact": "culprit", "value": "$innocent:1", "confidence": 0.25})])
        violations = validate_patch(world, patch)
        self.assertTrue(any("label に候補の名前は書けません" in v and "容疑者乙" in v for v in violations), violations)

    def test_label_naming_a_candidate_without_implies_is_not_flagged_by_this_rule(self):
        world = load_detective_world()
        fact = detective_fact(label="容疑者乙は書斎の窓辺に立っていたらしい")
        fact.pop("implies")
        patch = detective_patch([fact])
        violations = validate_patch(world, patch)
        self.assertFalse(any("label に候補の名前は書けません" in v for v in violations), violations)

    def test_momotaro_fixed_truth_rejects_a_token_and_accepts_the_fixed_name(self):
        world = load_world()
        token_patch = sample_patch(add={
            "zones": [], "items": [],
            "facts": [{"id": "囲炉裏端の噂", "label": "鬼は熱いものを嫌うと聞いた", "secrecy": 0.2,
                       "sources": [{"type": "investigate", "zone": "海", "count": 1}],
                       "implies": {"fact": "oni_weakness", "value": "$truth", "confidence": 0.25}}],
        })
        violations = validate_patch(world, token_patch)
        self.assertTrue(any("value は名前で書いてください" in v for v in violations), violations)

        fixed_patch = sample_patch(add={
            "zones": [], "items": [],
            "facts": [{"id": "囲炉裏端の噂", "label": "鬼は熱いものを嫌うと聞いた", "secrecy": 0.2,
                       "sources": [{"type": "investigate", "zone": "海", "count": 1}],
                       "implies": {"fact": "oni_weakness", "value": "塩", "confidence": 0.25}}],
        })
        self.assertEqual(validate_patch(world, fixed_patch), [])

    # -- R6-style: malformed shapes must produce violations, never raise ----

    def test_truth_as_list_does_not_raise(self):
        world = load_detective_world()
        world["truth"] = ["culprit"]
        patch = detective_patch([detective_fact()])
        self.assertEqual(lottery_facts(world), {})
        violations = validate_patch(world, patch)
        self.assertIsInstance(violations, list)

    def test_candidates_as_list_does_not_raise(self):
        world = load_detective_world()
        world["truth"]["culprit"]["candidates"] = ["容疑者甲", "容疑者乙", "容疑者丙"]
        patch = detective_patch([detective_fact()])
        self.assertEqual(lottery_facts(world).get("culprit"), None)
        violations = validate_patch(world, patch)
        self.assertIsInstance(violations, list)

    def test_implies_value_non_string_types_do_not_raise(self):
        world = load_detective_world()
        for bad_value in (1, None, {}, ["容疑者甲"], True):
            patch = detective_patch([detective_fact(
                implies={"fact": "culprit", "value": bad_value, "confidence": 0.25})])
            violations = validate_patch(world, patch)
            self.assertIsInstance(violations, list)
            self.assertTrue(violations, (bad_value, violations))

    # -- engine integration: tokens resolve per seed -------------------------

    def test_token_implies_resolves_per_seed_in_the_engine(self):
        world = load_detective_world()
        subjects = read_subjects(DETECTIVE_PROJECT / "subjects")
        patch = detective_patch([detective_fact()])
        patched_world, _ = materialize(world, subjects, [patch])  # raises PatchError if the gate rejects it

        with tempfile.TemporaryDirectory() as temp:
            world_path = Path(temp) / "world.yaml"
            world_path.write_text(yaml.safe_dump(patched_world, allow_unicode=True, sort_keys=False),
                                   encoding="utf-8")
            loaded = World.from_yaml(world_path, action_graph_path=DETECTIVE_ACTION_GRAPH_PATH)

            seen: dict[int, tuple[str, str]] = {}
            for seed in range(8):
                people = load_detective_subjects()
                loaded.resolve_truth(seed)
                loaded.bind_subjects(people)
                culprit = loaded.truth["culprit"]
                resolved = loaded.facts["怪しい足跡"]["implies"]["value"]
                self.assertIn(resolved, SUSPECTS)
                self.assertNotEqual(resolved, culprit)  # $innocent:1 must never point at the real culprit
                seen[seed] = (culprit, resolved)

            # (b)/(c): across seeds, the drawn culprit varies, and for at
            # least one pair of seeds with a different culprit, the
            # innocent-1 resolution differs too (it need not differ for
            # *every* such pair -- with 3 suspects, two different culprits
            # can share the same "first innocent" by coincidence).
            distinct_culprits = {culprit for culprit, _ in seen.values()}
            self.assertGreater(len(distinct_culprits), 1, seen)
            diff_culprit_pairs = [(seen[x], seen[y]) for x in seen for y in seen if seen[x][0] != seen[y][0]]
            self.assertTrue(any(a[1] != b[1] for a, b in diff_culprit_pairs), seen)

    def test_truth_token_resolves_to_the_actual_culprit(self):
        world = load_detective_world()
        subjects = read_subjects(DETECTIVE_PROJECT / "subjects")
        patch = detective_patch([detective_fact(
            id="血のついた手袋", label="誰かの手袋に血の跡が付いていた",
            sources=[{"type": "investigate", "zone": "客室", "count": 1}],
            implies={"fact": "culprit", "value": "$truth", "confidence": 0.25})])
        patched_world, _ = materialize(world, subjects, [patch])

        with tempfile.TemporaryDirectory() as temp:
            world_path = Path(temp) / "world.yaml"
            world_path.write_text(yaml.safe_dump(patched_world, allow_unicode=True, sort_keys=False),
                                   encoding="utf-8")
            loaded = World.from_yaml(world_path, action_graph_path=DETECTIVE_ACTION_GRAPH_PATH)
            for seed in (0, 1, 2):
                people = load_detective_subjects()
                loaded.resolve_truth(seed)
                loaded.bind_subjects(people)
                self.assertEqual(loaded.facts["血のついた手袋"]["implies"]["value"], loaded.truth["culprit"])

    def test_patched_world_stays_byte_deterministic_for_the_same_seed(self):
        world = load_detective_world()
        subjects = read_subjects(DETECTIVE_PROJECT / "subjects")
        patch = detective_patch([detective_fact()])
        patched_world, _ = materialize(world, subjects, [patch])

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            world_path = root / "world.yaml"
            world_path.write_text(yaml.safe_dump(patched_world, allow_unicode=True, sort_keys=False),
                                   encoding="utf-8")

            def run(tag):
                loaded = World.from_yaml(world_path, action_graph_path=DETECTIVE_ACTION_GRAPH_PATH)
                people = load_detective_subjects()
                return Simulation(7, loaded, people, root / tag).run()

            first, second = run("first"), run("second")
            self.assertEqual(first.read_bytes(), second.read_bytes())


def _minimal_contract_world(temp: Path, *, subject_range_zones: list[str]) -> tuple[Path, Path]:
    """A hand-built, four-zone world for gapengine.world_patch_contract.
    contract_check's negative-check unit tests (WB-WORLDGROW-001 R6) --
    lighter than a real momotaro copy since contract_check never runs a
    simulation, just World/Subject binding and reachable_paths.

    Topology: 拠点(parent) -- 小屋(branch) -- ZZZ_valid -- 拠点 (a second,
    direct route back to parent that bypasses the parent hop entirely), and
    拠点 -- AAA_isolated -- 拠点 (a dead end, only connected to parent).
    AAA_isolated sorts before ZZZ_valid, so naively taking whichever
    zone-with-a-route-to-parent sorts first (the pre-fix behavior) picks the
    dead end; the real bypass only shows up starting from ZZZ_valid.
    """
    world = {
        "name": "test", "protagonist": "サブ", "antagonist": "サブ",
        "time": {"days": 1, "slots": ["朝"]},
        "zones": [{"name": "拠点"}, {"name": "小屋"}, {"name": "AAA_isolated"}, {"name": "ZZZ_valid"}],
        "routes": {
            "拠点": [{"to": "AAA_isolated"}, {"to": "小屋"}],
            "AAA_isolated": [{"to": "拠点"}],
            "小屋": [{"to": "拠点"}, {"to": "ZZZ_valid"}],
            "ZZZ_valid": [{"to": "小屋"}, {"to": "拠点"}],
        },
        "movement": {"action_weight": 1.0, "hop_decay": 0.6, "destination_weights": {}},
        "stamina": {"default_max": 10, "default_recover_per_slot": 1.0, "exhausted_ratio": 0.2},
        "thresholds": [], "items": [{"name": "鍵"}], "facts": [],
        "ending": [{"id": "done", "when": "False", "label": "x"}], "target_ending": ["done"],
    }
    world_path = temp / "world.yaml"
    world_path.write_text(yaml.safe_dump(world, allow_unicode=True), encoding="utf-8")

    person = {
        "id": "サブ",
        "traits": {"social": 0.5, "stubbornness": 0.5, "curiosity": 0.5, "diligence": 0.5, "temper": 0.5},
        "base": 50, "modifiers": [], "beliefs_about": {},
        "knowledge": [], "inventory": {}, "reputation": 0.0, "phase": [],
        "verbs": ["move"], "identity": {"true": "サブ", "displayed": "サブ"},
        "goal": {"target": None, "deliver_to": None, "obstacles": [], "outcome": None},
        "relations": {}, "stamina": {"max": 10, "recover_per_slot": 1.0},
        "range": {"zones": subject_range_zones, "entry": "拠点",
                  "exclude": [{"zones": ["拠点"], "until_item": "鍵"}]},
        "companions": [], "ally_value": 0, "objective_claimant": True,
    }
    subjects_dir = temp / "subjects"
    subjects_dir.mkdir()
    (subjects_dir / "sub.yaml").write_text(yaml.safe_dump(person, allow_unicode=True), encoding="utf-8")
    return world_path, subjects_dir


class ContractCheckNegativeStartTests(unittest.TestCase):
    """WB-WORLDGROW-001 R6: the negative admission check must start from a
    neighbor the excluded subject is themselves allowed into, not just
    whichever zone happens to route to `parent`."""

    def test_catches_a_bypass_only_visible_from_an_in_range_neighbor(self):
        with tempfile.TemporaryDirectory() as temp:
            world_path, subjects_dir = _minimal_contract_world(
                Path(temp), subject_range_zones=["拠点", "小屋", "ZZZ_valid"])
            patch = {"add": {"zones": [{"name": "小屋", "parent": "拠点"}]}}
            result = contract_check(world_path, subjects_dir, patch, action_graph_path=None)
        # Starting from AAA_isolated (in range only via the earlier,
        # unfiltered pick) reaches nothing and would have missed this
        # entirely -- ZZZ_valid -> 小屋 bypasses the excluded 拠点 hop. The
        # branch also never inherited the parent's exclude rule here (it
        # names only 拠点, not 小屋) -- WB-WORLDGROW-001 N3's last acceptance
        # case, a patch applied without materialize()'s inheritance step.
        self.assertEqual(result["violations"], ["入場条件を回避できます: サブ/小屋"])
        self.assertEqual(result["negative"],
                          [{"subject": "サブ", "rule_index": 0, "status": "checked", "start": "ZZZ_valid"}])

    def test_no_in_range_neighbor_is_skipped_not_a_violation_or_crash(self):
        with tempfile.TemporaryDirectory() as temp:
            world_path, subjects_dir = _minimal_contract_world(
                Path(temp), subject_range_zones=["拠点", "小屋"])
            patch = {"add": {"zones": [{"name": "小屋", "parent": "拠点"}]}}
            result = contract_check(world_path, subjects_dir, patch, action_graph_path=None)
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["negative"],
                          [{"subject": "サブ", "rule_index": 0, "status": "skipped", "start": None,
                            "reason": "合法な開始場所を構成できません"}])

    def test_not_applicable_for_a_subject_with_no_exclude_rule_on_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            world_path, subjects_dir = _minimal_contract_world(
                Path(temp), subject_range_zones=["拠点", "小屋", "ZZZ_valid"])
            # A second subject with no range.exclude at all -- must show up
            # as not_applicable, not silently vanish from the output.
            other = yaml.safe_load((subjects_dir / "sub.yaml").read_text(encoding="utf-8"))
            other = dict(other, id="他", range=dict(other["range"], exclude=[]))
            (subjects_dir / "other.yaml").write_text(yaml.safe_dump(other, allow_unicode=True), encoding="utf-8")
            patch = {"add": {"zones": [{"name": "小屋", "parent": "拠点"}]}}
            result = contract_check(world_path, subjects_dir, patch, action_graph_path=None)
        entries = {n["subject"]: n for n in result["negative"]}
        self.assertEqual(entries["他"], {"subject": "他", "rule_index": None, "status": "not_applicable"})
        self.assertEqual(entries["サブ"]["status"], "checked")

    def test_permanent_exclude_rule_without_until_item_is_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            world_path, subjects_dir = _minimal_contract_world(
                Path(temp), subject_range_zones=["拠点", "小屋", "ZZZ_valid"])
            person = yaml.safe_load((subjects_dir / "sub.yaml").read_text(encoding="utf-8"))
            person["range"]["exclude"] = [{"zones": ["拠点"]}]  # no until_item: permanent
            (subjects_dir / "sub.yaml").write_text(yaml.safe_dump(person, allow_unicode=True), encoding="utf-8")
            patch = {"add": {"zones": [{"name": "小屋", "parent": "拠点"}]}}
            result = contract_check(world_path, subjects_dir, patch, action_graph_path=None)
        self.assertEqual(result["violations"], ["入場条件を回避できます: サブ/小屋"])
        self.assertEqual(result["negative"],
                          [{"subject": "サブ", "rule_index": 0, "status": "checked", "start": "ZZZ_valid"}])

    def test_an_already_satisfied_different_exclude_rule_does_not_poison_the_start_search(self):
        # N3 (WB-WORLDGROW-001, Astra review): サブ has two exclude rules
        # targeting different zones -- one under test (拠点, still active:
        # no 鍵) and one already satisfied (ZZZ_valid, has the item). The
        # pre-fix code unioned every rule's zones regardless of whether that
        # rule's own condition currently held, so the already-satisfied
        # rule's zone (which happens to be the *only* real neighbor of 拠点)
        # got excluded from the start search too, and the check was silently
        # skipped instead of run.
        with tempfile.TemporaryDirectory() as temp:
            world_path, subjects_dir = _minimal_contract_world(
                Path(temp), subject_range_zones=["拠点", "小屋", "ZZZ_valid"])
            world = yaml.safe_load(world_path.read_text(encoding="utf-8"))
            world["items"].append({"name": "通行証"})
            world_path.write_text(yaml.safe_dump(world, allow_unicode=True), encoding="utf-8")
            person = yaml.safe_load((subjects_dir / "sub.yaml").read_text(encoding="utf-8"))
            person["inventory"] = {"通行証": 1}
            person["range"]["exclude"].append({"zones": ["ZZZ_valid"], "until_item": "通行証"})
            (subjects_dir / "sub.yaml").write_text(yaml.safe_dump(person, allow_unicode=True), encoding="utf-8")
            patch = {"add": {"zones": [{"name": "小屋", "parent": "拠点"}]}}
            result = contract_check(world_path, subjects_dir, patch, action_graph_path=None)
        self.assertEqual(result["violations"], ["入場条件を回避できます: サブ/小屋"])
        rule0 = next(n for n in result["negative"] if n["rule_index"] == 0)
        self.assertEqual(rule0, {"subject": "サブ", "rule_index": 0, "status": "checked", "start": "ZZZ_valid"})


if __name__ == "__main__":
    unittest.main()
