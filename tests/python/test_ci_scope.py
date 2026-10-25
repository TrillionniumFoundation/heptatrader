"""Verified tool scope stays bounded; unknown paths restore full CI."""
import subprocess
import unittest
from unittest.mock import patch
from pathlib import Path
import shutil
import sys
import tempfile

from ci_scope import (
    docs_only, diff_paths, scope_for_paths,
    VERIFIED_PYTHON_GROUPS, VERIFIED_PYTHON_LEARNING_GROUPS, VERIFIED_HELPER_DEPENDENCIES,
)


class DocsOnlyScope(unittest.TestCase):
    def test_documentation_only(self):
        self.assertTrue(docs_only(["README.md", "docs/technical/a.md", "docs/b.md"]))

    def test_research_readmes_and_dated_reports_are_prose(self):
        self.assertTrue(docs_only([
            "research/README.md", "research/train_v2/README.md",
            "research/train_v2/FX_ORIGINAL_HEALTH_FIRST160_PHYSICAL_20261013.md",
        ]))

    def test_named_research_incident_report_is_prose(self):
        paths = ["research/historical_training/RESIDENT_RESOURCE_GUARD.md"]
        self.assertTrue(docs_only(paths))
        self.assertEqual(
            scope_for_paths(paths),
            {"run_native": False, "run_python": False, "run_learning": False})

    def test_research_contracts_and_configuration_keep_full_feedback(self):
        for path in ("research/MODELS.md", "research/CLIENT_PACKAGE.md",
                     "research/other/RESIDENT_RESOURCE_GUARD.md",
                     "research/historical_training/RESIDENT_RESOURCE_GUARD.json",
                     "research/train_v2/contract.md",
                     "research/train_v2/SOURCE_CONTRACT_20261013.md",
                     "research/train_v2/CONFIG_20261013.md",
                     "research/train_v2/plan.json",
                     "research/train_v2/config.yaml",
                     "research/train_v2/source.py"):
            with self.subTest(path=path):
                self.assertFalse(docs_only([path]))
                self.assertEqual(
                    scope_for_paths([path]),
                    {"run_native": True, "run_python": True, "run_learning": True})

    def test_empty_must_not_skip_everything(self):
        self.assertFalse(docs_only([]))

    def test_source_and_docs_always_full(self):
        self.assertFalse(docs_only(["docs/technical/a.md", "scripts/hepta_maintenance_latch.py"]))

    def test_native_and_broker_code_always_full(self):
        self.assertFalse(docs_only(["HeptaTrade/execution/execution_coordinator.cpp"]))

    def test_ci_workflow_edits_always_full(self):
        self.assertFalse(docs_only([".github/workflows/core-ci.yml"]))

    def test_catalog_and_mixed_nonmarkdown_docs_always_full(self):
        self.assertFalse(docs_only(["docs/module-catalog.json"]))
        self.assertFalse(docs_only(["docs/assets/chart.svg"]))
        self.assertFalse(docs_only(["docs/report.md", "docs/contracts.json"]))

    def test_path_traversal_and_uncertain_paths_always_full(self):
        for sample in ["docs/../scripts/a.md", "/docs/report.md", "docs//report.md",
                       "docs\\evil.md", "docs/.hidden/../report.md"]:
            with self.subTest(path=sample):
                self.assertFalse(docs_only([sample]))

    def test_git_diff_checks_both_sides_of_rename(self):
        base = "a" * 40
        with patch("ci_scope.subprocess.check_output", return_value=b"src/a.cpp\0docs/a.md\0") as check:
            names = diff_paths(base)
        self.assertEqual(names, ["src/a.cpp", "docs/a.md"])
        self.assertFalse(docs_only(names))
        self.assertIn("--no-renames", check.call_args.args[0])

    def test_git_failure_runs_full(self):
        with patch("ci_scope.subprocess.check_output",
                   side_effect=subprocess.CalledProcessError(1, ["git"])):
            self.assertIsNone(diff_paths("a" * 40))

    def test_bad_or_zero_base_runs_full(self):
        with patch("ci_scope.subprocess.check_output") as check:
            self.assertIsNone(diff_paths("0" * 39))
        check.assert_not_called()

    def test_nul_separated_nonempty_only(self):
        with patch("ci_scope.subprocess.check_output", return_value=b"docs/a.md\0"):
            self.assertEqual(diff_paths("a" * 40), ["docs/a.md"])
        for bad in [b"", b"docs/a.md", b"\0"]:
            with patch("ci_scope.subprocess.check_output", return_value=bad):
                self.assertFalse(docs_only(diff_paths("a" * 40) or []))


