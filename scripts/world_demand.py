"""Read-only "world demand" report (WB-WORLDGROW-001, stage 1).

Scans a GA experiment's layers.jsonl files for the protagonist's (or a given
subject's) decisions and aggregates them per zone: how much the subject
dwells there, how often they repeat the same move, how often it was a
wasted swing, and how few candidates the policy had to pick from. Zones
with high "demand" are where the world's content is thin relative to how
much the story leans on them.

Writes nothing under the experiment directory. Prints a Japanese table to
stdout; pass --out to also write the JSON report elsewhere.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

UNKNOWN_ZONE = "(unknown)"
# Passing through or stalling says nothing about what the subject wants from a zone.
PASSIVE_VERBS = frozenset({"move", "rest", "withdraw"})


def _round(value):
    return None if value is None else round(value, 4)


def _top(counter: Counter, n: int = 5) -> list:
    """Most common first; ties by name so the report never depends on row order."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))[:n]


def _mean(values):
    return _round(statistics.fmean(values)) if values else None


def _load_paths(experiment_dir: Path, use_all: bool) -> tuple[list[Path], dict | None]:
    """Return (layers paths to read, parsed archive.json or None)."""

    archive_path = experiment_dir / "archive.json"
    archive = json.loads(archive_path.read_text(encoding="utf-8")) if archive_path.is_file() else None
    if use_all or archive is None:
        return sorted(experiment_dir.rglob("layers.jsonl")), archive
    seen = []
    for key in sorted(archive.get("cells", {})):
        exemplar = archive["cells"][key].get("exemplar", {})
        layers_path = exemplar.get("layers_path")
        if layers_path and layers_path not in seen:
            seen.append(layers_path)
    return [experiment_dir / p for p in seen], archive


def _zone_after(row: dict, subject: str, current: str | None) -> str | None:
    """Follow the subject's zone through snapshots and deltas.

    Runs recorded without explanations carry no per-decision zone, but every
    zone change still shows up in a snapshot or in some row's delta.
    """

    if row.get("kind") == "snapshot":
        if row.get("subject") == subject:
            return (row.get("layers") or {}).get("zone") or current
        return current
    delta = row.get("delta") or {}
    moved = None
    if row.get("subject") == subject:
        moved = (delta.get("actor") or {}).get("zone")
    moved = moved or ((delta.get("targets") or {}).get(subject) or {}).get("zone")
    return moved or current


def collect(paths: list[Path], subject: str | None = None) -> dict:
    """Pure aggregation over already-resolved layers.jsonl paths.

    `subject` fixes the subject name for every file; when None, each file's
    own header.protagonist is used instead.
    """

    files_read = 0
    skipped_paths = 0
    subject_decisions = 0
    zones: dict[str, dict] = {}

    for path in paths:
        if not path.is_file():
            skipped_paths += 1
            continue
        files_read += 1
        file_subject = subject
        seen_actions: set[tuple[str, str, str]] = set()
        tracked_zone = None
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                kind = row.get("kind")
                if kind == "header" and subject is None:
                    file_subject = row.get("protagonist")
                    continue
                zone_before = tracked_zone
                tracked_zone = _zone_after(row, file_subject, tracked_zone)
                if kind != "decision" or row.get("subject") != file_subject:
                    continue
                subject_decisions += 1
                explanation = row.get("explanation") or {}
                zone = explanation.get("zone") or zone_before or UNKNOWN_ZONE
                bucket = zones.setdefault(zone, {
                    "decisions": 0, "dwell": 0, "verbs": Counter(), "verb_whiffs": Counter(),
                    "repeats": 0, "ineffective": 0, "ineffective_reasons": Counter(),
                    "p_prec": [], "m_nov": [], "candidates": [],
                })
                bucket["decisions"] += 1
                verb = row.get("verb")
                if verb in PASSIVE_VERBS:
                    continue
                bucket["dwell"] += 1
                bucket["verbs"][verb] += 1

                action_key = (zone, verb, json.dumps(row.get("args"), ensure_ascii=False, sort_keys=True))
                if action_key in seen_actions:
                    bucket["repeats"] += 1
                else:
                    seen_actions.add(action_key)

                effective = row.get("effective")
                result = row.get("result")
                if effective is False or result == "invalid":
                    bucket["ineffective"] += 1
                    bucket["verb_whiffs"][verb] += 1
                    details = row.get("details") or {}
                    reason = details.get("reason") or result
                    bucket["ineffective_reasons"][reason] += 1

                policy = row.get("policy")
                if isinstance(policy, dict):
                    if isinstance(policy.get("p_prec"), (int, float)):
                        bucket["p_prec"].append(policy["p_prec"])
                    if isinstance(policy.get("m_nov"), (int, float)):
                        bucket["m_nov"].append(policy["m_nov"])
                selection = explanation.get("selection")
                if isinstance(selection, dict) and isinstance(selection.get("total_candidates"), (int, float)):
                    bucket["candidates"].append(selection["total_candidates"])

    total_dwell = sum(b["dwell"] for b in zones.values())
    report_zones = []
    for zone, b in zones.items():
        dwell = b["dwell"]
        dwell_share = _round(dwell / total_dwell) if total_dwell else 0.0
        repeat_rate = _round(b["repeats"] / dwell) if dwell else 0.0
        ineffective_rate = _round(b["ineffective"] / dwell) if dwell else 0.0
        mean_p_prec = _mean(b["p_prec"])
        mean_m_nov = _mean(b["m_nov"])
        mean_candidates = _mean(b["candidates"])
        # ponytail: naive composite, equal weights. Recalibrate once we see real data across worlds.
        pressure_parts = [v for v in (repeat_rate, ineffective_rate, mean_p_prec) if v is not None]
        pressure = _round(statistics.fmean(pressure_parts)) if pressure_parts else 0.0
        demand = _round((dwell_share or 0.0) * pressure)
        report_zones.append({
            "zone": zone,
            "decisions": b["decisions"],
            "dwell": dwell,
            "dwell_share": dwell_share,
            # (verb, count, share of those that changed nothing)
            "verbs": [(v, c, _round(b["verb_whiffs"][v] / c)) for v, c in _top(b["verbs"])],
            "repeat_rate": repeat_rate,
            "ineffective_rate": ineffective_rate,
            "ineffective_reasons": _top(b["ineffective_reasons"]),
            "mean_p_prec": mean_p_prec,
            "mean_m_nov": mean_m_nov,
            "mean_candidates": mean_candidates,
            "pressure": pressure,
            "demand": demand,
        })
    report_zones.sort(key=lambda z: (-z["demand"], z["zone"]))

    return {
        "files": files_read,
        "skipped_paths": skipped_paths,
        "subject_decisions": subject_decisions,
        "zones": report_zones,
    }


