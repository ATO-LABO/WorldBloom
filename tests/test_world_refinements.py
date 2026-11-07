from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch
from execution.provenance import ConfigError, atomic_json
from execution.world_drafts import DraftStore, mock_response
from execution.world_refinements import Refinements
from execution.refinement_contract import compile_plan, digest
ROOT = Path(__file__).resolve().parents[1]

class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = DraftStore(ROOT, Path(self.tmp.name), mock_response, 'mock')
        self.token = uuid4().hex; self.rid = uuid4().hex
        self.base = self.store.create(self.token, 'Test', 'A delivery story')
        self.calls = 0
        self.plan = {'intent': 'Richer description', 'operations': [self.op()], 'unsupported': [], 'clarification': []}
        def planner(*args): self.calls += 1; return deepcopy(self.plan)
        self.svc = Refinements(self.store, planner)
    def op(self, **kwargs):
        op = {'id': 'profile', 'kind': 'person.update_profile', 'target': self.base['draft']['people'][1]['name'],
              'values': {'description': 'Friendly guide'}, 'depends_on': [], 'reason': 'Richer characterization'}
        op.update(kwargs); return op
    def preview(self, **kwargs):
        args = {'mode': 'prompt', 'prompt': 'Enrich the guide'}; args.update(kwargs)
        return self.svc.preview(self.token, self.base['revision'], self.rid, **args)
    def apply(self, p):
        return self.svc.apply(self.token, self.rid, self.base['revision'], p['candidate_id'], p['candidate_hash'], acknowledged_warnings=p['warnings'])
    def test_proposal_does_not_change_draft(self):
        p = self.preview(); self.assertEqual(p['state'], 'ready'); self.assertEqual(self.store.get(self.token)['draft'], self.base['draft'])
    def test_idempotent_and_get_never_dispatches(self):
        p = self.preview(); self.assertEqual(self.preview(), p); self.svc.get(self.token, self.rid); self.assertEqual(self.calls, 1)
    def test_reused_id_rejected(self):
        self.preview()
        with self.assertRaises(ConfigError): self.preview(prompt='different')
    def test_atomic_apply_and_double_apply(self):
        p = self.preview(); r = self.apply(p); self.assertEqual(r['draft'], p['candidate']); self.assertEqual(self.apply(p), r); self.assertEqual(self.calls, 1)
    def test_cancel_preserves_original(self):
        p = self.preview(); self.svc.cancel(self.token, self.rid)
        with self.assertRaises(ConfigError): self.apply(p)
        self.assertEqual(self.store.get(self.token)['draft'], self.base['draft'])
    def test_late_result_stays_cancelled(self):
        def planner(*args): self.svc.cancel(self.token, self.rid); return self.plan
        self.svc.planner = planner; self.assertEqual(self.preview()['state'], 'cancelled')
    def test_unknown_no_retry(self):
        def planner(*args): self.calls += 1; raise TimeoutError()
        self.svc.planner = planner; self.assertEqual(self.preview()['state'], 'unknown'); self.preview(); self.assertEqual(self.calls, 1)
    def test_stale(self):
        p = self.preview(); r = self.store.get(self.token); r['draft']['initial_story'] += 'manual'; atomic_json(self.store._path(self.token), r)
        with self.assertRaises(ConfigError): self.apply(p)
    def test_hash_and_warning_validation(self):
        p = self.preview()
        with self.assertRaises(ConfigError): self.svc.apply(self.token, self.rid, self.base['revision'], p['candidate_id'], 'wrong', acknowledged_warnings=p['warnings'])
        with self.assertRaises(ConfigError): self.svc.apply(self.token, self.rid, self.base['revision'], p['candidate_id'], p['candidate_hash'])
    def test_cross_category_place_route(self):
        old = self.base['draft']['places'][0]['name']
        self.plan['operations'] += [self.op(id='place', kind='place.add', target='', values={'name':'Harbor','description':'New destination'}), self.op(id='route', kind='route.add', target='', values={'from':old,'to':'Harbor'}, depends_on=['place'])]
        p = self.preview(); self.assertEqual(p['state'], 'ready', p); self.apply(p)
    def test_missing_and_cycle_dependencies(self):
        for deps in [['missing'], ['profile']]:
            plan = deepcopy(self.plan); plan['operations'][0]['depends_on'] = deps
            with self.assertRaises(ConfigError): compile_plan(self.base['draft'], plan)
    def test_unknown_paths_goal_and_unsupported_are_blocked(self):
        for values in [{'goal': {}}, {'../../file':'bad'}, {'entry':'Other'}]:
            plan = deepcopy(self.plan); plan['operations'][0]['values'] = values
            with self.assertRaises(ConfigError): compile_plan(self.base['draft'], plan)
    def test_explicit_unsupported_not_silently_dropped(self):
        self.plan['unsupported'] = ['arbitrary rule']; self.assertEqual(self.preview()['state'], 'unsupported')
    def test_ambiguous_not_ready(self):
        self.plan['clarification'] = ['which sunset?']; self.assertEqual(self.preview()['state'], 'needs_clarification')
    def test_duplicate_name(self):
        self.plan['operations'] = [self.op(kind='place.add', target='', values=self.base['draft']['places'][0])]
        self.assertEqual(self.preview()['state'], 'invalid')
    def test_crash_after_commit_recovers(self):
        import execution.world_refinements as module
        p = self.preview(); real = module.atomic_json
        def write(path, value):
            if value.get('state') == 'applied': raise OSError('receipt disk error')
            return real(path, value)
        with patch.object(module, 'atomic_json', write):
            with self.assertRaises(OSError): self.apply(p)
        result = self.apply(p); self.assertEqual(result['draft'], p['candidate']); self.assertEqual(self.svc.get(self.token, self.rid)['state'], 'applied')
    def test_live_never_bypasses_review(self):
        r = self.store.get(self.token); r['mode'] = 'live'; atomic_json(self.store._path(self.token), r)
        self.assertEqual(self.preview()['state'], 'unavailable')
    def test_preset_scope_and_selected_target(self):
        self.plan['operations'][0]['kind'] = 'place.update_description'
        self.assertEqual(self.preview(mode='preset', preset='enrich_people')['state'], 'unsupported')

    def test_holder_aliases_cannot_overwrite_each_other(self):
        item=self.base['draft']['items'][0]['name'];person=self.base['draft']['people'][0]['name']
        self.plan['operations']=[self.op(id='a',kind='item.set_holders',target=item,values={'holder':person}), self.op(id='b',kind='item.set_holders',target=item,values={'holders':[]})]
        self.assertEqual(self.preview()['state'],'invalid')

    def test_crash_before_commit_can_recover_without_inference(self):
        import execution.world_refinements as module
        p=self.preview();real=module.atomic_json
        def write(path, value):
            if 'last_refinement' in value:raise OSError('draft write interrupted')
            return real(path,value)
        with patch.object(module,'atomic_json',write):
            with self.assertRaises(OSError):self.apply(p)
        self.assertEqual(self.store.get(self.token)['draft'],self.base['draft'])
        self.assertEqual(self.apply(p)['draft'],p['candidate']);self.assertEqual(self.calls,1)
    def test_precommit_recovery_from_invalid_staged_draft(self):
        import execution.world_refinements as module
        token=uuid4().hex
        staged=self.store.stage(token,self.base['name'],self.base['overview'],self.base['draft'])
        self.token=token;self.base=staged;self.base['revision']=staged['edit_revision']
        p=self.preview();self.assertEqual(p['state'],'ready',p);real=module.atomic_json
        def write(path,value):
            if 'last_refinement' in value:raise OSError('precommit')
            return real(path,value)
        with patch.object(module,'atomic_json',write):
            with self.assertRaises(OSError):self.apply(p)
        self.assertEqual(self.apply(p)['draft'],p['candidate'])

    def test_human_origin_requires_semantic_review(self):
        r=self.store.get(self.token);r['mode']='human';atomic_json(self.store._path(self.token),r)
        self.assertEqual(self.preview()['state'],'unavailable')
        self.assertEqual(self.store.get(self.token)['draft'],self.base['draft'])

    def test_save_human_input_without_inference_invalidates_review(self):
        r=self.store.get(self.token);r['mode']='live';atomic_json(self.store._path(self.token),r)
        draft=deepcopy(r['draft']);draft['initial_story']='Human updated story'
        saved=self.svc.save_input(self.token,r['revision'],draft)
        self.assertEqual(saved['draft'],draft);self.assertIsNone(saved['alignment']);self.assertEqual(self.calls,0)
        with self.assertRaises(ConfigError):self.store.adopt(self.token,saved['revision'])
        with self.assertRaises(ConfigError):self.svc.save_input(self.token,r['revision'],draft)

    def test_live_review_receipt_apply_without_extra_calls(self):
        r = self.store.get(self.token); r['mode'] = 'live'; atomic_json(self.store._path(self.token), r)
        reviews = []
        def review(record, inputs, candidate):
            reviews.append(candidate)
            return {'overview_matches':True,'issues':[], 'goals':[{'person':p['name'],'matches':True,'reason':'Preserved'} for p in candidate['people']]}
        self.svc.planner.review = review
        p = self.preview(); self.assertEqual(p['state'], 'ready', p)
        self.assertEqual(p['model_calls_reserved'], 2)
        saved = self.apply(p); self.assertEqual(saved['alignment']['revision'], saved['revision'])
        self.assertEqual(len(reviews), 1); self.assertEqual(self.calls, 1)
        self.apply(p); self.assertEqual(len(reviews), 1)
    def test_negative_semantic_review_blocks_adoption(self):
        r = self.store.get(self.token); r['mode'] = 'live'; atomic_json(self.store._path(self.token), r)
        self.svc.planner.review = lambda *a: {'overview_matches':False,'issues':['wrong intent'],'goals':[]}
        self.assertEqual(self.preview()['state'], 'invalid')
        self.assertEqual(self.store.get(self.token)['draft'], self.base['draft'])
    def test_missing_new_entity_dependency_rejected(self):
        old = self.base['draft']['places'][0]['name']
        self.plan['operations'] = [self.op(id='place',kind='place.add',target='',values={'name':'Harbor','description':'New'}),self.op(id='route',kind='route.add',target='',values={'from':old,'to':'Harbor'})]
        self.assertEqual(self.preview()['state'], 'invalid')
    def test_second_operation_failure_keeps_base(self):
        self.plan['operations'].append(self.op(id='bad',target='Absent'))
        self.assertEqual(self.preview()['state'], 'invalid')
        self.assertEqual(self.store.get(self.token)['draft'], self.base['draft'])
    def test_unregistered_item_holder_rejected(self):
        self.plan['operations'] = [self.op(kind='item.add', target='',values={'name':'Tool','holder':'Absent'})]
        self.assertEqual(self.preview()['state'], 'invalid')
    def test_manual_other_field_preserved(self):
        r = self.store.get(self.token); r['draft']['initial_story'] = 'Human text'; atomic_json(self.store._path(self.token),r)
        p = self.preview(); self.assertEqual(p['candidate']['initial_story'], 'Human text')

