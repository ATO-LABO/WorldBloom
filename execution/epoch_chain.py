"""World-growth epoch chains (WB-WORLDGROW-001 段階5c-1): run -> propose ->
approve -> retire, repeated for growth.epochs, driven one step per tick()
call from ViewerServer.service_actions (see viewer/server.py). No thread or
timer of its own -- a chain that nobody ticks simply sits still, and a
missed tick is never lost (chain.json is the only state).

Only one chain may be active (state in running/waiting/stopping) at a time,
mirroring JobStore's own single-non-terminal-job constraint (execution/
jobs.py's submit()) -- an epoch's run/propose jobs already go through that
same JobStore, so a second concurrent chain would just deadlock against it.
"""
from __future__ import annotations

from contextlib import contextmanager
import secrets
import time
from pathlib import Path

import yaml

from execution import worker
from execution.provenance import ConfigError, atomic_json, contained, directory_lock, identifier, read_json
from execution.world_patch_approval import PatchError, approve, retire
from execution.world_patch_job import _triggers
from execution.world_patches import _world_parent_rev, patch_lock
from gapengine.world_patch import MAX_TOTAL_PATCHES, approved_patches, read_stack
from gapengine.world_patch_usage import load_archive, patch_usage, wither_candidates

# WB-WORLDGROW-001 段階5c §2: two consecutive epochs with neither an
# approval nor a retirement mean the world has stopped changing.
IDLE_EPOCHS = 2

_ACTIVE_STATES = ("running", "waiting", "stopping")


def _new_epoch(chain_id, n):
    now = time.time()
    return {"index": n, "step": "run", "config_id": f"{chain_id}-e{n}",
            "run_job_id": None, "run_id": None, "propose_job_id": None, "propose_attempts": 0,
            "trigger": None, "patch_id": None, "gate_status": None, "approved_rev": None,
            "retired": [], "notes": [], "started_at": now, "finished_at": None}


