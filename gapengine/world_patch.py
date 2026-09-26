"""Data-only expansion patches for a world (WB-WORLDGROW-001, stage 3a).

A patch adds zones/items/facts/daily_events to a world dict -- never touches
engine.yaml behavior, action_graph, effects, or existing entries. Approved
patches live at ``<project>/patches/<id>.yaml``; proposals awaiting review at
``<project>/patches/_proposed/<id>.yaml`` (this module never reads the latter
via `approved_patches`). Stdlib + PyYAML only: this module must not import
engine or execution, so it can be validated well before a run touches either.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")
_NAME_FORBIDDEN_CHARS = frozenset("'\"\n{}")
# engine/world.py only knows $innocent:1 and $innocent:2 (its truth_tokens
# set), whatever the number of candidates -- a larger N would pass here and
# then raise when the engine builds the world.
MAX_INNOCENT_INDEX = 2


def innocent_tokens(candidates) -> list[str]:
    """The exact $innocent:N strings usable against a lottery fact with these
    candidates (one of them is always the truth, so N stops at len - 1)."""
    return [f"$innocent:{n}" for n in range(1, min(len(candidates) - 1, MAX_INNOCENT_INDEX) + 1)]

# ponytail: budgets are placeholders; tune once real proposals have been reviewed.
PATCH_RULES_VERSION = 4
MAX_ZONES = 1
MAX_ITEMS = 2
MAX_FACTS = 2
MAX_DAILY_EVENTS = 3
MAX_TOTAL_PATCHES = 8
MAX_MODIFIER_PER_PATCH = 10
MAX_MODIFIER_TOTAL = 20
MAX_GIVE_PER_PATCH = 0.6
MAX_GIVE_TOTAL = 1.2
MAX_IMPLIES_CONFIDENCE = 0.3
MAX_IMPLIES_TOTAL = 0.6
# engine/verbs.py::_give_item defaults, including partially specified give.
# This bounds definitions x how many of each a patch lets someone pick up
# (sources.max), not affinity accumulated by giving the same item back and
# forth repeatedly.
DEFAULT_RECEIVER_AFFINITY = 0.2
DEFAULT_GIVER_AFFINITY = 0.05
EMPTY_STACK_DIGEST = hashlib.sha256(b"worldbloom-patch-stack-v1").hexdigest()

TOP_LEVEL_KEYS = frozenset({"id", "title", "rationale", "parent_digest", "trigger", "author", "add"})
ADD_KEYS = frozenset({"zones", "items", "facts", "sources"})
# WB-WORLDGROW-002 stage 2 review 1 fix M1 (design K): add.sources lets a
# patch give an *existing* item/fact a new investigate source, without
# touching any of its other fields -- the only way a "blocked" trigger's
# has_item:X/knows:F requirement (always an already-existing item/fact, per
# gapengine.route._blocked_on) can ever be satisfied at all, since
# validate_patch's check_name rejects re-adding an existing name via
# add.items/add.facts.
ADD_SOURCE_KEYS = frozenset({"item", "fact", "source"})
MAX_ADD_SOURCES = 2
ZONE_KEYS = frozenset({"name", "parent", "note"})
ITEM_KEYS = frozenset({"name", "sources", "lootable", "keepsake", "give", "modifier", "made_from", "craft_zone", "requires"})
ITEM_SOURCE_KEYS = frozenset({"type", "zone", "count", "max"})
GIVE_KEYS = frozenset({"receiver_affinity", "giver_affinity"})
MODIFIER_KEYS = frozenset({"id", "value", "kind", "visible"})
REQUIRES_KEYS = frozenset({"knowledge"})
FACT_KEYS = frozenset({"id", "label", "secrecy", "share_min_affinity", "sources", "implies"})
FACT_SOURCE_KEYS = frozenset({"type", "zone", "count"})
FACT_RELATION_KEYS = frozenset({"fact", "value", "confidence"})
DAILY_EVENT_KEYS = frozenset({"id", "label", "weight", "stress_delta"})

# WB-WORLDGROW-001 R10: literals the engine (engine/verbs.py, engine/actions.py,
# engine/world.py, engine/phase2.py) treats specially by exact string match. A
# zone/item/fact/daily_event named exactly one of these would silently change
# engine behavior instead of adding inert content -- validate_patch rejects
# them regardless of whether they happen to collide with existing world text.
RESERVED_NAMES: frozenset[str] = frozenset({
    # engine/verbs.py::_share_knowledge and engine/actions.py's share_knowledge
    # candidate builder both hardcode "雑談" as the "no real topic shared" small
    # talk sentinel; a fact id equal to it would be silently un-learnable.
    "雑談",
    # engine/verbs.py::HANDLED_VERBS -- the literal verb names the action
    # dispatcher recognizes. No confirmed path today from a patch-created
    # name into verb dispatch (patches can't write action_graph.yaml/
    # rules.yaml), but reserved so a future rule/effect that keys off a bare
    # name can't be quietly shadowed by a same-named zone/item/fact.
    "move", "rest", "investigate", "observe", "neutralize", "sabotage",
    "sacrifice", "mislead", "rethink", "confront", "share_knowledge",
    "give_item", "persuade", "pledge", "negotiate", "concede", "craft",
    "fight", "train", "rescue", "withdraw", "guard", "plant", "payoff",
    "disguise", "grand_gesture", "trial", "donate",
    # engine/phase2.py::_effect_reference's literal effect-target tokens, and
    # engine/world.py's truth-relative value tokens ($truth/$innocent:1/2 in
    # _validate_fact_sources's fact_truth_tokens). Same precautionary
    # reasoning: not reachable from a patch today (effects.yaml/canon.yaml
    # are template-authored, not patch-authored), reserved regardless.
    "target", "$target", "self", "$self", "planter", "$planter",
    "$truth", "$innocent:1", "$innocent:2",
})

_TEMPLATE_IDENTIFIER_KEYS = frozenset({"id", "verb", "fact", "item", "zone", "subtype", "category"})
_TEMPLATE_FILES = ("rules.yaml", "effects.yaml", "canon.yaml", "action_graph.yaml")


def _collect_identifiers(node: Any, out: set) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                out.add(key)
                if key in _TEMPLATE_IDENTIFIER_KEYS and isinstance(value, str):
                    out.add(value)
            _collect_identifiers(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_identifiers(item, out)


def template_identifiers(template_dir) -> set:
    """Every dict key, plus every string value under an id/verb/fact/item/
    zone/subtype/category key, found across a template's rules.yaml/
    effects.yaml/canon.yaml/action_graph.yaml (WB-WORLDGROW-001 R10) -- extra
    names a world-expansion patch for that template must not reuse, on top of
    RESERVED_NAMES. Returns an empty set when template_dir doesn't exist."""
    identifiers: set = set()
    for name in _TEMPLATE_FILES:
        path = Path(template_dir) / name
        if not path.is_file():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        _collect_identifiers(data, identifiers)
    return identifiers


