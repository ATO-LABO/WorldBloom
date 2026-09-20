"""Acceptance boundaries for expansion patches (WB-WORLDGROW-001, stage 3a)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml
from world_patch_fixtures import write_approved
from gapengine.world_patch import EMPTY_STACK_DIGEST

from engine.world import World
from gapengine.world_patch import (
    PatchError,
    absolutize_references,
    apply_patch,
    apply_patches,
    approved_patches,
    patch_id_for,
    validate_patch,
)
from gapengine.world_patch_contract import contract_check

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
            ["きっかけの場所に調べて得られるものが足されていません"])

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
        # entirely -- ZZZ_valid -> 小屋 bypasses the excluded 拠点 hop.
        self.assertEqual(result["violations"], ["入場条件を回避できます: サブ/小屋"])
        self.assertNotIn("notes", result)

    def test_no_in_range_neighbor_leaves_a_note_not_a_violation_or_crash(self):
        with tempfile.TemporaryDirectory() as temp:
            world_path, subjects_dir = _minimal_contract_world(
                Path(temp), subject_range_zones=["拠点", "小屋"])
            patch = {"add": {"zones": [{"name": "小屋", "parent": "拠点"}]}}
            result = contract_check(world_path, subjects_dir, patch, action_graph_path=None)
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["notes"], ["陰性検査の開始場所がありません: サブ"])


if __name__ == "__main__":
    unittest.main()
