"""Verify existing original PAPER REQUEST→DECISION→PREPARED bindings read-only.

This reports provenance only, never origin-side candidate completeness,
behavior propensity, product-root authentication or trading permission.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from hepta_decision_attribution import read_private, strict, verified_bindings

SCHEMA = "heptatrader.train-v2-original-policy-provenance-audit.v1"
MAX_RELEASES = 32
MAX_RUNTIME_FILES = 16


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_release(manifest_file: Path, *, binder=verified_bindings) -> dict:
    """A verified binding proves identity of the original call graph ONLY."""
    if (manifest_file.is_symlink() or manifest_file.parent.is_symlink()
            or manifest_file.parent.parent.is_symlink()):
        raise ValueError("MANIFEST_OR_RELEASE_SYMLINK")
    raw = read_private(manifest_file, 65536)
    manifest = strict(raw)
    instrument = manifest.get("instrument")
    if (manifest.get("schema") != "heptatrader.attribution-producer.v1"
            or manifest.get("execution_domain") != "PAPER"
            or instrument not in ("EUR.USD", "BTC.USD")
            or type(manifest.get("trading_behavior_changed")) is not bool):
        raise ValueError("ORIGINAL_PAPER_SCOPE_REQUIRED")
    if not isinstance(manifest.get("period_sha256"), str) or len(manifest["period_sha256"]) != 64:
        raise ValueError("PERIOD_ID_INVALID")
    if sha256(read_private(manifest["period_path"], 65536)) != manifest["period_sha256"]:
        raise ValueError("ORIGINAL_PERIOD_CHANGED")

    runtime = manifest.get("runtime_sha256")
    if not isinstance(runtime, dict) or not 1 <= len(runtime) <= MAX_RUNTIME_FILES:
        raise ValueError("ORIGINAL_RUNTIME_SET_MISSING")
    hashes = []
    for basename, expected in sorted(runtime.items()):
        if (not isinstance(basename, str) or basename in (".", "..")
                or Path(basename).name != basename or "/" in basename
                or not isinstance(expected, str) or len(expected) != 64):
            raise ValueError("ORIGINAL_RUNTIME_PATH_INVALID")
        actual = sha256(read_private(manifest_file.parent / basename, 1048576))
        if actual != expected:
            raise ValueError("ORIGINAL_RUNTIME_SHA_CHANGED")
        hashes.append(actual)

    registration = {"manifest": str(manifest_file), "sha256": sha256(raw)}
    bindings, issues = binder(registration, instrument, manifest["period_sha256"])
    if not isinstance(bindings, dict) or not isinstance(issues, list):
        raise ValueError("ORIGINAL_BINDING_READER_INVALID")
    required = {"input_id", "forecast_id", "request_id", "prompt_sha256",
                "producer_manifest_sha256", "source_prefixes", "binding_basis"}
    actions = Counter()
    for command_id, row in bindings.items():
        if (not isinstance(command_id, str) or not isinstance(row, dict)
                or not required <= set(row) or row["binding_basis"]
                != "IDENTICAL_RETURNED_OBJECT_AT_ORIGINAL_DURABLE_PREPARED"
                or row["producer_manifest_sha256"] != registration["sha256"]
                or row.get("instrument") != instrument or row.get("side") not in ("BUY", "SELL")
                or not isinstance(row["source_prefixes"], list)
                or not row["source_prefixes"]):
            raise ValueError("NONEXACT_ORIGINAL_COMMAND_BINDING")
        # Side is a recorded command, not its probability under the policy.
        actions[row["side"]] += 1
    return {
        "instrument": instrument,
        "declared_trading_behavior_changed": manifest["trading_behavior_changed"],
        "manifest_sha256": registration["sha256"],
        "runtime_file_count_verified": len(hashes),
        "runtime_digest_set_sha256": sha256("".join(hashes).encode()),
        "exact_original_command_binding_count": len(bindings),
        "original_command_identity_digests": sorted(
            sha256(instrument.encode() + bytes([0]) + cid.encode())
            for cid in bindings
        ),
        "bound_command_side_counts": dict(sorted(actions.items())),
        "binding_issue_count": len(issues),
        "binding_issue_reasons": dict(sorted(Counter(
            str(i.get("reason", "UNKNOWN"))[:120] for i in issues
        ).items())),
        "original_complete_candidate_sets_attested": 0,
        "original_logged_behavior_propensities_attested": 0,
        "canonical_v2_product_admission": False,
        "trade_authority": False,
    }


def audit_releases(root: Path, *, verifier=verify_release) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("RELEASE_ROOT_UNSAFE")
    manifests = sorted(root.glob("*/*/manifest.json"))
    if len(manifests) > MAX_RELEASES:
        raise ValueError("RELEASE_COUNT_LIMIT")
    results = []
    errors = []
    for manifest in manifests:
        try:
            results.append(verifier(manifest))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            # Never silently drop a failed producer and proclaim chain completeness.
            errors.append({"manifest_path_sha256": sha256(str(manifest).encode()),
                           "reason": type(exc).__name__ + ":" + str(exc)[:120]})
    total = sum(x["exact_original_command_binding_count"] for x in results)
    changed = sum(x["exact_original_command_binding_count"] for x in results
                  if x["declared_trading_behavior_changed"])
    issue_count = sum(x["binding_issue_count"] for x in results)
    identities = Counter(h for x in results for h in x["original_command_identity_digests"])
    collisions = sum(n - 1 for n in identities.values() if n > 1)
    return {
        "schema": SCHEMA,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "original_release_count": len(manifests),
        "verified_release_count": len(results),
        "unverified_release_count": len(errors),
        "verified_original_command_bindings": total,
        "unique_instrument_command_identities": len(identities),
        "cross_release_duplicate_command_ids": collisions,
        "declared_behavior_changed_command_bindings": changed,
        "unchanged_policy_command_bindings": total - changed,
        "original_binding_issues": issue_count,
        "per_release": results,
        "unverified": errors,
        "original_complete_candidate_sets_attested": 0,
        "original_logged_behavior_propensities_attested": 0,
        "original_generator_root_trust_activated_by_audit": False,
        "independent_observer_root_trust_activated_by_audit": False,
        "native_product_ledger_decisions_written_by_audit": 0,
        "canonical_dataset_snapshot_v3_frozen_by_audit": False,
        "audit_granted_trade_authority": False,
        "broker_queries_or_orders_performed_by_audit": 0,
        "assessment": (
            "HOLD_POLICY_COHORT_MISMATCH_AND_MISSING_PROPENSITY" if changed
            else "HOLD_MISSING_ORIGINAL_LOGGED_BEHAVIOR_PROPENSITY"
        ) if not errors and not issue_count and not collisions and total else "HOLD_ORIGINAL_BINDINGS_UNVERIFIED",
    }


def write_once(output: Path, value: dict) -> str:
    if not output.is_absolute() or ".." in output.parts:
        raise ValueError("OUTPUT_PATH_UNSAFE")
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent = output.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
            or parent.st_mode & 0o077):
        raise ValueError("PRIVATE_OUTPUT_DIRECTORY_REQUIRED")
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    dirfd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)
    return sha256(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--releases-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    report = audit_releases(args.releases_root)
    digest = write_once(args.output, report)
    print(json.dumps({"schema": report["schema"], "assessment": report["assessment"],
                      "release_count": report["original_release_count"],
                      "verified_original_bindings": report["verified_original_command_bindings"],
                      "unverified": report["unverified_release_count"],
                      "source_issue_count": report["original_binding_issues"],
                      "true_behavior_propensity_proven": False,
                      "receipt_sha256": digest}, sort_keys=True))


if __name__ == "__main__":
    main()
