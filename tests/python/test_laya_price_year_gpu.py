"""Causal 5m revised-bar GPU model feature and split contract (CPU only)."""
import copy
from datetime import datetime,timezone,timedelta
import math
import unittest
from laya_cell.price_year_gpu import (features_from_bars,fold_indices,class_prior,
    metrics,winner,FEATURE_NAMES,HORIZON_SECONDS,LOOKBACK_RETURNS,FAMILIES)
from laya_cell.integrity import ContractError


class HistoricalFeatureTests(unittest.TestCase):
    def bars(self,n=200):
        rows=[]
        for i in range(n):
            mid=1.12+.00001*i
            rows.append({"time":1700000000+300*i,
                         "bid":{"close":str(mid-.00001)},
                         "ask":{"close":str(mid+.00001)},
                         "midpoint":{"close":str(mid),"high":str(mid+.00003),
                                     "low":str(mid-.00003)}})
        return rows
    def test_five_minute_target_and_feature_count(self):
        bars=self.bars()
        data=features_from_bars(bars)
        self.assertEqual(len(data["features"]),len(bars)-LOOKBACK_RETURNS-1)
        self.assertEqual(len(data["features"][0]),len(FEATURE_NAMES))
        self.assertEqual(data["anchor_ms"][0],(bars[LOOKBACK_RETURNS]["time"]+300)*1000)
        self.assertEqual(data["label_horizon_ms"],300000)
        self.assertTrue(data["original_revised_data_not_contemporaneous"])
    def test_no_future_close_in_features(self):
        bars=self.bars()
        old=features_from_bars(bars)
        changed=copy.deepcopy(bars)
        for field in ("bid","ask","midpoint"):
            changed[LOOKBACK_RETURNS+1][field]["close"]=str(
                float(changed[LOOKBACK_RETURNS+1][field]["close"])+0.005)
        fresh=features_from_bars(changed)
        self.assertEqual(old["features"][0],fresh["features"][0])
        self.assertNotEqual(old["targets"][0],fresh["targets"][0])
    def test_close_gap_discards_cross_weekend_label(self):
        bars=self.bars()
        for b in bars[100:]:b["time"]+=172800
        out=features_from_bars(bars)
        self.assertGreater(out["omitted_due_to_gaps"],0)
        self.assertLess(len(out["features"]),len(bars)-LOOKBACK_RETURNS-1)
    def test_price_cross_or_nan_fail_closed(self):
        bars=self.bars();bars[0]["ask"]["close"]="0"
        with self.assertRaisesRegex(ContractError,"CROSSED"):features_from_bars(bars)
        bars=self.bars();bars[0]["midpoint"]["close"]="nan"
        with self.assertRaisesRegex(ContractError,"CROSSED"):features_from_bars(bars)
    def test_duplicate_bar_timestamp_fail_closed(self):
        bars=self.bars();bars[1]["time"]=bars[0]["time"]
        with self.assertRaisesRegex(ContractError,"ASCENDING"):features_from_bars(bars)
    def test_wrong_horizon_is_not_silently_900sec(self):
        self.assertEqual(HORIZON_SECONDS,300)


class FoldTests(unittest.TestCase):
    def fold(self):
        base=datetime.fromtimestamp(1700000000,tz=timezone.utc)
        def s(minutes):return (base+timedelta(minutes=minutes)).isoformat()
        return {"train_start_utc":s(0),"train_end_utc":s(180),
                "val_start_utc":s(195),"val_end_utc":s(255),
                "test_start_utc":s(270),"test_end_utc":s(420)}
    def test_train_label_before_validation_and_900sec_embargo(self):
        rows=HistoricalFeatureTests().bars(120)
        data=features_from_bars(rows)
        fold=self.fold()
        v=fold_indices(data,fold)
        self.assertGreater(len(v["train"]),0)
        self.assertGreater(len(v["validation"]),0)
        self.assertGreater(len(v["test"]),0)
        t=data["anchor_ms"]
        self.assertLess(max(t[i]+300000 for i in v["train"]),
                        min(t[i] for i in v["validation"]))
    def test_cross_boundary_label_excluded(self):
        rows=HistoricalFeatureTests().bars(120)
        data=features_from_bars(rows)
        f=self.fold()
        idx=fold_indices(data,f)
        end=datetime.fromisoformat(f["train_end_utc"]).timestamp()*1000
        self.assertTrue(all(data["anchor_ms"][i]+300000<=end for i in idx["train"]))
    def test_insufficient_embargo_refused(self):
        rows=HistoricalFeatureTests().bars(120)
        data=features_from_bars(rows)
        f=self.fold();f["val_start_utc"]=f["train_end_utc"]
        with self.assertRaisesRegex(ContractError,"EMBARGO"):fold_indices(data,f)
    def test_train_static_prior_smoothing(self):
        p=class_prior([3]*32)
        self.assertEqual(len(p),7)
        self.assertAlmostEqual(sum(p),1.)
        self.assertGreater(p[3],.8)
    def test_brier_nll_bounds(self):
        p=[[1/7]*7]*20
        r=metrics(p,[3]*20)
        self.assertAlmostEqual(r["brier"],6/7)
        self.assertAlmostEqual(r["nll"],math.log(7))
    def test_neural_requires_both_proper_scores_to_beat_prior(self):
        s={"prior":{"brier":.8,"nll":1.5},
           "linear":{"brier":.7,"nll":1.6},
           "tiny_mlp":{"brier":.9,"nll":1.4}}
        self.assertEqual(winner(s),"prior")
        s["linear"]["nll"]=1.4
        self.assertEqual(winner(s),"linear")
    def test_undocumented_model_family_rejected(self):
        with self.assertRaises(ContractError):winner({"neural":{"brier":0,"nll":0}})
    def test_prob_input_nan_or_malformed_rejected(self):
        for value in ([math.nan]*7,[2]+[0]*6,[0]*7,[True]+[0]*6):
            with self.assertRaises(ContractError):metrics([value],[3])

if __name__=="__main__":unittest.main()
