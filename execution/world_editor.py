"""Targeted, revision-checked edits for the world settings workspace."""
from copy import deepcopy
import math
import uuid

import yaml

from engine.actions import FORCE_ARG_KINDS
from execution.library import LibraryStore, _validate_rel
from execution.provenance import ConfigError, canonical, contained, directory_lock, identifier, sha256


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
            "revision": revision,
            # WB-TIMEEVENT-001: verb -> argument kinds, so the time screen's
            # force_action form knows which fields to draw per verb.
            "force_action_specs": {verb: list(kinds) for verb, kinds in FORCE_ARG_KINDS.items()}}


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
        "genre": {"template_id"},
        "add-event": {"id", "label", "day", "slot", "targets", "verb", "args", "item_name", "item_count", "stress_delta"},
        "event": {"id", "label", "day", "slot", "targets", "verb", "args", "item_name", "item_count", "stress_delta"},
        "remove-event": set(),
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
        elif operation == "genre":
            # WB-WORLD-IMPORT-001: lets a world imported without a genre (or
            # any world) pick/switch its templates/<id>/ afterward. Other
            # gapengine keys (e.g. a future antagonist graph) are preserved.
            # identifier() rejects "..", "", and anything with a "/" up
            # front -- without it, "templates" / ".." resolves to store.repo
            # itself (is_dir() is true), letting a crafted template_id write
            # a path-traversing gapengine value. Its own ConfigError defaults
            # to code="invalid_config" (HTTP 422); every other bad input in
            # this function is a 400 via _bad(), so the failure is folded
            # into that same "ジャンルがありません" 400 instead.
            try:
                template_id = identifier(values["template_id"], "template_id")
            except ConfigError:
                template_id = None
            if template_id is None or not (store.repo / "templates" / template_id).is_dir():
                _bad("template_id", "ジャンルがありません")
            base_gapengine = world.get("gapengine")
            if not isinstance(base_gapengine, dict):
                base_gapengine = {}
            updated["gapengine"] = {**base_gapengine,
                                    "action_graph": f"templates/{template_id}/action_graph.yaml",
                                    "effects": f"templates/{template_id}/effects.yaml"}
        elif operation in ("add-event", "event", "remove-event"):
            events = list(world.get("scheduled_events") or [])
            if any(not isinstance(e, dict) for e in events):
                _bad("target", "予定された出来事の形式を確認してください")
            if operation == "remove-event":
                if type(target) is not int or not 0 <= target < len(events):
                    _bad("target", "予定された出来事がありません")
                del events[target]
                updated["scheduled_events"] = events
            else:
                if operation == "add-event":
                    if target is not None:
                        _bad("target", "追加には対象を指定しません")
                    base_event = {}
                else:
                    if type(target) is not int or not 0 <= target < len(events):
                        _bad("target", "予定された出来事がありません")
                    base_event = deepcopy(events[target])

                event_id = _text(values["id"], "id", required=True, maximum=120)
                other_ids = [str(e.get("id")) for i, e in enumerate(events) if operation != "event" or i != target]
                if event_id in other_ids:
                    _bad("id", "この予定イベントIDはすでに使われています")
                label = _text(values["label"], "label", maximum=120)

                day = values["day"]
                days_total = int((world.get("time") or {}).get("days", 1))
                if type(day) is not int or not 1 <= day <= days_total:
                    _bad("day", f"日数は1〜{days_total}の整数で指定してください")

                slot = values["slot"]
                slots = list((world.get("time") or {}).get("slots") or [])
                if not isinstance(slot, str) or (slot and slot not in slots):
                    _bad("slot", "時間帯を確認してください")

                targets = _list(values["targets"], "targets")
                if not targets:
                    _bad("targets", "対象を1人以上指定してください")
                person_ids = [p.get("id") for _, p in people]
                if any(t not in person_ids for t in targets):
                    _bad("targets", "登録された人物を選んでください")

                verb = values["verb"]
                if not isinstance(verb, str) or (verb and verb not in FORCE_ARG_KINDS):
                    _bad("verb", "行動を確認してください")
                if verb and not slot:
                    _bad("slot", "行動の固定には時間帯が必要です")
                if verb:
                    target_people = [p for _, p in people if p.get("id") in targets]
                    for target_person in target_people:
                        if verb not in (target_person.get("verbs") or []):
                            _bad("verb", f"{target_person.get('id')} は {verb} を取れません")

                raw_args = values["args"]
                kinds = FORCE_ARG_KINDS.get(verb, ()) if verb else ()
                if not isinstance(raw_args, list) or len(raw_args) > len(kinds):
                    _bad("args", "引数の数を確認してください")
                args = [_text(v, "args", maximum=120) for v in raw_args]
                fact_ids = [f.get("id") for f in (world.get("facts") or []) if isinstance(f, dict)]
                if verb == "share_knowledge":
                    fact_ids = [*fact_ids, "雑談"]
                seen_empty = False
                for index, value in enumerate(args):
                    if value == "":
                        seen_empty = True
                        continue
                    if seen_empty:
                        _bad("args", "引数は前から順に指定してください")
                    kind = kinds[index]
                    if kind == "zone" and value not in names:
                        _bad("args", "場所を確認してください")
                    elif kind == "subject" and value not in person_ids:
                        _bad("args", "人物を確認してください")
                    elif kind == "fact" and value not in fact_ids:
                        _bad("args", "知識を確認してください")
                    elif kind.startswith("enum:") and value not in kind[len("enum:"):].split("|"):
                        _bad("args", "値を確認してください")
                while args and args[-1] == "":
                    args.pop()

                item_name = _text(values["item_name"], "item_name", maximum=120)
                item_count = values["item_count"]
                stress_delta = values["stress_delta"]
                if type(stress_delta) not in (int, float) or not math.isfinite(stress_delta):
                    _bad("stress_delta", "ストレスの増減は数値で指定してください")
                if abs(stress_delta) > 1000:
                    _bad("stress_delta", "ストレスの増減は-1000〜1000で指定してください")

                base_event["id"] = event_id
                if label:
                    base_event["label"] = label
                else:
                    base_event.pop("label", None)
                base_event["day"] = day
                if slot:
                    base_event["slot"] = slot
                else:
                    base_event.pop("slot", None)
                base_event["targets"] = targets
                if item_name:
                    if type(item_count) is not int or not 1 <= item_count <= 999:
                        _bad("item_count", "個数は1〜999の整数で指定してください")
                    base_event["grants_item"] = {"name": item_name, "count": item_count}
                else:
                    base_event.pop("grants_item", None)
                if stress_delta:
                    base_event["stress_delta"] = stress_delta
                else:
                    base_event.pop("stress_delta", None)
                if verb:
                    base_event["force_action"] = {"verb": verb, "args": args}
                    base_event.pop("move_to", None)
                else:
                    base_event.pop("force_action", None)

                if operation == "add-event":
                    events.append(base_event)
                else:
                    events[target] = base_event
                updated["scheduled_events"] = events
        _validate_rel("world", rel)
        # One atomic file replacement per operation; the rest of the world is untouched.
        store._write_file("world", ident, rel, yaml.safe_dump(updated, allow_unicode=True, sort_keys=False))
        return snapshot(store, ident)
