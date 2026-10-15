"""Local HTTP authoring -> actual simulation log -> owned fixed-response job."""
from copy import deepcopy
import http.client
import json
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from execution.fact_workspace import FactWorkspace
from execution.provenance import canonical, atomic_json, read_json, sha256, ConfigError
from execution.output_store import OutputStore
from execution.output_requests import normalize, admit
from viewer.server import ViewerServer, ViewerHandler
from viewer.data import RunRepository
from scripts.fact_runtime_demo import create_project, run, witness_commands
import test_fact_jobs as fact_job_fixtures


class WorkspaceFixture:
    def setUp(self):
        self.fx = fact_job_fixtures.FactJobTests('test_wrong_candidate_sha_version_and_conflicting_style_rejected_before_dispatch')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        project = create_project(self.fx.repo / 'projects/message-scene', lambda m: m['contexts'][0].update(value='SECRET_CAUSE_FOR_DISCLOSURE_TEST'))
        self.run = self.fx.configs.runs / 'actual-message-scene'
        runtime = run(project, self.run, witness_commands(combined=True), until=520)
        self.raw = (self.run / 'layers.jsonl').read_bytes()
        atomic_json(self.run / 'archive.json', {'cells': {'scene|explicit': {'generation': 0, 'quality': 0.0, 'reached': False,
                    'reach_rate': 0.0, 'exemplar': {'seed': 0, 'layers_path': 'layers.jsonl'}, 'genome': {}}}})
        self.rid = self.fx.catalog.register_legacy(self.run.name)
        self.cid = self.fx.catalog.candidates(self.rid)['candidates'][0]['candidate_id']
        self.cfg = 'cfg-message-scene'
        self.fx.configs.save({'label':'明示条件での通知と認識の試走', 'project_id':'message-scene', 'template_id':'romance',
            'evolution': {'generations':1, 'population':1, 'seeds':1}}, config_id=self.cfg)
        self.store = FactWorkspace(self.fx.configs, self.fx.catalog)
        ctx = self.store.context(self.rid, self.cid, self.cfg)
        selection = deepcopy(ctx['selection']); selection['selected'].remove('argument'); selection['hidden'] = ['argument']
        selection['required'] = ['F5-P', 'F5-E']
        self.body = {'config_id':self.cfg, 'selection':selection, 'styles':ctx['styles']}
        self.base = '/api/facts/runs/' + self.rid + '/' + self.cid
        self.page = '/facts/' + self.rid + '/' + self.cid + '?config=' + self.cfg

    def start_server(self):
        self.server = ViewerServer(('127.0.0.1', 0), ViewerHandler)
        self.server.repository = RunRepository(self.fx.configs.runs, control_root=self.fx.configs.control, jobs=self.fx.jobs)
        self.server.job_store = self.fx.jobs; self.server.settings_path = self.fx.settings_path
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def http(self, method, path, body=None, client=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=30)
        headers = {'Content-Type':'application/json'}
        if client: headers['X-WorldBloom-Client'] = '1'
        conn.request(method, path, None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8'), headers)
        response = conn.getresponse(); raw = response.read(); status = response.status
        content = response.getheader('Content-Type') or ''
        conn.close()
        return status, json.loads(raw) if 'application/json' in content else raw.decode('utf-8')


class FactWorkspaceTests(WorkspaceFixture, unittest.TestCase):
    def test_preview_is_non_mutating_and_hidden_record_never_reaches_prompts(self):
        before = {p.relative_to(self.store.facts.root).as_posix(): p.read_bytes() for p in self.store.facts.root.rglob('*') if p.is_file()}
        preview = self.store.preview(self.rid, self.cid, self.body)
        self.assertNotIn('SECRET', json.dumps(preview, ensure_ascii=False))
        self.assertFalse(self.store.root.exists())
        self.assertEqual(before, {p.relative_to(self.store.facts.root).as_posix(): p.read_bytes() for p in self.store.facts.root.rglob('*') if p.is_file()})
        self.assertIn('M1', json.dumps(preview['inputs'], ensure_ascii=False))
        self.assertEqual((self.run / 'layers.jsonl').read_bytes(), self.raw)

    def test_versions_reset_conflict_and_old_snapshot_are_preserved(self):
        first = self.store.save(self.rid, self.cid, self.body, 0)
        modified = deepcopy(self.body); modified['styles']['narrate'] = '短い文で静かに書く。'
        second = self.store.save(self.rid, self.cid, modified, 1)
        with self.assertRaises(ConfigError): self.store.save(self.rid, self.cid, modified, 1)
        third = self.store.save(self.rid, self.cid, self.body, 2)
        self.assertEqual(third['reference'], first['reference'])
        self.assertNotEqual(second['reference'], first['reference'])
        self.assertEqual(len(self.store.history(self.rid, self.cid)), 3)
        self.assertEqual(self.store.current(self.rid, self.cid, 1)['package']['styles'], self.body['styles'])
        with self.assertRaises(ConfigError): self.store.current(self.rid, self.cid, 4)
        self.assertEqual((self.run / 'layers.jsonl').read_bytes(), self.raw)

    def test_conflicting_styles_and_non_integer_revision_refuse_without_saving(self):
        bad = deepcopy(self.body); bad['styles']['narrate'] = '未読でも理解したように書く。'
        preview = self.store.preview(self.rid, self.cid, bad)
        self.assertIsNone(preview['prompts']['narrate'])
        self.assertTrue(preview['conflicts']['narrate'])
        with self.assertRaises(ConfigError): self.store.save(self.rid, self.cid, bad, 0)
        self.assertIsNone(self.store.current(self.rid, self.cid))
        with self.assertRaises(ConfigError): self.store.save(self.rid, self.cid, self.body, True)


class FactWorkspaceHTTPTests(WorkspaceFixture, unittest.TestCase):
    def setUp(self):
        WorkspaceFixture.setUp(self)
        self.start_server()

    def test_editor_preview_save_history_readonly_and_csrf(self):
        status, html = self.http('GET', self.page)
        self.assertEqual(status, 200, html)
        self.assertIn('data-fact-style="narrate"', html)
        self.assertIn('fact-workspace.js', html)
        self.assertEqual(self.http('POST', self.base+'/save', {**self.body, 'expected_revision':0}, client=False)[0], 403)
        status, preview = self.http('POST', self.base+'/preview', self.body)
        self.assertEqual(status, 200, preview); self.assertNotIn('SECRET', json.dumps(preview, ensure_ascii=False))
        status, first = self.http('POST', self.base+'/save', {**self.body, 'expected_revision':0})
        self.assertEqual(status, 200, first)
        self.assertEqual(self.http('POST', self.base+'/save', {**self.body, 'expected_revision':0})[0], 409)
        modified = deepcopy(self.body); modified['styles']['narrate'] = '新しい文体の目印。'
        self.assertEqual(self.http('POST', self.base+'/save', {**modified, 'expected_revision':1})[0], 200)
        status, old = self.http('GET', self.page+'&revision=1')
        self.assertEqual(status, 200, old); self.assertIn('閲覧のみ', old)
        self.assertNotIn('新しい文体の目印', old)
        self.assertEqual(self.http('GET', '/static/fact-workspace.js')[0], 200)
        self.assertEqual(self.http('GET', '/static/fact-workspace.css')[0], 200)

    def test_lost_acceptance_response_reuses_completed_job_and_explicit_regeneration_is_new(self):
        import socket
        self.fx.fixed_transport()
        saved = self.store.save(self.rid, self.cid, self.body, 0)
        request = {'expected_revision': 1, 'package_sha256': saved['reference']['package_sha256'],
                   'kind': 'narrate', 'request_id': 'req-lost-acceptance'}
        accepted = []
        original_send = ViewerHandler._send_json

        def lose_first_response(handler, status, data):
            if handler.path == self.base + '/generate' and not accepted:
                accepted.append(data)
                handler.close_connection = True
                handler.connection.shutdown(socket.SHUT_RDWR)
                return
            original_send(handler, status, data)

        with patch('execution.output_requests.generation_availability', return_value={'available': True}):
            with patch.object(ViewerHandler, '_send_json', lose_first_response):
                with self.assertRaises(http.client.RemoteDisconnected):
                    self.http('POST', self.base + '/generate', request)
            end = self.fx.finish(accepted[0]['job'])
            self.assertEqual(end['completion_kind'], 'generated', end)
            status, replay = self.http('POST', self.base + '/generate', request)
            self.assertEqual(status, 200, replay)
            self.assertFalse(replay['created'])
            self.assertEqual(replay['job']['job_id'], accepted[0]['job']['job_id'])
            outputs = OutputStore(self.fx.configs.control)
            self.assertEqual(len(read_json(outputs.folder(end['output_id']) / 'quota.json')['started']), 1)
            self.assertEqual(len(self.fx.jobs.list()), 1)
            # A new request ID means the author deliberately asks for another result.
            status, another = self.http('POST', self.base + '/generate', {**request, 'request_id': 'req-intentional-new'})
            self.assertEqual(status, 200, another)
            self.assertTrue(another['created'])
            self.assertNotEqual(another['job']['job_id'], replay['job']['job_id'])
            self.assertEqual(self.fx.finish(another['job'])['completion_kind'], 'generated')
            self.assertEqual(len(self.fx.jobs.list()), 2)

    def test_actual_simulation_to_http_fixed_response_job_and_historical_output(self):
        self.fx.fixed_transport()
        first = self.store.save(self.rid, self.cid, self.body, 0)
        request = {'expected_revision':1, 'package_sha256':first['reference']['package_sha256'], 'kind':'narrate', 'request_id':'req-browser-boundary'}
        self.assertFalse(self.fx.catalog.candidates(self.rid)['candidates'][0]['screenable'])
        with patch('execution.output_requests.generation_availability', return_value={'available':True}):
            status, dispatched = self.http('POST', self.base+'/generate', request)
        self.assertEqual(status, 200, dispatched)
        self.assertEqual(dispatched['url'], '/jobs/' + dispatched['job']['job_id'])
        self.assertEqual(self.http('GET', dispatched['url'])[0], 200)
        end = self.fx.finish(dispatched['job'])
        self.assertEqual((end['state'], end['completion_kind']), ('succeeded', 'generated'), end)
        oid = dispatched['job']['output_id']
        endpoint = '/facts/outputs/' + oid + '/' + self.cid
        status, html = self.http('GET', endpoint)
        self.assertEqual(status, 200, html)
        self.assertIn('文章の意味：未検証', html)
        self.assertNotIn('SECRET', html)
        original = html
        modified = deepcopy(self.body); modified['styles']['narrate'] = '現在の設定だけの目印。'
        self.store.save(self.rid, self.cid, modified, 1)
        self.assertEqual(self.http('GET', endpoint)[1], original)
        status, entry = self.http('GET', '/outputs/' + oid + '?view=record')
        self.assertEqual(status, 200, entry)
        self.assertIn(endpoint, entry)
        status, default_entry = self.http('GET', '/outputs/' + oid)
        self.assertEqual(status, 200, default_entry)
        self.assertIn(endpoint, default_entry)
        self.assertIn('書き方と入力を編集する', default_entry)
        self.assertNotIn('mode=regenerate', default_entry)
        self.assertEqual((self.run / 'layers.jsonl').read_bytes(), self.raw)
        # Existing full-story admission still rejects an unfinished candidate.
        from execution.output_settings import resolve_generation
        old = normalize({'schema_version':1, 'kind':'narrate', 'request_id':'req-old-scene', 'config_id':self.cfg,
            'run_id':self.rid, 'selection_revision':0, 'candidate_ids':[self.cid], **resolve_generation(self.fx.settings_path), 'mode':'regenerate'})
        with patch('execution.output_requests.generation_availability', return_value={'available':True}), self.assertRaises(ConfigError):
            admit(self.fx.jobs, old, settings_path=self.fx.settings_path)

    def test_stale_source_and_unsaved_revision_cannot_dispatch(self):
        saved = self.store.save(self.rid, self.cid, self.body, 0)
        req = {'expected_revision':0, 'package_sha256':saved['reference']['package_sha256'], 'kind':'synopsize', 'request_id':'req-stale'}
        self.assertEqual(self.http('POST', self.base+'/generate', req)[0], 409)
        (self.run/'layers.jsonl').write_bytes(self.raw+b'\n')
        req['expected_revision'] = 1
        self.assertNotEqual(self.http('POST', self.base+'/generate', req)[0], 200)
        self.assertEqual(list(self.fx.jobs.root.glob('job-*')), [])
