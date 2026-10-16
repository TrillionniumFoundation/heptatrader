"""Offline-only durable CLOSE receipt tests; no IB API, driver loop or orders."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("close_durable_driver",REPO/"scripts/heptatrader_paper_driver.py")
d=importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def receipt(command="cmd-one", *, price=1.119, number=41):
    state={"position":25000.0}
    pending={"command_id":command,"broker_order_id":number,
             "position_before_send":25000.0,
             "exit_trigger":d.freeze_exit_trigger(command,"MODEL_REVERSAL",25000)}
    fill={"broker_execution_id":"exec-"+command,
          "broker_execution_ids":["exec-"+command],
          "venue_correlation_id":"hepta-v1-sha256:"+hashlib.sha256(command.encode()).hexdigest(),
          "quantity":25000.0,"price":price,"terminal":True}
    result=d.record_correlated_close(state,pending,fill,0)
    return state,result


class DurableCloseReceipts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="close-receipt-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/"close-command-receipts"
        self.assertIsNone(d.CLOSE_RECEIPT_ROOT)

    def single(self):
        return list(self.folder.glob("*.json"))

    def test_create_private_fsynced_immutable(self):
        _,r=receipt()
        self.assertEqual(d.persist_close_receipt(self.folder,r),"FSYNCED_CREATE_ONLY")
        files=self.single()
        self.assertEqual(len(files),1)
        self.assertEqual(files[0].stat().st_mode & 0o777,0o600)
        self.assertEqual(self.folder.stat().st_mode & 0o777,0o700)
        self.assertEqual(json.loads(files[0].read_text())["exit_trigger"]["reason_code"],"MODEL_REVERSAL")
        self.assertEqual(files[0].name,hashlib.sha256(b"cmd-one").hexdigest()+".json")

    def test_restart_same_command_is_idempotent(self):
        _,r=receipt()
        d.persist_close_receipt(self.folder,r)
        p=self.single()[0]; original=p.read_bytes()
        same=copy.deepcopy(r);same["recorded_at_ms"]+=12345
        self.assertEqual(d.persist_close_receipt(self.folder,same),"EXACT_EXISTING")
        self.assertEqual(p.read_bytes(),original)

    def test_conflict_never_overwrites_original(self):
        _,r=receipt()
        d.persist_close_receipt(self.folder,r)
        p=self.single()[0];original=p.read_bytes()
        altered=copy.deepcopy(r);altered["broker_fill_quantity"]=1000.0
        with self.assertRaisesRegex(d.DriverError,"conflicting immutable"):
            d.persist_close_receipt(self.folder,altered)
        self.assertEqual(p.read_bytes(),original)

    def test_reject_unscoped_or_nonnumeric_receipt(self):
        _,r=receipt()
        for mutated in ({**r,"instrument":"BTC.USD"},
                        {**r,"schema":"NOT_A_RECEIPT"},
                        {**r,"broker_fill_quantity":float("nan")}):
            with self.subTest(mutated=mutated.get("schema")):
                with self.assertRaises((d.DriverError,ValueError)):
                    d.persist_close_receipt(self.folder,mutated)
        self.assertFalse(self.single())

    def test_symlink_existing_receipt_fails_closed(self):
        _,r=receipt()
        self.folder.mkdir(mode=0o700)
        target=Path(self.temp.name)/"sentinel"
        target.write_text("untouched")
        (self.folder/(hashlib.sha256(b"cmd-one").hexdigest()+".json")).symlink_to(target)
        with self.assertRaises(OSError):
            d.persist_close_receipt(self.folder,r)
        self.assertEqual(target.read_text(),"untouched")

    def test_corrupted_or_truncated_existing_receipt_preserved(self):
        _,r=receipt()
        self.folder.mkdir(mode=0o700)
        p=self.folder/(hashlib.sha256(b"cmd-one").hexdigest()+".json")
        p.write_bytes(b'{"incomplete":')
        p.chmod(0o600)
        with self.assertRaises(ValueError):
            d.persist_close_receipt(self.folder,r)
        self.assertEqual(p.read_bytes(),b'{"incomplete":')

    def test_more_than_64_receipts_remain_durable(self):
        state={"position":25000.0}
        for i in range(70):
            cmd="episode-"+str(i)
            pending={"command_id":cmd,"broker_order_id":i+1,
                "exit_trigger":d.freeze_exit_trigger(cmd,"MAX_ADVERSE_MOVE",25000)}
            fill={"broker_execution_id":"execution-"+cmd,
                  "broker_execution_ids":["execution-"+cmd],
                  "venue_correlation_id":"hepta-v1-sha256:"+hashlib.sha256(cmd.encode()).hexdigest(),
                  "quantity":25000.0,"price":1.1,"terminal":True}
            r=d.record_correlated_close(state,pending,fill,0)
            d.persist_close_receipt(self.folder,r)
        self.assertEqual(len(state["close_command_attribution"]),64)
        self.assertEqual(len(self.single()),70)

    def test_durable_io_failure_cannot_block_confirmed_close(self):
        def correlated_fill(cmd):
            return {"order_id":42,"command_id":cmd,"side":"SELL",
                    "venue_correlation_id":"hepta-v1-sha256:"+hashlib.sha256(cmd.encode()).hexdigest(),
                    "broker_execution_id":"exec-"+cmd,
                    "broker_execution_ambiguous":False,"economic_fill":True,
                    "filled_quantity":25000,"average_fill_price":1.119,"terminal":True}
        cmd="original-close-io-error"
        state={"pending":{"kind":"CLOSE","command_id":cmd,"broker_order_id":42,
                          "send_state":"ACKNOWLEDGED","position_before_send":25000.0,
                          "exit_trigger":d.freeze_exit_trigger(cmd,"MODEL_REVERSAL",25000)},
               "position":25000.0,"entry_price":1.1200,"entries":1,"exits":0,
               "closed_trades":0,"gross_pnl":0.0,"last_error":""}
        snap={"position":0.0,"active":[],"orders":{"recent_orders":[correlated_fill(cmd)]}}
        sent={"ok":True,"result":{"payload":{"authoritative":True,"command_id":cmd,
                                            "command_status":"accepted","order_id":42}}}
        with patch.object(d,"CLOSE_RECEIPT_ROOT",self.folder),\
             patch.object(d,"persist_close_receipt",side_effect=OSError("SIMULATED_DISK_ERROR")),\
             patch.object(d,"paper_cmd",return_value=sent),\
             patch.object(d,"atomic_json"):
            d.inspect_pending(state,Path("/unused"),"b","s","t","o",snap)
        self.assertEqual(state["position"],0.0)
        self.assertIsNone(state["pending"])
        self.assertEqual(state["exits"],1)
        self.assertEqual(state["closed_trades"],1)
        self.assertEqual(state["last_close_attribution_error"]["exception_type"],"OSError")
        self.assertEqual(state["close_command_attribution"][0]["exit_trigger"]["reason_code"],"MODEL_REVERSAL")


if __name__=="__main__":
    unittest.main()
