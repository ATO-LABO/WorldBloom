"""Executable logical time settings with reference checks; no automatic mode migration."""
from copy import deepcopy
import json
import math
import re
from pathlib import Path
import yaml
from execution.provenance import ConfigError,canonical,sha256,contained,atomic_json
from engine.time_kernel import TimeAxis
from engine.logical_calendar import normalize_date_calendar,date_label

VERSION=1
BACKUPS='.time-setting-backups'


def bad(field,message,code='bad_request'):raise ConfigError(field,message,code=code)


def settings_for(world):
    time=world.get('time') or {};presentation=time.get('presentation') or {};slots=list(time.get('slots') or [])
    if not isinstance(presentation,dict):presentation={}
    return {**({'date_calendar':deepcopy(time['date_calendar'])} if 'date_calendar' in time else {}),'calendar':presentation.get('calendar') if presentation.get('calendar') in ('ordinal','none','real') else 'ordinal',
            'advance':'day' if presentation.get('advance')=='day' and len(slots)==1 else 'bands','step':1,'days':time.get('days',1),'slots':slots}


def normalize(raw):
    if not isinstance(raw,dict) or set(raw) not in ({'calendar','advance','step','days','slots'},{'calendar','advance','step','days','slots','date_calendar'}):bad('settings','暦・進み方・期間を確認してください')
    if raw['calendar'] not in ('ordinal','none','real'):bad('calendar','実在暦への切替と独自の年月日暦は、契約を含む新実行版の作成が必要です','unsupported')
    if raw['advance'] not in ('bands','day') or type(raw['step']) is not int or raw['step']!=1:bad('advance','通常ターンは1時間帯または1日ずつです。時間・週単位への変換は未対応です','unsupported')
    if type(raw['days']) is not int or not 1<=raw['days']<=10000:bad('days','期間は1〜10000日の整数で指定してください')
    slots=raw['slots']
    if not isinstance(slots,list) or not 1<=len(slots)<=100:bad('slots','時間帯は1〜100個で指定してください')
    if any(not isinstance(s,str) or not s.strip() or len(s)>120 or any(ord(c)<32 or ord(c)==127 for c in s) for s in slots):bad('slots','時間帯は空欄にせず120文字以内の名前を指定してください')
    slots=[s.strip() for s in slots]
    if len(slots)!=len(set(slots)):bad('slots','同じ名前の時間帯が重複しています')
    if raw['advance']=='day' and slots!=['1日']:bad('slots','1日ずつ進む場合は、1日につき「1日」の1枠を使います')
    result={**raw,'slots':slots}
    if 'date_calendar' in raw:
        try:result['date_calendar']=normalize_date_calendar(raw['date_calendar'],raw['days'])
        except ValueError as exc:bad('date_calendar','年月日・日数・週末・指定休日を確認してください。'+str(exc))
    if raw['calendar']=='real' and not result.get('date_calendar'):bad('date_calendar','開始年月日と終了年月日を入力してください')
    return result


def logical_preview(settings):
    axis=TimeAxis.logical(settings['days'],settings['slots']);count=axis.end+1;indices=list(range(min(5,count)))
    if count>5:indices.append(count-1)
    rows=[{**axis.label(i),'turn':i+1,**date_label({'days':settings['days'],'date_calendar':settings.get('date_calendar')},axis.label(i)['day'])} for i in indices]
    return {'kind':'logical','days':settings['days'],'slots_per_day':len(settings['slots']),'total_steps':count,'rows':rows,'omitted':max(0,count-len(rows)),'one_step':'全人物が順に行動する1巡。分・時間の長さには換算しません。','start_day':1,'start_slot':settings['slots'][0],'end_day':settings['days'],'end_slot':settings['slots'][-1]}


