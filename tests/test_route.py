"""WB-ROUTE-001 S0: gapengine.route's planner/annotator, and the guarantee
that wiring a Route into Policy never changes what gets chosen (see
route_s0_plan.md §5/§6)."""

from __future__ import annotations

import copy
import json
import random
import tempfile
import unittest
from pathlib import Path

from engine.actions import Action
from engine.sim import Simulation
from engine.subject import Belief, Subject
from engine.world import World
from gapengine.genome import Genome
from gapengine.policy import Policy
from gapengine.route import Route, annotate, load_route_config, plan

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "momotaro_plus2"
TEMPLATE = ROOT / "templates" / "momotaro_plus2"


def load_fixture() -> tuple[World, dict[str, Subject]]:
    world = World.from_yaml(
        PROJECT / "world.yaml",
        action_graph_path=TEMPLATE / "action_graph.yaml",
    )
    subjects = [
        Subject.from_yaml(path)
        for path in sorted((PROJECT / "subjects").glob("*.yaml"), key=lambda v: v.name)
    ]
    values = {subject.id: subject for subject in subjects}
    world.bind_subjects(values)
    return world, values


class RouteConfigTests(unittest.TestCase):
    def test_momotaro_plus2_route_config(self) -> None:
        cfg = load_route_config(TEMPLATE)
        self.assertIsNotNone(cfg)
        assert cfg is not None
        self.assertEqual(cfg["delta"], 0.02)
        self.assertEqual(cfg["holder_belief_fact"], "treasure_thief")

    def test_template_without_route_yaml_is_disabled(self) -> None:
        self.assertIsNone(load_route_config(ROOT / "templates" / "momotaro"))


