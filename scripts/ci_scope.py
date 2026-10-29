#!/usr/bin/env python3
"""Scope routine CI to the changed components; uncertain changes run every lane.

Repository docs and unconsumed research notes are prose. Research contracts,
configuration and documents consumed by source/build inputs keep full feedback. Verified operator tools
run every core/source Python test. Ordinary Python changes in the Laya and
historical-research packages also run the complete learning lane. Package tests
are discovered, not registered one filename/version at a time. Native interfaces,
shared contracts, build/install inputs and uncertain changes retain full CI.
Verified installed-release validation keeps native/package and Python feedback.
"""
from __future__ import annotations
import argparse
import ast
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")

# Contract/interface names retain full feedback regardless of a report suffix.
# Other notes do not need a category word or a date in their filename. Before
# omitting their lanes, check actual tracked source/build references as well.
RESEARCH_CONTRACT = re.compile(
    r"(?:^|[_-])(?:CONTRACTS?|CONFIGS?|CONFIGURATION|SCHEMAS?|PROTOCOLS?|"
    r"SPECS?|SPECIFICATION|MANIFESTS?|REQUIREMENTS)(?:[_-]|\.|$)", re.IGNORECASE)

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
    frozenset({"scripts/activate_prepared_gateway.py", "scripts/native_execution_maintenance.py",
               "tests/python/test_activate_prepared_gateway.py",
               "tests/python/test_native_execution_maintenance.py"}),
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

# These validate the installed native/SIM packages, not numerical research.
# Keep native/package and all core/source behavior; only this exact group may
# omit learning, after checking its existing first-party import closure below.
RELEASE_VALIDATION_GROUP = frozenset({
    "scripts/accept_core_release.py", "scripts/run_release_simulator_smoke.py",
    "tests/python/test_core_release_acceptance.py", "tests/python/test_release_simulator_smoke.py",
})

