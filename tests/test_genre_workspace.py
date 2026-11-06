"""Genre creation, optimistic item saves, and non-mutating draft checks."""
import json
from pathlib import Path
from unittest.mock import patch
import unittest
import yaml
import test_library as fixtures
from execution import genre_editor as ge
from execution.library import LibraryStore
from execution.provenance import ConfigError

class GenreWorkspaceTests(unittest.TestCase):
    setUp = fixtures.LibraryHttpBoundaryTests.setUp
    http = fixtures.LibraryHttpBoundaryTests.http
    get = fixtures.LibraryHttpBoundaryTests.get

    def create(self, **changes):
        body = {'mode':'new','template_id':'custom','name':'冒険と発見','description':'未知の世界へ'}
        body.update(changes)
        return self.http('POST','/api/genres',body)

    def snap(self, ident='romance'):
        return ge.snapshot(LibraryStore(self.repo), ident)

    def test_create_original_without_copy_and_display_name(self):
        before=set((self.repo/'templates').iterdir())
        self.assertEqual(self.create()[0],403)
        self.assertEqual(before,set((self.repo/'templates').iterdir()))

    def test_copy_preserves_files_and_legacy_api(self):
        for body in ({'template_id':'legacy','from_template_id':'romance'}, {'mode':'copy','template_id':'copy','from_template_id':'romance','name':'複製','description':'説明'}):
            self.assertEqual(self.http('POST','/api/genres',body)[0],403)
            self.assertFalse((self.repo/'templates'/body['template_id']).exists())

    def test_duplicate_invalid_inputs_and_write_boundary(self):
        for change in ({},{'template_id':'../bad'},{'name':''},{'mode':'copy','from_template_id':None},{'mode':'bad'},{'description':[]}):
            self.assertEqual(self.create(**change)[0],403)
        self.assertFalse((self.repo/'templates/custom').exists())

    def test_atomic_save_preserves_other_fields_and_conflicts_with_legacy_write(self):
        snap=self.snap();path='rules.yaml'
        before={p:v['content'] for p,v in snap['files'].items()}
        value=snap['files'][path]['value'];value[0]['description']='編集';value[0]['future']={'nested':[1,False,None]}
        body={'path':path,'content':json.dumps(value),'revision':snap['files'][path]['revision']}
        status,result=self.http('POST','/api/genres/romance/save',body)
        self.assertEqual(status,200,result)
        self.assertEqual(result['files'][path]['value'][0]['future'],{'nested':[1,False,None]})
        for p in before:
            if p!=path:self.assertEqual(result['files'][p]['content'],before[p])
        self.assertEqual(self.http('POST','/api/genres/romance/save',body)[0],409)
        current=self.snap()
        LibraryStore(self.repo).write('genre','romance',path,'[]')
        self.assertEqual(self.http('POST','/api/genres/romance/save',{**body,'revision':current['files'][path]['revision']})[0],409)

    def test_save_failure_preserves_original(self):
        snap=self.snap();before=snap['files']['rules.yaml']['content']
        with patch('execution.genre_editor.os.replace',side_effect=OSError('disk error')):
            with self.assertRaises(OSError):ge.save(LibraryStore(self.repo),'romance','rules.yaml','[]',snap['files']['rules.yaml']['revision'])
        self.assertEqual(self.snap()['files']['rules.yaml']['content'],before)
        self.assertFalse(list((self.repo/'templates/romance').glob('.rules.yaml-*')))

    def test_create_failure_not_published(self):
        with patch('execution.genre_editor.yaml.safe_dump',side_effect=OSError('disk error')):
            with self.assertRaises(OSError):ge.create(LibraryStore(self.repo),'failed',name='失敗')
        self.assertFalse((self.repo/'templates/failed').exists())
        self.assertFalse(list(self.repo.glob('.genre-create-*')))

    def test_draft_check_uses_unsaved_selected_genre_and_never_saves(self):
        ge.create(LibraryStore(self.repo),'custom',name='旧基本ルール');before=self.snap('custom')
        world=(self.repo/'projects/momotaro/world.yaml').read_bytes()
        body={'world_id':'momotaro','files':{},'revision':before['revision']}
        status,result=self.http('POST','/api/genres/custom/check',body)
        self.assertEqual(status,200,result)
        self.assertEqual(result['world_name'],'桃太郎')
        # Invalid unsaved action graph must be checked, even though the saved
        # world points at another (valid) genre's graph.
        bad={'action_graph.yaml':'[]'}
        status,result=self.http('POST','/api/genres/custom/check',{**body,'files':bad})
        self.assertIn(status,(400,422),result)
        self.assertEqual(before,self.snap('custom'))
        self.assertEqual(world,(self.repo/'projects/momotaro/world.yaml').read_bytes())

    def test_metadata_and_raw_parse_roundtrip(self):
        snap=self.snap();path='genre.json'
        value={'name':'恋愛の文法','description':'二行\n説明','future':{'keep':True}}
        status,result=self.http('POST','/api/genres/romance/save',{'path':path,'content':json.dumps(value),'revision':snap['files'][path]['revision']})
        self.assertEqual(status,200,result)
        self.assertEqual(result['files'][path]['value'],value)
        status,result=self.http('POST','/api/genres/romance/parse',{'path':'rules.yaml','content':'- id: x\n  adjust: {custom.key: 0.2}\n'})
        self.assertEqual(status,200,result)
        self.assertEqual(result['value'][0]['adjust']['custom.key'],.2)
        self.assertEqual(self.http('POST','/api/genres/romance/parse',{'path':'../x','content':'[]'})[0],400)
        self.assertEqual(self.http('POST','/api/genres/romance/save',{'path':'rules.yaml','content':'[','revision':snap['files']['rules.yaml']['revision']})[0],400)

    def test_save_writes_yaml_not_json_but_keeps_raw_yaml_text_verbatim(self):
        snap=self.snap();path='rules.yaml'
        value=[{'id':'x','adjust':{'custom.key':0.2}}]
        body={'path':path,'content':json.dumps(value),'revision':snap['files'][path]['revision']}
        status,result=self.http('POST','/api/genres/romance/save',body)
        self.assertEqual(status,200,result)
        written=(self.repo/'templates/romance'/path).read_text(encoding='utf-8')
        with self.assertRaises(ValueError): json.loads(written)
        self.assertEqual(yaml.safe_load(written),value)
        raw='# a comment\n- id: y\n  adjust: {custom.key: 0.3}\n'
        body={'path':path,'content':raw,'revision':result['files'][path]['revision']}
        status,result=self.http('POST','/api/genres/romance/save',body)
        self.assertEqual(status,200,result)
        self.assertEqual((self.repo/'templates/romance'/path).read_text(encoding='utf-8'),raw)

    def test_markup_assets_escaping_and_missing_optional_sections(self):
        ge.create(LibraryStore(self.repo),'custom',name='<script>alert(1)</script>')
        status,body=self.get('/genres/custom')
        self.assertEqual(status,200)
        self.assertIn('data-genre-editor',body)
        self.assertNotIn('<script>alert(1)</script>',body)
        self.assertNotIn('data-genre-new',self.get('/genres/new')[1])
        for name in ('genre-workspace.js','genre-workspace.css'):
            self.assertEqual(self.get('/static/'+name)[0],200)
        self.assertEqual(self.http('POST','/api/genres/custom/check',{'world_id':'momotaro','files':{},'revision':'stale'})[0],409)
        self.assertEqual(self.http('POST','/api/genres/custom/save',{'path':'rules.yaml','content':'[]','revision':'missing'},client_header=False)[0],403)

if __name__=='__main__':unittest.main()
