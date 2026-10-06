from pathlib import Path
import copy, importlib.util, tempfile, unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("paper_driver",ROOT/"scripts/heptatrader_paper_driver.py")
d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)

def matched_fill(order_id,command_id,side,quantity,price):
    import hashlib
    return {"order_id":order_id,"command_id":command_id,"side":side,
        "venue_correlation_id":"hepta-v1-sha256:"+hashlib.sha256(command_id.encode()).hexdigest(),
        "broker_execution_id":"exec-"+command_id,"broker_execution_ambiguous":False,
        "economic_fill":True,"filled_quantity":quantity,"average_fill_price":price,"terminal":True}

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

    def test_pending_snapshot_does_not_depend_on_quote_freshness(self):
        from unittest.mock import patch
        calls=[]
        def native(_cli,tool,*_fields):
            calls.append(tool)
            if tool=="system.get_health":
                return {"execution_mode":"PAPER","gateway_ready":True,"remote_execution_ready":True,
                    "execution_service_epoch":"epoch","execution_service_fencing_generation":1,
                    "execution_authoritative_health":{"source":"IB","authoritative":True,
                        "owner_fenced":False,"owner_recovery_only":False,"mutation_ready":True}}
            if tool=="orders.list":
                return {"source":"IB","authoritative":True,"global_active_orders_complete":True,
                    "owner_projection_complete":True,"active_order_ids":[],"recent_orders":[]}
            if tool=="portfolio.list_positions":
                return {"source":"IB","authoritative":True,
                    "positions":[{"instrument":"EUR.USD","quantity":-25000}]}
            raise AssertionError("pending reconciliation attempted "+tool)
        with patch.object(d,"native",native):
            snap=d.snapshot("cli",require_quote=False)
        self.assertEqual(calls,["system.get_health","orders.list","portfolio.list_positions"])
        self.assertEqual(snap["position"],-25000.0)
        self.assertIsNone(snap["quote"])

    def test_synchronous_application_rejection_is_not_unknown_send(self):
        state=self.state();state["position"]=-25000.0;writes=[];calls=[]
        old_cmd,old_atomic=d.paper_cmd,d.atomic_json
        try:
            def cmd(_b,_s,_t,_o,op,_extra):
                calls.append(op)
                if op=="prepare-close": return {"ok":True,"command_id":"rejected-close-1"}
                return {"ok":False,"transport_ok":True,"application_status":"error",
                    "application_reason":"AUTHORITATIVE_QUOTE_STALE",
                    "result":{"status":"error","reason_code":"AUTHORITATIVE_QUOTE_STALE","order_id":-1}}
            d.paper_cmd=cmd;d.atomic_json=lambda _p,value:writes.append(copy.deepcopy(value))
            d.prepare_send(state,Path("/unused"),"b","s","t","o","CLOSE")
        finally:d.paper_cmd,d.atomic_json=old_cmd,old_atomic
        self.assertEqual(calls,["prepare-close","submit"])
        self.assertEqual([w["pending"]["send_state"] for w in writes],
            ["PREPARED","POSSIBLY_SENT","DEFINITELY_REJECTED"])
        self.assertEqual(state["pending"]["position_before_send"],-25000.0)
        self.assertEqual(state["pending"]["application_reason"],"AUTHORITATIVE_QUOTE_STALE")
        self.assertIsNone(state["pending"]["broker_order_id"])

    def test_transport_loss_and_typed_uncertainty_remain_original_unknown(self):
        for submitted in (
            {"ok":False,"transport_ok":False,"reason":"SOCKET_TIMEOUT","result":None},
            {"ok":False,"transport_ok":True,"application_status":"uncertain",
             "application_reason":"IB_FLATTEN_OUTCOME_UNCERTAIN",
             "result":{"status":"uncertain","reason_code":"IB_FLATTEN_OUTCOME_UNCERTAIN"}}):
            state=self.state();state["position"]=-25000.0
            old_cmd,old_atomic=d.paper_cmd,d.atomic_json
            try:
                d.atomic_json=lambda *_:None
                d.paper_cmd=lambda _b,_s,_t,_o,op,_extra: (
                    {"ok":True,"command_id":"same-original-close"} if op=="prepare-close" else submitted)
                d.prepare_send(state,Path("/unused"),"b","s","t","o","CLOSE")
            finally:d.paper_cmd,d.atomic_json=old_cmd,old_atomic
            self.assertEqual(state["pending"]["send_state"],"POSSIBLY_SENT")
            self.assertEqual(state["pending"]["command_id"],"same-original-close")

    def test_application_acceptance_is_acknowledged_but_not_assumed_filled(self):
        state=self.state();old_cmd,old_atomic=d.paper_cmd,d.atomic_json
        try:
            d.atomic_json=lambda *_:None
            d.paper_cmd=lambda _b,_s,_t,_o,op,_extra: (
                {"ok":True,"command_id":"accepted-open"} if op=="prepare-open" else
                {"ok":True,"transport_ok":True,"application_status":"ok",
                 "application_reason":"","result":{"status":"ok","order_id":44}})
            d.prepare_send(state,Path("/unused"),"b","s","t","o","OPEN",{"bid":1.1,"ask":1.2},"BUY")
        finally:d.paper_cmd,d.atomic_json=old_cmd,old_atomic
        self.assertEqual(state["pending"]["send_state"],"ACKNOWLEDGED")
        self.assertEqual(state["pending"]["broker_order_id"],44)
        self.assertEqual(state["entries"],0)

    def test_definite_non_send_clears_only_after_unchanged_authoritative_state(self):
        base=self.state();base.update(position=-25000.0,entries=21,exits=20,
            closed_trades=20,gross_pnl=142.5)
        pending={"kind":"CLOSE","command_id":"definite-close","broker_order_id":None,
            "send_state":"DEFINITELY_REJECTED","position_before_send":-25000.0,
            "application_reason":"AUTHORITATIVE_QUOTE_STALE"}
        old_cmd,old_atomic=d.paper_cmd,d.atomic_json
        try:
            d.paper_cmd=lambda *_:self.fail("definite rejection must not query or resubmit")
            d.atomic_json=lambda *_:None
            for snap in ({"position":0.0,"active":[],"orders":{}},
                         {"position":-25000.0,"active":[77],"orders":{}}):
                state=copy.deepcopy(base);state["pending"]=copy.deepcopy(pending)
                d.inspect_pending(state,Path("/unused"),"b","s","t","o",snap)
                self.assertIsNotNone(state["pending"])
                self.assertEqual(state["gross_pnl"],142.5)
            state=copy.deepcopy(base);state["pending"]=copy.deepcopy(pending)
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                {"position":-25000.0,"active":[],"orders":{}})
        finally:d.paper_cmd,d.atomic_json=old_cmd,old_atomic
        self.assertIsNone(state["pending"])
        self.assertEqual((state["entries"],state["exits"],state["closed_trades"]),(21,20,20))
        self.assertEqual(state["gross_pnl"],142.5)
        self.assertEqual(state["position"],-25000.0)

    def test_uncertain_original_command_never_clears_for_resend(self):
        state=self.state(); state["pending"]={"kind":"OPEN","command_id":"execution-command-0001",
            "broker_order_id":None,"reference_price":1.2}
        old_cmd,old_atomic=d.paper_cmd,d.atomic_json
        try:
            d.atomic_json=lambda *_: None
            d.paper_cmd=lambda *_args,**_kw: {"ok":True,"result":{"payload":{
                "authoritative":True,"command_id":"execution-command-0001","command_status":"uncertain",
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
            def inspect(*_args,**_kw):
                current=state["pending"]
                return {"ok":True,"result":{"payload":{"authoritative":True,
                    "command_id":current["command_id"],"command_status":"accepted",
                    "order_id":current["broker_order_id"],"reason_code":""}}}
            d.paper_cmd=inspect
            state=self.state(); state["pending"]={"kind":"OPEN","command_id":"execution-command-0001",
                "broker_order_id":42,"reference_price":1.1,"side":"BUY"}
            orders={"recent_orders":[matched_fill(42,"execution-command-0001","BUY",25000,1.101)]}
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                {"position":25000.0,"active":[],"orders":orders})
            self.assertIsNone(state["pending"]); self.assertEqual(state["entries"],1)
            self.assertEqual(state["entry_price_basis"],"broker_average_fill_price")
            state["pending"]={"kind":"CLOSE","command_id":"execution-command-0002",
                "broker_order_id":43}
            orders={"recent_orders":[matched_fill(43,"execution-command-0002","SELL",25000,1.102)]}
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
                return {"ok":True,"result":{"payload":{"authoritative":True,
                    "command_id":"execution-command-0053" if oid==53 else "execution-command-0052",
                    "command_status":"accepted","order_id":oid,"reason_code":""}}}
            d.paper_cmd=inspect
            state=self.state(); state["pending"]={"kind":"OPEN","command_id":"execution-command-0052",
                "broker_order_id":52,"reference_price":1.1,"side":"BUY"}
            orders={"recent_orders":[matched_fill(52,"execution-command-0052","BUY",10000,1.101)]}
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                {"position":10000.0,"active":[],"orders":orders})
            self.assertIsNone(state["pending"]); self.assertTrue(state["halt_new_entries"])
            self.assertEqual(state["position"],10000.0)
            state["pending"]={"kind":"CLOSE","command_id":"execution-command-0053","broker_order_id":53}
            orders={"recent_orders":[matched_fill(53,"execution-command-0053","SELL",4000,1.102)]}
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                {"position":6000.0,"active":[],"orders":orders})
            self.assertIsNone(state["pending"]); self.assertEqual(state["position"],6000.0)
            self.assertIn("remaining exposure",state["last_error"])
        finally:
            d.paper_cmd,d.atomic_json=old_cmd,old_atomic

    def test_two_orders_with_same_broker_number_keep_separate_economics(self):
        from unittest.mock import patch
        state=self.state();state["gross_pnl"]=61.5
        current="current-sell-request";old="historical-buy-request"
        state["pending"]={"kind":"OPEN","command_id":current,"broker_order_id":101,"side":"SELL"}
        old_fill=matched_fill(101,old,"BUY",25000,1.1331)
        new_fill=matched_fill(101,current,"SELL",25000,1.13489)
        response={"ok":True,"result":{"payload":{"authoritative":True,"command_id":current,
            "command_status":"accepted","order_id":101}}}
        snap={"position":-25000.0,"active":[],"orders":{"recent_orders":[old_fill,new_fill]}}
        with patch.object(d,"paper_cmd",return_value=response) as cmd,patch.object(d,"atomic_json"):
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",snap)
            self.assertEqual(cmd.call_count,1)
            self.assertEqual(cmd.call_args.args[4],"inspect")
        self.assertEqual(state["entry_price"],1.13489)
        self.assertEqual(state["entries"],1)
        self.assertEqual(state["gross_pnl"],61.5)
        self.assertIsNone(state["pending"])

    def test_pending_original_cannot_be_replaced_by_prepare(self):
        from unittest.mock import patch
        state=self.state();state["pending"]={"command_id":"original-146","broker_order_id":146}
        original=copy.deepcopy(state)
        with patch.object(d,"paper_cmd") as cmd,patch.object(d,"atomic_json") as write:
            for kind in ("OPEN","CLOSE"):
                with self.assertRaisesRegex(d.DriverError,"original command pending"):
                    d.prepare_send(state,Path("/unused"),"b","s","t","o",kind,{"bid":1.1,"ask":1.2},"BUY")
            cmd.assert_not_called();write.assert_not_called()
        self.assertEqual(state,original)

    def test_status_only_rejection_after_possible_send_cannot_clear_pending(self):
        from unittest.mock import patch
        for send_state in ("POSSIBLY_SENT","ACKNOWLEDGED"):
            state=self.state();command="original-146"
            state["pending"]={"kind":"OPEN","command_id":command,"broker_order_id":146,"side":"BUY","send_state":send_state}
            result={"ok":True,"result":{"payload":{"authoritative":True,"command_id":command,
                "command_status":"rejected","order_id":146}}}
            with patch.object(d,"paper_cmd",return_value=result),patch.object(d,"atomic_json"):
                d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                    {"position":0.0,"active":[],"orders":{"recent_orders":[]}})
            self.assertEqual(state["pending"]["command_id"],command)
            self.assertEqual(state["gross_pnl"],0.0)
            self.assertEqual(state["entries"],0)

    def test_unrelated_terminal_callback_cannot_clear_pending(self):
        from unittest.mock import patch
        state=self.state();original={"kind":"OPEN","command_id":"pending-sell","broker_order_id":101,"side":"SELL"}
        state["pending"]=copy.deepcopy(original)
        other=matched_fill(101,"old-buy","BUY",25000,1.1331)
        other.update(economic_fill=False,status="Cancelled")
        response={"ok":True,"result":{"payload":{"authoritative":True,"command_id":"pending-sell",
            "command_status":"uncertain","order_id":101}}}
        with patch.object(d,"paper_cmd",return_value=response) as cmd,patch.object(d,"atomic_json"):
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                {"position":0.0,"active":[],"orders":{"recent_orders":[other]}})
            self.assertEqual(cmd.call_args.args[4],"inspect")
        self.assertEqual(state["pending"]["command_id"],original["command_id"])
        self.assertEqual(state["entries"],0)
        self.assertEqual(state["gross_pnl"],0)

    def test_exact_zero_fill_cancellation_only_clears_original_pending(self):
        from unittest.mock import patch
        state=self.state();state.update(entries=8,exits=8,closed_trades=8,gross_pnl=103.75)
        command="cancelled-original-command"
        state["pending"]={"kind":"OPEN","command_id":command,"broker_order_id":103,"side":"SELL"}
        row=matched_fill(103,command,"SELL",0,0)
        row.update(status="ApiCancelled",economic_fill=False,broker_execution_id="",broker_execution_ambiguous=False)
        result={"ok":True,"result":{"payload":{"authoritative":True,"command_id":command,
            "command_status":"accepted","order_id":103}}}
        snap={"position":0.0,"active":[],"orders":{"recent_orders":[row]}}
        with patch.object(d,"paper_cmd",return_value=result) as cmd,patch.object(d,"atomic_json"):
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",snap)
            self.assertEqual(cmd.call_count,1)
            self.assertEqual(cmd.call_args.args[4],"inspect")
        self.assertIsNone(state["pending"])
        self.assertEqual((state["entries"],state["exits"],state["closed_trades"]),(8,8,8))
        self.assertEqual(state["gross_pnl"],103.75)
        self.assertEqual(state["position"],0.0)

    def test_zero_fill_cancellation_cannot_hide_an_unaccounted_position(self):
        from unittest.mock import patch
        state=self.state();command="cancelled-original-command"
        state["pending"]={"kind":"OPEN","command_id":command,"broker_order_id":103,"side":"SELL"}
        row=matched_fill(103,command,"SELL",0,0)
        row.update(status="ApiCancelled",economic_fill=False,broker_execution_id="",broker_execution_ambiguous=False)
        result={"ok":True,"result":{"payload":{"authoritative":True,"command_id":command,
            "command_status":"accepted","order_id":103}}}
        with patch.object(d,"paper_cmd",return_value=result),patch.object(d,"atomic_json"):
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",
                {"position":-10000.0,"active":[],"orders":{"recent_orders":[row]}})
        self.assertEqual(state["pending"]["command_id"],command)
        self.assertEqual(state["entries"],0)
        self.assertEqual(state["gross_pnl"],0)

    def test_partial_or_ambiguous_terminal_row_is_not_zero_fill(self):
        pending={"command_id":"x","broker_order_id":103}
        row=matched_fill(103,"x","SELL",0,0)
        row.update(status="ApiCancelled",economic_fill=False,broker_execution_id="",broker_execution_ambiguous=False)
        self.assertTrue(d.terminal_nonfill({"recent_orders":[row]},pending,"SELL"))
        for key,value in (("filled_quantity",1),("average_fill_price",1.1),("broker_execution_id","some-fill"),
                          ("broker_execution_ambiguous",True),("status","PendingCancel")):
            modified=dict(row);modified[key]=value
            self.assertFalse(d.terminal_nonfill({"recent_orders":[modified]},pending,"SELL"))

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

