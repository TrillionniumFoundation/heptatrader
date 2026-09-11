#!/usr/bin/env python3
"""Fail-closed validation for the complete supported-scope gap register."""
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
REQUIRED_GAP_EVIDENCE: dict[str, set[str]] = {
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
        "cmake/HeptaInstall.cmake",
        "docs/BROKER-NETWORK-ISOLATION.md",
        "docs/preflight-policy-v1.json",
        "scripts/hepta_broker_egress_policy.py",
        "systemd/hepta-broker-network-policy-v1.json",
        "tests/python/test_hepta_broker_egress_policy.py",
        "tests/python/test_hepta_broker_egress_policy_atomic.py",
        "HeptaTrade/execution/ib_paper_execution_flatten_guard.cpp",
        "HeptaTrade/execution/ib_paper_authoritative_flatten.cpp",
        "tests/python/test_behavior_bound_gap_evidence.py",
        "tests/ib_paper_execution_profile_tests.cpp",
        "tests/execution_coordinator_tests.cpp",
        "docs/modules/ib-paper.md",
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
REQUIRED_BEHAVIOR_TOKENS: dict[str, tuple[str, ...]] = {
    "HeptaTrade/execution/ib_paper_execution_profile.cpp": (
        "maxOrderQuantity != maxGrossPosition",
        "IB_PAPER_QUALIFICATION_ORDER_MODE_LIMITS_INVALID",
    ),
    "HeptaTrade/execution/ib_paper_execution_flatten_guard.cpp": (
        "m_config.maxOrderQuantity",
        "IB_PAPER_EXTERNAL_FLATTEN_POSITION_LIMIT_EXCEEDED",
        "ExactReduceOnlyQuantity",
    ),
    "HeptaTrade/execution/ib_paper_authoritative_flatten.cpp": (
        "config.maxOrderQuantity",
        "PopulateNonzeroFlattenOrder",
        "plan.order.totalQuantity = std::fabs(position.quantity);",
    ),
    "tests/ib_paper_execution_profile_tests.cpp": (
        "TestQualificationEnvelopeAlwaysHasAnAtomicFlattenPath",
        "125000.0",
        "250001.0",
        "IbPaperKillSwitchState::Engaged",
        "IB_PAPER_MAX_GROSS_POSITION_EXCEEDED",
        "IB_PAPER_EXTERNAL_FLATTEN_POSITION_LIMIT_EXCEEDED",
    ),
    "tests/execution_coordinator_tests.cpp": (
        "TestQualificationExternalFlattenIsExactAndAbsolutelyBounded",
    ),
    "scripts/build_release_package.py": (
        'getattr(os, "O_NONBLOCK", 0)',
        "def _clear_nonblocking(",
    ),
    "scripts/hepta_preflight.py": (
        'getattr(os, "O_NONBLOCK", 0)',
        "def _clear_nonblocking(",
    ),
    "scripts/hepta_preflight_core.py": (
        'getattr(os, "O_NONBLOCK", 0)',
        "artifact_admitted",
        "Broker probing requires successful artifact and policy admission",
        "CANONICAL_IB_PAPER_KILL_SWITCH_PATH",
        "/run/hepta/ib-paper-control/kill-switch",
        'CANONICAL_IB_PAPER_KILL_SWITCH_CONTENT = b"engaged"',
        "def _safe_kill_switch(",
        "expected_group_gid",
    ),
    "tests/python/test_hepta_preflight.py": (
        "test_declared_preflight_regressions_are_discovered",
        "test_rejected_artifacts_never_probe_broker",
        "connect.assert_not_called()",
        "test_kill_switch_accepts_canonical_tmpfiles_marker",
        "test_kill_switch_rejects_unrelated_absolute_file",
        "test_kill_switch_rejects_valid_marker_at_wrong_path",
        "test_kill_switch_rejects_final_and_ancestor_symlinks",
        "test_kill_switch_rejects_identity_change_during_read",
        "test_ib_static_preflight_rejects_arbitrary_kill_switch",
    ),
    ".github/workflows/canonical-full-suite.yml": (
        "python3 -m unittest discover -s tests/python -p 'test_*.py'",
        "./scripts/dev_core.sh",
        "cmake --build build/reliability-gcc --target hepta_core_test_binaries",
        "ctest --test-dir build/reliability-gcc --output-on-failure -L core",
        "cmake --build build/reliability-clang --target hepta_core_test_binaries",
        "ctest --test-dir build/reliability-clang --output-on-failure -L core",
    ),
    "cmake/HeptaInstall.cmake": (
        "systemd/hepta-broker-network-policy-v1.json",
        "if(HEPTA_ENABLE_IBAPI)",
    ),
    "docs/preflight-policy-v1.json": (
        "share/heptatrader/hepta-broker-network-policy-v1.json",
    ),
    "scripts/hepta_broker_egress_policy.py": (
        "CANONICAL_POLICY_SHA256",
        "5eddd44a588ac3269804cb62adb19c3879febce8569df30ab86886028e969e6b",
        "def _open_policy_parent(",
        "def _read_bounded_policy(",
        "policy identity changed while being read",
        "policy parent or final path changed during read",
        "_apply(nft, COMPILED_POLICY, deny_all=True)",
        "APPLY_ATTEMPTS = 3",
        "RULE_COMMENTS = {",
        "def _run_nft_query(",
        "def _table_exists(",
        "def _verify_table(",
        "machine-state retries",
    ),
    "tests/python/test_hepta_broker_egress_policy.py": (
        "test_policy_digest_mismatch_is_rejected",
        "test_policy_in_place_mutation_during_read_is_rejected",
        "test_policy_final_replacement_during_read_is_rejected",
        "test_policy_parent_substitution_during_read_is_rejected",
        "test_policy_read_failure_attempts_compiled_deny_all",
        "test_allow_apply_failure_attempts_compiled_deny_all",
        "test_explicit_deny_all_does_not_read_policy",
    ),
    "tests/python/test_hepta_broker_egress_policy_atomic.py": (
        "test_table_presence_uses_json_inventory_not_diagnostics",
        "test_localized_failure_reprobes_and_replaces",
        "test_present_to_absent_race_is_retried_without_text_matching",
        "test_command_failure_is_accepted_only_after_exact_readback",
        "test_unverified_state_fails_after_bounded_attempts",
        "test_structural_readback_accepts_exact_allow_and_deny",
        "test_structural_readback_rejects_extra_permissive_rule",
        "test_double_apply_failure_reports_unverified_fallback",
    ),
    "tmpfiles.d/heptatrader-ib-paper.conf": (
        "/run/hepta/ib-paper-control",
        "kill-switch 0440 root hepta-ib-exec",
        "engaged",
    ),
    "tests/python/test_preflight_special_files.py": (
        "test_fifo_policy_is_rejected_without_blocking_or_receipt",
        "test_fifo_artifact_is_rejected_without_blocking_or_pass_receipt",
        "test_fifo_installed_leaf_is_rejected_without_blocking",
        "test_regular_to_fifo_builder_swap_is_rejected_without_blocking",
        "test_fifo_preflight_core_is_rejected_without_blocking",
    ),
}
FORBIDDEN_BEHAVIOR_TOKENS: dict[str, tuple[str, ...]] = {
    "scripts/hepta_broker_egress_policy.py": ("File exists",),
}
ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,63}$")


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


