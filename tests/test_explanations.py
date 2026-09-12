import copy
import unittest
from gapengine.explanations import explain_rows, merge_after


def header():
    return {"kind":"header", "protagonist":"A", "truth":{"culprit":"SECRET"}}


def snapshot(layers):
    return {"kind":"snapshot", "subject":"A", "turn":0, "layers":layers}


def decision(turn, verb, args=None, actor=None, relations=None, targets=None, details=None, subject="A"):
    return {"kind":"decision", "turn":turn, "subject":subject, "verb":verb, "args":args or [],
            "result":"applied", "details":details or {},
            "delta":{"actor":actor or {}, "relations":relations or [], "targets":targets or {}}}


class ExplanationTests(unittest.TestCase):
    def test_asset_deletion_without_baseline_keeps_unknown_quantity_and_partial_cost(self):
        rows = [header(), decision(1, "give_item", actor={"resources":{"assets":{"手紙":None}}}),
                decision(2, "grand_gesture", subject="C", actor={"resources":{"assets":{"花束":None}, "reputation":.35}})]
        items = explain_rows(rows)["decisions"]
        for item, owner in zip(items, ("A", "C")):
            self.assertEqual(item["cost"]["status"], "confirmed")
            loss = item["cost"]["items"][0]
            self.assertEqual(loss["owner"], owner)
            self.assertIsNone(loss["amount"])
            self.assertIsNone(loss["before"])
            self.assertIn("所持品", loss["text"])
        self.assertTrue(items[0]["cost"]["complete"])
        self.assertFalse(items[1]["cost"]["complete"])
        self.assertIn("resources.reputation", items[1]["cost"]["unknown_paths"])

    def test_precise_cost_baseline_overrides_stale_snapshot_but_not_knowledge(self):
        move = decision(2, "move", actor={"stamina":7, "resources":{"reputation":.1}})
        move["explanation"] = {"cost_baseline":{"version":1, "timing":"before_execute",
                                "stamina":9, "resources":{"reputation":.3}, "knowledge":["SECRET"]}}
        rows = [header(), snapshot({"stamina":1, "resources":{"reputation":.2}}), move]
        item = explain_rows(rows)["decisions"][0]
        self.assertEqual(sorted(c["amount"] for c in item["cost"]["items"]), [-2, -.2])
        self.assertTrue(item["cost"]["complete"])
        self.assertTrue(all(c["before_source"]["line"] == 3 for c in item["cost"]["items"]))
        self.assertNotIn("SECRET", str(item["grounds"]))
        for baseline in ({}, {"version":99, "timing":"before_execute", "stamina":9},
                         {"version":1, "timing":"after_execute", "stamina":9}):
            move["explanation"]["cost_baseline"] = baseline
            cost = explain_rows(rows)["decisions"][0]["cost"]
            self.assertIn("stamina", cost["unknown_paths"])
            self.assertFalse(cost["complete"])

    def test_turning_candidates_absence_and_incomplete_or_unsupported_remain_distinct(self):
        ending = {"kind":"event", "verb":"ending", "subject":"A", "turn":3,
                  "details":{}, "delta":{}, "result":"expired"}
        rows = [header(), decision(1,"rest"), ending]
        result = explain_rows(rows)
        turn = result["decisions"][0]["turning"]
        self.assertEqual((turn["status"],turn["confirmation"]), ("absent","absent"))
        self.assertEqual(turn["search"]["last_line"], 3)
        # The entire file must be scanned; a later same-turn marker changes the verdict.
        marker = {**ending,"verb":"betrayal","turn":1}
        rows.insert(2, marker)
        turn = explain_rows(rows)["decisions"][0]["turning"]
        self.assertEqual((turn["status"],turn["confirmation"]), ("confirmed","candidate"))
        self.assertEqual(turn["search"]["candidate_sources"][0]["line"],3)
        for row in (decision(1,"future_verb"), decision(1,"rethink"), decision(1,"move",actor={"zone":"B"}),
                    {**decision(1,"rest"),"policy":{"novelty":1}}):
            self.assertEqual(explain_rows([header(),row,ending])["decisions"][0]["turning"]["status"],"unknown")
        for incomplete in ([header(),decision(1,"rest"),{**marker,"verb":"future_event"},ending],
                           [header(),decision(1,"rest")],
                           [header(),decision(1,"rest"),{"kind":"unknown"},ending],
                           [header(),{**decision(1,"rest"),"delta":{}},ending]):
            self.assertEqual(explain_rows(incomplete)["decisions"][0]["turning"]["status"],"unknown")
        rethink = decision(1,"rethink",details={"before":{},"after":{"f":1}},subject="C")
        turn = explain_rows([header(),rethink])["decisions"][0]["turning"]
        self.assertEqual((turn["status"],turn["confirmation"]),("confirmed","candidate"))

    def test_predecision_knowledge_no_future_or_truth_leak(self):
        rows = [header(),snapshot({"valued_beliefs":{"culprit":{"value":"B","confidence":.72}}}),
                decision(1,"confront",["B","culprit"],actor={"valued_beliefs":{"culprit":{"confidence":.36}}}),
                decision(2,"observe",["C"],actor={"belief":{"C":{"known_modifiers":["future"]}}}),
                decision(3,"rest")]
        items = explain_rows(rows)["decisions"]
        self.assertEqual(items[0]["grounds"]["knowledge"]["valued_beliefs"]["culprit"]["confidence"],.72)
        self.assertEqual(items[1]["grounds"]["knowledge"]["valued_beliefs"]["culprit"]["confidence"],.36)
        self.assertNotIn("future",str(items[1]["grounds"]))
        self.assertIn("future",str(items[2]["grounds"]))
        self.assertNotIn("SECRET",str([i["grounds"] for i in items]))
        self.assertEqual(items[0]["choice"]["status"],"confirmed")
        self.assertEqual(items[0]["choice"]["alternatives"]["status"],"unknown")

    def test_after_values_deletion_target_update_cost_direction_and_unknown(self):
        rows = [header(),snapshot({"resources":{"reputation":.25,"assets":{"coin":1}}}),
                decision(1,"confront",actor={"resources":{"reputation":.15}},relations=[
                    {"observer":"B","target":"A","affinity":-.3},
                    {"observer":"A","target":"C","affinity":-.2}]),
                decision(2,"give_item",actor={"resources":{"assets":{"coin":None}}}),
                decision(3,"observe",subject="B",targets={"A":{"valued_beliefs":{"f":{"value":"C","confidence":.4}}}}),
                decision(4,"rest"), decision(5,"move",actor={"stamina":4})]
        items = explain_rows(rows)["decisions"]
        costs = items[0]["cost"]["items"]
        self.assertEqual([i["amount"] for i in costs],[-.1,-.3])
        self.assertTrue(all(i["owner"] == "A" for i in costs))
        self.assertEqual(items[1]["cost"]["items"][0]["amount"],-1)
        self.assertEqual(items[3]["grounds"]["knowledge"]["valued_beliefs"]["f"]["confidence"],.4)
        self.assertEqual(items[4]["cost"]["status"],"unknown")
        self.assertEqual(items[0]["cost"]["delayed"]["status"],"unknown")
        unknown = explain_rows([header(),decision(1,"confront",actor={"resources":{"reputation":.15}})])["decisions"][0]
        self.assertEqual(unknown["cost"]["status"],"unknown")
        self.assertEqual(explain_rows([header(),decision(1,"future_verb")])["decisions"][0]["cost"]["status"],"unknown")

    def test_linked_representative_invalidation_and_same_turn_source(self):
        def rethink(turn,before,after):
            return decision(turn,"rethink",actor={"valued_beliefs":after},details={"before":before,"after":after,"evidence":["clue"]})
        b={"f":{"value":"B","confidence":.75}}
        c={"f":{"value":"C","confidence":.75}}
        rows=[header(),snapshot({"valued_beliefs":{}}),rethink(1,{},b),rethink(2,b,c),rethink(2,c,b),decision(3,"confront",["B","f"])]
        result=explain_rows(rows,sha256="hash")
        rep=result["representative"]
        self.assertEqual(rep["line"],5)
        self.assertEqual(rep["turning"]["links"][0]["downstream"]["line"],6)
        self.assertEqual(rep["cost"]["status"],"absent")
        self.assertEqual(result,explain_rows(copy.deepcopy(rows),sha256="hash"))
        self.assertEqual(result["decisions"][0]["turning"]["confirmation"],"candidate")

    def test_confidence_interruption_is_not_silently_ignored(self):
        rows=[header(),snapshot({"valued_beliefs":{"f":{"value":"C","confidence":.7}}}),
              decision(1,"rethink",actor={"valued_beliefs":{"f":{"value":"B","confidence":.75}}},details={"before":{"f":{"value":"C","confidence":.7}},"after":{"f":{"value":"B","confidence":.75}}}),
              decision(2,"confront",["B","f"],actor={"valued_beliefs":{"f":{"confidence":.18}}}),
              decision(3,"rethink",actor={"valued_beliefs":{"f":{"confidence":.75}}}),
              decision(4,"confront",["B","f"])]
        result=explain_rows(rows)
        first=result["decisions"][0]
        self.assertEqual([l["downstream"]["turn"] for l in first["turning"]["links"]],[2])

    def test_pending_cancellation_replant_uses_latest(self):
        def pending(turn,resolved=False):
            return {"id":"p:B","library_id":"p","planted_by":"A","planted_turn":turn,"mode":"chosen","resolved":resolved,"target":"B"}
        rows=[header(),snapshot({"pending":[]}),decision(1,"neutralize",actor={"pending":[pending(1)]}),
              decision(2,"rest",actor={"pending":[pending(1,True)]}),
              decision(3,"neutralize",actor={"pending":[pending(3)]}),
              decision(4,"payoff",["p"],actor={"pending":[pending(3,True)]},details={"effect_id":"p:B","applied":{"kind":"stance"}})]
        result=explain_rows(rows)
        self.assertEqual(result["representative"]["turn"],3)
        self.assertEqual(result["representative"]["turning"]["confirmation"],"confirmed")

    def test_learned_fact_survives_snapshot_without_knowledge_field(self):
        rows=[header(), {"kind":"event","subject":"A","verb":"learn_fact","turn":1,"details":{"fact":"clue"}},
              snapshot({"valued_beliefs":{}}),decision(2,"rest")]
        item=explain_rows(rows)["decisions"][0]
        self.assertEqual(item["grounds"]["knowledge"]["knowledge"],["clue"])
        self.assertIn(2,[s["line"] for s in item["grounds"]["sources"]])

    def test_missing_decisions_empty_and_partial_are_unknown(self):
        self.assertIsNone(explain_rows([header()])["representative"])
        result=explain_rows([header(),decision(1,"rest")])
        self.assertEqual(result["representative"]["turning"]["status"],"unknown")
        self.assertEqual(result["representative"]["grounds"]["status"],"unknown")
        self.assertEqual(result["trajectory_signature"],explain_rows([header(),decision(1,"rest")])["trajectory_signature"])
        state={"x":{"a":1,"b":2}}
        merge_after(state,{"x":{"a":None,"b":.5}})
        self.assertEqual(state,{"x":{"b":.5}})

if __name__ == "__main__":
    unittest.main()
