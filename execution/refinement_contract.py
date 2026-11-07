"""Typed, non-destructive refinement compiler. No inference or filesystem writes."""
from copy import deepcopy
from execution.provenance import ConfigError, canonical, sha256

VERSION = 'refinement-v3'
EVENT_FIELDS = {'id', 'label', 'day', 'slot', 'targets', 'grants_item', 'grants_fact', 'move_to', 'stress_delta'}
OPERATIONS = {
    'time.update': ('settings', {'days', 'slots'}, 'runtime'),
    'schedule.add': ('settings', EVENT_FIELDS, 'runtime'),
    'schedule.update': ('settings', EVENT_FIELDS - {'id'}, 'runtime'),
    'action.set_constraints': ('settings', {'actors', 'slots'}, 'runtime'),
    'person.update_profile': ('people', {'description', 'personality'}, 'description'),
    'place.update_description': ('places', {'description'}, 'description'),
    'place.add': ('places', {'name', 'description'}, 'runtime'),
    'route.set_requirement': ('routes', {'requires_item'}, 'runtime'),
    'route.add': ('routes', {'from', 'to', 'cost', 'requires_item'}, 'runtime'),
    'item.add': ('items', {'name', 'holder', 'holders', 'description', 'display_name', 'acquisition', 'effects'}, 'runtime'),
    'item.set_holders': ('items', {'holder', 'holders'}, 'runtime'),
}
PRESETS = {
    'enrich_people': ['person.update_profile'],
    'add_place': ['place.add', 'route.add'],
    'obstacles': ['route.set_requirement', 'item.add'],
    'add_item': ['item.add', 'item.set_holders'],
    'schedule': ['time.update','schedule.add','schedule.update'],
    'actions': ['action.set_constraints'],
}
UNSUPPORTED = ['person.add', 'schedule.force_action', 'rule.change', 'goal.change', 'entity.rename', 'entity.delete']

def fail(field, message, code='invalid_config'):
    raise ConfigError(field, message, code=code)

def digest(value):
    return sha256(canonical(value))

def capabilities():
    return {'version': VERSION, 'workspace_kind': 'draft', 'operations': sorted(OPERATIONS),
            'presets': deepcopy(PRESETS), 'unsupported': UNSUPPORTED[:], 'max_operations': 16,
            'partial_apply': False, 'automatic_world_publish': False}

