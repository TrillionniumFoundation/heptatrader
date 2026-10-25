#!/usr/bin/env python3
"""Scope routine CI to the changed components; uncertain changes run every lane.

Repository docs, research READMEs and identified observation reports are prose.
Other research contracts/configuration keep full feedback. Verified operator tools
run every core/source Python test. Source monitoring, PnL watchers and price
baselines also retain learning tests. Neither verified group needs a native rebuild.
Any unknown, shared, CI, missing or renamed-outside-group path restores full CI.
"""
from __future__ import annotations
import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")

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
)

# These support files must exist, but changing one does not inherit the helper's
# exemption. Keep import checks local to the verified Python entry points.
VERIFIED_HELPER_DEPENDENCIES = {
    "scripts/hepta_progress.py": (
        frozenset(),
        frozenset({"scripts/heptatrader-user", "cmake/HeptaInstall.cmake",
                   "tests/python/test_cmake_install_behavior.py"}),
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

# These components consume existing native interfaces. Keep all Python/source
# and learning integration tests while avoiding an unrelated native rebuild.
VERIFIED_PYTHON_LEARNING_GROUPS = (
    frozenset({
        "scripts/hepta_pnl_watch.py",
        "scripts/hepta_change_watch.py",
        "tests/python/test_pnl_watch_regression.py",
        "tests/python/test_pnl_append_ledger.py",
        "tests/python/test_pnl_bounded_view.py",
        "tests/python/test_pnl_journal_cache.py",
    }),
    frozenset({
        "research/neuron_learning/laya_cell/source_readiness.py",
        "scripts/hepta_change_watch.py",
        "research/neuron_learning/laya_cell/verified_sealed_readiness_cache.py",
        "research/neuron_learning/laya_cell/readiness_heartbeat.py",
        "tests/python/test_verified_sealed_readiness_cache.py",
        "tests/python/test_source_original_proof_cache.py",
        "tests/python/test_fx_source_monitor_cadence.py",
        "tests/python/test_readiness_heartbeat.py",
    }),
    frozenset({
        "research/neuron_learning/laya_cell/resident_price_baselines.py",
        "research/neuron_learning/laya_cell/tests/test_resident_price_baselines.py",
        "research/neuron_learning/laya_cell/tests/test_resident_training_v3.py",
    }),
    # Resident orchestration consumes the existing Python/native interfaces.
    # Keep numerical integration feedback and the default-core discovery entry
    # for historical_training's nested guard tests.
    frozenset({
        "research/historical_training/resident_resource_guard.py",
        "research/historical_training/tests/test_resident_resource_guard.py",
        "research/neuron_learning/laya_cell/resident.py",
        "research/neuron_learning/laya_cell/resident_store.py",
        "research/neuron_learning/laya_cell/resident_plan_admission.py",
        "research/neuron_learning/laya_cell/resident_plan_calendar.py",
        "research/neuron_learning/laya_cell/tests/test_resident.py",
        "research/neuron_learning/laya_cell/tests/test_resident_plan_calendar.py",
        "research/neuron_learning/laya_cell/tests/test_resident_plan_admission.py",
        "tests/python/test_historical_training.py",
        "tests/python/test_laya_training_cadence.py",
        "tests/python/test_laya_training_window.py",
    }),
    # Successor evaluation reads frozen evidence; a native/interface or install
    # change outside this bounded group still restores every CI lane.
    frozenset({
        "research/historical_training/value_successor_evaluator.py",
        "research/historical_training/value_ledger_stream.py",
        "tests/python/test_value_successor_evaluator.py",
    }),

    frozenset({
        "research/historical_training/consumer_launch.py",
        "research/historical_training/consumer_checkpoint.py",
        "research/historical_training/current100k.py",
        "research/historical_training/export_current100k.py",
        "research/historical_training/install_consumer.py",
        "research/neuron_learning/laya_cell/financial_value_data.py",
        "tests/python/test_consumer_checkpoint.py",
        "tests/python/test_consumer_runtime_inventory.py",
    }),
    # These training objectives, calibration and input transforms stay within
    # the existing Python learner. Native, shared integrity and installation
    # changes outside this group still require the complete source build.
    frozenset({
        "research/neuron_learning/laya_cell/resident_training_v4.py",
        "research/neuron_learning/laya_cell/resident_training_v5.py",
        "research/neuron_learning/laya_cell/resident_training_v6.py",
        "research/neuron_learning/laya_cell/resident_training_v7.py",
        "research/neuron_learning/laya_cell/resident_training_v8.py",
        "research/neuron_learning/laya_cell/resident_closed_action_training.py",
        "research/neuron_learning/laya_cell/resident_price_calibration.py",
        "research/neuron_learning/laya_cell/resident_price_calibration_v2.py",
        "research/neuron_learning/laya_cell/resident_price_calibration_v3.py",
        "research/neuron_learning/laya_cell/resident_price_input.py",
        "research/neuron_learning/laya_cell/resident_train.py",
        "research/neuron_learning/laya_cell/resident.py",
        "research/neuron_learning/laya_cell/resident_data.py",
        "research/neuron_learning/laya_cell/resident_predict.py",
        "research/neuron_learning/laya_cell/resident_row_accounting.py",
        "research/neuron_learning/laya_cell/resident_store.py",
        "research/neuron_learning/laya_cell/tests/test_resident_training_v4.py",
        "research/neuron_learning/laya_cell/tests/test_resident_training_v5.py",
        "research/neuron_learning/laya_cell/tests/test_resident_training_v6.py",
        "research/neuron_learning/laya_cell/tests/test_resident_training_v6_integration.py",
        "research/neuron_learning/laya_cell/tests/test_resident_training_v7.py",
        "research/neuron_learning/laya_cell/tests/test_resident_training_v8.py",
        "research/neuron_learning/laya_cell/tests/test_resident_closed_action_optimizer.py",
        "research/neuron_learning/laya_cell/tests/test_resident_price_calibration.py",
        "research/neuron_learning/laya_cell/tests/test_resident_price_calibration_v2.py",
        "research/neuron_learning/laya_cell/tests/test_resident_price_calibration_v3.py",
        "research/neuron_learning/laya_cell/tests/test_resident_price_input_v8.py",
        "research/neuron_learning/laya_cell/tests/test_resident_price_cache.py",
        "research/neuron_learning/laya_cell/tests/test_resident_row_worker.py",
        "research/neuron_learning/laya_cell/tests/test_resident_lineage.py",
        "research/neuron_learning/laya_cell/tests/test_resident_training_v3.py",
        "research/neuron_learning/laya_cell/tests/test_resident_price_baselines.py",
    }),
    frozenset({
        "research/neuron_learning/laya_cell/financial_forward_source.py",
        "research/neuron_learning/laya_cell/financial_forward_predict.py",
        "research/neuron_learning/laya_cell/current100k_forward.py",
        "research/neuron_learning/laya_cell/current100k_forward_worker.py",
        "research/neuron_learning/laya_cell/financial_forward_evaluate.py",
        "research/historical_training/value_timing_reconcile.py",
        "research/historical_training/tests/test_current100k_forward_scope.py",
        "research/historical_training/tests/test_value_front_snapshot.py",
        "research/historical_training/tests/test_value_timing_reconcile.py",
    }),
    frozenset({
        "research/historical_training/resident_comgr_cache.py",
        "research/historical_training/resident_resource_guard.py",
        "research/historical_training/tests/test_resident_comgr_cache.py",
        "research/historical_training/tests/test_resident_cache_lifecycle.py",
        "research/historical_training/tests/test_value_predictor_resource_gate.py",
    }),
)


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
            # Reports are prose; unnamed research contracts retain full CI.
            if name == "README.md" or re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*_(?:PHYSICAL|AUDIT|CLOSEOUT|FORENSICS)_20[0-9]{6}\.md", name):
                continue
            return False
        if len(components) < 2 or components[0] != "docs" or not path.endswith(".md"):
            return False
    return True


def scope_for_paths(paths: list[str] | None, root: Path = ROOT) -> dict[str, bool]:
    full = {"run_native": True, "run_python": True, "run_learning": True}
    if not paths:
        return full
    if docs_only(paths):
        return {key: False for key in full}
    changed = [path for path in paths if not docs_only([path])]
    groups = VERIFIED_PYTHON_GROUPS + VERIFIED_PYTHON_LEARNING_GROUPS
    allowed = frozenset.union(*groups)
    if not changed or any(path not in allowed for path in changed):
        return full
    # A deleted/retyped tool or missing paired test must not qualify for exemption.
    needed = set.union(*(set(group) for group in groups
                         if group.intersection(changed)))
    helpers = [path for path in VERIFIED_HELPER_DEPENDENCIES if path in needed]
    for path in helpers:
        needed.update(VERIFIED_HELPER_DEPENDENCIES[path][1])
    try:
        if any((root / path).is_symlink() or not (root / path).is_file() for path in needed):
            return full
        if any(not _helper_imports_known(root, path, VERIFIED_HELPER_DEPENDENCIES[path][0])
               for path in helpers):
            return full
    except (OSError, SyntaxError, ValueError, UnicodeError):
        return full
    return {
        "run_native": False,
        "run_python": True,
        "run_learning": any(group.intersection(changed)
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
