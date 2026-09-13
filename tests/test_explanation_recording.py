"""Behavioral invariants for opt-in explanation recording."""
import copy
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml
from engine.actions import Action
from engine.decision_record import record_distribution
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine.genome import Genome
from gapengine.policy import Policy

ROOT = Path(__file__).resolve().parents[1]


def fixture(genre):
    project = ROOT / "projects" / genre
    graph = ROOT / "templates" / genre / "action_graph.yaml"
    world = World.from_yaml(project / "world.yaml", action_graph_path=graph)
    subjects = [Subject.from_yaml(p) for p in sorted((project / "subjects").glob("*.yaml"))]
    return world, {s.id: s for s in subjects}, yaml.safe_load(graph.read_text(encoding="utf-8"))


class RecordingTests(unittest.TestCase):
    def test_bound_ties_duplicates_and_full_probability(self):
        weighted = [(Action("rest", (), {"secret": "hidden"}), 1.0) for _ in range(12)]
        original = copy.deepcopy(weighted)
        record = record_distribution(weighted, [1.0] * 12, 11)
        self.assertEqual([r["candidate_index"] for r in record["candidates"]], [0,1,2,3,4,5,6,7,11])
        self.assertEqual(record["recorded_candidates"], 9)
        self.assertTrue(record["truncated"])
        self.assertEqual(record["candidates"][-1]["probability"], 1/12)
        self.assertNotIn("hidden", json.dumps(record))
        self.assertEqual(weighted, original)
        self.assertEqual(record_distribution(weighted[:1], [2], 0)["recorded_candidates"], 1)

    def test_recorded_evolution_serial_parallel_bytes(self):
        from test_gapengine import make_reaching_project, TEMPLATE
        from gapengine.evolve import evolve
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_reaching_project(root)
            common = {"project":project,"template":TEMPLATE,"ga_seed":3,"generations":1,
                      "population":3,"seeds":1,"seed_base":8,"keep":"all","record_explanations":True}
            for processes in (1,2):
                evolve({**common,"out":root/str(processes),"processes":processes})
            first = sorted((root/"1").rglob("layers.jsonl"))
            self.assertTrue(first)
            for path in first:
                other = root/"2"/path.relative_to(root/"1")
                self.assertEqual(path.read_bytes(),other.read_bytes())
                self.assertIn("explanation_recording",json.loads(path.read_text(encoding="utf-8").splitlines()[0]))
            self.assertEqual((root/"1"/"archive.json").read_bytes(),(root/"2"/"archive.json").read_bytes())

    def test_fallback_resets_and_zone_is_before_move(self):
        world, subjects, _ = fixture("momotaro")
        sim = Simulation(1, world, subjects, Path("unused"), record_explanations=True)
        actor = subjects[world.protagonist]
        zone = actor.zone
        with patch("engine.sim.candidates", return_value=[(Action("move", ("elsewhere",)), 1)]):
            sim.choose_action(actor)
        actor.zone = "elsewhere"
        self.assertEqual(sim._decision_context["zone"], zone)
        rng = sim.rng.getstate()
        with patch("engine.sim.candidates", return_value=[]):
            sim.choose_action(actor)
        self.assertEqual(sim._decision_context["selection"]["candidates"], [])
        self.assertEqual(sim.rng.getstate(), rng)
        with patch("engine.sim.candidates", return_value=[(Action("rest"), -1)]):
            sim.choose_action(actor)
        selection = sim._decision_context["selection"]
        self.assertEqual(selection["fallback"], "non_positive_total")
        self.assertIsNone(selection["candidates"][0]["probability"])
        self.assertEqual(sim.rng.getstate(), rng)

    def test_cost_baseline_matches_execution_after_recovery_for_every_actor(self):
        world, subjects, _ = fixture("romance")
        world.days = 2
        for subject in subjects.values():
            subject.stamina = 2
        seen, recoveries = [], []
        with tempfile.TemporaryDirectory() as tmp:
            sim = Simulation(4, world, subjects, Path(tmp), record_explanations=True)
            execute = sim.verb_engine.execute
            recover = sim._recover_stamina
            def recovered():
                before = {s.id: s.stamina for s in subjects.values()}
                recover()
                recoveries.append(any(s.stamina > before[s.id] for s in subjects.values()))
            def checked(subject, action, **kwargs):
                baseline = copy.deepcopy(sim._decision_context["cost_baseline"])
                actual = subject.layer_snapshot(world, sim._present_for(subject))
                self.assertEqual(baseline["stamina"], actual["stamina"])
                self.assertEqual(baseline["resources"], actual["resources"])
                seen.append((subject.id, sim.turn, baseline))
                return execute(subject, action, **kwargs)
            with patch.object(sim.verb_engine, "execute", side_effect=checked), patch.object(sim, "_recover_stamina", side_effect=recovered):
                rows = [json.loads(l) for l in sim.run().read_text(encoding="utf-8").splitlines()]
        recorded = [r for r in rows if r.get("kind") == "decision"]
        self.assertEqual(len(recorded), len(seen))
        self.assertGreater(len(seen), len(subjects))
        self.assertTrue(any(recoveries), "exercise real passive recovery between decisions")
        self.assertEqual({r[0] for r in seen}, set(subjects))
        for row, (actor, turn, expected) in zip(recorded, seen):
            self.assertEqual(row["explanation"]["cost_baseline"], expected)
            self.assertEqual((row["subject"], row["turn"]), (actor, turn))

    def test_recording_preserves_all_rows_rng_and_npc_policies(self):
        with tempfile.TemporaryDirectory() as tmp:
            for genre in ("momotaro", "detective", "romance"):
                outputs = []
                for enabled in (False, True):
                    world, subjects, cfg = fixture(genre)
                    world.days = 3
                    raw = Genome.neutral().to_dict()
                    raw["novelty_drive"] = 0.7
                    policy = Policy(Genome.from_dict(raw), None, cfg=cfg)
                    sim = Simulation(4, world, subjects, Path(tmp)/genre/str(enabled),
                                     policies={world.protagonist: policy}, record_explanations=enabled)
                    rows = [json.loads(l) for l in sim.run().read_text(encoding="utf-8").splitlines()]
                    if enabled:
                        self.assertIn("explanation_recording", rows[0])
                        for row in rows:
                            if row.get("kind") == "decision":
                                self.assertIsNotNone(row["explanation"])
                                if row["subject"] != world.protagonist:
                                    self.assertIsNone(row["policy"])
                                sel = row["explanation"]["selection"]
                                if sel["selected_index"] is not None:
                                    chosen = [c for c in sel["candidates"] if c["selected"]]
                                    self.assertEqual(len(chosen), 1)
                                    self.assertEqual(chosen[0]["verb"], row["verb"])
                                    self.assertEqual(chosen[0]["args"], row["args"])
                                    self.assertEqual(chosen[0]["probability"], row["choice_prob"])
                    for row in rows:
                        row.pop("explanation", None)
                        row.pop("explanation_recording", None)
                    outputs.append((rows, sim.rng.getstate()))
                self.assertEqual(outputs[0], outputs[1], genre)


    def test_recorded_move_cost_reaches_extractor_as_confirmed_loss(self):
        from gapengine.explanations import extract_explanation
        world, subjects, _ = fixture("momotaro")
        world.days = 3
        with tempfile.TemporaryDirectory() as tmp:
            log = Simulation(0, world, subjects, Path(tmp), record_explanations=True).run()
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            moves = [d for d in extract_explanation(log)["decisions"] if d["verb"] == "move"
                     and d["outcome"]["result"] != "invalid"]
        self.assertTrue(moves)
        for move in moves:
            row = rows[move["line"] - 1]
            baseline = row["explanation"]["cost_baseline"]
            self.assertEqual((baseline["version"], baseline["timing"]), (1, "before_execute"))
            self.assertEqual(move["cost"]["status"], "confirmed")
            stamina = next(c for c in move["cost"]["items"] if c.get("path") == ["stamina"])
            self.assertEqual(stamina["before"], baseline["stamina"])
            self.assertLess(stamina["amount"], 0)
            self.assertEqual(stamina["after"], row["delta"]["actor"]["stamina"])


if __name__ == "__main__":
    unittest.main()
