"""Bounded purpose-condition edits; initial preview is not an engine rollout."""
import ast
import re
import json
from copy import deepcopy
from engine.predicate import compile_predicate, PREDICATE_NAMES
from execution.provenance import ConfigError

VERSION = 2
MAX_ATOMS = 8
MAX_GROUP_DEPTH = 2
RESERVED = {'self', 'target', 'planter'}


def bad(field, message):
    raise ConfigError(field, message, code='bad_request')


def text(value, field, maximum=120):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > maximum or any(ord(c) < 32 for c in value)):
        bad(field, '空ではない登録名・識別子を指定してください')
    try: value.encode('utf-8')
    except UnicodeEncodeError: bad(field, '文字の形式を確認してください')
    return value


def catalog(world, people):
    actors = {}
    for person in people:
        if not isinstance(person, dict): bad('people', '人物の設定を確認してください')
        key = text(person.get('id'), 'people', 256)
        if key in actors: bad('people', '人物IDが重複しています')
        actors[key] = person
    def names(rows, field):
        if isinstance(rows, dict): return {text(k, field, 256) for k in rows}
        if not isinstance(rows, list): bad(field, '登録済みの一覧を確認してください')
        result = set()
        for row in rows:
            key = text(row.get('name') if isinstance(row, dict) else row, field, 256)
            if key in result: bad(field, '登録名が重複しています')
            result.add(key)
        return result
    return actors, names(world.get('zones', []), 'zones'), names(world.get('items', []), 'items')


def parse_when(source, world, people, *, existing=False):
    """Accept literals on writes; registered bare aliases only on legacy reads."""
    try: size = len(source.encode('utf-8')) if isinstance(source, str) else 0
    except UnicodeEncodeError: bad('when', '文字の形式を確認してください')
    if not isinstance(source, str) or not source.strip() or size > 8192:
        bad('when', '対応する達成条件を指定してください')
    catalog(world, people)
    from execution.ending_condition_catalog import parse
    expression, atoms = parse(source, world, people, bad, existing)
    try: compile_predicate(source, set(PREDICATE_NAMES) | {p['id'] for p in people} | {str(v) for v in catalog(world, people)[1] | catalog(world, people)[2]})
    except (ValueError, SyntaxError, RecursionError): bad('when', '条件式の書式を確認してください')
    return expression, atoms


def editable(ending, world, people):
    if (not isinstance(ending, dict) or not {'id', 'when'} <= set(ending)
            or not set(ending) <= {'id', 'label', 'when', '_purpose_builder'}
            or '_purpose_builder' in ending and (type(ending['_purpose_builder']) is not int or ending['_purpose_builder'] != 1)
            or 'label' in ending and not isinstance(ending['label'], str)):
        return False
    try: parse_when(ending['when'], world, people, existing=True)
    except ConfigError: return False
    return True


def describe(world, people):
    rows = []
    for ending in world.get('ending', []):
        if not isinstance(ending, dict): continue
        supported = editable(ending, world, people)
        row = {'id': ending.get('id'), 'editable': supported,
               'reason': None if supported else 'protected_existing_condition', 'deletable': managed(ending, world, people)}
        row['when'] = deepcopy(ending.get('when'))
        if supported:
            from execution.ending_condition_catalog import ui_tree
            row['tree'] = ui_tree(parse_when(ending['when'], world, people, existing=True)[0])
        rows.append(row)
    return {'version': VERSION, 'endings': rows}


def label_name(value):
    if not isinstance(value, str): bad('label', '達成条件の名前を入力してください')
    value = value.strip()
    if not value: bad('label', '達成条件の名前を入力してください')
    if len(value) > 120 or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value):
        bad('label', '名前は改行・制御文字を含めず120文字以内で入力してください')
    try: value.encode('utf-8')
    except UnicodeEncodeError: bad('label', '名前の文字の形式を確認してください')
    return value


def managed(ending, world, people):
    """New records carry an explicit marker; v1 reserved IDs remain compatible."""
    if not editable(ending, world, people) or not isinstance(ending.get('id'), str): return False
    return (ending.get('_purpose_builder') == 1
            or re.fullmatch(r'purpose_[0-9a-z]{8,12}_[0-9a-z]+', ending['id']) is not None)


