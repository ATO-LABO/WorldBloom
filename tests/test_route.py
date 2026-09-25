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
    gene_strength,
    load_motives,
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
        self.assertEqual(cfg["holder_belief_fact"], "treasure_thief")
        self.assertEqual(cfg["min_win_prob"], 0.2)

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

    def test_befriending_oni_no_otouto_after_learning_of_him_is_advance(self) -> None:
        # Once 弟の消息 is known, 鬼の弟 is a legitimate *alternative*
        # waypoint toward a negotiate offer (brother_letter_trial grants
        # 弟の手紙) -- but momotaro already starts holding 勾玉, a free
        # negotiate offer, so the *acquisition's own* winning plan never
        # needs 弟の手紙, only review 3's _first_strength_item independently
        # picking it up (fix 3) as a strength boost (never via the generic
        # companion rule, since 鬼の弟's ally_value is 0 -- review 3 required
        # fix 2). Originally "prepare" (an alt-branch tag) here.
        #
        # S1 review 2 design judgment E: momotaro is outmatched by 鬼 by
        # default (base 50 vs believed 80) -- the danger-zone gate promotes
        # the *whole* boost-item acquisition chain (including this
        # stance_ge sub-step) into best, so persuading 鬼の弟 is now
        # "advance" via the same pre-existing stance_ge rule, not a new one.
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
        self.assertEqual(result["kind"], "advance")
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
        # 鉄砲 held (S1 review 2 design judgment E): momotaro is outmatched
        # by 鬼 by default (base 50 vs believed 80) -- without it, the
        # danger-zone gate takes over this exact scenario (道中 also
        # happens to be where its own boost material is gathered), which
        # is its own, separately covered behavior (DesignJudgmentETests).
        def setup(m, w):
            m.zone = "海"
            m.inventory["船"] = 1
            m.inventory["鉄砲"] = 1

        _m, _w, out = self._annotate(
            setup,
            [Action("move", ("道中",), {"dest": "道中"}), Action("move", ("鬼ヶ島",), {"dest": "鬼ヶ島"})],
        )
        self.assertNotEqual(out[0]["kind"], "advance")
        self.assertEqual(out[1]["kind"], "advance")

    def test_r1_h_does_not_increase_when_moving_toward_the_holder(self) -> None:
        # 鉄砲 held for the same reason as the sibling test just above --
        # otherwise design judgment E's own boost_h (tied to *current*
        # position) breaks this scenario's monotonicity for an unrelated
        # reason (see DesignJudgmentETests for that behavior instead).
        def setup(m, w):
            m.zone = "海"
            m.inventory["船"] = 1
            m.inventory["鉄砲"] = 1

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
        # 鉄砲 held (S1 review 2 design judgment E): without it, 小判 (also
        # gatherable right here at 道中, toward the danger-zone gate's own
        # boost item) becomes a second, currently-actionable leaf, and the
        # existing S1-review-1 "don't leave what's actionable here" rule
        # (unrelated to this test) demotes the move to 森 to "prepare".
        def setup_zero(m, w):
            m.zone = "道中"
            m.inventory["縄"] = 1
            m.inventory["鉄砲"] = 1

        def setup_one(m, w):
            m.zone = "道中"
            m.inventory["縄"] = 1
            m.inventory["木材"] = 1
            m.inventory["鉄砲"] = 1

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
        # test_fix3_first_strength_item_is_advance_even_with_a_free_offer --
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

    def test_fix3_first_strength_item_is_advance_even_with_a_free_offer(self) -> None:
        # Required fix 3 (2026-09-24 review 3): a *first* copy of a
        # not-yet-held positive-strength-modifier item (鉄砲) matters
        # regardless of design decision C -- it raises engine.contest.
        # strength() in any fight, independent of the free 勾玉 offer.
        # Originally "prepare" here.
        #
        # S1 review 2 design judgment E: momotaro is outmatched by 鬼 by
        # default (base 50 vs believed 80) -- the danger-zone gate promotes
        # this acquisition (小判, toward 鉄砲) into best, so gathering it
        # right where it's sourced (道中) is now "advance" via the ordinary
        # investigate rule, not a new one.
        def setup(m, w):
            m.zone = "道中"
            m.inventory["縄"] = 1  # see test_gathering_missing_ship_material_is_advance

        _m, _w, out = self._annotate(
            setup,
            [Action("investigate", ("道中",), {"target": "道中", "gather": True})],
        )
        self.assertEqual(out[0]["kind"], "advance")
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


class Review1ReproductionTests(unittest.TestCase):
    """WB-ROUTE-001 S1 Opus review 1 (145d318, 1st pass): R1-R3 reproduction
    cases from scratchpad/review/opus_s1_repro.py, pinned as regression
    tests. Each is the concrete scenario a required fix was written for."""

    TRF = {"鬼の弟": "弟の消息"}

    def _annotate(self, *, zone, inventory, actions):
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = zone
        momotaro.inventory.clear()
        momotaro.inventory.update(inventory)
        present = world.present_subjects(zone)
        return annotate(
            momotaro, world, present, actions,
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )

    def test_r1_delivery_toward_deliver_to_is_advance_elsewhere_is_not(self) -> None:
        # Required fix 3: holding the goal item (道中, 船 also held so the
        # trip is otherwise unblocked), the only real leaf left is the
        # delivery zone (村) -- moving there is advance, moving anywhere
        # else is not.
        out = self._annotate(
            zone="道中",
            inventory={"鬼ヶ島の宝物": 1, "船": 1},
            actions=[
                Action("move", ("村",), {"dest": "村"}),
                Action("move", ("海",), {"dest": "海"}),
                Action("move", ("森",), {"dest": "森"}),
            ],
        )
        self.assertEqual(out[0]["kind"], "advance")
        self.assertNotEqual(out[1]["kind"], "advance")
        self.assertNotEqual(out[2]["kind"], "advance")

    def test_r2_nearest_leaf_hop_respects_requires_item_gating(self) -> None:
        # Required fix 2: at 海 without 船, the nearest *reachable* leaf
        # (木材@森, gathering for 船) is one hop via 道中 -- the old
        # ungated BFS instead measured the (unusable) direct hop to
        # 鬼ヶ島 as nearer and left this move "prepare".
        out = self._annotate(
            zone="海",
            inventory={"縄": 1, "きびだんご": 3, "勾玉": 1},
            actions=[Action("move", ("道中",), {"dest": "道中"})],
        )
        self.assertEqual(out[0]["kind"], "advance")

    def test_r3_negotiate_leaf_only_when_nothing_else_is_open(self) -> None:
        # Required fix 1: at 鬼ヶ島 still needing an offer (きびだんご alone
        # is short of 小判x3 for 鉄砲, the alternative build), "negotiate"
        # is not yet actionable here -- moving toward the nearest still-open
        # leaf (小判@道中) is advance.
        out = self._annotate(
            zone="鬼ヶ島",
            inventory={"きびだんご": 1, "船": 1},
            actions=[Action("move", ("海",), {"dest": "海"})],
        )
        self.assertEqual(out[0]["kind"], "advance")


