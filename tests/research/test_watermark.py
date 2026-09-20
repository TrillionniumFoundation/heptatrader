"""Real compiled-converter checks; no wall-clock-derived completion or fake ticks."""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class WatermarkCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = os.environ.get("HEPTA_RESEARCH_BARS")
        if not binary or not Path(binary).is_file():
            raise RuntimeError("HEPTA_RESEARCH_BARS must name the actual built converter")
        cls.binary = str(Path(binary).resolve())

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hepta-watermark-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.ticks = self.root / "ticks.csv"
        self.sessions = self.root / "sessions.csv"
        self.sessions.write_text("begin_us,end_us,trading_day\n0,10,20260921\n20,30,20260921\n")
        self.write_ticks([(0, 1, 10, 100), (9, 2, -2, 105)])

    def write_ticks(self, rows):
        self.ticks.write_text("instrument,trading_day,timestamp_us,sequence,price_ticks,cumulative_volume\n" +
                              "".join(f"TEST,20260921,{t},{seq},{p},{v}\n" for t, seq, p, v in rows))

    def command(self, *extra, period=10, policy="baseline"):
        return [self.binary, str(self.ticks), str(self.sessions), str(period), policy, *map(str, extra)]

    def invoke(self, *extra, period=10, policy="baseline", success=True):
        result = subprocess.run(self.command(*extra, period=period, policy=policy),
                                capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0 if success else 2, result.stderr)
        if not success:
            self.assertIn("research input rejected:", result.stderr)
            return result
        self.assertEqual(result.stderr, "")
        return list(csv.DictReader(io.StringIO(result.stdout)))

    def test_default_eof_stays_incomplete(self):
        rows = self.invoke()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], dict(instrument="TEST", trading_day="20260921", begin_us="0", end_us="10",
                                      open="10", high="10", low="-2", close="-2", volume="5", ticks="2", complete="0"))

    def test_explicit_close_changes_only_completeness(self):
        expected = self.invoke()[0]
        expected["complete"] = "1"
        self.assertEqual(self.invoke("--watermark-us", 10), [expected])

    def test_at_last_tick_is_not_bar_end(self):
        self.assertEqual(self.invoke("--watermark-us", 9), self.invoke())

    def test_watermark_before_last_tick_fails(self):
        self.invoke("--watermark-us", 8, success=False)

    def test_far_future_does_not_create_empty_bars(self):
        self.assertEqual(self.invoke("--watermark-us", 10), self.invoke("--watermark-us", 10**12))

    def test_midday_break_does_not_close_daily_bar(self):
        rows = self.invoke("--watermark-us", 10, period=0)
        self.assertEqual((len(rows), rows[0]["end_us"], rows[0]["complete"]), (1, "30", "0"))
        self.assertEqual(rows, self.invoke("--watermark-us", 19, period=0))

    def test_final_daily_close_has_no_synthetic_observation(self):
        row = self.invoke("--watermark-us", 30, period=0)[0]
        self.assertEqual((row["complete"], row["ticks"], row["volume"]), ("1", "2", "5"))

    def test_sessions_preserve_volume_baseline(self):
        self.write_ticks([(0, 1, 10, 100), (9, 2, -2, 105), (20, 3, 3, 108), (29, 4, 5, 110)])
        rows = self.invoke("--watermark-us", 30)
        self.assertEqual([r["volume"] for r in rows], ["5", "5"])
        self.assertEqual([r["ticks"] for r in rows], ["2", "2"])
        self.assertEqual([r["complete"] for r in rows], ["1", "1"])

    def test_include_policy_remains_explicit(self):
        row = self.invoke("--watermark-us", 30, policy="include")[0]
        self.assertEqual((row["volume"], row["ticks"]), ("105", "2"))

    def test_exact_last_duplicate_is_not_counted(self):
        self.write_ticks([(0, 1, 10, 100), (9, 2, -2, 105), (9, 2, -2, 105)])
        row = self.invoke("--watermark-us", 10)[0]
        self.assertEqual((row["volume"], row["ticks"]), ("5", "2"))

    def test_invalid_watermark_syntax_and_overflow(self):
        for value in ("-1", "1.0", "+10", "1e2", "", str(2**63), " 10", "10 "):
            with self.subTest(value=value):
                self.invoke("--watermark-us", value, success=False)

    def test_invalid_options_and_incomplete_flag(self):
        for args in (("--watermark", 10), ("--watermark-us",), ("--watermark-us", 10, "extra")):
            with self.subTest(args=args):
                self.invoke(*args, success=False)

    def test_interval_clipped_to_supplied_session(self):
        rows = self.invoke("--watermark-us", 10, period=7)
        self.assertEqual([(r["begin_us"], r["end_us"], r["complete"]) for r in rows],
                         [("0", "7", "1"), ("7", "10", "1")])

    def test_int64_limit_does_not_overflow(self):
        maximum = 2**63 - 1
        self.sessions.write_text(f"begin_us,end_us,trading_day\n{maximum-10},{maximum},20260921\n")
        self.write_ticks([(maximum-1, 1, -(2**63), 2**64-1)])
        row = self.invoke("--watermark-us", maximum, period=maximum, policy="include")[0]
        self.assertEqual((row["end_us"], row["close"], row["volume"], row["complete"]),
                         (str(maximum), str(-(2**63)), str(2**64-1), "1"))

    def test_bad_later_tick_is_not_repaired_by_watermark(self):
        self.write_ticks([(0, 1, 10, 100), (20, 2, 20, 105), (21, 3, 30, 99)])
        self.invoke("--watermark-us", 30, success=False)

    @unittest.skipUnless(Path("/dev/full").exists(), "requires POSIX full-device sink")
    def test_buffered_output_failure_is_not_success(self):
        with open("/dev/full", "wb") as sink:
            result = subprocess.run(self.command("--watermark-us", 10), stdout=sink,
                                    stderr=subprocess.PIPE, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("CSV write error", result.stderr)


if __name__ == "__main__":
    unittest.main()
