"""Read-only references to authored logical slot names, outside engine semantics.

Only current project/template inputs are inspected. Historical run snapshots are
never visited. Route goals are survey goals, not natural agent action goals.
The caller owns locking and must compare dependency_hash again before saving.
"""
from copy import deepcopy
from pathlib import Path

import yaml

from execution.library import MAX_BYTES
from execution.provenance import ConfigError, canonical, contained, identifier, sha256
from gapengine.route_goals import clock_minutes


VERSION = 1


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


def _issue(items, field, message):
    value = {"field": field, "message": message}
    if value not in items:
        items.append(value)


def _route_file(root, relative, display, errors):
    """Return parsed data plus bounded dependency metadata; never leak OS paths."""
    record = {"path": display, "present": None}
    try:
        path = contained(root, relative)
        if not path.exists():
            record["present"] = False
            return None, record
        record["present"] = True
        if not path.is_file():
            record["state"] = "not_file"
            _issue(errors, display, display + " は通常のYAMLファイルで指定してください。")
            return None, record
        record["size"] = path.stat().st_size
        if record["size"] > MAX_BYTES:
            record["state"] = "oversize"
            _issue(errors, display, display + " は256KB以内で指定してください。")
            return None, record
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        record.update(size=len(raw), sha256=sha256(raw))
        if len(raw) > MAX_BYTES:
            record["state"] = "oversize"
            _issue(errors, display, display + " は256KB以内で指定してください。")
            return None, record
        try:
            value = yaml.load(raw.decode("utf-8"), Loader=_UniqueLoader)
            if not isinstance(value, dict):
                raise ValueError("route document must be a mapping")
        except (ValueError, TypeError, UnicodeError, yaml.YAMLError, RecursionError):
            record["state"] = "invalid_yaml"
            _issue(errors, display, display + " のYAML形式・重複キーを確認してください。")
            return None, record
        record["state"] = "read"
        return value, record
    except ConfigError:
        record["state"] = "unsafe_path"
        _issue(errors, display, display + " は保存範囲内のリンクを経由しないファイルで指定してください。")
    except OSError:
        record["state"] = "unreadable"
        _issue(errors, display, display + " を読み取れません。参照を確認してから変更してください。")
    return None, record


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _goal_time(raw):
    """Check the existing GoalSpec shape, not condition target/route validity."""
    if not isinstance(raw, dict) or set(raw) - {"actor", "all", "time"} or "all" not in raw:
        raise ValueError("goal fields")
    if "actor" in raw and not _text(raw["actor"]):
        raise ValueError("goal actor")
    if not isinstance(raw["all"], list) or not raw["all"]:
        raise ValueError("goal conditions")
    for condition in raw["all"]:
        if not isinstance(condition, dict):
            raise ValueError("goal condition")
        kind = condition.get("predicate")
        field = {"at": "zone", "holds": "item", "knows": "fact"}.get(kind) if isinstance(kind, str) else None
        if field is None:
            raise ValueError("goal predicate")
        allowed = {"predicate", "subject", field} | ({"quantity"} if kind == "holds" else set())
        if set(condition) - allowed or field not in condition or not _text(condition[field]):
            raise ValueError("goal condition fields")
        if "subject" in condition and not _text(condition["subject"]):
            raise ValueError("goal subject")
        if kind == "holds" and (type(condition.get("quantity", 1)) is not int or condition.get("quantity", 1) < 1):
            raise ValueError("goal quantity")
    timing = raw.get("time", {"mode": "any"})
    if not isinstance(timing, dict) or set(timing) - {"mode", "day", "slot"} or "mode" not in timing:
        raise ValueError("goal time")
    mode = timing["mode"]
    if not isinstance(mode, str) or mode not in {"any", "at", "by"}:
        raise ValueError("goal time mode")
    if mode == "any":
        if set(timing) != {"mode"}:
            raise ValueError("any with a point")
        return None
    day, slot = timing.get("day", 1), timing.get("slot")
    if type(day) is not int or day < 1:
        raise ValueError("goal day")
    clock_minutes(slot)  # Same pure HH:MM parser used by GoalSpec.
    return {"mode": mode, "day": day, "slot": slot}


