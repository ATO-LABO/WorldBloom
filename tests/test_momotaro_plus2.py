"""WB-JEV-004 Stage 4 second cut (design by Fable, 2026-09-21): momotaro_plus2
fixes the three funnel problems the momotaro_plus (κ=0.6・35b・192 run)
experiment exposed -- see the Stage 4b plan §1 -- without touching
momotaro/momotaro_plus:

1. the gun no longer requires backtracking to the village (小判 comes only
   from 道中, and 鉄砲 is crafted there too -- both were 村-only or
   村+道中-mixed before);
2. the ship (船) is no longer ``lootable``, so it can never be handed over
   in a negotiate/concede trade (or looted on death -- see
   LootableShipSideEffectTests) while still working as a vehicle;
3. the trip is 20 days instead of 16, giving more slack to reach 鬼ヶ島 and
   back after gathering/crafting.

Also covers the WB-JEV-004 Stage 4b addendum: ``rationality.yaml``'s new
``candidate_labels``/``describe_negotiate_offer`` knobs (momotaro_plus2 only;
momotaro/momotaro_plus must render byte-identically -- see
test_momotaro_plus.py, untouched by this change)."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.actions import Action, candidates
from engine.sim import Simulation
from engine.subject import Subject
from engine.verbs import VerbEngine
from engine.world import World
from gapengine.genome import Genome
from gapengine.knowledge_text import (
    describe_candidate_coarse,
    load_candidate_labels,
    load_describe_negotiate_offer,
)
from gapengine.policy import Policy

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


class WorldLoadTests(unittest.TestCase):
    def test_world_and_subjects_load(self) -> None:
        world, subjects = load_fixture()
        self.assertEqual(world.name, "桃太郎＋2")
        self.assertEqual(world.days, 20)
        self.assertIn("鬼の弟", subjects)
        for item in ("小判", "鉄砲", "弟の手紙"):
            self.assertIn(item, world.items)
        self.assertEqual(world.items["船"]["lootable"], False)
        # 小判 only comes from 道中 now (村's source was removed).
        koban_zones = {
            str(source["zone"]) for source in world.items["小判"]["sources"]
        }
        self.assertEqual(koban_zones, {"道中"})
        self.assertEqual(world.items["鉄砲"]["craft_zone"], "道中")

    def test_one_run_at_kappa_zero(self) -> None:
        # Same path evolve.py's run_individual takes at kappa=0 (no
        # Rationality object constructed -- see test_momotaro_plus.py's
        # identical test).
        import yaml

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
        action_cfg = yaml.safe_load(
            (TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8")
        )
        genome = Genome.neutral()
        policy = Policy(genome, precedent=None, rules=[], cfg=action_cfg)
        with tempfile.TemporaryDirectory() as out_dir:
            simulation = Simulation(
                1,
                world,
                values,
                Path(out_dir),
                policies={world.protagonist: policy},
            )
            path = simulation.run()
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(lines), 1)


class NoBacktrackingGunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]

    def test_koban_gathered_on_the_road_only(self) -> None:
        # 村 no longer yields 小判 at all; 道中 yields 1/investigate, max 3.
        self.momotaro.zone = "村"
        engine = VerbEngine(self.world, random.Random(1))
        action = Action("investigate", (self.momotaro.zone,), {"target": self.momotaro.zone})
        _result, details, _markers = engine.execute(self.momotaro, action, turn=1, day=1)
        self.assertEqual(
            [item["item"] for item in details["gathered"] if item["item"] == "小判"], []
        )

        self.momotaro.zone = "道中"
        gained = []
        for _ in range(4):
            action = Action("investigate", (self.momotaro.zone,), {"target": self.momotaro.zone})
            _result, details, _markers = engine.execute(self.momotaro, action, turn=1, day=1)
            gained.extend(item["item"] for item in details["gathered"] if item["item"] == "小判")
        self.assertEqual(len(gained), 3)  # capped at max=3

    def test_craft_candidate_appears_on_the_road_once_three_koban_are_held(self) -> None:
        self.momotaro.zone = "道中"
        weighted = candidates(self.momotaro, self.world, SimpleNamespace(day=1, turn=1))
        self.assertFalse(
            any(
                action.verb == "craft" and action.args == ("鉄砲",)
                for action, _weight in weighted
            )
        )

        self.momotaro.add_item("小判", 3)
        weighted = candidates(self.momotaro, self.world, SimpleNamespace(day=1, turn=1))
        self.assertTrue(
            any(
                action.verb == "craft" and action.args == ("鉄砲",)
                for action, _weight in weighted
            )
        )

    def test_craft_candidate_does_not_appear_in_the_village(self) -> None:
        # The whole point of Stage 4b: no backtracking to 村 required, and
        # crucially none *possible* either -- craft_zone is 道中 only now.
        self.momotaro.zone = "村"
        self.momotaro.add_item("小判", 3)
        weighted = candidates(self.momotaro, self.world, SimpleNamespace(day=1, turn=1))
        self.assertFalse(
            any(
                action.verb == "craft" and action.args == ("鉄砲",)
                for action, _weight in weighted
            )
        )

    def test_craft_consumes_the_three_koban_and_grants_the_gun(self) -> None:
        self.momotaro.zone = "道中"
        self.momotaro.add_item("小判", 3)
        action = Action("craft", ("鉄砲",), {"item": "鉄砲"})
        engine = VerbEngine(self.world, random.Random(1))
        result, details, _markers = engine.execute(self.momotaro, action, turn=1, day=1)
        self.assertEqual(result, "crafted")
        self.assertEqual(self.momotaro.inventory.get("小判", 0), 0)
        self.assertEqual(self.momotaro.inventory.get("鉄砲", 0), 1)


class LootableShipSideEffectTests(unittest.TestCase):
    """WB-JEV-004 Stage 4b plan §2's "壊れるものが無いか確認": 船's
    lootable: false removes it from negotiate/concede trades (the intended
    change) and, as side effects, from sacrifice-as-asset (engine/actions.py
    _sacrifice_candidates, engine/verbs.py _sacrifice) and from a dead
    holder's loot transfer (engine/vitality.py kill()) -- both desirable
    (nobody should be able to give away or lose the only way home). Fight
    loot (engine/verbs.py _fight) was never affected either way: it already
    excludes vehicle:true items regardless of lootable."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.oni = self.subjects["鬼"]

    def test_ship_not_in_negotiate_assets(self) -> None:
        self.momotaro.zone = "鬼ヶ島"
        self.oni.zone = "鬼ヶ島"
        self.momotaro.add_item("船", 1)
        action = Action(
            "negotiate",
            (self.oni.id,),
            {"target": self.oni.id, "objective": self.momotaro.goal.target},
        )
        engine = VerbEngine(self.world, random.Random(1))
        result, details, _markers = engine.execute(self.momotaro, action, turn=1, day=1)
        self.assertEqual(result, "offered")
        self.assertNotIn("船", details["assets"])

    def test_ship_not_eligible_for_sacrifice(self) -> None:
        # No modifier either, so this was already the case in momotaro_plus
        # -- lootable: false makes it doubly excluded (belt and suspenders:
        # the "lootable and not objective" branch of the eligibility check
        # in engine/actions.py's _sacrifice_candidates now also fails).
        # Strip the other eligible asset (勾玉, has a modifier) so 船 would be
        # the only candidate if it were eligible -- otherwise
        # _sacrifice_candidates' single "propose the first eligible asset"
        # slot could hide a still-eligible 船 behind 勾玉 and this test would
        # pass for the wrong reason.
        self.momotaro.inventory.pop("勾玉", None)
        self.momotaro.add_item("船", 1)
        weighted = candidates(self.momotaro, self.world, SimpleNamespace(day=1, turn=1))
        sacrifice_items = {
            action.meta.get("item")
            for action, _weight in weighted
            if action.verb == "sacrifice" and action.meta.get("kind") == "asset"
        }
        self.assertEqual(sacrifice_items, set())

    def test_ship_not_transferred_when_holder_is_killed(self) -> None:
        from engine import vitality

        self.momotaro.add_item("船", 1)
        vitality.kill(self.momotaro, self.world, killer=self.oni)
        # Not lootable -> engine/vitality.py's kill() skips it entirely: the
        # dead body still "has" it (never removed), and the killer never
        # gets it. In momotaro_plus (lootable: true) this would transfer to
        # the killer instead.
        self.assertEqual(self.momotaro.inventory.get("船", 0), 1)
        self.assertEqual(self.oni.inventory.get("船", 0), 0)


class TradeConcedeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.oni = self.subjects["鬼"]
        self.momotaro.zone = "鬼ヶ島"
        self.oni.zone = "鬼ヶ島"

    def _negotiate(self) -> None:
        action = Action(
            "negotiate",
            (self.oni.id,),
            {"target": self.oni.id, "objective": self.momotaro.goal.target},
        )
        engine = VerbEngine(self.world, random.Random(1))
        result, _details, _markers = engine.execute(self.momotaro, action, turn=1, day=1)
        self.assertEqual(result, "offered")

    def test_gun_offer_makes_concede_a_trade(self) -> None:
        self.momotaro.add_item("鉄砲", 1)
        self._negotiate()
        weighted = candidates(self.oni, self.world, SimpleNamespace(day=1, turn=1))
        concede = next(
            (action for action, _weight in weighted if action.verb == "concede"),
            None,
        )
        self.assertIsNotNone(concede)
        self.assertEqual(concede.meta["mode"], "trade")
        self.assertIn("鉄砲", concede.meta["trade_assets"])

    def test_letter_offer_makes_concede_a_trade(self) -> None:
        self.momotaro.add_item("弟の手紙", 1)
        self._negotiate()
        weighted = candidates(self.oni, self.world, SimpleNamespace(day=1, turn=1))
        concede = next(
            (action for action, _weight in weighted if action.verb == "concede"),
            None,
        )
        self.assertIsNotNone(concede)
        self.assertEqual(concede.meta["mode"], "trade")
        self.assertIn("弟の手紙", concede.meta["trade_assets"])


