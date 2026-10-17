"""Adversarial no-Broker tests for fixed Pocket4 Value343 future shadow."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import tempfile
import time
import unittest
from unittest.mock import patch

from laya_cell import value343_prospective as shadow
from laya_cell.value343_oos import FEATURES, make_features


def source_example():
    manifest=shadow.registration()
    events,requests=shadow.original_events(manifest)
    return deepcopy(next(iter(requests.values()))["data"])


def synthetic_request(now):
    r=source_example()
    delta=(now-1000)-r["started_at_ms"]
    r["started_at_ms"]+=delta
    for q in r["history_snapshot"]:
        q["observed_at_ms"]+=delta
    r["position_at_request"]=0
    r["model_id"]=shadow.EXPECTED_MODEL
    r["prompt_sha256"]=hashlib.sha256(r["prompt"].encode()).hexdigest()
    r["request_id"]="test-research-"+str(now)
    return r


def event(request,now):
    return {"kind":"REQUEST","data":request,"sha256":"a"*64,
            "captured_at_ms":now-550,"written_at_ms":now-400,
            "_witness":{"path":"/private/original","bytes":123,
                        "sha256":"b"*64}}


class Value343ProspectiveContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model=shadow.read_model()

    def test_frozen_335_historical_is_not_admitted_trading_policy(self):
        study=self.model[0]
        self.assertEqual(study["train_rows"],217)
        self.assertEqual(study["historical_holdout_rows"],67)
        self.assertEqual(study["model"]["actual_optimizer_steps"],20)
        self.assertFalse(study["model_mae_beats_train_median"])
        self.assertFalse(study["economic_promotion_supported"])

    def test_both_predecision_sides_use_same_preopen_source(self):
        r=synthetic_request(int(time.time()*1000))
        p,median=shadow.predict_two_sides(r,self.model)
        self.assertEqual(set(p),{"BUY","SELL"})
        self.assertEqual(len(FEATURES),17)
        self.assertTrue(all(-800<=v<=800 and math.isfinite(v) for v in p.values()))
        self.assertTrue(math.isfinite(median))
        self.assertEqual(p,shadow.predict_two_sides(deepcopy(r),self.model)[0])

    def test_pretrade_feature_rejects_quote_from_future(self):
        r=synthetic_request(int(time.time()*1000))
        r["history_snapshot"][-1]["observed_at_ms"]=r["started_at_ms"]+1
        with self.assertRaises(Exception):shadow.predict_two_sides(r,self.model)

    def test_unbound_prompt_and_nonflat_input_rejected(self):
        r=synthetic_request(int(time.time()*1000))
        r["prompt_sha256"]="f"*64
        with self.assertRaises(ValueError):shadow.predict_two_sides(r,self.model)
        r=synthetic_request(int(time.time()*1000))
        r["position_at_request"]=1.
        with self.assertRaises(ValueError):shadow.predict_two_sides(r,self.model)

    def test_causal_journal_chain_and_actual_study_model_are_readable(self):
        manifest=shadow.registration()
        events,requests=shadow.original_events(manifest)
        self.assertGreater(len(events),1000)
        self.assertGreater(len(requests),100)
        self.assertTrue(all(ev["data"]["model_id"]==shadow.EXPECTED_MODEL
                for ev in list(requests.values())[:10]))

    def test_create_only_never_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"one.json"
            shadow.write_once(p,{"broker_orders_sent":0})
            with self.assertRaises(FileExistsError):
                shadow.write_once(p,{"broker_orders_sent":0})

    def test_future_request_seals_two_sides_once(self):
        now=int(time.time()*1000)
        req=synthetic_request(now)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for sub in ("predictions","misses","scores","rejects"):
                (root/sub).mkdir()
            freeze={"activated_at_ms":now-5000,
                    "model_report_id":self.model[0]["report_id"],
                    "source_period_sha256":"d"*64}
            ev=event(req,now)
            with patch.object(shadow,"OUT",root):
                first=shadow.capture([ev],{req["request_id"]:ev},freeze,self.model)
                self.assertEqual(first["new_valid_predictions"],1)
                path=root/"predictions"/(req["request_id"]+".json")
                self.assertTrue(path.exists())
                saved=json.loads(path.read_bytes())
                self.assertEqual(set(saved["predictions_after_actual_reported_fees_usd"]),
                                 {"BUY","SELL"})
                self.assertTrue(saved["selected_side_unknown_before_response"])
                self.assertFalse(saved["automatic_trading"])
                second=shadow.capture([ev],{req["request_id"]:ev},freeze,self.model)
                self.assertEqual(second["already_sealed"],1)
                self.assertEqual(second["new_valid_predictions"],0)

    def test_request_with_already_recorded_response_is_never_predicted(self):
        now=int(time.time()*1000)
        req=synthetic_request(now)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for n in ("predictions","misses","scores","rejects"):
                (root/n).mkdir()
            freeze={"activated_at_ms":now-5000,"model_report_id":"z",
                    "source_period_sha256":"z"}
            ev=event(req,now)
            response={"kind":"RESPONSE","data":{"request_id":req["request_id"]}}
            with patch.object(shadow,"OUT",root):
                out=shadow.capture([ev,response],{req["request_id"]:ev},
                                   freeze,self.model)
                self.assertEqual(out["new_missed"],1)
                self.assertFalse(list((root/"predictions").iterdir()))
                miss=json.loads(next((root/"misses").iterdir()).read_bytes())
                self.assertEqual(miss["reason"],"RESPONSE_ALREADY_RECORDED")

    def test_nonflat_original_request_is_counted_as_missed(self):
        now=int(time.time()*1000)
        req=synthetic_request(now)
        req["position_at_request"]=25000.0
        ev=event(req,now)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for n in ("predictions","misses","scores","rejects"):
                (root/n).mkdir()
            with patch.object(shadow,"OUT",root):
                r=shadow.capture([ev],{req["request_id"]:ev},
                                 {"activated_at_ms":now-5000},self.model)
                self.assertEqual(r["new_missed"],1)
                miss=json.loads(next((root/"misses").iterdir()).read_bytes())
                self.assertEqual(miss["reason"],
                    "NON_FLAT_REQUEST_OUTSIDE_TRAINED_VALUE_SCOPE")
                self.assertFalse(list((root/"predictions").glob("*.json")))

    def test_stale_unsealed_request_is_not_backfilled(self):
        now=int(time.time()*1000)
        req=synthetic_request(now)
        req["started_at_ms"]=now-shadow.MAX_REQUEST_AGE_MS-5000
        ev=event(req,now)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for n in ("predictions","misses","scores","rejects"):
                (root/n).mkdir()
            with patch.object(shadow,"OUT",root):
                out=shadow.capture([ev],{req["request_id"]:ev},
                          {"activated_at_ms":now-200000},self.model)
                self.assertEqual(out["new_missed"],1)

    def test_no_predeployment_historical_request_regenerated(self):
        now=int(time.time()*1000)
        req=synthetic_request(now)
        ev=event(req,now)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for n in ("predictions","misses","scores","rejects"):
                (root/n).mkdir()
            with patch.object(shadow,"OUT",root):
                out=shadow.capture([ev],{req["request_id"]:ev},
                          {"activated_at_ms":now+100},self.model)
                self.assertEqual(out["new_valid_predictions"],0)
                self.assertFalse(list((root/"predictions").iterdir()))

    def test_wrong_source_study_pin_refuses_boot(self):
        with patch.object(shadow,"STUDY_SHA","0"*64):
            with self.assertRaises(ValueError):shadow.read_model()

    def test_future_score_requires_original_decision_after_sealing(self):
        now=int(time.time()*1000)
        req=synthetic_request(now)
        rid=req["request_id"]
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for n in ("predictions","misses","scores","rejects"):
                (root/n).mkdir()
            sealed=now
            shadow.write_once(root/"predictions"/(rid+".json"),
                {"request_id":rid,"input_request_sha256":shadow.digest(req),
                 "sealed_at_ms":sealed,
                 "predictions_after_actual_reported_fees_usd":{"BUY":-4.,"SELL":-8.},
                 "train_only_median_baseline_usd":-12.})
            fake_reward={"reward_id":"r"*64,"input_id":shadow.digest(req),
                         "input_attribution":{"producer_manifest_sha256":shadow.COHORT}}
            src=root/"rewards.json"
            src.write_text(json.dumps({"broker_orders_sent":0,"rewards":[fake_reward]}))
            binding={"request_id":rid,"request_sha256":shadow.digest(req),
                     "command":{"created_at_ms":now+2000,"side":"BUY"}}
            prior_event={"kind":"DECISION","data":{"request_id":rid,
                         "request_sha256":shadow.digest(req),
                         "available_at_ms":now+1000}}
            bind_event={"kind":"COMMAND_BINDING","data":binding}
            sample={"data":req}
            with patch.object(shadow,"OUT",root),\
                 patch.object(shadow.prior,"REWARDS",src),\
                 patch.object(shadow.prior,"original_oms_events",
                              return_value=({},{})),\
                 patch.object(shadow.prior,"settled_time",
                              return_value=now+5000),\
                 patch.object(shadow.prior,"verify_100k_reward",
                              return_value={"net_usd":"-5"}):
                out=shadow.score([prior_event,bind_event],{rid:sample},{})
                self.assertEqual(out["new_scores"],1)
                scored=json.loads((root/"scores"/(rid+".json")).read_bytes())
                self.assertEqual(scored["actual_net_after_reported_fees_usd"],-5.)
                self.assertEqual(scored["model_absolute_error_usd"],1.)
                self.assertTrue(scored["no_counterfactual_buy_sell_conclusion"])
                # New copy with a pre-decision violation must be rejected.
                (root/"scores"/(rid+".json")).unlink()
                prior_event["data"]["available_at_ms"]=now
                out=shadow.score([prior_event,bind_event],{rid:sample},{})
                self.assertEqual(out["new_rejected"],1)
                self.assertTrue((root/"rejects"/(rid+".json")).exists())


if __name__=="__main__":unittest.main()
