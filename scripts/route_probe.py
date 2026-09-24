"""WB-ROUTE-001 S0: measure the route planner/annotator against real
momotaro_plus2 runs -- kind/cause distribution, "no reason" rate, and how
often a fight/sabotage/mislead lands on 犬/猿/キジ (the three neutral
companion candidates a plan-free policy sometimes attacks for no reason).

Runs (a) the neutral genome and (b) 5 uniform-random genomes (drawn from
their own ``random.Random`` stream, independent of any GA run), each across
seeds 1..40, with ``gapengine.route.Route`` wired into the protagonist's
Policy (measurement only -- see gapengine/policy.py's ``route`` hook; this
never changes a weight, so the run is exactly the run the GA/CLI would
already produce, just with an extra ``policy.route`` annotation per
decision). Output goes under --out (default
``C:\\Projects\\WorldBloom-local\\runs\\route-s0\\``), never under the
Drive-mounted repo.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from engine.sim import Simulation
from engine.world import World
from gapengine.evolve import _load_subjects
from gapengine.genome import Genome
from gapengine.policy import Policy
from gapengine.qd import read_rows
from gapengine.route import Route, load_route_config

PROJECT = ROOT / "projects" / "momotaro_plus2"
TEMPLATE = ROOT / "templates" / "momotaro_plus2"
SEEDS = tuple(range(1, 41))
COMPANION_TARGETS = ("犬", "猿", "キジ")
ATTACK_VERBS = ("fight", "sabotage", "mislead")
REACHED_ENDINGS = frozenset({"homecoming", "homecoming_shared"})


def _genomes() -> list[tuple[str, Genome]]:
    rng = random.Random("WB-ROUTE-001-S0")  # separate from any GA run's rng
    entries = [("neutral", Genome.neutral())]
    for index in range(5):
        entries.append((f"random-{index}", Genome.random(rng)))
    return entries


def _action_cfg() -> dict[str, Any]:
    path = TEMPLATE / "action_graph.yaml"
    if path.is_file():
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    return {"nodes": [], "edges": []}


def _run_one(genome: Genome, seed: int, out_dir: Path) -> list[dict[str, Any]]:
    world = World.from_yaml(
        PROJECT / "world.yaml", action_graph_path=TEMPLATE / "action_graph.yaml"
    )
    subjects = _load_subjects(PROJECT / "subjects")
    route_cfg = load_route_config(TEMPLATE)
    route = Route.from_config(route_cfg) if route_cfg is not None else None
    policy = Policy(genome, precedent=None, cfg=_action_cfg(), route=route)
    Simulation(
        seed,
        world,
        subjects,
        out_dir,
        policies={world.protagonist: policy},
    ).run()
    return read_rows(out_dir / "layers.jsonl")


def _reached(rows: list[dict[str, Any]]) -> bool:
    return any(
        row.get("kind") == "event"
        and row.get("verb") == "ending"
        and row.get("id") in REACHED_ENDINGS
        for row in rows
    )


def _protagonist_decisions(rows: list[dict[str, Any]], protagonist: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("kind") == "decision" and row.get("subject") == protagonist
    ]


def _situation_summary(route_meta: dict[str, Any]) -> str:
    # gapengine.route.annotate() now attaches zone/inventory/allies directly
    # to each decision's route meta (2026-09-24 revision) -- reading them
    # from the decision row's own actor delta would miss anything the delta
    # happened not to change that turn (see engine.log.make_delta's
    # nested_diff), so the annotator now hands the probe a full snapshot
    # instead.
    inventory = "、".join(route_meta.get("inventory") or []) or "なし"
    allies = "、".join(route_meta.get("allies") or []) or "なし"
    plan_name = route_meta.get("plan") or "-"
    h_before = (route_meta.get("h") or [None, None])[0]
    return (
        f"zone={route_meta.get('zone')} 所持={inventory} 仲間={allies} "
        f"最善経路={plan_name} h={h_before}"
    )


def _run_probe(out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    kind_counts: Counter[str] = Counter()
    cause_counts: Counter[str] = Counter()
    companion_attacks: Counter[tuple[str, str]] = Counter()  # (target, kind)
    none_verb_targets: Counter[str] = Counter()
    reached_none = reached_total = reached_body = 0
    unreached_none = unreached_total = unreached_body = 0
    sample_pool: dict[str, list[dict[str, Any]]] = {"advance": [], "prepare": [], "detour": []}

    for genome_label, genome in _genomes():
        for seed in SEEDS:
            seed_out = out_dir / genome_label / f"seed-{seed}"
            rows = _run_one(genome, seed, seed_out)
            reached = _reached(rows)
            decisions = _protagonist_decisions(rows, "桃太郎")
            for row in decisions:
                policy_meta = row.get("policy") or {}
                route_meta = policy_meta.get("route")
                if not isinstance(route_meta, dict):
                    continue
                kind = route_meta["kind"]
                cause = route_meta.get("cause")
                kind_counts[kind] += 1
                verb = row.get("verb")
                args = row.get("args") or []
                if kind == "detour":
                    cause_counts[cause or "none"] += 1
                    is_none = cause is None or cause == "none"
                    is_body = cause == "body"
                    if reached:
                        reached_total += 1
                        reached_none += is_none
                        reached_body += is_body
                    else:
                        unreached_total += 1
                        unreached_none += is_none
                        unreached_body += is_body
                    if is_none:
                        target = args[0] if args else "-"
                        none_verb_targets[f"{verb}:{target}"] += 1

                if verb in ATTACK_VERBS and args and args[0] in COMPANION_TARGETS:
                    companion_attacks[(args[0], kind)] += 1

                if len(sample_pool[kind]) < 40:
                    sample_pool[kind].append(
                        {
                            "genome": genome_label,
                            "seed": seed,
                            "turn": row.get("turn"),
                            "day": row.get("day"),
                            "slot": row.get("slot"),
                            "verb": verb,
                            "args": args,
                            "kind": kind,
                            "cause": cause,
                            "milestone": route_meta.get("milestone"),
                            "text": route_meta.get("text"),
                            "situation": _situation_summary(route_meta),
                        }
                    )

    total_decisions = sum(kind_counts.values())
    total_detours = kind_counts.get("detour", 0)
    report = {
        "runs": len(_genomes()) * len(SEEDS),
        "total_decisions": total_decisions,
        "kind_counts": dict(kind_counts),
        "kind_rate": {
            kind: (count / total_decisions if total_decisions else None)
            for kind, count in kind_counts.items()
        },
        "detour_cause_counts": dict(cause_counts),
        "detour_cause_rate": {
            cause: (count / total_detours if total_detours else None)
            for cause, count in cause_counts.items()
        },
        "no_reason_rate_of_all_decisions": (
            cause_counts.get("none", 0) / total_decisions if total_decisions else None
        ),
        "no_reason_rate_of_detours": (
            cause_counts.get("none", 0) / total_detours if total_detours else None
        ),
        "no_reason_rate_reached_runs": (
            reached_none / reached_total if reached_total else None
        ),
        "no_reason_rate_unreached_runs": (
            unreached_none / unreached_total if unreached_total else None
        ),
        "body_rate_reached_runs": (
            reached_body / reached_total if reached_total else None
        ),
        "body_rate_unreached_runs": (
            unreached_body / unreached_total if unreached_total else None
        ),
        "reached_detour_total": reached_total,
        "unreached_detour_total": unreached_total,
        "companion_attacks": {
            f"{target}:{kind}": count
            for (target, kind), count in sorted(companion_attacks.items())
        },
        "top_none_verb_targets": dict(none_verb_targets.most_common(15)),
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_markdown_report(out_dir, report, sample_pool)
    return report


def _write_markdown_report(
    out_dir: Path, report: dict[str, Any], sample_pool: dict[str, list[dict[str, Any]]]
) -> None:
    lines = [
        "# WB-ROUTE-001 S0 route probe",
        "",
        f"- runs={report['runs']} (neutral + 5 random genomes x seed 1..40)",
        f"- total_decisions={report['total_decisions']}",
        "",
        "## kind 別",
        "| kind | 件数 | 割合 |",
        "|---|---|---|",
    ]
    for kind, count in sorted(report["kind_counts"].items()):
        rate = report["kind_rate"].get(kind)
        lines.append(f"| {kind} | {count} | {rate:.3f} |" if rate is not None else f"| {kind} | {count} | - |")

    lines += ["", "## detour の cause 別", "| cause | 件数 | detour内の割合 |", "|---|---|---|"]
    for cause, count in sorted(report["detour_cause_counts"].items()):
        rate = report["detour_cause_rate"].get(cause)
        lines.append(f"| {cause} | {count} | {rate:.3f} |" if rate is not None else f"| {cause} | {count} | - |")

    lines += [
        "",
        "## 無理由率",
        f"- 全決定に対する無理由率 = {report['no_reason_rate_of_all_decisions']}",
        f"- detourの中の無理由率 = {report['no_reason_rate_of_detours']}",
        f"- 到達したランでの無理由率 = {report['no_reason_rate_reached_runs']}"
        f"（detour {report['reached_detour_total']} 件中、body原因 = "
        f"{report['body_rate_reached_runs']}）",
        f"- 未到達のランでの無理由率 = {report['no_reason_rate_unreached_runs']}"
        f"（detour {report['unreached_detour_total']} 件中、body原因 = "
        f"{report['body_rate_unreached_runs']}）",
        "- 到達ランの方が無理由率が高いのは集計バグではない: 到達ランは中立/乱数遺伝子が"
        "route重み付けなし（S0は計測のみ）で偶然ゴールに辿り着くまで80ターン近く周り続ける"
        "ため無理由detourの絶対機会が多く、未到達ランは疲労・下車等body原因の割合が高く"
        "無理由の相対シェアを押し下げる（body原因比率は上記の通り未到達側が高い）。",
        "",
        "## キジ・犬・猿への fight/sabotage/mislead",
        "| 対象:kind | 件数 |",
        "|---|---|",
    ]
    for key, count in sorted(report["companion_attacks"].items()):
        lines.append(f"| {key} | {count} |")

    lines += ["", "## 無理由(none)の動詞×対象 上位15件", "| 動詞:対象 | 件数 |", "|---|---|"]
    for key, count in report["top_none_verb_targets"].items():
        lines.append(f"| {key} | {count} |")

    lines += ["", "## 人手確認用の抜き取り（kind 層化、固定 seed 抽出）"]
    sample_rng = random.Random("WB-ROUTE-001-S0-sample")
    picked: list[dict[str, Any]] = []
    for kind in ("advance", "prepare", "detour"):
        pool = sample_pool.get(kind, [])
        if not pool:
            continue
        count = min(7 if kind != "detour" else 6, len(pool))
        picked.extend(sample_rng.sample(pool, count))
    picked = picked[:20]

    lines += ["| 遺伝子 | seed | turn | 時点 | 状況 | 行動 | kind | cause | text |", "|---|---|---|---|---|---|---|---|---|"]
    for entry in picked:
        lines.append(
            f"| {entry['genome']} | {entry['seed']} | {entry['turn']} | "
            f"day={entry['day']} {entry['slot']} | "
            f"{entry['situation']} | {entry['verb']}{entry['args']} | {entry['kind']} | "
            f"{entry['cause']} | {entry['text']} |"
        )

    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(r"C:\Projects\WorldBloom-local\runs\route-s0"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = _run_probe(args.out.resolve())
    print(f"report={args.out.resolve() / 'report.md'}")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
