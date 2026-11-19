"""Explicit creation input -> draft -> publish -> UI -> frozen input -> runtime.

Fixed JSON provider, CPU-only native execution, no model or original world writes.
"""
from copy import deepcopy
import json,unittest,yaml
from types import SimpleNamespace
from execution.world_drafts import compile_draft,validate,mock_response,parse_response,existing_provider
from unittest.mock import patch
from execution.provenance import ConfigError
from execution.library import LibraryStore
from execution.configs import ConfigStore
from execution.world_editor import snapshot
from gapengine.route_paths import _load_world_and_subjects
from engine.actions import Action,candidates
from engine.verbs import VerbEngine
from engine.sim import Simulation
from gapengine.knowledge_text import _goal_line
import test_world_drafts as fixture
from test_engine import FixedRandom


class DraftExecutionTests(unittest.TestCase):
    setUp=fixture.DraftTests.setUp

    def proposal(self):
        d=parse_response(mock_response('期間のある世界','村と森で20日間を過ごす'))
        d['people'][0]['goal']={'description':'20日目まで生存する','conditions':[{'type':'survive','day':20}]}
        d['people'][0]['commitments']=[{'condition':'reach_forest','priority':3,'required':False}]
        d['facts']=[{'name':'川の警報','description':'川の状態の通知','place':'森'}]
        d['settings']={'version':1,'time':{'days':20,'slots':['朝','昼']},
            'scheduled_events':[{'id':'warning','day':2,'slot':'朝','targets':['アオ'],'grants_fact':'川の警報','stress_delta':2}],
            'action_usage':{'version':2,'engine_contract':'normal_turn_v2','catalog_version':1,'enabled':['verb:move','verb:rest'],
                            'constraints':{'verb:move':{'actors':['アオ'],'slots':['昼']}}},
            'mechanics':{'version':1,'stamina_management':False,'budgeted_purchases':False},
            'concerns':{'version':2,'conditions':[{'id':'reach_forest','label':'森へ到着する','mode':'achieve','visibility':'public','test':{'kind':'zone','actor':'アオ','equals':'森'}}]}}
        return d

    def adopt(self,d):
        self.store.provider=lambda *_:json.dumps(d,ensure_ascii=False)
        r=self.store.create(self.token,d['name'],d['overview'])
        self.assertEqual(r['state'],'review',r.get('errors'))
        self.store.adopt(self.token,r['revision'])
        project=self.repo/'projects'/r['world_id']
        w,p=_load_world_and_subjects(project,self.repo/'templates/basic');w.bind_subjects(p)
        return r,project,w,p

    def test_complete_round_trip_and_native_effects(self):
        d=self.proposal();r,project,w,p=self.adopt(d)
        ui=snapshot(LibraryStore(self.repo),r['world_id'])
        self.assertEqual(ui['world']['time'],d['settings']['time'])
        self.assertEqual(ui['world']['action_usage'],d['settings']['action_usage'])
        self.assertEqual(ui['world']['scheduled_events'],d['settings']['scheduled_events'])
        self.assertEqual(p['アオ'].commitments,d['people'][0]['commitments'])
        self.assertEqual(w.concerns.assessment(w,'アオ')['entries'][0]['state'],'false')
        a=p['アオ'];before=deepcopy(a.__dict__)
        self.assertEqual(VerbEngine(w,FixedRandom([0])).execute(a,Action('move',('森',)),turn=1,day=1)[1]['reason'],'action_time_restricted')
        self.assertEqual(a.__dict__,before)
        self.assertFalse(any(action.verb=='move' for action,_ in candidates(a,w,SimpleNamespace(turn=1,day=1))))
        VerbEngine(w,FixedRandom([0])).execute(a,Action('move',('森',)),turn=2,day=1)
        self.assertEqual(a.zone,'森')
        self.assertEqual(w.concerns.assessment(w,'アオ')['entries'][0]['state'],'true')
        self.assertEqual(VerbEngine(w,FixedRandom([0])).execute(p['レン'],Action('move',('町',)),turn=2,day=1)[1]['reason'],'action_actor_restricted')
        cfgs=ConfigStore(self.repo,self.control,self.root/'runs')
        cfg=cfgs.save({'label':'explicit','project_id':r['world_id'],'template_id':'basic','evolution':{'kappa':0}},config_id='cfg-explicit')
        frozen=self.control/'configs/cfg-explicit/inputs/projects'/r['world_id']
        saved=yaml.safe_load((frozen/'world.yaml').read_bytes())
        self.assertEqual(saved['action_usage'],d['settings']['action_usage'])
        fw,fp=_load_world_and_subjects(frozen,self.control/'configs/cfg-explicit/inputs/templates/basic')
        sim=Simulation(19,fw,fp,self.root/'native-events')
        rows=[json.loads(line) for line in sim.run().read_text(encoding='utf-8').splitlines()]
        event=next(row for row in rows if row.get('verb')=='scheduled_event' and row.get('id')=='warning')
        self.assertEqual((event['day'],event['slot']),(2,'朝'))
        self.assertIn('川の警報',fp['アオ'].knowledge)
        self.assertNotIn('川の警報',fp['レン'].knowledge)

    def test_survive_uses_declared_days_and_slots(self):
        d=self.proposal();_,_,w,p=self.adopt(d);a=p['アオ']
        predicate=w.endings[0]['predicate']
        self.assertFalse(predicate.evaluate(w.namespace(a,list(p.values()),turn=39,day=20)))
        self.assertTrue(predicate.evaluate(w.namespace(a,list(p.values()),turn=40,day=20)))
        d['people'][0]['goal']['conditions'][0]['day']=21
        with self.assertRaises(ConfigError):compile_draft(d)

    def test_explicit_date_labels_and_holidays_are_preserved_without_clock_conversion(self):
        d=self.proposal()
        calendar={'version':1,'start_date':'2028-02-01','end_date':'2028-02-20','weekend_days':[5,6],'holidays':['2028-02-02']}
        d['settings']['time'].update(presentation={'calendar':'real','advance':'bands'},date_calendar=calendar)
        r,_,w,_=self.adopt(d)
        self.assertEqual(w.date_calendar,calendar)
        self.assertEqual(snapshot(LibraryStore(self.repo),r['world_id'])['world']['time']['date_calendar'],calendar)
        d['settings']['time']['days']=19
        with self.assertRaises(ConfigError):validate(self.repo,d)

    def test_actual_generation_adapter_carries_explicit_input_using_mocked_transport(self):
        from execution.generation import Response
        settings_path=self.root/'settings.json'
        settings_path.write_text(json.dumps({'output':{'default_backend':'ollama','ollama':{'model':'test-model'}}}),encoding='utf-8')
        response=json.dumps({'message':{'content':mock_response('x','y')},'done':True,'done_reason':'stop'}).encode()
        specified={'version':1,'time':{'days':20,'slots':['朝','昼']}}
        with patch('execution.generation.transport',return_value=Response(response)) as transport:
            existing_provider(settings_path).with_settings('x','y',specified)
        request=transport.call_args.args[0]
        payload=json.loads(request['prompt'].rsplit('\n',1)[1])
        self.assertEqual(payload['input']['settings'],specified)
        self.assertEqual(transport.call_count,1)

    def test_unrelated_ai_repair_cannot_replace_settings_or_personal_priorities(self):
        from execution.draft_revisions import scoped_candidate
        base=self.proposal();candidate=deepcopy(base)
        candidate['settings']['time']['days']=10
        with self.assertRaises(ConfigError):scoped_candidate(base,candidate,{'section':'people','entity':'アオ','field':'profile'})
        candidate=deepcopy(base);candidate['people'][0]['commitments'][0]['priority']=10
        with self.assertRaises(ConfigError):scoped_candidate(base,candidate,{'section':'people','entity':'アオ'})

    def test_manual_settings_are_not_reinitialized_or_lost_on_validate(self):
        d=self.proposal();d['settings']['mechanics']['stamina_management']=True
        first=validate(self.repo,d);second=validate(self.repo,first[0])
        for value in (first,second):
            self.assertEqual(value[1]['mechanics'],d['settings']['mechanics'])
            self.assertEqual(value[1]['action_usage'],d['settings']['action_usage'])
            self.assertEqual(value[0]['people'][0]['commitments'],d['people'][0]['commitments'])

    def test_explicit_generation_input_wins_and_request_identity_includes_settings(self):
        d=self.proposal();specified={'version':1,'time':d['settings']['time']}
        del d['people'][0]['commitments'];d['settings']={'version':1,'time':{'days':1,'slots':['夜']}}
        self.store.provider=lambda *_:json.dumps(d,ensure_ascii=False)
        r=self.store.create(self.token,d['name'],d['overview'],settings=specified)
        self.assertEqual(r['state'],'review',r.get('errors'))
        self.assertEqual(r['draft']['settings'],specified)
        self.assertEqual(self.store.get(self.token)['input_settings'],specified)
        with self.assertRaises(ConfigError):self.store.create(self.token,d['name'],d['overview'],settings={'version':1,'time':{'days':10,'slots':['朝']}})

    def test_priority_is_never_inferred_and_goal_description_is_visible(self):
        d=self.proposal();d['people'][0].pop('commitments');d['settings'].pop('concerns')
        _,_,w,p=self.adopt(d)
        self.assertIsNone(p['アオ'].commitments)
        self.assertEqual(_goal_line(p['アオ']),'目的: 20日目まで生存する')
        self.assertNotIn('目的: なし',_goal_line(p['アオ']))

    def test_explicit_priority_and_existing_planning_reach_native_choice(self):
        d=self.proposal()
        d['settings']['responses']={'version':2,'state':{'fixture_marker':'idle'},'locations':{'fixture_marker':'町'},
            'signals':[],'actions':[],'channels':[],'events':[],'windows':[]}
        d['settings']['goal_rules']={'version':1,'definitions':[{'condition':'reach_forest','success':{'condition':'reach_forest'},
            'failure':{'constant':False},'period':{'start':1,'end':40}}]}
        d['settings']['planning']={'version':1,'actors':['アオ'],'max_depth':4,'max_nodes':128}
        _,_,w,p=self.adopt(d)
        w.goal_rules.record(w,2)
        plan=w.planner.search(w,p['アオ'],'reach_forest',2)
        self.assertEqual(plan['status'],'planned')
        self.assertEqual((plan['steps'][0]['verb'],plan['steps'][0]['args']),('move',['森']))
        options=candidates(p['アオ'],w,SimpleNamespace(turn=2,day=1))
        self.assertTrue(any(a.verb=='move' and a.args==('森',) and a.meta.get('goal_plan',{}).get('root')=='reach_forest' for a,_ in options))

    def test_invalid_refs_times_required_rest_and_unconfigured_priorities_never_publish(self):
        variants=[]
        for mutate in [lambda d:d['settings']['time'].update(slots=['朝','朝']),
                       lambda d:d['settings']['scheduled_events'][0].update(day=21),
                       lambda d:d['settings']['scheduled_events'][0].update(slot='夜'),
                       lambda d:d['settings']['scheduled_events'][0].update(targets=['知らない人']),
                       lambda d:d['settings']['scheduled_events'][0].update(grants_fact='知らない情報'),
                       lambda d:d['settings']['scheduled_events'][0].update(move_to='知らない場所'),
                       lambda d:d['settings']['action_usage'].update(enabled=['verb:move']),
                       lambda d:d['settings']['action_usage']['constraints']['verb:move'].update(actors=['知らない人']),
                       lambda d:d['people'][0]['commitments'][0].update(priority=None),
                       lambda d:d['settings'].pop('concerns')]:
            d=self.proposal();mutate(d);variants.append(d)
        for d in variants:
            with self.subTest(settings=d['settings']),self.assertRaises((ConfigError,ValueError)):
                validate(self.repo,d)
        self.assertFalse((self.repo/'projects').exists())

    def test_prose_does_not_invent_execution_settings(self):
        d=parse_response(mock_response('文章だけ','20日間、夜は移動禁止'))
        _,w,_=compile_draft(d)
        self.assertEqual(w['time']['days'],7)
        self.assertNotIn('constraints',w['action_usage'])


class DraftExecutionHTTPTests(unittest.TestCase):
    setUp=fixture.DraftHTTPTests.setUp
    shutdown=fixture.DraftHTTPTests.shutdown
    request=fixture.DraftHTTPTests.request

    def test_declared_time_is_preserved_through_http_adoption_and_page_readback(self):
        settings={'version':1,'time':{'days':20,'slots':['朝','昼']}}
        status,raw=self.request('/api/world-drafts',{'request_id':self.token,'name':'20日世界','overview':'町と森','settings':settings})
        self.assertEqual(status,200);record=json.loads(raw);self.assertEqual(record['state'],'review',record)
        self.assertEqual(record['draft']['settings'],settings)
        status,raw=self.request('/api/world-drafts/'+self.token+'/adopt',{'revision':record['revision']})
        self.assertEqual(status,200)
        status,raw=self.request('/worlds/'+record['world_id'])
        self.assertEqual(status,200);self.assertIn('"days": 20'.encode(),raw)
        self.assertEqual(self.calls,1)


if __name__=='__main__':unittest.main()
