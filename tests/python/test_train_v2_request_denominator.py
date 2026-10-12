"""Captured original REQUEST denominator versus successfully returned LLMs.

All synthetic fixtures are NOT real original model actions or broker facts.
The separate original-source smoke verifies actual private PAPER journals.
"""
import copy
from unittest.mock import patch
import unittest

from train_v2.contracts import ContractError
from train_v2.request_denominator import (
    SCHEMA, classify_producer, reconcile_denominators,
    source_model_attempts,
)

M="a"*64
N="b"*64

def row(kind,rid,sequence):
    return {"kind":kind,"sequence":sequence,"data":{"request_id":rid}}

def attempts(*,requests=7,done=4,unresolved=None,manifest=M):
    if unresolved is None:
        unresolved={"NO_DECISION":2,"NO_RESPONSE":1}
    return [{
      "original_manifest_sha256":manifest,
      "captured_request_events":requests,
      "verified_original_model_returns":done,
      "unresolved_original_request_reasons":unresolved,
      "orphan_without_request_not_in_denominator":{"ORPHAN_NO_REQUEST":1},
    }]

def quote_result(*,done=4, fresh=2, stale=1, unknown=1,manifest=M):
    return {
        "observations":{
          "verified_original_model_returns":done,
          "fresh_local_quote_asof_model_return_upper_bound":fresh,
          "fresh_returned_BUY":fresh,
          "in_attested_sealed_source_time_bounds":fresh+stale,
          "no_original_local_fresh_quote_known_at_model_return":stale,
          "outside_attested_sealed_source_coverage_unknown":unknown,
        },
        "per_release":{manifest:{"verified_model_returns":done}},
        "scope":"RETROSPECTIVE_READONLY_UPPER_BOUND_NOT_ORIGINAL_LLM_POLICY",
        "promotion_allowed":False,
        "original_full_action_propensities_known":False,
        "source_attested_as_broker_signed":False,
        "broker_orders_sent":0,
    }

