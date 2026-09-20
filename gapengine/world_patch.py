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
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")
_NAME_FORBIDDEN_CHARS = frozenset("'\"\n{}")

# ponytail: budgets are placeholders; tune once real proposals have been reviewed.
MAX_ZONES = 2
MAX_ITEMS = 4
MAX_FACTS = 4
MAX_DAILY_EVENTS = 3
MAX_TOTAL_PATCHES = 8

TOP_LEVEL_KEYS = frozenset({"id", "title", "rationale", "parent_rev", "approved_seq", "trigger", "author", "add"})
ADD_KEYS = frozenset({"zones", "items", "facts", "daily_events"})
ZONE_KEYS = frozenset({"name", "parent", "note"})
ITEM_KEYS = frozenset({"name", "sources", "lootable", "keepsake", "give", "modifier", "made_from", "craft_zone", "requires"})
ITEM_SOURCE_KEYS = frozenset({"type", "zone", "count", "max"})
GIVE_KEYS = frozenset({"receiver_affinity", "giver_affinity"})
MODIFIER_KEYS = frozenset({"id", "value", "kind", "visible"})
REQUIRES_KEYS = frozenset({"knowledge"})
FACT_KEYS = frozenset({"id", "label", "secrecy", "share_min_affinity", "sources", "implies", "refutes"})
FACT_SOURCE_KEYS = frozenset({"type", "zone", "count"})
FACT_RELATION_KEYS = frozenset({"fact", "value", "confidence"})
DAILY_EVENT_KEYS = frozenset({"id", "label", "weight", "stress_delta"})


class PatchError(ValueError):
    """A patch could not be loaded or applied."""


def load_patch(path) -> dict:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PatchError(f"{path}: パッチはオブジェクトである必要があります")
    return raw


def approved_patches(project_dir) -> list[dict]:
    """Approved patches under <project_dir>/patches/*.yaml, in approved_seq order.

    Never descends into patches/_proposed/ (glob is non-recursive).
    """
    folder = Path(project_dir) / "patches"
    if not folder.is_dir():
        return []
    patches = []
    for path in sorted(folder.glob("*.yaml")):
        patch = load_patch(path)
        if patch.get("id") != path.stem:
            raise PatchError(f"{path.name}: id がファイル名と一致しません")
        seq = patch.get("approved_seq")
        if type(seq) is not int:
            raise PatchError(f"{path.name}: approved_seq が整数ではありません")
        patches.append(patch)
    patches.sort(key=lambda p: (p["approved_seq"], str(p.get("id", ""))))
    return patches


def _is_name(value: Any) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 30
            and not any(c in _NAME_FORBIDDEN_CHARS for c in value))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


