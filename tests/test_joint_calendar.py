from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import yaml

from engine.calendar_inputs import load_project
from engine.sim import Simulation

ROOT=Path(__file__).resolve().parents[1]


def write(path,value):path.write_text(yaml.safe_dump(value,allow_unicode=True,sort_keys=False),encoding='utf8')


def project(folder):
    shutil.copytree(ROOT/'projects/calendar_workshop_example',folder)
    raw=yaml.safe_load((folder/'world.yaml').read_text(encoding='utf8'))
    contract=yaml.safe_load((folder/'contract.yaml').read_text(encoding='utf8'))
    contract['roles']['Bo']=['maker'];contract['facility_hours']={}
    contract['start_time']='08:00';contract['end_time']='10:00'
    definition=json.loads((ROOT/'docs/design-examples/joint-activities-contract.json').read_text(encoding='utf8'))['definitions'][1]
    definition['roles'][0]['requires']['facts']=[]
    definition['progress']['task']='pieces'
    definition['duration']['max_units']=30
    definition['resources']=definition['resources'][:1]
    definition['resources'][0]['binding']['task']='pieces'
    definition['effects']['on_complete']['task']='pieces'
    definition['effects']['on_complete']['units']=3
    definition['bindings_required']=['target','target_revision']
    raw['joint_activities']={'version':1,'definitions':[definition],
        'workflows':[{'id':'workshop','tasks':[{'id':'pieces','units':3,'minutes_per_unit':10,
            'execution':{'kind':'joint','definition':'replace_component'},'dependencies':[]}]}],
        'instances':[{'id':'work','definition_id':'replace_component','definition_revision':1,'terms_revision':1,
            'roles':{'technician':['Ada'],'holder':['Bo']},'bindings':{'target':'order','target_revision':1},
            'earliest_start':482,'expires_at':500,
            'initial_agreement':{'source':'authored_initial_terms','accepted_by':['Ada','Bo'],'known_by':['Ada','Bo']}}]}
    for path in (folder/'subjects').glob('*.yaml'):
        person=yaml.safe_load(path.read_text(encoding='utf8'));person['range']['entry']='Bench'
        person['verbs']=['move','respond','rest'];person['stamina']={'max':100,'current':100,'recover_per_slot':1}
        write(path,person)
    write(folder/'world.yaml',raw);write(folder/'contract.yaml',contract)
    return raw,contract


def simulation(folder,out):
    world,people=load_project(folder/'world.yaml');sim=Simulation(0,world,people,out)
    sim.slot_runtime.configure([])
    return sim


class JointCalendarTests(unittest.TestCase):
    def test_unforced_calendar_consent_and_checkpoint_share_goal_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);raw,contract=project(base/'project')
            raw['planning']={'version':1,'actors':['Ada','Bo'],'max_depth':8,'max_nodes':256}
            raw['concerns']={'version':2,'conditions':[{'id':'pieces_done','label':'Complete work','mode':'achieve','visibility':'observed',
                                                       'test':{'kind':'task','task':'pieces','minimum':3}}]}
            date=contract['calendar']['period']['start_date']
            raw['goal_rules']={'version':2,'definitions':[{'condition':'pieces_done','success':{'condition':'pieces_done'},
                'failure':{'constant':False},'period':{'start':{'date':date,'time':'08:00'},'end':{'date':date,'time':'08:32'}}}]}
            i=raw['joint_activities']['instances'][0];i.pop('initial_agreement');i['known_by']=['Ada','Bo']
            write(base/'project/world.yaml',raw)
            for path in (base/'project/subjects').glob('*.yaml'):
                person=yaml.safe_load(path.read_text(encoding='utf8'))
                person['commitments']=[{'condition':'pieces_done','priority':8,'required':False}];write(path,person)
            whole=simulation(base/'project',base/'whole');whole.slot_runtime.run(until=520)
            part=simulation(base/'project',base/'part');part.slot_runtime.run(until=489)
            resumed=simulation(base/'project',base/'part');resumed.slot_runtime.run(until=520,resume=True)
            self.assertEqual(whole.slot_runtime.joint.instances['work']['status'],'completed')
            self.assertEqual(whole.slot_runtime.planning.snapshot(),resumed.slot_runtime.planning.snapshot())
            self.assertEqual((base/'whole/layers.jsonl').read_bytes(),(base/'part/layers.jsonl').read_bytes())

    def test_JA12_JA18_JA39_native_ledger_materials_and_individual_contributions(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);project(base/'project');sim=simulation(base/'project',base/'run')
            sim.slot_runtime.run(until=512)
            rt=sim.slot_runtime;j=rt.joint
            self.assertEqual(j.instances['work']['status'],'completed')
            self.assertIs(j.work.tasks,rt.ledger['tasks'])
            self.assertEqual(rt.ledger['tasks']['pieces']['effort_minutes'],30)
            self.assertEqual(rt.ledger['tasks']['pieces']['units'],3)
            self.assertEqual(rt.ledger['tasks']['pieces']['contributions'],{'Ada':3,'Bo':3})
            self.assertEqual(rt.ledger['materials']['blank'],0)
            self.assertAlmostEqual(sim.subjects['Ada'].stamina,97)
            self.assertAlmostEqual(sim.subjects['Bo'].stamina,98.5)

    def test_JA32_cross_process_resume_matches_continuous(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);project(base/'project')
            whole=simulation(base/'project',base/'whole');whole.slot_runtime.run(until=520)
            split=simulation(base/'project',base/'split');split.slot_runtime.run(until=489)
            restored=simulation(base/'project',base/'split');restored.slot_runtime.run(until=520,resume=True)
            self.assertEqual(whole.slot_runtime.joint.snapshot(),restored.slot_runtime.joint.snapshot())
            self.assertEqual(whole.slot_runtime.ledger,restored.slot_runtime.ledger)
            self.assertEqual((base/'whole/layers.jsonl').read_bytes(),(base/'split/layers.jsonl').read_bytes())


if __name__=='__main__':unittest.main()
