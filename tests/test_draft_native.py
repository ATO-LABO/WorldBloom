from copy import deepcopy
import base64,io,json,tempfile,unittest,zipfile
from pathlib import Path
from uuid import uuid4
import shutil,yaml
from execution.draft_native import NativeStore,apply_edits,unpack,proposal_forms
from execution.world_drafts import compile_draft,mock_response,parse_response
from execution.library import LibraryStore
from execution.provenance import ConfigError

ROOT=Path(__file__).resolve().parents[1]
class NativeTests(unittest.TestCase):
    def test_list_uses_native_buffer_saved_stage_and_preserves_original_bytes(self):
        r=self.create();self.assertIsNone(self.store.list()[0]['workflow_step'])
        buffer=deepcopy(r['buffer']);buffer['workflow']={'version':1,'step':2,'category':'time','selected':0}
        self.store.save(self.token,r['revision'],uuid4().hex,buffer)
        before={p.relative_to(self.control).as_posix():p.read_bytes() for p in self.control.rglob('*') if p.is_file()}
        row=self.store.list()[0];self.assertEqual(row['workflow_step'],2);self.assertEqual(row['overview'],self.proposal['overview'][:240])
        self.assertEqual(before,{p.relative_to(self.control).as_posix():p.read_bytes() for p in self.control.rglob('*') if p.is_file()})
        for rel,data in self.before.items():self.assertEqual((self.repo/rel).read_bytes(),data)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=self.root/'repo'
        shutil.copytree(ROOT/'templates/basic',self.repo/'templates/basic')
        self.proposal=parse_response(mock_response('原本','森へ行く'))
        _,world,subjects=compile_draft(self.proposal)
        world['future']={'custom':[1,{'nested':'保持'}]};world['time']['future_time']='保持'
        for rel,text in list(subjects.items()):
            p=yaml.safe_load(text);p['custom_person']={'keep':True};p['goal']['custom_goal']='保持';subjects[rel]=yaml.safe_dump(p,allow_unicode=True)
        LibraryStore(self.repo)._publish_world('original',world=world,subjects=subjects)
        extra=self.repo/'projects/original/notes/unsupported.bin';extra.parent.mkdir();extra.write_bytes(b'\x00\xffunknown')
        self.before={p.relative_to(self.repo).as_posix():p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        self.control=self.root/'control';self.store=NativeStore(self.repo,self.control);self.token=uuid4().hex
    def create(self):return self.store.create(self.token,uuid4().hex,world_id='original')
    def save(self,r,edits):
        b=deepcopy(r['buffer']);b['edits']=edits
        return self.store.save(self.token,r['revision'],uuid4().hex,b)
    def test_native_import_edit_validate_publish_keeps_unknowns_and_original(self):
        r=self.create();r=self.save(r,[{'kind':'time','target':None,'values':{'days':'20','slots':'朝\n昼'}}])
        check=self.store.check(self.token,r['revision']);self.assertTrue(check['valid'])
        result=self.store.publish(self.token,r['revision'],check['digest']);dest=self.repo/'projects'/result['world_id']
        w=yaml.safe_load((dest/'world.yaml').read_bytes());self.assertEqual(w['future'],{'custom':[1,{'nested':'保持'}]});self.assertEqual(w['time']['future_time'],'保持');self.assertEqual(w['time']['days'],20)
        self.assertEqual((dest/'notes/unsupported.bin').read_bytes(),b'\x00\xffunknown')
        for rel,data in self.before.items():self.assertEqual((self.repo/rel).read_bytes(),data)
        self.assertEqual(self.store.publish(self.token,r['revision'],check['digest']),result)
        with self.assertRaises(ConfigError):self.save(r,[{'kind':'time','target':None,'values':{'days':21,'slots':['朝','昼']}}])
        self.assertEqual(self.store.get(self.token)['published_world'],result['world_id'])
    def test_zip_unknown_binary_and_hidden_entries_export_roundtrip(self):
        r=self.create();raw=base64.b64decode(self.store.export(self.token,r['revision'])['zip_base64'])
        files,deps=unpack(raw);self.assertIn('notes/unsupported.bin',files);self.assertIn('templates/basic/action_graph.yaml',deps)
        second=self.store.create(uuid4().hex,uuid4().hex,zip_base64=base64.b64encode(raw).decode())
        self.assertEqual(second['model']['time'],r['model']['time']);self.assertEqual(second['model']['people'],r['model']['people'])
        original=self.store.store.get(second['workspace_id'])['source_snapshot'];self.assertEqual(base64.b64decode(original['archive']['base64']),raw)
    def test_zip_rejects_traversal_collisions_symlink_and_outside_files_without_partial_save(self):
        for names in [['../world.yaml'],['C:/world.yaml'],['world.yaml','WORLD.YAML'],['folder/world.yaml','outside.txt'],['world.yaml','nul.txt']]:
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:
                for name in names:z.writestr(name,'name: test')
            with self.subTest(names=names),self.assertRaises(ConfigError):self.store.create(uuid4().hex,uuid4().hex,zip_base64=base64.b64encode(out.getvalue()).decode())
        self.assertFalse(self.store.list())
    def test_invalid_yaml_retained_and_never_published(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('world.yaml','broken: [');z.writestr('unknown.txt','keep')
        r=self.store.create(self.token,uuid4().hex,zip_base64=base64.b64encode(out.getvalue()).decode())
        self.assertIsNone(r['model']);self.assertTrue(r['parse_error'])
        with self.assertRaises(ConfigError):self.store.check(self.token,r['revision'])
        self.assertFalse((self.repo/'projects'/('world-'+self.token)).exists())
        self.assertEqual(base64.b64decode(self.store.export(self.token,r['revision'],True)['zip_base64']),out.getvalue())
    def test_old_registered_world_is_rejected_and_old_zip_is_archive_only(self):
        path = self.repo / 'projects' / 'legacy'
        path.mkdir()
        original = b'name: Legacy archive\n'
        (path / 'world.yaml').write_bytes(original)
        with self.assertRaises(ConfigError):
            self.store.create(self.token, uuid4().hex, world_id='legacy')
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            archive.writestr('world.yaml', original)
        raw = out.getvalue()
        row = self.store.create(self.token, uuid4().hex, zip_base64=base64.b64encode(raw).decode())
        self.assertIsNone(row['model'])
        self.assertIn('旧世界形式', row['parse_error'])
        exported = self.store.export(self.token, row['revision'], original=True)
        self.assertEqual(base64.b64decode(exported['zip_base64']), raw)
        with self.assertRaises(ConfigError):
            self.store.check(self.token, row['revision'])

    def test_incomplete_buffer_conflict_restart_and_fork(self):
        r=self.create();b=deepcopy(r['buffer']);b['edits']=[{'kind':'time','target':None,'values':{'days':'','slots':'朝\n朝'}}]
        saved=self.store.save(self.token,r['revision'],uuid4().hex,b)
        reread=NativeStore(self.repo,self.root/'control').get(self.token);self.assertEqual(saved['buffer'],reread['buffer'])
        with self.assertRaises(ConfigError):self.store.check(self.token,saved['revision'])
        with self.assertRaises(ConfigError):self.store.save(self.token,r['revision'],uuid4().hex,r['buffer'])
        fork=self.store.fork(self.token,uuid4().hex,uuid4().hex);self.assertEqual(fork['buffer'],saved['buffer'])
    def test_actions_events_goal_and_priorities_reach_native_runtime(self):
        r=self.create();condition={'id':'reach_forest','label':'森へ行く','mode':'achieve','visibility':'observed','test':{'kind':'zone','actor':'アオ','equals':'森'}}
        edits=[{'kind':'time','target':None,'values':{'days':20,'slots':['朝','昼']}},
          {'kind':'action','target':'verb:move','values':{'actors':['アオ'],'slots':['昼']}},
          {'kind':'goal','target':'アオ','values':{'description':'森へ移動したい','target':'灯りの種','deliver_to':'町'}},
          {'kind':'commitments','target':'アオ','values':{'additions':[condition],'commitments':[{'condition':'reach_forest','priority':'3','required':False}]}},
          {'kind':'event','target':None,'values':{'id':'notice','label':'連絡','day':'2','slot':'朝','targets':['アオ'],'stress_delta':'0.2'}}]
        saved=self.save(r,edits);self.assertTrue(self.store.check(self.token,saved['revision'])['valid'])
        files,_,_,world,people=self.store.materialize(self.store.store.get(self.token))
        self.assertEqual(world['action_usage']['constraints']['verb:move'],{'actors':['アオ'],'slots':['昼']})
        p=next(p for p in people.values() if p['id']=='アオ');self.assertEqual(p['commitments'][0]['priority'],3);self.assertEqual(p['goal']['custom_goal'],'保持');self.assertEqual(world['scheduled_events'][0]['day'],2)
        result=self.store.publish(self.token,saved['revision'],self.store.check(self.token,saved['revision'])['digest'])
        from gapengine.route_paths import _load_world_and_subjects
        w,subjects=_load_world_and_subjects(self.repo/'projects'/result['world_id'],self.repo/'templates/basic');w.bind_subjects(subjects)
        from execution.configs import ConfigStore
        control=self.root/'frozen-control';cfgs=ConfigStore(self.repo,control,self.root/'runs')
        cfgs.save({'label':'native forms','project_id':result['world_id'],'template_id':'basic','evolution':{'kappa':0}},config_id='cfg-native')
        frozen=control/'configs/cfg-native/inputs/projects'/result['world_id']
        captured=yaml.safe_load((frozen/'world.yaml').read_bytes());self.assertEqual(captured['action_usage']['constraints'],world['action_usage']['constraints']);self.assertEqual(captured['scheduled_events'],world['scheduled_events']);self.assertEqual(captured['future'],world['future'])
        frozen_people=[yaml.safe_load(p.read_bytes()) for p in (frozen/'subjects').glob('*.yaml')];self.assertEqual(next(p for p in frozen_people if p['id']=='アオ')['commitments'][0]['priority'],3)
        from engine.action_usage import denial
        from engine.actions import Action
        self.assertEqual(denial(w,subjects['アオ'],Action('move',['森']),1,1),'action_time_restricted')
    def test_missing_references_required_rest_and_changed_dependencies_rejected(self):
        r=self.create()
        for op in [{'kind':'action','target':'verb:rest','values':{'actors':[],'slots':None}}, {'kind':'event','target':None,'values':{'id':'bad','day':50,'targets':['missing']}}]:
            s=self.save(self.store.get(self.token),[op])
            with self.assertRaises(ConfigError):self.store.check(self.token,s['revision'])
        s=self.save(self.store.get(self.token),[]);check=self.store.check(self.token,s['revision'])
        (self.repo/'templates/basic/effects.yaml').write_text('changed: true',encoding='utf-8')
        with self.assertRaises(ConfigError):self.store.publish(self.token,s['revision'],check['digest'])
    def test_proposal_forms_preserve_settings_and_unrelated_people(self):
        original=deepcopy(self.proposal)
        changed=proposal_forms(original,[{'kind':'time','target':None,'values':{'days':'20','slots':'朝\n昼'}},{'kind':'action','target':'verb:move','values':{'actors':['アオ'],'slots':['昼']}}])
        self.assertEqual(changed['people'],original['people']);self.assertEqual(changed['settings']['time']['days'],20)
        self.assertEqual(proposal_forms(changed)['constraints']['verb:move']['slots'],['昼'])

import test_world_drafts as draft_http
class NativeHTTPTests(unittest.TestCase):
    request=draft_http.DraftHTTPTests.request
    shutdown=draft_http.DraftHTTPTests.shutdown
    def setUp(self):
        NativeTests.setUp(self)
        from viewer.server import ViewerServer,ViewerHandler
        from execution.configs import ConfigStore
        from execution.jobs import JobStore
        from viewer.data import RunRepository
        import threading
        (self.root/'runs').mkdir()
        self.server=ViewerServer(('127.0.0.1',0),ViewerHandler)
        self.server.job_store=JobStore(ConfigStore(self.repo,self.control,self.root/'runs'))
        self.server.repository=RunRepository(self.root/'runs');self.server.job_store.world_draft_mode='mock'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.shutdown)
    def test_http_native_large_zip_save_reload_and_scope_boundary(self):
        import random
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            for p in (self.repo/'projects/original').rglob('*'):
                if p.is_file():z.writestr(p.relative_to(self.repo/'projects/original').as_posix(),p.read_bytes())
            z.writestr('unknown-big.bin',random.Random(1).randbytes(1600000))
        path='/api/native-world-drafts/'+self.token
        body={'request_id':uuid4().hex,'source':{'zip_base64':base64.b64encode(out.getvalue()).decode()}}
        status,raw=self.request(path+'/create',body);self.assertEqual(status,200,raw);r=json.loads(raw)
        status,_=self.request(path+'/create',body,client=False);self.assertEqual(status,403)
        b=r['buffer'];b['edits']=[{'kind':'time','target':None,'values':{'days':'','slots':'朝\n朝'}}]
        status,raw=self.request(path+'/save',{'revision':r['revision'],'request_id':uuid4().hex,'buffer':b});self.assertEqual(status,200,raw);saved=json.loads(raw)
        status,raw=self.request(path);self.assertEqual(status,200);self.assertEqual(json.loads(raw)['buffer'],b)
        status,_=self.request(path+'/check',{'revision':saved['revision']});self.assertEqual(status,422)
        self.assertEqual((self.repo/'projects/original/notes/unsupported.bin').read_bytes(),b'\x00\xffunknown')
        self.assertFalse((self.repo/'projects'/('world-'+self.token)).exists())
    def test_http_forms_new_assets_and_no_model(self):
        status,raw=self.request('/worlds/new?method=ai');self.assertEqual(status,200,raw)
        self.assertIn(b'draft-settings-forms.js',raw);self.assertIn(b'draft-native.js',raw)
        for asset in ['draft-settings-forms.js','draft-native.js','draft-native.css']:
            status,_=self.request('/static/'+asset);self.assertEqual(status,200)
        status,raw=self.request('/api/world-draft-forms',{'draft':self.proposal});self.assertEqual(status,200,raw);self.assertEqual(json.loads(raw)['people'][0]['name'],'アオ')
        status,raw=self.request('/api/world-draft-forms',{'draft':self.proposal,'edits':[{'kind':'time','target':None,'values':{'days':20,'slots':['朝','昼']}}]});self.assertEqual(status,200,raw);self.assertEqual(json.loads(raw)['settings']['time']['days'],20)
