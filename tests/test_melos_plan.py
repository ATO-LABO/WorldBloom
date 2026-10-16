"""Admission and evidence tests; never an independent-trial batch."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

from engine.execution_plan import ExecutionPlan, digest
from execution.melos_plan import MelosPlanBinding, capabilities, classify_session
from execution.melos_session import MelosSession
from gapengine.genome import Genome
from gapengine.melos_choice_provider import MelosChoiceProvider


class EvidenceTests(unittest.TestCase):
    def test_capability_reports_explicit_connected_profile(self):
        self.assertTrue(capabilities()['product_active'])
        self.assertTrue(capabilities()['independent_batch_runner'])
        self.assertTrue(capabilities()['product_api_connected'])
        self.assertTrue(capabilities()['run_record_connected'])

    def test_agreement_is_not_completed_work_and_failure_is_retained(self):
        s = SimpleNamespace(rt=SimpleNamespace(rows=[], ledger={'tasks': {}, 'completion_events': []},
            now=10, end=100, busy={'Philostratus': {}}),
            trial=SimpleNamespace(state={'outcome': None, 'agreed': True}), finished=False)
        before = deepcopy(s.__dict__)
        result = classify_session(s)
        self.assertEqual(result['status'], 'unresolved')
        self.assertFalse(result['physical_court_arrival'])
        self.assertFalse(result['completed_120_minute_wedding']['completed'])
        self.assertEqual(result['remaining_background_actors'], ['Philostratus'])
        self.assertFalse(result['calendar_horizon_reached'])
        self.assertEqual(s.__dict__, before)
        self.assertEqual(classify_session(s, execution_failure=RuntimeError('failed'))['status'], 'execution_failure')

    def test_physical_arrival_report_and_wedding_are_separate_evidence(self):
        def action(ident, at, choice, duration, **extra):
            return {'id': ident, 'verb': 'action_completed', 'subject': 'Melos',
                    'result': 'applied', 'absolute_minute': at,
                    'details': {'choice': choice, 'duration_minutes': duration,
                                'caused_by': ['start:'+ident], **extra}}
        arrival = action('arrival', 10, {'verb': 'move', 'to': 'court'}, 5, arrived_at=10)
        report = action('report', 12, {'verb': 'work', 'task_id': 'return'}, 2)
        wedding = action('wedding', 132, {'verb': 'work', 'task_id': 'wedding'}, 120)
        rows = [arrival, report, wedding]
        ledger = {'tasks': {}, 'completion_events': []}
        for a, task in ((report, 'return'), (wedding, 'wedding')):
            uid = 'unit:'+task
            rows.append({'id': uid, 'verb': 'work_unit_completed', 'subject': 'Melos',
                'absolute_minute': a['absolute_minute'], 'result': 'applied', 'details': {'task_id': task,
                    'caused_by': a['details']['caused_by']}})
            ledger['tasks'][task] = {'units': 1, 'contributions': {'Melos': 1}}
            ledger['completion_events'].append({'id': uid, 'task_id': task, 'actor': 'Melos'})
        state = {'outcome': 'both_alive_pardoned', 'returned_at': 12, 'wedding': True}
        rows.append({'id': 'final', 'verb': 'observation_result', 'details': {'state': deepcopy(state)}})
        s = SimpleNamespace(rt=SimpleNamespace(rows=rows, ledger=ledger, now=132, end=200,
                busy={'Philostratus': {}}), trial=SimpleNamespace(state=state), finished=True)
        result = classify_session(s)
        self.assertEqual(result['status'], 'observed')
        self.assertTrue(result['physical_court_arrival'])
        self.assertTrue(result['fixed_two_minute_report']['completed'])
        self.assertTrue(result['completed_120_minute_wedding']['completed'])
        self.assertFalse(result['calendar_horizon_reached'])
        wedding['details']['duration_minutes'] = 119
        result = classify_session(s)
        self.assertFalse(result['completed_120_minute_wedding']['completed'])
        self.assertEqual(result['status'], 'unresolved')
        report['details']['duration_minutes'] = 1
        result = classify_session(s)
        self.assertTrue(result['physical_court_arrival'])
        self.assertFalse(result['fixed_two_minute_report']['completed'])


@unittest.skipUnless(os.environ.get('MELOS_APPROVED_ADAPTER_DIR') and os.environ.get('MELOS_INPUT_DIR'),
                     'approved external fixture paths not supplied')
class BindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = Path(os.environ['MELOS_APPROVED_ADAPTER_DIR'])
        sys.path.insert(0, str(folder))
        import melos_runner
        cls.base = melos_runner
        root = Path(os.environ['MELOS_INPUT_DIR'])
        cls.base.ROOT = root.parent
        cls.inputs = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(root.rglob('*.yaml'))}
        cls.sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (folder/'melos_runner.py', folder/'ninegene_melos.py')}

    def session(self, seed):
        genome = Genome.neutral()
        return MelosSession(self.base.Trial(genome, seed), input_hashes=self.inputs,
                            source_hashes=self.sources, choice_provider=MelosChoiceProvider(genome, seed))

    def test_two_seed_plan_binds_actual_distinct_providers(self):
        slots = {0: self.session(0), 63: self.session(63)}
        plan = MelosPlanBinding(slots)
        self.assertNotEqual(slots[0].stage.provider.checkpoint_identity(), slots[63].stage.provider.checkpoint_identity())
        self.assertEqual(plan.plan.to_dict()['predeclared_seeds'], [0, 63])
        self.assertFalse(any(plan.plan.to_dict()['selection'].values()))
        for seed in slots:
            fresh = self.session(seed)
            self.assertIs(plan.bind_session(fresh, seed), fresh)
        with self.assertRaises(ValueError): plan.bind_session(self.session(0), 63)
        with self.assertRaises(ValueError): plan.bind_session(self.session(1), 1)

    def test_seed_admission_is_detached_and_exact(self):
        s = self.session(0); binding = MelosPlanBinding({0: s})
        binding.bind_session(s, 0)
        receipt = binding.seed_admission(0)
        self.assertEqual(receipt['actual_provider'], s.stage.provider.checkpoint_identity())
        self.assertEqual(receipt['plan_hash'], binding.plan.sha256)
        self.assertEqual(digest(receipt['provider_catalog']), binding.plan.to_dict()['provider']['config_hash'])
        self.assertEqual(receipt['provider_catalog']['0']['provider'], receipt['actual_provider'])
        self.assertEqual(receipt['provider_catalog']['0']['initial_hash'], receipt['initial_session_hash'])
        manifest = binding.manifest()
        self.assertEqual(manifest['execution_plan_hash'], binding.plan.sha256)
        manifest['provider_catalog']['0']['initial_hash'] = '0'*64
        self.assertNotEqual(manifest, binding.manifest())
        receipt['actual_provider']['config_hash'] = '0'*64
        self.assertNotEqual(receipt, binding.seed_admission(0))
        for bad in (True, 1, '0'):
            with self.assertRaises(ValueError): binding.seed_admission(bad)

    def test_mutated_classifier_stop_provider_or_sampler_rejected(self):
        binding = MelosPlanBinding({0: self.session(0)})
        for field in ('sampler', 'classification', 'provider'):
            value = binding.plan.to_dict(); value[field]['config_hash'] = 'a'*64
            with self.assertRaises(ValueError): binding.admit(ExecutionPlan(value))
        value = binding.plan.to_dict(); value['observation']['end'] += 1
        with self.assertRaises(ValueError): binding.admit(ExecutionPlan(value))

    def test_changed_inputs_source_genome_time_seed_or_initial_state_rejected(self):
        binding = MelosPlanBinding({0: self.session(0)})
        def inputs(s): s.stage.identity['inputs']['world.yaml'] = '0'*64
        def sources(s): s.stage.identity['sources']['melos_runner.py'] = '0'*64
        def genome(s): s.trial.genome.category_weight['I'] = .7
        def time(s): s.rt.end += 1
        def seed(s): s.trial.seed = 63
        def world(s): s.rt.world.zones['court']['name'] = 'changed'
        def initial(s): s.trial.info['Melos']['invented'] = True
        for mutate in (inputs, sources, genome, time, seed, world, initial):
            s = self.session(0); mutate(s)
            with self.assertRaises(ValueError, msg=mutate.__name__): binding.bind_session(s, 0)

    def test_pre_stage_overrides_are_not_approved_by_matching_file_hashes(self):
        def scenario(t): t.p['swim_cost'] += 1
        def goals(t): t.goals['Melos']['self'] += 1
        def world(t): t.rt.world.zones['court']['name'] = 'changed'
        def contract(t): t.rt.contract['tasks']['return']['minutes_per_unit'] = 3
        def state(t): t.state['wedding'] = True
        for mutate in (scenario, goals, world, contract, state):
            genome = Genome.neutral(); trial = self.base.Trial(genome, 0)
            mutate(trial)
            session = MelosSession(trial, input_hashes=self.inputs, source_hashes=self.sources,
                                   choice_provider=MelosChoiceProvider(genome, 0))
            with self.assertRaises(ValueError, msg=mutate.__name__): MelosPlanBinding({0: session})

    def test_partial_run_is_not_pristine_and_classification_does_not_mutate(self):
        s = self.session(0); binding = MelosPlanBinding({0: s})
        binding.bind_session(s, 0); s.run(until=73)
        before = s.export_state(); result = classify_session(s)
        self.assertEqual(s.export_state(), before)
        self.assertEqual(result['status'], 'unresolved')
        with self.assertRaises(ValueError): binding.bind_session(s, 0)


if __name__ == '__main__': unittest.main()
