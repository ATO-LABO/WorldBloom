from pathlib import Path
from copy import deepcopy
import json,tempfile,unittest,os
import shutil
import yaml
from engine.world import World
from engine.subject import Subject
from engine.sim import Simulation
from engine.world_format import initialize
from execution import world_editor,time_basic,time_settings
from execution.library import LibraryStore
from execution.provenance import ConfigError

LIVE=Path(os.environ.get('WB_TIME_LIVE_ROOT',Path(__file__).resolve().parents[1]))


def fixture():
    world=initialize({'name':'time fixture','gapengine':{'action_graph':'templates/basic/action_graph.yaml','effects':'templates/basic/effects.yaml'},'time':{'days':3,'slots':['朝','昼','夜'],'keep':'authored'},'protagonist':'a','antagonist':'b','zones':[{'name':'home'}],'routes':{},'items':[],'ending':[{'id':'end','when':'turn >= 99999'}],'target_ending':['end']})
    def person(name):return {'id':name,'traits':{k:.5 for k in ['social','stubbornness','curiosity','diligence','temper']},'base':50,'range':{'entry':'home','zones':['home']},'verbs':['rest'],'goal':{}}
    return world,[person('a'),person('b')]


class TimeBasicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='time-basic-');self.addCleanup(self.temp.cleanup);self.repo=Path(self.temp.name);self.base=self.repo/'projects/sample';(self.base/'subjects').mkdir(parents=True);self.store=LibraryStore(self.repo);self.world,self.people=fixture()
        shutil.copytree(LIVE/'templates/basic', self.repo/'templates/basic')
        self.write()
    def write(self):
        (self.base/'world.yaml').write_text(yaml.safe_dump(self.world,allow_unicode=True,sort_keys=False),encoding='utf-8')
        for p in self.people:(self.base/'subjects'/f'{p["id"]}.yaml').write_text(yaml.safe_dump(p,allow_unicode=True),encoding='utf-8')
    def files(self):return {p.relative_to(self.base).as_posix():p.read_bytes() for p in self.base.rglob('*') if p.is_file() and p.name!='.write.lock'}
    def body(self,operation,values,target=None,revision=None):return {'revision':revision or world_editor.snapshot(self.store,'sample')['revision'],'operation':operation,'target':target,'values':values}
    def checked(self,settings=None):return world_editor.save(self.store,'sample',self.body('time-basic-check',{'settings':settings or time_basic.settings_for(self.world)}))
    def apply(self,settings,confirmation):return world_editor.save(self.store,'sample',self.body('time-basic',{'settings':settings,'confirmation':confirmation}))
    def test_inspection_and_preview_are_real_logical_axis_and_do_not_write(self):
        before=self.files();result=world_editor.save(self.store,'sample',self.body('time-basic-inspect',{}));self.assertEqual(result['mode'],'logical');self.assertTrue(result['can_edit_logical']);self.assertEqual(result['preview']['total_steps'],9);self.assertEqual(result['preview']['rows'][0]['turn'],1);self.assertEqual(result['preview']['rows'][-1]['turn'],9);self.assertEqual(before,self.files())
    def test_explicit_save_changes_only_world_time_and_has_original_byte_backup(self):
        (self.base/time_settings.DRAFT).write_text(json.dumps(time_settings.default_settings()),encoding='utf-8');before=self.files();settings={**time_basic.settings_for(self.world),'days':4,'calendar':'none'};check=self.checked(settings);self.assertTrue(check['can_apply']);self.assertEqual(before,self.files());saved=self.apply(settings,check['confirmation']);self.assertEqual(saved['world']['time'],{'days':4,'slots':['朝','昼','夜'],'keep':'authored','presentation':{'calendar':'none','advance':'bands'}})
        for name,raw in before.items():
            if name!='world.yaml':self.assertEqual((self.base/name).read_bytes(),raw)
        backups=list((self.base/time_basic.BACKUPS).glob('*.json'));self.assertEqual(len(backups),1);record=json.loads(backups[0].read_text(encoding='utf-8'));self.assertEqual(record['world_text'].encode('utf-8'),before['world.yaml']);self.assertIn('time_backup',saved)
    def test_changed_time_reaches_real_simulation_not_only_a_sidecar(self):
        settings={**time_basic.settings_for(self.world),'days':2,'slots':['午前','午後']};check=self.checked(settings);saved=self.apply(settings,check['confirmation']);runtime=World(saved['world'],self.base/'world.yaml');cast={p['id']:Subject.from_definition(p) for p in saved['people']};sim=Simulation(11,runtime,cast,self.repo/'run');path=sim.run();rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()];decisions=[r for r in rows if r.get('kind')=='decision'];self.assertEqual({r['turn'] for r in decisions},{1,2,3,4});self.assertEqual(len(decisions),8);self.assertEqual({r['slot'] for r in decisions},{'午前','午後'});self.assertEqual({r['day'] for r in decisions},{1,2})
    def test_one_day_step_is_one_real_round_per_day(self):
        settings={**time_basic.settings_for(self.world),'days':3,'advance':'day','slots':['1日']};check=self.checked(settings);saved=self.apply(settings,check['confirmation']);runtime=World(saved['world'],self.base/'world.yaml');cast={p['id']:Subject.from_definition(p) for p in saved['people']};rows=[json.loads(l) for l in Simulation(9,runtime,cast,self.repo/'day-run').run().read_text(encoding='utf-8').splitlines()];decisions=[r for r in rows if r.get('kind')=='decision'];self.assertEqual(len(decisions),6);self.assertEqual({r['day'] for r in decisions},{1,2,3});self.assertEqual({r['slot'] for r in decisions},{'1日'})
    def test_event_day_and_deleted_or_renamed_slot_are_blocked_without_deletion(self):
        self.world['scheduled_events']=[{'id':'due','day':3,'slot':'夜','targets':['a'],'stress_delta':1}];self.write();before=self.files()
        for changes in [{'days':2},{'slots':['朝','昼']},{'slots':['朝','昼','晩']}]:
            settings={**time_basic.settings_for(self.world),**changes};check=self.checked(settings);self.assertFalse(check['can_apply']);self.assertTrue(check['errors'])
            with self.assertRaises(ConfigError):self.apply(settings,check['confirmation'])
            self.assertEqual(before,self.files())
    def test_legacy_time_api_cannot_bypass_reference_checks_or_calendar_guard(self):
        self.world['scheduled_events']=[{'id':'due','day':3,'slot':'夜','targets':['a']}];self.write();before=self.files()
        with self.assertRaises(ConfigError):world_editor.save(self.store,'sample',self.body('time',{'days':1,'slots':['朝']}))
        self.assertEqual(before,self.files());self.world['slot_execution']={'mode':'unknown','contract':'kept.yaml'};self.write()
        with self.assertRaises(ConfigError):world_editor.save(self.store,'sample',self.body('time',{'days':3,'slots':['朝','昼','夜']}))
    def test_response_event_signal_and_window_horizon_errors(self):
        candidate=deepcopy(self.world);candidate['time']['days']=1
        candidate['responses']={'events':[{'id':'later','turn':8}],'signals':[{'deadline':7}],'windows':[{'start':7,'end':8},'bad-shape']}
        errors,_=time_basic.transition(self.world,candidate,self.people,self.base/'world.yaml');fields={e['field'] for e in errors};self.assertIn('responses.events.0.turn',fields);self.assertIn('responses.signals.0.deadline',fields);self.assertIn('responses.windows.0.end',fields);self.assertIn('responses.windows.1',fields)
    def test_goal_period_is_checked_by_actual_engine_bind(self):
        source=LIVE/'projects/village_objectives/world.yaml';world=yaml.safe_load(source.read_text(encoding='utf-8'));people=[yaml.safe_load(p.read_text(encoding='utf-8')) for p in (source.parent/'subjects').glob('*.yaml')];candidate=deepcopy(world);candidate['time']['days']=1;candidate['scheduled_events']=[]
        for entry in candidate['responses']['signals']:
            if entry['deadline'] is not None:entry['deadline']=1
        for entry in candidate['responses']['events']:entry['turn']=1
        for entry in candidate['responses']['windows']:entry.update(start=1,end=1)
        errors,_=time_basic.transition(world,candidate,people,source);self.assertTrue(any('period exceeds' in e['message'] for e in errors),errors)
    def test_day_turn_ending_checks_respect_or_and_comparison_meaning(self):
        for expression,warning in [('day == 5',True),('day <= 5',False),('day != 5',False),('turn >= 8',True),('day == 5 or day == 1',False),('day >= 5 and vitality("a") == "alive"',True),('day == 5 or vitality("a") == "alive"',False)]:
            with self.subTest(expression=expression):
                self.world['ending'][0]['when']=expression;self.write();check=self.checked({**time_basic.settings_for(self.world),'days':1});self.assertTrue(check['can_apply']);self.assertEqual(any(w['field']=='ending' for w in check['warnings']),warning,check)
    def test_truth_sample_validation_does_not_reject_or_expose_valid_evidence(self):
        self.world['facts']=[{'id':'secret','values':['x','y']},{'id':'clue','implies':{'fact':'secret','value':'$truth'}}];self.world['truth']={'secret':{'candidates':{'x':1,'y':1}}};self.write();before=self.files();check=self.checked({**time_basic.settings_for(self.world),'days':4});self.assertTrue(check['can_apply'],check);self.assertEqual(before,self.files());self.assertNotIn('drawn_truth',check)
    def test_confirmation_revision_and_pressure_dependency_are_bound(self):
        settings={**time_basic.settings_for(self.world),'days':4};check=self.checked(settings);before=self.files()
        with self.assertRaises(ConfigError):self.apply(settings,'not-the-reviewed-input')
        self.assertEqual(before,self.files());pressure=self.base/'time-pressure.yaml';pressure.write_text('version: 1\nsources: []\n',encoding='utf-8');check=self.checked(settings);pressure.write_text('version: 1\nsources: []\n\n',encoding='utf-8')
        with self.assertRaises(ConfigError) as caught:self.apply(settings,check['confirmation'])
        self.assertEqual(caught.exception.code,'conflict');self.assertEqual((self.base/'world.yaml').read_bytes(),before['world.yaml'])
        old=self.body('time-basic-check',{'settings':settings});self.world['name']='changed';self.write()
        with self.assertRaises(ConfigError):world_editor.save(self.store,'sample',old)
    def test_unknown_presentation_fields_are_kept_and_nonmapping_is_not_replaced(self):
        self.world['time']['presentation']={'calendar':'ordinal','advance':'bands','author_note':'keep'};self.write();settings={**time_basic.settings_for(self.world),'days':4};checked=self.checked(settings);saved=self.apply(settings,checked['confirmation']);self.assertEqual(saved['world']['time']['presentation']['author_note'],'keep')
        self.world['time']['presentation']='existing annotation';self.write();before=self.files()
        with self.assertRaises(ConfigError):self.checked({**time_basic.settings_for(self.world),'days':4})
        self.assertEqual(before,self.files())
    def test_legacy_safe_time_save_also_has_preimage_backup(self):
        self.world['ending'][0]['when']='zone("a") == "home"';self.write();before=(self.base/'world.yaml').read_bytes();saved=world_editor.save(self.store,'sample',self.body('time',{'days':4,'slots':self.world['time']['slots']}));self.assertEqual(saved['world']['time']['days'],4);records=list((self.base/time_basic.BACKUPS).glob('*.json'));self.assertEqual(len(records),1);self.assertEqual(json.loads(records[0].read_text(encoding='utf-8'))['world_text'].encode('utf-8'),before)
    def test_unsupported_calendar_week_hours_and_invalid_values_never_save(self):
        base=time_basic.settings_for(self.world);before=self.files()
        for change in [{'calendar':'real'},{'calendar':'invented-year-month'},{'advance':'week'},{'advance':'hours'},{'step':2},{'days':True},{'days':0},{'slots':['朝','朝']},{'slots':[]},{'advance':'day'}]:
            with self.subTest(change=change),self.assertRaises(ConfigError):self.checked({**base,**change})
            self.assertEqual(before,self.files())
        with self.assertRaises(ConfigError):world_editor.save(self.store,'sample',self.body('time-basic-inspect',{},target='not-null'))
    def test_existing_calendar_is_real_validated_readonly_and_not_migrated(self):
        source=LIVE/'projects/calendar_workshop_example/world.yaml';raw=source.read_bytes();world=yaml.safe_load(raw);people=[yaml.safe_load((source.parent/p['path']).read_text(encoding='utf-8')) for p in world['slot_execution']['subjects']];result=time_basic.inspect(world,people,source);self.assertEqual(result['mode'],'calendar_promises_v2');self.assertFalse(result['can_edit_logical']);self.assertFalse(result['calendar']['valid'],result);self.assertIn('旧世界形式',result['calendar']['error']);self.assertEqual(source.read_bytes(),raw)
        with self.assertRaises(ConfigError):time_basic.check({'world.yaml':world,**{'subjects/'+str(i)+'.yaml':p for i,p in enumerate(people)}},'r',time_basic.settings_for(world),source)
    def test_calendar_design_compilation_never_claims_executable_finalization(self):
        source=LIVE/'projects/calendar_workshop_example/world.yaml';world=yaml.safe_load(source.read_text(encoding='utf-8'));people=[yaml.safe_load((source.parent/p['path']).read_text(encoding='utf-8')) for p in world['slot_execution']['subjects']];contract=yaml.safe_load((source.parent/world['slot_execution']['contract']).read_text(encoding='utf-8'));report=time_basic.inspect_design(contract['calendar'],world,people,source);self.assertFalse(report['can_finalize']);self.assertTrue(report['blockers'])
    def test_design_finalize_is_explicitly_unsupported_and_preserves_world(self):
        before=self.files();draft=time_settings.default_settings();draft['period']={'start_date':'2026-11-01','end_date':'2026-11-03'};snapshot=time_settings.snapshot(self.store,'sample');body={'revision':snapshot['revision'],'settings':draft};checked=time_settings.validate(self.store,'sample',body);self.assertIn('execution_check',checked);self.assertFalse(checked['execution_check']['can_finalize'])
        with self.assertRaises(ConfigError) as caught:time_settings.finalize(self.store,'sample',body)
        self.assertEqual(caught.exception.code,'unsupported');self.assertEqual(before,self.files())

    def route(self, mode='at', slot='10:00'):
        data={'goal':{'all':[{'predicate':'at','zone':'home'}],'time':{'mode':mode} if mode=='any' else {'mode':mode,'day':1,'slot':slot}}}
        (self.base/'route.yaml').write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')

    def test_slot_catalog_includes_current_survey_and_event_without_writes(self):
        self.world['time']['slots']=['09:00','10:00','11:00'];self.world['scheduled_events']=[{'id':'depart','label':'出発','day':1,'slot':'09:00'}];self.write();self.route();before=self.files()
        value=world_editor.save(self.store,'sample',self.body('time-basic-inspect',{}))
        self.assertEqual(value['slot_refs_version'],1);self.assertFalse(value['slot_reference_errors'])
        self.assertEqual({r['kind'] for r in value['slot_references']},{'scheduled_event','route_survey_goal'})
        self.assertNotIn(str(self.repo),json.dumps(value,ensure_ascii=False));self.assertEqual(self.files(),before)

    def test_slot_rename_delete_protection_covers_survey_and_legacy_time_api(self):
        self.world['time']['slots']=['09:00','10:00','11:00'];self.world['scheduled_events']=[{'id':'depart','label':'出発','day':1,'slot':'09:00'}];self.write();self.route();before=(self.base/'world.yaml').read_bytes()
        for slots,label in [(['09:00','11:00'],'経路調査'),(['10:00','11:00'],'出発'),(['09:00','noon','11:00'],'経路調査')]:
            settings={**time_basic.settings_for(self.world),'slots':slots};checked=self.checked(settings);self.assertFalse(checked['can_apply']);self.assertTrue(any(label in e['message'] for e in checked['errors']))
            with self.assertRaises(ConfigError):world_editor.save(self.store,'sample',self.body('time',{'days':3,'slots':slots}))
            self.assertEqual((self.base/'world.yaml').read_bytes(),before)

    def test_confirmation_binds_route_creation_and_content_even_without_world_revision_change(self):
        settings={**time_basic.settings_for(self.world),'days':4};checked=self.checked(settings);revision=self.body('time-basic-inspect',{})['revision'];self.route('any');before=(self.base/'world.yaml').read_bytes()
        self.assertEqual(self.body('time-basic-inspect',{})['revision'],revision)
        with self.assertRaises(ConfigError) as caught:self.apply(settings,checked['confirmation'])
        self.assertEqual(caught.exception.code,'conflict');self.assertEqual((self.base/'world.yaml').read_bytes(),before)
        checked=self.checked(settings);(self.base/'route.yaml').write_text((self.base/'route.yaml').read_text(encoding='utf-8')+'# edited\n',encoding='utf-8')
        with self.assertRaises(ConfigError) as caught:self.apply(settings,checked['confirmation'])
        self.assertEqual(caught.exception.code,'conflict');self.assertEqual((self.base/'world.yaml').read_bytes(),before)

    def test_unreadable_slot_reference_shape_blocks_instead_of_becoming_unused(self):
        (self.base/'route.yaml').write_text('goal: [',encoding='utf-8');before=self.files();value=world_editor.save(self.store,'sample',self.body('time-basic-inspect',{}));self.assertTrue(value['slot_reference_errors'])
        checked=self.checked({**time_basic.settings_for(self.world),'days':4});self.assertFalse(checked['can_apply']);self.assertEqual(self.files(),before)

    def test_adding_missing_survey_label_repairs_reference_without_mutating_route(self):
        self.world['time']['slots']=['09:00','11:00'];self.write();self.route();before=(self.base/'route.yaml').read_bytes();settings={**time_basic.settings_for(self.world),'slots':['09:00','10:00','11:00']};checked=self.checked(settings);self.assertTrue(checked['can_apply'],checked);self.assertTrue(any('経路調査' in w['message'] for w in checked['warnings']));self.apply(settings,checked['confirmation']);self.assertEqual((self.base/'route.yaml').read_bytes(),before)

    def test_last_moment_route_dependency_change_prevents_world_replacement(self):
        from unittest.mock import patch
        from execution import time_slot_references as refs
        settings={**time_basic.settings_for(self.world),'days':4};checked=self.checked(settings);before=(self.base/'world.yaml').read_bytes();real=refs.snapshot;calls=[]
        def changed(*args):
            result=real(*args);calls.append(True)
            if len(calls)>1:result['dependency_hash']='changed-after-confirmation'
            return result
        with patch.object(refs,'snapshot',side_effect=changed):
            with self.assertRaises(ConfigError) as caught:self.apply(settings,checked['confirmation'])
        self.assertEqual(caught.exception.code,'conflict');self.assertEqual((self.base/'world.yaml').read_bytes(),before)


if __name__=='__main__':unittest.main()
