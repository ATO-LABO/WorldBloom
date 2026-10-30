from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import tempfile
import shutil
import unittest
from unittest.mock import patch
import yaml

from engine.actions import Action, candidates
from engine.native_planning import NativePlanning
from engine.native_planning import aux
from engine.sim import Simulation
from engine.subject import Subject
from engine.verbs import VerbEngine
from engine.world import World
from tools.migrate_legacy_stories import FAMILIES
from tools.migrate_legacy_stories import migrate

ROOT=Path(__file__).resolve().parents[1]
FIXTURE_TEMP=tempfile.TemporaryDirectory(prefix='native-planning-')
FIXTURE_ROOT=Path(FIXTURE_TEMP.name)
for source in FAMILIES:
    shutil.copytree(ROOT/'projects'/source,FIXTURE_ROOT/'projects'/source)
with patch('tools.migrate_legacy_stories.ROOT',FIXTURE_ROOT):
    for source in FAMILIES:migrate(source)

def project_folder(name):
    generated=FIXTURE_ROOT/'projects'/name
    return generated if generated.exists() else ROOT/'projects'/name

def inputs(name='momotaro_shared_planning'):
    folder=project_folder(name)
    return yaml.safe_load((folder/'world.yaml').read_text(encoding='utf8')), [yaml.safe_load(p.read_text(encoding='utf8')) for p in sorted((folder/'subjects').glob('*.yaml'))]

def load(name='momotaro_shared_planning',raw=None,cast=None):
    original,people=inputs(name)
    world=World(deepcopy(raw or original),project_folder(name)/'world.yaml')
    world.resolve_truth(0)
    persons={p['id']:Subject.from_definition(p) for p in deepcopy(cast or people)}
    world.bind_subjects(persons);world.goal_rules.record(world,1)
    return world,persons,VerbEngine(world,random.Random(0))

def episode(name='momotaro_shared_planning'):
    w,p,_=load(name)
    with tempfile.TemporaryDirectory() as temp:
        log=Simulation(0,w,p,Path(temp),record_explanations=True).run()
        rows=[json.loads(s) for s in log.read_text(encoding='utf8').splitlines()]
    return w,p,rows

