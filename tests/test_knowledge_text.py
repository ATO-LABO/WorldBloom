"""WB-JEV-001 Stage 1 (revised 2026-09-18, Opus review fixes) acceptance
tests for gapengine/knowledge_text.py's situation-class renderer."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from engine.actions import Action
from engine.contest import strength
from engine.subject import Subject
from engine.world import World
from gapengine.knowledge_text import (
    _power_bucket,
    context_key,
    describe_candidate,
    describe_candidate_coarse,
    load_common_knowledge,
    load_key_items,
    map_line,
    recipes_line,
    render_situation,
    situation,
)

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "momotaro"
TEMPLATE = ROOT / "templates" / "momotaro"

DECIMAL = re.compile(r"\d+\.\d+")


def load_fixture() -> tuple[World, dict[str, Subject]]:
    world = World.from_yaml(PROJECT / "world.yaml")
    subjects = [
        Subject.from_yaml(path)
        for path in sorted(
            (PROJECT / "subjects").glob("*.yaml"),
            key=lambda value: value.name,
        )
    ]
    values = {subject.id: subject for subject in subjects}
    world.bind_subjects(values)
    return world, values


class SituationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.oni = self.subjects["鬼"]
        self.dog = self.subjects["犬"]
        self.ojiisan = self.subjects["おじいさん"]
        # ally + neutral + hostile all in one cast, like the v1 test.
        self.present = [self.momotaro, self.ojiisan, self.dog, self.oni]
        self.key_items = load_key_items(TEMPLATE)
        self.common_knowledge = load_common_knowledge(TEMPLATE)
        self.recipe_lines = recipes_line(self.momotaro, self.world)
        self.map_line = map_line(self.world)

    def render(self) -> str:
        sit = situation(self.momotaro, self.world, self.present, key_items=self.key_items)
        return render_situation(
            sit,
            self.world,
            common_knowledge=self.common_knowledge,
            recipe_lines=self.recipe_lines,
            map_line=self.map_line,
        )

    def test_deterministic(self) -> None:
        self.assertEqual(self.render(), self.render())

    def test_no_decimals(self) -> None:
        self.assertIsNone(DECIMAL.search(self.render()))

    def test_no_ground_truth_leak(self) -> None:
        text = self.render()
        oni_strength = strength(self.oni, self.world, [])
        self.assertNotIn(str(oni_strength), text)

        known_line = next(
            line for line in text.splitlines() if line.startswith("見当がついていること:")
        )
        for fact_id, value in self.world.truth.items():
            if fact_id not in self.momotaro.beliefs:
                self.assertNotIn(value, known_line)
        # even when momotaro *does* hold a belief, only the fact id/label is
        # shown -- never the believed value itself.
        known_content = known_line.removeprefix("見当がついていること: ")
        self.assertNotIn(": ", known_content)

    def test_known_valued_has_no_unfilled_placeholder(self) -> None:
        # 桃太郎 holds no valued belief by default; force one to exercise the
        # {value}-in-label rendering path (Opus review: 522/768 decision
        # points leaked a literal "{value}").
        from engine.subject import Belief

        self.momotaro.beliefs["oni_weakness"] = Belief(value="金棒", confidence=0.8)
        text = self.render()
        known_line = next(
            line for line in text.splitlines() if line.startswith("見当がついていること:")
        )
        self.assertNotIn("{value}", known_line)
        self.assertIn("何か", known_line)
        self.assertNotIn("金棒", known_line)  # the believed value itself never appears

    def test_key_items(self) -> None:
        self.assertEqual(self.key_items, ["きびだんご"])
        sit = situation(self.momotaro, self.world, self.present, key_items=self.key_items)
        self.assertEqual(sit["key_items"], {"きびだんご": True})
        text = self.render()
        holdings_line = next(line for line in text.splitlines() if line.startswith("所持:"))
        self.assertIn("きびだんご: あり", holdings_line)

    def test_recipes_state(self) -> None:
        sit = situation(self.momotaro, self.world, self.present, key_items=self.key_items)
        # 桃太郎 knows 造船術 but has no 木材/縄 yet.
        self.assertEqual(sit["recipes"].get("船"), "材料不足")
        self.assertIn("船", self.recipe_lines)

    def test_companions_and_objective_use_roles_not_names(self) -> None:
        text = self.render()
        self.assertIn("同席: 味方がいる、中立の相手がいる、敵対者がいる", text)
        self.assertIn("目的物: 敵対者が持っている", text)
        for name in ("犬", "おじいさん"):
            self.assertNotIn(name, text)

    def test_power_bucket_boundaries(self) -> None:
        self.assertEqual(_power_bucket(0.94), "劣る")
        self.assertEqual(_power_bucket(0.96), "互角")
        self.assertEqual(_power_bucket(1.04), "互角")
        self.assertEqual(_power_bucket(1.06), "優る")

    def test_common_knowledge_reflected(self) -> None:
        sit = situation(self.momotaro, self.world, self.present, key_items=self.key_items)
        text = render_situation(
            sit,
            self.world,
            common_knowledge=["常識A", "常識B"],
            recipe_lines=self.recipe_lines,
            map_line=self.map_line,
        )
        self.assertIn("世界の常識: 常識A／常識B", text)

    def test_load_key_items_and_common_knowledge(self) -> None:
        self.assertTrue(all(isinstance(v, str) for v in self.key_items))
        self.assertTrue(all(isinstance(v, str) for v in self.common_knowledge))
        empty_dir = ROOT / "templates" / "romance"
        self.assertEqual(load_key_items(empty_dir), [])
        self.assertEqual(load_common_knowledge(empty_dir), [])

    def test_context_key_deterministic(self) -> None:
        text = self.render()
        self.assertEqual(context_key(text), context_key(text))
        self.assertNotEqual(context_key(text), context_key(text + "x"))
        self.assertEqual(len(context_key(text)), 16)


class DescribeCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world, self.subjects = load_fixture()
        self.momotaro = self.subjects["桃太郎"]
        self.oni = self.subjects["鬼"]
        self.dog = self.subjects["犬"]
        self.ojiisan = self.subjects["おじいさん"]
        self.present = [self.momotaro, self.ojiisan, self.dog, self.oni]

    def test_describe_candidate_exact(self) -> None:
        self.assertEqual(describe_candidate(Action("move", ("海",))), "移動した（海）")

    def test_coarse_replaces_ally_name_with_role(self) -> None:
        action = Action("give_item", ("おじいさん", "きびだんご"))
        text = describe_candidate_coarse(action, self.momotaro, self.world, self.present)
        self.assertEqual(text, "品物を渡した（味方、きびだんご）")

    def test_coarse_replaces_hostile_name_with_role(self) -> None:
        action = Action("fight", ("鬼",))
        text = describe_candidate_coarse(action, self.momotaro, self.world, self.present)
        self.assertEqual(text, "戦った（敵対）")

    def test_coarse_mislead_strength_drops_decimal(self) -> None:
        action = Action(
            "mislead",
            ("犬", "桃太郎", 42.5),
            {"target": "犬", "about": "桃太郎", "belief_kind": "strength"},
        )
        text = describe_candidate_coarse(action, self.momotaro, self.world, self.present)
        self.assertEqual(text, "誤った情報へ誘導した（中立、桃太郎の強さ）")
        self.assertIsNone(DECIMAL.search(text))

    def test_coarse_mislead_valued_fact_drops_placeholder_and_name(self) -> None:
        # oni_weakness's label is "鬼の力の要は{value}だ"; 金棒の由来's label
        # embeds no companion name here, so use treasure_thief instead, whose
        # "宝を奪ったのは{value}だ" has no name either -- exercise the
        # {value} defuzz path (name-in-label is covered by share_knowledge
        # below, where 猿の知恵's label bakes in 猿).
        action = Action(
            "mislead",
            ("犬", "treasure_thief", "猿"),
            {"target": "犬", "about": "treasure_thief", "belief_kind": "valued_fact"},
        )
        text = describe_candidate_coarse(action, self.momotaro, self.world, self.present)
        self.assertNotIn("{value}", text)
        self.assertEqual(text, "誤った情報へ誘導した（中立、宝を奪ったのは何か）")

    def test_coarse_share_knowledge_fact_uses_label_not_raw_id(self) -> None:
        # 猿の知恵's id AND label ("猿が知る鬼ヶ島の抜け道") both bake in 猿's
        # name; with 猿 present as a neutral bystander it must become a role.
        saru = self.subjects["猿"]
        present = [*self.present, saru]
        action = Action("share_knowledge", ("犬", "猿の知恵"))
        text = describe_candidate_coarse(action, self.momotaro, self.world, present)
        self.assertNotIn("猿", text)
        self.assertEqual(text, "知識を伝えた（中立、中立が知る鬼ヶ島の抜け道）")

    def test_coarse_share_knowledge_chitchat_unchanged(self) -> None:
        action = Action("share_knowledge", ("犬", "雑談"))
        text = describe_candidate_coarse(action, self.momotaro, self.world, self.present)
        self.assertEqual(text, "知識を伝えた（中立、雑談）")

    def test_coarse_move_keeps_zone_name(self) -> None:
        action = Action("move", ("海",))
        text = describe_candidate_coarse(action, self.momotaro, self.world, self.present)
        self.assertEqual(text, "移動した（海）")


class GoldenTurnOneTests(unittest.TestCase):
    """Locks turn 1's full render (seed 1, momotaro) so a future change to
    line order/labels shows up as a diff, not a surprise."""

    def test_turn_one_render_matches_exactly(self) -> None:
        from scripts.rationality_probe import _run_mode_a

        with tempfile.TemporaryDirectory() as run_out:
            points = _run_mode_a(
                project=PROJECT,
                template=TEMPLATE,
                seed=1,
                turns=1,
                run_out=Path(run_out),
            )
        point = points[0]
        self.assertEqual(point["label"], "turn=1 day=1 zone=道中")
        self.assertEqual(
            point["state_text"],
            "\n".join(
                [
                    "目的: 鬼ヶ島の宝物を村へ持ち帰る",
                    "現在地: 道中",
                    "通過段階: なし",
                    "同席: 味方はいない、中立の相手がいる、敵対者はいない",
                    "目的物: 敵対者が持っている",
                    "所持: きびだんご: あり、船: 材料不足",
                    "力関係: 劣る",
                    "生命状態: 健在",
                    "見当がついていること: なし",
                    "知っている作り方: 船は木材2と縄1から海で作れる"
                    "（木材は森で調べると手に入る、縄は村で調べると手に入る）",
                    "知っている地図: 村→道中、森→道中、海→道中、"
                    "海→鬼ヶ島（船が必要）、道中→村、道中→森、道中→海、"
                    "鬼ヶ島→海（船が必要）",
                    "世界の常識: きびだんごを渡すと相手は仲間になりやすい／"
                    "仲間は戦いで力を貸してくれる／鬼ヶ島へ渡るには船が要る／"
                    "鬼は宝物を守っており、倒すか説得しなければ宝物は手に入らない／"
                    "相手を観察すると弱点や隠し持つ力が分かることがある",
                ]
            ),
        )
        chosen = [desc for desc, is_chosen in point["candidates"] if is_chosen]
        self.assertEqual(chosen, ["知識を伝えた（中立、雑談）"])


class NoDisallowedNamesAcrossSeedsTests(unittest.TestCase):
    """Runs the real Mode A pipeline for seeds 1-3 and checks that no
    companion name (only protagonist/antagonist/zones/items/effect ids are
    allowed) leaks into either the rendered situation or any candidate
    description."""

    def test_seeds_one_to_three_have_no_disallowed_names(self) -> None:
        from scripts.rationality_probe import _run_mode_a

        world, _ = load_fixture()
        disallowed = sorted(set(world.subjects) - {world.protagonist, world.antagonist})
        self.assertTrue(disallowed)  # sanity: there is something to check for

        for seed in (1, 2, 3):
            with tempfile.TemporaryDirectory() as run_out:
                points = _run_mode_a(
                    project=PROJECT,
                    template=TEMPLATE,
                    seed=seed,
                    turns=None,
                    run_out=Path(run_out),
                )
            for point in points:
                for name in disallowed:
                    self.assertNotIn(
                        name,
                        point["state_text"],
                        f"seed={seed} {point['label']}: disallowed name {name!r} in situation text",
                    )
                for desc, _chosen in point["candidates"]:
                    for name in disallowed:
                        self.assertNotIn(
                            name,
                            desc,
                            f"seed={seed} {point['label']}: disallowed name {name!r} in {desc!r}",
                        )


if __name__ == "__main__":
    unittest.main()
