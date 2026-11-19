"""Revision-checked world-local pressure evidence; no run or calendar writes."""
from copy import deepcopy
import re
import yaml

from engine.time_pressure import FILENAME, MAX_BYTES, validate_sources, validate_runtime
from execution import world_editor, person_values
from execution.provenance import ConfigError, canonical, contained, directory_lock, sha256
from execution.time_settings import _atomic


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("duplicate or non-string YAML key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _bad(field, message, code="bad_request"):
    raise ConfigError(field, message, code=code)


def _checked(settings, world, subjects):
    try:
        normalized = validate_sources(settings, world, subjects)
        if len(canonical(normalized)) > MAX_BYTES:
            raise ValueError("size limit")
        return normalized
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise ConfigError("time_pressure.sources", f"根拠の形式・参照先を確認してください。32件以内で、登録済みの目標・出来事・主人公・情報を指定します。詳細: {error}", code="bad_request") from error


def _stored(settings):
    """Keep safely shaped broken references readable so the editor can repair them."""
    def text(value):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= 120 and all(ord(c) >= 32 and ord(c) != 127 for c in value)
    def shape(value, keys):
        return isinstance(value, dict) and set(value) == set(keys)
    if (not shape(settings, {"version", "sources"}) or type(settings["version"]) is not int or settings["version"] != 1
            or not isinstance(settings["sources"], list) or len(settings["sources"]) > 32):
        raise ValueError("Invalid time pressure source document")
    seen = set()
    required = {"id", "anchor", "response_goal_id", "actor_ids", "awareness"}
    for source in settings["sources"]:
        if not isinstance(source, dict) or not required <= set(source) or set(source) - required - {"estimate"}:
            raise ValueError("Invalid time pressure source fields")
        if not text(source["id"]) or source["id"] in seen or not text(source["response_goal_id"]):
            raise ValueError("Invalid or duplicate source id")
        seen.add(source["id"])
        anchor = source["anchor"]
        if not shape(anchor, {"kind", "id"}) or anchor["kind"] not in ("goal_period", "scheduled_event") or not text(anchor["id"]):
            raise ValueError("Invalid time pressure anchor")
        if not isinstance(source["actor_ids"], list) or len(source["actor_ids"]) != 1 or not all(text(v) for v in source["actor_ids"]):
            raise ValueError("Invalid actor ids")
        aware = source["awareness"]
        if not isinstance(aware, dict) or (aware.get("kind") == "public" and not shape(aware, {"kind"})) or (
                aware.get("kind") == "known_fact" and (not shape(aware, {"kind", "fact_id"}) or not text(aware["fact_id"]))) or aware.get("kind") not in ("public", "known_fact"):
            raise ValueError("Invalid awareness")
        if "estimate" in source:
            estimate = source["estimate"]
            if not shape(estimate, {"duration", "fact_id"}) or type(estimate["duration"]) is not int or not 1 <= estimate["duration"] <= 10000 or not text(estimate["fact_id"]):
                raise ValueError("Invalid actor duration estimate")
    return settings


def _read(store, ident):
    base = world_editor._base(store, ident)
    if contained(base, person_values.JOURNAL).exists():
        _bad("world_revision", "世界の保存が中断されています。復旧してから再読み込みしてください", "conflict")
    files, world_revision = world_editor._read(store, ident)
    world = files["world.yaml"]
    subjects = {value["id"]: value for rel, value in files.items() if rel != "world.yaml" and isinstance(value.get("id"), str)}
    path = contained(base, FILENAME)
    exists = path.exists()
    if exists and path.stat().st_size > MAX_BYTES:
        _bad("storage", "期限と切迫の設定がサイズ上限を超えています")
    raw = path.read_bytes() if exists else b""
    if len(raw) > MAX_BYTES:
        _bad("storage", "期限と切迫の設定がサイズ上限を超えています")
    try:
        settings = yaml.load(raw.decode("utf-8"), Loader=_UniqueLoader) if exists else {"version": 1, "sources": []}
        settings = _stored(settings)
    except (ValueError, UnicodeError, yaml.YAMLError, RecursionError) as error:
        raise ConfigError("storage", "保存された期限と切迫の設定を確認してください", code="bad_request") from error
    revision = sha256(canonical({"exists": exists, "sha256": sha256(raw)}))
    return base, world, subjects, world_revision, settings, revision


def _catalog(world, subjects):
    labels = {c.get("id"): c.get("label") for c in (world.get("concerns") or {}).get("conditions", []) if isinstance(c, dict)}
    goals = [{"id": g["condition"], "label": labels.get(g["condition"]) or g["condition"],
              "deadline": (g.get("period") or {}).get("end")} for g in (world.get("goal_rules") or {}).get("definitions", []) if isinstance(g, dict) and isinstance(g.get("condition"), str)]
    facts = world.get("facts") or []
    facts = [{"id": key} for key in facts] if isinstance(facts, dict) else facts
    return {"goals": goals,
            "events": [{"id": e["id"], "label": e.get("label") or e["id"]} for e in world.get("scheduled_events", []) if isinstance(e, dict) and isinstance(e.get("id"), str)],
            "actors": [{"id": key, "label": key} for key in subjects if key == world.get("protagonist")],
            "facts": [{"id": f["id"], "label": f.get("label") or f["id"]} if isinstance(f, dict) else {"id": f, "label": f} for f in facts]}


def snapshot(store, ident):
    for _ in range(3):
        state = _read(store, ident)
        _, world, subjects, world_revision, settings, revision = state
        confirmed = _read(store, ident)
        if (world_revision, revision) == (confirmed[3], confirmed[5]):
            break
    else:
        _bad("revision", "保存中です。再読み込みしてください", "conflict")
    validation = {"valid": True}
    try:
        _checked(settings, world, subjects)
    except ConfigError as error:
        validation = {"valid": False, "message": str(error)}
    try:
        validate_runtime(world, subjects)
        availability = {"available": True, "reason": "通常の行動選択で主人公を対象に利用できます。実行設定でオンにすると適用します。"}
    except (ValueError, TypeError, KeyError):
        availability = {"available": False, "reason": "この世界の実行方式は未対応です。通常の主人公計画・目標ルールv1・既存の実行時計が必要です。設計カレンダーは実行に適用しません。"}
    if not validation["valid"]:
        availability = {"available": False, "reason": "保存済みの根拠の参照先が無効です。登録済みの目標・出来事・人物・情報へ選び直してください。"}
    return {"revision": revision, "world_revision": world_revision, "settings": deepcopy(settings),
            "catalog": _catalog(world, subjects), "availability": availability, "validation": validation}


def _request(body):
    if not isinstance(body, dict) or set(body) != {"revision", "world_revision", "settings"}:
        _bad("request", "読み込み時の版と期限の根拠を指定してください")
    for key in ("revision", "world_revision"):
        if not isinstance(body[key], str) or not re.fullmatch(r"[0-9a-f]{64}", body[key]):
            _bad(key, "読み込み時の版を指定してください")


def _validate_locked(store, ident, body):
    _, world, subjects, world_revision, _, revision = _read(store, ident)
    if body["revision"] != revision or body["world_revision"] != world_revision:
        _bad("revision", "世界または期限の根拠が別の画面で更新されています。入力を控えてから再読み込みしてください", "conflict")
    return _checked(body["settings"], world, subjects)


def validate(store, ident, body):
    _request(body)
    settings = _validate_locked(store, ident, body)
    _validate_locked(store, ident, body)
    return {"revision": body["revision"], "world_revision": body["world_revision"], "settings": settings, "validation": {"valid": True}}


def save(store, ident, body):
    _request(body)
    base = world_editor._base(store, ident)
    with directory_lock(base):
        settings = _validate_locked(store, ident, body)
        _atomic(contained(base, FILENAME), canonical(settings))
        return snapshot(store, ident)