class NativePlanningTests(unittest.TestCase):
    def test_sources_and_cast_are_preserved(self):
        for source in FAMILIES:
            folder=project_folder(source+'_shared_planning')
            manifest=json.loads((folder/'migration.json').read_text(encoding='utf8'))
            expected_paths = {'world.yaml'} | {'subjects/' + p.name for p in (ROOT/'projects'/source/'subjects').glob('*.yaml')}
            self.assertEqual(set(manifest['source_sha256']), expected_paths)
            for generated in [folder/'world.yaml', folder/'migration.json', *sorted((folder/'subjects').glob('*.yaml'))]:
                self.assertNotIn(b'\r\n', generated.read_bytes())
            for rel,digest in manifest['source_sha256'].items():
                self.assertEqual(hashlib.sha256((ROOT/'projects'/source/rel).read_bytes()).hexdigest(),digest)
            _,old=inputs(source);_,new=inputs(source+'_shared_planning')
            self.assertEqual(len(old),len(new))
            for a,b in zip(old,new):
                for key in ('id','traits','base','modifiers','inventory','knowledge','verbs','range','goal','relations'):
                    self.assertEqual(a.get(key),b.get(key),(source,a['id'],key))

    def test_final_goal_derives_native_recipe_and_return_without_new_values(self):
        w,p,_=load();hero=p[w.protagonist];before=deepcopy(hero.commitments)
        plan=w.planner.search(w,hero,'journey_return',1)
        self.assertEqual(plan['status'],'conditional')
        verbs=[s['verb'] for s in plan['steps']]
        self.assertIn('craft',verbs);self.assertIn('fight',verbs)
        self.assertEqual(plan['steps'][-1]['args'],['村'])
        self.assertEqual(hero.commitments,before)
        self.assertEqual(len(before),1)
        self.assertTrue(plan['assumptions'])

    def test_search_never_writes_forecast_victory_or_consumes_resources(self):
        w,p,_=load();before={k:v.to_definition() for k,v in p.items()} if hasattr(p[w.protagonist],'to_definition') else deepcopy(p[w.protagonist].inventory)
        for _ in range(2):w.planner.search(w,p[w.protagonist],'journey_return',1)
        self.assertEqual(w.responses.state['native_victory'],'pending')
        self.assertEqual(w.native_planning.history,[])
        self.assertEqual(p[w.protagonist].inventory,before)
        self.assertEqual(p['鬼'].vitality,'alive')

    def test_remote_strength_equipment_and_position_do_not_leak(self):
        w,p,_=load();hero=p[w.protagonist]
        before=w.planner.search(w,hero,'journey_return',1)
        p['鬼'].base=1;p['鬼'].inventory={};p['鬼'].zone='森'
        self.assertEqual(w.planner.search(w,hero,'journey_return',1),before)

    def test_actual_losses_do_not_complete_victory(self):
        w,p,e=load();hero=p[w.protagonist];hero.zone='鬼ヶ島'
        result,_,_=e.execute(hero,Action('fight',('鬼',)),turn=1,day=1)
        self.assertEqual(result,'lost')
        self.assertEqual(w.responses.state['native_victory'],'pending')
        self.assertEqual(w.native_planning.history,[])

    def test_actual_victory_receipt_reaches_only_present_people(self):
        w,p,e=load();hero=p[w.protagonist];hero.zone='鬼ヶ島';hero.base=1000
        result,details,_=e.execute(hero,Action('fight',('鬼',)),turn=1,day=1)
        self.assertEqual(result,'won')
        self.assertEqual(w.responses.state['native_victory'],'done')
        self.assertEqual(w.native_planning.read(('state','native_victory'),hero.id),'done')
        self.assertEqual(w.native_planning.read(('state','native_victory'),'おじいさん'),'pending')
        self.assertIn('native_outcomes',details)

    def test_stolen_treasure_without_actual_victory_is_not_the_new_ending(self):
        w,p,_=load();p[w.protagonist].add_item('鬼ヶ島の宝物',1)
        ns=w.namespace(p[w.protagonist],[],turn=1,day=1)
        self.assertFalse(ns['objective_achieved']('journey_return'))

    def test_full_journey_executes_craft_loss_replanning_and_actual_return(self):
        w,p,rows=episode();ds=[r for r in rows if r.get('kind')=='decision' and r['subject']==w.protagonist]
        self.assertTrue(any(r['result']=='lost' for r in ds))
        self.assertTrue(any(r.get('details',{}).get('planning',{}).get('reason')=='previous_action_failed' for r in ds))
        self.assertTrue(any(r['verb']=='craft' and r['result']=='crafted' for r in ds))
        self.assertEqual(p[w.protagonist].zone,'村')
        self.assertTrue(p[w.protagonist].has_item('鬼ヶ島の宝物'))
        self.assertEqual(w.responses.state['native_victory'],'done')
        self.assertFalse(any('forced' in r for r in ds))
        self.assertFalse(any(r['result']=='invalid' for r in ds))

    def test_departure_is_not_a_forced_action(self):
        raw,_=inputs()
        self.assertFalse(any('force_action' in e for e in raw['scheduled_events']))
        original,_=inputs('momotaro')
        self.assertTrue(any('force_action' in e for e in original['scheduled_events']))

    def test_relationship_failure_drives_observation_and_known_cap_removal(self):
        w,p,rows=episode('romance_shared_planning');ds=[r for r in rows if r.get('kind')=='decision' and r['subject']=='A']
        self.assertTrue(any(r['details'].get('native_observations') for r in ds))
        self.assertTrue(any(r['verb']=='observe' for r in ds))
        self.assertTrue(any(r['verb']=='neutralize' for r in ds))
        self.assertFalse(any(r['result']=='invalid' for r in ds))
        self.assertTrue(w.namespace(p['A'],[],turn=w.goal_rules.current_turn,day=1)['objective_achieved']('relationship_mutual'))

    def test_absent_person_invalidates_remembered_presence(self):
        w,p,_=load('romance_shared_planning');hero=p['A']
        w.planner.before_action(w,hero);p['B'].zone='カフェ'
        view=w.planner.view(w,hero)
        self.assertIsNone(view[('zone','B')])
        plan=w.planner.search(w,hero,'relationship_mutual',1)
        self.assertEqual(plan['status'],'seeking_information')
        self.assertEqual(plan['steps'][0]['verb'],'move')

    def test_detective_plan_does_not_read_culprit_truth(self):
        w,p,_=load('detective_shared_planning')
        class NoTruth(dict):
            def __getitem__(self,key):raise AssertionError('truth leaked into plan')
            def get(self,*args):raise AssertionError('truth leaked into plan')
        w.truth=NoTruth()
        plan=w.planner.search(w,p[w.protagonist],'inquiry_solved',1)
        self.assertEqual(plan['status'],'seeking_information')
        self.assertNotIn('confront',[s['verb'] for s in plan['steps']])

    def test_detective_recovers_from_wrong_accusations_using_real_evidence(self):
        w,p,rows=episode('detective_shared_planning');ds=[r for r in rows if r.get('kind')=='decision' and r['subject']==w.protagonist]
        self.assertTrue(any(r['result']=='misjudged' for r in ds))
        self.assertTrue(any(r['verb']=='rethink' for r in ds))
        self.assertTrue(any(r['result']=='exposed' for r in ds))
        self.assertFalse(any(r['result']=='invalid' for r in ds))
        self.assertEqual(w.responses.state['native_exposed'],'done')

    def test_renamed_people_places_and_items_use_same_operators(self):
        raw,cast=inputs();text=json.dumps([raw,cast],ensure_ascii=False)
        effects=(ROOT/raw['gapengine']['effects']).read_text(encoding='utf8')
        for a,b in [('桃太郎','旅人'),('鬼ヶ島の宝物','観測記録'),('鬼ヶ島','離島'),('鬼','番人'),('村','拠点'),('船','渡航艇')]:
            text=text.replace(a,b);effects=effects.replace(a,b)
        renamed,cast=json.loads(text)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'effects.yaml';path.write_text(effects,encoding='utf8')
            renamed['gapengine']['effects']=str(path)
            w,p,_=load(raw=renamed,cast=cast)
            plan=w.planner.search(w,p['旅人'],'journey_return',1)
        self.assertIn(['渡航艇'],[s['args'] for s in plan['steps']])
        self.assertEqual(plan['steps'][-1]['args'],['拠点'])

    def test_bad_native_outcome_config_is_rejected(self):
        raw,_=inputs()
        for mutate in (lambda r:r['outcomes'][0].update(value=1),
                       lambda r:r['outcomes'].append(deepcopy(r['outcomes'][0])),
                       lambda r:r.update(inquiry_facts=['x','x'])):
            config=deepcopy(raw['native_planning']);mutate(config)
            with self.assertRaises(ValueError):NativePlanning(config)

    def test_native_step_is_revalidated_before_crafting(self):
        w,p,e=load();hero=p[w.protagonist]
        plan=w.planner.search(w,hero,'journey_return',1)
        forged=Action('craft',('船',),{'goal_plan':plan})
        before=deepcopy(hero.inventory)
        result,details,_=e.execute(hero,forged,turn=1,day=1)
        self.assertEqual(result,'invalid');self.assertEqual(details['reason'],'plan_no_longer_justified')
        self.assertEqual(hero.inventory,before)

    def test_native_and_existing_response_operators_compose(self):
        raw,cast=inputs('ship_planning')
        raw['native_planning']={'version':1,'outcomes':[],'inquiry_facts':[]}
        w,p,_=load('ship_planning',raw,cast)
        plan=w.planner.search(w,p['通信士'],'beacon_sent',1)
        self.assertEqual([s['verb'] for s in plan['steps']],['move','respond','move','respond'])

    def test_crossed_phase_survives_projected_return_travel(self):
        w,p,_=load();hero=p[w.protagonist];state=w.planner.view(w,hero)
        state[('zone',hero.id)]='海';state[aux('phase','越境')]=True
        options=list(w.native_planning.steps(hero,'journey_return',state,1,0,0))
        self.assertNotIn('train',[s['verb'] for s in options])

    def test_fight_loss_cannot_discard_a_required_keepsake(self):
        raw,cast=inputs();raw['concerns']['conditions'].append(dict(id='keep_charm',label='勾玉を守る',mode='maintain',visibility='observed',
            test=dict(kind='inventory',actor='桃太郎',item='勾玉',minimum=1)))
        hero=next(p for p in cast if p['id']=='桃太郎')
        hero['commitments'].append(dict(condition='keep_charm',priority=9,required=True))
        w,p,_=load(raw=raw,cast=cast);p['桃太郎'].zone='鬼ヶ島'
        options=list(w.native_planning.steps(p['桃太郎'],'journey_return',w.planner.view(w,p['桃太郎']),1,0,0))
        self.assertNotIn('fight',[s['verb'] for s in options])

    def test_regeneration_refuses_to_overwrite_an_edited_edition(self):
        import shutil
        from unittest.mock import patch
        from tools.migrate_legacy_stories import migrate
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);shutil.copytree(ROOT/'projects'/'momotaro',root/'projects'/'momotaro')
            with patch('tools.migrate_legacy_stories.ROOT',root):
                migrate('momotaro');migrate('momotaro')
                path=root/'projects'/'momotaro_shared_planning'/'world.yaml'
                path.write_text(path.read_text(encoding='utf8')+'\n# saved user edit\n',encoding='utf8')
                before=path.read_bytes()
                with self.assertRaises(FileExistsError):migrate('momotaro')
                self.assertEqual(path.read_bytes(),before)

    def test_recipe_knowledge_is_a_searchable_prerequisite(self):
        raw,cast=inputs();hero=next(p for p in cast if p['id']=='桃太郎');hero['knowledge']=[]
        manual=next(f for f in raw['facts'] if f['id']=='造船術')
        manual['sources']=[dict(type='investigate',zone='村',count=1)]
        w,p,e=load(raw=raw,cast=cast)
        plan=w.planner.search(w,p['桃太郎'],'journey_return',1)
        self.assertEqual(plan['steps'][0]['verb'],'investigate')
        self.assertNotIn('造船術',p['桃太郎'].knowledge)
        first=plan['steps'][0]
        result,_,_=e.execute(p['桃太郎'],Action(first['verb'],tuple(first['args']),{'goal_plan':plan}),turn=1,day=1)
        self.assertEqual(result,'investigated');self.assertIn('造船術',p['桃太郎'].knowledge)


    def test_remote_unseen_vehicle_owner_does_not_authorize_projected_travel(self):
        w,p,_=load();hero=p['桃太郎'];owner=p['鬼'];owner.zone='海';owner.inventory['船']=1;owner.companions=[hero.id]
        state=w.planner.view(w,hero);state[('zone',hero.id)]='海'
        steps=list(w.native_planning.steps(hero,'journey_return',state,1,0,0))
        self.assertFalse(any(s['verb']=='move' and s['args']==['鬼ヶ島'] for s in steps))

    def test_received_vehicle_access_does_not_consult_remote_ownership(self):
        w,p,_=load();hero=p['桃太郎'];owner=p['鬼'];owner.zone='森';owner.inventory={};owner.companions=[hero.id]
        state=w.planner.view(w,hero);state[('zone',hero.id)]='海';state[('zone',owner.id)]='海';state[('inventory',owner.id,'船')]=1
        steps=list(w.native_planning.steps(hero,'journey_return',state,1,0,0))
        move=next(s for s in steps if s['verb']=='move' and s['args']==['鬼ヶ島'])
        self.assertIn({'requires_presence':owner.id,'zone':'海','item':'船'},move['assumptions'])

    def test_non_item_modifier_does_not_create_a_lootable_item(self):
        from engine.subject import Modifier
        w,p,_=load();hero=p['桃太郎'];foe=p['鬼'];hero.zone='鬼ヶ島';foe.inventory={}
        foe.modifiers=[Modifier(id='not-equipment',source='金棒',value=40,kind='trauma',visible=False)]
        hero.beliefs_about[foe.id].known_modifiers.add('金棒')
        steps=list(w.native_planning.steps(hero,'journey_return',w.planner.view(w,hero),1,0,0))
        step=next(s for s in steps if s['verb']=='neutralize')
        self.assertFalse(any(e.get('kind')=='inventory' and e.get('item')=='金棒' for e in step['effects']))

    def test_known_held_item_neutralization_forecast_matches_execution(self):
        w,p,e=load();hero=p['桃太郎'];hero.zone='鬼ヶ島';hero.beliefs_about['鬼'].known_modifiers.add('金棒')
        state=w.planner.view(w,hero);steps=list(w.native_planning.steps(hero,'journey_return',state,1,0,0))
        step=next(s for s in steps if s['verb']=='neutralize' and s['args']==['鬼','金棒'])
        self.assertTrue(any(x.get('kind')=='inventory' and x.get('actor')==hero.id and x.get('item')=='金棒' and x['value']==1 for x in step['effects']))
        result,_,_=e.execute(hero,Action('neutralize',('鬼','金棒')),turn=1,day=1)
        self.assertEqual(result,'neutralized');self.assertEqual(hero.inventory['金棒'],1)

    def test_hidden_remote_phase_predicate_cannot_read_actual_location(self):
        raw,cast=inputs();raw['thresholds'].append({'id':'remote_phase','when':"zone(鬼) == '森'"})
        w,p,_=load(raw=raw,cast=cast);hero=p['桃太郎'];state=w.planner.view(w,hero)
        before=w.native_planning._shadow(hero,state,0,1).phase
        p['鬼'].zone='森'
        self.assertEqual(w.native_planning._shadow(hero,state,0,1).phase,before)
        self.assertNotIn('remote_phase',before)

    def test_projected_inventory_can_unlock_an_authored_phase(self):
        raw,cast=inputs();raw['thresholds'].append({'id':'equipped','when':'holds(桃太郎, 船)'})
        w,p,_=load(raw=raw,cast=cast);hero=p['桃太郎'];state=w.planner.view(w,hero)
        state[('inventory',hero.id,'船')]=1
        self.assertIn('equipped',w.native_planning._shadow(hero,state,0,1).phase)
        self.assertFalse(hero.has_item('船'))

if __name__=='__main__':unittest.main()
