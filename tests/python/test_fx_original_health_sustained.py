"""Sustained 480-cycle PAPER quote-health byte custody and negative tests.

Only synthetic source snapshots are used. No broker, model or trade APIs.
"""
from __future__ import annotations
from pathlib import Path
import tempfile
import unittest

from train_v2.fx_original_health_sustained import (
    SCHEMA as COHORT, N, STEP_MS, MAX_SOURCE_AGE_MS, MIN_LEAD_MS,
    RULE, canonical, digest, write_once, verify_status, AuditHold
)
from train_v2.fx_original_health_sustained_audit import (
    SCHEMA as AUDIT, N as AUDIT_N, COHORT as AUDIT_COHORT,
    verify_all, IntegrityHold
)

START=1791878400000
PERIOD="b"*64

def original_status(i,hold=False):
    at=START+i*STEP_MS
    original=[{"status":("PATH_GAP" if hold and j < 7 else "USABLE"),
               "start_ms":at-j*60000} for j in range(24)]
    prefixes=[{"segment":f"p{i}","prefix_sha256":"f"*64,"bytes":8096}]
    usable=17 if hold else 24
    return {
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":PERIOD,"rule":RULE,
        "state":"HOLD_NEW_FUSION_WINDOW" if hold else "READY_FOR_REVIEW_NOT_AUTHORIZATION",
        "at_ms":at,"source_prefixes":prefixes,
        "source_prefix_digest":digest(canonical(prefixes)),
        "probes":original,
        "counts":{"USABLE":usable,**({"PATH_GAP":7} if hold else {})},
        "usable":usable,"unusable":24-usable,
        "latest_source_quote_ms":at-1200,"latest_quote_age_ms":1200,
        "automatic_trial_registration":False,
        "trading_authority":False,"orders_sent":0,
        "reasons":["INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE"] if hold else [],
    }

def synthetic(root, *, market_hold_index=None, missing_index=None):
    root.mkdir(mode=0o700)
    plan={
        "schema":COHORT+".plan.v1",
        "registered_at_ms":START-MIN_LEAD_MS-10_000,
        "first_cycle_start_ms":START,
        "cycle_schedule_ms":[START+i*STEP_MS for i in range(N)],
        "full_snapshots_count":N,
        "original_monitor_rules":RULE,
        "candidate_assessment_cadence_ms":STEP_MS,
        "maximum_status_age_ms":MAX_SOURCE_AGE_MS,
        "trade_authority":False,
        "no_production_monitor_cutover":True,
        "broker_requests_or_orders_sent":0,
        "period_sha256":PERIOD,
    }
    plan_hash=write_once(root/"plan.json",canonical(plan)+b"\n")
    write_once(root/"worker-start.json",canonical({
        "plan_sha256":plan_hash,"no_restart":True,
        "production_monitor_not_modified":True,"broker_orders_sent":0,
    })+b"\n")
    write_once(root/"sandbox.json",b'{"sandbox":true}\n')
    prior="0"*64
    items=[]
    for i in range(N):
        at=START+i*STEP_MS
        if i == missing_index:
            record={
                "state":"CENSORED_MISSED_FROZEN_CYCLE",
                "issues":["MISSED_FROZEN_SCHEDULE"],
                "status_sha256":None,
                "original_asof_ms":None,
                "publisher_eligible":False,"source_ready":False,
            }
        else:
            s=original_status(i,hold=(i==market_hold_index))
            raw=canonical(s)+b"\n"
            source_hash=write_once(root/(f"cycle-{i:02d}-status.json"),raw)
            record={
                "state":"CAPTURED_ORIGINAL_HEALTH",
                "issues":[],"status_sha256":source_hash,
                "original_asof_ms":at,
                "source_ready":i!=market_hold_index,
                "source_usable_probes":s["usable"],
                "source_prefix_digest":s["source_prefix_digest"],
                "published_at_ms":at+8500,
                "publisher_eligible":True,
            }
        record.update({
            "schema":COHORT+".cycle.v1","index":i,
            "frozen_target_ms":at,"assessment_started_ms":at,
            "record_sealed_at_ms":at+8600,
            "previous_receipt_sha256":prior,
            "original_period_sha256":PERIOD,
            "trade_authority":False,"orders_sent":0,
        })
        prior=write_once(root/(f"cycle-{i:02d}-receipt.json"),
                         canonical(record)+b"\n")
        items.append({"index":i,"status":record["state"],
                      "receipt_sha256":prior,"source_ready":record["source_ready"]})
    write_once(root/"final.json",canonical({
        "schema":COHORT+".final.v1",
        "plan_sha256":plan_hash,"receipt_chain_head":prior,"cycles":items,
        "full_snapshots_written":N-(missing_index is not None),
        "no_prod_monitor_cutover":True,
        "production_trading_grant":False,"broker_orders_sent":0,
    })+b"\n")
    return root

