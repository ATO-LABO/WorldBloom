from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import yaml
from execution import world_editor, person_values
from execution.library import LibraryStore
from execution.provenance import ConfigError
from execution.world_drafts import compile_draft, mock_response
from execution.draft_revisions import target_for, scope_paths, scoped_candidate, patch_draft
import test_library as fixtures
import test_draft_revisions as revision_tests
from uuid import uuid4


class ItemsWorkspaceTests(unittest.TestCase):
    setUp = fixtures.LibraryHttpBoundaryTests.setUp
    http = fixtures.LibraryHttpBoundaryTests.http

    def current(self):
        return world_editor.snapshot(LibraryStore(self.repo), 'momotaro_plus3')

    def save(self, kind, name, topic, value, revision=None):
        return self.http('POST', '/api/worlds/momotaro_plus3/edit', dict(
            revision=revision or self.current()['revision'], operation=kind, target=name,
            values={'topic':topic, 'value':value}))

    def test_multiple_holders_update_only_the_selected_item_and_reject_stale_revision(self):
        before=self.current();holders=[{'person':n,'count':c} for n,c in [('桃太郎',2),('猿',1),('犬',5)]]
        status,saved=self.save('item-topic','きびだんご','holders',holders)
        self.assertEqual(status,200,saved)
        after=self.current();self.assertEqual(before['world'],after['world'])
        for person in after['people']:
            old=next(p for p in before['people'] if p['id']==person['id']);expected=deepcopy(old)
            expected['inventory'].pop('きびだんご',None)
            count=next((h['count'] for h in holders if h['person']==person['id']),None)
            if count:expected['inventory']['きびだんご']=count
            self.assertEqual(person,expected)
        self.assertEqual(self.save('item-topic','きびだんご','holders',[],before['revision'])[0],409)
        self.assertEqual(self.save('item-topic','きびだんご','holders',[])[0],200)
        self.assertTrue(all('きびだんご' not in p['inventory'] for p in self.current()['people']))

    def test_invalid_holders_never_write(self):
        before=self.current()
        for holders in [[{'person':'桃太郎','count':n}] for n in [0,-1,1.5,True,10001]]+[
            [{'person':'不明','count':1}], [{'person':'桃太郎','count':1}]*2]:
            self.assertEqual(self.save('item-topic','きびだんご','holders',holders)[0],400)
            self.assertEqual(self.current()['revision'],before['revision'])

    def test_transaction_rolls_back_all_people_when_write_is_interrupted(self):
        before=self.current();original=person_values._replace;calls=[]
        def interrupted(path,raw):
            calls.append(path)
            if len(calls)==2:raise OSError('interrupted')
            return original(path,raw)
        with patch.object(person_values,'_replace',side_effect=interrupted),self.assertRaises(OSError):
            world_editor.save(LibraryStore(self.repo),'momotaro_plus3',dict(revision=before['revision'],operation='item-topic',target='きびだんご',values={'topic':'holders','value':[{'person':'桃太郎','count':2},{'person':'猿','count':1},{'person':'犬','count':5}]}))
        self.assertEqual(self.current()['revision'],before['revision'])

    def test_information_content_knowledge_and_sources_preserve_other_settings(self):
        before=self.current()
        self.assertEqual(self.save('fact-topic','造船術','knowledge',['桃太郎','猿','犬'])[0],200)
        self.assertEqual(self.save('fact-topic','造船術','content','船を安全に組み立てる方法')[0],200)
        self.assertEqual(self.save('fact-topic','造船術','sources',{'sources':[{'type':'investigate','zone':'村','count':1}]})[0],200)
        after=self.current();fact=next(f for f in after['world']['facts'] if f['id']=='造船術')
        self.assertEqual(fact['label'],'船を安全に組み立てる方法')
        self.assertEqual([p['id'] for p in after['people'] if '造船術' in p.get('knowledge',[])],['桃太郎','犬','猿'])
        self.assertEqual(before['world']['items'],after['world']['items'])
        self.assertEqual(self.save('fact-topic','造船術','sources',{'sources':[{'type':'investigate','zone':'不明','count':1}]})[0],400)
        self.assertEqual(self.save('fact-topic','造船術','sources',{'sources':[{'type':'labor','zone':'村','count':1}]})[0],400)
        self.assertEqual(self.save('item-topic','縄','sources',{'sources':[{'type':'labor','agent':'桃太郎','count':1}], 'shop_zone':None, 'price':{}})[0],400)
        self.assertEqual(self.save('item-topic','縄','sources',{'sources':[], 'shop_zone':'村', 'price':{}})[0],400)

    def test_legacy_and_multiple_draft_holders_compile_to_real_inventories(self):
        draft=json.loads(mock_response('灯りの町','アオが灯りの種を町に届ける。'));_,_,legacy=compile_draft(draft)
        self.assertEqual(sum(p['inventory'].get('灯りの種',0) for p in map(yaml.safe_load,legacy.values())),1)
        people=[p['name'] for p in draft['people']];draft['items'][0]={'name':'灯りの種','holders':[{'person':people[0],'count':2},{'person':people[1],'count':5}]}
        normalized,world,subjects=compile_draft(draft)
        self.assertEqual([yaml.safe_load(v)['inventory']['灯りの種'] for v in subjects.values()],[2,5])
        target=target_for(normalized,{'section':'items','entity':'灯りの種','field':'holder'})
        self.assertEqual(scope_paths(normalized,target),['/items/0/holders'])
        candidate,_=patch_draft(normalized,json.dumps({'changes':[{'path':'/items/0/holders','value':[{'person':people[0],'count':3}]}]}))
        scoped_candidate(normalized,candidate,target)
        candidate['items'][0]['name']='別の物'
        with self.assertRaises(ConfigError):scoped_candidate(normalized,candidate,target)
        for bad in [[],[{'person':people[0],'count':2}]*2,[{'person':people[0],'count':0}]]:
            changed=deepcopy(draft);changed['items'][0]['holders']=bad
            if bad:
                with self.assertRaises(ConfigError):compile_draft(changed)
            else:self.assertTrue(all(not yaml.safe_load(v)['inventory'] for v in compile_draft(changed)[2].values()))

    def test_draft_information_edits_are_scoped_to_content_or_place(self):
        draft=json.loads(mock_response('灯りの町','アオが灯りの種を町に届ける。'))
        draft['facts']=[{'name':'祭りの予定','description':'夜に開く','place':draft['places'][0]['name']}]
        for field,key in [('factDescription','description'),('factPlace','place')]:
            target=target_for(draft,{'section':'facts','entity':'祭りの予定','field':field})
            self.assertEqual(scope_paths(draft,target),['/facts/0/'+key])


class ItemAIRevisionTests(unittest.TestCase):
    setUp = revision_tests.Revisions.setUp

    def test_quantities_survive_candidate_preview_apply_and_reload(self):
        draft=deepcopy(self.record['draft'])
        draft['items'][0]={'name':'灯りの種','holders':[{'person':'レン','count':1}]}
        def revise(*args):
            return json.dumps({'changes':[{'path':'/items/0/holders','value':[{'person':'レン','count':5}]}]})
        self.store.provider.revise=revise
        request=uuid4().hex
        preview=self.store.revise(self.token,self.record['revision'],draft,
            {'section':'items','entity':'灯りの種','field':'holder'},'数量を5にする',request)
        self.assertEqual(preview['state'],'ready')
        self.assertEqual(self.store.get(self.token)['draft'],self.record['draft'])
        saved=self.store.apply_revision(self.token,self.record['revision'],request,draft,preview['candidate'])
        self.assertEqual(saved['state'],'review')
        self.assertEqual(self.store.get(self.token)['draft']['items'][0]['holders'],[{'person':'レン','count':5}])
