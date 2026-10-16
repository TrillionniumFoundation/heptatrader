"""Pure contract tests; no IB connection, historical data, or live state."""
from datetime import datetime, timedelta, timezone
import unittest

from research.historical_training.derive_ib_schedule_window import (
    utc_epoch, validate_window)
from research.historical_training.common import EvidenceError


class DerivedIBScheduleWindowTests(unittest.TestCase):
    def fixture(self):
        start = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
        epoch = int(start.timestamp())
        bars = [{"time": epoch + 300 * i,
                 "bid": {"close": "1.100"},
                 "ask": {"close": "1.101"}} for i in range(120)]
        old = list(bars[:30])
        schedule = {
            "timezone": "UTC",
            "start": "20261014-09:30:00",
            "end": "20261014-20:30:00",
            "sessions": [{
                "start": "20261014-10:00:00",
                "end": "20261014-20:00:00",
            }]
        }
        return bars, old, schedule, epoch, epoch + 120 * 300

    def test_complete_schedule_and_exact_overlap(self):
        bars, old, schedule, start, end = self.fixture()
        subset, expected, overlap, added, header_a, header_b = validate_window(
            bars, old, schedule, start, end)
        self.assertEqual(len(subset), 120)
        self.assertEqual(len(expected), 120)
        self.assertEqual(len(overlap), 30)
        self.assertEqual(len(added), 90)
        self.assertLess(header_a, start)
        self.assertGreater(header_b, end)

    def test_missing_expected_bar_is_rejected(self):
        bars, old, schedule, start, end = self.fixture()
        bars.pop(80)
        with self.assertRaisesRegex(EvidenceError, "DERIVED_EXPECTED_SCHEDULE_MISMATCH"):
            validate_window(bars, old, schedule, start, end)

    def test_revised_overlap_is_rejected(self):
        bars, old, schedule, start, end = self.fixture()
        old[5] = {**old[5], "bid": {"close": "1.098"}}
        with self.assertRaisesRegex(EvidenceError, "DERIVED_OVERLAP_OR_APPEND_MISMATCH"):
            validate_window(bars, old, schedule, start, end)

    def test_outside_header_is_rejected(self):
        bars, old, schedule, start, end = self.fixture()
        with self.assertRaisesRegex(EvidenceError, "DERIVED_INTERVAL_OUTSIDE_RETURNED_BROKER_HEADER"):
            validate_window(bars, old, schedule, start - 3600, end)

    def test_schedule_missing_tail_is_rejected(self):
        bars, old, schedule, start, end = self.fixture()
        schedule["sessions"][0]["end"] = "20261014-19:55:00"
        with self.assertRaisesRegex(EvidenceError, "DERIVED_EXPECTED_SCHEDULE_MISMATCH"):
            validate_window(bars, old, schedule, start, end)

    def test_duplicate_bar_is_rejected(self):
        bars, old, schedule, start, end = self.fixture()
        bars.append(dict(bars[50]))
        with self.assertRaisesRegex(EvidenceError, "DERIVED_SUBWINDOW_TIME_OR_SIZE"):
            validate_window(bars, old, schedule, start, end)

    def test_utc_parser_rejects_naive_and_non_utc(self):
        self.assertEqual(utc_epoch("2026-10-14T10:00:00Z"),
                         utc_epoch("2026-10-14T10:00:00+00:00"))
        for value in ("2026-10-14T10:00:00", "2026-10-14T10:00:00+02:00"):
            with self.assertRaisesRegex(EvidenceError, "EXPLICIT_UTC_REQUIRED"):
                utc_epoch(value)

    def test_future_window_is_rejected(self):
        bars, old, schedule, start, end = self.fixture()
        with self.assertRaisesRegex(EvidenceError, "DERIVED_HISTORICAL_WINDOW_ONLY"):
            validate_window(bars, old, schedule, start, 3_000_000_000)


if __name__ == "__main__":
    unittest.main()
