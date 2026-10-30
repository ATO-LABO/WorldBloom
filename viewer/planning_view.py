"""Read-only purpose timelines: separate attempts, forecasts and world outcomes."""
from copy import deepcopy
from hashlib import sha256
from html import escape
from urllib.parse import quote

from viewer import ui


def e(value):
    return escape(str(value), quote=True)


def activity_time(activity, key):
    label = activity.get(key + '_label')
    if isinstance(label, dict) and type(label.get('day')) is int and isinstance(label.get('slot'), str):
        return str(label['day']) + '日目・' + label['slot']
    point = activity.get(key)
    if type(point) is not int: return '不明'
    return ('開始から' + str(point) + '分' if activity.get('unit') == 'minute' else '時点 ' + str(point + 1))


STATUS = {'pending': '継続中', 'not_started': '判定前', 'achieved': '達成', 'failed': '失敗確定', 'unknown': '判定できない'}
KINDS = {'initial': '最初の計画', 'failure': '手段の失敗', 'replan': '計画を変更',
         'complete': '作業を完了', 'information': '情報を確認', 'observe': '観測へ移動',
         'offer': '交換を申し入れ', 'accepted': '交換が成立', 'refused': '交換を拒否された',
         'pause': '手順を探し直す', 'achieved': '目的を達成', 'failed': '目的の失敗確定', 'routine': '行動の記録'}
KINDS.update(arrived='目的地に到着', interrupted='行動を中断', unfinished='観測終了時も継続中')
KINDS.update(recovered='休息で回復')
KINDS.update(social='相手とのやり取り')


def point_label(explanation, point):
    if explanation.get('calendar') and type(point) is int:
        from engine.calendar_v2 import time_label
        value = time_label(explanation['calendar'], point)
        return value['date'] + ' ' + value['time']
    return '時点 ' + str(point)


def step_key(step):
    return step.get('verb'), tuple(map(str, step.get('args', [])))


def step_label(step):
    args = step.get('args', [])
    if step.get('verb') == 'move': return (str(args[0]) if args else '目的地') + 'へ移動'
    if step.get('verb') == 'negotiate':
        return (str(args[0]) if args else '相手') + 'に' + str(step.get('meta', {}).get('objective', '品物')) + 'の交換を申し入れる'
    return str(step.get('label') or '記録された作業')


def event_label(event):
    social=event.get('social')
    if not social: return KINDS[event['kind']]
    who=social.get('actor') or social.get('from') or '相手'
    if social['kind']=='cooperation':
        return who+'：'+{None:'協力を依頼','accept':'協力を承諾','refuse':'協力を拒否',
                        'completed':'依頼作業を完了','failed':'依頼作業に失敗'}.get(social.get('answer'),'返答')
    if social['kind']=='disclosure': return who+'→'+social['to']+'：情報共有'
    verb=social.get('verb');revision='（第'+str(social.get('revision','?'))+'版）'
    action=({'support':'賛成','oppose':'反対','undecided':'判断材料が不足'}.get(social.get('stance'),'審査')
            if verb=='review_proposal' else {'approved':'承認','rejected':'却下','deferred':'保留'}.get(social.get('status'),'裁定')
            if verb=='decide_proposal' else {'submit_proposal':'提案を提出','amend_proposal':'提案を修正',
            'request_decision':'判断を依頼','present_proposal':'提案を説明'}.get(verb,'審議'))
    return who+'：'+action+revision


