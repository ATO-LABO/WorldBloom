"""Calendar design drafts, independent from the simulator's fixed time model.

Only project sidecars are written. A design version is never an executable run
configuration, and existing world files, events and run references stay intact.
"""
from copy import deepcopy
from datetime import date, timedelta
import json
import os
import re
import uuid

from execution import world_editor
from execution.provenance import ConfigError, canonical, contained, directory_lock, sha256

DRAFT = "time-settings-draft.json"
VERSION_DIR = "time-settings-versions"
MAX_BYTES = 262144
MAX_DAYS = 3660
PREVIEW_DAYS = 366
MAX_SEGMENTS = 10000
MAX_BAND_DEFINITIONS = 100
UNSUPPORTED = "複数日・可変判断間隔は現行時間エンジン未対応です。設計版として保存し、実行には適用しません。"


def _bad(field, message, code="bad_request"):
    raise ConfigError(field, message, code=code)


def default_settings():
    return {"schema_version": 1, "period": {"start_date": "", "end_date": ""},
            "normal": {"bands": [{"id": "day", "name": "通常", "start": "09:00", "end": "17:00",
                                   "overnight": False, "interval_minutes": 60, "event_id": ""}],
                       "weekdays": {}, "holidays": []}, "specials": []}


def _shape(value, fields, required, field):
    if not isinstance(value, dict) or not required <= set(value) or not set(value) <= fields:
        _bad(field, "設定の項目と形式を確認してください")


