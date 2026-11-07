"""New serialized format and unified action workspace; isolated data only."""
from pathlib import Path
from copy import deepcopy
import hashlib,json,shutil,tempfile,unittest,yaml
from engine.world import World
from engine.subject import Subject
from engine.world_format import validate,initialize
from engine.actions import Action,candidates
from engine.verbs import VerbEngine
from execution.library import LibraryStore
from execution.provenance import ConfigError
from execution import action_usage_settings as settings
from execution.world_editor import snapshot,save as edit_world
ROOT=Path(__file__).resolve().parents[1]
WORLD='momotaro_plus3_new'
class NewWorldFormatTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.repo=self.root/'repo'
  for sub in ['projects/'+WORLD,'templates/momotaro_plus3','templates/basic']:
   shutil.copytree(ROOT/sub,self.repo/sub)
  self.store=LibraryStore(self.repo);self.project=self.repo/'projects'/WORLD
 def load(self):
  w=World.from_yaml(self.project/'world.yaml')
  people={p.id:p for p in [Subject.from_yaml(p) for p in sorted((self.project/'subjects').glob('*.yaml'))]}
  w.bind_subjects(people);return w,people
 def request(self):
  m=settings.view(self.store,WORLD)
  return {key:deepcopy(m[key]) for key in settings.FIELDS}
 def test_only_current_world_is_active_and_original_stays_on_disk(self):
  self.assertEqual([w['id'] for w in LibraryStore(ROOT).worlds()],[WORLD])
  self.assertTrue((ROOT/'projects/momotaro_plus3/world.yaml').exists())
 def test_serialized_legacy_world_is_rejected(self):
  with self.assertRaisesRegex(ValueError,'旧世界'):
   World.from_yaml(ROOT/'projects/momotaro_plus3/world.yaml')
  with self.assertRaisesRegex(ConfigError,'旧世界'):
   LibraryStore(ROOT).read('world','momotaro_plus3','world.yaml')
  d=yaml.safe_load((self.project/'world.yaml').read_bytes());d['story_genres']=[]
  with self.assertRaisesRegex(ValueError,'旧ジャンル'):validate(d)
 def test_old_editor_operation_and_raw_file_downgrade_cannot_write(self):
  before=(self.project/'world.yaml').read_bytes();model=snapshot(self.store,WORLD)
  with self.assertRaises(ConfigError):
   edit_world(self.store,WORLD,{'revision':model['revision'],'operation':'story-genres','target':None,'values':{'genres':[]}})
  d=yaml.safe_load(before);d.pop('world_format_version')
  with self.assertRaises(ConfigError):self.store.write('world',WORLD,'world.yaml',yaml.safe_dump(d))
  self.assertEqual((self.project/'world.yaml').read_bytes(),before)
 def test_saved_configuration_cannot_capture_a_retired_world(self):
  from execution.configs import ConfigStore
  repo=ROOT
  store=ConfigStore(repo,self.root/'control',self.root/'runs')
  with self.assertRaises(ConfigError):
   store.preview({'label':'Retired','project_id':'momotaro_plus3','template_id':'momotaro_plus3'})
 def test_new_world_creation_uses_one_explicit_policy(self):
  self.store.create_original_world('fresh',name='Fresh')
  d=yaml.safe_load((self.repo/'projects/fresh/world.yaml').read_bytes());validate(d)
  self.assertNotIn('story_genres',d);self.assertNotIn('story_genres_version',d)
  self.assertNotIn('inherited_grants',d['action_usage'])
  self.assertEqual(d['action_usage']['version'],2);self.assertIn('verb:rest',d['action_usage']['enabled'])
 def test_new_draft_compiles_only_current_format(self):
  from execution.world_drafts import compile_draft,mock_response,parse_response
  draft=parse_response(mock_response('Demo','Demo story'))
  _,world,people=compile_draft(draft);validate(world)
  self.assertNotIn('story_genres',world);self.assertTrue(people)
 def test_momotaro_values_and_target_are_retained(self):
  w,people=self.load();d=yaml.safe_load((self.project/'world.yaml').read_bytes())
  self.assertEqual(len(people),15);self.assertEqual(len(d['zones']),6)
  self.assertEqual(len(d['items']),9);self.assertEqual(len(d['facts']),9)
  self.assertEqual(d['target_ending'],['purpose_mvz6xmq3_1'])
  self.assertEqual(len(w.action_usage['enabled']),40)
  self.assertNotIn('verb:start_relationship',w.action_usage['enabled'])
  self.assertTrue(w.mechanics_enabled('budgeted_purchases'));self.assertTrue(w.mechanics_enabled('stamina_management'))
  self.assertEqual(w.stamina['default_action_cost'],1);self.assertFalse(w.stamina['passive_recovery'])
  self.assertNotIn('zero_behavior',w.stamina);self.assertTrue(w.vitality['allow_death'])
  self.assertEqual(d['economy']['currency']['asset'],'小判')
 def test_person_ids_are_preserved_and_the_new_world_keeps_item_ownership(self):
  old = {Subject.from_yaml(path).id for path in (ROOT/'projects/momotaro_plus3/subjects').glob('*.yaml')}
  _,people = self.load()
  self.assertEqual(set(people),old)
  self.assertIn('弟の手紙',people['鬼の弟'].inventory)
 def test_legacy_labor_permission_was_resolved_once(self):
  m=settings.view(self.store,WORLD)
  self.assertEqual(len(m['effective_actors']['verb:labor']),15)
  self.assertEqual(m['default_actors']['verb:start_relationship'],[])
  self.assertNotIn('inherited_grants',yaml.safe_load((self.project/'world.yaml').read_bytes())['action_usage'])
 def test_no_change_preview_has_no_migration(self):
  before={p.relative_to(self.project).as_posix():p.read_bytes() for p in self.project.rglob('*.yaml')}
  r=settings.check(self.store,WORLD,self.request())
  self.assertNotIn('migration',r);self.assertEqual(r['changes'],[])
  self.assertEqual(before,{p.relative_to(self.project).as_posix():p.read_bytes() for p in self.project.rglob('*.yaml')})
 def test_mechanics_and_action_are_independent_but_one_save(self):
  req=self.request();req['enabled'].remove('verb:labor')
  r=settings.check(self.store,WORLD,req)
  self.assertTrue(r['candidate']['mechanics']['budgeted_purchases'])
  req['mechanics']['budgeted_purchases']=False
  r=settings.check(self.store,WORLD,req)
  self.assertEqual({c.get('ref',c.get('setting')) for c in r['changes']},{'verb:labor','budgeted_purchases'})
 def test_save_is_atomic_idempotent_and_preserves_people(self):
  hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.project/'subjects').glob('*.yaml')}
  req=self.request();req['mechanics']['budgeted_purchases']=False
  checked=settings.check(self.store,WORLD,req)
  body={**req,'check_fingerprint':checked['check_fingerprint'],'request_id':'format-test-001'}
  a=settings.save(self.store,WORLD,body);b=settings.save(self.store,WORLD,body)
  self.assertEqual(a['state'],'applied');self.assertEqual(a['saved_revision'],b['saved_revision'])
  self.assertEqual(hashes,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.project/'subjects').glob('*.yaml')})
  self.assertFalse(settings.view(self.store,WORLD)['mechanics']['budgeted_purchases'])
  with self.assertRaises(ConfigError):settings.check(self.store,WORLD,req)
 def test_time_and_actor_permissions_use_same_guard(self):
  req=self.request();req['constraints']['verb:train']={'actors':['桃太郎'],'slots':['昼']}
  r=settings.check(self.store,WORLD,req)
  w=World(r['candidate'],self.project/'world.yaml')
  people={p.id:p for p in [Subject.from_yaml(p) for p in sorted((self.project/'subjects').glob('*.yaml'))]};w.bind_subjects(people)
  from random import Random
  p=people['桃太郎'];zone=p.zone;energy=p.stamina
  result=VerbEngine(w,Random(0)).execute(p,Action('train',()),turn=1,day=1)
  self.assertEqual(p.zone,zone);self.assertEqual(p.stamina,energy);self.assertIn('time',str(result))
 def test_required_rest_and_scheduled_actions_cannot_be_disabled(self):
  req=self.request();req['enabled'].remove('verb:rest')
  with self.assertRaises(ConfigError):settings.check(self.store,WORLD,req)
  req=self.request();req['enabled'].remove('verb:move')
  with self.assertRaises(ConfigError):settings.check(self.store,WORLD,req)
 def test_unified_ui_and_metadata_have_one_owner(self):
  from viewer.action_settings import render
  from execution.configs import ConfigStore
  from execution.jobs import JobStore
  jobs=JobStore(ConfigStore(self.repo,self.root/'control',self.root/'runs'))
  html=render(self.store.worlds()[0],self.store,jobs)
  self.assertLess(html.index('data-as-tab="types"'),html.index('data-as-tab="actions"'))
  self.assertIn('行動の種類',html);self.assertIn('行動一覧',html);self.assertIn('data-as-type',html)
  self.assertNotIn('data-settings-category="genres"',html)
  model=snapshot(self.store,WORLD);self.assertIn('mechanic_features',model)
  self.assertNotIn('story_genres',model);self.assertNotIn('story_genre_features',model)
if __name__=='__main__':unittest.main()
