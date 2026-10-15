from copy import deepcopy
from pathlib import Path
import json,tempfile,unittest
from engine.decision_provider import ExplicitChoiceProvider,provider_snapshot,staged_provider_restore
from engine.calendar_inputs import load_project
from engine.sim import Simulation

class StatefulProvider(ExplicitChoiceProvider):
    def __init__(self,commands):
        super().__init__(commands)
        self.sent={};self.gene_position=0
    def record_choice(self,command):
        super().record_choice(command)
        self.gene_position+=1
        self.sent[command['id']]={'at':command['at'],'reply_status':'waiting'}
    def export_state(self):
        return {**super().export_state(),'sent':deepcopy(self.sent),'gene_position':self.gene_position}
    def restore_state(self,state):
        base={k:state[k] for k in ('consumed','epoch_position')}
        super().restore_state(base)
        if state['gene_position']!=len(self.consumed) or set(state['sent'])!=set(self.consumed):raise ValueError('bad gene/history')
        self.gene_position=state['gene_position'];self.sent=deepcopy(state['sent'])

class ProviderTests(unittest.TestCase):
    def test_changed_table_config_or_state_cannot_restore(self):
        commands=[{'id':'one','actor':'A','at':0,'verb':'wait','minutes':1}]
        p=ExplicitChoiceProvider(commands);p.record_choice(commands[0]);snap=provider_snapshot(p)
        self.assertEqual(provider_snapshot(staged_provider_restore(p,snap)),snap)
        q=ExplicitChoiceProvider(commands,table_hash='1'*64)
        with self.assertRaises(ValueError):staged_provider_restore(q,snap)
        bad=deepcopy(snap);bad['state']['epoch_position']=20
        with self.assertRaises(ValueError):staged_provider_restore(p,bad)
        self.assertEqual(p.export_state(),snap['state'])

    def test_calendar_resume_preserves_provider_gene_history_rng_and_all_bytes(self):
        root=Path(__file__).resolve().parents[1]
        project=root/'projects/calendar_workshop_example/world.yaml'
        world,people=load_project(project)
        actor=sorted(people)[0]
        start=0
        from engine.calendar_runtime import CalendarRuntime
        prototype=Simulation(12,world,people,root/'unused-provider-output')
        start=prototype.slot_runtime.now
        commands=[{'id':'first','actor':actor,'at':start,'verb':'wait','minutes':1},
                  {'id':'later','actor':actor,'at':start+2,'verb':'wait','minutes':1}]
        def sim(out):
            w,p=load_project(project)
            s=Simulation(12,w,p,out,decision_provider=StatefulProvider(commands))
            s.slot_runtime.configure(commands)
            return s
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);continuous=sim(d/'continuous');continuous.slot_runtime.run(until=start+4)
            partial=sim(d/'partial');partial.slot_runtime.run(until=start+1)
            new_process=sim(d/'partial');new_process.slot_runtime.run(until=start+4,resume=True)
            self.assertEqual(continuous.slot_runtime.export_state(),new_process.slot_runtime.export_state())
            self.assertEqual((d/'continuous/layers.jsonl').read_bytes(),(d/'partial/layers.jsonl').read_bytes())
            self.assertEqual(new_process.decision_provider.gene_position,2)
            self.assertEqual(set(new_process.decision_provider.sent),{'first','later'})
            before=(d/'partial/layers.jsonl').read_bytes()
            record=new_process.run_record()
            self.assertEqual(record['stop_reason'],'paused')
            self.assertIsNone(record['execution_plan'])
            self.assertEqual(record['provider_identity'],provider_snapshot(new_process.decision_provider)['identity'])
            self.assertEqual(before,(d/'partial/layers.jsonl').read_bytes())