def compile_plan(base, plan, *, preset=None, targets=None):
    if not isinstance(plan, dict) or set(plan) != {'intent', 'operations', 'unsupported', 'clarification'}:
        fail('plan', 'Expected intent, operations, unsupported and clarification only')
    if not isinstance(plan['intent'], str) or not 1 <= len(plan['intent']) <= 2000:
        fail('intent', 'Intent must contain 1-2000 characters')
    if not isinstance(plan['unsupported'], list) or not isinstance(plan['clarification'], list):
        fail('plan', 'Unsupported and clarification must be lists')
    if plan['unsupported'] or plan['clarification']:
        fail('plan', 'Resolve unsupported requirements and ambiguity before adoption',
             'unsupported' if plan['unsupported'] else 'needs_clarification')
    ops = plan['operations']
    if not isinstance(ops, list) or not 1 <= len(ops) <= 16:
        fail('operations', 'Expected 1-16 operations')
    allowed = PRESETS.get(preset) if preset else OPERATIONS
    if allowed is None: fail('preset', 'Unknown preset', 'unsupported')
    by_id = {}
    for op in ops:
        if not isinstance(op, dict) or set(op) != {'id', 'kind', 'target', 'values', 'depends_on', 'reason'}:
            fail('operation', 'Unknown operation fields')
        oid = op['id']
        if not isinstance(oid, str) or not oid.isascii() or not oid.replace('_', '').isalnum() or len(oid) > 64 or oid in by_id:
            fail('operation.id', 'Invalid or duplicate operation id')
        if not isinstance(op['kind'], str) or op['kind'] not in allowed or op['kind'] not in OPERATIONS:
            fail('operation.kind', 'Operation not supported for this request', 'unsupported')
        section, fields, effect = OPERATIONS[op['kind']]
        if not isinstance(op['values'], dict) or not op['values'] or set(op['values']) - fields:
            fail('operation.values', 'Unknown or empty operation values')
        if not isinstance(op['reason'], str) or not 1 <= len(op['reason']) <= 2000:
            fail('operation.reason', 'Reason required (maximum 2000 characters)')
        if not isinstance(op['depends_on'], list) or any(not isinstance(x, str) for x in op['depends_on']) or len(set(op['depends_on'])) != len(op['depends_on']):
            fail('operation.depends_on', 'Invalid dependencies')
        if not isinstance(op['target'], str): fail('operation.target', 'Expected stable entity name')
        if op['kind'].endswith('.add'):
            if op['target']: fail('operation.target', 'Add uses values.name, not an existing target')
        elif op['kind'] == 'time.update':
            if op['target'] != 'time': fail('operation.target', 'Time updates target time')
        elif targets and op['target'] not in targets:
            fail('operation.target', 'Target was not selected')
        if op['kind'] == 'item.set_holders' and len(op['values']) != 1:
            fail('operation.values', 'Use holder OR holders')
        by_id[oid] = op
    creators = {}
    for op in ops:
        if op['kind'] in ('place.add', 'item.add'):
            key = (OPERATIONS[op['kind']][0], op['values'].get('name'))
            if not isinstance(key[1], str) or not key[1] or key[1] != key[1].strip(): fail('operation.values.name', 'Created names must be nonempty canonical text without outer whitespace')
            if key in creators: fail('operation.values.name', 'Duplicate created name')
            creators[key] = op['id']
    ordered, active, done = [], set(), set()
    def visit(oid):
        if oid not in by_id: fail('dependencies', 'Missing dependency')
        if oid in active: fail('dependencies', 'Dependency cycle')
        if oid in done: return
        active.add(oid)
        for dep in by_id[oid]['depends_on']: visit(dep)
        active.remove(oid); done.add(oid); ordered.append(by_id[oid])
    for oid in by_id: visit(oid)
    def ancestors(oid):
        result = set(by_id[oid]['depends_on'])
        for dep in by_id[oid]['depends_on']: result.update(ancestors(dep))
        return result
    for op in ordered:
        refs = []
        if op['kind'] == 'route.add':
            refs = [('places', op['values'].get('from')), ('places', op['values'].get('to')),
                    ('items', op['values'].get('requires_item'))]
        if op['kind'] == 'route.set_requirement':
            from execution.refinement_obstacles import route_identity
            if op['target'] not in {route_identity(r['from'], r['to']) for r in base['routes']}:
                fail('operation.target', 'Route requirement updates target a pre-existing directed route')
            refs = [('items', op['values'].get('requires_item'))]
        if op['kind'].startswith('schedule.'):
            grant = op['values'].get('grants_item')
            refs = [('places', op['values'].get('move_to')),
                    ('items', grant.get('name') if isinstance(grant, dict) else None)]
        for section, name in refs:
            if name is not None and not isinstance(name, str): fail('reference', 'Reference must be text')
            creator = creators.get((section, name))
            if creator and creator not in ancestors(op['id']):
                fail('dependencies', 'References to new entities require an explicit dependency')
    candidate = deepcopy(base)
    changes, touched, added_places = [], set(), set()
    for op in ordered:
        section, fields, effect = OPERATIONS[op['kind']]
        values = deepcopy(op['values'])
        if section == 'settings':
            before, after = _apply_settings(candidate, op, touched)
        elif op['kind'] == 'route.set_requirement':
            from execution.refinement_obstacles import apply_requirement
            before, after = apply_requirement(candidate, op, touched)
        elif op['kind'].endswith('.add'):
            if section != 'routes':
                name = values.get('name')
                if not isinstance(name, str) or not name.strip() or any(r['name'] == name.strip() for r in candidate[section]):
                    fail('operation.values.name', 'Missing or duplicate name')
                values['name'] = name.strip()
                if section == 'places': added_places.add(name.strip())
            elif any(r.get('from') == values.get('from') and r.get('to') == values.get('to') for r in candidate['routes']):
                fail('routes', 'Duplicate directed route')
            before = None
            candidate[section].append(values)
            after = values
        else:
            rows = [r for r in candidate[section] if r['name'] == op['target']]
            if len(rows) != 1: fail('operation.target', 'Unknown or ambiguous target')
            row = rows[0]; before = deepcopy(row)
            for field in values:
                key = (section, op['target'], 'inventory' if op['kind'] == 'item.set_holders' else field)
                if key in touched: fail('operations', 'Overlapping writes')
                touched.add(key)
            if op['kind'] == 'item.set_holders':
                row.pop('holder', None); row.pop('holders', None)
            row.update(values); after = deepcopy(row)
        changes.append({'operation_id': op['id'], 'kind': op['kind'], 'target': op['target'],
                        'before': before, 'after': deepcopy(after), 'effect_kind': effect, 'reason': op['reason']})
    # A new place must be linked to the pre-existing map, not just an isolated new island.
    reachable = {p['name'] for p in base['places']}
    while True:
        expanded = reachable | {r['to'] for r in candidate['routes'] if r['from'] in reachable}
        if expanded == reachable: break
        reachable = expanded
    if added_places - reachable: fail('routes', 'New places need an incoming route from the existing map')
    _settings_transition(base, candidate)
    from execution.refinement_obstacles import validate_transition
    evidence = validate_transition(base, candidate)
    for change in changes:
        if change['kind'] in ('route.set_requirement', 'route.add'):
            key = change['after'].get('requires_item')
            change['acquisition_evidence'] = [row for row in evidence if row['item'] == key]
    if candidate == base: fail('operations', 'No actual change')
    return candidate, changes



