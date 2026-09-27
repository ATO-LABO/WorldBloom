"""World workspace persistence, revision conflicts and HTTP boundaries."""
from copy import deepcopy
import json
import re
import unittest
from unittest.mock import patch

import yaml
import test_library as fixtures
from engine.world import World
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
        self.assertIn('data-world-advanced',html)
        self.assertIn('&quot;world.yaml&quot;',html)

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

    def test_genre_operation_sets_gapengine_and_preserves_other_keys(self):
        store = LibraryStore(self.repo)
        world = yaml.safe_load(store.read('world', 'momotaro', 'world.yaml'))
        world['gapengine']['antagonist_hint'] = 'kept'
        store.write('world', 'momotaro', 'world.yaml', yaml.safe_dump(world, allow_unicode=True, sort_keys=False))
        status, result = self.submit('genre', {'template_id': 'basic'})
        self.assertEqual(status, 200, result)
        saved = yaml.safe_load((self.repo / 'projects/momotaro/world.yaml').read_text(encoding='utf-8'))
        self.assertEqual(saved['gapengine']['action_graph'], 'templates/basic/action_graph.yaml')
        self.assertEqual(saved['gapengine']['effects'], 'templates/basic/effects.yaml')
        self.assertEqual(saved['gapengine']['antagonist_hint'], 'kept')

    def test_genre_operation_rejects_unknown_genre(self):
        before = self.current()['revision']
        status, error = self.submit('genre', {'template_id': 'no-such-genre'})
        self.assertEqual(status, 400, error)
        self.assertEqual(before, self.current()['revision'])

    def test_genre_operation_rejects_stale_revision(self):
        revision = self.current()['revision']
        self.assertEqual(self.submit('intro', {'text': '他の画面で更新'})[0], 200)
        status, error = self.submit('genre', {'template_id': 'basic'}, revision=revision)
        self.assertEqual(status, 409, error)

    def test_genre_operation_rejects_path_traversal_ids(self):
        # ".." resolves to store.repo itself under "templates" / "..", which
        # is_dir() alone would accept -- identifier() must reject it (and any
        # other non-identifier value) with a 400, not a 500 or a written
        # path-traversing gapengine value.
        before = self.current()['revision']
        for template_id in ('..', '', '../projects', '../templates/basic'):
            with self.subTest(template_id=template_id):
                status, error = self.submit('genre', {'template_id': template_id})
                self.assertEqual(status, 400, error)
        self.assertEqual(before, self.current()['revision'])
        world = self.current()['world']
        self.assertNotIn('..', yaml.safe_dump(world))

    def test_genre_operation_survives_non_dict_gapengine(self):
        # A world.yaml with a corrupt/non-mapping gapengine (e.g. imported
        # with "gapengine: something-else") must not 500 when a genre is
        # picked afterward; the corrupt value is simply replaced.
        store = LibraryStore(self.repo)
        world = yaml.safe_load(store.read('world', 'momotaro', 'world.yaml'))
        world['gapengine'] = 'not-a-mapping'
        store.write('world', 'momotaro', 'world.yaml', yaml.safe_dump(world, allow_unicode=True, sort_keys=False))
        status, result = self.submit('genre', {'template_id': 'basic'})
        self.assertEqual(status, 200, result)
        saved = yaml.safe_load((self.repo / 'projects/momotaro/world.yaml').read_text(encoding='utf-8'))
        self.assertEqual(saved['gapengine']['action_graph'], 'templates/basic/action_graph.yaml')

    def test_failed_atomic_save_preserves_original(self):
        store=LibraryStore(self.repo)
        body={'revision':self.current()['revision'],'operation':'intro','target':None,'values':{'text':'test'}}
        with patch('execution.library.os.replace',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):world_editor.save(store,'momotaro',body)
        self.assertEqual(body['revision'],self.current()['revision'])

    # WB-TIMEEVENT-001 (S2): scheduled_events.force_action editing.

    def test_scheduled_event_add_edit_remove_round_trip(self):
        # U-1
        subjects_before = {p: p.read_bytes() for p in (self.repo / 'projects/momotaro/subjects').glob('*.yaml')}
        status, result = self.submit('add-event', {
            'id': 'test_event', 'label': 'テスト', 'day': 1, 'slot': '朝',
            'targets': ['桃太郎'], 'verb': 'give_item', 'args': ['犬', ''],
            'item_name': '', 'item_count': 1, 'stress_delta': 0,
        })
        self.assertEqual(status, 200, result)
        events = self.current()['world']['scheduled_events']
        index = next(i for i, e in enumerate(events) if e['id'] == 'test_event')
        self.assertEqual(events[index]['force_action'], {'verb': 'give_item', 'args': ['犬']})
        self.assertNotIn('grants_item', events[index])
        self.assertNotIn('stress_delta', events[index])

        status, result = self.submit('event', {
            'id': 'test_event', 'label': '変更後', 'day': 2, 'slot': '朝',
            'targets': ['桃太郎'], 'verb': 'give_item', 'args': ['犬', ''],
            'item_name': '', 'item_count': 1, 'stress_delta': 0,
        }, index)
        self.assertEqual(status, 200, result)
        updated = next(e for e in self.current()['world']['scheduled_events'] if e['id'] == 'test_event')
        self.assertEqual(updated['label'], '変更後')
        self.assertEqual(updated['day'], 2)

        status, result = self.submit('remove-event', {}, index)
        self.assertEqual(status, 200, result)
        self.assertNotIn('test_event', [e.get('id') for e in self.current()['world']['scheduled_events']])
        for path, raw in subjects_before.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(World.from_yaml(self.repo / 'projects/momotaro/world.yaml').name, '桃太郎')

    def test_scheduled_event_edit_keeps_untouched_fields(self):
        # U-2 (part 1): departure_day keeps grants_item/stress_delta across an edit
        events = self.current()['world']['scheduled_events']
        index = next(i for i, e in enumerate(events) if e['id'] == 'departure_day')
        status, result = self.submit('event', {
            'id': 'departure_day', 'label': '変更後ラベル', 'day': 1, 'slot': '朝',
            'targets': ['桃太郎'], 'verb': 'move', 'args': ['道中'],
            'item_name': '縄', 'item_count': 1, 'stress_delta': 0.3,
        }, index)
        self.assertEqual(status, 200, result)
        updated = self.current()['world']['scheduled_events'][index]
        self.assertEqual(updated['label'], '変更後ラベル')
        self.assertEqual(updated['grants_item'], {'name': '縄', 'count': 1})
        self.assertEqual(updated['stress_delta'], 0.3)
        self.assertEqual(updated['force_action'], {'verb': 'move', 'args': ['道中']})

    def test_scheduled_event_edit_drops_move_to_when_verb_added(self):
        # U-2 (part 2): a move_to-only event loses move_to once a verb is set
        store = LibraryStore(self.repo)
        world = yaml.safe_load(store.read('world', 'momotaro', 'world.yaml'))
        world['scheduled_events'].append({
            'id': 'test_move_to_event', 'day': 1, 'slot': '朝', 'targets': ['桃太郎'],
            'label': '移動イベント', 'move_to': '森', 'stress_delta': 0.1,
        })
        store.write('world', 'momotaro', 'world.yaml', yaml.safe_dump(world, allow_unicode=True, sort_keys=False))
        events = self.current()['world']['scheduled_events']
        index = next(i for i, e in enumerate(events) if e['id'] == 'test_move_to_event')

        status, result = self.submit('event', {
            'id': 'test_move_to_event', 'label': '移動イベント', 'day': 1, 'slot': '朝',
            'targets': ['桃太郎'], 'verb': 'move', 'args': ['道中'],
            'item_name': '', 'item_count': 1, 'stress_delta': 0.1,
        }, index)
        self.assertEqual(status, 200, result)
        updated = self.current()['world']['scheduled_events'][index]
        self.assertNotIn('move_to', updated)
        self.assertEqual(updated['force_action'], {'verb': 'move', 'args': ['道中']})
        self.assertEqual(updated['stress_delta'], 0.1)

    def test_scheduled_event_invalid_requests_are_rejected(self):
        # U-3
        valid = {'id': 'test_bad', 'label': 'x', 'day': 1, 'slot': '朝', 'targets': ['桃太郎'],
                 'verb': '', 'args': [], 'item_name': '', 'item_count': 1, 'stress_delta': 0}
        before = self.current()['revision']
        cases = [
            {**valid, 'day': 9999},
            {**valid, 'slot': '深夜'},
            {**valid, 'targets': ['存在しない']},
            {**valid, 'verb': 'move', 'slot': ''},
            {**valid, 'verb': 'guard', 'args': []},
            {**valid, 'verb': 'move', 'args': ['道中', '余分']},
            {**valid, 'verb': 'give_item', 'args': ['', '犬']},
            {**valid, 'verb': 'move', 'args': ['未登録の場所']},
        ]
        for values in cases:
            with self.subTest(values=values):
                status, error = self.submit('add-event', values)
                self.assertEqual(status, 400, error)
        self.assertEqual(before, self.current()['revision'])

        status, error = self.submit('event', valid, 999)
        self.assertEqual(status, 400, error)
        status, error = self.submit('remove-event', {}, 999)
        self.assertEqual(status, 400, error)

        status, error = self.submit('add-event', {**valid, 'id': 'departure_day'})
        self.assertEqual(status, 400, error)
        self.assertEqual(before, self.current()['revision'])

        # Renaming an existing event (via the "event" operation) to another
        # event's id must also be rejected as a duplicate, not just add-event.
        status, result = self.submit('add-event', {**valid, 'id': 'second_event'})
        self.assertEqual(status, 200, result)
        events = self.current()['world']['scheduled_events']
        index = next(i for i, e in enumerate(events) if e['id'] == 'second_event')
        before = self.current()['revision']
        status, error = self.submit('event', {**valid, 'id': 'departure_day'}, index)
        self.assertEqual(status, 400, error)
        self.assertEqual(before, self.current()['revision'])
        self.assertEqual(self.current()['world']['scheduled_events'][index]['id'], 'second_event')

    def test_snapshot_and_render_include_force_action_specs(self):
        # U-4
        snap = self.current()
        self.assertEqual(snap['force_action_specs']['move'], ['zone'])
        status, html = self.get('/worlds/momotaro')
        self.assertEqual(status, 200, html)
        model = json.loads(re.search(r'id="wp-data">(.*?)</script>', html).group(1))
        self.assertEqual(model['force_action_specs']['move'], ['zone'])
        self.assertIn('scheduled_events', model['world'])


if __name__=='__main__': unittest.main()
