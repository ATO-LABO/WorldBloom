"""Typed, non-destructive refinement compiler. No inference or filesystem writes."""
from copy import deepcopy
from execution.provenance import ConfigError, canonical, sha256

VERSION = 'refinement-v1'
OPERATIONS = {
    'person.update_profile': ('people', {'description', 'personality'}, 'description'),
    'place.update_description': ('places', {'description'}, 'description'),
    'place.add': ('places', {'name', 'description'}, 'runtime'),
    'route.add': ('routes', {'from', 'to', 'cost', 'requires_item'}, 'runtime'),
    'item.add': ('items', {'name', 'holder', 'holders', 'description', 'display_name', 'acquisition', 'effects'}, 'runtime'),
    'item.set_holders': ('items', {'holder', 'holders'}, 'runtime'),
}
PRESETS = {
    'enrich_people': ['person.update_profile'],
    'add_place': ['place.add', 'route.add'],
    'add_item': ['item.add', 'item.set_holders'],
}
UNSUPPORTED = ['person.add', 'route.set_requirement', 'schedule.update', 'schedule.add',
               'time.update', 'action.set_constraints', 'goal.change', 'entity.rename', 'entity.delete']

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
        elif targets and op['target'] not in targets:
            fail('operation.target', 'Target was not selected')
        if op['kind'] == 'item.set_holders' and len(op['values']) != 1:
            fail('operation.values', 'Use holder OR holders')
        by_id[oid] = op
    creators = {}
    for op in ops:
        if op['kind'] in ('place.add', 'item.add'):
            key = (OPERATIONS[op['kind']][0], op['values'].get('name'))
            if not isinstance(key[1], str): fail('operation.values.name', 'Name must be text')
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
        if op['kind'].endswith('.add'):
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
                key = (section, op['target'], field)
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
    if candidate == base: fail('operations', 'No actual change')
    return candidate, changes


def validate_candidate(store, record, candidate):
    from execution.world_drafts import validate
    base = record['draft']
    for key in ('name', 'overview', 'settings', 'protagonist', 'antagonist'):
        if candidate.get(key) != base.get(key): fail(key, 'Protected value changed')
    for person in base['people']:
        rows = [p for p in candidate['people'] if p['name'] == person['name']]
        if len(rows) != 1 or any(rows[0].get(k) != person.get(k) for k in ('goal', 'entry', 'commitments')):
            fail('goals', 'Existing goals, entry and commitments are protected')
    proposal, world, subjects, summary = validate(store.repo, candidate)
    if record.get('goal_plan'):
        from execution.draft_goal_plan import check_draft
        check_draft(store.repo, record['goal_plan'], proposal)
    return proposal, world, subjects, summary
