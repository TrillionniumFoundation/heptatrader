"""Capture health regressions: diagnostics never replace original quote evidence."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from laya_cell.feed_health import CaptureHealth, feed_status, atomic_status
from laya_cell.shadow_source import collect, read_source, valid_quote

T = int(datetime(2026,10,16,16,15,tzinfo=timezone.utc).timestamp()*1000)


def quote():
    return {"instrument":"EUR.USD","source":"IB","authoritative":True,"stale":False,
            "bid":"1.1","ask":"1.10002","observed_at_ms":T,
            "stale_after_ms":T+5000,"subscription_id":"TEST","subscription_state":"active"}


class FeedHealthTests(unittest.TestCase):
    def health(self):
        return CaptureHealth("/fixture/segment-1/quotes.jsonl","a"*64,T)

    def test_completed_zero_quote_capture_is_unavailable(self):
        h=self.health()
        for i,reason in enumerate(["QUOTE_TOOL_JSON_INVALID","EXECUTION_EVENT_RESPONSE_READ_FAILED","AUTHORITATIVE_QUOTE_STALE"]):
            h.observe("FAILURE",{"reason":reason},T+i)
        s=h.snapshot(T+4,completed=True)
        status=feed_status(s,None,False,T+4)
        self.assertEqual(status["data_state"],"CAPTURE_UNAVAILABLE")
        self.assertEqual(s["counts"],{"QUOTE":0,"FAILURE":3})
        self.assertEqual(len(s["failure_reasons"]),3)
        self.assertIsNone(s["last_valid_quote"])
        self.assertTrue(status["prior_regular_session_outage_unrecovered"])

    def test_source_timestamp_is_not_refreshed_by_sampling_or_status(self):
        h=self.health()
        h.observe("QUOTE",quote(),T+1)
        h.observe("QUOTE",quote(),T+1000)
        s=h.snapshot(T+4000)
        self.assertEqual(s["counts"]["QUOTE"],2)
        self.assertEqual(s["last_valid_quote"]["observed_at_ms"],T)
        self.assertEqual(s["count_semantics"],"CAPTURE_RECORDS_NOT_INDEPENDENT_MARKET_UPDATES")
        for age,wanted in [(4999,True),(5000,False),(5001,False)]:
            self.assertEqual(h.snapshot(T+age)["quote_fresh_at_snapshot"],wanted)
        valid_quote(quote(),T+4999,"EUR.USD")
        with self.assertRaises(ValueError):
            valid_quote(quote(),T+5000,"EUR.USD")

    def test_progress_from_stopped_collector_is_stale(self):
        h=self.health();h.observe("QUOTE",quote(),T+1)
        status=feed_status(h.snapshot(T+2),None,False,T+16000)
        self.assertEqual(status["data_state"],"PROGRESS_STALE")
        self.assertFalse(status["data_ready_from_calendar"])

    def test_weekend_rollover_does_not_erase_friday_outage(self):
        h=self.health();h.observe("QUOTE",quote(),T+1)
        h.observe("FAILURE",{"reason":"AUTHORITATIVE_QUOTE_STALE"},T+6000)
        old=feed_status(h.snapshot(T+6000),None,False,T+6000)
        saturday=int(datetime(2026,10,17,9,tzinfo=timezone.utc).timestamp()*1000)
        current=CaptureHealth("/fixture/segment-2/quotes.jsonl","a"*64,saturday)
        current.observe("FAILURE",{"reason":"AUTHORITATIVE_QUOTE_UNREADY"},saturday)
        status=feed_status(current.snapshot(saturday),old["last_valid_quote"],
                           old["prior_regular_session_outage_unrecovered"],saturday)
        self.assertFalse(status["calendar"]["calendar_regular_session_open"])
        self.assertTrue(status["prior_regular_session_outage_unrecovered"])
        self.assertEqual(status["last_valid_quote"]["observed_at_ms"],T)
        self.assertEqual(status["current_segment_counts"]["QUOTE"],0)
        self.assertEqual(status["data_state"],"CAPTURE_UNAVAILABLE")

    def test_real_subsequent_quote_clears_outage(self):
        h=self.health();h.observe("FAILURE",{"reason":"UNREADY"},T)
        h.observe("QUOTE",quote(),T+1)
        status=feed_status(h.snapshot(T+2),None,True,T+2)
        self.assertFalse(status["prior_regular_session_outage_unrecovered"])
        self.assertEqual(status["data_state"],"QUOTE_CAPTURE_OBSERVED")

    def test_atomic_status_has_no_partial_json(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"status.json"
            atomic_status(p,{"counts":{"QUOTE":1}})
            atomic_status(p,{"counts":{"QUOTE":0}})
            self.assertEqual(json.loads(p.read_text())["counts"]["QUOTE"],0)
            self.assertFalse(p.with_name("status.json.next").exists())
            self.assertEqual(p.stat().st_mode&0o777,0o600)

    def test_completed_failure_collector_keeps_journal_and_reports_health(self):
        with tempfile.TemporaryDirectory() as d:
            h=Path(d);period=h/".config/heptatrader/pnl-period.json"
            period.parent.mkdir(parents=True)
            period.write_text('{"execution_domain":"PAPER"}')
            out=h/"capture"
            counter=[0]
            def monotonic():
                counter[0]+=1
                return float(counter[0])
            with patch("laya_cell.shadow_source.Path.home",return_value=h), \
                 patch("laya_cell.shadow_source.subprocess.run",return_value=SimpleNamespace(returncode=0,stdout='not-json')), \
                 patch("laya_cell.shadow_source.time.monotonic",side_effect=monotonic), \
                 patch("laya_cell.shadow_source.time.sleep"), \
                 patch("laya_cell.shadow_source.signal.signal"):
                collect(out,10)
            report=json.loads((out/"collector-receipt.json").read_text())
            progress=json.loads((out/"collector-status.json").read_text())
            self.assertGreater(report["counts"]["FAILURE"],0)
            self.assertEqual(report["data_health"]["counts"]["QUOTE"],0)
            self.assertEqual(progress["last_capture_kind"],"FAILURE")
            header,quotes,prefix=read_source(out/"quotes.jsonl")
            self.assertEqual(quotes,[])
            self.assertTrue(prefix["ended"])
            self.assertEqual(header["broker_orders_sent"],0)

if __name__=="__main__":unittest.main()


class HistoricalSeedTests(unittest.TestCase):
    def test_newer_zero_quote_segment_keeps_open_outage(self):
        from laya_cell.feed_health import historical_last_quote
        from laya_cell.shadow_source import canonical, digest, SCHEMA
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def segment(name, events, quotes):
                d=root/name;d.mkdir()
                prev="0"*64;records=[]
                for i,(kind,at,data) in enumerate(events):
                    r={"schema":SCHEMA,"sequence":i,"previous":prev,"at_ms":at,"kind":kind,"data":data}
                    prev=digest(r);records.append(canonical({**r,"sha256":prev})+b"\n")
                (d/"quotes.jsonl").write_bytes(b"".join(records));(d/"quotes.jsonl").chmod(0o600)
                (d/"collector-receipt.json").write_text(json.dumps({"counts":{"QUOTE":quotes}}))
                return d
            t=1792167316000
            h={"source":"IB_TOOL_GATEWAY","execution_domain":"PAPER","instrument":"EUR.USD",
               "write_operations_allowed":[],"broker_orders_sent":0,"period_sha256":"a"*64}
            # Mock only quote normalization; the real hash-chain reader still verifies both files.
            q={"instrument":"EUR.USD","observed_at_ms":t,"stale_after_ms":t+5000,
               "bid":"1.1","ask":"1.2"}
            d1=segment("segment-1",[("HEADER",t-1,h),("QUOTE",t,q),("END",t+1,{})],1)
            segment("segment-2",[("HEADER",t+10000,h),("FAILURE",t+10001,{"reason":"UPSTREAM"}),
                                 ("END",t+10002,{})],0)
            older=t-86400000
            oldq={**q,"observed_at_ms":older,"stale_after_ms":older+5000}
            segment("segment-recovery-live",[("HEADER",older-1,h),("QUOTE",older,oldq),
                                              ("END",older+1,{})],1)
            from decimal import Decimal
            from unittest import mock
            with mock.patch("laya_cell.shadow_source.valid_quote",return_value=(Decimal("1.1"),Decimal("1.2"))):
                last,outage=historical_last_quote(root,"a"*64)
            self.assertEqual(last["observed_at_ms"],t);self.assertTrue(outage)
            raw=(d1/"quotes.jsonl").read_bytes().replace(b'1.1',b'1.3')
            (d1/"quotes.jsonl").write_bytes(raw)
            with mock.patch("laya_cell.shadow_source.valid_quote",return_value=(Decimal("1.1"),Decimal("1.2"))):
                last,outage=historical_last_quote(root,"a"*64)
            self.assertEqual(last["observed_at_ms"],older);self.assertTrue(outage)
