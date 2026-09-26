"""GA replay panel for the run page (WB-GAVIZ-001).

Server-rendered "what happened this generation" panel: the most recently
published generation's representative individuals, how each child's 9 genes
trace back to its two parents, and whether it broke into the QD archive.
This module builds the data model and the no-JS fallback HTML; the actual
replay animation lives in viewer/static/ga_replay.js, fed through a single
`data-replay` attribute (CSP forbids inline scripts/JSON).

Read-only against gapengine/execution: everything here reads results.json
files and published archive snapshots the GA run already wrote to disk;
nothing here feeds back into the run.
"""
from __future__ import annotations

import json
import sys

from execution.provenance import ConfigError, contained, sha256
from gapengine import lineage
from gapengine.evolve import _best_reached
from gapengine.genome import CATEGORY_MAX, CATEGORY_MIN
from viewer.pages import _escape


_GENE_KEYS = tuple(lineage._SCALAR_LABELS.keys())
_GENE_LABELS = tuple(lineage._SCALAR_LABELS.values())
_GENE_SHORT = ("I", "II", "III", "IV", "V", "VI", "慎重", "態度", "前例")
_GENE_RANGES = tuple(
    (CATEGORY_MIN, CATEGORY_MAX) if key.startswith("category_weight.")
    else (-1.0, 1.0) if key == "stance_shift_bias"
    else (0.0, 1.0)
    for key in _GENE_KEYS
)

_OUTCOME_TEXT = {
    "new": "新しい型の物語を発見。地図が広がる",
    "replaced": "同じ型でより良い物語。入れ替える",
    "rejected": "同じ型にもっと良い物語がある。今回は残らない",
    "unreached": "決められた結末にたどり着けなかった。地図には載らない",
    "unclassified": "結末には届いたが、地図の型に分類できなかった。地図には載らない",
    "kept": "地図に残った",
}

# "kept" is grouped with "new" (mutually exclusive: "kept" only happens when
# prev_cells is unavailable, "new" only when it is -- see pick_representatives).
_PICK_ORDER = ("new", "replaced", "rejected", "unreached", "unclassified")

# Same broad catch used for every disk/schema read in this module: a run
# whose results.json/archive is missing, malformed, or mid-write should make
# the panel disappear (return None), never 500 the run page.
_MODEL_ERRORS = (ConfigError, FileNotFoundError, OSError, ValueError, KeyError, TypeError, IndexError,
                  lineage.LineageError)

_SAME_PARENT_NOTE = "同じ親が2回選ばれた。交叉しても変わらず、違いは突然変異だけ"


def _cell_key(cell):
    return "|".join(cell) if cell else None


def _parent_display(ref):
    """A human-readable version of a lineage parent ref (gN/ind-i or
    gN/archive/CATEGORY-BIN), falling back to the raw ref for anything else.
    Reuses lineage.py's own ref regexes/split rule (archive cell text is
    "-"-joined there, unlike archive.json's own "|"-joined keys -- see
    gapengine/lineage.py::_resolve_archive_ref's comment) instead of
    re-deriving it.
    """
    match = lineage._IND_REF.match(ref)
    if match:
        generation, index = int(match.group(1)), int(match.group(2))
        return f"第 {generation + 1} 世代の個体 #{index}"
    match = lineage._ARCHIVE_REF.match(ref)
    if match:
        generation = int(match.group(1))
        category, _, volatility_bin = match.group(2).rpartition("-")
        if category:
            return f"地図の {category} × {volatility_bin}（第 {generation + 1} 世代に入った物語）"
    return ref


