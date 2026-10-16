"""Actual loopback HTTP; OS worker launcher isolated, no models or external I/O."""
from unittest.mock import patch
import unittest
import test_execution_jobs as fixtures


class IndependentHttpBoundaryTests(unittest.TestCase):
    def test_real_http_capability_csrf_admission_and_idempotency(self):
        fixture=fixtures.JobTests('test_http_security_validation_and_missing_resources')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        server=fixture.server()
        status,cap=fixture.http(server,'GET','/api/independent/capabilities?config_id=cfg-test')
        self.assertEqual(status,200,cap)
        self.assertEqual(cap['genome_kind'],'none')
        self.assertEqual(cap['decision_provider_kind'],'native_base_candidates_no_genome_no_judge')
        request={'request_id':'http-independent-fixture','kind':'independent_trials','config_id':'cfg-test',
                 **{k:cap[k] for k in ('run_spec_hash','world_revision','policy_revision','settings_version','provider_revision')},
                 'seed_plan':[0],'method':'independent_trials','population':'unselected','selection':'none','reference_guided':False}
        status,body=fixture.http(server,'POST','/api/independent/jobs',request,{'Origin':'https://evil.example'})
        self.assertEqual(status,403,body)
        from execution.provenance import code_snapshot, canonical, sha256
        # ConfigStore canonicalizes its repo. Snapshot metadata includes source
        # paths, so a Windows 8.3 alias would spuriously change the revision.
        frozen_source=code_snapshot(fixture.configs.repo)
        self.assertEqual(sha256(canonical(frozen_source[1])),cap['engine_revision'])
        with patch('execution.independent_review.code_snapshot',return_value=frozen_source),patch('execution.jobs.worker.ProcessTree'),patch('execution.jobs.subprocess.Popen'),\
             patch('execution.jobs.worker.process_identity',return_value={'pid':999,'created':1,'image':'fixture'}),\
             patch('execution.jobs.worker.probe',return_value='alive'):
            status,current_cap=fixture.http(server,'GET','/api/independent/capabilities?config_id=cfg-test')
            self.assertEqual(status,200,current_cap)
            self.assertEqual(current_cap,cap)
            status,job=fixture.http(server,'POST','/api/independent/jobs',request)
            self.assertEqual(status,202,job)
            status,retry=fixture.http(server,'POST','/api/independent/jobs',request)
            self.assertEqual(status,200,retry);self.assertEqual(retry['job_id'],job['job_id'])
            status,current=fixture.http(server,'GET','/api/independent/jobs/'+job['job_id'])
            self.assertEqual(status,200,current)
            changed={**request,'seed_plan':[1]}
            status,conflict=fixture.http(server,'POST','/api/independent/jobs',changed)
            self.assertEqual(status,409,conflict)
        # No OS worker was created; retain no fake process cleanup target.
        for folder in fixture.jobs.root.glob('job-*'):
            raw=fixture.jobs._read(folder.name)
            fixture.jobs._save(raw,launch_identity=None,receipt=None,state='failed')

if __name__=='__main__':unittest.main()
