"""Run the authored shared-execution examples without model/API calls."""
from pathlib import Path
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from engine.calendar_inputs import load_project
from engine.yaml_cache import load_yaml
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World


def run(project, output, seed=0):
    source=project/'world.yaml'; raw=load_yaml(source)
    if raw.get('slot_execution',{}).get('mode')=='calendar_promises_v2':
        world,people=load_project(source)
    else:
        world=World.from_yaml(source)
        people={p.id:p for p in [Subject.from_definition(load_yaml(path)) for path in sorted((project/'subjects').glob('*.yaml'))]}
    sim=Simulation(seed,world,people,output,record_explanations=True)
    if sim.slot_runtime is not None:
        sim.slot_runtime.configure(json.loads((project/'choices.json').read_text(encoding='utf8')))
    path=sim.run()
    joint=world.joint_activities
    report={'project':project.name,'seed':seed,'layers':str(path),'unit':sim.time_kernel.axis.unit,
            'segments':{i:{'status':s['status'],'start':s['start'],'finish':s['finish'],'spent':s['spent']}
                        for i,s in joint.instances.items()},'tasks':{t:joint.work.read(t) for t in joint.work.specs},
            'positions':{a:p.zone for a,p in people.items()},'state':world.responses.state if world.responses else {},
            'api_calls':0}
    (output/'joint-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    if any(s['status']!='completed' for s in joint.instances.values()):
        raise RuntimeError('Example did not complete; inspect '+str(output/'joint-report.json'))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--seed',type=int,default=0)
    parser.add_argument('--example',choices=['voyage','repair','festival'])
    args=parser.parse_args()
    reports=[]
    for name in ([args.example] if args.example else ['voyage','repair','festival']):
        reports.append(run(ROOT/('projects/joint_'+name+'_example'),args.out/name,args.seed))
    print(json.dumps(reports,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
