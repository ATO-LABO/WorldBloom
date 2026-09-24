"""WB-ROUTE-001 S0: gapengine.route's planner/annotator, and the guarantee
that wiring a Route into Policy never changes what gets chosen. Design:
Notion 設計子ページ https://app.notion.com/p/3e5e21ef1cac81218cb0c01e4a60446d."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from engine.actions import Action
from engine.sim import Simulation
from engine.subject import Belief, Subject
from engine.world import World
from gapengine.evolve import evolve
from gapengine.genome import Genome
from gapengine.policy import Policy
from gapengine.route import (
    Route,
    _acquire,
    _acquire_from_subject,
    annotate,
    load_route_config,
    plan,
)

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
        # S1 §1.2 update: at this fixture's zone (道中, without 縄 --
        # 村's investigate source for 縄 is excluded once momotaro has left
        # without the treasure), the plan is genuinely unreachable (h=inf)
        # even in S0 -- S1 now classifies every decision here as "lost"
        # (cause still resolves to "none", since fighting キジ has no
        # body/belief justification either) rather than folding it into
        # "detour".
        action = Action("fight", ("キジ",), {"target": "キジ"})
        result = annotate(
            self.momotaro,
            self.world,
            self._present(),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "lost")
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
        # S1 §1.2 update: same unreachable-plan state as
        # test_neutral_kiji_fight_is_detour_none above -- kind is "lost",
        # but the body cause still comes through (_lost_cause shares
        # _body_or_belief_cause with _detour_cause).
        self.momotaro.exhausted = True
        action = Action("rest", meta={"under_threat": False})
        result = annotate(
            self.momotaro,
            self.world,
            self._present(),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "lost")
        self.assertEqual(result["cause"], "body")

    def test_gathering_missing_ship_material_is_advance(self) -> None:
        self.momotaro.zone = "森"
        # 縄 is granted for free by the day-1 scheduled event before the
        # protagonist ever leaves 村 -- give it here too, so this scenario
        # matches a state the real simulation can actually reach (縄's own
        # investigate source is 村, which range.exclude now correctly
        # blocks once he's left without the treasure -- review 2 required
        # fix B; without this, the whole plan is genuinely unreachable and
        # the scenario stops testing what it means to).
        self.momotaro.inventory["縄"] = 1
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
        # regardless of which route is currently cheapest, so this was
        # "prepare" even though it advances no item/zone/fact node
        # (2026-09-24 revision, requested after the design review found the
        # first classifier too strict to measure anything useful).
        #
        # S1 §1.2 update: this fixture's zone (道中, without 縄) is now a
        # genuinely unreachable plan (h=inf, see
        # test_neutral_kiji_fight_is_detour_none above) -- every decision
        # there, including this one, is "lost" rather than "prepare"; the
        # companion-candidate rule is a best/alt-plan-scoped rule and is
        # never consulted once the plan itself is unreachable. m_route=1
        # for "lost" (no modulation at all) rather than "prepare"'s
        # b**rho<1.0 keeps this from ever being *penalized* just because
        # the wider plan happens to be unreachable right now.
        action = Action("share_knowledge", ("猿", "雑談"), {"target": "猿", "topic": "雑談"})
        result = annotate(
            self.momotaro,
            self.world,
            self._present(),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "lost")
        self.assertEqual(result["cause"], "none")

    def test_befriending_oni_no_otouto_after_learning_of_him_is_prepare(self) -> None:
        # Once 弟の消息 is known, 鬼の弟 is a legitimate *alternative*
        # waypoint toward a negotiate offer (brother_letter_trial grants
        # 弟の手紙) -- but momotaro already starts holding 勾玉, a free
        # negotiate offer, so the *winning* plan never actually needs
        # 弟の手紙: this is "prepare" (an alt-branch tag), not "advance"
        # (review fix 2 -- the best/alt split -- pinned to a single expected
        # value instead of accepting either, per the review's recommended
        # fix 5). Credited via the stance_ge tag -- 弟の手紙 has a positive
        # modifier and momotaro doesn't hold one yet, so review 3's
        # _first_strength_item also independently picks it up (fix 3) --
        # never via the generic companion rule, since 鬼の弟's ally_value is
        # 0 (review 3 required fix 2).
        self.momotaro.zone = "森"
        self.momotaro.knowledge.add("弟の消息")
        self.momotaro.inventory["縄"] = 1  # see test_gathering_missing_ship_material_is_advance
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
        self.momotaro.inventory["縄"] = 1  # see test_gathering_missing_ship_material_is_advance
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


class S1CarryoverTests(unittest.TestCase):
    """WB-ROUTE-001 S1 §5: the four carried-over S0 fixes, checked
    directly against the scenarios that motivated them (route_s1_carryover.md
    items 1/2/3/4)."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]

    def test_leaving_a_zone_with_an_actionable_leaf_is_not_advance(self) -> None:
        # §1.1: at the initial state (村), both open leaves -- 木材@森 and
        # 縄@村 -- include one at momotaro's own zone, so leaving for 森
        # (still on the best route) must not read as "advance" (leaving
        # before finishing what's here isn't the shortest path); it's
        # "prepare" instead.
        action = Action("move", ("森",), {"dest": "森"})
        result = annotate(
            self.momotaro,
            self.world,
            self.world.present_subjects("村"),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "prepare")

    def test_move_toward_the_nearest_open_leaf_is_advance_others_are_prepare(self) -> None:
        # §1.1: once nothing is actionable at the current zone (道中, with
        # 縄 already held), only a move toward the *nearest* remaining leaf
        # (木材@森, one hop) is "advance" -- a move toward a farther best-route
        # zone (海/鬼ヶ島) is still a reasonable "prepare", never "advance".
        self.momotaro.zone = "道中"
        self.momotaro.inventory["縄"] = 1
        present = self.world.present_subjects("道中")
        to_forest = Action("move", ("森",), {"dest": "森"})
        to_sea = Action("move", ("海",), {"dest": "海"})
        results = annotate(
            self.momotaro, self.world, present, [to_forest, to_sea],
            holder_belief_fact="treasure_thief",
        )
        self.assertEqual(results[0]["kind"], "advance")
        self.assertEqual(results[1]["kind"], "prepare")

    def test_unreachable_plan_is_kind_lost_not_detour(self) -> None:
        # §1.2: h==inf (縄's only source, 村, is excluded once momotaro has
        # left without the treasure) must be its own "lost" kind, with
        # cause falling back to "none" absent a body/belief reason -- never
        # folded into the old ignorance-flavored detour bucket.
        self.momotaro.zone = "道中"
        action = Action("fight", ("キジ",), {"target": "キジ"})
        result = annotate(
            self.momotaro,
            self.world,
            self.world.present_subjects("道中"),
            [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "lost")
        self.assertEqual(result["cause"], "none")
        self.assertEqual(result["h"], [None, None])

    def test_neutral_holder_is_never_a_fight_candidate_for_materials(self) -> None:
        # §1.3: 猿 is a neutral (not hostile) companion candidate --
        # action_graph.yaml's fight permission for "neutral" is "restricted",
        # not "allow", so the planner must never register him as a fight
        # target for materials he might hold (which used to tag him
        # boost_fight-worthy and made observe/train against him read as
        # "prepare" for no good reason).
        saru = self.subjects["猿"]
        self.momotaro.zone = saru.zone
        h, best, alt, route_name = _acquire_from_subject(
            "木材", saru, self.momotaro, self.world, frozenset(), {}, is_objective=False
        )
        self.assertEqual(h, float("inf"))
        self.assertEqual(best, frozenset())
        self.assertIsNone(route_name)

    def test_h_scales_with_the_full_deficit_not_a_flat_one(self) -> None:
        # §1.4: gathering a deficit of n units (小判, count=1/action) costs
        # n investigate actions, not a flat 1 regardless of n.
        self.momotaro.zone = "道中"  # 小判's own investigate source zone
        h_missing_three, _, _ = _acquire(
            "小判", self.momotaro, self.world, frozenset(), {}, needed=3
        )
        self.momotaro.inventory["小判"] = 1
        h_missing_two, _, _ = _acquire(
            "小判", self.momotaro, self.world, frozenset(), {}, needed=3
        )
        self.assertEqual(h_missing_three, 3.0)
        self.assertEqual(h_missing_two, 2.0)


class RouteMultiplierTests(unittest.TestCase):
    """WB-ROUTE-001 S1 §2/§5: m_route = b**rho, and rho<=0 is a strict
    mathematical/behavioral no-op."""

    def test_multiplier_is_base_to_the_rho(self) -> None:
        route = Route(rho=0.5)
        for kind, cause, key in (
            ("advance", None, "advance"),
            ("prepare", None, "prepare"),
            ("detour", "body", "detour:body"),
            ("detour", "belief", "detour:belief"),
            ("detour", "ignorance", "detour:ignorance"),
            ("detour", "none", "detour:none"),
            ("lost", None, "lost"),
        ):
            expected = route.multipliers[key] ** 0.5
            self.assertAlmostEqual(route.multiplier(kind, cause), expected)

    def test_rho_zero_disables_all_modulation(self) -> None:
        route = Route(rho=0.0, multipliers={"detour:none": 0.02})
        self.assertFalse(route.enabled)
        self.assertEqual(route.multiplier("detour", "none"), 1.0)
        self.assertEqual(route.multiplier("advance", None), 1.0)

    def test_rho_zero_policy_output_matches_route_free_policy(self) -> None:
        # rho<=0 must leave Policy.reweight's chosen weights *and* meta
        # byte-identical to no Route at all, for a genome with real rules
        # (not the neutral-genome annotate-only shortcut).
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "道中"
        action_cfg = yaml.safe_load((TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8"))
        rng = random.Random("WB-ROUTE-001-S1-rho-zero")
        genome = Genome.random(rng)
        present = world.present_subjects(momotaro.zone)

        def _fresh_weighted() -> list[tuple[Action, float]]:
            return [
                (Action("fight", ("キジ",), {"target": "キジ"}), 1.0),
                (Action("move", ("森",), {"dest": "森"}), 1.0),
            ]

        route = Route.from_config(load_route_config(TEMPLATE))
        self.assertEqual(route.rho, 0.0)
        with_route = Policy(genome, precedent=None, cfg=action_cfg, route=route).reweight(
            momotaro, world, present, _fresh_weighted()
        )
        without_route = Policy(genome, precedent=None, cfg=action_cfg, route=None).reweight(
            momotaro, world, present, _fresh_weighted()
        )
        self.assertEqual(
            [(action.verb, action.args, round(weight, 12)) for action, weight in with_route],
            [(action.verb, action.args, round(weight, 12)) for action, weight in without_route],
        )
        for action, _weight in with_route:
            self.assertNotIn("m_route", action.meta.get("policy", {}))

    def test_neutral_genome_is_still_modulated_when_rho_positive(self) -> None:
        # S1 §2: route is a constraint on the plan, not a personality trait
        # -- a neutral genome (which otherwise takes Policy's
        # annotation-only shortcut) must still have its weights modulated
        # once rho>0.
        # 森, holding 縄 (see AnnotateTests.test_giving_away_a_still_needed_
        # material_is_detour_none): 木材 is still needed for 船, so handing
        # it to 鬼の弟 (a companion candidate) is a genuine detour/none --
        # a finite-h state, unlike the "lost" (h=inf) states used
        # elsewhere in this file for the plain fixture zone (道中 without
        # 縄).
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "森"
        momotaro.inventory["縄"] = 1
        action_cfg = yaml.safe_load((TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8"))
        present = world.present_subjects(momotaro.zone)
        weighted = [
            (
                Action(
                    "give_item",
                    ("鬼の弟", "木材"),
                    {"target": "鬼の弟", "item": "木材", "stance_sign": 1},
                ),
                1.0,
            )
        ]
        route = Route.from_config(load_route_config(TEMPLATE))
        route.rho = 1.0
        policy = Policy(Genome.neutral(), precedent=None, cfg=action_cfg, route=route)
        output = policy.reweight(momotaro, world, present, weighted)
        self.assertEqual(len(output), 1)
        action, weight = output[0]
        self.assertEqual(action.meta["policy"]["route"]["kind"], "detour")
        self.assertEqual(action.meta["policy"]["route"]["cause"], "none")
        self.assertAlmostEqual(action.meta["policy"]["m_route"], route.multipliers["detour:none"])
        self.assertAlmostEqual(weight, 1.0 * route.multipliers["detour:none"])


class RouteEvolveWiringTests(unittest.TestCase):
    """WB-ROUTE-001 S1 §3: cfg["route"]["rho"]/--route-rho threading through
    gapengine.evolve into the protagonist's Policy, and into the run
    header (engine.sim.Simulation._header) -- only when rho>0."""

    def _run_evolve(self, *, route_cfg: dict | None, project=None, template=None) -> Path:
        project = project or (ROOT / "projects" / "momotaro_plus2")
        template = template or TEMPLATE
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out_dir = Path(tmp.name) / "out"
        cfg: dict = {
            "project": project,
            "template": template,
            "out": out_dir,
            "generations": 1,
            "population": 1,
            "seeds": 1,
            "keep": "all",
        }
        if route_cfg is not None:
            cfg["route"] = route_cfg
        evolve(cfg)
        return out_dir

    def _header(self, out_dir: Path) -> dict:
        layer_files = sorted((out_dir / "g0" / "ind-0").glob("seed-*/layers.jsonl"))
        self.assertTrue(layer_files)
        first_line = layer_files[0].read_text(encoding="utf-8").splitlines()[0]
        return json.loads(first_line)

    def test_rho_zero_default_adds_no_route_header_key(self) -> None:
        out_dir = self._run_evolve(route_cfg=None)
        header = self._header(out_dir)
        self.assertNotIn("route", header)

    def test_rho_positive_adds_route_header_key(self) -> None:
        out_dir = self._run_evolve(route_cfg={"rho": 1.0})
        header = self._header(out_dir)
        self.assertIn("route", header)
        self.assertEqual(header["route"]["rho"], 1.0)
        self.assertIsInstance(header["route"]["multipliers_hash"], str)

    def test_rho_positive_without_route_yaml_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._run_evolve(
                route_cfg={"rho": 1.0},
                project=ROOT / "projects" / "momotaro",
                template=ROOT / "templates" / "momotaro",
            )


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
            m.inventory["縄"] = 1  # the day-1 scheduled grant; 村 (its own
            # investigate source) is otherwise range.exclude'd (fix B)

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

    def test_r6_short_by_one_unit_is_still_advance(self) -> None:
        # Opus review 2, required fix A: _acquire's old unconditional
        # subject.has_item(item) (>=1) early-return made holding 1 of a
        # material a recipe needs 2 of read as fully satisfied -- gathering
        # more must stay "advance" whether the subject holds 0 or 1 already.
        def setup_zero(m, w):
            m.zone = "道中"
            m.inventory["縄"] = 1

        def setup_one(m, w):
            m.zone = "道中"
            m.inventory["縄"] = 1
            m.inventory["木材"] = 1

        for setup in (setup_zero, setup_one):
            _m, _w, out = self._annotate(
                setup, [Action("move", ("森",), {"dest": "森"})]
            )
            self.assertEqual(out[0]["kind"], "advance", msg=str(setup))

    def test_r7_negotiate_never_used_for_a_non_objective_item(self) -> None:
        # Required fix B: the real engine's _negotiate_candidates only ever
        # offers to negotiate for the subject's own goal.target -- a
        # subject holding a raw material (縄) must only be planned via
        # fight, never "negotiated" for.
        #
        # S1 §1.3 update: the holder must be one the fight permission table
        # actually allows fighting (犬/猿/キジ are now excluded as fight
        # targets entirely -- see
        # S1CarryoverTests.test_neutral_holder_is_never_a_fight_candidate_for_materials)
        # -- 鬼 (the only hostile subject in this template) is used here
        # instead, with 船 already held so 鬼ヶ島 is reachable.
        # Tests _acquire("縄", ...) directly rather than the full plan():
        # with 船 already held (needed so 鬼ヶ島 is reachable at all for the
        # fight-permission check above), the overall objective plan no
        # longer needs 縄 for anything -- 船 being the only reason 縄 was
        # ever wanted -- so plan()'s own best/alt would never mention it.
        from gapengine.route import _acquire

        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "道中"
        momotaro.inventory["船"] = 1
        world.subjects["鬼"].inventory["縄"] = 1
        _h, best, alt = _acquire("縄", momotaro, world, frozenset(), self.TRF)
        # 縄's own acquisition sub-problem must be tagged "fight", not
        # "negotiate" -- and no stance_ge/offer-building tag for 縄's holder
        # (鬼) should appear at all, since negotiate was never considered
        # for a non-objective item.
        self.assertIn(("win_fight", "鬼"), best)
        self.assertNotIn(("stance_ge", "鬼"), best)
        self.assertNotIn(("stance_ge", "鬼"), alt)

    def test_recommended1_delivery_leg_does_not_double_count_the_boat(self) -> None:
        # Recommended fix 1: the delivery leg (origin=holder's zone) must
        # assume a persistent (vehicle) item acquired on the way there is
        # still in hand for the trip home, not silently re-plan building a
        # second one. 鬼ヶ島 -> 海 -> 道中 -> 村 is 3 hops.
        from gapengine.route import _travel

        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "道中"
        momotaro.inventory["木材"] = 2
        momotaro.inventory["縄"] = 1
        deliver_h, _best, _alt = _travel(
            momotaro, world, "村", frozenset(), self.TRF,
            origin="鬼ヶ島", excluded=frozenset(), assume_held=frozenset({"船"}),
        )
        self.assertEqual(deliver_h, 3.0)

        # Without assume_held, a subject who genuinely doesn't hold 船 (and
        # never will by assumption) legitimately has to build it again --
        # this asserts the *fix* is what changes the number, not that "5"
        # was never a valid answer to any question.
        naive_h, _best2, _alt2 = _travel(
            momotaro, world, "村", frozenset(), self.TRF, origin="鬼ヶ島", excluded=frozenset(),
        )
        self.assertGreater(naive_h, deliver_h)

    def test_recommended2_protection_is_scoped_to_the_live_plan(self) -> None:
        # Recommended fix 2: giving away the *last* きびだんご must not be
        # blocked by 猿's trial (monkey_shortcut_trial, requires きびだんご)
        # when that trial's grant (猿の知恵) is nowhere on the current
        # best/alt plan -- momotaro already holds a free offer (勾玉).
        #
        # S1 §1.2 update: this fixture's zone (道中, without 縄) is the same
        # unreachable-plan state as test_neutral_kiji_fight_is_detour_none
        # above (h=inf) -- every decision there is "lost" now, including
        # this give_item. The original "not blocked by an off-plan trial"
        # claim is best/alt-plan-scoped and has nothing to assert once the
        # plan itself is unreachable; what remains true and worth keeping
        # is that this never regresses to "detour" (which -- unlike "lost"
        # -- would still carry a b**rho<1.0 penalty at rho>0).
        def setup(m, w):
            m.zone = "道中"
            m.inventory["きびだんご"] = 1

        _m, _w, out = self._annotate(
            setup,
            [Action("give_item", ("犬", "きびだんご"), {"target": "犬", "item": "きびだんご"})],
        )
        self.assertEqual(out[0]["kind"], "lost")

    def test_design_c_a_second_copy_of_a_held_item_is_a_detour(self) -> None:
        # Design decision C (confirmed by the design role) governs a
        # genuinely *redundant* second copy: momotaro already holds 勾玉
        # (free offer), 船 (boat), and a 鉄砲 -- nothing left needs 道中, so
        # moving there is a plain detour. (Review 3 required fix 3 narrowed
        # this: a *first*, not-yet-held strength item is prepare regardless
        # of an unrelated free offer -- see
        # test_fix3_first_strength_item_is_prepare_even_with_a_free_offer --
        # only a second copy of something already in hand stays a detour.)
        def setup(m, w):
            m.zone = "海"
            m.inventory["船"] = 1
            m.inventory["鉄砲"] = 1

        _m, _w, out = self._annotate(
            setup, [Action("move", ("道中",), {"dest": "道中"})]
        )
        self.assertEqual(out[0]["kind"], "detour")
        self.assertEqual(out[0]["cause"], "none")

    def test_fix3_first_strength_item_is_prepare_even_with_a_free_offer(self) -> None:
        # Required fix 3 (2026-09-24 review 3): a *first* copy of a
        # not-yet-held positive-strength-modifier item (鉄砲) is prepare
        # regardless of design decision C -- it raises engine.contest.
        # strength() in any fight, independent of the free 勾玉 offer.
        def setup(m, w):
            m.zone = "道中"
            m.inventory["縄"] = 1  # see test_gathering_missing_ship_material_is_advance

        _m, _w, out = self._annotate(
            setup,
            [Action("investigate", ("道中",), {"target": "道中", "gather": True})],
        )
        self.assertEqual(out[0]["kind"], "prepare")
        self.assertEqual(out[0]["cause"], None)

    def test_fix2_ally_value_zero_target_is_not_a_generic_companion(self) -> None:
        # Required fix 2: 鬼の弟's ally_value is 0 -- recruiting him adds
        # nothing to strength() -- so the generic "any present non-hostile
        # non-ally" rule must not fire for him even when he's a fresh
        # stranger (stance far below companionship.threshold) and no
        # stance_ge tag exists at all (h is unreachable here, so best/alt
        # are both empty -- isolating the generic rule from the tag path).
        from gapengine.route import _is_companion_candidate

        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        oni_brother = subjects["鬼の弟"]
        self.assertLessEqual(oni_brother.ally_value, 0.0)
        self.assertFalse(_is_companion_candidate(momotaro, oni_brother, world))
        # A real companion (positive ally_value) is unaffected.
        inu = subjects["犬"]
        self.assertGreater(inu.ally_value, 0.0)
        self.assertTrue(_is_companion_candidate(momotaro, inu, world))


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


def _hash_seed_script(seed: int, *, rho: float = 0.0) -> str:
    return (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "import tempfile\n"
        "import yaml\n"
        "from engine.sim import Simulation\n"
        "from engine.world import World\n"
        "from gapengine.evolve import _load_subjects\n"
        "from gapengine.genome import Genome\n"
        "from gapengine.policy import Policy\n"
        "from gapengine.route import Route, load_route_config\n"
        f"project = Path({str(PROJECT)!r})\n"
        f"template = Path({str(TEMPLATE)!r})\n"
        'cfg = yaml.safe_load((template / "action_graph.yaml").read_text(encoding="utf-8"))\n'
        "route = Route.from_config(load_route_config(template))\n"
        f"route.rho = {rho!r}\n"
        'world = World.from_yaml(project / "world.yaml", action_graph_path=template / "action_graph.yaml")\n'
        'subjects = _load_subjects(project / "subjects")\n'
        "policy = Policy(Genome.neutral(), precedent=None, cfg=cfg, route=route)\n"
        "with tempfile.TemporaryDirectory() as tmp:\n"
        f"    Simulation({seed}, world, subjects, Path(tmp), policies={{world.protagonist: policy}}).run()\n"
        '    sys.stdout.write((Path(tmp) / "layers.jsonl").read_text(encoding="utf-8"))\n'
    )


class DeterminismAcrossHashSeedsTests(unittest.TestCase):
    """2026-09-24 review 3, required fix 1: a plain ``for kind, value in
    tags: return value`` scan over a ``frozenset`` of ``(str, str)`` tuples
    is not guaranteed to visit elements in the same order across Python
    processes -- CPython randomizes str hashing per-process unless
    ``PYTHONHASHSEED`` is fixed, and frozenset iteration order follows hash
    bucket order. ``_route_name`` used to do exactly that scan; two
    processes with different ``PYTHONHASHSEED`` could (and, on
    neutral/seed 1 around t50-54, did) pick a different ``("route", ...)``
    tag when a nested sub-acquisition's own fight tag coexisted with the
    top-level winner's tag in the same ``best`` set. Fixed by having
    ``_acquire_from_subject`` return the winning route name *explicitly*
    (from its own fixed-order options list), never scanned back out of a
    set -- this test runs the same short simulation in two subprocesses
    with different hash seeds and requires byte-identical output."""

    def test_layers_jsonl_is_byte_identical_across_two_hash_seeds(self) -> None:
        script = _hash_seed_script(1)
        outputs = []
        for hash_seed in ("1", "2"):
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = hash_seed
            # Force the child's stdout encoding to UTF-8 regardless of the
            # console codepage (Windows defaults to the system codepage,
            # e.g. cp932, which can't round-trip the Japanese subject/item
            # names in layers.jsonl and corrupts the comparison).
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])

    def test_layers_jsonl_is_byte_identical_across_two_hash_seeds_at_rho_one(self) -> None:
        # WB-ROUTE-001 S1 §5: the same cross-process determinism guarantee
        # must hold once m_route is actually modulating weights (rho=1.0),
        # not just at the S0-era rho=0 default above.
        script = _hash_seed_script(1, rho=1.0)
        outputs = []
        for hash_seed in ("1", "2"):
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = hash_seed
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
