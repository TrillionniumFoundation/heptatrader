"""Exercise the real one-sample driver loop without native calls or orders."""
import copy
import importlib.util
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("driver_main_quote_test",ROOT/"scripts/heptatrader_paper_driver.py")
d=importlib.util.module_from_spec(spec);sys.modules[spec.name]=d;spec.loader.exec_module(d)


class MainQuoteOrderingTests(unittest.TestCase):
    instruments=("EUR.USD","BTC.USD")

    def once(self,instrument,*,position=1.0,halt=False,fenced=False,pending=None,
             active=False,quote_error=False,stale=False,snapshot_error=False):
        state={"history":[],"pending":copy.deepcopy(pending),"position":position,
               "entry_price":1.0,"halt_new_entries":halt,
               "owner_fence_recovery_required":fenced,
               "active_lease_continuation_supported":False,"last_error":""}
        snap={"position":position,"active":["existing-order"] if active else [],
              "owner_health":{"mutation_ready":True}}
        quote={"source":"IB","authoritative":True,"stale":stale,
               "bid":0.1,"ask":0.11,"observed_at_ms":d.now_ms()}
        def native(_cli,tool,*_args):
            self.assertEqual(tool,"market.get_quote")
            if quote_error:raise d.DriverError("quote unavailable in fixture")
            return quote
        globals_before={k:getattr(d,k) for k in
            ("INSTRUMENT","QTY","NATIVE_ENDPOINT","QUANTITY_TOLERANCE","CLOSE_RECEIPT_ROOT")}
        try:
            with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                home=Path(directory)
                for name in (".local/bin/heptatrader",
                    ".local/share/heptatrader/current/bin/hepta-paper-command",
                    ".local/share/heptatrader/current/bin/hepta-sessionctl",
                    ".npm-global/bin/openclaw",".config/heptatrader/paper.token",
                    ".config/heptatrader/crypto.token"):
                    path=home/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text("fixture")
                stack.enter_context(patch.object(d.Path,"home",return_value=home))
                stack.enter_context(patch.object(sys,"argv",[str(ROOT/"scripts/heptatrader_paper_driver.py"),
                    "--once","--instrument",instrument]))
                stack.enter_context(patch.object(d,"load_state",return_value=state))
                snap_mock=stack.enter_context(patch.object(d,"snapshot",return_value=snap,
                    side_effect=d.DriverError("untrusted position snapshot") if snapshot_error else None))
                quote_mock=stack.enter_context(patch.object(d,"native",side_effect=native))
                send=stack.enter_context(patch.object(d,"prepare_send"))
                reconcile=stack.enter_context(patch.object(d,"inspect_pending"))
                closes=stack.enter_context(patch.object(d,"should_close",wraps=d.should_close))
                for name in ("maybe_renew","atomic_json"):
                    stack.enter_context(patch.object(d,name))
                stack.enter_context(patch.object(d,"crypto_roundtrip_qualified",return_value=True))
                stack.enter_context(patch.object(d.signal,"signal"))
                for name in ("run","Popen"):
                    stack.enter_context(patch.object(d.subprocess,name,
                        side_effect=AssertionError("external processes forbidden in fixture")))
                self.assertEqual(d.main(),0)
                snap_mock.assert_called_once()
                self.assertFalse(snap_mock.call_args.kwargs["require_quote"])
                return SimpleNamespace(state=state,snap=snap,quote=quote,quote_call=quote_mock,
                                       send=send,reconcile=reconcile,closes=closes)
        finally:
            for name,value in globals_before.items():setattr(d,name,value)

    def test_price_independent_exits_do_not_request_quotes(self):
        for instrument in self.instruments:
            for flags,reason in (({"fenced":True},"OWNER_RECOVERY_REDUCE_ONLY"),
                                 ({"halt":True},"HALT_REDUCE_ONLY")):
                with self.subTest(instrument=instrument,reason=reason):
                    r=self.once(instrument,quote_error=True,**flags)
                    r.quote_call.assert_not_called();r.reconcile.assert_not_called()
                    r.send.assert_called_once()
                    self.assertEqual(r.send.call_args.args[6:],("CLOSE",))
                    self.assertEqual(r.send.call_args.kwargs,{"exit_reason":reason})
                    r.closes.assert_not_called()

    def test_pending_only_reconciles_the_original_command(self):
        pending={"command_id":"original-fixture-command","kind":"CLOSE"}
        for instrument in self.instruments:
            with self.subTest(instrument=instrument):
                r=self.once(instrument,pending=pending,halt=True,fenced=True,quote_error=True)
                r.reconcile.assert_called_once()
                self.assertIs(r.reconcile.call_args.args[0],r.state)
                self.assertIs(r.reconcile.call_args.args[6],r.snap)
                self.assertEqual(r.state["pending"],pending)
                r.quote_call.assert_not_called();r.send.assert_not_called()

    def test_price_exits_and_entries_still_require_fresh_quotes(self):
        for instrument in self.instruments:
            for position in (0.0,1.0):
                for flags in ({"quote_error":True},{"stale":True}):
                    with self.subTest(instrument=instrument,position=position,flags=flags):
                        r=self.once(instrument,position=position,**flags)
                        r.quote_call.assert_called_once()
                        r.send.assert_not_called();r.closes.assert_not_called()
                        self.assertIn("quote",r.state["last_error"])

    def test_valid_quote_still_triggers_original_price_exit(self):
        for instrument in self.instruments:
            with self.subTest(instrument=instrument):
                r=self.once(instrument)
                r.quote_call.assert_called_once();r.closes.assert_called_once()
                r.send.assert_called_once()
                self.assertEqual(r.send.call_args.args[6],"CLOSE")
                self.assertIs(r.send.call_args.args[7],r.quote)
                self.assertEqual(r.send.call_args.kwargs,{"exit_reason":"MAX_ADVERSE_MOVE"})

    def test_active_order_or_invalid_position_snapshot_prevents_another_close(self):
        for instrument in self.instruments:
            for flags in ({"active":True},{"snapshot_error":True}):
                with self.subTest(instrument=instrument,flags=flags):
                    r=self.once(instrument,halt=True,quote_error=True,**flags)
                    r.send.assert_not_called();r.reconcile.assert_not_called()


if __name__=="__main__":unittest.main()
