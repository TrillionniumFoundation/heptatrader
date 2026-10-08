"""Offline order-incapable EUR/USD quote transport and freshness tests."""
from __future__ import annotations

import pathlib
import json
import tempfile
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/"scripts"))
import hepta_fx_readonly_probe as probe


def wire(fields):
    return "\0".join(fields) + "\0"


class QuoteWireTests(unittest.TestCase):
    def test_exact_allowed_send_sequence_and_counts(self):
        gate=probe.QuoteWireGate(29554)
        gate.check(wire(["71","2","29554"]))
        gate.check(wire(["59","1","1"]))
        gate.check(wire(probe.PINNED_MARKET_DATA_WIRE))
        gate.check(wire(["2","2","4"]))
        self.assertEqual(gate.counts,{"71":1,"59":1,"1":1,"2":1})
        self.assertTrue(gate.cancelled)

    def test_foreign_trading_and_generic_sends_denied(self):
        for fields in (
            ["3","45","1"],  # place order
            ["4","2","1"],   # cancel order
            ["58","1"],      # global cancel
            ["76","1","1","DU_FAKE","","1"],  # account updates
            ["71","2","29553"],  # foreign client
            ["1","11","5"]+probe.PINNED_MARKET_DATA_WIRE[3:],  # foreign id
            probe.PINNED_MARKET_DATA_WIRE[:4]+["GBP"]+
                probe.PINNED_MARKET_DATA_WIRE[5:],  # wrong contract
        ):
            with self.subTest(fields=fields[:4]),self.assertRaisesRegex(
                probe.ProbeViolation,"FOREIGN_SEND_DENIED"):
                probe.QuoteWireGate(29554).check(wire(fields))

    def test_denies_duplicate_subscription_and_unowned_cancel(self):
        gate=probe.QuoteWireGate(29554)
        gate.check(wire(["71","2","29554"]))
        with self.assertRaises(probe.ProbeViolation):
            gate.check(wire(["2","2","4"]))
        gate.check(wire(probe.PINNED_MARKET_DATA_WIRE))
        with self.assertRaises(probe.ProbeViolation):
            gate.check(wire(probe.PINNED_MARKET_DATA_WIRE))
        gate.check(wire(["2","2","4"]))
        with self.assertRaises(probe.ProbeViolation):
            gate.check(wire(["2","2","4"]))


class FreshQuoteTests(unittest.TestCase):
    def test_both_fresh_live_bid_and_ask_mandatory(self):
        q=probe.LiveQuoteState()
        q.price(1,1.12,1000)
        self.assertIsNone(q.evidence(1001))
        q.market_type(1)
        q.price(1,1.12,1002)
        self.assertIsNone(q.evidence(1003))
        q.price(2,1.121,1004)
        self.assertEqual(q.evidence(1005)["bid"],"1.1200000000000001")
        self.assertIsNone(q.evidence(6003))
        self.assertIsNone(q.evidence(1003))  # future ask cannot be backdated
        q.market_type(3)
        self.assertIsNone(q.evidence(1006))

    def test_nonfinite_crossed_and_delayed_cannot_become_live(self):
        q=probe.LiveQuoteState()
        q.market_type(3);q.price(1,1.12,1000);q.price(2,1.121,1001)
        self.assertIsNone(q.evidence(1002))
        q.market_type(1);q.price(1,1.13,1100);q.price(2,1.12,1101)
        self.assertIsNone(q.evidence(1102))
        q.price(2,float("nan"),1103)
        self.assertIsNone(q.evidence(1104))
        q.price(2,1.14,1105)
        self.assertIsNotNone(q.evidence(1105))
        q.market_type(4)
        self.assertIsNone(q.evidence(1106))


class FakeContract:
    pass


class FakeWrapper:
    pass


