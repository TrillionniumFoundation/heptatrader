#!/usr/bin/env python3
"""Verify an externally signed HeptaTrader evidence-ingestion receipt."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

import build_heptatrader_evidence_ingestion_request as request_builder


LEGACY_RECEIPT_SCHEMA = "hepta.evidence-ingestion-receipt.v1"
RECEIPT_SCHEMA = "hepta.evidence-ingestion-receipt.v2"
TRUST_SCHEMA = "hepta.evidence-receipt-trust-policy.v1"
VERIFICATION_SCHEMA = "hepta.evidence-ingestion-receipt-verification.v2"
TEST_VERIFICATION_SCHEMA = (
    "hepta.test-only-evidence-ingestion-receipt-verification.v2")
SIGNATURE_DOMAIN = "HEPTA-EVIDENCE-INGESTION-RECEIPT-V2"
OPENSSL = Path("/usr/bin/openssl")
RELEASE_CAUSAL_OPENSSL_CONF = Path(
    "/etc/heptatrader/release-causal-openssl.cnf")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")
MAX_PUBLIC_KEY_BYTES = 64 * 1024
ED25519_SPKI_DER_PREFIX = bytes.fromhex(
    "302a300506032b6570032100")
ED25519_SPKI_DER_BYTES = len(ED25519_SPKI_DER_PREFIX) + 32
MAX_LEGAL_HOLD_ATTESTATION_AGE_SECONDS = 7 * 24 * 60 * 60
SYSTEM_TRUST_POLICY = Path(
    "/etc/heptatrader/heptatrader-evidence-receipt-trust-v1.json")


class IngestionReceiptError(RuntimeError):
    pass


def _timestamp(value: Any, label: str) -> datetime:
    try:
        return request_builder.require_rfc3339(value, label)
    except request_builder.IngestionRequestError as error:
        raise IngestionReceiptError(str(error)) from error


def _safe_token(value: Any, label: str) -> str:
    if not isinstance(value, str) or SAFE_TOKEN.fullmatch(value) is None:
        raise IngestionReceiptError(f"{label} is not a safe ASCII token")
    return value


def _capture_verification_time(
        supplied: datetime | None, *,
        require_system_trust: bool) -> datetime:
    if supplied is not None and require_system_trust:
        raise IngestionReceiptError(
            "system-trust verification time cannot be supplied by the caller")
    captured = datetime.now(timezone.utc) if supplied is None else supplied
    if (not isinstance(captured, datetime) or captured.tzinfo is None or
            captured.utcoffset() is None):
        raise IngestionReceiptError(
            "verification_time must be a timezone-aware datetime")
    return captured.astimezone(timezone.utc)


def _strict_file(path: Path, label: str, limit: int) -> tuple[bytes, Any]:
    try:
        _, data = request_builder.stable_file(path, limit)
        return data, request_builder.strict_json(data, label)
    except request_builder.IngestionRequestError as error:
        raise IngestionReceiptError(str(error)) from error


def _run_openssl(arguments: list[str], *, input_bytes: bytes | None = None) -> bytes:
    if not OPENSSL.is_file() or OPENSSL.is_symlink():
        raise IngestionReceiptError("reviewed OpenSSL executable is unavailable")
    metadata = OPENSSL.stat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise IngestionReceiptError("OpenSSL executable has unsafe ownership or mode")
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        # Never allow a caller-controlled OpenSSL configuration/provider path.
        # The release-causal verifier supplies the exact staged file inside its
        # private root; ordinary verification uses the kernel null device and
        # the built-in default provider only.
        "OPENSSL_CONF": (
            str(RELEASE_CAUSAL_OPENSSL_CONF)
            if os.environ.get("HEPTA_RELEASE_CAUSAL_ROOTFS") == "1"
            else "/dev/null"),
        "OPENSSL_MODULES": "/nonexistent-hepta-openssl-provider-directory",
    }
    result = subprocess.run(
        [str(OPENSSL), *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        close_fds=True,
        check=False,
    )
    if result.returncode != 0:
        raise IngestionReceiptError("OpenSSL verification operation failed")
    return result.stdout


def _ed25519_spki_der(public_key: bytes) -> bytes:
    der = _run_openssl(
        ["pkey", "-pubin", "-inform", "PEM", "-outform", "DER"],
        input_bytes=public_key,
    )
    if (len(der) != ED25519_SPKI_DER_BYTES or
            not der.startswith(ED25519_SPKI_DER_PREFIX)):
        raise IngestionReceiptError(
            "public key is not a canonical RFC 8410 Ed25519 SPKI")
    return der


def public_key_spki_sha256(public_key: bytes) -> str:
    der = _ed25519_spki_der(public_key)
    return hashlib.sha256(der).hexdigest()


def _verify_ed25519(
        public_key: bytes, signed_payload: bytes, signature: bytes) -> None:
    _ed25519_spki_der(public_key)
    if len(signature) != 64:
        raise IngestionReceiptError("Ed25519 signature must be 64 bytes")
    with tempfile.TemporaryDirectory(
            prefix="hepta-evidence-receipt-verify-") as temporary:
        root = Path(temporary)
        key_path = root / "public.pem"
        payload_path = root / "statement.bin"
        signature_path = root / "signature.bin"
        for path, data in (
                (key_path, public_key),
                (payload_path, signed_payload),
                (signature_path, signature)):
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                offset = 0
                while offset < len(data):
                    offset += os.write(descriptor, data[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        _run_openssl([
            "pkeyutl", "-verify", "-pubin", "-inkey", str(key_path),
            "-rawin", "-in", str(payload_path), "-sigfile", str(signature_path),
        ])


def _load_trust_policy(
        path: Path, *,
        require_system_ownership: bool = True,
        include_bytes: bool = False,
) -> (
        tuple[dict[str, Any], dict[str, dict[str, Any]]] |
        tuple[bytes, dict[str, Any], dict[str, dict[str, Any]]]):
    try:
        _, trust_bytes = request_builder.stable_file(
            path, request_builder.MAX_JSON_BYTES,
            allowed_owner_uids=frozenset({0, os.geteuid()}))
        trust = request_builder.strict_json(
            trust_bytes, "evidence receipt trust policy")
    except request_builder.IngestionRequestError as error:
        raise IngestionReceiptError(str(error)) from error
    required = {
        "schema", "version", "project_id", "signature_domain",
        "production_receipt_status",
        "allowed_retention_policy_sha256",
        "legal_hold_attestation_max_age_seconds", "keys",
    }
    if not isinstance(trust, dict) or set(trust) != required:
        raise IngestionReceiptError("trust policy fields do not match schema")
    if (trust["schema"] != TRUST_SCHEMA or
            type(trust["version"]) is not int or trust["version"] != 1 or
            trust["project_id"] != request_builder.PROJECT_ID or
            trust["signature_domain"] != SIGNATURE_DOMAIN or
            not isinstance(trust["production_receipt_status"], str) or
            trust["production_receipt_status"] not in {
                "pending-external", "configured-external"} or
            not isinstance(trust["keys"], list)):
        raise IngestionReceiptError("unsupported receipt trust policy")
    allowed_policies = trust["allowed_retention_policy_sha256"]
    if (not isinstance(allowed_policies, list) or not allowed_policies or
            any(not isinstance(digest, str) or
                HEX64.fullmatch(digest) is None
                for digest in allowed_policies) or
            allowed_policies != sorted(allowed_policies) or
            len(set(allowed_policies)) != len(allowed_policies)):
        raise IngestionReceiptError(
            "invalid allowed retention policy digest list")
    legal_hold_max_age = trust[
        "legal_hold_attestation_max_age_seconds"]
    if (not isinstance(legal_hold_max_age, int) or
            isinstance(legal_hold_max_age, bool) or
            legal_hold_max_age <= 0 or
            legal_hold_max_age >
            MAX_LEGAL_HOLD_ATTESTATION_AGE_SECONDS):
        raise IngestionReceiptError(
            "invalid legal-hold attestation maximum age")
    keys: dict[str, dict[str, Any]] = {}
    for record in trust["keys"]:
        if not isinstance(record, dict) or set(record) != {
                "key_id", "algorithm", "public_key_path",
                "public_key_spki_sha256", "valid_from", "valid_until",
                "revoked", "allowed_store_ids",
                "allowed_retention_policy_sha256"}:
            raise IngestionReceiptError("invalid trust key record")
        key_id = record["key_id"]
        if (not isinstance(key_id, str) or
                not key_id.startswith("sha256/") or
                HEX64.fullmatch(key_id[7:]) is None or
                record["algorithm"] != "ed25519" or
                record["public_key_spki_sha256"] != key_id[7:] or
                not isinstance(record["revoked"], bool)):
            raise IngestionReceiptError("invalid trust key identity")
        relative = record["public_key_path"]
        relative_path = Path(relative) if isinstance(relative, str) else Path()
        if (not isinstance(relative, str) or not relative or
                not relative.isascii() or relative_path.is_absolute() or
                ".." in relative_path.parts or
                relative_path.as_posix() != relative):
            raise IngestionReceiptError("unsafe public key path")
        valid_from = _timestamp(record["valid_from"], "key valid_from")
        valid_until = record["valid_until"]
        if valid_until is not None:
            valid_until = _timestamp(valid_until, "key valid_until")
            if valid_until <= valid_from:
                raise IngestionReceiptError("invalid trust key validity interval")
        stores = record["allowed_store_ids"]
        if (not isinstance(stores, list) or not stores or
                any(not isinstance(store, str) or
                    SAFE_TOKEN.fullmatch(store) is None for store in stores) or
                stores != sorted(set(stores))):
            raise IngestionReceiptError("invalid allowed store list")
        key_policies = record["allowed_retention_policy_sha256"]
        if (not isinstance(key_policies, list) or not key_policies or
                any(not isinstance(digest, str) or
                    HEX64.fullmatch(digest) is None
                    for digest in key_policies) or
                key_policies != sorted(key_policies) or
                len(set(key_policies)) != len(key_policies) or
                not set(key_policies).issubset(allowed_policies)):
            raise IngestionReceiptError(
                "invalid key retention policy digest list")
        if key_id in keys:
            raise IngestionReceiptError("duplicate trust key")
        keys[key_id] = record
    if trust["production_receipt_status"] == "configured-external" and not keys:
        raise IngestionReceiptError(
            "configured trust policy must contain a public key")
    if trust["production_receipt_status"] == "pending-external" and keys:
        raise IngestionReceiptError(
            "pending trust policy cannot contain active production keys")
    if (require_system_ownership and
            trust["production_receipt_status"] == "configured-external"):
        if Path(os.path.abspath(path)) != SYSTEM_TRUST_POLICY:
            raise IngestionReceiptError(
                "configured production trust must use the fixed system path")
        try:
            _, confirmed = request_builder.stable_file(
                path, request_builder.MAX_JSON_BYTES,
                allowed_owner_uids=frozenset({0}),
                trusted_parent_owner_uid=0)
        except request_builder.IngestionRequestError as error:
            raise IngestionReceiptError(
                "configured production trust must be rooted in a "
                "root-owned, non-writable path") from error
        if confirmed != trust_bytes:
            raise IngestionReceiptError(
                "production trust changed during ownership validation")
    if include_bytes:
        return trust_bytes, trust, keys
    return trust, keys


def _confirm_trust_policy_unchanged(
        path: Path, expected: bytes, *,
        require_system_ownership: bool) -> None:
    try:
        _, confirmed = request_builder.stable_file(
            path, request_builder.MAX_JSON_BYTES,
            allowed_owner_uids=(
                frozenset({0}) if require_system_ownership
                else frozenset({os.geteuid()})),
            trusted_parent_owner_uid=(
                0 if require_system_ownership else None))
    except request_builder.IngestionRequestError as error:
        raise IngestionReceiptError(
            "receipt trust policy became unsafe during verification") from error
    if confirmed != expected:
        raise IngestionReceiptError(
            "receipt trust policy changed during verification")


def _validate_request(
        request_path: Path, index_path: Path, evidence_root: Path,
        policy_path: Path,
        evidence_set_manifest_path: Path | None,
) -> tuple[bytes, dict[str, Any]]:
    request_bytes, request = _strict_file(
        request_path, "ingestion request", request_builder.MAX_JSON_BYTES)
    base_fields = {
        "schema", "version", "project_id", "created_at", "request_nonce",
        "index", "object_key_template", "object_count", "objects_sha256",
        "upload_status", "retention_anchor_status", "source_files_deleted",
        "source_removal_authorized", "paper_authorized", "live_authorized",
        "objects",
    }
    if not isinstance(request, dict):
        raise IngestionReceiptError("ingestion request fields do not match schema")
    if (request.get("schema") == request_builder.REQUEST_SCHEMA and
            type(request.get("version")) is int and
            request["version"] == 2):
        required = base_fields | {"evidence_set"}
        if evidence_set_manifest_path is None:
            raise IngestionReceiptError(
                "v2 ingestion request requires an evidence-set manifest")
    elif (request.get("schema") == request_builder.LEGACY_REQUEST_SCHEMA and
            type(request.get("version")) is int and
            request["version"] == 1):
        required = base_fields
        if evidence_set_manifest_path is not None:
            raise IngestionReceiptError(
                "legacy ingestion request cannot bind an evidence-set manifest")
    else:
        raise IngestionReceiptError("unsupported ingestion request schema")
    if set(request) != required:
        raise IngestionReceiptError("ingestion request fields do not match schema")
    try:
        expected = request_builder.build_request(
            index_path, evidence_root, policy_path,
            project_id=request["project_id"],
            request_nonce=request["request_nonce"],
            created_at=request["created_at"],
            evidence_set_manifest_path=evidence_set_manifest_path,
        )
    except (request_builder.IngestionRequestError, KeyError) as error:
        raise IngestionReceiptError("ingestion request is invalid") from error
    if (request_builder.canonical_json(request) !=
            request_builder.canonical_json(expected)):
        raise IngestionReceiptError("ingestion request exact closure drift")
    return request_bytes, request


def _confirm_request_and_evidence_unchanged(
        request_path: Path, index_path: Path, evidence_root: Path,
        policy_path: Path, evidence_set_manifest_path: Path | None,
        expected_request_bytes: bytes, expected_request: dict[str, Any],
) -> None:
    try:
        confirmed_request_bytes, confirmed_request = _validate_request(
            request_path, index_path, evidence_root, policy_path,
            evidence_set_manifest_path)
    except IngestionReceiptError as error:
        raise IngestionReceiptError(
            "ingestion request or evidence changed during verification"
        ) from error
    if (confirmed_request_bytes != expected_request_bytes or
            confirmed_request != expected_request):
        raise IngestionReceiptError(
            "ingestion request or evidence changed during verification")


def _validated_receipt_retention(
        actual: Any, required: dict[str, Any],
        anchor: datetime, verified_at: datetime,
        signed_at: datetime) -> dict[str, Any]:
    fields = {
        "kind", "days", "anchor_at", "retain_until",
        "object_lock_mode", "legal_hold",
    }
    if not isinstance(actual, dict) or set(actual) != fields:
        raise IngestionReceiptError("receipt retention fields are invalid")
    if _timestamp(actual["anchor_at"], "retention anchor_at") != anchor:
        raise IngestionReceiptError("retention anchor does not equal ingestion time")
    if actual["kind"] != required["kind"] or actual["days"] != required["days"]:
        raise IngestionReceiptError("receipt weakens required retention")
    if required["kind"] == "indefinite":
        if (actual["retain_until"] is not None or
                actual["object_lock_mode"] != "legal-hold" or
                actual["legal_hold"] is not True):
            raise IngestionReceiptError(
                "indefinite evidence requires an active legal hold")
        return {
            "kind": "indefinite",
            "attested_at": verified_at.isoformat(),
            "retain_until": None,
        }
    days = required["days"]
    if (not isinstance(days, int) or isinstance(days, bool) or days <= 0 or
            not isinstance(actual["days"], int) or
            isinstance(actual["days"], bool) or actual["days"] <= 0 or
            actual["object_lock_mode"] != "compliance" or
            actual["legal_hold"] is not False):
        raise IngestionReceiptError("finite retention enforcement is invalid")
    retain_until = _timestamp(actual["retain_until"], "retain_until")
    try:
        required_interval = timedelta(days=days)
    except OverflowError as error:
        raise IngestionReceiptError(
            "required retention interval is out of range") from error
    if retain_until - max(anchor, verified_at, signed_at) < required_interval:
        raise IngestionReceiptError("remote retention interval is too short")
    return {
        "kind": "finite-days",
        "attested_at": verified_at.isoformat(),
        "retain_until": retain_until.isoformat(),
    }


def _current_retention_status(
        validated: dict[str, Any], verification_time: datetime,
        legal_hold_attestation_max_age_seconds: int) -> dict[str, Any]:
    attested_at = _timestamp(
        validated["attested_at"], "retention attested_at")
    attestation_age = verification_time - attested_at
    if attestation_age < timedelta(0):
        raise IngestionReceiptError(
            "retention attestation is in the future at verification_time")
    if validated["kind"] == "indefinite":
        if attestation_age > timedelta(
                seconds=legal_hold_attestation_max_age_seconds):
            raise IngestionReceiptError(
                "legal-hold attestation is stale at verification_time")
        return {
            **validated,
            "status": "fresh-signed-active-attestation",
            "attestation_age_seconds": attestation_age.total_seconds(),
            "attestation_max_age_seconds":
                legal_hold_attestation_max_age_seconds,
        }
    retain_until = _timestamp(
        validated["retain_until"], "validated retain_until")
    if retain_until <= verification_time:
        raise IngestionReceiptError(
            "remote finite retention is expired at verification_time")
    return {
        **validated,
        "status": "active-at-verification-time",
        "attestation_age_seconds": attestation_age.total_seconds(),
        "attestation_max_age_seconds": None,
    }


def verify_receipt(
        receipt_path: Path,
        request_path: Path,
        trust_policy_path: Path,
        index_path: Path,
        evidence_root: Path,
        policy_path: Path,
        evidence_set_manifest_path: Path | None = None,
        *,
        require_system_trust: bool = True,
        verification_time: datetime | None = None,
) -> dict[str, Any]:
    if verification_time is not None and require_system_trust:
        raise IngestionReceiptError(
            "system-trust verification time cannot be supplied by the caller")
    supplied_verification_time = verification_time
    receipt_bytes, receipt = _strict_file(
        receipt_path, "ingestion receipt", request_builder.MAX_JSON_BYTES)
    del receipt_bytes
    if not isinstance(receipt, dict) or set(receipt) != {
            "schema", "version", "statement", "statement_sha256", "signature"}:
        raise IngestionReceiptError("receipt fields do not match schema")
    request_bytes, request = _validate_request(
        request_path, index_path, evidence_root, policy_path,
        evidence_set_manifest_path)
    request_is_v2 = request["schema"] == request_builder.REQUEST_SCHEMA
    expected_receipt_schema = (
        RECEIPT_SCHEMA if request_is_v2 else LEGACY_RECEIPT_SCHEMA)
    expected_receipt_version = 2 if request_is_v2 else 1
    if (receipt["schema"] != expected_receipt_schema or
            type(receipt["version"]) is not int or
            receipt["version"] != expected_receipt_version):
        raise IngestionReceiptError("unsupported ingestion receipt")
    loaded_trust = _load_trust_policy(
        trust_policy_path,
        require_system_ownership=require_system_trust,
        include_bytes=True)
    trust_bytes, trust, keys = loaded_trust
    if trust["production_receipt_status"] != "configured-external":
        raise IngestionReceiptError(
            "production receipt trust remains pending external configuration")
    if require_system_trust and not request_is_v2:
        raise IngestionReceiptError(
            "production receipt requires a manifest-defined v2 evidence set")
    if request["index"]["policy_sha256"] not in \
            trust["allowed_retention_policy_sha256"]:
        raise IngestionReceiptError(
            "retention policy digest is not trusted")

    statement = receipt["statement"]
    fields = {
        "project_id", "request_sha256", "request_nonce", "index_sha256",
        "policy_sha256", "records_sha256", "store_id", "provider",
        "receipt_serial", "ingested_at", "verified_at", "signed_at",
        "verification_method", "source_files_deleted",
        "source_removal_authorized", "paper_authorized", "live_authorized",
        "objects",
    }
    if request_is_v2:
        fields.add("evidence_set")
    if not isinstance(statement, dict) or set(statement) != fields:
        raise IngestionReceiptError("receipt statement fields do not match schema")
    if (statement["project_id"] != request_builder.PROJECT_ID or
            statement["request_sha256"] !=
            hashlib.sha256(request_bytes).hexdigest() or
            statement["request_nonce"] != request["request_nonce"] or
            statement["index_sha256"] != request["index"]["sha256"] or
            statement["policy_sha256"] != request["index"]["policy_sha256"] or
            statement["records_sha256"] != request["index"]["records_sha256"] or
            statement["verification_method"] !=
            "full-object-readback-sha256" or
            statement["source_files_deleted"] is not False or
            statement["source_removal_authorized"] is not False or
            statement["paper_authorized"] is not False or
            statement["live_authorized"] is not False):
        raise IngestionReceiptError("receipt safety or request binding drift")
    if (request_is_v2 and
            statement["evidence_set"] != request["evidence_set"]):
        raise IngestionReceiptError(
            "receipt evidence-set binding drift")
    store_id = _safe_token(statement["store_id"], "store_id")
    _safe_token(statement["provider"], "provider")
    _safe_token(statement["receipt_serial"], "receipt_serial")
    ingested_at = _timestamp(statement["ingested_at"], "ingested_at")
    verified_at = _timestamp(statement["verified_at"], "verified_at")
    signed_at = _timestamp(statement["signed_at"], "signed_at")
    try:
        request_created_at = request_builder.require_rfc3339(
            request["created_at"], "request created_at")
    except request_builder.IngestionRequestError as error:
        raise IngestionReceiptError(
            "ingestion request timestamp is invalid") from error
    if ingested_at < request_created_at:
        raise IngestionReceiptError(
            "receipt ingestion cannot predate its request")
    if not ingested_at <= verified_at <= signed_at:
        raise IngestionReceiptError("receipt timestamps are not monotonic")
    objects = statement["objects"]
    if not isinstance(objects, list) or len(objects) != len(request["objects"]):
        raise IngestionReceiptError("receipt object count drift")
    if [item.get("sha256") for item in objects if isinstance(item, dict)] != sorted(
            item["sha256"] for item in request["objects"]):
        raise IngestionReceiptError("receipt objects are not canonically ordered")
    validated_retentions = []
    for expected, actual in zip(request["objects"], objects):
        if not isinstance(actual, dict) or set(actual) != {
                "sha256", "size", "object_key", "version_id",
                "provider_checksum_sha256", "readback_sha256",
                "retention"}:
            raise IngestionReceiptError("invalid receipt object")
        if (actual["sha256"] != expected["sha256"] or
                not isinstance(actual["size"], int) or
                isinstance(actual["size"], bool) or actual["size"] < 0 or
                actual["size"] != expected["size"] or
                actual["object_key"] != expected["object_key"] or
                actual["provider_checksum_sha256"] != expected["sha256"] or
                actual["readback_sha256"] != expected["sha256"]):
            raise IngestionReceiptError("remote object exact closure drift")
        _safe_token(actual["version_id"], "object version_id")
        retention_result = _validated_receipt_retention(
            actual["retention"], expected["required_retention"],
            ingested_at, verified_at, signed_at)
        validated_retentions.append({
            "sha256": actual["sha256"],
            **retention_result,
        })

    statement_bytes = request_builder.canonical_json(statement)
    statement_sha256 = hashlib.sha256(statement_bytes).hexdigest()
    if receipt["statement_sha256"] != statement_sha256:
        raise IngestionReceiptError("receipt statement digest drift")
    signature = receipt["signature"]
    if not isinstance(signature, dict) or set(signature) != {
            "algorithm", "key_id", "value_base64"}:
        raise IngestionReceiptError("invalid receipt signature envelope")
    if signature["algorithm"] != "ed25519":
        raise IngestionReceiptError("unsupported receipt signature algorithm")
    key_id = signature["key_id"]
    if (not isinstance(key_id, str) or not key_id.startswith("sha256/") or
            HEX64.fullmatch(key_id[7:]) is None):
        raise IngestionReceiptError("receipt signature key_id is invalid")
    if key_id not in keys:
        raise IngestionReceiptError("receipt signing key is not trusted")
    key = keys[key_id]
    if key["revoked"]:
        raise IngestionReceiptError("receipt signing key is revoked")
    valid_from = _timestamp(key["valid_from"], "key valid_from")
    valid_until = (
        _timestamp(key["valid_until"], "key valid_until")
        if key["valid_until"] is not None else None)
    if signed_at < valid_from or (valid_until is not None and signed_at > valid_until):
        raise IngestionReceiptError("receipt was signed outside key validity")
    if store_id not in key["allowed_store_ids"]:
        raise IngestionReceiptError("signing key is not trusted for this store")
    if request["index"]["policy_sha256"] not in \
            key["allowed_retention_policy_sha256"]:
        raise IngestionReceiptError(
            "signing key is not trusted for this retention policy")
    public_key_path = (
        trust_policy_path.parent / key["public_key_path"])
    try:
        _, public_key = request_builder.stable_file(
            public_key_path, MAX_PUBLIC_KEY_BYTES,
            allowed_owner_uids=(
                frozenset({0}) if require_system_trust
                else frozenset({os.geteuid()})),
            trusted_parent_owner_uid=(
                0 if require_system_trust else None))
    except request_builder.IngestionRequestError as error:
        raise IngestionReceiptError("trusted public key is unsafe") from error
    if public_key_spki_sha256(public_key) != key_id[7:]:
        raise IngestionReceiptError("trusted public key digest mismatch")
    signature_base64 = signature["value_base64"]
    if not isinstance(signature_base64, str) or not signature_base64.isascii():
        raise IngestionReceiptError(
            "receipt signature is not canonical base64")
    try:
        signature_bytes = base64.b64decode(
            signature_base64, validate=True)
    except (TypeError, ValueError) as error:
        raise IngestionReceiptError("receipt signature is not canonical base64") from error
    if base64.b64encode(signature_bytes).decode("ascii") != signature_base64:
        raise IngestionReceiptError(
            "receipt signature is not canonical base64")
    signed_payload = (
        SIGNATURE_DOMAIN.encode("ascii") + b"\0" + statement_bytes)
    _verify_ed25519(public_key, signed_payload, signature_bytes)
    _confirm_request_and_evidence_unchanged(
        request_path, index_path, evidence_root, policy_path,
        evidence_set_manifest_path, request_bytes, request)
    _confirm_trust_policy_unchanged(
        trust_policy_path, trust_bytes,
        require_system_ownership=require_system_trust)
    verification_time = _capture_verification_time(
        supplied_verification_time,
        require_system_trust=require_system_trust)
    if verified_at > verification_time:
        raise IngestionReceiptError(
            "receipt verified_at is in the future")
    if signed_at > verification_time:
        raise IngestionReceiptError(
            "receipt signed_at is in the future")
    if (verification_time < valid_from or
            (valid_until is not None and verification_time > valid_until)):
        raise IngestionReceiptError(
            "receipt signing key is not valid at verification_time")
    retention_results = [
        {
            "sha256": validated["sha256"],
            **_current_retention_status(
                {key: value for key, value in validated.items()
                 if key != "sha256"},
                verification_time,
                trust["legal_hold_attestation_max_age_seconds"]),
        }
        for validated in validated_retentions
    ]
    _confirm_request_and_evidence_unchanged(
        request_path, index_path, evidence_root, policy_path,
        evidence_set_manifest_path, request_bytes, request)
    _confirm_trust_policy_unchanged(
        trust_policy_path, trust_bytes,
        require_system_ownership=require_system_trust)
    production_trust = require_system_trust
    return {
        "schema": (
            VERIFICATION_SCHEMA if production_trust
            else TEST_VERIFICATION_SCHEMA),
        "version": 2,
        "verification_time": verification_time.isoformat(),
        "production_trust": production_trust,
        "trust_scope": (
            "system-production" if production_trust else "test-local"),
        "signature_status": (
            "verified" if production_trust else "test-key-verified"),
        "retention_status": (
            "current-policy-satisfied" if production_trust
            else "test-evaluated-current-policy-satisfied"),
        "current_policy_satisfied_object_count": len(retention_results),
        "statement_sha256": receipt["statement_sha256"],
        "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        "index_sha256": request["index"]["sha256"],
        "evidence_set_manifest_sha256": (
            request["evidence_set"]["manifest_sha256"]
            if request_is_v2 else None),
        "trust_policy_sha256": hashlib.sha256(trust_bytes).hexdigest(),
        "source_files_deleted": False,
        "source_removal_authorized": False,
        "paper_authorized": False,
        "live_authorized": False,
        "evidence_set_bound": request_is_v2,
        "evidence_set_certified": production_trust and request_is_v2,
        "evidence_set": (
            request["evidence_set"] if request_is_v2 else None),
        "objects": retention_results,
        "receipt": receipt,
    }


def production_trust_path(repository_root: Path) -> Path:
    try:
        SYSTEM_TRUST_POLICY.lstat()
    except FileNotFoundError:
        return (
            repository_root /
            "policies/heptatrader-evidence-receipt-trust-v1.json")
    return SYSTEM_TRUST_POLICY


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument(
        "--evidence-set-manifest", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    resolve = lambda value: value if value.is_absolute() else root / value
    receipt = resolve(args.receipt)
    request = resolve(args.request)
    index = resolve(args.index)
    evidence_set_manifest = resolve(args.evidence_set_manifest)
    trust = production_trust_path(root)
    evidence_root = resolve(
        args.evidence_root or Path("runtime-logs")).resolve(strict=True)
    policy = root / "policies/heptatrader-evidence-retention-v1.json"
    report = verify_receipt(
        receipt, request, trust, index, evidence_root, policy,
        evidence_set_manifest,
        require_system_trust=True)
    print(
        f"PASS: signed external ingestion receipt "
        f"{report['statement_sha256']} has current retention policy "
        f"satisfied at "
        f"{report['verification_time']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IngestionReceiptError, OSError) as error:
        print(f"evidence-ingestion-receipt: {error}", file=os.sys.stderr)
        raise SystemExit(78)
