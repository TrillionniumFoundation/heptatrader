"""Adversarial read-only audit cases: no postdecision rescoring or promotion."""
from __future__ import annotations
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from historical_training.common import digest
from train_v2.fx_ridge_independent_original_check import (
    AuditHold, check_preaction, check_maturity, compute_features,
    independent_prediction, original_prefix
)

ANCHOR=1791795900000
DUE=ANCHOR+900000
PERIOD="c"*64

def fixtures():
    samples=[]
    for i in range(13):
        end=ANCHOR-(12-i)*300000
        samples.append({
            "schema":"heptatrader.train-v2-observation.v1",
            "source_id":f"{i+1:064x}","source_sha256":f"{i+1:064x}",
            "source_kind":"LIVE_CAUSAL_QUOTE","period_sha256":PERIOD,
            "instrument":"EUR.USD","event_at_ms":end-700,
            "available_at_ms":end-550,"valid_until_ms":end+4000,
            "bid":"1.12000","ask":"1.12002"})
    model={"schema":"heptatrader.fx-ridge-historical-model.v1",
           "trade_authority":False,"candidate_has_positive_validation_edge":False,
           "historical_holdout_previously_seen":True,
           "historical_validation_selected_action":"NO_TRADE",
           "period_sha256":PERIOD,"mean":[0.]*8,"stddev":[1.]*8,
           "weights":[0.081644]+[0.]*8}
    manifest={"schema":"heptatrader.fx-ridge-original-research.v1",
              "historical_model_sha256":digest(model),
              "validation_trading_edge_qualified":False,
              "trading_behavior_propensity_known":False,
              "external_production_root_enrolled":False,
              "broker_signed_quote":False,"original_period_sha256":PERIOD,
              "anchor_ms":ANCHOR,"outcome_due_ms":DUE,"policy_id":"RESEARCH_POLICY"}
    manifest["source_root"]="/tmp/original-test-source-only"
    plan={"plan_id":"test","period_sha256":PERIOD,"trade_authority":False,
          "promotion_allowed":False,"first_decision_ms":ANCHOR+1000,
          "last_decision_ms":ANCHOR+4500,"registered_at_ms":ANCHOR-66000}
    receipt={"plan_id":"test","trade_authority":False,
             "canonical_agentd_owner_enrolled":False,
             "broker_market_data_signed":False,
             "independent_owner_signature":False,
             "can_relabel_previously_inspected_data_as_unseen":False,
             "sealed_at_ms":ANCHOR-65000,
             "first_decision_ms":plan["first_decision_ms"],
             "last_decision_ms":plan["last_decision_ms"],
             "original_source_root":manifest["source_root"],
             "plan":plan,"plan_sha256":digest(plan)}
    cost={"quote_spread_full_bps":{"p99":3.4837759},
          "fx_commission_one_way_bps":{"p90":0.7147899}}
    bid=Decimal(samples[-1]["bid"]);ask=Decimal(samples[-1]["ask"])
    spread=float((ask-bid)/((ask+bid)/2)*10000)
    decision={"plan_id":"test","anchor_ms":ANCHOR,
              "decision_at_ms":ANCHOR+1500,
              "outcome_mature_not_before_ms":DUE,
              "history_quote_samples":samples,
              "feature_vector":compute_features(samples,ANCHOR),
              "forecast_midpoint_bps":0.081644,
              "selected_trading_action":"NO_TRADE",
              "trade_authority":False,"promotion_allowed":False,
              "actual_execution_reward":False,
              "original_trader_behavior_propensity_attested":False,
              "native_owner_current":False,
              "forecast_magnitude_clears_cost":False,
              "cost_hurdle":{"live_original_spread_bps":spread,
                             "stressed_spread_bps":3.4837759,
                             "roundtrip_commission_p90_bps":1.4295798,
                             "hypothetical_slippage_roundtrip_bps":4.,
                             "minimum_margin_bps":2.,
                             "required_absolute_prediction_bps":10.9133557}}
    qualification={"trade_authority":False,"model_promotion_allowed":False,
                   "state":"PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE",
                   "sealed_before_broker_reported_quote_expiry":True,
                   "seal_at_ms":ANCHOR+2000,
                   "last_original_quote_expiry_ms":ANCHOR+4000}
    return model,manifest,plan,receipt,decision,qualification,cost

