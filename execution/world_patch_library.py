"""Genre-library assets: exporting an applied world-expansion patch to its
template so other worlds on the same genre can import it, and importing one
back into a different world's proposal queue (WB-WORLDGROW-001 段階5d).

An asset lives at ``<template>/expansions/<patch_id>.yaml`` -- outside
``<template>/(rules|effects|canon|action_graph).yaml``, so LIBRARY_DIR is
excluded wherever a template dir is walked for behavior-affecting content
(see gapengine.world_patch.LIBRARY_DIR's docstring): adding or removing an
asset never changes an input digest, a frozen input manifest, or a GA content
fingerprint. Stdlib + PyYAML + gapengine.world_patch* only, no engine import,
matching the discipline of the modules it wraps (execution.world_patches,
execution.world_patch_approval).
"""
from __future__ import annotations

import datetime
import hashlib
import os
import uuid
from pathlib import Path

import yaml

from execution.provenance import contained, directory_lock
from execution.world_patches import _world_parent_rev, applicable_snapshot, patch_lock
from gapengine.world_patch import (
    ID_RE,
    LIBRARY_DIR,
    PatchError,
    materialize,
    patch_id_for,
    read_stack,
    retired_patches,
    template_identifiers,
    validate_patch,
    verify_stack,
)
from gapengine.world_patch_propose import _sourced_zones, check_proposal_rules, check_trigger_coverage
from gapengine.world_patch_usage import load_archive


class DuplicateAssetError(PatchError):
    """export_patch() was asked to write an asset id that already exists --
    a caller (the viewer's export API) can catch this specifically to answer
    409 instead of guessing from the message (same principle as
    execution.world_patch_approval.StalePatch)."""


def library_dir(template_dir) -> Path:
    return Path(template_dir) / LIBRARY_DIR


def _frozen_world_parent_ids(experiment: Path, project_name: str) -> set:
    """The patch ids `experiment`'s own *frozen* world was actually built
    from (execution.world_patches._world_parent_rev), read from whichever
    on-disk shape this experiment has -- a ConfigStore-prepared run's sealed
    inputs/projects/<id>/world.yaml (published-version-only experiments have
    this too; only their archive.json lives under published/<rev>/, inputs/
    is unaffected), or an --expanded-project scratch run's own copy. Raises
    PatchError when neither exists: an experiment with no frozen world at
    all (e.g. a legacy/CLI run resolved only through the live repository)
    can never prove which patches it actually ran under (WB-WORLDGROW-001
    段階5d M1, matching execution.epoch_chain.EpochChain._auto_retire's own
    frozen-world read)."""
    for candidate in (
        experiment / "inputs" / "projects" / project_name / "world.yaml",
        experiment / "expanded-project" / "world.yaml",
    ):
        if candidate.is_file():
            frozen_world = yaml.safe_load(candidate.read_text(encoding="utf-8"))
            return set(_world_parent_rev(frozen_world if isinstance(frozen_world, dict) else {}))
    raise PatchError("この実験の凍結された世界を確認できません（凍結されていない実験は資産の証拠にできません）")


def list_entries(template_dir) -> list[dict]:
    """[{"id", "doc" (or None), "error" (or None), "path"}] in id order --
    a broken/unreadable asset is reported as an error entry instead of
    raising, so one corrupt file never hides every other asset."""
    folder = library_dir(template_dir)
    if not folder.is_dir():
        return []
    entries = []
    for path in sorted(folder.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict) or not isinstance(doc.get("patch"), dict):
                raise ValueError("形式が不正です（patch がありません）")
        except (OSError, ValueError, yaml.YAMLError) as error:
            entries.append({"id": path.stem, "doc": None, "error": str(error), "path": path})
            continue
        entries.append({"id": path.stem, "doc": doc, "error": None, "path": path})
    return entries


