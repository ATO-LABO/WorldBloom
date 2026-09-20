"""Acceptance boundaries for LLM proposal prompting/parsing (WB-WORLDGROW-001, stage 3b)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from gapengine.world_patch_propose import build_prompt, check_trigger_coverage, make_patch, parse_proposal

ROOT = Path(__file__).resolve().parents[1]
WORLD = yaml.safe_load((ROOT / "projects" / "momotaro" / "world.yaml").read_text(encoding="utf-8"))
SUBJECT_IDS = ["おじいさん", "おばあさん", "桃太郎", "犬", "猿", "キジ", "鬼"]
TRIGGER = {"zone": "海", "verb": "investigate", "count": 228, "whiffs": 228}


def sample_add() -> dict:
    return {
        "zones": [{"name": "船大工の小屋", "parent": "海", "note": "船具を扱う小屋"}],
        "items": [{"name": "古びた帆布", "sources": [
            {"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 2}]}],
        "facts": [], "daily_events": [],
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
        first = make_patch(proposal, trigger=TRIGGER, parent_rev=[], author={"backend": "none"})
        second = make_patch(dict(proposal), trigger=TRIGGER, parent_rev=[], author={"backend": "none"})
        self.assertEqual(first["id"], second["id"])

        different = make_patch(
            {**proposal, "add": {**sample_add(), "daily_events": []}},
            trigger=TRIGGER, parent_rev=[], author={"backend": "none"},
        )
        self.assertEqual(first["id"], different["id"])  # same add content -> same id

        changed_add = dict(sample_add())
        changed_add["items"] = []
        changed = make_patch(
            {**proposal, "add": changed_add}, trigger=TRIGGER, parent_rev=[], author={"backend": "none"})
        self.assertNotEqual(first["id"], changed["id"])

    def test_trigger_is_slimmed_and_parent_rev_and_author_preserved(self):
        proposal = {"title": "t", "rationale": "r", "add": sample_add()}
        trigger = {**TRIGGER, "experiment": "run-xxxx", "wasted_share": 0.5, "zone_dwell_share": 0.9}
        patch = make_patch(proposal, trigger=trigger, parent_rev=["p-aaaaaaaa"], author={"backend": "codex-cli"})
        self.assertEqual(patch["trigger"], {"experiment": "run-xxxx", "zone": "海", "verb": "investigate",
                                             "count": 228, "whiffs": 228})
        self.assertEqual(patch["parent_rev"], ["p-aaaaaaaa"])
        self.assertEqual(patch["author"], {"backend": "codex-cli"})


class CheckTriggerCoverageTests(unittest.TestCase):
    def test_source_in_trigger_zone_has_no_violation(self):
        add = {"items": [{"name": "潮見の貝殻", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}]}]}
        self.assertEqual(check_trigger_coverage(add, TRIGGER), [])

    def test_source_in_branch_zone_off_trigger_has_no_violation(self):
        add = {
            "zones": [{"name": "船大工の小屋", "parent": "海"}],
            "facts": [{"id": "船大工の噂", "sources": [{"type": "investigate", "zone": "船大工の小屋", "count": 1}]}],
        }
        self.assertEqual(check_trigger_coverage(add, TRIGGER), [])

    def test_source_only_in_unrelated_zone_is_a_violation(self):
        add = {"items": [{"name": "山の薬草", "sources": [{"type": "investigate", "zone": "山", "count": 1, "max": 1}]}]}
        self.assertTrue(check_trigger_coverage(add, TRIGGER))

    def test_items_and_facts_with_no_sources_is_a_violation(self):
        add = {
            "items": [{"name": "古びた帆布", "made_from": {"木材": 1}}],
            "facts": [{"id": "何かの噂", "label": "詳細不明"}],
        }
        self.assertTrue(check_trigger_coverage(add, TRIGGER))


if __name__ == "__main__":
    unittest.main()
