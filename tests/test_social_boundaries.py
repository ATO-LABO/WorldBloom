from copy import deepcopy
import random
import unittest
from engine.actions import Action
from engine.verbs import VerbEngine
from engine.world import World
from engine.subject import Subject
from test_shared_planning import inputs,run,ROOT

class SocialBoundaryTests(unittest.TestCase):
    def world(self,name='ship'):
        raw,people=inputs(name);world=World(raw,ROOT/'projects'/f'{name}_shared_planning/world.yaml');cast={p['id']:Subject.from_definition(p) for p in people};world.bind_subjects(cast);world.goal_rules.record(world,1)
        return world,cast,VerbEngine(world,random.Random(0))

    def test_private_helpers_requirements_do_not_change_requesters_forecast(self):
        raw,people=inputs('ship');world,cast,_=self.world()
        first=world.planner.search(world,cast['通信士'],'beacon_sent',1)
        people[1]['commitments'][1]['required']=True
        changed=World(raw,ROOT/'projects/ship_shared_planning/world.yaml');other={p['id']:Subject.from_definition(p) for p in people};changed.bind_subjects(other);changed.goal_rules.record(changed,1)
        self.assertEqual(first,changed.planner.search(changed,other['通信士'],'beacon_sent',1))

    def test_remote_acceptance_is_not_received_knowledge(self):
        world,cast,engine=self.world();a,b=cast['通信士'],cast['補給係']
        engine.execute(a,Action('request_response',(b.id,'prepare_reserve')),turn=1,day=1)
        before=world.social_planner.visible(world,a);a.zone='保管庫'
        result,_,_=engine.execute(b,Action('answer_response',('reserve_help','accept')),turn=2,day=1)
        self.assertEqual(result,'answered');self.assertEqual(before,world.social_planner.visible(world,a))
        self.assertIsNone(world.social_planner.messages['reserve_help']['known_reply'])

    def test_failed_accepted_work_keeps_failure_receipt_and_disables_repeated_forecast(self):
        world,cast,engine=self.world();a,b=cast['通信士'],cast['補給係']
        engine.execute(a,Action('request_response',(b.id,'prepare_reserve')),turn=1,day=1)
        engine.execute(b,Action('answer_response',('reserve_help','accept')),turn=1,day=1)
        world.social_planner.after_action(world,b,Action('respond',('prepare_reserve',)),'failed',{},2)
        self.assertEqual(world.social_planner.messages['reserve_help']['known_reply'],'failed')
        self.assertFalse(world.social_planner.can_work(b.id,'prepare_reserve'))
        from engine.social_planning import key
        self.assertEqual(world.social_planner.visible(world,a)[key('reserve_help','request')],'failed')

    def test_bridge_guard_is_actor_scoped_and_rechecked_on_real_move(self):
        world,cast,engine=self.world('melos');actor=cast['メロス'];actor.zone='西岸'
        self.assertIn('東岸',world.reachable_paths(actor))
        actor.zone='王宮';known=deepcopy(world.planner.search(world,actor,'friend_saved',1))
        world.responses.state['橋']='流失'
        self.assertEqual(known,world.planner.search(world,actor,'friend_saved',1))
        actor.zone='西岸'
        self.assertNotIn('東岸',world.reachable_paths(actor))
        result,_,_=engine.execute(actor,Action('move',('東岸',)),turn=2,day=1)
        self.assertEqual(result,'invalid');self.assertEqual(actor.zone,'西岸')

    def test_unknown_route_condition_rejected_at_load(self):
        raw,_=inputs('melos');raw['routes']['王宮'][0]['requires_conditions']=['missing_condition']
        with self.assertRaises(ValueError):World(raw,ROOT/'world.yaml')

    def test_timed_negotiation_allows_real_recipient_response_before_expiry(self):
        raw,people=inputs('ship')
        raw['social_planning']['requests']=[]
        raw['responses']['actions']=[s for s in raw['responses']['actions'] if s['id']!='prepare_reserve']
        people[0]['commitments']=people[0]['commitments'][:2]
        people[1]['verbs']=['rest','concede'];people[1]['inventory']={'予備電池':1}
        people[1]['commitments']=[{'condition':'keeper_paid','priority':10,'required':False},{'condition':'stock_battery','priority':5,'required':False}]
        world,_,rows=run(raw,people)
        results=[r.get('details',{}).get('activity_result') for r in rows]
        self.assertIn('offered',results);self.assertIn('conceded',results)
        self.assertEqual(next(r for r in world.goal_rules.report(world) if r['condition']=='beacon_sent')['status'],'achieved')