def canonical_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise GapRegisterError(f"{label}: invalid repository-relative path")
    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise GapRegisterError(f"{label}: non-canonical path: {value!r}")
    path = root / relative
    try:
        metadata = path.lstat()
    except OSError as error:
        raise GapRegisterError(f"{label}: missing evidence {value}: {error}") from error
    if path.is_symlink() or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise GapRegisterError(f"{label}: evidence is not a regular file/directory: {value}")
    return relative


def validate_behavior_evidence(root: Path, observed: dict[str, dict[str, Any]]) -> None:
    for gap_id, required in REQUIRED_GAP_EVIDENCE.items():
        evidence = observed.get(gap_id, {}).get("evidence", [])
        present = set(evidence) if isinstance(evidence, list) else set()
        missing = sorted(required - present)
        if missing:
            raise GapRegisterError(
                f"{gap_id}: missing required behavior evidence: " + ", ".join(missing)
            )

    for relative, tokens in REQUIRED_BEHAVIOR_TOKENS.items():
        text = read_text(root / relative)
        missing = [token for token in tokens if token not in text]
        if missing:
            raise GapRegisterError(
                f"behavior evidence {relative}: missing contract tokens: "
                + ", ".join(repr(token) for token in missing)
            )

    for relative, tokens in FORBIDDEN_BEHAVIOR_TOKENS.items():
        text = read_text(root / relative)
        present = [token for token in tokens if token in text]
        if present:
            raise GapRegisterError(
                f"behavior evidence {relative}: forbidden diagnostic protocol tokens: "
                + ", ".join(repr(token) for token in present)
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
            raise GapRegisterError("source_state must be READY after all supported-scope gaps close")
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
            if not isinstance(gap.get("summary"), str) or not gap["summary"].strip():
                raise GapRegisterError(f"{gap_id}: non-empty summary required")
            if not isinstance(gap.get("blocking_authorization"), bool):
                raise GapRegisterError(f"{gap_id}: blocking_authorization must be boolean")
            evidence = gap.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise GapRegisterError(f"{gap_id}: evidence must be a non-empty array")
            if any(not isinstance(item, str) for item in evidence) or len(evidence) != len(set(evidence)):
                raise GapRegisterError(f"{gap_id}: evidence paths must be unique strings")
            for evidence_index, value in enumerate(evidence):
                canonical_path(root, value, f"{gap_id}.evidence[{evidence_index}]")

            domain = gap.get("domain")
            state = gap.get("state")
            issue = gap.get("issue")
            if domain == "REPOSITORY":
                if state != "CLOSED_SOURCE":
                    raise GapRegisterError(f"{gap_id}: repository-controlled gap must be closed in this candidate")
                if issue is not None:
                    raise GapRegisterError(f"{gap_id}: closed source gap must not delegate closure")
                if gap["blocking_authorization"]:
                    raise GapRegisterError(f"{gap_id}: closed source gap cannot remain an authorization blocker")
            elif domain == "EXTERNAL":
                if state != "OPEN_EXTERNAL":
                    raise GapRegisterError(f"{gap_id}: source cannot mark an external control closed")
                expected_issue = REQUIRED_EXTERNAL_GAPS.get(gap_id)
                if expected_issue is None or issue != expected_issue:
                    raise GapRegisterError(f"{gap_id}: external issue binding is invalid")
                if not gap["blocking_authorization"]:
                    raise GapRegisterError(f"{gap_id}: external gap must block authorization")
            else:
                raise GapRegisterError(f"{gap_id}: invalid domain {domain!r}")

        missing_repository = sorted(REQUIRED_REPOSITORY_GAPS - set(observed))
        if missing_repository:
            raise GapRegisterError("missing repository gaps: " + ", ".join(missing_repository))
        unexpected = sorted(set(observed) - REQUIRED_REPOSITORY_GAPS)
        if unexpected:
            raise GapRegisterError("unexpected gaps in supported source register: " + ", ".join(unexpected))
        missing_external = sorted(set(REQUIRED_EXTERNAL_GAPS) - set(observed))
        if missing_external:
            raise GapRegisterError("missing external gaps: " + ", ".join(missing_external))

        validate_behavior_evidence(root, observed)

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

        catalog = load_json(root / "docs/module-catalog.json")
        modules = {
            item.get("id"): item
            for item in catalog.get("modules", [])
            if isinstance(item, dict)
        } if isinstance(catalog, dict) else {}
        if modules.get("ib-paper", {}).get("production_authorized") is not False:
            raise GapRegisterError("ib-paper: Broker qualification requires production_authorized=false")
        ib_tests = set(modules.get("ib-paper", {}).get("tests", []))
        if "tests/ib_paper_execution_profile_tests.cpp" not in ib_tests:
            raise GapRegisterError("ib-paper: atomic-flatten regression is missing from the module catalog")
        if "tests/python/test_hepta_broker_egress_policy_atomic.py" not in ib_tests:
            raise GapRegisterError("ib-paper: atomic nftables regression is missing from the module catalog")
        deployment_tests = set(modules.get("deployment", {}).get("tests", []))
        if "tests/python/test_hepta_broker_egress_policy_atomic.py" not in deployment_tests:
            raise GapRegisterError("deployment: atomic nftables regression is missing from the module catalog")
    except GapRegisterError as error:
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
