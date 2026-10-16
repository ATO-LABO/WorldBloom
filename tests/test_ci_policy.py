"""Meaningful fail-closed classifier/gate/receipt tests; no GA or network."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.github/ci'))
from ci_plan import parse_diff, classify_changes, windows_only, make_plan, digest, ENVIRONMENTS
from ci_gate import validate_plan, validate_needs, validate_evidence
from ci_proof import validate_receipt, NoCredentialRedirect, get,find_receipt
from ci_contract import validate,workflow

def cfg(mode='tiered'):
    return {'schema':1,'develop_mode':mode,'basic_modules':['test_a'],'windows_modules':['test_w']}
def inputs(event='pull_request',target='develop'):
    return {'event':event,'target':target,'base_sha':'a'*40,'head_sha':'b'*40,
        'tested_sha':'c'*40,'tested_tree':'d'*40,'parents':['a'*40,'b'*40],
        'merge_base':'a'*40,'pr_number':9,'run_id':10,'run_attempt':1}
def docs():return [{'status':'M','paths':['docs/guide.md']}]
def plan(tier='basic'):
    rows=docs() if tier=='basic' else [{'status':'M','paths':['execution/provenance.py']}]
    result=make_plan(inputs(),rows,cfg(),lambda name:None)
    if tier=='windows':
        result=make_plan(inputs(),[{'status':'M','paths':['viewer/build_exe.cmd']}],cfg(),lambda name:None)
    return result
def needs(tier='basic'):
    return {name:{'result':value} for name,value in {
        'classify':'success','basic':'success','secrets':'success',
        'windows-related':'success' if tier=='windows' else 'skipped',
        'regression':'success' if tier=='full' else 'skipped'}.items()}
IDS={'basic':['test_a.Case.test_one','test_a.Case.test_one'],
     'windows':['test_w.Case.test_win'],'full':['test_a.Case.test_one','test_a.Case.test_one','test_w.Case.test_win']}
def selected(profile='basic',environment='windows-latest-py3.13'):
    return {'head':'c'*40,'environment':environment,'profile':profile,'python':[3,int(environment[-2:]),0],
        'platform':'win32','selected_ids':IDS[profile][:],'completed_ids':IDS[profile][:],
        'tests_run':len(IDS[profile]),'completed_normally':True,'success':True,'failures':0,'errors':0,
        'loader_errors':[],'skips':1,'outcomes':dict(python='success',syntax='success',javascript='success') if profile=='basic' else {'python':'success'}}
def full_reports():
    return [{'head':'c'*40,'environment':env,'complete_census_verified':True,'tests_run':len(IDS['full']),
        'failures':0,'errors':0,'census_sha256':hashlib.sha256(json.dumps(IDS['full']).encode()).hexdigest()}
        for env in ENVIRONMENTS]

class ClassificationTests(unittest.TestCase):
    def test_documents_are_basic_and_unknown_or_shared_files_full(self):
        for name in ('docs/guide.md','docsite/stylesheets/extra.css','README.md'):
            self.assertEqual(classify_changes([{'status':'M','paths':[name]}],lambda name:None)[0],'basic')
        for name in ('engine/state.py','execution/provenance.py','requirements.txt','templates/x.yaml',
                     'projects/a/world.yaml','.github/ci/policy.json','.github/workflows/tests.yml',
                     'tests/test_something.py','docs/strange.xyz','viewer/static/raw-workspace.js'):
            with self.subTest(name=name):
                self.assertEqual(classify_changes([{'status':'M','paths':[name]}],lambda name:None)[0],'full')

    def test_add_delete_rename_copy_type_changes_always_full(self):
        for status in ('A','D','R100','C99','T','U','X','B'):
            paths=['docs/guide.md','docs/renamed.md'] if status[0] in 'RC' else ['docs/guide.md']
            self.assertEqual(classify_changes([{'status':status,'paths':paths}],lambda name:None)[0],'full')

    def test_diff_preserves_both_rename_paths_and_unicode(self):
        rows=parse_diff('R100\0docs/旧.md\0docs/新.md\0M\0docs/guide.md\0'.encode(),max_bytes=1000,max_files=5)
        self.assertEqual(rows[0]['paths'],['docs/旧.md','docs/新.md'])
        self.assertEqual(len(rows),2)

    def test_truncation_limits_invalid_status_encoding_and_paths_rejected(self):
        for raw,limits in ((b'M\0docs/x.md',{}),(b'Z\0a\0',{}),(b'M\0../x\0',{}),
            (b'M\0C:\\secret\0',{}),(b'M\0\xff\0',{}),(b'M\0a\0',{'max_bytes':2}),
            (b'M\0a\0M\0b\0',{'max_files':1}),(b'R100\0a\0',{}),(b'',{})):
            with self.subTest(raw=raw),self.assertRaises((ValueError,UnicodeError)):
                parse_diff(raw,**({'max_bytes':1000,'max_files':5}|limits))

    def test_only_audited_windows_body_changes_are_narrowed(self):
        old=b'def kernel():\n return 1\n\nclass _WindowsProcessTree:\n def finish(self):\n  return 2\n\ndef common():\n return 3\n'
        win=old.replace(b'return 2',b'return 4')
        self.assertTrue(windows_only(old,win))
        self.assertEqual(classify_changes([{'status':'M','paths':['execution/worker.py']}],lambda name:(old,win))[0],'windows')
        for new in (old.replace(b'return 3',b'return 9'),old.replace(b'finish(self)',b'finish(self, flag=1)'),
                    b'import os\n'+win,win.replace(b'class _WindowsProcessTree:',b'class _WindowsProcessTree(object):'),
                    old,old+b'\ndef unknown():\n return 1\n'):
            self.assertFalse(windows_only(old,new))

    def test_main_transition_errors_and_push_without_receipt_are_full(self):
        for inp,config,error in ((inputs(target='main'),cfg(),None),(inputs(target='feature/work'),cfg(),None),
            (inputs(),cfg('transition-full'),None),(inputs(),cfg(),'diff-error'),
            (inputs('push'),cfg(),None)):
            self.assertEqual(make_plan(inp,docs(),config,lambda name:None,diff_error=error)['effective_tier'],'full')

    def test_push_can_be_short_only_with_proof_and_main_PR_never_reuses(self):
        proof={'merged_sha':'c'*40,'tested_tree':'d'*40}
        self.assertEqual(make_plan(inputs('push'),docs(),cfg(),lambda name:None,receipt=proof)['effective_tier'],'postmerge')
        self.assertEqual(make_plan(inputs('push'),[],cfg(),lambda name:None,receipt=proof,diff_error='truncated-diff')['effective_tier'],'full')
        self.assertEqual(make_plan(inputs(target='main'),docs(),cfg(),lambda name:None,receipt=proof)['effective_tier'],'full')

class GateTests(unittest.TestCase):
    def verify(self,p=None,reports=None,censuses=None,state=None):
        p=p or plan();return validate_evidence(p,state or needs(p['effective_tier']),
            selected_reports=reports if reports is not None else [selected()],
            full_reports=censuses if censuses is not None else [],expected_ids=IDS)

    def test_basic_windows_full_success_and_normal_test_skip(self):
        self.assertEqual(self.verify()[0]['tests_run'],2)
        windows=[selected(),selected('windows','windows-latest-py3.12'),selected('windows')]
        self.assertEqual(len(self.verify(plan('windows'),windows)),3)
        self.assertEqual(len(self.verify(plan('full'),censuses=full_reports())),1)

    def test_failed_cancelled_skipped_missing_and_unexpected_jobs_fail(self):
        for name in needs():
            for outcome in ('failure','cancelled','missing','skipped','success'):
                state=needs()
                if state[name]['result']==outcome:continue
                state[name]['result']=outcome
                with self.subTest(name=name,outcome=outcome),self.assertRaises(ValueError):self.verify(state=state)
        state=needs();state.pop('classify')
        with self.assertRaises(ValueError):self.verify(state=state)

    def test_missing_duplicate_extra_incomplete_wrong_input_and_step_skip_fail(self):
        for reports in ([],[selected(),selected()],[selected(),selected('windows')]):
            with self.assertRaises(ValueError):self.verify(reports=reports)
        for edits in ({'head':'old'},{'completed_ids':IDS['basic'][:1]},{'tests_run':1},
            {'selected_ids':IDS['basic'][:1]},{'success':False},{'success':'true'},{'completed_normally':False},
            {'completed_normally':'true'},{'tests_run':2.0},{'skips':-1},
            {'failures':1},{'errors':1},{'loader_errors':['failed']},{'python':[3,12,0]},
            {'platform':'linux'},{'outcomes':{'python':'success','syntax':'skipped','javascript':'success'}}):
            report=selected();report.update(edits)
            with self.subTest(edits=edits),self.assertRaises(ValueError):self.verify(reports=[report])

    def test_every_full_environment_and_full_count_digest_are_required(self):
        p=plan('full')
        for reports in (full_reports()[:3],full_reports()+[full_reports()[0]],[]):
            with self.assertRaises(ValueError):self.verify(p,censuses=reports)
        for edits in ({'head':'old'},{'tests_run':2},{'census_sha256':'wrong'},
                      {'complete_census_verified':False},{'errors':1}):
            reports=full_reports();reports[0].update(edits)
            with self.assertRaises(ValueError):self.verify(p,censuses=reports)

    def test_plan_policy_inputs_required_work_or_digest_tampering_fail(self):
        p=plan();validate_plan(p,inputs=inputs(),cfg=cfg())
        for edit in ('digest','head','policy','required','tier'):
            value=deepcopy(p)
            if edit=='digest':value['plan_sha256']='0'*64
            if edit=='head':value['inputs']['tested_sha']='e'*40
            if edit=='policy':value['policy_sha256']='0'*64
            if edit=='required':value['required']['basic']=False
            if edit=='tier':value['effective_tier']='full'
            with self.subTest(edit=edit),self.assertRaises(ValueError):validate_plan(value,inputs=inputs(),cfg=cfg())
        p=plan('full');p['effective_tier']='basic';p['requested_tier']='basic';p['required']=plan()['required']
        p.pop('plan_sha256');p['plan_sha256']=digest(p)
        with self.assertRaises(ValueError):validate_plan(p,inputs=inputs(),cfg=cfg())

    def test_workflow_contract_preserves_required_names_and_catches_gate_skip(self):
        value=workflow(ROOT/'.github/workflows/tests.yml')
        names=validate(value)['required_main_contexts']
        self.assertEqual(len(names),4)
        self.assertIn('regression (windows-latest, 3.13)',names)
        value['jobs']['ci-policy']['if']='success()'
        with self.assertRaises(AssertionError):validate(value)
        value=workflow(ROOT/'.github/workflows/tests.yml')
        value['jobs']['regression']['if']="always() && needs.classify.outputs.full == 'true'"
        with self.assertRaises(AssertionError):validate(value)

class ReceiptTests(unittest.TestCase):
    def fixture(self):
        source=inputs();receipt={'schema':1,'inputs':source,'outcome':'success','effective_tier':'full',
            'complete_census_verified':True,'basic_count':2,'full_environments':ENVIRONMENTS,
            'windows_environments':[],'plan_sha256':'e'*64}
        return receipt,inputs('push'),{'number':9,'head':{'sha':'b'*40}},{'id':10,'run_attempt':1}

    def test_merge_tree_and_fixed_base_head_are_required_not_just_green_head(self):
        receipt,push,pr,run=self.fixture()
        self.assertEqual(validate_receipt(receipt,inputs=push,pr=pr,run=run)['pr'],9)
        for field in ('tested_tree','base_sha','head_sha','tested_sha'):
            value=deepcopy(receipt);value['inputs'][field]='wrong'
            with self.subTest(field=field),self.assertRaises(ValueError):validate_receipt(value,inputs=push,pr=pr,run=run)
        for changes in ({'run_attempt':2},{'id':11}):
            with self.assertRaises(ValueError):validate_receipt(receipt,inputs=push,pr=pr,run=run|changes)

    def test_incomplete_census_or_main_nonfull_receipt_is_rejected(self):
        receipt,push,pr,run=self.fixture()
        for change in ({'complete_census_verified':False},{'basic_count':0},{'full_environments':ENVIRONMENTS[:3]},
                       {'outcome':'skipped'}):
            with self.assertRaises(ValueError):validate_receipt(receipt|change,inputs=push,pr=pr,run=run)
        receipt['inputs']['target']='main';push['target']='main'
        receipt.update(effective_tier='basic',full_environments=[])
        with self.assertRaises(ValueError):validate_receipt(receipt,inputs=push,pr=pr,run=run)

    def test_redirect_never_forwards_token_to_artifact_host(self):
        request=urllib.request.Request('https://api.github.com/test',headers={'Authorization':'Bearer synthetic'})
        handler=NoCredentialRedirect()
        redirected=handler.redirect_request(request,None,302,'',{},'https://example.invalid/archive.zip')
        self.assertIsNone(redirected.get_header('Authorization'))
        with self.assertRaises(ValueError):handler.redirect_request(request,None,302,'',{},'http://example.invalid/a')

    def test_API_pagination_and_oversize_responses_fail_closed(self):
        with patch('ci_proof.urllib.request.build_opener') as opener:
            response=opener.return_value.open.return_value.__enter__.return_value
            response.headers={'Link':'<https://api.github.com/next>; rel="next"'}
            with self.assertRaises(ValueError):get('repos/example/repository/actions/runs','synthetic')
            response.headers={};response.read.return_value=b'x'*131073
            with self.assertRaises(ValueError):get('repos/example/repository/actions/artifacts/1/zip','synthetic',binary=True)

    def test_missing_or_skipped_policy_job_never_deduplicates_push(self):
        receipt,push,pr,run=self.fixture()
        pull=pr|{'merged_at':'2026-10-16T00:00:00Z','merge_commit_sha':push['tested_sha'],
            'base':{'ref':'develop','repo':{'full_name':'owner/repo'}}}
        execution=run|{'path':'.github/workflows/tests.yml','event':'pull_request',
                      'conclusion':'success','head_sha':'b'*40}
        for jobs in ([],[{'name':'ci-policy','status':'completed','conclusion':'skipped'}]):
            with patch('ci_proof.get',side_effect=[[pull],{'total_count':1,'workflow_runs':[execution]},
                            {'total_count':len(jobs),'jobs':jobs}]):
                with self.assertRaises(ValueError):find_receipt(push,'owner/repo','synthetic')

if __name__=='__main__':unittest.main()