def _bounded_json(value):
    # Reject non-JSON values, infinities and pathological nesting before persistence.
    def walk(item, depth=0):
        if depth > 12:
            _bad("settings", "設定の階層が深すぎます")
        if isinstance(item, dict):
            if len(item) > 100 or any(not isinstance(k, str) or len(k) > 120 for k in item):
                _bad("settings", "設定の項目数・名前が長すぎます")
            for child in item.values():
                walk(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 100:
                _bad("settings", "一覧は100項目以内で指定してください")
            for child in item:
                walk(child, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool):
            _bad("settings", "JSON形式で指定してください")
        elif isinstance(item, str) and len(item) > 8000:
            _bad("settings", "文字列が長すぎます")
    walk(value)
    try:
        data = canonical(value)
    except (ValueError, TypeError, OverflowError, RecursionError):
        _bad("settings", "有限のJSON値で指定してください")
    if len(data) > MAX_BYTES:
        _bad("settings", "設定は256KB以内で指定してください")


def _structure(settings):
    _bounded_json(settings)
    _shape(settings, {"schema_version", "period", "normal", "specials"},
           {"schema_version", "period", "normal", "specials"}, "settings")
    if type(settings["schema_version"]) is not int or settings["schema_version"] != 1:
        _bad("schema_version", "対応していない設定版です")
    _shape(settings["period"], {"start_date", "end_date"}, {"start_date", "end_date"}, "period")
    normal = settings["normal"]
    _shape(normal, {"bands", "weekdays", "holidays"}, {"bands", "weekdays", "holidays"}, "normal")
    band_count = 0
    def bands(values, field):
        nonlocal band_count
        if not isinstance(values, list):
            _bad(field, "時間帯は一覧で指定してください")
        band_count += len(values)
        if band_count > MAX_BAND_DEFINITIONS:
            _bad(field, "時間帯の定義は設定全体で100件以内にしてください")
        for i, band in enumerate(values):
            _shape(band, {"id", "name", "start", "end", "overnight", "interval_minutes", "event_id"},
                   {"id", "name", "start", "end", "overnight", "interval_minutes"}, f"{field}.{i}")
    bands(normal["bands"], "normal.bands")
    if not isinstance(normal["weekdays"], dict) or not set(normal["weekdays"]) <= set("0123456"):
        _bad("normal.weekdays", "曜日は0（月曜）から6（日曜）の項目で指定してください")
    for day, values in normal["weekdays"].items():
        bands(values, f"normal.weekdays.{day}")
    if not isinstance(normal["holidays"], list):
        _bad("normal.holidays", "休日は一覧で指定してください")
    for i, holiday in enumerate(normal["holidays"]):
        _shape(holiday, {"date", "bands"}, {"date", "bands"}, f"normal.holidays.{i}")
        bands(holiday["bands"], f"normal.holidays.{i}.bands")
    if not isinstance(settings["specials"], list):
        _bad("specials", "特別日は一覧で指定してください")
    for i, special in enumerate(settings["specials"]):
        field = f"specials.{i}"
        _shape(special, {"id", "name", "enabled", "selector", "priority", "bands"},
               {"id", "name", "enabled", "selector", "priority", "bands"}, field)
        _shape(special["selector"], {"kind", "start", "end", "offset"}, {"kind"}, field + ".selector")
        bands(special["bands"], field + ".bands")


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _minute(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        return None
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _clock(minutes):
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def evaluate(settings, world):
    """Validate a structurally safe draft and resolve calendar design previews."""
    _structure(settings)
    errors, unresolved = [], []
    errors_truncated = False
    def error(field, message, code="invalid", sources=None):
        nonlocal errors_truncated
        record = {"field": field, "message": message, "code": code}
        if sources:
            record["sources"] = sources
        if record not in errors:
            if len(errors) < 200:
                errors.append(record)
            else:
                errors_truncated = True
    def missing(field, message):
        record = {"field": field, "message": message}
        if record not in unresolved:
            unresolved.append(record)
    period = settings["period"]
    dates = []
    for key in ("start_date", "end_date"):
        value = period[key]
        if value in (None, ""):
            missing(f"period.{key}", "期間の日付は未確定です")
        elif _date(value) is None:
            error(f"period.{key}", "日付を YYYY-MM-DD で指定してください")
        dates.append(_date(value))
    first, last = dates
    if first and last:
        if first > last:
            error("period", "開始日が終了日より後です")
        elif (last - first).days >= MAX_DAYS:
            error("period", f"期間は{MAX_DAYS}日以内で指定してください")
    events = {}
    for item in world.get("scheduled_events") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            events.setdefault(item["id"], []).append(item)

    def checked_bands(bands, field, source):
        output, ids = {}, set()
        for index, band in enumerate(bands):
            key = f"{field}.{index}"
            ident = band["id"]
            if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", ident):
                error(key + ".id", "時間帯IDの形式を確認してください")
                continue
            if ident in ids:
                error(key + ".id", "同じ時間帯IDが重複しています", sources=[source])
                continue
            ids.add(ident)
            if not isinstance(band["name"], str) or not band["name"].strip() or len(band["name"]) > 120:
                error(key + ".name", "時間帯名を120文字以内で指定してください")
            start, end = _minute(band["start"]), _minute(band["end"])
            event_id = band.get("event_id", "")
            event_ref = None
            if not isinstance(event_id, str):
                error(key + ".event_id", "既存イベントIDを指定してください")
            elif event_id:
                matches = events.get(event_id, [])
                if len(matches) != 1:
                    error(key + ".event_id", "既存イベントを一意に参照できません")
                else:
                    event_ref = {k: matches[0].get(k) for k in ("id", "label", "day", "slot")}
                    start = _minute(event_ref["slot"])
                    if start is None:
                        missing(key + ".event_id", "参照イベントの時刻は未確定です（既存の時間帯名を保持）")
            if start is None and not event_ref:
                error(key + ".start", "開始時刻を HH:MM で指定してください")
            if end is None:
                error(key + ".end", "終了時刻を HH:MM で指定してください")
            interval = band["interval_minutes"]
            if type(interval) is not int or not 1 <= interval <= 1440:
                error(key + ".interval_minutes", "判断間隔は1〜1440分の整数で指定してください")
            if type(band["overnight"]) is not bool:
                error(key + ".overnight", "夜跨ぎの有無を指定してください")
            elif start is not None and end is not None and end <= start and not band["overnight"]:
                error(key + ".overnight", "終了が開始以前の場合は夜跨ぎを指定してください")
            output[ident] = {**deepcopy(band), "_start": start,
                             "_end": None if end is None else end + (1440 if start is not None and end <= start and band["overnight"] is True else 0),
                             "source_ids": [source], "event_reference": event_ref}
        return output

    normal = settings["normal"]
    base = checked_bands(normal["bands"], "normal.bands", "normal")
    if not base:
        error("normal.bands", "通常の時間帯を1つ以上指定してください")
    weekdays = {key: checked_bands(bands, f"normal.weekdays.{key}", f"weekday:{key}")
                for key, bands in normal["weekdays"].items()}
    holidays = {}
    for i, holiday in enumerate(normal["holidays"]):
        target = _date(holiday["date"])
        if target is None:
            error(f"normal.holidays.{i}.date", "休日の日付を YYYY-MM-DD で指定してください")
        elif target in holidays:
            error(f"normal.holidays.{i}.date", "同じ休日の日付が重複しています")
        else:
            holidays[target] = checked_bands(holiday["bands"], f"normal.holidays.{i}.bands", f"holiday:{i}")
        if target and first and last and not first <= target <= last:
            error(f"normal.holidays.{i}.date", "休日が期間外です")

    specials, special_ids = [], set()
    for i, special in enumerate(settings["specials"]):
        field, ident = f"specials.{i}", special["id"]
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", ident):
            error(field + ".id", "特別日IDの形式を確認してください")
            ident = f"invalid:{i}"
        if ident in special_ids:
            error(field + ".id", "特別日IDが重複しています")
        special_ids.add(ident)
        if not isinstance(special["name"], str) or not special["name"].strip() or len(special["name"]) > 120:
            error(field + ".name", "特別日名を120文字以内で指定してください")
        if type(special["enabled"]) is not bool:
            error(field + ".enabled", "有効状態を指定してください")
        priority = special["priority"]
        if type(priority) is not int or not -10000 <= priority <= 10000:
            error(field + ".priority", "優先順位は-10000〜10000の整数で指定してください")
            priority = 0
        selector = special["selector"]
        kind = selector["kind"]
        start = end = None
        if kind in ("date", "range"):
            start = _date(selector.get("start"))
            end = start if kind == "date" else _date(selector.get("end"))
            if start is None or end is None:
                error(field + ".selector", "対象日を YYYY-MM-DD で指定してください")
        elif kind in ("first", "last"):
            offset = selector.get("offset", 0)
            if type(offset) is not int or not -3660 <= offset <= 3660:
                error(field + ".selector.offset", "相対日数は-3660〜3660の整数で指定してください")
            elif (first if kind == "first" else last) is None:
                missing(field + ".selector", "相対日の参照先が未確定です")
            else:
                try:
                    start = end = (first if kind == "first" else last) + timedelta(days=offset)
                except OverflowError:
                    error(field + ".selector", "相対日が日付の範囲外です")
        else:
            error(field + ".selector.kind", "単日・期間・初日・最終日を指定してください")
        if start and end:
            if start > end:
                error(field + ".selector", "特別日の開始が終了より後です")
            if first and last and (start < first or end > last):
                error(field + ".selector", "特別日が期間外です")
        bands = checked_bands(special["bands"], field + ".bands", ident)
        if not bands:
            error(field + ".bands", "対象時間帯を1つ以上指定してください")
        specials.append((special["enabled"] is True, priority, start, end, bands, ident, field))

    preview_days = []
    previous_tails = []
    segments_remaining = MAX_SEGMENTS
    segments_truncated = False
    day_count = (last - first).days + 1 if first and last and 0 <= (last - first).days < MAX_DAYS else 0
    for day_index in range(day_count):
        target = first + timedelta(days=day_index)
        resolved = deepcopy(base)
        resolved.update(deepcopy(weekdays.get(str(target.weekday()), {})))
        resolved.update(deepcopy(holidays.get(target, {})))
        choices = {}
        for enabled, priority, start, end, bands, ident, field in specials:
            if enabled and start and end and start <= target <= end:
                for band_id, band in bands.items():
                    choices.setdefault(band_id, []).append((priority, band, ident, field))
        conflicts = []
        for band_id, options in choices.items():
            highest = max(item[0] for item in options)
            winners = [item for item in options if item[0] == highest]
            # Labels are metadata; clock, interval and event identity are the target values.
            signatures = {canonical({k: item[1].get(k) for k in ("start", "end", "overnight", "interval_minutes", "event_id")}) for item in winners}
            if len(signatures) > 1:
                sources = [item[2] for item in winners]
                record = {"date": target.isoformat(), "band_id": band_id, "priority": highest, "sources": sources}
                conflicts.append(record)
                resolved.pop(band_id, None)
                for item in winners:
                    error(item[3] + ".bands", f"{target.isoformat()} の {band_id} は同順位で値が競合しています", "conflict", sources)
            else:
                resolved[band_id] = deepcopy(winners[0][1])
                resolved[band_id]["source_ids"] = [item[2] for item in winners]
        bands = list(resolved.values())
        for a_index, a in enumerate(bands):
            for b in bands[a_index + 1:]:
                if None in (a["_start"], a["_end"], b["_start"], b["_end"]):
                    continue
                intersects = max(a["_start"], b["_start"]) < min(a["_end"], b["_end"])
                if intersects:
                    sources = sorted(set(a["source_ids"] + b["source_ids"]))
                    error("bands", f"{target.isoformat()} の時間帯 {a['name']} と {b['name']} が重複しています", "overlap", sources)
        for prior in previous_tails:
            for band in bands:
                if band["_start"] is None or band["_end"] is None:
                    continue
                if max(0, band["_start"]) < min(prior["_end"] - 1440, band["_end"]):
                    sources = sorted(set(prior["source_ids"] + band["source_ids"]))
                    error("bands", f"{target.isoformat()} の時間帯 {band['name']} が前日の夜跨ぎ {prior['name']} と重複しています", "overlap", sources)
        previous_tails = [band for band in bands if band["_end"] is not None and band["_end"] > 1440]
        if day_index < PREVIEW_DAYS:
            display = []
            for band in bands:
                entry = {k: v for k, v in band.items() if not k.startswith("_")}
                entry["segments"] = []
                entry["resolved_start"] = None if band["_start"] is None else _clock(band["_start"])
                entry["resolved_end"] = None if band["_end"] is None else _clock(band["_end"])
                entry["remainder_minutes"] = None
                interval = band["interval_minutes"]
                if band["_start"] is not None and band["_end"] is not None and band["_end"] > band["_start"] and type(interval) is int and 1 <= interval <= 1440:
                    total = band["_end"] - band["_start"]
                    entry["remainder_minutes"] = total % interval
                    for current in range(band["_start"], band["_end"], interval):
                        if segments_remaining <= 0:
                            segments_truncated = True
                            entry["segments_truncated"] = True
                            break
                        segments_remaining -= 1
                        end = min(current + interval, band["_end"])
                        entry["segments"].append({"start": _clock(current), "end": _clock(end),
                                                  "start_day_offset": current // 1440, "end_day_offset": end // 1440,
                                                  "duration_minutes": end - current, "short": end - current < interval})
                display.append(entry)
            preview_days.append({"date": target.isoformat(), "bands": display, "conflicts": conflicts})
    validation = {"valid": not errors and not unresolved, "errors": errors, "errors_truncated": errors_truncated, "unresolved": unresolved}
    preview = {"status": "invalid" if errors else "unresolved" if unresolved else "ready",
               "days": preview_days, "unresolved": unresolved, "total_days": day_count,
               "truncated": day_count > PREVIEW_DAYS, "segments_truncated": segments_truncated, "engine_supported": False,
               "action_durations_changed": False}
    return validation, preview


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def _read(store, ident):
    world = world_editor.snapshot(store, ident)
    base = store._base("world", ident)
    path = contained(base, DRAFT)
    exists = path.exists()
    raw = path.read_bytes() if exists else b""
    if len(raw) > MAX_BYTES:
        _bad("storage", "下書きがサイズ上限を超えています", "invalid_config")
    if exists:
        try:
            settings = _json(raw.decode("utf-8"))
            _structure(settings)
        except (ValueError, UnicodeError) as error:
            raise ConfigError("storage", "保存された下書きの形式を確認してください") from error
    else:
        settings = default_settings()
    revision = sha256(canonical({"world": world["revision"], "draft": sha256(raw)}))
    return base, world, settings, revision


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = contained(path.parent, "." + path.name + "-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def snapshot(store, ident):
    base, world, settings, revision = _read(store, ident)
    validation, preview = evaluate(settings, world["world"])
    versions = []
    directory = contained(base, VERSION_DIR)
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            safe = contained(base, VERSION_DIR + "/" + path.name)
            try:
                raw_version = safe.read_bytes()
                if len(raw_version) > MAX_BYTES + 1024:
                    raise ValueError("version too large")
                value = _json(raw_version.decode("utf-8"))
                _shape(value, {"schema_version", "world_revision", "settings", "id", "engine_supported", "execution_ready"},
                       {"schema_version", "world_revision", "settings", "id", "engine_supported", "execution_ready"}, "versions")
                digest = sha256(canonical({"schema_version": value["schema_version"],
                                           "world_revision": value["world_revision"], "settings": value["settings"]}))
                if value["id"] != digest or path.stem != digest or value.get("engine_supported") is not False or value.get("execution_ready") is not False:
                    raise ValueError("version integrity")
                _structure(value["settings"])
            except (ValueError, KeyError, TypeError):
                raise ConfigError("versions", "設計版の整合性を確認してください")
            versions.append({"id": value["id"], "world_revision": value["world_revision"],
                             "engine_supported": False, "execution_ready": False})
    if _read(store, ident)[3] != revision:
        _bad("revision", "読み込み中に設定が更新されました。再読み込みしてください", "conflict")
    return {"revision": revision, "world_revision": world["revision"], "settings": settings,
            "validation": validation, "preview": preview, "versions": versions,
            "scheduled_events": deepcopy(world["world"].get("scheduled_events") or []),
            "engine_supported": False, "execution_ready": False, "unsupported_reason": UNSUPPORTED}


def _request(body):
    _shape(body, {"revision", "settings"}, {"revision", "settings"}, "request")
    if not isinstance(body["revision"], str) or len(body["revision"]) != 64:
        _bad("revision", "読み込み時の版を指定してください")
    _structure(body["settings"])


def validate(store, ident, body):
    _request(body)
    _, world, _, revision = _read(store, ident)
    if body["revision"] != revision:
        _bad("revision", "別の画面で設定が更新されています。再読み込みしてください", "conflict")
    validation, preview = evaluate(body["settings"], world["world"])
    if _read(store, ident)[3] != revision:
        _bad("revision", "検証中に設定が更新されました。再読み込みしてください", "conflict")
    return {"revision": revision, "validation": validation, "preview": preview,
            "engine_supported": False, "execution_ready": False, "unsupported_reason": UNSUPPORTED}


def save(store, ident, body):
    _request(body)
    base = world_editor._base(store, ident)
    with directory_lock(base):
        _, _, _, revision = _read(store, ident)
        if body["revision"] != revision:
            _bad("revision", "別の画面で設定が更新されています。再読み込みしてください", "conflict")
        _atomic(contained(base, DRAFT), canonical(body["settings"]))
        return snapshot(store, ident)


def archive_design(store, ident, body):
    """Save an immutable design version; do not change any execution settings."""
    _request(body)
    base = world_editor._base(store, ident)
    with directory_lock(base):
        _, world, _, revision = _read(store, ident)
        if body["revision"] != revision:
            _bad("revision", "別の画面で設定が更新されています。再読み込みしてください", "conflict")
        validation, _ = evaluate(body["settings"], world["world"])
        if not validation["valid"]:
            _bad("settings", "未確定・無効・競合の設定は設計版として確定できません", "invalid_config")
        source = {"schema_version": 1, "world_revision": world["revision"], "settings": body["settings"]}
        digest = sha256(canonical(source))
        value = {**source, "id": digest, "engine_supported": False, "execution_ready": False}
        path = contained(base, VERSION_DIR + "/" + digest + ".json")
        data = canonical(value)
        if path.exists():
            if path.read_bytes() != data:
                _bad("versions", "同じ設計版の内容が変更されています", "conflict")
        else:
            _atomic(path, data)
        result = snapshot(store, ident)
        result["version"] = value
        return result


def finalize(store, ident, body):
    """The current engine cannot finalize this calendar for execution."""
    validate(store, ident, body)
    _bad("time_settings", UNSUPPORTED, "unsupported")