class DesignJudgmentDTests(unittest.TestCase):
    """WB-ROUTE-001 S1 Opus review 1, design judgment D: a fight/sabotage/
    neutralize aimed at the true holder is forced to detour/none once the
    subject's own believed win probability drops below route.yaml's
    min_win_prob (default 0.2) -- regardless of whether fight happens to be
    the plan's best or alt branch."""

    TRF = {"鬼の弟": "弟の消息"}

    def test_hopeless_fight_against_the_holder_is_detour_none(self) -> None:
        # Default fixture: momotaro (base 50) vs 鬼 (believed base 80) at
        # 鬼ヶ島 with only 船 held -- p~=0.098, well under 0.2. Without the
        # fix this was "prepare" (fight sits on alt, negotiate is best).
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "鬼ヶ島"
        momotaro.inventory["船"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("fight", ("鬼",), {"target": "鬼"})
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "none")
        self.assertEqual(result["text"], "勝ち目の薄い無謀な挑戦")

    def test_winnable_fight_against_the_holder_is_unaffected(self) -> None:
        # Same holder, but 鉄砲 (a strength modifier) pushes p to ~=0.78,
        # above min_win_prob -- ordinary best/alt classification applies
        # (fight is still alt here, negotiate remains cheaper -- "prepare").
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "鬼ヶ島"
        momotaro.inventory["船"] = 1
        momotaro.inventory["鉄砲"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("fight", ("鬼",), {"target": "鬼"})
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )[0]
        self.assertEqual(result["kind"], "prepare")
        self.assertIsNone(result["cause"])

    def test_hopeless_fight_against_a_misattributed_target_is_still_belief(self) -> None:
        # A confidently misattributed target (belief, not the true holder)
        # keeps its existing detour/belief classification (never punished)
        # -- design D is scoped to the *true*, correctly-identified holder.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.beliefs["treasure_thief"] = Belief(value="猿", confidence=0.9)
        momotaro.zone = "道中"
        present = world.present_subjects(momotaro.zone)
        action = Action("fight", ("猿",), {"target": "猿"})
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "belief")


