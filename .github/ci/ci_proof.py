"""Bounded authenticated GET-only proof for deduplicating a merged PR's tests."""
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile

class NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme!='https':raise ValueError('non-HTTPS-redirect')
        result=super().redirect_request(request,fp,code,msg,headers,newurl)
        if urllib.parse.urlparse(newurl).hostname!='api.github.com':
            result.remove_header('Authorization')
        return result

def get(path,token,*,binary=False):
    request=urllib.request.Request('https://api.github.com/'+path,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28'})
    with urllib.request.build_opener(NoCredentialRedirect()).open(request,timeout=15) as response:
        if 'rel="next"' in response.headers.get('Link',''):raise ValueError('API-pagination-limit')
        maximum=131072 if binary else 2*1024*1024
        raw=response.read(maximum+1)
        if len(raw)>maximum:raise ValueError('API-byte-limit')
    return raw if binary else json.loads(raw)

def validate_receipt(receipt,*,inputs,pr,run):
    source=receipt['inputs']
    if (type(receipt['schema']) is not int or receipt['schema']!=1 or receipt['outcome']!='success' or
        source['event']!='pull_request' or source['target']!=inputs['target'] or
        source['pr_number']!=pr['number'] or len(inputs['parents'])!=2 or
        source['base_sha']!=inputs['parents'][0] or source['head_sha']!=inputs['parents'][1] or
        pr['head']['sha']!=inputs['parents'][1] or source['tested_tree']!=inputs['tested_tree'] or
        source['run_id']!=run['id'] or source['run_attempt']!=run['run_attempt'] or
        receipt['complete_census_verified'] is not True or type(receipt['basic_count']) is not int or receipt['basic_count']<=0 or
        source['parents']!=[source['base_sha'],source['head_sha']] or
        not re.fullmatch('[0-9a-f]{40}',source['tested_sha']) or
        not re.fullmatch('[0-9a-f]{64}',receipt['plan_sha256'])):
        raise ValueError('receipt-input-or-census-mismatch')
    from ci_plan import ENVIRONMENTS
    tier=receipt['effective_tier']
    if tier not in ('basic','windows','full'):raise ValueError('invalid-receipt-tier')
    if inputs['target']=='main' and tier!='full':raise ValueError('main-receipt-not-full')
    if receipt['full_environments']!=(ENVIRONMENTS if tier=='full' else []):raise ValueError('missing-full-environment')
    if receipt['windows_environments']!=(ENVIRONMENTS[2:] if tier=='windows' else []):raise ValueError('missing-Windows-environment')
    return {'pr':pr['number'],'run_id':run['id'],'run_attempt':run['run_attempt'],
            'tested_sha':source['tested_sha'],'tested_tree':source['tested_tree'],
            'merged_sha':inputs['tested_sha'],'base_sha':source['base_sha'],
            'head_sha':source['head_sha'],'effective_tier':tier,'receipt_plan_sha256':receipt['plan_sha256']}

def find_receipt(inputs,repository,token):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):raise ValueError('invalid-repository')
    if len(inputs['parents'])!=2:raise ValueError('not-a-merge-commit')
    prefix='repos/'+repository+'/'
    pulls=get(prefix+'commits/'+inputs['tested_sha']+'/pulls?per_page=100',token)
    if len(pulls)>=100:raise ValueError('PR-association-limit')
    matches=[p for p in pulls if p.get('merged_at') and p.get('merge_commit_sha')==inputs['tested_sha'] and
             p['base']['ref']==inputs['target'] and p['base']['repo']['full_name']==repository and
             p['head']['sha']==inputs['parents'][1]]
    if len(matches)!=1:raise ValueError('ambiguous-or-missing-merged-PR')
    pr=matches[0]
    data=get(prefix+'actions/runs?event=pull_request&status=completed&head_sha='+pr['head']['sha']+'&per_page=100',token)
    if data['total_count']>=100:raise ValueError('workflow-run-limit')
    runs=[r for r in data['workflow_runs'] if r['path']=='.github/workflows/tests.yml' and
          r['event']=='pull_request' and r['conclusion']=='success' and r['head_sha']==pr['head']['sha']]
    if len(runs)>20:raise ValueError('receipt-candidate-limit')
    proofs=[]
    for run in runs:
        jobs=get(prefix+'actions/runs/'+str(run['id'])+'/jobs?filter=latest&per_page=100',token)
        if jobs['total_count']>=100:raise ValueError('job-list-limit')
        gates=[job for job in jobs['jobs'] if job['name']=='ci-policy']
        if len(gates)!=1 or gates[0]['status']!='completed' or gates[0]['conclusion']!='success':continue
        artifacts=get(prefix+'actions/runs/'+str(run['id'])+'/artifacts?per_page=100',token)
        if artifacts['total_count']>=100:raise ValueError('artifact-list-limit')
        matches=[a for a in artifacts['artifacts'] if a['name']=='policy-receipt-a'+str(run['run_attempt']) and not a['expired']]
        if len(matches)!=1:continue
        artifact=matches[0]
        if artifact['size_in_bytes']>131072:raise ValueError('receipt-archive-limit')
        raw=get(prefix+'actions/artifacts/'+str(artifact['id'])+'/zip',token,binary=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if archive.namelist()!=['ci-receipt.json'] or archive.infolist()[0].file_size>65536:
                raise ValueError('receipt-member-limit')
            receipt=json.loads(archive.read('ci-receipt.json'))
        try:proof=validate_receipt(receipt,inputs=inputs,pr=pr,run=run)
        except (ValueError,KeyError,TypeError):continue
        proofs.append(proof)
    if not proofs:raise ValueError('no-matching-successful-receipt')
    return max(proofs,key=lambda p:(p['run_id'],p['run_attempt']))
