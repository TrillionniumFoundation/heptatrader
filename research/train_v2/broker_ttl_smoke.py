"""Read-only on original source: prove new quote expiry *timing* feasibility.

No original PAPER request, Ed25519 signing keys, model load, timer or trade.
This only inspects already retained local-source quote chain with both profiles.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

from historical_training.common import sha
from .adapters import make_spec, sealed_live_segment
from .causal_ttl import sealed_original_broker_expiry
from .contracts import ContractError, compile_example
from .parity import compare_numeric_laya_price_inputs


def run(period_file, original_segment):
    period_hash = sha(period_file)
    tag = "RESEARCH_ONLY_NEW_EXPLICIT_TTL_PROFILE_NO_SELECTED_MODEL"
    old = make_spec(period_hash, tag)
    new = make_spec(period_hash, tag, broker_reported_expiry=True)
    old_rows, old_proof = sealed_live_segment(original_segment, old)
    rows, proof = sealed_original_broker_expiry(original_segment, new)
    if len(old_rows) != len(rows) or len(rows) < 20:
        raise ContractError("TTL_ORIGINAL_SOURCE_PROFILE_DISAGREES")
    valid = 0
    old_refusals = 0
    windows = []
    parity = None
    for index in range(5, min(len(rows)-1, 260)):
        q = rows[index]
        decision_at = q["available_at_ms"] + 200
        if (decision_at >= rows[index+1]["available_at_ms"]
                or decision_at > q["valid_until_ms"]):
            continue
        try:
            example = compile_example(new, rows, decision_at, decision_at)
        except ContractError:
            continue
        if example["label_status"] != "PENDING_OUTCOME":
            raise ContractError("TTL_EARLY_OUTCOME_FABRICATED")
        if not example["locally_point_in_time"] or example["candidate_training_eligible"]:
            raise ContractError("TTL_SOURCE_AUTHORITY_UPGRADED")
        valid += 1
        windows.append(q["valid_until_ms"]-q["available_at_ms"])
        try:
            compile_example(old, old_rows, decision_at, decision_at)
        except ContractError as exc:
            if str(exc) != "FEATURE_QUOTE_EXPIRED":
                raise
            old_refusals += 1
        else:
            raise ContractError("TTL_LEGACY_V1_EXPIRED_SOURCE_REPLAYED")
        if parity is None:
            try:
                check = compare_numeric_laya_price_inputs(
                    new, rows, decision_at, horizon_sec=300)
            except ContractError:
                pass
            else:
                if len(check["numeric16"]) != 16 or len(check["laya_price_question"]["crit"]) != 7:
                    raise ContractError("TTL_ORIGINAL_MODEL_FEATURES_CHANGED")
                parity = {
                    "feature_width": 16,
                    "laya_future_return_classes": 7,
                    "requires_new_model_profile": True,
                    "actual_model_executed": False,
                }
    if not valid or old_refusals != valid or parity is None:
        raise ContractError("TTL_REAL_SOURCE_READONLY_FEASIBILITY_NOT_ESTABLISHED")
    return {
        "schema": "heptatrader.train-v2-broker-expiry-readonly-v1",
        "original_source_quote_count": len(rows),
        "original_source_prefix_sha256": proof["verified_prefix_sha256"],
        "original_local_chain_head": proof["original_chain_head"],
        "new_profile_valid_decisions_200ms_after_received": valid,
        "old_arrival_only_profile_refusals_same_decisions": old_refusals,
        "minimum_observed_quote_ttl_remaining_ms": min(windows),
        "maximum_observed_quote_ttl_remaining_ms": max(windows),
        "numeric_laya_semantic_parity": parity,
        "broker_ttl_provenance": proof["expiry_provenance"],
        "period_sha256": period_hash,
        "source_code_sha256": {
            name: sha(Path(__file__).with_name(name))
            for name in (
                "contracts.py", "adapters.py", "causal_ttl.py",
                "parity.py", "handoff.py", "external_signatures.py",
                "signed_handoff.py", "broker_ttl_smoke.py",
            )
        },
        "model_signature_witness_created": False,
        "new_forward_trial_registered": False,
        "new_realtime_quote_obtained": False,
        "automatic_training": False,
        "model_selected": False,
        "canonical_agentd_owner_enrolled": False,
        "broker_queries_sent": 0,
        "broker_orders_sent": 0,
        "production_mutation": False,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", type=Path, required=True)
    ap.add_argument("--original-segment", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    os.umask(0o077)
    result = run(args.period, args.original_segment)
    dest = args.output
    if dest.is_symlink() or dest.exists():
        raise ContractError("TTL_IMMUTABLE_NEW_OUTPUT_REQUIRED")
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = (json.dumps(result, sort_keys=True, indent=2)+"\n").encode()
    fd = os.open(dest, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    parent = os.open(dest.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    print(json.dumps({
        "valid_delayed_decisions": result["new_profile_valid_decisions_200ms_after_received"],
        "legacy_refusals": result["old_arrival_only_profile_refusals_same_decisions"],
        "numeric16_and_laya7": True,
        "signed_new_event": False,
        "broker_orders_sent": 0,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }, sort_keys=True))


if __name__=="__main__":
    main()