def gene_origins(child, parent_a, parent_b):
    """Per-scalar origin of `child`'s 9 genes against its (up to 2) parents.

    "a"/"b": matches exactly one parent. "same": both parents already agreed
    on that value (so the child matching them proves nothing about which one
    it came from). "mut": matches neither known parent -- definitely a
    mutation. `None`: unknown -- either both parents are unresolved, or only
    one is and the child's value does not match it (it could just as well
    have come from the *other*, unresolved parent, so this is not provably a
    mutation). Comparison is float `==`: crossover copies a parent's scalar
    verbatim, so an exact match is meaningful.
    """
    child_values = list(lineage.genome_scalars(child).values())
    a_values = list(lineage.genome_scalars(parent_a).values()) if parent_a is not None else None
    b_values = list(lineage.genome_scalars(parent_b).values()) if parent_b is not None else None
    origins = []
    for index, value in enumerate(child_values):
        if a_values is not None and b_values is not None:
            a, b = a_values[index], b_values[index]
            if a == b == value:
                origins.append("same")
            elif value == a:
                origins.append("a")
            elif value == b:
                origins.append("b")
            else:
                origins.append("mut")
        elif a_values is not None:
            origins.append("a" if value == a_values[index] else None)
        elif b_values is not None:
            origins.append("b" if value == b_values[index] else None)
        else:
            origins.append(None)
    return origins


def classify_outcome(individual, prev_cells, final_cells):
    """What became of `individual` in the published archive.

    `individual` needs "generation", "parents", "genome", "cell_key",
    "quality", and optionally "classification_status" (from results.json;
    absent on older runs). `prev_cells`/`final_cells` are archive.json-shaped
    {cell_key: elite dict} maps (elite has generation/parents/genome/
    quality); `prev_cells` may be None when the prior generation's
    publication could not be read (distinct from {}, an empty archive).
    """
    cell_key = individual.get("cell_key")
    if cell_key is None:
        if individual.get("classification_status") == "unclassified":
            return {"kind": "unclassified"}
        return {"kind": "unreached"}
    elite = final_cells.get(cell_key)
    is_winner = (
        isinstance(elite, dict)
        and elite.get("generation") == individual.get("generation")
        and list(elite.get("parents") or []) == list(individual.get("parents") or [])
        and isinstance(elite.get("genome"), dict)
        and lineage.genome_scalars(elite["genome"]) == lineage.genome_scalars(individual["genome"])
    )
    if is_winner:
        quality = elite.get("quality")
        if prev_cells is None:
            return {"kind": "kept", "quality": quality}
        prev_elite = prev_cells.get(cell_key)
        if isinstance(prev_elite, dict):
            return {"kind": "replaced", "quality": quality, "prev_quality": prev_elite.get("quality")}
        return {"kind": "new", "quality": quality}
    incumbent_quality = elite.get("quality") if isinstance(elite, dict) else None
    return {"kind": "rejected", "quality": individual.get("quality"), "incumbent_quality": incumbent_quality}


def pick_representatives(individuals, limit=4):
    """Deterministically pick up to `limit` individuals to replay in full.

    One per outcome kind, in a fixed order ("kept" counts as "new"),
    preferring a two-parent individual with the lowest index within that
    kind; any kind with no members is skipped. Leftover slots are filled by
    ascending index over whatever was not already picked.
    """
    def bucket(individual):
        kind = individual["outcome"]["kind"]
        return "new" if kind == "kept" else kind

    picked = []
    picked_indices = set()
    for kind in _PICK_ORDER:
        if len(picked) >= limit:
            break
        candidates = [item for item in individuals if bucket(item) == kind]
        if not candidates:
            continue
        with_parents = [item for item in candidates if len(item.get("parents") or []) == 2]
        pool = with_parents or candidates
        choice = min(pool, key=lambda item: item["index"])
        picked.append(choice)
        picked_indices.add(choice["index"])
    if len(picked) < limit:
        remaining = sorted(
            (item for item in individuals if item["index"] not in picked_indices),
            key=lambda item: item["index"],
        )
        for item in remaining:
            if len(picked) >= limit:
                break
            picked.append(item)
            picked_indices.add(item["index"])
    return picked


