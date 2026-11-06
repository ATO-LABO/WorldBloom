"""Resolve draft action defaults without inventing permissions or migrating worlds.

Only compiled current-format drafts are accepted. Runtime construction reads the
shared action graph; it never executes turns, writes files or calls a model.
"""
from copy import deepcopy
from pathlib import Path

import yaml

from execution.provenance import ConfigError


def _bad(message):
    raise ConfigError('action_usage', message, code='unsupported')


def runtime(draft):
    from execution.world_drafts import compile_draft
    from execution.draft_settings import validate_runtime
    from engine.world import World
    from engine.subject import Subject
    from engine.world_format import validate as validate_format
    _, definition, subjects = compile_draft(draft)
    validate_format(definition)
    root = Path(__file__).resolve().parents[1]
    try:
        world = World(definition, root / 'projects/preview/world.yaml',
                      action_graph_path=root / 'templates/basic/action_graph.yaml')
        world.resolve_truth(0)
        people = [yaml.safe_load(value) for value in subjects.values()]
        world.bind_subjects({p['id']: Subject.from_definition(p) for p in people})
        validate_runtime(world, world.subjects)
        return world
    except (ValueError, TypeError, KeyError, OSError) as error:
        if isinstance(error, ConfigError):
            raise
        _bad('Cannot verify the existing draft action policy: ' + type(error).__name__)


def permissions(world):
    """Every actor/catalog leaf/named slot at the permission layer.

Domain preconditions are unchanged by policy materialization; this snapshot
includes both verb grants and leaf/time gates, including recipes/responses.
"""
    from engine.action_usage import references, allows
    from engine.action_constraints import (effective_verbs, actor_allowed,
                                          response_actor_allowed, time_allowed,
                                          verb_for)
    refs = sorted(references(world))
    result = {}
    for actor_id, actor in world.subjects.items():
        verbs = effective_verbs(world, actor)
        for ref in refs:
            verb = verb_for(ref)
            args = [ref.split(':', 1)[1]] if ref.startswith(('recipe:', 'response:')) else []
            actor_gate = actor_allowed(world, actor_id, ref)
            if ref.startswith('response:'):
                actor_gate = actor_gate and response_actor_allowed(
                    world, world.responses.actions[args[0]], actor_id)
            permitted = allows(world, verb, args) and verb in verbs and actor_gate
            for turn, slot in enumerate(world.slots, 1):
                result[(actor_id, ref, slot)] = bool(permitted and time_allowed(world, ref, turn))
    return result


def materialize(draft):
    """Copy the actual compiled policy/mechanics, then prove exact equivalence."""
    from engine.action_usage import current_policy
    original = runtime(draft)
    policy, mechanics = current_policy(original)
    proposed = deepcopy(draft)
    settings = proposed.setdefault('settings', {'version': 1})
    settings['action_usage'] = deepcopy(policy)
    settings['mechanics'] = deepcopy(mechanics)
    resolved = runtime(proposed)
    if (resolved.action_usage != original.action_usage or resolved.mechanics != original.mechanics
            or permissions(resolved) != permissions(original)):
        _bad('Explicit policy materialization changed effective permissions')
    draft['settings'] = settings
    return deepcopy(policy)


def transition_baseline(base, candidate):
    """Permit only exact default materialization followed by a strict reduction."""
    old = base.get('settings', {'version': 1})
    new = candidate.get('settings', {'version': 1})
    if 'action_usage' in old or 'action_usage' not in new:
        return old
    baseline = deepcopy(base)
    materialize(baseline)
    expected = baseline['settings']
    policy = new['action_usage']
    if not isinstance(policy, dict):
        _bad('Materialized action policy must be an object')
    if ({k:v for k,v in policy.items() if k != 'constraints'} !=
            {k:v for k,v in expected['action_usage'].items() if k != 'constraints'}):
        _bad('Materialization must retain the exact existing enabled action catalog')
    if new.get('mechanics') != expected['mechanics']:
        _bad('Materialization must retain the exact existing mechanics')
    before, after = runtime(base), runtime(candidate)
    original, restricted = permissions(before), permissions(after)
    # Keep simultaneous catalog/actor/slot changes out of this bounded operation.
    if original.keys() != restricted.keys():
        _bad('Materialization cannot also change the actor, action or slot catalog')
    if any(value and not original[key] for key, value in restricted.items()):
        _bad('Materialization cannot expand any actor/action/slot permission')
    if restricted == original:
        _bad('Materialization requires an effective action restriction')
    return expected
