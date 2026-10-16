"""Conservative fixed-input CI plan. No remote writes or diff REST truncation."""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
POLICY = Path(__file__).with_name('policy.json')
ENVIRONMENTS = ['ubuntu-latest-py3.12', 'ubuntu-latest-py3.13',
                'windows-latest-py3.12', 'windows-latest-py3.13']
WINDOWS_SYMBOLS = frozenset(('kernel', 'identity_from_handle', '_WindowsProcessTree'))

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def policy():
    value = json.loads(POLICY.read_text(encoding='utf-8'))
    assert value['schema'] == 1 and value['develop_mode'] in ('transition-full', 'tiered')
    assert type(value['max_diff_files']) is int and 0<value['max_diff_files']<=5000
    assert type(value['max_diff_bytes']) is int and 0<value['max_diff_bytes']<=2*1024*1024
    for name in ('basic_modules', 'windows_modules'):
        assert value[name] and len(value[name]) == len(set(value[name]))
        assert all(re.fullmatch(r'test_[a-z0-9_]+', item) for item in value[name])
    return value

def git(*args, root=ROOT):
    return subprocess.run(['git', *args], cwd=root, capture_output=True,
                          check=True, timeout=45).stdout

def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('invalid-commit')
    return value

def path_ok(value):
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and '..' not in path.parts and '\\' not in value and ':' not in value

def parse_diff(raw, *, max_bytes, max_files):
    if len(raw) > max_bytes:
        raise ValueError('diff-byte-limit')
    tokens = raw.decode('utf-8', errors='strict').split('\0')
    if tokens[-1] != '':
        raise ValueError('truncated-diff')
    tokens.pop()
    rows = []
    while tokens:
        status = tokens.pop(0)
        count = 2 if status.startswith(('R', 'C')) else 1
        if len(tokens) < count or not re.fullmatch(r'(?:[MADTUXB]|[RC][0-9]{1,3})', status):
            raise ValueError('invalid-diff-status')
        names = [tokens.pop(0) for _ in range(count)]
        if not all(path_ok(name) for name in names):
            raise ValueError('unsafe-diff-path')
        rows.append({'status': status, 'paths': names})
        if len(rows) > max_files:
            raise ValueError('diff-file-limit')
    if not rows:
        raise ValueError('empty-diff')
    return rows

def body_only(old, new):
    """Allow existing Windows function bodies, never signatures/import-time code."""
    if type(old) is not type(new): return False
    if isinstance(old, (ast.FunctionDef, ast.AsyncFunctionDef)):
        import copy
        left,right=copy.deepcopy(old),copy.deepcopy(new)
        left.body=[];right.body=[]
        return ast.dump(left,include_attributes=False)==ast.dump(right,include_attributes=False)
    if isinstance(old, ast.ClassDef):
        if len(old.body) != len(new.body): return False
        import copy
        left,right=copy.deepcopy(old),copy.deepcopy(new)
        left.body=[];right.body=[]
        if ast.dump(left,include_attributes=False)!=ast.dump(right,include_attributes=False):return False
        return all(ast.dump(a,include_attributes=False)==ast.dump(b,include_attributes=False) or
                   isinstance(a,ast.FunctionDef) and isinstance(b,ast.FunctionDef) and body_only(a,b)
                   for a,b in zip(old.body,new.body))
    return False

def windows_only(old_bytes, new_bytes):
    before, after = ast.parse(old_bytes), ast.parse(new_bytes)
    if len(before.body) != len(after.body): return False
    changed=False
    for old,new in zip(before.body,after.body):
        if ast.dump(old,include_attributes=False)==ast.dump(new,include_attributes=False):continue
        changed=True
        if getattr(old,'name',None) not in WINDOWS_SYMBOLS or getattr(new,'name',None)!=old.name or not body_only(old,new):
            return False
    return changed

def classify_changes(rows, load_pair):
    tier='basic'; reasons=[]
    for row in rows:
        name=row['paths'][-1]
        if row['status'] != 'M':
            tier='full';reasons.append({'path':name,'reason':'addition-deletion-rename-copy-or-type-change'});continue
        if name=='execution/worker.py':
            try: allowed=windows_only(*load_pair(name))
            except (SyntaxError,ValueError,UnicodeError):allowed=False
            if allowed:
                if tier!='full':tier='windows'
                reasons.append({'path':name,'reason':'audited-Windows-symbol-body-only'});continue
        if name=='viewer/build_exe.cmd':
            if tier!='full':tier='windows'
            reasons.append({'path':name,'reason':'Windows-executable-build-wrapper'});continue
        suffix=PurePosixPath(name).suffix.lower()
        if (name.startswith(('docs/','docsite/')) and suffix in ('.md','.png','.jpg','.jpeg','.webp','.css') or
                name in ('README.md','LICENSE')):
            reasons.append({'path':name,'reason':'document-or-display-asset'});continue
        tier='full'
        reasons.append({'path':name,'reason':'shared-foundation-test-infrastructure-dependency-template-or-unmapped-file'})
    return tier,reasons

