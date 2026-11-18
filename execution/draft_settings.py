"""Explicit draft execution inputs, using existing normal-world contracts.

No prose inference, default priorities, execution, or model calls.
"""
from copy import deepcopy
import math

from execution.provenance import ConfigError

FIELDS = {'time', 'scheduled_events', 'action_usage', 'mechanics', 'concerns', 'goal_rules', 'planning', 'responses'}


def bad(field, message):
    raise ConfigError('settings.' + field, message)


def normalize(raw):
    if not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != 1 or set(raw) - {'version', *FIELDS}:
        bad('version', '実行設定はversion 1の対応項目で指定してください')
    result = deepcopy(raw)
    if 'time' in raw:
        time = raw['time']
        if not isinstance(time, dict) or not {'days','slots'} <= set(time) or set(time) - {'days','slots','presentation','date_calendar'}:
            bad('time', '通常方式の日数と時間帯を指定してください。暦の変更はこの下案では未対応です')
        from execution.time_basic import normalize as normalize_time
        presentation=time.get('presentation',{})
        if not isinstance(presentation,dict) or set(presentation)-{'calendar','advance'}:
            bad('time.presentation','対応する暦表示・進み方を指定してください')
        value = normalize_time({'calendar':presentation.get('calendar','ordinal'), 'advance':presentation.get('advance','bands'), 'step':1,
                                'days':time['days'],'slots':time['slots'], **({'date_calendar':time['date_calendar']} if 'date_calendar' in time else {})})
        result['time'] = {key:value[key] for key in ('days', 'slots')}
        if 'presentation' in time:result['time']['presentation']=deepcopy(presentation)
        if 'date_calendar' in value:result['time']['date_calendar']=value['date_calendar']
    if 'scheduled_events' in raw and (not isinstance(raw['scheduled_events'], list) or len(raw['scheduled_events']) > 100):
        bad('scheduled_events', '予定された出来事は100件以内の一覧で指定してください')
    if 'mechanics' in raw:
        from engine.action_usage import FEATURES
        value=raw['mechanics']
        if not isinstance(value,dict) or set(value)!={'version',*FEATURES} or type(value['version']) is not int or value['version']!=1 or any(type(value[k]) is not bool for k in FEATURES):
            bad('mechanics','既存の世界機能と真偽値で指定してください')
    if 'action_usage' in raw:
        from engine.action_usage import VERSION,CONTRACT,REQUIRED
        value=raw['action_usage'];base={'version','engine_contract','catalog_version','enabled'}
        if (not isinstance(value,dict) or not base<=set(value) or set(value)-base-{'constraints'}
                or type(value['version']) is not int or value['version']!=VERSION or value['engine_contract']!=CONTRACT
                or type(value['catalog_version']) is not int or value['catalog_version']!=1):
            bad('action_usage','対応する行動設定の版と項目を指定してください')
        enabled=value['enabled']
        if not isinstance(enabled,list) or len(enabled)>4096 or any(not isinstance(v,str) for v in enabled) or len(set(enabled))!=len(enabled) or not REQUIRED<=set(enabled):
            bad('action_usage.enabled','使用する行動を重複せず指定し、必須休息を残してください')
        constraints=value.get('constraints',{})
        if not isinstance(constraints,dict) or len(constraints)>4096:bad('action_usage.constraints','人物と実行可能時間帯を行動ごとに指定してください')
        for ref,rule in constraints.items():
            if not isinstance(ref,str) or ref in REQUIRED or not isinstance(rule,dict) or set(rule)!={'actors','slots'}:
                bad('action_usage.constraints','対応する行動の人物・時間帯を指定してください')
            for key,selected in rule.items():
                if selected is not None and (not isinstance(selected,list) or len(selected)>1000 or any(not isinstance(v,str) or not v for v in selected) or len(set(selected))!=len(selected) or key=='slots' and not selected):
                    bad('action_usage.constraints','重複しない人物・時間帯の一覧を指定してください')
    if 'concerns' in raw:
        from engine.concerns import Concerns
        try:
            if not isinstance(raw['concerns'], dict) or raw['concerns'].get('version') != 2:
                raise ValueError('人物の優先事項は人物側で指定してください')
            Concerns(raw['concerns'])
        except (ValueError, TypeError, KeyError) as error:
            bad('concerns', '条件定義を確認してください。' + str(error))
    return result


