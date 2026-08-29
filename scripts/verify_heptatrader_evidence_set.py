#!/usr/bin/env python3
"""Verify a manifest-defined HeptaTrader evidence set."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
from typing import Any

import build_heptatrader_delivery_closure as delivery_closure_builder
import build_heptatrader_evidence_index as index_builder
import build_heptatrader_engineering_closure as engineering_closure_builder
import verify_heptatrader_delivery_closure as delivery_closure_verifier
import verify_heptatrader_engineering_closure as engineering_closure_verifier
import verify_heptatrader_evidence_index as index_verifier


MANIFEST_SCHEMA = "hepta.evidence-set-manifest.v2"
VERIFICATION_SCHEMA = "hepta.evidence-set-verification.v2"
PROJECT_ID = "heptatrader-agent-os"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_SEMANTIC_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ROLES = 256
ROLE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SET_ID = re.compile(
    r"^round(?P<round>[1-9][0-9]*)-certification$")
RELEASE_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,126}$")
INVENTORY_SCHEMA = "hepta.ops-inventory.v2"
INVENTORY_VERSION = 2
PROFILE = "round-closure-inventory-v2"
ENGINEERING_PROFILE = "round38-engineering-v2"
RELEASE_PROFILE = "release-validation-p0-v1"
ENGINEERING_CLOSURE_ROLE = "engineering-closure"
ENGINEERING_SUPPORT_ROLE_PREFIX = "supporting-evidence-"
ENGINEERING_ARTIFACT_DIRECTORY = re.compile(
    r"^heptatrader-round(?P<round>[1-9][0-9]*)-"
    r"engineering-artifacts-v[1-9][0-9]*$")
ENGINEERING_CLOSURE_NAME = "engineering-closure-v2.json"
ENGINEERING_CORE_ROLES = (
    ENGINEERING_CLOSURE_ROLE,
    *engineering_closure_builder.REQUIRED_ROLES,
)
DELIVERY_ARTIFACT_DIRECTORY = (
    r"heptatrader-round(?P<round>[1-9][0-9]*)-"
    r"semantic-delivery-artifacts-v[1-9][0-9]*"
)
DELIVERY_ARTIFACT_FILENAMES = {
    role: role + (".tar" if role == "strict-source-bundle" else ".json")
    for role in delivery_closure_builder.REQUIRED_ARTIFACT_ROLES
}
ROLE_PROFILES = {
    PROFILE: {
        "repository-inventory": {
            "path_pattern": re.compile(
                r"^heptatrader-round(?P<round>[1-9][0-9]*)-"
                r"(?:[a-z0-9][a-z0-9._-]*-)?inventory-v2\.json$"),
            "tier": "latest",
        },
        "round-closure": {
            "path_pattern": re.compile(
                r"^heptatrader-round(?P<round>[1-9][0-9]*)-"
                r"(?:[a-z0-9][a-z0-9._-]*-)?delivery-closure-v1\.json$"),
            "tier": "certification",
        },
        **{
            role: {
                "path_pattern": re.compile(
                    rf"^{DELIVERY_ARTIFACT_DIRECTORY}/"
                    rf"{re.escape(DELIVERY_ARTIFACT_FILENAMES[role])}$"
                ),
                "tier": "certification",
            }
            for role in delivery_closure_builder.REQUIRED_ARTIFACT_ROLES
        },
    },
    # Round38 roles are derived from the independently verified engineering
    # closure rather than from user-controlled filenames.  The empty static
    # contract is intentional; _engineering_index_roles() closes the exact
    # core role set and assigns canonical supporting-evidence roles.
    ENGINEERING_PROFILE: {},
    # Generic release-validation roles are derived from the independently
    # reconstructed P0 input manifest.  This keeps Round95+ free of the
    # Round38 rescue/ref constants while retaining an exact, closed role set.
    RELEASE_PROFILE: {},
}
SOURCE_BASELINE_FIELDS = {"path", "sha256", "size", "mode"}
INVENTORY_FIELDS = {
    "schema", "version", "project_id", "round", "release_version",
    "source_baseline", "wrapper_count", "wrapper_counts",
    "implementation_count", "implementation_test_count", "wrappers",
    "implementations", "implementation_tests",
}
WRAPPER_RECORD_FIELDS = {
    "path", "sha256", "size", "lifecycle", "python_targets",
}
IMPLEMENTATION_RECORD_FIELDS = {
    "path", "sha256", "size", "lifecycle",
}
WRAPPER_LIFECYCLES = frozenset({"canonical", "compat", "archive"})


class EvidenceSetError(RuntimeError):
    pass


def _read_json(path: Path, label: str, limit: int) -> tuple[bytes, Any]:
    try:
        _, data = index_builder.stable_bytes(path, limit)
        return data, index_builder.strict_json(data, label)
    except index_builder.EvidenceIndexError as error:
        raise EvidenceSetError(str(error)) from error


def _relative_path(value: Any, label: str) -> str:
    candidate = Path(value) if isinstance(value, str) else Path()
    if (not isinstance(value, str) or not value or "\0" in value or
            "\\" in value or candidate.is_absolute() or
            ".." in candidate.parts or candidate.as_posix() != value):
        raise EvidenceSetError(f"{label} is not a normalized relative path")
    return value


def _engineering_directory(
        relative: str, round_number: int, label: str) -> str:
    normalized = _relative_path(relative, label)
    parts = Path(normalized).parts
    if len(parts) < 2:
        raise EvidenceSetError(
            f"{label} is not inside one Round38 engineering artifact root")
    match = ENGINEERING_ARTIFACT_DIRECTORY.fullmatch(parts[0])
    if match is None or int(match.group("round")) != round_number:
        raise EvidenceSetError(
            f"{label} is outside the trusted Round38 engineering root")
    return parts[0]


def _engineering_closure_structure(
        value: Any, round_number: int,
        release_version: str) -> list[dict[str, Any]]:
    required_fields = {
        "schema", "version", "project_id", "round", "release_version",
        "generated_at", "status", "passed", "passed_scope", "source",
        "production_passed", "release_authorized",
        "artifact_roles", "artifacts", "semantic_summary",
        "safety_boundaries", "internal_open_items", "external_blockers",
    }
    if not isinstance(value, dict) or set(value) != required_fields:
        raise EvidenceSetError(
            "engineering closure fields do not exactly match schema")
    if (value["schema"] != engineering_closure_builder.SCHEMA or
            type(value["version"]) is not int or value["version"] != 2 or
            value["project_id"] != engineering_closure_builder.PROJECT_ID or
            _release_identity(
                value["round"], value["release_version"],
                "engineering closure") !=
            (round_number, release_version) or
            value["status"] != engineering_closure_builder.STATUS or
            value["passed"] is not True or
            value["passed_scope"] !=
            engineering_closure_builder.PASSED_SCOPE or
            value["production_passed"] is not False or
            value["release_authorized"] is not False or
            value["artifact_roles"] !=
            list(engineering_closure_builder.REQUIRED_ROLES) or
            value["safety_boundaries"] !=
            engineering_closure_builder.SAFETY_BOUNDARIES or
            value["external_blockers"] !=
            list(engineering_closure_builder.EXTERNAL_BLOCKERS) or
            not isinstance(value["internal_open_items"], list)):
        raise EvidenceSetError(
            "engineering closure identity or safety boundary is invalid")
    source = value["source"]
    if (not isinstance(source, dict) or
            set(source) != {
                "product_git_head", "release_git_head",
                "artifact_map_sha256"} or
            not isinstance(source.get("product_git_head"), str) or
            engineering_closure_builder.HEX40.fullmatch(
                source.get("product_git_head", "")) is None or
            not isinstance(source.get("release_git_head"), str) or
            engineering_closure_builder.HEX40.fullmatch(
                source.get("release_git_head", "")) is None or
            source["release_git_head"] == source["product_git_head"] or
            not isinstance(source.get("artifact_map_sha256"), str) or
            index_builder.HEX64.fullmatch(
                source.get("artifact_map_sha256", "")) is None):
        raise EvidenceSetError("engineering closure source identity is invalid")
    artifacts = value["artifacts"]
    if (not isinstance(artifacts, list) or
            len(artifacts) !=
            len(engineering_closure_builder.REQUIRED_ROLES)):
        raise EvidenceSetError(
            "engineering closure artifact count is invalid")
    normalized: list[dict[str, Any]] = []
    roles: list[str] = []
    paths: set[str] = set()
    for record in artifacts:
        if not isinstance(record, dict) or set(record) != {
                "role", "path", "sha256", "size", "mode"}:
            raise EvidenceSetError(
                "engineering closure artifact binding is invalid")
        role = record["role"]
        if role not in engineering_closure_builder.REQUIRED_ROLES:
            raise EvidenceSetError(
                "engineering closure artifact role is invalid")
        relative = _relative_path(
            record["path"], f"engineering closure {role} path")
        if (relative in paths or
                not isinstance(record.get("sha256"), str) or
                index_builder.HEX64.fullmatch(
                    record.get("sha256", "")) is None or
                type(record["size"]) is not int or record["size"] < 0 or
                not isinstance(record["mode"], str) or
                re.fullmatch(r"0[0-7]{3}", record["mode"]) is None or
                int(record["mode"], 8) & 0o022):
            raise EvidenceSetError(
                "engineering closure artifact metadata is invalid")
        roles.append(role)
        paths.add(relative)
        normalized.append(record)
    if roles != list(engineering_closure_builder.REQUIRED_ROLES):
        raise EvidenceSetError(
            "engineering closure artifact roles are not canonical")
    return normalized


def _engineering_index_roles(
        verified_index: dict[str, Any], evidence_root: Path,
        round_number: int, release_version: str,
) -> tuple[dict[str, str], str, dict[str, Any], bytes]:
    if round_number != 38 or not release_version.endswith("-round38"):
        raise EvidenceSetError(
            "round38-engineering-v2 is restricted to Round38")
    records = verified_index["files"]
    closure_candidates = []
    for record in records:
        relative = record["path"]
        parts = Path(relative).parts
        if len(parts) == 2 and parts[1] == ENGINEERING_CLOSURE_NAME:
            match = ENGINEERING_ARTIFACT_DIRECTORY.fullmatch(parts[0])
            if (match is not None and
                    int(match.group("round")) == round_number):
                closure_candidates.append(record)
    if len(closure_candidates) != 1:
        raise EvidenceSetError(
            "Round38 engineering index requires one canonical closure")
    closure_record = closure_candidates[0]
    if closure_record["tier"] != "certification":
        raise EvidenceSetError(
            "engineering closure is not certification evidence")
    directory = _engineering_directory(
        closure_record["path"], round_number,
        "engineering closure path")
    root = evidence_root.resolve(strict=True)
    closure_bytes, closure = _read_json(
        root / closure_record["path"], "engineering closure",
        MAX_SEMANTIC_ARTIFACT_BYTES)
    closure_artifacts = _engineering_closure_structure(
        closure, round_number, release_version)
    indexed_by_path = {record["path"]: record for record in records}
    roles_by_path = {
        closure_record["path"]: ENGINEERING_CLOSURE_ROLE,
    }
    for record in closure_artifacts:
        indexed_path = (
            Path(directory) / Path(record["path"])).as_posix()
        indexed = indexed_by_path.get(indexed_path)
        if indexed is None:
            raise EvidenceSetError(
                f"engineering artifact is absent from index: "
                f"{record['role']}")
        if indexed["tier"] != "certification":
            raise EvidenceSetError(
                f"engineering artifact is not certification evidence: "
                f"{record['role']}")
        for field in ("sha256", "size", "mode"):
            if indexed[field] != record[field]:
                raise EvidenceSetError(
                    f"engineering artifact {record['role']} {field} "
                    "binding drift")
        roles_by_path[indexed_path] = record["role"]
    if len(roles_by_path) != len(ENGINEERING_CORE_ROLES):
        raise EvidenceSetError(
            "engineering closure paths do not form a unique core role set")

    supporting_paths = []
    for record in records:
        relative = record["path"]
        if _engineering_directory(
                relative, round_number,
                "engineering indexed path") != directory:
            raise EvidenceSetError(
                "engineering evidence index spans multiple artifact roots")
        if record["tier"] != "certification":
            raise EvidenceSetError(
                "Round38 engineering evidence must use certification "
                "retention")
        if relative not in roles_by_path:
            supporting_paths.append(relative)
    if (len(ENGINEERING_CORE_ROLES) + len(supporting_paths) >
            MAX_ROLES):
        raise EvidenceSetError(
            "Round38 supporting evidence exceeds the role limit")
    for position, relative in enumerate(sorted(supporting_paths), start=1):
        roles_by_path[relative] = (
            f"{ENGINEERING_SUPPORT_ROLE_PREFIX}{position:04d}")
    return roles_by_path, directory, closure, closure_bytes


def _engineering_required_roles(roles: list[str]) -> None:
    core = set(ENGINEERING_CORE_ROLES)
    if not core.issubset(roles):
        raise EvidenceSetError(
            "Round38 engineering core evidence roles are incomplete")
    supporting = sorted(role for role in roles if role not in core)
    expected = [
        f"{ENGINEERING_SUPPORT_ROLE_PREFIX}{position:04d}"
        for position in range(1, len(supporting) + 1)
    ]
    if supporting != expected:
        raise EvidenceSetError(
            "Round38 supporting evidence roles are not contiguous")


def _release_contract():
    # Local import avoids a module cycle: the release closure defers its own
    # evidence-set/receipt imports until the external-retention phase.
    import build_heptatrader_release_validation_closure as release_contract
    return release_contract


def _release_required_roles(roles: list[str]) -> None:
    release_contract = _release_contract()
    core = set(release_contract.CORE_EVIDENCE_ROLES)
    if not core.issubset(roles):
        raise EvidenceSetError(
            "release-validation core evidence roles are incomplete")
    supporting = sorted(role for role in roles if role not in core)
    expected = [
        f"{release_contract.SUPPORTING_ROLE_PREFIX}{position:04d}"
        for position in range(1, len(supporting) + 1)
    ]
    if supporting != expected:
        raise EvidenceSetError(
            "release-validation supporting evidence roles are not contiguous")


def _release_index_roles(
        verified_index: dict[str, Any], evidence_root: Path,
        round_number: int, release_version: str,
        ) -> tuple[dict[str, str], str, dict[str, Any], bytes]:
    release_contract = _release_contract()
    root = evidence_root.resolve(strict=True)
    candidates: list[dict[str, Any]] = []
    for record in verified_index["files"]:
        parts = Path(record["path"]).parts
        if (len(parts) == 2 and
                parts[1] == release_contract.INPUT_MANIFEST_NAME):
            match = ENGINEERING_ARTIFACT_DIRECTORY.fullmatch(parts[0])
            if (match is not None and
                    int(match.group("round")) == round_number):
                candidates.append(record)
    if len(candidates) != 1:
        raise EvidenceSetError(
            "release-validation profile requires one canonical input manifest")
    manifest_record = candidates[0]
    if manifest_record["tier"] != "certification":
        raise EvidenceSetError(
            "release-validation input manifest is not certification evidence")
    directory = Path(manifest_record["path"]).parts[0]
    input_path = root / manifest_record["path"]
    try:
        local = release_contract.verify_local_input_manifest(
            input_path, root)
        roles_by_path, confirmed = release_contract.release_index_roles(
            input_path, root, verified_local=local)
    except (release_contract.ReleaseValidationError, OSError) as error:
        raise EvidenceSetError(
            "release-validation full P0 semantic verification failed") from error
    if (confirmed != local or local.get("profile") != RELEASE_PROFILE or
            local.get("round") != round_number or
            local.get("release_version") != release_version or
            local.get("artifact_directory") != directory):
        raise EvidenceSetError(
            "release-validation local identity or lineage drift")
    records_by_path = {
        record["path"]: record for record in verified_index["files"]
    }
    if set(records_by_path) != set(roles_by_path):
        raise EvidenceSetError(
            "release-validation index does not cover the exact P0 file closure")
    critical_by_path = {
        record["path"]: record for record in local["critical_files"]
    }
    if set(critical_by_path) != set(roles_by_path):
        raise EvidenceSetError(
            "release-validation critical path closure drift")
    for path, role in roles_by_path.items():
        indexed = records_by_path[path]
        critical = critical_by_path[path]
        if (indexed["tier"] != "certification" or
                critical.get("role") != role or
                any(indexed[field] != critical[field]
                    for field in ("sha256", "size", "mode"))):
            raise EvidenceSetError(
                f"release-validation indexed evidence binding drift: {role}")
        if Path(path).parts[0] != directory:
            raise EvidenceSetError(
                "release-validation index spans multiple artifact roots")
    required_roles = sorted(roles_by_path.values())
    _release_required_roles(required_roles)
    if len(required_roles) > MAX_ROLES:
        raise EvidenceSetError(
            "release-validation evidence exceeds the role limit")
    manifest_bytes, _manifest = _read_json(
        input_path, "release-validation input manifest", MAX_MANIFEST_BYTES)
    if hashlib.sha256(manifest_bytes).hexdigest() != local[
            "input_manifest_sha256"]:
        raise EvidenceSetError(
            "release-validation input manifest digest drift")
    return roles_by_path, directory, local, manifest_bytes


def _release_identity(
        round_number: Any, release_version: Any, label: str) -> tuple[int, str]:
    if type(round_number) is not int or round_number <= 0:
        raise EvidenceSetError(f"{label} round is invalid")
    if (not isinstance(release_version, str) or
            not release_version.isascii() or
            RELEASE_VERSION.fullmatch(release_version) is None or
            not release_version.endswith(f"-round{round_number}")):
        raise EvidenceSetError(f"{label} release_version is invalid")
    return round_number, release_version


def _source_baseline_binding(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != SOURCE_BASELINE_FIELDS:
        raise EvidenceSetError(
            f"{label} source-baseline binding fields are invalid")
    path = _relative_path(value["path"], f"{label} source-baseline path")
    if (not isinstance(value["sha256"], str) or
            index_builder.HEX64.fullmatch(value["sha256"]) is None or
            type(value["size"]) is not int or value["size"] < 0 or
            not isinstance(value["mode"], str) or
            re.fullmatch(r"0[0-7]{3}", value["mode"]) is None or
            int(value["mode"], 8) & 0o7022):
        raise EvidenceSetError(
            f"{label} source-baseline binding metadata is invalid")
    return {
        "path": path,
        "sha256": value["sha256"],
        "size": value["size"],
        "mode": value["mode"],
    }


def _validate_inventory(
        value: Any, round_number: int,
        release_version: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != INVENTORY_FIELDS:
        raise EvidenceSetError(
            "repository-inventory fields do not exactly match schema")
    if (value["schema"] != INVENTORY_SCHEMA or
            type(value["version"]) is not int or
            value["version"] != INVENTORY_VERSION or
            value["project_id"] != PROJECT_ID):
        raise EvidenceSetError("unsupported repository-inventory schema")
    identity = _release_identity(
        value["round"], value["release_version"], "repository-inventory")
    if identity != (round_number, release_version):
        raise EvidenceSetError(
            "repository-inventory release identity does not match manifest")

    count_fields = (
        "wrapper_count", "implementation_count",
        "implementation_test_count")
    if any(type(value[field]) is not int or value[field] < 0
           for field in count_fields):
        raise EvidenceSetError("repository-inventory counts are invalid")
    wrapper_counts = value["wrapper_counts"]
    if (not isinstance(wrapper_counts, dict) or
            set(wrapper_counts) != {"canonical", "compat", "archive"} or
            any(type(wrapper_counts[field]) is not int or
                wrapper_counts[field] < 0 for field in wrapper_counts) or
            sum(wrapper_counts.values()) != value["wrapper_count"]):
        raise EvidenceSetError(
            "repository-inventory wrapper counts are invalid")
    collection_counts = (
        ("wrappers", "wrapper_count"),
        ("implementations", "implementation_count"),
        ("implementation_tests", "implementation_test_count"),
    )
    for collection, count in collection_counts:
        records = value[collection]
        if (not isinstance(records, list) or len(records) != value[count] or
                any(not isinstance(record, dict) for record in records)):
            raise EvidenceSetError(
                f"repository-inventory {collection} closure is invalid")

    all_paths: set[str] = set()
    lifecycle_counts = {
        lifecycle: 0 for lifecycle in WRAPPER_LIFECYCLES
    }
    wrapper_paths: list[str] = []
    for record in value["wrappers"]:
        if set(record) != WRAPPER_RECORD_FIELDS:
            raise EvidenceSetError(
                "repository-inventory wrapper record fields are invalid")
        path = _relative_path(
            record["path"], "repository-inventory wrapper path")
        if (path in all_paths or
                not isinstance(record["sha256"], str) or
                index_builder.HEX64.fullmatch(record["sha256"]) is None or
                type(record["size"]) is not int or record["size"] < 0 or
                not isinstance(record["lifecycle"], str) or
                record["lifecycle"] not in WRAPPER_LIFECYCLES):
            raise EvidenceSetError(
                "repository-inventory wrapper record metadata is invalid")
        targets = record["python_targets"]
        if (not isinstance(targets, list) or
                any(not isinstance(target, str) or not target or
                    "\0" in target or "\\" in target or
                    Path(target).name != target or
                    not target.endswith(".py")
                    for target in targets) or
                targets != sorted(set(targets))):
            raise EvidenceSetError(
                "repository-inventory wrapper python_targets are invalid")
        lifecycle = record["lifecycle"]
        if lifecycle == "canonical":
            if (not path.startswith("compat/hepta-ops-generated/") or
                    not path.endswith(".sh") or
                    targets != ["hepta_ops.py"]):
                raise EvidenceSetError(
                    "repository-inventory canonical wrapper is invalid")
        elif "/" in path or not path.endswith((".sh", ".ps1")):
            raise EvidenceSetError(
                "repository-inventory compatibility wrapper is invalid")
        all_paths.add(path)
        wrapper_paths.append(path)
        lifecycle_counts[lifecycle] += 1
    if (wrapper_paths != sorted(wrapper_paths) or
            lifecycle_counts != wrapper_counts):
        raise EvidenceSetError(
            "repository-inventory wrapper record closure is invalid")

    collection_contracts = (
        ("implementations", "scripts/openclaw_fx_", "compat"),
        ("implementation_tests", "scripts/test_openclaw_fx_", "archive"),
    )
    for collection, prefix, lifecycle in collection_contracts:
        paths: list[str] = []
        for record in value[collection]:
            if set(record) != IMPLEMENTATION_RECORD_FIELDS:
                raise EvidenceSetError(
                    f"repository-inventory {collection} record fields "
                    "are invalid")
            path = _relative_path(
                record["path"],
                f"repository-inventory {collection} path")
            if (path in all_paths or not path.startswith(prefix) or
                    not path.endswith(".py") or
                    not isinstance(record["sha256"], str) or
                    index_builder.HEX64.fullmatch(
                        record["sha256"]) is None or
                    type(record["size"]) is not int or record["size"] < 0 or
                    not isinstance(record["lifecycle"], str) or
                    record["lifecycle"] != lifecycle):
                raise EvidenceSetError(
                    f"repository-inventory {collection} record metadata "
                    "is invalid")
            all_paths.add(path)
            paths.append(path)
        if paths != sorted(paths):
            raise EvidenceSetError(
                f"repository-inventory {collection} records are not "
                "canonical")
    return _source_baseline_binding(
        value["source_baseline"], "repository-inventory")


def _validate_delivery_closure(
        value: Any, round_number: int,
        release_version: str) -> dict[str, Any]:
    try:
        closure = delivery_closure_builder.validate_contract_structure(value)
    except delivery_closure_builder.DeliveryClosureError as error:
        raise EvidenceSetError(
            "round-closure is not a valid delivery-closure.v1") from error
    if (closure["round"], closure["release_version"]) != (
            round_number, release_version):
        raise EvidenceSetError(
            "round-closure release identity does not match manifest")
    source_records = [
        artifact for artifact in closure["artifacts"]
        if artifact["role"] == "source-baseline-manifest"
    ]
    if len(source_records) != 1:
        raise EvidenceSetError(
            "round-closure source-baseline lineage is not unique")
    source = source_records[0]
    return _source_baseline_binding(
        {field: source[field] for field in SOURCE_BASELINE_FIELDS},
        "round-closure")


def _delivery_artifact_root(
        manifest: dict[str, Any], closure: dict[str, Any],
        evidence_root: Path) -> Path:
    artifacts_by_role = {
        artifact["role"]: artifact for artifact in manifest["artifacts"]
    }
    closure_by_role = {
        artifact["role"]: artifact for artifact in closure["artifacts"]
    }
    roots: set[tuple[str, ...]] = set()
    for role in delivery_closure_builder.REQUIRED_ARTIFACT_ROLES:
        indexed = artifacts_by_role[role]
        bound = closure_by_role[role]
        indexed_parts = Path(indexed["path"]).parts
        bound_parts = Path(bound["path"]).parts
        if (len(indexed_parts) <= len(bound_parts) or
                indexed_parts[-len(bound_parts):] != bound_parts):
            raise EvidenceSetError(
                f"delivery artifact {role} is not rooted at its indexed path")
        roots.add(indexed_parts[:-len(bound_parts)])
        for field in ("sha256", "size", "mode"):
            if indexed[field] != bound[field]:
                raise EvidenceSetError(
                    f"delivery artifact {role} {field} binding drift")
    if len(roots) != 1:
        raise EvidenceSetError(
            "delivery artifact paths do not derive one unique artifact root")
    relative_root = Path(*next(iter(roots)))
    root = evidence_root.resolve(strict=True)
    artifact_root = root / relative_root
    if (artifact_root.resolve(strict=True) != artifact_root or
            artifact_root.parent != root):
        raise EvidenceSetError(
            "derived delivery artifact root is not a canonical direct child "
            "of the evidence root")
    return artifact_root


def _load_manifest(path: Path) -> tuple[bytes, dict[str, Any]]:
    data, manifest = _read_json(
        path, "evidence-set manifest", MAX_MANIFEST_BYTES)
    fields = {
        "schema", "version", "project_id", "round", "release_version",
        "evidence_set_id", "profile", "coverage", "index", "required_roles",
        "source_files_deleted", "source_removal_authorized",
        "paper_authorized", "live_authorized", "artifacts",
    }
    if not isinstance(manifest, dict) or set(manifest) != fields:
        raise EvidenceSetError(
            "evidence-set manifest fields do not exactly match schema")
    if (manifest["schema"] != MANIFEST_SCHEMA or
            type(manifest["version"]) is not int or
            manifest["version"] != 2 or
            manifest["project_id"] != PROJECT_ID or
            not isinstance(manifest["evidence_set_id"], str) or
            SET_ID.fullmatch(manifest["evidence_set_id"]) is None or
            not isinstance(manifest["profile"], str) or
            manifest["profile"] not in ROLE_PROFILES or
            not isinstance(manifest["coverage"], str) or
            manifest["coverage"] not in {
                "manifest-defined", "full-index-eligible-tree"}):
        raise EvidenceSetError("unsupported evidence-set manifest")
    round_number, release_version = _release_identity(
        manifest["round"], manifest["release_version"],
        "evidence-set manifest")
    set_id = SET_ID.fullmatch(manifest["evidence_set_id"])
    if (set_id is None or int(set_id.group("round")) != round_number):
        raise EvidenceSetError(
            "evidence_set_id does not match the manifest round")
    if (manifest["source_files_deleted"] is not False or
            manifest["source_removal_authorized"] is not False or
            manifest["paper_authorized"] is not False or
            manifest["live_authorized"] is not False):
        raise EvidenceSetError("evidence-set safety boundary drift")

    index_binding = manifest["index"]
    if not isinstance(index_binding, dict) or set(index_binding) != {
            "sha256", "records_sha256", "selection_mode"}:
        raise EvidenceSetError("evidence-set index binding is invalid")
    if (not isinstance(index_binding["sha256"], str) or
            index_builder.HEX64.fullmatch(index_binding["sha256"]) is None or
            not isinstance(index_binding["records_sha256"], str) or
            index_builder.HEX64.fullmatch(
                index_binding["records_sha256"]) is None or
            not isinstance(index_binding["selection_mode"], str) or
            index_binding["selection_mode"] not in {
                "explicit", "complete-tree"}):
        raise EvidenceSetError("evidence-set index binding is invalid")

    required_roles = manifest["required_roles"]
    if (not isinstance(required_roles, list) or not required_roles or
            len(required_roles) > MAX_ROLES or
            any(not isinstance(role, str) or ROLE.fullmatch(role) is None
                for role in required_roles) or
            required_roles != sorted(set(required_roles))):
        raise EvidenceSetError(
            "required evidence roles must be fixed, unique, and canonical")
    engineering_profile = manifest["profile"] == ENGINEERING_PROFILE
    release_profile = manifest["profile"] == RELEASE_PROFILE
    role_contracts = ROLE_PROFILES[manifest["profile"]]
    if engineering_profile:
        if round_number != 38:
            raise EvidenceSetError(
                "round38-engineering-v2 is restricted to Round38")
        _engineering_required_roles(required_roles)
    elif release_profile:
        _release_required_roles(required_roles)
    elif required_roles != sorted(role_contracts):
        raise EvidenceSetError(
            "required evidence roles do not match the trusted profile")

    artifacts = manifest["artifacts"]
    if (not isinstance(artifacts, list) or
            len(artifacts) != len(required_roles)):
        raise EvidenceSetError(
            "evidence role and artifact counts do not match")
    artifact_roles: list[str] = []
    paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {
                "role", "path", "sha256", "size", "mode", "tier"}:
            raise EvidenceSetError("invalid evidence role binding")
        role = artifact["role"]
        if not isinstance(role, str) or ROLE.fullmatch(role) is None:
            raise EvidenceSetError("invalid evidence role")
        dynamic_profile = engineering_profile or release_profile
        if ((dynamic_profile and role not in required_roles) or
                (not dynamic_profile and role not in role_contracts)):
            raise EvidenceSetError(
                "artifact role is not present in the trusted profile")
        artifact_roles.append(role)
        relative = _relative_path(artifact["path"], "evidence artifact path")
        if relative in paths:
            raise EvidenceSetError(
                "one evidence path cannot satisfy multiple roles")
        paths.add(relative)
        if (not isinstance(artifact["sha256"], str) or
                index_builder.HEX64.fullmatch(artifact["sha256"]) is None or
                not isinstance(artifact["size"], int) or
                isinstance(artifact["size"], bool) or artifact["size"] < 0 or
                not isinstance(artifact["mode"], str) or
                re.fullmatch(r"0[0-7]{3}", artifact["mode"]) is None or
                int(artifact["mode"], 8) & 0o022 or
                not isinstance(artifact["tier"], str) or
                artifact["tier"] not in index_builder.EXPECTED_TIERS):
            raise EvidenceSetError("invalid evidence content binding")
        if engineering_profile:
            directory = _engineering_directory(
                relative, round_number,
                f"engineering evidence role {role} path")
            if (artifact["tier"] != "certification" or
                    (role == ENGINEERING_CLOSURE_ROLE and
                     relative !=
                     f"{directory}/{ENGINEERING_CLOSURE_NAME}")):
                raise EvidenceSetError(
                    f"evidence role {role} violates its trusted "
                    "path/tier contract")
        elif release_profile:
            directory = _engineering_directory(
                relative, round_number,
                f"release-validation evidence role {role} path")
            release_contract = _release_contract()
            if (artifact["tier"] != "certification" or
                    (role == "release-input-manifest" and
                     relative !=
                     f"{directory}/{release_contract.INPUT_MANIFEST_NAME}")):
                raise EvidenceSetError(
                    f"evidence role {role} violates its trusted "
                    "path/tier contract")
        else:
            contract = role_contracts[role]
            path_match = contract["path_pattern"].fullmatch(relative)
            if (path_match is None or
                    int(path_match.group("round")) != round_number or
                    artifact["tier"] != contract["tier"]):
                raise EvidenceSetError(
                    f"evidence role {role} violates its trusted "
                    "path/tier contract")
    if artifact_roles != required_roles:
        raise EvidenceSetError(
            "artifact roles do not exactly match required roles")
    return data, manifest


def _verify_engineering_profile(
        manifest: dict[str, Any], verified_index: dict[str, Any],
        root: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    roles_by_path, directory, closure, closure_bytes = (
        _engineering_index_roles(
            verified_index, root, manifest["round"],
            manifest["release_version"]))
    manifest_roles_by_path = {
        artifact["path"]: artifact["role"]
        for artifact in manifest["artifacts"]
    }
    if manifest_roles_by_path != roles_by_path:
        raise EvidenceSetError(
            "Round38 engineering evidence role assignment drift")
    artifact_root = root / directory
    if (artifact_root.resolve(strict=True) != artifact_root or
            artifact_root.parent != root):
        raise EvidenceSetError(
            "engineering artifact root is not a canonical direct child")
    closure_path = artifact_root / ENGINEERING_CLOSURE_NAME
    try:
        engineering_report = engineering_closure_verifier.verify(
            closure_path, artifact_root)
    except (engineering_closure_verifier.VerificationError,
            OSError) as error:
        raise EvidenceSetError(
            "engineering closure full semantic verification failed") from error
    if (not isinstance(engineering_report, dict) or
            set(engineering_report) != {
                "schema", "passed", "round", "release_version", "status",
                "product_git_head", "release_git_head",
                "artifact_count", "semantic_summary"} or
            engineering_report["schema"] !=
            "heptatrader.engineering-closure-verification.v2" or
            engineering_report["passed"] is not True or
            engineering_report["round"] != manifest["round"] or
            engineering_report["release_version"] !=
            manifest["release_version"] or
            engineering_report["status"] !=
            engineering_closure_builder.STATUS or
            engineering_report["product_git_head"] !=
            closure["source"]["product_git_head"] or
            engineering_report["release_git_head"] !=
            closure["source"]["release_git_head"] or
            engineering_report["artifact_count"] !=
            len(engineering_closure_builder.REQUIRED_ROLES) or
            engineering_report["semantic_summary"] !=
            closure["semantic_summary"]):
        raise EvidenceSetError(
            "engineering closure semantic verification identity drift")

    artifacts_by_role = {
        artifact["role"]: artifact for artifact in manifest["artifacts"]
    }
    map_artifact = artifacts_by_role["engineering-artifact-map"]
    map_path = root / map_artifact["path"]
    try:
        artifact_map, map_bytes = (
            engineering_closure_builder.load_artifact_map(map_path))
    except engineering_closure_builder.EngineeringClosureError as error:
        raise EvidenceSetError(
            "engineering artifact map is invalid") from error
    source = closure["source"]
    if (artifact_map["round"] != manifest["round"] or
            artifact_map["release_version"] !=
            manifest["release_version"] or
            artifact_map["git_head"] != source["product_git_head"] or
            hashlib.sha256(map_bytes).hexdigest() !=
            source["artifact_map_sha256"] or
            map_artifact["sha256"] != source["artifact_map_sha256"]):
        raise EvidenceSetError(
            "engineering artifact map release/source identity drift")
    closure_artifacts = _engineering_closure_structure(
        closure, manifest["round"], manifest["release_version"])
    expected_map_records = [
        {"role": record["role"], "path": record["path"]}
        for record in closure_artifacts
    ]
    if artifact_map["artifacts"] != expected_map_records:
        raise EvidenceSetError(
            "engineering artifact map and closure role/path bindings differ")
    for record in closure_artifacts:
        indexed = artifacts_by_role[record["role"]]
        expected_path = (
            Path(directory) / Path(record["path"])).as_posix()
        if indexed["path"] != expected_path:
            raise EvidenceSetError(
                f"engineering artifact {record['role']} path binding drift")
        for field in ("sha256", "size", "mode"):
            if indexed[field] != record[field]:
                raise EvidenceSetError(
                    f"engineering artifact {record['role']} {field} "
                    "binding drift")
    return ({
        "engineering_artifact_root": directory,
        "product_git_head": source["product_git_head"],
        "release_git_head": source["release_git_head"],
        "production_passed": False,
        "release_authorized": False,
        "internal_open_items": closure["internal_open_items"],
    }, {
        ENGINEERING_CLOSURE_ROLE: closure_bytes,
        "engineering-artifact-map": map_bytes,
    })


def _verify_release_profile(
        manifest: dict[str, Any], verified_index: dict[str, Any],
        root: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    roles_by_path, directory, local, input_manifest_bytes = (
        _release_index_roles(
            verified_index, root, manifest["round"],
            manifest["release_version"]))
    manifest_roles_by_path = {
        artifact["path"]: artifact["role"]
        for artifact in manifest["artifacts"]
    }
    if manifest_roles_by_path != roles_by_path:
        raise EvidenceSetError(
            "release-validation evidence role assignment drift")
    artifacts_by_path = {
        artifact["path"]: artifact for artifact in manifest["artifacts"]
    }
    for critical in local["critical_files"]:
        artifact = artifacts_by_path.get(critical["path"])
        if artifact is None or artifact["role"] != critical["role"]:
            raise EvidenceSetError(
                "release-validation critical evidence role is absent")
        for field in ("sha256", "size", "mode"):
            if artifact[field] != critical[field]:
                raise EvidenceSetError(
                    f"release-validation {critical['role']} {field} "
                    "binding drift")
    release_contract = _release_contract()
    input_path = (
        root / directory / release_contract.INPUT_MANIFEST_NAME).as_posix()
    input_artifact = next(
        item for item in manifest["artifacts"]
        if item["role"] == "release-input-manifest")
    if (Path(input_path).resolve(strict=True) !=
            (root / input_artifact["path"]).resolve(strict=True) or
            input_artifact["sha256"] != local["input_manifest_sha256"]):
        raise EvidenceSetError(
            "release-validation input manifest binding drift")
    return ({
        "release_artifact_root": directory,
        "source_baseline": local["source_baseline"],
        "source_lineage": local["source_lineage"],
        "verification_fresh_until":
            local["verification"]["fresh_until"],
        "four_soaks_eight_rounds_verified":
            local["delivery"]["four_soaks_eight_rounds_verified"],
        "native_distinct_vms": local["native"]["distinct_native_vms"],
        "release_authorized": False,
    }, {
        "release-input-manifest": input_manifest_bytes,
    })


def verify(
        manifest_path: Path, index_path: Path, evidence_root: Path,
        policy_path: Path) -> dict[str, Any]:
    manifest_bytes, manifest = _load_manifest(manifest_path)
    index_bytes, captured_index = _read_json(
        index_path, "evidence index", index_builder.MAX_POLICY_BYTES * 16)
    try:
        verified_index = index_verifier.verify(
            index_path, evidence_root, policy_path, verify_files=True)
    except index_builder.EvidenceIndexError as error:
        raise EvidenceSetError(str(error)) from error
    confirmed_index_bytes, _ = _read_json(
        index_path, "evidence index", index_builder.MAX_POLICY_BYTES * 16)
    if (verified_index != captured_index or
            confirmed_index_bytes != index_bytes):
        raise EvidenceSetError(
            "evidence index changed across set verification")

    index_binding = manifest["index"]
    if (index_binding["sha256"] != hashlib.sha256(index_bytes).hexdigest() or
            index_binding["records_sha256"] !=
            verified_index["records_sha256"] or
            index_binding["selection_mode"] !=
            verified_index["selection_mode"]):
        raise EvidenceSetError("evidence-set index binding drift")

    if verified_index["selection_mode"] == "explicit":
        if manifest["coverage"] != "manifest-defined":
            raise EvidenceSetError(
                "an explicit evidence index cannot claim full-tree coverage")
    elif manifest["coverage"] != "full-index-eligible-tree":
        raise EvidenceSetError(
            "a complete-tree evidence index must declare "
            "full-index-eligible-tree coverage")

    indexed_by_path = {
        record["path"]: record for record in verified_index["files"]
    }
    manifest_paths = {
        artifact["path"] for artifact in manifest["artifacts"]
    }
    if manifest_paths != set(indexed_by_path):
        raise EvidenceSetError(
            "evidence-set roles do not cover the exact indexed path set")
    for artifact in manifest["artifacts"]:
        indexed = indexed_by_path[artifact["path"]]
        for field in ("path", "sha256", "size", "mode", "tier"):
            if artifact[field] != indexed[field]:
                raise EvidenceSetError(
                    f"evidence role {artifact['role']} {field} binding drift")

    root = evidence_root.resolve(strict=True)
    if manifest["profile"] == ENGINEERING_PROFILE:
        semantic_summary, semantic_bytes = _verify_engineering_profile(
            manifest, verified_index, root)
    elif manifest["profile"] == RELEASE_PROFILE:
        semantic_summary, semantic_bytes = _verify_release_profile(
            manifest, verified_index, root)
    else:
        semantic_documents: dict[str, Any] = {}
        semantic_bytes = {}
        for artifact in (
                next(item for item in manifest["artifacts"]
                     if item["role"] == role)
                for role in ("repository-inventory", "round-closure")):
            contents, document = _read_json(
                root / artifact["path"],
                f"{artifact['role']} semantic artifact",
                MAX_SEMANTIC_ARTIFACT_BYTES)
            semantic_bytes[artifact["role"]] = contents
            semantic_documents[artifact["role"]] = document
        inventory_lineage = _validate_inventory(
            semantic_documents["repository-inventory"],
            manifest["round"], manifest["release_version"])
        closure_lineage = _validate_delivery_closure(
            semantic_documents["round-closure"],
            manifest["round"], manifest["release_version"])
        if inventory_lineage != closure_lineage:
            raise EvidenceSetError(
                "inventory and closure source-baseline lineage differ")
        artifact_root = _delivery_artifact_root(
            manifest, semantic_documents["round-closure"], root)
        closure_path = root / next(
            artifact["path"] for artifact in manifest["artifacts"]
            if artifact["role"] == "round-closure")
        try:
            delivery_report = delivery_closure_verifier.verify(
                closure_path, artifact_root)
        except (delivery_closure_verifier.DeliveryClosureVerificationError,
                OSError) as error:
            raise EvidenceSetError(
                "round-closure full delivery verification failed") from error
        if (delivery_report["round"] != manifest["round"] or
                delivery_report["release_version"] !=
                manifest["release_version"] or
                delivery_report["artifact_roles"] !=
                list(delivery_closure_builder.REQUIRED_ARTIFACT_ROLES) or
                delivery_report["closure_sha256"] != hashlib.sha256(
                    semantic_bytes["round-closure"]).hexdigest()):
            raise EvidenceSetError(
                "round-closure full delivery verification identity drift")
        semantic_summary = {
            "delivery_artifact_root":
                artifact_root.relative_to(root).as_posix(),
            "source_baseline": inventory_lineage,
        }

    try:
        final_index = index_verifier.verify(
            index_path, evidence_root, policy_path, verify_files=True)
    except index_builder.EvidenceIndexError as error:
        raise EvidenceSetError(
            "evidence payload/tree changed before set verification "
            "completed") from error
    final_index_bytes, _ = _read_json(
        index_path, "evidence index", index_builder.MAX_POLICY_BYTES * 16)
    final_manifest_bytes, _ = _read_json(
        manifest_path, "evidence-set manifest", MAX_MANIFEST_BYTES)
    if (final_index != verified_index or final_index_bytes != index_bytes or
            final_manifest_bytes != manifest_bytes):
        raise EvidenceSetError(
            "evidence manifest/index changed before verification completed")
    for role, expected_bytes in semantic_bytes.items():
        artifact = next(
            item for item in manifest["artifacts"]
            if item["role"] == role)
        final_semantic_bytes, _ = _read_json(
            root / artifact["path"],
            f"{role} semantic artifact",
            MAX_SEMANTIC_ARTIFACT_BYTES)
        if final_semantic_bytes != expected_bytes:
            raise EvidenceSetError(
                "semantic evidence changed before verification completed")

    report = {
        "schema": VERIFICATION_SCHEMA,
        "version": 2,
        "status": "verified",
        "evidence_set_id": manifest["evidence_set_id"],
        "profile": manifest["profile"],
        "round": manifest["round"],
        "release_version": manifest["release_version"],
        "coverage": manifest["coverage"],
        "role_count": len(manifest["required_roles"]),
        "roles": manifest["required_roles"],
        "index_sha256": index_binding["sha256"],
        "index_records_sha256": index_binding["records_sha256"],
        "excluded_local_only_count":
            verified_index["excluded_local_only_count"],
        "excluded_local_only_sha256":
            verified_index["excluded_local_only_sha256"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "source_files_deleted": False,
        "source_removal_authorized": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    report.update(semantic_summary)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    resolve = lambda value: value if value.is_absolute() else root / value
    evidence_root = resolve(
        args.evidence_root or Path("runtime-logs")).resolve(strict=True)
    report = verify(
        resolve(args.manifest), resolve(args.index), evidence_root,
        root / "policies/heptatrader-evidence-retention-v1.json")
    print(
        f"PASS: evidence set {report['evidence_set_id']} "
        f"coverage={report['coverage']} roles={report['role_count']} "
        f"manifest_sha256={report['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvidenceSetError, OSError) as error:
        print(f"evidence-set: {error}", file=os.sys.stderr)
        raise SystemExit(78)