class PreactionTests(unittest.TestCase):
    def run_valid(self):
        items=fixtures()
        return check_preaction(*items[:-1],ANCHOR+6000,items[-1])

    def test_original_ridge_prediction_and_cost_recomputed(self):
        r=self.run_valid()
        self.assertAlmostEqual(r["independent_forecast_bps"],.081644,places=10)
        self.assertAlmostEqual(r["calibrated_required_bps"],10.9133557,places=7)
    def test_historical_no_edge_is_not_promoted(self):
        data=list(fixtures());data[0]["candidate_has_positive_validation_edge"]=True
        with self.assertRaisesRegex(AuditHold,"ORIGINAL_HISTORICAL_NONADMISSION_CHANGED"):
            check_preaction(*data[:-1],ANCHOR+6000,data[-1])
    def test_unenrolled_product_root_is_required(self):
        data=list(fixtures());data[1]["external_production_root_enrolled"]=True
        with self.assertRaisesRegex(AuditHold,"PRETENDED_PRODUCT_TRUST"):
            check_preaction(*data[:-1],ANCHOR+6000,data[-1])
    def test_fake_local_propensity_rejected(self):
        data=list(fixtures());data[4]["original_trader_behavior_propensity_attested"]=True
        with self.assertRaisesRegex(AuditHold,"QUALIFICATION_FALSIFIED"):
            check_preaction(*data[:-1],ANCHOR+6000,data[-1])
    def test_last_quote_stale_at_bar_close(self):
        data=list(fixtures());data[4]["history_quote_samples"][-1]["valid_until_ms"]=ANCHOR-1
        with self.assertRaisesRegex(AuditHold,"NONCAUSAL_ORIGINAL_BAR_CLOSE"):
            check_preaction(*data[:-1],ANCHOR+6000,data[-1])
    def test_missing_quote_bucket(self):
        data=list(fixtures());data[4]["history_quote_samples"].pop()
        with self.assertRaisesRegex(AuditHold,"INPUT_OR_DUPLICATE_COUNT"):
            check_preaction(*data[:-1],ANCHOR+6000,data[-1])
    def test_modified_forecast_rejected(self):
        data=list(fixtures());data[4]["forecast_midpoint_bps"]=20
        with self.assertRaisesRegex(AuditHold,"FROZEN_RIDGE_FORECAST_CHANGED"):
            check_preaction(*data[:-1],ANCHOR+6000,data[-1])
    def test_registration_late_rejected(self):
        data=list(fixtures());data[3]["sealed_at_ms"]=ANCHOR-15000
        with self.assertRaisesRegex(AuditHold,"REGISTRATION_NOT_FROZEN_BEFORE_WINDOW"):
            check_preaction(*data[:-1],ANCHOR+6000,data[-1])
    def test_reduced_cost_hurdle_rejected(self):
        data=list(fixtures());data[4]["cost_hurdle"]["required_absolute_prediction_bps"]=0.
        with self.assertRaisesRegex(AuditHold,"COST_ABSTENTION_NOT_REPRODUCIBLE"):
            check_preaction(*data[:-1],ANCHOR+6000,data[-1])
    def test_incorrect_input_features_rejected(self):
        data=list(fixtures());data[4]["feature_vector"][0]+=0.1
        with self.assertRaisesRegex(AuditHold,"FROZEN_FEATURES_CHANGED"):
            check_preaction(*data[:-1],ANCHOR+6000,data[-1])

