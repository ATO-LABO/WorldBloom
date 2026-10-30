from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import json
import tempfile
import unittest

from engine.actions import Action
from engine.sim import Simulation
from engine.time_kernel import TimeAxis, TimeActionKernel
from engine.action_timing import ActionTiming, step_timing
from gapengine.explanations import explain_rows
from viewer.planning_view import model, render
from test_goal_planner import inputs, load


def configuration():
    raw, people = inputs()
    raw['time'] = {'days': 2, 'slots': ['朝', '昼', '夕', '夜']}
    raw['ending'][0]['when'] = 'turn >= 8'
    raw['goal_rules']['definitions'] = [raw['goal_rules']['definitions'][0]]
    raw['goal_rules']['definitions'][0]['period']['end'] = 8
    raw['responses']['events'] = []
    spec = next(s for s in raw['responses']['actions'] if s['id'] == 'send_beacon')
    spec.update(steps=2, cost=3, items={}, requires=[], effects=[{'kind':'state','key':'beacon','value':'sent'}])
    raw['responses']['actions'] = [spec]
    raw['action_timing'] = {'version':1,'rules':[
        {'verb':'move','duration':2,'interruptible':False},
        {'verb':'respond','duration':'work_steps','interruptible':True},
        {'verb':'rest','duration':1,'interruptible':True}]}
    for p in people:
        p['stamina']['current'] = 30; p['stamina']['recover_per_slot'] = 2
    return raw, people


