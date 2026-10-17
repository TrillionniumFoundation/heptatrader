"""Synthetic calendar boundaries; no market or model performance is simulated."""
import copy
from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo
from laya_cell.resident_plan_calendar import calendar_admission

NY = ZoneInfo("America/New_York")
MINUTE = 60000
LENGTH = 558 * MINUTE


def ny(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=NY).timestamp() * 1000)


def fixture(now, deadline, length=LENGTH):
    candidate = {"created_at_ms": now - 1000, "trained_until_ms": now - 2000,
                 "window": {"training_end_ms": now - 3000,
                            "validation_start_ms": now - 500,
                            "validation_end_ms": deadline}}
    plan = {"created_at_ms": now, "start_ms": now + MINUTE,
            "outcome_end_ms": now + MINUTE + length,
            "rule": {"slots": 93, "min_observed": 91, "max_censored": 2}}
    return candidate, plan


class CalendarAdmissionTests(unittest.TestCase):
    def test_saturday_with_47_minutes_after_sunday_open_has_no_trial(self):
        now = ny("2026-10-17T18:00:00")
        c, p = fixture(now, ny("2026-10-18T18:02:00"))
        r = calendar_admission(c, p, now)
        self.assertEqual(r["state"], "NO_FORWARD_TRIAL")
        self.assertIsNone(r["earliest_feasible_start_ms"])

    def test_future_session_fits_so_saturday_only_defers(self):
        now = ny("2026-10-17T18:00:00")
        c, p = fixture(now, ny("2026-10-19T12:00:00"))
        r = calendar_admission(c, p, now)
        self.assertEqual(r["state"], "DEFERRED")
        self.assertEqual(r["earliest_feasible_start_ms"], ny("2026-10-18T17:16:00"))

    def test_open_endpoints_do_not_hide_internal_daily_rollover(self):
        now = ny("2026-10-19T08:00:00")
        c, p = fixture(now, ny("2026-10-20T12:00:00"))
        r = calendar_admission(c, p, now)
        self.assertEqual(r["state"], "DEFERRED")
        self.assertEqual(r["earliest_feasible_start_ms"], ny("2026-10-19T17:16:00"))
        close = ny("2026-10-19T17:00:00")
        c, p = fixture(now, close, close - now - MINUTE)
        self.assertEqual(calendar_admission(c, p, now)["state"], "NO_FORWARD_TRIAL")
        p["outcome_end_ms"] -= 1
        self.assertTrue(calendar_admission(c, p, now)["ready"])

    def test_open_monday_has_a_real_feasible_interval(self):
        now = ny("2026-10-19T06:00:00")
        c, p = fixture(now, ny("2026-10-19T16:00:00"))
        self.assertTrue(calendar_admission(c, p, now)["ready"])
        c, p = fixture(now, ny("2026-10-19T16:00:00"), 60 * MINUTE)
        c["window"]["validation_start_ms"] = ny("2026-10-19T09:00:00")
        r = calendar_admission(c, p, now)
        self.assertEqual(r["state"], "DEFERRED")
        self.assertEqual(r["earliest_feasible_start_ms"], ny("2026-10-19T09:00:00"))

    def test_new_york_dst_controls_utc_opening(self):
        cases = [
            ("2026-10-24T18:00:00", "2026-10-26T12:00:00", "2026-10-25T21:16:00+00:00"),
            ("2026-10-31T18:00:00", "2026-11-02T12:00:00", "2026-11-01T22:16:00+00:00"),
            ("2026-03-07T18:00:00", "2026-03-09T12:00:00", "2026-03-08T21:16:00+00:00"),
        ]
        for start, end, expected in cases:
            with self.subTest(start=start):
                now = ny(start)
                c, p = fixture(now, ny(end))
                r = calendar_admission(c, p, now)
                actual = datetime.fromtimestamp(r["earliest_feasible_start_ms"] / 1000, timezone.utc)
                self.assertEqual(actual.isoformat(), expected)

    def test_scores_never_change_timing_decisions_or_inputs(self):
        now = ny("2026-10-19T06:00:00")
        c, p = fixture(now, ny("2026-10-19T16:00:00"))
        original = copy.deepcopy((c, p))
        expected = calendar_admission(c, p, now)
        self.assertEqual((c, p), original)
        for score in (-1e12, 0, 1e12, float("nan")):
            changed = {**c, "measurement": {"pnl": score, "nll": score}, "loss": score}
            self.assertEqual(calendar_admission(changed, p, now), expected)

    def test_expired_candidate_remains_an_untried_training_success(self):
        now = ny("2026-10-19T06:00:00")
        c, p = fixture(now, now - 1)
        r = calendar_admission(c, p, now)
        self.assertEqual(r["state"], "NO_FORWARD_TRIAL")
        self.assertEqual(r["reason"], "CANDIDATE_WINDOW_EXPIRED")
        self.assertFalse(r["future_data_availability_proven"])

    def test_invalid_or_unbounded_inputs_fail(self):
        now = ny("2026-10-19T06:00:00")
        c, p = fixture(now, ny("2026-10-19T16:00:00"))
        mutations = [
            lambda c, p: c.update(created_at_ms=True),
            lambda c, p: p.update(created_at_ms=now - 1),
            lambda c, p: p.update(outcome_end_ms=p["start_ms"]),
            lambda c, p: c["window"].update(validation_start_ms=c["window"]["training_end_ms"]),
            lambda c, p: c["window"].update(validation_end_ms=now + 7 * 86400000 + 1),
            lambda c, p: c.update(trained_until_ms=now + 1),
            lambda c, p: p.pop("rule"),
        ]
        for change in mutations:
            changed_c, changed_p = copy.deepcopy((c, p))
            change(changed_c, changed_p)
            with self.assertRaises(ValueError):
                calendar_admission(changed_c, changed_p, now)
        with self.assertRaises(ValueError):
            calendar_admission(c, p, True)
        with self.assertRaises(ValueError):
            calendar_admission(c, p, now, clock=lambda _: {})


if __name__ == "__main__":
    unittest.main()