class MaturityTests(unittest.TestCase):
    def setup_episode(self,directory):
        root=Path(directory)
        _,manifest,_,_,decision,_,_=fixtures()
        sourcedir=root/"source"
        file=sourcedir/"segment-1"/"quotes.jsonl"
        file.parent.mkdir(parents=True)
        data=b'{"kind":"HEADER"}\n'
        file.write_bytes(data);file.chmod(0o600)
        manifest["source_root"]=str(sourcedir)
        quote={"event_at_ms":DUE+400,"available_at_ms":DUE+550,
               "bid":"1.12008","ask":"1.12010","source_id":"f"*64}
        origin=decision["history_quote_samples"][-1]
        start=(Decimal(origin["bid"])+Decimal(origin["ask"]))/2
        end=(Decimal(quote["bid"])+Decimal(quote["ask"]))/2
        move=(end/start-1)*10000
        forecast=Decimal(str(decision["forecast_midpoint_bps"]))
        outcome={"schema":"heptatrader.fx-ridge-original-mature-price.v1",
                 "outcome_due_ms":DUE,"selected_trading_action":"NO_TRADE",
                 "observed_trading_pnl_attested":False,"model_efficacy_proven":False,
                 "trade_authority":False,
                 "input_quote_sha256":origin["source_id"],
                 "forecast_midpoint_bps":str(forecast),
                 "price_event_ms":quote["event_at_ms"],
                 "price_available_ms":quote["available_at_ms"],
                 "mature_source_prefix_receipts":[
                     {"path":str(file),"complete_prefix_bytes":len(data),
                      "prefix_sha256":hashlib.sha256(data).hexdigest()}],
                 "outcome_quote_sha256":quote["source_id"],
                 "observed_original_midpoint_return_bps":str(move),
                 "forecast_error_bps":str(forecast-move),
                 "research_direction_correct":True}
        p=root/"mature-outcome.json";p.write_text(json.dumps(outcome));p.chmod(0o600)
        proof={"original_period_verified":True,"historical_revisions_used":False,
               "broker_requests_sent":0,"broker_orders_sent":0}
        return root,manifest,decision,outcome,quote,proof

    def test_no_mature_file_is_hold(self):
        with tempfile.TemporaryDirectory() as d:
            _,manifest,_,_,decision,_,_=fixtures()
            self.assertEqual(check_maturity(Path(d),None,manifest,decision,DUE+7000)["state"],
                             "PREOUTCOME_ONLY_HOLD_NOT_MATURE_RESULT")
    def test_independent_first_real_source_price(self):
        with tempfile.TemporaryDirectory() as d:
            root,m,decision,out,q,proof=self.setup_episode(d)
            with mock.patch("train_v2.fx_ridge_independent_original_check.sealed_original_broker_expiry",
                            return_value=([q],proof)):
                r=check_maturity(root,None,m,decision,DUE+7000)
                self.assertEqual(r["state"],"MATURE_ORIGINAL_PRICE_INDEPENDENTLY_RECOMPUTED")
    def test_cherry_picked_later_quote_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root,m,decision,out,q,proof=self.setup_episode(d)
            earlier=dict(q,event_at_ms=DUE+100,available_at_ms=DUE+200,source_id="e"*64)
            with mock.patch("train_v2.fx_ridge_independent_original_check.sealed_original_broker_expiry",
                            return_value=([q,earlier],proof)):
                with self.assertRaisesRegex(AuditHold,"MATURITY_CHERRY_PICK"):
                    check_maturity(root,None,m,decision,DUE+7000)
    def test_future_label_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root,m,decision,out,q,proof=self.setup_episode(d)
            out["observed_original_midpoint_return_bps"]="999"
            file=root/"mature-outcome.json"
            file.write_text(json.dumps(out))
            with mock.patch("train_v2.fx_ridge_independent_original_check.sealed_original_broker_expiry",
                            return_value=([q],proof)):
                with self.assertRaisesRegex(AuditHold,"INDEPENDENT_PRICE_OR_ERROR_DIFFERS"):
                    check_maturity(root,None,m,decision,DUE+7000)
    def test_pre_due_label_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root,m,decision,out,q,proof=self.setup_episode(d)
            with self.assertRaisesRegex(AuditHold,"MATURE_RESULT_NOT_ADMISSIBLE"):
                check_maturity(root,None,m,decision,DUE+5000)
    def test_source_prefix_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root,m,decision,out,q,proof=self.setup_episode(d)
            file=Path(out["mature_source_prefix_receipts"][0]["path"])
            file.write_bytes(b'{"kind":"TAMPER"}\n')
            with self.assertRaisesRegex(AuditHold,"ORIGINAL_PREFIX_HASH_CHANGED"):
                check_maturity(root,None,m,decision,DUE+7000)

if __name__=="__main__":
    unittest.main()