def apply(world, people, values):
    """Validate every add/update/delete before returning a new world; no I/O."""
    if not isinstance(values, dict) or set(values) != {'changes', 'target_ending'}:
        bad('values', '変更する達成条件と目標の結末を指定してください')
    changes, targets = values['changes'], values['target_ending']
    if not isinstance(changes, list) or len(changes) > 100: bad('changes', '変更は100件以内で指定してください')
    if not isinstance(targets, list) or not 1 <= len(targets) <= 100:
        bad('target_ending', '削除後も最終目標が必要です。代わりの達成条件を選択してください')
    for value in targets: text(value, 'target_ending')
    if len(set(targets)) != len(targets): bad('target_ending', '同じ結末が重複しています')
    endings = world.get('ending', [])
    if not isinstance(endings, list): bad('ending', '結末の設定を確認してください')
    catalog(world, people)
    by_id = {}
    for ending in endings:
        if not isinstance(ending, dict): bad('ending', '結末の設定を確認してください')
        key = text(ending.get('id'), 'ending')
        if key in by_id: bad('ending', '結末IDが重複しています')
        by_id[key] = ending
    seen, validated, deleted = set(), [], set()
    for change in changes:
        if not isinstance(change, dict) or not {'kind', 'id'} <= set(change):
            bad('changes', '条件の操作と識別子を指定してください')
        key = text(change['id'], 'changes')
        if key in seen: bad('changes', '同じ結末への変更が重複しています')
        seen.add(key)
        kind = change['kind']
        if kind == 'delete':
            if set(change) != {'kind', 'id'} or key not in by_id or not managed(by_id[key], world, people):
                bad('changes', 'この画面で作成した単純な達成条件だけを削除できます')
            deleted.add(key)
        elif kind in {'add', 'update'}:
            if not set(change) <= {'kind', 'id', 'when', 'label'} or not {'when', 'label'} & set(change):
                bad('changes', '名前または条件式を指定してください')
            if kind == 'add':
                if key in by_id: bad('changes', 'この結末IDは既に登録されています')
                if 'when' not in change: bad('when', '新しい達成条件を入力してください')
            elif key not in by_id or not editable(by_id[key], world, people):
                bad('when', 'この既存条件は保護されています。設定ファイルで確認してください')
            if 'when' in change: parse_when(change['when'], world, people)
            if 'label' in change: label_name(change['label'])
        else: bad('changes', '条件の追加・更新・削除を指定してください')
        validated.append(change)
    known = (set(by_id) | {c['id'] for c in validated if c['kind'] == 'add'}) - deleted
    if not set(targets) <= known: bad('target_ending', '削除しない登録済みの達成条件を最終目標に選択してください')
    updated = deepcopy(world)
    result = updated['ending'] = [deepcopy(e) for e in endings if e['id'] not in deleted]
    index = {entry['id']: entry for entry in result}
    labels = {entry.get('label') for entry in result if isinstance(entry.get('label'), str)}
    number = 1
    for change in validated:
        if change['kind'] == 'delete': continue
        if change['kind'] == 'update':
            if 'when' in change: index[change['id']]['when'] = change['when']
            if 'label' in change: index[change['id']]['label'] = label_name(change['label'])
        else:
            while '達成条件' + str(number) in labels: number += 1
            label = label_name(change['label']) if 'label' in change else '達成条件' + str(number)
            labels.add(label)
            result.append({'id': change['id'], 'label': label, 'when': change['when'], '_purpose_builder': 1})
    updated['target_ending'] = deepcopy(targets)
    return updated


BACKUP_FOLDER = '.purpose-condition-backups'


def backup_deletion(base, ident, revision, before_bytes, after_text, changes):
    """Persist a preimage before every condition save; called under the world save lock."""
    deleted = sorted(c['id'] for c in changes if c['kind'] == 'delete')
    from execution.provenance import atomic_json, canonical, contained, sha256
    after_hash = sha256(after_text.encode('utf-8'))
    revisions = {'world.yaml': after_hash}
    for path in sorted((base/'subjects').glob('*.yaml')):
        revisions['subjects/'+path.name] = sha256(path.read_bytes())
    after_revision = sha256(canonical(revisions))
    token = revision[:16] + '-' + after_hash[:16]
    record = {'version': 1, 'world_id': ident, 'before_revision': revision, 'after_revision': after_revision,
              'before_world_sha256': sha256(before_bytes), 'after_world_sha256': after_hash,
              'world_text': before_bytes.decode('utf-8'), 'deleted_ids': deleted}
    path = contained(base, BACKUP_FOLDER+'/'+token+'.json')
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != record:
            bad('backup', '復元用ファイルが一致しないため削除を停止しました')
    else: atomic_json(path, record)
    return {'id': token, 'before_revision': revision, 'after_revision': after_revision}


