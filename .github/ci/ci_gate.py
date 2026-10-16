"""One required policy gate: planned work, metadata and census must all agree."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT)]
from ci_plan import digest,policy,ENVIRONMENTS,fixed_inputs,make_plan,git,parse_diff
from ci_selected import inventory,validate_selected

def validate_plan(plan,*,inputs,cfg,load_pair=None):
    value=dict(plan);claimed=value.pop('plan_sha256')
    if claimed!=digest(value) or plan['inputs']!=inputs or plan['policy_sha256']!=digest(cfg):
        raise ValueError('plan-input-policy-or-digest-mismatch')
    tier=plan['effective_tier']
    if type(plan['schema']) is not int or plan['schema']!=1 or type(plan['diff_complete']) is not bool:
        raise ValueError('invalid-plan-schema')
    if tier not in ('basic','windows','full','postmerge'):raise ValueError('unknown-tier')
    if inputs['event']=='pull_request' and inputs['target']=='main' and tier!='full':raise ValueError('main-must-be-full')
    if cfg['develop_mode']=='transition-full' and tier!='full':raise ValueError('transition-must-be-full')
    if not plan['diff_complete'] and tier!='full':raise ValueError('incomplete-diff-must-be-full')
    if inputs['event']=='push' and tier not in ('full','postmerge'):raise ValueError('push-must-be-proven-or-full')
    if tier=='postmerge' and (inputs['event']!='push' or not plan['receipt_proof'] or
       plan['receipt_proof']['merged_sha']!=inputs['tested_sha'] or
       plan['receipt_proof']['tested_tree']!=inputs['tested_tree']):raise ValueError('invalid-postmerge-proof')
    expected={'basic':True,'secrets':True,'full_environments':ENVIRONMENTS if tier=='full' else [],
              'windows_environments':ENVIRONMENTS[2:] if tier=='windows' else []}
    if (plan['required']!=expected or plan['required']['basic'] is not True or plan['required']['secrets'] is not True or
        plan['modules']!={'basic':cfg['basic_modules'],'windows':cfg['windows_modules']}):
        raise ValueError('required-work-or-modules-mismatch')
    if load_pair is None:
        load_pair=lambda name:(git('show',inputs['merge_base']+':'+name),git('show',inputs['head_sha']+':'+name))
    derived=make_plan(inputs,plan['changes'],cfg,load_pair,
                      diff_error=None if plan['diff_complete'] else 'gate-full-fallback',receipt=plan['receipt_proof'])
    if derived['effective_tier']!=tier or derived['requested_tier']!=plan['requested_tier']:
        raise ValueError('classification-tier-mismatch')
    return expected

def validate_needs(needs,required):
    expected={'classify':'success','basic':'success','secrets':'success',
              'windows-related':'success' if required['windows_environments'] else 'skipped',
              'regression':'success' if required['full_environments'] else 'skipped'}
    actual={name:value.get('result') if isinstance(value,dict) else value for name,value in needs.items()}
    if actual!=expected:raise ValueError('failed-cancelled-skipped-missing-or-unexpected-job')

def validate_evidence(plan,needs,*,selected_reports,full_reports,expected_ids):
    required=plan['required'];validate_needs(needs,required)
    expected_pairs={('basic','windows-latest-py3.13')}
    expected_pairs.update(('windows',env) for env in required['windows_environments'])
    pairs=[(r['profile'],r['environment']) for r in selected_reports]
    if len(pairs)!=len(set(pairs)) or set(pairs)!=expected_pairs:raise ValueError('missing-duplicate-or-extra-selected-report')
    verified=[]
    for report in selected_reports:
        profile,environment=report['profile'],report['environment']
        verified.append(validate_selected(report,head=plan['inputs']['tested_sha'],environment=environment,
                                         profile=profile,expected=expected_ids[profile]))
    environments=[r['environment'] for r in full_reports]
    if len(environments)!=len(set(environments)) or set(environments)!=set(required['full_environments']):
        raise ValueError('missing-duplicate-or-extra-full-census')
    for report in full_reports:
        if (report['head']!=plan['inputs']['tested_sha'] or report['complete_census_verified'] is not True or
            type(report['tests_run']) is not int or report['tests_run']!=len(expected_ids['full']) or report['failures'] or report['errors'] or
            report['census_sha256']!=__import__('hashlib').sha256(json.dumps(expected_ids['full']).encode()).hexdigest()):
            raise ValueError('full-census-source-count-or-digest-mismatch')
    return verified

def read(path):return json.loads(path.read_text(encoding='utf-8'))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True);args=parser.parse_args()
    paths=list(args.evidence.rglob('policy-plan.json'))
    if len(paths)!=1:raise ValueError('missing-or-duplicate-plan')
    plan=read(paths[0]);event=read(Path(os.environ['GITHUB_EVENT_PATH']))
    inputs=fixed_inputs(event,os.environ['GITHUB_EVENT_NAME'],os.environ['GITHUB_SHA'])
    cfg=policy();required=validate_plan(plan,inputs=inputs,cfg=cfg)
    if plan['diff_complete']:
        rows=parse_diff(git('diff','--name-status','-z','--find-renames=50%',inputs['merge_base'],inputs['head_sha'],'--'),
                        max_bytes=cfg['max_diff_bytes'],max_files=cfg['max_diff_files'])
        if rows!=plan['changes']:raise ValueError('changed-file-list-mismatch')
    if plan['effective_tier']=='postmerge':
        from ci_proof import find_receipt
        proof=find_receipt(inputs,os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_TOKEN'])
        for key in ('tested_tree','base_sha','head_sha','merged_sha'):
            if proof[key]!=plan['receipt_proof'][key]:raise ValueError('postmerge-proof-no-longer-verifiable')
    needs=json.loads(os.environ['WB_NEEDS']);validate_needs(needs,required)
    reports=[]
    for path in args.evidence.rglob('selected-result.json'):
        report=read(path);report['outcomes']=read(path.with_name('selected-outcomes.json'));reports.append(report)
    from regression_partition import flatten
    import unittest
    loader=unittest.TestLoader();full=list(flatten(loader.discover(str(ROOT/'tests'))))
    if loader.errors:raise ValueError('gate-discovery-failed')
    ids={'full':[test.id() for test in full]}
    for profile in ('basic','windows'):
        ids[profile]=[test.id() for test in full if test.id().split('.')[0] in cfg[profile+'_modules']]
        if {test.split('.')[0] for test in ids[profile]}!=set(cfg[profile+'_modules']):raise ValueError('gate-module-missing')
    censuses=[read(path) for path in args.evidence.rglob('census-verification.json')]
    verified=validate_evidence(plan,needs,selected_reports=reports,full_reports=censuses,expected_ids=ids)
    receipt={'schema':1,'inputs':inputs,'plan_sha256':plan['plan_sha256'],'effective_tier':plan['effective_tier'],
             'outcome':'success','complete_census_verified':True,'basic_count':len(ids['basic']),
             'full_environments':required['full_environments'],'windows_environments':required['windows_environments'],
             'selected_censuses':verified,'full_censuses':censuses}
    # Keep post-merge proof archives small and bounded. Full IDs stay in reports.
    raw=json.dumps(receipt,indent=2).encode()
    if len(raw)>65536:raise ValueError('receipt-byte-limit')
    args.receipt.write_bytes(raw)
    print(json.dumps({'outcome':'success','tier':plan['effective_tier'],'inputs':inputs,'censuses':verified}))

if __name__=='__main__':
    try:main()
    except Exception as error:
        # Do not publish transport URLs, credentials or arbitrary error strings.
        print(json.dumps({'ci_policy_error':type(error).__name__}))
        raise SystemExit(1)
