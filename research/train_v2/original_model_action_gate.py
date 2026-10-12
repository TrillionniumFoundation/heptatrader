"""Original unmodified PAPER model-return and pre-PREPARED static gate census.

Read the ORIGINAL installed attribution Journal hash chains, exhaustive
*observed* REQUEST/RESPONSE/DECISION graphs, and actual PREPARED OPEN
bindings. Reconcile known observed model outputs and an exact pinned
driver's very first legacy direction/confidence predicate, conditioned on
the model *request*-time position.

This is emphatically NOT:
* the full population of original policy decision opportunities;
* the source-time final guard, risk veto, mutation-ready or order send;
* a probability distribution of the original stochastic LLM or a
  representative off-policy evaluation denominator;
* an authorized product-root Generator/Observer, native LedgerWriter
  DecisionV2 ACK, or economic return.

A missing captured graph is UNKNOWN, never a new HOLD. A successful
research predicate is never a native complete candidate set or propensity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path

from hepta_decision_attribution import digest as original_digest
from hepta_decision_attribution import read_private, strict

from .original_decision_population import (
    MAX_MANIFESTS, BTC_INSTALLED_WRAPPER_SHA256, BTC_INSTALLED_POLICY_SHA256,
    _graph_decision, _request_id, _scan_original,
)
from .original_policy_audit import verify_release, write_once

SCHEMA = "heptatrader.train-v2-observed-original-model-return-static-open-predicate.v1"
ORIGINAL_THRESHOLD = 0.62
EXACT_SOURCE_GUARDS = (
    'CONF=0.62',
    'actions={"BUY"} if INSTRUMENT=="BTC.USD" else {"BUY","SELL"}',
    'return decision.get("action") in actions and decision.get("confidence",-1)>=CONF',
)
MAX_AGE_AT_SOURCE = 60_000
# Only original externally witnessed ISSUE domains are allowed. This is
# source provenance, not permission to call a model or live trading service.


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _verify_original_policy_code(manifest_path: Path, manifest: dict) -> dict:
    runtime = manifest.get("runtime_sha256")
    if not isinstance(runtime, dict) or "heptatrader_paper_driver.py" not in runtime:
        raise ValueError("ORIGINAL_PRODUCER_RUNTIME_PIN_MISSING")
    original_path = manifest_path.parent / "heptatrader_paper_driver.py"
    raw = read_private(original_path, 1024 * 1024)
    if _sha(raw) != runtime["heptatrader_paper_driver.py"]:
        raise ValueError("ORIGINAL_RUNTIME_SOURCE_CHANGED")
    src = raw.decode("utf-8")
    for clause in EXACT_SOURCE_GUARDS:
        if src.count(clause) != 1:
            raise ValueError("ORIGINAL_LEGACY_GATE_SOURCE_DRIFT")
    if src.count('if not isinstance(decision,dict):') != 1:
        raise ValueError("ORIGINAL_LEGACY_GATE_NULL_HANDLING_CHANGED")
    # The BTC decorator additionally vets economics and the exact forecast
    # enrichment. Admit only the EXACT audited pinned BTC wrapper and policy
    # source pair from #200. This still does not expose full veto reasons
    # or behavior propensities in the original source-only journal.
    btc_wrapper_pinned = False
    if manifest.get("instrument") == "BTC.USD":
        if (runtime.get("heptatrader_btc_economics_driver.py")
                != BTC_INSTALLED_WRAPPER_SHA256
                or runtime.get("hepta_btc_entry_policy.py")
                != BTC_INSTALLED_POLICY_SHA256):
            raise ValueError("UNSUPPORTED_ORIGINAL_BTC_DETERMINISTIC_DECORATOR")
        if (_sha(read_private(manifest_path.parent / "hepta_btc_entry_policy.py",1024*1024))
                != BTC_INSTALLED_POLICY_SHA256
                or _sha(read_private(manifest_path.parent / "heptatrader_btc_economics_driver.py",1024*1024))
                != BTC_INSTALLED_WRAPPER_SHA256):
            raise ValueError("ORIGINAL_BTC_ACTUAL_DECORATOR_SOURCE_CHANGED")
        btc_wrapper_pinned = True
    return {
        "original_driver_sha256": _sha(raw),
        "original_open_predicate_source_pinned": True,
        "observed_source_threshold": str(ORIGINAL_THRESHOLD),
        "btc_legacy_direction_is_buy_only": True,
        "fx_legacy_direction_is_buy_or_sell": True,
        "exact_installed_BTC_additional_economics_code_pinned":
            btc_wrapper_pinned,
        "additional_BTC_runtime_economics_vetoes_not_observed_at_model_request":
            manifest.get("instrument") == "BTC.USD",
        "complete_product_eligibility_gates": False,
        "actual_final_execution_gate_observed": False,
        "original_full_action_propensities": False,
    }


def _request_position(value: object) -> float:
    # It was the position when the original ModelWorker was first invoked,
    # not the later authoritative position at a PREPARED command.
    if type(value) not in (int, float):
        raise ValueError("ORIGINAL_REQUEST_POSITION_NOT_NUMERIC")
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("ORIGINAL_REQUEST_POSITION_NONFINITE")
    return f


def _decision_result(
    records: list[dict], *, instrument: str, period_sha256: str,
) -> dict:
    """One *verified prefix*; each original request identity counted once."""
    if instrument not in ("EUR.USD", "BTC.USD"):
        raise ValueError("ORIGINAL_MODEL_INSTRUMENT_NOT_SUPPORTED")
    if not isinstance(period_sha256, str) or len(period_sha256) != 64:
        raise ValueError("ORIGINAL_MODEL_PERIOD_INVALID")
    requests: dict[str,dict] = {}
    binding_rows: list[dict] = []
    event_types = Counter()
    capture_failures = Counter()
    for row in records:
        kind = row["kind"]
        event_types[kind] += 1
        if kind in ("REQUEST", "RESPONSE", "DECISION"):
            rid = _request_id(row["data"])
            group = requests.setdefault(rid, {})
            if kind in group:
                raise ValueError("ORIGINAL_DUPLICATE_OBSERVED_MODEL_EVENT")
            group[kind] = (row["data"], row["sequence"])
        elif kind == "COMMAND_BINDING":
            binding_rows.append(row)
        elif kind in ("UNBOUND_DECISION", "UNBOUND_COMMAND", "OBSERVER_ERROR"):
            capture_failures[kind] += 1
    if event_types["REQUEST"] < event_types["DECISION"]:
        raise ValueError("ORIGINAL_MODEL_DECISION_WITHOUT_REQUEST")
    decoded: dict[str,dict] = {}
    model_output = Counter()
    predicate = Counter()
    positions = Counter()
    decode_profiles = Counter()
    for rid, events in requests.items():
        if not {"REQUEST", "RESPONSE", "DECISION"} <= set(events):
            if "REQUEST" not in events:
                capture_failures["MISSING_ORIGINAL_REQUEST"] += 1
            elif "RESPONSE" not in events:
                capture_failures["MISSING_ORIGINAL_RESPONSE"] += 1
            elif "DECISION" not in events:
                capture_failures["MISSING_ORIGINAL_DECISION"] += 1
            continue
        request, response, decision = (events[key][0] for key
                                       in ("REQUEST", "RESPONSE", "DECISION"))
        try:
            action, profile = _graph_decision(
                request, response, decision,
                events["REQUEST"][1], events["RESPONSE"][1],
                events["DECISION"][1], instrument, period_sha256)
            value = decision["decision"]
            confidence = value["confidence"]
            if type(confidence) not in (int, float) or not math.isfinite(confidence):
                raise ValueError("ORIGINAL_CONFIDENCE_NOT_FINITE")
            position = _request_position(request.get("position_at_request"))
            if type(request.get("started_at_ms")) is not int:
                raise ValueError("ORIGINAL_REQUEST_START_NOT_TIMESTAMP")
            age = int(decision["available_at_ms"] - request["started_at_ms"])
            if age < 0:
                raise ValueError("ORIGINAL_INFERENCE_NEGATIVE_AGE")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            capture_failures["INVALID_CAPTURED_TRIPLE:"+str(exc)[:110]] += 1
            continue
        source_flat = abs(position) <= 1e-9
        threshold = confidence >= ORIGINAL_THRESHOLD
        directional = action != "HOLD"
        direction_supported = action in (
            ("BUY",) if instrument == "BTC.USD" else ("BUY", "SELL")
        )
        legacy_candidate = source_flat and threshold and direction_supported
        classification = (
            "OBSERVED_RAW_REQUEST_FLAT_DIRECTION_CONFIDENCE_PASS_ONLY"
            if legacy_candidate else "OBSERVED_RAW_MODEL_NOT_STATIC_OPEN_CANDIDATE"
        )
        model_output[action] += 1
        decode_profiles[profile] += 1
        positions["flat_at_model_request"] += source_flat
        positions["nonflat_at_model_request"] += not source_flat
        predicate["all_verified_model_returns"] += 1
        predicate["literal_historical_model_confidence_ge_0_62"] += threshold
        predicate["literal_historical_model_confidence_below_0_62"] += not threshold
        predicate["high_confidence_HOLD_is_not_open"] += action == "HOLD" and threshold
        predicate["selected_HOLD_even_if_confident"] += action == "HOLD"
        predicate["directional_output"] += directional
        predicate["directional_high_confidence"] += directional and threshold
        predicate["at_request_flat_directional_high_confidence"] += (
            source_flat and directional and threshold)
        predicate["at_request_nonflat_directional"] += (
            not source_flat and directional)
        predicate["at_request_legacy_open_candidate_only"] += legacy_candidate
        if instrument == "BTC.USD":
            predicate["btc_original_model_output_sell_not_supported_as_open"] += (
                action == "SELL")
        if age > MAX_AGE_AT_SOURCE:
            # Not the actual post-return age check at the live trading tick.
            # We cannot know that tick's wall clock from this journal.
            predicate["inference_roundtrip_over_60s_not_proven_usable"] += 1
        decoded[rid] = {
            "action": action, "confidence_ge_threshold": threshold,
            "position_flat_at_model_request": source_flat,
            "legacy_candidate_only": legacy_candidate,
            "classification": classification,
            "available_at_ms": decision["available_at_ms"],
            "decision_journal_sequence": events["DECISION"][1],
            "request_started_at_ms": request["started_at_ms"],
            "request_sha256": original_digest(request),
            "response_sha256": original_digest(response),
            "decision_sha256": original_digest(decision["decision"]),
            "source_policy_profile": profile,
        }
    prepared = Counter()
    bound_commands: set[str] = set()
    bound_requests: set[str] = set()
    for row in binding_rows:
        command_binding = row["data"]
        request_id = command_binding.get("request_id")
        command = command_binding.get("command")
        if not isinstance(command, dict):
            raise ValueError("ORIGINAL_PREPARED_COMMAND_SHAPE")
        cid = command.get("command_id")
        if (not isinstance(cid, str) or not 1 <= len(cid) <= 256
                or cid in bound_commands):
            raise ValueError("ORIGINAL_PREPARED_IDENTITY_COLLISION")
        bound_commands.add(cid)
        prepared["original_capture_command_binding_rows"] += 1
        if request_id in bound_requests:
            raise ValueError("ORIGINAL_PREPARED_REQUEST_MULTIPLE_COMMANDS")
        bound_requests.add(request_id)
        observation = decoded.get(request_id)
        if observation is None:
            capture_failures["PREPARED_WITHOUT_VERIFIED_MODEL_TRIPLE"] += 1
            continue
        try:
            if (command_binding.get("basis")
                    != "IDENTICAL_RETURNED_OBJECT_AT_ORIGINAL_DURABLE_PREPARED"
                    or command_binding.get("state_write_succeeded") is not True
                    or command.get("kind") != "OPEN"
                    or command.get("instrument") != instrument
                    or command.get("side") != observation["action"]
                    or command_binding.get("request_sha256")
                    != observation["request_sha256"]
                    or command_binding.get("response_sha256")
                    != observation["response_sha256"]
                    or command_binding.get("decision_sha256")
                    != observation["decision_sha256"]
                    or type(command.get("created_at_ms")) is not int
                    or observation["available_at_ms"] > command["created_at_ms"]
                    or observation["decision_journal_sequence"] >= row["sequence"]
                    or not observation["legacy_candidate_only"]):
                raise ValueError("ORIGINAL_PREPARED_MODEL_OR_STATIC_GATE_MISMATCH")
        except (KeyError, TypeError) as exc:
            raise ValueError("ORIGINAL_PREPARED_IDENTITY_MISSING") from exc
        prepared["exact_model_input_to_PREPARED_provenance_verified"] += 1
        prepared["verified_PREPARED_"+observation["action"]] += 1
    # We intentionally never call lack of a PREPARED binding a "veto":
    # activity/pending, runtime position drift, owner safety and all
    # post-response gates are not enumerated by this Journal.
    return {
        "observed_raw_original_event_counts": dict(sorted(event_types.items())),
        "unique_observed_model_request_id_count": len(requests),
        "model_return_action_counts": dict(sorted(model_output.items())),
        "model_return_decode_profile_counts": dict(sorted(decode_profiles.items())),
        "raw_original_model_request_position_counts": dict(sorted(positions.items())),
        "partial_legacy_request_time_predicate_counts":dict(sorted(predicate.items())),
        "local_prepared_open_original_object_binding_counts":
            dict(sorted(prepared.items())),
        "incomplete_or_invalid_original_capture_counts":
            dict(sorted(capture_failures.items())),
        "verified_three_event_return_graph_count": len(decoded),
        "model_run_opportunities_not_observed_by_journal_count": "UNKNOWN",
        "unobserved_mutation_and_risk_veto_count": "UNKNOWN",
        "actual_command_dispatch_result_count": "UNKNOWN",
        "true_original_model_action_selection_probability_available": False,
        "original_complete_policy_postrisk_action_population_proven": False,
        "any_off_policy_effectiveness_eligible_rows": 0,
        "can_deploy_model_or_send_order": False,
    }


def audit(root: Path) -> dict:
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("ORIGINAL_RELEASE_ROOT_NOT_REAL_ABSOLUTE")
    manifests = sorted(root.glob("*/*/manifest.json"))
    if not 1 <= len(manifests) <= MAX_MANIFESTS:
        raise ValueError("ORIGINAL_RELEASES_BOUNDED")
    sources: dict[Path,dict] = {}
    reports = []
    combined_raw = Counter()
    combined_action = Counter()
    combined_partial = Counter()
    combined_prepared = Counter()
    combined_capture = Counter()
    original_seen_across_release = set()
    original_prepared_across_release = set()
    for path in manifests:
        original_verified = verify_release(path)
        blob = read_private(path,65536)
        manifest = strict(blob)
        manifest_sha = _sha(blob)
        if manifest_sha != original_verified["manifest_sha256"]:
            raise ValueError("ORIGINAL_RELEASE_MUTATED_DURING_AUDIT")
        static_gate = _verify_original_policy_code(path,manifest)
        directory = Path(manifest.get("journal_directory",""))
        if directory not in sources:
            sources[directory] = _scan_original(directory)
        source = sources[directory]
        producers=[p for p in source["producers"]
                   if p["manifest_sha256"] == manifest_sha]
        if not producers:
            raise ValueError("ORIGINAL_POLICY_CAPTURE_NOT_FOUND")
        raw = Counter()
        actions = Counter()
        partial = Counter()
        prepared = Counter()
        gaps = Counter()
        profiles = Counter()
        request_count = 0
        original_captured_sha = []
        all_release_observed_requests = set()
        all_release_prepared_commands = set()
        for producer in producers:
            headers=[r for r in producer["rows"] if r["kind"] == "HEADER"]
            if (not headers
                    or any(h["data"].get("instrument") != manifest["instrument"]
                           or h["data"].get("period_sha256") != manifest["period_sha256"]
                           or h["data"].get("execution_domain") != "PAPER"
                           or h["data"].get("source_kind") != "PINNED_DRIVER_CALLSITE"
                           or h["data"].get("trading_authority") is not False
                           for h in headers)):
                raise ValueError("ORIGINAL_CAPTURE_SOURCE_SCOPE_DRIFT")
            one=_decision_result(producer["rows"], instrument=manifest["instrument"],
                                 period_sha256=manifest["period_sha256"])
            raw.update(one["observed_raw_original_event_counts"])
            actions.update(one["model_return_action_counts"])
            partial.update(one["partial_legacy_request_time_predicate_counts"])
            prepared.update(one["local_prepared_open_original_object_binding_counts"])
            gaps.update(one["incomplete_or_invalid_original_capture_counts"])
            profiles.update(one["model_return_decode_profile_counts"])
            request_count += one["unique_observed_model_request_id_count"]
            original_captured_sha.append(_sha(producer["producer"].encode()))
            # An original model request UUID or product command cannot be
            # counted twice under different process IDs in the same release.
            # These UUIDs are original locally observed identities, not
            # synthetic decision population weights.
            for entry in producer["rows"]:
                if entry["kind"]=="REQUEST":
                    rid=_request_id(entry["data"])
                    if rid in all_release_observed_requests:
                        raise ValueError("ORIGINAL_CROSS_PRODUCER_REQUEST_IDENTITY_REUSED")
                    all_release_observed_requests.add(rid)
                    global_identity=(manifest["instrument"],manifest["period_sha256"],rid)
                    if global_identity in original_seen_across_release:
                        raise ValueError("ORIGINAL_CROSS_RELEASE_REQUEST_DUPLICATE")
                    original_seen_across_release.add(global_identity)
                elif entry["kind"]=="COMMAND_BINDING":
                    command=entry["data"].get("command",{})
                    cid=command.get("command_id") if isinstance(command,dict) else None
                    if not isinstance(cid,str) or cid in all_release_prepared_commands:
                        raise ValueError("ORIGINAL_CROSS_PRODUCER_PREPARED_IDENTITY_REUSED")
                    all_release_prepared_commands.add(cid)
                    global_command=(manifest["instrument"],manifest["period_sha256"],cid)
                    if global_command in original_prepared_across_release:
                        raise ValueError("ORIGINAL_CROSS_RELEASE_COMMAND_DUPLICATE")
                    original_prepared_across_release.add(global_command)
            if producer["local_drops"]:
                gaps["OBSERVED_PRODUCER_EVENT_DROPS"]+=1
            if producer["health_dropped_or_failed"] is not False:
                gaps["PRODUCER_HEALTH_NOT_CLEAN_OR_NOT_PRESENT"]+=1
        if sum(actions.values()) != partial["all_verified_model_returns"]:
            raise ValueError("ORIGINAL_MODEL_COUNTS_NOT_COHERENT")
        if prepared["exact_model_input_to_PREPARED_provenance_verified"] > partial[
            "at_request_legacy_open_candidate_only"]:
            raise ValueError("PREPARED_MORE_THAN_OBSERVED_REQUEST_STATIC_CANDIDATES")
        # The source scan and the older exact-binding release verifier may
        # straddle a concurrent append. Preserve both independent counts
        # and never silently force them equal, or infer a veto from drift.
        report={
            "instrument":manifest["instrument"],
            "behavior_changed":manifest["trading_behavior_changed"],
            "original_producer_manifest_sha256":manifest_sha,
            "original_period_sha256":manifest["period_sha256"],
            "original_pinned_base_driver_code":static_gate,
            "source_producer_count":len(producers),
            "source_producer_ids_hashed":sorted(original_captured_sha),
            "snapshot_original_journal_file_count":source["file_count"],
            "snapshot_original_file_prefixes_sha256":_sha(json.dumps(
                source["files"],sort_keys=True,separators=(",",":")).encode()),
            "snapshot_verified_original_bytes":source["verified_total_prefix_bytes"],
            "raw_event_counts":dict(sorted(raw.items())),
            "observed_model_request_id_count":request_count,
            "original_verified_selected_actions":dict(sorted(actions.items())),
            "original_model_decode_profiles":dict(sorted(profiles.items())),
            "original_request_time_position_and_static_gate":dict(sorted(partial.items())),
            "original_PREPARED_OPEN_same_model_identity_counts":dict(sorted(prepared.items())),
            "unverified_or_missing_journal_items":dict(sorted(gaps.items())),
            "independent_verifier_PREPARED_OPEN_count_during_separate_read":
                original_verified["exact_original_command_binding_count"],
            "independent_verifier_problem_count":
                original_verified["binding_issue_count"],
            "independent_verifier_count_same_time_snapshot":False,
            "full_original_decision_opportunities_known":False,
            "actual_post_response_guarded_trading_decision_known":False,
            "selected_original_llm_policy_propensities_known":False,
            "production_learning_eligible":False,
        }
        reports.append(report)
        combined_raw.update(raw)
        combined_action.update(actions)
        combined_partial.update(partial)
        combined_prepared.update(prepared)
        combined_capture.update(gaps)
    return {
        "schema":SCHEMA,
        "observation_utc":datetime.now(timezone.utc).isoformat(),
        "original_manifest_count":len(reports),
        "original_release_reports":reports,
        "total_raw_observed_journal_event_counts":dict(sorted(combined_raw.items())),
        "original_all_verified_model_return_action_counts":
            dict(sorted(combined_action.items())),
        "original_model_position_and_partial_static_gate":
            dict(sorted(combined_partial.items())),
        "original_same_model_prepared_open_counts":
            dict(sorted(combined_prepared.items())),
        "original_missing_or_invalid_capture_count_by_reason":
            dict(sorted(combined_capture.items())),
        "original_observed_model_return_count":sum(combined_action.values()),
        "same_model_true_prepared_open_count":combined_prepared[
            "exact_model_input_to_PREPARED_provenance_verified"],
        "original_all_model_return_unknown_final_guard_eligibility":True,
        "original_model_confidence_not_behavior_probability":True,
        "original_trading_policy_complete_decision_opportunities_verified":False,
        "original_complete_candidate_set_and_true_action_probabilities":False,
        "original_true_paper_prepared_open_is_not_broker_fill":True,
        "observed_model_return_counts_are_not_true_action_probabilities":True,
        "unknown_stale_owner_pending_session_quote_and_risk_veto_denominator":True,
        "true_original_ope_eligible_sample_count":0,
        "product_generator_root_and_observer_enrolled":False,
        "canonical_product_ledger_writer_witness_acked":False,
        "real_order_after_cost_outcomes_independently_observed":False,
        "automatic_trading_or_inference_performed_by_audit":False,
        "broker_requests_or_orders_sent":0,
        "trading_authority":False,
        "assessment":"HOLD_OBSERVED_ONLY_ORIGINAL_FULL_POLICY_SUPPORT_AND_PROPENSITY_MISSING",
    }


def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--original-release-root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    os.umask(0o077)
    result=audit(args.original_release_root)
    receipt_sha=write_once(args.output,result)
    print(json.dumps({
        "assessment":result["assessment"],
        "observed_original_return_count":result["original_observed_model_return_count"],
        "selected_original_model_return_action_counts":
            result["original_all_verified_model_return_action_counts"],
        "partial_request_time_legacy_candidate_count":
            result["original_model_position_and_partial_static_gate"].get(
                "at_request_legacy_open_candidate_only",0),
        "true_same_model_original_prepared_open_count":
            result["same_model_true_prepared_open_count"],
        "missing_or_corrupt_observed_source_rows":
            result["original_missing_or_invalid_capture_count_by_reason"],
        "root_enrolled_product_decisions":0,
        "true_original_action_propensities":0,
        "receipt_sha256":receipt_sha,
        "broker_queries_or_orders_sent":0,
    },sort_keys=True))


if __name__=="__main__":
    main()
