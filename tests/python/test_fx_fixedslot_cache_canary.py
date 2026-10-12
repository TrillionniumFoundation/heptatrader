"""No-replay and evidence-fencing tests for a fixed 10:05 cached-health canary."""
from __future__ import annotations
import json,hashlib,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from train_v2.fx_fixedslot_cache_canary import (
    ANCHOR_MS,CANARY_START_MS,CAPTURE_MS,WINDOW_START_MS,WINDOW_END_MS,
    ORIGINAL_PLAN_SHA,ORIGINAL_SEAL_SHA,Hold,RULE,
    canonical,sha,health_quality,private_dir,read_private,read_pinned_research_code,
    write_once,compare_once,freeze
)

def fake_status(t=CAPTURE_MS,usable=24):
    state="READY_FOR_REVIEW_NOT_AUTHORIZATION" if usable>=20 else "HOLD_NEW_FUSION_WINDOW"
    counts={"USABLE":usable}
    if usable<24: counts["PATH_GAP"]=24-usable
    status={
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":"61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f",
        "rule":dict(RULE),
        "at_ms":t-10000,
        "state":state,"reasons":[] if usable>=20 else ["INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE"],
        "counts":counts,
        "probes":[{"status":"USABLE"} for _ in range(usable)]+
                 [{"status":"PATH_GAP"} for _ in range(24-usable)],
        "usable":usable,"unusable":24-usable,
        "source_prefixes":[{"source":"original","hash":"a"*64}],
        "latest_source_quote_ms":t-12000,
        "latest_quote_age_ms":2000,
        "automatic_trial_registration":False,"trading_authority":False,"orders_sent":0,
    }
    status["source_prefix_digest"]=sha(canonical(status["source_prefixes"]))
    return status

def tmp_test():
    t=tempfile.TemporaryDirectory()
    root=Path(t.name)/"canary";root.mkdir(mode=0o700)
    a=root/"baseline.json";b=root/"cached.json"
    a.write_bytes(canonical(fake_status())+b"\n")
    b.write_bytes(canonical(fake_status())+b"\n")
    a.chmod(0o600);b.chmod(0o600)
    (root/"plan.json").write_text('{"intentionally":"test-only"}')
    (root/"plan.json").chmod(0o600)
    return t,root,{"original_health_file":str(a),"canary_health_file":str(b)}

