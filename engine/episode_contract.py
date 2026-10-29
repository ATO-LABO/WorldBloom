"""Bind execution provenance to an actual episode before writing output."""
from pathlib import Path
from engine.execution_plan import digest
from engine.decision_provider import provider_snapshot
import hashlib

def native_profile(world):
    field = world.get if isinstance(world, dict) else lambda key: getattr(world, key, None)
    return 'next_boundary' if field('slot_execution') is not None else 'timed_logical_v1' if field('action_timing') is not None else 'legacy_instant'

def factory_identities(operation_profile,*,engine_files=None,commands=None,provider=None):
    if engine_files is None:
        root=Path(__file__).resolve().parent
        engine_files={'engine/'+p.name:p.read_bytes() for p in sorted(root.glob('*.py'))}
    source=digest({n:hashlib.sha256(b).hexdigest() for n,b in engine_files.items() if n.startswith('engine/') and n.endswith('.py')})
    explicit=operation_profile=='explicit_calendar'
    profile={'operation_profile':operation_profile,'genome':None,'external_judge':False}
    actual_provider={'id':'native_base_candidates_v1','version':1,'source_hash':source,'config_hash':digest(profile)}
    if explicit:actual_provider={'id':'explicit_commands_v1','version':1,'source_hash':source,'config_hash':digest(commands or [])}
    if provider is not None:
        identity=provider_snapshot(provider)['identity']
        actual_provider={'id':identity['adapter'],'version':identity['schema_version'],'source_hash':identity['runtime_source_hash'],
                         'config_hash':digest({'config_hash':identity['config_hash'],'table_hash':identity['table_hash'],'rng_owner':identity['rng_owner']})}
    sampler={'id':'explicit_command_replay_v1' if explicit else 'seeded_native_simulation_v1','version':1,
             'source_hash':source,'config_hash':digest({'scheme':'explicit' if explicit else 'seeded_shared_rng'})}
    return sampler,actual_provider

def bind_episode(simulation,axis):
    if simulation.execution_plan is None:return None
    value=simulation.execution_plan.to_dict()
    if simulation.seed not in value['predeclared_seeds']:raise ValueError('Episode seed is outside the execution plan')
    o=value['observation']
    if (o['unit'],o['start'],o['end'])!=(axis.unit,axis.start,axis.end):raise ValueError('Execution plan time axis differs')
    terminal=simulation.terminal_policy or {'target_as_observation':False,'death_stops':True}
    if o['stop_contract_hash']!=digest(terminal):raise ValueError('Execution plan stop contract differs')
    runtime=simulation.slot_runtime;explicit=runtime is not None and hasattr(runtime,'commands')
    if explicit and value['execution_kind'] not in ('explicit_witness','replay'):
        raise ValueError('Explicit witness is not an independent sampler or production search')
    if any(p.policy is not None for p in simulation.subjects.values()):raise ValueError('Policy adapter requires verified binding')
    profile='explicit_calendar' if explicit else native_profile(simulation.world)
    sampler,provider=factory_identities(profile,commands=runtime.commands if explicit else None,provider=simulation.decision_provider)
    if value['sampler']!=sampler:raise ValueError('Execution plan sampler identity differs')
    if value['provider']!=provider:raise ValueError('Execution plan provider identity differs')
    return {'schema_version':1,'execution_kind':value['execution_kind'],'seed':simulation.seed,
            'axis':{'unit':axis.unit,'start':axis.start,'end':axis.end},'sampler':sampler,'provider':provider,
            'stop_contract_hash':o['stop_contract_hash'],'execution_plan_hash':simulation.execution_plan.sha256}
