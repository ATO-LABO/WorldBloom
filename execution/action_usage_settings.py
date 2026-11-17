"""Read, preview and atomically save explicit action usage; no simulation runs."""
from copy import deepcopy
import json
import re
from pathlib import Path
import yaml
from engine.world import World
from engine.subject import Subject
from engine.action_usage import current_policy, supported, FEATURES, CONTRACT, REQUIRED
from execution.provenance import ConfigError, canonical, sha256, contained, directory_lock, atomic_json

FIELDS = {'revision','catalog_version','engine_contract','enabled','mechanics','world_rules','constraints'}
FOLDER = '.action-usage-saves'


def bad(field, message, code='invalid_config'):
    raise ConfigError(field, message, code=code)


def runtime(store, ident, files):
    from execution.world_editor import _base
    w = World(files['world.yaml'], _base(store,ident)/'world.yaml')
    w.bind_subjects({p['id']:Subject.from_definition(p) for rel,p in files.items() if rel!='world.yaml'})
    return w


def dependency(store):
    code = Path(__file__).resolve().parents[1]
    paths = [*code.joinpath('engine').glob('*.py'), code/'engine/action_types.json',Path(__file__),
             *store.repo.joinpath('templates').rglob('*.yaml')]
    return sha256(canonical({('code/'+str(p.resolve().relative_to(code)) if p.resolve().is_relative_to(code) else 'template/'+str(p.relative_to(store.repo))):sha256(p.read_bytes()) for p in paths if p.is_file()}))


def actor_defaults(world):
    from engine.action_usage import references
    from engine.action_constraints import verb_for
    result = {}
    for ref in references(world):
        verb = verb_for(ref)
        actors = [p.id for p in world.subjects.values() if verb in p.verbs or verb in world.global_action_grants() or verb == 'rest']
        if ref.startswith('response:'):
            actors = [a for a in actors if a in world.responses.actions[ref.split(':',1)[1]]['actors']]
        result[ref] = actors
    return result


def effective_actors(world):
    defaults = actor_defaults(world)
    for ref, rule in (world.action_usage or {}).get('constraints',{}).items():
        if rule['actors'] is not None: defaults[ref] = list(rule['actors'])
    return defaults


def view(store, ident):
    from execution.world_editor import snapshot
    model = snapshot(store,ident)
    files = {'world.yaml':model['world'],**{'subjects/'+str(i)+'.yaml':p for i,p in enumerate(model['people'])}}
    try: w=runtime(store,ident,files)
    except (ValueError,KeyError,TypeError) as exc:
        return {'version':1,'revision':model['revision'],'supported':False,'error':'設定を確認してください。'+str(exc)}
    policy, mechanics = current_policy(w)
    return {'version':1,'revision':model['revision'],'supported':supported(w),
            'type_bundles':[{**r,'refs':['verb:'+v for v in r['actions']]} for r in __import__('engine.action_types',fromlist=['catalog']).catalog()],
            'constraints':policy.get('constraints',{}),'people':[{'id':p.id,'name':p.id} for p in w.subjects.values()],
            'default_actors':actor_defaults(w),'effective_actors':effective_actors(w),'slots':list(w.slots),'timed_verbs':sorted(w.action_timing.rules) if w.action_timing else [],
            'catalog_version':1,'engine_contract':CONTRACT,'enabled':policy['enabled'],'required':sorted(REQUIRED) if supported(w) else [],
            'mechanics':mechanics,'world_rules':{'vitality':{'allow_death':w.vitality['allow_death']}},
            'zero_behavior':w.stamina.get('zero_behavior'),'stamina_max':w.stamina['default_max'],
            'stamina_recover':w.stamina['default_recover_per_slot']}


