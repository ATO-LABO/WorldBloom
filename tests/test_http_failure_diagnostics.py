"""Failure-only observations are bounded, private and preserve the HTTP assertion."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import http_failure_diagnostics as d
import test_evolution_execution as evolution_tests
import test_execution_jobs as job_tests


class HttpFailureDiagnosticTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wb-http-diagnostic-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.job = self.root / "job"; self.job.mkdir()
        self.run = self.root / "run"; (self.run / "evaluation").mkdir(parents=True)

    def record(self, **kwargs):
        with redirect_stdout(io.StringIO()) as output:
            result = d.record_http_timeout(evidence=self.root / "evidence", job_folder=self.job,
                run_root=self.run, last_http={"state": "running"}, http_status=200,
                probe=kwargs.pop("probe", Mock(return_value="alive")), **kwargs)
        self.assertLessEqual(len(output.getvalue().encode()), d.OUTPUT_LIMIT + 64)
        return result, output.getvalue()

    def test_snapshot_is_allowlisted_bounded_and_does_not_modify_job(self):
        secret = "fixture-only-secret@example.invalid"
        job = {"state": "running", "phase": "evaluating", "revision": 4, "nonce": secret,
               "receipt": {"identity": {"pid": 1, "image": secret}}, "child_identity": {"pid": 2},
               "progress": {"completed_seeds": 3, "metrics": {"secret": secret}, "phase": "evaluating"}}
        # Sensitive test values exercise the real JSON parser and allowlist in
        # memory; they are never written to a job, progress or event file.
        memory_files = {
            self.job / "job.json": json.dumps(job).encode(),
            self.run / "evaluation/progress.json": json.dumps({"completed_seeds": 4, "phase": "publishing", "api_key": secret}).encode(),
            self.job / "events.jsonl": "\n".join(json.dumps({"revision": n, "state": "running", "phase": "evaluating", "secret": secret}) for n in range(1000)).encode(),
        }

        def memory_read(path):
            return io.TextIOWrapper(io.BytesIO(memory_files[Path(path)]), encoding="utf-8")

        probe = Mock(return_value="alive")
        with patch.object(d, "open_atomic_read", side_effect=memory_read) as reader:
            result, output = self.record(probe=probe)
        self.assertEqual(reader.call_count, 3)
        self.assertEqual({call.args[0] for call in reader.call_args_list}, set(memory_files))
        self.assertTrue(all(not path.exists() for path in memory_files))
        self.assertEqual(result["disk_job"]["revision"], 4)
        self.assertEqual(result["ga_progress"]["completed_seeds"], 4)
        self.assertEqual(result["recent_events"][-1]["revision"], 999)
        self.assertLessEqual(len(result["recent_events"]), 16)
        self.assertEqual(probe.call_count, 2)
        self.assertNotIn(secret, output)
        artifact = next((self.root / "evidence/http-timeouts").glob("*.json"))
        self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")), result)
        self.assertNotIn(secret, artifact.read_text(encoding="utf-8"))
        self.assertLessEqual(artifact.stat().st_size, d.OUTPUT_LIMIT + 1)

        # Independently exercise actual file reads and byte preservation with
        # ordinary state/counter data, without persisting sensitive markers.
        plain_job = {"state": "running", "phase": "evaluating", "revision": 4,
                     "receipt": {"identity": {"pid": 1}}, "child_identity": {"pid": 2},
                     "progress": {"completed_seeds": 3, "phase": "evaluating"}}
        (self.job / "job.json").write_text(json.dumps(plain_job), encoding="utf-8")
        (self.run / "evaluation/progress.json").write_text(json.dumps({"completed_seeds": 4, "phase": "publishing"}), encoding="utf-8")
        (self.job / "events.jsonl").write_text("\n".join(json.dumps({"revision": n, "state": "running", "phase": "evaluating"}) for n in range(1000)), encoding="utf-8")
        before = {path: path.read_bytes() for path in memory_files}
        plain_result, _ = self.record()
        self.assertEqual(plain_result["read_status"], {"job": "read", "ga": "read", "events": "bounded_tail"})
        self.assertEqual(plain_result["disk_job"]["revision"], 4)
        self.assertEqual(plain_result["ga_progress"]["completed_seeds"], 4)
        self.assertEqual(plain_result["recent_events"][-1]["revision"], 999)
        self.assertLessEqual(len(plain_result["recent_events"]), 16)
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_oversized_missing_and_malformed_files_do_not_escape_into_output(self):
        (self.job / "job.json").write_bytes(b"x" * (d.JSON_LIMIT + 1))
        (self.job / "events.jsonl").write_bytes(b"not-json\n")
        result, output = self.record()
        self.assertEqual(result["read_status"], {"job": "size_limit", "ga": "missing", "events": "read"})
        self.assertEqual(result["recent_events"], [])
        self.assertNotIn("not-json", output)

    def test_existing_timeout_assertion_and_http_call_count_are_preserved(self):
        case = evolution_tests.EvolutionHttpTests("test_real_frozen_adapter_progress_and_publication_over_http")
        case.jobs = SimpleNamespace(root=self.root)
        case.configs = SimpleNamespace(runs=self.root)
        case.http = Mock(side_effect=[(202, {"job_id": "job", "run_id": "run"}),
                                      (200, {"state": "running", "progress": {"completed_seeds": 3}})])
        with patch("test_evolution_execution.time.monotonic", side_effect=[0, 1, 46]), \
             patch("test_evolution_execution.time.sleep"), \
             patch.dict("os.environ", {"WB_REGRESSION_EVIDENCE_DIR": str(self.root / "evidence")}), \
             redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(AssertionError, "real GA did not reach terminal state"):
                case.run_job("fixture")
        self.assertEqual(case.http.call_count, 2)  # One submit and the original poll only.
        artifact = next((self.root / "evidence/http-timeouts").glob("*.json"))
        self.assertEqual(json.loads(artifact.read_text(encoding="utf-8"))["last_http"]["progress"]["completed_seeds"], 3)

    def startup_case(self):
        case = job_tests.JobTests("test_http_response_while_running_and_restart")
        case.jobs = SimpleNamespace(root=self.root, get=Mock(), cancel=Mock(), _read=Mock())
        case.configs = SimpleNamespace(runs=self.root)
        case.http = Mock()
        return case, {"job_id": "job", "run_id": "run", "state": "queued"}

    def assert_no_startup_calls(self, case):
        for method in (case.http, case.jobs.get, case.jobs.cancel, case.jobs._read):
            method.assert_not_called()

    def test_started_success_does_not_collect_or_probe(self):
        case, job = self.startup_case()
        (self.run / "started.json").write_text("{}", encoding="utf-8")
        with patch.object(job_tests, "record_http_timeout", create=True) as collect, \
             patch.object(job_tests.worker, "probe") as probe:
            self.assertEqual(case.started(job), self.run)
        collect.assert_not_called()
        probe.assert_not_called()
        self.assert_no_startup_calls(case)

    def test_started_timeout_preserves_assertion_and_original_wait(self):
        case, job = self.startup_case()
        original = AssertionError("startup marker timeout")
        with patch.object(job_tests, "until", side_effect=original) as wait, \
             patch.object(job_tests, "record_http_timeout", create=True) as collect, \
             patch.dict("os.environ", {"WB_REGRESSION_EVIDENCE_DIR": str(self.root / "evidence")}):
            with self.assertRaises(AssertionError) as raised:
                case.started(job)
        self.assertIs(raised.exception, original)
        wait.assert_called_once()
        self.assertEqual(wait.call_args.args[0].__self__, self.run / "started.json")
        self.assertEqual(wait.call_args.args[0].__name__, "exists")
        self.assertEqual(wait.call_args.kwargs, {})
        collect.assert_called_once_with(evidence=str(self.root / "evidence"),
            job_folder=self.job, run_root=self.run, last_http=job, http_status=None,
            probe=job_tests.worker.probe)
        self.assert_no_startup_calls(case)

    def test_started_wait_still_has_sixty_second_deadline(self):
        case, job = self.startup_case()
        with patch.object(job_tests.time, "monotonic", side_effect=[0, 59.9, 60]) as clock, \
             patch.object(job_tests.time, "sleep") as sleep, \
             patch.object(job_tests, "record_http_timeout", create=True) as collect:
            with self.assertRaisesRegex(AssertionError, "condition did not become true"):
                case.started(job)
        self.assertEqual(clock.call_count, 3)
        sleep.assert_called_once_with(0.05)
        collect.assert_called_once()
        self.assert_no_startup_calls(case)

    def test_started_diagnostic_and_argument_errors_preserve_assertion(self):
        for broken_argument in (False, True):
            with self.subTest(broken_argument=broken_argument):
                case, job = self.startup_case()
                if broken_argument:
                    del job["job_id"]
                original = AssertionError("original startup failure")
                with patch.object(job_tests, "until", side_effect=original), \
                     patch.object(job_tests, "record_http_timeout", create=True,
                                  side_effect=RuntimeError("diagnostic unavailable")) as collect:
                    with self.assertRaises(AssertionError) as raised:
                        case.started(job)
                self.assertIs(raised.exception, original)
                self.assertEqual(collect.call_count, 0 if broken_argument else 1)
                self.assert_no_startup_calls(case)

    def test_started_non_assertion_failure_is_not_collected(self):
        case, job = self.startup_case()
        original = ValueError("unrelated failure")
        with patch.object(job_tests, "until", side_effect=original), \
             patch.object(job_tests, "record_http_timeout", create=True) as collect:
            with self.assertRaises(ValueError) as raised:
                case.started(job)
        self.assertIs(raised.exception, original)
        collect.assert_not_called()
        self.assert_no_startup_calls(case)

    def test_preparation_process_state_is_allowlisted_and_private(self):
        private_fixture = "fixture-only-preparation@example.invalid"
        identity = {"pid": 3, "image": private_fixture, "nonce": private_fixture}
        for raw, answer in ((identity, "alive"), (identity, "dead"), (identity, "unknown"),
                            (identity, "unexpected"), (identity, OSError("probe unavailable")),
                            (private_fixture, "alive"), (None, "alive")):
            with self.subTest(identity_is_mapping=isinstance(raw, dict), answer=str(answer)):
                job = {"receipt": {"identity": {"pid": 1}}, "child_identity": {"pid": 2},
                       "preparation_identity": raw, "nonce": private_fixture}
                def probe(value):
                    if value is raw:
                        if isinstance(answer, Exception):
                            raise answer
                        return answer
                    return "alive"
                mock_probe = Mock(side_effect=probe)
                with patch.object(d, "read_object", side_effect=[(job, "read"), ({}, "missing")]), \
                     patch.object(d, "recent_events", return_value=([], "missing")):
                    result, output = self.record(probe=mock_probe)
                expected = answer if isinstance(raw, dict) and answer in ("alive", "dead", "unknown") else "unknown"
                self.assertEqual(result["processes"], {"worker": "alive", "child": "alive", "preparation": expected})
                self.assertEqual(mock_probe.call_count, 3 if isinstance(raw, dict) else 2)
                self.assertNotIn(private_fixture, output)
                self.assertNotIn('"pid"', output)
                self.assertNotIn('"identity"', output)
                self.assertNotIn('"nonce"', output)
                artifact = next((self.root / "evidence/http-timeouts").glob("*.json"))
                self.assertNotIn(private_fixture, artifact.read_text(encoding="utf-8"))
                self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")), result)
