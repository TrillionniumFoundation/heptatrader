#!/usr/bin/env python3
"""Scope routine CI to the changed components; uncertain changes run every lane.

Repository docs, research READMEs and identified observation reports are prose.
Other research contracts/configuration keep full feedback. Verified operator tools
run every core/source Python test. Ordinary Python changes in the Laya and
historical-research packages also run the complete learning lane. Package tests
are discovered, not registered one filename/version at a time. Native interfaces,
shared contracts, build/install inputs and uncertain changes retain full CI.
"""
from __future__ import annotations
import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")

# Observation reports use stable document categories, not a per-experiment
# allowlist. Contract/configuration names retain full feedback even when a
# report suffix is appended; unknown research Markdown does too.
RESEARCH_REPORT = re.compile(
    r"[A-Z0-9][A-Z0-9_-]*_(?:PHYSICAL|AUDIT|CLOSEOUT|FORENSICS|"
    r"CONVERGENCE|OBSERVATIONS|REPORT)_20[0-9]{6}\.md")
RESEARCH_CONTRACT = re.compile(
    r"(?:^|_)(?:CONTRACT|CONFIG|CONFIGURATION|SCHEMA|PROTOCOL|"
    r"SPEC|SPECIFICATION|MANIFEST|REQUIREMENTS)(?:_|\.)")

ROOT = Path(__file__).resolve().parents[1]
VERIFIED_PYTHON_GROUPS = (
    frozenset({"scripts/hepta_nav_assurance.py", "tests/python/test_nav_assurance.py",
               "tests/python/test_account_observation.py"}),
    frozenset({"scripts/hepta_pnl_report.py", "tests/python/test_pnl_cli.py",
               "tests/python/test_account_observation.py"}),
    frozenset({"scripts/hepta_maintenance_latch.py", "tests/python/test_hepta_maintenance_latch.py"}),
    # These operators inspect/control existing release identities; they do not
    # build native code or import the learning/model runtime.
    frozenset({"scripts/activate_prepared_driver.py", "tests/python/test_activate_prepared_driver.py"}),
    frozenset({"scripts/hepta_runtime_identity.py", "tests/python/test_runtime_identity.py",
               "scripts/activate_prepared_driver.py", "tests/python/test_activate_prepared_driver.py"}),
    frozenset({"scripts/hepta_verified_loader.py", "tests/python/test_verified_python_loader.py"}),
    frozenset({"scripts/install_decision_attribution.py",
               "tests/python/test_decision_attribution_installer_readonly.py",
               "tests/python/test_decision_attribution_prepare_upgrade.py"}),
    frozenset({"scripts/hepta_progress.py", "tests/python/test_hepta_progress.py"}),
    # The OMS report is stdlib-only; account PnL consumes only the pure Python
    # accounting module. Keep the complete core/source behavior suites, and
    # recheck this actual import boundary before omitting native/learning work.
    frozenset({"scripts/hepta_oms_report.py", "tests/python/test_oms_operational_report.py",
               "scripts/hepta_account_pnl.py", "tests/python/test_account_pnl.py",
               "scripts/hepta_pnl_accounting.py", "tests/python/test_pnl_accounting.py"}),
)

# These support files must exist, but changing one does not inherit the helper's
# exemption. Keep import checks local to the verified Python entry points.
VERIFIED_HELPER_DEPENDENCIES = {
    "scripts/hepta_oms_report.py": (
        frozenset(),
        frozenset({"tests/python/test_oms_operational_report.py",
                   "tests/python/test_oms_capacity.py",
                   "tests/python/test_oms_observation_faults.py"}),
    ),
    "scripts/hepta_account_pnl.py": (
        frozenset({"hepta_pnl_accounting"}),
        frozenset({"scripts/hepta_pnl_accounting.py",
                   "tests/python/test_account_pnl.py", "tests/python/test_pnl_accounting.py"}),
    ),
    "scripts/hepta_pnl_accounting.py": (
        frozenset(),
        frozenset({"scripts/hepta_account_pnl.py", "scripts/hepta_pnl_report.py",
                   "scripts/heptatrader_paper_driver.py",
                   "tests/python/test_pnl_accounting.py", "tests/python/test_pnl_cli.py",
                   "tests/python/test_paper_driver.py"}),
    ),
    "scripts/hepta_progress.py": (
        frozenset({"hepta_maintenance_latch"}),
        frozenset({"scripts/heptatrader-user", "cmake/HeptaInstall.cmake",
                   "tests/python/test_cmake_install_behavior.py",
                   "scripts/hepta_maintenance_latch.py", "tests/python/test_hepta_maintenance_latch.py"}),
    ),
    "scripts/hepta_verified_loader.py": (
        frozenset(),
        frozenset({
            "scripts/heptatrader_attributed_driver.py",
            "scripts/heptatrader_btc_economics_driver.py",
            "scripts/heptatrader_paper_driver.py",
            "scripts/hepta_btc_entry_policy.py",
            "scripts/hepta_decision_attribution.py",
            "scripts/install_decision_attribution.py",
            "scripts/install_btc_entry_runtime.py",
            "research/cost_aware/scripts/hepta_cost_driver.py",
            "cmake/HeptaInstall.cmake",
            "tests/python/test_attributed_driver_launcher.py",
            "tests/python/test_btc_entry_economics.py",
            "tests/python/test_cost_research.py",
            "tests/python/test_cmake_install_behavior.py",
            "tests/python/test_development_ci.py",
        }),
    ),
    "scripts/install_decision_attribution.py": (
        frozenset({"hepta_verified_loader", "hepta_decision_attribution", "activate_prepared_driver"}),
        frozenset({
            "scripts/hepta_verified_loader.py",
            "scripts/hepta_decision_attribution.py",
            "scripts/activate_prepared_driver.py",
            "scripts/heptatrader_attributed_driver.py",
            "scripts/heptatrader_paper_driver.py",
            "scripts/heptatrader_btc_economics_driver.py",
            "scripts/hepta_btc_entry_policy.py",
            "tests/python/test_verified_python_loader.py",
            "tests/python/test_activate_prepared_driver.py",
            "tests/python/test_runtime_identity.py",
        }),
    ),
}


