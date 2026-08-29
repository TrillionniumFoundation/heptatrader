#!/usr/bin/env python3
"""Build a generic, non-authorizing release-validation evidence closure.

The workflow is deliberately two phase.  ``prepare`` publishes a canonical
input manifest that names every P0 evidence root/component.  The evidence-set
profile verifies that manifest, reconstructs the Release/CTest reports, and
indexes the complete transitive file closure before an external receipt is
issued.  ``close`` accepts only a production-trust, signed, currently retained
receipt for that exact evidence set and emits a PAPER-testing *admission
candidate* decision.  Neither phase grants trading or mutation authority.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
from typing import Any, Callable, Mapping, Optional


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import aggregate_hepta_execution_native_systemd_gate as native_aggregate  # noqa: E402
import build_heptatrader_delivery_closure as common  # noqa: E402
import build_heptatrader_verification_evidence as verification  # noqa: E402
import verify_heptatrader_agent_os_source_bundle as agent_source_verifier  # noqa: E402
import verify_heptatrader_clean_source_bundle as source_verifier  # noqa: E402
import verify_heptatrader_delivery_closure as delivery_verifier  # noqa: E402
import verify_heptatrader_runtime_package as runtime_verifier  # noqa: E402


INPUT_MANIFEST_SCHEMA = "heptatrader.release-validation-input-manifest.v1"
INPUT_MANIFEST_VERSION = 1
SCHEMA = "heptatrader.release-validation-closure.v1"
VERSION = 1
VERIFICATION_SCHEMA = "heptatrader.release-validation-closure-verification.v1"
PROJECT_ID = common.PROJECT_ID
PROFILE = "release-validation-p0-v1"
DECISION = "GO"
CANDIDATE_SCOPE = "paper-testing-admission-candidate-only"
MAX_INPUT_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_CLOSURE_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = common.MAX_ARTIFACT_BYTES
MAX_VERIFICATION_AGE_SECONDS = 24 * 60 * 60
MAX_CLOCK_SKEW_SECONDS = 5 * 60
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = common.HEX64
ARTIFACT_DIRECTORY = re.compile(
    r"^heptatrader-round(?P<round>[1-9][0-9]*)-"
    r"engineering-artifacts-v[1-9][0-9]*$")
INPUT_MANIFEST_NAME = "release-validation-input-manifest-v1.json"

ROOT_ROLES = (
    "delivery-artifact-root",
    "verification-artifact-root",
)
COMPONENT_ROLES = (
    "agent-os-source-bundle",
    "agent-os-source-manifest",
    "agent-os-source-policy",
    "delivery-closure",
    "native-runtime-aggregate",
    "runner-identity-report",
    "runtime-package",
    "runtime-package-manifest",
    "test-matrix-report",
)
CORE_EVIDENCE_ROLES = (
    "release-input-manifest",
    "round-closure",
    *common.REQUIRED_ARTIFACT_ROLES,
    "agent-os-source-bundle",
    "agent-os-source-manifest",
    "agent-os-source-policy",
    "runtime-package",
    "runtime-package-manifest",
    "test-matrix-report",
    "runner-identity-report",
    "native-runtime-aggregate",
    "native-variant-report-real",
    "native-variant-report-sandbox",
    "native-variant-report-stub",
    "native-instance-receipt-real",
    "native-instance-receipt-sandbox",
    "native-instance-receipt-stub",
)
SUPPORTING_ROLE_PREFIX = "supporting-evidence-"

SAFETY_BOUNDARIES = {
    "broker_connection_performed": False,
    "direct_broker_access_authorized": False,
    "live_authorized": False,
    "mutation_authorized": False,
    "mutation_performed": False,
    "order_placement_authorized": False,
    "order_placement_performed": False,
    "paper_authorized": False,
    "release_authorized": False,
    "source_files_deleted": False,
    "source_removal_authorized": False,
}


class ReleaseValidationError(RuntimeError):
    """A release-validation input, lineage, or publication failed closed."""


def _fail(message: str) -> None:
    raise ReleaseValidationError(message)


@dataclass(frozen=True)
class CapturedFile:
    path: Path
    snapshot: common.StableRead
    limit: int
    require_trusted_parent: bool
    capture_data: bool

    @property
    def binding(self) -> dict[str, Any]:
        return {
            "path": self.path.as_posix(),
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
    evidence_set_manifest: Path
    retention_policy: Path


def canonical_json(value: Any) -> bytes:
    try:
        return common.canonical_json(value)
    except common.DeliveryClosureError as error:
        raise ReleaseValidationError(
            "release-validation document is not canonical JSON data") from error


def _normalized_time(value: str, label: str) -> str:
    try:
        return common._normalize_generated_at(value)
    except (TypeError, common.DeliveryClosureError) as error:
        raise ReleaseValidationError(
            f"{label} is not normalized UTC RFC3339") from error


def _parse_time(value: str, label: str) -> datetime:
    normalized = _normalized_time(value, label)
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:  # pragma: no cover - guarded by normalization
        raise ReleaseValidationError(f"{label} is invalid") from error


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_directory(path: Path, label: str) -> Path:
    try:
        lexical = Path(os.path.abspath(os.fspath(path)))
        resolved = lexical.resolve(strict=True)
        metadata = resolved.lstat()
    except (OSError, TypeError, ValueError) as error:
        raise ReleaseValidationError(f"{label} is unavailable") from error
    if (lexical != resolved or not stat.S_ISDIR(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) & 0o022 or
            stat.S_IMODE(metadata.st_mode) & 0o7000):
        _fail(f"{label} is not a canonical protected directory")
    return resolved


def _canonical_file(path: Path, label: str) -> Path:
    try:
        lexical = Path(os.path.abspath(os.fspath(path)))
        resolved = lexical.resolve(strict=True)
        metadata = resolved.lstat()
    except (OSError, TypeError, ValueError) as error:
        raise ReleaseValidationError(f"{label} is unavailable") from error
    if lexical != resolved or not stat.S_ISREG(metadata.st_mode):
        _fail(f"{label} is not a canonical regular file")
    return resolved


def _capture_file(
        path: Path, label: str, *, limit: int = MAX_JSON_BYTES,
        require_trusted_parent: bool = True,
        capture_data: bool = True) -> CapturedFile:
    canonical = _canonical_file(path, label)
    try:
        snapshot = common.stable_read(
            canonical, limit=limit, capture=capture_data,
            require_trusted_parent=require_trusted_parent)
    except common.DeliveryClosureError as error:
        raise ReleaseValidationError(
            f"{label} failed stable read: {error}") from error
    if capture_data and snapshot.data is None:
        _fail(f"{label} contents were not captured")
    return CapturedFile(
        canonical, snapshot, limit, require_trusted_parent, capture_data)


def _recheck_file(captured: CapturedFile, label: str) -> None:
    confirmed = _capture_file(
        captured.path, label, limit=captured.limit,
        require_trusted_parent=captured.require_trusted_parent,
        capture_data=captured.capture_data)
    if confirmed.path != captured.path or confirmed.snapshot != captured.snapshot:
        _fail(f"{label} changed across release validation")


def _strict_document(captured: CapturedFile, label: str) -> dict[str, Any]:
    assert captured.snapshot.data is not None
    try:
        value = common.strict_json(captured.snapshot.data, label)
    except common.DeliveryClosureError as error:
        raise ReleaseValidationError(str(error)) from error
    if not isinstance(value, dict):
        _fail(f"{label} root is not an object")
    return value


def _canonical_document(captured: CapturedFile, label: str) -> dict[str, Any]:
    value = _strict_document(captured, label)
    assert captured.snapshot.data is not None
    if captured.snapshot.data != canonical_json(value) + b"\n":
        _fail(f"{label} is not canonical JSON plus one newline")
    return value


def _relative(value: Any, label: str) -> str:
    if not isinstance(value, str):
        _fail(f"{label} is not a normalized relative path")
    try:
        return common.normalized_relative_path(value, label)
    except common.DeliveryClosureError as error:
        raise ReleaseValidationError(str(error)) from error


def _inside(root: Path, path: Path, label: str) -> str:
    canonical = _canonical_file(path, label)
    try:
        relative = canonical.relative_to(root).as_posix()
    except ValueError as error:
        raise ReleaseValidationError(
            f"{label} is outside the evidence root") from error
    return _relative(relative, label)


def _resolve_relative(root: Path, relative: str, label: str) -> Path:
    normalized = _relative(relative, label)
    path = root.joinpath(*PurePosixPath(normalized).parts)
    canonical = _canonical_file(path, label)
    try:
        canonical.relative_to(root)
    except ValueError as error:  # pragma: no cover - normalization guards this
        raise ReleaseValidationError(f"{label} escapes evidence root") from error
    return canonical


def _resolve_relative_directory(
        root: Path, relative: str, label: str) -> Path:
    normalized = _relative(relative, label)
    path = root.joinpath(*PurePosixPath(normalized).parts)
    canonical = _canonical_directory(path, label)
    try:
        canonical.relative_to(root)
    except ValueError as error:
        raise ReleaseValidationError(f"{label} escapes evidence root") from error
    return canonical


def _release_identity(round_number: Any, release_version: Any) -> tuple[int, str]:
    try:
        common._validate_release_identity(
            PROJECT_ID, round_number, release_version)
    except common.DeliveryClosureError as error:
        raise ReleaseValidationError(str(error)) from error
    return round_number, release_version


def validate_input_manifest(value: Any) -> dict[str, Any]:
    fields = {
        "schema", "version", "project_id", "round", "release_version",
        "generated_at", "roots", "components", "safety_boundaries",
    }
    if not isinstance(value, dict) or set(value) != fields:
        _fail("release input-manifest fields do not exactly match schema")
    if (value["schema"] != INPUT_MANIFEST_SCHEMA or
            type(value["version"]) is not int or
            value["version"] != INPUT_MANIFEST_VERSION or
            value["project_id"] != PROJECT_ID):
        _fail("unsupported release input-manifest schema")
    round_number, release_version = _release_identity(
        value["round"], value["release_version"])
    _normalized_time(value["generated_at"], "input manifest generated_at")
    roots = value["roots"]
    if (not isinstance(roots, dict) or
            set(roots) != set(ROOT_ROLES)):
        _fail("release input-manifest root roles are incomplete")
    components = value["components"]
    if (not isinstance(components, dict) or
            set(components) != set(COMPONENT_ROLES)):
        _fail("release input-manifest component roles are incomplete")
    normalized_roots = {
        role: _relative(roots[role], f"{role} path") for role in ROOT_ROLES
    }
    normalized_components = {
        role: _relative(components[role], f"{role} path")
        for role in COMPONENT_ROLES
    }
    if len(set(normalized_roots.values())) != len(normalized_roots):
        _fail("release input-manifest root paths are not unique")
    if len(set(normalized_components.values())) != len(normalized_components):
        _fail("one release input file cannot satisfy multiple component roles")
    all_paths = [*normalized_roots.values(), *normalized_components.values()]
    first_parts = {PurePosixPath(path).parts[0] for path in all_paths}
    if len(first_parts) != 1:
        _fail("release input-manifest spans multiple artifact directories")
    artifact_directory = next(iter(first_parts))
    match = ARTIFACT_DIRECTORY.fullmatch(artifact_directory)
    if match is None or int(match.group("round")) != round_number:
        _fail("release input-manifest artifact directory round drift")
    if any(len(PurePosixPath(path).parts) < 2 for path in all_paths):
        _fail("release input paths must remain inside the artifact directory")
    boundaries = value["safety_boundaries"]
    if boundaries != SAFETY_BOUNDARIES:
        _fail("release input-manifest safety boundary drift")
    return {
        **value,
        "roots": normalized_roots,
        "components": normalized_components,
        "release_version": release_version,
    }


def build_input_manifest(
        *, round_number: int, release_version: str, generated_at: str,
        roots: Mapping[str, str], components: Mapping[str, str]) -> dict[str, Any]:
    value = {
        "schema": INPUT_MANIFEST_SCHEMA,
        "version": INPUT_MANIFEST_VERSION,
        "project_id": PROJECT_ID,
        "round": round_number,
        "release_version": release_version,
        "generated_at": _normalized_time(generated_at, "generated_at"),
        "roots": dict(roots),
        "components": dict(components),
        "safety_boundaries": dict(SAFETY_BOUNDARIES),
    }
    return validate_input_manifest(value)


def _binding_tuple(value: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    return value.get("sha256"), value.get("size"), value.get("mode")


def _report_inputs(
        root: Path, value: dict[str, Any], kind: str,
        captures: list[CapturedFile]) -> dict[str, dict[str, Any]]:
    inputs = value.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        _fail(f"{kind} verification report has no input closure")
    by_name: dict[str, dict[str, Any]] = {}
    for record in inputs:
        if (not isinstance(record, dict) or set(record) != {
                "name", "path", "sha256", "size", "mode"} or
                not isinstance(record.get("name"), str) or
                record["name"] in by_name):
            _fail(f"{kind} verification input fields are invalid")
        relative = _relative(record["path"], f"{kind} input path")
        captured = _capture_file(
            root.joinpath(*PurePosixPath(relative).parts),
            f"{kind} input {record['name']}", limit=verification.MAX_INPUT_BYTES)
        observed = {
            "name": record["name"],
            "path": relative,
            "sha256": captured.snapshot.sha256,
            "size": captured.snapshot.size,
            "mode": captured.snapshot.mode,
        }
        if observed != record:
            _fail(f"{kind} verification input binding drift: {record['name']}")
        captures.append(captured)
        by_name[record["name"]] = record
    return by_name


def _verify_report(
        path: Path, root: Path, kind: str,
        captures: list[CapturedFile]) -> tuple[dict[str, Any], CapturedFile,
                                                dict[str, dict[str, Any]]]:
    captured = _capture_file(path, f"{kind} verification report")
    value = _canonical_document(captured, f"{kind} verification report")
    fields = {
        "schema", "version", "kind", "generated_at", "passed",
        "cases", "inputs", "boundary",
    }
    if (set(value) != fields or value["schema"] != verification.SCHEMA or
            type(value["version"]) is not int or value["version"] != 2 or
            value["kind"] != kind or value["passed"] is not True or
            value["boundary"] != verification.BOUNDARY or
            not isinstance(value["cases"], list) or not value["cases"]):
        _fail(f"{kind} verification report contract is invalid")
    _normalized_time(value["generated_at"], f"{kind} generated_at")
    inputs = _report_inputs(root, value, kind, captures)
    try:
        if kind == "matrix":
            cases = value["cases"]
            if (not all(isinstance(case, dict) for case in cases) or
                    {case.get("name") for case in cases} !=
                    verification.MATRIX_LABELS):
                _fail("matrix verification case closure is invalid")
            arguments = []
            for case in cases:
                label = case["name"]
                sidecar = inputs.get(f"{label}.sidecar")
                expected = case.get("expected")
                if (sidecar is None or type(expected) is not int or expected <= 0):
                    _fail(f"matrix verification case is invalid: {label}")
                arguments.append(f"{label}={expected}={sidecar['path']}")
            rebuilt = verification.build_ctest(
                "matrix", root, arguments, value["generated_at"])
        elif kind == "runner":
            caches = []
            sources = []
            for label in sorted(verification.RUNNER_LABELS):
                cache = inputs.get(f"{label}.cmake-cache")
                if cache is None:
                    _fail("runner cache input closure is invalid")
                caches.append(f"{label}={cache['path']}")
            for label in sorted(verification.SOURCE_ATTESTATION_LABELS):
                source = inputs.get(f"{label}.source-manifest")
                if source is None:
                    _fail("runner source input closure is invalid")
                sources.append(f"{label}={source['path']}")
            rebuilt = verification.build_runner(
                root, caches, value["generated_at"], sources)
        else:  # pragma: no cover - internal callers use the two fixed kinds
            _fail(f"unsupported release verification report kind: {kind}")
    except verification.EvidenceError as error:
        raise ReleaseValidationError(
            f"{kind} verification evidence failed reconstruction: {error}") from error
    if rebuilt != value:
        _fail(f"{kind} verification report is not reproducible")
    captures.append(captured)
    return value, captured, inputs


def _verification_freshness(
        input_generated_at: str, matrix_generated_at: str,
        runner_generated_at: str, evaluation_time: datetime) -> str:
    if evaluation_time.tzinfo is None or evaluation_time.utcoffset() is None:
        _fail("evaluation_time must be timezone aware")
    evaluation_time = evaluation_time.astimezone(timezone.utc)
    manifest_time = _parse_time(input_generated_at, "input generated_at")
    matrix_time = _parse_time(matrix_generated_at, "matrix generated_at")
    runner_time = _parse_time(runner_generated_at, "runner generated_at")
    future_limit = evaluation_time + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS)
    if any(item > future_limit for item in (manifest_time, matrix_time, runner_time)):
        _fail("release verification evidence is future-dated")
    if manifest_time + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS) < max(
            matrix_time, runner_time):
        _fail("input manifest predates its verification evidence")
    oldest = min(matrix_time, runner_time)
    fresh_until = oldest + timedelta(seconds=MAX_VERIFICATION_AGE_SECONDS)
    if evaluation_time > fresh_until:
        _fail("Release/CTest verification evidence is expired")
    return fresh_until.isoformat().replace("+00:00", "Z")


def _lane_summary(
        matrix: dict[str, Any], runner: dict[str, Any],
        matrix_inputs: dict[str, dict[str, Any]],
        runner_inputs: dict[str, dict[str, Any]],
        agent_manifest_binding: dict[str, Any],
        strict_manifest_binding: dict[str, Any]) -> list[dict[str, Any]]:
    matrix_cases = {case["name"]: case for case in matrix["cases"]}
    runner_cases = {case["name"]: case for case in runner["cases"]}
    if set(runner_cases) != verification.RUNNER_LABELS:
        _fail("runner lane closure is invalid")
    summaries = []
    for label in sorted(verification.MATRIX_LABELS):
        case = matrix_cases[label]
        runner_case = runner_cases[label]
        policy = runner_case.get("cmake", {}).get("policy")
        expected_ibapi = label in verification.IBAPI_ON_LABELS
        if (case.get("passed") is not True or case.get("returncode") != 0 or
                case.get("selection") != [] or
                type(case.get("expected")) is not int or
                case["expected"] <= 0 or
                case.get("observed") != case["expected"] or
                runner_case.get("passed") is not True or
                runner_case.get("cmake", {}).get("build_type") != "Release" or
                not isinstance(policy, dict) or
                policy.get("build_testing") is not True or
                policy.get("ibapi_enabled") is not expected_ibapi or
                policy.get("legacy_enabled") is not False or
                policy.get("build_type") != "Release" or
                policy.get("sanitizer") is not None or
                policy.get("coverage") is not False):
            _fail(f"Release/full-CTest lane contract drift: {label}")
        cache_name = f"{label}.cmake-cache"
        if matrix_inputs.get(cache_name) != runner_inputs.get(cache_name):
            _fail(f"matrix/runner cache lineage drift: {label}")
        if label in verification.NO_GIT_LABELS:
            source_name = f"{label}.source-manifest"
            if (matrix_inputs.get(source_name) != runner_inputs.get(source_name) or
                    _binding_tuple(matrix_inputs.get(source_name, {})) !=
                    _binding_tuple(agent_manifest_binding)):
                _fail(f"Agent source lineage drift in Release lane: {label}")
        summaries.append({
            "name": label,
            "build_type": "Release",
            "build_testing": True,
            "ibapi_enabled": expected_ibapi,
            "expected_tests": case["expected"],
            "observed_tests": case["observed"],
            "selection": [],
            "passed": True,
        })
    for label in verification.STRICT_SOURCE_LABELS:
        source_name = f"{label}.source-manifest"
        if _binding_tuple(runner_inputs.get(source_name, {})) != _binding_tuple(
                strict_manifest_binding):
            _fail(f"strict source lineage drift in runner lane: {label}")
    return summaries


def _critical_record(
        role: str, captured: CapturedFile, evidence_root: Path) -> dict[str, Any]:
    return {
        "role": role,
        "path": _inside(evidence_root, captured.path, role),
        "sha256": captured.snapshot.sha256,
        "size": captured.snapshot.size,
        "mode": captured.snapshot.mode,
    }


def _capture_native_instance_receipt(
        raw_report: CapturedFile, parsed_native: Mapping[str, Any],
        variant: str) -> CapturedFile:
    raw_document = _strict_document(
        raw_report, f"native raw report {variant}")
    identity = raw_document.get("instance_identity")
    receipt_binding = (
        identity.get("receipt") if isinstance(identity, dict) else None)
    if (not isinstance(receipt_binding, dict) or
            set(receipt_binding) != {
                "path", "file_sha256", "body_sha256", "size", "mode",
                "device", "inode"}):
        _fail(f"native instance receipt binding is invalid: {variant}")
    receipt = _capture_file(
        Path(receipt_binding["path"]),
        f"native instance receipt {variant}")
    receipt_document = _strict_document(
        receipt, f"native instance receipt {variant}")
    variants = parsed_native.get("variants")
    aggregate_record = (
        variants.get(variant) if isinstance(variants, Mapping) else None)
    if (not isinstance(aggregate_record, Mapping) or
            receipt.snapshot.sha256 != receipt_binding["file_sha256"] or
            receipt.snapshot.size != receipt_binding["size"] or
            receipt.snapshot.mode != receipt_binding["mode"] or
            receipt_document.get("body_sha256") !=
            receipt_binding["body_sha256"] or
            receipt_binding["file_sha256"] !=
            aggregate_record.get("instance_receipt_file_sha256") or
            receipt_binding["body_sha256"] !=
            aggregate_record.get("instance_receipt_body_sha256")):
        _fail(f"native instance receipt binding drift: {variant}")
    return receipt


def _add_critical(
        by_path: dict[str, dict[str, Any]], record: dict[str, Any]) -> None:
    path = record["path"]
    previous = by_path.get(path)
    if previous is not None:
        if any(previous[field] != record[field]
               for field in ("sha256", "size", "mode")):
            _fail(f"critical evidence path has conflicting bindings: {path}")
        return
    by_path[path] = record


def _release_source_lineage(
        *, git_head: str, source_result: Mapping[str, Any],
        agent_result: Mapping[str, Any],
        runtime_result: Mapping[str, Any]) -> dict[str, str]:
    """Preserve the distinct strict-source digest domains in one lineage."""
    security_manifest = source_result.get("security_manifest_sha256")
    if (type(git_head) is not str or HEX40.fullmatch(git_head) is None or
            type(security_manifest) is not str or
            not security_manifest.startswith("sha256:") or
            HEX64.fullmatch(security_manifest.removeprefix("sha256:")) is None):
        _fail("strict source security-manifest lineage is invalid")
    result = {
        "git_head": git_head,
        "strict_source_bundle_sha256": source_result.get("bundle_sha256"),
        "strict_source_manifest_sha256": source_result.get("manifest_sha256"),
        "strict_source_security_manifest_sha256":
            security_manifest.removeprefix("sha256:"),
        "strict_source_files_sha256": source_result.get("files_sha256"),
        "agent_source_bundle_sha256": agent_result.get("bundle_sha256"),
        "runtime_package_sha256": runtime_result.get("package_sha256"),
        "runtime_package_manifest_sha256":
            runtime_result.get("manifest_sha256"),
    }
    if any(
            type(value) is not str or HEX64.fullmatch(value) is None
            for key, value in result.items() if key != "git_head"):
        _fail("release source lineage contains an invalid digest")
    return result


def verify_local_input_manifest(
        input_manifest_path: Path, evidence_root: Path, *,
        evaluation_time: datetime | None = None) -> dict[str, Any]:
    """Reconstruct the complete generic P0 local evidence closure."""
    root = _canonical_directory(evidence_root, "release evidence root")
    manifest_capture = _capture_file(
        input_manifest_path, "release input manifest",
        limit=MAX_INPUT_MANIFEST_BYTES)
    if manifest_capture.path.name != INPUT_MANIFEST_NAME:
        _fail(f"release input manifest must be named {INPUT_MANIFEST_NAME}")
    manifest = validate_input_manifest(
        _canonical_document(manifest_capture, "release input manifest"))
    artifact_directory = PurePosixPath(
        next(iter(manifest["components"].values()))).parts[0]
    artifact_root = _canonical_directory(
        root / artifact_directory, "release artifact directory")
    if artifact_root.parent != root:
        _fail("release artifact directory must be a direct evidence-root child")
    roots = {
        role: _resolve_relative_directory(root, path, role)
        for role, path in manifest["roots"].items()
    }
    components = {
        role: _resolve_relative(root, path, role)
        for role, path in manifest["components"].items()
    }
    evaluation = (evaluation_time or _utc_now()).astimezone(timezone.utc)

    captures: list[CapturedFile] = [manifest_capture]
    delivery_capture = _capture_file(
        components["delivery-closure"], "delivery closure",
        limit=common.MAX_CLOSURE_BYTES)
    delivery_document = _canonical_document(
        delivery_capture, "delivery closure")
    try:
        delivery_document = common.validate_contract_structure(
            delivery_document)
        delivery_report = delivery_verifier.verify(
            delivery_capture.path, roots["delivery-artifact-root"])
    except (common.DeliveryClosureError,
            delivery_verifier.DeliveryClosureVerificationError,
            OSError) as error:
        raise ReleaseValidationError(
            "delivery closure failed full semantic verification") from error
    if (delivery_document["round"] != manifest["round"] or
            delivery_document["release_version"] != manifest["release_version"] or
            delivery_report.get("round") != manifest["round"] or
            delivery_report.get("release_version") != manifest["release_version"] or
            delivery_report.get("passed") is not True or
            delivery_report.get("clean_checkout_certified") is not True or
            delivery_report.get("blocked_reason") is not None or
            delivery_report.get("release_authorized") is not False or
            any(delivery_report.get(field) is not False for field in (
                "broker_connection_performed", "order_placement_performed",
                "paper_authorized", "live_authorized", "real_ib_certified"))):
        _fail("delivery closure release identity or safety boundary drift")
    captures.append(delivery_capture)
    delivery_artifacts = {
        record["role"]: record for record in delivery_document["artifacts"]
    }
    delivery_artifact_captures: dict[str, CapturedFile] = {}
    for role in common.REQUIRED_ARTIFACT_ROLES:
        record = delivery_artifacts[role]
        captured = _capture_file(
            roots["delivery-artifact-root"] /
            Path(*PurePosixPath(record["path"]).parts),
            f"delivery artifact {role}", limit=MAX_ARTIFACT_BYTES,
            capture_data=(role != "strict-source-bundle"))
        if _binding_tuple(record) != _binding_tuple(captured.binding):
            _fail(f"delivery artifact binding drift: {role}")
        captures.append(captured)
        delivery_artifact_captures[role] = captured

    baseline_capture = delivery_artifact_captures["source-baseline-manifest"]
    baseline = _canonical_document(baseline_capture, "source baseline manifest")
    try:
        baseline_summary = common._validate_baseline(
            baseline, round_number=manifest["round"],
            release_version=manifest["release_version"])
    except common.DeliveryClosureError as error:
        raise ReleaseValidationError("source baseline is invalid") from error
    if (baseline_summary["clean_checkout_certified"] is not True or
            baseline_summary["blocked_reason"] is not None or
            baseline_summary["release_authorized"] is not False):
        _fail("source baseline is not a clean frozen release baseline")

    strict_bundle = delivery_artifact_captures["strict-source-bundle"]
    strict_manifest = delivery_artifact_captures[
        "strict-source-bundle-manifest"]
    try:
        source_result = source_verifier.verify_bundle(
            strict_bundle.path, strict_manifest.path)
    except (SystemExit, OSError, ValueError, RuntimeError) as error:
        raise ReleaseValidationError(
            "strict source bundle failed exact verification") from error
    strict_document = _canonical_document(
        strict_manifest, "strict source manifest")
    if (source_result.get("version") != manifest["release_version"] or
            source_result.get("git_head") != baseline_summary["git_head"] or
            strict_document.get("version") != manifest["release_version"] or
            strict_document.get("git_head") != baseline_summary["git_head"]):
        _fail("strict source and frozen baseline lineage differ")

    component_captures = {
        role: _capture_file(
            path, role,
            limit=(MAX_ARTIFACT_BYTES if role in {
                "agent-os-source-bundle", "runtime-package"} else
                MAX_JSON_BYTES),
            capture_data=role not in {
                "agent-os-source-bundle", "runtime-package"})
        for role, path in components.items()
        if role != "delivery-closure"
    }
    captures.extend(component_captures.values())
    try:
        agent_result = agent_source_verifier.verify(
            component_captures["agent-os-source-bundle"].path,
            component_captures["agent-os-source-manifest"].path,
            strict_bundle.path,
            strict_manifest.path,
            component_captures["agent-os-source-policy"].path)
    except (SystemExit, OSError, ValueError, RuntimeError) as error:
        raise ReleaseValidationError(
            "Agent OS source bundle failed strict-source verification") from error
    if agent_result.get("passed") is not True:
        _fail("Agent OS source bundle did not pass")
    try:
        runtime_result = runtime_verifier.verify_package(
            component_captures["runtime-package"].path,
            component_captures["runtime-package-manifest"].path)
    except (runtime_verifier.RuntimePackageError, OSError) as error:
        raise ReleaseValidationError(
            "runtime package failed exact verification") from error
    expected_source_ref = {
        "bundle_sha256": "sha256:" + source_result["bundle_sha256"],
        "manifest_sha256": "sha256:" + source_result["manifest_sha256"],
        "files_sha256": "sha256:" + source_result["files_sha256"],
        "security_manifest_sha256":
            source_result["security_manifest_sha256"],
        "git_head": source_result["git_head"],
    }
    runtime_source = runtime_result.get("source_ref")
    if (runtime_result.get("release_version") != manifest["release_version"] or
            not isinstance(runtime_source, dict) or
            any(runtime_source.get(field) != expected
                for field, expected in expected_source_ref.items()) or
            runtime_result.get("boundary", {}).get("paper_authorized") is not False or
            runtime_result.get("boundary", {}).get("live_authorized") is not False):
        _fail("runtime package source or authority lineage drift")

    verification_captures: list[CapturedFile] = []
    matrix, matrix_capture, matrix_inputs = _verify_report(
        component_captures["test-matrix-report"].path,
        roots["verification-artifact-root"], "matrix",
        verification_captures)
    runner, runner_capture, runner_inputs = _verify_report(
        component_captures["runner-identity-report"].path,
        roots["verification-artifact-root"], "runner",
        verification_captures)
    # The component captures and report captures must describe the same inode
    # snapshot; this also detects replacement between component resolution and
    # semantic reconstruction.
    if (matrix_capture.snapshot !=
            component_captures["test-matrix-report"].snapshot or
            runner_capture.snapshot !=
            component_captures["runner-identity-report"].snapshot):
        _fail("verification report changed before reconstruction")
    captures.extend(verification_captures)
    lanes = _lane_summary(
        matrix, runner, matrix_inputs, runner_inputs,
        component_captures["agent-os-source-manifest"].binding,
        strict_manifest.binding)
    fresh_until = _verification_freshness(
        manifest["generated_at"], matrix["generated_at"],
        runner["generated_at"], evaluation)

    native_capture = component_captures["native-runtime-aggregate"]
    native_document = _strict_document(
        native_capture, "native runtime aggregate")
    try:
        parsed_native = native_aggregate.verify_runtime_aggregate(
            native_document)
    except (native_aggregate.AggregateError, OSError) as error:
        raise ReleaseValidationError(
            "native three-VM aggregate failed raw-report reconstruction") from error
    common_native = parsed_native.get("common_closure", {})
    native_boundary = parsed_native.get("boundary", {})
    if (common_native.get("distinct_native_vms") != 3 or
            common_native.get("distinct_provisioner_attested_instances") != 3 or
            common_native.get("external_instance_receipts_verified") is not
            True or
            common_native.get("instance_receipt_validity_windows_overlap") is
            not True or
            common_native.get("clean_source_bundle_sha256") !=
            source_result["bundle_sha256"] or
            common_native.get("clean_source_manifest_sha256") !=
            source_result["manifest_sha256"] or
            common_native.get("clean_source_files_sha256") !=
            source_result["files_sha256"] or
            native_boundary.get("real_broker_connections") != 0 or
            native_boundary.get("paper_orders") != 0 or
            native_boundary.get("paper_authorized") is not False or
            native_boundary.get("live_enabled") is not False or
            native_boundary.get("real_ibapi_elf_executed") is not False):
        _fail("native aggregate source or offline authority lineage drift")

    by_path: dict[str, dict[str, Any]] = {}
    _add_critical(by_path, _critical_record(
        "release-input-manifest", manifest_capture, root))
    _add_critical(by_path, _critical_record(
        "round-closure", delivery_capture, root))
    for role in common.REQUIRED_ARTIFACT_ROLES:
        _add_critical(by_path, _critical_record(
            role, delivery_artifact_captures[role], root))
    component_role_map = {
        "agent-os-source-bundle": "agent-os-source-bundle",
        "agent-os-source-manifest": "agent-os-source-manifest",
        "agent-os-source-policy": "agent-os-source-policy",
        "runtime-package": "runtime-package",
        "runtime-package-manifest": "runtime-package-manifest",
        "test-matrix-report": "test-matrix-report",
        "runner-identity-report": "runner-identity-report",
        "native-runtime-aggregate": "native-runtime-aggregate",
    }
    for component, role in component_role_map.items():
        _add_critical(by_path, _critical_record(
            role, component_captures[component], root))

    for binding in native_aggregate.parse_variant_report_inputs(
            parsed_native["aggregation_inputs"]):
        variant = binding["variant"]
        raw = _capture_file(
            Path(binding["path"]), f"native raw report {variant}")
        if _binding_tuple(binding) != _binding_tuple(raw.binding):
            _fail(f"native raw report binding drift: {variant}")
        captures.append(raw)
        _add_critical(by_path, _critical_record(
            f"native-variant-report-{variant}", raw, root))
        receipt = _capture_native_instance_receipt(
            raw, parsed_native, variant)
        captures.append(receipt)
        _add_critical(by_path, _critical_record(
            f"native-instance-receipt-{variant}", receipt, root))

    fixed_paths = set(by_path)
    transitive: dict[str, CapturedFile] = {}
    for captured in verification_captures:
        relative = _inside(root, captured.path, "verification transitive input")
        if relative not in fixed_paths:
            prior = transitive.get(relative)
            if prior is not None and prior.snapshot != captured.snapshot:
                _fail("verification transitive input binding conflict")
            transitive[relative] = captured
    for position, relative in enumerate(sorted(transitive), start=1):
        _add_critical(by_path, _critical_record(
            f"{SUPPORTING_ROLE_PREFIX}{position:04d}",
            transitive[relative], root))

    critical = sorted(by_path.values(), key=lambda record: record["role"])
    roles = [record["role"] for record in critical]
    if (not set(CORE_EVIDENCE_ROLES).issubset(roles) or
            len(roles) != len(set(roles))):
        _fail("release critical evidence role closure is incomplete")
    if any(PurePosixPath(record["path"]).parts[0] != artifact_directory
           for record in critical):
        _fail("release critical evidence escapes its single artifact directory")
    baseline_record = delivery_artifacts["source-baseline-manifest"]
    source_baseline = {
        field: baseline_record[field]
        for field in ("path", "sha256", "size", "mode")
    }
    result = {
        "profile": PROFILE,
        "round": manifest["round"],
        "release_version": manifest["release_version"],
        "artifact_directory": artifact_directory,
        "input_manifest_sha256": manifest_capture.snapshot.sha256,
        "source_baseline": source_baseline,
        "source_lineage": _release_source_lineage(
            git_head=baseline_summary["git_head"],
            source_result=source_result, agent_result=agent_result,
            runtime_result=runtime_result),
        "verification": {
            "matrix_generated_at": matrix["generated_at"],
            "runner_generated_at": runner["generated_at"],
            "fresh_until": fresh_until,
            "maximum_age_seconds": MAX_VERIFICATION_AGE_SECONDS,
            "lanes": lanes,
        },
        "delivery": {
            "closure_sha256": delivery_report["closure_sha256"],
            "artifact_roles": list(common.REQUIRED_ARTIFACT_ROLES),
            "four_soaks_eight_rounds_verified": True,
        },
        "native": {
            "schema": native_aggregate.RUNTIME_AGGREGATE_SCHEMA,
            "certification_level": native_aggregate.RUNTIME_CERTIFICATION_LEVEL,
            "distinct_native_vms": 3,
            "distinct_provisioner_attested_instances": 3,
            "external_instance_receipts_verified": True,
            "runtime_contract_verified": True,
        },
        "critical_files": critical,
        "safety_boundaries": dict(SAFETY_BOUNDARIES),
    }
    for captured in captures:
        _recheck_file(captured, f"release evidence {captured.path.name}")
    return result


def release_index_roles(
        input_manifest_path: Path, evidence_root: Path,
        *, evaluation_time: datetime | None = None,
        verified_local: dict[str, Any] | None = None) -> tuple[
            dict[str, str], dict[str, Any]]:
    local = verified_local or verify_local_input_manifest(
        input_manifest_path, evidence_root,
        evaluation_time=evaluation_time)
    roles_by_path = {
        record["path"]: record["role"] for record in local["critical_files"]
    }
    if len(roles_by_path) != len(local["critical_files"]):
        _fail("release critical evidence paths are not unique")
    return roles_by_path, local


def _receipt_summary(
        receipt_inputs: ReceiptInputs, evidence_root: Path,
        local: dict[str, Any]) -> tuple[dict[str, Any], dict[str, CapturedFile]]:
    # Deferred imports keep the generic evidence-set verifier free of a module
    # cycle when it imports this module to validate the P0 profile.
    import verify_heptatrader_evidence_ingestion_receipt as receipt_verifier
    import verify_heptatrader_evidence_set as set_verifier

    paths = {
        "receipt": receipt_inputs.receipt,
        "request": receipt_inputs.request,
        "trust_policy": receipt_inputs.trust_policy,
        "index": receipt_inputs.index,
        "evidence_set_manifest": receipt_inputs.evidence_set_manifest,
        "retention_policy": receipt_inputs.retention_policy,
    }
    captures = {
        role: _capture_file(
            path, f"retention input {role}", limit=MAX_JSON_BYTES,
            require_trusted_parent=False)
        for role, path in paths.items()
    }
    try:
        report = receipt_verifier.verify_receipt(
            captures["receipt"].path,
            captures["request"].path,
            captures["trust_policy"].path,
            captures["index"].path,
            evidence_root,
            captures["retention_policy"].path,
            captures["evidence_set_manifest"].path,
            require_system_trust=True)
    except (receipt_verifier.IngestionReceiptError, OSError) as error:
        raise ReleaseValidationError(
            "external ingestion/retention receipt failed production trust") from error
    evidence_set = report.get("evidence_set")
    if (report.get("schema") != receipt_verifier.VERIFICATION_SCHEMA or
            report.get("version") != 2 or
            report.get("production_trust") is not True or
            report.get("trust_scope") != "system-production" or
            report.get("signature_status") != "verified" or
            report.get("retention_status") != "current-policy-satisfied" or
            report.get("evidence_set_bound") is not True or
            report.get("evidence_set_certified") is not True or
            report.get("source_files_deleted") is not False or
            report.get("source_removal_authorized") is not False or
            report.get("paper_authorized") is not False or
            report.get("live_authorized") is not False or
            not isinstance(evidence_set, dict) or
            evidence_set.get("profile") != PROFILE or
            evidence_set.get("round") != local["round"] or
            evidence_set.get("release_version") != local["release_version"] or
            evidence_set.get("source_baseline") != local["source_baseline"] or
            evidence_set.get("manifest_sha256") !=
            captures["evidence_set_manifest"].snapshot.sha256):
        _fail("production receipt release/evidence-set lineage drift")
    object_count = report.get("current_policy_satisfied_object_count")
    objects = report.get("objects")
    if (type(object_count) is not int or object_count <= 0 or
            not isinstance(objects, list) or len(objects) != object_count or
            any(not isinstance(item, dict) or
                item.get("kind") != "indefinite" or
                item.get("retain_until") is not None or
                item.get("status") != "fresh-signed-active-attestation"
                for item in objects)):
        _fail("production receipt does not prove current immutable retention")
    set_report = set_verifier.verify(
        captures["evidence_set_manifest"].path,
        captures["index"].path,
        evidence_root,
        captures["retention_policy"].path)
    if (set_report.get("profile") != PROFILE or
            set_report.get("round") != local["round"] or
            set_report.get("release_version") != local["release_version"] or
            set_report.get("source_baseline") != local["source_baseline"] or
            set_report.get("manifest_sha256") !=
            captures["evidence_set_manifest"].snapshot.sha256 or
            set_report.get("role_count") != len(local["critical_files"])):
        _fail("verified release evidence set differs from local P0 closure")
    summary = {
        "schema": receipt_verifier.VERIFICATION_SCHEMA,
        "trust_scope": "system-production",
        "signature_status": "verified",
        "retention_status": "current-policy-satisfied",
        "current_policy_satisfied_object_count": object_count,
        "statement_sha256": report["statement_sha256"],
        "request_sha256": report["request_sha256"],
        "index_sha256": report["index_sha256"],
        "evidence_set_manifest_sha256":
            report["evidence_set_manifest_sha256"],
        "trust_policy_sha256": report["trust_policy_sha256"],
        "evidence_set_id": evidence_set["evidence_set_id"],
        "profile": PROFILE,
        "role_count": set_report["role_count"],
        "production_contract_verified": True,
    }
    return summary, captures


def build_closure(
        input_manifest_path: Path, evidence_root: Path,
        receipt_inputs: ReceiptInputs, *,
        evaluated_at: datetime | None = None) -> dict[str, Any]:
    evaluation = (evaluated_at or _utc_now()).astimezone(timezone.utc)
    local = verify_local_input_manifest(
        input_manifest_path, evidence_root, evaluation_time=evaluation)
    retention, receipt_captures = _receipt_summary(
        receipt_inputs, _canonical_directory(
            evidence_root, "release evidence root"), local)
    # Receipt verification recursively reopens and semantically verifies the
    # evidence set.  One final local pass closes replacement races spanning the
    # two independent verifiers.
    confirmed = verify_local_input_manifest(
        input_manifest_path, evidence_root, evaluation_time=evaluation)
    if confirmed != local:
        _fail("local P0 evidence changed across retention verification")
    for role, captured in receipt_captures.items():
        _recheck_file(captured, f"retention input {role}")
    evaluated_text = evaluation.replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")
    closure = {
        "schema": SCHEMA,
        "version": VERSION,
        "project_id": PROJECT_ID,
        "round": local["round"],
        "release_version": local["release_version"],
        "evaluated_at": evaluated_text,
        "expires_at": local["verification"]["fresh_until"],
        "decision": DECISION,
        "passed": True,
        "candidate_scope": CANDIDATE_SCOPE,
        "local_evidence": local,
        "retention_evidence": {
            "inputs": {
                role: receipt_captures[role].binding
                for role in sorted(receipt_captures)
            },
            "evidence_root": _canonical_directory(
                evidence_root, "release evidence root").as_posix(),
            "verification": retention,
        },
        "safety_boundaries": dict(SAFETY_BOUNDARIES),
    }
    return closure


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    return value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _atomic_publish(
        output: Path, payload: bytes, *, expected_name: str,
        maximum: int, validate: Callable[[Path], None]) -> None:
    absolute = Path(os.path.abspath(output))
    if output != absolute or absolute.name != expected_name:
        _fail(f"output must be absolute and named {expected_name}")
    if len(payload) > maximum:
        _fail("release-validation output exceeds its size limit")
    parent = _canonical_directory(absolute.parent, "output parent")
    parent_metadata = parent.lstat()
    if parent_metadata.st_uid != os.geteuid():
        _fail("output parent must be caller-owned")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    directory = os.open(parent, directory_flags)
    temporary = f".{expected_name}.{os.getpid()}.{secrets.token_hex(16)}.tmp"
    published = False
    published_inode: tuple[int, int] | None = None
    try:
        if _directory_identity(os.fstat(directory)) != _directory_identity(
                parent_metadata):
            _fail("output parent changed while opening")
        try:
            os.stat(absolute.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _fail("refusing to replace an existing release-validation output")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=directory)
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    _fail("short release-validation output write")
                offset += written
            os.fsync(descriptor)
            temporary_metadata = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        validate(parent / temporary)
        current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
        if (_file_identity(current) != _file_identity(temporary_metadata) or
                not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or
                current.st_uid != os.geteuid() or
                stat.S_IMODE(current.st_mode) != 0o600):
            _fail("temporary release-validation output changed")
        try:
            os.link(
                temporary, absolute.name, src_dir_fd=directory,
                dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError as error:
            raise ReleaseValidationError(
                "release-validation output appeared concurrently") from error
        published = True
        published_inode = current.st_dev, current.st_ino
        os.unlink(temporary, dir_fd=directory)
        temporary = ""
        os.fsync(directory)
        validate(absolute)
        final = os.stat(
            absolute.name, dir_fd=directory, follow_symlinks=False)
        if (not stat.S_ISREG(final.st_mode) or final.st_nlink != 1 or
                stat.S_IMODE(final.st_mode) != 0o600 or
                (final.st_dev, final.st_ino) != published_inode or
                final.st_size != len(payload)):
            _fail("published release-validation output identity drift")
        final_capture = _capture_file(
            absolute, "published release-validation output", limit=maximum)
        if final_capture.snapshot.data != payload:
            _fail("published release-validation output content drift")
    except BaseException:
        if published and published_inode is not None:
            try:
                current = os.stat(
                    absolute.name, dir_fd=directory, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == published_inode:
                    os.unlink(absolute.name, dir_fd=directory)
                    os.fsync(directory)
            except (FileNotFoundError, OSError):
                pass
        raise
    finally:
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)


def publish_input_manifest(output: Path, value: dict[str, Any]) -> None:
    validated = validate_input_manifest(value)
    expected = (
        f"heptatrader-round{validated['round']}-engineering-artifacts-v1")
    if output.parent.name != expected:
        _fail(f"input manifest parent must be named {expected}")
    payload = canonical_json(validated) + b"\n"

    def validate(path: Path) -> None:
        captured = _capture_file(
            path, "published input manifest", limit=MAX_INPUT_MANIFEST_BYTES)
        if (validate_input_manifest(
                _canonical_document(captured, "published input manifest")) !=
                validated):
            _fail("published input manifest semantic drift")

    _atomic_publish(
        output, payload, expected_name=INPUT_MANIFEST_NAME,
        maximum=MAX_INPUT_MANIFEST_BYTES, validate=validate)


def build_and_publish_closure(
        input_manifest_path: Path, evidence_root: Path,
        receipt_inputs: ReceiptInputs, output: Path) -> dict[str, Any]:
    report = build_closure(
        input_manifest_path, evidence_root, receipt_inputs)
    expected = (
        f"heptatrader-round{report['round']}-"
        "release-validation-closure-v1.json")
    payload = canonical_json(report) + b"\n"
    import verify_heptatrader_release_validation_closure as verifier

    def validate(path: Path) -> None:
        verifier.verify(path)

    _atomic_publish(
        output, payload, expected_name=expected,
        maximum=MAX_CLOSURE_BYTES, validate=validate)
    return verifier.verify(output)


def _parse_role_path(values: list[str], expected: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            _fail("role path arguments must use ROLE=PATH")
        role, path = value.split("=", 1)
        if role in result:
            _fail(f"duplicate role path argument: {role}")
        result[role] = path
    if set(result) != set(expected):
        _fail(
            f"role path closure mismatch; missing={sorted(set(expected)-set(result))} "
            f"extra={sorted(set(result)-set(expected))}")
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="build generic fail-closed release validation evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--round", dest="round_number", type=int, required=True)
    prepare.add_argument("--release-version", required=True)
    prepare.add_argument("--generated-at")
    prepare.add_argument("--root", action="append", default=[])
    prepare.add_argument("--component", action="append", default=[])
    prepare.add_argument("--output", type=Path, required=True)

    close = subparsers.add_parser("close")
    close.add_argument("--input-manifest", type=Path, required=True)
    close.add_argument("--evidence-root", type=Path, required=True)
    close.add_argument("--receipt", type=Path, required=True)
    close.add_argument("--request", type=Path, required=True)
    close.add_argument("--trust-policy", type=Path, required=True)
    close.add_argument("--index", type=Path, required=True)
    close.add_argument("--evidence-set-manifest", type=Path, required=True)
    close.add_argument("--retention-policy", type=Path, required=True)
    close.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "prepare":
        generated_at = arguments.generated_at or _utc_now().replace(
            microsecond=0).isoformat().replace("+00:00", "Z")
        value = build_input_manifest(
            round_number=arguments.round_number,
            release_version=arguments.release_version,
            generated_at=generated_at,
            roots=_parse_role_path(arguments.root, ROOT_ROLES),
            components=_parse_role_path(
                arguments.component, COMPONENT_ROLES))
        publish_input_manifest(arguments.output, value)
        print(
            "heptatrader-release-validation: PREPARED "
            f"round={value['round']} authority=false")
        return 0
    receipt_inputs = ReceiptInputs(
        receipt=arguments.receipt,
        request=arguments.request,
        trust_policy=arguments.trust_policy,
        index=arguments.index,
        evidence_set_manifest=arguments.evidence_set_manifest,
        retention_policy=arguments.retention_policy)
    result = build_and_publish_closure(
        arguments.input_manifest, arguments.evidence_root,
        receipt_inputs, arguments.output)
    print(
        "heptatrader-release-validation: "
        f"decision={result['decision']} round={result['round']} "
        "candidate_only=true authority=false")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReleaseValidationError, OSError) as error:
        print(f"heptatrader-release-validation: FAIL {error}", file=sys.stderr)
        raise SystemExit(78)
