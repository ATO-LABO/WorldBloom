"""Same-state summary serialization, independent of output publication."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine.calendar_checkpoint import canonical
from engine.calendar_inputs import load_project
from engine.sim import Simulation

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT/'projects/calendar_workshop_example'


class CalendarSummaryExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.commands = json.loads((PROJECT/'choices.json').read_bytes())

    def simulation(self, name='out'):
        world, people = load_project(PROJECT/'world.yaml')
        sim = Simulation(37, world, people, self.root/name)
        sim.slot_runtime.configure(deepcopy(self.commands))
        return sim

    def test_initial_export_needs_no_store_or_file_io(self):
        rt = self.simulation().slot_runtime
        before = deepcopy(rt.export_state())
        with patch('builtins.open', side_effect=AssertionError('file I/O')), \
             patch('io.open', side_effect=AssertionError('path I/O')), \
             patch('os.open', side_effect=AssertionError('descriptor I/O')), \
             patch.object(rt, 'inputs', side_effect=AssertionError('input reopen')):
            data = rt._summary_bytes()
        self.assertIsInstance(data, bytes)
        self.assertEqual(json.loads(data)['now'], rt.now)
        self.assertEqual(before, rt.export_state())
        self.assertFalse((self.root/'out').exists())

    def test_paused_export_is_saved_summary_byte_exact_and_repeatable(self):
        sim = self.simulation()
        rt = sim.slot_runtime
        rt.run(until=1442)
        before = deepcopy(rt.export_state())
        saved = (sim.out_dir/'summary.json').read_bytes()
        with patch('builtins.open', side_effect=AssertionError('file I/O')), \
             patch('io.open', side_effect=AssertionError('path I/O')), \
             patch('os.open', side_effect=AssertionError('descriptor I/O')):
            self.assertEqual(rt._summary_bytes(), saved)
            self.assertEqual(rt._summary_bytes(), saved)
        self.assertEqual(before, rt.export_state())

    def assert_relation_cache_refresh(self, *, fail_serialization):
        for cache_mode in ('cold', 'stale'):
            with self.subTest(cache_mode=cache_mode):
                rt = self.simulation('cache-'+cache_mode).slot_runtime
                relations = rt.world.relations
                before = deepcopy(rt.export_state())
                # Change a semantic value before the export under test. Record
                # it directly so this baseline does not warm the stale cache.
                relations.change('Ada', 'Bo', affinity=0.125)
                before['relations'] = deepcopy(relations._values)
                version = relations._version
                if cache_mode == 'cold':
                    relations._snapshot_cache = None
                previous_cache = relations._snapshot_cache
                if cache_mode == 'stale':
                    self.assertLess(previous_cache[0], version)
                with patch('builtins.open', side_effect=AssertionError('file I/O')), \
                     patch('io.open', side_effect=AssertionError('path I/O')), \
                     patch('os.open', side_effect=AssertionError('descriptor I/O')):
                    if fail_serialization:
                        with patch('engine.calendar_runtime.canonical',
                                   side_effect=ValueError('encoding failure')):
                            with self.assertRaisesRegex(ValueError, 'encoding failure'):
                                rt._summary_bytes()
                    else:
                        data = rt._summary_bytes()
                        self.assertEqual(json.loads(data)['relations'], before['relations'])
                # Memoization is permitted on success and on failure; actual
                # relationship values/version and all serialized state persist.
                self.assertIsNot(relations._snapshot_cache, previous_cache)
                self.assertEqual(relations._snapshot_cache[0], version)
                self.assertEqual(relations._version, version)
                self.assertEqual(relations._values, before['relations'])
                self.assertEqual(rt.export_state(), before)
                self.assertFalse((self.root/('cache-'+cache_mode)).exists())

    def test_cold_relation_cache_success_preserves_serialized_state(self):
        self.assert_relation_cache_refresh(fail_serialization=False)

    def test_cold_relation_cache_serialization_failure_preserves_serialized_state(self):
        self.assert_relation_cache_refresh(fail_serialization=True)

    def test_returned_bytes_do_not_alias_later_live_state(self):
        rt = self.simulation().slot_runtime
        data = rt._summary_bytes()
        decoded = json.loads(data)
        rt.observations['later'] = {'value': 1}
        self.assertNotIn('later', json.loads(data)['observations'])
        self.assertEqual(json.loads(data), decoded)
        self.assertNotEqual(rt._summary_bytes(), data)

    def test_optional_summary_sections_remain_serialized_without_mutation(self):
        rt = self.simulation().slot_runtime
        facts = {'facts': ['known']}
        planning = {'selection': ['one']}
        joint = {'segments': ['shared']}
        rt.facts = SimpleNamespace(export_state=lambda: facts)
        rt.planning = SimpleNamespace(snapshot=lambda: planning)
        rt.joint = SimpleNamespace(snapshot=lambda: joint,
            work=SimpleNamespace(specs={'pieces': {}}, remaining=lambda task: 3))
        before = deepcopy((facts, planning, joint))
        result = json.loads(rt._summary_bytes())
        self.assertEqual(result['fact_model'], facts)
        self.assertEqual(result['goal_planning'], planning)
        self.assertEqual(result['joint_activities'], joint)
        self.assertEqual(result['remaining_work']['pieces']['minutes'], 3.0)
        self.assertEqual((facts, planning, joint), before)

    def test_outputs_preserve_layer_summary_record_order(self):
        rt = self.simulation().slot_runtime
        rt.store = SimpleNamespace(out=self.root/'not-created')
        calls = []
        expected = rt._summary_bytes()
        with patch('engine.calendar_runtime.atomic_bytes',
                   side_effect=lambda path, data: calls.append((path.name, data))), \
             patch('engine.run_record.commit_run_record',
                   side_effect=lambda sim: calls.append(('run-record.json', None))):
            rt._outputs()
        self.assertEqual([name for name, _ in calls],
                         ['layers.jsonl', 'summary.json', 'run-record.json'])
        self.assertEqual(calls[1][1], expected)
        self.assertFalse(rt.store.out.exists())

    def test_layer_failure_does_not_generate_summary_or_commit_record(self):
        rt = self.simulation().slot_runtime
        rt.store = SimpleNamespace(out=self.root/'out')
        with patch('engine.calendar_runtime.atomic_bytes', side_effect=OSError('layer failure')), \
             patch.object(rt, '_summary_bytes') as summary, \
             patch('engine.run_record.commit_run_record') as record:
            with self.assertRaisesRegex(OSError, 'layer failure'):
                rt._outputs()
        summary.assert_not_called()
        record.assert_not_called()

    def test_summary_generation_failure_occurs_after_layer_and_before_record(self):
        rt = self.simulation().slot_runtime
        rt.store = SimpleNamespace(out=self.root/'out')
        calls = []
        with patch('engine.calendar_runtime.atomic_bytes',
                   side_effect=lambda path, data: calls.append(path.name)), \
             patch.object(rt, '_summary_bytes', side_effect=ValueError('summary failure')), \
             patch('engine.run_record.commit_run_record') as record:
            with self.assertRaisesRegex(ValueError, 'summary failure'):
                rt._outputs()
        self.assertEqual(calls, ['layers.jsonl'])
        record.assert_not_called()

    def test_summary_write_failure_preserves_prior_checkpoint_and_summary(self):
        sim = self.simulation()
        rt = sim.slot_runtime
        rt.run(until=1442)
        before = {p.name: p.read_bytes() for p in sim.out_dir.iterdir() if p.is_file()}
        from engine.calendar_runtime import atomic_bytes
        def fail_summary(path, data):
            if path.name == 'summary.json':
                raise OSError('summary write failure')
            return atomic_bytes(path, data)
        with patch('engine.calendar_runtime.atomic_bytes', side_effect=fail_summary), \
             patch('engine.run_record.commit_run_record') as record:
            with self.assertRaisesRegex(OSError, 'summary write failure'):
                rt._outputs()
        record.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in sim.out_dir.iterdir() if p.is_file()})

    def test_full_pause_resume_and_completed_resume_are_byte_identical(self):
        full = self.simulation('full')
        full.slot_runtime.run()
        self.simulation('split').slot_runtime.run(until=1442)
        resumed = self.simulation('split')
        resumed.slot_runtime.run(resume=True)
        def files(sim):
            return {p.name: p.read_bytes() for p in sim.out_dir.iterdir() if p.is_file()}
        self.assertEqual(files(full), files(resumed))
        self.assertEqual(full.slot_runtime.export_state(), resumed.slot_runtime.export_state())
        self.assertEqual(resumed.slot_runtime._summary_bytes(), files(resumed)['summary.json'])
        before = files(resumed)
        self.simulation('split').slot_runtime.run(resume=True)
        self.assertEqual(before, files(resumed))

    def test_serialization_error_preserves_serialized_state_without_output(self):
        rt = self.simulation().slot_runtime
        before = deepcopy(rt.export_state())
        with patch('engine.calendar_runtime.canonical', side_effect=ValueError('encoding failure')):
            with self.assertRaisesRegex(ValueError, 'encoding failure'):
                rt._summary_bytes()
        self.assertEqual(canonical(before), canonical(rt.export_state()))
        self.assertFalse((self.root/'out').exists())


if __name__ == '__main__':
    unittest.main()
