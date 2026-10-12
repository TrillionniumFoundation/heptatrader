"""Adversarial independent cohort review; tests copy only private fixtures."""
from __future__ import annotations
import json
import math
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from train_v2.fx_ridge_finite_independent_review import (
    InvalidProof, audit, independent_cost, independent_price_prediction,
    read, strict, verify_seal,
)

class ParserTests(unittest.TestCase):
    def test_duplicate_json_key_rejected(self):
        with self.assertRaisesRegex(InvalidProof, "DUPLICATE_JSON_FIELD"):
            strict(b'{"x":1,"x":2}')
    def test_nan_json_rejected(self):
        with self.assertRaisesRegex(InvalidProof, "NONFINITE_JSON"):
            strict(b'{"x":NaN}')
    def test_causal_source_quote_cannot_arrive_after_bucket(self):
        anchor=1791799800000
        quotes=[]
        for k in range(13):
            end=anchor-(12-k)*300000
            quotes.append({"event_at_ms":end-1000, "available_at_ms":end-900,
                           "bid":"1.1","ask":"1.1"})
        hour=((anchor//1000-300)//3600)%24
        vec=[0.,0.,0.,0.,0.,0.,
             math.sin(2*math.pi*hour/24),math.cos(2*math.pi*hour/24)]
        decision={"history_quote_samples":quotes,"feature_vector":vec,
                  "forecast_midpoint_bps":0.0}
        model={"mean":[0.]*8,"stddev":[1.]*8,"weights":[0.]*9}
        self.assertAlmostEqual(independent_price_prediction(decision,model,anchor),0.)
        quotes[0]["available_at_ms"] += 3000
        with self.assertRaisesRegex(InvalidProof,"NONCAUSAL_ORIGINAL_QUOTE_BUCKET"):
            independent_price_prediction(decision,model,anchor)
    def test_cost_recomputed_no_double_spread(self):
        decision={"history_quote_samples":[{"bid":1.1,"ask":1.1}],
                  "forecast_midpoint_bps":1.0,
                  "cost_hurdle":{"required_absolute_prediction_bps":8.4},
                  "forecast_magnitude_clears_cost":False}
        cost={"quote_spread_full_bps":{"p99":1.},"fx_commission_one_way_bps":{"p90":0.7}}
        # Live spread 1bp, commission roundtrip 1.4bps, conservative reserve 6bps.
        self.assertAlmostEqual(independent_cost(decision,cost),8.4,places=3)
        decision["forecast_magnitude_clears_cost"]=True
        with self.assertRaisesRegex(InvalidProof,"COST_CLEARS_FLAG_NOT_REPRODUCED"):
            independent_cost(decision,cost)

class ActualImmutableCohortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw=os.environ.get("HEPTA_FX_FINITE_FROZEN_ROOT")
        if not raw:
            raise unittest.SkipTest("No original read-only cohort path was provided")
        cls.root=Path(raw)
    def clone(self):
        tmp=tempfile.TemporaryDirectory()
        cloned=Path(tmp.name)/"cohort"
        shutil.copytree(self.root,cloned)
        return tmp,cloned
    def test_unchanged_campaign_consistency(self):
        outcome=audit(self.root)
        self.assertEqual(outcome["frozen_plan_count"],4)
        self.assertFalse(outcome["model_promotion_allowed"])
        self.assertFalse(outcome["trade_authority"])
    def test_registered_source_and_model_bound(self):
        intent,seal=verify_seal(self.root)
        self.assertEqual(len(seal["windows"]),4)
        self.assertEqual(len(set(item["plan_id"] for item in seal["windows"])),4)
        self.assertTrue(intent["no_historical_test_reselection"])
    def test_modify_window_model_bytes_refused(self):
        tmp,root=self.clone()
        try:
            path=root/"window-03/model.json"
            data=json.loads(path.read_text())
            data["weights"][0]+=1
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(InvalidProof,"FROZEN_WINDOW_BYTE_TAMPERING"):
                verify_seal(root)
        finally:tmp.cleanup()
    def test_modify_prior_registration_refused(self):
        tmp,root=self.clone()
        try:
            path=root/"window-01/local-registration.json"
            data=json.loads(path.read_text())
            data["sealed_at_ms"]+=1
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(InvalidProof,"FROZEN_WINDOW_BYTE_TAMPERING"):
                verify_seal(root)
        finally:tmp.cleanup()
    def test_modify_plan_authority_refused(self):
        tmp,root=self.clone()
        try:
            path=root/"window-02/plan.json"
            data=json.loads(path.read_text())
            data["trade_authority"]=True
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(InvalidProof,"FROZEN_WINDOW_BYTE_TAMPERING"):
                verify_seal(root)
        finally:tmp.cleanup()
    def test_modify_seal_after_registration_refused(self):
        tmp,root=self.clone()
        try:
            path=root/"registry/sealed-plans"
            sealed=next(path.glob("*.json"))
            data=json.loads(sealed.read_text())
            data["independent_owner_signature"]=True
            sealed.write_text(json.dumps(data))
            with self.assertRaisesRegex(InvalidProof,"REGISTERED_SEAL_DIFFERS"):
                verify_seal(root)
        finally:tmp.cleanup()
    def test_modify_sqlite_wal_registration_refused(self):
        tmp,root=self.clone()
        try:
            dbpath=root/"registry/registrations.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.execute("UPDATE registrations SET sealed_at_ms=sealed_at_ms+1")
                db.commit()
            with self.assertRaisesRegex(InvalidProof,"SQLITE_REGISTRY_OR_SEAL_MISMATCH"):
                verify_seal(root)
        finally:tmp.cleanup()
    def test_fabricate_past_success_without_frozen_proof_refused(self):
        tmp,root=self.clone()
        try:
            # Use a pristine future window. Any fabricated success must be
            # rejected due to missing original quote/model decision artifacts.
            file=root/"window-03/decision-attempt.json"
            fake={"schema":"heptatrader.fx-ridge-finite-prospective-campaign.v1.attempt.v1",
                  "stage":"decision","state":"FROZEN_PRICE_ONLY",
                  "reason_code":"NONE","attempt_recorded_at_ms":1791803401500,
                  "trade_authority":False,"model_promotion_allowed":False,
                  "broker_orders_sent":0,"broker_requests_sent":0,"nonretryable":True}
            file.write_text(json.dumps(fake));file.chmod(0o600)
            with self.assertRaises((FileNotFoundError,InvalidProof)):
                audit(root)
        finally:tmp.cleanup()
    def test_fabricate_future_maturity_before_decision_refused(self):
        tmp,root=self.clone()
        try:
            f=root/"window-03/maturity-attempt.json"
            fake={"schema":"heptatrader.fx-ridge-finite-prospective-campaign.v1.attempt.v1",
                  "stage":"maturity","state":"MATURE_PRICE_ONLY",
                  "attempt_recorded_at_ms":1791804306500,
                  "trade_authority":False,"model_promotion_allowed":False,
                  "broker_orders_sent":0,"broker_requests_sent":0,"nonretryable":True}
            f.write_text(json.dumps(fake));f.chmod(0o600)
            with self.assertRaisesRegex(InvalidProof,"FUTURE_WITHOUT_PAST"):
                audit(root)
        finally:tmp.cleanup()

if __name__=="__main__":
    unittest.main()
