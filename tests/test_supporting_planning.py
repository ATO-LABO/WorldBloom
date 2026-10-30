from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from engine.actions import Action,candidates
from engine.concerns import Concerns,coordinates
from engine.objectives import Objectives
from engine.subject import Subject
from engine.sim import Simulation
from engine.verbs import VerbEngine
from engine.world import World
from tools.migrate_supporting_stories import migrate,SOURCES
from test_native_planning import FIXTURE_ROOT
import random
import yaml

ROOT=Path(__file__).resolve().parents[1]


def definitions():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        source=FIXTURE_ROOT/'projects/momotaro_shared_planning'
        shutil.copytree(source,root/'projects'/source.name)
        migrate('momotaro',root=root)
        folder=root/'projects/momotaro_companions_planning'
        return (yaml.safe_load((folder/'world.yaml').read_text(encoding='utf8')),
            [yaml.safe_load(p.read_text(encoding='utf8')) for p in sorted((folder/'subjects').glob('*.yaml'))])


def load(raw=None,cast=None):
    default,people=definitions()
    w=World(deepcopy(raw if raw is not None else default),ROOT/'projects/momotaro_shared_planning/world.yaml')
    w.resolve_truth(0)
    p={a['id']:Subject.from_definition(a) for a in deepcopy(cast if cast is not None else people)}
    w.bind_subjects(p)
    return w,p,VerbEngine(w,random.Random(0))


def ready(w,p):
    hero=p[w.protagonist];dog=p['犬']
    hero.zone=dog.zone='道中'
    w.relations.change(dog.id,hero.id,affinity=.5)
    w.planner.before_action(w,dog)
    return hero,dog


