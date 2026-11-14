"""Actual captured runtime native save, semantic restore and deterministic resume."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import threading
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.calendar_capture import open_calendar_capture
from engine.calendar_checkpoint import compatibility
from engine.calendar_generation_capture import NativeGenerationCapture
from engine.calendar_generation_runtime import run_calendar_generation, GenerationProgress
from engine.calendar_inputs import load_captured_project
from engine.calendar_parent_owner import _ExistingParentOwner
from engine.calendar_run_store import RunStore, NativeWriterPort
from engine.sim import Simulation

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'projects/calendar_workshop_example/world.yaml'


@unittest.skipUnless(sys.platform == 'linux' or os.name == 'nt', 'native Linux or Windows required')
class GenerationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'output'; self.output.mkdir()
        self.owner = _ExistingParentOwner(self.output, backend='windows' if os.name == 'nt' else 'linux')
        self.addCleanup(self.owner.close)

    def runtime(self):
        capture = open_calendar_capture(PROJECT, spool_parent=self.root)
        self.addCleanup(capture.close)
        world, people = load_captured_project(capture)
        rt = Simulation(0, world, people, self.root / 'unused', calendar_capture=capture).slot_runtime
        rt.configure([])
        return rt

    def test_real_native_save_restore_and_deterministic_continuation(self):
        full = self.runtime()
        a = run_calendar_generation(full, self.owner, stream_id='a' * 32)
        split = self.runtime()
        b = run_calendar_generation(split, self.owner, until=split.now + 1, stream_id='b' * 32)
        original = {p.name: p.read_bytes() for p in self.output.iterdir()}
        resumed = self.runtime()
        c = run_calendar_generation(resumed, self.owner, selection=b.selection, stream_id='c' * 32)
        self.assertEqual(full.export_state(), resumed.export_state())
        self.assertEqual(a.layers, c.layers)
        self.assertEqual(a.summary, c.summary)
        for name, data in original.items(): self.assertEqual(data, (self.output / name).read_bytes())
        self.assertTrue(a.done); self.assertTrue(c.done)
        self.assertEqual(0, self.owner.active_borrows)
        self.assertFalse((self.root / 'unused').exists())

    def test_completed_resume_is_noop_and_keeps_exact_derived_bytes(self):
        complete = run_calendar_generation(self.runtime(), self.owner, stream_id='d' * 32)
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        again = run_calendar_generation(self.runtime(), self.owner, selection=complete.selection, stream_id='e' * 32)
        self.assertEqual(complete.selection, again.selection)
        self.assertFalse(again.saved)
        self.assertEqual(complete.layers, again.layers)
        self.assertEqual(complete.summary, again.summary)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_invalid_semantics_never_run_or_export_or_partially_adopt(self):
        rt = self.runtime(); before = deepcopy(rt.export_state())
        invalid = deepcopy(before); invalid['now'] = -1
        with RunStore(self.owner, NativeWriterPort(), NativeGenerationCapture(), compatibility(rt.inputs(), rt.commands), stream_id='f' * 32) as store:
            selected = store.initialize(invalid).selection()
        names = sorted(p.name for p in self.output.iterdir())
        with patch.object(rt.time_kernel, 'run', side_effect=AssertionError('must not simulate')), \
                patch.object(rt, '_layers_bytes', side_effect=AssertionError('must not export')):
            with self.assertRaises(ValueError):
                run_calendar_generation(rt, self.owner, selection=selected, stream_id='1' * 32)
        self.assertEqual(before, rt.export_state())
        self.assertEqual(names, sorted(p.name for p in self.output.iterdir()))
        self.assertEqual(0, self.owner.active_borrows)

    def test_native_path_never_calls_legacy_checkpoint_or_output_writers(self):
        rt = self.runtime()
        with patch('engine.calendar_runtime.CheckpointStore', side_effect=AssertionError('legacy checkpoint')), \
                patch('engine.calendar_runtime.atomic_bytes', side_effect=AssertionError('legacy output')):
            result = run_calendar_generation(rt, self.owner, until=rt.now + 1, stream_id='2' * 32)
        self.assertIsInstance(result.layers, bytes)
        self.assertIsInstance(result.summary, bytes)
        self.assertEqual(0, self.owner.active_borrows)

    def test_native_persistence_freezes_commands_and_journal_after_handles_close(self):
        rt = self.runtime()
        commands = [{'id': 'in-flight', 'actor': 'Ada', 'at': rt.now, 'verb': 'wait', 'minutes': 10}]
        rt.configure(commands)
        result = run_calendar_generation(rt, self.owner, until=rt.now+1, stream_id='3'*32)
        self.assertIsNone(rt.store)
        with self.assertRaisesRegex(ValueError, 'frozen'):
            rt.configure([])
        with self.assertRaisesRegex(ValueError, 'frozen'):
            rt.configure(commands)
        fresh = self.runtime(); fresh.configure(commands)
        run_calendar_generation(fresh, self.owner, selection=result.selection, stream_id='4'*32)
        with self.assertRaisesRegex(ValueError, 'frozen'):
            fresh.configure([])
        with self.assertRaisesRegex(ValueError, 'before persistence'):
            fresh.enable_adapter_journal({'adapter': 'new', 'schema_version': 1,
                                          'source_hash': 'a'*64, 'config_hash': 'b'*64})
        self.assertEqual(0, self.owner.active_borrows)

    def test_failed_initial_write_does_not_thaw_configuration(self):
        rt = self.runtime(); before = rt.export_state()
        with patch.object(NativeWriterPort, 'open', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                run_calendar_generation(rt, self.owner, stream_id='5'*32)
        with self.assertRaisesRegex(ValueError, 'frozen'):
            rt.configure([])
        self.assertEqual(before, rt.export_state())
        self.assertEqual(0, self.owner.active_borrows)

    def test_same_existing_journal_identity_remains_idempotent_after_persistence(self):
        rt = self.runtime()
        identity = {'adapter': 'existing', 'schema_version': 1,
                    'source_hash': 'a'*64, 'config_hash': 'b'*64}
        rt.enable_adapter_journal(identity)
        run_calendar_generation(rt, self.owner, until=rt.now+1, stream_id='6'*32)
        before = rt.export_state()
        rt.enable_adapter_journal(identity)
        self.assertEqual(before, rt.export_state())
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            rt.enable_adapter_journal(dict(identity, adapter='other'))

    def test_checksum_valid_malformed_rows_rejected_before_adoption_run_or_export(self):
        source = self.runtime()
        source.configure([{'id': 'wait', 'actor': 'Ada', 'at': source.now, 'verb': 'wait', 'minutes': 1}])
        run_calendar_generation(source, self.owner, until=source.now+2, stream_id='7'*32)
        valid = source.export_state()
        index = next(i for i, row in enumerate(valid['rows']) if row['verb'] == 'action_completed')
        changes = [
            lambda row: row.pop('verb'),
            lambda row: row.update(verb=[]),
            lambda row: row.update(subject='unknown'),
            lambda row: row.update(absolute_minute=-1),
            lambda row: row.update(turn=True),
            lambda row: row.update(day=False),
            lambda row: row.update(date='2000-01-01'),
            lambda row: row.update(args={}),
            lambda row: row.update(delta=None),
            lambda row: row.update(details=[]),
            lambda row: row['details'].update(caused_by=[None]),
            lambda row: row['details'].pop('choice'),
            lambda row: row['details'].update(choice=[]),
            lambda row: row['details']['choice'].update(verb=[]),
            lambda row: row['details'].pop('duration_minutes'),
            lambda row: row['details'].update(duration_minutes=True),
            lambda row: row['details'].update(duration_minutes=-1),
            lambda row: row['details'].update(started_at=0),
        ]
        for number, change in enumerate(changes):
            with self.subTest(mutation=number):
                rt = self.runtime(); rt.configure(source.commands)
                before = deepcopy(rt.export_state())
                invalid = deepcopy(valid); change(invalid['rows'][index])
                with RunStore(self.owner, NativeWriterPort(), NativeGenerationCapture(),
                              compatibility(rt.inputs(), rt.commands), stream_id=f'{number+100:032x}') as store:
                    selected = store.initialize(invalid).selection()
                files = {p.name: p.read_bytes() for p in self.output.iterdir()}
                with patch.object(rt.time_kernel, 'run', side_effect=AssertionError('must not simulate')), \
                        patch.object(rt, '_layers_bytes', side_effect=AssertionError('must not export layers')), \
                        patch.object(rt, '_summary_bytes', side_effect=AssertionError('must not export summary')):
                    with self.assertRaises(ValueError):
                        run_calendar_generation(rt, self.owner, selection=selected, stream_id=f'{number+200:032x}')
                self.assertEqual(before, rt.export_state())
                self.assertEqual(files, {p.name: p.read_bytes() for p in self.output.iterdir()})
                self.assertEqual(0, self.owner.active_borrows)

    def test_backwards_selected_stop_rejects_before_live_adoption(self):
        source = self.runtime()
        saved = run_calendar_generation(source, self.owner, until=source.now+5, stream_id='8'*32)
        fresh = self.runtime(); before = deepcopy(fresh.export_state())
        progress = GenerationProgress()
        with patch.object(fresh, 'restore_state', side_effect=AssertionError('must not adopt')), \
                patch.object(fresh.time_kernel, 'run', side_effect=AssertionError('must not simulate')):
            with self.assertRaises(ValueError):
                run_calendar_generation(fresh, self.owner, selection=saved.selection,
                                        until=fresh.now+1, stream_id='9'*32, progress=progress)
        self.assertEqual(before, fresh.export_state())
        self.assertIsNone(progress.selection)
        self.assertFalse(progress.saved)
        self.assertEqual(0, self.owner.active_borrows)

    def test_progress_keeps_last_confirmed_head_when_transition_write_fails(self):
        rt = self.runtime(); before = deepcopy(rt.export_state()); progress = GenerationProgress()
        original = NativeWriterPort.open
        calls = []
        def fail_second(port, *args, **kwargs):
            calls.append(args[1])
            if len(calls) == 2:
                raise OSError('uncertain transition write')
            return original(port, *args, **kwargs)
        with patch.object(NativeWriterPort, 'open', fail_second):
            with self.assertRaisesRegex(OSError, 'uncertain transition'):
                run_calendar_generation(rt, self.owner, until=rt.now+1, stream_id='a1'*16, progress=progress)
        self.assertEqual(2, len(calls))
        self.assertEqual(calls[0], progress.selection.leaf)
        self.assertNotEqual(calls[1], progress.selection.leaf)
        self.assertTrue(progress.saved)
        self.assertEqual(before, rt.export_state())
        resumed = self.runtime()
        with RunStore(self.owner, NativeWriterPort(), NativeGenerationCapture(),
                      compatibility(resumed.inputs(), resumed.commands), stream_id='a2'*16) as store:
            resumed.restore_state(store.continue_from(progress.selection))
        self.assertEqual(before, resumed.export_state())
        self.assertEqual(0, self.owner.active_borrows)

    def test_progress_retains_committed_head_when_derived_export_fails(self):
        rt = self.runtime(); progress = GenerationProgress()
        with patch.object(rt, '_summary_bytes', side_effect=ValueError('derived export')):
            with self.assertRaisesRegex(ValueError, 'derived export'):
                run_calendar_generation(rt, self.owner, until=rt.now+1, stream_id='a3'*16, progress=progress)
        self.assertIsNotNone(progress.selection)
        fresh = self.runtime()
        run_calendar_generation(fresh, self.owner, selection=progress.selection,
                                until=rt.now, stream_id='a4'*16)
        self.assertEqual(rt.export_state(), fresh.export_state())
        with self.assertRaisesRegex(ValueError, 'fresh per-call'):
            run_calendar_generation(rt, self.owner, progress=progress)

    def test_progress_only_accepts_semantic_resume_and_noop_retains_base(self):
        complete = run_calendar_generation(self.runtime(), self.owner, stream_id='a5'*16)
        progress = GenerationProgress()
        again = run_calendar_generation(self.runtime(), self.owner, selection=complete.selection,
                                        stream_id='a6'*16, progress=progress)
        self.assertEqual(complete.selection, progress.selection)
        self.assertEqual(again.selection, progress.selection)
        self.assertFalse(progress.saved)
        rt = self.runtime(); bad = rt.export_state(); bad['now'] = -1
        with RunStore(self.owner, NativeWriterPort(), NativeGenerationCapture(),
                      compatibility(rt.inputs(), rt.commands), stream_id='a7'*16) as store:
            selected = store.initialize(bad).selection()
        rejected = GenerationProgress()
        with self.assertRaises(ValueError):
            run_calendar_generation(rt, self.owner, selection=selected, stream_id='a8'*16, progress=rejected)
        self.assertIsNone(rejected.selection)
        self.assertFalse(rejected.saved)

    def test_shared_progress_claim_is_atomic_before_compatibility_or_output(self):
        first, second = self.runtime(), self.runtime()
        progress = GenerationProgress(); entered = threading.Event(); release = threading.Event()
        calls = []
        original = compatibility
        def paused(inputs, commands):
            calls.append(1); entered.set()
            if not release.wait(5):
                raise AssertionError('preflight release timed out')
            return original(inputs, commands)
        with patch('engine.calendar_generation_runtime.compatibility', paused), ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(run_calendar_generation, first, self.owner,
                                  until=first.now+1, stream_id='b1'*16, progress=progress)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaisesRegex(ValueError, 'fresh per-call'):
                    run_calendar_generation(second, self.owner, until=second.now+1,
                                            stream_id='b2'*16, progress=progress)
                self.assertEqual([], list(self.output.iterdir()))
            finally:
                release.set()
            result = pending.result(timeout=10)
        self.assertEqual([1], calls)
        self.assertEqual(result.selection, progress.selection)
        self.assertEqual(0, self.owner.active_borrows)

    def test_result_uses_local_store_even_if_observer_state_changes(self):
        rt = self.runtime(); progress = GenerationProgress()
        original = rt._layers_bytes
        def export():
            progress._selection = None; progress._saved = False
            return original()
        with patch.object(rt, '_layers_bytes', export):
            result = run_calendar_generation(rt, self.owner, until=rt.now+1,
                                             stream_id='b3'*16, progress=progress)
        self.assertIsNotNone(result.selection)
        self.assertTrue(result.saved)
        self.assertIsNone(progress.selection)

    def test_initial_write_failure_keeps_consumed_holder_empty(self):
        rt = self.runtime(); progress = GenerationProgress()
        with patch.object(NativeWriterPort, 'open', side_effect=OSError('initial write')):
            with self.assertRaises(OSError):
                run_calendar_generation(rt, self.owner, stream_id='b4'*16, progress=progress)
        self.assertIsNone(progress.selection); self.assertFalse(progress.saved)
        with self.assertRaisesRegex(ValueError, 'fresh per-call'):
            run_calendar_generation(rt, self.owner, progress=progress)
        self.assertEqual(0, self.owner.active_borrows)
