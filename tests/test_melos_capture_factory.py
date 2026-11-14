"""Private captured factory integration. Disposable copies, no output writers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.calendar_capture import open_calendar_capture
from execution.melos_factory import (MelosSessionFactory, approved_catalogs,
                                     CAPTURE_TRANSFORM_VERSION, TRANSFORM_VERSION)
from gapengine.genome import Genome

ROOT = Path(__file__).resolve().parents[1]


class CapturedFactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.catalog = approved_catalogs()

    def fixture(self, name, leaf='melos-input'):
        source = self.root/name/'sources'
        inputs = self.root/name/leaf
        shutil.copytree(ROOT/'execution/approved_melos_sources', source)
        shutil.copytree(ROOT/'projects/melos-input', inputs)
        return source, inputs

    def capture(self, inputs):
        return self.stack.enter_context(open_calendar_capture(inputs/'world.yaml', profile='melos'))

    def factory(self, source, inputs, capture=None, catalog=None):
        c = catalog or self.catalog
        return MelosSessionFactory(source, inputs, approved_source_hashes=c['sources'],
                                   approved_input_hashes=c['inputs'], genome=Genome.neutral(),
                                   calendar_capture=capture)

    def forbid_input_access(self, inputs):
        # Source integrity reads remain enabled. Only diagnostic input labels
        # are barred, including resolve/stat probes that would reopen authority.
        stack = ExitStack()
        for method in ('resolve', 'stat', 'lstat', 'open', 'read_bytes', 'read_text',
                       'exists', 'is_file', 'is_dir', 'is_symlink'):
            original = getattr(Path, method)
            def guarded(path, *args, _original=original, **kwargs):
                if path == inputs or inputs in path.parents:
                    raise AssertionError('Captured input pathname reused: ' + str(path))
                return _original(path, *args, **kwargs)
            stack.enter_context(patch.object(Path, method, guarded))
        return stack

    def test_renamed_namespace_never_reopened_and_trials_are_detached(self):
        source, inputs = self.fixture('renamed', 'custom-project')
        capture = self.capture(inputs)
        shutil.rmtree(inputs)
        with self.forbid_input_access(inputs):
            factory = self.factory(source, inputs, capture)
            first, second = factory.create(0), factory.create(63)
            self.assertIsNot(first.rt.world, second.rt.world)
            self.assertIsNot(first.rt.people['Melos'], second.rt.people['Melos'])
            first.trial.scenario['parameters']['deadline'] = -1
            self.assertNotEqual(second.trial.scenario['parameters']['deadline'], -1)
            third = factory.create(0)
            self.assertNotEqual(third.trial.scenario['parameters']['deadline'], -1)
            for session in (first, second, third):
                self.assertEqual(session.rt.world.source, capture.display_source)
                self.assertIsNone(session.rt.inputs()['subject_sources'])
                self.assertIsNone(session.rt.inputs()['input_provenance'])
                session.export_state()
            self.assertEqual(factory.identity['transform'], CAPTURE_TRANSFORM_VERSION)
        self.assertFalse((inputs.parent/'unused-output').exists())

    def test_seeds_match_default_in_memory_state_rows_and_stopping(self):
        source, inputs = self.fixture('parity')
        legacy = self.factory(source, inputs)
        captured = self.factory(source, inputs, self.capture(inputs))
        self.assertEqual(legacy.identity['transform'], TRANSFORM_VERSION)
        for seed in (0, 63):
            a, b = legacy.create(seed), captured.create(seed)
            self.assertEqual(a.rt.inputs(), b.rt.inputs())
            self.assertEqual(a.export_state(), b.export_state())
            self.assertEqual(a.run(), b.run())
            self.assertEqual(a.export_state(), b.export_state())
        self.assertFalse((inputs.parent/'unused-output').exists())

    def test_closed_capture_and_live_source_tampering_reject(self):
        source, inputs = self.fixture('liveness')
        capture = self.capture(inputs)
        factory = self.factory(source, inputs, capture)
        path = source/'ninegene_melos.py'
        original = path.read_bytes()
        path.write_bytes(original + b'\n# unapproved\n')
        with self.assertRaises(ValueError):
            factory.create(0)
        path.write_bytes(original)
        capture.close()
        with self.assertRaises((ValueError, RuntimeError)):
            factory.create(0)

    def test_wrong_selectors_config_and_caller_catalog_reject(self):
        source, inputs = self.fixture('selectors')
        capture = self.capture(inputs)
        factory = self.factory(source, inputs, capture)
        with self.assertRaises(ValueError):
            factory._module.World.from_yaml(inputs/'other.yaml')
        with self.assertRaises(ValueError):
            factory._module.read_subjects(capture.display_source, {})
        with self.assertRaises(ValueError):
            factory._module.read_subjects(inputs/'other.yaml', capture.config)
        with self.assertRaises(ValueError):
            self.factory(source, inputs.parent, capture)
        altered = deepcopy(self.catalog)
        altered['inputs']['scenario.yaml'] = '0'*64
        with self.assertRaises(ValueError):
            self.factory(source, inputs, capture, altered)

    def test_captured_run_saved_rejects_before_output_io(self):
        source, inputs = self.fixture('writer-guard')
        session = self.factory(source, inputs, self.capture(inputs)).create(0)
        output = self.root/'never-created-output'
        before = session.export_state()
        with patch('execution.melos_session.CheckpointStore',
                   side_effect=AssertionError('CheckpointStore must not be reached')):
            for resume in (False, True):
                with self.assertRaisesRegex(ValueError, 'Captured calendar prototype is input-only; legacy save writers are unavailable'):
                    session.run_saved(output, until=1, resume=resume)
        self.assertFalse(output.exists())
        self.assertEqual(before, session.export_state())
        # The default route still reaches its original writer, without invoking
        # that writer or publishing any test output.
        legacy = self.factory(source, inputs).create(0)
        with patch('execution.melos_session.CheckpointStore',
                   side_effect=NotImplementedError('default writer reached')) as store:
            with self.assertRaisesRegex(NotImplementedError, 'default writer reached'):
                legacy.run_saved(output, until=1)
            store.assert_called_once()
        self.assertFalse(output.exists())

    def test_wrong_capture_profile_and_empty_scenario_fail_closed(self):
        source, inputs = self.fixture('profile')
        generic = self.stack.enter_context(open_calendar_capture(inputs/'world.yaml'))
        with self.assertRaises(ValueError):
            self.factory(source, inputs, generic)
        capture = self.capture(inputs)
        factory = self.factory(source, inputs, capture)
        original = type(capture).yaml
        def empty_scenario(owner, role):
            return {} if role == 'scenario' else original(owner, role)
        with patch.object(type(capture), 'yaml', empty_scenario):
            with self.assertRaisesRegex(ValueError, 'Nonempty approved scenario'):
                factory.create(0)
        self.assertTrue(capture.yaml('scenario'))

    def test_concurrent_factories_have_isolated_capture_bindings(self):
        pairs = [self.fixture('one', 'first'), self.fixture('two', 'second')]
        captures = [self.capture(inputs) for _, inputs in pairs]
        before = list(sys.path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            factories = list(pool.map(lambda args: self.factory(*args),
                                      [(*pair, cap) for pair, cap in zip(pairs, captures)]))
            sessions = list(pool.map(lambda f: f.create(0), factories))
        self.assertEqual(before, sys.path)
        self.assertIsNot(type(sessions[0].trial), type(sessions[1].trial))
        for session, capture in zip(sessions, captures):
            self.assertEqual(session.rt.world.source, capture.display_source)


if __name__ == '__main__':
    unittest.main()
