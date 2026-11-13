"""Legacy exports distinguish calendar observer metadata from logical terminals."""
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

import yaml
from engine.calendar_inputs import load_project
from engine.execution_plan import digest
from engine.run_record import _saved_projection, commit_run_record, describe_run
from engine.sim import Simulation
from scripts import calendar_sim

ROOT = Path(__file__).resolve().parents[1]
LABELS = ('ending', 'aborted', 'time_limit', 'observation_finished', 'target_observed')


class LegacyCalendarRunRecordTests(unittest.TestCase):
    def project(self, root, label='ending'):
        project = root/'inputs'
        shutil.copytree(ROOT/'projects/calendar_workshop_example', project)
        path = project/'contract.yaml'
        value = yaml.safe_load(path.read_text(encoding='utf-8'))
        value['observers'] = [{'id': 'ordinary-audit', 'event_id': 'new-shift', 'verb': label,
            'fields': {'completed': {'check': 'task_complete', 'task_id': 'pieces'}}}]
        path.write_text(yaml.safe_dump(value), encoding='utf-8')
        return project

    def cli(self, project, out, *options):
        argv = ['calendar_sim', '--world', str(project/'world.yaml'),
                '--choices', str(project/'choices.json'), '--out', str(out), *options]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', argv), redirect_stdout(stdout), redirect_stderr(stderr):
            code = calendar_sim.main()
        self.assertEqual(code, 0, stderr.getvalue())
        return json.loads(stdout.getvalue())

    def read(self, out):
        return describe_run(SimpleNamespace(out_dir=out))

    def assert_saved(self, out, label, done):
        record = self.read(out)
        summary = json.loads((out/'summary.json').read_text())
        rows = [json.loads(line) for line in (out/'layers.jsonl').read_text().splitlines()]
        self.assertIs(summary['done'], done)
        self.assertIsNone(record['actual_terminal'])
        self.assertEqual(record['observed_targets'], [])
        self.assertEqual(record['stop_reason'], 'observation_limit' if done else 'paused')
        self.assertEqual(record['observations'], summary['observations'])
        self.assertIs(record['observations']['ordinary-audit']['completed'], False)
        audit = [r for r in rows if r.get('id') == 'ordinary-audit']
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['verb'], label)
        self.assertEqual(record['observed_through'], summary['now'])
        revision = record.pop('saved_revision_hash')
        self.assertEqual(revision, digest(record))
        record['saved_revision_hash'] = revision
        self.assertEqual(record, json.loads((out/'run-record.json').read_text()))
        return record

    def test_default_cli_all_labels_paused_completed_resumed_and_noop(self):
        cases = []
        for label in LABELS:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); project = self.project(root, label)
                split, full = root/'split', root/'full'
                self.cli(project, split, '--until-minute', '1442')
                paused = self.assert_saved(split, label, False)
                self.cli(project, full)
                completed = self.assert_saved(full, label, True)
                self.cli(project, split, '--resume')
                resumed = self.assert_saved(split, label, True)
                before = {p.name: p.read_bytes() for p in split.iterdir() if p.is_file()}
                self.cli(project, split, '--resume')
                noop = self.assert_saved(split, label, True)
                self.assertEqual(before, {p.name: p.read_bytes() for p in split.iterdir() if p.is_file()})
                self.assertEqual(completed, resumed)
                self.assertEqual(resumed, noop)
                self.assertEqual((full/'layers.jsonl').read_bytes(), (split/'layers.jsonl').read_bytes())
                for name, record in [('paused', paused), ('completed', completed), ('resumed', resumed), ('noop', noop)]:
                    cases.append({'label': label, 'phase': name, 'record': record})
        if os.environ.get('WORLDBLOOM_RECORD_CASES'):
            Path(os.environ['WORLDBLOOM_RECORD_CASES']).write_text(json.dumps(cases, indent=2))

    def saved(self, root, complete=False):
        project = self.project(root)
        out = root/'output'
        self.cli(project, out, *([] if complete else ['--until-minute', '1442']))
        return project, out

    def rewrite(self, out, filename, edit, rebind=True):
        path = out/filename
        if filename == 'layers.jsonl':
            value = [json.loads(line) for line in path.read_text().splitlines()]
            edit(value)
            path.write_text(''.join(json.dumps(row)+'\n' for row in value))
        else:
            value = json.loads(path.read_text()); edit(value)
            if filename == 'calendar-state.json': value['sha256'] = digest(value['payload'])
            path.write_text(json.dumps(value))
        if rebind and filename != 'run-record.json':
            record = json.loads((out/'run-record.json').read_text())
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if filename == 'layers.jsonl': record['ledger']['sha256'] = sha
            else: record[{'summary.json':'summary_sha256', 'calendar-state.json':'checkpoint_sha256'}[filename]] = sha
            record.pop('saved_revision_hash'); record['saved_revision_hash'] = digest(record)
            (out/'run-record.json').write_text(json.dumps(record))

    def test_old_false_calendar_descriptors_reject_even_with_valid_hash(self):
        for label in LABELS:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); project = self.project(root, label); out = root/'output'
                self.cli(project, out, '--until-minute', '1442')
                record = self.read(out)
                false = {'verb':label,'line':2,'event_id':'ordinary-audit','details':{}}
                if label == 'target_observed': record['observed_targets'] = [false]
                else: record.update(actual_terminal=false, stop_reason=label)
                record.pop('saved_revision_hash'); record['saved_revision_hash'] = digest(record)
                path=out/'run-record.json'; path.write_text(json.dumps(record)); before=path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'semantics differ'): self.read(out)
                self.assertEqual(before, path.read_bytes())
                path.unlink()
                fallback = self.read(out)
                self.assertIsNone(fallback['actual_terminal']); self.assertEqual(fallback['observed_targets'], [])
                self.assertEqual(fallback['stop_reason'], 'paused')

    def test_descriptorless_complete_and_checkpointless_exports_have_explicit_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, out = self.saved(Path(tmp), complete=True)
            (out/'run-record.json').unlink()
            fallback = self.read(out)
            self.assertEqual(fallback['stop_reason'], 'observation_limit')
            self.assertEqual(fallback['descriptor_scope'], 'legacy_saved_output_only')
            (out/'calendar-state.json').unlink()
            self.assertEqual(self.read(out)['stop_reason'], 'unknown')
            (out/'summary.json').unlink()
            last = self.read(out)
            self.assertEqual(last['stop_reason'], 'unknown')
            self.assertIsNone(last['actual_terminal']); self.assertEqual(last['observed_targets'], [])

    def test_unchanged_correct_saved_descriptor_is_not_rehashed_or_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, out = self.saved(Path(tmp))
            before = {p.name:p.read_bytes() for p in out.iterdir() if p.is_file()}
            record = self.read(out)
            record['observations'].clear()
            self.assertEqual(before, {p.name:p.read_bytes() for p in out.iterdir() if p.is_file()})
            self.assertTrue(self.read(out)['observations'])

    def test_rehashed_malformed_or_contradictory_summaries_reject(self):
        mutations = [lambda s:s.update(done='false'), lambda s:s.update(done=1),
            lambda s:s.update(now=True), lambda s:s.update(done=True),
            lambda s:s.update(now=1440), lambda s:s.update(observations={}),
            lambda s:s.update(contract='future_calendar')]
        for index, edit in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                _, out = self.saved(Path(tmp)); self.rewrite(out, 'summary.json', edit)
                with self.assertRaises(ValueError): self.read(out)

    def test_rehashed_unknown_or_stripped_calendar_headers_reject(self):
        mutations = [lambda h:h.update(schema_version=99), lambda h:h.update(schema_version=True),
            lambda h:h.update(contract='future'), lambda h:h.pop('contract'),
            lambda h:h.update(calendar={'schema_version':2}),
            lambda h:h.update(calendar={**h['calendar'], 'duration_minutes':1})]
        for index, edit in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                _, out = self.saved(Path(tmp)); self.rewrite(out,'layers.jsonl',lambda rows:edit(rows[0]))
                with self.assertRaises(ValueError): self.read(out)

    def test_rehashed_checkpoint_conflicts_and_unknown_schema_reject(self):
        mutations = [lambda p:p['state'].update(done=True), lambda p:p['state'].update(end=1),
            lambda p:p['compatibility'].update(schema_version=99),
            lambda p:p['state'].update(observations={})]
        for index, edit in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                _, out = self.saved(Path(tmp)); self.rewrite(out,'calendar-state.json',lambda v:edit(v['payload']))
                with self.assertRaises(ValueError): self.read(out)

    def test_invalid_saved_revision_and_unbound_exports_still_reject(self):
        for filename in ('run-record.json','summary.json','layers.jsonl','calendar-state.json'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                _, out = self.saved(Path(tmp)); path=out/filename
                if filename == 'run-record.json': self.rewrite(out, filename, lambda r:r.update(stop_reason='ending'),False)
                else: path.write_bytes(path.read_bytes()+b' ')
                with self.assertRaises(ValueError): self.read(out)

    def test_unknown_descriptor_schema_or_scope_rejects_rehashed_record(self):
        for field,value in [('schema_version',2), ('schema_version',True),('descriptor_scope','future'),('time_axis',None)]:
            with self.subTest(field=field,value=value), tempfile.TemporaryDirectory() as tmp:
                _, out=self.saved(Path(tmp)); record=self.read(out); record[field]=value
                record.pop('saved_revision_hash'); record['saved_revision_hash']=digest(record)
                (out/'run-record.json').write_text(json.dumps(record))
                with self.assertRaises(ValueError): self.read(out)

    def test_rehashed_calendar_axis_conflicts_reject(self):
        for edit in (lambda a:a.update(unit='logical_step'), lambda a:a.update(end=a['end']+1),
                     lambda a:a.update(start=True)):
            with tempfile.TemporaryDirectory() as tmp:
                _,out=self.saved(Path(tmp)); record=self.read(out); edit(record['time_axis'])
                record.pop('saved_revision_hash'); record['saved_revision_hash']=digest(record)
                (out/'run-record.json').write_text(json.dumps(record))
                with self.assertRaises(ValueError): self.read(out)

    def test_duplicate_json_keys_cannot_change_boundary_meaning(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,out=self.saved(Path(tmp)); (out/'run-record.json').unlink()
            path=out/'summary.json'; path.write_text(path.read_text().replace('"done":false','"done":false,"done":true'))
            with self.assertRaises(ValueError): self.read(out)

    def test_unsaved_runtime_change_cannot_recommit_old_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            project,out=self.saved(Path(tmp)); world,people=load_project(project/'world.yaml')
            sim=Simulation(0,world,people,out)
            before=(out/'run-record.json').read_bytes()
            with self.assertRaisesRegex(ValueError,'differs from runtime'): commit_run_record(sim)
            self.assertEqual(before,(out/'run-record.json').read_bytes())

    def test_unknown_ledger_is_readable_without_terminal_or_target_claims(self):
        for label in LABELS:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)
                (out/'layers.jsonl').write_text(json.dumps({'kind':'header','schema_version':99})+'\n'+
                    json.dumps({'kind':'event','turn':1,'verb':label,'id':'authored','details':{}})+'\n')
                (out/'summary.json').write_text(json.dumps({'now':1,'done':True}))
                record=self.read(out)
                self.assertIsNone(record['actual_terminal']); self.assertEqual(record['observed_targets'],[])
                self.assertEqual(record['stop_reason'],'unknown')


class LogicalRunRecordCompatibilityTests(unittest.TestCase):
    def simulation(self, out, *, observe=False, terminal='ending'):
        from frozen_world import load_frozen_world
        from engine.subject import Subject
        project=ROOT/'projects/momotaro'
        world=load_frozen_world(project/'world.yaml')
        people={p.id:p for p in (Subject.from_yaml(path) for path in sorted((project/'subjects').glob('*.yaml')))}
        world.days=1; world.slots=world.slots[:3]
        world.daily_events=(); world.daily_event_chance=0; world.scheduled_events=()
        for person in people.values(): person.verbs={'rest'}
        world.target_ending='fixture'
        world.endings=[{'id':'fixture','label':'fixture',
                        'predicate_source':'turn >= 1' if terminal=='ending' else 'turn < 0'}]
        if terminal=='aborted': people[world.protagonist].vitality='dead'
        return Simulation(0,world,people,out,
            terminal_policy={'target_as_observation':observe,'death_stops':True})

    def test_real_logical_ending_aborted_time_limit_and_target_observation(self):
        for terminal,observe,expected in [('ending',False,'ending'),('aborted',False,'aborted'),
                ('time_limit',False,'time_limit'),('ending',True,'observation_finished')]:
            with self.subTest(terminal=terminal,observe=observe), tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp); sim=self.simulation(out,observe=observe,terminal=terminal);sim.run()
                record=sim.run_record()
                self.assertEqual(record['stop_reason'],expected)
                self.assertEqual(record['actual_terminal']['verb'],'ending' if expected=='time_limit' else expected)
                self.assertEqual(len(record['observed_targets']),1 if observe else 0)
                if observe:self.assertEqual(record['observed_targets'][0]['event_id'],'fixture')
                original=(out/'run-record.json').read_bytes()
                self.assertEqual(sim.run_record(),record);self.assertEqual((out/'run-record.json').read_bytes(),original)
                (out/'run-record.json').unlink()
                fallback=sim.run_record()
                for field in ('actual_terminal','stop_reason','observed_targets'):
                    self.assertEqual(fallback[field],record[field])

    def test_real_non_calendar_slot_runtime_retains_observation_finished(self):
        from festival_fixture import load_frozen_festival
        from scripts.cultural_festival_trial import FestivalPolicy
        with tempfile.TemporaryDirectory() as tmp:
            world,people=load_frozen_festival()
            sim=Simulation(0,world,people,Path(tmp),policies={name:FestivalPolicy(world.slots) for name in people})
            sim.run(); record=sim.run_record()
            self.assertEqual(record['time_axis']['unit'],'logical_step')
            self.assertEqual(record['actual_terminal']['verb'],'observation_finished')
            self.assertEqual(record['stop_reason'],'observation_finished')
            self.assertEqual(record['observed_targets'],[])

    def test_non_event_labels_do_not_create_terminal_or_target_evidence(self):
        for kind in ('decision','snapshot','header'):
            for label in LABELS:
                with self.subTest(kind=kind,label=label), tempfile.TemporaryDirectory() as tmp:
                    out=Path(tmp);sim=self.simulation(out)
                    rows=[sim._header(),{'kind':kind,'turn':1,'verb':label,'details':{}}]
                    (out/'layers.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
                    record,_=_saved_projection(out)
                    self.assertIsNone(record['actual_terminal']);self.assertEqual(record['observed_targets'],[])

    def test_malformed_logical_terminal_policy_is_not_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);sim=self.simulation(out);header=sim._header()
            header['terminal_policy']['target_as_observation']='false'
            (out/'layers.jsonl').write_text(json.dumps(header)+'\n'+json.dumps(
                {'kind':'event','turn':1,'verb':'ending','id':'fixture','details':{}})+'\n')
            with self.assertRaisesRegex(ValueError,'terminal policy'):_saved_projection(out)


if __name__ == '__main__': unittest.main()
