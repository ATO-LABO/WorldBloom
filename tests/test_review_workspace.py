"""Remaining workspace contracts: read-only, provenance, recovery, and revisioned edits."""
import hashlib
import html
import http.client
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

import test_viewer as fixture
import test_library as library_fixture
import test_workbench_pages as workbench_fixture
from execution import world_editor
from execution.library import LibraryStore
from execution.provenance import ConfigError
from viewer import data, library_pages, pages, review_pages, raw_pages, raw_view


class ReadOnlyWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.runs=Path(self.tmp.name)/'runs';self.exp=fixture._create_experiment(self.runs)
        self.repo=data.RunRepository(self.runs)
        archive=self.repo.archive(self.exp);archive['cells']['I|low']=json.loads(json.dumps(next(iter(archive['cells'].values()))));(self.exp/'archive.json').write_text(json.dumps(archive),encoding='utf-8')

    def test_all_reading_pages_have_no_mutating_controls_or_file_changes(self):
        before={str(p):p.read_bytes() for p in self.exp.rglob('*') if p.is_file()}
        cells=list(self.repo.archive(self.exp)['cells'])
        rendered=[pages.experiment_page(self.repo,self.exp.name),pages.cell_page(self.repo,self.exp.name,cells[0],view='digest'),pages.compare_page(self.repo,self.exp.name,cells[:2])]
        for content in rendered:
            self.assertEqual(content.count('class="ux-shell'),1)
            self.assertNotIn('selection-toggle',content)
            self.assertNotIn('data-candidate-binding',content)
            self.assertNotIn('data-reader-generate',content)
            self.assertNotIn('name="verdict"',content)
        self.assertIn('data-readonly-grid',rendered[0]);self.assertIn('<details class="ux-metrics">',rendered[0]);self.assertIn('占有マス',rendered[0]);self.assertIn('閲覧専用',rendered[2])
        after={str(p):p.read_bytes() for p in self.exp.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_invalid_compare_has_common_guidance_and_escape(self):
        content=pages.compare_page(self.repo,self.exp.name,[])
        self.assertIn('ux-guidance',content);self.assertIn('2〜4件',content)
        from viewer.error_pages import render
        content=render('/exp/test/cell/I%7Clow/raw',404,'<script>alert(1)</script>')
        self.assertNotIn('<script>alert',content)
        self.assertIn('/exp/test/cell/I%7Clow',content)

    def test_bound_source_keeps_exact_bytes_line_and_its_own_links(self):
        raw=b'\xef\xbb\xbf'+(' {"kind":"decision","subject":"a","verb":"move","args":["x"]} \r\n\r\n{"x":3}\n').encode()
        source=raw_view.source_bytes(raw,relative='g1/original.jsonl',experiment='exp',cell='I|low')
        self.assertEqual(source['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(source['records'][0].raw,' {"kind":"decision","subject":"a","verb":"move","args":["x"]} ')
        content=raw_pages.render_source(source,line='3',back_href='/runs/r/candidates?candidate=c',raw_href='/runs/r/candidates/c/raw')
        self.assertIn('action="/runs/r/candidates/c/raw"',content)
        self.assertIn('href="/runs/r/candidates?candidate=c"',content)
        self.assertNotIn('/exp/exp/cell/I%7Clow/raw',content)


class WorkspaceHttpTests(unittest.TestCase):
    setUp=workbench_fixture.WorkbenchTests.setUp
    _start_server=workbench_fixture.WorkbenchTests._start_server
    _cleanup_temp=workbench_fixture.WorkbenchTests._cleanup_temp
    _legacy_experiment=workbench_fixture.WorkbenchTests._legacy_experiment
    get_status=workbench_fixture.WorkbenchTests.get_status
    http=workbench_fixture.WorkbenchTests.http

    def test_page_errors_are_html_and_api_errors_keep_json(self):
        def unavailable(*args,**kwargs):raise ConfigError('output','作品がありません',code='not_found')
        self.fake.output=unavailable
        for url in ('/worlds/missing','/jobs/missing','/exp/missing','/outputs/missing'):
            status,content,headers=self.get_status(url)
            self.assertEqual(status,404,(url,content));self.assertIn('text/html',headers['Content-Type'])
            self.assertIn('ux-guidance',content)
        status,content,headers=self.get_status('/api/jobs/missing')
        self.assertEqual(status,404);self.assertIn('application/json',headers['Content-Type'])
        self.assertIn('code',json.loads(content))

    def test_published_raw_binds_candidate_and_detects_changed_log(self):
        root=self._legacy_experiment('binding');catalog=self.server.repository.catalog
        rid=catalog.register_legacy('binding');candidate=catalog.candidates(rid)['candidates'][0]
        cid=candidate['candidate_id'];url=f'/runs/{rid}/candidates/{cid}/raw'
        before=(root/candidate['log']['relative_path']).read_bytes()
        status,content,_=self.get_status(url+'?line=1')
        self.assertEqual(status,200,content);self.assertIn('data-record-viewer',content)
        self.assertIn(f'action="{url}"',content)
        self.assertEqual(before,(root/candidate['log']['relative_path']).read_bytes())
        (root/candidate['log']['relative_path']).write_text('{"changed":true}\n',encoding='utf-8')
        status,content,_=self.get_status(url)
        self.assertNotEqual(status,200);self.assertIn('ux-guidance',content)
        self.assertNotIn('data-rv-original',content)

    def test_bound_candidate_page_offers_verdict_and_rejects_changed_log(self):
        root=self._legacy_experiment('bound');catalog=self.server.repository.catalog
        rid=catalog.register_legacy('bound');candidate=catalog.candidates(rid)['candidates'][0]
        url='/exp/bound/cell/'+quote(candidate['cell_key'],safe='')
        status,content,_=self.get_status(url)
        self.assertEqual(status,200,content);self.assertEqual(content.count('class="ux-shell'),1)
        self.assertIn('data-candidate-binding',content);self.assertIn('name="verdict"',content)
        self.assertNotIn('閲覧専用',content)
        (root/candidate['log']['relative_path']).write_text('{"changed":true}\n',encoding='utf-8')
        status,content,_=self.get_status(url)
        self.assertNotEqual(status,200);self.assertIn('ux-guidance',content);self.assertNotIn('name="verdict"',content)


class AdvancedEditTests(unittest.TestCase):
    setUp=library_fixture.LibraryHttpBoundaryTests.setUp
    get=library_fixture.LibraryHttpBoundaryTests.get
    http=library_fixture.LibraryHttpBoundaryTests.http

    def current(self):return world_editor.snapshot(LibraryStore(self.repo),'momotaro')

    def submit(self,operation,values,target=None,revision=None):
        return self.http('POST','/api/worlds/momotaro/edit',{'revision':revision or self.current()['revision'],'operation':operation,'target':target,'values':values})

    def test_roles_preserve_world_and_reject_unknown_endings(self):
        before=self.current();values={'protagonist':'桃太郎','antagonist':'鬼','target_ending':['homecoming']}
        status,result=self.submit('roles',values);self.assertEqual(status,200,result)
        for key,value in before['world'].items():
            if key not in values:self.assertEqual(value,result['world'][key])
        revision=result['revision'];values['target_ending']=['made-up']
        self.assertEqual(self.submit('roles',values)[0],400)
        self.assertEqual(self.current()['revision'],revision)

    def test_file_edit_preserves_content_and_guards_revision_and_paths(self):
        before=self.current();path='subjects/03_momotaro.yaml';original=LibraryStore(self.repo).read('world','momotaro',path)
        content=original+'\n# preserve a user comment\n'
        status,result=self.submit('file',{'content':content},path);self.assertEqual(status,200,result)
        self.assertEqual(LibraryStore(self.repo).read('world','momotaro',path),content)
        self.assertEqual(self.submit('file',{'content':original},path,before['revision'])[0],409)
        self.assertEqual(self.submit('file',{'content':'id: bad'},'../escape.yaml')[0],400)
        self.assertEqual(self.submit('file',{'content':'[not a mapping]'},path)[0],400)
        self.assertEqual(LibraryStore(self.repo).read('world','momotaro',path),content)

    def test_advanced_page_has_real_canon_and_world_context(self):
        before=self.current()['revision'];status,content=self.get('/worlds/momotaro?view=advanced')
        self.assertEqual(status,200,content)
        model=json.loads(html.unescape(re.search('data-world-advanced data-initial="([^"]+)"',content).group(1)))
        self.assertEqual(model['world']['revision'],before)
        self.assertEqual(model['genre']['id'],'momotaro')
        self.assertIn('canon.yaml',model['genre']['files'])
        self.assertIn('character-readout',content)
        self.assertEqual(before,self.current()['revision'])


class PostApiDataExceptionTests(unittest.TestCase):
    """library_pages.dispatch: a data.* exception from a POST handler must
    come back as job_api JSON (not be re-raised into the HTML error page,
    which is only correct for GET)."""
    setUp=library_fixture.LibraryHttpBoundaryTests.setUp

    def raw_post(self,path,body):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        try:
            conn.request('POST',path,json.dumps(body),headers={'Content-Type':'application/json','X-WorldBloom-Client':'1'})
            response=conn.getresponse()
            return response.status,response.getheader('Content-Type'),response.read()
        finally:
            conn.close()

    def test_post_api_missing_resource_returns_job_api_json_not_html(self):
        body={'mode':'new','world_id':'ghost','name':'幽霊','overview':''}
        status,content_type,raw=self.raw_post('/api/worlds',body)
        self.assertEqual(status,201,raw)  # baseline: unpatched handler succeeds
        def boom(handler):library_pages._boundary_body(handler);raise data.MissingResource('ghost2')  # real handlers read the body first
        with patch.object(library_pages,'_create_world',boom):
            status,content_type,raw=self.raw_post('/api/worlds',{**body,'world_id':'ghost2'})
        self.assertEqual(status,404,raw)
        self.assertIn('application/json',content_type)
        self.assertIn('code',json.loads(raw))