def _snapshot(catalog, run_id, root, revision):
    # ponytail: RunCatalog.snapshot() only skips its manifest_sha256 check
    # when the requested revision equals whatever published/current.json
    # says *right now*; every normal caller instead chains a hash forward
    # from a selection recorded at the time that revision *was* current (see
    # execution/selections.py::_read). The replay panel has no such record
    # for an older generation, but published/<rev>/manifest.json is
    # write-once (evolution_worker.publish() only ever adds new revision
    # folders), so hashing it ourselves and feeding it back in is a
    # self-consistency check, not a real chain of trust -- and unlike
    # branching on "is this revision the current one", it also can't race
    # against published/current.json being rewritten between the job
    # ledger's publication_revision being read and this call (the panel
    # would otherwise vanish in that window). Upgrade: record each
    # revision's manifest_sha256 in the job's progress log at publish time
    # if a stronger guarantee is ever needed.
    manifest_sha256 = sha256(contained(root, f"published/{revision}/manifest.json").read_bytes())
    return catalog.snapshot(run_id, revision=revision, manifest_sha256=manifest_sha256, observe=False)


def _resolve_parent(handler, root, ref):
    try:
        node = lineage.resolve_ref(handler.repository, root, ref)
        return {
            "label": ref,
            "display": _parent_display(ref),
            "genes": list(lineage.genome_scalars(node["genome"]).values()),
            "cell_key": _cell_key(node.get("cell")),
        }, node["genome"]
    except Exception:
        # ponytail: an ancestor whose generation was pruned/rewritten can
        # fail to resolve; show the ref with unknown genes rather than
        # failing the whole panel. Upgrade: fall back to a recorded genome
        # in candidates.json if this proves common.
        return {"label": ref, "display": _parent_display(ref), "genes": None, "cell_key": None}, None