class PlanTests(unittest.TestCase):
    def test_initial_state_has_finite_deterministic_plan(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        first = plan(momotaro, world, holder_belief_fact="treasure_thief")
        second = plan(momotaro, world, holder_belief_fact="treasure_thief")
        self.assertIsNotNone(first["h"])
        self.assertNotEqual(first["h"], float("inf"))
        self.assertEqual(first["believed_holder"], "鬼")
        self.assertEqual(first, second)

    def test_plan_is_read_only(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        before_inventory = dict(momotaro.inventory)
        before_zone = momotaro.zone
        before_knowledge = set(momotaro.knowledge)
        plan(momotaro, world, holder_belief_fact="treasure_thief")
        self.assertEqual(momotaro.inventory, before_inventory)
        self.assertEqual(momotaro.zone, before_zone)
        self.assertEqual(momotaro.knowledge, before_knowledge)


class AnnotateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        # The scheduled departure event moves him to 道中 on day 1 -- every
        # scenario below assumes he has already left the village so he can
        # meet 犬/猿/キジ, which all start there.
        self.momotaro.zone = "道中"

    def _present(self):
        return self.world.present_subjects(self.momotaro.zone)

    def test_neutral_kiji_fight_is_detour_none(self) -> None:
        action = Action("fight", ("キジ",), {"target": "キジ"})
        result = annotate(
            self.momotaro,
            self.world,
            self._present(),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "none")

    def test_misattributed_holder_fight_is_detour_belief(self) -> None:
        # 猿 is a living subject (not the true holder, 鬼) but the
        # protagonist confidently -- above act_threshold=0.5 -- believes 猿
        # stole the treasure.
        self.momotaro.beliefs["treasure_thief"] = Belief(value="猿", confidence=0.9)
        action = Action("fight", ("猿",), {"target": "猿"})
        result = annotate(
            self.momotaro,
            self.world,
            self._present(),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "belief")

    def test_exhausted_rest_is_detour_body(self) -> None:
        self.momotaro.exhausted = True
        action = Action("rest", meta={"under_threat": False})
        result = annotate(
            self.momotaro,
            self.world,
            self._present(),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "body")

    def test_gathering_missing_ship_material_is_advance(self) -> None:
        self.momotaro.zone = "森"
        action = Action(
            "investigate",
            ("森",),
            {"target": "森", "gather": True},
        )
        result = annotate(
            self.momotaro,
            self.world,
            self.world.present_subjects("森"),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "advance")
        self.assertEqual(result["cause"], None)

    def test_small_talk_with_a_companion_candidate_is_prepare(self) -> None:
        # 猿 is present, neutral (not yet an ally, not hostile) -- rapport
        # with him raises engine.contest.strength via the ally modifier
        # regardless of which route is currently cheapest, so this is
        # "prepare" even though it advances no item/zone/fact node
        # (2026-09-24 revision, requested after the design review found the
        # first classifier too strict to measure anything useful).
        action = Action("share_knowledge", ("猿", "雑談"), {"target": "猿", "topic": "雑談"})
        result = annotate(
            self.momotaro,
            self.world,
            self._present(),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "prepare")
        self.assertEqual(result["cause"], None)

    def test_befriending_oni_no_otouto_after_learning_of_him_is_not_a_detour(self) -> None:
        # Once 弟の消息 is known, 鬼の弟 is a legitimate waypoint toward the
        # negotiate route's offer (brother_letter_trial grants 弟の手紙).
        # negotiate is always attemptable against the true holder here, so
        # this lands as "advance"; either way it must not be a detour.
        self.momotaro.zone = "森"
        self.momotaro.knowledge.add("弟の消息")
        action = Action("persuade", ("鬼の弟",), {"target": "鬼の弟", "stance_sign": 1})
        result = annotate(
            self.momotaro,
            self.world,
            self.world.present_subjects("森"),
            [action],
            holder_belief_fact="treasure_thief",
            trial_reveal_facts={"鬼の弟": "弟の消息"},
        )[0]
        self.assertIn(result["kind"], ("advance", "prepare"))
        self.assertEqual(result["cause"], None)

    def test_giving_away_a_still_needed_material_is_detour_none(self) -> None:
        # 木材 is still needed for 船 -- handing it to 鬼の弟 (a companion
        # candidate for the generic rapport-building rule) must not be
        # credited just because the target is friendly.
        self.momotaro.zone = "森"
        action = Action(
            "give_item",
            ("鬼の弟", "木材"),
            {"target": "鬼の弟", "item": "木材", "stance_sign": 1},
        )
        result = annotate(
            self.momotaro,
            self.world,
            self.world.present_subjects("森"),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "none")

    def test_annotate_is_deterministic_and_read_only(self) -> None:
        action = Action("fight", ("キジ",), {"target": "キジ"})
        before_state = random.getstate()
        before_subject = copy.deepcopy(self.momotaro)
        first = annotate(
            self.momotaro,
            self.world,
            self._present(),
            [action],
            holder_belief_fact="treasure_thief",
        )
        second = annotate(
            self.momotaro,
            self.world,
            self._present(),
            [action],
            holder_belief_fact="treasure_thief",
        )
        self.assertEqual(first, second)
        self.assertEqual(random.getstate(), before_state)
        self.assertEqual(self.momotaro.inventory, before_subject.inventory)
        self.assertEqual(self.momotaro.zone, before_subject.zone)
        self.assertEqual(self.momotaro.knowledge, before_subject.knowledge)
        self.assertEqual(self.momotaro.beliefs, before_subject.beliefs)


class RouteWiringByteIdenticalTests(unittest.TestCase):
    """Plan §5/§6: with route enabled, every non-route field of a run must
    stay byte-identical to the same run without it -- route only adds
    action.meta["policy"]["route"] to decision rows."""

    def _run(self, *, with_route: bool, seed: int = 1) -> list[dict]:
        world, subjects = load_fixture()
        action_cfg = {"nodes": [], "edges": []}
        route = Route.from_config(load_route_config(TEMPLATE)) if with_route else None
        policy = Policy(Genome.neutral(), precedent=None, cfg=action_cfg, route=route)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            Simulation(
                seed,
                world,
                subjects,
                out_dir,
                policies={world.protagonist: policy},
            ).run()
            rows = [
                json.loads(line)
                for line in (out_dir / "layers.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        return rows

    def test_route_only_adds_the_route_key(self) -> None:
        with_route = self._run(with_route=True)
        without_route = self._run(with_route=False)
        self.assertEqual(len(with_route), len(without_route))

        stripped_with = []
        saw_route_key = False
        for row in with_route:
            policy_meta = row.get("policy")
            if isinstance(policy_meta, dict) and "route" in policy_meta:
                saw_route_key = True
                policy_meta = {k: v for k, v in policy_meta.items() if k != "route"}
                row = {**row, "policy": policy_meta}
            stripped_with.append(row)

        self.assertTrue(saw_route_key, "expected at least one decision to carry policy.route")
        self.assertEqual(stripped_with, without_route)


if __name__ == "__main__":
    unittest.main()
