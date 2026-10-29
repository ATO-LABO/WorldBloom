from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest

from execution import world_editor
from execution.library import LibraryStore
from execution.provenance import ConfigError
from viewer.explanation_ui import planning_panel

ROOT = Path(__file__).resolve().parents[1]


class PlanningEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.folder = self.repo / 'projects' / 'ship_planning'
        shutil.copytree(ROOT / 'projects' / 'ship_planning', self.folder)
        self.store = LibraryStore(self.repo)
        self.current = world_editor.snapshot(self.store, 'ship_planning')

    def save(self, value, operation='planning', revision=None):
        return world_editor.save(self.store, 'ship_planning', {'revision': revision or self.current['revision'],
            'operation': operation, 'target': None, 'values': {'definition': value}})

    def test_check_never_writes_and_save_preserves_person_values_and_other_settings(self):
        before = {p: p.read_bytes() for p in self.folder.rglob('*.yaml')}
        value = deepcopy(self.current['world']['planning']); value['max_nodes'] = 256
        self.assertTrue(self.save(value, 'planning-check')['valid'])
        for p, raw in before.items(): self.assertEqual(p.read_bytes(), raw)
        after = self.save(value)
        self.assertEqual(after['world']['planning'], value)
        for key, original in self.current['world'].items():
            if key != 'planning': self.assertEqual(after['world'][key], original)
        for p, raw in before.items():
            if p.name != 'world.yaml': self.assertEqual(p.read_bytes(), raw)

    def test_invalid_bounds_or_actors_do_not_replace_saved_configuration(self):
        original = (self.folder / 'world.yaml').read_bytes()
        for key, value in [('max_nodes', 8), ('max_nodes', True), ('max_depth', 50), ('actors', ['unknown'])]:
            with self.subTest(key=key):
                candidate = deepcopy(self.current['world']['planning']); candidate[key] = value
                with self.assertRaises(ConfigError): self.save(candidate)
                self.assertEqual((self.folder / 'world.yaml').read_bytes(), original)

    def test_removal_does_not_silently_orphan_intermediate_operators(self):
        original = (self.folder / 'world.yaml').read_bytes()
        with self.assertRaises(ConfigError): self.save(None)
        self.assertEqual((self.folder / 'world.yaml').read_bytes(), original)

    def test_revision_guard_preserves_external_edits(self):
        value = deepcopy(self.current['world']['planning']); value['max_nodes'] = 256
        self.save(value)
        with self.assertRaises(ConfigError) as caught: self.save(value)
        self.assertEqual(caught.exception.code, 'conflict')

    def test_render_distinguishes_conditional_plan_actual_result_and_no_plan(self):
        html = planning_panel({'goal_label': '<script>goal</script>', 'status': 'conditional',
            'reason': 'previous_action_failed', 'assumptions': [{'consent': 'B'}],
            'steps': [{'verb': 'respond', 'args': ['x'], 'label': '<img src=x>', 'duration': 2}]})
        self.assertIn('直前の行動が失敗したため再計画', html)
        self.assertIn('予定した結果が実現した記録ではありません', html)
        self.assertIn('&lt;script&gt;goal&lt;/script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        empty = planning_panel({'status': 'search_limit'})
        self.assertIn('手順が見つからないことと、目的の失敗確定は分けて扱います', empty)

    def test_editor_asset_is_registered_and_shared_without_new_css(self):
        from viewer.server import STATIC_FILES
        from viewer.world_advanced import render
        self.assertIn('planning-editor.js', STATIC_FILES)
        # Rendering of the full workspace is covered by the existing saved-world
        # tests; the new renderer owns no private tokens or stylesheet.
        source = (ROOT / 'viewer/static/planning-editor.js').read_text(encoding='utf8')
        self.assertIn('ui-field', source)
        self.assertNotIn('style.', source)


if __name__ == '__main__': unittest.main()
