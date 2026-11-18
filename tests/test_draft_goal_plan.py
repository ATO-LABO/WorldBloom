from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import json
from unittest.mock import patch

from execution.draft_goal_plan import normalize, generation_schema, actor_goal, check_draft
from execution.draft_goals import compile_goal
from execution.goal_plan_pipeline import GoalPlanRun
from execution.provenance import ConfigError

REPO = Path(__file__).resolve().parents[1]
OVERVIEW = '川辺の村で、薬師ハルは対岸の診療所へ薬を届けたい。渡し守レンは診療所へ向かい、ハルと仲良くなりたい。村と診療所は道で結ばれ、薬は初めにハルが所持する。二人の目的はまだ未達成とし、洪水や敵は設定しない。'
NAME = '川辺の診療所（隔離試験）'


def plan():
    return {'people': ['薬師ハル', '渡し守レン'], 'places': ['川辺の村', '対岸の診療所'], 'items': ['薬'], 'facts': [],
        'initial_items': [{'person':'薬師ハル','item':'薬','count':1,'source_note':'薬は初めにハルが所持する'}],
        'goals': [
            {'id': 'g01', 'actor': '薬師ハル', 'source_note': '薬師ハルは対岸の診療所へ薬を届けたい',
             'description': '薬を対岸の診療所へ届ける', 'conditions': [{'type': 'deliver', 'item': '薬', 'place': '対岸の診療所'}], 'initial_status': 'unachieved'},
            {'id': 'g02', 'actor': '渡し守レン', 'source_note': '渡し守レンは診療所へ向かい',
             'description': '対岸の診療所へ到着する', 'conditions': [{'type': 'reach', 'place': '対岸の診療所'}], 'initial_status': 'unachieved'},
            {'id': 'g03', 'actor': '渡し守レン', 'source_note': 'ハルと仲良くなりたい',
             'description': '薬師ハルと仲良くなる', 'conditions': [{'type': 'befriend', 'person': '薬師ハル', 'threshold': .5}], 'initial_status': 'unachieved'}],
        'assumptions': ['親しさの達成閾値0.5は原文にない提案値です']}


def draft():
    p = plan()
    return {'name': NAME, 'overview': OVERVIEW, 'initial_story': 'ハルは薬を持ち、レンと村を出ようとしている。',
        'people': [{'name': name, 'description': name, 'personality': '穏やか', 'entry': '川辺の村',
            'goal': actor_goal(p, name), 'relations': [{'person': other, 'affinity': .2, 'awareness': 1, 'label': '知人'}]}
            for name, other in [('薬師ハル', '渡し守レン'), ('渡し守レン', '薬師ハル')]],
        'places': [{'name': name, 'description': name} for name in p['places']],
        'items': [{'name': '薬', 'holders': [{'person': '薬師ハル', 'count': 1}]}], 'facts': [],
        'routes': [{'from': '川辺の村', 'to': '対岸の診療所'}, {'from': '対岸の診療所', 'to': '川辺の村'}],
        'protagonist': '薬師ハル', 'antagonist': '渡し守レン'}


def review_result(value=None, proposal=None):
    value, proposal = value or plan(), proposal or draft()
    return {'alignment': {'overview_matches': True,
            'goals': [{'person': p['name'], 'matches': True, 'reason': 'synthetic correspondence'} for p in proposal['people']], 'issues': []},
        'coverage_complete': True,
        'atomic_objectives': [{**{key: deepcopy(g[key]) for key in ('actor', 'source_note', 'conditions', 'initial_status')},
                               'plan_id': g['id'], 'verdict': 'matched'} for g in value['goals']]}


class FakeProvider:
    max_calls = 3
    def __init__(self, failure=None, change=None):
        self.calls = []
        self.failure, self.change = failure, change

    def call(self, phase, record, elapsed):
        self.calls.append(phase)
        assert record['calls'][-1]['phase'] == phase and record['calls'][-1]['state'] == 'dispatching'
        if phase == self.failure:
            raise OSError('synthetic transport lost')
        if phase == 'extract':
            return plan()
        if phase == 'generate':
            value = draft()
            if self.change: self.change(value)
            return value
        return review_result(record['confirmed_plan'], record['draft'])


