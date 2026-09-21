"""Targeted, revision-checked edits for the world settings workspace."""
from copy import deepcopy
import math
import uuid

import yaml

from execution.library import LibraryStore, _validate_rel
from execution.provenance import ConfigError, canonical, contained, directory_lock, sha256


def _bad(field, message):
    raise ConfigError(field, message, code="bad_request")


def _text(value, field, *, required=False, maximum=8000):
    return LibraryStore._text(value, field, required=required, maximum=maximum)


def _list(value, field):
    if not isinstance(value, list) or len(value) > 100:
        _bad(field, "100項目以内の一覧で指定してください")
    result = [_text(v, field, required=True, maximum=120) for v in value]
    if len(set(result)) != len(result):
        _bad(field, "同じ項目が重複しています")
    return result


def _base(store, ident):
    base = store._base("world", ident)
    if not base.is_dir():
        raise ConfigError("world_id", "世界がありません", code="not_found")
    return base


def _read(store, ident):
    base = _base(store, ident)
    files = ["world.yaml"] + ["subjects/" + p.name for p in sorted((base / "subjects").glob("*.yaml"))]
    revisions, values = {}, {}
    for rel in files:
        raw = contained(base, rel).read_bytes()
        try:
            value = yaml.safe_load(raw.decode("utf-8"))
        except (yaml.YAMLError, UnicodeError) as error:
            raise ConfigError("content", f"{rel} を読めません。詳細設定で確認してください", code="bad_request") from error
        if not isinstance(value, dict):
            _bad("content", f"{rel} は項目と値の形式で指定してください")
        values[rel] = value
        revisions[rel] = sha256(raw)
    return values, sha256(canonical(revisions))


def _model(ident, files, revision):
    world = files["world.yaml"]
    return {"id": ident, "name": world.get("name") or ident, "world": world,
            "people": [value for rel, value in files.items() if rel != "world.yaml"],
            "overview": world.get("overview") or "", "intro": world.get("initial_story") or "",
            "revision": revision}


def snapshot(store, ident):
    files, revision = _read(store, ident)
    return _model(ident, files, revision)


