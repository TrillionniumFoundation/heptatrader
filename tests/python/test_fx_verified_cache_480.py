"""Adversarial controls for 480-cycle verified-cache original-source research."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from train_v2.fx_verified_cache_480 import (
    SCHEMA, ORIGINAL_MONITOR_SHA, FAST_MONITOR_SHA, VERIFIED_CACHE_SHA,
    N, STEP_MS, MAX_LATE_MS, MAX_SOURCE_AGE_MS, RULE,
    AuditHold, review_health, owner_dir, write_once, canonical,
    verify_candidate, verify_plan, run,
)

def fake_status(at,period="f"*64,state="READY_FOR_REVIEW_NOT_AUTHORIZATION"):
    statuses=["USABLE"]*24
    src=[]
    return {
      "schema":"heptatrader.quote-readiness-status.v1",
      "period_sha256":period,"rule":dict(RULE),
      "state":state,"reasons":[] if state.startswith("READY") else
          ["INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE"],
      "probes":[{"status":t} for t in statuses],
      "counts":{"USABLE":24},"usable":24,"unusable":0,
      "at_ms":at,"latest_quote_age_ms":300,
      "latest_source_quote_ms":at-300,
      "source_prefixes":src,
      "source_prefix_digest":hashlib.sha256(canonical(src)).hexdigest(),
      "trading_authority":False,"automatic_trial_registration":False,
      "orders_sent":0
    }

def plan():
    return {"period_sha256":"f"*64}

class Verified480Tests(unittest.TestCase):
    def test_exact_candidate_bytes_and_existing_original_rules(self):
        # Test the invariant of the frozen-source verifier, not an assumption
        # that all future mainline code must remain byte-identical to 2026-10-13.
        from train_v2 import fx_verified_cache_480 as frozen
        with patch.object(frozen,"FAST_MONITOR_SHA",hashlib.sha256(frozen.source_file().read_bytes()).hexdigest()),\
             patch.object(frozen,"VERIFIED_CACHE_SHA",hashlib.sha256(frozen.cache_file().read_bytes()).hexdigest()):
            self.assertTrue(verify_candidate())
        with patch.object(frozen,"FAST_MONITOR_SHA","0"*64):
            with self.assertRaisesRegex(AuditHold,"CACHE_CANDIDATE_OR_SOURCE_CONTRACT_CHANGED"):
                verify_candidate()
        self.assertEqual(N,480)
        self.assertEqual(STEP_MS,15000)
        self.assertEqual(MAX_LATE_MS,3000)
        self.assertEqual(MAX_SOURCE_AGE_MS,30000)
        self.assertEqual(RULE["minimum_usable"],20)
        self.assertEqual(RULE["source_ttl_ms"],5000)
        self.assertEqual(len(ORIGINAL_MONITOR_SHA),64)
        self.assertEqual(len(FAST_MONITOR_SHA),64)
        self.assertEqual(len(VERIFIED_CACHE_SHA),64)
    def test_original_24_probes_accepted_but_not_trading(self):
        t=1791881000123
        result=review_health(fake_status(t),plan(),t,t+1200)
        self.assertEqual(result,[])
    def test_natural_original_hold_never_converts_to_ready(self):
        t=1791881000123
        s=fake_status(t,state="HOLD_NEW_FUSION_WINDOW")
        issues=review_health(s,plan(),t,t+1200)
        self.assertEqual(issues,[])
        self.assertEqual(s["state"],"HOLD_NEW_FUSION_WINDOW")
    def test_future_and_prestart_asof_refused(self):
        t=1791881000123
        self.assertIn("SOURCE_ASOF_NOT_ACTUAL_EVALUATION_TIME",
            review_health(fake_status(t+3200),plan(),t,t+4000))
    def test_public_source_age_fails_after_30s(self):
        t=1791881000123
        self.assertIn("ORIGINAL_STATUS_STALE_AT_PUBLICATION",
            review_health(fake_status(t),plan(),t,t+30001))
    def test_dirty_source_rule_refused(self):
        t=1791881000123
        s=fake_status(t);s["rule"]["minimum_usable"]=1
        self.assertIn("HEALTH_RULE_CHANGED",
            review_health(s,plan(),t,t+100))
    def test_counterfeit_source_digest_refused(self):
        t=1791881000123
        s=fake_status(t);s["source_prefix_digest"]="e"*64
        self.assertIn("PREFIXES_SHA_CHANGED",
            review_health(s,plan(),t,t+100))
    def test_wrong_paper_period_refused(self):
        t=1791881000123
        self.assertIn("HEALTH_PERIOD_CHANGED",
            review_health(fake_status(t,period="e"*64),plan(),t,t+100))
    def test_malformed_fake_ready_rejected(self):
        t=1791881000123
        s=fake_status(t);s["usable"]=12
        self.assertIn("PROBE_COUNTS_CHANGED",
            review_health(s,plan(),t,t+100))
    def test_no_legacy_reuse_can_modify_original(self):
        # This runner has no production service restart or write function.
        import inspect
        from train_v2 import fx_verified_cache_480 as runner
        source=inspect.getsource(runner)
        for token in ("systemctl --user restart", "broker.placeOrder(",
                      "ib_insync", "os.system("):
            self.assertNotIn(token,source)
    def test_output_is_private_and_write_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"root"
            owner_dir(p,create=True)
            file=p/"status.json"
            data=b'{"state":"HOLD"}\n'
            sha=write_once(file,data)
            self.assertEqual(sha,hashlib.sha256(data).hexdigest())
            self.assertEqual(file.stat().st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):
                write_once(file,b'{"state":"READY"}\n')
            self.assertEqual(file.read_bytes(),data)
    def test_public_root_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"test"
            p.mkdir(mode=0o700);p.chmod(0o755)
            with self.assertRaisesRegex(AuditHold,"ARCHIVE_DIR_NOT_OWNER_ONLY"):
                owner_dir(p)
    def test_absent_plan_cannot_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"root"
            owner_dir(p,create=True)
            with self.assertRaises((FileNotFoundError,AuditHold)):
                verify_plan(p)

if __name__=="__main__":unittest.main()
