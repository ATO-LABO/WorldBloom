"""Navigation and immutable save boundaries for the execution settings editor."""
import unittest
from html.parser import HTMLParser
import test_workbench_pages as fixture


class SettingsMarkup(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.nav = False
        self.links = []
        self.fields = []
        self.feed(body)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav" and attrs.get("aria-label") == "実行メニュー":
            self.nav = True
        if tag == "a" and self.nav:
            self.links.append(attrs)
        if "data-field" in attrs:
            self.fields.append(attrs["data-field"])
    def handle_endtag(self, tag):
        if tag == "nav":
            self.nav = False


class RunSettingsTests(unittest.TestCase):
    setUp = fixture.WorkbenchTests.setUp
    _start_server = fixture.WorkbenchTests._start_server
    _cleanup_temp = fixture.WorkbenchTests._cleanup_temp
    get_status = fixture.WorkbenchTests.get_status
    http = fixture.WorkbenchTests.http

    def test_four_destinations_and_current_page(self):
        self.fake.add(fixture._job('job-settings-nav', 'run-nav', 'running'))
        for path, index in [('/configs/new?from=cfg-test', 0), ('/configs/cfg-test', 1),
                            ('/jobs/job-settings-nav', 2), ('/history', 3)]:
            with self.subTest(path=path):
                status, body, _ = self.get_status(path)
                self.assertEqual(status, 200)
                nav = SettingsMarkup(body).links
                self.assertEqual(len(nav), 4)
                self.assertTrue(nav[0]['href'].startswith('/configs/new'))
                self.assertEqual(nav[3]['href'], '/history')
                self.assertEqual([i for i, a in enumerate(nav) if a.get('aria-current') == 'page'], [index])
        self.assertEqual(self.fake.submitted, [])

    def test_status_link_stays_with_selected_world(self):
        from viewer.run_browse import navigation
        self.fake.add(fixture._job('job-other-world', 'other-run', 'running', config_id='cfg-other'))
        body = navigation('settings', world={'id': 'romance'}, store=self.fake)
        self.assertEqual(SettingsMarkup(body).links[2]['href'], '/jobs?world=romance')
        self.fake.add(fixture._job('job-this-world', 'this-run', 'completed'))
        body = navigation('settings', world={'id': 'romance'}, store=self.fake)
        self.assertEqual(SettingsMarkup(body).links[2]['href'], '/jobs/job-this-world')

    def test_duplicate_keeps_world_outside_changes(self):
        status, body, _ = self.get_status('/configs/new?from=cfg-test')
        self.assertEqual(status, 200)
        markup = SettingsMarkup(body)
        self.assertNotIn('project_id', markup.fields)
        self.assertNotIn('template_id', markup.fields)
        self.assertIn('evolution.keep', markup.fields)
        self.assertEqual(len(markup.fields), len(set(markup.fields)))
        self.assertIn('data-parent="cfg-test"', body)
        self.assertIn('保存して実行条件へ', body)
        original = self.configs.get('cfg-test')
        status, saved = self.http('POST', '/api/configs/cfg-test/duplicate',
                                 {'changes': {'label': '画面検証', 'evolution': {'generations': 3}}})
        self.assertEqual(status, 201)
        self.assertNotEqual(saved['config_id'], 'cfg-test')
        self.assertEqual(self.configs.get('cfg-test'), original)
        self.assertEqual(self.fake.submitted, [])
        _, conditions, _ = self.get_status('/configs/' + saved['config_id'])
        self.assertIn('今回の探索', conditions)
        self.assertNotIn('data-wb="start"', conditions)

    def test_settings_and_conditions_share_coevolution_totals(self):
        status, saved = self.http('POST', '/api/configs/cfg-test/duplicate', {
            'changes': {'evolution': {'generations': 3, 'population': 4, 'seeds': 2, 'coevolve': True}}})
        self.assertEqual(status, 201)
        cid = saved['config_id']
        for path in ['/configs/new?from=' + cid, '/configs/' + cid, '/jobs?world=romance&config=' + cid]:
            with self.subTest(path=path):
                status, body, _ = self.get_status(path)
                self.assertEqual(status, 200)
                self.assertEqual(body.count('data-run-summary'), 1)
                self.assertIn('3世代 × 4個体 × 2回 × 2陣営', body)
                self.assertIn('<b data-individual-total>24</b>', body)
                self.assertIn('<b data-total>48</b>', body)
                self.assertIn('Siftingで候補を選ぶ', body)
        self.assertEqual(self.fake.submitted, [])

    def test_new_settings_and_static_files(self):
        status, body, _ = self.get_status('/configs/new?project=romance')
        self.assertEqual(status, 200)
        fields = SettingsMarkup(body).fields
        self.assertIn('project_id', fields)
        self.assertIn('template_id', fields)
        self.assertIn('data-run-settings', body)
        self.assertIn('data-worlds=', body)
        self.assertNotIn('data-parent=', body)
        for path in ['/static/run-settings.css', '/static/run-settings.js']:
            self.assertEqual(self.get_status(path)[0], 200)
        self.assertEqual(self.fake.submitted, [])


if __name__ == '__main__':
    unittest.main()
