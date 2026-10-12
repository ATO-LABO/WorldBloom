"""Verify the sample, and record native limitations without calling them success."""
from pathlib import Path
import tempfile
import unittest
import yaml

from scripts.cultural_festival_probe import (
    DiagnosticPolicy, ObservedSimulation, PROJECT, capability_probes,
    digest, input_manifest, load, loaded_parameters, native_run,
    static_plan_feasibility,
)


class CulturalFestivalTests(unittest.TestCase):
    def test_schema_initial_state_and_isolation(self):
        world, subjects = load(legacy=True)
        contract = yaml.safe_load((PROJECT / "contract_v1.yaml").read_text(encoding="utf-8"))
        self.assertEqual(tuple(contract["clock"]["slots"]), world.slots)
        self.assertEqual(len(world.slots), 23)
        self.assertEqual({n: s.zone for n, s in subjects.items()}, contract["initial_locations"])
        self.assertEqual({n: s.traits for n, s in subjects.items()}, contract["personality"])
        for name, s in subjects.items():
            self.assertEqual(s.knowledge, set(contract["knowledge"]["initial_facts"][name]))
            self.assertFalse(s.goal.obstacles)
            self.assertFalse(s.verbs & {"fight", "sabotage", "mislead"})
        self.assertTrue(subjects["蒼"].has_item("台本"))
        self.assertFalse(any(s.has_item("衣装") for s in subjects.values()))
        self.assertTrue(all(not e.get("force_action") and not e.get("move_to")
                            for e in world.scheduled_events))

    def test_authored_witness_is_feasible_not_generated(self):
        witness = static_plan_feasibility()
        self.assertTrue(witness["plan_feasible"])
        self.assertEqual(witness["errors"], [])
        self.assertTrue(witness["expected_primary_presence"])
        self.assertTrue(witness["expected_readiness"]["costume_prepared"])
        self.assertTrue(witness["expected_readiness"]["sound_checked"])
        self.assertTrue(witness["expected_readiness"]["ren_present"])
        self.assertIn("NOT native execution", witness["evidence_kind"])

    def test_capability_counterexamples_are_explicit(self):
        probes = capability_probes()
        self.assertTrue(probes["nonadjacent_move_candidates_at_08"])
        self.assertFalse(probes["closed_gate_move_offered_at_06"])
        self.assertEqual(probes["closed_gate_execute_at_06"]["result"], "invalid")
        self.assertTrue(probes["open_gate_move_offered_at_08"])
        self.assertTrue(probes["movement"]["same_tick_arrival"])
        self.assertEqual(probes["remote_contact"]["result"], "invalid")
        self.assertEqual(probes["remote_contact"]["details"]["reason"], "target_not_present")
        self.assertTrue(probes["co_room_speech"]["same_tick_delivery"])
        self.assertFalse(probes["co_room_speech"]["bystander_heard"])
        for verb, evidence in probes["domain_verbs"].items():
            with self.subTest(verb=verb):
                self.assertFalse(evidence["handled"])
                self.assertEqual(evidence["details"]["reason"], "unknown_verb")

    def test_native_run_replay_and_knowledge_views(self):
        import json
        before = input_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = native_run(out / "first")
            second = native_run(out / "second")
            self.assertEqual(first["native_layers_sha256"], second["native_layers_sha256"])
            self.assertEqual(first["loaded_initial_parameters_sha256"],
                             second["loaded_initial_parameters_sha256"])
            self.assertEqual(first["input_manifest"], second["input_manifest"])
            self.assertEqual(first["decision_count"], 23 * 4)
            self.assertEqual(first["invalid_count"], 0)
            self.assertFalse(first["pattern0_pass"])
            self.assertEqual(first["missing_proposals"], [])
            self.assertTrue(first["opening"]["primary_presence"])
            self.assertIsNone(first["opening"]["readiness"]["sound_checked"])
            self.assertIsNone(first["opening"]["declines_to_perform"])
            for name in ("蒼", "紗季", "蓮", "灯"):
                rows = json.loads((out / "first" / f"knowledge-{name}.json").read_text(encoding="utf-8"))
                self.assertTrue(rows)
                self.assertTrue(all("positions_inspector_only" not in row and "trigger" not in row
                                    for row in rows))
                if name == "蒼":
                    self.assertIn("終盤短縮希望", rows[-1]["own_knowledge"])
                elif name in ("紗季", "灯"):
                    self.assertNotIn("終盤短縮希望", rows[-1]["own_knowledge"])
            self.assertEqual((out / "first/observations.json").read_bytes(),
                             (out / "second/observations.json").read_bytes())
        self.assertEqual(before, input_manifest())

    def test_opening_captured_before_15_action(self):
        # Changes only choices in a local test policy, never world/people/initial state.
        class BoundaryPolicy(DiagnosticPolicy):
            PROPOSALS = {
                **DiagnosticPolicy.PROPOSALS,
                "蒼": {"08:00": ("move", ("校門",)),
                        "08:30": ("move", ("部室",)),
                        "15:00": ("move", ("体育館",))},
            }
        world, subjects = load(legacy=True)
        initial = digest(loaded_parameters(subjects))
        with tempfile.TemporaryDirectory() as tmp:
            sim = ObservedSimulation(0, world, subjects, Path(tmp),
                policies={name: BoundaryPolicy(world.slots) for name in subjects})
            sim.run()
        self.assertFalse(sim.opening["primary_presence"])
        self.assertEqual(sim.opening["ao_zone"], "部室")
        after_action = [x for x in sim.observations if x["slot"] == "15:00"
                        and x["trigger"] and x["trigger"]["subject"] == "蒼"
                        and x["trigger"]["verb"] == "move"]
        self.assertEqual(after_action[0]["positions_inspector_only"]["蒼"], "体育館")
        _, fresh = load(legacy=True)
        self.assertEqual(initial, digest(loaded_parameters(fresh)))


if __name__ == "__main__":
    unittest.main()
