"""Post-maturity price-only observer adversarial tests (no live broker)."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from train_v2.contracts import ContractError, digest
from train_v2.original_multihorizon import ordered_quote_index
from train_v2.prospective_horizon_observe import (
    observe, make_result, pending_reason,
)
import test_train_v2_prospective_multihorizon as fixture_module


class FuturePriceObservationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.FutureHorizonTests(methodName="test_zero_automatic_actions")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.frozen=self.fixture.capture()
        self.plan=self.fixture.plan
        self.root=self.fixture.campaign
        self.due=self.frozen["outcome_maturity_ms"]["5"]
        self.rows=self.synthetic_quotes()

    def synthetic_quotes(self):
        initial=self.frozen["source_event_at_ms"]
        mid=Decimal(self.frozen["input_quote_midpoint"])
        actual=self.frozen["original_source_id"]
        items=[]
        for offset in range(0, 310000, 1000):
            event=initial+offset
            ident=actual if offset==0 else digest({"after":event})
            price=mid + Decimal(offset//1000)*Decimal("0.00000001")
            available=(self.frozen["source_available_at_ms"]
                       if offset==0 else event+120)
            items.append((available,event,event+5000,ident,price))
        return items

    def archive(self, rows=None):
        return {
            "rows":self.rows if rows is None else rows,
            "source_fingerprint_sha256":"a"*64,
            "sealed_segments":3,
            "first_available_ms":self.rows[0][0],
            "last_available_ms":self.rows[-1][0],
        }

    def test_genuine_presealed_input_then_mature_original_price(self):
        index=ordered_quote_index(self.rows)
        r=make_result(self.plan,self.frozen,self.rows[0],5,index)
        self.assertEqual(r["outcome_state"],
                         "OBSERVED_NEW_ORIGINAL_MARKET_PRICE_ONLY")
        self.assertIsNotNone(r["original_midprice_change_bps"])
        self.assertFalse(r["actual_ib_fills_or_slippage_attested"])
        self.assertFalse(r["model_selection_permitted"])
        self.assertFalse(r["independent_observer_signed"])
        self.assertEqual(r["broker_orders_sent"],0)

    def test_early_evaluation_fails_before_any_source_scan(self):
        with self.assertRaisesRegex(ContractError,
                                    "PROSPECTIVE_OUTCOME_NOT_MATURE"):
            observe(self.root,0,5,now_ms=self.due+4999)

    def test_after_maturity_but_unsealed_input_remains_unknown(self):
        with patch("train_v2.prospective_horizon_observe.load_original_quote_archive",
                   return_value=self.archive(self.rows[1:])):
            r=observe(self.root,0,5,now_ms=self.due+6000)
        self.assertEqual(r["state"],"WAIT_INPUT_ORIGINAL_SEGMENT_SEAL")
        self.assertFalse(r["outcome_written"])
        self.assertFalse((self.root/"slot-00-h05.outcome.json").exists())

    def test_sealed_archive_not_extended_past_endpoint_is_pending(self):
        with patch("train_v2.prospective_horizon_observe.load_original_quote_archive",
                   return_value=self.archive(self.rows[:120])):
            r=observe(self.root,0,5,now_ms=self.due+6000)
        self.assertEqual(r["state"],"WAIT_END_OF_ORIGINAL_SEALED_COVERAGE")
        self.assertFalse((self.root/"slot-00-h05.outcome.json").exists())

    def test_mature_observation_written_once_and_replay_rejected(self):
        with patch("train_v2.prospective_horizon_observe.load_original_quote_archive",
                   return_value=self.archive()):
            first=observe(self.root,0,5,now_ms=self.due+6000)
            self.assertEqual(first["outcome_state"],
                             "OBSERVED_NEW_ORIGINAL_MARKET_PRICE_ONLY")
            with self.assertRaisesRegex(ContractError,"PROSPECTIVE_OUTCOME_ALREADY_FINAL"):
                observe(self.root,0,5,now_ms=self.due+7000)
        self.assertTrue((self.root/"slot-00-h05.outcome.json").is_file())

    def test_due_endpoint_missing_is_censored_not_zero(self):
        censored=[row for row in self.rows
                  if not self.due<=row[1]<=self.due+5000]
        with patch("train_v2.prospective_horizon_observe.load_original_quote_archive",
                   return_value=self.archive(censored)):
            r=observe(self.root,0,5,now_ms=self.due+7000)
        self.assertEqual(r["outcome_state"],"CENSORED_ENDPOINT_NOT_OBSERVED")
        self.assertIsNone(r["original_midprice_change_bps"])

    def test_tampered_preoutcome_source_quote_blocks_observer(self):
        corrupt=list(self.rows)
        first=list(corrupt[0]);first[4]=first[4]+Decimal("0.1")
        corrupt[0]=tuple(first)
        with patch("train_v2.prospective_horizon_observe.load_original_quote_archive",
                   return_value=self.archive(corrupt)):
            with self.assertRaisesRegex(ContractError,
                                      "PROSPECTIVE_OUTCOME_ORIGINAL_INPUT_CHANGED"):
                observe(self.root,0,5,now_ms=self.due+6000)

    def test_wrong_horizon_or_slot_refused(self):
        for horizon in (0,12,120,True):
            with self.subTest(horizon=horizon),self.assertRaises(ContractError):
                observe(self.root,0,horizon,now_ms=self.due+6000)
        with self.assertRaisesRegex(ContractError,
                                    "PROSPECTIVE_OUTCOME_SLOT_OR_HORIZON"):
            observe(self.root,99,5,now_ms=self.due+6000)

    def test_modified_frozen_feature_digest_refused(self):
        f=self.root/"slot-00.input.json"
        original=json.loads(f.read_text())
        original["feature_id"]="f"*64
        f.write_text(json.dumps(original))
        with self.assertRaisesRegex(ContractError,
                                  "PROSPECTIVE_ORIGINAL_PREOUTCOME_INPUT_CHANGED"):
            observe(self.root,0,5,now_ms=self.due+6000)

    def test_never_reinterpret_quote_as_a_broker_profit(self):
        self.assertEqual(pending_reason(original_covered=False,quote_in_archive=True),
                         "WAIT_END_OF_ORIGINAL_SEALED_COVERAGE")
        self.assertEqual(pending_reason(original_covered=True,quote_in_archive=False),
                         "WAIT_INPUT_ORIGINAL_SEGMENT_SEAL")
