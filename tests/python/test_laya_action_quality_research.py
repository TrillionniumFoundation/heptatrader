"""Adversarial validation of offline action calibration; no trade or GPU."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from laya_cell.action_quality_research import (
    brier_for_correctness, probabilities, fit, score, experiment,
    predict_correctness, chronological_days, collect_closed, MIN_ACT_CORRECT_PROB)
from laya_cell.integrity import ContractError, digest, sha_file


def row(i, day="2026-10-07", correct=False, confidence=0.4):
    d = int(datetime.fromisoformat(day + "T12:00:00+00:00").timestamp() * 1000)
    return {"anchor_ms": d + 360000 * i, "utc_day": day,
            "parent_correct": correct, "parent_confidence": confidence,
            "raw_action_correct_probability": 1.0, "raw_action_brier": 0.0 if correct else 2.0,
            "plan_id": "plan"}


class CalibrationMathTests(unittest.TestCase):
    def test_binary_brier_upper_bound_and_half(self):
        self.assertEqual(brier_for_correctness(1.0, False), 2.0)
        self.assertEqual(brier_for_correctness(0.0, False), 0.0)
        self.assertEqual(brier_for_correctness(.5, False), .5)
    def test_nonfinite_and_boolean_probability_are_rejected(self):
        with self.assertRaises(ContractError): probabilities([float("nan"), .5], 2)
        with self.assertRaises(ContractError): probabilities([True, False], 2)
        with self.assertRaises(ContractError): brier_for_correctness(float("inf"), False)
        with self.assertRaises(ContractError): brier_for_correctness(.5, 1)
    def test_normalization_and_shape_fail_closed(self):
        with self.assertRaises(ContractError): probabilities([.9, .9], 2)
        with self.assertRaises(ContractError): probabilities([.5], 2)
        with self.assertRaises(ContractError): probabilities([-1., 2.], 2)
    def test_laplace_prior_is_bounded_and_hashed(self):
        rows = [row(i, correct=(i == 0)) for i in range(20)]
        model = fit(rows, 1)
        self.assertEqual(len(model["correctness_rates"]), 1)
        self.assertAlmostEqual(model["laplace_global_correctness"], 2/22)
        self.assertLess(predict_correctness(model, rows[0]), .2)
        self.assertFalse(model["trade_authority"])
    def test_model_mutation_refused(self):
        model=fit([row(i,correct=(i==0)) for i in range(20)], 2)
        model["correctness_rates"][0]=1.0
        with self.assertRaisesRegex(ContractError, "MODEL_CHANGED"): predict_correctness(model,row(0))
    def test_bin_model_unknown_size_rejected(self):
        with self.assertRaises(ContractError):fit([row(0)], 3)
    def test_a_single_correct_label_cannot_authorize_act_by_default(self):
        training=[row(i,correct=(i==0),confidence=.3+i/100) for i in range(20)]
        for bins in (1,2,4):
            model=fit(training,bins)
            self.assertLess(max(model["correctness_rates"]),MIN_ACT_CORRECT_PROB)
            self.assertEqual(score(model,training)["act_suggestions"],0)
    def test_frozen_three_day_split_no_future_training_rows(self):
        rows=[row(i,"2026-10-07") for i in range(20)]+[row(i,"2026-10-08") for i in range(20)]+[row(i,"2026-10-09") for i in range(20)]
        train,val,test=chronological_days(rows)
        self.assertEqual((len(train),len(val),len(test)),(20,20,20))
        self.assertLess(max(x["anchor_ms"] for x in train),min(x["anchor_ms"] for x in val))
        self.assertLess(max(x["anchor_ms"] for x in val),min(x["anchor_ms"] for x in test))
    def test_two_days_not_an_independent_test(self):
        with self.assertRaisesRegex(ContractError, "THREE_DAYS"):chronological_days([row(i) for i in range(20)])
    def test_selected_candidate_is_unaffected_by_test_labels(self):
        rows=([row(i,"2026-10-07",correct=i%16==0,confidence=.15+i/100) for i in range(20)] +
              [row(i,"2026-10-08",correct=i%10==0,confidence=.15+i/100) for i in range(20)] +
              [row(i,"2026-10-09",correct=i%9==0,confidence=.15+i/100) for i in range(20)])
        original=experiment(rows,{"source_files_sha256":"x"})
        mutated=copy.deepcopy(rows)
        for r in mutated[40:]:r["parent_correct"]=not r["parent_correct"]
        later=experiment(mutated,{"source_files_sha256":"x"})
        self.assertEqual(original["validation_selected"],later["validation_selected"])
        self.assertEqual(original["selected_model"],later["selected_model"])
        self.assertNotEqual(original["test_scores_after_selection"],later["test_scores_after_selection"])
    def test_model_does_not_use_retrospective_raw_logits_as_confidence(self):
        data=[row(i,correct=(i==0)) for i in range(20)]
        a=fit(data,2)
        for r in data:r["raw_action_correct_probability"]=0.0
        self.assertEqual(a,fit(data,2))
    def test_report_denies_promotion_and_economic_claims(self):
        rows=sum(([row(i,day,correct=i==0) for i in range(20)] for day in
                  ("2026-10-07","2026-10-08","2026-10-09")),[])
        rep=experiment(rows,{"source_files_sha256":"x"})
        self.assertFalse(rep["claims"]["order_authority"])
        self.assertFalse(rep["claims"]["economic_alpha_proven"])
        self.assertTrue(rep["claims"]["retrospective_only"])


class SealedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
    def build(self, name="trial", date="2026-10-07", n=12, correct_every=10, pending=False):
        d=self.root/name;d.mkdir()
        start=int(datetime.fromisoformat(date+"T12:00:00+00:00").timestamp()*1000)
        plan={"candidate_id":name,"scope":"period","trial":1,
              "rule":{"horizon_ms":300000},"start_ms":start,"outcome_end_ms":start+n*360000}
        plan["plan_id"]=digest(plan);(d/"plan.json").write_text(json.dumps(plan))
        if not pending:
            (d/"qualification.json").write_text(json.dumps({
                "plan_id":plan["plan_id"],"candidate_id":name,"state":"REJECTED"}))
        for i in range(n):
            anchor=start+i*360000;good=(i%correct_every)==0
            parent=[.4]+[.1]*6
            pred={"candidate_id":name,"plan_id":plan["plan_id"],"slot":i,"anchor_ms":anchor,
                  "created_at_ms":anchor+3000,"trained_until_ms":anchor-100000,
                  "parent_probs":parent,"candidate_probs":list(parent),"candidate_act_escalate_probs":[1.0,0.0]}
            path=d/("prediction-%03d.json"%i);path.write_text(json.dumps(pred))
            obs={"candidate_id":name,"plan_id":plan["plan_id"],"slot":i,"status":"OBSERVED",
                 "target_class":0 if good else 1,"anchor_ms":anchor,"outcome_ms":anchor+300000,
                 "action_head_brier":0.0 if good else 2.0,
                 "prediction_file_sha256":sha_file(path)}
            (d/("outcome-%03d.json"%i)).write_text(json.dumps(obs))
        return d,plan
    def test_closed_observations_are_read_only_and_exact(self):
        d,_=self.build();files={p.name:p.read_bytes() for p in d.iterdir()}
        rows,scope=collect_closed(self.root)
        self.assertEqual(len(rows),12)
        self.assertEqual(sum(r["parent_correct"] for r in rows),2)
        self.assertEqual(scope["pending_plans_excluded"],0)
        self.assertEqual(files,{p.name:p.read_bytes() for p in d.iterdir()})
    def test_pending_trial_excluded_even_if_it_has_observations(self):
        self.build(pending=True)
        self.build("closed",date="2026-10-08")
        rows,prov=collect_closed(self.root)
        self.assertEqual(len(rows),12);self.assertEqual(prov["pending_plans_excluded"],1)
    def test_tampered_prediction_is_rejected(self):
        d,_=self.build();f=d/"prediction-000.json";p=json.loads(f.read_text())
        p["created_at_ms"]+=2;f.write_text(json.dumps(p))
        with self.assertRaisesRegex(ContractError,"MISSING_OR_CHANGED"):collect_closed(self.root)
    def test_modified_reported_brier_is_rejected(self):
        d,_=self.build();f=d/"outcome-001.json";p=json.loads(f.read_text())
        p["action_head_brier"]=0.5;f.write_text(json.dumps(p))
        with self.assertRaisesRegex(ContractError,"REPORTED_SCORE_CHANGED"):collect_closed(self.root)
    def test_forged_qualification_is_rejected(self):
        d,_=self.build();f=d/"qualification.json";p=json.loads(f.read_text())
        p["candidate_id"]="another";f.write_text(json.dumps(p))
        with self.assertRaisesRegex(ContractError,"QUALIFICATION_IDENTITY"):collect_closed(self.root)
    def test_same_anchor_from_two_trials_is_not_counted_twice(self):
        self.build();self.build("different")
        with self.assertRaisesRegex(ContractError,"OVERLAPPING_OBSERVED_ANCHORS"):collect_closed(self.root)
    def test_prediction_after_outcome_rejected_even_when_digest_matches(self):
        d,_=self.build();f=d/"prediction-001.json";p=json.loads(f.read_text())
        p["created_at_ms"]=p["anchor_ms"]+301000;f.write_text(json.dumps(p))
        o=d/"outcome-001.json";data=json.loads(o.read_text());data["prediction_file_sha256"]=sha_file(f)
        o.write_text(json.dumps(data))
        with self.assertRaisesRegex(ContractError,"NONCAUSAL_PREDICTION"):collect_closed(self.root)
    def test_qualifications_are_required(self):
        self.build(pending=True)
        with self.assertRaisesRegex(ContractError,"NO_CLOSED_OBSERVATIONS"):collect_closed(self.root)


if __name__ == "__main__":
    unittest.main()