def snapshot(store, ident, world):
    """Inspect current authored refs with no world/survey execution or writes.

    Fields beyond the public contract: slots/days are the baseline for warnings;
    has_survey_goal also covers explicit mode=any goals. The dependency hash
    tracks project override absence/presence, effective raw content, and genre.
    It intentionally ignores shadowed template contents and all historical runs.
    """
    references, errors, warnings = [], [], []
    result = {"version": VERSION, "references": references, "errors": errors,
              "warnings": warnings, "slots": [], "days": None,
              "has_survey_goal": False, "has_timed_survey_goal": False}
    records = {"version": VERSION, "genre": None, "effective_source": None}

    def finish():
        result["dependency_hash"] = sha256(canonical(records))
        return result

    if not isinstance(world, dict):
        _issue(errors, "world", "世界設定の形式を確認してください。")
        records["state"] = "invalid_world"
        return finish()
    time = world.get("time") or {}
    if isinstance(time, dict):
        slots = time.get("slots")
        if isinstance(slots, (list, tuple)) and all(isinstance(s, str) for s in slots):
            result["slots"] = list(slots)
        if type(time.get("days")) is int:
            result["days"] = time["days"]
    events = world.get("scheduled_events") or []
    if not isinstance(events, list):
        _issue(errors, "scheduled_events", "予定された出来事の形式を確認してください。")
    else:
        for index, event in enumerate(events):
            field = f"scheduled_events.{index}.slot"
            if not isinstance(event, dict):
                _issue(errors, field, "予定された出来事の形式を確認してください。")
                continue
            slot = event.get("slot")
            if slot is None or slot == "":
                continue
            if not isinstance(slot, str):
                _issue(errors, field, "予定された出来事の時間帯は名前で指定してください。")
                continue
            label = next((event[k] for k in ("label", "id") if _text(event.get(k))), f"出来事{index + 1}")
            reference = {"slot": slot, "kind": "scheduled_event", "label": label[:256],
                         "path": field, "source": "world"}
            if type(event.get("day")) is int:
                reference["day"] = event["day"]
            if _text(event.get("id")):
                reference["id"] = event["id"][:120]
            references.append(reference)
    policy = world.get("action_usage") or {}
    constraints = policy.get('constraints', {}) if isinstance(policy,dict) else None
    if not isinstance(constraints,dict):
        _issue(errors, "action_usage", "行動の人物・時間帯設定の形式を確認してください。")
    else:
        for ref, rule in constraints.items():
            slots = rule.get('slots') if isinstance(rule,dict) else False
            if slots is not None and (not isinstance(slots,list) or any(not isinstance(v,str) for v in slots)):
                _issue(errors, "action_usage", "行動の時間帯参照の形式を確認してください。")
                continue
            for slot in slots or []:
                references.append({"slot": slot, "kind": "action_constraint", "label": "行動「" + str(ref) + "」", "path": "action_usage.constraints." + str(ref), "source": "world"})
    try:
        records["world_id"] = identifier(ident, "world_id")
        project = store._base("world", ident)
        if not project.is_dir():
            _issue(errors, "world_id", "現在の世界の保存先がありません。")
            records["state"] = "missing_world"
            return finish()
    except (ConfigError, OSError, ValueError, TypeError):
        _issue(errors, "world_id", "世界の保存先を安全に確認できません。")
        records["state"] = "unsafe_world"
        return finish()
    genre = None
    try:
        selected = store._genre_of(ident, world)  # Official LibraryStore genre resolution.
        if selected is not None:
            genre = identifier(selected, "template_id")
        records["genre"] = genre
    except (ConfigError, OSError, ValueError, TypeError, AttributeError):
        _issue(errors, "genre", "経路調査のジャンルを安全に判別できません。")
        records["genre_state"] = "unresolved"
    config, records["project_route"] = _route_file(project, "route.yaml", "route.yaml", errors)
    source, display = "world", "route.yaml"
    if records["project_route"]["present"] is False:
        source, config = "template", None
        if genre is not None:
            display = f"templates/{genre}/route.yaml"
            try:
                template = contained(store.repo, "templates/" + genre)
                if not template.is_dir():
                    _issue(errors, "genre", "選択した経路調査のジャンルがありません。")
                    records["template_route"] = {"path": display, "state": "missing_template"}
                else:
                    config, records["template_route"] = _route_file(template, "route.yaml", display, errors)
            except (ConfigError, OSError):
                _issue(errors, "genre", "経路調査のジャンルを安全に読み取れません。")
                records["template_route"] = {"path": display, "state": "unsafe_template"}
    elif genre is None:
        _issue(warnings, "route.yaml", "世界側の経路調査設定を参照していますが、調査のジャンルは未選択です。")
    if config is None:
        return finish()
    records["effective_source"] = source
    raw_goal = config.get("goal")
    if raw_goal is None:
        return finish()
    try:
        timing = _goal_time(raw_goal)
    except (ValueError, TypeError, KeyError, RecursionError):
        _issue(errors, display + ".goal", "経路調査の達成条件・時点の形式を確認してください。")
        return finish()
    result["has_survey_goal"] = True
    if timing is not None:
        result["has_timed_survey_goal"] = True
        references.append({**timing, "kind": "route_survey_goal", "source": source,
                           "path": display + ".goal.time.slot",
                           "label": "経路調査の達成時点（" + ("この世界" if source == "world" else "ジャンル既定") + "）"})
    return finish()