def export_patch(project, template, patch_id, *, experiment, protagonist, usage) -> Path:
    """Publish an already-approved-and-still-applied patch as a genre asset.
    Refuses (writing nothing) unless:
    - the patch is currently active (verify_stack), checked first (R1) --
      an unapplied/unknown/retired id is a plain usage error, not a missing-
      evidence one;
    - `experiment`'s own *frozen* world was actually built with this patch
      applied (WB-WORLDGROW-001 M1: gapengine.world_patch_usage.patch_usage()
      only ever counts a patch's zone/item *names* appearing in an exemplar
      log -- without this check, a same-named patch approved on some other
      world, or any experiment at all, could be offered as "evidence" for a
      patch it never actually ran);
    - `usage` (the caller's own, server-side patch_usage() result for this
      patch) shows at least one strong use -- an asset nobody's
      representative individuals actually used is not evidence the content
      works.
    `usage` and `experiment`/`protagonist` are recorded into
    provenance.evidence verbatim, same principle as
    execution.world_patch_approval.retire()'s own `usage` argument: measured
    once by the caller, trusted here, never recomputed."""
    project, template, experiment = Path(project), Path(template), Path(experiment)
    if not ID_RE.fullmatch(patch_id):
        raise PatchError("パッチ ID の形式が不正です")
    with patch_lock(project):
        verified = verify_stack(project)
        stack = read_stack(project)
        active = {p["id"]: (p, raw) for p, raw in verified}
        if patch_id not in active:
            raise PatchError("対象のパッチは適用中ではありません（未承認・すでに淘汰済み・存在しないのいずれかです）")
        patch, raw = active[patch_id]
        # R2: the *last* recorded revision for this patch id -- a patch can
        # in principle be reopened and re-approved (a fresh check/approve
        # cycle appends a new revision under the same patch_id), and only
        # the latest one is still what verify_stack() just proved is active.
        revisions_for_patch = [r for r in stack["revisions"]
                               if r.get("kind", "patch") == "patch" and r["patch_id"] == patch_id]
        if not revisions_for_patch:
            raise PatchError("承認記録が見つかりません")
        revision = revisions_for_patch[-1]
        stack_head = stack["head"]
        world = yaml.safe_load((project / "world.yaml").read_text(encoding="utf-8"))

    frozen_ids = _frozen_world_parent_ids(experiment, project.name)
    if patch_id not in frozen_ids:
        raise PatchError("この実験の凍結された世界にはこのパッチが含まれていません")

    counts = usage or {}
    if counts.get("elites_strong", 0) < 1:
        raise PatchError("この実験の代表個体に使われていないため資産にできません")

    try:
        _archive, archive_raw, archive_source = load_archive(experiment)
        archive_sha256 = hashlib.sha256(archive_raw).hexdigest()
    except (OSError, ValueError, KeyError, TypeError):
        archive_sha256, archive_source = None, None

    entry = {
        "schema_version": 1,
        "patch": patch,
        "provenance": {
            "patch_sha256": hashlib.sha256(raw).hexdigest(),
            "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "world_id": project.name,
            "world_name": world.get("name"),
            "template_id": template.name,
            "stack_head": stack_head,
            "revision": revision,
            "evidence": {
                "experiment": experiment.name,
                "archive_sha256": archive_sha256,
                "archive_source": archive_source,
                "protagonist": protagonist,
                "usage": counts,
            },
        },
    }
    # Lock library_dir(template) itself, not `template` -- directory_lock()
    # leaves a persistent .write.lock file in whatever directory it locks,
    # and that file is not itself a .yaml/.yml (template_data()/configs.py's
    # freeze loop already tolerate it by suffix) but IS picked up by
    # gapengine.evolve._content_fingerprint, which hashes every file under
    # template_dir regardless of suffix. Locking `template` directly would
    # plant .write.lock at the template root the first time anything is ever
    # exported, moving the fingerprint for every world on this genre --
    # locking the LIBRARY_DIR subdirectory instead keeps that lock file
    # under the same excluded subtree as the assets themselves.
    folder = library_dir(template)
    with directory_lock(folder):
        destination = contained(folder, f"{patch_id}.yaml")
        if destination.exists():
            raise DuplicateAssetError("同じ ID の資産がすでにあります")
        temporary = destination.with_name("." + destination.name + "-" + uuid.uuid4().hex)
        try:
            temporary.write_text(yaml.safe_dump(entry, allow_unicode=True, sort_keys=False), encoding="utf-8")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def _resolve_trigger_zone(add: dict, original_trigger, world: dict):
    """The engine's check_trigger_coverage only needs trigger.zone to be one
    of add's own sourced zones (whichever one, since the added
    items/facts/zones travel with the patch regardless of world) -- but
    picking a zone the target world already has, when one of the sourced
    zones qualifies, keeps the display-facing "きっかけの場所" meaningful
    there instead of always falling back to a zone this same patch is about
    to add."""
    sourced = _sourced_zones(add)
    original_zone = (original_trigger or {}).get("zone") if isinstance(original_trigger, dict) else None
    if isinstance(original_zone, str) and original_zone in sourced:
        return original_zone
    existing_zones = {z.get("name") for z in (world.get("zones") or []) if isinstance(z, dict)}
    existing_match = next((zone for zone in sorted(sourced) if zone in existing_zones), None)
    if existing_match is not None:
        return existing_match
    return sorted(sourced)[0] if sourced else None


