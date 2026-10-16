"""Pure, opt-in DTOs/codecs for the reviewed nine-gene verification contract.

Not wired into a runtime or API. The producer must establish observation scope:
JSON shape validation cannot prove that evidence was actually observed.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import random


def _json(value):
    def check(v):
        if v is None or type(v) in (str, bool, int):
            return
        if type(v) is float and math.isfinite(v):
            return
        if type(v) is list:
            for x in v:
                check(x)
            return
        if type(v) is dict and all(type(k) is str for k in v):
            for x in v.values():
                check(x)
            return
        raise ValueError('Exact finite JSON values required')
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Invalid '+name)


def _text(value):
    if type(value) is not str or not value:
        raise ValueError('Nonempty identifier required')


@dataclass(frozen=True)
class DecisionOffer:
    """Immutable original DTO bytes; reads return detached JSON copies."""
    payload: bytes

    def __post_init__(self):
        if type(self.payload) is not bytes:
            raise ValueError('Canonical offer bytes required')
        try:
            v = json.loads(self.payload)
        except (ValueError, UnicodeError) as exc:
            raise ValueError('Invalid offer JSON') from exc
        if type(v) is not dict or _json(v) != self.payload or set(v) != {'schema', 'view', 'offered'}:
            raise ValueError('Noncanonical offer envelope')
        if v['schema'] != 'ninegene_offer_v1':
            raise ValueError('Unknown offer schema')
        view = v['view']
        fields = {'actor', 'now', 'zone', 'stamina', 'goals', 'initial', 'own',
                  'evidence', 'public_map', 'decision_ordinal', 'own_vitality'}
        if type(view) is not dict or set(view) != fields:
            raise ValueError('Unsupported ActorView fields')
        _text(view['actor']); _text(view['zone']); _text(view['own_vitality'])
        _integer(view['now'], 'minute'); _integer(view['decision_ordinal'], 'ordinal')
        if view['decision_ordinal'] >= 32:
            raise ValueError('Decision budget exhausted')
        if type(view['stamina']) not in (int, float):
            raise ValueError('Invalid own stamina')
        for key in ('goals', 'initial', 'own', 'public_map'):
            if type(view[key]) is not dict:
                raise ValueError('Invalid ActorView mapping')
        if any(type(x) not in (int, float) for x in view['goals'].values()):
            raise ValueError('Numeric goals required')
        if type(view['evidence']) is not list:
            raise ValueError('Invalid evidence list')
        for event in view['evidence']:
            if type(event) is not dict or 'at' not in event:
                raise ValueError('Timestamped evidence required')
            _integer(event['at'], 'evidence minute')
            if event['at'] > view['now']:
                raise ValueError('Future evidence forbidden')
        offered = v['offered']
        if type(offered) is not list or not offered:
            raise ValueError('Nonempty offered candidates required')
        seen = set()
        for c in offered:
            required = {'id', 'label', 'kind', 'minutes', 'features'}
            if (type(c) is not dict or not required <= set(c)
                    or set(c)-required-{'to', 'task', 'cost', 'recover'}):
                raise ValueError('Unsupported candidate fields')
            for key in ('id', 'label', 'kind'):
                _text(c[key])
            if c['kind'] not in {'move', 'work', 'wait'}:
                raise ValueError('Unsupported execution kind')
            if c['kind'] == 'move' and 'to' not in c or c['kind'] == 'work' and 'task' not in c:
                raise ValueError('Missing execution target')
            for field in ('to', 'task'):
                if field in c:
                    _text(c[field])
            for field in ('cost', 'recover'):
                if field in c and type(c[field]) not in (int, float):
                    raise ValueError('Numeric candidate cost/recovery required')
            if c['id'] in seen:
                raise ValueError('Duplicate original candidate ID')
            seen.add(c['id']); _integer(c['minutes'], 'duration', 1)
            if type(c['features']) is not dict or any(type(x) not in (int, float) for x in c['features'].values()):
                raise ValueError('Finite numeric features required')

    @classmethod
    def create(cls, view, offered):
        return cls(_json({'schema': 'ninegene_offer_v1', 'view': view, 'offered': offered}))

    @property
    def identity(self):
        return hashlib.sha256(self.payload).hexdigest()

    def read(self):
        return json.loads(self.payload)

    def selected(self, candidate_id, *, offer_hash, actor, now, ordinal):
        """Return the exact original candidate, never a reconstructed action."""
        v = self.read(); view = v['view']
        if (offer_hash != self.identity or type(now) is not int or type(ordinal) is not int
                or (actor, now, ordinal) != (view['actor'], view['now'], view['decision_ordinal'])):
            raise ValueError('Stale or wrong-owner selection')
        _text(candidate_id)
        for c in v['offered']:
            if c['id'] == candidate_id:
                return c
        raise ValueError('Selected original ID was not offered')


def _history_key(key):
    # Actual Policy.record stores (ContextKey, ActionKey), not ActionKey alone.
    if type(key) is not tuple or len(key) != 2:
        raise ValueError('Invalid policy history key')
    context, action = key
    if (type(context) is not tuple or len(context) != 6
            or type(context[0]) is not tuple
            or any(type(x) is not str for x in context[0])
            or tuple(sorted(context[0])) != context[0]
            or type(context[1]) is not bool or type(context[5]) is not bool
            or any(type(context[i]) is not str for i in (2, 3, 4))
            or type(action) is not tuple or len(action) != 3
            or any(type(x) is not str for x in action)):
        raise ValueError('Invalid policy context/action key')


def encode_history(history):
    """Lossless actual Policy Counter: (six-field context, three-field action)."""
    if type(history) is not Counter:
        raise ValueError('Counter required')
    rows = []
    for key, count in history.items():
        _history_key(key)
        context, action = key
        _integer(count, 'history count')
        rows.append({'key': [[list(context[0]), *context[1:]], list(action)], 'count': count})
    rows.sort(key=lambda row: _json(row['key']))
    return rows


def decode_history(rows):
    if type(rows) is not list:
        raise ValueError('History rows required')
    result = Counter()
    for row in rows:
        if (type(row) is not dict or set(row) != {'key', 'count'}
                or type(row['key']) is not list or len(row['key']) != 2):
            raise ValueError('Invalid history row')
        context, action = row['key']
        if (type(context) is not list or len(context) != 6 or type(context[0]) is not list
                or type(action) is not list):
            raise ValueError('Invalid encoded policy context/action')
        key = ((tuple(context[0]), *context[1:]), tuple(action))
        _history_key(key)
        if key in result:
            raise ValueError('Duplicate history key')
        _integer(row['count'], 'history count'); result[key] = row['count']
    if encode_history(result) != rows:
        raise ValueError('Noncanonical history rows')
    return result


def encode_rng(rng):
    if type(rng) is not random.Random:
        raise ValueError('Explicit standard RNG required')
    version, internal, gaussian = rng.getstate()
    return {'schema': 'python_random_v3', 'version': version,
            'internal': list(internal), 'gaussian': gaussian}


def decode_rng(value):
    if (type(value) is not dict or set(value) != {'schema', 'version', 'internal', 'gaussian'}
            or value['schema'] != 'python_random_v3' or type(value['version']) is not int or value['version'] != 3
            or type(value['internal']) is not list or len(value['internal']) != 625):
        raise ValueError('Invalid RNG schema/state')
    internal = value['internal']
    if any(type(x) is not int or not 0 <= x <= 0xffffffff for x in internal[:-1]) or type(internal[-1]) is not int or not 0 <= internal[-1] <= 624:
        raise ValueError('Invalid RNG word/index')
    gaussian = value['gaussian']
    if gaussian is not None and (type(gaussian) is not float or not math.isfinite(gaussian)):
        raise ValueError('Invalid RNG gaussian cache')
    rng = random.Random(0)
    rng.setstate((3, tuple(internal), gaussian))
    return rng
