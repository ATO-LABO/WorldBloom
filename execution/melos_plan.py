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
from types import FunctionType, MethodType

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


def _data(value, *, captured=False):
    if isinstance(value, dict):
        return {k: _data(v, captured=captured) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_data(v, captured=captured) for v in value)
    if isinstance(value, (list, tuple)):
        return [_data(v, captured=captured) for v in value]
    if isinstance(value, Path):
        return str(value) if captured else str(value.resolve())
    if is_dataclass(value):
        return _data(asdict(value), captured=captured)
    if hasattr(value, '__dict__'):
        return {'class': type(value).__module__+'.'+type(value).__qualname__,
                'fields': _data(vars(value), captured=captured)}
    return deepcopy(value)


def _effective(trial, *, captured=False):
    runtime_inputs = deepcopy(trial.rt.inputs())
    runtime_inputs.pop('adapter_journal_identity', None)
    runtime_state = trial.rt.export_state()
    runtime_state.pop('adapter_journal', None)
    runtime_state.pop('actor_vitality', None)
    # The capture is an owned resource, not semantic configuration. Keep every
    # other world field, including live mutable configuration and diagnostic
    # labels. Captured labels are compared lexically, never resolved or read.
    world = dict(vars(trial.rt.world))
    if captured:
        world.pop('calendar_capture')
    effective = {'scenario': trial.scenario, 'parameters': trial.p, 'goals': trial.goals,
                  'world': world, 'contract': trial.rt.contract,
                  'axis': asdict(trial.rt.time_kernel.axis),
                  'inputs': runtime_inputs, 'initial_runtime': runtime_state,
                  'external': {name: getattr(trial, name) for name in
                      ('state', 'info', 'evidence', 'count', 'decisions', 'serial', 'notes')}}
    if captured:
        # These live compiled/transient values are not all in export_state().
        # Recompute them in the fresh factory too, rather than trusting hashes.
        effective['runtime_configuration'] = {name: getattr(trial.rt, name) for name in
            ('commands', 'task_windows', 'energy_rules', '_facility_events',
             '_observation_buffer', '_active_at_tick', '_generation_persistence_started',
             'registry', 'time_kernel')}
        effective['lifecycle_configuration'] = {name: getattr(trial.rt.lifecycle, name)
            for name in ('start_key', 'finish_key', 'actor_key')}
        effective['simulation_configuration'] = {name: getattr(trial.sim, name) for name in
            ('precedent', 'record_explanations', 'terminal_policy', '_initial_subject_specs',
             '_observed_ending_ids', '_instant_pending', '_decision_context', 'turn', 'day',
             'slot', '_last_fact_turn', '_previous_snapshot_layers', '_forced', '_fallback')}
        effective['verb_configuration'] = {name: value for name, value in
            vars(trial.sim.verb_engine).items() if name not in ('world', 'rng')}
    return _data(effective, captured=captured)


def _captured_links(rt, pristine):
    """Check owned aliases/callbacks without invoking a caller's replacement."""
    for live, fresh in ((rt, pristine), (rt.world, pristine.world),
                        (rt.sim, pristine.sim), (rt.time_kernel, pristine.time_kernel),
                        (rt.lifecycle, pristine.lifecycle), (rt.registry, pristine.registry),
                        (rt.sim.verb_engine, pristine.sim.verb_engine),
                        (rt.world.relations, pristine.world.relations)):
        if type(live) is not type(fresh):
            raise ValueError('Captured runtime implementation differs')
    if (set(rt.world.subjects) != set(rt.people)
            or any(rt.world.subjects[name] is not person for name, person in rt.people.items())
            or rt.sim.verb_engine.world is not rt.world
            or rt.sim.verb_engine.rng is not rt.sim.rng):
        raise ValueError('Captured execution owner differs')
    # These two approved closures each contain exactly the owning runtime or
    # world. Matching function labels/empty __dict__ cannot prove that binding.
    for live, fresh, owner in (
            (rt.lifecycle.pending, pristine.lifecycle.pending, rt),
            (rt.world.relations._affinity_cap_resolver,
             pristine.world.relations._affinity_cap_resolver, rt.world)):
        if (type(live) is not FunctionType or live.__code__ is not fresh.__code__
                or live.__globals__ is not fresh.__globals__
                or live.__defaults__ != fresh.__defaults__
                or live.__kwdefaults__ != fresh.__kwdefaults__
                or live.__closure__ is None or len(live.__closure__) != 1
                or live.__closure__[0].cell_contents is not owner):
            raise ValueError('Captured execution callback owner differs')
    resolver = rt.world.relations._target_resolver
    if (type(resolver) is not MethodType or resolver.__self__ is not rt.world
            or resolver.__func__ is not pristine.world.relations._target_resolver.__func__):
        raise ValueError('Captured relation resolver owner differs')


