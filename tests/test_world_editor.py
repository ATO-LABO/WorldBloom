"""World workspace persistence, revision conflicts and HTTP boundaries."""
from copy import deepcopy
import json
import re
import unittest
from unittest.mock import patch

import yaml
import test_library as fixtures
from execution.library import LibraryStore
from execution.provenance import ConfigError
from execution import world_editor


class WorldEditorTests(unittest.TestCase):
    setUp = fixtures.LibraryHttpBoundaryTests.setUp
    get = fixtures.LibraryHttpBoundaryTests.get
    http = fixtures.LibraryHttpBoundaryTests.http

    def current(self):
        return world_editor.snapshot(LibraryStore(self.repo), 'momotaro')

    def submit(self, operation, values, target=None, revision=None, **kwargs):
        return self.http('POST', '/api/worlds/momotaro/edit', {
            'revision': revision or self.current()['revision'], 'operation': operation,
            'target': target, 'values': values}, **kwargs)

    def test_normal_and_old_preview_show_saved_workspace_without_examples(self):
        status, saved = self.submit('overview', {'name':'桃の世界','text':'実際に保存した概要'})
        self.assertEqual(status,200,saved)
        self.assertEqual(self.submit('intro',{'text':'まだ旅に出る前。'})[0],200)
        before=self.current()['revision']
        for url in ('/worlds/momotaro','/worlds/momotaro?preview=editorial'):
            status, html=self.get(url)
            self.assertEqual(status,200,html)
            model=json.loads(re.search(r'id="wp-data">(.*?)</script>',html).group(1))
            self.assertEqual(model['overview'],'実際に保存した概要')
            self.assertEqual(model['intro'],'まだ旅に出る前。')
            self.assertTrue(model['editable'])
            self.assertNotIn('プレビュー用の例文',html)
            self.assertNotIn('元の世界設定には保存されません',html)
            self.assertIn('/worlds/momotaro?view=advanced',html)
            for screen in ('overview','people','places','story','time'):
                self.assertIn(f'data-screen="{screen}"',html)
        self.assertEqual(before,self.current()['revision'])
        status,html=self.get('/worlds/momotaro?view=advanced')
        self.assertEqual(status,200,html)
        self.assertIn('canon-table',html)
        self.assertIn('data-file="world.yaml"',html)

    def test_world_edits_preserve_unrelated_fields_and_subject_bytes(self):
        original=deepcopy(self.current()['world'])
        subjects={p:p.read_bytes() for p in (self.repo/'projects/momotaro/subjects').glob('*.yaml')}
        edits=[('overview',{'name':'新しい桃太郎','text':'概要'},None),
               ('intro',{'text':'始まり'},None),('time',{'days':8,'slots':['朝','夜']},None),
               ('place',{'note':'新しい村の説明'},'村'),
               ('route',{'item':'船','cost':2.5},{'from':'海','index':1}),
               ('add-place',{'name':'港','description':'新しい港'},None)]
        for operation,values,target in edits:
            status,result=self.submit(operation,values,target)
            self.assertEqual(status,200,result)
            self.assertEqual(result,self.current())
        world=self.current()['world']
        for key in original:
            if key not in ('name','overview','initial_story','time','zones','routes'):
                self.assertEqual(original[key],world[key],key)
        self.assertEqual(world['time']['days'],8)
        self.assertEqual(world['zones'][0]['note'],'新しい村の説明')
        self.assertEqual(world['zones'][-1],{'name':'港','note':'新しい港'})
        self.assertEqual(world['routes']['海'][1]['cost'],2.5)
        for path,raw in subjects.items():self.assertEqual(path.read_bytes(),raw)

    def test_person_goal_and_initial_state_survive_reload_without_losing_engine_fields(self):
        original=next(p for p in self.current()['people'] if p['id']=='桃太郎')
        world_bytes=(self.repo/'projects/momotaro/world.yaml').read_bytes()
        status,result=self.submit('person',{'description':'桃の子','personality':'勇敢','target':'勾玉','deliver_to':'村'},'桃太郎')
        self.assertEqual(status,200,result)
        status,result=self.submit('state',{'entry':'海','knowledge':['造船術','航海']},'桃太郎')
        self.assertEqual(status,200,result)
        person=next(p for p in self.current()['people'] if p['id']=='桃太郎')
        self.assertEqual(person['goal'],{**original['goal'],'target':'勾玉','deliver_to':'村'})
        self.assertEqual(person['range'],{**original['range'],'entry':'海'})
        self.assertEqual(person['knowledge'],['造船術','航海'])
        for key in original:
            if key not in ('description','personality','goal','range','knowledge'):
                self.assertEqual(person[key],original[key],key)
        self.assertEqual((self.repo/'projects/momotaro/world.yaml').read_bytes(),world_bytes)
        self.assertEqual(self.submit('state',{'entry':'','knowledge':[]},'桃太郎')[0],200)
        person=next(p for p in self.current()['people'] if p['id']=='桃太郎')
        self.assertNotIn('entry',person['range'])

    def test_added_person_has_engine_defaults_and_duplicate_is_rejected(self):
        status,result=self.submit('add-person',{'name':'旅人','description':'遠くから来た人','entry':'村'})
        self.assertEqual(status,200,result)
        person=next(p for p in self.current()['people'] if p['id']=='旅人')
        self.assertEqual(person['base'],50)
        self.assertEqual(len(person['traits']),5)
        self.assertIn('村',person['range']['zones'])
        before=self.current()['revision']
        self.assertEqual(self.submit('add-person',{'name':'旅人','description':'','entry':'村'})[0],400)
        self.assertEqual(before,self.current()['revision'])
        # Actual configuration preparation uses these stored definitions.
        preview=self.server.job_store.configs.preview({'label':'test','project_id':'momotaro','template_id':'momotaro'})
        self.assertIn('preview',preview)

    def test_conflict_after_other_editor_prevents_overwrite(self):
        revision=self.current()['revision']
        self.assertEqual(self.submit('intro',{'text':'他の画面で更新'})[0],200)
        before=self.current()['revision']
        status,error=self.submit('intro',{'text':'古い画面からの更新'},revision=revision)
        self.assertEqual(status,409,error)
        self.assertEqual(self.current()['intro'],'他の画面で更新')
        self.assertEqual(before,self.current()['revision'])
        revision=before
        self.assertEqual(self.http('POST','/api/worlds/momotaro/basics',{'overview':'旧エディタの更新'})[0],200)
        self.assertEqual(self.submit('intro',{'text':'競合'},revision=revision)[0],409)

    def test_invalid_requests_never_change_yaml(self):
        before=self.current()['revision']
        requests=[('time',{'days':True,'slots':['朝']},None),
                  ('time',{'days':1,'slots':[]},None),('time',{'days':1,'slots':['朝','朝']},None),
                  ('state',{'entry':'不明','knowledge':[]},'桃太郎'),
                  ('state',{'entry':'村','knowledge':'string'},'桃太郎'),
                  ('person',{'description':'x','personality':'','target':'','deliver_to':''},'../other'),
                  ('place',{'note':'x'},['村']),('add-place',{'name':'村','description':''},None),
                  ('route',{'item':'','cost':0},{'from':'海','index':1}),
                  ('route',{'item':'','cost':1},{'from':'海','index':-1}),
                  ('intro',{'text':'x','world':{}},None)]
        for operation,values,target in requests:
            with self.subTest(operation=operation,values=values):
                status,error=self.submit(operation,values,target)
                self.assertEqual(status,400,error)
                self.assertEqual(before,self.current()['revision'])

    def test_http_boundary_and_readonly_deny_saves(self):
        before=self.current()['revision']
        self.assertEqual(self.submit('intro',{'text':'forbidden'},client_header=False)[0],403)
        with patch.object(self.server,'job_store',None),patch('viewer.library_pages.data.ROOT',self.repo):
            status,html=self.get('/worlds/momotaro')
            self.assertEqual(status,200,html)
            model=json.loads(re.search(r'id="wp-data">(.*?)</script>',html).group(1))
            self.assertFalse(model['editable'])
            self.assertIn('閲覧モード',html)
            self.assertEqual(self.submit('intro',{'text':'readonly'})[0],503)
        self.assertEqual(before,self.current()['revision'])

    def test_duplicate_person_id_is_rejected_without_guessing_filename(self):
        path=self.repo/'projects/momotaro/subjects/duplicate.yaml'
        path.write_text('id: 桃太郎\n',encoding='utf-8')
        before=self.current()['revision']
        status,error=self.submit('state',{'entry':'村','knowledge':[]},'桃太郎')
        self.assertEqual(status,400,error)
        self.assertEqual(before,self.current()['revision'])

    def test_failed_atomic_save_preserves_original(self):
        store=LibraryStore(self.repo)
        body={'revision':self.current()['revision'],'operation':'intro','target':None,'values':{'text':'test'}}
        with patch('execution.library.os.replace',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):world_editor.save(store,'momotaro',body)
        self.assertEqual(body['revision'],self.current()['revision'])


if __name__=='__main__': unittest.main()
