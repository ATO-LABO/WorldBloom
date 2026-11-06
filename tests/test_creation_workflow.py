from copy import deepcopy
from pathlib import Path
import json
import shutil
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import Mock

from execution.world_drafts import DraftStore, mock_response
from execution.draft_workspaces import WorkspaceStore, workflow
from execution.provenance import ConfigError

ROOT=Path(__file__).resolve().parents[1]


class CreationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='wb-three-stage-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.repo=self.base/'repo'
        shutil.copytree(ROOT/'templates/basic',self.repo/'templates/basic')
        self.provider=Mock(side_effect=AssertionError('No model calls in structural path'))
        self.provider.review=Mock(side_effect=AssertionError('No AI review in structural path'))
        self.store=DraftStore(self.repo,self.base/'control',self.provider,'live')
        self.draft=json.loads(mock_response('検証用の世界','人が設定した世界です。'))

    def test_manual_structural_stage_validate_adopt_without_provider(self):
        token=uuid4().hex;r=self.store.stage(token,self.draft['name'],self.draft['overview'],self.draft,validation_mode='structural')
        self.assertEqual(r['mode'],'human');self.assertEqual(r['state'],'invalid')
        self.assertFalse((self.repo/'projects').exists())
        r=self.store.update(token,r['edit_revision'],self.draft)
        self.assertEqual(r['state'],'review');self.assertIsNone(r['alignment']);self.assertEqual(r['summary']['alignment'],'offline')
        result=self.store.adopt(token,r['revision']);self.assertEqual(result['state'],'adopted')
        self.assertEqual(self.store.adopt(token,r['revision'])['world_id'],result['world_id'])
        self.provider.assert_not_called();self.provider.review.assert_not_called()

    def test_structural_does_not_accept_invalid_references_or_fake_review(self):
        bad=deepcopy(self.draft);bad['people'][0]['entry']='存在しない場所';token=uuid4().hex
        r=self.store.stage(token,bad['name'],bad['overview'],bad,validation_mode='structural')
        with self.assertRaises(ConfigError):self.store.update(token,r['edit_revision'],bad)
        with self.assertRaises(ConfigError):self.store.adopt(token,r['edit_revision'])
        self.assertFalse((self.repo/'projects').exists());self.provider.assert_not_called()

    def test_old_live_stage_still_requires_ai_review_and_policy_is_identity(self):
        token=uuid4().hex;r=self.store.stage(token,self.draft['name'],self.draft['overview'],self.draft)
        self.assertEqual(r['mode'],'live')
        with self.assertRaises(ConfigError):self.store.stage(token,self.draft['name'],self.draft['overview'],self.draft,validation_mode='structural')
        with self.assertRaises(AssertionError):self.store.update(token,r['edit_revision'],self.draft)
        self.provider.review.assert_called_once()
        with self.assertRaises(ConfigError):self.store.stage(uuid4().hex,self.draft['name'],self.draft['overview'],self.draft,validation_mode='mock')

    def test_working_copy_roundtrip_keeps_incomplete_input_and_ui_state(self):
        store=WorkspaceStore(self.repo,self.base/'control');token=uuid4().hex
        content={'version':1,'name':'','overview':'途中の概要','time':{'enabled':True,'days':'','slots':'朝\n朝'},'draft_text':'{ unfinished','source_draft_id':None,
                 'authoring':{'version':1,'type':'','outline':{'genre':'ファンタジー','future-note':'消さない'}},
                 'workflow':{'version':1,'step':2,'category':'places','selected':0,'consultation':{'instruction':'人の要望を保持する','targets':{'people':'{"entity":"甲"}'}}}}
        r=store.save(token,None,uuid4().hex,content)
        self.assertEqual(WorkspaceStore(self.repo,self.base/'control').get(token)['content'],content)
        other=deepcopy(content);other['overview']='別の手修正';r2=store.save(token,r['revision'],uuid4().hex,other)
        with self.assertRaises(ConfigError):store.save(token,r['revision'],uuid4().hex,content)
        self.assertEqual(store.get(token)['content'],other)
        for value in [{**content['workflow'],'step':True},{**content['workflow'],'step':4},{**content['workflow'],'category':'../files'}]:
            with self.assertRaises(ConfigError):workflow(value)

    def test_native_ui_metadata_never_changes_materialized_world(self):
        from test_draft_native import NativeTests
        fixture=NativeTests();fixture.setUp()
        try:
            r=fixture.create();before=fixture.store.materialize(fixture.store.store.get(fixture.token))[0]
            buffer=deepcopy(r['buffer']);buffer['workflow']={'version':1,'step':3,'category':'time','selected':0}
            updated=fixture.store.save(fixture.token,r['revision'],uuid4().hex,buffer)
            self.assertEqual(updated['buffer']['workflow'],buffer['workflow'])
            self.assertEqual(fixture.store.materialize(fixture.store.store.get(fixture.token))[0],before)
        finally:fixture.doCleanups()


