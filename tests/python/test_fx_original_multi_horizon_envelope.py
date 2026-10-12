"""Adversarial, original quote-only retrospective multi-horizon research tests."""
from __future__ import annotations
from decimal import Decimal
import hashlib
import unittest

from train_v2.fx_original_multi_horizon_envelope import (
    HORIZON_MINUTES, HORIZON_MS, EnvelopeHold, evaluate_horizon,
    full_broker_session, price_outcome, quantile,
    select_nonoverlap_anchors, common_anchor_comparison,
)

ANCHOR=1791366000000
def quote(t, bid="1.1000",ask="1.1002",n=None,late=False):
    return {"event_at_ms":t,"available_at_ms":t+(6000 if late else 600),
            "valid_until_ms":t+5000,"bid":bid,"ask":ask,
            "source_id":hashlib.sha256(str(t if n is None else n).encode()).hexdigest()}

def schedule(*, low=None, high=None, cut=None):
    lo=ANCHOR-3_600_000 if low is None else low
    hi=ANCHOR+6*3_600_000 if high is None else high
    close=hi if cut is None else cut
    return {"requested_low_ms":lo,"requested_high_ms":hi,
        "interval_starts":[lo],
        "session_intervals":[(lo,close)]}

def original_sample(anchor):
    rows=[]
    for i in range(13):
        t=anchor-(12-i)*300000-1000
        rows.append(quote(t,n=f"{anchor}-{i}"))
    return rows

class PlanTests(unittest.TestCase):
    def test_horizons_are_release_frozen(self):
        self.assertEqual(HORIZON_MINUTES,(15,30,60,120))
        self.assertEqual(HORIZON_MS,(900000,1800000,3600000,7200000))
    def test_nonoverlap_by_clock_not_future_label(self):
        anchors=[ANCHOR+i*300000 for i in range(60)]
        selected=select_nonoverlap_anchors(anchors,HORIZON_MS[0])
        self.assertEqual(selected[0],ANCHOR)
        self.assertEqual(selected[1],ANCHOR+80*60000)
        self.assertEqual(len(selected),4)
    def test_larger_horizon_enforces_more_purge(self):
        anchors=[ANCHOR+i*300000 for i in range(80)]
        shorter=select_nonoverlap_anchors(anchors,HORIZON_MS[0])
        longer=select_nonoverlap_anchors(anchors,HORIZON_MS[-1])
        self.assertGreater(len(shorter),len(longer))
    def test_duplicate_anchor_refused(self):
        with self.assertRaisesRegex(EnvelopeHold,"ELIGIBILITY_ANCHORS_NOT_ORDERED"):
            select_nonoverlap_anchors([ANCHOR,ANCHOR],900000)
    def test_future_quote_after_freeze_censored(self):
        rows=[quote(ANCHOR+900000)]
        result,reason=price_outcome(ANCHOR,rows,
            [x["event_at_ms"] for x in rows],900000,
            ANCHOR+900000+4000)
        self.assertIsNone(result)
        self.assertEqual(reason,"CENSORED_OUTCOME_NOT_PRESEAL")
    def test_late_first_future_quote_is_not_replaced(self):
        rows=[quote(ANCHOR+900000,late=True),quote(ANCHOR+9000200)]
        result,reason=price_outcome(ANCHOR,rows,
            [x["event_at_ms"] for x in rows],900000,
            ANCHOR+1200000)
        self.assertIsNone(result)
        self.assertIn("FIRST_FUTURE_QUOTE_LATE",reason)
    def test_missing_future_event_censored(self):
        rows=[quote(ANCHOR+9010000)]
        result,reason=price_outcome(ANCHOR,rows,
            [x["event_at_ms"] for x in rows],900000,
            ANCHOR+1200000)
        self.assertIsNone(result)
        self.assertIn("NO_ORIGINAL_FUTURE_MARKET_EVENT",reason)
    def test_eligible_earliest_future_selected(self):
        rows=[quote(ANCHOR+900000),quote(ANCHOR+9000500)]
        result,reason=price_outcome(ANCHOR,rows,
            [x["event_at_ms"] for x in rows],900000,
            ANCHOR+1200000)
        self.assertEqual(reason,"MATURE_PRICE_ONLY")
        self.assertEqual(result["event_at_ms"],ANCHOR+900000)
    def test_full_session_entire_window_needed(self):
        s=schedule(cut=ANCHOR+1790000)
        self.assertEqual(full_broker_session(ANCHOR,ANCHOR+900000,s),
                         "IB_HISTORICAL_SESSION_FULL_HORIZON_OPEN")
        self.assertEqual(full_broker_session(ANCHOR,ANCHOR+1800000,s),
                         "IB_HISTORICAL_SESSION_BREAK_OR_CLOSED")
    def test_unknown_outside_request_not_closed(self):
        s=schedule(high=ANCHOR+920000)
        self.assertEqual(full_broker_session(ANCHOR,ANCHOR+900000,s),
                         "IB_HISTORICAL_SESSION_FULL_HORIZON_OPEN")
        self.assertEqual(full_broker_session(ANCHOR,ANCHOR+1800000,s),
                         "UNKNOWN_OUTSIDE_CACHED_BROKER_SESSION_SCOPE")
    def test_quantiles_are_numeric(self):
        self.assertEqual(quantile([Decimal("0"),Decimal("2")],"0.5"),"1.0")