def _apply_settings(candidate, op, touched):
    settings = candidate.setdefault('settings', {'version': 1})
    kind, target, values = op['kind'], op['target'], deepcopy(op['values'])
    # One write per semantic setting/event, even across dependent operations.
    key = (kind.split('.')[0], values.get('id') if kind == 'schedule.add' else target)
    if not isinstance(key[1], str): fail('operation.target', 'Expected stable setting or event identifier')
    if key in touched: fail('operations', 'Overlapping settings writes')
    touched.add(key)
    if kind == 'time.update':
        before = deepcopy(settings.get('time'))
        if before is None:
            from execution.world_drafts import compile_draft
            _, world, _ = compile_draft(candidate)
            settings['time'] = deepcopy(world['time'])
        settings['time'].update(values)
        return before, deepcopy(settings['time'])
    if kind.startswith('schedule.'):
        events = settings.setdefault('scheduled_events', [])
        if not isinstance(events, list): fail('scheduled_events', 'Expected event list')
        if kind == 'schedule.add':
            if any(e.get('id') == values.get('id') for e in events):
                fail('scheduled_events', 'Duplicate event id')
            events.append(values)
            return None, deepcopy(values)
        rows = [e for e in events if e.get('id') == target]
        if len(rows) != 1: fail('operation.target', 'Unknown or ambiguous event id')
        if 'force_action' in rows[0]: fail('scheduled_events', 'Forced-action event edits are unsupported', 'unsupported')
        before = deepcopy(rows[0]); rows[0].update(values)
        return before, deepcopy(rows[0])
    policy = settings.get('action_usage')
    if policy is None and 'action_usage' not in settings:
        from execution.refinement_temporal_policy import materialize
        policy = materialize(candidate)
        settings = candidate['settings']
        policy = settings['action_usage']
    if not isinstance(policy, dict):
        fail('action_usage', 'Expected a valid existing action policy', 'unsupported')
    if not target.startswith('verb:') or target == 'verb:rest' or target not in policy.get('enabled', []):
        fail('action_usage', 'Only enabled non-rest verb constraints are supported', 'unsupported')
    constraints = policy.setdefault('constraints', {})
    before = deepcopy(constraints.get(target))
    rule = deepcopy(before or {'actors': None, 'slots': None}); rule.update(values)
    constraints[target] = rule
    return before, deepcopy(rule)


