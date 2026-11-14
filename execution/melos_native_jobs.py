"""Initial native Melos jobs and explicit read-only control-plane result refs.

No restart catalog, output scans or path-writer fallback. A single live capture
and native output owner span every trial chunk, fresh projection and publication.
Control references describe accepted observations, never restart permission.
"""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import secrets

from engine.calendar_capture import _owned_directory, _raise_cleanup
from engine.calendar_checkpoint import strict_json
from engine.calendar_fs import directory_ancestry
from engine.calendar_generation import GenerationRef, SelectedGeneration
from engine.calendar_generation_capture import NativeGenerationCapture
from engine.calendar_native_exports import (MAX_EXPORT_BYTES, NativeExportArtifact, export_leaf,
    publish_native_artifacts, publish_native_exports)
from engine.calendar_parent_owner import _ExistingParentOwner
from engine.calendar_run_store import CapturedGeneration
from engine.execution_plan import ExecutionPlan, digest
from execution.calendar_native import (_output_label, _separate_ancestry, _captured_generation,
                                      selection_value)
from execution.melos_generation import MelosGenerationProgress, run_melos_generation
from execution.melos_generation_record import project_melos_generation_record
from execution.provenance import canonical, verify_files

STORAGE = {'kind': 'melos_independent_native_v1', 'restart_authority': False}
CONTROL_SCHEMA = 'melos_native_job_refs_v1'
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_RECEIPT = re.compile(r'wbreview-[0-9a-f]{32}-receipt\.json\Z')
MAX_READ_TOTAL = 128 * 1024 * 1024


