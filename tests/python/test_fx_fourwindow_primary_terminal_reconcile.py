"""Hostile primary-finish manifest consistency tests for frozen 4x4 FX research."""
import copy
import unittest

from train_v2.fx_all_four_final_crosshost import (
    SCHEMA as PHYSICAL_SCHEMA, BEFORE_LAST_DUE_MS
)
from train_v2.fx_first_three_original_crosshost import (
    TRIAL, SEAL_SHA, HORIZONS, CrosshostHold
)
from train_v2.fx_fourwindow_primary_terminal_reconcile import (
    SCHEMA, validate_manifest,
)

def sample():
    early={"original_decisions_censored":3,
           "original_maturity_attempts_censored":12,
           "first_three":[]}
    physical={"schema":PHYSICAL_SCHEMA,
              "result":"COMPLETE_4X4_CENSORED_RESEARCH_PROVEN_NO_TRADE",
              "original_complete_matured_censored_count":16,
              "original_expected_maturity_count":16,
              "fourth_future_horizons_pending":[],
              "trade_authority":False,"model_promotion_allowed":False,
              "original_1005_decision_sha256":"d"*64,
              "original_1005_detailed_maturities":[]}
    manifest={"schema":TRIAL+".final.v1",
              "sealed_sha256":SEAL_SHA,
              "window_count":4,
              "completed_at_ms":BEFORE_LAST_DUE_MS-8000,
              "original_preaction_frozen_count":0,
              "mature_quote_only_samples":0,
              "all_historical_test_and_loss_facts_unchanged":True,
              "no_model_predictions_or_production_admission":True,
              "broker_orders_sent":0,"trading_strategy_changed":False,
              "model_promotion_allowed":False,
              "actual_execution_rewards":0,"windows":[]}
    for i in range(4):
        original_decision=chr(97+i)*64
        if i<3:
            entry={"original_decision_sha256":original_decision,
                   "horizon_outcomes":[]}
            early["first_three"].append(entry)
        else:
            original_decision="d"*64
        row={"index":i,"decision_state":"CENSORED",
             "decision_attempt_sha256":original_decision,
             "outcomes":[]}
        for j,h in enumerate(HORIZONS):
            # one unique SHA per genuine window/horizon, no duplicates
            hsh=f"{i+1:02x}{j+1:02x}"*16
            row["outcomes"].append({"horizon_minutes":h,
                                    "state":"CENSORED",
                                    "attempt_sha256":hsh})
            if i<3:
                early["first_three"][i]["horizon_outcomes"].append({
                    "horizon_minutes":h,
                    "original_maturity_attempt_sha256":hsh})
            else:
                physical["original_1005_detailed_maturities"].append({
                    "horizon_minutes":h,
                    "original_maturity_receipt_sha256":hsh})
        manifest["windows"].append(row)
    return manifest,early,physical

class TerminalSourceTests(unittest.TestCase):
    def test_full_final_identity_no_product_authority(self):
        m,e,p=sample()
        got=validate_manifest(m,e,p)
        self.assertTrue(got["all_sixteen_maturity_sha256s_reconciled"])
        self.assertTrue(got["all_four_entry_sha256s_reconciled"])
        self.assertTrue(got["primary_final_alone_not_independent_systemd_exit_proof"])
        self.assertFalse(got["trade_authority"])
        self.assertFalse(got["model_promotion_allowed"])
    def test_no_final_before_last_causal_event(self):
        m,e,p=sample();m["completed_at_ms"]=BEFORE_LAST_DUE_MS-8501
        with self.assertRaisesRegex(CrosshostHold,"PRIMARY_WORKER_FINAL_PREMATURE"):
            validate_manifest(m,e,p)
    def test_wrong_period_seal_denied(self):
        m,e,p=sample();m["sealed_sha256"]="f"*64
        with self.assertRaisesRegex(CrosshostHold,"PRIMARY_WORKER_FINAL_SCOPE"):
            validate_manifest(m,e,p)
    def test_forged_trade_reward_denied(self):
        m,e,p=sample();m["actual_execution_rewards"]=1
        with self.assertRaisesRegex(CrosshostHold,"PRIMARY_WORKER_FINAL_SCOPE"):
            validate_manifest(m,e,p)
    def test_extra_fifth_window_refused(self):
        m,e,p=sample();m["windows"].append(copy.deepcopy(m["windows"][0]))
        m["window_count"]=5
        with self.assertRaises(CrosshostHold):validate_manifest(m,e,p)
    def test_three_of_four_matures_refused(self):
        m,e,p=sample();m["windows"][3]["outcomes"].pop()
        with self.assertRaisesRegex(CrosshostHold,"PRIMARY_FOUR_MATURITIES_MISSING"):
            validate_manifest(m,e,p)
    def test_one_future_receipt_sha_mutation_refused(self):
        m,e,p=sample();m["windows"][3]["outcomes"][3]["attempt_sha256"]="e"*64
        with self.assertRaisesRegex(CrosshostHold,"PRIMARY_MATURE_RECEIPT"):
            validate_manifest(m,e,p)
    def test_one_original_decision_mutation_refused(self):
        m,e,p=sample();m["windows"][1]["decision_attempt_sha256"]="e"*64
        with self.assertRaisesRegex(CrosshostHold,"PRIMARY_DECISION_SHA"):
            validate_manifest(m,e,p)
    def test_future_study_partial_cannot_be_final(self):
        m,e,p=sample();p["result"]="PARTIAL_AWAIT_ORIGINAL_FUTURE_MATURITIES"
        p["original_complete_matured_censored_count"]=15
        with self.assertRaisesRegex(CrosshostHold,"INDEPENDENT_PHYSICAL_VERIFIER_NOT_COMPLETE"):
            validate_manifest(m,e,p)
    def test_first_three_missing_original_source_refused(self):
        m,e,p=sample();e["original_maturity_attempts_censored"]=11
        with self.assertRaisesRegex(CrosshostHold,"EARLY_PHYSICAL_WINDOW_REPORT_MISSING"):
            validate_manifest(m,e,p)
    def test_duplicate_maturity_receipt_ids_refused(self):
        m,e,p=sample()
        duplicate=m["windows"][0]["outcomes"][0]["attempt_sha256"]
        m["windows"][3]["outcomes"][0]["attempt_sha256"]=duplicate
        p["original_1005_detailed_maturities"][0][
            "original_maturity_receipt_sha256"]=duplicate
        with self.assertRaisesRegex(CrosshostHold,"DUPLICATE_PRIMARY_MATURE_RECEIPT"):
            validate_manifest(m,e,p)
    def test_original_censored_maturity_not_relabelled(self):
        m,e,p=sample();m["windows"][0]["outcomes"][0]["state"]="MATURE_PRICE_ONLY"
        with self.assertRaisesRegex(CrosshostHold,"PRIMARY_MATURE_RECEIPT"):
            validate_manifest(m,e,p)
    def test_product_efficacy_claim_not_accepted(self):
        m,e,p=sample();m["mature_quote_only_samples"]=1
        with self.assertRaisesRegex(CrosshostHold,"PRIMARY_WORKER_FINAL_SCOPE"):
            validate_manifest(m,e,p)

if __name__=="__main__":unittest.main()
