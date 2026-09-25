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

2026-09-24 Opus review (234a090, 1st pass), required fix 6: the sample table
used to draw only from each kind's first 40 recorded decisions (in practice,
almost entirely neutral-genome seed 1-5) -- it now stratifies across every
decision from every run. The reached/unreached "no reason" comparison used
detour-internal denominators (mathematically 1 - body_rate, so it measured
nothing beyond the body rate already shown); it now compares each group's
share of *all* decisions, and reports each group's average final turn
instead of the previous (checked and wrong) "reached runs wander ~80 turns"
claim.
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
from gapengine.route import Route, _shortest_route_path, load_route_config

PROJECT = ROOT / "projects" / "momotaro_plus2"
TEMPLATE = ROOT / "templates" / "momotaro_plus2"
SEEDS = tuple(range(1, 41))
COMPANION_TARGETS = ("犬", "猿", "キジ")
ATTACK_VERBS = ("fight", "sabotage", "mislead")
REACHED_ENDINGS = frozenset({"homecoming", "homecoming_shared"})
GOAL_ZONE = "鬼ヶ島"  # momotaro_plus2-specific: only used for the
# "distancing move" diagnostic below, not by the planner itself.
BOAT_ITEM = "船"


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


def _topology_world() -> World:
    """A throwaway World, used only to read static zone topology
    (world.routes never changes across genomes/seeds) for the "distancing
    move" diagnostic."""

    return World.from_yaml(PROJECT / "world.yaml", action_graph_path=TEMPLATE / "action_graph.yaml")


def _hops_to_goal(world: World, zone: str) -> int | None:
    if zone == GOAL_ZONE:
        return 0
    edges = _shortest_route_path(world, zone, GOAL_ZONE)
    return None if edges is None else len(edges)


def _offer_items(world: World) -> set[str]:
    return {
        name
        for name, definition in world.items.items()
        if definition.get("lootable") and definition.get("modifier")
    }


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
    # gapengine.route.annotate() attaches zone/inventory/allies directly to
    # each decision's route meta -- reading them from the decision row's own
    # actor delta would miss anything the delta happened not to change that
    # turn (see engine.log.make_delta's nested_diff), so the annotator hands
    # the probe a full snapshot instead.
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
    topology = _topology_world()
    offer_items = _offer_items(topology)

    kind_counts: Counter[str] = Counter()
    cause_counts: Counter[str] = Counter()
    companion_attacks: Counter[tuple[str, str]] = Counter()  # (target, kind)
    none_verb_targets: Counter[str] = Counter()
    sample_pool: dict[str, list[dict[str, Any]]] = {"advance": [], "prepare": [], "detour": []}

    # reached/unreached: all-decisions denominators (required fix 6) --
    # detour-internal denominators are 1 - body_rate and show nothing new.
    group_totals = {True: 0, False: 0}
    group_none = {True: 0, False: 0}
    group_final_turns: dict[bool, list[int]] = {True: [], False: []}

    move_total = move_advance = 0
    distancing_total = distancing_advance = 0

    for genome_label, genome in _genomes():
        for seed in SEEDS:
            seed_out = out_dir / genome_label / f"seed-{seed}"
            rows = _run_one(genome, seed, seed_out)
            reached = _reached(rows)
            final_turn = max((int(row.get("turn", 0)) for row in rows), default=0)
            group_final_turns[reached].append(final_turn)

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

                group_totals[reached] += 1
                is_none = kind == "detour" and (cause is None or cause == "none")
                if is_none:
                    group_none[reached] += 1

                if kind == "detour":
                    cause_counts[cause or "none"] += 1
                    if is_none:
                        target = args[0] if args else "-"
                        none_verb_targets[f"{verb}:{target}"] += 1

                if verb in ATTACK_VERBS and args and args[0] in COMPANION_TARGETS:
                    companion_attacks[(args[0], kind)] += 1

                if verb == "move" and args:
                    move_total += 1
                    is_advance = kind == "advance"
                    move_advance += is_advance
                    inventory = route_meta.get("inventory") or []
                    if BOAT_ITEM in inventory and any(item in offer_items for item in inventory):
                        before_hops = _hops_to_goal(topology, str(route_meta.get("zone")))
                        after_hops = _hops_to_goal(topology, str(args[0]))
                        if before_hops is not None and after_hops is not None and after_hops > before_hops:
                            distancing_total += 1
                            distancing_advance += is_advance

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
            group_none[True] / group_totals[True] if group_totals[True] else None
        ),
        "no_reason_rate_unreached_runs": (
            group_none[False] / group_totals[False] if group_totals[False] else None
        ),
        "reached_decision_total": group_totals[True],
        "unreached_decision_total": group_totals[False],
        "reached_run_count": len(group_final_turns[True]),
        "unreached_run_count": len(group_final_turns[False]),
        "avg_final_turn_reached_runs": (
            sum(group_final_turns[True]) / len(group_final_turns[True])
            if group_final_turns[True] else None
        ),
        "avg_final_turn_unreached_runs": (
            sum(group_final_turns[False]) / len(group_final_turns[False])
            if group_final_turns[False] else None
        ),
        "move_advance_rate": (move_advance / move_total if move_total else None),
        "move_total": move_total,
        "distancing_move_advance_rate": (
            distancing_advance / distancing_total if distancing_total else None
        ),
        "distancing_move_total": distancing_total,
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
        f"- detourの中の無理由率 = {report['no_reason_rate_of_detours']}"
        "（参考値。1-body比率と同値なので単独では意味を持たない）",
        f"- 到達したラン（{report['reached_run_count']}本）: 全決定に対する無理由率 = "
        f"{report['no_reason_rate_reached_runs']}（決定 {report['reached_decision_total']} 件）"
        f"、平均最終ターン = {report['avg_final_turn_reached_runs']}",
        f"- 未到達のラン（{report['unreached_run_count']}本）: 全決定に対する無理由率 = "
        f"{report['no_reason_rate_unreached_runs']}（決定 {report['unreached_decision_total']} 件）"
        f"、平均最終ターン = {report['avg_final_turn_unreached_runs']}",
        "",
        "## 移動の advance 率",
        f"- move 全体: {report['move_advance_rate']}（n={report['move_total']}）",
        f"- 船＋差出品保持時に鬼ヶ島から遠ざかる move: {report['distancing_move_advance_rate']}"
        f"（n={report['distancing_move_total']}）",
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

    lines += ["", "## 人手確認用の抜き取り（kind 層化、全件から抽出）"]
    sample_rng = random.Random("WB-ROUTE-001-S0-sample")
    picked: list[dict[str, Any]] = []
    for kind, count in (("advance", 12), ("prepare", 8), ("detour", 12)):
        pool = sample_pool.get(kind, [])
        if not pool:
            continue
        picked.extend(sample_rng.sample(pool, min(count, len(pool))))

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