class ComponentScopeTests(unittest.TestCase):
    FULL = {"run_native": True, "run_python": True, "run_learning": True}
    PYTHON = {"run_native": False, "run_python": True, "run_learning": False}
    PYTHON_LEARNING = {"run_native": False, "run_python": True, "run_learning": True}

    def setUp(self):
        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        self.root = Path(box.name)
        groups = VERIFIED_PYTHON_GROUPS + VERIFIED_PYTHON_LEARNING_GROUPS
        fixture_paths = set(frozenset.union(*groups))
        for _, support in VERIFIED_HELPER_DEPENDENCIES.values():
            fixture_paths.update(support)
        for path in fixture_paths:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("pass\n")

    def test_verified_loader_and_installer_use_python_feedback(self):
        for path in (
            "scripts/hepta_verified_loader.py",
            "tests/python/test_verified_python_loader.py",
            "scripts/install_decision_attribution.py",
            "tests/python/test_decision_attribution_installer_readonly.py",
            "tests/python/test_decision_attribution_prepare_upgrade.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([path], self.root), self.PYTHON)
        self.assertEqual(scope_for_paths([
            "scripts/hepta_verified_loader.py", "scripts/hepta_change_watch.py",
        ], self.root), self.PYTHON_LEARNING)

    def test_helper_support_is_required_but_does_not_exempt_changes(self):
        helpers = ["scripts/hepta_verified_loader.py", "scripts/install_decision_attribution.py"]
        for path in (
            "scripts/heptatrader_paper_driver.py", "scripts/hepta_decision_attribution.py",
            "research/cost_aware/scripts/hepta_cost_driver.py", "cmake/HeptaInstall.cmake",
        ):
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([path], self.root), self.FULL)
                self.assertEqual(scope_for_paths([helpers[0], path], self.root), self.FULL)
        support = self.root / "scripts/heptatrader_paper_driver.py"
        support.unlink()
        for helper in helpers:
            self.assertEqual(scope_for_paths([helper], self.root), self.FULL)
        support.symlink_to(self.root / "scripts/hepta_maintenance_latch.py")
        for helper in helpers:
            self.assertEqual(scope_for_paths([helper], self.root), self.FULL)

    def test_helper_import_expansion_and_invalid_source_restore_full_feedback(self):
        for helper in ("scripts/hepta_verified_loader.py", "scripts/install_decision_attribution.py"):
            for source in ("import torch\n", "import unregistered_runtime\n",
                           "from . import runtime\n", "def invalid(\n"):
                with self.subTest(helper=helper, source=source):
                    (self.root / helper).write_text(source)
                    self.assertEqual(scope_for_paths([helper], self.root), self.FULL)
            (self.root / helper).write_text("from pathlib import Path\nimport hashlib\n")
            self.assertEqual(scope_for_paths([helper], self.root), self.PYTHON)
        installer = self.root / "scripts/install_decision_attribution.py"
        installer.write_text("import hepta_verified_loader\nimport hepta_decision_attribution\n"
                             "import activate_prepared_driver\n")
        self.assertEqual(scope_for_paths([str(installer.relative_to(self.root))], self.root), self.PYTHON)

    def test_missing_helper_restores_full_even_for_a_paired_test(self):
        (self.root / "scripts/hepta_verified_loader.py").unlink()
        self.assertEqual(scope_for_paths(["tests/python/test_verified_python_loader.py"], self.root), self.FULL)

    def test_shared_change_watcher_requires_both_consumer_test_sets(self):
        for test_path in ("tests/python/test_pnl_journal_cache.py", "tests/python/test_source_original_proof_cache.py"):
            target = self.root / test_path
            target.unlink()
            self.assertEqual(scope_for_paths(["scripts/hepta_change_watch.py"], self.root), self.FULL)
            target.write_text("pass\n")

    def test_verified_tools_tests_and_docs_use_full_python_only(self):
        for paths in (
            ["scripts/hepta_nav_assurance.py"],
            ["scripts/activate_prepared_driver.py"],
            ["tests/python/test_activate_prepared_driver.py"],
            ["scripts/hepta_runtime_identity.py"],
            ["tests/python/test_runtime_identity.py"],
            ["tests/python/test_pnl_cli.py"],
            ["scripts/hepta_maintenance_latch.py", "scripts/hepta_pnl_report.py",
             "tests/python/test_account_observation.py", "docs/technical/change.md"],
        ):
            with self.subTest(paths=paths):
                self.assertEqual(scope_for_paths(paths, self.root), self.PYTHON)
        self.assertEqual(scope_for_paths(["README.md", "docs/technical/a.md"], self.root),
                         {key: False for key in self.FULL})

    def test_monitors_and_baselines_keep_python_and_learning_without_native(self):
        for path in (
            "scripts/hepta_pnl_watch.py",
            "scripts/hepta_change_watch.py",
            "tests/python/test_pnl_watch_regression.py",
            "tests/python/test_pnl_append_ledger.py",
            "tests/python/test_pnl_bounded_view.py",
            "tests/python/test_pnl_journal_cache.py",
            "research/neuron_learning/laya_cell/resident.py",
            "research/neuron_learning/laya_cell/resident_store.py",
            "research/historical_training/resident_resource_guard.py",
            "research/historical_training/tests/test_resident_resource_guard.py",
            "tests/python/test_laya_training_window.py",
            "research/historical_training/value_successor_evaluator.py",
            "research/historical_training/value_ledger_stream.py",
            "tests/python/test_value_successor_evaluator.py",
            "research/neuron_learning/laya_cell/source_readiness.py",
            "research/neuron_learning/laya_cell/verified_sealed_readiness_cache.py",
            "research/neuron_learning/laya_cell/readiness_heartbeat.py",
            "tests/python/test_verified_sealed_readiness_cache.py",
            "tests/python/test_source_original_proof_cache.py",
            "tests/python/test_fx_source_monitor_cadence.py",
            "tests/python/test_readiness_heartbeat.py",
            "research/neuron_learning/laya_cell/resident_price_baselines.py",
            "research/neuron_learning/laya_cell/tests/test_resident_price_baselines.py",
            "research/neuron_learning/laya_cell/tests/test_resident_training_v3.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([path], self.root), self.PYTHON_LEARNING)

    def test_mixed_verified_components_preserve_learning_feedback(self):
        for paths in (
            [
                "research/neuron_learning/laya_cell/resident.py",
                "research/historical_training/value_successor_evaluator.py",
                "docs/technical/runtime-cost-observations.md",
            ],
            [
                "scripts/hepta_pnl_watch.py",
                "research/neuron_learning/laya_cell/resident_price_baselines.py",
                "docs/technical/account-pnl.md",
            ],
            [
                "scripts/activate_prepared_driver.py",
                "tests/python/test_verified_sealed_readiness_cache.py",
                "research/historical_training/RESIDENT_RESOURCE_GUARD.md",
            ],
        ):
            with self.subTest(paths=paths):
                self.assertEqual(scope_for_paths(paths, self.root), self.PYTHON_LEARNING)

    def test_learning_component_missing_or_symlinked_test_restores_full(self):
        for implementation, test_name in (
            ("research/neuron_learning/laya_cell/resident.py", "tests/python/test_laya_training_window.py"),
            ("research/historical_training/value_successor_evaluator.py",
             "research/historical_training/value_ledger_stream.py"),
            ("scripts/hepta_pnl_watch.py", "tests/python/test_pnl_append_ledger.py"),
            ("research/neuron_learning/laya_cell/source_readiness.py",
             "tests/python/test_verified_sealed_readiness_cache.py"),
            ("research/neuron_learning/laya_cell/resident_price_baselines.py",
             "research/neuron_learning/laya_cell/tests/test_resident_price_baselines.py"),
            ("research/neuron_learning/laya_cell/resident_training_v5.py",
             "research/neuron_learning/laya_cell/tests/test_resident_closed_action_optimizer.py"),
            ("research/neuron_learning/laya_cell/resident_training_v8.py",
             "research/neuron_learning/laya_cell/tests/test_resident_price_cache.py"),
        ):
            with self.subTest(implementation=implementation):
                test = self.root / test_name
                test.unlink()
                self.assertEqual(scope_for_paths([implementation], self.root), self.FULL)
                test.symlink_to(self.root / implementation)
                self.assertEqual(scope_for_paths([implementation], self.root), self.FULL)
                test.unlink()
                test.write_text("pass\n")
                tool = self.root / implementation
                tool.unlink()
                self.assertEqual(scope_for_paths([test_name], self.root), self.FULL)
                tool.write_text("pass\n")

    def test_consumer_episode_and_value_leaves_keep_learning_without_native(self):
        leaves = (
            "research/historical_training/consumer_checkpoint.py",
            "research/historical_training/install_consumer.py",
            "research/neuron_learning/laya_cell/resident_train.py",
            "research/neuron_learning/laya_cell/resident_training_v4.py",
            "research/neuron_learning/laya_cell/financial_forward_source.py",
            "research/neuron_learning/laya_cell/current100k_forward_worker.py",
            "research/neuron_learning/laya_cell/current100k_forward.py",
            "research/neuron_learning/laya_cell/financial_forward_evaluate.py",
            "research/historical_training/value_timing_reconcile.py",
            "research/historical_training/tests/test_value_predictor_resource_gate.py",
            "research/historical_training/tests/test_resident_cache_lifecycle.py",
        )
        for path in leaves:
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([path], self.root), self.PYTHON_LEARNING)
        self.assertEqual(scope_for_paths(list(leaves), self.root), self.PYTHON_LEARNING)
        self.assertEqual(scope_for_paths([leaves[0], "scripts/hepta_progress.py"],
                                        self.root), self.PYTHON_LEARNING)
        paired = self.root / "tests/python/test_consumer_checkpoint.py"
        paired.unlink()
        self.assertEqual(scope_for_paths([leaves[0]], self.root), self.FULL)

    def test_current_resident_objectives_keep_learning_and_native_boundaries(self):
        # Use the real checkout so a misspelled or absent fixture cannot pass
        # merely because the synthetic setup copied the scope's own file list.
        root = Path(__file__).resolve().parents[2]
        leaves = [
            f"research/neuron_learning/laya_cell/resident_training_v{version}.py"
            for version in range(5, 9)
        ] + [
            "research/neuron_learning/laya_cell/resident_closed_action_training.py",
            "research/neuron_learning/laya_cell/resident_price_calibration.py",
            "research/neuron_learning/laya_cell/resident_price_calibration_v2.py",
            "research/neuron_learning/laya_cell/resident_price_calibration_v3.py",
            "research/neuron_learning/laya_cell/resident_price_input.py",
            "research/neuron_learning/laya_cell/tests/test_resident_training_v6_integration.py",
            "research/neuron_learning/laya_cell/tests/test_resident_price_input_v8.py",
        ]
        for path in leaves:
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([path], root), self.PYTHON_LEARNING)
        self.assertEqual(scope_for_paths(leaves, root), self.PYTHON_LEARNING)
        for shared in (
            "research/neuron_learning/laya_cell/native.py",
            "research/neuron_learning/laya_cell/integrity.py",
            "research/neuron_learning/laya_cell/install_resident.py",
            "CMakeLists.txt",
            ".github/workflows/core-ci.yml",
        ):
            with self.subTest(shared=shared):
                self.assertEqual(scope_for_paths([leaves[0], shared], root), self.FULL)

    def test_progress_scope_requires_installation_support_and_stdlib_imports(self):
        observer = "scripts/hepta_progress.py"
        paired = "tests/python/test_hepta_progress.py"
        self.assertEqual(scope_for_paths([observer, paired], self.root), self.PYTHON)
        source = self.root / observer
        source.write_text("import torch\n")
        self.assertEqual(scope_for_paths([observer], self.root), self.FULL)
        source.write_text("import pathlib\nimport subprocess\n")
        self.assertEqual(scope_for_paths([observer], self.root), self.PYTHON)
        self.assertEqual(scope_for_paths([observer, "scripts/heptatrader-user"],
                                        self.root), self.FULL)
        (self.root / "scripts/heptatrader-user").unlink()
        self.assertEqual(scope_for_paths([paired], self.root), self.FULL)

    def test_unknown_shared_ci_and_invalid_paths_restore_full(self):
        for extra in ("scripts/unknown.py", "scripts/hepta_account_pnl.py",
                      "research/neuron_learning/laya_cell/integrity.py",
                      "research/neuron_learning/laya_cell/install_resident.py",
                      "research/neuron_learning/laya_cell/new_monitor.py",
                      "research/neuron_learning/laya_cell/financial_forward_protocol.py",
                      "scripts/heptatrader-user", "scripts/ci_scope.py",
                      "scripts/run_python_tests.py", ".github/workflows/core-ci.yml",
                      "other/hepta_nav_assurance.py", "scripts/hepta_nav_assurance.py.bak",
                      "docs/../scripts/a.md", "requirements.txt"):
            with self.subTest(extra=extra):
                self.assertEqual(scope_for_paths(["scripts/hepta_nav_assurance.py", extra],
                                                 self.root), self.FULL)
                self.assertEqual(scope_for_paths(["scripts/hepta_pnl_watch.py", extra],
                                                 self.root), self.FULL)
        for unknown in (None, [], [""]):
            self.assertEqual(scope_for_paths(unknown, self.root), self.FULL)

    def test_missing_or_symlinked_paired_test_cannot_exempt_a_tool(self):
        test = self.root / "tests/python/test_nav_assurance.py"
        test.unlink()
        self.assertEqual(scope_for_paths(["scripts/hepta_nav_assurance.py"], self.root), self.FULL)
        other = self.root / "retained.py"
        other.write_text("pass\n")
        test.symlink_to(other)
        self.assertEqual(scope_for_paths(["scripts/hepta_nav_assurance.py"], self.root), self.FULL)
        tool = self.root / "scripts/hepta_pnl_report.py"
        tool.unlink()
        self.assertEqual(scope_for_paths(["tests/python/test_pnl_cli.py"], self.root), self.FULL)

    def test_real_git_cli_emits_three_explicit_outputs_and_mixed_fallback(self):
        source = Path(__file__).resolve().parents[2] / "scripts/ci_scope.py"
        script = self.root / "scripts/ci_scope.py"
        shutil.copyfile(source, script)
        def git(*args):
            return subprocess.check_output(
                ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Scope Test",
                 "-c", "user.email=scope-test@example.invalid", *args],
                cwd=self.root, stderr=subprocess.PIPE, text=True, timeout=15).strip()
        def outputs(base):
            result = subprocess.run(
                [sys.executable, "-B", str(script), "--base", base, "--format", "github"],
                cwd=self.root, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            pairs = [line.split("=", 1) for line in result.stdout.splitlines()]
            self.assertEqual(len(pairs), 3)
            self.assertEqual({key for key, _ in pairs}, set(self.FULL))
            self.assertTrue(all(value in ("true", "false") for _, value in pairs))
            return {key: value == "true" for key, value in pairs}
        git("init", "-q")
        git("add", ".")
        git("commit", "-qm", "initial fixture")
        base = git("rev-parse", "HEAD")
        (self.root / "scripts/hepta_nav_assurance.py").write_text("value = 1\n")
        git("add", ".")
        git("commit", "-qm", "tool change")
        self.assertEqual(outputs(base), self.PYTHON)
        (self.root / "research/neuron_learning/laya_cell/resident_training_v8.py").write_text(
            "value = 2\n")
        git("add", ".")
        git("commit", "-qm", "resident objective change")
        self.assertEqual(outputs(base), self.PYTHON_LEARNING)
        (self.root / "shared.py").write_text("value = 2\n")
        git("add", ".")
        git("commit", "-qm", "mixed unknown")
        self.assertEqual(outputs(base), self.FULL)
        self.assertEqual(outputs("0" * 40), self.FULL)


if __name__ == "__main__":
    unittest.main()


class FrozenPriceLearningScopeTests(unittest.TestCase):
    def test_frozen_inference_and_its_test_require_learning_feedback(self):
        import ci_scope
        for path in ("research/neuron_learning/laya_cell/frozen_price_numpy.py",
                     "tests/python/test_laya_frozen_price_numpy.py"):
            with self.subTest(path=path):
                self.assertTrue(ci_scope.scope_for_paths([path])["run_learning"])