class CanaryTests(unittest.TestCase):
    def test_complete_original_paper_period_digest_is_64_hex(self):
        from train_v2.fx_fixedslot_cache_canary import PERIOD_SHA
        self.assertEqual(PERIOD_SHA,"61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f")
        self.assertEqual(len(PERIOD_SHA),64)

    def test_prior_plan_sha_identity_is_pinned(self):
        self.assertEqual(ORIGINAL_PLAN_SHA,
            "02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04")
        self.assertEqual(ORIGINAL_SEAL_SHA,
            "1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b")
    def test_one_precise_future_fixed_anchor(self):
        self.assertEqual(CANARY_START_MS,ANCHOR_MS-320000)
        self.assertEqual(CAPTURE_MS,ANCHOR_MS+2000)
        self.assertTrue(WINDOW_START_MS<=CAPTURE_MS<=WINDOW_END_MS)
    def test_ready_does_not_upgrade_trade(self):
        result,age=health_quality(fake_status(),CAPTURE_MS)
        self.assertEqual(result,[])
        self.assertEqual(age,10000)
    def test_original_monitor_stale_preserved(self):
        x=fake_status()
        x["at_ms"]=CAPTURE_MS-50000
        self.assertIn("STATUS_SNAPSHOT_NOT_CAUSALLY_FRESH",
                      health_quality(x,CAPTURE_MS)[0])
    def test_status_period_changed_rejected(self):
        x=fake_status();x["period_sha256"]="a"*64
        self.assertIn("WRONG_PAPER_PERIOD",health_quality(x,CAPTURE_MS)[0])
    def test_probe_counts_cannot_be_invented(self):
        x=fake_status();x["counts"]["USABLE"]=23
        self.assertIn("SOURCE_PROBE_TOTAL_NOT_REPRODUCIBLE",
                      health_quality(x,CAPTURE_MS)[0])
    def test_monitor_rule_cannot_be_relaxed(self):
        x=fake_status();x["rule"]["minimum_usable"]=1
        self.assertIn("ORIGINAL_24_PROBE_RULE_CHANGED",
                      health_quality(x,CAPTURE_MS)[0])
    def test_source_prefix_hash_mutation_fails(self):
        x=fake_status();x["source_prefixes"][0]["hash"]="b"*64
        self.assertIn("SOURCE_PREFIX_FINGERPRINT_CHANGED",
                      health_quality(x,CAPTURE_MS)[0])
    def test_19_probe_hold_cannot_be_laundered(self):
        x=fake_status(usable=19)
        self.assertIn("SOURCE_MONITOR_NOT_READY",
                      health_quality(x,CAPTURE_MS)[0])
    def test_unauthorized_trading_field_rejected(self):
        x=fake_status();x["trading_authority"]=True
        self.assertIn("RESEARCH_SOURCE_AUTHORITY_CHANGED",
                      health_quality(x,CAPTURE_MS)[0])
    def test_malformed_status_not_promoted(self):
        x=fake_status();x["probes"]=x["probes"][1:]
        self.assertIn("PROBE_POPULATION_NOT_24",
                      health_quality(x,CAPTURE_MS)[0])
    def test_public_input_not_trusted(self):
        t,r,p=tmp_test()
        with t:
            q=Path(p["original_health_file"]);q.chmod(0o644)
            with self.assertRaisesRegex(Hold,"SOURCE_OWNERSHIP_OR_SIZE"):
                read_private(q)
    def test_duplicate_output_refused(self):
        t,r,p=tmp_test()
        with t:
            x=r/"snapshot.json"
            write_once(x,{"state":"CENSORED"})
            with self.assertRaises(FileExistsError):
                write_once(x,{"state":"READY"})
            self.assertEqual(json.loads(x.read_text())["state"],"CENSORED")
    def test_new_source_code_bytes_can_be_pinned_without_private_mode(self):
        t,r,p=tmp_test()
        with t:
            file=r/"code.py";file.write_text("print('research')\n");file.chmod(0o664)
            self.assertEqual(read_pinned_research_code(file),file.read_bytes())
    def test_future_capture_once_without_change_to_original(self):
        t,r,p=tmp_test()
        with t,patch("train_v2.fx_fixedslot_cache_canary.verify_plan",return_value=p):
            receipt=compare_once(r,captured_at=CAPTURE_MS)
            self.assertEqual(receipt["state"],"TWO_READONLY_SOURCE_HEALTH_SNAPSHOTS_PRESERVED")
            self.assertTrue(receipt["baseline"]["healthy_at_exact_time"])
            self.assertTrue(receipt["canary"]["healthy_at_exact_time"])
            self.assertFalse(receipt["trade_authority"])
            self.assertEqual(receipt["broker_orders_sent"],0)
            self.assertEqual(json.loads((r/"comparison.json").read_text())["state"],
                "TWO_READONLY_SOURCE_HEALTH_SNAPSHOTS_PRESERVED")
            with self.assertRaisesRegex(Hold,"COMPARISON_ALREADY_ATTEMPTED"):
                compare_once(r,captured_at=CAPTURE_MS)
    def test_missed_deadline_refuses_later_healthy_state(self):
        t,r,p=tmp_test()
        with t,patch("train_v2.fx_fixedslot_cache_canary.verify_plan",return_value=p):
            result=compare_once(r,captured_at=WINDOW_END_MS+1)
            self.assertEqual(result["state"],"MISSED_NO_REPLAY")
            self.assertFalse((r/"baseline-original-status.json").exists())
            self.assertFalse((r/"canary-original-status.json").exists())
    def test_cache_status_missing_is_not_rescued(self):
        t,r,p=tmp_test()
        with t,patch("train_v2.fx_fixedslot_cache_canary.verify_plan",return_value=p):
            Path(p["canary_health_file"]).unlink()
            result=compare_once(r,captured_at=CAPTURE_MS)
            self.assertFalse(result["canary"]["healthy_at_exact_time"])
            self.assertTrue(result["baseline"]["healthy_at_exact_time"])
    def test_future_status_is_not_causal(self):
        x=fake_status();x["at_ms"]=CAPTURE_MS+1
        self.assertIn("STATUS_SNAPSHOT_NOT_CAUSALLY_FRESH",
                      health_quality(x,CAPTURE_MS)[0])

if __name__=="__main__":unittest.main()
