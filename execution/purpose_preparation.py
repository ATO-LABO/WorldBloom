"""Revision-bound preparation management sidecar; never writes world/subjects."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from engine import purpose_preparation as pure
from execution import world_editor, person_values
from execution.provenance import ConfigError, canonical, contained, directory_lock, sha256, atomic_json

VERSION = 1
FILE = 'purpose-preparation.json'
BACKUPS = '.purpose-preparation-backups'
MAX_BYTES = 1048576
MAX_CANDIDATES = 512
ID = re.compile(r'prep_[0-9a-f]{24}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')


def _bad(field, message, code='bad_request'):
    raise ConfigError(field, message, code=code)


def _shape(value, allowed, required, field):
    if not isinstance(value, dict) or not required <= set(value) or not set(value) <= allowed:
        _bad(field, '設定の項目と形式を確認してください')


def _ids(values, field):
    if (not isinstance(values, list) or len(values) > MAX_CANDIDATES
            or any(not isinstance(v, str) or not ID.fullmatch(v) for v in values)
            or len(values) != len(set(values))):
        _bad(field, '準備候補IDを重複しない一覧で指定してください')
    return values


def _json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def _record(value):
    fields = {'version', 'enabled', 'root_id', 'root_when_hash', 'dependency_hash', 'managed_ids', 'candidates', 'pressure'}
    _shape(value, fields, fields, 'storage')
    if type(value['version']) is not int or value['version'] != VERSION or type(value['enabled']) is not bool:
        _bad('storage', '保存された準備設定の版を確認してください', 'invalid_config')
    if (not isinstance(value['root_id'], str) or not value['root_id']
            or any(not isinstance(value[k], str) or not HASH.fullmatch(value[k]) for k in ['root_when_hash', 'dependency_hash'])):
        _bad('storage', '保存された最終条件の参照を確認してください', 'invalid_config')
    _ids(value['managed_ids'], 'storage')
    rows = value['candidates']
    if not isinstance(rows, list) or len(rows) > MAX_CANDIDATES:
        _bad('storage', '保存された準備候補の一覧を確認してください', 'invalid_config')
    seen = set()
    for row in rows:
        required = {'id', 'semantic_key', 'predicate', 'source', 'source_hash', 'supported'}
        if not isinstance(row, dict) or not required <= set(row):
            _bad('storage', '保存された準備候補の形式を確認してください', 'invalid_config')
        ident = row['id']
        if (not isinstance(ident, str) or not ID.fullmatch(ident) or ident in seen
                or row['semantic_key'] != {'predicate': row['predicate']}
                or ident != 'prep_' + pure.digest(row['semantic_key'])[:24]
                or row['source_hash'] != pure.digest(row['source'])
                or type(row['supported']) is not bool):
            _bad('storage', '保存された準備候補の整合性を確認してください', 'invalid_config')
        seen.add(ident)
    if not set(value['managed_ids']) <= seen:
        _bad('storage', '管理する準備候補の保存参照が欠けています', 'invalid_config')
    _shape(value['pressure'], {'enabled', 'entries'}, {'enabled', 'entries'}, 'storage')
    if type(value['pressure']['enabled']) is not bool or not isinstance(value['pressure']['entries'], list) or len(value['pressure']['entries']) > MAX_CANDIDATES:
        _bad('storage', '保存された期限設定の形式を確認してください', 'invalid_config')
    # Historical deadlines can become stale after world edits. Validate their
    # JSON structure now, and their current references only on explicit save.
    deadlines = set()
    for entry in value['pressure']['entries']:
        _shape(entry, {'candidate_id', 'day', 'slot', 'awareness', 'estimate'}, {'candidate_id', 'day', 'slot', 'awareness'}, 'storage')
        ident = entry['candidate_id']
        if not isinstance(ident, str) or ident not in seen or ident in deadlines: _bad('storage', '保存された期限の候補参照が欠けているか重複しています', 'invalid_config')
        deadlines.add(ident)
        if type(entry['day']) is not int or entry['day'] < 1 or not isinstance(entry['slot'], str) or not entry['slot']: _bad('storage', '保存された期限の時点を確認してください', 'invalid_config')
        awareness = entry['awareness']
        if not isinstance(awareness, dict) or awareness.get('kind') not in {'public', 'known_fact'}: _bad('storage', '保存された期限認識を確認してください', 'invalid_config')
        _shape(awareness, {'kind', 'fact_id'} if awareness['kind'] == 'known_fact' else {'kind'}, {'kind', 'fact_id'} if awareness['kind'] == 'known_fact' else {'kind'}, 'storage')
        if awareness['kind'] == 'known_fact' and not isinstance(awareness['fact_id'], str): _bad('storage', '保存された期限認識の情報参照を確認してください', 'invalid_config')
        if 'estimate' in entry:
            estimate = entry['estimate'];_shape(estimate, {'duration', 'fact_id'}, {'duration', 'fact_id'}, 'storage')
            if type(estimate['duration']) is not int or estimate['duration'] < 1 or not isinstance(estimate['fact_id'], str): _bad('storage', '保存された所要見積りを確認してください', 'invalid_config')
    return value


def _read_once(store, ident):
    base = world_editor._base(store, ident)
    if contained(base, person_values.JOURNAL).exists():
        _bad('revision', '人物設定の保存が中断されています。先に設定を復旧してください', 'conflict')
    files, world_revision = world_editor._read(store, ident)
    path = contained(base, FILE);exists = path.exists();raw = path.read_bytes() if exists else b''
    if len(raw) > MAX_BYTES: _bad('storage', '準備設定が保存上限を超えています', 'invalid_config')
    try: record = _record(_json(raw)) if exists else None
    except (ValueError, UnicodeError, RecursionError, TypeError) as error:
        if isinstance(error, ConfigError): raise
        raise ConfigError('storage', '保存された準備設定を読み取れません', code='invalid_config') from error
    revision = sha256(canonical({'world': world_revision, 'preparation': sha256(raw)}))
    return {'base': base, 'world': files['world.yaml'], 'people': [v for k, v in files.items() if k != 'world.yaml'],
            'world_revision': world_revision, 'record': record, 'raw': raw, 'exists': exists, 'revision': revision}


def _read_confirmed(store, ident):
    first = _read_once(store, ident);second = _read_once(store, ident)
    if first['revision'] != second['revision']:
        _bad('revision', '読み込み中に設定が更新されました。再読み込みしてください', 'conflict')
    return first


def _derived(state):
    world = state['world'];people = state['people'];tree = None
    ids = world.get('target_ending', [])
    if isinstance(ids, list) and len(ids) == 1:
        rows = [e for e in world.get('ending', []) if isinstance(e, dict) and e.get('id') == ids[0]]
        if len(rows) == 1 and isinstance(rows[0].get('when'), str):
            from execution import ending_settings
            try: tree = ending_settings.parse_when(rows[0]['when'], world, people, existing=True)[0]
            except ConfigError: pass  # Pure derive returns a protected/unsupported diagnosis.
    try: return pure.derive(world, people, tree)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, AttributeError) as error:
        raise ConfigError('world', '準備候補を読み込む前に、世界・人物の設定形式を確認してください', code='invalid_config') from error


def _settings(record):
    return {'managed_ids': deepcopy(record['managed_ids']) if record else [],
            'pressure': deepcopy(record['pressure']) if record else {'enabled': False, 'entries': []}}


def _response(state, derived, record=None):
    record = state['record'] if record is None else record
    plan = pure.compile_plan(state['world'], state['people'], record or {}, derived.get('tree'))
    rows = [];old_ids = {c['id'] for c in (state['record'] or {}).get('candidates', [])}
    for row in plan['candidates']:
        value = deepcopy(row);value['managed'] = row['id'] in plan['managed_ids'];value['active'] = value['managed'] and plan['enabled'] and row['status'] == 'current' and row['supported']
        if value['id'] not in old_ids and value['status'] == 'current': value['status'] = 'new'
        rows.append(value)
    return {'version': VERSION, 'revision': state['revision'], 'world_revision': state['world_revision'],
            'settings': _settings(record), 'root': deepcopy(derived['root']), 'candidates': rows,
            'diagnostics': deepcopy(derived['diagnostics']), 'available': derived['available'], 'supported': derived['supported'],
            'saved': state['exists'], 'enabled': plan['enabled'], 'needs_review': plan['needs_review'],
            'dependency_hash': derived['dependency_hash'],
            'diff': {'added': [r['id'] for r in rows if r['id'] not in old_ids and r['status'] != 'stale'],
                     'removed': [r['id'] for r in rows if r['status'] == 'stale'],
                     'changed': [r['id'] for r in rows if r['status'] == 'changed'],
                     'stale': [r['id'] for r in rows if r['status'] == 'stale']}}


def snapshot(store, ident):
    with directory_lock(world_editor._base(store, ident)):
        state = _read_confirmed(store, ident)
        return _response(state, _derived(state))


def _facts(world):
    raw = world.get('facts', [])
    rows = list(raw.values()) if isinstance(raw, dict) else raw
    if isinstance(raw, dict): return {k for k, row in raw.items() if isinstance(row, dict) and row.get('values') is None}
    return {f['id'] for f in rows if isinstance(f, dict) and isinstance(f.get('id'), str) and f.get('values') is None}


def _pressure(value, world, candidates, managed, previous):
    _shape(value, {'enabled', 'entries'}, {'enabled', 'entries'}, 'pressure')
    if type(value['enabled']) is not bool or not isinstance(value['entries'], list) or len(value['entries']) > MAX_CANDIDATES:
        _bad('pressure', '期限の有効設定と一覧を確認してください')
    time = world.get('time') or {};days = time.get('days');slots = time.get('slots')
    facts = _facts(world);old_entries = {e['candidate_id']: e for e in previous.get('entries', [])};result = [];seen = set();active = 0
    for entry in value['entries']:
        _shape(entry, {'candidate_id', 'day', 'slot', 'awareness', 'estimate'}, {'candidate_id', 'day', 'slot', 'awareness'}, 'pressure')
        key = entry['candidate_id']
        if not isinstance(key, str) or key in seen or key not in candidates: _bad('pressure', '期限の候補IDが重複しているか参照先がありません')
        seen.add(key);candidate = candidates[key]
        inactive = candidate.get('status') == 'stale' or key not in managed or not candidate.get('supported')
        if inactive:
            if old_entries.get(key) != entry: _bad('pressure', '未採用・消失・未対応の候補へ新しい期限を設定できません')
            result.append(deepcopy(entry));continue
        if type(days) is not int or days < 1 or not isinstance(slots, list) or any(not isinstance(s, str) for s in slots):
            _bad('pressure', 'この時間軸の期限設定は未対応です')
        if type(entry['day']) is not int or not 1 <= entry['day'] <= days or not isinstance(entry['slot'], str) or entry['slot'] not in slots:
            _bad('pressure', '期限は世界の期間内の日数と登録済み時間帯で指定してください')
        awareness = entry['awareness']
        if not isinstance(awareness, dict) or awareness.get('kind') not in {'public', 'known_fact'}: _bad('pressure', '期限の認識方法を指定してください')
        _shape(awareness, {'kind', 'fact_id'} if awareness['kind'] == 'known_fact' else {'kind'}, {'kind', 'fact_id'} if awareness['kind'] == 'known_fact' else {'kind'}, 'pressure')
        if awareness['kind'] == 'known_fact' and (not isinstance(awareness['fact_id'], str) or awareness['fact_id'] not in facts): _bad('pressure', '期限を知る根拠は登録済みboolean情報で指定してください')
        if 'estimate' in entry:
            estimate = entry['estimate'];_shape(estimate, {'duration', 'fact_id'}, {'duration', 'fact_id'}, 'pressure')
            if type(estimate['duration']) is not int or not 1 <= estimate['duration'] <= days * len(slots) or not isinstance(estimate['fact_id'], str) or estimate['fact_id'] not in facts:
                _bad('pressure', '所要見積りは期間内の正の時間帯数と登録済みboolean情報で指定してください')
        active += 1;result.append(deepcopy(entry))
    if value['enabled'] and not active: _bad('pressure', '期限補正を有効にするには、対応済みの採用候補の期限を1件以上設定してください')
    return {'enabled': value['enabled'], 'entries': result}


def _draft(state, derived, values):
    _shape(values, {'managed_ids', 'pressure'}, {'managed_ids', 'pressure'}, 'values');_ids(values['managed_ids'], 'managed_ids')
    if not derived['available']: _bad('root', '現在の最終条件は準備生成の対応範囲外です。元設定は保持しています', 'unsupported')
    old = state['record'] or {};old_selected = set(old.get('managed_ids', []));current = {c['id']: deepcopy(c) for c in derived['candidates']}
    for row in old.get('candidates', []):
        if row['id'] not in current:
            stale = deepcopy(row);stale['status'] = 'stale';current[row['id']] = stale
    if len(current) > MAX_CANDIDATES: _bad('storage', '保持する過去参照が上限に達しました。整理方法の確認が必要です')
    for key in values['managed_ids']:
        if key not in current or not current[key]['supported'] or current[key]['status'] == 'stale' and key not in old_selected:
            _bad('managed_ids', '対応済みの現在の候補だけを新しく採用できます')
    pressure = _pressure(values['pressure'], state['world'], current, set(values['managed_ids']), old.get('pressure', {'enabled': False, 'entries': []}))
    return {'version': VERSION, 'enabled': True, 'root_id': derived['root']['id'], 'root_when_hash': derived['root']['when_hash'],
            'dependency_hash': derived['dependency_hash'], 'managed_ids': deepcopy(values['managed_ids']),
            'candidates': list(current.values()), 'pressure': pressure}


def preview(store, ident, values, expected_revision=None):
    with directory_lock(world_editor._base(store, ident)):
        state = _read_confirmed(store, ident)
        if expected_revision is not None and expected_revision != state['revision']: _bad('revision', '設定が更新されています。再読み込みしてください', 'conflict')
        derived = _derived(state);draft = _draft(state, derived, values);out = _response(state, derived, draft)
        if _read_once(store, ident)['revision'] != state['revision']: _bad('revision', '確認中に設定が更新されました', 'conflict')
        out['preview'] = True;out['enabled'] = bool(state['record'] and state['record'].get('enabled') and not out['needs_review'])
        for row in out['candidates']: row['active'] = False
        return out


def _backup(state, after):
    before_hash = sha256(state['raw']);after_hash = sha256(after);token = sha256(canonical({'before': state['revision'], 'after': after_hash}))
    value = {'version': VERSION, 'before_exists': state['exists'], 'before_text': state['raw'].decode('utf-8'),
             'before_world_revision': state['world_revision'], 'before_revision': state['revision'],
             'before_sha256': before_hash, 'after_sha256': after_hash}
    path = contained(state['base'], BACKUPS + '/' + token + '.json')
    if path.exists():
        try: saved = _json(path.read_bytes())
        except (ValueError, UnicodeError): _bad('backup', '既存の復元用ファイルを確認してください', 'invalid_config')
        if saved != value: _bad('backup', '既存の復元用ファイルが一致しません。保存を停止しました', 'conflict')
    else: atomic_json(path, value)
    return {'id': token, 'before_revision': state['revision'], 'before_sha256': before_hash, 'after_sha256': after_hash}


def save(store, ident, body):
    _shape(body, {'revision', 'values'}, {'revision', 'values'}, 'request')
    if not isinstance(body['revision'], str) or not HASH.fullmatch(body['revision']): _bad('revision', '読み込んだ版を指定してください')
    with directory_lock(world_editor._base(store, ident)):
        state = _read_confirmed(store, ident)
        if body['revision'] != state['revision']: _bad('revision', '別の画面で設定が更新されています。再読み込みしてください', 'conflict')
        derived = _derived(state);record = _draft(state, derived, body['values']);after = canonical(record)
        if len(after) > MAX_BYTES: _bad('storage', '準備設定が保存上限を超えています')
        if _read_once(store, ident)['revision'] != state['revision']: _bad('revision', '保存前に設定が更新されました', 'conflict')
        try: backup = _backup(state, after)
        except OSError as error: raise ConfigError('backup', '復元用保存に失敗したため、準備設定は変更していません', code='backup_unavailable') from error
        if _read_once(store, ident)['revision'] != state['revision']: _bad('revision', '復元用保存中に設定が更新されました', 'conflict')
        error = None
        try: atomic_json(contained(state['base'], FILE), record)
        except OSError as caught: error = caught
        try: confirmed = _read_confirmed(store, ident)
        except (OSError, ConfigError) as caught: raise ConfigError('revision', '保存結果を確認できません。入力を保持して再読み込みしてください', code='save_outcome_unknown') from caught
        if confirmed['world_revision'] != state['world_revision'] or confirmed['raw'] != after:
            if error is not None and confirmed['revision'] == state['revision']:
                raise ConfigError('storage', '保存できませんでした。元の準備設定を保持しています', code='write_failed') from error
            _bad('revision', '保存結果を確認できません。入力を保持して再読み込みしてください', 'save_outcome_unknown')
        out = _response(confirmed, _derived(confirmed));out['backup'] = backup;return out


def restore(store, ident, token, expected_revision):
    """Explicit operator recovery; never called automatically on conflicts/errors."""
    if not isinstance(token, str) or not HASH.fullmatch(token): _bad('backup', '復元用IDを確認してください')
    with directory_lock(world_editor._base(store, ident)):
        state = _read_confirmed(store, ident)
        if state['revision'] != expected_revision: _bad('revision', '復元前に設定が更新されています', 'conflict')
        try: backup = _json(contained(state['base'], BACKUPS + '/' + token + '.json').read_bytes())
        except (OSError, ValueError, UnicodeError) as error: raise ConfigError('backup', '復元用ファイルを読み取れません') from error
        before = backup.get('before_text', '').encode('utf-8')
        if (backup.get('version') != VERSION or backup.get('before_sha256') != sha256(before)
                or backup.get('after_sha256') != sha256(state['raw'])
                or backup.get('before_world_revision') != state['world_revision']):
            _bad('backup', '復元用ファイルと現在の設定が一致しません', 'conflict')
        path = contained(state['base'], FILE)
        if backup.get('before_exists'):
            record = _record(_json(before))
            # Preserve the exact previous bytes, not a JSON reserialization.
            from execution.provenance import write_bytes
            import os, uuid
            temporary = contained(state['base'], '.' + FILE + '-' + uuid.uuid4().hex)
            try: write_bytes(temporary, before);os.replace(temporary, path)
            finally: temporary.unlink(missing_ok=True)
        else: path.unlink()
        confirmed = _read_confirmed(store, ident)
        return _response(confirmed, _derived(confirmed))
