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

    def test_conditions_shows_epoch_chain_strip_for_the_chains_base_config(self):
        # WB-WORLDGROW-001 段階5c-2: run_browse.conditions() (here reached
        # via GET /configs/<id>, the bare "実行条件" screen) shows the
        # user-selected (base) config -- matches epoch_view.relevant_chain()'s
        # base_config_id arm, not an epoch's own derived config_id
        # (test_run_workspace.py's own test covers that arm instead).
        # state="completed" (not in EpochChain._ACTIVE_STATES) keeps the live
        # server's own background tick() (ViewerServer.service_actions polls
        # every 0.05s in this fixture) fully inert against this
        # hand-fabricated chain, which skips fields a real
        # EpochChain.start()/tick() would have filled in for an active one.
        import time
        from execution.provenance import atomic_json, directory_lock
        chain = {"schema_version": 1, "chain_id": "chain-cond", "created_at": time.time(),
                 "updated_at": time.time(), "revision": 1, "base_config_id": "cfg-test",
                 "max_epochs": 3, "state": "completed", "approval": "auto", "auto_retire": True,
                 "idle_streak": 0, "stop_requested_at": None, "error": None,
                 "epochs": [{"index": 0, "step": "done", "config_id": "chain-cond-e0",
                             "run_job_id": None, "run_id": None, "patch_id": None,
                             "gate_status": None, "approved_rev": None, "retired": [], "notes": []}]}
        with directory_lock(self.control / "epochs"):
            atomic_json(self.control / "epochs" / "chain-cond" / "chain.json", chain)
        status, text, _ = self.get_status('/configs/cfg-test')
        self.assertEqual(status, 200)
        self.assertIn("data-epoch-chain", text)
        self.assertIn("完了", text)
        self.assertIn("epoch-chain.js", text)

    def test_no_chain_leaves_conditions_free_of_epoch_chain_markup(self):
        status, text, _ = self.get_status('/configs/cfg-test')
        self.assertEqual(status, 200)
        self.assertNotIn("data-epoch-chain", text)
        self.assertNotIn("epoch-chain.js", text)

    def test_config_detail_is_read_only_and_keeps_provenance(self):
        status, text, _ = self.get_status('/configs/cfg-test')
        self.assertEqual(status, 200)
        self.assertIn('詳細な設定・固定値・来歴を確認', text)
        self.assertIn('input_manifest_sha256', text)
        # One-step start: the conditions page carries the start form itself.
        self.assertNotIn('/configs/cfg-test/start', text)
        self.assertIn('data-wb="start"', text)
        self.assertEqual(self.fake.submitted, [])


if __name__ == '__main__':
    unittest.main()
