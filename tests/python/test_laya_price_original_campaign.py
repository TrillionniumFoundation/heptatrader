"""Read-only fixed 90-slot original PAPER price campaign adversarial controls."""
import copy
import json
import math
import unittest
from laya_cell.price_original_campaign import (
    SCHEMA,SLOTS,HORIZON_MS,MIN_OBSERVED,INPUT_GRACE_MS,
    check_probs,create_plan,check_plan,expected_anchor,prediction,grade,state_summary)
from laya_cell.integrity import ContractError,digest


PINS={k:("a"*64 if i%2 else "b"*64) for i,k in enumerate(
    ("model_weights","revised_bars","frozen_folds","frozen_selection","frozen_protocol",
     "runner","feature_module","original_bridge","source_config","contract_module"))}
PRIOR=[.05,.05,.10,.65,.10,.03,.02]


class CampaignContract(unittest.TestCase):
 def setUp(self):
  self.plan=create_plan(1800000000000,PINS,PRIOR,"c"*64)
  self.anchor=expected_anchor(self.plan,0)
  self.feature={"anchor_ms":self.anchor,"source_kind":"ORIGINAL_VERIFIED_PAPER_QUOTES",
                "trade_authority":False,"original_event_ticks_not_revised_ib_bars":True,
                "snapshot_id":"f"*64,"asof_ms":self.anchor+2000,
                "last_closed_bar_age_ms":2000,"future_outcome_was_not_required":True,
                "features":[0.]*24}
  self.pred=prediction(self.plan,0,self.feature,[.1,.1,.1,.4,.1,.1,.1],
                       self.anchor+3000,1.2345,"d"*64,0)
  self.bar={"time":self.anchor//1000,
            "midpoint":{"close":1.2348}}
  self.receipt={"bar_start_ms":self.anchor,
                "bar_end_ms":self.anchor+300000,"last_quote_receipt":"e"*64}
 def test_90_sealed_5m_slots(self):
  self.assertEqual(self.plan["slots"],90)
  self.assertEqual(expected_anchor(self.plan,89)-self.anchor,89*300000)
  self.assertTrue(300000<=self.anchor-1800000000000<=600000)
  self.assertEqual(self.plan["last_outcome_end_ms"],expected_anchor(self.plan,89)+300000)
 def test_no_early_prediction(self):
  with self.assertRaises(ContractError):prediction(self.plan,0,self.feature,PRIOR,self.anchor-1,1.2345,"e"*64,0)
 def test_no_after_grace_prediction(self):
  with self.assertRaises(ContractError):prediction(self.plan,0,self.feature,PRIOR,self.anchor+60001,1.2345,"e"*64,0)
 def test_future_anchor_changing_rejected(self):
  f={**self.feature,"anchor_ms":self.anchor+1}
  with self.assertRaises(ContractError):prediction(self.plan,0,f,PRIOR,self.anchor+3000,1.2345,"e"*64,0)
 def test_source_must_be_original_and_complete(self):
  for key,v in (("source_kind","REVISED_IB_BARS"),("future_outcome_was_not_required",False),
                ("original_event_ticks_not_revised_ib_bars",False),
                ("trade_authority",True),
                ("last_closed_bar_age_ms",61000)):
   f={**self.feature,key:v}
   with self.assertRaises(ContractError):prediction(self.plan,0,f,PRIOR,self.anchor+3000,1.2345,"e"*64,0)
 def test_nonfinite_and_wrong_shape(self):
  for v in ([float("nan")]+[0]*6,[1,0,0],[-1]+[1]*6,[True]+[0]*6):
   with self.assertRaises(ContractError):check_probs(v)
 def test_plan_changed_or_promotion_refused(self):
  for key,value in (("slots",89),("trade_authority",True),("model_promotion",True),
                    ("training_prior_probs",[1,0,0,0,0,0,0])):
   p={**self.plan,key:value}
   with self.assertRaises(ContractError):check_plan(p)
 def test_censored_endpoint_stays_pending_until_real_deadline(self):
  self.assertIsNone(grade(self.plan,0,self.pred,[],[],self.anchor+300001,"x"*64))
  result=grade(self.plan,0,self.pred,[],[],self.anchor+300000+60000,"x"*64)
  self.assertEqual(result["status"],"CENSORED")
 def test_true_original_future_outcome(self):
  self.assertIsNone(grade(self.plan,0,self.pred,[self.bar],[self.receipt],self.anchor+299999,"x"*64))
  r=grade(self.plan,0,self.pred,[self.bar],[self.receipt],self.anchor+302000,"x"*64)
  self.assertEqual(r["status"],"OBSERVED")
  self.assertAlmostEqual(r["return_bps"],(1.2348/1.2345-1)*10000)
  self.assertEqual(r["actual_class"],4)
  self.assertFalse(r["broker_orders_sent"])
  self.assertEqual(r["outcome_quote_receipt"],"e"*64)
 def test_prediction_hash_binding(self):
  p=copy.deepcopy(self.pred)
  p["model_probs"][0]=.2
  with self.assertRaisesRegex(ContractError,"PREDICTION_CHANGED"):
   grade(self.plan,0,p,[self.bar],[self.receipt],self.anchor+301000,"x"*64)
 def test_no_wrong_model_scoring(self):
  p={**self.pred,"model_sha256":"0"*64}
  with self.assertRaises(ContractError):grade(self.plan,0,p,[self.bar],[self.receipt],self.anchor+301000,"x"*64)
 def test_precommit_must_come_before_outcome(self):
  p=copy.deepcopy(self.pred)
  p["created_at_ms"]=self.anchor+HORIZON_MS
  p["prediction_id"]=digest({k:v for k,v in p.items() if k!="prediction_id"})
  with self.assertRaises(ContractError):grade(self.plan,0,p,[self.bar],[self.receipt],self.anchor+302000,"x"*64)
 def test_run_requires_exact_frozen_prior(self):
  self.assertEqual(self.pred["prior_probs"],PRIOR)
  self.assertEqual(self.plan["selection_from_year_fold"],13)
 def test_no_outcome_repairs_or_duplicate_slots(self):
  r=grade(self.plan,0,self.pred,[self.bar],[self.receipt],self.anchor+302000,"x"*64)
  s=state_summary(self.plan,[r])
  self.assertEqual((s["observed"],s["censored"]),(1,0))
  with self.assertRaisesRegex(ContractError,"OUTCOME_IDENTITY"):
   state_summary(self.plan,[r,r])
 def test_frozen_rule_not_relaxed_after_three_censors(self):
  values=[{"schema":"heptatrader.laya-original-price-outcome.v1",
           "slot":i,"plan_id":self.plan["plan_id"],
           "anchor_ms":expected_anchor(self.plan,i),
           "status":"CENSORED","at_ms":expected_anchor(self.plan,i)+300000,
           "reason":"NO_STRICT_CAUSAL_INPUT",
           "automatic_trading":False,"broker_orders_sent":0,
           "model_promotion":False} for i in range(3)]
  self.assertFalse(state_summary(self.plan,values)["minimum_88_of_90_possible"])
 def test_only_ninty_slots_allowable(self):
  with self.assertRaises(ContractError):expected_anchor(self.plan,90)
  with self.assertRaises(ContractError):expected_anchor(self.plan,True)
 def test_model_promotion_is_always_false(self):
  r=grade(self.plan,0,self.pred,[self.bar],[self.receipt],self.anchor+302000,"x"*64)
  self.assertFalse(r["model_promotion"])
  self.assertFalse(self.plan["model_promotion"])
  self.assertFalse(state_summary(self.plan,[r])["formal_laya_qualification"])
 def test_forged_prior_cannot_replace_frozen_baseline(self):
  p=copy.deepcopy(self.pred);p["prior_probs"]=[1,0,0,0,0,0,0]
  p["prediction_id"]=digest({k:v for k,v in p.items() if k!="prediction_id"})
  with self.assertRaisesRegex(ContractError,"PREDICTION_CHANGED"):
   grade(self.plan,0,p,[self.bar],[self.receipt],self.anchor+303000,"x"*64)
 def test_outcome_bar_receipts_cannot_be_swapped(self):
  r={**self.receipt,"bar_start_ms":self.anchor-300000}
  with self.assertRaisesRegex(ContractError,"RESULT_PRICE_OR_RECEIPT"):
   grade(self.plan,0,self.pred,[self.bar],[r],self.anchor+303000,"x"*64)
  with self.assertRaisesRegex(ContractError,"BAR_RECEIPT_LENGTH"):
   grade(self.plan,0,self.pred,[self.bar],[],self.anchor+303000,"x"*64)
 def test_fixed_plan_retains_exact_last_endpoint(self):
  p={**self.plan,"last_outcome_end_ms":self.plan["last_outcome_end_ms"]+300000}
  p["plan_id"]=digest({k:v for k,v in p.items() if k!="plan_id"})
  with self.assertRaisesRegex(ContractError,"PLAN_CHANGED"):check_plan(p)
 def test_expired_original_outcome_never_backfilled_as_observed(self):
  # Even a truly complete original close cannot recover a missed
  # precommitted evidence deadline after the fact.
  end=self.anchor+HORIZON_MS+INPUT_GRACE_MS
  valid=grade(self.plan,0,self.pred,[self.bar],[self.receipt],end,"d"*64)
  self.assertEqual(valid["status"],"OBSERVED")
  late=grade(self.plan,0,self.pred,[self.bar],[self.receipt],end+1,"d"*64)
  self.assertEqual(late["status"],"CENSORED")
  self.assertEqual(late["reason"],"OUTCOME_COMMITTED_DEADLINE_MISSED")
 def test_registered_model_source_hashes_cannot_be_omitted(self):
  missing=dict(PINS);del missing["source_config"]
  with self.assertRaisesRegex(ContractError,"PINS"):create_plan(1800000000000,missing,PRIOR,"c"*64)
 def test_noncausal_feature_vector_rejected(self):
  for key,val in (("features",[float("nan")]*24),("features",[0.]*22),
                  ("snapshot_id","short")):
   bad={**self.feature,key:val}
   with self.assertRaisesRegex(ContractError,"NONCAUSAL_INPUT"):
    prediction(self.plan,0,bad,PRIOR,self.anchor+3000,1.2345,"d"*64,0)
 def test_observation_mutation_not_accepted_as_real_coverage(self):
  out=grade(self.plan,0,self.pred,[self.bar],[self.receipt],self.anchor+303000,"d"*64)
  for changed in ({"model_promotion":True},{"broker_orders_sent":1},
                  {"outcome_end_ms":self.anchor+HORIZON_MS+1},
                  {"model":{"brier":float("nan"),"nll":1.0,"hit":True}},
                  {"actual_class":True}):
   invalid={**out,**changed}
   with self.assertRaises(ContractError):state_summary(self.plan,[invalid])
 def test_censored_without_reason_refused(self):
  out={"schema":"heptatrader.laya-original-price-outcome.v1",
       "slot":0,"plan_id":self.plan["plan_id"],"anchor_ms":self.anchor,
       "status":"CENSORED","at_ms":self.anchor+300000,
       "automatic_trading":False,
       "model_promotion":False,"broker_orders_sent":0}
  with self.assertRaisesRegex(ContractError,"CENSORED_REASON"):
   state_summary(self.plan,[out])
 def test_aggregate_quality_is_observed_only_and_not_promotion(self):
  observed=grade(self.plan,0,self.pred,[self.bar],[self.receipt],
                 self.anchor+303000,"d"*64)
  miss={"schema":"heptatrader.laya-original-price-outcome.v1",
        "plan_id":self.plan["plan_id"],"slot":1,
        "anchor_ms":expected_anchor(self.plan,1),
        "at_ms":expected_anchor(self.plan,1)+300000,
        "status":"CENSORED","reason":"NO_STRICT_CAUSAL_INPUT",
        "automatic_trading":False,"broker_orders_sent":0,"model_promotion":False}
  summary=state_summary(self.plan,[observed,miss])
  self.assertEqual((summary["recorded"],summary["observed"],summary["censored"]),(2,1,1))
  self.assertAlmostEqual(summary["scores_on_observed_only"]["model"]["mean_brier"],
                         observed["model"]["brier"])
  self.assertAlmostEqual(summary["scores_on_observed_only"]["train_frequency_prior"]["mean_nll"],
                         observed["train_frequency_prior"]["nll"])
  self.assertFalse(summary["statistical_qualification"])
  self.assertFalse(summary["financial_alpha_proven"])
 def test_outcome_record_status_identity_must_be_strict(self):
  row=grade(self.plan,0,self.pred,[self.bar],[self.receipt],
             self.anchor+303000,"d"*64)
  for change in ({"schema":"unknown"},{"at_ms":0}):
   fake={**row,**change}
   with self.assertRaisesRegex(ContractError,"OUTCOME_IDENTITY"):
    state_summary(self.plan,[fake])
 def test_input_snapshot_cannot_be_older_than_anchor(self):
  f={**self.feature,"asof_ms":self.anchor-1}
  with self.assertRaisesRegex(ContractError,"NONCAUSAL_INPUT"):
   prediction(self.plan,0,f,PRIOR,self.anchor+3000,1.2345,"d"*64,0)
 def test_all_censored_has_no_fabricated_scores(self):
  miss={"schema":"heptatrader.laya-original-price-outcome.v1",
        "slot":0,"plan_id":self.plan["plan_id"],"anchor_ms":self.anchor,
        "at_ms":self.anchor+300000,
        "status":"CENSORED","reason":"NO_STRICT_CAUSAL_INPUT",
        "automatic_trading":False,"broker_orders_sent":0,"model_promotion":False}
  status=state_summary(self.plan,[miss])
  self.assertIsNone(status["scores_on_observed_only"]["model"]["mean_brier"])
  self.assertIsNone(status["scores_on_observed_only"]["train_frequency_prior"]["mean_nll"])
 def test_source_conflict_no_silent_mid_repair(self):
  bar={**self.bar,"midpoint":{"close":0.0}}
  with self.assertRaises(ContractError):
   grade(self.plan,0,self.pred,[bar],[self.receipt],self.anchor+302000,"x"*64)


if __name__=="__main__":unittest.main()
