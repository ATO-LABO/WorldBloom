"""Bounded approved-fixture checks; no experiment/search/network/model calls.

Set MELOS_APPROVED_ADAPTER_DIR and MELOS_INPUT_DIR explicitly to exercise the
approved external fixture. No user-machine paths are embedded in this module.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

from execution.melos_adapter import MelosActionStage, _hash


@unittest.skipUnless(os.environ.get('MELOS_APPROVED_ADAPTER_DIR') and
                     os.environ.get('MELOS_INPUT_DIR'), 'approved external fixture paths not supplied')
class MelosActionStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(os.environ['MELOS_APPROVED_ADAPTER_DIR'])/'melos_runner.py'
        spec = importlib.util.spec_from_file_location('approved_melos_stage_fixture', source)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)
        root = Path(os.environ['MELOS_INPUT_DIR'])
        if root.name != 'melos-input':
            raise ValueError('Original Trial requires a melos-input fixture directory')
        cls.module.ROOT = root.parent
        cls.inputs = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(root.rglob('*.yaml'))}
        cls.sources = {'approved_trial': hashlib.sha256(source.read_bytes()).hexdigest()}

    def stage(self):
        trial = self.module.Trial(self.module.MelosGenome.random(random.Random(1)))
        return MelosActionStage(trial, input_hashes=self.inputs, source_hashes=self.sources)

    def complete(self, stage):
        if stage.phase == 'physical': stage.advance(stage.pending['finish'])
        while stage.phase not in ('branch', 'idle'): stage.step_effect()
        return stage.finish_action()

    def test_move_pauses_restore_real_people_and_exact_final_state(self):
        original = self.stage()
        candidate = self.module.candidate('go_village', 'move', kind='move', to='pass', minutes=150, cost=6)
        original.start_action(candidate, continuation={'phase': 'outbound', 'step': 1})
        original.advance(73)
        self.assertEqual(original.rt.people['Melos'].zone, '移動中')
        snapshot = json.loads(json.dumps(original.export_state()))
        restored = self.stage()
        person = restored.rt.people['Melos']
        restored.restore_state(snapshot)
        self.assertIs(person, restored.rt.people['Melos'])
        self.assertIs(restored.rt.time_kernel, restored.trial.sim.time_kernel)
        self.assertEqual(restored.export_state(), snapshot)
        self.assertEqual(self.complete(original), self.complete(restored))
        self.assertEqual(original.export_state(), restored.export_state())
        self.assertEqual(original.rt.people['Melos'].zone, 'pass')

    def test_each_post_effect_boundary_is_resumable_and_not_replayed(self):
        stage = self.stage()
        stage.start_action(self.module.candidate('rest_arrival', 'rest', minutes=30, recover=4))
        stage.advance(30)
        for expected in ('cost', 'completion', 'recovery', 'branch'):
            self.assertEqual(stage.phase, expected)
            copy = self.stage(); copy.restore_state(stage.export_state())
            self.assertEqual(stage.export_state(), copy.export_state())
            if expected != 'branch':
                stage.step_effect(); copy.step_effect()
                self.assertEqual(stage.export_state(), copy.export_state())
        self.assertEqual([r['verb'] for r in stage.rt.rows if r['verb'] in
                          ('energy_change', 'scenario_action_completed')],
                         ['energy_change', 'scenario_action_completed', 'energy_change'])

    def test_two_minute_report_work_and_vitality_restore(self):
        stage = self.stage()
        stage.start_action(self.module.candidate('surrender_now', 'return', kind='move', to='court', minutes=30, cost=1))
        self.complete(stage)
        stage.start_action(self.module.candidate('report', 'report', kind='work', task='return', minutes=2),
                           continuation={'phase': 'return_observed'})
        stage.advance(31)
        stage.rt.people['Selinuntius'].vitality = 'dead'
        stage.trial.state['dead_friend'] = True
        restored = self.stage(); restored.restore_state(stage.export_state())
        self.assertEqual(restored.rt.people['Selinuntius'].vitality, 'dead')
        self.assertEqual(restored.rt.ledger['tasks']['return']['units'], 0)
        self.complete(stage); self.complete(restored)
        self.assertEqual(restored.rt.ledger['tasks']['return']['units'], 1)
        self.assertEqual(stage.export_state(), restored.export_state())

    def test_environment_disciple_pending_and_deadline_order(self):
        stage, direct = self.stage(), self.stage()
        c = self.module.candidate('stay', 'wait', minutes=3961)
        stage.start_action(c); stage.advance(3841)
        self.assertIn('Philostratus', stage.rt.busy)
        self.assertFalse(stage.trial.state['dead_friend'])
        resumed = self.stage(); resumed.restore_state(stage.export_state())
        self.complete(stage); self.complete(resumed)
        direct.trial.act(c)
        self.assertEqual(stage.rt.rows, direct.rt.rows)
        self.assertEqual(stage.export_state(), resumed.export_state())
        verbs = [row['verb'] for row in stage.rt.rows]
        self.assertLess(verbs.index('hostage_executed'), verbs.index('scenario_action_completed'))
        self.assertEqual(stage.rt.people['Selinuntius'].vitality, 'dead')

    def test_wedding_work_remains_pending_until_120_minutes(self):
        stage = self.stage()
        for to in ('pass', 'west_bank', 'east_bank', 'village'):
            source = stage.rt.people['Melos'].zone
            duration = stage.rt.contract['travel_minutes'][source][to]
            stage.start_action(self.module.candidate('continue', 'move', kind='move', to=to, minutes=duration))
            self.complete(stage)
        start = stage.rt.now
        stage.start_action(self.module.candidate('wedding', 'wedding', kind='work', task='wedding', minutes=120, cost=4))
        stage.advance(start+60)
        self.assertEqual(stage.rt.ledger['tasks']['wedding']['units'], 0)
        restored = self.stage(); restored.restore_state(stage.export_state())
        self.complete(stage); self.complete(restored)
        self.assertEqual(stage.rt.now, start+120)
        self.assertEqual(stage.rt.ledger['tasks']['wedding']['units'], 1)
        self.assertEqual(stage.export_state(), restored.export_state())

    def test_unaffordable_move_collapses_without_teleport_or_recovery(self):
        stage, direct = self.stage(), self.stage()
        c = self.module.candidate('go_village', 'move', kind='move', to='pass', minutes=150, cost=101, recover=7)
        stage.start_action(c); stage.advance(15)
        restored = self.stage(); restored.restore_state(stage.export_state())
        self.complete(stage); self.complete(restored)
        direct.trial.act(c)
        self.assertEqual(stage.rt.rows, direct.rt.rows)
        self.assertEqual(stage.export_state(), restored.export_state())
        self.assertEqual(stage.rt.people['Melos'].zone, 'outskirts')
        self.assertEqual(stage.rt.now, 30)
        self.assertTrue(stage.trial.state['exhausted'])

    def test_phase_edits_cannot_skip_or_repeat_committed_effects(self):
        stage = self.stage()
        stage.start_action(self.module.candidate('rest_arrival', 'rest', minutes=30, cost=6, recover=4))
        stage.advance(30)
        for phase in ('cost', 'completion', 'recovery', 'branch'):
            self.assertEqual(stage.phase, phase)
            before = stage.export_state()
            for other in ('cost', 'completion', 'recovery', 'branch'):
                if other == phase: continue
                broken = deepcopy(before); broken['state']['phase'] = other
                broken['sha256'] = _hash(broken['state'])
                with self.subTest(phase=phase, other=other), self.assertRaises(ValueError):
                    stage.restore_state(broken)
                self.assertEqual(stage.export_state(), before)
            if phase != 'branch': stage.step_effect()
        before = stage.export_state()
        for verb, field, value in (('energy_change', 'after', 93.0),
                                   ('scenario_action_completed', 'core_completion_id', 'wrong')):
            broken = deepcopy(before)
            row = next(row for row in broken['state']['runtime']['rows'] if row['verb'] == verb)
            row['details'][field] = value
            broken['sha256'] = _hash(broken['state'])
            with self.assertRaises(ValueError): stage.restore_state(broken)
            self.assertEqual(stage.export_state(), before)

    def test_invalid_admission_rolls_back_serial_and_core(self):
        stage = self.stage(); before = stage.export_state()
        with self.assertRaises(ValueError):
            stage.start_action(self.module.candidate('bad', 'bad', kind='move', to='village', minutes=3))
        self.assertEqual(stage.export_state(), before)

    def test_invalid_snapshot_is_rejected_without_mutation(self):
        stage = self.stage()
        stage.start_action(self.module.candidate('rest_arrival', 'rest', minutes=30))
        stage.advance(10)
        pristine = stage.export_state()
        for change in (
            lambda s: s['state']['external']['count'].__setitem__('Melos', True),
            lambda s: s['state']['external']['vitality'].__setitem__('Melos', 'imagined'),
            lambda s: s['state']['pending'].__setitem__('finish', 29),
            lambda s: s['state']['identity']['inputs'].__setitem__('other', '0'*64),
            lambda s: s['state']['runtime']['busy']['Melos'].__setitem__('id', 'invented'),
        ):
            broken = deepcopy(pristine); change(broken)
            broken['sha256'] = _hash(broken['state'])
            with self.assertRaises(ValueError): stage.restore_state(broken)
            self.assertEqual(stage.export_state(), pristine)

    def test_injected_environment_and_energy_failures_roll_back(self):
        stage = self.stage()
        stage.start_action(self.module.candidate('rest_arrival', 'rest', minutes=30, recover=4))
        before = stage.export_state()
        original = stage.trial.environment
        def failed_environment():
            original()
            stage.trial.state['rain'] = True
            raise RuntimeError('injected after environment mutation')
        with patch.object(stage.trial, 'environment', failed_environment):
            with self.assertRaises(RuntimeError): stage.advance(15)
        self.assertEqual(stage.export_state(), before)
        stage.advance(30)
        before = stage.export_state()
        original = stage.trial.set_energy
        def failed_energy(*args):
            original(*args)
            raise RuntimeError('injected after energy/event mutation')
        with patch.object(stage.trial, 'set_energy', failed_energy):
            with self.assertRaises(RuntimeError): stage.step_effect()
        self.assertEqual(stage.export_state(), before)
        self.complete(stage)

    def test_actual_nine_policy_history_and_rng_resume(self):
        from gapengine.genome import Genome
        source = Path(os.environ['MELOS_APPROVED_ADAPTER_DIR'])/'ninegene_melos.py'
        spec = importlib.util.spec_from_file_location('approved_nine_stage_fixture', source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        with patch.dict(sys.modules, {'melos_runner': self.module}):
            spec.loader.exec_module(module)
        def new():
            trial = module.NineTrial(Genome.neutral(), seed=3)
            return MelosActionStage(trial, input_hashes=self.inputs,
                source_hashes={**self.sources, 'ninegene': hashlib.sha256(source.read_bytes()).hexdigest()})
        stage = new()
        offered = [self.module.candidate('disclose', 'disclose', minutes=2,
                                         features={'friend': 1, 'honor': 1})]
        selected = stage.trial.choose('Melos', 'village', offered)
        self.assertTrue(stage.trial.policy_history)
        stage.start_action(selected); stage.advance(1)
        snapshot = json.loads(json.dumps(stage.export_state()))
        restored = new(); restored.restore_state(snapshot)
        self.assertEqual(stage.trial.policy_history, restored.trial.policy_history)
        self.assertEqual(stage.trial.rng.getstate(), restored.trial.rng.getstate())
        self.complete(stage); self.complete(restored)
        self.assertEqual(stage.export_state(), restored.export_state())
        self.assertEqual(stage.trial.choose('Melos', 'village', offered),
                         restored.trial.choose('Melos', 'village', offered))
        self.assertEqual(stage.export_state(), restored.export_state())

    def test_original_act_equivalence_for_move_and_recovery(self):
        for candidate in (
            self.module.candidate('go_village', 'move', kind='move', to='pass', minutes=150, cost=6),
            self.module.candidate('rest_arrival', 'rest', minutes=30, recover=4),
        ):
            direct, staged = self.stage(), self.stage()
            direct.trial.act(deepcopy(candidate))
            staged.start_action(candidate); self.complete(staged)
            self.assertEqual(direct.rt.rows, staged.rt.rows)
            self.assertEqual(direct.trial.state, staged.trial.state)
            self.assertEqual(direct.rt._people(), staged.rt._people())
