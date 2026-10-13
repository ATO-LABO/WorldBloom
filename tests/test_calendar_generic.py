"""Independent worlds exercise generic minute execution through its public APIs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from engine.calendar_inputs import freeze_project, load_project
from engine.calendar_v2 import minute_for
from engine.promises_v2 import PromiseRegistry
from engine.sim import Simulation


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'projects/calendar_workshop_example'


def yaml_read(path):
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def yaml_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding='utf-8')


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


class CalendarGenericTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wb-calendar-generic-')
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.project = self.root / 'workshop'
        shutil.copytree(EXAMPLE, self.project)
        self.world_path = self.project / 'world.yaml'
        self.commands = json.loads((self.project / 'choices.json').read_text())

    def simulation(self, name, *, world=None, commands=None):
        definition, people = load_project(self.world_path if world is None else world)
        simulation = Simulation(37, definition, people, self.root / name)
        simulation.slot_runtime.configure(self.commands if commands is None else commands)
        return simulation

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/calendar_sim.py'),
                               *map(str, args)], cwd=self.root,
                              capture_output=True, text=True, encoding='utf-8', timeout=30)

    def summary(self, out):
        return json.loads((out / 'summary.json').read_text())

    def checkpoint(self, out):
        return json.loads((out / 'calendar-state.json').read_text())['payload']['state']

    def add_observer(self):
        contract = yaml_read(self.project / 'contract.yaml')
        contract['observers'] = [{
            'id': 'shift-audit', 'event_id': 'new-shift', 'verb': 'workshop_audit',
            'fields': {'maker_present': {'check': 'at', 'actor': 'Ada', 'place': 'Bench'},
                       'production': {'group': {
                           'complete': {'check': 'task_complete', 'task_id': 'pieces'},
                           'scope': {'literal': 'night workshop'}}}}}]
        yaml_write(self.project / 'contract.yaml', contract)

    def test_example_loads_two_declared_ascii_people_without_festival_dependencies(self):
        before = files(self.project)
        world, people = load_project(self.world_path)
        self.assertEqual(set(people), {'Ada', 'Bo'})
        self.assertEqual(set(world.zones), {'Bench', 'Store'})
        self.assertEqual(world.slot_contract['version'], 3)
        self.assertNotIn('play', world.slot_contract)
        self.assertFalse(world.slot_contract.get('observers', []))
        self.assertEqual(people['Ada'].zone, 'Bench')
        self.assertEqual(people['Ada'].traits['diligence'], 0.7)
        self.assertEqual(people['Bo'].initial_relations['Ada']['affinity'], 0.3)
        self.assertEqual(before, files(self.project))
        text = '\n'.join(blob.decode('utf-8') for blob in before.values())
        for name in ('cultural_festival', '蒼', '体育館'):
            self.assertNotIn(name, text)

    def test_cli_executes_quantity_and_handoff_without_any_observers_or_play(self):
        out = self.root / 'cli-run'
        result = self.cli('--world', self.world_path, '--choices', self.project / 'choices.json',
                          '--out', out)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self.summary(out)
        self.assertTrue(summary['done'])
        self.assertEqual(summary['tasks']['pieces']['units'], 3)
        self.assertEqual(summary['tasks']['handoff']['units'], 1)
        self.assertEqual(summary['materials'], {'blank': 0})
        self.assertEqual(summary['observations'], {})
        self.assertNotIn('opening', summary)
        self.assertEqual(summary['api_calls'], 0)
        self.assertFalse(summary['gpu'])
        rows = [json.loads(line) for line in (out / 'layers.jsonl').read_text().splitlines()]
        self.assertFalse([r for r in rows[1:] if r['result'] in ('rejected', 'failed')])
        self.assertFalse([r for r in rows if r.get('verb') == 'play_opening_observation'])
        units = [r for r in rows if r.get('verb') == 'work_unit_completed']
        self.assertEqual([r['absolute_minute'] for r in units], [1435, 1445, 1455])
        delivered = [r for r in rows if r.get('verb') == 'action_completed'
                     and r['details'].get('delivered_to') == 'Bo']
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]['absolute_minute'], 1462)
        self.assertEqual(delivered[0]['details']['task_id'], 'handoff')
        state = self.checkpoint(out)
        self.assertEqual(state['promises']['promises']['piece-order']['versions']['1']['status'],
                         'fulfilled')

    def test_work_moves_in_real_minutes_outside_display_windows_and_across_midnight(self):
        sim = self.simulation('minutes', commands=[
            {'id': 'walk', 'actor': 'Bo', 'at': 1270, 'verb': 'move', 'to': 'Store'},
            {'id': 'return', 'actor': 'Bo', 'at': 1280, 'verb': 'move', 'to': 'Bench'},
            *self.commands])
        sim.slot_runtime.run()
        rows = sim.slot_runtime.rows
        walk = next(r for r in rows if r['id'] == 'complete:walk')
        self.assertEqual(walk['absolute_minute'], 1280)
        self.assertEqual(walk['details']['duration_minutes'], 10)
        notice = next(r for r in rows if r['id'] == 'scheduled:new-shift')
        self.assertEqual(notice['absolute_minute'], 1441)
        self.assertEqual(sim.slot_runtime.registry.get('piece-order')['status'], 'fulfilled')
        self.assertEqual(sim.slot_runtime.ledger['tasks']['pieces']['units'], 3)
        self.assertEqual(minute_for(sim.slot_runtime.calendar, '2026-11-02', '00:02'), 1442)

    def test_split_resume_mid_work_preserves_layers_ledger_relations_rng_and_noop_bytes(self):
        self.add_observer()
        before_inputs = files(self.project)
        continuous = self.simulation('continuous'); continuous.slot_runtime.run()
        split = self.simulation('split'); split.slot_runtime.run(until=1442)
        self.assertFalse(split.slot_runtime.done)
        self.assertEqual(split.slot_runtime.ledger['tasks']['pieces']['effort_minutes'], 17)
        self.assertEqual(split.slot_runtime.ledger['tasks']['pieces']['units'], 1)
        self.assertIn('Ada', split.slot_runtime.busy)
        resumed = self.simulation('split'); resumed.slot_runtime.run(resume=True)
        self.assertEqual(files(continuous.out_dir), files(resumed.out_dir))
        self.assertEqual(continuous.slot_runtime.export_state(), resumed.slot_runtime.export_state())
        self.assertEqual(continuous.world.relations.snapshot(), resumed.world.relations.snapshot())
        self.assertEqual(continuous.rng.getstate(), resumed.rng.getstate())
        before_outputs = files(resumed.out_dir)
        self.simulation('split').slot_runtime.run(resume=True)
        self.assertEqual(before_outputs, files(resumed.out_dir))
        self.assertEqual(before_inputs, files(self.project))

    def test_generic_registered_event_observer_takes_snapshot_of_actual_mid_work_state(self):
        self.add_observer()
        sim = self.simulation('observer'); sim.slot_runtime.run()
        observations = self.summary(sim.out_dir)['observations']
        self.assertEqual(observations, {'shift-audit': {
            'at': 1441, 'maker_present': True,
            'production': {'complete': False, 'scope': 'night workshop'}}})
        row = [r for r in sim.slot_runtime.rows if r['verb'] == 'workshop_audit']
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]['absolute_minute'], 1441)
        self.assertEqual(sim.slot_runtime.ledger['tasks']['pieces']['units'], 3)

    def test_delivery_by_an_actor_without_the_task_role_is_rejected_before_handoff(self):
        sim = self.simulation('wrong-delivery-role', commands=[
            deepcopy(self.commands[-2]),
            {'id': 'wrong-handoff', 'actor': 'Bo', 'at': 1460,
             'verb': 'deliver', 'task_id': 'handoff', 'target': 'Ada'}])
        sim.slot_runtime.run()
        rejected = [r for r in sim.slot_runtime.rows if r['id'] == 'reject:wrong-handoff']
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]['details']['reason'], 'task_place_or_role')
        self.assertEqual(sim.slot_runtime.ledger['tasks']['pieces']['units'], 3)
        self.assertEqual(sim.slot_runtime.ledger['tasks']['handoff']['units'], 0)
        self.assertFalse([r for r in sim.slot_runtime.rows if r['id'] == 'start:wrong-handoff'])
        self.assertFalse([e for e in sim.slot_runtime.ledger['completion_events'] if e['task_id'] == 'handoff'])

    def test_five_minute_delivery_uses_configured_duration_and_resumes_mid_handoff_exactly(self):
        contract = yaml_read(self.project / 'contract.yaml')
        contract['tasks']['handoff']['minutes_per_unit'] = 5
        yaml_write(self.project / 'contract.yaml', contract)
        self.commands[0]['terms']['deadline'] = 1465
        terms_hash = PromiseRegistry({'Ada', 'Bo'}, {'pieces', 'handoff'},
                                     {'Bench', 'Store'}).terms_hash(self.commands[0]['terms'])
        for command in self.commands:
            if command['verb'] == 'accept': command['terms_hash'] = terms_hash
        continuous = self.simulation('five-continuous'); continuous.slot_runtime.run()
        start = next(r for r in continuous.slot_runtime.rows if r['id'] == 'start:give-pieces')
        complete = next(r for r in continuous.slot_runtime.rows if r['id'] == 'complete:give-pieces')
        self.assertEqual(start['details']['duration_minutes'], 5)
        self.assertEqual(complete['details']['duration_minutes'], 5)
        self.assertEqual(complete['absolute_minute'], 1465)
        self.assertEqual(continuous.slot_runtime.registry.get('piece-order')['status'], 'fulfilled')
        split = self.simulation('five-split'); split.slot_runtime.run(until=1463)
        self.assertEqual(split.slot_runtime.busy['Ada']['verb'], 'deliver')
        self.assertEqual(split.slot_runtime.busy['Ada']['finish'], 1465)
        self.assertEqual(split.slot_runtime.ledger['tasks']['handoff']['units'], 0)
        resumed = self.simulation('five-split'); resumed.slot_runtime.run(resume=True)
        self.assertEqual(files(continuous.out_dir), files(resumed.out_dir))
        self.assertEqual(continuous.slot_runtime.export_state(), resumed.slot_runtime.export_state())
        self.assertEqual(resumed.slot_runtime.ledger['tasks']['handoff']['effort_minutes'], 5)

    def test_material_consuming_delivery_is_explicitly_unsupported_before_execution(self):
        contract = yaml_read(self.project / 'contract.yaml')
        contract['tasks']['handoff']['materials_per_unit'] = {'blank': 1}
        yaml_write(self.project / 'contract.yaml', contract)
        before = files(self.project)
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            load_project(self.world_path)
        self.assertEqual(before, files(self.project))

    def test_three_people_different_ids_and_places_use_same_generic_runtime(self):
        mapping = {'Ada': 'Nia', 'Bo': 'Omar', 'Bench': 'Kiln', 'Store': 'Depot'}
        def rename(value):
            if isinstance(value, dict):
                return {mapping.get(k, k): rename(v) for k, v in value.items()}
            if isinstance(value, list):
                return [rename(v) for v in value]
            return mapping.get(value, value) if isinstance(value, str) else value
        world = rename(yaml_read(self.world_path)); world['name'] = 'River ceramics studio'
        world['slot_execution']['subjects'].append({'id': 'Paz', 'path': 'subjects/paz.yaml'})
        world['zones'].append({'name': 'Gallery'})
        world['routes']['Kiln'].append({'to': 'Gallery', 'cost': 1})
        world['routes']['Gallery'] = [{'to': 'Kiln', 'cost': 1}]
        yaml_write(self.world_path, world)
        for filename in ('ada.yaml', 'bo.yaml'):
            p = self.project / 'subjects' / filename; yaml_write(p, rename(yaml_read(p)))
        third = deepcopy(yaml_read(self.project / 'subjects/bo.yaml'))
        third.update(id='Paz', relations={})
        third['range'] = {'zones': ['Kiln', 'Depot', 'Gallery'], 'entry': 'Kiln'}
        yaml_write(self.project / 'subjects/paz.yaml', third)
        for filename in ('ada.yaml', 'bo.yaml'):
            p = self.project / 'subjects' / filename; v = yaml_read(p)
            v['range']['zones'].append('Gallery'); yaml_write(p, v)
        contract = rename(yaml_read(self.project / 'contract.yaml'))
        contract['roles']['Paz'] = ['auditor']; contract['initial_task_knowledge']['Paz'] = []
        contract['travel_minutes']['Kiln']['Gallery'] = 10
        contract['travel_minutes']['Gallery'] = {'Kiln': 10}
        contract['observers'] = [{'id': 'studio-audit', 'event_id': 'new-shift', 'verb': 'studio_audit',
                                  'fields': {'auditor_at_gallery': {'check': 'at', 'actor': 'Paz', 'place': 'Gallery'}}}]
        yaml_write(self.project / 'contract.yaml', contract)
        commands = rename(self.commands)
        terms_hash = PromiseRegistry(set(contract['roles']), set(contract['tasks']),
                                     {'Kiln', 'Depot', 'Gallery'}).terms_hash(commands[0]['terms'])
        for command in commands:
            if command['verb'] == 'accept': command['terms_hash'] = terms_hash
        commands.append({'id': 'auditor-visit', 'actor': 'Paz', 'at': 1300,
                         'verb': 'move', 'to': 'Gallery'})
        sim = self.simulation('three-people', commands=commands); sim.slot_runtime.run()
        self.assertEqual(set(sim.subjects), {'Nia', 'Omar', 'Paz'})
        self.assertEqual(sim.subjects['Paz'].zone, 'Gallery')
        self.assertEqual(sim.slot_runtime.registry.get('piece-order')['status'], 'fulfilled')
        self.assertEqual(self.summary(sim.out_dir)['observations']['studio-audit'],
                         {'at': 1441, 'auditor_at_gallery': True})

    def test_resume_rejects_changed_world_contract_person_and_commands_without_output_writes(self):
        for name in ('world.yaml', 'contract.yaml', 'subjects/ada.yaml', 'subjects/bo.yaml', 'choices'):
            with self.subTest(name=name):
                sim = self.simulation('guard-'+name.replace('/', '-'))
                sim.slot_runtime.run(until=1442); before = files(sim.out_dir)
                commands = deepcopy(self.commands)
                if name == 'choices': commands[-2]['minutes'] = 29
                else:
                    target = self.project / name; original = target.read_bytes()
                    target.write_bytes(original + b'\n# independently changed input\n')
                try:
                    with self.assertRaises(ValueError):
                        self.simulation(sim.out_dir.name, commands=commands).slot_runtime.run(resume=True)
                    self.assertEqual(before, files(sim.out_dir))
                finally:
                    if name != 'choices': target.write_bytes(original)

    def test_freeze_uses_exact_declared_files_and_a_new_execution_version(self):
        (self.project / 'unreferenced.txt').write_text('not a declared input')
        before = files(self.project)
        destination = self.root / 'frozen'
        version = freeze_project(self.world_path, destination)
        self.assertEqual(version['schema_version'], 3)
        self.assertEqual(set(version['inputs']), {'world.yaml', 'contract.yaml', 'subjects/ada.yaml', 'subjects/bo.yaml'})
        self.assertNotIn('original_subject_files', version)
        for name, record in version['inputs'].items():
            actual = (destination / name).read_bytes()
            self.assertEqual(record, {'size_bytes': len(actual), 'sha256': hashlib.sha256(actual).hexdigest()})
        self.assertEqual(before, files(self.project))
        frozen_world = destination / version['world']
        world, people = load_project(frozen_world)
        self.assertTrue(world.slot_execution['fixed_version'])
        self.assertEqual(set(people), {'Ada', 'Bo'})
        sim = self.simulation('fixed-run', world=frozen_world); sim.slot_runtime.run()
        self.assertTrue(sim.slot_runtime.done)
        with self.assertRaises(ValueError): freeze_project(self.world_path, destination)

    def test_arbitrary_world_name_nested_contract_and_person_paths_are_preserved_in_freeze(self):
        world = yaml_read(self.world_path)
        (self.project / 'settings').mkdir()
        (self.project / 'contract.yaml').rename(self.project / 'settings/minutes.yml')
        (self.project / 'subjects').rename(self.project / 'people')
        world['slot_execution']['contract'] = 'settings/minutes.yml'
        for ref in world['slot_execution']['subjects']:
            ref['path'] = ref['path'].replace('subjects/', 'people/')
        self.world_path.unlink()
        self.world_path = self.project / 'atelier-definition.yml'
        yaml_write(self.world_path, world)
        before = files(self.project)
        destination = self.root / 'nested-fixed'
        version = freeze_project(self.world_path, destination)
        self.assertEqual(version['world'], 'atelier-definition.yml')
        self.assertEqual(set(version['inputs']), {'atelier-definition.yml', 'settings/minutes.yml',
                                                 'people/ada.yaml', 'people/bo.yaml'})
        self.assertEqual(before, files(self.project))
        sim = self.simulation('nested-run', world=destination / version['world'])
        sim.slot_runtime.run()
        self.assertEqual(sim.slot_runtime.registry.get('piece-order')['status'], 'fulfilled')

    def test_freeze_protects_existing_empty_or_nonempty_destinations_and_source_files(self):
        before = files(self.project)
        for nonempty in (False, True):
            with self.subTest(nonempty=nonempty):
                destination = self.root / ('existing-'+str(nonempty)); destination.mkdir()
                if nonempty: (destination / 'keep.txt').write_text('preserve existing data')
                before_destination = files(destination)
                with self.assertRaises(ValueError): freeze_project(self.world_path, destination)
                self.assertEqual(before_destination, files(destination))
                self.assertEqual(before, files(self.project))

    def test_frozen_project_rejects_added_file_or_empty_directory_without_cleanup(self):
        for kind in ('file', 'directory'):
            with self.subTest(kind=kind):
                destination = self.root / ('added-'+kind)
                version = freeze_project(self.world_path, destination)
                extra = destination / 'undeclared'
                if kind == 'file': extra.write_text('must remain available after refusal')
                else: extra.mkdir()
                before = files(destination)
                with self.assertRaises(ValueError): load_project(destination / version['world'])
                self.assertEqual(before, files(destination))
                self.assertTrue(extra.exists())

    def test_fixed_version_cannot_be_downgraded_by_a_resigned_schema_or_manifest(self):
        from engine.calendar_checkpoint import digest
        for kind in ('old-schema', 'bool-schema', 'omit-person', 'outside-input'):
            with self.subTest(kind=kind):
                destination = self.root / ('version-'+kind)
                version = freeze_project(self.world_path, destination)
                if kind == 'old-schema': version['schema_version'] = 2
                elif kind == 'bool-schema': version['schema_version'] = True
                elif kind == 'omit-person': del version['inputs']['subjects/ada.yaml']
                else: version['inputs']['../outside.yaml'] = version['inputs']['subjects/ada.yaml']
                version['id'] = digest({k: v for k, v in version.items() if k != 'id'})
                (destination / 'execution-version.json').write_text(json.dumps(version), encoding='utf-8')
                before = files(destination)
                with self.assertRaises(ValueError): load_project(destination / version['world'])
                self.assertEqual(before, files(destination))

    def test_frozen_input_mutation_and_missing_version_are_rejected_before_run(self):
        for name in ('world.yaml', 'contract.yaml', 'subjects/ada.yaml', 'subjects/bo.yaml', 'execution-version.json'):
            with self.subTest(name=name):
                destination = self.root / ('fixed-'+name.replace('/', '-'))
                version = freeze_project(self.world_path, destination)
                target = destination / name
                if name == 'execution-version.json': target.unlink()
                else: target.write_bytes(target.read_bytes() + b'\n# tampered\n')
                before = files(destination)
                with self.assertRaises(ValueError): load_project(destination / version['world'])
                self.assertEqual(before, files(destination))

    def test_cli_freeze_is_save_only_then_requires_an_explicit_run(self):
        destination = self.root / 'cli-fixed'
        result = self.cli('--world', self.world_path, '--freeze-to', destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list(destination.rglob('calendar-state.json')))
        self.assertFalse(list(destination.rglob('layers.jsonl')))
        version = json.loads((destination / 'execution-version.json').read_text())
        out = self.root / 'fixed-cli-run'
        result = self.cli('--world', destination / version['world'], '--choices', self.project / 'choices.json', '--out', out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.summary(out)['done'])

    def test_invalid_relative_references_ids_and_subject_paths_are_explicitly_rejected(self):
        original = self.world_path.read_bytes()
        cases = [
            ('contract', '/absolute.yaml'), ('contract', '../contract.yaml'),
            ('subject-path', '../subjects/ada.yaml'), ('subject-path', 'subjects\\ada.yaml'),
            ('subject-path', '/absolute.yaml'), ('subject-path', 'subjects/./ada.yaml'),
            ('subject-id', 'Imposter'), ('duplicate-id', 'Ada'), ('duplicate-path', 'subjects/ada.yaml')]
        for kind, value in cases:
            with self.subTest(kind=kind, value=value):
                world = yaml.safe_load(original)
                if kind == 'contract': world['slot_execution']['contract'] = value
                elif kind == 'subject-path': world['slot_execution']['subjects'][0]['path'] = value
                elif kind == 'subject-id': world['slot_execution']['subjects'][0]['id'] = value
                elif kind == 'duplicate-id': world['slot_execution']['subjects'][1]['id'] = value
                else: world['slot_execution']['subjects'][1]['path'] = value
                yaml_write(self.world_path, world)
                with self.assertRaises(ValueError): load_project(self.world_path)
        self.world_path.write_bytes(original)

    def test_symlinked_subject_even_with_identical_bytes_is_rejected(self):
        path = self.project / 'subjects/ada.yaml'
        target = self.root / 'external-ada.yaml'; target.write_bytes(path.read_bytes()); path.unlink()
        try: path.symlink_to(target)
        except OSError as error: self.skipTest('host cannot create symlinks: '+str(error))
        with self.assertRaises(ValueError): load_project(self.world_path)

    def test_unknown_actor_and_invalid_role_catalog_do_not_silently_reduce_the_people(self):
        original = (self.project / 'contract.yaml').read_bytes()
        for change in ('unknown-actor', 'missing-actor', 'string-roles', 'unknown-place'):
            with self.subTest(change=change):
                contract = yaml.safe_load(original)
                if change == 'unknown-actor': contract['roles']['Ghost'] = ['maker']
                elif change == 'missing-actor': del contract['roles']['Bo']
                elif change == 'string-roles': contract['roles']['Ada'] = 'maker'
                else: contract['tasks']['pieces']['place'] = 'Missing'
                yaml_write(self.project / 'contract.yaml', contract)
                with self.assertRaises(ValueError): self.simulation('invalid')

    def test_existing_item_goal_and_destination_zone_are_preserved_without_actor_reinterpretation(self):
        world = yaml_read(self.world_path)
        world['items'] = [{'name': 'badge'}]
        yaml_write(self.world_path, world)
        path = self.project / 'subjects/ada.yaml'; ada = yaml_read(path)
        ada['goal'] = {'target': 'badge', 'deliver_to': 'Store', 'outcome': 'observation_finished'}
        ada['inventory'] = {'badge': 1}
        yaml_write(path, ada)
        before = files(self.project)
        sim = self.simulation('item-goal', commands=[]); sim.slot_runtime.run()
        self.assertEqual(sim.subjects['Ada'].goal.target, 'badge')
        self.assertEqual(sim.subjects['Ada'].goal.deliver_to, 'Store')
        self.assertEqual(sim.subjects['Ada'].inventory, {'badge': 1})
        self.assertEqual(before, files(self.project))
        ada['goal']['target'] = 'Bo'; yaml_write(path, ada)
        with self.assertRaises(ValueError): load_project(self.world_path)

    def test_bad_quantity_bool_version_and_legacy_play_are_rejected(self):
        original = (self.project / 'contract.yaml').read_bytes()
        for field, value in [('quantity', True), ('quantity', 0), ('quantity', 1.5),
                             ('version', True), ('version', 3.0), ('legacy-play', {})]:
            with self.subTest(field=field, value=value):
                contract = yaml.safe_load(original)
                if field == 'quantity': contract['tasks']['pieces']['units'] = value
                elif field == 'version': contract['version'] = value
                else: contract['play'] = value
                yaml_write(self.project / 'contract.yaml', contract)
                with self.assertRaises(ValueError): load_project(self.world_path)

    def test_observers_reject_unknown_events_actors_tasks_and_unsupported_expressions(self):
        self.add_observer(); original = (self.project / 'contract.yaml').read_bytes()
        for bad in ('event', 'actor', 'task', 'expression', 'duplicate-id'):
            with self.subTest(bad=bad):
                contract = yaml.safe_load(original); observer = contract['observers'][0]
                if bad == 'event': observer['event_id'] = 'missing-notice'
                elif bad == 'actor': observer['fields']['maker_present']['actor'] = 'Ghost'
                elif bad == 'task': observer['fields']['production']['group']['complete']['task_id'] = 'Ghost'
                elif bad == 'expression': observer['fields']['maker_present'] = {'check': 'execute_python', 'source': '1'}
                else: contract['observers'].append(deepcopy(observer))
                yaml_write(self.project / 'contract.yaml', contract)
                with self.assertRaises(ValueError): load_project(self.world_path)

    def test_invalid_cli_input_and_backward_resume_preserve_existing_data(self):
        out = self.root / 'cli-guard'
        result = self.cli('--world', self.world_path, '--choices', self.project / 'choices.json',
                          '--out', out, '--until-minute', 1442)
        self.assertEqual(result.returncode, 0, result.stderr); before = files(out)
        result = self.cli('--world', self.world_path, '--choices', self.project / 'choices.json',
                          '--out', out, '--resume', '--until-minute', 1441)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, files(out))
        bad = self.root / 'duplicate-choices.json'; bad.write_text('[{"id":"first","id":"second"}]')
        result = self.cli('--world', self.world_path, '--choices', bad, '--out', out, '--resume')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(before, files(out))

    def test_cli_rejects_output_in_input_project_before_creating_files_or_directories(self):
        destination = self.root / 'guard-fixed'
        version = freeze_project(self.world_path, destination)
        for world in (self.world_path, destination / version['world']):
            for nested in (False, True):
                with self.subTest(world=world.name, fixed=world.parent == destination, nested=nested):
                    before = files(world.parent)
                    dirs_before = {p.relative_to(world.parent).as_posix() for p in world.parent.rglob('*') if p.is_dir()}
                    out = world.parent / 'new-run' if nested else world.parent
                    result = self.cli('--world', world, '--choices', self.project / 'choices.json', '--out', out)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertEqual(before, files(world.parent))
                    self.assertEqual(dirs_before, {p.relative_to(world.parent).as_posix() for p in world.parent.rglob('*') if p.is_dir()})


if __name__ == '__main__':
    unittest.main()
