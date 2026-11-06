"""Opt-in usage: no legacy migration on read, exact guards, atomic receipts."""
from copy import deepcopy
import http.client
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import yaml

from engine.action_usage import current_policy, reference, parse
from engine.actions import Action, candidates
from engine.verbs import VerbEngine
from engine.world import World
from execution import action_usage_settings as settings, world_editor
from execution.library import LibraryStore
from execution.provenance import ConfigError
from test_engine import FixedRandom
from engine.subject import Subject
import shutil
ROOT=Path(__file__).resolve().parents[1]
def load_fixture():
    project=ROOT/"projects/momotaro_plus3_new"
    world=World.from_yaml(project/"world.yaml")
    people={p.id:p for p in [Subject.from_yaml(f) for f in sorted((project/"subjects").glob("*.yaml"))]}
    world.bind_subjects(people);return world,people
def install_fixture(repo):
    shutil.copytree(ROOT/"projects/momotaro_plus3_new",repo/"projects/modern")
    for name in ["momotaro_plus3","basic"]:
        shutil.copytree(ROOT/"templates"/name,repo/"templates"/name,dirs_exist_ok=True)
import test_library as fixtures


def adopt(world, remove=()):
    world.action_usage,world.mechanics=current_policy(world)
    world.action_usage['enabled']=[r for r in world.action_usage['enabled'] if r not in remove]
    return world


class UsageEngineTests(unittest.TestCase):
    def test_world_on_does_not_grant_actor_permission_or_bypass_phase(self):
        w,p=load_fixture();a=p['桃太郎'];adopt(w);w.action_usage['constraints']['verb:move']['actors']=None;a.verbs=[]
        self.assertTrue(w.action_usage_allows('move',('道中',)))
        before=deepcopy(a.__dict__)
        result=VerbEngine(w,FixedRandom([0])).execute(a,Action('move',('道中',)),turn=1,day=1)
        self.assertEqual(result[1]['reason'],'actor_action_unavailable');self.assertEqual(a.__dict__,before)

    def test_direct_disabled_has_no_effect_or_stamina_charge_even_at_zero(self):
        for zero in (False,True):
            w,p=load_fixture();a=p['桃太郎'];adopt(w,['verb:move']);a.stamina=0 if zero else a.stamina
            before=deepcopy((a.__dict__,w.turn if hasattr(w,'turn') else None))
            result,details,markers=VerbEngine(w,FixedRandom([0])).execute(a,Action('move',('道中',)),turn=1,day=1)
            self.assertEqual((result,details['reason'],markers),('invalid','action_disabled',[]))
            self.assertEqual(a.__dict__,before[0])

    def test_recipe_leaf_and_parent_guard_and_candidates(self):
        w,p=load_fixture();a=p['桃太郎'];a.zone='海';a.knowledge.add('造船術');a.inventory.update({'木材':2,'縄':1})
        adopt(w,['recipe:船'])
        self.assertFalse(w.action_usage_allows('craft'));self.assertFalse(w.action_usage_allows('craft',('船',)))
        options=candidates(a,w,SimpleNamespace(turn=1,day=1))
        self.assertFalse(any(action.verb=='craft' for action,_ in options))
        self.assertEqual(VerbEngine(w,FixedRandom([0])).execute(a,Action('craft',('船',)),turn=1,day=1)[1]['reason'],'action_disabled')
        self.assertEqual((a.inventory['木材'],a.inventory['縄']),(2,1))

    def test_only_rest_enabled_produces_only_rest_in_seeded_simulations(self):
        from engine.sim import Simulation
        for seed in (0,17):
            w,p=load_fixture();adopt(w);w.action_usage['enabled']=['verb:rest']
            with tempfile.TemporaryDirectory() as temp:
                path=Simulation(seed,w,p,Path(temp),record_explanations=True).run()
                decisions=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if json.loads(line).get('kind')=='decision']
            self.assertTrue(decisions);self.assertEqual({row['verb'] for row in decisions},{'rest'})

    def test_required_unknown_duplicate_and_scheduled_references_are_validated(self):
        w,_=load_fixture();policy,mechanics=current_policy(w)
        for refs in ([],policy['enabled']+['verb:future'],policy['enabled']+['verb:rest']):
            with self.assertRaises(ValueError):parse(w,{**policy,'enabled':refs},mechanics)
        w.scheduled_events=[{'id':'departure','force_action':{'verb':'move','args':['海']}}]
        with self.assertRaisesRegex(ValueError,'departure'):parse(w,{**policy,'enabled':['verb:rest']},mechanics)

    def test_planner_and_timed_response_cannot_use_disabled_leaf(self):
        from test_action_timing import configuration
        from test_goal_planner import load
        raw,people=configuration();w,p,_=load(raw,people);policy,mechanics=current_policy(w)
        policy['enabled'].remove('response:send_beacon');raw.update(action_usage=policy,mechanics=mechanics)
        w,p,_=load(raw,people);a=p['通信士']
        with self.assertRaisesRegex(ValueError,'action_disabled'):w.action_timing._spec(w,a,Action('respond',('send_beacon',)),1)
        plan=w.planner.search(w,a,'beacon_sent',1)
        self.assertFalse(any(s['verb']=='respond' and s['args']==['send_beacon'] for s in plan.get('steps',[])))
        result=w.responses.execute(w,a,Action('respond',('send_beacon',)),1,1)
        self.assertEqual(result[1]['reason'],'action_disabled')

    def test_legacy_absence_remains_no_op_and_migration_preserves_grants_features(self):
        w,p=load_fixture();a=p['桃太郎'];before=[(action.to_dict() if hasattr(action,'to_dict') else (action.verb,action.args),weight) for action,weight in candidates(a,w,SimpleNamespace(turn=1,day=1))]
        self.assertFalse(w.action_usage_allows('not_registered',()))
        grants=w.global_action_grants();features={f:w.mechanics_enabled(f) for f in ('stamina_management','budgeted_purchases')}
        adopt(w)
        after=[(action.to_dict() if hasattr(action,'to_dict') else (action.verb,action.args),weight) for action,weight in candidates(a,w,SimpleNamespace(turn=1,day=1))]
        self.assertEqual(before,after);self.assertEqual(grants,w.global_action_grants())
        self.assertEqual(features,{f:w.mechanics_enabled(f) for f in features})


