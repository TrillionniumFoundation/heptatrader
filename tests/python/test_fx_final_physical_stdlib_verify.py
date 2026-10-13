"""Adversarial tests for independent Mac/X230 frozen 4x4 PAPER receipt verification.

No broker, model inference, training, or real account data in fixtures.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from train_v2.fx_final_physical_stdlib_verify import (
    ANCHORS, HORIZONS, ORIGINAL, SOURCE_RULE, AuditHold, canonical,
    document, expected_names, load_bundle, sha, strict, validate_attempt,
    validate_health_record, write_once_private
)

def receipt(*,h=None,anchor=ANCHORS[1]):
    stage="decision" if h is None else "maturity-%03d"%h
    clock=anchor+1001 if h is None else anchor+h*60000+6500
    return {
        "schema":ORIGINAL+".attempt.v1","stage":stage,
        "state":"CENSORED",
        "reason_code":("EXCEPTION_Hold_ORIGINAL_READINESS_HOLD_OR_STALE"
                       if h is None else "NO_ELIGIBLE_ORIGINAL_PREACTION"),
        "attempt_recorded_at_ms":clock,
        "automatic_retry_allowed":False,
        "historical_price_or_future_quote_backfilled":False,
        "trade_authority":False,
        "broker_orders_sent":0,
        "broker_queries_sent":0,
        "model_promotion_allowed":False,
    }

def status_and_custody(anchor=ANCHORS[1]):
    stamp=anchor+1001
    health={
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":"61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f",
        "rule":copy.deepcopy(SOURCE_RULE),
        "at_ms":stamp-40000,
        "latest_source_quote_ms":stamp-40700,
        "latest_quote_age_ms":700,
        "source_prefixes":[{"segment":"precommit","bytes":123,"sha256":"f"*64}],
        "counts":{"USABLE":24},
        "usable":24,"unusable":0,
        "probes":[{"status":"USABLE","start_ms":anchor-i*60000}
                  for i in range(24)],
        "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
        "reasons":[],"automatic_trial_registration":False,
        "trading_authority":False,
    }
    health["source_prefix_digest"]=sha(canonical(health["source_prefixes"]))
    attempt={
        "schema":"heptatrader.fx-forward-original-health-custody.v1.snapshot-attempt.v1",
        "window":1,"anchor_ms":anchor,
        "attempt_started_at_ms":stamp,
        "durable_at_ms":stamp+3,
        "state":"HOLD",
        "reasons":["READINESS_SNAPSHOT_STALE_OR_FUTURE"],
        "source_health_status_sha256":sha(canonical(health)+b"\n"),
        "source_health_status_bytes":len(canonical(health)+b"\n"),
        "readiness_health_at_ms":health["at_ms"],
        "recomputed_source_usable_probes":24,
        "original_study_entry_readiness_proven":False,
        "no_retroactive_relabel_of_original_trial":True,
        "trade_authority":False,"broker_orders_sent":0,
    }
    return health,attempt

class PhysicalIndependentTests(unittest.TestCase):
    def test_exact_packet_count(self):
        self.assertEqual(len(expected_names()),36)
    def test_genuine_original_decision(self):
        row=receipt()
        self.assertEqual(validate_attempt(row,ANCHORS[1],"decision"),ANCHORS[1]+1001)
    def test_censored_120minute_receipt_time(self):
        row=receipt(h=120)
        self.assertEqual(validate_attempt(row,ANCHORS[1],"maturity-120",h=120),
                         ANCHORS[1]+120*60000+6500)
    def test_wrong_reason_fail_closed(self):
        row=receipt();row["reason_code"]="SOURCE_READY"
        with self.assertRaisesRegex(AuditHold,"ATTEMPT_IDENTITY"):
            validate_attempt(row,ANCHORS[1],"decision")
    def test_try_to_backfill_price_fails(self):
        row=receipt(h=30);row["historical_price_or_future_quote_backfilled"]=True
        with self.assertRaisesRegex(AuditHold,"ATTEMPT_IDENTITY"):
            validate_attempt(row,ANCHORS[1],"maturity-030",h=30)
    def test_maturity_before_frozen_horizon_fails(self):
        row=receipt(h=60);row["attempt_recorded_at_ms"]-=1
        with self.assertRaisesRegex(AuditHold,"OUTSIDE_PREREGISTERED_CLOCK"):
            validate_attempt(row,ANCHORS[1],"maturity-060",h=60)
    def test_maturity_after_max_attempt_fails(self):
        row=receipt(h=60);row["attempt_recorded_at_ms"]+=9000
        with self.assertRaisesRegex(AuditHold,"OUTSIDE_PREREGISTERED_CLOCK"):
            validate_attempt(row,ANCHORS[1],"maturity-060",h=60)
    def test_fabricated_trading_authority_fails(self):
        row=receipt(h=15);row["trade_authority"]=True
        with self.assertRaisesRegex(AuditHold,"ATTEMPT_IDENTITY"):
            validate_attempt(row,ANCHORS[1],"maturity-015",h=15)
    def test_boolean_fabricated_zero_order_not_valid(self):
        row=receipt();row["broker_queries_sent"]=False
        with self.assertRaisesRegex(AuditHold,"ATTEMPT_IDENTITY"):
            validate_attempt(row,ANCHORS[1],"decision")
    def test_original_24_of_24_health_still_stale(self):
        health,attempt=status_and_custody()
        self.assertEqual(validate_health_record(health,attempt,ANCHORS[1]),40000)
    def test_later_healthy_state_cannot_reopen_failure(self):
        health,attempt=status_and_custody()
        health["at_ms"]=attempt["attempt_started_at_ms"]-1000
        health["latest_source_quote_ms"]=health["at_ms"]-health["latest_quote_age_ms"]
        with self.assertRaisesRegex(AuditHold,"WAS_NOT_STALE"):
            validate_health_record(health,attempt,ANCHORS[1])
    def test_quote_stale_even_if_health_24_of_24(self):
        health,attempt=status_and_custody()
        health["latest_quote_age_ms"]=6000
        health["latest_source_quote_ms"]=health["at_ms"]-6000
        with self.assertRaisesRegex(AuditHold,"LAST_QUOTE_HEALTH_INVALID"):
            validate_health_record(health,attempt,ANCHORS[1])
    def test_modified_health_prefix_rejected(self):
        health,attempt=status_and_custody()
        health["source_prefixes"][0]["bytes"]=777
        with self.assertRaisesRegex(AuditHold,"SOURCE_PREFIX_DIGEST"):
            validate_health_record(health,attempt,ANCHORS[1])
    def test_false_probe_status_count_rejected(self):
        health,attempt=status_and_custody()
        health["probes"][0]["status"]="PATH_GAP"
        with self.assertRaisesRegex(AuditHold,"NOT_ORIGINAL_24_24_READY"):
            validate_health_record(health,attempt,ANCHORS[1])
    def test_health_custody_pretends_entry_was_admitted(self):
        health,attempt=status_and_custody()
        attempt["original_study_entry_readiness_proven"]=True
        with self.assertRaisesRegex(AuditHold,"CUSTODY_DID_NOT_HOLD"):
            validate_health_record(health,attempt,ANCHORS[1])
    def test_witness_capture_after_deadline_fails(self):
        health,attempt=status_and_custody()
        attempt["durable_at_ms"]=ANCHORS[1]+5000
        with self.assertRaisesRegex(AuditHold,"SNAPSHOT_CAPTURE_CLOCK"):
            validate_health_record(health,attempt,ANCHORS[1])
    def test_json_duplicate_key_refused(self):
        with self.assertRaisesRegex(AuditHold,"DUPLICATED_JSON_KEY"):
            strict(b'{"key":1,"key":2}')
    def test_json_nonfinite_refused(self):
        with self.assertRaisesRegex(AuditHold,"NONFINITE_JSON"):
            strict(b'{"a":NaN}')
    def test_report_owner_private_write_once(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t)/"reports";d.mkdir(mode=0o700)
            p=d/"receipt.json"
            checksum=write_once_private(p,{"test":True})
            self.assertEqual(checksum,sha(p.read_bytes()))
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600)
            with self.assertRaises((AuditHold,FileExistsError)):
                write_once_private(p,{"test":False})
    def test_public_output_root_not_accepted(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t)/"reports";d.mkdir(mode=0o755)
            with self.assertRaisesRegex(AuditHold,"OUTPUT_PARENT_NOT_PRIVATE"):
                write_once_private(d/"result.json",{"test":True})
    def test_zip_public_member_refused(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"bundle.zip"
            with zipfile.ZipFile(p,"w") as z:
                for i,name in enumerate(sorted(expected_names())):
                    info=zipfile.ZipInfo(name)
                    info.external_attr=(stat.S_IFREG|(0o644 if i==0 else 0o600))<<16
                    z.writestr(info,b'{"state":"NO_TRADE"}')
            p.chmod(0o600)
            with self.assertRaisesRegex(AuditHold,"ZIP_FILE_NOT_PRIVATE_REGULAR"):
                load_bundle(p)
    def test_zip_missing_member_refused(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"bundle.zip"
            with zipfile.ZipFile(p,"w") as z:
                info=zipfile.ZipInfo("original/plan.json")
                info.external_attr=(stat.S_IFREG|0o600)<<16
                z.writestr(info,b'{"state":"NO_TRADE"}')
            p.chmod(0o600)
            with self.assertRaisesRegex(AuditHold,"ZIP_FILE_COUNT"):
                load_bundle(p)
    def test_document_not_json_mapping_refused(self):
        with self.assertRaisesRegex(AuditHold,"DOCUMENT_MUST_BE_JSON_OBJECT"):
            document({"x":b"[]"},"x")

if __name__=="__main__":unittest.main()
