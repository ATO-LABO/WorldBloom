import json
from copy import deepcopy
import unittest

from gapengine.story_plan import create_plan, revise_identity
from gapengine.story_v2 import _fact_sentence, build_story_v2_prompt, inspect_story_v2
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

    def test_real_03_sabotage_and_required_chain_are_rendered_in_both_modes(self):
        material, _, _ = fixture("03")
        plan = create_plan(material)
        for kind in ("synopsize", "narrate"):
            result = inspect_story_v2('{"slots":[]}', material, plan, kind)
            self.assertEqual(result["validation"]["structural_status"], "passed")
            self.assertEqual(set(result["validation"]["required_event_coverage"]), set(plan["required_event_ids"]))
            self.assertIn("桃太郎は鬼を妨害した。", result["text"])
            self.assertEqual(result["validation"]["semantic_review_status"], "not_reviewed")
            self.assertEqual(result["validation"]["human_adoption"], "pending")

    def test_body_keeps_selected_relationship_detail_and_same_mandatory_spine(self):
        synopsis = inspect_story_v2('{"slots":[]}', self.material, self.plan, "synopsize")
        story = inspect_story_v2('{"slots":[]}', self.material, self.plan, "narrate")
        self.assertEqual(synopsis["validation"]["required_event_coverage"], story["validation"]["required_event_coverage"])
        self.assertNotEqual(synopsis["text"], story["text"])
        detail = "鬼の弟が桃太郎に向ける好意は薄れた。"
        self.assertNotIn(detail, synopsis["text"])
        self.assertIn(detail, story["text"])
        self.assertLess(story["text"].index("鬼は倒れた。"), story["text"].index(detail))
        self.assertLess(story["text"].index(detail), story["text"].index("桃太郎は村へ移動した。"))

    def test_omitted_context_is_not_reintroduced_and_core_response_ids_stay_stable(self):
        packet = json.loads(build_story_v2_prompt(self.material, self.plan, "story").split("固定場面パケット：\n", 1)[1])
        core = [slot for slot in packet["slots"] if slot["core"]]
        self.assertEqual([slot["slot_id"] for slot in core], [f"scene-{i:03d}" for i in range(1, 7)])
        self.assertTrue(core[-2]["event_id"].endswith(":1485"))
        removed = next(slot["event_id"] for slot in packet["slots"] if not slot["core"])
        plan = deepcopy(self.plan)
        for beat in plan["beats"]:
            beat["event_ids"] = [event_id for event_id in beat["event_ids"] if event_id != removed]
        plan["beats"] = [beat for beat in plan["beats"] if beat["event_ids"]]
        plan["omissions"].append({"event_ids": [removed], "reason": "読み手が選んだ本文からの省略"})
        plan = revise_identity(plan)
        story = inspect_story_v2('{"slots":[]}', self.material, plan, "story")
        self.assertNotIn(removed, [i for source in story["paragraph_sources"] for i in source["event_ids"]])

    def test_unapplied_effect_descriptions_and_unsuccessful_sabotage_are_not_facts(self):
        event = next(e for e in self.material["events"] if e["verb"] == "payoff")
        registered = {**event, "type": "effect_registered", "result": "registered"}
        self.assertIsNone(_fact_sentence(registered))
        event = next(e for e in fixture("03")[0]["events"] if e["verb"] == "sabotage")
        self.assertIsNone(_fact_sentence({**event, "result": "failed", "effective": False}))

    def test_real_observed_paraphrases_and_prefixed_ending_do_not_duplicate_facts(self):
        material, _, _ = fixture("03")
        plan = create_plan(material)
        packet = json.loads(build_story_v2_prompt(material, plan, "story").split("固定場面パケット：\n", 1)[1])
        ally = next(s for s in packet["slots"] if s["anchor"] == "馬は桃太郎の仲間になった。")
        ending = packet["slots"][-1]
        raw = json.dumps({"slots": [
            {"slot_id": ally["slot_id"], "text": "その馬は桃太郎の仲間となった。"},
            {"slot_id": ending["slot_id"], "text": "最終的に、" + ending["anchor"]},
        ]}, ensure_ascii=False)
        result = inspect_story_v2(raw, material, plan, "story")
        self.assertNotIn("その馬", result["text"])
        self.assertNotIn("最終的に", result["text"])
        self.assertEqual(result["text"].count("仲間"), 1)
        self.assertEqual(result["text"].count(ending["anchor"]), 1)


if __name__ == "__main__":
    unittest.main()
