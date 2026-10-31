"""PREPARED reconnect: real private-file fixtures, no Broker control permission."""
import copy,json,os,unittest
from unittest import mock
import test_activate_prepared_gateway as fixtures
gateway=fixtures.gateway

def fixture(test):
    f=fixtures.GatewayUpgradeTests();f.setUp()
    test.addCleanup(f.doCleanups);test.addCleanup(f.tearDown)
    broker=gateway.driver.BROKER_UNIT
    f.units[broker].update(ActiveState="active",SubState="running",MainPID="0",InvocationID="managed-original")
    proof={'execution_process': {'pid_key': ['368455', '100'], 'exe_sha256': 'e' * 64, 'cgroup': '/execution', 'netns': [1, 2]}, 'broker_process': {'pid_key': ['10617', '200'], 'exe_sha256': 'b' * 64, 'cgroup': '/gnome', 'netns': [1, 2]}, 'scope': {'InvocationID': 'gnome-original'}, 'profile': {'HEPTA_IB_PAPER_HOST': '127.0.0.1', 'HEPTA_IB_PAPER_PORT': '4002'}, 'selected_edges': {'PropagatesStopTo': ''}, 'sockets': {'execution': {'inode': 2918032, 'fds': [44], 'local': ['127.0.0.1', 44164], 'remote': ['127.0.0.1', 4002], 'state': '01', 'uid': os.geteuid()}, 'accepted': {'inode': 2869080, 'fds': [91], 'local': ['127.0.0.1', 4002], 'remote': ['127.0.0.1', 44164], 'state': '01', 'uid': os.geteuid()}, 'listener': {'inode': 50, 'fds': [90], 'local': ['127.0.0.1', 4002], 'remote': ['0.0.0.0', 0], 'state': '0A', 'uid': os.geteuid()}}}
    proof.pop("scope")
    proof["broker_process"]["cgroup"]="/ibgateway"
    proof["managed_unit"]=dict(f.units[broker],Id=broker,Type="simple",ExitType="cgroup",ControlGroup="/ibgateway",
        ControlPID="0",RemainAfterExit="no",StopWhenUnneeded="no")
    proof["broker_manual_stop_shield_claimed"]=True
    f.snapshot["interactive"]=copy.deepcopy(proof);f.interactive_current=copy.deepcopy(proof)
    f.patches.enter_context(mock.patch.object(gateway.driver,"interactive_broker_snapshot",
        side_effect=lambda *args:copy.deepcopy(f.interactive_current)))
    return f

def state_for(f):
    return json.loads(json.dumps({"schema":"heptatrader.gateway-upgrade.v1","plan_sha256":"a"*64,
        "status":"RUNNING","phase":"PREPARED","snapshot":f.snapshot,"markers":f.markers,
        "stopped_controllers":[],"rollback":"NO_PROCESS_FORCED_OR_REPLACED"}))

def reconnect(f):
    f.interactive_current["sockets"]["execution"].update(inode=200,fds=[45],local=["127.0.0.1",33336])
    f.interactive_current["sockets"]["accepted"].update(inode=201,fds=[101],remote=["127.0.0.1",33336])

EFFECTS={"owners_fenced":False,"stopping":None,"owned_policies":[],"owner_progress":{},
    "release_bytes":None,"selected_release":None,"gateway_stopped":False,"gateway_started":{},
    "controllers_started":{},"starting_controller":None,"native_execution":None,"unknown_effect":False}