def restore_deletion(store, ident, token, expected_revision):
    """Operator recovery via the existing revision-checked file save, never auto-run."""
    if not isinstance(token, str) or re.fullmatch(r'[0-9a-f]{16}-[0-9a-f]{16}', token) is None:
        bad('backup', '復元用ファイルの識別子を確認してください')
    from execution.provenance import contained, sha256
    from execution import world_editor
    path = contained(world_editor._base(store, ident), BACKUP_FOLDER+'/'+token+'.json')
    try: record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): bad('backup', '復元用ファイルを読み取れません')
    if (record.get('version') != 1 or record.get('world_id') != ident
            or not isinstance(record.get('world_text'), str)
            or sha256(record['world_text'].encode('utf-8')) != record.get('before_world_sha256')):
        bad('backup', '復元用ファイルの整合性を確認してください')
    if record.get('after_revision') != expected_revision:
        raise ConfigError('revision', '削除後に別の変更があります。自動復元せず差分を確認してください', code='conflict')
    return world_editor.save(store, ident, {'revision': expected_revision, 'operation': 'file',
            'target': 'world.yaml', 'values': {'content': record['world_text']}})


def preview(world, people):
    """Initial raw settings, before events; unknown is never made into success."""
    actors, zones, items = catalog(world, people)
    namespace = {key: key for key in set(actors) | zones | items}
    def zone(actor): return actors[actor].get('range', {}).get('entry')
    def holds(actor, item): return int(actors[actor].get('inventory', {}).get(item, 0)) > 0
    namespace.update(zone=zone, holds=holds)
    allowed = set(PREDICATE_NAMES) | set(namespace)
    atoms_out = []
    def combine(kind, states):
        decisive = 'false' if kind == 'all' else 'true'
        if decisive in states: return decisive
        if 'unknown' in states: return 'unknown'
        return 'true' if kind == 'all' else 'false'
    def evaluate(node, atoms):
        if node['kind'] in {'all', 'any'}:
            return combine(node['kind'], [evaluate(child, atoms) for child in node['children']])
        if node['kind'] not in {'zone', 'holds'}:
            atoms.append({'expression': node['expression'], 'state': 'unknown', 'reason': 'initial_condition_unsupported'})
            return 'unknown'
        person = actors[node['actor']]
        reason = None
        if node['kind'] == 'zone':
            location = person.get('range')
            if not isinstance(location, dict) or location.get('entry') not in zones: reason = 'initial_location_unknown'
        else:
            inventory = person.get('inventory', {})
            if not isinstance(inventory, dict): reason = 'initial_inventory_unknown'
            else:
                count = inventory.get(node['item'], 0)
                try:
                    if isinstance(count, bool) or int(count) < 0: reason = 'initial_inventory_unknown'
                except (ValueError, TypeError, OverflowError): reason = 'initial_inventory_unknown'
        try:
            state = 'unknown' if reason else ('true' if compile_predicate(node['expression'], allowed).evaluate(namespace) else 'false')
        except (KeyError, TypeError, ValueError):
            state, reason = 'unknown', 'initial_data_incompatible'
        atoms.append({'expression': node['expression'], 'state': state, **({'reason': reason} if reason else {})})
        return state
    outcomes = []
    selected = set(world['target_ending'])
    for ending in world['ending']:
        if ending['id'] not in selected: continue
        if not editable(ending, world, people):
            atoms_out.append({'ending_id': ending['id'], 'expression': None, 'state': 'unknown', 'reason': 'protected_existing_condition'})
            outcomes.append('unknown'); continue
        tree, _ = parse_when(ending['when'], world, people, existing=True)
        local = []; outcomes.append(evaluate(tree, local))
        atoms_out.extend(dict(atom, ending_id=ending['id']) for atom in local)
    return {'valid': True, 'state': combine('any', outcomes), 'atoms': atoms_out,
            'scope': 'initial_settings_before_events', 'ending_settings_version': VERSION}


def schema(world, people):
    from execution.ending_condition_catalog import kinds
    return {'version': 1, 'kinds': kinds(world, people), 'endings': describe(world, people)['endings']}
