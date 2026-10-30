"""Proposal semantics and genuine cross-world execution, not prose matching."""
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import unittest

from engine.actions import Action, candidates
from engine.deliberation import Deliberation
from engine.sim import Simulation
from engine.verbs import VerbEngine
from engine.world import World
from gapengine.deliberation_episode import load_episode, run_batch
from gapengine.deliberation_story import scene_material

ROOT = Path(__file__).resolve().parents[1]
REFORM = ROOT / "projects/reform_hearing/world.yaml"
RESCUE = ROOT / "projects/rescue_council/world.yaml"


class DeliberationTests(unittest.TestCase):
    def setUp(self):
        self.world, self.people = load_episode(REFORM)
        self.rt = self.world.deliberation
        self.engine = VerbEngine(self.world, random.Random(0))
        self.spec = self.rt.specs["main"]
        self.proposer = self.spec["proposer"]
        self.authority = self.spec["authority"]

    def act(self, actor, verb, *args):
        return self.engine.execute(self.people[actor], Action(verb, args), turn=1, day=1)

    def submit(self):
        self.assertEqual(self.act(self.proposer, "submit_proposal", "main", "original", "0")[0], "applied")

    def review_all(self):
        rev = str(self.rt.records["main"]["revision"])
        for actor in self.spec["reviewers"]:
            self.assertEqual(self.act(actor, "review_proposal", "main", rev)[0], "applied")

    def judge(self):
        revision = str(self.rt.records["main"]["revision"])
        self.assertEqual(self.act(self.proposer, "request_decision", "main", revision)[0], "applied")
        return self.act(self.authority, "decide_proposal", "main", revision)

    def test_affection_and_assent_are_separate_and_knowledge_scoped(self):
        self.submit(); self.review_all()
        reviews = self.rt.records["main"]["reviews"]
        self.assertGreater(self.world.relations.stance(self.authority, self.proposer), .5)
        self.assertEqual(reviews[self.authority]["stance"], "oppose")
        self.assertLess(self.world.relations.stance("瀬尾", self.proposer), 0)
        self.assertEqual(reviews["瀬尾"]["stance"], "support")
        before = self.world.relations.snapshot()
        self.judge()
        self.assertTrue(self.rt.decided("main", "rejected"))
        self.assertEqual(self.world.relations.snapshot(), before)

    def test_amendment_requires_fresh_reviews_and_preserves_old_opposition(self):
        self.submit(); self.review_all()
        old_hash = self.rt.records["main"]["terms_hash"]
        self.act(self.proposer, "amend_proposal", "main", "protected", "1")
        record = self.rt.records["main"]
        self.assertEqual(record["reviews"], {})
        self.assertNotEqual(record["terms_hash"], old_hash)
        self.assertEqual(len(record["review_history"]), 3)
        before = self.rt.snapshot()
        for actor, verb, args in [(self.authority, "decide_proposal", ("main", "1")),
                                  (self.authority, "review_proposal", ("main", "1")),
                                  (self.proposer, "amend_proposal", ("main", "staged", "1")),
                                  (self.authority, "decide_proposal", ("main", "2"))]:
            self.assertEqual(self.act(actor, verb, *args)[0], "invalid")
            self.assertEqual(self.rt.snapshot(), before)
        self.review_all(); self.judge()
        self.assertTrue(self.rt.decided("main", "approved"))
        self.assertEqual(self.rt.state["婚姻制度"], "存続")
        self.assertEqual(self.rt.state["検討段階"], "正式検討へ")

    def test_no_private_profile_reading_for_amendment_before_heard_objection(self):
        self.submit()
        offered = self.rt.candidates(self.world, self.proposer)
        self.assertFalse(any(v == "amend_proposal" for v, _, _ in offered))
        self.review_all()
        self.assertTrue(any(v == "amend_proposal" for v, _, _ in self.rt.candidates(self.world, self.proposer)))

    def test_absent_reviewer_does_not_learn_revision_and_must_be_briefed(self):
        # A registered second room is an actual World location, not a text flag.
        self.people["瀬尾"].zone = "別室"
        self.submit()
        self.assertNotIn("瀬尾", self.rt.records["main"]["known_by"])
        self.people["瀬尾"].zone = self.spec["place"]
        self.assertEqual(self.act("瀬尾", "review_proposal", "main", "1")[0], "invalid")
        self.act(self.proposer, "present_proposal", "main", "瀬尾", "1")
        self.assertEqual(self.act("瀬尾", "review_proposal", "main", "1")[0], "applied")

    def test_wrong_authority_place_dead_actor_and_unknown_choice_are_atomic(self):
        self.submit(); self.review_all()
        before = self.rt.snapshot()
        cases = [(self.proposer, "decide_proposal", ("main", "1")),
                 (self.proposer, "amend_proposal", ("main", "invented", "1")),
                 (self.authority, "decide_proposal", ("missing", "1"))]
        for actor, verb, args in cases:
            self.assertEqual(self.act(actor, verb, *args)[0], "invalid")
            self.assertEqual(self.rt.snapshot(), before)
        self.people[self.authority].zone = "別室"
        self.assertEqual(self.act(self.authority, "decide_proposal", "main", "1")[0], "invalid")
        self.people[self.authority].zone = self.spec["place"]
        self.people[self.authority].vitality = "dead"
        self.assertEqual(self.act(self.authority, "decide_proposal", "main", "1")[0], "invalid")
        self.assertEqual(self.rt.snapshot(), before)

    def test_missing_knowledge_is_deferred_not_invented_assent(self):
        self.people["有村"].knowledge.clear()
        self.submit(); self.review_all()
        self.judge()
        self.assertTrue(self.rt.decided("main", "deferred"))
        self.assertEqual(self.rt.records["main"]["decision"]["unresolved"], ["有村"])

    def test_new_knowledge_requires_explicit_reassessment(self):
        self.people["有村"].knowledge.clear()
        self.submit(); self.review_all()
        self.act(self.proposer, "request_decision", "main", "1")
        self.people["有村"].knowledge.add("移行費用試算")
        self.assertEqual(self.act(self.authority, "decide_proposal", "main", "1")[0], "invalid")
        self.act("有村", "review_proposal", "main", "1")
        self.assertEqual(len(self.rt.records["main"]["review_history"]), 4)
        self.assertEqual(self.act(self.authority, "decide_proposal", "main", "1")[0], "applied")

    def test_terminal_decision_cannot_be_rewritten_or_applied_twice(self):
        self.submit(); self.review_all()
        self.judge()
        before = self.rt.snapshot()
        self.assertEqual(self.act(self.authority, "decide_proposal", "main", "1")[0], "invalid")
        self.assertEqual(self.act(self.proposer, "amend_proposal", "main", "protected", "1")[0], "invalid")
        self.assertEqual(self.rt.snapshot(), before)

    def test_request_locks_revision_and_prevents_authority_preempting_proposer(self):
        self.submit(); self.review_all()
        self.assertEqual(self.act(self.authority, "decide_proposal", "main", "1")[0], "invalid")
        self.act(self.proposer, "request_decision", "main", "1")
        self.assertEqual(self.act(self.proposer, "amend_proposal", "main", "protected", "1")[0], "invalid")
        self.assertEqual(self.act(self.authority, "decide_proposal", "main", "1")[0], "applied")

    def test_dead_reviewer_cannot_count_toward_current_decision(self):
        self.submit(); self.review_all()
        self.act(self.proposer, "request_decision", "main", "1")
        self.people["有村"].vitality = "dead"
        self.assertEqual(self.act(self.authority, "decide_proposal", "main", "1")[0], "invalid")

    def test_contract_rejects_unknown_refs_bool_quorum_and_executable_terms(self):
        raw = self.rt.config
        mutations = [lambda s: s.update(quorum=True),
                     lambda s: s.update(required_support=["誰もいない"]),
                     lambda s: s["alternatives"][0]["terms"].update(婚姻資格={"eval": "True"}),
                     lambda s: s["profiles"][self.authority]["constraints"].update(不存在=["値"]),
                     lambda s: s["outcomes"]["approved"].update(不存在="値")]
        for mutate in mutations:
            changed = deepcopy(raw); mutate(changed["proposals"][0])
            with self.assertRaises(ValueError): Deliberation(changed)
        changed = deepcopy(raw); changed["proposals"][0]["profiles"][self.authority]["requires"] = ["不存在"]
        self.world.deliberation = Deliberation(changed)
        with self.assertRaises(ValueError): self.world.deliberation.bind(self.world)

    def test_runtime_state_is_defensive_and_rebind_resets_episode(self):
        self.submit()
        snapshot = self.rt.snapshot(); snapshot["proposals"]["main"]["status"] = "approved"
        self.assertFalse(self.rt.decided("main", "approved"))
        self.world.bind_subjects(self.people)
        self.assertEqual(self.rt.records["main"]["revision"], 0)

    def test_existing_world_is_opt_in_and_has_no_new_candidates(self):
        world = World.from_yaml(ROOT / 'projects/momotaro/world.yaml')
        self.assertIsNone(world.deliberation)

    def test_all_reviewers_are_needed_and_required_support_is_not_affinity(self):
        self.submit()
        self.act(self.authority, "review_proposal", "main", "1")
        self.assertEqual(self.act(self.authority, "decide_proposal", "main", "1")[0], "invalid")
        for actor in self.spec["reviewers"]:
            if actor != self.authority:
                self.act(actor, "review_proposal", "main", "1")
        self.judge()
        decision = self.rt.records["main"]["decision"]
        self.assertIn("有村", decision["missing_support"])
        self.assertIn(self.authority, decision["missing_support"])


