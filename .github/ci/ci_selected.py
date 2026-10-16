"""Run a complete selected-module census without changing existing tests."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT)]
from ci_plan import digest,policy
from regression_partition import flatten,RecordingResult

def inventory(modules):
    loader=unittest.TestLoader()
    tests=list(flatten(loader.discover(str(ROOT/'tests'))))
    if loader.errors:raise ValueError('failed-discovery')
    selected=[test for test in tests if test.id().split('.')[0] in modules]
    if {test.id().split('.')[0] for test in selected}!=set(modules):raise ValueError('missing-selected-module')
    if not selected:raise ValueError('empty-selected-census')
    return selected

def validate_selected(report,*,head,environment,profile,expected):
    expected_python=list(map(int,environment.rsplit('-py',1)[1].split('.')))
    expected_platform='win32' if environment.startswith('windows-') else 'linux'
    if (report['head']!=head or report['environment']!=environment or report['profile']!=profile or
        report['python'][:2]!=expected_python or report['platform']!=expected_platform or
        report['selected_ids']!=expected or report['completed_ids']!=expected or
        type(report['tests_run']) is not int or report['tests_run']!=len(expected) or
        report['completed_normally'] is not True or report['success'] is not True or
        report['failures'] or report['errors'] or report['loader_errors']!=[] or not expected or
        type(report['skips']) is not int or not 0<=report['skips']<=report['tests_run']):
        raise ValueError('incomplete-or-mismatched-selected-census')
    outcomes={'python':'success'}
    if profile=='basic':outcomes.update(syntax='success',javascript='success')
    if report['outcomes']!=outcomes:raise ValueError('required-selected-step-failed-or-skipped')
    return {'head':head,'environment':environment,'profile':profile,'tests_run':len(expected),
            'census_sha256':digest(expected),'skips':report['skips'],'complete_census_verified':True}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('profile',choices=('basic','windows'))
    parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    selected=inventory(policy()[args.profile+'_modules']);ids=[test.id() for test in selected]
    report={'head':os.environ.get('WB_SOURCE_HEAD','local'),'environment':os.environ.get('WB_CI_ENVIRONMENT','local'),
            'profile':args.profile,'python':list(sys.version_info[:3]),'platform':sys.platform,
            'selected_ids':ids,'completed_ids':[],'tests_run':0,'skips':0,'loader_errors':[],
            'completed_normally':False,'success':False,'failures':0,'errors':0}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();result=None
    with (args.report.parent/'events.private.jsonl').open('w',encoding='utf-8') as events:
        def factory(*values,**kwargs):
            nonlocal result
            result=RecordingResult(*values,events=events,**kwargs);return result
        try:
            unittest.TextTestRunner(verbosity=2,resultclass=factory).run(unittest.TestSuite(selected))
            report['completed_normally']=True
        finally:
            if result is not None:
                report.update(completed_ids=result.completed_ids,tests_run=result.testsRun,
                    skips=len(result.skipped),failures=len(result.failures),errors=len(result.errors),
                    success=report['completed_normally'] and result.wasSuccessful())
            report['seconds']=time.monotonic()-started
            args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return 0 if report['success'] else 1

if __name__=='__main__':raise SystemExit(main())
