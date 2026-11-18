"""Private, generation-bound Melos RunRecord projection, without scenario runs.

The independently retained selection and exact captured generation bytes are
required. A fresh session from the same captured factory is admitted, restored
and compared before any derived bytes are produced. Legacy paths are untouched.
"""
from copy import deepcopy
import hashlib
import secrets

from engine.calendar_generation import SelectedGeneration, decode_generation
from engine.calendar_native_exports import PreparedNativeExports, export_leaf
from engine.decision_provider import provider_snapshot
from engine.execution_plan import digest
from execution.melos_factory import MelosSessionFactory
from execution.melos_generation import melos_generation_compatibility
from execution.melos_plan import MelosPlanBinding, classify_session
from execution.provenance import canonical


def _sha(data):
    return hashlib.sha256(data).hexdigest()



def _validate_admission(admission, plan, session, seed):
    # Binding internals are mutable Python objects. Re-bind the whole detached
    # catalog, including other seeds, to the plan before emitting any evidence.
    value = plan.to_dict()
    keys = {'plan_hash', 'seed', 'aggregate_provider', 'actual_provider',
            'initial_session_hash', 'provider_catalog'}
    if type(admission) is not dict or set(admission) != keys:
        raise ValueError('Malformed detached Melos admission')
    catalog = admission['provider_catalog']
    if (type(admission['seed']) is not int or admission['seed'] != seed
            or admission['plan_hash'] != plan.sha256
            or admission['aggregate_provider'] != value['provider']
            or type(catalog) is not dict
            or set(catalog) != {str(s) for s in value['predeclared_seeds']}
            or digest(catalog) != value['provider']['config_hash']
            or catalog[str(seed)] != {'provider': admission['actual_provider'],
                                     'initial_hash': admission['initial_session_hash']}
            or admission['initial_session_hash'] != digest(session.export_state())):
        raise ValueError('Detached seed/catalog admission differs from the admitted plan')


def project_melos_generation_record(selection, generation_bytes, factory, binding, seed):
    """Derive a new immutable publication; never run callbacks or open inputs.

    Installed code is still verified. Input labels are not storage authority.
    Caller owns the live capture, supplies independent selection evidence, and
    excludes concurrent mutation of its factory/binding during this operation.
    This returns bytes only: no borrowed output handle or mutable session leaks.
    """
    if type(selection) is not SelectedGeneration:
        raise ValueError('An independent selected generation is required')
    selection.validate()
    if (type(factory) is not MelosSessionFactory or factory._calendar_capture is None
            or type(binding) is not MelosPlanBinding):
        raise ValueError('An exact captured factory and admitted Melos binding are required')
    factory._calendar_capture.assert_live()
    plan = binding.admit(binding.plan)
    session = factory.create(seed)
    if (session.stage._captured_factory is not factory
            or session.rt.calendar_capture is not factory._calendar_capture):
        raise ValueError('Fresh session differs from captured factory ownership')
    binding.bind_session(session, seed, plan=plan)
    admission = binding.seed_admission(seed)
    _validate_admission(admission, plan, session, seed)
    expected = melos_generation_compatibility(session)
    generation = decode_generation(generation_bytes, expected=selection.expected,
                                   expected_compatibility=expected)
    committed = generation.state(expected)
    session.restore_state(committed)
    if session.export_state() != committed:
        raise ValueError('Restored session differs from selected generation')
    actual_provider = provider_snapshot(session.stage.provider)['identity']
    declared_provider = {k: v for k, v in actual_provider.items() if k != 'runtime_source_hash'}
    if declared_provider != admission['actual_provider'] or session.trial.seed != admission['seed']:
        raise ValueError('Selected seed/provider differs from admitted slot')
    factory._calendar_capture.assert_live()
    rows = session.rt.rows
    ledger = b''.join(canonical(row) + b'\n' for row in rows)
    result = canonical({'schema': 'melos_native_generation_result_v1',
        'completion': 'complete' if session.finished else 'paused', 'seed': session.trial.seed,
        'state': deepcopy(session.trial.state), 'decisions': deepcopy(session.trial.decisions),
        'counts': deepcopy(session.trial.count),
        'audit': session.trial.audit() if session.finished else None,
        'product_profile_active': True, 'checkpoint_sha256': committed['sha256']}) + b'\n'
    observations = classify_session(session)
    stop = ('scenario_terminal' if observations['status'] == 'observed' else
            'scenario_unresolved' if session.finished else 'paused')
    terminals = [(line, row) for line, row in enumerate(rows, 1) if row['verb'] == 'observation_result']
    terminal = None
    if terminals:
        line, row = terminals[-1]
        terminal = {'verb': row['verb'], 'line': line, 'event_id': row['id'],
                    'details': deepcopy(row['details'])}
    axis = session.rt.time_kernel.axis
    publication_id = secrets.token_hex(16)
    selected = {'leaf': selection.leaf, 'expected': {
        name: getattr(selection.expected, name) for name in ('sha256', 'size', 'stream_id', 'ordinal')}}
    run_spec = {'execution_plan_hash': plan.sha256, 'admission': admission,
                'checkpoint_compatibility': expected}
    record = {'schema_version': 1, 'descriptor_scope': 'melos_native_generation_v1',
        'storage': {'kind': 'native_generation_exports_v1', 'selected_generation': selected,
                    'publication_id': publication_id, 'restart_authority': False},
        'run_id': 'saved-' + digest({'ledger': _sha(ledger), 'run_spec': digest(run_spec)}),
        'time_axis': {'unit': axis.unit, 'start': axis.start, 'end': axis.end},
        'observed_through': session.rt.now,
        'configured_target_ending': deepcopy(session.rt.world.target_ending),
        'execution_plan': plan.to_dict(), 'execution_plan_hash': plan.sha256,
        'verified_execution': None, 'melos_admission': admission,
        'ledger': {'leaf': export_leaf(selection, publication_id, 'layers.jsonl'),
                   'sha256': _sha(ledger), 'size': len(ledger)},
        'result': {'leaf': export_leaf(selection, publication_id, 'result.json'),
                   'sha256': _sha(result), 'size': len(result)},
        'actual_terminal': terminal, 'stop_reason': stop, 'observed_targets': [],
        'observations': observations, 'provider_identity': actual_provider,
        'input_hashes': {'files': deepcopy(session.stage.identity['inputs']), 'seed': admission['seed']},
        'run_spec_hash': digest(run_spec),
        'time_contract_hashes': deepcopy(session.rt.inputs().get('time_contract_hashes')),
        'engine_hash': session.rt.sim._engine_hash(),
        'generation_sha256': selection.expected.sha256,
        'checkpoint_sha256': _sha(generation.checkpoint),
        'session_state_sha256': committed['sha256'], 'result_sha256': _sha(result),
        'product_profile_active': True, 'independent_ui_verified': False,
        'native_ui_admission': 'not_connected', 'failure_evidence': None}
    record['saved_revision_hash'] = digest(record)
    prepared = PreparedNativeExports(selection, publication_id, ledger, result, canonical(record) + b'\n')
    prepared.artifacts()  # Bound all immutable output before exposing a bundle.
    return prepared
