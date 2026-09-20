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
from typing import Sequence

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
    validate_patch,
)
from gapengine.world_patch_propose import build_prompt, check_trigger_coverage, make_patch, parse_proposal
from gapengine.world_patch_trial import run_trial


def _resolve_ctx(experiment: Path):
    from viewer.data import RunRepository

    repository = RunRepository(experiment.parent)
    return repository, lineage._resolve_world_context(repository, experiment)


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
        current_approved = approved_patches(project)
    except PatchError as error:
        return f"承認済みパッチの読み込みに失敗しました: {error}"
    if _world_parent_rev(base_world) != [p["id"] for p in current_approved]:
        return "この実験は現在の承認済み拡張とは別の版の世界で回っています"
    return None


def _gate(experiment: Path, project: Path, patch: dict, ctx: dict, subject_ids: list[str],
          *, skip_trial: bool, max_runs: int, seeds_per_run: int) -> dict:
    base_world = yaml.safe_load(Path(ctx["world_path"]).read_text(encoding="utf-8"))
    violations = validate_patch(base_world, patch, subject_ids=subject_ids)
    violations += check_trigger_coverage(patch.get("add", {}), patch.get("trigger", {}))
    static_passed = not violations
    trial_result = None
    passed = static_passed
    if static_passed and not skip_trial:
        with tempfile.TemporaryDirectory() as tmp:
            trial_result = run_trial(experiment, patch, work_dir=Path(tmp), max_runs=max_runs,
                                      seeds_per_run=seeds_per_run)
        passed = trial_result["passed"]
    return {
        "schema_version": 1,
        "patch_id": patch["id"],
        "static": {"passed": static_passed, "violations": violations},
        "trial": trial_result,
        "passed": passed,
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _print_gate_summary(patch: dict, gate: dict) -> None:
    print(f"題: {patch.get('title')}")
    print(f"理由: {patch.get('rationale')}")
    print(f"足したもの: {_added_summary(patch.get('add', {}))}")
    static = gate["static"]
    if static["passed"]:
        print("静的ゲート: 合格")
    else:
        print("静的ゲート: 不合格 -- " + "; ".join(static["violations"]))
    trial = gate.get("trial")
    if trial is not None:
        print(f"試走: {'合格' if trial.get('passed') else '不合格'}")
        for reason in trial.get("reasons", []):
            print(f"  - {reason}")
        if trial.get("errors"):
            print(f"  - 再実行エラー: {trial['errors']}")
    elif static["passed"]:
        print("試走: スキップ")


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

    repository, ctx = _resolve_ctx(experiment)
    base_world = yaml.safe_load(Path(ctx["world_path"]).read_text(encoding="utf-8"))
    mismatch = _check_parent_rev(base_world, project)
    if mismatch:
        print(mismatch)
        return 1
    world_parent_rev = _world_parent_rev(base_world)

    subject_ids = _subject_ids(Path(ctx["subjects_dir"]))
    zone_verbs = _zone_verbs(report, trigger["zone"])
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

        try:
            proposal = parse_proposal(response_text)
            patch = make_patch(proposal, trigger=trigger, parent_rev=world_parent_rev, author=author)
            violations = validate_patch(base_world, patch, subject_ids=subject_ids)
            violations += check_trigger_coverage(patch["add"], trigger)
        except ValueError as error:
            violations = [str(error)]
            patch = make_patch(
                {"title": "(解析失敗)", "rationale": str(error)[:300], "add": {}},
                trigger=trigger, parent_rev=world_parent_rev, author=author,
            )

        if not violations or args.from_file or attempt == max_attempts:
            break
        current_prompt = (
            current_prompt
            + f"\n\n前回の提案は次の理由で不採用でした: {'、'.join(violations)}。これらを直したJSONを出力してください。"
        )

    gate = {
        "schema_version": 1,
        "patch_id": patch["id"],
        "static": {"passed": not violations, "violations": violations},
        "trial": None,
        "passed": not violations,
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if not violations and not args.skip_trial:
        with tempfile.TemporaryDirectory() as tmp:
            gate["trial"] = run_trial(experiment, patch, work_dir=Path(tmp), max_runs=args.max_runs,
                                       seeds_per_run=args.seeds_per_run)
        gate["passed"] = gate["trial"]["passed"]

    proposed_dir = project / "patches" / "_proposed"
    proposed_dir.mkdir(parents=True, exist_ok=True)
    patch_path = proposed_dir / f"{patch['id']}.yaml"
    gate_path = proposed_dir / f"{patch['id']}.gate.json"
    patch_path.write_text(yaml.safe_dump(patch, allow_unicode=True, sort_keys=False), encoding="utf-8")
    gate["patch_sha256"] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    gate_path.write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_gate_summary(patch, gate)
    print(f"保存先: {patch_path}")
    return 0 if gate["passed"] else 1


def cmd_check(args: argparse.Namespace) -> int:
    if not ID_RE.fullmatch(args.patch):
        print("パッチ ID の形式が不正です")
        return 1
    experiment = args.experiment.resolve()
    project = args.project.resolve()
    proposed_dir = project / "patches" / "_proposed"
    patch_path = proposed_dir / f"{args.patch}.yaml"
    if not patch_path.is_file():
        print(f"提案が見つかりません: {patch_path}")
        return 1
    patch = yaml.safe_load(patch_path.read_text(encoding="utf-8"))

    _, ctx = _resolve_ctx(experiment)
    base_world = yaml.safe_load(Path(ctx["world_path"]).read_text(encoding="utf-8"))
    mismatch = _check_parent_rev(base_world, project)
    if mismatch:
        print(mismatch)
        return 1

    subject_ids = _subject_ids(Path(ctx["subjects_dir"]))
    gate = _gate(experiment, project, patch, ctx, subject_ids,
                 skip_trial=args.skip_trial, max_runs=args.max_runs, seeds_per_run=args.seeds_per_run)
    gate["patch_sha256"] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    gate_path = proposed_dir / f"{args.patch}.gate.json"
    gate_path.write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_gate_summary(patch, gate)
    return 0 if gate["passed"] else 1


def cmd_approve(args: argparse.Namespace) -> int:
    if not ID_RE.fullmatch(args.patch):
        print("パッチ ID の形式が不正です")
        return 1
    project = args.project.resolve()
    proposed_dir = project / "patches" / "_proposed"
    patch_path = proposed_dir / f"{args.patch}.yaml"
    gate_path = proposed_dir / f"{args.patch}.gate.json"
    if not patch_path.is_file() or not gate_path.is_file():
        print("提案またはゲート結果が見つかりません")
        return 1
    patch = yaml.safe_load(patch_path.read_text(encoding="utf-8"))
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if not gate.get("passed"):
        print("ゲートに合格していません")
        return 1

    # Every check below closes a way `approve` could be tricked into
    # accepting a patch the gate never actually vetted.
    if patch.get("id") != args.patch:
        print("パッチの id がファイル名と一致しません")
        return 1
    if gate.get("patch_id") != args.patch:
        print("ゲート結果の patch_id がファイル名と一致しません")
        return 1
    current_sha256 = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    if gate.get("patch_sha256") != current_sha256:
        print("ゲート検査の後にパッチが変更されています。check をやり直してください")
        return 1
    if patch_id_for(patch.get("add", {})) != patch.get("id"):
        print("パッチの内容と ID が一致しません。propose --from-file で作り直してください")
        return 1

    try:
        current_approved = approved_patches(project)
    except PatchError as error:
        print(f"承認済みパッチの読み込みに失敗しました: {error}")
        return 1
    current_ids = [p["id"] for p in current_approved]
    if list(patch.get("parent_rev") or []) != current_ids:
        print("parent_rev が現在の承認済みスタックと一致しません")
        return 1

    world = yaml.safe_load((project / "world.yaml").read_text(encoding="utf-8"))
    subject_ids = _subject_ids(project / "subjects")
    try:
        world = apply_patches(world, current_approved)
        violations = validate_patch(world, patch, subject_ids=subject_ids)
    except PatchError as error:
        print(f"現在の世界に対する検証に失敗しました: {error}")
        return 1
    if violations:
        print("現在の世界に対する検証に失敗しました: " + "; ".join(violations))
        return 1

    # Last gate: the current approved stack + this patch must actually build
    # in the engine, not just pass the data-only static checks.
    full_world = apply_patch(world, patch)
    absolutize_references(full_world, project, ROOT)
    action_graph_path = (args.template / "action_graph.yaml") if args.template else None
    with tempfile.TemporaryDirectory() as tmp:
        world_path = Path(tmp) / "world.yaml"
        world_path.write_text(yaml.safe_dump(full_world, allow_unicode=True, sort_keys=False), encoding="utf-8")
        try:
            World.from_yaml(world_path, action_graph_path=action_graph_path)
        except Exception as error:  # noqa: BLE001 -- surface as a rejection, not a crash
            print(f"世界を構築できません: {error}")
            return 1

    seq = max((p.get("approved_seq", 0) for p in current_approved), default=0) + 1
    approved_patch = {**patch, "approved_seq": seq}
    patches_dir = project / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    approved_patch_path = patches_dir / f"{args.patch}.yaml"
    approved_patch_path.write_text(
        yaml.safe_dump(approved_patch, allow_unicode=True, sort_keys=False), encoding="utf-8")
    gate["patch_sha256"] = hashlib.sha256(approved_patch_path.read_bytes()).hexdigest()
    (patches_dir / f"{args.patch}.gate.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
    patch_path.unlink()
    gate_path.unlink()
    print(f"承認しました: {args.patch}（approved_seq={seq}）")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    if not ID_RE.fullmatch(args.patch):
        print("パッチ ID の形式が不正です")
        return 1
    project = args.project.resolve()
    proposed_dir = project / "patches" / "_proposed"
    rejected_dir = project / "patches" / "_rejected"
    moved = False
    for suffix in (".yaml", ".gate.json"):
        src = proposed_dir / f"{args.patch}{suffix}"
        if src.is_file():
            rejected_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(rejected_dir / src.name))
            moved = True
    if not moved:
        print("提案が見つかりません")
        return 1
    print(f"却下しました: {args.patch}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    try:
        approved = approved_patches(project)
    except PatchError as error:
        print(f"承認済みパッチの読み込みに失敗しました: {error}")
        return 1

    print("承認済み:")
    if not approved:
        print("  (なし)")
    for patch in approved:
        print(f"  {patch['approved_seq']}: {patch['id']} {patch.get('title')} "
              f"({_added_summary(patch.get('add', {}))})")

    print("提案中:")
    proposed_dir = project / "patches" / "_proposed"
    proposals = sorted(proposed_dir.glob("*.yaml")) if proposed_dir.is_dir() else []
    if not proposals:
        print("  (なし)")
    for path in proposals:
        patch = yaml.safe_load(path.read_text(encoding="utf-8"))
        gate_path = path.parent / f"{path.stem}.gate.json"
        if gate_path.is_file():
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            violations = (gate.get("static") or {}).get("violations") or []
            note = "合格" if gate.get("passed") else ("不合格: " + violations[0] if violations else "不合格")
        else:
            note = "未検査"
        print(f"  {patch.get('id')}: {patch.get('title')} ({note})")
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
    propose.set_defaults(func=cmd_propose)

    check = sub.add_parser("check")
    check.add_argument("--experiment", type=Path, required=True)
    check.add_argument("--project", type=Path, required=True)
    check.add_argument("--patch", required=True)
    check.add_argument("--skip-trial", action="store_true")
    check.add_argument("--max-runs", type=int, default=5)
    check.add_argument("--seeds-per-run", type=int, default=8)
    check.set_defaults(func=cmd_check)

    approve = sub.add_parser("approve")
    approve.add_argument("--project", type=Path, required=True)
    approve.add_argument("--patch", required=True)
    approve.add_argument("--template", type=Path)
    approve.set_defaults(func=cmd_approve)

    reject = sub.add_parser("reject")
    reject.add_argument("--project", type=Path, required=True)
    reject.add_argument("--patch", required=True)
    reject.set_defaults(func=cmd_reject)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--project", type=Path, required=True)
    list_cmd.set_defaults(func=cmd_list)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
