from pathlib import Path
from copy import deepcopy
import tempfile,unittest,json
from engine.calendar_inputs import load_project
from engine.sim import Simulation
from engine.episode_contract import factory_identities
from engine.execution_plan import digest

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'projects/calendar_workshop_example/world.yaml'
H='1'*64
def fixture(out,seed=7):
    world,people=load_project(PROJECT)
    sim=Simulation(seed,world,people,out)
    at=sim.slot_runtime.now;actor=sorted(people)[0]
    commands=[{'id':'wait','actor':actor,'at':at,'verb':'wait','minutes':1}]
    sim.slot_runtime.configure(commands)
    return sim,commands

def valid_plan(sim):
    r=sim.slot_runtime;sampler,provider=factory_identities('explicit_calendar',commands=r.commands)
    return {'schema_version':1,'execution_kind':'explicit_witness','sampler':sampler,'provider':provider,
            'classification':{'id':'fixture','version':1,'source_hash':H,'config_hash':H},
            'predeclared_seeds':[sim.seed],'planned_trials':1,
            'selection':dict.fromkeys(['fitness_selection','breeding','keep_filter','canon_feedback'],False),
            'observation':{'unit':'minute','start':r.now,'end':r.end,'stop_contract_hash':digest({'target_as_observation':False,'death_stops':True})}}

class BindingTests(unittest.TestCase):
    def test_each_contradiction_is_rejected_before_output(self):
        from engine.execution_plan import ExecutionPlan
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            for field in ('seed','unit','range','provider','sampler','stop','kind'):
                sim,_=fixture(d/field);value=valid_plan(sim)
                if field=='seed':value['predeclared_seeds']=[0]
                if field=='unit':value['observation']['unit']='logical_step'
                if field=='range':value['observation']['end']-=1
                if field=='provider':value['provider']['config_hash']='2'*64
                if field=='sampler':value['sampler']['source_hash']='2'*64
                if field=='stop':value['observation']['stop_contract_hash']='2'*64
                if field=='kind':value['execution_kind']='independent_trials'
                sim.execution_plan=ExecutionPlan(value)
                with self.subTest(field=field),self.assertRaises(ValueError):sim.slot_runtime.run(until=sim.slot_runtime.now+1)
                self.assertFalse((d/field/'layers.jsonl').exists())

    def test_valid_witness_binds_seed_axis_and_provider(self):
        from engine.execution_plan import ExecutionPlan
        with tempfile.TemporaryDirectory() as d:
            sim,_=fixture(Path(d));sim.execution_plan=ExecutionPlan(valid_plan(sim))
            sim.slot_runtime.run(until=sim.slot_runtime.now+1)
            record=sim.run_record()
            self.assertEqual(record['verified_execution']['execution_kind'],'explicit_witness')
            self.assertEqual(record['verified_execution']['seed'],7)
            self.assertEqual(record['verified_execution']['provider'],sim.execution_plan.to_dict()['provider'])

class SavedRecordTests(unittest.TestCase):
    def test_unsaved_dispatch_does_not_change_saved_result(self):
        with tempfile.TemporaryDirectory() as d:
            sim,_=fixture(Path(d));start=sim.slot_runtime.now;sim.slot_runtime.run(until=start+1)
            before=sim.run_record();sim.slot_runtime._tick(start+2)
            self.assertEqual(sim.slot_runtime.now,start+2)
            self.assertEqual(sim.run_record(),before)

    def test_projection_mutation_does_not_change_runtime_checkpoint_or_next_read(self):
        with tempfile.TemporaryDirectory() as d:
            sim,_=fixture(Path(d));sim.slot_runtime.observations['fixture']={'nested':{'value':1}}
            sim.slot_runtime.run(until=sim.slot_runtime.now+1)
            before=sim.slot_runtime.export_state();record=sim.run_record()
            record['observations']['fixture']['nested']['value']=99
            self.assertEqual(sim.slot_runtime.export_state(),before)
            self.assertEqual(sim.run_record()['observations']['fixture']['nested']['value'],1)

    def test_checkpoint_saved_but_ledger_not_published_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            sim,_=fixture(Path(d));start=sim.slot_runtime.now;sim.slot_runtime.run(until=start+1)
            sim.slot_runtime._tick(start+2);sim.slot_runtime.store.save(sim.slot_runtime.export_state())
            with self.assertRaises(ValueError):sim.run_record()

    def test_new_simulation_cannot_replace_saved_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            sim,_=fixture(Path(d),7);sim.slot_runtime.run(until=sim.slot_runtime.now+1)
            before=sim.run_record();other,_=fixture(Path(d),99)
            self.assertEqual(other.run_record(),before)