def model(explanation):
    groups = {}; pending_offers = {}
    records = sorted([*explanation.get('decisions', []), *explanation.get('activity_records', []),
                      *explanation.get('planning_assessments', []),*explanation.get('social_records',[])], key=lambda r:r['line'])
    for item in records:
        detail = item.get('outcome', {}).get('details') or {}
        plan = detail.get('planning')
        if isinstance(plan, dict) and plan.get('root'):
            key = item['subject'], plan['root']
            group = groups.setdefault(key, {'actor': key[0], 'root': key[1], 'label': plan.get('goal_label') or '選んだ目的',
                                           'events': [], 'records': [], 'outcomes': [], 'previous': None, 'failure': None})
            previous = group['previous']
            current_steps = plan.get('steps', [])
            reference = (previous or {}).get('plan')
            if reference and reference.get('status') == 'awaiting_consent': reference = group.get('offer_plan') or reference
            expected = deepcopy((reference or {}).get('steps', []))
            if previous and previous['result'] in {'moved', 'responded', 'rested', 'delivered', 'waited',
                    'crafted','investigated','observed','neutralized','given','persuaded','won','rethought','unchanged','exposed','rescued'} and expected: expected = expected[1:]
            elif previous and group.get('reply') == 'accepted' and expected and expected[0]['verb'] == 'negotiate': expected = expected[1:]
            changed = previous is not None and [step_key(s) for s in current_steps] != [step_key(s) for s in expected]
            result = item['outcome'].get('result')
            status = plan.get('status')
            response = detail.get('response') or {}
            kind = 'routine'
            if previous is None: kind = 'initial'
            if result in {'failed', 'invalid','lost','misjudged'}: kind = 'failure'
            elif item['verb'] == 'negotiate' and result == 'offered': kind = 'offer'
            elif status in {'no_known_plan', 'search_limit'} and (not previous or status != previous['plan'].get('status')): kind = 'pause'
            elif status == 'seeking_information' and (not previous or previous['plan'].get('observation_site') != plan.get('observation_site')): kind = 'observe'
            elif changed and current_steps and previous is not None and ('received_events' not in plan or
                    plan.get('reason') in {'previous_action_failed','action_interrupted','new_information',
                    'cooperation_refused','cooperation_failed','cooperation_accepted','cooperation_completed','review_received','judgment_received','information_shared'}): kind = 'replan'
            elif previous is not None and result == 'responded':
                kind = ('information' if any(v.get('kind') == 'knowledge' and v.get('actor') == item['subject']
                        and v.get('value') is True for v in response.get('effects', [])) else 'complete')
            activity = detail.get('activity')
            if activity:
                if activity.get('phase') == 'completed' and item['verb'] == 'move': kind = 'arrived'
                elif activity.get('phase') == 'completed' and item['verb'] == 'rest': kind = 'recovered'
                elif activity.get('phase') in {'interrupted','unfinished'}: kind = activity['phase']
            action_label = next((step_label(s) for s in current_steps if step_key(s) == (item['verb'], tuple(map(str, item.get('args', []))))),
                                '状況を確かめる' if status == 'seeking_information' else '次の手順を検討する')
            event = {'turn': item['turn'], 'line': item['line'], 'source': item['source'], 'kind': kind,
                     'plan': deepcopy(plan), 'result': result, 'action': action_label,
                     'before': deepcopy(reference), 'cause': deepcopy(group['failure']) if kind in {'replan', 'pause', 'observe'} and
                         (plan.get('reason') in {'previous_action_failed', 'reply_received'} or previous and previous['kind'] == 'failure') else None,
                     'outcome': None, 'progress': response.get('progress'), 'total': response.get('steps')}
            if activity: event['activity'] = deepcopy(activity)
            if item.get('source_kind') == 'execution': event['source_kind'] = 'execution'
            if item.get('source_kind') == 'assessment': event['source_kind'] = 'assessment'
            group['records'].append(event)
            group['events'].append(event)
            if kind == 'failure': group['failure'] = {'turn': item['turn'], 'line': item['line'], 'action': action_label, 'kind': 'failure'}
            if kind == 'offer':
                pending_offers[(item['subject'], (item.get('args') or [''])[0], detail.get('objective'))] = group
                group['offer_plan'] = deepcopy(plan)
            if status != 'awaiting_consent': group.pop('reply', None)
            group['previous'] = event
        social=detail.get('social_event')
        binding=(social or {}).get('binding')
        if binding and (binding['actor'],binding['root']) in groups:
            group=groups[binding['actor'],binding['root']]
            event=next((v for v in group['events'] if v['line']==item['line']),None)
            if event is None:
                event={'turn':item['turn'],'line':item['line'],'source':item['source'],'kind':'social',
                    'plan':None,'result':item['outcome'].get('result'),'action':'実際に届いたやり取り',
                    'before':None,'cause':None,'outcome':None,'progress':None,'total':None}
                group['events'].append(event)
            event['social']=deepcopy(social)
            if event['kind']=='routine': event['kind']='social'
        if item.get('verb') == 'concede':
            claimant = detail.get('claimant') or (item.get('args') or [''])[0]
            group = pending_offers.pop((claimant, item['subject'], detail.get('objective')), None)
            if group is not None and item['outcome'].get('result') in {'conceded', 'refused'}:
                group['reply'] = 'accepted' if item['outcome']['result'] == 'conceded' else 'refused'
                if group['reply'] == 'refused':
                    group['failure'] = {'turn': item['turn'], 'line': item['line'], 'action': item['subject'] + 'との交換', 'kind': 'refused'}
                group['events'].append({'turn': item['turn'], 'line': item['line'], 'source': item['source'],
                    'kind': 'accepted' if item['outcome']['result'] == 'conceded' else 'refused',
                    'plan': None, 'result': item['outcome']['result'], 'action': item['subject'] + 'の返答',
                    'before': None, 'cause': None, 'outcome': None, 'progress': None, 'total': None})
    outcomes = []
    for item in records:
        for outcome in (item.get('outcome', {}).get('details') or {}).get('objective_outcomes', []):
            outcomes.append({'turn': item['turn'], 'source': item['source'], 'outcome': outcome})
    outcomes.extend(explanation.get('planning_outcomes', []))
    outcomes.sort(key=lambda row: row['source']['line'])
    for group in groups.values():
        group['outcomes'] = [deepcopy(r) for r in outcomes if r['outcome'].get('condition') == group['root']]
        group['final'] = group['outcomes'][-1]['outcome'] if group['outcomes'] else {'status': 'unknown'}
        final = group['final']
        terminal = next((r for r in group['outcomes'] if r['outcome'].get('status') in {'achieved', 'failed'}), None)
        if terminal:
            same_point = [v for v in group['records'] if v['turn'] == final.get('decided_at')
                          and any(r['outcome'].get('status') == final['status'] and r['source']['line'] == v['line'] for r in group['outcomes'])]
            if same_point:
                chosen = same_point[-1]; chosen['kind'] = final['status']; chosen['outcome'] = deepcopy(final)
            else:
                group['events'].append({'turn': final.get('decided_at', terminal['turn']), 'line': terminal['source']['line'],
                    'source': terminal['source'], 'kind': final['status'], 'plan': None, 'result': final['status'],
                    'action': '世界の達成・失敗の判定', 'before': None, 'cause': None, 'outcome': deepcopy(final), 'progress': None, 'total': None})
        group['events'].sort(key=lambda row: row['line'])
        for i, event in enumerate(group['events']):
            prior = [o['outcome'] for o in group['outcomes'] if o['source']['line'] <= event['line']]
            event['actual'] = deepcopy(event.get('outcome') or (prior[-1] if prior else {'status': 'unknown'}))
            event['index'] = i
        group['default'] = next((v['index'] for v in group['events'] if v['kind'] == 'replan'),
                                next((v['index'] for v in group['events'] if v['kind'] == 'failure'), 0))
        group['turning_count'] = sum(v['kind'] != 'routine' for v in group['events'])
        group.pop('previous', None); group.pop('failure', None)
    return list(groups.values())


