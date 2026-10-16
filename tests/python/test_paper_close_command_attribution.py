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

    def test_invalid_reason_is_rejected_and_future_quote_omitted(self):
        state=closing_state()
        with patch.object(d,"paper_cmd") as send:
            with self.assertRaisesRegex(d.DriverError,"unknown close trigger"):
                d.prepare_send(state,Path("/unused"),"b","s","t","o",
                               "CLOSE",exit_reason="ARBITRARY_UNVERIFIED_CAUSE")
            send.assert_not_called()
        self.assertIsNone(state["pending"])
        evidence=d.freeze_exit_trigger("uncommitted","MAX_ADVERSE_MOVE",25000,
            {"bid":1.1,"ask":1.12,"observed_at_ms":d.now_ms()+10000})
        self.assertEqual(evidence["quote_evidence_status"],"UNVERIFIED_DIAGNOSTIC_OMITTED")
        self.assertNotIn("quote_bid",evidence)

    def test_inverted_or_malformed_quote_is_not_claimed_as_evidence(self):
        for bad in (
            {"observed_at_ms":d.now_ms()-20,"bid":1.125,"ask":1.12},
            {"observed_at_ms":d.now_ms()-20,"bid":"not-a-number","ask":1.12},
            {"observed_at_ms":d.now_ms()-20,"bid":1.125}):
            with self.subTest(bad=bad):
                evidence=d.freeze_exit_trigger("uncommitted","MODEL_REVERSAL",25000,bad)
                self.assertEqual(evidence["reason_code"],"MODEL_REVERSAL")
                self.assertEqual(evidence["quote_evidence_status"],"UNVERIFIED_DIAGNOSTIC_OMITTED")
                self.assertNotIn("quote_observed_at_ms",evidence)

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
        self.assertEqual(receipt["broker_execution_ids"],["exec-orig-close-11"])
        self.assertEqual(receipt["broker_average_fill_price"],1.119)
        self.assertEqual(receipt["broker_fill_quantity"],25000)
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

    def test_multiple_original_broker_executions_retain_full_exact_set(self):
        state=closing_state()
        pending={"command_id":"multi-fill-original","broker_order_id":108}
        fill=correlated_fill("multi-fill-original",order=108)
        fill["broker_execution_ids"]=["exec-multi-fill-original","exec-second-fill"]
        d.record_correlated_close(state,pending,fill,0)
        receipt=state["close_command_attribution"][0]
        self.assertEqual(receipt["broker_execution_ids"],fill["broker_execution_ids"])
        changed=dict(fill,broker_execution_ids=list(reversed(fill["broker_execution_ids"])))
        with self.assertRaisesRegex(d.DriverError,"attribution mismatch"):
            d.record_correlated_close(state,pending,changed,0)

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

class DiagnosticIsolationTests(unittest.TestCase):
    def scenario(self,remaining=0.0):
        state=closing_state()
        state['close_command_attribution']={'invalid':'original diagnostic preserved'}
        state['pending']={'kind':'CLOSE','command_id':'confirmed-close',
            'broker_order_id':42,'position_before_send':25000.0}
        row=correlated_fill('confirmed-close',quantity=25000-remaining)
        snapshot={'position':remaining,'active':[],'orders':{'recent_orders':[row]}}
        returned={'ok':True,'result':{'payload':{'authoritative':True,
            'command_id':'confirmed-close','command_status':'accepted','order_id':42}}}
        return state,snapshot,returned

    def reconcile(self,state,snapshot,returned):
        with patch.object(d,'paper_cmd',return_value=returned) as query,patch.object(d,'atomic_json'):
            d.inspect_pending(state,Path('/unused'),'b','s','t','o',snapshot)
        return query

    def test_corrupt_diagnostic_cannot_block_broker_confirmed_close(self):
        state,snap,result=self.scenario();original=copy.deepcopy(state['close_command_attribution'])
        query=self.reconcile(state,snap,result)
        self.assertIsNone(state['pending']);self.assertEqual(state['position'],0.0)
        self.assertEqual(state['exits'],1);self.assertEqual(state['closed_trades'],1)
        self.assertAlmostEqual(state['gross_pnl'],-25.0)
        self.assertEqual(state['close_command_attribution'],original)
        self.assertEqual(state['last_close_attribution_error']['original_command_id'],'confirmed-close')
        self.assertEqual(query.call_count,1)

    def test_repeated_poll_cannot_double_count_confirmed_fill(self):
        state,snap,result=self.scenario();self.reconcile(state,snap,result)
        saved=copy.deepcopy(state);query=self.reconcile(state,snap,result)
        self.assertEqual(state,saved);query.assert_not_called()
        self.assertAlmostEqual(state['gross_pnl'],-25.0)

    def test_partial_fill_reconciles_and_keeps_remaining_exit_available(self):
        state,snap,result=self.scenario(15000.0);self.reconcile(state,snap,result)
        self.assertIsNone(state['pending']);self.assertEqual(state['position'],15000.0)
        self.assertAlmostEqual(state['gross_pnl'],-10.0)
        self.assertTrue(state['halt_new_entries']);self.assertEqual(state['risk_halt_reason'],'PARTIAL_FLATTEN')
        self.assertEqual(state['partial_exit_fills'],1)

    def test_conflicting_old_diagnostic_is_retained_not_used_as_order_authority(self):
        state,snap,result=self.scenario()
        prior={'original_command_id':'confirmed-close','broker_execution_id':'wrong-old-id'}
        state['close_command_attribution']=[prior.copy()]
        self.reconcile(state,snap,result)
        self.assertIsNone(state['pending']);self.assertEqual(state['close_command_attribution'],[prior])
        self.assertIn('mismatch',state['last_close_attribution_error']['reason'])

    def test_unexpected_diagnostic_exception_is_bounded_and_not_a_close_gate(self):
        state,snap,result=self.scenario()
        with patch.object(d,'record_correlated_close',side_effect=RuntimeError('x'*1000)):
            self.reconcile(state,snap,result)
        self.assertIsNone(state['pending']);self.assertAlmostEqual(state['gross_pnl'],-25.0)
        self.assertEqual(state['last_close_attribution_error']['exception_type'],'RuntimeError')
        self.assertLessEqual(len(state['last_close_attribution_error']['reason']),256)

    def test_unproven_fill_still_waits_despite_diagnostic_isolation(self):
        state,snap,result=self.scenario();snap['orders']['recent_orders'][0]['broker_execution_ambiguous']=True
        with patch.object(d,'record_correlated_close') as record:
            self.reconcile(state,snap,result)
        record.assert_not_called();self.assertIsNotNone(state['pending'])
        self.assertEqual(state['gross_pnl'],0);self.assertEqual(state['exits'],0)

    def test_healthy_diagnostic_remains_exact(self):
        state,snap,result=self.scenario();state['close_command_attribution']=[]
        self.reconcile(state,snap,result)
        self.assertNotIn('last_close_attribution_error',state)
        self.assertEqual(state['close_command_attribution'][0]['broker_execution_id'],'exec-confirmed-close')
        self.assertIsNone(state['pending'])

if __name__=="__main__":unittest.main()
