"""Trial gate for a proposed world-expansion patch (WB-WORLDGROW-001, stage 3b).

Reruns a handful of the experiment's best archived individuals under the
world both before and after the patch, using each one's exact (genome,
precedent) -- the same "reproduce a pruned run" mechanism as
gapengine/lineage.py, but without lineage's on-disk cache: a trial never
writes into the experiment directory, only into a caller-owned scratch
`work_dir`.

Seeds are *not* the archive's own exemplar seed: an archived individual was
selected because some seed of it reached the ending, so rerunning it on that
same seed makes `base` reach almost by construction while `patched` (a
different random path) regresses to the individual's ordinary reach rate --
a comparison biased against every patch, not just bad ones. Instead each
individual is rerun on a fixed block of seeds that were never part of
selection, on both worlds, so the comparison is apples-to-apples.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import yaml

from gapengine import lineage
from gapengine.evolve import run_individual
from gapengine.qd import read_rows
from gapengine.world_demand import collect
from gapengine.world_patch import PatchError, absolutize_references, apply_patches

REPO_ROOT = Path(__file__).resolve().parents[1]

TRIAL_SEED_BASE = 100000

TRIAL_TOLERANCE_SHARE = 0.2
TRIAL_TOLERANCE_MIN = 2
# ponytail: tolerance is ~2 sd of the reach-count difference at n=40, p~0.3; recalibrate per world.


def _subject_ids(subjects_dir: Path) -> list[str]:
    ids = []
    for path in sorted(subjects_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            ids.append(data["id"])
    return ids


def _select_cells(archive: dict, max_runs: int) -> list[tuple[str, dict]]:
    cells = archive.get("cells", {}) or {}
    ordered = sorted(cells.items(), key=lambda kv: (-float(kv[1].get("quality", 0.0)), kv[0]))
    return ordered[:max_runs]


def _job(*, ctx: dict, world_path: Path, out_dir: Path, elite: dict,
         index: int, seeds: list[int], precedent_json: str, antagonist_precedent_json,
         antagonist_genome) -> dict[str, Any]:
    return {
        "action_cfg": ctx["action_cfg"],
        "action_graph_path": (str(ctx["action_graph_path"]) if ctx["action_graph_path"] is not None else None),
        "antagonist": ctx["antagonist"],
        "antagonist_action_cfg": ctx["antagonist_action_cfg"],
        "antagonist_genome": antagonist_genome,
        "antagonist_precedent_json": antagonist_precedent_json,
        "genome": elite["genome"],
        "index": index,
        "logical_root": str(out_dir),
        "out_dir": str(out_dir),
        "parents": elite.get("parents", []),
        "precedent_json": precedent_json,
        "protagonist": ctx["protagonist"],
        "qd_cfg": ctx["qd_cfg"],
        "record_explanations": True,
        "rules": ctx["rules"],
        "seeds": list(seeds),
        "subjects_dir": str(ctx["subjects_dir"]),
        "target_ending": ctx["target_ending"],
        "world_path": str(world_path),
    }


def _trigger_counts(report: dict, zones: set[str], verb: str) -> dict[str, int]:
    verb_counts = report.get("verb_counts", {})
    count = whiffs = 0
    for zone in zones:
        pair = verb_counts.get(zone, {}).get(verb, [0, 0])
        count += pair[0]
        whiffs += pair[1]
    return {"count": count, "whiffs": whiffs}


def run_trial(experiment_dir: Path, patch: dict, *, work_dir: Path, max_runs: int = 5,
              seeds_per_run: int = 8) -> dict:
    """Rerun up to `max_runs` archived individuals under `patch`, base vs
    patched, each on `seeds_per_run` fresh seeds. Never writes into
    `experiment_dir` -- everything lands under `work_dir` (caller-owned,
    typically a TemporaryDirectory)."""

    from viewer.data import RunRepository

    experiment_dir = Path(experiment_dir)
    work_dir = Path(work_dir)
    repository = RunRepository(experiment_dir.parent)
    ctx = lineage._resolve_world_context(repository, experiment_dir)
    base_source = "frozen_inputs" if (experiment_dir / "manifest.json").is_file() else "repository"

    world_path = Path(ctx["world_path"])
    base = yaml.safe_load(world_path.read_text(encoding="utf-8"))
    subject_ids = _subject_ids(Path(ctx["subjects_dir"]))

    try:
        # Apply before absolutizing base's own references: absolutize_references
        # can inject absolute-path strings into the world, and validate_patch's
        # name-collision scan (`_all_strings`) must never see those.
        patched = apply_patches(base, [patch], subject_ids=subject_ids)
    except PatchError as error:
        return {
            "schema_version": 1,
            "runs": 0,
            "skipped": 0,
            "reached_base": 0,
            "reached_patched": 0,
            "errors": [{"world": "patched", "error": repr(error)}],
            "trigger": None,
            "used_new": 0,
            "passed": False,
            "reasons": [f"パッチを適用できません: {error}"],
            "total_runs": 0,
            "seeds_per_run": seeds_per_run,
            "tolerance": TRIAL_TOLERANCE_MIN,
            "base_source": base_source,
            "easier": False,
        }
    absolutize_references(base, world_path.parent, REPO_ROOT)
    absolutize_references(patched, world_path.parent, REPO_ROOT)

    base_world_path = work_dir / "base" / "world.yaml"
    patched_world_path = work_dir / "patched" / "world.yaml"
    base_world_path.parent.mkdir(parents=True, exist_ok=True)
    patched_world_path.parent.mkdir(parents=True, exist_ok=True)
    base_world_path.write_text(yaml.safe_dump(base, allow_unicode=True, sort_keys=False), encoding="utf-8")
    patched_world_path.write_text(yaml.safe_dump(patched, allow_unicode=True, sort_keys=False), encoding="utf-8")
    world_paths = {"base": base_world_path, "patched": patched_world_path}

    archive = json.loads(repository.safe_path(experiment_dir, "archive.json").read_text(encoding="utf-8"))
    selected = _select_cells(archive, max_runs)

    runs = 0
    skipped = 0
    total_runs = 0
    errors: list[dict[str, Any]] = []
    reached: dict[str, int] = {"base": 0, "patched": 0}
    shaped_sum: dict[str, float] = {"base": 0.0, "patched": 0.0}
    layers_paths: dict[str, list[Path]] = {"base": [], "patched": []}

    for cell_key, elite in selected:
        generation = elite["generation"]
        precedent_path = repository.safe_path(experiment_dir, f"g{generation}/precedent.json")
        if not precedent_path.is_file():
            skipped += 1
            continue
        exemplar = elite.get("exemplar", {})
        match = lineage._EXEMPLAR_PATH.match(str(exemplar.get("layers_path", "")))
        if not match:
            skipped += 1
            continue
        index = int(match.group(2))
        seeds = [TRIAL_SEED_BASE + j for j in range(seeds_per_run)]
        antagonist_genome = exemplar.get("antagonist_genome")

        antagonist_precedent_json = None
        antagonist_precedent_path = repository.safe_path(
            experiment_dir, f"g{generation}/precedent.antagonist.json")
        if antagonist_precedent_path.is_file():
            antagonist_precedent_json = antagonist_precedent_path.read_text(encoding="utf-8")
        precedent_json = precedent_path.read_text(encoding="utf-8")

        runs += 1
        out_dirs: dict[str, Path] = {}
        side_runs: dict[str, list[dict]] = {}
        individual_failed = False
        for label, world_path_for_label in world_paths.items():
            out_dir = work_dir / label / f"run-{runs}"
            out_dirs[label] = out_dir
            job = _job(
                ctx=ctx, world_path=world_path_for_label, out_dir=out_dir, elite=elite,
                index=index, seeds=seeds, precedent_json=precedent_json,
                antagonist_precedent_json=antagonist_precedent_json,
                antagonist_genome=antagonist_genome,
            )
            try:
                result = run_individual(job)
            except Exception as error:  # noqa: BLE001 -- a bad rerun must not abort the whole trial
                errors.append({"cell": cell_key, "world": label, "error": repr(error)})
                individual_failed = True
                continue
            side_runs[label] = result["runs"]

        if individual_failed:
            continue
        total_runs += seeds_per_run
        for label in ("base", "patched"):
            for run_result in side_runs[label]:
                if run_result["reached"]:
                    reached[label] += 1
                shaped_sum[label] += float(run_result.get("shaped") or 0.0)
                layers_paths[label].append(out_dirs[label] / run_result["layers_path"])

    trigger = None
    patch_trigger = patch.get("trigger")
    if isinstance(patch_trigger, dict) and patch_trigger.get("zone") and patch_trigger.get("verb"):
        trigger_zone = patch_trigger["zone"]
        trigger_verb = patch_trigger["verb"]
        branch_zones = {
            z.get("name") for z in (patch.get("add", {}).get("zones") or [])
            if isinstance(z, dict) and z.get("parent") == trigger_zone
        }
        zones_to_sum = {trigger_zone} | branch_zones
        base_report = collect(layers_paths["base"], subject=ctx["protagonist"])
        patched_report = collect(layers_paths["patched"], subject=ctx["protagonist"])
        trigger = {
            "zone": trigger_zone, "verb": trigger_verb,
            "base": _trigger_counts(base_report, zones_to_sum, trigger_verb),
            "patched": _trigger_counts(patched_report, zones_to_sum, trigger_verb),
        }

    new_names: set[str] = set()
    for zone in (patch.get("add", {}).get("zones") or []):
        if isinstance(zone, dict) and isinstance(zone.get("name"), str):
            new_names.add(zone["name"])
    for item in (patch.get("add", {}).get("items") or []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            new_names.add(item["name"])
    for fact in (patch.get("add", {}).get("facts") or []):
        if isinstance(fact, dict) and isinstance(fact.get("id"), str):
            new_names.add(fact["id"])

    used_new = 0
    protagonist = ctx["protagonist"]
    for path in layers_paths["patched"]:
        for row in read_rows(path):
            if row.get("kind") != "decision" or row.get("subject") != protagonist:
                continue
            blob = json.dumps([row.get("args"), row.get("details")], ensure_ascii=False)
            if any(name in blob for name in new_names):
                used_new += 1

    tolerance = max(TRIAL_TOLERANCE_MIN, math.ceil(TRIAL_TOLERANCE_SHARE * total_runs))
    passed = total_runs >= 1 and not errors and reached["patched"] >= reached["base"] - tolerance
    # Recorded only: where almost no fresh-seed rerun reaches the ending, reach
    # counts cannot show harm, but the mean distance-to-ending proxy still moves.
    shaped_mean = {label: round(shaped_sum[label] / total_runs, 4) if total_runs else None
                   for label in ("base", "patched")}
    easier = reached["patched"] > reached["base"] + tolerance

    reasons = []
    if base_source == "repository":
        reasons.append("この実験は凍結入力を持たないため、現在の projects/ の世界を基準にしています")
    if total_runs == 0:
        reasons.append("再実行できる個体がありませんでした")
    else:
        reasons.append(f"到達 {reached['base']}→{reached['patched']}（{total_runs}本中、許容差 {tolerance}）")
        reasons.append(f"結末への近さ（shaped の平均）{shaped_mean['base']}→{shaped_mean['patched']}")
    if easier:
        reasons.append("到達が大きく増えています（結末が易しくなった可能性）")
    if trigger is not None and (trigger["base"]["count"] or trigger["patched"]["count"]):
        reasons.append(
            f"『{trigger['zone']}』の『{trigger['verb']}』空振り "
            f"{trigger['base']['whiffs']}→{trigger['patched']['whiffs']}"
        )
    if used_new:
        reasons.append(f"新要素を使った決定 {used_new} 件")
    if errors:
        reasons.append(f"再実行エラー {len(errors)} 件")

    return {
        "schema_version": 1,
        "runs": runs,
        "skipped": skipped,
        "shaped_base": shaped_mean["base"],
        "shaped_patched": shaped_mean["patched"],
        "reached_base": reached["base"],
        "reached_patched": reached["patched"],
        "errors": errors,
        "trigger": trigger,
        "used_new": used_new,
        "passed": passed,
        "reasons": reasons,
        "total_runs": total_runs,
        "seeds_per_run": seeds_per_run,
        "tolerance": tolerance,
        "base_source": base_source,
        "easier": easier,
    }
