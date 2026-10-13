"""Real temporal execution, shared world state and checkpoint transaction tests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.calendar_checkpoint import canonical, digest
from engine.calendar_v2 import minute_for
from engine.sim import Simulation
from scripts.cultural_festival_v2 import PROJECT, ORIGINAL, choices, freeze_design, load, write_fixture


class CalendarRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def simulation(self, name, commands=None, *, project=PROJECT):
        world, people = load(project)
        sim = Simulation(0, world, people, self.root/name)
        sim.slot_runtime.configure([] if commands is None else commands)
        return sim

    def clock(self, day, clock):
        sim = self.simulation('clock')
        return minute_for(sim.slot_runtime.calendar, day, clock)

    def execute(self, commands, name='run', *, until=None, project=PROJECT, lead=None):
        if lead:
            world, people = load(project)
            world.slot_contract['play']['lead'] = lead
            sim = Simulation(0, world, people, self.root/name)
            sim.slot_runtime.configure(commands)
        else:
            sim = self.simulation(name, commands, project=project)
        sim.slot_runtime.run(until=until)
        return sim.slot_runtime

    def rows(self, runtime, verb=None):
        return [r for r in runtime.rows if verb is None or r['verb'] == verb]

    def test_shared_original_people_and_legacy_inputs_remain_exact(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in ORIGINAL.rglob('*.yaml')}
        world, people = load()
        original = {p.id: p for p in [__import__('engine.subject', fromlist=['Subject']).Subject.from_yaml(f)
                                    for f in (ORIGINAL/'subjects').glob('*.yaml')]}
        for name, person in original.items():
            self.assertEqual(person.traits, people[name].traits)
            self.assertEqual(person.inventory, people[name].inventory)
            self.assertEqual(person.knowledge, people[name].knowledge)
            self.assertEqual(person.goal, people[name].goal)
        self.assertEqual(set(world.slot_contract['roles']), set(people))
        sim = Simulation(0, world, people, self.root/'shared')
        self.assertIs(sim.slot_runtime.people, sim.subjects)
        sim.slot_runtime.configure([]); sim.slot_runtime.run()
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_three_real_cases_same_world_different_work_burden_and_remaining_role(self):
        records = []
        for case in ('fulfilled', 'partial', 'renegotiated'):
            world, _ = load(); commands = choices(world, case)
            rt = self.execute(commands, case)
            self.assertFalse([r for r in rt.rows if r['result'] in ('failed', 'rejected')])
            summary = json.loads((rt.store.out/'summary.json').read_text())
            records.append((rt, summary))
        self.assertEqual(len({digest(rt.contract) for rt, _ in records}), 1)
        self.assertEqual(len({digest(rt.initial) for rt, _ in records}), 1)
        full, partial, revised = records
        self.assertEqual(full[0].registry.get('p-panel')['status'], 'fulfilled')
        self.assertEqual(partial[0].registry.get('p-panel')['status'], 'breached')
        self.assertEqual(revised[0].registry.get('p-panel', 1)['status'], 'renegotiated')
        self.assertEqual(revised[0].registry.get('p-panel', 2)['status'], 'fulfilled')
        self.assertEqual([s['dependencies']['akari-reception']['burden_minutes'] for _, s in records], [0, 20, 20])
        self.assertEqual([s['remaining_work']['reception-signs']['minutes'] for _, s in records], [0, 30, 0])
        self.assertEqual(revised[1]['dependencies']['akari-reception']['version'], 2)
        self.assertEqual(revised[1]['dependencies']['akari-reception']['prior_plan']['version'], 1)
        self.assertEqual(partial[0].ledger['tasks']['panel-cards']['contributions'], {'蒼': 2, '灯': 2})
        self.assertEqual(partial[0].registry.get('p-panel')['progress'][1]['completed_units'], 2)
        self.assertTrue(all(s['api_calls'] == 0 and not s['gpu'] for _, s in records))
        for rt, _ in records:
            self.assertTrue(all(any(r.get('subject') == p and r['verb'] == 'action_started' for r in rt.rows)
                                for p in rt.people))

    def test_arrival_1459_1500_1501_and_start_at_opening(self):
        for clock, expected in [('14:49', True), ('14:50', True), ('14:51', False), ('15:00', False)]:
            with self.subTest(clock=clock):
                at = self.clock('2026-10-15', clock)
                rt = self.execute([{'id': 'departure', 'actor': '紗季', 'at': at, 'verb': 'move', 'to': '体育館'}],
                                  'arrival-'+clock.replace(':', ''), lead='紗季')
                # Saki is used as lead just for this runtime check; no original
                # subject/contract data is modified.
                observation = next(r for r in rt.rows if r['id'] == 'play-opening-observation')
                move = next(r for r in rt.rows if r['id'] == 'complete:departure')
                self.assertEqual(move['absolute_minute']-at, 10)
                self.assertEqual(move['details']['arrived_at'] <= rt.opening['at'], expected)
                self.assertEqual(rt.opening['primary_present'], expected)
                if expected:
                    self.assertLess(rt.rows.index(move), rt.rows.index(observation))
                else:
                    self.assertLess(rt.rows.index(observation), rt.rows.index(move))

    def test_display_refinement_and_multiple_specials_do_not_change_physical_minutes(self):
        projects = []
        for interval in (60, 7):
            project = self.root/('project'+str(interval)); write_fixture(project)
            import yaml
            p = project/'contract_v2.yaml'; contract = yaml.safe_load(p.read_text())
            for band in contract['calendar']['normal']['bands']:
                band['interval_minutes'] = interval
            extra = deepcopy(contract['calendar']['specials'][0])
            extra['id'] = 'first-special'; extra['selector'] = {'kind': 'first', 'offset': 0}
            extra['priority'] = 20
            for b in extra['bands']:
                b['interval_minutes'] = interval
            contract['calendar']['specials'].append(extra)
            p.write_text(yaml.safe_dump(contract, allow_unicode=True))
            commands = [{'id': 'ten', 'actor': '紗季', 'at': 510, 'verb': 'move', 'to': '体育館'}]
            projects.append(self.execute(commands, 'split-'+str(interval), project=project))
        for rt in projects:
            completion = next(r for r in rt.rows if r['id'] == 'complete:ten')
            self.assertEqual(completion['absolute_minute'], 520)
        self.assertEqual(projects[0]._people(), projects[1]._people())

    def test_two_actors_parallel_and_resource_winner_is_choice_id_not_array_order(self):
        commands = [
            {'id': 'z-lamp', 'actor': '灯', 'at': 500, 'verb': 'work', 'task_id': 'panel-cards', 'minutes': 10},
            {'id': 'a-ao-move', 'actor': '蒼', 'at': 480, 'verb': 'move', 'to': '校門'},
            {'id': 'a-ao-club', 'actor': '蒼', 'at': 490, 'verb': 'move', 'to': '部室'},
            {'id': 'a-ao-exhibit', 'actor': '蒼', 'at': 500, 'verb': 'move', 'to': '展示室'},
            {'id': 'a-ao-work', 'actor': '蒼', 'at': 510, 'verb': 'work', 'task_id': 'panel-cards', 'minutes': 10},
            {'id': 'z-lamp-work', 'actor': '灯', 'at': 510, 'verb': 'work', 'task_id': 'panel-headline', 'minutes': 20},
            {'id': 'parallel-saki', 'actor': '紗季', 'at': 510, 'verb': 'work', 'task_id': 'costume-ready', 'minutes': 20}]
        one = self.execute(commands, 'one'); two = self.execute(list(reversed(commands)), 'two')
        self.assertEqual(one.export_state(), two.export_state())
        self.assertTrue(any(r['id'] == 'reject:z-lamp-work' and r['details']['reason'] == 'resource_busy' for r in one.rows))
        self.assertEqual(one.ledger['tasks']['costume-ready']['units'], 1)
        self.assertEqual(one.ledger['tasks']['panel-cards']['contributions'], {'灯': 1, '蒼': 1})

    def test_night_receipt_separate_read_and_new_day_preserves_prior_state(self):
        terms = {'promise_id': 'night', 'version': 1, 'proposer': '蒼', 'promisor': '蒼', 'beneficiary': '灯',
                 'acceptance_parties': ['蒼', '灯'], 'place': '展示室', 'start': 1300, 'deadline': 2500,
                 'grace_until': None, 'requirements': [{'task_id': 'panel-cards', 'units': 1, 'contribution_by': '蒼'}]}
        rt = self.execute([{'id': 'night-offer', 'actor': '蒼', 'at': 1319, 'verb': 'offer', 'terms': terms}],
                          until=1440)
        self.assertIn('night:1', rt.known_terms['蒼'])
        self.assertNotIn('night:1', rt.known_terms['灯'])
        self.assertTrue(rt.pending_read)
        current = self.simulation('run', [{'id': 'night-offer', 'actor': '蒼', 'at': 1319, 'verb': 'offer', 'terms': terms}])
        current.slot_runtime.run(resume=True, until=1800)
        self.assertIn('night:1', current.slot_runtime.known_terms['灯'])
        self.assertTrue(any(r['verb'] == 'message_read' and r['absolute_minute'] == 1800 for r in current.slot_runtime.rows))
        self.assertEqual(current.slot_runtime.registry.get('night')['status'], 'offered')

    def test_save_resume_while_working_next_day_and_noop_byte_exact(self):
        world, _ = load(); commands = choices(world, 'fulfilled')
        continuous = self.execute(commands, 'continuous')
        stop = self.clock('2026-10-14', '16:05')
        split = self.execute(commands, 'split', until=stop)
        self.assertTrue(split.busy)
        self.assertEqual(split.ledger['tasks']['panel-cards']['effort_minutes'], 5)
        resumed = self.simulation('split', commands)
        resumed.slot_runtime.run(resume=True)
        for name in ('layers.jsonl', 'summary.json', 'calendar-state.json'):
            self.assertEqual((continuous.store.out/name).read_bytes(), (resumed.out_dir/name).read_bytes())
        before = {p.name: p.read_bytes() for p in resumed.out_dir.iterdir()}
        self.simulation('split', commands).slot_runtime.run(resume=True)
        self.assertEqual(before, {p.name: p.read_bytes() for p in resumed.out_dir.iterdir()})
        effects = [r['id'] for r in resumed.slot_runtime.rows]
        self.assertEqual(len(effects), len(set(effects)))

    def test_physical_completion_at_deadline_precedes_cancel_or_renegotiation(self):
        world, _ = load(); commands = choices(world, 'fulfilled')
        # Term deadline and photo receipt exactly coincide; a simultaneous
        # cancellation attempt must see real fulfillment, not a false breach.
        offer = next(c for c in commands if c['id'] == 'panel-offer')
        offer['terms']['deadline'] = self.clock('2026-10-14', '16:44')
        from engine.promises_v2 import PromiseRegistry
        th = PromiseRegistry(set(world.slot_contract['roles']), set(world.slot_contract['tasks']), set(world.zones)).terms_hash(offer['terms'])
        for c in commands:
            if c['verb'] == 'accept':
                c['terms_hash'] = th
        commands += [{'id': 'cancel-a', 'actor': '灯', 'at': self.clock('2026-10-14', '16:42'),
                      'verb': 'cancel', 'promise_id': 'p-panel', 'version': 1, 'terms_hash': th}]
        rt = self.execute(commands)
        self.assertEqual(rt.registry.get('p-panel')['status'], 'fulfilled')
        self.assertTrue(rt.registry.get('p-panel')['due_met'])
        terminal = [r for r in self.rows(rt, 'promise_transition') if r['details']['promise_transition']['status'] == 'fulfilled']
        self.assertEqual(len(terminal), 1)

    def test_existing_output_bad_choice_changed_input_and_corruption_refuse_without_overwrite(self):
        sim = self.simulation('owned'); sim.slot_runtime.run(until=500)
        before = {p.name: p.read_bytes() for p in sim.out_dir.iterdir()}
        with self.assertRaises(ValueError):
            self.simulation('owned').slot_runtime.run()
        with self.assertRaises(ValueError):
            self.simulation('owned', [{'id': 'different', 'actor': '灯', 'at': 510, 'verb': 'wait', 'minutes': 1}]).slot_runtime.run(resume=True)
        self.assertEqual(before, {p.name: p.read_bytes() for p in sim.out_dir.iterdir()})
        for bad in (True, 0, -1):
            with self.subTest(minutes=bad), self.assertRaises(ValueError):
                self.simulation('invalid', [{'id': 'bad', 'actor': '蒼', 'at': 500, 'verb': 'work',
                                            'task_id': 'panel-cards', 'minutes': bad}])
        state = sim.out_dir/'calendar-state.json'; raw = state.read_bytes()
        state.write_bytes(raw[:-3])
        with self.assertRaises(ValueError):
            self.simulation('owned').slot_runtime.run(resume=True)
        self.assertEqual(state.read_bytes(), raw[:-3])

    def test_atomic_checkpoint_replace_failure_keeps_prior_whole_state_and_replay(self):
        sim = self.simulation('atomic')
        sim.slot_runtime.run(until=500)
        before = (sim.out_dir/'calendar-state.json').read_bytes()
        current = self.simulation('atomic')
        with patch('engine.calendar_checkpoint.os.replace', side_effect=OSError('injected disk failure')):
            with self.assertRaises(OSError):
                current.slot_runtime.run(resume=True, until=510)
        self.assertEqual((sim.out_dir/'calendar-state.json').read_bytes(), before)
        resumed = self.simulation('atomic'); resumed.slot_runtime.run(resume=True)
        self.assertTrue(resumed.slot_runtime.done)
        self.assertFalse([p for p in sim.out_dir.iterdir() if p.name.startswith('.calendar-state.json-')])

    def test_pr9_design_transfer_new_fixed_version_then_actual_simulation(self):
        settings = deepcopy(self.simulation('settings').slot_runtime.contract['calendar'])
        original_bytes = canonical(settings)
        version = freeze_design(self.root/'fixed-project', settings)
        self.assertTrue(version['execution_ready'])
        self.assertEqual(original_bytes, canonical(settings))
        sim = self.simulation('fixed-run', project=self.root/'fixed-project')
        sim.slot_runtime.run()
        self.assertTrue(sim.slot_runtime.done)
        with self.assertRaises(ValueError):
            freeze_design(self.root/'fixed-project', settings)
        p = self.root/'fixed-project/contract_v2.yaml'
        p.write_text(p.read_text()+'\n# tampered frozen bytes\n')
        with self.assertRaises(ValueError):
            load(self.root/'fixed-project')

    def test_31_calendar_days_actual_runtime_without_search_or_daily_resets(self):
        project = self.root/'long'; write_fixture(project, prep_days=30)
        sim = self.simulation('long-run', project=project)
        cal = sim.slot_runtime.calendar
        self.assertEqual(len(cal['days']), 31)
        self.assertEqual(sim.world.days, 31)
        self.assertEqual(next(e for e in cal['events'] if e['id'] == 'schedule-3')['day'], 31)
        sim.slot_runtime.configure([{'id': 'long-move', 'actor': '紗季',
                                    'at': minute_for(cal, cal['start_date'], '08:00'), 'verb': 'move', 'to': '体育館'}])
        sim.slot_runtime.run()
        self.assertEqual(sim.subjects['紗季'].zone, '体育館')
        self.assertTrue(sim.slot_runtime.done)
        self.assertEqual(sim.slot_runtime.opening['at'], minute_for(cal, cal['end_date'], '15:00'))

    def test_bad_checkpoint_validation_never_partially_changes_live_state(self):
        rt = self.simulation('validation').slot_runtime
        original = rt.export_state()
        for field, replacement in [('rng', []), ('promises', {}), ('rows', [None]), ('busy', {'unknown': {}}),
                                   ('ledger', {'tasks': {}, 'materials': {}, 'completion_events': []})]:
            state = deepcopy(original)
            state['people']['蒼']['zone'] = '校門'
            state[field] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                rt.restore_state(state)
            self.assertEqual(rt.export_state(), original)

    def test_unseen_acceptance_cannot_create_dependent_plan(self):
        rt = self.simulation('private').slot_runtime
        terms = {'promise_id': 'remote', 'version': 1, 'proposer': '蒼', 'promisor': '蒼', 'beneficiary': '灯',
                 'acceptance_parties': ['蒼', '灯'], 'place': '展示室', 'start': 360, 'deadline': 1000,
                 'grace_until': None, 'requirements': [{'task_id': 'panel-cards', 'units': 1, 'contribution_by': '蒼'}]}
        th = rt.registry.offer(terms, 360, 'offer')
        th = rt.registry.get('remote')['terms_hash']
        rt.registry.accept('remote', 1, '蒼', th, 360, 'accept-a')
        rt.registry.accept('remote', 1, '灯', th, 360, 'accept-b')
        rt.known_terms['灯']['remote:1'] = th
        rt._complete('灯', {'id': 'depend-unseen', 'verb': 'depend', 'start': 358, 'promise_id': 'remote',
                           'plan_id': 'hidden', 'then_task': 'reception-signs', 'fallback_task': 'panel-cards'})
        self.assertFalse(rt.dependencies)
        self.assertEqual(rt.rows[-1]['result'], 'failed')

    def test_relayed_old_work_snapshot_does_not_reveal_new_work(self):
        rt = self.simulation('relay').slot_runtime
        task = deepcopy(rt.ledger['tasks']['panel-cards']); task['units'] = 1
        row = rt.emit('old-card', 'work_unit_completed', '灯', task_id='panel-cards', task_progress=task)
        rt.observe(row, ['灯'])
        rt.ledger['tasks']['panel-cards']['units'] = 3
        rt._complete('灯', {'id': 'tell-old', 'verb': 'tell', 'start': rt.now-2, 'target': '蒼', 'event_id': row['id']})
        self.assertEqual(rt.known_tasks['蒼']['panel-cards']['units'], 1)
        knowledge = next(k for k in rt.knowledge['蒼'] if k['event_id'] == 'old-card')
        self.assertEqual(knowledge['path'], ['old-card', 'complete:tell-old', '灯'])

    def test_arbitrary_partial_minute_resume_preserves_exact_stamina(self):
        world, _ = load(); commands = choices(world, 'fulfilled')
        continuous = self.execute(commands, 'exact-full')
        stop = self.clock('2026-10-14', '16:03')
        self.execute(commands, 'exact-split', until=stop)
        resumed = self.simulation('exact-split', commands); resumed.slot_runtime.run(resume=True)
        self.assertEqual(continuous.export_state(), resumed.slot_runtime.export_state())

    def test_input_types_duplicate_json_and_missing_fixed_sidecar_refuse(self):
        import yaml
        from engine.calendar_checkpoint import strict_json
        with self.assertRaises(ValueError):
            strict_json('{"version":2,"version":1}')
        for field, bad in [('version', 2.0), ('roles', []), ('appraisal', {'fulfilled': float('nan'), 'unexplained_breach': -.05})]:
            project = self.root/('bad-'+field); write_fixture(project)
            p = project/'contract_v2.yaml'; data = yaml.safe_load(p.read_text()); data[field] = bad
            p.write_text(yaml.safe_dump(data, allow_unicode=True))
            with self.subTest(field=field), self.assertRaises(ValueError):
                load(project)
        project = self.root/'bad-days'; write_fixture(project)
        p = project/'world.yaml'; data = yaml.safe_load(p.read_text()); data['time']['days'] = 3.5
        p.write_text(yaml.safe_dump(data, allow_unicode=True))
        with self.assertRaises(ValueError):
            load(project)
        fixed = self.root/'missing-fixed'; freeze_design(fixed, self.simulation('design').slot_runtime.contract['calendar'])
        (fixed/'execution-version.json').unlink()
        with self.assertRaises(ValueError):
            load(fixed)

    def test_run_never_opens_network_connections(self):
        with patch('socket.create_connection', side_effect=AssertionError('network forbidden')), \
             patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \
             patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            world, _ = load()
            rt = self.execute(choices(world, 'fulfilled'), 'offline')
            self.assertTrue(rt.done)

    def test_second_process_cannot_write_during_entire_session(self):
        import subprocess, sys
        from engine.calendar_checkpoint import CheckpointStore
        store = CheckpointStore(self.root/'writers', {})
        code = "from pathlib import Path; from execution.provenance import directory_lock; import sys\nwith directory_lock(Path(sys.argv[1])): print('unexpected second writer')"
        with store.session():
            result = subprocess.run([sys.executable, '-c', code, str(store.out)], cwd=ORIGINAL.parents[1],
                                    capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('unexpected second writer', result.stdout)
            self.assertIn('conflict', result.stderr)
        self.assertEqual(list(store.out.iterdir())[0].name, '.write.lock')

    def test_sleeping_beneficiary_does_not_observe_deadline_or_receive_blame(self):
        rt = self.simulation('sleep').slot_runtime
        terms = {'promise_id': 'sleep', 'version': 1, 'proposer': '蒼', 'promisor': '蒼', 'beneficiary': '灯',
                 'acceptance_parties': ['蒼', '灯'], 'place': '展示室', 'start': 360, 'deadline': 1380,
                 'grace_until': None, 'requirements': [{'task_id': 'panel-cards', 'units': 1, 'contribution_by': '蒼'}]}
        rt.registry.offer(terms, 360, 'offer'); th = rt.registry.get('sleep')['terms_hash']
        rt.registry.accept('sleep', 1, '蒼', th, 360, 'a'); rt.registry.accept('sleep', 1, '灯', th, 360, 'b')
        rt.registry.evaluate(360, rt.ledger)
        rt.people['灯'].zone = '展示室'; before = rt.world.relations.snapshot()
        rt.now = 1380; rt._evaluate()
        self.assertEqual(rt.registry.get('sleep')['status'], 'breached')
        self.assertFalse(rt.appraisals)
        self.assertEqual(rt.world.relations.snapshot(), before)
        self.assertFalse(rt.observed['灯'])

    def test_interrupted_work_retains_effort_and_counts_actual_used_time(self):
        commands = [{'id': 'late-cards', 'actor': '灯', 'at': 1075, 'verb': 'work',
                     'task_id': 'panel-cards', 'minutes': 10}]
        rt = self.execute(commands, 'closed-work')
        self.assertEqual(rt.ledger['tasks']['panel-cards']['effort_minutes'], 5)
        summary = json.loads((rt.store.out/'summary.json').read_text())
        self.assertEqual(summary['used_minutes']['灯']['work'], 5)
        self.assertEqual(summary['remaining_work']['panel-cards']['minutes'], 35)

    def test_unapproved_action_search_policy_is_explicitly_refused(self):
        world, people = load()
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            Simulation(0, world, people, self.root/'no-ga', policies={p: object() for p in people})


if __name__ == '__main__':
    unittest.main()
