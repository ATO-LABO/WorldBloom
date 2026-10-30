"""Retain ordinary original-protagonist / companion planning runs without APIs."""
import argparse
from pathlib import Path
import json
import hashlib
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from tools.migrate_supporting_stories import SOURCES


def run(source,edition,seed,out,*,accepted=False):
    name=source+('_shared_planning' if edition=='protagonist' else '_companions_planning')
    folder=ROOT/'projects'/name
    w=World.from_yaml(folder/'world.yaml');w.resolve_truth(seed)
    people={a.id:a for a in (Subject.from_yaml(p) for p in sorted((folder/'subjects').glob('*.yaml')))}
    w.bind_subjects(people)
    assumption=None
    if accepted:
        # Explicit alternative initial situation, never a forced runtime choice.
        hero=people[w.protagonist]
        if any(hero.zone not in people[who].range_zones for who in w.planner.config['actors']):
            raise ValueError('The accepted-companion initial location must be allowed for every planning actor')
        for who in w.planner.config['actors']:
            if who==hero.id:continue
            people[who].zone=hero.zone
            people[who].initial_relations.setdefault(hero.id,{})['affinity']=w.companionship['threshold']+.05
        assumption='Mobile companions already accepted the journey and are with the protagonist at the initial location.'
    simulation=Simulation(seed,w,people,out,record_explanations=True)
    if accepted and any(w.relations.stance(who,w.protagonist)<w.companionship['threshold']
            for who in w.planner.config['actors'] if who!=w.protagonist):
        raise ValueError('Accepted-companion initial conditions were not retained by runtime binding')
    log=simulation.run()
    rows=[json.loads(l) for l in log.read_text(encoding='utf8').splitlines()]
    decisions=[r for r in rows if r.get('kind')=='decision']
    actors={who:{'actions':sum(r['subject']==who for r in decisions),
        'planned':sum(r['subject']==who and bool(r.get('details',{}).get('planning')) for r in decisions),
        'verbs':sorted({r['verb'] for r in decisions if r['subject']==who}),
        'invalid':sum(r['subject']==who and r['result']=='invalid' for r in decisions),
        'final_zone':p.zone} for who,p in people.items()}
    return dict(source=source,edition=edition,seed=seed,initial_assumption=assumption,
        planning_actors=w.planner.config['actors'],actors=actors,
        target_reached=any(r.get('verb')=='ending' and r.get('id') in {e['id'] for e in w.target_endings()} for r in rows),
        log=str(log.resolve()),sha256=hashlib.sha256(log.read_bytes()).hexdigest())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--sources',nargs='+',choices=SOURCES,default=list(SOURCES))
    parser.add_argument('--seeds',nargs='+',type=int,default=[0,1])
    parser.add_argument('--accepted',action='store_true')
    args=parser.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):raise FileExistsError('Preserve prior evidence; choose a fresh folder')
    if len(set(args.seeds))!=len(args.seeds):raise ValueError('Seeds must be distinct')
    args.out.mkdir(parents=True,exist_ok=True);results=[]
    for source in args.sources:
        for edition in (('companions',) if args.accepted else ('protagonist','companions')):
            for seed in args.seeds:
                dest=args.out/source/edition/str(seed);dest.mkdir(parents=True)
                result=run(source,edition,seed,dest,accepted=args.accepted);results.append(result)
                (args.out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
                print(source,edition,seed,'invalid='+str(sum(a['invalid'] for a in result['actors'].values())),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
