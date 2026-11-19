from copy import deepcopy
import json
import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from execution.provenance import ConfigError
from execution.world_drafts import mock_response
from execution.refinement_obstacles import apply_requirement, route_identity, validate_transition
from execution.refinement_contract import compile_plan, validate_candidate

ROOT = Path(__file__).resolve().parents[1]


class ObstacleTests(unittest.TestCase):
    def setUp(self):
        self.base = json.loads(mock_response('Test', 'A delivery story'))
        self.candidate = deepcopy(self.base)
        self.route = self.candidate['routes'][0]
        self.target = route_identity(self.route['from'], self.route['to'])
        entries = {p['entry'] for p in self.base['people']}
        self.candidate['items'].append({'name': 'GateKey', 'holders': [], 'acquisition': {
            'sources': [{'type': 'investigate', 'zone': zone, 'count': 1, 'max': 1} for zone in entries]}})

    def gate(self, value='GateKey', target=None, touched=None):
        return apply_requirement(self.candidate, {'target': target or self.target,
            'values': {'requires_item': value}}, set() if touched is None else touched)

    def plan(self):
        return {'intent':'Add one attainable route gate', 'unsupported':[], 'clarification':[], 'operations':[
            {'id':'key','kind':'item.add','target':'','values':deepcopy(self.candidate['items'][-1]),
             'depends_on':[],'reason':'Provide a deterministic entry acquisition source'},
            {'id':'gate','kind':'route.set_requirement','target':self.target,'values':{'requires_item':'GateKey'},
             'depends_on':['key'],'reason':'Require the key on the selected directed route'}]}

    def test_compile_plan_dependency_and_selected_target(self):
        before = deepcopy(self.base)
        candidate, changes = compile_plan(self.base, self.plan(), preset='obstacles', targets=[self.target])
        self.assertEqual(candidate['routes'][0]['requires_item'], 'GateKey')
        self.assertEqual([row['kind'] for row in changes], ['item.add','route.set_requirement'])
        self.assertEqual(self.base, before)
        validate_candidate(SimpleNamespace(repo=ROOT), {'draft':self.base}, candidate)
        with self.assertRaises(ConfigError):
            compile_plan(self.base, self.plan(), preset='obstacles', targets=['unselected'])

    def test_compile_plan_missing_dependency_is_atomic(self):
        before = deepcopy(self.base)
        plan = self.plan()
        plan['operations'][1]['depends_on'] = []
        with self.assertRaises(ConfigError): compile_plan(self.base, plan)
        self.assertEqual(self.base, before)

    def test_compile_plan_topologically_orders_gate_after_key(self):
        plan = self.plan()
        plan['operations'].reverse()
        candidate, changes = compile_plan(self.base, plan)
        self.assertEqual(candidate['routes'][0]['requires_item'], 'GateKey')
        self.assertEqual([row['operation_id'] for row in changes], ['key','gate'])

    def test_noncanonical_created_name_cannot_bypass_dependency(self):
        plan = self.plan()
        plan['operations'][0]['values']['name'] = ' GateKey '
        plan['operations'][1]['depends_on'] = []
        before = deepcopy(self.base)
        with self.assertRaises(ConfigError): compile_plan(self.base, plan)
        self.assertEqual(self.base, before)

    def test_set_requirement_cannot_target_route_created_in_same_plan(self):
        base = deepcopy(self.base)
        added_route = base['routes'].pop(0)
        plan = self.plan()
        plan['operations'].insert(1, {'id':'new_route','kind':'route.add','target':'',
                                     'values':added_route,'depends_on':[], 'reason':'Create route'})
        plan['operations'][-1]['depends_on'].append('new_route')
        before = deepcopy(base)
        with self.assertRaises(ConfigError): compile_plan(base, plan)
        self.assertEqual(base, before)

    def test_obstacle_preset_rejects_unrelated_changes(self):
        for kind, target, values in [
                ('place.update_description', self.base['places'][0]['name'], {'description':'Changed'}),
                ('item.set_holders', self.base['items'][0]['name'], {'holders':[]}),
                ('route.add', '', {'from':'A','to':'B'})]:
            plan = self.plan()
            plan['operations'].append({'id':'extra','kind':kind,'target':target,'values':values,
                                       'depends_on':[],'reason':'Unrelated request'})
            with self.subTest(kind=kind), self.assertRaises(ConfigError):
                compile_plan(self.base, plan, preset='obstacles')
        with self.assertRaises(ConfigError): compile_plan(self.base, self.plan(), preset='add_item')

    def test_persisted_validation_rejects_gate_removal_before_world_validation(self):
        candidate, _ = compile_plan(self.base, self.plan())
        base = deepcopy(candidate)
        candidate['routes'][0].pop('requires_item')
        with patch('execution.world_drafts.validate', side_effect=AssertionError('Must reject first')):
            with self.assertRaises(ConfigError): validate_candidate(SimpleNamespace(repo=ROOT), {'draft':base}, candidate)

    def test_service_preview_and_atomic_idempotent_apply(self):
        from execution.world_drafts import DraftStore
        from execution.world_refinements import Refinements
        with tempfile.TemporaryDirectory() as folder:
            store = DraftStore(ROOT, Path(folder), mock_response, 'mock')
            token, request = uuid4().hex, uuid4().hex
            record = store.create(token, 'Test', 'A delivery story')
            calls = []
            def planner(*args):
                calls.append(args)
                return self.plan()
            service = Refinements(store, planner)
            preview = service.preview(token, record['revision'], request, mode='preset',
                                      preset='obstacles', prompt='Add an attainable key gate', targets=[self.target])
            self.assertEqual(preview['state'], 'ready', preview)
            self.assertEqual(store.get(token)['draft'], record['draft'])
            self.assertEqual(service.get(token, request), preview)
            self.assertIn('simulation_not_tested', preview['warnings'])
            with self.assertRaises(ConfigError):
                service.apply(token, request, record['revision'], preview['candidate_id'], preview['candidate_hash'])
            self.assertEqual(store.get(token)['draft'], record['draft'])
            applied = service.apply(token, request, record['revision'], preview['candidate_id'], preview['candidate_hash'],
                                    acknowledged_warnings=preview['warnings'])
            self.assertEqual(applied['draft'], preview['candidate'])
            self.assertEqual(service.apply(token, request, record['revision'], preview['candidate_id'], preview['candidate_hash'],
                                          acknowledged_warnings=preview['warnings']), applied)
            self.assertEqual(len(calls), 1)

    def test_service_invalid_acquisition_never_adopts_partial_item(self):
        from execution.world_drafts import DraftStore
        from execution.world_refinements import Refinements
        plan = self.plan()
        plan['operations'][0]['values']['acquisition']['sources'] = []
        with tempfile.TemporaryDirectory() as folder:
            store = DraftStore(ROOT, Path(folder), mock_response, 'mock')
            token, request = uuid4().hex, uuid4().hex
            record = store.create(token, 'Test', 'A delivery story')
            service = Refinements(store, lambda *args: deepcopy(plan))
            result = service.preview(token, record['revision'], request, mode='preset', preset='obstacles', prompt='Gate')
            self.assertEqual(result['state'], 'unsupported', result)
            self.assertIsNone(result['candidate'])
            self.assertEqual(store.get(token)['draft'], record['draft'])

    def test_entry_acquisition_runtime_witness_is_pure(self):
        original = deepcopy(self.base)
        before, after = self.gate()
        self.assertNotIn('requires_item', before)
        self.assertEqual(after['requires_item'], 'GateKey')
        candidate_before = deepcopy(self.candidate)
        evidence = validate_transition(self.base, self.candidate)
        self.assertEqual(len(evidence), len(self.base['people']))
        self.assertTrue(all(row['method'] == 'entry_investigate' for row in evidence))
        self.assertEqual(self.base, original)
        self.assertEqual(self.candidate, candidate_before)

    def test_initial_possession_needs_no_acquisition(self):
        item = self.candidate['items'][-1]
        item.pop('acquisition')
        item['holders'] = [{'person': p['name'], 'count': 1} for p in self.base['people']]
        self.gate()
        self.assertTrue(all(row['steps'] == 0 for row in validate_transition(self.base, self.candidate)))

    def test_identity_is_unambiguous_directional_and_canonical(self):
        self.assertNotEqual(route_identity('A,B', 'C'), route_identity('A', 'B,C'))
        self.assertNotEqual(route_identity('A', 'B'), route_identity('B', 'A'))
        for target in ['0', json.dumps([self.route['from'], self.route['to']]), '["missing","route"]']:
            with self.subTest(target=target), self.assertRaises(ConfigError):
                self.gate(target=target)

    def test_unknown_item_null_and_wrong_type_rejected(self):
        for value in [None, '', 1, [], {}, 'missing']:
            with self.subTest(value=value), self.assertRaises(ConfigError): self.gate(value)

    def test_overlapping_writes_rejected(self):
        touched = set()
        self.gate(touched=touched)
        with self.assertRaises(ConfigError): self.gate(touched=touched)

    def test_cannot_replace_existing_requirement(self):
        self.route['requires_item'] = self.base['items'][0]['name']
        with self.assertRaises(ConfigError): self.gate()

    def test_persisted_candidate_cannot_relax_gate(self):
        self.base['routes'][0]['requires_item'] = self.base['items'][0]['name']
        for value in [None, 'GateKey']:
            with self.subTest(value=value):
                if value is None: self.route.pop('requires_item', None)
                else: self.route['requires_item'] = value
                with self.assertRaises(ConfigError): validate_transition(self.base, self.candidate)

    def test_cannot_remove_route_or_change_cost(self):
        for change in ['remove', 'cost']:
            candidate = deepcopy(self.candidate)
            if change == 'remove': candidate['routes'].pop(0)
            else: candidate['routes'][0]['cost'] = 99
            with self.subTest(change=change), self.assertRaises(ConfigError): validate_transition(self.base, candidate)

    def test_acquisition_cannot_be_behind_gate_or_another_actor(self):
        self.gate()
        for sources in [[], [{'type':'investigate', 'agent':self.base['people'][0]['name']}],
                        [{'type':'investigate', 'zone':self.base['people'][0]['entry'], 'count':2}],
                        [{'type':'investigate', 'zone':self.base['people'][0]['entry'], 'max':0}]]:
            self.candidate['items'][-1]['acquisition']['sources'] = sources
            with self.subTest(sources=sources), self.assertRaises(ConfigError): validate_transition(self.base, self.candidate)

    def policy(self, enabled=None, constraints=None, stamina=False):
        self.candidate['settings'] = {'version':1, 'time':{'days':7,'slots':['morning','night']},
            'action_usage':{'version':2,'engine_contract':'normal_turn_v2','catalog_version':1,
                            'enabled':enabled or ['verb:rest','verb:move','verb:investigate'],
                            'constraints':constraints or {}},
            'mechanics':{'version':1,'stamina_management':stamina,'budgeted_purchases':False}}

    def test_disabled_and_actor_or_initial_slot_restrictions(self):
        self.gate()
        cases = [(['verb:rest','verb:move'], {}), (None, {'verb:investigate':{'actors':[],'slots':None}}),
                 (None, {'verb:investigate':{'actors':None,'slots':['night']}})]
        for enabled, constraints in cases:
            self.policy(enabled, constraints)
            with self.subTest(constraints=constraints), self.assertRaises(ConfigError): validate_transition(self.base, self.candidate)

    def test_initial_slot_permission_is_sufficient(self):
        self.gate()
        self.policy(constraints={'verb:investigate':{'actors':None,'slots':['morning']}})
        self.assertTrue(validate_transition(self.base, self.candidate))

    def test_stamina_scheduled_and_short_time_outside_proof(self):
        self.gate()
        self.policy(stamina=True)
        with self.assertRaises(ConfigError): validate_transition(self.base, self.candidate)
        self.policy()
        self.candidate['settings']['scheduled_events'] = []
        with self.assertRaises(ConfigError): validate_transition(self.base, self.candidate)
        self.policy()
        self.candidate['settings']['time'] = {'days':1,'slots':['only']}
        with self.assertRaises(ConfigError): validate_transition(self.base, self.candidate)

    def test_combined_holder_removal_cannot_lose_old_reachability(self):
        old_key = self.base['items'][0]
        old_key.pop('holder', None)
        old_key['holders'] = [{'person':p['name'],'count':1} for p in self.base['people']]
        self.base['routes'][1]['requires_item'] = old_key['name']
        self.candidate['routes'][1]['requires_item'] = old_key['name']
        self.candidate['items'][0].pop('holder', None)
        self.candidate['items'][0]['holders'] = []
        self.gate()
        with self.assertRaises(ConfigError): validate_transition(self.base, self.candidate)

    def test_gated_new_route_uses_same_acquisition_guard(self):
        self.base['routes'].pop(0)
        self.route['requires_item'] = 'GateKey'
        self.assertTrue(validate_transition(self.base, self.candidate))
        self.candidate['items'][-1]['acquisition']['sources'] = []
        with self.assertRaises(ConfigError): validate_transition(self.base, self.candidate)


if __name__ == '__main__':
    unittest.main()
