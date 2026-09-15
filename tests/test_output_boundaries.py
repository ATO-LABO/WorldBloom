"""UI006 persistence, fixed-source and provider-response boundaries."""
from copy import deepcopy
import http.client
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

from execution.generation import Response, BoundaryError, run_generation, transport, validate_response
from execution.output_requests import normalize, admit, prepare
from execution.output_store import OutputStore
from execution.provenance import ConfigError, canonical, read_json, sha256
import test_generation_execution as foundation
TEXT = foundation.TEXT
import test_output_jobs as integration


class PersistenceBoundaries(unittest.TestCase):
    setUp = foundation.GenerationTests.setUp
    make = foundation.GenerationTests.make
    call = foundation.GenerationTests.call

    def test_each_persistence_boundary_recovers_without_external_call(self):
        import execution.output_store as storage
        for stage in ("raw", "response_receipt", "body", "success_receipt", "pointer", "index"):
            with self.subTest(stage=stage):
                sink = self.make(output="out-"+stage, count=1)
                real_bytes, real_json = storage.write_bytes, storage.atomic_json
                hit = []
                def fail_bytes(path, data):
                    selected = (stage == "raw" and path.name == "response.bin") or (stage == "body" and path.name.startswith("body-"))
                    if selected and not hit:
                        hit.append(stage); raise OSError("private-disk-error")
                    return real_bytes(path, data)
                def fail_json(path, value):
                    selected = ((stage == "response_receipt" and path.name == "response.json")
                        or (stage == "success_receipt" and path.parent.name == "receipts" and value.get("status") == "ok")
                        or (stage == "pointer" and path.name == "current.json")
                        or (stage == "index" and path.name == "index.json"))
                    if selected and not hit:
                        hit.append(stage); raise OSError("private-disk-error")
                    return real_json(path, value)
                with patch.object(storage,"write_bytes",side_effect=fail_bytes), patch.object(storage,"atomic_json",side_effect=fail_json):
                    value, called = self.call(sink)
                self.assertEqual(hit,[stage]); called.assert_called_once()
                raw = (sink.folder / "response.bin").read_bytes() if (sink.folder / "response.bin").exists() else None
                with patch("execution.generation.transport") as resend:
                    result = self.store.recover(sink.identity["output_id"], owner_stopped=True)
                resend.assert_not_called()
                self.assertEqual(result["entries"][0]["status"], "unknown" if stage in ("raw","response_receipt") else "ok")
                if raw is not None:self.assertEqual((sink.folder/"response.bin").read_bytes(),raw)
                if stage == "index":self.assertEqual(value["status"],"ok")

    def test_bad_repeated_request_cannot_replace_success(self):
        sink=self.make(count=1);self.call(sink)
        before=sink.current();bad=sink.call_request();bad["model"]="wrong"
        with self.assertRaises(ConfigError),patch("execution.generation.transport") as external:
            run_generation(bad,sink)
        external.assert_not_called();self.assertEqual(sink.current(),before)

    def test_limit_recovery_retains_unknown_and_marks_unstarted_limit(self):
        sink=self.make();sink.reserve()
        doc=self.store.recover("out-test",owner_stopped=True,stopped="limit")
        self.assertEqual([e["status"] for e in doc["entries"]],["unknown","skipped_limit"])

    def test_unconfirmed_child_termination_aborts_worker_before_next_dispatch(self):
        import io
        from unittest.mock import Mock
        from execution.generation import ContainmentError
        sink=self.make()
        child=Mock();child.returncode=0;child.stdin=io.BytesIO();child.stdout=io.BytesIO(b"text")
        with patch("execution.generation.preflight",return_value=["fixture"]),patch("execution.worker.ProcessTree") as factory:
            factory.return_value.launch.return_value=child
            factory.return_value.finish.side_effect=OSError("cannot confirm termination")
            with self.assertRaises(ContainmentError):run_generation(sink.call_request(),sink)
        self.assertTrue(sink.started())
        self.assertFalse(self.store.sink("out-test","cand-1").started())
        self.assertEqual(self.store.recover("out-test",owner_stopped=True)["entries"][0]["status"],"unknown")

    def test_api_responses_are_bounded_and_invalid_shapes_are_classified(self):
        valid={"status":"completed","output":[{"content":[{"type":"output_text","text":TEXT}]}]}
        self.assertEqual(validate_response("openai",Response(canonical(valid))),TEXT)
        self.assertEqual(validate_response("anthropic",Response(canonical({"stop_reason":"end_turn","content":[{"type":"text","text":TEXT}]}))),TEXT)
        for i, payload in enumerate(([], {"output":None}, {"output":[None]}, {"status":"incomplete","output":[]}, {"output":[{"content":[{"type":"output_text","text":123}]}]})):
            sink=self.make(backend="openai",count=1,output="out-api-"+str(i))
            value,_=self.call(sink,response=Response(canonical(payload)))
            self.assertEqual(value["code"],"response_invalid")
            self.assertEqual(value["retry_policy"],"safe_new_request")
        with self.assertRaises(ValueError):
            validate_response("anthropic",Response(canonical({"stop_reason":"max_tokens","content":[{"type":"text","text":TEXT}]})))
        req={"backend":"openai","model":"fixed-model","credentials":{"api_key":"secret"},"prompt":"prompt",
             "limits":{"max_saved_response_bytes":8,"call_timeout_seconds":1}}
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):self.n=n;return b"a"*n
        stream=Stream()
        with patch("execution.generation.urllib.request.urlopen",return_value=stream):response=transport(req,None)
        self.assertEqual(stream.n,9);self.assertEqual(len(response.raw),8);self.assertTrue(response.truncated)
        for cause,code in ((TimeoutError(),"transport_timeout"),(urllib.error.URLError("closed"),"transport_disconnected"),
                           (http.client.IncompleteRead(b"part"),"transport_disconnected")):
            with patch("execution.generation.urllib.request.urlopen",side_effect=cause),self.assertRaises(BoundaryError) as error:
                transport(req,None)
            self.assertEqual(error.exception.code,code)


