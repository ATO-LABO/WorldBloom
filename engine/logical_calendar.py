"""Date labels for the ordinary logical day/slot clock.

These dates never define durations, turn boundaries or action restrictions.
``weekend_days`` uses Monday=0 .. Sunday=6. Public labels use ISO weekdays
Monday=1 .. Sunday=7, with Japanese weekday names.
"""
from __future__ import annotations

from datetime import date, timedelta
import re

_VERSION = 1
_MAX_DAYS = 10000
_FIELDS = {"version", "start_date", "end_date", "weekend_days", "holidays"}
_WEEKDAY_NAMES = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


def _date(value, field):
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise ValueError(f"time.date_calendar.{field}: expected YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"time.date_calendar.{field}: invalid calendar date") from error


def _range(start_date, end_date):
    start = _date(start_date, "start_date")
    end = _date(end_date, "end_date")
    count = (end - start).days + 1
    if not 1 <= count <= _MAX_DAYS:
        raise ValueError("time.date_calendar: expected 1..10000 inclusive calendar days")
    return start, end, count


def normalize_date_calendar(raw, days):
    """Return a validated fresh v1 definition, or None for an absent calendar.

    All five v1 fields are required. Lists are sorted but duplicate entries are
    rejected. ``days`` must exactly match the inclusive authored date range.
    An absent calendar leaves existing logical-world validation unchanged.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != _FIELDS:
        raise ValueError("time.date_calendar: expected exactly the five v1 fields")
    if type(raw["version"]) is not int or raw["version"] != _VERSION:
        raise ValueError("time.date_calendar.version: expected version 1")
    start, end, count = _range(raw["start_date"], raw["end_date"])
    if type(days) is not int or not 1 <= days <= _MAX_DAYS or days != count:
        raise ValueError("time.days: must match the inclusive date_calendar range (1..10000)")
    weekend = raw["weekend_days"]
    if (not isinstance(weekend, list) or len(weekend) > 7
            or any(type(value) is not int or not 0 <= value <= 6 for value in weekend)
            or len(set(weekend)) != len(weekend)):
        raise ValueError("time.date_calendar.weekend_days: distinct Monday=0..Sunday=6 integers required")
    holidays = raw["holidays"]
    if not isinstance(holidays, list) or len(holidays) > count:
        raise ValueError("time.date_calendar.holidays: expected a bounded date list")
    parsed = [_date(value, "holidays") for value in holidays]
    if len(set(parsed)) != len(parsed):
        raise ValueError("time.date_calendar.holidays: duplicate date")
    if any(not start <= value <= end for value in parsed):
        raise ValueError("time.date_calendar.holidays: date outside the authored range")
    return {"version": _VERSION, "start_date": start.isoformat(), "end_date": end.isoformat(),
            "weekend_days": sorted(weekend), "holidays": sorted(value.isoformat() for value in parsed)}


def date_calendar_for_range(start, end, existing=None):
    """Create a v1 date definition, preserving reviewed weekend/holiday choices.

    The default weekend is Saturday/Sunday, with no automatically fetched
    holidays. An existing holiday outside the new range raises instead of being
    silently deleted or shifted. The caller derives ``time.days`` from the same
    inclusive date range; it is deliberately not a second field in this object.
    """
    _, _, days = _range(start, end)
    if existing is None:
        weekend, holidays = [5, 6], []
    else:
        if not isinstance(existing, dict):
            raise ValueError("time.date_calendar: expected a v1 mapping")
        _, _, old_days = _range(existing.get("start_date"), existing.get("end_date"))
        prior = normalize_date_calendar(existing, old_days)
        weekend, holidays = prior["weekend_days"], prior["holidays"]
    return normalize_date_calendar({"version": _VERSION, "start_date": start, "end_date": end,
                                    "weekend_days": weekend, "holidays": holidays}, days)


class LogicalDateLabels:
    """Validated per-world date labels with O(1) lookup and no mutable aliases.

    Construct once from the authored time definition before a simulation or GA
    rollout. Later input mutations cannot change this compiled labeling context;
    a new world definition requires a new instance.
    """
    __slots__ = ("_start", "_days", "_holidays", "_weekend")

    def __init__(self, time_definition):
        self._start = None
        self._days = 0
        self._holidays = frozenset()
        self._weekend = frozenset()
        if time_definition is None:
            return
        if not isinstance(time_definition, dict):
            raise ValueError("time: expected a mapping")
        raw = time_definition.get("date_calendar")
        if raw is None:
            return
        calendar = normalize_date_calendar(raw, time_definition.get("days"))
        self._start = date.fromisoformat(calendar["start_date"])
        self._days = time_definition["days"]
        self._holidays = frozenset(calendar["holidays"])
        self._weekend = frozenset(calendar["weekend_days"])

    def label(self, day):
        """Return an independent label; ISO weekday is Monday=1..Sunday=7.

        The non-working flag labels the date only; it forbids no action.
        An absent date calendar returns {} without changing legacy validation.
        """
        if self._start is None:
            return {}
        if type(day) is not int or not 1 <= day <= self._days:
            raise ValueError("day: expected a logical day within time.days")
        value = self._start + timedelta(days=day - 1)
        iso = value.isoformat()
        weekend = value.weekday() in self._weekend
        holiday = iso in self._holidays
        return {"date": iso, "weekday": value.isoweekday(),
                "weekday_name": _WEEKDAY_NAMES[value.weekday()], "is_weekend": weekend,
                "is_holiday": holiday, "is_non_working_day": weekend or holiday}


def date_label(time_definition, day):
    """One-off label API; use LogicalDateLabels once for repeated runtime lookup.

    Returns {} when date_calendar is absent/None. Otherwise returns ``date``,
    ``weekday`` (ISO 1..7), ``weekday_name`` (Japanese full weekday), and boolean
    ``is_weekend``, ``is_holiday``, ``is_non_working_day``. The final flag is only
    a label; it does not prohibit work, movement or any other action.
    """
    return LogicalDateLabels(time_definition).label(day)