def _tasks(plan, *, old=None, failure=None, title='選択時の計画'):
    steps = (plan or {}).get('steps', [])
    tasks = [s for s in steps if s.get('verb') != 'move']
    moves = sum(s.get('verb') == 'move' for s in steps)
    body = '<section class="pw-plan"><h4>' + e(title) + '</h4>'
    if not tasks:
        return body + '<p class="ui-hint">実行する作業は、まだ記録されていません。</p></section>'
    body += '<p class="pw-caption">主な作業 ' + e(len(tasks)) + '件・移動 ' + e(moves) + '区間</p><ol class="pw-tasks">'
    existing = {step_key(s) for s in (old or {}).get('steps', [])}
    for task in tasks:
        label = step_label(task)
        tag = '失敗した手順' if failure and label == failure['action'] else '追加' if old is not None and step_key(task) not in existing else ''
        body += '<li><span>' + e(label) + '</span>' + (f'<small class="pw-tag">{e(tag)}</small>' if tag else '') + '</li>'
    return body + '</ol></section>'


def _source(explanation, event):
    src = explanation.get('source', {})
    if not src.get('experiment') or not src.get('cell'): return ''
    href = '/exp/' + quote(str(src['experiment']), safe='') + '/cell/' + quote(str(src['cell']), safe='')
    href += '/raw?line=' + str(int(event['source']['line'])) + '#L' + str(int(event['source']['line']))
    return '<a href="' + e(href) + '">この場面の原記録 L' + e(event['source']['line']) + '</a>'


