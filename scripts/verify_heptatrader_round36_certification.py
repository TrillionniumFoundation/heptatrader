#!/usr/bin/env python3
"""Independently verify a Round36 final-certification document."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
from typing import Any, Optional


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_heptatrader_round36_certification as builder  # noqa: E402


VERIFICATION_SCHEMA = (
    "heptatrader.round36-final-certification-verification.v1")
TOP_LEVEL_FIELDS = {
    "schema",
    "version",
    "project_id",
    "round",
    "status",
    "passed",
    "local_delivery",
    "external_evidence",
    "certification_flags",
    "blocked_external_evidence",
}
LOCAL_FIELDS = {
    "closure_input",
    "source_round",
    "source_release_version",
    "passed_scope",
    "artifact_root",
    "artifact_bindings_sha256",
    "artifacts",
    "source_lineage",
}
EXTERNAL_FIELDS = {"native_systemd", "production_receipt"}
NATIVE_FIELDS = {
    "input",
    "variant_inputs",
    "schema",
    "certification_level",
    "distinct_native_vms",
    "distinct_provisioner_attested_instances",
    "external_instance_receipts_verified",
    "four_uid_agent_os_runtime_preflight_verified",
    "runtime_contract_verified",
    "clean_source_bundle_sha256",
    "clean_source_manifest_sha256",
    "clean_source_files_sha256",
}
RECEIPT_FIELDS = {
    "inputs",
    "evidence_root",
    "schema",
    "trust_scope",
    "signature_status",
    "retention_status",
    "current_policy_satisfied_object_count",
    "statement_sha256",
    "request_sha256",
    "index_sha256",
    "evidence_set_manifest_sha256",
    "trust_policy_sha256",
    "evidence_set_id",
    "production_contract_verified",
}
SOURCE_LINEAGE_FIELDS = {
    "git_head",
    "source_manifest_sha256",
    "source_manifest_file_count",
    "source_baseline_sha256",
    "source_baseline_size",
    "source_baseline_mode",
    "strict_source_bundle_sha256",
    "strict_source_bundle_manifest_sha256",
    "strict_source_files_sha256",
}
SAFE_MODE = re.compile(r"0[0-7]{3}")


class Round36CertificationVerificationError(RuntimeError):
    """The report or one of its bound inputs failed closed."""


def _fail(message: str) -> None:
    raise Round36CertificationVerificationError(message)


def _binding(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != builder.RAW_BINDING_FIELDS:
        _fail(f"{label} raw binding fields are invalid")
    path = value["path"]
    mode = value["mode"]
    if (not isinstance(path, str) or not path or "\0" in path or
            not Path(path).is_absolute() or
            not isinstance(value["sha256"], str) or
            builder.HEX64.fullmatch(value["sha256"]) is None or
            type(value["size"]) is not int or value["size"] < 0 or
            not isinstance(mode, str) or SAFE_MODE.fullmatch(mode) is None or
            int(mode, 8) & 0o022 or int(mode, 8) & 0o7000):
        _fail(f"{label} raw binding is invalid")
    return value


def _source_lineage(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != SOURCE_LINEAGE_FIELDS:
        _fail("Round36 source-lineage fields are invalid")
    for field in (
            "source_baseline_sha256",
            "strict_source_bundle_sha256",
            "strict_source_bundle_manifest_sha256",
            "strict_source_files_sha256"):
        if (not isinstance(value[field], str) or
                builder.HEX64.fullmatch(value[field]) is None):
            _fail(f"Round36 {field} is invalid")
    source_manifest = value["source_manifest_sha256"]
    if (not isinstance(source_manifest, str) or
            re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}",
                         source_manifest) is None or
            not isinstance(value["git_head"], str) or
            re.fullmatch(r"[0-9a-f]{40,64}", value["git_head"]) is None or
            type(value["source_manifest_file_count"]) is not int or
            value["source_manifest_file_count"] <= 0 or
            type(value["source_baseline_size"]) is not int or
            value["source_baseline_size"] < 0 or
            value["source_baseline_mode"] != "0600"):
        _fail("Round36 source-lineage values are invalid")


def validate_structure(value: Any) -> dict[str, Any]:
    """Validate static shape and fail-closed flag relationships."""
    if not isinstance(value, dict) or set(value) != TOP_LEVEL_FIELDS:
        _fail("Round36 certification fields do not exactly match schema")
    if (value["schema"] != builder.SCHEMA or
            type(value["version"]) is not int or
            value["version"] != builder.VERSION or
            value["project_id"] != builder.PROJECT_ID or
            type(value["round"]) is not int or
            value["round"] != builder.ROUND or
            value["status"] not in {"pending-external", "certified"} or
            type(value["passed"]) is not bool):
        _fail("unsupported Round36 certification contract")

    local = value["local_delivery"]
    if not isinstance(local, dict) or set(local) != LOCAL_FIELDS:
        _fail("Round36 local-delivery fields are invalid")
    _binding(local["closure_input"], "Round36 closure")
    if (local["closure_input"]["mode"] != "0600" or
            Path(local["closure_input"]["path"]).name !=
            builder.SOURCE_CLOSURE_NAME or
            local["source_round"] != builder.SOURCE_ROUND or
            local["source_release_version"] !=
            builder.SOURCE_RELEASE_VERSION or
            local["passed_scope"] !=
            builder.closure_contract.LOCAL_OFFLINE_SCOPE or
            not isinstance(local["artifact_root"], str) or
            not Path(local["artifact_root"]).is_absolute() or
            Path(local["artifact_root"]).name !=
            builder.SOURCE_ARTIFACT_ROOT_NAME or
            not isinstance(local["artifact_bindings_sha256"], str) or
            builder.HEX64.fullmatch(
                local["artifact_bindings_sha256"]) is None or
            not isinstance(local["artifacts"], list) or
            len(local["artifacts"]) !=
            len(builder.closure_contract.REQUIRED_ARTIFACT_ROLES)):
        _fail("Round36 local-delivery identity is invalid")
    _source_lineage(local["source_lineage"])

    external = value["external_evidence"]
    if not isinstance(external, dict) or set(external) != EXTERNAL_FIELDS:
        _fail("Round36 external-evidence fields are invalid")
    native = external["native_systemd"]
    if native is not None:
        if not isinstance(native, dict) or set(native) != NATIVE_FIELDS:
            _fail("native runtime evidence summary is invalid")
        _binding(native["input"], "native runtime aggregate")
        try:
            variant_inputs = (
                builder.native_aggregate.parse_variant_report_inputs(
                    native["variant_inputs"]))
        except builder.native_aggregate.AggregateError as error:
            raise Round36CertificationVerificationError(
                "native raw variant bindings are invalid") from error
        if variant_inputs != native["variant_inputs"]:
            _fail("native raw variant binding normalization drift")
        for field in (
                "clean_source_bundle_sha256",
                "clean_source_manifest_sha256",
                "clean_source_files_sha256"):
            if (not isinstance(native[field], str) or
                    builder.HEX64.fullmatch(native[field]) is None):
                _fail(f"native runtime {field} is invalid")
        if (native["input"]["mode"] != "0600" or
                type(native["distinct_native_vms"]) is not int or
                native["distinct_native_vms"] != 3 or
                type(native["distinct_provisioner_attested_instances"]) is not
                int or native["distinct_provisioner_attested_instances"] != 3 or
                native["external_instance_receipts_verified"] is not True or
                native["four_uid_agent_os_runtime_preflight_verified"] is not
                True or
                native["runtime_contract_verified"] is not True or
                not isinstance(native["schema"], str) or
                not native["schema"] or
                not isinstance(native["certification_level"], str) or
                not native["certification_level"]):
            _fail("native runtime production contract is invalid")

    receipt = external["production_receipt"]
    if receipt is not None:
        if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
            _fail("production receipt evidence summary is invalid")
        inputs = receipt["inputs"]
        if (not isinstance(inputs, dict) or
                set(inputs) != set(builder.RECEIPT_INPUT_ROLES)):
            _fail("production receipt raw-input roles are invalid")
        for role in builder.RECEIPT_INPUT_ROLES:
            _binding(inputs[role], f"production receipt {role}")
        if (not isinstance(receipt["evidence_root"], str) or
                not Path(receipt["evidence_root"]).is_absolute() or
                receipt["schema"] !=
                builder.receipt_verifier.VERIFICATION_SCHEMA or
                receipt["trust_scope"] != "system-production" or
                receipt["signature_status"] != "verified" or
                receipt["retention_status"] !=
                "current-policy-satisfied" or
                type(receipt[
                    "current_policy_satisfied_object_count"]) is not int or
                receipt["current_policy_satisfied_object_count"] <= 0 or
                receipt["production_contract_verified"] is not True or
                not isinstance(receipt["evidence_set_id"], str) or
                not receipt["evidence_set_id"]):
            _fail("production receipt production/current contract is invalid")
        for field in (
                "statement_sha256", "request_sha256", "index_sha256",
                "evidence_set_manifest_sha256", "trust_policy_sha256"):
            if (not isinstance(receipt[field], str) or
                    builder.HEX64.fullmatch(receipt[field]) is None):
                _fail(f"production receipt {field} is invalid")

    flags = value["certification_flags"]
    if (not isinstance(flags, dict) or
            set(flags) != builder.CERTIFICATION_FLAG_FIELDS or
            any(type(flag) is not bool for flag in flags.values()) or
            any(flags[field] is not False
                for field in builder.ALWAYS_FALSE_FLAG_FIELDS)):
        _fail("Round36 certification safety flags are invalid")
    blockers = value["blocked_external_evidence"]
    expected_blockers = []
    if native is None:
        expected_blockers.append(builder.NATIVE_BLOCKER)
    if receipt is None:
        expected_blockers.append(builder.RECEIPT_BLOCKER)
    if blockers != expected_blockers:
        _fail("Round36 external blocker set is not verifier-derived")
    certified = not expected_blockers
    if (value["status"] !=
            ("certified" if certified else "pending-external") or
            value["passed"] is not certified or
            flags != builder._certification_flags(certified)):
        _fail("Round36 status or certification flags are not fail-closed")
    return value


def _read_report(
    path: Path,
) -> tuple[builder.CapturedFile, dict[str, Any]]:
    try:
        captured = builder._capture_file(
            path,
            "Round36 certification report",
            limit=builder.MAX_CERTIFICATION_BYTES,
            capture_data=True,
            require_trusted_parent=True,
            required_mode="0600",
        )
        document = builder._strict_document(
            captured, "Round36 certification report")
    except builder.Round36CertificationError as error:
        raise Round36CertificationVerificationError(str(error)) from error
    try:
        validated = validate_structure(document)
        expected_bytes = builder.canonical_json(validated) + b"\n"
    except builder.Round36CertificationError as error:
        raise Round36CertificationVerificationError(str(error)) from error
    if captured.snapshot.data != expected_bytes:
        _fail(
            "Round36 certification is not canonical JSON plus one newline")
    return captured, validated


def _receipt_inputs(
    summary: dict[str, Any] | None,
) -> builder.ReceiptInputs | None:
    if summary is None:
        return None
    inputs = summary["inputs"]
    return builder.ReceiptInputs(
        receipt=Path(inputs["receipt"]["path"]),
        request=Path(inputs["request"]["path"]),
        trust_policy=Path(inputs["trust_policy"]["path"]),
        index=Path(inputs["index"]["path"]),
        evidence_root=Path(summary["evidence_root"]),
        retention_policy=Path(inputs["retention_policy"]["path"]),
        evidence_set_manifest=Path(
            inputs["evidence_set_manifest"]["path"]),
    )


def verify(path: Path) -> dict[str, Any]:
    """Re-run both independent verifiers and compare the exact document."""
    captured, document = _read_report(path)
    external = document["external_evidence"]
    native = external["native_systemd"]
    try:
        expected = builder.build_certification(
            Path(document["local_delivery"]["closure_input"]["path"]),
            Path(document["local_delivery"]["artifact_root"]),
            native_aggregate_path=(
                Path(native["input"]["path"]) if native is not None else None),
            receipt_inputs=_receipt_inputs(
                external["production_receipt"]),
        )
    except builder.Round36CertificationError as error:
        raise Round36CertificationVerificationError(
            "Round36 bound evidence failed independent reconstruction"
        ) from error
    if expected != document:
        _fail("Round36 certification differs from verifier-derived result")
    try:
        builder._recheck_file(
            captured, "Round36 certification report")
    except builder.Round36CertificationError as error:
        raise Round36CertificationVerificationError(str(error)) from error
    return {
        "schema": VERIFICATION_SCHEMA,
        "version": 1,
        "status": document["status"],
        "passed": document["passed"],
        "report_sha256": captured.snapshot.sha256,
        "source_round": builder.SOURCE_ROUND,
        "source_release_version": builder.SOURCE_RELEASE_VERSION,
        "blocked_external_evidence":
            document["blocked_external_evidence"],
        "production_certified":
            document["certification_flags"]["production_certified"],
        "real_systemd_certified":
            document["certification_flags"]["real_systemd_certified"],
        "object_store_ingestion_receipt_certified":
            document["certification_flags"][
                "object_store_ingestion_receipt_certified"],
        "retention_enforcement_certified":
            document["certification_flags"][
                "retention_enforcement_certified"],
        "real_ib_certified": False,
        "paper_authorized": False,
        "live_authorized": False,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="verify a Round36 final-certification document")
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args(argv)
    result = verify(arguments.report)
    print(
        "heptatrader-round36-certification-verification: "
        f"status={result['status']} "
        f"passed={str(result['passed']).lower()} "
        f"report_sha256={result['report_sha256']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Round36CertificationVerificationError,
            builder.Round36CertificationError, OSError) as error:
        print(
            f"heptatrader-round36-certification-verification: FAIL {error}",
            file=sys.stderr)
        raise SystemExit(78)
