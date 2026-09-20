"""Execution browsing is read-only and filters/sorts recorded jobs."""
import unittest
import test_workbench_pages as fixture


class RunBrowseTests(unittest.TestCase):
    setUp = fixture.WorkbenchTests.setUp
    _start_server = fixture.WorkbenchTests._start_server
    _cleanup_temp = fixture.WorkbenchTests._cleanup_temp
    get_status = fixture.WorkbenchTests.get_status

    def test_conditions_keep_start_contract_without_submitting_on_read(self):
        status, text, _ = self.get_status('/jobs?world=romance&config=cfg-test')
        self.assertEqual(status, 200)
        self.assertIn('data-wb="start" data-config-id="cfg-test"', text)
        self.assertIn('data-request-id=', text)
        self.assertIn('今回の探索', text)
        self.assertEqual(self.fake.submitted, [])

    def test_history_filters_and_orders_by_recorded_time(self):
        older = fixture._job('job-old', 'run-a', 'succeeded')
        newer = fixture._job('job-new', 'run-z', 'failed')
        older.update(created_at=100, started_at=100)
        newer.update(created_at=200, started_at=200)
        self.fake.add(older)
        self.fake.add(newer)
        status, text, _ = self.get_status('/history')
        self.assertEqual(status, 200)
        self.assertLess(text.index('<code>run-z</code>'), text.index('<code>run-a</code>'))
        _, text, _ = self.get_status('/history?state=succeeded&world=romance&q=run-a')
        self.assertIn('<code>run-a</code>', text)
        self.assertNotIn('<code>run-z</code>', text)
        self.assertIn('1 件の探索', text)
        self.assertEqual(self.fake.submitted, [])

    def test_search_is_escaped_and_has_empty_state(self):
        status, text, _ = self.get_status('/history?q=%22%3E%3Cscript%3Ebad%3C%2Fscript%3E')
        self.assertEqual(status, 200)
        self.assertNotIn('<script>bad</script>', text)
        self.assertIn('&lt;script&gt;', text)
        self.assertIn('条件に一致する実行履歴はありません', text)

    def test_config_detail_is_read_only_and_keeps_provenance(self):
        status, text, _ = self.get_status('/configs/cfg-test')
        self.assertEqual(status, 200)
        self.assertIn('詳細な設定・固定値・来歴を確認', text)
        self.assertIn('input_manifest_sha256', text)
        self.assertIn('/configs/cfg-test/start', text)
        self.assertNotIn('data-wb="start"', text)
        self.assertEqual(self.fake.submitted, [])


if __name__ == '__main__':
    unittest.main()
