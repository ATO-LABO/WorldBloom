"""Per-patch usage across an experiment's representative individuals
(WB-WORLDGROW-001 段階5a "パッチの淘汰"). Stdlib + gapengine.world_patch_contract
only -- no engine/execution imports, same discipline as gapengine/world_patch.py
(this must be safe to call from the CLI, the job worker, and the viewer alike).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from gapengine.world_patch_contract import new_usage

# new_usage()'s six counters, split into "strong" (the protagonist actually
# moved into / gathered from / learned or shared / gave something the patch
# added) and "weak" (merely stood in the new zone and decided something,
# without any of the above -- ユーザー決定2026-09-22: 弱い使用は表示のみで、
# 枯れ候補の判定には数えない).
_STRONG_KEYS = ("moves_into_new_zones", "gathered_new_items", "learned_new_facts",
                "shared_new_facts", "gave_new_items")
_WEAK_KEY = "decisions_in_new_zones"


def load_archive(experiment_dir) -> tuple[dict, bytes, str]:
    """(archive_dict, raw_bytes, source) for an experiment's archive.

    Read-only, no manifest sha verification -- that belongs to the catalog's
    own trusted path (viewer.data.RunRepository.archive(), which every
    viewer caller should prefer and pass in via patch_usage(archive=...)).
    This is the best-effort fallback for a caller with no RunRepository/
    catalog to hand, namely the CLI and execution.world_patch_approval.
    retire()'s own archive_sha256 hashing.

    A ConfigStore-prepared experiment -- the only kind the screen can ever
    expand-run -- has no top-level archive.json, only
    published/<revision>/archive.json, referenced by published/current.
    json's "revision" (viewer/run_catalog.py's read_publication). This
    prefers the top-level file when present (a legacy/CLI-run experiment)
    and falls back to the published one. Raises OSError/ValueError if
    neither is readable."""
    experiment_dir = Path(experiment_dir)
    top_level = experiment_dir / "archive.json"
    if top_level.is_file():
        raw = top_level.read_bytes()
        return json.loads(raw), raw, "top-level"
    pointer = json.loads((experiment_dir / "published" / "current.json").read_bytes())
    if not isinstance(pointer, dict):
        raise ValueError("公開ポインタの形式が不正です")
    revision = pointer.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("公開版番号が不正です")
    raw = (experiment_dir / "published" / str(revision) / "archive.json").read_bytes()
    return json.loads(raw), raw, f"published/{revision}"


def _add_from_patch(patch: dict) -> dict:
    """A patch's own full `add` (zones/items/facts with all fields), or --
    for the name-only shape a frozen world's expansion.patches[]/a承認済み
    パッチの `added` carries -- the equivalent new_usage() needs (it only
    checks name/id membership). Same conversion as
    viewer/world_usage_badge.py's _added_names/_patch_for."""
    add = patch.get("add")
    if isinstance(add, dict):
        return add
    added = patch.get("added") or {}
    return {
        "zones": [{"name": n} for n in added.get("zones") or [] if isinstance(n, str)],
        "items": [{"name": n} for n in added.get("items") or [] if isinstance(n, str)],
        "facts": [{"id": n} for n in added.get("facts") or [] if isinstance(n, str)],
    }


def _name_sets(patch: dict) -> tuple[frozenset, frozenset, frozenset, frozenset]:
    """The zone/item/fact *names* a patch adds, as hashable sets -- new_usage()
    only ever checks name/id membership (never the full add definition), so
    this is everything _cached_counts needs to key its cache on.

    The 4th set (WB-WORLDGROW-002 stage 2 review 1 fix M1) is add.sources'
    (kind, target name, zone) triples -- new_usage() needs the zone too (an
    add.sources target is an *existing* item/fact, only "new" when actually
    gathered/learned at the zone this patch added a source in)."""
    add = _add_from_patch(patch)
    zones = frozenset(z.get("name") for z in add.get("zones") or [] if isinstance(z, dict))
    items = frozenset(i.get("name") for i in add.get("items") or [] if isinstance(i, dict))
    facts = frozenset(f.get("id") for f in add.get("facts") or [] if isinstance(f, dict))
    sources = frozenset(
        (("item", s.get("item")) if "item" in s else ("fact", s.get("fact")), (s.get("source") or {}).get("zone"))
        for s in add.get("sources") or []
        if isinstance(s, dict) and isinstance(s.get("source"), dict)
        and isinstance(s["source"].get("zone"), str)
    )
    return zones, items, facts, sources


