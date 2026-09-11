#!/usr/bin/env python3
"""Verify bounded IB PAPER progressive-rollout evidence.

This verifier is intentionally smaller than full qualification. It proves that
one immutable candidate completed the requested PAPER-V4 rollout stage, that
all bounded mutation cycles terminalized flat, and that no unresolved command
remains. It never grants PAPER or LIVE authorization by itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs/ib-paper-rollout-policy-v1.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RESULT_KEYS = {
    "schema",
    "candidate_sha",
    "binary_sha256",
    "harness_sha256",
    "stage",
    "account_mode",
    "profile_order_mode",
    "mutation_cycles",
    "successful_round_trips",
    "max_order_quantity",
    "max_order_notional",
    "max_active_orders",
    "max_gross_position",
    "final_active_orders",
    "final_uncertain_commands",
    "final_position_quantity",
    "authoritative_reconciliation_complete",
    "live_authorized",
    "evidence",
}
EVIDENCE_KEYS = {"kind", "path", "sha256", "size"}
REQUIRED_EVIDENCE_KINDS = {
    "authoritative-snapshot",
    "broker-callbacks",
    "oms-journal",
}
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024


class VerificationError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise VerificationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise VerificationError(f"non-finite JSON number: {value}")


def _load_json(path: Path) -> Any:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise VerificationError(f"{path}: expected a regular single-link file")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"{path}: invalid JSON: {error}") from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.resolve(strict=True)
    metadata = resolved.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise VerificationError(f"{label}: expected a regular single-link file")
    return resolved


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VerificationError(f"{label}: finite number required")
    result = float(value)
    if not math.isfinite(result):
        raise VerificationError(f"{label}: finite number required")
    return result


def _positive_number(value: Any, label: str) -> float:
    result = _finite_number(value, label)
    if result <= 0.0:
        raise VerificationError(f"{label}: positive number required")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise VerificationError(f"{label}: positive integer required")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VerificationError(f"{label}: non-negative integer required")
    return value


def _stage_policy(policy: dict[str, Any], stage: str) -> dict[str, Any]:
    if policy.get("schema") != "heptatrader.ib-paper-rollout-policy.v1":
        raise VerificationError("unsupported rollout policy schema")
    if policy.get("execution_mode") != "PAPER" or policy.get("live_authorized") is not False:
        raise VerificationError("rollout policy must remain PAPER-only")
    stages = policy.get("stages")
    if not isinstance(stages, list):
        raise VerificationError("rollout policy stages must be an array")
    matches = [item for item in stages if isinstance(item, dict) and item.get("id") == stage]
    if len(matches) != 1:
        raise VerificationError(f"unknown or duplicate rollout stage: {stage}")
    return matches[0]


def _verify_evidence(root: Path, entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or not entries:
        raise VerificationError("evidence must be a non-empty array")
    root = root.resolve(strict=True)
    observed_kinds: set[str] = set()
    observed_paths: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict) or set(item) != EVIDENCE_KEYS:
            raise VerificationError(f"evidence[{index}]: fields are not canonical")
        kind = item["kind"]
        relative = item["path"]
        digest = item["sha256"]
        size = item["size"]
        if not isinstance(kind, str) or kind not in REQUIRED_EVIDENCE_KINDS:
            raise VerificationError(f"evidence[{index}]: unsupported kind")
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise VerificationError(f"evidence[{index}]: invalid path")
        path_value = Path(relative)
        if path_value.is_absolute() or any(part in {"", ".", ".."} for part in path_value.parts):
            raise VerificationError(f"evidence[{index}]: non-canonical path")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise VerificationError(f"evidence[{index}]: invalid sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0 or size > MAX_EVIDENCE_BYTES:
            raise VerificationError(f"evidence[{index}]: invalid size")
        candidate = (root / path_value).resolve(strict=True)
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise VerificationError(f"evidence[{index}]: path escapes evidence root") from error
        metadata = candidate.lstat()
        if candidate.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise VerificationError(f"evidence[{index}]: file is not regular single-link")
        if metadata.st_size != size:
            raise VerificationError(f"evidence[{index}]: size mismatch")
        if _sha256_file(candidate) != digest:
            raise VerificationError(f"evidence[{index}]: digest mismatch")
        if kind in observed_kinds:
            raise VerificationError(f"duplicate evidence kind: {kind}")
        if relative in observed_paths:
            raise VerificationError(f"duplicate evidence path: {relative}")
        observed_kinds.add(kind)
        observed_paths.add(relative)
        normalized.append({"kind": kind, "path": relative, "sha256": digest, "size": size})
    if observed_kinds != REQUIRED_EVIDENCE_KINDS:
        missing = sorted(REQUIRED_EVIDENCE_KINDS - observed_kinds)
        raise VerificationError("missing rollout evidence kinds: " + ", ".join(missing))
    return normalized


def verify(
    result_path: Path,
    evidence_root: Path,
    expected_git_sha: str,
    expected_binary: Path,
    expected_harness: Path,
    expected_stage: str,
    policy_path: Path = POLICY,
) -> dict[str, Any]:
    if SHA40.fullmatch(expected_git_sha) is None:
        raise VerificationError("expected git SHA is not canonical")
    binary = _regular_file(expected_binary, "expected binary")
    harness = _regular_file(expected_harness, "expected harness")
    result = _load_json(result_path)
    policy = _load_json(policy_path)
    if not isinstance(result, dict) or set(result) != RESULT_KEYS:
        raise VerificationError("rollout result fields are not canonical")
    if not isinstance(policy, dict):
        raise VerificationError("rollout policy is invalid")
    stage = _stage_policy(policy, expected_stage)

    if result.get("schema") != "heptatrader.ib-paper-rollout-result.v1":
        raise VerificationError("unsupported rollout result schema")
    if result.get("candidate_sha") != expected_git_sha:
        raise VerificationError("candidate SHA mismatch")
    binary_digest = _sha256_file(binary)
    harness_digest = _sha256_file(harness)
    if result.get("binary_sha256") != binary_digest:
        raise VerificationError("binary digest mismatch")
    if result.get("harness_sha256") != harness_digest:
        raise VerificationError("harness digest mismatch")
    if result.get("stage") != expected_stage:
        raise VerificationError("rollout stage mismatch")
    if result.get("account_mode") != "PAPER":
        raise VerificationError("rollout must execute against PAPER")
    if result.get("live_authorized") is not False:
        raise VerificationError("rollout result may not authorize LIVE")
    if result.get("profile_order_mode") != policy.get("profile_order_mode"):
        raise VerificationError("rollout profile mode mismatch")

    cycles = _positive_int(result.get("mutation_cycles"), "mutation_cycles")
    successful = _positive_int(result.get("successful_round_trips"), "successful_round_trips")
    maximum_cycles = _positive_int(stage.get("max_mutation_cycles"), "policy max_mutation_cycles")
    if cycles > maximum_cycles or successful != cycles:
        raise VerificationError("rollout cycles exceed policy or did not all round-trip")

    numeric_limits = (
        ("max_order_quantity", "max_order_quantity"),
        ("max_order_notional", "max_order_notional"),
        ("max_gross_position", "max_gross_position"),
    )
    for result_name, policy_name in numeric_limits:
        observed = _positive_number(result.get(result_name), result_name)
        maximum = _positive_number(stage.get(policy_name), f"policy {policy_name}")
        if observed > maximum:
            raise VerificationError(f"{result_name} exceeds rollout policy")
    observed_active_limit = _positive_int(result.get("max_active_orders"), "max_active_orders")
    policy_active_limit = _positive_int(stage.get("max_active_orders"), "policy max_active_orders")
    if observed_active_limit > policy_active_limit:
        raise VerificationError("max_active_orders exceeds rollout policy")

    if _nonnegative_int(result.get("final_active_orders"), "final_active_orders") != 0:
        raise VerificationError("rollout did not terminalize active orders")
    if _nonnegative_int(result.get("final_uncertain_commands"), "final_uncertain_commands") != 0:
        raise VerificationError("rollout retained uncertain commands")
    final_position = _finite_number(result.get("final_position_quantity"), "final_position_quantity")
    if final_position != 0.0:
        raise VerificationError("rollout final position is not flat")
    if result.get("authoritative_reconciliation_complete") is not True:
        raise VerificationError("authoritative reconciliation is incomplete")
    if stage.get("require_flat_between_cycles") is not True:
        raise VerificationError("rollout policy must require flatness between cycles")

    evidence = _verify_evidence(evidence_root, result.get("evidence"))
    return {
        "schema": "heptatrader.ib-paper-rollout-verification.v1",
        "candidate_sha": expected_git_sha,
        "binary_sha256": binary_digest,
        "harness_sha256": harness_digest,
        "stage": expected_stage,
        "mutation_cycles": cycles,
        "successful_round_trips": successful,
        "final_active_orders": 0,
        "final_uncertain_commands": 0,
        "final_position_quantity": 0.0,
        "authoritative_reconciliation_complete": True,
        "evidence": evidence,
        "authorization_effect": "NONE",
        "paper_authorized": False,
        "live_authorized": False,
    }


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise VerificationError("receipt path already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".rollout.", delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-binary", type=Path, required=True)
    parser.add_argument("--expected-harness", type=Path, required=True)
    parser.add_argument("--expected-stage", choices=("canary", "pilot", "extended"), required=True)
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = verify(
            args.result,
            args.evidence_root,
            args.expected_git_sha,
            args.expected_binary,
            args.expected_harness,
            args.expected_stage,
            args.policy,
        )
        _write_receipt(args.receipt, receipt)
    except (OSError, VerificationError) as error:
        print(f"[IB-PAPER-ROLLOUT] FAIL: {error}", file=os.sys.stderr)
        return 1
    print(f"[IB-PAPER-ROLLOUT] PASS stage={args.expected_stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
