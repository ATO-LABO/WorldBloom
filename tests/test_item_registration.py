from copy import deepcopy
import json
import unittest
import yaml
from engine.world import World
from engine.subject import Subject
from engine.verbs import VerbEngine
from engine.actions import Action
from execution import items_editor
from execution.world_drafts import compile_draft, mock_response
from execution.provenance import ConfigError
from test_items_workspace import ItemsWorkspaceTests


EFFECTS = dict(strength=3, receiver_affinity=.2, giver_affinity=.05, shared_affinity=0, objective=False)


class ItemRegistrationTests(ItemsWorkspaceTests):
    def test_letter_and_treasure_have_generic_names_and_separate_owners(self):
        import random
        for item, label in [('弟の手紙', '手紙'), ('鬼ヶ島の宝物', '宝物')]:
            status, result = self.save('item-topic', item, 'identity', label)
            self.assertEqual(status, 200, result)
        status, result = self.save('item-topic', '弟の手紙', 'holders', [
            {'person': '鬼の弟', 'count': 1},
        ])
        self.assertEqual(status, 200, result)
        trial = next(row for row in self.current()['world']['trials']
                     if row['id'] == 'brother_letter_trial')
        status, result = self.save('item-topic', '弟の手紙', 'sources', {
            'sources': [], 'rewards': [{
                'id': trial['id'], 'giver': trial['giver'],
                'requires': trial.get('requires', {}), 'mode': 'transfer',
            }],
        })
        self.assertEqual(status, 200, result)
        snapshot=self.current();world=World(snapshot['world'],self.repo/'projects/momotaro_plus3/world.yaml')
        people={p['id']:Subject.from_definition(p) for p in snapshot['people']};world.bind_subjects(people)
        self.assertEqual(world.items['弟の手紙']['display_name'],'手紙')
        self.assertEqual(world.items['鬼ヶ島の宝物']['display_name'],'宝物')
        actor,giver=people['桃太郎'],people['鬼の弟'];actor.zone=giver.zone
        self.assertTrue(giver.has_item('弟の手紙'));self.assertTrue(people['鬼'].has_item('鬼ヶ島の宝物'))
        world.relations.change(giver.id,actor.id,affinity=.4)
        engine=VerbEngine(world,random.Random(1));action=Action('trial',(giver.id,),meta={'trial_id':'brother_letter_trial'})
        self.assertEqual(engine.execute(actor,action,turn=1,day=1)[0],'trial_completed')
        self.assertFalse(giver.has_item('弟の手紙'));self.assertTrue(actor.has_item('弟の手紙'))

    def register(self, name='手紙', acquisition=None):
        value=dict(name=name, content='持っている間は強さが3増える', holders=[dict(person='鬼の弟',count=1)], effects=EFFECTS,
                   acquisition=acquisition or dict(sources=[],shop_zone=None,price={},craft=None,rewards=[dict(id='letter-reward',giver='鬼の弟',requires={},mode='transfer')]))
        return self.http('POST','/api/worlds/momotaro_plus3/edit',dict(revision=self.current()['revision'],operation='add-item',target=None,values=value))

    def test_register_routes_effects_and_holders_are_atomic_and_generic(self):
        status,result=self.register('封書')
        self.assertEqual(status,200,result)
        after=self.current();item=next(i for i in after['world']['items'] if i['name']=='封書')
        self.assertEqual(item['modifier']['value'],3)
        self.assertEqual(next(p for p in after['people'] if p['id']=='鬼の弟')['inventory']['封書'],1)
        before=after['revision'];self.assertEqual(self.register('封書')[0],400)
        self.assertEqual(self.current()['revision'],before)
        self.assertEqual(self.register('失敗',dict(sources=[],shop_zone=None,price={},craft=dict(materials={'不明':1},knowledge=None,zone=None),rewards=[]))[0],400)
        self.assertEqual(self.current()['revision'],before)

    def test_existing_craft_and_reward_round_trip_and_transfer_conserves_inventory(self):
        value=dict(sources=[dict(type='investigate',zone='村',count=1)],shop_zone='街',price={'小判':2},craft=dict(materials={'木材':2,'縄':1},knowledge='造船術',zone='海'),rewards=[dict(id='ship-transfer',giver='漁師',requires={},mode='transfer')])
        status,result=self.save('item-topic','船','sources',value)
        self.assertEqual(status,200,result)
        world=World(result['world'],self.repo/'projects/momotaro_plus3/world.yaml');people={p['id']:Subject.from_definition(p) for p in result['people']};world.bind_subjects(people)
        actor,giver=people['桃太郎'],people['漁師'];actor.zone=giver.zone;giver.add_item('船',1)
        import random
        engine=VerbEngine(world, random.Random(1))
        action=Action('trial',(giver.id,),meta={'trial_id':'ship-transfer'})
        # The action consumes the giver's actual stock, and cannot mint a second copy.
        outcome,_,_=engine._trial(actor,action,turn=1,day=1)
        self.assertEqual(outcome,'trial_completed');self.assertFalse(giver.has_item('船'));self.assertTrue(actor.has_item('船'))
        other=people['犬'];other.zone=giver.zone
        self.assertEqual(engine._trial(other,action,turn=2,day=1)[0],'invalid')
        item=next(i for i in result['world']['items'] if i['name']=='船')
        self.assertEqual(item['made_from'],value['craft']['materials']);self.assertEqual(item['price'],{'小判':2})
        actor.zone='海';actor.inventory={'木材':2,'縄':1};actor.knowledge.add('造船術')
        self.assertEqual(engine._craft(actor,Action('craft',('船',)),turn=3,day=1)[0],'crafted')
        self.assertEqual(actor.inventory,{'船':1})

    def test_draft_metadata_compiles_into_real_routes_and_initial_knowledge(self):
        draft=json.loads(mock_response('町','アオが灯りの種を町に届ける。'))
        person=draft['people'][0]['name'];place=draft['places'][0]['name'];draft['facts']=[dict(name='作り方',description='種の作り方',known=[person],acquisition=dict(sources=[],rewards=[]))]
        draft['items'][0].update(effects=EFFECTS,description='強さ補正',acquisition=dict(sources=[],shop_zone=None,price={},craft=None,rewards=[dict(id='r1',giver=person,requires={},mode='transfer')]))
        _,world,raw=compile_draft(draft);subjects=[yaml.safe_load(v) for v in raw.values()]
        self.assertEqual(world['items'][0]['modifier']['value'],3);self.assertTrue(world['trials'][0]['transfer_item'])
        self.assertIn('trial',subjects[0]['verbs']);self.assertEqual(subjects[0]['knowledge'],['作り方'])
        before=deepcopy(draft);draft['items'][0]['acquisition']['craft']=dict(materials={draft['items'][0]['name']:1},knowledge=None,zone=place)
        with self.assertRaises(ConfigError):compile_draft(draft)
        self.assertIsNone(before['items'][0]['acquisition']['craft'])


if __name__=='__main__':unittest.main()
