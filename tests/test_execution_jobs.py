"""Windows process, persistence, idempotency and HTTP boundaries for UI-003."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from execution.configs import ConfigStore
from execution.jobs import JobStore
from execution.provenance import ConfigError, atomic_json, read_json
from execution import worker
from viewer.data import RunRepository
from viewer.server import ViewerServer, ViewerHandler

ROOT = Path(__file__).resolve().parents[1]


def until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition did not become true")


@unittest.skipUnless(os.name == "nt", "Windows supervisor contract")
class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui003-")
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        for name in ("engine", "gapengine", "scripts", "execution", "projects", "templates"):
            shutil.copytree(ROOT / name, self.repo / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / "requirements.txt", self.repo / "requirements.txt")
        self.configs = ConfigStore(self.repo, self.base / "control", self.base / "runs")
        self.configs.save({"label":"jobs", "project_id":"romance", "template_id":"romance",
                           "generation":{"backend":"none"},
                           "evolution":{"generations":1,"population":1,"seeds":1}}, config_id="cfg-test")
        self.jobs = JobStore(self.configs, cancel_grace_seconds=0.3)
        self.identities = []
        self.addCleanup(self.cleanup_jobs)

    def cleanup_jobs(self):
        if self.jobs.root.exists():
            for folder in self.jobs.root.glob("job-*"):
                job = read_json(folder / "job.json")
                identity = (job.get("receipt") or {}).get("identity") or job.get("launch_identity")
                if identity:
                    self.identities.append(identity)
        for identity in self.identities:
            try:
                worker.terminate_verified(identity)
            except OSError:
                pass
        # Windows releases handles asynchronously after process termination.
        for _ in range(30):
            try:
                self.temp.cleanup()
                return
            except PermissionError:
                time.sleep(0.1)
        self.temp.cleanup()

    def fixture(self, mode="success", delay=0.2):
        source = f'''import argparse, json, pathlib, subprocess, sys, time
p=argparse.ArgumentParser();p.add_argument("--out");a,_=p.parse_known_args()
out=pathlib.Path(a.out)
mode={mode!r}
if mode == "tree":
    child=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(300)"])
    (out/"descendant.json").write_text(json.dumps({{"pid":child.pid}}))
(out/"started.json").write_text(json.dumps({{"pid":__import__("os").getpid()}}))
time.sleep({delay!r})
if mode == "fail":
    raise SystemExit(7)
(out/"done.json").write_text("{{}}")
'''
        source = "def build_parser(): pass\ndef main():\n" + "\n".join("    " + line for line in source.splitlines()) + "\nif __name__ == '__main__': main()\n"
        (self.repo / "scripts/evolve.py").write_text(source, encoding="utf-8")

    def start(self, rid="req-test"):
        return self.jobs.submit({"request_id":rid,"kind":"evolve","config_id":"cfg-test"})[0]

    def wait_state(self, jid, states):
        return until(lambda: (j if (j := self.jobs.get(jid))["state"] in states else None))

    def started(self, job):
        marker = self.configs.runs / job["run_id"] / "started.json"
        until(marker.exists)
        return marker.parent

    def test_success_reconnect_and_fixed_manifest(self):
        self.fixture()
        job = self.start()
        result = self.wait_state(job["job_id"], worker.TERMINAL)
        self.assertEqual(result["state"], "succeeded", result)
        another = JobStore(self.configs)
        self.assertEqual(another.get(job["job_id"])["job_id"], job["job_id"])
        self.assertEqual(self.configs.verify_run(job["run_id"])["job_id"], job["job_id"])
        self.assertNotIn("nonce", result)
        self.assertNotIn("entrypoint", result)
        self.assertEqual(another.cancel(job["job_id"])["state"], "succeeded")

    def test_concurrent_same_request_only_one_launch(self):
        self.fixture(delay=2)
        body = {"request_id":"same", "kind":"evolve", "config_id":"cfg-test"}
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: self.jobs.submit(body), range(4)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({j["job_id"] for j, _ in results}), 1)
        self.assertEqual(len(list(self.jobs.root.glob("job-*"))), 1)
        with self.assertRaises(ConfigError) as error:
            self.jobs.submit({**body,"config_id":"different"})
        self.assertEqual(error.exception.code, "conflict")
        with self.assertRaises(ConfigError):
            self.start("another")
        self.wait_state(results[0][0]["job_id"], worker.TERMINAL)

    def test_stop_kills_child_and_descendant_and_keeps_results(self):
        self.fixture("tree", 300)
        job = self.start(); run = self.started(job)
        descendant = worker.process_identity(read_json(run / "descendant.json")["pid"])
        self.assertIsNotNone(descendant)
        stopped = self.jobs.cancel(job["job_id"])
        self.assertEqual(stopped["state"], "stopping")
        final = self.wait_state(job["job_id"], worker.TERMINAL)
        self.assertEqual(final["state"], "cancelled", final)
        until(lambda: worker.probe(descendant) == "dead")
        self.assertTrue((run / "started.json").exists())
        self.assertFalse((run / "done.json").exists())
        self.assertEqual(self.jobs.cancel(job["job_id"])["state"], "cancelled")

    def test_worker_crash_cleans_tree_and_reconciles_interrupted(self):
        self.fixture("tree", 300)
        job = self.start(); run = self.started(job)
        with self.jobs._lock():
            raw = read_json(self.jobs._folder(job["job_id"]) / "job.json")
        descendant = worker.process_identity(read_json(run / "descendant.json")["pid"])
        self.assertTrue(worker.terminate_verified(raw["receipt"]["identity"]))
        self.assertEqual(self.jobs.get(job["job_id"])["state"], "interrupted")
        until(lambda: worker.probe(descendant) == "dead")

    def test_worker_crash_during_preparation_cleans_subprocess(self):
        marker = self.base / "preparing.json"
        path = self.repo / "execution/configs.py"
        source = path.read_text(encoding="utf-8")
        instrumentation = ("import subprocess as _sp, sys as _sys, time as _time, json as _json\n"
            "from pathlib import Path as _Path\n"
            "_child = _sp.Popen([_sys.executable, '-B', '-c', 'import time;time.sleep(300)'])\n"
            f"_Path({str(marker)!r}).write_text(_json.dumps({{'pid':_child.pid}}))\n"
            "_time.sleep(300)\n")
        source=source.replace("from __future__ import annotations\n", "from __future__ import annotations\n"+instrumentation)
        path.write_text(source,encoding="utf-8")
        job=self.start()
        until(marker.exists)
        child=worker.process_identity(read_json(marker)["pid"])
        self.identities.append(child)
        with self.jobs._lock():
            raw=read_json(self.jobs._folder(job["job_id"])/"job.json")
        self.assertEqual(raw["phase"],"preparing")
        self.assertTrue(worker.terminate_verified(raw["receipt"]["identity"]))
        self.assertEqual(self.jobs.get(job["job_id"])["state"],"interrupted")
        until(lambda:worker.probe(child)=="dead")

    def stall_preparation(self, stage):
        marker = self.base / "preparation-tree.json"
        partial = self.configs.runs / ".pending-test" / "partial.txt"
        descendant_code = (
            "import subprocess,sys,time,json,os;from pathlib import Path;"
            "grand=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(300)']);"
            f"p=Path({str(marker)!r});t=p.with_suffix('.tmp');"
            "t.write_text(json.dumps({'child':os.getpid(),'grandchild':grand.pid}));t.replace(p);time.sleep(300)")
        instrumentation = ("import subprocess as _sp, sys as _sys, time as _time\n"
            "from pathlib import Path as _Path\n"
            f"_partial=_Path({str(partial)!r});_partial.parent.mkdir(parents=True,exist_ok=True)\n"
            "_partial.write_text('unfinished preparation')\n"
            f"_child=_sp.Popen([_sys.executable,'-B','-c',{descendant_code!r}])\n"
            "_time.sleep(300)\n")
        path = self.repo / "execution/configs.py"
        source = path.read_text(encoding="utf-8")
        if stage == "import":
            anchor = "from __future__ import annotations\n"
            addition = instrumentation
        else:
            anchor = ("    def prepare_run(self, config_id, *, run_id=None, job_id):\n" if stage == "prepare"
                      else "    def verify_run(self, run_id):\n")
            addition = "".join("        " + line + "\n" for line in instrumentation.splitlines())
        self.assertEqual(source.count(anchor), 1)
        path.write_text(source.replace(anchor, anchor + addition), encoding="utf-8")
        return marker, partial

    def preparation_boundary(self, stage, *, cancel):
        self.fixture()
        self.configs.duplicate("cfg-test", {"execution_limits":{"wall_seconds":5 if not cancel else 60}},
                               new_id="cfg-boundary")
        prior = self.configs.runs / "prior-result" / "archive.json"
        prior.parent.mkdir(parents=True)
        prior.write_bytes(b'{"preserved":true}')
        marker, partial = self.stall_preparation(stage)
        body = {"request_id":"preparation-boundary", "kind":"evolve", "config_id":"cfg-boundary"}
        server = None
        # This test server accepts cancel, but cannot contribute reconciliation.
        if cancel:
            server = self.server()
            server.service_actions = lambda: None
        with patch.object(JobStore, "get", side_effect=AssertionError("get must not assist")), \
             patch.object(JobStore, "list", side_effect=AssertionError("list must not assist")):
            job, _ = self.jobs.submit(body)
            folder = self.jobs._folder(job["job_id"])
            raw = lambda: worker.read_job(self.jobs.root, folder)
            until(marker.exists)
            identities = [worker.process_identity(pid) for pid in read_json(marker).values()]
            self.assertTrue(all(identities))
            self.identities.extend(identities)
            before = raw()
            self.assertEqual(before["phase"], "preparing")
            preparation = before["preparation_identity"]
            self.identities.append(preparation)
            if cancel:
                status, accepted = self.http(server, "POST", "/api/jobs/" + job["job_id"] + "/cancel", {})
                self.assertEqual(status, 202, accepted)
                server.shutdown(); server.server_close()
                deadline = accepted["cancel_requested_at"] + 0.3
            else:
                deadline = before["created_at"] + 5
            final = until(lambda: (j if (j := raw())["state"] in worker.TERMINAL else None), timeout=10)
        self.assertEqual(final["state"], "cancelled" if cancel else "failed", final)
        self.assertEqual(final["error"], None if cancel else {"code":"wall_timeout"})
        self.assertGreaterEqual(final["finished_at"], deadline)
        self.assertLess(final["finished_at"] - deadline, 3)
        # At the terminal record, not merely eventually, every owned process is dead.
        for identity in [preparation, *identities]:
            self.assertEqual(worker.probe(identity), "dead")
        self.assertTrue(partial.is_file())
        self.assertFalse((partial.parent / "complete.json").exists())
        self.assertEqual(prior.read_bytes(), b'{"preserved":true}')
        self.assertFalse((folder / "prepared.json").exists())
        self.assertFalse((folder / "launch.json").exists())
        run = self.configs.runs / job["run_id"]
        self.assertFalse((run / "started.json").exists())
        self.assertFalse((run / "archive.json").exists())

    def test_preparation_wall_without_http_or_reconciliation(self):
        self.preparation_boundary("prepare", cancel=False)

    def test_preparation_cancel_after_http_shutdown_without_reconciliation(self):
        self.preparation_boundary("prepare", cancel=True)

    def test_import_wall_without_http_or_reconciliation(self):
        self.preparation_boundary("import", cancel=False)

    def test_verification_cancel_after_http_shutdown_without_reconciliation(self):
        self.preparation_boundary("verify", cancel=True)

    def test_failure_not_success_and_new_request_new_run(self):
        self.fixture("fail")
        first = self.start()
        result = self.wait_state(first["job_id"], worker.TERMINAL)
        self.assertEqual(result["state"], "failed", result)
        self.assertEqual(result["exit_code"], 7)
        again, created = self.jobs.submit({"request_id":"req-test","kind":"evolve","config_id":"cfg-test"})
        self.assertFalse(created)
        self.assertEqual(again["state"], "failed")
        self.fixture()
        second = self.start("new")
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(self.wait_state(second["job_id"], worker.TERMINAL)["state"], "succeeded")

    def test_wall_limit(self):
        self.configs.duplicate("cfg-test", {"execution_limits":{"wall_seconds":3}}, new_id="cfg-short")
        self.fixture("tree", 300)
        job, _ = self.jobs.submit({"request_id":"limit","kind":"evolve","config_id":"cfg-short"})
        result = self.wait_state(job["job_id"], worker.TERMINAL)
        self.assertEqual(result["state"], "failed", result)
        self.assertEqual(result["error"]["code"], "wall_timeout")

    def test_unknown_liveness_blocks_new_work_and_pid_mismatch_not_killed(self):
        self.fixture(delay=2)
        job = self.start(); self.started(job)
        with patch("execution.jobs.worker.probe", return_value="unknown"):
            self.assertEqual(self.jobs.get(job["job_id"])["reconciliation"], "unknown")
            with self.assertRaises(ConfigError):
                self.start("blocked")
        identity = worker.process_identity(os.getpid())
        self.assertFalse(worker.terminate_verified({**identity,"created":identity["created"]+1}))
        self.assertEqual(worker.probe(identity), "alive")
        self.wait_state(job["job_id"], worker.TERMINAL)

    def test_launch_failure_and_unsupported_generation(self):
        with patch("execution.jobs.subprocess.Popen", side_effect=OSError("sensitive token")):
            with self.assertRaises(ConfigError) as error:
                self.start()
        self.assertEqual(error.exception.code, "unavailable")
        self.assertNotIn("sensitive", str(error.exception))
        self.assertEqual(self.jobs.list()[0]["state"], "failed")
        with self.assertRaises(ConfigError) as error:
            self.jobs.submit({"request_id":"gen","kind":"narrate","config_id":"cfg-test"})
        self.assertEqual(error.exception.code,"unavailable")

    def test_path_and_request_field_rejection(self):
        for extra in ({"argv":["calc"]},{"run_id":"existing"},{"out":"C:/tmp"}):
            with self.assertRaises(ConfigError):
                self.jobs.submit({"request_id":"x","kind":"evolve","config_id":"cfg-test",**extra})
        for bad in ("../x", "x/y", "x\\y", "", None):
            with self.assertRaises(ConfigError):
                self.jobs.get(bad)

    def test_real_small_ga_uses_frozen_input_and_completes(self):
        job = self.start()
        result = self.wait_state(job["job_id"], worker.TERMINAL)
        self.assertEqual(result["state"], "succeeded", result)
        run = self.configs.runs / job["run_id"]
        self.assertTrue((run / "archive.json").is_file())
        self.assertTrue((run / "summary.json").is_file())
        self.configs.verify_run(job["run_id"])
        events = [json.loads(line) for line in (self.jobs._folder(job["job_id"])/"events.jsonl").read_text().splitlines()]
        self.assertEqual(events[-1]["state"], "succeeded")
        self.assertEqual(len({e["revision"] for e in events}), len(events))
        self.assertNotIn("nonce", str(events))

    def test_fixed_future_ga_adapter_receives_job_context(self):
        adapter = self.repo / "execution/evolution_worker.py"
        adapter.write_text("import argparse,json,pathlib\n"
            "p=argparse.ArgumentParser();p.add_argument('--run');p.add_argument('--control');p.add_argument('--job');a=p.parse_args()\n"
            "pathlib.Path(a.run,'adapter.json').write_text(json.dumps(vars(a)))\n",encoding="utf-8")
        job = self.start()
        self.assertEqual(self.wait_state(job["job_id"],worker.TERMINAL)["state"],"succeeded")
        context = read_json(self.configs.runs/job["run_id"]/"adapter.json")
        self.assertEqual(context["job"],job["job_id"])
        self.assertEqual(context["control"],str(self.configs.control))
        launch = read_json(self.jobs._folder(job["job_id"])/"launch.json")
        self.assertEqual(launch["handler"],"evolution_worker")
        self.assertEqual(launch["argv"][3],str(self.configs.runs/job["run_id"]/"runtime/execution/evolution_worker.py"))
        request = read_json(self.jobs._folder(job["job_id"])/"request.json")
        self.assertEqual(request["schema_version"],1)

    def test_two_client_processes_submit_once_and_exit(self):
        self.fixture(delay=2)
        code = ("import json,sys; from execution.configs import ConfigStore; from execution.jobs import JobStore; "
                "from pathlib import Path; c=ConfigStore(*sys.argv[1:4]); "
                "j,created=JobStore(c).submit({'request_id':'clients','kind':'evolve','config_id':'cfg-test'}); "
                "Path(sys.argv[4]).write_text(json.dumps({'job':j,'created':created}))")
        clients = [subprocess.Popen([sys.executable,"-B","-c",code,str(self.repo),str(self.configs.control),
                                    str(self.configs.runs),str(self.base/f"client{i}.json")],cwd=ROOT,
                                   stdout=subprocess.DEVNULL,stderr=subprocess.PIPE) for i in range(2)]
        for client in clients:
            _, error = client.communicate(timeout=20)
            self.assertEqual(client.returncode,0,error)
        results = [read_json(self.base/f"client{i}.json") for i in range(2)]
        self.assertEqual(sum(r["created"] for r in results),1)
        self.assertEqual(results[0]["job"]["job_id"],results[1]["job"]["job_id"])
        self.assertEqual(self.wait_state(results[0]["job"]["job_id"],worker.TERMINAL)["state"],"succeeded")

    def unlaunched_job(self):
        with patch("execution.jobs.subprocess.Popen", side_effect=OSError("no launch")):
            with self.assertRaises(ConfigError):
                self.start()
        path = next(self.jobs.root.glob("job-*/job.json"))
        job = read_json(path)
        job.update(state="queued", launch_identity=None, receipt=None, created_at=time.time()-30,
                   finished_at=None, error=None)
        atomic_json(path,job)
        return job,path

    def test_unlaunched_request_reconciles_without_relaunch(self):
        job,_ = self.unlaunched_job()
        with patch("execution.jobs.subprocess.Popen") as launch:
            self.assertEqual(JobStore(self.configs).get(job["job_id"])["state"],"interrupted")
            launch.assert_not_called()
        self.assertFalse(self.configs.runs.exists())

    def test_cancel_queued_request_is_terminal_before_worker_claim(self):
        job,path = self.unlaunched_job()
        job["created_at"] = time.time(); atomic_json(path,job)
        result = self.jobs.cancel(job["job_id"])
        self.assertEqual(result["state"],"cancelled")
        unchanged = worker.change(self.jobs.root,path.parent,job["nonce"],state="running")
        self.assertEqual(unchanged["state"],"cancelled")
        self.assertFalse(self.configs.runs.exists())

    def test_modified_request_cannot_be_replayed(self):
        job,path = self.unlaunched_job()
        atomic_json(path.parent/"request.json",{"kind":"evolve","config_id":"other"})
        with self.assertRaises(ConfigError) as error:
            self.jobs.get(job["job_id"])
        self.assertEqual(error.exception.code,"snapshot_changed")

    def test_http_response_while_running_and_restart(self):
        self.fixture(delay=3)
        server = self.server()
        status, job = self.http(server, "POST", "/api/jobs", {"request_id":"http","kind":"evolve","config_id":"cfg-test"})
        self.assertEqual(status,202,job)
        self.started(job)
        self.assertEqual(self.http(server,"GET","/api/jobs/"+job["job_id"])[0],200)
        server.shutdown(); server.server_close()
        other = self.server()
        status, current = self.http(other,"GET","/api/jobs/"+job["job_id"])
        self.assertEqual(status,200,current)
        self.assertEqual(current["job_id"],job["job_id"])
        self.assertEqual(self.wait_state(job["job_id"],worker.TERMINAL)["state"],"succeeded")

    def test_http_server_process_exit_does_not_stop_supervisor(self):
        self.fixture(delay=4)
        self.configs.runs.mkdir(exist_ok=True)
        def boot():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]
            process=subprocess.Popen([sys.executable,"-B",str(ROOT/"viewer/server.py"),
                "--runs",str(self.configs.runs),"--control",str(self.configs.control),
                "--repo",str(self.repo),"--port",str(port)],cwd=ROOT,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.addCleanup(process.wait,timeout=10)
            self.addCleanup(process.terminate)
            server=SimpleNamespace(server_port=port)
            def ready():
                self.assertIsNone(process.poll(), "HTTP server exited before becoming ready")
                try:
                    return self.http(server,"GET","/api/jobs")[0]==200
                except (OSError,http.client.HTTPException):
                    return False
            until(ready)
            return process,server
        process,server=boot()
        status,job=self.http(server,"POST","/api/jobs",{"request_id":"server-exit","kind":"evolve","config_id":"cfg-test"})
        self.assertEqual(status,202,job)
        self.started(job)
        process.terminate();process.wait(timeout=10)
        _,server=boot()
        status,current=self.http(server,"GET","/api/jobs/"+job["job_id"])
        self.assertEqual(status,200,current)
        self.assertEqual(current["job_id"],job["job_id"])
        self.assertEqual(self.wait_state(job["job_id"],worker.TERMINAL)["state"],"succeeded")

    def server(self):
        self.configs.runs.mkdir(exist_ok=True)
        server = ViewerServer(("127.0.0.1",0),ViewerHandler)
        server.repository=RunRepository(self.configs.runs)
        server.job_store=JobStore(self.configs,cancel_grace_seconds=0.3)
        thread=threading.Thread(target=server.serve_forever,kwargs={"poll_interval":0.1},daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def http(self,server,method,path,body=None,headers=None):
        conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
        defaults={"Content-Type":"application/json","X-WorldBloom-Client":"1"}
        if headers:
            defaults.update(headers)
        try:
            conn.request(method,path,None if body is None else json.dumps(body),headers=defaults)
            response=conn.getresponse()
            raw=response.read()
            return response.status,json.loads(raw)
        finally:
            conn.close()

    def test_http_security_validation_and_missing_resources(self):
        server=self.server()
        body={"request_id":"x","kind":"evolve","config_id":"cfg-test"}
        for headers in ({"X-WorldBloom-Client":""},{"Origin":"https://evil.example"},
                        {"Host":"evil.example"},{"Sec-Fetch-Site":"cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.http(server,"POST","/api/jobs",body,headers)[0],403)
        self.assertEqual(self.http(server,"POST","/api/jobs",body,{"Content-Type":"text/plain"})[0],400)
        self.assertEqual(self.http(server,"POST","/api/configs/preview",{})[0],422)
        self.assertEqual(self.http(server,"GET","/api/jobs/absent")[0],404)
        self.assertEqual(self.http(server,"GET","/api/configs/absent")[0],404)
        self.assertEqual(list(self.configs.runs.iterdir()),[])


if __name__ == "__main__":
    unittest.main()