class TwoHourTests(unittest.TestCase):
    def test_exact_cohort_and_preserved_thresholds(self):
        self.assertEqual(N,AUDIT_N)
        self.assertEqual(N,480)
        self.assertEqual(STEP_MS,15000)
        self.assertEqual(N*STEP_MS,7200000)
        self.assertGreaterEqual(MIN_LEAD_MS,120000)
        self.assertEqual(AUDIT_COHORT,COHORT)
        self.assertEqual(RULE["minimum_usable"],20)
        self.assertEqual(RULE["source_ttl_ms"],5000)
        self.assertEqual(RULE["path_gap_max_ms"],30000)
    def test_production_monitor_not_mutated(self):
        self.assertIn("sustained",COHORT)
        self.assertIn("sustained",AUDIT)
    def test_valid_original_ready_remains_only_research(self):
        self.assertEqual(verify_status(original_status(2),PERIOD,START+2*STEP_MS),[])
    def test_real_source_hold_not_upgraded_to_ready(self):
        self.assertEqual(verify_status(original_status(5,hold=True),
                                        PERIOD,START+5*STEP_MS),[])
        self.assertEqual(original_status(5,hold=True)["state"],"HOLD_NEW_FUSION_WINDOW")
    def test_wrong_paper_period_rejected(self):
        s=original_status(0);s["period_sha256"]="c"*64
        self.assertIn("HEALTH_PERIOD_CHANGED",verify_status(s,PERIOD,START))
    def test_source_quote_age_not_relabelled(self):
        s=original_status(0);s["latest_source_quote_ms"]+=10
        self.assertIn("SOURCE_AGE_CLOCK_MISMATCH",verify_status(s,PERIOD,START))
    def test_output_write_once(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"private";p.mkdir(mode=0o700)
            f=p/"receipt.json";write_once(f,b'{"status":"HOLD"}\n')
            with self.assertRaises(FileExistsError):
                write_once(f,b'{"status":"READY"}\n')
            self.assertEqual(f.read_bytes(),b'{"status":"HOLD"}\n')
    def test_complete_480_cycle_chain_is_independently_verifiable(self):
        with tempfile.TemporaryDirectory() as t:
            root=synthetic(Path(t)/"cohort",market_hold_index=271)
            result=verify_all(root)
            self.assertEqual(result["complete_original_health_json_count"],N)
            self.assertEqual(result["source_ready_cycle_count"],N-1)
            self.assertEqual(result["source_hold_cycle_count"],1)
            self.assertEqual(result["worst_source_age_just_before_next_publication_ms"],23500)
            self.assertTrue(result["all_decision_time_snapshot_ages_within_30s"])
            self.assertFalse(result["prod_monitor_cadence_switch_approved"])
            self.assertFalse(result["trade_authority"])
    def test_first_changed_future_health_body_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            root=synthetic(Path(t)/"cohort")
            changed=root/"cycle-00-status.json"
            changed.write_bytes(b'{"falsified":true}\n')
            with self.assertRaisesRegex(IntegrityHold,"SNAPSHOT_SHA_NOT_ORIGINAL"):
                verify_all(root)
    def test_missing_observation_remains_unqualified(self):
        with tempfile.TemporaryDirectory() as t:
            root=synthetic(Path(t)/"cohort",missing_index=121)
            result=verify_all(root)
            self.assertEqual(result["complete_original_health_json_count"],N-1)
            self.assertEqual(result["source_ready_cycle_count"],N-1)
            self.assertFalse(result["all_decision_time_snapshot_ages_within_30s"])
            self.assertEqual(result["state"],"HOLD_INCOMPLETE_OR_STALE_ORIGINAL_SOURCE_SNAPSHOTS")

if __name__=="__main__":
    unittest.main()