class CryptoDriverPolicyTests(unittest.TestCase):
    def setUp(self):
        self.old=(d.INSTRUMENT,d.QTY,d.QUANTITY_TOLERANCE,d.NATIVE_ENDPOINT)
        d.INSTRUMENT="BTC.USD";d.QTY=1.0;d.QUANTITY_TOLERANCE=1e-10;d.NATIVE_ENDPOINT=None
    def tearDown(self):
        d.INSTRUMENT,d.QTY,d.QUANTITY_TOLERANCE,d.NATIVE_ENDPOINT=self.old
    def state(self):
        return {"instrument":"BTC.USD","pending":None,"position":0.0,"entry_price":None,
            "entries":0,"exits":0,"closed_trades":0,"gross_pnl":0.0,"last_error":"","halt_new_entries":False}
    def test_owner_recovery_precheck_uses_subject_position(self):
        self.assertTrue(d.recovery_position_flat({"position":0.0,"gross_position":25000.0}))
        for value in (0.00000001,-0.00000001,float("nan"),float("inf"),None,False,"0"):
            self.assertFalse(d.recovery_position_flat({"position":value,"gross_position":25000.0}))
        with __import__("unittest.mock",fromlist=["patch"]).patch.object(d,"INSTRUMENT","EUR.USD"):
            self.assertTrue(d.recovery_position_flat({"position":0.0,"gross_position":0.00099993}))
            self.assertFalse(d.recovery_position_flat({"position":-25000.0,"gross_position":25000.00099993}))

    def test_crypto_resume_keeps_fx_exposure_and_requires_native_ack_and_preview(self):
        from unittest.mock import patch
        import subprocess
        state=self.state();state.update(lease_generation=1)
        calls=[];snap_calls=[];saved=[]
        def snap(_cli,_state,_path,require_quote=True):
            snap_calls.append(require_quote)
            resumed=any("paper-resume" in c for c in calls)
            return {"position":0.0,"gross_position":25000.0,"active":[],"quote":{"ask":84569.5},
                "owner_health":{"owner_fenced":False,"owner_recovery_only":not resumed,"mutation_ready":resumed},
                "service_epoch":"native-epoch","service_generation":3}
        def run(argv,*_):
            calls.append(argv)
            if "paper-resume" in argv:
                self.assertEqual(argv[argv.index("--token-file")+1],"original-token-path")
                self.assertEqual(argv[argv.index("--generation")+1],"1")
                return subprocess.CompletedProcess(argv,0,'{"accepted":true,"reason_code":"SESSION_OWNER_RESUMED","lease_generation":2}',"")
            self.assertEqual(argv,["cli","paper-on"])
            return subprocess.CompletedProcess(argv,0,"","")
        def preview(_cli,tool,*fields):
            self.assertEqual(tool,"risk.preview_order")
            self.assertIn("order_type=MKT",fields);self.assertIn("tif=IOC",fields)
            self.assertTrue(any(f.startswith("cash_quantity=") for f in fields))
            self.assertFalse(any(f.startswith("quantity=") or f.startswith("limit_price=") for f in fields))
            return {"approved":True,"preview_permit":"test-only-permit","service_epoch":"native-epoch","service_fencing_generation":3}
        with patch.object(d,"snapshot",snap),patch.object(d,"run",run),patch.object(d,"native",preview),\
             patch.object(d,"atomic_json",lambda _,v:saved.append(copy.deepcopy(v))):
            d.latch_owner_fence(state,Path("/unused"),"OPERATOR_AUDITED_RECOVERY")
            self.assertTrue(d.recover_owner(state,Path("/unused"),"cli","sessionctl","socket","original-token-path",1000))
        self.assertEqual(snap_calls,[False,True]);self.assertEqual(len(calls),2)
        self.assertEqual(state["lease_generation"],2);self.assertEqual(state["owner_recovery_phase"],"VERIFIED")
        self.assertFalse(state["halt_new_entries"]);self.assertIsNone(state["pending"])
        self.assertEqual(state["entries"],0);self.assertEqual(state["exits"],0);self.assertEqual(state["gross_pnl"],0)
        self.assertNotIn("test-only-permit",str(saved))

    def test_fx_resume_keeps_btc_exposure_and_requires_native_ack_and_preview(self):
        from unittest.mock import patch
        import subprocess
        state=self.state();state.update(instrument="EUR.USD",lease_generation=1)
        calls=[];saved=[]
        def snap(_cli,_state,_path,require_quote=True):
            resumed=any("paper-resume" in c for c in calls)
            return {"position":0.0,"gross_position":0.00099993,"active":[],"quote":{"ask":1.1234},
                "owner_health":{"owner_fenced":False,"owner_recovery_only":not resumed,"mutation_ready":resumed},
                "service_epoch":"native-epoch","service_generation":3}
        def run(argv,*_):
            calls.append(argv)
            if "paper-resume" in argv:
                return subprocess.CompletedProcess(argv,0,'{"accepted":true,"reason_code":"SESSION_OWNER_RESUMED","lease_generation":2}',"")
            self.assertEqual(argv,["cli","paper-on"])
            return subprocess.CompletedProcess(argv,0,"","")
        def preview(_cli,tool,*fields):
            self.assertEqual(tool,"risk.preview_order")
            self.assertIn("instrument=EUR.USD",fields)
            self.assertIn("order_type=MKT",fields)
            self.assertIn("tif=DAY",fields)
            self.assertIn("quantity=25000",fields)
            self.assertFalse(any(f.startswith("cash_quantity=") for f in fields))
            return {"approved":True,"preview_permit":"test-only-permit","service_epoch":"native-epoch","service_fencing_generation":3}
        with patch.object(d,"INSTRUMENT","EUR.USD"),patch.object(d,"QTY",25000.0),             patch.object(d,"QUANTITY_TOLERANCE",1e-9),patch.object(d,"snapshot",snap),             patch.object(d,"run",run),patch.object(d,"native",preview),             patch.object(d,"atomic_json",lambda _,v:saved.append(copy.deepcopy(v))):
            d.latch_owner_fence(state,Path("/unused"),"SESSION_RECOVERY_ONLY")
            self.assertTrue(d.recover_owner(state,Path("/unused"),"cli","sessionctl","socket","token",1000))
        self.assertEqual(state["lease_generation"],2)
        self.assertEqual(state["owner_recovery_phase"],"VERIFIED")
        self.assertFalse(state["halt_new_entries"])
        self.assertFalse(state["owner_fence_recovery_required"])
        self.assertIsNone(state["pending"])
        self.assertNotIn("test-only-permit",str(saved))

    def test_crypto_recovery_never_ignores_nonzero_btc_or_pending(self):
        from unittest.mock import patch
        state=self.state();state["owner_fence_recovery_required"]=True
        with patch.object(d,"snapshot",return_value={"position":0.0001,"gross_position":25000.0001,"active":[]}),\
             patch.object(d,"run") as run:
            with self.assertRaises(d.DriverError):
                d.recover_owner(state,Path("/unused"),"cli","s","s","t",1000)
            run.assert_not_called()
        state["pending"]={"command_id":"original-uncertain"}
        with patch.object(d,"snapshot") as snap,patch.object(d,"run") as run:
            with self.assertRaisesRegex(d.DriverError,"original pending"):
                d.recover_owner(state,Path("/unused"),"cli","s","s","t",1000)
            snap.assert_not_called();run.assert_not_called()

    def test_crypto_native_resume_refusal_cannot_clear_recovery_latch(self):
        from unittest.mock import patch
        import subprocess
        state=self.state();state.update(lease_generation=1,owner_fence_recovery_required=True,halt_new_entries=True)
        snap={"position":0.0,"gross_position":25000.0,"active":[]}
        refusal=subprocess.CompletedProcess([],1,'{"accepted":false,"reason_code":"CRYPTO_OWNER_POSITION_SCOPE_UNPROVEN","lease_generation":1}',"")
        with patch.object(d,"snapshot",return_value=snap),patch.object(d,"run",return_value=refusal) as run,\
             patch.object(d,"atomic_json"),patch.object(d,"native") as preview:
            with self.assertRaisesRegex(d.DriverError,"CRYPTO_OWNER_POSITION_SCOPE_UNPROVEN"):
                d.recover_owner(state,Path("/unused"),"cli","s","s","t",1000)
            self.assertEqual(run.call_count,1);preview.assert_not_called()
        self.assertTrue(state["owner_fence_recovery_required"]);self.assertTrue(state["halt_new_entries"])
        self.assertEqual(state["lease_generation"],1);self.assertIsNone(state["pending"])

    def test_fx_state_cannot_be_reinterpreted_as_crypto(self):
        import json
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"state.json";path.write_text(json.dumps({"schema":"heptatrader.paper-driver.v1","position":-25000}));path.chmod(0o600)
            before=path.read_bytes()
            with self.assertRaisesRegex(d.DriverError,"cross-instrument"):d.load_state(path)
            self.assertEqual(path.read_bytes(),before)
    def test_crypto_buy_uses_cash_mkt_without_a_limit_or_base_size(self):
        from unittest.mock import patch
        state=self.state();calls=[]
        def cmd(_b,_s,_t,_o,op,extra):
            calls.append((op,list(extra)))
            if op=="prepare-crypto-open":
                return {"ok":True,"command_id":"protected-buy"}
            return {"ok":False,"transport_ok":False,"reason":"SOCKET_TIMEOUT","result":None}
        with patch.object(d,"paper_cmd",cmd),patch.object(d,"atomic_json"):
            d.prepare_send(state,Path("/unused"),"b","s","t","o","OPEN",
                {"bid":84569.0,"ask":84569.5},"BUY")
        args=calls[0][1]
        price=float(args[args.index("--price")+1])
        self.assertEqual(price,84569.5)
        cash=float(args[args.index("--cash-quantity")+1])
        self.assertEqual(cash,25000.0)
        self.assertNotIn("--quantity",args)
        self.assertNotIn("--limit-price",args)
        self.assertEqual(state["pending"]["quantity_mode"],"CASH")
        self.assertEqual(state["pending"]["cash_quantity"],cash)
        self.assertEqual(state["pending"]["order_type"],"MKT")
        self.assertEqual(state["pending"]["tif"],"IOC")
        self.assertEqual(state["pending"]["reference_price"],price)

    def test_crypto_short_rejected_before_preview(self):
        from unittest.mock import patch
        with patch.object(d,"paper_cmd") as cmd:
            with self.assertRaisesRegex(d.DriverError,"short entries"):
                d.prepare_send(self.state(),Path("/unused"),"b","s","t","o","OPEN",{"bid":85000,"ask":85000.25},"SELL")
            cmd.assert_not_called()
    def test_invalid_crypto_market_budget_rejects_before_preview(self):
        from unittest.mock import patch
        for ask in (float("inf"),float("nan"),0,-1,0.001):
            with patch.object(d,"paper_cmd") as cmd:
                with self.assertRaises(d.DriverError):
                    d.prepare_send(self.state(),Path("/unused"),"b","s","t","o","OPEN",{"bid":ask,"ask":ask},"BUY")
                cmd.assert_not_called()
    def inspect(self,state,qty,price,side,new_position,command):
        from unittest.mock import patch
        row=matched_fill(155,command,side,qty,price);row["instrument"]="BTC.USD"
        response={"ok":True,"result":{"payload":{"authoritative":True,"command_id":command,"command_status":"accepted","order_id":155}}}
        with patch.object(d,"paper_cmd",return_value=response) as native,patch.object(d,"atomic_json"):
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",{"position":new_position,"active":[],"orders":{"recent_orders":[row]}})
            self.assertEqual(native.call_args.args[4],"inspect")
    def test_partial_ioc_entry_and_two_exits_settle_once_in_usd(self):
        state=self.state()
        state["pending"]={"kind":"OPEN","command_id":"crypto-open","broker_order_id":155,"side":"BUY","position_before_send":0.0}
        self.inspect(state,0.6,85000,"BUY",0.6,"crypto-open")
        self.assertEqual(state["entries"],1);self.assertTrue(state["halt_new_entries"])
        state["pending"]={"kind":"CLOSE","command_id":"crypto-partial-close","broker_order_id":155,"position_before_send":0.6,
            "parent_entry_command_id":"crypto-open"}
        self.inspect(state,0.2,85100,"SELL",0.4,"crypto-partial-close")
        self.assertAlmostEqual(state["gross_pnl"],20.0,places=10)
        self.assertEqual(state["closed_trades"],0);self.assertIsNone(state["pending"])
        self.assertFalse(d.crypto_roundtrip_qualified(state))
        state["pending"]={"kind":"CLOSE","command_id":"crypto-final-close","broker_order_id":155,"position_before_send":0.4,
            "parent_entry_command_id":"crypto-open"}
        self.inspect(state,0.4,85050,"SELL",0.0,"crypto-final-close")
        self.assertAlmostEqual(state["gross_pnl"],40.0,places=10)
        self.assertEqual((state["entries"],state["exits"],state["closed_trades"]),(1,1,1))
        self.assertFalse(state["halt_new_entries"]);self.assertIsNone(state["pending"])
        self.assertTrue(d.crypto_roundtrip_qualified(state))
        self.assertEqual(state["crypto_roundtrip_proof"]["entry_command_id"],"crypto-open")
        self.assertEqual(len(state["crypto_roundtrip_proof"]["exits"]),2)
    def test_mkt_actual_base_size_is_not_assumed_equal_to_nominal(self):
        for qty in (0.29,1.01):
            state=self.state();command="cash-variable-fill"
            state["pending"]={"kind":"OPEN","command_id":command,"broker_order_id":155,"side":"BUY",
                "position_before_send":0.0,"quantity_mode":"CASH","cash_quantity":25000.0}
            self.inspect(state,qty,84000,"BUY",qty,command)
            self.assertIsNone(state["pending"])
            self.assertEqual(state["position"],qty)
            self.assertEqual(state["entry_quantity"],qty)
            self.assertEqual(state["entries"],1)
            self.assertEqual(state["halt_new_entries"],qty>1.0)
            self.assertFalse(d.crypto_roundtrip_qualified(state))
            state["pending"]={"kind":"CLOSE","command_id":"cash-exit","broker_order_id":155,
                "position_before_send":qty,"parent_entry_command_id":command}
            self.inspect(state,qty,84100,"SELL",0.0,"cash-exit")
            self.assertTrue(d.crypto_roundtrip_qualified(state))
            self.assertAlmostEqual(state["gross_pnl"],qty*100,places=10)
    def test_invalid_or_duplicate_roundtrip_proof_never_qualifies_driver(self):
        self.assertFalse(d.crypto_roundtrip_qualified(self.state()))
        row={"command_id":"exit","execution_id":"e2","quantity":0.001}
        proof={"entry_command_id":"entry","entry_execution_id":"e1","entry_quantity":0.001,"exits":[row]}
        self.assertTrue(d.crypto_roundtrip_qualified({"crypto_roundtrip_proof":proof}))
        for changed in (dict(proof,exits=[row,row]),dict(proof,entry_quantity=0.0009),
                        dict(proof,entry_execution_id=""),dict(proof,exits=[])):
            self.assertFalse(d.crypto_roundtrip_qualified({"crypto_roundtrip_proof":changed}))
    def test_market_reference_is_not_a_limit_price_tick(self):
        self.assertEqual(d.crypto_market_cash_budget(85000.1),25000.0)
        self.assertEqual(d.crypto_market_cash_budget(150000.25),25000.0)
        with self.assertRaises(d.DriverError):
            d.crypto_market_cash_budget(24999.0)
    def test_position_without_exact_fill_quantity_does_not_settle(self):
        state=self.state();state["pending"]={"kind":"OPEN","command_id":"mismatch","broker_order_id":155,"side":"BUY","position_before_send":0}
        self.inspect(state,0.0002,85000,"BUY",0.001,"mismatch")
        self.assertIsNotNone(state["pending"]);self.assertEqual(state["entries"],0)
        self.assertEqual(state["position"],0);self.assertEqual(state["gross_pnl"],0)
    def test_crypto_cannot_use_an_fx_fill_with_same_number_and_command(self):
        row=matched_fill(155,"same-command","BUY",0.001,1.1);row["instrument"]="EUR.USD"
        self.assertIsNone(d.recent_fill({"recent_orders":[row]},{"command_id":"same-command","broker_order_id":155},"BUY"))
    def test_active_lease_continues_without_flatten_or_quote(self):
        from unittest.mock import patch
        import subprocess
        state=self.state();state.update(active_lease_continuation_supported=True,lease_generation=17,lease_last_renew_ms=0)
        def run(argv,timeout):
            self.assertIn("renew-active",argv);self.assertIn("17",argv)
            return subprocess.CompletedProcess(argv,0,'{"accepted":true,"reason_code":"SESSION_LEASE_CONTINUED","lease_generation":18}',"")
        with patch.object(d,"run",run),patch.object(d,"atomic_json"),patch.object(d,"paper_cmd") as order:
            d.maybe_renew(state,Path("/unused"),"ctl","sock","token",1000,-25000,0,None)
            order.assert_not_called()
        self.assertEqual(state["lease_generation"],18);self.assertFalse(state["halt_new_entries"])
        self.assertFalse(state["lease_uncertain"])
    def test_active_lease_unknown_reply_keeps_original_generation_and_halt(self):
        from unittest.mock import patch
        state=self.state();state.update(lease_generation=3,lease_last_renew_ms=0)
        with patch.object(d,"run",side_effect=TimeoutError("timeout")),patch.object(d,"atomic_json"):
            with self.assertRaises(TimeoutError):d.continue_active_lease(state,Path("/unused"),"ctl","sock","token",1000,0,None)
        self.assertEqual(state["lease_generation"],3);self.assertTrue(state["halt_new_entries"])
        self.assertTrue(state["lease_uncertain"]);self.assertEqual(state["lease_renewal_state"],"POSSIBLY_SENT")