def _settings_transition(base, candidate):
    """Independent safety check, also applied to externally supplied candidates."""
    from execution.refinement_temporal_policy import transition_baseline
    old = transition_baseline(base, candidate)
    new = candidate.get('settings', {'version': 1})
    mutable = {'time', 'scheduled_events', 'action_usage'}
    if {k:v for k,v in old.items() if k not in mutable} != {k:v for k,v in new.items() if k not in mutable}:
        fail('settings', 'Unrelated settings are protected')
    if old == new: return
    from execution.draft_settings import normalize
    normalize(new)
    from execution.world_drafts import compile_draft
    _, compiled, subjects = compile_draft(candidate)
    days, slots = compiled['time']['days'], compiled['time']['slots']
    before_time, after_time = old.get('time', {}), new.get('time', {})
    if set(before_time) - set(after_time): fail('settings.time', 'Existing time fields cannot be removed')
    if {k:v for k,v in before_time.items() if k not in {'days','slots'}} != {k:v for k,v in after_time.items() if k not in {'days','slots'}}:
        fail('settings.time', 'Calendar and presentation settings are protected')
    people = {p['name'] for p in candidate['people']}
    import math
    events = new.get('scheduled_events', [])
    if any(not isinstance(e, dict) for e in events): fail('scheduled_events', 'Expected event objects')
    old_events = old.get('scheduled_events', [])
    previous = {e['id']: e for e in old_events}
    seen = set(); occupancy = set()
    for event in events:
        if not isinstance(event, dict): fail('scheduled_events', 'Expected event object')
        ident = event.get('id')
        if not isinstance(ident, str) or not ident.strip() or len(ident) > 120 or any(ord(c)<32 or ord(c)==127 for c in ident) or ident in seen:
            fail('scheduled_events.id', 'Invalid or duplicate event id')
        seen.add(ident)
        changed = event != previous.get(ident)
        if set(previous.get(ident, {})) - set(event): fail('scheduled_events', 'Existing event fields cannot be removed')
        if changed and (set(event) - EVENT_FIELDS or not {'id','day','targets'} <= set(event)):
            fail('scheduled_events', 'Only explicit normal-time event effects are supported', 'unsupported')
        if type(event.get('day')) is not int or not 1 <= event['day'] <= days:
            fail('scheduled_events.day', 'Event falls outside the time range')
        if 'slot' in event and event['slot'] not in slots: fail('scheduled_events.slot', 'Unknown time slot')
        targets = event.get('targets')
        if not isinstance(targets, list) or not targets or any(not isinstance(t,str) or t not in people for t in targets) or len(set(targets)) != len(targets):
            fail('scheduled_events.targets', 'Select unique existing people')
        if 'label' in event and (not isinstance(event['label'],str) or len(event['label']) > 120):
            fail('scheduled_events.label', 'Invalid event label')
        if 'stress_delta' in event and (type(event['stress_delta']) not in (int,float) or not math.isfinite(event['stress_delta']) or abs(event['stress_delta']) > 1000):
            fail('scheduled_events.stress_delta', 'Expected finite stress change within 1000')
        effects = set(event) & {'grants_item','grants_fact','move_to','stress_delta','force_action'}
        if changed and not effects: fail('scheduled_events', 'Event requires an executable effect')
        for actor in targets:
            for effect in effects:
                resource = 'movement_or_forced_action' if effect in ('move_to','force_action') else effect
                point = (event['day'], event.get('slot'), actor, resource)
                if point in occupancy:
                    # Legacy overlaps are preserved, but refinements cannot add or alter them.
                    if changed or any(e != previous.get(e.get('id')) and e.get('day') == event['day'] and e.get('slot') == event.get('slot') and actor in e.get('targets',[]) and (bool({'move_to','force_action'} & set(e)) if effect in ('move_to','force_action') else effect in e) for e in events):
                        fail('scheduled_events', 'Conflicting effects for the same actor and time')
                occupancy.add(point)
        for field, names in [('move_to', {p['name'] for p in candidate['places']}), ('grants_fact', {f['name'] for f in candidate.get('facts',[])})]:
            if field in event and (not isinstance(event[field], str) or event[field] not in names): fail('scheduled_events.'+field, 'Unknown reference')
        if 'grants_item' in event:
            grant = event['grants_item']
            if not isinstance(grant,dict) or not {'name'} <= set(grant) or set(grant)-{'name','count'} or not isinstance(grant['name'],str) or grant['name'] not in {i['name'] for i in candidate['items']} or type(grant.get('count',1)) is not int or not 1 <= grant.get('count',1) <= 999:
                fail('scheduled_events.grants_item', 'Invalid item grant')
    if set(previous) - seen: fail('scheduled_events', 'Existing events cannot be removed or renamed')
    old_policy, policy = old.get('action_usage'), new.get('action_usage')
    if old_policy != policy:
        if not isinstance(old_policy,dict) or not isinstance(policy,dict): fail('action_usage', 'Existing policy required', 'unsupported')
        if {k:v for k,v in old_policy.items() if k != 'constraints'} != {k:v for k,v in policy.items() if k != 'constraints'}:
            fail('action_usage', 'Enabled actions and policy version are protected')
    if policy:
        import yaml
        definitions = [yaml.safe_load(s) for s in subjects.values()]
        for ref, rule in policy.get('constraints', {}).items():
            prior = (old_policy or {}).get('constraints', {}).get(ref, {'actors':None,'slots':None})
            if rule != prior:
                if not ref.startswith('verb:') or ref == 'verb:rest' or ref not in policy['enabled']:
                    fail('action_usage.constraints', 'Only enabled non-rest verb constraints supported', 'unsupported')
                actors = prior['actors']
                if actors is None:
                    actors = [p['id'] for p in definitions if ref[5:] in p.get('verbs',[])]
                selected = rule['actors']
                if selected is None and prior['actors'] is not None or selected is not None and not set(selected) <= set(actors):
                    fail('action_usage.constraints', 'Actor permission expansion is unsupported', 'unsupported')
                if prior['slots'] is not None and (rule['slots'] is None or not set(rule['slots']) <= set(prior['slots'])):
                    fail('action_usage.constraints', 'Time permission expansion is unsupported', 'unsupported')
            if rule['slots'] is not None and not set(rule['slots']) <= set(slots): fail('action_usage.constraints', 'Unknown time slot')
            if rule['actors'] is not None and not set(rule['actors']) <= people: fail('action_usage.constraints', 'Unknown person')
        for ref, prior in (old_policy or {}).get('constraints', {}).items():
            if ref not in policy.get('constraints', {}) and prior != {'actors':None,'slots':None}:
                fail('action_usage.constraints', 'Removing constraints expands permissions', 'unsupported')


