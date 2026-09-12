#!/usr/bin/env python3
"""Run a disjoint Python CI partition, or the complete local suite.

New test files belong to core unless explicitly assigned to source, install or isolated process integration.
No test is dropped by a filename glob maintained in a second workflow.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_TESTS = frozenset({
    "test_documentation_control_plane.py",
    "test_documentation_structure.py",
    "test_component_coverage.py",
    "test_systemd_units.py",
    "test_build_ownership.py",
    "test_gap_register.py",
    "test_behavior_bound_gap_evidence.py",
    "test_canonical_ib_paper_profile.py",
    "test_ib_paper_scenario_contract.py",
    "test_risk_legacy_compatibility_boundary.py",
    "test_legacy_runtime_boundary.py",
    "test_python_test_partition.py",
})
INSTALL_TESTS = frozenset({"test_cmake_install_integration.py"})
PROCESS_TESTS = frozenset({"test_installed_runtime_processes.py"})


def partitions(root: Path = ROOT) -> dict[str, set[str]]:
    discovered = {p.name for p in (root / "tests/python").glob("test_*.py") if p.is_file()}
    overlap = (SOURCE_TESTS & INSTALL_TESTS) | (SOURCE_TESTS & PROCESS_TESTS) | (INSTALL_TESTS & PROCESS_TESTS)
    missing = (SOURCE_TESTS | INSTALL_TESTS | PROCESS_TESTS) - discovered
    if overlap or missing:
        raise ValueError(f"invalid test ownership: overlap={sorted(overlap)}, missing={sorted(missing)}")
    if not discovered:
        raise ValueError("no Python test files discovered")
    return {"source": set(SOURCE_TESTS), "install": set(INSTALL_TESTS),
            "process": set(PROCESS_TESTS),
            "core": discovered - SOURCE_TESTS - INSTALL_TESTS - PROCESS_TESTS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("core", "source", "install", "process", "all"), required=True)
    parser.add_argument("--list", action="store_true", help="list selected test files without importing them")
    args = parser.parse_args(argv)
    if args.lane == "process" and not args.list and os.environ.get("HEPTA_ISOLATED_PROCESS_TESTS") != "1":
        parser.error("process lane requires HEPTA_ISOLATED_PROCESS_TESTS=1 on a disposable Linux host")
    try:
        groups = partitions()
    except ValueError as error:
        parser.error(str(error))
    selected = set.union(*groups.values()) if args.lane == "all" else groups[args.lane]
    if args.list:
        print("\n".join(sorted(selected)))
        return 0
    sys.path.insert(0, str(ROOT / "tests/python"))
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(loader.loadTestsFromName(Path(name).stem) for name in sorted(selected))
    if loader.errors or not suite.countTestCases():
        for error in loader.errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Python lane={args.lane}: {len(selected)} files, {suite.countTestCases()} cases", flush=True)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
