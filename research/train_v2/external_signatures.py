"""Verify independently issued Ed25519 *research* witnesses, never sign.

This is an adapter boundary for host-controlled identities, not canonical
Agentd enrollment. Caller-supplied pinned trust is NOT authoritative unless
the real product host protects its hash and current revocation generation.
No private-key read, owner API, broker API or model dispatch in this module.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from .contracts import ContractError, canonical, digest, is_digest
from .orchestrator import SOURCE_EVENT, OUTCOME_EVENT, verify_plan, verify_event

TRUST_SCHEMA = "heptatrader.train-v2-external-trust.v1"
WITNESS_SCHEMA = "heptatrader.train-v2-external-witness.v1"
DOMAIN = b"HEPTATRADER.TRAIN_V2.EXTERNAL_WITNESS.V1\x00"
ROLE_KEYS = ("registrar", "producer", "observer")
MAX_FRESH_MS = 5000
MAX_WITNESS_TTL_MS = 120000
MAX_TRUST_TTL_MS = 16 * 86400000


def _hex_key(key):
    if not isinstance(key, str) or len(key) != 64:
        raise ContractError("EXTERNAL_KEY_FORMAT")
    try:
        result = bytes.fromhex(key)
    except ValueError as exc:
        raise ContractError("EXTERNAL_KEY_FORMAT") from exc
    if len(result) != 32 or result == bytes(32):
        raise ContractError("EXTERNAL_KEY_INVALID")
    return result


def verify_trust(trust, expected_sha256, plan, *, min_generation=1,
                 now_ms=None):
    """Check only an externally pinned public trust snapshot.

    Never accept a trust SHA from a signed envelope or candidate as the pin.
    """
    verify_plan(plan)
    if not isinstance(trust, dict) or not is_digest(expected_sha256):
        raise ContractError("EXTERNAL_TRUST_PIN_MISSING")
    if not hmac.compare_digest(hashlib.sha256(canonical(trust) + b"\n").hexdigest(),
                               expected_sha256):
        raise ContractError("EXTERNAL_TRUST_HASH_CHANGED")
    required = {
        "schema", "period_sha256", "instrument", "policy_id",
        "trust_generation", "not_before_ms", "expires_at_ms",
        "roles", "revoked_role_keys", "issuer_kind",
        "canonical_agentd_owner_enrolled", "trade_authority",
    }
    if set(trust) != required or trust["schema"] != TRUST_SCHEMA:
        raise ContractError("EXTERNAL_TRUST_SCHEMA")
    if (trust.get("period_sha256") != plan["period_sha256"]
            or trust.get("instrument") != "EUR.USD"
            or trust.get("policy_id") != plan["behavior_policy_id"]
            or trust.get("issuer_kind") != "OPERATOR_PINNED_EXTERNAL_RESEARCH_ONLY"
            or trust.get("canonical_agentd_owner_enrolled") is not False
            or trust.get("trade_authority") is not False):
        raise ContractError("EXTERNAL_TRUST_SCOPE_NOT_CANONICAL")
    gen = trust.get("trust_generation")
    if (type(gen) is not int or type(min_generation) is not int
            or not 1 <= min_generation <= gen <= 2**31):
        raise ContractError("EXTERNAL_TRUST_GENERATION_ROLLBACK")
    keys = trust.get("roles")
    if not isinstance(keys, dict) or set(keys) != set(ROLE_KEYS):
        raise ContractError("EXTERNAL_TRUST_ROLES")
    raw = [_hex_key(keys[role]) for role in ROLE_KEYS]
    if len(set(raw)) != len(raw):
        raise ContractError("EXTERNAL_TRUST_KEYS_NOT_INDEPENDENT")
    revoked = trust.get("revoked_role_keys")
    if not isinstance(revoked, list) or len(revoked) > 32 or any(
            x not in ROLE_KEYS for x in revoked) or len(set(revoked)) != len(revoked):
        raise ContractError("EXTERNAL_TRUST_REVOCATION_LIST")
    at = int(time.time_ns() // 1_000_000) if now_ms is None else now_ms
    start, end = trust.get("not_before_ms"), trust.get("expires_at_ms")
    if (type(at) is not int or type(start) is not int or type(end) is not int
            or not 0 < start <= at < end <= start + MAX_TRUST_TTL_MS):
        raise ContractError("EXTERNAL_TRUST_EXPIRED_OR_FUTURE")
    return trust


def signed_bytes(witness):
    """Deterministic domain-separated canonical bytes for an external signer."""
    if not isinstance(witness, dict):
        raise ContractError("EXTERNAL_WITNESS_SHAPE")
    return DOMAIN + canonical({k: v for k, v in witness.items()
                               if k != "signature"})


def verify_witness(trust, trust_sha256, plan, witness, *, expected_role,
                   expected_kind, bound_id, binding_sha256, now_ms=None,
                   min_generation=1):
    """Validate one incoming signed record without contacting its issuer."""
    if expected_role not in ROLE_KEYS or expected_kind not in ("PLAN", "EVENT"):
        raise ContractError("EXTERNAL_ROLE_OR_KIND")
    trust = verify_trust(trust, trust_sha256, plan,
                         min_generation=min_generation, now_ms=now_ms)
    if expected_role in trust["revoked_role_keys"]:
        raise ContractError("EXTERNAL_ROLE_KEY_REVOKED")
    required = {
        "schema", "kind", "role", "plan_id", "bound_id", "trust_sha256",
        "nonce", "binding_sha256", "issued_at_ms", "expires_at_ms", "signature",
        "period_sha256", "policy_id", "authority_effect",
    }
    if not isinstance(witness, dict) or set(witness) != required:
        raise ContractError("EXTERNAL_WITNESS_SHAPE")
    if (witness["schema"] != WITNESS_SCHEMA
            or witness["role"] != expected_role
            or witness["kind"] != expected_kind
            or witness["plan_id"] != plan["plan_id"]
            or witness["bound_id"] != bound_id
            or witness["binding_sha256"] != binding_sha256
            or not is_digest(binding_sha256)
            or witness["trust_sha256"] != trust_sha256
            or witness["period_sha256"] != plan["period_sha256"]
            or witness["policy_id"] != plan["behavior_policy_id"]
            or witness["authority_effect"] != "NONE"
            or not is_digest(witness["nonce"])):
        raise ContractError("EXTERNAL_WITNESS_SCOPE_OR_REPLAY")
    now = int(time.time_ns() // 1_000_000) if now_ms is None else now_ms
    issued, expiry = witness["issued_at_ms"], witness["expires_at_ms"]
    if (type(now) is not int or type(issued) is not int or type(expiry) is not int
            or not 0 <= now - issued <= MAX_FRESH_MS
            or not issued < expiry <= issued + MAX_WITNESS_TTL_MS
            or not now < expiry or expiry > trust["expires_at_ms"]
            or issued < trust["not_before_ms"]):
        raise ContractError("EXTERNAL_WITNESS_EXPIRED_REPLAY_OR_FUTURE")
    try:
        signature = base64.b64decode(witness["signature"], validate=True)
    except (TypeError, ValueError) as exc:
        raise ContractError("EXTERNAL_SIGNATURE_ENCODING") from exc
    if len(signature) != 64:
        raise ContractError("EXTERNAL_SIGNATURE_LENGTH")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
        Ed25519PublicKey.from_public_bytes(_hex_key(trust["roles"][expected_role])).verify(
            signature, signed_bytes(witness))
    except (ValueError, InvalidSignature) as exc:
        raise ContractError("EXTERNAL_SIGNATURE_INVALID") from exc
    return {
        "schema": "heptatrader.train-v2-verified-external-research-witness.v1",
        "plan_id": plan["plan_id"],
        "bound_id": bound_id,
        "kind": expected_kind,
        "role": expected_role,
        "trust_generation": trust["trust_generation"],
        "witness_sha256": hashlib.sha256(canonical(witness) + b"\n").hexdigest(),
        "signature_verified": True,
        "host_owner_enrolled": False,
        "canonical_agentd_admitted": False,
        "can_issue_orders": False,
        "can_promote_model": False,
    }


def validate_plan_witness(trust, trust_sha256, plan, spec, source_root,
                          registry_root, witness, *, now_ms=None, min_generation=1):
    bind = digest({"plan": plan, "feature_spec": spec,
                   "original_source_root": str(source_root),
                   "registry_root": str(registry_root)})
    result = verify_witness(trust, trust_sha256, plan, witness,
                            expected_role="registrar", expected_kind="PLAN",
                            bound_id=plan["plan_id"], binding_sha256=bind,
                            now_ms=now_ms, min_generation=min_generation)
    if witness["issued_at_ms"] >= plan["first_decision_ms"] - 30000:
        raise ContractError("EXTERNAL_PLAN_NOT_SIGNED_AHEAD")
    return result


def validate_event_witness(trust, trust_sha256, plan, event, source_file,
                           registry_root, witness, *, now_ms=None,
                           min_generation=1, decision_at_ms=None):
    verify_event(event)
    if event["plan_id"] != plan["plan_id"]:
        raise ContractError("EXTERNAL_EVENT_CROSS_PLAN")
    role = {SOURCE_EVENT: "producer", OUTCOME_EVENT: "observer"}.get(event["kind"])
    if role is None:
        raise ContractError("EXTERNAL_CORRECTION_REQUIRES_CANONICAL_OWNER")
    bind = digest({"event": event, "original_source_file": str(source_file),
                   "registry_root": str(registry_root)})
    result = verify_witness(trust, trust_sha256, plan, witness,
                            expected_role=role, expected_kind="EVENT",
                            bound_id=event["event_id"], binding_sha256=bind,
                            now_ms=now_ms, min_generation=min_generation)
    if event["kind"] == SOURCE_EVENT:
        # A plan-wide last date is NOT the original decision deadline. When a
        # caller supplies the exact sample, bind its decision time; standalone
        # verifiers conservatively require signing by the event timestamp.
        deadline = event["at_ms"] if decision_at_ms is None else decision_at_ms
        if (type(deadline) is not int
                or not event["at_ms"] <= witness["issued_at_ms"] <= deadline
                or deadline > plan["last_decision_ms"]):
            raise ContractError("EXTERNAL_PRODUCER_SIGNED_TOO_LATE")
    if event["kind"] == OUTCOME_EVENT and event["at_ms"] > witness["issued_at_ms"]:
        raise ContractError("EXTERNAL_OBSERVER_PREDATED")
    return result
