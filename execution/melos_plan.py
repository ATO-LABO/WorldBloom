"""Bounded, opt-in plan admission for the approved finite Melos session.

No product registration, independent runner, selection, or narrative inference.
A caller supplies every initialized seed slot; admission never executes it.
"""
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import hashlib
import inspect
from pathlib import Path
import re

from engine.execution_plan import ExecutionPlan, digest
from execution.melos_session import MelosSession
from gapengine.melos_choice_provider import MelosChoiceProvider


def capabilities():
    return {'profile': 'melos_approved_ninegene_v1', 'product_active': True,
            'product_api_connected': True, 'run_record_connected': True,
            'plan_binding': True, 'saved_session': True,
            'independent_batch_runner': True, 'selection': False}


def _catalog(value):
    if type(value) is not dict or not value or any(
            type(k) is not str or not k or type(v) is not str or
            re.fullmatch('[0-9a-f]{64}', v) is None for k, v in value.items()):
        raise ValueError('Explicit SHA-256 catalog required')
    return deepcopy(value)


def _file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _logical_class(cls):
    from execution.melos_factory import source_class_identity
    return source_class_identity(cls)


def _initial_hash(session):
    """Hash exact initial state after verifying canonical source/journal binding.

    The adapter canonicalizes only the byte-approved Trial class key at initial
    construction. No snapshot or arbitrary path/label is rewritten here.
    """
    snapshot = session.export_state()
    stage = snapshot['state']['stage']['state']
    identity = stage['identity']
    journal = stage['runtime']['adapter_journal']
    if journal['identity']['config_hash'] != digest(identity):
        raise ValueError('Actual stage/journal binding differs')
    sources = identity['trial_sources']
    for cls in type(session.trial).__mro__:
        if cls is object:
            continue
        if sources.get(_logical_class(cls)) != _file(inspect.getsourcefile(cls)):
            raise ValueError('Actual Trial implementation binding differs')
    return digest(snapshot)


def _closure(session):
    root = Path(__file__).resolve().parents[1]
    paths = {str(p.relative_to(root)): p for package in ('engine', 'gapengine', 'execution')
             for p in sorted((root / package).rglob('*.py'))}
    for cls in type(session.trial).__mro__:
        if cls is not object:
            path = Path(inspect.getsourcefile(cls))
            paths['trial:'+_logical_class(cls)] = path
    # External Trial helpers belong to its explicit source closure too.
    folder = Path(inspect.getsourcefile(type(session.trial))).parent
    for path in sorted(folder.glob('*.py')):
        paths['external:'+path.name] = path
    return {name: _file(path) for name, path in paths.items()}