def _helper_imports_known(root: Path, path: str, local_imports: frozenset[str]) -> bool:
    tree = ast.parse((root / path).read_bytes(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                return False
            names = [node.module or ""]
        else:
            continue
        if any(name.split(".", 1)[0] not in sys.stdlib_module_names
               and name not in local_imports for name in names):
            return False
    return True

# Python consumers of existing native interfaces keep every Python/source and
# learning test. The package owns discovery; adding a normal module or test does
# not require another CI filename registration. New subpackages remain unknown.
PYTHON_LEARNING_PACKAGES = {
    "research/neuron_learning/laya_cell": (
        "scripts/run_python_tests.py",
        "research/neuron_learning/laya_cell/native.py",
        "research/neuron_learning/laya_cell/integrity.py",
    ),
    "research/historical_training": (
        "scripts/run_python_tests.py", "tests/python/test_historical_training.py",
        "research/historical_training/common.py",
    ),
}
# These names describe shared interfaces/installation, not ordinary consumers.
# Non-Python inputs (Rust/C++/CMake/lockfiles/configuration) never enter this rule.
PYTHON_BOUNDARY = re.compile(
    r"(?:^|_)(?:build|install|integrate|native|integrity|protocol|schema|"
    r"contracts?|owner|egress|hip)(?:_|$)")

# Cross-package operator/observer dependencies remain explicit. Research package
# members and their in-package tests no longer need per-version lists here.
VERIFIED_PYTHON_LEARNING_GROUPS = (
    frozenset({
        "scripts/hepta_pnl_watch.py", "scripts/hepta_change_watch.py",
        "tests/python/test_pnl_watch_regression.py", "tests/python/test_pnl_append_ledger.py",
        "tests/python/test_pnl_bounded_view.py", "tests/python/test_pnl_journal_cache.py",
    }),
    frozenset({
        "scripts/hepta_change_watch.py",
        "research/neuron_learning/laya_cell/source_readiness.py",
        "research/neuron_learning/laya_cell/verified_sealed_readiness_cache.py",
        "research/neuron_learning/laya_cell/readiness_heartbeat.py",
        "tests/python/test_verified_sealed_readiness_cache.py",
        "tests/python/test_source_original_proof_cache.py",
        "tests/python/test_fx_source_monitor_cadence.py", "tests/python/test_readiness_heartbeat.py",
    }),
    frozenset({
        "tests/python/test_laya_training_cadence.py", "tests/python/test_laya_training_window.py",
        "research/neuron_learning/laya_cell/resident.py",
    }),
    frozenset({
        "tests/python/test_value_successor_evaluator.py",
        "research/historical_training/value_successor_evaluator.py",
        "research/historical_training/value_ledger_stream.py",
    }),
    frozenset({
        "tests/python/test_consumer_checkpoint.py", "tests/python/test_consumer_runtime_inventory.py",
        "research/historical_training/consumer_checkpoint.py",
        "research/historical_training/consumer_launch.py",
        "research/historical_training/current100k.py",
        "research/historical_training/export_current100k.py",
        "research/neuron_learning/laya_cell/financial_value_data.py",
    }),
)


def learning_package(path: str) -> str | None:
    for package in PYTHON_LEARNING_PACKAGES:
        if not path.startswith(package + "/"):
            continue
        relative = path[len(package) + 1:]
        parts = relative.split("/")
        if (not relative.endswith(".py") or
                not (len(parts) == 1 or len(parts) == 2 and parts[0] == "tests"
                     and parts[1].startswith("test_"))):
            return None
        stem = Path(parts[-1]).stem
        if stem in {"__init__", "common"} or PYTHON_BOUNDARY.search(stem):
            return None
        return package
    return None


def regular_source(root: Path, path: str) -> bool:
    candidate = root / path
    return candidate.resolve() == candidate and candidate.is_file()


def docs_only(paths: list[str]) -> bool:
    if not paths:
        return False
    for path in paths:
        components = path.split("/")
        if (path.startswith("/") or "\\" in path or
                any(not p or p in (".", "..") for p in components)):
            return False
        if path in (
                "README.md",
                "research/historical_training/RESIDENT_RESOURCE_GUARD.md"):
            continue
        if len(components) >= 2 and components[0] == "research":
            name = components[-1]
            if name == "README.md" or (
                    RESEARCH_REPORT.fullmatch(name) and
                    not RESEARCH_CONTRACT.search(name)):
                continue
            return False
        if len(components) < 2 or components[0] != "docs" or not path.endswith(".md"):
            return False
    return True


def scope_for_paths(paths: list[str] | None, root: Path = ROOT) -> dict[str, bool]:
    full = {"run_native": True, "run_python": True, "run_learning": True}
    if not paths or any(path.startswith("/") or "\\" in path or "\0" in path or
                        any(part in {"", ".", ".."} for part in path.split("/"))
                        for path in paths):
        return full
    if docs_only(paths):
        return {key: False for key in full}
    root = root.resolve()
    changed = [path for path in paths if not docs_only([path])]
    groups = VERIFIED_PYTHON_GROUPS + VERIFIED_PYTHON_LEARNING_GROUPS
    allowed = frozenset.union(*groups)
    packages = {learning_package(path) for path in changed}
    if any(path not in allowed and learning_package(path) is None for path in changed):
        return full
    packages.discard(None)
    needed = set(changed)
    for group in groups:
        if group.intersection(changed):
            needed.update(group)
    helpers = [path for path in VERIFIED_HELPER_DEPENDENCIES if path in needed]
    for path in helpers:
        needed.update(VERIFIED_HELPER_DEPENDENCIES[path][1])
    try:
        for package in packages:
            needed.update(PYTHON_LEARNING_PACKAGES[package])
            tests = list((root / package / "tests").glob("test_*.py"))
            if not tests:
                return full
            needed.update(str(path.relative_to(root)) for path in tests)
        # Deleted/retyped paths and missing test discovery are uncertain. A Git
        # rename includes both sides, so moving outside a package remains full.
        if any(not regular_source(root, path) for path in needed):
            return full
        for path in changed:
            if path.endswith(".py"):
                ast.parse((root / path).read_bytes(), filename=path)
        if any(not _helper_imports_known(root, path, VERIFIED_HELPER_DEPENDENCIES[path][0])
               for path in helpers):
            return full
    except (OSError, SyntaxError, ValueError, UnicodeError, RuntimeError):
        return full
    return {
        "run_native": False,
        "run_python": True,
        "run_learning": bool(packages) or any(group.intersection(changed)
                                              for group in VERIFIED_PYTHON_LEARNING_GROUPS),
    }


def diff_paths(base: str) -> list[str] | None:
    if not SHA.fullmatch(base) or base == "0" * 40:
        return None
    try:
        data = subprocess.check_output(
            ["git", "diff", "--no-renames", "--name-only", "-z",
             "--diff-filter=ACDMRTUXB", base, "HEAD", "--"], timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if not data or not data.endswith(b"\0"):
        return None
    try:
        return [part.decode("utf-8", errors="strict") for part in data[:-1].split(b"\0")]
    except UnicodeDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="40-hex event base/previous SHA")
    parser.add_argument("--format", choices=("text", "github"), default="text")
    args = parser.parse_args(argv)
    names = diff_paths(args.base)
    scope = scope_for_paths(names)
    if args.format == "github":
        for name, enabled in scope.items():
            print(f"{name}={str(enabled).lower()}")
        return 0
    if not scope["run_python"]:
        print("DOCS_ONLY: preserve documentation checks.")
        return 0
    if scope["run_native"]:
        print("FULL_SOURCE_TESTS: source, config, unknown or unverified diff.")
    elif scope["run_learning"]:
        print("PYTHON_AND_LEARNING: all core/source and learning tests.")
    else:
        print("PYTHON_TOOLS: all core/source tests.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
