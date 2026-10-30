"""Character-owned values, legacy replay, reference integrity and save recovery."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import json
import shutil
import tempfile
import unittest

from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from execution import world_editor, person_values
from execution.library import LibraryStore
from execution.provenance import ConfigError
from gapengine.deliberation_episode import load_episode
from gapengine.response_episode import load_response

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ('reform_values', 'rescue_values', 'secret_values', 'village_response',
            'ship_response', 'reform_response', 'reform_implementation')
LEGACY = ROOT/'tests/fixtures/legacy_concerns'


class PersonValueTests(unittest.TestCase):
    def test_malformed_personal_values_fail_at_input(self):
        person = json.loads(next((ROOT/'projects/village_response/subjects').glob('*.yaml')).read_text(encoding='utf8'))
        for invalid in (None, {}, [{'condition':'a','priority':True,'required':False}],
                        [{'condition':'a','priority':1,'required':1}],
                        [{'condition':'a','priority':11,'required':False}],
                        [{'condition':'a','priority':1,'required':False}]*2):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Subject.from_definition({**person, 'commitments':invalid})

    def test_personal_refs_empty_values_and_conflicts(self):
        files, _ = world_editor._read(LibraryStore(ROOT), 'village_response')
        hero = next(p for r,p in files.items() if r!='world.yaml' and p['id']=='桃太郎')
        def bind(raw):
            w=World(raw['world.yaml'],ROOT/'projects/village_response/world.yaml')
            w.bind_subjects({p['id']:Subject.from_definition(p) for r,p in raw.items() if r!='world.yaml'})
            return w
        hero['commitments']=[]
        w=bind(files)
        self.assertEqual(w.concerns.commitments_for(w,'桃太郎'),[])
        self.assertIn('no_commitment',w.responses.options(w,w.subjects['桃太郎'],1)[0]['excluded'])
        hero['commitments']=[{'condition':'missing','priority':1,'required':False}]
        with self.assertRaises(ValueError):bind(files)
        hero['commitments']=[]
        files['world.yaml'].pop('responses');files['world.yaml'].pop('concerns')
        for rel,person in files.items():
            if rel!='world.yaml':person['commitments']=[]
        bind(files)  # An explicit empty list is valid even without a registry.
        hero['commitments']=[{'condition':'village_safe','priority':1,'required':False}]
        with self.assertRaises(ValueError):bind(files)
        legacy_files = {'world.yaml':json.loads((LEGACY/'village_response/world.yaml').read_text(encoding='utf8')),
                        **{'subjects/'+p.name:json.loads(p.read_text(encoding='utf8')) for p in (LEGACY/'village_response/subjects').glob('*.yaml')}}
        personal=next(p for r,p in legacy_files.items() if r!='world.yaml' and p['id']=='桃太郎')
        personal['commitments']=deepcopy(legacy_files['world.yaml']['concerns']['commitments']['桃太郎'])
        bind(legacy_files)  # Identical dual data is accepted during a transition.
        personal['commitments']=[]
        with self.assertRaises(ValueError):bind(legacy_files)

    def test_same_seeds_replay_identical_decisions_in_seven_domains(self):
        with tempfile.TemporaryDirectory() as temp:
            for project in PROJECTS:
                loader=load_response if 'response' in project or project=='reform_implementation' else load_episode
                for seed in range(8):
                    runs=[]
                    for label,base in [('legacy',LEGACY),('personal',ROOT/'projects')]:
                        w,p=loader(base/project/'world.yaml')
                        sim=Simulation(seed,w,p,Path(temp)/project/str(seed)/label,record_explanations=True)
                        rows=[json.loads(line) for line in sim.run().read_text(encoding='utf8').splitlines()]
                        runs.append(([r for r in rows if r.get('kind') in ('decision','snapshot')],
                                     w.concerns.world_report(w)))
                    with self.subTest(project=project,seed=seed):self.assertEqual(*runs)


class PersonValuesSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name);self.base=self.repo/'projects/village_response'
        shutil.copytree(LEGACY/'village_response',self.base)
        self.store=LibraryStore(self.repo)
        self.current=world_editor.snapshot(self.store,'village_response')

    def bytes(self):return {p.relative_to(self.base).as_posix():p.read_bytes() for p in self.base.rglob('*.yaml')}

    def save(self,values=None,**kw):
        return world_editor.save(self.store,'village_response',{'revision':self.current['revision'],
            'operation':kw.get('operation','person-commitments'),'target':'桃太郎',
            'values':values if values is not None else {'commitments':[{'condition':'village_safe','priority':7,'required':False}]}})

    def test_read_and_invalid_input_never_migrate(self):
        before=self.bytes();world_editor.snapshot(self.store,'village_response')
        self.assertEqual(before,self.bytes());self.assertFalse((self.base/person_values.JOURNAL).exists())
        with self.assertRaises(ConfigError):self.save({'commitments':[{'condition':'absent','priority':2,'required':False}]})
        self.assertEqual(before,self.bytes())

    def test_save_migrates_all_old_owners_and_subsequent_edit_only_touches_person(self):
        before=self.bytes();result=self.save()
        self.assertEqual(set(result['world']['concerns']),{'version','conditions'})
        self.assertEqual(result['world']['concerns']['version'],2)
        for p in result['people']:
            old=self.current['world']['concerns']['commitments'].get(p['id'])
            if old is not None and p['id']!='桃太郎':self.assertEqual(p['commitments'],old)
        with self.assertRaises(ConfigError) as caught:self.save()
        self.assertEqual(caught.exception.code,'conflict')
        self.current=result;before=self.bytes()
        result=self.save({'commitments':[]})
        changed=[rel for rel,raw in self.bytes().items() if raw!=before[rel]]
        self.assertEqual(len(changed),1);self.assertTrue(changed[0].startswith('subjects/'))
        w,p=load_response(self.base/'world.yaml')
        self.assertEqual(w.concerns.commitments_for(w,'桃太郎'),[])
        self.assertIn('no_commitment',w.responses.options(w,p['桃太郎'],1)[0]['excluded'])

    def test_removing_a_referenced_condition_is_rejected_and_check_never_writes(self):
        before=self.bytes();definition={'version':2,'conditions':deepcopy(self.current['world']['concerns']['conditions'])}
        result=self.save({'definition':definition},operation='concerns-check')
        self.assertTrue(result['valid']);self.assertEqual(before,self.bytes())
        with self.assertRaises(ConfigError):self.save({'definition':None},operation='concerns')
        self.assertEqual(before,self.bytes())

    def test_saved_person_values_change_ordinary_action_without_changing_world(self):
        import yaml
        self.current=self.save()
        path=self.base/'world.yaml'
        raw=yaml.safe_load(path.read_text(encoding='utf8'))
        raw['responses']['actions'][0]['actors']=['桃太郎']
        path.write_text(yaml.safe_dump(raw,allow_unicode=True,sort_keys=False),encoding='utf8')
        self.current=world_editor.snapshot(self.store,'village_response')
        original_world=path.read_bytes()
        outcomes=[]
        for label in ('valued','indifferent'):
            if label=='indifferent':self.current=self.save({'commitments':[]})
            w,p=load_response(path)
            sim=Simulation(0,w,p,self.repo/label,record_explanations=True)
            rows=[json.loads(line) for line in sim.run().read_text(encoding='utf8').splitlines()]
            actions=[r for r in rows if r.get('kind')=='decision' and r['subject']=='桃太郎']
            self.assertTrue(all('forced' not in r for r in actions))
            outcomes.append((any(r['verb']=='respond' and r['result']=='responded' for r in actions),w.responses.state['protected']))
            self.assertEqual(path.read_bytes(),original_world)
        self.assertEqual(outcomes,[(True,'yes'),(False,'no')])

    def test_partial_write_failure_rolls_back_exact_original_bytes(self):
        before=self.bytes();original=person_values._replace;calls=0
        def fail_once(path,raw):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('injected second replacement failure')
            original(path,raw)
        with patch.object(person_values,'_replace',fail_once),self.assertRaises(OSError):self.save()
        self.assertEqual(before,self.bytes());self.assertFalse((self.base/person_values.JOURNAL).exists())

    def test_interrupted_save_is_not_displayed_and_next_save_recovers(self):
        before=self.bytes();original=person_values._replace;calls=0
        def interrupt_once(path,raw):
            nonlocal calls
            calls+=1
            if calls==2:raise KeyboardInterrupt('simulated process stop')
            original(path,raw)
        with patch.object(person_values,'_replace',interrupt_once),self.assertRaises(KeyboardInterrupt):self.save()
        self.assertTrue((self.base/person_values.JOURNAL).exists())
        interrupted=self.bytes()
        with self.assertRaises(ConfigError):world_editor.snapshot(self.store,'village_response')
        self.assertEqual(interrupted,self.bytes())
        self.save()  # Roll back prepared bytes, then validate the original revision and save.
        self.assertFalse((self.base/person_values.JOURNAL).exists())
        self.assertEqual(load_response(self.base/'world.yaml')[1]['桃太郎'].commitments[0]['priority'],7)

    def test_interrupted_save_never_overwrites_an_external_edit(self):
        original=person_values._replace;calls=0
        def interrupt_once(path,raw):
            nonlocal calls
            calls+=1
            if calls==2:raise KeyboardInterrupt()
            original(path,raw)
        with patch.object(person_values,'_replace',interrupt_once),self.assertRaises(KeyboardInterrupt):self.save()
        path=self.base/'world.yaml';path.write_bytes(path.read_bytes()+b'\n# outside edit\n');edited=path.read_bytes()
        with self.assertRaises(ConfigError):self.save()
        self.assertEqual(path.read_bytes(),edited)

    def test_committed_interruption_keeps_saved_values_and_rejects_old_revision(self):
        original=person_values.atomic_json
        def stop_after_commit(path,data):
            original(path,data)
            if data['phase']=='committed':raise KeyboardInterrupt()
        with patch.object(person_values,'atomic_json',stop_after_commit),self.assertRaises(KeyboardInterrupt):self.save()
        committed=self.bytes()
        with self.assertRaises(ConfigError) as caught:self.save()
        self.assertEqual(caught.exception.code,'conflict')
        self.assertEqual(committed,self.bytes())
        self.assertFalse((self.base/person_values.JOURNAL).exists())
        self.assertEqual(load_response(self.base/'world.yaml')[1]['桃太郎'].commitments[0]['priority'],7)


if __name__=='__main__':unittest.main()
