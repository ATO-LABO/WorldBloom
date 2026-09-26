"""execution/epoch_chain.py (WB-WORLDGROW-001 段階5c-1「エポック連鎖」): run ->
propose -> approve -> retire, ticked one step at a time. Orchestration tests
use a lightweight FakeJobStore (real ConfigStore underneath, so config
save/normalize -- including growth -- is exercised for real); a job's own
GA/propose subprocess is never actually run here, its outcome is injected
directly the way tests/test_workbench_pages.py's FakeJobStore lets a caller
`add()` a job in whatever state a test needs. The one exception is
test_auto_retire_uses_published_only_archive_fixture, which calls
EpochChain._auto_retire() against a hand-built published-only archive (no
top-level archive.json) the way tests/test_world_patch_usage.py does, per
the WB-WORLDGROW-001 段階5c-1 acceptance criteria.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import yaml

from execution.configs import ConfigStore
from execution.epoch_chain import IDLE_EPOCHS, EpochChain
from execution.provenance import ConfigError
from execution.worker import TERMINAL
from gapengine.world_patch import approved_patches
from viewer.data import RunRepository
from viewer.server import ViewerHandler, ViewerServer
from world_patch_fixtures import write_approved

ROOT = Path(__file__).resolve().parents[1]

_NO_MOVE_ROWS = [{"kind": "decision", "subject": "桃太郎", "verb": "rest", "result": "rested"}]


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


class FakeJobStore:
    """Duck-typed job store: enough of execution.jobs.JobStore for
    EpochChain (.configs / .submit() / .get() / .list() / .cancel()). A
    test drives a submitted job to completion with finish(), the same
    shape execution.jobs.JobStore itself would eventually reach (state +
    run_id/progress/error), instead of actually launching a subprocess."""

    def __init__(self, configs):
        self.configs = configs
        self._jobs = {}

    def submit(self, request, *, settings_path=None):
        kind = request.get("kind")
        if kind == "evolve":
            if set(request) - {"request_id", "kind", "config_id"}:
                raise ConfigError("request", "未対応の要求項目があります")
            rid, cid = request["request_id"], request["config_id"]
            self.configs.get(cid)
            run_id = None
        elif kind == "world_patch":
            from execution.world_patch_job import normalize as normalize_patch
            normalized = normalize_patch(request)
            rid, cid, run_id = normalized["request_id"], normalized["config_id"], normalized["run_id"]
        else:
            raise ConfigError("kind", "処理種別が不正です")
        jid = "job-" + hashlib.sha256(rid.encode()).hexdigest()
        if jid in self._jobs:
            return dict(self._jobs[jid]), False
        for job in self._jobs.values():
            if job["state"] not in TERMINAL:
                raise ConfigError("jobs", "他の処理が実行中または状態確認中です", code="conflict")
        job = {"job_id": jid, "request_id": rid, "kind": kind, "config_id": cid,
               "run_id": run_id, "state": "queued", "progress": {}, "error": None}
        self._jobs[jid] = job
        return dict(job), True

    def get(self, jid):
        if jid not in self._jobs:
            raise ConfigError("job_id", "ジョブがありません", code="not_found")
        return dict(self._jobs[jid])

    def list(self):
        return [dict(job) for job in self._jobs.values()]

    def cancel(self, jid):
        job = self._jobs[jid]
        if job["state"] not in TERMINAL:
            job["state"] = "cancelled"
        return dict(job)

    def finish(self, jid, state, **updates):
        self._jobs[jid].update(state=state, **updates)


class EpochChainTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-epoch-chain-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.repo = base / "repo"
        for name in ("projects", "templates", "engine", "gapengine", "scripts", "execution"):
            shutil.copytree(ROOT / name, self.repo / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / "requirements.txt", self.repo / "requirements.txt")
        self.project = self.repo / "projects" / "momotaro"
        self.template_dir = self.repo / "templates" / "momotaro"
        self.store = ConfigStore(self.repo, base / "control", base / "runs")
        self.jobs = FakeJobStore(self.store)
        self.chain = EpochChain(self.jobs)
        self.base_spec = {"label": "連鎖試験", "project_id": "momotaro", "template_id": "momotaro",
                           "evolution": {"generations": 1, "population": 2, "seeds": 1, "keep": "all"}}
        self.store.save(self.base_spec, config_id="cfg-base")

    def start(self, **overrides):
        spec = {"base_config_id": "cfg-base", "max_epochs": 3, "approval": "auto",
                 "auto_retire": True, "seed_run_id": None}
        spec.update(overrides)
        return self.chain.start(spec)

    def finish_run(self, run_id, *, world_demand=None):
        """Advance the current (latest) epoch's run job to succeeded. Uses
        the real ConfigStore.prepare_run() (freezing inputs only -- no GA is
        actually run) so the run directory is genuinely verify_run()-able:
        an epoch beyond the first always carries evolution.seed_genomes
        forward to the prior epoch's run_id (execution/epoch_chain.py's
        _epoch_spec()), and configs.save() re-verifies that run for real."""
        job = self.chain.current()["epochs"][-1]
        self.store.prepare_run(job["config_id"], run_id=run_id, job_id=job["run_job_id"])
        run_root = self.store.runs / run_id
        (run_root / "world_demand.json").write_text(
            json.dumps({"triggers": world_demand or []}, ensure_ascii=False), encoding="utf-8")
        # A later epoch's evolution.seed_genomes carries this run_id forward
        # (execution/epoch_chain.py's _epoch_spec()), which needs a real,
        # loadable archive.json -- no GA actually ran here, so fabricate the
        # minimal shape gapengine.seed_genomes.from_archive() accepts.
        from gapengine.genome import Genome
        archive = {"cells": {"c0": {"quality": 1.0, "generation": 0, "genome": Genome.neutral().to_dict()}}}
        (run_root / "archive.json").write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
        self.jobs.finish(job["run_job_id"], "succeeded", run_id=run_id)
        return run_root


class StartAndRunStepTests(EpochChainTestBase):
    def test_start_creates_epoch0_config_and_submits_run_job(self):
        job = self.start()
        self.assertEqual(job["kind"], "evolve")
        self.assertEqual(len(self.jobs._jobs), 1)
        config = self.store.get(job["config_id"])
        self.assertEqual(config["label"], "連鎖試験 ／エポック1")
        self.assertEqual(config["evolution"]["world_expansion"], "expand")
        current = self.chain.current()
        self.assertEqual(current["state"], "running")
        self.assertEqual(current["epochs"][0]["run_job_id"], job["job_id"])

    def test_second_start_conflicts_while_a_chain_is_active(self):
        self.start()
        with self.assertRaises(ConfigError) as ctx:
            self.start()
        self.assertEqual(ctx.exception.code, "conflict")

    def test_tick_is_idempotent_while_run_job_still_queued(self):
        self.start()
        self.assertEqual(len(self.jobs._jobs), 1)
        self.chain.tick()
        self.chain.tick()
        self.assertEqual(len(self.jobs._jobs), 1)

    def test_run_success_advances_to_propose_step(self):
        self.start()
        self.finish_run("run-e0")
        self.chain.tick()
        current = self.chain.current()
        self.assertEqual(current["epochs"][0]["step"], "propose")
        self.assertEqual(current["epochs"][0]["run_id"], "run-e0")

    def test_run_failure_marks_chain_failed(self):
        job = self.start()
        self.jobs.finish(job["job_id"], "failed", error={"code": "wall_timeout"})
        self.chain.tick()
        current = self.chain.current()
        self.assertEqual(current["state"], "failed")
        self.assertEqual(current["error"]["code"], "run_failed")

    def test_run_cancelled_externally_marks_chain_stopped(self):
        job = self.start()
        self.jobs.finish(job["job_id"], "cancelled")
        self.chain.tick()
        current = self.chain.current()
        self.assertEqual(current["state"], "stopped")

    def test_tick_is_idempotent_against_the_chains_own_job(self):
        job = self.start()
        # A second tick while the chain's own run job is still queued must
        # neither raise nor submit a duplicate.
        self.chain.tick()
        self.assertEqual(len(self.jobs._jobs), 1)
        self.jobs.finish(job["job_id"], "succeeded", run_id="run-e0")

    def test_start_while_another_unrelated_job_is_running_is_a_clean_conflict(self):
        """M1 (Opus review, orphan.py's test_orphan): starting a chain while
        some OTHER job (not this chain's own) occupies JobStore's single
        non-terminal-job slot must answer 409 immediately and leave nothing
        behind -- not a "running" chain with no job that would otherwise
        submit itself unattended once that other job finishes."""
        self.jobs._jobs["job-other"] = {"job_id": "job-other", "state": "running", "kind": "evolve",
                                         "config_id": "cfg-other", "run_id": None, "progress": {}, "error": None}
        with self.assertRaises(ConfigError) as ctx:
            self.start()
        self.assertEqual(ctx.exception.code, "conflict")
        self.assertIsNone(self.chain.current())
        # A retry while still blocked answers the same clean conflict again.
        with self.assertRaises(ConfigError) as ctx2:
            self.start()
        self.assertEqual(ctx2.exception.code, "conflict")
        self.assertIsNone(self.chain.current())
        # Once the other job finishes, ticking must NOT silently submit e0
        # on its own -- nothing was ever created, so there is nothing to
        # tick; a fresh explicit start() is required.
        self.jobs._jobs["job-other"]["state"] = "succeeded"
        self.chain.tick()
        self.assertIsNone(self.chain.current())
        job = self.start()
        self.assertEqual(job["kind"], "evolve")


class ProposeStepTests(EpochChainTestBase):
    def _to_propose(self, world_demand=None):
        self.start()
        self.finish_run("run-e0", world_demand=world_demand)
        self.chain.tick()  # run -> propose (records run_id)
        return self.chain.current()["epochs"][0]

    def test_no_investigate_trigger_skips_straight_to_retire(self):
        self._to_propose(world_demand=[{"zone": "海", "verb": "craft"}])
        self.chain.tick()
        current = self.chain.current()
        self.assertEqual(current["epochs"][0]["step"], "retire")
        self.assertIn("需要なし", current["epochs"][0]["notes"])
        self.assertEqual(len(self.jobs._jobs), 1)  # no propose job submitted

    def test_investigate_trigger_submits_propose_job(self):
        self._to_propose(world_demand=[{"zone": "海", "verb": "investigate"}])
        self.chain.tick()
        current = self.chain.current()["epochs"][0]
        self.assertEqual(current["step"], "propose")
        self.assertIsNotNone(current["propose_job_id"])
        self.assertEqual(current["trigger"], 0)

    def test_max_patches_reached_without_wither_candidates_skips_propose(self):
        self._to_propose(world_demand=[{"zone": "海", "verb": "investigate"}])
        fake_patches = [{"id": f"p-fake{i:04d}", "add": {}} for i in range(8)]
        with mock.patch("execution.epoch_chain.approved_patches", return_value=fake_patches), \
                mock.patch("execution.epoch_chain.wither_candidates", return_value=[]), \
                mock.patch.object(EpochChain, "_usage", return_value={}):
            self.chain.tick()
        current = self.chain.current()["epochs"][0]
        self.assertEqual(current["step"], "retire")
        self.assertTrue(any("上限" in note for note in current["notes"]))
        self.assertEqual(len(self.jobs._jobs), 1)

    def test_propose_failure_retries_once_then_fails_chain(self):
        self._to_propose(world_demand=[{"zone": "海", "verb": "investigate"}])
        self.chain.tick()
        pid0 = self.chain.current()["epochs"][0]["propose_job_id"]
        self.jobs.finish(pid0, "succeeded", progress={"step": "failed", "message": "no viable patch"})
        self.chain.tick()
        current = self.chain.current()["epochs"][0]
        self.assertEqual(current["propose_attempts"], 1)
        self.assertIsNone(current["propose_job_id"])
        self.assertEqual(self.chain.current()["state"], "running")
        self.chain.tick()  # submit attempt 2
        pid1 = self.chain.current()["epochs"][0]["propose_job_id"]
        self.assertNotEqual(pid0, pid1)
        self.jobs.finish(pid1, "succeeded", progress={"step": "failed", "message": "no viable patch"})
        self.chain.tick()
        current = self.chain.current()
        self.assertEqual(current["state"], "failed")
        self.assertEqual(current["error"]["code"], "propose_failed")

    def test_propose_job_cancelled_externally_marks_chain_stopped(self):
        self._to_propose(world_demand=[{"zone": "海", "verb": "investigate"}])
        self.chain.tick()
        pid = self.chain.current()["epochs"][0]["propose_job_id"]
        self.jobs.finish(pid, "cancelled")
        self.chain.tick()
        self.assertEqual(self.chain.current()["state"], "stopped")

    def test_static_failed_gate_skips_approval_without_calling_approve(self):
        self._to_propose(world_demand=[{"zone": "海", "verb": "investigate"}])
        self.chain.tick()
        pid = self.chain.current()["epochs"][0]["propose_job_id"]
        self.jobs.finish(pid, "succeeded", progress={"step": "done", "patch_id": "p-static01", "status": "static_failed"})
        self.chain.tick()  # propose terminal -> records gate_status, step="approve"
        with mock.patch("execution.epoch_chain.approve") as approve_mock:
            self.chain.tick()  # approve step itself: gate_status != reviewable -> retire
        approve_mock.assert_not_called()
        current = self.chain.current()["epochs"][0]
        self.assertEqual(current["step"], "retire")
        self.assertEqual(current["gate_status"], "static_failed")


class ApproveAndRetireStepTests(EpochChainTestBase):
    def _to_approve(self, *, approval="auto", max_epochs=1, auto_retire=True):
        self.start(approval=approval, max_epochs=max_epochs, auto_retire=auto_retire)
        self.finish_run("run-e0", world_demand=[{"zone": "海", "verb": "investigate"}])
        self.chain.tick()  # -> propose
        self.chain.tick()  # submit propose
        pid = self.chain.current()["epochs"][0]["propose_job_id"]
        self.jobs.finish(pid, "succeeded", progress={"step": "done", "patch_id": "p-review01", "status": "reviewable"})
        self.chain.tick()  # propose terminal -> records gate_status, step="approve"
        return self.chain.current()["epochs"][0]

    def test_manual_mode_waits_for_approval(self):
        self._to_approve(approval="manual")
        self.chain.tick()  # approve step itself: manual -> waiting
        self.assertEqual(self.chain.current()["state"], "waiting")

    def test_approve_step_skipped_while_another_job_is_running(self):
        """M2 (Opus review, plan C8; orphan.py's test_approve_while_other_
        job_running): approve()/retire() never check for a concurrently
        running job themselves (only the viewer's HTTP approve/reject/
        retire actions do, via library_pages._reject_running_job) -- the
        chain must refuse to call either while some other job (unrelated to
        this chain's own, already-terminal run/propose jobs) is still
        writing to patches/, and simply retry on a later tick instead."""
        epoch = self._to_approve(approval="auto", auto_retire=False)
        project_patches = self.project / "patches" / "_proposed"
        project_patches.mkdir(parents=True, exist_ok=True)
        gate = {"trial": {"pairs": [{"base": {"reached": True}, "patched": {"reached": True}}]}}
        (project_patches / f"{epoch['patch_id']}.gate.json").write_text(json.dumps(gate), encoding="utf-8")
        self.jobs._jobs["job-user"] = {"job_id": "job-user", "state": "running", "kind": "world_patch",
                                        "config_id": "cfg-user", "run_id": None, "progress": {}, "error": None}
        with mock.patch("execution.epoch_chain.approve", return_value={"rev": 1}) as approve_mock:
            self.chain.tick()
        approve_mock.assert_not_called()
        current = self.chain.current()["epochs"][0]
        self.assertEqual(current["step"], "approve")  # unchanged: retry next tick
        self.assertIsNone(current["approved_rev"])
        # Once the other job clears, the very next tick approves for real.
        self.jobs._jobs["job-user"]["state"] = "succeeded"
        with mock.patch("execution.epoch_chain.approve", return_value={"rev": 7}) as approve_mock2:
            self.chain.tick()
        approve_mock2.assert_called_once()
        self.assertEqual(self.chain.current()["epochs"][0]["approved_rev"], 7)

    def test_retire_step_skipped_while_another_job_is_running(self):
        """M2: same guard for the retire step's own auto_retire() call."""
        epoch = self._to_approve(approval="auto", auto_retire=True)
        project_patches = self.project / "patches" / "_proposed"
        project_patches.mkdir(parents=True, exist_ok=True)
        gate = {"trial": {"pairs": [{"base": {"reached": True}, "patched": {"reached": True}}]}}
        (project_patches / f"{epoch['patch_id']}.gate.json").write_text(json.dumps(gate), encoding="utf-8")
        with mock.patch("execution.epoch_chain.approve", return_value={"rev": 1}):
            self.chain.tick()  # approve step succeeds, step -> retire
        self.assertEqual(self.chain.current()["epochs"][0]["step"], "retire")
        self.jobs._jobs["job-user"] = {"job_id": "job-user", "state": "running", "kind": "world_patch",
                                        "config_id": "cfg-user", "run_id": None, "progress": {}, "error": None}
        with mock.patch.object(EpochChain, "_auto_retire") as auto_retire_mock:
            self.chain.tick()
        auto_retire_mock.assert_not_called()
        self.assertEqual(self.chain.current()["epochs"][0]["step"], "retire")  # unchanged: retry next tick
        self.jobs._jobs["job-user"]["state"] = "succeeded"
        self.chain.tick()
        self.assertEqual(self.chain.current()["epochs"][0]["step"], "done")

    def test_resume_conflicts_while_proposal_still_pending(self):
        epoch = self._to_approve(approval="manual")
        self.chain.tick()  # approve step itself: manual -> waiting
        chain_id = self.chain.current()["chain_id"]
        proposed = self.project / "patches" / "_proposed"
        proposed.mkdir(parents=True)
        (proposed / f"{epoch['patch_id']}.yaml").write_text("id: x\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            self.chain.resume(chain_id)
        self.assertEqual(ctx.exception.code, "conflict")

    def test_resume_after_reject_continues_to_retire_and_completes(self):
        epoch = self._to_approve(approval="manual", max_epochs=1, auto_retire=False)
        self.chain.tick()  # approve step itself: manual -> waiting
        chain_id = self.chain.current()["chain_id"]
        # No _proposed/ file left (as if the user already rejected it).
        result = self.chain.resume(chain_id)
        self.assertEqual(result["state"], "running")
        self.assertEqual(result["epochs"][0]["step"], "retire")
        self.chain.tick()
        self.assertEqual(self.chain.current()["state"], "completed")

    def test_worsening_more_than_improving_skips_auto_approval(self):
        epoch = self._to_approve(approval="auto", auto_retire=False)
        project_patches = self.project / "patches" / "_proposed"
        project_patches.mkdir(parents=True, exist_ok=True)
        gate = {"trial": {"pairs": [
            {"base": {"reached": True}, "patched": {"reached": False}},
            {"base": {"reached": True}, "patched": {"reached": False}},
            {"base": {"reached": False}, "patched": {"reached": True}},
        ]}}
        (project_patches / f"{epoch['patch_id']}.gate.json").write_text(json.dumps(gate), encoding="utf-8")
        with mock.patch("execution.epoch_chain.approve") as approve_mock:
            self.chain.tick()
        approve_mock.assert_not_called()
        current = self.chain.current()
        self.assertEqual(current["epochs"][0]["step"], "retire")
        self.assertTrue(any("見送りました" in n for n in current["epochs"][0]["notes"]))

    def test_gate_read_failure_falls_back_to_retire(self):
        # No gate.json ever written under _proposed/ for this patch_id.
        self._to_approve(approval="auto", auto_retire=False)
        with mock.patch("execution.epoch_chain.approve") as approve_mock:
            self.chain.tick()
        approve_mock.assert_not_called()
        self.assertEqual(self.chain.current()["epochs"][0]["step"], "retire")


class SeedGenomesCarryForwardTests(EpochChainTestBase):
    def test_empty_prior_archive_falls_back_to_no_seed_genomes_instead_of_failing(self):
        """R2 (Opus review): gapengine.evolve can legitimately end a run
        with zero occupied archive cells (every individual rejected) --
        ConfigStore.save() then refuses evolution.seed_genomes with
        "引き継ぎ元のアーカイブにデータがありません". That must not fail the
        chain: it should retry once with seed_genomes cleared and keep
        going, noting why the carry-forward was skipped."""
        self.start(max_epochs=2, auto_retire=False)
        job = self.chain.current()["epochs"][-1]
        self.store.prepare_run(job["config_id"], run_id="run-e0", job_id=job["run_job_id"])
        run_root = self.store.runs / "run-e0"
        (run_root / "world_demand.json").write_text(json.dumps({"triggers": []}), encoding="utf-8")
        (run_root / "archive.json").write_text(json.dumps({"cells": {}}), encoding="utf-8")
        self.jobs.finish(job["run_job_id"], "succeeded", run_id="run-e0")
        self.chain.tick()  # run -> propose (records run_id)
        self.chain.tick()  # no demand -> retire
        self.chain.tick()  # retire -> next epoch appended
        current = self.chain.current()
        self.assertEqual(current["state"], "running")
        self.assertEqual(len(current["epochs"]), 2)
        self.chain.tick()  # epoch1's run step: config save falls back, then submits
        epoch1 = self.chain.current()["epochs"][1]
        self.assertIsNotNone(epoch1["run_job_id"])
        self.assertIn("前エポックの地図が空のため引き継ぎなし", epoch1["notes"])
        saved = self.store.get(epoch1["config_id"])
        self.assertIsNone(saved["evolution"]["seed_genomes"])


class IdleCompletionAndStopTests(EpochChainTestBase):
    def test_two_consecutive_idle_epochs_complete_the_chain(self):
        self.start(max_epochs=5, auto_retire=False)
        for n in range(IDLE_EPOCHS):
            if n > 0:
                self.chain.tick()  # submit the run job for the newly appended epoch
            self.finish_run(f"run-e{n}", world_demand=[])
            self.chain.tick()  # -> propose
            self.chain.tick()  # no demand -> retire
            self.chain.tick()  # retire step -> idle_streak += 1
            current = self.chain.current()
            if n + 1 < IDLE_EPOCHS:
                self.assertEqual(current["state"], "running")
                self.assertEqual(len(current["epochs"]), n + 2)
        self.assertEqual(self.chain.current()["state"], "completed")

    def test_max_epochs_reached_completes_even_without_idle_streak(self):
        self.start(max_epochs=1, auto_retire=False)
        self.finish_run("run-e0", world_demand=[])
        self.chain.tick()
        self.chain.tick()
        self.chain.tick()
        self.assertEqual(self.chain.current()["state"], "completed")

    def test_stop_while_run_job_pending_cancels_it_and_marks_stopped(self):
        job = self.start()
        chain_id = self.chain.current()["chain_id"]
        result = self.chain.stop(chain_id)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(self.jobs.get(job["job_id"])["state"], "cancelled")

    def test_tick_never_raises_on_unexpected_error(self):
        self.start()
        with mock.patch.object(EpochChain, "_step_run", side_effect=RuntimeError("boom")):
            self.chain.tick()  # must not raise
        current = self.chain.current()
        self.assertEqual(current["state"], "failed")
        self.assertEqual(current["error"]["code"], "tick_error")


class BrokenChainDirectoryTests(EpochChainTestBase):
    """M2 (Opus review, WB-WORLDGROW-001 段階5c-2 follow-up): a broken chain
    directory (never written, or corrupt chain.json -- e.g. a crash mid-
    write) must not take down every OTHER chain's tick()/current()/start(),
    and must never surface as a 404/500 on an unrelated page's own GET
    (viewer/epoch_view.py's relevant_chain(), tested directly in
    test_epoch_view.py). Reproduces the scenario the review's own broken.py
    script exercised over HTTP."""

    def _make_broken(self, name, *, contents=None):
        folder = self.chain.root / name
        folder.mkdir(parents=True)
        if contents is not None:
            (folder / "chain.json").write_text(contents, encoding="utf-8")
        return folder

    def test_all_skips_an_empty_chain_directory(self):
        self.chain.root.mkdir(parents=True, exist_ok=True)
        self._make_broken("chain-empty")
        self.assertEqual(self.chain._all(), [])  # must not raise

    def test_all_skips_a_corrupt_chain_json(self):
        self.chain.root.mkdir(parents=True, exist_ok=True)
        self._make_broken("chain-corrupt", contents="{bad")
        self.assertEqual(self.chain._all(), [])  # must not raise

    def test_all_still_returns_healthy_chains_alongside_a_broken_one(self):
        self.start()
        healthy = self.chain.current()
        self._make_broken("chain-corrupt", contents="{bad")
        all_chains = self.chain._all()
        self.assertEqual([c["chain_id"] for c in all_chains], [healthy["chain_id"]])

    def test_tick_and_current_survive_a_broken_directory_with_no_active_chain(self):
        self._make_broken("chain-empty")
        self._make_broken("chain-corrupt", contents="{bad")
        self.chain.tick()  # must not raise
        self.assertIsNone(self.chain.current())  # neither broken dir "counts"

    def test_start_still_works_alongside_a_broken_directory(self):
        self._make_broken("chain-corrupt", contents="{bad")
        job = self.start()
        self.assertIsNotNone(job["job_id"])


class AutoRetirePublishedOnlyArchiveTests(EpochChainTestBase):
    def test_auto_retire_uses_published_only_archive_fixture(self):
        add = {"zones": [{"name": "船大工の小屋", "parent": "海"}]}
        patch = write_approved(self.project, {"title": "小屋を足す", "add": add}, template=self.template_dir)
        pid = patch["id"]
        run_root = self.store.runs / "run-e0"
        world_path = run_root / "inputs/projects/momotaro/world.yaml"
        world_path.parent.mkdir(parents=True)
        world_path.write_text(yaml.safe_dump({"expansion": {"patches": [{"id": pid}]}}, allow_unicode=True),
                               encoding="utf-8")
        # No top-level archive.json -- only published/<rev>/archive.json,
        # the shape a ConfigStore-prepared experiment actually has.
        _write_jsonl(run_root / "g0/ind-0/seed-1/layers.jsonl", _NO_MOVE_ROWS)
        (run_root / "published/1").mkdir(parents=True)
        (run_root / "published/current.json").write_text(json.dumps({"revision": 1}), encoding="utf-8")
        (run_root / "published/1/archive.json").write_text(
            json.dumps({"cells": {"c0": {"exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"}}}},
                       ensure_ascii=False), encoding="utf-8")
        self.store.save(self.base_spec, config_id="cfg-e0")
        chain = {"chain_id": "chain-test", "project_id": "momotaro", "template_id": "momotaro"}
        epoch = {"index": 0, "config_id": "cfg-e0", "run_id": "run-e0",
                 "patch_id": None, "approved_rev": None, "retired": [], "notes": []}
        self.chain._auto_retire(chain, epoch)
        self.assertEqual(epoch["retired"], [pid])
        self.assertEqual(approved_patches(self.project), [])
        record = json.loads((self.project / "patches" / f"{pid}.retire.json").read_text(encoding="utf-8"))
        self.assertEqual(record["usage"]["elites_strong"], 0)
        self.assertEqual(record["usage"]["elites_total"], 1)


# --------------------------------------------------------------------------
# HTTP API (viewer/job_api.py's /api/epochs routes and the POST /api/jobs
# growth interception), modeled on tests/test_world_expansion_api.py's
# real-ViewerServer + FakeJobStore approach.
# --------------------------------------------------------------------------


class EpochApiTests(EpochChainTestBase):
    def setUp(self):
        super().setUp()
        self.store.runs.mkdir(parents=True, exist_ok=True)
        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = RunRepository(self.store.runs)
        self.server.job_store = self.jobs
        self.server.settings_path = None
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def http(self, method, path, body=None, *, client_header=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if client_header:
            headers["X-WorldBloom-Client"] = "1"
        try:
            # C1 (Opus re-review): a GET with a body the server never reads
            # makes it close with unread data -> RST on Windows (WinError 10053).
            payload = None if method == "GET" else json.dumps({} if body is None else body)
            conn.request(method, path, payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
        finally:
            conn.close()

    def test_current_is_null_before_any_chain_and_reflects_state_after(self):
        status, body = self.http("GET", "/api/epochs/current")
        self.assertEqual(status, 200)
        self.assertIsNone(body["chain"])
        self.http("POST", "/api/epochs", {"base_config_id": "cfg-base", "max_epochs": 1,
                                            "approval": "auto", "auto_retire": False, "seed_run_id": None})
        status, body = self.http("GET", "/api/epochs/current")
        self.assertEqual(status, 200)
        self.assertEqual(body["chain"]["state"], "running")

    def test_start_via_epochs_route_rejects_out_of_range_epochs(self):
        status, body = self.http("POST", "/api/epochs", {"base_config_id": "cfg-base", "max_epochs": 99,
                                                            "approval": "auto", "auto_retire": False,
                                                            "seed_run_id": None})
        self.assertEqual(status, 422)

    def test_stop_route_marks_chain_stopped(self):
        status, body = self.http("POST", "/api/epochs", {"base_config_id": "cfg-base", "max_epochs": 1,
                                                            "approval": "auto", "auto_retire": False,
                                                            "seed_run_id": None})
        self.assertEqual(status, 202)
        chain_id = self.chain.current()["chain_id"]
        status, body = self.http("POST", f"/api/epochs/{chain_id}/stop")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "stopped")

    def test_continue_route_409s_when_not_waiting(self):
        self.http("POST", "/api/epochs", {"base_config_id": "cfg-base", "max_epochs": 1,
                                            "approval": "auto", "auto_retire": False, "seed_run_id": None})
        chain_id = self.chain.current()["chain_id"]
        status, body = self.http("POST", f"/api/epochs/{chain_id}/continue")
        self.assertEqual(status, 409)

    def test_continue_route_rejects_a_non_empty_body(self):
        self.http("POST", "/api/epochs", {"base_config_id": "cfg-base", "max_epochs": 1,
                                            "approval": "auto", "auto_retire": False, "seed_run_id": None})
        chain_id = self.chain.current()["chain_id"]
        status, body = self.http("POST", f"/api/epochs/{chain_id}/continue", {"reason": "not empty"})
        self.assertEqual(status, 400)

    def test_stop_route_rejects_a_non_empty_body(self):
        self.http("POST", "/api/epochs", {"base_config_id": "cfg-base", "max_epochs": 1,
                                            "approval": "auto", "auto_retire": False, "seed_run_id": None})
        chain_id = self.chain.current()["chain_id"]
        status, body = self.http("POST", f"/api/epochs/{chain_id}/stop", {"reason": "not empty"})
        self.assertEqual(status, 400)

    def test_post_jobs_against_growth_off_config_is_a_plain_evolve_job(self):
        status, body = self.http("POST", "/api/jobs",
                                  {"request_id": "req-plain", "kind": "evolve", "config_id": "cfg-base"})
        self.assertEqual(status, 202)
        self.assertIsNone(self.chain.current())  # no chain was started

    def test_post_jobs_against_growth_config_starts_a_chain(self):
        self.store.save({**self.base_spec, "growth": {"mode": "auto", "epochs": 2, "auto_retire": True}},
                         config_id="cfg-growth")
        status, body = self.http("POST", "/api/jobs",
                                  {"request_id": "req-growth", "kind": "evolve", "config_id": "cfg-growth"})
        self.assertEqual(status, 202)
        current = self.chain.current()
        self.assertIsNotNone(current)
        self.assertEqual(current["max_epochs"], 2)
        self.assertEqual(current["approval"], "auto")

    def test_post_jobs_against_growth_config_is_idempotent_by_request_id(self):
        """R3 (Opus review): the growth path must give the same request_id
        idempotency contract as the plain evolve path (execution/jobs.py's
        JobStore.submit()) -- a retried POST with the same request_id
        returns the SAME epoch-0 job, not a "chain already active" 409."""
        self.store.save({**self.base_spec, "growth": {"mode": "auto", "epochs": 2, "auto_retire": True}},
                         config_id="cfg-growth")
        body = {"request_id": "req-growth-idem", "kind": "evolve", "config_id": "cfg-growth"}
        status1, job1 = self.http("POST", "/api/jobs", body)
        status2, job2 = self.http("POST", "/api/jobs", body)
        self.assertEqual(status1, 202)
        self.assertEqual(status2, 202)
        self.assertEqual(job1, job2)

    def test_post_jobs_against_growth_config_rejects_unknown_keys(self):
        self.store.save({**self.base_spec, "growth": {"mode": "auto", "epochs": 2, "auto_retire": True}},
                         config_id="cfg-growth")
        status, body = self.http("POST", "/api/jobs",
                                  {"request_id": "req-growth-x", "kind": "evolve",
                                   "config_id": "cfg-growth", "extra": 1})
        self.assertEqual(status, 422)

    def test_post_jobs_against_growth_config_rejects_bad_request_id_format(self):
        self.store.save({**self.base_spec, "growth": {"mode": "auto", "epochs": 2, "auto_retire": True}},
                         config_id="cfg-growth")
        status, body = self.http("POST", "/api/jobs",
                                  {"request_id": "not a valid id!", "kind": "evolve", "config_id": "cfg-growth"})
        self.assertEqual(status, 422)

    def test_epochs_start_route_requires_client_header(self):
        status, _ = self.http("POST", "/api/epochs",
                               {"base_config_id": "cfg-base", "max_epochs": 1, "approval": "auto",
                                "auto_retire": False, "seed_run_id": None}, client_header=False)
        self.assertEqual(status, 403)

    def test_no_job_store_returns_503(self):
        self.server.job_store = None
        status, body = self.http("GET", "/api/epochs/current")
        self.assertEqual(status, 503)


# --------------------------------------------------------------------------
# M4 (Opus review): a heavy, real end-to-end run -- real ConfigStore/
# JobStore, a real (tiny, guaranteed-reaching) GA for both epochs, a real
# propose+holdout-check subprocess (gapengine.synopsis.generate_text stubbed
# the way tests/test_world_patch_job.py stubs it -- no real LLM), and a real
# in-process approve()/retire().
#
# The chain-driving logic below runs in a CHILD process whose sys.path puts
# the cloned repo FIRST, before any execution/gapengine import. This is not
# an accident of test plumbing: approve()'s own runtime_digest() call takes
# no repo_root argument, so it hashes whatever copy of gapengine/execution
# Python happens to have imported (gapengine.world_patch_inputs.ROOT is
# derived from that module's own __file__) -- which must be the SAME files
# the propose subprocess measured against, or approve() refuses with "入力・
# 実行コード・規約が検査時と一致しません" even though nothing is actually
# wrong. In production this never comes up (the viewer and every job share
# one repo); only an isolated test clone creates the mismatch. Driving the
# chain from a child process whose sys.path[0] is the clone keeps every
# runtime_digest() call -- subprocess and in-process alike -- pointed at
# the same files.
# --------------------------------------------------------------------------

_DRIVER_SOURCE = '''
import sys, json, time
repo, base, tests_dir = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, repo)
sys.path.insert(1, tests_dir)
from pathlib import Path
from unittest import mock
from execution.configs import ConfigStore
from execution.jobs import JobStore
from execution import epoch_chain as ec
from test_world_patch_job import _write_world_demand

base = Path(base)
store = ConfigStore(Path(repo), base / "control", base / "runs")
jobs = JobStore(store, cancel_grace_seconds=5)
chain = ec.EpochChain(jobs, base / "settings.json")

patched = False
with mock.patch("execution.world_patch_job.generation_availability", return_value={"available": True}):
    chain.start({"base_config_id": "cfg-e2e-base", "max_epochs": 2, "approval": "auto",
                 "auto_retire": True, "seed_run_id": None})
    deadline = time.monotonic() + 550
    while time.monotonic() < deadline:
        c = chain.current()
        ep = c["epochs"][-1]
        if not patched and ep["step"] == "propose" and ep.get("run_id") and ep.get("propose_job_id") is None:
            _write_world_demand(store.runs / ep["run_id"])
            patched = True
        if c["state"] not in ("running", "waiting", "stopping"):
            break
        chain.tick()
        time.sleep(0.3)

(base / "result.json").write_text(json.dumps(chain.current(), ensure_ascii=False), encoding="utf-8")
'''


@unittest.skipUnless(os.name == "nt", "Windows process supervision")
class HeavyRealChainEndToEndTest(unittest.TestCase):
    def test_two_epoch_auto_chain_with_real_ga_and_real_approval(self):
        temp = tempfile.TemporaryDirectory(prefix="wb-epoch-chain-e2e-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        from world_patch_fixtures import frozen_experiment
        from test_world_patch_cli import VALID_ADD
        experiment, project, template = frozen_experiment(base)
        repo = base / "repo"

        # Shrink the holdout trial (default 5x8 reruns would add minutes) --
        # same technique tests/test_world_patch_job.py uses.
        wpj_path = repo / "execution" / "world_patch_job.py"
        wpj_path.write_text(
            wpj_path.read_text(encoding="utf-8") + '\nTRIAL_ARGS = ("--max-runs", "5", "--seeds-per-run", "4")\n',
            encoding="utf-8")
        # Stub generate_text in the CLONED repo only -- no real LLM.
        source = (ROOT / "gapengine" / "synopsis.py").read_text(encoding="utf-8")
        payload = json.dumps({"title": "海辺の船大工小屋", "rationale": "海でのinvestigateが空振りし続けている",
                               "add": VALID_ADD}, ensure_ascii=False)
        source += ("\n\ndef generate_text(backend, prompt, *, settings_path=None, timeout=600):\n"
                   f"    return GenerationResult(status='ok', text={payload!r})\n")
        (repo / "gapengine" / "synopsis.py").write_text(source, encoding="utf-8")

        (base / "settings.json").write_text(json.dumps({"output": {"default_backend": "codex-cli",
            "codex-cli": {"limits": {"wall_seconds": 600}}}}), encoding="utf-8")

        store = ConfigStore(repo, base / "control", base / "runs")
        store.save({"label": "e2e", "project_id": "momotaro", "template_id": "momotaro",
                    "evolution": {"generations": 2, "population": 12, "seeds": 2, "seed_base": 31, "ga_seed": 29,
                                  "processes": 1, "keep": "all", "record_explanations": True},
                    "growth": {"mode": "auto", "epochs": 2, "auto_retire": True}}, config_id="cfg-e2e-base")

        driver = base / "chain_driver.py"
        driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
        import subprocess
        result = subprocess.run(
            [sys.executable, str(driver), str(repo), str(base), str(ROOT / "tests")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=580)
        self.assertTrue((base / "result.json").is_file(),
                         f"driver produced no result.json -- rc={result.returncode}\n"
                         f"stdout={result.stdout[-2000:]}\nstderr={result.stderr[-2000:]}")
        chain = json.loads((base / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(chain["state"], "completed", chain)
        epoch0, epoch1 = chain["epochs"]

        # Auto-approval succeeded, idle_streak reset by it, and the
        # just-approved patch was excluded from this same epoch's
        # auto-retire (the "猶予1" grace rule).
        self.assertIsNotNone(epoch0["approved_rev"])
        self.assertEqual(epoch0["retired"], [])
        self.assertIn("自動承認しました", epoch0["notes"])

        # e1's config never carries growth, is forced to "expand", carries
        # e0's approved patch in its input-manifest, and its seed_genomes.json
        # sources e0's own run_id.
        e1_config = json.loads((base / "control/configs" / epoch1["config_id"] / "config.json").read_text("utf-8"))
        self.assertNotIn("growth", e1_config)
        self.assertEqual(e1_config["evolution"]["world_expansion"], "expand")
        self.assertEqual(e1_config["evolution"]["seed_genomes"], epoch0["run_id"])
        manifest = json.loads(
            (base / "control/configs" / epoch1["config_id"] / "input-manifest.json").read_text("utf-8"))
        world_patch_ids = {p["id"] for record in manifest["files"]
                            for p in (record.get("world_patches") or [])}
        self.assertIn(epoch0["patch_id"], world_patch_ids)
        seed_genomes = json.loads(
            (base / "control/configs" / epoch1["config_id"] / "inputs/seed_genomes.json").read_text("utf-8"))
        self.assertEqual(seed_genomes["source"]["run_id"], epoch0["run_id"])


if __name__ == "__main__":
    unittest.main()
