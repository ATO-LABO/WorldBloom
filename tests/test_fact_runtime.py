"""Real calendar execution; explicit witness timings remain distinct from calibrated rules."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from engine.fact_runtime import stable
from gapengine.fact_contract import extract_supplement, make_package, build_prompt
from scripts.fact_runtime_demo import create_project, run, witness_commands
from scripts.cultural_festival_v2 import load
from engine.sim import Simulation


class FactRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def execute(self, name="run", transform=None, commands=None, until=520):
        project = create_project(self.root / ("project-" + name), transform)
        rt = run(project, self.root / name, commands, until=until)
        return project, rt

    def typed(self, rt):
        return [r["details"]["fact_record"] for r in rt.rows if r["verb"] == "fact_record"]

    def test_real_delivery_notice_understanding_and_separate_beliefs(self):
        project, rt = self.execute()
        state = rt.facts.state["messages"]["M"]
        self.assertEqual([state[k] for k in ("sent_at", "delivered_at", "noticed_at", "understood_at")], [501, 503, 504, 506])
        belief = rt.facts.state["beliefs"]
        self.assertIs(belief[stable("belief", "灯", "arrival_prediction", "on_time")], False)
        self.assertEqual(belief[stable("belief", "灯", "reason_assessment", "road_closed")], "withheld")
        self.assertFalse(any(r["kind"] == "belief" and r["content"]["domain"] == "intent_inference" for r in self.typed(rt)))
        self.assertIn(stable("understood", "M"), rt.people["灯"].knowledge)
        skipped = [r["details"]["reason"] for r in rt.rows if r["verb"] == "fact_update_not_applied"]
        self.assertEqual(skipped, ["not_understood", "same_message_evidence_already_applied"])
        raw = (rt.store.out / "layers.jsonl").read_bytes()
        supplement = extract_supplement(raw, {}, {"run_id": "actual", "candidate_id": "scene"})
        package = make_package(supplement, selected=["F5-P", "F5-E"], required=["F5-P", "F5-E"])
        self.assertIn("灯", build_prompt(package, "narrate"))
        self.assertFalse(any(r["kind"] == "world_fact" and r["content"].get("predicate") == "road_closed" for r in supplement["records"]))
        self.assertEqual(raw, (rt.store.out / "layers.jsonl").read_bytes())

    def test_notice_does_not_update_belief_and_premature_read_is_rejected(self):
        commands = witness_commands() + [{"id": "too-early", "actor": "灯", "at": 502, "verb": "read_message", "message_id": "M"}]
        _, rt = self.execute(commands=commands, until=504)
        self.assertEqual(rt.facts.state["messages"]["M"]["noticed_at"], 504)
        self.assertIsNone(rt.facts.state["messages"]["M"]["understood_at"])
        self.assertIs(rt.facts.state["beliefs"][stable("belief", "灯", "arrival_prediction", "on_time")], True)
        self.assertNotIn(stable("understood", "M"), rt.people["灯"].knowledge)
        self.assertTrue(any(r["id"] == "reject:too-early" for r in rt.rows))

    def test_combined_departure_and_question_is_real_parallel_execution(self):
        _, rt = self.execute(commands=witness_commands(combined=True))
        self.assertEqual(rt.people["灯"].zone, "教室")
        starts = [r for r in rt.rows if r["verb"] == "action_started" and r["details"]["choice"]["id"].startswith("leave-and-ask-")]
        self.assertEqual(len(starts), 2)
        self.assertEqual({r["absolute_minute"] for r in starts}, {509})
        self.assertEqual(rt.facts.state["messages"]["Q"]["sent_at"], 510)
        self.assertTrue(any(r["id"] == "complete:leave-and-ask-move" and r["absolute_minute"] == 519 for r in rt.rows))

    def test_capability_controls_combined_candidate_and_execution(self):
        def disable(model):
            model["capabilities"]["灯"]["send_while_moving"] = False
        _, rt = self.execute(transform=disable, commands=witness_commands(combined=True))
        choice = next(r for r in rt.rows if r["id"] == "decision:leave-and-ask")
        self.assertFalse(any(c["verb"] == "move_and_send" for c in choice["details"]["candidates"]))
        self.assertTrue(any(r["id"] == "reject:leave-and-ask" for r in rt.rows))
        self.assertNotIn("Q", rt.facts.state["messages"])
        self.assertEqual(rt.people["灯"].zone, "展示室")

    def test_reading_during_movement_requires_explicit_capability(self):
        commands = witness_commands()
        commands[-1] = {**commands[-1], "verb": "move_and_read", "to": "教室"}
        for allowed in (True, False):
            with self.subTest(allowed=allowed):
                def setting(m): m["capabilities"]["灯"]["read_while_moving"] = allowed
                _, rt = self.execute(str(allowed), transform=setting, commands=commands)
                self.assertEqual(rt.facts.state["messages"]["M"]["understood_at"], 506 if allowed else None)
                self.assertEqual(rt.people["灯"].zone, "教室" if allowed else "展示室")

    def test_argument_is_context_and_does_not_force_notice_result(self):
        for argument, notice in ((True, 504), (False, None)):
            def setting(m):
                if not argument: m["contexts"] = []
                m["messages"][0]["notice_at"] = notice
            _, rt = self.execute(str(argument), transform=setting)
            self.assertEqual(rt.facts.state["messages"]["M"]["noticed_at"], notice)

    def test_work_selection_references_the_chosen_task_and_rejects_wrong_candidate(self):
        from execution.provenance import ConfigError
        commands = witness_commands() + [{"id": "choose-headline", "actor": "灯", "at": 509,
            "verb": "work", "task_id": "panel-headline", "minutes": 10}]
        _, rt = self.execute("work-selection", commands=commands)
        byid = {r["id"]: r for r in self.typed(rt)}
        selected = byid[stable("selected", "choose-headline")]
        execution = byid[stable("execution", "choose-headline")]
        candidates = [byid[i] for i in selected["depends_on"] if byid[i]["kind"] == "action_candidate"]
        self.assertEqual(selected["content"]["choice"]["task_id"], "panel-headline")
        self.assertEqual(execution["content"]["choice"]["task_id"], "panel-headline")
        self.assertEqual([r["content"]["candidate"]["task_id"] for r in candidates], ["panel-headline"])
        raw = (rt.store.out / "layers.jsonl").read_bytes()
        extract_supplement(raw, {}, {"run_id": "work-selection", "candidate_id": "scene"})
        wrong = next(r for r in byid.values() if r["kind"] == "action_candidate"
                     and r["content"]["action_id"] == "choose-headline"
                     and r["content"]["candidate"].get("task_id") == "panel-cards")
        rows = deepcopy(rt.rows)
        next(r["details"]["fact_record"] for r in rows if r["verb"] == "fact_record"
             and r["details"]["fact_record"]["id"] == selected["id"])["depends_on"][0] = wrong["id"]
        import json
        tampered = b"\n".join(json.dumps(r, ensure_ascii=False).encode("utf-8") for r in rows)
        with self.assertRaises(ConfigError):
            extract_supplement(tampered, {}, {"run_id": "work-selection", "candidate_id": "scene"})

    def test_checkpoint_rejects_missing_inflight_communication_without_changing_state(self):
        _, split = self.execute("missing-communication", until=505)
        before = split.export_state()
        self.assertEqual(before["fact_model"]["communications"]["灯"]["id"], "read-M")
        bad = deepcopy(before)
        bad["fact_model"]["communications"] = {}
        with self.assertRaises(ValueError):
            split.restore_state(bad)
        self.assertEqual(split.export_state(), before)

    def test_checkpoint_resume_mid_communication_is_byte_exact_and_tamper_rejected(self):
        project = create_project(self.root / "project")
        continuous = run(project, self.root / "continuous", until=520)
        split = run(project, self.root / "split", until=505)
        self.assertTrue(split.facts.state["communications"])
        resumed = run(project, self.root / "split", until=520, resume=True)
        for name in ("layers.jsonl", "summary.json", "calendar-state.json"):
            self.assertEqual((continuous.store.out / name).read_bytes(), (resumed.store.out / name).read_bytes())
        bad = deepcopy(resumed.export_state())
        bad["fact_model"]["messages"]["M"]["noticed_at"] = None
        before = resumed.export_state()
        with self.assertRaises(ValueError): resumed.restore_state(bad)
        self.assertEqual(resumed.export_state(), before)
        bad = deepcopy(before)
        bad['fact_model']['processed'] = []
        with self.assertRaises(ValueError): resumed.restore_state(bad)
        self.assertEqual(resumed.export_state(), before)

    def test_unknown_schema_and_implicit_duration_rejected_at_project_load(self):
        for name, change in (("version", lambda m: m.update(schema_version=2)),
                             ("duration", lambda m: m["capabilities"]["灯"].pop("read_minutes"))):
            project = create_project(self.root / name, change)
            with self.assertRaises(ValueError): load(project)
