"""CPU-only adverse contracts for genuine Action V2 experiment metadata."""
from __future__ import annotations
import copy
import math
import unittest
from laya_cell.integrity import ContractError,digest
from laya_cell.resident_action_v2_closed_source import SCHEMA as ORIGINAL_SOURCE_SCHEMA
from laya_cell.action_v2_real_fit import (
    SOURCE_SHA, PARENT_ID, TRAIN_END, HOLD_END,
    split_artifact, binary_scores, parent_probability_drift, MAX_PROB_DIFF,
)


def source_fixture():
    rows=[]
    for i in range(298):
        day=0 if i<62 else (1 if i<178 else 2)
        ordinal=(i if day==0 else i-62 if day==1 else i-178)
        anchor=TRAIN_END+(day-2)*86400000 + ordinal*1000
        # Historical-only fixture deliberately runs synthetic seconds:
        # only cutoff and label availability contracts matter.
        pos=(i in (1,3,5,64,66,68,70,72,74,180,182,184,186,188))
        rows.append({"row_id":f"{i+1:064x}",
                     "plan_id":"plan-alpha","slot":i,
                     "parent_id":PARENT_ID,"parent_correct":pos,
                     "anchor_ms":anchor,
                     "future_outcome_available_ms":anchor+300000,
                     "target_class":3 if pos else 2,
                     "frozen_parent_probs":[0.,0.,.2,.8,0.,0.,0.]})
    # Correct training truth counts 3+6=9, holdout 5
    m={"schema":ORIGINAL_SOURCE_SCHEMA,
       "frozen_parent_id":PARENT_ID,"training_cutoff_ms":HOLD_END+86400000,
       "training_rows":len(rows),"frozen_parent_correct_ACT":14,
       "frozen_parent_wrong_ESCALATE":284,
       "no_pending_trial_outcomes_included":True,
       "not_independent_future_evaluation":True,
       "model_or_trading_authority":False,
       "rows_digest":digest(rows)}
    m["manifest_id"]=digest(m)
    return {"manifest":m,"rows":rows}


def resign(x):
    x["manifest"]["rows_digest"]=digest(x["rows"])
    x["manifest"]["manifest_id"]=digest({
        k:v for k,v in x["manifest"].items() if k!="manifest_id"})


class FitContractTests(unittest.TestCase):
    def test_true_fixed_cohort(self):
        a,b=split_artifact(source_fixture())[1:]
        self.assertEqual((len(a),sum(r["parent_correct"] for r in a)),(178,9))
        self.assertEqual((len(b),sum(r["parent_correct"] for r in b)),(120,5))
        self.assertLess(max(r["future_outcome_available_ms"] for r in a),TRAIN_END)

    def test_does_not_admit_later_train_label(self):
        src=source_fixture()
        src["rows"][0]["future_outcome_available_ms"]=TRAIN_END+1
        resign(src)
        with self.assertRaises(ContractError):split_artifact(src)

    def test_no_forged_parent_or_unsealed_row(self):
        src=source_fixture();src["rows"][0]["parent_id"]="f"*64;resign(src)
        with self.assertRaises(ContractError):split_artifact(src)
        src=source_fixture();src["rows"][0]["row_id"]=src["rows"][1]["row_id"];resign(src)
        with self.assertRaises(ContractError):split_artifact(src)

    def test_change_original_count_fails(self):
        src=source_fixture();src["rows"].pop();src["manifest"]["training_rows"]-=1;resign(src)
        with self.assertRaises(ContractError):split_artifact(src)

    def test_late_foreign_calendar_fails(self):
        src=source_fixture();src["rows"][-1]["anchor_ms"]=HOLD_END+1;resign(src)
        with self.assertRaises(ContractError):split_artifact(src)

    def test_score_two_class_brier_and_nll(self):
        out=binary_scores([.2,.8],[0,1])
        self.assertAlmostEqual(out["binary_brier"],.08)
        self.assertAlmostEqual(out["binary_nll"],-math.log(.8))
        self.assertEqual(out["action_proposals_at_5_of_6"],0)

    def test_score_rejects_censored_nonfinite_and_missing(self):
        for bad in ([None],[float("nan")],[-1],[1.2]):
            with self.assertRaises(ContractError):binary_scores(bad,[0])
        with self.assertRaises(ContractError):binary_scores([],[])
        with self.assertRaises(ContractError):binary_scores([.2,.3],[0])

    def test_real_parent_parity_refuses_large_changed_class(self):
        original=[.21397866308689117,.03540749475359917,.11749482154846191,
                  .10098172724246979,.10405883938074112,.21110258996486664,
                  .21697582304477692]
        current=[.26050281524658203,.031099364161491394,.07616259157657623,
                 .10843025147914886,.10584861040115356,.3158022463321686,
                 .10215404629707336]
        drift=parent_probability_drift(original,current)
        self.assertAlmostEqual(drift,.11482177674770355)
        self.assertGreater(drift,MAX_PROB_DIFF)
        self.assertNotEqual(original.index(max(original)),current.index(max(current)))

    def test_parent_parity_allows_only_small_numeric_difference(self):
        base=[1/7]*7
        same=base.copy()
        same[0]+=1e-6
        same[1]-=1e-6
        self.assertLess(parent_probability_drift(base,same),MAX_PROB_DIFF)
        for bad in ([1.]*7,[float("nan")]*7,[.1]*6,[-.1]+[.2]*6):
            with self.assertRaises(ContractError):parent_probability_drift(base,bad)

    def test_fixed_source_and_parent_id(self):
        self.assertEqual(len(SOURCE_SHA),64)
        self.assertEqual(len(PARENT_ID),64)
        self.assertLess(TRAIN_END,HOLD_END)


if __name__=="__main__":unittest.main()
