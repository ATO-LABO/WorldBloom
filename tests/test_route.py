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
        # companion holding a raw material (縄) must only be planned via
        # fight, never "negotiated" for.
        def setup(m, w):
            m.zone = "道中"
            m.inventory["木材"] = 2
            w.subjects["犬"].inventory["縄"] = 1

        momotaro, world, _out = self._annotate(setup, [])
        state = plan(
            momotaro, world, holder_belief_fact="treasure_thief", trial_reveal_facts=self.TRF
        )
        # 縄's own acquisition sub-problem must be tagged "fight", not
        # "negotiate" -- and no stance_ge/offer-building tag for 縄's holder
        # (犬) should appear at all, since negotiate was never considered
        # for a non-objective item.
        self.assertIn(("win_fight", "犬"), state["best"])
        self.assertNotIn(("stance_ge", "犬"), state["best"])
        self.assertNotIn(("stance_ge", "犬"), state["alt"])

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
        def setup(m, w):
            m.zone = "道中"
            m.inventory["きびだんご"] = 1

        _m, _w, out = self._annotate(
            setup,
            [Action("give_item", ("犬", "きびだんご"), {"target": "犬", "item": "きびだんご"})],
        )
        self.assertEqual(out[0]["kind"], "prepare")

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


def _hash_seed_script(seed: int) -> str:
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


if __name__ == "__main__":
    unittest.main()
