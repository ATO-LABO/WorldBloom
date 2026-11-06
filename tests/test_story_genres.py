"""Frozen legacy genre behavior and rejection of retired Studio edits."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import http.client
from pathlib import Path

import test_library as fixtures
from test_engine import load_fixture, FixedRandom
from engine import story_genres as genres
from engine.actions import Action, candidates
from engine.verbs import VerbEngine
from engine.phase2 import available_verbs
from execution import world_editor
from execution.library import LibraryStore


class GenreEngineTests(unittest.TestCase):
    def pair(self, selected, affinity=1):
        world, subjects = load_fixture()
        world.story_genres = list(selected)
        a, b = subjects['桃太郎'], subjects['犬']
        a.zone = b.zone = '道中'
        world.relations.change(a.id,b.id,affinity=affinity)
        world.relations.change(b.id,a.id,affinity=affinity)
        return world,a,b

    def test_four_combinations_and_common_actions(self):
        for selected,label in [([], '共通'),(['combat'],'共通＋戦闘'),(['romance'],'共通＋恋愛'),(['romance','combat'],'共通＋戦闘＋恋愛')]:
            self.assertEqual(genres.label(selected),label)
            self.assertEqual(genres.allows(selected,'fight'),'combat' in selected)
            self.assertEqual(genres.allows(selected,'start_relationship'),'romance' in selected)
            self.assertTrue(genres.allows(selected,'rest'))
        for bad in ['combat',['unknown'],['combat','combat'],[None]]:
            with self.assertRaises(ValueError):genres.validate(bad)

    def test_registry_is_part_of_the_frozen_execution_closure(self):
        from execution.provenance import code_snapshot
        from engine.sim import _engine_source_hash
        import tempfile
        root=Path(__file__).resolve().parents[1]
        blobs,_=code_snapshot(root)
        self.assertEqual(blobs['engine/story_genres.json'],(root/'engine/story_genres.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/'example.py').write_text('example',encoding='utf-8')
            data=folder/'story_genres.json';data.write_text('[]',encoding='utf-8')
            _engine_source_hash.cache_clear();before=_engine_source_hash(folder)
            data.write_text('[{}]',encoding='utf-8');_engine_source_hash.cache_clear()
            self.assertNotEqual(before,_engine_source_hash(folder))

    def test_registry_extension_and_retirement_use_the_same_gate_and_choices(self):
        rows=deepcopy(genres.REGISTRY)+({'id':'future','name':'将来の機能','description':'test','actions':['future_action'],'grant_actions':['future_action']},)
        with patch.object(genres,'REGISTRY',rows):
            self.assertEqual(genres.validate(['future','combat']),['combat','future'])
            self.assertEqual(genres.label(['future']),'共通＋将来の機能')
            self.assertFalse(genres.allows([],'future_action'))
            world,a,_=self.pair(['future'])
            self.assertIn('future_action',available_verbs(world,a,turn=1,day=1))
            rows[-1]['enabled']=False
            self.assertNotIn('future', [g['id'] for g in genres.catalog()])
            self.assertFalse(genres.allows(['future'],'future_action'))
            self.assertEqual(genres.resolve({'story_genres':['future']},{}),[])

    def test_disabled_combat_cannot_be_forced_and_leaves_subject_unchanged(self):
        world,a,b=self.pair([])
        before=(a.base,b.base,set(a.phase),set(b.phase),b.vitality)
        result,details,_=VerbEngine(world,FixedRandom([0])).execute(a,Action('fight',(b.id,)),turn=1,day=1)
        self.assertEqual((result,details['reason']),('invalid','genre_disabled'))
        self.assertEqual(before,(a.base,b.base,set(a.phase),set(b.phase),b.vitality))
        choices=candidates(a,world,SimpleNamespace(turn=1,day=1))
        self.assertNotIn('fight',[v.verb for v,_ in choices])

    def test_combat_can_execute_when_enabled(self):
        world,a,b=self.pair(['combat'])
        a.base=200
        result,_,_=VerbEngine(world,FixedRandom([0])).execute(a,Action('fight',(b.id,)),turn=1,day=1)
        self.assertEqual(result,'won')

    def test_romance_requires_selection_mutual_affinity_and_an_executed_action(self):
        for selected,affinity,expected in [([],1,False),(['combat'],1,False),(['romance'],-1,False),(['romance'],1,True),(['combat','romance'],1,True)]:
            world,a,b=self.pair(selected,affinity)
            choices=candidates(a,world,SimpleNamespace(turn=1,day=1))
            self.assertEqual(any(v.verb=='start_relationship' and v.args==(b.id,) for v,_ in choices),expected)
            self.assertFalse(world.romantic_relationships)
            self.assertNotIn('交際:'+b.id,a.phase)
            result,_,_=VerbEngine(world,FixedRandom([0])).execute(a,Action('start_relationship',(b.id,)),turn=1,day=1)
            self.assertEqual(result=='relationship_started',expected)
            if expected:
                self.assertIn('交際:'+b.id,a.phase)
                self.assertIn('交際:'+a.id,b.phase)
                self.assertEqual(VerbEngine(world,FixedRandom([0])).execute(a,Action('start_relationship',(b.id,)),turn=2,day=1)[0],'invalid')


class GenreStorageTests(unittest.TestCase):
    setUp=fixtures.LibraryHttpBoundaryTests.setUp
    http=fixtures.LibraryHttpBoundaryTests.http

    def get(self,path):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=30)
        try:
            conn.request('GET',path)
            response=conn.getresponse()
            return response.status,response.read().decode('utf-8')
        finally:conn.close()

    def test_retired_genre_edit_cannot_change_current_world_or_subjects(self):
        store=LibraryStore(self.repo)
        path=self.repo/'projects/momotaro/world.yaml'
        before=path.read_bytes()
        subject_bytes={p:p.read_bytes() for p in (self.repo/'projects/momotaro/subjects').glob('*.yaml')}
        current=world_editor.snapshot(store,'momotaro')
        status,result=self.http('POST','/api/worlds/momotaro/edit',{
            'revision':current['revision'],'operation':'story-genres','target':None,
            'values':{'genres':['combat']},
        })
        self.assertEqual(status,400,result)
        self.assertEqual(result['code'],'bad_request')
        self.assertEqual(world_editor.snapshot(store,'momotaro'),current)
        self.assertEqual(path.read_bytes(),before)
        self.assertTrue(all(p.read_bytes()==v for p,v in subject_bytes.items()))

    def test_user_creation_is_closed_and_catalog_has_no_creation_entry(self):
        before=set((self.repo/'templates').iterdir())
        status,_=self.http('POST','/api/genres',{'mode':'new','template_id':'custom','name':'追加','description':''})
        self.assertEqual(status,403)
        self.assertEqual(set((self.repo/'templates').iterdir()),before)
        for url in ['/genres/new','/genres/combat','/genres/romance','/worlds?view=genres','/configs?tab=genres']:
            status,body=self.get(url)
            self.assertEqual(status,200,body[:200])
            self.assertNotIn('新しいジャンルを作る',body)
            self.assertNotIn('複製して編集',body)
