#!/usr/bin/env python3
"""Verify a HeptaTrader delivery closure and every fixed-role artifact."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
from typing import Any

import build_heptatrader_delivery_closure as closure_builder


VERIFICATION_SCHEMA = "heptatrader.delivery-closure-verification.v1"


class DeliveryClosureVerificationError(RuntimeError):
    """A closure or one of its artifact bindings failed closed."""


def _translate(error: Exception) -> DeliveryClosureVerificationError:
    return DeliveryClosureVerificationError(str(error))


def _read_closure(
    path: os.PathLike[str] | str,
) -> tuple[closure_builder.StableRead, bytes, dict[str, Any]]:
    try:
        captured = closure_builder.stable_read(
            path,
            limit=closure_builder.MAX_CLOSURE_BYTES,
            capture=True,
            require_trusted_parent=True,
        )
        if captured.mode != "0600":
            raise DeliveryClosureVerificationError(
                "delivery closure mode must remain 0600")
        if captured.data is None:
            raise DeliveryClosureVerificationError(
                "delivery closure contents were not captured")
        parsed = closure_builder.strict_json(
            captured.data, "delivery closure")
        closure = closure_builder.validate_contract_structure(parsed)
        if (captured.data !=
                closure_builder.canonical_json(closure) + b"\n"):
            raise DeliveryClosureVerificationError(
                "delivery closure is not canonical JSON plus one newline")
        return captured, captured.data, closure
    except DeliveryClosureVerificationError:
        raise
    except closure_builder.DeliveryClosureError as error:
        raise _translate(error) from error


def _verify_artifact(
    artifact_root: os.PathLike[str] | str,
    artifact: dict[str, Any],
) -> closure_builder.StableRead:
    role = artifact["role"]
    try:
        captured = closure_builder.stable_artifact(
            artifact_root, artifact["path"])
    except closure_builder.DeliveryClosureError as error:
        raise DeliveryClosureVerificationError(
            f"artifact {role} failed stable read: {error}") from error
    for field, observed in (
            ("sha256", captured.sha256),
            ("size", captured.size),
            ("mode", captured.mode)):
        if artifact[field] != observed:
            raise DeliveryClosureVerificationError(
                f"artifact {role} {field} binding drift")
    return captured


def verify(
    closure_path: os.PathLike[str] | str,
    artifact_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    """Verify exact schema and the complete causal seven-artifact lineage."""
    first_closure, closure_bytes, closure = _read_closure(closure_path)
    paths = {
        artifact["role"]: artifact["path"]
        for artifact in closure["artifacts"]
    }
    try:
        with closure_builder.open_artifact_set(
                artifact_root, paths) as opened:
            for artifact in closure["artifacts"]:
                role = artifact["role"]
                observed = opened[role].snapshot
                for field, value in (
                        ("sha256", observed.sha256),
                        ("size", observed.size),
                        ("mode", observed.mode)):
                    if artifact[field] != value:
                        raise DeliveryClosureVerificationError(
                            f"artifact {role} {field} binding drift")
            semantic = closure_builder.validate_delivery_evidence(
                opened,
                round_number=closure["round"],
                release_version=closure["release_version"],
            )
            second_closure, second_bytes, second_value = _read_closure(
                closure_path)
            if (second_closure != first_closure or
                    second_bytes != closure_bytes or
                    second_value != closure):
                raise DeliveryClosureVerificationError(
                    "delivery closure changed across verification")
    except DeliveryClosureVerificationError:
        raise
    except closure_builder.DeliveryClosureError as error:
        raise _translate(error) from error

    final_closure, final_bytes, final_value = _read_closure(closure_path)
    if (final_closure != first_closure or
            final_bytes != closure_bytes or final_value != closure):
        raise DeliveryClosureVerificationError(
            "delivery closure changed across final verification")

    return {
        "schema": VERIFICATION_SCHEMA,
        "version": 1,
        "status": "verified",
        "project_id": closure["project_id"],
        "round": closure["round"],
        "release_version": closure["release_version"],
        "passed": closure["passed"],
        "passed_scope": closure_builder.LOCAL_OFFLINE_SCOPE,
        "artifact_count": len(closure_builder.REQUIRED_ARTIFACT_ROLES),
        "artifact_roles": list(closure_builder.REQUIRED_ARTIFACT_ROLES),
        "closure_sha256": hashlib.sha256(closure_bytes).hexdigest(),
        "git_head": semantic["git_head"],
        "source_manifest_sha256":
            semantic["source_manifest_sha256"],
        "source_manifest_file_count":
            semantic["source_manifest_file_count"],
        "bundle_sha256": semantic["bundle_sha256"],
        "bundle_manifest_sha256":
            semantic["bundle_manifest_sha256"],
        "broker_connection_performed": False,
        "order_placement_performed": False,
        "paper_authorized": False,
        "live_authorized": False,
        "source_files_deleted": False,
        "source_removal_authorized": False,
        "real_systemd_certified": False,
        "real_ib_certified": False,
        "object_store_ingestion_receipt_certified": False,
        "retention_enforcement_certified": False,
        "release_authorized": semantic["release_authorized"],
        "clean_checkout_certified":
            semantic["clean_checkout_certified"],
        "blocked_reason": semantic["blocked_reason"],
        "production_trust_status":
            closure_builder.PRODUCTION_TRUST_STATUS,
        "production_trust_key_count": 0,
    }


verify_delivery_closure = verify


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a local/offline HeptaTrader delivery closure")
    parser.add_argument("--closure", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    arguments = parser.parse_args()
    report = verify(arguments.closure, arguments.artifact_root)
    print(
        f"VERIFIED: round={report['round']} "
        f"release={report['release_version']} "
        f"scope={report['passed_scope']} "
        f"passed={str(report['passed']).lower()} "
        f"artifacts={report['artifact_count']} "
        f"closure_sha256={report['closure_sha256']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DeliveryClosureVerificationError, OSError) as error:
        print(f"delivery-closure-verification: {error}", file=os.sys.stderr)
        raise SystemExit(78)
