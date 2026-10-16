"""Fail-closed unit controls for a single IBKR PAPER WhatIf-only preview."""
import unittest
import tempfile
import contextlib
import io
from pathlib import Path
import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
import ib_paper_whatif_preview as p
from ibapi.client import EClient

class PreviewBoundaries(unittest.TestCase):
    def test_real_sdk_preview_object_is_not_working_stop(self):
        c,o=p.exact_whatif_order("DU-ISOLATED-SYNTHETIC",1.12362,9001)
        self.assertEqual(c.conId,12087792)
        self.assertEqual(c.secType,"CASH")
        self.assertEqual(c.exchange,"IDEALPRO")
        self.assertEqual(o.account,"DU-ISOLATED-SYNTHETIC")
        self.assertEqual(o.orderType,"STP")
        self.assertEqual(o.action,"SELL")
        self.assertEqual(str(o.totalQuantity),"1000")
        self.assertTrue(o.whatIf)
        self.assertTrue(o.transmit)
        self.assertFalse(o.parentId)
        self.assertEqual(o.tif,"DAY")
        self.assertLess(o.auxPrice,1.12362)
        self.assertEqual(round(o.auxPrice/0.00005)%1,0)

    def test_installed_tws_python_sdk_serializes_whatif(self):
        # Exact SDK source: WhatIf must be on the actual wire, not just
        # a Python-only preview flag that a future dependency silently drops.
        serializer=inspect.getsource(EClient.placeOrder)
        self.assertIn("make_field(order.whatIf)",serializer)

    def test_nonpaper_and_broker_id_refused(self):
        for account in ("U123","",None,"DA123"):
            with self.assertRaises(ValueError):
                p.exact_whatif_order(account,1.12362,9001)
        for order_id in (0,-1,2147483647):
            with self.assertRaises(ValueError):
                p.exact_whatif_order("DU-TEST",1.12362,order_id)

    def test_stale_invalid_or_triggered_mark_refused(self):
        for quote in (0,0.4,1.9,float("nan"),float("inf")):
            with self.assertRaises(ValueError):
                p.synthetic_stop_price(quote)

    def test_repeated_place_message_cannot_leave_client(self):
        client=p.PreviewClient()
        with patch.object(EClient,"sendMsg",return_value=None):
            with self.assertRaises(RuntimeError):
                client.sendMsg("3\0")
            client.allow_preview=True
            client.sendMsg("3\0")
            self.assertEqual(client.sent_preview,1)
            with self.assertRaises(RuntimeError):
                client.sendMsg("3\0")
            with self.assertRaises(RuntimeError):
                client.sendMsg("99\0")
            self.assertEqual(client.message_codes,[3])

    def test_managed_account_requires_single_paper_account(self):
        for text in ("U123","DU123,U123","","DU123,DU456"):
            client=p.PreviewClient()
            client.managedAccounts(text)
            self.assertFalse(client.paper_account)
        client=p.PreviewClient()
        client.managedAccounts("DU123")
        self.assertEqual(client.paper_account,"DU123")

    def test_callback_rejects_actual_working_status(self):
        client=p.PreviewClient()
        client.expected_order_id=123
        client.orderStatus(123,"Filled",1,0)
        self.assertEqual(client.executions,1)
        self.assertTrue(client.preview_ready.is_set())
        self.assertEqual(client.order_status_callbacks,["Filled"])

    def make_exact_echo(self):
        c,o=p.exact_whatif_order("DU-PAPER-SYNTHETIC",1.12362,9001)
        client=p.PreviewClient()
        client.allow_preview=True
        client.expected_order_id=9001
        client.expected_account="DU-PAPER-SYNTHETIC"
        client.expected_stop_price=o.auxPrice
        state=SimpleNamespace(status="PreSubmitted",initMarginChange="250")
        return client,c,o,state

    def test_only_complete_original_whatif_echo_qualifies(self):
        client,c,o,state=self.make_exact_echo()
        client.openOrder(9001,c,o,state)
        self.assertEqual(client.exact_preview_echoes,1)
        self.assertEqual(client.invalid_preview_echoes,0)
        self.assertTrue(client.order_states[0]["exact_nonworking_whatif_echo"])
        self.assertTrue(client.order_states[0]["echoed_exact_account"])
        self.assertTrue(client.order_states[0]["quantity_exact"])
        self.assertTrue(client.preview_ready.is_set())

    def test_foreign_account_and_drifted_native_echo_fail_closed(self):
        mutators=(
            ("account", "DU-FOREIGN"),
            ("whatIf", False),
            ("clientId", 8995),
            ("action", "BUY"),
            ("orderRef", "H1-FOREIGN"),
            ("tif", "GTC"),
            ("orderType", "MKT"),
            ("transmit", False),
            ("parentId", 12),
            ("totalQuantity", Decimal("999")),
            ("auxPrice", 1.05),
        )
        for attr,value in mutators:
            with self.subTest(attribute=attr):
                client,c,o,state=self.make_exact_echo()
                setattr(o,attr,value)
                client.openOrder(9001,c,o,state)
                self.assertEqual(client.exact_preview_echoes,0)
                self.assertEqual(client.invalid_preview_echoes,1)
                self.assertFalse(client.order_states[0]["exact_nonworking_whatif_echo"])
        client,c,o,state=self.make_exact_echo()
        c.conId=99999
        client.openOrder(9001,c,o,state)
        self.assertEqual(client.exact_preview_echoes,0)
        client,c,o,state=self.make_exact_echo()
        c.exchange="FOREIGN"
        client.openOrder(9001,c,o,state)
        self.assertEqual(client.exact_preview_echoes,0)
        client,c,o,state=self.make_exact_echo()
        state.status="Submitted"
        client.openOrder(9001,c,o,state)
        self.assertEqual(client.exact_preview_echoes,0)

    def test_terminal_and_error_after_good_echo_revoke_preview(self):
        client,c,o,state=self.make_exact_echo()
        client.openOrder(9001,c,o,state)
        client.sent_preview=1
        client.message_codes=[71,3]
        self.assertTrue(p.exact_preview_callback_attested(client))
        client.orderStatus(9001,"Filled",0,1000)
        self.assertFalse(p.exact_preview_callback_attested(client))
        client,c,o,state=self.make_exact_echo()
        client.openOrder(9001,c,o,state)
        client.sent_preview=1
        client.message_codes=[71,3]
        client.error(9001,201,"order rejected")
        self.assertFalse(p.exact_preview_callback_attested(client))
        client,c,o,state=self.make_exact_echo()
        client.openOrder(9001,c,o,state)
        client.sent_preview=1
        client.message_codes=[71,3]
        client.executions=1
        self.assertFalse(p.exact_preview_callback_attested(client))

    def test_status_alone_not_exact_open_order_evidence(self):
        client,c,o,state=self.make_exact_echo()
        client.orderStatus(9001,"PreSubmitted",0,Decimal("1000"))
        self.assertEqual(client.exact_preview_echoes,0)
        self.assertEqual(client.invalid_preview_echoes,0)
        self.assertTrue(client.preview_ready.is_set())
        client.error(9001,201,"Rejected")
        self.assertTrue(any(e["request_id_match"] and e["code"]==201
                            for e in client.broker_error_codes))

    def test_quote_time_must_be_bounded_on_both_sides(self):
        now=1792119000000
        q={"instrument":"EUR.USD","stale":False,"subscription_state":"active",
           "observed_at_ms":now-100,"bid":1.12362}
        self.assertEqual(p.require_fresh_quote_observation(q,now),1.12362)
        for t in (now+1,now-5001,None,True):
            with self.subTest(ts=t):
                q["observed_at_ms"]=t
                with self.assertRaises(RuntimeError):
                    p.require_fresh_quote_observation(q,now)

    def test_gtc_oca_whatif_sdk_native_fields_are_nonexecuting(self):
        scope=p.preview_profile("gtc_oca")
        self.assertEqual(scope["client_id"],8995)
        self.assertEqual(scope["tif"],"GTC")
        self.assertEqual(scope["oca_type"],1)
        self.assertEqual(len(scope["oca_group"]),45)
        self.assertTrue(scope["oca_group"].startswith("H1"))
        contract,order=p.exact_whatif_order("DU-SYNTHETIC",1.12362,9001,scope)
        self.assertEqual(contract.conId,12087792)
        self.assertEqual(contract.secType,"CASH")
        self.assertEqual(contract.exchange,"IDEALPRO")
        self.assertTrue(order.whatIf)
        self.assertTrue(order.transmit)
        self.assertFalse(order.parentId)
        self.assertEqual(order.tif,"GTC")
        self.assertEqual(order.ocaGroup,scope["oca_group"])
        self.assertEqual(order.ocaType,1)
        self.assertEqual(order.action,"SELL")
        self.assertEqual(order.orderType,"STP")
        self.assertEqual(order.totalQuantity,Decimal("1000"))
        self.assertEqual(order.clientId,8995)
        self.assertEqual(order.orderRef,scope["order_ref"])
        self.assertLess(order.auxPrice,1.12362)
        self.assertNotEqual(order.ocaGroup,p.preview_profile()["oca_group"])

    def test_gtc_oca_exact_echo_and_hostile_mutations(self):
        scope=p.preview_profile("gtc_oca")
        def make():
            c,o=p.exact_whatif_order("DU-SYNTHETIC",1.12362,9001,scope)
            app=p.PreviewClient(scope)
            app.expected_order_id=9001
            app.expected_account="DU-SYNTHETIC"
            app.expected_stop_price=o.auxPrice
            app.allow_preview=True
            app.sent_preview=1
            app.message_codes=[71,3]
            state=SimpleNamespace(status="PreSubmitted",initMarginChange="250")
            return app,c,o,state
        app,c,o,state=make()
        app.openOrder(9001,c,o,state)
        self.assertTrue(p.exact_preview_callback_attested(app))
        self.assertEqual(app.order_states[0]["oca_scope_exact"],True)
        self.assertEqual(app.order_states[0]["tif_scope_exact"],True)
        for attr,value in (("ocaGroup","H1OTHER"),
                           ("ocaType",2),("tif","DAY"),
                           ("clientId",8994),("orderRef","H1FOREIGN"),
                           ("whatIf",False),("parentId",23),
                           ("transmit",False),("orderType","MKT")):
            with self.subTest(attr=attr):
                app,c,o,state=make()
                setattr(o,attr,value)
                app.openOrder(9001,c,o,state)
                self.assertFalse(p.exact_preview_callback_attested(app))
                self.assertEqual(app.invalid_preview_echoes,1)
        app,c,o,state=make()
        c.conId=13579
        app.openOrder(9001,c,o,state)
        self.assertFalse(p.exact_preview_callback_attested(app))
        app,c,o,state=make()
        app.openOrder(9001,c,o,state)
        app.orderStatus(9001,"Filled",0,1000)
        self.assertFalse(p.exact_preview_callback_attested(app))

    def test_gtc_oca_one_shot_and_unknown_scope_refuse(self):
        with self.assertRaises(ValueError):
            p.preview_profile("live")
        app=p.PreviewClient(p.preview_profile("gtc_oca"))
        with patch.object(EClient,"sendMsg",return_value=None):
            with self.assertRaises(RuntimeError):
                app.sendMsg("3\0")
            app.allow_preview=True
            app.sendMsg("3\0")
            with self.assertRaises(RuntimeError):
                app.sendMsg("3\0")
            self.assertEqual(app.sent_preview,1)
            self.assertEqual(app.message_codes,[3])

    def test_gtc_oca_cli_one_shot_lock_does_not_resend(self):
        quote={"instrument":"EUR.USD","stale":False,
               "subscription_state":"active","observed_at_ms":0,"bid":1.12362}
        runs=[]
        def fake_read(tool):
            import time
            if tool!="market.get_quote":
                raise AssertionError("unapproved read")
            q=dict(quote)
            q["observed_at_ms"]=int(time.time()*1000)
            return q
        def fake_preview(bid,profile):
            runs.append(profile["scope"])
            return {"status":"PREVIEW_ACCOUNT_ONLY_FAKE",
                    "exact_echo_verified":False,
                    "preview_variant":profile["scope"],
                    "broker_order_preview_only":True,
                    "execution_callbacks":0}
        with tempfile.TemporaryDirectory() as folder:
            argv=["ib_paper_whatif_preview.py","--paper-whatif-gtc-oca",
                  "--evidence-dir",folder]
            with patch.object(p.sys,"argv",argv), \
                 patch.object(p,"native_read",side_effect=fake_read), \
                 patch.object(p,"observe_orders",return_value={"count":0,"fingerprint":"x"}), \
                 patch.object(p,"one_preview",side_effect=fake_preview), \
                 contextlib.redirect_stdout(io.StringIO()):
                p.main()
                with self.assertRaises(FileExistsError):
                    p.main()
            root=Path(folder)
            self.assertEqual(runs,["gtc_oca"])
            self.assertTrue((root/"whatif-preview-gtc-oca-executed-once.lock").exists())
            self.assertTrue((root/"whatif-gtc-oca-result.json").exists())
            self.assertFalse((root/"whatif-preview-result.json").exists())
            self.assertFalse((root/"whatif-preview-executed-once.lock").exists())

    def test_durable_directory_marker_precedes_any_broker_preview(self):
        import time
        attempts=[]
        fsynced=[]
        def read(tool):
            if tool!="market.get_quote":raise AssertionError("unexpected tool")
            return {"instrument":"EUR.USD","stale":False,
                    "subscription_state":"active",
                    "observed_at_ms":int(time.time()*1000),"bid":1.12362}
        original=p.fsync_directory
        def seal(path):
            fsynced.append(str(path))
            return original(path)
        with tempfile.TemporaryDirectory() as home:
            root=Path(home)/"evidence"
            argv=["ib_paper_whatif_preview.py","--paper-whatif-gtc-oca",
                  "--evidence-dir",str(root)]
            with patch.object(p.sys,"argv",argv), \
                 patch.object(p,"native_read",side_effect=read), \
                 patch.object(p,"observe_orders",return_value={"count":0,"fingerprint":"ok"}), \
                 patch.object(p,"one_preview",side_effect=lambda *args: (
                     attempts.append(args) or {"status":"SYNTHETIC_PREVIEW",
                     "exact_echo_verified":False,"execution_callbacks":0})), \
                 patch.object(p,"fsync_directory",side_effect=seal), \
                 contextlib.redirect_stdout(io.StringIO()):
                p.main()
            self.assertEqual(attempts[0][1]["scope"],"gtc_oca")
            self.assertEqual(fsynced,[str(root.parent),str(root),str(root),str(root)])
            self.assertTrue((root/"whatif-gtc-oca-result.json").exists())

    def test_marker_dir_fsync_failure_refuses_preview_and_reissue(self):
        import time
        invoked=[]
        def fresh(tool):
            if tool!="market.get_quote":raise AssertionError("unexpected tool")
            return {"instrument":"EUR.USD","stale":False,
                    "subscription_state":"active",
                    "observed_at_ms":int(time.time()*1000),"bid":1.12362}
        with tempfile.TemporaryDirectory() as home:
            root=Path(home)/"evidence"
            argv=["ib_paper_whatif_preview.py","--paper-whatif-gtc-oca",
                  "--evidence-dir",str(root)]
            # Two directory fsyncs seal the evidence directory itself; the
            # third secures the O_EXCL one-shot marker BEFORE any API request.
            with patch.object(p.sys,"argv",argv), \
                 patch.object(p,"native_read",side_effect=fresh), \
                 patch.object(p,"fsync_directory",
                              side_effect=[None,None,OSError("fsync directory unavailable")]), \
                 patch.object(p,"observe_orders",side_effect=AssertionError("read after fsync failure")), \
                 patch.object(p,"one_preview",side_effect=lambda *a: invoked.append(a)):
                with self.assertRaisesRegex(OSError,"fsync directory unavailable"):
                    p.main()
            self.assertEqual(invoked,[])
            self.assertTrue((root/"whatif-preview-gtc-oca-executed-once.lock").exists())
            self.assertFalse((root/"whatif-gtc-oca-result.json").exists())
            with patch.object(p.sys,"argv",argv), \
                 patch.object(p,"native_read",side_effect=fresh), \
                 patch.object(p,"one_preview",side_effect=lambda *a: invoked.append(a)):
                with self.assertRaises(FileExistsError):
                    p.main()
            self.assertEqual(invoked,[])

    def test_evidence_parent_required_without_creating_ancestor_chain(self):
        import time
        with tempfile.TemporaryDirectory() as home:
            root=Path(home)/"missing-parent"/"evidence"
            argv=["ib_paper_whatif_preview.py","--paper-whatif-gtc-oca",
                  "--evidence-dir",str(root)]
            fake_quote={"instrument":"EUR.USD","stale":False,
                         "subscription_state":"active",
                         "observed_at_ms":int(time.time()*1000),"bid":1.12362}
            with patch.object(p.sys,"argv",argv), \
                 patch.object(p,"native_read",return_value=fake_quote), \
                 patch.object(p,"one_preview",side_effect=AssertionError("preview sent")):
                with self.assertRaisesRegex(RuntimeError,"EVIDENCE_PARENT_MUST_PREEXIST"):
                    p.main()
            self.assertFalse(root.parent.exists())

