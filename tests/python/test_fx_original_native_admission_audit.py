"""Adversarial checks for the original-quote-native development admission gate."""
from __future__ import annotations
import hashlib, json, stat, tempfile, unittest
from pathlib import Path
from train_v2.fx_original_native_admission_audit import (
    AdmissionHold, audit, nonoverlap_count, safe_read, strict_json
)

BASE=1800000000000
NATIVE="heptatrader.fx-original-causal-features-and-maturity.v1"
CALENDAR="heptatrader.fx-original-broker-session-coverage-audit.v1"
def blob(v):
    return (json.dumps(v,sort_keys=True,separators=(",",":"))+"\n").encode()
def sha(v):
    return hashlib.sha256(v).hexdigest()
def write(p,v):
    p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    raw=blob(v) if type(v) is dict else v
    p.write_bytes(raw);p.chmod(0o600)
    return sha(raw)

def make_fixture(base):
    root=base/"evidence";root.mkdir(mode=0o700)
    c=root/"broker";c.mkdir(mode=0o700)
    seal=root/"seal.json";drift=root/"drift.json"
    sh=write(seal,{"schema":"sealed"})
    dh=write(drift,{"schema":"drift"})
    request={"kind":"schedule","what":"SCHEDULE"}
    rh=write(c/"request.json",request)
    response={"bars":[],"errors":[],"schedule":{"sessions":[{"start":"2026","end":"2026"}]}}
    resph=write(c/"response.json",response)
    receipth=write(c/"receipt.json",{"state":"RECEIVED","request":request,"response_sha256":resph})
    example={"schema":NATIVE+".example.v1",
             "anchor_ms":BASE,
             "decision_delay_ms":1000,
             "original_input_quote_ids":["%064x"%(i+1) for i in range(13)],
             "input_last_quote_available_at_ms":BASE-1000,
             "input_last_quote_valid_until_ms":BASE+5000,
             "mature_due_ms":BASE+900000,
             "mature_original_quote_event_ms":BASE+900200,
             "mature_original_quote_available_ms":BASE+900300,
             "mature_original_quote_id":"f"*64,
             "price_only_midpoint_return_bps":"0.5",
             "features":[0.0]*6+[0.5,0.8],
             "same_original_source_domain":True,
             "retrospectively_observed":True,
             "independent_prospective_prediction":False,
             "actual_executable_fill":False,"monetary_reward":False}
    examples={"schema":NATIVE+".private-examples.v1",
              "original_preseal_source_prefixes_sha256":"a"*64,
              "research_only":True,"model_fit_authorized":False,
              "retrospective_not_an_oos_test":True,"examples":[example]}
    eh=write(root/"examples.json",examples)
    groups={
      "IB_HISTORICAL_SESSION_OPEN":{"grid_count":2,"causal_quote_close":2,
                                    "missing_quote_close":0,
                                    "original_valid_for_fixed_plus_1000_ms":2,
                                    "quote_age_or_expiry_rejected":0},
      "IB_HISTORICAL_SESSION_BREAK_OR_CLOSED":{"grid_count":0,"causal_quote_close":0,
                                               "missing_quote_close":0,
                                               "original_valid_for_fixed_plus_1000_ms":0,
                                               "quote_age_or_expiry_rejected":0},
      "UNKNOWN_OUTSIDE_BROKER_SCHEDULE_REQUEST":{"grid_count":0,"causal_quote_close":0,
                                                "missing_quote_close":0,
                                                "original_valid_for_fixed_plus_1000_ms":0,
                                                "quote_age_or_expiry_rejected":0}}
    ctr={"anchors_with_close":2,"missing_13_contiguous_endpoints":1,
         "input_source_duplicate":0,"decision_quote_age_or_expiry_hold":0,
         "future_quote_absent":0,"future_quote_late":0,
         "mature_price_only_rows":1}
    timing={str(ms):{"anchors_considered":2,"eligible":2,"too_old":0,
                     "broker_expired":0,"missing_close":0,"invalid_causal":0}
             for ms in (250,500,1000)}
    native={"schema":NATIVE,"frozen_campaign_seal_sha256":sh,
            "source_drift_audit_sha256":dh,
            "original_segment_prefixes_sha256":"a"*64,
            "original_unique_timestamps":200,
            "preseal_cutoff_ms":BASE+1000000,
            "private_examples_sha256":eh,
            "preseal_grid_quote_eligibility":{"all_endpoints":2,"missing_preclose":0,
                                               "expired_before_grid":0,
                                               "new_contract_valid_closes":2},
            "native_mature_price_development_counts":ctr,
            "counterfactual_decision_delays_ms":timing,
            "native_price_development_samples":1,
            "nonoverlap_80min_candidate_anchor_count":1,
            "required_nonoverlap_for_any_new_research_model_fit":1024,
            "research_model_fit_hold_reasons":["INSUFFICIENT_NATIVE_NONOVERLAP_SAMPLE_COUNT"],
            "research_model_fit_admission":False,
            "trade_authority":False,"model_promotion_allowed":False,
            "automatic_training":False,"automatic_trading":False,
            "price_only_alpha_claim":False,
            "market_session_not_independently_attested":True,
            "availability_selection_bias_remains":True,
            "broker_requests_sent":0,"broker_orders_sent":0}
    nh=write(root/"native.json",native)
    calendar={"schema":CALENDAR,
              "source_native_contract_sha256":nh,
              "frozen_campaign_seal_sha256":sh,
              "original_preseal_source_prefixes_sha256":"a"*64,
              "broker_schedule_request_sha256":rh,
              "broker_schedule_receipt_sha256":receipth,
              "broker_schedule_response_sha256":resph,
              "historical_schedule_session_count":1,
              "session_coverage":groups,
              "trade_authority":False,
              "model_promotion_allowed":False,
              "original_trader_behavior_propensity_attested":False,
              "canonical_owner_or_witness_enrolled":False,
              "cached_historical_session_is_not_current_live_authorization":True,
              "no_session_claim_outside_broker_request":True,
              "no_backward_ohlc_fill_or_training":True,
              "broker_requests_sent":0,"broker_orders_sent":0}
    write(root/"calendar.json",calendar)
    return root,{"native":root/"native.json","examples":root/"examples.json",
                 "calendar":root/"calendar.json","broker_chunk":c,
                 "campaign_seal":seal,"previous_drift":drift}

class OriginalAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root,self.p=make_fixture(Path(self.temp.name))
    def check(self):
        return audit(self.p["native"],self.p["examples"],self.p["calendar"],
                     broker_chunk=self.p["broker_chunk"],
                     campaign_seal=self.p["campaign_seal"],
                     previous_drift=self.p["previous_drift"])
    def change(self, name, path, fn):
        d=json.loads(path.read_text());fn(d);write(path,d)
        # downstream content hashes intentionally remain stale: tamper must fail.
    def test_valid_development_population_stays_hold(self):
        r=self.check()
        self.assertEqual(r["native_price_development_rows"],1)
        self.assertEqual(r["nonoverlap_80min_blocks"],1)
        self.assertEqual(r["training_and_trading_admission"],"HOLD")
        self.assertFalse(r["automatic_training"])
    def test_nonoverlap_discards_overlapping_rows(self):
        self.assertEqual(nonoverlap_count([BASE,BASE+300000,BASE+4800000]),2)
    def test_private_example_tamper_refused(self):
        p=self.p["examples"]
        self.change("example",p,lambda d:d["examples"][0].update(monetary_reward=True))
        with self.assertRaisesRegex(AdmissionHold,"DATASET_OR_CALENDAR"):
            self.check()
    def test_censored_relabel_without_hash_not_admitted(self):
        p=self.p["native"]
        self.change("contract",p,lambda d:d.update(research_model_fit_admission=True))
        with self.assertRaisesRegex(AdmissionHold,"DATASET_OR_CALENDAR"):
            self.check()
    def test_missing_future_session_unknown_stratum_refused(self):
        p=self.p["calendar"]
        self.change("calendar",p,lambda d:d["session_coverage"].pop(
            "UNKNOWN_OUTSIDE_BROKER_SCHEDULE_REQUEST"))
        with self.assertRaisesRegex(AdmissionHold,"BROKER_UNKNOWN_SESSION_STRATUM_MISSING"):
            self.check()
    def test_broker_response_edit_refused(self):
        p=self.p["broker_chunk"]/"response.json"
        self.change("response",p,lambda d:d.update(errors=[{"code":162}]))
        with self.assertRaisesRegex(AdmissionHold,"SCHEDULE_BYTES"):
            self.check()
    def test_broker_request_must_remain_schedule(self):
        p=self.p["broker_chunk"]/"request.json"
        self.change("request",p,lambda d:d.update(what="MIDPOINT"))
        with self.assertRaisesRegex(AdmissionHold,"SCHEDULE_BYTES"):
            self.check()
    def test_model_fit_gate_does_not_pass_at_one_row(self):
        p=self.p["native"]
        self.change("report",p,lambda d:d.update(nonoverlap_80min_candidate_anchor_count=1024))
        with self.assertRaisesRegex(AdmissionHold,"DATASET_OR_CALENDAR"):
            self.check()
    def test_missing_original_seal_refused(self):
        self.p["campaign_seal"].unlink()
        with self.assertRaises(FileNotFoundError):self.check()
    def test_external_statement_not_qualified_as_broker_signature(self):
        p=self.p["calendar"]
        self.change("calendar",p,lambda d:d.update(
            cached_historical_session_is_not_current_live_authorization=False))
        with self.assertRaises(AdmissionHold):self.check()
    def test_duplicate_json_fields_refused(self):
        with self.assertRaisesRegex(AdmissionHold,"DUPLICATE_JSON_KEY"):
            strict_json(b'{"x":1,"x":2}')
    def test_nan_json_refused(self):
        with self.assertRaisesRegex(AdmissionHold,"JSON_NONFINITE"):
            strict_json(b'{"x":NaN}')
    def test_private_file_permission_required(self):
        p=self.p["examples"]
        p.chmod(0o644)
        with self.assertRaisesRegex(AdmissionHold,"PRIVATE_SOURCE"):
            self.check()
    def test_private_symlink_required(self):
        p=self.p["native"]
        old=self.root/"native-source.json"
        p.rename(old);p.symlink_to(old)
        with self.assertRaisesRegex(AdmissionHold,"PRIVATE_PATH_REQUIRED"):
            self.check()
    def test_forward_available_only_after_horizon(self):
        p=self.p["examples"]
        self.change("example",p,lambda d:d["examples"][0].update(
            mature_original_quote_available_ms=BASE+899999))
        with self.assertRaises(AdmissionHold):self.check()
    def test_official_schedule_does_not_expand_by_guess(self):
        p=self.p["calendar"]
        self.change("calendar",p,lambda d:d["session_coverage"][
             "IB_HISTORICAL_SESSION_OPEN"].update(grid_count=3))
        with self.assertRaises(AdmissionHold):self.check()

if __name__=="__main__":
    unittest.main()