class CumulativeSettlementTests(unittest.TestCase):
    def setUp(self):
        self.globals=(d.INSTRUMENT,d.QTY,d.QUANTITY_TOLERANCE)
        d.INSTRUMENT="BTC.USD";d.QTY=1.0;d.QUANTITY_TOLERANCE=1e-10
    def tearDown(self):
        d.INSTRUMENT,d.QTY,d.QUANTITY_TOLERANCE=self.globals
    def fixture(self):
        health={"execution_mode":"PAPER","gateway_ready":True,"remote_execution_ready":True,
            "execution_service_epoch":"service-current","execution_service_fencing_generation":1,
            "execution_authoritative_health":{"source":"IB","authoritative":True,"owner_fenced":False,
                "owner_recovery_only":False,"startup_recovery_read_only":True,"mutation_blocked":True,
                "mutation_ready":False,"mutation_reason":"IB_FX_CASH_ATTESTED_BALANCE_MISMATCH"}}
        orders={"source":"IB","authoritative":True,"global_active_orders_complete":True,
            "owner_projection_complete":True,"active_order_ids":[],"active_orders_connection_epoch":9,
            "active_orders_generation":1,"owner_scope":{"account":"DU_TEST"},"recent_orders":[]}
        physical={"source":"IB_REQ_POSITIONS","authoritative":True,"scope":"PHYSICAL_CRYPTO_ONLY",
            "mutation_authorized":False,"account":"DU_TEST","execution_service_epoch":"service-current",
            "connection_epoch":9,"position_generation":2,"active_orders_generation":1,"terminal_generation":1,
            "sampled_at_ms":d.now_ms(),"positions":[{"instrument":"BTC.USD","quantity":.03313821}]}
        portfolio={"source":"IB","authoritative":False,"positions":[],"settlement_observation":physical}
        return health,orders,portfolio
    def test_physical_snapshot_does_not_promote_cash_or_mutation_authority(self):
        h,o,p=self.fixture();before=copy.deepcopy((h,o,p))
        snap=d.physical_crypto_settlement_snapshot(h,o,p)
        self.assertEqual(snap["position"],.03313821);self.assertTrue(snap["settlement_only"])
        self.assertEqual((h,o,p),before);self.assertFalse(p["authoritative"])
        self.assertFalse(h["execution_authoritative_health"]["mutation_ready"])
    def test_physical_snapshot_rejects_false_empty_cash_account_epoch_or_authority(self):
        from unittest.mock import patch
        for kind in range(19):
            h,o,p=self.fixture();x=p["settlement_observation"]
            if kind==0:x["authoritative"]=False
            elif kind==1:x["mutation_authorized"]=True
            elif kind==2:x["account"]="OTHER"
            elif kind==3:x["connection_epoch"]=8
            elif kind==4:x["execution_service_epoch"]="old-service"
            elif kind==5:x["position_generation"]=0
            elif kind==6:x["active_orders_generation"]=2
            elif kind==7:x["terminal_generation"]=0
            elif kind==8:x["sampled_at_ms"]-=11000
            elif kind==9:x["sampled_at_ms"]+=11000
            elif kind==10:x["positions"]=[]
            elif kind==11:x["positions"][0]["quantity"]=float("nan")
            elif kind==12:x["positions"][0]["instrument"]="EUR.USD"
            elif kind==13:o["active_order_ids"]=[159]
            elif kind==14:o["owner_projection_complete"]=False
            elif kind==15:h["execution_authoritative_health"]["mutation_blocked"]=False
            elif kind==16:h["execution_authoritative_health"]["mutation_reason"]="OTHER_ERROR"
            elif kind==17:p["authoritative"]=True
            elif kind==18:x["scope"]="PAPER_BASELINE_DELTA"
            with self.subTest(kind=kind),self.assertRaises(d.DriverError):
                d.physical_crypto_settlement_snapshot(h,o,p)
        h,o,p=self.fixture()
        with patch.object(d,"INSTRUMENT","EUR.USD"),self.assertRaises(d.DriverError):
            d.physical_crypto_settlement_snapshot(h,o,p)
    def test_default_resident_snapshot_cannot_use_settlement_observation(self):
        from unittest.mock import patch
        h,o,p=self.fixture()
        def native(_cli,tool,*fields):
            return {"system.get_health":h,"orders.list":o,"portfolio.list_positions":p}[tool]
        with patch.object(d,"native",native):
            with self.assertRaises(d.DriverError):d.snapshot("cli",require_quote=False)
            snap=d.snapshot("cli",require_quote=False,allow_settlement=True)
            self.assertTrue(snap["settlement_only"])
    def test_multiple_execution_receipts_adopt_once_without_realizing_pnl_or_resending(self):
        from unittest.mock import patch
        h,o,p=self.fixture();cmd="original-cumulative-command"
        ids=["fixture.0001.01.01","fixture.0002.01.01"]
        row=matched_fill(159,cmd,"BUY",.03313821,86437.099117)
        row.update(status="Cancelled",economic_evidence_version=2,economic_evidence_complete=True,
            broker_execution_ids=ids,broker_execution_id=ids[-1],broker_execution_observed_at_ms=123456)
        o["recent_orders"]=[row]
        snap=d.physical_crypto_settlement_snapshot(h,o,p)
        state={"pending":{"kind":"OPEN","command_id":cmd,"broker_order_id":159,"side":"BUY",
            "position_before_send":0.0,"quantity_mode":"CASH","cash_quantity":25000},"position":0.0,
            "entries":5,"exits":5,"closed_trades":5,"gross_pnl":.93769834,"halt_new_entries":True}
        operations=[];writes=[]
        def inspect(_b,_s,_t,_o,op,extra):
            operations.append(op);self.assertEqual(op,"inspect");self.assertIn(cmd,extra)
            return {"ok":True,"result":{"payload":{"authoritative":True,"command_id":cmd,
                "command_status":"accepted","order_id":159,"reason_code":""}}}
        with patch.object(d,"paper_cmd",inspect),patch.object(d,"atomic_json",lambda p,v:writes.append(copy.deepcopy(v))):
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",snap)
        self.assertEqual(operations,["inspect"]);self.assertEqual(len(writes),1)
        self.assertIsNone(state["pending"]);self.assertEqual(state["entries"],6)
        self.assertEqual(state["exits"],5);self.assertEqual(state["gross_pnl"],.93769834)
        self.assertEqual(state["entry_at_ms"],123456);self.assertEqual(state["entry_broker_execution_ids"],ids)
        self.assertTrue(state["halt_new_entries"])
        self.assertEqual(state["last_reconciliation"]["broker_status"],"Cancelled")
        self.assertFalse(state["last_reconciliation"]["risk_authoritative"])
        self.assertEqual(state["last_reconciliation"]["execution_ids"],ids)
    def test_incomplete_or_conflicting_v2_cumulative_evidence_cannot_settle(self):
        cmd="same-original";pending={"command_id":cmd,"broker_order_id":159}
        base=matched_fill(159,cmd,"BUY",.03313821,86437.099117)
        base.update(economic_evidence_version=2,economic_evidence_complete=True,
            broker_execution_ids=[base["broker_execution_id"]])
        for kind in range(6):
            row=copy.deepcopy(base)
            if kind==0:row["economic_evidence_complete"]=False
            elif kind==1:row["broker_execution_ids"]=["other"]
            elif kind==2:row["broker_execution_ids"]*=2
            elif kind==3:row["broker_execution_ids"]=[]
            elif kind==4:row["broker_execution_ambiguous"]=True
            elif kind==5:row["broker_execution_ids"]=[False]
            with self.subTest(kind=kind):self.assertIsNone(d.recent_fill({"recent_orders":[row]},pending,"BUY"))

if __name__=="__main__": unittest.main()