class SupportingPlanningTests(unittest.TestCase):
    def test_shared_condition_is_two_received_positions_not_an_assignable_flag(self):
        w,p,e=load();hero,dog=ready(w,p)
        self.assertEqual(coordinates(w.concerns.conditions['escort_1']['test']),[('zone','犬'),('zone','桃太郎')])
        self.assertEqual(w.concerns.evaluate(w,'escort_1',actor=dog.id)['state'],'true')
        hero.zone='森'
        self.assertEqual(w.concerns.evaluate(w,'escort_1',actor=dog.id)['state'],'unknown')
        projection={'effects':[{'kind':'zone','actor':dog.id,'value':'森'},{'kind':'zone','actor':hero.id,'value':'森'}]}
        self.assertEqual(w.concerns.evaluate(w,'escort_1',actor=dog.id,projection=projection)['state'],'true')
        raw,_=definitions();next(c for c in raw['concerns']['conditions'] if c['id']=='escort_1')['test']['target']='犬'
        with self.assertRaises(ValueError):load(raw=raw)

    def test_meeting_does_not_finish_the_companions_purpose(self):
        w,p,e=load();hero,dog=ready(w,p)
        w.goal_rules.record(w,1,close=True)
        self.assertEqual(w.goal_rules.actor_view(w,dog.id,'escort_1',2)['status'],'pending')
        self.assertEqual(w.planner.search(w,dog,'escort_1',2)['status'],'condition_met')
        result,_,_=e.execute(hero,Action('move',('森',)),turn=2,day=1)
        self.assertEqual(result,'moved')
        plan=w.planner.search(w,dog,'escort_1',2)
        self.assertEqual((plan['steps'][0]['verb'],plan['steps'][0]['args']),('move',['森']))
        action=Action('move',('森',),{'goal_plan':plan})
        self.assertEqual(e.execute(dog,action,turn=2,day=1)[0],'moved')
        self.assertEqual(dog.zone,hero.zone)
        # A second departure must produce a new route, not a latched success.
        e.execute(hero,Action('move',('道中',)),turn=3,day=1)
        self.assertEqual(w.planner.search(w,dog,'escort_1',3)['steps'][0]['args'],['道中'])

    def test_actual_gift_changes_acceptance_and_lost_affinity_ends_following(self):
        w,p,e=load();hero=p[w.protagonist];dog=p['犬'];hero.zone=dog.zone='道中'
        hero.add_item('きびだんご',1)
        self.assertEqual(w.planner.search(w,dog,'escort_1',1)['status'],'condition_met')
        self.assertEqual(e.execute(hero,Action('give_item',(dog.id,'きびだんご')),turn=1,day=1)[0],'given')
        self.assertGreaterEqual(w.relations.stance(dog.id,hero.id),w.companionship['threshold'])
        e.execute(hero,Action('move',('森',)),turn=2,day=1)
        self.assertEqual(w.planner.search(w,dog,'escort_1',2)['steps'][0]['args'],['森'])
        w.relations.change(dog.id,hero.id,affinity=-1)
        self.assertEqual(w.planner.search(w,dog,'escort_1',3)['status'],'condition_met')

    def test_rescue_is_planned_and_executed_using_existing_permissions(self):
        w,p,e=load();hero,dog=ready(w,p);hero.vitality='downed'
        plan=w.planner.search(w,dog,'escort_1',1)
        self.assertEqual(plan['steps'][0]['verb'],'rescue')
        self.assertEqual(e.execute(dog,Action('rescue',(hero.id,),{'goal_plan':plan}),turn=1,day=1)[0],'rescued')
        self.assertEqual(hero.vitality,'revived')
        self.assertEqual(w.planner.search(w,dog,'escort_1',2)['status'],'condition_met')

    def test_dead_leader_is_terminal_and_no_resurrection_is_forecast(self):
        w,p,e=load();hero,dog=ready(w,p);hero.vitality='dead';w.goal_rules.record(w,1)
        self.assertEqual(w.planner.search(w,dog,'escort_1',1)['status'],'inactive')
        self.assertEqual(w.goal_rules.actor_view(w,dog.id,'escort_1',1)['reason'],'failure_condition')

    def test_unwitnessed_position_health_and_inventory_do_not_leak(self):
        w,p,e=load();hero,dog=ready(w,p);hero.zone='森';w.planner.before_action(w,dog)
        before=w.planner.search(w,dog,'escort_1',1)
        hero.zone='村';hero.vitality='downed';hero.add_item('船',1)
        self.assertEqual(w.planner.search(w,dog,'escort_1',1),before)
        self.assertNotIn(('inventory',hero.id,'船'),w.native_planning.visible(dog))

    def test_stale_wait_is_revalidated_when_help_becomes_necessary(self):
        w,p,e=load();hero,dog=ready(w,p)
        plan=w.planner.search(w,dog,'escort_1',1)
        wait=Action('rest',meta={'goal_plan':plan})
        self.assertEqual(e.execute(dog,wait,turn=1,day=1)[0],'rested')
        hero.vitality='downed'
        self.assertEqual(e.execute(dog,wait,turn=2,day=1)[0],'invalid')
        self.assertEqual(hero.vitality,'downed')

    def test_vehicle_access_uses_visible_owner_and_never_teleports_a_companion(self):
        w,p,e=load();hero,dog=ready(w,p);hero.zone=dog.zone='海';hero.add_item('船',1)
        view=w.planner.view(w,dog)
        edge=next(r for r in w.routes['海'] if r.destination=='鬼ヶ島')
        self.assertEqual(w.native_planning._route_access(dog,edge,view),(True,hero.id))
        hero.zone='鬼ヶ島';w.planner.before_action(w,dog)
        view=w.planner.view(w,dog)
        self.assertEqual(w.native_planning._route_access(dog,edge,view),(False,None))
        self.assertEqual(e.execute(dog,Action('move',('鬼ヶ島',)),turn=1,day=1)[0],'invalid')
        self.assertEqual(dog.zone,'海')

    def test_period_end_policy_allows_recovery_but_not_explicit_failure(self):
        w,p,e=load();hero,dog=ready(w,p)
        w.goal_rules.definitions['escort_1']['period']['end']=3
        w.goal_rules.record(w,1,close=True)
        hero.zone='森';w.goal_rules.record(w,2,close=True)
        hero.zone=dog.zone;w.goal_rules.record(w,3,close=True)
        report={r['condition']:r for r in w.goal_rules.report(w)}
        self.assertEqual(report['escort_1']['status'],'achieved')
        w,p,e=load();hero,dog=ready(w,p);hero.vitality='dead';w.goal_rules.record(w,1)
        hero.vitality='alive'
        self.assertEqual(w.goal_rules.actor_view(w,dog.id,'escort_1',2)['status'],'failed')

    def test_unknown_completion_policy_is_rejected(self):
        raw,_=definitions()
        for value in ('forever',[],None,1):
            bad=deepcopy(raw['goal_rules']);bad['definitions'][-1]['completion']=value
            with self.assertRaises(ValueError):Objectives(bad)

    def test_higher_priority_personal_goal_can_override_accompaniment(self):
        raw,cast=definitions()
        raw['concerns']['conditions'].append(dict(id='personal_home',label='帰宅する',mode='achieve',visibility='observed',
            test=dict(kind='zone',actor='犬',equals='村')))
        raw['goal_rules']['definitions'].append(dict(condition='personal_home',success={'condition':'personal_home'},
            failure={'constant':False},period={'start':1,'end':64}))
        next(p for p in cast if p['id']=='犬')['commitments'].append(dict(condition='personal_home',priority=9,required=False))
        w,p,e=load(raw,cast);hero,dog=ready(w,p);hero.zone='森'
        # Authored received information; no live remote read.
        w.planner.memory[dog.id]['known'][('zone',hero.id)]='森'
        options=candidates(dog,w,type('Clock',(),{'turn':1,'day':1})())
        self.assertTrue(options)
        self.assertTrue(all(a.meta['goal_plan']['root']=='personal_home' for a,_ in options))
        self.assertEqual(options[0][0].args,('村',))

    def test_non_negotiable_home_condition_blocks_following(self):
        raw,cast=definitions()
        raw['concerns']['conditions'].append(dict(id='must_stay',label='ここを離れない',mode='maintain',visibility='observed',
            test=dict(kind='zone',actor='犬',equals='道中')))
        next(p for p in cast if p['id']=='犬')['commitments'].append(dict(condition='must_stay',priority=10,required=True))
        w,p,e=load(raw,cast);hero,dog=ready(w,p)
        e.execute(hero,Action('move',('森',)),turn=1,day=1)
        options=candidates(dog,w,type('Clock',(),{'turn':1,'day':1})())
        self.assertFalse(any(a.verb=='move' and a.args==('森',) for a,_ in options))

    def test_no_rescue_verb_is_not_fabricated_by_the_planner(self):
        raw,cast=definitions();next(p for p in cast if p['id']=='犬')['verbs'].remove('rescue')
        w,p,e=load(raw,cast);hero,dog=ready(w,p);hero.vitality='downed'
        plan=w.planner.search(w,dog,'escort_1',1)
        self.assertFalse(any(s['verb']=='rescue' for s in plan['steps']))

    def test_same_place_and_period_policy_round_trip_through_real_settings(self):
        from execution.library import LibraryStore
        from execution import world_editor
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=FIXTURE_ROOT/'projects/momotaro_shared_planning'
            shutil.copytree(src,root/'projects'/src.name);migrate('momotaro',root=root)
            store=LibraryStore(root);name='momotaro_companions_planning'
            current=world_editor.snapshot(store,name)
            for operation in ('concerns','goal_rules'):
                key=operation
                definition=deepcopy(current['world'][key])
                if operation=='goal_rules':operation='goal-rules'
                result=world_editor.save(store,name,dict(revision=current['revision'],operation=operation,
                    target=None,values={'definition':definition}))
                self.assertEqual(result['world'][key],definition)
                current=result

    def test_ship_and_medical_roles_use_the_same_engine(self):
        raw,cast=definitions();text=json.dumps([raw,cast],ensure_ascii=False)
        for old,new in [('桃太郎','船長'),('犬','医療担当'),('猿','航法担当'),('キジ','整備担当'),('村','母港'),('道中','船内通路'),('森','機関室')]:
            text=text.replace(old,new)
        raw,cast=json.loads(text);raw.pop('gapengine',None)
        w,p,e=load(raw,cast);captain=p['船長'];medic=p['医療担当']
        captain.zone=medic.zone='船内通路';w.relations.change(medic.id,captain.id,affinity=.5)
        w.planner.before_action(w,medic)
        self.assertEqual(e.execute(captain,Action('move',('機関室',)),turn=1,day=1)[0],'moved')
        plan=w.planner.search(w,medic,'escort_1',1)
        self.assertEqual(plan['steps'][0]['args'],['機関室'])
        self.assertEqual(e.execute(medic,Action('move',('機関室',),{'goal_plan':plan}),turn=1,day=1)[0],'moved')
        captain.vitality='downed';plan=w.planner.search(w,medic,'escort_1',2)
        self.assertEqual(plan['steps'][0]['verb'],'rescue')
        self.assertEqual(e.execute(medic,Action('rescue',(captain.id,),{'goal_plan':plan}),turn=2,day=1)[0],'rescued')

    def test_normal_simulation_preserves_accepted_relations_and_executes_rescue(self):
        raw,cast=definitions()
        raw['time']['days']=1
        for spec in raw['goal_rules']['definitions']:spec['period']['end']=4
        w,p,e=load(raw,cast);hero=p[w.protagonist];dog=p['犬']
        dog.zone=hero.zone;dog.initial_relations[hero.id]['affinity']=.65
        hero.vitality='downed';hero.downed_since=0
        # Leave enough time for an explicit rescue, rather than passive revival.
        w.vitality['revive_after']=20
        with tempfile.TemporaryDirectory() as temp:
            sim=Simulation(0,w,p,Path(temp),record_explanations=True)
            self.assertGreaterEqual(w.relations.stance(dog.id,hero.id),.6)
            rows=[json.loads(s) for s in sim.run().read_text(encoding='utf8').splitlines()]
        rescues=[r for r in rows if r.get('kind')=='decision' and r['subject']==dog.id and r['result']=='rescued']
        self.assertTrue(rescues)
        self.assertEqual(rescues[0]['details']['planning']['root'],'escort_1')
        self.assertFalse(any(r.get('kind')=='decision' and r['result']=='invalid' for r in rows))

    def test_new_editions_are_reproducible_and_never_replace_edited_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for source in SOURCES:
                folder=FIXTURE_ROOT/'projects'/(source+'_shared_planning')
                shutil.copytree(folder,root/'projects'/folder.name)
                before={p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()}
                manifest=migrate(source,root=root)
                self.assertIn('犬',manifest['planning_actors']);self.assertNotIn('鬼',manifest['planning_actors'])
                self.assertEqual(manifest,migrate(source,root=root))
                self.assertEqual(before,{p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()})
                generated=root/'projects'/(source+'_companions_planning')
                w=World.from_yaml(generated/'world.yaml')
                people={a.id:a for a in (Subject.from_yaml(p) for p in sorted((generated/'subjects').glob('*.yaml')))}
                w.bind_subjects(people)
            path=root/'projects/momotaro_companions_planning/subjects/04_inu.yaml'
            path.write_text(path.read_text(encoding='utf8')+'# authored edit\n',encoding='utf8')
            with self.assertRaises(FileExistsError):migrate('momotaro',root=root)


if __name__=='__main__':unittest.main()
