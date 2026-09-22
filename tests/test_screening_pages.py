"""Screening identity, safe default drafts, frozen synopsis and read-only routes."""
import re
import unittest
from execution.provenance import atomic_json
from viewer import screening_view
import test_output_pages as fixtures


class ScreeningTests(unittest.TestCase):
    setUp = fixtures.OutputPagesTests.setUp
    _cleanup_temp = fixtures.OutputPagesTests._cleanup_temp
    get_status = fixtures.OutputPagesTests.get_status
    _legacy_run = fixtures.OutputPagesTests._legacy_run
    _create_output = fixtures.OutputPagesTests._create_output
    _finish = staticmethod(fixtures.OutputPagesTests._finish)

    def output(self, rid, cids, oid, stamp, status="ok", text="本文", refs=None, kind="narrate"):
        oid, jid, store = self._create_output(kind=kind, run_id=rid, candidate_ids=cids, output_id=oid, synopsis_refs=refs)
        for cid in cids:
            kwargs = dict(text=text) if status == "ok" else {}
            self._finish(store, oid, cid, status, "completed" if status == "ok" else "prompt_saved", **kwargs)
        job = fixtures._gen_job(jid, rid, oid, kind, "succeeded")
        job["created_at"] = stamp
        self.fake.add(job)
        return store

    def test_latest_success_and_explicit_draft_do_not_follow_random_ids(self):
        rid, cids = self._legacy_run("screen-drafts", count=1)
        self.output(rid, cids, "out-zzzz", 100, text="古い完成稿")
        self.output(rid, cids, "out-aaaa", 200, text="最新の完成稿")
        self.output(rid, cids, "out-mmmm", 300, status="prompt_only")
        works = screening_view.build(self.fake, self.fake.outputs())
        self.assertEqual(len(works), 1)
        self.assertEqual(works[0]["preferred"]["output_id"], "out-aaaa")
        self.assertEqual([d["output_id"] for d in works[0]["drafts"]], ["out-zzzz", "out-aaaa", "out-mmmm"])
        status, body, _ = self.get_status(f"/outputs?run={rid}")
        self.assertEqual(status, 200)
        self.assertIn('class="sc-prose">最新の完成稿', body)
        self.assertNotIn('class="sc-prose">古い完成稿', body)
        self.assertIn("完成済みの稿を表示", body)
        self.assertEqual(body.count('class="sc-work"'), 1)
        status, body, _ = self.get_status(f"/outputs/out-zzzz?candidate={cids[0]}")
        self.assertEqual(status, 200)
        self.assertIn('class="sc-prose">古い完成稿', body)
        self.assertEqual(self.fake.submitted, [])

    def test_new_failed_and_unknown_attempts_do_not_hide_completed_story(self):
        rid, cids = self._legacy_run("screen-retries", count=1)
        self.output(rid, cids, "out-complete", 100, text="完成した物語")
        for n, status in enumerate(("error", "unknown"), 2):
            oid, jid, store = self._create_output(kind="narrate", run_id=rid, candidate_ids=cids)
            self._finish(store, oid, cids[0], status, "response_invalid" if status == "error" else "dispatch_unknown",
                         stage="receive", call_state="started", retry_policy="safe_new_request" if status == "error" else "explicit_confirmation")
            job = fixtures._gen_job(jid, rid, oid, "narrate", "failed")
            job["created_at"] = n * 100
            self.fake.add(job)
        _, body, _ = self.get_status(f"/outputs?run={rid}")
        self.assertIn('class="sc-prose">完成した物語', body)
        self.assertIn("失敗", body)
        self.assertIn("結果不明", body)
        self.assertEqual(self.fake.submitted, [])

    def test_identity_includes_run_and_foreign_draft_is_not_used(self):
        rid, cids = self._legacy_run("screen-a", count=1)
        other, _ = self._legacy_run("screen-b", count=1)
        self.output(rid, cids, "out-first", 100, text="第一の世界の本文")
        self.output(other, cids, "out-second", 200, text="第二の世界の本文")
        works = screening_view.build(self.fake, self.fake.outputs())
        self.assertEqual(len(works), 2)
        _, body, _ = self.get_status(f"/outputs?run={rid}&candidate={cids[0]}&output=out-second")
        self.assertIn("第一の世界の本文", body)
        self.assertNotIn("第二の世界の本文", body)

    def test_synopsis_uses_exact_frozen_ref_and_rejects_other_run(self):
        rid, cids = self._legacy_run("screen-syn", count=1)
        cid = cids[0]
        store = self.output(rid, cids, "out-syn-old", 100, text="使用したあらすじ", kind="synopsize")
        entry = store.sink("out-syn-old", cid).current()
        ref = {"output_id": "out-syn-old", "attempt_id": entry["attempt_id"], "text_sha256": entry["text_sha256"]}
        self.output(rid, cids, "out-story", 200, refs={cid: ref})
        self.output(rid, cids, "out-syn-new", 300, text="後から別に作ったあらすじ", kind="synopsize")
        _, body, _ = self.get_status("/outputs/out-story")
        self.assertIn("使用したあらすじ", body)
        self.assertNotIn("後から別に作ったあらすじ", body)
        other, _ = self._legacy_run("screen-foreign", count=1)
        self.output(other, cids, "out-foreign", 400, refs={cid: ref})
        _, body, _ = self.get_status("/outputs/out-foreign")
        self.assertIn("照合できません", body)
        self.assertNotIn("使用したあらすじ", body)
        self.assertEqual(self.fake.submitted, [])

    def test_broken_text_never_rendered_and_success_remains_available(self):
        rid, cids = self._legacy_run("screen-broken", count=1)
        self.output(rid, cids, "out-good", 100, text="正常な本文")
        store = self.output(rid, cids, "out-bad", 200, text="元の本文")
        item = store.sink("out-bad", cids[0]).current()
        (store.folder("out-bad") / item["text_ref"]).write_text("改ざんされた本文", encoding="utf-8")
        _, body, _ = self.get_status(f"/outputs?run={rid}")
        self.assertIn("正常な本文", body)
        self.assertNotIn("改ざんされた本文", body)
        self.assertEqual(self.fake.submitted, [])

    def test_empty_search_filter_and_escaped_text(self):
        status, body, _ = self.get_status("/outputs")
        self.assertEqual(status, 200)
        self.assertIn("本文を生成した作品がここに並びます", body)
        rid, cids = self._legacy_run("screen-filter", count=2)
        self.output(rid, [cids[0]], "out-html", 100, text='<script>alert("bad")</script> みかん')
        self.output(rid, [cids[1]], "out-prompt", 200, status="prompt_only")
        _, body, _ = self.get_status(f"/outputs?run={rid}&state=ready")
        self.assertEqual(body.count('class="sc-work"'), 1)
        self.assertNotIn('<script>alert', body)
        self.assertIn('&lt;script&gt;', body)
        _, body, _ = self.get_status(f"/outputs?run={rid}&q=absent-query")
        self.assertIn("条件に一致する作品がありません", body)
        self.assertNotIn('class="sc-work"', body)

    def test_missing_timestamp_does_not_claim_latest(self):
        rid, cids = self._legacy_run("screen-no-date", count=1)
        store = self.output(rid, cids, "out-nodate", 100)
        self.fake._jobs.clear()
        atomic_json(store.folder("out-nodate") / "quota.json", {"schema_version": 1, "started": []})
        work = screening_view.build(self.fake, self.fake.outputs())[0]
        self.assertNotIn("最新", work["preferred"]["label"])
        self.assertEqual(work["preferred"]["date"], "日時の記録なし")

    def test_history_preserves_recovery_links_and_assets_are_served(self):
        rid, cids = self._legacy_run("screen-history", count=1)
        self.output(rid, cids, "out-history", 100)
        _, body, _ = self.get_status(f"/outputs?run={rid}&view=history")
        self.assertIn('/outputs/out-history?view=record', body)
        _, body, _ = self.get_status("/outputs/out-history?view=record")
        self.assertIn("別の稿を作る", body)
        self.assertIn("data-screening", body)
        for asset in ("screening-workspace.css", "screening-workspace.js"):
            status, _, _ = self.get_status("/static/" + asset)
            self.assertEqual(status, 200)
        self.assertEqual(self.fake.submitted, [])

    def test_active_generation_has_read_only_update_notice(self):
        rid, cids = self._legacy_run("screen-active", count=1)
        oid, jid, _ = self._create_output(kind="narrate", run_id=rid, candidate_ids=cids)
        self.fake.add(fixtures._gen_job(jid, rid, oid, "narrate", "running"))
        _, body, _ = self.get_status(f"/outputs?run={rid}")
        self.assertIn('data-sc-update hidden', body)
        self.assertIn('&quot;output_id&quot;', body)
        self.assertEqual(self.fake.submitted, [])