def save(store, ident, body):
    if not isinstance(body, dict) or set(body) != {"revision", "operation", "target", "values"}:
        _bad("request", "保存する項目を指定してください")
    operation, target, values = body["operation"], body["target"], body["values"]
    allowed = {
        "overview": {"text", "name"}, "intro": {"text"}, "time": {"days", "slots"},
        "person": {"description", "personality", "target", "deliver_to"},
        "place": {"note"}, "add-person": {"name", "description", "entry"},
        "add-place": {"name", "description"}, "state": {"entry", "knowledge"},
        "route": {"item", "cost"},
        "roles": {"protagonist", "antagonist", "target_ending"},
        "file": {"content"},
    }
    if not isinstance(operation, str) or operation not in allowed or not isinstance(values, dict) or set(values) != allowed[operation]:
        _bad("values", "編集項目を確認してください")
    with directory_lock(_base(store, ident)):
        files, revision = _read(store, ident)
        if not isinstance(body["revision"], str) or body["revision"] != revision:
            raise ConfigError("revision", "別の画面で設定が更新されています。再読み込みして確認してください", code="conflict")
        world = files["world.yaml"]
        zones = world.get("zones") or []
        names = [z if isinstance(z, str) else z["name"] for z in zones]
        people = [(rel, p) for rel, p in files.items() if rel != "world.yaml"]
        rel, updated = "world.yaml", deepcopy(world)
        if operation == "file":
            if not isinstance(target, str) or target not in files:
                _bad("target", "登録された世界・人物ファイルを指定してください")
            content = values["content"]
            if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > 262144:
                _bad("content", "空ではない256KB以内の設定を指定してください")
            try:
                parsed = yaml.safe_load(content)
            except yaml.YAMLError:
                _bad("content", "YAMLの書式を確認してください")
            if not isinstance(parsed, dict):
                _bad("content", "項目と値の形式で指定してください")
            store._write_file("world", ident, target, content)
            return snapshot(store, ident)
        elif operation == "roles":
            ids = [p.get("id") for _, p in people]
            for key in ("protagonist", "antagonist"):
                value = _text(values[key], key, maximum=120)
                if value and ids.count(value) != 1:
                    _bad(key, "登録された人物を選んでください")
                updated[key] = value
            endings = _list(values["target_ending"], "target_ending")
            known = [e.get("id") for e in world.get("ending", []) if isinstance(e, dict)]
            if any(e not in known for e in endings):
                _bad("target_ending", "登録された結末を選んでください")
            updated["target_ending"] = endings
        elif operation in ("person", "state"):
            matches = [(r, p) for r, p in people if isinstance(target, str) and p.get("id") == target]
            if len(matches) != 1:
                _bad("target", "人物を一意に特定できません。詳細設定で確認してください")
            rel, person = matches[0]
            updated = deepcopy(person)
            if operation == "person":
                updated["description"] = _text(values["description"], "description")
                updated["personality"] = _text(values["personality"], "personality")
                goal = dict(updated.get("goal") or {})
                goal["target"] = _text(values["target"], "target", maximum=120)
                destination = _text(values["deliver_to"], "deliver_to", maximum=120)
                # Keep historical values available; new destinations must exist.
                if destination and destination not in names and destination != goal.get("deliver_to"):
                    _bad("deliver_to", "届け先は登録済みの場所を選んでください")
                goal["deliver_to"] = destination
                updated["goal"] = goal
            else:
                entry = _text(values["entry"], "entry", maximum=120)
                area = dict(updated.get("range") or {})
                if entry and entry not in names and entry != area.get("entry"):
                    _bad("entry", "居場所は登録済みの場所を選んでください")
                if entry:
                    area["entry"] = entry
                else:
                    area.pop("entry", None)
                updated["range"] = area
                updated["knowledge"] = _list(values["knowledge"], "knowledge")
        elif operation == "overview":
            updated["name"] = _text(values["name"], "name", required=True, maximum=120)
            updated["overview"] = _text(values["text"], "text")
        elif operation == "intro":
            updated["initial_story"] = _text(values["text"], "text")
        elif operation == "time":
            days = values["days"]
            if type(days) is not int or not 1 <= days <= 10000:
                _bad("days", "日数は1〜10000の整数で指定してください")
            slots = _list(values["slots"], "slots")
            if not slots:
                _bad("slots", "時間帯を1つ以上指定してください")
            updated["time"] = {**(updated.get("time") or {}), "days": days, "slots": slots}
        elif operation == "place":
            if not isinstance(target, str) or names.count(target) != 1:
                _bad("target", "場所を一意に特定できません")
            index = names.index(target)
            zone = updated["zones"][index]
            updated["zones"][index] = {**({"name": zone} if isinstance(zone, str) else zone),
                                       "note": _text(values["note"], "note")}
        elif operation in ("add-person", "add-place"):
            name = _text(values["name"], "name", required=True, maximum=120)
            description = _text(values["description"], "description")
            existing = [p.get("id") for _, p in people] if operation == "add-person" else names
            if name in existing:
                _bad("name", "この名前はすでに登録されています")
            if operation == "add-place":
                updated["zones"] = [*zones, {"name": name, "note": description}]
            else:
                entry = _text(values["entry"], "entry", maximum=120)
                if entry and entry not in names:
                    _bad("entry", "居場所は登録済みの場所を選んでください")
                rel = f"subjects/person_{uuid.uuid4().hex}.yaml"
                updated = {"id": name, "description": description,
                           "traits": {key: 0.5 for key in ("social", "stubbornness", "curiosity", "diligence", "temper")},
                           "base": 50, "range": {"zones": list(names), **({"entry": entry} if entry else {})}, "goal": {}}
        elif operation == "route":
            if not isinstance(target, dict) or set(target) != {"from", "index"} or not isinstance(target["from"], str) or type(target["index"]) is not int:
                _bad("target", "経路を指定してください")
            routes = (updated.get("routes") or {}).get(target["from"], [])
            if not 0 <= target["index"] < len(routes):
                _bad("target", "経路がありません")
            cost = values["cost"]
            if type(cost) not in (int, float) or not math.isfinite(cost) or not 0 < cost <= 10000:
                _bad("cost", "移動コストは0より大きい10000以下の数値で指定してください")
            route = routes[target["index"]]
            item = _text(values["item"], "item", maximum=120)
            if item:
                route["requires_item"] = item
            else:
                route.pop("requires_item", None)
            route["cost"] = cost
        _validate_rel("world", rel)
        # One atomic file replacement per operation; the rest of the world is untouched.
        store._write_file("world", ident, rel, yaml.safe_dump(updated, allow_unicode=True, sort_keys=False))
        return snapshot(store, ident)
