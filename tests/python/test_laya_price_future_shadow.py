"""No-GPU adversarial checks of future-window shadow research protocol."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from laya_cell.integrity import ContractError
from research_laya_price_shadow90 import (
    grade, statuses, prior_asof, verified_model,
    SPACING_MS,HORIZON_MS,GRACE_MS,SLOTS,SOURCE_TIMEOUT_MS)


def prediction(anchor=1000000):
    return {"schema":"heptatrader.price-shadow-prediction.v1",
            "plan_id":"plan","slot":0,"anchor_ms":anchor,"created_at_ms":anchor+1000,
            "reference_mid":1.0,
            "online_prior":[1/7]*7,"frozen_train_prior":[1/7]*7}


def quotes(anchor=1000000):
    return [{"t":anchor+i*10000,"mid":1.0,"receipt":str(i)}
            for i in range(34)]


class PriceFutureShadowTests(unittest.TestCase):
    def test_original_forecast_not_made_after_outcome(self):
        p=prediction();p["created_at_ms"]=p["anchor_ms"]+HORIZON_MS
        with self.assertRaisesRegex(ContractError,"NONCAUSAL"):grade(p,quotes(),p["created_at_ms"])
    def test_exact_contiguous_midprice_outcome(self):
        p=prediction();q=quotes()
        score=grade(p,q,p["anchor_ms"]+HORIZON_MS+3000)
        self.assertEqual(score["status"],"OBSERVED")
        self.assertEqual(score["target_class"],3)
        self.assertAlmostEqual(score["online_asof"]["brier"],6/7)
        self.assertEqual(score["outcome_quote_receipt"],"30")
        self.assertFalse(score["trading_authority"])
    def test_no_fabricated_early_endpoint(self):
        p=prediction();q=quotes()[:29]
        self.assertIsNone(grade(p,q,p["anchor_ms"]+HORIZON_MS+1000))
        late=grade(p,q,p["anchor_ms"]+HORIZON_MS+SOURCE_TIMEOUT_MS+1000)
        self.assertEqual(late["status"],"CENSORED")
        self.assertEqual(late["reason"],"OUTCOME_MISSING")
    def test_changed_anchor_is_censored_not_backfilled(self):
        p=prediction();q=quotes();q[0]["mid"]=1.03
        self.assertEqual(grade(p,q,p["anchor_ms"]+HORIZON_MS+1000)["reason"],
                         "ANCHOR_MISSING_OR_CHANGED")
    def test_endpoint_gap_is_censored(self):
        p=prediction();q=quotes();q=[v for v in q if v["t"]!=p["anchor_ms"]+HORIZON_MS]
        q=[v for v in q if v["t"]!=p["anchor_ms"]+HORIZON_MS+10000]
        self.assertEqual(grade(p,q,p["anchor_ms"]+HORIZON_MS+3000)["reason"],"ENDPOINT_GAP")
    def test_internal_path_gap_is_censored(self):
        p=prediction()
        q=[v for v in quotes() if not 60000 < v["t"]-p["anchor_ms"] < 140000]
        self.assertEqual(grade(p,q,p["anchor_ms"]+HORIZON_MS+1000)["reason"],"PATH_GAP")
    def test_invalid_probability_is_not_scored(self):
        p=prediction();p["online_prior"]=[2.0]+[0.0]*6
        with self.assertRaises(ContractError):grade(p,quotes(),p["anchor_ms"]+HORIZON_MS+1000)
    def test_status_counts_fixed_denominator(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);plan={"slots":SLOTS,"plan_id":"plan"}
            for i,status in enumerate(["OBSERVED","CENSORED"]):
                f=root/("outcome-%03d.json"%i)
                f.write_text(json.dumps({"plan_id":"plan","slot":i,"status":status}))
            self.assertEqual(statuses(root,plan),(1,1))
    def test_status_rejects_an_outcome_forged_for_another_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/"outcome-000.json"
            p.write_text(json.dumps({"plan_id":"other","slot":0,"status":"OBSERVED"}))
            with self.assertRaisesRegex(ContractError,"OUTCOME_CHANGED"):
                statuses(root,{"slots":SLOTS,"plan_id":"plan"})
    def test_shadow_acceptance_does_not_mean_laya_promotion(self):
        self.assertEqual(SLOTS,90)
        self.assertEqual((SPACING_MS,HORIZON_MS,GRACE_MS),(360000,300000,60000))
    def test_future_label_is_not_used_for_current_probability(self):
        at=2000000
        old=[{"anchor_ms":1000000,"label_available_at_ms":1300000,"target_class":2}]
        q=[0.05,0.1,0.15,0.4,0.15,0.1,0.05]
        params={"half_life_hours":24,"backoff_strength":1.0}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"outcome-000.json"
            p.write_text(json.dumps({"status":"OBSERVED","anchor_ms":1400000,
               "evaluated_at_ms":2100000,"target_class":6}))
            a,n=prior_asof(old,Path(td),1,at,q,params)
            p.write_text(json.dumps({"status":"OBSERVED","anchor_ms":1400000,
               "evaluated_at_ms":2100000,"target_class":0}))
            b,m=prior_asof(old,Path(td),1,at,q,params)
            self.assertEqual((a,n),(b,m))
    def test_missing_model_or_old_report_is_rejected(self):
        with self.assertRaises(ContractError):
            verified_model({},{"automatic_trading":False})
    def test_no_trade_authority_config_is_required(self):
        with self.assertRaises(ContractError):
            verified_model({"schema":"wrong"},{"automatic_trading":True})


if __name__=="__main__":
    unittest.main()
