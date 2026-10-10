"""Adversarial self-contained fixtures; real trading decisions NEVER invented."""
from __future__ import annotations

from collections import Counter
import copy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from hepta_decision_attribution import (
    SCHEMA as ORIGINAL_SCHEMA, canonical as original_canonical,
    digest as original_digest,
)
from hepta_btc_entry_policy import digest as btc_digest
from train_v2 import original_decision_population as audited


def fixture_graph(action="HOLD", *, instrument="EUR.USD", is_btc=False):
    t = int(time.time() * 1000)-20000
    history = [
        {"observed_at_ms":t-12000+i*1000,
         "bid":"1.001", "ask":"1.003", "mid":1.002}
        for i in range(6)
    ]
    request = {
        "request_id":"real-uuid-fixture", "period_sha256":"a"*64,
        "instrument":instrument, "input_scope":audited.INPUT_SCOPE,
        "model_id":"gpt-6-luna", "started_at_ms":t,
        "prompt":"fixture-only synthetic model prompt",
        "prompt_sha256":hashlib.sha256(b"fixture-only synthetic model prompt").hexdigest(),
        "history_snapshot": history,
    }
    raw_decision = {"action":action,"confidence":0.7,
                    "rationale":"fixture-only"}
    if is_btc:
        raw_decision.update(expected_move_bps=0.5,horizon_sec=900)
    response = {
        "request_id":request["request_id"],
        "request_sha256":original_digest(request),
        "available_at_ms":t+100,
        "returncode":0,
        "stdout":json.dumps({"ok":True,"outputs":[{
            "text":json.dumps(raw_decision,sort_keys=True)}]}),
    }
    selected = dict(raw_decision)
    if is_btc:
        compact=[{"t":v["observed_at_ms"],"bid":v["bid"],
                  "ask":v["ask"],"mid":v["mid"]} for v in history]
        selected.update({
            "issued_at_ms":compact[-1]["t"],"available_at_ms":t+101,
            "reference_mid":str((Decimal(compact[-1]["bid"])+Decimal(compact[-1]["ask"]))/2),
            "input_sha256":btc_digest(compact),
            "model_id":request["model_id"],
            "forecast_schema":"heptatrader.btc-return-forecast.v1",
        })
    decision = {
        "request_id":request["request_id"],
        "request_sha256":original_digest(request),
        "response_sha256":original_digest(response),
        "decision_sha256":original_digest(selected),
        "decision":selected,
        "available_at_ms":t+102,
    }
    return request,response,decision


def source_records(*, instrument="EUR.USD", is_btc=False,
                   missing_response=False, missing_decision=False):
    request,response,decision=fixture_graph(
        instrument=instrument,is_btc=is_btc)
    messages=[("HEADER", {"manifest_sha256":"f"*64,
        "instrument":instrument,"period_sha256":"a"*64,
        "execution_domain":"PAPER","source_kind":"PINNED_DRIVER_CALLSITE",
        "trading_authority":False}),("REQUEST",request)]
    if not missing_response:messages.append(("RESPONSE",response))
    if not missing_decision:messages.append(("DECISION",decision))
    prev="0"*64
    rows=[]
    for seq,(kind,data) in enumerate(messages):
        row={"schema":ORIGINAL_SCHEMA, "producer_id":"synthetic-test-producer",
             "sequence":seq,"previous":prev,"kind":kind,"data":data,
             "captured_at_ms":int(time.time()*1000)-100,
             "written_at_ms":int(time.time()*1000)-90,
             "dropped_total":0}
        check=original_digest(row)
        rows.append({**row,"sha256":check})
        prev=check
    return rows