class PatchError(ValueError):
    """A patch could not be loaded or applied."""


def load_patch(path) -> dict:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PatchError(f"{path}: パッチはオブジェクトである必要があります")
    return raw


def approved_patches(project_dir) -> list[dict]:
    """Data-only verification; IO callers hold their project lock once."""
    return [patch for patch, raw in verify_stack(project_dir)]


def read_stack(project_dir) -> dict:
    path = Path(project_dir) / "patches" / "stack.json"
    if not path.exists():
        return {"schema_version": 1, "revisions": [], "head": EMPTY_STACK_DIGEST}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(result, dict) or result.get("schema_version") != 1
                or not isinstance(result.get("revisions"), list)):
            raise ValueError("manifest schema")
        return result
    except (OSError, ValueError) as error:
        raise PatchError(f"スタックmanifestが破損しています: {error}") from error


def stack_head(project_dir) -> str:
    verify_stack(project_dir)
    return read_stack(project_dir)["head"]


def next_digest(parent: str, patch_sha256: str) -> str:
    return hashlib.sha256((parent + patch_sha256).encode()).hexdigest()


def _walk_stack(project_dir) -> list[dict]:
    """Validate a manifest snapshot revision by revision, returning an
    ordered list of records -- {"kind": "patch", "patch_id", "patch", "raw", "rev"}
    for an approval, or {"kind": "retire", "patch_id", "retire", "rev"} for a
    tombstone (WB-WORLDGROW-001 段階5a). verify_stack()/retired_patches() are
    both thin views over this single walk, so the hash-chain/evidence
    checking logic lives in exactly one place.

    Callers taking part in publication hold the external directory lock.
    This module intentionally never imports execution or engine.
    """
    folder = Path(project_dir) / "patches"
    stack = read_stack(project_dir)
    head, seen_patch_ids, active, records = EMPTY_STACK_DIGEST, set(), {}, []
    try:
        for seq, entry in enumerate(stack["revisions"], 1):
            kind = entry.get("kind", "patch")
            if kind == "patch":
                pid = entry["patch_id"]
                if not isinstance(pid, str) or not ID_RE.fullmatch(pid) or pid in seen_patch_ids:
                    raise ValueError("patch_id の不正または重複")
                raw = (folder / f"{pid}.yaml").read_bytes()
                sha = hashlib.sha256(raw).hexdigest()
                patch = yaml.safe_load(raw)
                gate_raw = (folder / f"{pid}.gate.json").read_bytes()
                gate = json.loads(gate_raw)
                if (patch["id"] != pid or patch_id_for(patch["add"]) != pid
                        or entry["patch_sha256"] != sha
                        or entry["gate_sha256"] != hashlib.sha256(gate_raw).hexdigest()
                        or entry["rev"] != seq or type(entry["rev"]) is not int
                        or entry["parent_digest"] != head or patch["parent_digest"] != head
                        or entry["digest"] != next_digest(head, sha)
                        or gate["patch_id"] != pid or gate["patch_sha256"] != sha
                        or gate["status"] != "reviewable"
                        or gate["trial"]["evidence"]["seed_set"] != "holdout"
                        or len(gate["approval"]["reason"].strip()) < 10
                        or gate["approval"] != entry["approval"]
                        or gate["trial"]["evidence"]["patched_inputs_digest"] != entry["patched_inputs_digest"]
                        or gate["trial"]["evidence"]["patch_rules_version"] != entry["rules_version"]
                        or gate["trial"]["evidence"]["trial_rules_version"] != entry["trial_rules_version"]):
                    raise ValueError(f"revision {seq} の証拠が一致しません")
                head = entry["digest"]
                seen_patch_ids.add(pid)
                active[pid] = (patch, raw)
                records.append({"kind": "patch", "patch_id": pid, "patch": patch, "raw": raw, "rev": entry["rev"]})
            elif kind == "retire":
                pid = entry["patch_id"]
                if not isinstance(pid, str) or pid not in active:
                    raise ValueError("淘汰対象が適用中のパッチではありません")
                retire_raw = (folder / f"{pid}.retire.json").read_bytes()
                retire_sha = hashlib.sha256(retire_raw).hexdigest()
                retire = json.loads(retire_raw)
                if (entry["rev"] != seq or type(entry["rev"]) is not int
                        or entry["retire_sha256"] != retire_sha
                        or entry["parent_digest"] != head
                        or entry["digest"] != next_digest(head, retire_sha)
                        or retire["patch_id"] != pid or retire["parent_digest"] != head
                        or len(str(retire["reason"]).strip()) < 10):
                    raise ValueError(f"revision {seq} の淘汰記録が一致しません")
                head = entry["digest"]
                del active[pid]
                records.append({"kind": "retire", "patch_id": pid, "retire": retire, "rev": entry["rev"]})
            else:
                raise ValueError(f"revision {seq} の種別が不正です: {kind!r}")
        if stack["head"] != head:
            raise ValueError("head が一致しません")
        if records and (not stack.get("base_inputs_digest") or not stack.get("template_id")):
            raise ValueError("基準入力が記録されていません")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as error:
        raise PatchError(f"承認スタックが破損しています（repair不可）: {error}") from error
    extra = {p.stem for p in folder.glob("*.yaml")} - seen_patch_ids
    if extra:
        pid = sorted(extra)[0]
        state = "yaml と gate が移動済みで manifest 未更新" if (folder / f"{pid}.gate.json").exists() else "yaml だけ移動済み"
        raise PatchError(f"manifest に無いパッチ {pid}: {state}。repair を実行してください")
    return records


def verify_stack(project_dir) -> list[tuple[dict, bytes]]:
    """Validate a manifest snapshot, returning the (patch, raw bytes) of every
    currently-applied (approved and not since retired) patch, in approval
    order. Callers hold the external directory lock as before; a `kind`-less
    entry (pre-段階5a stack) is treated as an ordinary approval."""
    records = _walk_stack(project_dir)
    retired_ids = {r["patch_id"] for r in records if r["kind"] == "retire"}
    return [(r["patch"], r["raw"]) for r in records if r["kind"] == "patch" and r["patch_id"] not in retired_ids]


def retired_patches(project_dir) -> list[dict]:
    """{"patch": ..., "retire": <retire.json 内容>, "rev": ...} を、退場（淘汰）
    した順に返す（WB-WORLDGROW-001 段階5a）。verify_stack と同じ _walk_stack
    を経由するため、検証ロジックの二重化はない。"""
    records = _walk_stack(project_dir)
    patches_by_id = {r["patch_id"]: r["patch"] for r in records if r["kind"] == "patch"}
    return [{"patch": patches_by_id[r["patch_id"]], "retire": r["retire"], "rev": r["rev"]}
            for r in records if r["kind"] == "retire"]


