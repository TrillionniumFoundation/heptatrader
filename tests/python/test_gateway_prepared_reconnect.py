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


class LeaseSuccessorComponents(unittest.TestCase):
    def setUp(self):self.f=fixture(self)
    def advanced(self,snapshot):
        rows=copy.deepcopy(snapshot['records'])
        rows[0]['generation']+=1;rows[0]['expires_at_ms']+=14400000
        return rows
    def receipt_state(self):
        state=state_for(self.f);before=copy.deepcopy(state['snapshot'])
        rows=self.advanced(before);after,changes=gateway.lease_successor_snapshot(before,rows)
        witness={'plan_sha256':state['plan_sha256'],'markers':state['markers'],'deadline_ms':before['deadline'],
            'original_snapshot':copy.deepcopy(before),'native_authority':['fixture-service-epoch',1],
            'previous_witness':before['interactive'],'witness':before['interactive'],
            'audits':[{'token_sha256':owner['token_sha256'],'generation':owner['state_generation'],
                'original_command_sha256':gateway.digest(owner['command'])} for owner in after['owners']]}
        state['prepared_reconnects']=[witness]
        state['prepared_lease_successors']=[{'schema':'heptatrader.gateway-lease-successor.v1',
            'plan_sha256':state['plan_sha256'],'markers':state['markers'],'deadline_ms':before['deadline'],
            'before_catalog':before['records'],'after_catalog':rows,'successors':changes,
            'reconnect_receipt_sha256':gateway.digest(json.dumps(witness,sort_keys=True))}]
        state['effective_snapshot']=after
        return state
    def test_exact_successor_preserves_original_snapshot_and_deadline(self):
        state=self.receipt_state();original=copy.deepcopy(state['snapshot'])
        actual=gateway.effective_snapshot(state)
        self.assertEqual(actual['owners'][0]['state_generation'],113)
        self.assertEqual(actual['records'][0]['generation'],113)
        self.assertEqual(actual['deadline'],original['deadline'])
        self.assertEqual(state['snapshot'],original)
        self.assertGreater(actual['records'][0]['expires_at_ms'],actual['deadline'])
    def test_only_exact_plus_one_and_strictly_increasing_integer_expiry(self):
        snapshot=state_for(self.f)['snapshot']
        for generation,expiry in ((112,1),(114,1),(111,1),(True,1),(113,0),(113,-1),(113,True)):
            rows=self.advanced(snapshot);rows[0]['generation']=generation
            rows[0]['expires_at_ms']=expiry if type(expiry)is bool else snapshot['records'][0]['expires_at_ms']+expiry
            with self.subTest(generation=generation,expiry=expiry),self.assertRaises(RuntimeError):
                gateway.lease_successor_snapshot(snapshot,rows)
    def test_every_other_catalog_field_and_frozen_owner_remains_exact(self):
        snapshot=state_for(self.f)['snapshot']
        for key in snapshot['records'][0]:
            if key in ('generation','expires_at_ms','token_sha256'):continue
            rows=self.advanced(snapshot);old=rows[0][key]
            rows[0][key]=not old if type(old)is bool else old+1 if type(old)is int else str(old)+'-changed'
            with self.subTest(field=key),self.assertRaises(RuntimeError):gateway.lease_successor_snapshot(snapshot,rows)
        for mutation in ('extra','removed','token'):
            rows=self.advanced(snapshot)
            if mutation=='extra':rows[0]['unreviewed_field']=True
            elif mutation=='removed':rows[0].pop('agent_sha256')
            else:rows[0]['token_sha256']='f'*64
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):gateway.lease_successor_snapshot(snapshot,rows)
        for key,value in (('finalization_state',False),('finalization_state',0.0),('bootstrap_issuer',1),('watch',0)):
            rows=self.advanced(snapshot);rows[0][key]=value
            with self.subTest(field=key,value=value),self.assertRaises(RuntimeError):gateway.lease_successor_snapshot(snapshot,rows)
        snapshot['owners'][0]['resident']=None
        with self.assertRaises(RuntimeError):gateway.lease_successor_snapshot(snapshot,self.advanced(snapshot))
    def test_receipt_replay_binds_plan_markers_deadline_catalog_and_current_audit(self):
        original=self.receipt_state();gateway.effective_snapshot(original)
        for field,value in (('plan_sha256','b'*64),('markers',[]),('deadline_ms',1),
                ('before_catalog',[]),('reconnect_receipt_sha256','f'*64)):
            state=copy.deepcopy(original);state['prepared_lease_successors'][0][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):gateway.effective_snapshot(state)
        for field,value in (('deadline',original['snapshot']['deadline']+1),('environment',{})):
            state=copy.deepcopy(original);state['effective_snapshot'][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):gateway.effective_snapshot(state)
        state=copy.deepcopy(original);state['prepared_reconnects'][0]['audits'][0]['generation']=112
        state['prepared_lease_successors'][0]['reconnect_receipt_sha256']=gateway.digest(json.dumps(state['prepared_reconnects'][0],sort_keys=True))
        with self.assertRaises(RuntimeError):gateway.effective_snapshot(state)
    def test_later_control_records_are_separate_from_original_and_bound_effective_identity(self):
        state=self.receipt_state();original=copy.deepcopy(state['snapshot'])
        state.update(status='HELD',phase='PREPARED',owned_policies=[str(self.f.policy_path(gateway.GATEWAY))],
            error='RuntimeError:FIXTURE_AFTER_PARTIAL_FENCE',rollback='NO_PROCESS_FORCED_OR_REPLACED')
        state['effective_snapshot']['records'][0]['recovery_only']=True
        state['effective_snapshot']['owners'][0]['record']['recovery_only']=True
        path=self.f.root/'partial-effect-checkpoint.json';gateway.atomic_json(path,state)
        state=json.loads(path.read_text())
        self.assertIs(gateway.effective_snapshot(state),state['effective_snapshot'])
        self.assertEqual(state['snapshot'],original)
        with self.assertRaisesRegex(RuntimeError,'CONTROL_EFFECTS_NOT_EMPTY'):gateway.prepared_reconnect_effects(state,True)
        state['phase']='OWNERS_FENCED'
        self.assertIs(gateway.effective_snapshot(state),state['effective_snapshot'])
        state['effective_snapshot']['owners'][0]['command']='different'
        with self.assertRaises(RuntimeError):gateway.effective_snapshot(state)


