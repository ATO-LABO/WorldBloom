from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from execution.library import LibraryStore
from execution import world_editor
from execution.provenance import ConfigError
from viewer import world_advanced

ROOT=Path(__file__).resolve().parents[1]


class ResponseEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)
        shutil.copytree(ROOT/'projects/village_response',self.repo/'projects/village_response')
        self.store=LibraryStore(self.repo);self.current=world_editor.snapshot(self.store,'village_response')

    def save(self,value,operation='responses',revision=None):
        return world_editor.save(self.store,'village_response',{'revision':revision or self.current['revision'],
            'operation':operation,'target':None,'values':{'definition':value}})

    def test_check_does_not_write_and_valid_save_preserves_other_files(self):
        path=self.repo/'projects/village_response/world.yaml';before=path.read_bytes()
        people={p:p.read_bytes() for p in (path.parent/'subjects').glob('*.yaml')}
        value=deepcopy(self.current['world']['responses']);value['actions'][0]['label']='別の対処名'
        self.assertTrue(self.save(value,'responses-check')['valid']);self.assertEqual(path.read_bytes(),before)
        saved=self.save(value)
        self.assertEqual(saved['world']['responses'],value)
        for k,v in self.current['world'].items():
            if k!='responses':self.assertEqual(saved['world'][k],v)
        for p,content in people.items():self.assertEqual(p.read_bytes(),content)
        with self.assertRaises(ConfigError):self.save(value)

    def test_invalid_and_orphaned_config_cannot_replace_world(self):
        path=self.repo/'projects/village_response/world.yaml';before=path.read_bytes()
        value=deepcopy(self.current['world']['responses']);value['actions'][0]['zone']='不存在'
        with self.assertRaises(ConfigError):self.save(value)
        with self.assertRaises(ConfigError):self.save(None)
        self.assertEqual(path.read_bytes(),before)

    def test_advanced_navigation_and_readonly_assets(self):
        row=next(w for w in self.store.worlds() if w['id']=='village_response')
        html=world_advanced.render(row,self.store,None)
        self.assertIn('data-advanced-section="responses"',html)
        self.assertIn('/static/responses-editor.js',html)
        self.assertIn('閲覧専用です',html)

    def test_batch_retains_every_seed_and_hashes_actual_ledger(self):
        from gapengine.response_episode import run_batch
        import hashlib
        output=self.repo/'batch'
        summary=run_batch(ROOT/'projects/village_response/world.yaml',output,[0,1])
        self.assertEqual(summary['trials'],2)
        rows=[json.loads(line) for line in (output/'trials.jsonl').read_text(encoding='utf8').splitlines()]
        self.assertEqual(len(rows),2)
        for row in rows:
            outcome=row['outcome'];self.assertEqual(hashlib.sha256((output/outcome['ledger']).read_bytes()).hexdigest(),outcome['ledger_sha256'])
