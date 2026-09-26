"""CLI for world-expansion patch proposals, gates, and approval
(WB-WORLDGROW-001, stage 3b): propose (LLM draft + static gate + trial gate),
check (re-run the gates for an already-proposed patch), approve, reject, list.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.world import World
from execution.output_settings import read_output_settings
from gapengine import lineage
from gapengine import gpu_guard
from gapengine.synopsis import BACKENDS, GenerationError, generate_text, load_settings, _output_section
from gapengine.world_demand import build_report
from gapengine.world_patch import (
    ID_RE,
    PatchError,
    absolutize_references,
    apply_patch,
    apply_patches,
    approved_patches,
    patch_id_for,
    retired_patches,
    template_identifiers,
    trigger_is_proposable,
    validate_patch,
)
from gapengine.world_patch_contract import reachable_zones_from
from gapengine.world_patch_propose import (MAX_PROMPT_CHARS, build_prompt, check_proposal_rules,
                                           check_trigger_coverage, make_patch, parse_proposal)
from gapengine.world_patch_trial import run_trial, gate_status
from gapengine.world_patch import stack_head, verify_stack, read_stack
from gapengine.world_patch_usage import patch_usage, wither_candidates
from execution.provenance import ConfigError, atomic_json
from execution.world_patches import _check_parent_rev, patch_lock
from execution.world_patch_approval import approve, reject, reopen, repair, retire
from execution.world_patch_library import export_patch, import_patch

# Exceptions an LLM's malformed JSON/shape can realistically trigger while a
# proposal is parsed and gated (WB-WORLDGROW-001 R6): parse_proposal/make_patch/
# validate_patch/check_trigger_coverage never raise ValueError, but a caller
# building on top of them (this module) can still hit these from an unexpected
# shape slipping past a `.get()` chain -- treat that the same as a validation
# failure (a violation to fix on retry) instead of crashing the CLI.
_PROPOSAL_ERRORS = (ValueError, TypeError, KeyError, AttributeError)


def _resolve_ctx(experiment: Path, template_dir=None, repo_root=None):
    from viewer.data import RunRepository

    repository = RunRepository(experiment.parent)
    return repository, lineage._resolve_world_context(
        repository, experiment, template_dir=template_dir, repo_root=repo_root)


def _subject_ids(subjects_dir: Path) -> list[str]:
    ids = []
    for path in sorted(subjects_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            ids.append(data["id"])
    return ids


def _give_available(subjects_dir: Path) -> bool:
    """Whether any subject in this world can give_item -- if none can, give
    never fires and a proposal must not be allowed to spend its give budget
    on it (check_proposal_rules)."""
    for path in sorted(Path(subjects_dir).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        verbs = data.get("verbs") if isinstance(data, dict) else None
        if isinstance(verbs, list) and "give_item" in verbs:
            return True
    return False


def _load_report(experiment: Path) -> dict:
    path = experiment / "world_demand.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return build_report(experiment)


def _reachable_zones_for(ctx, base_world: dict, trigger: dict) -> set | None:
    """None for anything but a "blocked" trigger -- check_trigger_coverage
    treats None as "compute a conservative fallback from the trigger itself"
    (see its own docstring), so callers only need this for the one kind that
    actually needs engine/world reachability."""
    if trigger.get("kind") != "blocked":
        return None
    # R3: never raise on a malformed stuck_zones entry (expected shape is a
    # [name, count] pair) -- a non-conforming one is simply skipped.
    stuck_zones = [pair[0] for pair in (trigger.get("stuck_zones") or [])
                   if isinstance(pair, (list, tuple)) and len(pair) == 2 and isinstance(pair[0], str)]
    if not stuck_zones:
        return set()
    return reachable_zones_from(ctx["world_path"], ctx["subjects_dir"], base_world.get("protagonist"), stuck_zones)


def _zone_verbs(report: dict, zone: str) -> list:
    for entry in report.get("zones", []):
        if entry.get("zone") == zone:
            return entry.get("verbs", [])
    return []


def _added_summary(add: dict) -> str:
    parts = [f"{key}={len(value)}件" for key, value in add.items() if isinstance(value, list) and value]
    return "、".join(parts) if parts else "なし"


def _progress(args, **progress):
    """Best-effort job progress write (WB-WORLDGROW-001 stage 3b-3): a no-op
    for CLI-only use (no --job/--control given). The job runs propose/check
    as its owned child process (execution/worker.py's watch()), the same way
    execution/output_worker.py writes its own progress back via
    execution.worker.change() -- never lets a progress-write failure break
    propose/check itself."""
    if not getattr(args, "job", None) or not getattr(args, "control", None):
        return
    try:
        # Imported here, not at module top: execution.worker pulls in
        # ctypes.wintypes, which the plain CLI never needed.
        from execution.worker import change as job_change, read_job as job_read
        jobs = Path(args.control) / "jobs"
        folder = jobs / args.job
        nonce = job_read(jobs, folder)["nonce"]
        job_change(jobs, folder, nonce, progress=progress)
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        pass


def _gate(experiment, project, patch, ctx, subject_ids, *, skip_trial, max_runs,
          seeds_per_run, give_available, reserved=(), seed_set="exploration", template_dir=None,
          repo_root=None, progress=None):
    base_world = yaml.safe_load(Path(ctx["world_path"]).read_text(encoding="utf-8"))
    violations = validate_patch(base_world, patch, subject_ids=subject_ids, reserved=reserved,
                                        give_available=give_available)
    patch_trigger = patch.get("trigger", {}) if isinstance(patch.get("trigger"), dict) else {}
    violations += check_trigger_coverage(patch.get("add", {}), patch_trigger, world=base_world,
                                         reachable_zones=_reachable_zones_for(ctx, base_world, patch_trigger))
    violations += check_proposal_rules(patch.get("add", {}), give_available=give_available)
    gate = {"schema_version": 2, "patch_id": patch["id"],
            "static": {"passed": not violations, "violations": violations}, "trial": None,
            "passed": False, "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if not violations and not skip_trial:
        if progress is not None:
            progress(step="holdout" if seed_set == "holdout" else "trial")
        with tempfile.TemporaryDirectory() as tmp:
            # N2 (WB-WORLDGROW-001, Astra review): forward --repo here too --
            # `ctx` above was already resolved with it, but run_trial used to
            # re-resolve everything itself and silently drop back to this
            # repo's own default when repo_root wasn't threaded this far.
            gate["trial"] = run_trial(experiment, patch, work_dir=Path(tmp), template_dir=template_dir,
                                     repo_root=repo_root, max_runs=max_runs, seeds_per_run=seeds_per_run,
                                     seed_set=seed_set)
    gate["status"] = gate_status(gate)
    return gate


def _save_gate(project, patch_path, raw, gate):
    with patch_lock(project):
        if patch_path.read_bytes() != raw:
            raise PatchError("検査中にパッチが変わりました。check をやり直してください")
        path = patch_path.with_suffix(".gate.json")
        previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        holdout = (gate.get("trial") or {}).get("evidence", {}).get("seed_set") == "holdout"
        gate["holdout_checks"] = previous.get("holdout_checks", 0) + int(holdout)
        gate["patch_sha256"] = hashlib.sha256(raw).hexdigest()
        atomic_json(path, gate)


# Optional (WB-WORLDGROW-001 review): Japanese explanation for every
# non-reviewable status, so the CLI's last line always says why a patch
# can't be approved yet instead of only ever printing the reviewable caveat.
_STATUS_EXPLANATIONS = {
    "static_failed": "静的ゲートの違反",
    "trial_pending": "試走がまだ",
    "contract_failed": "契約検査の違反",
    "reference_only": "参考試走のみ（凍結入力なし・engine不一致・再現確認なし）",
    "insufficient": "試走の規模が足りません（個体3以上・seed4以上が必要）",
}


def _print_gate_summary(patch, gate):
    print(f"題: {patch.get('title')} / status: {gate.get('status')}")
    print(f"理由: {patch.get('rationale')}")
    print(f"足したもの: {_added_summary(patch.get('add', {}))}")
    for reason in gate["static"].get("violations", []):
        print(f"  - {reason}")
    trial = gate.get("trial") or {}
    for reason in trial.get("reasons", []):
        print(f"  - {reason}")
    for key in ("reproduction", "contract", "new_usage", "errors"):
        if key in trial:
            print(f"{key}: {json.dumps(trial[key], ensure_ascii=False)}")
    # Optional (WB-WORLDGROW-001 review): a 2x2 到達 transition table from
    # the actual paired runs -- the aggregate reached_base/reached_patched
    # totals can be unchanged while individuals still flipped both ways.
    pairs = trial.get("pairs") or []
    if pairs:
        table = {"成功維持": 0, "悪化": 0, "改善": 0, "失敗維持": 0}
        for pair in pairs:
            before, after = bool(pair["base"]["reached"]), bool(pair["patched"]["reached"])
            table["成功維持" if before and after else "悪化" if before else
                  "改善" if after else "失敗維持"] += 1
        print("到達の変化: " + "、".join(f"{label}={count}" for label, count in table.items()))
    for entry in trial.get("by_individual", []):
        print(f"  個体差 {entry['cell']}: {entry['mean_diff']:+.2f}")
    for entry in trial.get("by_seed", []):
        print(f"  seed差 {entry['seed']}: {entry['mean_diff']:+.2f}")
    print(f"holdout_checks: {gate.get('holdout_checks', 0)}")
    status = gate.get("status")
    if status == "reviewable":
        print("reviewable は人が検討できる状態です。統計的な合格ではありません")
    else:
        print(f"承認できる状態ではありません: {_STATUS_EXPLANATIONS.get(status, status)}")


def _retry_prompt(original_prompt: str, last_proposal_text: str | None,
                   last_response_text: str, violations: list[str]) -> str:
    """Rebuild the retry prompt from `original_prompt` fresh each attempt
    (WB-WORLDGROW-001 R9) instead of appending onto an ever-growing prompt:
    the original prompt, a quote of the last attempt (its parsed JSON, or --
    when parsing itself failed -- the first 1500 chars of the raw response),
    and only *this* attempt's violations (never a cumulative list)."""
    quote_source = last_proposal_text if last_proposal_text is not None else (last_response_text or "")[:1500]
    header = "\n\n# 前回の提案\n"
    footer = ("\n\n前回の提案は次の理由で不採用でした: " + "、".join(violations)
              + "。これらを直したJSONを出力してください。")
    budget = max(MAX_PROMPT_CHARS - len(original_prompt) - len(header) - len(footer), 0)
    quote = quote_source if len(quote_source) <= budget else quote_source[:budget]
    return original_prompt + header + quote + footer


def cmd_propose(args: argparse.Namespace) -> int:
    experiment = args.experiment.resolve()
    project = args.project.resolve()
    report = _load_report(experiment)
    # WB-WORLDGROW-002 S2: `--trigger` is now the raw index into
    # world_demand.json's triggers (every kind, every verb) -- the same
    # numbering the UI's data-trigger already used (viewer/world_demand_view.py).
    # execution/world_patch_job.py's prepare() forwards the UI's raw index
    # here unchanged; no more investigate-only re-numbering in between.
    triggers = report.get("triggers", [])
    progress = lambda **p: _progress(args, **p)
    if not (0 <= args.trigger < len(triggers)) or not trigger_is_proposable(triggers[args.trigger]):
        print("対応できる需要がありません")
        progress(step="failed", message="対応できる需要がありません")
        return 1
    trigger = dict(triggers[args.trigger])
    trigger["experiment"] = experiment.name

    repository, ctx = _resolve_ctx(experiment, args.template, repo_root=args.repo)
    base_world = yaml.safe_load(Path(ctx["world_path"]).read_text(encoding="utf-8"))
    mismatch = _check_parent_rev(base_world, project)
    if mismatch:
        print(mismatch)
        progress(step="failed", message=mismatch)
        return 1
    with patch_lock(project):
        parent_digest = stack_head(project)

    subject_ids = _subject_ids(Path(ctx["subjects_dir"]))
    give_available = _give_available(Path(ctx["subjects_dir"]))
    zone_verbs = _zone_verbs(report, trigger["zone"]) if "zone" in trigger else []
    reachable_zones = _reachable_zones_for(ctx, base_world, trigger)
    reserved = template_identifiers(ctx['template_dir'])
    try:
        prompt = build_prompt(base_world, subject_ids, trigger, zone_verbs, give_available=give_available,
                              reachable_zones=reachable_zones)
    except ValueError as error:
        print(str(error))
        progress(step="failed", message=str(error))
        return 1

    if args.from_file:
        max_attempts = 1
        backend_name = "file"
    else:
        backend_name = args.backend or read_output_settings(args.settings)["backend"]
        if backend_name == "none":
            print(prompt)
            return 0
        max_attempts = args.retries + 1

    author = {"backend": backend_name, "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}

    current_prompt = prompt
    patch: dict = {}
    violations: list[str] = []
    # One GPU session around every attempt: generate_text opens its own per
    # call, and a session that has to launch llama-server also stops it on
    # exit -- so without this, each regeneration paid the model load (about
    # two minutes) again. The nested sessions inside generate_text are no-ops
    # while this one is held.
    session = contextlib.ExitStack()
    if backend_name in ("llama-server", "ollama"):
        settings, _warning = load_settings(args.settings)
        try:
            session.enter_context(gpu_guard.local_gpu_session(
                backend_name, _output_section(settings), owner="world_patch", wait_seconds=900))
        except (gpu_guard.GpuBusy, RuntimeError) as error:
            message = f"生成できませんでした: {error}"
            print(message)
            progress(step="failed", message=message)
            return 2
    with session:
        for attempt in range(1, max_attempts + 1):
            progress(step="generate", attempt=attempt, attempts=max_attempts)
            if args.from_file:
                response_text = Path(args.from_file).read_text(encoding="utf-8")
            else:
                try:
                    # 900s, not the 600s default: a 27B local model measured 350-580s
                    # on this prompt, and the first call after a cold start ran over.
                    result = generate_text(backend_name, current_prompt, settings_path=args.settings,
                                           timeout=900)
                except GenerationError as error:
                    # e.g. the GPU lease is held by another run; nothing was written.
                    message = f"生成できませんでした: {error}"
                    print(message)
                    progress(step="failed", message=message)
                    return 2
                if result.status != "ok":
                    message = f"生成に失敗しました: status={result.status} warning={result.warning}"
                    print(message)
                    progress(step="failed", message=message)
                    return 1
                response_text = result.text

            last_proposal_text = None
            try:
                proposal = parse_proposal(response_text)
                last_proposal_text = json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=False)
                patch = make_patch(proposal, trigger=trigger, parent_digest=parent_digest, author=author)
                violations = validate_patch(base_world, patch, subject_ids=subject_ids, reserved=reserved,
                                            give_available=give_available)
                violations += check_trigger_coverage(patch["add"], trigger, world=base_world,
                                                     reachable_zones=reachable_zones)
                violations += check_proposal_rules(patch["add"], give_available=give_available)
            except _PROPOSAL_ERRORS as error:
                violations = [f"提案の形式を検査できませんでした: {error}"]
                patch = make_patch(
                    {"title": "(解析失敗)", "rationale": str(error)[:300], "add": {}},
                    trigger=trigger, parent_digest=parent_digest, author=author,
                )

            if not violations or args.from_file or attempt == max_attempts:
                break
            current_prompt = _retry_prompt(prompt, last_proposal_text, response_text, violations)

    proposed_dir = project / "patches" / "_proposed"
    raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
    with patch_lock(project):
        if stack_head(project) != parent_digest:
            raise PatchError("提案中に承認スタックが変わりました")
        proposed_dir.mkdir(parents=True, exist_ok=True)
        patch_path = proposed_dir / f"{patch['id']}.yaml"
        patch_path.write_bytes(raw)
    gate = _gate(experiment, project, patch, ctx, subject_ids, skip_trial=args.skip_trial,
                 max_runs=args.max_runs, seeds_per_run=args.seeds_per_run, reserved=reserved,
                 seed_set="exploration", template_dir=args.template, repo_root=args.repo,
                 give_available=give_available, progress=progress)
    _save_gate(project, patch_path, raw, gate)
    # Only the exploration-gated run above ever regenerates the patch on a
    # violation; --then-holdout re-gates the same already-saved patch against
    # holdout seeds -- never spends holdout seeds/CPU on a patch that isn't
    # reviewable yet (static_failed/contract_failed/insufficient/trial_pending/
    # reference_only all skip it).
    if args.then_holdout and not args.skip_trial and gate["status"] == "reviewable":
        gate = _gate(experiment, project, patch, ctx, subject_ids, skip_trial=args.skip_trial,
                     max_runs=args.max_runs, seeds_per_run=args.seeds_per_run, reserved=reserved,
                     seed_set="holdout", template_dir=args.template, repo_root=args.repo,
                     give_available=give_available, progress=progress)
        _save_gate(project, patch_path, raw, gate)
    _print_gate_summary(patch, gate)
    progress(step="done", patch_id=patch["id"], status=gate["status"])
    if args.job:
        return 0
    return 1 if gate["status"] in ("static_failed", "contract_failed") else 0


def cmd_check(args):
    progress = lambda **p: _progress(args, **p)
    if not ID_RE.fullmatch(args.patch):
        message = "パッチ ID の形式が不正です"
        progress(step="failed", message=message)
        raise PatchError(message)
    experiment, project = args.experiment.resolve(), args.project.resolve()
    _, ctx = _resolve_ctx(experiment, args.template, repo_root=args.repo)
    path = project / "patches" / "_proposed" / f"{args.patch}.yaml"
    with patch_lock(project):
        raw = path.read_bytes()
        patch = yaml.safe_load(raw)
        if patch.get("parent_digest") != stack_head(project):
            message = "parent_digest が現在のスタックと一致しません"
            progress(step="failed", message=message)
            raise PatchError(message)
    world = yaml.safe_load(Path(ctx["world_path"]).read_text(encoding="utf-8"))
    mismatch = _check_parent_rev(world, project)
    if mismatch:
        progress(step="failed", message=mismatch)
        raise PatchError(mismatch)
    gate = _gate(experiment, project, patch, ctx, _subject_ids(ctx["subjects_dir"]),
                 skip_trial=args.skip_trial, max_runs=args.max_runs, seeds_per_run=args.seeds_per_run,
                 seed_set=args.seed_set, template_dir=args.template,
                 reserved=template_identifiers(ctx["template_dir"]), repo_root=args.repo,
                 give_available=_give_available(ctx["subjects_dir"]), progress=progress)
    _save_gate(project, path, raw, gate)
    _print_gate_summary(patch, gate)
    progress(step="done", patch_id=patch["id"], status=gate["status"])
    if args.job:
        return 0
    return 1 if gate["status"] in ("static_failed", "contract_failed") else 0


def cmd_approve(args):
    revision = approve(args.project.resolve(), args.template.resolve(), args.patch, args.reason,
                        repo_root=args.repo)
    print(f"承認しました: {args.patch}（rev={revision['rev']}）")
    return 0


def cmd_reject(args):
    reject(args.project.resolve(), args.patch)
    return 0


def _protagonist(project: Path) -> str:
    world = yaml.safe_load((project / "world.yaml").read_text(encoding="utf-8"))
    return world["protagonist"]


def _usage_summary(usage: dict, candidates: list[str]) -> list[str]:
    lines = []
    for patch_id, counts in usage.items():
        mark = "（枯れ候補）" if patch_id in candidates else ""
        lines.append(f"  {patch_id}: 代表個体{counts['elites_total']}体中 強い使用{counts['elites_strong']}体・"
                      f"弱い使用{counts['elites_weak']}体{mark}")
    return lines


def cmd_retire(args):
    """WB-WORLDGROW-001 段階5a: wither an already-approved patch. --experiment
    supplies the representative individuals patch_usage() measures use
    against; the measured table is shown and written into retire.json
    verbatim (execution.world_patch_approval.retire's `usage` argument)."""
    project, template, experiment = args.project.resolve(), args.template.resolve(), args.experiment.resolve()
    with patch_lock(project):
        active = approved_patches(project)
    usage = patch_usage(experiment, _protagonist(project), active)
    candidates = wither_candidates(usage)
    for line in _usage_summary(usage, candidates):
        print(line)
    try:
        revision = retire(project, template, args.patch, args.reason, experiment=experiment,
                          usage=usage.get(args.patch))
    except PatchError as error:
        print(str(error))
        return 1
    print(f"枯らしました: {args.patch}（rev={revision['rev']}）")
    return 0


_GENRE_PATH = re.compile(r"^templates/([A-Za-z0-9][A-Za-z0-9_-]{0,95})/")


def _verify_template_matches_world(project: Path, template: Path) -> None:
    """R7 (Opus review): --template must actually be this world's own genre
    -- library assets are genre-scoped (execution.world_patch_library.
    library_dir lives under the template, not the world), so exporting to or
    importing from the wrong --template would silently mix genres. Mirrors
    execution.library.LibraryStore._genre_of's own reading of world.yaml's
    gapengine.action_graph path."""
    world_path = project / "world.yaml"
    if not world_path.is_file():
        raise PatchError(f"世界の設定を読み込めません: {world_path}")
    world = yaml.safe_load(world_path.read_text(encoding="utf-8"))
    graph = (world.get("gapengine") or {}).get("action_graph") if isinstance(world, dict) else None
    genre = None
    if isinstance(graph, str):
        match = _GENRE_PATH.match(graph.replace("\\", "/"))
        if match:
            genre = match.group(1)
    if genre is not None and genre != template.name:
        raise PatchError(f"--template（{template.name}）がこの世界のジャンル（{genre}）と一致しません")


def cmd_export(args):
    """WB-WORLDGROW-001 段階5d: publish an applied, strongly-used patch as a
    genre asset. Usage is measured here (server/CLI side), never taken from
    the caller -- same principle as cmd_retire's own usage table."""
    project, template, experiment = args.project.resolve(), args.template.resolve(), args.experiment.resolve()
    try:
        _verify_template_matches_world(project, template)
        protagonist = _protagonist(project)
        with patch_lock(project):
            active = approved_patches(project)
        usage = patch_usage(experiment, protagonist, active)
        counts = usage.get(args.patch)
        for line in _usage_summary(usage, wither_candidates(usage)):
            print(line)
        path = export_patch(project, template, args.patch, experiment=experiment,
                            protagonist=protagonist, usage=counts)
    except (PatchError, ConfigError) as error:
        print(str(error))
        return 1
    print(f"ジャンルの資産にしました: {args.patch} -> {path}")
    print("git への追加は手動で行ってください。")
    return 0


def cmd_import(args):
    """WB-WORLDGROW-001 段階5d: stage a genre asset as a new proposal in
    this world (trial_pending -- check/approve must still run here)."""
    project, template = args.project.resolve(), args.template.resolve()
    try:
        _verify_template_matches_world(project, template)
        rewritten = import_patch(project, template, args.entry, repo_root=args.repo)
    except (PatchError, ConfigError) as error:
        print(str(error))
        return 1
    print(f"取り込みました: {args.entry} -> patches/_proposed/{args.entry}.yaml")
    print(f"きっかけの場所: {rewritten['trigger']['zone']}")
    return 0


def cmd_usage(args):
    """WB-WORLDGROW-001 段階5a, 読み取り専用: 適用中パッチごとの使用表と枯れ候補。"""
    project, experiment = args.project.resolve(), args.experiment.resolve()
    with patch_lock(project):
        active = approved_patches(project)
    if not active:
        print("適用中のパッチはありません")
        return 0
    usage = patch_usage(experiment, _protagonist(project), active)
    candidates = wither_candidates(usage)
    for line in _usage_summary(usage, candidates):
        print(line)
    return 0


def cmd_list(args):
    project = args.project.resolve()
    with patch_lock(project):
        verified = verify_stack(project)
        stack = read_stack(project)
        approval_by_id = {r["patch_id"]: r for r in stack["revisions"] if r.get("kind", "patch") == "patch"}
        print("承認済み:")
        for patch, _raw in verified:
            revision = approval_by_id.get(patch["id"], {})
            reason = (revision.get("approval") or {}).get("reason")
            print(f"  rev={revision.get('rev')} {patch['id']} {patch.get('title')} / {reason}")
        retired = retired_patches(project)
        if retired:
            print("枯れた拡張:")
            for entry in retired:
                print(f"  rev={entry['rev']} {entry['patch']['id']} {entry['patch'].get('title')} / {entry['retire'].get('reason')}")
        print("提案中:")
        for path in sorted((project / "patches" / "_proposed").glob("*.yaml")):
            patch = yaml.safe_load(path.read_text(encoding="utf-8"))
            gate_path = path.with_suffix(".gate.json")
            gate = json.loads(gate_path.read_text(encoding="utf-8")) if gate_path.is_file() else {}
            reasons = gate.get("static", {}).get("violations") or gate.get("trial", {}).get("reasons", []) if gate.get("trial") else gate.get("static", {}).get("violations", [])
            print(f"  {patch.get('id')}: {patch.get('title')} / {gate.get('status', 'trial_pending')} / {(reasons or [''])[0]}")
    return 0


def cmd_reopen(args):
    print("再検査に戻しました: " + ", ".join(reopen(args.project.resolve())))
    # Optional (WB-WORLDGROW-001 review): rev2 以降は parent_digest が古い
    # head のままなので、まとめて再承認はできない。
    print("元の順序どおり、1枚ずつ check → approve をやり直してください。")
    print("基準の世界を変えた場合や2枚目以降は、その時点の世界で回した実験が再checkに必要です。")
    return 0


def cmd_repair(args):
    print("回復しました: " + ", ".join(repair(args.project.resolve())))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="World-expansion patch proposals, gates, and approval.")
    sub = parser.add_subparsers(dest="command", required=True)

    propose = sub.add_parser("propose")
    propose.add_argument("--experiment", type=Path, required=True)
    propose.add_argument("--project", type=Path, required=True)
    propose.add_argument("--trigger", type=int, default=0,
                         help="world_demand.json の triggers の生の番号（画面の提案ボタンのdata-triggerと同じ）。既定0")
    propose.add_argument("--backend", choices=BACKENDS)
    propose.add_argument("--settings", type=Path, default=ROOT / "settings.json")
    propose.add_argument("--from-file", type=Path)
    propose.add_argument("--skip-trial", action="store_true")
    propose.add_argument("--max-runs", type=int, default=5)
    propose.add_argument("--seeds-per-run", type=int, default=8)
    propose.add_argument("--retries", type=int, default=2)
    propose.add_argument("--template", type=Path)
    # R4: default None keeps resolve_experiment_inputs's own repo default;
    # only needed when the project/template referenced by the experiment
    # live outside this repo (e.g. under a control-side repo copy).
    propose.add_argument("--repo", type=Path)
    # WB-WORLDGROW-001 stage 3b-3: --then-holdout re-gates a reviewable
    # proposal against holdout seeds right after its exploration gate passes
    # (see _gate's caller in cmd_propose) -- unused by direct CLI/test
    # callers, which keep checking exploration or holdout explicitly via a
    # separate `check` call. --control/--job are optional: given only when
    # this run is a world_patch job's owned child (execution/worker.py),
    # so progress can be written back (see _progress); CLI-only use leaves
    # them unset and _progress becomes a no-op.
    propose.add_argument("--then-holdout", action="store_true")
    propose.add_argument("--control", type=Path)
    propose.add_argument("--job")
    propose.set_defaults(func=cmd_propose)

    check = sub.add_parser("check")
    check.add_argument("--experiment", type=Path, required=True)
    check.add_argument("--project", type=Path, required=True)
    check.add_argument("--patch", required=True)
    check.add_argument("--skip-trial", action="store_true")
    check.add_argument("--max-runs", type=int, default=5)
    check.add_argument("--seeds-per-run", type=int, default=8)
    check.add_argument("--template", type=Path)
    check.add_argument("--repo", type=Path)
    check.add_argument("--seed-set", choices=("exploration", "holdout"), default="holdout")
    check.add_argument("--control", type=Path)
    check.add_argument("--job")
    check.set_defaults(func=cmd_check)

    approve = sub.add_parser("approve")
    approve.add_argument("--project", type=Path, required=True)
    approve.add_argument("--patch", required=True)
    approve.add_argument("--template", type=Path, required=True)
    approve.add_argument("--reason", required=True)
    approve.add_argument("--repo", type=Path)
    approve.set_defaults(func=cmd_approve)

    reject = sub.add_parser("reject")
    reject.add_argument("--project", type=Path, required=True)
    reject.add_argument("--patch", required=True)
    reject.set_defaults(func=cmd_reject)

    retire_cmd = sub.add_parser("retire")
    retire_cmd.add_argument("--project", type=Path, required=True)
    retire_cmd.add_argument("--template", type=Path, required=True)
    retire_cmd.add_argument("--patch", required=True)
    retire_cmd.add_argument("--reason", required=True)
    retire_cmd.add_argument("--experiment", type=Path, required=True)
    retire_cmd.set_defaults(func=cmd_retire)

    usage_cmd = sub.add_parser("usage")
    usage_cmd.add_argument("--project", type=Path, required=True)
    usage_cmd.add_argument("--experiment", type=Path, required=True)
    usage_cmd.set_defaults(func=cmd_usage)

    export_cmd = sub.add_parser("export")
    export_cmd.add_argument("--project", type=Path, required=True)
    export_cmd.add_argument("--template", type=Path, required=True)
    export_cmd.add_argument("--patch", required=True)
    export_cmd.add_argument("--experiment", type=Path, required=True)
    export_cmd.set_defaults(func=cmd_export)

    import_cmd = sub.add_parser("import")
    import_cmd.add_argument("--project", type=Path, required=True)
    import_cmd.add_argument("--template", type=Path, required=True)
    import_cmd.add_argument("--entry", required=True)
    import_cmd.add_argument("--repo", type=Path)
    import_cmd.set_defaults(func=cmd_import)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--project", type=Path, required=True)
    list_cmd.set_defaults(func=cmd_list)

    for name, func in (("reopen", cmd_reopen), ("repair", cmd_repair)):
        command = sub.add_parser(name)
        command.add_argument("--project", type=Path, required=True)
        command.set_defaults(func=func)
    return parser


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