class LeaseSuccessorTransactions(unittest.TestCase):
    setUp=PreparedReconnectTransactions.setUp
    assert_no_controls=PreparedReconnectTransactions.assert_no_controls
    assert_rejected=PreparedReconnectTransactions.assert_rejected
    reset_checkpoint=PreparedReconnectTransactions.reset_checkpoint
    def advance(self,*,same_tcp=True):
        self.f.rows[0]['generation']+=1;self.f.rows[0]['expires_at_ms']+=14400000
        value=json.loads(self.f.state_path.read_text());value['lease_generation']=self.f.rows[0]['generation']
        value['lease_last_renew_ms']=int(self.f.clock*1000)
        self.f.state_path.write_text(json.dumps(value))
        if same_tcp:self.f.interactive_current=copy.deepcopy(self.original['snapshot']['interactive'])
    def complete(self,expected_generation=114):
        result=self.f.run_upgrade(resume=True)
        self.assertEqual(result['status'],'COMPLETE',result)
        state=json.loads(self.checkpoint.read_text())
        self.assertEqual(state['snapshot'],self.original['snapshot'])
        self.assertEqual(state['effective_snapshot']['deadline'],self.original['snapshot']['deadline'])
        self.assertEqual(json.loads(self.f.state_path.read_text())['lease_generation'],expected_generation)
        for operation in ('recovery-query','paper-resume'):
            calls=[e for e in self.f.events if operation in e]
            self.assertEqual(len(calls),1,(operation,calls))
            self.assertEqual(int(calls[0][calls[0].index('--generation')+1]),expected_generation-1)
        for unit in (gateway.EXECUTION,gateway.driver.BROKER_UNIT):
            self.assertFalse(any(e and e[0] in ('stop','start','reset-failed') and e[-1]==unit for e in self.f.events))
            self.assertFalse(self.f.policy_path(unit).exists())
        gateway.effective_snapshot(state)
        return state
    def test_unchanged_tcp_successor_rebinds_all_controls_without_extending_deadline(self):
        self.advance();state=self.complete()
        self.assertEqual(len(state['prepared_lease_successors']),1)
        receipt=state['prepared_lease_successors'][0]
        self.assertEqual(receipt['before_catalog'],self.original['snapshot']['records'])
        self.assertEqual(receipt['after_catalog'][0]['generation'],113)
        self.assertEqual(receipt['historical_control_operation'],'NOT_INFERRED')
        proof=state['prepared_reconnects'][0]
        self.assertEqual(proof['previous_witness'],proof['witness'])
        self.assertEqual(proof['audits'][0]['generation'],113)
        self.assertNotIn(proof['audits'][0]['audit_command_sha256'],{gateway.digest(x) for x in self.f.audit_used})
    def test_combined_tcp_and_exact_successor_uses_one_bound_receipt(self):
        self.advance(same_tcp=False);state=self.complete()
        self.assertEqual(len(state['prepared_reconnects']),1)
        self.assertNotEqual(state['prepared_reconnects'][0]['previous_witness'],state['prepared_reconnects'][0]['witness'])
    def test_interrupted_before_first_control_reuses_receipt_without_second_successor(self):
        self.advance();original=gateway.fresh_audit;calls=0
        def observe(*args):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('FIXTURE_BEFORE_FIRST_CONTROL')
            return original(*args)
        with mock.patch.object(gateway,'fresh_audit',side_effect=observe):
            result=self.f.run_upgrade(resume=True)
        self.assertEqual((result['status'],result['phase']),('HELD','PREPARED'),result)
        self.assert_no_controls()
        first=json.loads(self.checkpoint.read_text())
        self.assertEqual(len(first['prepared_lease_successors']),1)
        state=self.complete()
        self.assertEqual(state['prepared_lease_successors'],first['prepared_lease_successors'])
    def test_successor_after_stop_audit_is_held_for_new_same_plan_boundary(self):
        self.advance();original=gateway.fresh_audit;calls=0
        def observe(*args):
            nonlocal calls
            calls+=1;result=original(*args)
            if calls==2:self.advance()
            return result
        with mock.patch.object(gateway,'fresh_audit',side_effect=observe):
            result=self.f.run_upgrade(resume=True)
        self.assertEqual((result['status'],result['phase']),('HELD','PREPARED'),result)
        self.assertIn('REQUIRES_RESUME_BOUNDARY',result['error']);self.assert_no_controls()
        state=self.complete(expected_generation=115)
        self.assertEqual(len(state['prepared_lease_successors']),2)
    def test_successor_cannot_cross_any_existing_control_intent(self):
        self.advance()
        for key,value in EFFECTS.items():
            with self.subTest(field=key):
                self.reset_checkpoint();gateway.atomic_json(self.checkpoint,dict(self.original,**{key:value}))
                self.assert_rejected('CONTROL_EFFECTS_NOT_EMPTY')
    def test_catalog_movement_during_successor_audit_cannot_create_effective_baseline(self):
        self.advance();original=gateway.fresh_audit
        def observe(*args):
            result=original(*args);self.f.rows[0]['expires_at_ms']+=1;return result
        with mock.patch.object(gateway,'fresh_audit',side_effect=observe):
            self.assert_rejected('CATALOG_CHANGED_DURING_AUDIT')
        self.assertNotIn('effective_snapshot',json.loads(self.checkpoint.read_text()))
    def test_catalog_successor_without_driver_confirmation_is_rejected(self):
        self.f.rows[0]['generation']+=1;self.f.rows[0]['expires_at_ms']+=14400000
        self.assert_rejected('CONTROLLER_STATE_CHANGED:state_generation')
    def test_unchanged_native_prepared_flow_does_not_enter_successor_gate(self):
        self.f.interactive_current=copy.deepcopy(self.original['snapshot']['interactive'])
        self.original['snapshot']['native_execution']={'fixture_original_native_plan':True}
        self.reset_checkpoint()
        with mock.patch.object(gateway.native_maintenance,'reconcile_release'), \
                mock.patch.object(gateway.native_maintenance,'verify',return_value=False), \
                mock.patch.object(gateway,'fresh_audit',side_effect=RuntimeError('FIXTURE_ORIGINAL_NATIVE_AUDIT')):
            self.assert_rejected('FIXTURE_ORIGINAL_NATIVE_AUDIT')
    def test_unchanged_all_frozen_owner_flow_keeps_existing_policy_transition(self):
        unit=self.f.driver_unit;pid=self.f.units[unit]['MainPID']
        self.f.release_driver();self.f.alive.pop(pid)
        self.f.units[unit].update(ActiveState='inactive',SubState='dead',MainPID='0',InvocationID='')
        self.original['snapshot']['statuses'][unit]=copy.deepcopy(self.f.units[unit])
        self.original['snapshot']['owners'][0]['resident']=None
        self.original['snapshot']['frozen'][unit]=gateway.driver.frozen_driver_snapshot(unit)
        self.original['snapshot']['controller_topology'].pop(unit,None)
        self.f.interactive_current=copy.deepcopy(self.original['snapshot']['interactive'])
        self.reset_checkpoint()
        result=self.f.run_upgrade(resume=True)
        self.assertEqual(result['status'],'COMPLETE',result)
        self.assertFalse(any(e and e[0] in ('start','stop') and e[-1]==unit for e in self.f.events))
        self.assertNotIn('prepared_lease_successors',json.loads(self.checkpoint.read_text()))

    def test_saved_successor_reentry_rechecks_current_driver_and_prior_native_authority(self):
        self.advance();original=gateway.fresh_audit;calls=0
        def observe(*args):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('FIXTURE_BEFORE_FIRST_CONTROL')
            return original(*args)
        with mock.patch.object(gateway,'fresh_audit',side_effect=observe):held=self.f.run_upgrade(resume=True)
        self.assertEqual((held['status'],held['phase']),('HELD','PREPARED'),held)
        saved=json.loads(self.checkpoint.read_text());self.assert_no_controls()
        for authority in (('different-epoch',1),('fixture-service-epoch',2)):
            gateway.atomic_json(self.checkpoint,saved)
            with mock.patch.object(gateway,'held_native_authority',return_value=authority):result=self.f.run_upgrade(resume=True)
            self.assertEqual(result['status'],'HELD',result)
            self.assertIn('NATIVE_AUTHORITY_CHANGED',result['error']);self.assert_no_controls()
            self.assertEqual(json.loads(self.checkpoint.read_text())['prepared_lease_successors'],saved['prepared_lease_successors'])
        gateway.atomic_json(self.checkpoint,saved)
        raw=self.f.state_path.read_bytes();value=json.loads(raw);value['lease_generation']+=1
        self.f.state_path.write_text(json.dumps(value))
        try:
            result=self.f.run_upgrade(resume=True)
            self.assertEqual(result['status'],'HELD',result)
            self.assertIn('CONTROLLER_STATE_CHANGED:state_generation',result['error']);self.assert_no_controls()
        finally:self.f.state_path.write_bytes(raw)

    def test_two_unchanged_frozen_owners_keep_original_sequential_fencing_flow(self):
        unit=self.f.driver_unit;pid=self.f.units[unit]['MainPID']
        self.f.release_driver();self.f.alive.pop(pid)
        self.f.units[unit].update(ActiveState='inactive',SubState='dead',MainPID='0',InvocationID='')
        snapshot=self.original['snapshot'];snapshot['statuses'][unit]=copy.deepcopy(self.f.units[unit])
        snapshot['owners'][0]['resident']=None
        snapshot['frozen'][unit]=gateway.driver.frozen_driver_snapshot(unit)
        snapshot['controller_topology'].pop(unit,None)
        path=self.f.root/'crypto-driver/state.json'
        fixtures.put(path,json.dumps(dict(self.f.initial_state,instrument='BTC.USD')).encode())
        fixtures.put(path.parent/'driver.lock',b'')
        fixtures.put(self.f.home/'.config/heptatrader/crypto.token',b'second-token\n')
        owner=gateway.controller('BTC.USD',self.f.home);owner['resident']=None
        row=dict(fixtures.record(),token_sha256=gateway.digest('second-token'),session_sha256=gateway.digest('second-session'))
        owner['record']=copy.deepcopy(row);self.f.rows.append(row)
        snapshot['owners'].append(owner);snapshot['records']=copy.deepcopy(self.f.rows)
        self.f.interactive_current=copy.deepcopy(snapshot['interactive']);self.reset_checkpoint()
        result=self.f.run_upgrade(resume=True)
        self.assertEqual(result['status'],'COMPLETE',result)
        self.assertEqual(self.f.resume_count,2)
        self.assertEqual(len([e for e in self.f.events if 'recovery-query' in e]),2)
        self.assertFalse(any(e and e[0] in ('start','stop') and e[-1] in gateway.driver.DRIVER_UNITS for e in self.f.events))
        self.assertNotIn('prepared_lease_successors',json.loads(self.checkpoint.read_text()))
