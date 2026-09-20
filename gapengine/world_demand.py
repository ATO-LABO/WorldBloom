"""Read-only "world demand" report (WB-WORLDGROW-001, stage 2).

Scans a GA experiment's layers.jsonl files for the protagonist's (or a given
subject's) decisions and aggregates them per zone: how much the subject
dwells there, how often they repeat the same move, how often it was a
wasted swing, and how few candidates the policy had to pick from.

Stage 1 tried an equal-weight "pressure"/"demand" composite score and found
it doesn't separate zones on real data. The signal that does separate them:
a verb that whiffs almost every time in a zone the story leans on heavily
(see `WHIFF_RATE_MIN`/`WASTED_SHARE_MIN` below) -- these become `triggers`,
candidates for "the world's content is thin here".

Pure aggregation, no I/O beyond reading the given paths. Callers decide
whether/where to persist the report (scripts/world_demand.py's --save,
gapengine/evolve.py's world_expansion="detect").
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

UNKNOWN_ZONE = "(unknown)"
# Passing through or stalling says nothing about what the subject wants from a zone.
PASSIVE_VERBS = frozenset({"move", "rest", "withdraw"})

# ponytail: thresholds eyeballed on exp12-momotaro only. Recalibrate when more worlds are measured.
WHIFF_RATE_MIN = 0.5
WASTED_SHARE_MIN = 0.02
# A one-in-one whiff in a tiny run is noise, not a thin world.
WHIFFS_MIN = 10


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
                details = row.get("details") or {}
                # Learning a plain fact leaves no trace in the layer snapshot, so
                # the engine logs it as effective=False; it still found something.
                found = bool(details.get("learned") or details.get("gathered"))
                if (effective is False and not found) or result == "invalid":
                    bucket["ineffective"] += 1
                    bucket["verb_whiffs"][verb] += 1
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

    triggers = []
    for zone, b in zones.items():
        if zone == UNKNOWN_ZONE:
            continue
        zone_dwell_share = _round(b["dwell"] / total_dwell) if total_dwell else 0.0
        for verb, count in b["verbs"].items():
            whiffs = b["verb_whiffs"][verb]
            whiff_rate = whiffs / count if count else 0.0
            wasted_share = whiffs / total_dwell if total_dwell else 0.0
            if whiffs >= WHIFFS_MIN and whiff_rate >= WHIFF_RATE_MIN and wasted_share >= WASTED_SHARE_MIN:
                triggers.append({
                    "zone": zone, "verb": verb, "count": count, "whiffs": whiffs,
                    "whiff_rate": _round(whiff_rate), "wasted_share": _round(wasted_share),
                    "zone_dwell_share": zone_dwell_share,
                })
    triggers.sort(key=lambda t: (-t["wasted_share"], t["zone"], t["verb"]))

    report_zones = []
    for zone, b in zones.items():
        dwell = b["dwell"]
        dwell_share = _round(dwell / total_dwell) if total_dwell else 0.0
        repeat_rate = _round(b["repeats"] / dwell) if dwell else 0.0
        ineffective_rate = _round(b["ineffective"] / dwell) if dwell else 0.0
        mean_p_prec = _mean(b["p_prec"])
        mean_m_nov = _mean(b["m_nov"])
        mean_candidates = _mean(b["candidates"])
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
        })
    report_zones.sort(key=lambda z: (-(z["dwell_share"] or 0.0), z["zone"]))

    # Every (zone, verb) pair's [count, whiffs] -- report_zones["verbs"] only
    # keeps the top 5 per zone, but callers computing a specific trigger's
    # before/after counts (gapengine/world_patch_trial.py) need the exact
    # pair regardless of rank.
    verb_counts = {
        zone: {verb: [count, b["verb_whiffs"][verb]] for verb, count in b["verbs"].items()}
        for zone, b in zones.items()
    }

    return {
        "files": files_read,
        "skipped_paths": skipped_paths,
        "subject_decisions": subject_decisions,
        "zones": report_zones,
        "triggers": triggers,
        "verb_counts": verb_counts,
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


def build_report(experiment_dir: Path, *, use_all: bool = False, subject: str | None = None) -> dict:
    """Load an experiment's layers.jsonl files and build the full report
    (aggregates + archive summary + schema/thresholds metadata)."""

    paths, archive = _load_paths(experiment_dir, use_all)
    report = collect(paths, subject=subject)
    report["archive"] = _archive_summary(experiment_dir, archive)
    report["schema_version"] = 1
    report["thresholds"] = {"whiff_rate_min": WHIFF_RATE_MIN, "wasted_share_min": WASTED_SHARE_MIN,
                            "whiffs_min": WHIFFS_MIN}
    return report