def inspect(world,people,source):
    mode=(world.get('slot_execution') or {}).get('mode')
    result={'date_calendar_version':1,'version':VERSION,'mode':mode or 'logical','can_edit_logical':mode is None,'current':settings_for(world),'calendar':None,'warnings':[]}
    if mode=='calendar_promises_v2':
        try:
            from engine.world import World
            from engine.subject import Subject
            from engine.calendar_v2 import compile_calendar,minute_for,time_label
            runtime=World(deepcopy(world),source);runtime.resolve_truth(0);runtime.bind_subjects({p['id']:Subject.from_definition(deepcopy(p)) for p in people});contract=runtime.slot_contract
            calendar=compile_calendar(contract['calendar'],timezone=contract['timezone'],scheduled_events=list(runtime.scheduled_events))
            first=minute_for(calendar,calendar['start_date'],contract['start_time']);last=minute_for(calendar,calendar['end_date'],contract['end_time']);points=[p for p in calendar['checkpoints'] if first<=p<=last];indices=points[:5]+([points[-1]] if len(points)>5 else [])
            result['calendar']={'kind':'real','timezone':contract['timezone'],'start':time_label(calendar,first),'end':time_label(calendar,last),'days':len(calendar['days']),'display_checkpoints':len(points),'rows':[time_label(calendar,p) for p in indices],'execution_unit':'分単位の次イベント境界','valid':True}
        except (ValueError,KeyError,TypeError,OSError) as error:result['calendar']={'kind':'real','valid':False,'error':str(error)}
        result['warnings'].append('Calendarは実行済みの契約・人物・目標・予定を含む方式です。この画面からの期間変更や下書き確定は、新しい実行版の作成が必要です。現在の契約を自動変更しません。')
    elif mode:result['warnings'].append('旧実行方式の互換設定を保持しています。この画面で通常ターンへ変換しません。')
    else:
        try:result['preview']=logical_preview(result['current'])
        except (ValueError,TypeError,KeyError):result['preview']=None;result['warnings'].append('既存の期間・時間帯に不正な値があります。修正して確認してください。')
    return result


def preparation_dependency(base):
    from execution.purpose_preparation import FILE, MAX_BYTES, _json, _record
    path=contained(base,FILE)
    if not path.exists():return None,None
    if path.stat().st_size>MAX_BYTES:bad('purpose_preparation','目標と準備の保存上限を超えています')
    raw=path.read_bytes()
    try:value=_record(_json(raw))
    except (ValueError,UnicodeError,TypeError,RecursionError) as error:bad('purpose_preparation','保存済みの目標と準備を確認してください。'+str(error))
    return value,sha256(raw)


def dependency(base):
    from engine.time_pressure import FILENAME,MAX_BYTES
    path=contained(base,FILENAME);value=None;legacy_hash=sha256(b'')
    if path.exists():
        if path.stat().st_size>MAX_BYTES:bad('time_pressure','期限の根拠がサイズ上限を超えています')
        raw=path.read_bytes()
        from execution.time_pressure_settings import _UniqueLoader,_stored
        try:value=_stored(yaml.load(raw.decode('utf-8'),Loader=_UniqueLoader))
        except (ValueError,UnicodeError,yaml.YAMLError) as error:bad('time_pressure','保存済みの期限の根拠を確認してください。'+str(error))
        legacy_hash=sha256(raw)
    _,preparation_hash=preparation_dependency(base)
    return value,legacy_hash if preparation_hash is None else sha256(canonical({'legacy':legacy_hash,'purpose_preparation':preparation_hash}))