class CanaryAccountIsolationBoundaries(unittest.TestCase):
    def test_single_original_paper_account_never_admits_executable_canary(self):
        state=p.classify_paper_isolation(["DU100"],"DU100")
        self.assertEqual(state["managed_paper_account_count"],1)
        self.assertEqual(state["candidate_other_paper_account_count"],0)
        self.assertEqual(state["reason"],"NO_DISTINCT_BROKER_ACCOUNT")
        self.assertEqual(state["state"],"ONE_SHARED_PAPER_ACCOUNT_NO_EXECUTABLE_CANARY")
        self.assertIs(state["can_submit_executable_canary"],False)
        self.assertNotIn("DU100",str(state))

    def test_other_paper_account_is_only_a_candidate_not_authority(self):
        state=p.classify_paper_isolation(["DU100","DU200"],"DU100")
        self.assertEqual(state["candidate_other_paper_account_count"],1)
        self.assertEqual(
            state["reason"],"BROKER_ACCOUNT_ISOLATION_AND_OWNERSHIP_UNPROVEN")
        self.assertFalse(state["can_submit_executable_canary"])
        self.assertNotIn("DU200",str(state))

    def test_wrong_original_duplicate_live_or_malformed_accounts_fail(self):
        bad_cases=[
            (["DU100"],"DU200"),(["DU100","DU100"],"DU100"),
            (["U100"],"DU100"),([], "DU100"),
            (["DU100","DUX-123"],"DU100"),
            (["DU100",None],"DU100"),
        ]
        for managed,original in bad_cases:
            with self.subTest(managed=managed,original=original):
                with self.assertRaisesRegex(
                    RuntimeError,"PAPER_MANAGED_ACCOUNT_OR_OWNER_NOT_ATTESTED"):
                    p.classify_paper_isolation(managed,original)

    def test_original_broker_profile_must_be_private_single_owner(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"execution.env"
            path.write_text("HEPTA_IB_EXECUTION_MODE=PAPER\n"
                            "HEPTA_IB_PAPER_ACCOUNT=DU100\n")
            path.chmod(0o600)
            self.assertEqual(p.exact_original_paper_account(path),"DU100")
            path.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError,"NOT_PRIVATE"):
                p.exact_original_paper_account(path)
            path.chmod(0o600)
            alias=Path(td)/"alias.env"
            alias.symlink_to(path)
            with self.assertRaises(OSError):
                p.exact_original_paper_account(alias)
            path.write_text("HEPTA_IB_PAPER_ACCOUNT=DU100\n"
                            "HEPTA_IB_PAPER_ACCOUNT=DU200\n")
            with self.assertRaisesRegex(RuntimeError,"IDENTITY_UNAVAILABLE"):
                p.exact_original_paper_account(path)

    def test_readonly_probe_permanently_blocks_placeorder_wire(self):
        c=p.PaperIsolationReadOnlyClient()
        with patch.object(EClient,"sendMsg",return_value=None) as native:
            with self.assertRaisesRegex(RuntimeError,"ORDER_FORBIDDEN"):
                c.sendMsg("3\0")
            with self.assertRaisesRegex(RuntimeError,"ORDER_FORBIDDEN"):
                c.sendMsg("91\0")
            self.assertEqual(c.outbound_opcodes,[])
            native.assert_not_called()

    def test_cli_assessment_never_loads_quotes_or_sends_whatif(self):
        with tempfile.TemporaryDirectory() as td:
            argv=["ib_paper_whatif_preview.py","--assess-paper-canary-isolation",
                  "--evidence-dir",td]
            synthetic={"state":"ONE_SHARED_PAPER_ACCOUNT_NO_EXECUTABLE_CANARY",
                       "can_submit_executable_canary":False}
            with patch.object(p.sys,"argv",argv), \
                 patch.object(p,"assess_isolated_paper_canary",
                              return_value=synthetic) as assess, \
                 patch.object(p,"native_read",
                              side_effect=AssertionError("quote requested")), \
                 patch.object(p,"one_preview",
                              side_effect=AssertionError("preview requested")), \
                 contextlib.redirect_stdout(io.StringIO()) as stdout:
                p.main()
            assess.assert_called_once_with(Path(td))
            self.assertIn("NO_EXECUTABLE_CANARY",stdout.getvalue())

    def test_private_receipt_after_mock_broker_handshake(self):
        class FakeReadOnly:
            def __init__(self):
                from threading import Event
                self.accounts_ready=Event()
                self.id_ready=Event()
                self.accounts_ready.set();self.id_ready.set()
                self.accounts=["DU100"]
                self.outbound_opcodes=[71]
                self.broker_notice_codes=[]
            def connect(self,*a):pass
            def run(self):pass
            def disconnect(self):pass
        with tempfile.TemporaryDirectory() as td:
            parent=Path(td)
            account=parent/"execution.env"
            account.write_text("HEPTA_IB_PAPER_ACCOUNT=DU100\n")
            account.chmod(0o600)
            target=parent/"evidence"
            with patch.object(p,"PaperIsolationReadOnlyClient",FakeReadOnly):
                result=p.assess_isolated_paper_canary(target,account)
            self.assertEqual(result["outbound_opcodes"],[71])
            self.assertEqual(result["managed_paper_account_count"],1)
            self.assertFalse(result["can_submit_executable_canary"])
            self.assertEqual(result["broker_orders_sent"],0)
            receipt=Path(result["evidence_path"]).read_text()
            self.assertNotIn("DU100",receipt)
            self.assertIn("NO_DISTINCT_BROKER_ACCOUNT",receipt)


if __name__=="__main__":unittest.main()
