"""Optional leaf permissions and named logical time bands; absent means legacy.

The world usage checkbox and domain preconditions remain independent. An explicit
actor list replaces the base verb grant for that leaf, without mutating a Subject.
"""
from copy import deepcopy


def rules(world):
    return (getattr(world, 'action_usage', None) or {}).get('constraints', {})


def parse(world, raw, refs):
    if not isinstance(raw, dict) or len(raw) > 4096:
        raise ValueError('Invalid action constraints')
    result = {}
    for ref, value in raw.items():
        if ref not in refs or ref == 'verb:rest':
            raise ValueError('Unknown or required action constraint: ' + str(ref))
        if not isinstance(value, dict) or set(value) != {'actors', 'slots'}:
            raise ValueError('Invalid action constraint fields: ' + ref)
        for key in ('actors', 'slots'):
            selected = value[key]
            if selected is not None and (not isinstance(selected, list) or len(selected) > 4096
                    or any(not isinstance(v, str) or not v for v in selected)
                    or len(set(selected)) != len(selected)):
                raise ValueError('Invalid action ' + key + ': ' + ref)
        slots = value['slots']
        if slots is not None:
            if not slots or set(slots) - set(world.slots):
                raise ValueError('時間帯の参照を確認してください: ' + ref)
        if value != {'actors': None, 'slots': None}:
            result[ref] = deepcopy(value)
    return result


def verb_for(ref):
    kind, key = ref.split(':', 1)
    return {'recipe': 'craft', 'response': 'respond'}.get(kind, key)


def actor_allowed(world, actor_id, ref):
    selected = rules(world).get(ref, {}).get('actors')
    if selected is not None:
        return actor_id in selected
    if isinstance(ref, str) and ref.startswith(('recipe:', 'response:')):
        prefix = ref.split(':',1)[0] + ':'
        if any(r.startswith(prefix) and v['actors'] is not None for r,v in rules(world).items()):
            actor = world.subjects.get(actor_id)
            return actor is not None and verb_for(ref) in (set(actor.verbs) | world.global_action_grants())
    return True


def response_actor_allowed(world, spec, actor_id):
    selected = rules(world).get('response:' + spec['id'], {}).get('actors')
    return actor_id in (spec['actors'] if selected is None else selected)


def effective_verbs(world, actor):
    """Resolve explicit leaf grants before the existing phase rules apply."""
    base = set(actor.verbs) | world.global_action_grants()
    selected = rules(world)
    if not selected:
        return base
    for ref, rule in selected.items():
        if rule['actors'] is not None and ref.startswith('verb:'):
            verb = verb_for(ref)
            if actor.id in rule['actors']: base.add(verb)
            else: base.discard(verb)
    for prefix, verb, leaves in [('recipe:', 'craft', world.recipes),
                                 ('response:', 'respond', world.responses.actions if world.responses else {})]:
        if not any(r.startswith(prefix) and v['actors'] is not None for r,v in selected.items()):
            continue
        permitted = False
        for key in leaves:
            actors = selected.get(prefix + key, {}).get('actors')
            if actors is None:
                permitted |= verb in base and (verb != 'respond' or actor.id in world.responses.actions[key]['actors'])
            else:
                permitted |= actor.id in actors
        if permitted: base.add(verb)
        else: base.discard(verb)
    return base


def time_allowed(world, ref, turn):
    selected = rules(world).get(ref, {}).get('slots')
    if selected is None:
        return True
    return type(turn) is int and turn >= 1 and bool(world.slots) and world.slots[(turn - 1) % len(world.slots)] in selected


def work_span(world, ref, start, units):
    """Wall-clock logical intervals needed for `units` permitted work intervals."""
    if type(start) is not int or start < 0 or type(units) is not int or units < 0:
        raise ValueError('Invalid logical work interval')
    if not units: return 0
    selected = rules(world).get(ref, {}).get('slots')
    if selected is None: return units
    offsets = [i for i in range(len(world.slots)) if world.slots[(start+i)%len(world.slots)] in selected]
    if not offsets: raise ValueError('No permitted work interval')
    cycles, index = divmod(units-1, len(offsets))
    return cycles*len(world.slots)+offsets[index]+1


def worked_units(world, ref, start, finish):
    """Count [start, finish), including jumps, without charging a boundary twice."""
    if finish < start: raise ValueError('Work cursor moved backwards')
    selected = rules(world).get(ref, {}).get('slots')
    if selected is None: return finish-start
    size = len(world.slots)
    def prefix(point):
        cycles, tail = divmod(point,size)
        return cycles*sum(s in selected for s in world.slots)+sum(s in selected for s in world.slots[:tail])
    return prefix(finish)-prefix(start)


def completing_work(world, actor, verb, args):
    timing = getattr(world, 'action_timing', None)
    pending = timing.completing if timing else None
    return bool(pending and 'work_duration' in pending and pending['actor']==actor.id
                and pending['action'].verb==verb and pending['action'].args==tuple(args)
                and pending['work_done']==pending['work_duration'] and pending['cursor']>=pending['finish'])


def execution_busy(world, actor, action):
    timing = getattr(world, 'action_timing', None)
    pending = timing.pending.get(actor.id) if timing else None
    return bool(pending and 'work_duration' in pending and not timing.prepaid(actor, action))


def denial(world, actor, verb, args, turn):
    from engine.action_usage import reference
    ref = reference(verb, args)
    if not actor_allowed(world, actor.id, ref):
        return 'action_actor_restricted'
    if not time_allowed(world, ref, turn) and not completing_work(world,actor,verb,args):
        return 'action_time_restricted'
    return None


def validate_bound(world):
    people = set(world.subjects)
    for ref, rule in rules(world).items():
        if rule['actors'] is not None and set(rule['actors']) - people:
            raise ValueError('行動の人物参照がありません: ' + ref)
    from engine.action_usage import reference
    for event in world.scheduled_events:
        action = event.get('force_action')
        if not action:
            continue
        ref = reference(action['verb'], action.get('args', []))
        selected = rules(world).get(ref, {})
        if selected.get('slots') is not None and event.get('slot') not in selected['slots']:
            raise ValueError('予定の時間帯では実行できません: ' + str(event.get('id')))
        if selected.get('actors') is not None and set(event.get('targets', [])) - set(selected['actors']):
            raise ValueError('予定の人物には行動を許可していません: ' + str(event.get('id')))
