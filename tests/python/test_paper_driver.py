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

    def test_uncertain_renewal_latches_without_reprovision_or_blind_retry(self):
        state=self.state(); state.update(lease_renewal_state="POSSIBLY_SENT",lease_uncertain=True)
        old_run,old_atomic=d.run,d.atomic_json
        try:
            d.run=lambda *_: self.fail("uncertain renewal must first enter audited recovery")
            d.atomic_json=lambda *_: None
            d.maybe_renew(state,Path("/unused"),"s","s","t",1000,0.0,0,None)
            self.assertTrue(state["owner_fence_recovery_required"])
            self.assertTrue(state["halt_new_entries"])
        finally: d.run,d.atomic_json=old_run,old_atomic

    def test_generation_zero_mismatch_never_creates_a_new_session(self):
        state=self.state();state.update(lease_generation=1,lease_last_renew_ms=0)
        old_run,old_atomic=d.run,d.atomic_json;calls=[]
        try:
            def run(argv,*_):
                calls.append(argv)
                return __import__("subprocess").CompletedProcess(argv,3,
                    '{"accepted":false,"reason_code":"SESSION_LEASE_GENERATION_MISMATCH","lease_generation":0}',"")
            d.run=run;d.atomic_json=lambda *_:None
            d.maybe_renew(state,Path("/unused"),"s","s","t",1000,0.0,0,None)
            self.assertEqual(len(calls),1);self.assertIn("renew",calls[0])
            self.assertFalse(hasattr(d,"_provision_command"))
            self.assertTrue(state["owner_fence_recovery_required"])
            self.assertTrue(state["halt_new_entries"])
        finally:d.run,d.atomic_json=old_run,old_atomic

    def test_owner_fence_latches_before_any_submit_and_cannot_be_rearmed_as_a_lease(self):
        state=self.state(); writes=[]; calls=[]
        old_cmd,old_atomic,old_run=d.paper_cmd,d.atomic_json,d.run
        try:
            def refused(*args):
                calls.append(args[4])
                return {"ok":False,"reason":"SESSION_OWNER_FENCED"}
            d.paper_cmd=refused
            d.atomic_json=lambda _path,value: writes.append(copy.deepcopy(value))
            with self.assertRaisesRegex(d.DriverError,"SESSION_OWNER_FENCED"):
                d.prepare_send(state,Path("/unused"),"binary","socket","token","outbox","OPEN",{"bid":1.1,"ask":1.1001},"BUY")
            self.assertEqual(calls,["prepare-open"])
            self.assertTrue(writes[-1]["owner_fence_recovery_required"])
            self.assertTrue(state["halt_new_entries"])
            self.assertIsNone(state.get("pending"))
            def forbidden(*args,**kwargs): raise AssertionError("lease control must not bypass owner recovery")
            d.run=forbidden
            d.maybe_renew(state,Path("/unused"),"s","s","t",1000,0.0,0,None)
            with self.assertRaisesRegex(d.DriverError,"audited recovery"):
                d.rearm_session(state,Path("/unused"),"cli",1)
        finally:
            d.paper_cmd,d.atomic_json,d.run=old_cmd,old_atomic,old_run

    def test_recovery_requires_current_ack_and_preview_then_clears_only_its_halt(self):
        from unittest.mock import patch
        import subprocess
        state=self.state();state.update(lease_generation=1,gross_pnl=6.0)
        history=[];calls=[]
        def save(_p,value):history.append(copy.deepcopy(value))
        def snap(*_):
            return {"position":0.0,"gross_position":0.0,"active":[],"quote":{"ask":1.1},
                "owner_health":{"owner_fenced":len(calls)==0,"owner_recovery_only":len(calls)==0,"mutation_ready":len(calls)>0},
                "service_epoch":"epoch-1","service_generation":1}
        def run(argv,*_):
            calls.append(argv)
            if "paper-resume" in argv:
                self.assertEqual(history[-1]["owner_recovery_phase"],"RESUME_SENT")
                return subprocess.CompletedProcess(argv,0,'{"accepted":true,"reason_code":"SESSION_OWNER_RESUMED","lease_generation":2}',"")
            self.assertEqual(argv,["cli","paper-on"])
            return subprocess.CompletedProcess(argv,0,"","")
        def preview(*args):
            self.assertEqual(args[1],"risk.preview_order")
            self.assertTrue(state["halt_new_entries"])
            self.assertEqual(history[-1]["owner_recovery_phase"],"VERIFY_PREVIEW")
            return {"approved":True,"preview_permit":"private-test-permit","service_epoch":"epoch-1","service_fencing_generation":1}
        with patch.object(d,"atomic_json",save),patch.object(d,"snapshot",snap),patch.object(d,"run",run),patch.object(d,"native",preview):
            d.latch_owner_fence(state,Path("/unused"),"SESSION_OWNER_FENCED")
            self.assertTrue(d.recover_owner(state,Path("/unused"),"cli","s","s","t",1000))
        self.assertFalse(state["halt_new_entries"])
        self.assertFalse(state["owner_fence_recovery_required"])
        self.assertEqual(state["owner_recovery_phase"],"VERIFIED")
        self.assertEqual(state["gross_pnl"],6.0)
        self.assertNotIn("private-test-permit",str(history))
        self.assertEqual(len(calls),2)

    def test_failed_preview_keeps_latch_and_does_not_repeat_resume_on_next_check(self):
        from unittest.mock import patch
        import subprocess
        state=self.state();state.update(lease_generation=2,owner_fence_recovery_required=True,
            owner_recovery_phase="VERIFY_PREVIEW",halt_new_entries=True)
        snap={"position":0.0,"gross_position":0.0,"active":[],"quote":{"ask":1.1},
            "owner_health":{"owner_fenced":False,"owner_recovery_only":False,"mutation_ready":True},
            "service_epoch":"epoch-1","service_generation":1}
        with patch.object(d,"atomic_json"),patch.object(d,"snapshot",return_value=snap), \
             patch.object(d,"run",return_value=subprocess.CompletedProcess([],0,"","")) as run, \
             patch.object(d,"native",side_effect=d.DriverError("quote stale")):
            with self.assertRaisesRegex(d.DriverError,"quote stale"):
                d.recover_owner(state,Path("/unused"),"cli","s","s","t",1000)
            run.assert_called_once_with(["cli","paper-on"],10)
        self.assertTrue(state["owner_fence_recovery_required"])
        self.assertTrue(state["halt_new_entries"])
        self.assertEqual(state["owner_recovery_phase"],"VERIFY_PREVIEW")

    def test_exposure_or_pending_prevents_any_resume_control(self):
        from unittest.mock import patch
        state=self.state();state["owner_fence_recovery_required"]=True
        with patch.object(d,"run") as run,patch.object(d,"snapshot",return_value={"active":[],"gross_position":1.0}):
            with self.assertRaisesRegex(d.DriverError,"zero total strategy position"):
                d.recover_owner(state,Path("/unused"),"cli","s","s","t",1000)
            state["pending"]={"command_id":"original-command"}
            with self.assertRaisesRegex(d.DriverError,"pending command"):
                d.recover_owner(state,Path("/unused"),"cli","s","s","t",1000)
            run.assert_not_called()

    def test_renew_success_cannot_clear_an_unrelated_halt(self):
        state=self.state();state.update(halt_new_entries=True,lease_renewal_owned_halt=False)
        d._finish_renewal(state,2)
        self.assertTrue(state["halt_new_entries"])
        state.update(lease_renewal_owned_halt=True,risk_halt_reason="PARTIAL_ENTRY")
        d._finish_renewal(state,3)
        self.assertTrue(state["halt_new_entries"])

    def test_fence_is_latched_before_quote_failure_can_hide_it(self):
        from unittest.mock import patch
        state=self.state()
        def read(_cli,tool,*_):
            if tool=="system.get_health":return {"execution_authoritative_health":{"source":"IB","authoritative":True,
                "owner_fenced":True,"owner_recovery_only":False,"mutation_ready":False,"mutation_reason":"SESSION_OWNER_FENCED"}}
            raise d.DriverError("AUTHORITATIVE_QUOTE_STALE")
        with patch.object(d,"native",read),patch.object(d,"atomic_json"):
            with self.assertRaises(d.DriverError):d.snapshot("cli",state,Path("/unused"))
        self.assertTrue(state["owner_fence_recovery_required"])
        self.assertTrue(state["halt_new_entries"])
        self.assertEqual(state["owner_fence_block_reason"],"SESSION_OWNER_FENCED")

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
