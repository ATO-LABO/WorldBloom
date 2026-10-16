"""Syntax/static event, job, permission and required-gate contract checks."""
import itertools
import json
from pathlib import Path
import re
import subprocess
import yaml
from ci_plan import policy

ROOT=Path(__file__).resolve().parents[2]

class UniqueLoader(yaml.SafeLoader):pass
def mapping(loader,node,deep=False):
    result={}
    for key_node,value_node in node.value:
        key=loader.construct_object(key_node,deep=deep)
        if key in result:raise ValueError('duplicate-YAML-key')
        result[key]=loader.construct_object(value_node,deep=deep)
    return result
UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)

def workflow(path):
    value=yaml.load(path.read_text(encoding='utf-8'),Loader=UniqueLoader)
    if 'on' not in value and True in value:value['on']=value.pop(True)
    return value

def validate(value):
    assert set(value['on'])=={'pull_request','push'}
    assert value['on']['pull_request'] is None
    assert value['on']['push']['branches']==['main','develop']
    assert value['permissions']=={'contents':'read'}
    jobs=value['jobs']
    assert set(jobs)=={'classify','basic','windows-related','regression-shard','regression','secrets','ci-policy'}
    assert jobs['ci-policy']['if']=='always()'
    assert set(jobs['ci-policy']['needs'])=={'classify','basic','windows-related','regression','secrets'}
    assert jobs['ci-policy'].get('name','ci-policy')=='ci-policy'
    full=jobs['regression-shard']['strategy']['matrix']
    assert full=={'os':['ubuntu-latest','windows-latest'],'python':['3.12','3.13'],'shard':[0,1]}
    assert jobs['regression']['strategy']['matrix']=={key:full[key] for key in ('os','python')}
    assert jobs['windows-related']['strategy']['matrix']=={'python':['3.12','3.13']}
    assert jobs['regression']['if']=="always() && (needs.classify.result != 'success' || needs.classify.outputs.full == 'true' || (github.event_name == 'pull_request' && github.base_ref == 'main'))"
    assert set(jobs['regression']['needs'])=={'classify','basic','regression-shard','secrets'}
    assert jobs['regression-shard']['if']=="needs.classify.outputs.full == 'true' || (github.event_name == 'pull_request' && github.base_ref == 'main')"
    assert jobs['windows-related']['if']=="needs.classify.outputs.windows == 'true'"
    for name,job in jobs.items():
        assert 0<job['timeout-minutes']<=45
        assert job['runs-on'] in ('ubuntu-latest','windows-latest','${{ matrix.os }}')
        assert not job.get('continue-on-error')
        if 'strategy' in job:assert job['strategy']['fail-fast'] is False
        for right in job.get('permissions',{}).values():assert right=='read'
        for step in job['steps']:
            assert not step.get('continue-on-error')
            if 'uses' in step:assert re.fullmatch(r'[A-Za-z0-9_./-]+@[0-9a-f]{40}',step['uses'])
    secret_commands='\n'.join(step.get('run','') for step in jobs['secrets']['steps'])
    assert 'install-gitleaks.sh' in secret_commands and 'gitleaks git .' in secret_commands
    assert 'gitleaks dir .' in secret_commands and 'publication_guard.py samples' in secret_commands
    return {'required_main_contexts':['regression ('+os+', '+py+')' for os,py in itertools.product(full['os'],full['python'])],
            'new_develop_context':'ci-policy',
            'tests_workflow_jobs':{'basic_or_proven_postmerge':4,'windows':6,'full_or_transition':16},
            'codeql_jobs_main_or_develop':3,'other_target_PR_full_jobs':16,
            'docs_jobs_path_dependent':{'PR':1,'main_push':2},
            'concurrency_cancellation_added':False}

def main():
    raw=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT)
    count=0
    for name in raw.decode().split('\0'):
        if not name.endswith('.py'):continue
        path=ROOT/name
        assert path.resolve().is_relative_to(ROOT.resolve()) and not path.is_symlink()
        compile(path.read_bytes(),name,'exec');count+=1
    data=validate(workflow(ROOT/'.github/workflows/tests.yml'))
    cfg=policy()
    for profile in ('basic','windows'):
        assert all((ROOT/'tests'/(name+'.py')).is_file() for name in cfg[profile+'_modules'])
    codeql=workflow(ROOT/'.github/workflows/codeql.yml')
    assert codeql['jobs']['analyze']['strategy']['matrix']['language']==['actions','javascript-typescript','python']
    assert set(codeql['on'])=={'push','pull_request','schedule','workflow_dispatch'}
    assert not (ROOT/'.github/workflows/security.yml').exists(), 'secrets moved intact into tests workflow'
    print(json.dumps({'python_sources_compiled':count,'policy_mode':cfg['develop_mode'],**data}))

if __name__=='__main__':main()