def make_plan(inputs, rows, cfg, load_pair, *, diff_error=None, receipt=None):
    target=inputs['target']
    if not isinstance(target,str) or not target:raise ValueError('invalid-target')
    if diff_error:
        requested,reasons='full',[{'reason':'diff-fallback','error':diff_error}]
    else:requested,reasons=classify_changes(rows,load_pair)
    effective=requested
    if diff_error:
        effective='full';reasons.append({'reason':'incomplete-diff-overrides-receipt-optimization'})
    elif target not in ('main','develop'):
        effective='full';reasons.append({'reason':'other-PR-target-preserves-full-regression-and-secrets'})
    elif inputs['event']=='pull_request' and target=='main':
        effective='full';reasons.append({'reason':'main-PR-always-full'})
    elif cfg['develop_mode']=='transition-full':
        effective='full';reasons.append({'reason':'transition-preserves-old-four-gates'})
    elif inputs['event']=='push':
        effective='postmerge' if receipt else 'full'
        reasons.append({'reason':'verified-merged-PR-tree-receipt' if receipt else 'push-without-verifiable-receipt-full'})
    full=effective=='full';windows=effective=='windows'
    plan={'schema':1,'inputs':inputs,'policy_sha256':digest(cfg),'requested_tier':requested,
          'effective_tier':effective,'changes':rows,'reasons':reasons,'receipt_proof':receipt,
          'required':{'basic':True,'secrets':True,'full_environments':ENVIRONMENTS if full else [],
                      'windows_environments':ENVIRONMENTS[2:] if windows else []},
          'modules':{'basic':cfg['basic_modules'],'windows':cfg['windows_modules']},
          'diff_complete':diff_error is None}
    plan['plan_sha256']=digest(plan)
    return plan

def fixed_inputs(event, event_name, tested, *, root=ROOT):
    tested=sha(tested)
    actual=git('rev-parse','HEAD',root=root).decode().strip()
    if actual!=tested:raise ValueError('checkout-does-not-match-event')
    tree=git('rev-parse',tested+'^{tree}',root=root).decode().strip()
    parents=git('show','-s','--format=%P',tested,root=root).decode().split()
    if event_name=='pull_request':
        pr=event['pull_request'];base=sha(pr['base']['sha']);head=sha(pr['head']['sha'])
        if parents!=[base,head]:raise ValueError('test-merge-parent-input-mismatch')
        target=pr['base']['ref'];number=pr['number']
    elif event_name=='push':
        head=tested;base=sha(event['before']);target=event['ref'].removeprefix('refs/heads/');number=None
        if event.get('deleted') or head!=event['after']:raise ValueError('invalid-push-input')
    else:raise ValueError('unsupported-event')
    merge_bases=git('merge-base','--all',base,head,root=root).decode().split()
    if len(merge_bases)!=1:raise ValueError('ambiguous-merge-base')
    return {'event':event_name,'target':target,'base_sha':base,'head_sha':head,
            'tested_sha':tested,'tested_tree':tree,'parents':parents,'merge_base':merge_bases[0],
            'pr_number':number,'run_id':int(os.environ.get('GITHUB_RUN_ID','0')),
            'run_attempt':int(os.environ.get('GITHUB_RUN_ATTEMPT','1'))}

def main():
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    inputs=fixed_inputs(event,os.environ['GITHUB_EVENT_NAME'],os.environ['GITHUB_SHA'])
    cfg=policy();receipt=None;proof_error=None
    if inputs['event']=='push' and cfg['develop_mode']=='tiered':
        from ci_proof import find_receipt
        try:receipt=find_receipt(inputs,os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_TOKEN'])
        except Exception as error:proof_error=type(error).__name__
    try:
        raw=git('diff','--name-status','-z','--find-renames=50%',inputs['merge_base'],inputs['head_sha'],'--')
        rows=parse_diff(raw,max_bytes=cfg['max_diff_bytes'],max_files=cfg['max_diff_files']);error=None
    except Exception as exc:rows=[];error=type(exc).__name__
    def pair(name):return git('show',inputs['merge_base']+':'+name),git('show',inputs['head_sha']+':'+name)
    plan=make_plan(inputs,rows,cfg,pair,diff_error=error,receipt=receipt)
    if proof_error:plan['reasons'].append({'reason':'receipt-unavailable-fallback-full','error':proof_error});plan.pop('plan_sha256');plan['plan_sha256']=digest(plan)
    Path('policy-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as stream:
            for key,value in {'full':bool(plan['required']['full_environments']),
                              'windows':bool(plan['required']['windows_environments']),
                              'tested_sha':inputs['tested_sha']}.items():
                stream.write(key+'='+str(value).lower()+'\n')
    print(json.dumps({'effective_tier':plan['effective_tier'],'reasons':plan['reasons'],
                      'inputs':inputs,'plan_sha256':plan['plan_sha256']},ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'classification_error':type(error).__name__}))
        raise SystemExit(1)
