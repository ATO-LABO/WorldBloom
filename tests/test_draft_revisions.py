from copy import deepcopy
from pathlib import Path
import json
import shutil
import tempfile
import threading
import unittest
from uuid import uuid4

from execution.provenance import ConfigError, atomic_json
from execution.world_drafts import DraftStore, mock_response

ROOT=Path(__file__).resolve().parents[1]


class Revisions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='wb-revision-')
        self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name)/'repo'
        shutil.copytree(ROOT/'templates/basic',self.repo/'templates/basic')
        self.store=DraftStore(self.repo,Path(self.tmp.name)/'control',mock_response,'mock')
        self.token=uuid4().hex
        self.record=self.store.create(self.token,'灯りの町','アオが灯りの種を町に届ける。')
        self.record['mode']='live'
        atomic_json(self.store._path(self.token),self.record)
        self.calls=self.reviews=0
        def provider(*args):
            raise AssertionError('generation must not run')
        def revise(*args):
            self.calls+=1
            return json.dumps({'changes':[{'path':'/people/1/personality','value':'親切な案内人'}]},ensure_ascii=False)
        def review(name,overview,draft,**kwargs):
            self.reviews+=1
            return {'overview_matches':True,'goals':[{'person':p['name'],'matches':True,'reason':'対象の内容と条件が対応'} for p in draft['people']],'issues':[]}
        provider.revise=revise
        provider.review=review
        self.store.provider=provider
        self.store.mode='live'

    def preview(self, **kwargs):
        args={'revision':self.record['revision'],'draft':self.record['draft'],'target':{'section':'people','entity':'レン'},'instruction':'レンの人物像を親切な案内人に','request_id':uuid4().hex}
        args.update(kwargs)
        return self.store.revise(self.token,**args)

    def test_preview_preserves_human_edits_and_does_not_write_draft(self):
        human=deepcopy(self.record['draft'])
        human['places'][0]['description']='自分で修正した説明'
        p=self.preview(draft=human)
        self.assertEqual(p['state'],'ready')
        self.assertEqual(p['candidate']['places'][0]['description'],'自分で修正した説明')
        self.assertEqual(self.store.get(self.token)['draft'],self.record['draft'])
        self.assertEqual(self.reviews,0)

    def test_preview_and_apply_are_idempotent_and_reloaded(self):
        request_id=uuid4().hex
        p=self.preview(request_id=request_id)
        self.assertEqual(self.preview(request_id=request_id),p)
        self.assertEqual(self.calls,1)
        saved=self.store.apply_revision(self.token,self.record['revision'],request_id,self.record['draft'])
        self.assertEqual(self.store.get(self.token)['draft'],p['candidate'])
        self.assertEqual(saved['alignment']['revision'],saved['revision'])
        self.assertEqual(self.store.apply_revision(self.token,self.record['revision'],request_id,self.record['draft']),saved)
        self.assertEqual(self.reviews,1)
        self.assertFalse((self.repo/'projects').exists())

    def test_stale_input_does_not_call_ai_and_changed_local_draft_does_not_apply(self):
        with self.assertRaises(ConfigError):self.preview(revision='old')
        self.assertEqual(self.calls,0)
        p=self.preview()
        other=deepcopy(self.record['draft']);other['initial_story']='編集後'
        with self.assertRaises(ConfigError):self.store.apply_revision(self.token,self.record['revision'],p['request_id'],other)
        self.assertEqual(self.reviews,0)

    def test_conflicting_id_and_cancelled_draft_are_rejected(self):
        request_id=uuid4().hex
        self.preview(request_id=request_id)
        with self.assertRaises(ConfigError):self.preview(request_id=request_id,instruction='別の修正')
        self.store.cancel(self.token)
        with self.assertRaises(ConfigError):self.preview()
        self.assertEqual(self.calls,1)

    def test_invalid_unknown_and_noop_responses_do_not_write_or_retry(self):
        samples=['not json',json.dumps({'changes':[{'path':'/overview','value':'別の概要'}]}),json.dumps({'changes':[{'path':'/people/1/personality','value':'穏やか'}]}),json.dumps({'changes':[{'path':'/people/1/entry','value':'未登録の場所'}]}),json.dumps({'changes':[{'path':'/people/-1/description','value':'x'}]})]
        for response in samples:
            self.store.provider.revise=lambda *args:response
            p=self.preview()
            self.assertEqual(p['state'],'invalid',response)
            self.assertEqual(self.store.get(self.token)['draft'],self.record['draft'])
        def offline(*args):raise TimeoutError()
        self.store.provider.revise=offline
        p=self.preview();self.assertEqual(p['state'],'unknown')
        self.assertEqual(self.reviews,0)

    def test_rejected_alignment_keeps_original_and_cannot_adopt_candidate(self):
        p=self.preview()
        def rejected(*args,**kwargs):raise ConfigError('alignment','目標と一致しません')
        self.store.provider.review=rejected
        with self.assertRaises(ConfigError):self.store.apply_revision(self.token,self.record['revision'],p['request_id'],self.record['draft'])
        self.assertEqual(self.store.get(self.token)['draft'],self.record['draft'])

    def test_cancel_while_ai_runs_marks_response_stale(self):
        original=self.store.provider.revise
        def cancel(*args):
            self.store.cancel(self.token)
            return original(*args)
        self.store.provider.revise=cancel
        self.assertEqual(self.preview()['state'],'stale')
        self.assertEqual(self.store.get(self.token)['state'],'cancelled')

    def test_unavailable_and_invalid_target_never_call_ai(self):
        with self.assertRaises(ConfigError):self.preview(target={'section':'people','entity':'不明'})
        self.store.mode='mock'
        with self.assertRaises(ConfigError):self.preview()
        self.assertEqual(self.calls,0)

    def test_optional_route_condition_can_be_added_without_replacing_other_routes(self):
        self.store.provider.revise=lambda *args:json.dumps({'changes':[{'path':'/routes/1/requires_item','value':'灯りの種'}]},ensure_ascii=False)
        p=self.preview(target={'section':'routes','entity':'1'})
        self.assertEqual(p['state'],'ready')
        self.assertEqual(p['candidate']['routes'][1]['requires_item'],'灯りの種')
        self.assertEqual(p['candidate']['routes'][0],self.record['draft']['routes'][0])

    def goal_preview(self):
        goal={'description':'灯りの種を手に入れて町へ届ける','conditions':[{'type':'deliver','item':'灯りの種','place':'町'}]}
        self.store.provider.revise=lambda *args:json.dumps({'changes':[{'path':'/people/0/goal','value':goal}]},ensure_ascii=False)
        return self.preview(target={'section':'people','entity':'アオ','field':'goal'})

    def test_goal_candidate_can_be_manually_refined_before_acceptance(self):
        p=self.goal_preview();self.assertEqual(p['state'],'ready')
        edited=deepcopy(p['candidate']);edited['people'][0]['goal']['description']='灯りの種を町へ届ける'
        saved=self.store.apply_revision(self.token,self.record['revision'],p['request_id'],self.record['draft'],edited)
        self.assertEqual(saved['draft'],edited)
        self.assertEqual(saved['draft']['people'][1],self.record['draft']['people'][1])

    def test_ai_goal_candidate_cannot_change_personality_or_other_people(self):
        goal={'description':'灯りの種を町へ届ける','conditions':[{'type':'deliver','item':'灯りの種','place':'町'}]}
        self.store.provider.revise=lambda *args:json.dumps({'changes':[{'path':'/people/0/goal','value':goal},{'path':'/people/1/personality','value':'変更してはいけない人物像'}]},ensure_ascii=False)
        p=self.preview(target={'section':'people','entity':'アオ','field':'goal'})
        self.assertEqual(p['state'],'invalid');self.assertEqual(self.store.get(self.token)['draft'],self.record['draft'])

    def test_manually_submitted_ai_candidate_cannot_escape_edited_item(self):
        p=self.goal_preview();edited=deepcopy(p['candidate']);edited['people'][0]['description']='範囲外'
        with self.assertRaises(ConfigError):self.store.apply_revision(self.token,self.record['revision'],p['request_id'],self.record['draft'],edited)
        self.assertEqual(self.reviews,0)

    def test_invalid_field_target_never_contacts_model(self):
        with self.assertRaises(ConfigError):self.preview(target={'section':'people','entity':'アオ','field':'unregistered'})
        self.assertEqual(self.calls,0)


if __name__=='__main__':unittest.main()
