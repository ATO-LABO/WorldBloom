"""Read-only purpose timelines: separate attempts, forecasts and world outcomes."""
from copy import deepcopy
from hashlib import sha256
from html import escape
from urllib.parse import quote

from viewer import ui


def e(value):
    return escape(str(value), quote=True)


STATUS = {'pending': '継続中', 'not_started': '判定前', 'achieved': '達成', 'failed': '失敗確定', 'unknown': '判定できない'}
KINDS = {'initial': '最初の計画', 'failure': '手段の失敗', 'replan': '計画を変更',
         'complete': '作業を完了', 'information': '情報を確認', 'observe': '観測へ移動',
         'offer': '交換を申し入れ', 'accepted': '交換が成立', 'refused': '交換を拒否された',
         'pause': '手順を探し直す', 'achieved': '目的を達成', 'failed': '目的の失敗確定', 'routine': '行動の記録'}


def step_key(step):
    return step.get('verb'), tuple(map(str, step.get('args', [])))


def step_label(step):
    args = step.get('args', [])
    if step.get('verb') == 'move': return (str(args[0]) if args else '目的地') + 'へ移動'
    if step.get('verb') == 'negotiate':
        return (str(args[0]) if args else '相手') + 'に' + str(step.get('meta', {}).get('objective', '品物')) + 'の交換を申し入れる'
    return str(step.get('label') or '記録された作業')


