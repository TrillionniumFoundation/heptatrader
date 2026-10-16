"""CPU-only contracts for seven-bin residual calibration research."""
import copy
import math
import unittest

from laya_cell.integrity import ContractError
from laya_cell.price_residual_research import fit, forecast, score_model, experiment
from laya_cell.price_quality_research import prior


def row(i, day="2026-10-07", target=3):
    pred=[.34,.03,.09,.18,.18,.13,.05]
    parent=[.35,.03,.09,.17,.18,.13,.05]
    return {"anchor_ms":1800000000000+i*360000,
            "utc_day":day, "target_class":target,
            "parent_probabilities":parent, "candidate_probabilities":pred}


def dataset():
    return ([row(i,"2026-10-07",target=i%7) for i in range(30)] +
            [row(i+30,"2026-10-08",target=(i+1)%7) for i in range(30)] +
            [row(i+60,"2026-10-09",target=(i+2)%7) for i in range(30)])


class ConditionalResearch(unittest.TestCase):
    def setUp(self):
        self.training=dataset()[:30]
        self.global_prior=prior(self.training,.5)["weights"]
    def test_conditional_bins_normalize_and_backoff_on_empty_predictions(self):
        model=fit(self.training,"candidate","conditional",8.0,self.global_prior)
        self.assertEqual(len(model["conditionals"]),7)
        for q in model["conditionals"]:
            self.assertAlmostEqual(sum(q),1)
            self.assertTrue(all(v>0 for v in q))
        # No rows predict class 4; conditional must equal training-only prior.
        self.assertEqual(model["conditionals"][4],self.global_prior)
    def test_ratio_shift_finite_and_normalized(self):
        model=fit(self.training,"candidate","prior_ratio",1.5,self.global_prior)
        p=forecast(model,self.training[0]["candidate_probabilities"])
        self.assertAlmostEqual(sum(p),1)
        self.assertTrue(all(0<=v<=1 and math.isfinite(v) for v in p))
    def test_only_predeclared_families_parameters_sources(self):
        for family,p in [("conditional",7.0),("prior_ratio",.333),("made_up",2.0)]:
            with self.assertRaises(ContractError):
                fit(self.training,"candidate",family,p,self.global_prior)
        with self.assertRaises(ContractError):
            fit(self.training,"BUY_SELL","conditional",8.0,self.global_prior)
    def test_model_hash_mutation_fails_closed(self):
        model=fit(self.training,"candidate","prior_ratio",1.0,self.global_prior)
        model["ratios"][0]=100.0
        with self.assertRaisesRegex(ContractError,"MODEL_INVALID"):
            forecast(model,self.training[0]["candidate_probabilities"])
    def test_nonfinite_probability_rejected(self):
        model=fit(self.training,"candidate","conditional",2.0,self.global_prior)
        with self.assertRaises(ContractError):forecast(model,[float("nan")]*7)
        with self.assertRaises(ContractError):forecast(model,[1.,0.,0.])
    def test_test_labels_do_not_choose_model(self):
        a=dataset()
        original=experiment(a,{"source":"synthetic"})
        revised=copy.deepcopy(a)
        for x in revised[60:]:x["target_class"]=0
        changed=experiment(revised,{"source":"synthetic"})
        self.assertEqual(original["validation_selected"],changed["validation_selected"])
        self.assertEqual(original["selected_model"],changed["selected_model"])
        self.assertNotEqual(original["test_scores"],changed["test_scores"])
    def test_score_does_not_touch_original_probabilities(self):
        model=fit(self.training,"candidate","conditional",2.0,self.global_prior)
        original=copy.deepcopy(self.training)
        outcome=score_model(model,self.training)
        self.assertEqual(self.training,original)
        self.assertEqual(outcome["rows"],len(self.training))
    def test_no_authority(self):
        result=experiment(dataset(),{"source":"synthetic"})
        self.assertTrue(result["claims"]["retrospective_only"])
        self.assertFalse(result["claims"]["economic_edge_proven"])
        self.assertFalse(result["claims"]["no_model_promotion"] is False)
        self.assertFalse(result["claims"]["no_trading_authority"] is False)


if __name__=="__main__":
    unittest.main()
