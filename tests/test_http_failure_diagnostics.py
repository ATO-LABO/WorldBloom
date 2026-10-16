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
