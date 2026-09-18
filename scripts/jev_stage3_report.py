"""Stage 3 comparison report for the Jev rationality layer (WB-JEV-001).

Aggregates one or more GA experiment output directories (as produced by
``scripts/evolve.py ... --keep all``, optionally with ``--kappa`` > 0) into a
single Markdown report: reach rate, archive occupancy/quality, the
protagonist's "weird action" rate, the rationality layer's own activity
(kappa > 0 runs only), route diversity among reached runs, shaped fitness
(overall and a paired generation-0-only comparison against the baseline --
useful because the momotaro target's overall reach rate is low enough
(~2%) that a 100-run experiment often shows no difference in reach rate
alone), allies-at-contest/contest-rate, mean quality across every run, and
a mechanical PASS/FAIL judgement against a baseline experiment.

Usage:
    python scripts/jev_stage3_report.py --runs k0=<dir> k03=<dir> --out report.md

Each experiment directory is expected to hold ``g<N>/results.json`` per
generation (the per-individual, per-seed output evolve.py writes before any
``--keep`` pruning -- so "reached"/"quality"/"effective_sequence" are read
straight from there rather than re-derived) plus a top-level
``archive.json`` and ``summary.json``. Missing or corrupt files are skipped
with a warning on stderr; this script never raises for that.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gapengine.qd import Archive  # noqa: E402

WEIRD_STALL_VERBS = {"rest", "withdraw"}
WEIRD_HOSTILE_VERBS = {"fight", "sabotage", "mislead"}
GEN_DIR_RE = re.compile(r"^g(\d+)$")


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _load_json(path: Path) -> Any | None:
    if not path.exists():
        _warn(f"missing file: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _warn(f"unreadable file {path}: {exc}")
        return None


def _iter_layer_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Streams layers.jsonl one line at a time (files can be large)."""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("cannot take a quantile of no values")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    remainder = position - lower
    return ordered[lower] * (1.0 - remainder) + ordered[upper] * remainder


def _target_ending(project: Path) -> Any:
    from engine.world import World

    return World.from_yaml(project / "world.yaml").target_ending


def _lazy_target_ending(project: Path) -> Callable[[], Any]:
    cache: dict[str, Any] = {}

    def getter() -> Any:
        if "value" not in cache:
            cache["value"] = _target_ending(project)
        return cache["value"]

    return getter


def _generation_dirs(exp_dir: Path) -> list[tuple[int, Path]]:
    if not exp_dir.is_dir():
        _warn(f"experiment directory not found: {exp_dir}")
        return []
    found = []
    for entry in exp_dir.iterdir():
        match = entry.is_dir() and GEN_DIR_RE.fullmatch(entry.name)
        if match:
            found.append((int(match.group(1)), entry))
    found.sort(key=lambda pair: pair[0])
    return found


def _load_generations(exp_dir: Path) -> list[tuple[int, list[dict[str, Any]]]]:
    """[(generation, individuals)], sorted, for every generation whose
    results.json is readable (a generation still being written, or an older
    pruned run missing it, is skipped with a warning rather than crashing)."""
    generations = []
    for generation, gen_dir in _generation_dirs(exp_dir):
        individuals = _load_json(gen_dir / "results.json")
        if individuals is not None:
            generations.append((generation, individuals))
    return generations