def _is_name(value: Any) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 30
            and not any(c in _NAME_FORBIDDEN_CHARS for c in value))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _existing_names(world: dict) -> dict[str, set[str]]:
    zones = {str(z.get("name")) for z in (world.get("zones") or []) if isinstance(z, dict)}
    items = {str(i.get("name")) for i in (world.get("items") or []) if isinstance(i, dict)}
    facts = {str(f.get("id")) for f in (world.get("facts") or []) if isinstance(f, dict)}
    daily = world.get("daily_events")
    events = set()
    if isinstance(daily, dict):
        events = {str(e.get("id")) for e in (daily.get("events") or []) if isinstance(e, dict)}
    return {"zones": zones, "items": items, "facts": facts, "daily_events": events}


def _applied_names(world: dict) -> dict[str, set]:
    """Names added by name (add.items/add.facts/...) -- validate_patch's
    give-budget check (prior_given) relies on this meaning exactly "an item
    this world didn't have before any patch", since it sums that item's
    *entire* current sources list (item_give_count): folding a sourced-only
    existing item in here would double-count its pre-existing (base) source
    max as if a patch had added it. addition_caps() below adds the sourced
    names back in for the cumulative items/facts *count*, which has no such
    double-counting risk (段階3 review 1 必須1/2)."""
    applied = {key: set() for key in ADD_KEYS}
    for entry in (world.get("expansion") or {}).get("patches", []):
        for key in ADD_KEYS:
            applied[key].update((entry.get("added") or {}).get(key, []))
    return applied


def _sourced_item_and_fact_names(world: dict, source_labels) -> tuple[set, set]:
    """Required 1 (段階3 review 1): a "品@場所" add.sources label doesn't say
    whether the target is an item or a fact. Resolve it by looking the name
    up in the *current* (already-patched) world's items/facts -- if a name
    matches both, count it in both (設計役の決定: 保守的に両方数える)."""
    items, facts = set(), set()
    if not source_labels:
        return items, facts
    item_names = {i.get("name") for i in (world.get("items") or []) if isinstance(i, dict)}
    fact_ids = {f.get("id") for f in (world.get("facts") or []) if isinstance(f, dict)}
    for label in source_labels:
        if not (isinstance(label, str) and "@" in label):
            continue
        name = label.rsplit("@", 1)[0]
        if name in item_names:
            items.add(name)
        if name in fact_ids:
            facts.add(name)
    return items, facts


def addition_caps(world: dict) -> dict[str, tuple[int, int]]:
    """{key: (cumulative cap, already added)} -- shared by the static gate and
    the proposal prompt, so the model is told the same numbers it is held to.

    Required 1 (段階3 review 1): a prior patch's add.sources now counts
    toward the items/facts cap too -- it already used the items/facts
    budget when it was proposed (validate_patch's own per-patch check counts
    it that way via sources_item_count/sources_fact_count)."""
    existing, applied = _existing_names(world), _applied_names(world)
    sourced_items, sourced_facts = _sourced_item_and_fact_names(world, applied["sources"])
    cap_applied = {"zones": applied["zones"], "items": applied["items"] | sourced_items,
                   "facts": applied["facts"] | sourced_facts}
    caps = {}
    for key in ("zones", "items", "facts"):
        base_count = len(existing[key] - cap_applied[key])
        cap = min(4 if key == "zones" else 8,
                  max(1, math.ceil(base_count * (0.4 if key == "zones" else 0.5))))
        caps[key] = (cap, len(cap_applied[key]))
    return caps


def _valued_facts(world: dict) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for fact in (world.get("facts") or []):
        if isinstance(fact, dict) and isinstance(fact.get("values"), list):
            result[str(fact.get("id"))] = {str(v) for v in fact["values"]}
    return result


def lottery_facts(world: dict) -> dict[str, set[str]]:
    """Valued facts whose true value is drawn per-seed instead of fixed --
    world["truth"][fact_id] is a mapping with a non-empty "candidates"
    mapping (engine/world.py's World.__init__/resolve_truth). Returns
    {fact_id: candidate names}. Shared by validate_patch and
    world_patch_propose's prompt so both agree on which facts need
    $truth/$innocent:N instead of a fixed value name. Never raises on a
    malformed world -- an unrecognized shape just isn't counted as lottery."""
    result: dict[str, set[str]] = {}
    truth = world.get("truth")
    if not isinstance(truth, dict):
        return result
    for fact_id, raw in truth.items():
        if not isinstance(raw, dict):
            continue
        candidates = raw.get("candidates")
        if isinstance(candidates, dict) and candidates:
            result[str(fact_id)] = {str(c) for c in candidates}
    return result


def _all_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _all_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _all_strings(v)


