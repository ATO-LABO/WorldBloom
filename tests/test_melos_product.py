"""Tiny admitted seed 0/63 product adapters; no discovery or model calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from execution.configs import ConfigStore
from execution import independent_review, melos_product
from execution.melos_factory import MelosSessionFactory, approved_catalogs
from execution.melos_plan import MelosPlanBinding
from execution.melos_run_record import publish_saved_run_record
from execution.melos_session import MelosSession
from execution.provenance import atomic_json, canonical, sha256
from gapengine.genome import Genome

ROOT = Path(__file__).resolve().parents[1]


class MelosProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        for package in ('engine', 'gapengine', 'scripts', 'execution'):
            shutil.copytree(ROOT / package, self.repo / package, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy(ROOT / 'requirements.txt', self.repo / 'requirements.txt')
        shutil.copytree(ROOT / 'projects/melos-input', self.repo / 'projects/renamed-approved')
        (self.repo / 'templates/unused').mkdir(parents=True)
        self.store = ConfigStore(self.repo, self.root / 'control', self.root / 'runs')
        self.config = self.store.save({'label': 'explicit neutral', 'project_id': 'renamed-approved',
            'template_id': 'unused', 'execution_profile': {'id': melos_product.PROFILE_ID,
                'version': 1, 'genome': Genome.neutral().to_dict()}}, config_id='approved')

    def request(self, seeds=(0, 63)):
        cap = melos_product.capability(self.store, 'approved')
        return {'request_id': 'tiny-request', 'kind': 'independent_trials', 'config_id': 'approved',
                **{field: cap[field] for field in melos_product._REVISION_FIELDS},
                'seed_plan': list(seeds), 'method': 'independent_trials', 'population': 'unselected',
                'selection': 'none', 'reference_guided': False}

    def frozen(self, name='run', seeds=(0, 63)):
        request = self.request(seeds)
        admission = melos_product.admit(self.store, request)
        folder = self.root / ('job-' + name)
        atomic_json(folder / 'request.json', request)
        atomic_json(folder / 'independent-plan.json', admission)
        job = {'run_id': name, 'job_id': 'job-' + name, 'nonce': 'nonce-' + name}
        independent_review.prepare_run(self.store, job, folder)
        return self.store.runs / name

    def test_explicit_capability_content_binding_and_legacy_refusal(self):
        cap = melos_product.capability(self.store, 'approved')
        self.assertTrue(cap['supported'])
        self.assertEqual(cap['time_unit'], 'minute')
        self.assertEqual(cap['genome_kind'], 'approved_neutral_ninegene')
        assumptions=cap['provider_assumptions']
        self.assertEqual(assumptions['focus_actor'], 'Melos')
        self.assertEqual(assumptions['other_actor_policy'], 'fixed_base_candidate_weights')
        self.assertEqual(assumptions['mapping_revision'], sha256(canonical(assumptions['mapping'])))
        self.assertTrue(independent_review.capability(self.store, 'approved')['supported'])
        legacy, inputs, root = self.store._bundle('approved')
        legacy = deepcopy(legacy); legacy.pop('execution_profile')
        with patch.object(self.store, '_bundle', return_value=(legacy, inputs, root)):
            self.assertFalse(independent_review.capability(self.store, 'approved')['supported'])
        request = self.request()
        admission = melos_product.admit(self.store, request)
        self.assertEqual(admission['execution_plan']['predeclared_seeds'], [0, 63])
        catalog = admission['binding_manifest']['provider_catalog']
        self.assertEqual(sha256(canonical(catalog)), admission['execution_plan']['provider']['config_hash'])
        self.assertNotEqual(catalog['0']['provider'], catalog['63']['provider'])
        changed = deepcopy(request); changed['policy_revision'] = '0' * 64
        with self.assertRaises(ValueError): melos_product.admit(self.store, changed)
        source = self.repo / 'execution/melos_product.py'
        source.write_bytes(source.read_bytes() + b'\n# source drift\n')
        with self.assertRaises(ValueError): melos_product.admit(self.store, request)
        self.assertFalse(self.store.runs.exists())

    def test_two_frozen_seeds_real_records_and_exact_scenario_semantics(self):
        run = self.frozen()
        updates = []
        result = melos_product.run_trials(run, progress=updates.append)
        self.assertEqual(result['completion'], 'complete')
        self.assertEqual(result['requested'], 2)
        self.assertEqual(result['completed'], 2)
        self.assertEqual(result['failed'], 0)
        self.assertEqual([t['seed'] for t in result['trials']], [0, 63])
        self.assertGreater(len(updates), 2)
        self.assertEqual(updates[-1]['completed'], 2)
        for trial in result['trials']:
            record = trial['run_record']
            self.assertIsNone(record['verified_execution'])
            self.assertEqual(record['descriptor_scope'], 'melos_committed_session_v1')
            self.assertTrue(record['observations']['scenario_finished'])
            self.assertEqual(trial['category'], record['observations']['scenario_outcome'])
            self.assertEqual(record['actual_terminal']['verb'], 'observation_result')
            self.assertEqual(record['melos_admission']['seed'], trial['seed'])
            self.assertNotEqual(record['provider_identity']['config_hash'],
                                record['execution_plan']['provider']['config_hash'])
            case = run / 'trials' / trial['id']
            self.assertEqual(sha256((case / 'layers.jsonl').read_bytes()), record['ledger']['sha256'])
            self.assertEqual(sha256((case / 'calendar-state.json').read_bytes()), record['checkpoint_sha256'])
        self.assertTrue((run / 'review-result.json').is_file())
        with self.assertRaises(ValueError): melos_product.run_trials(run)
        with patch.object(MelosSession, 'run_saved', side_effect=AssertionError('terminal rerun')):
            resumed = melos_product.run_trials(run, resume=True)
        self.assertEqual(resumed, result)
        with patch.object(MelosSession, 'run_saved', side_effect=AssertionError('cancelled rerun')):
            self.assertEqual(melos_product.run_trials(run, resume=True, should_cancel=lambda: True), result)
        if os.environ.get('MELOS_PRODUCT_EVIDENCE'):
            destination = Path(os.environ['MELOS_PRODUCT_EVIDENCE'])
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copytree(run, destination / 'two-seed-run', dirs_exist_ok=True)

    def test_cancel_resume_matches_continuous_and_repairs_derived_outputs(self):
        split = self.frozen('split', seeds=(0,))
        cancelled = {'value': False}
        def progress(value): cancelled['value'] = True
        partial = melos_product.run_trials(split, progress=progress,
                                           should_cancel=lambda: cancelled['value'])
        self.assertEqual(partial['completion'], 'cancelled_partial')
        self.assertEqual(partial['completed'], 0)
        self.assertEqual(partial['trials'][0]['status'], 'not_completed')
        self.assertEqual(partial['trials'][0]['run_record']['observed_through'], 120)
        self.assertFalse((split / 'review-result.json').exists())
        case = split / 'trials/trial-0000'
        (case / 'layers.jsonl').write_bytes(b'stale derived output')
        (case / 'run-record.json').unlink()
        resumed = melos_product.run_trials(split, resume=True)
        continuous = self.frozen('continuous', seeds=(0,))
        final = melos_product.run_trials(continuous)
        self.assertEqual(resumed['trials'], final['trials'])
        self.assertEqual((case / 'layers.jsonl').read_bytes(),
                         (continuous / 'trials/trial-0000/layers.jsonl').read_bytes())
        # Compare against the existing unchunked finite session, too.
        manifest = json.loads((split / 'review-manifest.json').read_text())
        with melos_product._factory(split / 'runtime', split / 'inputs', manifest['config']) as factory:
            binding = MelosPlanBinding({0: factory.create(0)})
            unchunked = self.root / 'unchunked'
            factory.create(0).run_saved(unchunked)
            record = publish_saved_run_record(unchunked, binding, factory.create(0), 0)
        self.assertEqual(record, resumed['trials'][0]['run_record'])

    def test_failure_retains_seed_checkpoint_and_never_retries_it(self):
        run = self.frozen(seeds=(0, 63))
        original = MelosSession.run_saved
        calls = []
        def fail_after_commit(session, *args, **kwargs):
            calls.append(session.trial.seed)
            result = original(session, *args, **kwargs)
            if session.trial.seed == 0:
                raise RuntimeError('injected after committed boundary')
            return result
        with patch.object(MelosSession, 'run_saved', new=fail_after_commit):
            result = melos_product.run_trials(run)
        self.assertEqual(result['completed'], 2)
        self.assertEqual(result['failed'], 1)
        failed = result['trials'][0]
        self.assertEqual(failed['seed'], 0)
        self.assertEqual(failed['status'], 'execution_failure')
        self.assertEqual(failed['run_record']['stop_reason'], 'paused')
        self.assertEqual(calls.count(0), 1)
        with patch.object(MelosSession, 'run_saved', side_effect=AssertionError('reroll')):
            self.assertEqual(melos_product.run_trials(run, resume=True), result)
            self.assertEqual(melos_product.run_trials(run, resume=True, should_cancel=lambda: True), result)

    def test_preflight_drift_and_cross_job_resume_reject_before_execution(self):
        run = self.frozen(seeds=(0,))
        request_path = run / 'accepted-request.json'
        request = json.loads(request_path.read_text()); request['seed_plan'] = [63]
        atomic_json(request_path, request)
        with self.assertRaises(ValueError): melos_product.run_trials(run)
        self.assertFalse((run / 'trials').exists())
        other = self.frozen('owned', seeds=(0,))
        melos_product.run_trials(other, should_cancel=lambda: True)
        owner = json.loads((other / 'melos-owner.json').read_text()); owner['job_id'] = 'different'
        atomic_json(other / 'melos-owner.json', owner)
        with self.assertRaises(ValueError): melos_product.run_trials(other, resume=True)
        self.assertFalse((other / 'trials').exists())

    def test_unresolved_finished_and_paused_are_distinct(self):
        record = {'observations': {'scenario_finished': True, 'status': 'unresolved',
                                  'scenario_outcome': 'decision_budget_observation_incomplete'}}
        trial = melos_product._trial(Path('trial-0000'), 0, record)
        self.assertEqual((trial['status'], trial['category']), ('observed', 'unresolved'))
        record['observations']['scenario_finished'] = False
        trial = melos_product._trial(Path('trial-0000'), 0, record)
        self.assertEqual((trial['status'], trial['category']), ('not_completed', 'not_completed'))

    def test_frozen_runtime_subprocess_preserves_portable_plan(self):
        run = self.frozen(seeds=(0,))
        script = "import sys;sys.path.insert(0,sys.argv[1]);from execution.melos_product import run_trials;run_trials(sys.argv[2])"
        process = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(run / 'runtime'), str(run)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads((run / 'review-result.json').read_text())
        self.assertEqual(result['completed'], 1)
        self.assertEqual(result['trials'][0]['run_record']['melos_admission']['seed'], 0)


if __name__ == '__main__':
    unittest.main()