def model(explanation):
    groups = {}; pending_offers = {}
    for item in explanation.get('decisions', []):
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
            if previous and previous['result'] in {'moved', 'responded'} and expected: expected = expected[1:]
            elif previous and group.get('reply') == 'accepted' and expected and expected[0]['verb'] == 'negotiate': expected = expected[1:]
            changed = previous is not None and [step_key(s) for s in current_steps] != [step_key(s) for s in expected]
            result = item['outcome'].get('result')
            status = plan.get('status')
            response = detail.get('response') or {}
            kind = 'routine'
            if previous is None: kind = 'initial'
            if result in {'failed', 'invalid'}: kind = 'failure'
            elif item['verb'] == 'negotiate' and result == 'offered': kind = 'offer'
            elif status in {'no_known_plan', 'search_limit'} and (not previous or status != previous['plan'].get('status')): kind = 'pause'
            elif status == 'seeking_information' and (not previous or previous['plan'].get('observation_site') != plan.get('observation_site')): kind = 'observe'
            elif changed and current_steps and previous is not None: kind = 'replan'
            elif previous is not None and result == 'responded':
                kind = ('information' if any(v.get('kind') == 'knowledge' and v.get('actor') == item['subject']
                        and v.get('value') is True for v in response.get('effects', [])) else 'complete')
            action_label = next((step_label(s) for s in current_steps if step_key(s) == (item['verb'], tuple(map(str, item.get('args', []))))),
                                '状況を確かめる' if status == 'seeking_information' else '次の手順を検討する')
            event = {'turn': item['turn'], 'line': item['line'], 'source': item['source'], 'kind': kind,
                     'plan': deepcopy(plan), 'result': result, 'action': action_label,
                     'before': deepcopy(reference), 'cause': deepcopy(group['failure']) if kind in {'replan', 'pause', 'observe'} and
                         (plan.get('reason') in {'previous_action_failed', 'reply_received'} or previous and previous['kind'] == 'failure') else None,
                     'outcome': None, 'progress': response.get('progress'), 'total': response.get('steps')}
            group['records'].append(event)
            group['events'].append(event)
            if kind == 'failure': group['failure'] = {'turn': item['turn'], 'line': item['line'], 'action': action_label, 'kind': 'failure'}
            if kind == 'offer':
                pending_offers[(item['subject'], (item.get('args') or [''])[0], detail.get('objective'))] = group
                group['offer_plan'] = deepcopy(plan)
            if status != 'awaiting_consent': group.pop('reply', None)
            group['previous'] = event
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
    for item in explanation.get('decisions', []):
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
        if final.get('decided_at') is not None: body += '<span>時点 ' + e(final['decided_at']) + '</span>'
        if final.get('period'): body += '<span class="pw-caption">判定期間 ' + e(final['period'].get('start')) + '〜' + e(final['period'].get('end')) + '</span>'
        body += '</div></header><div class="pw-layout"><aside class="pw-history"><div class="pw-history-heading"><h3>変化のあった場面</h3>'
        body += f'<span class="pw-caption">{group["turning_count"]}件</span></div>'
        body += '<button type="button" class="ui-button" data-plan-all aria-pressed="false">全行動を表示</button>'
        body += '<div class="pw-events" aria-label="計画を追う場面">'
        for event in group['events']:
            idx = event['index']; active = idx == group['default']; key = f'{gi}-{idx}'
            body += f'<button type="button" class="pw-event ui-button" data-plan-event="{idx}" aria-controls="{prefix}-{key}" aria-pressed="{str(active).lower()}"'
            body += (' data-plan-routine hidden' if event['kind'] == 'routine' else '') + '>'
            body += '<span class="pw-caption">時点 ' + e(event['turn']) + '</span><strong>' + e(KINDS[event['kind']]) + '</strong></button>'
        body += '</div></aside><div class="pw-details">'
        for event in group['events']:
            idx = event['index']; key = f'{gi}-{idx}'; plan = event.get('plan') or {}
            body += f'<article id="{prefix}-{key}" data-plan-detail="{idx}"' + ('' if idx == group['default'] else ' hidden') + '>'
            body += '<header class="pw-scene"><p class="pw-caption">この場面の判断と結果</p><h3>時点 ' + e(event['turn']) + ' ／ ' + e(KINDS[event['kind']]) + '</h3></header>'
            if event.get('cause'):
                cause = event['cause']
                body += '<p class="ui-notice ui-notice--warning">見直しのきっかけ：時点 ' + e(cause['turn']) + 'で「' + e(cause['action'])
                body += ('」を断られた。' if cause.get('kind') == 'refused' else '」に失敗した。') + '</p>'
            elif event['kind'] == 'failure':
                body += '<p class="ui-notice ui-notice--warning">「' + e(event['action']) + '」を実行できなかった。最終目的の判定とは分けて確認します。</p>'
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
            if plan.get('status') == 'seeking_information': body += '<p>次の手順を決めるため、観測場所へ向かいます。</p>'
            if plan:
                result_label = {'moved': '移動完了', 'working': '作業中', 'responded': '作業完了',
                    'offered': '申し入れ済み', 'rested': '待機・休息', 'failed': '実行できなかった', 'invalid': '実行されなかった'}
                body += '<p class="pw-action">この場面の行動：' + e(event['action']) + ' ／ ' + e(result_label.get(event['result'], '結果は原記録で確認')) + '</p>'
            if event['kind'] == 'replan' and event.get('before'):
                body += '<div class="pw-comparison">' + _tasks(event['before'], failure=event.get('cause'), title='見直し前')
                body += _tasks(plan, old=event['before'], title='組み直した計画') + '</div>'
            elif plan.get('steps'): body += _tasks(plan, failure=event.get('cause') or ({'action': event['action']} if event['kind'] == 'failure' else None))
            if plan.get('assumptions'):
                body += '<p class="pw-caption pw-caution">未確認の条件や相手の承諾を含む見込みです。結果は確定していません。</p>'
            actual = event['actual']
            body += '<p class="pw-at-point">この時点の目的の判定（世界の実態）：<strong>' + e(STATUS.get(actual.get('status'), '不明')) + '</strong></p>'
            if plan.get('steps'):
                body += '<details class="pw-route"><summary>移動を含む全手順を確認（' + e(len(plan['steps'])) + '段階）</summary><ol>'
                for step in plan['steps']:
                    body += '<li>' + e(step_label(step)) + ' ／ 所要見込み ' + e(step.get('duration', '?')) + '時点</li>'
                body += '</ol></details>'
            body += '<p class="pw-caption">計画は行動を選んだ時点の見込みです。最後の判定と、この場面で本人が知っていた情報は異なる場合があります。</p>'
            body += _source(explanation, event) + '</article>'
        body += '</div></div></section>'
    return body + '</section>'
