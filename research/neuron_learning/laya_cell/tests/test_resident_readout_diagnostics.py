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


if __name__ == "__main__":
    unittest.main()
