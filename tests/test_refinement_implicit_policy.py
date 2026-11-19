"""Implicit draft policies must be resolved, never guessed or broadened."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from execution.provenance import ConfigError
from execution.refinement_contract import compile_plan, validate_candidate
from execution.refinement_temporal_policy import materialize, permissions, runtime, transition_baseline
from execution.world_drafts import mock_response

ROOT = Path(__file__).resolve().parents[1]

class ImplicitPolicyTests(unittest.TestCase):
    def setUp(self):
        self.base = json.loads(mock_response('Test', 'A delivery story'))
        self.base['settings'] = {'version':1, 'time':{'days':7,'slots':['morning','night']}}
        self.actor = self.base['people'][0]['name']

    def restrict(self, target='verb:move', values=None, base=None):
        op={'id':'restrict','kind':'action.set_constraints','target':target,
            'values':values if values is not None else {'slots':['night']},
            'depends_on':[],'reason':'Explicit restriction'}
        return compile_plan(base or self.base, {'intent':'Restrict an action','operations':[op],
                                               'unsupported':[],'clarification':[]})[0]

    def test_materialization_exactly_matches_every_actor_action_slot(self):
        before=runtime(self.base); candidate=deepcopy(self.base)
        materialize(candidate); after=runtime(candidate)
        self.assertEqual(before.action_usage,after.action_usage)
        self.assertEqual(before.mechanics,after.mechanics)
        self.assertEqual(permissions(before),permissions(after))
        from engine.action_usage import references
        self.assertEqual(len(permissions(before)),len(before.subjects)*len(references(before))*len(before.slots))
        self.assertEqual(after.mechanics,{'version':1,'stamina_management':False,'budgeted_purchases':False})
        self.assertNotIn('action_usage',self.base['settings'])

    def test_actual_candidate_generation_unchanged_before_restriction(self):
        from engine.actions import candidates
        before=runtime(self.base); candidate=deepcopy(self.base);materialize(candidate);after=runtime(candidate)
        for actor in before.subjects:
            for turn in range(1,len(before.slots)+1):
                context=SimpleNamespace(turn=turn,day=1)
                def rows(world):
                    return [(a.verb,a.args,weight) for a,weight in candidates(world.subjects[actor],world,context)]
                self.assertEqual(rows(before),rows(after))

    def test_restriction_reduces_only_selected_permission(self):
        original=deepcopy(self.base);candidate=self.restrict();before=runtime(self.base);after=runtime(candidate)
        old,new=permissions(before),permissions(after)
        changed={key for key in old if old[key]!=new[key]}
        self.assertEqual(changed,{(actor,'verb:move','morning') for actor in before.subjects})
        self.assertTrue(all(not value or old[key] for key,value in new.items()))
        self.assertEqual(before.action_usage['enabled'],after.action_usage['enabled'])
        self.assertEqual(before.mechanics,after.mechanics)
        self.assertEqual(self.base,original)
        validate_candidate(SimpleNamespace(repo=ROOT),{'draft':self.base},candidate)

    def test_actor_restriction_does_not_grant_new_verbs(self):
        candidate=self.restrict(values={'actors':[self.actor]})
        self.assertEqual(runtime(candidate).subjects[self.actor].verbs,runtime(self.base).subjects[self.actor].verbs)
        with self.assertRaises(ConfigError):self.restrict('verb:train',{'actors':[self.actor]})

    def test_required_rest_optional_actions_and_noop_rejected(self):
        for target,values in [('verb:rest',{'actors':[]}),('verb:start_relationship',{'actors':[self.actor]}),('verb:move',{'slots':None}),('verb:move',{'actors':None})]:
            with self.subTest(target=target,values=values),self.assertRaises(ConfigError):self.restrict(target,values)

    def test_materialized_mechanics_tampering_rejected(self):
        candidate=self.restrict()
        candidate['settings']['mechanics']['stamina_management']=True
        with self.assertRaises(ConfigError):transition_baseline(self.base,candidate)

    def test_materialized_enabled_actions_tampering_rejected(self):
        for change in ('add','remove'):
            candidate=self.restrict();enabled=candidate['settings']['action_usage']['enabled']
            if change=='add':enabled.append('verb:start_relationship')
            else:enabled.remove('verb:observe')
            with self.assertRaises(ConfigError):transition_baseline(self.base,candidate)

    def test_implicit_mechanics_override_is_copied_exactly(self):
        self.base['settings']['mechanics']={'version':1,'stamina_management':True,'budgeted_purchases':False}
        candidate=self.restrict()
        self.assertEqual(candidate['settings']['mechanics'],self.base['settings']['mechanics'])
        self.assertEqual(runtime(candidate).mechanics,runtime(self.base).mechanics)

    def test_no_settings_and_repeat_restriction(self):
        self.base.pop('settings')
        actor_only=self.restrict(values={'actors':[self.actor]})
        first_slot=runtime(actor_only).slots[0]
        candidate=self.restrict(values={'slots':[first_slot]},base=actor_only)
        self.assertEqual(candidate['settings']['action_usage']['constraints']['verb:move'],{'actors':[self.actor],'slots':[first_slot]})

    def test_slot_catalog_change_is_rejected_during_materialization(self):
        candidate=self.restrict();candidate['settings']['time']['slots'].append('extra')
        with self.assertRaises(ConfigError):transition_baseline(self.base,candidate)

    def test_existing_recipe_catalog_preserved(self):
        material=self.base['items'][0]['name']
        self.base['items'].append({'name':'crafted token','holders':[],'acquisition':{'sources':[],
            'craft':{'materials':{material:1},'knowledge':None,'zone':None}}})
        candidate=self.restrict()
        self.assertIn('recipe:crafted token',candidate['settings']['action_usage']['enabled'])
        before,after=permissions(runtime(self.base)),permissions(runtime(candidate))
        for key,value in before.items():
            if key[1]=='recipe:crafted token':self.assertEqual(after[key],value)

    def test_goal_and_unrelated_setting_changes_still_rejected(self):
        for change in ('goal','settings'):
            candidate=self.restrict()
            if change=='goal':candidate['people'][0]['goal']={}
            else:candidate['settings']['planning']={}
            with self.assertRaises(ConfigError):validate_candidate(SimpleNamespace(repo=ROOT),{'draft':self.base},candidate)

    def test_failed_equivalence_check_does_not_mutate_input(self):
        original=deepcopy(self.base)
        with patch('execution.refinement_temporal_policy.permissions',side_effect=[{'key':True},{'key':False}]):
            with self.assertRaises(ConfigError):materialize(self.base)
        self.assertEqual(self.base,original)

if __name__=='__main__':unittest.main()
