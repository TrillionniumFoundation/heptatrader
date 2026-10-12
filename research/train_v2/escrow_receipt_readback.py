"""Read-only exact comparison of a test Escrow ACK against remote-origin bytes.

No signer, private key, ledger write, broker request or production authority.
The origin file must be read through an independently authenticated channel.
This module cannot attest host identity, remote fsync or hardware custody.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from .real_source_native_remote_escrow import canonical, OWNERS, DOMAIN, RECEIPT_SCHEMA, SCHEMA


def verify_returned_bytes(*, stage: str, claimed_ack: dict,
                          expected_intent: dict, pinned_test_public_hex: str,
                          independent_origin_file: bytes | None) -> dict:
    if stage not in OWNERS:
        raise ValueError("TEST_ESCROW_STAGE_INVALID")
    if (not isinstance(expected_intent,dict)
            or expected_intent.get("schema")!=SCHEMA
            or expected_intent.get("stage")!=stage
            or expected_intent.get("native_signer")!=
               ("generator" if stage=="decision" else "observer")
            or expected_intent.get("source_policy")!=
               "SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1"
            or expected_intent.get("test_only") is not True
            or expected_intent.get("product_trading_authority") is not False
            or type(expected_intent.get("source_time_ms")) is not int
            or type(expected_intent.get("outcome_due_ms")) is not int
            or expected_intent["source_time_ms"]<=0
            or expected_intent["outcome_due_ms"]<=0
            or (stage=="decision" and
                expected_intent["outcome_due_ms"]<=expected_intent["source_time_ms"])
            or (stage=="outcome" and
                expected_intent["source_time_ms"]<expected_intent["outcome_due_ms"])):
        raise ValueError("TEST_ESCROW_ORIGINAL_INTENT_NOT_IN_SCOPE")
    target=1 if stage=="decision" else 2
    if (type(expected_intent.get("expected_previous_witness_sequence")) is not int
            or type(expected_intent.get("next_ledger_sequence")) is not int
            or expected_intent["expected_previous_witness_sequence"]!=target-1
            or expected_intent["next_ledger_sequence"]!=target):
        raise ValueError("TEST_ESCROW_EXPECTED_FRONTIER_NOT_INTEGER_OR_WRONG")
    for key in ("source_quote_receipt", "test_source_packet_sha256",
                "original_native_payload_sha256",
                "original_native_evidence_message_sha256",
                "original_detached_signature_sha256",
                "expected_predecessor_chain_digest"):
        value=expected_intent.get(key)
        if (not isinstance(value,str) or len(value)!=64
                or any(c not in "0123456789abcdef" for c in value)):
            raise ValueError("TEST_ESCROW_EXPECTED_SOURCE_DIGEST_INVALID")
    if (expected_intent["expected_predecessor_chain_digest"]=="0"*64
            )!=(stage=="decision"):
        raise ValueError("TEST_ESCROW_EXPECTED_PREDECESSOR_CHANGED")
    if claimed_ack.get("role") != "escrow_"+stage:
        raise ValueError("TEST_ESCROW_ACK_ROLE_INVALID")
    if claimed_ack.get("host") != OWNERS[stage] or claimed_ack.get("test_only") is not True:
        raise ValueError("TEST_ESCROW_HOST_OR_SCOPE_INVALID")
    path = claimed_ack.get("durable_receipt_path")
    if not isinstance(path,str):
        raise ValueError("TEST_ESCROW_REMOTE_PATH_INVALID")
    p=PurePosixPath(path)
    if (not p.is_absolute() or ".." in p.parts or
            p.parent.name!="hepta-native-escrow-phase4n-20261012" or
            not p.name.startswith("receipt-"+stage+"-") or
            not p.name.endswith(".json")):
        raise ValueError("TEST_ESCROW_REMOTE_PATH_NOT_PINNED")
    if independent_origin_file is None:
        return {
            "state":"HOLD_REMOTE_ORIGIN_BYTES_NOT_OBSERVED",
            "production_authority":False,
            "test_only":True,
            "signed_original_host_cryptographically_attested":False,
        }
    raw_hex=claimed_ack.get("receipt_hex")
    if (not isinstance(raw_hex,str) or len(raw_hex)>32768
            or len(raw_hex)<64 or len(raw_hex)%2
            or not isinstance(independent_origin_file,bytes)
            or len(independent_origin_file)>16384):
        raise ValueError("TEST_ESCROW_REPLAY_UNBOUNDED_BYTES")
    original = bytes.fromhex(raw_hex)
    if (len(original)>16384 or len(original)<32 or
            original != independent_origin_file or
            hashlib.sha256(original).hexdigest()!=claimed_ack.get("receipt_sha256")):
        raise ValueError("TEST_ESCROW_RETURNED_AND_REMOTE_ORIGIN_BYTES_DIFFER")
    receipt=json.loads(original)
    if canonical(receipt)+b"\n" != original:
        raise ValueError("TEST_ESCROW_RECEIPT_NOT_CANONICAL")
    frozen = canonical(expected_intent)
    if (receipt.get("schema") != RECEIPT_SCHEMA or
            receipt.get("body") != expected_intent or
            receipt.get("body_sha256") != hashlib.sha256(frozen).hexdigest() or
            receipt.get("signing_host") != OWNERS[stage] or
            receipt.get("escrow_test_role") != "escrow_"+stage or
            receipt.get("public_key_hex") != pinned_test_public_hex or
            receipt.get("message_domain_hex") != DOMAIN.hex() or
            receipt.get("test_only") is not True or
            receipt.get("production_root_authority") is not False or
            receipt.get("durably_persisted_before_reply") is not True):
        raise ValueError("TEST_ESCROW_RECEIPT_WRONG_SCOPE")
    when=receipt.get("intent_held_at_ms")
    if (type(when) is not int or when<expected_intent.get("source_time_ms",0)
            or not 0<=when<2**64 or
            (stage=="decision" and
             when>=expected_intent.get("outcome_due_ms",0))
            or (stage=="outcome" and when<expected_intent["outcome_due_ms"])):
        raise ValueError("TEST_ESCROW_RECEIPT_CLOCK_INVALID")
    try:
        pub=bytes.fromhex(pinned_test_public_hex)
        sig=bytes.fromhex(receipt["signature_hex"])
        if len(pub)!=32 or len(sig)!=64:
            raise ValueError("TEST_ESCROW_SIGNATURE_LENGTH")
        Ed25519PublicKey.from_public_bytes(pub).verify(
            sig,DOMAIN+frozen+when.to_bytes(8,"big"))
    except (ValueError,InvalidSignature) as exc:
        raise ValueError("TEST_ESCROW_INDEPENDENT_ED25519_INVALID") from exc
    if (p.name != "receipt-"+stage+"-"+receipt.get("body_sha256","")+".json"):
        raise ValueError("TEST_ESCROW_REMOTE_NAME_DOES_NOT_BIND_INTENT")
    return {
        "state":"TEST_REMOTE_BYTES_MATCH_ACROSS_TWO_READS",
        "receipt_sha256":hashlib.sha256(original).hexdigest(),
        "exact_remote_file_bytes_matched":True,
        "test_only":True,
        "independent_root_enrollment":False,
        "remote_fsync_survived_power_loss":False,
        "signed_original_host_cryptographically_attested":False,
        "production_authority":False,
    }
