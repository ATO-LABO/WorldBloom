"""WB-ROUTE-001 S1 §4: does m_route (rho>0) actually change protagonist
behavior for the better -- fewer unreasoned wanders, a higher reach rate,
fewer pointless attacks on neutral companions -- without collapsing GA
archive diversity?

Two comparisons, both against the same fixed inputs S0's route_probe.py
used (neutral + 5 uniform-random genomes, ``random.Random("WB-ROUTE-001-S0")``,
seeds 1..40):

(a) fixed-gene sweep: the same 6 genomes x 40 seeds run once per
    rho in {0, 0.5, 1.0}, protagonist Policy wired with Route(rho=...).
    Reports: reach rate, avg turn-to-reach, kind/cause distribution, the
    "no reason" rate (kind=detour, cause=none), fight/sabotage/mislead
    counts against the three neutral companions, and the QD category
    distribution (gapengine.qd.descriptor's own axis-1 bucketing).

(b) a small GA (momotaro_plus2, population 20 x generations 10 x 3 seeds
    per individual) at rho in {0, 1.0}, same GA seed -- archive cell
    count, final-generation reach rate, best quality.

Output goes under --out (default C:\\Projects\\WorldBloom-local\\runs\\route-s1\\eval),
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
from gapengine.genome import Genome
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


def _action_cfg() -> dict[str, Any]:
    return yaml.safe_load((TEMPLATE / "action_graph.yaml").read_text(encoding="utf-8"))


def _qd_cfg() -> dict[str, Any]:
    path = TEMPLATE / "qd.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _run_one(genome: Genome, seed: int, rho: float, out_dir: Path) -> list[dict[str, Any]]:
    world = World.from_yaml(PROJECT / "world.yaml", action_graph_path=TEMPLATE / "action_graph.yaml")
    subjects = _load_subjects(PROJECT / "subjects")
    route_cfg = load_route_config(TEMPLATE)
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


def _sweep_one_rho(rho: float, out_dir: Path) -> dict[str, Any]:
    kind_counts: Counter[str] = Counter()
    cause_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    companion_attacks: Counter[tuple[str, str]] = Counter()
    total_decisions = 0
    no_reason = 0
    runs = 0
    reached_runs = 0
    reached_turns: list[int] = []
    qd_cfg = _qd_cfg()

    for genome_label, genome in _genomes():
        for seed in SEEDS:
            seed_out = out_dir / genome_label / f"seed-{seed}"
            rows = _run_one(genome, seed, rho, seed_out)
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

                verb = row.get("verb")
                args = row.get("args") or []
                if verb in ATTACK_VERBS and args and args[0] in COMPANION_TARGETS:
                    companion_attacks[(args[0], kind)] += 1

    return {
        "rho": rho,
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
    }


def run_sweep(out_dir: Path) -> dict[str, Any]:
    results = {}
    for rho in RHOS:
        rho_dir = out_dir / f"rho-{rho}"
        results[str(rho)] = _sweep_one_rho(rho, rho_dir)
    (out_dir / "sweep_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return results


def _run_ga(rho: float, out_dir: Path) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "project": PROJECT,
        "template": TEMPLATE,
        "out": out_dir,
        "generations": 10,
        "population": 20,
        "seeds": 3,
        "ga_seed": 20260925,
        "seed_base": 1,
        "keep": "all",
    }
    if rho > 0.0:
        cfg["route"] = {"rho": rho}
    archive = evolve(cfg)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    reach_rate = summary["generations"][-1]["reach_rate"]
    best_q = max((cell.quality for cell in archive.cells.values()), default=None)
    return {
        "rho": rho,
        "archive_cells": len(archive.cells),
        "final_generation_reach_rate": reach_rate,
        "best_quality": best_q,
    }


def run_ga_comparison(out_dir: Path) -> dict[str, Any]:
    results = {}
    for rho in (0.0, 1.0):
        results[str(rho)] = _run_ga(rho, out_dir / f"ga-rho-{rho}")
    (out_dir / "ga_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(r"C:\Projects\WorldBloom-local\runs\route-s1\eval"))
    parser.add_argument("--skip-ga", action="store_true", help="Only run the fixed-gene sweep (§4.1).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sweep = run_sweep(out_dir / "sweep")
    print(json.dumps(sweep, ensure_ascii=False, indent=2, sort_keys=True))
    if not args.skip_ga:
        ga = run_ga_comparison(out_dir / "ga")
        print(json.dumps(ga, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