def _archive_summary(experiment_dir: Path, archive: dict | None) -> dict | None:
    if archive is None:
        return None
    cells = archive.get("cells", {})
    last_improved: dict[int, int] = {}
    for cell in cells.values():
        generation = cell.get("generation")
        if isinstance(generation, int):
            last_improved[generation] = last_improved.get(generation, 0) + 1
    generations = None
    summary_path = experiment_dir / "summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        raw = summary.get("generations")
        # generations in summary.json is the per-generation stats list; report its count.
        generations = len(raw) if isinstance(raw, list) else raw
    return {
        "cells": len(cells),
        "last_improved": {str(k): last_improved[k] for k in sorted(last_improved)},
        "generations": generations,
    }


def _print_table(report: dict) -> None:
    print(f"読んだファイル数: {report['files']}（スキップ {report['skipped_paths']}）"
          f" / 主人公の決定数: {report['subject_decisions']}")
    print()
    header = ("ゾーン", "滞在決定数", "滞在シェア", "繰り返し率", "空振り率", "前例確率", "候補数", "圧力", "需要")
    print("\t".join(header))
    for z in report["zones"]:
        print("\t".join(str(v) for v in (
            z["zone"], z["dwell"], z["dwell_share"], z["repeat_rate"], z["ineffective_rate"],
            z["mean_p_prec"], z["mean_candidates"], z["pressure"], z["demand"])))
        verbs = "、".join(f"{v}×{c}(空振り{w:.0%})" for v, c, w in z["verbs"]) or "(なし)"
        reasons = "、".join(f"{r}×{c}" for r, c in z["ineffective_reasons"]) or "(なし)"
        print(f"    主な行動: {verbs} / 空振り理由: {reasons}")
    archive = report.get("archive")
    if archive:
        print()
        print(f"archive: セル数={archive['cells']} / 世代数={archive['generations']} "
              f"/ セルの最終改善世代: {archive['last_improved']}")


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment_dir", type=Path)
    parser.add_argument("--all", action="store_true",
                         help="scan every layers.jsonl instead of just archive exemplars")
    parser.add_argument("--subject", help="decision subject to track (default: each file's protagonist)")
    parser.add_argument("--out", type=Path, help="also write the JSON report here")
    args = parser.parse_args(argv)

    experiment_dir = args.experiment_dir.resolve()
    if args.out is not None:
        out_resolved = args.out.resolve()
        if out_resolved == experiment_dir or experiment_dir in out_resolved.parents:
            raise ValueError("--out must not be inside experiment_dir")

    paths, archive = _load_paths(experiment_dir, args.all)
    report = collect(paths, subject=args.subject)
    report["archive"] = _archive_summary(experiment_dir, archive)

    _print_table(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
