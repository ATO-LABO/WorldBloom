"""Optional stamina budgets; time and physical injuries remain independent."""
from contextlib import contextmanager
from fractions import Fraction


NATIVE_ACTIONS = {'work','wait','offer','change','accept','accept_change','cancel','deliver','tell','depend','replan',
                  'practice','costume_prepare','sound_check','exhibit_help','dialogue',
                  'join_activity','decline_activity','leave_activity','await_activity','joint_move','joint_work'}


def action_verbs():
    from engine.verbs import HANDLED_VERBS
    return set(HANDLED_VERBS) | NATIVE_ACTIONS


def adjustment(world, verb):
    return float(world.stamina.get('action_cost_adjustments', {}).get(verb, 0))


def adjusted_cost(world, verb, base):
    if not enabled(world): return 0.0
    delta=adjustment(world,verb)
    return float(base) if not delta else round(max(0.0, float(base) + delta), 4)


def adjusted_rate(world, verb, rate, duration):
    if not enabled(world): return Fraction(0)
    if not adjustment(world,verb): return Fraction(str(rate))
    return Fraction(str(adjusted_cost(world,verb,float(rate)*duration)))/duration
def enabled(world):
    return world.mechanics_enabled('stamina_management')


def modern(world):
    return enabled(world)


def zero_behavior(world):
    return world.stamina.get('zero_behavior') if enabled(world) else None


def needs_rest(world, actor):
    return zero_behavior(world) == 'rest' and actor.stamina <= 0 and actor.vitality in {'alive','revived'}


def forced_rest(world, actor):
    if needs_rest(world, actor):
        from engine.actions import Action
        return Action('rest', meta={'stamina_forced_rest':True,'reason':'体力が0のため強制休息'})
    return None


def resolve_zero(world, actor):
    """Exhaustion is an independent opt-in cause, separate from combat lethality."""
    if zero_behavior(world) != 'death' or actor.stamina > 0 or actor.vitality == 'dead':
        return []
    actor.vitality='dead';actor.downed_since=None
    events=[{'verb':'dead','subject':actor.id,'details':{'cause':'stamina_zero','killer':None,'transferred':{}}}]
    for survivor in world.subjects.values():
        if survivor.id!=actor.id and survivor.vitality!='dead' and world.relations.stance(survivor.id,actor.id)>=world.companionship['threshold']:
            survivor.change_stress(world.grief['stress'])
            events.append({'verb':'grief','subject':survivor.id,'details':{'dead':actor.id,'cause':'stamina_zero','killer':None,'relation_delta':{}}})
    return events


FREE_OR_NATIVE = {'move', 'rest', 'rescue', 'respond'} | NATIVE_ACTIONS


def planned_cost(world, verb, cost):
    if not enabled(world): return 0.0
    if modern(world) and verb=='train': cost=max(1,cost)+world.stamina['default_action_cost']
    if modern(world) and not cost and verb not in FREE_OR_NATIVE:
        cost = world.stamina['default_action_cost']
    return adjusted_cost(world, verb, cost)


def refresh_fatigue(world, actor):
    if not enabled(world): actor.exhausted = False
    elif modern(world):
        actor.exhausted = actor.stamina < actor.stamina_max * world.stamina['exhausted_ratio']


def extra_cost(world, actor, action):
    if not modern(world) or action.verb in FREE_OR_NATIVE:
        return 0.0
    if world.action_timing is not None and world.action_timing.prepaid(actor, action):
        return 0.0
    if action.verb == 'labor' and labor_cost(world, actor):
        return 0.0
    return world.stamina['default_action_cost']


def labor_cost(world, actor):
    total = 0.0
    for name, definition in world.items.items():
        for index, source in enumerate(definition.get('sources', []) or []):
            if source.get('type') != 'labor' or source.get('zone') != actor.zone:
                continue
            key = f'item:{name}:{actor.zone}:{index}'
            if actor.gathered.get(key, 0) >= int(source.get('max', 1)):
                continue
            total += float(source.get('stamina', 0))
    return total


def required_cost(world, actor, action):
    if (action.verb=='rest' and action.meta.get('stamina_forced_rest')): return 0.0
    if getattr(actor, '_stamina_meter', {}).get('paid'):
        return 0.0
    if world.action_timing is not None and world.action_timing.prepaid(actor, action):
        return 0.0
    if adjustment(world, action.verb):
        return adjusted_cost(world, action.verb, base_cost(world, actor, action))
    if not modern(world):
        return 0.0
    if action.verb == 'labor':
        return labor_cost(world, actor) or extra_cost(world, actor, action)
    if action.verb == 'train':
        return 1.0+extra_cost(world,actor,action)
    return extra_cost(world, actor, action)


def base_cost(world, actor, action):
    verb=action.verb
    if verb=='move':
        path=world.reachable_paths(actor,actual=True).get(str(action.args[0])) if action.args else None
        return path.cost if path else 0.0
    if verb=='labor': return labor_cost(world,actor) or extra_cost(world,actor,action)
    if verb=='respond':
        spec=world.responses.actions.get(action.args[0],{}) if world.responses and action.args else {}
        return spec.get('cost',0)
    if verb=='train': return 1.0+extra_cost(world,actor,action)
    if verb=='rescue': return 0.0
    return extra_cost(world,actor,action)


def settle(actor):
    meter=getattr(actor,'_stamina_meter',None)
    if meter is not None and not meter['paid']:
        if meter['cost']: actor.change_stamina(-meter['cost'])
        else: meter['paid']=True


@contextmanager
def cost_scope(world, actor, action):
    previous=getattr(actor,'_stamina_meter',None)
    prepaid=world.action_timing is not None and world.action_timing.prepaid(actor,action)
    active=enabled(world) and bool(adjustment(world,action.verb)) and not prepaid and not (action.verb=='rest' and action.meta.get('stamina_forced_rest'))
    try:
        if active:
            actor._stamina_meter=dict(cost=adjusted_cost(world,action.verb,base_cost(world,actor,action)),paid=False,spent=0.0)
            if action.verb=='rest': settle(actor)
        yield
    finally:
        if previous is None:
            if hasattr(actor,'_stamina_meter'): del actor._stamina_meter
        else: actor._stamina_meter=previous
