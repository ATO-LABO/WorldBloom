"""viewer/epoch_view.py (WB-WORLDGROW-001 段階5c-2): read-only epoch chain
progress strip + waiting hint. strip_html/waiting_hint are pure functions
over a chain dict (hand-built here); relevant_chain() is exercised against a
real (lightweight) EpochChain, reusing test_epoch_chain.EpochChainTestBase's
fixture so config save/normalize is genuine."""
import unittest

from test_epoch_chain import EpochChainTestBase
from viewer import epoch_view, server


class StaticFileTests(unittest.TestCase):
    def test_epoch_chain_js_is_registered(self):
        # WB-WORLDGROW-001 段階5c-2: run_workspace.render()/run_browse._document()
        # only ever reference this script when a chain strip is actually
        # present -- but the file itself must exist and be servable.
        path = server.static_path("epoch-chain.js")
        self.assertTrue(path.is_file())


def _epoch(index=0, **overrides):
    epoch = {"index": index, "step": "run", "run_job_id": f"job-e{index}", "run_id": None,
             "config_id": f"chain-x-e{index}", "patch_id": None, "approved_rev": None,
             "retired": [], "notes": []}
    epoch.update(overrides)
    return epoch


def _chain(state="running", epochs=None, **overrides):
    chain = {"chain_id": "chain-x", "base_config_id": "cfg-base", "max_epochs": 3,
             "state": state, "epochs": epochs if epochs is not None else [_epoch()]}
    chain.update(overrides)
    return chain


class StripHtmlTests(unittest.TestCase):
    def test_none_chain_is_empty(self):
        self.assertEqual(epoch_view.strip_html(None), "")

    def test_running_shows_stop_button_only(self):
        html = epoch_view.strip_html(_chain(state="running", epochs=[_epoch(step="propose")]))
        self.assertIn("エポック連鎖 1/3", html)
        self.assertIn("提案中", html)
        self.assertNotRegex(html, r"data-epoch-stop[^>]*hidden")
        self.assertRegex(html, r"data-epoch-continue[^>]*hidden")

    def test_waiting_shows_both_buttons(self):
        html = epoch_view.strip_html(_chain(state="waiting"))
        self.assertIn("承認待ち", html)
        self.assertNotRegex(html, r"data-epoch-stop[^>]*hidden")
        self.assertNotRegex(html, r"data-epoch-continue[^>]*hidden")

    def test_terminal_states_hide_both_buttons(self):
        for state in ("stopped", "failed", "completed"):
            with self.subTest(state=state):
                html = epoch_view.strip_html(_chain(state=state))
                self.assertRegex(html, r"data-epoch-stop[^>]*hidden")
                self.assertRegex(html, r"data-epoch-continue[^>]*hidden")

    def test_approved_and_retired_counts_sum_across_epochs(self):
        epochs = [_epoch(0, approved_rev="rev-1"), _epoch(1, retired=["p-1", "p-2"])]
        html = epoch_view.strip_html(_chain(epochs=epochs))
        self.assertIn("承認 1 件・淘汰 2 件", html)

    def test_history_row_links_job_and_shows_patch_and_notes(self):
        epoch = _epoch(0, run_job_id="job-abc", patch_id="patch-xyz", notes=["自動承認しました"])
        html = epoch_view.strip_html(_chain(epochs=[epoch]))
        self.assertIn('<a href="/jobs/job-abc">第1エポック</a>', html)
        self.assertIn("patch-xyz", html)
        self.assertIn("自動承認しました", html)

    def test_history_row_without_job_id_has_no_link(self):
        html = epoch_view.strip_html(_chain(epochs=[_epoch(0, run_job_id=None)]))
        self.assertNotIn("<a href=", html)
        self.assertIn("第1エポック", html)


class WaitingHintTests(unittest.TestCase):
    def test_none_chain_is_empty(self):
        self.assertEqual(epoch_view.waiting_hint(None), "")

    def test_non_waiting_state_is_empty(self):
        self.assertEqual(epoch_view.waiting_hint(_chain(state="running")), "")

    def test_waiting_names_the_epoch_number_and_next_action(self):
        html = epoch_view.waiting_hint(_chain(state="waiting", epochs=[_epoch(0), _epoch(1)]))
        self.assertIn("第 2 エポック", html)
        self.assertIn("次のエポックへ", html)


class _Server:
    def __init__(self, job_store=None, settings_path=None):
        self.job_store = job_store
        self.settings_path = settings_path


class _Handler:
    def __init__(self, server):
        self.server = server


class RelevantChainTests(EpochChainTestBase):
    def test_no_job_store_returns_none(self):
        self.assertIsNone(epoch_view.relevant_chain(_Handler(_Server(job_store=None)), "cfg-base"))

    def test_empty_config_id_returns_none(self):
        self.assertIsNone(epoch_view.relevant_chain(_Handler(_Server(self.jobs)), None))

    def test_no_chain_at_all_returns_none(self):
        self.assertIsNone(epoch_view.relevant_chain(_Handler(_Server(self.jobs)), "cfg-base"))

    def test_matches_base_config_id_before_any_job(self):
        # run_browse.conditions shows the user-selected (base) config, not
        # any epoch's own derived one -- this is the "gap between jobs"
        # screen (plan §5).
        self.start()
        self.assertIsNotNone(epoch_view.relevant_chain(_Handler(_Server(self.jobs)), "cfg-base"))

    def test_matches_current_epoch_own_config_id(self):
        # run_workspace.render shows the job's own config -- always the
        # per-epoch derived one, never the base.
        self.start()
        epoch_config_id = self.chain.current()["epochs"][-1]["config_id"]
        self.assertIsNotNone(epoch_view.relevant_chain(_Handler(_Server(self.jobs)), epoch_config_id))

    def test_unrelated_config_id_returns_none(self):
        self.start()
        self.assertIsNone(epoch_view.relevant_chain(_Handler(_Server(self.jobs)), "cfg-other"))

    def test_broken_chain_directory_returns_none_instead_of_raising(self):
        # M2 (Opus review): a corrupt chain.json (e.g. a crash mid-write)
        # must never turn an unrelated page's own GET into a 404/500 --
        # reproduces the scenario the review's own broken.py script
        # exercised over HTTP (execution/epoch_chain.py's _all() already
        # skips it; this is the second, independent guard in relevant_chain
        # itself, plus proof the two together leave the page working).
        self.chain.root.mkdir(parents=True, exist_ok=True)
        (self.chain.root / "chain-corrupt").mkdir()
        (self.chain.root / "chain-corrupt" / "chain.json").write_text("{bad", encoding="utf-8")
        self.assertIsNone(epoch_view.relevant_chain(_Handler(_Server(self.jobs)), "cfg-base"))


if __name__ == "__main__":
    unittest.main()
