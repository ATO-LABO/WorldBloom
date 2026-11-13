"""Immutable descriptors of saved output, never a mixture with live preview."""
from pathlib import Path
from copy import deepcopy
import hashlib
from engine.calendar_checkpoint import strict_json
from engine.execution_plan import digest

def _saved_family(header, rows, summary):
    """Recognize saved formats, not event labels or the reader's live world.

    This validates a legacy representation, not independent restart authority.
    Incomplete/unknown logical headers remain readable without semantic claims.
    """
    calendar_markers = ('contract' in header or 'calendar' in header
                        or 'contract' in summary
                        or any('absolute_minute' in row for row in rows))
    if calendar_markers:
        if (header.get('contract') != 'calendar_promises_v2'
                or type(header.get('schema_version')) is not int
                or header['schema_version'] != 2):
            raise ValueError('Unsupported saved calendar format')
        from engine.calendar_v2 import time_label
        time_label(header.get('calendar'), 0)  # Validates the compiled clock.
        if summary and summary.get('contract') != 'calendar_promises_v2':
            raise ValueError('Saved calendar summary contract differs')
        return 'calendar'
    required = {'seed', 'world', 'protagonist', 'antagonist', 'genome',
                'precedent_hash', 'engine_hash'}
    if (required <= header.keys() and type(header.get('seed')) is int
            and type(header.get('world')) is str
            and type(header.get('engine_hash')) is str
            and 'schema_version' not in header
            and header.get('execution_mode') in (None, 'cultural_festival_next_boundary_v1')):
        policy = header.get('terminal_policy')
        if policy is not None and (not isinstance(policy, dict)
                or set(policy) != {'target_as_observation', 'death_stops'}
                or any(type(value) is not bool for value in policy.values())
                or policy['death_stops'] is not True):
            raise ValueError('Unsupported saved logical terminal policy')
        return 'logical'
    return 'unknown'


def _calendar_boundary(folder, header, rows, summary, axis):
    """Check saved clock/status consistency without adopting or replaying state."""
    from engine.calendar_v2 import time_label
    calendar = header['calendar']
    previous = -1
    for row in rows[1:]:
        at = row.get('absolute_minute')
        if (row.get('kind') != 'event' or type(at) is not int or at < previous
                or type(row.get('turn')) is not int or row['turn'] != at
                or type(row.get('day')) is not int or row['day'] != at//1440+1
                or any(row.get(key) != value for key, value in time_label(calendar, at).items())):
            raise ValueError('Invalid saved calendar event clock')
        previous = at
    if not summary:
        return None
    now, done = summary.get('now'), summary.get('done')
    if (type(now) is not int or type(done) is not bool
            or not isinstance(summary.get('observations'), dict) or now < previous):
        raise ValueError('Invalid saved calendar boundary')
    time_label(calendar, now)
    # Old exports without their checkpoint can still be read, but the end of
    # their execution horizon is unavailable. Do not invent completion proof.
    end = None
    checkpoint = folder/'calendar-state.json'
    if checkpoint.is_file():
        from engine.calendar_checkpoint import read
        payload = read(checkpoint)
        if (not isinstance(payload, dict) or set(payload) != {'compatibility', 'state'}
                or not isinstance(payload['compatibility'], dict)
                or payload['compatibility'].get('family') != 'calendar_promises_v2'
                or type(payload['compatibility'].get('schema_version')) is not int
                or payload['compatibility']['schema_version'] != 3
                or not isinstance(payload['state'], dict)):
            raise ValueError('Unsupported saved calendar checkpoint format')
        state = payload['state']
        end = state.get('end')
        if (type(end) is not int or end < now
                or type(state.get('now')) is not int or state['now'] != now
                or type(state.get('done')) is not bool or state['done'] != done
                or state.get('rows') != rows[1:]
                or state.get('observations') != summary['observations']):
            raise ValueError('Saved calendar checkpoint and exports differ')
        time_label(calendar, end)
        if done and now != end:
            raise ValueError('Saved calendar completed before its horizon')
    if axis is not None:
        if (not isinstance(axis, dict) or set(axis) != {'unit', 'start', 'end'}
                or axis['unit'] != 'minute' or type(axis['start']) is not int
                or type(axis['end']) is not int or not 0 <= axis['start'] <= now <= axis['end']
                or end is not None and axis['end'] != end
                or done and now != axis['end']):
            raise ValueError('Invalid saved calendar descriptor clock')
        time_label(calendar, axis['start']); time_label(calendar, axis['end'])
        end = axis['end']
    return now, done, end


