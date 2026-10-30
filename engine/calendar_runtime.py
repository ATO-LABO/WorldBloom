"""Opt-in minute execution sharing the existing World/Subject/Relations.

The commands used for acceptance witnesses are explicit decisions, not searched
stories. Display boundaries never scale work or movement. Existing v1 execution
is dispatched independently and its inputs are never migrated.
"""
from __future__ import annotations

from engine.time_kernel import TimeAxis, TimeActionKernel, BoundaryStage, BoundaryResult
from engine.decision_provider import provider_snapshot, staged_provider_restore
from engine.execution_plan import run_spec_hashes
from engine.action_lifecycle import ActionLifecycle
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
import random
from pathlib import Path

from engine.calendar_checkpoint import CheckpointStore, atomic_bytes, compatibility, digest, strict_json
from engine.calendar_v2 import compile_calendar, minute_for, time_label
from engine.calendar_inputs import validate_execution_config, validate_fixed_version, safe_project_path, read_project_yaml
from engine.calendar_observers import validate_observers, evaluate_observer, validate_observation_state
from engine.log import LayersWriter
from engine.promises_v2 import PromiseRegistry
from engine.relations import Relations
from execution.provenance import canonical


def integer(value, name, minimum=0, maximum=1000000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum},{maximum}]')
    return value


def load_contract(world, config):
    try:
        return _load_contract(world, config)
    except (TypeError, KeyError, AttributeError, OverflowError) as error:
        raise ValueError('Malformed calendar execution contract') from error


