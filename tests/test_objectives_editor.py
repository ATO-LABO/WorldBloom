from copy import deepcopy
from pathlib import Path
import hashlib
import json
import shutil
import tempfile
import unittest

from execution import world_editor
from execution.library import LibraryStore
from execution.provenance import ConfigError
from gapengine.objective_episode import run_batch
from viewer import world_advanced
from viewer.explanation_ui import objective_outcomes

ROOT=Path(__file__).resolve().parents[1]


class ObjectiveEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name);self.folder=self.repo/'projects/village_objectives'
        shutil.copytree(ROOT/'projects/village_objectives',self.folder)
        self.store=LibraryStore(self.repo);self.current=world_editor.snapshot(self.store,'village_objectives')

    def save(self,value,operation='goal-rules',revision=None):
        return world_editor.save(self.store,'village_objectives',{'revision':revision or self.current['revision'],
            'operation':operation,'target':None,'values':{'definition':value}})

    def test_check_never_writes_and_save_preserves_people_and_other_world_fields(self):
        before={p:p.read_bytes() for p in self.folder.rglob('*.yaml')}
        value=deepcopy(self.current['world']['goal_rules']);value['definitions'][0]['period']['end']=11
        self.assertTrue(self.save(value,'goal-rules-check')['valid'])
        for path,raw in before.items():self.assertEqual(path.read_bytes(),raw)
        result=self.save(value)
        self.assertEqual(result['world']['goal_rules'],value)
        for key,value in self.current['world'].items():
            if key!='goal_rules':self.assertEqual(result['world'][key],value)
        for path,raw in before.items():
            if path.name!='world.yaml':self.assertEqual(path.read_bytes(),raw)
        with self.assertRaises(ConfigError) as caught:self.save(self.current['world']['goal_rules'])
        self.assertEqual(caught.exception.code,'conflict')

    def test_bad_period_and_orphan_refs_do_not_replace_saved_files(self):
        before=(self.folder/'world.yaml').read_bytes()
        for mutate in (lambda v:v['definitions'][0]['period'].update(end=0),
                       lambda v:v['definitions'][0]['period'].update(end=17),
                       lambda v:v['definitions'][0].update(failure={'condition':'absent'})):
            value=deepcopy(self.current['world']['goal_rules']);mutate(value)
            with self.assertRaises(ConfigError):self.save(value)
            self.assertEqual((self.folder/'world.yaml').read_bytes(),before)
        with self.assertRaises(ConfigError):self.save(None)  # Proactive actor still needs this rule.

    def test_removing_failure_predicate_or_reducing_time_cannot_orphan_rules(self):
        value=deepcopy(self.current['world']['concerns'])
        value['conditions']=[c for c in value['conditions'] if c['id']!='raid_repelled']
        with self.assertRaises(ConfigError):self.save(value,operation='concerns')
        before=(self.folder/'world.yaml').read_bytes()
        with self.assertRaises(ConfigError):
            world_editor.save(self.store,'village_objectives',{'revision':self.current['revision'],
                'operation':'time','target':None,'values':{'days':1,'slots':['一時点']}})
        self.assertEqual((self.folder/'world.yaml').read_bytes(),before)

    def test_readonly_shared_screen_assets_and_escaped_actual_outcomes(self):
        row=next(w for w in self.store.worlds() if w['id']=='village_objectives')
        html=world_advanced.render(row,self.store,None)
        self.assertIn('data-advanced-section="objectives"',html)
        self.assertLess(html.index('/static/objectives-editor.js'),html.index('/static/world-advanced.js'))
        self.assertIn('閲覧専用です',html)
        shown=objective_outcomes([dict(label='<script>bad</script>',status='failed',reason='failure_condition',
                                      period=dict(start=1,end=12),decided_at=4)])
        self.assertIn('&lt;script&gt;',shown);self.assertNotIn('<script>',shown)
        self.assertIn('失敗確定',shown);self.assertIn('人物がこの結果を知っているとは限りません',shown)

    def test_batch_preserves_losing_characters_and_all_seeds_with_hashes(self):
        output=self.repo/'batch';summary=run_batch(self.folder/'world.yaml',output,[0,1])
        self.assertEqual(summary['trials'],2);self.assertEqual(summary['execution_failures'],0)
        records=[json.loads(line) for line in (output/'trials.jsonl').read_text(encoding='utf8').splitlines()]
        self.assertEqual([r['seed'] for r in records],[0,1])
        for record in records:
            outcome=record['outcome']
            self.assertEqual(hashlib.sha256((output/outcome['ledger']).read_bytes()).hexdigest(),outcome['ledger_sha256'])
            self.assertIn('鬼',outcome['personal_outcomes']);self.assertIn('桃太郎',outcome['personal_outcomes'])
            self.assertTrue(any(r['status']=='failed' for r in outcome['world_outcomes']))
            self.assertTrue(all(not d['forced'] for d in outcome['decisions'] if d['subject'] in ('鬼','桃太郎')))


if __name__=='__main__':unittest.main()