def check(store, ident, body, *, files=None, revision=None):
    from execution import world_editor as editor
    if not isinstance(body,dict) or set(body) != FIELDS:bad('request','確認する入力を確認してください','bad_request')
    if files is None: files,revision=editor._read(store,ident)
    if not isinstance(body['revision'],str) or body['revision']!=revision:bad('revision','世界や人物の設定が更新されています。入力を控えて再読み込みしてください','conflict')
    if type(body['catalog_version']) is not int or body['catalog_version']!=1 or body['engine_contract']!=CONTRACT:bad('engine_contract','この実行方式またはカタログ版の切替は未対応です')
    original=runtime(store,ident,files)
    if not supported(original):bad('engine_contract','この実行方式の行動切替は未対応です。現在の設定は保持されます')
    policy,old_mechanics=current_policy(original)
    body={**body,'constraints':deepcopy(body['constraints'])}
    policy['enabled']=deepcopy(body['enabled'])
    if body['constraints'] or 'constraints' in policy:policy['constraints']=deepcopy(body['constraints'])
    rules=body['world_rules']
    if (not isinstance(rules,dict) or set(rules)!={'vitality'} or not isinstance(rules['vitality'],dict)
            or set(rules['vitality'])!={'allow_death'} or type(rules['vitality']['allow_death']) is not bool):
        bad('world_rules','戦闘による死亡の扱いを確認してください')
    candidate=deepcopy(files['world.yaml'])
    candidate['action_usage']=policy;candidate['mechanics']=deepcopy(body['mechanics'])
    candidate['vitality']={**(candidate.get('vitality') or {}),'allow_death':rules['vitality']['allow_death']}
    try: result_runtime=runtime(store,ident,{**files,'world.yaml':candidate})
    except (ValueError,KeyError,TypeError) as exc:bad('enabled','使用設定と予定の参照を確認してください。'+str(exc))
    if result_runtime.action_usage is None:bad('enabled','使用設定を解決できません')
    warnings=[]
    grants=result_runtime.global_action_grants()
    for ref in result_runtime.action_usage['enabled']:
        kind,key=ref.split(':',1);verb='craft' if kind=='recipe' else 'respond' if kind=='response' else key
        actors=[p.id for p in result_runtime.subjects.values() if verb in p.verbs or verb in grants]
        if kind=='response':actors=[p for p in actors if p in result_runtime.responses.actions[key]['actors']]
        actors=effective_actors(result_runtime).get(ref,actors)
        if not actors and ref not in REQUIRED:
            warnings.append({'ref':ref,'code':'no_actor','message':'利用設定のある人物がいません。人物の許可も必要です。'})
    constraint_changes=sorted(r for r in set(policy.get('constraints',{}))|set(current_policy(original)[0].get('constraints',{})) if policy.get('constraints',{}).get(r)!=current_policy(original)[0].get('constraints',{}).get(r))
    changed=sorted(set(policy['enabled']) ^ set(current_policy(original)[0]['enabled']))
    if (changed or constraint_changes) and original.planner is not None:
        warnings.append({'code':'planning_changed','message':'計画と目標への経路が変わる可能性があります。実現可能性を再確認してください。'})
    proposal={'verb:'+v for v in __import__('engine.deliberation',fromlist=['VERBS']).VERBS}
    active=proposal&set(policy['enabled'])
    if active and not original.deliberation:
        warnings.append({'code':'no_proposal','message':'提案・合意形成の行動には提案データと人物の役割も必要です。'})
    if active and len(active)<len(proposal):
        warnings.append({'code':'partial_proposal','message':'提案・合意形成の一部だけを使用します。必要な手順を確認してください。'})
    changes=[{'ref':r,'enabled':r in policy['enabled']} for r in changed]
    changes.extend({'ref':r,'constraints':policy.get('constraints',{}).get(r)} for r in constraint_changes)
    for key in sorted(FEATURES):
        if old_mechanics[key]!=body['mechanics'][key]:changes.append({'setting':key,'enabled':body['mechanics'][key]})
    if original.vitality['allow_death']!=rules['vitality']['allow_death']:changes.append({'setting':'allow_death','enabled':rules['vitality']['allow_death']})
    dep=dependency(store)
    normalized={k:deepcopy(body[k]) for k in sorted(FIELDS)}
    normalized['enabled']=result_runtime.action_usage['enabled']
    fingerprint=sha256(canonical({'input':normalized,'dependency':dep}))
    _,confirmed=editor._read(store,ident)
    if confirmed!=revision or dependency(store)!=dep:bad('revision','確認中に設定や実行コードが更新されました。再確認してください','conflict')
    return {'valid':True,'revision':revision,'check_fingerprint':fingerprint,'warnings':warnings,'changes':changes,
            'dependency_hash':dep,'candidate':candidate}


