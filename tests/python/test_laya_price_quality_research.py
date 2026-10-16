"""Retrospective-only price baseline, immutable chronological holdout."""
import copy
import json
import math
import unittest
from laya_cell.integrity import ContractError
from laya_cell.price_quality_research import (
    CLASSES, SMOOTHING_GRID, BLEND_GRID, prior, score, score_blend, experiment)


def row(index, day="2026-10-07", target=3):
    a=[.05,.10,.10,.20,.15,.15,.25]
    b=[.05,.10,.10,.23,.15,.15,.22]
    return {"anchor_ms":index+100000, "utc_day":day,
            "target_class":target,"parent_probabilities":a,
            "candidate_probabilities":b}


def three_days():
    return ([row(i,day="2026-10-07",target=i%7) for i in range(20)] +
            [row(i+20,day="2026-10-08",target=(i+1)%7) for i in range(20)] +
            [row(i+40,day="2026-10-09",target=(i+2)%7) for i in range(20)])


class PricePriorTests(unittest.TestCase):
    def test_all_seven_weights_positive_and_normalized(self):
        for alpha in SMOOTHING_GRID:
            model=prior([row(i,target=3) for i in range(20)],alpha)
            self.assertEqual(len(model["weights"]),7)
            self.assertAlmostEqual(sum(model["weights"]),1)
            self.assertGreater(min(model["weights"]),0)
            self.assertFalse(model["trade_authority"])
    def test_only_declared_smoothing_allowed(self):
        with self.assertRaises(ContractError):prior([row(1)],0.12345)
    def test_no_empty_training_population(self):
        with self.assertRaises(ContractError):prior([],0.5)
    def test_future_label_mutation_does_not_change_fitted_prior(self):
        rows=three_days()
        before=prior(rows[:20],0.5)
        for r in rows[20:]:r["target_class"]=6
        self.assertEqual(prior(rows[:20],0.5),before)
    def test_invalid_target_rejected(self):
        with self.assertRaises(ContractError):prior([row(1,target=7)],0.5)
        with self.assertRaises(ContractError):prior([row(1,target=True)],0.5)
    def test_correct_one_hot_brier_zero(self):
        r=row(1,target=3)
        exact=[float(i==3) for i in range(7)]
        result=score([r],"constant",constant=exact)
        self.assertEqual(result["brier"],0.)
        self.assertEqual(result["negative_log_likelihood"],0.)
        self.assertEqual(result["top1_hits"],1)
    def test_wrong_one_hot_brier_two_without_infinite_nll(self):
        r=row(1,target=3)
        p=[float(i==0) for i in range(7)]
        result=score([r],"constant",constant=p)
        self.assertEqual(result["brier"],2.)
        self.assertAlmostEqual(result["negative_log_likelihood"],-math.log(1e-12))
    def test_uniform_top1_tie_is_explicitly_first_class(self):
        res=score([row(1,target=3)],"constant",constant=[1/7]*7)
        self.assertEqual(res["top1_hits"],0)
        self.assertEqual(res["top1_tie_policy"],"LOWEST_CLASS_INDEX")
        self.assertAlmostEqual(res["brier"],6/7)
    def test_parent_and_candidate_are_different_but_both_scored(self):
        r=row(1,target=3)
        p=score([r],"parent");c=score([r],"candidate")
        self.assertNotEqual(p["brier"],c["brier"])
    def test_bad_probability_vector_fails(self):
        r=row(1);r["candidate_probabilities"]=[float("nan")]*7
        with self.assertRaises(ContractError):score([r],"candidate")
    def test_empty_scoring_denied(self):
        with self.assertRaises(ContractError):score([],"parent")
    def test_unregistered_scoring_mode_denied(self):
        with self.assertRaises(ContractError):score([row(1)],"unknown")
    def test_test_labels_cannot_select_smoothing(self):
        rows=three_days()
        before=experiment(rows,{"source":"synthetic"})
        revised=copy.deepcopy(rows)
        for r in revised[40:]:r["target_class"]=0
        after=experiment(revised,{"source":"synthetic"})
        self.assertEqual(before["selected_smoothing"],after["selected_smoothing"])
        self.assertEqual(before["selected_prior"],after["selected_prior"])
        self.assertNotEqual(before["test_scores"],after["test_scores"])
    def test_no_promotion_trade_permission_or_prospective_claim(self):
        r=experiment(three_days(),{"source":"synthetic"})
        self.assertFalse(r["claims"]["no_order_authority"] is False)
        self.assertFalse(r["claims"]["economic_alpha_proven"])
        self.assertFalse(r["claims"]["future_coverage_proven"])
        self.assertEqual(r["split_counts"]["test"],20)
    def test_target_class_count_and_predicted_class_count(self):
        data=[row(i,target=3) for i in range(20)]
        v=score(data,"constant",constant=[1/7]*7)
        self.assertEqual(v["observed_class_counts"]["3"],20)
        self.assertEqual(v["predicted_class_counts"]["0"],20)
    def test_blend_at_zero_equals_training_prior(self):
        data=[row(i,target=i%7) for i in range(20)]
        model=prior(data,0.5)
        expected=score(data,"constant",constant=model["weights"])
        self.assertEqual(score_blend(data,model["weights"],"parent",0.0),expected)
        self.assertEqual(score_blend(data,model["weights"],"candidate",0.0),expected)
    def test_blend_at_one_equals_raw_model(self):
        data=[row(i,target=i%7) for i in range(20)]
        model=prior(data,0.5)
        for kind in ("parent","candidate"):
            self.assertEqual(score_blend(data,model["weights"],kind,1.0),
                             score(data,kind))
    def test_unregistered_blend_weight_refused(self):
        data=[row(i,target=i%7) for i in range(20)]
        weights=prior(data,0.5)["weights"]
        with self.assertRaises(ContractError):score_blend(data,weights,"parent",0.123)
    def test_unknown_blend_source_refused(self):
        data=[row(i,target=i%7) for i in range(20)]
        weights=prior(data,0.5)["weights"]
        with self.assertRaises(ContractError):score_blend(data,weights,"future_truth",.1)
    def test_blend_validation_choice_never_reads_test_labels(self):
        a=three_days()
        first=experiment(a,{"source":"synthetic"})
        b=copy.deepcopy(a)
        for row_value in b[40:]:row_value["target_class"]=0
        second=experiment(b,{"source":"synthetic"})
        for kind in ("parent","candidate"):
            self.assertEqual(
                first["validation_selected_blends"][kind]["validation_selected_model_weight"],
                second["validation_selected_blends"][kind]["validation_selected_model_weight"])
    def test_blend_report_has_no_trade_claim(self):
        r=experiment(three_days(),{"source":"synthetic"})
        self.assertEqual(r["blend_weight_grid_fixed_before_test"],list(BLEND_GRID))
        self.assertFalse(r["claims"]["economic_alpha_proven"])
        self.assertFalse(r["claims"]["no_model_promotion"] is False)


if __name__=="__main__":
    unittest.main()