class DecisionPopulationTests(unittest.TestCase):
    def test_fx_literal_original_model_output_valid(self):
        req,res,dec=fixture_graph("HOLD")
        self.assertEqual(audited._graph_decision(req,res,dec,1,2,3,
                              "EUR.USD","a"*64),
                         ("HOLD","EXACT_LITERAL_THREE_FIELD_MODEL_OUTPUT"))

    def test_btc_original_postmodel_enrichment_exact(self):
        req,res,dec=fixture_graph("BUY", instrument="BTC.USD",is_btc=True)
        self.assertEqual(audited._graph_decision(req,res,dec,1,2,3,
                              "BTC.USD","a"*64),
                         ("BUY","BTC_EXACT_DECLARED_POSTMODEL_FORECAST_DECORATOR"))

    def test_forecast_derived_fields_individually_tampered_fail(self):
        for key,replacement in (
            ("input_sha256","e"*64),("reference_mid","999"),
            ("issued_at_ms",42),("model_id","changed"),
            ("forecast_schema","other"),("available_at_ms",42),
            ("confidence",0.99),("expected_move_bps",50),
        ):
            with self.subTest(key=key):
                req,res,dec=fixture_graph("BUY",instrument="BTC.USD",is_btc=True)
                dec["decision"][key]=replacement
                dec["decision_sha256"]=original_digest(dec["decision"])
                with self.assertRaisesRegex(ValueError,
                        "ORIGINAL_DECISION_NOT_DECLARED_OUTPUT_TRANSFORM"):
                    audited._graph_decision(req,res,dec,1,2,3,"BTC.USD","a"*64)

    def test_decision_chain_digest_tampering_refused(self):
        req,res,dec=fixture_graph()
        dec["decision_sha256"]="e"*64
        with self.assertRaisesRegex(ValueError,"ORIGINAL_DECISION_DIGEST_MISMATCH"):
            audited._graph_decision(req,res,dec,1,2,3,"EUR.USD","a"*64)

    def test_response_link_replacement_refused(self):
        req,res,dec=fixture_graph()
        dec["response_sha256"]="f"*64
        with self.assertRaisesRegex(ValueError,"ORIGINAL_INPUT_RESPONSE_IDENTITY"):
            audited._graph_decision(req,res,dec,1,2,3,"EUR.USD","a"*64)

    def test_prompt_substitution_refused(self):
        req,res,dec=fixture_graph()
        req["prompt"]="different"
        with self.assertRaisesRegex(ValueError,"ORIGINAL_MODEL_PROMPT_TAMPERED"):
            audited._graph_decision(req,res,dec,1,2,3,"EUR.USD","a"*64)

    def test_decision_causal_order_refused(self):
        req,res,dec=fixture_graph()
        with self.assertRaisesRegex(ValueError,"ORIGINAL_DECISION_CAUSAL_ORDER"):
            audited._graph_decision(req,res,dec,1,3,2,"EUR.USD","a"*64)

    def test_valid_hold_is_observed_but_no_offpolicy_credit(self):
        rows=source_records()
        report=audited.analyze_producer(rows,"EUR.USD","a"*64)
        self.assertEqual(report["exact_three_stage_observed_decision_graph_count"],1)
        self.assertEqual(report["verified_original_selected_model_output_counts"],{"HOLD":1})
        self.assertEqual(report["off_policy_evaluation_eligible_decisions"],0)
        self.assertFalse(report["complete_original_behavior_policy_decision_population_attested"])
        self.assertEqual(report["true_original_action_propensities_attested"],0)

    def test_missing_response_is_not_verified_model_graph(self):
        report=audited.analyze_producer(
            source_records(missing_response=True),"EUR.USD","a"*64)
        self.assertEqual(report["exact_three_stage_observed_decision_graph_count"],0)
        self.assertIn("ORIGINAL_INCOMPLETE_DECISION_CHAIN",
                      report["unverified_or_missing_graph_counts"])

    def test_missing_decision_is_reported_without_synthetic_hold(self):
        report=audited.analyze_producer(
            source_records(missing_decision=True),"EUR.USD","a"*64)
        self.assertEqual(report["exact_three_stage_observed_decision_graph_count"],0)
        self.assertIn("ORIGINAL_DECISION_NOT_RECORDED",
                      report["unverified_or_missing_graph_counts"])

    def test_duplicate_request_kind_fails_closed(self):
        rows=source_records()
        dup=dict(rows[1]); dup["sequence"]=4
        with self.assertRaisesRegex(ValueError,"ORIGINAL_REQUEST_DUPLICATE_KIND"):
            audited.analyze_producer(rows+[dup],"EUR.USD","a"*64)

    def test_mismatched_original_period_does_not_create_credit(self):
        report=audited.analyze_producer(source_records(),"EUR.USD","b"*64)
        self.assertEqual(report["exact_three_stage_observed_decision_graph_count"],0)
        self.assertIn("ORIGINAL_REQUEST_SCOPE",report["unverified_or_missing_graph_counts"])

    def test_nonexistent_or_symlink_original_source_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            original=Path(tmp)/"actual"
            original.mkdir(mode=0o700)
            link=Path(tmp)/"symlink"
            link.symlink_to(original,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,"ORIGINAL_JOURNAL_SYMLINK"):
                audited._source_directory(link)

    def test_modified_original_hash_chain_rejected_before_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"rows.jsonl"
            path.write_bytes(b"\n".join(original_canonical(row).encode()
                                     for row in source_records())+b"\n")
            path.chmod(0o600)
            records,receipt=audited._read_immutable_prefix(path)
            self.assertEqual(len(records),4)
            self.assertEqual(receipt["not_admitted_unfinished_tail_bytes"],0)
            contents=path.read_bytes()
            path.write_bytes(contents.replace(b'"action":"HOLD"',b'"action":"SELL"'))
            with self.assertRaisesRegex(ValueError,"ORIGINAL_JOURNAL_ROW_TAMPERED"):
                audited._read_immutable_prefix(path)

    def test_partial_last_line_not_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"rows.jsonl"
            path.write_bytes(b"".join(original_canonical(row).encode()+b"\n"
                                     for row in source_records())+b'{"partial":')
            path.chmod(0o600)
            records,receipt=audited._read_immutable_prefix(path)
            self.assertEqual(len(records),4)
            self.assertEqual(receipt["not_admitted_unfinished_tail_bytes"],len(b'{"partial":'))

    def test_btc_decorator_must_be_exact_source_pinned_before_any_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/"release"/"BTC.USD"
            folder.mkdir(parents=True,mode=0o700)
            path=folder/"manifest.json"
            doc={"instrument":"BTC.USD",
                 "runtime_sha256":{
                     "heptatrader_btc_economics_driver.py":"e"*64,
                     "hepta_btc_entry_policy.py":audited.BTC_INSTALLED_POLICY_SHA256}}
            path.write_text(json.dumps(doc))
            path.chmod(0o600)
            def fake_verifier(manifest):
                return {"manifest_sha256":hashlib.sha256(manifest.read_bytes()).hexdigest()}
            with self.assertRaisesRegex(ValueError,
                    "UNSUPPORTED_BTC_ORIGINAL_DECORATOR_VERSION"):
                audited.audit(root,release_verifier=fake_verifier)

    def test_audit_output_must_not_export_model_prompt_or_response_payload(self):
        report=audited.analyze_producer(source_records(),"EUR.USD","a"*64)
        encoded=json.dumps(report,sort_keys=True)
        self.assertNotIn("fixture-only synthetic model prompt",encoded)
        self.assertNotIn('"stdout"',encoded)
        self.assertNotIn("fixture-only",encoded)
        self.assertFalse(report["complete_original_behavior_policy_decision_population_attested"])

    def test_unknown_original_model_output_format_stays_unverified(self):
        req,res,dec=fixture_graph()
        dec["decision"]["undocumented_field"]="noncanonical"
        dec["decision_sha256"]=original_digest(dec["decision"])
        with self.assertRaisesRegex(ValueError,
                "ORIGINAL_DECISION_NOT_DECLARED_OUTPUT_TRANSFORM"):
            audited._graph_decision(req,res,dec,1,2,3,"EUR.USD","a"*64)

    def test_unknown_or_false_unsigned_issuer_claim_rejected(self):
        report=audited.analyze_producer(source_records(),"EUR.USD","a"*64)
        self.assertFalse(report["independent_product_generator_root_enrolled"])
        self.assertFalse(report["independent_product_observer_root_enrolled"])
        self.assertEqual(report["canonical_product_ledger_decisions_appended"],0)


if __name__ == "__main__":
    unittest.main()
