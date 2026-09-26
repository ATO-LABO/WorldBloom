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
import re
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

# WB-WORLDGROW-002 S1: route-layer (rho>0) triggers. Only ever populated on
# a route-wired run -- see route_counts/blocked_counts docstrings below.
# ponytail: eyeballed on a fresh momotaro_plus2 rho=1.0 sweep (11 genomes x
# 40 seeds, scripts/route_eval.py's --skip-ga fixed-gene sweep re-run under
# this stage's code, runs/wg1/sweep) -- 31,860 decisions, 1,801 "lost", all
# 1,801 converging on the single requirement "reach:村" (share 1.0, this
# world's only real blocker right now: 村, 縄's only source, gets excluded
# the moment the protagonist leaves without the treasure). "ignorance" saw
# only 33 decisions total (鬼ヶ島, 0.5% of that zone's route-tagged
# decisions) in this sweep -- WHIFFS_MIN-sized MIN plus a much higher
# SHARE_MIN than whiff's own (whiff's 2% is a share of the *whole world's*
# dwell; ignorance/blocked's share is already scoped to a zone or to all
# "lost" decisions, a far smaller population, so a real signal should be a
# much bigger fraction of it). Recalibrate once more worlds/genomes are
# measured, same as WHIFF_* above.
IGNORANCE_MIN = 10
IGNORANCE_SHARE_MIN = 0.3
BLOCKED_MIN = 10
BLOCKED_SHARE_MIN = 0.3
# M4 required fix: BLOCKED_MIN alone let one single run's ~60 identical
# "lost" decisions (one genome/seed stuck in a loop for the rest of the run)
# read as a world-wide blocker. Recalibrated on the same wg1/sweep rho=1.0
# re-run cited above: 47/47 runs saw the dominant has_item:縄 requirement
# (every run, not a fluke of one) -- BLOCKED_RUNS_MIN=3 asks for it to
# recur in at least a handful of independent runs, comfortably below that
# 47 but well above the single-run false positive this fix targets.
BLOCKED_RUNS_MIN = 3


def _round(value):
    return None if value is None else round(value, 4)