class SyntheticOriginalRequestDenominatorTests(unittest.TestCase):
    def test_complete_graph_counted_once(self):
        rows=[row("REQUEST","x",1),row("RESPONSE","x",2),
              row("DECISION","x",3)]
        with patch("train_v2.request_denominator._graph_decision",
                   return_value=("BUY","synthetic")):
            result=classify_producer(rows,"EUR.USD",M)
        self.assertEqual(result["captured_request_events"],1)
        self.assertEqual(result["verified_original_model_returns"],1)
        self.assertEqual(result["captured_unresolved_requests"],{})
        self.assertFalse(result["model_behavior_propensities_known"])

    def test_request_without_response_or_decision_is_unknown_not_zero(self):
        result=classify_producer([row("REQUEST","x",1)],"EUR.USD",M)
        self.assertEqual(result["captured_unresolved_requests"],
                         {"NO_RESPONSE_NO_DECISION":1})
        self.assertEqual(result["verified_original_model_returns"],0)
        self.assertFalse(result["whole_policy_opportunity_population_complete"])

    def test_request_with_response_but_without_decision_kept_unknown(self):
        result=classify_producer(
            [row("REQUEST","x",1),row("RESPONSE","x",2)],
            "EUR.USD",M)
        self.assertEqual(result["captured_unresolved_requests"],{"NO_DECISION":1})

    def test_request_with_decision_but_missing_response_kept_unknown(self):
        result=classify_producer(
            [row("REQUEST","x",1),row("DECISION","x",2)],
            "EUR.USD",M)
        self.assertEqual(result["captured_unresolved_requests"],{"NO_RESPONSE":1})

    def test_orphan_not_in_request_denominator(self):
        result=classify_producer(
            [row("RESPONSE","x",2),row("DECISION","x",3)],
            "EUR.USD",M)
        self.assertEqual(result["captured_request_events"],0)
        self.assertEqual(result["orphan_event_groups_without_request"],
                         {"ORPHAN_NO_REQUEST":1})

    def test_mixed_missing_and_complete_exhaust_request_population(self):
        rows=[
          row("REQUEST","valid",1),row("RESPONSE","valid",2),
          row("DECISION","valid",3),
          row("REQUEST","incomplete",4),row("RESPONSE","incomplete",5),
          row("REQUEST","missing",6),row("RESPONSE","orphan",7)]
        with patch("train_v2.request_denominator._graph_decision",
                   return_value=("HOLD","synthetic")):
            result=classify_producer(rows,"EUR.USD",M)
        self.assertEqual(result["captured_request_events"],3)
        self.assertEqual(result["verified_original_model_returns"],1)
        self.assertEqual(sum(result["captured_unresolved_requests"].values()),2)
        self.assertEqual(result["orphan_event_groups_without_request"],
                         {"ORPHAN_NO_REQUEST":1})

    def test_duplicate_original_request_kind_refused(self):
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_DENOMINATOR_DUPLICATE_KIND"):
            classify_producer([row("REQUEST","x",1),
                               row("REQUEST","x",2)],"EUR.USD",M)

    def test_invalid_or_failed_complete_original_graph_stays_unknown(self):
        rows=[row("REQUEST","x",1),row("RESPONSE","x",2),
              row("DECISION","x",3)]
        with patch("train_v2.request_denominator._graph_decision",
                   side_effect=ValueError("invalid original return")):
            result=classify_producer(rows,"EUR.USD",M)
        self.assertEqual(result["captured_unresolved_requests"],
                         {"FULL_GRAPH_UNQUALIFIED":1})
        self.assertEqual(result["verified_original_model_returns"],0)

    def test_unregistered_action_cannot_become_verified_trade(self):
        rows=[row("REQUEST","x",1),row("RESPONSE","x",2),
              row("DECISION","x",3)]
        with patch("train_v2.request_denominator._graph_decision",
                   return_value=("CLOSE_ALL","synthetic")):
            result=classify_producer(rows,"EUR.USD",M)
        self.assertEqual(result["captured_unresolved_requests"],
                         {"FULL_GRAPH_UNQUALIFIED":1})

    def test_request_identity_malformed_refused(self):
        with self.assertRaises(ValueError):
            classify_producer([row("REQUEST","",1)],"EUR.USD",M)

    def test_bounded_scoped_manifest_root_required(self):
        with self.assertRaisesRegex(ContractError,"REQUEST_RELEASE_ROOT_UNSAFE"):
            source_model_attempts(".",M,{})

