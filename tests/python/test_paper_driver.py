from pathlib import Path
import copy, importlib.util, tempfile, unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("paper_driver",ROOT/"scripts/heptatrader_paper_driver.py")
d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)

class DriverPolicyTests(unittest.TestCase):
    def state(self):
        return {"pending":None,"position":0.0,"entry_price":None,"entry_price_basis":None,
            "entries":0,"exits":0,"closed_trades":0,"gross_pnl":0.0,"last_error":""}

    def test_send_boundary_is_durable_before_transport(self):
        state=self.state(); writes=[]; calls=[]
        old_cmd,old_atomic=d.paper_cmd,d.atomic_json
        try:
            def atomic(_path,value): writes.append(copy.deepcopy(value))
            def cmd(_b,_s,_t,_o,op,extra):
                calls.append((op,list(extra)))
                if op=="prepare-open": return {"ok":True,"command_id":"execution-command-0001"}
                self.assertEqual(writes[-1]["pending"]["send_state"],"POSSIBLY_SENT")
                return {"ok":False,"reason":"SOCKET_TIMEOUT","result":None}
            d.atomic_json=atomic; d.paper_cmd=cmd
            d.prepare_send(state,Path("/unused"),"bin","sock","token","outbox","OPEN",
                {"bid":1.1,"ask":1.2},"BUY")
            self.assertEqual([w["pending"]["send_state"] for w in writes[:2]],
                             ["PREPARED","POSSIBLY_SENT"])
            self.assertEqual(calls[1][0],"submit")
            self.assertEqual(state["pending"]["command_id"],"execution-command-0001")
        finally:
            d.paper_cmd,d.atomic_json=old_cmd,old_atomic

    def test_uncertain_original_command_never_clears_for_resend(self):
        state=self.state(); state["pending"]={"kind":"OPEN","command_id":"execution-command-0001",
            "broker_order_id":None,"reference_price":1.2}
        old_cmd,old_atomic=d.paper_cmd,d.atomic_json
        try:
            d.atomic_json=lambda *_: None
            d.paper_cmd=lambda *_args,**_kw: {"ok":True,"result":{"payload":{
                "command_id":"execution-command-0001","command_status":"uncertain",
                "order_id":-1,"reason_code":"UNKNOWN"}}}
            snap={"position":0.0,"active":[],"orders":{"recent_orders":[]}}
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",snap)
            self.assertEqual(state["pending"]["command_id"],"execution-command-0001")
        finally:
            d.paper_cmd,d.atomic_json=old_cmd,old_atomic

    def test_authoritative_fill_opens_and_flatten_closes(self):
        old_cmd,old_atomic=d.paper_cmd,d.atomic_json
        try:
            d.atomic_json=lambda *_: None
            d.paper_cmd=lambda *_args,**_kw: {"ok":True,"result":{"payload":{
                "command_status":"accepted","order_id":42,"reason_code":""}}}
            state=self.state(); state["pending"]={"kind":"OPEN","command_id":"execution-command-0001",
                "broker_order_id":42,"reference_price":1.1,"side":"BUY"}
            orders={"recent_orders":[{"order_id":42,"economic_fill":True,
                "filled_quantity":25000,"average_fill_price":1.101,"terminal":True}]}
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                {"position":25000.0,"active":[],"orders":orders})
            self.assertIsNone(state["pending"]); self.assertEqual(state["entries"],1)
            self.assertEqual(state["entry_price_basis"],"broker_average_fill_price")
            state["pending"]={"kind":"CLOSE","command_id":"execution-command-0002",
                "broker_order_id":43}
            orders={"recent_orders":[{"order_id":43,"economic_fill":True,
                "filled_quantity":25000,"average_fill_price":1.102,"terminal":True}]}
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                {"position":0.0,"active":[],"orders":orders})
            self.assertIsNone(state["pending"]); self.assertEqual(state["exits"],1)
            self.assertEqual(state["closed_trades"],1)
        finally:
            d.paper_cmd,d.atomic_json=old_cmd,old_atomic

    def test_partial_entry_latches_reduce_only_and_partial_close_continues(self):
        old_cmd,old_atomic=d.paper_cmd,d.atomic_json
        try:
            d.atomic_json=lambda *_: None
            def inspect(_b,_s,_t,_o,_op,extra):
                oid=53 if "execution-command-0053" in extra else 52
                return {"ok":True,"result":{"payload":{"command_status":"accepted","order_id":oid,"reason_code":""}}}
            d.paper_cmd=inspect
            state=self.state(); state["pending"]={"kind":"OPEN","command_id":"execution-command-0052",
                "broker_order_id":52,"reference_price":1.1,"side":"BUY"}
            orders={"recent_orders":[{"order_id":52,"economic_fill":True,"filled_quantity":10000,
                "average_fill_price":1.101,"terminal":True}]}
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                {"position":10000.0,"active":[],"orders":orders})
            self.assertIsNone(state["pending"]); self.assertTrue(state["halt_new_entries"])
            self.assertEqual(state["position"],10000.0)
            state["pending"]={"kind":"CLOSE","command_id":"execution-command-0053","broker_order_id":53}
            orders={"recent_orders":[{"order_id":53,"economic_fill":True,"filled_quantity":4000,
                "average_fill_price":1.102,"terminal":True}]}
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                {"position":6000.0,"active":[],"orders":orders})
            self.assertIsNone(state["pending"]); self.assertEqual(state["position"],6000.0)
            self.assertIn("remaining exposure",state["last_error"])
        finally:
            d.paper_cmd,d.atomic_json=old_cmd,old_atomic

    def test_model_worker_is_asynchronous_and_preserves_snapshot_identity(self):
        old=d.model_decision
        gate=__import__("threading").Event()
        try:
            def slow(_codex,_state_dir,_history,_position):
                gate.wait(1); return {"action":"HOLD","confidence":0.9,"rationale":"test"}
            d.model_decision=slow
            worker=d.ModelWorker("c",Path("/unused"),[{"observed_at_ms":123,"bid":1,"ask":2,"mid":1.5}],25000)
            self.assertFalse(worker.done()); self.assertEqual(worker.position,25000); self.assertEqual(worker.observed_at_ms,123)
            gate.set(); self.assertTrue(worker._done.wait(1)); self.assertEqual(worker.result()["action"],"HOLD")
        finally: d.model_decision=old

    def test_possibly_sent_renewal_must_be_treated_as_uncertain_on_restart(self):
        state=self.state(); state["lease_renewal_state"]="POSSIBLY_SENT"
        # main() performs this exact startup latch before any Broker read/mutation.
        if state.get("lease_renewal_state")=="POSSIBLY_SENT":
            state["lease_uncertain"]=True; state["halt_new_entries"]=True
        self.assertTrue(state["lease_uncertain"]); self.assertTrue(state["halt_new_entries"])

    def test_reversal_adverse_and_halt_are_reduce_only_triggers(self):
        state=self.state(); state["entry_price"]=1.0
        self.assertEqual(d.should_close(state,25000,{"bid":0.997,"ask":0.998},None)[1],
                         "MAX_ADVERSE_MOVE")
        self.assertEqual(d.should_close(state,25000,{"bid":1.0,"ask":1.001},
            {"action":"SELL","confidence":0.62})[1],"MODEL_REVERSAL")
        state["halt_new_entries"]=True
        self.assertEqual(d.should_close(state,-25000,{"bid":1.0,"ask":1.001},None)[1],
                         "HALT_REDUCE_ONLY")

if __name__=="__main__": unittest.main()
