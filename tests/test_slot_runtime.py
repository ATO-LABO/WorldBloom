"""Native temporal semantics, epistemic scope, real readiness, and opt-in isolation."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import yaml

from engine.actions import Action
from engine.log import LayersWriter
from engine.sim import Simulation
from engine.world import World
from scripts.cultural_festival_probe import PROJECT, input_manifest, load
from scripts.cultural_festival_trial import FestivalPolicy, run_trial


class SlotRuntimeTests(unittest.TestCase):
    def test_pattern0_native_events_knowledge_and_replay(self):
        before = input_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = run_trial(root / "first"), run_trial(root / "second")
            self.assertTrue(first["pattern0_pass"])
            self.assertEqual(first["native_layers_sha256"], second["native_layers_sha256"])
            self.assertEqual(first["input_manifest"], second["input_manifest"])
            self.assertEqual(first["loaded_initial_parameters_sha256"], second["loaded_initial_parameters_sha256"])
            rows = [json.loads(line) for line in (root / "first/layers.jsonl").read_text().splitlines()]
            completed = [r for r in rows if r.get("verb") == "action_completed"]
            self.assertEqual(len(completed), 4 * 22)
            self.assertFalse(any(r["kind"] == "decision" and r["slot"] == "17:00" for r in rows))
            first_move = next(r for r in completed if r["subject"] == "蒼" and r["details"]["action"] == "move")
            self.assertEqual(first_move["slot"], "08:30")
            self.assertEqual(first_move["details"]["start_t"], 4)
            self.assertEqual(first_move["details"]["hops"], 1)
            for r in completed:
                self.assertEqual(r["details"]["completed_t"], r["details"]["start_t"] + 1)
            snapshots = {(r["subject"], r["slot"]): r for r in rows if r["kind"] == "knowledge_snapshot"}
            self.assertNotIn("終盤短縮希望", snapshots["蒼", "09:30"]["knowledge"])
            self.assertIn("終盤短縮希望", snapshots["蒼", "10:00"]["knowledge"])
            self.assertIn("終盤短縮希望", snapshots["紗季", "10:00"]["knowledge"])
            self.assertNotIn("終盤短縮希望", snapshots["灯", "10:00"]["knowledge"])
            states = {r["slot"]: r["state"] for r in rows if r["kind"] == "activity_snapshot"}
            self.assertFalse(states["09:00"]["costume_prepared"]["蒼"])
            self.assertTrue(states["09:30"]["costume_prepared"]["蒼"])
            self.assertFalse(states["12:00"]["sound_checked"])
            self.assertTrue(states["12:30"]["sound_checked"])
            self.assertEqual(states["10:00"]["responses"], [])
            self.assertEqual(states["10:30"]["responses"][0]["intent"], "accept")
            for person in ("蒼", "紗季", "蓮", "灯"):
                for slot in ("06:00", "15:00"):
                    own = snapshots[person, slot]
                    room = [o for o in own["perceptions"] if o["kind"] == "room_observation"][-1]
                    self.assertEqual(own["own_zone"], room["zone"])
                    self.assertNotIn("positions_inspector_only", own)
            opening_i = next(i for i, r in enumerate(rows) if r.get("verb") == "opening_check")
            self.assertTrue(all(i < opening_i for i, r in enumerate(rows)
                if r.get("verb") == "action_completed" and r["slot"] == "15:00"))
            self.assertTrue(all(i > opening_i for i, r in enumerate(rows)
                if r["kind"] == "decision" and r["slot"] == "15:00"))
        self.assertEqual(before, input_manifest())

    def test_remote_delivery_waits_for_completion_and_is_private(self):
        world, people = load()
        sim = Simulation(0, world, people, Path('/tmp/unused'))
        runtime = sim.slot_runtime
        with tempfile.TemporaryDirectory() as tmp, LayersWriter(Path(tmp) / 'layers.jsonl') as writer:
            runtime.writer = writer
            sim.day, sim.turn, sim.slot = 1, 1, '06:00'
            runtime.start(people['蓮'], Action('dialogue', ('蒼', '終盤短縮希望', 'request', 'contact')), 1)
            self.assertNotIn('終盤短縮希望', people['蒼'].knowledge)
            self.assertEqual(runtime.state['requests'], [])
            sim.turn, sim.slot = 2, '06:30'
            runtime.complete(runtime.pending.pop())
            self.assertIn('終盤短縮希望', people['蒼'].knowledge)
            self.assertNotIn('終盤短縮希望', people['紗季'].knowledge)
            self.assertNotIn('終盤短縮希望', people['灯'].knowledge)
            self.assertEqual(runtime.state['requests'][0]['completed_t'], 1)

    def test_15_start_move_cannot_change_primary_and_is_late(self):
        class BoundaryPolicy(FestivalPolicy):
            PLANS = deepcopy(FestivalPolicy.PLANS)
            PLANS['蒼']['13:30'] = ('exhibit_help', ())
            PLANS['蒼']['14:00'] = ('exhibit_help', ())
        world, people = load()
        with tempfile.TemporaryDirectory() as tmp:
            sim = Simulation(0, world, people, Path(tmp),
                policies={name: BoundaryPolicy(world.slots) for name in people})
            sim.run()
            self.assertFalse(sim.slot_runtime.opening['primary_presence'])
            self.assertEqual(sim.slot_runtime.opening['ao_zone'], '展示室')
            self.assertTrue(sim.slot_runtime.opening['readiness']['costume_prepared'])
            arrivals = sim.slot_runtime.arrivals_after_opening
            self.assertEqual(arrivals[0], {'slot': '15:30', 'zone': '体育館'})

    def test_candidates_adjacent_closed_gate_and_known_request_only(self):
        world, people = load()
        sim = Simulation(0, world, people, Path('/tmp/unused'))
        runtime = sim.slot_runtime
        self.assertFalse(any(a.verb == 'move' for a, _ in runtime.candidates(people['蒼'])))
        people['蒼'].add_item('校門通行可', 1)  # isolated capability fixture, not a candidate run
        moves = [a.args[0] for a, _ in runtime.candidates(people['蒼']) if a.verb == 'move']
        self.assertEqual(moves, ['校門'])
        self.assertFalse(any(a.verb == 'dialogue' and a.args[2] in ('accept', 'decline')
                             for a, _ in runtime.candidates(people['蒼'])))
        self.assertFalse(any(a.verb == 'joint_transport' for a, _ in runtime.candidates(people['蒼'])))
        self.assertFalse(any(a.verb == 'sound_check' for a, _ in runtime.candidates(people['蒼'])))

    def test_in_place_decline_is_distinct_from_absence_or_failure(self):
        class DeclinePolicy(FestivalPolicy):
            PLANS = deepcopy(FestivalPolicy.PLANS)
            PLANS['蒼']['14:30'] = ('dialogue', ('紗季', '出演', 'decline', 'room'))
        world, people = load()
        with tempfile.TemporaryDirectory() as tmp:
            sim = Simulation(0, world, people, Path(tmp),
                policies={name: DeclinePolicy(world.slots) for name in people})
            sim.run()
        opening = sim.slot_runtime.opening
        self.assertTrue(opening['primary_presence'])
        self.assertTrue(opening['declines_to_perform'])
        self.assertFalse(opening['execution_failure'])
        self.assertTrue(opening['readiness']['sound_checked'])

    def test_configuration_fail_closed_and_legacy_opt_out(self):
        raw = yaml.safe_load((PROJECT / 'world.yaml').read_text())
        for mutate in (lambda w: w['slot_execution'].update(mode='unknown'),
                       lambda w: w['time'].update(days=2),
                       lambda w: w['scheduled_events'][0].update(move_to='体育館')):
            invalid = deepcopy(raw)
            mutate(invalid)
            with self.assertRaises(ValueError):
                World(invalid, PROJECT / 'world.yaml')
        world, people = load(legacy=True)
        self.assertIsNone(Simulation(0, world, people, Path('/tmp/unused')).slot_runtime)


if __name__ == '__main__':
    unittest.main()
