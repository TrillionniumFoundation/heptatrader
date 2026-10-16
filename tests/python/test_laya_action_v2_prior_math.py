"""Research-only causal Action V2 prior / class-weighting regressions.

Runs on CPU without loading a resident model, touching Broker or altering trials.
"""
from __future__ import annotations

import math
import unittest

import torch

from laya_cell.integrity import ContractError, digest
from laya_cell.resident_action_v2_research import (
    balanced_action_loss, decide_train_only_action, stratified_action_pairs,
    v2_action_inference,
)

CUTOFF = 1900000000000


def rows(correct=14, wrong=284):
    flags = [True] * correct + [False] * wrong
    return [
        {"row_id": f"{i + 1:064x}", "anchor_ms": CUTOFF - 500000 - i * 400000,
         "future_outcome_available_ms": CUTOFF - 1000 - i * 400000,
         "parent_correct": label}
        for i, label in enumerate(flags)
    ]


def profile(data):
    return decide_train_only_action(
        data, cutoff_ms=CUTOFF, parent_action_logits=torch.zeros(len(data), 2)
    )


class ActionV2PriorMathTests(unittest.TestCase):
    def test_actual_population_counts_preserved_in_new_profile(self):
        r = rows()
        p = profile(r)
        self.assertEqual(p["labels"], {"ACT_correct": 14, "ESCALATE_wrong": 284})
        self.assertTrue(p["action_optimizer_admitted"])
        self.assertEqual(p["paired_action_loss"], "UNWEIGHTED_EQUAL_CLASS_PAIRS")
        self.assertEqual(p["inference_prior_correction"], "FROZEN_UNPAIRED_TRAIN_LABEL_COUNTS")
        self.assertEqual(len(p["training_row_labels_digest"]), 64)

    def test_zero_logits_corrected_to_true_training_prior_not_half(self):
        p = profile(rows())
        out = v2_action_inference(torch.zeros(3, 2), p)
        self.assertEqual(out["model_or_trade_permission"], False)
        self.assertEqual(out["recommendations"], ["ESCALATE_RESEARCH_ONLY"] * 3)
        self.assertAlmostEqual(out["action_correct_probs"][0], 14 / 298, places=12)
        self.assertAlmostEqual(out["training_only_act_prior"], 14 / 298, places=12)
        self.assertTrue(out["prior_correction_not_future_calibration"])

    def test_balanced_population_prior_remains_half(self):
        p = profile(rows(8, 8))
        out = v2_action_inference(torch.zeros(1, 2), p)
        self.assertAlmostEqual(out["action_correct_probs"][0], 0.5, places=12)

    def test_paired_gradients_are_symmetric_not_double_balanced(self):
        p = profile(rows())
        logits = torch.zeros(2, 2, requires_grad=True)
        loss = balanced_action_loss(
            logits, torch.tensor([True, False]), torch.tensor(100.0), p
        )
        loss.backward()
        self.assertGreater(float(loss.detach()), 0)
        self.assertAlmostEqual(float(logits.grad[0, 0]),
                               -float(logits.grad[1, 0]), places=7)
        self.assertLess(float(logits.grad[0, 0]), 0)
        self.assertGreater(float(logits.grad[1, 0]), 0)

    def test_nonpaired_mixed_minibatch_rejected(self):
        p = profile(rows())
        with self.assertRaisesRegex(ContractError, "EQUAL_CLASS_PAIRS"):
            balanced_action_loss(
                torch.zeros(3, 2), torch.tensor([True, False, False]),
                torch.tensor(100.0), p
            )

    def test_single_label_minibatch_has_zero_action_gradient(self):
        p = profile(rows())
        logits = torch.zeros(2, 2, requires_grad=True)
        loss = balanced_action_loss(
            logits, torch.tensor([False, False]), torch.tensor(100.0), p
        )
        loss.backward()
        self.assertEqual(float(logits.grad.abs().sum()), 0)

    def test_label_swap_with_same_counts_cannot_reuse_frozen_pair_plan(self):
        r = rows(4, 12)
        p = profile(r)
        forged = [dict(x) for x in r]
        forged[0]["parent_correct"] = False
        forged[-1]["parent_correct"] = True
        self.assertEqual(sum(x["parent_correct"] for x in r),
                         sum(x["parent_correct"] for x in forged))
        with self.assertRaisesRegex(ContractError, "TRAINING_ROWS_CHANGED"):
            stratified_action_pairs(forged, p)

    def test_frozen_time_swap_refused_even_if_ids_and_labels_unchanged(self):
        r = rows(4, 12)
        p = profile(r)
        forged = [dict(x) for x in r]
        forged[0]["anchor_ms"] -= 1
        with self.assertRaisesRegex(ContractError, "TRAINING_ROWS_CHANGED"):
            stratified_action_pairs(forged, p)

    def test_old_class_weight_profile_fails_closed(self):
        p = profile(rows())
        old = dict(p)
        old.pop("paired_action_loss")
        old["profile_id"] = digest({k: v for k, v in old.items() if k != "profile_id"})
        with self.assertRaisesRegex(ContractError, "PROFILE_CHANGED"):
            v2_action_inference(torch.zeros(1, 2), old)

    def test_training_cutoff_prevents_future_label_leakage(self):
        r = rows(4, 12)
        r[0]["future_outcome_available_ms"] = CUTOFF + 1
        with self.assertRaisesRegex(ContractError, "CAUSAL"):
            profile(r)

    def test_odds_adjustment_is_monotonic_and_ood_abstains(self):
        p = profile(rows())
        t = p["action_temperature_training_only"]
        x = torch.tensor([[0.0, 0.0], [3.0, 0.0], [20.0, 0.0]])
        out = v2_action_inference(x, p)
        self.assertLess(out["action_correct_probs"][0],
                        out["action_correct_probs"][1])
        self.assertIsNone(out["action_correct_probs"][2])
        self.assertEqual(out["ood_abstentions"], 1)

    def test_extreme_finite_offsets_keep_probability_finite(self):
        p = profile(rows())
        x = torch.tensor([[1e19, 1e19]], dtype=torch.float32)
        out = v2_action_inference(x, p)
        self.assertTrue(math.isfinite(out["action_correct_probs"][0]))
        self.assertAlmostEqual(out["action_correct_probs"][0], 14 / 298)


if __name__ == "__main__":
    torch.set_num_threads(1)
    unittest.main()