import test_world_drafts as http_fixtures


class WorkflowHTTP(unittest.TestCase):
    setUp=http_fixtures.DraftHTTPTests.setUp
    shutdown=http_fixtures.DraftHTTPTests.shutdown
    request=http_fixtures.DraftHTTPTests.request

    def test_inline_list_asset_and_read_metadata_without_save_or_model(self):
        token=uuid4().hex;content={'version':1,'name':'list name','overview':'saved overview','time':{'enabled':False,'days':'','slots':''},'draft_text':'{ incomplete','source_draft_id':None,'workflow':{'version':1,'step':3,'category':'people','selected':0}}
        WorkspaceStore(self.repo,self.control).save(token,None,uuid4().hex,content)
        before={str(p.relative_to(self.control)):p.read_bytes() for p in self.control.rglob('*') if p.is_file()}
        status,raw=self.request('/worlds/new?screen=resume');self.assertEqual(status,200)
        self.assertIn(b'creation-workflow-list.js',raw);self.assertNotIn(b'data-cw-open',raw)
        self.assertEqual(self.request('/static/creation-workflow-list.js')[0],200)
        status,raw=self.request('/api/world-draft-workspaces');self.assertEqual(status,200)
        row=next(x for x in json.loads(raw)['workspaces'] if x['workspace_id']==token)
        self.assertEqual(row['overview'],'saved overview');self.assertEqual(row['workflow_step'],3)
        self.assertEqual(self.calls,0);self.assertEqual(before,{str(p.relative_to(self.control)):p.read_bytes() for p in self.control.rglob('*') if p.is_file()})

    def test_invalid_zip_import_rejects_without_creating_draft_or_calling_model(self):
        import base64
        token=uuid4().hex
        status,raw=self.request('/api/native-world-drafts/'+token+'/create',{'request_id':uuid4().hex,'source':{'zip_base64':base64.b64encode(b'not a zip').decode()}})
        self.assertIn(status,(400,422),raw)
        self.assertFalse((self.control/'native-world-drafts'/(token+'.json')).exists());self.assertEqual(self.calls,0)

    def test_entry_and_source_screens_separate_from_prepare_without_writes(self):
        from html.parser import HTMLParser
        class Screens(HTMLParser):
            def __init__(self):super().__init__();self.stack=[];self.controls={};self.choices=[]
            def handle_starttag(self,tag,attrs):
                a=dict(attrs)
                if a.get('data-cw-enter'):self.choices.append(a['data-cw-enter'])
                for key in ['data-cw-source','data-cw-zip','data-cw-list','data-cw-prepare']:
                    if key in a:self.controls[key]=tuple(x for frame in self.stack for x in frame[1])
                if tag not in ['input','img','br','hr','meta','link']:
                    self.stack.append((tag,[a['data-cw-screen']] if 'data-cw-screen' in a else ['stage-'+a['data-cw-panel']] if 'data-cw-panel' in a else []))
            def handle_endtag(self,tag):
                for i in range(len(self.stack)-1,-1,-1):
                    if self.stack[i][0]==tag:self.stack=self.stack[:i];break
        before={str(p.relative_to(self.repo)):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        for route in ['/worlds/new','/worlds/new?screen=import','/worlds/new?screen=world-import','/worlds/new?screen=zip-import','/worlds/new?screen=resume','/worlds/new?method=manual']:
            status,raw=self.request(route);self.assertEqual(status,200,raw)
            parsed=Screens();parsed.feed(raw.decode())
            self.assertEqual(parsed.choices,['world-import','zip-import','resume','new'])
            self.assertEqual(parsed.controls['data-cw-source'],('world-import',));self.assertEqual(parsed.controls['data-cw-zip'],('zip-import',))
            self.assertIn('resume',parsed.controls['data-cw-list']);self.assertNotIn('stage-1',parsed.controls['data-cw-list'])
            self.assertEqual(parsed.controls['data-cw-prepare'],('stage-1',))
        self.assertEqual(self.calls,0)
        self.assertEqual(before,{str(p.relative_to(self.repo)):p.read_bytes() for p in self.repo.rglob('*') if p.is_file()})

    def test_three_panels_registered_assets_and_explicit_structural_adoption(self):
        status, raw=self.request('/worlds/new');self.assertEqual(status,200)
        for label in ['下書きを用意','内容を整える','確認して採用','モック応答','AIへの相談は任意']:
            self.assertIn(label.encode(),raw)
        for asset in ['creation-workflow.css','creation-workflow-model.js','creation-workflow.js']:
            self.assertIn(asset.encode(),raw);self.assertEqual(self.request('/static/'+asset)[0],200)
        d=json.loads(mock_response('手入力の世界','人が設定した世界です。'))
        path='/api/world-drafts/'+self.token
        before_worlds=set((self.repo/'projects').glob('*/world.yaml'))
        body={'name':d['name'],'overview':d['overview'],'draft':d,'validation_mode':'structural'}
        self.assertEqual(self.request(path+'/stage',body,client=False)[0],403)
        status,raw=self.request(path+'/stage',body);self.assertEqual(status,200,raw);r=json.loads(raw)
        status,raw=self.request(path+'/validate',{'revision':r['edit_revision'],'draft':d});self.assertEqual(status,200,raw);r=json.loads(raw)
        self.assertEqual(r['mode'],'human');self.assertIsNone(r['alignment']);self.assertEqual(self.calls,0)
        status,raw=self.request(path+'/adopt',{'revision':r['revision']});self.assertEqual(status,200,raw)
        self.assertEqual(self.calls,0);self.assertEqual(len(set((self.repo/'projects').glob('*/world.yaml'))-before_worlds),1)

    def test_revision_status_is_read_only_and_identifier_checked(self):
        d=json.loads(mock_response('手入力の世界','人が設定した世界です。'))
        self.store.mode='live'
        self.store.provider.revise=lambda *args:json.dumps({'changes':[{'path':'/people/1/personality','value':'検証用の人物像'}]})
        r=self.store.stage(self.token,d['name'],d['overview'],d,validation_mode='structural')
        rid=uuid4().hex
        self.store.revise(self.token,r['edit_revision'],d,{'section':'people','entity':d['people'][1]['name'],'field':'profile'},'補足',rid)
        before={str(p.relative_to(self.control)):p.read_bytes() for p in self.control.rglob('*') if p.is_file()}
        status,raw=self.request('/api/world-drafts/'+self.token+'/revisions/'+rid)
        self.assertEqual(status,200,raw);self.assertEqual(json.loads(raw)['state'],'ready')
        self.assertEqual(before,{str(p.relative_to(self.control)):p.read_bytes() for p in self.control.rglob('*') if p.is_file()})
        self.assertNotEqual(self.request('/api/world-drafts/'+self.token+'/revisions/not-a-token')[0],200)


if __name__=='__main__':unittest.main()
