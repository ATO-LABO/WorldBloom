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
        from execution.melos_native_jobs import read_native_result, STORAGE
        run = self.frozen(); updates = []; refs = []
        with patch.object(MelosSession, 'run_saved', side_effect=AssertionError('legacy save forbidden')), \
                patch('engine.calendar_checkpoint.atomic_bytes', side_effect=AssertionError('legacy result writer forbidden')):
            result = melos_product.run_trials(run, progress=updates.append, acknowledge=lambda v: refs.append(v) or v)
        self.assertEqual((result['completion'], result['requested'], result['completed'], result['failed']), ('complete',2,2,0))
        self.assertGreater(len(updates), 2)
        for trial in result['trials']:
            record = trial['run_record']; self.assertIsNone(record['verified_execution'])
            self.assertEqual(record['descriptor_scope'], 'melos_native_generation_v1')
            self.assertTrue(record['observations']['scenario_finished'])
            self.assertEqual(trial['category'], record['observations']['scenario_outcome'])
            self.assertEqual(record['actual_terminal']['verb'], 'observation_result')
            self.assertEqual(record['melos_admission']['seed'], trial['seed'])
            self.assertNotEqual(record['provider_identity']['config_hash'],record['execution_plan']['provider']['config_hash'])
            self.assertEqual(sha256((run/'native-results'/trial['artifacts']['layers']['leaf']).read_bytes()), record['ledger']['sha256'])
        self.assertFalse((run/'review-result.json').exists());self.assertFalse((run/'trials').exists())
        job={'job_id':'job-run','independent_storage':STORAGE,'execution_plan_sha256':refs[-1]['execution_plan_sha256'],
             'accepted_request':{'seed_plan':[0,63]},'native_result_refs':refs[-1]}
        self.assertEqual(read_native_result(run,job),result)
        with self.assertRaises(ValueError):melos_product.run_trials(run,resume=True)
        with self.assertRaises((ValueError,OSError)):melos_product.run_trials(run)

    def test_cancel_retains_exact_chunk_no_restart_or_legacy_repair(self):
        run=self.frozen('split',seeds=(0,));cancelled=[False]
        def progress(value):cancelled[0]=True
        result=melos_product.run_trials(run,progress=progress,should_cancel=lambda:cancelled[0])
        self.assertEqual((result['completion'],result['completed']),('cancelled_partial',0))
        trial=result['trials'][0];self.assertEqual(trial['run_record']['observed_through'],120)
        self.assertEqual(trial['status'],'not_completed')
        before={p.name:p.read_bytes() for p in (run/'native-results').iterdir()}
        for resume in (False,True):
            with self.assertRaises((ValueError,OSError)):melos_product.run_trials(run,resume=resume)
        self.assertEqual(before,{p.name:p.read_bytes() for p in (run/'native-results').iterdir()})

    def test_export_failure_retains_selection_without_fictional_scenario_failure(self):
        from execution.melos_native_jobs import read_native_result, STORAGE
        run=self.frozen(seeds=(0,));refs=[]
        with patch('execution.melos_native_jobs.publish_native_exports',side_effect=RuntimeError('injected export failure')):
            with self.assertRaisesRegex(RuntimeError,'injected export'):
                melos_product.run_trials(run,acknowledge=lambda v:refs.append(v) or v)
        job={'job_id':'job-run','independent_storage':STORAGE,'execution_plan_sha256':refs[-1]['execution_plan_sha256'],
             'accepted_request':{'seed_plan':[0]},'native_result_refs':refs[-1]}
        result=read_native_result(run,job)
        self.assertEqual((result['completion'],result['completed'],result['failed']),('failed_partial',0,0))
        self.assertEqual(result['operation_error']['phase'],'export')
        self.assertIn('last_accepted_generation',result['trials'][0]);self.assertNotIn('run_record',result['trials'][0])

    def test_preflight_drift_and_cross_seed_catalog_reject_before_execution(self):
        run=self.frozen(seeds=(0,));request_path=run/'accepted-request.json'
        request=json.loads(request_path.read_text());request['seed_plan']=[63];atomic_json(request_path,request)
        with self.assertRaises(ValueError):melos_product.run_trials(run)
        self.assertEqual(list((run/'native-results').iterdir()),[])
        other=self.frozen('catalog',seeds=(0,63));path=other/'review-manifest.json';manifest=json.loads(path.read_text())
        manifest['admission']['binding_manifest']['provider_catalog']['63']['initial_hash']='0'*64;atomic_json(path,manifest)
        with self.assertRaises(ValueError):melos_product.run_trials(other)
        self.assertEqual(list((other/'native-results').iterdir()),[])

    def test_unresolved_finished_and_paused_are_distinct(self):
        record = {'observations': {'scenario_finished': True, 'status': 'unresolved',
                                  'scenario_outcome': 'decision_budget_observation_incomplete'}}
        trial = melos_product._trial(Path('trial-0000'), 0, record)
        self.assertEqual((trial['status'], trial['category']), ('observed', 'unresolved'))
        record['observations']['scenario_finished'] = False
        trial = melos_product._trial(Path('trial-0000'), 0, record)
        self.assertEqual((trial['status'], trial['category']), ('not_completed', 'not_completed'))

    def test_frozen_runtime_subprocess_preserves_portable_plan(self):
        run=self.frozen(seeds=(0,))
        script="import sys,json;sys.path.insert(0,sys.argv[1]);from execution.melos_product import run_trials;v=run_trials(sys.argv[2],should_cancel=lambda:True);print(json.dumps(v))"
        process=subprocess.run([sys.executable,'-I','-B','-c',script,str(run/'runtime'),str(run)],
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=90)
        self.assertEqual(process.returncode,0,process.stderr)
        result=json.loads(process.stdout);self.assertEqual(result['schema'],'worldbloom_independent_v3')
        self.assertEqual(result['completion'],'cancelled_partial')


if __name__ == '__main__':unittest.main()