def replay_model(handler, job, axes, generation=None):
    """The replay panel's data model for `job`'s most (or a chosen) prior
    published generation, or None when there is nothing to replay (no job,
    no publication yet, a legacy/imported run, an empty generation, or an
    unreadable record).

    Parent genomes are only resolved for the handful of individuals
    pick_representatives() actually selects (at most 2 lineage.resolve_ref
    calls each) -- resolving them for the whole population made this page
    take several seconds on a large run (WB-GAVIZ-001 review).
    """
    if job is None or job.get("run_id") is None or job.get("publication_revision") is None:
        return None
    catalog = handler.repository.catalog
    if catalog is None:
        return None
    run_id = job["run_id"]
    current_revision = job["publication_revision"]
    try:
        root, legacy = catalog.resolve(run_id)
        if legacy:
            return None  # native runs only -- a legacy import has no per-generation results.json to replay
        latest_generation = current_revision - 1
        if not isinstance(generation, int) or generation < 0 or generation > latest_generation:
            generation = latest_generation
        final = _snapshot(catalog, run_id, root, generation + 1)
        final_cells = (final.get("archive") or {}).get("cells") or {}
        raw_results = lineage._generation_results(handler.repository, root, generation)
        if not raw_results:
            return None

        # WB-WORLDGROW-001 段階5b: g0/population.json's own "seed_cell" (only
        # ever present on a run whose --seed-genomes seeded generation 0) and
        # summary.json's seed_genomes.source.run_id -- best-effort, missing/
        # malformed data just means no seed labels, never a broken panel.
        seed_cell_by_index: dict[int, str] = {}
        seed_run: str | None = None
        if generation == 0:
            try:
                population_raw = json.loads(
                    handler.repository.safe_path(root, "g0/population.json").read_text(encoding="utf-8"))
                seed_cell_by_index = {
                    int(item["index"]): str(item["seed_cell"])
                    for item in population_raw if isinstance(item, dict) and "seed_cell" in item
                }
                if seed_cell_by_index:
                    summary_raw = json.loads(
                        handler.repository.safe_path(root, "summary.json").read_text(encoding="utf-8"))
                    seed_run = ((summary_raw.get("seed_genomes") or {}).get("source") or {}).get("run_id")
            except _MODEL_ERRORS:
                seed_cell_by_index = {}
                seed_run = None

        if generation == 0:
            prev_cells = {}
        else:
            try:
                prev_snapshot = _snapshot(catalog, run_id, root, generation)
                prev_cells = (prev_snapshot.get("archive") or {}).get("cells") or {}
            except _MODEL_ERRORS:
                prev_cells = None

        individuals = []
        for entry in raw_results:
            index = int(entry["index"])
            parent_refs = [str(ref) for ref in (entry.get("parents") or [])]
            genome = entry["genome"]
            cell_key = _cell_key(entry.get("cell"))
            best = _best_reached(entry)
            quality = float(best["quality"]) if best is not None else None
            outcome = classify_outcome(
                {"generation": generation, "parents": parent_refs, "genome": genome,
                 "cell_key": cell_key, "quality": quality,
                 "classification_status": entry.get("classification_status")},
                prev_cells, final_cells,
            )
            individual = {
                "index": index, "parents": parent_refs, "genome": genome,
                "cell_key": cell_key, "outcome": outcome,
            }
            if index in seed_cell_by_index:
                individual["seed_cell"] = seed_cell_by_index[index]
            individuals.append(individual)

        counts = {"new": 0, "replaced": 0, "rejected": 0, "unreached": 0, "unclassified": 0, "kept": 0}
        for individual in individuals:
            counts[individual["outcome"]["kind"]] += 1

        representatives = pick_representatives(individuals)
        for individual in representatives:
            genome = individual.pop("genome")
            parent_refs = individual["parents"]
            parents = []
            parent_genomes = [None, None]
            for slot, ref in enumerate(parent_refs[:2]):
                parent, parent_genome = _resolve_parent(handler, root, ref)
                parents.append(parent)
                parent_genomes[slot] = parent_genome
            individual["parents"] = parents
            individual["genes"] = list(lineage.genome_scalars(genome).values())
            individual["origins"] = gene_origins(genome, parent_genomes[0], parent_genomes[1])

        categories, bins = axes
        model = {
            "generation": generation,
            "max_generation": latest_generation,
            "categories": list(categories),
            "bins": list(bins),
            "gene_labels": list(_GENE_LABELS),
            "gene_short": list(_GENE_SHORT),
            "gene_ranges": [list(pair) for pair in _GENE_RANGES],
            "prev_cells": (
                None if prev_cells is None
                else {key: elite.get("quality") for key, elite in prev_cells.items() if isinstance(elite, dict)}
            ),
            "final_cells": {key: elite.get("quality") for key, elite in final_cells.items()
                             if isinstance(elite, dict)},
            "individuals": representatives,
            "population": len(individuals),
            "counts": counts,
        }
        # Opus review R4: only when set -- a seed-less run's embedded JSON
        # must stay byte-identical to before WB-WORLDGROW-001 段階5b
        # (render_panel()/ga_replay.js already treat a missing seed_run the
        # same as None/undefined).
        if seed_run:
            model["seed_run"] = seed_run
        return model
    except _MODEL_ERRORS as error:
        # No logging idiom exists elsewhere in viewer/ to hook into; stderr
        # is enough for this to show up in the same place a startup/worker
        # error would, so a future logic bug degrades to "no panel" loudly
        # instead of silently.
        print(f"ga_replay.replay_model: {run_id}: {type(error).__name__}: {error}", file=sys.stderr)
        return None


def _quality_text(value):
    return f"{value:.3f}" if isinstance(value, (int, float)) else "—"


def _outcome_sentence(outcome):
    kind = outcome["kind"]
    text = _OUTCOME_TEXT[kind]
    if kind == "replaced":
        return f"{text}（旧 {_quality_text(outcome.get('prev_quality'))} → 新 {_quality_text(outcome.get('quality'))}）"
    if kind in ("new", "kept"):
        quality = outcome.get("quality")
        return f"{text}（q {_quality_text(quality)}）" if isinstance(quality, (int, float)) else text
    if kind == "rejected":
        return f"{text}（この個体 {_quality_text(outcome.get('quality'))} ・既存 {_quality_text(outcome.get('incumbent_quality'))}）"
    return text


