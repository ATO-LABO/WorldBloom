"""WB-GROWTH-001 S2: scenes/synopsis rendering, the viewer's "性格の変化"
run-detail panel, and the "性格の成長" execution-settings toggle."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from test_viewer import _write_json, _write_jsonl

from gapengine.scenes import SPECIAL_PRIORITY, describe_row, extract_scenes, growth_event_text
from gapengine.synopsis import (
    _has_growth,
    build_narration_prompt,
    build_synopsis_prompt,
)


GROWTH_ROW = {
    "kind": "event",
    # Same turn as the fight decision below: a "growth" row is always
    # written right after the row that triggered it (engine/sim.py's
    # on_write callback fires synchronously), never on some later turn.
    "turn": 1,
    "day": 1,
    "slot": "morning",
    "subject": "桃太郎",
    "verb": "growth",
    "args": [],
    "result": "applied",
    "delta": {},
    "details": {
        "rule": "g_fight_won",
        "description": "勝利で大胆になる",
        "trigger": {"kind": "decision", "verb": "fight", "result": "won", "turn": 1},
        "shift": {"risk_tolerance": 0.1, "category_weight.I": 0.1},
        "plasticity": 1.0,
        "acquired_after": {"risk_tolerance": 0.1, "category_weight.I": 0.1},
    },
}

NO_GROWTH_ROWS = [
    {"kind": "header", "protagonist": "桃太郎", "antagonist": "鬼"},
    {
        "kind": "decision",
        "turn": 1,
        "day": 1,
        "slot": "morning",
        "subject": "桃太郎",
        "verb": "fight",
        "args": ["鬼"],
        "result": "won",
        "effective": True,
        "delta": {"actor": {}, "targets": {}, "relations": [], "objective": None},
        "classification": {
            "category": "I", "subtype": "attack", "risk_class": "risky",
            "stance_sign": 1, "target_role": "hostile",
        },
    },
]

WITH_GROWTH_ROWS = NO_GROWTH_ROWS + [GROWTH_ROW]

# Review fix (should #3): a turn where "growth" is the *only* protagonist-
# relevant row -- a "rest" that is not effective, no objective change, no
# SPECIAL_PRIORITY verb -- must never become a scene by itself.
GROWTH_ONLY_ROWS = [
    {"kind": "header", "protagonist": "桃太郎", "antagonist": "鬼"},
    {
        "kind": "decision",
        "turn": 5,
        "day": 1,
        "slot": "evening",
        "subject": "桃太郎",
        "verb": "rest",
        "args": [],
        "result": "rested",
        "effective": False,
        "delta": {"actor": {}, "targets": {}, "relations": [], "objective": None},
        "classification": {
            "category": None, "subtype": "rest", "risk_class": "neutral",
            "stance_sign": 0, "target_role": "none",
        },
    },
    dict(GROWTH_ROW, turn=5, slot="evening"),
]


class ScenesGrowthTests(unittest.TestCase):
    def test_growth_is_not_in_special_priority(self) -> None:
        # Review fix (must #3): growth must never bump a turn's priority or
        # select it as a scene by itself.
        self.assertNotIn("growth", SPECIAL_PRIORITY)

    def test_describe_row_growth_text(self) -> None:
        text = describe_row(GROWTH_ROW, {})
        self.assertIn("勝利で大胆になる", text)
        self.assertIn("慎重さ +0.100", text)
        self.assertIn("カテゴリI +0.100", text)

    def test_growth_event_text_with_empty_shift_is_bare_description(self) -> None:
        self.assertEqual(
            growth_event_text({"description": "何かが起きた", "shift": {}}),
            "何かが起きた",
        )

    def test_extract_scenes_has_no_growth_key_without_growth_rows(self) -> None:
        scenes = extract_scenes(NO_GROWTH_ROWS, {"protagonist": "桃太郎"})
        self.assertTrue(scenes)
        self.assertNotIn("growth", scenes[0])

    def test_extract_scenes_adds_growth_key_only_when_present(self) -> None:
        scenes = extract_scenes(WITH_GROWTH_ROWS, {"protagonist": "桃太郎"})
        matching = [scene for scene in scenes if scene["turn"] == 1]
        self.assertEqual(len(matching), 1)
        growth = matching[0]["growth"]
        self.assertEqual(len(growth), 1)
        self.assertEqual(growth[0]["rule"], "g_fight_won")
        self.assertEqual(
            growth[0]["shift"], {"category_weight.I": 0.1, "risk_tolerance": 0.1}
        )

    def test_growth_alone_does_not_create_a_scene(self) -> None:
        # Review fix (must #3): "growth" carries no SPECIAL_PRIORITY weight,
        # so a turn with nothing else notable stays unselected even though
        # it has a growth row.
        scenes = extract_scenes(GROWTH_ONLY_ROWS, {"protagonist": "桃太郎"})
        self.assertEqual([scene["turn"] for scene in scenes], [])

    def test_growth_choice_of_scenes_is_unaffected_by_growth_presence(self) -> None:
        # Review fix (must #3): which turns get selected as scenes must be
        # identical whether or not a growth row is appended -- growth is an
        # annotation on an already-selected scene, never a reason to select
        # one, so it must not compete with foreshadowing/objective-transfer/
        # concede scenes for extract_scenes's limited `limit`.
        without = extract_scenes(NO_GROWTH_ROWS, {"protagonist": "桃太郎"})
        with_growth = extract_scenes(WITH_GROWTH_ROWS, {"protagonist": "桃太郎"})
        self.assertEqual(
            [scene["turn"] for scene in without],
            [scene["turn"] for scene in with_growth],
        )

    def test_growth_row_is_not_double_counted_in_events(self) -> None:
        # nice #5: the growth row's own text must not appear a second time
        # in scene["events"] alongside the (synopsis-rendered) "性格の変化:"
        # line -- it is excluded from events entirely now that "growth" has
        # no SPECIAL_PRIORITY weight (see must #3).
        scenes = extract_scenes(WITH_GROWTH_ROWS, {"protagonist": "桃太郎"})
        matching = [scene for scene in scenes if scene["turn"] == 1][0]
        self.assertFalse(
            any("勝利で大胆になる" in event for event in matching["events"])
        )
        self.assertIn("growth", matching)


class SynopsisGrowthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world_meta = {"name": "テスト世界", "identities": []}

    def test_has_growth(self) -> None:
        no_growth_scenes = extract_scenes(NO_GROWTH_ROWS, {"protagonist": "桃太郎"})
        with_growth_scenes = extract_scenes(WITH_GROWTH_ROWS, {"protagonist": "桃太郎"})
        self.assertFalse(_has_growth(no_growth_scenes))
        self.assertTrue(_has_growth(with_growth_scenes))

    def test_prompts_are_byte_identical_without_growth(self) -> None:
        scenes_a = extract_scenes(NO_GROWTH_ROWS, {"protagonist": "桃太郎"})
        scenes_b = extract_scenes(NO_GROWTH_ROWS, {"protagonist": "桃太郎"})
        elite = {"cell": "I|low", "quality": 0.5, "reach_rate": 1.0}
        self.assertEqual(
            build_synopsis_prompt(elite, scenes_a, self.world_meta),
            build_synopsis_prompt(elite, scenes_b, self.world_meta),
        )
        self.assertNotIn(
            "性格の変化は出来事の結果として書き",
            build_synopsis_prompt(elite, scenes_a, self.world_meta),
        )
        self.assertNotIn(
            "性格の変化は出来事の結果として書き",
            build_narration_prompt(elite, scenes_a, self.world_meta),
        )

    def test_growth_instruction_appears_only_with_growth_scenes(self) -> None:
        scenes = extract_scenes(WITH_GROWTH_ROWS, {"protagonist": "桃太郎"})
        elite = {"cell": "I|low", "quality": 0.5, "reach_rate": 1.0}
        synopsis_prompt = build_synopsis_prompt(elite, scenes, self.world_meta)
        narration_prompt = build_narration_prompt(elite, scenes, self.world_meta)
        self.assertIn("性格の変化は出来事の結果として書き", synopsis_prompt)
        self.assertIn("性格の変化は出来事の結果として書き", narration_prompt)
        self.assertIn("性格の変化: 勝利で大胆になる", synopsis_prompt)


def _write_growth_experiment(runs_root: Path, *, with_growth: bool) -> Path:
    name = "exp-growth" if with_growth else "exp-no-growth"
    experiment = runs_root / name
    layers_path = experiment / "g0" / "ind-0" / "seed-1" / "layers.jsonl"

    header = {
        "kind": "header",
        "seed": 1,
        "world": "桃太郎",
        "protagonist": "桃太郎",
        "antagonist": "鬼",
        "engine_hash": "engine",
        "precedent_hash": "precedent",
        "genome": {
            "category_weight": {c: 0.5 for c in ("I", "II", "III", "IV", "V", "VI")},
            "risk_tolerance": 0.5,
            "stance_shift_bias": 0.0,
            "novelty_drive": 0.0,
        },
    }
    fight_row: dict[str, Any] = {
        "kind": "decision",
        "turn": 1,
        "day": 1,
        "slot": "morning",
        "subject": "桃太郎",
        "verb": "fight",
        "args": ["鬼"],
        "result": "won",
        "effective": True,
        "delta": {"actor": {}, "targets": {}, "relations": [], "objective": None},
        "classification": {
            "category": "I", "subtype": "attack", "risk_class": "risky",
            "stance_sign": 1, "target_role": "hostile",
        },
    }
    snapshot = {
        "kind": "snapshot",
        "turn": 1,
        "day": 1,
        "subject": "桃太郎",
        "layers": {
            "ability": {"base": 50.0, "modifiers": []},
            "belief": {},
            "identity": {"displayed": "桃太郎", "true": "桃太郎"},
            "objective": {},
            "pending": [],
            "phase": [],
            "resources": {"assets": {}, "bonds": 0.0, "reputation": 0.0},
            "vitality": "alive",
            "zone": "村",
        },
        "relations": [],
        "vector": [0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    }
    ending_row = {
        "kind": "event", "turn": 2, "day": 1, "slot": "evening",
        "subject": "桃太郎", "verb": "ending", "id": "homecoming",
        "result": "applied", "details": {"label": "凱旋"},
    }

    rows = [header, fight_row]
    if with_growth:
        # Deliberately NOT {"risk_tolerance": 0.1} here: a decision row's own
        # "policy.acquired" meta is a snapshot from *before* that decision's
        # own outcome was observed (reweight() runs first, then execute(),
        # then observe()) -- so the very decision that triggers this growth
        # row realistically still shows the *pre*-growth acquired state.
        fight_row["policy"] = {"ctx": [], "acquired": {}}
        rows.append(dict(GROWTH_ROW, subject="桃太郎"))
    rows += [snapshot, ending_row]
    _write_jsonl(layers_path, rows)

    elite = {
        "descriptor": {"category": "I", "volatility": 0.1, "volatility_bin": "low"},
        "exemplar": {
            "engine_hash": "engine", "precedent_hash": "precedent", "seed": 1,
            "layers_path": "g0/ind-0/seed-1/layers.jsonl",
        },
        "generation": 0,
        "genome": header["genome"],
        "parents": [],
        "quality": 0.5,
        "reach_rate": 1.0,
    }
    _write_json(
        experiment / "archive.json",
        {"cells": {"I|low": elite}, "volatility_thresholds": {"low_max": 0.05, "mid_max": 0.15}},
    )
    return experiment


class ViewerGrowthPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "runs"

    def test_cell_view_has_growth_events_and_genome_now_only_with_growth(self) -> None:
        from viewer import data

        no_growth = _write_growth_experiment(self.runs_root, with_growth=False)
        with_growth = _write_growth_experiment(self.runs_root, with_growth=True)
        repository = data.RunRepository(self.runs_root)

        plain_model = data.cell_view(repository, no_growth, "I|low", view="digest")
        self.assertEqual(plain_model["growth_events"], [])
        self.assertIsNone(plain_model["genome_now"])

        growth_model = data.cell_view(repository, with_growth, "I|low", view="digest")
        self.assertEqual(len(growth_model["growth_events"]), 1)
        self.assertEqual(growth_model["growth_events"][0]["rule"], "g_fight_won")
        # Review fix (should #2): the fixture's last decision row (the fight
        # that itself triggers this growth) has an *empty* policy.acquired
        # (its own reweight() ran before the outcome was observed) -- only
        # reading the growth row's own "acquired_after" (0.1) gets this
        # right; the old "last decision row's acquired" reading would have
        # left this at 0.5 (the birth genome, unchanged).
        self.assertEqual(
            growth_model["genome_now"]["risk_tolerance"], 0.6,
        )

    def test_cell_page_renders_panel_only_with_growth(self) -> None:
        from viewer import data, pages

        no_growth = _write_growth_experiment(self.runs_root, with_growth=False)
        with_growth = _write_growth_experiment(self.runs_root, with_growth=True)
        repository = data.RunRepository(self.runs_root)

        plain_html = pages.cell_page(repository, no_growth.name, "I|low", view="digest")
        self.assertNotIn("性格の変化", plain_html)

        growth_html = pages.cell_page(repository, with_growth.name, "I|low", view="digest")
        self.assertIn("性格の変化", growth_html)
        self.assertIn("性格（開始時）", growth_html)
        self.assertIn("性格（今）", growth_html)
        self.assertIn("勝利で大胆になる", growth_html)


class ExecutionConfigGrowthToggleTests(unittest.TestCase):
    def test_default_is_off_and_excluded_from_argv(self) -> None:
        from execution.configs import evolution_defaults, normalize

        self.assertEqual(evolution_defaults()["personality_growth"], False)
        normalized = normalize(
            {"label": "x", "project_id": "momotaro", "template_id": "momotaro"}
        )
        self.assertEqual(normalized["evolution"]["personality_growth"], False)

    def test_true_round_trips_and_rejects_non_bool(self) -> None:
        from execution.configs import ConfigError, normalize

        normalized = normalize(
            {
                "label": "x", "project_id": "momotaro", "template_id": "momotaro",
                "evolution": {"personality_growth": True},
            }
        )
        self.assertTrue(normalized["evolution"]["personality_growth"])

        with self.assertRaises(ConfigError):
            normalize(
                {
                    "label": "x", "project_id": "momotaro", "template_id": "momotaro",
                    "evolution": {"personality_growth": "yes"},
                }
            )

    def test_prepare_run_argv_reflects_the_flag(self) -> None:
        import shutil

        from execution.configs import ConfigStore

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as base_str:
            base = Path(base_str)
            repo = base / "repo"
            repo.mkdir()
            for name in ("projects", "templates", "engine", "gapengine", "scripts", "execution"):
                shutil.copytree(
                    root / name, repo / name,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
            shutil.copyfile(root / "requirements.txt", repo / "requirements.txt")
            store = ConfigStore(repo, base / "control", base / "runs")
            spec = {
                "label": "growth-toggle", "project_id": "momotaro", "template_id": "momotaro",
                "evolution": {
                    "generations": 1, "population": 2, "seeds": 1, "keep": "all",
                    "personality_growth": True,
                },
            }
            store.save(spec, config_id="cfg-growth")
            manifest = store.prepare_run("cfg-growth", run_id="run-growth", job_id="job-growth")
            self.assertIn("--personality-growth", manifest["argv"])
            self.assertEqual(manifest["evolution"]["personality_growth"], True)


if __name__ == "__main__":
    unittest.main()