def validate(catalog, candidate_slots, days):
    """Validate named refs and warn about survey projection; never mutate input.

    This is a reference/shape check, not proof of survey reachability or complete
    route-option/condition-target validity. The normal engine remains unchanged.
    """
    if (not isinstance(catalog, dict) or catalog.get("version") != VERSION
            or not isinstance(catalog.get("references"), list)
            or not isinstance(catalog.get("errors"), list)
            or not isinstance(catalog.get("warnings"), list)):
        return [{"field": "time_references", "message": "時間帯の参照確認をやり直してください。"}], []
    errors, warnings = deepcopy(catalog["errors"]), deepcopy(catalog["warnings"])
    if (not isinstance(candidate_slots, (list, tuple)) or not 1 <= len(candidate_slots) <= 100
            or any(not isinstance(s, str) or not s.strip() or len(s) > 120
                   or any(ord(c) < 32 or ord(c) == 127 for c in s) for s in candidate_slots)):
        _issue(errors, "slots", "時間帯は空欄にせず1〜100個の名前で指定してください。")
        return errors, warnings
    if len(set(candidate_slots)) != len(candidate_slots):
        _issue(errors, "slots", "同じ名前の時間帯が重複しています。")
    if type(days) is not int or not 1 <= days <= 10000:
        _issue(errors, "days", "期間は1〜10000日の整数で指定してください。")
        return errors, warnings
    for ref in catalog["references"]:
        if not isinstance(ref, dict) or not isinstance(ref.get("slot"), str):
            _issue(errors, "time_references", "時間帯の参照確認をやり直してください。")
            continue
        if ref["slot"] not in candidate_slots:
            _issue(errors, ref.get("path", "slots"), f'「{ref.get("label", "設定")}」が時間帯「{ref["slot"]}」を使用しています。参照先を残してください。')
        if ref.get("kind") == "route_survey_goal" and type(ref.get("day")) is int and ref["day"] > days:
            _issue(errors, ref.get("path", "route.goal.time"), "経路調査の達成時点が新しい期間の範囲外になります。")
    changed = list(candidate_slots) != catalog.get("slots") or days != catalog.get("days")
    if changed and catalog.get("has_survey_goal"):
        _issue(warnings, "route.goal.time", "時間帯の並び・枠数・日数の変更は経路調査の時点や投影の意味にも影響します。自動では移し替えません。これは人物の行動目標とは別の調査設定です。")
        try:
            minutes = [clock_minutes(s) for s in candidate_slots]
            valid_projection = (days == 1 and len(minutes) >= 2 and minutes[1] > minutes[0]
                                and all(b - a == minutes[1] - minutes[0] for a, b in zip(minutes, minutes[1:])))
        except (ValueError, TypeError):
            valid_projection = False
        if not valid_projection:
            _issue(warnings, "route.goal.time", "経路調査の時刻投影は1日内・2枠以上・昇順で等間隔のHH:MMに限られます。通常の物語実行とは別に、経路調査では未対応となる場合があります。到達不可能とは判定していません。")
    return errors, warnings
