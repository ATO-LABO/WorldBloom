"""Primary-lineage reconstruction, turning-point detection and gene deltas for
elite ancestry (WB-LINEAGE-002).

Read-only against engine/ and StorySim: it never invents a new (world,
genome, seed, precedent) combination for an ancestor -- it only reruns
run_individual() with the exact one an ancestor already used (or reads back
an earlier such rerun's cached layers.jsonl), which reproduces the original
run byte-for-byte by construction (see gapengine/evolve.py::run_individual).
That lets a pruned ancestor's layers.jsonl be recovered without touching sim
internals, rng, or any existing GA/QD logic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from engine.world import World
from gapengine.evolve import _best_reached, _lineage_stats, _load_yaml, run_individual
from gapengine.genome import CATEGORIES
from gapengine.qd import read_rows, reached as qd_reached


_IND_REF = re.compile(r"^g(\d+)/ind-(\d+)$")
_ARCHIVE_REF = re.compile(r"^g(\d+)/archive/(.+)$")
_EXEMPLAR_PATH = re.compile(r"^g(\d+)/ind-(\d+)/seed-(\d+)/layers\.jsonl$")

# The 9 genome scalars compared for lineage distance and the "personality that
# moved" display (design doc WB-LINEAGE-002). rule_bits is deliberately
# excluded -- it is meta-evolution bookkeeping, not part of this genome's
# "personality" axes.
_SCALAR_LABELS: dict[str, str] = {
    **{f"category_weight.{category}": f"カテゴリ{category}" for category in CATEGORIES},
    "risk_tolerance": "慎重さ",
    "stance_shift_bias": "態度の変わりやすさ",
    "novelty_drive": "前例を避ける度合い",
}


class LineageError(ValueError):
    """Raised for malformed lineage data (a bug, not an expected user state)."""


def genome_scalars(genome: Mapping[str, Any]) -> dict[str, float]:
    weights = genome.get("category_weight") or {}
    values = {
        f"category_weight.{category}": float(weights.get(category, 0.5))
        for category in CATEGORIES
    }
    values["risk_tolerance"] = float(genome.get("risk_tolerance", 0.5))
    values["stance_shift_bias"] = float(genome.get("stance_shift_bias", 0.0))
    values["novelty_drive"] = float(genome.get("novelty_drive", 0.0))
    return values


def genome_distance(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    """L1 distance over the 9 scalars (rule_bits excluded)."""

    left, right = genome_scalars(a), genome_scalars(b)
    return sum(abs(left[key] - right[key]) for key in left)


def genome_shift(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Per-scalar deltas between two genomes, sorted by |delta| descending."""

    left, right = genome_scalars(before), genome_scalars(after)
    shifts = [
        {
            "key": key,
            "label": _SCALAR_LABELS[key],
            "before": left[key],
            "after": right[key],
            "delta": right[key] - left[key],
        }
        for key in left
    ]
    shifts.sort(key=lambda item: (-abs(item["delta"]), item["key"]))
    return shifts


