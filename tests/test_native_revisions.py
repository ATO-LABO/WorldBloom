from copy import deepcopy
import json
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4
import yaml

import test_draft_native as fixtures
from execution.native_revisions import NativeRevisions
from execution.draft_native import NativeStore
from execution.provenance import ConfigError


class NativeRevisionTests(unittest.TestCase):
    setUp = fixtures.NativeTests.setUp
    create = fixtures.NativeTests.create
    save = fixtures.NativeTests.save

    def candidate(self, r, target=None, provider=None, request_id=None):
        return NativeRevisions(self.store).propose(self.token, r['revision'], request_id or uuid4().hex,
            target or {'kind':'action','target':'verb:move'}, '昼だけアオの移動を許可する',
            provider or (lambda c,i:{'kind':'action','target':'verb:move','values':{'actors':['アオ'],'slots':['昼']}}))

    def test_import_human_ai_diff_save_reopen_publish_and_runtime_preserves_original(self):
        r = self.create();r = self.save(r,[{'kind':'time','target':None,'values':{'days':20,'slots':['朝','昼']}}])
        before = deepcopy(r['buffer']);calls=[]
        def provider(ctx,instruction):
            calls.append((ctx,instruction));self.assertEqual(ctx['references']['people'],['アオ','レン'])
            return {'kind':'action','target':'verb:move','values':{'actors':['アオ'],'slots':['昼']}}
        revs=NativeRevisions(self.store);c=self.candidate(r,provider=provider)
        self.assertEqual(c['state'],'ready',c);self.assertEqual(self.store.get(self.token)['buffer'],before)
        self.assertEqual({x['field'] for x in c['changes']},{'actors','slots'})
        self.assertEqual(self.candidate(r,provider=provider,request_id=c['request_id']),c);self.assertEqual(len(calls),1)
        saved=revs.apply(self.token,r['revision'],c['request_id']);self.assertEqual(saved['buffer']['edits'][0],before['edits'][0])
        reopened=NativeStore(self.repo,self.control);self.assertEqual(reopened.get(self.token)['buffer'],saved['buffer'])
        self.assertEqual(revs.apply(self.token,r['revision'],c['request_id']),saved)
        checked=reopened.check(self.token,saved['revision']);world_id=reopened.publish(self.token,saved['revision'],checked['digest'])['world_id']
        project=self.repo/'projects'/world_id;world=yaml.safe_load((project/'world.yaml').read_bytes())
        self.assertEqual(world['time']['days'],20);self.assertEqual(world['future'],{'custom':[1,{'nested':'保持'}]})
        self.assertEqual((project/'notes/unsupported.bin').read_bytes(),b'\x00\xffunknown')
        for rel,data in self.before.items():self.assertEqual((self.repo/rel).read_bytes(),data)
        from execution.configs import ConfigStore
        cfg=ConfigStore(self.repo,self.control,self.root/'runs');cfg.save({'label':'native共同作成','project_id':world_id,'template_id':'basic','evolution':{'kappa':0}},config_id='cfg-native')
        from gapengine.route_paths import _load_world_and_subjects
        inputs=self.control/'configs/cfg-native/inputs';w,people=_load_world_and_subjects(inputs/'projects'/world_id,inputs/'templates/basic');w.bind_subjects(people)
        from engine.action_usage import denial
        from engine.actions import Action
        self.assertEqual(denial(w,people['アオ'],Action('move',['森']),1,1),'action_time_restricted')
        self.assertIsNone(denial(w,people['アオ'],Action('move',['森']),2,1))
        self.assertEqual(denial(w,people['レン'],Action('move',['町']),2,1),'action_actor_restricted')

    def test_scope_and_invalid_references_fail_without_buffer_or_original_changes(self):
        r=self.create();before=deepcopy(r['buffer'])
        for op in [
            {'kind':'goal','target':'レン','values':{'description':'other'}},
            {'kind':'action','target':'verb:move','values':{'enabled':False}},
            {'kind':'action','target':'verb:move','values':{'actors':['未登録'],'slots':None}},
            {'kind':'action','target':'verb:move','values':{'actors':None,'slots':['未登録']}},
            {'kind':'action','target':'verb:move','values':{'actors':None,'slots':None},'file':'world.yaml'},
        ]:
            c=self.candidate(r,provider=lambda c,i:op);self.assertEqual(c['state'],'failed',c)
            self.assertEqual(self.store.get(self.token)['buffer'],before)
        calls=[]
        for target in [{'kind':'action','target':'verb:rest'},{'kind':'goal','target':'未登録'},{'kind':'event','target':True},{'kind':'file','target':'../world.yaml'}]:
            with self.assertRaises(ConfigError):self.candidate(r,target,lambda c,i:calls.append(1))
        self.assertFalse(calls)

    def test_conflict_stale_and_incomplete_input_never_overwrites_human_edits(self):
        r=self.create();c=self.candidate(r);revs=NativeRevisions(self.store)
        newer=self.save(r,[{'kind':'time','target':None,'values':{'days':23,'slots':['朝','昼']}}])
        self.assertEqual(revs.get(self.token,c['request_id'])['state'],'stale')
        with self.assertRaises(ConfigError):revs.apply(self.token,r['revision'],c['request_id'])
        self.assertEqual(self.store.get(self.token)['buffer'],newer['buffer'])
        unfinished=self.save(newer,[{'kind':'time','target':None,'values':{'days':'','slots':[]}}]);calls=[]
        with self.assertRaises(ConfigError):self.candidate(unfinished,provider=lambda c,i:calls.append(1))
        self.assertFalse(calls)

    def test_cancel_during_generation_and_tombstone_do_not_apply_or_retry(self):
        r=self.create();start=threading.Event();finish=threading.Event();out=[];revs=NativeRevisions(self.store);rid=uuid4().hex
        def provider(c,i):start.set();finish.wait(5);return {'kind':'action','target':'verb:move','values':{'actors':None,'slots':['昼']}}
        t=threading.Thread(target=lambda:out.append(self.candidate(r,provider=provider,request_id=rid)));t.start();self.assertTrue(start.wait(5))
        revs.cancel(self.token,rid);finish.set();t.join(10);self.assertFalse(t.is_alive());self.assertEqual(out[0]['state'],'cancelled')
        with self.assertRaises(ConfigError):revs.apply(self.token,r['revision'],rid)
        rid=uuid4().hex;self.assertEqual(revs.cancel(self.token,rid)['state'],'cancelled')
        with self.assertRaises(ConfigError):self.candidate(r,request_id=rid)
        self.assertEqual(self.store.get(self.token)['buffer'],r['buffer'])

    def test_uncertain_apply_is_readable_and_retry_is_idempotent(self):
        r=self.create();c=self.candidate(r);revs=NativeRevisions(self.store)
        import execution.native_revisions as module
        real=module.atomic_json
        def fail_receipt(path,value):
            if value.get('state')=='applied':raise OSError('simulated lost receipt')
            return real(path,value)
        with patch.object(module,'atomic_json',side_effect=fail_receipt),self.assertRaises(OSError):revs.apply(self.token,r['revision'],c['request_id'])
        self.assertEqual(revs.get(self.token,c['request_id'])['state'],'applied')
        with self.assertRaises(ConfigError):revs.cancel(self.token,c['request_id'])
        applied=revs.apply(self.token,r['revision'],c['request_id']);self.assertEqual(len(applied['buffer']['edits']),1)
        newer=self.save(applied,applied['buffer']['edits']+[{'kind':'time','target':None,'values':{'days':22,'slots':['朝','昼']}}])
        with self.assertRaises(ConfigError):revs.apply(self.token,r['revision'],c['request_id'])
        self.assertEqual(self.store.get(self.token)['revision'],newer['revision'])

    def test_goal_unknown_siblings_and_registered_priority_constraints(self):
        r=self.create();target={'kind':'goal','target':'アオ'}
        c=self.candidate(r,target,lambda ctx,i:{**target,'values':{**ctx['current'],'description':'人の意図を残す'}})
        self.assertEqual(c['state'],'ready',c);r=NativeRevisions(self.store).apply(self.token,r['revision'],c['request_id'])
        _,_,_,_,people=self.store.materialize(self.store.store.get(self.token));a=next(p for p in people.values() if p['id']=='アオ')
        self.assertEqual(a['goal']['custom_goal'],'保持');self.assertEqual(a['custom_person'],{'keep':True})
        target={'kind':'commitments','target':'アオ'}
        c=self.candidate(r,target,lambda ctx,i:{**target,'values':{'commitments':[],'additions':[{'id':'invented'}]}})
        self.assertEqual(c['state'],'failed')

    def test_factory_uses_existing_transport_once_without_executing_it(self):
        from execution.world_drafts import existing_provider
        provider=existing_provider(self.root/'settings.json')
        with patch('execution.world_drafts._draft_request',return_value={'backend':'ollama'}) as req,patch('execution.generation.preflight',return_value={}),patch('execution.generation.transport',return_value='fixed') as transport,patch('execution.generation.validate_response',return_value='fixed'):
            self.assertEqual(provider.native_revise({'target':{'kind':'goal','target':'アオ'},'current':{}},'目的の文章だけ修正'),'fixed')
            self.assertEqual(transport.call_count,1);self.assertIn('任意パス',req.call_args.args[1]);self.assertIn('アオ',req.call_args.args[1])


