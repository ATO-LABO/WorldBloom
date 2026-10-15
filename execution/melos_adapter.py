"""Resumable physical-action stage for the approved Melos Trial.

This is deliberately not a scene engine or a complete run_choices dispatcher.
Callers own explicit scenario branch continuations. All physical execution uses
Trial.rt (CalendarRuntime) and its existing TimeActionKernel. No chooser is
called by this stage, including on restore.
"""
from copy import deepcopy
import hashlib
import inspect
import json
import math
from pathlib import Path
from functools import wraps
import re

from engine.calendar_runtime import CalendarRuntime
from engine.provider_boundary import encode_history, decode_history, encode_rng, decode_rng
from engine.decision_provider import provider_snapshot, staged_provider_restore


def _json(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False))


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Invalid '+name)


def _atomic(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        before = self.export_state()
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            self.restore_state(before)
            raise
    return guarded


class MelosActionStage:
    """Own one admitted physical action and its ordered deferred effects.

    ``trial`` must already be initialized from explicitly supplied inputs.
    ``input_hashes`` identifies those inputs; ``source_hashes`` identifies the
    approved Trial/policy source. Neither accepts paths in place of SHA-256s.
    The caller must not run Trial.run/act/advance concurrently with this stage.
    ``continuation`` is a persisted JSON payload for the future scenario
    dispatcher, returned unchanged by finish_action(). It is never executed.
    """
    FIELDS = ('state', 'info', 'evidence', 'count', 'decisions', 'serial', 'notes')
    PHASES = ('idle', 'physical', 'cost', 'completion', 'recovery', 'branch')

    def __init__(self, trial, *, input_hashes, source_hashes, choice_provider=None):
        if not isinstance(trial.rt, CalendarRuntime):
            raise ValueError('An existing CalendarRuntime is required')
        if trial.rt.store is not None or trial.rt.sim.decision_provider is not None:
            raise ValueError('Standalone stage requires an unfrozen explicit runtime')
        for catalog in (input_hashes, source_hashes):
            if not isinstance(catalog, dict) or not catalog or any(
                type(k) is not str or not k or type(v) is not str or
                re.fullmatch('[0-9a-f]{64}', v) is None for k, v in catalog.items()):
                raise ValueError('Explicit source/input SHA-256 identities required')
        if choice_provider is not None and (hasattr(trial, 'rng') or hasattr(trial, 'policy_history')):
            raise ValueError('Choice RNG/history must have one explicit owner')
        self.trial = trial
        self.provider = choice_provider
        self.phase = 'idle'
        self.pending = None
        from execution.melos_factory import source_class_identity
        closure = {source_class_identity(cls):
                   hashlib.sha256(Path(inspect.getsourcefile(cls)).read_bytes()).hexdigest()
                   for cls in type(trial).__mro__ if cls is not object}
        closure['execution.melos_factory.source_class_identity'] = hashlib.sha256(
            Path(inspect.getsourcefile(source_class_identity)).read_bytes()).hexdigest()
        for cls in (type(trial.rt), type(trial.rt.time_kernel), type(trial.rt.lifecycle)):
            closure[cls.__module__+'.'+cls.__qualname__] = hashlib.sha256(Path(inspect.getsourcefile(cls)).read_bytes()).hexdigest()
        self.identity = _json({'schema': 1, 'adapter': 'melos_action_stage_v1',
            'implementation': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'trial_sources': closure, 'inputs': input_hashes, 'sources': source_hashes,
            'config': _hash({'scenario': trial.scenario, 'parameters': trial.p,
                             'goals': trial.goals, 'seed': trial.seed,
                             'arm': trial.arm, 'reference': trial.reference,
                             'genome': trial.genome.to_dict(), 'contract': trial.rt.contract})})
        self.rt.enable_adapter_journal({'adapter': 'melos_action_stage_v1',
            'schema_version': 1, 'source_hash': self.identity['implementation'],
            'config_hash': _hash(self.identity)})

    @property
    def rt(self):
        return self.trial.rt

    def _start(self, command, *, candidate_id, effective_duration=None):
        return self.rt.admit_adapter_command(command, proposal_id=command['id'],
            candidate_id=candidate_id, effective_duration=effective_duration)

    def _environment(self):
        # The approved environment starts Philostratus directly. Temporarily
        # intercept that call; core admission itself must see the real _start.
        original = self.rt._start
        def admitted(command):
            self.rt._start = original
            try:
                return self._start(command, candidate_id='disciple-walk')
            finally:
                self.rt._start = admitted
        self.rt._start = admitted
        try:
            self.trial.environment()
        finally:
            del self.rt.__dict__['_start']

    @_atomic
    def start_action(self, candidate, *, continuation=None):
        if self.phase != 'idle' or 'Melos' in self.rt.busy:
            raise ValueError('Finish the pending action first')
        c = _json(candidate)
        if not isinstance(c, dict) or not {'id', 'kind', 'minutes'} <= set(c):
            raise ValueError('Incomplete candidate')
        if type(c['id']) is not str or not c['id'] or c['kind'] not in ('move', 'work', 'wait'):
            raise ValueError('Invalid candidate identity/kind')
        _integer(c['minutes'], 'candidate duration', 1)
        for key in ('cost', 'recover'):
            v = c.get(key, 0)
            if type(v) not in (int, float) or not math.isfinite(v) or v < 0:
                raise ValueError('Invalid candidate '+key)
        continuation = _json(continuation)
        if self.rt.now+c['minutes'] > self.rt.end:
            self.trial.state['outcome'] = 'observation_incomplete'
            self.trial.event('observation_horizon_stop', 'Melos', ['Melos'],
                candidate=c['id'], requested_minutes=c['minutes'],
                available_minutes=self.rt.end-self.rt.now, completed=False)
            return False
        collapse = c['kind'] == 'move' and c.get('cost', 0) > self.rt.people['Melos'].stamina
        self.trial.serial += 1
        ident = 'melos-action-'+str(self.trial.serial)
        if collapse:
            self.trial.serial += 1
            ident = 'wait-collapse-'+str(self.trial.serial)
        command = {'id': ident, 'actor': 'Melos', 'at': self.rt.now,
                   'verb': 'wait' if collapse else c['kind']}
        if collapse:
            command['minutes'] = 30
        elif c['kind'] == 'move':
            command['to'] = c['to']
        elif c['kind'] == 'work':
            command.update(task_id=c['task'], minutes=c['minutes'])
        else:
            command['minutes'] = c['minutes']
        receipt = self._start(command, candidate_id=c['id'],
                    effective_duration=c['minutes'] if command['verb'] == 'move' else None)
        if 'Melos' not in self.rt.busy:
            raise ValueError('Core rejected action')
        self.pending = {'candidate': c, 'command': command, 'collapse': collapse,
                        'finish': self.rt.busy['Melos']['finish'],
                        'continuation': continuation, 'completion_id': None, 'receipt': receipt}
        self.phase = 'physical'
        return True

    @_atomic
    def advance(self, until):
        """Pause at any physical minute; effects are explicitly stepped later."""
        _integer(until, 'pause minute')
        if self.phase != 'physical' or not self.rt.now <= until <= self.pending['finish']:
            raise ValueError('Pause must lie within the pending physical action')
        while self.rt.now < until:
            points = [x for x in self.rt._points() if self.rt.now < x <= until]
            p = self.trial.p
            points += [x for x in (p['rain_at'], p['bridge_destroyed_at'],
                p['execution_preparation_at'], p['deadline'], p['deadline']+1)
                if self.rt.now < x <= until]
            self.rt._tick(min(points+[until]))
            self._environment()
        self._environment()
        if self.rt.now == self.pending['finish']:
            ident = self.pending['command']['id']
            completion = next((r for r in reversed(self.rt.rows)
                if r['verb'] == 'action_completed' and r['details']['choice']['id'] == ident), None)
            if completion is None or completion['result'] != 'applied':
                raise ValueError('Missing successful core completion')
            self.pending['completion_id'] = completion['id']
            self.phase = 'cost'

    @_atomic
    def step_effect(self):
        """Apply exactly one finite post-completion stage, never resampling."""
        t, p = self.trial, self.pending
        if self.phase not in ('cost', 'completion', 'recovery'):
            raise ValueError('No deferred effect ready')
        c = p['candidate']
        if p['collapse']:
            t.state['exhausted'] = True
            t.event('exhaustion', 'Melos', ['Melos'], failed_candidate=c['id'],
                    zone=self.rt.people['Melos'].zone, basis='A3')
            self.phase = 'branch'
        elif self.phase == 'cost':
            t.set_energy(self.rt.people['Melos'].stamina-c.get('cost', 0), c['id'])
            self.phase = 'completion'
        elif self.phase == 'completion':
            t.event('scenario_action_completed', 'Melos', ['Melos'],
                candidate=c['id'], core_completion_id=p['completion_id'],
                place=self.rt.people['Melos'].zone, duration=c['minutes'], energy_cost=c.get('cost', 0))
            self.phase = 'recovery'
        else:
            if c.get('recover'):
                t.set_energy(self.rt.people['Melos'].stamina+c['recover'], c['id']+' recovery')
            self.phase = 'branch'

    def finish_action(self):
        """Return persisted branch data only after physical and post effects."""
        if self.phase != 'branch':
            raise ValueError('Action effects are not complete')
        result = deepcopy(self.pending)
        self.phase, self.pending = 'idle', None
        return result

    def export_state(self):
        external = {k: deepcopy(getattr(self.trial, k)) for k in self.FIELDS}
        external['vitality'] = {a: p.vitality for a, p in self.rt.people.items()}
        external['rng'] = encode_rng(self.trial.rng) if hasattr(self.trial, 'rng') else None
        external['policy_history'] = encode_history(self.trial.policy_history) if hasattr(self.trial, 'policy_history') else None
        state = _json({'identity': self.identity, 'runtime': self.rt.export_state(),
            'external': external, 'phase': self.phase,
            'pending': self.pending,
            'provider': provider_snapshot(self.provider) if self.provider is not None else None})
        return {'state': state, 'sha256': _hash(state)}

    def restore_state(self, snapshot):
        """Validate a detached complete state before mutating the live Trial."""
        try:
            self._restore_state(snapshot)
        except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
            raise ValueError('Malformed Melos action checkpoint') from exc

    def _restore_state(self, snapshot):
        if not isinstance(snapshot, dict) or set(snapshot) != {'state', 'sha256'}:
            raise ValueError('Invalid checkpoint envelope')
        s = _json(snapshot['state'])
        if snapshot['sha256'] != _hash(s) or set(s) != set(self.export_state()['state']):
            raise ValueError('Corrupt checkpoint')
        if s['identity'] != self.identity or s['phase'] not in self.PHASES:
            raise ValueError('Checkpoint identity/phase differs')
        e = s['external']; actors = set(self.rt.people)
        if set(e) != set(self.FIELDS)|{'vitality', 'rng', 'policy_history'}:
            raise ValueError('Invalid external fields')
        for field in ('info', 'evidence', 'count', 'vitality'):
            if not isinstance(e[field], dict) or set(e[field]) != actors:
                raise ValueError('Invalid external actor catalog')
        _integer(e['serial'], 'event serial')
        for count in e['count'].values():
            _integer(count, 'choice count')
            if count > 32: raise ValueError('Choice budget exceeded')
        if set(e['state']) != set(self.trial.state):
            raise ValueError('Invalid scenario state keys')
        for key, value in self.trial.state.items():
            if type(value) is bool and type(e['state'][key]) is not bool:
                raise ValueError('Invalid scenario boolean')
        if any(v not in ('alive', 'dead', 'revived') for v in e['vitality'].values()):
            raise ValueError('Invalid vitality')
        if not isinstance(e['decisions'], list) or not isinstance(e['notes'], list):
            raise ValueError('Invalid external history')
        for actor, evidence in e['evidence'].items():
            if not isinstance(evidence, list) or any(not isinstance(x, dict) or
                type(x.get('at')) is not int or x['at'] > s['runtime']['now'] for x in evidence):
                raise ValueError('Invalid/future evidence')
        returned = e['state'].get('returned_at')
        if returned is not None and (type(returned) is not int or not 0 <= returned <= s['runtime']['now']):
            raise ValueError('Invalid return time')
        if e['state'].get('outcome') is not None and type(e['state']['outcome']) is not str:
            raise ValueError('Invalid outcome')
        rows = {r['id']: r for r in s['runtime']['rows']}
        for evidence in e['evidence'].values():
            for item in evidence:
                row = rows.get(item.get('event_id'))
                if row is None or item.get('verb') != row['verb'] or item['at'] != row['absolute_minute']:
                    raise ValueError('Evidence does not identify a real event')
        counted = {a: 0 for a in actors}
        for decision in e['decisions']:
            if not isinstance(decision, dict) or decision.get('actor') not in actors:
                raise ValueError('Invalid choice history')
            row = rows.get(decision.get('event_id'))
            if row is None or row['verb'] != 'subjective_choice' or row['subject'] != decision['actor'] or row['details']['selected'] != decision.get('selected') or row['absolute_minute'] != decision.get('at'):
                raise ValueError('Choice history does not identify a real choice')
            counted[decision['actor']] += 1
        if counted != e['count']:
            raise ValueError('Choice counts differ from history')
        # Both the core and dynamic-command validation occur on a detached copy.
        staged = deepcopy(self.trial)
        staged.rt.restore_state(s['runtime'])
        if any(e['vitality'][a] != staged.rt.people[a].vitality for a in actors):
            raise ValueError('Conflicting core/external vitality')
        pending = s['pending']
        if s['phase'] == 'idle':
            if pending is not None or 'Melos' in staged.rt.busy:
                raise ValueError('Idle stage has pending action')
        else:
            if not isinstance(pending, dict) or set(pending) != {'candidate', 'command', 'collapse', 'finish', 'continuation', 'completion_id', 'receipt'}:
                raise ValueError('Invalid pending continuation')
            if type(pending['collapse']) is not bool or pending['command'].get('actor') != 'Melos':
                raise ValueError('Unadmitted pending action')
            if not isinstance(pending['receipt'], dict) or pending['receipt'].get('command') != pending['command'] or pending['receipt'].get('candidate_id') != pending['candidate']['id']:
                raise ValueError('Pending admission receipt mismatch')
            _integer(pending['finish'], 'finish')
            candidate = pending['candidate']
            if not isinstance(candidate, dict) or not {'id', 'kind', 'minutes'} <= set(candidate) or candidate['kind'] not in ('move', 'work', 'wait') or type(candidate['id']) is not str or not candidate['id']:
                raise ValueError('Invalid pending candidate')
            _integer(candidate['minutes'], 'pending duration', 1)
            for key in ('cost', 'recover'):
                if type(candidate.get(key, 0)) not in (int, float) or not math.isfinite(candidate.get(key, 0)) or candidate.get(key, 0) < 0:
                    raise ValueError('Invalid pending exertion/recovery')
            expected_command = {'id': pending['command']['id'], 'actor': 'Melos',
                                'at': pending['command']['at'],
                                'verb': 'wait' if pending['collapse'] else candidate['kind']}
            if pending['collapse']:
                if candidate['kind'] != 'move': raise ValueError('Only unaffordable moves collapse')
                expected_command['minutes'] = 30
            elif candidate['kind'] == 'move': expected_command['to'] = candidate['to']
            elif candidate['kind'] == 'work': expected_command.update(task_id=candidate['task'], minutes=candidate['minutes'])
            else: expected_command['minutes'] = candidate['minutes']
            if expected_command != pending['command']:
                raise ValueError('Candidate and command differ')
            entry = s['runtime']['adapter_journal']['entries'].get(pending['receipt'].get('proposal_id'))
            if entry is None or entry['receipt'] != pending['receipt']:
                raise ValueError('Pending receipt differs from core admission')
            if pending['collapse'] and s['phase'] in ('completion', 'recovery'):
                raise ValueError('Invalid collapse continuation phase')
            ident = pending['command']['id']
            starts = [r for r in staged.rt.rows if r['verb'] == 'action_started' and r['details']['choice']['id'] == ident]
            if len(starts) != 1 or starts[0]['details']['finish_at'] != pending['finish'] or starts[0]['details']['choice'] != pending['command']:
                raise ValueError('Pending start/finish mismatch')
            if s['phase'] == 'physical':
                action = staged.rt.busy.get('Melos', {})
                if action.get('id') != ident or action.get('finish') != pending['finish'] or pending['completion_id'] is not None:
                    raise ValueError('Pending physical action mismatch')
            elif 'Melos' in staged.rt.busy or pending['finish'] != staged.rt.now or not any(
                r['id'] == pending['completion_id'] and r['verb'] == 'action_completed' and
                r['details']['choice']['id'] == ident and r['result'] == 'applied' for r in staged.rt.rows):
                raise ValueError('Post effect lacks matching completion')
        if pending is not None and s['phase'] != 'physical':
            # A continuation is meaningful only with its exact committed prefix
            # of effects. A checksum alone cannot detect a phase moved forward
            # (skipping cost) or backward (charging twice).
            completion_index = next(i for i, row in enumerate(staged.rt.rows)
                                    if row['id'] == pending['completion_id'])
            effects = [row for row in staged.rt.rows[completion_index+1:]
                       if row['subject'] == 'Melos' and row['verb'] in
                       ('energy_change', 'scenario_action_completed', 'exhaustion')]
            c = pending['candidate']
            if pending['collapse']:
                expected = ['exhaustion'] if s['phase'] == 'branch' else []
            else:
                expected = [] if s['phase'] == 'cost' else ['energy_change']
                if s['phase'] in ('recovery', 'branch'):
                    expected.append('scenario_action_completed')
                if s['phase'] == 'branch' and c.get('recover'):
                    expected.append('energy_change')
            if [row['verb'] for row in effects] != expected:
                raise ValueError('Post-effect phase differs from committed event prefix')
            last_energy = None
            for index, row in enumerate(effects):
                d = row['details']
                if row['absolute_minute'] != pending['finish'] or row['result'] != 'applied' or re.fullmatch(r'melos:[0-9]+', row['id']) is None:
                    raise ValueError('Invalid post-effect event identity/time')
                if int(row['id'].split(':')[1]) > e['serial']:
                    raise ValueError('Post-effect event exceeds scenario serial')
                if row['verb'] == 'energy_change':
                    recovery = index > 0
                    reason = c['id']+' recovery' if recovery else c['id']
                    delta = c.get('recover', 0) if recovery else -c.get('cost', 0)
                    before, after = d.get('before'), d.get('after')
                    if type(before) not in (int, float) or not math.isfinite(before) or not 0 <= before <= 100 or type(after) not in (int, float) or not math.isfinite(after):
                        raise ValueError('Invalid energy effect values')
                    if d.get('reason') != reason or after != round(max(0, min(100, before+delta)), 6) or (last_energy is not None and before != last_energy):
                        raise ValueError('Energy charge/recovery differs from candidate')
                    last_energy = after
                elif row['verb'] == 'scenario_action_completed':
                    if any(d.get(k) != value for k, value in {
                            'candidate': c['id'], 'core_completion_id': pending['completion_id'],
                            'place': staged.rt.people['Melos'].zone,
                            'duration': c['minutes'], 'energy_cost': c.get('cost', 0)}.items()):
                        raise ValueError('Scenario completion differs from physical action')
                elif d.get('failed_candidate') != c['id'] or d.get('zone') != staged.rt.people['Melos'].zone or not e['state']['exhausted']:
                    raise ValueError('Collapse effect differs from failed movement')
            if last_energy is not None and staged.rt.people['Melos'].stamina != last_energy:
                raise ValueError('Current energy differs from committed post effects')
        staged_provider = None
        if self.provider is not None:
            staged_provider = staged_provider_restore(self.provider, s['provider'])
        elif s['provider'] is not None:
            raise ValueError('Unexpected provider snapshot')
        if hasattr(staged, 'rng'):
            rng = decode_rng(e['rng'])
        elif e['rng'] is not None:
            raise ValueError('Unexpected external RNG')
        if hasattr(staged, 'policy_history'):
            history = decode_history(e['policy_history'])
        elif e['policy_history'] is not None:
            raise ValueError('Unexpected policy history')
        # Core has already accepted exactly this data. Preserve real object
        # identity (Simulation.subjects and Trial.rt), never swap in the copy.
        self.rt.restore_state(s['runtime'])
        for key in self.FIELDS: setattr(self.trial, key, deepcopy(e[key]))
        for actor, vitality in e['vitality'].items(): self.rt.people[actor].vitality = vitality
        if hasattr(staged, 'rng'): self.trial.rng.setstate(rng.getstate())
        if hasattr(staged, 'policy_history'): self.trial.policy_history = history
        self.phase, self.pending = s['phase'], deepcopy(pending)
        self.provider = staged_provider
