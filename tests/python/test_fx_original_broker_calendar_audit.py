"""Fail-closed scope tests for cached IB historical trading-session evidence."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest

from historical_training.common import sha
from train_v2.fx_original_broker_calendar_audit import (
    CalendarHold, parse_cached_schedule, session_label, grid_session_quality
)
from train_v2.fx_original_native_dataset import write_once_private

def ms(y,mo,d,h,minute=0):
    return int(datetime(y,mo,d,h,minute,tzinfo=timezone.utc).timestamp()*1000)

def fixture(directory):
    p=Path(directory)
    request={
        "kind":"schedule","what":"SCHEDULE","bar_size":"1 day",
        "duration":"30 D","end":"20261009 20:00:00 UTC",
        "requested_start_utc":"2026-10-07T22:00:00+00:00",
        "requested_end_utc":"2026-10-09T20:00:00+00:00",
    }
    response={
        "schedule":{
            "start":"20261007-17:15:00",
            "end":"20261009-17:00:00",
            "timezone":"US/Eastern",
            "sessions":[
                {"start":"20261007-17:15:00","end":"20261008-17:00:00",
                 "reference_date":"20261008"},
                {"start":"20261008-17:15:00","end":"20261009-17:00:00",
                 "reference_date":"20261009"},
            ],
        },
        "bars":[],"errors":[],"head":None,"end_callback":None,
    }
    write_once_private(p/"request.json",request)
    write_once_private(p/"response.json",response)
    receipt={"state":"RECEIVED","request":request,
             "response_sha256":sha(p/"response.json")}
    write_once_private(p/"receipt.json",receipt)
    return p

class CachedBrokerSessionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.p=Path(tmp.name)/"private"
        self.p.mkdir(mode=0o700)
        fixture(self.p)

    def remake(self, filename, mutate):
        path=self.p/filename
        obj=json.loads(path.read_text())
        mutate(obj)
        path.unlink()
        write_once_private(path,obj)

    def test_accepts_pinned_actual_schedule_shape(self):
        s=parse_cached_schedule(self.p)
        self.assertEqual(s["session_count"],2)
        self.assertEqual(s["source"],
           "PREVIOUSLY_CAPTURED_READONLY_IB_PAPER_HISTORICAL_SCHEDULE")
        self.assertFalse(s["broker_digitally_signed"])

    def test_verified_broker_open_session(self):
        self.assertEqual(session_label(ms(2026,10,8,14),parse_cached_schedule(self.p)),
                         "IB_HISTORICAL_SESSION_OPEN")

    def test_daily_ib_break_is_not_open(self):
        self.assertEqual(session_label(ms(2026,10,8,21,5),parse_cached_schedule(self.p)),
                         "IB_HISTORICAL_SESSION_BREAK_OR_CLOSED")

    def test_future_not_in_cached_request_is_unknown_not_closed(self):
        self.assertEqual(session_label(ms(2026,10,12,10),parse_cached_schedule(self.p)),
                         "UNKNOWN_OUTSIDE_BROKER_SCHEDULE_REQUEST")

    def test_before_request_unknown(self):
        self.assertEqual(session_label(ms(2026,10,7,20),parse_cached_schedule(self.p)),
                         "UNKNOWN_OUTSIDE_BROKER_SCHEDULE_REQUEST")

    def test_same_scope_event_counts_are_disjoint(self):
        s=parse_cached_schedule(self.p)
        open_time=ms(2026,10,8,14)
        break_time=ms(2026,10,8,21,5)
        unknown_time=ms(2026,10,12,10)
        close={open_time:{
            "event_at_ms":open_time-1000,"available_at_ms":open_time-900,
            "valid_until_ms":open_time+4000}}
        groups=grid_session_quality([open_time,break_time,unknown_time],close,s)
        self.assertEqual(groups["IB_HISTORICAL_SESSION_OPEN"]["causal_quote_close"],1)
        self.assertEqual(groups["IB_HISTORICAL_SESSION_OPEN"]
                         ["original_valid_for_fixed_plus_1000_ms"],1)
        self.assertEqual(groups["IB_HISTORICAL_SESSION_BREAK_OR_CLOSED"]["grid_count"],1)
        self.assertEqual(groups["UNKNOWN_OUTSIDE_BROKER_SCHEDULE_REQUEST"]["grid_count"],1)
        self.assertEqual(sum(x["grid_count"] for x in groups.values()),3)

    def test_quote_over_4500ms_rejected_only_in_quality(self):
        s=parse_cached_schedule(self.p)
        t=ms(2026,10,8,14)
        close={t:{"event_at_ms":t-4000,
                  "available_at_ms":t-3700,"valid_until_ms":t+1000}}
        result=grid_session_quality([t],close,s)
        self.assertEqual(result["IB_HISTORICAL_SESSION_OPEN"]
                         ["quote_age_or_expiry_rejected"],1)

    def test_modified_broker_response_without_receipt_hash_refused(self):
        self.remake("response.json",lambda x:x["schedule"]["sessions"][0]
                    .update(end="20261008-16:59:00"))
        with self.assertRaisesRegex(CalendarHold,"COMPLETION_OR_HASH"):
            parse_cached_schedule(self.p)

    def test_broker_reported_session_overlap_refused(self):
        self.remake("response.json",lambda x:x["schedule"]["sessions"][1]
                    .update(start="20261008-16:55:00"))
        self.remake("receipt.json",lambda x:x.update(
            response_sha256=sha(self.p/"response.json")))
        with self.assertRaisesRegex(CalendarHold,"SESSIONS_OVERLAP"):
            parse_cached_schedule(self.p)

    def test_request_not_fully_covered_refused(self):
        self.remake("request.json",lambda x:x.update(
            requested_end_utc="2026-10-09T23:00:00+00:00"))
        self.remake("receipt.json",lambda x:x.update(
            request=json.loads((self.p/"request.json").read_text())))
        with self.assertRaisesRegex(CalendarHold,"NOT_REQUEST_FULL_COVERAGE"):
            parse_cached_schedule(self.p)

    def test_wrong_request_not_a_broker_schedule(self):
        self.remake("request.json",lambda x:x.update(what="MIDPOINT"))
        self.remake("receipt.json",lambda x:x.update(
            request=json.loads((self.p/"request.json").read_text())))
        with self.assertRaisesRegex(CalendarHold,"BROKER_SCHEDULE_SCOPE"):
            parse_cached_schedule(self.p)

    def test_forged_unknown_zone_refused(self):
        self.remake("response.json",lambda x:x["schedule"].update(
            timezone="America/Los_Angeles"))
        self.remake("receipt.json",lambda x:x.update(
            response_sha256=sha(self.p/"response.json")))
        with self.assertRaisesRegex(CalendarHold,"TIMEZONE_NOT_QUALIFIED"):
            parse_cached_schedule(self.p)

    def test_future_session_after_request_not_silently_extended(self):
        s=parse_cached_schedule(self.p)
        t=ms(2026,10,10,10)
        self.assertEqual(session_label(t,s),
                         "UNKNOWN_OUTSIDE_BROKER_SCHEDULE_REQUEST")

    def test_modified_error_codes_refused(self):
        self.remake("response.json",lambda x:x.update(
            errors=[{"code":2105,"message":"historical farm disconnected"}]))
        self.remake("receipt.json",lambda x:x.update(
            response_sha256=sha(self.p/"response.json")))
        with self.assertRaisesRegex(CalendarHold,"COMPLETION_OR_HASH"):
            parse_cached_schedule(self.p)

if __name__=="__main__":
    unittest.main()