def preparation_transition(record,world,candidate):
    if not record or not record.get('enabled'):return [],[]
    before=world.get('time') or {};after=candidate.get('time') or {};errors=[];warnings=[]
    pressure=record['pressure'];active=set(record['managed_ids']);days=after.get('days',1);slots=after.get('slots') or []
    if pressure['enabled']:
        for entry in pressure['entries']:
            if entry['candidate_id'] not in active:continue
            if entry['day']>days:errors.append({'field':'purpose_preparation','message':'目標と準備の期限が新しい期間外になります。期限と切迫で先に編集してください。'})
            if entry['slot'] not in slots:errors.append({'field':'purpose_preparation','message':'目標と準備の期限が参照する時間帯「'+entry['slot']+'」がなくなります。期限と切迫で先に編集してください。'})
    if before!=after:
        warnings.append({'field':'purpose_preparation','message':'時間設定の変更後は「話の目的→目標と準備」で候補と期限の参照を更新・確認してから、新しい実行を開始してください。保存済みの目標や期限を自動で移し替えません。'})
    if pressure['enabled'] and before.get('slots')!=slots:
        warnings.append({'field':'purpose_preparation','message':'時間帯の並び・枠数が変わると、同じ日・時間帯の期限でも残りターン数が変わります。人物の見積と期限の意味を確認してください。'})
    return errors,warnings


def temporal_warnings(world,people,days,slots):
    """Only prove impossibility for the temporal projection; never guess other state."""
    from execution.ending_settings import parse_when
    horizon=days*len(slots);warnings=[];ids=world.get('target_ending') or [];ids=[ids] if isinstance(ids,str) else ids
    for ending in world.get('ending') or []:
        if not isinstance(ending,dict) or ids and ending.get('id') not in ids:continue
        source=ending.get('when')
        if not isinstance(source,str) or not re.search(r'\b(?:day|turn)\b',source):continue
        label=ending.get('label') or ending.get('id') or '結末'
        try:tree,_=parse_when(source,world,people,existing=True)
        except (ValueError,TypeError,KeyError):
            warnings.append({'field':'ending','message':f'判定対象の結末「{label}」の複雑な時点条件は、自動の範囲検証では確認できません。条件を確認してください。'});continue
        atoms=[]
        def walk(n):
            if n.get('kind') in ('all','any'):
                for child in n['children']:walk(child)
            elif n.get('kind') in ('day','turn'):atoms.append(n)
        walk(tree)
        if not atoms:continue
        points={1,horizon}
        for atom in atoms:
            v=atom['value']
            for number in {math.floor(v)-1,math.floor(v),math.floor(v)+1,math.ceil(v)-1,math.ceil(v),math.ceil(v)+1}:
                if atom['kind']=='turn':points.add(min(horizon,max(1,number)))
                else:
                    for turn in [(number-1)*len(slots)+1,number*len(slots)]:points.add(min(horizon,max(1,turn)))
        def possible(n,turn):
            kind=n.get('kind')
            if kind=='all':return all(possible(c,turn) for c in n['children'])
            if kind=='any':return any(possible(c,turn) for c in n['children'])
            if kind not in ('day','turn'):return True
            actual=turn if kind=='turn' else (turn-1)//len(slots)+1;value=n['value'];op=n.get('cmp','>=')
            return {'==':actual==value,'!=':actual!=value,'>':actual>value,'>=':actual>=value,'<':actual<value,'<=':actual<=value}[op]
        if not any(possible(tree,t) for t in points):warnings.append({'field':'ending','message':f'判定対象の結末「{label}」は、新しい期間内に時点条件が成立しません。結末は自動変更しないため、目的の条件を確認してください。'})
    for key,label in [('phase_rules','通過段階'),('concerns','状態・条件')]:
        if re.search(r'\b(?:day|turn)\s*(?:[<>=!])',json.dumps(world.get(key),ensure_ascii=False)):
            warnings.append({'field':key,'message':f'{label}に時点条件があります。日数・時間帯の変更後の意味を確認してください。自動で書き換えません。'})
    return warnings