class DesignJudgmentETests(unittest.TestCase):
    """WB-ROUTE-001 S1 Opus review 2, design judgment E: entering a too-
    strong hostile holder's own zone is gated on a "strong_enough" leaf
    (believed win probability >= route.yaml's min_win_prob) before the
    winning acquisition plan gives credit for it -- getting strong (train,
    a strength-modifier item, recruiting help) is the actionable step
    while it's unmet, not the approach/entry itself. Found from eval2's
    sweep data: 主導カテゴリ V jumped to 141/240 at rho=1.0 because momotaro
    kept ferrying himself, still weak, straight into 鬼ヶ島 and getting
    knocked down by 鬼 (211 -> 1221 losses across the sweep)."""

    TRF = {"鬼の弟": "弟の消息"}

    def test_entering_the_holder_zone_while_weak_is_not_advance(self) -> None:
        # Default fixture: momotaro (base 50) vs 鬼 (believed base 80) --
        # p~=0.098, well under min_win_prob (0.2). Holding 船 (so 鬼ヶ島 is
        # directly reachable) used to make this move "advance" (the exact
        # bug the eval data surfaced); it must not be while genuinely
        # outmatched.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "海"
        momotaro.inventory["船"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("move", ("鬼ヶ島",), {"dest": "鬼ヶ島"})
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )[0]
        self.assertNotEqual(result["kind"], "advance")

    def test_entering_the_holder_zone_while_weak_is_detour_none(self) -> None:
        # Design judgment F (Opus review): not merely "not advance" (the
        # test above) -- entering while strong_enough is still open is
        # actively reckless, forced to detour/none (not "prepare"), with a
        # dedicated text naming the danger.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "海"
        momotaro.inventory["船"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("move", ("鬼ヶ島",), {"dest": "鬼ヶ島"})
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "none")
        self.assertIn("鬼", result["text"])

    def test_training_while_weak_is_advance(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "道中"
        momotaro.inventory["縄"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("train", (), {})
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )[0]
        self.assertEqual(result["kind"], "advance")
        self.assertIsNone(result["cause"])
        self.assertIn("鬼", result["text"])

    def test_withdrawing_from_the_holder_zone_while_weak_is_not_advance(self) -> None:
        # E-3 (Opus review): engine.verbs._withdraw only lowers stress (it
        # never relocates the subject -- see engine/verbs.py) -- crediting
        # it as the same "left the zone" escape/advance as an actual move
        # was wrong; without exhaustion/downed/under_threat it now falls to
        # the ordinary classification (detour/none, same as the idle-rest
        # sibling test below), and never gets the "left the zone" wording.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "鬼ヶ島"
        momotaro.inventory["船"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("withdraw", (), {})
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "none")
        self.assertNotIn("離れた", result["text"])

    def test_withdrawing_under_threat_is_detour_body(self) -> None:
        # E-3: withdraw still reads as the physically-forced pause it
        # actually is (_body_or_belief_cause's existing "body" rule) when
        # under_threat, exhausted, or downed -- untouched by the escape
        # override's removal.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "鬼ヶ島"
        momotaro.inventory["船"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("withdraw", (), {"under_threat": True})
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "body")

    def test_resting_idle_at_the_holder_zone_while_weak_is_detour_none(self) -> None:
        # The mirror of the withdraw case above: staying and doing nothing
        # (not forced by exhaustion/downed) is punished, not rewarded.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "鬼ヶ島"
        momotaro.inventory["船"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("rest", (), {"under_threat": False})
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "none")

    def test_winnable_entry_is_unaffected(self) -> None:
        # 鉄砲 pushes p above min_win_prob -- ordinary classification
        # applies, entering the holder's zone reads as advance again.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "海"
        momotaro.inventory["船"] = 1
        momotaro.inventory["鉄砲"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("move", ("鬼ヶ島",), {"dest": "鬼ヶ島"})
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
        )[0]
        self.assertEqual(result["kind"], "advance")

    def test_misattributed_target_is_not_gated(self) -> None:
        # 猿 is not the true holder -- design E ("誤認経路は対象外") must
        # never gate entry to *his* zone on strength, even though momotaro
        # confidently (mis)believes he holds the treasure.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.beliefs["treasure_thief"] = Belief(value="猿", confidence=0.9)
        momotaro.zone = "道中"
        present = world.present_subjects(momotaro.zone)
        h, best, _alt, _route = _acquire_from_subject(
            "鬼ヶ島の宝物", subjects["猿"], momotaro, world, frozenset(), self.TRF,
            is_objective=True,
        )
        self.assertNotIn(("strong_enough", "猿"), best)

    def test_failed_negotiate_offer_stance_ge_is_not_advance_either(self) -> None:
        # E-1 (Opus review): a negotiate offer that finds no free trade
        # leaves ("stance_ge", holder.id) in best as the lever to raise
        # instead of the offer itself -- the old entry_tags demotion list
        # didn't include it, so this leaf's own zone (holder.zone) kept the
        # danger zone reachable as an advance/prepare leaf even after the
        # zone/win_fight/route tags were correctly demoted. visiting is
        # pre-seeded with every offer/boost item's own acquire-key so
        # _best_offer/_first_strength_item can't find *any* item (forcing
        # the stance_ge branch deterministically, without depending on
        # which items happen to be affordable from wherever momotaro is
        # standing).
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "道中"
        momotaro.inventory["船"] = 1
        momotaro.inventory["勾玉"] = 0  # otherwise already-held 勾玉 (_acquire's
        # own inventory>=needed short-circuit, checked before `visiting`)
        # gives a free offer for h==0.0 regardless of the block below.
        blocked = frozenset(
            ("item", name) for name in ("勾玉", "弟の手紙", "鉄砲", "金棒", "小判")
        )
        h, best, alt, _route = _acquire_from_subject(
            "鬼ヶ島の宝物", subjects["鬼"], momotaro, world, blocked, self.TRF,
            is_objective=True,
        )
        self.assertNotIn(("stance_ge", "鬼"), best)
        self.assertIn(("stance_ge", "鬼"), alt)

    def test_strength_item_only_reachable_in_the_dangerous_zone_is_excluded(self) -> None:
        # E-2 (Opus review): _first_strength_item must not hand back a
        # strength item whose only acquisition route requires entering the
        # dangerous holder's own zone -- that's circular, presupposing the
        # very win the boost step exists to avoid.
        from gapengine.route import _first_strength_item

        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "森"
        momotaro.inventory["船"] = 1  # 鬼ヶ島 is only reachable by boat
        world.items["秘薬"] = {
            "lootable": True,
            "sources": [{"type": "investigate", "zone": "鬼ヶ島", "count": 1}],
            "modifier": {"id": "item:秘薬", "value": 99, "kind": "item", "visible": True},
        }
        _h, best, alt = _first_strength_item(momotaro, world, frozenset(), self.TRF)
        self.assertTrue(
            ("has_item", "秘薬") in best or ("has_item", "秘薬") in alt,
            "sanity: the synthetic item is a real candidate without the guard",
        )
        _h2, best2, alt2 = _first_strength_item(
            momotaro, world, frozenset(), self.TRF, avoid_zone="鬼ヶ島",
        )
        self.assertNotIn(("has_item", "秘薬"), best2)
        self.assertNotIn(("has_item", "秘薬"), alt2)

    def test_strength_item_only_reachable_by_fighting_the_holder_is_excluded(self) -> None:
        # E-2: same guard, the "take it from whoever holds it" (fight)
        # route instead of a zone source -- a strength item only obtainable
        # by fighting the dangerous holder directly must also be excluded.
        from gapengine.route import _first_strength_item

        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "道中"
        momotaro.inventory["船"] = 1  # 鬼ヶ島 is only reachable by boat
        oni = subjects["鬼"]
        world.items["秘宝"] = {
            "lootable": True,
            "modifier": {"id": "item:秘宝", "value": 60, "kind": "item", "visible": True},
        }
        oni.inventory["秘宝"] = 1
        _h, best, alt = _first_strength_item(momotaro, world, frozenset(), self.TRF)
        self.assertTrue(
            ("has_item", "秘宝") in best or ("has_item", "秘宝") in alt,
            "sanity: the synthetic item is a real candidate without the guard",
        )
        _h2, best2, alt2 = _first_strength_item(
            momotaro, world, frozenset(), self.TRF, avoid_fight_holder="鬼",
        )
        self.assertNotIn(("has_item", "秘宝"), best2)
        self.assertNotIn(("has_item", "秘宝"), alt2)


class DesignJudgmentHTests(unittest.TestCase):
    """WB-ROUTE-001 S2 design judgment H (Opus review): rest/withdraw whose
    real driver is fatigue/stress -- not a tactical read of the enemy --
    must resolve to detour/body with its own text, not fall through to
    detour/none where the caution motive (believed_weaker) would
    misattribute it. Same weak-momotaro-at-鬼ヶ島 fixture as
    DesignJudgmentETests (believed_weaker(self) is true there)."""

    TRF = {"鬼の弟": "弟の消息"}

    def _weak_momotaro(self):
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "鬼ヶ島"
        momotaro.inventory["船"] = 1
        return world, momotaro

    def test_tired_rest_is_detour_body_not_caution(self) -> None:
        world, momotaro = self._weak_momotaro()
        momotaro.stamina = 7.0  # ratio 0.5 <= REST_FATIGUE_STAMINA_RATIO (0.6)
        momotaro.stress = 0.0
        present = world.present_subjects(momotaro.zone)
        action = Action("rest", (), {"under_threat": False})
        motives = load_motives(TEMPLATE)
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
            motives=motives,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "body")
        self.assertEqual(result["text"], "疲れが溜まっていたので休んだ")

    def test_stressed_withdraw_is_detour_body_not_caution(self) -> None:
        world, momotaro = self._weak_momotaro()
        momotaro.stamina = momotaro.stamina_max
        momotaro.stress = 5.0  # > WITHDRAW_STRESS_THRESHOLD (4.0)
        present = world.present_subjects(momotaro.zone)
        action = Action("withdraw", (), {"under_threat": False})
        motives = load_motives(TEMPLATE)
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
            motives=motives,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "body")
        self.assertEqual(result["text"], "心労がかさみ、ひとまず気を落ち着けた")

    def test_rested_calm_rest_still_gets_caution(self) -> None:
        # Below both new thresholds (full stamina, no stress) -- unchanged
        # from before judgment H: falls to detour/none, then the caution
        # motive (believed_weaker) claims it.
        world, momotaro = self._weak_momotaro()
        momotaro.stamina = momotaro.stamina_max
        momotaro.stress = 0.0
        present = world.present_subjects(momotaro.zone)
        action = Action("rest", (), {"under_threat": False})
        motives = load_motives(TEMPLATE)
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
            motives=motives,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "motive")
        self.assertEqual(result["motive"], "caution")

    def test_rested_calm_withdraw_still_gets_caution(self) -> None:
        world, momotaro = self._weak_momotaro()
        momotaro.stamina = momotaro.stamina_max
        momotaro.stress = 0.0
        present = world.present_subjects(momotaro.zone)
        action = Action("withdraw", (), {"under_threat": False})
        motives = load_motives(TEMPLATE)
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
            motives=motives,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "motive")
        self.assertEqual(result["motive"], "caution")


