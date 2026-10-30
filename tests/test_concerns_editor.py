"""Real revision-checked settings operations, validation and non-destructive checks."""
from copy import deepcopy
from pathlib import Path
import json
import shutil
import tempfile
import unittest

from execution.library import LibraryStore
from execution import world_editor
from execution.provenance import ConfigError
from viewer import world_advanced

ROOT=Path(__file__).resolve().parents[1]


class ConcernsEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)
        shutil.copytree(ROOT/'projects/secret_values',self.repo/'projects/secret_values')
        self.store=LibraryStore(self.repo)
        self.current=world_editor.snapshot(self.store,'secret_values')

    def save(self,definition,*,operation='concerns',revision=None):
        return world_editor.save(self.store,'secret_values',{'revision':revision or self.current['revision'],
            'operation':operation,'target':None,'values':{'definition':definition}})

    def test_unsaved_check_validates_actual_definition_without_writing(self):
        p=self.repo/'projects/secret_values/world.yaml';before=p.read_bytes()
        changed=deepcopy(self.current['world']['concerns']);changed['conditions'][0]['label']='別の説明'
        self.assertTrue(self.save(changed,operation='concerns-check')['valid'])
        self.assertEqual(p.read_bytes(),before)
        changed['conditions'][0]['test']['actors']=['不存在']
        with self.assertRaises(ConfigError):self.save(changed,operation='concerns-check')
        self.assertEqual(p.read_bytes(),before)

    def test_save_preserves_world_fields_and_exact_person_files(self):
        folder=self.repo/'projects/secret_values'
        subjects={p:p.read_bytes() for p in (folder/'subjects').glob('*.yaml')}
        changed=deepcopy(self.current['world']['concerns']);changed['conditions'][0]['label']='<img src=x onerror=alert(1)>'
        result=self.save(changed)
        self.assertEqual(result['world']['concerns'],changed)
        for key,value in self.current['world'].items():
            if key!='concerns':self.assertEqual(result['world'][key],value)
        for path,value in subjects.items():self.assertEqual(path.read_bytes(),value)
        with self.assertRaises(ConfigError) as caught:self.save(changed)
        self.assertEqual(caught.exception.code,'conflict')

    def test_invalid_or_orphaned_definition_cannot_replace_saved_data(self):
        path=self.repo/'projects/secret_values/world.yaml';before=path.read_bytes()
        with self.assertRaises(ConfigError):self.save(None)
        bad=deepcopy(self.current['world']['concerns']);bad['conditions'][0]['test']={'kind':'code','eval':'True'}
        with self.assertRaises(ConfigError):self.save(bad)
        self.assertEqual(path.read_bytes(),before)

    def test_advanced_screen_reuses_shared_assets_in_read_only_mode(self):
        row=next(w for w in self.store.worlds() if w['id']=='secret_values')
        html=world_advanced.render(row,self.store,None)
        self.assertIn('data-advanced-section="concerns"',html)
        self.assertIn('/static/concerns-editor.js',html)
        self.assertLess(html.index('/static/concerns-editor.js'),html.index('/static/world-advanced.js'))
        self.assertIn('ui-screen',html)
        self.assertIn('閲覧専用です',html)

    def test_saved_explanation_shows_forecast_separately_from_actual_pending_result(self):
        from gapengine.deliberation_episode import run_batch
        from gapengine.explanations import explain_rows
        from viewer.explanation_ui import panel
        target=Path(self.temp.name)/'runs'
        run_batch(ROOT/'projects/secret_values/world.yaml',target,[0])
        rows=[json.loads(line) for line in (target/'seed-0000/layers.jsonl').read_text(encoding='utf8').splitlines()]
        explanation=explain_rows(rows,experiment='example',cell='cell')
        selected=next(item for item in explanation['decisions'] if item['verb']=='amend_proposal')
        # Escaping must apply to authored labels, including inside details.
        selected['outcome']['details']['deliberation_event']['world_concerns'][0]['label']='<script>hidden</script>'
        html=panel(explanation,selected,details=False)
        self.assertIn('この案で守れそうなこと（本人の認識）',html)
        self.assertIn('記録時点の実際の状態',html)
        self.assertIn('まだ実現していない',html)
        self.assertIn('&lt;script&gt;hidden&lt;/script&gt;',html)
        self.assertNotIn('<script>',html)


if __name__=='__main__':unittest.main()
