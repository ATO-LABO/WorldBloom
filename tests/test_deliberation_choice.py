"""Causal controls for situated choices, epistemic scope and saved evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from engine.actions import Action, candidates
from engine.deliberation import Deliberation
from engine.deliberation_choice import choice_basis
from engine.sim import Simulation
from gapengine.deliberation_episode import load_episode, run_batch

ROOT = Path(__file__).resolve().parents[1]
REFORM = ROOT / "projects/reform_hearing/world.yaml"
RESCUE = ROOT / "projects/rescue_council/world.yaml"


class SituatedChoiceTests(unittest.TestCase):
    def prepare(self, project=REFORM, *, seed=0, change=None):
        world, people = load_episode(project)
        if change:
            change(world, people)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        sim = Simulation(seed, world, people, Path(self.temp.name)/"episode")
        runtime = world.deliberation
        spec = runtime.specs["main"]
        actor = spec["proposer"]
        def execute(who, verb, *args, turn=1):
            return sim.verb_engine.execute(people[who], Action(verb, args), turn=turn, day=1)
        self.assertEqual(execute(actor, "submit_proposal", "main", spec["initial"], "0")[0], "applied")
        for who in spec["reviewers"]:
            self.assertEqual(execute(who, "review_proposal", "main", "1")[0], "applied")
        sim.turn, sim.day = 2, 1
        return sim, runtime, spec, actor, execute

    def basis(self, sim, runtime, spec, actor):
        return choice_basis(runtime, sim.world, spec, runtime.records["main"], actor, sim.turn)

    def option(self, basis, alternative):
        return next(o for o in basis["options"] if o["verb"] == "amend_proposal" and o["args"][1] == alternative)

    def test_same_seed_different_stubbornness_changes_real_selected_action_in_two_genres(self):
        for project in [REFORM, RESCUE]:
            for seed in [0, 4]:
                low = self.prepare(project, seed=seed)
                def stubborn(world, people):
                    proposer = world.deliberation.specs['main']['proposer']
                    people[proposer].traits['stubbornness'] = .95
                high = self.prepare(project, seed=seed, change=stubborn)
                # Identical RNG states immediately before actual engine sampling.
                self.assertEqual(low[0].rng.getstate(), high[0].rng.getstate())
                self.assertEqual(low[0].choose_action(low[0].subjects[low[3]])[0].verb, "amend_proposal")
                self.assertEqual(high[0].choose_action(high[0].subjects[high[3]])[0].verb, "request_decision")

    def test_testimony_is_scoped_credited_and_never_silently_becomes_knowledge(self):
        sim, rt, spec, actor, execute = self.prepare()
        self.assertNotIn("移行費用試算", sim.subjects[actor].knowledge)
        basis = self.basis(sim, rt, spec, actor)
        protected = self.option(basis, "protected")
        self.assertTrue(protected['available'])
        self.assertEqual(protected['evidence'][0]['source'], 'heard_testimony')
        self.assertEqual(protected['evidence'][0]['actor'], '有村')
        result, details, _ = execute(actor, "amend_proposal", "main", "protected", "1", turn=2)
        self.assertEqual(result, 'applied')
        self.assertEqual(details['deliberation_event']['choice_basis'], {**basis, 'chosen':{'verb':'amend_proposal','args':['main','protected','1']}})
        self.assertNotIn("移行費用試算", sim.subjects[actor].knowledge)

    def test_low_trust_blocks_unverified_solution_but_firsthand_evidence_unlocks_it(self):
        sim, rt, spec, actor, _ = self.prepare()
        spec['choice_policy']['trust']['有村'] = .1
        unknown = self.option(self.basis(sim, rt, spec, actor), 'protected')
        self.assertFalse(unknown['available'])
        self.assertIn('missing_evidence:移行費用試算', unknown['exclusions'])
        sim.subjects[actor].knowledge.add('移行費用試算')
        known = self.option(self.basis(sim, rt, spec, actor), 'protected')
        self.assertTrue(known['available'])
        self.assertEqual(known['evidence'][0]['source'], 'firsthand')

    def test_same_seed_trust_changes_selection_without_changing_affinity_or_reviewer_votes(self):
        low = self.prepare()
        for who in low[2]['reviewers']:
            low[2]['choice_policy']['trust'][who] = .1
        high = self.prepare()
        self.assertEqual(low[1].records['main']['reviews'], high[1].records['main']['reviews'])
        self.assertEqual(low[0].world.relations.snapshot(), high[0].world.relations.snapshot())
        self.assertEqual(low[0].rng.getstate(), high[0].rng.getstate())
        self.assertEqual(low[0].choose_action(low[0].subjects[low[3]])[0].verb, 'request_decision')
        self.assertEqual(high[0].choose_action(high[0].subjects[high[3]])[0].verb, 'amend_proposal')

    def test_same_seed_firsthand_knowledge_changes_actual_choice_with_trust_held_fixed(self):
        cases = []
        for knows in [False, True]:
            sim, rt, spec, actor, _ = self.prepare(seed=4)
            spec['choice_policy']['trust'] = {who:.1 for who in spec['reviewers']}
            if knows:
                sim.subjects[actor].knowledge.add('移行費用試算')
            cases.append((sim, actor))
        self.assertEqual(cases[0][0].rng.getstate(), cases[1][0].rng.getstate())
        chosen=[sim.choose_action(sim.subjects[actor])[0].verb for sim,actor in cases]
        self.assertEqual(chosen, ['request_decision','amend_proposal'])

    def test_hidden_other_profiles_and_private_knowledge_do_not_affect_choice_basis(self):
        sim, rt, spec, actor, _ = self.prepare()
        before = self.basis(sim, rt, spec, actor)
        spec['profiles']['有村']['weights']['財源']['追加財源'] = -80
        spec['profiles']['有村']['constraints'] = {}
        sim.subjects['有村'].knowledge.clear()
        sim.subjects['有村'].traits['stubbornness'] = .99
        self.assertEqual(before, self.basis(sim, rt, spec, actor))

    def test_unheard_objection_and_citations_are_not_visible_or_actionable(self):
        sim, rt, spec, actor, _ = self.prepare()
        review = rt.records['main']['reviews']['有村']
        review['audience'].remove(actor)
        basis = self.basis(sim, rt, spec, actor)
        self.assertNotIn('有村', [r['speaker'] for r in basis['heard_reviews']])
        self.assertFalse(self.option(basis, 'protected')['available'])
        self.assertFalse(any(v in {'amend_proposal','request_decision'} for v, _, _ in rt.candidates(sim.world, actor, turn=2)))

    def test_choice_computation_is_pure_and_explanation_is_detached(self):
        sim, rt, spec, actor, _ = self.prepare()
        before, rng = rt.snapshot(), sim.rng.getstate()
        basis = self.basis(sim, rt, spec, actor)
        basis['policy']['trust']['有村'] = 0
        basis['heard_reviews'][0]['review']['blockers'].clear()
        self.assertEqual(rt.snapshot(), before)
        self.assertEqual(sim.rng.getstate(), rng)
        self.assertEqual(spec['choice_policy']['trust']['有村'], .9)

    def test_affection_changes_do_not_manufacture_trust_or_assent(self):
        sim, rt, spec, actor, _ = self.prepare()
        before = self.basis(sim, rt, spec, actor)
        sim.world.relations.change(actor, '有村', affinity=1)
        self.assertEqual(before, self.basis(sim, rt, spec, actor))
        self.assertEqual(rt.records['main']['reviews']['有村']['stance'], 'oppose')

    def test_deadline_is_used_by_engine_candidates_and_rechecked_atomically_at_execution(self):
        sim, rt, spec, actor, execute = self.prepare()
        spec['choice_policy']['amendment_deadline'] = 4
        self.assertTrue(any(a.verb == 'amend_proposal' for a, _ in candidates(sim.subjects[actor], sim.world, sim)))
        before = rt.snapshot()
        sim.turn = 3
        self.assertFalse(any(a.verb == 'amend_proposal' for a, _ in candidates(sim.subjects[actor], sim.world, sim)))
        self.assertEqual(execute(actor, 'amend_proposal','main','protected','1',turn=3)[0], 'invalid')
        self.assertEqual(rt.snapshot(), before)
        self.assertIn('insufficient_revision_time', self.option(self.basis(sim, rt, spec, actor), 'protected')['exclusions'])

    def test_own_nonnegotiable_values_and_compromise_budget_are_hard_limits(self):
        sim, rt, spec, actor, _ = self.prepare()
        spec['choice_policy']['compromise_limit'] = 0
        self.assertTrue(self.option(self.basis(sim, rt, spec, actor), 'protected')['available'])
        self.assertIn('compromise_limit_exceeded', self.option(self.basis(sim, rt, spec, actor), 'staged')['exclusions'])
        spec['profiles'][actor]['constraints']['移行時期'] = ['段階']
        self.assertIn('own_conditions_not_met', self.option(self.basis(sim, rt, spec, actor), 'protected')['exclusions'])

    def test_diligence_and_goal_priority_change_weights_not_actual_votes(self):
        sim, rt, spec, actor, _ = self.prepare()
        before = rt.snapshot()
        initial = self.option(self.basis(sim, rt, spec, actor), 'protected')['weight']
        sim.subjects[actor].traits['diligence'] = 0
        diligent_low = self.option(self.basis(sim, rt, spec, actor), 'protected')['weight']
        spec['choice_policy']['goal_priority'] = 0
        no_goal = self.option(self.basis(sim, rt, spec, actor), 'protected')['weight']
        self.assertGreater(initial, diligent_low)
        self.assertGreater(diligent_low, no_goal)
        self.assertEqual(rt.snapshot(), before)

    def test_addressing_public_blockers_does_not_guarantee_future_assent(self):
        sim, rt, spec, actor, execute = self.prepare()
        # A private evaluation of a not-yet-presented term must not be consulted
        # by the proposer. The original review is still fresh and unchanged.
        spec['profiles'][spec['authority']]['weights']['移行保障']['公費保障'] = -4
        self.assertTrue(self.option(self.basis(sim, rt, spec, actor), 'protected')['available'])
        self.assertEqual(execute(actor,'amend_proposal','main','protected','1',turn=2)[0],'applied')
        for who in spec['reviewers']:
            self.assertEqual(execute(who,'review_proposal','main','2',turn=2)[0],'applied')
        self.assertEqual(rt.records['main']['reviews'][spec['authority']]['stance'],'oppose')
        self.assertEqual(execute(actor,'request_decision','main','2',turn=3)[0],'applied')
        self.assertEqual(execute(spec['authority'],'decide_proposal','main','2',turn=3)[0],'applied')
        self.assertTrue(rt.decided('main','rejected'))

    def test_no_policy_preserves_legacy_deliberation_weights(self):
        def legacy(world, people):
            del world.deliberation.specs['main']['choice_policy']
        sim, rt, spec, actor, _ = self.prepare(change=legacy)
        offered = {(v, a[1] if v=='amend_proposal' else ''):w for v,a,w in rt.candidates(sim.world, actor, turn=2)}
        self.assertEqual(offered, {('request_decision',''):4.0,('amend_proposal','protected'):3.4,('amend_proposal','staged'):3.0})

    def test_malformed_policy_and_unknown_fact_fail_closed(self):
        world, people = load_episode(REFORM)
        raw = world.deliberation.config
        mutations = [lambda p:p.update(goal_priority=True),lambda p:p.update(goal_priority=float('nan')),
                     lambda p:p.update(evidence_threshold=1.1),lambda p:p.update(compromise_limit=-1),
                     lambda p:p.update(amendment_deadline=True),lambda p:p.update(revision_steps=0),
                     lambda p:p.update(trust={'nonexistent':.9}),lambda p:p.update(trust={'有村':float('inf')}),
                     lambda p:p.update(alternative_requires={'unknown':['移行費用試算']}),
                     lambda p:p.update(alternative_requires={'protected':['x','x']})]
        for mutate in mutations:
            changed=deepcopy(raw); mutate(changed['proposals'][0]['choice_policy'])
            with self.assertRaises(ValueError): Deliberation(changed)
        changed=deepcopy(raw); changed['proposals'][0]['choice_policy']['alternative_requires']['protected']=['unknown']
        world.deliberation=Deliberation(changed)
        with self.assertRaises(ValueError): world.bind_subjects(people)

    def test_invalid_runtime_trait_does_not_enter_sampling(self):
        for value in [float('nan'),float('inf'),-1,2,True]:
            def invalid(world, people): people['真壁'].traits['stubbornness']=value
            with self.assertRaises(ValueError): self.prepare(change=invalid)

    def test_non_mapping_proposal_and_null_policy_raise_validation_error(self):
        world, _ = load_episode(REFORM)
        for invalid in [None, 1, [], 'proposal']:
            changed = deepcopy(world.deliberation.config)
            changed['proposals'] = [invalid]
            with self.assertRaises(ValueError): Deliberation(changed)
        changed = deepcopy(world.deliberation.config)
        changed['proposals'][0]['choice_policy'] = None
        with self.assertRaises(ValueError): Deliberation(changed)

    def test_saved_episode_contains_actual_basis_and_probability_in_two_genres(self):
        with tempfile.TemporaryDirectory() as temp:
            for project in [REFORM, RESCUE]:
                folder=Path(temp)/project.parent.name
                run_batch(project, folder, [0])
                rows=[json.loads(l) for l in (folder/'seed-0000/layers.jsonl').read_text(encoding='utf8').splitlines()]
                choices=[r for r in rows if r.get('verb') in {'amend_proposal','request_decision'}]
                self.assertTrue(choices)
                for row in choices:
                    basis=row['details']['deliberation_event']['choice_basis']
                    self.assertEqual(basis['mechanism'],'situated_proposer_v1')
                    self.assertEqual(basis['chosen'], {'verb':row['verb'],'args':row['args']})
                    self.assertEqual(basis['turn'],row['turn'])
                    self.assertGreater(row['choice_prob'],0)
                    self.assertLessEqual(row['choice_prob'],1)
                material=json.loads((folder/'seed-0000/scene-material.json').read_text(encoding='utf8'))
                self.assertTrue(any('choice_basis' in e for e in material['events']))


if __name__ == '__main__':
    unittest.main()
