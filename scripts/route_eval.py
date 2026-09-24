"""WB-ROUTE-001 S1 §4 / S2 §6: does m_route (rho>0) actually change
protagonist behavior for the better -- fewer unreasoned wanders, a higher
reach rate, fewer pointless attacks on neutral companions -- without
collapsing GA archive diversity? S2 adds: does gene_affinity actually make
different genomes prefer different routes (fight vs negotiate), do motives
actually fire more for the genome they're aimed at, and does re-adding
reasons to former "detour/none" candidates cost back S1's own gains?

Two comparisons, both against the same fixed inputs S0's route_probe.py
used (neutral + 5 uniform-random genomes, ``random.Random("WB-ROUTE-001-S0")``,
seeds 1..40) plus S2's five typical genomes (--typical-genomes):

(a) fixed-gene sweep: each genome x 40 seeds run once per rho in {0, 0.5,
    1.0} (--rhos), protagonist Policy wired with Route(rho=...). At rho=1.0
    also run once more with motives/gene_affinity stripped (the "S1
    equivalent" comparison point, --s2 controls whether the S2-only sweep
    runs at all). Reports: reach rate, avg turn-to-reach, kind/cause
    distribution, the "no reason" rate (kind=detour, cause=none), motive
    counts (S2), plan (fight/negotiate) share among decisions with a
    best-route (S2), fight/sabotage/mislead counts against the three
    neutral companions, and the QD category distribution (gapengine.qd.
    descriptor's own axis-1 bucketing, "leading category").

(b) a small GA (momotaro_plus2, population 20 x generations 10 x 3+ seeds
    per individual, --ga-seeds) at rho in {0, 1.0} (S2 config) plus, when
    --s2 is set, a third rho=1.0 run with motives/gene_affinity stripped
    (S1 config) -- archive cell count/contents, final-generation reach
    rate, best quality.

Output goes under --out (default C:\\Projects\\WorldBloom-local\\runs\\route-s2\\),
never under the Drive-mounted repo.
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
from gapengine.evolve import _load_subjects, evolve
from gapengine.genome import CATEGORIES, Genome
from gapengine.policy import Policy
from gapengine.qd import Archive, descriptor, read_rows
from gapengine.route import Route, load_route_config

PROJECT = ROOT / "projects" / "momotaro_plus2"
TEMPLATE = ROOT / "templates" / "momotaro_plus2"
SEEDS = tuple(range(1, 41))
COMPANION_TARGETS = ("犬", "猿", "キジ")
ATTACK_VERBS = ("fight", "sabotage", "mislead")
REACHED_ENDINGS = frozenset({"homecoming", "homecoming_shared"})
RHOS = (0.0, 0.5, 1.0)


def _genomes() -> list[tuple[str, Genome]]:
    rng = random.Random("WB-ROUTE-001-S0")  # same draw S0's route_probe.py used
    entries = [("neutral", Genome.neutral())]
    for index in range(5):
        entries.append((f"random-{index}", Genome.random(rng)))
    return entries


def _typical_genome(**overrides: float | dict[str, float]) -> Genome:
    neutral = Genome.neutral()
    category_weight = dict(neutral.category_weight)
    category_weight.update(overrides.pop("category_weight", {}))
    return Genome(
        category_weight=category_weight,
        risk_tolerance=overrides.pop("risk_tolerance", neutral.risk_tolerance),
        stance_shift_bias=overrides.pop("stance_shift_bias", neutral.stance_shift_bias),
        novelty_drive=overrides.pop("novelty_drive", neutral.novelty_drive),
    )


def _typical_genomes() -> list[tuple[str, Genome]]:
    """S2 §6: five fixed-value genomes chosen to make each gene_affinity/
    motive lever unambiguous, on top of S0/S1's 6 (neutral + 5 random)."""

    low = {category: 0.05 for category in CATEGORIES}
    return [
        ("type-I", _typical_genome(category_weight={**low, "I": 1.0})),
        ("type-III", _typical_genome(category_weight={**low, "III": 1.0})),
        ("curious", _typical_genome(novelty_drive=1.0)),
        ("cautious", _typical_genome(risk_tolerance=0.0)),
        ("reckless", _typical_genome(risk_tolerance=1.0)),
    ]


def _action_cfg() -> dict[str, Any]:
    return yaml.safe_load((TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8"))


def _qd_cfg() -> dict[str, Any]:
    path = TEMPLATE / "qd.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _run_one(
    genome: Genome, seed: int, rho: float, out_dir: Path, *, s1_equivalent: bool = False
) -> list[dict[str, Any]]:
    """``s1_equivalent`` (S2 §6): strip motives/gene_affinity so this run is
    exactly S1's route.yaml -- the comparison point for "did S2 cost back
    S1's own gains"."""

    world = World.from_yaml(PROJECT / "world.yaml", action_graph_path=TEMPLATE / "action_graph.yaml")
    subjects = _load_subjects(PROJECT / "subjects")
    route_cfg = load_route_config(TEMPLATE)
    if route_cfg is not None and s1_equivalent:
        route_cfg = {**route_cfg, "motives": None, "gene_affinity": 0.0, "route_category": {}}
    route = Route.from_config(route_cfg) if route_cfg is not None else None
    if route is not None:
        route.rho = rho
    policy = Policy(genome, precedent=None, cfg=_action_cfg(), route=route)
    Simulation(
        seed, world, subjects, out_dir, policies={world.protagonist: policy}
    ).run()
    return read_rows(out_dir / "layers.jsonl")


def _reached(rows: list[dict[str, Any]]) -> bool:
    return any(
        row.get("kind") == "event" and row.get("verb") == "ending" and row.get("id") in REACHED_ENDINGS
        for row in rows
    )


def _final_turn(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("turn", 0)) for row in rows), default=0)