@contextlib.contextmanager
def _job_log(args):
    """With --job/--control, also copy everything printed to
    <control>/jobs/<job>/scratch/world_patch.log: the job worker discards the
    child's stdout, so this is the only record of why an attempt was sent back.
    A no-op (and never an error) for plain CLI use."""
    job, control = getattr(args, "job", None), getattr(args, "control", None)
    if not job or not control:
        yield
        return
    try:
        folder = Path(control) / "jobs" / job / "scratch"
        folder.mkdir(parents=True, exist_ok=True)
        log = open(folder / "world_patch.log", "a", encoding="utf-8")
    except OSError:
        yield
        return
    original = sys.stdout
    sys.stdout = _Tee(original, log)
    try:
        yield
    finally:
        sys.stdout = original
        log.close()


def main(argv: Sequence[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(arguments)
    except SystemExit as error:
        # Approval prerequisites, including missing required options, use 1.
        # Preserve normal argparse help and other commands' usage behavior.
        if error.code == 2 and arguments[:1] == ["approve"]:
            return 1
        raise
    with _job_log(args):
        try:
            return args.func(args)
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
            message = f"処理できません: {error}"
            print(message)
            # A safety net for any propose/check failure that raises instead of
            # printing+returning (e.g. cmd_check's PatchErrors, or cmd_propose's
            # "提案中に承認スタックが変わりました") -- _progress is a no-op
            # without --job, so this never affects direct CLI/test use.
            _progress(args, step="failed", message=message)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
