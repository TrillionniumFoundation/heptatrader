"""Batch cohort still delegates ALL labeling to frozen strict source auditor."""
from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path

from train_v2.contracts import ContractError, digest
from train_v2.paged_batch_cohort_replay import audit_frozen_batch

CAMPAIGN = "a"*64
PERIOD = "e"*64
SOURCE = "f"*64
OLD_AUDIT_SHA = "1"*64
OLD_CLOSEOUT_SHA = "2"*64


class BatchCohortTests(unittest.TestCase):
    def setUp(self):
        self.campaign = {"campaign_id": CAMPAIGN,
                         "source_root": "/original",
                         "period_sha256": PERIOD}
        self.roster = {"campaign_id": CAMPAIGN}
        self.old = {
            "campaign_id": CAMPAIGN,
            "original_sealed_segment_count": 269,
            "distinct_original_decision_receipts": [
                {"source_decision_at_ms": 10000000+slot*4800000,
                 "original_source_quote_id": str(slot)*64}
                for slot in range(3)
            ],
        }
        self.closeout = {
            "largest_abs_hindsight_midprice_move_bps": "3.360",
            "fixed_original_stress_cost_plus_margin_bps": "10.913",
            "hindsight_midprice_cells_at_or_above_stress_hurdle": 0,
            "verified_distinct_original_source_decisions": 3,
            "verified_original_horizon_labels": 12,
            "actual_broker_fill_profit_samples": 0,
            "automatic_model_promotion": False,
            "broker_orders_sent": 0,
        }
        self.bounds = [
            (10000000+slot*4800000-60000,
             10000000+slot*4800000+3600000+60000)
            for slot in range(3)]
        self.views = [
            {"window_from_ms": start, "window_until_ms": end,
             "source_fingerprint_sha256": SOURCE, "window_quote_count": 2}
            for start, end in self.bounds
        ]
        self.batch = {
            "full_original_scan_count": 1, "views": self.views,
            "sealed_source_fingerprint_sha256": SOURCE,
            "frozen_prefix_sha256": "b"*64,
            "verified_sealed_segments": 287,
            "selected_quotes": 6,
        }
        self.called = []

    def frozen(self, path, sha, schema):
        if "original-price-cohort-audit.v2" in schema:
            self.assertEqual(sha, OLD_AUDIT_SHA)
            return self.old
        self.assertEqual(sha, OLD_CLOSEOUT_SHA)
        return self.closeout

    def batcher(self, source, period, windows, *, frozen_prefix_segments=None):
        self.called.append(("batch", source, period, windows,
                            frozen_prefix_segments))
        return self.batch

    def strict(self, root, olda, oldah, oldc, oldch, *,
               now_ms=None, pager=None):
        for start, end in self.bounds:
            view = pager("/original", PERIOD, start, end,
                         frozen_prefix_segments=269)
            self.called.append(("pager", start, end))
            self.assertEqual((view["window_from_ms"],
                              view["window_until_ms"]), (start, end))
        return {
            "audit_id": "c"*64,
            "original_outcome_cells_reverified_exact": 12,
            "actual_original_decisions": 3,
            "new_complete_source_fingerprint_sha256": SOURCE,
            "frozen_original_audit_sha256": OLD_AUDIT_SHA,
            "frozen_original_closeout_sha256": OLD_CLOSEOUT_SHA,
            "broker_orders_sent": 0,
            "max_abs_hindsight_midprice_bps":
                self.closeout["largest_abs_hindsight_midprice_move_bps"],
            "original_cost_plus_margin_bps":
                self.closeout["fixed_original_stress_cost_plus_margin_bps"],
            "price_cells_above_original_cost_margin":
                self.closeout["hindsight_midprice_cells_at_or_above_stress_hurdle"],
        }

    def audit(self, strict=None):
        def identity(root, slot, campaign, roster):
            return (
                {"decision_at_ms": 10000000+slot*4800000,
                 "original_source_id": str(slot)*64},
                {"worker_finished_at_ms": 1}
            )
        with patch("train_v2.paged_batch_cohort_replay._load",
                   return_value=self.campaign):
            with patch("train_v2.paged_batch_cohort_replay.load_roster",
                       return_value=self.roster):
                with patch("train_v2.paged_batch_cohort_replay.pinned_receipt",
                           side_effect=self.frozen):
                    with patch("train_v2.paged_batch_cohort_replay.verify_input_and_timer",
                               side_effect=identity):
                        return audit_frozen_batch(
                            "/fixture", "/old_audit", OLD_AUDIT_SHA,
                            "/old_closeout", OLD_CLOSEOUT_SHA, now_ms=20000000,
                            batched=self.batcher,
                            original_audit=self.strict if strict is None else strict,
                        )

    def test_exactly_one_original_scan_but_all_three_strict_pager_calls(self):
        result = self.audit()
        self.assertEqual(len([x for x in self.called if x[0]=="batch"]), 1)
        self.assertEqual(len([x for x in self.called if x[0]=="pager"]), 3)
        self.assertEqual(result["source_full_scans"], 1)
        self.assertEqual(result["original_decisions"], 3)
        self.assertEqual(result["correlated_price_labels"], 12)
        self.assertEqual(result["cells_meeting_fixed_cost_hurdle"], 0)
        self.assertEqual(result["selected_price_quote_count"], 6)
        self.assertEqual(result["receipt_id"], digest({
            k:v for k,v in result.items() if k!="receipt_id"}))
        self.assertFalse(result["economic_alpha_qualified"])
        self.assertFalse(result["trade_authority"])

    def test_batch_must_supply_three_distinct_windows(self):
        self.batch["views"] = self.views[:2]
        with self.assertRaisesRegex(ContractError, "SINGLE_SOURCE_SCAN_REQUIRED"):
            self.audit()

    def test_batch_new_source_fingerprint_required(self):
        self.batch["sealed_source_fingerprint_sha256"] = "7"*64
        with self.assertRaisesRegex(ContractError, "ORIGINAL_AUDIT_DISAGREES"):
            self.audit()

    def test_batch_cannot_reuse_same_pager_window_to_inflate_count(self):
        def twice(*args, pager=None, **kwargs):
            self.strict(*args,pager=pager,**kwargs)
            a,b=self.bounds[0]
            pager("/original",PERIOD,a,b,frozen_prefix_segments=269)
        with self.assertRaisesRegex(ContractError, "PAGER_SCOPE_OR_REUSE"):
            self.audit(strict=twice)

    def test_original_immutable_price_economics_must_match(self):
        def forged(*args, **kwargs):
            value=self.strict(*args,**kwargs)
            value["max_abs_hindsight_midprice_bps"]="999"
            return value
        with self.assertRaisesRegex(ContractError, "ORIGINAL_ECONOMICS_WERE_CHANGED"):
            self.audit(strict=forged)

    def test_original_immutable_hurdle_and_zero_cells_are_not_optional(self):
        def forged(*args, **kwargs):
            value=self.strict(*args,**kwargs)
            value["price_cells_above_original_cost_margin"]=1
            return value
        with self.assertRaisesRegex(ContractError, "ORIGINAL_ECONOMICS_WERE_CHANGED"):
            self.audit(strict=forged)

    def test_original_closeout_broker_fill_claim_cannot_be_modified(self):
        self.closeout["actual_broker_fill_profit_samples"]=1
        with self.assertRaisesRegex(ContractError, "FROZEN_PRICE_NOT_FINANCIAL"):
            self.audit()

    def test_unverified_original_twelve_labels_cannot_pass(self):
        def fewer(*args, **kwargs):
            value=self.strict(*args,**kwargs)
            value["original_outcome_cells_reverified_exact"]=11
            return value
        with self.assertRaisesRegex(ContractError, "ORIGINAL_AUDIT_DISAGREES"):
            self.audit(strict=fewer)

    def test_original_wrong_source_decision_cannot_be_reused(self):
        self.old["distinct_original_decision_receipts"][0][
            "original_source_quote_id"] = "mismatch"
        with self.assertRaisesRegex(ContractError, "ORIGINAL_SOURCE_DECISION_CHANGED"):
            self.audit()