class NativeRevisionHTTP(unittest.TestCase):
    setUp=fixtures.NativeHTTPTests.setUp
    request=fixtures.NativeHTTPTests.request
    shutdown=fixtures.NativeHTTPTests.shutdown
    def test_http_scoped_candidate_explicit_apply_and_read_only_status(self):
        from execution.world_drafts import DraftStore
        calls=[]
        def provider(c,i):calls.append(1);return {'kind':'action','target':'verb:move','values':{'actors':['アオ'],'slots':['昼']}}
        main=lambda n,o:None;main.native_revise=provider
        self.server.world_drafts=DraftStore(self.repo,self.control,main,'live');self.server.job_store.world_draft_mode='fixture'
        path='/api/native-world-drafts/'+self.token;status,body=self.request(path+'/create',{'request_id':uuid4().hex,'source':{'world_id':'original'}});self.assertEqual(status,200,body);r=json.loads(body)
        rid=uuid4().hex;body={'revision':r['revision'],'request_id':rid,'target':{'kind':'action','target':'verb:move'},'instruction':'昼はアオだけ'}
        status,_=self.request(path+'/revise',body,client=False);self.assertEqual(status,403);self.assertFalse(calls)
        status,raw=self.request(path+'/revise',body);self.assertEqual(status,200,raw);self.assertEqual(json.loads(raw)['state'],'ready')
        status,raw=self.request(path+'/revisions/'+rid);self.assertEqual(status,200,raw);self.assertNotIn('buffer',json.loads(raw));self.assertEqual(len(calls),1)
        status,raw=self.request(path+'/apply-revision',{'revision':r['revision'],'request_id':rid});self.assertEqual(status,200,raw);self.assertEqual(len(json.loads(raw)['buffer']['edits']),1)
        status,raw=self.request('/worlds/new?method=ai');self.assertEqual(status,200);self.assertIn(b'draft-native-ai.js',raw)
        status,_=self.request('/static/draft-native-ai.js');self.assertEqual(status,200)
