"""The live original-quote bridge is causal and agrees with historical math."""
import copy
import math
import unittest
from laya_cell.integrity import ContractError, digest
from laya_cell.price_year_gpu import features_from_bars
from laya_cell.price_original_live_features import (
    original_closed_bars, features_at_closed_bar,
    original_feature_snapshot, NEEDED_CLOSED_BARS)


def realish_ticks(bins=14, step_ms=10000, beginning=1_791_800_000_000):
    start=(beginning//300000)*300000
    q=[]
    for b in range(bins):
        for dt in range(1000,299000,step_ms):
            t=start+b*300000+dt
            mid=1.1200+0.00003*b+0.000005*math.sin(dt/30000)
            bid=mid-0.00001;ask=mid+0.00001
            q.append({"t":t,"known_at_ms":t+500,"mid":mid,"bid":bid,"ask":ask,"receipt":"r"+str(t)})
    return q,start


class ProspectiveOutcomeContractTests(unittest.TestCase):
    def test_next_bucket_close_is_stored_after_input_bucket(self):
        from research_laya_price_original_one import outcome_from_new_closed_bar
        q,start=realish_ticks()
        bars,_=original_closed_bars(q,start+14*300000)
        anchor=start+13*300000
        base=bars[-2]["midpoint"]["close"]
        r=outcome_from_new_closed_bar(bars,anchor,base)
        self.assertEqual(r["end_mid"],bars[-1]["midpoint"]["close"])
        self.assertEqual(len(r),3)
    def test_missing_or_repeated_future_bucket_rejected(self):
        from research_laya_price_original_one import outcome_from_new_closed_bar
        q,start=realish_ticks()
        bars,_=original_closed_bars(q,start+14*300000)
        anchor=start+13*300000
        with self.assertRaisesRegex(ContractError,"UNIQUELY_COMPLETE"):
            outcome_from_new_closed_bar(bars[:-1],anchor,bars[-2]["midpoint"]["close"])
        with self.assertRaisesRegex(ContractError,"UNIQUELY_COMPLETE"):
            outcome_from_new_closed_bar(bars+[bars[-1]],anchor,bars[-2]["midpoint"]["close"])


class OriginalQuoteBridgeTests(unittest.TestCase):
    def setUp(self):
        self.q,self.start=realish_ticks()
        self.asof=self.start+13*300000+15000
    def test_13_historical_features_agree_without_next_label(self):
        bars,receipts=original_closed_bars(self.q,self.asof)
        self.assertEqual(len(bars),13)
        # The 14th synthetic bar exists, but was not visible at the input as-of.
        # Append it ONLY to the historical label-producing comparator.
        later,_=original_closed_bars(self.q,self.start+14*300000)
        historical=features_from_bars(later)
        self.assertEqual(historical["features"][0],features_at_closed_bar(bars)["features"])
        self.assertEqual(features_at_closed_bar(bars)["anchor_ms"],self.start+13*300000)
        self.assertEqual(len(receipts),13)
    def test_archived_decimal_strings_match_frozen_year_feature_math(self):
        q,start=realish_ticks()
        bars,_=original_closed_bars(q,start+14*300000)
        for b in bars:
            for side in ("bid","ask"):
                b[side]["close"]=format(b[side]["close"],".12f")
            for key in ("close","high","low"):
                b["midpoint"][key]=format(b["midpoint"][key],".12f")
        expected=features_from_bars(bars)["features"][0]
        actual=features_at_closed_bar(bars[:13])["features"]
        self.assertEqual(actual,expected)
    def test_output_has_no_authority_or_future_outcome(self):
        r=original_feature_snapshot(self.q,[],self.asof)
        self.assertEqual(r["source_bar_count"],13)
        self.assertTrue(r["future_outcome_was_not_required"])
        self.assertTrue(r["original_event_ticks_not_revised_ib_bars"])
        self.assertFalse(r["trade_authority"])
        self.assertEqual(r["snapshot_id"],digest({k:v for k,v in r.items() if k!="snapshot_id"}))
    def test_unclosed_last_bar_is_never_used(self):
        bars,_=original_closed_bars(self.q,self.start+13*300000+60000)
        self.assertEqual(len(bars),13)
    def test_insufficient_history_holds(self):
        q=[r for r in self.q if r["t"]<self.start+12*300000]
        with self.assertRaisesRegex(ContractError,"INSUFFICIENT"): original_feature_snapshot(q,[],self.asof)
    def test_input_expired_never_fabricates_freshness(self):
        with self.assertRaisesRegex(ContractError,"INPUT_LATE"): original_feature_snapshot(
            self.q,[],self.start+13*300000+61000)
    def test_missing_bar_breaks_contiguous_feature_history(self):
        q=[r for r in self.q if not self.start+6*300000<=r["t"]<self.start+7*300000]
        bars,_=original_closed_bars(q,self.asof)
        with self.assertRaisesRegex(ContractError,"INSUFFICIENT_CLOSED_HISTORY"): features_at_closed_bar(bars)
    def test_stale_quote_gaps_are_not_interpolated(self):
        q=[r for r in self.q if not self.start+5*300000+50000<=r["t"]<
           self.start+5*300000+190000]
        bars,_=original_closed_bars(q,self.asof)
        self.assertEqual(len(bars),12)
    def test_missing_last_15_seconds_excludes_partial(self):
        q=[r for r in self.q if not self.start+12*300000+280000<=r["t"]<
           self.start+13*300000]
        bars,_=original_closed_bars(q,self.asof)
        self.assertEqual(len(bars),12)
    def test_missing_first_15_seconds_excludes_partial(self):
        q=[r for r in self.q if not self.start+300000<=r["t"]<
           self.start+300000+20000]
        bars,_=original_closed_bars(q,self.asof)
        self.assertEqual(len(bars),12)
    def test_future_known_at_not_used(self):
        q=copy.deepcopy(self.q)
        for r in q:
            if self.start+12*300000<=r["t"]<self.start+13*300000:
                r["known_at_ms"]=self.asof+1
        bars,_=original_closed_bars(q,self.asof)
        self.assertEqual(len(bars),12)
    def test_crossed_and_nonfinite_prices_rejected(self):
        for change in ({"bid":2.0}, {"mid":float("nan")}):
            q=copy.deepcopy(self.q)
            q[0].update(change)
            with self.assertRaisesRegex(ContractError,"CROSSED_OR_INVALID"):original_closed_bars(q,self.asof)
    def test_duplicate_original_timestamp_refused(self):
        with self.assertRaisesRegex(ContractError,"TIME_OR_ID"):original_closed_bars(
            self.q+[self.q[0]],self.asof)
    def test_non_integer_and_boolean_times_refused(self):
        for val in (True, 1.5, None):
            q=copy.deepcopy(self.q);q[0]["t"]=val
            with self.assertRaisesRegex(ContractError,"TIME_OR_ID"):original_closed_bars(q,self.asof)
    def test_reversed_arrival_order_sorted_by_event_time(self):
        direct=original_feature_snapshot(self.q,[],self.asof)
        reversed_quotes=original_feature_snapshot(list(reversed(self.q)),[],self.asof)
        self.assertEqual(direct["features"],reversed_quotes["features"])
    def test_adversarial_bar_gap_rejected_even_if_thirteen_exist(self):
        bars,_=original_closed_bars(self.q,self.asof)
        bars[1]["time"]+=600
        with self.assertRaisesRegex(ContractError,"NONCONTIGUOUS"):features_at_closed_bar(bars)
    def test_too_many_quotes_rejected(self):
        with self.assertRaisesRegex(ContractError,"CAPACITY"):original_closed_bars([self.q[0]]*100001,self.asof)


if __name__=="__main__":
    unittest.main()
