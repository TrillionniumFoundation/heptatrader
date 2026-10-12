"""Immutable price-only 3x4 closure: never a 12-trade PnL claim."""
import copy
from decimal import Decimal
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from train_v2.contracts import ContractError, digest
from train_v2.price_only_cohort_audit import SCHEMA as AUDIT_SCHEMA
from train_v2.price_cohort_closeout import (
    OBSERVED, CENSORED, closeout, verify_final_price_audit,
)


def complete():
    # Entirely synthetic original PRICE audit for pure closeout unit testing.
    decisions = [
        {
            "slot": i,
            "source_decision_at_ms": 1800000000000 + 4800000*i,
            "preoutcome_input_id": digest({"input": i}),
            "original_source_quote_id": digest({"quote": i}),
        }
        for i in range(3)
    ]
    prices = [
        "0.2688786416", "1.7028980636", "3.0921043787", "0.5377572832",
        "0.1792251201", "-0.5376753605", "-1.9266700420", "-3.3604710036",
        "-0.1344483583", "-0.4033450751", "-2.5097026898", "-1.7478286590",
    ]
    slots = []
    for i, d in enumerate(decisions):
        slots.append({
            "slot": i,
            "slot_state": "ORIGINAL_PREOUTCOME_INPUT_SEALED",
            "original_input_id": d["preoutcome_input_id"],
            "original_source_quote_id": d["original_source_quote_id"],
            "outcomes": [
                {
                    "horizon_min": h,
                    "state": OBSERVED,
                    "price_bps": prices[4*i+j],
                    "economically_qualified": False,
                }
                for j, h in enumerate((5, 15, 30, 60))
            ]
        })
    report = {
        "schema": AUDIT_SCHEMA,
        "campaign_id": "a"*64,
        "roster_id": "b"*64,
        "campaign_sha256": "c"*64,
        "roster_sha256": "d"*64,
        "original_sealed_archive_fingerprint_sha256": "e"*64,
        "declared_source_slots": 3,
        "declared_source_horizon_cells": 12,
        "declared_original_decision_slots": 3,
        "captured_original_decision_slots": 3,
        "horizons_min": [5, 15, 30, 60],
        "distinct_original_decision_receipts": decisions,
        "all_slots": slots,
        "state_counts": {OBSERVED: 12},
        "sealed_original_price_horizon_labels": 12,
        "fixed_frozen_stressed_cost_plus_margin_bps": "10.9133557",
        "horizons_share_one_original_decision_per_slot": True,
        "horizon_labels_are_independent_economic_trades": False,
        "independently_executable_trading_samples": 0,
        "actual_trading_reward_samples": 0,
        "original_model_forecast_count": 0,
        "signed_independent_observer_count": 0,
        "executed_strategy_trades_count": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
        "financial_alpha_qualified": False,
        "economic_generalization_qualified": False,
        "automatic_model_selection": False,
    }
    report["audit_id"] = digest(report)
    return report


def rehash(report):
    report["audit_id"] = digest({
        key: value for key, value in report.items() if key != "audit_id"})
    return report