class EpisodeTests(unittest.TestCase):
    def test_same_runtime_executes_two_genres_and_records_real_endings(self):
        with tempfile.TemporaryDirectory() as temp:
            for project in (REFORM, RESCUE):
                target = Path(temp) / project.parent.name
                summary = run_batch(project, target, list(range(8)))
                self.assertEqual(summary["trial_count"], 8)
                self.assertEqual(summary["counts"].get("execution_failure", 0), 0)
                trials = [json.loads(line) for line in (target / 'trials.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual(len(trials), 8)
                self.assertTrue(any(r["outcome"]["status"] == "approved" for r in trials))
                for row in trials:
                    outcome = row["outcome"]
                    lines = [json.loads(line) for line in (target / outcome["ledger"]).read_text(encoding='utf-8').splitlines()]
                    self.assertTrue(lines[0]['verified_execution'])
                    decisions = [r for r in lines if r.get('verb') == 'decide_proposal']
                    self.assertEqual(len(decisions), 1)
                    self.assertTrue(decisions[0]['effective'])
                    self.assertEqual(decisions[0]['details']['deliberation_event']['status'], outcome['status'])
                    self.assertEqual(outcome['saved_run']['actual_terminal']['event_id'], outcome['status'])
                    self.assertEqual(lines[-1]['verb'], 'ending')

    def test_seed_determinism_and_existing_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp)/'one', Path(temp)/'two'
            run_batch(REFORM, first, [3]); run_batch(REFORM, second, [3])
            self.assertEqual((first/'seed-0003/layers.jsonl').read_bytes(), (second/'seed-0003/layers.jsonl').read_bytes())
            before = (first/'trials.jsonl').read_bytes()
            with self.assertRaises(FileExistsError): run_batch(REFORM, first, [3])
            self.assertEqual((first/'trials.jsonl').read_bytes(), before)

    def test_prose_material_has_exact_speaker_statements_and_rejects_modified_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'episode'
            run_batch(REFORM, target, [4])
            folder = target/'seed-0004'
            material = scene_material(folder)
            self.assertEqual(material['final_state']['state']['婚姻制度'], '存続')
            self.assertTrue(all(e['statement'] for e in material['events']))
            ledger = folder/'layers.jsonl'
            ledger.write_text(ledger.read_text(encoding='utf-8').replace('真壁', '別人'), encoding='utf-8')
            with self.assertRaises(ValueError): scene_material(folder)


if __name__ == '__main__':
    unittest.main()