def run(raw, people, program=None):
    world, persons, _ = load(raw, people)
    with tempfile.TemporaryDirectory() as temp:
        sim = Simulation(0, world, persons, Path(temp), record_explanations=True)
        def select(this, actor, forced=None):
            return (program(this, actor) or Action('rest')), 1.0
        if program is None: path = sim.run()
        else:
            with patch.object(Simulation, 'choose_action', select): path = sim.run()
        rows = [json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
    return world, persons, rows


def events(rows, actor='通信士', phase=None):
    return [r for r in rows if r['kind']=='event' and r['subject']==actor
            and r['verb'].startswith('activity_') and (phase is None or r['verb']=='activity_'+phase)]


class TimedActionTests(unittest.TestCase):
    def test_move_is_started_then_completes_at_evening_not_at_selection(self):
        raw, people = configuration()
        w,p,rows = run(raw, people, lambda s,a: Action('move',('保管庫',)) if a.id=='通信士' and s.turn==1 else None)
        moved = next(r for r in events(rows,phase='completed') if r['details']['activity']['verb']=='move')
        self.assertEqual(moved['turn'],3)
        self.assertEqual((moved['details']['activity']['start'],moved['details']['activity']['finish']),(0,2))
        own = [r for r in rows if r['kind']=='decision' and r['subject']=='通信士']
        self.assertEqual(own[0]['result'],'started'); self.assertNotIn(2,[r['turn'] for r in own])
        self.assertEqual(moved['details']['activity']['spent'],1)
        self.assertTrue(any(r.get('turn')==2 and r.get('subject')=='補給係' for r in rows))
        self.assertEqual(p['通信士'].zone,'保管庫')

    def test_no_early_work_effect_and_no_double_charge_at_completion(self):
        raw, people = configuration()
        w,p,rows = run(raw,people,lambda s,a: Action('respond',('send_beacon',)) if a.id=='通信士' and s.turn==1 else None)
        done = next(r for r in events(rows,phase='completed') if r['details']['activity']['verb']=='respond')
        self.assertEqual(done['turn'],3); self.assertEqual(done['details']['activity']['spent'],6)
        self.assertEqual(done['details']['activity_result'],'responded')
        self.assertEqual(done['delta']['actor']['stamina'], 24)
        self.assertEqual(w.goal_rules.world_records['beacon_sent']['decided_at'],3)
        self.assertEqual(p['通信士'].commitments, load(raw,people)[1]['通信士'].commitments)

    def test_exact_deadline_completion_precedes_closing_verdict(self):
        raw, people = configuration(); raw['goal_rules']['definitions'][0]['period']['end']=3
        w,_,rows=run(raw,people,lambda s,a: Action('respond',('send_beacon',)) if a.id=='通信士' and s.turn==1 else None)
        self.assertEqual(w.goal_rules.world_records['beacon_sent']['status'],'achieved')
        self.assertFalse(any(r.get('details',{}).get('objective_outcome',{}).get('status')=='failed' for r in rows
                            if r.get('details',{}).get('objective_outcome',{}).get('condition')=='beacon_sent'))

    def test_planner_rejects_one_boundary_too_late(self):
        raw, people = configuration(); raw['goal_rules']['definitions'][0]['period']['end']=2
        w,p,_=load(raw,people)
        self.assertFalse(w.planner.search(w,p['通信士'],'beacon_sent',1)['steps'])
        w,_,rows=run(raw,people,lambda s,a: Action('respond',('send_beacon',)) if a.id=='通信士' and s.turn==1 else None)
        self.assertEqual(w.responses.state['beacon'],'unsent')
        self.assertEqual(w.goal_rules.world_records['beacon_sent']['status'],'failed')

    def test_mid_work_guard_failure_retains_elapsed_effort_and_no_effect(self):
        raw, people = configuration(); raw['responses']['actions'][0]['steps']=4
        raw['responses']['events']=[{'id':'failure','turn':3,'unless':{},'effects':[{'kind':'state','key':'beacon','value':'damaged'}]}]
        w,p,rows=run(raw,people,lambda s,a: Action('respond',('send_beacon',)) if a.id=='通信士' and s.turn==1 else None)
        failed=events(rows,phase='failed')[0]
        self.assertEqual(failed['turn'],3); self.assertEqual(failed['details']['activity']['spent'],6)
        self.assertEqual(failed['details']['response']['reason'],'actual_preconditions_changed')
        self.assertEqual(w.responses.state['beacon'],'damaged')
        self.assertEqual(w.goal_rules.world_records['beacon_sent']['status'],'failed')

    def test_recovery_occurs_only_at_rest_completion(self):
        raw, people = configuration(); raw['action_timing']['rules']=[{'verb':'rest','duration':2,'interruptible':True}]
        w,p,rows=run(raw,people,lambda s,a: Action('rest'))
        first=events(rows,phase='completed')[0]
        self.assertEqual(first['turn'],3)
        progress=events(rows,phase='progress')[0]
        self.assertEqual(progress['delta']['actor'],{})
        self.assertEqual(first['delta']['actor']['stamina'],32)

    def test_in_transit_has_no_stationary_presence_or_new_local_observations(self):
        raw, people = configuration(); w,p,_=load(raw,people)
        with tempfile.TemporaryDirectory() as temp:
            sim=Simulation(0,w,p,Path(temp)); sim.turn=1;sim.day=1
            sim.time_kernel=TimeActionKernel(TimeAxis.logical(w.days,w.slots)); w.action_timing.attach(sim)
            actor=p['通信士']; origin=actor.zone
            result,_,_=w.action_timing.start(actor,Action('move',('保管庫',)))
            self.assertEqual(result,'started'); self.assertEqual(actor.zone,origin)
            self.assertNotIn(actor,w.present_subjects(origin))
            self.assertFalse(w.responses.can_observe(actor,'beacon'))
            self.assertNotIn(('state','beacon'),w.planner._visible(w,actor))
            count=w.action_timing.sequence
            self.assertEqual(w.action_timing.start(actor,Action('rest'))[0],'invalid')
            self.assertEqual(w.action_timing.sequence,count)

    def test_same_shared_duration_model_handles_both_clock_units(self):
        for unit in ['logical_step','minute']:
            axis=TimeAxis(unit,0,10); kernel=TimeActionKernel(axis)
            window=kernel.window(2)
            self.assertEqual((window.start,window.finish,window.unit),(0,2,unit))
            self.assertEqual(window.elapsed(0,1),1)
            self.assertEqual(window.elapsed(1,2),1)

    def test_invalid_and_unsupported_rules_are_rejected(self):
        for rule in [dict(verb='move',duration=True,interruptible=False),
                     dict(verb='move',duration=2,interruptible=True),
                     dict(verb='unregistered_action',duration=2,interruptible=False),
                     dict(verb='rest',duration='work_steps',interruptible=True)]:
            with self.subTest(rule=rule),self.assertRaises(ValueError): ActionTiming({'version':1,'rules':[rule]})

    def test_observation_boundary_does_not_create_future_completion(self):
        raw, people=configuration(); raw['ending'][0]['when']='turn >= 2'
        w,p,rows=run(raw,people,lambda s,a: Action('move',('保管庫',)) if a.id=='通信士' and s.turn==1 else None)
        self.assertEqual(p['通信士'].zone,'操舵室')
        self.assertTrue(events(rows,phase='unfinished'))
        self.assertFalse(events(rows,phase='completed'))

    def test_new_observed_information_can_interrupt_and_replan_work(self):
        raw,people=configuration(); slow=raw['responses']['actions'][0];slow['steps']=4;slow['cost']=2
        fast=deepcopy(slow);fast.update(id='fast',label='短い手段',requires=['battery_location'],steps=1,cost=0)
        raw['responses']['actions'].append(fast)
        raw['responses']['events']=[{'id':'learn','turn':2,'unless':{},'effects':[
            {'kind':'knowledge','actor':'通信士','fact':'battery_location','value':True}]}]
        def select(sim,actor):
            if actor.id=='通信士':
                return max(sim.world.planner.candidates(sim.world,actor,sim.turn,sim.day),key=lambda x:x[1],default=(None,0))[0]
        w,p,rows=run(raw,people,select)
        stopped=events(rows,phase='interrupted')[0]
        self.assertEqual(stopped['turn'],2);self.assertEqual(stopped['details']['activity']['spent'],2)
        self.assertEqual(w.goal_rules.world_records['beacon_sent']['decided_at'],3)
        self.assertEqual(w.responses.state['beacon'],'sent')

    def test_remaining_duration_and_effort_are_shared_with_planner(self):
        raw,people=configuration();w,p,_=load(raw,people); actor=p['通信士']
        runtime=w.action_timing
        runtime.pending[actor.id]={'action':Action('respond',('send_beacon',)), 'finish':2,'cost':6,'spent':3}
        self.assertEqual(step_timing(w,actor,'respond',('send_beacon',),2,6,turn=2),(1,3,1))
        plan=w.planner.search(w,actor,'beacon_sent',2)
        self.assertEqual(plan['steps'][0]['duration'],1);self.assertEqual(plan['steps'][0]['cost'],3)

    def test_progress_and_completion_are_execution_records_not_new_choices(self):
        raw,people=configuration()
        def select(s,a):
            if a.id==s.world.protagonist and s.turn==1:
                plan=s.world.planner.search(s.world,a,'beacon_sent',s.turn)
                return Action('respond',('send_beacon',),{'goal_plan':plan})
        w,p,rows=run(raw,people,select)
        ex=explain_rows(rows)
        self.assertEqual(len(ex['decisions']),sum(r['kind']=='decision' for r in rows))
        done=next(r for r in ex['activity_records'] if r['subject']==w.protagonist and r['outcome']['result']=='responded')
        self.assertEqual(done['source_kind'],'execution');self.assertEqual(done['turn'],3)
        group,=model(ex)
        self.assertEqual(group['final']['status'],'achieved')
        self.assertEqual(group['final']['decided_at'],3)
        html=render(ex)
        self.assertIn('1日目・朝',html);self.assertIn('1日目・夕',html)
        self.assertIn('開始（まだ完了していません）',html)

    def test_unknown_or_mismatched_execution_cannot_borrow_another_actors_plan(self):
        raw,people=configuration();w,p,rows=run(raw,people,lambda s,a:Action('rest'))
        ex=explain_rows(rows)
        wrong=next(deepcopy(r) for r in rows if r.get('verb')=='activity_completed')
        wrong['subject']='someone_else';rows.append(wrong)
        malformed=deepcopy(wrong);malformed['details']['activity']['id']=[];rows.append(malformed)
        rows.append({'kind':'decision','subject':'x','turn':8,'verb':'rest','args':[],'delta':{},'result':'started','details':{'activity':{}}})
        after=explain_rows(rows)
        self.assertEqual(len(after['activity_records']),len(ex['activity_records']))

    def test_actor_rename_does_not_require_a_special_execution_path(self):
        raw,people=configuration()
        def replace(v):
            if isinstance(v,str):return {'通信士':'actor_b','補給係':'actor_a'}.get(v,v)
            if isinstance(v,list):return [replace(x) for x in v]
            if isinstance(v,dict):return {replace(k):replace(x) for k,x in v.items()}
            return v
        def select(s,a):
            if a.id==s.world.protagonist and s.turn==1:return Action('respond',(next(iter(s.world.responses.actions)),))
        first,_,_=run(raw,people,select)
        second,_,_=run(replace(raw),replace(people),select)
        self.assertEqual(first.goal_rules.world_records,second.goal_rules.world_records)

    def test_same_boundary_is_not_charged_twice(self):
        raw,people=configuration();w,p,_=load(raw,people)
        class Writer:
            def __init__(self):self.rows=[]
            def write(self,row):self.rows.append(row)
        with tempfile.TemporaryDirectory() as temp:
            sim=Simulation(0,w,p,Path(temp));sim.turn=1;sim.day=1
            sim.time_kernel=TimeActionKernel(TimeAxis.logical(w.days,w.slots));w.action_timing.attach(sim)
            w.action_timing.start(p['通信士'],Action('move',('保管庫',)))
            sim.time_kernel.restore_position(1);sim.turn=2;writer=Writer()
            w.action_timing.advance(writer);before=deepcopy(w.action_timing.snapshot())
            w.action_timing.advance(writer)
            self.assertEqual(w.action_timing.snapshot(),before)
            self.assertEqual(len(writer.rows),1)
            self.assertEqual(w.action_timing.pending['通信士']['spent'],.5)

    def test_late_arrival_does_not_rewrite_a_terminal_goal(self):
        raw,people=configuration(); raw['goal_rules']['definitions'][0]['period']['end']=2
        condition=next(c for c in raw['concerns']['conditions'] if c['id']=='beacon_sent')
        condition['test']={'kind':'zone','actor':'通信士','equals':'保管庫'}
        w,p,rows=run(raw,people,lambda s,a:Action('move',('保管庫',)) if a.id=='通信士' and s.turn==1 else None)
        self.assertEqual(p['通信士'].zone,'保管庫')
        self.assertEqual(w.goal_rules.world_records['beacon_sent']['status'],'failed')
        self.assertEqual(w.goal_rules.world_records['beacon_sent']['decided_at'],2)

    def test_timed_trajectory_signature_includes_actual_execution_outcome(self):
        raw,people=configuration();_,_,rows=run(raw,people,lambda s,a:Action('rest'))
        before=explain_rows(rows)['trajectory_signature']
        altered=deepcopy(rows)
        completed=next(r for r in altered if r.get('verb')=='activity_completed' and r.get('subject')=='通信士')
        completed['details']['activity_result']='failed';completed['details']['activity']['phase']='failed'
        completed['verb']='activity_failed'
        self.assertNotEqual(explain_rows(altered)['trajectory_signature'],before)
        other=deepcopy(rows)
        completed=next(r for r in other if r.get('verb')=='activity_completed' and r.get('subject')=='補給係')
        completed['details']['activity_result']='failed';completed['details']['activity']['phase']='failed';completed['verb']='activity_failed'
        self.assertEqual(explain_rows(other)['trajectory_signature'],before)

    def test_reactive_forecast_reports_the_same_travel_duration_as_execution(self):
        raw,people=configuration();raw['responses']['actions'][0]['zone']='保管庫'
        w,p,_=load(raw,people)
        option=w.responses._option(w,p['通信士'],w.responses.actions['send_beacon'],1)
        self.assertEqual(option['eta'],4)


if __name__=='__main__':unittest.main()
