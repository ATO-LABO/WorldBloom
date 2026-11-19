"""Lossless native working copies; scoped form edits; explicit new-world publish.

No model, GPU, original-world write or legacy conversion occurs here.
"""
from copy import deepcopy
import base64
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile
import yaml

from execution.draft_workspaces import WorkspaceStore, identifier, MAX_SOURCE
from execution.provenance import ConfigError, atomic_json, canonical, contained, directory_lock, sha256

MAX_ZIP=10*1024*1024

def bad(field,message,code='invalid_config'):raise ConfigError(field,message,code=code)

def safe_path(name):
    if not isinstance(name,str) or len(name.encode('utf-8'))>512 or not name or '\\' in name or ':' in name or '\x00' in name or name.startswith('/'):
        bad('zip','安全な相対パスだけを指定してください')
    parts=PurePosixPath(name).parts
    if any(p in ('..','.') or p.endswith((' ','.')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',p) for p in parts) or '/'.join(parts)!=name:
        bad('zip','対象外のファイルパスです: '+name)
    return name

def entry(data):return {'base64':base64.b64encode(data).decode('ascii'),'bytes':len(data),'sha256':sha256(data)}

def unpack(raw):
    if len(raw)>MAX_ZIP:bad('zip','ZIPは10MB以内にしてください')
    files={};seen=set();total=0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if len(z.infolist())>1000:bad('zip','ZIPは1000項目以内にしてください')
            for info in z.infolist():
                name=safe_path(info.filename.rstrip('/') if info.is_dir() else info.filename)
                if stat.S_ISLNK(info.external_attr>>16) or info.flag_bits&1:bad('zip','リンクや暗号化された項目は取り込めません')
                if info.is_dir():continue
                if name.casefold() in seen:bad('zip','ZIP内のファイル名が重複しています')
                seen.add(name.casefold())
                if info.file_size>MAX_SOURCE-total:bad('zip','展開後は8MB以内にしてください')
                data=z.read(info);total+=len(data)
                if total>MAX_SOURCE:bad('zip','展開後は8MB以内にしてください')
                files[name]=data
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError,OSError) as exc:
        bad('zip','ZIPを読めません: '+type(exc).__name__)
    roots=[k[:-len('world.yaml')] for k in files if k.split('/')[-1]=='world.yaml']
    if len(roots)!=1:bad('zip','world.yamlが一つだけあるZIPを指定してください')
    prefix=roots[0];project={};dependencies={}
    for name,data in files.items():
        if name.startswith(prefix):project[safe_path(name[len(prefix):])]=data
        elif prefix and name.startswith('templates/'):
            from execution.library import identifier as library_id
            parts=PurePosixPath(name).parts
            if len(parts)<3:bad('zip','基本ルールのパスを確認してください')
            library_id(parts[1],'template_id');dependencies[name]=data
        else:bad('zip','世界フォルダー外の項目もあります。欠落を避けるため取込を停止しました: '+name)
    return project,dependencies

def parsed(files):
    try:
        from execution.time_pressure_settings import _UniqueLoader
        selected={rel:data for rel,data in files.items() if rel=='world.yaml' or re.fullmatch(r'subjects/[^/]+\.ya?ml',rel)}
        if any(len(data)>256*1024 for data in selected.values()):bad('files','世界・人物のYAMLは各256KB以内です。元ファイルは保持しています')
        loaded={rel:yaml.load(data,Loader=_UniqueLoader) for rel,data in selected.items()}
        world=loaded['world.yaml'];people={rel:p for rel,p in loaded.items() if rel!='world.yaml'}
    except (yaml.YAMLError,UnicodeError,KeyError,ValueError) as exc:bad('files','世界・人物のYAMLを読めません。元ファイルは保持しています: '+type(exc).__name__)
    if not isinstance(world,dict) or any(not isinstance(p,dict) or not isinstance(p.get('id'),str) for p in people.values()):bad('files','世界と人物の設定形式を確認してください')
    return world,people

def integer(value,field,low,high):
    if isinstance(value,str) and re.fullmatch(r'-?\d+',value):value=int(value)
    if type(value) is not int or not low<=value<=high:bad(field,f'{low}〜{high}の整数を指定してください')
    return value

def number(value,field,low,high):
    if isinstance(value,str):
        try:value=float(value)
        except ValueError:bad(field,'数値を指定してください')
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:bad(field,'範囲内の有限な数値を指定してください')
    return value

def apply_edits(world,people,edits):
    """Only named fields change. Unknown siblings and untouched files survive."""
    world=deepcopy(world);people=deepcopy(people)
    ids={p['id']:p for p in people.values()}
    if not isinstance(edits,list) or len(edits)>300:bad('edits','編集は300件以内で指定してください')
    for op in edits:
        if not isinstance(op,dict) or set(op)-{'kind','target','values'} or set(op)!={'kind','target','values'} or not isinstance(op['values'],dict):bad('edits','編集項目の形式を確認してください')
        kind,target,v=op['kind'],op['target'],deepcopy(op['values'])
        if kind=='time':
            if target is not None or set(v)!={'days','slots'}:bad('time','日数と時間帯を指定してください')
            if world.get('slot_execution') is not None or world.get('joint_activities') is not None:bad('time','この実行方式の時間変更は未対応です','unsupported')
            slots=v['slots'].splitlines() if isinstance(v['slots'],str) else v['slots']
            candidate={**(world.get('time') or {}),'days':integer(v['days'],'time.days',1,10000),'slots':slots}
            from execution.draft_settings import normalize
            # Validate the known values without replacing unknown time fields.
            normalized=normalize({'version':1,'time':{k:candidate[k] for k in ('days','slots','presentation','date_calendar') if k in candidate}})
            candidate['slots']=normalized['time']['slots']
            world['time']=candidate
        elif kind=='event':
            allowed={'id','label','day','slot','targets','grants_item','grants_fact','move_to','stress_delta','force_action'}
            if set(v)-allowed:bad('event','対応する予定の項目だけを編集してください')
            events=world.setdefault('scheduled_events',[])
            if target is None:base={};index=len(events)
            else:index=integer(target,'event.target',0,len(events));base=deepcopy(events[index]) if index<len(events) else {}
            if 'day' in v:v['day']=integer(v['day'],'event.day',1,10000)
            if 'stress_delta' in v:v['stress_delta']=number(v['stress_delta'],'event.stress_delta',-1000,1000)
            if v.get('grants_item') is not None:
                old=base.get('grants_item') or {};grant=v['grants_item']
                if not isinstance(grant,dict) or set(grant)!={'name','count'}:bad('event.grants_item','品物と個数を指定してください')
                v['grants_item']={**old,'name':grant['name'],'count':integer(grant['count'],'event.count',1,999)}
            if v.get('force_action') is not None:
                old=base.get('force_action') or {};force=v['force_action']
                if not isinstance(force,dict) or set(force)!={'verb','args'}:bad('event.force_action','行動と引数を指定してください')
                v['force_action']={**old,**force}
            for k,value in v.items():
                if value is None or k in ('slot','grants_fact','move_to') and value=='':base.pop(k,None)
                else:base[k]=value
            if index==len(events):events.append(base)
            else:events[index]=base
        elif kind=='event-remove':
            if v:bad('event','削除の指定を確認してください')
            events=world.get('scheduled_events') or [];del events[integer(target,'event.target',0,len(events)-1)]
        elif kind=='action':
            if not isinstance(target,str) or set(v)!={'actors','slots'} or target=='verb:rest':bad('action','必須休息以外の人物・時間制限を指定してください')
            usage=world.get('action_usage')
            if not isinstance(usage,dict):bad('action','この世界の行動使用設定は未対応です','unsupported')
            for key,values in v.items():
                if values is not None and (not isinstance(values,list) or len(set(values))!=len(values) or any(not isinstance(s,str) for s in values) or key=='slots' and not values):bad('action','重複しない人物・実行可能な時間帯を選択してください')
            constraints=usage.setdefault('constraints',{});constraints[target]={**constraints.get(target,{}),**v}
        elif kind=='goal':
            if target not in ids or set(v)!={'description','target','deliver_to'} or any(not isinstance(s,str) or len(s)>2000 for s in v.values()):bad('goal','人物の目標を確認してください')
            p=ids[target];p['goal']={**(p.get('goal') or {}),'description':v['description'],'target':v['target'] or None,'deliver_to':v['deliver_to'] or None}
        elif kind=='commitments':
            if target not in ids or set(v)!={'commitments','additions'}:bad('commitments','人物と優先事項を指定してください')
            from engine.concerns import Concerns,validate_commitments
            additions=v['additions']
            if not isinstance(additions,list) or len(additions)>32:bad('commitments','追加する条件を確認してください')
            if additions:
                concerns=world.setdefault('concerns',{'version':2,'conditions':[]})
                if concerns.get('version')!=2:bad('concerns','旧方式の条件定義は保持し、編集は停止します','unsupported')
                for definition in additions:
                    old=next((c for c in concerns['conditions'] if c['id']==definition.get('id')),None)
                    if old and old!=definition:bad('concerns','既存の条件IDを上書きできません')
                    if not old:concerns['conditions'].append(definition)
                Concerns(concerns)
            values=deepcopy(v['commitments'])
            for row in values:row['priority']=integer(row['priority'],'priority',1,10)
            ids[target]['commitments']=validate_commitments(values,{c['id'] for c in (world.get('concerns') or {}).get('conditions',[])})
        else:bad('edits','未対応の編集項目です')
    return world,people

class NativeStore:
    def __init__(self,repo,control):
        self.repo=Path(repo);self.store=WorkspaceStore(repo,control);self.store.root=contained(control,'native-world-drafts')

    def _bytes(self,r):
        snap=r['source_snapshot'];prefix='projects/'+snap['world_id']+'/'
        files={};deps={}
        for rel,e in snap['files'].items():
            safe_path(rel);data=base64.b64decode(e['base64'],validate=True)
            if sha256(data)!=e['sha256']:bad('files','保全原本のハッシュが一致しません')
            if rel.startswith(prefix):files[safe_path(rel[len(prefix):])]=data
            elif rel.startswith('templates/'):deps[rel]=data
            else:bad('files','原本のファイル範囲を確認してください')
        return files,deps

    def _dependencies(self,world):
        result={}
        def visit(v):
            if isinstance(v,dict):
                for value in v.values():visit(value)
            elif isinstance(v,list):
                for value in v:visit(value)
            elif isinstance(v,str) and v.startswith('templates/'):
                from execution.library import identifier as libid
                parts=PurePosixPath(safe_path(v)).parts
                if len(parts)<3:bad('templates','参照する基本ルールを確認してください')
                ident=libid(parts[1],'template_id');folder=contained(self.repo,'templates/'+ident)
                if not folder.is_dir():bad('templates','参照する基本ルールがありません。元データを保持して停止します')
                for p in folder.rglob('*'):
                    if p.is_symlink():bad('templates','基本ルールのリンクは未対応です')
                    if p.is_file() and p.name!='.write.lock':result[p.relative_to(self.repo).as_posix()]=p.read_bytes()
        visit(world);return result

    def create(self,token,request_id,*,world_id=None,zip_base64=None):
        if (world_id is None)==(zip_base64 is None):bad('source','既存世界かZIPのいずれかを指定してください')
        if world_id is not None:snap=self.store._snapshot(world_id)
        else:
            try:raw=base64.b64decode(zip_base64,validate=True)
            except (ValueError,TypeError):bad('zip','ZIPの内容を確認してください')
            files,deps=unpack(raw)
            # Invalid/old YAML is still retained. It cannot silently become runnable.
            try:world,_=parsed(files);fallback=self._dependencies(world)
            except ConfigError:fallback={}
            for rel,data in fallback.items():deps.setdefault(rel,data)
            mapping={**{'projects/import/'+k:entry(v) for k,v in files.items()},**{k:entry(v) for k,v in deps.items()}}
            if sum(e['bytes'] for e in mapping.values())>MAX_SOURCE:bad('zip','関連ファイル込みで8MB以内にしてください')
            snap={'version':1,'world_id':'import','files':mapping,'revision':sha256(canonical(mapping)),'archive':entry(raw),'scope':'ZIP entries retained; no source overwrite'}
        files={k.split('projects/'+snap['world_id']+'/',1)[1]:base64.b64decode(e['base64']) for k,e in snap['files'].items() if k.startswith('projects/'+snap['world_id']+'/')}
        try:world,_=parsed(files);name=str(world.get('name') or '名称未定');overview=str(world.get('overview') or '')
        except ConfigError:name='形式を確認する下書き';overview=''
        content={'version':1,'name':name[:120],'overview':overview[:8000],'time':{'enabled':False,'days':'','slots':''},
                 'draft_text':json.dumps({'version':1,'source_digest':snap['revision'],'edits':[]},ensure_ascii=False),'source_draft_id':None}
        inner=WorkspaceStore(self.repo,self.store.root.parent);inner.root=self.store.root;inner._snapshot=lambda _:deepcopy(snap)
        return self.model(inner.save(token,None,request_id,content,source_world_id=snap['world_id']))

    def model(self,r):
        result={'workspace_id':r['workspace_id'],'revision':r['revision'],'name':r['content']['name'],
                'buffer':json.loads(r['content']['draft_text']),'file_count':len(r['source_snapshot']['files']),
                'published_world':r.get('published_world')}
        files,deps=self._bytes(r)
        try:
            from engine.world_format import validate
            world,people=parsed(files)
            validate(world)
            result['model']=projection(world,people)
            result['parse_error']=None
        except (ConfigError,ValueError,TypeError,KeyError) as exc:result['model']=None;result['parse_error']=str(exc)
        return result

    def get(self,token):return self.model(self.store.get(token))
    def list(self):return self.store.list(native=True)

    def fork(self,token,new_token,request_id):
        r=self.store.get(token)
        return self.model(self.store.save(new_token,None,request_id,deepcopy(r['content']),source_workspace_id=token))

    def save(self,token,revision,request_id,buffer):
        r=self.store.get(token);base=json.loads(r['content']['draft_text'])
        if not isinstance(buffer,dict) or not {'version','source_digest','edits'}<=set(buffer) or set(buffer)-{'version','source_digest','edits','workflow'} or buffer['version']!=1 or buffer['source_digest']!=base['source_digest'] or not isinstance(buffer['edits'],list) or len(canonical(buffer))>200000:bad('buffer','下書きの編集形式・原本を確認してください')
        if 'workflow' in buffer:
            from execution.draft_workspaces import workflow
            workflow(buffer['workflow'])
        content=deepcopy(r['content']);content['draft_text']=json.dumps(buffer,ensure_ascii=False)
        return self.model(self.store.save(token,revision,request_id,content))

    def materialize(self,r):
        files,deps=self._bytes(r);original,people=parsed(files)
        buffer=json.loads(r['content']['draft_text']);world,changed_people=apply_edits(original,people,buffer['edits'])
        result=dict(files)
        if world!=original:result['world.yaml']=yaml.safe_dump(world,allow_unicode=True,sort_keys=False).encode('utf-8')
        for rel,p in changed_people.items():
            if p!=people[rel]:result[rel]=yaml.safe_dump(p,allow_unicode=True,sort_keys=False).encode('utf-8')
        return result,deps,original,world,changed_people

    def check(self,token,revision):
        r=self.store.get(token)
        if revision!=r['revision']:bad('revision','下書きが変更されています','conflict')
        files,deps,original,world,people=self.materialize(r)
        try:
            from execution.configs import _describe,normalize
            with tempfile.TemporaryDirectory(prefix='wb-native-check-') as temp:
                root=Path(temp);project=root/'projects'/('world-'+token)
                for rel,data in files.items():p=contained(project,rel);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
                for rel,data in deps.items():p=contained(root,rel);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
                from engine.world_format import validate
                validate(world)
                template=next(iter({PurePosixPath(k).parts[1] for k in deps}),'basic')
                description=_describe(root,normalize({'label':'下書きの入力確認','project_id':'world-'+token,'template_id':template,'evolution':{'kappa':0}}))
                from execution.time_basic import transition,dependency
                pressure,_=dependency(project)
                errors,warnings=transition(original,world,list(people.values()),project/'world.yaml',pressure)
                if errors:bad('settings',' / '.join(e['message'] for e in errors))
                from execution.action_usage_settings import runtime
                from execution.library import LibraryStore
                runtime_world=runtime(LibraryStore(root),'world-'+token,{'world.yaml':world,**people})
                # Native data may include future event keys; don't filter them.
                from execution.scheduled_event_safety import _changed_references
                old_events={e.get('id'):e for e in original.get('scheduled_events',[]) if isinstance(e,dict)}
                _changed_references([(event,old_events.get(event.get('id'))) for event in runtime_world.scheduled_events],runtime_world)
                seen=set()
                for event in runtime_world.scheduled_events:
                    eid=event.get('id')
                    if not isinstance(eid,str) or not eid.strip() or len(eid)>120 or eid in seen:bad('event.id','予定のIDを空欄・重複にしないでください')
                    seen.add(eid);integer(event.get('day'),'event.day',1,runtime_world.days)
                    if event.get('slot') not in (None,'',*runtime_world.slots):bad('event.slot','予定の時間帯がありません')
                    targets=event.get('targets')
                    if not isinstance(targets,list) or not targets or any(not isinstance(t,str) or t not in runtime_world.subjects for t in targets) or len(set(targets))!=len(targets):bad('event.targets','登録済みの対象人物を指定してください')
                    if 'stress_delta' in event:number(event['stress_delta'],'event.stress_delta',-1000,1000)
                for p in people.values():
                    goal=p.get('goal') or {}
                    if goal.get('target') and goal['target'] not in runtime_world.items:bad('goal','目標の品物がありません')
                    if goal.get('deliver_to') and goal['deliver_to'] not in runtime_world.zones:bad('goal','目標の場所がありません')
                actors=set(people_by_id(people))
                for rule in (runtime_world.action_usage or {}).get('constraints',{}).values():
                    if rule['actors'] is not None and set(rule['actors'])-actors:bad('action','未登録の人物です')
            digest=sha256(canonical({k:sha256(v) for k,v in {**{'project/'+k:v for k,v in files.items()},**deps}.items()}))
            if self.store.get(token)['revision']!=revision:bad('revision','確認中に変更されました','conflict')
            return {'valid':True,'revision':revision,'digest':digest,'warnings':warnings,'subjects':len(description['subjects']),'files':len(files),'note':'構造・参照と実行入力を確認しました。物語の面白さや到達可能性は保証しません。'}
        except ConfigError:raise
        except (ValueError,TypeError,KeyError,OSError,yaml.YAMLError) as exc:bad('settings','元データを保持して検証を停止しました: '+str(exc))

    def export(self,token,revision,original=False):
        r=self.store.get(token)
        if revision!=r['revision']:bad('revision','下書きが更新されています','conflict')
        if original and r['source_snapshot'].get('archive'):
            return {'zip_base64':r['source_snapshot']['archive']['base64'],'name':'original-'+token+'.zip'}
        if original:files,deps=self._bytes(r)
        else:files,deps,*_=self.materialize(r)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            for rel,data in files.items():z.writestr('projects/copy/'+rel,data)
            for rel,data in deps.items():z.writestr(rel,data)
        return {'zip_base64':base64.b64encode(out.getvalue()).decode('ascii'),'name':'world-draft-'+token+'.zip'}

    def publish(self,token,revision,digest):
        check=self.check(token,revision)
        if check['digest']!=digest:bad('revision','確認した入力と一致しません','conflict')
        dest=contained(self.repo,'projects/world-'+identifier(token))
        with directory_lock(self.store.root):
            r=self.store.get(token)
            if r['revision']!=revision:bad('revision','下書きが変更されています','conflict')
            files,deps,*_=self.materialize(r)
            for rel,data in deps.items():
                p=contained(self.repo,rel)
                if not p.is_file() or p.read_bytes()!=data:bad('templates','基本ルールが変更または欠落しています。共有原本は上書きしません','conflict')
            marker={'workspace_id':token,'revision':revision,'digest':digest}
            receipt=contained(self.store.root,'publications/'+token+'.json')
            with directory_lock(self.repo/'projects'):
                if dest.exists():
                    from execution.provenance import read_json
                    if not receipt.is_file() or read_json(receipt) != marker:
                        bad('world_id','同じIDの世界があります。上書きしません','conflict')
                    actual = {p.relative_to(dest).as_posix(): p.read_bytes() for p in dest.rglob('*') if p.is_file()}
                    if actual != files:
                        bad('world_id','同じIDの世界があります。上書きしません','conflict')
                else:
                    atomic_json(receipt, marker)
                    with tempfile.TemporaryDirectory(prefix='.native-world-', dir=self.repo) as temp:
                        stage = Path(temp) / 'world'
                        stage.mkdir()
                        for rel, data in files.items():
                            path = contained(stage, safe_path(rel))
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(data)
                        stage.rename(dest)
            if r.get('published_world') != dest.name:
                r['published_world'] = dest.name
                atomic_json(self.store._path(token), r)
            return {'world_id':dest.name}

def proposal_forms(draft,edits=None):
    from execution.world_drafts import compile_draft
    from execution.draft_settings import FIELDS
    d,world,subjects=compile_draft(draft)
    people={rel:yaml.safe_load(text) for rel,text in subjects.items()}
    if edits is None:return projection(world,people)
    if any(op.get('kind')=='goal' for op in edits):bad('goal','新規下案の目標は人物の目標フォームから編集してください')
    updated,changed=apply_edits(world,people,edits)
    settings=deepcopy(d.get('settings',{'version':1}))
    for key in FIELDS:
        if updated.get(key)!=world.get(key):settings[key]=deepcopy(updated[key])
    if 'action_usage' in settings and 'mechanics' not in settings:settings['mechanics']=deepcopy(updated['mechanics'])
    d['settings']=settings
    by_id=people_by_id(changed)
    for p in d['people']:
        if 'commitments' in by_id[p['name']]:p['commitments']=deepcopy(by_id[p['name']]['commitments'])
    # Structural checking only; does not call the live alignment reviewer.
    compile_draft(d)
    return d

def people_by_id(people):return {p['id']:p for p in people.values()}

def projection(world,people):
    from engine.verbs import HANDLED_VERBS
    from engine.actions import FORCE_ARG_KINDS
    usage=world.get('action_usage') or {}
    refs=set(usage.get('enabled',[]))|set(usage.get('constraints',{}))
    refs|={'verb:'+v for v in HANDLED_VERBS-{'craft','respond'}}
    from viewer.action_settings_copy import SUMMARIES
    action_labels={ref:SUMMARIES.get(ref.split(':',1)[1],(ref,))[0] if ref.startswith('verb:') else ('製作：' if ref.startswith('recipe:') else '対処：')+ref.split(':',1)[1] for ref in refs}
    return {'action_labels':action_labels,'name':world.get('name',''),'time':deepcopy(world.get('time') or {}),'scheduled_events':deepcopy(world.get('scheduled_events') or []),
            'people':[{'name':p['id'],'goal':deepcopy(p.get('goal') or {}),'commitments':deepcopy(p.get('commitments') or [])} for p in people.values()],
            'places':[z['name'] if isinstance(z,dict) else z for z in world.get('zones',[])],
            'items':list(world.get('items',{})) if isinstance(world.get('items'),dict) else [p['name'] for p in world.get('items',[])],
            'facts':[p['id'] for p in world.get('facts',[])], 'conditions':deepcopy((world.get('concerns') or {}).get('conditions',[])),
            'actions':sorted(refs),'constraints':deepcopy(usage.get('constraints',{})),'enabled':deepcopy(usage.get('enabled',[])),
            'action_policy':bool(usage),
            'force_verbs':{k:list(v) for k,v in FORCE_ARG_KINDS.items()},
            'ordinary':world.get('slot_execution') is None and world.get('joint_activities') is None}
