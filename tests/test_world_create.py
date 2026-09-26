"""Original and copied worlds are published without mutating existing worlds."""
import base64
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch
import unittest
import yaml
import test_library as fixtures
from execution.library import MAX_ZIP_BYTES, LibraryStore
from execution.provenance import ConfigError

class WorldCreateTests(unittest.TestCase):
    setUp = fixtures.LibraryHttpBoundaryTests.setUp
    http = fixtures.LibraryHttpBoundaryTests.http
    get = fixtures.LibraryHttpBoundaryTests.get

    def create(self, **changes):
        body = {"mode":"new", "world_id":"original", "name":"星を運ぶ街", "overview":"星が地上へ降りる街。"}
        body.update(changes)
        return self.http('POST','/api/worlds',body)

    def test_original_is_empty_and_visible_without_changing_sources(self):
        before = {str(p.relative_to(self.repo)): p.read_bytes() for p in (self.repo/'projects').rglob('*.yaml')}
        status, payload = self.create()
        self.assertEqual(status,201,payload)
        path = self.repo/'projects/original/world.yaml'
        world = yaml.safe_load(path.read_text(encoding='utf-8'))
        self.assertEqual(world['name'],'星を運ぶ街')
        self.assertEqual(world['overview'],'星が地上へ降りる街。')
        self.assertEqual(world['zones'],[])
        self.assertEqual(world['initial_story'],'')
        self.assertEqual(world['protagonist'],'')
        self.assertEqual(world['time']['days'],7)
        self.assertEqual(list((path.parent/'subjects').iterdir()),[])
        for rel, original in before.items(): self.assertEqual((self.repo/rel).read_bytes(),original,rel)
        status, markup = self.get('/worlds/original')
        self.assertEqual(status,200)
        self.assertIn('星が地上へ降りる街。',markup)
        self.assertIn('data-screen="story"',markup)
        self.assertIn('data-world-prototype',markup)
        self.assertNotIn('data-quick-start',markup)
        with self.assertRaises(ConfigError):
            self.server.job_store.configs.preview({'label':'draft','project_id':'original','template_id':'basic'})

    def test_name_overview_and_intro_round_trip_preserves_other_settings(self):
        self.create()
        store=LibraryStore(self.repo)
        before=yaml.safe_load(store.read('world','original','world.yaml'))
        status,result=self.http('POST','/api/worlds/original/basics',{'name':'新しい名前','overview':'二行の概要\n続き'})
        self.assertEqual(status,200,result)
        status,result=self.http('POST','/api/worlds/original/basics',{'initial_story':'夜明け前、まだ誰もいない。'})
        self.assertEqual(status,200,result)
        after=yaml.safe_load(store.read('world','original','world.yaml'))
        for key in before:
            if key not in ('name','overview','initial_story'): self.assertEqual(before[key],after[key])
        self.assertEqual(after['overview'],'二行の概要\n続き')
        status,markup=self.get('/worlds/original')
        self.assertEqual(status,200)
        self.assertIn('新しい名前',markup)
        self.assertIn('夜明け前、まだ誰もいない。',markup)

    def test_original_needs_no_source_worlds(self):
        store=LibraryStore(self.repo)
        with patch.object(LibraryStore,'worlds',return_value=[]):
            status,markup=self.get('/worlds/new')
            self.assertEqual(status,200)
            self.assertIn('作成元の世界がまだありません',markup)
            self.assertEqual(self.create()[0],201)

    def test_incomplete_source_preview_does_not_block_original_creation(self):
        store = LibraryStore(self.repo)
        world = yaml.safe_load(store.read('world', 'momotaro', 'world.yaml'))
        world.update(time=3, zones=5)
        store.write('world', 'momotaro', 'world.yaml', yaml.safe_dump(world, allow_unicode=True))
        self.assertEqual(self.get('/worlds/new')[0], 200)
        with patch.object(LibraryStore, 'read', side_effect=ConfigError('world_id', 'missing', code='not_found')):
            self.assertEqual(self.get('/worlds/new')[0], 200)
        self.assertEqual(self.create()[0], 201)

    def test_legacy_and_explicit_copy_preserve_source(self):
        store=LibraryStore(self.repo)
        before=store.read('world','momotaro','world.yaml')
        for mode in (None,'copy'):
            wid='legacy' if mode is None else 'explicit'
            body={'world_id':wid,'name':'複製','from_world_id':'momotaro','template_id':'momotaro'}
            if mode: body['mode']=mode
            status,payload=self.http('POST','/api/worlds',body)
            self.assertEqual(status,201,payload)
            world=yaml.safe_load(store.read('world',wid,'world.yaml'))
            self.assertEqual(world['zones'],yaml.safe_load(before)['zones'])
            self.assertEqual(len(list((self.repo/f'projects/{wid}/subjects').glob('*.yaml'))),7)
        self.assertEqual(before,store.read('world','momotaro','world.yaml'))

    def test_duplicate_and_invalid_input_do_not_overwrite(self):
        self.create()
        path=self.repo/'projects/original/world.yaml'; before=path.read_bytes()
        self.assertEqual(self.create(name='上書き')[0],409)
        self.assertEqual(path.read_bytes(),before)
        for changes in ({'world_id':'../bad'},{'name':' '},{'overview':[]},{'overview':'a'*8001},{'mode':'other'},{'mode':'new','from_world_id':'momotaro'}):
            with self.subTest(changes=str(changes)[:80]):
                status,_=self.create(**changes)
                self.assertIn(status,(400,422))
        self.assertEqual(path.read_bytes(),before)

    def test_failure_never_publishes_partial_world(self):
        with patch('execution.library.yaml.safe_dump',side_effect=OSError('disk error')):
            with self.assertRaises(OSError): LibraryStore(self.repo).create_original_world('fail',name='失敗')
        self.assertFalse((self.repo/'projects/fail').exists())
        self.assertFalse(list(self.repo.glob('.world-create-*')))

    def test_new_endpoints_keep_write_boundary(self):
        status,_=self.http('POST','/api/worlds',{'mode':'new','world_id':'bad','name':'bad','overview':''},client_header=False)
        self.assertEqual(status,403)
        self.create()
        status,_=self.http('POST','/api/worlds/original/basics',{'name':'変更'},client_header=False)
        self.assertEqual(status,403)
        status,_=self.http('POST','/api/worlds/original/basics',{'zones':[]})
        self.assertEqual(status,400)

    def test_markup_escapes_text_and_direct_copy_mode(self):
        self.create(name='<img src=x onerror=alert(1)>',overview='<script>alert(1)</script>')
        status,markup=self.get('/worlds/new?from=original')
        self.assertEqual(status,200)
        self.assertIn('name="mode" value="copy" checked',markup)
        self.assertNotIn('<img src=x',markup)
        self.assertNotIn('<script>alert(1)',markup)
        status,markup=self.get('/worlds/new')
        self.assertIn('name="mode" value="new" checked',markup)
        for asset in ('world-create.js','world-create.css'):
            self.assertEqual(self.get('/static/'+asset)[0],200)

    def test_copy_mode_notes_that_expansions_are_not_duplicated(self):
        # WB-WORLDGROW-001 段階5d: patches/ is excluded from a world copy
        # (execution/library.py's LibraryStore._publish_world), so the copy
        # screen says so and points at the genre-asset route back in instead.
        self.create()
        status, markup = self.get('/worlds/new?from=original')
        self.assertEqual(status, 200)
        self.assertIn('拡張は複製されません。ジャンルの資産から取り込めます。', markup)

    def test_basic_rules_accept_a_configured_original(self):
        self.create()
        store=LibraryStore(self.repo)
        world=yaml.safe_load(store.read('world','original','world.yaml'))
        world.update(protagonist='旅人',antagonist='門番',zones=[{'name':'広場'}],ending=[{'id':'arrival','when':"zone(旅人) == '広場'"}],target_ending=['arrival'])
        store.write('world','original','world.yaml',yaml.safe_dump(world,allow_unicode=True))
        for idx,name in enumerate(('旅人','門番')):
            person={'id':name,'range':{'entry':'広場','zones':['広場']},'base':50,'traits':dict.fromkeys(('social','stubbornness','curiosity','diligence','temper'),.5)}
            store.write('world','original',f'subjects/person{idx}.yaml',yaml.safe_dump(person,allow_unicode=True))
        result=self.server.job_store.configs.preview({'label':'configured','project_id':'original','template_id':'basic'})
        self.assertEqual(result['preview']['world_name'],'星を運ぶ街')
        self.assertEqual(result['preview']['protagonist'],'旅人')

