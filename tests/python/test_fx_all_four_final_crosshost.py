"""Hostile tests for 4x4 frozen original FX crosshost closeout; no broker client."""
import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

from train_v2.fx_all_four_final_crosshost import (
    SCHEMA, ANCHOR_10_05, BEFORE_LAST_DUE_MS,
    exact_maturity, verify_4, CrosshostHold, HORIZONS
)
from train_v2.fx_first_three_original_crosshost import (
    PLAN_SHA, SEAL_SHA, TRIAL, DECISION_REJECT, MATURITY_REJECT, sha
)

def fake_first():
    return {
       "original_decisions_censored":3,
       "original_maturity_attempts_censored":12,
       "original_plan_sha256":PLAN_SHA,
       "original_seal_sha256":SEAL_SHA,
       "window_zero_full_health_snapshot_unattested":True,
       "later_ready_health_snapshots_were_stale_at_original_decisions":True,
       "price_only_matured_labels":0,
       "first_three":[{"original_decision_sha256":str(i)*64} for i in range(3)]
    }

def fake_cache():
    return {
       "original_entry_state":"CENSORED",
       "original_entry_health_age_ms":85031,
       "source_health_exact_time_comparison":{
          "baseline":{"age_ms":86092},"canary":{"age_ms":18000}},
       "broker_queries_or_orders_sent_by_auditor":0,
       "production_sla_rollout_authorized":False
    }

def decision():
    return {"schema":TRIAL+".attempt.v1",
       "stage":"decision","state":"CENSORED","reason_code":DECISION_REJECT,
       "attempt_recorded_at_ms":ANCHOR_10_05+1001,
       "broker_orders_sent":0, "trade_authority":False,
       "automatic_retry_allowed":False,
       "model_promotion_allowed":False,
       "historical_price_or_future_quote_backfilled":False}

def mature(h):
    return {"schema":TRIAL+".attempt.v1",
        "stage":f"maturity-{h:03d}","state":"CENSORED",
        "reason_code":MATURITY_REJECT,
        "attempt_recorded_at_ms":ANCHOR_10_05+h*60000+6500,
        "automatic_retry_allowed":False,
        "historical_price_or_future_quote_backfilled":False,
        "broker_orders_sent":0,"broker_queries_sent":0,
        "trade_authority":False,"model_promotion_allowed":False}

