"""Physical shared execution, independent consent, and checkpoint contracts."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fractions import Fraction
import json
import random
import tempfile
import unittest

from engine.actions import Action, candidates
from engine.activity_bookings import ActivityBookings
from engine.joint_activities import JointActivities
from engine.sim import Simulation
from engine.subject import Subject
from engine.time_kernel import TimeAxis, TimeActionKernel
from engine.world import World
from engine.work_registry import rational


ROOT = Path(__file__).resolve().parents[1]


def inputs(*, repair=False, accepted=True, names=None):
    names=names or ['pilot','helper','analyst','backup']
    a,b,c,e=names
    examples=json.loads((ROOT/'docs/design-examples/joint-activities-contract.json').read_text(encoding='utf8'))
    definition=deepcopy(examples['definitions'][1 if repair else 0])
    if repair:
        definition['duration']['unit']='logical_step'
        definition['resources'][1]['binding']['item']='cell'
    raw={'name':'Shared execution fixture','protagonist':a,'antagonist':e,
         'time':{'days':1,'slots':['s'+str(i) for i in range(61)]},
         'zones':[{'name':z} for z in ['coast','island','sea','bench']],
         'routes':{'coast':[{'to':'island','cost':1,'requires_item':'boat'}], 'island':[], 'sea':[], 'bench':[]},
         'items':[{'name':'boat','access':{'mode':'owner','allow':['companions']}},{'name':'cell'}],
         'facts':[{'id':'diagnosis_record','sources':[]}],
         'ending':[{'id':'stop','when':'turn >= 61','label':'stop'}],'target_ending':'stop',
         'responses':{'version':2,'state':{'equipment_status':'broken'},'locations':{'equipment_status':'bench'},
                      'signals':[],'actions':[],'channels':[],'events':[],'windows':[]},
         'planning':{'version':1,'actors':names,'max_depth':12,'max_nodes':256},
         'concerns':{'version':2,'conditions':[
             {'id':'arrived','label':'All arrive','mode':'achieve','visibility':'public',
              'test':{'kind':'zone','actor':a,'equals':'island'}},
             {'id':'together','label':'Stay together','mode':'maintain','visibility':'observed',
              'test':{'kind':'co_location','actor':a,'target':b}},
             {'id':'repaired','label':'Repaired','mode':'achieve','visibility':'observed',
              'test':{'kind':'state','key':'equipment_status','equals':'repaired'}}]},
         'goal_rules':{'version':1,'definitions':[{'condition':'arrived',
             'success':{'all':[{'condition':'arrived'},{'condition':'together'}]},
             'failure':{'constant':False},'period':{'start':1,'end':61}}]},
         'joint_activities':{'version':1,'definitions':[definition],'workflows':deepcopy(examples['workflows']) if repair else [],'instances':[]}}
    people=[]
    for who in names:
        people.append({'id':who,'description':who,'personality':who,'base':50,
            'traits':{'social':.5,'stubbornness':.5,'curiosity':.5,'diligence':.8,'temper':.2},
            'range':{'zones':['coast','island','sea','bench'],'entry':'bench' if repair else 'coast'},
            'verbs':['move','respond','rest','share_knowledge'],'beliefs':{},'goal':{},
            'inventory':{'boat':1,'cell':1} if who==a else {},
            'knowledge':[],'stamina':{'max':100,'current':100,'recover_per_slot':1},
            'commitments':[{'condition':'arrived','priority':8,'required':False}]})
    people[0]['companions']=names[1:]
    if repair:
        for task in raw['joint_activities']['workflows'][0]['tasks']: task['place']='bench'
        roster={'technician':[a],'holder':[b]}
        bindings={'target':'equipment','target_revision':1,'material_owner':a,'place':'bench'}
    else:
        roster={'pilot':[a],'passenger':[b,c,e]}
        bindings={'origin':'coast','destination':'island','transit_zone':'sea'}
    instance={'id':'segment','definition_id':definition['id'],'definition_revision':1,'terms_revision':1,
              'earliest_start':1,'expires_at':40,'roles':roster,'bindings':bindings}
    if accepted: instance['initial_agreement']={'source':'authored_initial_terms','accepted_by':sorted(x for v in roster.values() for x in v),
                                               'known_by':sorted(x for v in roster.values() for x in v)}
    raw['joint_activities']['instances']=[instance]
    return raw,people


def runtime(raw=None, people=None, *, repair=False, axis=None):
    if raw is None: raw,people=inputs(repair=repair)
    world=World(raw,ROOT/'projects/ship_planning/world.yaml')
    cast={p['id']:Subject.from_definition(p) for p in people}
    sim=Simulation(0,world,cast,ROOT/'unused-joint-test-output')
    sim.time_kernel=TimeActionKernel(axis or TimeAxis('logical_step',0,60))
    joint=world.joint_activities; joint.attach(sim)
    if repair:
        joint.work.tasks['diagnosis'].update(effort_minutes=10,units=1)
        cast['pilot'].knowledge.add('diagnosis_record')
    return world,cast,sim,joint


def tick(joint, at):
    joint.kernel.restore_position(at); joint.advance(); joint.recheck_running(); joint.start_due()


class JointExecutionTests(unittest.TestCase):
    def test_JA22_full_minute_pipeline_keeps_diagnosis_and_requires_real_disclosure(self):
        raw,people=inputs(repair=True);config=raw['joint_activities']
        config['definitions'][0]['duration']['unit']='minute'
        exchange=config['instances'][0];exchange['earliest_start']=12
        def stage(ident,task,actor,start):
            return {'id':ident,'definition_id':'work_'+task,'definition_revision':1,'terms_revision':1,
                    'roles':{'worker':[actor]},'bindings':{'target':'equipment','target_revision':1,'place':'bench'},
                    'earliest_start':start,'expires_at':40,'initial_agreement':{'source':'authored_initial_terms',
                    'accepted_by':[actor],'known_by':[actor]}}
        config['instances'] += [stage('diagnose','diagnosis','analyst',0),stage('verify','verification','analyst',32)]
        w,p,s,j=runtime(raw,people,axis=TimeAxis('minute',0,60));tick(j,0);tick(j,10)
        self.assertIn('diagnosis_record',p['analyst'].knowledge)
        self.assertNotIn('diagnosis_record',p['pilot'].knowledge)
        result,_,_=s.verb_engine.execute(p['analyst'],Action('share_knowledge',('pilot','diagnosis_record')),turn=11,day=1)
        self.assertEqual(result,'shared')
        tick(j,12);tick(j,32)
        self.assertEqual(w.responses.state['equipment_status'],'broken')
        self.assertFalse(j.bookings.is_busy('pilot',32));self.assertTrue(j.bookings.is_busy('analyst',32))
        tick(j,37);self.assertEqual(w.responses.state['equipment_status'],'repaired')
        self.assertEqual(j.work.read('replacement')['effort_minutes'],20)

    def test_JA23_product_is_created_once_for_the_declared_owner(self):
        raw,people=inputs(repair=True);raw['items'].append({'name':'product'})
        raw['joint_activities']['workflows'][0]['tasks'][1]['completion_effect']={
            'kind':'declared_inventory','owner_binding':'material_owner','item':'product','quantity':1}
        w,p,s,j=runtime(raw,people,repair=True);tick(j,0);tick(j,1);tick(j,21);j.advance()
        self.assertEqual(sum(a.inventory.get('product',0) for a in p.values()),1)
        self.assertEqual(p['pilot'].inventory['product'],1)

    def test_JA37_unreceived_reply_does_not_change_another_persons_forecast(self):
        raw,people=inputs(accepted=False);w,p,s,j=runtime(raw,people)
        i=j.instances['segment'];i['known_by']=list(p)
        before=w.planner.search(w,p['helper'],'arrived',1)
        # A remote actor can decide, but the reply has not reached anyone else.
        p['analyst'].zone='bench';j.record_consent('segment','analyst',i['terms_hash'],True)
        after=w.planner.search(w,p['helper'],'arrived',1)
        self.assertEqual(before,after)
        self.assertEqual(i['status'],'proposed')

    def test_JA40_opted_out_world_has_identical_choices_and_random_stream(self):
        from test_goal_planner import inputs as old_inputs,episode
        raw,people=old_inputs();before=deepcopy(raw)
        w,p,first=episode(raw,people);w,p,second=episode(before,people)
        self.assertEqual(first,second)
        self.assertIsNone(w.joint_activities)

    def test_JA41_search_limits_are_not_terminal_failure(self):
        raw,people=inputs(accepted=False);raw['planning'].update(max_nodes=16,max_depth=1)
        w,p,s,j=runtime(raw,people);j.instances['segment']['known_by']=list(p)
        plan=w.planner.search(w,p['pilot'],'arrived',1)
        self.assertIn(plan['status'],{'conditional','search_limit'})
        self.assertNotEqual(w.goal_rules.actor_view(w,'pilot','arrived',1)['status'],'failed')

    def test_JA42_ui_groups_one_segment_with_role_costs_and_raw_sources(self):
        from gapengine.explanations import explain_rows
        from viewer.planning_view import render
        w,p,s,j=runtime(repair=True);tick(j,0);tick(j,1);tick(j,21)
        rows=[{'kind':'header','protagonist':'pilot'}]+[
            {'kind':'event','turn':event['at']+1,'subject':'pilot','verb':'joint_'+event['phase'],
             'details':{'joint_activity':event},'delta':{}} for event in j.history]
        explanation=explain_rows(rows,experiment='example',cell='a|b')
        html=render(explanation)
        self.assertEqual(html.count('data-joint-segment="segment"'),1)
        self.assertIn('pilot',html);self.assertIn('holder',html);self.assertIn('原記録',html)
        self.assertIn('完了',html);self.assertIn('参加者と体力の消費',html)

    def test_shared_goal_search_offers_joint_consent_without_reserving_during_search(self):
        raw,people=inputs(accepted=False);w,p,s,j=runtime(raw,people)
        j.instances['segment']['known_by']=list(p)
        before=j.snapshot()
        plan=w.planner.search(w,p['pilot'],'arrived',1)
        self.assertEqual(j.snapshot(),before)
        self.assertTrue(plan['steps'])
        self.assertEqual(plan['steps'][0]['verb'],'join_activity')
        self.assertEqual(plan['status'],'conditional')
        options=candidates(p['pilot'],w,SimpleNamespace(turn=1,day=1))
        self.assertTrue(any(a.verb=='join_activity' for a,_ in options))

    def test_unforced_goal_choices_obtain_consents_then_complete(self):
        raw,people=inputs(accepted=False);raw['joint_activities']['instances'][0]['known_by']=[p['id'] for p in people]
        world=World(raw,ROOT/'projects/ship_planning/world.yaml');cast={p['id']:Subject.from_definition(p) for p in people}
        with tempfile.TemporaryDirectory() as folder:
            sim=Simulation(0,world,cast,Path(folder));sim.run()
        self.assertEqual(world.joint_activities.instances['segment']['status'],'completed')
        self.assertEqual({p.zone for p in cast.values()},{'island'})

    def test_JA01_JA11_all_travel_in_one_window_with_one_vessel(self):
        w,p,s,j=runtime(); tick(j,0); tick(j,1)
        self.assertEqual({x.zone for x in p.values()},{'sea'})
        self.assertTrue(all(j.bookings.is_busy(a,1) for a in p))
        self.assertTrue(all(x.in_transit for x in p.values()))
        tick(j,2)
        self.assertEqual({x.zone for x in p.values()},{'island'})
        self.assertEqual(p['pilot'].inventory['boat'],1)
        self.assertEqual(p['pilot'].stamina,98)
        self.assertEqual(p['helper'].stamina,100)
        self.assertFalse(any(j.bookings.is_busy(a,2) for a in p))

    def test_JA02_JA03_JA04_missing_duplicate_and_excess_people_rejected(self):
        for modify in [lambda i:i['roles']['pilot'].clear(),lambda i:i['roles']['passenger'].__setitem__(0,'pilot'),
                       lambda i:i['roles']['passenger'].append('pilot')]:
            raw,people=inputs();modify(raw['joint_activities']['instances'][0])
            with self.assertRaises(ValueError): runtime(raw,people)

    def test_JA05_start_rechecks_knowledge_and_permissions(self):
        w,p,s,j=runtime(repair=True);p['pilot'].knowledge.clear();tick(j,0);tick(j,1)
        self.assertNotEqual(j.instances['segment']['status'],'running')
        self.assertEqual(j.work.read('replacement')['units'],0)

    def test_JA06_JA09_stale_or_unreceived_consent_cannot_reserve(self):
        raw,people=inputs(accepted=False);w,p,s,j=runtime(raw,people)
        with self.assertRaises(ValueError):j.record_consent('segment','pilot',j.instances['segment']['terms_hash'],True)
        j.instances['segment']['known_by']=list(p)
        with self.assertRaises(ValueError):j.record_consent('segment','pilot','old',True)
        self.assertEqual(j.bookings.entries,{})

    def test_JA07_atomic_conflicts_and_half_open_intervals(self):
        b=ActivityBookings();self.assertTrue(b.reserve_all('one',1,3,['actor:a','resource:boat']))
        self.assertFalse(b.reserve_all('two',2,4,['actor:b','resource:boat']))
        self.assertNotIn('two',b.entries);self.assertTrue(b.reserve_all('three',3,4,['actor:a','resource:boat']))

    def test_JA08_same_inventory_under_two_aliases_rejected(self):
        raw,people=inputs();d=raw['joint_activities']['definitions'][0]
        other=deepcopy(d['resources'][0]);other['key']='alias';d['resources'].append(other)
        w,p,s,j=runtime(raw,people);tick(j,0)
        self.assertEqual(j.bookings.entries,{})

    def test_JA10_late_consents_reserve_next_boundary(self):
        raw,people=inputs(accepted=False);w,p,s,j=runtime(raw,people)
        i=j.instances['segment'];i['known_by']=list(p)
        for a in p:j.record_consent(i['id'],a,i['terms_hash'],True)
        j.start_due();self.assertEqual(i['start'],1);self.assertEqual(i['status'],'reserved')

    def test_JA16_units_and_observation_horizon(self):
        raw,people=inputs();raw['joint_activities']['definitions'][0]['duration']['unit']='minute'
        with self.assertRaises(ValueError):runtime(raw,people)
        raw,people=inputs();raw['joint_activities']['instances'][0].update(earliest_start=60,expires_at=60)
        w,p,s,j=runtime(raw,people);tick(j,0)
        self.assertEqual(j.bookings.entries,{})

    def test_JA17_JA19_twenty_work_not_forty_and_individual_costs(self):
        w,p,s,j=runtime(repair=True);tick(j,0);tick(j,1);tick(j,21)
        self.assertEqual(j.work.read('replacement')['effort_minutes'],20)
        self.assertEqual(j.work.read('replacement')['units'],1)
        self.assertEqual((p['pilot'].stamina,p['helper'].stamina),(98,99))
        self.assertEqual(p['pilot'].inventory['cell'],0)
        self.assertEqual(w.responses.state['equipment_status'],'broken')

    def test_JA20_JA21_diagnosis_retained_but_not_automatically_shared(self):
        w,p,s,j=runtime(repair=True)
        p['pilot'].knowledge.clear();p['analyst'].knowledge.add('diagnosis_record')
        tick(j,0);tick(j,1)
        self.assertEqual(j.work.read('diagnosis')['units'],1)
        self.assertNotIn('diagnosis_record',p['pilot'].knowledge)
        self.assertNotEqual(j.instances['segment']['status'],'running')

    def test_JA24_changed_target_cannot_reuse_work(self):
        w,p,s,j=runtime(repair=True);tick(j,0);tick(j,1);tick(j,8)
        j.interrupt('segment','test')
        i=deepcopy(j.instances['segment']['terms']);i.update(id='other',terms_revision=2)
        i['bindings']['target_revision']=2
        h=j.propose(i,authored=True);j.instances['other']['known_by']=['pilot','helper']
        for a in ('pilot','helper'):j.record_consent('other',a,h,True,authored=True)
        self.assertFalse(j.reserve('other'))

    def test_JA25_no_partial_start_cost_when_one_person_lacks_energy(self):
        w,p,s,j=runtime(repair=True);p['helper'].stamina=0;tick(j,0);tick(j,1)
        self.assertEqual(p['pilot'].stamina,100);self.assertEqual(p['pilot'].inventory['cell'],1)
        self.assertEqual(j.work.read('replacement')['effort_minutes'],0)

    def test_JA26_JA27_interruption_keeps_seven_then_new_roster_finishes_thirteen(self):
        w,p,s,j=runtime(repair=True)
        j.instances['segment']['earliest_start']=12;j.instances['segment']['terms']['earliest_start']=12
        tick(j,0);tick(j,12);tick(j,19);j.interrupt('segment','participant_left')
        self.assertEqual(j.work.read('replacement')['effort_minutes'],7)
        self.assertEqual(rational(j.instances['segment']['spent']['pilot']),Fraction(7,10))
        self.assertEqual(rational(j.instances['segment']['spent']['helper']),Fraction(7,20))
        i=deepcopy(j.instances['segment']['terms']);i.update(id='resume',earliest_start=20)
        i['roles']['technician']=['backup'];i['bindings']['material_owner']='helper'
        p['helper'].inventory['cell']=p['pilot'].inventory.pop('cell');p['backup'].knowledge.add('diagnosis_record')
        h=j.propose(i,authored=True);j.instances['resume']['known_by']=['backup','helper']
        for a in ('backup','helper'):j.record_consent('resume',a,h,True,authored=True)
        tick(j,20);tick(j,21);tick(j,34)
        self.assertEqual(j.instances['segment']['status'],'interrupted')
        self.assertEqual(j.instances['resume']['finish']-j.instances['resume']['start'],13)
        self.assertEqual(j.work.read('replacement')['effort_minutes'],20)
        self.assertEqual(p['helper'].inventory['cell'],0)

    def test_JA29_transit_loss_keeps_actual_transit_location(self):
        raw,people=inputs();raw['joint_activities']['definitions'][0]['duration']['units']=4
        w,p,s,j=runtime(raw,people);tick(j,0);tick(j,1);p['pilot'].inventory['boat']=0;tick(j,2)
        self.assertEqual(j.instances['segment']['status'],'interrupted')
        self.assertEqual({x.zone for x in p.values()},{'sea'})
        self.assertEqual(p['pilot'].stamina,98)

    def test_JA30_consumed_start_material_is_not_refunded(self):
        raw,people=inputs(repair=True);raw['joint_activities']['definitions'][0]['resources'][1]['settlement']='consume_on_start'
        w,p,s,j=runtime(raw,people,repair=True);tick(j,0);tick(j,1);j.interrupt('segment','stop')
        self.assertEqual(p['pilot'].inventory['cell'],0)

    def test_JA31_duplicate_advance_does_not_duplicate_effect_or_work(self):
        w,p,s,j=runtime(repair=True);tick(j,0);tick(j,1);tick(j,21);before=j.snapshot()
        j.advance();j.advance();self.assertEqual(j.snapshot(),before)

    def test_JA32_restore_continues_without_replaying(self):
        w,p,s,j=runtime(repair=True);tick(j,0);tick(j,1);tick(j,8)
        state=j.snapshot();j.restore(json.loads(json.dumps(state)));self.assertEqual(j.snapshot(),state)
        tick(j,21);self.assertEqual(j.work.read('replacement')['effort_minutes'],20)
        self.assertEqual(p['pilot'].inventory['cell'],0)

    def test_JA33_checkpoint_rejects_missing_pointers_and_reversed_cursor(self):
        w,p,s,j=runtime(repair=True);tick(j,0);tick(j,1);tick(j,8);state=j.snapshot()
        for alter in [lambda x:x['bookings'].clear(),lambda x:x['instances']['segment'].update(cursor=40),
                      lambda x:x['instances']['segment']['roles']['holder'].clear(),
                      lambda x:x.update(signature='other-world')]:
            bad=deepcopy(state);alter(bad)
            with self.assertRaises(ValueError):j.restore(bad)
            self.assertEqual(j.snapshot(),state)

    def test_JA34_completion_exception_rolls_back_cost_progress_and_items(self):
        w,p,s,j=runtime(repair=True);tick(j,0);tick(j,1);j.kernel.restore_position(21)
        state=j.snapshot();energy=p['pilot'].stamina
        with patch.object(j,'_finish_work',side_effect=RuntimeError('fault')):
            with self.assertRaises(RuntimeError):j.advance()
        self.assertEqual(j.snapshot(),state);self.assertEqual(p['pilot'].stamina,energy)
        self.assertEqual(p['pilot'].inventory['cell'],1)

    def test_JA35_JA36_required_personal_condition_blocks_consent(self):
        raw,people=inputs(accepted=False)
        raw['concerns']['conditions'].append({'id':'stay','label':'Remain at coast','mode':'maintain','visibility':'public',
                                             'test':{'kind':'zone','actor':'helper','equals':'coast'}})
        people[1]['commitments']=[{'condition':'stay','priority':10,'required':True}]
        w,p,s,j=runtime(raw,people);i=j.instances['segment'];i['known_by']=list(p)
        with self.assertRaises(ValueError):j.record_consent('segment','helper',i['terms_hash'],True)
        self.assertEqual(p['helper'].commitments,people[1]['commitments'])

    def test_JA38_generic_names_preserve_execution(self):
        raw,people=inputs(names=['captain','engineer','scientist','reserve']);w,p,s,j=runtime(raw,people)
        tick(j,0);tick(j,1);tick(j,2)
        self.assertEqual({x.zone for x in p.values()},{'island'})
        self.assertEqual(p['captain'].stamina,98)

    def test_JA43_shared_container_observed_without_arrival(self):
        w,p,s,j=runtime();tick(j,0);tick(j,1)
        self.assertEqual(w.concerns.evaluate(w,'together',actor='helper')['state'],'true')
        self.assertEqual(w.concerns.evaluate(w,'arrived',actor='helper')['state'],'false')
        self.assertEqual(w.concerns.evaluate(w,'arrived')['state'],'false')

    def test_JA44_sum_role_rate_is_capped(self):
        raw,people=inputs(repair=True);d=raw['joint_activities']['definitions'][0]
        d['progress'].pop('per_unit');d['progress'].update(model='sum_role_rates',role_rates={
            'technician':{'numerator':2,'denominator':1},'holder':{'numerator':1,'denominator':1}},cap={'numerator':2,'denominator':1})
        w,p,s,j=runtime(raw,people,repair=True);tick(j,0);tick(j,1);tick(j,11)
        self.assertEqual(j.work.read('replacement')['effort_minutes'],20)
        self.assertEqual(j.instances['segment']['status'],'completed')

    def test_real_simulation_skips_all_participant_choices_during_segment(self):
        raw,people=inputs();world=World(raw,ROOT/'projects/ship_planning/world.yaml')
        cast={p['id']:Subject.from_definition(p) for p in people};decisions=[]
        with tempfile.TemporaryDirectory() as folder:
            sim=Simulation(0,world,cast,Path(folder))
            def choose(this,actor,forced=None):
                decisions.append((this.turn,actor.id));return Action('rest'),1
            with patch.object(Simulation,'choose_action',choose):sim.run()
            rows=[json.loads(x) for x in (Path(folder)/'layers.jsonl').read_text(encoding='utf8').splitlines()]
        self.assertFalse(any(turn==2 for turn,_ in decisions))
        self.assertEqual(len([r for r in rows if r.get('verb')=='joint_completed']),1)
        self.assertEqual({x.zone for x in cast.values()},{'island'})


if __name__=='__main__':unittest.main()
