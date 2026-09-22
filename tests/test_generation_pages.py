"""Generation review is read-only and preserves receipt/retry boundaries."""
import json
import unittest
from urllib.parse import urlencode
import test_output_pages as fixtures

class GenerationPagesTests(unittest.TestCase):
    setUp = fixtures.OutputPagesTests.setUp
    _cleanup_temp = fixtures.OutputPagesTests._cleanup_temp
    get_status = fixtures.OutputPagesTests.get_status
    http = fixtures.OutputPagesTests.http
    _legacy_run = fixtures.OutputPagesTests._legacy_run
    _create_output = fixtures.OutputPagesTests._create_output
    _finish = staticmethod(fixtures.OutputPagesTests._finish)

    def plan(self, rid, cids, **kw):
        query = {"kind":"synopsize", "config":"cfg-test", "candidate":cids, **kw}
        status, body, _ = self.get_status(f"/api/runs/{rid}/generation-plan?"+urlencode(query,doseq=True))
        self.assertEqual(status,200,body)
        return json.loads(body)

    def test_read_only_plan_freezes_only_eligible_candidates(self):
        rid,cids = self._legacy_run("generation-plan",count=3)
        oid,jid,store = self._create_output(kind="synopsize",run_id=rid,candidate_ids=[cids[0]])
        self._finish(store,oid,cids[0],"ok","completed",text="既存の文章")
        self.fake.add(fixtures._gen_job(jid,rid,oid,"synopsize","succeeded"))
        result = self.plan(rid,cids)
        self.assertEqual(result["request"]["candidate_ids"],sorted(cids[1:]))
        self.assertFalse(next(c for c in result["candidates"] if c["candidate_id"]==cids[0])["eligible"])
        self.assertEqual(self.fake.submitted,[])
        self.assertEqual(result["backend"],"none")
        self.assertNotIn("config",result)
        self.assertNotIn("generation",result)
        self.assertNotIn("by_id",result)

    def test_legacy_requires_explicit_config(self):
        rid,cids = self._legacy_run("generation-legacy",count=1)
        result = self.plan(rid,cids,config="")
        self.assertIsNone(result["request"])
        self.assertTrue(result["legacy"])
        self.assertEqual(result["configs"][0]["config_id"],"cfg-test")
        self.assertTrue(result["errors"])

    def test_active_job_resumes_instead_of_start(self):
        rid,cids = self._legacy_run("generation-busy",count=1)
        self.fake.add(fixtures._gen_job("job-busy",rid,None,"narrate","running",completed=0))
        result = self.plan(rid,cids)
        self.assertIsNone(result["request"])
        self.assertEqual(result["active_job"],{"job_id":"job-busy","kind":"narrate"})
        self.assertFalse(self.fake.submitted)

    def test_unknown_attempt_requires_exact_ack(self):
        rid,cids = self._legacy_run("generation-unknown",count=1)
        oid,jid,store = self._create_output(kind="synopsize",run_id=rid,candidate_ids=cids)
        record = self._finish(store,oid,cids[0],"unknown","worker_disappeared")
        self.fake.add(fixtures._gen_job(jid,rid,oid,"synopsize","interrupted"))
        self.assertIsNone(self.plan(rid,cids)["request"])
        result = self.plan(rid,cids,ack="1",from_output=oid,mode="regenerate")
        self.assertTrue(result["request"]["acknowledge_unknown"])
        self.assertEqual(result["request"]["attempt_ids"],[record["attempt_id"]])
        self.assertEqual(self.fake.submitted,[])

    def test_bad_kind_returns_json(self):
        status,body,headers = self.get_status('/api/runs/anything/generation-plan?kind=bad')
        self.assertEqual(status,400)
        self.assertIn('application/json',headers['Content-Type'])
        self.assertEqual(json.loads(body)['code'],'bad_request')

    def test_monitor_has_single_controller_and_correct_phase(self):
        rid,cids = self._legacy_run("generation-monitor",count=1)
        for kind,label in (("synopsize","あらすじの生成"),("narrate","本文の生成")):
            jid="job-"+kind
            self.fake.add(fixtures._gen_job(jid,rid,None,kind,"running",completed=0))
            status,body,_ = self.get_status('/jobs/'+jid)
            self.assertEqual(status,200,body)
            self.assertIn(label,body)
            self.assertEqual(body.count('data-generation '),1)
            self.assertIn('/static/generation.js',body)
            self.assertNotIn('data-wb="job"',body)

    def test_single_and_grid_use_same_modal(self):
        rid,cids = self._legacy_run("generation-surfaces",count=2)
        for path in (f'/runs/{rid}/candidates','/exp/generation-surfaces'):
            status,body,_=self.get_status(path)
            self.assertEqual(status,200,body)
            self.assertEqual(body.count('<dialog class="gen-surface"'),1)
            self.assertIn('/static/generation.js',body)
            self.assertIn('data-sf-mode="synopsis"',body)
        status,body,_=self.get_status(f'/runs/{rid}/generate?kind=synopsize&config=cfg-test&candidate={cids[0]}')
        self.assertEqual(status,200,body)
        self.assertEqual(body.count('data-generation '),1)
        self.assertIn('plan_url',body)
        self.assertFalse(self.fake.submitted)

    def test_verified_text_rejects_tampering(self):
        rid,cids=self._legacy_run("generation-sha",count=1)
        oid,jid,store=self._create_output(kind="synopsize",run_id=rid,candidate_ids=cids)
        receipt=self._finish(store,oid,cids[0],"ok","completed",text="検証済みのあらすじ")
        path=f'/outputs/{oid}/entries/{cids[0]}/text'
        self.assertEqual(self.get_status(path)[:2],(200,"検証済みのあらすじ"))
        (store.folder(oid)/receipt['text_ref']).write_text('changed',encoding='utf-8')
        self.assertNotEqual(self.get_status(path)[0],200)