def _trait_series(
    ancestry_entries: Sequence[Mapping[str, Any]],
    gene_shift: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The single gene that moved most at one step (`gene_shift[0]`), traced
    across the *whole* primary lineage (`ancestry_entries`, oldest -> newest)
    -- one scalar's actual trajectory, not a line stitched from whichever
    gene happened to move most at each different step (design doc
    WB-LINEAGE-002 §5.4/§5.5)."""

    key = gene_shift[0]["key"]
    return {
        "key": key,
        "label": _SCALAR_LABELS[key],
        "values": [entry["scalars"][key] for entry in ancestry_entries],
    }


def _generation_results(
    repository: Any,
    experiment: Path,
    generation: int,
) -> list[Mapping[str, Any]]:
    path = repository.safe_path(experiment, f"g{generation}/results.json")
    if not path.is_file():
        raise LineageError(f"missing g{generation}/results.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise LineageError(f"g{generation}/results.json must be a list")
    return raw


def _node_from_result(
    result: Mapping[str, Any],
    *,
    ref: str,
    generation: int,
    index: int,
) -> dict[str, Any]:
    best = _best_reached(result)
    cell = result.get("cell")
    return {
        "ref": ref,
        "generation": generation,
        "index": index,
        "genome": dict(result["genome"]),
        "shaped": float(result["shaped"]),
        "parents": [str(value) for value in result.get("parents", [])],
        "cell": [str(value) for value in cell] if cell else None,
        "reached": best is not None,
        "quality": float(best["quality"]) if best is not None else None,
        "opponent_genome": result.get("opponent_genome"),
        "rerun_error": None,
    }


def _resolve_individual(
    repository: Any,
    experiment: Path,
    generation: int,
    index: int,
    *,
    ref: str,
) -> dict[str, Any]:
    results = _generation_results(repository, experiment, generation)
    matches = [
        result
        for result in results
        if int(result.get("index", -1)) == index
    ]
    if len(matches) != 1:
        raise LineageError(
            f"g{generation}/results.json has {len(matches)} entries with "
            f"index {index} (expected exactly 1) for ref {ref!r}"
        )
    return _node_from_result(
        matches[0],
        ref=ref,
        generation=generation,
        index=index,
    )


def _resolve_archive_ref(
    repository: Any,
    experiment: Path,
    generation_limit: int,
    cell_text: str,
    *,
    ref: str,
) -> dict[str, Any]:
    # Parent-ref cells are joined with "-" (gapengine/evolve.py::_parent_pool),
    # unlike archive.json's own "|"-joined keys. Categories/volatility bins
    # never contain "-" in any shipped template, so the last "-" is the split
    # point.
    category, _, volatility_bin = cell_text.rpartition("-")
    if not category:
        raise LineageError(f"malformed archive cell in ref {ref!r}")

    best_key: tuple[float, int, int] | None = None
    best_generation: int | None = None
    best_result: Mapping[str, Any] | None = None
    for generation in range(generation_limit + 1):
        for result in _generation_results(repository, experiment, generation):
            cell = result.get("cell")
            if not cell or list(cell) != [category, volatility_bin]:
                continue
            best_run = _best_reached(result)
            if best_run is None:
                continue
            # Highest quality wins; ties broken by earliest generation, then
            # lowest index -- this mirrors gapengine/qd.py::Archive.insert's
            # quality-first ordering. insert()'s own tie-break uses reach_rate
            # (not generation/index); see the "_insert_result cross-check"
            # test for how often that can matter in practice.
            key = (
                float(best_run["quality"]),
                -generation,
                -int(result["index"]),
            )
            if best_key is None or key > best_key:
                best_key = key
                best_generation = generation
                best_result = result
    if best_result is None or best_generation is None:
        raise LineageError(
            f"no individual found for archive cell {cell_text!r} at or "
            f"before g{generation_limit}"
        )
    resolved = _node_from_result(
        best_result,
        ref=f"g{best_generation}/ind-{int(best_result['index'])}",
        generation=best_generation,
        index=int(best_result["index"]),
    )
    resolved["ref"] = ref
    return resolved


def resolve_ref(
    repository: Any,
    experiment: Path,
    ref: str,
) -> dict[str, Any]:
    match = _IND_REF.match(ref)
    if match:
        return _resolve_individual(
            repository,
            experiment,
            int(match.group(1)),
            int(match.group(2)),
            ref=ref,
        )
    match = _ARCHIVE_REF.match(ref)
    if match:
        return _resolve_archive_ref(
            repository,
            experiment,
            int(match.group(1)),
            match.group(2),
            ref=ref,
        )
    raise LineageError(f"unrecognized lineage parent ref: {ref!r}")


def _resolve_archive_elite(
    repository: Any,
    experiment: Path,
    elite: Mapping[str, Any],
) -> dict[str, Any]:
    exemplar = elite.get("exemplar") or {}
    layers_path = str(exemplar.get("layers_path", ""))
    match = _EXEMPLAR_PATH.match(layers_path)
    if not match:
        raise LineageError(
            f"cannot parse generation/index from exemplar layers_path: "
            f"{layers_path!r}"
        )
    generation, index, seed = (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    )
    node = _resolve_individual(
        repository,
        experiment,
        generation,
        index,
        ref=f"g{generation}/ind-{index}",
    )
    node["seed"] = seed
    return node


def _choose_nearer_parent(
    genome: Mapping[str, Any],
    candidates: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    def sort_key(candidate: Mapping[str, Any]) -> tuple[float, float, int, int, int]:
        distance = genome_distance(genome, candidate["genome"])
        archive_ref = 0 if "/archive/" in str(candidate["ref"]) else 1
        return (
            distance,
            -float(candidate["shaped"]),
            archive_ref,
            int(candidate["generation"]),
            int(candidate["index"]),
        )

    return min(candidates, key=sort_key)


def primary_lineage(
    repository: Any,
    experiment: Path,
    cell_key: str,
) -> list[dict[str, Any]]:
    """The elite's ancestry, newest-first: [elite, parent, grandparent, ...].

    Stops at the first node with no parents (a g0 individual or a random
    immigrant introduced later). Each non-root node has exactly two parent
    refs (gapengine/evolve.py::_next_population); the nearer one by genome
    L1 distance continues the chain (ties: higher shaped, then archive-ref
    over ind-ref, then earlier generation, then lower index).
    """

    archive = json.loads(
        repository.safe_path(experiment, "archive.json").read_text(
            encoding="utf-8",
        )
    )
    cells = archive.get("cells") or {}
    elite = cells.get(cell_key)
    if not isinstance(elite, Mapping):
        raise LineageError(f"cell not found in archive: {cell_key!r}")

    chain = [_resolve_archive_elite(repository, experiment, elite)]
    guard = 0
    while chain[-1]["parents"]:
        guard += 1
        if guard > 10_000:
            raise LineageError("lineage chain exceeded a sane depth")
        refs = chain[-1]["parents"][:2]
        candidates = [
            resolve_ref(repository, experiment, ref) for ref in refs
        ]
        chosen = (
            candidates[0]
            if len(candidates) == 1
            else _choose_nearer_parent(chain[-1]["genome"], candidates)
        )
        chain.append(chosen)
    return chain


def _safe_ref_name(ref: str) -> str:
    return ref.replace("/", "-")


def _has_recorded_explanations(rows: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        row.get("kind") == "decision" and "explanation" in row
        for row in rows
    )


def _resolve_world_context(
    repository: Any,
    experiment: Path,
    *, template_dir=None,
) -> dict[str, Any]:
    """cfg needed to rerun any node: project/template dirs, action/qd/rules
    config, target ending and protagonist/antagonist. Restored either from a
    catalog run's frozen `inputs/` snapshot (manifest.json + config.json) or,
    for a legacy/CLI run with no such snapshot, by resolving the live
    projects//templates/ directories from a still-present layers.jsonl's
    world name (viewer/data.py::resolve_genre) -- deferred import to avoid a
    module-load cycle (viewer.data imports gapengine.lineage for
    lineage_view()).

    Whether the run coevolved an antagonist is never read from manifest.json
    (a legacy/CLI run has none, and would otherwise always look
    non-coevolved even when it was): `_rerun_node` decides per-generation, by
    whether that generation's precedent.antagonist.json file exists.
    `antagonist_action_cfg` here is likewise decided by file presence, not by
    a coevolve flag (gapengine/evolve.py::evolve loads
    action_graph.antagonist.yaml the same way when coevolve is on).
    """

    from gapengine.world_patch_inputs import resolve_experiment_inputs
    resolved = resolve_experiment_inputs(experiment, template_dir=template_dir)
    project_dir = resolved["world_path"].parent
    template_dir = resolved["template_dir"]

    world_path = project_dir / "world.yaml"
    subjects_dir = project_dir / "subjects"
    action_graph_path = template_dir / "action_graph.yaml"
    if not action_graph_path.is_file():
        action_graph_path = None

    action_cfg = dict(
        _load_yaml(
            template_dir / "action_graph.yaml",
            {"nodes": [], "edges": []},
        )
    )
    antagonist_action_graph_path = template_dir / "action_graph.antagonist.yaml"
    antagonist_action_cfg = (
        dict(_load_yaml(antagonist_action_graph_path, action_cfg))
        if antagonist_action_graph_path.is_file()
        else action_cfg
    )
    qd_cfg = dict(
        _load_yaml(
            template_dir / "qd.yaml",
            {
                "categories": list(CATEGORIES),
                "volatility_bins": ["low", "mid", "high"],
            },
        )
    )
    rules = list(_load_yaml(template_dir / "rules.yaml", []))

    summary = json.loads(
        repository.safe_path(experiment, "summary.json").read_text(
            encoding="utf-8",
        )
    )
    target_ending = summary.get("target_ending")

    world = World.from_yaml(world_path, action_graph_path=action_graph_path)
    if target_ending is not None:
        world.set_target_ending(target_ending)

    return {
        "source": resolved["source"],
        "template_dir": template_dir,
        "references": resolved["references"],
        "action_cfg": action_cfg,
        "action_graph_path": action_graph_path,
        "antagonist": world.antagonist,
        "antagonist_action_cfg": antagonist_action_cfg,
        "protagonist": world.protagonist,
        "qd_cfg": qd_cfg,
        "rules": rules,
        "subjects_dir": subjects_dir,
        "target_ending": target_ending,
        "world_path": world_path,
    }


def _rerun_node(
    repository: Any,
    experiment: Path,
    node: Mapping[str, Any],
    seed: int,
    ctx: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Rows for `node` at the fixed lineage `seed`, from cache or a rerun."""

    from execution.provenance import directory_lock

    out_dir = repository.safe_path(
        experiment,
        f"lineage/{_safe_ref_name(str(node['ref']))}",
    )
    layers_path = out_dir / f"seed-{seed}" / "layers.jsonl"
    if layers_path.is_file():
        return read_rows(layers_path)

    # execution.provenance.directory_lock (same pattern as
    # viewer/data.py::toggle_selection): a second same-process thread racing
    # here blocks on its internal RLock, then sees layers_path already
    # written and returns that instead of rerunning; a genuinely separate
    # process racing on the same directory fails fast (ConfigError, caught
    # as rerun_error by build_lineage_report) instead of corrupting
    # layers.jsonl.
    with directory_lock(out_dir):
        if layers_path.is_file():
            return read_rows(layers_path)

        precedent_path = repository.safe_path(
            experiment,
            f"g{node['generation']}/precedent.json",
        )
        if not precedent_path.is_file():
            raise LineageError(
                f"missing g{node['generation']}/precedent.json"
            )
        antagonist_precedent_json = None
        antagonist_precedent_path = repository.safe_path(
            experiment,
            f"g{node['generation']}/precedent.antagonist.json",
        )
        if antagonist_precedent_path.is_file():
            antagonist_precedent_json = antagonist_precedent_path.read_text(
                encoding="utf-8",
            )

        original_path = repository.safe_path(
            experiment,
            f"g{node['generation']}/ind-{node['index']}/seed-{seed}/layers.jsonl",
        )
        record_explanations = True
        if original_path.is_file():
            record_explanations = _has_recorded_explanations(
                read_rows(original_path)
            )

        job = {
            "action_cfg": ctx["action_cfg"],
            "action_graph_path": (
                str(ctx["action_graph_path"])
                if ctx["action_graph_path"] is not None
                else None
            ),
            "antagonist": ctx["antagonist"],
            "antagonist_action_cfg": ctx["antagonist_action_cfg"],
            "antagonist_genome": node.get("opponent_genome"),
            "antagonist_precedent_json": antagonist_precedent_json,
            "genome": node["genome"],
            "index": node["index"],
            "logical_root": str(out_dir),
            "out_dir": str(out_dir),
            "parents": node["parents"],
            "precedent_json": precedent_path.read_text(encoding="utf-8"),
            "protagonist": ctx["protagonist"],
            "qd_cfg": ctx["qd_cfg"],
            "record_explanations": record_explanations,
            "rules": ctx["rules"],
            "seeds": [seed],
            "subjects_dir": str(ctx["subjects_dir"]),
            "target_ending": ctx["target_ending"],
            "world_path": str(ctx["world_path"]),
        }
        run_individual(job)
        return read_rows(layers_path)


def _protagonist_decisions(
    rows: Sequence[Mapping[str, Any]],
    protagonist: str,
) -> list[Mapping[str, Any]]:
    return [
        row
        for row in rows
        if row.get("kind") == "decision" and row.get("subject") == protagonist
    ]


def _same_action(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return a.get("verb") == b.get("verb") and list(a.get("args") or []) == list(
        b.get("args") or []
    )


def _find_turning(
    parent_rows: Sequence[Mapping[str, Any]],
    child_rows: Sequence[Mapping[str, Any]],
    protagonist: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """The first (parent_decision, child_decision) pair where the protagonist's
    choice first differs *and* either side's decision was effective.

    Walks both decision lists in lockstep: advances past every matching
    (verb, args) pair, and at the first mismatch returns it as the turning
    point if either side was effective. If neither side was effective, that
    mismatched pair is not a turning point -- skip past it (advance both
    indices by one) and keep looking for the next mismatch, all the way to
    the end of either list if needed."""

    parent_decisions = _protagonist_decisions(parent_rows, protagonist)
    child_decisions = _protagonist_decisions(child_rows, protagonist)
    i = j = 0
    while i < len(parent_decisions) and j < len(child_decisions):
        parent_decision, child_decision = parent_decisions[i], child_decisions[j]
        if _same_action(parent_decision, child_decision):
            i += 1
            j += 1
            continue
        if bool(parent_decision.get("effective")) or bool(
            child_decision.get("effective")
        ):
            return parent_decision, child_decision
        i += 1
        j += 1
    return None


def _selection_of(decision: Mapping[str, Any]) -> Mapping[str, Any] | None:
    explanation = decision.get("explanation")
    if not isinstance(explanation, Mapping):
        return None
    selection = explanation.get("selection")
    return selection if isinstance(selection, Mapping) else None


def _candidates_of(decision: Mapping[str, Any]) -> list[dict[str, Any]]:
    selection = _selection_of(decision)
    if selection is None:
        return []
    candidates = selection.get("candidates")
    return list(candidates) if isinstance(candidates, list) else []


def _candidate_stats_of(decision: Mapping[str, Any]) -> dict[str, Any] | None:
    """total/recorded/truncated counts for the "N of M recorded" note
    (engine/decision_record.py::record_distribution), or None when nothing
    was recorded (record_explanations was off for that rerun)."""

    selection = _selection_of(decision)
    if selection is None:
        return None
    return {
        "total_candidates": selection.get("total_candidates"),
        "recorded_candidates": selection.get("recorded_candidates"),
        "truncated": bool(selection.get("truncated")),
    }


def _present_of(decision: Mapping[str, Any]) -> list[str] | None:
    explanation = decision.get("explanation")
    if not isinstance(explanation, Mapping):
        return None
    present = explanation.get("present")
    return list(present) if isinstance(present, list) else None


def _find_contest_decision(
    rows: Sequence[Mapping[str, Any]],
    protagonist: str,
    antagonist: str,
) -> Mapping[str, Any] | None:
    """The decisive-contest fight decision between protagonist and
    antagonist, first one wins -- same condition as
    gapengine/evolve.py::_lineage_stats's contest detection, so both agree on
    which fight "the" contest is."""

    for row in rows:
        if (
            row.get("kind") != "decision"
            or row.get("verb") != "fight"
            or row.get("result") not in ("won", "lost")
        ):
            continue
        args = row.get("args")
        target = args[0] if isinstance(args, list) and args else None
        if {row.get("subject"), target} != {protagonist, antagonist}:
            continue
        return row
    return None


def _win_probability(
    rows: Sequence[Mapping[str, Any]],
    protagonist: str,
    antagonist: str,
) -> float | None:
    """The protagonist's pre-roll win chance at the decisive contest, read
    directly off the fight decision's recorded `p_actor` (engine/verbs.py::
    _fight, engine/contest.py::resolve) instead of recomputing anything: it
    is already the actor's win probability, so it *is* the protagonist's
    chance when they were the actor, and 1 - p_actor when the antagonist was
    (decisive-contest is symmetric regardless of who initiated it, WB-
    LINEAGE-001)."""

    row = _find_contest_decision(rows, protagonist, antagonist)
    if row is None:
        return None
    details = row.get("details")
    if not isinstance(details, Mapping) or "p_actor" not in details:
        return None
    p_actor = float(details["p_actor"])
    return p_actor if row.get("subject") == protagonist else 1.0 - p_actor


def _outcome_summary(
    rows: Sequence[Mapping[str, Any]],
    ctx: Mapping[str, Any],
) -> dict[str, Any]:
    protagonist, antagonist = ctx["protagonist"], ctx["antagonist"]
    ending_row = next(
        (
            row
            for row in rows
            if row.get("kind") == "event" and row.get("verb") == "ending"
        ),
        None,
    )
    downed_row = next(
        (
            row
            for row in rows
            if row.get("kind") == "event"
            and row.get("verb") == "downed"
            and row.get("subject") == protagonist
        ),
        None,
    )
    stats = _lineage_stats(rows, protagonist, antagonist)
    target_ending = ctx.get("target_ending")
    return {
        "ending": ending_row.get("id") if ending_row is not None else None,
        "downed": downed_row is not None,
        "allies_final": stats["allies_final"],
        "reached": (
            bool(qd_reached(rows, target_ending))
            if target_ending is not None
            else None
        ),
        "win_probability": _win_probability(rows, protagonist, antagonist),
    }


def build_lineage_report(
    repository: Any,
    experiment: Path,
    cell_key: str,
) -> dict[str, Any]:
    """The full model for the lineage page: the primary-lineage band, every
    turning point found between adjacent ancestors, and the first-reach
    marker -- computed once per (experiment, cell, seed, elite ref) and
    cached at <experiment>/lineage/<cell_key with '|' replaced by '-'>-cache.json
    (the seed and elite ref are checked against the cache's own contents
    before it is reused, not encoded in the filename).
    """

    cache_path = repository.safe_path(
        experiment,
        f"lineage/{cell_key.replace('|', '-')}-cache.json",
    )
    chain = primary_lineage(repository, experiment, cell_key)
    seed = chain[0]["seed"]
    elite_ref = chain[0]["ref"]
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            cached.get("seed") == seed
            and cached.get("cell") == cell_key
            and cached.get("elite_ref") == elite_ref
            and all("scalars" in entry for entry in cached.get("ancestry", []))
            and all("trait_series" in turning for turning in cached.get("turnings", []))
        ):
            return cached

    ancestry = list(reversed(chain))  # oldest -> newest for display
    ctx = _resolve_world_context(repository, experiment)

    rows_by_ref: dict[str, list[dict[str, Any]] | None] = {}
    for node in ancestry:
        try:
            rows_by_ref[node["ref"]] = _rerun_node(
                repository,
                experiment,
                node,
                seed,
                ctx,
            )
        except Exception as error:
            # Broad on purpose: a legacy run's world/template can have been
            # edited since it ran, and run_individual() then raises whatever
            # StorySim/engine happens to raise (KeyError, etc.), not just
            # LineageError -- degrade that one ancestor instead of 500ing the
            # whole page.
            node["rerun_error"] = str(error)
            rows_by_ref[node["ref"]] = None

    first_reach_index = next(
        (
            index
            for index, node in enumerate(ancestry)
            if rows_by_ref[node["ref"]] is not None
            and ctx.get("target_ending") is not None
            and qd_reached(rows_by_ref[node["ref"]], ctx["target_ending"])
        ),
        None,
    )

    ancestry_entries = []
    for position, node in enumerate(ancestry):
        gene_shift = (
            genome_shift(ancestry[position - 1]["genome"], node["genome"])
            if position > 0
            else None
        )
        node_rows = rows_by_ref[node["ref"]]
        ancestry_entries.append(
            {
                "ref": node["ref"],
                "generation": node["generation"],
                "index": node["index"],
                "shaped": node["shaped"],
                "quality": node["quality"],
                "reached": node["reached"],
                "rerun_error": node.get("rerun_error"),
                "scalars": genome_scalars(node["genome"]),
                "gene_shift": gene_shift,
                "outcome": (
                    _outcome_summary(node_rows, ctx)
                    if node_rows is not None
                    else None
                ),
            }
        )

    turnings: list[dict[str, Any]] = []
    for position in range(len(ancestry) - 1):
        older, newer = ancestry[position], ancestry[position + 1]
        older_rows, newer_rows = rows_by_ref[older["ref"]], rows_by_ref[newer["ref"]]
        if older_rows is None or newer_rows is None:
            continue
        found = _find_turning(older_rows, newer_rows, ctx["protagonist"])
        if found is None:
            continue
        parent_decision, child_decision = found
        trait_series = _trait_series(
            ancestry_entries, ancestry_entries[position + 1]["gene_shift"]
        )
        turnings.append(
            {
                "parent_ref": older["ref"],
                "child_ref": newer["ref"],
                "parent_index": position,
                "child_index": position + 1,
                "parent_generation": older["generation"],
                "child_generation": newer["generation"],
                "turn": child_decision.get("turn"),
                "parent_turn": parent_decision.get("turn"),
                "present": (
                    _present_of(child_decision)
                    or _present_of(parent_decision)
                ),
                "parent_action": {
                    "verb": parent_decision.get("verb"),
                    "args": list(parent_decision.get("args") or []),
                },
                "child_action": {
                    "verb": child_decision.get("verb"),
                    "args": list(child_decision.get("args") or []),
                },
                "parent_candidates": _candidates_of(parent_decision),
                "child_candidates": _candidates_of(child_decision),
                "parent_candidate_stats": _candidate_stats_of(parent_decision),
                "child_candidate_stats": _candidate_stats_of(child_decision),
                "gene_shift": ancestry_entries[position + 1]["gene_shift"],
                "trait_series": trait_series,
                "outcome": ancestry_entries[position + 1]["outcome"],
            }
        )

    report = {
        "cell": cell_key,
        "seed": seed,
        "elite_ref": elite_ref,
        "ancestry": ancestry_entries,
        "first_reach_index": first_reach_index,
        "turnings": turnings,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None),
        encoding="utf-8",
    )
    return report
