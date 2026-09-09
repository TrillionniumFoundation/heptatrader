#!/usr/bin/env python3
"""Check the source-gap registry and supplemental static source contracts.

Documentation, fresh CMake ownership, qualification-boundary and profile
validators are executed. Risk, venue and OMS token checks are static guards,
not behavioral proof. C++ behavior is established by the separately built and
executed core test suites. This script neither runs those suites nor grants
PAPER/LIVE authorization. Optional Broker activation remains separately fail-closed.
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


def require_tokens(
    root: Path,
    relative: str,
    tokens: tuple[str, ...],
    gap: str,
    errors: list[str],
) -> str:
    try:
        text = read_text(root, relative)
    except SourceClosureError as error:
        errors.append(f"{gap}: {error}")
        return ""
    for token in tokens:
        if token not in text:
            errors.append(f"{gap}: {relative}: missing contract token {token!r}")
    return text


def forbid_tokens(
    text: str,
    relative: str,
    tokens: tuple[str, ...],
    gap: str,
    errors: list[str],
) -> None:
    for token in tokens:
        if token in text:
            errors.append(f"{gap}: {relative}: forbidden fail-open token {token!r}")


def validate_documentation(root: Path) -> list[str]:
    import check_documentation
    import verify_build_ownership

    errors = [
        f"documentation: {item}" for item in check_documentation.validate(root)
    ]
    try:
        inventory = verify_build_ownership.load_json(
            root / "docs/build-targets.json"
        )
        verify_build_ownership.verify(root, inventory, "core")
    except (verify_build_ownership.OwnershipError, OSError) as error:
        errors.append(f"build ownership: {error}")
    return errors


def validate_ci_boundary(root: Path) -> list[str]:
    import check_qualification_trust_boundary

    return [
        f"qualification boundary: {item}"
        for item in check_qualification_trust_boundary.validate(root)
    ]


def validate_test_inventory(root: Path) -> list[str]:
    import verify_build_ownership

    errors: list[str] = []
    try:
        manifest = verify_build_ownership.load_json(root / "docs/build-targets.json")
        # A source token or a hand-edited JSON dependency is not a build edge.
        # Even when this function is used alone, compare with a fresh codemodel.
        verify_build_ownership.verify(root, manifest, "core")
        targets = {item["name"]: item for item in manifest["profiles"]["core"]["targets"]}
    except (verify_build_ownership.OwnershipError, OSError) as error:
        return [str(error)]
    missing = sorted((REQUIRED_TEST_TARGETS | {"hepta_core_test_binaries"}) - set(targets))
    if missing:
        errors.append("missing gap-critical test targets: " + ", ".join(missing))
        return errors
    for name in sorted(REQUIRED_TEST_TARGETS):
        if targets[name]["type"] != "EXECUTABLE":
            errors.append(f"gap-critical test is not an executable target: {name}")
    reachable: set[str] = set()
    pending = list(targets["hepta_core_test_binaries"]["dependencies"])
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        pending.extend(targets[name]["dependencies"])
    absent = sorted(REQUIRED_TEST_TARGETS - reachable)
    if absent:
        errors.append("core test aggregate does not build gap-critical targets: " + ", ".join(absent))
    return errors


def validate_risk(root: Path) -> list[str]:
    errors: list[str] = []
    require_tokens(
        root,
        "HeptaTrade/risk/pre_trade_risk_engine.h",
        (
            "struct PreTradeRiskSubject",
            "std::set<std::string> instruments",
            "struct PreTradeRiskOrderNotionalEvidence",
            "PreTradeRiskInstrumentContract",
            "authorizedQuoteSourceId",
            "authorizedFxSourceId",
        ),
        "RISK-001",
        errors,
    )
    implementation = require_tokens(
        root,
        "HeptaTrade/risk/pre_trade_risk_engine.cpp",
        (
            "bool SameSubject(",
            "PreTradeRiskDecision ValidateOrderNotional(",
            "RISK_SNAPSHOT_SUBJECT_MISMATCH",
            "RISK_ORDER_NOTIONAL_SUBJECT_MISMATCH",
            "RISK_ORDER_NOTIONAL_UNIT_CONTRACT_INVALID",
            "RISK_ORDER_NOTIONAL_QUOTE_STALE",
            "RISK_ORDER_NOTIONAL_FX_STALE",
            "RISK_ORDER_NOTIONAL_CONVERSION_MISMATCH",
            "evidence.quantity * evidence.contract.multiplier * price * fx.rate",
            "afterPosition >= 0.0",
            "afterPosition <= 0.0",
        ),
        "RISK-001",
        errors,
    )
    forbid_tokens(
        implementation,
        "HeptaTrade/risk/pre_trade_risk_engine.cpp",
        (
            "orderNotional = ctx.totalQuantity * price",
            "snapshotComplete = true",
        ),
        "RISK-001",
        errors,
    )
    require_tokens(
        root,
        "HeptaTrade/simulator/deterministic_execution_venue.cpp",
        (
            "flattenCapacityReserved",
            "SIM_FLATTEN_RESERVATION_INCONSISTENT",
            "reducesWithoutCrossing",
            'order.terminalStatus = "Rejected"',
        ),
        "RISK-001",
        errors,
    )
    tests = require_tokens(
        root,
        "tests/pre_trade_risk_engine_tests.cpp",
        (
            "TestSubjectIsolation",
            "TestNotionalEvidence",
            "generic quantity-price fallback removed",
            "cross-subject or mixed-section snapshot must fail",
            "omitted multiplier",
            "stale quote",
            "stale FX",
            "small long-to-short crossing must be blocked",
            "small short-to-long crossing must be blocked",
        ),
        "RISK-001",
        errors,
    )
    if "pending exposure must count in worst-case gross" not in tests:
        errors.append(
            "PENDING-EXPOSURE-001: pending exposure hostile regression is missing"
        )
    require_tokens(
        root,
        "tests/simulator_risk_runtime_tests.cpp",
        (
            "TestFlattenCapacityReservations",
            "TestFlattenCancellationAndStrictRemainder",
            "TestFlattenPolicyTransitionsAndFillBoundary",
            "two exact exits must not both reserve ten units",
        ),
        "RISK-001",
        errors,
    )
    return errors


def validate_venue(root: Path) -> list[str]:
    errors: list[str] = []
    ctp = require_tokens(
        root,
        "HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp",
        (
            "CTP_TRANSPORT_NOT_IMPLEMENTED",
            'return "EXPERIMENTAL_NO_TRANSPORT"',
            "return false",
        ),
        "VENUE-001",
        errors,
    )
    require_tokens(
        root,
        "HeptaTrade/adapter_xt/xt_gateway_adapter.cpp",
        (
            "XT_TRANSPORT_NOT_IMPLEMENTED",
            'return "EXPERIMENTAL_NO_TRANSPORT"',
            'return RejectUnsupported("place_order")',
            "if (!TransportImplemented())",
        ),
        "VENUE-001",
        errors,
    )
    if "m_connected = true" in ctp:
        errors.append(
            "VENUE-001: CTP scaffold must not report a connected transport"
        )
    require_tokens(
        root,
        "HeptaTrade/adapter_xt/xt_gateway_adapter.h",
        ("static bool TransportImplemented() { return false; }",),
        "VENUE-001",
        errors,
    )
    require_tokens(
        root,
        "tests/venue_capability_tests.cpp",
        (
            "EXPERIMENTAL_NO_TRANSPORT",
            "TRANSPORT_NOT_IMPLEMENTED",
        ),
        "VENUE-001",
        errors,
    )
    return errors


def validate_oms(root: Path) -> list[str]:
    errors: list[str] = []
    # Presence checks only. Textual ordering cannot establish control flow or
    # durability; coordinator/durability tests provide that separate evidence.
    require_tokens(
        root,
        "HeptaTrade/execution/execution_place_order_dispatch.cpp",
        (
            '"place_send_attempt"',
            "OMS_PLACE_SEND_ATTEMPT_WRITE_FAILED",
            "placeIbOrderCommandCorrelated",
            "IB_PLACE_OUTCOME_UNCERTAIN",
            "RECOVERY_RECONCILE_REQUIRED",
            '"place_activated"',
        ),
        "OMS-001",
        errors,
    )
    require_tokens(
        root,
        "tests/execution_coordinator_tests.cpp",
        (
            "place_send_attempt",
            "IB_PLACE_OUTCOME_UNCERTAIN",
            "venueCalls == 1",
        ),
        "OMS-001",
        errors,
    )
    require_tokens(
        root,
        "tests/oms_journal_durability_tests.cpp",
        (
            "TestPathReplacementPoisonsBeforeWriting",
            "TestStrictReplayIsCallbackAtomicAndReentrant",
            "writePoisoned",
        ),
        "OMS-001",
        errors,
    )
    require_tokens(
        root,
        "scripts/verify_oms_journal_replay.py",
        (
            "CURRENT_SCHEMA = 4",
            "duplicate key",
            "non-finite JSON constant",
        ),
        "OMS-001",
        errors,
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



def validate_release(root: Path) -> list[str]:
    errors: list[str] = []
    require_tokens(
        root,
        "CMakeLists.txt",
        (
            'set(HEPTA_RELEASE_LABEL "0.1.0-beta.1" CACHE STRING',
            "include(cmake/HeptaInstall.cmake)",
        ),
        "RELEASE-001",
        errors,
    )
    require_tokens(
        root,
        "cmake/HeptaInstall.cmake",
        (
            "canonical install target is missing",
            "scripts/hepta_preflight.py",
            "docs/preflight-policy-v1.json",
            "install(DIRECTORY",
        ),
        "RELEASE-001",
        errors,
    )
    require_tokens(
        root,
        "scripts/build_release_package.py",
        (
            'MANIFEST_SCHEMA = "heptatrader.release-manifest.v1"',
            'RECEIPT_SCHEMA = "heptatrader.release-package-receipt.v1"',
            'getattr(os, "O_NOFOLLOW", 0)',
            "authorization_effect",
            "paper_authorized",
            "live_authorized",
        ),
        "RELEASE-001",
        errors,
    )
    require_tokens(
        root,
        "scripts/hepta_preflight.py",
        (
            'POLICY_SCHEMA = "heptatrader.preflight-policy.v1"',
            'RECEIPT_SCHEMA = "heptatrader.preflight-receipt.v1"',
            'getattr(os, "O_NOFOLLOW", 0)',
            "HARD_MAXIMUM_ARCHIVE_MEMBERS",
            "paper_authorized",
            "live_authorized",
        ),
        "RELEASE-001",
        errors,
    )
    for relative in (
        "docs/modules/release-engineering.md",
        "docs/RELEASE-PUBLICATION-SECURITY.md",
        "docs/adr/0001-release-publication-atomicity.md",
        "docs/operations/release-package.md",
        "docs/operations/preflight.md",
        "tests/python/test_release_package.py",
        "tests/python/test_hepta_preflight.py",
        "tests/python/test_cmake_install_integration.py",
    ):
        require_tokens(root, relative, ("release",), "RELEASE-001", errors)
    try:
        policy = load_json(root / "docs/preflight-policy-v1.json")
        if not isinstance(policy, dict) or policy.get("schema") != "heptatrader.preflight-policy.v1":
            errors.append("RELEASE-001: invalid preflight policy schema")
        profiles = policy.get("profiles") if isinstance(policy, dict) else None
        if not isinstance(profiles, dict) or set(profiles) != {"core", "ib-paper"}:
            errors.append("RELEASE-001: preflight policy must define exact core and ib-paper profiles")
        catalog = load_json(root / "docs/module-catalog.json")
        modules = {
            item.get("id"): item
            for item in catalog.get("modules", [])
            if isinstance(item, dict)
        } if isinstance(catalog, dict) else {}
        release = modules.get("release-engineering")
        if not isinstance(release, dict):
            errors.append("RELEASE-001: release-engineering module is missing")
        else:
            if release.get("status") != "CURRENT":
                errors.append("RELEASE-001: release-engineering module is not CURRENT")
            if release.get("broker_mutation") != "NONE":
                errors.append("RELEASE-001: release-engineering must have no Broker mutation")
            if release.get("production_authorized") is not False:
                errors.append("RELEASE-001: release engineering cannot grant production authority")
    except SourceClosureError as error:
        errors.append(f"RELEASE-001: {error}")
    return errors

def validate_register_projection(root: Path) -> list[str]:
    import check_gap_register

    # Keep external issue/authorization/evidence rules in the canonical
    # validator instead of maintaining a weaker second implementation here.
    errors = [f"canonical registry: {item}" for item in check_gap_register.validate(root)]
    try:
        register = load_json(root / "docs/gap-register.json")
    except SourceClosureError as error:
        return [str(error)]
    if not isinstance(register, dict) or register.get("schema") != (
        "heptatrader.gap-register.v1"
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
        gap_id for gap_id, item in by_id.items()
        if item.get("domain") == "REPOSITORY"
    }
    external_ids = {
        gap_id for gap_id, item in by_id.items()
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
        item = by_id.get(gap_id, {})
        if item.get("state") != "CLOSED_SOURCE":
            errors.append(f"{gap_id}: repository source state is not CLOSED_SOURCE")
    for gap_id in EXPECTED_EXTERNAL_GAPS:
        item = by_id.get(gap_id, {})
        if item.get("state") != "OPEN_EXTERNAL":
            errors.append(
                f"{gap_id}: external evidence cannot be closed by source"
            )
    authorization = register.get("authorization")
    if not isinstance(authorization, dict):
        errors.append("authorization projection is missing")
    else:
        if authorization.get("source_state") != "READY":
            errors.append("source_state must be READY after supported-scope closure")
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
    for relative in (
        ".github/workflows/refactor-apply.yml",
        ".github/workflows/source-snapshot-temporary.yml",
        ".github/workflows/recover-gap-payload.yml",
        ".github/workflows/flatten-capacity-materialize.yml",
    ):
        if (root / relative).exists():
            errors.append(
                f"CI-001: temporary workflow remains in the product tree: {relative}"
            )
    applicator = "scripts/apply_flatten_capacity_fix.py"
    if (root / applicator).exists():
        errors.append(f"BUILD-001: temporary source applicator remains in the product tree: {applicator}")
    return errors


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    try:
        errors.extend(validate_temporary_artifacts(root))
        errors.extend(f"DOC-001: {item}" for item in validate_documentation(root))
        errors.extend(f"CI-001: {item}" for item in validate_ci_boundary(root))
        errors.extend(f"TEST-001: {item}" for item in validate_test_inventory(root))
        errors.extend(validate_risk(root))
        errors.extend(validate_venue(root))
        errors.extend(validate_oms(root))
        errors.extend(f"PENDING-EXPOSURE-001: {item}" for item in validate_profile(root))
        errors.extend(validate_release(root))
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
    print("[SOURCE-GAPS] PASS registry/static contracts and delegated validators; no C++ behavioral tests executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
