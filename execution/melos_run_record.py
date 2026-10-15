"""Opt-in saved Melos RunRecord projection, never fictional engine admission.

The committed calendar-state.json is authoritative. Derived files are rebuilt
under its writer lease; the RunRecord is published last. The aggregate plan's
provider catalog and this seed's actual provider identity remain distinct.
No API/UI registration or independent-batch verification is implied.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys

from engine.calendar_checkpoint import CheckpointStore, atomic_bytes, strict_json
from engine.decision_provider import provider_snapshot
from engine.execution_plan import digest
from execution.melos_plan import MelosPlanBinding, classify_session
from execution.melos_session import MelosSession
from execution.provenance import canonical


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Missing, oversized, or linked saved output')
    return path.read_bytes()


def _prepare(out, binding, pristine_session, seed):
    if type(binding) is not MelosPlanBinding or type(pristine_session) is not MelosSession:
        raise ValueError('Exact admitted Melos binding and pristine session required')
    path = Path(out).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Linked output location is unsupported')
    plan = binding.admit(binding.plan)
    binding.bind_session(pristine_session, seed, plan=plan)
    admission = binding.seed_admission(seed)
    session = deepcopy(pristine_session)
    expected = {'family': 'melos_approved_session_v1', 'identity': session.stage.identity,
                'provider': provider_snapshot(session.stage.provider)['identity'],
                'python': list(sys.version_info[:3])}
    return CheckpointStore(path, expected), session, plan, admission


def _projection(store, session, plan, admission):
    """Called while holding exactly one CheckpointStore writer lease."""
    committed = store.load()
    session.restore_state(committed)
    if session.export_state() != committed:
        raise ValueError('Restored session differs from committed state')
    actual_provider = provider_snapshot(session.stage.provider)['identity']
    # Snapshot adds runtime_source_hash to the provider's declared identity.
    declared_provider = {k: v for k, v in actual_provider.items() if k != 'runtime_source_hash'}
    if declared_provider != admission['actual_provider'] or session.trial.seed != admission['seed']:
        raise ValueError('Committed seed/provider differs from admitted slot')
    checkpoint = _read(store.out / 'calendar-state.json')
    checkpoint_envelope = strict_json(checkpoint.decode('utf-8'))
    if (checkpoint_envelope['payload']['state'] != committed
            or checkpoint_envelope['payload']['compatibility'] != store.expected):
        raise ValueError('Checkpoint changed during projection')
    rows = session.rt.rows
    ledger = b''.join(canonical(row) + b'\n' for row in rows)
    result = {'schema': 'melos_approved_session_result_v1',
              'completion': 'complete' if session.finished else 'paused',
              'seed': session.trial.seed, 'state': deepcopy(session.trial.state),
              'decisions': deepcopy(session.trial.decisions), 'counts': deepcopy(session.trial.count),
              'audit': session.trial.audit() if session.finished else None,
              'product_profile_active': True, 'checkpoint_sha256': committed['sha256']}
    result_bytes = canonical(result) + b'\n'
    observations = classify_session(session)
    stop = ('scenario_terminal' if observations['status'] == 'observed' else
            'scenario_unresolved' if session.finished else 'paused')
    terminals = [(line, row) for line, row in enumerate(rows, 1)
                 if row['verb'] == 'observation_result']
    terminal = None
    if terminals:
        line, row = terminals[-1]
        terminal = {'verb': row['verb'], 'line': line, 'event_id': row['id'],
                    'details': deepcopy(row['details'])}
    axis = session.rt.time_kernel.axis
    run_spec = {'execution_plan_hash': plan.sha256, 'admission': admission,
                'checkpoint_compatibility': store.expected}
    record = {'schema_version': 1, 'descriptor_scope': 'melos_committed_session_v1',
              'run_id': 'saved-' + digest({'ledger': _sha(ledger), 'run_spec': digest(run_spec)}),
              'time_axis': {'unit': axis.unit, 'start': axis.start, 'end': axis.end},
              'observed_through': session.rt.now,
              'configured_target_ending': deepcopy(session.rt.world.target_ending),
              'execution_plan': plan.to_dict(), 'execution_plan_hash': plan.sha256,
              'verified_execution': None, 'melos_admission': deepcopy(admission),
              'ledger': {'path': 'layers.jsonl', 'sha256': _sha(ledger)},
              'actual_terminal': terminal, 'stop_reason': stop, 'observed_targets': [],
              'observations': observations, 'provider_identity': actual_provider,
              'input_hashes': {'files': deepcopy(session.stage.identity['inputs']), 'seed': admission['seed']},
              'run_spec_hash': digest(run_spec),
              'time_contract_hashes': deepcopy(session.rt.inputs().get('time_contract_hashes')),
              'engine_hash': session.rt.sim._engine_hash(),
              'checkpoint_sha256': _sha(checkpoint), 'session_state_sha256': committed['sha256'],
              'result_sha256': _sha(result_bytes),
              'product_profile_active': True, 'independent_ui_verified': False,
              'failure_evidence': None}
    record['saved_revision_hash'] = digest(record)
    return record, ledger, result_bytes


def publish_saved_run_record(out, binding, pristine_session, seed):
    """Project an existing committed save; never executes a scenario step.

    A fresh, admitted session is restored privately. Stale/missing derived
    outputs are repairable; a corrupt checkpoint or mismatched binding is not.
    Failure to publish leaves the checkpoint intact and propagates the error.
    """
    store, session, plan, admission = _prepare(out, binding, pristine_session, seed)
    with store.session():
        record, ledger, result = _projection(store, session, plan, admission)
        atomic_bytes(store.out / 'layers.jsonl', ledger)
        atomic_bytes(store.out / 'result.json', result)
        atomic_bytes(store.out / 'run-record.json', canonical(record) + b'\n')
        if (_read(store.out / 'layers.jsonl') != ledger or _read(store.out / 'result.json') != result
                or _read(store.out / 'run-record.json') != canonical(record) + b'\n'):
            raise ValueError('Derived publication readback differs')
        return deepcopy(record)


def describe_saved_run_record(out, binding, pristine_session, seed):
    """Validate saved descriptor against admitted committed state, not live UI.

    Rehashed descriptor tampering is rejected by regenerating the complete
    projection, including per-seed admission. No repairs or writes occur here.
    Execution errors cannot be inferred from an interrupted/stale export.
    """
    store, session, plan, admission = _prepare(out, binding, pristine_session, seed)
    with store.session():
        expected, ledger, result = _projection(store, session, plan, admission)
        saved = strict_json(_read(store.out / 'run-record.json').decode('utf-8'))
        if (canonical(saved) != canonical(expected) or _read(store.out / 'layers.jsonl') != ledger
                or _read(store.out / 'result.json') != result):
            raise ValueError('Saved descriptor/derived outputs differ from committed revision')
        return deepcopy(expected)
