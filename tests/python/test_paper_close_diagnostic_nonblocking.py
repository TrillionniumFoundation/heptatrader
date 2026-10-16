"""CLOSE safety: optional provenance cannot block the original risk exit.

All sends are intercepted by in-process fakes. No IB, orders or service access.
"""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("paper_driver_close_diagnostic",ROOT/"scripts/heptatrader_paper_driver.py")
driver=importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class NonBlockingCloseProvenanceTests(unittest.TestCase):
    def test_bad_quote_fails_open_for_diagnostic_only(self):
        wrong=[
            {"bid":1.1,"ask":1.2},
            {"observed_at_ms":1001,"bid":1.1,"ask":1.2},
            {"observed_at_ms":999,"bid":1.2,"ask":1.1},
            {"observed_at_ms":999,"bid":"NaN","ask":1.2},
            {"observed_at_ms":999,"bid":"Infinity","ask":1.2},
            {"observed_at_ms":999,"bid":"x","ask":1.2},
            {"observed_at_ms":999,"bid":0,"ask":1.2},
            {"observed_at_ms":999,"bid":1.1},
            ["not","a","quote"],
        ]
        with mock.patch.object(driver,"now_ms",return_value=1000):
            for quote in wrong:
                with self.subTest(quote=quote):
                    record=driver.freeze_exit_trigger("original-close-1","MAX_ADVERSE_MOVE",25000,quote)
                    self.assertEqual(record["reason_code"],"MAX_ADVERSE_MOVE")
                    self.assertEqual(record["quote_evidence_status"],"UNVERIFIED_DIAGNOSTIC_OMITTED")
                    self.assertNotIn("quote_bid",record)
                    self.assertNotIn("quote_observed_at_ms",record)

    def test_valid_quote_keeps_original_evidence(self):
        with mock.patch.object(driver,"now_ms",return_value=1000):
            record=driver.freeze_exit_trigger("original-close-1","MODEL_REVERSAL",-25000,
                {"observed_at_ms":999,"bid":"1.1000","ask":"1.1002"})
        self.assertEqual(record["quote_evidence_status"],"VALIDATED")
        self.assertEqual(record["quote_bid"],1.1)
        self.assertEqual(record["reason_code"],"MODEL_REVERSAL")
        self.assertEqual(record["reason_source"],"DRIVER_EVALUATED")
        self.assertEqual(record["quote_observed_at_ms"],999)

    def test_original_close_still_prepared_sent_once_on_bad_quote(self):
        state={"pending":None,"position":25000.0,"entry_price":1.1}
        writes=[]
        calls=[]
        def paper_cmd(_bin,_socket,_token,_outbox,action,argv):
            calls.append((action,list(argv)))
            if action=="prepare-close":
                return {"ok":True,"command_id":"original-command-test-1"}
            if action=="submit":
                return {"ok":False,"reason":"TRANSPORT_UNKNOWN"}
            self.fail("unexpected mutation path:"+str(action))
        with (mock.patch.object(driver,"paper_cmd",side_effect=paper_cmd),
              mock.patch.object(driver,"atomic_json",side_effect=lambda _p,st:writes.append(copy.deepcopy(st))),
              mock.patch.object(driver,"response_transport_ok",return_value=False),
              mock.patch.object(driver,"response_application_status",return_value="uncertain"),
              mock.patch.object(driver,"response_application_reason",return_value="TRANSPORT_UNKNOWN"),
              mock.patch.object(driver,"now_ms",return_value=1000)):
            driver.prepare_send(state,Path("/unused"),"bin","sock","token","outbox",
                "CLOSE",{"observed_at_ms":1001,"bid":"oops","ask":1.2},
                exit_reason="HALT_REDUCE_ONLY")
        self.assertEqual([op for op,_ in calls],["prepare-close","submit"])
        self.assertEqual([s["pending"]["send_state"] for s in writes[:2]],
                         ["PREPARED","POSSIBLY_SENT"])
        self.assertEqual(state["pending"]["command_id"],"original-command-test-1")
        self.assertEqual(state["pending"]["exit_trigger"]["original_command_id"],
                         "original-command-test-1")
        self.assertEqual(state["pending"]["exit_trigger"]["reason_code"],"HALT_REDUCE_ONLY")
        self.assertEqual(state["pending"]["exit_trigger"]["quote_evidence_status"],
                         "UNVERIFIED_DIAGNOSTIC_OMITTED")
        self.assertEqual(state["pending"]["send_state"],"POSSIBLY_SENT")

    def test_none_quote_preserves_recovery_close(self):
        with mock.patch.object(driver,"now_ms",return_value=1000):
            record=driver.freeze_exit_trigger("close-1","OWNER_RECOVERY_REDUCE_ONLY",5000)
        self.assertEqual(record["reason_source"],"DRIVER_RECOVERY_OR_CANARY")
        self.assertNotIn("quote_evidence_status",record)

    def test_unknown_exit_reason_not_reinterpreted(self):
        with self.assertRaisesRegex(driver.DriverError,"not an original driver predicate"):
            driver.freeze_exit_trigger("close-1","UNKNOWN_UNREGISTERED_CAUSE",1000)


if __name__=="__main__":
    unittest.main()
