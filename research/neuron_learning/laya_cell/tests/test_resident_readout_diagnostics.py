"""Numerical oracles for detached readout diagnostics."""
import math
import unittest
import torch
from laya_cell.resident_readout_diagnostics import (
    action_margin_parts, geometry, probability_metrics, tensor_summary)


class ReadoutDiagnosticsTests(unittest.TestCase):
    def test_known_margin_and_support_contributions_without_mutation(self):
        h = torch.tensor([[1., 2., 3.], [-1., 0., 1.]])
        w = torch.tensor([[2., 3., -1.], [1., 1., 1.]])
        bias = torch.tensor([.25, -.25])
        a = torch.tensor([[.1, .2]])
        b = torch.tensor([[1.], [-1.]])
        copies = [x.clone() for x in (h, w, bias, a, b)]
        result = action_margin_parts(h, w, bias, a, b, [0, 2])
        for key, expected in {
            "base_adapted_columns": [-5., -3.], "base_other_columns": [4., 0.],
            "base_bias": [.5, .5], "adapter": [1.4, .2],
            "reconstructed_margin_fp64": [.9, -2.3]}.items():
            torch.testing.assert_close(result[key], torch.tensor(expected, dtype=torch.float64), atol=1e-7, rtol=1e-7)
        self.assertTrue(all(torch.equal(x, y) for x, y in zip((h, w, bias, a, b), copies)))

    def test_mask_and_nonfinite_are_rejected(self):
        args = [torch.ones(2, 3), torch.ones(2, 3), torch.zeros(2),
                torch.ones(1, 2), torch.ones(2, 1)]
        for columns in ([0, 0], [-1, 0], [0, 3]):
            with self.subTest(columns=columns), self.assertRaises(ValueError):
                action_margin_parts(*args, columns)
        args[0][0, 0] = float("nan")
        with self.assertRaisesRegex(ValueError, "NONFINITE_READOUT"):
            action_margin_parts(*args, [0, 2])

    def test_constant_geometry_is_only_training_projection(self):
        h = torch.tensor([[1., 0.], [1., 0.], [1., 0.]])
        result = geometry(h, torch.tensor([3., 3., 3.]), .25)
        self.assertEqual(result["rank"], 1)
        self.assertTrue(result["training_only_not_candidate"])
        self.assertLess(result["constant_offset_projection_rmse"], 1e-12)
        self.assertLess(result["prior_offset_fit_rmse"], 1e-12)
        self.assertAlmostEqual(result["minimum_margin_weight_delta_l2"], 3 + math.log(3), places=10)

    def test_nll_preserves_saturated_raw_margin(self):
        result = probability_metrics(torch.zeros(2, 7),
                                     torch.tensor([[3000., 0.], [0., 3000.]]),
                                     [0, 1], [1, 0])
        self.assertEqual(result["action_nll"], 3000)
        self.assertEqual(result["action_brier"], 2)
        self.assertEqual(result["raw_margin_auc_training_only"], 0)
        self.assertAlmostEqual(result["price_nll"], math.log(7))

    def test_summary_rejects_empty_and_nonfinite(self):
        for x in (torch.empty(0), torch.tensor([float("inf")])):
            with self.assertRaises(ValueError):
                tensor_summary(x)

    def test_cls_intervention_preserves_features_and_input(self):
        from laya_cell.resident_readout_diagnostics import normalize_cls_input
        cls = torch.arange(768, dtype=torch.float64).repeat(2, 1) * 100 + 9000
        features = torch.tensor([[.7, .2, .4, 7/255], [.6, .1, .5, 7/255]], dtype=torch.float64)
        original = torch.cat((cls, features), dim=-1)
        saved = original.clone()
        result = normalize_cls_input(original)
        self.assertTrue(torch.equal(original, saved))
        self.assertTrue(torch.equal(result[:, 768:], features))
        torch.testing.assert_close(result[:, :768].mean(-1), torch.zeros(2, dtype=torch.float64), atol=1e-12, rtol=0)
        torch.testing.assert_close(result[:, :768].var(-1, unbiased=False), torch.ones(2, dtype=torch.float64), atol=1e-10, rtol=0)
        with self.assertRaises(ValueError):
            normalize_cls_input(torch.ones(2, 771))

    def test_prior_offset_is_train_only_and_preserves_ranking(self):
        from laya_cell.resident_readout_diagnostics import fit_training_prior_offset, apply_margin_offset
        margins = torch.tensor([30.9, 31., 31.05, 31.1], dtype=torch.float64)
        profile = fit_training_prior_offset(margins, .05, split="train")
        self.assertAlmostEqual(profile["training_mean_probability_fp64"], .05, places=14)
        logits = torch.stack([margins/2, -margins/2], -1).requires_grad_()
        shifted = apply_margin_offset(logits, profile["offset"])
        self.assertTrue(torch.equal(shifted[:,0].argsort(), logits[:,0].argsort()))
        torch.testing.assert_close(shifted.sum(-1), logits.sum(-1))
        shifted.sum().backward()
        torch.testing.assert_close(logits.grad, torch.ones_like(logits))
        self.assertFalse(profile["individual_labels_used_in_offset_fit"])
        with self.assertRaises(ValueError):
            fit_training_prior_offset(margins, .05, split="holdout")

    def test_prior_offset_constant_case_and_invalid_input(self):
        from laya_cell.resident_readout_diagnostics import fit_training_prior_offset
        profile = fit_training_prior_offset(torch.tensor([31., 31.]), .25, split="train")
        self.assertAlmostEqual(profile["offset"], -math.log(3)-31, places=12)
        for prior in (0., 1., float("nan")):
            with self.assertRaises(ValueError):
                fit_training_prior_offset(torch.tensor([31., 31.]), prior, split="train")


if __name__ == "__main__":
    unittest.main()
