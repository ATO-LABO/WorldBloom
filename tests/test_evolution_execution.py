"""UI004: deterministic observation, publication integrity and safe boundaries."""
from copy import deepcopy
import http.client
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
import yaml
from unittest.mock import Mock, patch

from execution.configs import ConfigStore
from execution.jobs import JobStore
from execution import worker
from execution.evolution_worker import EvolutionObserver, candidate_identity, log_observation
from execution.provenance import ConfigError, atomic_json, canonical, read_json, sha256
from gapengine.evolve import EvolutionCancelled, evolve
from gapengine.qd import Archive
from test_gapengine import make_reaching_project, ROOT, TEMPLATE
from viewer.data import RunRepository
from viewer.server import ViewerServer, ViewerHandler


def files(root, pattern="*"):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob(pattern)
            if p.is_file() and not any(part in ("evaluation", "published") for part in p.relative_to(root).parts)
            and p.name != ".write.lock"}


class EvolutionExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui004-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = make_reaching_project(self.base)
        self.cfg = {"project": self.project, "template": TEMPLATE, "generations": 2,
                    "population": 3, "seeds": 2, "seed_base": 3, "ga_seed": 19,
                    "keep": "all", "record_explanations": True}

    def run_observed(self, name, cfg=None, **kwargs):
        root = self.base / name
        cfg = {**self.cfg, **(cfg or {}), "out": root}
        with EvolutionObserver(root, name, cfg, **kwargs) as observer:
            evolve(cfg, observer=observer)
        return root, observer

    def test_serial_parallel_observed_unobserved_equal_and_progress(self):
        for coevolve in (False, True):
            baseline = self.base / f"baseline-{coevolve}"
            cfg = {**self.cfg, "coevolve": coevolve, "out": baseline}
            evolve(cfg)
            expected = files(baseline)
            for processes in (1, 2):
                updates = []
                root, observer = self.run_observed(f"observed-{coevolve}-{processes}",
                    {"coevolve": coevolve, "processes": processes}, sink=updates.append)
                self.assertEqual(expected, files(root))
                self.assertEqual(updates[0]["completed_generations"], 0)
                factor = 2 if coevolve else 1
                self.assertEqual(updates[-1]["total_individuals"], 6 * factor)
                self.assertEqual(updates[-1]["completed_individuals"], 6 * factor)
                self.assertEqual(updates[-1]["completed_seeds"], 12 * factor)
                self.assertEqual(updates[-1]["total_seeds"], 12 * factor)
                self.assertTrue(any(0 < p["completed_seeds"] < 12 * factor and
                                    p["completed_generations"] < 2 for p in updates))
                before = len(observer.completed)
                observer.poll(); observer.poll()
                self.assertEqual(len(observer.completed), before)
                self.verify_publication(root, 2, 12 * factor)

    def verify_publication(self, root, revision, count):
        pointer = read_json(root / "published/current.json")
        self.assertEqual(pointer["revision"], revision)
        manifest_raw = (root / f"published/{revision}/manifest.json").read_bytes()
        self.assertEqual(pointer["manifest_sha256"], sha256(manifest_raw))
        manifest = json.loads(manifest_raw)
        for record in manifest["files"].values():
            self.assertEqual(record["sha256"], sha256((root / record["path"]).read_bytes()))
        catalog = read_json(root / manifest["files"]["candidates"]["path"])
        self.assertEqual(len(catalog["candidates"]), count)
        self.assertEqual(len({c["candidate_id"] for c in catalog["candidates"]}), count)
        for c in catalog["candidates"]:
            self.assertEqual(c["candidate_id"], "cand-" + sha256(canonical(c["identity"])))
            self.assertEqual(c["identity"]["source_log_sha256"], c["source_log_sha256"])
        archive = Archive.load(root / manifest["files"]["archive"]["path"])
        self.assertEqual(archive.to_dict(), read_json(root / "archive.json"))
        return catalog

    def test_pruning_records_predelete_hash_and_unreached_candidates(self):
        for target in (False, True):
            if target:
                world_path = self.project / "world.yaml"
                world = yaml.safe_load(world_path.read_text("utf-8"))
                world["scheduled_events"] = []
                world_path.write_text(yaml.safe_dump(world, allow_unicode=True), encoding="utf-8")
            cfg = {"keep": "exemplar", "coevolve": True}
            root, observer = self.run_observed("unreached" if target else "prune", cfg)
            catalog = self.verify_publication(root, 2, 24)
            candidates = catalog["candidates"]
            self.assertTrue(any(c["log"]["availability"] == "pruned" for c in candidates))
            for c in candidates:
                if c["log"]["availability"] == "present":
                    self.assertEqual(c["source_log_sha256"], sha256((root / c["log"]["relative_path"]).read_bytes()))
                else:
                    self.assertEqual(c["log"]["availability"], "pruned")
                    self.assertIsNone(c["log"]["observed_sha256"])
            if target:
                self.assertTrue(all(not c["reached"] for c in candidates))
                self.assertEqual(read_json(root / "archive.json")["cells"], {})
            first = read_json(root / "published/1/candidates.json")["candidates"]
            later = {c["candidate_id"]: c for c in candidates}
            self.assertTrue(all(c == later[c["candidate_id"]] for c in first))

    def test_cancel_before_seed_during_seed_and_between_generations(self):
        for boundary in ("preparing", "seed", "generation"):
            root = self.base / boundary
            cfg = {**self.cfg, "out": root}
            stop = threading.Event()
            saved = {}
            def sink(progress):
                if boundary == "preparing" or (boundary == "seed" and progress["completed_seeds"] >= 1):
                    stop.set()
                if boundary == "generation" and progress["completed_generations"] == 1:
                    saved.update({p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "published").rglob("*.json")})
                    stop.set()
            with self.assertRaises(EvolutionCancelled):
                with EvolutionObserver(root, boundary, cfg, sink=sink, cancellation=stop.is_set) as observer:
                    evolve(cfg, observer=observer)
            if boundary == "generation":
                self.assertTrue(saved)
                self.assertEqual(read_json(root / "published/current.json")["revision"], 1)
                for path, data in saved.items():
                    self.assertEqual((root / path).read_bytes(), data)
                self.assertFalse((root / "g1").exists())
            else:
                self.assertFalse((root / "published/current.json").exists())
                if boundary == "seed":
                    self.assertGreaterEqual(len(observer.completed), 1)
                    self.assertLess(observer.progress()["completed_seeds"], observer.progress()["total_seeds"])
                else:
                    self.assertFalse(list(root.rglob("layers.jsonl")))

    def test_publication_failure_preserves_previous_revision(self):
        from execution import evolution_worker as module
        original = module.atomic_json
        root = self.base / "failure"
        cfg = {**self.cfg, "out": root}
        saved = {}
        def fail_pointer(path, value):
            if path.name == "current.json" and value["revision"] == 2:
                raise OSError("injected pointer write failure")
            return original(path, value)
        def sink(progress):
            if progress["completed_generations"] == 1 and not saved:
                saved.update({p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "published").rglob("*.json")})
        with patch.object(module, "atomic_json", side_effect=fail_pointer), self.assertRaises(OSError):
            with EvolutionObserver(root, "failure", cfg, sink=sink) as observer:
                evolve(cfg, observer=observer)
        self.assertEqual(read_json(root / "published/current.json")["revision"], 1)
        for path, data in saved.items():
            self.assertEqual((root / path).read_bytes(), data)
        self.assertEqual(observer.completed_generations, 1)

    def test_failed_seed_never_published(self):
        import gapengine.evolve as module
        root = self.base / "seed-failure"
        cfg = {**self.cfg, "out": root}
        with patch.object(module.Simulation, "run", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                with EvolutionObserver(root, "seed-failure", cfg) as observer:
                    evolve(cfg, observer=observer)
        self.assertEqual(observer.completed, {})
        self.assertTrue(list(observer.events.glob("*-started.json")))
        self.assertFalse((root / "published/current.json").exists())

    def test_next_generation_seed_exception_keeps_prior_logs_and_publication(self):
        import gapengine.evolve as module
        root = self.base / "late-failure"
        cfg = {**self.cfg, "out": root, "coevolve": True}
        original_run = module.Simulation.run
        saved = {}
        fail = threading.Event()
        def sink(progress):
            if progress["completed_generations"] == 1 and not saved:
                saved.update({p.relative_to(root).as_posix(): p.read_bytes()
                    for p in root.rglob("*") if p.is_file() and
                    p.relative_to(root).parts[0] in ("published", "g0")})
                fail.set()
        def simulation_run(simulation):
            if fail.is_set():
                raise RuntimeError("next generation failed")
            return original_run(simulation)
        with patch.object(module.Simulation, "run", simulation_run), self.assertRaises(RuntimeError):
            with EvolutionObserver(root, "late-failure", cfg, sink=sink) as observer:
                evolve(cfg, observer=observer)
        self.assertTrue(saved)
        self.assertEqual(read_json(root / "published/current.json")["revision"], 1)
        for path, data in saved.items():
            self.assertEqual((root / path).read_bytes(), data)
        self.assertEqual(len(observer.completed), 12)
        self.assertTrue(list(observer.events.glob("1-*-started.json")))
        self.assertFalse(list(observer.events.glob("1-*-completed.json")))

    def test_identity_and_log_availability_boundaries(self):
        root = self.base / "states"; root.mkdir()
        path = root / "layers.jsonl"; path.write_bytes(b"original")
        digest = sha256(path.read_bytes())
        event = {"role":"protagonist", "generation":0, "individual_index":0, "seed":3,
                 "source_log_sha256":digest}
        identity, cid = candidate_identity("run-test", event)
        self.assertEqual(log_observation(root, path.name, digest, set())["availability"], "present")
        path.write_bytes(b"changed")
        self.assertEqual(log_observation(root, path.name, digest, set())["availability"], "stale")
        path.unlink()
        self.assertEqual(log_observation(root, path.name, digest, set())["availability"], "missing")
        self.assertEqual(log_observation(root, path.name, digest, {path.name})["availability"], "pruned")
        self.assertEqual(candidate_identity("run-test", event), (identity, cid))
        for key in ("generation", "individual_index", "seed"):
            with self.assertRaises(ValueError):
                candidate_identity("run-test", {**event, key:True})
        for key, value in (("generation",1),("individual_index",1),("seed",4),("role","antagonist")):
            self.assertNotEqual(candidate_identity("run-test", {**event,key:value})[1], cid)
        with self.assertRaises(ConfigError):
            log_observation(root, "../outside", digest, set())
        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                log_observation(root, path.name, digest, set())

    def test_drain_reads_directory_diff_and_ignores_non_json(self):
        root = self.base / "drain"
        cfg = {**self.cfg, "out": root}
        observer = EvolutionObserver(root, root.name, cfg)

        def write_event(event_id):
            atomic_json(observer.events / f"{event_id}.json", {"kind": "completed", "event_id": event_id,
                "role": "protagonist", "generation": 0, "individual_index": 0, "seed": 0})

        write_event("e1")
        write_event("e2")
        (observer.events / "not-an-event.txt").write_text("ignore me", encoding="utf-8")
        import execution.evolution_worker as module
        from unittest.mock import patch
        with patch.object(module, "read_json", wraps=module.read_json) as reads:
            observer.poll()
            self.assertEqual(len(observer.completed), 2)
            self.assertEqual(len(observer.seen), 2)
            write_event("e3")
            observer.poll()
            # Only the new file is read on the second poll.
            self.assertEqual(reads.call_count, 3)
        self.assertEqual(len(observer.completed), 3)
        self.assertEqual(len(observer.seen), 3)


def cleanup_owned_process(identity):
    """Only a confirmed dead/replaced identity makes termination unnecessary."""
    status = worker.probe(identity)
    if status == "unknown":
        # A first identity query may race with exit; require a fresh observation.
        status = worker.probe(identity)
    if status == "dead":
        return
    if status != "alive":
        raise RuntimeError("owned process liveness is unknown")
    try:
        terminated = worker.terminate_verified(identity)
    except OSError:
        # The verified process can exit while its image name is being queried.
        # An OS error alone is not proof of exit (nor permission to kill by PID).
        if worker.probe(identity) == "dead":
            return
        raise
    if not terminated and worker.probe(identity) != "dead":
        raise RuntimeError("owned process termination was not confirmed")


def cleanup_http_fixture(server, thread, jobs_root, temporary):
    errors = []
    def attempt(action):
        try:
            action()
        except Exception as error:
            errors.append(error)
    attempt(server.shutdown)
    attempt(server.server_close)
    def join_server():
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("HTTP fixture thread did not stop")
    attempt(join_server)
    def cleanup_job(path):
        raw = read_json(path)
        identity = (raw.get("receipt") or {}).get("identity") or raw.get("launch_identity")
        if identity:
            cleanup_owned_process(identity)
    def cleanup_jobs():
        if jobs_root.exists():
            for path in jobs_root.glob("job-*/job.json"):
                attempt(lambda path=path: cleanup_job(path))
    attempt(cleanup_jobs)
    def cleanup_files():
        for _ in range(50):
            try:
                temporary.cleanup()
                return
            except PermissionError:
                time.sleep(0.1)
        temporary.cleanup()
    attempt(cleanup_files)
    if errors:
        raise ExceptionGroup("HTTP fixture cleanup failed", errors)


class CleanupBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.identity = {"pid":123, "created":456, "image":"owned-python"}

    def test_dead_or_replaced_identity_is_not_terminated(self):
        # probe returns dead both for exit and PID reuse with a different identity.
        with patch.object(worker, "probe", return_value="dead"), patch.object(worker, "terminate_verified") as terminate:
            cleanup_owned_process(self.identity)
            terminate.assert_not_called()
        with patch.object(worker, "probe", side_effect=["unknown", "dead"]), patch.object(worker, "terminate_verified") as terminate:
            cleanup_owned_process(self.identity)
            terminate.assert_not_called()

    def test_exit_during_identity_query_is_accepted_only_after_confirmation(self):
        error = PermissionError("image query failed")
        with patch.object(worker, "probe", side_effect=["alive", "dead"]), patch.object(worker, "terminate_verified", side_effect=error):
            cleanup_owned_process(self.identity)
        for status in ("unknown", "alive"):
            with self.subTest(status=status), patch.object(worker, "probe", side_effect=["alive", status]), patch.object(worker, "terminate_verified", side_effect=error):
                with self.assertRaises(PermissionError) as raised:
                    cleanup_owned_process(self.identity)
                self.assertIs(raised.exception, error)

    def test_alive_owned_process_uses_verified_termination(self):
        with patch.object(worker, "probe", return_value="alive"), patch.object(worker, "terminate_verified", return_value=True) as terminate:
            cleanup_owned_process(self.identity)
            terminate.assert_called_once_with(self.identity)

    def test_unknown_is_not_killed_and_false_termination_is_checked(self):
        with patch.object(worker, "probe", return_value="unknown"), patch.object(worker, "terminate_verified") as terminate:
            with self.assertRaises(RuntimeError):
                cleanup_owned_process(self.identity)
            terminate.assert_not_called()
        for status in ("dead", "alive", "unknown"):
            with self.subTest(status=status), patch.object(worker, "probe", side_effect=["alive",status]), patch.object(worker, "terminate_verified", return_value=False):
                if status == "dead":
                    cleanup_owned_process(self.identity)
                else:
                    with self.assertRaises(RuntimeError):
                        cleanup_owned_process(self.identity)

    def test_remaining_jobs_and_files_are_cleaned_after_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index in range(2):
                path = root / f"job-{index}" / "job.json"; path.parent.mkdir()
                path.write_text(json.dumps({"launch_identity":{"pid":index}}), encoding="utf-8")
            server, thread, temporary = Mock(), Mock(), Mock()
            thread.is_alive.return_value = False
            server.shutdown.side_effect = OSError("shutdown failed")
            with patch(__name__ + ".cleanup_owned_process", side_effect=[OSError("process unknown"),None]) as cleanup:
                with self.assertRaises(ExceptionGroup) as raised:
                    cleanup_http_fixture(server, thread, root, temporary)
                self.assertEqual(cleanup.call_count, 2)
            self.assertEqual(len(raised.exception.exceptions), 2)
            server.server_close.assert_called_once()
            thread.join.assert_called_once_with(timeout=5)
            temporary.cleanup.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows process handles")
    def test_exited_handle_alive_process_and_identity_mismatch(self):
        import subprocess, sys
        process = subprocess.Popen([sys.executable, "-B", "-c", "import time;time.sleep(30)"])
        try:
            identity = worker.process_identity(process.pid)
            self.assertIsNotNone(identity)
            cleanup_owned_process({**identity, "created":identity["created"] + 1})
            self.assertIsNone(process.poll())
            cleanup_owned_process(identity)
            process.wait(timeout=5)
            # Keep Popen's native handle alive, reproducing the review's WinError 31.
            cleanup_owned_process(identity)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


@unittest.skipUnless(os.name == "nt", "Windows supervisor contract")
class EvolutionHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui004-http-")
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        for name in ("engine", "gapengine", "scripts", "execution", "projects", "templates"):
            shutil.copytree(ROOT / name, self.repo / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / "requirements.txt", self.repo / "requirements.txt")
        project = make_reaching_project(self.base)
        shutil.copytree(project, self.repo / "projects/momotaro", dirs_exist_ok=True)
        # Controlled slow seed in a private input repository: real GA, pool and
        # frozen worker still run. Production source is never modified.
        engine = self.repo / "gapengine/evolve.py"
        source = engine.read_text("utf-8")
        source = source.replace('        _seed_event(job, seed, "started")',
            '        _seed_event(job, seed, "started")\n        __import__("time").sleep(0.15)')
        engine.write_text(source, encoding="utf-8")
        self.configs = ConfigStore(self.repo, self.base / "control", self.base / "runs")
        self.configs.save({"label":"progress", "project_id":"momotaro", "template_id":"momotaro",
            "evolution":{"generations":2,"population":3,"seeds":2,"processes":2,"coevolve":True}}, config_id="cfg-test")
        self.jobs = JobStore(self.configs, cancel_grace_seconds=5)
        self.server = ViewerServer(("127.0.0.1",0),ViewerHandler)
        self.configs.runs.mkdir(exist_ok=True)
        self.server.repository = RunRepository(self.configs.runs)
        self.server.job_store = self.jobs
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval":0.1}, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        cleanup_http_fixture(self.server, self.thread, self.jobs.root, self.temp)

    def http(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, path, None if body is None else json.dumps(body),
                headers={"Content-Type":"application/json", "X-WorldBloom-Client":"1"})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def run_job(self, request_id, cancel_after_publication=False):
        status, job = self.http("POST", "/api/jobs", {"request_id":request_id,"kind":"evolve","config_id":"cfg-test"})
        self.assertEqual(status, 202, job)
        snapshots, preserved = [], {}
        cancelled = False
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            status, value = self.http("GET", "/api/jobs/" + job["job_id"])
            self.assertEqual(status, 200, value)
            progress = value.get("progress")
            if progress:
                snapshots.append(progress)
                if cancel_after_publication and progress.get("completed_generations") == 1 and not cancelled:
                    root = self.configs.runs / job["run_id"]
                    preserved = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "published").rglob("*.json")}
                    self.assertEqual(self.http("POST", "/api/jobs/" + job["job_id"] + "/cancel", {})[0], 202)
                    cancelled = True
            if value["state"] in worker.TERMINAL:
                break
            time.sleep(0.05)
        else:
            self.fail("real GA did not reach terminal state")
        return job, value, snapshots, preserved

    def test_real_frozen_adapter_progress_and_publication_over_http(self):
        job, result, updates, _ = self.run_job("http-progress")
        self.assertEqual(result["state"], "succeeded", result)
        self.assertTrue(any(0 < p["completed_seeds"] < 24 for p in updates))
        self.assertEqual(result["publication_revision"], 2)
        self.assertEqual(result["progress"]["completed_seeds"], 24)
        self.assertEqual(result["progress"]["completed_individuals"], 12)
        self.assertEqual(result["progress"]["human_quality"], "not_evaluated")
        self.assertEqual(read_json(self.configs.runs / job["run_id"] / "published/current.json")["revision"], 2)
        self.configs.verify_run(job["run_id"])

    def test_http_cancel_keeps_published_generation_and_rerun_has_new_id(self):
        job, result, updates, preserved = self.run_job("http-cancel", True)
        self.assertEqual(result["state"], "cancelled", result)
        self.assertEqual(result["publication_revision"], 1)
        self.assertTrue(preserved)
        root = self.configs.runs / job["run_id"]
        for path, data in preserved.items():
            self.assertEqual((root / path).read_bytes(), data)
        self.configs.verify_run(job["run_id"])
        next_job, final, _, _ = self.run_job("http-rerun")
        self.assertNotEqual(next_job["run_id"], job["run_id"])
        self.assertEqual(final["state"], "succeeded", final)
        for path, data in preserved.items():
            self.assertEqual((root / path).read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
