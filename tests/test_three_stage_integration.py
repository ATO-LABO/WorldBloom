"""Fixed-provider integration tests for the opt-in three-stage draft path."""
from copy import deepcopy
from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import patch

from execution.draft_goal_plan import annotations, normalize
from execution.goal_plan_pipeline import GoalPlanRun
from execution.provenance import ConfigError
from execution.world_drafts import DraftStore
from test_draft_goal_plan import FakeProvider, NAME, OVERVIEW, REPO, draft, plan, review_result


class BridgeProvider:
    def __init__(self, inner):
        self.inner = inner
        self.legacy_calls = 0
        self.plan_factory = lambda: self.inner
        self.planning_status = lambda: {"status": "configured", "max_calls": 3}

    def __call__(self, name, overview):
        self.legacy_calls += 1
        return json.dumps(draft())


class ThreeStageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wb-three-stage-integration-')
        self.addCleanup(self.temp.cleanup)
        self.control = Path(self.temp.name)
        self.token = 'c' * 32

    def store(self, inner=None):
        inner = inner or FakeProvider()
        provider = BridgeProvider(inner)
        return DraftStore(REPO, self.control, provider, 'mock'), provider, inner

    def create(self, store):
        return store.create(self.token, NAME, OVERVIEW, strategy='goal-plan-v1')

    def test_full_path_has_three_calls_and_preserved_interpretation(self):
        store, provider, inner = self.store()
        result = self.create(store)
        self.assertEqual(result['state'], 'review')
        self.assertEqual(inner.calls, ['extract', 'generate', 'review'])
        self.assertEqual(provider.legacy_calls, 0)
        self.assertEqual(result['model_calls'], 3)
        self.assertEqual(result['goal_plan'], plan())
        self.assertEqual(result['goal_plan_notes']['origin'], 'ai_interpretation')
        saved = GoalPlanRun(REPO, self.control, inner).get(self.token)
        self.assertEqual(saved['confirmation']['origin'], 'automatic_interpretation')

    def test_duplicate_create_never_replays(self):
        store, provider, inner = self.store()
        first = self.create(store)
        self.assertEqual(self.create(store), first)
        self.assertEqual(len(inner.calls), 3)

    def test_changed_input_same_token_is_rejected_without_calls(self):
        store, provider, inner = self.store()
        self.create(store)
        with self.assertRaises(ConfigError):
            store.create(self.token, NAME, OVERVIEW + ' changed', strategy='goal-plan-v1')
        self.assertEqual(len(inner.calls), 3)

    def test_unknown_generation_retains_plan_and_no_retry(self):
        store, provider, inner = self.store(FakeProvider(failure='generate'))
        result = self.create(store)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['goal_plan'], plan())
        self.assertEqual(result['model_calls'], 2)
        self.assertEqual(self.create(store), result)
        self.assertEqual(inner.calls, ['extract', 'generate'])

    def test_failed_extract_does_not_generate(self):
        store, provider, inner = self.store(FakeProvider(failure='extract'))
        result = self.create(store)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual(self.create(store), result)
        self.assertEqual(inner.calls, ['extract'])

    def test_editable_bad_initial_state_is_retained_and_reviewed_once(self):
        inner = FakeProvider(change=lambda d: d['people'][1].update(entry=plan()['places'][1]))
        store, provider, inner = self.store(inner)
        result = self.create(store)
        self.assertEqual(result['state'], 'invalid')
        self.assertTrue(result['editable_generated_candidate'])
        self.assertIsInstance(result['draft'], dict)
        self.assertIn('plan.g02', result['errors'])
        self.assertEqual(inner.calls, ['extract', 'generate', 'review'])
        self.assertEqual(self.create(store), result)

    def test_plan_is_saved_before_generation_transport(self):
        observed = []
        control, token = self.control, self.token
        class Inspect(FakeProvider):
            def call(self, phase, record, elapsed):
                if phase == 'generate':
                    saved = GoalPlanRun(REPO, control, self).get(token)
                    observed.append(saved['confirmed_plan'])
                    self_outer = store.get(token)
                    self_assert = self_outer['goal_plan']
                    observed.append(self_assert)
                return super().call(phase, record, elapsed)
        store, provider, inner = self.store(Inspect())
        self.assertEqual(self.create(store)['state'], 'review')
        self.assertEqual(observed, [plan(), plan()])

    def test_cancel_during_extraction_prevents_followup_calls(self):
        class Cancel(FakeProvider):
            def call(self, phase, record, elapsed):
                result = super().call(phase, record, elapsed)
                if phase == 'extract':
                    store.cancel(record['request_id'])
                return result
        store, provider, inner = self.store(Cancel())
        result = self.create(store)
        self.assertEqual(result['state'], 'cancelled')
        self.assertIsNone(result['draft'])
        self.assertEqual(inner.calls, ['extract'])
        self.assertEqual(self.create(store), result)

    def test_paraphrase_is_allowed_but_labelled_interpretation(self):
        p = plan()
        p['goals'][0]['source_note'] = 'A paraphrased interpretation, not an exact quotation.'
        self.assertEqual(normalize(p, OVERVIEW), p)
        notes = annotations(p, OVERVIEW)
        self.assertEqual(notes['notes'][0]['kind'], 'interpretation')
        self.assertEqual(notes['origin'], 'ai_interpretation')

    def test_cancel_during_generation_prevents_review(self):
        class Cancel(FakeProvider):
            def call(self, phase, record, elapsed):
                result = super().call(phase, record, elapsed)
                if phase == 'generate':
                    store.cancel(record['request_id'])
                return result
        store, provider, inner = self.store(Cancel())
        result = self.create(store)
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(inner.calls, ['extract', 'generate'])
        self.assertEqual(self.create(store), result)

    def test_unknown_review_keeps_candidate_and_never_replays(self):
        store, provider, inner = self.store(FakeProvider(failure='review'))
        result = self.create(store)
        self.assertEqual(result['state'], 'unknown')
        self.assertIsInstance(result['draft'], dict)
        self.assertEqual(inner.calls, ['extract', 'generate', 'review'])
        resumed = DraftStore(REPO, self.control, provider, 'mock')
        self.assertEqual(self.create(resumed), result)
        self.assertEqual(len(inner.calls), 3)

    def test_readonly_admission_reports_three_call_budget_without_provider_calls(self):
        from execution.draft_generation_status import planning_availability
        store, provider, inner = self.store()
        result = planning_availability(store)
        self.assertEqual(result['status'], 'configured')
        self.assertEqual(result['required_calls'], 3)
        self.assertEqual(inner.calls, [])
        self.assertFalse(store._path(self.token).exists())

    def test_readonly_admission_rejects_two_call_budget(self):
        from execution.draft_generation_status import planning_availability
        store, provider, inner = self.store()
        provider.planning_status = lambda: {'status': 'configured', 'max_calls': 2}
        self.assertEqual(planning_availability(store)['status'], 'insufficient_budget')
        self.assertEqual(inner.calls, [])

    def test_readonly_admission_reports_active_gate_busy(self):
        from execution.draft_generation_status import planning_availability
        store, provider, inner = self.store()
        with store._plan_gate:
            self.assertEqual(planning_availability(store)['status'], 'busy')
        self.assertEqual(inner.calls, [])

    def test_create_insufficient_budget_leaves_no_record_or_call(self):
        store, provider, inner = self.store()
        provider.planning_status = lambda: {'status': 'configured', 'max_calls': 2}
        with self.assertRaises(ConfigError):
            self.create(store)
        self.assertEqual(inner.calls, [])
        self.assertFalse(store._path(self.token).exists())

    def test_workspace_preserves_interpretation_metadata_without_model(self):
        from execution.draft_workspaces import WorkspaceStore
        metadata = {'strategy': 'goal-plan-v1', 'model_calls': 3,
                    'goal_plan': plan(), 'goal_plan_notes': annotations(plan(), OVERVIEW)}
        content = {'version': 1, 'name': NAME, 'overview': OVERVIEW,
                   'time': {'enabled': False, 'days': '', 'slots': ''},
                   'draft_text': json.dumps(draft()), 'source_draft_id': self.token,
                   'generation': metadata}
        original = deepcopy(content)
        workspace = WorkspaceStore(REPO, self.control)
        saved = workspace.save('d' * 32, None, 'e' * 32, content)
        self.assertEqual(workspace.get('d' * 32)['content'], original)
        self.assertEqual(saved['content']['generation'], metadata)
        self.assertEqual(content, original)

    def test_workspace_rejects_invalid_generation_metadata(self):
        from execution.draft_workspaces import generation_metadata
        base = {'strategy': 'goal-plan-v1', 'model_calls': 3}
        invalid = [{}, None, [], {**base, 'model_calls': True},
                   {**base, 'model_calls': -1}, {**base, 'model_calls': 4},
                   {**base, 'extra': 'unexpected'}, {**base, 'goal_plan': []},
                   {**base, 'goal_plan_notes': {}},
                   {**base, 'goal_plan': {'large': 'x' * 64000}}]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ConfigError):
                    generation_metadata(value)
        for calls in range(4):
            self.assertEqual(generation_metadata({**base, 'model_calls': calls})['model_calls'], calls)

    def test_structurally_broken_dict_is_not_an_editable_candidate(self):
        store, provider, inner = self.store(FakeProvider(change=lambda d: d.clear()))
        result = self.create(store)
        self.assertEqual(result['state'], 'invalid')
        self.assertFalse(result['editable_generated_candidate'])
        self.assertIsNone(result['draft'])
        self.assertEqual(inner.calls, ['extract', 'generate'])
        raw = GoalPlanRun(REPO, self.control, inner).get(self.token)
        self.assertIsInstance(raw['draft'], dict)
        self.assertNotIn('draft_revision', raw)
        self.assertEqual(self.create(store), result)

    def test_elapsed_budget_includes_local_normalization_and_validation(self):
        import execution.goal_plan_pipeline as pipeline
        clock = [100.0]
        received = []
        class Timed(FakeProvider):
            def call(self, phase, record, elapsed):
                received.append((phase, elapsed))
                value = super().call(phase, record, elapsed)
                clock[0] += 2
                return value
        real_normalize, real_check = pipeline.normalize, pipeline.check_draft
        def normalize_with_delay(*args, **kwargs):
            clock[0] += 5
            return real_normalize(*args, **kwargs)
        def check_with_delay(*args, **kwargs):
            clock[0] += 11
            return real_check(*args, **kwargs)
        with patch.object(pipeline.time, 'monotonic', side_effect=lambda: clock[0]):
            inner = Timed()
            runner = GoalPlanRun(REPO, self.control, inner)
            with patch.object(pipeline, 'normalize', side_effect=normalize_with_delay):
                extracted = runner.extract(self.token, NAME, OVERVIEW)
            runner.confirm(self.token, extracted['plan_revision'], extracted['plan'], origin='synthetic_test_oracle')
            with patch.object(pipeline, 'check_draft', side_effect=check_with_delay):
                result = runner.generate(self.token)
        self.assertEqual(result['state'], 'review')
        self.assertEqual(received, [('extract', 0.0), ('generate', 7.0), ('review', 20.0)])

    def test_gemma_profile_preserves_qwen_credentials_and_options(self):
        from execution.output_settings import write_output_settings, model_credentials, GEMMA_MODEL, GEMMA_PROFILE
        qwen = 'qwen3.8:local-test'
        base = {'model': qwen, 'base_url': 'http://127.0.0.1:11434',
                'api_key': 'synthetic-test-only-not-a-secret', 'think': True,
                'options': {'num_ctx': 16384, 'temperature': 0.4, 'top_k': 20},
                'model_options': {qwen: {'think': True, 'options': {'num_ctx': 32768, 'temperature': 0.2}}},
                'limits': {'max_calls': 3}}
        original = deepcopy(base)
        settings = self.control / 'synthetic-settings.json'
        settings.write_text(json.dumps({'output': {'default_backend': 'ollama', 'ollama': base},
                                        'unrelated': {'keep': True}}), encoding='utf-8')
        with patch('execution.output_settings.generation_availability', return_value={'available': False, 'reason': 'synthetic'}):
            write_output_settings(settings, {'backend': 'ollama', 'model': GEMMA_MODEL, 'profile': GEMMA_PROFILE})
        saved = json.loads(settings.read_text())
        section = saved['output']['ollama']
        for key in ('base_url', 'api_key', 'think', 'options'):
            self.assertEqual(section[key], original[key])
        self.assertEqual(section['model_options'][qwen], original['model_options'][qwen])
        self.assertEqual(saved['unrelated'], {'keep': True})
        self.assertEqual(section['model'], GEMMA_MODEL)
        qwen_selected = model_credentials(section, qwen)
        self.assertEqual(qwen_selected['options'], original['model_options'][qwen]['options'])
        self.assertTrue(qwen_selected['think'])
        gemma_selected = model_credentials(section, GEMMA_MODEL)
        self.assertFalse(gemma_selected['think'])
        self.assertEqual(gemma_selected['options']['num_ctx'], 8192)
        self.assertEqual(gemma_selected['api_key'], original['api_key'])
        self.assertEqual(base, original)

    def test_independent_audit_catches_source_reach_omitted_from_plan_and_world(self):
        class MissingReach(FakeProvider):
            def call(self, phase, record, elapsed):
                value = super().call(phase, record, elapsed)
                if phase == 'extract':
                    value['goals'] = [g for g in value['goals'] if g['id'] != 'g02']
                elif phase == 'generate':
                    value['people'][1]['goal']['conditions'] = [c for c in value['people'][1]['goal']['conditions'] if c['type'] != 'reach']
                elif phase == 'review':
                    value = review_result(plan(), record['draft'])
                    value['coverage_complete'] = False
                    value['atomic_objectives'][1].update(plan_id='', verdict='missing')
                return value
        store, provider, inner = self.store(MissingReach())
        result = self.create(store)
        self.assertEqual(result['state'], 'invalid')
        self.assertTrue(result['errors'])
        self.assertEqual(inner.calls, ['extract', 'generate', 'review'])
        self.assertEqual(self.create(store), result)
        self.assertEqual(len(inner.calls), 3)

    def test_combined_description_accepts_separate_conditions_for_same_actor(self):
        class CombinedDescription(FakeProvider):
            def call(self, phase, record, elapsed):
                value = super().call(phase, record, elapsed)
                if phase == 'extract':
                    value['goals'][1]['description'] = plan()['places'][1] + '\u3078\u5411\u304b\u3044\u3001' + plan()['goals'][2]['description']
                elif phase == 'generate':
                    from execution.draft_goal_plan import actor_goal
                    value['people'][1]['goal'] = actor_goal(record['confirmed_plan'], plan()['people'][1])
                return value
        store, provider, inner = self.store(CombinedDescription())
        result = self.create(store)
        self.assertEqual(result['state'], 'review')
        self.assertEqual(len(result['goal_plan']['goals']), 3)
        self.assertEqual(inner.calls, ['extract', 'generate', 'review'])
        self.assertEqual(self.create(store), result)
        self.assertEqual(len(inner.calls), 3)

    def test_false_positive_audit_does_not_hide_combined_reach_description(self):
        class FalsePositive(FakeProvider):
            def call(self, phase, record, elapsed):
                value = super().call(phase, record, elapsed)
                if phase == 'extract':
                    value['goals'] = [g for g in value['goals'] if g['id'] != 'g02']
                    value['goals'][1]['description'] = plan()['places'][1] + '\u3078\u5411\u304b\u3044\u3001' + plan()['goals'][2]['description']
                elif phase == 'generate':
                    from execution.draft_goal_plan import actor_goal
                    value['people'][1]['goal'] = actor_goal(record['confirmed_plan'], plan()['people'][1])
                return value
        store, provider, inner = self.store(FalsePositive())
        result = self.create(store)
        self.assertEqual(result['state'], 'invalid')
        self.assertIn('alignment.atomic_coverage', result['errors'])
        self.assertEqual(inner.calls, ['extract', 'generate', 'review'])
        self.assertEqual(self.create(store), result)
        self.assertEqual(len(inner.calls), 3)

    def test_invalid_atomic_audits_are_rejected_without_retry(self):
        def mismatch_actor(v):
            v['atomic_objectives'][0]['actor'] = plan()['people'][1]
        def mismatch_target(v):
            v['atomic_objectives'][1]['conditions'][0]['place'] = plan()['places'][0]
        cases = {
            'actor mismatch': mismatch_actor,
            'target mismatch': mismatch_target,
            'status mismatch': lambda v: v['atomic_objectives'][0].update(initial_status='achieved'),
            'duplicate mapping': lambda v: v['atomic_objectives'][1].update(plan_id='g01'),
            'unmapped': lambda v: v['atomic_objectives'][1].update(plan_id=''),
            'unknown mapping': lambda v: v['atomic_objectives'][1].update(plan_id='g99'),
            'duplicate row': lambda v: v['atomic_objectives'].append(deepcopy(v['atomic_objectives'][0])),
            'omitted plan row': lambda v: v['atomic_objectives'].pop(),
            'missing audit': lambda v: v.pop('atomic_objectives'),
            'bad audit type': lambda v: v.update(atomic_objectives={}),
            'incomplete coverage': lambda v: v.update(coverage_complete=False),
            'uncertain': lambda v: v['atomic_objectives'][0].update(verdict='uncertain'),
            'unsupported': lambda v: v['atomic_objectives'][0].update(verdict='unsupported', conditions=[]),
            'non atomic': lambda v: v['atomic_objectives'][0]['conditions'].append(deepcopy(v['atomic_objectives'][1]['conditions'][0])),
            'empty source note': lambda v: v['atomic_objectives'][0].update(source_note=''),
        }
        for i, (label, change) in enumerate(cases.items()):
            with self.subTest(case=label):
                class InvalidAudit(FakeProvider):
                    def call(self, phase, record, elapsed):
                        value = super().call(phase, record, elapsed)
                        if phase == 'review': change(value)
                        return value
                self.token = format(i + 1, '032x')
                store, provider, inner = self.store(InvalidAudit())
                result = self.create(store)
                self.assertEqual(result['state'], 'invalid')
                self.assertTrue(result['errors'])
                self.assertEqual(inner.calls, ['extract', 'generate', 'review'])
                self.assertEqual(self.create(store), result)
                self.assertEqual(len(inner.calls), 3)

    def test_no_strategy_preserves_legacy_provider(self):
        store, provider, inner = self.store()
        result = store.create(self.token, NAME, OVERVIEW)
        self.assertEqual(result['state'], 'review')
        self.assertEqual(provider.legacy_calls, 1)
        self.assertEqual(inner.calls, [])


if __name__ == '__main__':
    unittest.main()