def validate_patch(world: dict, patch: dict, *, subject_ids: Iterable[str] = (),
                    reserved: Iterable[str] = (), check_budgets: bool = True,
                    give_available: bool = True) -> list[str]:
    """Static gate. Returns a list of Japanese violation strings (empty = pass).

    Never raises -- a malformed patch just accumulates violations instead of
    crashing the caller. `reserved` (e.g. from template_identifiers()) is
    merged with RESERVED_NAMES; any new zone/item/fact/daily_event name that
    exactly matches one is a violation (WB-WORLDGROW-001 R10).
    """
    violations: list[str] = []
    if not isinstance(patch, dict):
        return ["パッチはオブジェクトである必要があります"]
    reserved_names = RESERVED_NAMES | {str(r) for r in reserved}

    unknown_top = set(patch) - TOP_LEVEL_KEYS
    if unknown_top:
        violations.append("未対応のトップレベル項目があります: " + "、".join(sorted(str(k) for k in unknown_top)))

    patch_id = patch.get("id")
    if not (isinstance(patch_id, str) and ID_RE.fullmatch(patch_id)):
        violations.append(f"id の形式が不正です: {patch_id!r}")

    title = patch.get("title")
    if not (isinstance(title, str) and 1 <= len(title) <= 40):
        violations.append("title は1〜40文字の文字列で指定してください")

    add = patch.get("add")
    if not isinstance(add, dict):
        violations.append("add はオブジェクトで指定してください")
        add = {}
    else:
        unknown_add = set(add) - ADD_KEYS
        if unknown_add:
            violations.append("add に未対応の項目があります: " + "、".join(sorted(str(k) for k in unknown_add)))

    raw_zones, raw_items, raw_facts, raw_events, raw_sources = [], [], [], [], []
    for key, bucket_name, target in (
        ("zones", "add.zones", "raw_zones"), ("items", "add.items", "raw_items"),
        ("facts", "add.facts", "raw_facts"), ("daily_events", "add.daily_events", "raw_events"),
        ("sources", "add.sources", "raw_sources"),
    ):
        value = add.get(key, [])
        if not isinstance(value, list):
            violations.append(f"{bucket_name} はリストで指定してください")
            value = []
        if target == "raw_zones":
            raw_zones = value
        elif target == "raw_items":
            raw_items = value
        elif target == "raw_facts":
            raw_facts = value
        elif target == "raw_events":
            raw_events = value
        else:
            raw_sources = value

    if not (raw_zones or raw_items or raw_facts or raw_events or raw_sources):
        violations.append("何も足していません")

    # add.sources entries each count toward the items/facts cap of the kind
    # of thing they target (an "item" source is one more use of the items
    # budget, a "fact" source one more use of the facts budget) -- design K:
    # 品への source は「アイテム」、事実への source は「事実」の枠に1件として数える。
    sources_item_count = sum(1 for s in raw_sources if isinstance(s, dict) and "item" in s)
    sources_fact_count = sum(1 for s in raw_sources if isinstance(s, dict) and "fact" in s)

    if check_budgets and len(raw_zones) > MAX_ZONES:
        violations.append(f"ゾーンの追加数が上限（{MAX_ZONES}）を超えています")
    if check_budgets and len(raw_items) + sources_item_count > MAX_ITEMS:
        violations.append(f"アイテムの追加数が上限（{MAX_ITEMS}）を超えています")
    if check_budgets and len(raw_facts) + sources_fact_count > MAX_FACTS:
        violations.append(f"事実の追加数が上限（{MAX_FACTS}）を超えています")
    if len(raw_events) > MAX_DAILY_EVENTS:
        violations.append(f"日々の出来事の追加数が上限（{MAX_DAILY_EVENTS}）を超えています")
    if check_budgets and len(raw_sources) > MAX_ADD_SOURCES:
        violations.append(f"既存への入手手段の追加数が上限（{MAX_ADD_SOURCES}）を超えています")

    existing_expansion = world.get("expansion")
    existing_patch_count = (len(existing_expansion.get("patches", []))
                             if isinstance(existing_expansion, dict) else 0)
    if check_budgets and existing_patch_count + 1 > MAX_TOTAL_PATCHES:
        violations.append(f"適用後のパッチ総数が上限（{MAX_TOTAL_PATCHES}）を超えます")

    items_by_name = {i.get("name"): i for i in (world.get("items") or []) if isinstance(i, dict)}
    applied_modifier_total = 0.0
    if isinstance(existing_expansion, dict):
        for entry in existing_expansion.get("patches", []) or []:
            for name in ((entry.get("added") or {}).get("items") or []):
                modifier = (items_by_name.get(name) or {}).get("modifier")
                if isinstance(modifier, dict) and _is_number(modifier.get("value")):
                    applied_modifier_total += modifier["value"]
    patch_modifier_total = sum(
        item["modifier"]["value"] for item in raw_items
        if isinstance(item, dict) and isinstance(item.get("modifier"), dict)
        and _is_number(item["modifier"].get("value"))
    )
    if check_budgets and patch_modifier_total > MAX_MODIFIER_PER_PATCH:
        violations.append(f"強化値の合計が上限（{MAX_MODIFIER_PER_PATCH}）を超えています")
    if check_budgets and applied_modifier_total + patch_modifier_total > MAX_MODIFIER_TOTAL:
        violations.append(f"強化値の合計が上限（{MAX_MODIFIER_TOTAL}）を超えています")

    existing = _existing_names(world)
    subject_id_set = {str(s) for s in subject_ids}
    valued_facts = _valued_facts(world)
    lottery = lottery_facts(world)
    daily = world.get("daily_events")
    has_daily_slot = isinstance(daily, dict) and isinstance(daily.get("events"), list)

    provisional_zone_names = {z.get("name") for z in raw_zones if isinstance(z, dict) and isinstance(z.get("name"), str)}
    provisional_item_names = {i.get("name") for i in raw_items if isinstance(i, dict) and isinstance(i.get("name"), str)}
    provisional_fact_ids = {f.get("id") for f in raw_facts if isinstance(f, dict) and isinstance(f.get("id"), str)}
    valid_zone_names = existing["zones"] | provisional_zone_names
    valid_fact_ids = existing["facts"] | provisional_fact_ids

    seen_new_names: set[str] = set()

    def check_name(name: Any, kind: str) -> bool:
        if not _is_name(name):
            violations.append(f"{kind}名の形式が不正です: {name!r}")
            return False
        if name in reserved_names:
            violations.append(f"予約された名前は使えません: {name}")
            return False
        if (name in existing["zones"] or name in existing["items"]
                or name in existing["facts"] or name in existing["daily_events"]):
            violations.append(f"既存の名前と重複しています: {name}")
            return False
        if name in subject_id_set:
            violations.append(f"人物IDと重複しています: {name}")
            return False
        if name in seen_new_names:
            violations.append(f"パッチ内で名前が重複しています: {name}")
            return False
        # Deliberately broad: any text of the world, not just predicates, so a
        # new name can never be mistaken for part of an ending or gate condition.
        if any(name in s for s in _all_strings(world)):
            violations.append(f"世界の既存の文言に含まれる名前は使えません（結末や関門の条件への混入を防ぐため）: {name}")
            return False
        seen_new_names.add(name)
        return True

    # R2: a branch can only be one hop off a *base* zone -- contract_check's
    # negative/positive admission tests only ever look one hop from parent to
    # branch, so a branch-of-a-branch's own entry condition would never
    # actually get exercised.
    expansion_branch_names = {
        name for entry in (existing_expansion or {}).get("patches", []) if isinstance(entry, dict)
        for name in ((entry.get("added") or {}).get("zones") or [])
    }

    for zone in raw_zones:
        if not isinstance(zone, dict):
            violations.append("zones の要素はオブジェクトで指定してください")
            continue
        unknown = set(zone) - ZONE_KEYS
        if unknown:
            violations.append("zones に未対応の項目があります: " + "、".join(sorted(str(k) for k in unknown)))
        check_name(zone.get("name"), "ゾーン")
        parent = zone.get("parent")
        if not (isinstance(parent, str) and parent in existing["zones"]):
            violations.append(f"parent が既存ゾーンではありません: {parent!r}")
        elif parent in expansion_branch_names:
            violations.append(f"拡張で足した場所を親にはできません（枝は1段まで）: {parent}")
        note = zone.get("note")
        if note is not None and not isinstance(note, str):
            violations.append("note は文字列で指定してください")

    def check_item_source(source: Any, item_name: Any) -> None:
        if not isinstance(source, dict) or set(source) != ITEM_SOURCE_KEYS:
            violations.append(f"sources のキーが不正です: {item_name!r}")
            return
        if source.get("type") != "investigate":
            violations.append(f"sources.type は investigate のみです: {item_name!r}")
        zone = source.get("zone")
        if not isinstance(zone, str) or zone not in valid_zone_names:
            violations.append(f"sources.zone が存在しません: {zone!r}")
        if type(source.get("count")) is not int or source.get("count") != 1:
            violations.append(f"sources.count は1のみです: {item_name!r}")
        mx = source.get("max")
        if type(mx) is not int or not (1 <= mx <= 3):
            violations.append(f"sources.max は1〜3の整数で指定してください: {item_name!r}")

    for item in raw_items:
        if not isinstance(item, dict):
            violations.append("items の要素はオブジェクトで指定してください")
            continue
        unknown = set(item) - ITEM_KEYS
        if unknown:
            violations.append("items に未対応の項目があります: " + "、".join(sorted(str(k) for k in unknown)))
        name = item.get("name")
        check_name(name, "アイテム")

        sources = item.get("sources")
        made_from = item.get("made_from")
        has_sources = isinstance(sources, list) and len(sources) > 0
        has_made_from = isinstance(made_from, dict) and len(made_from) > 0
        if not has_sources and not has_made_from:
            violations.append(f"入手手段がありません: {name!r}")
        if sources is not None:
            if not isinstance(sources, list):
                violations.append(f"sources はリストで指定してください: {name!r}")
            else:
                for source in sources:
                    check_item_source(source, name)

        give = item.get("give")
        if give is not None:
            if not isinstance(give, dict) or set(give) - GIVE_KEYS:
                violations.append(f"give のキーが不正です: {name!r}")
            else:
                for key in ("receiver_affinity", "giver_affinity"):
                    if key in give and not (_is_number(give[key]) and 0 <= give[key] <= 0.5):
                        violations.append(f"give.{key} は0〜0.5で指定してください: {name!r}")

        modifier = item.get("modifier")
        if modifier is not None:
            if not isinstance(modifier, dict) or set(modifier) != MODIFIER_KEYS:
                violations.append(f"modifier のキーが不正です: {name!r}")
            else:
                if modifier.get("id") != f"item:{name}":
                    violations.append(f"modifier.id が name と一致しません: {name!r}")
                if modifier.get("kind") != "item":
                    violations.append(f"modifier.kind は item のみです: {name!r}")
                if not (_is_number(modifier.get("value")) and 0 <= modifier.get("value") <= 10):
                    violations.append(f"modifier.value は0〜10で指定してください: {name!r}")
                if not isinstance(modifier.get("visible"), bool):
                    violations.append(f"modifier.visible は真偽値で指定してください: {name!r}")

        if made_from is not None:
            if not isinstance(made_from, dict) or not made_from:
                violations.append(f"made_from はオブジェクトで指定してください: {name!r}")
            else:
                for material, count in made_from.items():
                    if not isinstance(material, str) or material not in provisional_item_names:
                        violations.append(f"made_from の素材は同じパッチで追加する新アイテムのみです: {material!r}")
                    if type(count) is not int or not (1 <= count <= 3):
                        violations.append(f"made_from の個数は1〜3で指定してください: {material!r}")

        craft_zone = item.get("craft_zone")
        if craft_zone is not None and (not isinstance(craft_zone, str) or craft_zone not in valid_zone_names):
            violations.append(f"craft_zone が存在しません: {craft_zone!r}")

        requires = item.get("requires")
        if requires is not None:
            if not isinstance(requires, dict) or set(requires) != REQUIRES_KEYS:
                violations.append(f"requires のキーが不正です: {name!r}")
            else:
                knowledge = requires.get("knowledge")
                if not isinstance(knowledge, str) or knowledge not in valid_fact_ids:
                    violations.append(f"requires.knowledge が存在しません: {knowledge!r}")

        for boolean_key in ("lootable", "keepsake"):
            if boolean_key in item and not isinstance(item[boolean_key], bool):
                violations.append(f"{boolean_key} は真偽値で指定してください: {name!r}")

    made_from_graph: dict[str, set] = {
        item["name"]: set(item["made_from"])
        for item in raw_items
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("made_from"), dict)
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    cyclic: set[str] = set()

    def visit_made_from(node: str) -> None:
        if node in visiting:
            cyclic.add(node)
            return
        if node in visited:
            return
        visiting.add(node)
        for material in made_from_graph.get(node, ()):
            visit_made_from(material)
        visiting.discard(node)
        visited.add(node)

    for node in made_from_graph:
        visit_made_from(node)
    for name in sorted(cyclic):
        violations.append(f"made_from が循環しています: {name}")

    def check_fact_source(source: Any, fact_id: Any) -> None:
        if not isinstance(source, dict) or set(source) != FACT_SOURCE_KEYS:
            violations.append(f"sources のキーが不正です: {fact_id!r}")
            return
        if source.get("type") != "investigate":
            violations.append(f"sources.type は investigate のみです: {fact_id!r}")
        zone = source.get("zone")
        if not isinstance(zone, str) or zone not in valid_zone_names:
            violations.append(f"sources.zone が存在しません: {zone!r}")
        if type(source.get("count")) is not int or source.get("count") != 1:
            violations.append(f"sources.count は1のみです: {fact_id!r}")

    for fact in raw_facts:
        if not isinstance(fact, dict):
            violations.append("facts の要素はオブジェクトで指定してください")
            continue
        unknown = set(fact) - FACT_KEYS
        if unknown:
            violations.append("facts に未対応の項目があります: " + "、".join(sorted(str(k) for k in unknown)))
        fact_id = fact.get("id")
        check_name(fact_id, "事実")

        label = fact.get("label")
        if not (isinstance(label, str) and 1 <= len(label) <= 60):
            violations.append(f"label は1〜60文字で指定してください: {fact_id!r}")
        secrecy = fact.get("secrecy")
        if secrecy is not None and not (_is_number(secrecy) and 0 <= secrecy <= 1):
            violations.append(f"secrecy は0〜1で指定してください: {fact_id!r}")
        share_min_affinity = fact.get("share_min_affinity")
        if share_min_affinity is not None and not (_is_number(share_min_affinity) and -1 <= share_min_affinity <= 1):
            violations.append(f"share_min_affinity は-1〜1で指定してください: {fact_id!r}")

        sources = fact.get("sources")
        if not (isinstance(sources, list) and sources):
            violations.append(f"sources が1件以上必要です: {fact_id!r}")
        else:
            for source in sources:
                check_fact_source(source, fact_id)

        for relation in ("implies", "refutes"):
            rel = fact.get(relation)
            if rel is None:
                continue
            if not isinstance(rel, dict) or set(rel) != FACT_RELATION_KEYS:
                violations.append(f"{relation} のキーが不正です: {fact_id!r}")
                continue
            target = rel.get("fact")
            if not isinstance(target, str) or target not in valued_facts:
                violations.append(f"{relation}.fact が値付きの既存事実ではありません: {target!r}")
            else:
                value = rel.get("value")
                # WB-WORLD-DEMAND: a lottery-target implies must point at the
                # seed-drawn truth via a token, never a fixed candidate name --
                # engine/world.py resolves $truth/$innocent:N per seed
                # (fact_truth_tokens), so a fixed name would silently mean
                # something different every run instead of what the author
                # intended. refutes keeps the old fixed-name-only behavior
                # unchanged (v1 patches don't use refutes at all).
                if relation == "implies" and target in lottery:
                    # Exact strings only: the engine matches tokens literally, so
                    # "$innocent:01" or a full-width digit would pass a numeric
                    # parse here and then raise when the world is built.
                    allowed = innocent_tokens(lottery[target])
                    if not (isinstance(value, str) and value.startswith("$")):
                        violations.append(
                            f"この事実の真値は毎回くじで決まるので、value は $truth か $innocent:N で書いてください: {fact_id!r}")
                    elif value != "$truth" and value not in allowed:
                        violations.append(
                            (f"$innocent:N の N は1〜{len(allowed)}で指定してください（半角で）: {fact_id!r}" if allowed
                             else f"この事実に使えるのは $truth だけです: {fact_id!r}"))
                    # Any lottery fact's candidate, not just this target's: every
                    # one of them names something that changes per seed. The id
                    # reaches belief records too, so it is held to the same rule.
                    names = sorted({c for group in lottery.values() for c in group if c})
                    for where, text in (("label", fact.get("label")), ("id", fact_id)):
                        hit = next((c for c in names if isinstance(text, str) and c in text), None)
                        if hit:
                            violations.append(
                                f"誰が本物かは回ごとに変わるので、{where} に候補の名前は書けません: {fact_id!r}（{hit}）")
                elif relation == "implies" and isinstance(value, str) and value.startswith("$"):
                    violations.append(f"この事実の真値は固定なので、value は名前で書いてください: {fact_id!r}")
                elif not isinstance(value, str) or value not in valued_facts[target]:
                    violations.append(f"{relation}.value が対象事実の値にありません: {value!r}")
            confidence = rel.get("confidence")
            if not (_is_number(confidence) and 0 < confidence <= 1):
                violations.append(f"{relation}.confidence は0より大きく1以下で指定してください: {fact_id!r}")
            elif check_budgets and confidence > MAX_IMPLIES_CONFIDENCE:
                violations.append(f"{relation}.confidence は{MAX_IMPLIES_CONFIDENCE}以下で指定してください: {fact_id!r}")

    # WB-WORLDGROW-002 stage 2 review 1 fix M1 (design K): add.sources gives
    # an *existing* item/fact a new investigate source (add.items/add.facts
    # can only ever create brand-new names -- check_name above rejects
    # reusing an existing one). Never the goal item, a vehicle, a keepsake,
    # a crafted (made_from) item, or a lottery fact ($truth/$innocent's own
    # target) -- those either can't be gathered this way in the engine, or
    # their true value/role must not be quietly made easier to get.
    facts_by_id = {f.get("id"): f for f in (world.get("facts") or []) if isinstance(f, dict)}
    seen_source_targets: set[tuple[str, str]] = set()
    for entry in raw_sources:
        if not isinstance(entry, dict):
            violations.append("add.sources の要素はオブジェクトで指定してください")
            continue
        has_item, has_fact = "item" in entry, "fact" in entry
        if has_item == has_fact:
            violations.append("add.sources は item か fact のどちらか一方だけを指定してください")
            continue
        unknown = set(entry) - ADD_SOURCE_KEYS
        if unknown:
            violations.append("add.sources に未対応の項目があります: " + "、".join(sorted(str(k) for k in unknown)))
        source = entry.get("source")
        if has_item:
            name = entry.get("item")
            if not isinstance(name, str) or name not in existing["items"]:
                violations.append(f"add.sources.item が既存のアイテムではありません: {name!r}")
                continue
            item_def = items_by_name.get(name) or {}
            if (item_def.get("objective") or item_def.get("vehicle") or item_def.get("keepsake")
                    or (isinstance(item_def.get("made_from"), dict) and item_def.get("made_from"))):
                violations.append(f"この品には入手手段を足せません: {name}")
                continue
            check_item_source(source, name)
            existing_zones = {s.get("zone") for s in (item_def.get("sources") or []) if isinstance(s, dict)}
        else:
            name = entry.get("fact")
            if not isinstance(name, str) or name not in existing["facts"]:
                violations.append(f"add.sources.fact が既存の事実ではありません: {name!r}")
                continue
            # R1 (段階3 review 1): a valued (non-lottery) fact -- e.g.
            # oni_weakness/treasure_thief -- also fails contract_check's
            # "Valued fact cannot have direct sources" once applied; reject
            # it here too, with a clear reason, instead of letting it pass
            # the static gate and only fail later at the trial.
            if name in lottery or name in valued_facts:
                violations.append(f"この事実には入手手段を足せません（値が決まる事実です）: {name}")
                continue
            fact_def = facts_by_id.get(name) or {}
            check_fact_source(source, name)
            existing_zones = {s.get("zone") for s in (fact_def.get("sources") or []) if isinstance(s, dict)}
        zone = source.get("zone") if isinstance(source, dict) else None
        if isinstance(zone, str):
            if zone in existing_zones or (name, zone) in seen_source_targets:
                violations.append(f"同じ場所への入手手段が既にあります: {name}@{zone}")
            seen_source_targets.add((name, zone))

    if raw_events and not has_daily_slot:
        violations.append("この世界には日々の出来事の枠がありません")
    for event in raw_events:
        if not isinstance(event, dict):
            violations.append("daily_events の要素はオブジェクトで指定してください")
            continue
        unknown = set(event) - DAILY_EVENT_KEYS
        if unknown:
            violations.append("daily_events に未対応の項目があります: " + "、".join(sorted(str(k) for k in unknown)))
        event_id = event.get("id")
        check_name(event_id, "日々の出来事")
        label = event.get("label")
        if not (isinstance(label, str) and label):
            violations.append(f"label は空でない文字列で指定してください: {event_id!r}")
        weight = event.get("weight")
        if not (_is_number(weight) and 0 < weight <= 3):
            violations.append(f"weight は0より大きく3以下で指定してください: {event_id!r}")
        stress_delta = event.get("stress_delta")
        if not (_is_number(stress_delta) and -1 <= stress_delta <= 1):
            violations.append(f"stress_delta は-1〜1で指定してください: {event_id!r}")

    if check_budgets:
        applied = _applied_names(world)
        caps = addition_caps(world)
        extra_by_key = {"zones": 0, "items": sources_item_count, "facts": sources_fact_count}
        for key, new in (("zones", raw_zones), ("items", raw_items), ("facts", raw_facts)):
            cap, applied_count = caps[key]
            if applied_count + len(new) + extra_by_key[key] > cap:
                violations.append(f"{key} の累積追加数が上限（{cap}）を超えています")
        def item_give_count(item):
            sources = item.get("sources")
            if not isinstance(sources, list):
                return 1
            counted, count = False, 0
            for source in sources:
                if isinstance(source, dict) and type(source.get("max")) is int:
                    counted, count = True, count + source["max"]
            return count if counted else 1

        def give_total(items):
            total = 0.0
            for item in items:
                if not isinstance(item, dict) or item.get("keepsake"):
                    continue
                made_from = item.get("made_from")
                if isinstance(made_from, dict) and made_from:
                    continue
                give = item.get("give") or {}
                if isinstance(give, dict):
                    per_item = 0.0
                    for key, default in (("receiver_affinity", DEFAULT_RECEIVER_AFFINITY),
                                         ("giver_affinity", DEFAULT_GIVER_AFFINITY)):
                        value = give.get(key, default)
                        if _is_number(value):
                            per_item += value
                    total += per_item * item_give_count(item)
            return total
        given = give_total(raw_items)
        prior_given = give_total([i for i in world.get("items", []) if i.get("name") in applied["items"]])
        # Required 2 (段階3 review 1): fold in every prior patch's own
        # add.sources give contribution (added.sources_give, recorded by
        # apply_patch at application time -- see its own comment for why
        # this can't just be re-derived from the item's current sources).
        prior_given += sum(
            (entry.get("added") or {}).get("sources_give") or 0.0
            for entry in (world.get("expansion") or {}).get("patches", [])
        )
        # add.sources doesn't touch an existing item's own give -- but a
        # bigger max means more pickups of the same give, so it still has to
        # spend budget (design K, 段階3 review 1 必須3で修正: give を書いてい
        # ない既存の品も、エンジン/give_totalの既定値=受け手0.2・渡し手0.05
        # で数える -- 書いていない＝0円ではない).
        for entry in raw_sources:
            if not isinstance(entry, dict) or "item" not in entry:
                continue
            item_def = items_by_name.get(entry.get("item"))
            source = entry.get("source")
            if not isinstance(item_def, dict) or not isinstance(source, dict):
                continue
            give = item_def.get("give") or {}
            mx = source.get("max")
            if not isinstance(give, dict) or type(mx) is not int:
                continue
            per_item = 0.0
            for key, default in (("receiver_affinity", DEFAULT_RECEIVER_AFFINITY),
                                 ("giver_affinity", DEFAULT_GIVER_AFFINITY)):
                value = give.get(key, default)
                if _is_number(value):
                    per_item += value
            given += per_item * mx
        # No subject has give_item -> give never fires, so it costs no budget
        # (check_proposal_rules separately rejects writing give there at all).
        if give_available and (given > MAX_GIVE_PER_PATCH + 1e-12
                               or given + prior_given > MAX_GIVE_TOTAL + 1e-12):
            violations.append(
                "渡したときの効果の総量が上限を超えています（(受け手＋渡し手)×その品の sources の max の合計、を全アイテムで足して1パッチ0.6・"
                "累積1.2まで。give を書かない品も受け手0.2・渡し手0.05で数えます。"
                "たくさん拾える品は give の値を小さくしてください）")
        totals = {}
        for fact in [f for f in world.get("facts", []) if f.get("id") in applied["facts"]] + raw_facts:
            relation = fact.get("implies") if isinstance(fact, dict) else None
            if isinstance(relation, dict) and _is_number(relation.get("confidence")):
                target = (relation.get("fact"), relation.get("value"))
                if all(isinstance(v, str) for v in target):
                    totals[target] = totals.get(target, 0) + relation["confidence"]
        if any(v > MAX_IMPLIES_TOTAL + 1e-12 for v in totals.values()):
            violations.append("implies の対象ごとの累積効果が上限を超えています")
    return violations