# 段階5a Opus review R1: a result screen re-renders this table on every view
# of the demand tab, re-parsing every exemplar log for every active patch
# (up to ~8 patches x ~18 cells, each log up to ~0.6MB) with no cache at all.
# Keyed the same way viewer/world_usage_badge.py's _cached_counts is
# (path, mtime_ns, size, name sets, protagonist): a log's content only ever
# changes by being replaced (a new mtime/size), never edited in place.
@lru_cache(maxsize=512)
def _cached_counts(path, mtime_ns, size, zones, items, facts, sources, protagonist):
    add = {"add": {"zones": [{"name": n} for n in zones],
                   "items": [{"name": n} for n in items],
                   "facts": [{"id": n} for n in facts],
                   "sources": [{kind_name[0]: kind_name[1], "source": {"zone": zone}}
                               for kind_name, zone in sources]}}
    return new_usage([path], add, protagonist)


def patch_usage(experiment_dir, protagonist: str, patches: list[dict], *,
                 archive: dict | None = None) -> dict[str, dict]:
    """{patch_id: {"elites_total", "elites_strong", "elites_weak", "cells": {cell_key: counts}}}
    for each of `patches` (approved patches, or their name-only `added`
    equivalent), counted over this experiment's archive exemplars (one
    representative individual per occupied cell).

    `archive` should be the caller's own already-resolved/verified archive
    dict when one is available (viewer callers: handler.repository.archive
    (experiment)); left as None it falls back to this module's own
    load_archive(), for callers with no RunRepository/catalog to hand.

    A cell whose exemplar log can't be read is skipped entirely -- it never
    counts toward elites_total, so a patch with no readable evidence at all
    reports elites_total == 0 (judgment withheld, not "zero use")."""
    experiment_dir = Path(experiment_dir)
    if archive is None:
        archive, _raw, _source = load_archive(experiment_dir)
    cells = archive.get("cells") if isinstance(archive, dict) else None
    log_paths: dict[str, Path] = {}
    for cell_key, cell in (cells or {}).items():
        if not isinstance(cell, dict):
            continue
        relative = (cell.get("exemplar") or {}).get("layers_path")
        if not isinstance(relative, str) or not relative:
            continue
        path = experiment_dir / relative
        if path.is_file():
            log_paths[cell_key] = path

    result: dict[str, dict] = {}
    for patch in patches:
        patch_id = patch.get("id")
        if not isinstance(patch_id, str):
            continue
        zones, items, facts, sources = _name_sets(patch)
        cell_counts: dict[str, dict] = {}
        strong = weak = total = 0
        for cell_key, path in log_paths.items():
            stat = path.stat()
            # dict(...): the cached return value is shared across every call
            # that hits this same key -- copy before handing it to the caller
            # so nothing here (or a caller) can mutate the cached object.
            counts = dict(_cached_counts(path, stat.st_mtime_ns, stat.st_size, zones, items, facts, sources,
                                        protagonist))
            cell_counts[cell_key] = counts
            total += 1
            if any(counts.get(key) for key in _STRONG_KEYS):
                strong += 1
            elif counts.get(_WEAK_KEY):
                weak += 1
        result[patch_id] = {"elites_total": total, "elites_strong": strong,
                             "elites_weak": weak, "cells": cell_counts}
    return result


def wither_candidates(usage: dict[str, dict]) -> list[str]:
    """Patch ids with at least one readable exemplar and zero strong use
    among them (WB-WORLDGROW-001 段階5a, 猶予 N=1: 1回の実験で判定する。全代表
    個体のログが読めない場合は判定不能として除外する)."""
    return [pid for pid, counts in usage.items()
            if counts.get("elites_total", 0) > 0 and counts.get("elites_strong", 0) == 0]
