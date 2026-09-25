"""Phase 3 D8 output-stage tests; all LLM calls use backend none."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "momotaro"
TEMPLATE = ROOT / "templates" / "momotaro"

from gapengine.genome import Genome
from gapengine.qd import Archive, Descriptor, Elite
from gapengine.scenes import extract_scenes
import gapengine.synopsis as synopsis_module
from gapengine.synopsis import (
    build_narration_prompt,
    build_synopsis_prompt,
    load_world_meta,
)
from scripts.narrate import main as narrate_main
from scripts.synopsize import main as synopsize_main


def _snapshot(turn: int, vector: list[float]) -> dict[str, object]:
    return {
        "kind": "snapshot",
        "turn": turn,
        "day": turn,
        "subject": "桃太郎",
        "vector": vector,
        "layers": {
            "ability": {
                "base": 50.0 + turn,
                "modifiers": [],
            },
            "belief": {
                "鬼": {
                    "base_estimate": 80.0,
                    "identity_seen": False,
                    "known_modifiers": [],
                }
            },
            "identity": {
                "true": "桃から生まれた者",
                "displayed": "桃太郎",
            },
            "objective": {
                "鬼ヶ島の宝物": (
                    "桃太郎" if turn >= 3 else "鬼"
                )
            },
            "pending": [],
            "phase": ["出発"],
            "resources": {
                "assets": {},
                "bonds": 0.0,
                "reputation": 0.0,
            },
            "vitality": "alive",
            "zone": "鬼ヶ島" if turn >= 2 else "道中",
        },
        "relations": [
            {
                "observer": "桃太郎",
                "target": "鬼",
                "affinity": -0.5,
                "awareness": 1.0,
            }
        ],
    }


def _rows() -> list[dict[str, object]]:
    return [
        {
            "kind": "header",
            "world": "桃太郎",
            "protagonist": "桃太郎",
            "antagonist": "鬼",
            "seed": 7,
        },
        _snapshot(1, [0.0, 0.0]),
        {
            "kind": "decision",
            "turn": 2,
            "day": 2,
            "slot": "朝",
            "subject": "桃太郎",
            "verb": "observe",
            "args": ["鬼"],
            "result": "observed",
            "effective": True,
            "delta": {
                "actor": {"belief": {"鬼": {}}},
                "targets": {},
                "relations": [],
                "objective": None,
            },
            "details": {
                "believed_diff": -25.0,
            },
        },
        _snapshot(2, [1.0, 0.0]),
        {
            "kind": "event",
            "turn": 3,
            "day": 3,
            "slot": "昼",
            "subject": "桃太郎",
            "verb": "planted",
            "args": [],
            "result": "applied",
            "delta": {
                "actor": {},
                "targets": {},
                "relations": [],
                "objective": None,
            },
            "details": {
                "effect_id": "oni_gap:鬼",
                "library_id": "oni_gap",
                "target": "鬼",
                "mode": "chosen",
            },
        },
        {
            "kind": "decision",
            "turn": 3,
            "day": 3,
            "slot": "昼",
            "subject": "桃太郎",
            "verb": "fight",
            "args": ["鬼"],
            "result": "won",
            "effective": True,
            "delta": {
                "actor": {},
                "targets": {},
                "relations": [],
                "objective": {
                    "鬼ヶ島の宝物": "桃太郎",
                },
            },
            "details": {},
        },
        _snapshot(3, [1.0, 1.0]),
        {
            "kind": "event",
            "turn": 4,
            "day": 4,
            "slot": "夜",
            "subject": "桃太郎",
            "verb": "ending",
            "id": "homecoming",
            "args": [],
            "result": "applied",
            "delta": {
                "actor": {},
                "targets": {},
                "relations": [],
                "objective": {
                    "鬼ヶ島の宝物": "村",
                },
            },
            "details": {
                "label": "鬼退治を果たし、宝を村へ持ち帰った",
            },
        },
    ]


def _write_rows(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in _rows()
        ),
        encoding="utf-8",
        newline="\n",
    )


def _write_archive(path: Path, layers_path: str) -> None:
    archive = Archive()
    archive.volatility_thresholds = {
        "low_max": 0.1,
        "mid_max": 0.2,
    }
    archive.cells[("III", "high")] = Elite(
        genome=Genome.neutral(),
        quality=0.75,
        descriptor=Descriptor(
            category="III",
            volatility=0.4,
            volatility_bin="high",
        ),
        reach_rate=1.0,
        exemplar={
            "engine_hash": "test",
            "layers_path": layers_path,
            "precedent_hash": "test",
            "seed": 7,
        },
        generation=3,
    )
    archive.save(path)


class OutputStageTests(unittest.TestCase):
    def test_scene_extraction_merges_turns_and_is_deterministic(
        self,
    ) -> None:
        world_meta = load_world_meta(PROJECT, TEMPLATE)
        first = extract_scenes(_rows(), world_meta)
        second = extract_scenes(_rows(), world_meta)

        self.assertEqual(first, second)
        self.assertEqual(
            [scene["turn"] for scene in first],
            [2, 3, 4],
        )
        turn_three = next(
            scene for scene in first if scene["turn"] == 3
        )
        self.assertIn("目的物の所持者交代", turn_three["reasons"])
        self.assertEqual(
            turn_three["foreshadowing"],
            ["金棒の隙を見切って仲間に合図を送る"],
        )
        self.assertEqual(
            turn_three["state"]["holder"]["鬼ヶ島の宝物"],
            "桃太郎",
        )

    def test_prompts_are_deterministic_and_natural_language_only(
        self,
    ) -> None:
        world_meta = load_world_meta(PROJECT, TEMPLATE)
        scenes = extract_scenes(_rows(), world_meta)
        elite = {
            "cell": "III|high",
            "quality": 0.75,
            "reach_rate": 1.0,
        }

        synopsis = build_synopsis_prompt(
            elite,
            scenes,
            world_meta,
        )
        narration = build_narration_prompt(
            elite,
            scenes,
            world_meta,
            synopsis="桃太郎は鬼を観察し、宝を取り戻す。",
        )

        self.assertEqual(
            synopsis,
            build_synopsis_prompt(elite, scenes, world_meta),
        )
        self.assertIn("200〜300字", synopsis)
        self.assertIn("2,000〜4,000字", narration)
        self.assertIn(
            "金棒の隙を見切って仲間に合図を送る",
            narration,
        )
        for predicate in (
            "stance(",
            "holder(",
            "strength(",
            "believed_strength(",
            "vitality(",
            "condition:",
        ):
            self.assertNotIn(predicate, synopsis)
            self.assertNotIn(predicate, narration)

    def test_cli_backends_pass_full_prompt_on_stdin_from_temp_cwd(
        self,
    ) -> None:
        prompt = (
            "あなたは物語のあらすじ作家です。\n"
            "第1の転機です。\n"
            "第2の転機です。\n"
            "本文のみを出力してください。\n"
        )
        observed_cwds: list[Path] = []
        observed_commands: list[list[str]] = []

        def fake_run(
            command: list[str],
            **kwargs: object,
        ) -> mock.Mock:
            cwd = Path(str(kwargs["cwd"])).resolve()
            observed_cwds.append(cwd)
            observed_commands.append(list(command))

            self.assertEqual(kwargs["input"], prompt)
            self.assertEqual(kwargs["encoding"], "utf-8")
            self.assertEqual(kwargs["errors"], "replace")
            self.assertTrue(kwargs["capture_output"])
            self.assertTrue(cwd.is_dir())
            self.assertNotEqual(cwd, ROOT.resolve())
            self.assertNotIn(ROOT.resolve(), cwd.parents)
            self.assertFalse((cwd / "CLAUDE.md").exists())
            self.assertFalse((cwd / ".git").exists())
            self.assertNotIn(prompt, command)

            return mock.Mock(
                returncode=0,
                stdout="桃太郎は旅に出て、鬼を退けて帰郷した。",
                stderr="",
            )

        command_paths = {
            "claude": "C:\\tools\\claude.CMD",
            "codex": "C:\\tools\\codex.CMD",
        }

        with (
            mock.patch(
                "gapengine.synopsis.shutil.which",
                side_effect=lambda command: command_paths[command],
            ),
            mock.patch(
                "gapengine.synopsis.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            claude_result = synopsis_module.generate_text(
                "claude-cli",
                prompt,
                settings_path=ROOT / "missing-settings.json",
            )
            codex_result = synopsis_module.generate_text(
                "codex-cli",
                prompt,
                settings_path=ROOT / "missing-settings.json",
            )

        self.assertEqual(claude_result.status, "ok")
        self.assertEqual(codex_result.status, "ok")
        self.assertEqual(
            observed_commands,
            [
                ["C:\\tools\\claude.CMD", "-p", "--model", "claude-sonnet-5"],
                [
                    "C:\\tools\\codex.CMD",
                    "exec",
                    "--skip-git-repo-check",
                    "-s",
                    "read-only",
                    "-",
                ],
            ],
        )
        self.assertEqual(len(observed_cwds), 2)
        for cwd in observed_cwds:
            self.assertFalse(cwd.exists())

    def test_clarification_responses_are_rejected(
        self,
    ) -> None:
        clarification_responses = (
            "",
            "もう少し具体的に教えてください。",
            "要件を確認させてください。",
            "どの物語を書けばよいですか?",
            "一つ質問があります。\n主人公は誰ですか？",
        )

        for response in clarification_responses:
            with self.subTest(response=response):
                with self.assertRaisesRegex(
                    synopsis_module.GenerationError,
                    "^clarification_request$",
                ):
                    synopsis_module._validate_response(response)

        self.assertEqual(
            synopsis_module._validate_response(
                "桃太郎は仲間と鬼ヶ島へ渡り、鬼を退けて宝を村へ持ち帰った。"
            ),
            "桃太郎は仲間と鬼ヶ島へ渡り、鬼を退けて宝を村へ持ち帰った。",
        )

    def test_none_backend_generates_prompts_without_llm(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "archive.json"
            layers_path = root / "g0" / "ind-0" / "seed-7" / "layers.jsonl"
            synopses_path = root / "synopses.json"
            selection_path = root / "selection.json"
            stories_dir = root / "stories"

            _write_rows(layers_path)
            _write_archive(
                archive_path,
                "g0/ind-0/seed-7/layers.jsonl",
            )
            selection_path.write_text(
                json.dumps(
                    {"selected": ["III|high"]},
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )

            with mock.patch(
                "gapengine.synopsis.subprocess.run",
                side_effect=AssertionError("LLM must not run"),
            ):
                self.assertEqual(
                    synopsize_main(
                        [
                            "--archive",
                            str(archive_path),
                            "--runs",
                            str(root),
                            "--out",
                            str(synopses_path),
                            "--backend",
                            "none",
                            "--project",
                            str(PROJECT),
                            "--template",
                            str(TEMPLATE),
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    narrate_main(
                        [
                            "--archive",
                            str(archive_path),
                            "--runs",
                            str(root),
                            "--selection",
                            str(selection_path),
                            "--synopses",
                            str(synopses_path),
                            "--out",
                            str(stories_dir),
                            "--backend",
                            "none",
                            "--project",
                            str(PROJECT),
                            "--template",
                            str(TEMPLATE),
                        ]
                    ),
                    0,
                )

            synopses = json.loads(
                synopses_path.read_text(encoding="utf-8")
            )
            story_index = json.loads(
                (stories_dir / "index.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                synopses["entries"][0]["status"],
                "prompt_only",
            )
            self.assertEqual(
                story_index["entries"][0]["status"],
                "prompt_only",
            )
            self.assertTrue(
                (root / "prompts" / "synopsis-III-high.txt").is_file()
            )
            self.assertTrue(
                (root / "prompts" / "narration-III-high.txt").is_file()
            )
            self.assertFalse((stories_dir / "III-high.md").exists())


class RouteMotiveTests(unittest.TestCase):
    """WB-ROUTE-001 S3: policy.route -> scenes.py "motives" ->
    synopsis.py's "行動の理由" lines and prompt instruction."""

    def _rows_with_route(self) -> list[dict[str, object]]:
        rows = json.loads(json.dumps(_rows()))
        for row in rows:
            if row.get("kind") != "decision":
                continue
            if row["turn"] == 2:  # the "observe" decision (advance)
                row["policy"] = {
                    "route": {
                        "kind": "advance",
                        "cause": None,
                        "h": [1.0, 0.0],
                        "plan": None,
                        "milestone": None,
                        "text": "鬼の正体を探るため観察した",
                    }
                }
            elif row["turn"] == 3:  # the "fight" decision (detour/none)
                row["policy"] = {
                    "route": {
                        "kind": "detour",
                        "cause": "none",
                        "h": [1.0, 1.0],
                        "plan": None,
                        "milestone": None,
                        "text": "特に理由のない寄り道",
                    }
                }
        return rows

    def test_motives_added_only_for_protagonist_decisions_with_route(
        self,
    ) -> None:
        world_meta = load_world_meta(PROJECT, TEMPLATE)
        scenes = extract_scenes(self._rows_with_route(), world_meta)

        turn_two = next(scene for scene in scenes if scene["turn"] == 2)
        self.assertEqual(len(turn_two["motives"]), 1)
        self.assertEqual(turn_two["motives"][0]["kind"], "advance")
        self.assertEqual(turn_two["motives"][0]["cause"], None)
        self.assertEqual(
            turn_two["motives"][0]["why"],
            "鬼の正体を探るため観察した",
        )

        turn_three = next(scene for scene in scenes if scene["turn"] == 3)
        motive = next(
            m for m in turn_three["motives"] if m["kind"] == "detour"
        )
        self.assertEqual(motive["cause"], "none")
        self.assertIsNone(motive["why"])

        # Turn 4 (the ending event, no decision row at all) never gets a
        # "motives" key -- not even an empty list.
        turn_four = next(scene for scene in scenes if scene["turn"] == 4)
        self.assertNotIn("motives", turn_four)

    def test_lost_kind_gets_a_fixed_reason_regardless_of_route_text(
        self,
    ) -> None:
        rows = self._rows_with_route()
        for row in rows:
            if row.get("kind") == "decision" and row["turn"] == 2:
                row["policy"]["route"] = {
                    "kind": "lost",
                    "cause": "unreachable",
                    "h": [None, None],
                    "plan": None,
                    "milestone": None,
                    "text": "この文字列は使われない",
                }
        world_meta = load_world_meta(PROJECT, TEMPLATE)
        scenes = extract_scenes(rows, world_meta)
        turn_two = next(scene for scene in scenes if scene["turn"] == 2)
        self.assertEqual(
            turn_two["motives"][0]["why"],
            "先の見通しが立たないまま動いた",
        )

    def test_lost_with_body_or_belief_cause_uses_route_text(self) -> None:
        """S3.5 §3: lost is no longer an automatic override -- body/belief
        causes keep their own recorded route.text (a fatigued or mistaken
        wander still has a real reason), only other causes (e.g. plain
        "unreachable") fall back to the fixed lost text above."""

        for cause, text in (
            ("body", "疲れが溜まっていたので休んだ"),
            ("belief", "誤った思い込みに基づいて動いた"),
        ):
            rows = self._rows_with_route()
            for row in rows:
                if row.get("kind") == "decision" and row["turn"] == 2:
                    row["policy"]["route"] = {
                        "kind": "lost",
                        "cause": cause,
                        "h": [None, None],
                        "plan": None,
                        "milestone": None,
                        "text": text,
                    }
            world_meta = load_world_meta(PROJECT, TEMPLATE)
            scenes = extract_scenes(rows, world_meta)
            turn_two = next(scene for scene in scenes if scene["turn"] == 2)
            self.assertEqual(turn_two["motives"][0]["why"], text)

    def test_antagonist_decision_with_route_is_not_a_motive(self) -> None:
        rows = self._rows_with_route()
        for row in rows:
            if row.get("kind") == "decision" and row["turn"] == 2:
                row["subject"] = "鬼"
        world_meta = load_world_meta(PROJECT, TEMPLATE)
        scenes = extract_scenes(rows, world_meta)
        turn_two = next(
            (scene for scene in scenes if scene["turn"] == 2), None
        )
        if turn_two is not None:
            self.assertNotIn("motives", turn_two)

    def test_old_runs_without_policy_are_byte_identical(self) -> None:
        world_meta = load_world_meta(PROJECT, TEMPLATE)
        old_scenes = extract_scenes(_rows(), world_meta)
        for scene in old_scenes:
            self.assertNotIn("motives", scene)

        elite = {"cell": "III|high", "quality": 0.75, "reach_rate": 1.0}
        synopsis = build_synopsis_prompt(elite, old_scenes, world_meta)
        narration = build_narration_prompt(
            elite, old_scenes, world_meta, synopsis="桃太郎は鬼を退けた。"
        )
        self.assertNotIn("行動の理由", synopsis)
        self.assertNotIn("行動の理由", narration)

    def test_scene_lines_render_reason_and_no_reason_rows(self) -> None:
        world_meta = load_world_meta(PROJECT, TEMPLATE)
        scenes = extract_scenes(self._rows_with_route(), world_meta)

        synopsis = build_synopsis_prompt(
            {"cell": "x", "quality": 1, "reach_rate": 1.0}, scenes, world_meta
        )
        self.assertIn(
            "行動の理由: 桃太郎が（鬼）観察した。結果は観察に成功した — "
            "鬼の正体を探るため観察した",
            synopsis,
        )
        self.assertIn(
            "行動の理由: 桃太郎が（鬼）戦った。結果は勝った。鬼ヶ島の宝物"
            "の新しい所持先は桃太郎 — （はっきりした理由は記録されていない）",
            synopsis,
        )
        self.assertIn(
            "「行動の理由」が示された行動は、その理由に沿って因果を書く",
            synopsis,
        )
        self.assertIn(
            "「行動の理由」は本人の意図であって、その結果ではない",
            synopsis,
        )

        narration = build_narration_prompt(
            {"cell": "x", "quality": 1, "reach_rate": 1.0},
            scenes,
            world_meta,
            synopsis="桃太郎は鬼を退けた。",
        )
        self.assertIn(
            "理由は心理描写や会話に翻訳してよいが、理由そのものを別の動機に"
            "置き換えない。",
            narration,
        )
        self.assertIn(
            "「行動の理由」は本人の意図であって、その結果ではない",
            narration,
        )


if __name__ == "__main__":
    unittest.main()