"""Read-only person presentation. Mirrors initialization without changing state."""
import math


def goal_text(goal, *, delivery='届ける', acquisition='求める'):
    get = goal.get if isinstance(goal, dict) else lambda key, default=None: getattr(goal,key,default)
    target=get('target')
    if not target:return 'なし'
    destination=get('deliver_to')
    return f'{target}を{destination}へ{delivery}' if destination else f'{target}を{acquisition}'


def initial_stamina(world, person):
    """Subject.from_definition clamp followed by Simulation's default resolution.

    Modern explicit zero recovery stays zero; legacy nonpositive recovery inherits.
    Missing/nonpositive maximum resets both maximum and current to world maximum.
    This function never writes raw values or changes the runtime implementation.
    """
    raw=person.get('stamina') or {};rules=world.get('stamina') or {}
    maximum=float(raw.get('max',0));current=min(maximum,max(0,float(raw.get('current',maximum))))
    recovery=float(raw.get('recover_per_slot',0));inherit_max=maximum<=0
    inherit_recovery=recovery<=0 and ('recover_per_slot' not in raw)
    if inherit_max:maximum=float(rules.get('default_max',10));current=maximum
    if inherit_recovery:recovery=float(rules.get('default_recover_per_slot',1))
    if not all(math.isfinite(v) for v in [maximum,current,recovery]):raise ValueError('Invalid stamina display values')
    return dict(max=maximum,current=current,recover_per_slot=recovery,max_inherited=inherit_max,current_inherited=inherit_max or 'current' not in raw,recovery_inherited=inherit_recovery)


def stamina_map(world, people):
    return {p['id']:initial_stamina(world,p) for p in people if p.get('id')}
