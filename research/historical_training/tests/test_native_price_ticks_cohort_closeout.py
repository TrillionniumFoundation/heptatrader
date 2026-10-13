"""Phase 5: independent two-forecast original SEALED price closure.

No broker, GPU or trained model in tests; these are audit-logic fixtures.
"""
import copy
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_training import native_price_ticks_cohort_closeout as cohort
from laya_cell.integrity import ContractError

def fixture():
    forecasts=[]
    outcomes=[]
    for i,change in enumerate(("-0.2","0.15")):
        plan_id=("a" if i==0 else "b")*64
        price_receipt=("c" if i==0 else "d")*64
        score_id=("e" if i==0 else "f")*64
        at=1791868492542+i*370000
        predicted="0.73047167" if i==0 else "0.73336015"
        forecast={
            "plan_id":plan_id,
            "root":"/tmp/ticks"+str(i),
            "original_quote_receipt":price_receipt,
            "original_quote_event_at_ms":at,
            "original_quote_available_at_ms":at+300,
            "prediction_fsynced_at_ms":at+1400,
            "frozen_numeric_model_sha256":"1"*64,
            "score_id":score_id,
            "forecast_mid_bps":predicted,
        }
        outcome={
            "schema":cohort.EXPECTED_OUTCOME_SCHEMA,
            "plan_id":plan_id,
            "source_quote_receipt":price_receipt,
            "source_event_at_ms":at,
            "original_quote_known_at_ms":at+300,
            "prediction_fsynced_at_ms":at+1400,
            "frozen_model_sha256":"1"*64,
            "model_score_id":score_id,
            "model_score_fixed_bps":predicted,
            "original_price_due_at_ms":at+300000,
            "native_score_new_profile_replayed":True,
            "native_f64_exact_replay_attested":False,
            "broker_orders_sent":0,
            "actual_broker_fills":0,
            "actual_executed_net_pnl_samples":0,
            "model_promotion_permitted":False,
            "automatic_trading":False,
            "canonical_agentd_owner_enrolled":False,
            "original_price_only_not_executable_fill":True,
            "original_source_fingerprint_sha256":"6"*64,
            "original_source_sealed_segments":100,
            "active_unsealed_segment_excluded":True,
            "state":cohort.OBSERVED,
            "original_price_change_bps":change,
            "price_direction_match":i==1,
            "original_source_sealed_price_observed":True,
            "absolute_price_move_clears_prior_hypothetical_cost":False,
            "audit_id":("7" if i==0 else "8")*64,
        }
        forecasts.append(forecast)
        outcomes.append(outcome)
    preview={
        "schema":cohort.EXPECTED_FORECAST_SCHEMA,
        "audit_id":"9"*64,
        "distinct_original_source_decisions":2,
        "native_future_tick_score_replays":2,
        "source_price_horizons_disjoint":True,
        "broker_orders_sent":0,
        "actual_broker_execution_net_pnl_samples":0,
        "after_cost_alpha_qualified":False,
        "forecasts":forecasts,
    }
    return preview,outcomes


