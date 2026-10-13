"""Observer facts have a bounded schema and no scenario-specific source guard."""
from copy import deepcopy
from pathlib import Path
import unittest
import shutil
import tempfile
from unittest.mock import patch

from engine.calendar_checkpoint import source_hash
from engine.calendar_observers import validate_observers, validate_observation_state
from engine.calendar_inputs import load_project
from engine.sim import Simulation


class CalendarObserverBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.calendar = {'events': [{'id': 'shift', 'minute': 15}]}
        self.definition = {'id': 'audit', 'event_id': 'shift', 'verb': 'audit_snapshot',
                           'fields': {'present': {'check': 'at', 'actor': 'Ada', 'place': 'Bench'},
                                      'scope': {'literal': 'authored metadata'}}}

    def validate(self, value):
        return validate_observers(value, self.calendar, {'Ada'}, {'Bench'}, {'parts'})

    def test_reserved_emission_names_depth_and_literal_bounds_are_rejected(self):
        variants = []
        for key, value in [('id', 'scheduled:shift'), ('verb', 'action_completed'),
                           ('fields', {'at': {'literal': 1}}),
                           ('fields', {'x': {'literal': float('nan')}}),
                           ('fields', {'x': {'literal': []}})]:
            candidate = deepcopy(self.definition); candidate[key] = value; variants.append(candidate)
        expression = {'literal': True}
        for _ in range(9): expression = {'group': {'child': expression}}
        candidate = deepcopy(self.definition); candidate['fields'] = {'deep': expression}; variants.append(candidate)
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError): self.validate([candidate])

    def test_checkpoint_observation_requires_the_exact_configured_fact_shape_and_row(self):
        value = {'at': 15, 'present': True, 'scope': 'authored metadata'}
        def row(v): return {'id': 'audit', 'absolute_minute': 15, 'verb': 'audit_snapshot',
                            'details': {'caused_by': [], **v}}
        validate_observation_state({'audit': value}, [self.definition], self.calendar, [row(value)], 15)
        for field, replacement in [('present', 1), ('scope', 'invented'), ('at', True)]:
            bad = {**value, field: replacement}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_observation_state({'audit': bad}, [self.definition], self.calendar, [row(bad)], 15)
        with self.assertRaises(ValueError):
            validate_observation_state({}, [self.definition], self.calendar, [row(value)], 15)

    def test_source_guard_tracks_generic_cli_and_excludes_sample_adapter(self):
        baseline = source_hash(); read = Path.read_bytes
        def altered(path):
            data = read(path)
            return data + b'changed adapter' if path.name == 'cultural_festival_v2.py' else data
        with patch.object(Path, 'read_bytes', altered): self.assertEqual(source_hash(), baseline)
        def altered_generic(path):
            data = read(path)
            return data + b'changed execution' if path.name == 'calendar_sim.py' else data
        with patch.object(Path, 'read_bytes', altered_generic): self.assertNotEqual(source_hash(), baseline)

    def test_direct_simulation_rejects_runtime_people_that_differ_from_declared_inputs(self):
        project = Path(__file__).resolve().parents[1]/'projects/calendar_workshop_example/world.yaml'
        for field in ('traits', 'zone', 'knowledge'):
            world, people = load_project(project)
            if field == 'traits': people['Ada'].traits['diligence'] = .1
            elif field == 'zone': people['Ada'].zone = 'Store'
            else: people['Ada'].knowledge.add('invented')
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'declared project inputs'):
                Simulation(0, world, people, Path('/tmp/unused-observer-boundary'))

    def test_runtime_cannot_claim_a_declared_contract_after_in_memory_modification(self):
        project = Path(__file__).resolve().parents[1]/'projects/calendar_workshop_example/world.yaml'
        world, people = load_project(project)
        world.slot_contract['tasks']['pieces']['minutes_per_unit'] = 11
        with self.assertRaisesRegex(ValueError, 'declared project input'):
            Simulation(0, world, people, Path('/tmp/unused-observer-boundary'))

    def test_raw_world_changes_after_loading_are_rejected_before_any_output(self):
        example = Path(__file__).resolve().parents[1]/'projects/calendar_workshop_example'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root/'project'; shutil.copytree(example, project)
            world, people = load_project(project/'world.yaml')
            with (project/'world.yaml').open('a') as stream: stream.write('\n# changed after load\n')
            with self.assertRaisesRegex(ValueError, 'changed after loading'):
                Simulation(0, world, people, root/'out')
            self.assertFalse((root/'out').exists())

    def test_container_in_an_actor_identifier_is_an_explicit_configuration_error(self):
        project = Path(__file__).resolve().parents[1]/'projects/calendar_workshop_example/world.yaml'
        world, people = load_project(project)
        runtime = Simulation(0, world, people, Path('/tmp/unused-observer-boundary')).slot_runtime
        before = runtime.export_state()
        for actor in ({}, [], True):
            with self.subTest(actor=actor), self.assertRaises(ValueError):
                runtime.configure([{'id': 'typed', 'actor': actor, 'at': runtime.now, 'verb': 'wait', 'minutes': 1}])
            self.assertEqual(runtime.export_state(), before)
