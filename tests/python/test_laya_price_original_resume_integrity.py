"""Synthetic durable files: reject forged/stale resumed original-quote results.

These tests do not load any real model or require any broker/GPU access.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell.price_original_campaign import (
    REQUIRED_PINS, HORIZON_MS, INPUT_GRACE_MS, OUTCOME_SCHEMA,
    create_plan, expected_anchor, prediction, grade,
    verify_saved_outcome)
from research_laya_price_original_shadow90 import outcome_list

PINS={name:"a"*64 for name in REQUIRED_PINS}
PRIOR=[0.05,0.05,0.10,0.65,0.10,0.03,0.02]


class RestartReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.plan=create_plan(1800000000000,PINS,PRIOR,"b"*64)
        self.anchor=expected_anchor(self.plan,0)
        feature={"anchor_ms":self.anchor,
                 "source_kind":"ORIGINAL_VERIFIED_PAPER_QUOTES",
                 "original_event_ticks_not_revised_ib_bars":True,
                 "trade_authority":False,
                 "snapshot_id":"c"*64,"asof_ms":self.anchor+2000,
                 "last_closed_bar_age_ms":2000,
                 "future_outcome_was_not_required":True,
                 "features":[0.0]*24}
        self.pred=prediction(self.plan,0,feature,[.1,.1,.1,.4,.1,.1,.1],
                             self.anchor+3000,1.2345,"d"*64,0)
        bar={"time":self.anchor//1000,
             "midpoint":{"close":1.2348}}
        receipt={"bar_start_ms":self.anchor,
                 "bar_end_ms":self.anchor+HORIZON_MS,
                 "last_quote_receipt":"e"*64}
        self.out=grade(self.plan,0,self.pred,[bar],[receipt],self.anchor+HORIZON_MS+2000,"f"*64)
        self.pred_path=self.root/"prediction-000.json"
        self.out_path=self.root/"outcome-000.json"
        self._write(self.pred_path,self.pred)
        self.out["prediction_sha256"]=sha_file(self.pred_path)
        self._write(self.out_path,self.out)

    def _write(self,path,item):
        path.write_text(json.dumps(item,sort_keys=True,separators=(",",":"))+"\n")

    def _verify(self,out=None,pred=None,file_sha=None):
        return verify_saved_outcome(
            self.plan,0,self.out if out is None else out,
            self.pred if pred is None else pred,
            sha_file(self.pred_path) if file_sha is None else file_sha)

    def test_good_precommitted_observation_survives_restart(self):
        self.assertEqual(self._verify()["status"],"OBSERVED")
        self.assertEqual(outcome_list(self.root,self.plan),[self.out])

    def test_brier_forged_after_seal_cannot_be_scored(self):
        fake=copy.deepcopy(self.out)
        fake["model"]["brier"]+=.1
        with self.assertRaisesRegex(ContractError,"PERSISTED_SCORE"):
            self._verify(out=fake)

    def test_nll_forged_after_seal_cannot_be_scored(self):
        fake=copy.deepcopy(self.out)
        fake["train_frequency_prior"]["nll"]+=.1
        with self.assertRaisesRegex(ContractError,"PERSISTED_SCORE"):
            self._verify(out=fake)

    def test_wrong_top1_hit_fails_even_if_other_metrics_valid(self):
        fake=copy.deepcopy(self.out)
        fake["model"]["hit"]=not fake["model"]["hit"]
        with self.assertRaisesRegex(ContractError,"PERSISTED_SCORE"):
            self._verify(out=fake)

    def test_outcome_target_must_match_actual_return_bins(self):
        fake=copy.deepcopy(self.out)
        fake["actual_class"]=(fake["actual_class"]+1)%7
        with self.assertRaisesRegex(ContractError,"PERSISTED_OUTCOME"):
            self._verify(out=fake)

    def test_tampered_prediction_file_sha_during_restart_fails(self):
        tampered=copy.deepcopy(self.pred)
        tampered["model_probs"]=[1,0,0,0,0,0,0]
        self._write(self.pred_path,tampered)
        with self.assertRaisesRegex(ContractError,"PREDICTION_CHANGED|PREDICTION_FILE_SHA"):
            outcome_list(self.root,self.plan)

    def test_wrong_sha_in_existing_outcome_rejected(self):
        fake={**self.out,"prediction_sha256":"a"*64}
        with self.assertRaisesRegex(ContractError,"PREDICTION_FILE_SHA"):
            self._verify(out=fake)

    def test_forecast_model_id_mutation_with_updated_digest_fails(self):
        tampered=copy.deepcopy(self.pred)
        tampered["model_sha256"]="c"*64
        tampered["prediction_id"]=digest({k:v for k,v in tampered.items() if k!="prediction_id"})
        self._write(self.pred_path,tampered)
        revised=copy.deepcopy(self.out)
        revised["prediction_sha256"]=sha_file(self.pred_path)
        revised["prediction_id"]=tampered["prediction_id"]
        with self.assertRaisesRegex(ContractError,"SAVED_PREDICTION"):
            self._verify(out=revised,pred=tampered)

    def test_prior_forgery_even_with_fresh_prediction_hash_refused(self):
        forged=copy.deepcopy(self.pred)
        forged["prior_probs"]=[1.,0.,0.,0.,0.,0.,0.]
        forged["prediction_id"]=digest({k:v for k,v in forged.items() if k!="prediction_id"})
        self._write(self.pred_path,forged)
        result=copy.deepcopy(self.out)
        result["prediction_sha256"]=sha_file(self.pred_path)
        result["prediction_id"]=forged["prediction_id"]
        with self.assertRaisesRegex(ContractError,"SAVED_PREDICTION"):
            self._verify(out=result,pred=forged)

    def test_recovered_observation_cannot_repair_missed_deadline(self):
        late={**self.out,"at_ms":self.anchor+HORIZON_MS+INPUT_GRACE_MS+1}
        with self.assertRaisesRegex(ContractError,"PERSISTED_OUTCOME"):
            self._verify(out=late)

    def test_observed_claim_with_no_sealed_forecast_is_rejected(self):
        with self.assertRaisesRegex(ContractError,"CENSORED_WITHOUT_FORECAST"):
            verify_saved_outcome(self.plan,0,self.out,None,None)

    def test_original_missing_input_can_be_replayed_only_as_censored(self):
        missing={"schema":OUTCOME_SCHEMA,"plan_id":self.plan["plan_id"],
                 "slot":0,"anchor_ms":self.anchor,
                 "at_ms":self.anchor+INPUT_GRACE_MS+10,
                 "status":"CENSORED","reason":"MISSED_PRECOMMITTED_INPUT_SLOT",
                 "automatic_trading":False,"broker_orders_sent":0,"model_promotion":False}
        self.assertEqual(verify_saved_outcome(self.plan,0,missing,None,None)["status"],"CENSORED")
        self.pred_path.unlink()
        self._write(self.out_path,missing)
        self.assertEqual(outcome_list(self.root,self.plan),[missing])

    def test_missing_input_cannot_forge_arbitrary_censor(self):
        missing={"schema":OUTCOME_SCHEMA,"plan_id":self.plan["plan_id"],
                 "slot":0,"anchor_ms":self.anchor,
                 "at_ms":self.anchor+HORIZON_MS,
                 "status":"CENSORED","reason":"SOME_FAVORABLE_BACKFILLED_REASON",
                 "automatic_trading":False,"broker_orders_sent":0,"model_promotion":False}
        with self.assertRaisesRegex(ContractError,"CENSORED_WITHOUT_FORECAST"):
            verify_saved_outcome(self.plan,0,missing)

    def test_registered_prediction_censor_before_future_horizon_is_invalid(self):
        censored={"schema":OUTCOME_SCHEMA,"plan_id":self.plan["plan_id"],
                  "slot":0,"anchor_ms":self.anchor,"at_ms":self.anchor+INPUT_GRACE_MS,
                  "prediction_sha256":sha_file(self.pred_path),
                  "status":"CENSORED","reason":"ORIGINAL_SOURCE_OUTCOME_UNAVAILABLE",
                  "automatic_trading":False,"broker_orders_sent":0,"model_promotion":False}
        with self.assertRaisesRegex(ContractError,"CENSORED_WITH_FORECAST"):
            self._verify(out=censored)

    def test_late_censored_original_outcome_is_preserved(self):
        censored={"schema":OUTCOME_SCHEMA,"plan_id":self.plan["plan_id"],
                  "slot":0,"anchor_ms":self.anchor,"at_ms":self.anchor+HORIZON_MS+INPUT_GRACE_MS+1,
                  "prediction_sha256":sha_file(self.pred_path),
                  "status":"CENSORED","reason":"OUTCOME_COMMITTED_DEADLINE_MISSED",
                  "automatic_trading":False,"broker_orders_sent":0,"model_promotion":False}
        self.assertEqual(self._verify(out=censored)["status"],"CENSORED")

    def test_persisted_return_bps_must_be_finite(self):
        forged={**self.out,"return_bps":float("nan")}
        with self.assertRaisesRegex(ContractError,"PERSISTED_OUTCOME"):
            self._verify(out=forged)

    def test_no_price_score_can_be_added_to_failed_slot(self):
        censored={"schema":OUTCOME_SCHEMA,"plan_id":self.plan["plan_id"],
                  "slot":0,"anchor_ms":self.anchor,"at_ms":self.anchor+HORIZON_MS+1,
                  "prediction_sha256":sha_file(self.pred_path),
                  "status":"CENSORED","reason":"ORIGINAL_SOURCE_OUTCOME_UNAVAILABLE",
                  "model":{"brier":0.0},"automatic_trading":False,
                  "broker_orders_sent":0,"model_promotion":False}
        with self.assertRaisesRegex(ContractError,"CENSORED_WITH_FORECAST"):
            self._verify(out=censored)

    def test_altered_persisted_model_source_cannot_be_reclassified(self):
        forged=copy.deepcopy(self.pred)
        forged["source_asof_ms"]=self.anchor-1
        forged["prediction_id"]=digest({k:v for k,v in forged.items() if k!="prediction_id"})
        self._write(self.pred_path,forged)
        changed={**self.out,"prediction_sha256":sha_file(self.pred_path),
                 "prediction_id":forged["prediction_id"]}
        with self.assertRaisesRegex(ContractError,"SAVED_PREDICTION"):
            self._verify(out=changed,pred=forged)


if __name__=="__main__":unittest.main()
