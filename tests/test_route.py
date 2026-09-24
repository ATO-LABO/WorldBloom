"""WB-ROUTE-001 S0: gapengine.route's planner/annotator, and the guarantee
that wiring a Route into Policy never changes what gets chosen. Design:
Notion 設計子ページ https://app.notion.com/p/3e5e21ef1cac81218cb0c01e4a60446d."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

import yaml

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

    def test_befriending_oni_no_otouto_after_learning_of_him_is_prepare(self) -> None:
        # Once 弟の消息 is known, 鬼の弟 is a legitimate *alternative*
        # waypoint toward a negotiate offer (brother_letter_trial grants
        # 弟の手紙) -- but momotaro already starts holding 勾玉, a free
        # negotiate offer, so the *winning* plan never actually needs
        # 弟の手紙: this is "prepare" (an alt-branch tag), not "advance"
        # (2026-09-24 review fix 2 -- the best/alt split -- pinned to a
        # single expected value instead of accepting either, per the
        # review's recommended fix 5).
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
        self.assertEqual(result["kind"], "prepare")
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
        # 2026-09-24 review recommended fix 5: random.getstate() proves
        # nothing (annotate() never touches the `random` module at all, so
        # this always trivially passes regardless of whether the planner
        # mutates anything) -- check actual world invariance instead
        # (every subject's inventory plus the full relations snapshot).
        action = Action("fight", ("キジ",), {"target": "キジ"})
        before_inventories = {
            subject_id: dict(subject.inventory)
            for subject_id, subject in self.world.subjects.items()
        }
        before_relations = self.world.relations.snapshot()
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
        after_inventories = {
            subject_id: dict(subject.inventory)
            for subject_id, subject in self.world.subjects.items()
        }
        self.assertEqual(after_inventories, before_inventories)
        self.assertEqual(self.world.relations.snapshot(), before_relations)


class ReviewReproductionTests(unittest.TestCase):
    """2026-09-24 Opus review (234a090, 1st pass): R1-R5 reproduction cases
    from scratchpad/review/repro.py, pinned as regression tests. Each is
    marked with which required fix it exercises."""

    TRF = {"鬼の弟": "弟の消息"}

    def _annotate(self, setup, actions):
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        setup(momotaro, world)
        present = world.present_subjects(momotaro.zone)
        out = annotate(
            momotaro, world, present, actions,
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )
        return momotaro, world, out

    def test_r1_retreating_before_engaging_is_not_advance(self) -> None:
        # Required fix 1: holding 船+勾玉 at 海 (holder 鬼's zone is directly
        # reachable in one hop), moving away to 道中 must not be "advance"
        # just because 道中 happens to sit on the *eventual* homeward leg.
        def setup(m, w):
            m.zone = "海"
            m.inventory["船"] = 1

        _m, _w, out = self._annotate(
            setup,
            [Action("move", ("道中",), {"dest": "道中"}), Action("move", ("鬼ヶ島",), {"dest": "鬼ヶ島"})],
        )
        self.assertNotEqual(out[0]["kind"], "advance")
        self.assertEqual(out[1]["kind"], "advance")

    def test_r1_h_does_not_increase_when_moving_toward_the_holder(self) -> None:
        def setup(m, w):
            m.zone = "海"
            m.inventory["船"] = 1

        momotaro, world, _out = self._annotate(setup, [])
        h_at_sea = plan(momotaro, world, holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF)["h"]
        momotaro.zone = "道中"
        h_at_road = plan(momotaro, world, holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF)["h"]
        self.assertLess(h_at_sea, h_at_road)

    def test_r3_craft_while_already_holding_one_is_not_advance(self) -> None:
        # Required fix 3: _best_offer must not tag an already-held item.
        def setup(m, w):
            m.zone = "道中"
            m.inventory["鉄砲"] = 1

        _m, _w, out = self._annotate(setup, [Action("craft", ("鉄砲",), {})])
        self.assertNotEqual(out[0]["kind"], "advance")

    def test_r4_giving_away_exactly_sufficient_material_is_detour(self) -> None:
        # Required fix 4: holding *exactly* 木材=2 (船's own requirement, no
        # surplus) must still block crediting a give_item that would leave
        # only 1.
        def setup(m, w):
            m.zone = "道中"
            m.inventory["木材"] = 2

        _m, _w, out = self._annotate(
            setup,
            [Action("give_item", ("猿", "木材"), {"target": "猿", "item": "木材"})],
        )
        self.assertEqual(out[0]["kind"], "detour")
        self.assertEqual(out[0]["cause"], "none")

    def test_r5_unused_alternative_offer_is_prepare_not_advance(self) -> None:
        # Required fix 2: momotaro starts holding 勾玉 (a free negotiate
        # offer), so investigating 小判 (toward crafting a second,
        # unnecessary offer, 鉄砲) must not be "advance".
        def setup(m, w):
            m.zone = "道中"

        _m, _w, out = self._annotate(
            setup,
            [Action("investigate", ("道中",), {"target": "道中", "gather": True})],
        )
        self.assertNotEqual(out[0]["kind"], "advance")

    def test_r8_hidden_modifier_possession_is_not_read(self) -> None:
        # Required/recommended fix 8: 金棒 has visible:false and momotaro
        # has no known_modifiers entry for 鬼 -- a negotiate offer search
        # must not silently know 鬼 already holds it.
        from gapengine.route import _holder_appears_to_have

        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni = subjects["鬼"]
        self.assertTrue(oni.has_item("金棒"))
        self.assertFalse(_holder_appears_to_have(momotaro, oni, "金棒", world))


class RouteWiringByteIdenticalTests(unittest.TestCase):
    """Plan §5/§6: with route enabled, every non-route field of a run must
    stay byte-identical to the same run without it -- route only adds
    action.meta["policy"]["route"] to decision rows."""

    def _run(self, *, with_route: bool, seed: int, genome: Genome) -> list[dict]:
        world, subjects = load_fixture()
        # 2026-09-24 review recommended fix 5: the real action_graph.yaml
        # (permissions/risk classes), not an empty stub -- an empty graph
        # skips whole code paths (e.g. every permission check short-circuits
        # to "allow") that a real run exercises.
        action_cfg = yaml.safe_load((TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8"))
        route = Route.from_config(load_route_config(TEMPLATE)) if with_route else None
        policy = Policy(genome, precedent=None, cfg=action_cfg, route=route)
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
        # 2026-09-24 review recommended fix 5: a neutral genome takes the
        # annotate-only shortcut in Policy.reweight regardless of route, so
        # it alone can't prove route leaves *weighted* decisions untouched.
        # Cover a real (uniform-random) genome across several seeds too.
        rng = random.Random("WB-ROUTE-001-review-byte-identity")
        genomes = [Genome.neutral()] + [Genome.random(rng) for _ in range(2)]
        saw_route_key = False
        for genome in genomes:
            for seed in (1, 2, 3):
                with_route = self._run(with_route=True, seed=seed, genome=genome)
                without_route = self._run(with_route=False, seed=seed, genome=genome)
                self.assertEqual(len(with_route), len(without_route))

                stripped_with = []
                for row in with_route:
                    policy_meta = row.get("policy")
                    if isinstance(policy_meta, dict) and "route" in policy_meta:
                        saw_route_key = True
                        policy_meta = {k: v for k, v in policy_meta.items() if k != "route"}
                        row = {**row, "policy": policy_meta}
                    stripped_with.append(row)

                self.assertEqual(stripped_with, without_route)

        self.assertTrue(saw_route_key, "expected at least one decision to carry policy.route")


if __name__ == "__main__":
    unittest.main()