def _zip_b64(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content.encode('utf-8'))
    return base64.b64encode(buffer.getvalue()).decode('ascii')


class WorldImportTests(unittest.TestCase):
    """WB-WORLD-IMPORT-001 U2: POST /api/worlds mode=import."""
    setUp = fixtures.LibraryHttpBoundaryTests.setUp
    http = fixtures.LibraryHttpBoundaryTests.http
    get = fixtures.LibraryHttpBoundaryTests.get

    def import_body(self, **changes):
        body = {'mode': 'import', 'world_id': 'imported', 'name': '',
                'zip_base64': _zip_b64({'world.yaml': 'name: ZIPの世界'})}
        body.update(changes)
        return body

    def test_import_creates_world_from_zip(self):
        status, payload = self.http('POST', '/api/worlds', self.import_body())
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload['world_id'], 'imported')
        store = LibraryStore(self.repo)
        world = yaml.safe_load(store.read('world', 'imported', 'world.yaml'))
        self.assertEqual(world['name'], 'ZIPの世界')
        self.assertNotIn('gapengine', world)

    def test_import_rejects_unexpected_keys(self):
        body = self.import_body()
        body['extra'] = 'x'
        status, payload = self.http('POST', '/api/worlds', body)
        self.assertEqual(status, 400, payload)

    def test_import_rejects_invalid_base64(self):
        status, payload = self.http('POST', '/api/worlds', self.import_body(zip_base64='not-base64!!'))
        self.assertEqual(status, 400, payload)
        self.assertIn('zip_base64', payload['field_errors'])

    def test_import_rejects_zip_over_limit(self):
        oversized = 'a' * (MAX_ZIP_BYTES + 1)
        status, payload = self.http('POST', '/api/worlds', self.import_body(zip_base64=base64.b64encode(oversized.encode()).decode('ascii')))
        self.assertEqual(status, 400, payload)
        self.assertIn('zip_base64', payload['field_errors'])

    def test_import_requires_client_header(self):
        status, payload = self.http('POST', '/api/worlds', self.import_body(), client_header=False)
        self.assertEqual(status, 403, payload)
        self.assertFalse((self.repo / 'projects/imported').exists())

    def test_new_form_offers_import_mode(self):
        status, markup = self.get('/worlds/new')
        self.assertEqual(status, 200, markup)
        self.assertIn('name="mode" value="import"', markup)
        self.assertIn('data-zip-input', markup)

    def test_import_without_genre_then_pick_one_via_edit_then_preview_succeeds(self):
        # End-to-end: import a genre-less ZIP over HTTP, pick a genre via the
        # world-settings "genre" edit operation, then confirm the resulting
        # world.yaml actually drives a successful ConfigStore.preview() --
        # not just that the two writes individually look right.
        base = self.repo / 'projects/momotaro'
        entries = {'world.yaml': (base / 'world.yaml').read_text(encoding='utf-8')}
        for path in sorted((base / 'subjects').glob('*.yaml')):
            entries[f'subjects/{path.name}'] = path.read_text(encoding='utf-8')
        world = yaml.safe_load(entries['world.yaml'])
        del world['gapengine']
        entries['world.yaml'] = yaml.safe_dump(world, allow_unicode=True, sort_keys=False)

        status, payload = self.http('POST', '/api/worlds', self.import_body(
            world_id='e2e-genre-pick', zip_base64=_zip_b64(entries)))
        self.assertEqual(status, 201, payload)
        store = LibraryStore(self.repo)
        self.assertIsNone(store._genre_of('e2e-genre-pick', yaml.safe_load(store.read('world', 'e2e-genre-pick', 'world.yaml'))))

        from execution.world_editor import snapshot
        revision = snapshot(store, 'e2e-genre-pick')['revision']
        status, result = self.http('POST', '/api/worlds/e2e-genre-pick/edit', {
            'revision': revision, 'operation': 'genre', 'target': None, 'values': {'template_id': 'momotaro'}})
        self.assertEqual(status, 200, result)

        saved = yaml.safe_load(store.read('world', 'e2e-genre-pick', 'world.yaml'))
        self.assertEqual(saved['gapengine']['action_graph'], 'templates/momotaro/action_graph.yaml')
        self.assertEqual(saved['gapengine']['effects'], 'templates/momotaro/effects.yaml')

        preview = self.server.job_store.configs.preview(
            {'label': 'e2e', 'project_id': 'e2e-genre-pick', 'template_id': 'momotaro'})
        self.assertEqual(preview['preview']['world_name'], '桃太郎')


if __name__=='__main__': unittest.main()
