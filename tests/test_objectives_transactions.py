"""Causal item transactions, priorities, independent consent and old contracts."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import random
import tempfile
import unittest

from engine.actions import Action, candidates
from engine.objective_actions import review_transaction
from engine.sim import Simulation
from engine.subject import Subject, Belief
from engine.verbs import VerbEngine
from engine.world import World

ROOT = Path(__file__).resolve().parents[1]


def inputs(project='village_negotiation'):
    folder = ROOT / 'projects' / project
    return (json.loads((folder / 'world.yaml').read_text(encoding='utf8')),
            [json.loads(p.read_text(encoding='utf8')) for p in sorted((folder / 'subjects').glob('*.yaml'))])


def load(raw=None, people=None, project='village_negotiation'):
    original, cast = inputs(project)
    w = World(deepcopy(raw if raw is not None else original), ROOT / 'projects' / project / 'world.yaml')
    ps = {p['id']: Subject.from_definition(p) for p in deepcopy(cast if people is None else people)}
    w.bind_subjects(ps)
    w.goal_rules.record(w, 1)
    return w, ps, VerbEngine(w, random.Random(0))


def options(w, p, who='鬼', turn=1):
    return candidates(p[who], w, SimpleNamespace(turn=turn, day=1))


def select(w, p, verb, who='鬼', turn=1):
    return next(a for a, _ in options(w, p, who, turn) if a.verb == verb)


class ObjectiveTransactionTests(unittest.TestCase):
    def test_priority_alone_switches_means_without_changing_genes_or_legacy_goal(self):
        w, p, _ = load()
        self.assertIsNone(p['鬼'].goal.target)
        self.assertIn('negotiate', [a.verb for a, _ in options(w, p)])
        self.assertNotIn('respond', [a.verb for a, _ in options(w, p)])
        genes = deepcopy(p['鬼'].traits)
        p['鬼'].commitments[2]['priority'] = 9
        self.assertNotIn('negotiate', [a.verb for a, _ in options(w, p)])
        self.assertIn('respond', [a.verb for a, _ in options(w, p)])
        self.assertEqual(p['鬼'].traits, genes)

    def test_required_condition_overrides_priority_and_both_block_all_means(self):
        w, p, _ = load()
        p['鬼'].commitments[2]['priority'] = 9
        p['鬼'].commitments[1]['required'] = True
        self.assertEqual({a.verb for a, _ in options(w, p)}, {'rest', 'negotiate'})
        p['鬼'].commitments[2]['required'] = True
        self.assertEqual({a.verb for a, _ in options(w, p)}, {'rest'})

    def test_offer_is_conditional_not_achievement_and_concession_conserves_both_items(self):
        w, p, engine = load()
        action = select(w, p, 'negotiate')
        self.assertTrue(action.meta['objective_choice']['consent_required'])
        result, details, _ = engine.execute(p['鬼'], action, turn=1, day=1)
        self.assertEqual(result, 'offered')
        self.assertEqual(next(r for r in details['objective_outcomes'] if r['condition'] == 'food_taken')['status'], 'pending')
        result, details, _ = engine.execute(p['倉番'], select(w, p, 'concede', '倉番'), turn=1, day=1)
        self.assertEqual((result, details['mode']), ('conceded', 'trade'))
        self.assertEqual(p['鬼'].inventory, {'食料袋': 1})
        self.assertEqual(p['倉番'].inventory, {'工芸品': 1})
        self.assertTrue(all(sum(a.inventory.get(i, 0) for a in p.values()) == 1 for i in ['食料袋', '工芸品']))

    def test_receiver_uses_own_required_condition_and_claimant_cannot_read_it(self):
        w, p, engine = load()
        before = options(w, p)
        p['倉番'].commitments[1]['required'] = True
        self.assertEqual(options(w, p), before)
        engine.execute(p['鬼'], select(w, p, 'negotiate'), turn=1, day=1)
        answer = select(w, p, 'concede', '倉番')
        self.assertEqual(answer.meta['objective_choice']['answer'], 'refuse')
        result, _, _ = engine.execute(p['倉番'], answer, turn=1, day=1)
        self.assertEqual(result, 'refused')
        self.assertTrue(p['倉番'].has_item('食料袋'))
        self.assertTrue(p['鬼'].has_item('工芸品'))
        self.assertIn('respond', [a.verb for a, _ in options(w, p, turn=2)])

    def test_pending_offer_waits_for_answer_without_treating_consent_as_fact(self):
        w, p, e = load()
        e.execute(p['鬼'], select(w, p, 'negotiate'), turn=1, day=1)
        choices = options(w, p, turn=2)
        self.assertFalse(any(a.verb in {'negotiate', 'respond'} for a, _ in choices))
        self.assertTrue(any(a.meta.get('objective_wait') for a, _ in choices))
        # A silent counterparty does not suspend the world indefinitely.
        self.assertIn('respond', [a.verb for a, _ in options(w, p, turn=3)])

    def test_stock_knowledge_and_colocation_required_not_hidden_inventory(self):
        raw, people = inputs()
        raw['concerns']['conditions'][3]['visibility'] = 'observed'
        w, p, _ = load(raw, people)
        self.assertFalse(any(a.verb == 'negotiate' for a, _ in options(w, p)))
        p['鬼'].beliefs['stock_food'] = Belief('true', .8)
        self.assertTrue(any(a.verb == 'negotiate' for a, _ in options(w, p)))
        before = options(w, p)
        p['倉番'].inventory.clear()
        self.assertEqual(options(w, p), before)
        p['倉番'].zone = 'elsewhere'
        self.assertFalse(any(a.verb == 'negotiate' for a, _ in options(w, p)))

    def test_expiry_and_goal_deadline_revalidated_at_execution(self):
        w, p, e = load()
        a = select(w, p, 'negotiate')
        e.execute(p['鬼'], a, turn=1, day=1)
        stale = Action('concede', ('鬼',))
        self.assertEqual(e.execute(p['倉番'], stale, turn=3, day=1)[0], 'invalid')
        self.assertNotIn('negotiate', [a.verb for a, _ in options(w, p, turn=12)])
        self.assertEqual(e.execute(p['鬼'], a, turn=12, day=1)[0], 'invalid')
        self.assertTrue(p['倉番'].has_item('食料袋'))

    def test_original_payment_cannot_be_silently_replaced_by_new_assets(self):
        w, p, e = load()
        e.execute(p['鬼'], select(w, p, 'negotiate'), turn=1, day=1)
        p['鬼'].inventory.clear()
        result, _, _ = e.execute(p['倉番'], Action('concede', ('鬼',)), turn=1, day=1)
        self.assertEqual(result, 'refused')
        self.assertTrue(p['倉番'].has_item('食料袋'))

    def test_changed_claimant_conditions_revalidate_original_consent(self):
        w, p, e = load()
        e.execute(p['鬼'], select(w, p, 'negotiate'), turn=1, day=1)
        p['鬼'].commitments[2]['required'] = True
        result, details, _ = e.execute(p['倉番'], Action('concede', ('鬼',)), turn=1, day=1)
        self.assertEqual((result, details['reason']), ('refused', 'claimant_consent_no_longer_valid'))
        self.assertTrue(p['鬼'].has_item('工芸品'))

    def test_guard_cannot_be_bypassed_by_forging_action_metadata(self):
        w, p, e = load()
        p['鬼'].commitments[2]['required'] = True
        fake = Action('negotiate', ('倉番',), {'objective_goal': 'food_taken', 'objective_choice': {'excluded': []}})
        result, _, _ = e.execute(p['鬼'], fake, turn=1, day=1)
        self.assertEqual(result, 'invalid')
        self.assertEqual(w.offers, {})

    def test_gift_is_generated_from_public_recipient_goal_and_guarded_by_donor_values(self):
        raw, people = inputs()
        raw['concerns']['conditions'][4]['visibility'] = 'public'
        people[0]['verbs'] = ['rest', 'give_item']
        people[0]['commitments'].append({'condition': 'keeper_paid', 'priority': 10, 'required': False})
        w, p, e = load(raw, people)
        a = select(w, p, 'give_item')
        self.assertEqual(a.args, ('倉番', '工芸品'))
        p['鬼'].commitments[2]['required'] = True
        self.assertEqual(e.execute(p['鬼'], a, turn=1, day=1)[0], 'invalid')
        self.assertTrue(p['鬼'].has_item('工芸品'))

    def test_partial_inventory_progress_is_not_full_goal_success(self):
        raw, people = inputs()
        raw['concerns']['conditions'][0]['test']['minimum'] = 2
        people[1]['inventory']['食料袋'] = 2
        w, p, e = load(raw, people)
        e.execute(p['鬼'], select(w, p, 'negotiate'), turn=1, day=1)
        e.execute(p['倉番'], select(w, p, 'concede', '倉番'), turn=1, day=1)
        self.assertEqual(p['鬼'].inventory.get('食料袋'), 1)
        self.assertEqual(next(r for r in w.goal_rules.report(w) if r['condition'] == 'food_taken')['status'], 'pending')

    def test_no_sufficient_offer_generates_independent_refusal_even_without_person_values(self):
        w, p, e = load()
        p['倉番'].commitments = []
        p['鬼'].inventory.clear()
        e.execute(p['鬼'], select(w, p, 'negotiate'), turn=1, day=1)
        a = select(w, p, 'concede', '倉番')
        self.assertEqual(e.execute(p['倉番'], a, turn=1, day=1)[0], 'refused')

    def test_phase_and_genre_unavailable_transaction_does_not_suppress_viable_response(self):
        w, p, _ = load()
        p['鬼'].verbs.remove('negotiate')
        self.assertIn('respond', [a.verb for a, _ in options(w, p)])

    def test_explanation_separates_consent_from_achievement_and_escapes_authored_labels(self):
        from viewer.explanation_ui import objective_choice_panel
        w, p, _ = load()
        choice = select(w, p, 'negotiate').meta['objective_choice']
        choice['forecast']['entries'][0]['label'] = '<script>food</script>'
        html = objective_choice_panel(choice)
        self.assertIn('相手が同意した場合の見込み', html)
        self.assertIn('目的も達成しません', html)
        self.assertIn('本人の優先順位で別の手段を選んだ', html)
        self.assertIn('&lt;script&gt;food&lt;/script&gt;', html)
        self.assertNotIn('<script>', html)

    def test_multiple_visible_holders_do_not_use_global_first_owner(self):
        raw, people = inputs()
        extra = deepcopy(people[1]); extra['id'] = '別の倉番'; extra['commitments'] = []
        people.append(extra)
        stock = deepcopy(raw['concerns']['conditions'][3]); stock['id'] = 'second_stock'
        stock['test']['actor'] = extra['id']; raw['concerns']['conditions'].append(stock)
        w, p, _ = load(raw, people)
        self.assertEqual({a.args[0] for a, _ in options(w, p) if a.verb == 'negotiate'}, {'倉番', '別の倉番'})

    def test_explanation_does_not_present_other_peoples_operators_as_own_alternatives(self):
        from viewer.explanation_ui import objective_choice_panel
        choice = {'alternatives': [
            {'verb': 'respond', 'args': ['他人専用'], 'excluded': ['capability_unavailable']},
            {'verb': 'respond', 'args': ['無関係な目的'], 'excluded': ['no_commitment']},
            {'verb': 'respond', 'args': ['自身の選択'], 'excluded': ['missing_resources', 'missing_evidence']}]}
        html = objective_choice_panel(choice)
        self.assertNotIn('他人専用', html)
        self.assertNotIn('無関係な目的', html)
        self.assertIn('自身の選択', html)
        self.assertNotIn('実行条件を満たさない・実行条件を満たさない', html)

    def test_other_domain_requires_actual_trade_then_consumes_battery_to_send_beacon(self):
        with tempfile.TemporaryDirectory() as temp:
            w, p, _ = load(project='ship_negotiation')
            sim = Simulation(0, w, p, Path(temp), record_explanations=True)
            rows = [json.loads(s) for s in sim.run().read_text(encoding='utf8').splitlines()]
            ds = [r for r in rows if r.get('kind') == 'decision']
            self.assertTrue(any(r['verb'] == 'negotiate' and r['result'] == 'offered' for r in ds))
            self.assertTrue(any(r['verb'] == 'concede' and r['result'] == 'conceded' for r in ds))
            self.assertEqual(w.responses.state['beacon'], 'sent')
            self.assertFalse(p['通信士'].has_item('予備電池'))
            self.assertEqual(w.responses.state['damage'], 'no')
            self.assertFalse(any('forced' in r for r in ds))


if __name__ == '__main__':
    unittest.main()
