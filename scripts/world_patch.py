"""CLI for world-expansion patch proposals, gates, and approval
(WB-WORLDGROW-001, stage 3b): propose (LLM draft + static gate + trial gate),
check (re-run the gates for an already-proposed patch), approve, reject, list.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
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
from gapengine.synopsis import BACKENDS, GenerationError, generate_text
from gapengine.world_demand import build_report
from gapengine.world_patch import (
    ID_RE,
    PatchError,
    absolutize_references,
    apply_patch,
    apply_patches,
    approved_patches,
    patch_id_for,
    template_identifiers,
    validate_patch,
)
from gapengine.world_patch_propose import MAX_PROMPT_CHARS, build_prompt, check_trigger_coverage, make_patch, parse_proposal
from gapengine.world_patch_trial import run_trial, gate_status
from gapengine.world_patch import stack_head, verify_stack, read_stack
from execution.provenance import atomic_json
from execution.world_patches import patch_lock
from execution.world_patch_approval import approve, reopen, repair

# Exceptions an LLM's malformed JSON/shape can realistically trigger while a
# proposal is parsed and gated (WB-WORLDGROW-001 R6): parse_proposal/make_patch/
# validate_patch/check_trigger_coverage never raise ValueError, but a caller
# building on top of them (this module) can still hit these from an unexpected
# shape slipping past a `.get()` chain -- treat that the same as a validation
# failure (a violation to fix on retry) instead of crashing the CLI.
_PROPOSAL_ERRORS = (ValueError, TypeError, KeyError, AttributeError)


def _resolve_ctx(experiment: Path, template_dir=None):
    from viewer.data import RunRepository

    repository = RunRepository(experiment.parent)
    return repository, lineage._resolve_world_context(repository, experiment, template_dir=template_dir)


def _subject_ids(subjects_dir: Path) -> list[str]:
    ids = []
    for path in sorted(subjects_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            ids.append(data["id"])
    return ids


def _load_report(experiment: Path) -> dict:
    path = experiment / "world_demand.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return build_report(experiment)


def _investigate_triggers(report: dict) -> list[dict]:
    return [t for t in report.get("triggers", []) if t.get("verb") == "investigate"]


def _zone_verbs(report: dict, zone: str) -> list:
    for entry in report.get("zones", []):
        if entry.get("zone") == zone:
            return entry.get("verbs", [])
    return []


def _added_summary(add: dict) -> str:
    parts = [f"{key}={len(value)}件" for key, value in add.items() if isinstance(value, list) and value]
    return "、".join(parts) if parts else "なし"


def _world_parent_rev(base_world: dict) -> list:
    return [p["id"] for p in (base_world.get("expansion") or {}).get("patches", [])]


def _check_parent_rev(base_world: dict, project: Path) -> str | None:
    """None = ok; otherwise a Japanese error message. Both propose and check
    must refuse to gate a patch against a world state the experiment never
    actually ran under."""
    try:
        with patch_lock(project):
            current_approved = approved_patches(project)
    except PatchError as error:
        return f"承認済みパッチの読み込みに失敗しました: {error}"
    if _world_parent_rev(base_world) != [p["id"] for p in current_approved]:
        return "この実験は現在の承認済み拡張とは別の版の世界で回っています"
    return None


def _gate(experiment, project, patch, ctx, subject_ids, *, skip_trial, max_runs,
          seeds_per_run, reserved=(), seed_set="exploration", template_dir=None):
    base_world = yaml.safe_load(Path(ctx["world_path"]).read_text(encoding="utf-8"))
    violations = validate_patch(base_world, patch, subject_ids=subject_ids, reserved=reserved)
    violations += check_trigger_coverage(patch.get("add", {}), patch.get("trigger", {}))
    gate = {"schema_version": 2, "patch_id": patch["id"],
            "static": {"passed": not violations, "violations": violations}, "trial": None,
            "passed": False, "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if not violations and not skip_trial:
        with tempfile.TemporaryDirectory() as tmp:
            gate["trial"] = run_trial(experiment, patch, work_dir=Path(tmp), template_dir=template_dir,
                                     max_runs=max_runs, seeds_per_run=seeds_per_run, seed_set=seed_set)
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
    print(f"holdout_checks: {gate.get('holdout_checks', 0)}")
    print("reviewable は人が検討できる状態です。統計的な合格ではありません")


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
    triggers = _investigate_triggers(report)
    if args.trigger >= len(triggers):
        print("対応できる需要がありません")
        return 1
    trigger = dict(triggers[args.trigger])
    trigger["experiment"] = experiment.name

    repository, ctx = _resolve_ctx(experiment, args.template)
    base_world = yaml.safe_load(Path(ctx["world_path"]).read_text(encoding="utf-8"))
    mismatch = _check_parent_rev(base_world, project)
    if mismatch:
        print(mismatch)
        return 1
    with patch_lock(project):
        parent_digest = stack_head(project)

    subject_ids = _subject_ids(Path(ctx["subjects_dir"]))
    zone_verbs = _zone_verbs(report, trigger["zone"])
    reserved = template_identifiers(ctx['template_dir'])
    try:
        prompt = build_prompt(base_world, subject_ids, trigger, zone_verbs)
    except ValueError as error:
        print(str(error))
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
    for attempt in range(1, max_attempts + 1):
        if args.from_file:
            response_text = Path(args.from_file).read_text(encoding="utf-8")
        else:
            try:
                result = generate_text(backend_name, current_prompt, settings_path=args.settings)
            except GenerationError as error:
                # e.g. the GPU lease is held by another run; nothing was written.
                print(f"生成できませんでした: {error}")
                return 2
            if result.status != "ok":
                print(f"生成に失敗しました: status={result.status} warning={result.warning}")
                return 1
            response_text = result.text

        last_proposal_text = None
        try:
            proposal = parse_proposal(response_text)
            last_proposal_text = json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=False)
            patch = make_patch(proposal, trigger=trigger, parent_digest=parent_digest, author=author)
            violations = validate_patch(base_world, patch, subject_ids=subject_ids, reserved=reserved)
            violations += check_trigger_coverage(patch["add"], trigger)
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
                 seed_set="exploration", template_dir=args.template)
    _save_gate(project, patch_path, raw, gate)
    _print_gate_summary(patch, gate)
    return 1 if gate["status"] in ("static_failed", "contract_failed") else 0


def cmd_check(args):
    if not ID_RE.fullmatch(args.patch):
        raise PatchError("パッチ ID の形式が不正です")
    experiment, project = args.experiment.resolve(), args.project.resolve()
    _, ctx = _resolve_ctx(experiment, args.template)
    path = project / "patches" / "_proposed" / f"{args.patch}.yaml"
    with patch_lock(project):
        raw = path.read_bytes()
        patch = yaml.safe_load(raw)
        if patch.get("parent_digest") != stack_head(project):
            raise PatchError("parent_digest が現在のスタックと一致しません")
    world = yaml.safe_load(Path(ctx["world_path"]).read_text(encoding="utf-8"))
    mismatch = _check_parent_rev(world, project)
    if mismatch:
        raise PatchError(mismatch)
    gate = _gate(experiment, project, patch, ctx, _subject_ids(ctx["subjects_dir"]),
                 skip_trial=args.skip_trial, max_runs=args.max_runs, seeds_per_run=args.seeds_per_run,
                 seed_set=args.seed_set, template_dir=args.template,
                 reserved=template_identifiers(ctx["template_dir"]))
    _save_gate(project, path, raw, gate)
    _print_gate_summary(patch, gate)
    return 1 if gate["status"] in ("static_failed", "contract_failed") else 0


def cmd_approve(args):
    revision = approve(args.project.resolve(), args.template.resolve(), args.patch, args.reason)
    print(f"承認しました: {args.patch}（rev={revision['rev']}）")
    return 0


def cmd_reject(args):
    if not ID_RE.fullmatch(args.patch):
        raise PatchError("パッチ ID の形式が不正です")
    project = args.project.resolve()
    with patch_lock(project):
        proposed = project / "patches" / "_proposed"
        destination = project / "patches" / "_rejected"
        files = [proposed / f"{args.patch}{suffix}" for suffix in (".yaml", ".gate.json")]
        files = [p for p in files if p.is_file()]
        if not files:
            raise PatchError("提案が見つかりません")
        if any((destination / p.name).exists() for p in files):
            raise PatchError("却下済みの同名ファイルがあります")
        destination.mkdir(exist_ok=True)
        for path in files:
            shutil.move(str(path), str(destination / path.name))
    return 0


def cmd_list(args):
    project = args.project.resolve()
    with patch_lock(project):
        verified = verify_stack(project)
        stack = read_stack(project)
        print("承認済み:")
        for (patch, _), revision in zip(verified, stack["revisions"]):
            print(f"  rev={revision['rev']} {patch['id']} {patch.get('title')} / {revision['approval']['reason']}")
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
    propose.add_argument("--trigger", type=int, default=0)
    propose.add_argument("--backend", choices=BACKENDS)
    propose.add_argument("--settings", type=Path, default=ROOT / "settings.json")
    propose.add_argument("--from-file", type=Path)
    propose.add_argument("--skip-trial", action="store_true")
    propose.add_argument("--max-runs", type=int, default=5)
    propose.add_argument("--seeds-per-run", type=int, default=8)
    propose.add_argument("--retries", type=int, default=2)
    propose.add_argument("--template", type=Path)
    propose.set_defaults(func=cmd_propose)

    check = sub.add_parser("check")
    check.add_argument("--experiment", type=Path, required=True)
    check.add_argument("--project", type=Path, required=True)
    check.add_argument("--patch", required=True)
    check.add_argument("--skip-trial", action="store_true")
    check.add_argument("--max-runs", type=int, default=5)
    check.add_argument("--seeds-per-run", type=int, default=8)
    check.add_argument("--template", type=Path)
    check.add_argument("--seed-set", choices=("exploration", "holdout"), default="holdout")
    check.set_defaults(func=cmd_check)

    approve = sub.add_parser("approve")
    approve.add_argument("--project", type=Path, required=True)
    approve.add_argument("--patch", required=True)
    approve.add_argument("--template", type=Path, required=True)
    approve.add_argument("--reason", required=True)
    approve.set_defaults(func=cmd_approve)

    reject = sub.add_parser("reject")
    reject.add_argument("--project", type=Path, required=True)
    reject.add_argument("--patch", required=True)
    reject.set_defaults(func=cmd_reject)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--project", type=Path, required=True)
    list_cmd.set_defaults(func=cmd_list)

    for name, func in (("reopen", cmd_reopen), ("repair", cmd_repair)):
        command = sub.add_parser(name)
        command.add_argument("--project", type=Path, required=True)
        command.set_defaults(func=func)
    return parser


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
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        print(f"処理できません: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