def _replay_list_item(individual, seed_run=None):
    parents = individual["parents"]
    seed_cell = individual.get("seed_cell")
    if not parents and seed_cell:
        cell_text = seed_cell.replace("|", " × ")
        lineage_text = f'前の実験 {seed_run} の「{cell_text}」の代表から引き継いだ性格' if seed_run else f'前の実験の「{cell_text}」の代表から引き継いだ性格'
    elif not parents:
        lineage_text = "親なしの新顔（ランダムな性格）"
    elif len(parents) == 1:
        lineage_text = f'親: {parents[0]["display"]}'
    elif parents[0]["label"] == parents[1]["label"]:
        # O6: the note replaces the "A × B" framing, but must still name
        # which parent it was -- not just that there were two of them.
        lineage_text = f'親 A・B とも {parents[0]["display"]}（{_SAME_PARENT_NOTE}）'
    else:
        lineage_text = f'親 A: {parents[0]["display"]} × 親 B: {parents[1]["display"]}'
    return f'<li>個体 #{individual["index"]}: {_escape(lineage_text)} → {_escape(_outcome_sentence(individual["outcome"]))}</li>'


def _replay_track(base_url, generation, max_generation):
    """The player row: one clickable segment per generation 0..max_generation
    (a plain link -- works with no JS, and ga_replay.js progressively
    enhances it into the animated player's scrubber/auto-advance track)."""
    escaped_base = _escape(base_url or "")
    segments = []
    for k in range(max_generation + 1):
        classes = ["ga-replay-gen"]
        if k < generation:
            classes.append("is-done")
        current = k == generation
        if current:
            classes.append("is-current")
        aria_current = ' aria-current="true"' if current else ""
        segments.append(
            f'<a class="{" ".join(classes)}" href="{escaped_base}?gen={k}" '
            f'title="第 {k + 1} 世代" aria-label="第 {k + 1} 世代を再生"{aria_current}>'
            '<span class="ga-replay-gen-fill"></span></a>'
        )
    return (
        '<div class="pbar ga-replay-player">'
        '<div class="ga-replay-player-label">リプレイ</div>'
        f'<div class="ga-replay-track" role="group" aria-label="リプレイする世代">{"".join(segments)}</div>'
        f'<span class="ga-replay-pos">第 {generation + 1} 世代 / {max_generation + 1}</span>'
        '</div>'
    )


def render_panel(model, base_url):
    """The replay panel's HTML: a data-only summary + <ol> that works with
    no JS, plus the `data-replay` payload viewer/static/ga_replay.js reads
    to draw and animate the same data. Empty string when there is nothing
    to replay."""
    if model is None:
        return ""
    generation = model["generation"]
    representatives = model["individuals"]
    counts = model["counts"]
    sub = (
        f"第 {generation + 1} 世代（g{generation}）・"
        f"全 {model['population']} 体のうち代表 {len(representatives)} 体"
    )
    # A None prev_cells means we could not tell "new" from "already there",
    # let alone "replaced" (replaced is only ever detected by comparing
    # against prev_cells) -- say what we actually know instead of claiming
    # "new"/"replaced" counts that are always exactly 0 in that case.
    prev_known = model.get("prev_cells") is not None
    segments = [f"発見 {counts['new']}" if prev_known else f"地図に残った {counts['kept']}"]
    if prev_known:
        segments.append(f"入れ替え {counts['replaced']}")
    segments.append(f"残らず {counts['rejected']}")
    if counts.get("unclassified"):
        segments.append(f"分類不能 {counts['unclassified']}")
    segments.append(f"未到達 {counts['unreached']}")
    summary = "・".join(segments)
    payload = json.dumps(model, ensure_ascii=False, separators=(",", ":"))
    parts = [
        '<div class="ga-replay-block" data-vessel="replay">',
        _replay_track(base_url, generation, model["max_generation"]),
        f'<div class="ga-replay-head"><h3>この世代で起きたこと</h3><span class="vhead-sub">{_escape(sub)}</span></div>',
        f'<p class="replay-summary">{_escape(summary)}</p>',
        f'<div class="ga-replay" data-replay="{_escape(payload)}"></div>',
        '<details class="ga-replay-text"><summary>文章で読む</summary><ol class="ga-replay-list">',
    ]
    seed_run = model.get("seed_run")
    parts.extend(_replay_list_item(individual, seed_run) for individual in representatives)
    parts.append('</ol></details>')
    parts.append('</div>')
    return "".join(parts)
