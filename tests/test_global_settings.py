"""Global settings views must not leak private settings or launch jobs."""
import html
import json
import re
import unittest
from unittest.mock import patch
import test_workbench_pages as fixtures
from execution.output_settings import write_api_key, write_output_settings
from viewer.global_settings import collection_panel, compute_panel

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

    def test_old_genres_tab_falls_back_to_ai_settings(self):
        status, body, _ = self.get_status('/configs?tab=genres&item=romance')
        self.assertEqual(status, 200)
        self.assertEqual(self.initial(body)['tab'], 'output')
        self.assertNotIn('data-gs-tab="genres"', body)
        self.assertNotIn('data-gs-panel="genres"', body)
        self.assertIn('data-gs-panel="output">', body)

    def test_jev_tab_is_in_ai_settings_only(self):
        status, body, _ = self.get_status('/configs?tab=output')
        self.assertEqual(status, 200)
        ai, compute = body.split('data-gs-panel="compute"', 1)
        self.assertIn('aria-controls="gs-panel-jev"', ai)
        self.assertIn('data-gs-jev-key-save', ai)
        self.assertNotIn('data-gs-jev-key-save', compute)

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
        with patch.object(self.configs, 'list', return_value=[]):
            status, body, _ = self.get_status('/configs?tab=bad')
        self.assertEqual(status, 200)
        self.assertEqual(self.initial(body)['tab'], 'output')
        self.assertIsNone(self.initial(body)['view'])
        self.assertIn('settings.json を読めません', body)
        self.assertNotIn('data-gs-output', body)
        self.assertEqual(body.count('data-gs-tab='), 3)
        self.assertIn('まだ保存版がありません', body)

    def test_assets_are_served(self):
        for name, content_type in [('global-settings.css', 'text/css'), ('global-settings.js', 'application/javascript')]:
            status, body, headers = self.get_status('/static/'+name)
            self.assertEqual(status, 200)
            self.assertIn(content_type, headers['Content-Type'])
            self.assertTrue(body)

class SettingsTabRenderTests(unittest.TestCase):
    def test_compute_tabs_share_one_numeric_save(self):
        settings = dict(processes=2, cpu_count=8, default=4, thermal_enabled=True,
                        pause_at=80, pause_min=60, pause_max=95, pause_default=85)
        body = compute_panel(settings, False)
        self.assertEqual(body.count('class="ui-tabs"'), 1)
        self.assertIn('aria-controls="gs-compute-panel-parallel"', body)
        self.assertIn('aria-controls="gs-compute-panel-gpu"', body)
        self.assertEqual(body.count('data-gs-compute-save '), 1)
        self.assertNotIn('data-gs-jev-key-save', body)

    def test_saved_configs_keep_deep_links_and_actions(self):
        item = dict(config_id="cfg-a", label="保存版 A", project_id="a", template_id="base",
                    evolution=dict(generations=2, population=3, seeds=4))
        body = collection_panel([item], {"a": "世界 A"}, "cfg-a", show_detail=True)
        self.assertRegex(body, r'aria-selected="true" aria-controls="gs-configs-panel-detail"')
        self.assertIn('/configs?tab=configs&amp;item=cfg-a', body)
        self.assertIn('/configs/new?from=cfg-a', body)
        self.assertIn('/configs/cfg-a/start', body)

    def test_empty_saved_configs_disable_detail_tab(self):
        body = collection_panel([], {}, None, show_detail=True)
        self.assertRegex(body, r'aria-controls="gs-configs-panel-detail" tabindex="-1" disabled')
        self.assertIn('まだ保存版がありません', body)


if __name__ == '__main__':
    unittest.main()
