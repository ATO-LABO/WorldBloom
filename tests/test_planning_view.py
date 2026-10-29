from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from gapengine.explanations import explain_rows
from viewer import planning_view, review_pages
from viewer.server import STATIC_FILES


def step(name, verb='respond'):
    return {'verb': verb, 'args': [name], 'label': name, 'duration': 1}


def decision(turn, steps, *, result='moved', verb='move', args=None, actor='A', root='goal', reason='new_information', status='planned'):
    return {'subject': actor, 'turn': turn, 'line': turn * 2, 'source': {'line': turn * 2},
            'verb': verb, 'args': args or [steps[0]['args'][0] if steps else 'wait'],
            'outcome': {'result': result, 'details': {'planning': {
                'root': root, 'goal_label': root, 'steps': deepcopy(steps), 'reason': reason, 'status': status}}}}


def explanation(items):
    return {'source': {'experiment': 'run', 'cell': 'a|b'}, 'decisions': items}


class PlanningViewTests(unittest.TestCase):
    def test_native_completion_advances_the_plan_and_losses_are_attempt_failures(self):
        path=[step('boat','craft'),step('enemy','fight'),step('home','move')]
        rows=[decision(1,path,result='crafted',verb='craft'),
              decision(2,path[1:],result='lost',verb='fight'),
              decision(3,[step('enemy','observe')],result='observed',verb='observe',reason='previous_action_failed')]
        group,=planning_view.model(explanation(rows))
        self.assertEqual(group['events'][1]['kind'],'failure')
        self.assertEqual(group['events'][2]['kind'],'replan')
        html=planning_view.render(explanation(rows))
        self.assertIn('戦闘に敗れました',html)
        self.assertIn('製作完了',html)

    def test_wrong_accusation_is_a_failed_attempt_not_a_final_failure(self):
        item=decision(1,[step('suspect','confront')],result='misjudged',verb='confront')
        group,=planning_view.model(explanation([item]))
        self.assertEqual(group['events'][0]['kind'],'failure')
        html=planning_view.render(explanation([item]))
        self.assertIn('告発は誤りでした',html)
        self.assertIn('最終目的の判定とは分けて',html)

    def test_completed_scheduled_wait_is_progress_not_a_replan(self):
        path = [step('opening', 'wait'), step('scheduled work')]
        rows = [decision(1,path,result='waited',verb='wait'),
                decision(2,path[1:],result='working',verb='respond')]
        group, = planning_view.model(explanation(rows))
        self.assertNotEqual(group['events'][1]['kind'],'replan')

    def test_ordinary_progress_is_not_a_replan_even_when_reason_is_new_information(self):
        path = [step('store', 'move'), step('fetch'), step('home', 'move'), step('send')]
        rows = [decision(1, path), decision(2, path[1:], result='working', verb='respond'),
                decision(3, path[1:], result='responded', verb='respond'), decision(4, path[2:])]
        group, = planning_view.model(explanation(rows))
        self.assertEqual([e['kind'] for e in group['events']], ['initial', 'routine', 'complete', 'routine'])
        self.assertEqual(group['turning_count'], 2)

    def test_failure_replan_and_historical_verdict_are_distinct(self):
        old = [step('fetch'), step('send')]; new = [step('scan'), step('recover'), step('send')]
        rows = [decision(1, old, result='working', verb='respond'),
                decision(2, old, result='failed', verb='respond'),
                decision(3, new, result='working', verb='respond', reason='previous_action_failed')]
        ex = explanation(rows)
        ex['planning_outcomes'] = [
            {'turn': 0, 'source': {'line': 1}, 'outcome': {'condition': 'goal', 'status': 'pending'}},
            {'turn': 9, 'source': {'line': 99}, 'outcome': {'condition': 'goal', 'status': 'achieved', 'decided_at': 9}}]
        group, = planning_view.model(ex)
        self.assertEqual(group['events'][2]['kind'], 'replan')
        self.assertEqual(group['default'], 2)
        self.assertEqual(group['events'][2]['cause']['action'], 'fetch')
        self.assertEqual(group['events'][2]['actual']['status'], 'pending')
        self.assertEqual(group['events'][-1]['kind'], 'achieved')
        html = planning_view.render(ex)
        for text in ['見直し前', '組み直した計画', '失敗した手順', '追加', '作者向け', '世界の実態']:
            self.assertIn(text, html)

    def test_search_limit_never_creates_objective_failure(self):
        group, = planning_view.model(explanation([decision(1, [], result='rested', verb='rest', status='search_limit')]))
        self.assertEqual(group['final']['status'], 'unknown')
        self.assertEqual(group['events'][0]['kind'], 'pause')
        html = planning_view.render(explanation([decision(1, [], status='search_limit')]))
        self.assertIn('これだけで最終目的を失敗確定にはしません', html)

    def test_deadline_marker_after_last_choice_is_shown_separately(self):
        row = decision(1, [], status='no_known_plan')
        ex = explanation([row]); ex['planning_outcomes'] = [
            {'turn': 8, 'source': {'line': 16}, 'outcome': {'condition': 'goal', 'status': 'failed', 'decided_at': 8, 'reason': 'deadline_unmet'}}]
        group, = planning_view.model(ex)
        self.assertEqual([(e['turn'], e['actual']['status']) for e in group['events']], [(1, 'unknown'), (8, 'failed')])
        self.assertIn('期限までに達成条件を満たさなかった', planning_view.render(ex))

    def test_exchange_reply_binds_only_to_matching_claimant_peer_and_asset(self):
        offer = decision(1, [step('B', 'negotiate'), step('send')], result='offered', verb='negotiate', args=['B'])
        offer['outcome']['details']['objective'] = 'battery'
        reply = {'subject': 'B', 'turn': 2, 'line': 5, 'source': {'line': 5}, 'verb': 'concede', 'args': ['A'],
                 'outcome': {'result': 'refused', 'details': {'claimant': 'A', 'objective': 'battery'}}}
        wrong = deepcopy(reply); wrong['outcome']['details']['objective'] = 'other'
        group, = planning_view.model(explanation([offer, wrong]))
        self.assertEqual(len(group['events']), 1)
        follow = decision(3, [step('recover'), step('send')], verb='respond', reason='reply_received')
        group, = planning_view.model(explanation([offer, reply, follow]))
        self.assertEqual([e['kind'] for e in group['events']], ['offer', 'refused', 'replan'])
        self.assertEqual(group['events'][-1]['cause']['kind'], 'refused')

    def test_accepted_offer_does_not_create_spurious_replan(self):
        offer = decision(1, [step('B', 'negotiate'), step('send')], result='offered', verb='negotiate', args=['B'])
        offer['outcome']['details']['objective'] = 'battery'
        waiting = decision(2, [], result='rested', verb='rest', status='awaiting_consent')
        reply = {'subject': 'B', 'turn': 2, 'line': 5, 'source': {'line': 5}, 'verb': 'concede', 'args': ['A'],
                 'outcome': {'result': 'conceded', 'details': {'claimant': 'A', 'objective': 'battery'}}}
        follow = decision(3, [step('send')], result='working', verb='respond', reason='reply_received')
        group, = planning_view.model(explanation([offer, waiting, reply, follow]))
        self.assertNotIn('replan', [e['kind'] for e in group['events']])

    def test_new_information_does_not_blame_an_old_failure(self):
        rows = [decision(1, [step('fetch')], result='failed', verb='respond'),
                decision(2, [step('scan')], reason='previous_action_failed'),
                decision(3, [step('other')], reason='new_information')]
        group, = planning_view.model(explanation(rows))
        self.assertIsNone(group['events'][-1]['cause'])

    def test_group_identity_and_escaping(self):
        ex = explanation([decision(1, [step('<img>')], actor='<script>'),
                          decision(2, [step('other')], actor='B'), decision(3, [step('x')], root='other')])
        self.assertEqual(len(planning_view.model(ex)), 3)
        html = planning_view.render(ex)
        self.assertIn('&lt;script&gt;', html); self.assertNotIn('<img>', html)
        self.assertIn('/raw?line=2#L2', html)
        ex['source'] = {}
        self.assertNotIn('/raw?', planning_view.render(ex))

    def test_extraction_preserves_terminal_events_only_for_planning_runs(self):
        rows = [{'kind': 'header'}, {'kind': 'decision', 'subject': 'A', 'turn': 1, 'verb': 'rest',
                 'args': [], 'result': 'rested', 'delta': {}, 'details': {'planning': {'root': 'goal'}}},
                {'kind': 'event', 'verb': 'objective_outcome', 'turn': 8,
                 'details': {'objective_outcome': {'condition': 'goal', 'status': 'failed', 'decided_at': 8}}}]
        args = dict(layers_path='layers.jsonl', sha256='x', experiment='run', cell='cell')
        ex = explain_rows(rows, **args)
        self.assertEqual(ex['planning_outcomes'][0]['source']['line'], 3)
        rows[1]['details'] = {}
        self.assertNotIn('planning_outcomes', explain_rows(rows, **args))

    def test_candidate_optional_tab_assets_and_legacy_tabs(self):
        basic = {'quality': .5, 'reach_rate': 0, 'seeds': [], 'explanation': {}}
        with patch('viewer.review_pages.data.phase_status', return_value={}):
            html = review_pages.candidate(SimpleNamespace(catalog=None), 'run', 'cell', basic, '', '', '', [])
            self.assertNotIn('data-ux-tab="plan"', html)
            self.assertNotIn('/static/planning-workspace', html)
            basic['explanation'] = explanation([decision(1, [step('send')])])
            html = review_pages.candidate(SimpleNamespace(catalog=None), 'run', 'cell', basic, '', '', '', [])
        self.assertIn('data-ux-tab="plan"', html)
        self.assertIn('data-ux-panel="plan" hidden', html)
        for asset in ['planning-workspace.css', 'planning-workspace.js']:
            self.assertIn(asset, STATIC_FILES); self.assertIn('/static/' + asset, html)
        self.assertIn('data-ux-tab="story"', review_pages.tabs())
        self.assertNotIn('data-ux-tab="plan"', review_pages.tabs())

    def test_saved_log_to_candidate_route_preserves_bytes_and_terminal_marker(self):
        from test_viewer import _create_experiment
        from viewer import pages, data
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'runs'; exp = _create_experiment(root)
            path = exp / 'g0/ind-0/seed-7/layers.jsonl'
            rows = [json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
            own = next(r for r in rows if r.get('kind') == 'decision')
            own.setdefault('details', {})['planning'] = {'root': 'goal', 'goal_label': '救助を完了',
                'status': 'planned', 'steps': [step('send')], 'reason': 'initial_plan'}
            rows.append({'kind': 'event', 'turn': 20, 'subject': own['subject'], 'verb': 'objective_outcome',
                'details': {'objective_outcome': {'condition': 'goal', 'status': 'failed', 'decided_at': 20,
                    'reason': 'deadline_unmet'}}, 'delta': {}, 'result': 'failed'})
            path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf8')
            before = {p: p.read_bytes() for p in exp.rglob('*') if p.is_file()}
            html = pages.cell_page(data.RunRepository(root), exp.name, 'III|high', view='digest')
            self.assertIn('data-ux-tab="plan"', html)
            self.assertIn('救助を完了', html)
            self.assertIn('期限までに達成条件を満たさなかった', html)
            self.assertEqual(before, {p: p.read_bytes() for p in exp.rglob('*') if p.is_file()})


if __name__ == '__main__': unittest.main()
