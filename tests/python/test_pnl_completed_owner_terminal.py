"""Reconcile native completedOrder(Filled) only with owner and real fills."""
import unittest
from test_pnl_accounting import NOW, commission, fill, period, send, terminal
import hepta_pnl_accounting as a

def completed(f,**changes):
    e=dict(f)
    e.update(event="broker_completed_order",status="Filled",
       source="ib-api-callback",broker_callback_type="completedOrder",
       risk_code="IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED",broker_error_code=0,
       order_id=0,broker_client_id=0,side="",instrument="",trace_id="",
       req_id="",client_req_id="",qty=0,price=0,ts_ms=f["ts_ms"]+2,
       broker_execution_id="")
    e.update(changes)
    return e

def reconciled(s,f,**changes):
    e=dict(s)
    e.update(event="order_owner_reconciled_terminal",status="terminal",
       risk_code="ORDER_OWNER_RECONCILED_TERMINAL",source="agent.tool:agent",
       req_id="separate-control",client_req_id="separate-control",
       broker_client_id=-1,qty=0,price=0,ts_ms=f["ts_ms"]+3)
    e.update(changes)
    return e

class CompletedOwnerTests(unittest.TestCase):
    def setUp(self):
        self.s=send();self.f=fill(self.s,qty=.4)
        self.base=[self.s,self.f,commission(self.f)]
        self.c=completed(self.f);self.o=reconciled(self.s,self.f)
        self.reference=self.replay(self.base)
        self.assertEqual(self.reference["settlement_pending_command_ids"],["cmd1"])

    def replay(self,items):
        return a.replay(items,period(),"BTC.USD",NOW)

    def test_full_triple_native_witness_matures_only_original_command(self):
        out=self.replay(self.base+[self.c,self.o])
        self.assertEqual(out["settlement_pending_command_ids"],[])
        self.assertEqual(out["owner_terminal_witnesses_total"],1)
        w=out["owner_terminal_witnesses"][0]
        self.assertEqual(w["original_order_id"],1)
        self.assertEqual(w["completed_order_numeric_id"],0)
        self.assertFalse(w["zero_fill_imputed"])
        self.assertIn("SAME_EPOCH_EXEC_DETAILS",w["basis"])
        for field in ("execution_count","fees_complete","fees_known_by_currency","executions"):
            self.assertEqual(out[field],self.reference[field],field)
        self.assertEqual(out["book"],self.reference["book"])
        self.assertNotEqual(self.o["req_id"],self.s["req_id"])

    def test_none_or_single_component_never_concludes(self):
        for extra in ([],[self.c],[self.o]):
            with self.subTest(extra=extra):
                out=self.replay(self.base+extra)
                self.assertEqual(out["settlement_pending_command_ids"],["cmd1"])
                self.assertEqual(out["owner_terminal_witnesses_total"],0)

    def test_repeated_native_callback_and_reordering_idempotent(self):
        sample=self.base+[self.c,self.o]
        self.assertEqual(self.replay(sample),self.replay(list(reversed(sample))*3))

    def test_completed_scope_collision_and_untrusted_subject_rejected(self):
        variants={
          "source":"agent.tool:agent","broker_callback_type":"orderStatus",
          "risk_code":"IB_FILLED_ECONOMIC_EVIDENCE_ACCEPTED",
          "status":"Cancelled","broker_error_code":202,
          "broker_client_id":701,"order_id":1,"account":"DU_OTHER",
          "execution_domain":"LIVE","instrument":"EUR.USD","side":"SELL",
          "req_id":"foreign","trace_id":"foreign",
          "venue_correlation_id":"hepta-v1-sha256:"+"b"*64}
        for key,value in variants.items():
            with self.subTest(key=key):
                out=self.replay(self.base+[completed(self.f,**{key:value}),self.o])
                self.assertEqual(out["settlement_pending_command_ids"],["cmd1"])

    def test_forged_owner_or_wrong_original_binding_rejected(self):
        variants={
          "source":"agent.tool:foreign","status":"Submitted",
          "risk_code":"GENERIC_CANCEL_ACK","account":"DU_OTHER",
          "execution_domain":"LIVE","instrument":"EUR.USD","side":"SELL",
          "trace_id":"foreign","order_id":999,"req_id":"",
          "client_req_id":"foreign","venue_correlation_id":"hepta-v1-sha256:"+"b"*64}
        for key,value in variants.items():
            with self.subTest(key=key):
                out=self.replay(self.base+[self.c,reconciled(self.s,self.f,**{key:value})])
                self.assertEqual(out["settlement_pending_command_ids"],["cmd1"])

    def test_no_execution_does_not_invent_zero_fill(self):
        with self.assertRaisesRegex(a.EvidenceError,"SUBMITTED_COMMAND_WITHOUT_FILL_OR_ZERO_FILL_PROOF"):
            self.replay([self.s,self.c,self.o])

    def test_wrong_completed_epoch_or_time_is_not_borrowed(self):
        cases=[
          {"broker_service_epoch":"other"},{"broker_connection_epoch":4},
          {"broker_connection_epoch":0},{"ts_ms":self.s["ts_ms"]-1},
          {"ts_ms":NOW+1},{"ts_ms":self.o["ts_ms"]+1}]
        for case in cases:
            with self.subTest(case=case):
                out=self.replay(self.base+[completed(self.f,**case),self.o])
                self.assertEqual(out["settlement_pending_command_ids"],["cmd1"])

    def test_other_native_status_does_not_prove_full_fill(self):
        for status in ("Cancelled","Submitted","Inactive","PendingCancel"):
            with self.subTest(status=status):
                out=self.replay(self.base+[completed(self.f,status=status),self.o])
                self.assertEqual(out["settlement_pending_command_ids"],["cmd1"])

    def test_late_nonterminal_status_blocks_reconciliation(self):
        late=terminal(self.f,"Submitted");late["ts_ms"]=self.o["ts_ms"]+1
        out=self.replay(self.base+[self.c,self.o,late])
        self.assertEqual(out["settlement_pending_command_ids"],["cmd1"])

    def test_existing_orderstatus_terminal_precedes_native_witness(self):
        out=self.replay(self.base+[terminal(self.f,"Filled"),self.c,self.o])
        self.assertEqual(out["settlement_pending_command_ids"],[])
        self.assertEqual(out["owner_terminal_witnesses_total"],0)

    def test_native_pair_for_another_order_cannot_close_this_one(self):
        s2=send(cmd="cmd2",oid=24,at=120000)
        f2=fill(s2,eid="e2.1",at=120010)
        out=self.replay(self.base+[s2,f2,commission(f2),self.c,self.o])
        self.assertEqual(out["settlement_pending_command_ids"],["cmd2"])
        self.assertEqual(out["owner_terminal_witnesses_total"],1)

    def test_broker_error_202_keeps_precedence_and_no_double_count(self):
        from test_pnl_broker_202_cancel import order_cancel_error
        out=self.replay(self.base+[order_cancel_error(self.f),self.c,self.o])
        self.assertEqual(out["settlement_pending_command_ids"],[])
        self.assertEqual(out["broker_cancel_witnesses_total"],1)
        self.assertEqual(out["owner_terminal_witnesses_total"],0)

if __name__=="__main__":
    unittest.main()