def transition(world,candidate,people,source,pressure=None):
    """Validate references without moving, truncating or rewriting them."""
    record,_=preparation_dependency(source.parent)
    errors,warnings=preparation_transition(record,world,candidate);time=candidate.get('time') or {};days=time.get('days',1);slots=time.get('slots') or [];horizon=days*len(slots)
    def error(field,message):
        if len(errors)<200:errors.append({'field':field,'message':message})
    for i,event in enumerate(candidate.get('scheduled_events') or []):
        if not isinstance(event,dict):error('scheduled_events.'+str(i),'出来事の形式を確認してください');continue
        label=event.get('label') or event.get('id') or str(i+1)
        if type(event.get('day')) is not int or not 1<=event['day']<=days:error('scheduled_events.'+str(i),f'出来事「{label}」の指定日が期間外になります')
        if event.get('slot') not in (None,'',*slots):error('scheduled_events.'+str(i),f'出来事「{label}」の時間帯「{event.get("slot")}」がなくなります。出来事を先に編集してください')
    responses=candidate.get('responses') or {}
    if not isinstance(responses,dict):error('responses','状態変化の形式を確認してください');responses={}
    for kind,field in [('events','turn'),('signals','deadline'),('windows','end')]:
        for i,entry in enumerate(responses.get(kind) or []):
            value=entry.get(field) if isinstance(entry,dict) else None
            if value is not None and (type(value)is not int or not 1<=value<=horizon):error(f'responses.{kind}.{i}.{field}','状態変化・期限の指定turnが期間外になります')
    for i,entry in enumerate(responses.get('windows') or []):
        if not isinstance(entry,dict):error(f'responses.windows.{i}','判定期間の形式を確認してください');continue
        if type(entry.get('start')) is int and entry['start']>horizon:error(f'responses.windows.{i}.start','判定開始turnが期間外になります')
    if pressure is not None:
        from engine.time_pressure import validate_sources
        try:validate_sources(pressure,candidate,{p['id']:p for p in people})
        except (ValueError,TypeError,KeyError) as exc:error('time_pressure','期限の根拠の参照を維持できません。'+str(exc))
    if not errors:
        from engine.world import World
        from engine.subject import Subject
        try:
            runtime=World(deepcopy(candidate),source);runtime.resolve_truth(0);runtime.bind_subjects({p['id']:Subject.from_definition(deepcopy(p)) for p in people})
        except (ValueError,TypeError,KeyError,OSError) as exc:error('runtime','実行の参照整合を確認できません。'+str(exc))
    old=world.get('time') or {};changed=old.get('days')!=days or list(old.get('slots') or [])!=slots
    if changed:warnings.extend(temporal_warnings(candidate,people,days,slots))
    if changed and list(old.get('slots') or [])!=slots:
        for key,label in [('scheduled_events','予定された出来事'),('goal_rules','達成・失敗の判定期間'),('responses','状態変化・期限'),('action_timing','所要turn'),('joint_activities','共同活動')]:
            if world.get(key):warnings.append({'field':key,'message':f'{label}の参照は保持しますが、時間帯の並び・1日の枠数の変更で日内の意味が変わります。自動の移し替えはしません。'})
        if pressure and pressure.get('sources'):warnings.append({'field':'time_pressure','message':'期限の根拠を保持します。時間の余裕の解釈が変わるため確認してください。'})
    old_dates=old.get('date_calendar');new_dates=time.get('date_calendar')
    if old_dates!=new_dates:
        warnings.append({'field':'date_calendar','message':'日番号・ターン番号・時間帯の予定と期限はそのまま保持します。開始日を変えると、それらに対応する年月日・曜日が移ります。既存の実行・再開ログは変換しません。'})
        warnings.append({'field':'date_calendar','message':'曜日・週末・指定休日は日付情報です。祝日の自動取得や、休日・夜間による立入／行動制限は、この設定だけでは発動しません。'})
    return errors,warnings


