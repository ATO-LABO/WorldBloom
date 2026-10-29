"""Run any authored planning world with ordinary, unforced choices."""
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--seed',type=int,default=0)
    parser.add_argument('--choices',type=Path,help='Explicit initial situation commands for a native calendar world')
    args=parser.parse_args(argv)
    folder=args.project.resolve();out=args.out.resolve()
    if out.exists():parser.error('Output exists; choose a new directory to preserve saved runs')
    from engine.world import World
    from engine.subject import Subject
    from engine.sim import Simulation
    raw=World.from_yaml(folder/'world.yaml')
    if raw.slot_execution is not None:
        from engine.calendar_inputs import load_project
        world,people=load_project(folder/'world.yaml')
    else:
        world=raw
        people={p.id:p for p in (Subject.from_yaml(f) for f in sorted((folder/'subjects').glob('*.yaml')))}
        world.bind_subjects(people)
    sim=Simulation(args.seed,world,people,out,record_explanations=True)
    if args.choices is not None and sim.slot_runtime is None:
        parser.error('--choices requires a native calendar world')
    if sim.slot_runtime is not None:
        sim.slot_runtime.configure(json.loads(args.choices.read_text(encoding='utf8')) if args.choices else [])
    sim.run()
    print(json.dumps({'ledger':str(out/'layers.jsonl'),'outcomes':world.goal_rules.report(world)},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
