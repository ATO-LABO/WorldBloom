"""Lossless ownership migration and recoverable multi-file settings writes.

Reading never migrates a library. The journal contains exact bytes so an
interrupted prepared transaction can be rolled back by the next save.
"""
from base64 import b64decode, b64encode
from copy import deepcopy
import json
import os
import uuid

import yaml

from execution.library import _validate_rel
from execution.provenance import ConfigError, atomic_json, contained, sha256

JOURNAL = '.person-values-edit.json'


def canonicalize(files):
    result = deepcopy(files)
    concerns = result['world.yaml'].get('concerns')
    if concerns is None or concerns.get('version') != 1:
        return result
    from engine.concerns import Concerns
    Concerns(concerns)  # Do not discard malformed legacy fields.
    for actor, values in concerns.get('commitments', {}).items():
        people = [p for rel, p in result.items() if rel != 'world.yaml' and p.get('id') == actor]
        if len(people) != 1:
            raise ValueError('Unknown/ambiguous concern owner: ' + str(actor))
        person = people[0]
        if 'commitments' in person and person['commitments'] != values:
            raise ValueError('Conflicting world and person commitments: ' + actor)
        person['commitments'] = deepcopy(values)
    result['world.yaml']['concerns'] = {'version': 2, 'conditions': deepcopy(concerns['conditions'])}
    return result


def _replace(path, raw):
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def recover(base):
    """Call only under the library's directory lock, before checking revisions."""
    journal = contained(base, JOURNAL)
    if not journal.exists():
        return
    try:
        if journal.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Oversized journal')
        data = json.loads(journal.read_text(encoding='utf-8'))
        if set(data) != {'phase', 'files'} or data['phase'] not in ('prepared', 'committed'):
            raise ValueError('Invalid journal')
        if not isinstance(data['files'], dict) or not 1 <= len(data['files']) <= 41:
            raise ValueError('Invalid file count')
        decoded = []
        for rel, entry in data['files'].items():
            _validate_rel('world', rel)
            if set(entry) != {'before', 'after', 'before_sha256', 'after_sha256'}:
                raise ValueError('Invalid journal entry')
            before, after = (b64decode(entry[k], validate=True) for k in ('before', 'after'))
            if sha256(before) != entry['before_sha256'] or sha256(after) != entry['after_sha256']:
                raise ValueError('Invalid journal checksum')
            path = contained(base, rel)
            if sha256(path.read_bytes()) not in (entry['before_sha256'], entry['after_sha256']):
                raise ValueError('File was edited outside the interrupted save: ' + rel)
            decoded.append((path, after if data['phase'] == 'committed' else before))
    except (ValueError, TypeError, KeyError, OSError) as error:
        raise ConfigError('commitments', '中断された保存を復旧できません。保存時の記録と設定ファイルを確認してください。' + str(error), code='conflict') from error
    for path, raw in decoded:
        if path.read_bytes() != raw:
            _replace(path, raw)
    journal.unlink()


def write_changes(store, ident, before, after):
    """Validated dictionaries; preserve bytes of every semantically untouched file."""
    changes = {rel: value for rel, value in after.items() if value != before[rel]}
    if not changes:
        return
    if len(changes) == 1:
        rel, value = next(iter(changes.items()))
        store._write_file('world', ident, rel, yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
        return
    base = store._base('world', ident)
    entries = {}
    for rel, value in changes.items():
        _validate_rel('world', rel)
        old = contained(base, rel).read_bytes()
        new = yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode('utf-8')
        entries[rel] = {'before': b64encode(old).decode('ascii'), 'after': b64encode(new).decode('ascii'),
                        'before_sha256': sha256(old), 'after_sha256': sha256(new)}
    journal = contained(base, JOURNAL)
    atomic_json(journal, {'phase': 'prepared', 'files': entries})
    try:
        for rel, entry in entries.items():
            _replace(contained(base, rel), b64decode(entry['after']))
        atomic_json(journal, {'phase': 'committed', 'files': entries})
    except Exception:
        recover(base)
        raise
    journal.unlink()
