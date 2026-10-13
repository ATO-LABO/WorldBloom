"""State/knowledge-dependent selection, no forced route, and source provenance."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.actions import Action
from engine.sim import Simulation
from gapengine.festival_policy import build_policies
from scripts.cultural_festival_probe import load, source_manifest
from scripts.cultural_festival_state_search import run
from scripts.cultural_festival_trial import run_trial


class StatePolicyTests(unittest.TestCase):
    def fixture(self):
        world, people = load()
        sim = Simulation(0, world, people, Path('/tmp/unused'))
        policies = build_policies(world.slot_contract)
        for person in people.values():
            person.slot_context = dict(own_practice_completed=0,own_costume_prepared=False,
                own_requests=[],own_responses=[],perceptions=[])
        return world,people,sim,policies

    def test_hidden_other_state_cannot_change_scores(self):
        w,p,sim,policies = self.fixture()
        person = p['蒼']
        offered = sim.slot_runtime.candidates(person)
        first = policies['蒼'].reweight(person,w,[person],deepcopy(offered),turn=1,day=1)
        p['蓮'].zone = '展示室'
        p['灯'].zone = '部室'
        p['蓮'].knowledge.add('音響手順')
        sim.slot_runtime.state['sound_checked'] = True
        second = policies['蒼'].reweight(person,w,[person],deepcopy(offered),turn=1,day=1)
        self.assertEqual([(a.verb,a.args,wt) for a,wt in first],[(a.verb,a.args,wt) for a,wt in second])
        self.assertEqual(policies['蒼'].audits[0]['basis'],policies['蒼'].audits[1]['basis'])

    def test_prepared_actor_has_no_unsupported_decline_weight(self):
        w,p,sim,policies = self.fixture()
        person = p['蒼']
        person.slot_context.update(own_practice_completed=3,own_costume_prepared=True,
            own_requests=[dict(id='req',to='蒼',**{'from':'紗季'},topic='出演',completed_t=16)])
        offered=[(Action('wait'),1),(Action('dialogue',('紗季','出演','decline','contact'),{'request_id':'req'}),1),
                 (Action('dialogue',('紗季','出演','accept','contact'),{'request_id':'req'}),1)]
        chosen=policies['蒼'].reweight(person,w,[person],offered,turn=18,day=1)
        self.assertFalse(any(a.verb=='dialogue' and a.args[2]=='decline' for a,_ in chosen))
        self.assertTrue(any(a.verb=='dialogue' and a.args[2]=='accept' for a,_ in chosen))

    def test_received_acceptance_stops_repeating_line_request(self):
        w,p,sim,policies = self.fixture()
        person=p['蓮']
        person.slot_context['perceptions']=[dict(kind='speech',subject='蒼',to='蓮',topic='終盤短縮希望',intent='accept',t=2)]
        weighted=sim.slot_runtime.candidates(person)
        candidates=policies['蓮'].reweight(person,w,[person],weighted,turn=9,day=1)
        self.assertFalse(any(a.verb=='dialogue' and a.args[0]=='蒼' and a.args[1]=='終盤短縮希望' for a,_ in candidates))

    def test_15_o_clock_does_not_force_movement(self):
        w,p,sim,policies=self.fixture()
        person=p['蒼'];person.zone='展示室'
        person.slot_context.update(own_practice_completed=3,own_costume_prepared=True)
        candidates=policies['蒼'].reweight(person,w,[person],sim.slot_runtime.candidates(person),turn=19,day=1)
        verbs={a.verb for a,_ in candidates}
        self.assertIn('move',verbs);self.assertIn('wait',verbs);self.assertIn('exhibit_help',verbs)
        self.assertGreater(len(candidates),1)

    def test_declared_purposes_participate_in_scores(self):
        from gapengine.festival_policy import FestivalStatePolicy
        w,p,sim,policies=self.fixture()
        person=p['蒼']
        offered=sim.slot_runtime.candidates(person)
        policies['蒼'].reweight(person,w,[person],deepcopy(offered),turn=1,day=1)
        # Counterfactual scoring fixture only; candidate runs never change goals.
        no_goals=FestivalStatePolicy(role='lead',goals=[],public=policies['蒼'].public,fixed_traits=person.traits)
        candidates=no_goals.reweight(person,w,[person],deepcopy(offered),turn=1,day=1)
        self.assertFalse(any(a.verb=='practice' for a,_ in candidates))
        self.assertFalse(no_goals.audits[-1]['basis']['goal_potentials'])

    def test_partner_rehearses_after_received_line_acceptance(self):
        w,p,sim,policies=self.fixture()
        person=p['蓮'];person.slot_context['own_practice_completed']=3
        person.slot_context['perceptions']=[
            dict(kind='activity',subject='蓮',verb='practice',t=1),
            dict(kind='speech',subject='蒼',to='蓮',topic='終盤短縮希望',intent='accept',t=2)]
        candidates=policies['蓮'].reweight(person,w,[person],sim.slot_runtime.candidates(person),turn=4,day=1)
        action,weight=next((a,wt) for a,wt in candidates if a.verb=='practice')
        self.assertIn('rehearse_after_agreed_line_change',action.meta['decision_basis']['selected']['components'])
        self.assertTrue(policies['蓮'].audits[-1]['basis']['needs_rehearsal_after_line_agreement'])

    def test_small_native_run_audit_and_replay(self):
        sources=source_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            first=run(root/'first',0);second=run(root/'second',0)
            self.assertEqual(first['native_layers_sha256'],second['native_layers_sha256'])
            self.assertEqual(first['source_manifest'],sources)
            self.assertEqual(first['audit']['failures'],[])
            self.assertEqual(first['audit']['decision_checks'],88)
            self.assertFalse(first['ga_evolution']);self.assertEqual(first['jev_calls'],0)
            for name in ('蒼','紗季','蓮','灯'):
                audits=json.loads((root/'first'/f'decision-audit-{name}.json').read_text())
                self.assertEqual(len(audits),22)
                self.assertTrue(all(a['positive_count']>1 for a in audits))

    def test_candidate07_unanswered_is_not_pattern0_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run_trial(Path(tmp),search=True,seed=7)
        self.assertIsNone(result['opening']['declines_to_perform'])
        self.assertFalse(result['pattern0_pass'])
        self.assertEqual(result['classification'],'在場・出演回答未了')
        self.assertEqual(result['source_manifest'],source_manifest())

    def test_state_run_needs_neither_network_nor_authored_policy(self):
        with tempfile.TemporaryDirectory() as tmp, patch('socket.socket.connect',side_effect=AssertionError('network forbidden')), \
             patch('scripts.cultural_festival_trial.FestivalPolicy.reweight',side_effect=AssertionError('PLANS forbidden')):
            result=run(Path(tmp),1)
        self.assertEqual(result['audit']['failures'],[])


if __name__=='__main__':
    unittest.main()