def render(explanation, *, instance='planning'):
    groups = model(explanation)
    if not groups: return ''
    prefix = 'pw-' + sha256(instance.encode()).hexdigest()[:8]
    body = '<section class="ui-workspace pw-workspace" data-planning-workspace>'
    body += '<div class="pw-toolbar"><h2>目的と計画の変化</h2>'
    options = ''.join(f'<option value="{i}">{e(g["actor"])} ／ {e(g["label"])}</option>' for i, g in enumerate(groups))
    body += ui.field('人物と目的', '<select data-plan-group>' + options + '</select>') + '</div>'
    for gi, group in enumerate(groups):
        body += f'<section data-plan-group-panel="{gi}"' + ('' if gi == 0 else ' hidden') + '>'
        final = group['final']; status = final.get('status', 'unknown')
        body += '<header class="pw-goal"><div><p class="pw-caption">' + e(group['actor']) + 'が目指すこと</p><h3>' + e(group['label']) + '</h3></div>'
        body += '<div class="pw-final" data-plan-verdict="' + e(status) + '"><span class="pw-caption">記録の最後の判定（作者向け）</span><strong>' + e(STATUS.get(status, '不明')) + '</strong>'
        if final.get('decided_at') is not None: body += '<span>' + e(point_label(explanation,final['decided_at'])) + '</span>'
        if final.get('period'): body += '<span class="pw-caption">判定期間 ' + e(point_label(explanation,final['period'].get('start')) if explanation.get('calendar') else final['period'].get('start')) + '〜' + e(point_label(explanation,final['period'].get('end')) if explanation.get('calendar') else final['period'].get('end')) + '</span>'
        body += '</div></header><div class="pw-layout"><aside class="pw-history"><div class="pw-history-heading"><h3>変化のあった場面</h3>'
        body += f'<span class="pw-caption">{group["turning_count"]}件</span></div>'
        body += '<button type="button" class="ui-button" data-plan-all aria-pressed="false">全行動を表示</button>'
        body += '<div class="pw-events" aria-label="計画を追う場面">'
        for event in group['events']:
            idx = event['index']; active = idx == group['default']; key = f'{gi}-{idx}'
            body += f'<button type="button" class="pw-event ui-button" data-plan-event="{idx}" aria-controls="{prefix}-{key}" aria-pressed="{str(active).lower()}"'
            body += (' data-plan-routine hidden' if event['kind'] == 'routine' else '') + '>'
            body += '<span class="pw-caption">' + e(point_label(explanation,event['turn'])) + '</span><strong>' + e(event_label(event)) + '</strong></button>'
        body += '</div></aside><div class="pw-details">'
        for event in group['events']:
            idx = event['index']; key = f'{gi}-{idx}'; plan = event.get('plan') or {}
            body += f'<article id="{prefix}-{key}" data-plan-detail="{idx}"' + ('' if idx == group['default'] else ' hidden') + '>'
            caption = '開始済みの行動の経過と結果' if event.get('source_kind') == 'execution' else 'この場面の判断と結果'
            if event.get('source_kind') == 'assessment': caption = '手順の検討結果（行動は開始していません）'
            body += '<header class="pw-scene"><p class="pw-caption">' + caption + '</p><h3>' + e(point_label(explanation,event['turn'])) + ' ／ ' + e(event_label(event)) + '</h3></header>'
            if event.get('activity'):
                a = event['activity']
                phases = {'started':'開始（まだ完了していません）', 'progress':'継続中', 'completed':'完了',
                          'failed':'途中で失敗', 'interrupted':'中断', 'unfinished':'観測終了時に未完了'}
                body += '<p class="ui-notice">時間付きの行動：' + e(phases.get(a.get('phase'),'経過の記録'))
                body += ' ／ 開始 ' + e(activity_time(a,'start')) + ' → 完了予定 ' + e(activity_time(a,'finish'))
                body += ' ／ 消耗した体力 ' + e(a.get('spent'))
                if 'recovered' in a: body += ' ／ 回復した体力 ' + e(a['recovered'])
                body += '</p>'
            if event.get('social'):
                social=event['social']
                captions={'submit_proposal':'提案を提出','amend_proposal':'提案を修正','review_proposal':'自分の条件で審査',
                    'request_decision':'正式な判断を依頼','decide_proposal':'実際の裁定','present_proposal':'提案を説明'}
                answer={'accept':'協力を承諾（作業は別に実行）','refuse':'協力を拒否','failed':'依頼された作業に失敗','completed':'依頼された作業を完了',None:'協力を依頼（返事は未確定）'}
                label=('情報を共有' if social['kind']=='disclosure' else answer.get(social.get('answer'),'返事の記録')
                       if social['kind']=='cooperation' else captions.get(social.get('verb'),'審議の記録'))
                body+='<section class="ui-notice" data-social-receipt><h4>'+e(label)+'</h4>'
                if social.get('revision') is not None: body+='<p>提案の版：'+e(social['revision'])+'</p>'
                if social.get('stance'): body+='<p>審査の立場：'+e({'support':'賛成','oppose':'反対','undecided':'判断材料が不足'}.get(social['stance'],social['stance']))+'</p>'
                if social.get('status'): body+='<p>裁定：'+e({'approved':'承認','rejected':'却下','deferred':'保留'}.get(social['status'],social['status']))+'</p>'
                if social.get('statement'): body+='<p>'+e(social['statement'])+'</p>'
                if social.get('fact'): body+='<p>伝えた情報：'+e(social['fact'])+'</p>'
                if social.get('missing_knowledge'): body+='<p>相手が伝えた不足情報：'+e('、'.join(social['missing_knowledge']))+'</p>'
                if social.get('blockers'):
                    body+='<p>相手が示した受け入れ条件</p><ul>'
                    for blocker in social['blockers']:
                        body+='<li>'+e(blocker['term'])+'：現在は'+e(blocker['actual'])+' ／ 受け入れる値は'+e('、'.join(blocker['allowed']))+'</li>'
                    body+='</ul>'
                assessment=social.get('assessment') if social['kind']=='cooperation' else None
                if assessment:
                    body+='<p>返答した人物の判断（作者向け）</p><ul>'
                    impacted=[v for v in assessment.get('forecast',{}).get('entries',[]) if v['state']!='true']
                    for value in impacted:
                        body+='<li>'+e(value['label'])+'：'+('譲れない条件' if value['required'] else '優先度 '+e(value['priority']))
                        body+=' ／ '+('満たせない見込み' if value['state']=='false' else '満たせるか未確認')+'</li>'
                    if not impacted: body+='<li>条件・能力・時間など、その時点の実行可否を確認しています。</li>'
                    body+='</ul><p class="ui-hint">この評価の内容を、依頼した人物も知っているとは限りません。</p>'
                body+='<p>このやり取りを実際に受け取った人物：'+e('、'.join(social.get('received_by',[])))+'</p></section>'
            if plan.get('causes'):
                body+='<div class="pw-route"><p>計画を見直す際に受け取っていた記録</p><ul>'
                for cause in plan['causes']: body+='<li>'+_source(explanation,cause)+'</li>'
                body+='</ul></div>'
            if event.get('cause'):
                cause = event['cause']
                body += '<p class="ui-notice ui-notice--warning">見直しのきっかけ：時点 ' + e(cause['turn']) + 'で「' + e(cause['action'])
                body += ('」を断られた。' if cause.get('kind') == 'refused' else '」に失敗した。') + '</p>'
            elif event['kind'] == 'failure':
                text=('戦闘に敗れました。' if event['result']=='lost' else '告発は誤りでした。' if event['result']=='misjudged'
                      else '「'+event['action']+'」を実行できなかった。')
                body += '<p class="ui-notice ui-notice--warning">' + e(text) + '最終目的の判定とは分けて確認します。</p>'
            elif event['kind'] in {'accepted', 'refused'}:
                body += '<p class="ui-notice">' + e(event['action']) + '：' + ('交換が成立し、品物が移りました。' if event['kind'] == 'accepted' else '交換は成立せず、品物は移りませんでした。') + '</p>'
            elif event['kind'] in {'achieved', 'failed'}:
                reason = event['actual'].get('reason')
                text = '期限までに達成条件を満たさなかった。' if reason == 'deadline_unmet' else '世界の達成・失敗の判定が確定した。'
                body += '<p class="ui-notice">' + e(text) + '</p>'
            if plan.get('status') in {'no_known_plan', 'search_limit'}:
                text = '探索の上限に達しました。' if plan['status'] == 'search_limit' else '今の情報と使える手段では、実行する手順が見つかっていません。'
                body += '<p>' + e(text) + ' これだけで最終目的を失敗確定にはしません。</p>'
            if plan.get('status') == 'awaiting_consent': body += '<p>相手の返答を待っています。申し入れだけで品物を得た扱いにはしません。</p>'
            if plan.get('status') == 'seeking_information': body += '<p>次の手順を決めるため、情報を確かめます。</p>'
            if plan:
                result_label = {'moved': '移動完了', 'working': '作業中', 'responded': '作業完了',
                    'offered': '申し入れ済み', 'rested': '待機・休息', 'failed': '実行できなかった', 'invalid': '実行されなかった',
                    'started':'開始・未完了', 'ongoing':'開始済みの行動を継続中', 'interrupted':'中断', 'unfinished':'未完了',
                    'crafted':'製作完了','investigated':'調査完了','observed':'観察完了','neutralized':'制約を無力化',
                    'given':'品物を渡した','rescued':'救助した','persuaded':'説得を実行','won':'戦闘に勝利','lost':'戦闘に敗北',
                    'rethought':'証拠を再検討','unchanged':'再検討で変更なし','exposed':'告発が正しかった','misjudged':'告発が誤りだった'}
                body += '<p class="pw-action">この場面の行動：' + e(event['action']) + ' ／ ' + e(result_label.get(event['result'], '結果は原記録で確認')) + '</p>'
            if event['kind'] == 'replan' and event.get('before'):
                body += '<div class="pw-comparison">' + _tasks(event['before'], failure=event.get('cause'), title='見直し前')
                body += _tasks(plan, old=event['before'], title='組み直した計画') + '</div>'
            elif plan.get('steps'): body += _tasks(plan, failure=event.get('cause') or ({'action': event['action']} if event['kind'] == 'failure' else None))
            if plan.get('assumptions'):
                body += '<p class="pw-caption pw-caution">未確認の条件、相手の承諾や勝敗などを含む見込みです。結果は確定していません。</p>'
            actual = event['actual']
            body += '<p class="pw-at-point">この時点の目的の判定（世界の実態）：<strong>' + e(STATUS.get(actual.get('status'), '不明')) + '</strong></p>'
            if plan.get('steps'):
                body += '<details class="pw-route"><summary>移動を含む全手順を確認（' + e(len(plan['steps'])) + '段階）</summary><ol>'
                for step in plan['steps']:
                    body += '<li>' + e(step_label(step)) + ' ／ 所要見込み ' + e(step.get('duration', '?')) + ('分' if explanation.get('calendar') else '時点')+'</li>'
                body += '</ol></details>'
            body += '<p class="pw-caption">計画は行動を選んだ時点の見込みです。最後の判定と、この場面で本人が知っていた情報は異なる場合があります。</p>'
            body += _source(explanation, event) + '</article>'
        body += '</div></div></section>'
    return body + '</section>'
