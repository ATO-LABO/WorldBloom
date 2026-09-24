"""Global settings views must not leak private settings or launch jobs."""
import html
import json
import re
import unittest
from unittest.mock import patch
import test_workbench_pages as fixtures
from execution.output_settings import write_api_key, write_output_settings

class GlobalSettingsTests(unittest.TestCase):
    setUp = fixtures.WorkbenchTests.setUp
    _cleanup_temp = fixtures.WorkbenchTests._cleanup_temp
    _start_server = fixtures.WorkbenchTests._start_server
    get_status = fixtures.WorkbenchTests.get_status
    http = fixtures.WorkbenchTests.http

    def initial(self, body):
        return json.loads(html.unescape(re.search(r'data-initial="([^"]+)"', body).group(1)))

    def test_private_settings_are_absent_and_no_job_starts(self):
        write_api_key(self.settings_path, "openai", "test-private-key-not-for-display")
        before = self.settings_path.read_bytes()
        status, body, _ = self.get_status('/configs')
        self.assertEqual(status, 200)
        self.assertNotIn('test-private-key-not-for-display', body)
        self.assertTrue(self.initial(body)['view']['backends']['openai']['has_api_key'])
        self.assertIn('文章生成は行いません', body)
        self.assertIn('data-gs-output', body)
        self.assertEqual(before, self.settings_path.read_bytes())
        self.assertEqual(self.fake.submitted, [])

    def test_saved_configuration_has_read_only_detail_and_duplicate_entry(self):
        before = self.configs.get('cfg-test')
        status, body, _ = self.get_status('/configs?tab=configs&item=cfg-test')
        self.assertEqual(status, 200)
        self.assertEqual(self.initial(body)['tab'], 'configs')
        self.assertIn('data-gs-panel="configs">', body)
        self.assertIn('data-gs-item="cfg-test"', body)
        self.assertIn('/configs/new?from=cfg-test', body)
        self.assertIn('/configs/cfg-test/start', body)
        self.assertIn('保存版は編集不可', body)
        self.assertEqual(before, self.configs.get('cfg-test'))
        self.assertEqual(self.fake.submitted, [])

    def test_genre_usage_and_existing_editor_are_accessible(self):
        status, body, _ = self.get_status('/configs?tab=genres&item=romance')
        self.assertEqual(status, 200)
        self.assertIn('data-gs-panel="genres">', body)
        self.assertIn('href="/genres/romance"', body)
        self.assertIn('使っている世界', body)
        self.assertIn('href="/worlds/romance"', body)

    def test_save_round_trip_is_reflected_without_config_mutation(self):
        before = self.configs.get('cfg-test')
        payload = {'backend':'none', 'model':None, 'limits':{'max_calls':3,'call_timeout_seconds':120,'wall_seconds':500,'max_saved_response_bytes':2048}}
        status, saved = self.http('POST', '/api/settings/output', payload)
        self.assertEqual(status, 200, saved)
        status, body, _ = self.get_status('/configs')
        initial = self.initial(body)['view']
        self.assertEqual(initial['limits'], payload['limits'])
        self.assertEqual(initial['backend'], 'none')
        self.assertEqual(before, self.configs.get('cfg-test'))
        self.assertEqual(self.fake.submitted, [])

    def test_broken_settings_and_empty_collections_keep_navigation(self):
        self.settings_path.write_text('{broken', encoding='utf-8')
        with patch('execution.library.LibraryStore.genres', return_value=[]), patch.object(self.configs, 'list', return_value=[]):
            status, body, _ = self.get_status('/configs?tab=bad')
        self.assertEqual(status, 200)
        self.assertEqual(self.initial(body)['tab'], 'output')
        self.assertIsNone(self.initial(body)['view'])
        self.assertIn('settings.json を読めません', body)
        self.assertNotIn('data-gs-output', body)
        # WB-COMPUTE-001: nav gained a 4th tab ("計算").
        self.assertEqual(body.count('data-gs-tab='), 4)
        self.assertIn('まだ登録されていません', body)

    def test_assets_are_served(self):
        for name, content_type in [('global-settings.css', 'text/css'), ('global-settings.js', 'application/javascript')]:
            status, body, headers = self.get_status('/static/'+name)
            self.assertEqual(status, 200)
            self.assertIn(content_type, headers['Content-Type'])
            self.assertTrue(body)

if __name__ == '__main__':
    unittest.main()