def trigger_is_proposable(trigger: Any) -> bool:
    """Whether a world_demand.json trigger (raw entry, any kind) is one
    scripts/world_patch.py's `propose` can build a prompt for (WB-WORLDGROW-002
    S2): a `kind: "whiff"` trigger (or one with no `kind` at all -- pre-S1
    reports) needs `verb == "investigate"` specifically (build_prompt only
    ever handled that verb, v1); a route-layer "ignorance"/"blocked" trigger
    is always proposable once world_demand.py decided it clears its own
    thresholds -- there's no second verb-like gate for those kinds."""
    if not isinstance(trigger, dict):
        return False
    kind = trigger.get("kind", "whiff")
    if kind == "whiff":
        return trigger.get("verb") == "investigate"
    return kind in ("ignorance", "blocked")


# WB-WORLDGROW-002 S2: which of a trigger's own fields survive into a world's
# permanent expansion history (apply_patch's expansion.patches entry, below)
# -- keyed by kind so a pre-S1/whiff trigger's record is byte-identical to
# what it always was (just zone/verb; no "kind" key ever gets added to it).
# gapengine/world_patch_propose.py's make_patch has its own, separate keying
# for the *patch's own* trigger record (patch["trigger"]) -- a different,
# richer slimming for a different purpose (re-checking coverage later).
EXPANSION_TRIGGER_KEYS = {
    "whiff": ("zone", "verb"),
    "ignorance": ("kind", "zone", "count"),
    "blocked": ("kind", "requirement", "count", "stuck_zones"),
}


