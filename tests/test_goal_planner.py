from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import random
import tempfile
import unittest

from engine.actions import Action, candidates
from engine.goal_planner import GoalPlanner
from engine.sim import Simulation
from engine.subject import Subject
from engine.verbs import VerbEngine
from engine.world import World

ROOT = Path(__file__).resolve().parents[1]


def inputs(project='ship_planning'):
    folder = ROOT / 'projects' / project
    return json.loads((folder / 'world.yaml').read_text(encoding='utf8')), [
        json.loads(p.read_text(encoding='utf8')) for p in sorted((folder / 'subjects').glob('*.yaml'))]


def load(raw=None, people=None, project='ship_planning'):
    original, cast = inputs(project)
    world = World(deepcopy(raw if raw is not None else original), ROOT / 'projects' / project / 'world.yaml')
    persons = {p['id']: Subject.from_definition(p) for p in deepcopy(people if people is not None else cast)}
    world.bind_subjects(persons)
    world.goal_rules.record(world, 1)
    return world, persons, VerbEngine(world, random.Random(0))


def choose(w, p, turn, who='通信士'):
    options = candidates(p[who], w, SimpleNamespace(turn=turn, day=(turn - 1) // len(w.slots) + 1))
    return max(options, key=lambda item: item[1])[0]


def episode(raw=None, people=None, project='ship_planning'):
    w, p, e = load(raw, people, project)
    with tempfile.TemporaryDirectory() as temp:
        sim = Simulation(0, w, p, Path(temp), record_explanations=True)
        rows = [json.loads(line) for line in sim.run().read_text(encoding='utf8').splitlines()]
    return w, p, rows


class GoalPlannerTests(unittest.TestCase):
    def test_final_purpose_derives_item_prerequisite_without_adding_person_values(self):
        w, p, _ = load()
        original = deepcopy(p['通信士'].commitments)
        plan = w.planner.search(w, p['通信士'], 'beacon_sent', 1)
        self.assertEqual(plan['status'], 'planned')
        self.assertEqual([s['args'] for s in plan['steps']], [['保管庫'], ['retrieve_store'], ['操舵室'], ['send_beacon']])
        self.assertNotIn('battery_owned', [c['condition'] for c in original])
        self.assertEqual(p['通信士'].commitments, original)
        self.assertEqual(w.planner.history, [])

    def test_failed_mission_replans_search_recovery_and_send_without_rewinding_history(self):
        w, p, rows = episode()
        ds = [r for r in rows if r.get('kind') == 'decision' and r['subject'] == '通信士']
        failed = next(r for r in ds if r['result'] == 'failed')
        self.assertEqual((failed['turn'], failed['args']), (3, ['retrieve_store']))
        self.assertEqual(next(v for v in failed['details']['objective_outcomes'] if v['condition'] == 'beacon_sent')['status'], 'pending')
        self.assertTrue(any(r['details'].get('planning', {}).get('reason') == 'previous_action_failed' for r in ds))
        self.assertEqual(w.responses.state['beacon'], 'sent')
        self.assertIn('battery_location', p['通信士'].knowledge)
        self.assertTrue(any(r['args'] == ['scan_debris'] and r['result'] == 'responded' for r in ds))
        self.assertFalse(any('forced' in r for r in ds))
        self.assertEqual(w.responses.state['safety'], 'safe')

    def test_remote_truth_change_does_not_change_actor_plan_before_any_observation(self):
        w, p, _ = load()
        before = w.planner.search(w, p['通信士'], 'beacon_sent', 1)
        w.responses.state['battery'] = 'destroyed'
        self.assertEqual(w.planner.search(w, p['通信士'], 'beacon_sent', 1), before)

    def test_failed_attempt_learns_not_stored_but_not_hidden_location(self):
        w, p, e = load()
        actor = p['通信士']
        for turn in (1, 2):
            e.execute(actor, choose(w, p, turn), turn=turn, day=1)
        spent_before = actor.stamina
        w.responses.state['battery'] = 'adrift'
        result, _, _ = e.execute(actor, choose(w, p, 3), turn=3, day=1)
        self.assertEqual(result, 'failed')
        self.assertEqual(actor.stamina, spent_before)
        self.assertLess(spent_before, actor.stamina_max)
        view = w.planner.view(w, actor)
        self.assertNotIn(('state', 'battery'), view)
        self.assertEqual(actor.beliefs['battery_stored'].value, 'false')
        self.assertNotIn('battery_location', actor.knowledge)
        plan = w.planner.search(w, actor, 'beacon_sent', 4)
        self.assertEqual(plan['status'], 'conditional')
        self.assertIn({'key': 'battery', 'expected': 'adrift'}, plan['assumptions'])

    def test_search_only_grants_location_knowledge_when_real_work_completes(self):
        w, p, e = load()
        for turn in range(1, 7):
            w.responses.tick(w, turn)
            e.execute(p['通信士'], choose(w, p, turn), turn=turn, day=1)
            if turn < 6:
                self.assertNotIn('battery_location', p['通信士'].knowledge)
        self.assertIn('battery_location', p['通信士'].knowledge)
        self.assertEqual(w.responses.state['battery'], 'located')
        self.assertEqual(w.responses.progress, {})

    def test_unavailable_alternatives_wait_without_declaring_goal_failed_early(self):
        raw, people = inputs()
        raw['responses']['actions'] = [s for s in raw['responses']['actions'] if s['id'] in {'retrieve_store', 'send_beacon'}]
        w, p, rows = episode(raw, people)
        decisions = [r for r in rows if r.get('kind') == 'decision' and r['subject'] == '通信士']
        self.assertTrue(any(r.get('details', {}).get('planning', {}).get('status') == 'no_known_plan' for r in decisions))
        actual = next(r for r in w.goal_rules.report(w) if r['condition'] == 'beacon_sent')
        self.assertEqual((actual['status'], actual['decided_at'], actual['reason']), ('failed', 20, 'deadline_unmet'))
        for row in decisions:
            if row['turn'] < 20:
                self.assertEqual(next(r for r in row['details']['objective_outcomes'] if r['condition'] == 'beacon_sent')['status'], 'pending')

    def test_bounded_search_failure_is_not_an_objective_failure(self):
        raw, people = inputs()
        raw['planning']['max_depth'] = 1
        raw['planning']['max_nodes'] = 16
        w, p, _ = load(raw, people)
        plan = w.planner.search(w, p['通信士'], 'beacon_sent', 1)
        self.assertEqual(plan['status'], 'search_limit')
        self.assertLessEqual(plan['expanded'], 16)
        self.assertEqual(w.goal_rules.actor_view(w, '通信士', 'beacon_sent', 1)['status'], 'pending')

    def test_changed_resources_reject_stale_planned_action(self):
        w, p, e = load()
        action = choose(w, p, 1)
        p['通信士'].inventory['予備電池'] = 1
        result, _, _ = e.execute(p['通信士'], action, turn=1, day=1)
        self.assertEqual(result, 'invalid')
        self.assertEqual(p['通信士'].zone, '操舵室')

    def test_existing_negotiation_can_be_an_automatic_intermediate_task(self):
        raw, people = inputs()
        people[1]['inventory']['予備電池'] = 1
        people[0]['commitments'] = people[0]['commitments'][:2]
        w, p, rows = episode(raw, people)
        self.assertTrue(any(r.get('verb') == 'negotiate' and r.get('result') == 'offered' for r in rows))
        self.assertTrue(any(r.get('verb') == 'concede' and r.get('result') == 'conceded' for r in rows))
        self.assertEqual(w.responses.state['beacon'], 'sent')
        self.assertNotIn('battery_owned', [c['condition'] for c in p['通信士'].commitments])
        self.assertTrue(p['補給係'].has_item('修理工具'))

    def test_receiver_refusal_does_not_force_trade_or_remove_other_means(self):
        raw, people = inputs()
        people[1]['inventory']['予備電池'] = 1
        people[0]['commitments'] = people[0]['commitments'][:2]
        people[1]['commitments'][1]['required'] = True
        w, p, rows = episode(raw, people)
        self.assertTrue(any(r.get('verb') == 'concede' and r.get('result') == 'refused' for r in rows))
        self.assertFalse(any(r.get('verb') == 'concede' and r.get('result') == 'conceded' for r in rows))
        self.assertEqual(w.responses.state['beacon'], 'sent')
        self.assertTrue(p['補給係'].has_item('予備電池'))

    def test_required_conditions_apply_to_every_prefix_of_plan(self):
        raw, people = inputs()
        recover = next(s for s in raw['responses']['actions'] if s['id'] == 'recover_space')
        recover['effects'].append({'kind': 'state', 'key': 'safety', 'value': 'unsafe'})
        # A later repair cannot excuse an earlier required safety breach.
        next(s for s in raw['responses']['actions'] if s['id'] == 'send_beacon')['effects'].append(
            {'kind': 'state', 'key': 'safety', 'value': 'safe'})
        w, p, rows = episode(raw, people)
        self.assertFalse(any(r.get('args') == ['recover_space'] for r in rows))
        self.assertEqual(w.responses.state['safety'], 'safe')
        self.assertEqual(w.responses.state['beacon'], 'unsent')

    def test_required_tool_retention_blocks_exchange_but_allows_recovery(self):
        raw, people = inputs()
        people[1]['inventory']['予備電池'] = 1
        people[0]['commitments'][2]['required'] = True
        w, p, rows = episode(raw, people)
        self.assertFalse(any(r.get('verb') == 'negotiate' for r in rows))
        self.assertEqual(w.responses.state['beacon'], 'sent')
        self.assertTrue(p['通信士'].has_item('修理工具'))

    def test_soft_values_rank_complete_plans_before_duration(self):
        raw, people = inputs(); people[1]['inventory']['予備電池'] = 1
        w, p, rows = episode(raw, people)
        self.assertEqual(w.responses.state['beacon'], 'sent')
        self.assertFalse(any(r.get('verb') == 'negotiate' for r in rows))
        self.assertTrue(p['通信士'].has_item('修理工具'))

    def test_incompatible_unknown_guards_cannot_both_be_assumed(self):
        raw, people = inputs()
        raw['responses']['state']['mode'] = 'A'; raw['responses']['locations']['mode'] = '航行管制'
        first = deepcopy(raw['responses']['actions'][0]); first.update(zone='操舵室', steps=1, preconditions={'mode': 'A'})
        first['effects'] = [{'kind': 'inventory', 'actor': '通信士', 'item': '予備電池', 'value': 1}]
        last = deepcopy(raw['responses']['actions'][-1]); last['preconditions'] = {'mode': 'B'}
        raw['responses']['actions'] = [first, last]
        w, p, _ = load(raw, people)
        self.assertEqual(w.planner.search(w, p['通信士'], 'beacon_sent', 1)['steps'], [])

    def test_joint_guard_failure_does_not_claim_to_know_which_clause_failed(self):
        raw, people = inputs()
        raw['responses']['state']['door'] = 'locked'; raw['responses']['locations']['door'] = '航行管制'
        raw['responses']['actions'][0]['preconditions']['door'] = 'open'
        w, p, engine = load(raw, people)
        engine.execute(p['通信士'], choose(w, p, 1), turn=1, day=1)
        result, _, _ = engine.execute(p['通信士'], choose(w, p, 2), turn=2, day=1)
        self.assertEqual(result, 'failed')
        self.assertEqual(p['通信士'].beliefs['battery_stored'].value, 'true')
        self.assertNotIn(('state', 'door'), w.planner.view(w, p['通信士']))
        self.assertTrue(w.planner.memory['通信士']['blocked'])
        json.dumps(w.planner.snapshot())

    def test_unreachable_high_priority_goal_does_not_block_safe_lower_goal(self):
        raw, people = inputs()
        raw['planning']['max_depth'] = 1
        raw['responses']['state']['backup'] = 'idle'; raw['responses']['locations']['backup'] = '操舵室'
        raw['concerns']['conditions'].append({'id': 'backup_done', 'label': '予備の作業', 'mode': 'achieve', 'visibility': 'observed',
            'test': {'kind': 'state', 'key': 'backup', 'equals': 'done'}})
        raw['goal_rules']['definitions'].append({'condition': 'backup_done', 'success': {'condition': 'backup_done'},
            'failure': {'constant': False}, 'period': {'start': 1, 'end': 20}})
        people[0]['commitments'].append({'condition': 'backup_done', 'priority': 4, 'required': False})
        raw['responses']['actions'].append({'id': 'backup_work', 'label': '予備の作業を終える', 'condition': 'backup_done',
            'signal': None, 'zone': '操舵室', 'actors': ['通信士'], 'requires': [], 'items': {}, 'cost': 1, 'steps': 1,
            'preconditions': {}, 'effects': [{'kind': 'state', 'key': 'backup', 'value': 'done'}]})
        w, p, _ = load(raw, people)
        self.assertEqual(choose(w, p, 1).args, ('backup_work',))

    def test_forged_derived_acquisition_binding_cannot_spend_or_transfer_items(self):
        raw, people = inputs(); people[1]['inventory']['予備電池'] = 1
        people[0]['commitments'] = people[0]['commitments'][:2]
        w, p, e = load(raw, people)
        actor = p['通信士']; actor.zone = '補給室'; w.responses.state['battery'] = 'adrift'
        actor.beliefs['battery_stored'].value = 'false'
        action = choose(w, p, 4)
        self.assertEqual(action.verb, 'negotiate')
        action.meta['objective'] = '繋留索'
        self.assertEqual(e.execute(actor, action, turn=4, day=1)[0], 'invalid')
        self.assertEqual(w.offers, {})
        fake = Action('negotiate', ('補給係',), {'planning_root': 'beacon_sent', 'objective': '予備電池'})
        self.assertEqual(e.execute(actor, fake, turn=4, day=1)[0], 'invalid')

    def test_actor_does_not_change_plan_using_receivers_private_requiredness(self):
        raw, people = inputs(); people[1]['inventory']['予備電池'] = 1
        w, p, _ = load(raw, people)
        p['通信士'].zone = '補給室'
        first = w.planner.search(w, p['通信士'], 'beacon_sent', 1)
        p['補給係'].commitments[1]['required'] = True
        self.assertEqual(w.planner.search(w, p['通信士'], 'beacon_sent', 1), first)

    def test_inventory_prerequisite_does_not_require_a_separate_named_ownership_predicate(self):
        raw, people = inputs(); people[1]['inventory']['予備電池'] = 1
        people[0]['commitments'] = people[0]['commitments'][:2]
        for spec in raw['responses']['actions']:
            if spec['condition'] == 'battery_owned': spec['condition'] = 'beacon_sent'
        raw['concerns']['conditions'] = [c for c in raw['concerns']['conditions'] if c['id'] != 'battery_owned']
        w, p, rows = episode(raw, people)
        self.assertEqual(w.responses.state['beacon'], 'sent')
        self.assertTrue(any(r.get('verb') == 'negotiate' for r in rows))

    def test_quantities_come_from_operator_requirements_and_forecast_stock_is_conserved(self):
        raw, people = inputs(); people[1]['inventory']['予備電池'] = 2
        people[0]['commitments'] = people[0]['commitments'][:2]
        people[0]['range']['entry'] = '補給室'
        raw['responses']['actions'] = [raw['responses']['actions'][-1]]
        raw['responses']['actions'][0]['items']['予備電池'] = 2
        w, p, _ = load(raw, people)
        plan = w.planner.search(w, p['通信士'], 'beacon_sent', 1)
        self.assertEqual(sum(s['verb'] == 'negotiate' for s in plan['steps']), 2)
        people[1]['inventory']['予備電池'] = 1
        w, p, _ = load(raw, people)
        self.assertEqual(w.planner.search(w, p['通信士'], 'beacon_sent', 1)['steps'], [])

    def test_information_trip_observes_current_state_on_arrival_not_from_remote_truth(self):
        raw, people = inputs()
        raw['responses']['state']['emergency'] = 'locked'
        raw['responses']['locations']['emergency'] = '操舵室'
        raw['responses']['events'][0]['effects'].append({'kind': 'state', 'key': 'emergency', 'value': 'ready'})
        raw['responses']['actions'] = [s for s in raw['responses']['actions'] if s['id'] in {'retrieve_store', 'send_beacon'}]
        direct = deepcopy(raw['responses']['actions'][-1]); direct.update(id='emergency_send', label='非常電源で送る', items={}, preconditions={'emergency': 'ready'})
        direct['effects'] = [{'kind': 'state', 'key': 'beacon', 'value': 'sent'}]
        raw['responses']['actions'].append(direct)
        w, p, rows = episode(raw, people)
        self.assertTrue(any(r.get('details', {}).get('planning', {}).get('status') == 'seeking_information' for r in rows))
        self.assertTrue(any(r.get('args') == ['emergency_send'] and r.get('result') == 'responded' for r in rows))
        self.assertEqual(w.responses.state['beacon'], 'sent')

    def test_two_resource_recipe_in_another_domain_uses_same_planner(self):
        w, p, rows = episode(project='village_planning')
        self.assertEqual(w.responses.state['beacon'], 'sent')
        self.assertTrue(any(r.get('args') == ['make_rope'] for r in rows))
        self.assertFalse(p['桃太郎'].has_item('縄'))
        self.assertFalse(p['桃太郎'].has_item('丸太'))
        self.assertEqual(len(p['桃太郎'].commitments), 3)

    def test_configuration_bounds_and_unknown_actors_are_rejected(self):
        for key, value in [('version', 2), ('version', True), ('max_nodes', 0), ('max_depth', 25), ('actors', [])]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                raw = {'version': 1, 'actors': ['A'], 'max_depth': 6, 'max_nodes': 128}; raw[key] = value
                GoalPlanner(raw)
        raw, people = inputs(); raw['planning']['actors'] = ['unknown']
        with self.assertRaises(ValueError): load(raw, people)

    def test_optional_feature_does_not_change_unenabled_worlds(self):
        from gapengine.objective_episode import load_episode
        w, p = load_episode(ROOT / 'projects/ship_negotiation/world.yaml')
        self.assertIsNone(w.planner)
        self.assertTrue(any(a.verb == 'negotiate' for a, _ in candidates(p['通信士'], w, SimpleNamespace(turn=1, day=1))))


if __name__ == '__main__':
    unittest.main()
