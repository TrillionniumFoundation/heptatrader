"""Adversarial offline IB execution server-time evidence tests. No network."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import patch

from historical_training.ib_fx_execution_time import (
    Egress, CLIENT_ID, REQUEST_ID, bounded_execution,
    parse_server_time, validate_plan, _check_oms
)
from historical_training.common import EvidenceError

def dummy(t="20261009 11:20:00 US/Eastern", exec_id="e.01"):
    return (SimpleNamespace(conId=12087792,symbol="EUR",secType="CASH",currency="USD"),
            SimpleNamespace(acctNumber="DU_TEST",execId=exec_id,time=t,
                price=1.101,shares="25000",side="BOT",permId=981,orderId=45,
                pendingPriceRevision=False))

class ServerTimeTests(unittest.TestCase):
    def test_explicit_utc_and_zoned_server_seconds(self):
        expected=int(datetime(2026,10,9,15,20,tzinfo=timezone.utc).timestamp()*1000)
        self.assertEqual(parse_server_time("20261009-15:20:00"),expected)
        self.assertEqual(parse_server_time("20261009 11:20:00 US/Eastern"),expected)
    def test_ambiguous_dst_is_not_guessed(self):
        with self.assertRaisesRegex(EvidenceError,"DST_AMBIGUOUS"):
            parse_server_time("20261101 01:30:00 US/Eastern")
    def test_nonexistent_dst_does_not_make_a_valid_time(self):
        with self.assertRaises(EvidenceError):
            parse_server_time("20260308 02:30:00 US/Eastern")
    def test_implicit_timezone_refused(self):
        for t in ("20261009 11:20:00","20261009 11:20:00 XYZ_FANTASY",
                  "20261009 11:20:00","2026-10-09T15:20:00Z",""):
            with self.subTest(t=t),self.assertRaises(EvidenceError):
                parse_server_time(t)
    def test_foreign_account_contract_request_refused(self):
        contract,execution=dummy()
        since=parse_server_time("20261007-00:00:00")
        now=parse_server_time("20261010-00:00:00")
        for req,acc,con in ((8891,"DU_TEST",contract),
                            (REQUEST_ID,"DU_ANOTHER",contract),
                            (REQUEST_ID,"DU_TEST",SimpleNamespace(conId=999,symbol="EUR",secType="CASH",currency="USD"))):
            with self.subTest(req=req,acc=acc),self.assertRaisesRegex(EvidenceError,"FOREIGN"):
                bounded_execution(req,con,execution,acc,since,now)
    def test_price_pending_is_preserved_without_promoting(self):
        con,ex=dummy()
        ex.pendingPriceRevision=True
        row=bounded_execution(REQUEST_ID,con,ex,"DU_TEST",
            parse_server_time("20261007-00:00:00"),
            parse_server_time("20261010-00:00:00"))
        self.assertTrue(row["price_revision_pending"])
        self.assertEqual(row["server_time_precision_ms"],1000)
    def test_wrong_side_and_unbounded_quantity_refused(self):
        con,ex=dummy()
        ex.side="BUY"
        with self.assertRaisesRegex(EvidenceError,"SIDE"):
            bounded_execution(REQUEST_ID,con,ex,"DU_TEST",
                parse_server_time("20261007-00:00:00"),
                parse_server_time("20261010-00:00:00"))
        ex.side="BOT";ex.shares="-5"
        with self.assertRaisesRegex(EvidenceError,"ECONOMICS"):
            bounded_execution(REQUEST_ID,con,ex,"DU_TEST",
                parse_server_time("20261007-00:00:00"),
                parse_server_time("20261010-00:00:00"))

class WireTests(unittest.TestCase):
    def test_exact_single_paper_request(self):
        b=Egress("DU_TEST","20261007-00:00:00")
        start="\0".join(["71","2",str(CLIENT_ID),""])+"\0"
        request="\0".join(["7","3",str(REQUEST_ID),"0","DU_TEST",
                           "20261007-00:00:00","EUR","CASH","",""])+"\0"
        b.check(start,False,187)
        b.check(request,True,187)
        self.assertEqual(dict(b.counts),{71:1,7:1})
        with self.assertRaises(EvidenceError):b.check(request,True,187)
    def test_mutation_and_wrong_filter_rejected(self):
        base=["7","3",str(REQUEST_ID),"0","DU_TEST",
              "20261007-00:00:00","EUR","CASH","",""]
        for changes in ({"0":"9"},{"4":"DU_FOREIGN"},{"6":"BTC"},
                        {"7":"CRYPTO"},{"8":"IDEALPRO"},{"3":"700"}):
            b=Egress("DU_TEST","20261007-00:00:00")
            b.check("\0".join(["71","2",str(CLIENT_ID),""])+"\0",False,187)
            fields=base[:]
            for k,v in changes.items():fields[int(k)]=v
            with self.subTest(changes=changes),self.assertRaises(EvidenceError):
                b.check("\0".join(fields)+"\0",True,187)
    def test_request_before_managed_accounts_rejected(self):
        b=Egress("DU_TEST","20261007-00:00:00")
        b.check("\0".join(["71","2",str(CLIENT_ID),""])+"\0",False,187)
        with self.assertRaises(EvidenceError):
            b.check("\0".join(["7","3",str(REQUEST_ID),"0","DU_TEST",
                                "20261007-00:00:00","EUR","CASH","",""])+"\0",False,187)

class JoinTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.p=Path(self.t.name)/"oms.jsonl"
        self.ev={"event":"broker_execution","execution_domain":"PAPER",
                 "account":"DU_TEST","instrument":"EUR.USD","ts_ms":1791552000500,
                 "broker_execution_id":"e.01","broker_econ_execution_price":1.101,
                 "broker_econ_shares":25000.,"broker_econ_permanent_order_id":981}
        self.row={"execution_id":"e.01","price":"1.101","shares":"25000",
                  "permanent_order_id":981,"price_revision_pending":False,
                  "server_time_second_ms":1791552000000}
        self.p.write_text(json.dumps(self.ev)+"\n")
    def test_matching_oms_fee_source_never_invents_latency_precision(self):
        x=_check_oms([self.row.copy()],self.p,"DU_TEST",1791300000000)
        self.assertEqual(x["exact_oms_economics_matches"],1)
    def test_foreign_account_does_not_match(self):
        x=_check_oms([self.row.copy()],self.p,"DU_ANOTHER",1791300000000)
        self.assertEqual(x["exact_oms_economics_matches"],0)
    def test_economics_mismatch_not_silently_corrected(self):
        bad=dict(self.row,price="1.202")
        x=_check_oms([bad],self.p,"DU_TEST",1791300000000)
        self.assertEqual(x["economics_mismatches"],1)
        self.assertEqual(x["exact_oms_economics_matches"],0)
    def test_replay_with_conflicting_economics_fails_closed(self):
        bad={**self.ev,"broker_econ_shares":55000.}
        self.p.write_text(json.dumps(self.ev)+"\n"+json.dumps(bad)+"\n")
        with self.assertRaisesRegex(EvidenceError,"DUPLICATE_CONFLICT"):
            _check_oms([self.row.copy()],self.p,"DU_TEST",1791300000000)
    def test_same_broker_execution_replayed_with_new_callback_is_not_a_second_fill(self):
        replay={**self.ev,"ts_ms":1791552060500,"broker_connection_epoch":19,
                "source":"RECONNECT_EXECUTION_REPLAY","event_id":"another-event"}
        self.p.write_text(json.dumps(self.ev)+"\n"+json.dumps(replay)+"\n")
        row=self.row.copy()
        result=_check_oms([row],self.p,"DU_TEST",1791300000000)
        self.assertEqual(result["exact_oms_economics_matches"],1)
        self.assertEqual(result["oms_distinct_execution_count"],1)
        self.assertEqual(result["local_callback_replay_count"],1)
        self.assertEqual(row["earliest_oms_callback_lag_ms"],500)
        self.assertEqual(row["oms_callback_receipts"],2)

    def test_replay_changes_in_economics_still_fail_closed(self):
        bad={**self.ev,"broker_econ_execution_price":1.202,
             "ts_ms":1791552060500,"broker_connection_epoch":2}
        self.p.write_text(json.dumps(self.ev)+"\n"+json.dumps(bad)+"\n")
        with self.assertRaisesRegex(EvidenceError,"DUPLICATE_CONFLICT"):
            _check_oms([self.row.copy()],self.p,"DU_TEST",1791300000000)

    def test_pending_price_revision_excluded(self):
        x=_check_oms([dict(self.row,price_revision_pending=True)],self.p,"DU_TEST",1791300000000)
        self.assertEqual(x["price_revision_pending"],1)
        self.assertEqual(x["exact_oms_economics_matches"],0)

if __name__=="__main__":
    unittest.main()