def apply_patch(world: dict, patch: dict) -> dict:
    """Apply one already-validated patch, returning a new world dict."""
    result = copy.deepcopy(world)
    add = patch.get("add") or {}
    zones = [z for z in (add.get("zones") or []) if isinstance(z, dict)]
    items = [i for i in (add.get("items") or []) if isinstance(i, dict)]
    facts = [f for f in (add.get("facts") or []) if isinstance(f, dict)]
    events = [e for e in (add.get("daily_events") or []) if isinstance(e, dict)]
    sources = [s for s in (add.get("sources") or []) if isinstance(s, dict)]

    result.setdefault("zones", [])
    result.setdefault("routes", {})
    for zone in zones:
        name = zone["name"]
        parent = zone["parent"]
        entry: dict[str, Any] = {"name": name}
        if zone.get("note") is not None:
            entry["note"] = zone["note"]
        result["zones"].append(entry)
        result["routes"].setdefault(parent, [])
        result["routes"][parent].append({"to": name})
        result["routes"][name] = [{"to": parent}]
        movement = result.get("movement")
        if isinstance(movement, dict) and isinstance(movement.get("destination_weights"), dict):
            movement["destination_weights"][name] = 1.0

    if items:
        result.setdefault("items", [])
        result["items"].extend(copy.deepcopy(item) for item in items)

    if facts:
        result.setdefault("facts", [])
        result["facts"].extend(copy.deepcopy(fact) for fact in facts)

    if events:
        daily = result.get("daily_events")
        if not isinstance(daily, dict):
            daily = {}
            result["daily_events"] = daily
        daily.setdefault("events", [])
        daily["events"].extend(copy.deepcopy(event) for event in events)

    added_source_labels: list[str] = []
    # Required 2 (段階3 review 1): the give budget an add.sources entry uses,
    # summed for *this* patch -- recorded below as added.sources_give so a
    # later patch's validate_patch can add it to prior_given without having
    # to re-derive it from the item's *current* (base+patches) sources list,
    # which would double-count a pre-existing base source's own max.
    sources_give_total = 0.0
    if sources:
        items_by_name = {i.get("name"): i for i in (result.get("items") or []) if isinstance(i, dict)}
        facts_by_id = {f.get("id"): f for f in (result.get("facts") or []) if isinstance(f, dict)}
        for entry in sources:
            source = entry.get("source")
            if not isinstance(source, dict):
                continue
            if "item" in entry:
                target = items_by_name.get(entry.get("item"))
            else:
                target = facts_by_id.get(entry.get("fact"))
            if not isinstance(target, dict):
                continue
            existing_sources = target.get("sources")
            if not isinstance(existing_sources, list):
                existing_sources = []
            target["sources"] = existing_sources + [copy.deepcopy(source)]
            added_source_labels.append(f"{entry.get('item') or entry.get('fact')}@{source.get('zone')}")
            if "item" in entry and not target.get("keepsake") and not (
                    isinstance(target.get("made_from"), dict) and target.get("made_from")):
                # 必須3で修正した既定値の扱いと揃える: give を書いていない品も
                # 受け手0.2・渡し手0.05で数える(engine/verbs.pyの既定と同じ)。
                give = target.get("give") or {}
                mx = source.get("max")
                if isinstance(give, dict) and type(mx) is int:
                    per_item = 0.0
                    for key, default in (("receiver_affinity", DEFAULT_RECEIVER_AFFINITY),
                                         ("giver_affinity", DEFAULT_GIVER_AFFINITY)):
                        value = give.get(key, default)
                        if _is_number(value):
                            per_item += value
                    sources_give_total += per_item * mx

    if zones or items or facts or events or sources:
        existing_expansion = result.get("expansion")
        expansion = (copy.deepcopy(existing_expansion) if isinstance(existing_expansion, dict)
                     else {"patches": []})
        expansion.setdefault("patches", [])
        expansion["base"] = result.get("name")
        entry = {"id": patch.get("id"), "title": patch.get("title")}
        trigger = patch.get("trigger")
        if isinstance(trigger, dict):
            kind = trigger.get("kind", "whiff")
            keys = EXPANSION_TRIGGER_KEYS.get(kind, EXPANSION_TRIGGER_KEYS["whiff"])
            slim = {k: trigger[k] for k in keys if k in trigger}
            if slim:
                entry["trigger"] = slim
        added: dict[str, Any] = {
            "zones": [z["name"] for z in zones],
            "items": [i["name"] for i in items],
            "facts": [f["id"] for f in facts],
            "daily_events": [e["id"] for e in events],
        }
        # A patch with no add.sources (every patch before WB-WORLDGROW-002
        # stage 2, and every stage-2+ patch that doesn't use it) must keep
        # emitting byte-identical expansion output -- no "sources" key at all.
        if added_source_labels:
            added["sources"] = added_source_labels
        # Required 2 (段階3 review 1): only present when non-zero, same
        # byte-identical-when-unused convention as "sources" above.
        if sources_give_total:
            added["sources_give"] = sources_give_total
        entry["added"] = added
        expansion["patches"].append(entry)
        result["expansion"] = expansion

    return result