def _exact(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('Invalid ' + label)


def is_native_storage(value):
    return (type(value) is dict and set(value)=={'kind','restart_authority'}
            and value['kind']=='melos_independent_native_v1'
            and value['restart_authority'] is False)


def selected(value):
    _exact(value, ('leaf', 'expected'), 'selected generation')
    _exact(value['expected'], ('sha256', 'size', 'stream_id', 'ordinal'), 'generation reference')
    selection = SelectedGeneration(value['leaf'], GenerationRef(**value['expected'])).validate()
    if selection.expected.ordinal > 9007199254740991:
        raise ValueError('Generation ordinal exceeds exact UI integer range')
    return selection


def artifact_ref(value, *, leaf=None):
    _exact(value, ('leaf', 'sha256', 'size'), 'native artifact reference')
    from engine.calendar_windows_writer import _leaf
    _leaf(value['leaf'])
    if (type(value['sha256']) is not str or not _HASH.fullmatch(value['sha256'])
            or type(value['size']) is not int or not 0 <= value['size'] <= MAX_EXPORT_BYTES
            or (leaf is not None and value['leaf'] != leaf)):
        raise ValueError('Invalid native artifact identity')
    return value


def reference(artifact):
    return {'leaf': artifact.leaf, 'sha256': artifact.sha256, 'size': len(artifact.data)}


def validate_control_refs(value, job):
    _exact(value, ('schema', 'storage', 'job_id', 'execution_plan_sha256', 'accepted', 'receipt_accepted', 'receipt'), 'native control references')
    if (value['schema'] != CONTROL_SCHEMA or not is_native_storage(value['storage'])
            or value['job_id'] != job['job_id']
            or value['execution_plan_sha256'] != job['execution_plan_sha256']
            or not is_native_storage(job.get('independent_storage'))
            or job.get('kind', 'independent_trials') != 'independent_trials'):
        raise ValueError('Native references differ from admitted job')
    seeds = job['accepted_request']['seed_plan']
    for field in ('accepted','receipt_accepted'):
        if type(value[field]) is not list or len(value[field]) > len(seeds):
            raise ValueError('Invalid accepted native slots')
        seen = set()
        for item in value[field]:
            _exact(item, ('seed', 'selection'), 'accepted slot')
            if type(item['seed']) is not int or item['seed'] not in seeds or item['seed'] in seen:
                raise ValueError('Invalid accepted native seed')
            seen.add(item['seed']); selected(item['selection'])
    if not {v['seed'] for v in value['receipt_accepted']} <= {v['seed'] for v in value['accepted']}:
        raise ValueError('Receipt slots are outside accepted job slots')
    if value['receipt'] is not None:
        artifact_ref(value['receipt'])
        if not _RECEIPT.fullmatch(value['receipt']['leaf']):
            raise ValueError('Invalid native receipt leaf')
    elif value['receipt_accepted']:
        raise ValueError('Unpublished receipt cannot claim accepted slots')
    return deepcopy(value)


def acknowledge_control(jobs, folder, nonce, value):
    """Fail rather than silently losing exact refs to nonce/terminal races.

    This is deliberately separate from legacy worker.change. Readback occurs
    under its existing lock; a failed/uncertain ack never returns success.
    """
    from execution import worker
    import time
    with worker.lock(jobs, operation='worker.native_result_ack'):
        job = worker.read(folder / 'job.json')
        if job['nonce'] != nonce or job['state'] in worker.TERMINAL:
            raise ValueError('Native result acknowledgement lost its live job owner')
        refs = validate_control_refs(value, job)
        prior = job.get('native_result_refs')
        if prior is not None:
            validate_control_refs(prior, job)
            if prior['receipt'] is not None and refs['receipt'] is None:
                raise ValueError('Native acknowledgement cannot erase its published receipt')
            if not {v['seed'] for v in prior['accepted']} <= {v['seed'] for v in refs['accepted']}:
                raise ValueError('Native acknowledgement cannot erase accepted slots')
            if refs['receipt'] == prior['receipt'] and refs['receipt_accepted'] != prior['receipt_accepted']:
                raise ValueError('Unchanged receipt cannot replace its associated selections')
        if (refs['receipt'] is not None and (prior is None or refs['receipt'] != prior['receipt'])
                and refs['receipt_accepted'] != refs['accepted']):
            raise ValueError('New receipt must acknowledge exact retained selections')
        job.update(native_result_refs=refs, updated_at=time.time(), revision=job['revision'] + 1)
        worker.atomic(folder / 'job.json', job)
        observed = worker.read(folder / 'job.json')
        if observed != job:
            raise ValueError('Native control publication readback did not acknowledge exact job revision')
        worker.record_event(folder, job)
        return deepcopy(refs)


def _read_artifact(owner, ref, budget):
    artifact_ref(ref)
    budget[0] += ref['size']
    if budget[0] > MAX_READ_TOTAL:
        raise ValueError('Native review exceeds bounded read budget')
    lease, primary = owner._borrow(owner.backend), None
    try:
        value = NativeGenerationCapture().capture(owner, ref['leaf'], max_bytes=ref['size'], parent_borrow=lease)
        if (type(value) is not CapturedGeneration or value.owner is not owner
                or value.parent_identity != owner.identity or value.leaf != ref['leaf']
                or value.normal_completion is not True or value.release != 'released'
                or type(value.data) is not bytes or len(value.data) != ref['size']
                or hashlib.sha256(value.data).hexdigest() != ref['sha256']):
            raise ValueError('Native artifact differs from acknowledged reference')
        return value.data
    except BaseException as error:
        primary = error; raise
    finally:
        try: lease.close()
        except BaseException as error: _raise_cleanup(primary, error, 'Native review read cleanup failed: ')


def _json(raw):
    return strict_json(raw.decode('utf-8'))


def _record_refs(trial, plan_hash):
    record = trial['run_record']; selection = selected(trial['selected_generation'])
    if type(record) is not dict:
        raise ValueError('Native RunRecord must be an object')
    storage = record.get('storage')
    _exact(storage, ('kind', 'selected_generation', 'publication_id', 'restart_authority'), 'RunRecord storage')
    if (record.get('descriptor_scope') != 'melos_native_generation_v1'
            or type(record.get('schema_version')) is not int or record['schema_version'] != 1 or storage['kind'] != 'native_generation_exports_v1'
            or storage['restart_authority'] is not False or storage['selected_generation'] != trial['selected_generation']
            or record.get('generation_sha256') != selection.expected.sha256
            or record.get('execution_plan_hash') != plan_hash or record.get('verified_execution') is not None
            or record.get('independent_ui_verified') is not False or record.get('native_ui_admission') != 'not_connected'):
        raise ValueError('Native RunRecord storage/admission mismatch')
    for field in ('checkpoint_sha256', 'session_state_sha256', 'result_sha256', 'saved_revision_hash'):
        if type(record.get(field)) is not str or not _HASH.fullmatch(record[field]):
            raise ValueError('Invalid native RunRecord digest')
    content = deepcopy(record); claimed = content.pop('saved_revision_hash')
    if digest(content) != claimed or digest(record['execution_plan']) != plan_hash:
        raise ValueError('Changed native RunRecord')
    admission = record['melos_admission']
    _exact(admission, ('plan_hash','seed','aggregate_provider','actual_provider','initial_session_hash','provider_catalog'), 'native seed admission')
    catalog = admission['provider_catalog']
    if (type(catalog) is not dict or type(record.get('provider_identity')) is not dict
            or type(admission['seed']) is not int):
        raise ValueError('Invalid native seed/provider catalog')
    plan = ExecutionPlan(record['execution_plan']).to_dict()
    if (admission['seed'] != trial['seed'] or admission['plan_hash'] != plan_hash
            or set(catalog) != {str(s) for s in plan['predeclared_seeds']}
            or digest(catalog) != plan['provider']['config_hash']
            or admission['aggregate_provider'] != plan['provider']
            or catalog[str(trial['seed'])] != {'provider':admission['actual_provider'], 'initial_hash':admission['initial_session_hash']}
            or {k:v for k,v in record['provider_identity'].items() if k != 'runtime_source_hash'} != admission['actual_provider']):
        raise ValueError('Changed per-seed native provider catalog')
    artifacts = trial['artifacts']; _exact(artifacts, ('layers','result','run_record'), 'native bundle')
    for key, role in (('layers','layers.jsonl'), ('result','result.json'), ('run_record','run-record.json')):
        artifact_ref(artifacts[key], leaf=export_leaf(selection, storage['publication_id'], role))
    if (record['ledger'] != artifacts['layers'] or record['result'] != artifacts['result']
            or record['result_sha256'] != artifacts['result']['sha256']):
        raise ValueError('Native bundle differs from RunRecord artifacts')
    return selection


def read_native_result(root, job):
    """Read only exact control refs. The output manifests are not authority."""
    refs = validate_control_refs(job.get('native_result_refs'), job)
    if refs['receipt'] is None:
        raise FileNotFoundError('No acknowledged native receipt')
    budget = [0]
    with _ExistingParentOwner(Path(root) / 'native-results', backend='windows' if os.name == 'nt' else 'linux') as owner:
        result = _json(_read_artifact(owner, refs['receipt'], budget))
        if type(result) is not dict:
            raise ValueError('Native receipt must be an object')
        if (result.get('schema') != 'worldbloom_independent_v3' or result.get('schema_version') != 3
                or not is_native_storage(result.get('storage')) or result.get('id') != job['job_id']
                or result.get('execution_plan_sha256') != job['execution_plan_sha256']
                or result.get('seed_plan') != job['accepted_request']['seed_plan']):
            raise ValueError('Native receipt differs from acknowledged job')
        trials = result['trials']; seeds = result['seed_plan']
        if type(trials) is not list or len(trials) != len(seeds):
            raise ValueError('Native receipt slot count differs')
        accepted = {x['seed']: x['selection'] for x in refs['receipt_accepted']}
        latest_accepted = {x['seed']: x['selection'] for x in refs['accepted']}
        verified_generations = set()
        def verify_generation(selection):
            if selection.leaf in verified_generations: return
            budget[0] += selection.expected.size
            if budget[0] > MAX_READ_TOTAL:
                raise ValueError('Native review exceeds bounded read budget')
            raw = _captured_generation(owner,selection)
            if (len(raw) != selection.expected.size
                    or hashlib.sha256(raw).hexdigest() != selection.expected.sha256):
                raise ValueError('Selected generation differs from bounded acknowledged bytes')
            verified_generations.add(selection.leaf)
        for i, (trial, seed) in enumerate(zip(trials, seeds)):
            if type(trial) is not dict:
                raise ValueError('Native trial must be an object')
            if type(trial.get('seed')) is not int or trial['seed'] != seed or trial['id'] != f'trial-{i:04d}':
                raise ValueError('Native receipt seed slot mismatch')
            if 'last_accepted_generation' in trial:
                latest = selected(trial['last_accepted_generation'])
                if trial['last_accepted_generation'] != accepted.get(seed):
                    raise ValueError('Receipt selection differs from its acknowledged snapshot')
                verify_generation(latest)
                # A newer accepted generation may have no successful export.
                # It is verified separately, never compared by stream/ordinal.
                verify_generation(selected(latest_accepted.get(seed)))
            if 'run_record' not in trial:
                if any(k in trial for k in ('selected_generation','artifacts')) or trial['status'] != 'not_completed':
                    raise ValueError('Unpublished native slot claims completion')
                continue
            selection = _record_refs(trial, job['execution_plan_sha256'])
            if 'last_accepted_generation' not in trial:
                raise ValueError('Published native bundle lacks an accepted selection')
            verify_generation(selection)
            blobs = {key:_read_artifact(owner, ref, budget) for key, ref in trial['artifacts'].items()}
            if _json(blobs['run_record']) != trial['run_record']:
                raise ValueError('Receipt RunRecord differs from published bounded bytes')
            record = trial['run_record']; observed = record['observations']
            if (type(observed) is not dict or type(observed.get('scenario_finished')) is not bool
                    or observed.get('status') not in ('observed','unresolved')):
                raise ValueError('Invalid native scenario observation')
            expected_status = 'observed' if observed['scenario_finished'] else 'not_completed'
            category = ('unresolved' if observed['status'] == 'unresolved' else observed['scenario_outcome']) if expected_status == 'observed' else 'not_completed'
            if trial['status'] != expected_status or trial['category'] != category:
                raise ValueError('Receipt classification differs from published observations')
        if result.get('completion') not in ('complete','partial','cancelled_partial','failed_partial'):
            raise ValueError('Invalid native completion state')
        if 'operation_error' in result:
            _exact(result['operation_error'], ('phase','error_type','error'), 'operation error')
            if any(type(v) is not str or len(v)>2000 for v in result['operation_error'].values()):
                raise ValueError('Invalid operation error')
        if (result['completion']=='failed_partial') != ('operation_error' in result):
            raise ValueError('Operation failure state differs from its evidence')
        if any(type(result.get(k)) is not int for k in ('requested','completed','failed','unresolved')):
            raise ValueError('Native receipt counts must be exact integers')
        completed = sum(t['status'] == 'observed' for t in trials)
        if (result['requested'] != len(seeds) or result['completed'] != completed or result['failed'] != 0
                or result['unresolved'] != sum(t.get('category') == 'unresolved' for t in trials)
                or (result['completion'] == 'complete' and completed != len(seeds))):
            raise ValueError('Native receipt counts differ from actual published observations')
    return result


def run_native_trials(run, progress=None, should_cancel=None, *, acknowledge=None):
    from execution import melos_product as product
    run = Path(run).absolute(); manifest = product._read(run, 'review-manifest.json')
    if not is_native_storage(manifest.get('storage')) or not is_native_storage(manifest['admission'].get('storage')):
        raise ValueError('Explicit native independent storage admission required')
    request = product._read(run, 'accepted-request.json')
    verify_files(run / 'runtime', manifest['runtime']['files'])
    verify_files(run / 'inputs', manifest['inputs']['files'])
    plan = ExecutionPlan(product._read(run, 'execution-plan.json'))
    if plan.to_dict() != manifest['admission']['execution_plan'] or plan.sha256 != manifest['admission']['execution_plan_sha256']:
        raise ValueError('Changed native execution plan')
    output = _output_label(run / 'native-results', (run / 'inputs', run / 'runtime'))
    installed = Path(__file__).resolve().parents[1]
    _output_label(output, (installed,))
    with product._factory(run / 'runtime', run / 'inputs', manifest['config']) as factory:
        cap = product._capability(manifest['config'], manifest['inputs'], manifest['runtime'], factory)
        admission, binding = product._admission(cap, request, factory)
        if admission != manifest['admission']:
            raise ValueError('Captured native profile differs from admitted identity')
        with _owned_directory(run / 'runtime') as directory:
            runtime_ancestry = directory_ancestry(directory)
        with _owned_directory(installed) as directory:
            installed_ancestry = directory_ancestry(directory)
        with _ExistingParentOwner(output, backend='windows' if os.name == 'nt' else 'linux') as owner:
            for ancestry in (factory._calendar_capture.source_ancestry, runtime_ancestry, installed_ancestry):
                _separate_ancestry(owner.ancestry, ancestry)
            # Create-only initial claim prevents a repeat call from rerunning any
            # slot. Its content is evidence, not ownership or restart authority.
            claim = NativeExportArtifact('wbjobs-initial-claim.json', canonical(product._owner(manifest,request,admission)))
            publish_native_artifacts(owner, (claim,))
            records = {}; accepted = {}; receipt_ref = None; receipt_accepted = []; cancelled = False
            def control():
                value = {'schema':CONTROL_SCHEMA, 'storage':deepcopy(STORAGE), 'job_id':manifest['job_id'],
                         'execution_plan_sha256':plan.sha256,
                         'accepted':[{'seed':s,'selection':selection_value(v)} for s,v in accepted.items()],
                         'receipt':deepcopy(receipt_ref),'receipt_accepted':deepcopy(receipt_accepted)}
                if acknowledge is not None:
                    observed = acknowledge(deepcopy(value))
                    if observed != value:
                        raise ValueError('Exact native control acknowledgement required')
            def publish(error=None):
                # Construct aggregate in memory from explicit retained records.
                from execution.independent_review import receipt
                value = receipt(run, list(records.values()), manifest=manifest)
                value.update(schema='worldbloom_independent_v3',schema_version=3,storage=deepcopy(STORAGE),
                    execution_profile=product.PROFILE_ID,provider_assumptions=deepcopy(cap['provider_assumptions']))
                for trial in value['trials']:
                    if trial['seed'] in accepted:
                        trial['last_accepted_generation']=selection_value(accepted[trial['seed']])
                    if 'run_record' not in trial:
                        trial['source']='admitted_execution_plan';trial['evidence_refs']=['admitted_execution_plan']
                value['completed']=sum(t['status']=='observed' for t in value['trials'])
                value['failed']=0;value['unresolved']=sum(t.get('category')=='unresolved' for t in value['trials'])
                value['completion']=('failed_partial' if error else 'complete' if value['completed']==value['requested'] else 'cancelled_partial' if cancelled else 'partial')
                if error: value['operation_error']=error
                artifact=NativeExportArtifact('wbreview-'+secrets.token_hex(16)+'-receipt.json',canonical(value)+b'\n')
                publish_native_artifacts(owner,(artifact,))
                nonlocal receipt_ref, receipt_accepted
                receipt_ref=reference(artifact)
                receipt_accepted=[{'seed':seed,'selection':selection_value(selection)} for seed,selection in accepted.items()]
                control()
                return value
            for ordinal,seed in enumerate(plan.to_dict()['predeclared_seeds']):
                if should_cancel and should_cancel(): cancelled=True
                if cancelled: break
                session=factory.create(seed);binding.bind_session(session,seed,plan=plan)
                selection=None
                while not session.finished:
                    if should_cancel and should_cancel(): cancelled=True;break
                    retained=MelosGenerationProgress();phase='generation'
                    try:
                        execution=run_melos_generation(session,owner,selection=selection,
                            until=min(session.rt.now+product.CHECKPOINT_MINUTES,session.rt.end),progress=retained)
                        selection=execution.selection;accepted[seed]=selection
                        phase='control_ack';control()
                        phase='export'
                        prepared=project_melos_generation_record(selection,_captured_generation(owner,selection),factory,binding,seed)
                        if prepared.ledger != execution.layers:
                            raise ValueError('Fresh native projection differs from executed ledger')
                        publish_native_exports(owner,prepared)
                        record=_json(prepared.run_record)
                        artifacts={key:reference(item) for key,item in zip(('layers','result','run_record'),prepared.artifacts())}
                        trial=product._trial(Path(f'trial-{ordinal:04d}'),seed,record)
                        trial.update(selected_generation=selection_value(selection),artifacts=artifacts,
                                     source=artifacts['run_record']['leaf'],evidence_refs=[v['leaf'] for v in artifacts.values()])
                        records[seed]=trial
                        phase='receipt';result=publish()
                    except Exception as error:
                        if retained.selection is not None: accepted[seed]=retained.selection
                        # Export/control failure is not a scenario outcome. Never
                        # retry uncertain ack or treat partial bundle as published.
                        if phase not in ('control_ack','receipt'):
                            publish({'phase':phase,'error_type':type(error).__name__,'error':str(error)[:2000]})
                        raise
                    if progress:
                        progress({'requested':len(request['seed_plan']),'completed':result['completed'],
                                  'failed':result['failed'],'phase':'independent_trials',
                                  'execution_profile':product.PROFILE_ID,'seed':seed,
                                  'observed_minute':session.rt.now,'calendar_horizon':session.rt.end})
            return publish()
