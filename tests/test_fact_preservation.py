"""Contract adversaries: provenance, stages, disclosure and meaning boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from execution.provenance import ConfigError, canonical, sha256
from execution.fact_store import FactStore
from gapengine.fact_contract import extract_supplement, make_package, packet, build_prompt, seal
from gapengine.fact_validation import inspect_output
from fact_fixtures import example, records, raw_log, response


class FactContractTests(unittest.TestCase):
    def test_stage_and_belief_dependencies_reject_premature_knowledge(self):
        original = records()
        cases = []
        bad = deepcopy(original); bad[-2]["depends_on"] = ["F3"]; cases.append(bad)
        bad = deepcopy(original); bad[6]["depends_on"] = ["M1"]; cases.append(bad)
        bad = deepcopy(original); bad[11]["content"]["claim_ids"].append("F0"); cases.append(bad)
        bad = deepcopy(original); bad[9]["depends_on"] = ["read-candidate"]; cases.append(bad)
        bad = deepcopy(original); bad[-1]["content"]["domain"] = "one_overall_trust"; cases.append(bad)
        for case in cases:
            with self.subTest(case=case[-1]), self.assertRaises(ConfigError):
                extract_supplement(raw_log(case), {}, {"run_id": "example", "candidate_id": "mio"})

    def test_legacy_log_is_not_reinterpreted_as_three_notification_stages(self):
        raw = b'{"kind":"event","subject":"mio","verb":"message_read","details":{}}\n'
        supplement = extract_supplement(raw, {}, {"run_id": "old", "candidate_id": "mio"})
        self.assertEqual(supplement["records"], [])
        self.assertEqual(supplement["unknowns"], [{"row": 1, "status": "unsupported_extraction"}])
        self.assertEqual(supplement["source_binding"]["source_log_sha256"], sha256(raw))
        with self.assertRaises(ConfigError):
            make_package(supplement, selected=[], required=[])

    def test_required_dependencies_survive_compression_hidden_fact_stays_out(self):
        _, _, supplement, package = example()
        compact = make_package(supplement, selected=["F5-P", "F5-E"], required=["F5-P", "F5-E"], hidden=["hidden"])
        for kind in ("synopsize", "narrate"):
            data = packet(compact, kind)
            self.assertTrue({"M1", "M2", "F2", "F3", "F4", "F5-P", "F5-E"} <= {r["id"] for r in data["records"]})
            prompt = build_prompt(compact, kind)
            self.assertNotIn("SECRET", prompt)
            self.assertNotIn('"hidden"', prompt)
        self.assertNotEqual(build_prompt(package, "synopsize"), build_prompt(package, "narrate"))

    def test_hidden_dependency_and_invalid_selection_rejected(self):
        _, _, supplement, _ = example()
        for selection in ({"selected": ["M1"], "required": ["M2"]},
                          {"selected": ["M1", "M1"], "required": []},
                          {"selected": ["F3"], "required": [], "hidden": ["M1"]}):
            with self.subTest(selection=selection), self.assertRaises(ConfigError):
                make_package(supplement, **selection)

    def test_no_ending_is_required_in_new_contract_old_validator_stays_strict(self):
        _, _, _, package = example()
        self.assertTrue(build_prompt(package, "narrate"))
        from test_story_pipeline import fixture
        from gapengine.story_plan import create_plan, validate_plan, revise_identity
        material, _, _ = fixture()
        material["events"] = [e for e in material["events"] if e["type"] != "ending"]
        from gapengine.story_materials import material_digest
        material["content_sha256"] = material_digest(material)
        with self.assertRaises(ConfigError):
            validate_plan(material, create_plan(material))

    def test_same_message_is_retained_across_claim_split(self):
        _, _, supplement, _ = example()
        claims = [r for r in supplement["records"] if r["kind"] == "claim"]
        self.assertEqual([r["id"] for r in claims], ["M1", "M2"])
        self.assertEqual({r["content"]["message_id"] for r in claims}, {"M"})
        self.assertFalse(any(r["kind"] == "belief" and r["content"]["domain"] == "intent_inference" for r in supplement["records"]))

    def test_styles_and_synopsis_are_versioned_without_changing_sources(self):
        _, _, supplement, package = example()
        styles = {**package["styles"], "narrate": "短い文を中心に書く。"}
        changed = make_package(supplement, **package["selection"], styles=styles)
        self.assertNotEqual(package["id"], changed["id"])
        self.assertEqual(package["supplement"], changed["supplement"])
        self.assertEqual(build_prompt(package, "synopsize"), build_prompt(changed, "synopsize"))
        self.assertNotEqual(build_prompt(package, "narrate"), build_prompt(changed, "narrate"))
        bad = make_package(supplement, **package["selection"], styles={**styles, "narrate": "事実を変更して面白く"})
        with self.assertRaises(ConfigError):
            build_prompt(bad, "narrate")


class FactValidationTests(unittest.TestCase):
    def setUp(self):
        self.package = example()[3]
        self.good = response(self.package)

    def inspect(self, doc):
        return inspect_output(json.dumps(doc, ensure_ascii=False), self.package, "narrate")["validation"]

    def test_correct_references_with_claim_promoted_to_world_truth_fails_typed_meaning(self):
        bad = deepcopy(self.good)
        claim = next(a for a in bad["assertions"] if a["record_id"] == "M2")
        claim["record"]["kind"] = "world_fact"
        bad["text"] = "道路は実際に通行止めだった。"
        result = self.inspect(bad)
        self.assertEqual(result["structural_status"], "passed")
        self.assertEqual(result["typed_meaning_status"], "failed")
        self.assertEqual(result["semantic_findings"][0]["record_id"], "M2")

    def test_false_prose_with_correct_typed_claims_is_unverified_never_accepted(self):
        bad = deepcopy(self.good)
        bad["text"] = "道路は実際に通行止めだった。澪は通知に気づく前から理解していた。"
        result = self.inspect(bad)
        self.assertEqual(result["structural_status"], "passed")
        self.assertEqual(result["typed_meaning_status"], "passed")
        self.assertEqual(result["semantic_review_status"], "not_reviewed")
        self.assertEqual(result["human_adoption"], "pending")
        self.assertTrue(result["unverified"])

    def test_changed_actor_time_belief_and_attempt_as_success_are_semantic_failures(self):
        for rid, field, value in (("M1", "actor", "mio"), ("F4", "at", 1),
                                  ("read-candidate", "kind", "action_result"),
                                  ("F5-E", "content", {"predicate": "road_closed", "value": True})):
            bad = deepcopy(self.good)
            next(a for a in bad["assertions"] if a["record_id"] == rid)["record"][field] = value
            with self.subTest(rid=rid):
                result = self.inspect(bad)
                self.assertEqual(result["structural_status"], "passed")
                self.assertEqual(result["typed_meaning_status"], "failed")
        bad = deepcopy(self.good)
        next(a for a in bad["assertions"] if a["record_id"] == "M2")["record"]["content"]["value"] = 1
        self.assertEqual(self.inspect(bad)["typed_meaning_status"], "failed")

    def test_missing_or_hidden_references_are_structural_failures(self):
        for rid in ("missing", "hidden"):
            bad = deepcopy(self.good); bad["assertions"][0]["record_id"] = rid
            self.assertEqual(self.inspect(bad)["structural_status"], "failed")
        bad = deepcopy(self.good); bad["assertions"] = []
        self.assertEqual(self.inspect(bad)["structural_status"], "failed")
        checked = inspect_output('{"text":"保存する本文", "assertions":false}', self.package, "narrate")
        self.assertEqual(checked["text"], "保存する本文")


class FactStoreTests(unittest.TestCase):
    def test_roundtrip_source_immutability_tamper_unknown_version_and_binding(self):
        raw, inputs, _, package = example()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); original = root / "original.jsonl"; original.write_bytes(raw)
            store = FactStore(root / "control")
            ref = store.save(package, raw=original.read_bytes(), inputs=inputs)
            self.assertEqual(store.resolve(ref), package)
            self.assertEqual(original.read_bytes(), raw)
            with self.assertRaises(ConfigError):
                store.resolve(ref, binding={"candidate_id": "another"})
            with self.assertRaises(ConfigError):
                store.save(package, raw=raw+b"\n", inputs=inputs)
            bad = deepcopy(package); bad["schema_version"] = 2; bad = seal(bad, "facts-")
            with self.assertRaises(ConfigError):
                store.save(bad, raw=raw, inputs=inputs)
            (store.root / package["id"] / "package.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ConfigError):
                store.resolve(ref)
