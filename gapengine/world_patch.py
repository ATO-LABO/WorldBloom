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

# ponytail: budgets are placeholders; tune once real proposals have been reviewed.
PATCH_RULES_VERSION = 2
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
# This bounds definitions, not affinity accumulated by repeated gifts.
DEFAULT_RECEIVER_AFFINITY = 0.2
DEFAULT_GIVER_AFFINITY = 0.05
EMPTY_STACK_DIGEST = hashlib.sha256(b"worldbloom-patch-stack-v1").hexdigest()

TOP_LEVEL_KEYS = frozenset({"id", "title", "rationale", "parent_digest", "trigger", "author", "add"})
ADD_KEYS = frozenset({"zones", "items", "facts"})
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


def verify_stack(project_dir) -> list[tuple[dict, bytes]]:
    """Validate a manifest snapshot, returning the exact verified patch bytes.

    Callers taking part in publication hold the external directory lock.
    This module intentionally never imports execution or engine.
    """
    folder = Path(project_dir) / "patches"
    stack = read_stack(project_dir)
    head, seen, result = EMPTY_STACK_DIGEST, set(), []
    try:
        for seq, entry in enumerate(stack["revisions"], 1):
            pid = entry["patch_id"]
            if not isinstance(pid, str) or not ID_RE.fullmatch(pid) or pid in seen:
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
            seen.add(pid)
            result.append((patch, raw))
        if stack["head"] != head:
            raise ValueError("head が一致しません")
        if result and (not stack.get("base_inputs_digest") or not stack.get("template_id")):
            raise ValueError("基準入力が記録されていません")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as error:
        raise PatchError(f"承認スタックが破損しています（repair不可）: {error}") from error
    extra = {p.stem for p in folder.glob("*.yaml")} - seen
    if extra:
        pid = sorted(extra)[0]
        state = "yaml と gate が移動済みで manifest 未更新" if (folder / f"{pid}.gate.json").exists() else "yaml だけ移動済み"
        raise PatchError(f"manifest に無いパッチ {pid}: {state}。repair を実行してください")
    return result


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


def _valued_facts(world: dict) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for fact in (world.get("facts") or []):
        if isinstance(fact, dict) and isinstance(fact.get("values"), list):
            result[str(fact.get("id"))] = {str(v) for v in fact["values"]}
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
                    reserved: Iterable[str] = (), check_budgets: bool = True) -> list[str]:
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

    raw_zones, raw_items, raw_facts, raw_events = [], [], [], []
    for key, bucket_name, target in (
        ("zones", "add.zones", "raw_zones"), ("items", "add.items", "raw_items"),
        ("facts", "add.facts", "raw_facts"), ("daily_events", "add.daily_events", "raw_events"),
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
        else:
            raw_events = value

    if not (raw_zones or raw_items or raw_facts or raw_events):
        violations.append("何も足していません")

    if check_budgets and len(raw_zones) > MAX_ZONES:
        violations.append(f"ゾーンの追加数が上限（{MAX_ZONES}）を超えています")
    if check_budgets and len(raw_items) > MAX_ITEMS:
        violations.append(f"アイテムの追加数が上限（{MAX_ITEMS}）を超えています")
    if check_budgets and len(raw_facts) > MAX_FACTS:
        violations.append(f"事実の追加数が上限（{MAX_FACTS}）を超えています")
    if len(raw_events) > MAX_DAILY_EVENTS:
        violations.append(f"日々の出来事の追加数が上限（{MAX_DAILY_EVENTS}）を超えています")

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
                if not isinstance(value, str) or value not in valued_facts[target]:
                    violations.append(f"{relation}.value が対象事実の値にありません: {value!r}")
            confidence = rel.get("confidence")
            if not (_is_number(confidence) and 0 < confidence <= 1):
                violations.append(f"{relation}.confidence は0より大きく1以下で指定してください: {fact_id!r}")
            elif check_budgets and confidence > MAX_IMPLIES_CONFIDENCE:
                violations.append(f"{relation}.confidence は{MAX_IMPLIES_CONFIDENCE}以下で指定してください: {fact_id!r}")

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
        applied = {key: set() for key in ADD_KEYS}
        for entry in (existing_expansion or {}).get("patches", []):
            for key in ADD_KEYS:
                applied[key].update((entry.get("added") or {}).get(key, []))
        for key, new in (("zones", raw_zones), ("items", raw_items), ("facts", raw_facts)):
            base_count = len(existing[key] - applied[key])
            cap = min(4 if key == "zones" else 8,
                      max(1, math.ceil(base_count * (0.4 if key == "zones" else 0.5))))
            if len(applied[key]) + len(new) > cap:
                violations.append(f"{key} の累積追加数が上限（{cap}）を超えています")
        def give_total(items):
            total = 0.0
            for item in items:
                if not isinstance(item, dict):
                    continue
                give = item.get("give") or {}
                if isinstance(give, dict):
                    for key, default in (("receiver_affinity", DEFAULT_RECEIVER_AFFINITY),
                                         ("giver_affinity", DEFAULT_GIVER_AFFINITY)):
                        value = give.get(key, default)
                        if _is_number(value):
                            total += value
            return total
        given = give_total(raw_items)
        prior_given = give_total([i for i in world.get("items", []) if i.get("name") in applied["items"]])
        if given > MAX_GIVE_PER_PATCH + 1e-12 or given + prior_given > MAX_GIVE_TOTAL + 1e-12:
            violations.append("give の効果総量が上限を超えています")
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


def apply_patch(world: dict, patch: dict) -> dict:
    """Apply one already-validated patch, returning a new world dict."""
    result = copy.deepcopy(world)
    add = patch.get("add") or {}
    zones = [z for z in (add.get("zones") or []) if isinstance(z, dict)]
    items = [i for i in (add.get("items") or []) if isinstance(i, dict)]
    facts = [f for f in (add.get("facts") or []) if isinstance(f, dict)]
    events = [e for e in (add.get("daily_events") or []) if isinstance(e, dict)]

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

    if zones or items or facts or events:
        existing_expansion = result.get("expansion")
        expansion = (copy.deepcopy(existing_expansion) if isinstance(existing_expansion, dict)
                     else {"patches": []})
        expansion.setdefault("patches", [])
        expansion["base"] = result.get("name")
        entry = {"id": patch.get("id"), "title": patch.get("title")}
        trigger = patch.get("trigger")
        if isinstance(trigger, dict):
            slim = {k: trigger[k] for k in ("zone", "verb") if k in trigger}
            if slim:
                entry["trigger"] = slim
        entry["added"] = {
            "zones": [z["name"] for z in zones],
            "items": [i["name"] for i in items],
            "facts": [f["id"] for f in facts],
            "daily_events": [e["id"] for e in events],
        }
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
                   reserved: Iterable[str] = (), check_budgets: bool = True) -> dict:
    """Validate and apply each patch in order. Returns `world` unchanged
    (same object, no `expansion` key added) when `patches` is empty."""
    if not patches:
        return world
    current = world
    for patch in patches:
        violations = validate_patch(current, patch, subject_ids=subject_ids, reserved=reserved,
                                    check_budgets=check_budgets)
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
    for patch in patches:
        current = apply_patches(current, [patch], subject_ids=ids, reserved=reserved,
                                check_budgets=check_budgets)
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
