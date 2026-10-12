"""Opt-in choice-genome adapter reusing WorldBloom's evaluation/breeding/archive.

No second simulator: all runs use Simulation/SlotRuntime and the same fixed
subjective policy. No personality mutation, absence bonus, Jev, or GPU use.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import random
import statistics

from gapengine.festival_choices import ChoiceGenome
from gapengine.qd import Archive,read_rows
from scripts.cultural_festival_probe import dump,digest,input_manifest,source_manifest,PROJECT
from scripts.cultural_festival_state_search import run

ADAPTER="cultural_festival_choices_v1"


def completed_evaluation(rows):
    """Join by (actor,start_t); score completed effects, never queued starts."""
    starts={}
    for row in rows:
        if row.get('kind')=='decision':
            key=(row['subject'],row['details']['start_t'])
            if key in starts:raise ValueError('Duplicate actor start')
            starts[key]=row
    completed=[];sequence=[];seen=set()
    for row in rows:
        if row.get('verb')!='action_completed':continue
        d=row['details'];key=(row['subject'],d['start_t'])
        start=starts.get(key)
        if (start is None or key in seen or start['verb']!=d['action'] or start['args']!=d['args']
                or d['completed_t']!=d['start_t']+1 or row['turn']-1!=d['completed_t']
                or start['details']['completes_t']!=d['completed_t']):
            raise ValueError('Unmatched/duplicate/mis-timed completion')
        seen.add(key);completed.append(row)
        delta=row.get('delta',{})
        effective=row['result']=='completed' and bool(d.get('activity_delta') or
            delta.get('actor') or delta.get('targets') or delta.get('objective') or delta.get('relations'))
        if effective:
            subtype=':'.join(d['args'][1:3]) if d['action']=='dialogue' else d['action']
            sequence.append((row['subject'],d['action'],subtype))
    if seen!=set(starts):raise ValueError('Uncompleted start at observation end')
    opening=next(r['details'] for r in rows if r.get('verb')=='opening_check')
    state=next(r['state'] for r in reversed(rows) if r['kind']=='activity_snapshot')
    # Opening components remain separate. No score component rewards absence.
    ready=opening['readiness']; consent=opening['declines_to_perform']
    line=any(r['topic']=='終盤短縮希望' and r['intent']=='accept' and r['completed_t']<=18
             and r['from']=='蒼' for r in state['responses'])
    learned=sum(r.get('verb')=='learn_fact' and r.get('subject')=='蒼' for r in rows)
    components={
        'primary_presence':.10*int(opening['primary_presence']),
        'costume_prepared':.20*int(ready['costume_prepared']),
        'practice_completed':.15*min(1,ready['practice_completed']/3),
        'sound_checked':.15*int(ready['sound_checked']),
        'partner_present':.05*int(ready['ren_present']),
        'performance_acceptance':.10*int(consent is False),
        'line_change_acceptance':.10*int(line),
        # Contributions are observed progress, not panel completion.
        'exhibit_contribution':.05*int(state['exhibit_contributions']['蒼']>0),
        'knowledge_acquired':.10*min(1,learned/3),
    }
    failed=[r for r in completed if r['result']!='completed']
    fitness=round(sum(components.values()),12) if not failed else 0.0
    lead_actions=Counter(verb for actor,verb,_ in sequence if actor=='蒼')
    groups={
        'coordination':lead_actions['dialogue'],
        'preparation':lead_actions['practice']+lead_actions['costume_prepare'],
        'exhibition':lead_actions['exhibit_help'],
        'movement':lead_actions['move'],
    }
    category=sorted(groups,key=lambda g:(-groups[g],g))[0]
    effects_by_tick=Counter(r['details']['completed_t'] for r in completed
        if r['result']=='completed' and (r['details'].get('activity_delta') or r.get('delta',{}).get('actor')
                                         or r.get('delta',{}).get('targets')))
    volatility=round(statistics.pstdev([effects_by_tick[t] for t in range(1,23)])/4,12)
    return {'fitness':fitness,'fitness_components':components,'opening':deepcopy(opening),
        'effective_sequence':[list(x) for x in sequence],'matched_completions':len(completed),
        'failed_completions':len(failed),'descriptor_category':category,'volatility':volatility,
        'absence_reward':0,'panel_completion_claimed':False,
        'fitness_scope':'fixed audit/progress metric, not Jev plausibility or full narrative quality'}


def run_individual(job):
    if job.get('rationality_cfg') or job.get('growth_enabled'):
        raise ValueError('Jev/growth disabled for this adapter')
    genome=ChoiceGenome.from_dict(job['genome'])
    project=Path(job.get('world_path',PROJECT/'world.yaml')).resolve().parent
    if Path(job.get('subjects_dir',project/'subjects')).resolve()!=project/'subjects':
        raise ValueError('Subjects must come from the same frozen project')
    if job.get('expected_input_manifest') is not None and input_manifest(project)!=job['expected_input_manifest']:
        raise ValueError('Frozen input mismatch')
    root=Path(job['logical_root']);out=Path(job['out_dir'])
    results=[]
    for seed in job['seeds']:
        directory=out/f'seed-{seed}'
        result=run(directory,int(seed),choice_genome=genome,project=project)
        evaluation=completed_evaluation(read_rows(directory/'layers.jsonl'))
        assert result['audit']['failures']==[]
        # Timings are observational telemetry, not deterministic GA evaluations.
        result.pop('performance', None)
        result['evaluation_id']=f"g{job['generation']}/ind-{job['index']}/seed-{seed}"
        result['temporal_evaluation']=evaluation
        result['ga_individual_evaluation']=True
        dump(directory/'run.json',result)
        results.append({**result,'layers_path':(directory/'layers.jsonl').relative_to(root).as_posix(),
            'quality':evaluation['fitness'],'reached':evaluation['opening']['primary_presence'],
            'effective_sequence':evaluation['effective_sequence'],'volatility':evaluation['volatility']})
    output={'candidate_id':f"g{job['generation']}/ind-{job['index']}",'index':int(job['index']),'generation':int(job['generation']),'parents':list(job.get('parents',[])),
        'genome':genome.to_dict(),'genome_kind':'action_choices_only','runs':results,
        'quality':statistics.mean(r['quality'] for r in results),
        'shaped':statistics.mean(r['quality'] for r in results),
        'reach_rate':statistics.mean(int(r['reached']) for r in results),
        'volatility':statistics.mean(r['volatility'] for r in results),
        'category':results[0]['temporal_evaluation']['descriptor_category'],
        'quality_scope':'completed temporal goal progress/audit heuristic',
        'reach_rate_scope':'15:00 primary presence only, readiness/consent recorded separately'}
    dump(out/'result.json',output)
    return output


def evolve_choices(cfg,*,observer=None):
    """Continue only this adapter, using strict generation-committed state.

    Generations is the total target, not a count to append. The existing
    88-quantile codec, fitness and breeding RNG order remain unchanged.
    """
    from gapengine.evolve import _evaluate_jobs,_rng_state_to_json,_rng_state_from_json
    from gapengine import festival_checkpoint as checkpoint
    allowed={'search_adapter','out','population','generations','ga_seed','seeds',
             'processes','project','resume','rationality_cfg','growth_enabled'}
    if set(cfg)-allowed:
        raise ValueError('Unsupported Choice configuration keys: '+','.join(sorted(set(cfg)-allowed)))
    if cfg.get('search_adapter',ADAPTER)!=ADAPTER:
        raise ValueError('Wrong Choice execution adapter')
    size,generations=cfg.get('population',4),cfg.get('generations',2)
    ga_seed=cfg.get('ga_seed',20261012)
    seeds=cfg.get('seeds',[0])
    if (type(size) is not int or not 2<=size<=4 or type(generations) is not int
            or not 1<=generations<=4 or type(ga_seed) is not int
            or not isinstance(seeds,list) or not 1<=len(seeds)<=2
            or any(type(seed) is not int for seed in seeds) or len(set(seeds))!=len(seeds)):
        raise ValueError('Choice CPU adapter requires population2..4/generations1..4/unique1..2 integer seeds')
    if (cfg.get('rationality_cfg') or cfg.get('growth_enabled')
            or type(cfg.get('processes',1)) is not int or cfg.get('processes',1)!=1
            or type(cfg.get('resume',False)) is not bool):
        raise ValueError('Only single-process CPU/no-judge/no-growth Choice trial supported')
    requested=Path(cfg['out']).absolute()
    if requested.is_symlink():
        raise ValueError('Symlink Choice output forbidden')
    out=requested.resolve()
    project=Path(cfg.get('project',PROJECT)).resolve()
    config={'population':size,'ga_seed':ga_seed,'seeds':seeds,'processes':1}
    with checkpoint.writer_lock(out):
        sources,inputs=checkpoint.source_fingerprint(),checkpoint.input_fingerprint(project)
        if cfg.get('resume',False):
            state=checkpoint.validate(out,config,sources,inputs)
            if generations<state['completed_generations']:
                raise ValueError('Choice total generation target precedes checkpoint')
            checkpoint.cleanup(out,state)
        else:
            if out.exists():
                raise ValueError('Fresh output directory required; use resume for Choice checkpoints')
            out.mkdir()
            state=checkpoint.save(out,checkpoint.new_state(config,sources,inputs))
        rng=random.Random()
        rng.setstate(_rng_state_from_json(state['rng_state']))
        archive=Archive.from_dict(state['archive'],genome_codec=ChoiceGenome)
        previous=state['previous_results']
        population=[(ChoiceGenome.from_dict(p['genome']),p['parents']) for p in state['population']]
        for generation in range(state['completed_generations'],generations):
            if generation:
                population=checkpoint.breed(size,archive,previous,rng)
            stage=checkpoint.start_generation(out,state)
            jobs=[{'search_adapter':ADAPTER,'index':i,'generation':generation,'genome':g.to_dict(),
                'parents':parents,'seeds':seeds,'out_dir':str(stage/f'g{generation}'/f'ind-{i}'),
                'logical_root':str(stage),'world_path':str(project/'world.yaml'),
                'subjects_dir':str(project/'subjects'),'expected_input_manifest':input_manifest(project)}
                for i,(g,parents) in enumerate(population)]
            results=_evaluate_jobs(jobs,1,observer=observer)
            if (sources!=checkpoint.source_fingerprint() or inputs!=checkpoint.input_fingerprint(project)):
                raise ValueError('Choice source/input changed during generation; checkpoint not committed')
            checkpoint.insert_results(archive,results)
            # The same candidate results (including the assigned archive cell)
            # are published to both the generation journal and individual log.
            for result in results:
                dump(stage/result['candidate_id']/'result.json',result)
            dump(stage/f'g{generation}'/'results.json',results)
            next_state={**state,'completed_generations':generation+1,
                'rng_state':_rng_state_to_json(rng),'archive':archive.to_dict(),
                'population':checkpoint.population_dict(population),
                'previous_results':results,'history':[*state['history'],results],
                'artifacts':{**state['artifacts'],f'g{generation}':
                    checkpoint.files_manifest(stage/f'g{generation}')}}
            state=checkpoint.publish_generation(stage,out,generation,next_state)
            previous=results
            checkpoint.exports(out,state)
        checkpoint.exports(out,state)
        return archive
