"""Future-only 1e-8 bps tick encoding does NOT retrofit old forecasts."""
import copy
from decimal import Decimal
import math
import unittest

from historical_training.native_score_ticks import (
    prepare_future_score, verify_future_score_replay, SCALE)
from laya_cell.integrity import ContractError

M="5db4629fe0798d618cf6f92dc4ce3b54bb5660c6594dddedb07f558d9a4d8c97"
B="0b1829dcec9cfebbf02c6bd392870a3c6bb9241a69d65e04823276fffff2f0e0"


class FutureScoreEncodingTests(unittest.TestCase):
    def make(self,x):
        return prepare_future_score(x,
             source_model_sha256=M,native_binary_sha256=B)

    def test_two_real_old_numbers_canonicalize_equivalently_for_future_only(self):
        vals = (
            (0.6625708695529248,0.6625708695529247),
            (0.6690629418227435,0.6690629418227438),
        )
        for stored,recomputed in vals:
            with self.subTest(stored=stored):
                record=self.make(stored)
                self.assertEqual(record,self.make(recomputed))
                self.assertEqual(record["scale_ticks_per_one_bps"],SCALE)
                self.assertFalse(record["existing_original_f64_prediction_rewritten"])
                result=verify_future_score_replay(record,recomputed)
                self.assertFalse(result["native_original_bitwise_f64_equal_attested"])
                self.assertTrue(result["not_a_frozen_legacy_prediction_requalification"])

    def test_nan_inf_booleans_and_strings_cannot_be_scores(self):
        for value in (float("inf"),float("-inf"),float("nan"),True,
                      False,None,"0.6625",0):
            with self.subTest(value=str(value)), self.assertRaises(ContractError):
                self.make(value)

    def test_out_of_range_float_rejected(self):
        for x in (-1000001.0,1000001.0):
            with self.assertRaisesRegex(ContractError,"NATIVE_SCORE_BOUND"):
                self.make(x)

    def test_half_tick_boundary_fails_closed(self):
        # Score precisely at a 0.5*1e-8 bps decision boundary.
        for value in (0.660000005,-0.660000005):
            with self.assertRaisesRegex(
                    ContractError,"NATIVE_SCORE_TICK_ROUNDING_AMBIGUOUS"):
                self.make(value)

    def test_value_far_from_tick_midpoint_can_round(self):
        row=self.make(0.6625708695529248)
        self.assertEqual(row["scaled_bps_ticks"],66257087)
        self.assertEqual(row["rounded_bps"],"0.66257087")
        r=self.make(-0.6690629418227435)
        self.assertEqual(r["scaled_bps_ticks"],-66906294)

    def test_significant_prediction_drift_cannot_be_accepted(self):
        record=self.make(0.6625708695529248)
        with self.assertRaisesRegex(ContractError,
                                    "NATIVE_SCORE_CANONICAL_REPLAY_MISMATCH"):
            verify_future_score_replay(record,0.6625711)

    def test_tamper_even_with_same_ticks_rejected(self):
        record=self.make(0.6625708695529248)
        altered=copy.deepcopy(record)
        altered["source_model_sha256"]="f"*64
        with self.assertRaisesRegex(ContractError,"NATIVE_SCORE_SEAL_CHANGED"):
            verify_future_score_replay(altered,0.6625708695529248)

    def test_false_model_selection_authority_rejected(self):
        record=self.make(0.6625708695529248)
        record["automatic_model_selection"]=True
        from laya_cell.integrity import digest
        record["score_id"]=digest({k:v for k,v in record.items() if k!="score_id"})
        with self.assertRaisesRegex(ContractError,"NATIVE_SCORE_NOT_AN_AUTHORITY_GRANT"):
            verify_future_score_replay(record,0.6625708695529248)

    def test_model_source_pin_enforced(self):
        with self.assertRaisesRegex(ContractError,"NATIVE_SCORE_ORIGINAL_MODEL_IDENTITY"):
            prepare_future_score(0.6,source_model_sha256="bad",
                                 native_binary_sha256=B)

    def test_codecs_are_identical_on_repeat(self):
        val=self.make(0.66)
        self.assertEqual(val,self.make(0.66))
        self.assertFalse(val["actual_broker_fill_net_pnl"])
        self.assertFalse(val["behavior_propensity_attested"])