class PreparedReconnectComponents(unittest.TestCase):
    def setUp(self):self.f=fixture(self)
    def test_no_control_intent_even_false_or_empty(self):
        state=state_for(self.f);gateway.prepared_reconnect_effects(state,True)
        for key,value in EFFECTS.items():
            with self.subTest(field=key),self.assertRaisesRegex(RuntimeError,"CONTROL_EFFECTS_NOT_EMPTY"):
                gateway.prepared_reconnect_effects(dict(state,**{key:value}),True)
        for resume,phase in ((False,"PREPARED"),(True,"STOP_POLICY_PREPARING")):
            with self.assertRaises(RuntimeError):gateway.prepared_reconnect_effects(dict(state,phase=phase),resume)
    def test_json_pid_keys_and_normal_diagnostic_refresh(self):
        state=state_for(self.f);value=json.loads(self.f.state_path.read_text())
        value["entry_inference_pause"]={"reason":"MAINTENANCE_HOLD","checked_at_ms":99}
        self.f.state_path.write_text(json.dumps(value))
        gateway.prepared_processes_unchanged(self.f.home,state["snapshot"])

    def test_unrecorded_policy_file_or_dangling_symlink_rejected(self):
        for unit in (gateway.GATEWAY,*gateway.driver.DRIVER_UNITS):
            path=self.f.home/".config/systemd/user"/(unit+".d")/gateway.POLICY
            path.parent.mkdir(parents=True,exist_ok=True)
            for dangling in (False,True):
                with self.subTest(unit=unit,dangling=dangling):
                    if dangling:path.symlink_to(path.with_name("missing"))
                    else:path.write_bytes(gateway.HELD_STOP)
                    try:
                        with self.assertRaisesRegex(RuntimeError,"EXISTING_STOP_POLICY"):
                            gateway.prepared_processes_unchanged(self.f.home,state_for(self.f)["snapshot"])
                    finally:path.unlink()

    def test_business_or_manifest_change_rejected(self):
        original=self.f.state_path.read_bytes()
        changes=({"position":1},{"pending":{"command_id":"new"}},{"lease_generation":113},
            {"last_reconciliation":{"command_id":"different"}},{"entries":3})
        for change in changes:
            with self.subTest(change=change):
                value=json.loads(original);value.update(change)
                self.f.state_path.write_text(json.dumps(value))
                try:
                    with self.assertRaises(RuntimeError):
                        gateway.prepared_processes_unchanged(self.f.home,state_for(self.f)["snapshot"])
                finally:self.f.state_path.write_bytes(original)
        running=self.f.running_driver(self.f.driver_unit,"EUR.USD")
        running["manifest_sha256"]="f"*64
        with mock.patch.object(gateway.driver,"running_driver",return_value=running):
            with self.assertRaises(RuntimeError):
                gateway.prepared_processes_unchanged(self.f.home,state_for(self.f)["snapshot"])

    def test_witness_chain_preserves_original_and_rejects_static_identity_changes(self):
        state=state_for(self.f);snapshot=copy.deepcopy(state["snapshot"])
        reconnect(self.f)
        record={"plan_sha256":state["plan_sha256"],"markers":state["markers"],
            "previous_witness":copy.deepcopy(snapshot["interactive"]),
            "witness":copy.deepcopy(self.f.interactive_current)}
        state["prepared_reconnects"]=[record]
        self.assertEqual(gateway.effective_interactive(state["snapshot"],state),record["witness"])
        self.assertEqual(state["snapshot"],snapshot)
        invalid=[]
        for key,value in (("plan_sha256","b"*64),("markers",[]),("previous_witness",{})):
            changed=copy.deepcopy(record);changed[key]=value;invalid.append(changed)
        for path,value in ((("broker_process","pid_key"),["99","200"]),
                (("managed_unit","InvocationID"),"replacement"),
                (("sockets","listener","inode"),999)):
            changed=copy.deepcopy(record);node=changed["witness"]
            for key in path[:-1]:node=node[key]
            node[path[-1]]=value;invalid.append(changed)
        for changed in invalid:
            with self.subTest(record=changed),self.assertRaisesRegex(RuntimeError,"RECEIPT_INVALID"):
                gateway.effective_interactive(snapshot,dict(state,prepared_reconnects=[changed]))

    def test_native_authority_requires_original_hold_and_owner_scope(self):
        snapshot=state_for(self.f)["snapshot"];owner=snapshot["owners"][0]
        self.assertEqual(gateway.held_native_authority(self.f.plan,snapshot,owner),("fixture-service-epoch",1))
        self.f.guard_blocked=False
        with self.assertRaisesRegex(RuntimeError,"NATIVE_AUTHORITY_INVALID"):
            gateway.held_native_authority(self.f.plan,snapshot,owner)


