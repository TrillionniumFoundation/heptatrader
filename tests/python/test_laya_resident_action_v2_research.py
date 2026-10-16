"""Future-only Laya ACT/ESCALATE supervision and numerical stability tests.

No GPU, Broker, unsealed future label, or existing Laya trial is modified.
"""
from __future__ import annotations
import math
import unittest

import torch
from laya_cell.integrity import ContractError
from laya_cell.resident_action_v2_research import (
    MIN_EACH_LABEL,MAX_ACTION_LOSS_FRACTION,
    balanced_action_loss,decide_train_only_action,research_action_recommendation,
    stratified_action_pairs)

CUTOFF=1800000400000
def data(flags):
    return [{"row_id":f"{i:064x}","anchor_ms":CUTOFF-10000-1000*i,
             "future_outcome_available_ms":CUTOFF-1000-100*i,
             "parent_correct":bool(flag)}
            for i,flag in enumerate(flags)]
def logits(count,margin=5000.):
    return torch.tensor([[margin/2,-margin/2]]*count,dtype=torch.float32)


class ActionTrainingV2Tests(unittest.TestCase):
    def setUp(self):
        self.unbalanced=data([False]*32)
        self.balanced=data([True,False]*8)
    def policy(self,rows,mag=5000.):
        return decide_train_only_action(rows,cutoff_ms=CUTOFF,
                                         parent_action_logits=logits(len(rows),mag))

    def test_actual_all_escalate_batch_refuses_action_training(self):
        p=self.policy(self.unbalanced)
        self.assertEqual(p["labels"],{"ACT_correct":0,"ESCALATE_wrong":32})
        self.assertFalse(p["action_optimizer_admitted"])
        self.assertIsNone(p["action_temperature_training_only"])
        self.assertEqual(p["action_refusal_reason"],"NO_BOTH_TRUE_ACTION_LABELS")
        self.assertTrue(p["price_learning_not_disallowed"])
        self.assertFalse(p["model_promotion"])
    def test_unbalanced_action_gradients_zero_and_price_loss_unaffected(self):
        p=self.policy(self.unbalanced)
        x=logits(32).requires_grad_()
        loss=balanced_action_loss(x,torch.zeros(32,dtype=torch.bool),
                                  torch.tensor(2.5),p)
        loss.backward()
        self.assertEqual(float(loss),0.0)
        self.assertEqual(float(x.grad.abs().sum()),0.0)
    def test_unbalanced_training_never_suggests_ACT_even_given_one_probability(self):
        p=self.policy(self.unbalanced)
        for v in (0.0,0.75,1.0):
            self.assertEqual(research_action_recommendation(p,v)["signal"],
                             "ESCALATE_ONLY_NO_BALANCED_TRAINING")
    def test_balanced_true_labels_enable_research_calibration(self):
        p=self.policy(self.balanced)
        self.assertEqual(p["labels"],{"ACT_correct":8,"ESCALATE_wrong":8})
        self.assertTrue(p["action_optimizer_admitted"])
        self.assertEqual(p["action_temperature_training_only"],5000.0)
        self.assertEqual(p["training_rows"],16)
    def test_mixed_class_requires_at_least_four_in_each(self):
        p=self.policy(data([True]*3+[False]*13))
        self.assertFalse(p["action_optimizer_admitted"])
        p=self.policy(data([True]*4+[False]*12))
        self.assertTrue(p["action_optimizer_admitted"])
    def test_disjoint_action_pairs_use_genuine_both_labels(self):
        rows=data([True]*4+[False]*12)
        p=self.policy(rows)
        pairs=stratified_action_pairs(rows,p)
        self.assertEqual(len(pairs),4)
        flattened=[k for pair in pairs for k in pair]
        self.assertEqual(len(flattened),len(set(flattened)))
        for i,j in pairs:
            self.assertTrue(rows[i]["parent_correct"])
            self.assertFalse(rows[j]["parent_correct"])
        # The majority-only residual rows remain for PRICE, not ACTION.
        self.assertEqual(len(rows)-len(flattened),8)
    def test_single_class_produces_no_action_pairs(self):
        self.assertEqual(stratified_action_pairs(self.unbalanced,self.policy(self.unbalanced)),[])
    def test_replacement_label_set_rejected_before_action_pairing(self):
        rows=[dict(x) for x in self.balanced]
        profile=self.policy(self.balanced)
        rows[0]["parent_correct"]=False
        with self.assertRaisesRegex(ContractError,"STRATIFIED"):
            stratified_action_pairs(rows,profile)
    def test_genuine_outcome_future_leakage_refused(self):
        a=self.balanced.copy()
        a[0]={**a[0],"future_outcome_available_ms":CUTOFF+1}
        with self.assertRaisesRegex(ContractError,"CAUSAL"):
            self.policy(a)
    def test_training_cannot_use_unmatured_anchor(self):
        a=self.balanced.copy()
        a[0]={**a[0],"anchor_ms":CUTOFF}
        with self.assertRaisesRegex(ContractError,"CAUSAL"):
            self.policy(a)
    def test_duplicate_row_fails_closed(self):
        a=[dict(x) for x in self.balanced]
        a[1]["row_id"]=a[0]["row_id"]
        with self.assertRaisesRegex(ContractError,"CAUSAL"):
            self.policy(a)
    def test_bad_training_class_or_identity_fails(self):
        a=[dict(x) for x in self.balanced]
        a[0]["parent_correct"]=1
        with self.assertRaisesRegex(ContractError,"CAUSAL"):
            self.policy(a)
    def test_action_optimizer_never_runs_on_single_class_minibatch(self):
        p=self.policy(self.balanced)
        x=logits(4).requires_grad_()
        loss=balanced_action_loss(x,torch.zeros(4,dtype=torch.bool),
                                  torch.tensor(3.0),p)
        loss.backward()
        self.assertEqual(float(x.grad.abs().sum()),0)
    def test_extreme_raw_logits_scaled_finite_with_nonzero_gradient(self):
        p=self.policy(self.balanced,mag=6000.)
        x=torch.tensor([[3000.,-3000.],[-3000.,3000.]]*8,
                       dtype=torch.float32,requires_grad=True)
        targets=torch.tensor([True,False]*8,dtype=torch.bool)
        primary=torch.tensor(2.5,requires_grad=True)
        value=balanced_action_loss(x,targets,primary,p)
        self.assertTrue(torch.isfinite(value))
        self.assertLess(float(value),.5)
        self.assertGreater(float(value),0)
        value.backward()
        self.assertTrue(torch.isfinite(x.grad).all())
        self.assertGreater(float(x.grad.abs().sum()),0)
    def test_future_v2_inference_uses_exact_frozen_train_temperature(self):
        from laya_cell.resident_action_v2_research import v2_action_inference
        p=self.policy(self.balanced,mag=5000.)
        a=logits(16,margin=5000.)
        x=v2_action_inference(a,p)
        self.assertEqual(x["training_only_temperature"],5000.)
        self.assertAlmostEqual(x["action_correct_probs"][0],1/(1+math.exp(-1)),places=6)
        self.assertTrue(all(sig=="ESCALATE_RESEARCH_ONLY" for sig in x["recommendations"]))
        self.assertFalse(x["model_or_trade_permission"])
    def test_real_v1_single_class_must_refuse_any_v2_inference_probabilities(self):
        from laya_cell.resident_action_v2_research import v2_action_inference
        p=self.policy(self.unbalanced,mag=2672.)
        x=v2_action_inference(logits(32,margin=2672.),p)
        self.assertIsNone(x["action_correct_probs"])
        self.assertEqual(x["recommendations"],["ESCALATE_ONLY_NO_BALANCED_TRAINING"]*32)
        self.assertEqual(x["status"],"NO_TRAINING_SUPPORT_ALL_ESCALATE")
    def test_out_of_train_distribution_logits_abstain(self):
        from laya_cell.resident_action_v2_research import v2_action_inference
        p=self.policy(self.balanced,mag=5000.)
        x=v2_action_inference(torch.tensor([[100000.,0.],[3000.,-3000.]]),p)
        self.assertEqual(x["ood_abstentions"],1)
        self.assertIsNone(x["action_correct_probs"][0])
        self.assertEqual(x["recommendations"][0],"ESCALATE_OOD_ACTION_LOGIT_SHIFT")
        self.assertIsNotNone(x["action_correct_probs"][1])
    def test_future_training_temperature_cannot_be_swapped_during_inference(self):
        from laya_cell.resident_action_v2_research import v2_action_inference
        p=self.policy(self.balanced,mag=5000.)
        forged={**p,"action_temperature_training_only":1}
        with self.assertRaisesRegex(ContractError,"PROFILE_CHANGED"):
            v2_action_inference(logits(16),forged)
    def test_extreme_absolute_logit_offset_does_not_forge_confidence(self):
        from laya_cell.resident_action_v2_research import v2_action_inference
        p=self.policy(self.balanced,mag=5000.)
        x=v2_action_inference(torch.tensor([[1e19,1e19]],dtype=torch.float32),p)
        self.assertAlmostEqual(x["action_correct_probs"][0],.5,places=6)
        self.assertEqual(x["recommendations"],["ESCALATE_RESEARCH_ONLY"])
    def test_research_act_is_not_trade_or_model_permission(self):
        from laya_cell.resident_action_v2_research import v2_action_inference
        p=self.policy(self.balanced,mag=5000.)
        x=v2_action_inference(torch.tensor([[6000.,-6000.]]),p)
        self.assertEqual(x["recommendations"][0],"RESEARCH_ONLY_ACT_CANDIDATE")
        self.assertFalse(x["model_or_trade_permission"])
    def test_future_logits_must_be_finite_and_bounded_batch(self):
        from laya_cell.resident_action_v2_research import v2_action_inference
        p=self.policy(self.balanced,mag=5000.)
        for raw in (torch.tensor([[float('inf'),0.]]),
                    torch.zeros(0,2),torch.zeros(2,3)):
            with self.assertRaisesRegex(ContractError,"INFERENCE_BAD_LOGITS"):
                v2_action_inference(raw,p)
    def test_action_numeric_loss_never_dominates_primary_loss(self):
        p=self.policy(self.balanced)
        x=logits(16).requires_grad_()
        target=torch.tensor([True,False]*8)
        primary=torch.tensor(.1)
        value=balanced_action_loss(x,target,primary,p)
        self.assertLessEqual(float(value.detach()),.1*MAX_ACTION_LOSS_FRACTION+1e-6)
    def test_frozen_profile_tampering_rejected(self):
        p=self.policy(self.balanced)
        q={**p,"action_temperature_training_only":1.0}
        with self.assertRaisesRegex(ContractError,"PROFILE_CHANGED"):
            balanced_action_loss(logits(16),torch.tensor([True,False]*8),
                                 torch.tensor(1.),q)
    def test_source_rows_hash_and_temperature_bound_to_training_only(self):
        p=self.policy(self.balanced)
        q=self.policy(self.balanced,mag=9000)
        self.assertNotEqual(p["profile_id"],q["profile_id"])
        self.assertEqual(p["training_row_ids_digest"],q["training_row_ids_digest"])
        self.assertEqual(q["action_temperature_training_only"],9000)
    def test_pretrained_margin_outside_numeric_budget_refuses(self):
        with self.assertRaisesRegex(ContractError,"UNCALIBRATABLE"):
            self.policy(self.balanced,mag=1_500_000.)
    def test_nonfinite_action_logit_rejected(self):
        x=logits(16);x[0,0]=float('nan')
        with self.assertRaisesRegex(ContractError,"PARENT_LOGIT"):
            decide_train_only_action(self.balanced,cutoff_ms=CUTOFF,
                                     parent_action_logits=x)
    def test_action_prob_is_not_trade_authority(self):
        p=self.policy(self.balanced)
        self.assertFalse(research_action_recommendation(p,0.95)["model_or_trade_permission"])
        self.assertEqual(research_action_recommendation(p,.2)["signal"],
                         "ESCALATE_RESEARCH_ONLY")
    def test_invalid_probability_rejected(self):
        p=self.policy(self.balanced)
        for x in (float('nan'),-1.,1.1,True):
            with self.assertRaisesRegex(ContractError,"PROBABILITY"):
                research_action_recommendation(p,x)


if __name__=="__main__":torch.set_num_threads(1);unittest.main()