def _data(value):
    if isinstance(value, dict):
        return {k: _data(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_data(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_data(v) for v in value]
    if isinstance(value, Path):
        return str(value.resolve())
    if is_dataclass(value):
        return _data(asdict(value))
    if hasattr(value, '__dict__'):
        return {'class': type(value).__module__+'.'+type(value).__qualname__,
                'fields': _data(vars(value))}
    return deepcopy(value)


def _effective(trial):
    runtime_inputs = deepcopy(trial.rt.inputs())
    runtime_inputs.pop('adapter_journal_identity', None)
    runtime_state = trial.rt.export_state()
    runtime_state.pop('adapter_journal', None)
    runtime_state.pop('actor_vitality', None)
    return _data({'scenario': trial.scenario, 'parameters': trial.p, 'goals': trial.goals,
                  'world': vars(trial.rt.world), 'contract': trial.rt.contract,
                  'axis': asdict(trial.rt.time_kernel.axis),
                  'inputs': runtime_inputs, 'initial_runtime': runtime_state,
                  'external': {name: getattr(trial, name) for name in
                      ('state', 'info', 'evidence', 'count', 'decisions', 'serial', 'notes')}})


def _descriptor(session):
    if type(session) is not MelosSession or type(session.stage.provider) is not MelosChoiceProvider:
        raise ValueError('Exact approved finite session/provider required')
    t = session.trial; rt = session.rt
    inputs = _catalog(session.stage.identity['inputs'])
    actual = {p.relative_to(rt.world.source.parent).as_posix(): _file(p)
              for p in sorted(rt.world.source.parent.rglob('*.yaml'))}
    if inputs != actual:
        raise ValueError('Input catalog differs from actual project YAML files')
    sources = _catalog(session.stage.identity['sources'])
    folder = Path(inspect.getsourcefile(type(t))).parent
    for name, expected in sources.items():
        path = Path(inspect.getsourcefile(MelosSession)) if name == 'melos_session' else folder / name
        if Path(name).is_absolute() or '..' in Path(name).parts or not path.is_file() or _file(path) != expected:
            raise ValueError('Declared external source differs from actual file')
    if t.reference or t.arm != 'baseline' or t.seed != session.stage.provider.config.seed or rt.sim.seed != t.seed:
        raise ValueError('Unapproved baseline/seed binding')
    # Reconstruct from the actual approved file-backed constructor. A matching
    # caller catalog and self-consistent stage do not authorize an overridden
    # scenario, parameters, goals, world, or initial state.
    pristine = type(t)(deepcopy(t.genome), t.seed)
    if _effective(t) != _effective(pristine):
        raise ValueError('Effective trial differs from pristine approved inputs')
    expected_provider = MelosChoiceProvider(t.genome, t.seed).checkpoint_identity()
    if session.stage.provider.checkpoint_identity() != expected_provider:
        raise ValueError('Genome/CFG/focus actor/RNG provider differs')
    stage_config = digest({'scenario': t.scenario, 'parameters': t.p, 'goals': t.goals,
                          'seed': t.seed, 'arm': t.arm, 'reference': t.reference,
                          'genome': t.genome.to_dict(), 'contract': rt.contract})
    if stage_config != session.stage.identity['config']:
        raise ValueError('Live trial differs from declared stage configuration')
    runtime_inputs = deepcopy(rt.inputs())
    runtime_inputs.pop('seed')
    runtime_inputs.pop('adapter_journal_identity', None)
    axis = asdict(rt.time_kernel.axis)
    if axis['unit'] != 'minute' or rt.end != axis['end']:
        raise ValueError('Approved minute axis required')
    return {'inputs': inputs, 'sources': sources, 'closure': _closure(session),
            'scenario': deepcopy(t.scenario), 'parameters': deepcopy(t.p),
            'goals': deepcopy(t.goals), 'genome': t.genome.to_dict(),
            'runtime_inputs': runtime_inputs, 'axis': axis,
            'world': _data({'zones': deepcopy(rt.world.zones),
                      'routes': {k: [asdict(r) for r in v] for k, v in rt.world.routes.items()},
                      'movement': deepcopy(rt.world.movement),
                      'stamina': deepcopy(rt.world.stamina),
                      'vitality': deepcopy(rt.world.vitality),
                      'slot_contract': deepcopy(rt.world.slot_contract),
                      'slot_execution': deepcopy(rt.world.slot_execution),
                      'scheduled_events': deepcopy(rt.world.scheduled_events)})}


class MelosPlanBinding:
    """Freeze an explicit seed->pristine session catalog before any execution."""
    def __init__(self, sessions):
        if type(sessions) is not dict or not sessions or len(sessions) > 10000 or any(
                type(s) is not int or s < 0 for s in sessions):
            raise ValueError('Explicit nonnegative seed slots required')
        descriptors = {}; self._slots = {}
        for seed, session in sessions.items():
            descriptors[seed] = _descriptor(session)
            if (session.trial.seed != seed or session.phase != 'choose_step' or session.step_index != 0
                    or session.finished or session.rt.now != session.rt.time_kernel.axis.start
                    or any(session.trial.count.values()) or session.trial.decisions):
                raise ValueError('Each seed needs its own pristine initialized session')
            self._slots[seed] = {'provider': session.stage.provider.checkpoint_identity(),
                                 'initial_hash': _initial_hash(session)}
        self._descriptor = deepcopy(next(iter(descriptors.values())))
        if any(value != self._descriptor for value in descriptors.values()):
            raise ValueError('All slots must share fixed inputs/world/genome/time/source')
        axis = self._descriptor['axis']
        stop = {'scenario': 'approved_finite_session', 'decision_steps': 40,
                'calendar_horizon': axis['end'], 'early_scenario_terminal_allowed': True,
                'background_completion_not_implied': True}
        identity = lambda name, config: {'id': name, 'version': 1,
            'source_hash': digest(self._descriptor['closure']), 'config_hash': digest(config)}
        self._plan = ExecutionPlan({'schema_version': 1, 'execution_kind': 'independent_trials',
            'sampler': identity('melos_finite_session_v1', self._descriptor),
            'provider': identity('melos_predeclared_seed_providers_v1',
                                 {str(k): v for k, v in self._slots.items()}),
            'classification': identity('melos_physical_evidence_v1', {'report_minutes': 2,
                'wedding_minutes': 120, 'retain_failures': True, 'retain_unresolved': True}),
            'predeclared_seeds': list(sessions), 'planned_trials': len(sessions),
            'selection': dict.fromkeys(('fitness_selection', 'breeding', 'keep_filter', 'canon_feedback'), False),
            'observation': {'unit': 'minute', 'start': axis['start'], 'end': axis['end'],
                            'stop_contract_hash': digest(stop)}})

    @property
    def plan(self):
        return ExecutionPlan(self._plan.to_dict())

    def manifest(self):
        """Detached catalog proves every seed entry against plan provider hash."""
        return {'execution_plan': self._plan.to_dict(),
                'execution_plan_hash': self._plan.sha256,
                'provider_catalog': {str(seed): deepcopy(slot) for seed, slot in self._slots.items()}}

    def seed_admission(self, seed):
        """Declared per-seed evidence; caller must successfully bind before use."""
        if type(seed) is not int or seed not in self._slots:
            raise ValueError('Undeclared seed')
        slot = self._slots[seed]
        return deepcopy({'plan_hash': self._plan.sha256, 'seed': seed,
                         'aggregate_provider': self._plan.to_dict()['provider'],
                         'actual_provider': slot['provider'],
                         'initial_session_hash': slot['initial_hash'],
                         'provider_catalog': self.manifest()['provider_catalog']})

    def admit(self, plan):
        if type(plan) is not ExecutionPlan or plan.to_dict() != self._plan.to_dict():
            raise ValueError('Plan differs from frozen Melos admission')
        return self.plan

    def bind_session(self, session, seed, *, plan=None):
        if plan is not None:
            self.admit(plan)
        if type(seed) is not int or seed not in self._slots or session.trial.seed != seed:
            raise ValueError('Undeclared or mismatched seed')
        if _descriptor(session) != self._descriptor:
            raise ValueError('Input/world/time/genome/source profile mismatch')
        expected = self._slots[seed]
        if (session.stage.provider.checkpoint_identity() != expected['provider'] or
                _initial_hash(session) != expected['initial_hash']):
            raise ValueError('Initial session/provider identity mismatch')
        return session


def classify_session(session, *, execution_failure=None):
    """Read-only physical evidence. Completion is never inferred from agreement.

    This is an evidence projection, not a checkpoint validator or fitness score.
    Call bind_session before executing the session; checkpoint restore owns its
    complete causal validation. Missing or inconsistent evidence stays unresolved.
    """
    rt = session.rt; rows = rt.rows; state = session.trial.state
    completed = [r for r in rows if r['verb'] == 'action_completed'
                 and r['subject'] == 'Melos' and r['result'] == 'applied']
    arrivals = [r['id'] for r in completed if r['details']['choice'].get('verb') == 'move'
                and r['details']['choice'].get('to') == 'court'
                and r['details'].get('arrived_at') == r['absolute_minute']]
    def work(task, minutes):
        actions = [r for r in completed if r['details']['choice'].get('verb') == 'work'
                   and r['details']['choice'].get('task_id') == task
                   and r['details'].get('duration_minutes') == minutes]
        units = [r for r in rows if r['verb'] == 'work_unit_completed' and r['subject'] == 'Melos' and r.get('result') == 'applied'
                 and r['details'].get('task_id') == task]
        ledger = rt.ledger['tasks'].get(task, {})
        events = [e for e in rt.ledger['completion_events'] if e['task_id'] == task and e['actor'] == 'Melos']
        ids = {e['id'] for e in events}
        linked = [u for u in units if u['id'] in ids and any(
            u['details'].get('caused_by') == a['details'].get('caused_by') and u['details'].get('caused_by')
            and u['absolute_minute'] == a['absolute_minute'] for a in actions)]
        ok = bool(actions and linked and ledger.get('units', 0) >= 1
                  and ledger.get('contributions', {}).get('Melos', 0) >= 1)
        return {'completed': ok, 'action_ids': [r['id'] for r in actions],
                'work_unit_ids': [r['id'] for r in linked]}
    report = work('return', 2); wedding = work('wedding', 120)
    terminal = [r for r in rows if r['verb'] == 'observation_result']
    outcome = state.get('outcome')
    resolved = (session.finished and len(terminal) == 1 and terminal[0]['details'].get('state') == state
                and outcome is not None and 'incomplete' not in outcome
                and (not state.get('wedding') or wedding['completed'])
                and (state.get('returned_at') is None or report['completed']))
    result = {'status': 'execution_failure' if execution_failure is not None else 'observed' if resolved else 'unresolved',
              'scenario_outcome': outcome, 'physical_court_arrival': bool(arrivals),
              'court_arrival_ids': arrivals, 'fixed_two_minute_report': report,
              'completed_120_minute_wedding': wedding, 'scenario_finished': session.finished,
              'calendar_horizon_reached': rt.now == rt.end, 'observed_minute': rt.now,
              'calendar_horizon': rt.end, 'remaining_physical_actors': sorted(rt.busy),
              'remaining_background_actors': sorted(a for a in rt.busy if a != 'Melos'),
              'product_active': True}
    if execution_failure is not None:
        if not isinstance(execution_failure, Exception):
            raise ValueError('Explicit execution exception required')
        result['error_type'] = type(execution_failure).__name__
        result['error'] = str(execution_failure)
    return deepcopy(result)
