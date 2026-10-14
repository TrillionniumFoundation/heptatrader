"""Synthetic adversarial fixtures: frozen population, causal dates and audit no-trade."""
from __future__ import annotations
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from train_v2.fx_ridge_finite_final_audit import (
    audit, sha, CohortAuditHold, read_private, write_once
)

BASE=1_800_000_000_000
STEP=1_200_000
HORIZON=900_000

def raw(obj):
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"))+"\n").encode()

def newprivate(p, data):
    p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if type(data) is dict:
        data=raw(data)
    p.write_bytes(data)
    p.chmod(0o600)
    return sha(data)

def build(home):
    root=home/"campaign"
    ind=home/"independent"
    root.mkdir(mode=0o700);ind.mkdir(mode=0o700)
    times=[BASE+i*STEP for i in range(4)]
    model={"schema":"heptatrader.fx-ridge-historical-model.v1",
           "historical_validation_selected_action":"NO_TRADE",
           "candidate_has_positive_validation_edge":False,
           "historical_holdout_previously_seen":True}
    import hashlib
    model_hash=hashlib.sha256(json.dumps(model,sort_keys=True,
                              separators=(",",":")).encode()).hexdigest()
    intent={"schema":"heptatrader.fx-ridge-finite-prospective-campaign.v1",
            "fixed_count":4,"anchors_ms":times,
            "registered_operator_start_ms":BASE-120000,"interval_ms":STEP,
            "trade_authority":False,"broker_orders_sent":0,"broker_requests_sent":0,
            "automatic_training":False,"model_promotion_allowed":False,
            "shadow_only":True,"no_historical_test_reselection":True,
            "original_validation_action":"NO_TRADE"}
    intent_hash=newprivate(root/"campaign-intent.json",intent)
    result=[]
    frozen=[]
    for i,anchor in enumerate(times):
        cell=root/("window-%02d"%i)
        cell.mkdir(mode=0o700)
        plan_id=("%064x"%(i+1))
        plan={"plan_id":plan_id,"first_decision_ms":anchor+1000,
              "last_decision_ms":anchor+4500}
        registration={"plan_id":plan_id,"plan":plan,
                      "sealed_at_ms":BASE-60000,
                      "trade_authority":False,
                      "broker_market_data_signed":False}
        manifest={"anchor_ms":anchor,"outcome_due_ms":anchor+HORIZON,
                  "historical_model_sha256":model_hash}
        hashes={}
        for filename,doc in (("model.json",model),("manifest.json",manifest),
                             ("local-registration.json",registration),
                             ("plan.json",plan),("feature-spec.json",{"sample":i})):
            hashes[filename]=newprivate(cell/filename,doc)
        entry=dict(index=i,anchor_ms=anchor,plan_id=plan_id,
                   first_decision_ms=anchor+1000,last_decision_ms=anchor+4500,
                   outcome_due_ms=anchor+HORIZON,
                   receipt_sealed_at_ms=BASE-60000,
                   model_sha256=hashes["model.json"],
                   manifest_sha256=hashes["manifest.json"],
                   registration_sha256=hashes["local-registration.json"],
                   plan_sha256=hashes["plan.json"],spec_sha256=hashes["feature-spec.json"])
        frozen.append(entry)
        for stage in ("decision","maturity"):
            censored=(i==0)
            status=("CENSORED" if censored else ("FROZEN_PRICE_ONLY" if stage=="decision"
                                               else "MATURE_PRICE_ONLY"))
            reason=("EXCEPTION_ResearchHold_LAST_ORIGINAL_QUOTE_TOO_OLD"
                    if censored and stage=="decision" else
                    "NO_PREMATURE_VALID_FROZEN_DECISION" if censored else "NONE")
            attempt={"schema":"heptatrader.fx-ridge-finite-prospective-campaign.v1.attempt.v1",
                     "stage":stage,"state":status,"reason_code":reason,
                     "nonretryable":True,"no_historical_replay_or_sample_backfill":True,
                     "prospective_research_only":True,"trade_authority":False,
                     "broker_orders_sent":0,"model_promotion_allowed":False,
                     "attempt_recorded_at_ms":anchor+(1500 if stage=="decision"
                                                      else HORIZON+6500)}
            hashes[stage]=newprivate(cell/(stage+"-attempt.json"),attempt)
        result.append(dict(window=i,plan_id=plan_id,
                           decision_attempt_sha256=hashes["decision"],
                           maturity_attempt_sha256=hashes["maturity"],
                           decision_state="CENSORED" if i==0 else "FROZEN_PRICE_ONLY",
                           maturity_state="CENSORED" if i==0 else "MATURE_PRICE_ONLY"))
        if i==0:continue
        forecast={1:"-0.1",2:"0.2",3:"0.2"}[i]
        actual={1:"-1",2:"-2",3:"-1"}[i]
        error=str(__import__("decimal").Decimal(forecast)-
                  __import__("decimal").Decimal(actual))
        original_decision={"plan_id":plan_id,"selected_trading_action":"NO_TRADE",
                "forecast_magnitude_clears_cost":False,
                "original_trader_behavior_propensity_attested":False,
                "native_owner_current":False,"actual_execution_reward":False,
                "forecast_midpoint_bps":float(forecast),"decision_at_ms":anchor+1200,
                "cost_hurdle":{"required_absolute_prediction_bps":10.9133557},
                "trade_authority":False,"broker_orders_sent":0,
                "model_promotion_allowed":False}
        decision_sha=newprivate(cell/"preoutcome-decision.json",original_decision)
        qualification={"state":"PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE",
                       "decision_sha256":decision_sha,
                       "sealed_before_broker_reported_quote_expiry":True,
                       "selected_trading_action":"NO_TRADE",
                       "seal_at_ms":anchor+1300,
                       "last_original_quote_expiry_ms":anchor+4000,
                       "trade_authority":False,"broker_orders_sent":0,
                       "model_promotion_allowed":False}
        newprivate(cell/"qualification.json",qualification)
        outcome={"predecision_sha256":decision_sha,"outcome_due_ms":anchor+HORIZON,
                 "price_event_ms":anchor+HORIZON+200,
                 "price_available_ms":anchor+HORIZON+300,
                 "selected_trading_action":"NO_TRADE","model_efficacy_proven":False,
                 "model_promotion_allowed":False,"observed_trading_pnl_attested":False,
                 "forecast_midpoint_bps":forecast,
                 "observed_original_midpoint_return_bps":actual,
                 "forecast_error_bps":error,"research_direction_correct":i==1,
                 "outcome_quote_sha256":"f"*64,"trade_authority":False,
                 "broker_orders_sent":0}
        newprivate(cell/"mature-outcome.json",outcome)
        independent={"schema":"heptatrader.fx-ridge-independent-original-check.v1",
                     "research_only":True,"broker_queries_sent":0,"broker_orders_sent":0,
                     "model_promotion_allowed":False,"native_ledger_witness_admitted":False,
                     "source_prefix_count":6,
                     "preaction":{"independent_forecast_bps":float(forecast),
                                  "outcome_due_ms":anchor+HORIZON},
                     "outcome":{"state":"MATURE_ORIGINAL_PRICE_INDEPENDENTLY_RECOMPUTED",
                                "observed_midpoint_return_bps":actual,
                                "forecast_error_bps":error,
                                "outcome_quote_sha256":"f"*64,
                                "direction_correct_research_only":i==1}}
        newprivate(ind/("window-%02d-independent-original-v1.json"%i),independent)
    sealed={"schema":"heptatrader.fx-ridge-finite-prospective-campaign.v1.sealed.v1",
            "intent_sha256":intent_hash,"sealed_at_ms":BASE-50000,
            "fixed_model_across_all_windows":True,
            "all_registered_before_first_decision":True,"windows":frozen}
    seal_hash=newprivate(root/"campaign-sealed.json",sealed)
    final={"schema":"heptatrader.fx-ridge-finite-prospective-campaign.v1.final.v1",
           "intent_sha256":intent_hash,"sealed_sha256":seal_hash,
           "window_results":result,"window_count":4,
           "successful_matured_count":3,"censored_count":1,
           "completed_at_ms":times[-1]+HORIZON+6500,
           "trade_authority":False,"broker_orders_sent":0,
           "automatic_promotion":False,"model_economic_efficacy_proven":False}
    newprivate(root/"campaign-final.json",final)
    return root,ind

class FinalAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root,self.ind=build(Path(self.tmp.name))

    def mutate(self,path,fn):
        data=json.loads(path.read_text())
        fn(data)
        newprivate(path,data)

    def test_full_valid_synthetic_cohort(self):
        r=audit(self.root,self.ind)
        self.assertEqual((r["population_windows"],r["mature_price_only"],r["censored"]),(4,3,1))
        self.assertEqual(r["correct_direction_count"],1)
        self.assertFalse(r["ridge_beats_zero_baseline_on_this_cohort"])
        self.assertFalse(r["trade_authority"])
        self.assertTrue(r["no_historical_replay_or_sample_backfill"])

    def test_censored_window_relabel_refused(self):
        f=self.root/"window-00/decision-attempt.json"
        self.mutate(f,lambda d:d.update(state="FROZEN_PRICE_ONLY"))
        with self.assertRaises(CohortAuditHold):audit(self.root,self.ind)

    def test_censored_future_result_forgery_refused(self):
        newprivate(self.root/"window-00/mature-outcome.json",{"fiction":1})
        with self.assertRaisesRegex(CohortAuditHold,"RETROFILLED"):
            audit(self.root,self.ind)

    def test_changed_precommitted_seal_refused(self):
        f=self.root/"campaign-sealed.json"
        self.mutate(f,lambda d:d["windows"][0].update(anchor_ms=d["windows"][0]["anchor_ms"]+300000))
        with self.assertRaisesRegex(CohortAuditHold,"IMMUTABILITY_CHAIN"):
            audit(self.root,self.ind)

    def test_changed_original_decision_refused(self):
        f=self.root/"window-01/preoutcome-decision.json"
        self.mutate(f,lambda d:d.update(forecast_midpoint_bps=10))
        with self.assertRaisesRegex(CohortAuditHold,"PREDECISION"):
            audit(self.root,self.ind)

    def test_unexpected_promotion_refused(self):
        f=self.root/"window-01/mature-outcome.json"
        self.mutate(f,lambda d:d.update(model_promotion_allowed=True))
        with self.assertRaisesRegex(CohortAuditHold,"PROMOTED_OUTCOME"):
            audit(self.root,self.ind)

    def test_future_maturity_before_cutoff_refused(self):
        f=self.root/"window-02/mature-outcome.json"
        self.mutate(f,lambda d:d.update(price_event_ms=BASE+2*STEP+HORIZON-1))
        with self.assertRaisesRegex(CohortAuditHold,"NONCAUSAL_OR_PROMOTED_OUTCOME"):
            audit(self.root,self.ind)

    def test_tampered_independent_receipt_refused(self):
        f=self.ind/"window-03-independent-original-v1.json"
        self.mutate(f,lambda d:d["outcome"].update(outcome_quote_sha256="0"*64))
        with self.assertRaisesRegex(CohortAuditHold,"INDEPENDENT_PREDICTION"):
            audit(self.root,self.ind)

    def test_unattested_broker_source_cannot_turn_into_signed(self):
        f=self.ind/"window-01-independent-original-v1.json"
        self.mutate(f,lambda d:d.update(native_ledger_witness_admitted=True))
        with self.assertRaisesRegex(CohortAuditHold,"INDEPENDENT_ORIGINAL"):
            audit(self.root,self.ind)

    def test_deleting_independent_receipt_refused(self):
        (self.ind/"window-02-independent-original-v1.json").unlink()
        with self.assertRaises(FileNotFoundError):audit(self.root,self.ind)

    def test_duplicate_json_field_refused(self):
        f=self.root/"campaign-intent.json"
        newprivate(f,b'{"schema":1,"schema":2}')
        with self.assertRaisesRegex(CohortAuditHold,"DUPLICATE_JSON_FIELD"):
            read_private(f)

    def test_public_evidence_refused(self):
        p=self.root/"campaign-final.json"
        p.chmod(0o644)
        with self.assertRaisesRegex(CohortAuditHold,"SOURCE_NOT_PRIVATE"):
            audit(self.root,self.ind)

    def test_symlink_evidence_refused(self):
        f=self.ind/"window-03-independent-original-v1.json"
        alias=self.ind/"target.json"
        f.rename(alias)
        f.symlink_to(alias)
        with self.assertRaisesRegex(CohortAuditHold,"SOURCE_PATH_INVALID"):
            audit(self.root,self.ind)

    def test_output_write_once(self):
        path=self.ind/"final.json"
        write_once(path,{"schema":"research","trade_authority":False})
        with self.assertRaises(FileExistsError):
            write_once(path,{"schema":"falsified"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

if __name__=="__main__":
    unittest.main()
