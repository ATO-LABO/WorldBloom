"""Behavioral regressions for overview alignment, initial goals and sealed review."""
from copy import deepcopy
from pathlib import Path
import json,unittest
from unittest.mock import patch
import test_world_drafts as fixtures
import test_world_draft_goals as goal_fixtures
from execution.world_drafts import validate, existing_provider
from execution.provenance import ConfigError,atomic_json
from execution.draft_alignment import checked_review

class AlignmentTests(unittest.TestCase):
    setUp=fixtures.DraftTests.setUp
    draft=goal_fixtures.GeneralGoalTests.draft

    def response(self):
        d=self.draft([{'type':'reach','place':'森'}],'森にたどり着く')
        return d

    def positive(self,d):
        return {'overview_matches':True,'goals':[{'person':p['name'],'matches':True,'reason':'概要の目的と条件が一致する'} for p in d['people']],'issues':[]}

    def live(self,review=None):
        d=self.response();calls=[]
        def provider(*args):calls.append('generate');return json.dumps(d,ensure_ascii=False)
        def assess(*args,**kwargs):
            calls.append('review')
            return review(*args,**kwargs) if review else self.positive(d)
        provider.review=assess;self.store.provider=provider;self.store.mode='live'
        return d,calls

    def test_unrelated_delivery_response_rejected_for_reconciliation(self):
        r=self.store.create(self.token,'夏祭り','幼なじみ二人が仲直りする')
        self.assertEqual(r['state'],'invalid');self.assertIn('alignment.overview',r['errors'])
        self.assertFalse((self.repo/'projects').exists())

    def test_discovery_cannot_be_substituted_by_arrival(self):
        with self.assertRaises(ConfigError) as caught:
            validate(self.repo,self.draft([{'type':'reach','place':'森'}],'事件の真相を解明する'))
        self.assertIn('people.0.goal.conditions',caught.exception.field_errors)

    def test_reunion_and_reconciliation_require_both_conditions(self):
        d=self.draft([{'type':'befriend','person':'レン','threshold':0.6}], 'レンと再会し、仲直りする')
        with self.assertRaises(ConfigError) as caught:validate(self.repo,d)
        self.assertIn('meet',str(caught.exception))
        d['people'][0]['goal']['conditions'].append({'type':'meet','person':'レン'})
        validate(self.repo,d)

    def test_initially_true_reach_meeting_and_possession_rejected(self):
        for kind in ['reach','meet','acquire']:
            with self.subTest(kind=kind):
                c={'type':kind,**({'place':'町'} if kind=='reach' else {'person':'レン'} if kind=='meet' else {'item':'鍵'})}
                d=self.draft([c]);d['people'][1]['entry']='町'
                if kind=='acquire':d['items']=[{'name':'鍵','holder':'アオ'}]
                with self.assertRaises(ConfigError) as caught:validate(self.repo,d)
                self.assertIn('開始時点',str(caught.exception))

    def test_initially_true_friendship_rejected_but_partial_and_not_rejected(self):
        d=self.draft([{'type':'befriend','person':'レン','threshold':0.3}])
        d['people'][1]['relations']=[{'person':'アオ','affinity':0.4,'awareness':1,'label':'知人'}]
        with self.assertRaises(ConfigError):validate(self.repo,d)
        d=self.draft([{'type':'reach','place':'町'},{'type':'acquire','item':'鍵'}])
        d['items']=[{'name':'鍵','holder':'レン'}]
        validate(self.repo,d)  # The whole conjunction is false at entry.

    def test_narrative_only_is_not_forced_into_an_unrelated_condition(self):
        d=self.draft([],'秘密を守りながら自分の生き方を見つける')
        _,world,_,summary=validate(self.repo,d)
        self.assertEqual(world['target_ending'],['manual_review'])
        self.assertIn('アオ',summary['narrative_only_goals'])

    def test_review_must_cover_every_person_with_actual_booleans(self):
        d=self.response();good=self.positive(d)
        for change in [lambda r:r.update(overview_matches='true'),lambda r:r.update(goals=[]),
                       lambda r:r['goals'].append(deepcopy(r['goals'][0])),lambda r:r['goals'][0].update(matches=1)]:
            bad=deepcopy(good);change(bad)
            with self.subTest(review=bad),self.assertRaises(ConfigError):checked_review(bad,d)

    def test_live_draft_requires_review_and_adoption_does_not_call_model_again(self):
        d,calls=self.live();r=self.store.create(self.token,d['name'],d['overview'])
        self.assertEqual(r['state'],'review');self.assertEqual(calls,['generate','review'])
        self.assertEqual(r['alignment']['revision'],r['revision'])
        self.assertEqual(self.store.adopt(self.token,r['revision'])['state'],'adopted')
        self.assertEqual(calls,['generate','review'])

    def test_negative_review_blocks_adoption_without_retry(self):
        d=self.response();negative=self.positive(d);negative['overview_matches']=False;negative['issues']=['概要を別の話へ置き換えている']
        _,calls=self.live(lambda *a,**k:negative)
        r=self.store.create(self.token,d['name'],d['overview'])
        self.assertEqual(r['state'],'invalid');self.assertIn('alignment',r['errors'])
        self.assertEqual(self.store.create(self.token,d['name'],d['overview']),r)
        self.assertEqual(calls,['generate','review'])
        with self.assertRaises(ConfigError):self.store.adopt(self.token,r['revision'])

    def test_timeout_never_auto_retries_review(self):
        def timeout(*a,**k):raise TimeoutError()
        d,calls=self.live(timeout);r=self.store.create(self.token,d['name'],d['overview'])
        self.assertEqual(r['state'],'unknown');self.assertEqual(calls,['generate','review'])
        self.assertEqual(self.store.create(self.token,d['name'],d['overview']),r)

    def test_edit_rechecks_exact_revision_and_input_cannot_be_erased(self):
        d,calls=self.live();r=self.store.create(self.token,d['name'],d['overview'])
        changed=deepcopy(r['draft']);changed['overview']='違う入力'
        with self.assertRaises(ConfigError):self.store.update(self.token,r['revision'],changed)
        changed=deepcopy(r['draft']);changed['initial_story']='アオが森への道を探し始める。'
        updated=self.store.update(self.token,r['revision'],changed)
        self.assertNotEqual(updated['revision'],r['revision']);self.assertEqual(calls,['generate','review','review'])
        with self.assertRaises(ConfigError):self.store.adopt(self.token,r['revision'])
        self.assertEqual(updated['alignment']['revision'],updated['revision'])

    def test_person_and_place_text_revalidation_and_reload_preserve_other_fields(self):
        d,calls=self.live();r=self.store.create(self.token,d['name'],d['overview'])
        changed=deepcopy(r['draft']);changed['people'][0]['description']='紹介の改行\n<img src=x>'
        changed['people'][0]['personality']='落ち着いた人物像';changed['places'][0]['description']='場所の説明\n次の行'
        updated=self.store.update(self.token,r['revision'],changed)
        self.assertEqual(calls,['generate','review','review'])
        self.assertNotEqual(updated['revision'],r['revision'])
        self.assertEqual(updated['draft']['people'][0]['goal'],r['draft']['people'][0]['goal'])
        self.assertEqual(updated['draft']['people'][0]['relations'],r['draft']['people'][0]['relations'])
        too_long=deepcopy(updated['draft']);too_long['places'][0]['description']='x'*2001
        with self.assertRaises(ConfigError):self.store.update(self.token,updated['revision'],too_long)
        self.assertEqual(self.store.get(self.token)['revision'],updated['revision'])
        self.assertEqual(calls,['generate','review','review'])
        self.store.adopt(self.token,updated['revision'])
        from execution.world_editor import snapshot
        from execution.library import LibraryStore
        saved=snapshot(LibraryStore(self.repo),updated['world_id'])
        person=next(p for p in saved['people'] if p['id']=='アオ')
        self.assertEqual(person['description'],changed['people'][0]['description'])
        self.assertEqual(person['personality'],changed['people'][0]['personality'])
        self.assertEqual(saved['world']['zones'][0]['note'],changed['places'][0]['description'])

    def test_story_and_directed_relations_revalidation_and_persistence(self):
        d,calls=self.live();r=self.store.create(self.token,d['name'],d['overview'])
        changed=deepcopy(r['draft']);changed['initial_story']='アオは町で出発の準備をしている。\nレンは森で待っている。'
        changed['people'][0]['relations']=[{'person':'レン','label':'幼なじみ','affinity':0.25,'awareness':0.9}]
        changed['people'][1]['relations']=[{'person':'アオ','label':'警戒','affinity':-0.1,'awareness':0.6}]
        updated=self.store.update(self.token,r['revision'],changed)
        self.assertEqual(calls,['generate','review','review']);self.assertNotEqual(updated['revision'],r['revision'])
        bad_cases=[]
        for key,value in [('affinity',1.1),('awareness',-0.1),('label','x'*121),('person','アオ')]:
            bad=deepcopy(changed);bad['people'][0]['relations'][0][key]=value;bad_cases.append(bad)
        bad=deepcopy(changed);bad['initial_story']='x'*8001;bad_cases.append(bad)
        bad=deepcopy(changed);bad['people'][0]['relations']*=2;bad_cases.append(bad)
        for bad in bad_cases:
            with self.subTest(draft=bad),self.assertRaises(ConfigError):self.store.update(self.token,updated['revision'],bad)
        self.assertEqual(calls,['generate','review','review']);self.assertEqual(self.store.get(self.token)['revision'],updated['revision'])
        self.store.adopt(self.token,updated['revision'])
        from execution.world_editor import snapshot
        from execution.library import LibraryStore
        saved=snapshot(LibraryStore(self.repo),updated['world_id'])
        self.assertEqual(saved['intro'],changed['initial_story'])
        people={p['id']:p for p in saved['people']}
        self.assertEqual(people['アオ']['relations']['レン'],{'label':'幼なじみ','affinity':0.25,'awareness':0.9})
        self.assertEqual(people['レン']['relations']['アオ'],{'label':'警戒','affinity':-0.1,'awareness':0.6})

    def test_relation_edit_cannot_make_a_goal_true_at_start(self):
        d=self.draft([{'type':'befriend','person':'レン','threshold':0.6}],'レンと仲直りする')
        self.store.provider=lambda *_:json.dumps(d,ensure_ascii=False)
        r=self.store.create(self.token,d['name'],d['overview']);self.assertEqual(r['state'],'review')
        changed=deepcopy(r['draft'])
        for i,other in [(0,'レン'),(1,'アオ')]:changed['people'][i]['relations']=[{'person':other,'label':'仲間','affinity':0.7,'awareness':1}]
        with self.assertRaises(ConfigError) as caught:self.store.update(self.token,r['revision'],changed)
        self.assertIn('開始時点',str(caught.exception));self.assertEqual(self.store.get(self.token)['revision'],r['revision'])

    def test_cancellation_during_review_cannot_resurrect_draft(self):
        def cancel(*a,**k):self.store.cancel(self.token);return self.positive(self.response())
        d,calls=self.live(cancel);r=self.store.create(self.token,d['name'],d['overview'])
        self.assertEqual(r['state'],'cancelled');self.assertFalse((self.repo/'projects').exists())

    def test_item_holder_edit_cannot_make_acquire_goal_true_at_start(self):
        d=self.draft([{'type':'acquire','item':'鍵'}],'鍵を手に入れる')
        d['items']=[{'name':'鍵','holder':'レン'}]
        self.store.provider=lambda *_:json.dumps(d,ensure_ascii=False)
        r=self.store.create(self.token,d['name'],d['overview']);self.assertEqual(r['state'],'review')
        changed=deepcopy(r['draft']);changed['items'][0]['holder']='アオ'
        with self.assertRaises(ConfigError) as caught:self.store.update(self.token,r['revision'],changed)
        self.assertIn('開始時点',str(caught.exception));self.assertEqual(self.store.get(self.token)['revision'],r['revision'])

    def test_missing_receipt_and_changed_receipt_cannot_be_adopted(self):
        d,_=self.live();r=self.store.create(self.token,d['name'],d['overview'])
        r['alignment']['revision']='wrong';atomic_json(self.store._path(self.token),r)
        with self.assertRaises(ConfigError):self.store.adopt(self.token,r['revision'])

    def test_invalid_draft_can_be_repaired_but_not_adopted_without_review(self):
        d,calls=self.live();d['people'][0]['goal']['description']='レンと再会し、仲直りする'
        d['people'][0]['goal']['conditions']=[{'type':'befriend','person':'レン','threshold':0.6}]
        r=self.store.create(self.token,d['name'],d['overview'])
        self.assertEqual(r['state'],'invalid');self.assertTrue(r['edit_revision'])
        with self.assertRaises(ConfigError):self.store.adopt(self.token,r['edit_revision'])
        repaired=deepcopy(r['draft']);repaired['people'][0]['goal']['conditions'].append({'type':'meet','person':'レン'})
        updated=self.store.update(self.token,r['edit_revision'],repaired)
        self.assertEqual(updated['state'],'review');self.assertEqual(calls,['generate','review'])
        self.assertNotIn('edit_revision',updated)
        with self.assertRaises(ConfigError):self.store.update(self.token,r['edit_revision'],repaired)
        self.assertEqual(self.store.adopt(self.token,updated['revision'])['state'],'adopted')

    def test_unknown_review_retains_draft_for_explicit_recheck(self):
        def timeout(*a,**k):raise TimeoutError()
        d,calls=self.live(timeout);r=self.store.create(self.token,d['name'],d['overview'])
        self.assertEqual(r['state'],'unknown');self.assertTrue(r['edit_revision'])
        self.store.provider.review=lambda *a,**k:self.positive(d)
        updated=self.store.update(self.token,r['edit_revision'],r['draft'])
        self.assertEqual(updated['state'],'review');self.assertEqual(calls,['generate','review'])
        self.assertEqual(updated['alignment']['revision'],updated['revision'])

    def test_repair_respects_original_mode_and_requires_live_reviewer(self):
        d,calls=self.live();r=self.store.create(self.token,d['name'],d['overview'])
        self.store.mode='waiting';self.store.provider=None
        with self.assertRaises(ConfigError):self.store.update(self.token,r['revision'],r['draft'])
        self.assertEqual(self.store.get(self.token)['revision'],r['revision'])
        r['mode']='mock';atomic_json(self.store._path(self.token),r)
        self.store.mode='live'
        self.assertIsNone(self.store.update(self.token,r['revision'],r['draft'])['alignment'])

    def test_cancelling_during_repair_discards_review_result(self):
        d,calls=self.live();r=self.store.create(self.token,d['name'],d['overview'])
        def cancel(*a,**k):self.store.cancel(self.token);return self.positive(d)
        self.store.provider.review=cancel
        with self.assertRaises(ConfigError):self.store.update(self.token,r['revision'],r['draft'])
        self.assertEqual(self.store.get(self.token)['state'],'cancelled')
        self.assertFalse((self.repo/'projects').exists())

    def test_stale_receipt_requires_revalidation_and_guide_needs_other_person(self):
        d,calls=self.live();r=self.store.create(self.token,d['name'],d['overview'])
        r['alignment']['check_version']=0;atomic_json(self.store._path(self.token),r)
        self.assertTrue(self.store.get(self.token)['needs_revalidation'])
        with self.assertRaises(ConfigError):self.store.adopt(self.token,r['revision'])
        updated=self.store.update(self.token,r['revision'],r['draft'])
        self.assertFalse(self.store.get(self.token)['needs_revalidation'])
        guide=self.draft([{'type':'reach','place':'森'}],'レンを森に案内する')
        with self.assertRaises(ConfigError):validate(self.repo,guide)
        guide['people'][0]['goal']['conditions'].append({'type':'meet','person':'レン'})
        with self.assertRaises(ConfigError):validate(self.repo,guide)
        guide['people'][0]['goal']['description']='レンと森に到着する'
        validate(self.repo,guide)

    def test_escorting_person_is_not_item_delivery(self):
        d=self.draft([{'type':'reach','place':'森'},{'type':'meet','person':'レン'}],'レンを森まで送り届ける')
        with self.assertRaises(ConfigError):validate(self.repo,d)
        d['people'][0]['goal']['conditions']=[]
        validate(self.repo,d)  # Preserve escorting as narrative; never require a cargo item.
        d['people'][0]['goal']['conditions']=[{'type':'reach','place':'森'},{'type':'meet','person':'レン'}]
        d['people'][0]['goal']['description']='レンを森まで送り届けて、鍵を届ける'
        with self.assertRaises(ConfigError):validate(self.repo,d)
        d['people'][0]['goal']['description']='レンを森まで送り届ける'
        d['people'][0]['goal']['conditions']=[{'type':'meet','person':'レン'}]
        with self.assertRaises(ConfigError):validate(self.repo,d)

    def test_review_obeys_remaining_wall_budget_without_sending(self):
        settings=self.root/'settings.json'
        settings.write_text(json.dumps({'output':{'default_backend':'ollama','ollama':{'model':'test'}}}),encoding='utf8')
        with patch('execution.generation.transport') as transport:
            with self.assertRaises(ConfigError):existing_provider(settings).review('x','y',self.response(),elapsed=100000)
        transport.assert_not_called()

    def test_local_time_profile_is_bounded_and_does_not_rewrite_settings(self):
        settings=self.root/'settings.json'
        settings.write_text(json.dumps({'output':{'default_backend':'ollama','ollama':{'model':'test',
            'limits':{'max_calls':1,'call_timeout_seconds':300,'wall_seconds':360,'max_saved_response_bytes':64000}}}}),encoding='utf8')
        before=settings.read_bytes();d=self.response()
        from execution.generation import Response
        raw=json.dumps({'message':{'content':json.dumps(self.positive(d))},'done':True,'done_reason':'stop'}).encode()
        with patch('execution.generation.transport',return_value=Response(raw)) as transport:
            existing_provider(settings,time_budget=(900,1500)).review('x','y',d,elapsed=1400)
        request=transport.call_args.args[0]
        self.assertEqual(request['limits']['call_timeout_seconds'],100)
        self.assertEqual(settings.read_bytes(),before)

if __name__=='__main__':unittest.main()