def _saved_projection(folder, *, calendar_axis=None):
    folder=Path(folder);path=folder/'layers.jsonl'
    data=path.read_bytes();rows=[strict_json(line) for line in data.decode('utf-8').splitlines()]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Empty or invalid saved ledger')
    header=rows[0] if rows[0].get('kind')=='header' else {}
    summary_path=folder/'summary.json'
    summary=strict_json(summary_path.read_text(encoding='utf-8')) if summary_path.is_file() else {}
    if not isinstance(summary, dict):raise ValueError('Invalid saved summary')
    family = _saved_family(header, rows, summary)
    boundary = _calendar_boundary(folder, header, rows, summary, calendar_axis) if family == 'calendar' else None
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
        if family != 'logical' or row.get('kind') != 'event':
            continue
        if row.get('verb') in {'ending', 'aborted', 'time_limit', 'observation_finished', 'target_observed'}:
            if (type(row.get('turn')) is not int or row['turn'] < 0
                    or not isinstance(row.get('details'), dict)):
                raise ValueError('Invalid saved logical terminal event')
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
    if boundary is not None:
        now, done, end = boundary
        record['observed_through'] = now
        record['stop_reason'] = ('observation_limit' if end is not None else 'unknown') if done else 'paused'
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
    folder=simulation.out_dir
    kernel=simulation.time_kernel;runtime=simulation.slot_runtime
    axis = {'unit':kernel.axis.unit, 'start':kernel.axis.start, 'end':kernel.axis.end}
    record,summary=_saved_projection(folder, calendar_axis=axis)
    from engine.calendar_runtime import CalendarRuntime
    if isinstance(runtime, CalendarRuntime):
        if (kernel.axis.unit != 'minute' or type(runtime.done) is not bool
                or summary.get('contract') != 'calendar_promises_v2'
                or summary.get('now') != kernel.now or summary.get('done') is not runtime.done
                or summary.get('observations') != runtime.observations
                or record['ledger']['sha256'] != hashlib.sha256(runtime._layers_bytes()).hexdigest()
                or runtime.done and kernel.now != kernel.axis.end):
            raise ValueError('Saved calendar boundary differs from runtime')
        record.update(actual_terminal=None, observed_targets=[],
                      stop_reason='observation_limit' if runtime.done else 'paused')
    elif kernel.axis.unit != 'logical_step':
        raise ValueError('Unsupported saved runtime time contract')
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
    record=strict_json(descriptor.read_text(encoding='utf-8'))
    if (not isinstance(record, dict) or type(record.get('schema_version')) is not int
            or record['schema_version'] != 1 or record.get('descriptor_scope') != 'saved_boundary_v1'):
        raise ValueError('Unsupported saved run descriptor format')
    expected=record.pop('saved_revision_hash',None)
    if expected!=digest(record):raise ValueError('Changed saved run descriptor')
    record['saved_revision_hash']=expected
    if hashlib.sha256((folder/'layers.jsonl').read_bytes()).hexdigest()!=record['ledger']['sha256']:
        raise ValueError('Saved descriptor and ledger revisions differ')
    for name,field in [('summary.json','summary_sha256'),('calendar-state.json','checkpoint_sha256'),('communication-state.json','communication_state_sha256')]:
        if field in record and hashlib.sha256((folder/name).read_bytes()).hexdigest()!=record[field]:
            raise ValueError('Saved descriptor and '+name+' revisions differ')
    projection, summary = _saved_projection(folder, calendar_axis=record.get('time_axis'))
    fields = ['actual_terminal', 'stop_reason', 'observed_targets']
    if summary.get('contract') == 'calendar_promises_v2':
        if record.get('time_axis') is None:
            raise ValueError('Missing saved calendar descriptor clock')
        fields += ['observed_through', 'observations']
    if any(record.get(key) != projection[key] for key in fields):
        raise ValueError('Saved run descriptor semantics differ from saved output; '
                         'legacy observer-label descriptors require explicit regeneration')
    return deepcopy(record)