def absolutize_references(world: dict, project_dir: Path, repo_root: Path = None, *,
                          references: dict | None = None) -> None:
    """Point world["gapengine"]'s action_graph/effects paths at absolute paths
    so they still resolve once a patched world.yaml is written to some other
    directory than `project_dir` (e.g. <out>/expanded-project, or a trial's
    scratch work_dir).

    With `references` (a role -> already-resolved absolute Path mapping, as
    `gapengine.world_patch_inputs.resolve_experiment_inputs` returns) this
    just writes those paths in directly -- no filesystem search -- so a
    caller that already resolved references against a caller-supplied
    repo_root (WB-WORLDGROW-001 N2: e.g. a --repo control-side copy) doesn't
    get silently overridden by this function's own, unrelated repo_root
    fallback search.

    Without `references`, mirrors the two candidates engine/world.py and
    engine/phase2.py already try (project-relative, then repo-root-relative);
    a field is left untouched if neither exists, so the engine's own error
    message still fires later. Moved here from scripts/evolve.py so
    world_patch_trial can reuse it without importing scripts/."""
    gapengine = world.get("gapengine")
    if references is not None:
        if not isinstance(gapengine, dict):
            gapengine = world.setdefault("gapengine", {})
        for role, path in references.items():
            gapengine[role] = str(path)
        return
    if not isinstance(gapengine, dict):
        return
    for field in ("action_graph", "effects"):
        value = gapengine.get(field)
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            continue
        for candidate in (project_dir / value, repo_root / value):
            if candidate.is_file():
                gapengine[field] = str(candidate.resolve())
                break


