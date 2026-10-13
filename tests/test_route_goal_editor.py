"""AIT-134: actual parser, HTTP, atomic persistence and input protection."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import yaml

from engine.subject import Subject
from engine.world import World
from execution import route_goal_editor as editor
from execution.library import LibraryStore
from execution.provenance import ConfigError
from gapengine.route_goals import resolve_goal
from gapengine.route_paths import survey, _load_world_and_subjects
import test_library as fixtures

ROOT = Path(__file__).resolve().parents[1]


class GoalEditorTests(unittest.TestCase):
    http = fixtures.LibraryHttpBoundaryTests.http
    get = fixtures.LibraryHttpBoundaryTests.get

    def setUp(self):
        fixtures.LibraryHttpBoundaryTests.setUp(self)
        sample = ROOT / 'samples/route_goals/cultural_festival'
        self.project = self.repo / 'projects/festival'
        shutil.copytree(sample, self.project)
        self.template = self.repo / 'templates/festival'
        shutil.copytree(self.repo / 'templates/momotaro', self.template)
        shutil.copyfile(sample / 'action_graph.yaml', self.template / 'action_graph.yaml')
        self.world_path = self.project / 'world.yaml'
        w = yaml.safe_load(self.world_path.read_text())
        w['gapengine'] = {'action_graph':'templates/festival/action_graph.yaml', 'effects':'templates/festival/effects.yaml'}
        w['facts'] = [{'id':'合図'}]
        self.world_path.write_text(yaml.safe_dump(w, allow_unicode=True))
        self.store = LibraryStore(self.repo)
        self.path = self.project / 'route.yaml'
        self.url = '/api/worlds/festival/route-goal'

    def snapshot(self):
        return editor.snapshot(self.store, 'festival')

    def body(self, goal=None):
        return {'revision':self.snapshot()['revision'], 'goal':self.snapshot()['goal'] if goal is None else goal}

    def files(self):
        return {p.relative_to(self.repo).as_posix():p.read_bytes() for p in self.repo.rglob('*.yaml')}

    def test_round_trip_uses_input_serializer_and_preserves_all_fields(self):
        goal = self.snapshot()['goal']
        goal['all'] += [{'predicate':'knows','subject':'実行委員','fact':'合図'}]
        goal['all'][1]['quantity'] = 2
        world, people = _load_world_and_subjects(self.project, self.template)
        for mode in ('any','at','by'):
            with self.subTest(mode=mode):
                raw = {**goal, 'time':{'mode':mode} if mode=='any' else {'mode':mode,'day':1,'slot':'15:00'}}
                parsed = resolve_goal(raw, world, people)
                encoded = parsed.to_input_dict()
                again = resolve_goal(yaml.safe_load(yaml.safe_dump(encoded)), world, people)
                self.assertEqual(parsed, again)
                self.assertNotIn('legacy', encoded)
                self.assertNotIn('value', encoded['all'][0])
                if mode=='any': self.assertEqual(encoded['time'], {'mode':'any'})
        with self.assertRaises(ValueError): resolve_goal(parsed.as_dict(), world, people)

    def test_save_reload_and_direct_yaml_survey_are_equal(self):
        before = self.files()
        goal = self.snapshot()['goal']; goal['time'] = {'mode':'by','day':1,'slot':'15:00'}
        status, result = self.http('POST',self.url,self.body(goal))
        self.assertEqual(status,200,result)
        self.assertEqual(result,self.snapshot())
        self.assertEqual(self.http('GET',self.url,{})[1]['goal'], result['goal'])
        self.assertEqual(yaml.safe_load(self.path.read_text())['goal'], result['goal'])
        self.assertEqual(self.http('GET','/api/worlds/festival/routes',{})[1],survey(self.project,self.template))
        after = self.files()
        self.assertEqual({k:v for k,v in before.items() if k!='projects/festival/route.yaml'},
                         {k:v for k,v in after.items() if k!='projects/festival/route.yaml'})

    def test_at_early_arrival_waits_but_by_succeeds_early(self):
        goal=self.snapshot()['goal']
        at=editor.validate(self.store,'festival',self.body(goal))
        self.assertEqual(at['status'],'found'); self.assertEqual(at['reached']['slot'],'15:00')
        self.assertIn('wait',[step['kind'] for step in at['steps']])
        goal['time']['mode']='by'
        by=editor.validate(self.store,'festival',self.body(goal))
        self.assertEqual(by['status'],'found'); self.assertLess(by['cost'],at['cost'])

    def test_invalid_goals_refuse_without_overwriting(self):
        invalids=[{'all':[]},{'all':[{'predicate':'OR','zone':'体育館'}]},
                  {'all':[{'predicate':'NOT','zone':'体育館'}]}, {'actor':'unknown','all':[{'predicate':'at','zone':'体育館'}]},
                  {'all':[{'predicate':'at','zone':'unknown'}]}, {'all':[{'predicate':'knows','fact':'unknown'}]},
                  {'all':[{'predicate':'holds','item':'unknown'}]}, {'all':[{'predicate':'at','subject':'unknown','zone':'体育館'}]}]
        invalids += [{'all':[{'predicate':'holds','item':'衣装','quantity':q}]} for q in (0,-1,True,1.5,'2',None)]
        invalids += [{**self.snapshot()['goal'],'time':time} for time in ({'mode':'any','slot':'15:00'},
                    {'mode':'at','day':2,'slot':'15:00'}, {'mode':'by','slot':'24:00'}, {'mode':'at','slot':'15:01'}, {'mode':'or'})]
        before=self.path.read_bytes()
        for goal in invalids:
            with self.subTest(goal=goal):
                status,result=self.http('POST',self.url,self.body(goal))
                self.assertEqual(status,422,result); self.assertEqual(result['code'],'invalid_config')
                self.assertEqual(self.path.read_bytes(),before)

    def test_validation_is_read_only_and_preserves_distinct_statuses(self):
        goal=self.snapshot()['goal']; before=self.files()
        self.assertEqual(editor.validate(self.store,'festival',self.body(goal))['status'],'found')
        goal['all'][1]['quantity']=2
        self.assertEqual(editor.validate(self.store,'festival',self.body(goal))['status'],'unreachable')
        cfg=yaml.safe_load(self.path.read_text()); cfg['search']={'max_expansions':1}; self.path.write_text(yaml.safe_dump(cfg,allow_unicode=True))
        self.assertEqual(editor.validate(self.store,'festival',self.body())['status'],'cutoff')
        cfg['search']={'max_expansions':0}; self.path.write_text(yaml.safe_dump(cfg,allow_unicode=True))
        self.assertEqual(editor.validate(self.store,'festival',self.body())['status'],'invalid_config')
        self.path.write_bytes(before['projects/festival/route.yaml'])
        self.assertEqual(before,self.files())

    def test_unsupported_calendar_and_event_are_not_saved(self):
        original=self.world_path.read_bytes(); route=self.path.read_bytes()
        for update in ({'time':{'days':2,'slots':['14:00','15:00']}},
                       {'scheduled_events':[{'day':1,'slot':'15:00','targets':['主役'],'stress_delta':1}]}):
            world=yaml.safe_load(original); world.update(update); self.world_path.write_text(yaml.safe_dump(world,allow_unicode=True))
            result=editor.validate(self.store,'festival',self.body())
            self.assertEqual(result['status'],'unsupported')
            status,payload=self.http('POST',self.url,self.body())
            self.assertEqual(status,422,payload); self.assertEqual(payload['code'],'unsupported')
            self.assertEqual(route,self.path.read_bytes())

    def test_conflict_in_route_subject_world_and_template_preserves_newest(self):
        for path in (self.path,self.world_path,self.project/'subjects/01_lead.yaml',self.template/'rules.yaml'):
            body=self.body(); original=path.read_bytes(); path.write_bytes(original+b'\n# other editor\n')
            newest=self.path.read_bytes()
            status,payload=self.http('POST',self.url,body)
            self.assertEqual(status,409,payload); self.assertEqual(self.path.read_bytes(),newest)
            path.write_bytes(original)

    def test_whole_world_config_precedence_and_template_copy(self):
        self.path.unlink()
        cfg={'search':{'max_expansions':100},'rho':0.3,'note':'keep me'}
        template_path=self.template/'route.yaml'; template_path.write_text(yaml.safe_dump(cfg))
        initial=self.snapshot(); self.assertEqual(initial['source'],'template'); self.assertIsNone(initial['goal'])
        # An unchanged legacy save must not create a world override.
        editor.save(self.store,'festival',{'revision':initial['revision'],'goal':None}); self.assertFalse(self.path.exists())
        goal={'all':[{'predicate':'at','zone':'体育館'}],'time':{'mode':'any'}}
        before=template_path.read_bytes()
        editor.save(self.store,'festival',{'revision':initial['revision'],'goal':goal})
        saved=yaml.safe_load(self.path.read_text()); self.assertEqual(saved['rho'],0.3); self.assertEqual(saved['note'],'keep me')
        self.assertEqual(template_path.read_bytes(),before)
        template_path.write_text('goal: {all: []}\nsearch: {max_expansions: 0}\n')
        self.assertEqual(self.snapshot()['source'],'world')
        self.assertEqual(self.snapshot()['goal']['all'][0]['zone'],'体育館')

    def test_return_to_delivery_preserves_other_settings_and_people(self):
        before={p:p.read_bytes() for p in (self.project/'subjects').glob('*.yaml')}
        cfg=yaml.safe_load(self.path.read_text()); cfg['rho']=0.3; self.path.write_text(yaml.safe_dump(cfg,allow_unicode=True))
        editor.save(self.store,'festival',{'revision':self.snapshot()['revision'],'goal':None})
        self.assertNotIn('goal',yaml.safe_load(self.path.read_text())); self.assertEqual(yaml.safe_load(self.path.read_text())['rho'],0.3)
        for p,raw in before.items():self.assertEqual(p.read_bytes(),raw)

    def test_atomic_failure_keeps_old_complete_yaml_and_cleans_temp(self):
        before=self.path.read_bytes(); body=self.body(); body['goal']['time']['mode']='by'
        with patch('execution.route_goal_editor.os.replace',side_effect=OSError('disk fault')):
            with self.assertRaises(OSError):editor.save(self.store,'festival',body)
        self.assertEqual(self.path.read_bytes(),before); self.assertFalse(list(self.project.glob('.route.yaml-*')))

    def test_duplicate_people_and_symlink_are_explicitly_rejected(self):
        shutil.copyfile(self.project/'subjects/01_lead.yaml',self.project/'subjects/03_duplicate.yaml')
        with self.assertRaises(ConfigError):self.snapshot()
        (self.project/'subjects/03_duplicate.yaml').unlink()
        self.path.unlink(); self.path.symlink_to(self.template/'route.yaml')
        with self.assertRaises(ConfigError):self.snapshot()

    def test_http_boundaries_and_readonly_page(self):
        self.assertEqual(self.http('POST',self.url,self.body(),client_header=False)[0],403)
        status,html=self.get('/worlds/festival/goal'); self.assertEqual(status,200,html)
        self.assertIn('data-goal-editor',html); self.assertIn('route-goal.js',html)
        self.assertEqual(self.get('/static/route-goal.js')[0],200)
        self.assertEqual(self.get('/static/route-goal.css')[0],200)
        self.server.job_store=None
        with patch('viewer.data.ROOT',self.repo):
            status,html=self.get('/worlds/festival/goal'); self.assertEqual(status,200,html)
            self.assertIn('"editable": false',html)
            self.assertEqual(self.http('POST',self.url,self.body())[0],503)

    def test_legacy_serializer_refuses_promotion(self):
        project=self.repo/'projects/momotaro'
        world=World.from_yaml(project/'world.yaml',action_graph_path=self.repo/'templates/momotaro/action_graph.yaml')
        subjects={p.id:p for p in (Subject.from_yaml(p) for p in (project/'subjects').glob('*.yaml'))}
        legacy=resolve_goal(None,world,subjects)
        self.assertTrue(legacy.legacy)
        with self.assertRaises(ValueError):legacy.to_input_dict()

    def test_config_freezes_world_route_and_keeps_existing_config_immutable(self):
        # ConfigStore normal inputs include the world goal without requiring
        # that it affect the GA's personality policy or ending.
        original=self.server.job_store.configs.save({'label':'original','project_id':'momotaro','template_id':'momotaro'},config_id='original')
        p=self.repo/'projects/momotaro/route.yaml'; p.write_text('goal: {all: [{predicate: at, zone: 村}]}\n')
        fresh=self.server.job_store.configs.save({'label':'fresh','project_id':'momotaro','template_id':'momotaro'},config_id='fresh')
        frozen=self.control/'configs/fresh/inputs/projects/momotaro/route.yaml'
        self.assertEqual(p.read_bytes(),frozen.read_bytes())
        p.write_text('goal: {all: []}\n')
        self.assertEqual(self.server.job_store.configs.get('original'),original)
        self.assertEqual(self.server.job_store.configs.get('fresh'),fresh)
