"""Bounded source isolation/admission checks; no scenario execution batches."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import inspect
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from execution.melos_factory import MelosSessionFactory, approved_catalogs, _transform
from execution.melos_plan import MelosPlanBinding
from gapengine.genome import Genome


class TransformTests(unittest.TestCase):
    def test_unrecognized_packaging_shape_fails_closed(self):
        for source in ('ROOT=1', "ROOT=Path(__file__).resolve().parent\nsys.path.insert(1, 'other')"):
            with self.assertRaises(ValueError): _transform(source, '<test>')

    def test_catalogs_are_detached(self):
        first = approved_catalogs(); first['sources']['melos_runner.py'] = '0'*64
        self.assertNotEqual(first, approved_catalogs())


@unittest.skipUnless(os.environ.get('MELOS_APPROVED_ADAPTER_DIR') and os.environ.get('MELOS_INPUT_DIR'),
                     'approved external fixture paths not supplied')
class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalogs = approved_catalogs()

    def copy(self, name):
        folder = self.root/name; source = folder/'sources'; inputs = folder/'melos-input'
        source.mkdir(parents=True)
        original = Path(os.environ['MELOS_APPROVED_ADAPTER_DIR'])
        for name in self.catalogs['sources']: shutil.copyfile(original/name, source/name)
        shutil.copytree(os.environ['MELOS_INPUT_DIR'], inputs)
        return source, inputs

    def factory(self, source, inputs, catalogs=None):
        c = catalogs or self.catalogs
        return MelosSessionFactory(source, inputs, approved_source_hashes=c['sources'],
                                   approved_input_hashes=c['inputs'], genome=Genome.neutral())

    def test_two_concurrent_factories_keep_root_and_import_path_isolated(self):
        one = self.copy('one'); two = self.copy('two'); before = list(sys.path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            factories = list(pool.map(lambda pair: self.factory(*pair), (one, two)))
        a = factories[0].create(0); b = factories[1].create(63); again = factories[0].create(1)
        self.assertEqual(sys.path, before)
        self.assertEqual(a.rt.world.source.parent, one[1])
        self.assertEqual(b.rt.world.source.parent, two[1])
        self.assertEqual(again.rt.world.source.parent, one[1])
        self.assertIsNot(type(a.trial), type(b.trial))
        self.assertEqual(Path(inspect.getsourcefile(type(a.trial))), one[0]/'melos_runner.py')
        self.assertFalse((one[1].parent/'unused-output').exists())
        self.assertFalse((two[1].parent/'unused-output').exists())
        for source, inputs in (one, two):
            for name, h in self.catalogs['sources'].items():
                self.assertEqual(hashlib.sha256((source/name).read_bytes()).hexdigest(), h)

    def test_factory_sessions_bind_both_seed_slots_with_actual_source_closure(self):
        source, inputs = self.copy('bound'); f = self.factory(source, inputs)
        binding = MelosPlanBinding({0: f.create(0), 63: f.create(63)})
        binding.bind_session(f.create(0), 0)
        binding.bind_session(f.create(63), 63)
        self.assertNotEqual(binding.seed_admission(0)['actual_provider'], binding.seed_admission(63)['actual_provider'])
        self.assertTrue(f.identity['product_active'])
        self.assertEqual(f.identity['transform'], 'approved_melos_root_binding_v1')

    def test_content_preserving_relocation_keeps_plan_and_binds_fresh_slots(self):
        one = self.copy('admission'); two = self.copy('frozen')
        a = self.factory(*one); b = self.factory(*two)
        original = MelosPlanBinding({0: a.create(0), 63: a.create(63)})
        relocated = MelosPlanBinding({0: b.create(0), 63: b.create(63)})
        self.assertEqual(original.plan.to_dict(), relocated.plan.to_dict())
        self.assertEqual(original.plan.sha256, relocated.plan.sha256)
        for seed in (0, 63):
            self.assertEqual(original.seed_admission(seed), relocated.seed_admission(seed))
            fresh = b.create(seed); before = fresh.export_state()
            self.assertIs(original.bind_session(fresh, seed, plan=original.plan), fresh)
            self.assertEqual(fresh.export_state(), before)
        tampered = b.create(0)
        path = two[1]/'scenario.yaml'; path.write_bytes(path.read_bytes()+b'\n# changed\n')
        with self.assertRaises(ValueError): original.bind_session(tampered, 0)

    def test_copied_checkpoint_resumes_midmove_and_wedding_with_exact_bytes(self):
        a = self.factory(*self.copy('checkpoint-original'))
        b = self.factory(*self.copy('checkpoint-relocated'))
        for seed, pause, end in ((0, 73, 101), (63, 2220, 2280)):
            control_dir = self.root/('control-'+str(seed))
            original_dir = self.root/('saved-'+str(seed))
            relocated_dir = self.root/('restored-'+str(seed))
            continuous = a.create(seed); continuous.run_saved(control_dir, until=end)
            split = a.create(seed); split.run_saved(original_dir, until=pause)
            if seed == 63:
                self.assertEqual(split.stage.pending['candidate']['id'], 'wedding')
            else:
                self.assertEqual(split.rt.people['Melos'].zone, '移動中')
            shutil.copytree(original_dir, relocated_dir)
            fresh = b.create(seed); fresh.run_saved(relocated_dir, until=end, resume=True)
            self.assertEqual(continuous.export_state(), fresh.export_state())
            for name in ('calendar-state.json', 'layers.jsonl', 'result.json'):
                self.assertEqual((control_dir/name).read_bytes(), (relocated_dir/name).read_bytes(), name)

    def test_tampered_source_and_even_matching_caller_catalog_are_rejected(self):
        source, inputs = self.copy('source')
        path = source/'melos_runner.py'; path.write_bytes(path.read_bytes()+b'\n# unapproved change\n')
        with self.assertRaises(ValueError): self.factory(source, inputs)
        catalog = deepcopy(self.catalogs); catalog['sources'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError): self.factory(source, inputs, catalog)

    def test_tampered_input_and_even_matching_caller_catalog_are_rejected(self):
        source, inputs = self.copy('input')
        path = inputs/'scenario.yaml'; path.write_bytes(path.read_bytes()+b'\n# unapproved change\n')
        with self.assertRaises(ValueError): self.factory(source, inputs)
        catalog = deepcopy(self.catalogs); catalog['inputs'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError): self.factory(source, inputs, catalog)

    def test_seed_root_and_post_factory_drift_are_rejected(self):
        source, inputs = self.copy('drift'); f = self.factory(source, inputs)
        for seed in (True, -1, 2147483648, '0'):
            with self.assertRaises(ValueError): f.create(seed)
        (source/'melos_runner.py').write_text('bad source')
        with self.assertRaises(ValueError): f.create(0)
        with self.assertRaises(ValueError): self.factory(source, inputs.parent)


if __name__ == '__main__': unittest.main()