def _protagonist_decisions(rows: list[dict[str, Any]], protagonist: str) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("kind") == "decision" and row.get("subject") == protagonist]


def _sweep_one_rho(
    rho: float,
    out_dir: Path,
    genomes: list[tuple[str, Genome]],
    *,
    s1_equivalent: bool = False,
) -> dict[str, Any]:
    kind_counts: Counter[str] = Counter()
    cause_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    companion_attacks: Counter[tuple[str, str]] = Counter()
    # S2 §6: motive counts (by id) and best-route (plan) share, both
    # per-genome so a genome-specific report (e.g. "did type-III actually
    # get more care_for_ally than neutral") doesn't need re-deriving from
    # the pooled totals.
    motive_counts: dict[str, Counter[str]] = {}
    plan_counts: dict[str, Counter[str]] = {}
    total_decisions = 0
    no_reason = 0
    runs = 0
    reached_runs = 0
    reached_turns: list[int] = []
    qd_cfg = _qd_cfg()

    for genome_label, genome in genomes:
        motive_counts[genome_label] = Counter()
        plan_counts[genome_label] = Counter()
        for seed in SEEDS:
            seed_out = out_dir / genome_label / f"seed-{seed}"
            rows = _run_one(genome, seed, rho, seed_out, s1_equivalent=s1_equivalent)
            runs += 1
            reached = _reached(rows)
            if reached:
                reached_runs += 1
                reached_turns.append(_final_turn(rows))
            category_counts[descriptor(rows, qd_cfg).category] += 1

            for row in _protagonist_decisions(rows, "桃太郎"):
                policy_meta = row.get("policy") or {}
                route_meta = policy_meta.get("route")
                if not isinstance(route_meta, dict):
                    continue
                total_decisions += 1
                kind = route_meta["kind"]
                cause = route_meta.get("cause")
                kind_counts[kind] += 1
                if kind == "detour" and (cause is None or cause == "none"):
                    no_reason += 1
                cause_counts[f"{kind}:{cause}"] += 1
                if cause == "motive" and route_meta.get("motive"):
                    motive_counts[genome_label][str(route_meta["motive"])] += 1
                plan = route_meta.get("plan")
                if plan:
                    plan_counts[genome_label][str(plan)] += 1

                verb = row.get("verb")
                args = row.get("args") or []
                if verb in ATTACK_VERBS and args and args[0] in COMPANION_TARGETS:
                    companion_attacks[(args[0], kind)] += 1

    return {
        "rho": rho,
        "s1_equivalent": s1_equivalent,
        "runs": runs,
        "reach_rate": reached_runs / runs if runs else None,
        "reached_runs": reached_runs,
        "avg_turn_to_reach": (sum(reached_turns) / len(reached_turns)) if reached_turns else None,
        "total_decisions": total_decisions,
        "no_reason_rate": (no_reason / total_decisions) if total_decisions else None,
        "no_reason_count": no_reason,
        "kind_rate": {k: v / total_decisions for k, v in kind_counts.items()} if total_decisions else {},
        "kind_counts": dict(kind_counts),
        "detour_cause_counts": dict(cause_counts),
        "category_distribution": dict(category_counts),
        "companion_attack_total": sum(companion_attacks.values()),
        "companion_attacks": {f"{t}:{k}": c for (t, k), c in sorted(companion_attacks.items())},
        "motive_counts_by_genome": {
            label: dict(counts) for label, counts in motive_counts.items()
        },
        "plan_share_by_genome": {
            label: (
                {name: count / sum(counts.values()) for name, count in counts.items()}
                if counts
                else {}
            )
            for label, counts in plan_counts.items()
        },
    }


