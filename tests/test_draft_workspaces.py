"""Working-copy persistence tests. No provider/model process is called."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import base64
import json
from pathlib import Path
import tempfile
import unittest
import yaml
from unittest.mock import patch
from uuid import uuid4

from execution.draft_workspaces import WorkspaceStore
from execution.provenance import ConfigError, atomic_json
from execution.world_drafts import DraftStore, mock_response, parse_response
import test_world_drafts as draft_http

def content(text='{ incomplete'):
    return {'version':1,'name':'','overview':'検討中\n','time':{'enabled':True,'days':'','slots':'朝\n朝\n'},'draft_text':text,'source_draft_id':None}

class WorkTests(unittest.TestCase):
    def test_list_metadata_reads_saved_overview_step_and_keeps_files_exact(self):
        c=content();c['overview']='概要'*300;c['workflow']={'version':1,'step':3,'category':'people','selected':0}
        self.save(c)
        before={p.relative_to(self.control).as_posix():p.read_bytes() for p in self.control.rglob('*') if p.is_file()}
        row=self.store.list()[0]
        self.assertEqual(row['overview'],c['overview'][:240]);self.assertEqual(row['workflow_step'],3)
        self.assertEqual(before,{p.relative_to(self.control).as_posix():p.read_bytes() for p in self.control.rglob('*') if p.is_file()})

    def test_list_missing_saved_stage_and_timestamp_are_unknown_without_rewriting(self):
        self.save();record=self.store.get(self.token);record.pop('updated_at');atomic_json(self.store._path(self.token),record)
        before=self.store._path(self.token).read_bytes();row=self.store.list()[0]
        self.assertIsNone(row['workflow_step']);self.assertIsNone(row['updated_at']);self.assertNotIn('unsupported',row)
        self.assertEqual(before,self.store._path(self.token).read_bytes())

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)/'repo';self.control=Path(self.temp.name)/'control'
        self.store=WorkspaceStore(self.repo,self.control);self.token=uuid4().hex
    def save(self,c=None,revision=None,**kw):
        return self.store.save(self.token,revision,uuid4().hex,c or content(),**kw)

    def test_incomplete_invalid_json_and_unknown_proposal_settings_are_exact(self):
        raw='{"settings":{"future_value":4}, "people": [\n'
        r=self.save(content(raw));again=WorkspaceStore(self.repo,self.control).get(self.token)
        self.assertEqual(again['content'],content(raw));self.assertEqual(self.store.list()[0]['workspace_id'],self.token)
        self.assertFalse((self.repo/'projects').exists());self.assertNotIn('alignment',r)

    def test_restart_roundtrip_conflict_and_history_keep_both_edits(self):
        r=self.save();c=deepcopy(r['content']);c['draft_text']='second edit'
        updated=self.save(c,r['revision'])
        with self.assertRaises(ConfigError):self.save(content('stale edit'),r['revision'])
        self.assertEqual(self.store.get(self.token),updated)
        old=json.loads((self.store.root/'history'/self.token/'1.json').read_text(encoding='utf-8'))
        self.assertEqual(old['content'],r['content'])

    def test_receipt_reconciles_response_loss_and_never_rolls_back(self):
        request=uuid4().hex;c=content();first=self.store.save(self.token,None,request,c)
        self.assertEqual(first,self.store.save(self.token,None,request,c))
        second=self.save(content('later'),first['revision'])
        with self.assertRaises(ConfigError):self.store.save(self.token,None,request,c)
        self.assertEqual(self.store.get(self.token),second)

    def test_partial_receipt_write_is_recovered_from_record_without_second_save(self):
        request=uuid4().hex
        def write(path,value):
            if 'receipts' in path.parts:raise OSError('test receipt failure')
            return atomic_json(path,value)
        with patch('execution.draft_workspaces.atomic_json',side_effect=write),self.assertRaises(OSError):
            self.store.save(self.token,None,request,content())
        first=self.store.get(self.token)
        retried=self.store.save(self.token,None,request,content())
        self.assertEqual(first,retried);self.assertEqual(first['sequence'],1)

    def test_atomic_failure_leaves_previous_complete_copy(self):
        first=self.save()
        def write(path,value):
            if path==self.store._path(self.token):raise OSError('test write failure')
            return atomic_json(path,value)
        with patch('execution.draft_workspaces.atomic_json',side_effect=write),self.assertRaises(OSError):self.save(content('lost response'),first['revision'])
        self.assertEqual(self.store.get(self.token),first)

    def test_competing_writers_cannot_both_replace_same_revision(self):
        first=self.save()
        def writer(text):
            try:return self.save(content(text),first['revision'])
            except ConfigError:return None
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(writer,['one','two']))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertIn(self.store.get(self.token)['content']['draft_text'],['one','two'])

    def test_native_original_and_related_template_are_lossless_and_immutable_on_fork(self):
        project=self.repo/'projects/example';project.mkdir(parents=True)
        template=self.repo/'templates/basic';template.mkdir(parents=True)
        from engine.world_format import initialize
        world = initialize({'name': 'original', 'gapengine': {'action_graph': 'templates/basic/graph.yaml', 'effects': 'templates/basic/graph.yaml'}})
        world_bytes = b'# comments preserved\n' + yaml.safe_dump(world).encode('utf-8')
        originals={'projects/example/world.yaml':world_bytes,
            'projects/example/subjects/person.yaml':b'custom_unknown: yes\n',
            'projects/example/notes/extra.bin':b'\x00\xffnative',
            'templates/basic/graph.yaml':b'custom: [one,two]\n'}
        for key,data in originals.items():p=self.repo/key;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        r=self.save(source_world_id='example')
        for key,data in originals.items():self.assertEqual(base64.b64decode(r['source_snapshot']['files'][key]['base64']),data)
        (project/'world.yaml').write_text('name: changed',encoding='utf-8')
        updated=self.save(content('manual edit'),r['revision'])
        self.assertEqual(updated['source_snapshot'],r['source_snapshot'])
        fork=self.store.save(uuid4().hex,None,uuid4().hex,content('branch'),source_workspace_id=self.token)
        self.assertEqual(fork['source_snapshot'],r['source_snapshot'])
        self.assertEqual((project/'world.yaml').read_text(),'name: changed')

    def test_unknown_envelope_and_oversize_never_overwrite(self):
        first=self.save();bad=content();bad['version']=2
        with self.assertRaises(ConfigError):self.save(bad,first['revision'])
        with self.assertRaises(ConfigError):self.save(content('x'*260000),first['revision'])
        self.assertEqual(self.store.get(self.token),first)
        future=deepcopy(first);future['schema_version']=2;atomic_json(self.store._path(self.token),future)
        with self.assertRaises(ConfigError):self.save(revision=first['revision'])
        self.assertTrue(self.store.list()[0]['unsupported'])

    def test_paths_and_ids_cannot_escape_workspace(self):
        for token in ['../bad','a'*31,'A'*32]:
            with self.assertRaises(ConfigError):self.store.save(token,None,uuid4().hex,content())
        with self.assertRaises(ConfigError):self.save(source_world_id='../bad')
        root=self.repo/'projects/bad';root.mkdir(parents=True)
        (root/'world.yaml').write_text('gapengine:\n  action_graph: templates/../private.yaml\n',encoding='utf-8')
        with self.assertRaises(ConfigError):self.save(source_world_id='bad')
        self.assertFalse(self.store._path(self.token).exists())

class WorkHTTPTests(unittest.TestCase):
    setUp=draft_http.DraftHTTPTests.setUp
    shutdown=draft_http.DraftHTTPTests.shutdown
    request=draft_http.DraftHTTPTests.request
    def test_save_restart_list_and_resume_without_model_or_world(self):
        base='/api/world-draft-workspaces';c=content();c['draft_text']='{ JSON unfinished\n'
        status,raw=self.request(base+'/'+self.token,{'revision':None,'request_id':uuid4().hex,'content':c})
        self.assertEqual(status,200,raw);r=json.loads(raw)
        status,raw=self.request(base);self.assertEqual(status,200);self.assertEqual(json.loads(raw)['workspaces'][0]['workspace_id'],self.token)
        status,raw=self.request(base+'/'+self.token);self.assertEqual(json.loads(raw)['content'],c)
        status,_=self.request(base+'/'+self.token,{'revision':'stale','request_id':uuid4().hex,'content':c});self.assertEqual(status,409)
        status,_=self.request(base+'/'+uuid4().hex,{'revision':None,'request_id':uuid4().hex,'content':c},client=False);self.assertEqual(status,403)
        self.assertEqual(self.calls,0);self.assertFalse((self.repo/'projects').exists())

    def test_human_working_copy_can_be_checked_and_adopted_as_new_only(self):
        d=parse_response(mock_response('manual','unfinished'))
        status,raw=self.request('/api/world-drafts/'+self.token+'/stage',{'name':'manual','overview':'unfinished','draft':d})
        self.assertEqual(status,200,raw);r=json.loads(raw)
        self.assertEqual(r['state'],'invalid');self.assertEqual(self.calls,0)
        status,_=self.request('/api/world-drafts/'+self.token+'/adopt',{'revision':r['edit_revision']});self.assertEqual(status,409)
        status,raw=self.request('/api/world-drafts/'+self.token+'/validate',{'revision':r['edit_revision'],'draft':d})
        self.assertEqual(status,200,raw);checked=json.loads(raw)
        status,raw=self.request('/api/world-drafts/'+self.token+'/adopt',{'revision':checked['revision']})
        self.assertEqual(status,200,raw);self.assertEqual(json.loads(raw)['state'],'adopted');self.assertEqual(self.calls,0)

    def test_stage_does_not_replace_existing_or_approve_live_without_review(self):
        d=parse_response(mock_response('x','y'));self.store.mode='live'
        r=self.store.stage(self.token,'x','y',d)
        with self.assertRaises(ConfigError):self.store.update(self.token,r['edit_revision'],d)
        self.assertEqual(self.store.get(self.token)['state'],'invalid');self.assertEqual(self.calls,0)
        changed=deepcopy(d);changed['initial_story']='edited'
        with self.assertRaises(ConfigError):self.store.stage(self.token,'x','y',changed)
