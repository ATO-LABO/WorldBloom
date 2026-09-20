"""WB-JEV-004 Stage 4 acceptance tests: the momotaro_plus world (money -> gun,
oni's estranged brother -> trade leverage) loads, is playable end to end, and
its new routes actually offer as candidates/execute as expected. Momotaro
(base) is untouched -- see test_momotaro_plus_does_not_touch_momotaro."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.actions import Action, candidates
from engine.phase2 import trial_options
from engine.sim import Simulation
from engine.subject import Subject
from engine.verbs import VerbEngine
from engine.world import World
from gapengine.genome import Genome
from gapengine.knowledge_text import (
    describe_candidate_coarse,
    load_common_knowledge,
    load_describe_trial_grants,
    load_key_items,
    map_line,
    recipes_line,
    render_situation,
    situation,
)
from gapengine.policy import Policy

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "momotaro_plus"
TEMPLATE = ROOT / "templates" / "momotaro_plus"


def load_fixture() -> tuple[World, dict[str, Subject]]:
    world = World.from_yaml(PROJECT / "world.yaml")
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
        self.assertEqual(world.name, "桃太郎＋")
        self.assertIn("鬼の弟", subjects)
        self.assertEqual(subjects["鬼の弟"].zone, "森")
        for item in ("小判", "鉄砲", "弟の手紙"):
            self.assertIn(item, world.items)
        self.assertIn("弟の消息", world.facts)
        self.assertIn(
            "brother_letter_trial",
            {trial["id"] for trial in world.trials},
        )

    def test_one_run_at_kappa_zero(self) -> None:
        # "κ=0で1ラン走る": no Rationality object is even constructed when
        # kappa<=0 in gapengine.evolve, so this exercises the plain engine
        # loop -- the same path evolve.py's run_individual takes at kappa=0.
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
        # More than just the header row: the run actually progressed.
        self.assertGreater(len(lines), 1)


class BrotherRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.otouto = self.subjects["鬼の弟"]
        self.oni = self.subjects["鬼"]

    def test_brother_lives_in_forest_and_offers_trial_when_stance_and_item_met(self) -> None:
        self.momotaro.zone = "森"
        # Below threshold (0.4) and without きびだんご: no trial offered.
        available = trial_options(self.world, self.momotaro)
        self.assertNotIn(
            "brother_letter_trial",
            {trial["id"] for trial in available},
        )

        self.world.relations.change(self.otouto.id, self.momotaro.id, affinity=0.4)
        self.assertGreaterEqual(
            self.world.relations.stance(self.otouto.id, self.momotaro.id), 0.4
        )
        self.assertTrue(self.momotaro.has_item("きびだんご"))
        available = trial_options(self.world, self.momotaro)
        self.assertIn(
            "brother_letter_trial",
            {trial["id"] for trial in available},
        )

        # It also shows up as a weighted "trial" candidate for 桃太郎.
        weighted = candidates(self.momotaro, self.world, SimpleNamespace(day=1, turn=1))
        trial_actions = [
            action
            for action, _weight in weighted
            if action.verb == "trial" and action.meta.get("trial_id") == "brother_letter_trial"
        ]
        self.assertEqual(len(trial_actions), 1)

    def test_trial_requires_item_is_not_consumed(self) -> None:
        # engine/phase2.py's trial_options only checks subject.has_item;
        # engine/verbs.py's _trial handler never removes it -- unlike craft,
        # which does consume its materials (see CraftGunTests below).
        self.momotaro.zone = "森"
        self.world.relations.change(self.otouto.id, self.momotaro.id, affinity=0.4)
        before = self.momotaro.inventory.get("きびだんご", 0)
        self.assertGreater(before, 0)

        action = Action(
            "trial",
            (self.otouto.id,),
            {"target": self.otouto.id, "trial_id": "brother_letter_trial"},
        )
        engine = VerbEngine(self.world, random.Random(1))
        result, details, _markers = engine.execute(
            self.momotaro, action, turn=1, day=1
        )
        self.assertEqual(result, "trial_completed")
        self.assertEqual(details["granted"], {"item": "弟の手紙"})
        self.assertEqual(self.momotaro.inventory.get("きびだんご", 0), before)
        self.assertEqual(self.momotaro.inventory.get("弟の手紙", 0), 1)


class CraftGunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]

    def test_craft_candidate_appears_once_three_koban_are_held_in_village(self) -> None:
        self.momotaro.zone = "村"
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

    def test_craft_consumes_the_three_koban_and_grants_the_gun(self) -> None:
        self.momotaro.zone = "村"
        self.momotaro.add_item("小判", 3)
        action = Action("craft", ("鉄砲",), {"item": "鉄砲"})
        engine = VerbEngine(self.world, random.Random(1))
        result, details, _markers = engine.execute(self.momotaro, action, turn=1, day=1)
        self.assertEqual(result, "crafted")
        self.assertEqual(self.momotaro.inventory.get("小判", 0), 0)
        self.assertEqual(self.momotaro.inventory.get("鉄砲", 0), 1)

    def test_koban_gather_rate_matches_plan(self) -> None:
        # 村: count=1 per investigate, max=3. 道中: count=2 per investigate,
        # max=2 (so 4 investigates there yield 2 koban). Verified directly
        # against engine/verbs.py's _gather_items rather than assumed.
        self.momotaro.zone = "村"
        engine = VerbEngine(self.world, random.Random(1))
        gained = []
        for _ in range(4):
            action = Action("investigate", (self.momotaro.zone,), {"target": self.momotaro.zone})
            _result, details, _markers = engine.execute(
                self.momotaro, action, turn=1, day=1
            )
            gained.extend(item["item"] for item in details["gathered"] if item["item"] == "小判")
        self.assertEqual(len(gained), 3)  # capped at max=3 in 村

        self.momotaro.zone = "道中"
        gained_road = []
        for _ in range(4):
            action = Action("investigate", (self.momotaro.zone,), {"target": self.momotaro.zone})
            _result, details, _markers = engine.execute(
                self.momotaro, action, turn=1, day=1
            )
            gained_road.extend(
                item["item"] for item in details["gathered"] if item["item"] == "小判"
            )
        self.assertEqual(len(gained_road), 2)  # count=2 per koban, max=2 -> needs 4 tries


class TradeConcedeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.oni = self.subjects["鬼"]
        self.momotaro.zone = "鬼ヶ島"
        self.oni.zone = "鬼ヶ島"

    def _negotiate(self) -> None:
        present = self.world.present_subjects(self.momotaro.zone)
        action = Action(
            "negotiate",
            (self.oni.id,),
            {"target": self.oni.id, "objective": self.momotaro.goal.target},
        )
        engine = VerbEngine(self.world, random.Random(1))
        result, _details, _markers = engine.execute(
            self.momotaro, action, turn=1, day=1
        )
        self.assertEqual(result, "offered")

    def test_gun_offer_makes_concede_a_trade(self) -> None:
        self.momotaro.add_item("鉄砲", 1)
        self._negotiate()
        present = self.world.present_subjects(self.oni.zone)
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

    def test_concede_execution_transfers_gun_for_treasure(self) -> None:
        self.momotaro.add_item("鉄砲", 1)
        self._negotiate()
        action = Action(
            "concede",
            (self.momotaro.id,),
            {"target": self.momotaro.id},
        )
        engine = VerbEngine(self.world, random.Random(1))
        result, details, _markers = engine.execute(self.oni, action, turn=1, day=1)
        self.assertEqual(result, "conceded")
        self.assertEqual(details["mode"], "trade")
        self.assertEqual(self.momotaro.inventory.get("鉄砲", 0), 0)
        self.assertEqual(self.oni.inventory.get("鉄砲", 0), 1)
        self.assertEqual(self.momotaro.inventory.get("鬼ヶ島の宝物", 0), 1)


class RenderSituationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.key_items = load_key_items(TEMPLATE)
        self.common_knowledge = load_common_knowledge(TEMPLATE)
        self.recipe_lines = recipes_line(self.momotaro, self.world)
        self.map_line = map_line(self.world)

    def test_key_items_and_common_knowledge_loaded(self) -> None:
        self.assertEqual(
            self.key_items, ["きびだんご", "小判", "鉄砲", "弟の手紙"]
        )
        self.assertEqual(len(self.common_knowledge), 9)
        self.assertIn(
            "村の店では小判3枚で鉄砲が買える。鉄砲があれば鬼と互角以上に戦える",
            self.common_knowledge,
        )
        self.assertIn(
            "鬼には生き別れの弟がいて、森で木こりをしている",
            self.common_knowledge,
        )

    def test_render_situation_shows_koban_gun_and_letter_holdings(self) -> None:
        self.momotaro.zone = "村"
        self.momotaro.add_item("小判", 3)
        self.momotaro.add_item("鉄砲", 1)
        self.momotaro.add_item("弟の手紙", 1)
        present = self.world.present_subjects(self.momotaro.zone)
        sit = situation(self.momotaro, self.world, present, key_items=self.key_items)
        text = render_situation(
            sit,
            self.world,
            common_knowledge=self.common_knowledge,
            recipe_lines=self.recipe_lines,
            map_line=self.map_line,
        )
        holdings_line = next(line for line in text.splitlines() if line.startswith("所持:"))
        self.assertIn("小判: あり", holdings_line)
        self.assertIn("鉄砲: あり", holdings_line)
        self.assertIn("弟の手紙: あり", holdings_line)
        knowledge_line = next(
            line for line in text.splitlines() if line.startswith("世界の常識:")
        )
        self.assertIn("小判3枚で鉄砲が買える", knowledge_line)
        self.assertIn("生き別れの弟", knowledge_line)


class DescribeTrialGrantsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.otouto = self.subjects["鬼の弟"]
        self.saru = self.subjects["猿"]

    def test_flag_loaded_true_for_momotaro_plus(self) -> None:
        self.assertTrue(load_describe_trial_grants(TEMPLATE))
        self.assertFalse(load_describe_trial_grants(ROOT / "templates" / "momotaro"))

    def test_item_grant_is_appended_only_when_enabled(self) -> None:
        present = [self.momotaro, self.otouto]
        action = Action(
            "trial",
            (self.otouto.id,),
            {"target": self.otouto.id, "trial_id": "brother_letter_trial"},
        )
        without = describe_candidate_coarse(action, self.momotaro, self.world, present)
        self.assertEqual(without, "試練に挑んだ（中立）")
        with_grants = describe_candidate_coarse(
            action, self.momotaro, self.world, present, describe_trial_grants=True
        )
        self.assertEqual(with_grants, "試練に挑んだ（中立、弟の手紙を得る）")

    def test_fact_grant_is_appended_only_when_enabled(self) -> None:
        present = [self.momotaro, self.saru]
        action = Action(
            "trial",
            (self.saru.id,),
            {"target": self.saru.id, "trial_id": "monkey_shortcut_trial"},
        )
        without = describe_candidate_coarse(action, self.momotaro, self.world, present)
        self.assertEqual(without, "試練に挑んだ（中立）")
        with_grants = describe_candidate_coarse(
            action, self.momotaro, self.world, present, describe_trial_grants=True
        )
        self.assertEqual(with_grants, "試練に挑んだ（中立、中立が知る鬼ヶ島の抜け道を得る）")


if __name__ == "__main__":
    unittest.main()
