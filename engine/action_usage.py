"""Explicit per-world action permission. Classification never grants ability.

Absent policy is deliberately a no-op for every legacy execution contract.
"""
from copy import deepcopy

VERSION = 2
CONTRACT = 'normal_turn_v2'
REQUIRED = frozenset({'verb:rest'})
FEATURES = frozenset({'stamina_management', 'budgeted_purchases'})


def reference(verb, args):
    if verb in {'craft', 'respond'}:
        if not args or not isinstance(args[0], str): return None
        return ('recipe:' if verb == 'craft' else 'response:') + args[0]
    return 'verb:' + str(verb)


def references(world):
    from engine.verbs import HANDLED_VERBS
    return ({'verb:' + v for v in HANDLED_VERBS - {'craft', 'respond'}}
            | {'recipe:' + str(v) for v in world.recipes}
            | {'response:' + str(v) for v in (world.responses.actions if world.responses else {})})


def supported(world):
    return world.slot_execution is None and world.joint_activities is None


def parse(world, raw, mechanics):
    if raw is None:
        if mechanics is not None: raise ValueError('mechanics requires action_usage')
        if not supported(world):return None, {"version":1,**{f:True for f in FEATURES}}
        raw={"version":VERSION,"engine_contract":CONTRACT,"catalog_version":1,"enabled":sorted(references(world)-{"verb:start_relationship"})}
        mechanics={"version":1,**{f:True for f in FEATURES}}
    if not supported(world): raise ValueError('Action usage editing does not support this execution contract')
    if not isinstance(raw, dict) or set(raw) not in ({'version','engine_contract','catalog_version','enabled'}, {'version','engine_contract','catalog_version','enabled','constraints'}):
        raise ValueError('Invalid action_usage fields')
    if type(raw['version']) is not int or raw['version'] != VERSION or raw['engine_contract'] != CONTRACT or type(raw['catalog_version']) is not int or raw['catalog_version'] != 1:
        raise ValueError('Unsupported action_usage version or contract')
    allowed = references(world)
    enabled = raw['enabled']
    if (not isinstance(enabled,list) or len(enabled)>4096 or any(not isinstance(r,str) or r not in allowed for r in enabled)
            or len(set(enabled)) != len(enabled)):
        raise ValueError('Unknown or duplicate action reference')
    if not REQUIRED <= set(enabled): raise ValueError('Required action rest cannot be disabled')
    from engine.verbs import HANDLED_VERBS
    if (not isinstance(mechanics,dict) or set(mechanics) != {'version',*FEATURES}
            or type(mechanics['version']) is not int or mechanics['version'] != 1
            or any(type(mechanics[f]) is not bool for f in FEATURES)):
        raise ValueError('Invalid mechanics settings')
    policy = deepcopy(raw); policy['enabled'] = sorted(enabled)
    from engine.action_constraints import parse as parse_constraints
    if "constraints" in raw: policy["constraints"] = parse_constraints(world, raw["constraints"], allowed)
    selected = set(enabled)
    for event in world.scheduled_events:
        forced = event.get('force_action')
        if forced and reference(forced['verb'], forced.get('args', [])) not in selected:
            raise ValueError('Scheduled action is disabled: ' + str(event.get('id', forced['verb'])))
    return policy, deepcopy(mechanics)


def allows(world, verb, args=None):
    policy = getattr(world, 'action_usage', None)
    if policy is None: return True
    selected = set(policy['enabled'])
    if args is None and verb in {'craft', 'respond'}:
        prefix = 'recipe:' if verb == 'craft' else 'response:'
        return any(r.startswith(prefix) for r in selected)
    return reference(verb, args) in selected


def denial(world, actor, action, turn, day):
    from engine.action_constraints import execution_busy
    if execution_busy(world,actor,action):return 'actor_busy'
    if not allows(world, action.verb, action.args): return 'action_disabled'
    if getattr(world,'action_usage',None) is None or action.verb=='rest': return None
    from engine.action_constraints import denial as constraint_denial
    denied = constraint_denial(world,actor,action.verb,action.args,turn)
    if denied: return denied
    from engine.phase2 import available_verbs
    if action.verb not in available_verbs(world,actor,turn=turn,day=day): return 'actor_action_unavailable'
    if not world.genre_allows(action.verb): return 'action_rule_unavailable'
    return None


def current_policy(world):
    """Return only the explicit resolved policy; never infer a genre."""
    if world.action_usage is None:raise ValueError("この実行方式の行動設定は未対応です")
    return deepcopy(world.action_usage),deepcopy(world.mechanics)
