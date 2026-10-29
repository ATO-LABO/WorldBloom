"""Scheduled work and real promise evidence, independent of festival names."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from test_calendar_goal_planning import project, read, write, simulation
from engine.calendar_inputs import load_project
from engine.sim import Simulation
from engine.task_windows import compile_windows


ROOT = Path(__file__).resolve().parents[1]


def scheduled(folder, *, side_minutes=10):
    world, contract, person, _ = project(folder)
    contract['tasks'] = {}
    for task, minutes in [('appointment',10), ('side',side_minutes)]:
        contract['tasks'][task] = {'label':task,'place':'Bench','units':1,'minutes_per_unit':minutes,
            'dependencies':[],'resource':task,'roles':['maker'],'materials_per_unit':{},'operation':'work'}
    contract['tasks']['appointment']['windows'] = [{'start':{'date':'2026-11-01','time':'09:00'},
                                                   'end':{'date':'2026-11-01','time':'09:10'}}]
    contract['roles'] = {'Ada':['maker'], 'Bo':['receiver']}
    contract['materials'] = {}
    contract['energy'] = {'move_per_minute':0,'work_per_minute':0,'rest_per_minute':1,'rest_minutes':10}
    contract['initial_task_knowledge'] = {'Ada':['appointment','side'],'Bo':[]}
    world['concerns'] = {'version':2,'conditions':[
        {'id':key,'label':key,'mode':'achieve','visibility':'observed',
         'test':{'kind':'task','task':key,'minimum':1}} for key in ('appointment','side')]}
    world['goal_rules']['definitions'] = [{'condition':key,'success':{'condition':key},'failure':{'constant':False},
        'period':{'start':{'date':'2026-11-01','time':'08:00'},'end':{'date':'2026-11-01','time':end}}}
        for key,end in [('appointment','09:10'),('side','08:50')]]
    person['commitments'] = [{'condition':'appointment','priority':9,'required':False},
                             {'condition':'side','priority':8,'required':False}]
    write(folder/'world.yaml',world); write(folder/'contract.yaml',contract); write(folder/'subjects/ada.yaml',person)
    (folder/'choices.json').write_text('[]',encoding='utf8')
    return world,contract,person


class CalendarPurposeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.folder = self.root/'inputs'
        self.world,self.contract,self.person = scheduled(self.folder)

    def save(self):
        write(self.folder/'world.yaml',self.world); write(self.folder/'contract.yaml',self.contract)
        write(self.folder/'subjects/ada.yaml',self.person)

    def run_normal(self, name='run', commands=None):
        sim = simulation(self.folder,self.root/name,commands=commands); sim.slot_runtime.run(); return sim

    def test_other_purpose_uses_slack_without_losing_scheduled_work(self):
        sim = self.run_normal(); rt=sim.slot_runtime
        selected = [r for r in rt.rows if r['details'].get('selection_mechanism')=='bounded_goal_search']
        self.assertEqual(selected[0]['details']['choice']['task_id'],'side')
        self.assertEqual(selected[0]['details']['planning']['uses_waiting_slack'],['appointment'])
        started = next(r for r in rt.rows if r['verb']=='action_started' and r['details']['choice'].get('task_id')=='appointment')
        self.assertEqual((started['absolute_minute'],started['details']['finish_at']),(540,550))
        verdicts = {r['condition']:r for r in sim.world.goal_rules.report(sim.world)}
        self.assertEqual(verdicts['side']['decided_at'],490)
        self.assertEqual(verdicts['appointment']['decided_at'],550)

    def test_too_long_secondary_work_cannot_displace_higher_purpose(self):
        self.contract['tasks']['side']['minutes_per_unit']=70; self.save()
        sim=self.run_normal(); verdicts={r['condition']:r for r in sim.world.goal_rules.report(sim.world)}
        self.assertEqual(verdicts['appointment']['status'],'achieved')
        self.assertEqual(verdicts['side']['status'],'failed')
        self.assertEqual(sim.slot_runtime.ledger['tasks']['side']['units'],0)

    def test_before_window_and_window_overrun_reject_without_reserving_material(self):
        self.contract['materials']={'blank':1}
        self.contract['tasks']['appointment']['materials_per_unit']={'blank':1}; self.save()
        sim=self.run_normal(commands=[{'id':'early','actor':'Bo','at':480,'verb':'wait','minutes':1},
            {'id':'outside','actor':'Ada','at':480,'verb':'work','task_id':'appointment','minutes':10}])
        reject=next(r for r in sim.slot_runtime.rows if r['id']=='reject:outside')
        self.assertEqual(reject['details']['reason'],'outside_task_window')
        # Re-run without planning, so only the deliberately invalid command runs.
        self.world.pop('planning'); self.world.pop('goal_rules'); self.save()
        second=self.run_normal('overrun',commands=[{'id':'late','actor':'Ada','at':545,'verb':'work','task_id':'appointment','minutes':10}])
        self.assertEqual(second.slot_runtime.ledger['materials'],{'blank':1})
        self.assertEqual(second.slot_runtime.ledger['tasks']['appointment']['reserved_units'],0)
        self.assertEqual(next(r for r in second.slot_runtime.rows if r['id']=='reject:late')['details']['reason'],'outside_task_window')

    def test_handoff_uses_same_window_and_actual_recipient_guard(self):
        self.world.pop('planning'); self.world.pop('goal_rules')
        self.contract['tasks']['appointment']['operation']='deliver'; self.save()
        sim=self.run_normal(commands=[{'id':'early','actor':'Ada','at':480,'verb':'deliver','task_id':'appointment','target':'Bo'},
            {'id':'valid','actor':'Ada','at':540,'verb':'deliver','task_id':'appointment','target':'Bo'}])
        self.assertEqual(next(r for r in sim.slot_runtime.rows if r['id']=='reject:early')['details']['reason'],'outside_task_window')
        self.assertEqual(sim.slot_runtime.ledger['tasks']['appointment']['units'],1)

    def test_split_during_wait_resumes_exactly(self):
        full=self.run_normal('full')
        split=simulation(self.folder,self.root/'split');split.slot_runtime.run(until=515)
        self.assertEqual(split.slot_runtime.busy['Ada']['verb'],'wait')
        resumed=simulation(self.folder,self.root/'split');resumed.slot_runtime.run(resume=True)
        self.assertEqual(full.slot_runtime.export_state(),resumed.slot_runtime.export_state())
        self.assertEqual({p.name:p.read_bytes() for p in full.out_dir.iterdir() if p.is_file()},
                         {p.name:p.read_bytes() for p in resumed.out_dir.iterdir() if p.is_file()})

    def test_work_window_crosses_midnight_on_the_same_minute_axis(self):
        self.world['time']['days']=2
        self.contract['calendar']['period']['end_date']='2026-11-02'
        self.contract.update(start_time='23:50',end_time='00:20')
        self.contract['tasks']['appointment']['windows']=[{'start':{'date':'2026-11-01','time':'23:55'},
                                                         'end':{'date':'2026-11-02','time':'00:05'}}]
        self.world['goal_rules']['definitions'][0]['period']={
            'start':{'date':'2026-11-01','time':'23:50'},'end':{'date':'2026-11-02','time':'00:05'}}
        self.world['goal_rules']['definitions']=self.world['goal_rules']['definitions'][:1]
        self.person['commitments']=self.person['commitments'][:1];self.save()
        sim=self.run_normal()
        verdict=next(r for r in sim.world.goal_rules.report(sim.world) if r['condition']=='appointment')
        self.assertEqual((verdict['status'],verdict['decided_at']),('achieved',1445))

    def test_priority_change_changes_means_without_seed_branch(self):
        self.contract['tasks']['side']['minutes_per_unit']=70
        self.world['goal_rules']['definitions'][1]['period']['end']['time']='09:20'
        self.person['commitments'][1]['priority']=10;self.save()
        sim=self.run_normal()
        verdicts={r['condition']:r for r in sim.world.goal_rules.report(sim.world)}
        self.assertEqual(verdicts['side']['status'],'achieved')
        self.assertEqual(verdicts['appointment']['status'],'failed')
        chosen=next(r for r in sim.slot_runtime.rows if r['details'].get('selection_mechanism')=='bounded_goal_search')
        self.assertEqual(chosen['details']['choice']['task_id'],'side')

    def test_real_accepted_promise_waits_for_work_and_exact_deadline(self):
        from engine.promises_v2 import PromiseRegistry
        self.world['concerns']['conditions'][0]['test']={'kind':'promise','promise':'visit','version':1,'statuses':['fulfilled']}
        self.world['goal_rules']['definitions'][0]['period']['start']['time']='08:06'
        self.person['commitments']=self.person['commitments'][:1];self.save()
        terms={'promise_id':'visit','version':1,'proposer':'Ada','promisor':'Ada','beneficiary':'Bo',
            'acceptance_parties':['Ada','Bo'],'place':'Bench','start':540,'deadline':550,'grace_until':None,
            'requirements':[{'task_id':'appointment','units':1,'contribution_by':'Ada'}]}
        h=PromiseRegistry({'Ada','Bo'},set(self.contract['tasks']),{'Bench','Store'}).terms_hash(terms)
        commands=[{'id':'offer','actor':'Ada','at':480,'verb':'offer','terms':terms},
            {'id':'accept-a','actor':'Ada','at':482,'verb':'accept','promise_id':'visit','version':1,'terms_hash':h},
            {'id':'accept-b','actor':'Bo','at':484,'verb':'accept','promise_id':'visit','version':1,'terms_hash':h}]
        sim=simulation(self.folder,self.root/'promise',commands=commands);rt=sim.slot_runtime
        rt.run(until=549)
        self.assertNotEqual(rt.registry.get('visit')['status'],'fulfilled')
        self.assertEqual(rt.ledger['tasks']['appointment']['units'],0)
        resumed=simulation(self.folder,self.root/'promise',commands=commands);resumed.slot_runtime.run(resume=True)
        self.assertEqual(resumed.slot_runtime.registry.get('visit')['status'],'fulfilled')
        verdict=next(r for r in resumed.world.goal_rules.report(resumed.world) if r['condition']=='appointment')
        self.assertEqual(verdict['decided_at'],550)

    def test_renamed_nonfestival_world_keeps_same_physical_schedule(self):
        first=self.run_normal('first');folder=self.root/'renamed'
        shutil.copytree(self.folder,folder)
        mapping={'Ada':'技師','Bo':'検査官','Bench':'整備室','Store':'倉庫',
                 'appointment':'予約点検','side':'工具整理','maker':'整備担当','receiver':'確認担当'}
        def rename(value):
            if isinstance(value,dict):return {mapping.get(k,k):rename(v) for k,v in value.items()}
            if isinstance(value,list):return [rename(v) for v in value]
            return mapping.get(value,value) if isinstance(value,str) else value
        for p in folder.rglob('*.yaml'):write(p,rename(read(p)))
        second=simulation(folder,self.root/'second');second.slot_runtime.run()
        a=[(r['absolute_minute'],r['details']['choice']['verb']) for r in first.slot_runtime.rows if r['details'].get('selection_mechanism')=='bounded_goal_search']
        b=[(r['absolute_minute'],r['details']['choice']['verb']) for r in second.slot_runtime.rows if r['details'].get('selection_mechanism')=='bounded_goal_search']
        self.assertEqual(a,b)

    def test_windows_validate_shape_range_and_overlap(self):
        sim=simulation(self.folder,self.root/'validation');calendar=sim.slot_runtime.calendar
        bad=[[],[{'start':{'date':'2026-11-01','time':'09:00'},'end':{'date':'2026-11-01','time':'08:00'}}],
             [{'start':{'date':'2026-11-02','time':'09:00'},'end':{'date':'2026-11-02','time':'09:10'}}],
             self.contract['tasks']['appointment']['windows']*2]
        for windows in bad:
            with self.subTest(windows=windows),self.assertRaises(ValueError):
                compile_windows({'job':{'windows':windows}},calendar)

    def test_compound_purpose_uses_whole_success_expression(self):
        self.world['goal_rules']['definitions'][0]['success']={'all':[{'condition':'appointment'},{'condition':'side'}]}
        self.save();sim=simulation(self.folder,self.root/'compound')
        assessment=sim.world.concerns.assessment(sim.world,'Ada',projection={'effects':[
            {'kind':'task','task':'appointment','value':1},{'kind':'task','task':'side','value':0}]})
        own=next(r for r in assessment['entries'] if r['condition']=='appointment')
        self.assertEqual((own['predicate_state'],own['state']),('true','false'))
        self.assertEqual(own['progress'],'pending')
        self.world['goal_rules']['definitions'][0]['success']={'any':[{'condition':'appointment'},{'condition':'side'}]}
        self.save();other=simulation(self.folder,self.root/'disjunction')
        estimate=other.world.concerns.assessment(other.world,'Ada',projection={'effects':[
            {'kind':'task','task':'appointment','value':0},{'kind':'task','task':'side','value':1}]})
        self.assertEqual(estimate['entries'][0]['state'],'true')

    def test_dependency_inspection_does_not_read_remote_truth_into_forecast(self):
        self.contract['tasks']['side'].update(place='Store',roles=['receiver'])
        self.contract['tasks']['appointment']['dependencies']=['side'];self.save()
        sim=simulation(self.folder,self.root/'inspection');rt=sim.slot_runtime
        rt.ledger['tasks']['side']['units']=1 # Unknown actual remote completion.
        plan=rt.planning.information(sim.subjects['Ada'],'appointment',480)
        self.assertEqual(plan['status'],'seeking_information')
        self.assertEqual(plan['steps'][0]['args'],['Store'])
        self.assertEqual(rt.planning.visible(sim.subjects['Ada'])[('task','side')],0)
        self.assertFalse([e for e in plan['steps'][0]['effects'] if e['kind']=='task'])


class FestivalPurposeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name);cls.folder=ROOT/'projects/cultural_festival_shared_planning'
        world,people=load_project(cls.folder/'world.yaml')
        cls.sim=Simulation(0,world,people,cls.root/'full',record_explanations=True)
        cls.commands=json.loads((cls.folder/'choices.json').read_text(encoding='utf8'))
        cls.sim.slot_runtime.configure(cls.commands);cls.sim.slot_runtime.run()

    def test_all_six_people_use_personal_purposes_and_common_choice(self):
        sim=self.sim;rt=sim.slot_runtime
        self.assertEqual(len(sim.subjects),6)
        self.assertTrue(all(p.commitments for p in sim.subjects.values()))
        self.assertEqual({r['subject'] for r in rt.rows if r['details'].get('selection_mechanism')=='bounded_goal_search'},set(sim.subjects))
        self.assertTrue(all(r['status']=='achieved' for r in sim.world.goal_rules.report(sim.world)))
        self.assertTrue(all(c['verb'] in {'offer','accept'} and c['at']<525 for c in self.commands))

    def test_actual_work_and_promises_not_early_presence_certify_outcome(self):
        rt=self.sim.slot_runtime
        self.assertTrue(all(rt.registry.get(pid)['status']=='fulfilled' for pid in ('出演-蒼','出演-蓮','進行-紗季','展示-写真')))
        for task in ('stage-ao','stage-ren','stage-saki'):
            completion=next(r for r in rt.rows if r['verb']=='action_completed' and r['details'].get('task_id')==task)
            self.assertEqual(completion['absolute_minute'],915)
        self.assertFalse([r for r in rt.rows if r['verb']=='action_rejected'])

    def test_original_personality_and_initial_state_are_preserved(self):
        original=ROOT/'projects/cultural_festival_v2'
        world=read(original/'world.yaml')
        for ref in world['slot_execution']['subjects']:
            before=read(original/ref['path']);after=read(self.folder/'subjects'/Path(ref['path']).name)
            for field in ('traits','inventory','knowledge','relations'):
                self.assertEqual(before[field],after[field])
            self.assertEqual(before['range']['entry'],after['range']['entry'])

    def test_common_explanation_contains_purposes_and_actual_receipts(self):
        from gapengine.explanations import extract_explanation
        from viewer.planning_view import model
        explanation=extract_explanation(self.sim.out_dir/'layers.jsonl')
        groups=model(explanation)
        self.assertTrue(groups)
        self.assertTrue(any(r['details'].get('planning',{}).get('uses_waiting_slack') for r in self.sim.slot_runtime.rows))
        self.assertEqual(self.sim.world.deliberation.records['line-change']['status'],'approved')