def _load_contract(world, config):
    config = validate_execution_config(config)
    value = read_project_yaml(world.source, config['contract'])
    required = {'version', 'calendar', 'timezone', 'start_time', 'end_time', 'travel_minutes',
                'roles', 'tasks', 'materials', 'facility_hours', 'appraisal',
                'assumptions', 'initial_task_knowledge'}
    if not isinstance(value, dict) or not required <= set(value) or set(value)-required-{'observers', 'fact_model', 'energy'} or type(value['version']) is not int or value['version'] != 3:
        raise ValueError('Unsupported or incomplete calendar contract')
    for field in ('travel_minutes', 'roles', 'materials', 'facility_hours', 'appraisal', 'initial_task_knowledge'):
        if not isinstance(value[field], dict):
            raise ValueError('Contract '+field+' must be a mapping')
    if 'energy' in value:
        energy = value['energy']
        if (not isinstance(energy, dict) or set(energy) != {'move_per_minute','work_per_minute','rest_per_minute','rest_minutes'}
                or any(type(energy[k]) not in (int,float) or not math.isfinite(energy[k]) or not 0 <= energy[k] <= 1000
                       for k in ('move_per_minute','work_per_minute','rest_per_minute')) or energy['rest_per_minute'] <= 0):
            raise ValueError('Invalid calendar energy rates')
        integer(energy['rest_minutes'], 'rest minutes', 1, 1440)
    if not isinstance(value['assumptions'], list) or any(not isinstance(x, str) for x in value['assumptions']):
        raise ValueError('Assumptions must be text entries')
    for actor, roles in value['roles'].items():
        if not isinstance(actor, str) or not isinstance(roles, list) or not roles or any(not isinstance(x, str) or not x for x in roles) or len(roles) != len(set(roles)):
            raise ValueError('Actor roles must be distinct nonempty identifiers')
    for actor, ids in value['initial_task_knowledge'].items():
        if not isinstance(actor, str) or not isinstance(ids, list) or any(not isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
            raise ValueError('Initial task knowledge must be distinct identifiers')
    if set(value['appraisal']) != {'fulfilled', 'unexplained_breach'} or any(type(x) not in (int, float) or not math.isfinite(x) or not -1 <= x <= 1 for x in value['appraisal'].values()):
        raise ValueError('Invalid fixed appraisal coefficients')
    calendar = compile_calendar(value['calendar'], timezone=value['timezone'],
                                scheduled_events=list(world.scheduled_events))
    validate_fixed_version(world.source, config, calendar)
    if world.days != len(calendar['days']):
        raise ValueError('World days and compiled calendar differ')
    if world.daily_events or world.thresholds or world.effect_library or getattr(world, 'phase_rules', ()):
        raise ValueError('Calendar v2 does not mix legacy daily/threshold/effect rules')
    for source, routes in value['travel_minutes'].items():
        if source not in world.zones or not isinstance(routes, dict):
            raise ValueError('Unknown route origin')
        if set(routes) != {edge.destination for edge in world.routes.get(source, ())}:
            raise ValueError('Minute routes must match World adjacency')
        for dest, minutes in routes.items():
            integer(minutes, 'travel minutes', 1, 1440)
            if dest not in world.zones:
                raise ValueError('Unknown route destination')
    if set(value['travel_minutes']) != set(world.zones):
        raise ValueError('Every place needs an explicit minute adjacency')
    tasks = value['tasks']
    if not isinstance(tasks, dict) or not tasks or len(tasks) > 100:
        raise ValueError('Tasks must be a bounded nonempty mapping')
    for task, spec in tasks.items():
        if not isinstance(task, str) or not task or not isinstance(spec, dict) or set(spec) != {'label', 'place', 'units', 'minutes_per_unit', 'dependencies', 'resource', 'roles', 'materials_per_unit', 'operation'}:
            raise ValueError('Unsupported task definition')
        if (not isinstance(spec['roles'], list) or not spec['roles'] or any(not isinstance(x, str) or not x for x in spec['roles'])
                or not isinstance(spec['dependencies'], list) or any(not isinstance(x, str) for x in spec['dependencies'])
                or len(spec['dependencies']) != len(set(spec['dependencies'])) or not isinstance(spec['materials_per_unit'], dict)):
            raise ValueError('Invalid task role/dependency/material containers')
        if not set(spec['roles']) <= {role for roles in value['roles'].values() for role in roles}:
            raise ValueError('Unknown task role')
        integer(spec['units'], 'task quantity', 1, 10000)
        integer(spec['minutes_per_unit'], 'task unit duration', 1, 1440)
        if spec['operation'] not in ('work', 'deliver'):
            raise ValueError('Unsupported task operation')
        if spec['operation'] == 'deliver' and spec['materials_per_unit']:
            raise ValueError('Material consumption during handoff is unsupported; model production as work')
        if spec['place'] not in world.zones or not spec['roles'] or not isinstance(spec['resource'], str) or not spec['resource']:
            raise ValueError('Task place/role/resource missing')
        if any(dep not in tasks or dep == task for dep in spec['dependencies']):
            raise ValueError('Unknown/cyclic task dependency')
        for item, count in spec['materials_per_unit'].items():
            if item not in value['materials']:
                raise ValueError('Unknown task material')
            integer(count, 'material quantity', 1, 10000)
    def visit(task, chain):
        if task in chain:
            raise ValueError('Cyclic work dependency')
        for dep in tasks[task]['dependencies']:
            visit(dep, chain | {task})
    for task in tasks:
        visit(task, set())
    for qty in value['materials'].values():
        integer(qty, 'stock quantity', 0, 100000)
    for place, hours in value['facility_hours'].items():
        if place not in world.zones or not isinstance(hours, dict) or set(hours) != {'opens', 'closes'}:
            raise ValueError('Invalid facility schedule')
        if not isinstance(hours['opens'], str) or not isinstance(hours['closes'], str) or hours['opens'] >= hours['closes']:
            raise ValueError('Facility must open and close within a day')
        minute_for(calendar, calendar['start_date'], hours['opens'])
        minute_for(calendar, calendar['start_date'], hours['closes'])
    validate_observers(value.get('observers', []), calendar, set(value['roles']), set(world.zones), set(tasks))
    if 'fact_model' in value:
        from engine.fact_runtime import validate_model
        validate_model(value['fact_model'], set(value['roles']),
                       minute_for(calendar, calendar['start_date'], value['start_time']),
                       minute_for(calendar, calendar['end_date'], value['end_time']))
    return value


class CalendarRuntime:
    def __init__(self, simulation):
        self.facts = None
        self.sim = simulation
        self.world = simulation.world
        self.people = simulation.subjects
        self.contract = deepcopy(self.world.slot_contract)
        if self.contract != load_contract(self.world, self.world.slot_execution):
            raise ValueError('Runtime contract differs from the declared project input')
        provenance = getattr(self.world, 'calendar_input_provenance', None)
        if provenance is not None:
            for name, expected in provenance['inputs'].items():
                data = safe_project_path(self.world.source, name).read_bytes()
                if expected != {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}:
                    raise ValueError('Project input changed after loading')
        self.calendar = compile_calendar(self.contract['calendar'], timezone=self.contract['timezone'],
                                         scheduled_events=list(self.world.scheduled_events))
        if set(self.people) != set(self.contract['roles']):
            raise ValueError('Every actor needs a fixed reviewed role')
        if any(actor not in self.people for actor in self.contract['initial_task_knowledge']):
            raise ValueError('Unknown knowledge actor')
        for who, ids in self.contract['initial_task_knowledge'].items():
            if any(t not in self.contract['tasks'] for t in ids):
                raise ValueError('Unknown initial work knowledge')
        for who in self.people:
            if not isinstance(self.contract['roles'][who], list) or not self.contract['roles'][who]:
                raise ValueError('Actor roles must be explicit')
        start = minute_for(self.calendar, self.calendar['start_date'], self.contract['start_time'])
        self.end = minute_for(self.calendar, self.calendar['end_date'], self.contract['end_time'])
        self.time_kernel = TimeActionKernel(TimeAxis('minute', start, self.end))
        self.sim.time_kernel = self.time_kernel
        if self.end <= self.now:
            raise ValueError('Invalid observation period')
        self.initial = self._people()
        self.initial_relations = self.world.relations.snapshot()
        self.energy = {p: [Fraction(str(s.stamina)).numerator, Fraction(str(s.stamina)).denominator]
                       for p, s in self.people.items()}
        self.ledger = {'tasks': {t: {'effort_minutes': 0, 'units': 0, 'contributions': {},
                                   'reserved_units': 0} for t in self.contract['tasks']},
                       'materials': deepcopy(self.contract['materials']), 'completion_events': []}
        self.registry = PromiseRegistry(set(self.people), set(self.contract['tasks']), set(self.world.zones))
        self.busy = {}
        self.lifecycle = ActionLifecycle(self.time_kernel, lambda: self.busy)
        self.processed = set()
        self.observed = {p: set() for p in self.people}
        self.known_terms = {p: {} for p in self.people}
        self.known_tasks = {p: {t: deepcopy(self.ledger['tasks'][t])
                              for t in self.contract['initial_task_knowledge'].get(p, [])} for p in self.people}
        self.knowledge = {p: [] for p in self.people}
        self.pending_read = []
        self.dependencies = {}
        self.appraisals = {}
        self.rows = []
        self.commands = []
        self.observations = {}
        self.done = False
        self.store = None
        self.facilities = {}
        self._observation_buffer = None
        self._active_at_tick = set()
        self._facility_events = []
        for day in self.calendar['days']:
            for place, hours in sorted(self.contract['facility_hours'].items()):
                for field, is_open in (('opens', True), ('closes', False)):
                    at = minute_for(self.calendar, day['date'], hours[field])
                    self._facility_events.append({'id': f"facility:{place}:{day['date']}:{field}",
                                                  'at': at, 'place': place, 'open': is_open})
                self.facilities[place] = False
        # Start at an explicit real time. Past opening notices affect access,
        # not positions or action completion.
        for e in sorted(self._facility_events, key=lambda x: (x['at'], x['id'])):
            if e['at'] < self.now:
                self.facilities[e['place']] = e['open']
                self.processed.add(e['id'])
        if 'fact_model' in self.contract:
            from engine.fact_runtime import FactRuntime
            self.facts = FactRuntime(self, self.contract['fact_model'])
            self.facts.initialize()
        self.energy_rules = self.contract.get('energy', {'move_per_minute': 0, 'work_per_minute': 1/120,
                                                       'rest_per_minute': 1/60, 'rest_minutes': 60})
        self.planning = None
        if self.world.planner is not None:
            from engine.calendar_planning import CalendarPlanning
            if simulation.decision_provider is not None or self.facts is not None:
                raise ValueError('Native goal planning cannot mix an external chooser or semantic fact policy')
            self.planning = CalendarPlanning(self)

    @property
    def now(self):
        return self.time_kernel.now

    @now.setter
    def now(self, value):
        # Explicit checkpoint restoration only; no boundary is replayed.
        self.time_kernel.restore_position(value)

    def _people(self):
        return {p: {'zone': s.zone, 'knowledge': sorted(s.knowledge), 'inventory': dict(s.inventory),
                    'stamina': s.stamina, 'stress': s.stress} for p, s in sorted(self.people.items())}

    def emit(self, ident, verb, actor=None, *, result='applied', caused_by=None, **details):
        if any(row.get('id') == ident for row in self.rows):
            raise ValueError('Duplicate event effect')
        label = time_label(self.calendar, self.now)
        self.rows.append({'kind': 'event', 'id': ident, 'turn': self.now,
                          'day': (self.now // 1440)+1, 'slot': label['time'],
                          'absolute_minute': self.now, **label, 'subject': actor,
                          'verb': verb, 'result': result, 'args': [],
                          'delta': {'actor': {}, 'targets': {}, 'relations': [], 'objective': None},
                          'details': {'caused_by': caused_by or [], **deepcopy(details)}})
        row = self.rows[-1]
        if self.facts is not None:
            self.facts.capture(row)
        return row

    def _awake(self, at):
        clock = time_label(self.calendar, at)['time']
        return '06:00' <= clock < '22:00'

    def observe(self, event, audience, source='direct', via=None):
        if self._observation_buffer is not None:
            self._observation_buffer.append((deepcopy(event), list(audience), source, deepcopy(via)))
            return
        for actor in sorted(set(audience)):
            if actor not in self.people or event['id'] in self.observed[actor]:
                continue
            if source == 'direct' and not self._awake(self.now) and actor not in self._active_at_tick:
                continue
            if source == 'addressed' and not self._awake(self.now) and actor not in self._active_at_tick:
                item = {'event': deepcopy(event), 'actor': actor, 'received_at': self.now,
                        'via': deepcopy(via),
                        'read_at': (self.now//1440+1)*1440+360 if self.now%1440 >= 1320 else self.now//1440*1440+360}
                if not any(i['event']['id'] == event['id'] and i['actor'] == actor for i in self.pending_read):
                    self.pending_read.append(item)
                    self.emit('received:'+event['id']+':'+actor, 'message_received', actor,
                              received_at=self.now, read_at=item['read_at'], caused_by=[event['id']])
                continue
            self.observed[actor].add(event['id'])
            self.people[actor].knowledge.add(event['id'])
            self.knowledge[actor].append({'event_id': event['id'], 'observed_at': self.now,
                                         'source': source, 'confidence': 1.0, 'path': via or [event['id']]})
            d = event['details']
            if 'terms' in d:
                terms = d['terms']
                self.known_terms[actor][f"{terms['promise_id']}:{terms['version']}"] = d['terms_hash']
            if 'task_id' in d and 'task_progress' in d:
                self.known_tasks[actor][d['task_id']] = deepcopy(d['task_progress'])
            if 'promise_transition' in d:
                self._appraise(actor, d['promise_transition'], event['id'])

    def _appraise(self, observer, transition, evidence):
        promise = self.registry.get(transition['promise_id'], transition['version'])
        terms = promise['terms']
        if observer != terms['beneficiary']:
            return
        if transition['status'] not in ('fulfilled', 'breached'):
            return
        key = f"{transition['promise_id']}:{transition['version']}:{observer}"
        previous = self.appraisals.get(key, 0.0)
        coefficient = self.contract['appraisal']['fulfilled'] if transition['status'] == 'fulfilled' else self.contract['appraisal']['unexplained_breach']
        # Explicitly evidenced external obstruction gets no automatic blame.
        if transition.get('outcome_reason') == 'external_obstruction':
            coefficient = 0.0
        desired = float(coefficient)
        delta = desired-previous
        if delta:
            change = self.world.relations.change(observer, terms['promisor'], affinity=delta)
            self.emit('appraisal:'+evidence+':'+observer, 'observed_relationship_change', observer,
                      caused_by=[evidence], target=terms['promisor'], difference=delta, relation_delta=change)
        self.appraisals[key] = desired

    def configure(self, commands):
        try:
            return self._configure(commands)
        except (TypeError, KeyError, AttributeError, OverflowError) as error:
            raise ValueError('Malformed explicit calendar choices') from error

    def _configure(self, commands):
        if self.store is not None:
            raise ValueError('Choices are frozen for this run')
        if getattr(self, '_adapter_journal', None) is not None:
            if commands != self.commands:
                raise ValueError('Choices are frozen after enabling the adapter journal')
            return
        if not isinstance(commands, list) or len(commands) > 10000:
            raise ValueError('Bounded explicit command list required')
        verbs = {'move', 'work', 'offer', 'accept', 'change', 'accept_change', 'cancel',
                 'deliver', 'tell', 'wait', 'rest', 'depend', 'replan'}
        if self.facts is not None:
            from engine.fact_runtime import VERBS
            verbs |= VERBS
        seen = set()
        for command in commands:
            if not isinstance(command, dict) or not {'id', 'actor', 'at', 'verb'} <= set(command):
                raise ValueError('Incomplete choice')
            if set(command) - {'id', 'actor', 'at', 'verb', 'to', 'task_id', 'minutes', 'terms',
                              'promise_id', 'version', 'terms_hash', 'target', 'event_id',
                              'plan_id', 'then_task', 'fallback_task', 'priority', 'if_status'} - ({'message_id'} if self.facts is not None else set()):
                raise ValueError('Unsupported choice field')
            if not isinstance(command['id'], str) or not command['id'] or command['id'] in seen:
                raise ValueError('Choice IDs must be nonempty and unique')
            seen.add(command['id'])
            if command['actor'] not in self.people or command['verb'] not in verbs:
                raise ValueError('Unknown actor/action')
            needed = {'move': {'to'}, 'work': {'task_id', 'minutes'}, 'wait': {'minutes'}, 'rest': {'minutes'},
                      'offer': {'terms'}, 'change': {'terms'},
                      'accept': {'promise_id', 'version', 'terms_hash'},
                      'accept_change': {'promise_id', 'version', 'terms_hash'},
                      'cancel': {'promise_id', 'version', 'terms_hash'},
                      'deliver': {'task_id', 'target'}, 'tell': {'event_id', 'target'},
                      'depend': {'promise_id', 'plan_id', 'then_task', 'fallback_task'},
                      'replan': {'promise_id', 'plan_id', 'then_task', 'fallback_task'}}
            if self.facts is not None:
                needed.update(send_message={'message_id'}, read_message={'message_id'},
                              move_and_send={'message_id', 'to'}, move_and_read={'message_id', 'to'})
            if set(command)-{'id', 'actor', 'at', 'verb', 'priority', 'if_status'}-needed[command['verb']]:
                raise ValueError('Field does not belong to this action')
            for key in ('actor', 'verb', 'promise_id', 'plan_id', 'event_id', 'terms_hash', 'to', 'task_id', 'target', 'then_task', 'fallback_task'):
                if key in command and (not isinstance(command[key], str) or not command[key].strip()):
                    raise ValueError('Action identifiers must be nonempty text')
            if 'if_status' in command and (command['if_status'] not in ('offered', 'accepted', 'active', 'partial', 'overdue', 'fulfilled', 'breached', 'renegotiated', 'cancelled') or 'promise_id' not in command):
                raise ValueError('Conditional action needs an explicit promise/status')
            if not needed[command['verb']] <= command.keys():
                raise ValueError('Missing action-specific fields')
            for key in ('then_task', 'fallback_task'):
                if key in command and command[key] not in self.contract['tasks']:
                    raise ValueError('Unknown dependent task')
            if 'terms' in command:
                trial = PromiseRegistry(set(self.people), set(self.contract['tasks']), set(self.world.zones))
                trial.terms_hash(command['terms'])
            integer(command['at'], 'choice minute', self.now, self.end-1)
            integer(command.get('priority', 0), 'priority', 0, 10000)
            if command['verb'] in ('work', 'wait', 'rest'):
                integer(command.get('minutes'), 'action minutes', 1, 1440)
            if 'task_id' in command and command['task_id'] not in self.contract['tasks']:
                raise ValueError('Unknown task ID')
            if 'to' in command and command['to'] not in self.world.zones:
                raise ValueError('Unknown place')
            if 'target' in command and command['target'] not in self.people:
                raise ValueError('Unknown message recipient')
            if 'message_id' in command and command['message_id'] not in self.facts.definitions:
                raise ValueError('Unknown semantic message')
            if 'version' in command:
                integer(command['version'], 'promise version', 1, 10000)
        self.commands = deepcopy(sorted(commands, key=lambda c: (c['at'], c.get('priority', 0), c['id'])))
        provider = self.sim.decision_provider
        if provider is not None:
            if canonical(provider.commands) != canonical(self.commands):
                raise ValueError('Provider commands differ from the frozen runtime choices')
            provider_snapshot(provider)
        if self.facts is not None:
            expected = len(commands) + 2*sum(c['verb'] in {'move_and_read', 'move_and_send'} for c in commands)
            if len(self.facts.command_ids(commands)) != expected:
                raise ValueError('Composite component IDs collide with explicit command IDs')

    @staticmethod
    def _adapter_identity(identity):
        import re
        if (not isinstance(identity, dict)
                or set(identity) != {'adapter', 'schema_version', 'source_hash', 'config_hash'}
                or type(identity['adapter']) is not str or not identity['adapter']
                or type(identity['schema_version']) is not int or identity['schema_version'] != 1
                or any(type(identity[k]) is not str or re.fullmatch('[0-9a-f]{64}', identity[k]) is None
                       for k in ('source_hash', 'config_hash'))):
            raise ValueError('Invalid adapter journal identity')
        return deepcopy(identity)

    def enable_adapter_journal(self, identity):
        """Opt in before persistence; this does not intercept arbitrary _start calls."""
        identity = self._adapter_identity(identity)
        if getattr(self, '_adapter_journal', None) is not None:
            if self._adapter_journal['identity'] != identity:
                raise ValueError('Adapter journal identity mismatch')
            return
        if self.store is not None or self.busy:
            raise ValueError('Enable adapter journal before persistence or pending actions')
        self._adapter_journal = {'identity': identity, 'entries': {},
                                 'initial_rows': {'count': len(self.rows), 'sha256': digest(self.rows)}}

    def _frozen_command_ids(self):
        return (self.facts.command_ids(self.commands) if self.facts is not None
                else {c['id'] for c in self.commands})

    def _adapter_command(self, command, now):
        if not isinstance(command, dict) or command.get('verb') not in {'move', 'work', 'wait', 'rest', 'deliver'}:
            raise ValueError('Unsupported adapter command')
        fields = {'id', 'actor', 'at', 'verb'} | ({'to'} if command['verb'] == 'move'
                  else {'minutes', 'task_id'} if command['verb'] == 'work'
                  else {'task_id','target'} if command['verb'] == 'deliver' else {'minutes'})
        if (set(command) != fields or type(command['id']) is not str or not command['id']
                or type(command['actor']) is not str or command['actor'] not in self.people):
            raise ValueError('Invalid adapter command fields/identity')
        integer(command['at'], 'adapter command minute', self.time_kernel.axis.start, now)
        if command['verb'] == 'move':
            if type(command['to']) is not str or command['to'] not in self.world.zones:
                raise ValueError('Invalid adapter destination')
        elif command['verb'] == 'deliver':
            if command['task_id'] not in self.contract['tasks'] or command['target'] not in self.people:
                raise ValueError('Invalid adapter delivery')
        else:
            integer(command['minutes'], 'adapter action duration', 1, self.end-self.time_kernel.axis.start)
            if command['verb'] == 'work' and command['task_id'] not in self.contract['tasks']:
                raise ValueError('Invalid adapter task')

    def admit_adapter_command(self, command, *, proposal_id, candidate_id, effective_duration=None):
        """Record one explicit adapter admission separately from frozen choices."""
        journal = getattr(self, '_adapter_journal', None)
        if journal is None:
            raise ValueError('Adapter journal is not enabled')
        self._adapter_command(command, self.now)
        if any(type(v) is not str or not v for v in (proposal_id, candidate_id)):
            raise ValueError('Adapter proposal/candidate IDs required')
        if effective_duration is not None:
            integer(effective_duration, 'adapter effective duration', 1, self.end-self.time_kernel.axis.start)
            if command['verb'] != 'move':
                raise ValueError('Only adjacent moves support duration overrides')
        old_entry = journal['entries'].get(proposal_id)
        if old_entry is not None:
            receipt = old_entry['receipt']
            if (receipt['command'] != command or receipt['candidate_id'] != candidate_id
                    or old_entry['requested_duration'] != effective_duration):
                raise ValueError('Changed adapter proposal collision')
            return deepcopy(receipt)
        if command['at'] != self.now:
            raise ValueError('Adapter admission must be at the current minute')
        ident = command['id']
        if (ident in self._frozen_command_ids()
                or any(e['receipt']['command']['id'] == ident for e in journal['entries'].values())
                or any(r.get('id') in {'decision:'+ident, 'start:'+ident, 'reject:'+ident,
                                      'complete:'+ident, 'interrupt:'+ident} for r in self.rows)):
            raise ValueError('Adapter command identity collision')
        source = self.people[command['actor']].zone
        route = self.contract['travel_minutes'].get(source, {})
        nominal = self._command_duration(source, command)
        if effective_duration is not None and nominal is None:
            raise ValueError('Duration override requires an adjacent route')
        duration = nominal if effective_duration is None else effective_duration
        backup = self.export_state()
        transient = (deepcopy(self._observation_buffer), deepcopy(self._active_at_tick))
        try:
            try:
                if effective_duration is not None:
                    route[command['to']] = effective_duration
                self._start(deepcopy(command))
            finally:
                if effective_duration is not None:
                    route[command['to']] = nominal
            outcome = 'started' if any(r.get('id') == 'start:'+ident for r in self.rows) else 'rejected'
            if outcome == 'rejected' and not any(r.get('id') == 'reject:'+ident for r in self.rows):
                raise ValueError('Adapter admission produced no start or rejection')
            receipt = {'command': deepcopy(command), 'proposal_id': proposal_id,
                       'candidate_id': candidate_id, 'effective_duration': duration, 'outcome': outcome}
            journal['entries'][proposal_id] = {'requested_duration': effective_duration, 'source': source, 'receipt': receipt}
            return deepcopy(receipt)
        except BaseException:
            self.restore_state(backup)
            self._observation_buffer, self._active_at_tick = transient
            raise

    def _validate_adapter_journal(self, state):
        """Validate detached journal and cross-references before any live mutation."""
        journal = state['adapter_journal']
        if (not isinstance(journal, dict) or set(journal) != {'identity', 'entries', 'initial_rows'}
                or self._adapter_identity(journal['identity']) != self._adapter_journal['identity']
                or not isinstance(journal['entries'], dict)
                or not isinstance(journal['initial_rows'], dict)
                or type(journal['initial_rows'].get('count')) is not int
                or journal['initial_rows'] != self._adapter_journal['initial_rows']):
            raise ValueError('Invalid adapter journal state/identity')
        vitality = state['actor_vitality']
        if (not isinstance(vitality, dict) or set(vitality) != set(self.people)
                or any(type(v) is not str or v not in {'alive', 'downed', 'dead', 'revived'} for v in vitality.values())):
            raise ValueError('Invalid adapter actor vitality')
        frozen_ids = self._frozen_command_ids()
        ids = set(frozen_ids)
        rows = state['rows']
        prefix = journal['initial_rows']
        if (len(rows) < prefix['count'] or digest(rows[:prefix['count']]) != prefix['sha256']):
            raise ValueError('Adapter initial row prefix mismatch')
        by_id = {r.get('id'): r for r in rows}
        commands = {}
        for proposal, entry in journal['entries'].items():
            if type(proposal) is not str or not proposal or not isinstance(entry, dict) or set(entry) != {'requested_duration', 'source', 'receipt'}:
                raise ValueError('Invalid adapter journal entry')
            receipt = entry['receipt']
            if (not isinstance(receipt, dict) or set(receipt) != {'command', 'proposal_id', 'candidate_id', 'effective_duration', 'outcome'}
                    or receipt['proposal_id'] != proposal or type(receipt['candidate_id']) is not str or not receipt['candidate_id']
                    or receipt['outcome'] not in {'started', 'rejected'}):
                raise ValueError('Invalid adapter receipt')
            command = receipt['command']; self._adapter_command(command, state['now'])
            ident = command['id']
            if ident in ids:
                raise ValueError('Duplicate adapter command identity')
            ids.add(ident); commands[ident] = command
            requested, duration = entry['requested_duration'], receipt['effective_duration']
            source = entry['source']
            if type(source) is not str or source not in set(self.world.zones)|{'移動中'}:
                raise ValueError('Invalid adapter admission source')
            nominal = self._command_duration(source, command)
            if (requested is not None and nominal is None) or duration != (nominal if requested is None else requested):
                raise ValueError('Adapter admission duration differs from source/command')
            if requested is not None:
                integer(requested, 'journal requested duration', 1, self.end-self.time_kernel.axis.start)
                if command['verb'] != 'move' or duration != requested:
                    raise ValueError('Invalid journal duration override')
            if duration is not None:
                integer(duration, 'journal effective duration', 1, self.end-self.time_kernel.axis.start)
            if command['verb'] != 'move' and duration != nominal:
                raise ValueError('Adapter duration differs from command')
            decision = by_id.get('decision:'+ident)
            start, reject = by_id.get('start:'+ident), by_id.get('reject:'+ident)
            if (decision is None or decision.get('verb') != 'decision' or decision.get('subject') != command['actor']
                    or decision.get('absolute_minute') != command['at'] or decision.get('details', {}).get('choice') != command):
                raise ValueError('Adapter decision/journal mismatch')
            row = start if receipt['outcome'] == 'started' else reject
            if (row is None or (reject is not None if start is row else start is not None)
                    or row.get('subject') != command['actor'] or row.get('absolute_minute') != command['at']
                    or row.get('details', {}).get('choice') != command
                    or row.get('verb') != ('action_started' if receipt['outcome'] == 'started' else 'action_rejected')):
                raise ValueError('Adapter outcome/journal mismatch')
            busy = state['busy'].get(command['actor'])
            matching_busy = busy is not None and busy.get('id') == ident
            complete, interrupt = by_id.get('complete:'+ident), by_id.get('interrupt:'+ident)
            if receipt['outcome'] == 'rejected':
                if matching_busy or complete is not None or interrupt is not None:
                    raise ValueError('Rejected adapter command has effects')
                continue
            actual = start['details'].get('duration_minutes')
            integer(actual, 'journal started duration', 1, self.end-self.time_kernel.axis.start)
            if (duration is None or actual > duration or (command['verb'] != 'work' and actual != duration)
                    or start['details'].get('finish_at') != command['at']+actual):
                raise ValueError('Adapter start duration mismatch')
            if start['details'].get('source') != source:
                raise ValueError('Adapter admission/start source mismatch')
            if command['verb'] == 'move':
                nominal = self.contract['travel_minutes'].get(source, {}).get(command['to'])
                if nominal is None or (requested is None and duration != nominal):
                    raise ValueError('Adapter move is not an approved adjacent duration')
            if sum((matching_busy, complete is not None, interrupt is not None)) != 1:
                raise ValueError('Adapter command must be pending or terminal exactly once')
            extra = {'start', 'finish', 'last_progress', 'source', 'work_unit_index'}
            if not matching_busy:
                terminal = complete if complete is not None else interrupt
                action = terminal.get('details', {}).get('choice')
                if (terminal.get('subject') != command['actor']
                        or terminal.get('verb') != ('action_completed' if complete is not None else 'work_interrupted')
                        or not isinstance(action, dict) or set(action) != set(command)|extra
                        or any(action[k] != v for k,v in command.items())
                        or action['start'] != command['at'] or action['finish'] != command['at']+actual
                        or action['source'] != source
                        or terminal['details'].get('started_at') != command['at']):
                    raise ValueError('Adapter terminal command mismatch')
                at = terminal.get('absolute_minute')
                integer(at, 'adapter terminal minute', command['at'], state['now'])
                if (terminal['details'].get('duration_minutes') != at-command['at']
                        or (complete is not None and at != action['finish'])
                        or (interrupt is not None and (command['verb'] != 'work' or at >= action['finish']))):
                    raise ValueError('Adapter terminal duration mismatch')
            if matching_busy:
                integer(busy.get('work_unit_index'), 'adapter work unit index', 0, 10000)
                if (set(busy) != set(command)|extra or any(busy[k] != v for k,v in command.items())
                        or busy['start'] != command['at'] or busy['finish'] != command['at']+actual
                        or busy['source'] != source):
                    raise ValueError('Adapter pending command/duration mismatch')
        # Reverse coverage prevents deleting a completed/rejected admission while
        # retaining its effects. Initialization rows predate journal ownership.
        verbs = {'decision', 'action_started', 'action_completed', 'action_rejected', 'work_interrupted'}
        later_rows = rows[prefix['count']:]
        for row in later_rows:
            if row.get('verb') in verbs:
                choice = row.get('details', {}).get('choice')
                if not isinstance(choice, dict) or choice.get('id') not in frozen_ids | set(commands):
                    raise ValueError('Execution row has no frozen or journaled admission')
        later_ids = {r.get('id') for r in later_rows}
        if any('decision:'+ident not in later_ids for ident in commands):
            raise ValueError('Adapter admission predates journal enablement')
        return deepcopy(journal), deepcopy(vitality), commands

    def candidates(self, actor):
        person = self.people[actor]
        result = [{'verb': 'wait', 'minutes': 1}]
        if actor in self.busy:
            return self.facts.candidates(actor, []) if self.facts is not None else []
        for to, duration in sorted(self.contract['travel_minutes'].get(person.zone, {}).items()):
            result.append({'verb': 'move', 'to': to, 'minutes': duration, 'prediction': 'known_route'})
        for task, spec in sorted(self.contract['tasks'].items()):
            if spec['operation'] == 'work' and spec['place'] == person.zone and set(spec['roles']) & set(self.contract['roles'][actor]):
                known = self.known_tasks[actor].get(task)
                result.append({'verb': 'work', 'task_id': task, 'minutes_per_unit': spec['minutes_per_unit'],
                               'known_units': None if known is None else known['units'],
                               'requirements': spec['dependencies'], 'feasibility': 'check_at_execution'})
        handoffs = {task: spec['minutes_per_unit'] for task, spec in self.contract['tasks'].items()
                    if spec['operation'] == 'deliver'}
        for name in ('offer', 'accept', 'change', 'accept_change', 'cancel', 'deliver', 'tell', 'depend', 'replan'):
            result.append({'verb': name, 'task_minutes': handoffs} if name == 'deliver' and any(v != 2 for v in handoffs.values())
                          else {'verb': name, 'minutes': 2})
        return self.facts.candidates(actor, result) if self.facts is not None else result

    def _command_duration(self, source, command):
        if command['verb'] == 'move': return self.contract['travel_minutes'].get(source, {}).get(command.get('to'))
        if command['verb'] == 'deliver': return self.contract['tasks'][command['task_id']]['minutes_per_unit']
        return command['minutes']

    def _start(self, command):
        if self.facts is not None:
            from engine.fact_runtime import VERBS
            if command['verb'] in VERBS:
                self.facts.start(command)
                return
        actor, ident = command['actor'], command['id']
        person = self.people[actor]
        choice = deepcopy(command)
        if 'if_status' in choice:
            # Conditions use observed promise evidence, never an unseen global
            # fulfillment. All case-control plans are labelled acceptance input.
            known = any(r['details'].get('promise_transition', {}).get('promise_id') == choice.get('promise_id')
                        and r['details'].get('promise_transition', {}).get('status') == choice['if_status']
                        and r['id'] in self.observed[actor] for r in self.rows)
            if not known:
                self.emit('skip:'+ident, 'conditional_choice_not_taken', actor, choice=choice)
                return
        offered = self.candidates(actor)
        self.emit('decision:'+ident, 'decision', actor, choice=choice, candidates=offered,
                  knowledge_version=len(self.knowledge[actor]) + (len(self.facts.knowledge_ids(actor)) if self.facts is not None else 0),
                  knowledge_event_ids=sorted(self.observed[actor] | (self.facts.knowledge_ids(actor) if self.facts is not None else set())),
                  selection_mechanism='explicit_acceptance_witness_command', calculation=None,
                  known_promises=deepcopy(self.known_terms[actor]))
        reason = None
        verb = choice['verb']
        duration = choice['minutes'] if verb in ('work', 'wait', 'rest') else 2
        if actor in self.busy:
            reason = 'actor_busy'
        elif self.facts is not None and not self.facts.can_overlap(actor, verb):
            reason = 'communication_busy'
        elif verb == 'move':
            duration = self.contract['travel_minutes'].get(person.zone, {}).get(choice.get('to'))
            if duration is None:
                reason = 'not_adjacent'
        elif verb == 'work':
            spec = self.contract['tasks'].get(choice.get('task_id'))
            if spec is None or spec['operation'] != 'work' or spec['place'] != person.zone or not (set(spec['roles']) & set(self.contract['roles'][actor])):
                reason = 'task_place_or_role'
            elif spec['place'] in self.facilities and not self.facilities[spec['place']]:
                reason = 'facility_closed'
            elif any(self.ledger['tasks'][dep]['units'] < self.contract['tasks'][dep]['units'] for dep in spec['dependencies']):
                reason = 'prerequisite_incomplete'
            elif any(x['verb'] == 'work' and self.contract['tasks'][x['task_id']]['resource'] == spec['resource'] for x in self.busy.values()):
                reason = 'resource_busy'
            else:
                task = self.ledger['tasks'][choice['task_id']]
                remaining = spec['units']*spec['minutes_per_unit']-task['effort_minutes']
                if remaining <= 0:
                    reason = 'task_already_complete'
                else:
                    duration = min(duration, remaining)
                    if ('energy' in self.contract and person.stamina+1e-9 < duration*self.energy_rules['work_per_minute']):
                        self.emit('reject:'+ident, 'action_rejected', actor, result='rejected', reason='insufficient_stamina', choice=choice)
                        return
                    reserve = (task['effort_minutes']+duration+spec['minutes_per_unit']-1)//spec['minutes_per_unit']-task['reserved_units']
                    if self.now+duration > self.end:
                        reason = 'outside_observation'
                    elif any(self.ledger['materials'][item] < reserve*qty for item, qty in spec['materials_per_unit'].items()):
                        reason = 'materials_unavailable'
                    else:
                        for item, qty in spec['materials_per_unit'].items():
                            self.ledger['materials'][item] -= reserve*qty
                        task['reserved_units'] += reserve
        elif verb == 'deliver':
            spec = self.contract['tasks'].get(choice.get('task_id'))
            if (spec is None or spec['operation'] != 'deliver' or spec['place'] != person.zone
                    or not (set(spec['roles']) & set(self.contract['roles'][actor]))):
                reason = 'task_place_or_role'
            else:
                duration = spec['minutes_per_unit']
        if reason:
            self.emit('reject:'+ident, 'action_rejected', actor, result='rejected', reason=reason, choice=choice)
            return
        if 'energy' in self.contract and verb in ('move','work'):
            rate = self.energy_rules[verb+'_per_minute']
            if float(Fraction(*self.energy[actor]))+1e-9 < duration*rate:
                self.emit('reject:'+ident, 'action_rejected', actor, result='rejected', reason='insufficient_stamina', choice=choice)
                return
        if not isinstance(duration, int) or duration < 1 or self.now+duration > self.end:
            self.emit('reject:'+ident, 'action_rejected', actor, result='rejected', reason='outside_observation', choice=choice)
            return
        if verb in ('accept', 'accept_change', 'cancel'):
            key = f"{choice.get('promise_id')}:{choice.get('version')}"
            if self.known_terms[actor].get(key) != choice.get('terms_hash'):
                self.emit('reject:'+ident, 'action_rejected', actor, result='rejected', reason='terms_not_received', choice=choice)
                return
        source = person.zone
        window = self.time_kernel.window(duration, unit='minute')
        pending = {**choice, 'start': window.start, 'finish': window.finish,
                   'last_progress': self.now, 'source': source, 'work_unit_index': 0}
        self.lifecycle.start(actor, pending, duration)
        if verb == 'move':
            person.zone = '移動中'
        self.emit('start:'+ident, 'action_started', actor, choice=choice, duration_minutes=duration,
                  finish_at=pending['finish'], source=source, reserved_resource=(self.contract['tasks'][choice['task_id']]['resource'] if verb == 'work' else None))

    def _accrue(self, at, *, elapsed=None):
        elapsed = at-self.now if elapsed is None else elapsed
        for actor, person in sorted(self.people.items()):
            # Recovery uses elapsed minutes, not number/size of display bands.
            if self.planning is None and actor not in self.busy and (self.facts is None or not self.facts.actor_busy(actor)):
                energy = min(Fraction(str(person.stamina_max)), Fraction(*self.energy[actor])+Fraction(elapsed, 60))
                self.energy[actor] = [energy.numerator, energy.denominator]
                person.stamina = float(energy)
        for actor, action in sorted(self.busy.items(), key=lambda pair: pair[1]['id']):
            if action['verb'] == 'move' and 'energy' in self.contract:
                minutes = max(0, min(at, action['finish'])-action['last_progress'])
                energy = max(Fraction(0), Fraction(*self.energy[actor])-minutes*Fraction(str(self.energy_rules['move_per_minute'])))
                self.energy[actor] = [energy.numerator, energy.denominator]
                self.people[actor].stamina = float(energy)
                action['last_progress'] = min(at, action['finish'])
            if action['verb'] != 'work':
                continue
            task_id = action['task_id']; task = self.ledger['tasks'][task_id]; spec = self.contract['tasks'][task_id]
            minutes = self.lifecycle.accrued(action, at)
            if minutes <= 0:
                continue
            before = task['units']
            task['effort_minutes'] += minutes
            for dep in self.dependencies.values():
                if dep['actor'] == actor and dep['fallback_task'] == task_id:
                    dep['burden_minutes'] += minutes
                    dep['status'] = 'fallback_selected'
            task['units'] = task['effort_minutes']//spec['minutes_per_unit']
            action['last_progress'] = min(at, action['finish'])
            cost = minutes*Fraction(str(self.energy_rules['work_per_minute'])) if 'energy' in self.contract else Fraction(minutes, 120)
            energy = max(Fraction(0), Fraction(*self.energy[actor])-cost)
            self.energy[actor] = [energy.numerator, energy.denominator]
            self.people[actor].stamina = float(energy)
            for _ in range(task['units']-before):
                action['work_unit_index'] += 1
                task['contributions'][actor] = task['contributions'].get(actor, 0)+1
                event = {'id': f"unit:{action['id']}:{action['work_unit_index']}", 'task_id': task_id,
                         'actor': actor, 'units': 1, 'at': at, 'place': spec['place']}
                self.ledger['completion_events'].append(event)
                previous = self.now; self.now = at
                row = self.emit(event['id'], 'work_unit_completed', actor, task_id=task_id, units=1,
                                task_progress=deepcopy(task), caused_by=['start:'+action['id']])
                self.observe(row, [p for p, s in self.people.items() if s.zone == spec['place']])
                self.now = previous

    def _complete(self, actor, action):
        ident, verb = action['id'], action['verb']
        person = self.people[actor]
        details = {'choice': deepcopy(action), 'started_at': action['start'], 'duration_minutes': self.now-action['start']}
        audience = [actor]
        try:
            if verb == 'move':
                target = action['to']
                if target in self.facilities and not self.facilities[target]:
                    person.zone = action['source']; details['failure_reason'] = 'destination_closed'
                else:
                    person.zone = target
                    details['arrived_at'] = self.now
            elif verb == 'rest':
                before = person.stamina
                energy = min(Fraction(str(person.stamina_max)), Fraction(*self.energy[actor])+
                             (self.now-action['start'])*Fraction(str(self.energy_rules['rest_per_minute'])))
                self.energy[actor] = [energy.numerator, energy.denominator]; person.stamina = float(energy)
                details['stamina_delta'] = person.stamina-before
            elif verb == 'work':
                details.update(task_id=action['task_id'], task_progress=deepcopy(self.ledger['tasks'][action['task_id']]))
                audience = [p for p, s in self.people.items() if s.zone == person.zone]
            elif verb in ('offer', 'change'):
                terms = action['terms']
                if terms['proposer'] != actor:
                    raise ValueError('Only proposer may offer/change terms')
                if verb == 'offer':
                    self.registry.offer(terms, self.now, 'complete:'+ident)
                else:
                    self.registry.propose_change(terms['promise_id'], terms, self.now, 'complete:'+ident)
                record = self.registry.get(terms['promise_id'], terms['version'])
                details.update(terms=record['terms'], terms_hash=record['terms_hash'])
                audience = record['terms']['acceptance_parties']
            elif verb in ('accept', 'accept_change', 'cancel'):
                method = getattr(self.registry, verb)
                method(action['promise_id'], action['version'], actor, action['terms_hash'], self.now, 'complete:'+ident)
                record = self.registry.get(action['promise_id'], action['version'])
                audience = record['terms']['acceptance_parties']
                details.update(promise_id=action['promise_id'], version=action['version'], status=record['status'])
            elif verb == 'deliver':
                task = action['task_id']; target = action['target']; spec = self.contract['tasks'][task]
                if spec['operation'] != 'deliver':
                    raise ValueError('Task is not a delivery operation')
                if self.people[target].zone != person.zone or person.zone != spec['place']:
                    raise ValueError('Handoff requires both parties at agreed place')
                if any(self.ledger['tasks'][d]['units'] < self.contract['tasks'][d]['units'] for d in spec['dependencies']):
                    raise ValueError('Deliverable prerequisites incomplete')
                if self.ledger['tasks'][task]['units'] >= spec['units']:
                    raise ValueError('Deliverable already handed off')
                event = {'id': 'delivery:'+ident, 'task_id': task, 'actor': actor, 'units': 1,
                         'at': self.now, 'place': person.zone, 'delivered_to': target}
                self.ledger['completion_events'].append(event)
                self.ledger['tasks'][task]['units'] += 1
                self.ledger['tasks'][task]['effort_minutes'] += spec['minutes_per_unit']
                contributions = self.ledger['tasks'][task]['contributions']
                contributions[actor] = contributions.get(actor, 0)+1
                details.update(task_id=task, delivered_to=target, task_progress=deepcopy(self.ledger['tasks'][task]))
                audience = [actor, target]
            elif verb == 'tell':
                evidence = next((r for r in self.rows if r['id'] == action['event_id']), None)
                if evidence is None or evidence['id'] not in self.observed[actor]:
                    raise ValueError('Cannot transmit unknown evidence')
                self.observe(evidence, [action['target']], 'addressed',
                             via=[evidence['id'], 'complete:'+ident, actor])
                details.update(transmitted_event_id=evidence['id'])
                audience = [actor, action['target']]
            elif verb in ('depend', 'replan'):
                record = self.registry.get(action['promise_id'])
                key = f"{record['promise_id']}:{record['version']}"
                known_acceptance = any(
                    r['id'] in self.observed[actor] and (
                        (r['details'].get('promise_transition', {}).get('promise_id') == record['promise_id']
                         and r['details']['promise_transition'].get('version') == record['version']
                         and r['details']['promise_transition'].get('status') in ('accepted', 'active', 'fulfilled'))
                        or (r['verb'] == 'action_completed' and r['result'] == 'applied'
                            and r['details'].get('promise_id') == record['promise_id']
                            and r['details'].get('version') == record['version']
                            and r['details'].get('status') in ('accepted', 'active', 'fulfilled')))
                    for r in self.rows)
                if key not in self.known_terms[actor] or not known_acceptance or record['status'] not in ('accepted', 'active', 'fulfilled'):
                    raise ValueError('Dependency needs known mutually accepted terms')
                if action['plan_id'] in self.dependencies and verb == 'depend':
                    raise ValueError('Duplicate dependent plan')
                old = self.dependencies.get(action['plan_id'])
                if verb == 'replan' and (old is None or old['actor'] != actor or old['version'] >= record['version']):
                    raise ValueError('Replanning needs own previous plan and newer known agreement')
                self.dependencies[action['plan_id']] = {'actor': actor, 'promise_id': record['promise_id'],
                    'version': record['version'], 'selected_at': self.now, 'then_task': action['then_task'],
                    'fallback_task': action['fallback_task'], 'status': 'planned', 'burden_minutes': 0,
                    'evidence_ids': ['complete:'+ident], 'prior_plan': deepcopy(old)}
                details.update(dependent_plan=deepcopy(self.dependencies[action['plan_id']]))
        except ValueError as exc:
            details['failure_reason'] = str(exc)
        if self.planning is not None: self.planning.completed(action, details)
        row = self.emit('complete:'+ident, 'action_completed', actor,
                        result='failed' if 'failure_reason' in details else 'applied',
                        caused_by=['start:'+ident], **details)
        if verb in ('offer', 'change', 'accept', 'accept_change', 'cancel'):
            self.observe(row, [actor], 'direct')
            self.observe(row, [p for p in audience if p != actor], 'addressed')
        else:
            self.observe(row, audience)

    def _evaluate(self):
        for transition in self.registry.evaluate(self.now, self.ledger):
            row = self.emit(transition['id'], 'promise_transition', promise_transition=transition,
                            caused_by=transition.get('evidence_ids', []))
            terms = self.registry.get(transition['promise_id'], transition['version'])['terms']
            if transition['status'] in ('offered', 'accepted', 'active', 'renegotiated', 'cancelled'):
                self.observe(row, terms['acceptance_parties'], 'addressed')
            else:
                self.observe(row, [p for p, s in self.people.items() if s.zone == terms['place']])
            for dep in self.dependencies.values():
                if dep['promise_id'] == transition['promise_id'] and dep['version'] == transition['version']:
                    dep['objective_status'] = transition['status']
                    dep['evidence_ids'].append(row['id'])

    def _points(self):
        points = {self.end}
        if self.planning is not None:
            points.update(p for spec in self.world.goal_rules.definitions.values() for p in spec['period'].values())
        if self.facts is not None:
            points.update(self.facts.points())
        points.update(c['at'] for c in self.commands if c['id'] not in self.processed)
        points.update(x for x in self.calendar['checkpoints'] if x >= self.now)
        points.update(e['minute'] for e in self.calendar['events'] if 'scheduled:'+e['id'] not in self.processed)
        points.update(e['at'] for e in self._facility_events if e['id'] not in self.processed)
        points.update(p['read_at'] for p in self.pending_read)
        for action in self.busy.values():
            points.add(action['finish'])
            if action['verb'] == 'work':
                spec = self.contract['tasks'][action['task_id']]
                effort = self.ledger['tasks'][action['task_id']]['effort_minutes']
                due = self.now + spec['minutes_per_unit'] - effort % spec['minutes_per_unit']
                if due <= action['finish']:
                    points.add(due)
        # Registry versions are exported as data, deadlines processed even at
        # night or outside every display band.
        for record in self._promise_records():
            if record['status'] not in ('fulfilled', 'breached', 'cancelled', 'renegotiated'):
                terms = record['terms']
                points.update(x for x in (terms['start'], terms['deadline'], terms['grace_until']) if x is not None and x >= self.now)
        return sorted(x for x in points if self.now <= x <= self.end)

    def _promise_records(self):
        # Public export format is adapted after the Registry schema is fixed.
        state = self.registry.export_state()
        return [record for entry in state['promises'].values()
                for record in entry['versions'].values()]

    def _boundary_plan(self):
        return (
            BoundaryStage('begin', self._time_begin),
            BoundaryStage('physical_completion', self._time_physical_completion),
            BoundaryStage('facility_changes', self._time_facility_changes),
            BoundaryStage('scheduled_events', self._time_scheduled_events),
            BoundaryStage('evaluate_before_agreements', self._time_evaluate_before_agreements),
            BoundaryStage('agreement_completion', self._time_agreement_completion),
            BoundaryStage('evaluate_after_agreements', self._time_evaluate_after_agreements),
            BoundaryStage('observers', self._time_observers),
            BoundaryStage('recognition', self._time_recognition),
            BoundaryStage('communication', self._time_communication),
            BoundaryStage('display', self._time_display),
            BoundaryStage('terminal', self._time_terminal),
            BoundaryStage('choices', self._time_choices),
        )

    def _tick(self, at):
        # Kept as a compatibility entry for frozen explicit adapters.
        return self.time_kernel.dispatch(at, self._boundary_plan())

    def _time_begin(self, frame):
        at = frame.at
        self._observation_buffer = []
        self._active_at_tick = {p for p, a in self.busy.items() if a['verb'] != 'wait'}
        self._accrue(at, elapsed=frame.elapsed)

    def _time_physical_completion(self, frame):
        at = frame.at
        semantic = []
        for actor, action in self.lifecycle.complete_due(at, key=lambda pair: (pair[1]['verb'] != 'move', pair[1]['verb'] != 'work', pair[1]['id'])):
            if action['verb'] in ('offer', 'accept', 'change', 'accept_change', 'cancel', 'depend', 'replan'):
                semantic.append((actor, action))
            else:
                self._complete(actor, action)
        frame.data['semantic'] = semantic

    def _time_facility_changes(self, frame):
        at = frame.at
        # Completed work and arrivals are visible before facility/scheduled
        # transitions and deadline/opening reads at the same minute.
        for event in sorted(self._facility_events, key=lambda e: e['id']):
            if event['at'] == at and event['id'] not in self.processed:
                self.facilities[event['place']] = event['open']
                self.processed.add(event['id'])
                self.emit(event['id'], 'facility_event', place=event['place'], open=event['open'])
                if not event['open']:
                    for actor, action in sorted(list(self.busy.items())):
                        if action['verb'] == 'work' and self.contract['tasks'][action['task_id']]['place'] == event['place']:
                            self.lifecycle.interrupt(actor)
                            self.emit('interrupt:'+action['id'], 'work_interrupted', actor,
                                      reason='facility_closed', task_id=action['task_id'],
                                      duration_minutes=self.now-action['start'], started_at=action['start'], choice=deepcopy(action),
                                      retained=deepcopy(self.ledger['tasks'][action['task_id']]))

    def _time_scheduled_events(self, frame):
        at = frame.at
        observers = []
        for event in sorted(self.calendar['events'], key=lambda e: e['id']):
            key = 'scheduled:'+event['id']
            if event['minute'] == at and key not in self.processed:
                self.processed.add(key)
                row = self.emit(key, 'scheduled_notice', label=event.get('label'), schedule=event)
                # Authored schedule is common public information; this
                # grants no private promise/work knowledge or teleportation.
                self.observe(row, list(self.people), 'public_schedule')
                observers.extend(observer for observer in self.contract.get('observers', [])
                                 if observer['event_id'] == event['id'])
        frame.data['observers'] = observers

    def _time_evaluate_before_agreements(self, frame):
        at = frame.at
        self._evaluate()

    def _time_agreement_completion(self, frame):
        at = frame.at
        semantic = frame.data['semantic']
        for actor, action in semantic:
            self._complete(actor, action)

    def _time_evaluate_after_agreements(self, frame):
        at = frame.at
        self._evaluate()

    def _time_observers(self, frame):
        at = frame.at
        observers = frame.data['observers']
        for observer in observers:
            value = evaluate_observer(observer, self.people, self.ledger, self.contract['tasks'], at)
            self.observations[observer['id']] = value
            self.emit(observer['id'], observer['verb'], **value)

    def _time_recognition(self, frame):
        at = frame.at
        buffered = self._observation_buffer
        self._observation_buffer = None
        for row, audience, source, via in buffered:
            # Finish arrivals before deciding who can see an actual work unit.
            if row['verb'] in ('work_unit_completed',) or (row['verb'] == 'action_completed' and row['details'].get('task_id')):
                task_id = row['details'].get('task_id')
                if task_id:
                    audience = sorted(set(audience) | {p for p, s in self.people.items() if s.zone == self.contract['tasks'][task_id]['place']})
            self.observe(row, audience, source, via=via)
        for item in self.pending_read[:]:
            if item['read_at'] == at:
                self.pending_read.remove(item)
                self.observe(item['event'], [item['actor']], 'addressed', via=item.get('via'))
                self.emit('read:'+item['event']['id']+':'+item['actor'], 'message_read', item['actor'],
                          received_at=item['received_at'], read_at=at, caused_by=[item['event']['id']])

    def _time_communication(self, frame):
        at = frame.at
        if self.facts is not None:
            self.facts.tick()

    def _time_display(self, frame):
        at = frame.at
        if at in self.calendar['checkpoints']:
            self.emit('calendar:'+str(at), 'calendar_checkpoint', display_only=True)

    def _time_terminal(self, frame):
        at = frame.at
        self._record_goals(close=at==self.end)
        if at == self.end:
            self.done = True
            return BoundaryResult.HALT

    def _time_choices(self, frame):
        at = frame.at
        for command in self.commands:
            if command['at'] == at and command['id'] not in self.processed:
                self.processed.add(command['id'])
                if self.sim.decision_provider is not None:
                    self.sim.decision_provider.record_choice(command)
                self._start(command)
        if self.planning is not None: self.planning.choose()
        self._record_goals(close=True)

    def _record_goals(self, *, close):
        if self.planning is None: return
        for event in self.world.goal_rules.record(self.world,self.now,close=close):
            outcome=event['details']['objective_outcome']
            ident=f"objective:{outcome['condition']}:{self.now}:{outcome['status']}"
            self.emit(ident,event['verb'],**event['details'])

    def export_state(self):
        state = {'now': self.now, 'end': self.end, 'people': self._people(),
                'energy': deepcopy(self.energy),
                'relations': self.world.relations.snapshot(), 'ledger': deepcopy(self.ledger),
                'promises': self.registry.export_state(), 'busy': deepcopy(self.busy),
                'processed': sorted(self.processed), 'observed': {p: sorted(v) for p, v in self.observed.items()},
                'known_terms': deepcopy(self.known_terms), 'known_tasks': deepcopy(self.known_tasks),
                'knowledge': deepcopy(self.knowledge), 'pending_read': deepcopy(self.pending_read),
                'dependencies': deepcopy(self.dependencies), 'appraisals': deepcopy(self.appraisals),
                'rows': deepcopy(self.rows), 'observations': deepcopy(self.observations), 'done': self.done,
                'facilities': deepcopy(self.facilities), 'rng': self.sim.rng.getstate()}
        if getattr(self, '_adapter_journal', None) is not None:
            state['adapter_journal'] = deepcopy(self._adapter_journal)
            state['actor_vitality'] = {p: person.vitality for p, person in self.people.items()}
        if self.facts is not None:
            state['fact_model'] = self.facts.export_state()
        if self.sim.decision_provider is not None:
            state['decision_provider'] = provider_snapshot(self.sim.decision_provider)
        if self.planning is not None: state['goal_planning'] = self.planning.snapshot()
        return state

    def restore_state(self, state):
        try:
            return self._restore_state(state)
        except (TypeError, KeyError, AttributeError, IndexError, OverflowError) as exc:
            raise ValueError('Malformed runtime checkpoint') from exc

    def _restore_state(self, state):
        expected = set(self.export_state())
        if not isinstance(state, dict) or set(state) != expected or set(state['people']) != set(self.people):
            raise ValueError('Invalid runtime checkpoint state')
        staged_provider = (None if self.sim.decision_provider is None else
                           staged_provider_restore(self.sim.decision_provider, state['decision_provider']))
        integer(state['now'], 'checkpoint minute', minute_for(self.calendar, self.calendar['start_date'], self.contract['start_time']), self.end)
        if state['end'] != self.end or type(state['done']) is not bool:
            raise ValueError('Invalid runtime end/state')
        if state['done'] and state['now'] != self.end:
            raise ValueError('Invalid completed checkpoint before runtime end')
        if set(state['energy']) != set(self.people) or set(state['observed']) != set(self.people):
            raise ValueError('Invalid actor checkpoint catalogs')
        for p, value in state['people'].items():
            person = self.people[p]
            if set(value) != {'zone', 'knowledge', 'inventory', 'stamina', 'stress'} or value['zone'] not in set(self.world.zones)|{'移動中'}:
                raise ValueError('Invalid subject state')
            if (not isinstance(value['knowledge'], list) or any(not isinstance(k, str) for k in value['knowledge'])
                    or len(value['knowledge']) != len(set(value['knowledge']))):
                raise ValueError('Invalid subject knowledge')
            if not isinstance(value['inventory'], dict):
                raise ValueError('Invalid inventory')
            for qty in value['inventory'].values():
                integer(qty, 'inventory quantity')
            if any(type(value[k]) not in (int, float) or not math.isfinite(value[k]) for k in ('stamina', 'stress')):
                raise ValueError('Invalid subject numeric state')
            energy = state['energy'][p]
            if not isinstance(energy, list) or len(energy) != 2:
                raise ValueError('Invalid exact stamina')
            integer(energy[0], 'energy numerator', 0, 10**15)
            integer(energy[1], 'energy denominator', 1, 10**15)
            if value['stamina'] != float(Fraction(*energy)) or not 0 <= value['stamina'] <= person.stamina_max:
                raise ValueError('Stamina/checkpoint mismatch')
        ledger = state['ledger']
        if not isinstance(ledger, dict) or set(ledger) != {'tasks', 'materials', 'completion_events'} or set(ledger['tasks']) != set(self.contract['tasks']):
            raise ValueError('Invalid work ledger')
        if set(ledger['materials']) != set(self.contract['materials']):
            raise ValueError('Invalid material catalog')
        for qty in ledger['materials'].values():
            integer(qty, 'remaining material')
        for task_id, work in ledger['tasks'].items():
            spec = self.contract['tasks'][task_id]
            if not isinstance(work, dict) or set(work) != {'effort_minutes', 'units', 'contributions', 'reserved_units'}:
                raise ValueError('Invalid task ledger state')
            integer(work['effort_minutes'], 'work effort', 0, spec['units']*spec['minutes_per_unit'])
            integer(work['units'], 'work units', 0, spec['units'])
            integer(work['reserved_units'], 'reserved units', 0, spec['units'])
            if work['units'] != work['effort_minutes']//spec['minutes_per_unit'] or not set(work['contributions']) <= set(self.people):
                raise ValueError('Inconsistent work progress')
            for qty in work['contributions'].values():
                integer(qty, 'actor contribution')
            if sum(work['contributions'].values()) != work['units']:
                raise ValueError('Inconsistent work contributions')
        parsed_registry = PromiseRegistry.from_state(state['promises'])
        if parsed_registry.actors != frozenset(self.people) or parsed_registry.tasks != frozenset(self.contract['tasks']) or parsed_registry.places != frozenset(self.world.zones):
            raise ValueError('Promise checkpoint catalogs differ')
        if not isinstance(state['rows'], list) or any(not isinstance(row, dict) for row in state['rows']):
            raise ValueError('Invalid event collection')
        for field in ('known_terms', 'known_tasks', 'knowledge'):
            if not isinstance(state[field], dict) or set(state[field]) != set(self.people):
                raise ValueError('Invalid knowledge actor catalog')
        for field in ('busy', 'dependencies', 'appraisals', 'facilities'):
            if not isinstance(state[field], dict):
                raise ValueError('Invalid runtime state collection')
        if not set(state['busy']) <= set(self.people) or set(state['facilities']) != set(self.facilities):
            raise ValueError('Invalid active actor/facility catalog')
        if any(type(v) is not bool for v in state['facilities'].values()):
            raise ValueError('Invalid facility state')
        if not isinstance(state['processed'], (list, tuple)) or any(not isinstance(x, str) for x in state['processed']) or len(state['processed']) != len(set(state['processed'])):
            raise ValueError('Invalid processed identities')
        if not isinstance(state['pending_read'], list) or not isinstance(ledger['completion_events'], list):
            raise ValueError('Invalid event/read collection')
        staged_journal = None
        adapter_commands = {}
        if getattr(self, '_adapter_journal', None) is not None:
            staged_journal, staged_vitality, adapter_commands = self._validate_adapter_journal(state)
        command_ids = {c['id'] for c in self.commands} | set(adapter_commands)
        if self.facts is not None:
            command_ids = self.facts.command_ids(self.commands) | set(adapter_commands)
        for actor, action in state['busy'].items():
            if not isinstance(action, dict) or action.get('id') not in command_ids or action.get('actor') != actor:
                raise ValueError('Invalid pending action identity')
            integer(action['start'], 'pending start', 0, state['now'])
            integer(action['finish'], 'pending finish', state['now'], self.end)
            integer(action['last_progress'], 'pending progress', action['start'], state['now'])
            if action['finish'] <= state['now']:
                raise ValueError('Pending action already finished')
        for actor in self.people:
            if not isinstance(state['observed'][actor], (list, tuple)) or any(not isinstance(x, str) for x in state['observed'][actor]) or len(state['observed'][actor]) != len(set(state['observed'][actor])):
                raise ValueError('Invalid observed identities')
            if not isinstance(state['known_terms'][actor], dict) or not isinstance(state['known_tasks'][actor], dict) or not isinstance(state['knowledge'][actor], list):
                raise ValueError('Invalid knowledge state')
            if not set(state['known_tasks'][actor]) <= set(self.contract['tasks']):
                raise ValueError('Unknown work knowledge')
        ids = [r.get('id') for r in state['rows']]
        if len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i for i in ids):
            raise ValueError('Duplicate/missing event identity')
        if any(type(r.get('absolute_minute')) is not int or r['absolute_minute'] > state['now'] for r in state['rows']):
            raise ValueError('Invalid event time')
        validate_observation_state(state['observations'], self.contract.get('observers', []),
                                   self.calendar, state['rows'], state['now'])
        def tuples(v):
            return tuple(tuples(i) for i in v) if isinstance(v, (tuple, list)) else v
        rng = tuples(state['rng'])
        trial_rng = random.Random()
        trial_rng.setstate(rng)
        relations = Relations(state['relations'], target_resolver=self.world.relations._target_resolver,
                              affinity_cap_resolver=self.world.relations._affinity_cap_resolver)
        if self.facts is not None:
            self.facts.validate_state(state['fact_model'], state['rows'], state['now'], self.commands)
        if self.planning is not None: self.planning.validate_snapshot(state['goal_planning'],state['rows'],state['now'])
        # Apply to the existing real subjects only after every validation passed.
        self._observation_buffer = None
        for p, value in state['people'].items():
            person = self.people[p]
            person.zone = value['zone']; person.knowledge = set(value['knowledge'])
            person.inventory = dict(value['inventory']); person.stamina = value['stamina']; person.stress = value['stress']
        self.world.relations = relations
        self.registry = parsed_registry
        for field in ('now', 'energy', 'ledger', 'busy', 'known_terms', 'known_tasks', 'knowledge', 'pending_read',
                      'dependencies', 'appraisals', 'rows', 'observations', 'done', 'facilities'):
            setattr(self, field, deepcopy(state[field]))
        self.processed = set(state['processed'])
        self.observed = {p: set(v) for p, v in state['observed'].items()}
        if self.facts is not None:
            self.facts.state = deepcopy(state['fact_model'])
        self.sim.rng.setstate(rng)
        if staged_journal is not None:
            self._adapter_journal = staged_journal
            for p, vitality in staged_vitality.items():
                self.people[p].vitality = vitality
        if staged_provider is not None:
            self.sim.decision_provider = staged_provider
        if self.planning is not None: self.planning.restore(state['goal_planning'])

    def inputs(self):
        files = {'world': hashlib.sha256(self.world.source.read_bytes()).hexdigest(),
                 'contract': hashlib.sha256((self.world.source.parent/self.world.slot_execution['contract']).read_bytes()).hexdigest()}
        for name in ('execution-version.json',):
            path = self.world.source.parent/name
            if path.exists():
                files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        result = {'files': files, 'contract': self.contract, 'calendar': self.calendar, 'seed': self.sim.seed,
                'initial_subjects': self.initial, 'personality': {p: self.people[p].traits for p in self.people},
                'subject_sources': getattr(self.world, 'calendar_subject_sources', None),
                'input_provenance': getattr(self.world, 'calendar_input_provenance', None),
                'initial_relations': self.initial_relations}
        if getattr(self, '_adapter_journal', None) is not None:
            result['adapter_journal_identity'] = deepcopy(self._adapter_journal['identity'])
        if self.sim.execution_plan is not None:
            result['execution_plan'] = self.sim.execution_plan.to_dict()
            result['execution_plan_hash'] = self.sim.execution_plan.sha256
            semantic_contract = {k: v for k, v in self.contract.items() if k != 'calendar'}
            axis = {k: self.calendar[k] for k in ('start_date', 'end_date', 'timezone', 'origin', 'duration_minutes', 'events')}
            result['time_contract_hashes'] = run_spec_hashes(
                {'axis': axis, 'actions': semantic_contract, 'choices': self.commands},
                self.contract['calendar'])
        if self.sim.decision_provider is not None:
            result['decision_provider_identity'] = provider_snapshot(self.sim.decision_provider)['identity']
        return result

    def _outputs(self):
        header = {'kind': 'header', 'contract': 'calendar_promises_v2', 'schema_version': 2,
                  'calendar': self.calendar, 'seed': self.sim.seed, 'initial_subjects': self.initial,
                  'assumptions': self.contract['assumptions'], 'api_calls': 0, 'gpu': False}
        if self.planning is not None:
            header.update(protagonist=self.world.protagonist, planning={'config':self.world.planner.config,
                          'domain':'native_calendar_tasks','selection_mechanism':'bounded_goal_search'})
        if self.sim.execution_plan is not None:
            header['execution_plan'] = self.sim.execution_plan.to_dict()
            header['execution_plan_hash'] = self.sim.execution_plan.sha256
            header['verified_execution'] = self.sim.verified_execution
            header['time_contract_hashes'] = self.inputs()['time_contract_hashes']
        if self.sim.decision_provider is not None:
            header['decision_provider_identity'] = provider_snapshot(self.sim.decision_provider)['identity']
        data = b''.join(canonical(row)+b'\n' for row in [header]+self.rows)
        atomic_bytes(self.store.out/'layers.jsonl', data)
        usage = {p: {'work': 0, 'move': 0, 'communication': 0, 'wait': 0} for p in self.people}
        for row in self.rows:
            if row['verb'] in ('action_completed', 'work_interrupted'):
                choice = row['details']['choice']
                kind = choice['verb'] if choice['verb'] in ('work', 'move', 'wait') else 'communication'
                usage[row['subject']][kind] += row['details']['duration_minutes']
        summary = {'contract': 'calendar_promises_v2', 'now': self.now, 'done': self.done,
                   'observations': self.observations, 'tasks': self.ledger['tasks'], 'materials': self.ledger['materials'],
                   'promises': self.registry.export_state(), 'people': self._people(),
                   'knowledge': self.knowledge, 'relations': self.world.relations.snapshot(),
                   'dependencies': self.dependencies, 'pending_read': self.pending_read,
                   'used_minutes': usage, 'roles': self.contract['roles'],
                   'remaining_work': {t: {'units': s['units']-self.ledger['tasks'][t]['units'],
                                           'minutes': s['units']*s['minutes_per_unit']-self.ledger['tasks'][t]['effort_minutes']}
                                      for t, s in self.contract['tasks'].items()},
                   'unresolved_actions': self.busy, 'api_calls': 0, 'gpu': False}
        if self.facts is not None:
            summary['fact_model'] = self.facts.export_state()
        if self.planning is not None: summary['goal_planning'] = self.planning.snapshot()
        atomic_bytes(self.store.out/'summary.json', canonical(summary))
        from engine.run_record import commit_run_record
        commit_run_record(self.sim)

    def run(self, *, until=None, resume=False):
        if self.planning is not None: self.enable_adapter_journal(self.planning.identity)
        self.sim.verify_execution(self.time_kernel.axis)
        project = self.world.source.parent.resolve()
        output = self.sim.out_dir.resolve()
        if output == project or project in output.parents:
            raise ValueError('Run output must be outside the input project')
        boundary = self.end if until is None else integer(until, 'stop minute', self.now, self.end)
        expected = compatibility(self.inputs(), self.commands)
        self.store = CheckpointStore(self.sim.out_dir, expected)
        with self.store.session():
            return self._run_locked(boundary, resume)

    def _next_boundary(self, now, boundary):
        points = self._points()
        future = [x for x in points if x > self.now]
        current_needed = any(c['at'] == self.now and c['id'] not in self.processed for c in self.commands)
        current_needed |= any(e['at'] == self.now and e['id'] not in self.processed for e in self._facility_events)
        current_needed |= any(e['minute'] == self.now and 'scheduled:'+e['id'] not in self.processed for e in self.calendar['events'])
        if self.facts is not None:
            current_needed |= self.now in self.facts.points()
        if self.planning is not None:
            current_needed |= any(self.planning.last_choice.get(who) != self.now for who in self.world.planner.config['actors']
                                  if who not in self.busy)
        if current_needed or ('calendar:'+str(self.now) not in {r['id'] for r in self.rows} and self.now in self.calendar['checkpoints']):
            at = self.now
        else:
            at = min(future+[boundary]) if future else boundary
        if at > boundary:
            at = boundary
        return at

    def _run_locked(self, boundary, resume):
        if resume:
            state = self.store.load()
            self.restore_state(state)
            if boundary < self.now:
                raise ValueError('Cannot resume backwards')
        else:
            self.store.initialize(self.export_state())
        if self.done:
            self._outputs()
            return self.store.out/'layers.jsonl'
        self.time_kernel.run(self._next_boundary, self._boundary_plan(), until=boundary,
                             before_boundary=self.export_state,
                             after_boundary=lambda: self.store.save(self.export_state()),
                             on_error=self.restore_state)
        self._outputs()
        return self.store.out/'layers.jsonl'
