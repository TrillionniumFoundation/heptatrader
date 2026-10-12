"""Original opaque-model latency: causal source provenance, no false OPE."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hepta_decision_attribution import digest as original_digest
from train_v2 import original_model_timing as timing
from test_train_v2_original_decision_population import fixture_graph, source_records


def resign(rows):
    """TEST-ONLY: rebind a synthetic request/response/decision triplet."""
    data = {r["kind"]: r["data"] for r in rows
            if r["kind"] in ("REQUEST", "RESPONSE", "DECISION")}
    request, response, decision = (
        data["REQUEST"], data["RESPONSE"], data["DECISION"])
    response["request_sha256"] = original_digest(request)
    decision["request_sha256"] = original_digest(request)
    decision["response_sha256"] = original_digest(response)
    decision["decision_sha256"] = original_digest(decision["decision"])
    return rows


class OriginalModelTimingTests(unittest.TestCase):
    def test_original_verified_graph_and_no_quote_ttl_relabel(self):
        rows = source_records()
        result = timing.measure_producer(rows, "EUR.USD", "a"*64)
        self.assertEqual(len(result["valid"]), 1)
        self.assertEqual(result["original_requests"], 1)
        self.assertEqual(result["missing"], {})
        sample = result["valid"][0]
        self.assertEqual(sample["model_request_to_response_ms"], 100)
        self.assertEqual(sample["model_request_to_decision_ms"], 102)
        self.assertEqual(sample["last_original_quote_to_request_ms"], 7000)
        self.assertEqual(sample["last_original_quote_to_decision_ms"], 7102)
        self.assertFalse(sample["max_original_same_quote_ttl_feasible"])
        self.assertEqual(sample["action"], "HOLD")

    def test_missing_response_not_imputed_as_hold(self):
        result = timing.measure_producer(
            source_records(missing_response=True), "EUR.USD", "a"*64)
        self.assertEqual(result["valid"], [])
        self.assertEqual(result["missing"], {"MISSING_RESPONSE": 1})

    def test_missing_decision_not_imputed_as_hold(self):
        result = timing.measure_producer(
            source_records(missing_decision=True), "EUR.USD", "a"*64)
        self.assertEqual(result["valid"], [])
        self.assertEqual(result["missing"], {"MISSING_DECISION": 1})

    def test_duplicate_request_identity_refused(self):
        rows = source_records()
        with self.assertRaisesRegex(ValueError, "TIMING_DUPLICATE_ORIGINAL_REQUEST"):
            timing.measure_producer(rows + [copy.deepcopy(rows[1])], "EUR.USD", "a"*64)

    def test_invalid_response_sha_refused_not_statistically_discarded(self):
        rows = source_records()
        for r in rows:
            if r["kind"] == "RESPONSE":
                r["data"]["stdout"] = "{}"
        with self.assertRaisesRegex(ValueError, "ORIGINAL_INPUT_RESPONSE_IDENTITY"):
            timing.measure_producer(rows, "EUR.USD", "a"*64)

    def test_future_quote_is_rejected_even_with_rehashed_graph(self):
        rows = source_records()
        request = next(r["data"] for r in rows if r["kind"]=="REQUEST")
        request["history_snapshot"][-1]["observed_at_ms"] = request["started_at_ms"]+1
        resign(rows)
        with self.assertRaisesRegex(ValueError, "TIMING_ORIGINAL_SOURCE_FUTURE"):
            timing.measure_producer(rows, "EUR.USD", "a"*64)

    def test_new_source_freshness_does_not_create_propensity(self):
        rows = source_records()
        request = next(r["data"] for r in rows if r["kind"]=="REQUEST")
        request["history_snapshot"][-1]["observed_at_ms"] = request["started_at_ms"]-100
        resign(rows)
        result = timing.measure_producer(rows, "EUR.USD", "a"*64)
        self.assertTrue(result["valid"][0]["max_original_same_quote_ttl_feasible"])
        summary = timing.report_from_groups(
            {"EUR.USD": result["valid"]}, created_at="TEST_FIXTURE")
        self.assertEqual(summary["observed_model_returns_within_optimistic_ttl"], 1)
        self.assertFalse(summary["actual_original_llm_selected_action_propensity_known"])
        self.assertFalse(summary["source_delivery_and_native_signature_ack_time_included"])

    def test_late_model_response_refused_by_optimistic_ttl(self):
        rows = source_records()
        request = next(r["data"] for r in rows if r["kind"]=="REQUEST")
        response = next(r["data"] for r in rows if r["kind"]=="RESPONSE")
        decision = next(r["data"] for r in rows if r["kind"]=="DECISION")
        request["history_snapshot"][-1]["observed_at_ms"] = request["started_at_ms"]-100
        response["available_at_ms"] = request["started_at_ms"]+6000
        decision["available_at_ms"] = response["available_at_ms"]+2
        resign(rows)
        sample = timing.measure_producer(rows, "EUR.USD", "a"*64)["valid"][0]
        self.assertFalse(sample["max_original_same_quote_ttl_feasible"])
        self.assertGreater(sample["last_original_quote_to_response_ms"],5000)

    def test_non_monotonic_original_history_refused(self):
        rows = source_records()
        request = next(r["data"] for r in rows if r["kind"]=="REQUEST")
        request["history_snapshot"][-2]["observed_at_ms"] = (
            request["history_snapshot"][-1]["observed_at_ms"])
        resign(rows)
        with self.assertRaisesRegex(ValueError, "TIMING_ORIGINAL_QUOTE_HISTORY_INVALID"):
            timing.measure_producer(rows, "EUR.USD", "a"*64)

    def test_bool_timestamps_are_not_valid_milliseconds(self):
        rows = source_records()
        request = next(r["data"] for r in rows if r["kind"]=="REQUEST")
        request["history_snapshot"][-1]["observed_at_ms"] = True
        resign(rows)
        with self.assertRaisesRegex(ValueError, "TIMING_ORIGINAL_QUOTE_HISTORY_INVALID"):
            timing.measure_producer(rows, "EUR.USD", "a"*64)

    def test_mismatched_release_scope_refused(self):
        with self.assertRaisesRegex(ValueError, "ORIGINAL_REQUEST_SCOPE"):
            timing.measure_producer(source_records(),"EUR.USD","b"*64)

    def test_original_BTC_declared_decorator_is_not_new_policy(self):
        rows = source_records(instrument="BTC.USD",is_btc=True)
        valid = timing.measure_producer(rows,"BTC.USD","a"*64)["valid"]
        self.assertEqual(len(valid),1)
        self.assertEqual(valid[0]["action"],"HOLD")
        self.assertFalse(valid[0]["max_original_same_quote_ttl_feasible"])

    def test_nearest_rank_quantiles_small_population(self):
        self.assertEqual(timing.percentile([7,1,4],50),4)
        self.assertEqual(timing.percentile([7,1,4],95),7)
        self.assertEqual(timing.describe([100,5000,5001])["within_5000ms"],2)
        self.assertEqual(timing.describe([100,5000,5001])["above_5000ms"],1)
        self.assertIsNone(timing.describe([])["p50_ms"])

    def test_nonfinite_or_boolean_durations_cannot_enter_statistics(self):
        for values in ([1,True],[1,-1],[1,None],["NaN",2]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                timing.describe(values)

    def test_no_valid_original_data_is_not_zero_latency(self):
        with self.assertRaisesRegex(ValueError, "TIMING_NO_COMPLETE_ORIGINAL_MODEL_GRAPHS"):
            timing.report_from_groups({"EUR.USD":[]}, created_at="fixture")

    def test_valid_results_export_no_raw_prompt_or_stdout(self):
        rows=source_records()
        values=timing.measure_producer(rows,"EUR.USD","a"*64)["valid"]
        result=timing.report_from_groups({"EUR.USD":values},created_at="TEST_FIXTURE")
        raw=json.dumps(result,sort_keys=True)
        self.assertNotIn("fixture-only",raw)
        self.assertNotIn('"stdout"',raw)
        self.assertNotIn("response_sha256",raw)
        self.assertFalse(result["observed_model_return_is_product_decision"])
        self.assertEqual(result["broker_queries_or_orders_sent"],0)



class TimingAuditAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.docs=[]
        self.sources={}

    def add_release(self,name,instrument="EUR.USD",is_btc=False,
                    missing_response=False):
        folder=self.root/name/instrument
        folder.mkdir(parents=True)
        journal=self.root/("source-"+name)
        journal.mkdir(mode=0o700)
        manifest={
            "instrument":instrument,
            "period_sha256":"a"*64,
            "journal_directory":str(journal),
        }
        path=folder/"manifest.json"
        raw=json.dumps(manifest,sort_keys=True,separators=(",",":")).encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        self.docs.append(path)
        source=source_records(instrument=instrument,is_btc=is_btc,
                              missing_response=missing_response)
        self.sources[journal]={
            "producers":[{
                "manifest_sha256":hashlib.sha256(raw).hexdigest(),
                "producer":"test-only-"+name,
                "rows":source,
                "local_drops":False,
                "health_dropped_or_failed":False,
            }],
            "files":[{
                "source_path_sha256":"a"*64,
                "verified_prefix_sha256":"b"*64,
                "verified_prefix_bytes":123,
                "not_admitted_unfinished_tail_bytes":0,
            }],
            "file_count":1,
            "verified_total_prefix_bytes":123,
        }

    def report(self):
        def verified(path):
            return {"manifest_sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
        with patch.object(timing,"verify_release",side_effect=verified):
            with patch.object(timing,"_verify_original_policy_code",
                              return_value={"original_open_predicate_source_pinned":True}):
                with patch.object(timing,"_scan_original",
                                  side_effect=lambda x:self.sources[x]):
                    return timing.audit(self.root)

    def test_distinct_original_fx_and_btc_release_reports(self):
        self.add_release("fx","EUR.USD")
        self.add_release("btc","BTC.USD",is_btc=True)
        r=self.report()
        self.assertEqual(r["original_registered_release_count"],2)
        self.assertEqual(r["original_observed_model_return_count"],2)
        self.assertEqual(set(r["per_instrument"]),{"EUR.USD","BTC.USD"})
        self.assertEqual(r["observed_model_returns_within_optimistic_ttl"],0)
        self.assertEqual(r["true_original_ope_eligible_rows"],0)
        self.assertFalse(r["model_inference_performed"])
        self.assertFalse(r["automatic_trading"])
        self.assertEqual(r["broker_orders_sent"],0)

    def test_missing_response_is_unknown_not_false_no_trade(self):
        self.add_release("miss","EUR.USD",missing_response=True)
        with self.assertRaisesRegex(ValueError,"TIMING_NO_COMPLETE_ORIGINAL_MODEL_GRAPHS"):
            self.report()
        self.add_release("good","BTC.USD",is_btc=True)
        r=self.report()
        self.assertEqual(r["original_observed_model_return_count"],1)
        self.assertEqual(r["original_incomplete_capture_by_reason"],
                         {"MISSING_RESPONSE":1})
        self.assertEqual(r["true_original_ope_eligible_rows"],0)

    def test_duplicate_request_across_release_disallowed(self):
        self.add_release("same1")
        self.add_release("same2")
        with self.assertRaisesRegex(ValueError,
                                    "TIMING_REQUEST_ID_REUSED_ACROSS_RELEASES"):
            self.report()
