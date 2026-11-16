"""Immutable generic RunRecord projection from an independently selected save.

Never reads saved output paths, runs callbacks or mutates the live runtime.
Installed source hashes remain verified; captured input labels are not authority.
"""
from copy import deepcopy
import hashlib
import secrets

from engine.calendar_checkpoint import compatibility
from engine.calendar_generation import SelectedGeneration, decode_generation
from engine.calendar_native_exports import PreparedNativeExports, export_leaf
from engine.execution_plan import digest
from execution.provenance import canonical


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def project_calendar_generation_record(selection, generation_bytes, factory):
    from execution.calendar_native import CapturedCalendarFactory, selection_value
    if type(selection) is not SelectedGeneration or type(factory) is not CapturedCalendarFactory:
        raise ValueError('An independent selection and exact captured generic factory are required')
    selection.validate()
    runtime = factory.create()
    expected = compatibility(runtime.inputs(), runtime.commands)
    generation = decode_generation(generation_bytes, expected=selection.expected,
                                   expected_compatibility=expected)
    committed = generation.state(expected)
    runtime.restore_state(committed)
    if canonical(runtime.export_state()) != canonical(committed):
        raise ValueError('Fresh restored runtime differs from the selected generation')
    ledger, result = runtime._layers_bytes(), runtime._summary_bytes()
    factory.capture.assert_live()
    inputs = runtime.inputs()
    axis = runtime.time_kernel.axis
    publication_id = secrets.token_hex(16)
    # Generic minute-runtime completion is a calendar observation horizon, not
    # an ending success. Its registered observations retain their actual values.
    # Observer verbs are authored labels, never terminal/target authority.
    stop = 'observation_limit' if runtime.done else 'paused'
    run_spec = {'inputs': inputs, 'commands': runtime.commands,
                'checkpoint_compatibility': expected}
    record = {'schema_version': 1, 'descriptor_scope': 'generic_calendar_native_generation_v1',
        'storage': {'kind': 'generic_calendar_native_generation_exports_v1',
                    'selected_generation': selection_value(selection),
                    'publication_id': publication_id, 'restart_authority': False},
        'run_id': 'saved-' + digest({'ledger': _sha(ledger), 'run_spec': digest(run_spec)}),
        'time_axis': {'unit': axis.unit, 'start': axis.start, 'end': axis.end},
        'observed_through': runtime.now,
        'configured_target_ending': deepcopy(runtime.world.target_ending),
        'execution_plan': None, 'execution_plan_hash': None, 'verified_execution': None,
        'ledger': {'leaf': export_leaf(selection, publication_id, 'layers.jsonl'),
                   'sha256': _sha(ledger), 'size': len(ledger)},
        # Preserve the publisher's result role. The payload is exactly the
        # generic runtime summary, not a Melos result and not a legacy pathname.
        'result': {'leaf': export_leaf(selection, publication_id, 'result.json'),
                   'sha256': _sha(result), 'size': len(result), 'kind': 'calendar_summary'},
        'actual_terminal': None, 'stop_reason': stop, 'observed_targets': [],
        'observations': deepcopy(runtime.observations), 'provider_identity': None,
        'input_hashes': deepcopy(inputs['files']),
        'choices_sha256': factory.choices.sha256, 'choices_size': len(factory.choices.data),
        'commands_sha256': digest(runtime.commands), 'run_spec_hash': digest(run_spec),
        'time_contract_hashes': deepcopy(inputs.get('time_contract_hashes')),
        'engine_hash': runtime.sim._engine_hash(),
        'generation_sha256': selection.expected.sha256,
        'checkpoint_sha256': _sha(generation.checkpoint),
        'runtime_state_sha256': digest(committed), 'result_sha256': _sha(result),
        'api_calls': 0, 'gpu': False,
        'independent_ui_verified': False, 'native_ui_admission': 'not_connected',
        'failure_evidence': None}
    record['saved_revision_hash'] = digest(record)
    prepared = PreparedNativeExports(selection, publication_id, ledger, result, canonical(record) + b'\n')
    prepared.artifacts()
    return prepared
