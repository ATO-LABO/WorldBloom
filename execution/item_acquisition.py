"""Validate acquisition channels and update only the selected definition/rewards."""
from copy import deepcopy
import math
from execution.provenance import ConfigError


def bad(message):
    raise ConfigError('acquisition', message, code='bad_request')


def amounts(value, names, target):
    if not isinstance(value, dict) or not value:
        bad('材料・代金を指定してください')
    if any(k not in names or k == target or type(v) is not int or not 1 <= v <= 10000 for k, v in value.items()):
        bad('材料・代金は別の登録済み品物と1〜10000の数量で指定してください')
    return deepcopy(value)


def apply(world, definition, target, kind, value, people):
    zones = {z if isinstance(z, str) else z['name'] for z in world.get('zones', [])}
    items = {i if isinstance(i, str) else i['name'] for i in world.get('items', [])}
    facts = {f if isinstance(f, str) else f['id'] for f in world.get('facts', [])}
    allowed = {'sources', 'rewards'} | ({'shop_zone', 'price', 'craft'} if kind == 'items' else set())
    if not isinstance(value, dict) or 'sources' not in value or set(value) - allowed:
        bad('入手経路を確認してください')
    sources = value['sources']
    if not isinstance(sources, list) or len(sources) > 100:
        bad('入手経路は100件以内で指定してください')
    if kind == 'facts' and definition.get('values') and (sources or value.get('rewards')):
        bad('値の候補がある情報は、証言などの根拠となる情報から判断する形で設定してください')
    for s in sources:
        if not isinstance(s, dict) or set(s) - {'type', 'zone', 'agent', 'count', 'max', 'stamina'}:
            bad('調査・労働の経路を確認してください')
        if s.get('type') not in ('investigate', 'labor') or kind == 'facts' and s['type'] != 'investigate':
            bad('入手する行動を確認してください')
        if ('zone' in s) == ('agent' in s) or ('zone' in s and (not isinstance(s['zone'],str) or s['zone'] not in zones)) or ('agent' in s and (not isinstance(s['agent'],str) or s['agent'] not in people)):
            bad('登録済みの場所または人物を指定してください')
        if s['type'] == 'labor' and 'zone' not in s:
            bad('働く場所を指定してください')
        if type(s.get('count', 1)) is not int or not 1 <= s.get('count', 1) <= 10000:
            bad('行動回数は1〜10000の整数で指定してください')
        for key in ('max', 'stamina'):
            n = s.get(key, 0)
            if type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 10000 or key == 'max' and type(n) is not int:
                bad('入手上限・体力の値を確認してください')
    definition['sources'] = deepcopy(sources)
    if kind == 'items':
        if 'shop_zone' in value:
            shop = value['shop_zone']
            if shop is not None and (not isinstance(shop,str) or shop not in zones):
                bad('購入する場所を指定してください')
            if shop:
                definition['shop_zone'] = shop
                definition['price'] = amounts(value.get('price'), items, target)
            else:
                definition.pop('shop_zone', None)
                definition.pop('price', None)
        if 'craft' in value:
            craft = value['craft']
            if craft is not None and (not isinstance(craft, dict) or set(craft) != {'materials', 'knowledge', 'zone'}):
                bad('作成に必要な材料・知識・場所を確認してください')
            for key in ('made_from', 'craft_zone'):
                definition.pop(key, None)
            requirements = deepcopy(definition.get('requires') or {})
            requirements.pop('knowledge', None)
            if craft:
                definition['made_from'] = amounts(craft['materials'], items, target)
                if craft['knowledge'] is not None:
                    if not isinstance(craft['knowledge'],str) or craft['knowledge'] not in facts:
                        bad('作成に必要な知識を指定してください')
                    requirements['knowledge'] = craft['knowledge']
                if craft['zone'] is not None:
                    if not isinstance(craft['zone'],str) or craft['zone'] not in zones:
                        bad('作成する場所を指定してください')
                    definition['craft_zone'] = craft['zone']
            if requirements:
                definition['requires'] = requirements
            else:
                definition.pop('requires', None)
    if 'rewards' not in value:
        return
    rewards = value['rewards']
    if not isinstance(rewards, list) or len(rewards) > 100:
        bad('報酬の経路は100件以内で指定してください')
    grant_key = 'item' if kind == 'items' else 'fact'
    def trial_id(t):
        return str(t.get('id', str(t['giver']) + ':' + ','.join(f'{k}:{t["grants"][k]}' for k in sorted(t['grants']))))
    original = {trial_id(t): t for t in world.get('trials', [])}
    kept = []
    for trial in world.get('trials', []):
        row = deepcopy(trial)
        row['id'] = trial_id(trial)
        if row.get('grants', {}).get(grant_key) == target:
            row['grants'].pop(grant_key)
            if grant_key == 'item':
                row.pop('transfer_item', None)
        if row.get('grants'):
            kept.append(row)
    ids = set()
    for reward in rewards:
        if not isinstance(reward, dict) or set(reward) != {'id', 'giver', 'requires', 'mode'}:
            bad('報酬の人物・条件・受け渡し方を確認してください')
        ident, giver, req, mode = (reward[k] for k in ('id', 'giver', 'requires', 'mode'))
        if not isinstance(ident, str) or not ident.strip() or len(ident) > 200 or ident in ids or not isinstance(giver, str) or giver not in people or mode not in ('grant', 'transfer') or kind == 'facts' and mode != 'grant':
            bad('報酬の指定や重複を確認してください')
        ids.add(ident)
        if not isinstance(req, dict) or set(req) - {'stance', 'item'}:
            bad('報酬の条件を確認してください')
        if 'stance' in req and (type(req['stance']) not in (int, float) or not math.isfinite(req['stance']) or not -1 <= req['stance'] <= 1):
            bad('関係の条件は-1〜1で指定してください')
        if 'item' in req and (not isinstance(req['item'],str) or req['item'] not in items):
            bad('必要な所持品を指定してください')
        prior = original.get(ident)
        if prior and prior.get('grants', {}).get(grant_key) != target:
            bad('別の報酬と識別子が重複しています')
        shared = next((t for t in kept if str(t['id']) == ident), None)
        if shared and (giver != shared['giver'] or req != shared.get('requires', {})):
            bad('他の報酬と共通の条件は設定ファイルでまとめて変更してください')
        row = shared or deepcopy(prior) or {'id': ident}
        row.update(giver=giver, requires=deepcopy(req))
        row.setdefault('grants', {})[grant_key] = target
        if grant_key == 'item':
            row['transfer_item'] = mode == 'transfer'
        if not shared:
            kept.append(row)
    world['trials'] = kept


def effects(definition, value, target):
    if not isinstance(value, dict) or set(value) != {'strength', 'receiver_affinity', 'giver_affinity', 'shared_affinity', 'objective'}:
        bad('効果を確認してください')
    for key in ('strength', 'receiver_affinity', 'giver_affinity', 'shared_affinity'):
        n = value[key]
        if key == 'strength' and n is None:
            continue
        limit = 10000 if key == 'strength' else 1
        if type(n) not in (int, float) or not math.isfinite(n) or not -limit <= n <= limit:
            bad('効果の数値を確認してください')
    if type(value['objective']) is not bool:
        bad('目標の対象かを指定してください')
    if value['strength'] is None:
        # Do not silently remove lethal/visibility metadata on a legacy modifier.
        if definition.get('modifier'):
            definition['modifier']['value'] = 0
    else:
        modifier = definition.setdefault('modifier', {'id': 'item:' + target, 'kind': 'item', 'visible': True})
        modifier['value'] = value['strength']
    give = definition.setdefault('give', {})
    give.update({k: value[k] for k in ('receiver_affinity', 'giver_affinity', 'shared_affinity')})
    definition['objective'] = value['objective']