class CloseoutTests(TestCase):
    def test_all_twelve_price_cells_close_without_trade_or_promotion(self):
        result = verify_final_price_audit(complete())
        self.assertEqual(result["terminal_state"], "ALL_ORIGINAL_PRICES_OBSERVED")
        self.assertEqual(result["verified_distinct_original_source_decisions"], 3)
        self.assertEqual(result["verified_original_horizon_labels"], 12)
        self.assertEqual(result["observed_price_labels"], 12)
        self.assertEqual(result["largest_abs_hindsight_midprice_move_bps"],
                         "3.3604710036")
        self.assertEqual(result["hindsight_midprice_cells_at_or_above_stress_hurdle"], 0)
        self.assertFalse(result["hindsight_midprice_comparison_is_executable_pnl"])
        self.assertFalse(result["economic_alpha_qualified"])
        self.assertFalse(result["automatic_model_promotion"])
        self.assertEqual(result["actual_broker_fill_profit_samples"], 0)
        self.assertEqual(result["broker_orders_sent"], 0)
        self.assertEqual(result["closeout_id"],
                         digest({k:v for k,v in result.items() if k != "closeout_id"}))

    def test_replay_produces_identical_digest(self):
        self.assertEqual(verify_final_price_audit(complete()),
                         verify_final_price_audit(complete()))

    def test_unverified_audit_hash_rejected(self):
        record = complete()
        record["all_slots"][0]["outcomes"][0]["price_bps"] = "100000"
        with self.assertRaisesRegex(ContractError, "CLOSEOUT_AUDIT_IDENTITY"):
            verify_final_price_audit(record)

    def test_rehashed_future_not_yet_sealed_is_never_terminal(self):
        record = complete()
        record["all_slots"][2]["outcomes"][3]["state"] = "READY_ONLY_NOT_YET_SEALED"
        with self.assertRaisesRegex(
                ContractError, "CLOSEOUT_NOT_ALL_ORIGINAL_LABELS_FINAL"):
            verify_final_price_audit(rehash(record))

    def test_missed_and_incomplete_source_denominator_cannot_close(self):
        record = complete()
        record["captured_original_decision_slots"] = 2
        with self.assertRaisesRegex(
                ContractError, "CLOSEOUT_FROZEN_SOURCE_DENOMINATOR"):
            verify_final_price_audit(rehash(record))

    def test_four_nested_labels_cannot_become_twelve_trade_rewards(self):
        for key in ("actual_trading_reward_samples",
                    "independently_executable_trading_samples",
                    "original_model_forecast_count"):
            report = complete()
            report[key] = 12
            with self.subTest(key=key), self.assertRaisesRegex(
                    ContractError, "CLOSEOUT_FINANCIAL_AUTHORITY_NOT_ALLOWED"):
                verify_final_price_audit(rehash(report))

    def test_fake_model_or_trade_promotion_denied(self):
        for flag in ("financial_alpha_qualified",
                     "economic_generalization_qualified",
                     "automatic_model_selection", "trade_authority"):
            report=complete()
            report[flag] = True
            with self.subTest(flag=flag), self.assertRaisesRegex(
                    ContractError, "CLOSEOUT_FINANCIAL_AUTHORITY_NOT_ALLOWED"):
                verify_final_price_audit(rehash(report))

    def test_censored_maturity_must_remain_null_not_zero(self):
        report = complete()
        item = report["all_slots"][0]["outcomes"][3]
        item["state"] = CENSORED
        report["state_counts"] = {OBSERVED: 11, CENSORED: 1}
        with self.assertRaisesRegex(ContractError, "CLOSEOUT_CENSORED_NOT_ZERO"):
            verify_final_price_audit(rehash(report))
        item["price_bps"] = None
        result = verify_final_price_audit(rehash(report))
        self.assertEqual(result["terminal_state"],
                         "ORIGINAL_COHORT_CLOSED_WITH_CENSORING")
        self.assertEqual(result["explicitly_censored_price_labels"], 1)
        self.assertFalse(result["economic_alpha_qualified"])

    def test_missing_label_cannot_be_filled_with_zero(self):
        report = complete()
        report["all_slots"][1]["outcomes"][1]["price_bps"] = None
        with self.assertRaisesRegex(ContractError, "CLOSEOUT_PRICE_INVALID"):
            verify_final_price_audit(rehash(report))

    def test_nonfinite_price_or_hurdle_denied(self):
        for field, value in (("price_bps", "NaN"), ("price_bps", True)):
            report=complete()
            report["all_slots"][0]["outcomes"][0][field]=value
            with self.assertRaisesRegex(ContractError, "CLOSEOUT_PRICE_INVALID"):
                verify_final_price_audit(rehash(report))
        report=complete()
        report["fixed_frozen_stressed_cost_plus_margin_bps"]="Infinity"
        with self.assertRaisesRegex(ContractError, "CLOSEOUT_PRICE_INVALID"):
            verify_final_price_audit(rehash(report))

    def test_rehashed_source_identity_duplicate_denied(self):
        report=complete()
        report["distinct_original_decision_receipts"][1][
            "original_source_quote_id"] = report[
                "distinct_original_decision_receipts"][0]["original_source_quote_id"]
        report["all_slots"][1]["original_source_quote_id"] = report[
            "distinct_original_decision_receipts"][0]["original_source_quote_id"]
        with self.assertRaisesRegex(ContractError,
                                    "CLOSEOUT_SOURCE_DECISIONS_NOT_DISTINCT"):
            verify_final_price_audit(rehash(report))

    def test_rehashed_mismatched_outcome_count_rejected(self):
        report=complete()
        report["state_counts"]={OBSERVED: 11}
        with self.assertRaisesRegex(ContractError,
                                    "CLOSEOUT_FINAL_STATUS_MISMATCH"):
            verify_final_price_audit(rehash(report))

    def test_even_large_hindsight_move_is_not_financial_alpha(self):
        report=complete()
        report["all_slots"][0]["outcomes"][0]["price_bps"]="12.000000"
        report["all_slots"][0]["outcomes"][0]["observed_price_below_stress_hurdle"]=False
        result=verify_final_price_audit(rehash(report))
        self.assertEqual(result["hindsight_midprice_cells_at_or_above_stress_hurdle"],1)
        self.assertFalse(result["economic_alpha_qualified"])
        self.assertFalse(result["hindsight_midprice_comparison_is_executable_pnl"])

    def test_closeout_reconstructs_from_original_source_not_submitted_json(self):
        report=complete()
        with patch("train_v2.price_cohort_closeout.audit", return_value=report) as original:
            received = closeout(Path("/fixture/source"))
            original.assert_called_once_with(Path("/fixture/source"))
        self.assertEqual(received["verified_original_horizon_labels"],12)
        with patch("train_v2.price_cohort_closeout.audit",
                   side_effect=ContractError("ORIGINAL_HASH_CHAIN_CHANGED")):
            with self.assertRaisesRegex(ContractError, "ORIGINAL_HASH_CHAIN_CHANGED"):
                closeout(Path("/fixture/source"))
