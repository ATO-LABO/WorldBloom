"""Explicit neutral Melos independent-trial adapter.

This module does not register an API route. It reuses the approved finite session,
real plan admission and authoritative checkpoint publisher. Minute chunks are
save/cancellation boundaries, never a shortened observation window.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import tempfile
import json

from engine.calendar_checkpoint import strict_json
from engine.execution_plan import ExecutionPlan, digest
from execution.melos_factory import MelosSessionFactory, approved_catalogs
from execution.melos_plan import MelosPlanBinding
from execution.melos_run_record import publish_saved_run_record
from execution.provenance import (ConfigError, atomic_json, canonical, code_snapshot,
                                  contained, directory_lock, identifier, materialize,
                                  sha256, verify_files)
from gapengine.genome import Genome

PROFILE_ID = 'melos_approved_ninegene_v1'
MAX_TRIALS = 100
CHECKPOINT_MINUTES = 120
_REVISION_FIELDS = ('run_spec_hash', 'world_revision', 'policy_revision',
                    'settings_version', 'provider_revision')


def is_melos_profile(config):
    value = config.get('execution_profile')
    return isinstance(value, dict) and value.get('id') == PROFILE_ID


def _profile(config):
    from execution.configs import normalize
    fields = ('label', 'project_id', 'template_id', 'evolution',
              'execution_limits', 'growth', 'execution_profile')
    normalized = normalize({k: deepcopy(config[k]) for k in fields if k in config})
    if not is_melos_profile(normalized):
        raise ConfigError('execution_profile', '明示的な承認済みプロファイルが必要です', code='unsupported')
    return normalized['execution_profile']


@contextmanager
def _factory(runtime_root, input_root, config):
    """Keep an exact-byte alias alive only when a project has a different name."""
    profile = _profile(config)
    project = contained(input_root, 'projects/' + identifier(config['project_id']))
    catalog = approved_catalogs()
    actual = {}
    for path in project.rglob('*'):
        if path.is_symlink():
            raise ValueError('Linked profile inputs are unsupported')
        if path.is_file():
            relative = path.relative_to(project).as_posix()
            actual[relative] = sha256(contained(project, relative).read_bytes())
    if actual != catalog['inputs']:
        raise ValueError('Saved project differs from exact approved input catalog')
    source = contained(runtime_root, 'execution/approved_melos_sources')
    with tempfile.TemporaryDirectory(prefix='wb-melos-profile-') as temporary:
        target = project
        if project.name != 'melos-input':
            target = Path(temporary) / 'melos-input'
            materialize(target, {name: contained(project, name).read_bytes()
                                 for name in catalog['inputs']})
        yield MelosSessionFactory(source, target,
            approved_source_hashes=catalog['sources'], approved_input_hashes=catalog['inputs'],
            genome=Genome.from_dict(profile['genome']))


def _capability(config, inputs, runtime, factory):
    if sha256(canonical(inputs)) != config['input_manifest_sha256']:
        raise ValueError('Saved input manifest differs from config')
    session = factory.create(0)
    axis = session.rt.time_kernel.axis
    provider_config = session.stage.provider.config
    assumptions = {'focus_actor': provider_config.focus_actor,
                   'other_actor_policy': 'fixed_base_candidate_weights',
                   'mapping_status': 'approved_verification_assumptions',
                   'mapping': json.loads(provider_config.cfg)}
    assumptions['mapping_revision'] = digest(assumptions['mapping'])
    policy = {'profile': config['execution_profile'], 'factory': factory.identity,
              'source_hash': digest(runtime),
              'selection': 'none', 'reference_guided': False}
    policy_hash = digest(policy)
    semantic = {'inputs': config['input_manifest_sha256'], 'policy': policy,
                'axis': {'unit': axis.unit, 'start': axis.start, 'end': axis.end},
                'finite_session': True, 'background_completion_not_implied': True}
    settings = digest(semantic)
    return {'schema_version': 1, 'supported': True, 'reason': None,
            'config_id': config['config_id'], 'world': config['project_id'],
            'world_revision': config['input_manifest_sha256'], 'policy_revision': policy_hash,
            'engine_revision': digest(runtime), 'settings_version': settings,
            'run_spec_hash': settings, 'provider_revision': policy_hash,
            'method': 'independent_trials', 'population': 'unselected', 'selection': 'none',
            'reference_guided': False, 'max_trials': MAX_TRIALS,
            'execution_profile': deepcopy(config['execution_profile']),
            'provider_assumptions': assumptions,
            'genome_kind': 'approved_neutral_ninegene',
            'decision_provider_kind': 'melos_ninegene_actor_view_v1',
            'knowledge_scope': 'ActorView_only_no_future_truth_no_external_judge',
            'time_unit': axis.unit, 'horizon': axis.end,
            'scenario_completion_is_calendar_horizon': False}


def capability(configs, config_id):
    config, inputs, root = configs._bundle(config_id)
    _, runtime = code_snapshot(configs.repo)
    with _factory(configs.repo, root / 'inputs', config) as factory:
        return _capability(config, inputs, runtime, factory)


def _admission(cap, request, factory):
    from execution.independent_review import normalize
    request = normalize(request)
    if request['config_id'] != cap['config_id']:
        raise ConfigError('config_id', '設定版が一致しません', code='conflict')
    for field in _REVISION_FIELDS:
        if request[field] != cap[field]:
            raise ConfigError(field, '入力または方針版が更新されています', code='conflict')
    sessions = {seed: factory.create(seed) for seed in request['seed_plan']}
    binding = MelosPlanBinding(sessions)
    result = {'capability': cap, 'execution_plan': binding.plan.to_dict(),
              'execution_plan_sha256': binding.plan.sha256, 'binding_manifest': binding.manifest()}
    return result, binding


def admit(configs, request):
    config, inputs, root = configs._bundle(request['config_id'])
    _, runtime = code_snapshot(configs.repo)
    with _factory(configs.repo, root / 'inputs', config) as factory:
        cap = _capability(config, inputs, runtime, factory)
        return _admission(cap, request, factory)[0]


def _read(root, name):
    path = contained(root, name)
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Missing or oversized profile evidence')
    return strict_json(path.read_text(encoding='utf-8'))


def _owner(manifest, request, admission):
    return {'schema': 'melos_independent_owner_v1', 'job_id': manifest['job_id'],
            'request_sha256': digest(request), 'config_sha256': digest(manifest['config']),
            'input_manifest_sha256': digest(manifest['inputs']),
            'runtime_manifest_sha256': digest(manifest['runtime']),
            'execution_plan_sha256': admission['execution_plan_sha256'],
            'binding_manifest_sha256': digest(admission['binding_manifest'])}


def _trial(case, seed, record, failure=None):
    base = {'id': case.name, 'seed': seed, 'source': f'trials/{case.name}/run-record.json',
            'evidence_refs': [f'trials/{case.name}/layers.jsonl',
                              f'trials/{case.name}/calendar-state.json']}
    if record is not None:
        base['run_record'] = record
    if failure is not None:
        base.update(status='execution_failure', category='execution_failure',
                    error_type=failure['error_type'], error=failure['error'])
        base['evidence_refs'].append(f'trials/{case.name}/failure.json')
        if record is None:
            base['source'] = f'trials/{case.name}/failure.json'
            base['evidence_refs'] = [base['source']]
    elif not record['observations']['scenario_finished']:
        base.update(status='not_completed', category='not_completed')
    else:
        category = ('unresolved' if record['observations']['status'] == 'unresolved'
                    else record['observations']['scenario_outcome'])
        base.update(status='observed', category=category)
    return base


def _receipt(run, records, *, cancelled=False):
    from execution.independent_review import receipt
    value = receipt(run, records)
    value['completed'] = sum(r['status'] in ('observed', 'execution_failure') for r in records)
    value['failed'] = sum(r['status'] == 'execution_failure' for r in records)
    value['unresolved'] = sum(r.get('category') == 'unresolved' for r in records)
    value['execution_profile'] = PROFILE_ID
    value['provider_assumptions'] = deepcopy(_read(run, 'review-manifest.json')['admission']['capability']['provider_assumptions'])
    value['completion'] = ('complete' if value['completed'] == value['requested'] else
                           'cancelled_partial' if cancelled else 'partial')
    return value


def _publish_receipt(run, records, *, cancelled=False):
    result = _receipt(run, records, cancelled=cancelled)
    atomic_json(run / 'partial-review.json', result)
    complete = result['completion'] == 'complete'
    if complete:
        atomic_json(run / 'review-result.json', result)
    atomic_json(run / 'review-completion.json',
                {key: result[key] for key in ('requested', 'completed', 'failed')} | {'complete': complete})
    return result


def run_trials(run, progress=None, should_cancel=None, *, resume=False):
    """Run only admitted slots; explicit resume never reruns terminal/failure slots.

    The outer job/process-tree owner must authorize a restart before passing
    resume=True. Matching on-disk ownership is necessary, not restart authority.
    A run-wide writer lock also rejects concurrent writers. Existing API retry
    reconnects to its job; this module adds no public restart route.
    """
    run = Path(run).absolute()
    manifest = _read(run, 'review-manifest.json')
    request = _read(run, 'accepted-request.json')
    verify_files(run / 'runtime', manifest['runtime']['files'])
    verify_files(run / 'inputs', manifest['inputs']['files'])
    plan = ExecutionPlan(_read(run, 'execution-plan.json'))
    if (plan.to_dict() != manifest['admission']['execution_plan'] or
            plan.sha256 != manifest['admission']['execution_plan_sha256']):
        raise ValueError('Changed execution plan')
    with _factory(run / 'runtime', run / 'inputs', manifest['config']) as factory:
        cap = _capability(manifest['config'], manifest['inputs'], manifest['runtime'], factory)
        admission, binding = _admission(cap, request, factory)
        if admission != manifest['admission']:
            raise ValueError('Frozen profile differs from admitted plan')
        owner = _owner(manifest, request, admission)
        with directory_lock(run, operation='melos.independent_trials'):
            owner_path = contained(run, 'melos-owner.json')
            trials = contained(run, 'trials')
            if resume:
                if _read(run, 'melos-owner.json') != owner:
                    raise ValueError('Resume requires exact owned frozen job')
            else:
                if owner_path.exists() or trials.exists():
                    raise ValueError('Existing trial output requires explicit owned resume')
                atomic_json(owner_path, owner)
            if trials.exists():
                expected_names = {f'trial-{i:04d}' for i in range(plan.to_dict()['planned_trials'])}
                if any(p.name not in expected_names or p.is_symlink() or not p.is_dir()
                       for p in trials.iterdir()):
                    raise ValueError('Unexpected or linked trial output')
            records = []
            cancelled = False
            for ordinal, seed in enumerate(plan.to_dict()['predeclared_seeds']):
                cancelled = cancelled or bool(should_cancel and should_cancel())
                case = contained(trials, f'trial-{ordinal:04d}')
                if cancelled and not case.exists():
                    continue
                pristine = factory.create(seed)
                binding.bind_session(pristine, seed, plan=plan)
                existed = case.exists()
                if existed and not resume:
                    raise ValueError('Existing slot output is protected')
                case.mkdir(parents=True, exist_ok=resume)
                checkpoint = contained(case, 'calendar-state.json')
                record = (publish_saved_run_record(case, binding, pristine, seed)
                          if checkpoint.exists() else None)
                failure = None
                if contained(case, 'failure.json').exists():
                    envelope = _read(case, 'failure.json')
                    failure = envelope['failure']
                    if (envelope.get('sha256') != digest(failure) or
                            failure.get('owner_sha256') != digest(owner) or
                            failure.get('seed') != seed or failure.get('id') != case.name):
                        raise ValueError('Failure evidence belongs to another slot/job')
                if not cancelled and failure is None and (record is None or not record['observations']['scenario_finished']):
                    session = factory.create(seed)
                    binding.bind_session(session, seed, plan=plan)
                    now = record['observed_through'] if record is not None else session.rt.now
                    while True:
                        if should_cancel and should_cancel():
                            cancelled = True
                            break
                        try:
                            session.run_saved(case, until=min(now + CHECKPOINT_MINUTES, session.rt.end),
                                              resume=checkpoint.exists())
                        except Exception as error:
                            failure = {'schema': 'melos_execution_failure_v1', 'id': case.name,
                                       'seed': seed, 'owner_sha256': digest(owner),
                                       'error_type': type(error).__name__, 'error': str(error)}
                            atomic_json(case / 'failure.json', {'failure': failure, 'sha256': digest(failure)})
                            break
                        now = session.rt.now
                        if progress:
                            progress({'requested': plan.to_dict()['planned_trials'],
                                      'completed': sum(r['status'] != 'not_completed' for r in records),
                                      'failed': sum(r['status'] == 'execution_failure' for r in records),
                                      'phase': 'independent_trials', 'execution_profile': PROFILE_ID,
                                      'seed': seed, 'observed_minute': now,
                                      'calendar_horizon': session.rt.end})
                        if session.finished:
                            break
                    if checkpoint.exists():
                        record = publish_saved_run_record(case, binding, pristine, seed)
                # No execution/checkpoint exists when cancellation won the race.
                if record is None and failure is None:
                    cancelled = True
                    continue
                trial = _trial(case, seed, record, failure)
                atomic_json(case / 'classification.json', trial)
                records.append(trial)
                _publish_receipt(run, records, cancelled=cancelled)
                if progress:
                    progress({'requested': plan.to_dict()['planned_trials'],
                              'completed': sum(r['status'] != 'not_completed' for r in records),
                              'failed': sum(r['status'] == 'execution_failure' for r in records),
                              'phase': 'independent_trials', 'execution_profile': PROFILE_ID,
                              'seed': seed, 'status': trial['status']})
            return _publish_receipt(run, records, cancelled=cancelled)
