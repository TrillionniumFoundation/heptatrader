"""Read-only, bounded, *observed* all-action PAPER model-decision provenance.

Rejects tampered local journal chains and checks REQUEST/RESPONSE/DECISION
identity for every captured original model selection, including HOLD.
Crucially, an instrument's observed source journal is NOT the exhaustive
set of opportunities or the original stochastic model's behavior propensity.
No broker query, model dispatch, LedgerWriter, signature, or training.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import stat
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from hepta_decision_attribution import (
    MAX_RECORD, MAX_STREAM, SCHEMA as ORIGINAL_SCHEMA, digest as original_digest,
    read_private, strict,
)
from .original_policy_audit import verify_release, write_once

SCHEMA = "heptatrader.train-v2-original-observed-decision-population.v1"
MAX_MANIFESTS = 32
MAX_FILES = 128
MAX_TOTAL_BYTES = 256 * 1024 * 1024
KINDS = frozenset(("HEADER", "REQUEST", "RESPONSE", "DECISION",
                   "COMMAND_BINDING", "UNBOUND_DECISION", "UNBOUND_COMMAND",
                   "OBSERVER_ERROR"))
ACTIONS = frozenset(("BUY", "SELL", "HOLD"))
INPUT_SCOPE = "EXACT_LOCAL_INFER_CLI_REQUEST_NOT_UNOBSERVED_PROVIDER_INTERNAL_CONTEXT"
# The actual installed BTC forecast decorator is NOT the same as the
# research worktree wrapper. Only this exact deployed, source-hashed
# transform is admitted; a new code version requires a fresh audit.
BTC_INSTALLED_WRAPPER_SHA256 = (
    "b9e18cd97fb3ad6345a67a9b53ac6229ee48a0e0b707e02765b54dd4969d04e2"
)
BTC_INSTALLED_POLICY_SHA256 = (
    "aa4ef347511117a2c18596f0aa2feef3d5f40a01694e67c2381501ed4c69edb1"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request_id(row: dict) -> str:
    rid = row.get("request_id")
    if not isinstance(rid, str) or not 1 <= len(rid) <= 256 or "\0" in rid:
        raise ValueError("BAD_ORIGINAL_REQUEST_ID")
    return rid


def _source_directory(directory: Path) -> None:
    if not directory.is_absolute() or ".." in directory.parts:
        raise ValueError("ORIGINAL_JOURNAL_ROOT_NOT_ABSOLUTE")
    for ancestor in (directory, directory.parent):
        if ancestor.is_symlink():
            raise ValueError("ORIGINAL_JOURNAL_SYMLINK")
    st = directory.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise ValueError("ORIGINAL_JOURNAL_OWNER_SCOPE")


def _read_immutable_prefix(file: Path) -> tuple[list[dict], dict]:
    """An active journal can append; never consume an unfinished last line."""
    raw = read_private(file, MAX_STREAM)
    cutoff = raw.rfind(b"\n") + 1
    prefix = raw[:cutoff]
    tail = len(raw) - cutoff
    if not prefix:
        raise ValueError("EMPTY_ORIGINAL_JOURNAL")
    rows = []
    for line in prefix.splitlines():
        if len(line) > MAX_RECORD:
            raise ValueError("ORIGINAL_JOURNAL_ROW_OVER_BOUND")
        row = strict(line)
        if (not isinstance(row, dict) or row.get("schema") != ORIGINAL_SCHEMA
                or row.get("kind") not in KINDS
                or type(row.get("sequence")) is not int
                or row.get("sequence") < 0
                or not isinstance(row.get("producer_id"), str)
                or not isinstance(row.get("data"), dict)
                or row.get("sha256") != original_digest({
                    k: v for k, v in row.items() if k != "sha256"})):
            raise ValueError("ORIGINAL_JOURNAL_ROW_TAMPERED")
        rows.append(row)
    if len({r["producer_id"] for r in rows}) != 1:
        raise ValueError("ORIGINAL_SEGMENT_MULTIPLE_PRODUCERS")
    # An append is permitted, but source prefix substitution is not.
    second = read_private(file, MAX_STREAM)
    if len(second) < cutoff or _sha(second[:cutoff]) != _sha(prefix):
        raise ValueError("ORIGINAL_JOURNAL_CHANGED_ON_REREAD")
    return rows, {
        "source_path_sha256": _sha(str(file).encode()),
        "verified_prefix_sha256": _sha(prefix),
        "verified_prefix_bytes": cutoff,
        "not_admitted_unfinished_tail_bytes": tail,
    }


def _scan_original(directory: Path) -> dict:
    _source_directory(directory)
    files = sorted(directory.glob("*.jsonl"))
    if len(files) > MAX_FILES:
        raise ValueError("ORIGINAL_JOURNAL_FILE_COUNT_EXCEEDED")
    groups: dict[str, list[dict]] = defaultdict(list)
    receipts = []
    total_bytes = 0
    for file in files:
        if file.is_symlink() or file.parent != directory:
            raise ValueError("ORIGINAL_JOURNAL_PATH_ESCAPED")
        rows, source_receipt = _read_immutable_prefix(file)
        total_bytes += source_receipt["verified_prefix_bytes"]
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("ORIGINAL_JOURNAL_BYTES_EXCEEDED")
        receipts.append(source_receipt)
        groups[rows[0]["producer_id"]].extend(rows)

    output = []
    for producer, rows in groups.items():
        rows.sort(key=lambda x: x["sequence"])
        prev = "0"*64
        now = int(time.time_ns() // 1_000_000)
        for i, row in enumerate(rows):
            if (row["sequence"] != i or row.get("previous") != prev
                    or type(row.get("captured_at_ms")) is not int
                    or row["captured_at_ms"] < 1
                    or row["captured_at_ms"] > now
                    or type(row.get("dropped_total")) is not int
                    or row["dropped_total"] < 0):
                raise ValueError("ORIGINAL_JOURNAL_PRODUCER_CHAIN_GAP")
            prev = row["sha256"]
        headers = [r for r in rows if r["kind"]=="HEADER"]
        if (not headers or rows[0]["kind"] != "HEADER"
                or len({h["data"].get("manifest_sha256") for h in headers}) != 1):
            raise ValueError("ORIGINAL_JOURNAL_PRODUCER_MANIFEST_MIX")
        health_file = directory / (producer + ".health.json")
        health = strict(read_private(health_file, 65536)) if health_file.exists() else None
        if health is not None and (health.get("producer_id") != producer
                                   or health.get("order_operations") != 0):
            raise ValueError("ORIGINAL_JOURNAL_HEALTH_SCOPE_CHANGED")
        output.append({
            "producer": producer, "manifest_sha256": headers[0]["data"]["manifest_sha256"],
            "rows": rows,
            "local_drops": any(r.get("dropped_total") for r in rows),
            "health_dropped_or_failed": (
                bool(health.get("dropped") or health.get("error"))
                if health is not None else None),
        })
    return {"producers": output, "files": receipts, "file_count": len(files),
            "verified_total_prefix_bytes": total_bytes}


def _verified_btc_forecast(decoded: dict, selected: dict, request: dict,
                           response: dict, decision: dict) -> bool:
    """Verify the BTC post-model decorator, NOT a model-action propensity.

    This is an original deterministic output transform used by the actual
    pinned BTC-side economics adapter. The extra issued/available/input
    fields cannot be trusted merely because they were added after inference.
    """
    from hepta_btc_entry_policy import digest as btc_digest
    from hepta_btc_entry_policy import validate_forecast
    expected_decoded = {"action", "confidence", "rationale",
                        "expected_move_bps", "horizon_sec"}
    expected_selected = expected_decoded | {
        "issued_at_ms", "available_at_ms", "reference_mid",
        "input_sha256", "model_id", "forecast_schema"}
    if (not isinstance(decoded, dict) or set(decoded) != expected_decoded
            or not isinstance(selected, dict)
            or set(selected) != expected_selected
            or any(selected[key] != decoded[key] for key in expected_decoded)
            or selected["model_id"] != request.get("model_id")
            or selected["forecast_schema"] != "heptatrader.btc-return-forecast.v1"):
        return False
    history = request.get("history_snapshot")
    if not isinstance(history, list) or not 1 <= len(history) <= 24:
        return False
    try:
        compact = [{
            "t": row["observed_at_ms"],
            "bid": row["bid"], "ask": row["ask"], "mid": row["mid"],
        } for row in history]
        if any(type(row["t"]) is not int or row["t"] <= 0
               or any(type(row[field]) is bool for field in
                      ("bid", "ask", "mid"))
               for row in compact):
            return False
        from hepta_btc_entry_policy import number
        reference = str((number(compact[-1]["bid"])
                         + number(compact[-1]["ask"])) / 2)
        if (selected["issued_at_ms"] != compact[-1]["t"]
                or selected["reference_mid"] != reference
                or selected["input_sha256"] != btc_digest(compact)
                or type(selected["available_at_ms"]) is not int
                or not response["available_at_ms"] <=
                    selected["available_at_ms"] <=
                    decision["available_at_ms"]):
            return False
        validate_forecast(selected)
        return True
    except (KeyError, ValueError, TypeError, InvalidOperation, AttributeError):
        return False


def _graph_decision(request, response, decision,
                    request_seq: int, response_seq: int, decision_seq: int,
                    instrument: str, period: str) -> str:
    """Return exact selected original output; raise on unbound/tampered link."""
    if not 0 <= request_seq < response_seq < decision_seq:
        raise ValueError("ORIGINAL_DECISION_CAUSAL_ORDER")
    if (request.get("instrument") != instrument
            or request.get("period_sha256") != period
            or request.get("input_scope") != INPUT_SCOPE):
        raise ValueError("ORIGINAL_REQUEST_SCOPE")
    prompt = request.get("prompt")
    if (not isinstance(prompt, str)
            or _sha(prompt.encode()) != request.get("prompt_sha256")):
        raise ValueError("ORIGINAL_MODEL_PROMPT_TAMPERED")
    req_digest = original_digest(request)
    resp_digest = original_digest(response)
    if (response.get("request_sha256") != req_digest
            or decision.get("request_sha256") != req_digest
            or decision.get("response_sha256") != resp_digest):
        raise ValueError("ORIGINAL_INPUT_RESPONSE_IDENTITY")
    a = request.get("started_at_ms")
    b = response.get("available_at_ms")
    c = decision.get("available_at_ms")
    if (type(a) is not int or type(b) is not int or type(c) is not int
            or not 0 < a <= b <= c):
        raise ValueError("ORIGINAL_MODEL_AVAILABLE_ORDER")
    if response.get("returncode") != 0:
        raise ValueError("ORIGINAL_MODEL_CALL_FAILED")
    envelope = strict(response["stdout"])
    outputs = envelope.get("outputs") if isinstance(envelope, dict) else None
    if envelope.get("ok") is not True or not isinstance(outputs, list) or len(outputs) != 1:
        raise ValueError("ORIGINAL_MODEL_ENVELOPE_SCOPE")
    decoded = strict(outputs[0]["text"])
    selected = decision.get("decision")
    if (not isinstance(selected, dict)
            or decision.get("decision_sha256") != original_digest(selected)):
        raise ValueError("ORIGINAL_DECISION_DIGEST_MISMATCH")
    if (instrument == "BTC.USD" and selected != decoded
            and _verified_btc_forecast(decoded, selected, request, response, decision)):
        profile = "BTC_EXACT_DECLARED_POSTMODEL_FORECAST_DECORATOR"
    elif (set(selected) == {"action", "confidence", "rationale"}
          and selected == decoded):
        profile = "EXACT_LITERAL_THREE_FIELD_MODEL_OUTPUT"
    else:
        raise ValueError("ORIGINAL_DECISION_NOT_DECLARED_OUTPUT_TRANSFORM")
    action = selected["action"]
    confidence = selected["confidence"]
    if (action not in ACTIONS or type(confidence) not in (float, int)
            or not math.isfinite(confidence) or not 0 <= confidence <= 1
            or not isinstance(selected["rationale"], str)):
        raise ValueError("ORIGINAL_MODEL_DECISION_FORMAT")
    return action, profile


def analyze_producer(rows: list[dict], instrument: str, period: str) -> dict:
    """Verified local captured model graph, not an eligible OPE population."""
    captures: dict[str, dict] = defaultdict(dict)
    counts = Counter()
    invalid = Counter()
    actions = Counter()
    decode_profiles = Counter()
    original_open_request_ids = []
    for row in rows:
        kind = row["kind"]
        counts[kind] += 1
        if kind not in ("REQUEST", "RESPONSE", "DECISION"):
            if kind in ("UNBOUND_DECISION", "UNBOUND_COMMAND", "OBSERVER_ERROR"):
                invalid[kind] += 1
            if kind == "COMMAND_BINDING" and "request_id" in row["data"]:
                original_open_request_ids.append(row["data"]["request_id"])
            continue
        rid = _request_id(row["data"])
        group = captures[rid]
        if kind in group:
            raise ValueError("ORIGINAL_REQUEST_DUPLICATE_KIND")
        group[kind] = (row["data"], row["sequence"])
    for rid, group in captures.items():
        if "DECISION" not in group:
            invalid["ORIGINAL_DECISION_NOT_RECORDED"] += 1
            continue
        if "REQUEST" not in group or "RESPONSE" not in group:
            invalid["ORIGINAL_INCOMPLETE_DECISION_CHAIN"] += 1
            continue
        try:
            action, profile = _graph_decision(*(
                group[k][0] for k in ("REQUEST", "RESPONSE", "DECISION")),
                *(group[k][1] for k in ("REQUEST", "RESPONSE", "DECISION")),
                instrument, period)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            invalid[str(exc)[:100] if str(exc) else type(exc).__name__] += 1
            continue
        actions[action] += 1
        decode_profiles[profile] += 1
    return {
        "raw_event_counts": dict(sorted(counts.items())),
        "unique_original_model_request_ids": len(captures),
        "exact_three_stage_observed_decision_graph_count": sum(actions.values()),
        "verified_original_selected_model_output_counts":
            dict(sorted(actions.items())),
        "exact_source_decision_decode_profiles":
            dict(sorted(decode_profiles.items())),
        "unverified_or_missing_graph_counts": dict(sorted(invalid.items())),
        "locally_observed_prepared_open_request_id_count":
            len(original_open_request_ids),
        "complete_original_behavior_policy_decision_population_attested": False,
        "entire_original_action_support_attested": False,
        "true_original_action_propensities_attested": 0,
        "off_policy_evaluation_eligible_decisions": 0,
        "canonical_product_ledger_decisions_appended": 0,
        "independent_product_generator_root_enrolled": False,
        "independent_product_observer_root_enrolled": False,
        "source_capture_failure_absence_guaranteed": False,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
    }


def audit(root: Path, *, release_verifier=verify_release) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("ORIGINAL_MANIFEST_ROOT_UNSAFE")
    manifests = sorted(root.glob("*/*/manifest.json"))
    if not 1 <= len(manifests) <= MAX_MANIFESTS:
        raise ValueError("ORIGINAL_MANIFEST_COUNT_BOUND")
    results = []
    cache = {}
    total = Counter()
    for path in manifests:
        verified = release_verifier(path)
        from hepta_decision_attribution import read_private, strict
        raw = read_private(path,65536)
        m = strict(raw)
        original_sha = _sha(raw)
        if m.get("instrument") == "BTC.USD":
            runtime_pins = m.get("runtime_sha256", {})
            current = Path(__file__).resolve().parents[2] / "scripts" / "hepta_btc_entry_policy.py"
            if (runtime_pins.get("heptatrader_btc_economics_driver.py") !=
                    BTC_INSTALLED_WRAPPER_SHA256
                    or runtime_pins.get("hepta_btc_entry_policy.py") !=
                    BTC_INSTALLED_POLICY_SHA256
                    or _sha(current.read_bytes()) != BTC_INSTALLED_POLICY_SHA256):
                raise ValueError("UNSUPPORTED_BTC_ORIGINAL_DECORATOR_VERSION")
        if verified["manifest_sha256"] != original_sha:
            raise ValueError("ORIGINAL_RELEASE_VERIFIER_MANIFEST_MISMATCH")
        source = Path(m["journal_directory"])
        if source not in cache:
            cache[source] = _scan_original(source)
        reader = cache[source]
        candidate = [x for x in reader["producers"]
                     if x["manifest_sha256"] == original_sha]
        if not candidate:
            raise ValueError("ORIGINAL_PRODUCER_MISSING_FOR_MANIFEST")
        combined = Counter()
        actions = Counter()
        decode_profiles = Counter()
        invalid = Counter()
        verified_count = 0
        all_observed_requests = 0
        for producer in candidate:
            header = [r for r in producer["rows"] if r["kind"] == "HEADER"]
            if any(h["data"].get("instrument") != m["instrument"]
                   or h["data"].get("period_sha256") != m["period_sha256"]
                   or h["data"].get("execution_domain") != "PAPER"
                   or h["data"].get("source_kind") != "PINNED_DRIVER_CALLSITE"
                   or h["data"].get("trading_authority") is not False
                   for h in header):
                raise ValueError("ORIGINAL_PRODUCER_HEADER_WRONG_SCOPE")
            one = analyze_producer(producer["rows"],m["instrument"],m["period_sha256"])
            combined.update(one["raw_event_counts"])
            actions.update(one["verified_original_selected_model_output_counts"])
            decode_profiles.update(one["exact_source_decision_decode_profiles"])
            invalid.update(one["unverified_or_missing_graph_counts"])
            verified_count += one["exact_three_stage_observed_decision_graph_count"]
            all_observed_requests += one["unique_original_model_request_ids"]
            if producer["local_drops"]:
                invalid["ORIGINAL_JOURNAL_DROPPED_EVENTS"] += 1
            if producer["health_dropped_or_failed"] is True:
                invalid["ORIGINAL_PRODUCER_HEALTH_DROPPED_OR_FAILED"] += 1
            if producer["health_dropped_or_failed"] is None:
                invalid["PRODUCER_HEALTH_NOT_PRESENT"] += 1
        results.append({
            "instrument": m["instrument"],
            "trading_behavior_changed": m["trading_behavior_changed"],
            "original_manifest_sha256": original_sha,
            "verified_source_runtime_files": verified["runtime_file_count_verified"],
            "observed_producer_process_count": len(candidate),
            "verified_original_journal_files_in_shared_source": reader["file_count"],
            "original_source_prefix_witness_set_sha256": _sha(json.dumps(
                reader["files"],sort_keys=True,separators=(",",":")).encode()),
            "original_full_verified_prefix_bytes": reader["verified_total_prefix_bytes"],
            "raw_event_counts": dict(sorted(combined.items())),
            "observed_unique_original_model_requests": all_observed_requests,
            "valid_observed_model_decision_graphs": verified_count,
            "observed_selected_action_counts": dict(sorted(actions.items())),
            "verified_original_model_output_profile_counts":
                dict(sorted(decode_profiles.items())),
            "unverified_or_missing_graph_issue_counts":dict(sorted(invalid.items())),
            "original_verified_open_command_bindings":
                verified["exact_original_command_binding_count"],
            "original_command_binding_issues": verified["binding_issue_count"],
            "signed_product_generator_decisions_written": 0,
            "original_behavior_propensities_attested": 0,
        })
        total.update(combined)
    return {
        "schema": SCHEMA,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_kind": "ORIGINAL_LOCAL_HASH_CHAIN_NOT_BROKER_SIGNED",
        "manifest_count": len(results),
        "per_original_release": results,
        "raw_total_counts": dict(sorted(total.items())),
        "total_verified_observed_model_decision_graphs":
            sum(x["valid_observed_model_decision_graphs"] for x in results),
        "total_original_open_command_bindings":
            sum(x["original_verified_open_command_bindings"] for x in results),
        "total_observed_actions_by_exact_model_output":dict(sorted(
            Counter({action:sum(x["observed_selected_action_counts"].get(action,0)
                                for x in results) for action in ACTIONS}).items())),
        "model_confidence_not_action_selection_propensity": True,
        "not_all_policy_decision_opportunities_witnessed": True,
        "complete_original_behavior_population_attested": False,
        "complete_legal_action_candidate_set_attested": False,
        "true_original_action_propensity_attested": 0,
        "eligible_original_ope_rows": 0,
        "generator_observer_independent_product_roles_activated": False,
        "native_product_ledger_writer_witness_acked": False,
        "snapshot_v3_frozen_from_product": False,
        "signed_corrections_revocations_and_independent_results": False,
        "new_real_after_cost_forward_efficacy": False,
        "broker_queries_or_orders_sent": 0,
        "trade_authority": False,
        "assessment": "HOLD_OBSERVED_ONLY_MISSING_FULL_POLICY_SUPPORT_AND_PROPENSITY",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--releases-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = audit(args.releases_root)
    receipt_sha = write_once(args.output, result)
    print(json.dumps({
        "assessment":result["assessment"],
        "raw_total_counts":result["raw_total_counts"],
        "valid_observed_model_graphs":result["total_verified_observed_model_decision_graphs"],
        "action_counts":result["total_observed_actions_by_exact_model_output"],
        "original_ope_rows":result["eligible_original_ope_rows"],
        "receipt_sha256":receipt_sha,
        "broker_requests_or_orders_sent":0,
    },sort_keys=True))


if __name__ == "__main__":
    main()
