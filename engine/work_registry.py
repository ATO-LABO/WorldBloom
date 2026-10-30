"""One writer for work progress, optionally bound to the calendar's own ledger."""
from copy import deepcopy
from fractions import Fraction


def rational(value):
    if isinstance(value, Fraction):
        result = value
    elif isinstance(value, dict):
        if set(value) != {'numerator', 'denominator'} or any(type(v) is not int for v in value.values()):
            raise ValueError('Invalid rational quantity')
        if value['denominator'] <= 0:
            raise ValueError('Invalid rational denominator')
        result = Fraction(value['numerator'], value['denominator'])
    elif isinstance(value, list) and len(value) == 2:
        result = rational(dict(zip(('numerator', 'denominator'), value)))
    elif type(value) in (int, float):
        result = Fraction(str(value))
    else:
        raise ValueError('Invalid rational quantity')
    if result < 0:
        raise ValueError('Negative quantity')
    return result


def pair(value):
    value = rational(value)
    return [value.numerator, value.denominator]


class WorkRegistry:
    def __init__(self, workflows):
        self.specs = {}
        for workflow in workflows:
            for task in workflow['tasks']:
                ident = task['id']
                if ident in self.specs:
                    raise ValueError('Duplicate work task')
                if (type(task['units']) is not int or task['units'] < 1 or
                        type(task['minutes_per_unit']) is not int or task['minutes_per_unit'] < 1):
                    raise ValueError('Invalid work quantity')
                self.specs[ident] = deepcopy(task)
        if len(self.specs) > 32:
            raise ValueError('Too many work tasks')
        def visit(ident, path):
            if ident not in self.specs or ident in path:
                raise ValueError('Unknown or cyclic work dependency')
            for dep in self.specs[ident]['dependencies']:
                visit(dep, path | {ident})
        for ident in self.specs:
            visit(ident, set())
        self.tasks = {ident: {'effort_minutes': 0, 'units': 0, 'contributions': {}, 'reserved_units': 0}
                      for ident in self.specs}
        self.receipts = {}
        self.scopes = {}
        self.cursors = {}
        self.completed = set()
        self.calendar = None

    def bind_store(self, calendar):
        for ident, spec in self.specs.items():
            native = calendar.contract['tasks'].get(ident)
            if (native is None or native['minutes_per_unit'] != spec['minutes_per_unit']
                    or native['units'] != spec['units'] or native['operation'] != 'work'):
                raise ValueError('Work registry and calendar task differ')
        self.calendar = calendar
        # Keep the calendar's dict as the only store, including after restore.
        self.tasks = calendar.ledger['tasks']

    def read(self, ident):
        return self.tasks[ident]

    def ready(self, ident):
        return all(self.tasks[d]['units'] >= self.specs[d]['units'] for d in self.specs[ident]['dependencies'])

    def remaining(self, ident):
        spec = self.specs[ident]
        return max(Fraction(0), spec['units']*spec['minutes_per_unit'] - rational(self.tasks[ident]['effort_minutes']))

    def validate_scope(self, ident, bindings):
        scope = [bindings.get('target'), bindings.get('target_revision')]
        if ident in self.scopes and self.scopes[ident] != scope:
            raise ValueError('Work target or input revision changed')
        if any(dep in self.scopes and self.scopes[dep]!=scope for dep in self.specs[ident]['dependencies']):
            raise ValueError('Dependency belongs to a different work target')
        return scope

    def commit_progress(self, ident, segment, at, amount, contributors, bindings):
        scope = self.validate_scope(ident, bindings)
        previous = self.cursors.get(segment, -1)
        if at < previous:
            raise ValueError('Work cursor moved backwards')
        if at == previous:
            return []
        amount = rational(amount)
        if not self.ready(ident):
            raise ValueError('Work dependency incomplete')
        self.scopes[ident] = scope
        task = self.tasks[ident]; spec = self.specs[ident]
        before = task['units']
        effort = min(spec['units']*spec['minutes_per_unit'], rational(task['effort_minutes']) + amount)
        # JSON-safe exact fractions; calendar's old writer is disabled for shared tasks.
        task['effort_minutes'] = int(effort) if effort.denominator == 1 else pair(effort)
        task['units'] = int(effort // spec['minutes_per_unit'])
        events = []
        for unit in range(before+1, task['units']+1):
            receipt = f'{segment}:unit:{unit}'
            if receipt in self.receipts:
                raise ValueError('Duplicate work receipt')
            event = {'id': receipt, 'task_id': ident, 'units': 1, 'at': at,
                     'actors': sorted(contributors), 'segment': segment}
            self.receipts[receipt] = event; events.append(event)
            for actor in contributors:
                task['contributions'][actor] = task['contributions'].get(actor, 0)+1
        self.cursors[segment] = at
        return events

    def commit_completion(self, ident):
        if self.remaining(ident) or ident in self.completed:
            return False
        self.completed.add(ident)
        return True

    def snapshot(self):
        return {'tasks': deepcopy({t: self.tasks[t] for t in self.specs}), 'scopes': deepcopy(self.scopes),
                'cursors': deepcopy(self.cursors), 'receipts': deepcopy(self.receipts), 'completed': sorted(self.completed)}

    def validate(self, state):
        if set(state) != {'tasks','scopes','cursors','receipts','completed'} or set(state['tasks']) != set(self.specs):
            raise ValueError('Invalid work checkpoint')
        for ident, task in state['tasks'].items():
            spec = self.specs[ident]; effort = rational(task['effort_minutes'])
            if (effort > spec['units']*spec['minutes_per_unit'] or type(task['units']) is not int
                    or task['units'] != effort // spec['minutes_per_unit']):
                raise ValueError('Inconsistent restored work progress')
        if not set(state['completed']) <= set(self.specs) or any(state['tasks'][t]['units'] != self.specs[t]['units'] for t in state['completed']):
            raise ValueError('Invalid completed work')

    def restore(self, state):
        self.validate(state)
        if self.calendar:
            for task, entry in state['tasks'].items():
                if self.calendar.ledger['tasks'][task] != entry:
                    raise ValueError('Duplicate work stores disagree')
            self.tasks = self.calendar.ledger['tasks']
        else:
            self.tasks = deepcopy(state['tasks'])
        self.scopes = deepcopy(state['scopes']); self.cursors = deepcopy(state['cursors'])
        self.receipts = deepcopy(state['receipts']); self.completed = set(state['completed'])
