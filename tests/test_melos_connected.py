"""Bounded actual cloud-loopback API and owned frozen worker, seed 0 only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest
import test_execution_jobs as fixtures

ROOT=Path(__file__).resolve().parents[1]

class MelosConnectedTests(unittest.TestCase):
    def test_javascript_spec_http_job_frozen_worker_and_ui_receipt(self):
        fixture=fixtures.JobTests('test_http_security_validation_and_missing_resources')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        server=fixture.server()
        spec=json.loads(subprocess.check_output(['node','-e',"console.log(JSON.stringify(require('./viewer/static/purpose-independent.js').melosProfileSpec()))"],cwd=ROOT,text=True))
        status,body=fixture.http(server,'POST','/api/configs',spec,{'Origin':'https://evil.example'})
        self.assertEqual(status,403,body)
        status,config=fixture.http(server,'POST','/api/configs',spec)
        self.assertEqual(status,201,config)
        status,cap=fixture.http(server,'GET','/api/independent/capabilities?config_id='+config['config_id'])
        self.assertEqual(status,200,cap);self.assertTrue(cap['supported']);self.assertEqual(cap['time_unit'],'minute')
        request={'request_id':'review-melos-connected','kind':'independent_trials','config_id':config['config_id'],
                 **{k:cap[k] for k in ['run_spec_hash','world_revision','policy_revision','settings_version','provider_revision']},
                 'seed_plan':[0],'method':'independent_trials','population':'unselected','selection':'none','reference_guided':False}
        status,body=fixture.http(server,'POST','/api/independent/jobs',request,{'Origin':'https://evil.example'})
        self.assertEqual(status,403,body)
        status,job=fixture.http(server,'POST','/api/independent/jobs',request)
        self.assertEqual(status,202,job)
        status,retry=fixture.http(server,'POST','/api/independent/jobs',request)
        self.assertEqual(status,200,retry);self.assertEqual(retry['job_id'],job['job_id'])
        status,conflict=fixture.http(server,'POST','/api/independent/jobs',{**request,'seed_plan':[63]})
        self.assertEqual(status,409,conflict)
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            status,current=fixture.http(server,'GET','/api/independent/jobs/'+job['job_id'])
            self.assertEqual(status,200,current)
            if current['state'] in ('succeeded','failed','cancelled','interrupted') and current['termination_confirmed']:break
            time.sleep(.2)
        self.assertEqual(current['state'],'succeeded',current)
        self.assertTrue(current['termination_confirmed'],current)
        status,result=fixture.http(server,'GET','/api/independent/jobs/'+job['job_id']+'/result')
        self.assertEqual(status,200,result);self.assertEqual(result['completed'],1);self.assertEqual(result['failed'],0)
        record=result['trials'][0]['run_record'];self.assertTrue(record['product_profile_active']);self.assertIsNone(record['verified_execution'])
        self.assertTrue(record['observations']['fixed_two_minute_report']['completed'])
        code="const fs=require('fs'),crypto=require('crypto'),a=require('./viewer/static/purpose-workspace.js');const r=JSON.parse(fs.readFileSync(0,'utf8'));a.verifyRunRecord(r,s=>crypto.createHash('sha256').update(s).digest('hex')).then(()=>console.log(JSON.stringify(a.melosObservationCounts(r.trials)))).catch(e=>{console.error(e);process.exitCode=1;});"
        checked=subprocess.run(['node','-e',code],cwd=ROOT,input=json.dumps(result),text=True,capture_output=True)
        self.assertEqual(checked.returncode,0,checked.stderr)
        self.assertEqual(json.loads(checked.stdout),{'physical_arrival':1,'report_completed':1,'wedding_completed':0,'missing_records':0})
        if os.environ.get('MELOS_CONNECTED_EVIDENCE'):
            destination=Path(os.environ['MELOS_CONNECTED_EVIDENCE']);destination.mkdir(parents=True,exist_ok=True)
            (destination/'http-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            (destination/'job-final.json').write_text(json.dumps(current,ensure_ascii=False,indent=2)+'\n')
            shutil.copytree(fixture.configs.runs/job['run_id'],destination/'frozen-run',dirs_exist_ok=True)

if __name__=='__main__':unittest.main()
