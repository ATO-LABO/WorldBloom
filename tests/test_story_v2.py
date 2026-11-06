import json
import unittest

from gapengine.story_plan import create_plan
from gapengine.story_v2 import build_story_v2_prompt, inspect_story_v2
from test_story_pipeline import fixture


class StoryV2Tests(unittest.TestCase):
    def setUp(self):
        self.material, _, _ = fixture("06")
        self.plan = create_plan(self.material)

    def test_kinds_share_packet_but_have_different_instruction(self):
        synopsis = build_story_v2_prompt(self.material, self.plan, "synopsize")
        story = build_story_v2_prompt(self.material, self.plan, "narrate")
        self.assertIn("あらすじ", synopsis)
        self.assertIn("本文", story)
        self.assertEqual(synopsis.split("固定場面パケット：\n", 1)[1], story.split("固定場面パケット：\n", 1)[1])

    def test_malformed_reply_keeps_ordered_safe_facts(self):
        result = inspect_story_v2("not json", self.material, self.plan, "story")
        self.assertEqual(result["validation"]["structural_status"], "failed")
        prompt = build_story_v2_prompt(self.material, self.plan, "story")
        packet = json.loads(prompt.split("固定場面パケット：\n", 1)[1])
        self.assertEqual([source["event_ids"][0] for source in result["paragraph_sources"]],
                         [slot["event_id"] for slot in packet["slots"] if slot["render"] and slot["anchor"]])
        self.assertTrue(result["text"])

    def test_reply_order_does_not_change_anchor_order(self):
        prompt = build_story_v2_prompt(self.material, self.plan, "story")
        packet = json.loads(prompt.split("固定場面パケット：\n", 1)[1])
        rendered = [slot for slot in packet["slots"] if slot["render"] and slot["anchor"]]
        first, last = rendered[0], rendered[-1]
        raw = json.dumps({"slots": [{"slot_id": last["slot_id"], "text": "最後の補足。"},
                                    {"slot_id": first["slot_id"], "text": "最初の補足。"}]}, ensure_ascii=False)
        result = inspect_story_v2(raw, self.material, self.plan, "story")
        self.assertIn("最初の補足。", result["text"].split("\n\n")[0])
        self.assertIn("最後の補足。", result["text"].split("\n\n")[-1])

    def test_model_echo_does_not_repeat_fixed_sentence(self):
        prompt = build_story_v2_prompt(self.material, self.plan, "synopsize")
        packet = json.loads(prompt.split("固定場面パケット：\n", 1)[1])
        first = next(slot for slot in packet["slots"] if slot["anchor"])
        raw = json.dumps({"slots": [{"slot_id": first["slot_id"],
                                   "text": first["anchor"] + "その後、" + first["anchor"] + "道に影が落ちた。"}]})
        result = inspect_story_v2(raw, self.material, self.plan, "synopsize")
        paragraph = result["text"].split("\n\n")[0]
        self.assertEqual(paragraph.count(first["anchor"]), 1)
        self.assertIn("道に影が落ちた。", paragraph)


if __name__ == "__main__":
    unittest.main()
