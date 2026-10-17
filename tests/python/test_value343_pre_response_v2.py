"""Physical inotify + original RESPONSE timing proof adversarial tests (Pocket4)."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from laya_cell import value343_prospective as shadow
from tests.python.test_value343_predecision_shadow import synthetic_request, event


def local_setup(now):
    req=synthetic_request(now)
    rid=req["request_id"]
    original=event(req,now)
    response={
        "kind":"RESPONSE","sha256":"c"*64,
        "captured_at_ms":now+1000,"written_at_ms":now+1020,
        "data":{"request_id":rid,
                "request_sha256":shadow.digest(req),
                "available_at_ms":now+800}
    }
    return req,rid,original,response


def write_prediction(root,req,original,now):
    rid=req["request_id"]
    for name in ("predictions","misses","response_proofs","scores","rejects"):
        (root/name).mkdir(exist_ok=True)
    body={
        "request_id":rid,"input_request_sha256":shadow.digest(req),
        "sealed_at_ms":now,"source_event_sha256":original["sha256"],
        "source_prefix":original["_witness"],
        "predictions_after_actual_reported_fees_usd":{"BUY":-4.,"SELL":-8.},
        "train_only_median_baseline_usd":-12.,
    }
    shadow.write_once(root/"predictions"/(rid+".json"),body)
    return body


class TwoSidedPreResponseProofTests(unittest.TestCase):
    def test_original_response_later_qualifies_only_after_it_appears(self):
        now=int(time.time()*1000)
        req,rid,origin,response=local_setup(now)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);write_prediction(root,req,origin,now)
            with patch.object(shadow,"OUT",root):
                empty=shadow.attest_responses([],{rid:origin},{})
                self.assertEqual(empty["still_no_response"],1)
                self.assertEqual(list((root/"response_proofs").glob("*.json")),[])
                done=shadow.attest_responses([response],{rid:origin},{})
                self.assertEqual(done["verified_new"],1)
                written=json.loads((root/"response_proofs"/(rid+".json")).read_bytes())
                self.assertEqual(written["original_response_available_at_ms"],now+800)
                self.assertEqual(written["status"],"VERIFIED_PRE_ORIGINAL_RESPONSE")
                self.assertFalse(written["automatic_trading"])
                repeated=shadow.attest_responses([response],{rid:origin},{})
                self.assertEqual(repeated["already_attested"],1)

    def test_seal_after_original_response_cannot_hide_behind_later_decision(self):
        now=int(time.time()*1000)
        req,rid,origin,response=local_setup(now)
        response["data"]["available_at_ms"]=now-1
        response["captured_at_ms"]=now+800
        response["written_at_ms"]=now+820
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);write_prediction(root,req,origin,now)
            with patch.object(shadow,"OUT",root):
                decision=shadow.attest_responses([response],{rid:origin},{})
                self.assertEqual(decision["censored_new"],1)
                self.assertFalse(list((root/"response_proofs").glob("*.json")))
                rejected=json.loads((root/"rejects"/(rid+".json")).read_bytes())
                self.assertEqual(rejected["status"],
                     "CENSORED_SEAL_NOT_PROVEN_BEFORE_ORIGINAL_RESPONSE")
                self.assertFalse(rejected["score_valid"])

    def test_response_wrong_original_request_hash_is_invalid(self):
        now=int(time.time()*1000)
        req,rid,origin,response=local_setup(now)
        response["data"]["request_sha256"]="f"*64
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);write_prediction(root,req,origin,now)
            with patch.object(shadow,"OUT",root):
                out=shadow.attest_responses([response],{rid:origin},{})
                self.assertEqual(out["censored_new"],1)
                self.assertFalse(list((root/"response_proofs").iterdir()))

    def test_original_request_changed_after_seal_is_fatal(self):
        now=int(time.time()*1000)
        req,rid,origin,response=local_setup(now)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);write_prediction(root,req,origin,now)
            orig2=deepcopy(origin);orig2["sha256"]="e"*64
            with patch.object(shadow,"OUT",root):
                with self.assertRaisesRegex(ValueError,"SOURCE_PROVENANCE_CHANGED"):
                    shadow.attest_responses([response],{rid:orig2},{})

    def test_duplicate_original_responses_do_not_qualify(self):
        now=int(time.time()*1000)
        req,rid,origin,response=local_setup(now)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);write_prediction(root,req,origin,now)
            with patch.object(shadow,"OUT",root):
                with self.assertRaisesRegex(ValueError,"DUPLICATE_ORIGINAL_MODEL_RESPONSE"):
                    shadow.attest_responses([response,response],{rid:origin},{})

    def test_actual_inotify_wakes_on_journal_append_not_health_file(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);os.chmod(root,0o700)
            fd=shadow.watch_handle(root)
            try:
                health=root/"watch.health.json"
                with health.open("w") as f:f.write('{"status":"healthy"}\n')
                ok,overflow=shadow.original_journal_events(fd)
                self.assertFalse(ok);self.assertEqual(overflow,0)
                journal=root/"test-0000.jsonl"
                with journal.open("wb") as f:
                    f.write(b'{"kind":"REQUEST"}\n')
                    f.flush();os.fsync(f.fileno())
                ok,overflow=shadow.original_journal_events(fd)
                self.assertTrue(ok);self.assertEqual(overflow,0)
            finally:
                os.close(fd)

    def test_receipt_write_crash_safe_create_only(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"receipts.json"
            payload={"a":1,"BROKER_ORDERS":0}
            expect=hashlib.sha256(
                (json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n").encode()
            ).hexdigest()
            sha=shadow.write_once(p,payload)
            self.assertEqual(sha,expect)
            self.assertEqual(list(Path(td).glob(".receipt-*")),[])
            self.assertEqual(json.loads(p.read_bytes()),payload)
            with self.assertRaises(FileExistsError):
                shadow.write_once(p,payload)
            self.assertEqual(json.loads(p.read_bytes()),payload)

    def test_protocol_v2_cannot_claim_old_v1_source(self):
        self.assertTrue(shadow.SCHEMA.endswith(".v2"))
        self.assertNotEqual(shadow.OUT,Path(
            "/home/alex/.local/state/heptatrader/operator-evidence/value343-predecision-shadow-20261017"))
        self.assertFalse(shadow.NO_AUTHORITY["automatic_trading"])


if __name__=="__main__":
    unittest.main()
