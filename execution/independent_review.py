"""CPU-only, unselected trial admission and saved review projection.

Uses existing frozen ConfigStore inputs and source snapshots. No model settings,
network client, GPU operation, new authentication or scenario-name dispatch.
"""
from pathlib import Path
from copy import deepcopy
import json
import yaml
from execution.provenance import (ConfigError,canonical,sha256,identifier,contained,
                                  atomic_json,materialize,verify_files,code_snapshot,
                                  directory_lock,publish_directory)
from engine.execution_plan import ExecutionPlan
from engine.episode_contract import factory_identities, native_profile

MAX_TRIALS=100

def capability(configs,config_id):
    config,manifest,root=configs._bundle(config_id)
    if 'execution_profile' in config:
        from execution.melos_product import capability as profile_capability
        return profile_capability(configs,config_id)
    evolution=config['evolution']
    code,runtime=code_snapshot(configs.repo)
    source_hash=sha256(canonical(runtime))
    input_hash=config['input_manifest_sha256']
    policy={'kind':'native_base_candidates_no_genome_no_judge','source_hash':source_hash}
    policy_hash=sha256(canonical(policy))
    semantic={'inputs':input_hash,'provider':policy,'terminal_policy':{'target_as_observation':True,'death_stops':True}}
    settings=sha256(canonical(semantic))
    world=yaml.safe_load(contained(root/'inputs',f"projects/{config['project_id']}/world.yaml").read_bytes())
    mode=(world.get('slot_execution') or {}).get('mode')
    supported=mode in (None,'cultural_festival_next_boundary_v1')
    reason=None if supported else '暦版の自律選択担当はまだ接続されていません。保存結果を読めます。'
    if evolution.get('kappa') not in (None,0) or evolution.get('route_rho') not in (None,0) or config.get('growth'):
        supported=False;reason='このCPU独立試行にはモデル・成長なしの設定版を選んでください。'
    return {'schema_version':1,'supported':supported,'reason':reason,'config_id':config_id,
            'world':config['project_id'],'world_revision':input_hash,'policy_revision':policy_hash,
            'engine_revision':source_hash,'settings_version':settings,'run_spec_hash':settings,
            'provider_revision':policy_hash,'method':'independent_trials','population':'unselected',
            'selection':'none','reference_guided':False,'max_trials':MAX_TRIALS,
            'genome_kind':'none','decision_provider_kind':'native_base_candidates_no_genome_no_judge',
            'knowledge_scope':'legacy_compatible_no_external_judge','time_unit':'logical_step',
            'horizon':int(world['time']['days'])*len(world['time']['slots'])-1}

def normalize(request):
    required={'request_id','kind','config_id','run_spec_hash','world_revision','policy_revision',
              'settings_version','provider_revision','seed_plan','method','population','selection','reference_guided'}
    if not isinstance(request,dict) or set(request)!=required:
        raise ConfigError('request','独立試行の固定要求を確認してください',code='bad_request')
    result=deepcopy(request)
    for field in ('request_id','config_id'):identifier(result[field],field)
    if (result['kind']!='independent_trials' or result['method']!='independent_trials'
            or result['population']!='unselected' or result['selection']!='none' or result['reference_guided'] is not False):
        raise ConfigError('method','選別・原作誘導なしの独立試行を指定してください',code='bad_request')
    seeds=result['seed_plan']
    if (not isinstance(seeds,list) or not 1<=len(seeds)<=MAX_TRIALS
            or any(type(s) is not int or not 0<=s<=2147483647 for s in seeds) or len(set(seeds))!=len(seeds)):
        raise ConfigError('seed_plan','重複のない事前seedを1〜100件指定してください',code='bad_request')
    for field in ('run_spec_hash','world_revision','policy_revision','settings_version','provider_revision'):
        if type(result[field]) is not str or len(result[field])!=64 or any(c not in '0123456789abcdef' for c in result[field]):
            raise ConfigError(field,'固定版hashが不正です',code='bad_request')
    return result

