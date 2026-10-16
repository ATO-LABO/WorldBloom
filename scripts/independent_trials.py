"""Frozen CPU independent trials through the same Simulation/TimeActionKernel."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import argparse,json,time,socket
from engine.execution_plan import ExecutionPlan
from engine.world import World
from engine.subject import Subject
from engine.sim import Simulation
from engine.time_kernel import TimeActionKernel
from execution.provenance import atomic_json,verify_files,sha256,canonical
from execution.independent_review import receipt

def run_trials(run,progress=None,should_cancel=None):
    run=Path(run)
    manifest=json.loads((run/'review-manifest.json').read_text(encoding='utf-8'))
    if 'execution_profile' in manifest['config']:
        from execution.melos_product import run_trials as run_profile
        return run_profile(run,progress,should_cancel)
    verify_files(run/'runtime',manifest['runtime']['files'])
    verify_files(run/'inputs',manifest['inputs']['files'])
    plan=ExecutionPlan(json.loads((run/'execution-plan.json').read_text(encoding='utf-8')))
    if plan.sha256!=manifest['admission']['execution_plan_sha256']:raise ValueError('Changed execution plan')
    records=[];project=run/'inputs/projects'/manifest['config']['project_id']
    for ordinal,seed in enumerate(plan.to_dict()['predeclared_seeds']):
        if should_cancel and should_cancel():break
        case=run/'trials'/f'trial-{ordinal:04d}'
        case.mkdir(parents=True,exist_ok=False)
        try:
            world=World.from_yaml(project/'world.yaml')
            people={p.id:p for p in (Subject.from_yaml(f) for f in sorted((project/'subjects').glob('*.yaml')))}
            simulation=Simulation(seed,world,people,case,execution_plan=plan.to_dict(),
                                  terminal_policy={'target_as_observation':True,'death_stops':True})
            simulation.run()
            if not isinstance(simulation.time_kernel,TimeActionKernel):raise AssertionError('Shared kernel missing')
            record=simulation.run_record()
            atomic_json(case/'run-record.json',record)
            outcome=record['actual_terminal'];kind=outcome['verb'] if outcome else record['stop_reason']
            trial={'id':case.name,'seed':seed,'status':'observed','category':kind,
                   'run_record':record,'source':f'trials/{case.name}/run-record.json',
                   'evidence_refs':[f'trials/{case.name}/layers.jsonl']}
        except Exception as error:
            trial={'id':case.name,'seed':seed,'status':'execution_failure','category':'execution_failure',
                   'error_type':type(error).__name__,'error':str(error),
                   'source':f'trials/{case.name}/failure.json','evidence_refs':[f'trials/{case.name}/failure.json']}
            atomic_json(case/'failure.json',trial)
        atomic_json(case/'classification.json',trial)
        records.append(trial)
        atomic_json(run/'partial-review.json',receipt(run,records))
        if progress:progress({'requested':plan.to_dict()['planned_trials'],'completed':len(records),
                              'failed':sum(r['status']=='execution_failure' for r in records),'phase':'independent_trials'})
    complete=len(records)==plan.to_dict()['planned_trials']
    result=receipt(run,records)
    result['completion']='complete' if complete else 'cancelled_partial'
    atomic_json(run/'partial-review.json',result)
    if complete:atomic_json(run/'review-result.json',result)
    atomic_json(run/'review-completion.json',{'complete':complete,'requested':plan.to_dict()['planned_trials'],
                'completed':len(records),'failed':sum(r['status']=='execution_failure' for r in records)})
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--control',required=True)
    p.add_argument('--job',required=True);p.add_argument('--nonce',required=True);a=p.parse_args()
    from execution import worker
    folder=Path(a.control)/'jobs'/a.job
    def progress(value):worker.change(folder.parent,folder,a.nonce,progress=value,heartbeat=time.time())
    def cancelled():return worker.read_job(folder.parent,folder).get('cancel_requested_at') is not None
    # A review admission never reads model settings; enforce that at runtime too.
    def deny(*args,**kwargs):raise RuntimeError('Network is disabled for CPU independent trials')
    socket.socket.connect=deny;socket.create_connection=deny
    run_trials(a.run,progress,cancelled)
    return 0

if __name__=='__main__':raise SystemExit(main())