class ConfirmedPlanRefinementTests(unittest.TestCase):
    def test_registered_additions_preserve_plan_and_goals(self):
        from test_draft_goal_plan import plan, draft
        from execution.refinement_contract import check_refined_goals
        original=plan(); before=deepcopy(original); d=draft()
        d['places'].append({'name':'Harbor','description':'New place'})
        d['routes'].append({'from':d['places'][0]['name'],'to':'Harbor'})
        d['items'].append({'name':'Tool','holders':[{'person':d['people'][0]['name'],'count':1}]})
        check_refined_goals(ROOT,original,d);self.assertEqual(original,before)
        d['people'][0]['goal']['conditions']=[]
        with self.assertRaises(ConfigError):check_refined_goals(ROOT,original,d)
    def test_confirmed_entities_cannot_disappear(self):
        from test_draft_goal_plan import plan, draft
        from execution.refinement_contract import check_refined_goals
        d=draft();d['items']=[]
        with self.assertRaises(ConfigError):check_refined_goals(ROOT,plan(),d)

class RefinementRouteTests(unittest.TestCase):
    def test_resolves_only_registered_get_and_post_routes(self):
        from viewer.library_pages import _resolve, _world_refinement_get, _world_refinement_post
        for action in ('refine','apply-refinement','cancel-refinement'):
            self.assertEqual(_resolve(['api','world-drafts','a'*32,action], 'POST'), (_world_refinement_post, ('a'*32,action)))
        self.assertEqual(_resolve(['api','world-drafts','a'*32,'refinements','capabilities'], 'GET'), (_world_refinement_get, ('a'*32,'capabilities')))

if __name__ == '__main__': unittest.main()
