"""End-to-end CLI acceptance tests for scripts/world_patch.py
(WB-WORLDGROW-001, stage 3b): propose/check/approve/reject/list against a
real (small, fast) experiment. No real LLM is ever called -- --from-file or a
stubbed gapengine.synopsis.generate_text stand in for the backend.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import yaml

import scripts.world_patch as wpc
from gapengine.evolve import evolve
from gapengine.synopsis import GenerationResult
from gapengine.world_patch import approved_patches
from world_patch_fixtures import frozen_experiment
from gapengine.world_patch import PatchError, read_stack
from execution.world_patch_approval import approve as approve_patch

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "momotaro"
TEMPLATE = ROOT / "templates" / "momotaro"

VALID_ADD = {
    # A1 needs a source directly in the trigger zone (海) itself; A2 needs
    # the added branch zone (船大工の小屋) to have one too. max:1 each (not
    # 2) keeps the two items' implicit give budget (no explicit `give` still
    # counts at the engine's default affinities x source max) within
    # MAX_GIVE_PER_PATCH.
    "zones": [{"name": "船大工の小屋", "parent": "海", "note": "船具を扱う小屋"}],
    "items": [
        {"name": "古びた帆布", "sources": [
            {"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 1}]},
        {"name": "潮見の貝殻", "sources": [
            {"type": "investigate", "zone": "海", "count": 1, "max": 1}]},
    ],
    "facts": [],
}
COLLIDING_ADD = {"zones": [{"name": "村", "parent": "海"}], "items": [], "facts": []}


def _make_fast_project(root: Path) -> Path:
    """A 1-day, guaranteed-homecoming momotaro copy -- keeps the fixture
    experiment's *own* evolve() run fast and non-flaky. (The propose/check
    commands under test still resolve the *real* projects/momotaro as the
    base world for gating/trial, per gapengine.lineage's legacy-run fallback
    when no manifest.json is present -- this fixture only needs to produce a
    valid, non-empty archive quickly.)"""
    project = root / "fast-project"
    shutil.copytree(PROJECT, project)
    world_path = project / "world.yaml"
    world_raw = yaml.safe_load(world_path.read_text(encoding="utf-8"))
    world_raw["time"] = {"days": 1, "slots": ["朝"]}
    world_raw["daily_events"] = None
    world_raw["scheduled_events"] = [{
        "id": "test_guaranteed_homecoming", "day": 1, "slot": "朝", "targets": ["桃太郎"],
        "label": "決定性テスト用の帰還", "grants_item": {"name": "鬼ヶ島の宝物", "count": 1}, "move_to": "村",
    }]
    world_path.write_text(yaml.safe_dump(world_raw, allow_unicode=True, sort_keys=False),
                           encoding="utf-8", newline="\n")
    return project


def _write_world_demand(experiment: Path) -> None:
    payload = {
        "schema_version": 1, "files": 1, "skipped_paths": 0, "subject_decisions": 30,
        "triggers": [{"zone": "海", "verb": "investigate", "count": 30, "whiffs": 30,
                       "whiff_rate": 1.0, "wasted_share": 0.3, "zone_dwell_share": 0.5}],
        "zones": [{"zone": "海", "decisions": 30, "dwell": 30, "dwell_share": 0.5,
                    "verbs": [["investigate", 30, 1.0]], "repeat_rate": 0.0, "ineffective_rate": 1.0,
                    "ineffective_reasons": [], "mean_p_prec": None, "mean_m_nov": None, "mean_candidates": None}],
        "archive": None,
        "thresholds": {"whiff_rate_min": 0.5, "wasted_share_min": 0.02, "whiffs_min": 10},
        "verb_counts": {"海": {"investigate": [30, 30]}},
    }
    (experiment / "world_demand.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_proposal_file(path: Path, *, add: dict | None = None, title: str = "海辺の船大工小屋") -> None:
    payload = {"title": title, "rationale": "海でのinvestigateが空振りし続けている",
               "add": VALID_ADD if add is None else add}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class WorldPatchCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._experiment_tmp = tempfile.TemporaryDirectory()
        root = Path(cls._experiment_tmp.name)
        cls.experiment, cls.source_project, cls.template = frozen_experiment(root)
        _write_world_demand(cls.experiment)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._experiment_tmp.cleanup()

    def setUp(self) -> None:
        self._scratch = tempfile.TemporaryDirectory()
        scratch = Path(self._scratch.name)
        self.project = scratch / "momotaro"
        shutil.copytree(self.source_project, self.project)
        # patches/ is untracked (git status confirms projects/momotaro/patches
        # is entirely `??`); a stray proposal left in the real momotaro project
        # by manual CLI use (or a prior test run against it) must not leak into
        # this scratch copy and break "exactly one proposed patch" assertions.
        patches_dir = self.project / "patches"
        if patches_dir.exists():
            shutil.rmtree(patches_dir)
        self.proposal_path = scratch / "proposal.json"

    def tearDown(self) -> None:
        self._scratch.cleanup()

    def _run(self, argv: list[str]) -> int:
        if argv[0] in ("propose", "check", "approve") and "--template" not in argv:
            argv += ["--template", str(self.template)]
        if argv[0] == "approve" and "--reason" not in argv:
            argv += ["--reason", "このテストの測定結果と契約検査を確認した"]
        args = wpc.build_parser().parse_args(argv)
        try:
            return args.func(args)
        except (ValueError, OSError, KeyError):
            return 1

    def _propose_from_file(self, *, add=None, extra: list[str] | None = None) -> int:
        _write_proposal_file(self.proposal_path, add=add)
        argv = ["propose", "--experiment", str(self.experiment), "--project", str(self.project),
                "--from-file", str(self.proposal_path)]
        # Keep trials tiny here: the gate's own arithmetic is covered in
        # test_world_patch_trial.py, and the default 5x8 reruns would add minutes.
        if "--skip-trial" not in (extra or []):
            argv += ["--max-runs", "5", "--seeds-per-run", "4"]
        return self._run(argv + (extra or []))

    def _proposed_files(self):
        return sorted((self.project / "patches" / "_proposed").glob("*.yaml"))

    def test_propose_from_file_creates_proposal_with_passing_gates_and_leaves_experiment_untouched(self):
        before = {p.relative_to(self.experiment): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.experiment.rglob("*") if p.is_file()}

        code = self._propose_from_file()

        after = {p.relative_to(self.experiment): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.experiment.rglob("*") if p.is_file()}
        self.assertEqual(before, after)  # no lineage/ cache, nothing written into the experiment

        proposed = self._proposed_files()
        self.assertEqual(len(proposed), 1)
        gate_path = proposed[0].with_name(proposed[0].stem + ".gate.json")
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        self.assertTrue(gate["static"]["passed"])
        trial = gate["trial"]
        self.assertIsNotNone(trial)
        self.assertGreaterEqual(trial["total_runs"], 1)
        self.assertEqual(trial["errors"], [])
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["status"], "reviewable")
        self.assertGreaterEqual(trial["reproduction"]["checked"], 1)
        self.assertEqual(trial["reproduction"]["checked"], trial["reproduction"]["identical"])
        self.assertEqual(code, 0)

    def test_propose_from_file_accepts_a_raw_indexed_blocked_trigger_with_reachable_coverage(self):
        # WB-WORLDGROW-002 S2: `--trigger` is now the raw index into
        # world_demand.json's triggers (any kind), and check_trigger_coverage
        # for a "blocked" trigger is checked against real engine reachability
        # (gapengine.world_patch_contract.reachable_zones_from) -- not just
        # substring/shape checks. 命綱 is a fictional item name (not momotaro's
        # own 縄, which already exists in the base world and so can never be
        # re-added by a patch); this only exercises the mechanism, not the
        # real momotaro/縄 blocker (out of scope here, see WB-ROUTE-003).
        original = (self.experiment / "world_demand.json").read_text(encoding="utf-8")
        blocked_payload = {
            "schema_version": 2, "files": 1, "skipped_paths": 0, "subject_decisions": 40,
            "triggers": [{
                "kind": "blocked", "requirement": "has_item:命綱", "count": 40, "share": 1.0,
                "lost_share": 0.5, "runs": 5, "reason": "sources_unreachable",
                "stuck_zones": [["道中", 40]], "source_zones": [["村", 40]], "held_by": [],
            }],
            "zones": [], "archive": None,
            "thresholds": {"whiff_rate_min": 0.5, "wasted_share_min": 0.02, "whiffs_min": 10,
                            "ignorance_min": 10, "ignorance_share_min": 0.3,
                            "blocked_min": 10, "blocked_share_min": 0.3, "blocked_runs_min": 3},
            "verb_counts": {}, "route_counts": {}, "blocked_counts": {},
        }
        blocked_add = {
            "zones": [],
            "items": [{"name": "命綱", "sources": [{"type": "investigate", "zone": "道中", "count": 1, "max": 2}]}],
            "facts": [],
        }
        try:
            (self.experiment / "world_demand.json").write_text(
                json.dumps(blocked_payload, ensure_ascii=False), encoding="utf-8")
            code = self._propose_from_file(add=blocked_add, extra=["--trigger", "0", "--skip-trial"])
        finally:
            (self.experiment / "world_demand.json").write_text(original, encoding="utf-8")

        proposed = self._proposed_files()
        self.assertEqual(len(proposed), 1)
        patch = yaml.safe_load(proposed[0].read_text(encoding="utf-8"))
        self.assertEqual(patch["trigger"], {"experiment": self.experiment.name, "kind": "blocked",
                                             "requirement": "has_item:命綱", "count": 40,
                                             "stuck_zones": [["道中", 40]]})
        gate = json.loads(proposed[0].with_name(proposed[0].stem + ".gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["static"]["violations"], [])
        self.assertTrue(gate["static"]["passed"])
        self.assertEqual(gate["status"], "trial_pending")
        self.assertEqual(code, 0)

    def test_propose_from_file_rejects_a_blocked_trigger_sourced_only_in_an_unreachable_zone(self):
        # Same setup, but the item's only source is 村 -- excluded from
        # 桃太郎's own range until he holds 鬼ヶ島の宝物 (projects/momotaro's
        # subjects/03_momotaro.yaml) -- must fail check_trigger_coverage even
        # though the item itself is perfectly valid on its own.
        original = (self.experiment / "world_demand.json").read_text(encoding="utf-8")
        blocked_payload = {
            "schema_version": 2, "files": 1, "skipped_paths": 0, "subject_decisions": 40,
            "triggers": [{
                "kind": "blocked", "requirement": "has_item:命綱", "count": 40, "share": 1.0,
                "lost_share": 0.5, "runs": 5, "reason": "sources_unreachable",
                "stuck_zones": [["道中", 40]], "source_zones": [["村", 40]], "held_by": [],
            }],
            "zones": [], "archive": None,
            "thresholds": {"whiff_rate_min": 0.5, "wasted_share_min": 0.02, "whiffs_min": 10,
                            "ignorance_min": 10, "ignorance_share_min": 0.3,
                            "blocked_min": 10, "blocked_share_min": 0.3, "blocked_runs_min": 3},
            "verb_counts": {}, "route_counts": {}, "blocked_counts": {},
        }
        blocked_add = {
            "zones": [],
            "items": [{"name": "命綱", "sources": [{"type": "investigate", "zone": "村", "count": 1, "max": 2}]}],
            "facts": [],
        }
        try:
            (self.experiment / "world_demand.json").write_text(
                json.dumps(blocked_payload, ensure_ascii=False), encoding="utf-8")
            code = self._propose_from_file(add=blocked_add, extra=["--trigger", "0", "--skip-trial"])
        finally:
            (self.experiment / "world_demand.json").write_text(original, encoding="utf-8")

        proposed = self._proposed_files()
        self.assertEqual(len(proposed), 1)
        gate = json.loads(proposed[0].with_name(proposed[0].stem + ".gate.json").read_text(encoding="utf-8"))
        self.assertIn("「命綱」を主人公が到達できる場所に足す入手手段がありません", gate["static"]["violations"])
        self.assertFalse(gate["static"]["passed"])
        self.assertEqual(gate["status"], "static_failed")
        self.assertEqual(code, 1)

    def test_propose_from_file_with_colliding_zone_fails_static_gate(self):
        self._propose_from_file(add=COLLIDING_ADD)

        proposed = self._proposed_files()
        self.assertEqual(len(proposed), 1)
        gate = json.loads(proposed[0].with_name(proposed[0].stem + ".gate.json").read_text(encoding="utf-8"))
        self.assertFalse(gate["passed"])
        self.assertTrue(gate["static"]["violations"])

    # -- B4: --job-less exit code is unchanged by stage 3b-3's --job-only
    # "0 once saved" override (execution/world_patch_job.py's job pipeline
    # is the only caller that ever passes --job). reviewable -> 0 without
    # --job is already covered by
    # test_propose_from_file_creates_proposal_with_passing_gates_and_leaves_experiment_untouched
    # (line ~170 above); static_failed -> 1 without --job was not previously
    # asserted anywhere, so it's added here.

    def test_propose_exit_code_without_job_is_1_for_static_failed(self):
        code = self._propose_from_file(add=COLLIDING_ADD)
        self.assertEqual(code, 1)
        gate = json.loads(self._proposed_files()[0].with_name(
            self._proposed_files()[0].stem + ".gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "static_failed")

    # -- B1-B3: --then-holdout ----------------------------------------------

    def test_propose_then_holdout_upgrades_a_reviewable_gate_to_holdout_evidence(self):
        code = self._propose_from_file(extra=["--then-holdout"])
        self.assertEqual(code, 0)
        proposed = self._proposed_files()
        self.assertEqual(len(proposed), 1)
        gate = json.loads(proposed[0].with_name(proposed[0].stem + ".gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "reviewable")
        self.assertIsNotNone(gate["trial"])
        self.assertEqual(gate["trial"]["evidence"]["seed_set"], "holdout")
        self.assertEqual(gate["holdout_checks"], 1)

    def test_propose_then_holdout_skips_holdout_when_static_gate_rejects(self):
        # --max-runs/--seeds-per-run are irrelevant here (a static rejection
        # never reaches the trial at all, exploration or holdout), so
        # _propose_from_file's default extra (added because "--skip-trial"
        # isn't in extra) is harmless.
        code = self._propose_from_file(add=COLLIDING_ADD, extra=["--then-holdout"])
        self.assertEqual(code, 1)
        proposed = self._proposed_files()
        self.assertEqual(len(proposed), 1)
        gate = json.loads(proposed[0].with_name(proposed[0].stem + ".gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "static_failed")
        self.assertIsNone(gate["trial"])
        self.assertEqual(gate.get("holdout_checks", 0), 0)

    def test_propose_then_holdout_with_skip_trial_never_runs_a_trial(self):
        code = self._propose_from_file(extra=["--then-holdout", "--skip-trial"])
        self.assertEqual(code, 0)
        proposed = self._proposed_files()
        self.assertEqual(len(proposed), 1)
        gate = json.loads(proposed[0].with_name(proposed[0].stem + ".gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "trial_pending")
        self.assertIsNone(gate["trial"])
        self.assertEqual(gate.get("holdout_checks", 0), 0)

    # -- B5: --job/--control given, but the job folder doesn't exist --------

    def test_progress_is_a_noop_when_the_job_folder_is_missing(self):
        control = Path(self._scratch.name) / "control"
        (control / "jobs").mkdir(parents=True)
        code = self._propose_from_file(extra=["--job", "job-does-not-exist", "--control", str(control)])
        # --job was given, so a successfully saved proposal returns 0
        # regardless of gate status (here: reviewable) -- _progress's own
        # failure (no such job folder) must not surface as a CLI failure.
        self.assertEqual(code, 0)
        self.assertEqual(len(self._proposed_files()), 1)

    def test_propose_retries_on_bad_json_then_succeeds(self):
        good_text = json.dumps(
            {"title": "海辺の船大工小屋", "rationale": "海でのinvestigateが空振りし続けている", "add": VALID_ADD},
            ensure_ascii=False,
        )
        responses = [GenerationResult(status="ok", text="これはJSONではありません"),
                     GenerationResult(status="ok", text=good_text)]
        prompts_seen: list[str] = []

        def fake_generate_text(backend, prompt, *, settings_path, timeout=600):
            prompts_seen.append(prompt)
            return responses[len(prompts_seen) - 1]

        with mock.patch("scripts.world_patch.generate_text", side_effect=fake_generate_text):
            code = self._run(["propose", "--experiment", str(self.experiment), "--project", str(self.project),
                               "--backend", "ollama", "--retries", "2", "--skip-trial"])

        self.assertEqual(len(prompts_seen), 2)
        self.assertIn("不採用でした", prompts_seen[1])
        self.assertEqual(code, 0)
        proposed = self._proposed_files()
        self.assertEqual(len(proposed), 1)
        patch = yaml.safe_load(proposed[0].read_text(encoding="utf-8"))
        self.assertEqual(patch["title"], "海辺の船大工小屋")

    def test_propose_reports_a_generation_failure_without_writing_anything(self):
        from gapengine.synopsis import GenerationError

        with mock.patch("scripts.world_patch.generate_text",
                        side_effect=GenerationError("GPU is busy: held by another-run")):
            code = self._run(["propose", "--experiment", str(self.experiment), "--project", str(self.project),
                               "--backend", "ollama", "--skip-trial"])

        self.assertEqual(code, 2)
        self.assertEqual(self._proposed_files(), [])

    def test_propose_backend_none_prints_prompt_only(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self._run(["propose", "--experiment", str(self.experiment), "--project", str(self.project),
                               "--backend", "none"])
        self.assertEqual(code, 0)
        self.assertIn("海", buffer.getvalue())
        self.assertEqual(self._proposed_files(), [])

    def test_propose_rejects_mismatched_parent_rev(self):
        # A patch staged as "already approved" in --project that the resolved
        # world's own expansion.patches never actually applied -- the
        # experiment was run against an earlier/different world version.
        patches_dir = self.project / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)
        stray = {
            "id": "p-stray001", "title": "t", "rationale": "r", "approved_seq": 1, "parent_rev": [],
            "trigger": {"zone": "海", "verb": "investigate", "count": 1, "whiffs": 1},
            "add": {"zones": [{"name": "余所のゾーン", "parent": "海"}], "items": [], "facts": []},
        }
        (patches_dir / "p-stray001.yaml").write_text(yaml.safe_dump(stray, allow_unicode=True), encoding="utf-8")

        code = self._run(["propose", "--experiment", str(self.experiment), "--project", str(self.project),
                           "--backend", "none"])
        self.assertEqual(code, 1)
        self.assertEqual(self._proposed_files(), [])

    def test_check_rewrites_gate_for_an_existing_proposal(self):
        self._propose_from_file(extra=["--skip-trial"])
        patch_id = self._proposed_files()[0].stem
        gate_path = self.project / "patches" / "_proposed" / f"{patch_id}.gate.json"
        self.assertIsNone(json.loads(gate_path.read_text(encoding="utf-8"))["trial"])

        code = self._run(["check", "--experiment", str(self.experiment), "--project", str(self.project),
                           "--patch", patch_id])
        self.assertEqual(code, 0)
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        self.assertIsNotNone(gate["trial"])
        self.assertGreaterEqual(gate["trial"]["runs"], 1)

    def test_propose_gate_fails_when_trial_fails(self):
        # A stubbed run_trial -- exercising the actual regression (base vs.
        # patched reach diverging beyond tolerance) would need a patch that
        # measurably hurts reach, which this fixture's data-only additions don't.
        stub_trial = {
            "schema_version": 1, "runs": 1, "skipped": 0, "reached_base": 5, "reached_patched": 0,
            "errors": [{"error": "contract failure"}], "trigger": None, "used_new": 0, "passed": False,
            "reasons": ["到達 5→0（8本中、許容差 2）"], "total_runs": 8, "seeds_per_run": 8,
            "tolerance": 2, "base_source": "repository", "easier": False,
        }
        with mock.patch("scripts.world_patch.run_trial", return_value=stub_trial):
            code = self._propose_from_file()

        self.assertEqual(code, 1)
        proposed = self._proposed_files()
        self.assertEqual(len(proposed), 1)
        gate = json.loads(proposed[0].with_name(proposed[0].stem + ".gate.json").read_text(encoding="utf-8"))
        self.assertFalse(gate["passed"])
        self.assertFalse(gate["trial"]["passed"])

        patch_id = proposed[0].stem
        approve_code = self._run(["approve", "--project", str(self.project), "--patch", patch_id])
        self.assertEqual(approve_code, 1)
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_moves_a_passing_patch_and_records_seq(self):
        self._propose_from_file()
        patch_id = self._proposed_files()[0].stem

        check = self._run(["check", "--experiment", str(self.experiment), "--project", str(self.project),
                           "--patch", patch_id, "--max-runs", "5", "--seeds-per-run", "4"])
        self.assertEqual(check, 0)
        code = self._run(["approve", "--project", str(self.project), "--patch", patch_id])

        self.assertEqual(code, 0)
        self.assertEqual(self._proposed_files(), [])
        approved = approved_patches(self.project)
        self.assertEqual([p["id"] for p in approved], [patch_id])
        self.assertEqual(read_stack(self.project)["revisions"][0]["rev"], 1)

        approved_patch_path = self.project / "patches" / f"{patch_id}.yaml"
        approved_gate = json.loads((self.project / "patches" / f"{patch_id}.gate.json").read_text(encoding="utf-8"))
        self.assertEqual(approved_gate["patch_sha256"],
                          hashlib.sha256(approved_patch_path.read_bytes()).hexdigest())

    def _holdout(self):
        self._propose_from_file(extra=["--skip-trial"])
        pid = self._proposed_files()[0].stem
        self.assertEqual(self._run(["check", "--experiment", str(self.experiment),
            "--project", str(self.project), "--patch", pid, "--max-runs", "5", "--seeds-per-run", "4"]), 0)
        return pid

    def test_concurrent_approval_publishes_once(self):
        pid = self._holdout()
        def attempt():
            try:
                approve_patch(self.project, self.template, pid, "測定結果を確認しテストとして承認する")
                return True
            except PatchError:
                # R5: the loser here races the winner's os.replace() of this
                # same _proposed/<id>.yaml -- a plain FileNotFoundError from
                # that race must not leak past approve() as a bare OSError.
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(read_stack(self.project)["revisions"]), 1)

    def test_approval_requires_current_inputs_evidence_and_human_reason(self):
        pid = self._holdout()
        gate_path = self.project / "patches/_proposed" / f"{pid}.gate.json"
        original_gate = gate_path.read_bytes()
        for reason in ("", "short"):
            with self.subTest(reason=reason), self.assertRaises(PatchError):
                approve_patch(self.project, self.template, pid, reason)
        for change in ("trial_pending", "reference_only", "contract_failed", "insufficient"):
            gate = json.loads(original_gate)
            gate["status"] = change
            gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
            with self.subTest(status=change), self.assertRaises(PatchError):
                approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        for field in ("base_inputs_digest", "patched_inputs_digest", "runtime_digest", "target_ending", "trial_rules_version", "patch_rules_version"):
            gate = json.loads(original_gate)
            gate["trial"]["evidence"][field] = "changed"
            gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(PatchError):
                approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        gate_path.write_bytes(original_gate)
        for path in (self.project / "world.yaml", next((self.project / "subjects").glob("*.yaml"))):
            original = path.read_bytes()
            try:
                data = yaml.safe_load(original)
                data["note"] = "input changed"
                path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
                with self.subTest(path=path), self.assertRaises(PatchError):
                    approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
            finally:
                path.write_bytes(original)

    def test_approve_rejects_a_gate_whose_status_was_rewritten_to_hide_too_few_individuals(self):
        # R1: gate_status/trial_state must derive "insufficient" from
        # len(evidence["individuals"]), not the self-reported trial["runs"]
        # counter or the gate's own stored status -- both of those a
        # rewritten gate.json could set independently of what evidence was
        # actually recorded.
        pid = self._holdout()
        gate_path = self.project / "patches/_proposed" / f"{pid}.gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(gate["trial"]["evidence"]["individuals"]), 3)
        gate["trial"]["evidence"]["individuals"] = gate["trial"]["evidence"]["individuals"][:1]
        gate["trial"]["runs"] = 999  # self-reported counter, left untouched
        gate["status"] = "reviewable"  # stored status, left untouched
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_rejects_individuals_padded_past_what_the_archive_backs(self):
        # R1 continued: even once the derived status is legitimately
        # "reviewable" (padding real individuals up, rather than truncating
        # them), approve()'s existing per-individual archive/genome-hash
        # cross-check must still catch a fabricated entry.
        pid = self._holdout()
        gate_path = self.project / "patches/_proposed" / f"{pid}.gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        individuals = gate["trial"]["evidence"]["individuals"]
        forged = dict(individuals[0], genome_sha256="0" * 64)
        gate["trial"]["evidence"]["individuals"] = individuals + [forged]
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_rejects_a_gate_that_triples_the_same_individual_to_fake_a_minimum(self):
        # N1 (WB-WORLDGROW-001, Astra review): the same real (generation,
        # index) row repeated 3x (with the status forced back to
        # "reviewable") must not count as 3 distinct individuals.
        pid = self._holdout()
        gate_path = self.project / "patches/_proposed" / f"{pid}.gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        real = gate["trial"]["evidence"]["individuals"][0]
        gate["trial"]["evidence"]["individuals"] = [dict(real) for _ in range(3)]
        gate["status"] = "reviewable"
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_rejects_the_same_cell_relabeled_to_fabricated_indices(self):
        # N1 continued: relabeling one real row's index to 999/1000/1001
        # (keeping the same real cell, so it dedups as 3 *distinct*
        # individuals and reaches "reviewable") must still be caught --
        # each fabricated index is cross-checked against the exemplar's
        # actual index encoded in the archive's own layers_path.
        pid = self._holdout()
        gate_path = self.project / "patches/_proposed" / f"{pid}.gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        real = gate["trial"]["evidence"]["individuals"][0]
        gate["trial"]["evidence"]["individuals"] = [dict(real, index=999 + n) for n in range(3)]
        gate["status"] = "reviewable"
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_rejects_an_individual_referencing_a_cell_not_in_the_archive(self):
        pid = self._holdout()
        gate_path = self.project / "patches/_proposed" / f"{pid}.gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        individuals = gate["trial"]["evidence"]["individuals"]
        forged = dict(individuals[0], cell="no-such-cell")
        gate["trial"]["evidence"]["individuals"] = individuals + [forged]
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_rejects_pairs_missing_a_row_for_a_verified_individual(self):
        pid = self._holdout()
        gate_path = self.project / "patches/_proposed" / f"{pid}.gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        gate["trial"]["pairs"].pop()
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_rejects_pairs_with_a_duplicated_row(self):
        pid = self._holdout()
        gate_path = self.project / "patches/_proposed" / f"{pid}.gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        gate["trial"]["pairs"].append(gate["trial"]["pairs"][0])
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_rejects_a_gate_whose_archive_was_rewritten_after_check(self):
        # N1: evidence["archive_sha256"] (sealed by run_trial) must be
        # re-verified against the experiment's *current* archive.json, not
        # just trusted.
        pid = self._holdout()
        archive_path = self.experiment / "archive.json"
        original = archive_path.read_bytes()
        try:
            archive_path.write_bytes(original + b" ")
            with self.assertRaises(PatchError):
                approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
            self.assertEqual(approved_patches(self.project), [])
        finally:
            archive_path.write_bytes(original)

    def test_holdout_counter_and_exploration_cannot_be_approved(self):
        pid = self._holdout()
        gate_path = self.project / "patches/_proposed" / f"{pid}.gate.json"
        self.assertEqual(json.loads(gate_path.read_text(encoding="utf-8"))["holdout_checks"], 1)
        self.assertEqual(self._run(["check", "--experiment", str(self.experiment), "--project", str(self.project),
            "--patch", pid, "--max-runs", "3", "--seeds-per-run", "4"]), 0)
        self.assertEqual(json.loads(gate_path.read_text(encoding="utf-8"))["holdout_checks"], 2)
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        gate["trial"]["evidence"]["seed_set"] = "exploration"
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")

    def test_partial_publish_recovers_without_touching_an_approved_revision(self):
        from execution.world_patch_approval import repair
        pid = self._holdout()
        replace = os.replace
        def fail_gate_move(source, destination):
            source, destination = Path(source), Path(destination)
            if source.name == pid + ".gate.json" and source.parent.name == "_proposed":
                raise OSError("simulated interruption after yaml move")
            return replace(source, destination)
        with mock.patch("execution.world_patch_approval.os.replace", side_effect=fail_gate_move), self.assertRaises(OSError):
            approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        self.assertEqual(repair(self.project), [pid + ".yaml"])
        revision = approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        self.assertEqual(revision["rev"], 1)
        self.assertEqual(len(approved_patches(self.project)), 1)

    def test_approve_rejects_a_failed_gate(self):
        self._propose_from_file(add=COLLIDING_ADD)
        patch_id = self._proposed_files()[0].stem

        code = self._run(["approve", "--project", str(self.project), "--patch", patch_id])

        self.assertEqual(code, 1)
        self.assertEqual(len(self._proposed_files()), 1)
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_rejects_when_engine_construction_fails_after_all_static_checks_pass(self):
        # Optional (WB-WORLDGROW-001 review): a patch that passes every
        # static/archive/pairs check but fails at the engine construction +
        # subject bind step (the last thing approve() does before writing
        # anything) must not publish. No natural input reaches this --
        # World.from_yaml is stubbed to raise instead.
        pid = self._holdout()
        with mock.patch("execution.world_patch_approval.World.from_yaml", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                approve_patch(self.project, self.template, pid, "測定値と契約検査の結果を確認した")
        self.assertEqual(approved_patches(self.project), [])
        self.assertTrue((self.project / "patches/_proposed" / f"{pid}.yaml").is_file())
        self.assertFalse((self.project / "patches/stack.json").is_file())

    def test_approve_rejects_a_patch_tampered_after_check(self):
        self._propose_from_file()
        patch_id = self._proposed_files()[0].stem
        patch_path = self.project / "patches" / "_proposed" / f"{patch_id}.yaml"
        patch = yaml.safe_load(patch_path.read_text(encoding="utf-8"))
        patch["add"]["items"].append({
            "name": "こっそり足したアイテム",
            "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}],
        })
        patch_path.write_text(yaml.safe_dump(patch, allow_unicode=True, sort_keys=False), encoding="utf-8")

        code = self._run(["approve", "--project", str(self.project), "--patch", patch_id])
        self.assertEqual(code, 1)
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_rejects_when_internal_id_does_not_match_filename(self):
        self._propose_from_file()
        patch_id = self._proposed_files()[0].stem
        patch_path = self.project / "patches" / "_proposed" / f"{patch_id}.yaml"
        gate_path = self.project / "patches" / "_proposed" / f"{patch_id}.gate.json"
        patch = yaml.safe_load(patch_path.read_text(encoding="utf-8"))
        patch["id"] = "p-notthesameid"
        patch_path.write_text(yaml.safe_dump(patch, allow_unicode=True, sort_keys=False), encoding="utf-8")
        # Keep the gate's recorded hash in sync with the tampered file, so
        # this isolates the id/filename check from the sha256 check.
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        gate["patch_sha256"] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")

        code = self._run(["approve", "--project", str(self.project), "--patch", patch_id])
        self.assertEqual(code, 1)
        self.assertEqual(approved_patches(self.project), [])

    def test_approve_rejects_a_path_traversal_patch_id(self):
        code = self._run(["approve", "--project", str(self.project), "--patch", "../x"])
        self.assertEqual(code, 1)

    def test_check_rejects_a_path_traversal_patch_id(self):
        code = self._run(["check", "--experiment", str(self.experiment), "--project", str(self.project),
                           "--patch", "../x"])
        self.assertEqual(code, 1)

    def test_approve_rejects_item_name_colliding_with_a_subject_id(self):
        add = {
            "zones": [], "facts": [],
            "items": [{"name": "桃太郎", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 1}]}],
        }
        self._propose_from_file(add=add, extra=["--skip-trial"])
        patch_id = self._proposed_files()[0].stem
        gate_path = self.project / "patches" / "_proposed" / f"{patch_id}.gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        # Simulate a gate that (wrongly) passed this -- approve's own
        # revalidation against the current world + subject ids is the backstop.
        gate["passed"] = True
        gate["static"] = {"passed": True, "violations": []}
        gate_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")

        code = self._run(["approve", "--project", str(self.project), "--patch", patch_id])
        self.assertEqual(code, 1)
        self.assertEqual(approved_patches(self.project), [])

    def test_reject_moves_both_files_to_rejected(self):
        self._propose_from_file()
        patch_id = self._proposed_files()[0].stem

        code = self._run(["reject", "--project", str(self.project), "--patch", patch_id])

        self.assertEqual(code, 0)
        rejected_dir = self.project / "patches" / "_rejected"
        self.assertTrue((rejected_dir / f"{patch_id}.yaml").is_file())
        self.assertTrue((rejected_dir / f"{patch_id}.gate.json").is_file())
        self.assertEqual(self._proposed_files(), [])

    def test_give_available_true_for_momotaro_false_for_detective(self):
        self.assertTrue(wpc._give_available(PROJECT / "subjects"))
        self.assertFalse(wpc._give_available(ROOT / "projects" / "detective" / "subjects"))

    def test_list_shows_approved_and_proposed_without_crashing(self):
        self._propose_from_file()
        patch_id = self._proposed_files()[0].stem

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self._run(["list", "--project", str(self.project)])

        self.assertEqual(code, 0)
        self.assertIn(patch_id, buffer.getvalue())
        self.assertIn("承認済み", buffer.getvalue())
        self.assertIn("提案中", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
