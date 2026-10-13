"""Adversarial original PRICE outcomes for genuine presealed Numeric forecasts.

Test harness uses mocked original reader/native verifier. Its synthetic
outcomes are not real market observations; real Pocket4 smoke is separate.
"""
import copy
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_training.common import canonical, read, sha
from historical_training.native_price_forecast_outcome import (
    evaluate, _score, ASSUMED_STRESS_COST_MARGIN_BPS)
from laya_cell.integrity import ContractError, digest
from train_v2.original_multihorizon import ordered_quote_index

T = 1900000000000
PERIOD = "a"*64
MODEL = "b"*64
NATIVE = "c"*64


class NativePriceOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source_root = self.root / "original-quote-source"
        self.source_root.mkdir(mode=0o700)
        self.config = self.root/"config.json"
        self.config.write_bytes(b"{}\n")
        self.first = self.root/"slot-first"
        self.second = self.root/"slot-second"
        self.quotes = []
        for n in range(60):
            event = T+n*10000
            self.quotes.append((
                event+125, event, event+5000,
                digest({"source_quote":n}),
                Decimal("1.10000")-Decimal(n)*Decimal("0.000001")
            ))
        self.make_files(self.first,0,T)
        self.make_files(self.second,18,T+180000)
        self.archive={
            "rows":self.quotes,
            "sealed_segments":3,
            "source_fingerprint_sha256":"d"*64,
            "unsealed_newest_excluded":True,
        }
        self.mock_verify=patch(
            "historical_training.native_price_forecast_outcome.verify",
            return_value={
                "native_model_output_recomputed":True,
                "full_original_source_reverified":True})
        self.mock_read=patch(
            "historical_training.native_price_forecast_outcome.load_original_quote_archive",
            side_effect=lambda root,period:self.archive)
        self.mock_verify.start()
        self.mock_read.start()
        self.addCleanup(self.mock_verify.stop)
        self.addCleanup(self.mock_read.stop)

    @staticmethod
    def store(path,obj):
        path.write_bytes(canonical(obj)+b"\n")

    def make_files(self,root,index,start):
        root.mkdir(mode=0o700)
        q=self.quotes[index]
        plan={
            "plan_id":digest({"start":start}),"slot_start_ms":start,
            "registered_at_ms":start-100000,
            "window_end_ms":start+16000,
            "target_horizon_sec":300,
            "original_period_sha256":PERIOD,
            "original_feed_directory":str(self.source_root),
            "trade_authority":False,
            "canonical_agentd_owner_enrolled":False,
            "broker_orders_sent":0,
        }
        inp={
            "plan_id":plan["plan_id"],
            "quote_receipt":q[3],
            "quote_known_at_ms":q[0],
            "quote_event_at_ms":q[1],
            "source_asof_ms":q[0]+5,
            "feature_decision_ms":q[0]+15,
            "feature_state":{"reference_mid":str(q[4])},
        }
        pred={
            "plan_id":plan["plan_id"],
            "anchor_ms":q[1],
            "sample_id":digest({"sample":index}),
            "baseline_model_sha256":MODEL,
            "native_binary_sha256":NATIVE,
            "native_source_sha":"f"*64,
            "target_horizon_sec":300,
            "new_prospective_numeric_prediction":True,
            "laya_model_inference":False,
            "automatic_trading":False,
            "prediction_ready_at_ms":q[0]+20,
            "frozen_numeric_model_expected_mid_bps":0.66,
        }
        self.store(root/"plan.json",plan)
        self.store(root/"input.json",inp)
        pred["source_input_sha256"]=sha(root/"input.json")
        self.store(root/"prediction.json",pred)
        ack={
            "plan_id":plan["plan_id"],
            "post_fsync_at_ms":q[0]+30,
            "input_sha256":sha(root/"input.json"),
            "prediction_sha256":sha(root/"prediction.json"),
            "broker_orders_sent":0
        }
        self.store(root/"commit.json",ack)

    def modify(self,root,file,key,value):
        path=root/file
        data=read(path)
        data[key]=value
        self.store(path,data)
        if file=="input.json":
            p=read(root/"prediction.json")
            p["source_input_sha256"]=sha(path)
            self.store(root/"prediction.json",p)
        if file in ("input.json","prediction.json"):
            ack=read(root/"commit.json")
            ack["input_sha256"]=sha(root/"input.json")
            ack["prediction_sha256"]=sha(root/"prediction.json")
            self.store(root/"commit.json",ack)

    def check(self):
        return evaluate(self.first,self.second,self.config,self.source_root)

    def test_two_original_price_outcomes_are_not_trading_returns(self):
        value=self.check()
        self.assertEqual(value["state"],"ALL_ORIGINAL_PRICE_OUTCOMES_TERMINAL")
        self.assertEqual(value["observed_original_paper_price_outcomes"],2)
        self.assertEqual(value["statistically_independent_trading_samples"],0)
        self.assertTrue(value["price_forecast_horizons_overlap"])
        self.assertTrue(value["all_price_movements_below_hypothetical_hurdle"])
        self.assertTrue(all(x["price_direction_match"] is False
                            for x in value["windows"]))
        self.assertFalse(value["after_cost_alpha_qualified"])
        self.assertFalse(value["model_promotion_permitted"])
        self.assertEqual(value["broker_orders_sent"],0)

    def test_idempotent_readonly_scoring(self):
        self.assertEqual(self.check(),self.check())
        self.assertEqual(self.check()["audit_id"],
            digest({k:v for k,v in self.check().items() if k!="audit_id"}))

    def test_unsealed_outcome_stays_wait_not_zero(self):
        self.archive["rows"]=self.quotes[:33]
        result=self.check()
        self.assertEqual(result["state"],"WAIT_ORIGINAL_SEALED_SOURCE_DO_NOT_SCORE_MISSING")
        self.assertEqual(result["observed_original_paper_price_outcomes"],1)
        self.assertIsNone(result["windows"][1]["original_price_change_bps"])

    def test_missing_endpoint_is_censored_not_zero_pnl(self):
        self.archive["rows"]=[q for q in self.quotes if not
            T+180000 < q[1] < T+330000]
        result=self.check()
        self.assertTrue(result["censored_original_paper_outcomes"]>=1)
        self.assertFalse(result["after_cost_alpha_qualified"])

    def test_same_presealed_model_or_source_not_double_counted(self):
        self.modify(self.second,"plan.json","plan_id",
                    read(self.first/"plan.json")["plan_id"])
        with self.assertRaisesRegex(ContractError,
                                    "PLAN_IDENTITY"):
            self.check()

    def test_wrong_frozen_model_hash_rejected(self):
        self.modify(self.second,"prediction.json","baseline_model_sha256","x"*64)
        with self.assertRaisesRegex(ContractError,"DISTINCT_SLOTS_MODEL_PERIOD"):
            self.check()

    def test_wrong_predecision_fsync_rejected(self):
        self.modify(self.first,"commit.json","post_fsync_at_ms",T+20000)
        with self.assertRaisesRegex(ContractError,"PREDECLARED_TIMING"):
            self.check()

    def test_fake_gpu_laya_or_trading_authority_rejected(self):
        self.modify(self.first,"prediction.json","laya_model_inference",True)
        with self.assertRaisesRegex(ContractError,
                                   "NO_TRADING_OR_CANONICAL_AUTHORITY"):
            self.check()

    def test_missing_exact_original_quote_fails(self):
        self.modify(self.second,"input.json","quote_receipt","0"*64)
        with self.assertRaisesRegex(ContractError,
                                    "ORIGINAL_QUOTE_NOT_UNIQUE"):
            self.check()

    def test_changed_original_input_price_refused(self):
        self.modify(self.second,"input.json","feature_state",
                    {"reference_mid":"1.777"})
        with self.assertRaisesRegex(ContractError,
                                    "REFERENCE_PRICE_CHANGED"):
            self.check()

    def test_unverified_native_model_refused(self):
        with patch("historical_training.native_price_forecast_outcome.verify",
                   return_value={"native_model_output_recomputed":False,
                     "full_original_source_reverified":False}):
            with self.assertRaisesRegex(ContractError,
                                        "ORIGINAL_MODEL_NOT_REPRODUCED"):
                self.check()

    def test_extreme_finite_model_prediction_refused(self):
        self.modify(self.first,"prediction.json",
                    "frozen_numeric_model_expected_mid_bps",2e6)
        with self.assertRaisesRegex(
                ContractError, "NATIVE_OUTCOME_PREDICTION_INVALID"):
            self.check()

    def test_bounded_native_fp_roundoff_remains_explicit_product_hold(self):
        import math
        stored=read(self.first/"prediction.json")[
            "frozen_numeric_model_expected_mid_bps"]
        self.modify(self.first,"input.json","sample",{"native_episode":[]})
        actual=stored+math.ulp(stored)
        original=read(self.first/"prediction.json")
        with patch("historical_training.native_price_forecast_outcome.verify",
                   side_effect=[ContractError("NATIVE_FUTURE_MODEL_OUTPUT_MISMATCH"),
                                {"native_model_output_recomputed":True,
                                 "full_original_source_reverified":True}]), \
             patch("historical_training.native_price_forecast_outcome._plan",
                   return_value=read(self.first/"plan.json")), \
             patch("historical_training.native_price_forecast_outcome._config",
                   return_value={}), \
             patch("historical_training.native_price_forecast_outcome.predict_point",
                   return_value=(actual, original["native_source_sha"])):
            out=self.check()
        self.assertEqual(out["state"],
            "PRICE_OUTCOMES_OBSERVED_NATIVE_EXACT_REPLAY_HOLD")
        self.assertFalse(out["all_original_strict_native_bitwise_replays_passed"])
        self.assertEqual(out["observed_original_paper_price_outcomes"],2)
        self.assertFalse(out["after_cost_alpha_qualified"])

    def test_large_native_fp_mismatch_is_not_forgiven(self):
        import math
        stored=read(self.first/"prediction.json")[
            "frozen_numeric_model_expected_mid_bps"]
        self.modify(self.first,"input.json","sample",{"native_episode":[]})
        original=read(self.first/"prediction.json")
        with patch("historical_training.native_price_forecast_outcome.verify",
                   side_effect=ContractError("NATIVE_FUTURE_MODEL_OUTPUT_MISMATCH")), \
             patch("historical_training.native_price_forecast_outcome._plan",
                   return_value=read(self.first/"plan.json")), \
             patch("historical_training.native_price_forecast_outcome._config",
                   return_value={}), \
             patch("historical_training.native_price_forecast_outcome.predict_point",
                   return_value=(stored+10*math.ulp(stored),
                                 original["native_source_sha"])):
            with self.assertRaisesRegex(
                    ContractError, "NATIVE_OUTCOME_NOT_A_BOUNDED_FLOAT_DIAGNOSTIC"):
                self.check()

    def test_no_real_broker_execution_claim_from_hindsight(self):
        value=self.check()
        self.assertFalse(value["actual_broker_fills_attested"])
        self.assertFalse(value["actual_executed_net_profit_qualified"])
        self.assertEqual(value["hypothetical_stress_cost_plus_margin_bps"],
                         str(ASSUMED_STRESS_COST_MARGIN_BPS))