class S1Review1SiblingTests(unittest.TestCase):
    """WB-ROUTE-001 S1 Opus review 1 recommended: the same three S0-era
    scenarios AnnotateTests already covers, re-run in a *reachable* state
    (holding 縄, unlike the plain 道中 fixture zone which is h=inf/"lost"
    -- see AnnotateTests's own scenarios) so m_route's actual multiplier
    (not "lost"'s unconditional 1.0) is what's being exercised."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.momotaro.zone = "道中"
        self.momotaro.inventory["縄"] = 1

    def _present(self):
        return self.world.present_subjects(self.momotaro.zone)

    def test_neutral_kiji_fight_is_detour_none_when_reachable(self) -> None:
        action = Action("fight", ("キジ",), {"target": "キジ"})
        result = annotate(
            self.momotaro, self.world, self._present(), [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "none")

    def test_companion_small_talk_is_advance_when_reachable(self) -> None:
        # Originally "prepare" here (S0-era companion-candidate rule). S1
        # review 2 design judgment E: momotaro is outmatched by 鬼 by
        # default (base 50 vs believed 80) -- recruiting help reads as
        # "advance" while "strong_enough" is the open leaf, not merely
        # prepare (see DesignJudgmentETests for the dedicated coverage;
        # this only re-confirms the S0-era scenario still resolves
        # sensibly once reachable).
        action = Action("share_knowledge", ("猿", "雑談"), {"target": "猿", "topic": "雑談"})
        result = annotate(
            self.momotaro, self.world, self._present(), [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "advance")
        self.assertIsNone(result["cause"])

    def test_off_plan_trial_material_is_not_protected_when_reachable(self) -> None:
        # 犬's trial (dog_loyalty_trial) wants きびだんご, but momotaro's
        # winning plan (negotiate, offering 勾玉) never needs it -- giving
        # away the last unit must not be credited just because some other
        # trial happens to want it.
        self.momotaro.inventory["きびだんご"] = 1
        action = Action(
            "give_item",
            ("犬", "きびだんご"),
            {"target": "犬", "item": "きびだんご"},
        )
        result = annotate(
            self.momotaro, self.world, self._present(), [action],
            holder_belief_fact="treasure_thief",
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "none")


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


class MotiveLoadTests(unittest.TestCase):
    """WB-ROUTE-001 S2 §1/§7: motives.yaml validation."""

    def test_momotaro_plus2_motives_load_in_order(self) -> None:
        motives = load_motives(TEMPLATE)
        self.assertIsNotNone(motives)
        assert motives is not None
        self.assertEqual(
            [m["id"] for m in motives],
            ["care_for_ally", "bravado", "grudge", "curiosity", "caution"],
        )

    def test_template_without_motives_yaml_is_none(self) -> None:
        self.assertIsNone(load_motives(ROOT / "templates" / "momotaro"))

    def test_unknown_gene_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "motives.yaml").write_text(
                "- id: bad\n"
                "  when: 'True'\n"
                "  verbs: [investigate]\n"
                "  gene: not_a_real_gene\n"
                "  text: x\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_motives(path)

    def test_duplicate_id_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "motives.yaml").write_text(
                "- id: dup\n  when: 'True'\n  verbs: [investigate]\n"
                "  gene: novelty_drive\n  text: x\n"
                "- id: dup\n  when: 'True'\n  verbs: [observe]\n"
                "  gene: novelty_drive\n  text: y\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_motives(path)


class GeneStrengthTests(unittest.TestCase):
    """WB-ROUTE-001 S2 §2: s (0..1), neutral genome -> 0.5 (novelty_drive
    -> 0, its own documented exception), a leading '-' inverts."""

    def test_neutral_genome(self) -> None:
        neutral = Genome.neutral()
        self.assertAlmostEqual(gene_strength(neutral, "risk_tolerance"), 0.5)
        self.assertAlmostEqual(gene_strength(neutral, "stance_shift_bias"), 0.5)
        self.assertAlmostEqual(gene_strength(neutral, "novelty_drive"), 0.0)
        self.assertAlmostEqual(
            gene_strength(neutral, "category_weight.III", category_mean=0.5), 0.5
        )

    def test_negation_inverts(self) -> None:
        neutral = Genome.neutral()
        reckless = Genome(
            category_weight=dict(neutral.category_weight),
            risk_tolerance=1.0,
            stance_shift_bias=neutral.stance_shift_bias,
            novelty_drive=neutral.novelty_drive,
        )
        self.assertAlmostEqual(gene_strength(reckless, "risk_tolerance"), 1.0)
        self.assertAlmostEqual(gene_strength(reckless, "-risk_tolerance"), 0.0)

    def test_unknown_gene_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            gene_strength(Genome.neutral(), "not_a_gene")


class MotiveMultiplierTests(unittest.TestCase):
    """WB-ROUTE-001 S2 §3: b = delta + (1-delta)*gene_s for cause=="motive",
    delta reused from multipliers["detour:none"] (no separate constant)."""

    def test_motive_multiplier_spans_delta_to_one(self) -> None:
        route = Route(rho=1.0, multipliers={"detour:none": 0.02})
        self.assertAlmostEqual(route.multiplier("detour", "motive", gene_s=0.0), 0.02)
        self.assertAlmostEqual(route.multiplier("detour", "motive", gene_s=1.0), 1.0)
        self.assertAlmostEqual(
            route.multiplier("detour", "motive", gene_s=0.5), 0.02 + 0.98 * 0.5
        )

    def test_rho_zero_disables_motive_multiplier_too(self) -> None:
        route = Route(rho=0.0)
        self.assertEqual(route.multiplier("detour", "motive", gene_s=1.0), 1.0)


class MotiveMatchingTests(unittest.TestCase):
    """WB-ROUTE-001 S2 §1: motives only ever relabel a candidate that was
    already kind=detour/cause=none; every other kind/cause is untouched."""

    TRF = {"鬼の弟": "弟の消息"}

    @staticmethod
    def _genome(*, risk_tolerance: float = 0.5) -> Genome:
        neutral = Genome.neutral()
        return Genome(
            category_weight=dict(neutral.category_weight),
            risk_tolerance=risk_tolerance,
            stance_shift_bias=neutral.stance_shift_bias,
            novelty_drive=neutral.novelty_drive,
        )

    def test_bravado_overrides_the_hopeless_holder_fight(self) -> None:
        # Same fixture as DesignJudgmentDTests.
        # test_hopeless_fight_against_the_holder_is_detour_none.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "鬼ヶ島"
        momotaro.inventory["船"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("fight", ("鬼",), {"target": "鬼"})
        motives = load_motives(TEMPLATE)
        reckless = self._genome(risk_tolerance=1.0)
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
            targets=["鬼"], genome=reckless, candidate_genomes=[reckless],
            motives=motives,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "motive")
        self.assertEqual(result["motive"], "bravado")
        self.assertIn("鬼", result["text"])
        self.assertAlmostEqual(result["gene_s"], 1.0)

    def test_bravado_fires_regardless_of_risk_tolerance_gene_only_weights_it(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "鬼ヶ島"
        momotaro.inventory["船"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("fight", ("鬼",), {"target": "鬼"})
        motives = load_motives(TEMPLATE)
        cautious = self._genome(risk_tolerance=0.0)
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
            targets=["鬼"], genome=cautious, candidate_genomes=[cautious],
            motives=motives,
        )[0]
        self.assertEqual(result["motive"], "bravado")
        self.assertAlmostEqual(result["gene_s"], 0.0)

    def test_unrelated_hostile_fight_never_gets_bravado(self) -> None:
        # target != the true goal holder -- _match_advance_or_prepare's
        # fight branch already returns None for this, landing in the
        # generic detour/none fallback; bravado's own
        # is_target_holder/holder_hopeless bindings must keep it out.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "道中"
        momotaro.beliefs["treasure_thief"] = Belief(value="鬼", confidence=0.9)
        present = world.present_subjects(momotaro.zone)
        action = Action("fight", ("キジ",), {"target": "キジ"})
        motives = load_motives(TEMPLATE)
        reckless = self._genome(risk_tolerance=1.0)
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief",
            targets=["キジ"], genome=reckless, candidate_genomes=[reckless],
            motives=motives,
        )[0]
        self.assertNotEqual(result.get("motive"), "bravado")

    def test_bravado_overrides_the_hopeless_danger_zone_entry_move(self) -> None:
        # S2 design judgment G: the move design judgment F forces to
        # detour/none (entering the too-strong true holder's own zone while
        # still hopeless -- same fixture as
        # DesignJudgmentETests.test_entering_the_holder_zone_while_weak_is_
        # detour_none) is now bravado-eligible for a reckless genome, with
        # {target} naming the holder (鬼), not the zone.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "海"
        momotaro.inventory["船"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("move", ("鬼ヶ島",), {"dest": "鬼ヶ島"})
        motives = load_motives(TEMPLATE)
        reckless = self._genome(risk_tolerance=1.0)
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
            genome=reckless, candidate_genomes=[reckless], motives=motives,
        )[0]
        self.assertEqual(result["kind"], "detour")
        self.assertEqual(result["cause"], "motive")
        self.assertEqual(result["motive"], "bravado")
        self.assertIn("鬼", result["text"])
        self.assertAlmostEqual(result["gene_s"], 1.0)

    def test_cautious_genome_still_gets_bravado_but_with_a_small_multiplier(self) -> None:
        # Design judgment G note in the S2 plan: risk_tolerance only weights
        # the motive's multiplier (via gene_s -> b), it never gates whether
        # the candidate is relabelled motive:bravado in the first place --
        # same "fires regardless" behavior as the fight case above.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "海"
        momotaro.inventory["船"] = 1
        present = world.present_subjects(momotaro.zone)
        action = Action("move", ("鬼ヶ島",), {"dest": "鬼ヶ島"})
        motives = load_motives(TEMPLATE)
        cautious = self._genome(risk_tolerance=0.0)
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
            genome=cautious, candidate_genomes=[cautious], motives=motives,
        )[0]
        self.assertEqual(result["motive"], "bravado")
        self.assertAlmostEqual(result["gene_s"], 0.0)
        route = Route(rho=1.0, multipliers={"detour:none": 0.02})
        self.assertAlmostEqual(
            route.multiplier("detour", "motive", gene_s=result["gene_s"]), 0.02
        )

    def test_care_for_ally_overrides_high_stance_give_item(self) -> None:
        # Same fixture as RouteMultiplierTests.
        # test_neutral_genome_is_still_modulated_when_rho_positive, but with
        # stance(桃太郎, 鬼の弟) raised above care_for_ally's 0.6 threshold.
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "森"
        momotaro.inventory["縄"] = 1
        world.relations.change("桃太郎", "鬼の弟", affinity=0.8)
        present = world.present_subjects(momotaro.zone)
        action = Action(
            "give_item", ("鬼の弟", "木材"),
            {"target": "鬼の弟", "item": "木材", "stance_sign": 1},
        )
        motives = load_motives(TEMPLATE)
        genome = self._genome()
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief",
            targets=["鬼の弟"], genome=genome, candidate_genomes=[genome],
            category_mean=0.5, motives=motives,
        )[0]
        self.assertEqual(result["cause"], "motive")
        self.assertEqual(result["motive"], "care_for_ally")
        self.assertEqual(result["motive_label"], "仲間を大事にする")
        self.assertIn("鬼の弟", result["text"])
        # S4 §0(c): the ending now matches the verb actually taken
        # (give_item -> "品を渡した"), not a fixed "絆を深めたかった" that
        # read oddly for a give_item candidate.
        self.assertEqual(result["text"], "鬼の弟との絆を深めたくて品を渡した")

    def test_curiosity_falls_back_to_zone_text_when_no_real_target(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "森"
        momotaro.inventory["縄"] = 1
        present = world.present_subjects(momotaro.zone)
        # A bare investigate with no gather flag and nothing sourced here:
        # falls through to the generic detour/none fallback (no ignorance,
        # since the target isn't the believed holder).
        action = Action("investigate", ("森",), {"target": "森"})
        motives = load_motives(TEMPLATE)
        genome = self._genome()
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief",
            targets=[momotaro.id], genome=genome, candidate_genomes=[genome],
            motives=motives,
        )[0]
        if result["cause"] == "motive":
            self.assertEqual(result["motive"], "curiosity")
            self.assertIn("森", result["text"])
            self.assertNotIn("{target}", result["text"])

    def test_lost_kind_is_never_touched_by_motives(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "道中"
        present = world.present_subjects(momotaro.zone)
        action = Action("investigate", ("道中",), {"target": "道中", "gather": True})
        motives = load_motives(TEMPLATE)
        genome = self._genome()
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief",
            targets=[momotaro.id], genome=genome, candidate_genomes=[genome],
            motives=motives,
        )[0]
        self.assertEqual(result["kind"], "lost")

    def test_advance_prepare_ignorance_belief_are_never_touched(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        momotaro.zone = "鬼ヶ島"
        momotaro.inventory["船"] = 1
        momotaro.inventory["鉄砲"] = 1  # winnable now -- see DesignJudgmentDTests
        present = world.present_subjects(momotaro.zone)
        action = Action("fight", ("鬼",), {"target": "鬼"})
        motives = load_motives(TEMPLATE)
        genome = self._genome()
        result = annotate(
            momotaro, world, present, [action],
            holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF,
            targets=["鬼"], genome=genome, candidate_genomes=[genome],
            motives=motives,
        )[0]
        self.assertEqual(result["kind"], "prepare")
        self.assertIsNone(result["cause"])


class GeneAffinityRouteSelectionTests(unittest.TestCase):
    """WB-ROUTE-001 S2 §4: gene_affinity biases plan()'s fight-vs-negotiate
    choice by category gene strength; the recorded h stays the winning
    option's own plain h (never h_eff), and gene_affinity=0 is S1-identical."""

    ROUTE_CATEGORY = {"fight": "I", "negotiate": "III"}

    @staticmethod
    def _genome(**category_weight: float) -> Genome:
        base = {category: 0.5 for category in Genome.neutral().category_weight}
        base.update(category_weight)
        return Genome(
            category_weight=base, risk_tolerance=0.5, stance_shift_bias=0.0, novelty_drive=0.0
        )

    def test_gene_affinity_zero_matches_s1(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        baseline = plan(momotaro, world, holder_belief_fact="treasure_thief")
        genome = self._genome(I=1.0, III=0.05)
        with_zero = plan(
            momotaro, world, holder_belief_fact="treasure_thief",
            genome=genome, category_mean=0.5, gene_affinity=0.0,
            route_category=self.ROUTE_CATEGORY,
        )
        self.assertEqual(baseline["route"], with_zero["route"])
        self.assertEqual(baseline["h"], with_zero["h"])
        self.assertEqual(baseline["best"], with_zero["best"])
        self.assertEqual(baseline["alt"], with_zero["alt"])

    def test_type_i_prefers_fight_type_iii_prefers_negotiate(self) -> None:
        world, subjects = load_fixture()
        momotaro = subjects["桃太郎"]
        type_i = self._genome(I=1.0, III=0.05)
        type_iii = self._genome(I=0.05, III=1.0)
        mean_i = sum(type_i.category_weight.values()) / len(type_i.category_weight)
        mean_iii = sum(type_iii.category_weight.values()) / len(type_iii.category_weight)
        plan_i = plan(
            momotaro, world, holder_belief_fact="treasure_thief",
            genome=type_i, category_mean=mean_i, gene_affinity=0.5,
            route_category=self.ROUTE_CATEGORY,
        )
        plan_iii = plan(
            momotaro, world, holder_belief_fact="treasure_thief",
            genome=type_iii, category_mean=mean_iii, gene_affinity=0.5,
            route_category=self.ROUTE_CATEGORY,
        )
        self.assertEqual(plan_i["route"], "fight")
        self.assertEqual(plan_iii["route"], "negotiate")
        # h stays the *winning* option's own plain h, not an h_eff -- never
        # negative/inflated, and matches a direct _acquire_from_subject
        # recomputation at gene_affinity=0 would have given that option.
        self.assertGreater(plan_i["h"], 0.0)


def _s2_hash_seed_script(seed: int) -> str:
    """Same shape as _hash_seed_script, but with a non-neutral, gene-
    affinity-sensitive genome (momotaro_plus2's real route.yaml already
    carries gene_affinity=0.5) -- exercises the h_eff winner-selection
    branch under PYTHONHASHSEED variation, not just the rho=1.0 static-
    multiplier path _hash_seed_script(seed, rho=1.0) already covers."""

    return (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "import tempfile\n"
        "import yaml\n"
        "from engine.sim import Simulation\n"
        "from engine.world import World\n"
        "from gapengine.evolve import _load_subjects\n"
        "from gapengine.genome import CATEGORIES, Genome\n"
        "from gapengine.policy import Policy\n"
        "from gapengine.route import Route, load_route_config\n"
        f"project = Path({str(PROJECT)!r})\n"
        f"template = Path({str(TEMPLATE)!r})\n"
        'cfg = yaml.safe_load((template / "action_graph.yaml").read_text(encoding="utf-8"))\n'
        "route = Route.from_config(load_route_config(template))\n"
        "route.rho = 1.0\n"
        "cw = {c: 0.5 for c in CATEGORIES}\n"
        "cw['I'] = 1.0\n"
        "cw['III'] = 0.05\n"
        "genome = Genome(category_weight=cw, risk_tolerance=1.0, stance_shift_bias=-0.5, novelty_drive=0.7)\n"
        'world = World.from_yaml(project / "world.yaml", action_graph_path=template / "action_graph.yaml")\n'
        'subjects = _load_subjects(project / "subjects")\n'
        "policy = Policy(genome, precedent=None, cfg=cfg, route=route)\n"
        "with tempfile.TemporaryDirectory() as tmp:\n"
        f"    Simulation({seed}, world, subjects, Path(tmp), policies={{world.protagonist: policy}}).run()\n"
        '    sys.stdout.write((Path(tmp) / "layers.jsonl").read_text(encoding="utf-8"))\n'
    )


class S2DeterminismTests(unittest.TestCase):
    """WB-ROUTE-001 S2 §7: motives + gene_affinity must not introduce any
    PYTHONHASHSEED-dependent behavior (motives.yaml/route_category are
    iterated as ordered lists/dicts, never a frozenset, so this is mostly a
    guard against a future regression, not an expected failure mode)."""

    def test_layers_jsonl_is_byte_identical_across_two_hash_seeds(self) -> None:
        script = _s2_hash_seed_script(1)
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


class AdvanceTextTests(unittest.TestCase):
    """WB-ROUTE-001 S3.5 §1/§2: each milestone kind gets its own move text
    (the S3 fallback "移動して近づいた" is now a last resort, not the common
    case), and the relation-verb texts read as the subject's want, not an
    already-achieved result."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]

    def test_craft_zone_milestone(self) -> None:
        from gapengine.route import _advance_text

        text = _advance_text(
            Action("move", ("海",), {"dest": "海"}),
            self.momotaro,
            self.world,
            {"has_item": {"船"}},
            milestone="has_item:船",
        )
        self.assertEqual(text, "船を作るため海へ向かった")

    def test_gather_milestone(self) -> None:
        from gapengine.route import _advance_text

        text = _advance_text(
            Action("move", ("森",), {"dest": "森"}),
            self.momotaro,
            self.world,
            {"has_item": {"木材"}},
            milestone="has_item:木材",
        )
        self.assertEqual(text, "木材を手に入れるため森へ向かった")

    def test_stance_ge_milestone_reads_as_meeting_not_fighting(self) -> None:
        from gapengine.route import _advance_text

        self.subjects["猿"].zone = "道中"
        text = _advance_text(
            Action("move", ("道中",), {"dest": "道中"}),
            self.momotaro,
            self.world,
            {"stance_ge": {"猿"}},
            milestone="stance_ge:猿",
        )
        self.assertEqual(text, "猿と会うため道中へ向かった")

    def test_win_fight_milestone(self) -> None:
        from gapengine.route import _advance_text

        self.subjects["鬼"].zone = "鬼ヶ島"
        text = _advance_text(
            Action("move", ("鬼ヶ島",), {"dest": "鬼ヶ島"}),
            self.momotaro,
            self.world,
            {"win_fight": {"鬼"}},
            milestone="win_fight:鬼",
        )
        self.assertEqual(text, "鬼を倒すため鬼ヶ島へ向かった")

    def test_route_negotiate_milestone(self) -> None:
        from gapengine.route import _advance_text

        self.subjects["鬼"].zone = "鬼ヶ島"
        text = _advance_text(
            Action("move", ("鬼ヶ島",), {"dest": "鬼ヶ島"}),
            self.momotaro,
            self.world,
            {"route": {"negotiate"}},
            believed_holder_id="鬼",
            milestone="route:negotiate",
        )
        self.assertEqual(text, "鬼と話をつけるため鬼ヶ島へ向かった")

    def test_goal_deliver_milestone(self) -> None:
        from gapengine.route import _advance_text

        self.momotaro.inventory["鬼ヶ島の宝物"] = 1
        text = _advance_text(
            Action("move", ("村",), {"dest": "村"}),
            self.momotaro,
            self.world,
            {},
            milestone="goal:deliver",
        )
        self.assertEqual(text, "宝を持ち帰るため村へ向かった")

    def test_companion_milestone_when_no_other_leaf_matches(self) -> None:
        from gapengine.route import _advance_text

        self.subjects["犬"].zone = "道中"
        text = _advance_text(
            Action("move", ("道中",), {"dest": "道中"}),
            self.momotaro,
            self.world,
            {},
        )
        self.assertEqual(text, "仲間と合流するため道中へ向かった")

    def test_milestone_text_survives_a_waypoint_hop_short_of_the_leaf(self) -> None:
        """S3.5 §4 follow-up: a hop toward a leaf 2+ zones away (holder not
        standing at this particular waypoint) still names the real purpose
        -- "ため〜へ向かった" states intent/direction, not that the target is
        physically at ``dest`` right now."""

        from gapengine.route import _advance_text

        self.subjects["鬼"].zone = "鬼ヶ島"  # not "海" -- still 1 hop further
        text = _advance_text(
            Action("move", ("海",), {"dest": "海"}),
            self.momotaro,
            self.world,
            {"win_fight": {"鬼"}},
            milestone="win_fight:鬼",
        )
        self.assertEqual(text, "鬼を倒すため海へ向かった")

    def test_generic_fallback_is_last_resort(self) -> None:
        from gapengine.route import _advance_text

        text = _advance_text(
            Action("move", ("鬼ヶ島",), {"dest": "鬼ヶ島"}),
            self.momotaro,
            self.world,
            {},
        )
        self.assertEqual(text, "鬼ヶ島へ移動して近づいた")

    def test_fight_names_the_goal_item_and_the_holder(self) -> None:
        """S4 §0(a): "目的物の持ち主と戦った" alone named neither the goal
        item nor who the holder actually is."""
        from gapengine.route import _advance_text

        text = _advance_text(
            Action("fight", ("鬼",), {"target": "鬼"}),
            self.momotaro,
            self.world,
            {},
            believed_holder_id="鬼",
        )
        self.assertEqual(text, "鬼ヶ島の宝物を手に入れるため鬼と戦った")

    def test_craft_names_the_vehicle_destination(self) -> None:
        """S4 §0(b): a craft's reason text named nothing beyond the event
        itself ("船を作った") -- 船 is a vehicle, so name where it takes the
        subject (the believed goal holder's zone)."""
        from gapengine.route import _advance_text

        self.subjects["鬼"].zone = "鬼ヶ島"
        text = _advance_text(
            Action("craft", ("船",)),
            self.momotaro,
            self.world,
            {},
            believed_holder_id="鬼",
        )
        self.assertEqual(text, "鬼ヶ島へ渡るため船を作った")

    def test_craft_names_the_fight_advantage(self) -> None:
        """S4 §0(b): 鉄砲 raises fight strength -- name it as preparing to
        face the believed holder, not just "鉄砲を作った"."""
        from gapengine.route import _advance_text

        text = _advance_text(
            Action("craft", ("鉄砲",)),
            self.momotaro,
            self.world,
            {},
            believed_holder_id="鬼",
        )
        self.assertEqual(text, "鬼と渡り合うため鉄砲を作った")

    def test_craft_with_no_known_purpose_stays_the_bare_event(self) -> None:
        from gapengine.route import _advance_text

        text = _advance_text(
            Action("craft", ("船",)),
            self.momotaro,
            self.world,
            {},
            believed_holder_id=None,
        )
        self.assertEqual(text, "先へ渡るため船を作った")

    def test_relation_verb_texts_are_worded_as_intent_not_result(self) -> None:
        from gapengine.route import _advance_text

        text = _advance_text(
            Action("give_item", ("犬",), {"target": "犬", "item": "きびだんご"}),
            self.momotaro,
            self.world,
            {},
        )
        self.assertEqual(text, "犬に仲間に加わってほしくて品を渡した")
        self.assertNotIn("仲間を増やすため", text)
        self.assertNotIn("仲間にした", text)

        text = _advance_text(
            Action("persuade", ("犬",), {"target": "犬"}),
            self.momotaro,
            self.world,
            {},
        )
        self.assertEqual(text, "犬に仲間に加わってほしくて説得した")

    def test_milestone_wins_over_an_unrelated_open_node_in_kinds(self) -> None:
        """S4 review required fix: reproduces the reported mislabeling --
        kinds carries several still-open nodes (犬/stance_ge, negotiate,
        船/has_item) besides the real milestone (木材/has_item, gathered at
        this hop's own destination). The old priority scan over ``kinds``
        picked stance_ge first regardless of which node this move actually
        serves; the milestone now decides directly."""

        from gapengine.route import _advance_text

        text = _advance_text(
            Action("move", ("森",), {"dest": "森"}),
            self.momotaro,
            self.world,
            {"has_item": {"木材", "船"}, "stance_ge": {"犬"}, "route": {"negotiate"}},
            milestone="has_item:木材",
        )
        self.assertEqual(text, "木材を手に入れるため森へ向かった")

    def test_milestone_wins_over_an_open_fight_leaf_elsewhere(self) -> None:
        """Same bug, fight side: win_fight:鬼 sorted ahead of has_item in the
        old scan order, so a wood-gathering hop at 森 was captioned as
        marching on 鬼 even though 鬼 isn't reachable from this hop."""

        from gapengine.route import _advance_text

        text = _advance_text(
            Action("move", ("森",), {"dest": "森"}),
            self.momotaro,
            self.world,
            {"has_item": {"木材"}, "win_fight": {"鬼"}},
            milestone="has_item:木材",
        )
        self.assertEqual(text, "木材を手に入れるため森へ向かった")

    def test_give_item_to_the_goal_holder_reads_as_negotiation_not_recruitment(
        self,
    ) -> None:
        """S4 review recommended fix: a relation-building action toward the
        goal item's own holder (鬼) is about winning them over for a
        hand-off, not recruiting a companion -- the default
        _RELATION_VERB_TEXT wording ("仲間に加わってほしくて") misreads as
        recruitment there."""

        from gapengine.route import _advance_text

        text = _advance_text(
            Action("give_item", ("鬼",), {"target": "鬼", "item": "きびだんご"}),
            self.momotaro,
            self.world,
            {},
            believed_holder_id="鬼",
        )
        self.assertEqual(text, "鬼と話をつけやすくしようと品を渡した")
        self.assertNotIn("仲間に加わってほしくて", text)


class PrepareMoveMilestoneTests(unittest.TestCase):
    """S4 review required fix: a prepare move's own milestone comes from
    alt's leaves, gated to whether this hop's destination actually lies on
    the shortest path to that leaf."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]

    def test_hop_toward_the_leaf_names_it(self) -> None:
        from gapengine.route import _prepare_move_milestone

        # 村 -> 道中 -> 森 is the shortest path to 木材 (sourced at 森);
        # 道中 lies on it.
        milestone = _prepare_move_milestone(
            {"has_item": {"木材"}}, self.world, self.momotaro, "村", "道中"
        )
        self.assertEqual(milestone, "has_item:木材")

    def test_hop_away_from_the_leaf_names_nothing(self) -> None:
        from gapengine.route import _prepare_move_milestone

        # 海 is not on the 村->道中->森 path to 木材.
        milestone = _prepare_move_milestone(
            {"has_item": {"木材"}}, self.world, self.momotaro, "村", "海"
        )
        self.assertIsNone(milestone)


class RouteReasonParityTests(unittest.TestCase):
    """S4 review recommended fix: the four-item panel (viewer/
    explanation_ui.py) and the scene timeline (gapengine/scenes.py) must
    agree on the reason text for the same route record -- both now call
    gapengine.scenes.route_reason."""

    def test_explanation_ui_reuses_scenes_route_reason(self) -> None:
        from gapengine.scenes import route_reason
        from viewer.explanation_ui import route_reason as ui_route_reason

        self.assertIs(ui_route_reason, route_reason)

    def test_lost_with_belief_cause_matches_across_both_call_sites(self) -> None:
        from gapengine.scenes import route_reason

        route = {"kind": "lost", "cause": "belief", "text": "誤った思い込みに基づいて動いた"}
        self.assertEqual(route_reason(route), "誤った思い込みに基づいて動いた")

    def test_detour_none_has_no_reason_at_either_call_site(self) -> None:
        from gapengine.scenes import route_reason

        route = {"kind": "detour", "cause": "none", "text": "勝ち目の薄い無謀な挑戦"}
        self.assertIsNone(route_reason(route))


if __name__ == "__main__":
    unittest.main()
