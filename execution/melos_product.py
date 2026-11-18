"""Explicit neutral Melos independent-trial adapter.

This module does not register an API route. It reuses the approved finite session,
real plan admission and native generation-bound publisher. Minute chunks are
save/cancellation boundaries, never a shortened observation window.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import json

from engine.calendar_checkpoint import strict_json
from engine.execution_plan import ExecutionPlan, digest
from execution.melos_factory import MelosSessionFactory, approved_catalogs
from execution.melos_plan import MelosPlanBinding
from execution.provenance import (ConfigError, canonical, code_snapshot,
                                  contained, identifier, sha256)
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
    """One live input capture, explicit frozen genome, no temporary path alias."""
    from engine.calendar_capture import open_calendar_capture
    profile = _profile(config)
    project = Path(input_root).absolute() / 'projects' / identifier(config['project_id'])
    catalog = approved_catalogs()
    genome = Genome.from_dict(deepcopy(profile['genome']))
    with open_calendar_capture(project / 'world.yaml', profile='melos', capture_ancestry=True) as capture:
        yield MelosSessionFactory(Path(runtime_root) / 'execution/approved_melos_sources', project,
            approved_source_hashes=catalog['sources'], approved_input_hashes=catalog['inputs'],
            genome=genome, calendar_capture=capture)


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
    result = {'storage': {'kind': 'melos_independent_native_v1', 'restart_authority': False},
              'capability': cap, 'execution_plan': binding.plan.to_dict(),
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


def run_trials(run, progress=None, should_cancel=None, *, resume=False, acknowledge=None):
    manifest = _read(Path(run), 'review-manifest.json')
    storage = manifest.get('storage')
    from execution.melos_native_jobs import is_native_storage
    if is_native_storage(storage):
        if resume:
            raise ValueError('Native independent jobs have no restart authorization or resume route')
        from execution.melos_native_jobs import run_native_trials
        return run_native_trials(run, progress, should_cancel, acknowledge=acknowledge)
    if storage is not None:
        raise ValueError('Unknown independent storage discriminator')
    # Saved legacy descriptors remain readable. New execution never downgrades.
    raise ValueError('Legacy Melos jobs are read-only; no automatic storage migration')