def _request_id(value):
    if not isinstance(value,str) or re.fullmatch(r'[A-Za-z0-9_-]{8,96}',value) is None:bad('request_id','保存要求の識別子を確認してください','bad_request')
    return value


def _existing(store, ident, path, *, request_hash=None):
    from execution import world_editor as editor
    if not path.exists():return None
    record=json.loads(path.read_text(encoding='utf-8'))
    if request_hash is not None and record['request_hash']!=request_hash:bad('request_id','同じ保存要求に異なる入力があります','conflict')
    files,revision=editor._read(store,ident)
    person_hash=sha256(canonical({k:sha256(contained(editor._base(store,ident),k).read_bytes()) for k in files if k!='world.yaml'}))
    if record['state']=='prepared' and sha256((editor._base(store,ident)/'world.yaml').read_bytes())==record['expected_world_sha'] and person_hash==record['people_hash']:
        record.update(state='applied',after_revision=revision);atomic_json(path,record)
    if record['state']=='applied':
        return {'state':'applied','saved_revision':record['after_revision'],'matches_current':revision==record['after_revision'],
                'backup':record['backup'],'model':view(store,ident)}
    if revision==record['before_revision']:return {'state':'not_applied'}
    return {'state':'unknown'}


def status(store, ident, request_id):
    from execution.world_editor import _base
    request_id=_request_id(request_id)
    with directory_lock(_base(store,ident)):
        return _existing(store,ident,contained(_base(store,ident),FOLDER+'/'+request_id+'.json')) or {'state':'not_found'}


def save(store, ident, body):
    from execution import world_editor as editor
    if not isinstance(body,dict) or set(body) != FIELDS|{'check_fingerprint','request_id'}:bad('request','保存する入力を確認してください','bad_request')
    request_id=_request_id(body['request_id']);base=editor._base(store,ident)
    request_hash=sha256(canonical(body));path=contained(base,FOLDER+'/'+request_id+'.json')
    with directory_lock(base):
        if contained(base,__import__('execution.person_values',fromlist=['JOURNAL']).JOURNAL).exists():bad('revision','別の保存が中断されています。先に復旧してください','conflict')
        prior=_existing(store,ident,path,request_hash=request_hash)
        if prior and prior['state']=='applied':return prior
        if prior and prior['state']=='unknown':bad('request_id','保存結果が不明です。入力を保持して状態を確認してください','conflict')
        files,revision=editor._read(store,ident)
        result=check(store,ident,{k:body[k] for k in FIELDS if k in body},files=files,revision=revision)
        if body['check_fingerprint']!=result['check_fingerprint']:bad('check_fingerprint','現在の変更内容を確認してから保存してください','conflict')
        text=yaml.safe_dump(result['candidate'],allow_unicode=True,sort_keys=False)
        expected=sha256(text.encode('utf-8'))
        backup_id=request_id+'.preimage.json';backup=contained(base,FOLDER+'/'+backup_id)
        record={'version':1,'world_id':ident,'before_revision':revision,'world_text':(base/'world.yaml').read_bytes().decode('utf-8'),
                'before_world_sha':sha256((base/'world.yaml').read_bytes()),'after_world_sha':expected}
        if backup.exists():
            if json.loads(backup.read_text(encoding='utf-8'))!=record:bad('backup','変更前原本が一致しません','conflict')
        else:atomic_json(backup,record)
        _,confirmed=editor._read(store,ident)
        if confirmed!=revision or dependency(store)!=result['dependency_hash']:bad('revision','保存前に設定が更新されました','conflict')
        receipt={'state':'prepared','request_hash':request_hash,'before_revision':revision,'expected_world_sha':expected,
                 'people_hash':sha256(canonical({k:sha256(contained(base,k).read_bytes()) for k in files if k!='world.yaml'})),
                 'backup':{'id':backup_id,'before_revision':revision}}
        atomic_json(path,receipt)
        store._write_file('world',ident,'world.yaml',text)
        response=_existing(store,ident,path,request_hash=request_hash)
        if not response or response['state']!='applied':bad('request_id','保存結果を確認できません。再保存せず状態を確認してください','conflict')
        return response
