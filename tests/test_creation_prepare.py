import unittest
from copy import deepcopy
from pathlib import Path
from html.parser import HTMLParser
import test_home_workspace as fixtures
from viewer.creation_workflow import render
from execution.world_drafts import mock_response,parse_response,validate
from execution.provenance import ConfigError

class PrepareTests(unittest.TestCase):
    setUp=fixtures.HomeWorkspaceTests.setUp

    def test_preparation_has_only_requested_fields_in_order_and_accessible_help(self):
        text=render(self.store,self.jobs)
        prepare=text.split('<form data-cw-prepare>')[1].split('</form>')[0]
        tokens=['name="name"','考え始める手がかり','自由に考える','世界の脅威への対応','name="overview"','name="genre"']
        self.assertEqual(sorted(prepare.index(t) for t in tokens),[prepare.index(t) for t in tokens])
        for old in ['name="days"','name="slots"','name="outline"','型・導入と時間帯のメモ','制作メモ','data-cw-preserve']:
            self.assertNotIn(old,prepare)
        self.assertNotIn('data-cw-preserve',text)
        self.assertIn('type="button" data-cw-help',prepare)
        self.assertIn('role="tooltip"',prepare)
        self.assertIn('aria-controls="cw-threat-help"',prepare)
        self.assertIn('value="threat-response"',prepare)
        self.assertIn('物語の期間・時間帯と導入文は',text)
        self.assertIn('/static/creation-prepare.js',text)

    def test_time_reference_conflicts_remain_validation_errors_without_deleting_events(self):
        for time,event in [({'days':3,'slots':['朝','夜']},{'day':5,'slot':'夜'}),({'days':7,'slots':['朝']},{'day':2,'slot':'夜'})]:
            draft=parse_response(mock_response('設定の検証','合成データ'))
            draft['settings']={'version':1,'time':time,'scheduled_events':[{'id':'existing_event','targets':['アオ'],**event}]}
            before=deepcopy(draft)
            with self.assertRaises(ConfigError):validate(Path(__file__).resolve().parents[1],draft)
            self.assertEqual(draft,before)

if __name__=='__main__':unittest.main()
