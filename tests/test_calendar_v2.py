"""Executable calendar boundaries, PR9 payload compatibility and refusal rules."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from engine import calendar_v2 as calendar


# Produced by the real PR9 archive_design() at ab93be195e603113e4686dbddba2c431cf5aced8
# using an isolated samples/route_goals/cultural_festival LibraryStore fixture.
# The producer branch is never imported, merged or required by these tests.
PR9_VERSION = {
    "schema_version": 1,
    "world_revision": "1aa0f2e9e33dafe7180b20d35012edcd19a6cab6c86b11d74cb99496f75e7333",
    "id": "0055c6f787cd1bd1d917e5fe8aa11fe915e0c119c8af2feb5d0910fb97f7ff7d",
    "engine_supported": False, "execution_ready": False,
    "settings": {
        "schema_version": 1,
        "period": {"start_date": "2026-10-12", "end_date": "2026-10-14"},
        "normal": {"bands": [{"id": "day", "name": "通常", "start": "09:00", "end": "17:00",
                              "overnight": False, "interval_minutes": 60, "event_id": ""}],
                   "weekdays": {}, "holidays": []}, "specials": [],
    },
}


def band(ident="day", start="09:00", end="17:00", interval=60, overnight=False, **extra):
    return {"id": ident, "name": ident, "start": start, "end": end,
            "interval_minutes": interval, "overnight": overnight, "event_id": "", **extra}


def special(ident, selector, bands, priority=10, enabled=True):
    return {"id": ident, "name": ident, "selector": selector,
            "priority": priority, "enabled": enabled, "bands": bands}


class CalendarV2Tests(unittest.TestCase):
    def setUp(self):
        self.settings = deepcopy(PR9_VERSION["settings"])

    def compile(self, **kwargs):
        return calendar.compile_calendar(self.settings, **kwargs)

    def test_actual_pr9_immutable_version_matches_direct_settings_and_is_not_mutated(self):
        version = deepcopy(PR9_VERSION)
        before = json.dumps(version, ensure_ascii=False, sort_keys=True)
        compiled = calendar.compile_calendar(version)
        self.assertEqual(compiled, calendar.compile_calendar(version["settings"]))
        self.assertEqual(json.dumps(version, ensure_ascii=False, sort_keys=True), before)
        self.assertEqual(json.loads(json.dumps(compiled, allow_nan=False)), compiled)
        self.assertEqual(compiled["schema_version"], 2)
        self.assertEqual(compiled["origin"], "2026-10-12T00:00:00+09:00")
        self.assertEqual(compiled["duration_minutes"], 4320)

    def test_immutable_version_hash_covers_settings_and_design_world_revision(self):
        for mutate in (
            lambda v: v["settings"]["normal"]["bands"][0].update(interval_minutes=30),
            lambda v: v.update(world_revision="0" * 64),
            lambda v: v.update(id="0" * 64),
            lambda v: v.update(execution_ready=True),
            lambda v: v.update(schema_version=True),
        ):
            version = deepcopy(PR9_VERSION)
            mutate(version)
            with self.subTest(version=version), self.assertRaises(calendar.CalendarError):
                calendar.compile_calendar(version)

    def test_origin_terminal_boundary_and_roundtrip_across_dates(self):
        compiled = self.compile()
        self.assertEqual(calendar.minute_for(compiled, "2026-10-12", "00:00"), 0)
        self.assertEqual(calendar.minute_for(compiled, "2026-10-13", "00:00"), 1440)
        self.assertEqual(calendar.minute_for(compiled, "2026-10-15", "00:00"), 4320)
        self.assertEqual(calendar.time_label(compiled, 4320),
                         {"date": "2026-10-15", "time": "00:00", "timezone": "Asia/Tokyo"})
        for minute in compiled["checkpoints"] + [1, 1439, 1441, 4319]:
            label = calendar.time_label(compiled, minute)
            self.assertEqual(calendar.minute_for(compiled, label["date"], label["time"]), minute)

    def test_real_weekday_holiday_and_band_id_inheritance(self):
        self.settings["normal"]["bands"] += [band("late", "18:00", "19:00")]
        self.settings["normal"]["weekdays"] = {"0": [band(interval=45)]}
        self.settings["normal"]["holidays"] = [{"date": "2026-10-12", "bands": [band(interval=120)]}]
        compiled = self.compile()
        monday = compiled["days"][0]
        self.assertEqual(monday["weekday"], 0)
        self.assertEqual(monday["bands"][0]["interval_minutes"], 120)
        self.assertEqual(monday["bands"][0]["source_ids"], ["holiday:0"])
        self.assertEqual(monday["bands"][1]["source_ids"], ["normal"])
        self.assertEqual(compiled["days"][1]["weekday"], 1)

    def test_leap_day_and_calendar_weekdays_do_not_use_day_number_modulo(self):
        self.settings["period"] = {"start_date": "2024-02-28", "end_date": "2024-03-01"}
        compiled = self.compile()
        self.assertEqual([d["date"] for d in compiled["days"]], ["2024-02-28", "2024-02-29", "2024-03-01"])
        self.assertEqual([d["weekday"] for d in compiled["days"]], [2, 3, 4])
        self.assertEqual(calendar.minute_for(compiled, "2024-03-01", "00:00"), 2880)

    def test_multiple_special_ranges_relative_offsets_and_per_band_priority(self):
        self.settings["specials"] = [
            special("first", {"kind": "first", "offset": 0}, [band(interval=15)]),
            special("range", {"kind": "range", "start": "2026-10-13", "end": "2026-10-14"}, [band(interval=120)]),
            special("last", {"kind": "last", "offset": 0}, [band(interval=30)], priority=20),
            special("late", {"kind": "last", "offset": -1}, [band("late", "18:00", "19:00")]),
        ]
        compiled = self.compile()
        self.assertEqual([d["bands"][0]["interval_minutes"] for d in compiled["days"]], [15, 120, 30])
        self.assertEqual([d["bands"][0]["source_ids"] for d in compiled["days"]], [["first"], ["range"], ["last"]])
        self.assertEqual(compiled["days"][1]["bands"][1]["id"], "late")

    def test_pr9_form_inactive_selector_fields_are_accepted(self):
        self.settings["specials"] = [special("last", {"kind": "last", "start": "", "end": "", "offset": 0}, [band(interval=30)])]
        self.assertEqual(self.compile()["days"][-1]["bands"][0]["interval_minutes"], 30)

    def test_same_priority_different_values_refuse_with_both_sources(self):
        self.settings["specials"] = [
            special("culture", {"kind": "last"}, [band(interval=60)]),
            special("last", {"kind": "last"}, [band(interval=30)]),
        ]
        with self.assertRaises(calendar.CalendarError) as caught:
            self.compile()
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(caught.exception.sources, ["culture", "last"])
        self.assertIn("2026-10-14", str(caught.exception))
        self.settings["specials"][1]["priority"] = 11
        self.assertEqual(self.compile()["days"][-1]["bands"][0]["source_ids"], ["last"])

    def test_same_priority_equal_values_merge_sources_without_row_order_winner(self):
        self.settings["specials"] = [special(ident, {"kind": "last"}, [band(name=ident)]) for ident in ("b", "a")]
        before = self.compile()
        self.settings["specials"].reverse()
        self.assertEqual(self.compile(), before)
        self.assertEqual(before["days"][-1]["bands"][0]["source_ids"], ["a", "b"])

    def test_disabled_valid_special_preserved_input_uses_normal(self):
        self.settings["specials"] = [special("off", {"kind": "last"}, [band(interval=30)], enabled=False)]
        before = deepcopy(self.settings)
        compiled = self.compile()
        self.assertEqual(compiled["days"][-1]["bands"][0]["source_ids"], ["normal"])
        self.assertEqual(self.settings, before)

    def test_uneven_intervals_preserve_the_short_final_segment(self):
        self.settings["normal"]["bands"] = [band(start="07:45", end="08:30", interval=20)]
        compiled = self.compile()
        value = compiled["days"][0]["bands"][0]
        self.assertEqual([s["duration_minutes"] for s in value["segments"]], [20, 20, 5])
        self.assertTrue(value["segments"][-1]["short"])
        self.assertEqual(value["segments"][-1]["end_minute"], 510)
        self.assertEqual(value["remainder_minutes"], 5)

    def test_overnight_absolute_minutes_and_final_day_clipping(self):
        self.settings["normal"]["bands"] = [band(start="22:00", end="06:00", interval=90, overnight=True)]
        compiled = self.compile()
        first, last = compiled["days"][0]["bands"][0], compiled["days"][-1]["bands"][0]
        self.assertEqual((first["start_minute"], first["end_minute"]), (1320, 1800))
        self.assertEqual(calendar.time_label(compiled, first["end_minute"])["date"], "2026-10-13")
        self.assertEqual(last["end_minute"], 4320)
        self.assertEqual(last["requested_end_minute"], 4680)
        self.assertTrue(last["clipped"])
        self.assertEqual([s["duration_minutes"] for s in last["segments"]], [90, 30])
        self.assertTrue(all(0 <= point <= 4320 for point in compiled["checkpoints"]))

    def test_previous_night_tail_overlaps_next_day_changed_weekday_band(self):
        self.settings["normal"]["bands"] = [band("night", "22:00", "06:00", overnight=True)]
        self.settings["normal"]["weekdays"] = {"1": [band("morning", "05:00", "07:00")]}
        with self.assertRaises(calendar.CalendarError) as caught:
            self.compile()
        self.assertEqual(caught.exception.code, "overlap")
        self.assertIn("2026-10-12/night", str(caught.exception))
        self.assertIn("2026-10-13/morning", str(caught.exception))

    def test_adjacent_band_boundaries_are_allowed_but_same_day_overlap_is_not(self):
        self.settings["normal"]["bands"] = [band("morning", "09:00", "12:00"), band("late", "12:00", "17:00")]
        self.assertEqual(len(self.compile()["days"][0]["bands"]), 2)
        self.settings["normal"]["bands"][1]["start"] = "11:59"
        with self.assertRaises(calendar.CalendarError) as caught:
            self.compile()
        self.assertEqual(caught.exception.code, "overlap")

    def test_overlap_is_identified_before_expanding_a_large_invalid_calendar(self):
        self.settings["period"] = {"start_date": "2026-01-01", "end_date": "2030-01-01"}
        self.settings["normal"]["bands"] = [band("a", interval=1), band("b", interval=1)]
        with patch.object(calendar, "MAX_SEGMENTS", 20):
            with self.assertRaises(calendar.CalendarError) as caught:
                self.compile()
        self.assertEqual(caught.exception.code, "overlap")

    def test_special_selector_and_priority_boolean_values_are_not_integers(self):
        for key, value in (("priority", True), ("priority", 1.5), ("enabled", 1)):
            self.settings["specials"] = [special("x", {"kind": "last"}, [band()])]
            self.settings["specials"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(calendar.CalendarError):
                self.compile()
        for offset in (True, 1.5, "0"):
            self.settings["specials"] = [special("x", {"kind": "last", "offset": offset}, [band()])]
            with self.subTest(offset=offset), self.assertRaises(calendar.CalendarError):
                self.compile()

    def test_equal_start_end_requires_explicit_overnight_and_represents_24_hours(self):
        self.settings["normal"]["bands"] = [band(start="06:00", end="06:00", overnight=True)]
        compiled = self.compile()
        self.assertEqual(compiled["days"][0]["bands"][0]["end_minute"], 1800)
        self.settings["normal"]["bands"][0]["overnight"] = False
        with self.assertRaises(calendar.CalendarError):
            self.compile()

    def test_scheduled_events_outside_windows_and_simultaneous_events_are_retained(self):
        events = [{"id": "b", "day": 1, "slot": "02:17", "label": "連絡"},
                  {"id": "a", "date": "2026-10-12", "time": "02:17", "targets": []}]
        before = deepcopy(events)
        compiled = self.compile(scheduled_events=events)
        self.assertEqual([e["id"] for e in compiled["events"]], ["a", "b"])
        self.assertEqual([e["minute"] for e in compiled["events"]], [137, 137])
        self.assertIn(137, compiled["checkpoints"])
        self.assertEqual(compiled["checkpoints"].count(137), 1)
        self.assertEqual(events, before)

    def test_event_reference_uses_existing_slot_and_updates_without_duplicate_time(self):
        self.settings["normal"]["bands"] = [band(start="", event_id="opening")]
        events = [{"id": "opening", "day": 2, "slot": "15:00", "label": "開演"}]
        compiled = self.compile(scheduled_events=events)
        self.assertEqual(compiled["days"][0]["bands"][0]["start_minute"], 900)
        self.assertEqual(compiled["events"][0]["minute"], 2340)
        events[0]["slot"] = "14:30"
        changed = self.compile(scheduled_events=events)
        self.assertEqual(changed["days"][0]["bands"][0]["start_minute"], 870)
        self.assertEqual(changed["events"][0]["minute"], 2310)
        self.assertNotIn("start", changed["days"][0]["bands"][0])

    def test_31_calendar_days_have_30_preparation_days_and_explicit_festival_day31(self):
        self.settings["period"] = {"start_date": "2026-10-01", "end_date": "2026-10-31"}
        self.settings["specials"] = [special("festival", {"kind": "last"}, [band(start="06:00", end="17:00", interval=120)])]
        events = [{"id": "play-opening", "day": 31, "slot": "15:00"}]
        compiled = self.compile(scheduled_events=events)
        self.assertEqual(len(compiled["days"]), 31)
        self.assertEqual(compiled["duration_minutes"], 31 * 1440)
        self.assertEqual(compiled["days"][0]["weekday"], 3)  # Actual Thursday.
        self.assertEqual(compiled["days"][-1]["weekday"], 5)  # Actual Saturday.
        opening = 30 * 1440 + 900
        self.assertEqual(compiled["events"][0]["minute"], opening)
        self.assertIn(opening, compiled["checkpoints"])
        self.assertEqual(compiled["days"][-1]["bands"][0]["source_ids"], ["festival"])
        # The legacy day=1 event is not silently rebased onto festival day31.
        events[0]["day"] = 1
        self.assertEqual(self.compile(scheduled_events=events)["events"][0]["minute"], 900)

    def test_ten_minute_move_is_independent_of_display_interval(self):
        for interval in (1, 30, 60, 120, 1440):
            self.settings["normal"]["bands"][0]["interval_minutes"] = interval
            compiled = self.compile()
            departure = calendar.minute_for(compiled, "2026-10-14", "14:50")
            self.assertEqual(calendar.time_label(compiled, departure + 10)["time"], "15:00")
            self.assertEqual(calendar.minute_for(compiled, "2026-10-14", "15:00") - departure, 10)

    def test_invalid_dates_empty_period_and_out_of_range_relative_dates_refuse(self):
        for value in ("", None, True, "2026-02-30", "2026-1-01", "2026-10-12T00:00"):
            with self.subTest(value=value):
                settings = deepcopy(self.settings)
                settings["period"]["start_date"] = value
                with self.assertRaises(calendar.CalendarError): calendar.compile_calendar(settings)
        self.settings["specials"] = [special("outside", {"kind": "last", "offset": 1}, [band()])]
        with self.assertRaises(calendar.CalendarError): self.compile()

    def test_invalid_quantities_booleans_ids_clocks_and_structures_refuse(self):
        for interval in (0, -1, 1441, 1.5, True, "60", None):
            settings = deepcopy(self.settings)
            settings["normal"]["bands"][0]["interval_minutes"] = interval
            with self.subTest(interval=interval), self.assertRaises(calendar.CalendarError):
                calendar.compile_calendar(settings)
        for key, value in (("id", ""), ("id", "../id"), ("name", " "), ("start", "24:00"),
                           ("end", "08:60"), ("overnight", 1), ("event_id", False)):
            settings = deepcopy(self.settings)
            settings["normal"]["bands"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(calendar.CalendarError):
                calendar.compile_calendar(settings)
        for value in (True, 1.0, 2):
            settings = deepcopy(self.settings); settings["schema_version"] = value
            with self.subTest(version=value), self.assertRaises(calendar.CalendarError):
                calendar.compile_calendar(settings)

    def test_duplicate_ids_unknown_fields_reversed_dates_and_nonfinite_json_refuse(self):
        cases = []
        s = deepcopy(self.settings); s["normal"]["bands"] *= 2; cases.append(s)
        s = deepcopy(self.settings); s["normal"]["unexpected"] = 1; cases.append(s)
        s = deepcopy(self.settings); s["period"]["end_date"] = "2026-10-11"; cases.append(s)
        s = deepcopy(self.settings); s["normal"]["bands"] = []; cases.append(s)
        for value in (float("nan"), float("inf")):
            s = deepcopy(self.settings); s["normal"]["bands"][0]["interval_minutes"] = value; cases.append(s)
        for s in cases:
            with self.subTest(settings=s), self.assertRaises(calendar.CalendarError): calendar.compile_calendar(s)

    def test_bad_event_id_dates_duplicate_ids_and_quantity_refuse(self):
        cases = [
            [{"id": "x", "day": True, "slot": "15:00"}],
            [{"id": "x", "day": 1, "date": "2026-10-13", "slot": "15:00"}],
            [{"id": "x", "day": 1, "time": "15:00", "slot": "16:00"}],
            [{"id": "x", "day": 4, "slot": "00:01"}],
            [{"id": "x", "day": 1, "slot": "昼"}],
            [{"id": "x", "day": 1, "slot": "15:00"}] * 2,
        ]
        for quantity in (0, -1, True, 1.5):
            cases.append([{"id": "x", "day": 1, "slot": "15:00", "grants_item": {"name": "素材", "count": quantity}}])
        for events in cases:
            with self.subTest(events=events), self.assertRaises(calendar.CalendarError): self.compile(scheduled_events=events)
        self.settings["normal"]["bands"][0]["event_id"] = "missing"
        with self.assertRaises(calendar.CalendarError): self.compile()

    def test_event_at_terminal_midnight_is_processed(self):
        compiled = self.compile(scheduled_events=[{"id": "close", "day": 4, "slot": "00:00"}])
        self.assertEqual(compiled["events"][0]["minute"], compiled["duration_minutes"])

    def test_public_clock_helpers_refuse_bool_float_outside_and_corrupt_header(self):
        compiled = self.compile()
        for minute in (True, 1.0, -1, 4321):
            with self.subTest(minute=minute), self.assertRaises(calendar.CalendarError): calendar.time_label(compiled, minute)
        for target, clock in (("2026-10-11", "23:59"), ("2026-10-15", "00:01"), ("2026-10-12", "24:00")):
            with self.subTest(target=target, clock=clock), self.assertRaises(calendar.CalendarError): calendar.minute_for(compiled, target, clock)
        for field, value in (("duration_minutes", True), ("origin", "2026-10-12T01:00:00+09:00"), ("schema_version", True)):
            corrupt = deepcopy(compiled); corrupt[field] = value
            with self.subTest(field=field), self.assertRaises(calendar.CalendarError): calendar.time_label(corrupt, 0)

    def test_bounds_reject_oversized_period_definitions_and_segment_expansion(self):
        self.settings["period"] = {"start_date": "2026-01-01", "end_date": "2037-01-01"}
        with self.assertRaises(calendar.CalendarError): self.compile()
        self.settings = deepcopy(PR9_VERSION["settings"])
        self.settings["specials"] = [special(f"s{i}", {"kind": "first"}, [band()]) for i in range(100)]
        with self.assertRaises(calendar.CalendarError): self.compile()
        self.settings = deepcopy(PR9_VERSION["settings"])
        self.settings["normal"]["bands"] = [band(start="00:00", end="00:00", interval=1, overnight=True)]
        with patch.object(calendar, "MAX_SEGMENTS", 20):
            with self.assertRaises(calendar.CalendarError) as caught: self.compile()
        self.assertEqual(caught.exception.code, "limit")

    def test_modern_tokyo_works_without_new_timezone_dependency(self):
        with patch.object(calendar, "ZoneInfo", side_effect=ZoneInfoNotFoundError("missing")):
            compiled = self.compile()
            self.assertEqual(compiled["origin"], "2026-10-12T00:00:00+09:00")
            self.assertEqual(calendar.minute_for(compiled, "2026-10-13", "00:00"), 1440)

    def test_utc_and_unknown_timezone(self):
        self.assertEqual(self.compile(timezone="UTC")["origin"], "2026-10-12T00:00:00+00:00")
        with self.assertRaises(calendar.CalendarError): self.compile(timezone="Unknown/Zone")

    def test_dst_changing_periods_are_explicitly_unsupported(self):
        try:
            ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError:
            self.skipTest("optional non-Tokyo timezone data is unavailable")
        for target in ("2026-03-08", "2026-11-01"):
            self.settings["period"] = {"start_date": target, "end_date": target}
            with self.subTest(target=target), self.assertRaises(calendar.CalendarError) as caught:
                self.compile(timezone="America/New_York")
            self.assertEqual(caught.exception.code, "unsupported")


if __name__ == "__main__":
    unittest.main()