class FakeClient:
    mode="live"
    def __init__(self,wrapper):
        self._connected=False
        self._stopped=threading.Event()
    def connect(self,host,port,client_id):
        assert host=="127.0.0.1" and port==4002
        self._connected=True
        self.sendMsg(wire(["71","2",str(client_id)]))
        if type(self).mode!="handshake_timeout":
            self.nextValidId(123)
    def run(self):
        self._stopped.wait(1)
    def disconnect(self):
        self._connected=False
        self._stopped.set()
    def isConnected(self):
        return self._connected
    def sendMsg(self,message):
        return True
    def reqMarketDataType(self,value):
        self.sendMsg(wire(["59","1",str(value)]))
    def reqMktData(self,req,contract,generic,snapshot,regulatory,opts):
        if type(self).mode=="unsafe":
            self.sendMsg(wire(["3","45","1"]))
            return
        self.sendMsg(wire(probe.PINNED_MARKET_DATA_WIRE))
        if type(self).mode=="no_quote":
            return
        if type(self).mode=="delayed":
            self.marketDataType(req,3)
        else:
            self.marketDataType(req,1)
        self.tickPrice(req,1,1.12,None)
        self.tickPrice(req,2,1.121,None)
    def cancelMktData(self,req):
        self.sendMsg(wire(["2","2",str(req)]))


class OwnerFenceTests(unittest.TestCase):
    def test_isolated_client_must_not_collide_with_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            conf=root/".config/heptatrader"
            conf.mkdir(parents=True)
            (conf/"account-observer.json").write_text(json.dumps({
                "schema":"heptatrader.account-observer-config.v1",
                "client_id":29554,
            }))
            (conf/"pnl-period.json").write_text(json.dumps({
                "schema":"heptatrader.pnl-period.v1",
                "instruments":{"EUR.USD":{"broker_client_id":29555}},
            }))
            for owner in (29554,29555,701,19999,30001):
                with self.subTest(owner=owner),self.assertRaises(probe.ProbeViolation):
                    probe.validate_isolated_client_id(owner,root)
            probe.validate_isolated_client_id(29556,root)
            (conf/"pnl-period.json").unlink()
            with self.assertRaisesRegex(probe.ProbeViolation,
                                        "OWNER_FENCE_UNAVAILABLE"):
                probe.validate_isolated_client_id(29556,root)


class SyntheticBrokerTests(unittest.TestCase):
    def exercise(self,mode):
        FakeClient.mode=mode
        with patch.object(probe,"read_sdk",return_value=(
                FakeClient,FakeWrapper,FakeContract)):
            return probe.capture_one(29554,pathlib.Path("/unused"),
                                     window_sec=0.01,handshake_sec=0.01)
    def test_live_quote_recovers_with_fresh_two_legs(self):
        result=self.exercise("live")
        self.assertEqual(result["status"],"LIVE_BID_ASK")
        self.assertEqual(result["allowed_send_counts"],
                         {"71":1,"59":1,"1":1,"2":1})
        self.assertFalse(result["live_quote"]["broker_valuation_time_proven"])
        self.assertEqual(result["order_send_count"],0)
    def test_missing_or_delayed_stays_unavailable(self):
        for mode in ("no_quote","delayed"):
            with self.subTest(mode=mode):
                result=self.exercise(mode)
                self.assertEqual(result["status"],"NO_LIVE_BID_ASK")
                self.assertEqual(result["order_send_count"],0)
    def test_unexpected_sdk_message_denied(self):
        result=self.exercise("unsafe")
        self.assertEqual(result["status"],"PROBE_UNSAFE_SEND")
        self.assertEqual(result["order_send_count"],0)
        self.assertNotIn("3",result["allowed_send_counts"])
    def test_handshake_timeout_still_fails_closed(self):
        result=self.exercise("handshake_timeout")
        self.assertEqual(result["status"],"HANDSHAKE_TIMEOUT")
        self.assertEqual(result["order_send_count"],0)
        self.assertNotIn("1",result["allowed_send_counts"])


if __name__=="__main__":
    unittest.main()