def validate_candidate(store, record, candidate):
    from execution.world_drafts import validate
    base = record['draft']
    for key in ('name', 'overview', 'protagonist', 'antagonist'):
        if candidate.get(key) != base.get(key): fail(key, 'Protected value changed')
    _settings_transition(base, candidate)
    for person in base['people']:
        rows = [p for p in candidate['people'] if p['name'] == person['name']]
        if len(rows) != 1 or any(rows[0].get(k) != person.get(k) for k in ('goal', 'entry', 'commitments')):
            fail('goals', 'Existing goals, entry and commitments are protected')
    from execution.refinement_obstacles import validate_transition
    validate_transition(base, candidate)
    proposal, world, subjects, summary = validate(store.repo, candidate)
    if record.get('goal_plan'):
        check_refined_goals(store.repo, record['goal_plan'], proposal)
    return proposal, world, subjects, summary


def check_refined_goals(repo, original_plan, proposal):
    """Preserve confirmed objectives/premises while allowing registered additions.

    Generation requires exact entity equality. Refinement may add places/items,
    so validate against a temporary extended entity catalog, never change the
    saved goal plan or any goal/premise/status in it.
    """
    from execution.draft_goal_plan import check_draft
    plan = deepcopy(original_plan)
    for key in ('places', 'items'):
        names = [row['name'] for row in proposal.get(key, [])]
        if not set(plan[key]) <= set(names): fail('plan.'+key, 'Confirmed entity removed')
        plan[key] = names
    return check_draft(repo, plan, proposal)
