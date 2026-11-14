"""Private captured plan admission; no runs, output borrows or publication."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from engine.calendar_capture import CalendarCapture, open_calendar_capture
from engine.execution_plan import digest
from execution.melos_factory import MelosSessionFactory, approved_catalogs
from execution.melos_plan import MelosPlanBinding
from execution.melos_session import MelosSession
from gapengine.genome import Genome
from gapengine.melos_choice_provider import MelosChoiceProvider

ROOT = Path(__file__).resolve().parents[1]


class CapturedPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.source = self.root/'sources'
        self.inputs = self.root/'melos-input'
        shutil.copytree(ROOT/'execution/approved_melos_sources', self.source)
        shutil.copytree(ROOT/'projects/melos-input', self.inputs)
        self.capture = self.stack.enter_context(open_calendar_capture(self.inputs/'world.yaml', profile='melos'))
        self.factory = self.make_factory(self.capture)

    def make_factory(self, capture):
        catalog = approved_catalogs()
        return MelosSessionFactory(self.source, self.inputs, approved_source_hashes=catalog['sources'],
            approved_input_hashes=catalog['inputs'], genome=Genome.neutral(), calendar_capture=capture)

    def forbid_inputs(self):
        stack = ExitStack()
        for method in ('resolve', 'stat', 'lstat', 'open', 'read_bytes', 'read_text',
                       'exists', 'is_file', 'is_dir', 'is_symlink', 'rglob', 'glob', 'iterdir'):
            original = getattr(Path, method)
            def guard(path, *a, _original=original, **kw):
                if path == self.inputs or self.inputs in path.parents:
                    raise AssertionError('Captured input label accessed: ' + str(path))
                return _original(path, *a, **kw)
            stack.enter_context(patch.object(Path, method, guard))
        return stack

    def test_pristine_seeds_and_fresh_bind_preserve_caller_state(self):
        sessions = {seed: self.factory.create(seed) for seed in (0, 63)}
        before = {seed: session.export_state() for seed, session in sessions.items()}
        binding = MelosPlanBinding(sessions)
        self.assertEqual(binding.plan.to_dict()['predeclared_seeds'], [0, 63])
        for seed, session in sessions.items():
            self.assertIs(binding.bind_session(session, seed), session)
            fresh = self.factory.create(seed)
            self.assertIs(binding.bind_session(fresh, seed), fresh)
            self.assertEqual(before[seed], session.export_state())
        self.assertIsNot(sessions[0].rt.world, sessions[63].rt.world)
        self.assertNotEqual(binding.seed_admission(0)['actual_provider'], binding.seed_admission(63)['actual_provider'])

    def test_removed_input_namespace_is_never_reopened(self):
        sessions = {seed: self.factory.create(seed) for seed in (0, 63)}
        shutil.rmtree(self.inputs)
        with self.forbid_inputs():
            binding = MelosPlanBinding(sessions)
            for seed in sessions:
                self.assertIs(binding.bind_session(sessions[seed], seed), sessions[seed])
                binding.bind_session(self.factory.create(seed), seed)

    def test_replaced_input_namespace_is_never_consulted(self):
        sessions = {seed: self.factory.create(seed) for seed in (0, 63)}
        shutil.rmtree(self.inputs)
        self.inputs.mkdir()
        (self.inputs/'world.yaml').write_text('unapproved: true\n')
        (self.inputs/'scenario.yaml').write_text('unapproved: true\n')
        with self.forbid_inputs():
            binding = MelosPlanBinding(sessions)
            for seed in sessions:
                binding.bind_session(self.factory.create(seed), seed)

    def test_plan_and_detached_manifest_match_legacy_semantics(self):
        legacy = self.make_factory(None)
        captured = MelosPlanBinding({seed: self.factory.create(seed) for seed in (0, 63)})
        old = MelosPlanBinding({seed: legacy.create(seed) for seed in (0, 63)})
        self.assertEqual(captured.manifest(), old.manifest())
        for seed in (0, 63):
            old.bind_session(self.factory.create(seed), seed)
            captured.bind_session(legacy.create(seed), seed)
        manifest = captured.manifest()
        self.assertEqual(digest(manifest['provider_catalog']), captured.plan.to_dict()['provider']['config_hash'])
        manifest['provider_catalog']['0']['initial_hash'] = '0'*64
        receipt = captured.seed_admission(63)
        receipt['actual_provider']['config_hash'] = '0'*64
        self.assertEqual(captured.manifest(), old.manifest())
        self.assertEqual(captured.seed_admission(63), old.seed_admission(63))

    def test_live_semantics_cannot_hide_behind_cached_identity(self):
        binding = MelosPlanBinding({0: self.factory.create(0)})
        mutations = {
            'scenario': lambda s: s.trial.scenario['initial_information']['Melos'].update(invented=True),
            'parameters': lambda s: s.trial.p.update(swim_cost=-1),
            'goals': lambda s: s.trial.goals['Melos'].update(self=100),
            'genome': lambda s: s.trial.genome.category_weight.update(I=.7),
            'contract': lambda s: s.rt.contract['tasks']['return'].update(minutes_per_unit=3),
            'world_zones': lambda s: s.rt.world.zones['court'].update(name='changed'),
            'world_routes': lambda s: s.rt.world.routes.clear(),
            'world_config': lambda s: s.rt.world.companionship.update(threshold=-1),
            'world_extra': lambda s: setattr(s.rt.world, 'invented_configuration', {'x': 1}),
            'initial_subjects': lambda s: s.rt.initial['Melos'].update(invented=True),
            'initial_state': lambda s: s.trial.state.update(wedding=True),
            'runtime_people': lambda s: setattr(s.rt.people['Melos'], 'stamina', 1),
            'runtime_vitality': lambda s: setattr(s.rt.people['Melos'], 'vitality', 'dead'),
            'runtime_rng': lambda s: s.rt.sim.rng.random(),
            'runtime_commands': lambda s: s.rt.commands.append({'id': 'unapproved'}),
            'runtime_windows': lambda s: s.rt.task_windows.update(unapproved=[(0, 1)]),
            'runtime_energy': lambda s: setattr(s.rt, 'energy_rules', {'move_per_minute': 100}),
            'runtime_facility_events': lambda s: s.rt._facility_events.append({'id': 'unapproved'}),
            'registry_catalog': lambda s: setattr(s.rt.registry, 'actors', s.rt.registry.actors | {'Unapproved'}),
            'lifecycle_config': lambda s: setattr(s.rt.lifecycle, 'finish_key', 'start'),
            'simulation_config': lambda s: setattr(s.rt.sim, 'terminal_policy', {'unapproved': True}),
            'provider_rng': lambda s: s.stage.provider._rng.random(),
            'provider_cfg': lambda s: setattr(s.stage.provider, '_config', replace(s.stage.provider.config, focus_actor='King')),
            'seed': lambda s: setattr(s.rt.sim, 'seed', 63),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                session = self.factory.create(0)
                mutate(session)
                with self.assertRaises(ValueError): MelosPlanBinding({0: session})
                with self.assertRaises(ValueError): binding.bind_session(session, 0)

    def test_pre_stage_mutations_and_rehashed_configuration_reject(self):
        mutations = {
            'scenario': lambda t: t.scenario['initial_information']['Melos'].update(invented=True),
            'parameters': lambda t: t.p.update(swim_cost=-1),
            'goals': lambda t: t.goals['Melos'].update(self=100),
            'contract': lambda t: t.rt.contract['tasks']['return'].update(minutes_per_unit=3),
            'world_routes': lambda t: t.rt.world.routes.clear(),
            'world_movement': lambda t: t.rt.world.movement.update(invented=True),
            'world_stamina': lambda t: t.rt.world.stamina.update(invented=True),
            'world_vitality': lambda t: t.rt.world.vitality.update(invented=True),
            'initial': lambda t: t.state.update(wedding=True),
        }
        catalog = approved_catalogs()
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                genome = Genome.neutral()
                trial = self.factory._module.Trial(genome, 0, scenario=self.capture.yaml('scenario'))
                mutate(trial)
                session = MelosSession(trial, input_hashes=catalog['inputs'], source_hashes=catalog['sources'],
                                       choice_provider=MelosChoiceProvider(genome, 0))
                session.stage._captured_factory = self.factory
                with self.assertRaises(ValueError): MelosPlanBinding({0: session})

    def test_runtime_capture_factory_and_provenance_bindings_reject(self):
        other_capture = self.stack.enter_context(open_calendar_capture(self.inputs/'world.yaml', profile='melos'))
        other_factory = self.make_factory(other_capture)
        mutations = {
            'missing_factory': lambda s: delattr(s.stage, '_captured_factory'),
            'wrong_factory': lambda s: setattr(s.stage, '_captured_factory', other_factory),
            'wrong_capture': lambda s: setattr(s.rt, 'calendar_capture', other_capture),
            'unbound_runtime': lambda s: setattr(s.rt, 'calendar_capture', None),
            'unbound_world': lambda s: setattr(s.rt.world, 'calendar_capture', None),
            'unbound_simulation': lambda s: setattr(s.rt.sim, 'calendar_capture', None),
            'trial_simulation': lambda s: setattr(s.trial, 'sim', other_factory.create(0).rt.sim),
            'runtime_subjects': lambda s: setattr(s.rt, 'people', dict(s.rt.people)),
            'runtime_kernel': lambda s: setattr(s.rt.sim, 'time_kernel', None),
            'provenance': lambda s: setattr(s.rt.world, 'calendar_input_provenance', {'invented': True}),
            'subject_provenance': lambda s: setattr(s.rt.world, 'calendar_subject_sources', {'invented': True}),
            'definition': lambda s: s.rt.world.purpose_preparation_definition.update(invented=True),
            'configuration': lambda s: s.rt.world.slot_execution.update(fixed_version=True),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                session = self.factory.create(0)
                mutate(session)
                with self.assertRaises(ValueError): MelosPlanBinding({0: session})

    def test_borrowed_execution_objects_and_callbacks_reject(self):
        other = self.factory.create(0)
        binding = MelosPlanBinding({0: self.factory.create(0)})
        mutations = {
            'lifecycle_pending': lambda s: setattr(s.rt.lifecycle, 'pending', other.rt.lifecycle.pending),
            'verb_world': lambda s: setattr(s.rt.sim.verb_engine, 'world', other.rt.world),
            'verb_rng': lambda s: setattr(s.rt.sim.verb_engine, 'rng', other.rt.sim.rng),
            'world_subject': lambda s: s.rt.world.subjects.update(Melos=other.rt.people['Melos']),
            'relation_target': lambda s: setattr(s.rt.world.relations, '_target_resolver', other.rt.world.relations._target_resolver),
            'relation_affinity': lambda s: setattr(s.rt.world.relations, '_affinity_cap_resolver', other.rt.world.relations._affinity_cap_resolver),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                session = self.factory.create(0)
                mutate(session)
                with self.assertRaises(ValueError): MelosPlanBinding({0: session})
                with self.assertRaises(ValueError): binding.bind_session(session, 0)
        # Do not execute a substituted callable to discover its owner.
        session = self.factory.create(0)
        session.rt.lifecycle.pending = lambda: self.fail('Untrusted callback invoked')
        with self.assertRaises(ValueError): MelosPlanBinding({0: session})

    def test_replaced_factory_same_capture_and_wrong_profile_reject(self):
        session = self.factory.create(0)
        replacement = self.make_factory(self.capture)
        session.stage._captured_factory = replacement
        with self.assertRaises(ValueError): MelosPlanBinding({0: session})
        session = replacement.create(0)
        self.capture.profile = None
        try:
            with self.assertRaises(ValueError): MelosPlanBinding({0: session})
        finally:
            self.capture.profile = 'melos'

    def test_closed_capture_fails_existing_binding_without_input_fallback(self):
        session = self.factory.create(0)
        binding = MelosPlanBinding({0: session})
        self.capture.close()
        with self.forbid_inputs():
            with self.assertRaises(ValueError): MelosPlanBinding({0: session})
            with self.assertRaises(ValueError): binding.bind_session(session, 0)

    def test_dynamic_session_journal_provider_and_cursor_are_not_pristine(self):
        binding = MelosPlanBinding({0: self.factory.create(0)})
        mutations = {
            'session_history': lambda s: s.step_history.append({'step': 1}),
            'session_cursor': lambda s: setattr(s, 'advance_target', 1),
            'stage_cursor': lambda s: setattr(s.stage, 'pending', {'invented': True}),
            'provider_counts': lambda s: s.stage.provider._counts.update(Melos=1),
            'journal_identity': lambda s: s.rt._adapter_journal['identity'].update(config_hash='0'*64),
            'journal_entries': lambda s: s.rt._adapter_journal.update(invented=True),
            'actual_step': lambda s: s.run(until=1),
            'persistence_started': lambda s: setattr(s.rt, '_generation_persistence_started', True),
            'writer': lambda s: setattr(s.rt, 'store', object()),
            'boundary_in_progress': lambda s: setattr(s.rt.time_kernel, '_dispatching', True),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                session = self.factory.create(0)
                mutate(session)
                with self.assertRaises(ValueError): MelosPlanBinding({0: session})
                with self.assertRaises(ValueError): binding.bind_session(session, 0)

    def test_actual_input_bytes_metadata_and_declared_catalog_must_match(self):
        session = self.factory.create(0)
        original = CalendarCapture.bytes
        def changed(capture, role):
            value = original(capture, role)
            return value+b'\n# changed' if role == 'scenario' else value
        with patch.object(CalendarCapture, 'bytes', changed):
            with self.assertRaises(ValueError): MelosPlanBinding({0: session})
        record = self.capture._records['world']
        self.capture._records['world'] = replace(record, sha256='0'*64)
        try:
            with self.assertRaises(ValueError): MelosPlanBinding({0: session})
        finally:
            self.capture._records['world'] = record
        session.stage.identity['inputs']['scenario.yaml'] = '0'*64
        with self.assertRaises(ValueError): MelosPlanBinding({0: session})

    def test_current_source_tampering_and_declared_source_manifest_reject(self):
        session = self.factory.create(0)
        binding = MelosPlanBinding({0: session})
        for name in ('melos_runner.py', 'ninegene_melos.py'):
            with self.subTest(source=name):
                path = self.source/name
                original = path.read_bytes()
                path.write_bytes(original+b'\n# changed\n')
                try:
                    with self.assertRaises(ValueError): MelosPlanBinding({0: session})
                    with self.assertRaises(ValueError): binding.bind_session(session, 0)
                finally:
                    path.write_bytes(original)
        session.stage.identity['sources']['melos_session'] = '0'*64
        with self.assertRaises(ValueError): MelosPlanBinding({0: session})
        session = self.factory.create(0)
        declared = deepcopy(session.stage.identity['sources'])
        declared['ninegene_melos.py'] = '0'*64
        session.stage.identity['sources'] = declared
        with self.assertRaises(ValueError): binding.bind_session(session, 0)

    def test_admission_does_not_borrow_write_run_publish_or_copy_capture(self):
        sessions = {seed: self.factory.create(seed) for seed in (0, 63)}
        with ExitStack() as stack:
            for name in ('execution.melos_session.CheckpointStore',
                         'execution.melos_generation.RunStore',
                         'engine.calendar_run_store.RunStore',
                         'execution.melos_run_record.publish_saved_run_record',
                         'execution.melos_run_record.describe_saved_run_record',
                         'engine.calendar_parent_owner._ExistingParentOwner._borrow',
                         'execution.melos_session.MelosSession.run',
                         'execution.melos_session.MelosSession.run_saved'):
                stack.enter_context(patch(name, side_effect=AssertionError('Admission reached '+name)))
            stack.enter_context(patch.object(CalendarCapture, '__deepcopy__', side_effect=AssertionError('Capture copied')))
            binding = MelosPlanBinding(sessions)
            for seed in sessions:
                binding.bind_session(self.factory.create(seed), seed)
        self.assertFalse((self.root/'unused-output').exists())
        self.assertFalse(any(self.root.glob('*.runrecord')))


if __name__ == '__main__':
    unittest.main()
