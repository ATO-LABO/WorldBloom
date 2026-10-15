"""Actual owned job -> frozen runtime -> fixed transport -> durable result."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_output_jobs as support
from execution.provenance import canonical, read_json, sha256, atomic_json, ConfigError
from execution.output_store import OutputStore
from execution.output_requests import normalize, admit, eligible
from execution.fact_store import FactStore
from execution.fact_service import pinned_package, process_fact_output, synopsis_from_output
from execution.selections import SelectionStore
from gapengine.fact_contract import make_package, packet, build_prompt, extract_supplement
from fact_fixtures import raw_log


class FactJobTests(unittest.TestCase):
    cleanup_jobs = support.OutputJobTests.cleanup_jobs
    wait_state = support.OutputJobTests.wait_state
    set_generation = support.OutputJobTests.set_generation
    finish = support.OutputJobTests.finish

    def setUp(self):
        support.OutputJobTests.setUp(self)
        self.config = self.configs.get("cfg-test")
        self.raw = raw_log()
        root = self.configs.runs / "facts-example"; root.mkdir()
        (root / "layers.jsonl").write_bytes(self.raw)
        archive = read_json(self.run / "archive.json")
        cell = next(iter(archive["cells"]))
        archive["cells"] = {cell: archive["cells"][cell]}
        archive["cells"][cell]["exemplar"]["layers_path"] = "layers.jsonl"
        atomic_json(root / "archive.json", archive)
        self.run = root
        self.rid = self.catalog.register_legacy(root.name)
        self.cid = self.catalog.candidates(self.rid)["candidates"][0]["candidate_id"]
        self.ids = [self.cid]
        self.sel = SelectionStore(self.catalog).update(self.rid,
            [{"candidate_id": self.cid, "state": "adopted"}], expected_revision=0)
        _, manifest, cfgroot = self.configs._bundle("cfg-test")
        self.inputs = {r["path"]: (cfgroot / "inputs" / r["path"]).read_bytes() for r in manifest["files"]}
        self.supplement = extract_supplement(self.raw, self.inputs, {
            "run_id": self.rid, "candidate_id": self.cid, "config_sha256": sha256(canonical(self.config)),
            "input_manifest_sha256": self.config["input_manifest_sha256"]})
        self.package = make_package(self.supplement,
            selected=[r["id"] for r in self.supplement["records"] if r["id"] != "hidden"],
            required=["M1", "M2", "F4", "F5-P", "F5-E"], hidden=["hidden"])
        self.facts = FactStore(self.configs.control)
        self.ref = self.facts.save(self.package, raw=self.raw, inputs=self.inputs)

    def request(self, kind="narrate", ident="req-facts", ref=None):
        return support.OutputJobTests.request(self, rid=ident, kind=kind, config="cfg-test",
            schema_version=3, pipeline="facts_v1", fact_refs={self.cid: ref or self.ref}, synopsis_refs={})

    def fixed_transport(self):
        path = self.repo / "execution/generation.py"
        marker = str(self.base / "transport-input.json")
        source = path.read_text(encoding="utf-8")
        source += '\nfrom pathlib import Path\n'
        source += 'def preflight(request): return None\n'
        source += 'def transport(request, command):\n'
        source += '    data = json.loads(request["prompt"].split("\\n入力:\\n", 1)[1])\n'
        source += f'    Path({marker!r}).write_text(json.dumps({{"prompt": request["prompt"], "input": data}}, ensure_ascii=False), encoding="utf-8")\n'
        source += '    text = json.dumps({"text":"律は通行止めで遅れると伝えた。澪は遅刻を見込んだが理由の真偽は保留した。", "assertions":[{"record_id": r["id"], "record": r} for r in data["records"]]}, ensure_ascii=False)\n'
        source += '    return Response(json.dumps({"status":"completed", "output":[{"content":[{"type":"output_text", "text":text}]}]}, ensure_ascii=False).encode("utf-8"))\n'
        path.write_text(source, encoding="utf-8")
        self.set_generation("openai", "fixed-response-test", {"max_calls": 1, "call_timeout_seconds": 10,
            "wall_seconds": 120, "max_saved_response_bytes": 128000})

    def submit(self, request):
        with patch("execution.output_requests.generation_availability", return_value={"available": True}):
            return self.jobs.submit(request, settings_path=self.settings_path)[0]

    def test_fixed_response_passes_through_both_real_job_paths_and_saves_exact_inputs(self):
        before = {p.name: p.read_bytes() for p in self.run.iterdir() if p.is_file()}
        self.fixed_transport()
        for kind in ("synopsize", "narrate"):
            job = self.submit(self.request(kind, "req-facts-" + kind))
            end = self.finish(job)
            self.assertEqual((end["state"], end["completion_kind"]), ("succeeded", "generated"), end)
            store = OutputStore(self.configs.control)
            folder = store.folder(job["output_id"])
            req = store.request(job["output_id"])
            self.assertEqual(pinned_package(folder, self.cid, req), self.package)
            selected = read_json(folder / "inputs/facts" / self.cid / "packet.json")
            sent = read_json(self.base / "transport-input.json")
            self.assertEqual(selected, sent["input"])
            sink = store.sink(job["output_id"], self.cid)
            self.assertEqual((sink.folder / "prompt.txt").read_text(encoding="utf-8"), sent["prompt"])
            self.assertEqual(sent["prompt"], build_prompt(self.package, kind))
            self.assertNotIn("SECRET", sent["prompt"])
            self.assertTrue(sink.received())
            self.assertEqual(req["fact_refs"][self.cid], self.ref)
            with patch("execution.generation.transport", side_effect=AssertionError("do not resend")):
                checked = process_fact_output(store, job["output_id"], self.cid)
                self.assertEqual(checked, process_fact_output(store, job["output_id"], self.cid))
            self.assertEqual(checked["validation"]["structural_status"], "passed")
            self.assertEqual(checked["validation"]["semantic_review_status"], "not_reviewed")
            self.assertEqual(len(read_json(folder / "quota.json")["started"]), 1)
            store.verify_artifacts(job["output_id"])
            if kind == "synopsize":
                synopsis = synopsis_from_output(store, job["output_id"], self.cid)
                self.package = make_package(self.supplement, **self.package["selection"], synopsis=synopsis)
                self.ref = self.facts.save(self.package, raw=self.raw, inputs=self.inputs)
            else:
                self.assertIn("synopsis", selected)
                self.assertEqual(selected["synopsis_semantic_review_status"], "not_reviewed")
                self.assertTrue(selected["records"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.run.iterdir() if p.is_file()})

    def test_new_style_revision_keeps_old_result_and_prompt_only_uses_same_job(self):
        job = self.submit(self.request())
        self.assertEqual(self.finish(job)["completion_kind"], "prompt_only")
        store = OutputStore(self.configs.control)
        old = store.sink(job["output_id"], self.cid).folder / "prompt.txt"
        original = old.read_bytes()
        changed = make_package(self.supplement, **self.package["selection"],
            styles={**self.package["styles"], "narrate": "一文を短くして書く。"})
        ref = self.facts.save(changed, raw=self.raw, inputs=self.inputs)
        job2 = self.submit(self.request(ident="req-new-style", ref=ref))
        self.assertEqual(self.finish(job2)["state"], "succeeded")
        self.assertEqual(old.read_bytes(), original)
        self.assertNotEqual(store.sink(job2["output_id"], self.cid).folder.joinpath("prompt.txt").read_bytes(), original)
        self.assertEqual(self.facts.resolve(self.ref), self.package)

    def test_wrong_candidate_sha_version_and_conflicting_style_rejected_before_dispatch(self):
        bad = self.request(); bad["fact_refs"] = {"other": self.ref}
        with self.assertRaises(ConfigError): normalize(bad)
        for changes in ({"schema_version": 2}, {"schema_version": 4}, {"story_refs": {}},
                        {"schema_version": 1, "pipeline": "facts_v1"}):
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                normalize({**self.request(), **changes})
        bad = self.request(); bad["fact_refs"][self.cid]["package_sha256"] = "0" * 64
        with self.assertRaises(ConfigError):
            self.submit(bad)
        bad_package = make_package(self.supplement, **self.package["selection"],
            styles={**self.package["styles"], "narrate": "未読でも理解したように書く"})
        ref = self.facts.save(bad_package, raw=self.raw, inputs=self.inputs)
        with self.assertRaises(ConfigError):
            self.submit(self.request(ident="req-conflict", ref=ref))
        self.assertEqual(list(self.jobs.root.glob("job-*")), [])

    def test_organize_uses_captured_run_and_config_without_rewriting_them(self):
        from scripts.fact_materials import organize
        before = (self.run / "layers.jsonl").read_bytes()
        result = organize(self.configs, self.catalog, self.rid, self.cid, "cfg-test", self.package["selection"])
        self.assertEqual(result["reference"], self.ref)
        self.assertEqual(result["package"], self.package)
        self.assertEqual((self.run / "layers.jsonl").read_bytes(), before)
        with self.assertRaises(ConfigError):
            organize(self.configs, self.catalog, self.rid, self.cid, "cfg-test", {"selected": []})

    def test_changing_pipeline_cannot_bypass_unknown_attempt_confirmation(self):
        from execution.generation import result
        req = normalize(self.request())
        req.update(output_id="out-unknown-facts", job_id="fixture-job")
        self.outputs.create(req, {self.cid: "fixture prompt"})
        sink = self.outputs.sink(req["output_id"], self.cid)
        sink.finish(result(sink.identity, "unknown", "transport_timeout", retry_policy="explicit_confirmation"))
        other = normalize(support.OutputJobTests.request(self, rid="req-other-path", kind="narrate", mode="regenerate"))
        with self.assertRaises(ConfigError):
            eligible(self.outputs, other)
