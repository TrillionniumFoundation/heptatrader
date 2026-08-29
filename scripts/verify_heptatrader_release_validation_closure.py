#!/usr/bin/env python3
"""Independently reconstruct a generic release-validation closure."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
import sys
from typing import Any, Optional


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_heptatrader_release_validation_closure as builder  # noqa: E402


class ReleaseValidationVerificationError(RuntimeError):
    """A closure failed canonical, freshness, or causal reconstruction."""


def _fail(message: str) -> None:
    raise ReleaseValidationVerificationError(message)


def _binding_path(value: Any, label: str) -> Path:
    if not isinstance(value, dict) or set(value) != {
            "path", "sha256", "size", "mode"}:
        _fail(f"{label} binding fields are invalid")
    path = value["path"]
    if not isinstance(path, str) or not Path(path).is_absolute():
        _fail(f"{label} binding path is not absolute")
    if (not isinstance(value["sha256"], str) or
            builder.HEX64.fullmatch(value["sha256"]) is None or
            type(value["size"]) is not int or value["size"] < 0 or
            not isinstance(value["mode"], str)):
        _fail(f"{label} binding metadata is invalid")
    return Path(path)


def _capture_closure(path: Path) -> builder.CapturedFile:
    try:
        captured = builder._capture_file(
            path, "release-validation closure",
            limit=builder.MAX_CLOSURE_BYTES)
    except builder.ReleaseValidationError as error:
        raise ReleaseValidationVerificationError(str(error)) from error
    if captured.snapshot.mode != "0600":
        _fail("release-validation closure mode must remain 0600")
    return captured


def _structure(value: Any) -> dict[str, Any]:
    fields = {
        "schema", "version", "project_id", "round", "release_version",
        "evaluated_at", "expires_at", "decision", "passed",
        "candidate_scope", "local_evidence", "retention_evidence",
        "safety_boundaries",
    }
    if not isinstance(value, dict) or set(value) != fields:
        _fail("release-validation closure fields do not exactly match schema")
    if (value["schema"] != builder.SCHEMA or
            type(value["version"]) is not int or
            value["version"] != builder.VERSION or
            value["project_id"] != builder.PROJECT_ID):
        _fail("unsupported release-validation closure schema")
    try:
        builder._release_identity(value["round"], value["release_version"])
        builder._normalized_time(value["evaluated_at"], "evaluated_at")
        builder._normalized_time(value["expires_at"], "expires_at")
    except builder.ReleaseValidationError as error:
        raise ReleaseValidationVerificationError(str(error)) from error
    if (value["decision"] != builder.DECISION or
            value["passed"] is not True or
            value["candidate_scope"] != builder.CANDIDATE_SCOPE or
            value["safety_boundaries"] != builder.SAFETY_BOUNDARIES):
        _fail("release-validation decision or non-authority boundary drift")
    local = value["local_evidence"]
    if (not isinstance(local, dict) or
            local.get("profile") != builder.PROFILE or
            local.get("round") != value["round"] or
            local.get("release_version") != value["release_version"] or
            local.get("safety_boundaries") != builder.SAFETY_BOUNDARIES or
            not isinstance(local.get("critical_files"), list)):
        _fail("release-validation local evidence identity drift")
    retention = value["retention_evidence"]
    if not isinstance(retention, dict) or set(retention) != {
            "inputs", "evidence_root", "verification"}:
        _fail("release-validation retention evidence fields are invalid")
    if (not isinstance(retention["evidence_root"], str) or
            not Path(retention["evidence_root"]).is_absolute() or
            not isinstance(retention["inputs"], dict) or
            set(retention["inputs"]) != {
                "evidence_set_manifest", "index", "receipt", "request",
                "retention_policy", "trust_policy"}):
        _fail("release-validation retention input closure is invalid")
    for role, binding in retention["inputs"].items():
        _binding_path(binding, f"retention input {role}")
    verification = retention["verification"]
    if (not isinstance(verification, dict) or
            verification.get("trust_scope") != "system-production" or
            verification.get("signature_status") != "verified" or
            verification.get("retention_status") !=
            "current-policy-satisfied" or
            verification.get("production_contract_verified") is not True or
            verification.get("profile") != builder.PROFILE):
        _fail("release-validation production retention contract drift")
    return value


def verify(
        path: Path, *, verification_time: datetime | None = None,
        _allow_test_time: bool = False) -> dict[str, Any]:
    """Reopen, time-check, and fully reconstruct one closure.

    ``verification_time`` is an internal test seam only.  Production callers
    cannot select the clock used for freshness decisions.
    """
    if verification_time is not None and not _allow_test_time:
        _fail("caller-supplied verification_time is forbidden")
    now = (verification_time or datetime.now(timezone.utc)).astimezone(
        timezone.utc)
    first = _capture_closure(path)
    try:
        value = _structure(builder._canonical_document(
            first, "release-validation closure"))
    except builder.ReleaseValidationError as error:
        raise ReleaseValidationVerificationError(str(error)) from error
    evaluated = builder._parse_time(value["evaluated_at"], "evaluated_at")
    expires = builder._parse_time(value["expires_at"], "expires_at")
    if evaluated > now + timedelta(seconds=builder.MAX_CLOCK_SKEW_SECONDS):
        _fail("release-validation closure is future-dated")
    if expires <= evaluated or now > expires:
        _fail("release-validation closure is expired")
    local_fresh_until = value["local_evidence"].get(
        "verification", {}).get("fresh_until")
    if local_fresh_until != value["expires_at"]:
        _fail("release-validation freshness lineage drift")

    retention = value["retention_evidence"]
    evidence_root = Path(retention["evidence_root"])
    manifest_record = next(
        (record for record in value["local_evidence"]["critical_files"]
         if isinstance(record, dict) and
         record.get("role") == "release-input-manifest"),
        None)
    if (not isinstance(manifest_record, dict) or
            not isinstance(manifest_record.get("path"), str)):
        _fail("release input-manifest binding is absent")
    try:
        relative = builder._relative(
            manifest_record["path"], "release input-manifest path")
    except builder.ReleaseValidationError as error:
        raise ReleaseValidationVerificationError(str(error)) from error
    input_manifest = evidence_root.joinpath(
        *PurePosixPath(relative).parts)
    inputs = retention["inputs"]
    receipt_inputs = builder.ReceiptInputs(
        receipt=_binding_path(inputs["receipt"], "receipt"),
        request=_binding_path(inputs["request"], "request"),
        trust_policy=_binding_path(inputs["trust_policy"], "trust policy"),
        index=_binding_path(inputs["index"], "index"),
        evidence_set_manifest=_binding_path(
            inputs["evidence_set_manifest"], "evidence-set manifest"),
        retention_policy=_binding_path(
            inputs["retention_policy"], "retention policy"),
    )
    try:
        rebuilt = builder.build_closure(
            input_manifest, evidence_root, receipt_inputs,
            evaluated_at=evaluated)
    except builder.ReleaseValidationError as error:
        raise ReleaseValidationVerificationError(
            "release-validation causal reconstruction failed") from error
    if rebuilt != value:
        _fail("release-validation closure differs from causal reconstruction")
    second = _capture_closure(path)
    if second.path != first.path or second.snapshot != first.snapshot:
        _fail("release-validation closure changed across verification")
    return {
        "schema": builder.VERIFICATION_SCHEMA,
        "version": 1,
        "status": "verified",
        "decision": builder.DECISION,
        "passed": True,
        "candidate_scope": builder.CANDIDATE_SCOPE,
        "round": value["round"],
        "release_version": value["release_version"],
        "expires_at": value["expires_at"],
        "critical_file_count": len(
            value["local_evidence"]["critical_files"]),
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access_authorized": False,
        "order_placement_authorized": False,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="verify a generic release-validation closure")
    parser.add_argument("--closure", type=Path, required=True)
    arguments = parser.parse_args(argv)
    report = verify(arguments.closure)
    print(
        "heptatrader-release-validation-verification: "
        f"decision={report['decision']} round={report['round']} "
        "candidate_only=true authority=false")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReleaseValidationVerificationError, OSError) as error:
        print(
            f"heptatrader-release-validation-verification: FAIL {error}",
            file=sys.stderr)
        raise SystemExit(78)