def _all_runs(individuals: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for individual in individuals:
        yield from individual.get("runs", [])


def _run_reached(
    run: dict[str, Any],
    exp_dir: Path,
    target_ending_getter: Callable[[], Any],
) -> bool:
    if "reached" in run:
        return bool(run["reached"])
    # Fallback for a results.json schema without a precomputed "reached"
    # field: recompute it the same way evolve.py does, from the layers.
    from gapengine.qd import reached as qd_reached

    layers_path = run.get("layers_path")
    if not layers_path:
        return False
    full_path = exp_dir / layers_path
    if not full_path.exists():
        _warn(f"missing layers file for reach fallback: {full_path}")
        return False
    return qd_reached(list(_iter_layer_rows(full_path)), target_ending_getter())


def _reach_totals(
    generations: list[tuple[int, list[dict[str, Any]]]],
    exp_dir: Path,
    project: Path,
) -> dict[int, tuple[int, int]]:
    """{generation: (reached, total)} across every individual and seed."""
    get_target_ending = _lazy_target_ending(project)
    totals: dict[int, tuple[int, int]] = {}
    for generation, individuals in generations:
        reached = 0
        total = 0
        for run in _all_runs(individuals):
            total += 1
            if _run_reached(run, exp_dir, get_target_ending):
                reached += 1
        totals[generation] = (reached, total)
    return totals


def _weird_and_rationality_stats(
    exp_dir: Path,
    generations: list[tuple[int, list[dict[str, Any]]]],
) -> dict[str, Any]:
    denominator = 0
    weird_a = weird_b = weird_c = 0
    m_rats: list[float] = []
    p_rat_denominator = 0
    p_rat_present = 0

    for _generation, individuals in generations:
        for run in _all_runs(individuals):
            layers_path = run.get("layers_path")
            if not layers_path:
                continue
            full_path = exp_dir / layers_path
            if not full_path.exists():
                _warn(f"missing layers file: {full_path}")
                continue

            protagonist: str | None = None
            for row in _iter_layer_rows(full_path):
                if protagonist is None:
                    if row.get("kind") == "header":
                        protagonist = row.get("protagonist")
                    continue
                if row.get("kind") != "decision" or row.get("subject") != protagonist:
                    continue

                denominator += 1
                verb = row.get("verb")
                policy = row.get("policy")
                classification = row.get("classification")

                if (
                    verb in WEIRD_STALL_VERBS
                    and isinstance(policy, dict)
                    and isinstance(policy.get("ctx"), list)
                    and len(policy["ctx"]) > 1
                    and policy["ctx"][1] is False
                ):
                    weird_a += 1
                if (
                    verb in WEIRD_HOSTILE_VERBS
                    and isinstance(classification, dict)
                    and classification.get("target_role") == "ally"
                ):
                    weird_b += 1
                if verb == "disguise":
                    weird_c += 1

                if isinstance(policy, dict) and "m_rat" in policy:
                    m_rats.append(float(policy["m_rat"]))
                    p_rat_denominator += 1
                    if policy.get("p_rat") is not None:
                        p_rat_present += 1

    return {
        "denominator": denominator,
        "weird_a": weird_a,
        "weird_b": weird_b,
        "weird_c": weird_c,
        "m_rats": m_rats,
        "p_rat_denominator": p_rat_denominator,
        "p_rat_present": p_rat_present,
    }


def _shaped_allies_quality_stats(
    generations: list[tuple[int, list[dict[str, Any]]]],
) -> dict[str, Any]:
    """Run-level shaped/allies/quality aggregates, plus generation-0's
    shaped values keyed by (individual index, seed) for a paired
    experiment-vs-baseline comparison: with the same --ga-seed, generation
    0's population is identical across experiments, so a matching
    (index, seed) pair is a genuine before/after comparison of the same
    run, not just a same-generation average."""

    all_shaped: list[float] = []
    gen0_shaped_by_key: dict[tuple[int, int], float] = {}
    allies_at_contest: list[float] = []
    contest_runs = 0
    total_runs = 0
    all_quality: list[float] = []

    for generation, individuals in generations:
        for individual in individuals:
            index = int(individual.get("index", -1))
            for run in individual.get("runs", []):
                total_runs += 1
                if "shaped" in run:
                    shaped_value = float(run["shaped"])
                    all_shaped.append(shaped_value)
                    if generation == 0:
                        gen0_shaped_by_key[(index, int(run["seed"]))] = shaped_value
                if run.get("allies_at_contest") is not None:
                    allies_at_contest.append(float(run["allies_at_contest"]))
                if run.get("contest_turn") is not None:
                    contest_runs += 1
                if "quality" in run:
                    all_quality.append(float(run["quality"]))

    return {
        "shaped_mean": statistics.mean(all_shaped) if all_shaped else None,
        "gen0_shaped_by_key": gen0_shaped_by_key,
        "gen0_shaped_mean": (
            statistics.mean(gen0_shaped_by_key.values())
            if gen0_shaped_by_key
            else None
        ),
        "allies_at_contest_mean": (
            statistics.mean(allies_at_contest) if allies_at_contest else None
        ),
        "contest_runs": contest_runs,
        "total_runs": total_runs,
        "quality_mean": statistics.mean(all_quality) if all_quality else None,
    }


def _paired_gen0_shaped_diff(
    stats: dict[str, Any], baseline: dict[str, Any]
) -> tuple[float | None, int, int]:
    """(mean_diff, positive_count, matched_count) for generation-0 runs
    whose (individual index, seed) key exists in both experiments -- diff
    is always experiment minus baseline."""

    this_map = stats["shaped"]["gen0_shaped_by_key"]
    baseline_map = baseline["shaped"]["gen0_shaped_by_key"]
    diffs = [value - baseline_map[key] for key, value in this_map.items() if key in baseline_map]
    if not diffs:
        return None, 0, 0
    mean_diff = sum(diffs) / len(diffs)
    positive_count = sum(1 for diff in diffs if diff > 0)
    return mean_diff, positive_count, len(diffs)


def _diversity_stats(
    generations: list[tuple[int, list[dict[str, Any]]]],
) -> tuple[int, int]:
    sequences = []
    for _generation, individuals in generations:
        for run in _all_runs(individuals):
            if run.get("reached"):
                sequences.append(
                    tuple(tuple(item) for item in run.get("effective_sequence", []))
                )
    return len(set(sequences)), len(sequences)


def _archive_stats(exp_dir: Path) -> dict[str, Any] | None:
    path = exp_dir / "archive.json"
    if not path.exists():
        _warn(f"missing file: {path}")
        return None
    try:
        archive = Archive.load(path)
    except (OSError, ValueError, KeyError) as exc:
        _warn(f"unreadable archive {path}: {exc}")
        return None
    qualities = [elite.quality for elite in archive.cells.values()]
    return {
        "occupied_cells": len(archive.cells),
        "avg_quality": statistics.mean(qualities) if qualities else None,
        "max_quality": max(qualities) if qualities else None,
    }


def _rationality_summary(exp_dir: Path) -> dict[str, Any] | None:
    summary = _load_json(exp_dir / "summary.json")
    if summary is None:
        return None
    generations = summary.get("generations", [])
    if not any("rationality_judge_calls" in generation for generation in generations):
        return None
    table_size = None
    for generation in reversed(generations):
        if "rationality_table_size" in generation:
            table_size = generation["rationality_table_size"]
            break
    return {
        "judge_calls": sum(
            int(generation.get("rationality_judge_calls", 0))
            for generation in generations
        ),
        "table_size": table_size,
        "thermal_wait_seconds": sum(
            float(generation.get("rationality_thermal_wait_seconds", 0.0))
            for generation in generations
        ),
    }


def compute_experiment(name: str, exp_dir: Path, project: Path) -> dict[str, Any]:
    generations = _load_generations(exp_dir)
    generation_reach = _reach_totals(generations, exp_dir, project)
    overall_reached = sum(reached for reached, _ in generation_reach.values())
    overall_total = sum(total for _, total in generation_reach.values())
    final_reach = generation_reach.get(max(generation_reach)) if generation_reach else None

    weird = _weird_and_rationality_stats(exp_dir, generations)
    weird_total = weird["weird_a"] + weird["weird_b"] + weird["weird_c"]
    distinct_sequences, reached_run_count = _diversity_stats(generations)

    return {
        "name": name,
        "dir": exp_dir,
        "generation_reach": generation_reach,
        "overall_reached": overall_reached,
        "overall_total": overall_total,
        "final_reach": final_reach,
        "archive": _archive_stats(exp_dir),
        "weird": weird,
        "weird_total": weird_total,
        "rationality": _rationality_summary(exp_dir),
        "distinct_sequences": distinct_sequences,
        "reached_run_count": reached_run_count,
        "shaped": _shaped_allies_quality_stats(generations),
    }


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "-"
    return f"{100.0 * numerator / denominator:.1f}% ({numerator}/{denominator})"


def _num(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _int_or_dash(value: int | None) -> str:
    return "-" if value is None else str(value)


def _metric_rows(
    stats_list: list[dict[str, Any]], baseline_name: str
) -> list[tuple[str, list[str]]]:
    def archive_field(stats: dict[str, Any], field: str) -> Any:
        return stats["archive"][field] if stats["archive"] else None

    def rationality_field(stats: dict[str, Any], field: str) -> Any:
        return stats["rationality"][field] if stats["rationality"] else None

    baseline = next(s for s in stats_list if s["name"] == baseline_name)
    paired = {
        s["name"]: _paired_gen0_shaped_diff(s, baseline) for s in stats_list
    }

    return [
        (
            "整形適応度 shaped: 全ラン平均",
            [_num(s["shaped"]["shaped_mean"]) for s in stats_list],
        ),
        (
            "整形適応度 shaped: 世代0平均",
            [_num(s["shaped"]["gen0_shaped_mean"]) for s in stats_list],
        ),
        (
            f"整形適応度 shaped: 世代0の対基準線差（平均、基準線={baseline_name}）",
            [_num(paired[s["name"]][0]) for s in stats_list],
        ),
        (
            "整形適応度 shaped: 世代0で差が正だったランの割合",
            [_pct(paired[s["name"]][1], paired[s["name"]][2]) for s in stats_list],
        ),
        (
            "決戦時の仲間数（平均）",
            [_num(s["shaped"]["allies_at_contest_mean"]) for s in stats_list],
        ),
        (
            "決戦に至ったランの割合",
            [
                _pct(s["shaped"]["contest_runs"], s["shaped"]["total_runs"])
                for s in stats_list
            ],
        ),
        (
            "quality平均（全ラン、到達不問）",
            [_num(s["shaped"]["quality_mean"]) for s in stats_list],
        ),
        (
            "到達率（全体）",
            [_pct(s["overall_reached"], s["overall_total"]) for s in stats_list],
        ),
        (
            "到達率（最終世代）",
            [_pct(*s["final_reach"]) if s["final_reach"] else "-" for s in stats_list],
        ),
        (
            "アーカイブ: 占有マス数",
            [_int_or_dash(archive_field(s, "occupied_cells")) for s in stats_list],
        ),
        (
            "アーカイブ: 平均quality",
            [_num(archive_field(s, "avg_quality")) for s in stats_list],
        ),
        (
            "アーカイブ: 最大quality",
            [_num(archive_field(s, "max_quality")) for s in stats_list],
        ),
        (
            "変な行動率（合計）",
            [_pct(s["weird_total"], s["weird"]["denominator"]) for s in stats_list],
        ),
        (
            "内訳: 休止/撤退（敵対者不在）",
            [_pct(s["weird"]["weird_a"], s["weird"]["denominator"]) for s in stats_list],
        ),
        (
            "内訳: 対味方 fight/sabotage/mislead",
            [_pct(s["weird"]["weird_b"], s["weird"]["denominator"]) for s in stats_list],
        ),
        (
            "内訳: 変装",
            [_pct(s["weird"]["weird_c"], s["weird"]["denominator"]) for s in stats_list],
        ),
        (
            "合理性層: 判定器呼び出し合計",
            [_int_or_dash(rationality_field(s, "judge_calls")) for s in stats_list],
        ),
        (
            "合理性層: 表サイズ（最終）",
            [_int_or_dash(rationality_field(s, "table_size")) for s in stats_list],
        ),
        (
            "合理性層: 温度待ち秒",
            [_num(rationality_field(s, "thermal_wait_seconds"), 1) for s in stats_list],
        ),
        (
            "合理性層: m_rat 中央値",
            [
                _num(statistics.median(s["weird"]["m_rats"])) if s["weird"]["m_rats"] else "-"
                for s in stats_list
            ],
        ),
        (
            "合理性層: m_rat 5%点",
            [
                _num(_quantile(s["weird"]["m_rats"], 0.05)) if s["weird"]["m_rats"] else "-"
                for s in stats_list
            ],
        ),
        (
            "合理性層: m_rat 95%点",
            [
                _num(_quantile(s["weird"]["m_rats"], 0.95)) if s["weird"]["m_rats"] else "-"
                for s in stats_list
            ],
        ),
        (
            "合理性層: p_rat が None でない割合",
            [
                _pct(s["weird"]["p_rat_present"], s["weird"]["p_rat_denominator"])
                for s in stats_list
            ],
        ),
        (
            "多様性: 到達ランの相異なり数/到達ラン数",
            [
                f'{s["distinct_sequences"]}/{s["reached_run_count"]}'
                if s["reached_run_count"]
                else "-"
                for s in stats_list
            ],
        ),
        (
            "多様性: 相異なり比",
            [
                _num(s["distinct_sequences"] / s["reached_run_count"])
                if s["reached_run_count"]
                else "-"
                for s in stats_list
            ],
        ),
    ]


def _generation_table_rows(
    stats_list: list[dict[str, Any]],
) -> list[tuple[str, list[str]]]:
    all_generations = sorted(
        {generation for s in stats_list for generation in s["generation_reach"]}
    )
    rows = []
    for generation in all_generations:
        row = []
        for s in stats_list:
            pair = s["generation_reach"].get(generation)
            row.append(_pct(*pair) if pair else "-")
        rows.append((str(generation), row))
    return rows


def _judgement_rows(
    stats_list: list[dict[str, Any]],
    baseline_name: str,
) -> list[tuple[str, str, str, str, str]]:
    baseline = next(s for s in stats_list if s["name"] == baseline_name)
    baseline_rate = (
        baseline["overall_reached"] / baseline["overall_total"]
        if baseline["overall_total"]
        else 0.0
    )
    baseline_cells = baseline["archive"]["occupied_cells"] if baseline["archive"] else 0
    baseline_weird_denominator = baseline["weird"]["denominator"]
    baseline_weird_rate = (
        baseline["weird_total"] / baseline_weird_denominator
        if baseline_weird_denominator
        else 0.0
    )
    baseline_gen0_shaped = baseline["shaped"]["gen0_shaped_mean"]

    rows = []
    for s in stats_list:
        rate = s["overall_reached"] / s["overall_total"] if s["overall_total"] else 0.0
        cells = s["archive"]["occupied_cells"] if s["archive"] else 0
        weird_denominator = s["weird"]["denominator"]
        weird_rate = s["weird_total"] / weird_denominator if weird_denominator else 0.0
        gen0_shaped = s["shaped"]["gen0_shaped_mean"]
        label = s["name"] + ("（基準線）" if s["name"] == baseline_name else "")
        if gen0_shaped is None or baseline_gen0_shaped is None:
            shaped_pass = "-"
        else:
            shaped_pass = "PASS" if gen0_shaped > baseline_gen0_shaped else "FAIL"
        rows.append(
            (
                label,
                "PASS" if rate > baseline_rate else "FAIL",
                "PASS" if cells >= 0.8 * baseline_cells else "FAIL",
                "PASS" if weird_rate < baseline_weird_rate else "FAIL",
                shaped_pass,
            )
        )
    return rows


def _markdown_table(header: list[str], rows: list[tuple[str, list[str]]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    for label, values in rows:
        lines.append("| " + " | ".join([label, *values]) + " |")
    return "\n".join(lines)


def build_report(stats_list: list[dict[str, Any]], baseline_name: str) -> str:
    names = [s["name"] for s in stats_list]
    lines = ["# Jev Stage 3 実験比較レポート", ""]
    lines.append(
        "対象実験: "
        + ", ".join(f"{s['name']} ({s['dir']})" for s in stats_list)
    )
    lines.append(f"基準線: {baseline_name}")
    lines.append("")

    lines.append("## 指標一覧")
    lines.append("")
    lines.append(_markdown_table(["指標", *names], _metric_rows(stats_list, baseline_name)))
    lines.append("")

    lines.append("## 世代別到達率")
    lines.append("")
    lines.append(_markdown_table(["世代", *names], _generation_table_rows(stats_list)))
    lines.append("")

    lines.append(f"## 判定（基準線: {baseline_name}）")
    lines.append("")
    judgement_header = [
        "実験",
        "到達率>基準線",
        "占有マス数>=基準線の80%",
        "変な行動率<基準線",
        "shaped平均(世代0)>基準線",
    ]
    judgement_lines = [
        "| " + " | ".join(judgement_header) + " |",
        "| " + " | ".join(["---"] * len(judgement_header)) + " |",
    ]
    for label, reach_pass, cells_pass, weird_pass, shaped_pass in _judgement_rows(
        stats_list, baseline_name
    ):
        judgement_lines.append(
            "| "
            + " | ".join([label, reach_pass, cells_pass, weird_pass, shaped_pass])
            + " |"
        )
    lines.append("\n".join(judgement_lines))
    lines.append("")

    return "\n".join(lines)


def _parse_runs(pairs: list[str]) -> list[tuple[str, Path]]:
    runs = []
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--runs entries must look like name=dir, got: {pair!r}")
        name, _, directory = pair.partition("=")
        runs.append((name, Path(directory)))
    return runs


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", nargs="+", required=True, metavar="NAME=DIR",
        help="one or more name=directory pairs, e.g. k0=runs/s3-k0 k03=runs/s3-k03",
    )
    parser.add_argument("--out", type=Path, required=True, help="Markdown report path")
    parser.add_argument(
        "--project", type=Path, default=Path("projects/momotaro"),
        help="project directory holding world.yaml (fallback target_ending source)",
    )
    parser.add_argument(
        "--baseline", default=None,
        help="experiment name to judge against (default: first --runs entry)",
    )
    args = parser.parse_args(argv)

    runs = _parse_runs(args.runs)
    names = [name for name, _ in runs]
    baseline_name = args.baseline or names[0]
    if baseline_name not in names:
        raise SystemExit(f"--baseline {baseline_name!r} is not among --runs {names}")

    stats_list = [
        compute_experiment(name, directory, args.project) for name, directory in runs
    ]
    report = build_report(stats_list, baseline_name)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
