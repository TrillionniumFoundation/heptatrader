#!/usr/bin/env python3
"""Run a disjoint Python CI partition, or the complete local suite.

New test files belong to core unless explicitly assigned to source, learning,
install, isolated process integration, archived workflow contracts, or optional
external native integration.
No test is dropped by a filename glob maintained in a second workflow.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_TESTS = frozenset({
    "test_protocol_reference.py",
    "test_heptadll_migration_state.py",
    "test_heptadll_source_disposition.py",
    "test_source_workflow_commands.py",
    "test_source_json.py",
    "test_development_ci.py",
    "test_documentation_links.py",
    "test_component_coverage.py",
    "test_systemd_units.py",
    "test_build_ownership.py",
    "test_gap_register.py",
    "test_canonical_ib_paper_profile.py",
    "test_ib_paper_scenario_contract.py",
    "test_risk_legacy_compatibility_boundary.py",
    "test_legacy_runtime_boundary.py",
    "test_python_test_partition.py",
})
INSTALL_TESTS = frozenset({"test_cmake_install_integration.py"})
PROCESS_TESTS = frozenset({
    "test_installed_runtime_processes.py",
    "test_oms_generation_process.py",
    "test_shadow_pipeline_integration.py",
})

# These tests execute independently built Canonical Rust fixtures, not the
# HeptaTrader core. Keep them explicit and runnable without blocking core edits.
EXTERNAL_TESTS = frozenset({
    "test_train_v2_original_native_escrow_contract.py",
    "test_train_v2_physical_native_roundtrip.py",
})

# Retired Desktop qualification fixtures remain runnable when their inputs change.
LEGACY_TESTS = frozenset({
    "test_ib_workflow_interfaces.py",
    "test_qualification_trust_boundary.py",
})

# These numerical research suites require Torch or safetensors.
LEARNING_TESTS = frozenset({
    "test_laya_frozen_price_numpy.py",
    "test_laya_resident_action_v2_research.py",
    "test_neuron256_archived_kernel_learning.py",
    "test_research_contract_smoke.py",
})


# Discover in-package fixtures as well: a second allowlist hid calendar,
# registration recovery, source and financial learner regressions from CI.
NESTED_LEARNING_MODULES = frozenset(
    "laya_cell.tests." + path.stem
    for path in (ROOT / "research/neuron_learning/laya_cell/tests").glob("test_*.py")
    if path.is_file()
)
# These classes/methods invoke separately built native fixtures. Ordinary
# learning runs import their modules but never execute these external tests.
NESTED_EXTERNAL_TESTS = frozenset({
    "laya_cell.tests.test_contracts.NativeConformance",
    "laya_cell.tests.test_resident.NativeResidentTests",
    "laya_cell.tests.test_financial_forward_trust.SelectorGateTests.test_native_accepted_boolean_cannot_substitute_certificates",
})


def external_nested_case(case: unittest.TestCase) -> bool:
    name = case.id()
    return any(name == external or name.startswith(external + ".")
               for external in NESTED_EXTERNAL_TESTS)


def learning_cases(suite: unittest.TestSuite):
    """Retain import failures and CPU cases, excluding explicit native fixtures."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from learning_cases(item)
        elif not external_nested_case(item):
            yield item


def partitions(root: Path = ROOT) -> dict[str, set[str]]:
    discovered = {p.name for p in (root / "tests/python").glob("test_*.py") if p.is_file()}
    explicit = (SOURCE_TESTS, LEARNING_TESTS, INSTALL_TESTS, PROCESS_TESTS, EXTERNAL_TESTS, LEGACY_TESTS)
    overlap = set()
    for i, group in enumerate(explicit):
        for other in explicit[i+1:]:
            overlap.update(group & other)
    missing = set.union(*(set(group) for group in explicit)) - discovered
    if overlap or missing:
        raise ValueError(f"invalid test ownership: overlap={sorted(overlap)}, missing={sorted(missing)}")
    if not discovered:
        raise ValueError("no Python test files discovered")
    return {"source": set(SOURCE_TESTS), "install": set(INSTALL_TESTS),
            "process": set(PROCESS_TESTS), "external": set(EXTERNAL_TESTS),
            "learning": set(LEARNING_TESTS), "legacy": set(LEGACY_TESTS),
            "core": discovered - SOURCE_TESTS - LEARNING_TESTS - INSTALL_TESTS - PROCESS_TESTS - EXTERNAL_TESTS - LEGACY_TESTS}


def configure_import_paths() -> None:
    """Use the same checkout imports in this runner and actual child processes."""
    roots = [str(ROOT / suffix) for suffix in
             ("research", "research/neuron_learning", "scripts", "tests/python")]
    for path in roots:
        if path not in sys.path:
            sys.path.insert(0, path)
    inherited = [part for part in os.environ.get("PYTHONPATH", "").split(os.pathsep)
                 if part and part not in roots]
    os.environ["PYTHONPATH"] = os.pathsep.join(roots + inherited)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("core", "source", "learning", "install", "process", "external", "legacy", "all"), required=True)
    parser.add_argument("--list", action="store_true", help="list selected test files without importing them")
    args = parser.parse_args(argv)
    if not args.list:
        if args.lane in ("process", "all"):
            if os.environ.get("HEPTA_ISOLATED_PROCESS_TESTS") != "1":
                parser.error("process/all requires HEPTA_ISOLATED_PROCESS_TESTS=1 on a disposable Linux host")
            if not sys.platform.startswith("linux") or getattr(os, "geteuid", lambda: -1)() != 0:
                parser.error("process/all requires the isolated Linux root fixture; never a trading host")
        if args.lane in ("install", "all") and not os.environ.get("HEPTA_RELEASE_INTEGRATION_BUILD_DIR"):
            parser.error("install/all requires HEPTA_RELEASE_INTEGRATION_BUILD_DIR")
        if args.lane in ("source", "all"):
            missing = [tool for tool in ("cmake", "ctest") if shutil.which(tool) is None]
            if missing:
                parser.error("source/all requires development tools on PATH: " + ", ".join(missing))
    try:
        groups = partitions()
    except ValueError as error:
        parser.error(str(error))
    selected = set.union(*groups.values()) if args.lane == "all" else groups[args.lane]
    nested = (NESTED_LEARNING_MODULES if args.lane in ("learning", "all") else
              NESTED_EXTERNAL_TESTS if args.lane == "external" else frozenset())
    if args.list:
        print("\n".join(sorted(selected | nested)))
        return 0
    configure_import_paths()
    loader = unittest.TestLoader()
    modules = {Path(name).stem for name in selected} | nested
    suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in sorted(modules))
    if args.lane == "learning":
        suite = unittest.TestSuite(learning_cases(suite))
    # Import failures are represented by unittest._FailedTest instances.
    # Run them with the valid modules so a broken import cannot hide the rest
    # of the lane, while the aggregate result still fails.
    if not suite.countTestCases():
        print("Selected lane contains no tests.", file=sys.stderr)
        return 1
    print(f"Python lane={args.lane}: {len(selected)} files, {len(nested)} nested modules, "
          f"{suite.countTestCases()} cases", flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print("Selected tests were skipped; this is not complete partition acceptance.", file=sys.stderr)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
