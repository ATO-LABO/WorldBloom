"""Pure compilation of PR9 calendar designs into an integer-minute v2 clock.

Display intervals create checkpoints; they never define action durations.
Weekday/holiday/special definitions override *band IDs*, as in the PR9 design
schema. No v1 settings, worlds, events or stored design versions are rewritten.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from functools import lru_cache
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_DAYS = 3660
MAX_BANDS = 100
MAX_SEGMENTS = 100000
MAX_EVENTS = 1000
MAX_BYTES = 262144
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


class CalendarError(ValueError):
    """Invalid, conflicting or unsupported executable calendar input."""

    def __init__(self, field, message, *, code="invalid", sources=()):
        self.field, self.code, self.sources = field, code, list(sources)
        super().__init__(f"{field}: {message}")


def _bad(field, message, **details):
    raise CalendarError(field, message, **details)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json(value, field):
    def walk(item, depth=0):
        if depth > 12:
            _bad(field, "JSON nesting exceeds 12 levels")
        if type(item) is dict:
            if len(item) > 100 or any(type(k) is not str or len(k) > 120 for k in item):
                _bad(field, "invalid JSON mapping")
            for child in item.values():
                walk(child, depth + 1)
        elif type(item) is list:
            if len(item) > MAX_EVENTS:
                _bad(field, "JSON list exceeds the bounded input size")
            for child in item:
                walk(child, depth + 1)
        elif type(item) is str:
            if len(item) > 8000:
                _bad(field, "JSON string is too long")
        elif item is not None and type(item) not in (int, float, bool):
            _bad(field, "expected JSON values")
    walk(value)
    try:
        size = len(_canonical(value))
    except (ValueError, TypeError, OverflowError, RecursionError) as error:
        raise CalendarError(field, "expected finite JSON values") from error
    if size > MAX_BYTES + 1024:
        _bad(field, "JSON input exceeds 256 KiB")


def _shape(value, fields, required, field):
    if type(value) is not dict or not required <= value.keys() or not value.keys() <= fields:
        _bad(field, "unexpected or missing fields")


def _integer(value, field, low, high):
    if type(value) is not int or not low <= value <= high:
        _bad(field, f"expected an integer in {low}..{high}")
    return value


def _identifier(value, field):
    if type(value) is not str or not _ID.fullmatch(value):
        _bad(field, "invalid ID")
    return value


def _name(value, field):
    if type(value) is not str or not value.strip() or len(value) > 120:
        _bad(field, "expected a nonempty name of at most 120 characters")


def _date(value, field):
    if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        _bad(field, "expected YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise CalendarError(field, "invalid calendar date") from error


def _clock(value, field):
    if type(value) is not str or not _CLOCK.fullmatch(value):
        _bad(field, "expected HH:MM (00:00..23:59)")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _zone(name, first):
    if type(name) is not str or not name or len(name) > 128:
        _bad("timezone", "expected an IANA timezone")
    if name == "UTC":
        return dt_timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        # Windows need not install a new tzdata dependency for the modern JST
        # contract. Historical Tokyo offsets/DST require real timezone data.
        if name == "Asia/Tokyo" and first >= date(1952, 1, 1):
            return dt_timezone(timedelta(hours=9), name)
        raise CalendarError("timezone", "timezone data unavailable", code="unsupported") from error


def _local(target, clock, zone):
    naive = datetime.combine(target, time(clock // 60, clock % 60))
    choices = [naive.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
    try:
        valid = all(v.astimezone(dt_timezone.utc).astimezone(zone).replace(tzinfo=None) == naive for v in choices)
    except (OverflowError, ValueError) as error:
        raise CalendarError("date", "local time cannot be represented") from error
    if not valid:
        _bad("timezone", "nonexistent local time", code="unsupported")
    if choices[0].utcoffset() != choices[1].utcoffset():
        _bad("timezone", "ambiguous local time", code="unsupported")
    return choices[0]


def _elapsed(origin, instant):
    seconds = (instant.astimezone(dt_timezone.utc) - origin.astimezone(dt_timezone.utc)).total_seconds()
    if seconds % 60:
        _bad("timezone", "sub-minute timezone transitions are unsupported", code="unsupported")
    return int(seconds // 60)


def _period(first, last, zone):
    if first > last or (last - first).days >= MAX_DAYS:
        _bad("period", f"expected 1..{MAX_DAYS} consecutive calendar days")
    try:
        terminal = last + timedelta(days=1)
        origin = _local(first, 0, zone)
        boundaries = [_local(first + timedelta(days=i), 0, zone)
                      for i in range((last - first).days + 2)]
        # Offset-changing periods are explicitly unsupported, rather than
        # assigning duplicate/missing local clock labels to integer minutes.
        if any(point.utcoffset() != origin.utcoffset() for point in boundaries):
            _bad("timezone", "offset changes within the period are unsupported", code="unsupported")
        duration = _elapsed(origin, _local(terminal, 0, zone))
    except (OverflowError, ValueError) as error:
        if isinstance(error, CalendarError):
            raise
        raise CalendarError("period", "date cannot be represented at this timezone") from error
    return origin, duration


def _settings(value):
    _json(value, "settings")
    if type(value) is dict and "settings" in value:
        fields = {"schema_version", "world_revision", "settings", "id", "engine_supported", "execution_ready"}
        _shape(value, fields, fields, "design_version")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            _bad("design_version.schema_version", "expected design schema 1")
        if value["engine_supported"] is not False or value["execution_ready"] is not False:
            _bad("design_version", "expected the immutable PR9 design wrapper")
        if type(value["world_revision"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", value["world_revision"]):
            _bad("design_version.world_revision", "invalid revision hash")
        source = {key: value[key] for key in ("schema_version", "world_revision", "settings")}
        digest = hashlib.sha256(_canonical(source)).hexdigest()
        if value["id"] != digest:
            _bad("design_version.id", "immutable design hash mismatch", code="integrity")
        value = value["settings"]
    fields = {"schema_version", "period", "normal", "specials"}
    _shape(value, fields, fields, "settings")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _bad("schema_version", "expected PR9 design schema 1")
    _shape(value["period"], {"start_date", "end_date"}, {"start_date", "end_date"}, "period")
    _shape(value["normal"], {"bands", "weekdays", "holidays"}, {"bands", "weekdays", "holidays"}, "normal")
    return value


def _events(values, first, origin, zone, duration):
    values = [] if values is None else values
    _json(values, "scheduled_events")
    if type(values) is not list or len(values) > MAX_EVENTS:
        _bad("scheduled_events", "expected a bounded event list")
    output, ids = [], set()
    for i, event in enumerate(values):
        field = f"scheduled_events.{i}"
        if type(event) is not dict:
            _bad(field, "expected an event mapping")
        ident = _identifier(event.get("id"), field + ".id")
        if ident in ids:
            _bad(field + ".id", "duplicate event ID")
        ids.add(ident)
        target = _date(event["date"], field + ".date") if "date" in event else None
        if "day" in event:
            number = _integer(event["day"], field + ".day", 1, MAX_DAYS + 1)
            try:
                day_date = first + timedelta(days=number - 1)
            except OverflowError as error:
                raise CalendarError(field + ".day", "date out of range") from error
            if target is not None and target != day_date:
                _bad(field, "date and day disagree")
            target = day_date
        if target is None:
            _bad(field, "event requires date or one-based day")
        clocks = [_clock(event[key], field + "." + key) for key in ("time", "slot") if key in event]
        if not clocks or len(set(clocks)) != 1:
            _bad(field, "event requires consistent time/slot")
        if "timezone" in event and event["timezone"] != str(zone):
            _bad(field + ".timezone", "mixed event timezones are unsupported", code="unsupported")
        minute = _elapsed(origin, _local(target, clocks[0], zone))
        _integer(minute, field + ".minute", 0, duration)
        if "minute" in event and _integer(event["minute"], field + ".minute", 0, duration) != minute:
            _bad(field, "absolute minute disagrees with date/time")
        if "grants_item" in event:
            item = event["grants_item"]
            _shape(item, {"name", "count"}, {"name", "count"}, field + ".grants_item")
            _name(item["name"], field + ".grants_item.name")
            _integer(item["count"], field + ".grants_item.count", 1, 1000000000)
        output.append({**deepcopy(event), "minute": minute, "date": target.isoformat(),
                       "time": f"{clocks[0] // 60:02d}:{clocks[0] % 60:02d}",
                       "timezone": str(zone)})
    return sorted(output, key=lambda event: (event["minute"], event["id"]))


def compile_calendar(settings, *, timezone="Asia/Tokyo", scheduled_events=None):
    """Compile schema-1 PR9 settings (or an integrity-checked design version).

    Output schema 2 is JSON only. ``origin`` is an ISO midnight in ``timezone``;
    intervals/endpoints are real elapsed integer minutes. The final endpoint is
    the midnight following ``end_date``. DST-changing periods are rejected.
    """
    settings = _settings(settings)
    first = _date(settings["period"]["start_date"], "period.start_date")
    last = _date(settings["period"]["end_date"], "period.end_date")
    zone = _zone(timezone, first)
    origin, duration = _period(first, last, zone)
    events = _events(scheduled_events, first, origin, zone, duration)
    for event in events:
        event["timezone"] = timezone
    event_by_id = {event["id"]: event for event in events}
    definitions = 0

    def bands(values, field, source):
        nonlocal definitions
        if type(values) is not list or len(values) > MAX_BANDS:
            _bad(field, "expected a bounded band list")
        definitions += len(values)
        if definitions > MAX_BANDS:
            _bad(field, "at most 100 band definitions are supported")
        result = {}
        for i, value in enumerate(values):
            key = f"{field}.{i}"
            fields = {"id", "name", "start", "end", "overnight", "interval_minutes", "event_id"}
            _shape(value, fields, fields - {"event_id"}, key)
            ident = _identifier(value["id"], key + ".id")
            if ident in result:
                _bad(key, "duplicate band ID")
            _name(value["name"], key + ".name")
            if type(value["overnight"]) is not bool:
                _bad(key + ".overnight", "expected a boolean")
            interval = _integer(value["interval_minutes"], key + ".interval_minutes", 1, 1440)
            event_id = value.get("event_id", "")
            if type(event_id) is not str:
                _bad(key + ".event_id", "expected an event ID or empty string")
            if type(value["start"]) is not str:
                _bad(key + ".start", "expected a clock string")
            if event_id and value["start"]:
                _clock(value["start"], key + ".start")
            if event_id:
                if event_id not in event_by_id:
                    _bad(key + ".event_id", "existing event ID does not resolve")
                start = _clock(event_by_id[event_id]["time"], key + ".event_id")
            else:
                start = _clock(value["start"], key + ".start")
            end = _clock(value["end"], key + ".end")
            if end <= start:
                if not value["overnight"]:
                    _bad(key + ".overnight", "end at/before start requires overnight")
                end += 1440
            result[ident] = {"id": ident, "name": value["name"], "_start": start,
                             "_end": end, "interval_minutes": interval, "event_id": event_id,
                             "overnight": value["overnight"], "source_ids": [source]}
        return result

    normal = settings["normal"]
    base = bands(normal["bands"], "normal.bands", "normal")
    if not base:
        _bad("normal.bands", "at least one normal band is required")
    if type(normal["weekdays"]) is not dict or not normal["weekdays"].keys() <= set("0123456"):
        _bad("normal.weekdays", "expected weekday keys 0 (Monday)..6 (Sunday)")
    weekdays = {key: bands(value, "normal.weekdays." + key, "weekday:" + key)
                for key, value in normal["weekdays"].items()}
    if type(normal["holidays"]) is not list or len(normal["holidays"]) > 100:
        _bad("normal.holidays", "expected at most 100 holidays")
    holidays = {}
    for i, holiday in enumerate(normal["holidays"]):
        field = f"normal.holidays.{i}"
        _shape(holiday, {"date", "bands"}, {"date", "bands"}, field)
        target = _date(holiday["date"], field + ".date")
        if target in holidays or not first <= target <= last:
            _bad(field + ".date", "duplicate or out-of-period holiday")
        holidays[target] = bands(holiday["bands"], field + ".bands", f"holiday:{i}")
    if type(settings["specials"]) is not list or len(settings["specials"]) > 100:
        _bad("specials", "expected at most 100 special definitions")
    specials, ids = [], set()
    for i, special in enumerate(settings["specials"]):
        field = f"specials.{i}"
        fields = {"id", "name", "enabled", "selector", "priority", "bands"}
        _shape(special, fields, fields, field)
        ident = _identifier(special["id"], field + ".id")
        if ident in ids:
            _bad(field + ".id", "duplicate special ID")
        ids.add(ident)
        _name(special["name"], field + ".name")
        if type(special["enabled"]) is not bool:
            _bad(field + ".enabled", "expected a boolean")
        priority = _integer(special["priority"], field + ".priority", -10000, 10000)
        selector = special["selector"]
        _shape(selector, {"kind", "start", "end", "offset"}, {"kind"}, field + ".selector")
        # PR9 forms retain empty inactive fields when switching selector kind.
        # Preserve that shape, but do not accept malformed nonempty dates.
        for key in ("start", "end"):
            if key in selector and selector[key] != "":
                _date(selector[key], field + ".selector." + key)
        kind = selector["kind"]
        offset = _integer(selector.get("offset", 0), field + ".selector.offset", -MAX_DAYS, MAX_DAYS)
        if kind in ("date", "range"):
            start = _date(selector.get("start"), field + ".selector.start")
            end = _date(selector.get("end"), field + ".selector.end") if kind == "range" else start
        elif kind in ("first", "last"):
            try:
                start = end = (first if kind == "first" else last) + timedelta(days=offset)
            except OverflowError as error:
                raise CalendarError(field + ".selector", "relative date out of range") from error
        else:
            _bad(field + ".selector.kind", "expected date, range, first or last")
        if not first <= start <= end <= last:
            _bad(field + ".selector", "special range is outside the period or reversed")
        values = bands(special["bands"], field + ".bands", ident)
        if not values:
            _bad(field + ".bands", "at least one special band is required")
        specials.append((special["enabled"], start, end, priority, ident, values))

    days, previous_tails = [], []
    checkpoints = {0, duration}
    segments_count = 0
    for index in range((last - first).days + 1):
        target = first + timedelta(days=index)
        day_start = _elapsed(origin, _local(target, 0, zone))
        day_end = _elapsed(origin, _local(target + timedelta(days=1), 0, zone))
        checkpoints.update((day_start, day_end))
        resolved = {**base, **weekdays.get(str(target.weekday()), {}), **holidays.get(target, {})}
        choices = {}
        for enabled, start, end, priority, ident, values in specials:
            if enabled and start <= target <= end:
                for band_id, value in values.items():
                    choices.setdefault(band_id, []).append((priority, ident, value))
        for band_id, options in choices.items():
            highest = max(option[0] for option in options)
            winners = sorted((option for option in options if option[0] == highest), key=lambda option: option[1])
            signatures = {_canonical({key: option[2][key] for key in
                                      ("_start", "_end", "interval_minutes", "event_id", "overnight")})
                          for option in winners}
            if len(signatures) != 1:
                _bad(f"days.{target.isoformat()}.{band_id}", "same-priority band values conflict",
                     code="conflict", sources=[option[1] for option in winners])
            resolved[band_id] = {**winners[0][2], "source_ids": [option[1] for option in winners]}
        timed, current_spans = [], []
        for value in resolved.values():
            start = _elapsed(origin, _local(target, value["_start"], zone))
            end_date = target + timedelta(days=value["_end"] // 1440)
            requested_end = _elapsed(origin, _local(end_date, value["_end"] % 1440, zone))
            end = min(requested_end, duration)
            timed.append((value, start, end, requested_end))
            current_spans.append((start, end, target.isoformat(), value["id"], value["source_ids"]))
        combined = sorted(previous_tails + current_spans, key=lambda span: (span[0], span[1], span[3]))
        for previous, current in zip(combined, combined[1:]):
            if current[0] < previous[1]:
                _bad("bands", f"overlap: {previous[2]}/{previous[3]} and {current[2]}/{current[3]}",
                     code="overlap", sources=sorted(set(previous[4] + current[4])))
        previous_tails = [span for span in current_spans if span[1] > day_end]
        output = []
        for value, start, end, requested_end in timed:
            interval = value["interval_minutes"]
            segments = []
            for current in range(start, end, interval):
                segments_count += 1
                if segments_count > MAX_SEGMENTS:
                    _bad("bands", "calendar exceeds 100000 compiled segments", code="limit")
                stop = min(current + interval, end)
                checkpoints.update((current, stop))
                segments.append({"start_minute": current, "end_minute": stop,
                                 "duration_minutes": stop - current, "short": stop - current < interval})
            entry = {key: deepcopy(item) for key, item in value.items() if not key.startswith("_")}
            entry.update(start_minute=start, end_minute=end, requested_end_minute=requested_end,
                         clipped=end != requested_end, segments=segments,
                         remainder_minutes=(end - start) % interval)
            output.append(entry)
        output.sort(key=lambda band: (band["start_minute"], band["id"]))
        days.append({"date": target.isoformat(), "weekday": target.weekday(),
                     "source": sorted({source for band in output for source in band["source_ids"]}),
                     "bands": output})
    checkpoints.update(event["minute"] for event in events)
    return {"schema_version": 2, "timezone": timezone, "origin": origin.isoformat(),
            "start_date": first.isoformat(), "end_date": last.isoformat(),
            "duration_minutes": duration, "days": days, "checkpoints": sorted(checkpoints), "events": events}


@lru_cache(maxsize=128)
def _header_values(first_value, last_value, timezone_value, origin_value, duration_value):
    first = _date(first_value, "calendar.start_date")
    last = _date(last_value, "calendar.end_date")
    zone = _zone(timezone_value, first)
    origin, duration = _period(first, last, zone)
    if origin_value != origin.isoformat() or duration_value != duration:
        _bad("calendar", "compiled origin/duration mismatch")
    return zone, origin, duration


def _header(calendar):
    if type(calendar) is not dict or type(calendar.get("schema_version")) is not int or calendar["schema_version"] != 2:
        _bad("calendar", "expected a compiled schema-2 calendar")
    keys = ("start_date", "end_date", "timezone", "origin")
    if any(type(calendar.get(key)) is not str for key in keys) or type(calendar.get("duration_minutes")) is not int:
        _bad("calendar", "invalid compiled clock header")
    return _header_values(*(calendar[key] for key in keys), calendar["duration_minutes"])


def minute_for(calendar, date, clock):
    """Map a local date/HH:MM to elapsed minutes, including the terminal 00:00."""
    zone, origin, duration = _header(calendar)
    target = _date(date, "date")
    minute = _elapsed(origin, _local(target, _clock(clock, "clock"), zone))
    return _integer(minute, "minute", 0, duration)


def time_label(calendar, minute):
    """Return JSON {date, time, timezone} for a real elapsed integer minute."""
    zone, origin, duration = _header(calendar)
    _integer(minute, "minute", 0, duration)
    instant = (origin.astimezone(dt_timezone.utc) + timedelta(minutes=minute)).astimezone(zone)
    return {"date": instant.date().isoformat(), "time": instant.strftime("%H:%M"),
            "timezone": calendar["timezone"]}
