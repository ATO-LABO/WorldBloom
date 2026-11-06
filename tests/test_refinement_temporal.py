from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import unittest
from execution.provenance import ConfigError
from execution.world_drafts import mock_response, validate
from execution.refinement_contract import compile_plan, validate_candidate, VERSION

ROOT = Path(__file__).resolve().parents[1]

class TemporalRefinementTests(unittest.TestCase):
    def setUp(self):
        self.base = json.loads(mock_response('Test', 'A delivery story'))
        self.base['settings'] = {'version':1, 'time':{'days':7,'slots':['morning','night']}}
        self.actor = self.base['people'][0]['name']
        self.place = self.base['places'][0]['name']

    def op(self, kind, values, target='', oid='change', depends=None):
        return {'id':oid,'kind':kind,'values':values,'target':target,'reason':'Explicit user refinement','depends_on':depends or []}

    def compile(self, *ops):
        return compile_plan(self.base, {'intent':'Refine executable settings','operations':list(ops),'unsupported':[],'clarification':[]})[0]

    def event(self, **kw):
        value={'id':'arrival','day':2,'slot':'morning','targets':[self.actor],'move_to':self.place}
        value.update(kw);return value

    def policy(self):
        self.base['settings'].update(action_usage={'version':2,'engine_contract':'normal_turn_v2','catalog_version':1,'enabled':['verb:rest','verb:move','verb:train']}, mechanics={'version':1,'stamina_management':True,'budgeted_purchases':True})

    def test_invalid_time_values_are_atomic(self):
        before=deepcopy(self.base)
        for values in [{'days':True},{'days':0},{'days':10001},{'slots':[]},{'slots':['same','same']}]:
            with self.assertRaises(ConfigError):self.compile(self.op('time.update',values,'time'))
            self.assertEqual(self.base,before)

    def test_absent_time_settings_receive_existing_defaults(self):
        self.base.pop('settings')
        candidate=self.compile(self.op('time.update',{'days':8},'time'))
        validate(ROOT,candidate)
        self.assertEqual(candidate['settings']['time']['days'],8)
        self.assertEqual(len(candidate['settings']['time']['slots']),4)

    def test_time_cannot_remove_constraint_slot(self):
        self.policy()
        self.base['settings']['action_usage']['constraints']={'verb:move':{'actors':None,'slots':['night']}}
        with self.assertRaises(ConfigError):self.compile(self.op('time.update',{'slots':['morning']},'time'))

    def test_add_update_same_event_is_overlapping_write(self):
        with self.assertRaises(ConfigError):self.compile(self.op('schedule.add',self.event(),oid='add'),self.op('schedule.update',{'day':3},'arrival','update',['add']))

    def test_forced_event_edit_remains_unsupported(self):
        event=self.event(force_action={'verb':'rest'})
        self.base['settings']['scheduled_events']=[event]
        with self.assertRaises(ConfigError):self.compile(self.op('schedule.update',{'day':3},'arrival'))

    def test_new_movement_conflicts_with_existing_forced_action(self):
        self.base['settings']['scheduled_events']=[{'id':'forced','day':2,'slot':'morning','targets':[self.actor],'force_action':{'verb':'rest'}}]
        with self.assertRaises(ConfigError):self.compile(self.op('schedule.add',self.event()))
    def test_version(self):
        self.assertEqual(VERSION,'refinement-v3')

    def test_time_update_is_runtime_valid_and_preserves_base(self):
        before=deepcopy(self.base)
        candidate=self.compile(self.op('time.update',{'days':8},'time'))
        validate_candidate(SimpleNamespace(repo=ROOT),{'draft':self.base},candidate)
        self.assertEqual(candidate['settings']['time']['days'],8)
        self.assertEqual(self.base,before)

    def test_time_requires_explicit_target(self):
        with self.assertRaises(ConfigError):self.compile(self.op('time.update',{'days':8}))

    def test_add_and_update_schedule(self):
        candidate=self.compile(self.op('schedule.add',self.event()))
        validate(ROOT,candidate)
        self.base=candidate
        updated=self.compile(self.op('schedule.update',{'day':3},'arrival'))
        self.assertEqual(updated['settings']['scheduled_events'][0]['day'],3)

    def test_schedule_bad_shapes_and_references(self):
        for event in [self.event(day=True),self.event(day=8),self.event(slot='bad'),self.event(targets=['unknown']),self.event(targets=[]),self.event(stress_delta=float('inf')),self.event(grants_item={'name':'missing'}),self.event(grants_fact='missing'),self.event(force_action={'verb':'rest'}),self.event(id=['bad'])]:
            with self.subTest(event=event),self.assertRaises(ConfigError):self.compile(self.op('schedule.add',event))

    def test_duplicate_event_id_and_overlapping_effect(self):
        self.base['settings']['scheduled_events']=[self.event()]
        for event in [self.event(),self.event(id='other')]:
            with self.assertRaises(ConfigError):self.compile(self.op('schedule.add',event))

    def test_schedule_effect_required(self):
        event=self.event();event.pop('move_to')
        with self.assertRaises(ConfigError):self.compile(self.op('schedule.add',event))

    def test_time_rejects_invalidating_event(self):
        self.base['settings']['scheduled_events']=[self.event()]
        for values in [{'days':1},{'slots':['night']}]:
            with self.assertRaises(ConfigError):self.compile(self.op('time.update',values,'time'))

    def test_action_restrict_only_preserves_other_policy(self):
        self.policy();before=deepcopy(self.base)
        candidate=self.compile(self.op('action.set_constraints',{'actors':[self.actor],'slots':['night']},'verb:move'))
        validate(ROOT,candidate)
        self.assertEqual(candidate['settings']['mechanics'],before['settings']['mechanics'])
        self.assertEqual(candidate['settings']['action_usage']['enabled'],before['settings']['action_usage']['enabled'])
        self.assertEqual(self.base,before)

    def test_action_rejects_expansion_and_rest(self):
        self.policy()
        for target,values in [('verb:rest',{'slots':['night']}),('verb:train',{'actors':[self.actor]}),('verb:move',{'slots':[]}),('verb:move',{'actors':['unknown']})]:
            with self.subTest(target=target,values=values),self.assertRaises(ConfigError):self.compile(self.op('action.set_constraints',values,target))
        self.base['settings']['action_usage']['constraints']={'verb:move':{'actors':[],'slots':['night']}}
        for values in [{'actors':None},{'slots':None},{'slots':['morning','night']}]:
            with self.assertRaises(ConfigError):self.compile(self.op('action.set_constraints',values,'verb:move'))

    def test_constraints_materialize_implicit_policy(self):
        candidate=self.compile(self.op('action.set_constraints',{'actors':[]},'verb:move'))
        validate(ROOT,candidate)
        self.assertEqual(candidate['settings']['action_usage']['constraints']['verb:move']['actors'],[])

    def test_protected_settings_and_person_values(self):
        for mutate in [lambda c:c['settings'].update(planning={}),lambda c:c['people'][0].update(entry='elsewhere'),lambda c:c['people'][0].update(goal={}),lambda c:c['people'][0].update(commitments=[]),lambda c:c['settings']['time'].update(presentation={'calendar':'none'})]:
            candidate=deepcopy(self.base);mutate(candidate)
            with self.assertRaises(ConfigError):validate_candidate(SimpleNamespace(repo=ROOT),{'draft':self.base},candidate)

    def test_overlapping_time_write(self):
        with self.assertRaises(ConfigError):self.compile(self.op('time.update',{'days':8},'time','a'),self.op('time.update',{'days':9},'time','b'))

    def test_schedule_new_item_requires_dependency(self):
        item=self.op('item.add',{'name':'New token','holder':self.actor},oid='item')
        event=self.event(grants_item={'name':'New token'})
        with self.assertRaises(ConfigError):self.compile(item,self.op('schedule.add',event,oid='event'))
        candidate=self.compile(item,self.op('schedule.add',event,oid='event',depends=['item']))
        validate(ROOT,candidate)

    def test_event_removal_and_policy_changes_rejected(self):
        self.policy();self.base['settings']['scheduled_events']=[self.event()]
        for mutate in [lambda c:c['settings'].update(scheduled_events=[]),lambda c:c['settings']['action_usage']['enabled'].append('verb:observe')]:
            candidate=deepcopy(self.base);mutate(candidate)
            with self.assertRaises(ConfigError):validate_candidate(SimpleNamespace(repo=ROOT),{'draft':self.base},candidate)

if __name__ == '__main__':unittest.main()