class PreparedReconnectTransactions(unittest.TestCase):
    """Run unchanged under a non-root user; the real operator lock is required."""
    def setUp(self):
        self.f=fixture(self)
        with mock.patch.object(gateway,"rpc",side_effect=RuntimeError("NATIVE_CONTROL_NOT_CONFIRMED:owner-audit")):
            failed=self.f.run_upgrade()
        self.assertEqual((failed["status"],failed["phase"]),("HELD","PREPARED"),failed)
        self.assert_no_controls()
        self.checkpoint=self.f.root/"operator-maintenance"/gateway.CHECKPOINT
        self.original=json.loads(self.checkpoint.read_text())
        reconnect(self.f)

    def assert_no_controls(self):
        self.assertFalse(any(e and e[0] in ("stop","start","reset-failed","daemon-reload") for e in self.f.events),self.f.events)

    def assert_rejected(self,reason=None):
        result=self.f.run_upgrade(resume=True)
        self.assertEqual(result["status"],"HELD",result)
        if reason:self.assertIn(reason,result["error"])
        state=json.loads(self.checkpoint.read_text())
        self.assertNotIn("prepared_reconnects",state)
        self.assertEqual(state["snapshot"],self.original["snapshot"])
        self.assert_no_controls()
        return result

    def reset_checkpoint(self):
        gateway.atomic_json(self.checkpoint,self.original)
        self.f.events.clear()

    def test_same_plan_reconnect_preserves_original_evidence_then_consumes_new_stop_audits(self):
        result=self.f.run_upgrade(resume=True)
        self.assertEqual(result["status"],"COMPLETE",result)
        state=json.loads(self.checkpoint.read_text())
        self.assertEqual(len(state["prepared_reconnects"]),1)
        receipt=state["prepared_reconnects"][0]
        self.assertEqual(receipt["original_snapshot"],self.original["snapshot"])
        self.assertEqual(receipt["previous_witness"],self.original["snapshot"]["interactive"])
        self.assertEqual(receipt["witness"],self.f.interactive_current)
        self.assertEqual(state["snapshot"]["interactive"],self.original["snapshot"]["interactive"])
        self.assertEqual(receipt["markers"],self.original["markers"])
        self.assertEqual(receipt["deadline_ms"],self.original["snapshot"]["deadline"])
        self.assertEqual(receipt["plan_sha256"],self.original["plan_sha256"])
        self.assertEqual(receipt["native_authority"],["fixture-service-epoch",1])
        used={gateway.digest(command) for command in self.f.audit_used}
        self.assertTrue(used)
        self.assertTrue(all(audit["audit_command_sha256"] not in used for audit in receipt["audits"]))
        for unit in (gateway.EXECUTION,gateway.driver.BROKER_UNIT):
            self.assertFalse(any(e and e[0] in ("start","stop","reset-failed") and e[-1]==unit for e in self.f.events))
            self.assertFalse(self.f.policy_path(unit).exists())

    def test_any_persisted_control_intent_blocks_prepared_refresh(self):
        for key,value in EFFECTS.items():
            with self.subTest(field=key):
                self.reset_checkpoint()
                gateway.atomic_json(self.checkpoint,dict(self.original,**{key:value}))
                self.assert_rejected("CONTROL_EFFECTS_NOT_EMPTY")

    def test_owner_generation_expiry_or_scope_drift_blocks_refresh(self):
        original=copy.deepcopy(self.f.rows)
        for key,value in (("generation",113),("expires_at_ms",original[0]["expires_at_ms"]+1),
                ("recovery_only",True),("account_sha256","f"*64)):
            with self.subTest(field=key):
                self.reset_checkpoint();self.f.rows=copy.deepcopy(original);self.f.rows[0][key]=value
                try:self.assert_rejected("NATIVE_AUTHORITY_INVALID" if key=="recovery_only" else "CATALOG_CHANGED")
                finally:self.f.rows=copy.deepcopy(original)

    def test_business_state_or_original_process_drift_blocks_refresh(self):
        original=self.f.state_path.read_bytes()
        value=json.loads(original);value["entries"]+=1
        self.f.state_path.write_text(json.dumps(value))
        try:self.assert_rejected("CONTROLLER_STATE_CHANGED")
        finally:self.f.state_path.write_bytes(original)
        self.reset_checkpoint()
        self.f.interactive_current["managed_unit"]["InvocationID"]="different-original-service"
        self.assert_rejected("BROKER_IDENTITY_CHANGED")

    def test_native_guard_or_fresh_audit_uncertainty_blocks_refresh(self):
        self.f.guard_blocked=False
        self.assert_rejected("NATIVE_AUTHORITY_INVALID")
        self.f.guard_blocked=True;self.reset_checkpoint();self.f.unsafe_audit=True
        self.assert_rejected()

    def test_native_authority_change_between_observations_blocks_receipt(self):
        original=gateway.held_native_authority;calls=0
        def observe(*args):
            nonlocal calls
            calls+=1;value=original(*args)
            return value if calls==1 else (value[0],value[1]+1)
        with mock.patch.object(gateway,"held_native_authority",side_effect=observe):
            self.assert_rejected("NATIVE_AUTHORITY_CHANGED")

    def test_hold_change_during_final_catalog_read_blocks_receipt(self):
        raw=self.f.hold.read_bytes();calls=0
        def rows(*args):
            nonlocal calls
            calls+=1
            if calls==3:self.f.hold.write_bytes(b"different-hold")
            return copy.deepcopy(self.f.rows)
        try:
            with mock.patch.object(gateway,"catalog",side_effect=rows):
                self.assert_rejected("MAINTENANCE_MARKERS_CHANGED")
            self.assertEqual(calls,3)
        finally:self.f.hold.write_bytes(raw)

    def test_unrecorded_actual_policy_blocks_refresh(self):
        path=self.f.policy_path(gateway.GATEWAY);path.write_bytes(gateway.HELD_STOP)
        try:self.assert_rejected("EXISTING_STOP_POLICY")
        finally:path.unlink()