class UsageStorageTests(unittest.TestCase):
    def setUp(self):
        fixtures.LibraryStoreTests.setUp(self);install_fixture(self.repo)

    def draft(self):
        model=settings.view(self.store,'modern')
        return {k:deepcopy(model[k]) for k in settings.FIELDS}

    def submit(self,draft=None,ident='save-request-1'):
        draft=draft or self.draft();draft['mechanics']['budgeted_purchases']=False if draft==self.draft() else draft['mechanics']['budgeted_purchases'];check=settings.check(self.store,'modern',draft)
        return {**draft,'request_id':ident,'check_fingerprint':check['check_fingerprint']}

    def test_read_check_cancel_does_not_write_and_save_preserves_people_history_and_time(self):
        project=self.repo/'projects/modern';before={p:p.read_bytes() for p in project.rglob('*') if p.is_file()}
        body=self.submit();self.assertNotIn('legacy',settings.view(self.store,'modern'))
        self.assertEqual(before,{p:p.read_bytes() for p in project.rglob('*') if p.is_file()})
        original=world_editor.snapshot(self.store,'modern')['world'];body['enabled'].remove('verb:investigate');body=self.submit({k:body[k] for k in settings.FIELDS})
        result=settings.save(self.store,'modern',body)
        self.assertEqual(result['state'],'applied');self.assertTrue(result['matches_current']);self.assertNotIn('legacy',result['model'])
        saved=world_editor.snapshot(self.store,'modern')['world']
        for key in original:
            if key not in ('vitality','action_usage','mechanics'):self.assertEqual(original[key],saved[key],key)
        for p,raw in before.items():
            if p.name!='world.yaml':self.assertEqual(raw,p.read_bytes())
        receipt=json.loads((project/settings.FOLDER/'save-request-1.preimage.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['world_text'].encode('utf-8'),before[project/'world.yaml'])
        self.assertEqual(settings.save(self.store,'modern',body),result)
        self.assertEqual(settings.status(self.store,'modern','save-request-1')['state'],'applied')

    def test_stale_revision_fingerprint_and_unknown_inputs_reject_without_mutation(self):
        body=self.submit();path=self.repo/'projects/modern/world.yaml';before=path.read_bytes()
        for changed in ({'revision':'stale'},{'check_fingerprint':'wrong'},{'enabled':['verb:rest','recipe:unknown']},{'inherited_grants':['fight']}):
            with self.assertRaises(ConfigError):settings.save(self.store,'modern',{**body,**changed})
            self.assertEqual(path.read_bytes(),before)

    def test_backup_failure_never_writes_world_and_lost_reply_can_be_reconciled(self):
        body=self.submit();path=self.repo/'projects/modern/world.yaml';before=path.read_bytes()
        with patch.object(settings,'atomic_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):settings.save(self.store,'modern',body)
        self.assertEqual(path.read_bytes(),before)
        original=settings._existing
        calls=0
        def lose_reply(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('response lost after write')
            return original(*args,**kwargs)
        with patch.object(settings,'_existing',side_effect=lose_reply):
            with self.assertRaises(OSError):settings.save(self.store,'modern',body)
        self.assertNotEqual(path.read_bytes(),before)
        result=settings.status(self.store,'modern','save-request-1');self.assertEqual(result['state'],'applied')
        self.assertTrue(result['matches_current']);self.assertEqual(settings.save(self.store,'modern',body)['state'],'applied')

    def test_changed_dependency_requires_recheck_and_failed_world_write_reports_not_applied(self):
        body=self.submit();path=self.repo/'projects/modern/world.yaml';before=path.read_bytes()
        with patch.object(settings,'dependency',return_value='new-code'):
            with self.assertRaises(ConfigError):settings.save(self.store,'modern',body)
        self.assertEqual(path.read_bytes(),before)
        with patch.object(self.store,'_write_file',side_effect=OSError('write failed')):
            with self.assertRaises(OSError):settings.save(self.store,'modern',body)
        self.assertEqual(settings.status(self.store,'modern','save-request-1')['state'],'not_applied')
        self.assertEqual(path.read_bytes(),before)

    def test_world_mechanics_and_usage_share_one_revision_without_person_grants(self):
        body=self.draft();body['mechanics']['stamina_management']=False;body['mechanics']['budgeted_purchases']=True;body['world_rules']['vitality']['allow_death']=False
        body['enabled']=[r for r in body['enabled'] if r!='verb:investigate'];result=settings.save(self.store,'modern',self.submit(body))
        model=result['model'];self.assertFalse(model['mechanics']['stamina_management']);self.assertTrue(model['mechanics']['budgeted_purchases']);self.assertFalse(model['world_rules']['vitality']['allow_death']);self.assertNotIn('verb:investigate',model['enabled'])
        current=world_editor.snapshot(self.store,'modern')
        self.assertIn('budgeted_purchases',current['mechanic_features']);self.assertNotIn('stamina_management',current['mechanic_features'])
        with self.assertRaises(ConfigError):world_editor.save(self.store,'modern',{'revision':current['revision'],'operation':'story-genres','target':None,'values':{'genres':[]}})


class UsageHttpTests(unittest.TestCase):
    def setUp(self):
        fixtures.LibraryHttpBoundaryTests.setUp(self);install_fixture(self.repo)
    http=fixtures.LibraryHttpBoundaryTests.http

    def test_boundary_check_save_status_and_readonly(self):
        store=LibraryStore(self.repo);model=settings.view(store,'modern');body={k:model[k] for k in settings.FIELDS};url='/api/worlds/modern/action-usage'
        import re
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port);conn.request('GET','/worlds/modern?view=actions');response=conn.getresponse();page=response.read().decode('utf-8');self.assertEqual(response.status,200);conn.close()
        initial=json.loads(re.search(r'id="as-data">(.*?)</script>',page,re.S).group(1))
        self.assertIn('mechanics',initial)
        self.assertIn('enabled',initial)
        self.assertEqual(self.http('POST',url+'/check',body,client_header=False)[0],403)
        status,result=self.http('POST',url+'/check',body);self.assertEqual(status,200,result);self.assertNotIn('candidate',result)
        status,result=self.http('POST',url+'/save',{**body,'request_id':'http-save-1','check_fingerprint':result['check_fingerprint']});self.assertEqual(status,200,result);self.assertEqual(result['state'],'applied')
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port);conn.request('GET',url+'/status?request_id=http-save-1');response=conn.getresponse();self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read())['state'],'applied');conn.close()
        self.server.job_store=None
        self.assertEqual(self.http('POST',url+'/save',body)[0],503)
