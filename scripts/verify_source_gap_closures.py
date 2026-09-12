#!/usr/bin/env python3
"""Validate HeptaTrader's repository-controlled source-gap closure.

This verifier delegates documentation, build ownership, qualification-boundary,
and profile checks to their canonical implementations. Source token-count guards have been removed; behavioral proof remains in the independently built
and executed C++ and Python test suites. Nothing here grants PAPER or LIVE
authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REPOSITORY_GAPS = {
    "DOC-001",
    "CI-001",
    "TEST-001",
    "RISK-001",
    "PENDING-EXPOSURE-001",
    "VENUE-001",
    "OMS-001",
    "BUILD-001",
    "RELEASE-001",
}
EXPECTED_EXTERNAL_GAPS: set[str] = set()
REQUIRED_TEST_TARGETS = {
    "hepta_pre_trade_risk_engine_tests",
    "hepta_venue_capability_tests",
    "hepta_oms_journal_durability_tests",
    "hepta_oms_journal_schema_v4_tests",
    "hepta_simulator_risk_runtime_tests",
    "hepta_execution_coordinator_tests",
    "hepta_unix_session_supervisor_server_tests",
    "hepta_ib_live_terminal_reconciliation_tests",
    "hepta_ib_paper_execution_profile_tests",
}


class SourceClosureError(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceClosureError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise SourceClosureError(f"non-finite JSON number: {value}")


def load_json(path: Path) -> Any:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise SourceClosureError(f"{path}: expected a regular file")
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SourceClosureError(f"cannot load {path}: {error}") from error


def read_text(root: Path, relative: str) -> str:
    path = root / relative
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise SourceClosureError(f"{relative}: expected a regular file")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SourceClosureError(f"{relative}: unreadable: {error}") from error






def validate_documentation(root: Path) -> list[str]:
    import check_documentation
    return [f"documentation: {item}" for item in check_documentation.validate(root)]


def validate_ci_boundary(root: Path) -> list[str]:
    import check_qualification_trust_boundary

    return [
        f"qualification boundary: {item}"
        for item in check_qualification_trust_boundary.validate(root)
    ]


def validate_test_inventory(root: Path) -> list[str]:
    import verify_build_ownership

    try:
        manifest = verify_build_ownership.load_json(root / "docs/build-targets.json")
        verify_build_ownership.verify(root, manifest, "core")
        targets = {
            item["name"]: item
            for item in manifest["profiles"]["core"]["targets"]
        }
    except (verify_build_ownership.OwnershipError, OSError, KeyError, TypeError) as error:
        return [str(error)]

    required = REQUIRED_TEST_TARGETS | {"hepta_core_test_binaries"}
    missing = sorted(required - set(targets))
    if missing:
        return ["missing gap-critical test targets: " + ", ".join(missing)]

    errors: list[str] = []
    for name in sorted(REQUIRED_TEST_TARGETS):
        if targets[name].get("type") != "EXECUTABLE":
            errors.append(f"gap-critical test is not an executable target: {name}")

    reachable: set[str] = set()
    pending = list(targets["hepta_core_test_binaries"].get("dependencies", []))
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        if name not in targets:
            errors.append(f"core test aggregate references unknown target: {name}")
            continue
        reachable.add(name)
        pending.extend(targets[name].get("dependencies", []))
    absent = sorted(REQUIRED_TEST_TARGETS - reachable)
    if absent:
        errors.append(
            "core test aggregate does not build gap-critical targets: "
            + ", ".join(absent)
        )
    return errors








def validate_profile(root: Path) -> list[str]:
    import verify_canonical_ib_paper_profile

    return [
        f"IB PAPER profile: {item}"
        for item in verify_canonical_ib_paper_profile.validate(
            root / "docs/ib-paper-profile-policy-v1.json",
            root / "systemd/hepta-execution-ib-paper.env.example",
        )
    ]




def validate_register_projection(root: Path) -> list[str]:
    import check_gap_register

    errors = [
        f"canonical registry: {item}"
        for item in check_gap_register.validate(root)
    ]
    try:
        register = load_json(root / "docs/gap-register.json")
    except SourceClosureError as error:
        return [str(error)]
    if (
        not isinstance(register, dict)
        or register.get("schema") != "heptatrader.gap-register.v1"
    ):
        return ["unsupported gap register schema"]
    gaps = register.get("gaps")
    if not isinstance(gaps, list):
        return ["gap register gaps must be an array"]

    by_id: dict[str, dict[str, Any]] = {}
    for item in gaps:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            errors.append("gap register contains an invalid gap")
            continue
        gap_id = item["id"]
        if gap_id in by_id:
            errors.append(f"duplicate gap id: {gap_id}")
        by_id[gap_id] = item

    repository_ids = {
        gap_id
        for gap_id, item in by_id.items()
        if item.get("domain") == "REPOSITORY"
    }
    external_ids = {
        gap_id
        for gap_id, item in by_id.items()
        if item.get("domain") == "EXTERNAL"
    }
    if repository_ids != EXPECTED_REPOSITORY_GAPS:
        errors.append(
            "repository gap/verifier set mismatch: "
            f"expected={sorted(EXPECTED_REPOSITORY_GAPS)} "
            f"actual={sorted(repository_ids)}"
        )
    if external_ids != EXPECTED_EXTERNAL_GAPS:
        errors.append(
            "unexpected external gaps remain: " + ", ".join(sorted(external_ids))
        )
    for gap_id in EXPECTED_REPOSITORY_GAPS:
        if by_id.get(gap_id, {}).get("state") != "CLOSED_SOURCE":
            errors.append(f"{gap_id}: repository source state is not CLOSED_SOURCE")
    for gap_id in EXPECTED_EXTERNAL_GAPS:
        if by_id.get(gap_id, {}).get("state") != "OPEN_EXTERNAL":
            errors.append(f"{gap_id}: external evidence cannot be closed by source")

    authorization = register.get("authorization")
    if not isinstance(authorization, dict):
        errors.append("authorization projection is missing")
    else:
        if authorization.get("source_state") != "READY":
            errors.append(
                "source_state must be READY after supported-scope closure"
            )
        if authorization.get("paper_authorized") is not False:
            errors.append("PAPER must remain unauthorized without a Broker receipt")
        if authorization.get("live_authorized") is not False:
            errors.append("LIVE must remain unauthorized")
    return errors


def validate_temporary_artifacts(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    if list((root / "scripts").glob(".hepta-gap-closure-payload-*.b64")):
        errors.append(
            "BUILD-001: temporary encoded gap-closure payload remains in the product tree"
        )
    workflows = root / ".github/workflows"
    if workflows.is_dir():
        for path in sorted(workflows.glob("codex-*")):
            errors.append(
                "CI-001: temporary branch-mutating workflow remains in the "
                f"product tree: {path.relative_to(root)}"
            )
    for relative in (
        ".github/workflows/refactor-apply.yml",
        ".github/workflows/source-snapshot-temporary.yml",
        ".github/workflows/recover-gap-payload.yml",
        ".github/workflows/flatten-capacity-materialize.yml",
        "scripts/apply_flatten_capacity_fix.py",
    ):
        if (root / relative).exists():
            errors.append(
                f"CI-001: temporary remediation artifact remains: {relative}"
            )
    return errors


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    try:
        errors.extend(validate_temporary_artifacts(root))
        errors.extend(f"DOC-001: {item}" for item in validate_documentation(root))
        errors.extend(f"CI-001: {item}" for item in validate_ci_boundary(root))
        errors.extend(f"TEST-001: {item}" for item in validate_test_inventory(root))
        errors.extend(
            f"PENDING-EXPOSURE-001: {item}" for item in validate_profile(root)
        )
        errors.extend(validate_register_projection(root))
    except (SourceClosureError, OSError, UnicodeError) as error:
        errors.append(str(error))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    errors = validate(args.root)
    for error in errors:
        print(f"[SOURCE-GAPS] {error}", file=sys.stderr)
    if errors:
        return 1
    print(
        "[SOURCE-GAPS] PASS registry/build model and executable workflow contracts; "
        "no C++ behavioral tests executed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
