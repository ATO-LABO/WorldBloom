"""Pure logical-day date labels: no simulation, I/O or external holiday service."""
from copy import deepcopy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from engine.logical_calendar import normalize_date_calendar, date_label, date_calendar_for_range, LogicalDateLabels


def definition(start="2026-11-02", end="2026-11-08", **values):
    return {"version": 1, "start_date": start, "end_date": end,
            "weekend_days": [5, 6], "holidays": [], **values}


class LogicalCalendarTests(unittest.TestCase):
    def test_absent_calendar_preserves_legacy_world(self):
        self.assertIsNone(normalize_date_calendar(None, None))
        for value in [None, {}, {"days": 3, "slots": ["morning", "night"]}, {"date_calendar": None}]:
            self.assertEqual(date_label(value, 1), {})

    def test_exact_iso_dates_reject_noncanonical_or_impossible_values(self):
        for invalid in ["2026-1-02", "2026-11-2", "2026-02-29", "2026-13-01", "0000-01-01",
                        "10000-01-01", "２０２６-11-02", " 2026-11-02", "2026-11-02T00:00:00", date(2026, 11, 2), None]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_date_calendar(definition(start=invalid), 7)

    def test_strict_v1_shape_and_version(self):
        for invalid in [{}, [], "calendar", {**definition(), "timezone": "UTC"},
                        {k: v for k, v in definition().items() if k != "holidays"},
                        definition(version=True), definition(version=2), definition(version=1.0)]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_date_calendar(invalid, 7)

    def test_inclusive_range_must_match_days_and_integer_bounds(self):
        self.assertEqual(normalize_date_calendar(definition(), 7)["end_date"], "2026-11-08")
        for invalid in [True, 7.0, "7", 0, 6, 8, 10001, None]:
            with self.subTest(days=invalid), self.assertRaises(ValueError):
                normalize_date_calendar(definition(), invalid)
        with self.assertRaises(ValueError):
            date_calendar_for_range("2026-11-08", "2026-11-02")

    def test_year_edges_and_exact_ten_thousand_day_bound(self):
        for value in ["0001-01-01", "9999-12-31"]:
            calendar = date_calendar_for_range(value, value)
            self.assertEqual(date_label({"days": 1, "date_calendar": calendar}, 1)["date"], value)
        start = date(2000, 1, 1)
        last = (start + timedelta(days=9999)).isoformat()
        calendar = date_calendar_for_range(start.isoformat(), last)
        self.assertEqual(date_label({"days": 10000, "date_calendar": calendar}, 10000)["date"], last)
        with self.assertRaises(ValueError):
            date_calendar_for_range(start.isoformat(), (start + timedelta(days=10000)).isoformat())

    def test_leap_day_and_year_rollover_are_one_day_each(self):
        for start, end, expected in [("2024-02-28", "2024-03-01", ["2024-02-28", "2024-02-29", "2024-03-01"]),
                                     ("2026-12-31", "2027-01-02", ["2026-12-31", "2027-01-01", "2027-01-02"])]:
            time = {"days": 3, "date_calendar": date_calendar_for_range(start, end)}
            self.assertEqual([date_label(time, day)["date"] for day in [1, 2, 3]], expected)

    def test_weekday_is_iso_and_weekends_use_zero_based_config(self):
        time = {"days": 7, "date_calendar": definition()}
        labels = [date_label(time, day) for day in range(1, 8)]
        self.assertEqual([label["weekday"] for label in labels], list(range(1, 8)))
        self.assertEqual([label["weekday_name"] for label in labels], ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"])
        self.assertEqual([label["is_weekend"] for label in labels], [False] * 5 + [True, True])
        self.assertFalse(any(label["is_holiday"] for label in labels))
        self.assertEqual([label["is_non_working_day"] for label in labels], [False] * 5 + [True, True])

    def test_custom_weekend_and_holiday_are_distinct(self):
        time = {"days": 7, "date_calendar": definition(weekend_days=[4], holidays=["2026-11-03", "2026-11-06"])}
        holiday = date_label(time, 2)
        self.assertTrue(holiday["is_holiday"])
        self.assertFalse(holiday["is_weekend"])
        self.assertTrue(holiday["is_non_working_day"])
        both = date_label(time, 5)
        self.assertTrue(both["is_holiday"] and both["is_weekend"])
        self.assertFalse(date_label(time, 6)["is_non_working_day"])
        no_weekends = {"days": 7, "date_calendar": definition(weekend_days=[])}
        self.assertFalse(any(date_label(no_weekends, day)["is_non_working_day"] for day in range(1, 8)))

    def test_invalid_weekend_definitions(self):
        for value in [[-1], [7], [True], [1.0], ["5"], [5, 5], [0] * 8, None, {5, 6}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_date_calendar(definition(weekend_days=value), 7)

    def test_holidays_must_be_unique_canonical_and_in_range(self):
        for value in [["2026-11-03", "2026-11-03"], ["2026-11-01"], ["2026-11-09"],
                      ["2026-11-3"], [None], "2026-11-03", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_date_calendar(definition(holidays=value), 7)

    def test_normalization_is_deterministic_and_never_mutates_input(self):
        raw = definition(weekend_days=[6, 5], holidays=["2026-11-08", "2026-11-03"])
        before = deepcopy(raw)
        normalized = normalize_date_calendar(raw, 7)
        self.assertEqual(raw, before)
        self.assertEqual(normalized["weekend_days"], [5, 6])
        self.assertEqual(normalized["holidays"], ["2026-11-03", "2026-11-08"])
        normalized["holidays"].clear()
        self.assertEqual(raw, before)

    def test_range_helper_retains_reviewed_holidays_and_weekends(self):
        prior = definition(weekend_days=[4], holidays=["2026-11-03"])
        before = deepcopy(prior)
        changed = date_calendar_for_range("2026-11-01", "2026-11-10", prior)
        self.assertEqual(changed["weekend_days"], [4])
        self.assertEqual(changed["holidays"], ["2026-11-03"])
        self.assertEqual(prior, before)
        with self.assertRaises(ValueError):
            date_calendar_for_range("2026-11-04", "2026-11-10", prior)
        self.assertEqual(prior, before)

    def test_range_helper_rejects_malformed_existing_config(self):
        for prior in [[], {}, definition(version=2), definition(holidays=["2026-12-01"])]:
            with self.subTest(prior=prior), self.assertRaises(ValueError):
                date_calendar_for_range("2026-11-01", "2026-11-10", prior)

    def test_label_rejects_non_integer_and_outside_days(self):
        time = {"days": 7, "date_calendar": definition()}
        for day in [True, 1.0, "1", 0, -1, 8, None]:
            with self.subTest(day=day), self.assertRaises(ValueError):
                date_label(time, day)
        with self.assertRaises(ValueError):
            date_label({"days": 8, "date_calendar": definition()}, 1)

    def test_compiled_labels_validate_once_and_snapshot_mutable_inputs(self):
        time = {"days": 7, "date_calendar": definition(holidays=["2026-11-03"])}
        with patch("engine.logical_calendar.normalize_date_calendar", wraps=normalize_date_calendar) as normalize:
            labels = LogicalDateLabels(time)
            self.assertEqual(normalize.call_count, 1)
            expected = labels.label(2)
            # A long-lived World may share the original mapping with UI fixtures;
            # labeling must keep its reviewed initial values, not alias them.
            time["days"] = 1
            time["date_calendar"]["start_date"] = "2027-01-01"
            time["date_calendar"]["holidays"].clear()
            time["date_calendar"]["weekend_days"].append(1)
            for _ in range(100):
                current = labels.label(2)
                self.assertEqual(current, expected)
                current["date"] = "mutated output"
            self.assertEqual(normalize.call_count, 1)
            self.assertEqual(labels.label(2)["date"], "2026-11-03")
            self.assertTrue(labels.label(2)["is_holiday"])
            self.assertFalse(labels.label(2)["is_weekend"])
            self.assertEqual(labels.label(7)["date"], "2026-11-08")
            for day in [0, 8, True, 2.0]:
                with self.subTest(day=day), self.assertRaises(ValueError):
                    labels.label(day)
        self.assertEqual(LogicalDateLabels({"days": 3}).label(None), {})

    def test_labels_do_not_change_presentation_slots_or_other_world_values(self):
        time = {"days": 7, "slots": ["morning", "noon", "night"], "presentation": {"calendar": "real"},
                "date_calendar": definition(holidays=["2026-11-03"]), "authored": {"keep": True}}
        before = deepcopy(time)
        label = date_label(time, 2)
        self.assertEqual(set(label), {"date", "weekday", "weekday_name", "is_weekend", "is_holiday", "is_non_working_day"})
        self.assertEqual(time, before)
        # This module has no slot, action, timestep or holiday restriction output.
        self.assertNotIn("slots", label)
        self.assertNotIn("allowed_actions", label)


if __name__ == "__main__":
    unittest.main()
