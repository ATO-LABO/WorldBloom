"""One atomic authoritative state; JSONL and views are derived exports.

This is a different family from legacy personality/Choice GA checkpoints.
No checkpoint replaces or converts an existing v1 run.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from uuid import uuid4

from execution.provenance import canonical, directory_lock

FAMILY = "calendar_promises_v2"


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def source_hash():
    root = Path(__file__).resolve().parents[1]
    paths = list((root/'engine').glob('*.py'))+[root/'execution/provenance.py', root/'scripts/calendar_sim.py']
    files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    return digest(files)


def compatibility(inputs, choices):
    return {'family': FAMILY, 'schema_version': 3, 'source_hash': source_hash(),
            'inputs_hash': digest(inputs), 'choices_hash': digest(choices),
            'python': sys.version.split()[0], 'platform': platform.system()}


def atomic_bytes(path, data):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refuse symlink output')
    temporary = path.with_name('.'+path.name+'-'+uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _pairs(pairs):
    output = {}
    for k, v in pairs:
        if k in output:
            raise ValueError('Duplicate checkpoint key')
        output[k] = v
    return output


def strict_json(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON") from exc


def read(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > 32*1024*1024:
        raise ValueError('Checkpoint missing, oversized, or symlinked')
    try:
        value = strict_json(p.read_text(encoding='utf-8'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Corrupt checkpoint') from exc
    if not isinstance(value, dict) or set(value) != {'payload', 'sha256'}:
        raise ValueError('Wrong checkpoint envelope')
    if digest(value['payload']) != value['sha256']:
        raise ValueError('Checkpoint checksum mismatch')
    return value['payload']


class CheckpointStore:
    def __init__(self, out, expected):
        self.out = Path(out).resolve()
        self.expected = expected
        self._locked = False
        checkout = Path(__file__).resolve().parents[1]
        if self.out == checkout or checkout in self.out.parents:
            raise ValueError('Run output must be outside checkout')

    @contextmanager
    def session(self):
        """Hold one writer lease across load, all ticks, and derived exports."""
        with directory_lock(self.out):
            self._locked = True
            try:
                yield
            finally:
                self._locked = False

    def initialize(self, state):
        self.out.mkdir(parents=True, exist_ok=True)
        if not self._locked:
            with self.session():
                return self.initialize(state)
        if any(p.name != '.write.lock' for p in self.out.iterdir()):
            raise ValueError('Existing output is protected; explicit resume required')
        self.save(state, locked=True)

    def save(self, state, *, locked=False):
        if not self._locked:
            with self.session():
                return self.save(state, locked=True)
        payload = {'compatibility': self.expected, 'state': state}
        atomic_bytes(self.out/'calendar-state.json', canonical({'payload': payload, 'sha256': digest(payload)}))

    def load(self):
        if not self._locked:
            with self.session():
                return self.load()
        payload = read(self.out/'calendar-state.json')
        if set(payload) != {'compatibility', 'state'} or payload['compatibility'] != self.expected:
            raise ValueError('Source/world/calendar/actions/policy/choices/Python/OS mismatch')
        return payload['state']