# These support files must exist, but changing one does not inherit the helper's
# exemption. Keep import checks local to the verified Python entry points.
VERIFIED_HELPER_DEPENDENCIES = {
    # Gateway/native handoff controls already-built releases through the Python
    # driver API. Keep both lifecycle suites and their shared driver fixture;
    # native code, release builders and installation changes stay outside this
    # group. Check both controllers even when only a lifecycle test changes.
    "scripts/activate_prepared_gateway.py": (
        frozenset({"activate_prepared_driver", "native_execution_maintenance",
                   "hepta_decision_attribution"}),
        frozenset({"scripts/activate_prepared_driver.py",
                   "scripts/hepta_decision_attribution.py",
                   "tests/python/test_activate_prepared_driver.py"}),
    ),
    "scripts/native_execution_maintenance.py": (frozenset(), frozenset()),
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
    "scripts/accept_core_release.py": (
        frozenset({"verify_build_ownership"}),
        frozenset({
            "scripts/verify_build_ownership.py", "scripts/source_json.py",
            "tests/python/test_installed_runtime_processes.py",
            "tests/python/test_oms_generation_process.py",
            "tests/python/test_client_pair_admission.py",
            "tests/python/runtime_acceptance_support.py",
            "tests/research/installed_client_server_pair.py",
            "tests/research/native_sdk_package_behavior.py",
            "tests/research/sdk_package_behavior.py",
            # Existing child commands/inputs stay available. Changing any of
            # these is outside the four-path exemption and remains full CI.
            "scripts/run_python_tests.py", "scripts/build_release_package.py",
            "scripts/hepta_preflight.py", "docs/preflight-policy-v1.json",
            "tests/systemd_simulator_smoke.py",
        }),
    ),
    "scripts/run_release_simulator_smoke.py": (frozenset(), frozenset()),
    "tests/python/test_core_release_acceptance.py": (
        frozenset({"accept_core_release", "test_installed_runtime_processes",
                   "test_oms_generation_process", "test_client_pair_admission"}), frozenset()),
    "tests/python/test_release_simulator_smoke.py": (
        frozenset({"run_release_simulator_smoke"}), frozenset()),
    "scripts/verify_build_ownership.py": (frozenset({"source_json"}), frozenset()),
    "scripts/source_json.py": (frozenset(), frozenset()),
    "tests/python/test_installed_runtime_processes.py": (
        frozenset({"run_release_simulator_smoke", "runtime_acceptance_support"}), frozenset()),
    "tests/python/test_oms_generation_process.py": (
        frozenset({"test_installed_runtime_processes", "accept_core_release"}), frozenset()),
    "tests/python/test_client_pair_admission.py": (
        frozenset({"accept_core_release", "installed_client_server_pair"}), frozenset()),
    "tests/python/runtime_acceptance_support.py": (frozenset(), frozenset()),
    "tests/research/installed_client_server_pair.py": (
        frozenset({"test_installed_runtime_processes", "native_sdk_package_behavior"}), frozenset()),
    "tests/research/native_sdk_package_behavior.py": (
        frozenset({"sdk_package_behavior"}), frozenset()),
    "tests/research/sdk_package_behavior.py": (frozenset(), frozenset()),
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


def research_document_inputs(paths: set[str], root: Path) -> set[str]:
    """Keep referenced notes in CI, including deleted inputs and formatted names.

    Git supplies the same tracked source set that CI checks out. References in
    Markdown are prose links, not executable dependencies. The scope classifier
    and its synthetic fixtures are excluded so a regression test does not turn
    its sample report into a product input. Unavailable Git/source reads keep
    full feedback; exported source without its index is deliberately uncertain.
    """
    if not paths:
        return set()
    try:
        result = subprocess.run(
            ["git", "grep", "-Ilz", "-F", ".md", "--", ":!*.md",
             ":!scripts/ci_scope.py", ":!tests/python/test_ci_scope.py"],
            cwd=root, capture_output=True, timeout=15, check=False)
        if result.returncode not in (0, 1):
            return paths
        inputs = set()
        for relative in result.stdout.decode("utf-8").split("\0"):
            if not relative:
                continue
            if not regular_source(root, relative):
                return paths
            source = (root / relative).read_text(encoding="utf-8")
            # Match literal paths/basenames and names made with f-strings or
            # build substitutions, e.g. window_{count}.md. Basename matching
            # is conservative when two packages use the same document name.
            patterns = []
            for token in re.findall(r"['\"]([^'\"\r\n]*\.md)['\"]", source):
                pattern = re.sub(r"\$?\{[^}]*\}|%[0-9]*[sd]", "*", token)
                leaf = pattern.rsplit("/", 1)[-1]
                if leaf == "*.md":
                    if "research" in source:
                        return paths
                elif leaf != ".md":
                    patterns.append(leaf)
            for path in paths:
                name = Path(path).name
                if name in source or any(fnmatch.fnmatchcase(name, p) for p in patterns):
                    inputs.add(path)
        return inputs
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return paths


def prose_paths(paths: list[str], root: Path) -> set[str]:
    prose, research = set(), set()
    for path in paths:
        components = path.split("/")
        if (path.startswith("/") or "\\" in path or "\0" in path or
                any(not p or p in (".", "..") for p in components)):
            return set()
        candidate = root / path
        if (candidate.is_symlink() or candidate.resolve() != candidate or
                candidate.exists() and not candidate.is_file()):
            continue
        if path == "README.md":
            prose.add(path)
            continue
        if len(components) >= 2 and components[0] == "research":
            name = components[-1]
            if name == "README.md":
                prose.add(path)
            elif (len(components) >= 3 and path.endswith(".md") and
                  not any(RESEARCH_CONTRACT.search(part) for part in components[1:])):
                research.add(path)
        elif len(components) >= 2 and components[0] == "docs" and path.endswith(".md"):
            prose.add(path)
    return prose | (research - research_document_inputs(research, root))


def docs_only(paths: list[str], root: Path = ROOT) -> bool:
    return bool(paths) and set(paths) == prose_paths(paths, root.resolve())


def scope_for_paths(paths: list[str] | None, root: Path = ROOT) -> dict[str, bool]:
    full = {"run_native": True, "run_python": True, "run_learning": True}
    if not paths or any(path.startswith("/") or "\\" in path or "\0" in path or
                        any(part in {"", ".", ".."} for part in path.split("/"))
                        for path in paths):
        return full
    root = root.resolve()
    prose = prose_paths(paths, root)
    if set(paths) == prose:
        return {key: False for key in full}
    changed = [path for path in paths if path not in prose]
    release_validation = bool(RELEASE_VALIDATION_GROUP.intersection(changed))
    if release_validation and not set(changed) <= RELEASE_VALIDATION_GROUP:
        return full
    groups = VERIFIED_PYTHON_GROUPS + VERIFIED_PYTHON_LEARNING_GROUPS + (RELEASE_VALIDATION_GROUP,)
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
    if release_validation:
        # The four files' support set includes the complete reviewed import
        # closure. Validate those helpers too, without exempting their edits.
        helpers = [path for path in VERIFIED_HELPER_DEPENDENCIES if path in needed]
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
        "run_native": release_validation,
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
    if scope["run_native"] and not scope["run_learning"]:
        print("NATIVE_AND_PYTHON: native/package and all core/source tests.")
    elif scope["run_native"]:
        print("FULL_SOURCE_TESTS: source, config, unknown or unverified diff.")
    elif scope["run_learning"]:
        print("PYTHON_AND_LEARNING: all core/source and learning tests.")
    else:
        print("PYTHON_TOOLS: all core/source tests.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
