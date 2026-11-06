"""Story v2 shares frozen evidence across the two Studio job types."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from execution.output_requests import admit, normalize
from execution.output_store import OutputStore
from execution.provenance import ConfigError, atomic_json, read_json
from execution.selections import SelectionStore
from execution.story_service import pinned_story, read_story_output
from execution.story_store import StoryStore

import test_output_jobs as jobfixtures
from test_story_pipeline import fixture


class StoryV2JobTests(unittest.TestCase):
    cleanup_jobs = jobfixtures.OutputJobTests.cleanup_jobs
    wait_state = jobfixtures.OutputJobTests.wait_state
    set_generation = jobfixtures.OutputJobTests.set_generation
    finish = jobfixtures.OutputJobTests.finish

    def setUp(self):
        jobfixtures.OutputJobTests.setUp(self)
        self.configs.save({
            "label": "momotaro", "project_id": "momotaro_plus3", "template_id": "momotaro_plus3",
            "evolution": {"generations": 1, "population": 1, "seeds": 1},
        }, config_id="cfg-story")
        _, raw, _ = fixture()
        root = self.configs.runs / "story-v2-fixture"
        root.mkdir()
        (root / "layers.jsonl").write_bytes(raw)
        atomic_json(root / "archive.json", {"cells": {"C|low": {
            "generation": 0, "quality": 0.5, "reach_rate": 1.0, "reached": True,
            "exemplar": {"seed": 20, "layers_path": "layers.jsonl"}, "parents": [], "genome": {},
        }}})
        self.rid = self.catalog.register_legacy(root.name)
        self.cid = next(iter(self.catalog.representatives(self.catalog.snapshot(self.rid)).values()))
        self.sel = SelectionStore(self.catalog).get(self.rid)
        self.stories = StoryStore(self.configs.control)
        self.store = OutputStore(self.configs.control)
        self.set_generation("ollama", "gemma4:e4b-it-qat", {
            "max_calls": 1, "call_timeout_seconds": 20, "wall_seconds": 120,
            "max_saved_response_bytes": 16000,
        })
        settings = read_json(self.settings_path)
        settings["output"]["ollama"]["think"] = True
        atomic_json(self.settings_path, settings)

    def request(self, kind, ref=None):
        return jobfixtures.OutputJobTests.request(
            self, rid=f"req-story-v2-{kind}", kind=kind, config="cfg-story",
            candidate_ids=[self.cid], schema_version=3, pipeline="story_v2",
            story_refs={self.cid: ref}, synopsis_refs={},
        )

    def test_unselected_synopsis_builds_plan_reused_by_adopted_screening(self):
        generation = self.configs.repo / "execution/generation.py"
        with generation.open("a", encoding="utf-8") as handle:
            handle.write(
                "\ndef transport(request, command):\n"
                "    assert request['credentials']['think'] is False\n"
                "    payload = {'done_reason': 'stop', 'message': {'content': '{\"slots\":[]}'}}\n"
                "    return Response(json.dumps(payload).encode('utf-8'))\n"
            )
        with patch("execution.output_requests.generation_availability", return_value={"available": True}):
            synopsis = self.jobs.submit(self.request("synopsize"), settings_path=self.settings_path)[0]
        completed = self.finish(synopsis)
        self.assertEqual(completed["state"], "succeeded", completed)
        synopsis_id = synopsis["output_id"]
        synopsis_req = self.store.request(synopsis_id)
        synopsis_ref = synopsis_req["story_refs"][self.cid]
        self.assertIsNone(synopsis_ref["confirmation_sha256"])
        self.assertEqual(SelectionStore(self.catalog).get(self.rid)["entries"], [])
        current = self.stories.current(self.rid, self.cid)
        self.assertFalse(current["confirmed"])
        self.assertEqual(current["plan"]["plan_id"], synopsis_ref["plan_id"])

        self.stories.confirm(current["plan"]["plan_id"], current["plan_sha256"])
        self.sel = SelectionStore(self.catalog).update(
            self.rid, [{"candidate_id": self.cid, "state": "adopted"}], expected_revision=self.sel["revision"],
        )
        with patch("execution.output_requests.generation_availability", return_value={"available": True}):
            narrate = self.jobs.submit(
                self.request("narrate", self.stories.reference(current["plan"]["plan_id"])),
                settings_path=self.settings_path,
            )[0]
        completed = self.finish(narrate)
        self.assertEqual(completed["state"], "succeeded", completed)
        narrate_id = narrate["output_id"]
        narrate_req = self.store.request(narrate_id)
        narrate_ref = narrate_req["story_refs"][self.cid]
        self.assertTrue(narrate_ref["confirmation_sha256"])
        for key in ("plan_id", "plan_sha256", "material_id", "material_sha256"):
            self.assertEqual(synopsis_ref[key], narrate_ref[key])

        for output_id, request in [(synopsis_id, synopsis_req), (narrate_id, narrate_req)]:
            material, plan, _ = pinned_story(self.store.folder(output_id), self.cid, request)
            checked = read_story_output(self.store, output_id, self.cid)
            self.assertEqual(checked["validation"]["material_sha256"], material["content_sha256"])
            self.assertEqual(checked["validation"]["plan_sha256"], self.stories.current(self.rid, self.cid)["plan_sha256"])
            self.assertEqual(checked["validation"]["semantic_review_status"], "not_reviewed")
            self.assertEqual(plan["plan_id"], synopsis_ref["plan_id"])
            self.assertTrue(checked["text"])

    def test_scope_and_confirmed_narration_are_enforced(self):
        request = self.request("synopsize")
        wrong_model = deepcopy(request)
        wrong_model["model"] = "gemma3:4b"
        with self.assertRaises(ConfigError):
            normalize(wrong_model)
        with self.assertRaises(ConfigError):
            normalize(self.request("narrate"))
        with patch("execution.output_requests.generation_availability", return_value={"available": True}):
            admitted = admit(self.jobs, normalize(request), settings_path=self.settings_path)
        self.assertEqual(admitted["candidate_ids"], [self.cid])
