"""Native minute choices, agreements and recovery through shared goal search."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import yaml

from engine.calendar_inputs import load_project
from engine.sim import Simulation


ROOT = Path(__file__).resolve().parents[1]


def read(path): return yaml.safe_load(path.read_text(encoding='utf8'))
def write(path, value): path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding='utf8')


def project(folder, *, deadline='09:08', rename=False):
    shutil.copytree(ROOT/'projects/calendar_workshop_example', folder)
    world = read(folder/'world.yaml'); contract = read(folder/'contract.yaml')
    world['time'] = {'days':1, 'slots':['09:00']}; world['scheduled_events'] = []
    world['planning'] = {'version':1, 'actors':['Ada'], 'max_depth':12, 'max_nodes':2048}
    world['concerns'] = {'version':2, 'conditions':[
        {'id':'order_done','label':'約束した品物を渡す','mode':'achieve','visibility':'observed',
         'test':{'kind':'promise','promise':'piece-order','version':1,'statuses':['fulfilled']}}]}
    world['goal_rules'] = {'version':2,'definitions':[{'condition':'order_done',
        'success':{'condition':'order_done'},'failure':{'constant':False},
        'period':{'start':{'date':'2026-11-01','time':'08:00'},'end':{'date':'2026-11-01','time':deadline}}}]}
    contract['calendar']['period']['end_date'] = '2026-11-01'
    contract['calendar']['normal']['bands'] = [{'id':'day','name':'Day','start':'08:00','end':'10:00','overnight':False,'interval_minutes':17}]
    contract.update(start_time='08:00',end_time='10:00',energy={
        'move_per_minute':.2,'work_per_minute':.2,'rest_per_minute':.4,'rest_minutes':10})
    contract['tasks']['pieces']['place'] = 'Store'
    person = read(folder/'subjects/ada.yaml')
    person['verbs'] = ['move','respond','rest']
    person['stamina'] = {'max':6,'current':6,'recover_per_slot':1}
    person['commitments'] = [{'condition':'order_done','priority':9,'required':False}]
    terms = {'promise_id':'piece-order','version':1,'proposer':'Ada','promisor':'Ada','beneficiary':'Bo',
        'acceptance_parties':['Ada','Bo'],'place':'Bench','start':486,'deadline':548,'grace_until':None,
        'requirements':[{'task_id':'handoff','units':1,'contribution_by':'Ada','delivered_to':'Bo'}]}
    from engine.promises_v2 import PromiseRegistry
    terms_hash = PromiseRegistry({'Ada','Bo'},set(contract['tasks']),set(world['routes'])).terms_hash(terms)
    commands = [{'id':'agree-offer','actor':'Ada','at':480,'verb':'offer','terms':terms},
        {'id':'agree-maker','actor':'Ada','at':482,'verb':'accept','promise_id':'piece-order','version':1,'terms_hash':terms_hash},
        {'id':'agree-receiver','actor':'Bo','at':484,'verb':'accept','promise_id':'piece-order','version':1,'terms_hash':terms_hash}]
    write(folder/'world.yaml',world); write(folder/'contract.yaml',contract); write(folder/'subjects/ada.yaml',person)
    (folder/'choices.json').write_text(json.dumps(commands,indent=2),encoding='utf8')
    return world,contract,person,commands


def simulation(folder, out, *, seed=0, commands=None):
    world, people = load_project(folder/'world.yaml')
    sim = Simulation(seed,world,people,out)
    sim.slot_runtime.configure(json.loads((folder/'choices.json').read_text()) if commands is None else commands)
    return sim


class CalendarGoalPlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.project = self.root/'inputs'
        self.world,self.contract,self.person,self.commands = project(self.project)

    def run_normal(self):
        sim = simulation(self.project,self.root/'out'); sim.slot_runtime.run(); return sim

    def test_real_agreement_then_unforced_move_work_rest_return_handoff_at_deadline(self):
        sim = self.run_normal(); rt = sim.slot_runtime
        decisions = [r for r in rt.rows if r['verb']=='decision' and r['details']['selection_mechanism']=='bounded_goal_search']
        verbs = [r['details']['choice']['verb'] for r in decisions]
        self.assertIn('rest',verbs); self.assertEqual(verbs.count('work'),3)
        self.assertEqual(verbs[-2:],['move','deliver'])
        self.assertEqual(rt.registry.get('piece-order')['status'],'fulfilled')
        verdict = next(r for r in sim.world.goal_rules.report(sim.world) if r['condition']=='order_done')
        self.assertEqual((verdict['status'],verdict['decided_at']),('achieved',548))
        self.assertEqual(sum(r['details'].get('duration_minutes',0) for r in rt.rows if r['verb']=='action_completed'
                         and r['details']['choice']['verb']=='rest'),10)
        self.assertFalse([r for r in rt.rows if r['result'] in {'failed','rejected'}])

    def test_no_received_acceptance_does_not_invent_agreement_or_work(self):
        sim = simulation(self.project,self.root/'no-agreement',commands=self.commands[:2]); sim.slot_runtime.run()
        self.assertFalse([r for r in sim.slot_runtime.rows if r['details'].get('selection_mechanism')=='bounded_goal_search'])
        self.assertEqual(sim.slot_runtime.ledger['tasks']['pieces']['units'],0)

    def test_one_minute_too_short_has_no_feasible_plan_and_never_fabricates_success(self):
        self.world['goal_rules']['definitions'][0]['period']['end']['time']='09:07'
        write(self.project/'world.yaml',self.world)
        sim=self.run_normal()
        self.assertNotEqual(sim.world.goal_rules.report(sim.world)[0]['status'],'achieved')
        self.assertEqual(sim.slot_runtime.ledger['tasks']['handoff']['units'],0)

    def test_split_resume_preserves_identical_all_outputs_and_plan_history(self):
        continuous=simulation(self.project,self.root/'continuous'); continuous.slot_runtime.run()
        split=simulation(self.project,self.root/'split'); split.slot_runtime.run(until=510)
        resumed=simulation(self.project,self.root/'split'); resumed.slot_runtime.run(resume=True)
        def files(folder): return {p.name:p.read_bytes() for p in folder.iterdir() if p.is_file()}
        self.assertEqual(files(continuous.out_dir),files(resumed.out_dir))
        self.assertEqual(continuous.slot_runtime.export_state(),resumed.slot_runtime.export_state())

    def test_search_only_does_not_change_energy_registry_or_people(self):
        sim=simulation(self.project,self.root/'search'); rt=sim.slot_runtime
        rt.run(until=486)
        before=deepcopy(rt.export_state()); actor=sim.subjects['Ada']
        sim.world.planner.search(sim.world,actor,'order_done',486)
        self.assertEqual(before,rt.export_state())

    def test_unreceived_promise_remains_unknown_even_if_condition_marked_public(self):
        self.world['concerns']['conditions'][0]['visibility']='public'; write(self.project/'world.yaml',self.world)
        sim=simulation(self.project,self.root/'private'); rt=sim.slot_runtime; rt.run(until=486)
        rt.observed['Ada'].clear(); rt.known_terms['Ada'].clear()
        self.assertEqual(sim.world.concerns.evaluate(sim.world,'order_done',actor='Ada')['state'],'unknown')

    def test_invalid_calendar_period_and_energy_are_rejected(self):
        self.world['goal_rules']['definitions'][0]['period']['end']['time']='99:99'; write(self.project/'world.yaml',self.world)
        with self.assertRaises(ValueError): simulation(self.project,self.root/'bad')

    def test_promise_with_multiple_work_units_keeps_planning_after_partial_transition(self):
        # Fulfillment must use own, matching, observed contributions.
        self.contract['tasks']['pieces']['place']='Bench'
        write(self.project/'contract.yaml',self.contract)
        terms=self.commands[0]['terms']; terms['requirements']=[{'task_id':'pieces','units':3,'contribution_by':'Ada'}]
        from engine.promises_v2 import PromiseRegistry
        h=PromiseRegistry({'Ada','Bo'},set(self.contract['tasks']),{'Bench','Store'}).terms_hash(terms)
        for c in self.commands[1:]: c['terms_hash']=h
        (self.project/'choices.json').write_text(json.dumps(self.commands),encoding='utf8')
        sim=self.run_normal()
        self.assertEqual(sim.slot_runtime.registry.get('piece-order')['status'],'fulfilled')
        self.assertEqual(sim.slot_runtime.ledger['tasks']['pieces']['units'],3)

    def test_display_spacing_does_not_change_chosen_actions_or_completion_times(self):
        first=self.run_normal()
        self.contract['calendar']['normal']['bands'][0]['interval_minutes']=3
        write(self.project/'contract.yaml',self.contract)
        second=simulation(self.project,self.root/'other-display'); second.slot_runtime.run()
        def physical(sim):
            return [(r['absolute_minute'],r['details']['choice']) for r in sim.slot_runtime.rows
                    if r['details'].get('selection_mechanism')=='bounded_goal_search']
        self.assertEqual(physical(first),physical(second))

    def test_checkpoint_rejects_changed_planning_memory_before_mutation(self):
        sim=simulation(self.project,self.root/'corrupt'); rt=sim.slot_runtime; rt.run(until=510)
        original=deepcopy(rt.export_state()); changed=deepcopy(original)
        changed['goal_planning']['planner']['history']=[]
        with self.assertRaises(ValueError): rt.restore_state(changed)
        self.assertEqual(original,rt.export_state())

    def test_native_calendar_choices_and_recovery_are_readable_in_shared_planning_view(self):
        sim=self.run_normal()
        from gapengine.explanations import extract_explanation
        from viewer.planning_view import render, model
        explanation=extract_explanation(sim.out_dir/'layers.jsonl')
        self.assertTrue(explanation['decisions'])
        groups=model(explanation)
        self.assertTrue(any(r['kind']=='recovered' for group in groups for r in group['events']))
        html=render(explanation)
        self.assertIn('回復した体力',html); self.assertIn('2026-11-01',html)
        self.assertIn('所要見込み 10分',html)
        self.assertIn('所要見込み 2分',html)
        self.assertNotIn('所要見込み 10時点',html)

    def test_personal_priority_selects_work_for_own_purpose_over_other_task(self):
        self.world['concerns']['conditions'].append({'id':'other_done','label':'別の仕事を終える','mode':'achieve','visibility':'observed',
            'test':{'kind':'task','task':'other','minimum':1}})
        self.world['goal_rules']['definitions'].append({'condition':'other_done','success':{'condition':'other_done'},
            'failure':{'constant':False},'period':deepcopy(self.world['goal_rules']['definitions'][0]['period'])})
        self.contract['tasks']['other']={**deepcopy(self.contract['tasks']['pieces']),'place':'Bench','units':1,'materials_per_unit':{}}
        self.contract['initial_task_knowledge']['Ada'].append('other')
        self.person['commitments'].append({'condition':'other_done','priority':10,'required':False})
        write(self.project/'world.yaml',self.world); write(self.project/'contract.yaml',self.contract)
        write(self.project/'subjects/ada.yaml',self.person)
        sim=self.run_normal()
        choices=[r['details']['choice'] for r in sim.slot_runtime.rows if r['details'].get('selection_mechanism')=='bounded_goal_search']
        self.assertEqual(choices[0]['task_id'],'other')

    def test_required_condition_prevents_leaving_place_to_fulfill_optional_promise(self):
        self.world['concerns']['conditions'].append({'id':'stay','label':'持ち場を守る','mode':'maintain','visibility':'observed',
            'test':{'kind':'zone','actor':'Ada','equals':'Bench'}})
        self.person['commitments'].append({'condition':'stay','priority':1,'required':True})
        write(self.project/'world.yaml',self.world); write(self.project/'subjects/ada.yaml',self.person)
        sim=self.run_normal()
        self.assertFalse([r for r in sim.slot_runtime.rows if r['details'].get('selection_mechanism')=='bounded_goal_search'])

    def test_time_based_objective_binds_twice_without_rewriting_authored_dates(self):
        sim=simulation(self.project,self.root/'bind')
        self.assertEqual(sim.world.goal_rules.config['definitions'][0]['period'],self.world['goal_rules']['definitions'][0]['period'])
        self.assertEqual(sim.world.goal_rules.definitions['order_done']['period'],{'start':480,'end':548})

    def test_ordinary_timed_world_searches_recovery_without_calendar_or_story_code(self):
        from test_action_timing import configuration
        from test_goal_planner import load
        raw,people=configuration()
        people[0]['stamina'].update(current=2,max=10,recover_per_slot=4)
        world,cast,_=load(raw,people)
        actor=cast['通信士']; before=actor.stamina
        plan=world.planner.search(world,actor,'beacon_sent',1)
        self.assertTrue(plan['steps'])
        self.assertIn('rest',[s['verb'] for s in plan['steps']])
        self.assertEqual(actor.stamina,before)

    def test_insufficient_energy_rejection_does_not_reserve_materials(self):
        self.person['range']['entry']='Store'; self.person['stamina']['current']=1
        write(self.project/'subjects/ada.yaml',self.person)
        sim=simulation(self.project,self.root/'rejected',commands=[
            {'id':'low-energy','actor':'Ada','at':480,'verb':'work','task_id':'pieces','minutes':30}])
        sim.slot_runtime.run(until=480)
        self.assertEqual(sim.slot_runtime.ledger['materials'],{'blank':3})
        self.assertEqual(sim.slot_runtime.ledger['tasks']['pieces']['reserved_units'],0)
        rejection=next(r for r in sim.slot_runtime.rows if r['id']=='reject:low-energy')
        self.assertEqual(rejection['details']['reason'],'insufficient_stamina')

    def test_partial_unit_reuses_existing_reservation_and_spent_effort(self):
        self.person['range']['entry']='Store'; write(self.project/'subjects/ada.yaml',self.person)
        terms=self.commands[0]['terms']; terms['place']='Store'
        terms['requirements']=[{'task_id':'pieces','units':3,'contribution_by':'Ada'}]
        from engine.promises_v2 import PromiseRegistry
        h=PromiseRegistry({'Ada','Bo'},set(self.contract['tasks']),{'Bench','Store'}).terms_hash(terms)
        for c in self.commands[1:]: c['terms_hash']=h
        sim=simulation(self.project,self.root/'partial',commands=[*self.commands,
            {'id':'short-work','actor':'Ada','at':486,'verb':'work','task_id':'pieces','minutes':5}])
        sim.slot_runtime.run()
        self.assertEqual(sim.slot_runtime.ledger['tasks']['pieces']['effort_minutes'],30)
        self.assertEqual(sim.slot_runtime.ledger['materials'],{'blank':0})
        self.assertEqual(sim.slot_runtime.registry.get('piece-order')['status'],'fulfilled')

    def test_known_cancellation_stops_obligation_work_without_undoing_travel(self):
        hash_value=self.commands[1]['terms_hash']
        commands=[*self.commands,*[{'id':'cancel-'+who,'actor':who,'at':at,'verb':'cancel','promise_id':'piece-order',
            'version':1,'terms_hash':hash_value} for who,at in [('Bo',488),('Ada',496)]]]
        sim=simulation(self.project,self.root/'cancelled',commands=commands); sim.slot_runtime.run()
        self.assertEqual(sim.slot_runtime.registry.get('piece-order')['status'],'cancelled')
        self.assertEqual(sim.slot_runtime.ledger['tasks']['pieces']['units'],0)
        self.assertEqual(sim.subjects['Ada'].zone,'Store')
        self.assertEqual(sim.subjects['Ada'].stamina,4)

    def test_capacity_too_small_cannot_be_fixed_by_an_infinite_rest_loop(self):
        self.person['stamina'].update(current=1,max=1); write(self.project/'subjects/ada.yaml',self.person)
        sim=self.run_normal()
        self.assertEqual(sim.slot_runtime.ledger['tasks']['handoff']['units'],0)
        self.assertFalse([r for r in sim.slot_runtime.rows if r['details'].get('selection_mechanism')=='bounded_goal_search'])

    def test_frozen_project_uses_same_shared_search_without_requiring_test_helpers(self):
        from engine.calendar_inputs import freeze_project
        target=self.root/'frozen'; version=freeze_project(self.project/'world.yaml',target)
        world,people=load_project(target/version['world'])
        sim=Simulation(4,world,people,self.root/'frozen-run'); sim.slot_runtime.configure(self.commands); sim.slot_runtime.run()
        self.assertEqual(sim.slot_runtime.registry.get('piece-order')['status'],'fulfilled')

    def test_date_deadline_selects_feasible_indirect_route_over_slow_direct_route(self):
        self.world['concerns']['conditions'][0]['test']={'kind':'zone','actor':'Ada','equals':'Store'}
        self.world['goal_rules']['definitions'][0]['period']['end']['time']='08:25'
        self.world['zones'].append({'name':'Bridge'})
        self.world['routes']={'Bench':[{'to':'Store','cost':1},{'to':'Bridge','cost':1}],
                              'Bridge':[{'to':'Store','cost':1}],'Store':[{'to':'Bench','cost':1}]}
        self.contract['travel_minutes']={'Bench':{'Store':120,'Bridge':10},'Bridge':{'Store':10},'Store':{'Bench':10}}
        self.contract['energy']['move_per_minute']=.01
        self.person['range']['zones'].append('Bridge')
        write(self.project/'world.yaml',self.world); write(self.project/'contract.yaml',self.contract)
        write(self.project/'subjects/ada.yaml',self.person)
        sim=simulation(self.project,self.root/'detour',commands=[]); sim.slot_runtime.run()
        chosen=[r['details']['choice']['to'] for r in sim.slot_runtime.rows
                if r['details'].get('selection_mechanism')=='bounded_goal_search']
        self.assertEqual(chosen,['Bridge','Store'])
        self.assertEqual(sim.world.goal_rules.report(sim.world)[0]['decided_at'],500)

    def test_date_deadline_can_cross_midnight_without_scaling_travel(self):
        self.world['time']['days']=2
        self.contract['calendar']['period']['end_date']='2026-11-02'
        self.contract['start_time']='23:50'; self.contract['end_time']='00:20'
        rule=self.world['goal_rules']['definitions'][0]
        rule['period']={'start':{'date':'2026-11-01','time':'23:50'},'end':{'date':'2026-11-02','time':'00:00'}}
        self.world['concerns']['conditions'][0]['test']={'kind':'zone','actor':'Ada','equals':'Store'}
        write(self.project/'world.yaml',self.world); write(self.project/'contract.yaml',self.contract)
        sim=simulation(self.project,self.root/'midnight',commands=[]); sim.slot_runtime.run()
        self.assertEqual(sim.world.goal_rules.report(sim.world)[0]['status'],'achieved')
        self.assertEqual(sim.world.goal_rules.report(sim.world)[0]['decided_at'],1440)

    def test_insufficient_time_to_rest_rejects_ordinary_timed_plan(self):
        from test_action_timing import configuration
        from test_goal_planner import load
        raw,people=configuration(); people[0]['stamina'].update(current=.1,max=10,recover_per_slot=4)
        for spec in raw['goal_rules']['definitions']:
            if spec['condition']=='beacon_sent': spec['period']['end']=2
        world,cast,_=load(raw,people)
        plan=world.planner.search(world,cast['通信士'],'beacon_sent',1)
        self.assertFalse(plan['steps'])

    def test_no_path_still_displays_assessment_and_actual_failure_without_faking_rest(self):
        self.world['goal_rules']['definitions'][0]['period']['end']['time']='09:07'; write(self.project/'world.yaml',self.world)
        sim=self.run_normal()
        from gapengine.explanations import extract_explanation
        from viewer.planning_view import render
        ex=extract_explanation(sim.out_dir/'layers.jsonl')
        self.assertFalse(ex['decisions']); self.assertTrue(ex['planning_assessments'])
        self.assertFalse([r for r in sim.slot_runtime.rows if r['verb']=='action_started' and r['details']['choice']['verb']=='rest'])
        html=render(ex)
        self.assertIn('行動は開始していません',html); self.assertIn('失敗確定',html)

    def test_planned_recovery_is_progress_not_a_fabricated_replan(self):
        sim=self.run_normal()
        from gapengine.explanations import extract_explanation
        from viewer.planning_view import model
        groups=model(extract_explanation(sim.out_dir/'layers.jsonl'))
        self.assertFalse([r for g in groups for r in g['events'] if r['kind']=='replan' and r['turn']==516])

    def test_maintained_location_fails_at_departure_not_later_arrival(self):
        self.world['concerns']['conditions'][0]['test']={'kind':'zone','actor':'Ada','equals':'Store'}
        self.world['concerns']['conditions'].append({'id':'stay','label':'持ち場にいる','mode':'maintain','visibility':'observed',
            'test':{'kind':'zone','actor':'Ada','equals':'Bench'}})
        self.world['goal_rules']['definitions'].append({'condition':'stay','success':{'condition':'stay'},
            'failure':{'constant':False},'period':deepcopy(self.world['goal_rules']['definitions'][0]['period'])})
        self.person['commitments'].append({'condition':'stay','priority':1,'required':False})
        write(self.project/'world.yaml',self.world); write(self.project/'subjects/ada.yaml',self.person)
        sim=simulation(self.project,self.root/'departure',commands=[]); sim.slot_runtime.run()
        stay=next(r for r in sim.world.goal_rules.report(sim.world) if r['condition']=='stay')
        self.assertEqual((stay['status'],stay['decided_at']),('failed',480))

    def test_two_travellers_are_not_treated_as_colocated_observers(self):
        self.world['concerns']['conditions'].append({'id':'peer_arrived','label':'相手が到着した','mode':'achieve','visibility':'observed',
            'test':{'kind':'zone','actor':'Bo','equals':'Store'}})
        write(self.project/'world.yaml',self.world)
        sim=simulation(self.project,self.root/'travellers',commands=[*self.commands,
            {'id':'peer-walk','actor':'Bo','at':490,'verb':'move','to':'Store'}])
        sim.slot_runtime.run(until=490)
        self.assertEqual(sim.subjects['Ada'].zone,'移動中'); self.assertEqual(sim.subjects['Bo'].zone,'移動中')
        self.assertEqual(sim.world.concerns.evaluate(sim.world,'peer_arrived',actor='Ada')['state'],'unknown')
