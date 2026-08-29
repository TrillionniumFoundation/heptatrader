#!/usr/bin/env python3
"""Build the fail-closed Round36 final-certification document.

This builder cannot turn caller-supplied booleans into certification.  It
always re-verifies the fixed Round36 local delivery lineage.  Native
systemd and production-retention claims are admitted only through their
independent verifiers; either missing evidence class leaves the document
pending external with every production certification flag false.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any, Callable, Optional


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import aggregate_hepta_execution_native_systemd_gate as native_aggregate  # noqa: E402
import build_heptatrader_delivery_closure as closure_contract  # noqa: E402
import build_heptatrader_evidence_ingestion_request as request_contract  # noqa: E402
import verify_heptatrader_delivery_closure as closure_verifier  # noqa: E402
import verify_heptatrader_evidence_ingestion_receipt as receipt_verifier  # noqa: E402


SCHEMA = "heptatrader.round36-final-certification.v1"
VERSION = 1
PROJECT_ID = closure_contract.PROJECT_ID
ROUND = 36
SOURCE_ROUND = 36
SOURCE_RELEASE_VERSION = "0.1.0-beta.1-round36"
SOURCE_CLOSURE_NAME = (
    "heptatrader-round36-semantic-v1-delivery-closure-v1.json")
SOURCE_ARTIFACT_ROOT_NAME = (
    "heptatrader-round36-semantic-delivery-artifacts-v1")
OUTPUT_NAME = "heptatrader-round36-final-certification-v1.json"
MAX_CERTIFICATION_BYTES = 4 * 1024 * 1024
MAX_NATIVE_AGGREGATE_BYTES = 16 * 1024 * 1024
HEX64 = closure_contract.HEX64

NATIVE_BLOCKER = "native-three-vm-four-uid-agent-os-runtime"
RECEIPT_BLOCKER = "production-ingestion-receipt-current-retention"

CERTIFICATION_FLAG_FIELDS = {
    "production_certified",
    "real_systemd_certified",
    "object_store_ingestion_receipt_certified",
    "retention_enforcement_certified",
    "real_ib_certified",
    "paper_authorized",
    "live_authorized",
    "broker_connection_performed",
    "order_placement_performed",
    "source_files_deleted",
    "source_removal_authorized",
}
ALWAYS_FALSE_FLAG_FIELDS = {
    "real_ib_certified",
    "paper_authorized",
    "live_authorized",
    "broker_connection_performed",
    "order_placement_performed",
    "source_files_deleted",
    "source_removal_authorized",
}
RAW_BINDING_FIELDS = {"path", "sha256", "size", "mode"}
RECEIPT_INPUT_ROLES = (
    "receipt",
    "request",
    "index",
    "evidence_set_manifest",
    "retention_policy",
    "trust_policy",
)


class Round36CertificationError(RuntimeError):
    """A Round36 certification input or publication failed closed."""


def _fail(message: str) -> None:
    raise Round36CertificationError(message)


@dataclass(frozen=True)
class CapturedFile:
    path: Path
    snapshot: closure_contract.StableRead
    limit: int
    capture_data: bool
    require_trusted_parent: bool

    @property
    def binding(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.snapshot.sha256,
            "size": self.snapshot.size,
            "mode": self.snapshot.mode,
        }


@dataclass(frozen=True)
class ReceiptInputs:
    receipt: Path
    request: Path
    trust_policy: Path
    index: Path
    evidence_root: Path
    retention_policy: Path
    evidence_set_manifest: Path


def canonical_json(value: Any) -> bytes:
    try:
        return request_contract.canonical_json(value)
    except (TypeError, ValueError,
            request_contract.IngestionRequestError) as error:
        raise Round36CertificationError(
            "Round36 certification is not canonical JSON data") from error


def _canonical_existing_file(path: Path, label: str) -> Path:
    try:
        absolute = Path(os.path.abspath(os.fspath(path)))
        resolved = absolute.resolve(strict=True)
    except (OSError, TypeError, ValueError) as error:
        raise Round36CertificationError(
            f"{label} path is unavailable or unsafe") from error
    if absolute != resolved or not resolved.is_file():
        _fail(f"{label} path must be a canonical regular file")
    return resolved


def _canonical_existing_directory(path: Path, label: str) -> Path:
    try:
        absolute = Path(os.path.abspath(os.fspath(path)))
        resolved = absolute.resolve(strict=True)
    except (OSError, TypeError, ValueError) as error:
        raise Round36CertificationError(
            f"{label} path is unavailable or unsafe") from error
    if absolute != resolved or not resolved.is_dir():
        _fail(f"{label} path must be a canonical directory")
    metadata = os.lstat(resolved)
    if (not stat.S_ISDIR(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) & 0o022 or
            stat.S_IMODE(metadata.st_mode) & 0o7000):
        _fail(f"{label} directory is not a protected trust boundary")
    return resolved


def _capture_file(
    path: Path,
    label: str,
    *,
    limit: int,
    capture_data: bool = True,
    require_trusted_parent: bool = False,
    required_mode: str | None = None,
) -> CapturedFile:
    canonical = _canonical_existing_file(path, label)
    try:
        snapshot = closure_contract.stable_read(
            canonical,
            limit=limit,
            capture=capture_data,
            require_trusted_parent=require_trusted_parent,
        )
    except closure_contract.DeliveryClosureError as error:
        raise Round36CertificationError(
            f"{label} failed stable read: {error}") from error
    if required_mode is not None and snapshot.mode != required_mode:
        _fail(f"{label} mode must remain {required_mode}")
    return CapturedFile(
        path=canonical,
        snapshot=snapshot,
        limit=limit,
        capture_data=capture_data,
        require_trusted_parent=require_trusted_parent,
    )


def _recheck_file(captured: CapturedFile, label: str) -> None:
    confirmed = _capture_file(
        captured.path,
        label,
        limit=captured.limit,
        capture_data=captured.capture_data,
        require_trusted_parent=captured.require_trusted_parent,
        required_mode=captured.snapshot.mode,
    )
    if confirmed.path != captured.path or confirmed.snapshot != captured.snapshot:
        _fail(f"{label} changed across Round36 certification")


def _strict_document(captured: CapturedFile, label: str) -> Any:
    data = captured.snapshot.data
    if data is None:
        _fail(f"{label} contents were not captured")
    try:
        return closure_contract.strict_json(data, label)
    except closure_contract.DeliveryClosureError as error:
        raise Round36CertificationError(str(error)) from error


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        _fail(f"{label} is not a lowercase SHA-256 digest")
    return value


def _artifact_capture(
    artifact_root: Path,
    artifact: dict[str, Any],
) -> CapturedFile:
    role = artifact["role"]
    capture_data = role == "strict-source-bundle-manifest"
    limit = (
        closure_contract.MAX_JSON_ARTIFACT_BYTES
        if capture_data else closure_contract.MAX_ARTIFACT_BYTES)
    captured = _capture_file(
        artifact_root / artifact["path"],
        f"Round36 artifact {role}",
        limit=limit,
        capture_data=capture_data,
        require_trusted_parent=True,
    )
    for field, observed in (
            ("sha256", captured.snapshot.sha256),
            ("size", captured.snapshot.size),
            ("mode", captured.snapshot.mode)):
        if artifact[field] != observed:
            _fail(f"Round36 artifact {role} {field} binding drift")
    return captured


def _verify_local_delivery(
    closure_path: Path,
    artifact_root: Path,
) -> tuple[dict[str, Any], dict[str, Any],
           CapturedFile, list[tuple[str, CapturedFile]]]:
    closure = _capture_file(
        closure_path,
        "Round36 delivery closure",
        limit=closure_contract.MAX_CLOSURE_BYTES,
        capture_data=True,
        require_trusted_parent=True,
        required_mode="0600",
    )
    if closure.path.name != SOURCE_CLOSURE_NAME:
        _fail(
            f"Round36 requires the fixed {SOURCE_CLOSURE_NAME} input")
    closure_document = _strict_document(
        closure, "Round36 delivery closure")
    try:
        closure_document = closure_contract.validate_contract_structure(
            closure_document)
    except closure_contract.DeliveryClosureError as error:
        raise Round36CertificationError(
            f"Round36 delivery closure contract failed: {error}") from error
    if (closure.snapshot.data !=
            canonical_json(closure_document) + b"\n"):
        _fail("Round36 delivery closure is not canonical JSON")
    root = _canonical_existing_directory(
        artifact_root, "Round36 artifact root")
    if root.name != SOURCE_ARTIFACT_ROOT_NAME:
        _fail(
            f"Round36 requires the fixed {SOURCE_ARTIFACT_ROOT_NAME} root")
    try:
        verified = closure_verifier.verify(closure.path, root)
    except (closure_verifier.DeliveryClosureVerificationError,
            closure_contract.DeliveryClosureError, OSError) as error:
        raise Round36CertificationError(
            "Round36 delivery closure did not independently verify"
        ) from error
    if (closure_document["project_id"] != PROJECT_ID or
            closure_document["round"] != SOURCE_ROUND or
            closure_document["release_version"] != SOURCE_RELEASE_VERSION or
            closure_document["passed"] is not True or
            closure_document["passed_scope"] !=
            closure_contract.LOCAL_OFFLINE_SCOPE or
            verified.get("round") != SOURCE_ROUND or
            verified.get("release_version") != SOURCE_RELEASE_VERSION or
            verified.get("passed") is not True or
            verified.get("passed_scope") !=
            closure_contract.LOCAL_OFFLINE_SCOPE or
            verified.get("closure_sha256") != closure.snapshot.sha256 or
            verified.get("artifact_roles") !=
            list(closure_contract.REQUIRED_ARTIFACT_ROLES) or
            any(verified.get(field) is not False for field in (
                "broker_connection_performed",
                "order_placement_performed",
                "paper_authorized",
                "live_authorized",
                "real_systemd_certified",
                "real_ib_certified",
                "object_store_ingestion_receipt_certified",
                "retention_enforcement_certified"))):
        _fail("Round36 delivery verification identity or boundary drift")

    artifacts: list[tuple[str, CapturedFile]] = []
    for artifact in closure_document["artifacts"]:
        artifacts.append((
            artifact["role"], _artifact_capture(root, artifact)))

    bundle_manifest_capture = dict(artifacts)[
        "strict-source-bundle-manifest"]
    bundle_manifest = _strict_document(
        bundle_manifest_capture, "strict source bundle manifest")
    if (not isinstance(bundle_manifest, dict) or
            bundle_manifest.get("version") != SOURCE_RELEASE_VERSION or
            bundle_manifest.get("git_head") != verified.get("git_head") or
            bundle_manifest.get("security_manifest_sha256") !=
            verified.get("source_manifest_sha256")):
        _fail("strict source bundle manifest lineage drift")
    source_files_sha256 = _require_sha256(
        bundle_manifest.get("files_sha256"),
        "strict source files_sha256")

    artifact_bindings = closure_document["artifacts"]
    baseline = next(
        item for item in artifact_bindings
        if item["role"] == "source-baseline-manifest")
    source_lineage = {
        "git_head": verified["git_head"],
        "source_manifest_sha256": verified["source_manifest_sha256"],
        "source_manifest_file_count":
            verified["source_manifest_file_count"],
        "source_baseline_sha256": baseline["sha256"],
        "source_baseline_size": baseline["size"],
        "source_baseline_mode": baseline["mode"],
        "strict_source_bundle_sha256": verified["bundle_sha256"],
        "strict_source_bundle_manifest_sha256":
            verified["bundle_manifest_sha256"],
        "strict_source_files_sha256": source_files_sha256,
    }
    local = {
        "closure_input": closure.binding,
        "source_round": SOURCE_ROUND,
        "source_release_version": SOURCE_RELEASE_VERSION,
        "passed_scope": closure_contract.LOCAL_OFFLINE_SCOPE,
        "artifact_root": str(root),
        "artifact_bindings_sha256":
            hashlib.sha256(canonical_json(artifact_bindings)).hexdigest(),
        "artifacts": artifact_bindings,
        "source_lineage": source_lineage,
    }
    return local, closure_document, closure, artifacts


def _require_native_root_ownership(captured: CapturedFile) -> None:
    identity = captured.snapshot.identity
    # build_heptatrader_delivery_closure._file_identity stores uid/gid at 4/5.
    if len(identity) < 6 or identity[4] != 0 or identity[5] != 0:
        _fail("native runtime aggregate must be root-owned")


def _verify_native_runtime(
    path: Path,
    source_lineage: dict[str, Any],
) -> tuple[dict[str, Any], CapturedFile, dict[str, Any]]:
    captured = _capture_file(
        path,
        "native three-VM runtime aggregate",
        limit=MAX_NATIVE_AGGREGATE_BYTES,
        capture_data=True,
        require_trusted_parent=True,
        required_mode="0600",
    )
    _require_native_root_ownership(captured)
    document = _strict_document(
        captured, "native three-VM runtime aggregate")

    verifier = getattr(native_aggregate, "verify_runtime_aggregate", None)
    runtime_schema = getattr(
        native_aggregate, "RUNTIME_AGGREGATE_SCHEMA", None)
    runtime_level = getattr(
        native_aggregate, "RUNTIME_CERTIFICATION_LEVEL", None)
    if (not callable(verifier) or
            not isinstance(runtime_schema, str) or not runtime_schema or
            not isinstance(runtime_level, str) or not runtime_level):
        _fail(
            "runtime-capable native aggregate verifier contract is unavailable")
    try:
        parsed = verifier(document)
    except Exception as error:
        raise Round36CertificationError(
            "native aggregate failed raw-report reconstruction") from error
    if not isinstance(parsed, dict) or parsed != document:
        _fail("native runtime aggregate verifier returned an invalid contract")

    common = parsed.get("common_closure")
    boundary = parsed.get("boundary")
    variants = parsed.get("variants")
    if (parsed.get("schema") != runtime_schema or
            parsed.get("passed") is not True or
            parsed.get("certification_level") != runtime_level or
            not isinstance(variants, dict) or
            set(variants) != set(native_aggregate.VARIANTS) or
            not isinstance(common, dict) or
            common.get("distinct_native_vms") != 3 or
            common.get("distinct_provisioner_attested_instances") != 3 or
            common.get("external_instance_receipts_verified") is not True or
            common.get("instance_receipt_validity_windows_overlap") is not
            True or
            common.get("all_agent_os_runtime_preflights_executed") is not
            True or
            not isinstance(boundary, dict) or
            boundary.get("agent_os_runtime_preflight_executed") is not True or
            boundary.get("native_agent_os_runtime_gate_satisfied") is not
            True or
            boundary.get("agent_os_runtime_evidence_fabricated") is not False):
        _fail("native aggregate is not the production runtime contract")

    expected_lineage = {
        "clean_source_bundle_sha256":
            source_lineage["strict_source_bundle_sha256"],
        "clean_source_manifest_sha256":
            source_lineage["strict_source_bundle_manifest_sha256"],
        "clean_source_files_sha256":
            source_lineage["strict_source_files_sha256"],
    }
    if any(common.get(field) != digest
           for field, digest in expected_lineage.items()):
        _fail("native runtime aggregate crosses the Round36 source lineage")

    summary = {
        "input": captured.binding,
        "variant_inputs": parsed["aggregation_inputs"],
        "schema": runtime_schema,
        "certification_level": runtime_level,
        "distinct_native_vms": 3,
        "distinct_provisioner_attested_instances": 3,
        "external_instance_receipts_verified": True,
        "four_uid_agent_os_runtime_preflight_verified": True,
        "runtime_contract_verified": True,
        **expected_lineage,
    }
    return summary, captured, document


def _receipt_capture_map(
    inputs: ReceiptInputs,
) -> tuple[dict[str, CapturedFile], Path]:
    roles = {
        "receipt": inputs.receipt,
        "request": inputs.request,
        "index": inputs.index,
        "evidence_set_manifest": inputs.evidence_set_manifest,
        "retention_policy": inputs.retention_policy,
        "trust_policy": inputs.trust_policy,
    }
    captures = {
        role: _capture_file(
            path,
            f"production receipt input {role}",
            limit=request_contract.MAX_JSON_BYTES,
            capture_data=True,
            require_trusted_parent=False,
        )
        for role, path in roles.items()
    }
    evidence_root = _canonical_existing_directory(
        inputs.evidence_root, "production receipt evidence root")
    return captures, evidence_root


def _manifest_artifacts(
    manifest_capture: CapturedFile,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _strict_document(
        manifest_capture, "production evidence-set manifest")
    if (not isinstance(manifest, dict) or
            not isinstance(manifest.get("artifacts"), list)):
        _fail("production evidence-set manifest is incomplete")
    artifacts: dict[str, dict[str, Any]] = {}
    for item in manifest["artifacts"]:
        if (not isinstance(item, dict) or
                not isinstance(item.get("role"), str) or
                item["role"] in artifacts):
            _fail("production evidence-set role closure is invalid")
        artifacts[item["role"]] = item
    return manifest, artifacts


def _verify_receipt_lineage(
    report: dict[str, Any],
    captures: dict[str, CapturedFile],
    closure_document: dict[str, Any],
) -> None:
    if (report.get("schema") != receipt_verifier.VERIFICATION_SCHEMA or
            report.get("version") != 2 or
            report.get("production_trust") is not True or
            report.get("trust_scope") != "system-production" or
            report.get("signature_status") != "verified" or
            report.get("retention_status") !=
            "current-policy-satisfied" or
            report.get("evidence_set_bound") is not True or
            report.get("evidence_set_certified") is not True or
            report.get("source_files_deleted") is not False or
            report.get("source_removal_authorized") is not False or
            report.get("paper_authorized") is not False or
            report.get("live_authorized") is not False):
        _fail("receipt verifier did not return the production/current contract")
    objects = report.get("objects")
    object_count = report.get("current_policy_satisfied_object_count")
    if (not isinstance(objects, list) or not objects or
            type(object_count) is not int or object_count != len(objects)):
        _fail("production receipt current-retention object closure is invalid")
    for field in (
            "statement_sha256", "request_sha256", "index_sha256",
            "evidence_set_manifest_sha256", "trust_policy_sha256"):
        _require_sha256(report.get(field), f"receipt verification {field}")
    if (report["request_sha256"] != captures["request"].snapshot.sha256 or
            report["index_sha256"] !=
            captures["index"].snapshot.sha256 or
            report["trust_policy_sha256"] !=
            captures["trust_policy"].snapshot.sha256 or
            report["evidence_set_manifest_sha256"] !=
            captures["evidence_set_manifest"].snapshot.sha256):
        _fail("production receipt raw-input digest binding drift")
    receipt = report.get("receipt")
    if (not isinstance(receipt, dict) or
            not isinstance(receipt.get("statement"), dict) or
            receipt["statement"].get("policy_sha256") !=
            captures["retention_policy"].snapshot.sha256):
        _fail("production receipt retention-policy digest binding drift")

    evidence_set = report.get("evidence_set")
    if (not isinstance(evidence_set, dict) or
            evidence_set.get("round") != SOURCE_ROUND or
            evidence_set.get("release_version") != SOURCE_RELEASE_VERSION or
            evidence_set.get("manifest_sha256") !=
            captures["evidence_set_manifest"].snapshot.sha256):
        _fail("production receipt crosses the Round36 release lineage")
    closure_baseline = next(
        item for item in closure_document["artifacts"]
        if item["role"] == "source-baseline-manifest")
    expected_baseline = {
        "path": closure_baseline["path"],
        "sha256": closure_baseline["sha256"],
        "size": closure_baseline["size"],
        "mode": closure_baseline["mode"],
    }
    if evidence_set.get("source_baseline") != expected_baseline:
        _fail("production receipt source-baseline lineage drift")

    manifest, manifest_artifacts = _manifest_artifacts(
        captures["evidence_set_manifest"])
    if (manifest.get("round") != SOURCE_ROUND or
            manifest.get("release_version") != SOURCE_RELEASE_VERSION):
        _fail("production evidence-set manifest release lineage drift")
    round_closure = manifest_artifacts.get("round-closure")
    if not isinstance(round_closure, dict):
        _fail("production evidence-set round-closure role is missing")
    closure_binding = {
        "sha256": hashlib.sha256(
            canonical_json(closure_document) + b"\n").hexdigest(),
        "size": len(canonical_json(closure_document) + b"\n"),
        "mode": "0600",
    }
    if any(round_closure.get(field) != value
           for field, value in closure_binding.items()):
        _fail("production receipt does not bind the exact Round36 closure")

    for closure_artifact in closure_document["artifacts"]:
        role = closure_artifact["role"]
        bound = manifest_artifacts.get(role)
        if not isinstance(bound, dict):
            _fail(f"production evidence set is missing delivery role {role}")
        if any(bound.get(field) != closure_artifact[field]
               for field in ("sha256", "size", "mode")):
            _fail(f"production evidence delivery role {role} lineage drift")


def _verify_production_receipt(
    inputs: ReceiptInputs,
    closure_document: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, CapturedFile]]:
    captures, evidence_root = _receipt_capture_map(inputs)
    paths = {role: captured.path for role, captured in captures.items()}
    try:
        report = receipt_verifier.verify_receipt(
            paths["receipt"],
            paths["request"],
            paths["trust_policy"],
            paths["index"],
            evidence_root,
            paths["retention_policy"],
            paths["evidence_set_manifest"],
            require_system_trust=True,
        )
    except (receipt_verifier.IngestionReceiptError,
            request_contract.IngestionRequestError, OSError) as error:
        raise Round36CertificationError(
            "production receipt failed system-trust verification") from error
    if not isinstance(report, dict):
        _fail("production receipt verifier returned an invalid result")
    _verify_receipt_lineage(report, captures, closure_document)
    evidence_set = report["evidence_set"]
    summary = {
        "inputs": {
            role: captures[role].binding for role in RECEIPT_INPUT_ROLES
        },
        "evidence_root": str(evidence_root),
        "schema": receipt_verifier.VERIFICATION_SCHEMA,
        "trust_scope": "system-production",
        "signature_status": "verified",
        "retention_status": "current-policy-satisfied",
        "current_policy_satisfied_object_count":
            report["current_policy_satisfied_object_count"],
        "statement_sha256": report["statement_sha256"],
        "request_sha256": report["request_sha256"],
        "index_sha256": report["index_sha256"],
        "evidence_set_manifest_sha256":
            report["evidence_set_manifest_sha256"],
        "trust_policy_sha256": report["trust_policy_sha256"],
        "evidence_set_id": evidence_set["evidence_set_id"],
        "production_contract_verified": True,
    }
    return summary, captures


def _certification_flags(certified: bool) -> dict[str, bool]:
    flags = {
        field: False for field in CERTIFICATION_FLAG_FIELDS
    }
    if certified:
        for field in (
                "production_certified",
                "real_systemd_certified",
                "object_store_ingestion_receipt_certified",
                "retention_enforcement_certified"):
            flags[field] = True
    return flags


def build_certification(
    closure_path: Path,
    artifact_root: Path,
    *,
    native_aggregate_path: Path | None = None,
    receipt_inputs: ReceiptInputs | None = None,
) -> dict[str, Any]:
    """Construct one deterministic report from verifier-derived facts only."""
    local, closure_document, closure_capture, artifact_captures = (
        _verify_local_delivery(closure_path, artifact_root))

    native_summary: dict[str, Any] | None = None
    native_capture: CapturedFile | None = None
    native_document: dict[str, Any] | None = None
    if native_aggregate_path is not None:
        native_summary, native_capture, native_document = (
            _verify_native_runtime(
                native_aggregate_path, local["source_lineage"]))

    receipt_summary: dict[str, Any] | None = None
    receipt_captures: dict[str, CapturedFile] = {}
    if receipt_inputs is not None:
        receipt_summary, receipt_captures = _verify_production_receipt(
            receipt_inputs, closure_document)

    blockers = []
    if native_summary is None:
        blockers.append(NATIVE_BLOCKER)
    if receipt_summary is None:
        blockers.append(RECEIPT_BLOCKER)
    certified = not blockers
    report = {
        "schema": SCHEMA,
        "version": VERSION,
        "project_id": PROJECT_ID,
        "round": ROUND,
        "status": "certified" if certified else "pending-external",
        "passed": certified,
        "local_delivery": local,
        "external_evidence": {
            "native_systemd": native_summary,
            "production_receipt": receipt_summary,
        },
        "certification_flags": _certification_flags(certified),
        "blocked_external_evidence": blockers,
    }

    _recheck_file(closure_capture, "Round36 delivery closure")
    for role, captured in artifact_captures:
        _recheck_file(captured, f"Round36 artifact {role}")
    if native_capture is not None:
        _recheck_file(native_capture, "native three-VM runtime aggregate")
    if native_document is not None:
        try:
            if native_aggregate.verify_runtime_aggregate(
                    native_document) != native_document:
                _fail("native raw reports changed across certification")
        except native_aggregate.AggregateError as error:
            raise Round36CertificationError(
                "native raw reports changed across certification") from error
    for role, captured in receipt_captures.items():
        _recheck_file(captured, f"production receipt input {role}")
    return report


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _publication_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _atomic_publish(
    output: Path,
    payload: bytes,
    validate: Callable[[Path], None],
) -> None:
    absolute = Path(os.path.abspath(output))
    if output != absolute or absolute.name != OUTPUT_NAME:
        _fail(
            f"Round36 output must be absolute and named {OUTPUT_NAME}")
    if len(payload) > MAX_CERTIFICATION_BYTES:
        _fail("Round36 certification exceeds its size limit")
    parent = _canonical_existing_directory(
        absolute.parent, "Round36 output parent")
    parent_metadata = os.lstat(parent)
    if parent_metadata.st_uid != os.geteuid():
        _fail("Round36 output parent must be caller-owned")

    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(parent, directory_flags)
    temporary = (
        f".{OUTPUT_NAME}.{os.getpid()}.{secrets.token_hex(16)}.tmp")
    published = False
    published_inode: tuple[int, int] | None = None
    try:
        opened_parent = os.fstat(descriptor)
        if (_directory_identity(opened_parent) !=
                _directory_identity(parent_metadata)):
            _fail("Round36 output parent changed while opening")
        try:
            os.stat(absolute.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _fail("refusing to overwrite an existing Round36 certification")

        file_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=descriptor,
        )
        try:
            os.fchmod(file_descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                written = os.write(file_descriptor, payload[offset:])
                if written <= 0:
                    _fail("short Round36 certification write")
                offset += written
            os.fsync(file_descriptor)
            temporary_metadata = os.fstat(file_descriptor)
        finally:
            os.close(file_descriptor)
        if (not stat.S_ISREG(temporary_metadata.st_mode) or
                temporary_metadata.st_nlink != 1 or
                temporary_metadata.st_uid != os.geteuid() or
                stat.S_IMODE(temporary_metadata.st_mode) != 0o600):
            _fail("temporary Round36 certification is unsafe")

        validate(parent / temporary)
        current_temporary = os.stat(
            temporary, dir_fd=descriptor, follow_symlinks=False)
        if (_publication_identity(current_temporary) !=
                _publication_identity(temporary_metadata)):
            _fail("temporary Round36 certification changed during validation")
        try:
            os.link(
                temporary,
                absolute.name,
                src_dir_fd=descriptor,
                dst_dir_fd=descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise Round36CertificationError(
                "Round36 output appeared concurrently") from error
        published = True
        published_inode = (
            current_temporary.st_dev, current_temporary.st_ino)
        os.unlink(temporary, dir_fd=descriptor)
        temporary = ""
        os.fsync(descriptor)
        validate(absolute)
        final = os.stat(
            absolute.name, dir_fd=descriptor, follow_symlinks=False)
        if (not stat.S_ISREG(final.st_mode) or final.st_nlink != 1 or
                final.st_uid != os.geteuid() or
                stat.S_IMODE(final.st_mode) != 0o600 or
                final.st_size != len(payload) or
                (final.st_dev, final.st_ino) != published_inode):
            _fail("published Round36 certification identity drift")
        final_capture = _capture_file(
            absolute,
            "published Round36 certification",
            limit=MAX_CERTIFICATION_BYTES,
            capture_data=True,
            require_trusted_parent=True,
            required_mode="0600",
        )
        if ((final_capture.snapshot.identity[0],
             final_capture.snapshot.identity[1]) != published_inode or
                final_capture.snapshot.data != payload):
            _fail("published Round36 certification content drift")
    except BaseException:
        if published:
            try:
                current = os.stat(
                    absolute.name, dir_fd=descriptor,
                    follow_symlinks=False)
                if (published_inode is not None and
                        (current.st_dev, current.st_ino) == published_inode):
                    os.unlink(absolute.name, dir_fd=descriptor)
                    os.fsync(descriptor)
            except (FileNotFoundError, OSError):
                pass
        raise
    finally:
        if temporary:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass
        os.close(descriptor)


def build_and_publish(
    closure_path: Path,
    artifact_root: Path,
    output: Path,
    *,
    native_aggregate_path: Path | None = None,
    receipt_inputs: ReceiptInputs | None = None,
) -> dict[str, Any]:
    report = build_certification(
        closure_path,
        artifact_root,
        native_aggregate_path=native_aggregate_path,
        receipt_inputs=receipt_inputs,
    )
    payload = canonical_json(report) + b"\n"

    # Local import avoids a module cycle: the verifier reconstructs the
    # document by calling build_certification with the report-bound paths.
    import verify_heptatrader_round36_certification as verifier

    def validate(path: Path) -> None:
        try:
            verifier.verify(path)
        except (verifier.Round36CertificationVerificationError,
                Round36CertificationError, OSError) as error:
            raise Round36CertificationError(
                "generated Round36 certification failed self-verification"
            ) from error

    _atomic_publish(output, payload, validate)
    return verifier.verify(output)


def _receipt_inputs_from_arguments(
    arguments: argparse.Namespace,
) -> ReceiptInputs | None:
    values = {
        "receipt": arguments.receipt,
        "request": arguments.request,
        "trust_policy": arguments.trust_policy,
        "index": arguments.index,
        "evidence_root": arguments.evidence_root,
        "retention_policy": arguments.retention_policy,
        "evidence_set_manifest": arguments.evidence_set_manifest,
    }
    present = {field for field, value in values.items() if value is not None}
    if not present:
        return None
    if present != set(values):
        _fail(
            "production receipt inputs are an all-or-none evidence class")
    return ReceiptInputs(**values)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="build the fail-closed Round36 final certification")
    parser.add_argument("--closure", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--native-aggregate", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--trust-policy", type=Path)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--retention-policy", type=Path)
    parser.add_argument("--evidence-set-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    receipt_inputs = _receipt_inputs_from_arguments(arguments)
    result = build_and_publish(
        arguments.closure,
        arguments.artifact_root,
        arguments.output,
        native_aggregate_path=arguments.native_aggregate,
        receipt_inputs=receipt_inputs,
    )
    print(
        "heptatrader-round36-certification: "
        f"status={result['status']} passed={str(result['passed']).lower()} "
        f"blocked={len(result['blocked_external_evidence'])}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Round36CertificationError, OSError) as error:
        print(f"heptatrader-round36-certification: FAIL {error}",
              file=sys.stderr)
        raise SystemExit(78)
