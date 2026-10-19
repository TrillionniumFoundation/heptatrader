import math
import unittest
from laya_cell.resident_action_ablation_report import aligned_holdout, score_actions, score_prices


class OfflineScoreTests(unittest.TestCase):
    def test_wrong_huge_margin_retains_uncapped_logit_nll(self):
        score = score_actions([[1500.0, -1500.0]], [False])
        self.assertEqual(score["nll"], 3000.0)
        self.assertEqual(score["brier_sum_two_classes"], 2.0)
        self.assertEqual(score["thresholds"]["0.5"], {"predicted_act": 1, "tp": 0, "fp": 1, "tn": 0, "fn": 0})

    def test_temperature_softens_probability_without_changing_class(self):
        raw = score_actions([[3.0, -3.0], [1.0, -1.0]], [True, False])
        scaled = score_actions([[3.0, -3.0], [1.0, -1.0]], [True, False], 100.0)
        self.assertEqual(raw["thresholds"]["0.5"], scaled["thresholds"]["0.5"])
        self.assertGreater(scaled["act_probability"]["min"], 0.5)
        self.assertLess(scaled["act_probability"]["max"], raw["act_probability"]["max"])

    def test_row_reordering_is_not_silently_scored(self):
        source = {"rows": [{"anchor_ms": 10, "row_id": "a"}, {"anchor_ms": 11, "row_id": "b"}]}
        result = {"train_cutoff_ms": 10, "holdout_end_ms": 12, "heldout_rows": 2}
        with self.assertRaisesRegex(ValueError, "HOLDOUT_ROW_ORDER"):
            aligned_holdout(source, result, {"row_ids": ["b", "a"], "prices": [[], []], "actions": [[], []]})

    def test_multiclass_brier_uses_class_sum_and_actual_counts(self):
        score = score_prices([[1 / 7] * 7] * 2, [0, 6])
        self.assertAlmostEqual(score["nll"], math.log(7))
        self.assertAlmostEqual(score["brier_sum_seven_classes"], 6 / 7)
        self.assertEqual(score["true_class_counts"], [1, 0, 0, 0, 0, 0, 1])
        self.assertEqual(score["predicted_class_counts"], [2, 0, 0, 0, 0, 0, 0])

    def test_nonfinite_values_and_non_boolean_action_labels_rejected(self):
        with self.assertRaisesRegex(ValueError, "ACTION_LOGITS"):
            score_actions([[math.inf, 0.0]], [False])
        with self.assertRaisesRegex(ValueError, "ACTION_LABEL_TYPE"):
            score_actions([[1.0, 0.0]], [0])
        with self.assertRaisesRegex(ValueError, "PRICE_PROBABILITIES"):
            score_prices([[0.0] * 7], [0])


if __name__ == "__main__":
    unittest.main()