class RetrospectiveTests(unittest.TestCase):
    def test_label_blind_denominator_preserves_censorship(self):
        a=ANCHOR
        preaction=[(a,original_sample(a)),
                   (a+5*300000,original_sample(a+5*300000))]
        # First maturity present, second future maturity missing.
        rows=[quote(a+900000,bid="1.1012",ask="1.1014")]
        out=evaluate_horizon(rows,preaction,schedule(),900000,
                             a+3*3600000,Decimal("8"),Decimal("10"))
        self.assertEqual(out["preaction_denominator"],2)
        self.assertEqual(out["mature_price_label_count"],1)
        self.assertEqual(out["nonoverlap_selected_before_outcome"],1)
        self.assertEqual(out["nonoverlap_mature_label_count"],1)
        self.assertEqual(out["maturity_state_counts"]["MATURE_PRICE_ONLY"],1)
        self.assertTrue(out["nonoverlap_anchor_choice_does_not_read_future_price"])
    def test_future_label_availability_does_not_change_anchor_choice(self):
        a=ANCHOR
        preaction=[(a,original_sample(a)),
                   (a+2*300000,original_sample(a+2*300000)),
                   (a+80*60000,original_sample(a+80*60000))]
        s=schedule()
        source=[quote(a+900000),quote(a+80*60000+900000)]
        s["requested_high_ms"]=a+8*3600000
        s["session_intervals"][0]=(s["interval_starts"][0],a+8*3600000)
        out=evaluate_horizon(source,preaction,s,900000,
                             a+9*3600000,Decimal("8"),Decimal("10"))
        self.assertEqual(out["nonoverlap_selected_before_outcome"],2)
        self.assertEqual(out["nonoverlap_mature_label_count"],2)
        self.assertEqual(out["mature_price_label_count"],2)
    def test_higher_cost_never_creates_more_passes(self):
        a=ANCHOR
        preaction=[(a,original_sample(a))]
        source=[quote(a+900000,bid="1.1005",ask="1.1007")]
        result=evaluate_horizon(source,preaction,schedule(),900000,
                                a+2*3600000,Decimal("1"),Decimal("10"))
        self.assertGreaterEqual(
            result["overall_retro_price_only"]["price_magnitude_covers_assumed_cost"],
            result["overall_retro_price_only"]["price_magnitude_covers_frozen_margin"])
    def test_closure_or_unknown_session_counts_not_blended(self):
        a=ANCHOR
        preaction=[(a,original_sample(a))]
        source=[quote(a+900000)]
        s=schedule(cut=a+100000)
        out=evaluate_horizon(source,preaction,s,900000,
                             a+2*3600000,Decimal("8"),Decimal("10"))
        self.assertEqual(out["ib_full_horizon_open_only_retro_price"]["count"],0)
        self.assertEqual(out["ib_schedule_class_counts"][
            "IB_HISTORICAL_SESSION_BREAK_OR_CLOSED"]["mature_price_labels"],1)

    def test_common_horizon_compares_same_entry_and_time(self):
        a=ANCHOR
        input_rows=[(a,original_sample(a))]
        futures=[quote(a+h,bid="1.1005",ask="1.1007") for h in HORIZON_MS]
        report=common_anchor_comparison(
            futures,input_rows,schedule(),a+3*3600000,
            Decimal("1"),Decimal("10"))
        self.assertEqual(report["all_four_mature_at_same_anchor"]["common_label_count"],1)
        self.assertEqual(report["common_longest_horizon_label_blind_nonoverlap_preselected"],1)
        self.assertEqual(report["common_longest_horizon_nonoverlap_all_four_mature"]["common_label_count"],1)
        self.assertEqual(report["common_all_four_mature_inside_same_cached_ib_session"]["common_label_count"],1)
        self.assertTrue(report["conditioning_on_four_mature_prices_has_future_source_selection_bias"])

    def test_missing_single_horizon_censors_common_cohort(self):
        a=ANCHOR
        input_rows=[(a,original_sample(a))]
        futures=[quote(a+h,bid="1.1005",ask="1.1007") for h in HORIZON_MS[:-1]]
        report=common_anchor_comparison(
            futures,input_rows,schedule(),a+3*3600000,
            Decimal("1"),Decimal("10"))
        self.assertEqual(report["all_four_mature_at_same_anchor"]["common_label_count"],0)
        self.assertEqual(report["common_longest_horizon_label_blind_nonoverlap_preselected"],1)
        self.assertEqual(report["common_longest_horizon_nonoverlap_all_four_mature"]["common_label_count"],0)
        self.assertFalse(report["different_horizons_were_not_ranked_or_selected_for_trading"] is False)

if __name__=="__main__":
    unittest.main()
