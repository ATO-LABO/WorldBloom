"""Frozen Studio job and local HTTP story boundaries; no provider calls."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from urllib.parse import urlencode
import test_output_jobs as jobfixtures
import test_output_pages as uifixtures
from test_story_pipeline import fixture
from gapengine.story_materials import extract_materials
from gapengine.story_plan import create_plan
from execution.story_store import StoryStore
from execution.story_service import organize_studio,pinned_story,process_story_output
from execution.provenance import ConfigError,canonical,sha256,atomic_json
from execution.output_requests import normalize,admit,eligible
from execution.output_store import OutputStore
from execution.generation import result
from execution.selections import SelectionStore

class StoryJobTests(unittest.TestCase):
    cleanup_jobs=jobfixtures.OutputJobTests.cleanup_jobs
    wait_state=jobfixtures.OutputJobTests.wait_state
    set_generation=jobfixtures.OutputJobTests.set_generation
    finish=jobfixtures.OutputJobTests.finish
    def setUp(self):
        jobfixtures.OutputJobTests.setUp(self)
        self.configs.save({"label":"momotaro","project_id":"momotaro_plus3","template_id":"momotaro_plus3",
            "evolution":{"generations":1,"population":1,"seeds":1}},config_id="cfg-story")
        _,raw,_=fixture()
        root=self.configs.runs/"story-fixture";root.mkdir()
        (root/"layers.jsonl").write_bytes(raw)
        atomic_json(root/"archive.json",{"cells":{"C|low":{"generation":0,"quality":0.5,"reach_rate":1.0,
             "reached":True,"exemplar":{"seed":20,"layers_path":"layers.jsonl"},"parents":[],"genome":{}}}})
        self.rid=self.catalog.register_legacy(root.name)
        self.ids=[self.catalog.candidates(self.rid)["candidates"][0]["candidate_id"]]
        self.sel=SelectionStore(self.catalog).update(self.rid,[{"candidate_id":self.ids[0],"state":"adopted"}],expected_revision=0)
        current=organize_studio(self.configs,self.catalog,self.rid,self.ids[0],"cfg-story")
        self.stories=StoryStore(self.configs.control)
        self.stories.confirm(current["plan"]["plan_id"],current["plan_sha256"])
        self.ref=self.stories.reference(current["plan"]["plan_id"])
    def request(self):
        request=jobfixtures.OutputJobTests.request(self,kind="narrate",config="cfg-story",schema_version=2,
            pipeline="story_v1",story_refs={self.ids[0]:self.ref},synopsis_refs={})
        return normalize(request)
    def test_owned_prompt_job_pins_confirmed_material_and_builds_from_runtime(self):
        job=self.jobs.submit(self.request(),settings_path=self.settings_path)[0]
        end=self.finish(job)
        self.assertEqual(end["state"],"succeeded",end)
        store=OutputStore(self.configs.control);req=store.request(job["output_id"])
        self.assertEqual(req["schema_version"],2)
        material,plan,_=pinned_story(store.folder(job["output_id"]),self.ids[0],req)
        self.assertEqual(plan["plan_id"],self.ref["plan_id"])
        prompt=store.sink(job["output_id"],self.ids[0]).folder/"prompt.txt"
        self.assertIn("used_event_ids",prompt.read_text(encoding="utf-8"))
        self.stories.edit(self.rid,self.ids[0],{"focus":"別の主軸"},expected_revision=1)
        # A started version continues using its pinned copy, not the live head.
        pinned_story(store.folder(job["output_id"]),self.ids[0],req)
        with self.assertRaises(ConfigError):admit(self.jobs,self.request(),settings_path=self.settings_path)
        # A received body can be checked repeatedly without invoking transport.
        from unittest.mock import patch
        sink=store.sink(job["output_id"],self.ids[0])
        body=json.dumps({"paragraphs":[{"text":"本文。","event_ids":plan["required_event_ids"],"embellishments":[]}]})
        sink.finish(result(sink.identity,"ok","fixture_completed"),text=body)
        with patch("execution.generation.transport",side_effect=AssertionError("must not resend")):
            checked=process_story_output(store,job["output_id"],self.ids[0])
            self.assertEqual(checked,process_story_output(store,job["output_id"],self.ids[0]))
        self.assertEqual(checked["validation"]["human_adoption"],"pending")
        self.assertEqual(sink.current()["status"],"ok")
        (store.folder(job["output_id"])/"inputs/story"/self.ids[0]/"plan.json").write_text("{}",encoding="utf-8")
        with self.assertRaises(ConfigError):store.verify_artifacts(job["output_id"])
    def test_cross_candidate_and_invalid_version_rejected_before_dispatch(self):
        request=self.request();bad=deepcopy(request);bad["story_refs"]["other"]=bad["story_refs"].pop(self.ids[0])
        with self.assertRaises(ConfigError):normalize(bad)
        bad=deepcopy(request);bad["schema_version"]=1
        with self.assertRaises(ConfigError):normalize(bad)
        bad=deepcopy(request);bad["story_refs"][self.ids[0]]["plan_sha256"]="0"*64
        with self.assertRaises(ConfigError):admit(self.jobs,bad,settings_path=self.settings_path)

class StoryPagesTests(unittest.TestCase):
    setUp=uifixtures.OutputPagesTests.setUp
    _cleanup_temp=uifixtures.OutputPagesTests._cleanup_temp
    get_status=uifixtures.OutputPagesTests.get_status
    http=uifixtures.OutputPagesTests.http
    def seed(self):
        m,raw,inputs=fixture()
        m=extract_materials(raw,inputs,{"run_id":"saved-experiment","candidate_id":"candidate-06"})
        store=StoryStore(self.control)
        store.save_material(m,artifacts={"inputs/layers.jsonl":raw,**{"inputs/"+p:b for p,b in inputs.items()}})
        current=store.set_current("saved-experiment","candidate-06",create_plan(m),expected_revision=0)
        return store,current
    def test_edit_confirm_conflict_and_csrf(self):
        store,current=self.seed();base="/api/stories/runs/saved-experiment/candidate-06"
        status,body,_=self.get_status("/stories/saved-experiment/candidate-06")
        self.assertEqual(status,200,body);self.assertIn("data-story-edit-json",body)
        self.assertIn("story-workspace.js",body)
        status,data=self.http("POST",base+"/edit",{"expected_revision":1,"changes":{"focus":"贈与と関係"}})
        self.assertEqual(status,200,data);self.assertFalse(data["confirmed"])
        self.assertEqual(self.http("POST",base+"/confirm",{"plan_sha256":current["plan_sha256"]})[0],409)
        self.assertEqual(self.http("POST",base+"/edit",{"expected_revision":1,"changes":{"focus":"競合"}})[0],409)
        current=store.current("saved-experiment","candidate-06")
        self.assertEqual(self.http("POST",base+"/confirm",{"plan_sha256":current["plan_sha256"]})[0],200)
        import http.client
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        conn.request("POST",base+"/confirm",body="{}",headers={"Content-Type":"application/json"})
        response=conn.getresponse();self.assertEqual(response.status,403);response.read();conn.close()
        self.assertFalse(self.fake.submitted)
    def test_saved_invalid_body_review_and_unknown_are_separate(self):
        stories,current=self.seed();plan=current["plan"]
        stories.confirm(plan["plan_id"],current["plan_sha256"])
        material=stories.material(plan["material_ref"]["material_id"]);confirmation=stories.confirmation(plan["plan_id"])
        oid,cid="out-story-ui","candidate-06";ref=stories.reference(plan["plan_id"])
        req={"schema_version":2,"pipeline":"story_v1","story_refs":{cid:ref},"output_id":oid,"request_id":"req-story",
             "job_id":"cli-fixture","kind":"narrate","run_id":"saved-experiment","candidate_ids":[cid],"backend":"none","model":None,
             "limits":{"max_calls":0,"wall_seconds":120,"call_timeout_seconds":60,"max_saved_response_bytes":128000},
             "mode":"regenerate","synopsis_refs":{},"runtime_manifest_sha256":"0"*64,
             "sources":[{"candidate_id":cid,"source_log_sha256":material["source_binding"]["source_log_sha256"]}]}
        prefix="inputs/story/"+cid+"/";store=OutputStore(self.control)
        store.create(req,{cid:"fixture prompt"},artifacts={prefix+"materials.json":canonical(material),prefix+"plan.json":canonical(plan),prefix+"confirmation.json":canonical(confirmation)})
        sink=store.sink(oid,cid)
        raw=json.dumps({"text":"本文は保全される。","paragraph_sources":[{"paragraph":1},{"paragraph":2}]})
        sink.finish(result(sink.identity,"ok","completed"),text=raw)
        endpoint="/api/stories/outputs/"+oid+"/"+cid
        status,data=self.http("POST",endpoint+"/recheck",{})
        self.assertEqual(status,200,data);self.assertEqual(data["text"],"本文は保全される。")
        self.assertEqual(data["validation"]["structural_status"],"failed")
        status,body,_=self.get_status("/stories/outputs/"+oid+"/"+cid)
        self.assertEqual(status,200,body);self.assertIn("failed",body);self.assertIn("本文は保全",body)
        review={"text_sha256":data["validation"]["text_sha256"],"adoption":"revise","note":"段落の対応を確認",
                "causal_clarity":1,"story_flow":2,"surprise":None}
        self.assertEqual(self.http("POST",endpoint+"/review",review)[0],200)
        status,body,_=self.get_status("/stories/outputs/"+oid+"/"+cid)
        self.assertEqual(status,200,body)
        self.assertIn('value="revise" selected',body)
        self.assertIn('data-story-review-note>段落の対応を確認</textarea>',body)
        self.assertIn('data-story-score="causal_clarity"',body)
        self.assertIn('<option value="1" selected>1</option>',body)
        invalid={**review,"causal_clarity":6}
        self.assertEqual(self.http("POST",endpoint+"/review",invalid)[0],422)
        review["text_sha256"]="0"*64
        self.assertEqual(self.http("POST",endpoint+"/review",review)[0],409)
        sink.finish(result(sink.identity,"unknown","fixture_unknown",retry_policy="explicit_confirmation"))
        new_request={**req,"acknowledge_unknown":False,"attempt_ids":[],"mode":"regenerate"}
        with self.assertRaises(ConfigError):eligible(store,new_request)
        self.assertFalse(self.fake.submitted)
    def test_missing_material_and_invalid_reference_are_explicit(self):
        self.assertEqual(self.get_status("/api/stories/runs/missing/candidate")[0],404)
        self.assertEqual(self.http("POST","/api/stories/runs/missing/candidate/edit",{"expected_revision":True,"changes":{}})[0],422)
        self.assertEqual(self.get_status("/static/story-workspace.js")[0],200)


class StandaloneStoryClockTests(unittest.TestCase):
    """Delayed preparation and GPU wait use one owned clock, without provider calls."""
    def run_clock(self, wait):
        import tempfile
        from contextlib import contextmanager
        from unittest.mock import patch
        from scripts.story_narrate import execute
        from execution.provenance import read_json
        clock={"now":0.0};seen=[]
        with tempfile.TemporaryDirectory() as tmp, patch("time.time",side_effect=lambda:clock["now"]):
            control=Path(tmp);oid,cid="out-clock","candidate-clock"
            request={"schema_version":2,"pipeline":"story_v1","story_refs":{},"output_id":oid,
                "job_id":"cli-clock","kind":"narrate","run_id":"clock-fixture","candidate_ids":[cid],
                "backend":"ollama","model":"fixture","limits":{"max_calls":1,"wall_seconds":100,
                "call_timeout_seconds":80,"max_saved_response_bytes":4096}}
            store=OutputStore(control);store.create(request,{cid:"clock fixture prompt"})
            sink=store.sink(oid,cid)
            # A prepared output is not an owned execution and cannot reserve calls yet.
            self.assertFalse(sink.reserve())
            with self.assertRaises(ConfigError):store.begin_cli_execution(oid)
            clock["now"]=1000.0
            @contextmanager
            def gpu(*args,**kwargs):
                self.assertEqual(kwargs["wait_seconds"],100)
                clock["now"]+=wait
                yield
            def generate(call,sink):
                seen.append(call["deadline"])
                self.assertTrue(sink.reserve())
                return sink.finish(result(sink.identity,"unknown","fixture_timeout",call_state="started",
                    retry_policy="explicit_confirmation"))
            with patch("gapengine.gpu_guard.local_gpu_session",gpu), \
                 patch("execution.output_worker._output_settings",return_value={}), \
                 patch("execution.output_worker.credentials",return_value={}), \
                 patch("execution.generation.run_generation",side_effect=generate), \
                 patch("execution.story_service.process_story_output",return_value=None):
                doc=execute(control,oid,None)
            quota=read_json(store.folder(oid)/"quota.json")
            self.assertEqual(quota["created_at"],0)
            self.assertEqual(quota["execution_started_at"],1000)
            clock["now"]=2000
            self.assertEqual(store.begin_cli_execution(oid),1000)  # never reset an expired budget
            with self.assertRaises(FileExistsError):execute(control,oid,None)
            self.assertEqual(len(read_json(store.folder(oid)/"quota.json")["started"]),len(seen))
            return seen,doc
    def test_delayed_execution_and_gpu_wait_share_deadline_without_reset(self):
        seen,doc=self.run_clock(70)
        self.assertEqual(seen,[1100])  # 30 seconds left, not a fresh 80-second call
        self.assertEqual(doc["counts"],{"unknown":1})
    def test_expired_gpu_wait_does_not_dispatch_or_consume_a_call(self):
        seen,doc=self.run_clock(101)
        self.assertEqual(seen,[])
        self.assertEqual(doc["counts"],{"skipped_limit":1})
