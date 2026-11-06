from copy import deepcopy
from types import SimpleNamespace
import unittest

from engine.actions import Action, candidates
from engine.action_usage import current_policy, parse
from engine.verbs import VerbEngine
from engine.action_constraints import validate_bound
from execution import action_usage_settings as settings, world_editor
from execution.provenance import ConfigError
from test_action_usage import current_response_fixture, load_fixture
from test_engine import FixedRandom
from test_goal_planner import load
import test_action_usage as usage_tests


def restrict(world, ref, actors=None, slots=None):
    world.action_usage, world.mechanics = current_policy(world)
    world.action_usage.setdefault('constraints', {})[ref] = {'actors': actors, 'slots': slots}


class ConstraintEngineTests(unittest.TestCase):
    def ship(self):
        w, p = load_fixture(); a = p['桃太郎']; a.zone='海'
        a.knowledge.add('造船術'); a.inventory.update({'木材':2,'縄':1})
        return w, p, a

    def test_named_band_filters_candidates_and_direct_execution_without_charge(self):
        for turn in (1,2,3,4,5,8):
            w,p,a=self.ship();restrict(w,'recipe:船',slots=['昼'])
            expected=w.slots[(turn-1)%len(w.slots)]=='昼'
            options=candidates(a,w,SimpleNamespace(turn=turn,day=(turn-1)//len(w.slots)+1))
            self.assertEqual(any(v.verb=='craft' and v.args==('船',) for v,_ in options),expected)
            before=deepcopy(a.__dict__)
            result=VerbEngine(w,FixedRandom([0])).execute(a,Action('craft',('船',)),turn=turn,day=1)
            if expected:self.assertEqual(result[0],'crafted')
            else:
                self.assertEqual(result[1]['reason'],'action_time_restricted')
                self.assertEqual(before,a.__dict__)

    def test_person_filter_is_leaf_specific_and_required_rest_unrestricted(self):
        w,p,a=self.ship();restrict(w,'recipe:船',actors=[])
        self.assertFalse(any(v.verb=='craft' for v,_ in candidates(a,w,SimpleNamespace(turn=1,day=1))))
        self.assertEqual(VerbEngine(w,FixedRandom([0])).execute(a,Action('craft',('船',)),turn=1,day=1)[1]['reason'],'action_actor_restricted')
        self.assertEqual(VerbEngine(w,FixedRandom([0])).execute(a,Action('rest'),turn=4,day=1)[0],'rested')

    def test_explicit_actor_grant_generates_and_executes_without_mutating_subject_verbs(self):
        w,p,a=self.ship();a.verbs=set(a.verbs)-{'craft'};before=set(a.verbs)
        restrict(w,'recipe:船',actors=[a.id])
        self.assertTrue(any(v.verb=='craft' for v,_ in candidates(a,w,SimpleNamespace(turn=1,day=1))))
        self.assertEqual(VerbEngine(w,FixedRandom([0])).execute(a,Action('craft',('船',)),turn=1,day=1)[0],'crafted')
        self.assertEqual(before,set(a.verbs))

    def test_recipe_grant_does_not_leak_to_unconfigured_recipe(self):
        w,p,a=self.ship();a.verbs=set(a.verbs)-{'craft'};w.recipes['別の船']=deepcopy(w.recipes['船']);w.items['別の船']=deepcopy(w.items['船'])
        restrict(w,'recipe:船',actors=[a.id]);w.action_usage['enabled'].append('recipe:別の船')
        choices=[v.args for v,_ in candidates(a,w,SimpleNamespace(turn=1,day=1)) if v.verb=='craft']
        self.assertIn(('船',),choices);self.assertNotIn(('別の船',),choices)
        result=VerbEngine(w,FixedRandom([0])).execute(a,Action('craft',('別の船',)),turn=1,day=1)
        self.assertEqual(result[1]['reason'],'action_actor_restricted')

    def test_schema_unknown_people_slots_and_mandatory_rest_reject(self):
        w,p,a=self.ship();policy,mechanics=current_policy(w)
        for ref,rule in [('verb:rest',{'actors':None,'slots':['昼']}),('recipe:船',{'actors':None,'slots':[]}),('recipe:船',{'actors':None,'slots':['missing']}),('recipe:船',{'actors':['桃太郎','桃太郎'],'slots':None})]:
            with self.assertRaises(ValueError):parse(w,{**policy,'constraints':{ref:rule}},mechanics)
        restrict(w,'recipe:船',actors=['missing'])
        with self.assertRaises(ValueError):validate_bound(w)

    def test_scheduled_conflict_checked_in_both_directions(self):
        w,p,a=self.ship();w.scheduled_events=[{'id':'ship','day':1,'slot':'夜','targets':['桃太郎'],'force_action':{'verb':'craft','args':['船']}}]
        restrict(w,'recipe:船',actors=['桃太郎'],slots=['昼'])
        with self.assertRaisesRegex(ValueError,'ship'):validate_bound(w)
        w.action_usage['constraints']['recipe:船']['slots']=['夜'];validate_bound(w)
        w.action_usage['constraints']['recipe:船']['actors']=[]
        with self.assertRaisesRegex(ValueError,'ship'):validate_bound(w)

    def test_absent_and_empty_constraints_do_not_change_legacy_candidates(self):
        w,p,a=self.ship();before=[(v.verb,v.args,weight) for v,weight in candidates(a,w,SimpleNamespace(turn=1,day=1))]
        w.action_usage,w.mechanics=current_policy(w);w.action_usage['constraints']={}
        after=[(v.verb,v.args,weight) for v,weight in candidates(a,w,SimpleNamespace(turn=1,day=1))]
        self.assertEqual(before,after)

    def test_response_grant_replaces_native_actor_list_without_bypassing_domain_conditions(self):
        raw,people=current_response_fixture();w,p,_=load(raw,people);a=p['通信士']
        spec=w.responses.actions['send_beacon']
        before=w.responses._option(w,a,spec,1,1)['excluded']
        a.verbs=set(a.verbs)-{'respond'};spec['actors']=[]
        restrict(w,'response:send_beacon',actors=[a.id])
        self.assertEqual(before,w.responses._option(w,a,spec,1,1)['excluded'])
        # Explicit restrictions now use the approved pause/resume work schedule.
        policy=deepcopy(w.action_usage);policy['constraints']['response:send_beacon']['slots']=['昼']
        self.assertEqual(parse(w,policy,w.mechanics)[0]['constraints']['response:send_beacon']['slots'],['昼'])

    def test_planning_forecast_excludes_forbidden_person_and_current_band(self):
        raw,people=current_response_fixture();w,p,_=load(raw,people);a=p['通信士']
        restrict(w,'response:send_beacon',actors=[])
        plan=w.planner.search(w,a,'beacon_sent',1)
        self.assertFalse(any(s['verb']=='respond' for s in plan.get('steps',[])))


class ConstraintStorageTests(usage_tests.UsageStorageTests):
    def test_constraints_save_atomic_and_stale_people_reject(self):
        draft=self.draft();draft['constraints']={'verb:observe':{'actors':['桃太郎'],'slots':['昼']}}
        before=world_editor.snapshot(self.store,'modern');result=settings.save(self.store,'modern',self.submit(draft))
        self.assertEqual(result['model']['constraints'],draft['constraints'])
        self.assertEqual(before['people'],world_editor.snapshot(self.store,'modern')['people'])
        self.assertEqual(before['world']['time'],world_editor.snapshot(self.store,'modern')['world']['time'])
        stale={**draft,'constraints':{}}
        with self.assertRaises(ConfigError):settings.check(self.store,'modern',stale)

    def test_time_reference_deletion_rejected_and_reorder_retains_names(self):
        from execution.time_slot_references import snapshot,validate
        draft=self.draft();draft['constraints']={'verb:observe':{'actors':None,'slots':['昼']}}
        settings.save(self.store,'modern',self.submit(draft))
        world=world_editor.snapshot(self.store,'modern')['world'];catalog=snapshot(self.store,'modern',world)
        self.assertTrue(validate(catalog,['朝','夜'],world['time']['days'])[0])
        self.assertFalse(validate(catalog,list(reversed(world['time']['slots'])),world['time']['days'])[0])

    def test_invalid_constraints_never_write(self):
        path=self.repo/'projects/momotaro/world.yaml';before=path.read_bytes()
        for rule in ({'actors':['missing'],'slots':None},{'actors':None,'slots':[]},{'actors':None,'slots':['missing']}):
            draft=self.draft();draft['constraints']={'verb:observe':rule}
            with self.assertRaises(ConfigError):settings.check(self.store,'modern',draft)
            self.assertEqual(before,path.read_bytes())
