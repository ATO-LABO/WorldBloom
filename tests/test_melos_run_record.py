"""Focused saved-revision checks; no batch, product API, or shared UI edits."""
from copy import deepcopy
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.execution_plan import digest
from execution.melos_plan import MelosPlanBinding
from execution.melos_session import MelosSession
from execution.melos_run_record import publish_saved_run_record, describe_saved_run_record
from execution.provenance import canonical
from gapengine.genome import Genome
from gapengine.melos_choice_provider import MelosChoiceProvider


@unittest.skipUnless(os.environ.get('MELOS_APPROVED_ADAPTER_DIR') and os.environ.get('MELOS_INPUT_DIR'),
                     'approved external fixture paths not supplied')
class MelosRunRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = Path(os.environ['MELOS_APPROVED_ADAPTER_DIR'])
        sys.path.insert(0, str(folder))
        import melos_runner
        cls.base = melos_runner
        root = Path(os.environ['MELOS_INPUT_DIR']); cls.base.ROOT = root.parent
        cls.inputs = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(root.rglob('*.yaml'))}
        cls.sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (folder/'melos_runner.py', folder/'ninegene_melos.py')}

    def session(self, seed=0):
        g = Genome.neutral()
        return MelosSession(self.base.Trial(g, seed), input_hashes=self.inputs, source_hashes=self.sources,
                            choice_provider=MelosChoiceProvider(g, seed))

    def test_paused_saved_record_preserves_actual_and_aggregate_identities(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.session(); binding = MelosPlanBinding({0: s, 63: self.session(63)})
            s.run_saved(d, until=73)
            pristine = self.session(); before = pristine.export_state()
            record = publish_saved_run_record(d, binding, pristine, 0)
            self.assertEqual(pristine.export_state(), before)
            self.assertIsNone(pristine.rt.sim.verified_execution)
            self.assertIsNone(s.rt.sim.verified_execution)
            self.assertIsNone(record['verified_execution'])
            self.assertNotEqual(record['provider_identity']['config_hash'], record['execution_plan']['provider']['config_hash'])
            self.assertEqual(record['melos_admission'], binding.seed_admission(0))
            self.assertEqual(record['stop_reason'], 'paused')
            self.assertEqual(record['observed_through'], 73)
            self.assertFalse(record['observations']['scenario_finished'])
            self.assertFalse(record['observations']['calendar_horizon_reached'])
            self.assertIsNone(record['failure_evidence'])
            self.assertTrue(record['product_profile_active'])
            if shutil.which('node'):
                script = """const fs=require('fs');
const ui=require('./viewer/static/purpose-workspace.js');
const normalized=ui.validateRunRecord(JSON.parse(fs.readFileSync(0,'utf8')));
console.log(JSON.stringify({kind:normalized.kind,verification:normalized.plan_verification}));"""
                process = subprocess.run(['node', '-e', script], input=canonical(record),
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                                         cwd=Path(__file__).resolve().parents[1])
                self.assertEqual(json.loads(process.stdout),
                                 {'kind': 'common_run_record', 'verification': 'unchecked'})
            for name, expected in [('layers.jsonl', record['ledger']['sha256']),
                                   ('result.json', record['result_sha256']),
                                   ('calendar-state.json', record['checkpoint_sha256'])]:
                self.assertEqual(hashlib.sha256((Path(d)/name).read_bytes()).hexdigest(), expected)
            self.assertEqual(describe_saved_run_record(d, binding, self.session(), 0), record)
            record['melos_admission']['seed'] = 9
            self.assertEqual(describe_saved_run_record(d, binding, self.session(), 0)['melos_admission']['seed'], 0)

    def test_continuous_and_resumed_same_revision_have_identical_records(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); binding = MelosPlanBinding({0: self.session()})
            self.session().run_saved(root/'continuous')
            final = publish_saved_run_record(root/'continuous', binding, self.session(), 0)
            self.session().run_saved(root/'split', until=73)
            partial = publish_saved_run_record(root/'split', binding, self.session(), 0)
            self.session().run_saved(root/'split', resume=True)
            resumed = publish_saved_run_record(root/'split', binding, self.session(), 0)
            self.assertEqual(final, resumed)
            self.assertNotEqual(partial['run_id'], final['run_id'])
            self.assertTrue(final['observations']['scenario_finished'])
            self.assertEqual(final['observations']['calendar_horizon_reached'], final['observed_through'] == final['time_axis']['end'])
            self.assertEqual(final['actual_terminal']['verb'], 'observation_result')
            self.assertIsNone(final['verified_execution'])
            self.assertEqual((root/'continuous/run-record.json').read_bytes(), (root/'split/run-record.json').read_bytes())

    def test_rehashed_admission_and_derived_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); binding = MelosPlanBinding({0: self.session(), 63: self.session(63)})
            self.session().run_saved(root, until=73)
            good = publish_saved_run_record(root, binding, self.session(), 0)
            original = (root/'run-record.json').read_bytes()
            for edit in (lambda r: r['melos_admission'].update(seed=63),
                         lambda r: r['melos_admission'].update(initial_session_hash='0'*64),
                         lambda r: r.update(verified_execution={'seed': 0}),
                         lambda r: r.update(stop_reason='execution_failure'),
                         lambda r: r['provider_identity'].update(config_hash='0'*64)):
                bad = deepcopy(good); edit(bad); bad.pop('saved_revision_hash')
                bad['saved_revision_hash'] = digest(bad)
                (root/'run-record.json').write_bytes(canonical(bad))
                with self.assertRaises(ValueError): describe_saved_run_record(root, binding, self.session(), 0)
            (root/'run-record.json').write_bytes(original)
            for name in ('layers.jsonl', 'result.json'):
                saved = (root/name).read_bytes(); (root/name).write_bytes(b'{}\n')
                with self.assertRaises(ValueError): describe_saved_run_record(root, binding, self.session(), 0)
                (root/name).write_bytes(saved)
            self.assertEqual(describe_saved_run_record(root, binding, self.session(), 0), good)

    def test_wrong_slot_live_session_and_corrupt_checkpoint_reject_without_repair(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); s = self.session(); binding = MelosPlanBinding({0: s, 63: self.session(63)})
            s.run_saved(root, until=73)
            good = publish_saved_run_record(root, binding, self.session(), 0)
            record_bytes = (root/'run-record.json').read_bytes()
            for pristine, seed in ((self.session(63), 63), (self.session(), True), (s, 0)):
                with self.assertRaises(ValueError): publish_saved_run_record(root, binding, pristine, seed)
                self.assertEqual((root/'run-record.json').read_bytes(), record_bytes)
            checkpoint = json.loads((root/'calendar-state.json').read_bytes())
            checkpoint['payload']['state']['state']['finished'] = True
            (root/'calendar-state.json').write_bytes(canonical(checkpoint))
            with self.assertRaises(ValueError): publish_saved_run_record(root, binding, self.session(), 0)
            with self.assertRaises(ValueError): describe_saved_run_record(root, binding, self.session(), 0)
            self.assertEqual((root/'run-record.json').read_bytes(), record_bytes)

    def test_interrupted_publication_keeps_authority_and_repairs_only_derived_files(self):
        from execution import melos_run_record as module
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); binding = MelosPlanBinding({0: self.session()})
            self.session().run_saved(root, until=73)
            publish_saved_run_record(root, binding, self.session(), 0)
            self.session().run_saved(root, until=90, resume=True)
            checkpoint = (root/'calendar-state.json').read_bytes()
            real_atomic = module.atomic_bytes
            def fail_record(path, data):
                if Path(path).name == 'run-record.json': raise OSError('injected descriptor publication failure')
                real_atomic(path, data)
            with patch.object(module, 'atomic_bytes', side_effect=fail_record):
                with self.assertRaises(OSError): publish_saved_run_record(root, binding, self.session(), 0)
            self.assertEqual((root/'calendar-state.json').read_bytes(), checkpoint)
            with self.assertRaises(ValueError): describe_saved_run_record(root, binding, self.session(), 0)
            (root/'layers.jsonl').write_bytes(b'stale')
            (root/'result.json').unlink()
            repaired = publish_saved_run_record(root, binding, self.session(), 0)
            self.assertEqual(repaired['observed_through'], 90)
            self.assertEqual(describe_saved_run_record(root, binding, self.session(), 0), repaired)
            self.assertEqual((root/'calendar-state.json').read_bytes(), checkpoint)

    def test_unsaved_exception_does_not_become_fictional_failure_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.session(); binding = MelosPlanBinding({0: s})
            with patch.object(s, '_action', side_effect=RuntimeError('injected execution failure')):
                with self.assertRaises(RuntimeError): s.run_saved(d, until=73)
            record = publish_saved_run_record(d, binding, self.session(), 0)
            self.assertEqual(record['stop_reason'], 'paused')
            self.assertEqual(record['observations']['status'], 'unresolved')
            self.assertIsNone(record['failure_evidence'])
            self.assertIsNone(record['actual_terminal'])
            self.assertFalse(record['observations']['scenario_finished'])


if __name__ == '__main__': unittest.main()
