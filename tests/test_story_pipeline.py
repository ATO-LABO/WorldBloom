"""Meaningful provenance, chronology, recovery and actual Momotaro fixture gates."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from execution.provenance import ConfigError, canonical, sha256
from execution.story_store import StoryStore
from gapengine.story_materials import extract_materials, merge_post, material_digest
from gapengine.story_plan import create_plan, validate_plan, revise_identity
from gapengine.story_narration import build_story_prompt
from gapengine.story_validation import inspect_story

FIXTURES = Path(__file__).parent / "fixtures/story_materials"

def fixture(name="06"):
    doc=json.loads((FIXTURES/(name+".json")).read_text(encoding="utf-8"))
    # Sparse fixtures preserve original row order with blank physical lines.
    raw_lines=[""]*max(r["row"] for r in doc["rows"])
    for r in doc["rows"]:
        raw_lines[r["row"]-1]=json.dumps(r["record"],ensure_ascii=False,sort_keys=True)
    raw=("\n".join(raw_lines)+"\n").encode("utf-8")
    inputs={p:t.encode("utf-8") for p,t in doc["inputs"].items()}
    binding={"run_id":"fixture","candidate_id":"candidate-"+name,"source_log_sha256":sha256(raw),
             "original_log_sha256":doc["source"]["layers_sha256"],"seed":doc["source"]["seed"]}
    return extract_materials(raw,inputs,binding),raw,inputs

class MaterialTests(unittest.TestCase):
    def test_subjectless_world_event_does_not_create_none_state(self):
        m,raw,inputs=fixture()
        raw += canonical({"kind":"event","subject":None,"verb":"payoff","turn":76,"details":{}})+b"\n"
        m=extract_materials(raw,inputs,{"run_id":"global-event","candidate_id":"a"})
        self.assertNotIn(None,m["final_recorded_state"]["subjects"])
        canonical(m)

    def test_actual_06_gift_grudge_return_order(self):
        material,raw,inputs=fixture()
        byrow={e["order"]:e for e in material["events"]}
        self.assertEqual(byrow[184]["verb"],"give_item")
        self.assertEqual(byrow[185]["type"],"relation_transition")
        self.assertEqual(byrow[1209]["type"],"effect_applied")
        self.assertEqual(byrow[1498]["type"],"ending")
        self.assertEqual(byrow[1203]["details"]["loser"],"鬼")
        self.assertTrue(any(f["value"]=="downed" for f in byrow[1203]["facts"]))
        self.assertEqual(material,extract_materials(raw,inputs,material["source_binding"]))
        campfire=[e for e in material["pending_effects"] if e["library_id"]=="campfire_oath"]
        self.assertTrue(campfire)
        self.assertTrue(all(e["status"]=="registered" for e in campfire))
        self.assertNotIn("焚き火",byrow[12]["label"])
        self.assertTrue(any(x["kind"]=="gift_to_ally" for x in material["causal_links"]))

    def test_post_values_replacement_and_deletion(self):
        state={"vitality":"alive","resources":{"assets":{"rope":2}},"pending":[1,2]}
        merge_post(state,{"vitality":"downed","resources":{"assets":{"rope":None,"boat":1}},"pending":[3]})
        self.assertEqual(state,{"vitality":"downed","resources":{"assets":{"boat":1}},"pending":[3]})

    def test_relation_delta_is_increment_and_snapshot_not_current(self):
        inputs={"project/world.yaml":b"name: fixture\nprotagonist: A\n",
                "project/subjects/a.yaml":b"id: A\n"}
        rows=[{"kind":"snapshot","subject":"A","layers":{"zone":"old"},"relations":[{"observer":"A","target":"B","affinity":0.4,"awareness":0.2}]},
              {"kind":"decision","subject":"A","verb":"move","turn":1,"effective":True,"result":"moved",
               "details":{"origin":"old","dest":"new"},"delta":{"actor":{"zone":"new"},"relations":[{"observer":"A","target":"B","affinity":0.2,"awareness":0.1}]}}]
        m=extract_materials(b"\n".join(canonical(r) for r in rows),inputs,{"run_id":"fixture","candidate_id":"a"})
        self.assertEqual(m["final_recorded_state"]["subjects"]["A"]["zone"],"new")
        rel=next(f for f in m["events"][0]["facts"] if f["predicate"]=="relation_increment")
        self.assertEqual(rel["post_value"],{"affinity":0.6,"awareness":0.3})
        self.assertEqual(m["events"][0]["state_as_of"]["row"],2)
        self.assertEqual(m["events"][0]["state_as_of"]["snapshot_row"],1)

    def test_other_real_fixture_turns_survive(self):
        for name,verb in [("03","sabotage"),("12","buy"),("08","exposure"),("05","mislead")]:
            m,_,_=fixture(name)
            self.assertTrue(any(e["verb"]==verb for e in m["events"]),name)
            p=create_plan(m)
            selected={i for b in p["beats"] for i in b["event_ids"]}
            self.assertTrue(any(e["verb"]==verb and e["event_id"] in selected for e in m["events"]),name)

class PlanTests(unittest.TestCase):
    def setUp(self):
        self.material,_,_=fixture()
        self.plan=create_plan(self.material)
    def test_unknown_id_reversed_order_required_and_gift(self):
        bad=deepcopy(self.plan)
        bad["beats"][0]["event_ids"].append("invented")
        with self.assertRaises(ConfigError): validate_plan(self.material,bad)
        bad=deepcopy(self.plan); bad["beats"].reverse()
        with self.assertRaises(ConfigError): validate_plan(self.material,bad)
        bad=deepcopy(self.plan); bad["required_event_ids"].append("invented")
        with self.assertRaises(ConfigError): validate_plan(self.material,bad)
        gift=next(e["event_id"] for e in self.material["events"] if e["order"]==184)
        bad=deepcopy(self.plan)
        bad["beats"]=[{**b,"event_ids":[i for i in b["event_ids"] if i!=gift]} for b in bad["beats"]]
        bad["beats"]=[b for b in bad["beats"] if b["event_ids"]]
        bad["required_event_ids"].remove(gift)
        with self.assertRaises(ConfigError): validate_plan(self.material,bad)
    def test_causal_spine_and_no_length_or_paragraph_quota(self):
        plan=self.plan
        self.assertIsNone(plan["length"])
        self.assertLess(len(create_plan(fixture("12")[0])["required_event_ids"]),
                        sum(len(b["event_ids"]) for b in create_plan(fixture("12")[0])["beats"]))
        self.assertTrue(plan["narrative_threads"])
        prompt=build_story_prompt(self.material,plan)
        self.assertNotIn("字。",prompt.split("素材と構成")[0])
        self.assertNotIn("一つにつき一段落",prompt)
        self.assertIn("転機",prompt)
    def test_unknown_motives_and_unapplied_conditions_not_writer_events(self):
        prompt=build_story_prompt(self.material,self.plan)
        packet,_=json.JSONDecoder().raw_decode(prompt.split("素材と構成（転機を中心に物語にする）：\n")[1])
        self.assertTrue(all(e["verb"]!="planted" for e in packet["pivotal_events"]+packet["supporting_events"]))
        self.assertTrue(any(e["verb"]=="payoff" for e in self.material["events"]))
        self.assertIn("downedは戦闘不能",prompt)
    def test_incremental_omissions_preserve_prior_reasons(self):
        material,raw,inputs=fixture("12")
        plan=create_plan(material)
        with tempfile.TemporaryDirectory(prefix="wb-story-edit-") as tmp:
            store=StoryStore(Path(tmp));store.save_material(material,artifacts={"inputs/layers.jsonl":raw,**{"inputs/"+p:b for p,b in inputs.items()}})
            rid=material["source_binding"]["run_id"];cid=material["source_binding"]["candidate_id"]
            store.set_current(rid,cid,plan,expected_revision=0)
            event=next(e["event_id"] for e in material["events"] if e["verb"]=="buy" and "鉄砲" in str(e))
            changes={"beats":[{**b,"event_ids":[i for i in b["event_ids"] if i!=event]} for b in plan["beats"]],
                "required_event_ids":[i for i in plan["required_event_ids"] if i!=event],
                "omissions":[{"event_ids":[event],"reason":"利用者がこの版では省略を選択"}]}
            changes["beats"]=[b for b in changes["beats"] if b["event_ids"]]
            edited=store.edit(rid,cid,changes,expected_revision=1)["plan"]
            self.assertTrue({i for o in plan["omissions"] for i in o["event_ids"]}.issubset({i for o in edited["omissions"] for i in o["event_ids"]}))
            self.assertIn(event,{i for o in edited["omissions"] for i in o["event_ids"]})
    def test_missing_ending_rejected(self):
        bad=deepcopy(self.plan)
        bad["required_event_ids"]=[i for i in bad["required_event_ids"] if i!=self.material["events"][-1]["event_id"]]
        with self.assertRaises(ConfigError):validate_plan(self.material,bad)

class StoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="wb-story-test-")
        self.addCleanup(self.tmp.cleanup)
        self.store=StoryStore(Path(self.tmp.name))
        self.material,self.raw,self.inputs=fixture()
        self.store.save_material(self.material,artifacts={"inputs/layers.jsonl":self.raw,
            **{"inputs/"+p:b for p,b in self.inputs.items()}})
        self.plan=create_plan(self.material)
        self.store.set_current("fixture","candidate-06",self.plan,expected_revision=0)
    def confirm(self):
        self.store.confirm(self.plan["plan_id"],sha256(canonical(self.plan)))
        return self.store.reference(self.plan["plan_id"])
    def test_confirmation_and_cross_candidate_binding(self):
        with self.assertRaises(ConfigError):self.store.reference(self.plan["plan_id"])
        ref=self.confirm()
        self.store.resolve(ref,binding={"candidate_id":"candidate-06"})
        with self.assertRaises(ConfigError):self.store.resolve(ref,binding={"candidate_id":"other"})
        bad={**ref,"plan_sha256":"0"*64}
        with self.assertRaises(ConfigError):self.store.resolve(bad)
    def test_conflict_new_revision_requires_new_confirmation(self):
        self.confirm()
        current=self.store.edit("fixture","candidate-06",{"focus":"後に残る関係"},expected_revision=1)
        self.assertFalse(current["confirmed"])
        with self.assertRaises(ConfigError):self.store.edit("fixture","candidate-06",{"focus":"lost"},expected_revision=1)
        with self.assertRaises(ConfigError):self.store.confirm(self.plan["plan_id"],sha256(canonical(self.plan)))
    def test_material_plan_and_confirmation_tamper(self):
        ref=self.confirm()
        root=self.store.folder("plans",self.plan["plan_id"])
        (root/"confirmation.json").write_text("{}",encoding="utf-8")
        with self.assertRaises(ConfigError):self.store.resolve(ref)
        with self.assertRaises(ConfigError):self.store.material("../outside")
    def test_missing_source_fails_before_publication(self):
        with self.assertRaises(ConfigError):self.store.save_material(self.material,artifacts={})

class ValidationTests(unittest.TestCase):
    def test_invalid_paragraph_shape_keeps_readable_text(self):
        m,_,_=fixture();p=create_plan(m)
        raw=json.dumps({"paragraphs":[{"text":"保存する本文。\n\n次の段落。"}]},ensure_ascii=False)
        result=inspect_story(raw,m,p)
        self.assertEqual(result["text"],"保存する本文。\n\n次の段落。")
        self.assertEqual(result["validation"]["structural_status"],"failed")
    def test_story_level_references_allow_free_paragraphs(self):
        m,_,_=fixture();p=create_plan(m)
        byid={e["event_id"]:e for e in m["events"]}
        aliases=["r"+str(byid[i]["order"]) for i in p["required_event_ids"]]
        raw=json.dumps({"story":"場面を描く。\n\n次の行動へ進む。","used_event_ids":aliases},ensure_ascii=False)
        result=inspect_story(raw,m,p)
        self.assertEqual(result["validation"]["structural_status"],"passed")
        self.assertEqual(result["validation"]["reference_encoding"],"row_alias_story_v2")

    def test_paragraph_local_contract_and_alias_expansion(self):
        m,_,_=fixture();p=create_plan(m)
        byid={e["event_id"]:e for e in m["events"]}
        raw=json.dumps({"paragraphs":[{"text":"出来事をたどる。","event_ids":["r"+str(byid[i]["order"]) for i in p["required_event_ids"]],"embellishments":[]}]})
        result=inspect_story(raw,m,p)
        self.assertEqual(result["validation"]["structural_status"],"passed")
        self.assertEqual(set(result["paragraph_sources"][0]["event_ids"]),set(p["required_event_ids"]))
    def test_reference_failure_retains_text_without_guessing_alignment(self):
        m,_,_=fixture();p=create_plan(m)
        raw=json.dumps({"text":"一段落。","paragraph_sources":[{"paragraph":1},{"paragraph":2}]})
        r=inspect_story(raw,m,p)
        self.assertEqual(r["text"],"一段落。")
        self.assertEqual(r["paragraph_sources"],[])
        self.assertEqual(r["validation"]["structural_status"],"failed")

    def test_references_do_not_approve_human_quality(self):
        m,_,_=fixture();p=create_plan(m)
        body={"text":"贈与から関係が変わり、戦闘の後に恨みが向いた。","paragraph_sources":[
            {"paragraph":1,"event_ids":p["required_event_ids"],"embellishments":[]}]}
        result=inspect_story(json.dumps(body,ensure_ascii=False),m,p)
        self.assertEqual(result["validation"]["structural_status"],"passed")
        self.assertEqual(result["validation"]["human_adoption"],"pending")
        self.assertEqual(result["validation"]["semantic_review_status"],"not_reviewed")
    def test_invalid_json_unknown_refs_missing_coverage(self):
        m,_,_=fixture();p=create_plan(m)
        for raw in ["unstructured",json.dumps({"text":"本文","paragraph_sources":[
            {"paragraph":1,"event_ids":["madeup"],"embellishments":[]}]}),
            json.dumps({"text":"本文","paragraph_sources":[{"paragraph":1,"event_ids":[],"embellishments":[]}]})]:
            self.assertEqual(inspect_story(raw,m,p)["validation"]["structural_status"],"failed")

if __name__=="__main__": unittest.main()
