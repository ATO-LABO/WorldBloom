"""Opt-in adapter command receipts and transactional calendar restoration."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.sim import Simulation
from scripts.cultural_festival_v2 import load

IDENTITY = {'adapter': 'test-calendar-adapter', 'schema_version': 1,
            'source_hash': 'a'*64, 'config_hash': 'b'*64}


class AdapterJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def runtime(self, name='run', enabled=True):
        world, people = load()
        rt = Simulation(0, world, people, self.root/name).slot_runtime
        rt.configure([])
        if enabled:
            rt.enable_adapter_journal(IDENTITY)
        return rt

    def move(self, rt, ident='move'):
        return {'id': ident, 'actor': '蒼', 'at': rt.now, 'verb': 'move', 'to': '校門'}

    def admit(self, rt, command=None, **kw):
        return rt.admit_adapter_command(command or self.move(rt), proposal_id='proposal', candidate_id='candidate', **kw)

    def test_opt_in_only_and_frozen_commands_unchanged(self):
        rt = self.runtime(enabled=False)
        original = rt.export_state()
        self.assertNotIn('adapter_journal', original)
        self.assertNotIn('actor_vitality', original)
        self.assertNotIn('adapter_journal_identity', rt.inputs())
        rt.enable_adapter_journal(IDENTITY)
        self.admit(rt)
        self.assertEqual(rt.commands, [])
        self.assertEqual(rt.inputs()['adapter_journal_identity'], IDENTITY)

    def test_fresh_runtime_midmove_restore_and_complete(self):
        rt = self.runtime()
        receipt = self.admit(rt, effective_duration=20)
        rt.run(until=365)
        state = rt.export_state()
        fresh = self.runtime('fresh')
        fresh.restore_state(state)
        self.assertEqual(fresh.export_state(), state)
        self.assertEqual(fresh.people['蒼'].zone, '移動中')
        self.assertEqual(fresh.busy['蒼']['finish'], 380)
        self.assertEqual(receipt['effective_duration'], 20)
        fresh._tick(380)
        # The fixture's gate is closed at this time: preserve the core failure.
        self.assertEqual(fresh.people['蒼'].zone, '自宅')
        completed = fresh.export_state()
        self.runtime('third').restore_state(completed)

    def test_disk_resume_midmove_uses_journal_not_frozen_choices(self):
        rt = self.runtime()
        self.admit(rt)
        rt.run(until=365)
        fresh = self.runtime()
        fresh.run(resume=True, until=370)
        # The fixture's gate is closed at this time: preserve the core failure.
        self.assertEqual(fresh.people['蒼'].zone, '自宅')
        self.assertEqual(fresh.commands, [])
        self.assertEqual(sum(r['id'] == 'complete:move' for r in fresh.rows), 1)

    def test_duplicate_detached_receipt_and_changed_collision(self):
        rt = self.runtime(); command = self.move(rt)
        receipt = self.admit(rt, command)
        receipt['command']['to'] = 'tampered'
        rt._tick(370)
        before = rt.export_state()
        repeated = self.admit(rt, command)
        self.assertEqual(repeated['command'], command)
        self.assertEqual(rt.export_state(), before)
        for change in ({'candidate_id': 'other'}, {'effective_duration': 10}):
            kwargs = {'proposal_id': 'proposal', 'candidate_id': 'candidate', **change}
            with self.assertRaises(ValueError):
                rt.admit_adapter_command(command, **kwargs)
            self.assertEqual(rt.export_state(), before)

    def test_rejected_admission_retained_and_never_retried(self):
        rt = self.runtime(); command = self.move(rt)
        command['to'] = '放送室'
        self.assertEqual(self.admit(rt, command)['outcome'], 'rejected')
        state = rt.export_state()
        rt.people['蒼'].zone = '体育館'
        before = rt.export_state()
        self.assertEqual(self.admit(rt, command)['outcome'], 'rejected')
        self.assertEqual(rt.export_state(), before)
        fresh = self.runtime('fresh'); fresh.restore_state(state)
        self.assertEqual(fresh.export_state(), state)

    def test_bad_identity_and_enable_timing(self):
        for key, value in [('adapter',''), ('schema_version',True), ('source_hash','x'*64), ('config_hash',None)]:
            rt = self.runtime(enabled=False); identity = {**IDENTITY, key: value}
            before = rt.export_state()
            with self.assertRaises(ValueError): rt.enable_adapter_journal(identity)
            self.assertEqual(rt.export_state(), before)
        rt = self.runtime(enabled=False)
        rt._start(self.move(rt))
        with self.assertRaises(ValueError): rt.enable_adapter_journal(IDENTITY)
        rt = self.runtime('stored', enabled=False); rt.run(until=361)
        with self.assertRaises(ValueError): rt.enable_adapter_journal(IDENTITY)

    def test_corrupt_restore_rejects_before_any_mutation(self):
        rt = self.runtime(); self.admit(rt, effective_duration=20)
        rt._tick(365)
        original = rt.export_state()
        def entry(s): return s['adapter_journal']['entries']['proposal']
        changes = [
            lambda s: s['adapter_journal']['identity'].update(config_hash='c'*64),
            lambda s: s['adapter_journal']['initial_rows'].update(count=False),
            lambda s: entry(s)['receipt']['command'].update(at=366),
            lambda s: entry(s)['receipt'].update(outcome='rejected'),
            lambda s: entry(s)['receipt'].update(effective_duration=19),
            lambda s: entry(s).update(requested_duration=True),
            lambda s: s['busy']['蒼'].update(to='体育館'),
            lambda s: s['busy']['蒼'].update(finish=379),
            lambda s: s['busy']['蒼'].update(extra='unknown'),
            lambda s: s['actor_vitality'].update(蒼='invalid'),
            lambda s: s['actor_vitality'].pop('蒼'),
            lambda s: s['adapter_journal']['entries'].clear(),
        ]
        for change in changes:
            bad = deepcopy(original); change(bad)
            with self.assertRaises(ValueError): rt.restore_state(bad)
            self.assertEqual(rt.export_state(), original)

    def test_vitality_is_restored_and_unknown_fields_rejected(self):
        rt = self.runtime(); rt.people['蒼'].vitality = 'dead'
        state = rt.export_state(); fresh = self.runtime('fresh')
        fresh.restore_state(state)
        self.assertEqual(fresh.people['蒼'].vitality, 'dead')
        bad = deepcopy(state); bad['adapter_journal']['extra'] = 1
        with self.assertRaises(ValueError): fresh.restore_state(bad)
        self.assertEqual(fresh.export_state(), state)

    def test_exception_restores_state_vitality_and_route_override(self):
        rt = self.runtime(); before = rt.export_state(); routes = deepcopy(rt.contract['travel_minutes'])
        start = rt._start
        def fail(command):
            start(command)
            rt.people['蒼'].vitality = 'dead'
            raise RuntimeError('injected after start')
        with patch.object(rt, '_start', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                self.admit(rt, effective_duration=20)
        self.assertEqual(rt.export_state(), before)
        self.assertEqual(rt.contract['travel_minutes'], routes)
        self.assertEqual(self.admit(rt, effective_duration=20)['outcome'], 'started')

    def test_wait_and_interrupted_work_receipts_restore(self):
        rt = self.runtime('wait')
        command = {'id':'wait', 'actor':'蒼', 'at':rt.now, 'verb':'wait', 'minutes':2}
        self.admit(rt, command)
        rt._tick(362)
        self.runtime('wait-restored').restore_state(rt.export_state())
        rt = self.runtime('work')
        rt.run(until=1075)
        command = {'id':'work', 'actor':'灯', 'at':rt.now, 'verb':'work',
                   'task_id':'panel-cards', 'minutes':10}
        self.assertEqual(self.admit(rt, command)['outcome'], 'started')
        rt._tick(1080)
        self.assertNotIn('灯', rt.busy)
        self.assertTrue(any(r['id'] == 'interrupt:work' for r in rt.rows))
        state = rt.export_state()
        fresh = self.runtime('work-restored'); fresh.restore_state(state)
        self.assertEqual(fresh.export_state(), state)
        bad = deepcopy(state)
        next(r for r in bad['rows'] if r['id'] == 'interrupt:work')['details']['choice']['task_id'] = 'panel-photo'
        with self.assertRaises(ValueError): fresh.restore_state(bad)
        self.assertEqual(fresh.export_state(), state)

    def test_completed_and_rejected_entries_cannot_be_deleted(self):
        for rejected in (False, True):
            rt = self.runtime('coverage-'+str(rejected))
            command = self.move(rt)
            if rejected: command['to'] = '放送室'
            self.admit(rt, command)
            if not rejected: rt._tick(370)
            original = rt.export_state(); bad = deepcopy(original)
            bad['adapter_journal']['entries'].clear()
            with self.assertRaisesRegex(ValueError, 'no frozen or journaled admission'):
                rt.restore_state(bad)
            self.assertEqual(rt.export_state(), original)

    def test_initial_historical_prefix_is_preserved_not_reclassified(self):
        def initialized(name):
            rt = self.runtime(name, enabled=False)
            rt._start({'id':'initial-wait', 'actor':'蒼', 'at':rt.now, 'verb':'wait', 'minutes':1})
            rt._tick(361)
            rt.enable_adapter_journal(IDENTITY)
            return rt
        rt = initialized('original'); self.admit(rt)
        original = rt.export_state()
        fresh = initialized('fresh'); fresh.restore_state(original)
        self.assertEqual(fresh.export_state(), original)
        for change in ('prefix', 'historical-row'):
            bad = deepcopy(original)
            if change == 'prefix': bad['adapter_journal']['initial_rows']['count'] += 1
            else: bad['rows'][0]['details']['choice']['minutes'] = 2
            with self.assertRaises(ValueError): fresh.restore_state(bad)
            self.assertEqual(fresh.export_state(), original)

    def test_configure_freezes_at_enablement_without_mutating_choices(self):
        rt = self.runtime(); self.admit(rt)
        before = rt.export_state()
        rt.configure([])
        for commands in ([self.move(rt)], [{'id':'different','actor':'灯','at':rt.now,'verb':'wait','minutes':1}]):
            with self.assertRaisesRegex(ValueError, 'frozen'):
                rt.configure(commands)
            self.assertEqual(rt.commands, [])
            self.assertEqual(rt.export_state(), before)
        self.runtime('fresh').restore_state(before)

    def test_composite_frozen_component_ids_reserved_on_admission_and_restore(self):
        from scripts.fact_runtime_demo import create_project
        project = create_project(self.root/'fact-project')
        combo = {'id':'combo', 'actor':'蒼', 'at':500, 'verb':'move_and_send', 'to':'校門', 'message_id':'M'}
        def fact_runtime(name, commands):
            world, people = load(project)
            rt = Simulation(0, world, people, self.root/name).slot_runtime
            rt.configure(commands); rt.enable_adapter_journal(IDENTITY)
            return rt
        rt = fact_runtime('fact-target', [combo])
        before = rt.export_state()
        for ident in ('combo', 'combo-move', 'combo-message'):
            command = {'id':ident, 'actor':'蒼', 'at':rt.now, 'verb':'wait', 'minutes':1}
            with self.assertRaisesRegex(ValueError, 'identity collision'):
                self.admit(rt, command)
            self.assertEqual(rt.export_state(), before)
            donor = fact_runtime('fact-donor-'+ident, [])
            self.admit(donor, command)
            with self.assertRaisesRegex(ValueError, 'Duplicate adapter command identity'):
                rt.restore_state(donor.export_state())
            self.assertEqual(rt.export_state(), before)

    def test_bad_admission_has_no_effect(self):
        rt = self.runtime(); before = rt.export_state()
        cases = [({**self.move(rt), 'at': rt.now+1}, None),
                 ({**self.move(rt), 'unknown': 1}, None),
                 (self.move(rt), True), (self.move(rt), 0),
                 ({**self.move(rt), 'to': '放送室'}, 10),
                 ({'id':'wait','actor':'蒼','at':rt.now,'verb':'wait','minutes':2}, 3)]
        for command, duration in cases:
            with self.assertRaises(ValueError): self.admit(rt, command, effective_duration=duration)
            self.assertEqual(rt.export_state(), before)


if __name__ == '__main__': unittest.main()
