"""Native preaction time qualification from ORIGINAL source and signed receipts.

Phase 4S. The earlier actual Phase 4L market experiment signed an original
PRICE-only shadow decision before its 5-minute endpoint, but after quote TTL.
This module validates original signed bytes and price source without modifying
the old experiment. Native signatures do not confer original trader authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from historical_training.common import read, sha
from .adapters import make_spec
from .causal_ttl import sealed_original_broker_expiry
from .real_source_native_signature_verify import (
    verify as verify_native_signatures, _utc_ms,
)
from .contracts import ContractError, digest

SCHEMA = "heptatrader.train-v2-native-preaction-timeline-audit.v1"
NATIVE_DOMAIN = b"hepta.learning-ledger.signed-evidence.v1"
ORIGINAL_DECISION_SHA = "0ec5ffc1e73158305916af6cd96cc68a76b73bd2d3f0f6727e0ed5eb66a87fb4"
ORIGINAL_NATIVE_INIT_SIGNATURE_SHA = "4c28088d53484be3d95c8f3cb3e638a146f9f6f709e261701adc6f5a56759023"
ORIGINAL_NATIVE_ACK_SHA = "62e2014cfcfa5b56e0b5e0cb558f6837450e036dfc144382f5a917c34b83a6d0"
BLOCKED = "HOLD_ORIGINAL_NATIVE_SIGNATURE_AND_WITNESS_TOO_LATE_FOR_QUOTE"


def _u64(data: bytes, offset: int):
    if offset + 8 > len(data):
        raise ContractError("NATIVE_TIMELINE_TRUNCATED_U64")
    return int.from_bytes(data[offset:offset + 8], "big"), offset + 8


def _native_id(data: bytes, offset: int):
    size, offset = _u64(data, offset)
    if not 1 <= size <= 128 or size + offset > len(data):
        raise ContractError("NATIVE_TIMELINE_ID_BOUND")
    raw = data[offset:offset + size]
    try:
        name = raw.decode("ascii")
    except UnicodeError as exc:
        raise ContractError("NATIVE_TIMELINE_ID_ASCII") from exc
    if any(ord(c) < 33 or ord(c) > 126 for c in name):
        raise ContractError("NATIVE_TIMELINE_ID_ASCII")
    return name, offset + size


def native_generator_time(entry):
    """Decode signed at-issuance time from original Rust native signing bytes.

    This is signed content and explicitly NOT a trusted monotonic host clock.
    The entire transcript must first pass real Ed25519 verification by
    real_source_native_signature_verify.verify().
    """
    if entry.get("role") != "generator":
        raise ContractError("NATIVE_TIMELINE_NOT_GENERATOR")
    try:
        raw = bytes.fromhex(entry["public_message_hex"])
    except (TypeError, KeyError, ValueError) as exc:
        raise ContractError("NATIVE_TIMELINE_MESSAGE_HEX") from exc
    if not 0 < len(raw) <= 4096 or not raw.startswith(NATIVE_DOMAIN):
        raise ContractError("NATIVE_TIMELINE_WRONG_DOMAIN")
    offset = len(NATIVE_DOMAIN)
    evidence_id, offset = _native_id(raw, offset)
    principal, offset = _native_id(raw, offset)
    if principal != "lab.generator" or not evidence_id.startswith("lab."):
        raise ContractError("NATIVE_TIMELINE_WRONG_PRINCIPAL")
    if offset >= len(raw) or raw[offset] != 0:
        raise ContractError("NATIVE_TIMELINE_WRONG_ROLE")
    offset += 1
    # Exact original SignedLearningEvidenceV1 order:
    # trust digest, scope digest, objective digest; then epoch and times.
    if offset + 96 + 8 * 3 + 32 != len(raw):
        raise ContractError("NATIVE_TIMELINE_FIELD_LAYOUT")
    digests = [raw[offset + i * 32:offset + (i + 1) * 32] for i in range(3)]
    if any(d == bytes(32) for d in digests):
        raise ContractError("NATIVE_TIMELINE_EMPTY_TRUST_SCOPE_OR_OBJECTIVE")
    offset += 96
    epoch, offset = _u64(raw, offset)
    issued, offset = _u64(raw, offset)
    expires, offset = _u64(raw, offset)
    if not epoch or not 0 < issued < expires:
        raise ContractError("NATIVE_TIMELINE_ISSUER_PERIOD")
    signed_host_time = _utc_ms(entry["signed_at_utc"])
    if not 0 <= signed_host_time - issued <= 5000:
        raise ContractError("NATIVE_TIMELINE_ISSUER_CLOCK_INCONSISTENT")
    return {
        "native_signed_message_sha256": hashlib.sha256(raw).hexdigest(),
        "native_principal": principal,
        "native_authority_epoch": epoch,
        "native_issued_at_ms": issued,
        "remote_reported_signed_at_ms": signed_host_time,
        "native_expires_at_ms": expires,
        "remote_host_wall_clock_is_product_attested": False,
        "native_timestamp_parser_does_not_verify_signature": True,
    }


def evaluate_chronology(*, quote_at_ms, quote_known_ms, quote_expires_ms,
                         decision_at_ms, signed_native_issued_ms,
                         signed_host_at_ms, native_ack_at_ms, outcome_due_ms,
                         prepared_at_ms=None, original_llm_propensity=False):
    """Stateless chronology only. Positive local time never grants authority."""
    times = (quote_at_ms, quote_known_ms, quote_expires_ms, decision_at_ms,
             signed_native_issued_ms, signed_host_at_ms, native_ack_at_ms,
             outcome_due_ms)
    if any(type(v) is not int or v <= 0 for v in times):
        raise ContractError("NATIVE_PREACTION_TIME_TYPE_OR_ZERO")
    if not quote_at_ms <= quote_known_ms <= quote_expires_ms <= quote_at_ms + 5000:
        raise ContractError("NATIVE_PREACTION_ORIGINAL_QUOTE_CLOCK")
    if not signed_native_issued_ms <= signed_host_at_ms <= native_ack_at_ms:
        raise ContractError("NATIVE_PREACTION_SIGNER_OR_WITNESS_ORDER")
    if not outcome_due_ms == decision_at_ms + 300000:
        raise ContractError("NATIVE_PREACTION_OUTCOME_HORIZON_CHANGED")
    if type(original_llm_propensity) is not bool:
        raise ContractError("NATIVE_PREACTION_PROPENSITY_ATTESTATION_TYPE")
    if prepared_at_ms is not None and (type(prepared_at_ms) is not int
                                       or prepared_at_ms <= 0):
        raise ContractError("NATIVE_PREACTION_PREPARED_TYPE")
    reasons = []
    if decision_at_ms < quote_known_ms or decision_at_ms > quote_expires_ms:
        reasons.append("ORIGINAL_DECISION_OUTSIDE_KNOWN_QUOTE_TTL")
    if signed_native_issued_ms > quote_expires_ms:
        reasons.append("NATIVE_GENERATOR_ISSUED_AFTER_ORIGINAL_QUOTE_EXPIRED")
    if signed_host_at_ms > quote_expires_ms:
        reasons.append("MAC_GENERATOR_SIGNED_AFTER_ORIGINAL_QUOTE_EXPIRED")
    if native_ack_at_ms > quote_expires_ms:
        reasons.append("NATIVE_WITNESS_ACKED_AFTER_ORIGINAL_QUOTE_EXPIRED")
    if signed_native_issued_ms >= outcome_due_ms or native_ack_at_ms >= outcome_due_ms:
        reasons.append("NATIVE_RESEARCH_DECISION_NOT_PREOUTCOME")
    if prepared_at_ms is None:
        reasons.append("ORIGINAL_BROKER_PREPARED_IDENTITY_NOT_ATTESTED")
    elif (native_ack_at_ms > prepared_at_ms
          or signed_host_at_ms > prepared_at_ms):
        reasons.append("NATIVE_DECISION_OR_WITNESS_AFTER_ORIGINAL_PREPARED")
    if not original_llm_propensity:
        reasons.append("ORIGINAL_OPAQUE_LLM_BEHAVIOR_PROPENSITY_MISSING")
    return {
        "original_quote_valid_at_shadow_selection": (
            quote_known_ms <= decision_at_ms <= quote_expires_ms),
        "native_issued_before_quote_expiration": signed_native_issued_ms <= quote_expires_ms,
        "remote_signed_before_quote_expiration": signed_host_at_ms <= quote_expires_ms,
        "native_ack_before_quote_expiration": native_ack_at_ms <= quote_expires_ms,
        "native_preoutcome_only": native_ack_at_ms < outcome_due_ms,
        "original_prepared_identity_attested": prepared_at_ms is not None,
        "native_ack_before_original_prepared": (
            prepared_at_ms is not None and native_ack_at_ms <= prepared_at_ms),
        "original_model_logged_action_propensity_attested": original_llm_propensity,
        "native_issued_after_quote_expiry_ms": signed_native_issued_ms - quote_expires_ms,
        "remote_signed_after_quote_expiry_ms": signed_host_at_ms - quote_expires_ms,
        "native_ack_after_quote_expiry_ms": native_ack_at_ms - quote_expires_ms,
        "native_signature_after_shadow_selection_ms": signed_host_at_ms - decision_at_ms,
        "reasons": reasons,
        "research_preaction_timeline_constraints_met": not reasons,
        "canonical_owner_or_trading_authority": False,
    }


def inspect_original_phase4l(root, period_file):
    """Revalidate the exact original experiment and classify its time limits."""
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ContractError("NATIVE_PREACTION_SOURCE_DIRECTORY_UNSAFE")
    # Original verifier independently checks four signed public messages,
    # original native decision/outcome, native witness and price-horizon order.
    verified = verify_native_signatures(root)
    if (verified["public_only_ed25519_messages_verified"] != 4
            or verified["native_witness_ack_sequence"] != 2
            or verified["trading_authority"] is not False
            or verified["native_V3_test_data_product_eligible"] is not False):
        raise ContractError("NATIVE_PREACTION_ORIGINAL_SIGNATURES_NOT_VERIFIED")
    frozen = root / "real-shadow-decision-v1.json"
    if sha(frozen) != ORIGINAL_DECISION_SHA:
        raise ContractError("NATIVE_PREACTION_ORIGINAL_DECISION_BYTES_DRIFT")
    decision = read(frozen, 65536)
    packet = read(root / "native-real-quote-packet.json", 65536)
    feature = read(root / "causal-feature-before-outcome.json", 65536)
    acknowledged_path = root / "native-lab-v1/native-source-decision-ack.json"
    acknowledged = read(acknowledged_path, 65536)
    signed_init_path = root / "native-source-time-crosshost-signatures.json"
    if (sha(signed_init_path) != ORIGINAL_NATIVE_INIT_SIGNATURE_SHA
            or sha(acknowledged_path) != ORIGINAL_NATIVE_ACK_SHA):
        raise ContractError("NATIVE_PREACTION_ORIGINAL_SIGNATURE_OR_ACK_BYTES_DRIFT")
    roles = read(signed_init_path, 65536)
    if (decision.get("schema") != "heptatrader.train-v2-shadow-preaction-policy.v1"
            or decision.get("status") != "RESEARCH_SHADOW_PREACTION_ONLY"
            or decision.get("not_an_original_broker_PREPARED_command") is not True
            or decision.get("original_opaque_llm_trading_policy_propensity_qualified") is not False
            or decision.get("trade_authority") is not False
            or packet.get("original_quote_receipt") != decision.get("source_quote_receipt")
            or packet.get("decision_at_ms") != decision.get("decision_at_ms")
            or packet.get("outcome_due_ms") != decision.get("decision_at_ms") + 300000
            or packet.get("original_llm_policy_probability") is not False
            or feature.get("decision_at_ms") != decision.get("decision_at_ms")
            or acknowledged.get("schema") != "heptatrader.train-v2-true-real-source-native-test-lab-decision.v1"
            or acknowledged.get("native_witness_ack_sequence") != 1
            or acknowledged.get("source_receipt") != decision.get("source_quote_receipt")
            or acknowledged.get("decision_at_ms") != decision.get("decision_at_ms")
            or acknowledged.get("source_packet_sha256") != sha(root / "native-real-quote-packet.json")):
        raise ContractError("NATIVE_PREACTION_ORIGINAL_IDENTITY_NOT_MATCHED")
    period_sha256 = sha(period_file)
    spec = make_spec(
        period_sha256, "SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1",
        broker_reported_expiry=True)
    original_path = Path(feature["source_segment_path"])
    if not original_path.is_absolute() or original_path.is_symlink():
        raise ContractError("NATIVE_PREACTION_ORIGINAL_QUOTE_PATH_UNSAFE")
    observations, source = sealed_original_broker_expiry(original_path, spec)
    quote = [x for x in observations
             if x["source_id"] == decision["source_quote_receipt"]]
    if len(quote) != 1:
        raise ContractError("NATIVE_PREACTION_QUOTE_WITNESS_NOT_FOUND")
    quote = quote[0]
    if (quote["event_at_ms"] != decision["source_at_ms"]
            or quote["available_at_ms"] != decision["source_available_at_ms"]
            or quote["valid_until_ms"] != decision["source_expires_at_ms"]
            or decision["source_snapshot_read_finished_at_ms"] > decision["decision_at_ms"]
            or feature.get("original_period_sha256") != period_sha256):
        raise ContractError("NATIVE_PREACTION_VERIFIED_SOURCE_TIME_DRIFT")
    sigs = roles.get("signatures")
    if (not isinstance(sigs, list) or len(sigs) != 2
            or [s.get("role") for s in sigs] != ["root", "generator"]):
        raise ContractError("NATIVE_PREACTION_GENERATOR_ROLE_RECORD_MISSING")
    generator = native_generator_time(sigs[1])
    known = [s for s in verified["verified_signature_events"]
             if s["role"] == "generator"]
    if (len(known) != 1
            or known[0]["message_sha256"] != generator["native_signed_message_sha256"]
            or known[0]["signed_at_ms"] != generator["remote_reported_signed_at_ms"]
            or known[0]["signing_host"] != "MacBook-Pro.local"):
        raise ContractError("NATIVE_PREACTION_PUBLIC_SIGNATURE_MISMATCH")
    ack_at = acknowledged["native_acknowledged_at_ms"]
    chronology = evaluate_chronology(
        quote_at_ms=quote["event_at_ms"],
        quote_known_ms=quote["available_at_ms"],
        quote_expires_ms=quote["valid_until_ms"],
        decision_at_ms=decision["decision_at_ms"],
        signed_native_issued_ms=generator["native_issued_at_ms"],
        signed_host_at_ms=generator["remote_reported_signed_at_ms"],
        native_ack_at_ms=ack_at,
        outcome_due_ms=packet["outcome_due_ms"],
        prepared_at_ms=None,
        original_llm_propensity=False)
    required = {
        "NATIVE_GENERATOR_ISSUED_AFTER_ORIGINAL_QUOTE_EXPIRED",
        "MAC_GENERATOR_SIGNED_AFTER_ORIGINAL_QUOTE_EXPIRED",
        "NATIVE_WITNESS_ACKED_AFTER_ORIGINAL_QUOTE_EXPIRED",
        "ORIGINAL_BROKER_PREPARED_IDENTITY_NOT_ATTESTED",
        "ORIGINAL_OPAQUE_LLM_BEHAVIOR_PROPENSITY_MISSING",
    }
    if not required <= set(chronology["reasons"]):
        raise ContractError("NATIVE_PREACTION_EXPECTED_ACTUAL_BLOCKERS_NOT_PROVEN")
    body = {
        "schema": SCHEMA,
        "assessment": BLOCKED,
        "actual_preexisting_experiment": "PHASE4L_TEST_ONLY_PRICE_SHADOW",
        "original_shadow_source_sha256": ORIGINAL_DECISION_SHA,
        "original_full_quote_segment_verified_sha256": source["verified_prefix_sha256"],
        "original_source_quote_count_at_readback": len(observations),
        "original_quote_receipt": quote["source_id"],
        "original_decision_at_ms": decision["decision_at_ms"],
        "original_quote_at_ms": quote["event_at_ms"],
        "original_quote_available_at_ms": quote["available_at_ms"],
        "original_quote_expires_at_ms": quote["valid_until_ms"],
        "generator_native_issued_at_ms": generator["native_issued_at_ms"],
        "generator_remote_reported_signed_at_ms": generator["remote_reported_signed_at_ms"],
        "native_source_decision_witness_ack_at_ms": ack_at,
        "original_outcome_due_ms": packet["outcome_due_ms"],
        "generator_original_signed_message_sha256":
            generator["native_signed_message_sha256"],
        "original_native_ack_receipt_sha256": sha(acknowledged_path),
        "full_original_crosshost_signatures_verified": True,
        "original_native_decision_and_outcome_durable_record_count": 2,
        "price_only_preoutcome_evidence_retained": True,
        "chronology": chronology,
        "native_source_signature_valid": True,
        "auditor_source_sha256": sha(Path(__file__)),
        "actual_original_native_signature_verifier_sha256": sha(
            Path(verify_native_signatures.__code__.co_filename)),
        "shadow_policy_logged_probability_not_original_llm": True,
        "original_policy_request_and_prepared_complete": False,
        "read_only_frozen_reinspection": True,
        "independent_product_root_or_canonical_owner_enrolled": False,
        "production_native_preaction_admission": False,
        "model_training_or_promotion": False,
        "original_experiment_modified": False,
        "broker_requests_or_orders_sent": 0,
        "trade_authority": False,
    }
    return {**body, "receipt_id": digest(body)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", type=Path, required=True)
    ap.add_argument("--period", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    os.umask(0o077)
    result = inspect_original_phase4l(args.original, args.period)
    dest = args.output
    if dest.is_symlink() or dest.exists():
        raise ContractError("NATIVE_PREACTION_OUTPUT_CREATE_ONCE_REQUIRED")
    dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if dest.parent.is_symlink() or dest.parent.stat().st_mode & 0o077:
        raise ContractError("NATIVE_PREACTION_OUTPUT_DIRECTORY_NOT_PRIVATE")
    raw = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    df = os.open(dest.parent, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(df)
    finally:
        os.close(df)
    print(json.dumps({
        "status": result["assessment"],
        "original_quote_expires_at_ms": result["original_quote_expires_at_ms"],
        "native_generator_signed_after_quote_expiry_ms":
            result["chronology"]["remote_signed_after_quote_expiry_ms"],
        "native_witness_ack_after_quote_expiry_ms":
            result["chronology"]["native_ack_after_quote_expiry_ms"],
        "actual_original_signatures_verified": True,
        "broker_orders_sent": 0,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
