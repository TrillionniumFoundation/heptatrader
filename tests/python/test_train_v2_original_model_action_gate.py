"""Adversarial SYNTHETIC model return/control classification, no order or IB."""

from __future__ import annotations
from decimal import Decimal
import copy
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hepta_decision_attribution import digest as origin_digest
from hepta_decision_attribution import canonical as origin_canonical
from hepta_btc_entry_policy import digest as btc_digest
from train_v2 import original_model_action_gate as audit


def graph(action="BUY", confidence=.62, position=0.0, instrument="EUR.USD",
          prepared=False, no_decision=False):
    """Synthetic, structurally identical to the installed original contract."""
    epoch=int(time.time_ns()//1000000)-10000
    history=[{
      "observed_at_ms":epoch-18000+i*1000,
      "bid":"1.001","ask":"1.003","mid":1.002
    } for i in range(6)]
    prompt="SYNTHETIC NEVER SENT TO MODEL"
    request={
        "request_id":"fixture-only-uuid","instrument":instrument,
        "period_sha256":"a"*64,"model_id":"gpt-6-luna",
        "input_scope":"EXACT_LOCAL_INFER_CLI_REQUEST_NOT_UNOBSERVED_PROVIDER_INTERNAL_CONTEXT",
        "started_at_ms":epoch,"position_at_request":position,
        "history_snapshot":history,
        "prompt":prompt,"prompt_sha256":hashlib.sha256(prompt.encode()).hexdigest(),
    }
    decoded={"action":action,"confidence":confidence,"rationale":"synthetic-test"}
    if instrument=="BTC.USD":
        decoded.update(expected_move_bps=0.5,horizon_sec=900)
    response={
      "request_id":request["request_id"],
      "request_sha256":origin_digest(request),
      "returncode":0,"available_at_ms":epoch+100,
      "stdout":json.dumps({"ok":True,"outputs":[{"text":json.dumps(decoded)}]}),
    }
    chosen=copy.deepcopy(decoded)
    if instrument=="BTC.USD":
        compact=[{"t":r["observed_at_ms"],"bid":r["bid"],
                  "ask":r["ask"],"mid":r["mid"]} for r in history]
        chosen.update({
          "issued_at_ms":compact[-1]["t"],"available_at_ms":epoch+101,
          "reference_mid":str((Decimal(compact[-1]["bid"])+Decimal(compact[-1]["ask"]))/2),
          "input_sha256":btc_digest(compact),
          "model_id":"gpt-6-luna",
          "forecast_schema":"heptatrader.btc-return-forecast.v1",
        })
    decision={
       "request_id":request["request_id"],
       "request_sha256":origin_digest(request),
       "response_sha256":origin_digest(response),
       "decision_sha256":origin_digest(chosen),
       "available_at_ms":epoch+102,
       "decision":chosen,
    }
    events=[("HEADER",{
      "manifest_sha256":"f"*64,"instrument":instrument,
      "period_sha256":"a"*64,
      "source_kind":"PINNED_DRIVER_CALLSITE",
      "execution_domain":"PAPER","trading_authority":False}),
      ("REQUEST",request),("RESPONSE",response)]
    if not no_decision:
        events.append(("DECISION",decision))
    if prepared:
        events.append(("COMMAND_BINDING",{
          "command":{
            "command_id":"fixture-open-command",
            "instrument":instrument,
            "kind":"OPEN",
            "side":action,
            "created_at_ms":epoch+105,
          },
          "basis":"IDENTICAL_RETURNED_OBJECT_AT_ORIGINAL_DURABLE_PREPARED",
          "state_write_succeeded":True,
          "request_id":request["request_id"],
          "request_sha256":origin_digest(request),
          "response_sha256":origin_digest(response),
          "decision_sha256":origin_digest(chosen),
          "decision_available_at_ms":decision["available_at_ms"],
        }))
    return [{
         "kind":kind,"sequence":index,"data":payload,
         "captured_at_ms":epoch+200,
         "schema":"heptatrader.exact-decision-attribution.v1",
         "producer_id":"synthetic-test-only",
         "previous":"f"*64,
    } for index,(kind,payload) in enumerate(events)]


def checked(rows,instrument="EUR.USD"):
    return audit._decision_result(rows,instrument=instrument,period_sha256="a"*64)


class ReadonlyObservedActionGateTests(unittest.TestCase):
    def test_flat_fx_exact_threshold_is_partial_candidate_only(self):
        d=checked(graph("BUY",.62,0))
        self.assertEqual(d["verified_three_event_return_graph_count"],1)
        self.assertEqual(d["model_return_action_counts"],{"BUY":1})
        self.assertEqual(d["partial_legacy_request_time_predicate_counts"][
            "at_request_legacy_open_candidate_only"],1)
        self.assertEqual(d["partial_legacy_request_time_predicate_counts"][
            "literal_historical_model_confidence_ge_0_62"],1)
        self.assertFalse(d["original_complete_policy_postrisk_action_population_proven"])
        self.assertFalse(d["true_original_model_action_selection_probability_available"])
        self.assertEqual(d["any_off_policy_effectiveness_eligible_rows"],0)

    def test_flat_fx_sell_candidate_and_real_bound_prepared(self):
        d=checked(graph("SELL",0.8,0.0,prepared=True))
        self.assertEqual(d["local_prepared_open_original_object_binding_counts"][
            "exact_model_input_to_PREPARED_provenance_verified"],1)
        self.assertEqual(d["local_prepared_open_original_object_binding_counts"][
            "verified_PREPARED_SELL"],1)
        self.assertEqual(d["model_run_opportunities_not_observed_by_journal_count"],"UNKNOWN")
        self.assertEqual(d["actual_command_dispatch_result_count"],"UNKNOWN")

    def test_flat_fx_high_confidence_hold_never_candidate(self):
        d=checked(graph("HOLD",0.99,0.0))
        self.assertEqual(d["partial_legacy_request_time_predicate_counts"][
            "high_confidence_HOLD_is_not_open"],1)
        self.assertEqual(d["partial_legacy_request_time_predicate_counts"].get(
            "at_request_legacy_open_candidate_only",0),0)

    def test_confidence_below_threshold_refused_even_at_flat(self):
        for action in ("BUY","SELL"):
            with self.subTest(action=action):
                d=checked(graph(action,0.619999,0.0))
                self.assertEqual(d["partial_legacy_request_time_predicate_counts"].get(
                    "at_request_legacy_open_candidate_only",0),0)
                self.assertEqual(d["partial_legacy_request_time_predicate_counts"][
                    "literal_historical_model_confidence_below_0_62"],1)

    def test_nonflat_fx_direction_does_not_prove_can_open(self):
        d=checked(graph("BUY",.99,25000.0))
        self.assertEqual(d["raw_original_model_request_position_counts"][
            "nonflat_at_model_request"],1)
        self.assertEqual(d["partial_legacy_request_time_predicate_counts"].get(
            "at_request_legacy_open_candidate_only",0),0)

    def test_btc_sell_with_high_confidence_never_legacy_open(self):
        d=checked(graph("SELL",.95,0.0,instrument="BTC.USD"),"BTC.USD")
        self.assertEqual(d["partial_legacy_request_time_predicate_counts"][
            "btc_original_model_output_sell_not_supported_as_open"],1)
        self.assertEqual(d["partial_legacy_request_time_predicate_counts"].get(
            "at_request_legacy_open_candidate_only",0),0)

    def test_btc_buy_can_pass_legacy_but_not_economic_guard(self):
        d=checked(graph("BUY",.95,0.0,instrument="BTC.USD"),"BTC.USD")
        self.assertEqual(d["partial_legacy_request_time_predicate_counts"][
            "at_request_legacy_open_candidate_only"],1)
        self.assertTrue(d["unknown_stale_owner_pending_session_quote_and_risk_veto_denominator"]
            if "unknown_stale_owner_pending_session_quote_and_risk_veto_denominator" in d else
            d["unobserved_mutation_and_risk_veto_count"]=="UNKNOWN")
        self.assertFalse(d["can_deploy_model_or_send_order"])

    def test_valid_original_prepared_requires_static_predicate(self):
        for action,confidence,pos in [("HOLD",.99,0.0),
                                      ("SELL",0.1,0.0),
                                      ("BUY",.99,100.0)]:
            with self.subTest(action=action,pos=pos):
                with self.assertRaisesRegex(ValueError,"ORIGINAL_PREPARED_MODEL_OR_STATIC_GATE_MISMATCH"):
                    checked(graph(action,confidence,pos,prepared=True))

    def test_same_model_bound_prepared_wrong_action_refused(self):
        rows=graph("BUY",.8,0,prepared=True)
        rows[-1]["data"]["command"]["side"]="SELL"
        with self.assertRaisesRegex(ValueError,"ORIGINAL_PREPARED_MODEL_OR_STATIC_GATE_MISMATCH"):
            checked(rows)

    def test_tampered_decision_bind_digest_refused(self):
        rows=graph("SELL",.8,0,prepared=True)
        rows[-1]["data"]["decision_sha256"]="0"*64
        with self.assertRaisesRegex(ValueError,"ORIGINAL_PREPARED_MODEL_OR_STATIC_GATE_MISMATCH"):
            checked(rows)

    def test_prepared_binding_before_model_return_seq_refused(self):
        rows=graph("BUY",.8,0,prepared=True)
        rows[-1]["sequence"]=3
        with self.assertRaisesRegex(ValueError,"ORIGINAL_PREPARED_MODEL_OR_STATIC_GATE_MISMATCH"):
            checked(rows)

    def test_same_original_model_multiple_prepared_refused(self):
        rows=graph("BUY",.8,0,prepared=True)
        extra=copy.deepcopy(rows[-1])
        extra["sequence"]=5
        extra["data"]["command"]["command_id"]="second"
        with self.assertRaisesRegex(ValueError,"ORIGINAL_PREPARED_REQUEST_MULTIPLE_COMMANDS"):
            checked(rows+[extra])

    def test_missing_decision_not_reclassified_hold(self):
        d=checked(graph("BUY",.8,0,no_decision=True))
        self.assertEqual(d["verified_three_event_return_graph_count"],0)
        self.assertEqual(d["incomplete_or_invalid_original_capture_counts"][
            "MISSING_ORIGINAL_DECISION"],1)
        self.assertEqual(d["model_return_action_counts"],{})
        self.assertFalse(d["original_complete_policy_postrisk_action_population_proven"])

    def test_boolean_position_rejected_not_flat(self):
        rows=graph("BUY",.8,0)
        rows[1]["data"]["position_at_request"]=True
        # A fully self-consistent synthetic request/response/decision graph
        # is still semantically INVALID as a position witness, even if all
        # local hashes are recomputed. Do not rely only on digest failure.
        rows[2]["data"]["request_sha256"]=origin_digest(rows[1]["data"])
        rows[3]["data"]["request_sha256"]=origin_digest(rows[1]["data"])
        rows[3]["data"]["response_sha256"]=origin_digest(rows[2]["data"])
        d=checked(rows)
        self.assertEqual(d["verified_three_event_return_graph_count"],0)
        self.assertIn("INVALID_CAPTURED_TRIPLE:ORIGINAL_REQUEST_POSITION_NOT_NUMERIC",
                      d["incomplete_or_invalid_original_capture_counts"])

    def test_duplicate_request_event_refused(self):
        rows=graph("BUY",.8,0)
        duplicated=copy.deepcopy(rows[1]);duplicated["sequence"]=4
        with self.assertRaisesRegex(ValueError,"ORIGINAL_DUPLICATE_OBSERVED_MODEL_EVENT"):
            checked(rows+[duplicated])

    def test_missing_provenance_does_not_mint_native_decision(self):
        d=checked(graph("SELL",.99,0))
        self.assertEqual(d["local_prepared_open_original_object_binding_counts"],{})
        self.assertEqual(d["any_off_policy_effectiveness_eligible_rows"],0)
        self.assertFalse(d["can_deploy_model_or_send_order"])

    def test_original_source_code_pin_refuses_changed_threshold_and_grammar(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=("\n".join(audit.EXACT_SOURCE_GUARDS)+
                    "\nif not isinstance(decision,dict):\n").encode()
            p=root/"heptatrader_paper_driver.py"
            p.write_bytes(source)
            p.chmod(0o600)
            good={"runtime_sha256":{"heptatrader_paper_driver.py":hashlib.sha256(source).hexdigest()}}
            result=audit._verify_original_policy_code(root/"manifest.json",good)
            self.assertTrue(result["original_open_predicate_source_pinned"])
            self.assertFalse(result["complete_product_eligibility_gates"])
            changed=source.replace(b"CONF=0.62",b"CONF=0.63")
            p.write_bytes(changed)
            tampered={"runtime_sha256":{"heptatrader_paper_driver.py":hashlib.sha256(changed).hexdigest()}}
            with self.assertRaisesRegex(ValueError,"ORIGINAL_LEGACY_GATE_SOURCE_DRIFT"):
                audit._verify_original_policy_code(root/"manifest.json",tampered)

    def test_original_request_id_cannot_be_counted_twice_across_producers(self):
        with tempfile.TemporaryDirectory() as d:
            parent=Path(d)
            manifest=parent/"one"/"EUR.USD"/"manifest.json"
            manifest.parent.mkdir(parents=True)
            blob=json.dumps({
                "instrument":"EUR.USD","period_sha256":"a"*64,
                "journal_directory":str(parent/"original-private-journal"),
                "trading_behavior_changed":False,
            }).encode()
            manifest.write_bytes(blob)
            manifest.chmod(0o600)
            msha=hashlib.sha256(blob).hexdigest()
            original_rows=graph("BUY",0.8,0)
            clones=[]
            for idx in (0,1):
                rows=copy.deepcopy(original_rows)
                for row in rows:
                    row["producer_id"]="synthetic-fixture-"+str(idx)
                    if row["kind"]=="HEADER":
                        row["data"]["manifest_sha256"]=msha
                clones.append({
                    "producer":"synthetic-fixture-"+str(idx),
                    "manifest_sha256":msha,
                    "rows":rows,
                    "local_drops":False,
                    "health_dropped_or_failed":False,
                })
            snap={"producers":clones,"files":[],"file_count":2,
                  "verified_total_prefix_bytes":0}
            result={"manifest_sha256":msha,
                    "exact_original_command_binding_count":0,
                    "binding_issue_count":0}
            with patch.object(audit,"verify_release",return_value=result),                  patch.object(audit,"_verify_original_policy_code",
                              return_value={"original_open_predicate_source_pinned":True}),                  patch.object(audit,"_scan_original",return_value=snap):
                with self.assertRaisesRegex(ValueError,
                      "ORIGINAL_CROSS_PRODUCER_REQUEST_IDENTITY_REUSED"):
                    audit.audit(parent)

    def test_original_request_id_cannot_be_counted_twice_across_releases(self):
        with tempfile.TemporaryDirectory() as d:
            parent=Path(d)
            snippets=[]
            digests=[]
            for prefix in ("one","two"):
                manifest=parent/prefix/"EUR.USD"/"manifest.json"
                manifest.parent.mkdir(parents=True)
                blob=json.dumps({
                    "instrument":"EUR.USD","period_sha256":"a"*64,
                    "journal_directory":str(parent/("original-"+prefix)),
                    "trading_behavior_changed":False,
                }).encode()
                manifest.write_bytes(blob)
                manifest.chmod(0o600)
                digest=hashlib.sha256(blob).hexdigest()
                digests.append(digest)
                rows=graph("SELL",.8,0)
                rows[0]["data"]["manifest_sha256"]=digest
                snippets.append({
                    "producers":[{
                        "producer":"synthetic-"+prefix,
                        "manifest_sha256":digest,"rows":rows,
                        "local_drops":False,"health_dropped_or_failed":False,
                    }],
                    "files":[],"file_count":1,"verified_total_prefix_bytes":0,
                })
            original_verify = audit.verify_release
            def fake_verify(p):
                sha=hashlib.sha256(p.read_bytes()).hexdigest()
                return {"manifest_sha256":sha,
                        "exact_original_command_binding_count":0,
                        "binding_issue_count":0}
            with patch.object(audit,"verify_release",side_effect=fake_verify),                  patch.object(audit,"_verify_original_policy_code",
                              return_value={"original_open_predicate_source_pinned":True}),                  patch.object(audit,"_scan_original",side_effect=snippets):
                with self.assertRaisesRegex(ValueError,
                      "ORIGINAL_CROSS_RELEASE_REQUEST_DUPLICATE"):
                    audit.audit(parent)

    def test_unknown_btc_economic_decorator_refuses_static_policy_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            raw=("\n".join(audit.EXACT_SOURCE_GUARDS)+
                 "\nif not isinstance(decision,dict):\n").encode()
            base=root/"heptatrader_paper_driver.py"
            base.write_bytes(raw)
            base.chmod(0o600)
            m={"instrument":"BTC.USD","runtime_sha256":{
                 "heptatrader_paper_driver.py":hashlib.sha256(raw).hexdigest(),
                 "heptatrader_btc_economics_driver.py":"0"*64,
                 "hepta_btc_entry_policy.py":"f"*64,
            }}
            with self.assertRaisesRegex(ValueError,
                    "UNSUPPORTED_ORIGINAL_BTC_DETERMINISTIC_DECORATOR"):
                audit._verify_original_policy_code(root/"manifest.json",m)

    def test_does_not_include_original_prompt_or_rationale_in_output(self):
        result=checked(graph("HOLD",.88,0.0))
        serialized=json.dumps(result)
        self.assertNotIn("SYNTHETIC NEVER SENT TO MODEL",serialized)
        self.assertNotIn("synthetic-test",serialized)
        self.assertNotIn('"stdout"',serialized)

if __name__=="__main__":
    unittest.main()