def rewrite_for_world(entry_patch: dict, *, parent_digest: str, library_ref: dict, world: dict) -> dict:
    """The asset's patch, re-pointed at a different world: parent_digest
    replaced with the target's own stack head, author replaced with a
    library-origin record (never the original proposal's LLM author --
    gate/trial evidence never travels with an asset, so claiming the
    original author here would misattribute an unreviewed import), trigger
    re-resolved against the target world's own zones. id/title/rationale/add
    are carried over verbatim -- the content itself does not change."""
    add = entry_patch.get("add") or {}
    zone = _resolve_trigger_zone(add, entry_patch.get("trigger"), world)
    return {
        "id": entry_patch.get("id"),
        "title": entry_patch.get("title"),
        "rationale": entry_patch.get("rationale"),
        "parent_digest": parent_digest,
        "trigger": {"zone": zone, "verb": "investigate"},
        "author": {
            "backend": "library",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "library": {"template_id": library_ref.get("template_id"), "patch_id": library_ref.get("patch_id")},
            "origin": {"world_id": library_ref.get("world_id"), "world_name": library_ref.get("world_name")},
        },
        "add": add,
    }


def fit(entry_doc: dict, world: dict, people: dict, *, reserved=()) -> list[str]:
    """Portability check (WB-WORLDGROW-001 段階5d §1): empty = importable
    into `world`/`people` as-is. Runs the same gates a live proposal would
    (validate_patch + check_proposal_rules + check_trigger_coverage) against
    a trigger re-resolved for this target -- never raises on a malformed
    entry, matching validate_patch's own contract."""
    patch = entry_doc.get("patch") if isinstance(entry_doc, dict) else None
    if not isinstance(patch, dict):
        return ["資産の形式が不正です"]
    add = patch.get("add") or {}
    zone = _resolve_trigger_zone(add, patch.get("trigger"), world)
    trigger = {"zone": zone, "verb": "investigate"}
    subject_ids = [p.get("id") for p in people.values() if isinstance(p, dict)]
    # R6 (Opus review): matches scripts/world_patch.py's own _give_available
    # (used by propose/check for exactly this purpose) -- False when no
    # subject has give_item, including when there are no subjects at all.
    # gapengine.world_patch.materialize()'s own convention ("empty subjects
    # means unknown, charge the budget") is deliberately not used here: fit()
    # answers "would a live check accept this", and check always resolves
    # give_available from this world's real subjects the same way.
    give_available = any(
        isinstance(subject, dict) and isinstance(subject.get("verbs"), list) and "give_item" in subject["verbs"]
        for subject in people.values())
    violations = validate_patch(world, {**patch, "trigger": trigger}, subject_ids=subject_ids,
                                reserved=reserved, give_available=give_available)
    violations += check_proposal_rules(add, give_available=give_available)
    violations += check_trigger_coverage(add, trigger)
    return violations


class StaleImport(PatchError):
    """import_patch() was asked to check the target's stack head against an
    expected value (expect_head) that no longer matches what's on disk -- a
    caller (the viewer's import API) can catch this specifically to answer
    409 instead of guessing from the message (same principle as
    execution.world_patch_approval.StalePatch)."""