def validate_patch(world: dict, patch: dict, *, subject_ids: Iterable[str] = ()) -> list[str]:
    """Static gate. Returns a list of Japanese violation strings (empty = pass).

    Never raises -- a malformed patch just accumulates violations instead of
    crashing the caller.
    """
    violations: list[str] = []
    if not isinstance(patch, dict):
        return ["パッチはオブジェクトである必要があります"]

    unknown_top = set(patch) - TOP_LEVEL_KEYS
    if unknown_top:
        violations.append("未対応のトップレベル項目があります: " + "、".join(sorted(unknown_top)))

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
            violations.append("add に未対応の項目があります: " + "、".join(sorted(unknown_add)))

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

    if len(raw_zones) > MAX_ZONES:
        violations.append(f"ゾーンの追加数が上限（{MAX_ZONES}）を超えています")
    if len(raw_items) > MAX_ITEMS:
        violations.append(f"アイテムの追加数が上限（{MAX_ITEMS}）を超えています")
    if len(raw_facts) > MAX_FACTS:
        violations.append(f"事実の追加数が上限（{MAX_FACTS}）を超えています")
    if len(raw_events) > MAX_DAILY_EVENTS:
        violations.append(f"日々の出来事の追加数が上限（{MAX_DAILY_EVENTS}）を超えています")

    existing_expansion = world.get("expansion")
    existing_patch_count = (len(existing_expansion.get("patches", []))
                             if isinstance(existing_expansion, dict) else 0)
    if existing_patch_count + 1 > MAX_TOTAL_PATCHES:
        violations.append(f"適用後のパッチ総数が上限（{MAX_TOTAL_PATCHES}）を超えます")

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

    for zone in raw_zones:
        if not isinstance(zone, dict):
            violations.append("zones の要素はオブジェクトで指定してください")
            continue
        unknown = set(zone) - ZONE_KEYS
        if unknown:
            violations.append("zones に未対応の項目があります: " + "、".join(sorted(unknown)))
        check_name(zone.get("name"), "ゾーン")
        parent = zone.get("parent")
        if not (isinstance(parent, str) and parent in existing["zones"]):
            violations.append(f"parent が既存ゾーンではありません: {parent!r}")
        note = zone.get("note")
        if note is not None and not isinstance(note, str):
            violations.append("note は文字列で指定してください")

    def check_item_source(source: Any, item_name: Any) -> None:
        if not isinstance(source, dict) or set(source) != ITEM_SOURCE_KEYS:
            violations.append(f"sources のキーが不正です: {item_name!r}")
            return
        if source.get("type") != "investigate":
            violations.append(f"sources.type は investigate のみです: {item_name!r}")
        if source.get("zone") not in valid_zone_names:
            violations.append(f"sources.zone が存在しません: {source.get('zone')!r}")
        if source.get("count") != 1:
            violations.append(f"sources.count は1のみです: {item_name!r}")
        mx = source.get("max")
        if not (isinstance(mx, int) and not isinstance(mx, bool) and 1 <= mx <= 3):
            violations.append(f"sources.max は1〜3の整数で指定してください: {item_name!r}")

    for item in raw_items:
        if not isinstance(item, dict):
            violations.append("items の要素はオブジェクトで指定してください")
            continue
        unknown = set(item) - ITEM_KEYS
        if unknown:
            violations.append("items に未対応の項目があります: " + "、".join(sorted(unknown)))
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
                    if material not in provisional_item_names:
                        violations.append(f"made_from の素材は同じパッチで追加する新アイテムのみです: {material!r}")
                    if not (isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 3):
                        violations.append(f"made_from の個数は1〜3で指定してください: {material!r}")

        craft_zone = item.get("craft_zone")
        if craft_zone is not None and craft_zone not in valid_zone_names:
            violations.append(f"craft_zone が存在しません: {craft_zone!r}")

        requires = item.get("requires")
        if requires is not None:
            if not isinstance(requires, dict) or set(requires) != REQUIRES_KEYS:
                violations.append(f"requires のキーが不正です: {name!r}")
            else:
                knowledge = requires.get("knowledge")
                if knowledge not in valid_fact_ids:
                    violations.append(f"requires.knowledge が存在しません: {knowledge!r}")

        for boolean_key in ("lootable", "keepsake"):
            if boolean_key in item and not isinstance(item[boolean_key], bool):
                violations.append(f"{boolean_key} は真偽値で指定してください: {name!r}")

    def check_fact_source(source: Any, fact_id: Any) -> None:
        if not isinstance(source, dict) or set(source) != FACT_SOURCE_KEYS:
            violations.append(f"sources のキーが不正です: {fact_id!r}")
            return
        if source.get("type") != "investigate":
            violations.append(f"sources.type は investigate のみです: {fact_id!r}")
        if source.get("zone") not in valid_zone_names:
            violations.append(f"sources.zone が存在しません: {source.get('zone')!r}")
        if source.get("count") != 1:
            violations.append(f"sources.count は1のみです: {fact_id!r}")

    for fact in raw_facts:
        if not isinstance(fact, dict):
            violations.append("facts の要素はオブジェクトで指定してください")
            continue
        unknown = set(fact) - FACT_KEYS
        if unknown:
            violations.append("facts に未対応の項目があります: " + "、".join(sorted(unknown)))
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
            if target not in valued_facts:
                violations.append(f"{relation}.fact が値付きの既存事実ではありません: {target!r}")
            else:
                value = rel.get("value")
                if value not in valued_facts[target]:
                    violations.append(f"{relation}.value が対象事実の値にありません: {value!r}")
            confidence = rel.get("confidence")
            if not (_is_number(confidence) and 0 < confidence <= 0.5):
                violations.append(f"{relation}.confidence は0より大きく0.5以下で指定してください: {fact_id!r}")

    if raw_events and not has_daily_slot:
        violations.append("この世界には日々の出来事の枠がありません")
    for event in raw_events:
        if not isinstance(event, dict):
            violations.append("daily_events の要素はオブジェクトで指定してください")
            continue
        unknown = set(event) - DAILY_EVENT_KEYS
        if unknown:
            violations.append("daily_events に未対応の項目があります: " + "、".join(sorted(unknown)))
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


def apply_patches(world: dict, patches: list[dict], *, subject_ids: Iterable[str] = ()) -> dict:
    """Validate and apply each patch in order. Returns `world` unchanged
    (same object, no `expansion` key added) when `patches` is empty."""
    if not patches:
        return world
    current = world
    for patch in patches:
        violations = validate_patch(current, patch, subject_ids=subject_ids)
        if violations:
            raise PatchError(f"{patch.get('id')}: {violations[0]}")
        current = apply_patch(current, patch)
    return current
