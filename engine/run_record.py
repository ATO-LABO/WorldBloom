"""Immutable descriptors of saved output, never a mixture with live preview."""
from pathlib import Path
from copy import deepcopy
import hashlib,json
from engine.execution_plan import digest

def _saved_projection(folder):
    folder=Path(folder);path=folder/'layers.jsonl'
    data=path.read_bytes();rows=[json.loads(line) for line in data.decode('utf-8').splitlines()]
    if not rows:raise ValueError('Empty saved ledger')
    header=rows[0] if rows[0].get('kind')=='header' else {}
    summary_path=folder/'summary.json'
    summary=json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.is_file() else {}
    record={'schema_version':1,'run_id':folder.name,'time_axis':None,'observed_through':None,
            'configured_target_ending':None,'execution_plan':header.get('execution_plan'),
            'execution_plan_hash':header.get('execution_plan_hash'),'verified_execution':header.get('verified_execution'),
            'ledger':{'path':'layers.jsonl','sha256':hashlib.sha256(data).hexdigest()},
            'actual_terminal':None,'stop_reason':'unknown','observed_targets':[], 'observations':deepcopy(summary.get('observations',{})),
            'provider_identity':deepcopy(header.get('decision_provider_identity')),
            'input_hashes':None,'run_spec_hash':None,'time_contract_hashes':deepcopy(header.get('time_contract_hashes')),
            'engine_hash':header.get('engine_hash'),'descriptor_scope':'legacy_saved_output_only'}
    for number,row in enumerate(rows,1):
        at=row.get('absolute_minute')
        if at is None and type(row.get('turn')) is int:at=max(0,row['turn']-1)
        if at is not None:record['observed_through']=at
        observation_only = (row.get('verb')=='target_observed' or
            (row.get('verb')=='ending' and (header.get('terminal_policy') or {}).get('target_as_observation')
             and (row.get('id') or row.get('event_id'))!='time_limit'))
        if observation_only:
            record['observed_targets'].append({'verb':row['verb'],'line':number,'event_id':row.get('id') or row.get('event_id'),
                'observed_through':at,'details':deepcopy(row.get('details',{}))})
        elif row.get('verb') in {'ending','aborted','time_limit','observation_finished'}:
            record['actual_terminal']={'verb':row['verb'],'line':number,'event_id':row.get('id') or row.get('event_id'),
                                       'details':deepcopy(row.get('details',{}))}
            record['stop_reason']='time_limit' if (row.get('id') or row.get('event_id'))=='time_limit' else row['verb']
    if type(summary.get('now')) is int:
        record['observed_through']=summary['now']
        if record['actual_terminal'] is None:record['stop_reason']='observation_limit' if summary.get('done') else 'paused'
    if record['execution_plan'] is not None and record['verified_execution'] is None:
        record['declared_plan']=record['execution_plan']
        record['execution_plan']=None
        record['execution_plan_hash']=None
    return record,summary

def commit_run_record(simulation):
    """Called only after ledger/summary publication, at a saved boundary."""
    from execution.provenance import atomic_json
    if simulation.execution_plan is not None and simulation.verified_execution is None:
        raise ValueError('An unbound execution plan cannot be committed as verified output')
    folder=simulation.out_dir;record,summary=_saved_projection(folder)
    kernel=simulation.time_kernel;runtime=simulation.slot_runtime
    record.update(time_axis={'unit':kernel.axis.unit,'start':kernel.axis.start,'end':kernel.axis.end},
                  observed_through=kernel.now,configured_target_ending=deepcopy(simulation.world.target_ending),
                  execution_plan=None if simulation.execution_plan is None else simulation.execution_plan.to_dict(),
                  execution_plan_hash=None if simulation.execution_plan is None else simulation.execution_plan.sha256,
                  verified_execution=deepcopy(simulation.verified_execution),engine_hash=simulation._engine_hash(),
                  descriptor_scope='saved_boundary_v1')
    if runtime is not None and hasattr(runtime,'inputs'):
        inputs=runtime.inputs();record['input_hashes']=deepcopy(inputs['files'])
        record['run_spec_hash']=digest(inputs);record['time_contract_hashes']=deepcopy(inputs.get('time_contract_hashes'))
        record['provider_identity']=deepcopy(inputs.get('decision_provider_identity'))
    else:
        inputs={'world':hashlib.sha256(simulation.world.source.read_bytes()).hexdigest(),
                'initial_subject_specs':digest(simulation._initial_subject_specs),'seed':simulation.seed,
                'target_ending':simulation.world.target_ending,'genome':simulation._genome_dict(),
                'precedent_hash':simulation._precedent_hash(),'execution_plan':record['execution_plan'],
                'terminal_policy':simulation.terminal_policy}
        record['input_hashes']=inputs;record['run_spec_hash']=digest(inputs)
    # Equivalent saved episodes must remain byte-identical after resume even
    # when their output folders differ. The enclosing job owns its job/run ID.
    record['run_id']='saved-'+digest({'ledger':record['ledger']['sha256'],'run_spec':record['run_spec_hash']})
    summary_path=folder/'summary.json'
    if summary_path.is_file():record['summary_sha256']=hashlib.sha256(summary_path.read_bytes()).hexdigest()
    checkpoint=folder/'calendar-state.json'
    if checkpoint.is_file():record['checkpoint_sha256']=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    communication=folder/'communication-state.json'
    if communication.is_file():record['communication_state_sha256']=hashlib.sha256(communication.read_bytes()).hexdigest()
    record['saved_revision_hash']=digest(record)
    atomic_json(folder/'run-record.json',record)
    return deepcopy(record)

def describe_run(simulation):
    folder=simulation.out_dir;descriptor=folder/'run-record.json'
    if not descriptor.is_file():
        # Old output remains readable, with unavailable metadata left unknown.
        return deepcopy(_saved_projection(folder)[0])
    record=json.loads(descriptor.read_text(encoding='utf-8'))
    expected=record.pop('saved_revision_hash',None)
    if expected!=digest(record):raise ValueError('Changed saved run descriptor')
    record['saved_revision_hash']=expected
    if hashlib.sha256((folder/'layers.jsonl').read_bytes()).hexdigest()!=record['ledger']['sha256']:
        raise ValueError('Saved descriptor and ledger revisions differ')
    for name,field in [('summary.json','summary_sha256'),('calendar-state.json','checkpoint_sha256'),('communication-state.json','communication_state_sha256')]:
        if field in record and hashlib.sha256((folder/name).read_bytes()).hexdigest()!=record[field]:
            raise ValueError('Saved descriptor and '+name+' revisions differ')
    return deepcopy(record)