def _captured_pristine(session, inputs):
    """Verify ownership/bytes, then reconstruct without reopening input labels.

    A capture is not a cached-hash approval. Actual bytes and member metadata
    must agree with the built-in profile; live values are compared separately.
    This is in-memory admission only, not output or publication authority.
    """
    from engine.calendar_capture import CalendarCapture
    from execution.melos_adapter import MelosActionStage
    from execution.melos_factory import MelosSessionFactory, approved_catalogs
    rt = session.rt
    capture = rt.calendar_capture
    factory = getattr(session.stage, '_captured_factory', None)
    if capture is None:
        if (getattr(rt.world, 'calendar_capture', None) is not None
                or getattr(rt.sim, 'calendar_capture', None) is not None
                or factory is not None):
            raise ValueError('Captured session ownership binding differs')
        return None
    if (type(capture) is not CalendarCapture or type(session.stage) is not MelosActionStage
            or type(factory) is not MelosSessionFactory
            or factory._calendar_capture is not capture):
        raise ValueError('Captured admission requires its approved factory binding')
    if rt.store is not None or rt.sim._writer is not None:
        raise ValueError('Pristine captured admission cannot own an output writer')
    capture.assert_world(rt.world)
    capture.assert_provenance(rt.world)
    capture.validate_profile()
    if (rt.sim.calendar_capture is not capture or session.trial.sim is not rt.sim
            or rt.world is not rt.sim.world or rt.people is not rt.sim.subjects
            or rt.sim.slot_runtime is not rt or rt.sim.time_kernel is not rt.time_kernel
            or rt.lifecycle.kernel is not rt.time_kernel):
        raise ValueError('Captured session runtime binding differs')
    expected = approved_catalogs()['inputs']
    actual = {}
    core = {}
    for name in expected:
        role = 'subject:' + Path(name).stem if name.startswith('subjects/') else Path(name).stem
        raw = capture.bytes(role)
        member = capture.member(role)
        record = {'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        if member.logical_name != name or member.manifest() != record:
            raise ValueError('Captured input metadata differs from actual bytes')
        actual[name] = record['sha256']
        if role != 'scenario':
            core[name] = record
    if actual != expected or inputs != actual or capture.core_manifest() != core:
        raise ValueError('Input catalog differs from approved captured bytes')
    if capture.has_role('manifest'):
        raw = capture.bytes('manifest')
        if capture.member('manifest').manifest() != {
                'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}:
            raise ValueError('Captured source manifest differs from actual bytes')
    fresh = factory.create(session.trial.seed)
    if (type(fresh.trial) is not type(session.trial)
            or fresh.rt.calendar_capture is not capture
            or fresh.stage.identity != session.stage.identity):
        raise ValueError('Captured factory differs from admitted session')
    _captured_links(rt, fresh.rt)
    return fresh


def _descriptor(session):
    if type(session) is not MelosSession or type(session.stage.provider) is not MelosChoiceProvider:
        raise ValueError('Exact approved finite session/provider required')
    t = session.trial; rt = session.rt
    inputs = _catalog(session.stage.identity['inputs'])
    fresh = _captured_pristine(session, inputs)
    captured = fresh is not None
    if not captured:
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
    # Reconstruct from the approved constructor and, when present, its capture.
    # A matching
    # caller catalog and self-consistent stage do not authorize an overridden
    # scenario, parameters, goals, world, or initial state.
    pristine = fresh.trial if captured else type(t)(deepcopy(t.genome), t.seed)
    if _effective(t, captured=captured) != _effective(pristine, captured=captured):
        raise ValueError('Effective trial differs from pristine approved inputs')
    # Include the full provider RNG/transcript, journal, stage and session
    # continuations. Matching identity hashes cannot authorize a dynamic state.
    if captured and _initial_hash(session) != _initial_hash(fresh):
        raise ValueError('Captured session differs from pristine factory state')
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
                      'scheduled_events': deepcopy(rt.world.scheduled_events)}, captured=captured)}


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