class EpochChain:
    def __init__(self, jobs, settings_path=None):
        self.jobs = jobs
        self.configs = jobs.configs
        self.settings_path = settings_path
        self.root = self.configs.control / "epochs"

    # -- storage -----------------------------------------------------------

    @contextmanager
    def _lock(self):
        with directory_lock(self.root):
            yield

    def _folder(self, chain_id):
        return contained(self.root, identifier(chain_id, "chain_id"))

    def _read(self, chain_id):
        try:
            return read_json(self._folder(chain_id) / "chain.json")
        except FileNotFoundError as error:
            raise ConfigError("chain_id", "連鎖がありません", code="not_found") from error

    def _save(self, chain, **updates):
        chain.update(updates)
        chain["updated_at"] = time.time()
        chain["revision"] = chain.get("revision", 0) + 1
        atomic_json(self._folder(chain["chain_id"]) / "chain.json", chain)
        return chain

    def _all(self):
        if not self.root.exists():
            return []
        return [self._read(p.name) for p in sorted(self.root.iterdir())
                if p.is_dir() and p.name.startswith("chain-")]

    @staticmethod
    def _public(chain):
        from copy import deepcopy
        return deepcopy(chain)

    # -- public API ----------------------------------------------------------

    def start(self, spec, *, chain_id=None):
        """chain_id: when given (R3, Opus review -- viewer/job_api.py's POST
        /api/jobs growth interception derives one from the client's own
        request_id), a second call with the same chain_id and the same
        effective request is a replay: it returns the first call's epoch-0
        job again instead of erroring, the same idempotency contract
        execution.jobs.JobStore.submit() gives every other request_id-keyed
        request. A different request under the same chain_id is rejected
        the same way JobStore.submit() rejects a reused request_id with
        different content. Left None (POST /api/epochs has no client
        request_id of its own), a fresh random id is generated as before."""
        if not isinstance(spec, dict):
            raise ConfigError("request", "オブジェクトを指定してください")
        allowed = {"base_config_id", "max_epochs", "approval", "auto_retire", "seed_run_id"}
        if set(spec) - allowed:
            raise ConfigError("request", "未対応の項目があります")
        base_config_id = identifier(spec.get("base_config_id"), "base_config_id")
        max_epochs = spec.get("max_epochs")
        if type(max_epochs) is not int or not (1 <= max_epochs <= 10):
            raise ConfigError("max_epochs", "エポック数は1〜10で指定してください")
        approval = spec.get("approval")
        if approval not in ("auto", "manual"):
            raise ConfigError("approval", "承認方式が不正です")
        auto_retire = spec.get("auto_retire")
        if type(auto_retire) is not bool:
            raise ConfigError("auto_retire", "真偽値を指定してください")
        seed_run_id = spec.get("seed_run_id")
        if seed_run_id is not None:
            seed_run_id = identifier(seed_run_id, "seed_run_id")
        if chain_id is not None:
            identifier(chain_id, "chain_id")
        request_signature = {"base_config_id": base_config_id, "max_epochs": max_epochs,
                              "approval": approval, "auto_retire": auto_retire, "seed_run_id": seed_run_id}
        base = self.configs.get(base_config_id)
        if seed_run_id is not None:
            self.configs.verify_run(seed_run_id)
        with self._lock():
            if chain_id is not None:
                try:
                    existing = self._read(chain_id)
                except ConfigError as error:
                    if error.code != "not_found":
                        raise
                    existing = None
                if existing is not None:
                    if existing.get("request_signature") != request_signature:
                        raise ConfigError("request_id", "同じ要求IDに異なる内容が指定されました", code="conflict")
                    run_job_id = existing["epochs"][0].get("run_job_id")
                    if run_job_id is None:
                        reason = (existing.get("error") or {}).get("message")
                        raise ConfigError(
                            "chain_id", "エポックの開始に失敗しました" + (f": {reason}" if reason else ""),
                            code="unavailable")
                    return self.jobs.get(run_job_id)
            if any(c["state"] in _ACTIVE_STATES for c in self._all()):
                raise ConfigError("chain_id", "実行中のエポック連鎖があります", code="conflict")
            # M1 (Opus review): same single-non-terminal-job constraint
            # JobStore.submit() itself enforces -- checked here too, and
            # BEFORE the chain is ever written, so starting while another
            # job (unrelated to any chain) is running answers a clean 409
            # instead of creating a "running" chain with no job that would
            # otherwise submit itself unattended once that other job ends.
            if any(job.get("state") not in worker.TERMINAL for job in self.jobs.list()):
                raise ConfigError("jobs", "他の処理が実行中または状態確認中です", code="conflict")
            now = time.time()
            chain_id = chain_id or ("chain-" + secrets.token_hex(8))
            chain = {"schema_version": 1, "chain_id": chain_id, "created_at": now, "updated_at": now,
                      "revision": 0, "project_id": base["project_id"], "template_id": base["template_id"],
                      "base_config_id": base_config_id, "approval": approval, "auto_retire": auto_retire,
                      "max_epochs": max_epochs, "seed_run_id": seed_run_id, "idle_streak": 0,
                      "request_signature": request_signature,
                      "state": "running", "stop_requested_at": None, "error": None,
                      "epochs": [_new_epoch(chain_id, 0)]}
            atomic_json(self._folder(chain_id) / "chain.json", chain)
            try:
                self._tick_locked(chain)
            except Exception as error:
                # M1: an exception here must not leave a "running" zombie
                # chain with no job behind -- mark it failed (never silently
                # delete: the note/error is diagnostic) before re-raising.
                try:
                    self._save(chain, state="failed",
                               error={"code": "start_failed", "message": str(error)[:300]})
                except Exception:
                    pass
                raise
            run_job_id = chain["epochs"][0].get("run_job_id")
            if run_job_id is None:
                if chain["state"] not in ("failed", "stopped", "completed"):
                    self._save(chain, state="failed", error={"code": "start_failed"})
                reason = (chain.get("error") or {}).get("message")
                raise ConfigError("chain_id", "エポックの開始に失敗しました" + (f": {reason}" if reason else ""),
                                   code="unavailable")
        return self.jobs.get(run_job_id)

    def _root_ready(self):
        """True once start() has created control/epochs/.write.lock at
        least once. Every read-only entry point (current/tick/get) checks
        this BEFORE calling directory_lock(): directory_lock()'s own
        root.mkdir() would otherwise race a concurrent first start() to
        create control/epochs for the very first time -- on Windows/
        Python 3.13 a resolve() racing that mkdir can return a transient
        \\\\?\\-prefixed path, which contained()'s _checked_root() then
        caches (lru_cache) forever, breaking every later contained() call
        under this root until process restart (M3, Opus review). Only
        start() is ever allowed to create the directory; this check itself
        touches no lock and creates nothing."""
        return (self.root / ".write.lock").exists()

    def get(self, chain_id):
        if not self._root_ready():
            raise ConfigError("chain_id", "連鎖がありません", code="not_found")
        with self._lock():
            return self._public(self._read(chain_id))

    def current(self):
        if not self._root_ready():
            return None
        with self._lock():
            chains = self._all()
            if not chains:
                return None
            chains.sort(key=lambda c: c["updated_at"])
            return self._public(chains[-1])

    def stop(self, chain_id):
        if not self._root_ready():
            raise ConfigError("chain_id", "連鎖がありません", code="not_found")
        with self._lock():
            chain = self._read(chain_id)
            if chain["state"] not in _ACTIVE_STATES:
                return self._public(chain)
            chain["stop_requested_at"] = chain.get("stop_requested_at") or time.time()
            self._drain_stop(chain)
            return self._public(chain)

    def resume(self, chain_id):
        if not self._root_ready():
            raise ConfigError("chain_id", "連鎖がありません", code="not_found")
        with self._lock():
            chain = self._read(chain_id)
            if chain["state"] != "waiting":
                raise ConfigError("chain_id", "再開できる状態ではありません", code="conflict")
            epoch = chain["epochs"][-1]
            project = contained(self.configs.repo, "projects/" + chain["project_id"])
            proposed = project / "patches" / "_proposed" / f"{epoch['patch_id']}.yaml"
            if proposed.is_file():
                raise ConfigError("patch_id", "先に承認か却下をしてください", code="conflict")
            try:
                stack = read_stack(project)
                match = next((r for r in stack["revisions"] if r.get("patch_id") == epoch["patch_id"]
                              and r.get("kind", "patch") != "retire"), None)
                if match:
                    epoch["approved_rev"] = match["rev"]
            except PatchError:
                pass
            epoch["step"] = "retire"
            return self._save(chain, state="running")

    def tick(self):
        """One step of the single active chain, if any. Never raises --
        service_actions calls this unconditionally on every accept loop."""
        if not self._root_ready():
            return
        with self._lock():
            chain = next((c for c in self._all() if c["state"] in _ACTIVE_STATES), None)
            if chain is None:
                return
            try:
                self._tick_locked(chain)
            except Exception as error:  # noqa: BLE001 -- tick must never crash the server
                try:
                    self._save(chain, state="failed",
                               error={"code": "tick_error", "message": str(error)[:300]})
                except Exception:
                    pass

    # -- stepping ------------------------------------------------------------

    def _tick_locked(self, chain):
        if chain["stop_requested_at"] is not None:
            self._drain_stop(chain)
            return
        epoch = chain["epochs"][-1]
        step = {"run": self._step_run, "propose": self._step_propose,
                "approve": self._step_approve, "retire": self._step_retire}.get(epoch["step"])
        if step is not None:
            step(chain, epoch)

    def _drain_stop(self, chain):
        epoch = chain["epochs"][-1]
        pending = [jid for jid in (epoch.get("run_job_id"), epoch.get("propose_job_id")) if jid]
        all_terminal = True
        for jid in pending:
            try:
                job = self.jobs.get(jid)
            except ConfigError:
                continue
            if job["state"] not in worker.TERMINAL:
                try:
                    job = self.jobs.cancel(jid)
                except ConfigError:
                    pass
                # A queued job (real JobStore or this test's fake) cancels
                # synchronously; a running one only moves to "stopping" and
                # needs a later tick to confirm the worker actually died.
                if job.get("state") not in worker.TERMINAL:
                    all_terminal = False
        self._save(chain, state="stopped" if all_terminal else "stopping")

    def _finish_epoch_failed(self, chain, code):
        epoch = chain["epochs"][-1]
        message = epoch["notes"][-1] if epoch.get("notes") else None
        self._save(chain, state="failed", error={"code": code, "message": message})

    def _epoch_spec(self, chain, n):
        base = self.configs.get(chain["base_config_id"])
        seed_genomes = (chain["epochs"][n - 1]["run_id"] if n > 0
                         else (chain["seed_run_id"] or base["evolution"].get("seed_genomes")))
        return {"label": f"{base['label']} ／エポック{n + 1}", "project_id": base["project_id"],
                "template_id": base["template_id"], "execution_limits": base["execution_limits"],
                "evolution": {**base["evolution"], "world_expansion": "expand", "seed_genomes": seed_genomes}}

    def _save_epoch_config(self, chain, epoch):
        """True: proceed to submit the run job (config exists, whether
        freshly saved here or already on disk from a crashed prior
        attempt -- "conflict" is treated the same as success). False: the
        epoch was marked failed; the caller must stop.

        R2 (Opus review): a prior epoch's archive can genuinely have zero
        occupied cells (every individual rejected -- see gapengine.evolve's
        own archive-admission rules), which makes ConfigStore.save() refuse
        evolution.seed_genomes with "引き継ぎ元のアーカイブにデータがありません".
        That is not a fatal chain error -- retried once with seed_genomes
        cleared, so the chain keeps going without a genome carry-forward
        rather than dying because the previous epoch's world produced
        nothing to inherit."""
        spec = self._epoch_spec(chain, epoch["index"])
        try:
            self.configs.save(spec, config_id=epoch["config_id"], parent_config_id=None)
        except ConfigError as error:
            if error.code == "conflict":
                return True
            if (spec["evolution"]["seed_genomes"] is not None
                    and error.code == "empty_seed_archive"):
                epoch["notes"].append("前エポックの地図が空のため引き継ぎなし")
                spec["evolution"]["seed_genomes"] = None
                try:
                    self.configs.save(spec, config_id=epoch["config_id"], parent_config_id=None)
                except ConfigError as retry_error:
                    if retry_error.code == "conflict":
                        return True
                    epoch["notes"].append(f"設定の保存に失敗しました: {retry_error}")
                    self._finish_epoch_failed(chain, "run_failed")
                    return False
                return True
            epoch["notes"].append(f"設定の保存に失敗しました: {error}")
            self._finish_epoch_failed(chain, "run_failed")
            return False
        return True

    def _step_run(self, chain, epoch):
        cid = epoch["config_id"]
        if epoch["run_job_id"] is None:
            try:
                self.configs.get(cid)
            except FileNotFoundError:
                exists = False
            except ConfigError as error:
                if error.code != "not_found":
                    raise
                exists = False
            else:
                exists = True
            if not exists and not self._save_epoch_config(chain, epoch):
                return
            rid = f"{chain['chain_id']}-e{epoch['index']}-run"
            try:
                job, _created = self.jobs.submit(
                    {"request_id": rid, "kind": "evolve", "config_id": cid}, settings_path=self.settings_path)
            except ConfigError as error:
                if error.code == "conflict":
                    return
                epoch["notes"].append(f"実行の投入に失敗しました: {error}")
                self._finish_epoch_failed(chain, "run_failed")
                return
            epoch["run_job_id"] = job["job_id"]
            self._save(chain)
            return
        job = self._get_job(epoch["run_job_id"])
        if job is None:
            return
        if job["state"] not in worker.TERMINAL:
            return
        if job["state"] == "succeeded":
            epoch["run_id"] = job["run_id"]
            epoch["step"] = "propose"
            self._save(chain)
        elif job["state"] == "cancelled":
            self._save(chain, state="stopped")
        else:
            epoch["notes"].append(f"実行が失敗しました: {(job.get('error') or {}).get('code')}")
            self._finish_epoch_failed(chain, "run_failed")

    def _step_propose(self, chain, epoch):
        if epoch["propose_job_id"] is None:
            try:
                run_root = contained(self.configs.runs, epoch["run_id"])
                triggers = _triggers(contained(run_root, "world_demand.json"))
            except (ConfigError, OSError):
                triggers = []
            raw_index = next((i for i, t in enumerate(triggers)
                               if isinstance(t, dict) and t.get("verb") == "investigate"), None)
            if raw_index is None:
                epoch["notes"].append("需要なし")
                epoch["step"] = "retire"
                self._save(chain)
                return
            project = contained(self.configs.repo, "projects/" + chain["project_id"])
            active = self._read_active_patches(project)
            if active is None:
                return  # transient stack read failure -- retry this tick later
            if len(active) >= MAX_TOTAL_PATCHES:
                try:
                    withering = wither_candidates(self._usage(chain, epoch, active))
                except (OSError, ValueError, KeyError, TypeError):
                    withering = []
                if not withering:
                    epoch["notes"].append(f"適用中の拡張が上限（{MAX_TOTAL_PATCHES}）に達し、枯れ候補もありません")
                    epoch["step"] = "retire"
                    self._save(chain)
                    return
            attempt = epoch["propose_attempts"]
            pid = f"{chain['chain_id']}-e{epoch['index']}-propose{attempt}"
            try:
                job, _created = self.jobs.submit(
                    {"schema_version": 1, "request_id": pid, "kind": "world_patch", "config_id": epoch["config_id"],
                     "run_id": epoch["run_id"], "action": "propose", "trigger": raw_index},
                    settings_path=self.settings_path)
            except ConfigError as error:
                if error.code == "conflict":
                    return
                epoch["propose_attempts"] += 1
                if epoch["propose_attempts"] >= 2:
                    epoch["notes"].append(f"提案の投入に失敗しました: {error}")
                    self._finish_epoch_failed(chain, "propose_failed")
                else:
                    epoch["notes"].append(f"提案の投入に失敗しました（再試行します）: {error}")
                    self._save(chain)
                return
            epoch["propose_job_id"] = job["job_id"]
            epoch["trigger"] = raw_index
            self._save(chain)
            return
        job = self._get_job(epoch["propose_job_id"])
        if job is None:
            return
        if job["state"] not in worker.TERMINAL:
            return
        if job["state"] == "cancelled":
            self._save(chain, state="stopped")
            return
        progress = job.get("progress") or {}
        if job["state"] != "succeeded" or progress.get("step") != "done":
            epoch["propose_attempts"] += 1
            epoch["propose_job_id"] = None
            if epoch["propose_attempts"] >= 2:
                epoch["notes"].append(
                    f"提案に失敗しました: {progress.get('message') or (job.get('error') or {}).get('code')}")
                self._finish_epoch_failed(chain, "propose_failed")
            else:
                epoch["notes"].append("提案に失敗したため再試行します")
                self._save(chain)
            return
        epoch["patch_id"] = progress.get("patch_id")
        epoch["gate_status"] = progress.get("status")
        epoch["step"] = "approve"
        self._save(chain)

    def _get_job(self, jid):
        """jobs.get(jid), or None on a transient job-table lock conflict
        (R1, Opus review) -- the caller must treat None as "retry this tick
        later", not as the job having disappeared (a real not_found still
        raises, same as any other unrecoverable ConfigError)."""
        try:
            return self.jobs.get(jid)
        except ConfigError as error:
            if error.code == "conflict":
                return None
            raise

    def _blocked_by_other_job(self):
        """M2 (Opus review, plan C8): true if some job the JobStore's single-
        non-terminal-job slot -- unrelated to this chain's own (already
        terminal) run/propose jobs -- could still be writing to patches/
        (a manually submitted world_patch job, a legacy CLI/check run,
        ...). approve()/retire() never check this themselves (only the
        viewer's HTTP approve/reject/retire actions do, via
        _reject_running_job); the chain must check it itself before calling
        either. Conservative on a read failure: treat as blocked so the
        step retries next tick rather than risking a write race."""
        try:
            return any(job.get("state") not in worker.TERMINAL for job in self.jobs.list())
        except (ConfigError, OSError):
            return True

    def _read_active_patches(self, project):
        """approved_patches() taken under patch_lock (plan §1 C8), or None
        on a transient read/lock failure -- callers must treat None as
        "retry this tick later", never as "no patches are active"."""
        try:
            with patch_lock(project):
                return approved_patches(project)
        except (PatchError, OSError):
            return None

    def _step_approve(self, chain, epoch):
        if self._blocked_by_other_job():
            return
        if epoch["gate_status"] != "reviewable":
            epoch["notes"].append(f"承認できる状態ではありません（{epoch['gate_status']}）")
            epoch["step"] = "retire"
            self._save(chain)
            return
        if chain["approval"] == "manual":
            if chain["state"] != "waiting":
                self._save(chain, state="waiting")
            return
        project = contained(self.configs.repo, "projects/" + chain["project_id"])
        template = contained(self.configs.repo, "templates/" + chain["template_id"])
        gate_path = project / "patches" / "_proposed" / f"{epoch['patch_id']}.gate.json"
        try:
            gate = read_json(gate_path)
        except (OSError, ValueError):
            epoch["notes"].append("ゲート記録を読み込めません")
            epoch["step"] = "retire"
            self._save(chain)
            return
        from viewer.world_expansion_view import _reach_table  # avoid execution->viewer at import time
        table = _reach_table((gate.get("trial") or {}).get("pairs") or [])
        if table["悪化"] > table["改善"]:
            epoch["notes"].append(f"自動承認を見送りました（悪化{table['悪化']}>改善{table['改善']}）")
            epoch["step"] = "retire"
            self._save(chain)
            return
        reason = (f"エポック連鎖 {chain['chain_id']} 第{epoch['index'] + 1}エポックの自動承認: "
                  f"holdout 検査 reviewable（到達 成功維持{table['成功維持']}・改善{table['改善']}・悪化{table['悪化']}）")
        try:
            revision = approve(project, template, epoch["patch_id"], reason, repo_root=self.configs.repo)
        except PatchError as error:
            epoch["notes"].append(f"自動承認に失敗しました: {error}")
        else:
            epoch["approved_rev"] = revision["rev"]
            epoch["notes"].append("自動承認しました")
        epoch["step"] = "retire"
        self._save(chain)

    def _usage(self, chain, epoch, patches):
        protagonist = self.configs.get(epoch["config_id"])["preview"]["protagonist"]
        run_root = contained(self.configs.runs, epoch["run_id"])
        archive, _raw, _source = load_archive(run_root)
        return patch_usage(run_root, protagonist, patches, archive=archive)

    def _auto_retire(self, chain, epoch):
        """True on a normal completion (even if nothing was retired), False
        on a transient stack-read failure the caller should retry later."""
        project = contained(self.configs.repo, "projects/" + chain["project_id"])
        template = contained(self.configs.repo, "templates/" + chain["template_id"])
        run_root = contained(self.configs.runs, epoch["run_id"])
        active = self._read_active_patches(project)
        if active is None:
            return False
        if not active:
            return True
        frozen_world = yaml.safe_load(
            contained(run_root, f"inputs/projects/{chain['project_id']}/world.yaml").read_text(encoding="utf-8"))
        frozen_ids = set(_world_parent_rev(frozen_world))
        target = [p for p in active if p["id"] in frozen_ids]
        if not target:
            return True
        usage = self._usage(chain, epoch, target)
        candidates = set(wither_candidates(usage))
        if epoch.get("approved_rev"):
            candidates.discard(epoch.get("patch_id"))
        if not candidates:
            return True
        stack = read_stack(project)
        order = [r["patch_id"] for r in stack["revisions"]
                 if r.get("kind", "patch") != "retire" and r["patch_id"] in candidates]
        for pid in reversed(order):
            total = usage.get(pid, {}).get("elites_total", 0)
            reason = (f"エポック連鎖 {chain['chain_id']} 第{epoch['index'] + 1}エポックの自動淘汰: "
                      f"実験 {epoch['run_id']} の代表個体{total}体で強い使用0体")
            try:
                retire(project, template, pid, reason, experiment=run_root, usage=usage.get(pid))
            except PatchError as error:
                epoch["notes"].append(f"淘汰に失敗しました（{pid}）: {error}")
            else:
                epoch["retired"].append(pid)
                epoch["notes"].append(f"自動淘汰: {pid}")
        return True

    def _step_retire(self, chain, epoch):
        if chain["auto_retire"]:
            if self._blocked_by_other_job():
                return
            try:
                ok = self._auto_retire(chain, epoch)
            except (PatchError, OSError, ValueError, KeyError, TypeError) as error:
                epoch["notes"].append(f"自動淘汰に失敗しました: {error}")
            else:
                if not ok:
                    return  # transient read failure -- retry this whole step next tick
        idle = not epoch.get("approved_rev") and not epoch.get("retired")
        chain["idle_streak"] = (chain.get("idle_streak", 0) + 1) if idle else 0
        epoch["step"] = "done"
        epoch["finished_at"] = time.time()
        n = epoch["index"]
        if chain["idle_streak"] >= IDLE_EPOCHS:
            epoch["notes"].append("世界が変化しなくなりました")
            self._save(chain, state="completed")
            return
        if n + 1 >= chain["max_epochs"]:
            self._save(chain, state="completed")
            return
        chain["epochs"].append(_new_epoch(chain["chain_id"], n + 1))
        self._save(chain)
