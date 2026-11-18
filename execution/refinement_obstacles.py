"""Conservative route gates for new drafts, never a full solvability proof.

A gate may only be added to an ungated existing directed route. Every actor
must possess its key initially or be able to investigate it at their entry in
one action. Acquisition witnesses use isolated compiled runtime objects, not
saved worlds. No inference, filesystem writes, or permission changes occur.
"""
from copy import deepcopy
import json
from pathlib import Path
import random

from execution.provenance import ConfigError


def fail(message):
    raise ConfigError('routes.requires_item', message, code='unsupported')


def route_identity(origin, destination):
    if any(not isinstance(v, str) or not v.strip() for v in (origin, destination)):
        fail('Route endpoints must be nonempty names')
    return json.dumps([origin, destination], ensure_ascii=False, separators=(',', ':'))


def _route_map(draft):
    result = {}
    for route in draft['routes']:
        identity = route_identity(route.get('from'), route.get('to'))
        if identity in result:
            fail('Ambiguous directed route')
        result[identity] = route
    return result


def apply_requirement(candidate, op, touched):
    """Apply one typed op; caller validates the complete transition afterward."""
    target, values = op['target'], op['values']
    if set(values) != {'requires_item'} or not isinstance(values['requires_item'], str) or not values['requires_item'].strip():
        fail('Specify one item name; removing an existing gate is unsupported')
    rows = _route_map(candidate)
    if target not in rows:
        fail('Target must be the canonical JSON [from,to] of one existing route')
    row = rows[target]
    prior = row.get('requires_item')
    if prior is not None and prior != values['requires_item']:
        fail('Replacing an existing route requirement is unsupported')
    key = ('routes', target, 'requires_item')
    if key in touched:
        fail('Overlapping route requirement writes')
    if values['requires_item'] not in {item['name'] for item in candidate['items']}:
        fail('Unknown route requirement item')
    touched.add(key)
    before = deepcopy(row)
    row['requires_item'] = values['requires_item']
    return before, deepcopy(row)


def _runtime(draft):
    import yaml
    from execution.world_drafts import compile_draft
    from engine.world import World
    from engine.subject import Subject
    _, definition, texts = compile_draft(draft)
    root = Path(__file__).resolve().parents[1]
    definition['gapengine']['effects'] = str(root / 'templates/basic/effects.yaml')
    world = World(definition, root / 'templates/basic/world.yaml',
                  action_graph_path=root / 'templates/basic/action_graph.yaml')
    actors = {}
    for text in texts.values():
        actor = Subject.from_definition(yaml.safe_load(text))
        actors[actor.id] = actor
    world.bind_subjects(actors)
    return world, actors


def _reachable(draft, entry, keys):
    reached = {entry}
    while True:
        added = {r['to'] for r in draft['routes'] if r['from'] in reached
                 and (not r.get('requires_item') or r['requires_item'] in keys)}
        if added <= reached:
            return reached
        reached |= added


def _initial_keys(draft, actor):
    return {item['name'] for item in draft['items']
            if any(h['person'] == actor and h['count'] > 0
                   for h in item.get('holders', [{'person': item.get('holder'), 'count': 1}]))}


def validate_transition(base, candidate):
    """Validate all added gates, including gated route.add and persisted input.

    Returns per-actor acquisition witnesses. Preserves prior graph reachability
    with initially held keys, not goal completion, deadline feasibility, future
    inventory retention, or automatic choice of the investigate action.
    """
    previous, current = _route_map(base), _route_map(candidate)
    added_keys = set()
    for identity, old in previous.items():
        new = current.get(identity)
        if new is None:
            fail('Existing directed routes cannot be removed or renamed')
        if {k:v for k,v in old.items() if k != 'requires_item'} != {k:v for k,v in new.items() if k != 'requires_item'}:
            fail('Existing route topology and cost are protected')
        if old.get('requires_item') is not None and new.get('requires_item') != old['requires_item']:
            fail('Existing route requirements cannot be removed or replaced')
    for identity, row in current.items():
        key = row.get('requires_item')
        if key is not None and key != previous.get(identity, {}).get('requires_item'):
            if not isinstance(key, str) or not key.strip():
                fail('A route requirement must reference an item name')
            added_keys.add(key)
    if not added_keys:
        return []
    world, actors = _runtime(candidate)
    if not added_keys <= set(world.items):
        fail('Unknown route requirement item')
    old_people = {p['name']:p for p in base['people']}
    if set(old_people) != set(actors):
        fail('Obstacle proposals cannot change the people catalog')
    witnesses = []
    for name, actor in actors.items():
        if actor.zone != old_people[name]['entry']:
            fail('Obstacle proposals cannot change initial positions')
        missing = {key for key in added_keys if not actor.has_item(key)}
        for key in added_keys - missing:
            witnesses.append({'actor': name, 'item': key, 'method': 'initial_possession', 'steps': 0})
        if missing:
            # Restrict this proof to a deterministic, no-cost normal opening.
            # More elaborate sources require a separate temporal planner.
            if set(candidate.get('settings', {})) - {'version','time','action_usage','mechanics'}:
                fail('Entry acquisition cannot be proved with scheduled or advanced settings')
            if world.mechanics_enabled('stamina_management'):
                fail('Entry acquisition with stamina management is not yet supported')
            if world.days * len(world.slots) < 2:
                fail('Entry acquisition needs at least one subsequent action interval')
            for key in missing:
                sources = world.items[key].get('sources', [])
                if not any(s.get('type') == 'investigate' and s.get('zone') == actor.zone
                           and s.get('count', 1) == 1 and s.get('max', 1) >= 1
                           for s in sources):
                    fail('Every actor needs initial possession or a one-step investigate source at their entry: ' + name + ' / ' + key)
            from engine.actions import Action
            from engine.verbs import VerbEngine
            from engine.action_usage import denial
            action = Action('investigate', (actor.zone,))
            if denial(world, actor, action, 1, 1):
                fail('Entry investigate is unavailable for actor: ' + name)
            outcome, _, _ = VerbEngine(world, random.Random(0)).execute(actor, action, turn=1, day=1, notify=False)
            if outcome != 'investigated' or any(not actor.has_item(key) for key in missing):
                fail('Runtime entry acquisition witness failed for actor: ' + name)
            for key in missing:
                witnesses.append({'actor': name, 'item': key, 'method': 'entry_investigate', 'steps': 1})
        old_reachable = _reachable(base, actor.zone, _initial_keys(base, name))
        new_reachable = _reachable(candidate, actor.zone, {k for k in world.items if actor.has_item(k)})
        if not old_reachable <= new_reachable:
            fail('Obstacle proposal loses previously reachable destinations for actor: ' + name)
    return sorted(witnesses, key=lambda row: (row['actor'], row['item']))