class CrossPopulationReconciliationTests(unittest.TestCase):
    def test_partition_conservation_and_hard_no_alpha(self):
        r=reconcile_denominators(attempts(),quote_result())
        self.assertEqual(r["captured_original_model_requests_lower_bound"],7)
        self.assertEqual(r["fully_verified_original_model_returns"],4)
        self.assertEqual(r["original_requests_without_qualified_model_return"],3)
        self.assertEqual(r["fresh_postmodel_quote_upper_bound"],2)
        self.assertEqual(r["verified_model_return_without_fresh_quote"],1)
        self.assertEqual(r["verified_model_return_outside_sealed_quote_coverage_unknown"],1)
        self.assertFalse(r["request_population_is_all_trade_opportunities"])
        self.assertFalse(r["original_model_actions_are_new_policy_candidates"])
        self.assertFalse(r["selection_propensity_attested"])
        self.assertFalse(r["economic_alpha_qualified"])
        self.assertEqual(r["broker_orders_sent"],0)

    def test_model_return_observation_snapshot_drift_refused(self):
        q=quote_result(done=5,fresh=3,stale=1,unknown=1)
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_MODEL_RETURNS_SNAPSHOT_DRIFT"):
            reconcile_denominators(attempts(),q)

    def test_distinct_release_set_required(self):
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_RELEASE_SET_OR_MODEL_COUNT_DRIFT"):
            reconcile_denominators(attempts(),quote_result(manifest=N))

    def test_duplicate_release_manifest_refused(self):
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_MANIFEST_REPEATED"):
            reconcile_denominators(attempts()+attempts(),quote_result(done=8,
                                                    fresh=8,stale=0,unknown=0))

    def test_request_count_must_cover_return_count(self):
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_POPULATION_NEGATIVE_OR_UNBOUNDED"):
            reconcile_denominators(attempts(requests=3),quote_result())

    def test_missing_request_conservation_cannot_be_forged(self):
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_UNRESOLVED_NOT_CONSERVED"):
            reconcile_denominators(attempts(unresolved={"NO_DECISION":1}),
                                   quote_result())

    def test_only_exact_missing_reasons_accepted(self):
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_UNRESOLVED_NOT_CONSERVED"):
            reconcile_denominators(attempts(unresolved={
                    "NO_DECISION":2,"NO_RESPONSE":1,"SUCCEEDED_FORGIVEN":0}),
                quote_result())

    def test_quote_join_three_states_must_conserve_models(self):
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_REQUOTE_POPULATION_NOT_CONSERVED"):
            reconcile_denominators(attempts(),quote_result(fresh=3))

    def test_mismatched_total_verified_return_count_refused(self):
        q=quote_result(done=5,fresh=3,stale=1,unknown=1)
        q["per_release"][M]["verified_model_returns"]=4
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_RELEASE_SET_OR_MODEL_COUNT_DRIFT"):
            reconcile_denominators(attempts(),q)

    def test_two_distinct_release_manifest_counts_combine(self):
        other=attempts(requests=2,done=2,unresolved={},manifest=N)
        q=quote_result(done=6,fresh=4,stale=1,unknown=1)
        q["per_release"][M]={"verified_model_returns":4}
        q["per_release"][N]={"verified_model_returns":2}
        r=reconcile_denominators(attempts()+other,q)
        self.assertEqual(r["captured_original_model_requests_lower_bound"],9)
        self.assertEqual(r["fully_verified_original_model_returns"],6)

    def test_negative_quote_counter_cannot_offset_overcount(self):
        q=quote_result(fresh=-1,stale=4,unknown=1)
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_REQUOTE_COUNTERS_INVALID"):
            reconcile_denominators(attempts(),q)

    def test_fake_qualified_model_action_count_rejected(self):
        q=quote_result()
        q["observations"]["fresh_returned_BUY"]=5
        with self.assertRaisesRegex(ContractError,
                                    "REQUEST_REQUOTE_ACTION_SUBSET_MISMATCH"):
            reconcile_denominators(attempts(),q)

    def test_broker_quote_signature_or_promotion_grant_denied(self):
        for key,bad in (
            ("source_attested_as_broker_signed",True),
            ("promotion_allowed",True),
            ("original_full_action_propensities_known",True),
            ("broker_orders_sent",1)):
            q=quote_result()
            q[key]=bad
            with self.subTest(field=key),self.assertRaisesRegex(
                   ContractError,"REQUEST_REQUOTE_SOURCE_POLICY_NOT_RESEARCH_ONLY"):
                reconcile_denominators(attempts(),q)

    def test_unsigned_missing_release_digest_refused(self):
        fake=attempts()
        fake[0]["original_manifest_sha256"]="bad"
        with self.assertRaisesRegex(ContractError,"REQUEST_MANIFEST_ID_MISSING"):
            reconcile_denominators(fake,quote_result())

    def test_empty_release_population_not_a_success(self):
        empty=quote_result(done=0,fresh=0,stale=0,unknown=0)
        empty["per_release"]={}
        with self.assertRaisesRegex(ContractError,"REQUEST_MANIFEST_ID_MISSING"):
            reconcile_denominators([],empty)

    def test_boolean_return_count_cannot_pass_as_integer(self):
        q=quote_result(done=1,fresh=1,stale=0,unknown=0)
        q["per_release"][M]["verified_model_returns"]=True
        fake=attempts(requests=1,done=1,unresolved={})
        with self.assertRaisesRegex(ContractError,"REQUEST_MANIFEST_COUNT_NOT_INTEGER"):
            reconcile_denominators(fake,q)
