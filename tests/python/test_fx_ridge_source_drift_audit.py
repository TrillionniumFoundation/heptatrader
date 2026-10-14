"""Adversarial tests for a read-only, non-retroactive FX source/drift audit."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from train_v2.fx_ridge_source_drift_audit import (
    MAX_DECISION_LATENCY_MS, BAR_MS, q, require, eligible_bucket_closes,
    source_feature_matrix, matched_timestamp_feature_comparison,
    retrospective_source_failure, unique_rows, source_segment_paths
)

ANCHOR=1791799800000
GRID=1800000000000

def quote(event, available=None, expiry=None, price=1.10, source_id="a"*64):
    return {
        "event_at_ms":event,
        "available_at_ms":event+100 if available is None else available,
        "valid_until_ms":event+5000 if expiry is None else expiry,
        "bid":str(price),
        "ask":str(price+0.00001),
        "source_id":source_id,
    }

class BoundTests(unittest.TestCase):
    def test_exact_freshness_boundary_strict(self):
        self.assertEqual(MAX_DECISION_LATENCY_MS,4500)

    def test_eligible_missing_distinguishes_sparse_market_and_late_arrival(self):
        first=GRID+BAR_MS
        second=GRID+2*BAR_MS
        third=GRID+3*BAR_MS
        rows=[
            quote(first-1000,available=first-900,source_id="1"*64),
            quote(second-1000,available=second+300,source_id="2"*64),
            quote(third-15000,available=third-14900,source_id="3"*64)
        ]
        closes,counts=eligible_bucket_closes(rows,third)
        self.assertEqual(counts["grid_endpoints_considered"],3)
        self.assertEqual(counts["missing_original_close_endpoints"],2)
        self.assertEqual(counts["missing_due_to_no_recent_market_event"],1)
        self.assertEqual(counts["missing_due_to_after_boundary_arrival"],1)
        self.assertEqual(counts["first_allowed_decision_eligible"],1)
        self.assertEqual(list(closes),[first])

    def test_fresh_market_event_too_old_for_fixed_grid(self):
        end=GRID+BAR_MS
        v=quote(end-4000,available=end-3900,expiry=end+1100)
        _,counts=eligible_bucket_closes([v],end)
        self.assertEqual(counts["first_allowed_decision_too_old"],1)
        self.assertEqual(counts["first_allowed_decision_eligible"],0)

    def test_quote_expires_before_fixed_decision(self):
        end=GRID+BAR_MS
        v=quote(end-3500,available=end-3300,expiry=end+900)
        _,counts=eligible_bucket_closes([v],end)
        self.assertEqual(counts["first_allowed_decision_quote_expired"],1)
        self.assertEqual(counts["first_allowed_decision_too_old"],0)

    def test_13_real_consecutive_closes_produce_one_feature(self):
        endings=[GRID+(i+1)*BAR_MS for i in range(13)]
        closes={end:quote(end-900,available=end-700,
                          price=1.10+i*0.0001,source_id=f"{i:064x}")
                for i,end in enumerate(endings)}
        x,anchors,stats=source_feature_matrix(closes,endings[-1])
        self.assertEqual(x.shape,(1,8))
        self.assertEqual(anchors,[endings[-1]])
        self.assertEqual(stats["reference_windows"],1)
        self.assertTrue(np.isfinite(x).all())

    def test_no_pseudoclose_gap_fill(self):
        endings=[GRID+(i+1)*BAR_MS for i in range(13)]
        closes={end:quote(end-900,source_id=f"{i:064x}")
                for i,end in enumerate(endings)}
        del closes[endings[5]]
        x,anchors,stats=source_feature_matrix(closes,endings[-1])
        self.assertEqual(len(x),0)
        self.assertEqual(anchors,[])
        self.assertEqual(stats["reference_windows"],0)

    def test_duplicate_quote_timestamp_price_conflict_refused(self):
        a=quote(GRID,price=1.1,source_id="a"*64)
        b=quote(GRID,price=1.2,source_id="b"*64)
        paths=[(1,1,Path("/tmp/fake1")),(2,2,Path("/tmp/fake2"))]
        proof={"original_period_verified":True,"verified_prefix_sha256":"f"*64,
               "original_complete_bytes":20}
        with patch("train_v2.fx_ridge_source_drift_audit.load_segment",
                   side_effect=[([a],proof),([b],proof)]):
            with self.assertRaisesRegex(ValueError,"CROSS_SEGMENT_SAME_EVENT_CONFLICT"):
                unique_rows(paths,{},GRID+10000)

    def test_duplicate_same_price_deduplicates_without_retraining(self):
        a=quote(GRID,price=1.1,source_id="a"*64)
        b=quote(GRID,price=1.1,source_id="b"*64)
        paths=[(1,1,Path("/tmp/fake1")),(2,2,Path("/tmp/fake2"))]
        proof={"original_period_verified":True,"verified_prefix_sha256":"f"*64,
               "original_complete_bytes":20}
        with patch("train_v2.fx_ridge_source_drift_audit.load_segment",
                   side_effect=[([a],proof),([b],proof)]):
            rows,receipts,dup=unique_rows(paths,{},GRID+10000)
        self.assertEqual(len(rows),1)
        self.assertEqual(len(receipts),2)
        self.assertEqual(dup,1)

class MatchedTests(unittest.TestCase):
    def fixture(self, matched=35):
        hist_at=np.arange(40,dtype=np.int64)*300+1000000000
        hist_x=np.zeros((40,8),dtype=float)
        hist_x[:,:6]=np.arange(40,dtype=float)[:,None]*.1
        # Temporal cyclical features must agree across an exact timestamp.
        hist_x[:,6]=.5
        hist_x[:,7]=-.5
        live_x=hist_x[:matched].copy()
        live_x[:,:6]+=.1
        anchors=(hist_at[:matched]*1000).tolist()
        model=dict(mean=[0]*8,stddev=[1]*8,weights=[.01]*9)
        return hist_x,hist_at,live_x,anchors,model

    def test_shared_utc_grid_reports_matched_feature_not_price_alpha(self):
        h,t,x,a,m=self.fixture()
        r=matched_timestamp_feature_comparison(h,t,x,a,m)
        self.assertEqual(r["shared_anchor_count"],35)
        self.assertEqual(r["state"],"MATCHED_RETROSPECTIVE_REPRESENTATION_DIFFERENCE_MEASURED")
        self.assertTrue(r["matched_hours_equal"])
        self.assertFalse(r["new_trade_or_model_efficacy_claim"])
        self.assertEqual(len(r["per_numeric_feature_absolute_pair_diff_bps_p50_p95_p99"]),6)

    def test_low_overlap_stays_hold(self):
        h,t,x,a,m=self.fixture(20)
        r=matched_timestamp_feature_comparison(h,t,x,a,m)
        self.assertEqual(r["shared_anchor_count"],20)
        self.assertEqual(r["state"],"HOLD_INSUFFICIENT_SAME_TIME_QUOTE_FEATURES")

    def test_same_timestamp_clock_features_cannot_shift(self):
        h,t,x,a,m=self.fixture()
        x[0,6]=0
        with self.assertRaisesRegex(ValueError,"SAME_GRID_CLOCK_FEATURE_INCONSISTENT"):
            matched_timestamp_feature_comparison(h,t,x,a,m)

    def test_repeated_historical_anchor_refused(self):
        h,t,x,a,m=self.fixture()
        t[1]=t[0]
        with self.assertRaisesRegex(ValueError,"HISTORICAL_FEATURE_ANCHOR_DUPLICATE"):
            matched_timestamp_feature_comparison(h,t,x,a,m)

    def test_no_future_label_in_feature_comparison_contract(self):
        h,t,x,a,m=self.fixture()
        r=matched_timestamp_feature_comparison(h,t,x,a,m)
        self.assertIs(r["common_date_price_outcomes_used"],False)
        self.assertIs(r["future_cohort_outcomes_reused"],False)

class RetrospectiveTests(unittest.TestCase):
    def fixture(self):
        d=tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root=Path(d.name)
        cell=root/"window-00"
        cell.mkdir()
        attempt={"reason_code":"EXCEPTION_ResearchHold_LAST_ORIGINAL_QUOTE_TOO_OLD",
                 "nonretryable":True,
                 "attempt_recorded_at_ms":ANCHOR+1164}
        (cell/"decision-attempt.json").write_text(json.dumps(attempt))
        (cell/"decision-attempt.json").chmod(0o600)
        campaign={"root":root,
                  "sealed":{"windows":[{"anchor_ms":ANCHOR,
                      "first_decision_ms":ANCHOR+1000,
                      "last_decision_ms":ANCHOR+4500}]},
                  "final":{"window_results":[{"decision_state":"CENSORED"}]}}
        orig=quote(ANCHOR-3744,available=ANCHOR-3609,
                   expiry=ANCHOR+1256,source_id="1"*64)
        future=quote(ANCHOR+1335,available=ANCHOR+1391,
                     expiry=ANCHOR+6335,source_id="2"*64)
        paths=[(ANCHOR*1000000-i*10000000000,0,Path("/tmp/mock"+str(i))) for i in (3,2,1)]
        proof=[{"sha256":"0"*64}]
        return campaign,paths,[orig,future],proof

    def test_original_1810_stale_even_though_venue_expiry_remaining(self):
        campaign,paths,quotes,proof=self.fixture()
        with patch("train_v2.fx_ridge_source_drift_audit.unique_rows",
                   return_value=(quotes,proof,0)):
            result=retrospective_source_failure(campaign,paths,{})
        self.assertEqual(result["first_allowed_decision_quote_age_ms"],4609)
        self.assertEqual(result["gap_between_distinct_quote_events_ms"],5079)
        self.assertEqual(result["first_allowed_decision_remaining_validity_ms"],256)
        self.assertFalse(result["can_prove_source_was_unmodified_at_original_decision"])
        self.assertTrue(result["frozen_censored_state_retained"])

    def test_nonstale_preanchor_not_consistent_with_frozen_failure(self):
        campaign,paths,quotes,proof=self.fixture()
        quotes[0]["available_at_ms"]=ANCHOR-3000
        with patch("train_v2.fx_ridge_source_drift_audit.unique_rows",
                   return_value=(quotes,proof,0)):
            with self.assertRaisesRegex(ValueError,"FROZEN_OLD_QUOTE_NOT_DIAGNOSTICALLY_EXPLAINED"):
                retrospective_source_failure(campaign,paths,{})

    def test_postanchor_new_quote_cannot_be_preanchor_input(self):
        campaign,paths,quotes,proof=self.fixture()
        quotes[1]["event_at_ms"]=ANCHOR-1000
        with patch("train_v2.fx_ridge_source_drift_audit.unique_rows",
                   return_value=(quotes,proof,0)):
            with self.assertRaisesRegex(ValueError,"SOURCE_STRADDLE_ANCHOR_CHANGED|NO_RETAINED_POSTANCHOR_QUOTE"):
                retrospective_source_failure(campaign,paths,{})

class CalendarAndHistoricalPresenceTests(unittest.TestCase):
    def test_utc_saturday_sunday_not_mislabeled_as_broker_session(self):
        from datetime import datetime, timezone
        from train_v2.fx_ridge_source_drift_audit import eligible_bucket_closes
        anchor=int(datetime(2026,10,10,12,5,tzinfo=timezone.utc).timestamp()*1000)
        rows=[quote(anchor-1000,available=anchor-800,expiry=anchor+4000)]
        _,stats=eligible_bucket_closes(rows,anchor)
        self.assertEqual(stats["sat_sun_utc_boundaries"],1)
        self.assertEqual(stats["mon_fri_utc_boundaries"],0)
        self.assertEqual(stats["first_allowed_decision_eligible"],1)

    def test_revised_historical_bar_presence_not_forward_price_permission(self):
        from train_v2.fx_ridge_source_drift_audit import historical_bar_present_overlap
        start=GRID+BAR_MS
        original=[quote(start-10000),quote(start+2*BAR_MS+2000)]
        ends=[start,start+BAR_MS,start+2*BAR_MS]
        bars=[{"time":end//1000-300} for end in ends]
        closes={start:original[0],start+2*BAR_MS:original[1]}
        r=historical_bar_present_overlap(bars,original,closes,GRID+4*BAR_MS)
        self.assertEqual(r["historical_revised_five_minute_bar_endpoints"],3)
        self.assertEqual(r["missing_causally_available_original_quote"],1)
        self.assertFalse(r["no_trade_or_model_efficacy_claim"] is False)
        self.assertTrue(r["historical_bar_availability_is_not_current_broker_session_schedule"])

    def test_revised_historical_bar_cannot_invent_original_quote(self):
        from train_v2.fx_ridge_source_drift_audit import historical_bar_present_overlap
        start=GRID+BAR_MS
        original=[quote(start-10000),quote(start+BAR_MS+2000)]
        ends=[start,start+BAR_MS]
        bars=[{"time":end//1000-300} for end in ends]
        r=historical_bar_present_overlap(bars,original,{},GRID+2*BAR_MS)
        self.assertEqual(r["with_causally_available_original_quote"],0)
        self.assertEqual(r["missing_causally_available_original_quote"],2)
        self.assertTrue(r["does_not_authorize_backfilling_missing_original_quotes"])


if __name__ == "__main__":
    unittest.main()