def admit(configs,request):
    config,_,_=configs._bundle(request['config_id'])
    if 'execution_profile' in config:
        from execution.melos_product import admit as profile_admit
        return profile_admit(configs,request)
    cap=capability(configs,request['config_id'])
    if not cap['supported']:raise ConfigError('capability',cap['reason'],code='unsupported')
    for field in ('run_spec_hash','world_revision','policy_revision','settings_version','provider_revision'):
        if request[field]!=cap[field]:raise ConfigError(field,'入力または方針版が更新されています',code='conflict')
    config,manifest,root=configs._bundle(request['config_id'])
    world=yaml.safe_load(contained(root/'inputs',f"projects/{config['project_id']}/world.yaml").read_bytes())
    code,runtime=code_snapshot(configs.repo)
    sampler,provider=factory_identities(native_profile(world),engine_files=code)
    classifier={'id':'observed_terminal_v1','version':1,'source_hash':sha256(Path(__file__).read_bytes()),'config_hash':sha256(canonical({'unknown_is_not_failure':True}))}
    value={'schema_version':1,'execution_kind':'independent_trials','sampler':sampler,'provider':provider,
           'predeclared_seeds':request['seed_plan'],'planned_trials':len(request['seed_plan']),
           'selection':{'fitness_selection':False,'breeding':False,'keep_filter':False,'canon_feedback':False},
           'observation':{'unit':'logical_step','start':0,'end':cap['horizon'],'stop_contract_hash':sha256(canonical({'target_as_observation':True,'death_stops':True}))},
           'classification':classifier}
    return {'capability':cap,'execution_plan':ExecutionPlan(value).to_dict(),'execution_plan_sha256':ExecutionPlan(value).sha256}

def prepare_run(configs,job,folder):
    request=json.loads((folder/'request.json').read_text(encoding='utf-8'))
    admission=json.loads((folder/'independent-plan.json').read_text(encoding='utf-8'))
    current=admit(configs,request)
    if current!=admission:raise ConfigError('snapshot','実行元が変更されています',code='snapshot_changed')
    config,inputs,source=configs._bundle(request['config_id'])
    final=contained(configs.runs,job['run_id'])
    with directory_lock(configs.runs,operation='independent.prepare'):
        if final.exists():raise ConfigError('run_id','出力先が既にあります',code='conflict')
        code,runtime=code_snapshot(configs.repo)
        if sha256(canonical(runtime))!=admission['capability']['engine_revision']:
            raise ConfigError('source','実行ソースが変更されています',code='snapshot_changed')
        staging=contained(configs.runs,'.pending-'+job['nonce'])
        blobs={r['path']:contained(source/'inputs',r['path']).read_bytes() for r in inputs['files']}
        materialize(staging/'inputs',blobs);verify_files(staging/'inputs',inputs['files'])
        materialize(staging/'runtime',code);verify_files(staging/'runtime',runtime['files'])
        atomic_json(staging/'execution-plan.json',admission['execution_plan'])
        atomic_json(staging/'accepted-request.json',request)
        atomic_json(staging/'review-manifest.json',{'schema_version':1,'job_id':job['job_id'],
                    'inputs':inputs,'runtime':runtime,'admission':admission,'config':config})
        publish_directory(staging,final)
    from execution.provenance import python_executable
    # ProcessTree owns the isolated Python gate and inserts UTF-8 itself.
    # Its established argv contract is python, -I, -B, script, arguments.
    argv=[python_executable(),'-I','-B',str(final/'runtime/scripts/independent_trials.py'),
          '--run',str(final),'--control',str(configs.control),'--job',job['job_id'],'--nonce',job['nonce']]
    return {'argv':argv,'runtime_manifest_sha256':sha256(canonical(runtime))}

def receipt(run,records):
    run=Path(run);manifest=json.loads((run/'review-manifest.json').read_text(encoding='utf-8'))
    admission=manifest['admission'];cap=admission['capability'];plan=admission['execution_plan']
    slots={r['seed']:r for r in records}
    published=[slots.get(seed,{'id':f'trial-{i:04d}','seed':seed,'status':'not_completed','category':'not_completed',
                             'source':'execution-plan.json','evidence_refs':['execution-plan.json']})
               for i,seed in enumerate(plan['predeclared_seeds'])]
    return {'schema':'worldbloom_independent_v2','schema_version':2,'id':manifest['job_id'],
            'world':cap['world'],'world_revision':cap['world_revision'],'policy_revision':cap['policy_revision'],
            'engine_revision':cap['engine_revision'],'settings_version':cap['settings_version'],
            'method':'independent_trials','population':'unselected','selection':'none','reference_guided':False,
            'seed_plan':plan['predeclared_seeds'],'execution_plan_id':admission['execution_plan_sha256'],
            'execution_plan_sha256':admission['execution_plan_sha256'],'sampling_rule':plan['sampler']['id'],
            'classification_rule':plan['classification']['id'],'observation_stop':plan['observation'],
            'source':'保存された全trialのRunRecordとledger','knowledge_scope':cap['knowledge_scope'],
            'genome_kind':cap['genome_kind'],'decision_provider_kind':cap['decision_provider_kind'],
            'completion':'complete' if len(records)==len(plan['predeclared_seeds']) else 'partial',
            'requested':len(plan['predeclared_seeds']),'completed':len(records),'trials':published}