def import_patch(project, template, entry_id: str, *, repo_root=None, expect_head=None) -> dict:
    """Stage a genre asset as a new proposal in `project` (no gate --
    trial_pending, same as a freshly-generated proposal's file with no
    .gate.json yet): the imported content is trusted static data (D1-D3
    excluded it from every digest already), but it has never been trial-run
    against *this* world, so it must go through the ordinary check/approve
    flow like anything else. `expect_head`, when given, is checked against
    the target's current stack head from *inside* patch_lock (R4, Opus
    review) -- the same freshness guarantee approve()/retire() give their own
    expect_*/expect_head arguments, instead of leaving the check to a
    pre-lock read a concurrent writer could race."""
    project, template = Path(project), Path(template)
    if not ID_RE.fullmatch(entry_id):
        raise PatchError("パッチ ID の形式が不正です")
    with patch_lock(project):
        verified, stack, world, people, refs, _root_digest = applicable_snapshot(
            project, template, repo_root=repo_root)
        # R4 (Opus review): staleness checked first, before this target's
        # own existence -- same precedence execution.world_patch_approval.
        # retire()/approve() give their own expect_*/expect_head checks
        # (right after reading the current stack, before anything specific
        # to the requested id), so a stale view of the world is never
        # mistaken for "that entry doesn't exist".
        if expect_head is not None and expect_head != stack["head"]:
            raise StaleImport("画面を開いたあとに内容が変わりました。再読み込みしてください")
        entries = {entry["id"]: entry for entry in list_entries(template)}
        entry = entries.get(entry_id)
        if entry is None:
            raise PatchError("資産が見つかりません")
        if entry["doc"] is None:
            raise PatchError(f"資産を読み込めません: {entry['error']}")
        entry_patch = entry["doc"].get("patch") if isinstance(entry["doc"], dict) else None
        if not isinstance(entry_patch, dict) or patch_id_for(entry_patch.get("add") or {}) != entry_id:
            # R5 (Opus review): the asset's own content must still hash to
            # the filename it's stored under -- a hand-edited or corrupted
            # asset file must never be imported under someone else's id.
            raise PatchError("資産の内容と id が一致しません")
        active_patches = [p for p, _raw in verified]
        if entry_id in {p["id"] for p in active_patches}:
            raise PatchError("すでにこの世界に適用されています")
        proposed_dir = project / "patches" / "_proposed"
        if (proposed_dir / f"{entry_id}.yaml").exists():
            raise PatchError("すでに提案中です")
        if entry_id in {r["patch"]["id"] for r in retired_patches(project)}:
            raise PatchError("この世界ですでに淘汰されています")
        reserved = template_identifiers(template)
        expanded_world, expanded_people = materialize(
            world, people, active_patches, reserved=reserved, check_budgets=False)
        violations = fit(entry["doc"], expanded_world, expanded_people, reserved=reserved)
        if violations:
            raise PatchError(violations[0])
        provenance = entry["doc"].get("provenance") or {}
        library_ref = {"template_id": template.name, "patch_id": entry_id,
                       "world_id": provenance.get("world_id"), "world_name": provenance.get("world_name")}
        rewritten = rewrite_for_world(entry_patch, parent_digest=stack["head"],
                                       library_ref=library_ref, world=expanded_world)
        if rewritten.get("id") != entry_id:
            raise PatchError("資産の id が一致しません")
        proposed_dir.mkdir(parents=True, exist_ok=True)
        destination = proposed_dir / f"{entry_id}.yaml"
        if destination.exists():
            raise PatchError("すでに提案中です")
        # R8 (Opus review): temp -> os.replace, matching export_patch()'s own
        # write and every other writer in this codebase (never a direct
        # write_text into the final path).
        temporary = destination.with_name("." + destination.name + "-" + uuid.uuid4().hex)
        try:
            temporary.write_text(yaml.safe_dump(rewritten, allow_unicode=True, sort_keys=False), encoding="utf-8")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return rewritten
