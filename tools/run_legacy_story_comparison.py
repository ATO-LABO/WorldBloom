"""Run fixed legacy/shared editions locally, without GA or external model calls."""
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine.world import World
from engine.subject import Subject
from engine.sim import Simulation
from tools.migrate_legacy_stories import FAMILIES


def run(source, edition, seed, out):
    name=source if edition=='original' else source+'_shared_planning'
    folder=ROOT/'projects'/name
    world=World.from_yaml(folder/'world.yaml');world.resolve_truth(seed)
    people={p.id:p for p in (Subject.from_yaml(f) for f in sorted((folder/'subjects').glob('*.yaml')))}
    world.bind_subjects(people)
    path=Simulation(seed,world,people,out,record_explanations=True).run()
    rows=[json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
    endings=[r for r in rows if r.get('verb')=='ending']
    decisions=[r for r in rows if r.get('kind')=='decision' and r['subject']==world.protagonist]
    return dict(project=source,edition=edition,seed=seed,cast_size=len(people),
        planning_actors=[] if world.planner is None else world.planner.config['actors'],
        target_reached=any(r.get('id') in {e['id'] for e in world.target_endings()} for r in endings),
        endings=[dict(id=r.get('id'),turn=r['turn']) for r in endings],
        invalid=sum(r['result']=='invalid' for r in decisions),
        objective=None if world.goal_rules is None else world.goal_rules.snapshot(world)['world'],
        log=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True,help='A new or empty evidence folder; saved logs are never overwritten.')
    parser.add_argument('--seeds',type=int,nargs='+',default=[0,1,2,3])
    parser.add_argument('--projects',choices=sorted(FAMILIES),nargs='+',default=list(FAMILIES))
    parser.add_argument('--editions',choices=['original','shared'],nargs='+',default=['original','shared'])
    args=parser.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):raise FileExistsError('Preserve existing evidence; choose a new output folder')
    if len(set(args.seeds))!=len(args.seeds):raise ValueError('Seeds must be distinct')
    args.out.mkdir(parents=True,exist_ok=True);results=[]
    for source in args.projects:
        for edition in args.editions:
            for seed in args.seeds:
                dest=args.out/source/edition/str(seed);dest.mkdir(parents=True)
                result=run(source,edition,seed,dest);results.append(result)
                (args.out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
                print(source,edition,seed,'target_reached='+str(result['target_reached']),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
