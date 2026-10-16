"""Fixed cutoff, no hindsight, REAL source-compatible Action V2 CPU control."""
from __future__ import annotations
from copy import deepcopy
import math
import unittest

from laya_cell.integrity import ContractError, digest
from laya_cell.resident_action_v2_closed_source import SCHEMA as SOURCE_SCHEMA
from laya_cell.action_v2_frozen_baseline import (
    TRAIN_CUTOFF_MS, HOLD_END_MS, analyze, fit, partition, predict, score
)


def rows():
    out=[]
    for k in range(24):
        training=k<16
        day=(k//8)  # Oct7, Oct8, Oct9
        anchor=TRAIN_CUTOFF_MS - 2*86400000 + day*86400000 + k%8*600000
        positive=k%4==0
        confidence=.41 + .025*(k%5)
        pv=[(1-confidence)/6]*7
        pv[3]=confidence
        r={
            "row_id":f"{k+1:064x}", "plan_id":"A", "slot":k,
            "parent_id":"p"*64, "anchor_ms":anchor,
            "future_outcome_available_ms":anchor+301000,
            "parent_correct":positive,
            "target_class":3 if positive else 2,
            "frozen_parent_probs":pv}
        out.append(r)
    return out


def make_source(r):
    m={"schema":SOURCE_SCHEMA,"frozen_parent_id":"p"*64,
       "training_cutoff_ms":HOLD_END_MS+86400000,
       "training_rows":len(r),
       "model_or_trading_authority":False,
       "no_pending_trial_outcomes_included":True,
       "not_independent_future_evaluation":True,
       "rows_digest":digest(r)}
    m["manifest_id"]=digest(m)
    return {"manifest":m,"rows":r}


class ActionV2FixedBaselineTest(unittest.TestCase):
    def test_true_chronological_train_hold(self):
        a,b=partition(rows())
        self.assertEqual([len(a),sum(y for _,y in a)], [16,4])
        self.assertEqual([len(b),sum(y for _,y in b)], [8,2])

    def test_fit_reproducible_and_bounded(self):
        a,b=partition(rows())
        self.assertEqual(fit(a),fit(list(a)))
        m=fit(a)
        self.assertEqual(m["optimizer_steps"],300)
        for x,_ in a+b:
            self.assertTrue(0<predict(x,m)<1)
        for name in ("train_prior","frozen_parent_confidence","cpu_train_only_logistic"):
            o=score(b,name,m)
            self.assertTrue(math.isfinite(o["binary_brier"]))
            self.assertTrue(math.isfinite(o["binary_nll"]))
            self.assertFalse(o["ACT_suggestions_at_5_of_6"])

    def test_holdout_label_cannot_influence_fit(self):
        a,b=partition(rows())
        expected=fit(a)
        altered=deepcopy(rows())
        # Change only one genuine holdout label; fitting must remain identical.
        altered[-1]["parent_correct"]=True
        altered[-1]["target_class"]=3
        self.assertEqual(expected,fit(partition(altered)[0]))
        self.assertNotEqual(score(b,"train_prior",expected),
                            score(partition(altered)[1],"train_prior",expected))

    def test_data_validation_and_temporal_leakage(self):
        bad=deepcopy(rows())
        bad[1]["parent_correct"]=not bad[1]["parent_correct"]
        with self.assertRaises(ContractError):partition(bad)
        bad=deepcopy(rows())
        bad[0]["frozen_parent_probs"][0]=float("nan")
        with self.assertRaises(ContractError):partition(bad)
        bad=deepcopy(rows())
        bad[1]["row_id"]=bad[0]["row_id"]
        with self.assertRaises(ContractError):partition(bad)
        bad=deepcopy(rows())
        bad[1]["slot"]=bad[0]["slot"]
        with self.assertRaises(ContractError):partition(bad)
        bad=deepcopy(rows())
        bad[-1]["anchor_ms"]=HOLD_END_MS+1
        with self.assertRaises(ContractError):partition(bad)

    def test_future_outcome_must_be_available_by_train_cutoff(self):
        bad=deepcopy(rows())
        bad[1]["future_outcome_available_ms"]=TRAIN_CUTOFF_MS+5
        training,hold=partition(bad)
        self.assertEqual(len(training),15)
        self.assertEqual(len(hold),8)
        self.assertEqual(sum(y for _,y in training),4)
        # A subsequently unavailable ACT would leave only three positives.
        bad[0]["future_outcome_available_ms"]=TRAIN_CUTOFF_MS+5
        with self.assertRaises(ContractError):partition(bad)

    def test_historical_source_manifest_binding(self):
        original=make_source(rows())
        a=analyze(original,"c"*64)
        self.assertEqual(a["fixed_train_cutoff_ms"],TRAIN_CUTOFF_MS)
        self.assertEqual(a["train_counts"],{"n":16,"ACT":4})
        self.assertEqual(a["holdout_counts"],{"n":8,"ACT":2})
        self.assertTrue(a["not_full_action_v2_lora_training"])
        self.assertFalse(a["trading_authority"])
        modified=deepcopy(original)
        modified["rows"][0]["row_id"]="f"*64
        with self.assertRaises(ContractError):analyze(modified,"c"*64)

    def test_refuses_one_class_research_training_and_holdout(self):
        bad=deepcopy(rows())
        for r in bad[:16]:
            r["parent_correct"]=False
            r["target_class"]=2
        with self.assertRaises(ContractError):partition(bad)
        bad=deepcopy(rows())
        for r in bad[16:]:
            r["parent_correct"]=False
            r["target_class"]=2
        with self.assertRaises(ContractError):partition(bad)


if __name__=="__main__":
    unittest.main()
