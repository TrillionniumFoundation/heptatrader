#!/usr/bin/env python3
"""Validate supported-scope gap closure from behavior-bearing source evidence.

This validator separates three concerns:

* repository evidence ownership and authorization truth;
* compact behavior-bound source invariants whose removal would invalidate a
  CLOSED_SOURCE claim;
* CI responsibility separation, so behavior is executed once by its owning
  lane instead of being duplicated as process ceremony.

It intentionally does not require compatibility workflows to repeat source or
behavior checks owned by another lane.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTER_KEYS = {"schema", "authorization", "gaps"}
AUTHORIZATION_KEYS = {"source_state", "paper_authorized", "live_authorized"}
GAP_KEYS = {
    "id",
    "domain",
    "state",
    "blocking_authorization",
    "summary",
    "evidence",
    "issue",
}
REQUIRED_REPOSITORY_GAPS = {
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
REQUIRED_EXTERNAL_GAPS: dict[str, str] = {}
ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,63}$")

# Catalog/index ownership anchors. These prove that an evidence family is
# registered; behavior-sensitive evidence below is checked separately so a
# generic ownership error cannot hide a missing safety contract.
REQUIRED_EVIDENCE: dict[str, set[str]] = {
    "DOC-001": {
        "docs/DOCUMENTATION-POLICY.md",
        "docs/module-catalog.json",
        "docs/DEVELOPMENT-DOCUMENTATION-INDEX.md",
        "scripts/check_documentation.py",
        "scripts/check_component_coverage.py",
    },
    "CI-001": {
        ".github/workflows/core-ci.yml",
        ".github/workflows/canonical-full-suite.yml",
        ".github/workflows/documentation-control-plane.yml",
    },
    "TEST-001": {
        "docs/build-targets.json",
        "tests/agent_simulator_e2e_tests.cpp",
        "tests/execution_coordinator_tests.cpp",
        "tests/python/test_behavior_bound_gap_evidence.py",
    },
    "RISK-001": {
        "HeptaTrade/risk/pre_trade_risk_engine.cpp",
        "tests/pre_trade_risk_engine_tests.cpp",
        "docs/modules/risk-engine.md",
    },
    "PENDING-EXPOSURE-001": {
        "HeptaTrade/execution/ib_paper_execution_profile.cpp",
        "HeptaTrade/execution/ib_paper_execution_flatten_guard.cpp",
        "HeptaTrade/execution/ib_paper_authoritative_flatten.cpp",
        "tests/execution_coordinator_tests.cpp",
        "scripts/hepta_broker_egress_policy.py",
    },
    "VENUE-001": {
        "HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp",
        "HeptaTrade/adapter_xt/xt_gateway_adapter.cpp",
        "tests/venue_capability_tests.cpp",
    },
    "OMS-001": {
        "HeptaTrade/execution/execution_place_order_dispatch.cpp",
        "tests/oms_journal_durability_tests.cpp",
        "tests/oms_journal_schema_v4_tests.cpp",
        "docs/OMS-EVENT-SCHEMA.md",
    },
    "BUILD-001": {
        "docs/build-targets.json",
        "scripts/verify_build_ownership.py",
        "scripts/check_component_coverage.py",
        "tests/python/test_build_ownership.py",
    },
    "RELEASE-001": {
        "cmake/HeptaInstall.cmake",
        "scripts/build_release_package.py",
        "scripts/hepta_preflight.py",
        "scripts/hepta_preflight_core.py",
        "scripts/run_release_simulator_smoke.py",
        "tests/python/test_release_simulator_smoke.py",
    },
}

# Evidence whose *presence in the register* is itself part of a source-closure
# claim. Tests intentionally mutate these arrays to ensure closure cannot be
# asserted after deleting the corresponding hostile regression.
REQUIRED_BEHAVIOR_EVIDENCE: dict[str, set[str]] = {
    "TEST-001": {
        "tests/python/test_behavior_bound_gap_evidence.py",
        "tests/python/test_hepta_preflight.py",
        "tests/python/test_hepta_broker_egress_policy.py",
        "tests/python/test_hepta_broker_egress_policy_atomic.py",
        "tests/ib_paper_execution_profile_tests.cpp",
        "tests/execution_coordinator_tests.cpp",
    },
    "PENDING-EXPOSURE-001": {
        "HeptaTrade/execution/ib_paper_execution_profile.cpp",
        "HeptaTrade/execution/ib_paper_execution_flatten_guard.cpp",
        "HeptaTrade/execution/ib_paper_authoritative_flatten.cpp",
        "tests/ib_paper_execution_profile_tests.cpp",
        "tests/execution_coordinator_tests.cpp",
        "scripts/hepta_broker_egress_policy.py",
        "tests/python/test_hepta_broker_egress_policy.py",
        "tests/python/test_hepta_broker_egress_policy_atomic.py",
        "tests/python/test_behavior_bound_gap_evidence.py",
    },
    "RELEASE-001": {
        "scripts/build_release_package.py",
        "scripts/hepta_preflight.py",
        "scripts/hepta_preflight_core.py",
        "tests/python/test_hepta_preflight.py",
        "tests/python/test_preflight_special_files.py",
        "tests/python/test_cmake_install_integration.py",
        "tmpfiles.d/heptatrader-ib-paper.conf",
    },
}

# Narrow source anchors for properties that cannot be inferred from file
# existence alone. These deliberately avoid CI-layout tokens: the runtime/core
# tests execute the behavior, while this list prevents the source contract from
# silently losing the mechanism those tests are meant to exercise.
class GapRegisterError(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GapRegisterError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise GapRegisterError(f"non-finite JSON number: {value}")


def load_json(path: Path) -> Any:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise GapRegisterError(f"{path}: expected a regular single-link file")
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GapRegisterError(f"cannot load {path}: {error}") from error


def read_text(path: Path) -> str:
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise GapRegisterError(f"{path}: expected a regular single-link file")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise GapRegisterError(f"cannot read {path}: {error}") from error


def canonical_evidence(root: Path, value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise GapRegisterError(f"{label}: invalid repository-relative evidence path")
    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise GapRegisterError(f"{label}: non-canonical evidence path: {value!r}")
    path = root / relative
    try:
        metadata = path.lstat()
    except OSError as error:
        raise GapRegisterError(f"{label}: missing evidence {value}: {error}") from error
    if path.is_symlink() or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise GapRegisterError(f"{label}: evidence is not a regular file/directory: {value}")
    return relative.as_posix()


def validate_ci_roles(root: Path) -> None:
    """Require real, failure-propagating jobs; never accept comment anchors."""
    from ci_workflow_contract import validate as validate_workflows
    errors = validate_workflows(root)
    if errors:
        raise GapRegisterError("; ".join(errors))


def validate_behavior_evidence(root: Path, observed: dict[str, dict[str, Any]]) -> None:
    for gap_id, required in REQUIRED_BEHAVIOR_EVIDENCE.items():
        evidence = observed.get(gap_id, {}).get("evidence", [])
        present = set(evidence) if isinstance(evidence, list) else set()
        missing = sorted(required - present)
        if missing:
            raise GapRegisterError(
                f"{gap_id}: missing required behavior evidence: " + ", ".join(missing)
            )

    inventory = load_json(root / "docs/build-targets.json")
    try:
        targets = {
            item["name"]: item
            for item in inventory["profiles"]["core"]["targets"]
        }
    except (KeyError, TypeError) as error:
        raise GapRegisterError("build target inventory has an invalid core profile") from error

    required_target = "hepta_ib_paper_execution_profile_tests"
    target = targets.get(required_target)
    if not isinstance(target, dict) or target.get("type") != "EXECUTABLE":
        raise GapRegisterError(
            f"TEST-001: required executable target is missing: {required_target}"
        )

    aggregate = targets.get("hepta_core_test_binaries")
    if not isinstance(aggregate, dict):
        raise GapRegisterError("TEST-001: core test aggregate is missing")
    reachable: set[str] = set()
    pending = list(aggregate.get("dependencies", []))
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        dependency = targets.get(name)
        if not isinstance(dependency, dict):
            raise GapRegisterError(f"TEST-001: unknown build dependency: {name}")
        pending.extend(dependency.get("dependencies", []))
    if required_target not in reachable:
        raise GapRegisterError(
            "TEST-001: V5 atomic-flatten test target is not reachable from hepta_core_test_binaries"
        )

    catalog = load_json(root / "docs/module-catalog.json")
    modules = {
        item.get("id"): item
        for item in catalog.get("modules", [])
        if isinstance(item, dict)
    } if isinstance(catalog, dict) else {}
    ib_tests = set(modules.get("ib-paper", {}).get("tests", []))
    deployment_tests = set(modules.get("deployment", {}).get("tests", []))
    atomic = "tests/python/test_hepta_broker_egress_policy_atomic.py"
    if atomic not in ib_tests:
        raise GapRegisterError("ib-paper: atomic nftables regression is missing from the module catalog")
    if atomic not in deployment_tests:
        raise GapRegisterError("deployment: atomic nftables regression is missing from the module catalog")


def validate_paper_scenario_contract(root: Path) -> None:
    """Bind the reviewable PAPER scenario contract to executable verification."""
    import verify_ib_paper_qualification as verifier

    value = load_json(root / "docs/ib-paper-qualification-scenarios-v1.json")
    if not isinstance(value, dict) or set(value) != {"schema", "purpose", "scenarios"}:
        raise GapRegisterError("IB PAPER scenario contract fields are not canonical")
    if value.get("schema") != "heptatrader.ib-paper-scenario-contract.v1":
        raise GapRegisterError("unsupported IB PAPER scenario contract schema")
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, list):
        raise GapRegisterError("IB PAPER scenario contract scenarios must be an array")
    ids = [item.get("id") for item in scenarios if isinstance(item, dict)]
    if ids != list(verifier.REQUIRED_SCENARIOS):
        raise GapRegisterError("reviewable IB PAPER scenarios drift from executable verifier")
    for item in scenarios:
        if not isinstance(item, dict) or set(item) != {
            "id", "objective", "required_assertions", "required_evidence_kinds"
        }:
            raise GapRegisterError("IB PAPER scenario entry fields are not canonical")
        scenario_id = item["id"]
        if set(item["required_assertions"]) != set(verifier.REQUIRED_ASSERTIONS[scenario_id]):
            raise GapRegisterError(f"{scenario_id}: assertion contract drift")
        if set(item["required_evidence_kinds"]) != set(verifier.REQUIRED_EVIDENCE_KINDS[scenario_id]):
            raise GapRegisterError(f"{scenario_id}: evidence-kind contract drift")


def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    try:
        register = load_json(root / "docs/gap-register.json")
        if not isinstance(register, dict) or set(register) != REGISTER_KEYS:
            raise GapRegisterError("gap register fields are not canonical")
        if register.get("schema") != "heptatrader.gap-register.v1":
            raise GapRegisterError("unsupported gap register schema")

        authorization = register.get("authorization")
        if not isinstance(authorization, dict) or set(authorization) != AUTHORIZATION_KEYS:
            raise GapRegisterError("authorization fields are not canonical")
        if authorization.get("source_state") != "READY":
            raise GapRegisterError("source_state must be READY after supported-scope closure")
        if authorization.get("paper_authorized") is not False:
            raise GapRegisterError("PAPER cannot be source-authorized")
        if authorization.get("live_authorized") is not False:
            raise GapRegisterError("LIVE must remain unauthorized")

        gaps = register.get("gaps")
        if not isinstance(gaps, list) or not gaps:
            raise GapRegisterError("gaps must be a non-empty array")
        observed: dict[str, dict[str, Any]] = {}
        for index, gap in enumerate(gaps):
            label = f"gap[{index}]"
            if not isinstance(gap, dict) or set(gap) != GAP_KEYS:
                raise GapRegisterError(f"{label}: fields are not canonical")
            gap_id = gap.get("id")
            if not isinstance(gap_id, str) or ID_RE.fullmatch(gap_id) is None:
                raise GapRegisterError(f"{label}: invalid gap id")
            if gap_id in observed:
                raise GapRegisterError(f"{label}: duplicate gap id {gap_id}")
            observed[gap_id] = gap
            if gap.get("domain") != "REPOSITORY":
                raise GapRegisterError(f"{gap_id}: supported-scope register may contain REPOSITORY gaps only")
            if gap.get("state") != "CLOSED_SOURCE":
                raise GapRegisterError(f"{gap_id}: repository gap must be CLOSED_SOURCE")
            if gap.get("blocking_authorization") is not False:
                raise GapRegisterError(f"{gap_id}: closed source gap cannot block authorization")
            if gap.get("issue") is not None:
                raise GapRegisterError(f"{gap_id}: closed source gap must not depend on an open issue")
            if not isinstance(gap.get("summary"), str) or not gap["summary"].strip():
                raise GapRegisterError(f"{gap_id}: non-empty summary required")
            evidence = gap.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise GapRegisterError(f"{gap_id}: non-empty evidence array required")
            canonical = [canonical_evidence(root, item, f"{gap_id}.evidence") for item in evidence]
            if len(canonical) != len(set(canonical)):
                raise GapRegisterError(f"{gap_id}: duplicate evidence path")

        observed_ids = set(observed)
        if observed_ids != REQUIRED_REPOSITORY_GAPS:
            raise GapRegisterError(
                "repository gap set mismatch; missing="
                + repr(sorted(REQUIRED_REPOSITORY_GAPS - observed_ids))
                + " extra="
                + repr(sorted(observed_ids - REQUIRED_REPOSITORY_GAPS))
            )

        validate_behavior_evidence(root, observed)

        for gap_id, required in REQUIRED_EVIDENCE.items():
            present = set(observed[gap_id]["evidence"])
            missing = sorted(required - present)
            if missing:
                raise GapRegisterError(
                    f"{gap_id}: missing ownership/evidence anchors: " + ", ".join(missing)
                )

        capabilities = load_json(root / "docs/capabilities.json")
        if not isinstance(capabilities, dict):
            raise GapRegisterError("capability matrix is invalid")
        if capabilities.get("live_trading_authorized") is not False:
            raise GapRegisterError("capability matrix must keep LIVE disabled")
        by_id = {
            item.get("id"): item
            for item in capabilities.get("capabilities", [])
            if isinstance(item, dict)
        }
        if by_id.get("ib-paper", {}).get("status") != "QUALIFICATION_REQUIRED":
            raise GapRegisterError("IB PAPER must remain qualification-required")
        if by_id.get("live", {}).get("status") != "UNAVAILABLE":
            raise GapRegisterError("LIVE must remain unavailable")

        validate_ci_roles(root)
        validate_paper_scenario_contract(root)
    except (GapRegisterError, KeyError, TypeError, ValueError) as error:
        errors.append(str(error))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    errors = validate(args.root)
    for error in errors:
        print(f"[GAPS] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[GAPS] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