class GoalPlanTests(unittest.TestCase):
    def test_survival_goal_uses_supplied_world_horizon_and_slots(self):
        raw = {'description': '十日間生き延びる', 'conditions': [{'type': 'survive', 'day': 10}]}
        _, expression = compile_goal(raw, '薬師ハル', {'薬師ハル'}, set(), set(), set(), 'goal',
                                     required=True, days=10, slots_per_day=3)
        self.assertIn('turn >= 30', expression)
        with self.assertRaises(ConfigError):
            compile_goal(raw, '薬師ハル', {'薬師ハル'}, set(), set(), set(), 'goal',
                         required=True, days=9, slots_per_day=3)

    def test_legacy_item_holder_still_compiles_with_one_item(self):
        from execution.world_drafts import compile_draft
        proposal = draft()
        proposal['items'] = [{'name': '薬', 'holder': '薬師ハル'}]
        _, _, subjects = compile_draft(proposal)
        self.assertIn('薬: 1', subjects['subjects/person_01.yaml'])

    def test_input_settings_are_rejected_before_any_provider_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FakeProvider()
            store = GoalPlanRun(REPO, Path(tmp), provider)
            with self.assertRaises(ConfigError):
                store.extract('a'*32, NAME, OVERVIEW, settings={'backend': 'other'})
            self.assertEqual(provider.calls, [])

    def test_provider_sends_three_distinct_contracts_without_changing_settings(self):
        from execution.goal_plan_pipeline import PlanProvider
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp)/'settings.json'
            settings.write_text(json.dumps({'output':{'default_backend':'ollama','ollama':{'model':'fake','limits':{'max_calls':3}}}}),encoding='utf-8')
            original = settings.read_bytes(); provider = PlanProvider(settings); captured = []
            review = review_result()
            def reply(request, command):
                captured.append(deepcopy(request))
                content = [plan(),draft(),review][len(captured)-1]
                return type('Response',(),{'truncated':False,'raw':json.dumps({'done':True,'done_reason':'stop','message':{'content':json.dumps(content,ensure_ascii=False)}}).encode()})()
            record = {'name':NAME,'overview':OVERVIEW,'authoring':None,'input_settings':None,'confirmed_plan':plan(),'draft':draft()}
            with patch('execution.generation.transport',side_effect=reply):
                for phase in ['extract','generate','review']: provider.call(phase,record,0)
            self.assertIn('source_note', captured[0]['response_schema']['properties']['goals']['items']['properties'])
            self.assertEqual(captured[1]['response_schema'], generation_schema(plan()))
            self.assertIn('atomic_objectives',captured[2]['response_schema']['properties'])
            self.assertIn('alignment',captured[2]['response_schema']['properties'])
            self.assertEqual(settings.read_bytes(),original)

    def test_two_call_profile_is_not_silently_upgraded(self):
        from execution.goal_plan_pipeline import PlanProvider
        with tempfile.TemporaryDirectory() as tmp:
            settings=Path(tmp)/'settings.json'
            settings.write_text(json.dumps({'output':{'default_backend':'ollama','ollama':{'model':'fake','limits':{'max_calls':2}}}}),encoding='utf-8')
            with self.assertRaises(ConfigError): PlanProvider(settings)

    def test_normalize_and_schema_compile_confirmed_conditions_without_changing_plan(self):
        p = plan(); original = deepcopy(p)
        self.assertEqual(normalize(p, OVERVIEW), p)
        s = generation_schema(p)
        ren = s['properties']['people']['items']['oneOf'][1]
        self.assertEqual(ren['properties']['goal']['const'], actor_goal(p, '渡し守レン'))
        self.assertEqual(len(ren['properties']['goal']['const']['conditions']), 2)
        self.assertEqual(p, original)

    def test_empty_note_bad_reference_id_and_non_atomic_rows_rejected(self):
        for change in [lambda p:p['goals'][0].update(source_note=''),
                       lambda p:p['goals'][0].update(actor='別人'),
                       lambda p:p['goals'][1].update(id='g01'),
                       lambda p:p['goals'][1]['conditions'].append({'type':'meet','person':'薬師ハル'})]:
            p = plan(); change(p)
            with self.assertRaises(ConfigError): normalize(p, OVERVIEW)

    def test_delivery_precondition_held_initially_is_allowed_but_each_objective_false(self):
        d = draft(); original = deepcopy(d)
        _, _, report = check_draft(REPO, plan(), d)
        self.assertEqual(d, original)
        self.assertEqual([g['initially_achieved'] for g in report['goals']], [False, False, False])
        self.assertEqual(d['items'][0]['holders'][0]['person'], '薬師ハル')

    def test_initial_item_is_not_removed_to_make_delivery_unachieved(self):
        d=draft();d['items'][0]['holders']=[]
        with self.assertRaises(ConfigError) as e:check_draft(REPO,plan(),d)
        self.assertIn('plan.initial_items',e.exception.field_errors)

    def test_missing_arrival_condition_rejected_even_if_description_is_also_rewritten(self):
        d = draft(); d['people'][1]['goal'] = {'description':'薬師ハルと仲良くなる','conditions':[{'type':'befriend','person':'薬師ハル','threshold':.5}]}
        with self.assertRaises(ConfigError) as e: check_draft(REPO, plan(), d)
        self.assertIn('plan.coverage', e.exception.field_errors)

    def test_initial_arrival_true_rejected_when_combined_goal_is_still_false(self):
        d = draft(); d['people'][1]['entry'] = '対岸の診療所'
        with self.assertRaises(ConfigError) as e: check_draft(REPO, plan(), d)
        self.assertIn('plan.g02', e.exception.field_errors)

    def test_initial_friendship_true_rejected_when_arrival_is_still_false(self):
        d = draft()
        for p in d['people']: p['relations'][0]['affinity'] = .8
        with self.assertRaises(ConfigError) as e: check_draft(REPO, plan(), d)
        self.assertIn('plan.g03', e.exception.field_errors)

    def test_directed_connectivity_is_checked_without_claiming_full_feasibility(self):
        d = draft(); d['routes'] = [{'from':'対岸の診療所','to':'川辺の村'}]
        with self.assertRaises(ConfigError) as e: check_draft(REPO, plan(), d)
        self.assertTrue(any(k.endswith('.route') for k in e.exception.field_errors))

    def start(self, root, provider):
        store = GoalPlanRun(REPO, Path(root), provider)
        r = store.extract('a'*32, NAME, OVERVIEW)
        self.assertEqual(r['state'], 'awaiting_confirmation')
        return store, r

    def test_three_stages_and_persisted_confirmation_resume_without_reextracting(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FakeProvider(); store, r = self.start(tmp, provider)
            self.assertEqual(provider.calls, ['extract'])
            self.assertEqual(store.generate('a'*32)['state'], 'awaiting_confirmation')
            store.confirm('a'*32, r['plan_revision'], r['plan'], origin='synthetic_test_oracle')
            resumed = GoalPlanRun(REPO, Path(tmp), provider)
            result = resumed.generate('a'*32)
            self.assertEqual(result['state'], 'review')
            self.assertEqual(provider.calls, ['extract','generate','review'])
            self.assertEqual(result['confirmation']['origin'], 'synthetic_test_oracle')
            self.assertEqual(resumed.generate('a'*32), result)
            self.assertEqual(len(provider.calls), 3)
            with self.assertRaises(ConfigError): resumed.dispatch('a'*32, 'extra')

    def test_stale_confirmation_and_changed_input_do_not_overwrite_saved_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FakeProvider(); store, r = self.start(tmp, provider)
            with self.assertRaises(ConfigError): store.confirm('a'*32, 'stale', r['plan'], origin='human')
            with self.assertRaises(ConfigError): store.extract('a'*32, NAME, OVERVIEW+'別入力')
            self.assertEqual(store.get('a'*32), r)
            self.assertEqual(len(provider.calls), 1)

    def test_empty_note_requires_explicit_edit_and_keeps_raw_plan_without_reextracting(self):
        class BadQuote(FakeProvider):
            def call(self, phase, record, elapsed):
                value=super().call(phase,record,elapsed)
                if phase=='extract':value['goals'][2]['source_note']=''
                return value
        from execution.provenance import sha256,canonical
        with tempfile.TemporaryDirectory() as tmp:
            provider=BadQuote();store=GoalPlanRun(REPO,Path(tmp),provider)
            r=store.extract('a'*32,NAME,OVERVIEW)
            self.assertEqual(r['state'],'invalid')
            self.assertEqual(store.generate('a'*32),r)
            revision=sha256(canonical(r['raw_plan']))
            with self.assertRaises(ConfigError):store.confirm('a'*32,revision,r['raw_plan'],origin='synthetic_test_oracle')
            fixed=deepcopy(r['raw_plan']);fixed['goals'][2]['source_note']=plan()['goals'][2]['source_note']
            r=store.confirm('a'*32,revision,fixed,origin='synthetic_test_oracle')
            self.assertTrue(r['confirmation']['explicitly_edited'])
            self.assertEqual(r['raw_plan']['goals'][2]['source_note'],'')
            result=store.generate('a'*32)
            self.assertEqual(result['state'],'review')
            self.assertEqual(provider.calls,['extract','generate','review'])

    def test_changed_confirmation_fingerprint_blocks_before_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider=FakeProvider();store,r=self.start(tmp,provider)
            r=store.confirm('a'*32,r['plan_revision'],r['plan'],origin='synthetic_test_oracle')
            r['confirmed_plan']['goals'].pop();store.save(r)
            with self.assertRaises(ConfigError):store.generate('a'*32)
            self.assertEqual(provider.calls,['extract'])

    def test_transport_loss_preserves_confirmed_plan_and_never_auto_resends(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FakeProvider(failure='generate'); store, r = self.start(tmp, provider)
            store.confirm('a'*32, r['plan_revision'], r['plan'], origin='synthetic_test_oracle')
            result = store.generate('a'*32)
            self.assertEqual(result['state'], 'unknown')
            self.assertEqual(result['confirmed_plan'], r['plan'])
            resumed = GoalPlanRun(REPO, Path(tmp), provider)
            self.assertEqual(resumed.generate('a'*32), result)
            self.assertEqual(resumed.extract('a'*32, NAME, OVERVIEW), result)
            self.assertEqual(provider.calls, ['extract','generate'])

    def test_bad_generated_initial_state_stops_before_review_and_keeps_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FakeProvider(change=lambda d:d['people'][1].update(entry='対岸の診療所'))
            store, r = self.start(tmp, provider)
            store.confirm('a'*32, r['plan_revision'], r['plan'], origin='synthetic_test_oracle')
            result = store.generate('a'*32)
            self.assertEqual(result['state'], 'invalid')
            self.assertIn('draft', result)
            self.assertEqual(provider.calls, ['extract','generate'])

    def test_narrative_only_intent_is_kept_but_not_silently_made_an_automatic_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, r = self.start(tmp, FakeProvider())
            p = deepcopy(r['plan']); p['goals'][1]['conditions'] = []
            with self.assertRaises(ConfigError): store.confirm('a'*32, r['plan_revision'], p, origin='human')
            self.assertEqual(store.get('a'*32)['state'], 'awaiting_confirmation')

    def test_failed_extraction_spends_only_one_call_and_does_not_generate(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = FakeProvider(failure='extract'); store = GoalPlanRun(REPO, Path(tmp), provider)
            r = store.extract('b'*32, NAME, OVERVIEW)
            self.assertEqual(r['state'], 'unknown')
            self.assertEqual(store.generate('b'*32), r)
            self.assertEqual(provider.calls, ['extract'])
