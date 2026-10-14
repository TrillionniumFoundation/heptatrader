"""Negative tests for future-only, original-paper readiness snapshot custody."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,os,tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from train_v2.fx_original_readiness_custody import (
    SCHEMA,RULE,ORIGINAL_PLAN_SHA,ORIGINAL_SEAL_SHA,
    CustodyHold, canonical, digest, evaluate_health,
    private_dir,read_exact,strict,snapshot_once,write_once,verify_frozen,
)

NOW=1791833701000
PLAN={"period_sha256":"f"*64}
def complete(*,n=24):
    statuses=["USABLE"]*n+["PATH_GAP"]*(24-n)
    p={
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":PLAN["period_sha256"],
        "rule":deepcopy(RULE),"state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
        "at_ms":NOW-1000,
        "source_prefixes":[{"sha256":"a"*64,"bytes":123}],
        "source_prefix_digest":None,
        "counts":{"USABLE":n} if n==24 else {"USABLE":n,"PATH_GAP":24-n},
        "probes":[{"status":x,"start_ms":NOW-i*60000} for i,x in enumerate(statuses)],
        "usable":n,"unusable":24-n,
        "latest_source_quote_ms":NOW-1300,
        "latest_quote_age_ms":300,
        "automatic_trial_registration":False,
        "trading_authority":False,
        "reasons":[],
    }
    p["source_prefix_digest"]=digest(canonical(p["source_prefixes"]))
    return p

class HealthCustodyTests(unittest.TestCase):
    def test_genuine_ready_still_not_product_authority(self):
        self.assertEqual(evaluate_health(complete(),PLAN,NOW),[])
    def test_period_mismatch_hold(self):
        s=complete();s["period_sha256"]="e"*64
        self.assertIn("READINESS_PERIOD_NOT_ORIGINAL_PAPER",evaluate_health(s,PLAN,NOW))
    def test_rule_loosen_not_admitted(self):
        s=complete();s["rule"]["minimum_usable"]=1
        self.assertIn("SOURCE_MONITOR_RULE_CHANGED",evaluate_health(s,PLAN,NOW))
    def test_inconsistent_usable_counts(self):
        s=complete();s["counts"]["USABLE"]=23
        self.assertIn("READINESS_PROBE_COUNTS_NOT_REPRODUCIBLE",evaluate_health(s,PLAN,NOW))
    def test_foreign_source_prefix_not_admitted(self):
        s=complete();s["source_prefixes"][0]["bytes"]=999
        self.assertIn("READINESS_PREFIX_DIGEST_CHANGED",evaluate_health(s,PLAN,NOW))
    def test_hold_gate_enforced_even_when_latest_quote_fresh(self):
        s=complete(n=19);s["state"]="HOLD_NEW_FUSION_WINDOW"
        s["reasons"]=["INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE"]
        self.assertIn("READINESS_HOLD_OR_INSUFFICIENT",evaluate_health(s,PLAN,NOW))
    def test_stale_full_snapshot_not_promoted(self):
        s=complete();s["at_ms"]=NOW-31000
        self.assertIn("READINESS_SNAPSHOT_STALE_OR_FUTURE",evaluate_health(s,PLAN,NOW))
    def test_snapshot_clock_future_rejected(self):
        s=complete();s["at_ms"]=NOW+1
        self.assertIn("READINESS_SNAPSHOT_STALE_OR_FUTURE",evaluate_health(s,PLAN,NOW))
    def test_mismatched_latest_quote_age_not_promoted(self):
        s=complete();s["latest_quote_age_ms"]=301
        self.assertIn("LAST_ORIGINAL_QUOTE_NOT_FRESH_OR_CLOCK_UNPROVEN",evaluate_health(s,PLAN,NOW))
    def test_unauthorized_source_registration_denied(self):
        s=complete();s["automatic_trial_registration"]=True
        self.assertIn("SOURCE_MONITOR_AUTHORITY_CHANGED",evaluate_health(s,PLAN,NOW))
    def test_retained_original_raw_must_be_private(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"data.json"
            p.write_text('{"ready":true}')
            p.chmod(0o644)
            with self.assertRaisesRegex(CustodyHold,"SOURCE_NOT_PRIVATE_REGULAR"):
                read_exact(p)
    def test_no_overwrite_and_fsync(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"evidence"
            p.mkdir(mode=0o700)
            d=p/"raw.json"
            write_once(d,b'{"state":"CENSORED"}\n')
            self.assertEqual(d.read_bytes(),b'{"state":"CENSORED"}\n')
            self.assertEqual(d.stat().st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):
                write_once(d,b'{"state":"TRADE"}\n')
    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(CustodyHold,"DUPLICATE_JSON_KEY"):
            strict(b'{"x":1,"x":2}')
    def test_nonfinite_numbers_rejected(self):
        with self.assertRaisesRegex(CustodyHold,"NONFINITE_JSON"):
            strict(b'{"x":NaN}')
    def test_directories_must_be_private(self):
        with tempfile.TemporaryDirectory() as t:
            Path(t).chmod(0o755)
            with self.assertRaisesRegex(CustodyHold,"PRIVATE_DIRECTORY_REQUIRED"):
                private_dir(Path(t))
    def test_wrong_program_does_not_relabel_original_window(self):
        self.assertEqual(ORIGINAL_PLAN_SHA,"02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04")
        self.assertEqual(ORIGINAL_SEAL_SHA,"1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b")
        # Explicitly no --window 0 option or retroactive status "fix".
        with self.assertRaisesRegex(CustodyHold,"PRESENT_OR_FUTURE_ONLY_NO_OLD_WINDOW"):
            with tempfile.TemporaryDirectory() as t:
                root=Path(t)/"custody";root.mkdir(mode=0o700)
                with patch("train_v2.fx_original_readiness_custody.verify_plan",
                           return_value=(PLAN,{"planned_capture_windows":[]})):
                    snapshot_once(root,root,0)

if __name__=="__main__": unittest.main()
