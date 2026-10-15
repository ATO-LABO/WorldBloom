"""Summarize spans without adding overlapping process times as elapsed time."""
from collections import defaultdict
import json
from pathlib import Path
import sys

root=Path(sys.argv[1])
targets={
 'test_evolution_execution.EvolutionHttpPersonalityGrowthTests.test_personality_growth_true_completes_and_writes_growth_rows',
 'test_evolution_execution.EvolutionHttpRouteRhoTests.test_route_rho_from_job_reaches_the_ga',
 'test_genre_workspace.GenreWorkspaceTests.test_draft_check_uses_unsaved_selected_genre_and_never_saves'}
rows=[]
starts={}
ends={}
all_test_seconds=0.
all_test_count=0
for path in (root/'phases').glob('*.jsonl'):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try: r=json.loads(line)
            except json.JSONDecodeError: continue # A live observer can have a partial final write.
            if r['event']=='test.end':
                all_test_count+=1
                all_test_seconds+=r['seconds']
            keep=r.get('test') in targets
            key=(r['pid'],r['process_created'],r.get('span'))
            if r['event']=='begin' and keep: starts[key]=r
            if r['event']=='end' and key in starts: ends[key]=r
            if keep or r['event'] in ('test.failure_before_cleanup','failure.owned_jobs','observer.start','observer.stop'):
                rows.append(r)
groups=defaultdict(list)
for key,start in starts.items():
    if key in ends:
        groups[(start['test'],start['occurrence'],start['pid'],start['process_created'],start['phase'])].append((start,ends[key]))
summary=[]
for (test,occurrence,pid,created,name),spans in groups.items():
    summary.append(dict(test=test,occurrence=occurrence,pid=pid,process_created=created,phase=name,count=len(spans),
                        inclusive_sum_seconds=sum(e['seconds'] for _,e in spans),
                        max_seconds=max(e['seconds'] for _,e in spans),
                        first=min(s['mono'] for s,_ in spans),last=max(e['mono'] for _,e in spans)))
failure_windows=[]
for failure in [r for r in rows if r['event']=='test.failure_before_cleanup']:
    cutoff=failure['mono']
    active={}
    for r in sorted((r for r in rows if r['event']=='lock' and r['mono']<=cutoff and r['test']==failure['test'] and r['occurrence']==failure['occurrence']),key=lambda r:r['mono']):
        k=(r['pid'],r['process_created'],r['tid'],r['lock_id'],r['operation'])
        if r['stage']=='acquired':active[k]=r
        elif r['stage']=='released':active.pop(k,None)
    failure_windows.append(dict(test=failure['test'],occurrence=failure['occurrence'],mono=cutoff,
        open_phases=[s for k,s in starts.items() if s['test']==failure['test'] and s['mono']<=cutoff and (k not in ends or ends[k]['mono']>cutoff)],
        unmatched_lock_acquisitions=list(active.values())))
report=dict(source=root.name,method='Per-process inclusive spans; nested and cross-process sums are not elapsed time. Unmatched events are observations, not proof of a continuing OS lock.',
            all_sequential_test_count=all_test_count,all_sequential_test_seconds=all_test_seconds,
            test_elapsed=[r for r in rows if r['event']=='test.end'],
            failures=[r for r in rows if r['event'] in ['test.failure_before_cleanup','failure.owned_jobs']],
            before_cleanup_failure_windows=failure_windows,
            unfinished=[s for k,s in starts.items() if k not in ends],
            dropped_events=[r for r in rows if r['event']=='observer.stop' and r['dropped']],
            summary=sorted(summary,key=lambda r:(r['test'],r['occurrence'],-r['inclusive_sum_seconds'])))
(root/'analysis.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
important={'ConfigStore.prepare_run','provenance.code_snapshot','provenance.materialize','provenance.verify_files','ConfigStore.verify_run','ConfigStore._prepare','ConfigStore.preview','configs._capture_inputs','configs._describe','genre.validate_draft','temp.cleanup','http.server.end_headers','http.server.body_write','os.fsync','legacy.trace_lock'}
print(json.dumps({'elapsed':report['test_elapsed'],'unfinished_count':len(report['unfinished']),'dropped':report['dropped_events'],
                  'phases':[{k:v for k,v in r.items() if k not in ('first','last')} for r in report['summary'] if r['phase'] in important]},indent=2))