class FrozenSourceBoundaries(unittest.TestCase):
    setUp = integration.OutputJobTests.setUp
    cleanup_jobs = integration.OutputJobTests.cleanup_jobs
    request = integration.OutputJobTests.request
    # WB-UI-021: setUp() now calls self.set_generation() (writes the test's
    # own settings.json), so it must be borrowed alongside setUp itself.
    set_generation = integration.OutputJobTests.set_generation

    def plan(self, request=None):
        request=normalize(request or self.request())
        with self.jobs._lock():return admit(self.jobs,request,settings_path=self.settings_path)

    def job(self, name):return {"job_id":"job-"+name,"output_id":"out-"+name}

    def test_source_changes_after_admission_fail_before_publication(self):
        plan=self.plan()
        source=self.run / plan["candidates"][0]["log"]["relative_path"]
        source.write_bytes(b"changed")
        with self.assertRaises(ConfigError):prepare(self.configs,self.job("changed"),plan)
        self.assertFalse(self.outputs.folder("out-changed").exists())

    def test_synopsis_reference_is_fixed_by_candidate_attempt_input_and_sha(self):
        plan=self.plan();prepare(self.configs,self.job("synopsis"),plan)
        cid=self.ids[0];sink=self.outputs.sink("out-synopsis",cid)
        from execution.generation import result
        record=sink.finish(result(sink.identity,"ok","completed"),text=TEXT)
        ref={"output_id":"out-synopsis","attempt_id":record["attempt_id"],"text_sha256":record["text_sha256"]}
        req=self.request("narration",kind="narrate",candidate_ids=[cid],synopsis_refs={cid:ref})
        narr=self.plan(req);prepare(self.configs,self.job("narration"),narr)
        folder=self.outputs.folder("out-narration")
        self.assertEqual((folder/"inputs/synopses"/(cid+".txt")).read_text(encoding="utf-8"),TEXT)
        before=(folder/"items"/cid/"prompt.txt").read_bytes()
        sink.finish(result(sink.identity,"ok","completed"),text="後の別版")
        self.assertEqual((folder/"items"/cid/"prompt.txt").read_bytes(),before)
        with self.assertRaises(ConfigError):prepare(self.configs,self.job("stale-synopsis"),narr)

    def test_historical_candidates_sharing_cell_do_not_overwrite_or_invent_reach_rate(self):
        plan=self.plan()
        # Exercise the immutable mapping with a historical non-representative.
        first,second=plan["candidates"]
        second["cell_key"]=first["cell_key"]
        plan["representatives"]={first["cell_key"]:first["candidate_id"]}
        prepare(self.configs,self.job("history"),plan)
        folder=self.outputs.folder("out-history")
        archive=read_json(folder/"generation-archive.json")
        self.assertEqual(len(archive["cells"]),2)
        self.assertNotIn("reach_rate",archive["cells"][second["candidate_id"]])
        self.assertTrue(all((folder/"items"/cid/"prompt.txt").is_file() for cid in self.ids))
        self.assertNotEqual(archive["candidate_mapping"][self.ids[0]]["candidate_id"],archive["candidate_mapping"][self.ids[1]]["candidate_id"])


if __name__ == "__main__":unittest.main()