def run_sweep(
    out_dir: Path,
    genomes: list[tuple[str, Genome]],
    *,
    rhos: tuple[float, ...] = RHOS,
    s2_extra: bool = True,
) -> dict[str, Any]:
    results = {}
    for rho in rhos:
        rho_dir = out_dir / f"rho-{rho}"
        results[str(rho)] = _sweep_one_rho(rho, rho_dir, genomes)
    if s2_extra and 1.0 in rhos:
        # S2 §6: the S1-equivalent comparison point at rho=1.0 (motives/
        # gene_affinity stripped) -- what the plan calls "S1 設定".
        rho_dir = out_dir / "rho-1.0-s1-equivalent"
        results["1.0-s1-equivalent"] = _sweep_one_rho(
            1.0, rho_dir, genomes, s1_equivalent=True
        )
    (out_dir / "sweep_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return results


GA_SEEDS = (20260925, 20260926, 20260927)


def _run_ga(
    rho: float, ga_seed: int, out_dir: Path, *, s1_equivalent: bool = False
) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "project": PROJECT,
        "template": TEMPLATE,
        "out": out_dir,
        "generations": 10,
        "population": 20,
        "seeds": 3,
        "ga_seed": ga_seed,
        "seed_base": 1,
        "keep": "all",
    }
    if rho > 0.0:
        cfg["route"] = {"rho": rho}
        if s1_equivalent:
            # S2 §6: the S1-equivalent comparison point -- gapengine.evolve's
            # own _route_cfg only ever overrides `rho` from cfg, so motives/
            # gene_affinity have to be stripped at the route.yaml level
            # instead (a throwaway template copy).
            import shutil
            import tempfile

            scratch = Path(tempfile.mkdtemp(prefix="route-s1-equiv-"))
            shutil.copytree(TEMPLATE, scratch / TEMPLATE.name, dirs_exist_ok=True)
            stripped_template = scratch / TEMPLATE.name
            (stripped_template / "motives.yaml").unlink(missing_ok=True)
            route_yaml = yaml.safe_load(
                (stripped_template / "route.yaml").read_text(encoding="utf-8")
            )
            route_yaml.pop("gene_affinity", None)
            route_yaml.pop("route_category", None)
            (stripped_template / "route.yaml").write_text(
                yaml.safe_dump(route_yaml, allow_unicode=True), encoding="utf-8"
            )
            cfg["template"] = stripped_template
    archive = evolve(cfg)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    reach_rate = summary["generations"][-1]["reach_rate"]
    best_q = max((cell.quality for cell in archive.cells.values()), default=None)
    # plan §6: "最良qと埋まったマスの内容も併記" -- the occupied cell keys
    # (leading category x volatility_bin, qd.Archive's own axis-1
    # bucketing) alongside each cell's quality, not just the cell count.
    cells = [
        {"category": category, "volatility_bin": volatility_bin, "quality": round(elite.quality, 6)}
        for (category, volatility_bin), elite in sorted(archive.cells.items())
    ]
    return {
        "rho": rho,
        "ga_seed": ga_seed,
        "archive_cells": len(archive.cells),
        "final_generation_reach_rate": reach_rate,
        "best_quality": best_q,
        "cells": cells,
    }


def run_ga_comparison(out_dir: Path, *, ga_seeds: tuple[int, ...] = GA_SEEDS, s2_extra: bool = True) -> dict[str, Any]:
    # plan §4.2 recommended (kept for S2 §6): 3+ GA seeds per rho, not just
    # one -- a single seed's archive-cell count is too noisy to trust for
    # the §4.3/§6 "80% of rho=0's (S2 §6: of the S1 setting's) cell count"
    # pass/fail line.
    configs: list[tuple[str, float, bool]] = [("0.0", 0.0, False), ("1.0", 1.0, False)]
    if s2_extra:
        # S2 §6: the S1-equivalent comparison point -- same rho, motives/
        # gene_affinity stripped.
        configs.append(("1.0-s1-equivalent", 1.0, True))

    results: dict[str, Any] = {}
    for label, rho, s1_equivalent in configs:
        per_seed = [
            _run_ga(
                rho,
                ga_seed,
                out_dir / f"ga-rho-{label}-seed-{ga_seed}",
                s1_equivalent=s1_equivalent,
            )
            for ga_seed in ga_seeds
        ]
        cell_counts = [r["archive_cells"] for r in per_seed]
        results[label] = {
            "per_seed": per_seed,
            "archive_cells_mean": sum(cell_counts) / len(cell_counts),
            "archive_cells_min": min(cell_counts),
            "archive_cells_max": max(cell_counts),
            "best_quality_max": max(
                (r["best_quality"] for r in per_seed if r["best_quality"] is not None),
                default=None,
            ),
        }
    (out_dir / "ga_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(r"C:\Projects\WorldBloom-local\runs\route-s2\eval"))
    parser.add_argument("--skip-ga", action="store_true", help="Only run the fixed-gene sweep.")
    parser.add_argument(
        "--no-typical-genomes",
        action="store_true",
        help="S1-only genome set (neutral + 5 random); drop S2's 5 typical genomes.",
    )
    parser.add_argument(
        "--no-s2-extra",
        action="store_true",
        help="Skip the S1-equivalent (motives/gene_affinity stripped) comparison runs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    genomes = _genomes() if args.no_typical_genomes else _genomes() + _typical_genomes()
    sweep = run_sweep(out_dir / "sweep", genomes, s2_extra=not args.no_s2_extra)
    print(json.dumps(sweep, ensure_ascii=False, indent=2, sort_keys=True))
    if not args.skip_ga:
        ga = run_ga_comparison(out_dir / "ga", s2_extra=not args.no_s2_extra)
        print(json.dumps(ga, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
