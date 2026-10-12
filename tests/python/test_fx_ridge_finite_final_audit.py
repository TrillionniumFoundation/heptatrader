"""Adversarial regression for the immutable four-window, price-only final audit."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from train_v2 import fx_ridge_finite_final_audit as final

BASE="heptatrader.fx-ridge-finite-prospective-campaign.v1"

def dump(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,sort_keys=True,allow_nan=False)+"\n")
    path.chmod(0o600)
    return final.hashed(path)

def altered(path,changes):
    data=final.read(path)
    data.update(changes)
    return dump(path,data)

def fixture(tmp):
    home=tmp/"user"
    home.mkdir(mode=0o700)
    root=home/"campaign";root.mkdir(mode=0o700)
    independent=home/"independent";independent.mkdir(mode=0o700)
    old=home/"old";old.mkdir(mode=0o700)
    config=home/"config.json";cost=home/"cost.json"
    dump(config,{"sandbox":True});dump(cost,{"commission":"paper"})
    dump(old/"model.json",{"frozen":True,"model":"ridge50"})
    anchors=[1791799800000+i*1200000 for i in range(4)]
    stamp=anchors[0]-120000
    intent={
        "schema":BASE,"fixed_count":4,"interval_ms":1200000,
        "anchors_ms":anchors,"original_model_root":str(old),
        "original_model_file_sha256":final.hashed(old/"model.json"),
        "config_path":str(config),"config_sha256":final.hashed(config),
        "cost_path":str(cost),"cost_sha256":final.hashed(cost),
        "original_period_sha256":"7"*64,
        "original_source_code_sha256":final.code_sha(
            Path(final.__file__).parent/"fx_ridge_original.py"),
        "campaign_source_code_sha256":final.code_sha(
            Path(final.__file__).parent/"fx_ridge_finite_campaign.py"),
        "historical_model_sha256":"8"*64,
        "registered_operator_start_ms":stamp-500,
        "shadow_only":True,"trade_authority":False,
        "model_promotion_allowed":False,"automatic_training":False,
        "no_historical_test_reselection":True,
        "independent_broker_signed_source":False,
        "trader_behavior_propensities_attested":False,
        "canonical_owner_or_witness_enrolled":False
    }
    intent_sha=dump(root/"campaign-intent.json",intent)
    receipts=[]
    finals=[]
    for i,anchor in enumerate(anchors):
        c=root/("window-%02d"%i);c.mkdir(mode=0o700)
        planid=("%x"% (i+1))*64
        plan=dict(plan_id=planid,registered_at_ms=stamp,trade_authority=False)
        registration=dict(plan_id=planid,sealed_at_ms=stamp+10,
            trade_authority=False,broker_market_data_signed=False,
            canonical_agentd_owner_enrolled=False,independent_owner_signature=False)
        manifest=dict(anchor_ms=anchor,outcome_due_ms=anchor+900000,
            historical_model_sha256="8"*64,validation_trading_edge_qualified=False)
        base=dict(index=i,anchor_ms=anchor,plan_id=planid,
                  first_decision_ms=anchor+1000,last_decision_ms=anchor+4500,
                  outcome_due_ms=anchor+900000,receipt_sealed_at_ms=stamp+10,
                  trade_authority=False)
        for key,file,obj in (
            ("registration_sha256","local-registration.json",registration),
            ("model_sha256","model.json",{"frozen":True,"model":"ridge50"}),
            ("manifest_sha256","manifest.json",manifest),
            ("plan_sha256","plan.json",plan),
            ("spec_sha256","feature-spec.json",{"feature_id":"original"})):
            base[key]=dump(c/file,obj)
        if i==0:
            decision="CENSORED";maturity="CENSORED"
        else:
            decision="FROZEN_PRICE_ONLY";maturity="MATURE_PRICE_ONLY"
        d=dict(stage="decision",state=decision,
               reason_code=("LAST_ORIGINAL_QUOTE_TOO_OLD" if i==0 else "NONE"),
               attempt_recorded_at_ms=anchor+1300,
               nonretryable=True,trade_authority=False,broker_orders_sent=0,
               model_promotion_allowed=False)
        m=dict(stage="maturity",state=maturity,
               reason_code=("NO_QUALIFIED_PREDECISION" if i==0 else "NONE"),
               attempt_recorded_at_ms=anchor+900000+6500,
               nonretryable=True,trade_authority=False,broker_orders_sent=0,
               model_promotion_allowed=False)
        if i:
            forecasts={1:("-0.5","-1.25","0.75",True),
                       2:("0.2","-1","1.2",False),
                       3:("0.3","-2","2.3",False)}
            prediction,outcome,error,correct=forecasts[i]
            frozen=dict(plan_id=planid,model_sha256="8"*64,
                        anchor_ms=anchor,decision_at_ms=anchor+1000,
                        forecast_midpoint_bps=prediction,
                        selected_trading_action="NO_TRADE",
                        forecast_magnitude_clears_cost=False,
                        actual_execution_reward=False,native_owner_current=False)
            d_sha=dump(c/"preoutcome-decision.json",frozen)
            qualification=dict(decision_sha256=d_sha,
                state="PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE",
                selected_trading_action="NO_TRADE",
                sealed_before_broker_reported_quote_expiry=True,
                seal_at_ms=anchor+1150,
                last_original_quote_expiry_ms=anchor+2100)
            dump(c/"qualification.json",qualification)
            outcome_data=dict(predecision_sha256=d_sha,
                 selected_trading_action="NO_TRADE",
                 outcome_due_ms=anchor+900000,
                 price_event_ms=anchor+900000+150,
                 price_available_ms=anchor+900000+500,
                 forecast_midpoint_bps=prediction,
                 observed_original_midpoint_return_bps=outcome,
                 forecast_error_bps=error,
                 outcome_quote_sha256=("%x"%i)*64,
                 research_direction_correct=correct,
                 model_efficacy_proven=False,
                 observed_trading_pnl_attested=False,trade_authority=False)
            dump(c/"mature-outcome.json",outcome_data)
            report={
                "research_only":True,
                "model_promotion_allowed":False,
                "native_ledger_witness_admitted":False,
                "source_locally_hashed_not_broker_signed":True,
                "broker_orders_sent":0,
                "preaction":{"independent_forecast_bps":float(prediction),
                             "calibrated_required_bps":10.9},
                "outcome":{
                    "state":"MATURE_ORIGINAL_PRICE_INDEPENDENTLY_RECOMPUTED",
                    "observed_midpoint_return_bps":outcome,
                    "forecast_error_bps":error,
                    "outcome_quote_sha256":("%x"%i)*64,
                    "direction_correct_research_only":correct
                }
            }
            dump(independent/("independent-window-%02d.json"%i),report)
        ds=dump(c/"decision-attempt.json",d)
        ms=dump(c/"maturity-attempt.json",m)
        receipts.append(base)
        finals.append(dict(window=i,plan_id=planid,
                           decision_state=decision,maturity_state=maturity,
                           decision_attempt_sha256=ds,maturity_attempt_sha256=ms))
    seal=dict(schema=BASE+".sealed.v1",intent_sha256=intent_sha,
              sealed_at_ms=stamp+100,windows=receipts,
              all_registered_before_first_decision=True,
              fixed_model_across_all_windows=True)
    sh=dump(root/"campaign-sealed.json",seal)
    dump(root/"campaign-worker-start.json",{
        "sealed_sha256":sh,"started_at_ms":stamp+200,
        "automatic_retries":False,"no_order_api_or_model_promotion":True})
    completed=anchors[-1]+900000+6700
    dump(root/"campaign-final.json",dict(
        schema=BASE+".final.v1",completed_at_ms=completed,
        intent_sha256=intent_sha,sealed_sha256=sh,
        window_results=finals,successful_matured_count=3,censored_count=1,
        automatic_promotion=False,model_economic_efficacy_proven=False,
        broker_orders_sent=0))
    return root,independent,completed+1000

class FourWindowIndependentFinalTests(unittest.TestCase):
    def build(self,work):
        return fixture(Path(work))
    def test_exact_final_math(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            r=final.verify(root,ind,at_ms=t)
            self.assertEqual(r["total_preregistered_windows"],4)
            self.assertEqual(r["eligible_price_only_predictions"],3)
            self.assertEqual(r["censored_windows"],1)
            self.assertEqual(r["direction_correct_count"],1)
            self.assertAlmostEqual(float(r["mean_abs_forecast_error_bps"]),4.25/3,places=10)
            self.assertFalse(r["live_trading_alpha_proven"])
    def test_mutated_registration_cannot_relabel_sample(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            altered(root/"window-03"/"local-registration.json",
                    {"sealed_at_ms":t})
            with self.assertRaisesRegex(final.AuditHold,"REGISTERED_FILE_CHANGED"):
                final.verify(root,ind,at_ms=t)
    def test_mutated_decision_hash_refused(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            altered(root/"window-02"/"preoutcome-decision.json",
                    {"forecast_midpoint_bps":100})
            with self.assertRaisesRegex(final.AuditHold,"PREACTION_OR_MATURITY_TIMELINE_CHANGED"):
                final.verify(root,ind,at_ms=t)
    def test_cherry_picked_later_future_quote_refused(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            altered(root/"window-03"/"mature-outcome.json",
                    {"price_event_ms":t+100000})
            with self.assertRaisesRegex(final.AuditHold,"PREACTION_OR_MATURITY_TIMELINE_CHANGED"):
                final.verify(root,ind,at_ms=t)
    def test_censored_0_cannot_receive_hindsight(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            dump(root/"window-00"/"mature-outcome.json",{"fabricated":True})
            with self.assertRaisesRegex(final.AuditHold,"CENSORED_WINDOW_WAS_BACKFILLED"):
                final.verify(root,ind,at_ms=t)
    def test_wrong_independent_numeric_replay_rejected(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            altered(ind/"independent-window-02.json",{"outcome":{"state":"MATURE_ORIGINAL_PRICE_INDEPENDENTLY_RECOMPUTED",
                  "observed_midpoint_return_bps":"9999",
                  "forecast_error_bps":"1.2","outcome_quote_sha256":"2"*64,
                  "direction_correct_research_only":False}})
            with self.assertRaisesRegex(final.AuditHold,"INDEPENDENT_ECONOMIC_OR_NUMERIC_REPLAY_DISAGREES"):
                final.verify(root,ind,at_ms=t)
    def test_missing_independent_report_refused(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            (ind/"independent-window-02.json").unlink()
            with self.assertRaises(FileNotFoundError):
                final.verify(root,ind,at_ms=t)
    def test_promotion_claim_refused(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            altered(root/"campaign-final.json",{"automatic_promotion":True})
            with self.assertRaisesRegex(final.AuditHold,"CAMPAIGN_TIMELINE_OR_SEALS_INVALID"):
                final.verify(root,ind,at_ms=t)
    def test_stale_quote_seal_rejected(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            altered(root/"window-01"/"qualification.json",
                    {"last_original_quote_expiry_ms":1791790000000})
            with self.assertRaisesRegex(final.AuditHold,"PREACTION_OR_MATURITY_TIMELINE_CHANGED"):
                final.verify(root,ind,at_ms=t)
    def test_late_pre_registration_hash_refused(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            altered(root/"campaign-sealed.json",{"sealed_at_ms":t})
            with self.assertRaisesRegex(final.AuditHold,"CAMPAIGN_TIMELINE_OR_SEALS_INVALID"):
                final.verify(root,ind,at_ms=t)
    def test_public_evidence_permissions_refused(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            f=root/"window-03"/"mature-outcome.json";f.chmod(0o644)
            with self.assertRaisesRegex(final.AuditHold,"SOURCE_NOT_PRIVATE"):
                final.verify(root,ind,at_ms=t)
    def test_write_once_never_overwrites(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            out=ind/"final-proof.json"
            final.write_exclusive(out,{"state":"sealed"})
            with self.assertRaises(FileExistsError):
                final.write_exclusive(out,{"state":"overwritten"})
            self.assertEqual(final.read(out)["state"],"sealed")
    def test_nonfinite_price_rejected(self):
        with tempfile.TemporaryDirectory() as p:
            root,ind,t=self.build(p)
            self.assertRaises(final.AuditHold,final.number,float("inf"))
    def test_duplicate_json_key_rejected(self):
        with self.assertRaisesRegex(final.AuditHold,"DUPLICATE_JSON_KEY"):
            final.object_pairs([("x",1),("x",2)])

if __name__=="__main__":
    unittest.main()
