from pathlib import Path
import copy
import importlib.util
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location(
    "paper_driver_exit_provenance",ROOT/"scripts/heptatrader_paper_driver.py")
d=importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def correlated_fill(cmd, order=42, *, quantity=25000, price=1.119, side="SELL"):
    import hashlib
    return {"order_id":order, "command_id":cmd, "side":side,
            "venue_correlation_id":"hepta-v1-sha256:"+
                hashlib.sha256(cmd.encode()).hexdigest(),
            "broker_execution_id":"exec-"+cmd,
            "broker_execution_ambiguous":False,
            "economic_fill":True,
            "filled_quantity":quantity,
            "average_fill_price":price,
            "terminal":True}


def closing_state():
    return {"pending":None,"position":25000.0,"entry_price":1.1200,
            "entries":1,"exits":0,"closed_trades":0,"gross_pnl":0.0,
            "last_error":"","last_exit_reason":"STALE_PAST_TRADE"}


class ExactCloseCommandProvenance(unittest.TestCase):
    def test_freezes_original_cause_before_command_send_and_remains_immutable(self):
        state=closing_state()
        quote={"bid":1.117,"ask":1.1173,"observed_at_ms":d.now_ms()-150}
        events=[]
        def command(_b,_s,_t,_o,op,_extra):
            events.append(op)
            if op=="prepare-close":
                return {"ok":True,"command_id":"exact-fx-close-1"}
            self.assertEqual(state["pending"]["send_state"],"POSSIBLY_SENT")
            self.assertEqual(state["pending"]["exit_trigger"]["reason_code"],
                             "MAX_ADVERSE_MOVE")
            return {"ok":False,"transport_ok":False,"reason":"SOCKET_TIMEOUT"}
        with patch.object(d,"paper_cmd",side_effect=command),patch.object(d,"atomic_json"):
            d.prepare_send(state,Path("/unused"),"b","s","t","o","CLOSE",quote,
                           exit_reason="MAX_ADVERSE_MOVE")
        trigger=state["pending"]["exit_trigger"]
        self.assertEqual(trigger["original_command_id"],"exact-fx-close-1")
        self.assertEqual(trigger["quote_observed_at_ms"],quote["observed_at_ms"])
        self.assertEqual(trigger["quote_bid"],quote["bid"])
        self.assertEqual(trigger["reason_source"],"DRIVER_EVALUATED")
        self.assertEqual(events,["prepare-close","submit"])
        state["last_exit_reason"]="MODEL_REVERSAL"
        self.assertEqual(state["pending"]["exit_trigger"],trigger)
        self.assertEqual(state["pending"]["send_state"],"POSSIBLY_SENT")
        self.assertNotIn("close_command_attribution",state)

    def test_invalid_trigger_is_rejected_before_preparing_original_command(self):
        state=closing_state()
        with patch.object(d,"paper_cmd") as send:
            with self.assertRaisesRegex(d.DriverError,"unknown close trigger"):
                d.prepare_send(state,Path("/unused"),"b","s","t","o",
                               "CLOSE",exit_reason="ARBITRARY_UNVERIFIED_CAUSE")
            with self.assertRaisesRegex(d.DriverError,"quote evidence invalid"):
                d.prepare_send(state,Path("/unused"),"b","s","t","o",
                               "CLOSE",{"bid":1.1,"ask":1.12,
                                "observed_at_ms":d.now_ms()+10000},
                               exit_reason="MAX_ADVERSE_MOVE")
            send.assert_not_called()
        self.assertIsNone(state["pending"])

    def test_inverted_or_malformed_quote_never_prepares_original_command(self):
        state=closing_state()
        with patch.object(d,"paper_cmd") as send:
            for bad in (
                {"observed_at_ms":d.now_ms()-20,"bid":1.125,"ask":1.12},
                {"observed_at_ms":d.now_ms()-20,"bid":"not-a-number","ask":1.12},
                {"observed_at_ms":d.now_ms()-20,"bid":1.125}):
                with self.subTest(bad=bad):
                    with self.assertRaisesRegex(d.DriverError,"quote evidence invalid"):
                        d.prepare_send(state,Path("/unused"),"b","s","t","o",
                                       "CLOSE",bad,exit_reason="MODEL_REVERSAL")
            send.assert_not_called()

    def test_open_cannot_be_mislabeled_as_exit(self):
        state=closing_state()
        with patch.object(d,"paper_cmd") as send:
            with self.assertRaisesRegex(d.DriverError,"exit trigger cannot"):
                d.prepare_send(state,Path("/unused"),"b","s","t","o","OPEN",
                               {"bid":1.1,"ask":1.12},"BUY",
                               exit_reason="MAX_ADVERSE_MOVE")
            send.assert_not_called()

    def test_broker_accepted_exact_close_keeps_command_and_execution_reason(self):
        state=closing_state()
        trigger=d.freeze_exit_trigger("orig-close-11","MODEL_REVERSAL",
            state["position"],{"bid":1.119,"ask":1.120,
                "observed_at_ms":d.now_ms()-300})
        state["pending"]={"kind":"CLOSE","command_id":"orig-close-11",
            "broker_order_id":42,"exit_trigger":trigger,
            "position_before_send":25000.0}
        row=correlated_fill("orig-close-11",order=42)
        returned={"ok":True,"result":{"payload":{
            "authoritative":True,"command_id":"orig-close-11",
            "command_status":"accepted","order_id":42}}}
        with patch.object(d,"paper_cmd",return_value=returned),patch.object(d,"atomic_json"):
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                 {"position":0.0,"active":[],"orders":{"recent_orders":[row]}})
        self.assertIsNone(state["pending"])
        self.assertEqual(state["exits"],1)
        history=state["close_command_attribution"]
        self.assertEqual(len(history),1)
        receipt=history[0]
        self.assertEqual(receipt["original_command_id"],"orig-close-11")
        self.assertEqual(receipt["broker_execution_id"],"exec-orig-close-11")
        self.assertEqual(receipt["broker_order_id"],42)
        self.assertEqual(receipt["exit_trigger"],trigger)
        self.assertFalse(receipt["terminal_partial_exit"])
        self.assertTrue(receipt["broker_terminal_observed"])
        self.assertIn("NOT_BROKER_CAUSAL_PROOF",receipt["evidence_scope"])
        state["last_exit_reason"]="MAX_ADVERSE_MOVE"
        self.assertEqual(receipt["exit_trigger"]["reason_code"],"MODEL_REVERSAL")

    def test_partial_close_keeps_own_original_command_reason(self):
        state=closing_state()
        state["pending"]={"kind":"CLOSE","command_id":"partial-close-1",
            "broker_order_id":43,"position_before_send":25000,
            "exit_trigger":d.freeze_exit_trigger("partial-close-1",
                "HALT_REDUCE_ONLY",25000)}
        row=correlated_fill("partial-close-1",order=43,quantity=10000)
        returned={"ok":True,"result":{"payload":{
            "authoritative":True,"command_id":"partial-close-1",
            "command_status":"accepted","order_id":43}}}
        with patch.object(d,"paper_cmd",return_value=returned),patch.object(d,"atomic_json"):
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                  {"position":15000,"active":[],"orders":{"recent_orders":[row]}})
        self.assertEqual(state["position"],15000)
        self.assertIsNone(state["pending"])
        self.assertTrue(state["halt_new_entries"])
        r=state["close_command_attribution"][0]
        self.assertTrue(r["terminal_partial_exit"])
        self.assertEqual(r["position_after_terminal_fill"],15000)
        self.assertEqual(r["exit_trigger"]["reason_code"],"HALT_REDUCE_ONLY")

    def test_legacy_missing_reason_never_inherits_mutable_last_exit_reason(self):
        state=closing_state()
        triggerless={"command_id":"old-command", "broker_order_id":6}
        row=correlated_fill("old-command",order=6)
        d.record_correlated_close(state,triggerless,row,0)
        receipt=state["close_command_attribution"][0]
        self.assertEqual(receipt["exit_trigger"]["reason_code"],"LEGACY_UNATTRIBUTED")
        self.assertEqual(receipt["exit_trigger"]["reason_source"],"LEGACY_MISSING")
        self.assertNotEqual(receipt["exit_trigger"]["reason_code"],
                            state["last_exit_reason"])

    def test_command_cannot_be_reattributed_to_different_execution(self):
        state=closing_state()
        p={"command_id":"same-original","broker_order_id":7}
        row=correlated_fill("same-original",order=7)
        d.record_correlated_close(state,p,row,0)
        d.record_correlated_close(state,p,row,0)
        self.assertEqual(len(state["close_command_attribution"]),1)
        other=dict(row,broker_execution_id="foreign-broker-execution")
        with self.assertRaisesRegex(d.DriverError,"attribution mismatch"):
            d.record_correlated_close(state,p,other,0)
        self.assertEqual(len(state["close_command_attribution"]),1)

    def test_corrupted_or_missing_broker_execution_must_not_mint_receipt(self):
        state=closing_state()
        p={"command_id":"x","broker_order_id":10}
        with self.assertRaisesRegex(d.DriverError,"attribution missing"):
            d.record_correlated_close(state,p,{"broker_execution_id":None,
                "venue_correlation_id":"some-venue-id"},0)
        self.assertNotIn("close_command_attribution",state)

    def test_bounded_history_does_not_overwrite_latest_original(self):
        state=closing_state()
        for n in range(70):
            p={"command_id":"receipt-"+str(n),"broker_order_id":n+1}
            row=correlated_fill(p["command_id"],order=n+1)
            d.record_correlated_close(state,p,row,0)
        self.assertEqual(len(state["close_command_attribution"]),64)
        self.assertEqual(state["close_command_attribution"][0]["original_command_id"],
                         "receipt-6")
        self.assertEqual(state["close_command_attribution"][-1]["original_command_id"],
                         "receipt-69")

if __name__=="__main__":unittest.main()
