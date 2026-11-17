"""Captured generation projection, not public route or native Windows evidence."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import shutil
import sys
import unittest
from unittest.mock import patch

from engine.calendar_generation import (SelectedGeneration, checkpoint_bytes, leaf_for,
                                         prepare_generation)
from engine.calendar_native_exports import PreparedNativeExports
from engine.execution_plan import digest
from execution.melos_generation import melos_generation_compatibility
from execution.melos_generation_record import project_melos_generation_record
from execution.melos_plan import MelosPlanBinding
from execution.melos_session import MelosSession
from execution.provenance import canonical
from execution.melos_adapter import MelosActionStage, _hash
from test_melos_generation import _CapturedSetup


class MelosGenerationRecordTests(_CapturedSetup, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.binding = MelosPlanBinding({0: self.make.create(0), 63: self.make.create(63)})

    def saved(self, seed=0, until=73, *, state=None):
        session = self.make.create(seed)
        if state is None:
            session.run(until=until)
            state = session.export_state()
        compat = melos_generation_compatibility(session)
        saved = prepare_generation(checkpoint_bytes(state, compat), expected_compatibility=compat,
                                   stream_id='a'*32, ordinal=0)
        return saved, session

    def project(self, saved, seed=0, **kwargs):
        return project_melos_generation_record(saved.selection(), saved.data,
            kwargs.get('factory', self.make), kwargs.get('binding', self.binding), seed)

    def record(self, bundle):
        return json.loads(bundle.run_record)

    def test_paused_and_complete_seeds_have_exact_refs_distinct_hashes_and_physical_evidence(self):
        for seed, until in ((0, 73), (63, 2220), (0, None), (63, None)):
            with self.subTest(seed=seed, until=until):
                saved, session = self.saved(seed, until)
                bundle = self.project(saved, seed); record = self.record(bundle)
                self.assertEqual(bundle.ledger, b''.join(canonical(r)+b'\n' for r in session.rt.rows))
                self.assertEqual(record['generation_sha256'], saved.ref.sha256)
                self.assertEqual(record['checkpoint_sha256'], hashlib.sha256(saved.checkpoint).hexdigest())
                self.assertEqual(record['session_state_sha256'], session.export_state()['sha256'])
                self.assertEqual(3, len({record[k] for k in ('generation_sha256','checkpoint_sha256','session_state_sha256')}))
                self.assertEqual(record['storage']['selected_generation'], {'leaf': saved.leaf, 'expected': {
                    'sha256': saved.ref.sha256, 'size': saved.ref.size, 'stream_id': saved.ref.stream_id, 'ordinal': saved.ref.ordinal}})
                self.assertFalse(record['storage']['restart_authority'])
                self.assertEqual('native_generation_exports_v1', record['storage']['kind'])
                self.assertEqual('not_connected', record['native_ui_admission'])
                self.assertFalse(record['independent_ui_verified']); self.assertIsNone(record['verified_execution'])
                self.assertIsNone(record['failure_evidence'])
                self.assertNotEqual(record['provider_identity'], record['execution_plan']['provider'])
                self.assertEqual(seed, record['melos_admission']['seed'])
                self.assertEqual(session.finished, record['observations']['scenario_finished'])
                self.assertEqual('complete' if session.finished else 'paused', json.loads(bundle.result)['completion'])
                if not session.finished:
                    self.assertEqual('paused', record['stop_reason']); self.assertIsNone(record['actual_terminal'])
                    self.assertEqual('unresolved', record['observations']['status'])
                if seed == 63 and session.finished:
                    self.assertTrue(record['observations']['completed_120_minute_wedding']['completed'])
                revision = record.pop('saved_revision_hash'); self.assertEqual(revision, digest(record))
                self.assertEqual(0, len(list(self.root.glob('unused-output'))))

    def test_projection_never_executes_callbacks_legacy_writers_or_copies_session(self):
        saved, session = self.saved(); before = session.export_state()
        with patch.object(MelosSession, 'run', side_effect=AssertionError('run forbidden')), \
                patch.object(MelosSession, '_choose_step', side_effect=AssertionError('choice forbidden')), \
                patch.object(MelosSession, '_action', side_effect=AssertionError('action forbidden')), \
                patch.object(MelosActionStage, 'advance', side_effect=AssertionError('advance forbidden')), \
                patch('execution.melos_run_record._prepare', side_effect=AssertionError('legacy forbidden')), \
                patch('engine.calendar_checkpoint.atomic_bytes', side_effect=AssertionError('write forbidden')):
            a = self.project(saved); b = self.project(saved)
        self.assertEqual(before, session.export_state())
        self.assertEqual(a.ledger, b.ledger); self.assertEqual(a.result, b.result)
        self.assertNotEqual(a.publication_id, b.publication_id)
        self.assertTrue(all(x.leaf != y.leaf for x,y in zip(a.artifacts(), b.artifacts())))
        with self.assertRaises(FrozenInstanceError): a.ledger = b'changed'
        with self.assertRaises(TypeError): deepcopy(self.cap)
        self.cap.assert_live()

    def test_continuous_split_and_terminal_noop_semantics_match(self):
        for seed, minute in ((0, 73), (63, 2220)):
            with self.subTest(seed=seed):
                full, original = self.saved(seed, None)
                partial, session = self.saved(seed, minute)
                continued = self.make.create(seed)
                continued.restore_state(partial.state(melos_generation_compatibility(continued)))
                continued.run()
                compat = melos_generation_compatibility(continued)
                split = prepare_generation(checkpoint_bytes(continued.export_state(), compat),
                    expected_compatibility=compat, stream_id='b'*32, ordinal=0, forked_from=partial.ref)
                a, b = self.project(full, seed), self.project(split, seed)
                self.assertEqual(a.ledger, b.ledger); self.assertEqual(a.result, b.result)
                r1, r2 = self.record(a), self.record(b)
                for key in ('storage','generation_sha256','saved_revision_hash','ledger','result'):
                    r1.pop(key); r2.pop(key)
                self.assertEqual(r1, r2)
                with patch.object(MelosSession, 'run', side_effect=AssertionError('terminal cannot execute')):
                    noop = self.project(split, seed)
                self.assertEqual(b.ledger, noop.ledger); self.assertEqual(b.result, noop.result)

    def test_wrong_independent_leaf_hash_size_stream_ordinal_and_nonbytes_reject(self):
        saved, _ = self.saved()
        for updates in ({'sha256':'0'*64}, {'size':saved.ref.size-1}, {'stream_id':'b'*32}, {'ordinal':1}):
            ref = replace(saved.ref, **updates)
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                project_melos_generation_record(SelectedGeneration(leaf_for(ref),ref), saved.data,self.make,self.binding,0)
        for selection in (None, saved.ref, SelectedGeneration('../'+saved.leaf,saved.ref)):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                project_melos_generation_record(selection,saved.data,self.make,self.binding,0)
        for data in (bytearray(saved.data), saved.data[:-1], saved.data+b'x'):
            with self.subTest(data_type=type(data)), self.assertRaises(ValueError):
                project_melos_generation_record(saved.selection(),data,self.make,self.binding,0)

    def test_wrong_seed_provider_admission_and_uncaptured_factory_reject(self):
        saved, _ = self.saved()
        for seed in (True,-1,1,63):
            with self.subTest(seed=seed), self.assertRaises(ValueError): self.project(saved, seed)
        other = MelosPlanBinding({63:self.make.create(63)})
        with self.assertRaises(ValueError): self.project(saved,binding=other)
        with patch.dict(self.binding._slots[0]['provider'], {'config_hash':'0'*64}):
            with self.assertRaises(ValueError): self.project(saved)
        with self.assertRaises(ValueError): self.project(saved,binding=object())
        legacy_source, legacy_inputs = self.fixture('legacy')
        legacy = self.factory(legacy_source,legacy_inputs)
        with self.assertRaises(ValueError): self.project(saved,factory=legacy)

    def test_rehashed_semantically_invalid_states_reject_before_derivation(self):
        saved, session = self.saved(); valid = session.export_state()
        mutations = {
            'step': lambda s: s.update(step_index=s['step_index']+1),
            'terminal': lambda s: s.update(phase='finished',finished=True),
            'physical': lambda s: s['stage']['state']['external']['state'].update(wedding=True),
            'provider': lambda s: s['stage']['state']['provider']['state']['counts'].update(Melos=100),
            'rng': lambda s: s['stage']['state']['runtime'].update(rng=[]),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                state=deepcopy(valid); mutate(state['state'])
                state['state']['stage']['sha256']=_hash(state['state']['stage']['state'])
                state['sha256']=_hash(state['state'])
                invalid,_=self.saved(state=state)
                with patch('execution.melos_generation_record.classify_session',side_effect=AssertionError('must not derive')):
                    with self.assertRaises(ValueError): self.project(invalid)
        self.assertEqual(valid,session.export_state())

    def test_closed_capture_and_factory_configuration_drift_reject(self):
        saved,_=self.saved()
        with patch.dict(self.make._genome['category_weight'], {'I':.7}):
            with self.assertRaises(ValueError): self.project(saved)
        self.cap.close()
        with self.assertRaises((ValueError,RuntimeError)): self.project(saved)

    def test_changed_installed_source_rejects(self):
        saved,_=self.saved()
        (self.source/'melos_runner.py').write_bytes((self.source/'melos_runner.py').read_bytes()+b'\n# changed\n')
        with self.assertRaises(ValueError): self.project(saved)

    def test_missing_replaced_input_labels_never_reopen(self):
        saved,_=self.saved()
        shutil.rmtree(self.inputs)
        for replacement in (False,True):
            if replacement:
                self.inputs.mkdir(); (self.inputs/'world.yaml').write_text('untrusted: replacement')
            with self.forbid_input_access(self.inputs):
                self.assertEqual(73,self.record(self.project(saved))['observed_through'])
        self.cap.assert_live()

    def test_fresh_factory_binding_and_roundtrip_are_required(self):
        saved,_=self.saved()
        foreign = self.make.create(0); foreign.stage._captured_factory = object()
        with patch.object(self.make,'create',return_value=foreign):
            with self.assertRaises(ValueError): self.project(saved)
        original = MelosSession.restore_state
        def corrupt(session,state):
            original(session,state); session.step_index+=1
        with patch.object(MelosSession,'restore_state',corrupt):
            with self.assertRaises(ValueError): self.project(saved)

    def test_initial_generation_has_no_fictional_execution_failure(self):
        session = self.make.create(0)
        saved,_=self.saved(state=session.export_state())
        record = self.record(self.project(saved))
        self.assertEqual('paused',record['stop_reason']); self.assertIsNone(record['failure_evidence'])
        self.assertIsNone(record['actual_terminal']); self.assertEqual('unresolved',record['observations']['status'])

    def test_bounds_reject_before_return(self):
        saved,_=self.saved()
        with patch('engine.calendar_native_exports.MAX_EXPORT_BYTES',1):
            with self.assertRaises(ValueError): self.project(saved)


    @unittest.skipUnless(sys.platform == 'linux', 'Native Linux evidence only')
    def test_native_saved_generation_capture_project_publish_and_recover(self):
        from engine.calendar_parent_owner import _ExistingParentOwner
        from engine.calendar_generation_capture import NativeGenerationCapture
        from engine.calendar_native_exports import publish_native_exports
        from engine.calendar_run_store import NativeWriterPort, RunStore
        from execution.melos_generation import run_melos_generation
        output = self.root/'native'; output.mkdir()
        with _ExistingParentOwner(output, backend='linux') as owner:
            for seed in (0, 63):
                session = self.make.create(seed)
                saved = run_melos_generation(session, owner, until=73, stream_id=f'{seed+1:032x}')
                lease = owner._borrow('linux')
                try:
                    capture = NativeGenerationCapture().capture(owner, saved.selection.leaf,
                        max_bytes=saved.selection.expected.size, parent_borrow=lease)
                finally:
                    lease.close()
                projected = project_melos_generation_record(saved.selection,capture.data,self.make,self.binding,seed)
                accepted = publish_native_exports(owner,projected)
                self.assertEqual(projected,accepted.prepared)
                self.assertEqual(saved.layers,projected.ledger)
                for artifact in projected.artifacts():
                    self.assertEqual(artifact.data,(output/artifact.leaf).read_bytes())
                with RunStore(owner,NativeWriterPort(),NativeGenerationCapture(),
                        melos_generation_compatibility(self.make.create(seed)),stream_id=f'{seed+100:032x}') as store:
                    state=store.continue_from(saved.selection)
                self.assertEqual(session.export_state(),state)
                self.assertEqual(0,owner.active_borrows)


    def test_other_seed_catalog_mutation_cannot_emit_inconsistent_admission(self):
        saved,_=self.saved()
        changes = {
            'initial_hash': lambda slots: slots[63].update(initial_hash='0'*64),
            'provider': lambda slots: slots[63]['provider'].update(config_hash='0'*64),
            'delete': lambda slots: slots.pop(63),
            'add': lambda slots: slots.update({1:deepcopy(slots[63])}),
        }
        for name,change in changes.items():
            with self.subTest(change=name):
                slots=deepcopy(self.binding._slots);change(slots)
                with patch.object(self.binding,'_slots',slots):
                    with self.assertRaisesRegex(ValueError,'catalog admission'):
                        self.project(saved)

    def test_detached_admission_seed_plan_provider_and_initial_hash_must_bind(self):
        saved,_=self.saved();valid=self.binding.seed_admission(0)
        for change in ({'seed':True},{'seed':63},{'plan_hash':'0'*64},{'aggregate_provider':{}},
                       {'actual_provider':{}},{'initial_session_hash':'0'*64},{'provider_catalog':{}},
                       {'unexpected':'untrusted'}):
            with self.subTest(change=change):
                invalid=dict(valid,**change)
                with patch.object(self.binding,'seed_admission',return_value=invalid):
                    with self.assertRaises(ValueError):self.project(saved)