def patch_id_for(add: dict) -> str:
    payload = json.dumps(add, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "p-" + hashlib.sha256(payload).hexdigest()[:8]


def apply_patches(world: dict, patches: list[dict], *, subject_ids: Iterable[str] = (),
                   reserved: Iterable[str] = (), check_budgets: bool = True,
                   give_available: bool = True) -> dict:
    """Validate and apply each patch in order. Returns `world` unchanged
    (same object, no `expansion` key added) when `patches` is empty."""
    if not patches:
        return world
    current = world
    for patch in patches:
        violations = validate_patch(current, patch, subject_ids=subject_ids, reserved=reserved,
                                    check_budgets=check_budgets, give_available=give_available)
        if violations:
            raise PatchError(f"{patch.get('id')}: {violations[0]}")
        current = apply_patch(current, patch)
    return current


def materialize(world: dict, subjects: dict[str, dict], patches: list[dict], *,
                reserved=(), check_budgets: bool = True) -> tuple[dict, dict[str, dict]]:
    """Expand both data sets together, preserving exclusion and entry rules."""
    if not patches:
        return world, subjects
    current, people = world, copy.deepcopy(subjects)
    ids = [p.get("id") for p in people.values() if isinstance(p, dict)]
    # M2: derive give_available from the actual subjects instead of the
    # validate_patch default (True) -- a world with no give_item verb on
    # any subject must not be held to a give budget it can never spend.
    # Empty subjects means "unknown", not "nobody can give": charge the budget.
    give_available = not people or any(isinstance(subject, dict) and isinstance(subject.get("verbs"), list)
                          and "give_item" in subject["verbs"] for subject in people.values())
    for patch in patches:
        current = apply_patches(current, [patch], subject_ids=ids, reserved=reserved,
                                check_budgets=check_budgets, give_available=give_available)
        for zone in patch.get("add", {}).get("zones", []):
            parent, name = zone["parent"], zone["name"]
            for subject in people.values():
                limits = subject.get("range") if isinstance(subject, dict) else None
                if not isinstance(limits, dict) or not isinstance(limits.get("zones"), list):
                    continue
                if parent in limits["zones"] and name not in limits["zones"]:
                    limits["zones"].append(name)
                for rule in limits.get("exclude", []) or []:
                    if isinstance(rule, dict) and isinstance(rule.get("zones"), list) and parent in rule["zones"]:
                        if name not in rule["zones"]:
                            rule["zones"].append(name)
    return current, people