def _top(counter: Counter, n: int = 5) -> list:
    """Most common first; ties by name so the report never depends on row order."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))[:n]


def _mean(values):
    return _round(statistics.fmean(values)) if values else None


# A GA run's protagonist layers land at exactly this shape (gapengine/evolve.py's
# run_individual: out_dir=g<gen>/ind-<index>, seed_dir=out_dir/seed-<seed>).
# rglob("layers.jsonl") also picks up gapengine/lineage.py's rerun cache
# (lineage/<ref>/seed-N/layers.jsonl) and other non-population copies -- opening
# the lineage screen must not change what a "demand" scan counts.
_RUN_LAYERS_RE = re.compile(r"^g\d+/ind-\d+/seed-\d+/layers\.jsonl$")


def _load_paths(
    experiment_dir: Path, use_all: bool
) -> tuple[list[Path], list[Path], dict | None, str]:
    """Return (whiff-population paths, full-population paths, parsed
    archive.json or None, population mode).

    S1 review 1 required fix M3 (design judgment J): the second list is
    always the *full* g*/ind-*/seed-*/layers.jsonl population, regardless
    of report mode -- ``collect()``'s route_counts/ignorance/blocked
    aggregation reads it unconditionally, since an exemplars-only report
    that only ever ran a handful of individuals to term would otherwise
    read as "no blocker anywhere" even when most of the population is
    stuck on one (rho1-big-p1: exemplars saw 0 lost decisions, the full
    population 30). Whiff's own population still follows ``use_all`` --
    the first list is unchanged from before this fix."""

    archive_path = experiment_dir / "archive.json"
    archive = json.loads(archive_path.read_text(encoding="utf-8")) if archive_path.is_file() else None
    found = sorted(
        p for p in experiment_dir.rglob("layers.jsonl")
        if _RUN_LAYERS_RE.match(p.relative_to(experiment_dir).as_posix())
    )
    if use_all or archive is None:
        return found, found, archive, "all"
    seen = []
    for key in sorted(archive.get("cells", {})):
        exemplar = archive["cells"][key].get("exemplar", {})
        layers_path = exemplar.get("layers_path")
        if layers_path and layers_path not in seen:
            seen.append(layers_path)
    return [experiment_dir / p for p in seen], found, archive, "exemplars"


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


def collect(
    paths: list[Path], subject: str | None = None, *, route_paths: list[Path] | None = None
) -> dict:
    """Pure aggregation over already-resolved layers.jsonl paths.

    `subject` fixes the subject name for every file; when None, each file's
    own header.protagonist is used instead.

    ``route_paths`` (S1 review 1 required fix M3, design judgment J):
    defaults to ``paths`` (unchanged behavior) -- pass the full population
    when ``paths`` is only the exemplars subset, so route_counts/ignorance/
    blocked (below) always scan every g*/ind-*/seed-*/layers.jsonl run,
    while zones/triggers/verb_counts keep following ``paths`` (whiff's own
    population, unaffected by report mode). ``route_paths`` is always a
    superset of ``paths`` in practice (an exemplar's layers_path is itself
    one of the full population's runs), so every file in it is opened
    exactly once below."""

    files_read = 0
    skipped_paths = 0
    subject_decisions = 0
    zones: dict[str, dict] = {}

    # WB-WORLDGROW-002 S1: route/demand aggregation (empty on a route-free,
    # rho=0 run -- no row ever has a policy.route dict, so every ignorance/
    # blocked trigger below is naturally skipped and route_counts/
    # blocked_counts come back empty, matching a pre-S1 report exactly).
    route_counts: dict[str, Counter] = {}
    route_zone_total: Counter = Counter()
    ignorance_by_zone: Counter = Counter()
    blocked_by_requirement: dict[str, dict] = {}
    lost_total = 0

    whiff_paths = set(paths)
    scan_paths = route_paths if route_paths is not None else paths

    for path in scan_paths:
        is_whiff_file = path in whiff_paths
        if not path.is_file():
            if is_whiff_file:
                skipped_paths += 1
            continue
        if is_whiff_file:
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
                if is_whiff_file:
                    subject_decisions += 1
                explanation = row.get("explanation") or {}
                zone = explanation.get("zone") or zone_before or UNKNOWN_ZONE

                # WB-WORLDGROW-002 S1: policy.route (only present on a route-
                # wired, rho>0 run) classifies the decision itself -- counted
                # for every decision, including move/rest/withdraw, unlike the
                # whiff aggregation below (a "lost" decision is very often a
                # move or a rest). Text is never read, only the structured
                # kind/cause/blocked_on/blocked_detail fields (S1 §3.5 review
                # note: text changes across code versions, structure
                # doesn't). Always aggregated from the full population
                # (M3, design judgment J), regardless of whiff's own mode.
                route = row.get("policy", {}).get("route") if isinstance(row.get("policy"), dict) else None
                if isinstance(route, dict):
                    route_kind = route.get("kind")
                    route_cause = route.get("cause")
                    route_key = (
                        route_kind
                        if route_kind in ("advance", "prepare", "lost")
                        else f"detour:{route_cause or 'none'}"
                    )
                    route_counts.setdefault(zone, Counter())[route_key] += 1
                    route_zone_total[zone] += 1
                    if route_kind == "detour" and route_cause == "ignorance":
                        ignorance_by_zone[zone] += 1
                    elif route_kind == "lost":
                        lost_total += 1
                        requirement = route.get("blocked_on")
                        if requirement:
                            detail = route.get("blocked_detail") or {}
                            entry = blocked_by_requirement.setdefault(
                                requirement,
                                {"count": 0, "runs": set(), "zones": Counter(),
                                 "source_zones": Counter(), "held_by": Counter(),
                                 "reasons": Counter()},
                            )
                            entry["count"] += 1
                            entry["runs"].add(path)
                            entry["zones"][zone] += 1
                            for source_zone in detail.get("zones") or ():
                                entry["source_zones"][source_zone] += 1
                            for holder in detail.get("held_by") or ():
                                entry["held_by"][holder] += 1
                            reason = detail.get("reason")
                            if reason:
                                entry["reasons"][reason] += 1

                if not is_whiff_file:
                    continue

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
    route_total_all = sum(route_zone_total.values())

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
                    "kind": "whiff",
                    "zone": zone, "verb": verb, "count": count, "whiffs": whiffs,
                    "whiff_rate": _round(whiff_rate), "wasted_share": _round(wasted_share),
                    "zone_dwell_share": zone_dwell_share,
                })
    # whiff's own order/index must never move (viewer/world_demand_view.py's
    # proposal buttons key off the raw position) -- ignorance/blocked are
    # appended after, each sorted only within their own kind.
    triggers.sort(key=lambda t: (-t["wasted_share"], t["zone"], t["verb"]))

    ignorance_triggers = []
    for zone, count in ignorance_by_zone.items():
        if zone == UNKNOWN_ZONE:
            continue
        route_total = route_zone_total.get(zone, 0)
        share = _round(count / route_total) if route_total else 0.0
        if count >= IGNORANCE_MIN and share >= IGNORANCE_SHARE_MIN:
            zone_dwell = zones.get(zone, {}).get("dwell", 0)
            ignorance_triggers.append({
                "kind": "ignorance",
                "zone": zone, "count": count, "share": share,
                "zone_dwell_share": _round(zone_dwell / total_dwell) if total_dwell else 0.0,
            })
    ignorance_triggers.sort(key=lambda t: (-t["share"], t["zone"]))
    triggers.extend(ignorance_triggers)

    blocked_triggers = []
    for requirement, entry in blocked_by_requirement.items():
        count = entry["count"]
        runs = len(entry["runs"])
        share = _round(count / lost_total) if lost_total else 0.0
        # M4: share of *all* routed decisions (not just the lost ones) --
        # "share" above can trivially sit near 1.0 when one requirement
        # dominates every lost decision (real data: 1,801/1,801); lost_share
        # says how much of the whole population that actually is.
        lost_share = _round(count / route_total_all) if route_total_all else 0.0
        # M4 required fix: a single run producing dozens of the same lost
        # decision is one anecdote, not a world-wide blocker -- gate on the
        # number of distinct runs it showed up in too, not only the raw
        # decision count (BLOCKED_MIN alone let a single 60-decision run
        # trip the trigger).
        if (
            count >= BLOCKED_MIN
            and share >= BLOCKED_SHARE_MIN
            and runs >= BLOCKED_RUNS_MIN
        ):
            blocked_triggers.append({
                "kind": "blocked",
                "requirement": requirement, "count": count, "share": share,
                "runs": runs, "lost_share": lost_share,
                # "stuck_zones": where the lost decision itself happened;
                # "source_zones"/"held_by": from blocked_detail -- where the
                # *requirement* (has_item:.../knows:...) would come from and
                # who currently holds it, when known.
                "stuck_zones": _top(entry["zones"], 3),
                "source_zones": _top(entry["source_zones"], 3),
                "held_by": _top(entry["held_by"], 5),
                "reason": entry["reasons"].most_common(1)[0][0] if entry["reasons"] else None,
            })
    blocked_triggers.sort(key=lambda t: (-t["share"], t["requirement"]))
    triggers.extend(blocked_triggers)

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

    # WB-WORLDGROW-002 S1: raw route counts (empty {} on a route-free run --
    # no zone/requirement is ever added above without a policy.route dict).
    report_route_counts = {
        zone: dict(sorted(counts.items())) for zone, counts in sorted(route_counts.items())
    }
    report_blocked_counts = {
        requirement: {
            "count": entry["count"],
            "runs": len(entry["runs"]),
            "zones": dict(sorted(entry["zones"].items())),
            "source_zones": dict(sorted(entry["source_zones"].items())),
            "held_by": dict(sorted(entry["held_by"].items())),
            "reasons": dict(sorted(entry["reasons"].items())),
        }
        for requirement, entry in sorted(blocked_by_requirement.items())
    }

    return {
        "files": files_read,
        "skipped_paths": skipped_paths,
        "subject_decisions": subject_decisions,
        "zones": report_zones,
        "triggers": triggers,
        "verb_counts": verb_counts,
        "route_counts": report_route_counts,
        "blocked_counts": report_blocked_counts,
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

    paths, route_paths, archive, mode = _load_paths(experiment_dir, use_all)
    report = collect(paths, subject=subject, route_paths=route_paths)
    report["archive"] = _archive_summary(experiment_dir, archive)
    report["schema_version"] = 2
    report["thresholds"] = {
        "whiff_rate_min": WHIFF_RATE_MIN, "wasted_share_min": WASTED_SHARE_MIN,
        "whiffs_min": WHIFFS_MIN,
        "ignorance_min": IGNORANCE_MIN, "ignorance_share_min": IGNORANCE_SHARE_MIN,
        "blocked_min": BLOCKED_MIN, "blocked_share_min": BLOCKED_SHARE_MIN,
        "blocked_runs_min": BLOCKED_RUNS_MIN,
    }
    report["population"] = {"mode": mode, "files": report["files"], "skipped": report["skipped_paths"]}
    return report
