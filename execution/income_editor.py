"""Lossless edits to existing labor/reward identities within a money transaction."""
from engine.economy import assets
from engine.income import amount, trial_id


def apply(world, entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ValueError('入手方法は1〜1000件で指定してください')
    allowed = set(assets(world))
    zones = {z if isinstance(z, str) else z['name'] for z in world.get('zones', [])}
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('入手方法を確認してください')
        kind, asset = entry.get('kind'), entry.get('asset')
        if not isinstance(asset, str) or asset not in allowed:
            raise ValueError('登録済みの支払資産を指定してください')
        quantity = amount(entry.get('amount'))
        definition = next(i for i in world['items'] if i['name'] == asset)
        if kind == 'labor':
            if set(entry) != {'kind', 'asset', 'index', 'amount'}:
                raise ValueError('労働の入力項目を確認してください')
            index = entry['index']
            sources = definition.get('sources') or []
            if type(index) is not int or not 0 <= index < len(sources) or sources[index].get('type') != 'labor':
                raise ValueError('既存の労働経路を指定してください')
            key = (kind, asset, index)
            if key in seen:
                raise ValueError('入手方法が重複しています')
            sources[index]['amount'] = quantity
        elif kind == 'trial':
            if set(entry) != {'kind', 'asset', 'id', 'amount'} or not isinstance(entry['id'], str):
                raise ValueError('既存の報酬を指定してください')
            matches = [t for t in world.get('trials', []) if trial_id(t) == entry['id'] and t.get('grants', {}).get('item') == asset]
            if len(matches) != 1:
                raise ValueError('既存の資産報酬を一意に指定してください')
            key = (kind, asset, entry['id'])
            if key in seen:
                raise ValueError('入手方法が重複しています')
            matches[0]['item_amount'] = quantity
        elif kind == 'add-labor':
            if set(entry) != {'kind', 'asset', 'zone', 'amount', 'count', 'max'}:
                raise ValueError('追加する労働経路を確認してください')
            if not isinstance(entry['zone'], str) or entry['zone'] not in zones:
                raise ValueError('働く場所は登録済みの場所から選択してください')
            if type(entry['count']) is not int or not 1 <= entry['count'] <= 10000 or type(entry['max']) is not int or not 0 <= entry['max'] <= 10000:
                raise ValueError('行動回数は1〜10000、受取回数上限は0〜10000の整数で指定してください')
            key = (kind, asset, entry['zone'])
            if key in seen:
                raise ValueError('同じ資産・場所の追加経路が重複しています')
            if len(definition.get('sources') or []) >= 100:
                raise ValueError('入手経路は100件以内で指定してください')
            if definition.get('sources') is None:
                definition['sources'] = []
            definition['sources'].append({'type': 'labor', 'zone': entry['zone'], 'amount': quantity,
                                          'count': entry['count'], 'max': entry['max']})
        else:
            raise ValueError('実行可能な労働または既存報酬を指定してください')
        seen.add(key)