class SealedPriceCohortTests(unittest.TestCase):
    def setUp(self):
        self.plan,self.outcomes=fixture()
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir=Path(temp.name)

    def scan(self):
        def source(root,config,origin):
            return copy.deepcopy(self.outcomes[0 if root.name=="first" else 1])
        with patch.object(cohort,"two_original_windows",
                          return_value=copy.deepcopy(self.plan)), \
             patch.object(cohort,"independent_outcome",side_effect=source):
            return cohort.audit_pair(self.dir/"first",self.dir/"second",
                                     self.dir/"config.json",self.dir/"sources")

    def wait(self,number,kind):
        o=self.outcomes[number]
        o["state"]=kind
        o["original_price_change_bps"]=None
        o["price_direction_match"]=None
        o["original_source_sealed_price_observed"]=False
        o["absolute_price_move_clears_prior_hypothetical_cost"]=None

    def test_two_realized_sealed_prices_not_broker_pnl(self):
        r=self.scan()
        self.assertEqual(r["state"],"TWO_SEALED_PRICE_OUTCOMES_ONLY_NO_ALPHA")
        self.assertEqual(r["mature_sealed_price_outcomes"],2)
        self.assertEqual(r["observed_price_direction_matches"],1)
        self.assertEqual(r["observed_price_direction_mismatches"],1)
        self.assertEqual(r["observed_price_moves_below_prior_assumed_cost"],2)
        self.assertEqual(r["prior_assumed_cost_plus_margin_bps"],"10.9133557")
        self.assertFalse(r["statistical_alpha_qualified"])
        self.assertFalse(r["model_promotion_permitted"])
        self.assertEqual(r["actual_net_pnl_trade_samples"],0)
        self.assertEqual(r["per_forecast"][0]["sealed_price_change_bps"],"-0.2")
        self.assertEqual(r["per_forecast"][1]["sealed_price_change_bps"],"0.15")

    def test_unsealed_input_waits_without_zero_label(self):
        self.wait(0,"WAIT_ORIGINAL_SEALED_INPUT_COVERAGE")
        result=self.scan()
        self.assertEqual(result["state"],"WAIT_NATURAL_ORIGINAL_SOURCE_SEAL")
        self.assertEqual(result["mature_sealed_price_outcomes"],1)
        self.assertEqual(result["awaiting_natural_seal_or_endpoint"],1)
        self.assertIsNone(result["per_forecast"][0]["sealed_price_change_bps"])

    def test_missing_future_endpoint_waits(self):
        self.wait(1,"WAIT_ORIGINAL_SEALED_PRICE_COVERAGE")
        result=self.scan()
        self.assertEqual(result["state"],"WAIT_NATURAL_ORIGINAL_SOURCE_SEAL")
        self.assertEqual(result["awaiting_natural_seal_or_endpoint"],1)

    def test_censored_is_not_a_direction_loss_or_zero_return(self):
        self.wait(1,"CENSORED_ENDPOINT_NOT_OBSERVED")
        r=self.scan()
        self.assertEqual(r["state"],"CENSORED_PRICE_COHORT_NO_EFFICACY_INFERENCE")
        self.assertEqual(r["censored_price_outcomes"],1)
        self.assertEqual(r["mature_sealed_price_outcomes"],1)
        self.assertIsNone(r["per_forecast"][1]["direction_match"])

    def test_mismatched_quote_receipt_rejected(self):
        self.outcomes[1]["source_quote_receipt"]="0"*64
        with self.assertRaisesRegex(ContractError,"FROZEN_PREDICTION_OUTCOME_CHANGED"):
            self.scan()

    def test_mismatched_frozen_score_rejected(self):
        self.outcomes[0]["model_score_fixed_bps"]="1.73047167"
        with self.assertRaisesRegex(ContractError,"FROZEN_PREDICTION_OUTCOME_CHANGED"):
            self.scan()

    def test_mismatched_future_due_time_rejected(self):
        self.outcomes[1]["original_price_due_at_ms"]+=5000
        with self.assertRaisesRegex(ContractError,"FROZEN_PREDICTION_OUTCOME_CHANGED"):
            self.scan()

    def test_mixed_original_archive_frontiers_denied(self):
        self.outcomes[1]["original_source_fingerprint_sha256"]="0"*64
        with self.assertRaisesRegex(ContractError,"SOURCE_FRONTIER_CHANGED"):
            self.scan()

    def test_mixed_original_archive_seal_count_denied(self):
        self.outcomes[1]["original_source_sealed_segments"]+=1
        with self.assertRaisesRegex(ContractError,"SOURCE_FRONTIER_CHANGED"):
            self.scan()

    def test_duplicated_plan_receipts_rejected(self):
        self.outcomes[1]["plan_id"]=self.outcomes[0]["plan_id"]
        with self.assertRaisesRegex(ContractError,"DUPLICATE_PLAN_OR_OUTCOME"):
            self.scan()

    def test_hypothetical_cost_flag_must_match_original_price(self):
        self.outcomes[0]["absolute_price_move_clears_prior_hypothetical_cost"]=True
        with self.assertRaisesRegex(ContractError,"HYPOTHETICAL_COST_FLAG_CHANGED"):
            self.scan()

    def test_reversed_argument_order_still_matches_original_plans(self):
        def source(root,config,origin):
            return copy.deepcopy(self.outcomes[0 if root.name=="first" else 1])
        with patch.object(cohort,"two_original_windows",
                          return_value=copy.deepcopy(self.plan)), \
             patch.object(cohort,"independent_outcome",side_effect=source):
            result=cohort.audit_pair(self.dir/"second",self.dir/"first",
                                     self.dir/"config.json",self.dir/"sources")
        self.assertEqual(result["mature_sealed_price_outcomes"],2)
        self.assertEqual(result["observed_price_direction_matches"],1)

    def test_fabricated_missed_endpoint_label_rejected(self):
        self.wait(1,"CENSORED_ENDPOINT_NOT_OBSERVED")
        self.outcomes[1]["original_price_change_bps"]="0"
        with self.assertRaisesRegex(ContractError,"MISSING_PRICE_WAS_FABRICATED"):
            self.scan()

    def test_broker_fill_or_promotion_flag_denied(self):
        for key,value in (("broker_orders_sent",1),
                          ("actual_broker_fills",1),
                          ("model_promotion_permitted",True)):
            with self.subTest(key=key):
                o=self.outcomes[0]
                original=o[key]
                o[key]=value
                with self.assertRaisesRegex(ContractError,
                       "FROZEN_PREDICTION_OUTCOME_CHANGED"):
                    self.scan()
                o[key]=original

    def test_direction_mismatch_rejected(self):
        self.outcomes[0]["price_direction_match"]=True
        with self.assertRaisesRegex(ContractError,"DIRECTION_MISMATCH"):
            self.scan()

    def test_unrecognized_label_state_denied(self):
        self.outcomes[0]["state"]="SUCCESSFUL_TRADING_PNL"
        with self.assertRaisesRegex(ContractError,"UNKNOWN_OR_SILENT_OUTCOME"):
            self.scan()

    def test_rewritten_preview_authority_claim_refused(self):
        self.plan["after_cost_alpha_qualified"]=True
        with self.assertRaisesRegex(ContractError,"FORECAST_COHORT_NOT_VERIFIED"):
            self.scan()

    def test_invalid_nonfinite_original_price_refused(self):
        self.outcomes[1]["original_price_change_bps"]="NaN"
        with self.assertRaisesRegex(ContractError,"NONFINITE_PRICE"):
            self.scan()
