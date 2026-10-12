"""Observed original opaque LLM inference latency vs source TTL, read-only.

A selected BUY/SELL/HOLD is NOT a full original policy decision population;
neither confidence nor observed action frequencies attest a propensity.
Never performs inference, broker access, model training or order operations.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from hepta_decision_attribution import read_private, strict
from .original_decision_population import (
    MAX_MANIFESTS, _graph_decision, _request_id, _scan_original,
)
from .original_model_action_gate import _verify_original_policy_code
from .original_policy_audit import verify_release, write_once

SCHEMA = "heptatrader.train-v2-original-opaque-model-timing.v1"
OPTIMISTIC_QUOTE_TTL_MS = 5000
EXACT_SOURCE_PROFILE = "OBSERVED_ORIGINAL_PAPER_MODEL_REQUEST_RESPONSE_DECISION"
SCOPE = ("original_model_return_only_not_complete_trading_opportunities"
         "_or_authoritative_current_order_quote")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def percentile(values, p):
    """Nearest-rank percentile: integer milliseconds, no statistical CI."""
    if not isinstance(values, list) or not values:
        raise ValueError("TIMING_NO_SAMPLES")
    if type(p) not in (int, float) or not 0 <= p <= 100:
        raise ValueError("TIMING_PERCENTILE_INVALID")
    ordered = sorted(values)
    if any(type(x) is not int or x < 0 for x in ordered):
        raise ValueError("TIMING_DURATION_INVALID")
    import math
    index = max(0, math.ceil(len(ordered) * p / 100) - 1)
    return ordered[index]


def describe(values):
    if not values:
        return {
            "count": 0, "p05_ms": None, "p25_ms": None,
            "p50_ms": None, "p75_ms": None, "p90_ms": None,
            "p95_ms": None, "p99_ms": None, "max_ms": None,
            "within_5000ms": 0, "above_5000ms": 0,
        }
    if any(type(x) is not int or x < 0 for x in values):
        raise ValueError("TIMING_DURATION_INVALID")
    return {
        "count": len(values),
        **{"p" + str(q).zfill(2) + "_ms": percentile(values, q)
           for q in (5, 25, 50, 75, 90, 95, 99)},
        "max_ms": max(values),
        "within_5000ms": sum(x <= OPTIMISTIC_QUOTE_TTL_MS for x in values),
        "above_5000ms": sum(x > OPTIMISTIC_QUOTE_TTL_MS for x in values),
    }


def measure_producer(rows, instrument, period):
    """Measure ONLY complete verified three-stage original observations."""
    groups = defaultdict(dict)
    unknown = Counter()
    original_events = Counter()
    for item in rows:
        kind = item["kind"]
        original_events[kind] += 1
        if kind not in ("REQUEST", "RESPONSE", "DECISION"):
            continue
        rid = _request_id(item["data"])
        if kind in groups[rid]:
            raise ValueError("TIMING_DUPLICATE_ORIGINAL_REQUEST_KIND")
        groups[rid][kind] = (item["data"], item["sequence"])
    verified = []
    for rid, group in groups.items():
        if not {"REQUEST", "RESPONSE", "DECISION"} <= group.keys():
            absent = sorted({"REQUEST", "RESPONSE", "DECISION"} - set(group))
            for missing in absent:
                unknown["MISSING_" + missing] += 1
            continue
        request, response, decision = [group[k][0] for k in
                                      ("REQUEST", "RESPONSE", "DECISION")]
        action, _ = _graph_decision(
            request, response, decision,
            *(group[k][1] for k in ("REQUEST", "RESPONSE", "DECISION")),
            instrument, period)
        hist = request.get("history_snapshot")
        if not isinstance(hist, list) or not 1 <= len(hist) <= 24:
            raise ValueError("TIMING_ORIGINAL_SOURCE_SNAPSHOT_ABSENT")
        ts = [q.get("observed_at_ms") for q in hist]
        if (any(type(t) is not int or t <= 0 for t in ts)
                or any(b <= a for a, b in zip(ts, ts[1:]))):
            raise ValueError("TIMING_ORIGINAL_QUOTE_HISTORY_INVALID")
        source_at = ts[-1]
        started_at = request.get("started_at_ms")
        response_at = response.get("available_at_ms")
        decision_at = decision.get("available_at_ms")
        if (any(type(x) is not int for x in (started_at, response_at, decision_at))
                or not 0 < source_at <= started_at <= response_at <= decision_at):
            raise ValueError("TIMING_ORIGINAL_SOURCE_FUTURE_OR_OUT_OF_ORDER")
        verified.append({
            "model_request_to_response_ms": response_at - started_at,
            "model_request_to_decision_ms": decision_at - started_at,
            "last_original_quote_to_request_ms": started_at - source_at,
            "last_original_quote_to_response_ms": response_at - source_at,
            "last_original_quote_to_decision_ms": decision_at - source_at,
            "action": action,
            # Scope is the ORIGINAL observed single output, not actual
            # post-risk PREPARED, original trade effect, or sampling law.
            "max_original_same_quote_ttl_feasible":
                decision_at - source_at <= OPTIMISTIC_QUOTE_TTL_MS,
        })
    return {
        "valid": verified,
        "original_requests": original_events["REQUEST"],
        "original_responses": original_events["RESPONSE"],
        "original_decisions": original_events["DECISION"],
        "missing": dict(unknown),
    }


def report_from_groups(groups, *, created_at):
    """Pure aggregation of already independently verified observations."""
    by_instrument = {}
    combined = []
    for instrument in sorted(groups):
        entries = groups[instrument]
        if not entries:
            continue
        combined += entries
        by_instrument[instrument] = {
            "observed_complete_models": len(entries),
            "returned_actions": dict(sorted(Counter(x["action"] for x in entries).items())),
            "request_to_response": describe(
                [x["model_request_to_response_ms"] for x in entries]),
            "request_to_decision": describe(
                [x["model_request_to_decision_ms"] for x in entries]),
            "original_quote_to_request": describe(
                [x["last_original_quote_to_request_ms"] for x in entries]),
            "original_quote_to_response": describe(
                [x["last_original_quote_to_response_ms"] for x in entries]),
            "original_quote_to_decision": describe(
                [x["last_original_quote_to_decision_ms"] for x in entries]),
        }
    if not combined:
        raise ValueError("TIMING_NO_COMPLETE_ORIGINAL_MODEL_GRAPHS")
    times = [x["last_original_quote_to_decision_ms"] for x in combined]
    if any(type(x) is not int or x < 0 for x in times):
        raise ValueError("TIMING_DURATION_INVALID")
    feasible = sum(x <= OPTIMISTIC_QUOTE_TTL_MS for x in times)
    return {
        "schema": SCHEMA,
        "observed_at_utc": created_at,
        "source_profile": EXACT_SOURCE_PROFILE,
        "original_observed_model_return_count": len(combined),
        "per_instrument": by_instrument,
        "combined_original_quote_to_decision": describe(times),
        "combined_model_request_to_decision": describe(
            [x["model_request_to_decision_ms"] for x in combined]),
        "combined_original_quote_to_request": describe(
            [x["last_original_quote_to_request_ms"] for x in combined]),
        "combined_original_quote_to_response": describe(
            [x["last_original_quote_to_response_ms"] for x in combined]),
        "original_quote_already_over_5s_at_request": sum(
            x["last_original_quote_to_request_ms"] > OPTIMISTIC_QUOTE_TTL_MS
            for x in combined),
        "original_quote_over_5s_at_response": sum(
            x["last_original_quote_to_response_ms"] > OPTIMISTIC_QUOTE_TTL_MS
            for x in combined),
        "best_case_max_same_original_quote_ttl_ms": OPTIMISTIC_QUOTE_TTL_MS,
        "observed_model_returns_within_optimistic_ttl": feasible,
        "observed_model_returns_exceeding_optimistic_ttl":
            len(combined)-feasible,
        "source_delivery_and_native_signature_ack_time_included": False,
        "quotations_verified_as_executable_at_model_completion": False,
        "observed_model_return_is_product_decision": False,
        "policy_decision_opportunity_population_complete": False,
        "actual_original_llm_selected_action_propensity_known": False,
        "native_root_and_canonical_owner_enrolled": False,
        "model_train_or_trade_authority": False,
        "financial_alpha_proven": False,
        "broker_queries_or_orders_sent": 0,
        "assessment": (
            "HOLD_MOST_ORIGINAL_LLM_OUTPUTS_EXCEED_OPTIMISTIC_5S_SOURCE_WINDOW"
            if len(combined)-feasible > feasible
            else "HOLD_OTHER_REQUIRED_NATIVE_PREPARED_AND_ECONOMIC_FACTS"),
    }


def audit(original_release_root):
    root = Path(original_release_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("TIMING_RELEASE_ROOT_UNSAFE")
    manifest_paths = sorted(root.glob("*/*/manifest.json"))
    if not 1 <= len(manifest_paths) <= MAX_MANIFESTS:
        raise ValueError("TIMING_ORIGINAL_MANIFEST_COUNT")
    source_cache = {}
    seen_global_request_ids = set()
    groups = defaultdict(list)
    missing_total = Counter()
    release_reports = []
    for path in manifest_paths:
        manifest_raw = read_private(path, 65536)
        manifest = strict(manifest_raw)
        expected_hash = _sha(manifest_raw)
        # Retain original runtime byte hashes, exact installed BTC decorator,
        # registered actual account period and separately qualified PREPARED.
        registered = verify_release(path)
        if registered.get("manifest_sha256") != expected_hash:
            raise ValueError("TIMING_RELEASE_INDEPENDENT_VERIFIER_MISMATCH")
        source_pin = _verify_original_policy_code(path, manifest)
        journal = Path(manifest["journal_directory"])
        if journal not in source_cache:
            source_cache[journal] = _scan_original(journal)
        scan = source_cache[journal]
        producers = [p for p in scan["producers"]
                     if p["manifest_sha256"] == expected_hash]
        if not producers:
            raise ValueError("TIMING_RELEASE_NO_ORIGINAL_SOURCE_PRODUCER")
        by_release = []
        source_unknown = Counter()
        for p in producers:
            rows = p["rows"]
            # One original request identity may only count once in this
            # original instrument/period even across process or release.
            for row in rows:
                if row["kind"] == "REQUEST":
                    rid = _request_id(row["data"])
                    key = (manifest["instrument"], manifest["period_sha256"], rid)
                    if key in seen_global_request_ids:
                        raise ValueError("TIMING_REQUEST_ID_REUSED_ACROSS_RELEASES")
                    seen_global_request_ids.add(key)
            summary = measure_producer(
                rows, manifest["instrument"], manifest["period_sha256"])
            groups[manifest["instrument"]].extend(summary["valid"])
            by_release.append({
                "source_producer_sha256": _sha(p["producer"].encode()),
                "valid_original_graphs": len(summary["valid"]),
                "original_request_rows": summary["original_requests"],
                "original_response_rows": summary["original_responses"],
                "original_decision_rows": summary["original_decisions"],
                "incomplete_graphs_by_missing_event": summary["missing"],
                "source_append_dropped": bool(p["local_drops"]),
                "producer_health_certain_clean":
                    p["health_dropped_or_failed"] is False,
            })
            source_unknown.update(summary["missing"])
        missing_total.update(source_unknown)
        release_reports.append({
            "instrument": manifest["instrument"],
            "behavior_release_manifest_sha256": expected_hash,
            "installed_runtime_source_pinned": source_pin[
                "original_open_predicate_source_pinned"],
            "actual_source_producers_count": len(producers),
            "source_prefix_files": scan["file_count"],
            "source_prefix_bytes": scan["verified_total_prefix_bytes"],
            "source_prefix_fingerprint_sha256": _sha(
                json.dumps(scan["files"], sort_keys=True, separators=(",", ":")).encode()),
            "captured_producers": by_release,
            "unverified_original_graphs": dict(source_unknown),
        })
    body = report_from_groups(
        groups, created_at=datetime.now(timezone.utc).isoformat())
    body["original_release_reports"] = release_reports
    body["original_registered_release_count"] = len(release_reports)
    body["original_incomplete_capture_by_reason"] = dict(sorted(missing_total.items()))
    body["source_validation_method"] = (
        "ORIGINAL_PINNED_RUNTIME_RELEASE_AND_LOCAL_HASH_CHAIN_ONLY")
    body["observed_model_return_count_may_grow_after_prefix_snapshot"] = True
    body["input_response_rationale_and_private_account_data_exported"] = False
    body["input_quote_to_decision_is_upper_bound_not_actual_5s_IB_TTL"] = True
    body["original_source_5s_feasible_count_is_optimistic_ceiling"] = True
    body["actual_llm_input_original_quote_expiry_verified"] = False
    body["real_original_native_witness_ack_before_quote_expiry_proven"] = False
    body["true_original_ope_eligible_rows"] = 0
    body["broker_requests_sent"] = 0
    body["broker_orders_sent"] = 0
    body["model_inference_performed"] = False
    body["model_trained"] = False
    body["model_promoted"] = False
    body["automatic_trading"] = False
    body["audit_code_sha256"] = _sha(Path(__file__).read_bytes())
    tests = (Path(__file__).resolve().parents[2]
             / "tests/python/test_train_v2_original_model_timing.py")
    if not tests.is_file() or tests.is_symlink():
        raise ValueError("TIMING_ADVERSARIAL_TEST_SOURCE_MISSING")
    body["adversarial_tests_source_sha256"] = _sha(tests.read_bytes())
    return body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-release-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    outcome = audit(args.original_release_root)
    sha = write_once(args.output, outcome)
    observed = outcome["combined_original_quote_to_decision"]
    model = outcome["combined_model_request_to_decision"]
    print(json.dumps({
        "assessment": outcome["assessment"],
        "complete_original_model_graphs": outcome[
            "original_observed_model_return_count"],
        "model_p50_ms": model["p50_ms"],
        "model_p95_ms": model["p95_ms"],
        "quote_to_decision_p50_ms": observed["p50_ms"],
        "quote_to_decision_p95_ms": observed["p95_ms"],
        "best_case_5s_model_returns": outcome[
            "observed_model_returns_within_optimistic_ttl"],
        "beyond_best_case_5s": outcome[
            "observed_model_returns_exceeding_optimistic_ttl"],
        "unknown_graphs": outcome["original_incomplete_capture_by_reason"],
        "true_ope_eligible": 0,
        "receipt_sha256": sha,
        "broker_orders_sent": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
