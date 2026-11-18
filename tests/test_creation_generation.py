from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4
import json, shutil, threading, unittest

from execution.draft_generation_status import settings_status, availability
from execution.world_drafts import DraftStore, existing_provider, mock_response
from execution.generation import BoundaryError
from execution.provenance import ConfigError
from gapengine.gpu_guard import GpuBusy

ROOT = Path(__file__).resolve().parents[1]


class CreationGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix='wb-first-input-'); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.repo = self.base / 'repo'
        shutil.copytree(ROOT / 'templates/basic', self.repo / 'templates/basic')
        self.settings = self.base / 'dedicated.json'
        self.draft = json.loads(mock_response('検証', '二人の物語'))
        self.calls = []
        def generate(*args):
            self.calls.append('generate'); return json.dumps(self.draft, ensure_ascii=False)
        def review(*args):
            self.calls.append('review')
            return {'overview_matches': True, 'goals': [{'person': p['name'], 'matches': True, 'reason': '検証用の固定評価'} for p in self.draft['people']], 'issues': []}
        generate.with_context = generate
        generate.review_with_context = review
        generate.review = review
        self.provider = generate
        self.store = DraftStore(self.repo, self.base / 'control', generate, 'live')
        self.authoring = {'version': 1, 'type': 'threat-response', 'outline': {'genre': '任意', 'introduction': '保持する旧導入'}}

    def write(self, value):
        self.settings.write_text(json.dumps(value), encoding='utf-8')

    def test_configuration_does_not_probe_or_rewrite(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('No network')), patch('subprocess.Popen', side_effect=AssertionError('No process')):
            self.assertEqual(settings_status(self.settings)['status'], 'unconfigured')
            self.write({'output': {'default_backend': 'none'}})
            self.assertEqual(settings_status(self.settings)['status'], 'unconfigured')
            self.write({'output': {'default_backend': 'ollama', 'ollama': {'model': 'dedicated-model'}}})
            original = self.settings.read_bytes()
            p = existing_provider(self.settings)
            result = availability(DraftStore(self.repo, self.base / 'other', p, 'live'))
            self.assertEqual(result['status'], 'configured'); self.assertEqual(result['model'], 'dedicated-model')
            self.assertFalse(result['connection_checked']); self.assertEqual(original, self.settings.read_bytes())
            self.assertNotIn('api_key', json.dumps(result))

    def test_missing_and_malformed_are_distinct(self):
        for raw in ['{ broken', '[]', '{"output":[]}', '{"output":{"default_backend":[]}}', '{"output":{"default_backend":"ollama","ollama":{"model":3}}}']:
            self.settings.write_text(raw, encoding='utf-8')
            self.assertEqual(settings_status(self.settings)['status'], 'invalid_settings', raw)
        for value in [{'output': {'default_backend': 'ollama'}}, {'output': {'default_backend': 'openai', 'openai': {'model': 'x'}}}]:
            self.write(value); self.assertEqual(settings_status(self.settings)['status'], 'unconfigured')

    def test_actual_provider_contract_not_other_settings_or_prompt_only_availability(self):
        self.assertEqual(availability(DraftStore(self.repo, self.base / 'waiting'))['status'], 'unconfigured')
        self.assertEqual(availability(self.store)['status'], 'configuration_unknown')
        self.provider.configuration_status = lambda: {'status': 'configured', 'model': 'actual'}
        self.assertEqual(availability(self.store)['model'], 'actual')
        self.provider.busy = lambda: True
        self.assertEqual(availability(self.store)['status'], 'busy')
        guarded = lambda *args: None
        guarded.review = Mock()
        self.assertEqual(availability(DraftStore(self.repo, self.base / 'guarded', guarded, 'live'))['status'], 'unsupported_provider')

    def test_success_idempotence_and_context_settings(self):
        token = uuid4().hex
        settings = {'version': 1, 'time': {'days': 13, 'slots': ['午前', '午後']}}
        result = self.store.create(token, '人の名前', '人の概要', settings=settings, authoring=self.authoring)
        self.assertEqual(result['state'], 'review'); self.assertEqual(self.calls, ['generate', 'review'])
        self.assertEqual(result['draft']['name'], '人の名前'); self.assertEqual(result['draft']['overview'], '人の概要')
        self.assertEqual(result['draft']['settings']['time']['days'], 13)
        self.assertEqual(result['authoring']['outline']['introduction'], '保持する旧導入')
        self.store.create(token, '人の名前', '人の概要', settings=settings, authoring=self.authoring)
        self.assertEqual(self.calls, ['generate', 'review'])
        with self.assertRaises(ConfigError): self.store.create(token, '変更', '人の概要', settings=settings, authoring=self.authoring)
        self.assertFalse((self.repo / 'projects').exists())

    def test_review_not_finished_is_not_success_and_second_request_does_not_replay(self):
        entered = threading.Event(); release = threading.Event(); results = []
        original = self.provider.review_with_context
        def delayed(*args):
            entered.set(); release.wait(5); return original(*args)
        self.provider.review_with_context = delayed
        token = uuid4().hex
        thread = threading.Thread(target=lambda: results.append(self.store.create(token, '検証', '二人の物語', authoring=self.authoring)))
        thread.start(); self.addCleanup(lambda: (release.set(), thread.join(6)))
        self.assertTrue(entered.wait(5)); self.assertEqual(self.store.get(token)['state'], 'generating')
        self.assertEqual(self.store.create(token, '検証', '二人の物語', authoring=self.authoring)['state'], 'generating')
        self.assertEqual(self.calls, ['generate']); release.set(); thread.join(6)
        self.assertEqual(results[0]['state'], 'review'); self.assertEqual(self.calls, ['generate', 'review'])

    def test_invalid_generation_and_review_failure_do_not_report_ready(self):
        self.draft['people'] = []
        result = self.store.create(uuid4().hex, '検証', '二人の物語', authoring=self.authoring)
        self.assertEqual(result['state'], 'invalid'); self.assertEqual(result['failure_reason'], 'invalid_draft')
        self.assertEqual(self.calls, ['generate'])
        self.draft = json.loads(mock_response('検証', '二人の物語'))
        self.provider.review_with_context = lambda *args: (_ for _ in ()).throw(ConfigError('alignment', '検査失敗'))
        result = self.store.create(uuid4().hex, '検証', '二人の物語', authoring=self.authoring)
        self.assertEqual(result['state'], 'invalid'); self.assertEqual(result['failure_reason'], 'review_failed')

    def test_transport_gpu_and_review_unknown_are_not_missing_settings(self):
        for error, reason in [(GpuBusy(), 'gpu_busy'), (BoundaryError('transport_timeout'), 'transport_timeout'), (BoundaryError('transport_disconnected'), 'transport_disconnected'), (BoundaryError('launch_failed'), 'launch_failed')]:
            def fail(*args): raise error
            self.provider.with_context = fail
            token = uuid4().hex
            result = self.store.create(token, '検証', '二人の物語', authoring=self.authoring)
            self.assertEqual(result['state'], 'unknown'); self.assertEqual(result['failure_reason'], reason)
            self.store.create(token, '検証', '二人の物語', authoring=self.authoring)
        self.assertFalse((self.repo / 'projects').exists())

    def test_get_route_is_read_only(self):
        from viewer.library_pages import _resolve
        self.assertEqual(_resolve(['api', 'world-draft-generation'], 'GET')[0].__name__, '_world_draft_generation_status')
        self.assertIsNone(_resolve(['api', 'world-draft-generation'], 'POST'))


