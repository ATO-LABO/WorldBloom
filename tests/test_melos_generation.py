"""Private native whole-session persistence and capture-aware restore coverage.

Native Linux/Windows execution uses the actual host backend. These tests alone
do not qualify Windows GUI, release upload, or a standalone Viewer package.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import unittest
from unittest.mock import patch

from engine.calendar_generation_capture import NativeGenerationCapture
from engine.calendar_parent_owner import _ExistingParentOwner
from engine.calendar_run_store import NativeWriterPort, RunStore
from execution.melos_adapter import _hash
from execution.melos_generation import (MelosGenerationProgress,
    melos_generation_compatibility, run_melos_generation)
from tests import test_melos_capture_factory as fixtures


class _CapturedSetup:
    fixture = fixtures.CapturedFactoryTests.fixture
    capture = fixtures.CapturedFactoryTests.capture
    factory = fixtures.CapturedFactoryTests.factory
    forbid_input_access = fixtures.CapturedFactoryTests.forbid_input_access

    def setUp(self):
        fixtures.CapturedFactoryTests.setUp(self)
        self.source, self.inputs = self.fixture('session', 'captured-project')
        self.cap = self.capture(self.inputs)
        self.make = self.factory(self.source, self.inputs, self.cap)

    def real_objects(self, session):
        return (session.trial, session.trial.sim, session.rt, session.rt.world,
                session.rt.sim.subjects, *session.rt.people.values())

    def assert_real_objects(self, before, session):
        after = self.real_objects(session)
        self.assertEqual(len(before), len(after))
        for a, b in zip(before, after):
            self.assertIs(a, b)

class CapturedSessionRestoreTests(_CapturedSetup, unittest.TestCase):
    def test_restore_detaches_staging_and_preserves_real_objects_and_binding(self):
        session = self.make.create(63)
        original = self.real_objects(session)
        staged = session.stage._staging_trial()
        self.assertIs(staged.rt.calendar_capture, self.cap)
        self.assertIs(staged.rt.world.calendar_capture, self.cap)
        for a, b in zip(original, self.real_objects(self.make.create(63))):
            self.assertIsNot(a, b)
        self.assertIsNot(staged.rt, session.rt)
        self.assertIsNot(staged.sim, session.trial.sim)
        self.assertIsNot(staged.rt.world, session.rt.world)
        self.assertIsNot(staged.rt.people['Melos'], session.rt.people['Melos'])
        self.assertIsNot(staged.scenario, session.trial.scenario)
        self.assertIsNot(staged.p, session.trial.p)
        staged.p['deadline'] = -1
        self.assertNotEqual(staged.p['deadline'], session.trial.p['deadline'])
        with self.assertRaises(TypeError):
            deepcopy(self.cap)
        source = self.make.create(63); source.run(until=2220)
        session.restore_state(source.export_state())
        self.assert_real_objects(original, session)
        self.assertIs(session.rt.calendar_capture, self.cap)
        self.cap.assert_live()
        self.assertEqual(source.export_state(), session.export_state())
        self.assertIsNot(source.stage.provider, session.stage.provider)
        self.assertEqual(source.stage.provider._rng.getstate(), session.stage.provider._rng.getstate())
        self.assertEqual(source.rt.sim.rng.getstate(), session.rt.sim.rng.getstate())

    def test_removed_and_replaced_input_paths_are_never_reopened_on_restore_or_rollback(self):
        source = self.make.create(0); source.run(until=73)
        snapshot = source.export_state()
        shutil.rmtree(self.inputs)
        for replaced in (False, True):
            if replaced:
                self.inputs.mkdir(); (self.inputs/'world.yaml').write_text('untrusted: replacement')
            with self.forbid_input_access(self.inputs):
                target = self.make.create(0)
                target.restore_state(snapshot)
                self.assertEqual(snapshot, target.export_state())
                before = target.export_state()
                error = RuntimeError('after action effects')
                with patch.object(target, '_after_action', side_effect=error):
                    with self.assertRaises(RuntimeError) as caught:
                        target.run(until=150)
                self.assertIs(caught.exception, error)
                self.assertEqual(before, target.export_state())
                target.run(until=150)
                self.cap.assert_live()

    def test_atomic_stage_and_session_failures_restore_provider_rng_exactly(self):
        session = self.make.create(0); before = session.export_state()
        for error in (RuntimeError('choice admission failure'), KeyboardInterrupt('cancelled')):
            with patch.object(session, '_action', side_effect=error):
                with self.assertRaises(type(error)) as caught:
                    session.run(until=73)
            self.assertIs(caught.exception, error)
            self.assertEqual(before, session.export_state())
        session._choose_step()
        before = session.export_state()
        error = RuntimeError('environment failure')
        with patch.object(session.stage, '_environment', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                session.stage.advance(73)
        self.assertIs(caught.exception, error)
        self.assertEqual(before, session.export_state())

    def test_capture_factory_binding_missing_or_changed_fails_without_mutation(self):
        session = self.make.create(0); before = session.export_state()
        with patch.object(session.stage, '_captured_factory', None):
            with self.assertRaisesRegex(ValueError, 'approved factory binding'):
                session.restore_state(before)
        self.assertEqual(before, session.export_state())
        source, inputs = self.fixture('other')
        other = self.factory(source, inputs, self.capture(inputs))
        with patch.object(session.stage, '_captured_factory', other):
            with self.assertRaisesRegex(ValueError, 'approved factory binding'):
                session.restore_state(before)
        self.assertEqual(before, session.export_state())

    def test_rollback_failure_respects_original_error_and_cancellation_precedence(self):
        for owner in ('session', 'stage'):
            for primary_cancelled, rollback_cancelled in ((False, False), (False, True),
                                                          (True, False), (True, True)):
                with self.subTest(owner=owner, primary_cancelled=primary_cancelled,
                                  rollback_cancelled=rollback_cancelled):
                    session = self.make.create(0)
                    primary = (KeyboardInterrupt if primary_cancelled else RuntimeError)('primary')
                    secondary = (KeyboardInterrupt if rollback_cancelled else ValueError)('rollback')
                    expected = secondary if rollback_cancelled and not primary_cancelled else primary
                    chained = primary if expected is secondary else secondary
                    if owner == 'session':
                        with patch.object(session, '_action', side_effect=primary), \
                                patch.object(session, 'restore_state', side_effect=secondary):
                            with self.assertRaises(type(expected)) as caught:
                                session.run(until=73)
                    else:
                        stage = session.stage
                        with patch.object(stage, '_start', side_effect=primary), \
                                patch.object(stage, 'restore_state', side_effect=secondary):
                            with self.assertRaises(type(expected)) as caught:
                                stage.start_action({'id': 'test', 'kind': 'wait', 'minutes': 1})
                    self.assertIs(expected, caught.exception)
                    self.assertIs(chained, caught.exception.__cause__)

    def test_closed_capture_cannot_restore_or_create_staging(self):
        session = self.make.create(0); state = session.export_state()
        self.cap.close()
        with self.assertRaises((ValueError, RuntimeError)):
            session.restore_state(state)
        with self.assertRaises((ValueError, RuntimeError)):
            session.stage._staging_trial()


@unittest.skipUnless(sys.platform == 'linux' or os.name == 'nt', 'native Linux or Windows required')
class MelosNativeGenerationTests(_CapturedSetup, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.output = self.root/'output'; self.output.mkdir()
        self.backend = 'windows' if os.name == 'nt' else 'linux'
        self.owner = _ExistingParentOwner(self.output, backend=self.backend)
        self.addCleanup(self.owner.close)
        self.stream = 0

    def run_native(self, session, **kwargs):
        self.stream += 1
        return run_melos_generation(session, self.owner, stream_id=f'{self.stream:032x}', **kwargs)

    def files(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.output.iterdir()}

    def save(self, state, session):
        self.stream += 1
        with RunStore(self.owner, NativeWriterPort(), NativeGenerationCapture(),
                      melos_generation_compatibility(session), stream_id=f'{self.stream:032x}') as store:
            return store.initialize(state).selection()

    def test_full_split_resume_and_completed_noop_are_byte_identical(self):
        full = self.make.create(0)
        a = self.run_native(full)
        split = self.make.create(0)
        b = self.run_native(split, until=73)
        self.assertFalse(b.done)
        self.assertEqual(73, b.now)
        retained = self.files()
        resumed = self.make.create(0); objects = self.real_objects(resumed)
        c = self.run_native(resumed, selection=b.selection)
        self.assertEqual(full.export_state(), resumed.export_state())
        self.assertEqual(a.layers, c.layers)
        self.assertEqual(a.result, c.result)
        self.assert_real_objects(objects, resumed)
        for name, sha in retained.items(): self.assertEqual(sha, self.files()[name])
        self.assertTrue(c.done)
        self.assertTrue(resumed.finished)
        self.assertFalse(resumed.rt.done)  # finite scenario finishes before calendar end
        retained = self.files()
        target = self.make.create(0)
        with patch.object(target, 'run', side_effect=AssertionError('terminal resume executed')):
            d = self.run_native(target, selection=c.selection)
        self.assertEqual(c.selection, d.selection)
        self.assertFalse(d.saved)
        self.assertEqual(c.layers, d.layers); self.assertEqual(c.result, d.result)
        self.assertEqual(retained, self.files())
        self.assertEqual(0, self.owner.active_borrows)
        self.assertEqual('OPEN', self.owner.state)
        self.cap.assert_live()
        self.assertFalse((self.inputs.parent/'unused-output').exists())

    def test_each_partial_post_effect_restores_without_duplicate_or_skipped_effect(self):
        full = self.make.create(0); full.run(until=150)
        for phase in ('cost', 'completion', 'recovery', 'branch'):
            with self.subTest(phase=phase):
                source = self.make.create(0); source._choose_step()
                source.stage.advance(source.stage.pending['finish'])
                while source.stage.phase != phase: source.stage.step_effect()
                selected = self.save(source.export_state(), source)
                target = self.make.create(0)
                result = self.run_native(target, selection=selected, until=150)
                self.assertFalse(result.done)
                self.assertEqual(full.export_state(), target.export_state())
                self.assertEqual(0, self.owner.active_borrows)

    def test_seed63_half_wedding_provider_and_rng_restore_exactly(self):
        source = self.make.create(63)
        source.run(until=2220)
        self.assertEqual('wedding', source.stage.pending['candidate']['id'])
        selected = self.save(source.export_state(), source)
        target = self.make.create(63)
        result = self.run_native(target, selection=selected, until=2220)
        self.assertEqual(source.export_state(), target.export_state())
        self.assertEqual(source.stage.provider._rng.getstate(), target.stage.provider._rng.getstate())
        self.assertEqual(source.rt.sim.rng.getstate(), target.rt.sim.rng.getstate())
        source.run(); target.run()
        self.assertEqual(source.export_state(), target.export_state())
        self.assertTrue(target.trial.state['wedding'])
        self.assertFalse(result.done)

    def test_rehashed_invalid_session_state_never_mutates_runs_or_exports(self):
        source = self.make.create(0); source.run(until=73)
        valid = source.export_state()
        def step(s): s['step_index'] += 1
        def phase(s): s['phase'] = 'finished'; s['finished'] = True
        def branch(s): s['stage']['state']['pending']['continuation']['branch'] = 'report'
        def external(s): s['stage']['state']['external']['state']['wedding'] = True
        def runtime(s): s['stage']['state']['runtime']['people']['Melos']['stamina'] = -1
        def provider(s): s['stage']['state']['provider']['state']['counts']['Melos'] += 1
        def rng(s): s['stage']['state']['runtime']['rng'] = []
        def malformed(s): s['stage']['state']['runtime']['rows'][0]['details'] = None
        for edit in (step, phase, branch, external, runtime, provider, rng, malformed):
            with self.subTest(edit=edit.__name__):
                invalid = deepcopy(valid); edit(invalid['state'])
                invalid['state']['stage']['sha256'] = _hash(invalid['state']['stage']['state'])
                invalid['sha256'] = _hash(invalid['state'])
                target = self.make.create(0); before = target.export_state()
                objects = self.real_objects(target)
                selected = self.save(invalid, target); files = self.files()
                progress = MelosGenerationProgress()
                with patch.object(target, 'run', side_effect=AssertionError('must not execute')), \
                        patch('execution.melos_generation._result_bytes', side_effect=AssertionError('must not export')):
                    with self.assertRaises(ValueError):
                        self.run_native(target, selection=selected, progress=progress)
                self.assertEqual(before, target.export_state())
                self.assert_real_objects(objects, target)
                self.assertEqual(files, self.files())
                self.assertIsNone(progress.selection)
                self.assertEqual(0, self.owner.active_borrows)
                self.assertTrue(target.rt._generation_persistence_started)

    def test_initial_write_failures_preserve_exception_and_leave_session_unchanged(self):
        for error in (OSError('native initial write failed'), KeyboardInterrupt('cancel initial write')):
            session = self.make.create(0); before = session.export_state()
            progress = MelosGenerationProgress()
            with patch.object(NativeWriterPort, 'open', side_effect=error):
                with self.assertRaises(type(error)) as caught:
                    self.run_native(session, progress=progress)
            self.assertIs(caught.exception, error)
            self.assertEqual(before, session.export_state())
            self.assertIsNone(progress.selection)
            self.assertEqual(0, self.owner.active_borrows)
            self.assertEqual('OPEN', self.owner.state)
            with self.assertRaisesRegex(ValueError, 'frozen'): session.rt.configure([])

    def test_failed_transition_rolls_back_and_retains_only_last_accepted_selection(self):
        for error in (OSError('native transition write failed'), KeyboardInterrupt('cancel transition write')):
            with self.subTest(error=type(error).__name__):
                session = self.make.create(0); before = session.export_state()
                progress = MelosGenerationProgress()
                original = NativeWriterPort.open; calls = 0
                def fail_second(port, *args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 2: raise error
                    return original(port, *args, **kwargs)
                with patch.object(NativeWriterPort, 'open', fail_second):
                    with self.assertRaises(type(error)) as caught:
                        self.run_native(session, until=73, progress=progress)
                self.assertIs(caught.exception, error)
                self.assertEqual(before, session.export_state())
                self.assertIsNotNone(progress.selection)
                self.assertEqual(0, progress.selection.expected.ordinal)
                self.assertTrue(progress.saved)
                self.assertEqual(0, self.owner.active_borrows)
                # Independently retained accepted head remains safely readable.
                recovered = self.make.create(0)
                self.run_native(recovered, selection=progress.selection, until=0)
                self.assertEqual(before, recovered.export_state())

    def test_post_publication_failure_releases_borrow_and_does_not_promote_attempt(self):
        if self.backend == 'windows':
            from engine.calendar_windows_writer import WindowsDirectSnapshot as Snapshot
        else:
            from engine.calendar_container_linux import LinuxSnapshot as Snapshot
        for error in (OSError('after namespace publication'), KeyboardInterrupt('cancel after publication')):
            with self.subTest(error=type(error).__name__):
                session = self.make.create(0); before = session.export_state()
                progress = MelosGenerationProgress(); original = Snapshot.seal
                receipts, injected = [], []
                previous_files = self.files()
                def fail_after_seal(writer):
                    receipt = original(writer)
                    receipts.append(receipt)
                    if self.backend == 'windows':
                        self.assertEqual('sealed', receipt.phase)
                        self.assertIs(receipt.expected_bytes_verified, True)
                        self.assertEqual('confirmed', receipt.data_flush)
                        self.assertEqual(receipt.expected_size, receipt.bytes_written)
                    else:
                        self.assertEqual('sealed', receipt.completion)
                        self.assertEqual('published', receipt.publication)
                        self.assertEqual('namespace_flush_confirmed', receipt.durability)
                    if len(receipts) == 2:
                        injected.append(error)
                        raise error
                    return receipt
                with patch.object(Snapshot, 'seal', fail_after_seal):
                    with self.assertRaises(type(error)) as caught:
                        self.run_native(session, until=73, progress=progress)
                self.assertIs(caught.exception, error)
                self.assertEqual(2, len(receipts))
                self.assertEqual([error], injected)
                self.assertEqual(before, session.export_state())
                self.assertIsNotNone(progress.selection)
                self.assertTrue(progress.saved)
                self.assertEqual(0, progress.selection.expected.ordinal)
                self.assertEqual(0, self.owner.active_borrows)
                self.assertEqual('OPEN', self.owner.state)
                files = self.files()
                self.assertEqual(2, len(set(files) - set(previous_files)))
                self.assertEqual({r.final_leaf for r in receipts}, set(files) - set(previous_files))
                for receipt in receipts:
                    self.assertEqual(receipt.expected_sha256, files[receipt.final_leaf])
                    self.assertEqual(receipt.expected_size, (self.output / receipt.final_leaf).stat().st_size)
                for name, sha in previous_files.items(): self.assertEqual(sha, files[name])
                # Only the confirmed ordinal zero is returned as authority,
                # although the failed later attempt remains on the native parent.
                self.assertIn(progress.selection.leaf, files)
                self.cap.assert_live()

    def test_resume_failure_preserves_base_and_no_inferred_attempt_authority(self):
        source = self.make.create(0); source.run(until=73)
        selected = self.save(source.export_state(), source)
        target = self.make.create(0); progress = MelosGenerationProgress()
        error = OSError('first resumed write failed')
        with patch.object(NativeWriterPort, 'open', side_effect=error):
            with self.assertRaises(OSError) as caught:
                self.run_native(target, selection=selected, until=150, progress=progress)
        self.assertIs(caught.exception, error)
        self.assertEqual(source.export_state(), target.export_state())
        self.assertEqual(selected, progress.selection)
        self.assertFalse(progress.saved)
        self.assertEqual(0, self.owner.active_borrows)

    def test_native_route_never_reaches_legacy_writers_after_input_path_removal(self):
        source = self.make.create(0)
        shutil.rmtree(self.inputs)
        with self.forbid_input_access(self.inputs), \
                patch('execution.melos_session.CheckpointStore', side_effect=AssertionError('legacy store')), \
                patch('execution.melos_session.atomic_bytes', side_effect=AssertionError('legacy output')), \
                patch('engine.calendar_runtime.CheckpointStore', side_effect=AssertionError('legacy core store')), \
                patch('engine.calendar_runtime.atomic_bytes', side_effect=AssertionError('legacy core output')):
            saved = self.run_native(source, until=73)
            target = self.make.create(0)
            result = self.run_native(target, selection=saved.selection, until=150)
        self.assertIsInstance(result.layers, bytes); self.assertIsInstance(result.result, bytes)
        self.assertFalse(json.loads(result.result)['product_profile_active'])
        self.assertFalse((self.inputs.parent/'unused-output').exists())
        self.assertEqual(0, self.owner.active_borrows)
        self.cap.assert_live()

    def test_compatibility_mismatch_rejects_before_restore_and_backwards_stop_before_adoption(self):
        source = self.make.create(0); source.run(until=73)
        selected = self.save(source.export_state(), source)
        other_seed = self.make.create(63); before = other_seed.export_state()
        with patch.object(other_seed, 'restore_state', side_effect=AssertionError('must not restore')):
            with self.assertRaisesRegex(ValueError, 'compatibility'):
                self.run_native(other_seed, selection=selected)
        self.assertEqual(before, other_seed.export_state())
        target = self.make.create(0); before = target.export_state()
        with self.assertRaises(ValueError):
            self.run_native(target, selection=selected, until=72)
        self.assertEqual(before, target.export_state())
        self.assertEqual(0, self.owner.active_borrows)

    def test_sticky_persistence_keeps_existing_journal_idempotent_after_release(self):
        session = self.make.create(0)
        identity = deepcopy(session.rt.export_state()['adapter_journal']['identity'])
        self.run_native(session, until=73)
        before = session.export_state()
        session.rt.enable_adapter_journal(identity)
        self.assertEqual(before, session.export_state())
        with self.assertRaisesRegex(ValueError, 'frozen'): session.rt.configure([])
        with self.assertRaises(ValueError):
            session.rt.enable_adapter_journal(dict(identity, adapter='unapproved'))
        self.assertIsNone(session.rt.store)
        self.assertEqual(0, self.owner.active_borrows)

    def test_live_configuration_drift_is_rejected_before_output_and_restore(self):
        from gapengine.melos_choice_provider import MelosChoiceProvider
        from gapengine.genome import Genome
        def deadline(s): s.trial.p['deadline'] -= 1
        def scenario(s): s.trial.scenario['initial_information']['Melos']['changed'] = True
        def goals(s): s.trial.goals['Melos']['honor'] += 1
        def genome(s): s.trial.genome.category_weight['I'] = .1
        def seed(s): s.rt.sim.seed += 1
        def provider(s): s.stage.provider = MelosChoiceProvider(Genome.neutral(), 63)
        def contract(s): s.rt.contract['travel_minutes']['outskirts']['pass'] += 1
        for mutate in (deadline, scenario, goals, genome, seed, provider, contract):
            with self.subTest(mutation=mutate.__name__):
                session = self.make.create(0); snapshot = session.export_state()
                mutate(session); before = session.export_state(); objects = self.real_objects(session)
                with patch('execution.melos_generation.RunStore', side_effect=AssertionError('must not borrow')):
                    with self.assertRaises(ValueError): self.run_native(session, until=0)
                with self.assertRaises(ValueError): session.restore_state(snapshot)
                self.assertEqual(before, session.export_state())
                self.assert_real_objects(objects, session)
                self.assertEqual({}, self.files())
                self.assertEqual(0, self.owner.active_borrows)

    def test_capture_world_and_runtime_binding_rejected_before_factory_creation(self):
        session = self.make.create(0)
        with patch.object(session.rt.world, 'calendar_capture', None), \
                patch.object(self.make, 'create', side_effect=AssertionError('must not stage')):
            with self.assertRaises(ValueError): self.run_native(session, until=0)
        with patch.object(session.rt.sim, 'calendar_capture', None), \
                patch.object(self.make, 'create', side_effect=AssertionError('must not stage')):
            with self.assertRaises(ValueError): self.run_native(session, until=0)
        self.assertEqual({}, self.files())
        self.assertEqual(0, self.owner.active_borrows)

    def test_shared_progress_is_atomically_claimed_before_preflight(self):
        sessions = [self.make.create(seed) for seed in (0, 63)]
        progress = MelosGenerationProgress()
        entered, release = threading.Event(), threading.Event()
        original = sessions[0].stage._staging_trial
        def paused_preflight():
            entered.set()
            if not release.wait(timeout=10): raise AssertionError('Preflight release timed out')
            return original()
        with patch.object(sessions[0].stage, '_staging_trial', side_effect=paused_preflight), \
                patch.object(sessions[1].stage, '_staging_trial', side_effect=AssertionError('second preflight')), \
                ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_melos_generation, sessions[0], self.owner, until=0,
                                 stream_id='a'*32, progress=progress)
            try:
                self.assertTrue(entered.wait(timeout=10))
                with self.assertRaisesRegex(ValueError, 'fresh per-call'):
                    run_melos_generation(sessions[1], self.owner, until=0,
                                         stream_id='b'*32, progress=progress)
                self.assertEqual({}, self.files())
                self.assertEqual(0, self.owner.active_borrows)
                self.assertFalse(sessions[1].rt._generation_persistence_started)
            finally:
                release.set()
            result = future.result(timeout=10)
        self.assertEqual(result.selection, progress.selection)
        self.assertEqual(0, json.loads(result.result)['seed'])
        self.assertEqual(1, len(self.files()))
        self.assertEqual(0, self.owner.active_borrows)
        with self.assertRaisesRegex(ValueError, 'fresh per-call'):
            self.run_native(sessions[1], until=0, progress=progress)
        # Independent holders remain supported for distinct concurrent sessions.
        def independent(index):
            return run_melos_generation(self.make.create(index), self.owner, until=0,
                                        stream_id=str(index+3)*32,
                                        progress=MelosGenerationProgress())
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(independent, (0, 1)))
        self.assertNotEqual(a.selection, b.selection)
        self.assertEqual([0, 1], [json.loads(r.result)['seed'] for r in (a, b)])
        self.assertEqual(0, self.owner.active_borrows)

    def test_progress_is_one_attempt_even_when_preflight_fails(self):
        session = self.make.create(0); progress = MelosGenerationProgress()
        with patch.object(session.rt.world, 'calendar_capture', None):
            with self.assertRaises(ValueError): self.run_native(session, until=0, progress=progress)
        with patch.object(session.stage, '_staging_trial', side_effect=AssertionError('reused preflight')):
            with self.assertRaisesRegex(ValueError, 'fresh per-call'):
                self.run_native(session, until=0, progress=progress)
        self.assertEqual({}, self.files())
        self.assertIsNone(progress.selection)
        self.assertFalse(progress.saved)

    def test_changed_approved_source_is_rejected_before_any_output_borrow(self):
        session = self.make.create(0); before = session.export_state()
        path = self.source/'melos_runner.py'
        original = path.read_bytes(); path.write_bytes(original + b'\n# unapproved\n')
        with patch('execution.melos_generation.RunStore', side_effect=AssertionError('must not borrow')):
            with self.assertRaises(ValueError): self.run_native(session, until=73)
        path.write_bytes(original)
        self.assertEqual(before, session.export_state())
        self.assertEqual({}, self.files())
        self.assertEqual(0, self.owner.active_borrows)

    def test_public_legacy_route_stays_closed_and_invalid_owner_selection_do_no_io(self):
        session = self.make.create(0); before = session.export_state()
        for resume in (False, True):
            with patch('execution.melos_session.CheckpointStore', side_effect=AssertionError('legacy store')):
                with self.assertRaisesRegex(ValueError, 'legacy save writers are unavailable'):
                    session.run_saved(self.root/'forbidden', resume=resume)
        for owner, selected in ((self.output, None), (self.owner, 'latest')):
            with self.assertRaises(ValueError):
                run_melos_generation(session, owner, selection=selected)
        self.assertEqual({}, self.files())
        self.assertEqual(before, session.export_state())
        self.assertFalse((self.root/'forbidden').exists())


if __name__ == '__main__':
    unittest.main()