class RationalityYamlLoaderTests(unittest.TestCase):
    """momotaro_plus2's rationality.yaml addendum (Stage 4b): the two
    templates predating it must load the defaults (empty/False)."""

    def test_defaults_for_templates_predating_stage_4b(self) -> None:
        self.assertEqual(load_candidate_labels(ROOT / "templates" / "momotaro"), {})
        self.assertFalse(load_describe_negotiate_offer(ROOT / "templates" / "momotaro"))
        self.assertEqual(load_candidate_labels(ROOT / "templates" / "momotaro_plus"), {})
        self.assertFalse(load_describe_negotiate_offer(ROOT / "templates" / "momotaro_plus"))

    def test_plus2_loads_the_new_knobs(self) -> None:
        labels = load_candidate_labels(TEMPLATE)
        self.assertEqual(
            labels,
            {
                "craft:鉄砲": "道中の商人から小判3枚で鉄砲を買った",
                "trial:brother_letter_trial": "鬼の弟に、鬼あての手紙を書いてもらった",
            },
        )
        self.assertTrue(load_describe_negotiate_offer(TEMPLATE))


class CandidateLabelReplacementTests(unittest.TestCase):
    """describe_candidate_coarse's candidate_labels knob: a matching craft
    or trial candidate's whole description is replaced -- default {} keeps
    momotaro/momotaro_plus byte-identical (WB-JEV-004 Stage 4b addendum)."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.otouto = self.subjects["鬼の弟"]
        self.labels = load_candidate_labels(TEMPLATE)

    def test_unset_leaves_output_unchanged(self) -> None:
        action = Action("craft", ("鉄砲",), {"item": "鉄砲"})
        self.assertEqual(
            describe_candidate_coarse(action, self.momotaro, self.world, []),
            "作った（鉄砲）",
        )

    def test_craft_label_is_replaced(self) -> None:
        action = Action("craft", ("鉄砲",), {"item": "鉄砲"})
        text = describe_candidate_coarse(
            action, self.momotaro, self.world, [], candidate_labels=self.labels
        )
        self.assertEqual(text, "道中の商人から小判3枚で鉄砲を買った")

    def test_trial_label_is_replaced_and_wins_over_describe_trial_grants(self) -> None:
        action = Action(
            "trial",
            (self.otouto.id,),
            {"target": self.otouto.id, "trial_id": "brother_letter_trial"},
        )
        present = [self.momotaro, self.otouto]
        text = describe_candidate_coarse(
            action,
            self.momotaro,
            self.world,
            present,
            describe_trial_grants=True,
            candidate_labels=self.labels,
        )
        self.assertEqual(text, "鬼の弟に、鬼あての手紙を書いてもらった")

    def test_unmatched_craft_item_is_unaffected(self) -> None:
        # Only "craft:鉄砲" is mapped; other recipes (船) fall through.
        action = Action("craft", ("船",), {"item": "船"})
        text = describe_candidate_coarse(
            action, self.momotaro, self.world, [], candidate_labels=self.labels
        )
        self.assertEqual(text, "作った（船）")


class NegotiateOfferDescriptionTests(unittest.TestCase):
    """describe_candidate_coarse's describe_negotiate_offer knob: default
    False keeps momotaro/momotaro_plus byte-identical."""

    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.oni = self.subjects["鬼"]
        self.momotaro.zone = "鬼ヶ島"
        self.oni.zone = "鬼ヶ島"
        self.action = Action(
            "negotiate",
            (self.oni.id,),
            {"target": self.oni.id, "objective": self.momotaro.goal.target},
        )

    def test_unset_leaves_output_unchanged(self) -> None:
        text = describe_candidate_coarse(
            self.action, self.momotaro, self.world, [self.momotaro, self.oni]
        )
        self.assertEqual(text, "交渉した（敵対）")

    def test_with_letter_only(self) -> None:
        self.momotaro.inventory.pop("勾玉", None)
        self.momotaro.add_item("弟の手紙", 1)
        text = describe_candidate_coarse(
            self.action,
            self.momotaro,
            self.world,
            [self.momotaro, self.oni],
            describe_negotiate_offer=True,
        )
        self.assertEqual(text, "宝を譲るよう交渉した（敵対、差し出せる品: 弟の手紙）")

    def test_with_gun_only(self) -> None:
        self.momotaro.inventory.pop("勾玉", None)
        self.momotaro.add_item("鉄砲", 1)
        text = describe_candidate_coarse(
            self.action,
            self.momotaro,
            self.world,
            [self.momotaro, self.oni],
            describe_negotiate_offer=True,
        )
        self.assertEqual(text, "宝を譲るよう交渉した（敵対、差し出せる品: 鉄砲）")

    def test_with_nothing_attractive(self) -> None:
        self.momotaro.inventory.pop("勾玉", None)
        text = describe_candidate_coarse(
            self.action,
            self.momotaro,
            self.world,
            [self.momotaro, self.oni],
            describe_negotiate_offer=True,
        )
        self.assertEqual(text, "宝を譲るよう交渉した（敵対、差し出せる品なし）")

    def test_ship_never_counts_even_when_held(self) -> None:
        # momotaro_plus2's 船 is lootable: false -- even holding it, it must
        # never show up as an offerable asset (plan addendum's explicit
        # regression check).
        self.momotaro.inventory.pop("勾玉", None)
        self.momotaro.add_item("船", 1)
        text = describe_candidate_coarse(
            self.action,
            self.momotaro,
            self.world,
            [self.momotaro, self.oni],
            describe_negotiate_offer=True,
        )
        self.assertNotIn("船", text)
        self.assertEqual(text, "宝を譲るよう交渉した（敵対、差し出せる品なし）")


if __name__ == "__main__":
    unittest.main()
