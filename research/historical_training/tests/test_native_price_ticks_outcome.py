"""Source-sealed post-forecast price-only outcomes; never economic trades."""
from decimal import Decimal
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_training.common import canonical
from historical_training import native_price_ticks_outcome as outcome
from laya_cell.integrity import ContractError

ANCHOR = 1900000000000


class NewTickPriceOutcomeTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name) / "slot"
        self.root.mkdir(mode=0o700)
        self.original = Path(td.name) / "source"
        self.original.mkdir(mode=0o700)
        self.config = Path(td.name) / "config.json"
        self.config.write_bytes(b"{}")
        self.registered = ANCHOR - 120000
        self.known = ANCHOR + 300
        self.asof = ANCHOR + 400
        self.decided = ANCHOR + 600
        self.predicted = ANCHOR + 650
        self.sealed = ANCHOR + 660
        self.plan = {
            "plan_id": "p"*64,
            "registered_at_ms": self.registered,
            "window_end_ms": ANCHOR + 16000,
            "original_feed_directory": str(self.original),
            "original_period_sha256": "b"*64,
            "baseline_model_sha256": "m"*64,
        }
        self.input = {
            "plan_id": self.plan["plan_id"],
            "quote_event_at_ms": ANCHOR + 100,
            "quote_known_at_ms": self.known,
            "quote_receipt": "c"*64,
            "source_asof_ms": self.asof,
            "feature_decision_ms": self.decided,
            "feature_state": {"reference_mid": 1.10005},
        }
        self.pred = {
            "plan_id": self.plan["plan_id"],
            "anchor_ms": ANCHOR + 100,
            "source_decision_at_ms": self.decided,
            "prediction_ready_at_ms": self.predicted,
            "target_horizon_sec": 300,
            "future_fixed_score": {
                "scaled_bps_ticks": 73047167, "score_id": "s"*64},
        }
        self.ack = {
            "plan_id": self.plan["plan_id"],
            "post_fsync_at_ms": self.sealed,
            "broker_orders_sent": 0,
        }
        for name, data in (("input", self.input),
                           ("prediction", self.pred), ("commit", self.ack)):
            (self.root / (name+".json")).write_bytes(canonical(data)+b"\n")
        self.due = self.pred["anchor_ms"] + outcome.HORIZON_MS
        self.reference = (
            self.known, ANCHOR+100, "source", "c"*64, Decimal("1.10005"))
        self.index = {
            "known": [self.reference],
            "known_times": [self.due + 9000],
        }
        self.archive = {
            "rows": [{"read_only_test_fixture": True}],
            "sealed_segments": 10,
            "source_fingerprint_sha256": "f"*64,
            "unsealed_newest_excluded": True,
        }

    def audit(self, *, index=None, result=None, verified=None):
        good = {"new_profile_score_reproduced": True,
                "original_source_rechecked": True,
                "original_f64_bitwise_equal_attested": False,
                "broker_orders_sent": 0}
        if verified:
            good.update(verified)
        with patch.object(outcome, "verify", return_value=good), \
             patch.object(outcome, "_plan", return_value=self.plan), \
             patch.object(outcome, "load_original_quote_archive",
                          return_value=self.archive), \
             patch.object(outcome, "ordered_quote_index",
                          return_value=index if index is not None else self.index), \
             patch.object(outcome, "label_after",
                          return_value=result if result is not None else (
                              "OBSERVED_RETROSPECTIVE_PRICE_ONLY",
                              Decimal("-0.15"))):
            return outcome.assess(self.root, self.config, self.original)

    def test_price_label_is_not_actual_broker_pnl(self):
        result = self.audit()
        self.assertEqual(result["state"], "OBSERVED_ORIGINAL_PAPER_MID_PRICE_ONLY")
        self.assertFalse(result["price_direction_match"])
        self.assertFalse(result["absolute_price_move_clears_prior_hypothetical_cost"])
        self.assertEqual(result["model_score_fixed_bps"], "0.73047167")
        self.assertEqual(result["original_price_change_bps"], "-0.15")
        self.assertTrue(result["native_score_new_profile_replayed"])
        self.assertFalse(result["native_f64_exact_replay_attested"])
        self.assertFalse(result["model_promotion_permitted"])
        self.assertEqual(result["actual_executed_net_pnl_samples"], 0)
        self.assertEqual(result["broker_orders_sent"], 0)

    def test_unsealed_original_endpoint_must_wait(self):
        index = dict(self.index)
        index["known_times"] = [self.due + outcome.ENDPOINT_GRACE_MS - 1]
        result = self.audit(index=index)
        self.assertEqual(result["state"], "WAIT_ORIGINAL_SEALED_PRICE_COVERAGE")
        self.assertIsNone(result["original_price_change_bps"])
        self.assertIsNone(result["price_direction_match"])

    def test_sealed_missing_endpoint_remains_censored(self):
        result = self.audit(result=("CENSORED_ENDPOINT_NOT_OBSERVED", None))
        self.assertEqual(result["state"], "CENSORED_ENDPOINT_NOT_OBSERVED")
        self.assertIsNone(result["original_price_change_bps"])

    def test_wrong_original_price_reference_refused(self):
        index = dict(self.index)
        index["known"] = [(
            self.known, ANCHOR+100, "src", "c"*64, Decimal("1.20005"))]
        with self.assertRaisesRegex(ContractError,
                                    "NATIVE_TICKS_OUTCOME_REFERENCE_MID_CHANGED"):
            self.audit(index=index)

    def test_original_input_not_yet_sealed_must_wait_not_zero(self):
        index = dict(self.index)
        index["known"] = []
        result = self.audit(index=index)
        self.assertEqual(result["state"], "WAIT_ORIGINAL_SEALED_INPUT_COVERAGE")
        self.assertIsNone(result["original_price_change_bps"])
        self.assertEqual(result["actual_executed_net_pnl_samples"], 0)

    def test_duplicate_original_receipt_identity_refused(self):
        index = dict(self.index)
        index["known"] = [self.reference, self.reference]
        with self.assertRaisesRegex(ContractError,
                                    "NATIVE_TICKS_OUTCOME_ORIGINAL_RECEIPT_NOT_UNIQUE"):
            self.audit(index=index)

    def test_wrong_original_source_root_refused(self):
        with self.assertRaisesRegex(ContractError,
                                    "NATIVE_TICKS_OUTCOME_SOURCE_ROOT_CHANGED"):
            with patch.object(outcome, "verify", return_value={
                "new_profile_score_reproduced": True,
                "original_source_rechecked": True,
                "original_f64_bitwise_equal_attested": False,
                "broker_orders_sent": 0}), patch.object(outcome, "_plan",
                                               return_value=self.plan):
                outcome.assess(self.root, self.config, self.root)

    def test_unverified_native_score_never_scored(self):
        with self.assertRaisesRegex(ContractError,
                                    "NATIVE_TICKS_OUTCOME_VERIFIER_NOT_VALID"):
            self.audit(verified={"new_profile_score_reproduced": False})

    def test_f64_legacy_claim_never_scored(self):
        with self.assertRaisesRegex(ContractError,
                                    "NATIVE_TICKS_OUTCOME_VERIFIER_NOT_VALID"):
            self.audit(verified={"original_f64_bitwise_equal_attested": True})

    def test_price_move_above_hurdle_still_cannot_promote(self):
        result = self.audit(result=(
            "OBSERVED_RETROSPECTIVE_PRICE_ONLY", Decimal("12.2")))
        self.assertTrue(result["absolute_price_move_clears_prior_hypothetical_cost"])
        self.assertFalse(result["model_promotion_permitted"])
        self.assertEqual(result["actual_broker_fills"], 0)
