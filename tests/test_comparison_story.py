"""Semantic comparison uses recorded evidence, never live inference in read paths."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from execution.configs import ConfigStore
from execution.provenance import ConfigError
from gapengine import reader_summary as rs
from gapengine.synopsis import GenerationResult
from gapengine.world_patch import PatchError
from viewer import data, comparison_story
from test_viewer import _create_experiment
from test_reader_summary import _summary_for

ROOT = Path(__file__).resolve().parents[1]


class ComparisonStoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.exp = _create_experiment(self.root / 'runs')
        self.repo = data.RunRepository(self.root / 'runs')

    def test_recorded_sample_payoff_ending_and_missing_goal(self):
        repo = data.RunRepository(ROOT / 'samples')
        exp = repo.experiment('exp12-momotaro')
        consequences = []
        with patch('gapengine.synopsis.generate_text', side_effect=AssertionError('live AI')):
            for cell in ('III|high', 'III|mid', 'II|high'):
                info = comparison_story.describe(data.cell_explanation(repo, exp, cell))
                self.assertIsNone(info['goal'])
                self.assertIn('焚き火で交わした約束', info['title'])
                self.assertIn('宝を村へ持ち帰った', info['ending'])
                self.assertNotEqual(info['consequence'], 'shared')
                self.assertEqual(len(info['lines']), 3)
                self.assertEqual(info['label'], '原記録から整理')
                consequences.append(info['consequence'])
        self.assertEqual(len(set(consequences)), 1)  # Timing alone is not a different plot.

    def test_frozen_goal_uses_selected_endings_and_rejects_tampering(self):
        store = ConfigStore(ROOT, self.root / 'control', self.root / 'frozen-runs')
        store.save({'label': 'test', 'project_id': 'momotaro', 'template_id': 'momotaro',
                    'evolution': {'generations': 1, 'population': 1, 'seeds': 1,
                                  'target_ending': ['homecoming_shared']}}, config_id='cfg-test')
        store.prepare_run('cfg-test', run_id='run-test', job_id='job-test')
        frozen = self.root / 'frozen-runs/run-test'
        goal = comparison_story.frozen_goal(frozen)
        self.assertEqual(goal['ids'], ['homecoming_shared'])
        self.assertEqual(goal['labels'], ['宝を村へ還元し、皆で喜びを分かち合った'])
        world = frozen / goal['path']
        world.write_bytes(world.read_bytes() + b'\n# changed')
        with self.assertRaises(PatchError):
            comparison_story.frozen_goal(frozen)
        with patch('viewer.data.resolve_genre', side_effect=AssertionError('live world fallback')):
            self.assertIsNone(comparison_story.frozen_goal(self.exp))

    def test_links_are_transitive_deduplicated_forward_only(self):
        ex = data.cell_explanation(self.repo, self.exp, 'III|high')
        rep = ex['representative']
        first = rep['line']
        rep['turning']['links'] = [{'downstream': {'line': first + 1}}]
        second = copy.deepcopy(rep)
        second.update(line=first + 1, turn=2)
        second['turning']['links'] = [{'downstream': {'line': first}}, {'downstream': {'line': first + 2}}]
        third = copy.deepcopy(second)
        third.update(line=first + 2, turn=3, verb='payoff')
        third['outcome'] = {'result': 'paid_off', 'details': {'description': '記録された回収', 'secret': 'HIDDEN'}}
        third['turning']['links'] = []
        ex['decisions'] = [rep, second, third]
        packet = rs.build_packet(ex)
        later = [f for f in packet['facts'] if f['kind'].startswith('executed_later')]
        self.assertEqual([f['lines'] for f in later], [[first + 1], [first + 2]])
        self.assertIn('記録された回収', json.dumps(packet, ensure_ascii=False))
        self.assertNotIn('HIDDEN', json.dumps(packet))
        second['outcome']['result'] = 'invalid'
        packet = rs.build_packet(ex)
        self.assertFalse(any(f['kind'].startswith('executed_later') for f in packet['facts']))

    def test_goal_is_not_allowed_as_choice_or_motive(self):
        ex = data.cell_explanation(self.repo, self.exp, 'III|high')
        ex['comparison_goal'] = {'labels': ['記録された目標'], 'path': 'frozen/world.yaml', 'world_sha256': 'bound'}
        packet = rs.build_packet(ex)
        summary = _summary_for(packet, '短い見出し')
        rs.parse_summary(json.dumps(summary), packet)
        summary['choice']['refs'] = summary['goal']['refs']
        with self.assertRaises(ValueError):
            rs.parse_summary(json.dumps(summary), packet)

    def test_summary_cached_per_packet_and_goal_change_invalidates(self):
        settings = self.root / 'settings.json'
        settings.write_text('{"output":{"codex-cli":{"model":"fixture"}}}')
        def respond(backend, prompt, **kwargs):
            packet = json.loads(prompt.split('資料:\n', 1)[1])
            return GenerationResult('ok', json.dumps(_summary_for(packet, '保存済みの見出し')))
        goal = {'labels': ['最初の目標'], 'path': 'frozen/world.yaml', 'world_sha256': 'first'}
        with patch('viewer.comparison_story.frozen_goal', return_value=goal), \
             patch('gapengine.synopsis.generate_text', side_effect=respond) as call:
            for _ in range(2):
                data.ensure_reader_summary(self.repo, self.exp, 'III|high', settings_path=settings, backend='codex-cli', timeout=5)
            self.assertEqual(call.call_count, 1)
            ex = data.cell_explanation(self.repo, self.exp, 'III|high')
            self.assertEqual(comparison_story.describe(ex)['title'], '保存済みの見出し')
            self.assertEqual(call.call_count, 1)
            goal['world_sha256'] = 'second'
            data.ensure_reader_summary(self.repo, self.exp, 'III|high', settings_path=settings, backend='codex-cli', timeout=5)
            self.assertEqual(call.call_count, 2)

    def test_source_changed_during_generation_is_not_saved(self):
        settings = self.root / 'settings.json'
        settings.write_text('{"output":{"codex-cli":{"model":"fixture"}}}')
        ex = data.cell_explanation(self.repo, self.exp, 'III|high')
        response = json.dumps(_summary_for(rs.build_packet(ex), '古い記録の要約'))
        def respond(*args, **kwargs):
            path = Path(ex['source']['layers_path'])
            path.write_bytes(path.read_bytes() + b'\n')
            return GenerationResult('ok', response)
        with patch('gapengine.synopsis.generate_text', side_effect=respond):
            with self.assertRaises(ConfigError):
                data.ensure_reader_summary(self.repo, self.exp, 'III|high', settings_path=settings, backend='codex-cli', timeout=5)
        self.assertFalse((self.exp / 'reader-summaries' / rs.artifact_name('III|high')).exists())


if __name__ == '__main__':
    unittest.main()
