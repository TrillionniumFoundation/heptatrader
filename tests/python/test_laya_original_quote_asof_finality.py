"""Adversarial original PAPER quote snapshot and late-arrival finality tests."""
from bisect import bisect_right
from copy import deepcopy
import unittest

from laya_cell.dataset import BIN_EDGES_BPS
from laya_cell.integrity import ContractError,digest
from laya_cell.price_original_live_features import original_feature_snapshot,original_closed_bars
from laya_cell.original_quote_asof_finality import (
    verify_original_input,verify_original_outcome,bars_known_asof)

ANCHOR=1800000300000
DUE=ANCHOR+300000
BASE=1.122515
SAVED=1.122285
LATER=1.122290


def q(t,mid,known_at=None,symbol='a'):
    return {"t":t,"known_at_ms":t+200 if known_at is None else known_at,
            "mid":mid,"bid":mid-.00005,"ask":mid+.00005,
            "receipt":symbol*64}


def outcome_quotes(late=True):
    all_quotes=[]
    for i,t in enumerate([ANCHOR+j*27000 for j in range(11)]+[DUE-2000]):
        all_quotes.append(q(t,SAVED if i==11 else BASE-.00001,
                            symbol=chr(97+i)))
    if late:
        all_quotes.append(q(DUE-233,LATER,known_at=DUE+353,symbol='z'))
    return all_quotes


def original_saved_report():
    bps=(SAVED/BASE-1)*10000
    return {"status":"OBSERVED","slot":0,"at_ms":DUE+816,
            "outcome_quote_receipt":'l'*64,
            "actual_class":bisect_right(BIN_EDGES_BPS,bps),
            "return_bps":bps}


class CausalReceiptFinalityTests(unittest.TestCase):
    def setUp(self):
        self.pred={"anchor_ms":ANCHOR,"base_mid":BASE}
        self.outcome=original_saved_report()
        self.quotes=outcome_quotes()

    def test_first_saved_receipt_is_causally_feasible_but_nonfinal(self):
        result=verify_original_outcome(self.pred,self.outcome,self.quotes,DUE+61000)
        self.assertEqual(result["status"],"RECEIPT_MATCH_CAUSAL_ASOF_FEASIBLE")
        self.assertEqual(result["read_clock_lower_bound_ms"],DUE)
        self.assertEqual(result["read_clock_upper_bound_ms"],DUE+352)
        self.assertTrue(result["actual_source_read_clock_was_not_logged"])
        self.assertFalse(result["matured_60s_finality"]["class_changed"])
        self.assertTrue(result["matured_60s_finality"]["quote_receipt_changed"])
        self.assertAlmostEqual(result["matured_60s_finality"]["return_shift_bps"],
                               (LATER-SAVED)/BASE*10000)

    def test_newer_event_quote_known_before_horizon_makes_old_close_infeasible(self):
        src=deepcopy(self.quotes)
        src[-1]["known_at_ms"]=DUE-10
        result=verify_original_outcome(self.pred,self.outcome,src,DUE+61000)
        self.assertEqual(result["status"],"INCONSISTENT_CAUSAL_SOURCE_RECEIPT")
        self.assertFalse(result["feasible_time_window"])

    def test_unrecorded_receipt_cannot_be_treated_as_valid(self):
        fake={**self.outcome,"outcome_quote_receipt":'0'*64}
        self.assertEqual(verify_original_outcome(self.pred,fake,self.quotes,DUE+61000)["status"],
                         "RECEIPT_NOT_IN_CURRENT_VERIFIED_SOURCE")

    def test_tampered_return_or_class_fails_causal_source_match(self):
        for change in ({"return_bps":self.outcome["return_bps"]+.01},
                       {"actual_class":(self.outcome["actual_class"]+1)%7}):
            fake={**self.outcome,**change}
            self.assertEqual(verify_original_outcome(self.pred,fake,self.quotes,DUE+61000)["status"],
                             "INCONSISTENT_CAUSAL_SOURCE_RECEIPT")

    def test_60_second_finality_is_not_available_before_grace(self):
        r=verify_original_outcome(self.pred,self.outcome,self.quotes,DUE+3000)
        self.assertEqual(r["matured_60s_finality"]["status"],"NOT_YET_MATURE")
        self.assertIsNone(r["matured_60s_finality"]["class_changed"])

    def test_when_late_quote_really_crosses_bin_edge_report_change(self):
        quotes=deepcopy(self.quotes)
        threshold=BIN_EDGES_BPS[3]+3
        x=BASE*(1+threshold/10000)
        quotes[-1]["mid"]=x
        quotes[-1]["bid"]=x-.00005
        quotes[-1]["ask"]=x+.00005
        r=verify_original_outcome(self.pred,self.outcome,quotes,DUE+61000)
        self.assertEqual(r["status"],"RECEIPT_MATCH_CAUSAL_ASOF_FEASIBLE")
        self.assertTrue(r["matured_60s_finality"]["class_changed"])

    def test_missing_last_event_receipt_bounded_failure(self):
        quotes=self.quotes[:3]
        r=verify_original_outcome(self.pred,self.outcome,quotes,DUE+61000)
        self.assertEqual(r["status"],"RECEIPT_NOT_IN_CURRENT_VERIFIED_SOURCE")

    def test_impossible_result_commit_clock_fails(self):
        for when in (DUE-1,DUE+60001):
            with self.assertRaises(ContractError):
                verify_original_outcome(self.pred,{**self.outcome,"at_ms":when},
                                        self.quotes,DUE+70000)

    def test_asof_bars_only_contain_known_events(self):
        at=bars_known_asof(self.quotes,DUE)
        self.assertEqual(at[ANCHOR][1]["last_quote_receipt"],'l'*64)
        later=bars_known_asof(self.quotes,DUE+60000)
        self.assertEqual(later[ANCHOR][1]["last_quote_receipt"],'z'*64)


class FrozenInputReproductionTests(unittest.TestCase):
    def setUp(self):
        start=ANCHOR-13*300000
        quotes=[]
        for bar in range(13):
            left=start+bar*300000
            mid=BASE+bar*.000005
            for j,t in enumerate([left+27*k*1000 for k in range(11)]+[left+298000]):
                quotes.append(q(t,mid+(j-5)*.000001,symbol=hex((bar*12+j)%16)[2:]))
        self.quotes=quotes
        self.asof=ANCHOR
        snap=original_feature_snapshot(quotes,[{"pin":"a"}],ANCHOR,require_fresh=True)
        bars,_=original_closed_bars(quotes,ANCHOR)
        self.pred={"anchor_ms":ANCHOR,"source_asof_ms":ANCHOR,
                   "feature_vector_digest":digest(snap["features"]),
                   "base_mid":bars[-1]["midpoint"]["close"]}

    def test_original_feature_math_can_be_replayed_from_asof_known_ticks(self):
        info=verify_original_input(self.pred,self.quotes,[{"pin":"different-now"}])
        self.assertTrue(info["historical_features_match"])
        self.assertTrue(info["feature_math_not_archive_prefix_proof"])

    def test_feature_hash_edit_is_rejected(self):
        changed={**self.pred,"feature_vector_digest":'f'*64}
        with self.assertRaisesRegex(ContractError,"ORIGINAL_INPUT_CHANGED"):
            verify_original_input(changed,self.quotes,[])

    def test_asof_feature_requires_strict_contiguous_bars(self):
        missing=[q for q in self.quotes if q["t"]<(ANCHOR-9*300000)]
        with self.assertRaises(ContractError):
            verify_original_input(self.pred,missing,[])


if __name__=="__main__":unittest.main()