def apply(world, settings):
    for key in FIELDS & set(settings):
        world[key] = deepcopy(settings[key])
    return world


def validate_runtime(world, people):
    """Validate references with the same runtime used by saved worlds."""
    from engine.action_constraints import effective_verbs
    from engine.actions import FORCE_ARG_KINDS
    from execution.scheduled_event_safety import _changed_references
    ids = set(people)
    for person in people.values():
        if person.commitments and world.concerns is None:
            bad('concerns', '人物の優先事項が参照する条件定義が必要です')
    for ref, rule in (world.action_usage or {}).get('constraints', {}).items():
        if rule['actors'] is not None and set(rule['actors']) - ids:
            bad('action_usage.constraints', '登録されていない人物が指定されています: ' + ref)
    seen = set()
    for i, event in enumerate(world.scheduled_events):
        field = 'scheduled_events.' + str(i)
        allowed = {'id','label','day','slot','targets','grants_item','grants_fact','move_to','stress_delta','force_action'}
        if set(event) - allowed or not {'id','day','targets'} <= set(event):
            bad(field, '出来事のID・日・対象と対応する効果を指定してください')
        ident = event['id']
        if not isinstance(ident, str) or not ident.strip() or len(ident) > 120 or any(ord(c) < 32 for c in ident) or ident in seen:
            bad(field + '.id', '出来事のIDは空欄や重複を避けて指定してください')
        seen.add(ident)
        if type(event['day']) is not int or not 1 <= event['day'] <= world.days:
            bad(field + '.day', '出来事の日は設定した期間内で指定してください')
        if 'slot' in event and (not isinstance(event['slot'], str) or event['slot'] not in world.slots):
            bad(field + '.slot', '登録済みの時間帯を指定してください')
        targets = event['targets']
        if not isinstance(targets, list) or not targets or any(not isinstance(v, str) or v not in ids for v in targets) or len(set(targets)) != len(targets):
            bad(field + '.targets', '対象には登録済みの人物を重複せず指定してください')
        if 'label' in event and (not isinstance(event['label'], str) or len(event['label']) > 120):
            bad(field + '.label', '出来事の表示名は120文字以内で指定してください')
        if 'stress_delta' in event and (type(event['stress_delta']) not in (int,float) or not math.isfinite(event['stress_delta']) or abs(event['stress_delta']) > 1000):
            bad(field + '.stress_delta', 'ストレスの増減は-1000〜1000で指定してください')
        force = event.get('force_action')
        if force is not None:
            if not isinstance(force, dict) or set(force) - {'verb','args'} or 'verb' not in force or force['verb'] not in FORCE_ARG_KINDS:
                bad(field + '.force_action', '対応する行動と引数を指定してください')
            args = force.get('args', [])
            kinds = FORCE_ARG_KINDS[force['verb']]
            if not isinstance(args, list) or len(args) > len(kinds) or any(not isinstance(a,str) or not a or len(a)>120 for a in args):
                bad(field + '.force_action.args', '行動の引数を確認してください')
            for kind, value in zip(kinds, args):
                if ((kind == 'zone' and value not in world.zones) or (kind == 'subject' and value not in people)
                        or (kind == 'fact' and value not in world.facts and value != '雑談')
                        or (kind.startswith('enum:') and value not in kind[5:].split('|'))):
                    bad(field + '.force_action.args', '行動が参照する人物・場所・情報を確認してください')
            from engine.action_usage import reference
            rule = (world.action_usage or {}).get('constraints', {}).get(reference(force['verb'], args), {})
            for actor in targets:
                if force['verb'] not in effective_verbs(world, people[actor]) or (rule.get('actors') is not None and actor not in rule['actors']):
                    bad(field + '.force_action', '対象の人物が実行できない行動です')
            if rule.get('slots') is not None and event.get('slot') not in rule['slots']:
                bad(field + '.force_action', '行動が許可されない時間帯に予定されています')
        _changed_references([(event, None)], world)
