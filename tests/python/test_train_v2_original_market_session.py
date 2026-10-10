"""Synthetic adverse calendar/source fixtures, explicitly not broker evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import os
import tempfile
import unittest

from train_v2.original_market_session import inspect, session_clock
from laya_cell.shadow_source import SCHEMA as SOURCE_SCHEMA, canonical, digest

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


class SessionCalendarTests(unittest.TestCase):
    def test_saturday_is_closed(self):
        x = session_clock(datetime(2026, 10, 10, 18, 24, tzinfo=UTC))
        self.assertEqual(x["classification"], "OUTSIDE_REGULAR_FX_WEEKLY_SESSION")
        self.assertFalse(x["calendar_regular_session_open"])
        self.assertEqual(x["next_regular_calendar_open_utc"],
                         "2026-10-11T21:15:00.000+00:00")
        self.assertFalse(x["trade_authority"])
        self.assertFalse(x["realtime_quote_subscription_admitted_by_calendar"])

    def test_sunday_open_transition_exact(self):
        before = datetime(2026, 10, 11, 17, 14, 59, tzinfo=NY)
        after = datetime(2026, 10, 11, 17, 15, tzinfo=NY)
        self.assertFalse(session_clock(before)["calendar_regular_session_open"])
        self.assertTrue(session_clock(after)["calendar_regular_session_open"])
        self.assertFalse(session_clock(after)["trade_authority"])

    def test_friday_close_transition_exact(self):
        before = datetime(2026, 10, 9, 16, 59, 59, tzinfo=NY)
        after = datetime(2026, 10, 9, 17, 0, tzinfo=NY)
        self.assertTrue(session_clock(before)["calendar_regular_session_open"])
        x = session_clock(after)
        self.assertFalse(x["calendar_regular_session_open"])
        self.assertEqual(x["next_regular_calendar_open_utc"],
                         "2026-10-11T21:15:00.000+00:00")

    def test_monday_daily_15min_rollover_fails_closed(self):
        day = datetime(2026, 10, 12, 17, 0, tzinfo=NY)
        self.assertEqual(session_clock(day)["classification"],
                         "FX_DAILY_ROLLOVER_GAP")
        self.assertFalse(session_clock(day)["calendar_regular_session_open"])
        self.assertTrue(session_clock(day + timedelta(minutes=15))[
            "calendar_regular_session_open"])

    def test_summer_and_winter_dst_time_both_new_york(self):
        summer = session_clock(datetime(2026, 6, 6, 18, 0, tzinfo=UTC))
        winter = session_clock(datetime(2026, 12, 5, 18, 0, tzinfo=UTC))
        self.assertEqual(summer["next_regular_calendar_open_utc"],
                         "2026-06-07T21:15:00.000+00:00")
        self.assertEqual(winter["next_regular_calendar_open_utc"],
                         "2026-12-06T22:15:00.000+00:00")

    def test_no_naive_or_fake_independent_provider_admission(self):
        with self.assertRaisesRegex(ValueError, "TIMEZONE_AWARE"):
            session_clock(datetime(2026, 10, 11))
        open_time = datetime(2026, 10, 12, 17, 15, tzinfo=NY)
        result = session_clock(open_time)
        self.assertTrue(result["calendar_regular_session_open"])
        self.assertFalse(result["realtime_quote_subscription_admitted_by_calendar"])
        self.assertTrue(result["calendar_holidays_and_scheduled_outages_not_proven"])


class OriginalChainFixtureTests(unittest.TestCase):
    def setUp(self):
        obj = tempfile.TemporaryDirectory()
        self.addCleanup(obj.cleanup)
        self.root = Path(obj.name)
        self.root.chmod(0o700)
        self.period = self.root / "period.json"
        self.period.write_bytes(b'{"execution_domain":"PAPER"}\n')
        self.period.chmod(0o600)
        self.period_hash = hashlib.sha256(self.period.read_bytes()).hexdigest()
        self.feed = self.root / "feed"
        self.feed.mkdir(mode=0o700)
        self.segment = self.feed / "segment-original-test"
        self.segment.mkdir(mode=0o700)
        self.source = self.segment / "quotes.jsonl"
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({
            "schema": "heptatrader.laya-resident-config.v1",
            "automatic_trading": False,
            "period_path": str(self.period),
            "period_sha256": self.period_hash,
            "feed_directory": str(self.feed),
        },sort_keys=True))
        self.config.chmod(0o600)
        self.regular_close = datetime(2026, 10, 9, 17, tzinfo=NY)
        self.close_ms = int(self.regular_close.timestamp()*1000)
        self.write_fixture()

    def write_fixture(self, *, include_quotes=True, corrupt=False,
                      reason="AUTHORITATIVE_QUOTE_UNREADY"):
        events = [("HEADER", self.close_ms - 1400, {
            "source": "IB_TOOL_GATEWAY",
            "execution_domain": "PAPER",
            "instrument": "EUR.USD",
            "period_sha256": self.period_hash,
            "write_operations_allowed": [],
            "broker_orders_sent": 0,
        })]
        if include_quotes:
            for index in range(6):
                at = self.close_ms - 1301 + index * 300
                events.append(("QUOTE", at+5, {
                    "instrument": "EUR.USD",
                    "source": "IB",
                    "authoritative": True,
                    "stale": False,
                    "subscription_id": "fixture-only",
                    "subscription_state": "ACTIVE",
                    "bid": "1.00001", "ask": "1.00003",
                    "observed_at_ms": at,
                    "stale_after_ms": at + 5000,
                }))
        events.append(("FAILURE", self.close_ms+1204, {
            "reason": reason, "quote_timestamp_rewritten": False
        }))
        events.append(("END", self.close_ms+1500, {"broker_orders_sent":0}))
        output=[]
        prev="0"*64
        for seq,(kind,at,data) in enumerate(events):
            body={"schema":SOURCE_SCHEMA,"sequence":seq,"previous":prev,
                  "at_ms":at,"kind":kind,"data":data}
            h=digest(body)
            output.append(canonical({**body,"sha256":h})+b"\n")
            prev=h
        if corrupt:
            output[-3]=output[-3].replace(b'"bid":"1.00001"',
                                         b'"bid":"1.90001"')
        self.source.write_bytes(b"".join(output))
        self.source.chmod(0o600)

    def test_original_chain_close_correlates_but_never_grants_trust(self):
        weekend = datetime(2026, 10, 10, 18, 24, tzinfo=UTC)
        report = inspect(self.config,now=weekend)
        self.assertEqual(report["status"], "HOLD_FX_REGULAR_SESSION_CLOSED")
        self.assertEqual(report["verified_local_quote_count_in_all_segments"],6)
        self.assertEqual(report["latest_original_quote"][
            "last_quote_offset_from_1700_friday_close_ms"],199)
        self.assertEqual(report["first_verified_failure_after_last_quote"][
            "offset_from_1700_friday_close_ms"],1204)
        self.assertTrue(report["regular_weekly_close_explains_transition_consistently"])
        self.assertFalse(report["market_data_entitlement_independently_verified"])
        self.assertFalse(report["native_product_ledger_writer_acked"])
        self.assertFalse(report["trade_authority"])

    def test_market_reopens_but_old_quote_is_not_rehabilitated(self):
        market_open = datetime(2026, 10, 11, 17, 20,tzinfo=NY)
        result = inspect(self.config,now=market_open)
        self.assertEqual(result["status"],"HOLD_AUTHORITATIVE_QUOTE_NOT_FRESH")
        self.assertTrue(result["calendar"]["calendar_regular_session_open"])
        self.assertFalse(result["trade_authority"])

    def test_weekend_without_any_original_quote_still_holds(self):
        self.write_fixture(include_quotes=False)
        result=inspect(self.config,now=datetime(2026,10,10,18,tzinfo=UTC))
        self.assertEqual(result["status"],"HOLD_FX_REGULAR_SESSION_CLOSED")
        self.assertIsNone(result["latest_original_quote"])
        self.assertEqual(result["verified_local_quote_count_in_all_segments"],0)

    def test_corrupt_hash_chain_fails_not_silent_hold(self):
        self.write_fixture(corrupt=True)
        with self.assertRaisesRegex(ValueError,"SHADOW_SOURCE_CHAIN"):
            inspect(self.config)

    def test_period_mutation_fails_before_source_read(self):
        self.period.write_text('{"execution_domain":"PAPER","modified":true}\n')
        with self.assertRaisesRegex(ValueError, "ORIGINAL_PAPER_PERIOD_CHANGED"):
            inspect(self.config)

    def test_symbolic_source_file_is_not_admitted(self):
        moved=self.root/"actual-original"
        self.source.rename(moved)
        self.source.symlink_to(moved)
        with self.assertRaisesRegex(ValueError,"SOURCE_PATH_ESCAPE"):
            inspect(self.config)

    def test_exact_duplicate_timestamp_in_second_segment_is_not_double_counted(self):
        second = self.feed / "segment-exact-duplicate-test"
        second.mkdir(mode=0o700)
        file = second / "quotes.jsonl"
        file.write_bytes(self.source.read_bytes())
        file.chmod(0o600)
        result = inspect(self.config,now=datetime(2026,10,10,18,tzinfo=UTC))
        self.assertEqual(result["verified_local_quote_count_in_all_segments"],12)
        self.assertEqual(result["unique_verified_original_quote_timestamps"],6)
        self.assertEqual(result["cross_segment_quote_price_conflicts"],0)

    def test_conflicting_timestamp_price_in_second_segment_fails(self):
        second = self.feed / "segment-conflicting-test"
        second.mkdir(mode=0o700)
        file = second / "quotes.jsonl"
        # Both original local hash chains are individually self-consistent.
        # Different bid on same timestamp MUST be rejected across segments.
        raw=self.source.read_bytes()
        rows=[json.loads(x) for x in raw.splitlines()]
        prev="0"*64
        out=[]
        for i,row in enumerate(rows):
            data=row["data"]
            if row["kind"] == "QUOTE":
                data=dict(data,bid="1.00002")
            body={k:v for k,v in row.items() if k not in ("data","previous","sha256")}
            body["data"]=data
            body["previous"]=prev
            digest_value=digest(body)
            out.append(canonical({**body,"sha256":digest_value})+b"\n")
            prev=digest_value
        file.write_bytes(b"".join(out))
        file.chmod(0o600)
        with self.assertRaisesRegex(ValueError,"CROSS_SEGMENT_ORIGINAL_QUOTE_PRICE_CONFLICT"):
            inspect(self.config,now=datetime(2026,10,10,18,tzinfo=UTC))

    def test_bounded_recent_archive_survives_more_than_two_hundred_segments(self):
        from train_v2.original_market_session import MAX_SEGMENTS
        raw = self.source.read_bytes()
        for index in range(MAX_SEGMENTS):
            directory = self.feed / ("segment-archive-%04d" % index)
            directory.mkdir(mode=0o700)
            file = directory / "quotes.jsonl"
            file.write_bytes(raw)
            file.chmod(0o600)
        result = inspect(self.config, now=datetime(2026,10,10,18,tzinfo=UTC))
        self.assertEqual(result["original_feed_source_count"], MAX_SEGMENTS)
        self.assertEqual(result["older_segments_outside_bounded_recent_window"], 1)
        self.assertFalse(result["all_configured_feed_history_verified_in_this_audit"])
        self.assertEqual(result["unique_verified_original_quote_timestamps"],6)
        self.assertEqual(result["status"],"HOLD_FX_REGULAR_SESSION_CLOSED")
        self.assertFalse(result["trade_authority"])

    def test_untrusted_live_trading_config_fails(self):
        c=json.loads(self.config.read_text())
        c["automatic_trading"]=True
        self.config.write_text(json.dumps(c))
        with self.assertRaisesRegex(ValueError,"LOCAL_PAPER_FEED_NOT_AUTHORIZED"):
            inspect(self.config)

    def test_invalid_timezone_or_source_age_never_produces_trade_permission(self):
        m=datetime(2026,10,10,18,tzinfo=UTC)
        value=inspect(self.config,now=m)
        for key in ("after_cost_forward_efficacy_accepted",
                    "complete_original_trade_behavior_policy_observed",
                    "production_generator_and_observer_enrolled",
                    "research_new_shadow_decision_emitted",
                    "trade_authority"):
            self.assertFalse(value[key],key)


if __name__ == "__main__":
    unittest.main()
