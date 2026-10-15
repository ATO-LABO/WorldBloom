from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
import tempfile,unittest,json
from execution.configs import ConfigStore
from execution.jobs import JobStore
from execution.independent_review import capability,admit,prepare_run
from execution.provenance import ConfigError

ROOT=Path(__file__).resolve().parents[1]
class IndependentJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.configs=ConfigStore(ROOT,self.base/'control',self.base/'runs')
        self.config=self.configs.save({'label':'CPU independent fixture','project_id':'momotaro','template_id':'momotaro',
                                      'evolution':{'population':1,'generations':1,'seeds':1,'processes':1,'kappa':0,'route_rho':0}})
        self.jobs=JobStore(self.configs)
        from execution.provenance import code_snapshot
        self.source_snapshot=code_snapshot(ROOT)
    def request(self):
        cap=capability(self.configs,self.config['config_id'])
        return {'request_id':'fixture-review','kind':'independent_trials','config_id':cap['config_id'],
                **{k:cap[k] for k in ['run_spec_hash','world_revision','policy_revision','settings_version','provider_revision']},
                'seed_plan':[0,1],'method':'independent_trials','population':'unselected','selection':'none','reference_guided':False}
    def submit_without_process(self,request):
        # Exercise the real durable admission, with the OS launcher isolated.
        with patch('execution.jobs.worker.ProcessTree') as tree,patch('execution.jobs.subprocess.Popen') as spawn,patch('execution.jobs.worker.process_identity',return_value={'pid':999,'created':1,'image':'fixture'}),patch('execution.independent_review.code_snapshot',return_value=self.source_snapshot):
            return self.jobs.submit(request)
    def test_duplicate_request_same_job_and_changed_payload_conflict(self):
        request=self.request();one,created=self.submit_without_process(request)
        self.assertTrue(created)
        with patch('execution.jobs.worker.probe',return_value='alive'):
            two,created=self.submit_without_process(request)
        self.assertFalse(created);self.assertEqual(one['job_id'],two['job_id'])
        self.assertEqual(one['accepted_request'],request)
        changed=deepcopy(request);changed['seed_plan']=[0]
        with self.assertRaises(ConfigError):self.submit_without_process(changed)
    def test_stale_revisions_and_canon_feedback_refuse(self):
        request=self.request();request['world_revision']='0'*64
        with self.assertRaises(ConfigError):self.submit_without_process(request)
        request=self.request();request['reference_guided']=True
        with self.assertRaises(ConfigError):self.submit_without_process(request)
    def test_cancel_is_requested_until_owner_termination_is_confirmed(self):
        job,_=self.submit_without_process(self.request());jid=job['job_id']
        with patch('execution.jobs.worker.probe',return_value='alive'):
            cancelled=self.jobs.cancel(jid)
        self.assertEqual(cancelled['state'],'stopping');self.assertFalse(cancelled['termination_confirmed'])
        raw=self.jobs._read(jid)
        self.jobs._save(raw,state='cancelled')
        with patch('execution.jobs.worker.probe',return_value='alive'):
            self.assertEqual(self.jobs.get(jid)['state'],'stopping')
        with patch('execution.jobs.worker.probe',return_value='dead'):
            self.assertTrue(self.jobs.get(jid)['termination_confirmed'])
    def test_frozen_prepare_and_direct_worker_call_reach_common_kernel(self):
        job,_=self.submit_without_process(self.request())
        folder=self.jobs._folder(job['job_id']);raw=self.jobs._read(job['job_id'])
        launch=prepare_run(self.configs,raw,folder)
        self.assertEqual(launch['argv'][1:3],['-I','-B'])
        self.assertIn('independent_trials.py',launch['argv'][3])
        from scripts.independent_trials import run_trials
        progress=[];result=run_trials(self.configs.runs/raw['run_id'],progress.append)
        self.assertEqual([t['seed'] for t in result['trials']],[0,1])
        self.assertTrue(all(t['status']=='observed' for t in result['trials']),result['trials'])
        self.assertEqual(progress[-1]['completed'],2)
        for t in result['trials']:
            self.assertEqual(t['run_record']['verified_execution']['execution_kind'],'independent_trials')
        self.jobs._save(self.jobs._read(job['job_id']),state='succeeded')
        with patch('execution.jobs.worker.probe',return_value='dead'):
            self.assertEqual(self.jobs.independent_result(job['job_id']),result)