def check(files,revision,settings,source,store=None,ident=None):
    world=files['world.yaml'];people=[p for rel,p in files.items() if rel!='world.yaml'];settings=normalize(settings)
    if world.get('slot_execution') is not None:bad('time','現在は通常ターンではありません。契約・目標・予定を含む新しい実行版への移行が必要です','unsupported')
    old_presentation=(world.get('time') or {}).get('presentation') or {}
    if not isinstance(old_presentation,dict):bad('time.presentation','既存の表示設定の形式が未対応です。勝手に置換せず詳細設定を確認してください','unsupported')
    candidate=deepcopy(world);candidate['time']={**(world.get('time') or {}),'days':settings['days'],'slots':settings['slots'],'presentation':{**old_presentation,'calendar':settings['calendar'],'advance':settings['advance']}}
    if 'date_calendar' in settings:candidate['time']['date_calendar']=deepcopy(settings['date_calendar'])
    from execution import time_slot_references as slot_refs
    if store is None or ident is None:bad('time_references','現在の世界と経路調査の参照を確認してから適用してください')
    catalog=slot_refs.snapshot(store,ident,world)
    pressure,dependency_hash=dependency(source.parent);errors,warnings=transition(world,candidate,people,source,pressure)
    ref_errors,ref_warnings=slot_refs.validate(catalog,settings['slots'],settings['days'])
    errors.extend(e for e in ref_errors if e not in errors);warnings.extend(w for w in ref_warnings if w not in warnings)
    token=sha256(canonical({'version':VERSION,'revision':revision,'settings':settings,'dependency':dependency_hash,'slot_references':catalog['dependency_hash']}))
    return {'version':VERSION,'revision':revision,'settings':settings,'can_apply':not errors,'errors':errors,'warnings':warnings,'confirmation':token,'preview':logical_preview(settings),'candidate':candidate,'dependency_hash':dependency_hash,'slot_dependency_hash':catalog['dependency_hash'],'slot_refs_version':catalog['version'],'slot_references':catalog['references'],'slot_reference_errors':catalog['errors'],'slot_reference_warnings':catalog['warnings']}


def public_check(result):return {k:v for k,v in result.items() if k not in ('candidate','dependency_hash','slot_dependency_hash')}


def read(store,ident,body):
    from execution import world_editor,person_values
    base=world_editor._base(store,ident)
    if contained(base,person_values.JOURNAL).exists():bad('revision','保存処理の完了を確認してください','conflict')
    files,revision=world_editor._read(store,ident)
    if body['revision']!=revision:bad('revision','別の画面で設定が更新されています。入力を控えて再読み込みしてください','conflict')
    from execution import time_slot_references as slot_refs
    catalog=slot_refs.snapshot(store,ident,files['world.yaml'])
    if body['operation']=='time-basic-inspect':
        result={**inspect(files['world.yaml'],[p for rel,p in files.items() if rel!='world.yaml'],base/'world.yaml'),'revision':revision,
                'slot_refs_version':catalog['version'],'slot_references':catalog['references'],
                'slot_reference_errors':catalog['errors'],'slot_reference_warnings':catalog['warnings']}
    else:
        checked=check(files,revision,body['values']['settings'],base/'world.yaml',store,ident)
        if dependency(base)[1]!=checked['dependency_hash']:bad('revision','確認中に期限の根拠が変わりました','conflict')
        result=public_check(checked)
    if slot_refs.snapshot(store,ident,files['world.yaml'])['dependency_hash']!=catalog['dependency_hash']:bad('revision','確認中に経路調査の参照が変わりました','conflict')
    if body['operation']=='time-basic-check' and catalog['dependency_hash']!=checked['slot_dependency_hash']:bad('revision','確認中に経路調査の参照が変わりました','conflict')
    if world_editor._read(store,ident)[1]!=revision or contained(base,person_values.JOURNAL).exists():bad('revision','確認中に設定が更新されました','conflict')
    return result


def backup(base,ident,revision,before,text,settings):
    after_hash=sha256(text.encode('utf-8'));token=revision[:16]+'-'+after_hash[:16]
    record={'version':1,'world_id':ident,'before_revision':revision,'before_world_sha256':sha256(before),'after_world_sha256':after_hash,'world_text':before.decode('utf-8'),'settings':settings}
    path=contained(base,BACKUPS+'/'+token+'.json')
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8'))!=record:bad('backup','変更前バックアップが一致しません','conflict')
    else:atomic_json(path,record)
    return token