class FinalCohortTests(unittest.TestCase):
    def test_true_immutable_negative_maturity(self):
        self.assertEqual(exact_maturity(mature(15),3,15,ANCHOR_10_05)["state"],"CENSORED")
    def test_positive_price_label_refused(self):
        r=mature(30);r["state"]="MATURE_PRICE_ONLY"
        with self.assertRaisesRegex(CrosshostHold,"FOURTH_MATURITY_RELABELED"):
            exact_maturity(r,3,30,ANCHOR_10_05)
    def test_late_forged_attempt_refused(self):
        r=mature(120);r["attempt_recorded_at_ms"]+=10000
        with self.assertRaisesRegex(CrosshostHold,"FOURTH_MATURITY_RELABELED"):
            exact_maturity(r,3,120,ANCHOR_10_05)
    def test_forged_or_backfilled_data_refused(self):
        r=mature(60);r["historical_price_or_future_quote_backfilled"]=True
        with self.assertRaises(CrosshostHold):
            exact_maturity(r,3,60,ANCHOR_10_05)
    def test_trading_authority_refused(self):
        r=mature(120);r["broker_orders_sent"]=1
        with self.assertRaises(CrosshostHold):
            exact_maturity(r,3,120,ANCHOR_10_05)
    def test_maturity_wrong_horizon_refused(self):
        with self.assertRaises(CrosshostHold):
            exact_maturity(mature(15),3,30,ANCHOR_10_05)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.first=Path(self.tmp.name)/"first"
        self.cache=Path(self.tmp.name)/"cache"
        self.fourth=Path(self.tmp.name)/"fourth"
        for d in (self.first,self.cache,self.fourth):d.mkdir(mode=0o700)
        self.write(self.cache/"original-entry-attempt.json",decision())
        self.write(self.fourth/"decision-attempt.json",decision())
    def write(self,path,obj):
        path.write_text(json.dumps(obj,sort_keys=True)+"\n")
        path.chmod(0o600)
    def evaluate(self,*,now=BEFORE_LAST_DUE_MS+1000,complete=False):
        with patch("train_v2.fx_all_four_final_crosshost.verify_first_three",return_value=fake_first()),patch("train_v2.fx_all_four_final_crosshost.verify_1005",return_value=fake_cache()):
            return verify_4(firstthree_root=self.first,cache1005_root=self.cache,
                fourth_root=self.fourth,audit_now_ms=now,require_complete=complete)
    def test_partial_never_fakes_future_maturity(self):
        for h in (15,30):self.write(self.fourth/f"maturity-{h:03d}-attempt.json",mature(h))
        r=self.evaluate(now=ANCHOR_10_05+30*60000+8000)
        self.assertEqual(r["result"],"PARTIAL_AWAIT_ORIGINAL_FUTURE_MATURITIES")
        self.assertEqual(r["original_complete_matured_censored_count"],14)
        self.assertEqual(r["fourth_future_horizons_pending"],[60,120])
        self.assertFalse(r["result_is_production_release"])
    def test_15_of_16_is_partial(self):
        for h in (15, 30, 60):
            self.write(self.fourth/f"maturity-{h:03d}-attempt.json", mature(h))
        result = self.evaluate(now=ANCHOR_10_05+120*60000-1000)
        self.assertEqual(result["original_complete_matured_censored_count"], 15)
        self.assertEqual(result["fourth_future_horizons_pending"], [120])
        self.assertEqual(result["result"], "PARTIAL_AWAIT_ORIGINAL_FUTURE_MATURITIES")
        self.assertFalse(result["result_is_production_release"])
        self.assertEqual(result["price_labels_proven"], 0)

    def test_missing_past_maturity_is_failure_not_pending(self):
        with self.assertRaisesRegex(CrosshostHold,"MISSING_FOURTH_MATURITY_AFTER"):
            self.evaluate(now=BEFORE_LAST_DUE_MS+1000)
    def test_require_complete_disallows_partial(self):
        for h in (15,30):self.write(self.fourth/f"maturity-{h:03d}-attempt.json",mature(h))
        with self.assertRaisesRegex(CrosshostHold,"MISSING_FOURTH_MATURITY_AFTER"):
            self.evaluate(now=BEFORE_LAST_DUE_MS+1000,complete=True)
    def test_exact_4x4_final_only_after_deadline(self):
        for h in HORIZONS:self.write(self.fourth/f"maturity-{h:03d}-attempt.json",mature(h))
        r=self.evaluate(complete=True)
        self.assertEqual(r["original_complete_matured_censored_count"],16)
        self.assertEqual(r["original_decision_censored_count"],4)
        self.assertEqual(r["result"],"COMPLETE_4X4_CENSORED_RESEARCH_PROVEN_NO_TRADE")
        self.assertEqual(r["price_labels_proven"],0)
        self.assertFalse(r["trade_authority"])
        self.assertFalse(r["model_promotion_allowed"])
    def test_early_false_final_refused(self):
        for h in HORIZONS:self.write(self.fourth/f"maturity-{h:03d}-attempt.json",mature(h))
        with self.assertRaisesRegex(CrosshostHold,"FULL_FOUR_WINDOW_AUDIT_PREMATURE"):
            self.evaluate(now=ANCHOR_10_05+120*60000+7000,complete=True)
    def test_forged_trade_on_original_decision_refused(self):
        d=decision();d["trade_authority"]=True
        self.write(self.fourth/"decision-attempt.json",d)
        with self.assertRaisesRegex(CrosshostHold,"FOURTH_ORIGINAL_ENTRY"):
            self.evaluate(now=ANCHOR_10_05+60000)
    def test_forged_postentry_model_prediction_refused(self):
        (self.fourth/"preaction.json").write_text('{"forecast":"positive"}')
        with self.assertRaisesRegex(CrosshostHold,"CENSORED_FOURTH_ENTRY_CANNOT_HAVE_PREACTION"):
            self.evaluate(now=ANCHOR_10_05+60000)
    def test_wrong_origin_sha_refused(self):
        changed=decision();changed["attempt_recorded_at_ms"]+=1
        self.write(self.fourth/"decision-attempt.json",changed)
        with self.assertRaisesRegex(CrosshostHold,"FOURTH_ORIGINAL_ENTRY"):
            self.evaluate(now=ANCHOR_10_05+60000)
    def test_wrong_first_three_results_refused(self):
        bad=fake_first();bad["original_maturity_attempts_censored"]=11
        with patch("train_v2.fx_all_four_final_crosshost.verify_first_three",return_value=bad),patch("train_v2.fx_all_four_final_crosshost.verify_1005",return_value=fake_cache()):
            with self.assertRaisesRegex(CrosshostHold,"FIRST_THREE_INDEPENDENT_CLOSURE_CHANGED"):
                verify_4(firstthree_root=self.first,cache1005_root=self.cache,fourth_root=self.fourth,
                    audit_now_ms=ANCHOR_10_05+60000)
    def test_wrong_original_vs_cache_verdict_refused(self):
        bad=fake_cache();bad["production_sla_rollout_authorized"]=True
        with patch("train_v2.fx_all_four_final_crosshost.verify_first_three",return_value=fake_first()),patch("train_v2.fx_all_four_final_crosshost.verify_1005",return_value=bad):
            with self.assertRaisesRegex(CrosshostHold,"ORIGINAL_1005_VERSUS_SANDBOX_CACHE_RESULT_CHANGED"):
                verify_4(firstthree_root=self.first,cache1005_root=self.cache,fourth_root=self.fourth,
                    audit_now_ms=ANCHOR_10_05+60000)

if __name__=="__main__": unittest.main()
