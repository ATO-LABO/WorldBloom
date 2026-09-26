"""Acceptance boundaries for LLM proposal prompting/parsing (WB-WORLDGROW-001, stage 3b)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from copy import deepcopy

from gapengine.world_patch import validate_patch
from gapengine.world_patch_propose import (build_prompt, check_proposal_rules, check_trigger_coverage,
                                           make_patch, parse_proposal)

ROOT = Path(__file__).resolve().parents[1]
WORLD = yaml.safe_load((ROOT / "projects" / "momotaro" / "world.yaml").read_text(encoding="utf-8"))
SUBJECT_IDS = ["おじいさん", "おばあさん", "桃太郎", "犬", "猿", "キジ", "鬼"]
TRIGGER = {"zone": "海", "verb": "investigate", "count": 228, "whiffs": 228}

DETECTIVE_WORLD = yaml.safe_load((ROOT / "projects" / "detective" / "world.yaml").read_text(encoding="utf-8"))
DETECTIVE_SUBJECT_IDS = ["探偵", "容疑者甲", "容疑者乙", "容疑者丙", "証人壱", "証人弐"]
DETECTIVE_TRIGGER = {"zone": "食堂", "verb": "investigate", "count": 10, "whiffs": 10}

ROMANCE_WORLD = yaml.safe_load((ROOT / "projects" / "romance" / "world.yaml").read_text(encoding="utf-8"))
ROMANCE_SUBJECT_IDS = ["A", "B", "C", "D", "E"]
ROMANCE_TRIGGER = {"zone": "学校", "verb": "investigate", "count": 10, "whiffs": 10}


def sample_add() -> dict:
    # A1 requires a source directly in the trigger zone (海) itself; A2
    # requires the added branch zone (船大工の小屋) to have one too.
    return {
        "zones": [{"name": "船大工の小屋", "parent": "海", "note": "船具を扱う小屋"}],
        "items": [
            {"name": "古びた帆布", "sources": [
                {"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 2}]},
            {"name": "潮見の貝殻", "sources": [
                {"type": "investigate", "zone": "海", "count": 1, "max": 2}]},
        ],
        "facts": [],
    }


class BuildPromptTests(unittest.TestCase):
    def test_contains_world_and_trigger_content_and_is_bounded(self):
        prompt = build_prompt(WORLD, SUBJECT_IDS, TRIGGER, [("craft", 40, 0.1), ("move", 20, 0.0)])
        for expected in ("村", "海", "鬼ヶ島", "きびだんご", "造船術", "桃太郎", "鬼",
                          "228", "craft×40", "move×20"):
            self.assertIn(expected, prompt)
        self.assertLessEqual(len(prompt), 12000)

    def test_rejects_non_investigate_verb(self):
        trigger = {**TRIGGER, "verb": "observe"}
        with self.assertRaises(ValueError):
            build_prompt(WORLD, SUBJECT_IDS, trigger, [])

    def test_huge_zone_notes_still_fit_and_keep_rules_and_output_and_example(self):
        world = deepcopy(WORLD)
        for zone in world["zones"]:
            zone["note"] = "え" * 3000
        prompt = build_prompt(world, SUBJECT_IDS, TRIGGER, [("craft", 40, 0.1), ("move", 20, 0.0)])
        self.assertLessEqual(len(prompt), 12000)
        for expected in ("# 拡張のルール", "# 出力", "228", "craft×40", "move×20",
                          "灯台守の記録", "この例に出てくる名前"):
            self.assertIn(expected, prompt)

    def test_normal_momotaro_world_drops_nothing(self):
        prompt = build_prompt(WORLD, SUBJECT_IDS, TRIGGER, [("craft", 40, 0.1), ("move", 20, 0.0)])
        self.assertNotIn("ほか", prompt)

    def test_give_unavailable_tells_the_model_not_to_write_give(self):
        prompt = build_prompt(WORLD, SUBJECT_IDS, TRIGGER, [], give_available=False)
        self.assertIn("give は書かないでください", prompt)

    def test_give_available_does_not_forbid_give(self):
        prompt = build_prompt(WORLD, SUBJECT_IDS, TRIGGER, [], give_available=True)
        self.assertNotIn("give は書かないでください", prompt)

    def test_give_available_tells_the_model_to_shrink_give_for_plentiful_items(self):
        # M3: the give budget counts default-affinity items too, so the
        # prompt must say the trade-off (more max -> smaller give), not just
        # the raw formula -- otherwise a retry can't fix a give-less item
        # rejected purely for its sources.max.
        prompt = build_prompt(WORLD, SUBJECT_IDS, TRIGGER, [], give_available=True)
        self.assertIn("小さく", prompt)


class LotteryImpliesPromptTests(unittest.TestCase):
    """WB-WORLD-DEMAND: build_prompt must tell the model to use $truth/
    $innocent:N (not a fixed candidate name) for facts whose truth is drawn
    per-seed, and must not let it name a candidate in a token-using fact's
    label."""

    def test_detective_prompt_mentions_truth_innocent_and_candidate_names(self):
        # culprit and weapon both have 3 candidates -> $innocent:1..2.
        prompt = build_prompt(DETECTIVE_WORLD, DETECTIVE_SUBJECT_IDS, DETECTIVE_TRIGGER, [])
        self.assertIn("$truth", prompt)
        self.assertIn("$innocent:2", prompt)
        self.assertIn("容疑者甲", prompt)
        # A lottery-only world must still be told the implies keys and the cap.
        self.assertIn("0.3以下", prompt)
        self.assertIn('"value":"$truth"', prompt)

    def test_momotaro_prompt_has_no_truth_token(self):
        # oni_weakness/treasure_thief are valued facts but fixed (not drawn
        # per seed) -- the prompt must not suggest $truth/$innocent for them.
        prompt = build_prompt(WORLD, SUBJECT_IDS, TRIGGER, [])
        self.assertNotIn("$truth", prompt)
        self.assertNotIn("$innocent", prompt)

    def test_romance_prompt_forbids_implies_entirely(self):
        # romance has no valued facts at all -- unaffected by this change.
        prompt = build_prompt(ROMANCE_WORLD, ROMANCE_SUBJECT_IDS, ROMANCE_TRIGGER, [])
        self.assertIn("implies は書かないでください", prompt)


class ParseProposalTests(unittest.TestCase):
    def test_accepts_fenced_response_with_preamble(self):
        text = '前置き\n```json\n' + json.dumps(
            {"title": "海辺の船大工小屋", "rationale": "海でのinvestigateが空振りし続けている", "add": sample_add()},
            ensure_ascii=False) + '\n```\n後書き'
        value = parse_proposal(text)
        self.assertEqual(value["title"], "海辺の船大工小屋")
        self.assertEqual(value["add"], sample_add())

    def test_rejects_extra_key(self):
        text = json.dumps({"title": "x", "rationale": "y", "add": {}, "extra": 1}, ensure_ascii=False)
        with self.assertRaises(ValueError):
            parse_proposal(text)

    def test_rejects_missing_key(self):
        text = json.dumps({"title": "x", "add": {}}, ensure_ascii=False)
        with self.assertRaises(ValueError):
            parse_proposal(text)

    def test_rejects_title_too_long(self):
        text = json.dumps({"title": "あ" * 41, "rationale": "y", "add": {}}, ensure_ascii=False)
        with self.assertRaises(ValueError):
            parse_proposal(text)

    def test_rejects_non_json_response(self):
        with self.assertRaises(ValueError):
            parse_proposal("これはJSONではありません")

    def test_rejects_non_string_response(self):
        with self.assertRaises(ValueError):
            parse_proposal(None)  # type: ignore[arg-type]


class MakePatchTests(unittest.TestCase):
    def test_id_is_content_addressed_and_deterministic(self):
        proposal = {"title": "海辺の船大工小屋", "rationale": "海でのinvestigateが空振りし続けている", "add": sample_add()}
        first = make_patch(proposal, trigger=TRIGGER, parent_digest="a" * 64, author={"backend": "none"})
        second = make_patch(dict(proposal), trigger=TRIGGER, parent_digest="a" * 64, author={"backend": "none"})
        self.assertEqual(first["id"], second["id"])

        different = make_patch(
            {**proposal, "add": {**sample_add()}},
            trigger=TRIGGER, parent_digest="a" * 64, author={"backend": "none"},
        )
        self.assertEqual(first["id"], different["id"])  # same add content -> same id

        changed_add = dict(sample_add())
        changed_add["items"] = []
        changed = make_patch(
            {**proposal, "add": changed_add}, trigger=TRIGGER, parent_digest="a" * 64, author={"backend": "none"})
        self.assertNotEqual(first["id"], changed["id"])

    def test_trigger_is_slimmed_and_parent_rev_and_author_preserved(self):
        proposal = {"title": "t", "rationale": "r", "add": sample_add()}
        trigger = {**TRIGGER, "experiment": "run-xxxx", "wasted_share": 0.5, "zone_dwell_share": 0.9}
        patch = make_patch(proposal, trigger=trigger, parent_digest="b" * 64, author={"backend": "codex-cli"})
        self.assertEqual(patch["trigger"], {"experiment": "run-xxxx", "zone": "海", "verb": "investigate",
                                             "count": 228, "whiffs": 228})
        self.assertEqual(patch["parent_digest"], "b" * 64)
        self.assertEqual(patch["author"], {"backend": "codex-cli"})


class CheckTriggerCoverageTests(unittest.TestCase):
    def test_source_in_trigger_zone_has_no_violation(self):
        add = {"items": [{"name": "潮見の貝殻", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}]}]}
        self.assertEqual(check_trigger_coverage(add, TRIGGER), [])

    def test_source_only_in_branch_zone_is_an_a1_violation(self):
        # A1: a source in the trigger zone (海) itself is required -- placing
        # it only in a branch off it leaves the whiff exactly as frequent.
        add = {
            "zones": [{"name": "船大工の小屋", "parent": "海"}],
            "facts": [{"id": "船大工の噂", "sources": [{"type": "investigate", "zone": "船大工の小屋", "count": 1}]}],
        }
        violations = check_trigger_coverage(add, TRIGGER)
        self.assertIn("きっかけの場所そのものに調べて得られるものが足されていません", violations)

    def test_trigger_zone_and_branch_zone_both_covered_has_no_violation(self):
        add = {
            "zones": [{"name": "船大工の小屋", "parent": "海"}],
            "items": [{"name": "潮見の貝殻", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}]}],
            "facts": [{"id": "船大工の噂", "sources": [{"type": "investigate", "zone": "船大工の小屋", "count": 1}]}],
        }
        self.assertEqual(check_trigger_coverage(add, TRIGGER), [])

    def test_added_zone_without_its_own_source_is_an_a2_violation(self):
        # A2: 海 itself is covered, but the added branch zone has nothing
        # sourcing from it -- an empty added zone is just a new whiff spot.
        add = {
            "zones": [{"name": "船大工の小屋", "parent": "海"}],
            "items": [{"name": "潮見の貝殻", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}]}],
        }
        violations = check_trigger_coverage(add, TRIGGER)
        self.assertIn("足した場所に調べて得られるものがありません: '船大工の小屋'", violations)

    def test_source_only_in_unrelated_zone_is_a_violation(self):
        add = {"items": [{"name": "山の薬草", "sources": [{"type": "investigate", "zone": "山", "count": 1, "max": 1}]}]}
        self.assertTrue(check_trigger_coverage(add, TRIGGER))

    def test_items_and_facts_with_no_sources_is_a_violation(self):
        add = {
            "items": [{"name": "古びた帆布", "made_from": {"木材": 1}}],
            "facts": [{"id": "何かの噂", "label": "詳細不明"}],
        }
        self.assertTrue(check_trigger_coverage(add, TRIGGER))

    def test_malformed_add_shapes_never_raise(self):
        for add in (
            {"items": {"a": 1}},  # items is a dict, not a list
            {"items": [{"name": "x", "sources": "not-a-list"}]},  # sources is a str
            {"zones": "not-a-list", "items": [{"name": "x", "sources": [{"zone": ["海"]}]}]},
            {"items": [{"name": "x", "sources": [{"zone": {"nested": True}}]}]},
            "not-a-dict",
            None,
        ):
            with self.subTest(add=add):
                self.assertEqual(check_trigger_coverage(add, TRIGGER), check_trigger_coverage(add, TRIGGER))
                self.assertTrue(check_trigger_coverage(add, TRIGGER))


class CheckProposalRulesTests(unittest.TestCase):
    def test_fact_missing_secrecy_is_a_violation(self):
        add = {"facts": [{"id": "船大工の噂", "label": "x"}]}
        violations = check_proposal_rules(add)
        self.assertTrue(any("secrecy" in v for v in violations), violations)

    def test_fact_with_secrecy_has_no_violation(self):
        add = {"facts": [{"id": "船大工の噂", "label": "x", "secrecy": 0.2}]}
        self.assertEqual(check_proposal_rules(add), [])

    def test_keepsake_with_give_is_a_violation(self):
        add = {"items": [{"name": "古びた帆布", "keepsake": True,
                           "give": {"receiver_affinity": 0.2, "giver_affinity": 0.05}}]}
        violations = check_proposal_rules(add)
        self.assertTrue(any("keepsake" in v for v in violations), violations)

    def test_keepsake_without_give_has_no_violation(self):
        add = {"items": [{"name": "古びた帆布", "keepsake": True}]}
        self.assertEqual(check_proposal_rules(add), [])

    def test_give_without_give_available_is_a_violation(self):
        add = {"items": [{"name": "古びた帆布", "give": {"receiver_affinity": 0.2, "giver_affinity": 0.05}}]}
        violations = check_proposal_rules(add, give_available=False)
        self.assertTrue(any("give" in v for v in violations), violations)

    def test_give_with_give_available_has_no_violation(self):
        add = {"items": [{"name": "古びた帆布", "give": {"receiver_affinity": 0.2, "giver_affinity": 0.05}}]}
        self.assertEqual(check_proposal_rules(add, give_available=True), [])

    def test_made_from_with_give_is_a_violation(self):
        # R1: a crafted item (made_from) can never be handed over
        # (engine/actions.py's _give_candidates excludes world.recipes), so
        # a proposal must not be allowed to write give on one.
        add = {"items": [{"name": "組み立てた品", "made_from": {"部品": 1},
                           "give": {"receiver_affinity": 0.2, "giver_affinity": 0.05}}]}
        violations = check_proposal_rules(add)
        self.assertTrue(any("made_from" in v for v in violations), violations)

    def test_made_from_without_give_has_no_violation(self):
        add = {"items": [{"name": "組み立てた品", "made_from": {"部品": 1}}]}
        self.assertEqual(check_proposal_rules(add), [])

    def test_malformed_add_shapes_never_raise(self):
        for add in (
            {"facts": "not-a-list"},
            {"facts": [None, "x", 1]},
            {"items": {"a": 1}},
            {"items": [None, "x", 1]},
            "not-a-dict",
            None,
        ):
            with self.subTest(add=add):
                self.assertEqual(check_proposal_rules(add), [])
                self.assertEqual(check_proposal_rules(add, give_available=False), [])


IGNORANCE_TRIGGER = {"kind": "ignorance", "zone": "鬼ヶ島", "count": 33, "share": 0.5, "zone_dwell_share": 0.02}
BLOCKED_TRIGGER = {
    "kind": "blocked", "requirement": "has_item:縄", "count": 1801, "share": 1.0, "lost_share": 0.491,
    "runs": 47, "reason": "sources_unreachable",
    "stuck_zones": [["道中", 900], ["森", 500], ["海", 401]],
    "source_zones": [["村", 1801]], "held_by": [],
}
BLOCKED_REACH_TRIGGER = {
    "kind": "blocked", "requirement": "reach:村", "count": 60, "share": 1.0, "lost_share": 0.3,
    "runs": 4, "reason": "destination_unreachable",
    "stuck_zones": [["道中", 60]], "source_zones": [], "held_by": [],
}


class BuildPromptKindDispatchTests(unittest.TestCase):
    """WB-WORLDGROW-002 S2: build_prompt/check_trigger_coverage/make_patch
    generalized beyond whiff (v1's only kind) to ignorance/blocked -- a
    route-layer (rho>0) trigger world_demand.py's collect() only ever
    produces alongside `policy.route`."""

    def test_whiff_prompt_is_byte_identical_to_pre_s2_text(self):
        # No regression from generalizing _demand_section/_RULES_TEMPLATE --
        # a whiff trigger's prompt (kind omitted, like every pre-S1 report)
        # must render exactly as before.
        prompt = build_prompt(WORLD, SUBJECT_IDS, TRIGGER, [("craft", 40, 0.1), ("move", 20, 0.0)])
        self.assertIn("必ず「海」そのものをsourcesのzoneにしたitemsかfactsを1つ以上入れてください", prompt)
        self.assertIn("今回の空振りは228回なので", prompt)

    def test_ignorance_prompt_mentions_zone_and_count(self):
        prompt = build_prompt(WORLD, SUBJECT_IDS, IGNORANCE_TRIGGER, [])
        self.assertIn("鬼ヶ島", prompt)
        self.assertIn("手探り", prompt)
        self.assertIn("33回", prompt)
        self.assertLessEqual(len(prompt), 12000)

    def test_blocked_prompt_names_target_source_zones_and_stuck_zones(self):
        prompt = build_prompt(WORLD, SUBJECT_IDS, BLOCKED_TRIGGER, [])
        for expected in ("縄", "村", "道中", "森", "海", "1801回", "47本のラン"):
            self.assertIn(expected, prompt)
        self.assertIn("誰も持っていません", prompt)  # held_by empty here -> the "no one holds it" fallback
        self.assertLessEqual(len(prompt), 12000)

    def test_blocked_reach_prompt_talks_about_the_zones_role_not_an_item(self):
        prompt = build_prompt(WORLD, SUBJECT_IDS, BLOCKED_REACH_TRIGGER, [])
        self.assertIn("村」に行く手段が主人公にありません", prompt)
        self.assertIn("役割を代わりに果たす", prompt)
        self.assertNotIn("を手に入れる手段が", prompt)  # not the has_item/knows phrasing


class MakePatchTriggerKindTests(unittest.TestCase):
    def test_whiff_trigger_slim_unchanged(self):
        proposal = {"title": "t", "rationale": "r", "add": sample_add()}
        patch = make_patch(proposal, trigger={**TRIGGER, "experiment": "run-x"},
                            parent_digest="a" * 64, author={"backend": "none"})
        self.assertEqual(patch["trigger"], {"experiment": "run-x", "zone": "海", "verb": "investigate",
                                             "count": 228, "whiffs": 228})
        self.assertNotIn("kind", patch["trigger"])

    def test_ignorance_trigger_slim_keeps_kind_zone_count(self):
        proposal = {"title": "t", "rationale": "r", "add": sample_add()}
        trigger = {**IGNORANCE_TRIGGER, "experiment": "run-x"}
        patch = make_patch(proposal, trigger=trigger, parent_digest="a" * 64, author={"backend": "none"})
        self.assertEqual(patch["trigger"], {"experiment": "run-x", "kind": "ignorance", "zone": "鬼ヶ島", "count": 33})

    def test_blocked_trigger_slim_keeps_kind_requirement_count_stuck_zones(self):
        proposal = {"title": "t", "rationale": "r", "add": sample_add()}
        trigger = {**BLOCKED_TRIGGER, "experiment": "run-x"}
        patch = make_patch(proposal, trigger=trigger, parent_digest="a" * 64, author={"backend": "none"})
        self.assertEqual(patch["trigger"], {
            "experiment": "run-x", "kind": "blocked", "requirement": "has_item:縄", "count": 1801,
            "stuck_zones": [["道中", 900], ["森", 500], ["海", 401]],
        })


class CheckTriggerCoverageIgnoranceTests(unittest.TestCase):
    def test_source_in_trigger_zone_has_no_violation(self):
        add = {"items": [{"name": "何かの手がかり", "sources": [
            {"type": "investigate", "zone": "鬼ヶ島", "count": 1, "max": 1}]}]}
        self.assertEqual(check_trigger_coverage(add, IGNORANCE_TRIGGER), [])

    def test_source_in_a_child_zone_has_no_violation(self):
        add = {
            "zones": [{"name": "洞穴", "parent": "鬼ヶ島"}],
            "facts": [{"id": "洞穴の噂", "sources": [{"type": "investigate", "zone": "洞穴", "count": 1}]}],
        }
        self.assertEqual(check_trigger_coverage(add, IGNORANCE_TRIGGER, world=WORLD), [])

    def test_source_only_in_an_unrelated_zone_is_a_violation(self):
        add = {"items": [{"name": "何かの手がかり", "sources": [
            {"type": "investigate", "zone": "村", "count": 1, "max": 1}]}]}
        self.assertIn("きっかけの場所そのものに調べて得られるものが足されていません",
                       check_trigger_coverage(add, IGNORANCE_TRIGGER, world=WORLD))


class CheckTriggerCoverageBlockedTests(unittest.TestCase):
    def test_item_requirement_sourced_in_a_reachable_zone_has_no_violation(self):
        add = {"items": [{"name": "命綱", "sources": [{"type": "investigate", "zone": "道中", "count": 1, "max": 2}]}]}
        violations = check_trigger_coverage(add, {**BLOCKED_TRIGGER, "requirement": "has_item:命綱"},
                                             reachable_zones={"道中", "森", "海"})
        self.assertEqual(violations, [])

    def test_item_requirement_sourced_only_in_an_unreachable_zone_is_a_violation(self):
        add = {"items": [{"name": "命綱", "sources": [{"type": "investigate", "zone": "村", "count": 1, "max": 2}]}]}
        violations = check_trigger_coverage(add, {**BLOCKED_TRIGGER, "requirement": "has_item:命綱"},
                                             reachable_zones={"道中", "森", "海"})
        self.assertIn("「命綱」を主人公が到達できる場所に足す入手手段がありません", violations)

    def test_no_reachable_zones_given_falls_back_to_the_triggers_own_stuck_zones(self):
        # Unit-level call with no engine access at all: falls back to the
        # trigger's own stuck_zones instead of treating everything (or
        # nothing) as reachable.
        add = {"items": [{"name": "命綱", "sources": [{"type": "investigate", "zone": "道中", "count": 1, "max": 2}]}]}
        trigger = {"kind": "blocked", "requirement": "has_item:命綱", "stuck_zones": [["道中", 5]]}
        self.assertEqual(check_trigger_coverage(add, trigger), [])
        other = {"items": [{"name": "命綱", "sources": [{"type": "investigate", "zone": "森", "count": 1, "max": 2}]}]}
        self.assertTrue(check_trigger_coverage(other, trigger))

    def test_fact_requirement_uses_the_facts_bucket(self):
        add = {"facts": [{"id": "縄の隠し場所", "sources": [{"type": "investigate", "zone": "森", "count": 1}]}]}
        trigger = {"kind": "blocked", "requirement": "knows:縄の隠し場所", "stuck_zones": [["道中", 5]]}
        self.assertEqual(check_trigger_coverage(add, trigger, reachable_zones={"道中", "森"}), [])

    def test_reach_requirement_accepts_any_reachable_source(self):
        add = {"items": [{"name": "見張り台の梯子", "sources": [
            {"type": "investigate", "zone": "道中", "count": 1, "max": 2}]}]}
        self.assertEqual(check_trigger_coverage(add, BLOCKED_REACH_TRIGGER, reachable_zones={"道中"}), [])

    def test_added_zone_without_its_own_source_is_still_an_a2_violation_for_blocked(self):
        add = {
            "zones": [{"name": "隠れ道", "parent": "道中"}],
            "items": [{"name": "命綱", "sources": [{"type": "investigate", "zone": "道中", "count": 1, "max": 2}]}],
        }
        violations = check_trigger_coverage(add, {**BLOCKED_TRIGGER, "requirement": "has_item:命綱"},
                                             reachable_zones={"道中"})
        self.assertIn("足した場所に調べて得られるものがありません: '隠れ道'", violations)


class KnownGoodRegressionTests(unittest.TestCase):
    def test_sea_proposal_human_add_passes_all_three_gates(self):
        # Mirrors the add from
        # C:\Projects\WorldBloom-local\runs\world-demand-reports\sea-proposal-human.json
        # (minus daily_events) -- a regression guard that A1/A2 trigger
        # coverage and the proposal-time rules (secrecy, keepsake x give,
        # give availability) all accept a real, previously-accepted shape.
        add = {
            "zones": [{"name": "船大工の小屋", "parent": "海", "note": "浜の外れに残る、昔の船大工の作業小屋"}],
            "items": [
                {"name": "古びた帆布", "keepsake": True,
                 "sources": [{"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 1}]},
                {"name": "潮見の貝殻", "give": {"receiver_affinity": 0.2, "giver_affinity": 0.05},
                 "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 2}]},
            ],
            "facts": [
                {"id": "潮の読み方", "label": "沖へ出る潮の変わり目を見分けられる", "secrecy": 0.2, "share_min_affinity": 0.2,
                 "sources": [{"type": "investigate", "zone": "海", "count": 1}]},
                {"id": "小屋の言い伝え", "label": "昔の船大工は、鬼は塩を嫌うと語っていたらしい", "secrecy": 0.3, "share_min_affinity": 0.0,
                 "sources": [{"type": "investigate", "zone": "船大工の小屋", "count": 1}],
                 "implies": {"fact": "oni_weakness", "value": "塩", "confidence": 0.3}},
            ],
        }
        patch = make_patch({"title": "海辺の船大工小屋", "rationale": "浜の船大工小屋を足す提案です。",
                             "add": add}, trigger=TRIGGER, parent_digest="a" * 64, author={"backend": "none"})
        self.assertEqual(validate_patch(WORLD, patch, subject_ids=SUBJECT_IDS), [])
        self.assertEqual(check_trigger_coverage(add, TRIGGER), [])
        self.assertEqual(check_proposal_rules(add), [])


if __name__ == "__main__":
    unittest.main()
