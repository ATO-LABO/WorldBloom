"""Topic-scoped item/fact edits, applied to a complete world snapshot."""
from copy import deepcopy
import math
from execution.provenance import ConfigError


def bad(field, message):
    raise ConfigError(field, message, code='bad_request')


def holdings(value, people):
    if not isinstance(value, list) or len(value) > len(people):
        bad('holders', '所持者を人物の一覧から指定してください')
    seen = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {'person', 'count'}:
            bad('holders', '人物と数量を指定してください')
        person, count = row['person'], row['count']
        if not isinstance(person, str) or person not in people or person in seen:
            bad('holders', '所持者の指定・重複を確認してください')
        if type(count) is not int or not 1 <= count <= 10000:
            bad('holders', '数量は1〜10000の整数で指定してください')
        seen.add(person)
    return deepcopy(value)


def apply(files, operation, target, values):
    from execution.library import LibraryStore
    from execution import item_acquisition
    candidate = deepcopy(files)
    world = candidate['world.yaml']
    persons = [p for rel, p in candidate.items() if rel != 'world.yaml']
    if any(not isinstance(p.get('id'), str) or not p['id'] for p in persons) or len({p['id'] for p in persons}) != len(persons):
        bad('people', '人物の名前を一意に指定してください')
    people = {p['id']: p for p in persons}
    kind = 'items' if operation in ('item-topic', 'add-item') else 'facts'
    key = 'name' if kind == 'items' else 'id'
    if operation.startswith('add-'):
        target = LibraryStore._text(values['name'], 'name', maximum=120)
        if not target.strip():
            bad('name', '名前を指定してください')
        definitions = world.setdefault(kind, [])
        if any((r if isinstance(r, str) else r.get(key)) == target or isinstance(r, dict) and r.get('display_name') == target for r in definitions):
            bad('name', '同じ名前が登録されています。既存の品物・情報に所持者や経路を追加してください')
        definition = {key: target}
        definitions.append(definition)
        sequence = [('content', values['content']), ('holders' if kind == 'items' else 'knowledge', values['holders' if kind == 'items' else 'known']), ('sources', values['acquisition'])]
        if kind == 'items':
            sequence.append(('effects', values['effects']))
        for topic, value in sequence:
            candidate = apply(candidate, 'item-topic' if kind == 'items' else 'fact-topic', target, {'topic': topic, 'value': value})
        return candidate
    if not isinstance(target, str) or not target.strip():
        bad('target', '物・情報を指定してください')
    definitions = world.get(kind) or []
    matches = [r for r in definitions if (r if isinstance(r, str) else r.get(key)) == target]
    used = any(target in (p.get('inventory') or {}) for p in people.values()) if kind == 'items' else any(target in (p.get('knowledge') or []) for p in people.values())
    if len(matches) > 1 or (not matches and not used):
        bad('target', '登録された物・情報を一意に指定してください')
    topic, value = values['topic'], values['value']
    if topic == 'holders' and kind == 'items':
        owners = {r['person']: r['count'] for r in holdings(value, people)}
        for name, person in people.items():
            inventory = deepcopy(person.get('inventory') or {})
            if target not in inventory and name not in owners:
                continue
            inventory.pop(target, None)
            if name in owners:
                inventory[target] = owners[name]
            person['inventory'] = inventory
    elif topic == 'knowledge' and kind == 'facts':
        if not isinstance(value, list) or any(not isinstance(p, str) or p not in people for p in value) or len(set(value)) != len(value):
            bad('knowledge', '知っている人物の指定・重複を確認してください')
        for name, person in people.items():
            if target not in person.get('knowledge', []) and name not in value:
                continue
            known = [f for f in person.get('knowledge', []) if f != target]
            if name in value:
                known.append(target)
            person['knowledge'] = known
    elif topic in ('content', 'sources', 'identity', 'effects'):
        definition = matches[0] if matches else None
        if not isinstance(definition, dict):
            definition = {key: target}
            if matches:
                definitions[definitions.index(matches[0])] = definition
            else:
                definitions.append(definition)
            world[kind] = definitions
        if topic == 'content':
            definition['description' if kind == 'items' else 'label'] = LibraryStore._text(value, 'content', maximum=8000)
        elif topic == 'identity' and kind == 'items':
            label = LibraryStore._text(value, 'name', maximum=120)
            if not label.strip() or any(r is not definition and (r if isinstance(r,str) else r.get('display_name', r.get('name'))) == label for r in definitions):
                bad('name', '品物の名前の空欄・重複を確認してください')
            definition['display_name'] = label
        elif topic == 'effects' and kind == 'items':
            effect = deepcopy(value)
            if 'description' in effect:
                definition['description'] = LibraryStore._text(effect.pop('description'), 'content', maximum=8000)
            item_acquisition.effects(definition, effect, target)
        elif topic == 'sources':
            item_acquisition.apply(world, definition, target, kind, value, people)
        else:
            bad('topic', '編集するトピックを指定してください')
    else:
        bad('topic', '編集するトピックを指定してください')
    return candidate
