"""Initial native job/control/read route. Real native Linux/Windows bounded IO.

No restart operation, external network, LLM or GPU. API dispatch uses the real
JobStore; only OS launch is isolated except the separate connected HTTP test.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_melos_product as fixtures
from execution import melos_native_jobs as native, melos_product, independent_review, worker
from execution.jobs import JobStore
from execution.provenance import atomic_json, ConfigError, canonical, sha256
from test_independent_api import Handler
from viewer.independent_api import dispatch


class NativeJobTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.MelosProductTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.jobs=JobStore(self.fixture.store)

    def admitted(self):
        request=self.fixture.request(seeds=(0,))
        from execution.provenance import code_snapshot
        snapshot=code_snapshot(self.fixture.store.repo)
        with patch('execution.melos_product.code_snapshot',return_value=snapshot),patch('execution.jobs.worker.ProcessTree'),patch('execution.jobs.subprocess.Popen'),patch('execution.jobs.worker.process_identity',return_value={'pid':999,'created':1,'image':'fixture'}):
            job,created=self.jobs.submit(request)
        self.assertTrue(created);jid=job['job_id'];raw=self.jobs._read(jid)
        folder=self.jobs._folder(jid);independent_review.prepare_run(self.fixture.store,raw,folder)
        return request,raw,folder,self.fixture.store.runs/raw['run_id']

    def callback(self,raw,folder):
        return lambda value:native.acknowledge_control(folder.parent,folder,raw['nonce'],value)

    def test_initial_api_job_partial_reconnect_repeat_and_legacy_spoof_ignored(self):
        request,raw,folder,run=self.admitted();flag=[False]
        from scripts.independent_trials import run_trials
        result=run_trials(run,lambda value:flag.__setitem__(0,True),lambda:flag[0],acknowledge=self.callback(raw,folder))
        self.assertEqual(result['completion'],'cancelled_partial')
        with patch('execution.jobs.worker.probe',return_value='alive'):
            repeat,created=self.jobs.submit(request)
            self.assertFalse(created);self.assertEqual(repeat['job_id'],raw['job_id'])
            self.assertEqual(self.jobs.cancel(raw['job_id'])['state'],'stopping')
        self.jobs._save(self.jobs._read(raw['job_id']),state='cancelled')
        atomic_json(run/'partial-review.json',{'forged':'legacy cannot win'})
        restored=JobStore(self.fixture.store)
        with patch('execution.jobs.worker.probe',return_value='dead'):
            handler=Handler(restored,'/api/independent/jobs/'+raw['job_id']+'/result')
            self.assertTrue(dispatch(handler,['api','independent','jobs',raw['job_id'],'result'],'GET'))
        self.assertEqual(handler.reply,(200,result))
        # No worker operation accepts saved references as restart permission.
        with self.assertRaisesRegex(ValueError,'restart'):melos_product.run_trials(run,resume=True)
        before=[p.name for p in (run/'native-results').iterdir()]
        with self.assertRaises((ValueError,OSError)):melos_product.run_trials(run)
        self.assertEqual(before,[p.name for p in (run/'native-results').iterdir()])

    def test_generation_failure_retains_accepted_boundary_without_completed_bundle(self):
        run=self.fixture.frozen(seeds=(0,));refs=[];execute=native.run_melos_generation
        def fail_after_accepted(*args,**kwargs):
            execute(*args,**kwargs)
            raise RuntimeError('after accepted generation')
        with patch.object(native,'run_melos_generation',side_effect=fail_after_accepted):
            with self.assertRaisesRegex(RuntimeError,'after accepted generation'):
                melos_product.run_trials(run,acknowledge=lambda v:refs.append(v) or v)
        job={'job_id':'job-run','independent_storage':native.STORAGE,'execution_plan_sha256':refs[-1]['execution_plan_sha256'],
             'accepted_request':{'seed_plan':[0]},'native_result_refs':refs[-1]}
        result=native.read_native_result(run,job)
        self.assertEqual(result['operation_error']['phase'],'generation')
        self.assertEqual((result['completed'],result['failed']),(0,0))
        self.assertIn('last_accepted_generation',result['trials'][0]);self.assertNotIn('artifacts',result['trials'][0])

    def test_legacy_saved_reader_is_preserved_without_native_fallback(self):
        request,raw,folder,run=self.admitted()
        legacy={'schema':'worldbloom_independent_v2','id':raw['job_id'],
                'execution_plan_sha256':raw['execution_plan_sha256'],'completion':'cancelled_partial','trials':[]}
        atomic_json(run/'partial-review.json',legacy)
        self.jobs._save(raw,state='cancelled')
        with patch('execution.jobs.worker.probe',return_value='dead'):
            with self.assertRaises(ConfigError):self.jobs.independent_result(raw['job_id'])
        # A genuine historical control record lacks the new discriminator.
        old=self.jobs._read(raw['job_id']);old.pop('independent_storage');atomic_json(folder/'job.json',old)
        with patch('execution.jobs.worker.probe',return_value='dead'):
            self.assertEqual(self.jobs.independent_result(raw['job_id']),legacy)

    def test_control_ack_rejects_wrong_nonce_terminal_and_bad_references(self):
        request,raw,folder,run=self.admitted()
        refs={'schema':native.CONTROL_SCHEMA,'storage':deepcopy(native.STORAGE),'job_id':raw['job_id'],
              'execution_plan_sha256':raw['execution_plan_sha256'],'accepted':[],'receipt_accepted':[],'receipt':None}
        before=worker.read(folder/'job.json')
        with self.assertRaisesRegex(ValueError,'live job owner'):
            native.acknowledge_control(folder.parent,folder,'wrong',refs)
        self.assertEqual(before,worker.read(folder/'job.json'))
        for mutate in (lambda v:v.update(job_id='other'),lambda v:v.update(receipt={'leaf':'../x','sha256':'a'*64,'size':1}),
                       lambda v:v['storage'].update(restart_authority=0),lambda v:v['storage'].update(restart_authority=True),lambda v:v.update(accepted=[{'seed':63,'selection':{}}])):
            changed=deepcopy(refs);mutate(changed)
            with self.assertRaises(ValueError):self.callback(raw,folder)(changed)
        self.jobs._save(raw,state='failed')
        with self.assertRaisesRegex(ValueError,'live job owner'):self.callback(raw,folder)(refs)

    def test_control_ack_readback_failure_propagates_no_guess_or_retry(self):
        request,raw,folder,run=self.admitted();reads=[];original=worker.read
        refs={'schema':native.CONTROL_SCHEMA,'storage':deepcopy(native.STORAGE),'job_id':raw['job_id'],
              'execution_plan_sha256':raw['execution_plan_sha256'],'accepted':[],'receipt_accepted':[],'receipt':None}
        def changed(path):
            value=original(path);reads.append(path)
            if len(reads)==2:value['revision']+=1
            return value
        with patch.object(worker,'read',side_effect=changed),patch.object(worker,'atomic',wraps=worker.atomic) as writes:
            with self.assertRaisesRegex(ValueError,'readback'):self.callback(raw,folder)(refs)
        self.assertEqual(writes.call_count,1);self.assertEqual(len(reads),2)
        self.assertIn('native_result_refs',original(folder/'job.json')) # uncertain from caller, never retry

    def test_control_ack_failure_stops_before_exports_and_preserves_accepted_reference(self):
        run=self.fixture.frozen(seeds=(0,));seen=[]
        def refuse(value):seen.append(value);raise RuntimeError('ack refused')
        with patch.object(native,'publish_native_exports',side_effect=AssertionError('export after failed ack')):
            with self.assertRaisesRegex(RuntimeError,'ack refused'):
                melos_product.run_trials(run,acknowledge=refuse)
        self.assertEqual(len(seen),1);self.assertEqual(len(seen[0]['accepted']),1)
        self.assertIsNone(seen[0]['receipt'])
        self.assertFalse(any(p.name.startswith('wbexport-') for p in (run/'native-results').iterdir()))

    def test_one_factory_owner_and_explicit_selection_across_chunks(self):
        run=self.fixture.frozen(seeds=(0,));seen=[];projected=[];progress=[]
        execute=native.run_melos_generation;capture=native._captured_generation
        def observe(session,owner,**kwargs):
            outcome=execute(session,owner,**kwargs)
            seen.append((session.stage._captured_factory,owner,kwargs['selection'],outcome.selection))
            return outcome
        def observe_capture(owner,selection):projected.append(owner);return capture(owner,selection)
        with patch.object(native,'run_melos_generation',side_effect=observe),patch.object(native,'_captured_generation',side_effect=observe_capture):
            result=melos_product.run_trials(run,progress.append,lambda:len(progress)>=2)
        self.assertEqual(len(seen),2);self.assertIs(seen[0][0],seen[1][0]);self.assertIs(seen[0][1],seen[1][1])
        self.assertIsNone(seen[0][2]);self.assertEqual(seen[1][2],seen[0][3])
        self.assertTrue(all(o is seen[0][1] for o in projected))
        self.assertEqual(result['trials'][0]['selected_generation'],native.selection_value(seen[-1][3]))
        self.assertEqual(seen[0][1].state,'CLOSED')

    def test_export_failure_after_prior_bundle_keeps_last_bundle_separate(self):
        run=self.fixture.frozen(seeds=(0,));refs=[];count=[0];publish=native.publish_native_exports
        def fail_second(*args):
            count[0]+=1
            if count[0]==2:raise RuntimeError('second bundle failed')
            return publish(*args)
        with patch.object(native,'publish_native_exports',side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError,'second bundle failed'):
                melos_product.run_trials(run,acknowledge=lambda v:refs.append(v) or v)
        job={'job_id':'job-run','independent_storage':native.STORAGE,'execution_plan_sha256':refs[-1]['execution_plan_sha256'],
             'accepted_request':{'seed_plan':[0]},'native_result_refs':refs[-1]}
        result=native.read_native_result(run,job);trial=result['trials'][0]
        self.assertNotEqual(trial['selected_generation'],trial['last_accepted_generation'])
        self.assertEqual(trial['run_record']['observed_through'],120)
        self.assertEqual((result['completed'],result['failed'],result['completion']),(0,0,'failed_partial'))

    def test_reader_rejects_artifact_tampering_without_manifest_inference(self):
        run=self.fixture.frozen(seeds=(0,));refs=[];flag=[False]
        result=melos_product.run_trials(run,lambda v:flag.__setitem__(0,True),lambda:flag[0],acknowledge=lambda v:refs.append(v) or v)
        job={'job_id':'job-run','independent_storage':native.STORAGE,'execution_plan_sha256':refs[-1]['execution_plan_sha256'],
             'accepted_request':{'seed_plan':[0]},'native_result_refs':refs[-1]}
        # Reader never needs adjacent manifest/config/input paths.
        (run/'review-manifest.json').write_bytes(b'not authoritative')
        self.assertEqual(native.read_native_result(run,job),result)
        path=run/'native-results'/result['trials'][0]['artifacts']['layers']['leaf'];original=path.read_bytes()
        path.write_bytes(original+b'changed')
        with self.assertRaises(ValueError):native.read_native_result(run,job)
        path.write_bytes(original)
        for mutate in (lambda v:v['receipt'].update(leaf='../bad'),lambda v:v['receipt'].update(size=True),
                       lambda v:v['receipt'].update(sha256='0'*64),lambda v:v['storage'].update(kind='legacy')):
            altered=deepcopy(job);mutate(altered['native_result_refs'])
            with self.assertRaises(ValueError):native.read_native_result(run,altered)

    def test_source_genome_admission_and_output_alias_fail_before_native_writes(self):
        for kind in ('source','genome','alias'):
            with self.subTest(kind=kind):
                run=self.fixture.frozen(kind,seeds=(0,))
                if kind=='source':
                    path=run/'runtime/execution/approved_melos_sources/melos_runner.py';path.write_bytes(path.read_bytes()+b'\n# drift')
                elif kind=='genome':
                    path=run/'review-manifest.json';value=json.loads(path.read_text());value['config']['execution_profile']['genome']={};atomic_json(path,value)
                else:
                    (run/'native-results').rmdir()
                    try:(run/'native-results').symlink_to(run/'inputs',target_is_directory=True)
                    except OSError:self.skipTest('directory symlink privilege unavailable')
                with self.assertRaises((ValueError,OSError)):melos_product.run_trials(run)
                self.assertFalse((run/'inputs/wbjobs-initial-claim.json').exists())

    def test_no_new_discriminator_means_legacy_read_only_not_auto_migration(self):
        run=self.fixture.frozen(seeds=(0,));path=run/'review-manifest.json';manifest=json.loads(path.read_text())
        del manifest['storage'];atomic_json(path,manifest)
        with self.assertRaisesRegex(ValueError,'read-only'):melos_product.run_trials(run)
        self.assertEqual(list((run/'native-results').iterdir()),[])


if __name__=='__main__':unittest.main()
