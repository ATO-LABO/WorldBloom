"""UI006 admission, frozen sources, live Windows workers and HTTP. No provider calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from execution.jobs import JobStore
from execution.output_settings import resolve_generation
from execution.output_store import OutputStore
from execution.output_requests import normalize, admit, prepare, eligible
from execution.provenance import ConfigError, atomic_json, canonical, read_json, sha256
from execution.selections import SelectionStore
from execution.generation import Response, BoundaryError, run_generation, transport
from execution import worker
from viewer.run_catalog import RunCatalog
from viewer.data import RunRepository
from viewer.server import ViewerServer, ViewerHandler
import test_execution_jobs as support
from test_output import _write_archive, _write_rows

ROOT = Path(__file__).resolve().parents[1]
TEXT = "二人は昔の約束を思い出し、すれ違いの理由を話し合って気持ちを確かめた。"


@unittest.skipUnless(os.name == "nt", "Windows generation supervision")
class OutputJobTests(unittest.TestCase):
    cleanup_jobs = support.JobTests.cleanup_jobs
    wait_state = support.JobTests.wait_state

    def setUp(self):
        support.JobTests.setUp(self)
        shutil.copytree(ROOT / "viewer", self.repo / "viewer", ignore=shutil.ignore_patterns("__pycache__"))
        self.settings_path = self.base / "settings.json"
        self.set_generation("none")
        self.configs.runs.mkdir(exist_ok=True)
        self.run = self.configs.runs / "legacy-fixture"
        self.run.mkdir()
        _write_rows(self.run / "first.jsonl")
        _write_rows(self.run / "second.jsonl")
        _write_archive(self.run / "archive.json", "first.jsonl")
        archive = read_json(self.run / "archive.json")
        archive["cells"]["IV|low"] = deepcopy(archive["cells"]["III|high"])
        archive["cells"]["IV|low"]["exemplar"]["layers_path"] = "second.jsonl"
        atomic_json(self.run / "archive.json", archive)
        self.catalog = RunCatalog(self.configs.runs, self.configs.control, self.jobs)
        self.rid = self.catalog.register_legacy(self.run.name)
        self.ids = sorted(self.catalog.representatives(self.catalog.snapshot(self.rid)).values())
        self.selection = SelectionStore(self.catalog)
        self.sel = self.selection.update(self.rid,
            [{"candidate_id": cid, "state": "adopted"} for cid in self.ids], expected_revision=0)
        self.outputs = OutputStore(self.configs.control)

    def set_generation(self, backend, model=None, limits=None):
        """Rewrite the test's own settings.json -- WB-UI-021's single source
        of generation config, replacing the old per-config "generation" spec.
        """
        section = {"model": model} if backend != "none" else {}
        if limits is not None:
            section["limits"] = limits
        payload = {"output": {"default_backend": backend}}
        if section:
            payload["output"][backend] = section
        atomic_json(self.settings_path, payload)

    def request(self, rid="req-output", kind="synopsize", config="cfg-test", **changes):
        return {"request_id": rid, "kind": kind, "config_id": config, "run_id": self.rid,
            "selection_revision": self.sel["revision"], "candidate_ids": self.ids,
            **resolve_generation(self.settings_path), "mode": "missing_or_failed", **changes}

    def start(self, request=None):
        return self.jobs.submit(request or self.request(), settings_path=self.settings_path)[0]

    def finish(self, job):
        end = self.wait_state(job["job_id"], worker.TERMINAL)
        support.until(lambda: worker.output_tree_stopped(self.jobs._read(job["job_id"])))
        return end

    def fixture_transport(self, mode="ok", delay=0):
        # Instrument ONLY the disposable runtime source. No real backend executes.
        path = self.repo / "execution/generation.py"
        source = path.read_text(encoding="utf-8")
        source += "\n_original_preflight = preflight\n"
        source += "def preflight(request): return None\n"
        source += f"def transport(request, command):\n    time.sleep({delay!r})\n"
        if mode == "tree":
            marker = str(self.base / "descendant.json")
            source += ("    import sys\n"
                       "    child = subprocess.Popen([sys.executable, '-B', '-c', 'import time;time.sleep(300)'])\n"
                       f"    Path({marker!r}).write_text(json.dumps({{'pid':child.pid}}))\n    time.sleep(300)\n")
        elif mode == "unknown":
            source += "    raise BoundaryError('transport_timeout')\n"
        elif mode == "mixed":
            source += f"    if request['candidate_id'] == {self.ids[-1]!r}: raise BoundaryError('process_exit_unknown')\n"
        elif mode == "slow_second":
            source += f"    if request['candidate_id'] == {self.ids[-1]!r}: time.sleep(300)\n"
        source += f"    return Response({canonical({"status":"completed","output":[{"content":[{"type":"output_text","text":TEXT}]}]})!r})\n"
        path.write_text(source, encoding="utf-8")
        self.set_generation("openai", "fixture-model", {"max_calls": 2, "call_timeout_seconds": 10,
            "wall_seconds": 60, "max_saved_response_bytes": 4096})

    def launch_fixture(self, request):
        with patch("execution.output_requests.generation_availability", return_value={"available":True}):
            return self.start(request)

    def test_prompt_only_and_concurrent_idempotency_with_read_only_reconnect(self):
        before = {p.name: p.read_bytes() for p in self.run.iterdir() if p.is_file()}
        req = self.request()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.jobs.submit(req, settings_path=self.settings_path), range(2)))
        self.assertEqual(sum(created for _, created in results), 1)
        job = results[0][0]
        end = self.finish(job)
        self.assertEqual((end["state"], end["completion_kind"]), ("succeeded", "prompt_only"), end)
        self.assertEqual(end["counts"], {"prompt_only":2})
        other = JobStore(self.configs)
        for _ in range(2):
            same, created = other.submit(req, settings_path=self.settings_path)
            self.assertFalse(created)
            self.assertEqual(same["output_id"], job["output_id"])
            self.assertEqual(other.output(job["output_id"])["counts"], {"prompt_only":2})
        self.assertEqual(len(list(self.outputs.root.glob("out-*"))), 1)
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.run.iterdir() if p.is_file()})
        folder = self.outputs.folder(job["output_id"])
        seal = self.outputs.verify_artifacts(job["output_id"])
        self.assertTrue(seal["artifacts"])
        self.assertEqual(read_json(folder / "quota.json")["started"], [])
        self.assertEqual(read_json(folder / "source-plan.json")["settings_provenance"], "posthoc_generation_config")
        self.assertEqual(self.catalog.history()[0]["state"], "legacy")

    def test_admission_rejects_zero_unknown_fields_model_and_unadopted(self):
        for changes in ({"candidate_ids":[]}, {"candidate_ids":["missing"]}, {"candidate_ids":[self.ids[0]]*2},
                        {"selection_revision":True}, {"command":"evil"}, {"model":"changed"},
                        {"limits":{}}, {"acknowledge_unknown":True}, {"synopsis_refs":{"missing":None}}):
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                self.start(self.request(**changes))
        self.sel = self.selection.update(self.rid, [{"candidate_id":self.ids[0], "state":"held"}], expected_revision=1)
        with self.assertRaises(ConfigError):
            self.start(self.request(kind="narrate"))
        self.assertEqual(list(self.jobs.root.glob("job-*")), [])

    def test_admission_rejects_settings_changed_after_request_built(self):
        # WB-UI-021 review item 1: a request frozen at page-render time (still
        # holding the old backend/model/limits) must be rejected -- not
        # silently admitted under today's settings.json -- once the operator
        # has since changed 文章生成 on /configs.
        req = self.request()
        self.set_generation("openai", "changed-model")
        with self.assertRaises(ConfigError) as caught:
            self.start(req)
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(caught.exception.field_errors,
            {"backend": "文章生成の設定が変更されています。画面を開き直してください"})
        self.assertEqual(list(self.jobs.root.glob("job-*")), [])

    def test_http_admission_conflict_when_settings_changed_is_409(self):
        server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        server.repository = RunRepository(self.configs.runs)
        server.job_store = self.jobs
        server.settings_path = self.settings_path
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        req = self.request()
        self.set_generation("openai", "changed-model")
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        conn.request("POST", "/api/jobs", body=json.dumps(req),
            headers={"Content-Type": "application/json", "X-WorldBloom-Client": "1"})
        response = conn.getresponse()
        status = response.status
        data = json.loads(response.read())
        conn.close()
        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "conflict")
        self.assertEqual(list(self.jobs.root.glob("job-*")), [])

    def test_frozen_input_and_legacy_prompt_bytes_for_two_genres(self):
        from gapengine.qd import read_rows
        from gapengine.synopsis import load_world_meta, build_synopsis_prompt, build_narration_prompt
        from gapengine.scenes import extract_scenes
        for genre in ("romance", "detective"):
            cfg = self.configs.save({"label":genre, "project_id":genre, "template_id":genre},
                config_id="cfg-"+genre)
            for kind in ("synopsize", "narrate"):
                req = self.request(genre+"-"+kind, kind=kind, config=cfg["config_id"], mode="regenerate")
                job = self.start(req)
                end = self.finish(job)
                self.assertEqual(end["state"], "succeeded", end)
                folder = self.outputs.folder(job["output_id"])
                request = self.outputs.request(job["output_id"])
                meta = load_world_meta(folder / "inputs/projects" / genre, folder / "inputs/templates" / genre)
                for c in read_json(folder / "source-plan.json")["candidates"]:
                    elite = {**read_json(self.run / "archive.json")["cells"][c["cell_key"]], "cell":c["cell_key"]}
                    scenes = extract_scenes(read_rows(self.run / c["log"]["relative_path"]), meta)
                    expected = build_synopsis_prompt(elite, scenes, meta) if kind == "synopsize" else build_narration_prompt(elite, scenes, meta, synopsis=None)
                    self.assertEqual((folder / "items" / c["candidate_id"] / "prompt.txt").read_bytes(), expected.encode())
                self.assertEqual(request["selection_sha256"], sha256((folder / "selection.json").read_bytes()))
                self.assertTrue(all(v is None for v in request["synopsis_refs"].values()))

    def test_success_preserved_new_version_and_explicit_unknown_retry(self):
        self.fixture_transport("mixed")
        job = self.launch_fixture(self.request())
        end = self.finish(job)
        self.assertEqual((end["state"],end["completion_kind"]),("partial","partial_unknown"), end)
        first = self.jobs.output(job["output_id"])
        successful = next(e for e in first["entries"] if e["status"] == "ok")
        unknown = next(e for e in first["entries"] if e["status"] == "unknown")
        before = (self.outputs.folder(job["output_id"]) / successful["text_ref"]).read_bytes()
        with patch("execution.output_requests.generation_availability", return_value={"available":True}):
            with self.assertRaises(ConfigError):
                self.start(self.request("no-repeat"))
            for attempts in ([], ["attempt-wrong"]):
                with self.assertRaises(ConfigError):
                    self.start(self.request("bad-ack", acknowledge_unknown=True, attempt_ids=attempts))
            second = self.start(self.request("confirmed", candidate_ids=[unknown["candidate_id"]],
                acknowledge_unknown=True, attempt_ids=[unknown["attempt_id"]]))
        self.finish(second)
        self.assertNotEqual(second["output_id"], job["output_id"])
        self.assertEqual(before, (self.outputs.folder(job["output_id"]) / successful["text_ref"]).read_bytes())
        self.assertEqual(self.outputs.sink(job["output_id"], unknown["candidate_id"]).current(), unknown)

    def test_stop_and_crash_confirm_descendant_exit_and_never_resend(self):
        self.fixture_transport("tree")
        for crash in (False, True):
            marker = self.base / "descendant.json"
            if marker.exists(): marker.unlink()
            job = self.launch_fixture(self.request("crash" if crash else "stop", mode="regenerate"))
            support.until(marker.exists)
            identity = worker.process_identity(read_json(marker)["pid"])
            self.identities.append(identity)
            if crash:
                self.assertTrue(worker.terminate_verified(self.jobs._read(job["job_id"])["receipt"]["identity"]))
            else:
                self.jobs.cancel(job["job_id"])
            end = self.finish(job)
            self.assertEqual(end["state"], "interrupted" if crash else "cancelled", end)
            self.assertEqual(worker.probe(identity), "dead")
            output = self.jobs.output(job["output_id"], recover=True)
            self.assertEqual(output["counts"].get("unknown"), 1)
            self.assertEqual(sum(output["counts"].values()), 2)
            with patch("execution.jobs.subprocess.Popen") as called:
                JobStore(self.configs).get(job["job_id"])
                self.jobs.output(job["output_id"], recover=True)
            called.assert_not_called()
            # The next case has a fresh candidate identity set, not a retry of unknown.
            if not crash:
                other = self.configs.runs / "other"; shutil.copytree(self.run, other)
                self.rid = self.catalog.register_legacy(other.name)
                self.ids = sorted(self.catalog.representatives(self.catalog.snapshot(self.rid)).values())
                self.sel = self.selection.update(self.rid, [{"candidate_id":cid,"state":"adopted"} for cid in self.ids], expected_revision=0)

    def test_http_generation_zero_rejected_and_output_poll_is_read_only(self):
        server = ViewerServer(("127.0.0.1",0), ViewerHandler)
        server.repository = RunRepository(self.configs.runs)
        server.job_store = self.jobs
        server.settings_path = self.settings_path
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def call(method, path, body=None, extra=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            headers = {"Content-Type":"application/json", "X-WorldBloom-Client":"1", **(extra or {})}
            conn.request(method,path,body=None if body is None else json.dumps(body),headers=headers)
            response=conn.getresponse(); status=response.status; data=json.loads(response.read()); conn.close()
            return status,data
        self.assertEqual(call("POST","/api/jobs",self.request(candidate_ids=[]))[0],422)
        self.assertEqual(call("POST","/api/jobs",self.request(),{"Origin":"http://foreign.example"})[0],403)
        status, job = call("POST","/api/jobs",self.request())
        self.assertEqual(status,202,job)
        end=self.finish(job);self.assertEqual(end["state"],"succeeded",end)
        self.assertEqual(call("POST","/api/jobs",self.request())[0],200)
        for _ in range(2):
            status,output=call("GET","/api/outputs/"+job["output_id"])
            self.assertEqual(status,200,output);self.assertEqual(output["counts"],{"prompt_only":2})
        self.assertEqual(call("POST","/api/outputs/"+job["output_id"]+"/recover",{})[0],200)
        self.assertEqual(len(list(self.outputs.root.glob("out-*"))),1)
        self.assertEqual(call("GET","/api/outputs/out-missing")[0],404)
        broken=self.outputs.root/"out-broken";broken.mkdir()
        (broken/"request-seal.json").write_text("{not json",encoding="utf-8")
        status,listing=call("GET","/api/outputs")
        self.assertEqual(status,200,listing)
        rows={row["output_id"]:row for row in listing["outputs"]}
        self.assertEqual(rows[job["output_id"]]["counts"],{"prompt_only":2})
        self.assertEqual(rows["out-broken"]["error"]["code"],"storage_error",rows["out-broken"])
        (broken/"request-seal.json").write_text(json.dumps({"sha256":"0"*64,"items":{}}),encoding="utf-8")
        (broken/"request.json").write_text("{}",encoding="utf-8")
        status,listing=call("GET","/api/outputs")
        self.assertEqual(status,200,listing)
        self.assertEqual({r["output_id"]:r.get("error",{}).get("code") for r in listing["outputs"]},{job["output_id"]:None,"out-broken":"snapshot_changed"})

    def test_runtime_and_input_snapshots_are_used_and_selection_stays_locked(self):
        self.fixture_transport("ok", delay=2)
        job=self.launch_fixture(self.request())
        support.until(lambda:self.jobs.get(job["job_id"])["phase"] == "generating")
        with self.assertRaises(ConfigError):
            self.selection.update(self.rid,[{"candidate_id":self.ids[0],"state":"held"}],expected_revision=1)
        with self.assertRaises(ConfigError):
            self.jobs.submit({"request_id":"parallel-ga","kind":"evolve","config_id":"cfg-test"})
        folder=self.outputs.folder(job["output_id"])
        frozen=(folder/"inputs/projects/romance/world.yaml").read_bytes()
        (self.repo/"projects/romance/world.yaml").write_text("invalid later input",encoding="utf-8")
        (self.repo/"execution/generation.py").write_text("raise RuntimeError('later code')",encoding="utf-8")
        end=self.finish(job)
        self.assertEqual((end["state"],end["completion_kind"]),("succeeded","generated"),end)
        self.assertEqual((folder/"inputs/projects/romance/world.yaml").read_bytes(),frozen)
        self.outputs.verify_artifacts(job["output_id"])
        from execution.output_worker import run
        with patch("execution.output_worker.run_generation") as called,self.assertRaises(FileExistsError):
            run(self.configs.control,job["output_id"])
        called.assert_not_called()

    def test_wall_limit_stops_started_call_and_keeps_unstarted_as_limit(self):
        self.fixture_transport("ok",delay=30)
        self.set_generation("openai","fixture-model",{"max_calls":2,"call_timeout_seconds":10,
            "wall_seconds":12,"max_saved_response_bytes":4096})
        job=self.launch_fixture(self.request())
        end=self.finish(job)
        self.assertEqual(end["state"],"failed",end)
        doc=self.jobs.output(job["output_id"],recover=True)
        self.assertEqual(doc["counts"],{"skipped_limit":1,"unknown":1},doc)
        self.assertEqual(len(read_json(self.outputs.folder(job["output_id"])/"quota.json")["started"]),1)

    def test_wall_limit_after_first_success_is_partial_and_keeps_wall_timeout(self):
        self.fixture_transport("slow_second")
        self.set_generation("openai","fixture-model",{"max_calls":2,"call_timeout_seconds":120,
            "wall_seconds":25,"max_saved_response_bytes":4096})
        job=self.launch_fixture(self.request())
        end=support.until(lambda:(j if (j:=self.jobs.get(job["job_id"]))["state"] in worker.TERMINAL else None),timeout=60)
        support.until(lambda:worker.output_tree_stopped(self.jobs._read(job["job_id"])),timeout=60)
        self.assertEqual((end["state"],end["completion_kind"]),("partial","partial_unknown"),end)
        self.assertEqual(end["error"],{"code":"wall_timeout"},end)
        self.assertEqual(end["counts"],{"ok":1,"unknown":1},end)

    def test_broken_output_recovery_does_not_block_job_ledger(self):
        with patch("execution.jobs.subprocess.Popen",side_effect=OSError("fixture launch failed")),self.assertRaises(ConfigError):
            self.start()
        path=next(self.jobs.root.glob("job-*/job.json"))
        job=read_json(path);job.update(state="queued",created_at=time.time()-self.jobs.startup_timeout-1,launch_identity=None,receipt=None)
        atomic_json(path,job)
        self.outputs.folder(job["output_id"]).mkdir(parents=True)
        with patch("execution.output_store.OutputStore.recover",side_effect=ConfigError("output","broken",code="snapshot_changed")) as recover:
            listed=self.jobs.list()
        recover.assert_called_once()
        self.assertEqual((listed[0]["state"],listed[0]["reconciliation"]),("interrupted","unknown"),listed)
        self.assertNotIn("completion_kind",listed[0])
        self.assertEqual(read_json(path)["state"],"interrupted")

    def test_recorded_ga_to_selected_generation_preserves_publication_and_checks_input(self):
        from test_gapengine import make_reaching_project
        source=make_reaching_project(self.base)
        shutil.copytree(source,self.repo/"projects"/"reaching")
        cfg=self.configs.save({"label":"real-ga", "project_id":"reaching", "template_id":"momotaro",
            "evolution":{"generations":2,"population":2,"seeds":1,"keep":"all"}},config_id="cfg-reaching")
        ga=self.jobs.submit({"request_id":"real-ga","kind":"evolve","config_id":cfg["config_id"]})[0]
        end=self.finish(ga);self.assertEqual(end["state"],"succeeded",end)
        self.rid=ga["run_id"]
        snapshot=self.catalog.snapshot(self.rid)
        self.ids=sorted(c["candidate_id"] for c in snapshot["candidates"]["candidates"] if c["screenable"])
        self.assertGreaterEqual(len(self.ids),2)
        self.sel=self.selection.update(self.rid,[{"candidate_id":cid,"state":"adopted"} for cid in self.ids],expected_revision=0)
        root=self.configs.runs/self.rid
        before={p.relative_to(root).as_posix():p.read_bytes() for p in (root/"published").rglob("*") if p.is_file()}
        with self.assertRaises(ConfigError):self.start(self.request("wrong-input",config="cfg-test"))
        job=self.start(self.request("real-generation",kind="narrate",config=cfg["config_id"]))
        end=self.finish(job);self.assertEqual(end["completion_kind"],"prompt_only",end)
        output=self.jobs.output(job["output_id"])
        self.assertEqual(output["request"]["settings_provenance"],"recorded_input")
        self.assertEqual(output["request"]["input_manifest_sha256"],self.configs.verify_run(self.rid)["input_manifest_sha256"])
        self.assertEqual(output["request"]["candidate_ids"],self.ids)
        self.assertEqual(before,{p.relative_to(root).as_posix():p.read_bytes() for p in (root/"published").rglob("*") if p.is_file()})
        self.assertEqual(next(h for h in self.catalog.history() if h["run_id"]==self.rid)["job_id"],ga["job_id"])

    def test_cancel_between_process_group_creation_and_worker_claim_is_persisted(self):
        with patch("execution.jobs.subprocess.Popen",side_effect=OSError("fixture launch failed")),self.assertRaises(ConfigError):
            self.start()
        path=next(self.jobs.root.glob("job-*/job.json"))
        job=read_json(path);job.update(state="queued",created_at=time.time(),launch_identity=None,receipt=None)
        atomic_json(path,job)
        with patch("execution.worker.output_tree_stopped",return_value=False):
            stopped=self.jobs.cancel(job["job_id"])
        self.assertEqual(stopped["state"],"stopping")
        self.assertIsNotNone(read_json(path)["cancel_requested_at"])

    def test_real_cli_boundary_drains_descendants_on_success_timeout_and_nonzero(self):
        req={"limits":{"max_saved_response_bytes":32,"call_timeout_seconds":1},"prompt":"hello"}
        for mode in ("success","timeout","nonzero"):
            marker=self.base/(mode+"-child.json")
            program=("import subprocess,sys,time,pathlib,json; c=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(300)']); "
                f"pathlib.Path({str(marker)!r}).write_text(json.dumps({{'pid':c.pid}})); "
                + ("time.sleep(30)" if mode=="timeout" else "sys.stdout.write('x'*100);sys.stdout.flush();"+ ("sys.exit(3)" if mode=="nonzero" else "")))
            if mode=="success":
                response=transport(req,[sys.executable,"-B","-c",program])
                self.assertTrue(response.truncated);self.assertEqual(len(response.raw),32)
            else:
                with self.assertRaises(BoundaryError) as error:
                    transport(req,[sys.executable,"-B","-c",program])
                self.assertEqual(error.exception.code,"transport_timeout" if mode=="timeout" else "process_exit_unknown")
            self.assertIsNone(worker.process_identity(read_json(marker)["pid"]))


if __name__ == "__main__":
    unittest.main()
