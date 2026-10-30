from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.actions import Action
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from test_joint_activities import ROOT,inputs,runtime,tick
from test_joint_calendar import project,simulation


class JointAcceptanceTests(unittest.TestCase):
    def test_fixed_work_rate_requires_every_declared_contributing_role(self):
        raw,people=inputs(repair=True);d=raw['joint_activities']['definitions'][0]
        d['roles'][1]['min']=0;raw['joint_activities']['instances'][0]['roles']['holder']=[]
        with self.assertRaises(ValueError):runtime(raw,people)

    def test_checkpoint_is_bound_to_world_routes_and_person_profiles(self):
        raw,people=inputs();w,p,s,j=runtime(raw,people);tick(j,0);tick(j,1);state=j.snapshot()
        changed=deepcopy(raw);changed['routes']['coast'][0]['cost']=2
        w2,p2,s2,j2=runtime(changed,people)
        with self.assertRaises(ValueError):j2.restore(state)
        cast=deepcopy(people);cast[1]['traits']['diligence']=.1
        w3,p3,s3,j3=runtime(raw,cast)
        with self.assertRaises(ValueError):j3.restore(state)

    def test_duplicate_consent_does_not_reopen_a_reserved_segment(self):
        w,p,s,j=runtime();tick(j,0);before=j.snapshot();i=j.instances['segment']
        j.record_consent('segment','pilot',i['terms_hash'],True)
        self.assertEqual(j.snapshot(),before)

    def test_mid_action_energy_loss_does_not_produce_unpaid_work(self):
        w,p,s,j=runtime(repair=True);tick(j,0);tick(j,1);p['pilot'].stamina=.2;tick(j,8)
        self.assertEqual(j.instances['segment']['status'],'interrupted')
        self.assertEqual(j.work.read('replacement')['effort_minutes'],2)

    def test_competing_accepted_activities_use_personal_priorities_before_booking(self):
        raw,people=inputs();raw['routes']['coast'].append({'to':'bench','cost':1,'requires_item':'boat'})
        low=deepcopy(raw['joint_activities']['instances'][0]);low['id']='aaa-lower-priority';low['bindings']['destination']='bench'
        raw['joint_activities']['instances'].insert(0,low)
        w,p,s,j=runtime(raw,people);tick(j,0);tick(j,1);tick(j,2)
        self.assertEqual(j.instances['segment']['status'],'completed')
        self.assertNotEqual(j.instances['aaa-lower-priority']['status'],'running')
        self.assertEqual({a.zone for a in p.values()},{'island'})

    def test_JA14_physical_arrival_precedes_destruction_at_same_boundary(self):
        raw,people=inputs();w=World(raw,ROOT/'projects/ship_planning/world.yaml')
        cast={p['id']:Subject.from_definition(p) for p in people}
        with tempfile.TemporaryDirectory() as folder:
            sim=Simulation(0,w,cast,Path(folder));observed=[]
            def scheduled(writer,slot=None):
                if sim.time_kernel.now==2:
                    observed.append(w.joint_activities.instances['segment']['status'])
                    cast['pilot'].inventory['boat']=0
            with patch.object(sim,'_apply_scheduled',scheduled),patch.object(sim,'choose_action',lambda actor,forced=None:(Action('rest'),1)):
                sim.run()
        self.assertEqual(observed,['completed'])
        self.assertEqual({p.zone for p in cast.values()},{'island'})

    def test_JA15_personal_timed_action_blocks_the_entire_shared_booking(self):
        raw,people=inputs();raw['action_timing']={'version':1,'rules':[{'verb':'rest','duration':3,'interruptible':True}]}
        w,p,s,j=runtime(raw,people);s.turn=1;s.day=1;w.action_timing.attach(s)
        result,_,_=w.action_timing.start(p['helper'],Action('rest'))
        self.assertEqual(result,'started');j.start_due()
        self.assertEqual(j.bookings.entries,{})
        self.assertEqual({a.zone for a in p.values()},{'coast'})

    def test_JA18_old_calendar_writer_cannot_double_count_registered_work(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);project(base/'project');sim=simulation(base/'project',base/'run');rt=sim.slot_runtime
            before=deepcopy(rt.ledger)
            rt._start({'id':'individual','actor':'Ada','at':480,'verb':'work','task_id':'pieces','minutes':10})
            self.assertEqual(rt.ledger,before)
            self.assertEqual(rt.rows[-1]['details']['reason'],'registered_work_requires_shared_writer')

    def test_JA28_optional_departure_closes_old_segment_and_retains_work(self):
        raw,people=inputs(repair=True);d=raw['joint_activities']['definitions'][0]
        d['roles'].append({'id':'optional','min':0,'max':1,'requires':{'verbs':['respond'],'facts':[],'vitalities':['alive','revived']}})
        inst=raw['joint_activities']['instances'][0];inst['roles']['optional']=['analyst']
        for key in ('known_by','accepted_by'):inst['initial_agreement'][key].append('analyst')
        w,p,s,j=runtime(raw,people,repair=True);tick(j,0);tick(j,1);tick(j,8)
        result,_,_=s.verb_engine.execute(p['analyst'],Action('leave_activity',('segment',)),turn=9,day=1)
        self.assertEqual(result,'applied')
        self.assertEqual(j.instances['segment']['status'],'interrupted')
        new=deepcopy(j.instances['segment']['terms']);new.update(id='remaining',earliest_start=9)
        new['roles']['optional']=[];h=j.propose(new,authored=True);i=j.instances['remaining'];i['known_by']=['pilot','helper']
        for actor in i['known_by']:j.record_consent('remaining',actor,h,True,authored=True)
        j.reserve('remaining');tick(j,9);tick(j,22)
        self.assertEqual(j.work.read('replacement')['effort_minutes'],20)
        self.assertEqual(j.instances['segment']['status'],'interrupted')

    def test_JA32_fresh_ordinary_runtime_restores_physical_state_without_replay(self):
        w,p,s,j=runtime(repair=True);tick(j,0);tick(j,1);tick(j,8);checkpoint=j.snapshot()
        w2,p2,s2,j2=runtime(repair=True);j2.restore(checkpoint)
        self.assertEqual(j2.snapshot(),checkpoint)
        tick(j,21);tick(j2,21)
        self.assertEqual(j.snapshot(),j2.snapshot())

    def test_JA35_higher_priority_deadline_blocks_lower_priority_participation(self):
        raw,people=inputs(accepted=False);raw['goal_rules']['definitions'].append({'condition':'repaired',
            'success':{'condition':'repaired'},'failure':{'constant':False},'period':{'start':1,'end':2}})
        people[0]['commitments'].append({'condition':'repaired','priority':10,'required':False})
        w,p,s,j=runtime(raw,people);i=j.instances['segment'];i['known_by']=list(p)
        with self.assertRaises(ValueError):j.record_consent('segment','pilot',i['terms_hash'],True)

    def test_JA39_real_contributor_receipts_fulfill_native_promise_once(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);project(base/'project');sim=simulation(base/'project',base/'run');rt=sim.slot_runtime
            terms={'promise_id':'group-work','version':1,'proposer':'Ada','promisor':'Ada','beneficiary':'Bo',
                'acceptance_parties':['Ada','Bo'],'place':'Bench','start':480,'deadline':512,'grace_until':None,
                'requirements':[{'task_id':'pieces','units':3,'contribution_by':'Ada','delivered_to':None}]}
            rt.registry.offer(terms,480,'offer')
            h=rt.registry.terms_hash(terms)
            rt.registry.accept('group-work',1,'Ada',h,480,'accept-Ada')
            rt.registry.accept('group-work',1,'Bo',h,480,'accept-Bo')
            rt.run(until=512)
            self.assertEqual(rt.registry.get('group-work',1)['status'],'fulfilled')
            self.assertEqual(rt.ledger['tasks']['pieces']['units'],3)

    def test_JA43_separate_vessels_do_not_share_their_interiors(self):
        raw,people=inputs();first=raw['joint_activities']['instances'][0]
        first['roles']['passenger']=['helper'];first['initial_agreement']['accepted_by']=['pilot','helper'];first['initial_agreement']['known_by']=['pilot','helper']
        other=deepcopy(first);other.update(id='other-boat');other['roles']={'pilot':['analyst'],'passenger':['backup']}
        other['initial_agreement']['accepted_by']=['analyst','backup'];other['initial_agreement']['known_by']=['analyst','backup']
        raw['joint_activities']['instances'].append(other);people[2]['inventory']={'boat':1};people[2]['companions']=['backup']
        raw['concerns']['conditions'].append({'id':'separate','label':'Two vessels','mode':'maintain','visibility':'observed',
                                            'test':{'kind':'co_location','actor':'pilot','target':'analyst'}})
        w,p,s,j=runtime(raw,people);tick(j,0);tick(j,1)
        self.assertEqual(w.concerns.evaluate(w,'separate')['state'],'false')
        self.assertEqual(w.concerns.evaluate(w,'separate',actor='pilot')['state'],'unknown')
        j.interrupt('segment','damage');j.interrupt('other-boat','damage')
        self.assertEqual(w.concerns.evaluate(w,'separate')['state'],'false')

    def test_unknown_provider_and_zero_participant_contract_are_rejected(self):
        for modify in [lambda d:d.update(provider='arbitrary_eval'),lambda d:d['roles'][0].update(min=0)]:
            raw,people=inputs();modify(raw['joint_activities']['definitions'][0])
            with self.assertRaises(ValueError):runtime(raw,people)

    def test_dependency_artifact_cannot_be_reused_for_another_target(self):
        w,p,s,j=runtime(repair=True)
        j.work.scopes['diagnosis']=['old-target',1]
        tick(j,0);self.assertEqual(j.bookings.entries,{})


if __name__=='__main__':unittest.main()
