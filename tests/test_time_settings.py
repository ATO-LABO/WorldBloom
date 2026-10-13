"""Sidecar calendar drafts preserve fixed-world and existing-run contracts."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

from execution import time_settings as ts, world_editor
from execution.library import LibraryStore
from execution.provenance import ConfigError

ROOT = Path(__file__).resolve().parents[1]


class TimeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.project = self.repo / 'projects/festival'
        shutil.copytree(ROOT / 'samples/route_goals/cultural_festival', self.project)
        self.world_path = self.project / 'world.yaml'
        world = yaml.safe_load(self.world_path.read_text())
        world['scheduled_events'] = [{'id': 'opening', 'day': 1, 'slot': '15:00', 'label': '開演', 'targets': ['実行委員']}]
        self.world_path.write_text(yaml.safe_dump(world, allow_unicode=True))
        self.store = LibraryStore(self.repo)
        self.settings = ts.default_settings()
        self.settings['period'] = {'start_date': '2026-10-12', 'end_date': '2026-10-14'}

    def snapshot(self):
        return ts.snapshot(self.store, 'festival')

    def body(self, settings=None):
        return {'revision': self.snapshot()['revision'], 'settings': deepcopy(self.settings if settings is None else settings)}

    def evaluate(self, settings=None):
        return ts.validate(self.store, 'festival', self.body(settings))

    def special(self, ident='festival', interval=60, priority=10, selector=None, enabled=True):
        band = deepcopy(self.settings['normal']['bands'][0])
        band['interval_minutes'] = interval
        return {'id': ident, 'name': ident, 'enabled': enabled, 'priority': priority,
                'selector': selector or {'kind': 'date', 'start': '2026-10-13'}, 'bands': [band]}

    def original_files(self):
        return {p.relative_to(self.project).as_posix(): p.read_bytes() for p in self.project.rglob('*.yaml')}

    def test_defaults_do_not_invent_dates_and_validate_is_read_only(self):
        before = {p.relative_to(self.repo).as_posix(): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        snapshot = self.snapshot()
        self.assertEqual(snapshot['settings']['period'], {'start_date': '', 'end_date': ''})
        self.assertEqual(snapshot['preview']['status'], 'unresolved')
        self.assertFalse(snapshot['validation']['valid'])
        ts.validate(self.store, 'festival', {'revision': snapshot['revision'], 'settings': snapshot['settings']})
        after = {p.relative_to(self.repo).as_posix(): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_save_reload_invalid_draft_and_preserve_world_events_runs(self):
        before = self.original_files()
        run = self.project / 'runs/old/config.json'
        run.parent.mkdir(parents=True); run.write_bytes(b'{"immutable":true}')
        self.settings['specials'] = [self.special(interval=0)]
        saved = ts.save(self.store, 'festival', self.body())
        self.assertEqual(saved, self.snapshot())
        self.assertEqual(saved['settings'], self.settings)
        self.assertFalse(saved['validation']['valid'])
        self.assertEqual(before, self.original_files())
        self.assertEqual(run.read_bytes(), b'{"immutable":true}')
        self.assertTrue((self.project / ts.DRAFT).is_file())

    def test_stale_draft_and_world_edits_refuse_without_overwriting(self):
        body = self.body()
        ts.save(self.store, 'festival', body)
        for stale in (body,):
            with self.assertRaises(ConfigError) as caught:
                ts.save(self.store, 'festival', stale)
            self.assertEqual(caught.exception.code, 'conflict')
        fresh = self.body()
        world = yaml.safe_load(self.world_path.read_text()); world['name'] = 'changed'
        self.world_path.write_text(yaml.safe_dump(world))
        with self.assertRaises(ConfigError) as caught:
            ts.save(self.store, 'festival', fresh)
        self.assertEqual(caught.exception.code, 'conflict')
        self.assertEqual(json.loads((self.project / ts.DRAFT).read_text()), self.settings)

    def test_failed_atomic_save_retains_old_draft_and_cleans_temporary(self):
        ts.save(self.store, 'festival', self.body())
        before = (self.project / ts.DRAFT).read_bytes()
        self.settings['normal']['bands'][0]['interval_minutes'] = 30
        with patch.object(ts.os, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                ts.save(self.store, 'festival', self.body())
        self.assertEqual((self.project / ts.DRAFT).read_bytes(), before)
        self.assertFalse(list(self.project.glob('.time-settings-draft.json-*')))

    def test_same_priority_conflicts_are_visible_no_silent_adoption_and_resolve(self):
        self.settings['specials'] = [self.special('culture', 60), self.special('last', 30)]
        result = self.evaluate()
        conflicts = result['preview']['days'][1]['conflicts']
        self.assertEqual(conflicts[0]['sources'], ['culture', 'last'])
        self.assertEqual(result['preview']['days'][1]['bands'], [])
        errors = result['validation']['errors']
        self.assertEqual({e['field'] for e in errors if e['code'] == 'conflict'}, {'specials.0.bands', 'specials.1.bands'})
        saved = ts.save(self.store, 'festival', self.body())
        self.assertFalse(saved['validation']['valid'])
        with self.assertRaises(ConfigError): ts.archive_design(self.store, 'festival', self.body())
        self.settings['specials'][1]['priority'] = 11
        resolved = self.evaluate()
        self.assertTrue(resolved['validation']['valid'])
        band = resolved['preview']['days'][1]['bands'][0]
        self.assertEqual(band['interval_minutes'], 30)
        self.assertEqual(band['source_ids'], ['last'])

    def test_last_disabled_special_uses_normal_not_deleted(self):
        self.settings['specials'] = [self.special(interval=30, enabled=False)]
        result = self.evaluate()
        self.assertTrue(result['validation']['valid'])
        band = result['preview']['days'][1]['bands'][0]
        self.assertEqual(band['interval_minutes'], 60)
        self.assertEqual(band['source_ids'], ['normal'])
        self.assertEqual(len(ts.save(self.store, 'festival', self.body())['settings']['specials']), 1)

    def test_relative_dates_weekdays_and_holidays_keep_sources(self):
        self.settings['specials'] = [self.special(selector={'kind': 'last', 'offset': 0}, interval=30)]
        override = deepcopy(self.settings['normal']['bands'][0]); override['interval_minutes'] = 45
        self.settings['normal']['weekdays'] = {'0': [override]}
        override = deepcopy(override); override['interval_minutes'] = 120
        self.settings['normal']['holidays'] = [{'date': '2026-10-13', 'bands': [override]}]
        result = self.evaluate()
        self.assertTrue(result['validation']['valid'])
        days = result['preview']['days']
        self.assertEqual([d['bands'][0]['source_ids'] for d in days], [['weekday:0'], ['holiday:0'], ['festival']])
        self.assertEqual([d['bands'][0]['interval_minutes'] for d in days], [45, 120, 30])

    def test_remainder_and_overnight_explicit_without_changing_action_duration(self):
        band = self.settings['normal']['bands'][0]
        band.update(start='22:00', end='01:10', overnight=True, interval_minutes=60)
        result = self.evaluate()
        self.assertTrue(result['validation']['valid'])
        preview = result['preview']['days'][0]['bands'][0]
        self.assertEqual(preview['remainder_minutes'], 10)
        self.assertEqual(preview['segments'][-1], {'start': '01:00', 'end': '01:10', 'start_day_offset': 1,
                                                 'end_day_offset': 1, 'duration_minutes': 10, 'short': True})
        self.assertFalse(result['preview']['action_durations_changed'])
        band['overnight'] = False
        self.assertFalse(self.evaluate()['validation']['valid'])

    def test_dates_intervals_names_and_overlap_rejected_but_persistable(self):
        cases = []
        for interval in (0, -1, 1.5, True, '60', None):
            s = deepcopy(self.settings); s['normal']['bands'][0]['interval_minutes'] = interval; cases.append(s)
        s = deepcopy(self.settings); s['period']['end_date'] = '2026-10-11'; cases.append(s)
        s = deepcopy(self.settings); s['period']['start_date'] = '2026-02-30'; cases.append(s)
        s = deepcopy(self.settings); s['specials'] = [self.special(selector={'kind': 'date', 'start': '2026-11-01'})]; cases.append(s)
        s = deepcopy(self.settings); s['normal']['bands'][0]['name'] = ''; cases.append(s)
        s = deepcopy(self.settings); b = deepcopy(s['normal']['bands'][0]); b['id'] = 'overlap'; s['normal']['bands'].append(b); cases.append(s)
        s = deepcopy(self.settings); s['normal']['bands'][0].update(start='22:00', end='02:00', overnight=True)
        b = deepcopy(s['normal']['bands'][0]); b.update(id='early', start='01:00', end='03:00', overnight=False); s['normal']['bands'].append(b); cases.append(s)
        for s in cases:
            with self.subTest(s=s):
                result = self.evaluate(s)
                self.assertFalse(result['validation']['valid'])
                self.assertTrue(result['validation']['errors'])
                ts.save(self.store, 'festival', self.body(s))

    def test_overnight_overlap_uses_actual_neighbor_calendar_not_repeating_special(self):
        # Monday has an overnight special, Tuesday has an otherwise disjoint early override.
        self.settings['normal']['bands'][0].update(start='03:00', end='06:00')
        special = self.special(selector={'kind': 'first', 'offset': 0})
        special['bands'][0].update(start='22:00', end='02:00', overnight=True)
        self.settings['specials'] = [special]
        early = deepcopy(self.settings['normal']['bands'][0]); early.update(id='early', start='01:00', end='03:00')
        self.settings['normal']['weekdays'] = {'1': [early]}
        result = self.evaluate()
        self.assertFalse(result['validation']['valid'])
        self.assertTrue(any('前日の夜跨ぎ' in e['message'] for e in result['validation']['errors']))
        self.settings['normal']['weekdays'] = {'2': [early]}
        self.assertTrue(self.evaluate()['validation']['valid'])

    def test_event_reference_reads_same_event_and_updates_without_duplicate(self):
        band = self.settings['normal']['bands'][0]; band['event_id'] = 'opening'; band['start'] = '09:00'
        result = self.evaluate()
        preview = result['preview']['days'][0]['bands'][0]
        self.assertEqual(preview['segments'][0]['start'], '15:00')
        self.assertEqual(preview['event_reference']['id'], 'opening')
        before = self.original_files(); ts.save(self.store, 'festival', self.body())
        self.assertEqual(before, self.original_files())
        world = yaml.safe_load(self.world_path.read_text()); world['scheduled_events'][0]['slot'] = '16:00'
        self.world_path.write_text(yaml.safe_dump(world))
        self.assertEqual(self.evaluate()['preview']['days'][0]['bands'][0]['segments'][0]['start'], '16:00')
        world['scheduled_events'][0]['slot'] = '夕方'; self.world_path.write_text(yaml.safe_dump(world))
        self.assertEqual(self.evaluate()['preview']['status'], 'unresolved')
        band['event_id'] = 'unknown'
        self.assertFalse(self.evaluate()['validation']['valid'])

    def test_design_archive_immutable_and_execution_finalize_explicitly_refuses(self):
        before = self.original_files()
        body = self.body()
        archived = ts.archive_design(self.store, 'festival', body)
        version = archived['version']; path = self.project / ts.VERSION_DIR / (version['id'] + '.json')
        content = path.read_bytes()
        self.assertFalse(version['engine_supported']); self.assertFalse(version['execution_ready'])
        self.assertEqual(ts.archive_design(self.store, 'festival', body)['version'], version)
        self.assertEqual(path.read_bytes(), content)
        self.settings['normal']['bands'][0]['interval_minutes'] = 30
        newer = ts.archive_design(self.store, 'festival', self.body())
        self.assertNotEqual(newer['version']['id'], version['id'])
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(before, self.original_files())
        draft_before = (self.project / ts.DRAFT).exists()
        with self.assertRaises(ConfigError) as caught: ts.finalize(self.store, 'festival', self.body())
        self.assertEqual(caught.exception.code, 'unsupported')
        self.assertEqual((self.project / ts.DRAFT).exists(), draft_before)
        path.write_bytes(b'{}')
        with self.assertRaises(ConfigError): self.snapshot()

    def test_unsafe_envelopes_ids_and_symlink_refuse(self):
        for body in ({}, {**self.body(), 'path': '../world.yaml'}, {'revision': 'x', 'settings': self.settings}):
            with self.assertRaises(ConfigError): ts.save(self.store, 'festival', body)
        bad = deepcopy(self.settings); bad['extra'] = 'not schema'
        with self.assertRaises(ConfigError): ts.save(self.store, 'festival', self.body(bad))
        bad = deepcopy(self.settings); bad['normal']['bands'][0]['interval_minutes'] = float('nan')
        with self.assertRaises(ConfigError): ts.save(self.store, 'festival', self.body(bad))
        with self.assertRaises(ConfigError): ts.snapshot(self.store, '../festival')
        outside = self.repo / 'outside.json'; outside.write_text('{}')
        (self.project / ts.DRAFT).symlink_to(outside)
        with self.assertRaises(ConfigError): self.snapshot()
        self.assertEqual(outside.read_text(), '{}')

    def test_empty_existing_draft_is_corruption_not_an_unregistered_design(self):
        (self.project / ts.DRAFT).write_bytes(b'')
        with self.assertRaises(ConfigError): self.snapshot()
        self.assertEqual((self.project / ts.DRAFT).read_bytes(), b'')

    def test_copy_preserves_sidecars_but_new_world_edits_are_independent(self):
        shutil.copytree(ROOT / 'templates/momotaro', self.repo / 'templates/momotaro')
        ts.save(self.store, 'festival', self.body())
        archived = ts.archive_design(self.store, 'festival', self.body())['version']
        source_bytes = (self.project / ts.DRAFT).read_bytes()
        self.store.create_world('copied', from_id='festival', genre_id='momotaro', name='copy')
        copied = ts.snapshot(self.store, 'copied')
        self.assertEqual(copied['settings'], self.settings)
        self.assertNotEqual(copied['world_revision'], archived['world_revision'])
        self.assertEqual(copied['versions'][0]['world_revision'], archived['world_revision'])
        new_settings = deepcopy(copied['settings']); new_settings['normal']['bands'][0]['interval_minutes'] = 30
        ts.save(self.store, 'copied', {'revision': copied['revision'], 'settings': new_settings})
        self.assertEqual((self.project / ts.DRAFT).read_bytes(), source_bytes)
        self.assertEqual(ts.snapshot(self.store, 'festival')['versions'][0]['id'], archived['id'])

    def test_capture_engine_inputs_ignores_draft_and_design_version(self):
        from execution.configs import _capture_inputs
        from engine.world import World
        shutil.copytree(ROOT / 'templates/momotaro', self.repo / 'templates/momotaro')
        spec = {'project_id': 'festival', 'template_id': 'momotaro', 'evolution': {'world_expansion': 'fixed'}}
        before = _capture_inputs(self.repo, spec)
        engine_before = World.from_yaml(self.world_path, action_graph_path=self.project / 'action_graph.yaml')
        ts.save(self.store, 'festival', self.body())
        ts.archive_design(self.store, 'festival', self.body())
        after = _capture_inputs(self.repo, spec)
        engine_after = World.from_yaml(self.world_path, action_graph_path=self.project / 'action_graph.yaml')
        self.assertEqual(before, after)
        self.assertEqual((engine_before.days, engine_before.slots), (engine_after.days, engine_after.slots))
        self.assertFalse(any('time-settings' in key for key in after[0]))

    def test_snapshot_refuses_mid_read_updates_and_version_directory_symlinks(self):
        real_evaluate = ts.evaluate
        def modified_evaluate(settings, world):
            result = real_evaluate(settings, world)
            raw_world = yaml.safe_load(self.world_path.read_text()); raw_world['name'] = 'concurrent edit'
            self.world_path.write_text(yaml.safe_dump(raw_world))
            return result
        with patch.object(ts, 'evaluate', side_effect=modified_evaluate):
            with self.assertRaises(ConfigError) as caught: self.snapshot()
        self.assertEqual(caught.exception.code, 'conflict')
        outside = self.repo / 'outside-versions'; outside.mkdir()
        (self.project / ts.VERSION_DIR).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ConfigError): self.snapshot()
        self.assertEqual(list(outside.iterdir()), [])

    def test_preview_is_bounded_explicitly_and_conflicts_after_visible_window_refuse(self):
        self.settings['period']['end_date'] = '2027-10-14'
        self.settings['normal']['bands'][0]['interval_minutes'] = 1
        result = self.evaluate()
        self.assertTrue(result['preview']['truncated'])
        self.assertTrue(result['preview']['segments_truncated'])
        self.assertEqual(len(result['preview']['days']), ts.PREVIEW_DAYS)
        self.assertLessEqual(sum(len(b['segments']) for d in result['preview']['days'] for b in d['bands']), ts.MAX_SEGMENTS)
        self.settings['specials'] = [self.special('a', 30, selector={'kind': 'date', 'start': '2027-10-14'}),
                                    self.special('b', 60, selector={'kind': 'last', 'offset': 0})]
        self.assertFalse(self.evaluate()['validation']['valid'])


if __name__ == '__main__':
    unittest.main()