def apply_checked(store,ident,files,revision,values):
    from execution import world_editor
    base=world_editor._base(store,ident);result=check(files,revision,values['settings'],base/'world.yaml',store,ident)
    if result['errors']:bad('time',' / '.join(e['message'] for e in result['errors'][:8]))
    if values.get('confirmation')!=result['confirmation']:bad('confirmation','現在の入力と依存関係を確認してから適用してください','conflict')
    text=yaml.safe_dump(result['candidate'],allow_unicode=True,sort_keys=False);before=(base/'world.yaml').read_bytes();token=backup(base,ident,revision,before,text,result['settings'])
    from execution import time_slot_references as slot_refs
    if slot_refs.snapshot(store,ident,files['world.yaml'])['dependency_hash']!=result['slot_dependency_hash']:bad('revision','適用前に経路調査の参照が変わりました','conflict')
    world_editor_result=world_editor._read(store,ident)
    if world_editor_result[1]!=revision or dependency(base)[1]!=result['dependency_hash']:bad('revision','適用前に設定または期限の根拠が変わりました','conflict')
    store._write_file('world',ident,'world.yaml',text)
    response=world_editor.snapshot(store,ident);response['time_backup']={'id':token,'before_revision':revision,'after_revision':response['revision']}
    return response


def inspect_design(settings,world,people,source):
    """Compile dates/events and validate current contract; never make an executable version."""
    from engine.calendar_v2 import compile_calendar
    mode=(world.get('slot_execution') or {}).get('mode');report={'can_finalize':False,'calendar_compiles':False,'errors':[],'blockers':[],'current_mode':mode or 'logical'};timezone='Asia/Tokyo';contract=None
    if mode=='calendar_promises_v2':
        from engine.world import World
        from engine.subject import Subject
        try:
            runtime=World(deepcopy(world),source);runtime.resolve_truth(0);runtime.bind_subjects({p['id']:Subject.from_definition(deepcopy(p)) for p in people});contract=runtime.slot_contract;timezone=contract['timezone']
        except (ValueError,KeyError,TypeError,OSError) as error:report['errors'].append(str(error))
    else:report['blockers']=['通常ターンの予定・目標の日時対応','契約・人物対応・所要時間・期限を含むCalendar実行定義']
    try:
        calendar=compile_calendar(settings,timezone=timezone,scheduled_events=world.get('scheduled_events') or []);report.update(calendar_compiles=True,timezone=timezone,days=len(calendar['days']))
        if contract:
            from engine.calendar_v2 import minute_for
            from engine.task_windows import compile_windows
            from engine.calendar_observers import validate_observers
            compile_windows(contract['tasks'],calendar)
            validate_observers(contract.get('observers',[]),calendar,set(contract['roles']),set(runtime.zones),set(contract['tasks']))
            first=minute_for(calendar,calendar['start_date'],contract['start_time']);last=minute_for(calendar,calendar['end_date'],contract['end_time'])
            for goal in (world.get('goal_rules') or {}).get('definitions',[]):
                if (world.get('goal_rules') or {}).get('version')==2:
                    start,end=[minute_for(calendar,goal['period'][key]['date'],goal['period'][key]['time']) for key in ['start','end']]
                    if not first<=start<=end<=last:report['errors'].append('目標「'+str(goal.get('condition'))+'」の判定期間が候補暦の観測範囲外です。')
            if contract.get('fact_model'):
                from engine.fact_runtime import validate_model
                validate_model(contract['fact_model'],set(contract['roles']),first,last)
    except (ValueError,KeyError,TypeError) as error:report['errors'].append(str(error))
    report['blockers'].append('変更前後の影響を確認した新しい固定実行版の作成。この画面からは未対応で、既存世界や実行ログを変換しません。')
    report['references_valid']=not report['errors']
    return report
