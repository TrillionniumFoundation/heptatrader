"""Causal cohort and research-only GPU training acceptance tests (CPU)."""
import copy
import math
import unittest
from laya_cell.price_gpu_ablation import (cohort, independent_count, score,
    frequency_prior, choose_validation, promotion_screen, ARMS, COHORT_PER_STAGE)
from laya_cell.integrity import ContractError


class FrozenCohortTests(unittest.TestCase):
    def data(self,n=600):
        initial=1000000000000
        rows=[];samples=[]
        for i in range(n):
            anchor=initial+i*10000
            sample={"sample_id":"sample%04d"%i,"anchor_ms":anchor,
                    "known_at_ms":anchor+200,
                    "outcome_receipts":[{"available_at_ms":anchor+300250,
                                         "observed_at_ms":anchor+300000}]}
            samples.append(sample)
            rows.append({"sample_id":sample["sample_id"],"horizon_sec":300,
                         "anchor_ms":anchor,"target_class":i%7,
                         "outcome_receipt_digest":"a"*64})
        return rows,samples
    def test_32_identical_protocol_rows_per_arm_without_time_overlap(self):
        rows,samples=self.data()
        c=cohort(rows,samples)
        self.assertEqual([len(c[k]) for k in ("training","validation","test")],[32,32,32])
        self.assertGreater(min(r["anchor_ms"] for r in c["validation"]),
                           max(r["anchor_ms"] for r in c["training"])+300000)
        self.assertGreater(min(r["anchor_ms"] for r in c["test"]),
                           max(r["anchor_ms"] for r in c["validation"])+300000)
        self.assertEqual(len(set(sum(c["row_identity"].values(),[]))),96)
        self.assertFalse(c["trading_authority"])
    def test_window_does_not_need_future_labeled_test_to_split(self):
        rows,samples=self.data()
        original=cohort(rows,samples)
        modified=copy.deepcopy(rows)
        for r in modified:
            if r["anchor_ms"]>=original["test_input_after_validation_ms"]:
                r["target_class"]=(r["target_class"]+3)%7
        later=cohort(modified,samples)
        self.assertEqual(original["row_identity"]["training"],later["row_identity"]["training"])
        self.assertEqual(original["row_identity"]["validation"],later["row_identity"]["validation"])
    def test_future_training_label_refused(self):
        rows,samples=self.data()
        samples[0]["outcome_receipts"][0]["available_at_ms"]+=3600000
        with self.assertRaisesRegex(ContractError,"TRAIN_FUTURE_LABEL"):cohort(rows,samples)
    def test_duplicate_sample_id_fails(self):
        rows,samples=self.data()
        samples[0]["sample_id"]=samples[1]["sample_id"]
        with self.assertRaisesRegex(ContractError,"DUPLICATE_SAMPLE_ID"):cohort(rows,samples)
    def test_duplicate_anchor_fails(self):
        rows,samples=self.data()
        rows[1]["anchor_ms"]=rows[0]["anchor_ms"]
        with self.assertRaisesRegex(ContractError,"SAMPLE_IDENTITIES"):cohort(rows,samples)
    def test_capacity_refused(self):
        rows,samples=self.data(80)
        with self.assertRaises(ContractError):cohort(rows,samples)
    def test_real_horizon_independence_not_row_count(self):
        rows,_=self.data()
        subset=rows[:32]
        self.assertEqual(independent_count(subset),2)
    def test_model_family_cannot_grow_silently(self):
        self.assertEqual(ARMS,("original_action_weight_001","price_only_action_weight_0"))


class ScoringTests(unittest.TestCase):
    def test_perfect_and_uniform(self):
        self.assertAlmostEqual(score([[0,0,0,1,0,0,0]],[3])["brier"],0)
        self.assertAlmostEqual(score([[1/7]*7],[3])["brier"],6/7)
    def test_nonfinite_or_malformed_fails(self):
        for data in [[.5,.5], [1.]*7, [math.nan]+[0]*6,[True]+[0]*6]:
            with self.assertRaises(ContractError):score([data],[3])
        with self.assertRaises(ContractError):score([[1/7]*7],[True])
    def test_frequency_prior_only_training(self):
        rows=[{"target_class":3}]*16
        prior=frequency_prior(rows)
        self.assertAlmostEqual(sum(prior),1)
        self.assertGreater(prior[3],.75)
    def test_fixed_validation_selection_does_not_observe_test(self):
        numbers={"train_frequency_prior":.8,"frozen_parent":.95,
                 "original_action_weight_001":.92,
                 "price_only_action_weight_0":.78}
        res={k:{"brier":v,"nll":1.2} for k,v in numbers.items()}
        self.assertEqual(choose_validation(res),"price_only_action_weight_0")
        with self.assertRaises(ContractError):choose_validation({"new_arm":res["frozen_parent"]})
    def test_independent_threshold_never_bypassed_by_dense_rows(self):
        rows=[{"anchor_ms":1000000000000+i*10000} for i in range(40)]
        c={"test":rows}
        scores={"train_frequency_prior":{"brier":.8,"nll":1.2},
                "price_only_action_weight_0":{"brier":.7,"nll":1.1}}
        r=promotion_screen("price_only_action_weight_0",scores,c)
        self.assertTrue(r["beats_static_prior_brier"])
        self.assertFalse(r["research_price_signal_qualified"])
        self.assertLess(r["test_independent_nonoverlap_5min"],r["minimum_independent_required"])

if __name__=="__main__":
    unittest.main()
