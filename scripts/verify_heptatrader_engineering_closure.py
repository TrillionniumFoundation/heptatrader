#!/usr/bin/env python3
"""Independently rebind and verify a local engineering closure."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from typing import Any


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_heptatrader_delivery_closure as common  # noqa: E402
import build_heptatrader_engineering_closure as builder  # noqa: E402


class VerificationError(RuntimeError):
    """The closure or a bound artifact failed independent verification."""


def verify(path: Path, artifact_root: Path) -> dict[str, Any]:
    try:
        snapshot = common.stable_read(
            path, limit=common.MAX_CLOSURE_BYTES, capture=True,
            require_trusted_parent=True)
    except common.DeliveryClosureError as error:
        raise VerificationError(str(error)) from error
    assert snapshot.data is not None
    value = builder._strict_document(
        snapshot.data, "engineering closure")
    if set(value) != {
            "schema", "version", "project_id", "round", "release_version",
            "generated_at", "status", "passed", "passed_scope", "source",
            "production_passed", "release_authorized",
            "artifact_roles", "artifacts", "semantic_summary",
            "safety_boundaries", "internal_open_items",
            "external_blockers"}:
        raise VerificationError("engineering closure fields are invalid")
    try:
        normalized_time = common._normalize_generated_at(value["generated_at"])
    except common.DeliveryClosureError as error:
        raise VerificationError("engineering generated_at is invalid") from error
    if (value["schema"] != builder.SCHEMA or value["version"] != 2 or
            value["project_id"] != builder.PROJECT_ID or
            type(value["round"]) is not int or value["round"] <= 0 or
            not isinstance(value["release_version"], str) or
            builder.RELEASE.fullmatch(value["release_version"]) is None or
            not value["release_version"].endswith(
                f"-round{value['round']}") or
            normalized_time != value["generated_at"] or
            value["status"] != builder.STATUS or
            value["passed"] is not True or
            value["passed_scope"] != builder.PASSED_SCOPE or
            value["production_passed"] is not builder.PRODUCTION_PASSED or
            value["release_authorized"] is not builder.RELEASE_AUTHORIZED or
            value["artifact_roles"] != list(builder.REQUIRED_ROLES) or
            value["safety_boundaries"] != builder.SAFETY_BOUNDARIES or
            not isinstance(value["internal_open_items"], list) or
            value["external_blockers"] != list(builder.EXTERNAL_BLOCKERS)):
        raise VerificationError(
            "engineering closure boundary is invalid")
    source = value["source"]
    if (not isinstance(source, dict) or
            set(source) != {
                "product_git_head", "release_git_head",
                "artifact_map_sha256"} or
            not isinstance(source["product_git_head"], str) or
            builder.HEX40.fullmatch(source["product_git_head"]) is None or
            not isinstance(source["release_git_head"], str) or
            builder.HEX40.fullmatch(source["release_git_head"]) is None or
            source["release_git_head"] == source["product_git_head"] or
            not isinstance(source["artifact_map_sha256"], str) or
            common.HEX64.fullmatch(source["artifact_map_sha256"]) is None):
        raise VerificationError("engineering source identity is invalid")
    artifacts = value["artifacts"]
    if (not isinstance(artifacts, list) or
            len(artifacts) != len(builder.REQUIRED_ROLES)):
        raise VerificationError("engineering artifact closure is invalid")
    try:
        root = builder.verification._protected_root(artifact_root)
    except builder.verification.EvidenceError as error:
        raise VerificationError(
            "engineering artifact root is unsafe") from error
    roles: set[str] = set()
    paths: dict[str, Path] = {}
    documents: dict[str, dict[str, Any]] = {}
    rebound = []
    for record in artifacts:
        if not isinstance(record, dict) or set(record) != {
                "role", "path", "sha256", "size", "mode"}:
            raise VerificationError("engineering artifact record is invalid")
        role = record["role"]
        if role not in builder.REQUIRED_ROLES or role in roles:
            raise VerificationError("engineering artifact role is invalid")
        roles.add(role)
        binding, data = builder._stable_binding(
            root, record["path"], role,
            capture=role in builder.JSON_ROLES)
        if binding != record:
            raise VerificationError(
                f"engineering artifact binding drift: {role}")
        rebound.append(binding)
        paths[role] = root.joinpath(
            *builder.PurePosixPath(record["path"]).parts)
        if role in builder.JSON_ROLES:
            assert data is not None
            documents[role] = builder._strict_document(data, role)
    if roles != set(builder.REQUIRED_ROLES):
        raise VerificationError("engineering artifact roles are incomplete")
    try:
        artifact_map, map_bytes = builder.load_artifact_map(
            paths["engineering-artifact-map"])
    except builder.EngineeringClosureError as error:
        raise VerificationError(str(error)) from error
    expected_map_records = [
        {"role": record["role"], "path": record["path"]}
        for record in artifacts
    ]
    if (artifact_map["round"] != value["round"] or
            artifact_map["release_version"] != value["release_version"] or
            artifact_map["git_head"] != source["product_git_head"] or
            artifact_map["artifacts"] != expected_map_records or
            source["artifact_map_sha256"] !=
            hashlib.sha256(map_bytes).hexdigest()):
        raise VerificationError("engineering artifact map identity drift")
    try:
        semantic = builder._semantic_verify(
            root, paths, documents,
            source["product_git_head"], value["release_version"])
    except builder.EngineeringClosureError as error:
        raise VerificationError(str(error)) from error
    if semantic != value["semantic_summary"]:
        raise VerificationError("engineering semantic summary drift")
    if (semantic.get("product_git_head") !=
            source["product_git_head"] or
            semantic.get("release_git_head") !=
            source["release_git_head"] or
            semantic.get("baseline_path") !=
            builder._round_baseline_path(value["release_version"])):
        raise VerificationError("engineering dual-head lineage drift")
    baseline_head = documents[
        "source-baseline-manifest"].get("git_head")
    if source["product_git_head"] != baseline_head:
        raise VerificationError("engineering source identity drift")
    artifact_map_binding = next(
        record for record in artifacts
        if record["role"] == "engineering-artifact-map")
    if source["artifact_map_sha256"] != (
            artifact_map_binding["sha256"]):
        raise VerificationError("engineering artifact map identity drift")
    expected_open_items = []
    if documents["workspace-layout-report"].get(
            "externalization_complete") is not True:
        expected_open_items.append("workspace-storage-externalization")
    if documents["legacy-wrapper-inventory-report"].get(
            "migration_complete") is not True:
        expected_open_items.append("legacy-wrapper-migration")
    if value["internal_open_items"] != expected_open_items:
        raise VerificationError("engineering internal open items drift")
    return {
        "schema": "heptatrader.engineering-closure-verification.v2",
        "passed": True,
        "round": value["round"],
        "release_version": value["release_version"],
        "status": value["status"],
        "product_git_head": source["product_git_head"],
        "release_git_head": source["release_git_head"],
        "artifact_count": len(rebound),
        "semantic_summary": semantic,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--closure", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    arguments = parser.parse_args()
    result = verify(arguments.closure, arguments.artifact_root)
    print(
        f"PASS: {result['schema']} round={result['round']} "
        f"artifacts={result['artifact_count']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
