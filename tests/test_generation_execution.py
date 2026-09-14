"""UI006 typed boundaries and durable receipts, no real provider calls."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

from execution import generation
from execution.generation import Response, BoundaryError, run_generation, aggregate
from execution.output_store import OutputStore
from execution.provenance import ConfigError, read_json

TEXT = "昔の約束を思い出した二人は、すれ違いの理由を話し合い、互いの気持ちを確かめた。"


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui006-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = OutputStore(self.root)

    def make(self, *, backend="codex-cli", calls=2, output="out-test", count=2, limit=4096):
        ids = ["cand-" + str(i) for i in range(count)]
        request = {"schema_version": 1, "output_id": output, "request_id": "request-test",
            "kind": "synopsize", "run_id": "run-test", "config_id": "cfg-test", "candidate_ids": ids,
            "backend": backend, "model": None if backend == "none" else "explicit-model",
            "limits": {"max_calls": calls, "call_timeout_seconds": 10, "wall_seconds": 100,
                       "max_saved_response_bytes": limit}}
        self.store.create(request, {cid: "固定された事実のみから物語を書く。" for cid in ids})
        return self.store.sink(output, ids[0])

    def call(self, sink, *, response=None, error=None):
        with patch("execution.generation.preflight", return_value=["fixed-cli"]), patch(
                "execution.generation.transport", return_value=response or Response(TEXT.encode()), side_effect=error) as transport:
            value = run_generation(sink.call_request({"api_key": "secret-never-save"}), sink)
        return value, transport

    def test_success_and_prompt_only_do_not_repeat_calls(self):
        sink = self.make(count=1)
        value, called = self.call(sink)
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["call_state"], "response_received")
        called.assert_called_once()
        body = self.store.folder("out-test") / value["text_ref"]
        self.assertEqual(body.read_text(encoding="utf-8"), TEXT)
        receipt = sink.current()
        with self.assertRaises(ConfigError), patch("execution.generation.transport") as again:
            run_generation(sink.call_request(), sink)
        again.assert_not_called()
        self.assertEqual(sink.current(), receipt)
        none = self.make(backend="none", output="out-none", count=1)
        with patch("execution.generation.transport") as external:
            self.assertEqual(run_generation(none.call_request(), none)["status"], "prompt_only")
        external.assert_not_called()
        for p in self.root.rglob("*"):
            if p.is_file():
                self.assertNotIn(b"secret-never-save", p.read_bytes())

    def test_timeout_and_nonzero_same_message_have_different_codes(self):
        for code, cause in (("transport_timeout", subprocess.TimeoutExpired("private", 1)),
                            ("process_exit_unknown", None)):
            with self.subTest(code=code):
                sink = self.make(output="out-" + code.replace("_", "-"), count=1)
                value, called = self.call(sink, error=BoundaryError(code, cause))
                self.assertEqual((value["status"], value["code"], value["retry_policy"]),
                                 ("unknown", code, "explicit_confirmation"))
                called.assert_called_once()
                before = sink.current()
                self.store.recover(sink.identity["output_id"], owner_stopped=True)
                self.assertEqual(sink.current(), before)

    def test_transport_cli_captures_timeout_and_nonzero_at_boundary(self):
        import io
        sink = self.make(count=1)
        for timeout in (True, False):
            child = Mock()
            child.returncode = 1
            child.stdin = io.BytesIO(); child.stdout = io.BytesIO(b"timeout")
            child.wait.side_effect = [subprocess.TimeoutExpired("private-secret", 1), 1] if timeout else None
            with patch("execution.worker.ProcessTree") as factory, self.assertRaises(BoundaryError) as caught:
                factory.return_value.launch.return_value = child
                generation.transport(sink.call_request(), ["unused-cli"])
            self.assertEqual(caught.exception.code, "transport_timeout" if timeout else "process_exit_unknown")
            factory.return_value.finish.assert_called_once()
            self.assertNotIn("private-secret", str(caught.exception))

    def test_creation_failure_is_proven_not_sent(self):
        sink = self.make(count=1)
        with patch("execution.worker.ProcessTree") as factory, self.assertRaises(BoundaryError) as caught:
            factory.return_value.launch.side_effect = FileNotFoundError("secret")
            generation.transport(sink.call_request(), ["unused-cli"])
        self.assertEqual(caught.exception.code, "launch_failed")
        value, _ = self.call(sink, error=caught.exception)
        self.assertEqual((value["status"], value["call_state"], value["retry_policy"]),
                         ("error", "not_started", "safe_new_request"))

    def test_model_preflight_and_reservation_failure_never_dispatch(self):
        sink = self.make(count=1)
        with patch("execution.generation.preflight", side_effect=ValueError("secret")), patch("execution.generation.transport") as called:
            value = run_generation(sink.call_request(), sink)
        self.assertEqual(value["code"], "preflight_failed")
        self.assertFalse(sink.started())
        called.assert_not_called()
        other = self.make(output="out-reservation", count=1)
        with patch("execution.generation.preflight", return_value=["fixed"]), patch.object(other, "reserve", side_effect=OSError()), patch("execution.generation.transport") as called:
            with self.assertRaises(OSError):
                run_generation(other.call_request(), other)
        called.assert_not_called()

    def test_limits_count_reserved_attempts_and_do_not_refund_unknown(self):
        first = self.make(calls=1)
        self.call(first, error=BoundaryError("transport_timeout"))
        second = self.store.sink("out-test", "cand-1")
        value, called = self.call(second)
        self.assertEqual(value["status"], "skipped_limit")
        called.assert_not_called()
        self.assertEqual(len(read_json(first.output / "quota.json")["started"]), 1)
        limit = self.make(output="out-no-budget", calls=0, count=1)
        value, called = self.call(limit)
        self.assertEqual(value["code"], "limit_reached")
        self.assertFalse(limit.started())
        called.assert_not_called()

    def test_response_invalid_preserves_bounded_raw(self):
        for i, response in enumerate((Response(b""), Response(b"x" * 100), Response(b"not-json"))):
            sink = self.make(output="out-invalid-" + str(i), backend="openai" if i == 2 else "codex-cli", limit=20, count=1)
            value, _ = self.call(sink, response=response)
            self.assertEqual(value["code"], "response_invalid")
            self.assertEqual(value["call_state"], "response_received")
            self.assertLessEqual((sink.output / value["response_ref"]).stat().st_size, 20)

    def test_raw_receipt_allows_only_local_recovery_after_body_save_failure(self):
        import execution.output_store as storage
        sink = self.make(count=1)
        real = storage.write_bytes
        def fail_body(path, data):
            if path.name.startswith("body-"):
                raise OSError("disk unavailable")
            return real(path, data)
        with patch.object(storage, "write_bytes", side_effect=fail_body):
            value, external = self.call(sink)
        self.assertEqual((value["code"], value["retry_policy"]), ("persistence_failed", "local_recovery_only"))
        raw = (sink.folder / "response.bin").read_bytes()
        with patch("execution.generation.transport") as called:
            recovered = self.store.recover("out-test", owner_stopped=True)
        called.assert_not_called()
        self.assertEqual(recovered["entries"][0]["status"], "ok")
        self.assertEqual((sink.folder / "response.bin").read_bytes(), raw)
        self.assertEqual(len(list((sink.folder / "receipts").glob("*.json"))), 2)
        external.assert_called_once()

    def test_no_raw_receipt_after_disk_failure_remains_unknown(self):
        sink = self.make(count=1)
        with patch.object(sink, "receive", side_effect=OSError("secret-disk")):
            value, _ = self.call(sink)
        self.assertEqual((value["status"], value["code"]), ("unknown", "persistence_outcome_unknown"))
        self.store.recover("out-test", owner_stopped=True)
        self.assertEqual(sink.current(), value)

    def test_success_receipt_wins_if_index_write_fails(self):
        sink = self.make(count=1)
        with patch.object(self.store, "project", side_effect=OSError("index unavailable")):
            value, called = self.call(sink)
        self.assertEqual(value["status"], "ok")
        self.assertEqual(read_json(sink.output / "index.json")["entries"][0]["status"], "pending")
        with patch("execution.generation.transport") as external:
            recovered = OutputStore(self.root).recover("out-test", owner_stopped=True)
        self.assertEqual(recovered["entries"][0], value)
        external.assert_not_called()
        called.assert_called_once()

    def test_started_crash_and_unstarted_items_are_distinct(self):
        sink = self.make()
        sink.reserve()
        with self.assertRaises(ConfigError):
            self.store.recover("out-test")
        recovered = self.store.recover("out-test", owner_stopped=True)
        self.assertEqual([e["status"] for e in recovered["entries"]], ["unknown", "skipped_interrupted"])
        self.assertEqual(recovered["completion_kind"], "unknown")

    def test_tamper_rejected_and_credentials_not_serialized(self):
        sink = self.make()
        (sink.folder / "prompt.txt").write_text("changed", encoding="utf-8")
        with self.assertRaises(ConfigError):
            sink.call_request()
        request = self.store.request("out-test")
        request["output_id"] = "out-secret"
        request["credentials"] = {"api_key": "secret"}
        with self.assertRaises(ConfigError):
            self.store.create(request, {cid: "prompt" for cid in request["candidate_ids"]})
        self.assertFalse(self.store.folder("out-secret").exists())

    def test_attempt_identity_and_prompt_digest_are_sealed(self):
        sink = self.make(count=1)
        item = read_json(sink.folder / "item.json")
        item["attempt_id"] = "attempt-rebound"
        (sink.folder / "item.json").write_text(json.dumps(item), encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            self.store.sink("out-test", "cand-0")
        self.assertEqual(caught.exception.code, "snapshot_changed")

    def test_aggregate_matches_contract(self):
        cases = [(["ok"], "succeeded", "generated"), (["prompt_only"], "succeeded", "prompt_only"),
                 (["ok", "prompt_only"], "succeeded", "mixed"), (["ok", "unknown"], "partial", "partial_unknown"),
                 (["unknown"], "failed", "unknown"), (["skipped_limit"], "failed", "limit_before_start"),
                 (["error", "skipped_limit"], "failed", "error")]
        for statuses, state, kind in cases:
            actual = aggregate([{"status": s} for s in statuses])
            self.assertEqual((actual["state"], actual["completion_kind"]), (state, kind))
        for statuses in ([], ["pending"], ["ok", "running"]):
            with self.assertRaises(ValueError):
                aggregate([{"status": s} for s in statuses])
        self.assertEqual(aggregate([{"status": "ok"}], stopped="cancelled")["state"], "cancelled")


class IncrementalCLITests(unittest.TestCase):
    def setUp(self):
        from test_output import _write_archive, _write_rows, PROJECT, TEMPLATE
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui006-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "archive.json"
        _write_rows(self.root / "layers.jsonl")
        _write_archive(self.archive, "layers.jsonl")
        raw = read_json(self.archive)
        import copy
        second = copy.deepcopy(raw["cells"]["III|high"])
        second["descriptor"].update(category="IV", volatility_bin="low")
        raw["cells"]["IV|low"] = second
        self.archive.write_text(json.dumps(raw), encoding="utf-8")
        self.common = ["--archive", str(self.archive), "--runs", str(self.root),
                       "--project", str(PROJECT), "--template", str(TEMPLATE)]

    def test_synopsis_cells_preserve_unrequested_entries_and_validate_before_call(self):
        from scripts import synopsize
        from gapengine.synopsis import GenerationResult
        path = self.root / "synopses.json"
        args = [*self.common, "--out", str(path)]
        with patch.object(synopsize, "generate_text", return_value=GenerationResult(status="ok", text="first")):
            synopsize.main(args)
        old = read_json(path)["entries"][0]
        with patch.object(synopsize, "generate_text", return_value=GenerationResult(status="ok", text="updated")) as called:
            synopsize.main([*args, "--cells", "IV|low"])
        called.assert_called_once()
        self.assertEqual(read_json(path)["entries"][0], old)
        self.assertEqual(read_json(path)["entries"][1]["synopsis"], "updated")
        before = path.read_bytes()
        with patch.object(synopsize, "generate_text") as called, self.assertRaises(ValueError):
            synopsize.main([*args, "--cells", "no|cell"])
        called.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_synopsis_first_result_survives_second_call_interruption(self):
        from scripts import synopsize
        from gapengine.synopsis import GenerationResult
        path = self.root / "synopses.json"
        with patch.object(synopsize, "generate_text", side_effect=[GenerationResult(status="ok", text=TEXT), KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                synopsize.main([*self.common, "--out", str(path)])
        data = read_json(path)
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["entries"][0]["synopsis"], TEXT)

    def test_narration_first_result_survives_second_call_interruption(self):
        from scripts import narrate
        from gapengine.synopsis import GenerationResult
        selection = self.root / "selection.json"
        selection.write_text(json.dumps({"selected": ["III|high", "IV|low"]}), encoding="utf-8")
        output = self.root / "stories"
        with patch.object(narrate, "generate_text", side_effect=[GenerationResult(status="ok", text=TEXT), KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                narrate.main([*self.common, "--selection", str(selection), "--out", str(output)])
        data = read_json(output / "index.json")
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["entries"][0]["status"], "ok")
        self.assertEqual((output / data["entries"][0]["story_path"]).read_text(encoding="utf-8"), TEXT + "\n")


if __name__ == "__main__":
    unittest.main()
