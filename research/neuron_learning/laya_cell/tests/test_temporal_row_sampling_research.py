"""Target-blind temporal row study: research only, no resident training."""
from __future__ import annotations

import unittest

from laya_cell.integrity import ContractError, digest
from laya_cell.temporal_row_sampling_research import select_recent_nonoverlap

T0 = 1792000000000
SHA = "a" * 64


def row(i, *, horizon=300, step_ms=300000, target=None):
    return {
        "sample_id": digest(["real-like-synthetic-sample", i, horizon]),
        "anchor_ms": T0 + i * step_ms,
        "horizon_sec": horizon,
        "target_class": i % 7 if target is None else target,
        "outcome_receipt_digest": digest(["receipt", i, horizon]),
    }


def analyze(rows, cutoff=None, **kwargs):
    if cutoff is None:
        cutoff = T0 + 100 * 300000
    return select_recent_nonoverlap(
        rows, training_cutoff_ms=cutoff, dataset_sha256=SHA, **kwargs
    )


class TemporalRowStudyTests(unittest.TestCase):
    def test_17_realistic_disjoint_anchors(self):
        report, selected = analyze([row(i) for i in range(17)])
        self.assertEqual(17, report["selected_rows"])
        self.assertTrue(report["has_minimum_temporal_coverage"])
        self.assertEqual(17, len(selected))
        self.assertTrue(all(
            b["anchor_ms"] - a["anchor_ms"] >= 300000
            for a, b in zip(selected, selected[1:])
        ))
        self.assertFalse(report["training_started"])
        self.assertFalse(report["trading_authority"])

    def test_recency_budget_selects_last_16_without_label_selection(self):
        report, selected = analyze(
            [row(i) for i in range(40)], maximum_rows=16
        )
        self.assertEqual(list(range(24, 40)),
                         [(r["anchor_ms"] - T0) // 300000 for r in selected])
        self.assertEqual(16, report["selected_rows"])

    def test_densely_overlapping_32_rows_are_not_32_windows(self):
        report, selected = analyze(
            [row(i, step_ms=10000) for i in range(32)]
        )
        self.assertEqual(2, len(selected))
        self.assertFalse(report["has_minimum_temporal_coverage"])
        self.assertEqual(
            "INSUFFICIENT_NONOVERLAPPING_WINDOWS_NO_TRAINING",
            report["state"],
        )

    def test_selection_is_permutation_invariant(self):
        population = [row(i) for i in range(19)]
        a, _ = analyze(population)
        b, _ = analyze(population[::-1])
        self.assertEqual(a, b)

    def test_selection_is_target_blind(self):
        population = [row(i) for i in range(19)]
        changed = [dict(r, target_class=(r["target_class"] + 1) % 7)
                   for r in population]
        a, _ = analyze(population)
        b, _ = analyze(changed)
        self.assertEqual(a["selected_row_ids"], b["selected_row_ids"])
        self.assertEqual(a["population_row_ids_digest"],
                         b["population_row_ids_digest"])
        self.assertNotEqual(a["selected_target_class_counts_diagnostic_only"],
                            b["selected_target_class_counts_diagnostic_only"])

    def test_900_second_rows_cannot_fill_300_second_quota(self):
        population = [row(i, horizon=900) for i in range(40)]
        report, selected = analyze(population)
        self.assertEqual([], selected)
        self.assertFalse(report["has_minimum_temporal_coverage"])
        self.assertEqual(0, report["eligible_five_minute_rows"])

    def test_cutoff_is_checked_with_full_future_horizon(self):
        with self.assertRaises(ContractError):
            analyze([row(0)], cutoff=T0 + 299999)

    def test_duplicate_anchor_even_with_new_identity_refused(self):
        with self.assertRaises(ContractError):
            analyze([row(0), row(1, step_ms=0)])

    def test_duplicate_row_refused(self):
        with self.assertRaises(ContractError):
            analyze([row(0), row(0)])

    def test_invalid_true_label_refused(self):
        with self.assertRaises(ContractError):
            analyze([row(0, target=7)])

    def test_false_receipt_is_not_a_valid_digest(self):
        r = row(0)
        r["outcome_receipt_digest"] = "unverified"
        with self.assertRaises(ContractError):
            analyze([r])

    def test_bad_source_identity_is_refused(self):
        with self.assertRaises(ContractError):
            select_recent_nonoverlap(
                [row(0)], training_cutoff_ms=T0 + 300000,
                dataset_sha256="not-a-real-digest"
            )

    def test_boolean_clock_is_not_integer(self):
        with self.assertRaises(ContractError):
            select_recent_nonoverlap(
                [row(0)], training_cutoff_ms=True,
                dataset_sha256=SHA
            )

    def test_more_than_4096_rows_refused_before_scan(self):
        with self.assertRaises(ContractError):
            analyze([row(0)] * 4097)

    def test_invalid_minimum_budget_refused(self):
        with self.assertRaises(ContractError):
            analyze([row(i) for i in range(20)], minimum_rows=15)

    def test_research_does_not_claim_proven_independence(self):
        report, _ = analyze([row(i) for i in range(20)])
        self.assertFalse(report["statistical_independence_claim"])
        self.assertFalse(report["model_promoted"])
        self.assertTrue(report["dataset_sha256_must_be_verified_by_caller"])


if __name__ == "__main__":
    unittest.main()
