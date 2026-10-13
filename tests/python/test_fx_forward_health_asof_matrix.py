"""Mutation and clock-bound tests for frozen FX source-monitor custody matrix."""
from __future__ import annotations

from copy import deepcopy
import unittest

from train_v2.fx_forward_health_asof_matrix import (
    AuditHold, classify, SCHEMA,
)
from train_v2.fx_original_readiness_custody import (
    RULE, SCHEMA as HEALTH_SCHEMA, canonical, digest,
)

ANCHOR=1791833700000
PERIOD="f"*64

def fixture():
    monitor_at=ANCHOR-38000
    statuses=["USABLE"]*24
    health={
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":PERIOD,
        "rule":deepcopy(RULE),
        "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
        "at_ms":monitor_at,
        "source_prefixes":[{"root_digest":"e"*64}],
        "counts":{"USABLE":24},
        "probes":[{"status":v} for v in statuses],
        "usable":24,
        "unusable":0,
        "latest_source_quote_ms":monitor_at-435,
        "latest_quote_age_ms":435,
        "automatic_trial_registration":False,
        "trading_authority":False,
        "reasons":[],
    }
    health["source_prefix_digest"]=digest(canonical(health["source_prefixes"]))
    capture={
        "schema":HEALTH_SCHEMA+".snapshot-attempt.v1",
        "window":1,
        "anchor_ms":ANCHOR,
        "attempt_started_at_ms":ANCHOR+1001,
        "state":"HOLD",
        "reasons":["READINESS_SNAPSHOT_STALE_OR_FUTURE"],
        "source_health_status_sha256":digest(canonical(health)),
        "source_health_status_bytes":len(canonical(health)),
        "readiness_health_at_ms":monitor_at,
        "recomputed_source_usable_probes":24,
        "original_study_entry_readiness_proven":False,
        "broker_orders_sent":0,
        "trade_authority":False,
    }
    decision={
        "schema":"heptatrader.fx-original-future-price-cost-calendar.v1.attempt.v1",
        "stage":"decision",
        "state":"CENSORED",
        "reason_code":"EXCEPTION_Hold_ORIGINAL_READINESS_HOLD_OR_STALE",
        "broker_orders_sent":0,
        "automatic_retry_allowed":False,
        "attempt_recorded_at_ms":ANCHOR+1001,
    }
    return health,capture,decision

def review(h,c,d):
    return classify(h,c,d,ANCHOR,PERIOD,digest(canonical(h)))

class MatrixTests(unittest.TestCase):
    def test_stale_monitor_despite_ready_and_fresh_quote_asof(self):
        h,c,d=fixture()
        out=review(h,c,d)
        self.assertEqual(out["source_monitor_age_at_original_slot_ms"],39001)
        self.assertEqual(out["monitor_usable_probe_count"],24)
        self.assertEqual(out["last_quote_age_at_monitor_asof_ms"],435)
        self.assertFalse(out["original_broker_quote_freshness_at_slot_attested"])
        self.assertTrue(out["no_retroactive_relabel"])
        self.assertEqual(out["cause_class"],
           "SOURCE_MONITOR_SNAPSHOT_TOO_OLD__NOT_PROOF_OF_LIVE_QUOTE_STALENESS")
    def test_stored_source_digest_mismatch(self):
        h,c,d=fixture()
        c["source_health_status_sha256"]="0"*64
        with self.assertRaisesRegex(AuditHold,"RAW_CUSTODY_BYTES_OR_SUMMARY_CHANGED"):
            review(h,c,d)
    def test_source_health_mutation_cannot_be_silent(self):
        h,c,d=fixture()
        h["state"]="HOLD_NEW_FUSION_WINDOW"
        with self.assertRaisesRegex(AuditHold,"RAW_CUSTODY_BYTES_OR_SUMMARY_CHANGED"):
            review(h,c,d)
    def test_missed_original_slot_cannot_be_relabelled(self):
        h,c,d=fixture()
        c["attempt_started_at_ms"]=ANCHOR+5000
        with self.assertRaisesRegex(AuditHold,"CUSTODY_ATTEMPT_NOT_IN_ORIGINAL_SLOT"):
            review(h,c,d)
    def test_custody_cannot_set_original_entry_proven_true(self):
        h,c,d=fixture()
        c["original_study_entry_readiness_proven"]=True
        with self.assertRaisesRegex(AuditHold,"CUSTODY_CANNOT_PROMOTE"):
            review(h,c,d)
    def test_custody_cannot_place_broker_order(self):
        h,c,d=fixture()
        c["broker_orders_sent"]=1
        with self.assertRaisesRegex(AuditHold,"CUSTODY_CANNOT_PROMOTE"):
            review(h,c,d)
    def test_original_rejection_cannot_be_changed_to_trade(self):
        h,c,d=fixture()
        d["state"]="FROZEN_ORIGINAL_PRICE_ONLY"
        with self.assertRaisesRegex(AuditHold,"ORIGINAL_ENTRY_REJECT_CHANGED"):
            review(h,c,d)
    def test_original_decision_cannot_be_retissued_later(self):
        h,c,d=fixture()
        d["attempt_recorded_at_ms"]=ANCHOR+9000
        with self.assertRaisesRegex(AuditHold,"ORIGINAL_ENTRY_REJECT_CHANGED"):
            review(h,c,d)
    def test_health_probe_inconsistency_not_hidden(self):
        h,c,d=fixture()
        h["counts"]={"USABLE":23,"PATH_GAP":1}
        c["source_health_status_sha256"]=digest(canonical(h))
        c["source_health_status_bytes"]=len(canonical(h))
        with self.assertRaisesRegex(AuditHold,"HEALTH_RAW_RECOMPUTATION_DISAGREES"):
            review(h,c,d)
    def test_health_forged_period_not_granted(self):
        h,c,d=fixture()
        h["period_sha256"]="e"*64
        c["source_health_status_sha256"]=digest(canonical(h))
        c["source_health_status_bytes"]=len(canonical(h))
        with self.assertRaisesRegex(AuditHold,"HEALTH_RAW_RECOMPUTATION_DISAGREES"):
            review(h,c,d)
    def test_quote_stale_at_monitor_asof_not_single_cause_claim(self):
        h,c,d=fixture()
        h["latest_quote_age_ms"]=5001
        h["latest_source_quote_ms"]=h["at_ms"]-5001
        c["source_health_status_sha256"]=digest(canonical(h))
        c["source_health_status_bytes"]=len(canonical(h))
        # The retained witness still reports ONLY snapshot age, so it is
        # refused instead of retroactively changing original censor causes.
        with self.assertRaisesRegex(AuditHold,"HEALTH_RAW_RECOMPUTATION_DISAGREES"):
            review(h,c,d)

if __name__=="__main__":unittest.main()
