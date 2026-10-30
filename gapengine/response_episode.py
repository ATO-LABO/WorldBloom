"""Predeclared independent response trials, retaining inputs and every outcome."""
from pathlib import Path
import hashlib
import json
import shutil

from engine.deliberation import digest
from engine.episode_contract import factory_identities
from engine.execution_plan import ExecutionPlan
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World


def load_response(world_path):
    path = Path(world_path)
    world = World.from_yaml(path)
    if world.responses is None or world.action_graph_enabled or world.effect_library:
        raise ValueError('Response runner requires a self-contained explicit response world')
    files = sorted((path.parent/'subjects').glob('*.yaml'))
    if not 1 <= len(files) <= 40: raise ValueError('Bounded response cast required')
    subjects = [Subject.from_yaml(p) for p in files]
    people = {s.id:s for s in subjects}
    if len(people) != len(subjects): raise ValueError('Duplicate response actor')
    world.bind_subjects(people)
    return world, people


def run_batch(world_path, output, seeds):
    if (not isinstance(seeds,list) or not 1 <= len(seeds) <= 1000
            or any(type(s) is not int or s < 0 for s in seeds) or len(set(seeds)) != len(seeds)):
        raise ValueError('Distinct predeclared integer seeds required')
    path, output = Path(world_path).resolve(), Path(output)
    world, _ = load_response(path)
    sampler, provider = factory_identities('legacy_instant')
    classification = {'id':'concern_response_v1','version':1,
                      'source_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      'config_hash':digest({'outcome':'actual_state_and_temporal_history'})}
    plan = ExecutionPlan({'schema_version':1,'execution_kind':'independent_trials',
        'sampler':sampler,'provider':provider,'classification':classification,
        'predeclared_seeds':seeds,'planned_trials':len(seeds),
        'selection':dict.fromkeys(['fitness_selection','breeding','keep_filter','canon_feedback'],False),
        'observation':{'unit':'logical_step','start':0,'end':world.days*len(world.slots)-1,
                       'stop_contract_hash':digest({'death_stops':True,'target_as_observation':False})}})
    output.mkdir(parents=True, exist_ok=False)
    inputs = output/'inputs';(inputs/'subjects').mkdir(parents=True)
    manifest = {}
    for original in [path,*sorted((path.parent/'subjects').glob('*.yaml'))]:
        rel = original.relative_to(path.parent);target = inputs/rel
        shutil.copyfile(original,target);manifest[rel.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    root = Path(__file__).resolve().parents[1]
    sources = {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((root/'engine').glob('*.py'))}
    def save(name, value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    save('input-manifest.json',{'inputs':manifest,'sources':sources,'runner':classification['source_hash']})
    save('execution-plan.json',plan.to_dict())
    def evaluate(seed):
        current, people = load_response(inputs/'world.yaml')
        sim = Simulation(seed,current,people,output/f'seed-{seed:04}',record_explanations=True,execution_plan=plan.to_dict())
        ledger = sim.run()
        rows = [json.loads(line) for line in ledger.read_text(encoding='utf8').splitlines()]
        snapshot = current.responses.snapshot()
        (ledger.parent/'response-state.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        actual = current.concerns.world_report(current)
        satisfied = (all(c['state']=='true' for c in actual)
                     and all(w['status']=='satisfied' for w in snapshot['temporal'].values()))
        return {'status':'satisfied' if satisfied else 'unmet',
                'actual_conditions':actual,'responses':snapshot,
                'decisions':[{'turn':r['turn'],'subject':r['subject'],'verb':r['verb'],'args':r['args'],
                              'result':r['result'],'response':r.get('details',{}).get('response')}
                             for r in rows if r.get('kind')=='decision' and (r.get('details',{}).get('response') or r['verb']=='share_knowledge')],
                'ledger':ledger.relative_to(output).as_posix(),'ledger_sha256':hashlib.sha256(ledger.read_bytes()).hexdigest(),
                'saved_run':sim.run_record()}
    def retain(record):
        with (output/'trials.jsonl').open('a',encoding='utf8') as stream:
            stream.write(json.dumps(record,ensure_ascii=False,sort_keys=True)+'\n')
    results = plan.independent(evaluate,retain)
    summary = {'world':world.name,'trials':len(results),'all_trials_retained':True,'api_calls':0,'gpu':False,
               'execution_failures':sum(r['outcome'].get('status')=='execution_failure' for r in results),
               'execution_plan_hash':plan.sha256}
    save('batch-summary.json',summary)
    return summary