import test_world_drafts as http_fixtures


class CreationGenerationHTTP(unittest.TestCase):
    setUp = http_fixtures.DraftHTTPTests.setUp
    shutdown = http_fixtures.DraftHTTPTests.shutdown
    request = http_fixtures.DraftHTTPTests.request

    def test_actual_status_route_asset_and_no_generation_or_writes(self):
        before = {str(p.relative_to(self.control)): p.read_bytes() for p in self.control.rglob('*') if p.is_file()}
        status, raw = self.request('/api/world-draft-generation')
        self.assertEqual(status, 200); self.assertTrue(json.loads(raw)['mock'])
        self.store.mode = 'waiting'
        status, raw = self.request('/api/world-draft-generation')
        self.assertEqual(status, 200); self.assertEqual(json.loads(raw)['status'], 'unconfigured')
        self.assertEqual(self.request('/api/world-draft-generation', {})[0], 404)
        self.assertEqual(self.request('/static/creation-generation.js')[0], 200)
        self.assertEqual(self.calls, 0)
        self.assertEqual(before, {str(p.relative_to(self.control)): p.read_bytes() for p in self.control.rglob('*') if p.is_file()})

    def test_settings_return_target_is_same_origin_validated_workspace(self):
        status, raw = self.request('/configs?tab=output&creation_workspace=' + 'a' * 32)
        self.assertEqual(status, 200)
        self.assertIn(('workspace=' + 'a' * 32).encode(), raw)
        self.assertIn('保存した下書きの①へ戻る'.encode(), raw)
        status, raw = self.request('/configs?creation_workspace=https%3A%2F%2Fevil.invalid')
        self.assertEqual(status, 200); self.assertNotIn('保存した下書きの①へ戻る'.encode(), raw)
        self.assertEqual(self.calls, 0)
