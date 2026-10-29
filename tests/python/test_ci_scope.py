"""Package feedback follows Python ownership; shared/native changes stay full."""
import subprocess
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch
from pathlib import Path
import shutil
import sys
import tempfile

from ci_scope import (
    docs_only, diff_paths, scope_for_paths, main,
    VERIFIED_PYTHON_GROUPS, VERIFIED_PYTHON_LEARNING_GROUPS, VERIFIED_HELPER_DEPENDENCIES,
    PYTHON_LEARNING_PACKAGES, RELEASE_VALIDATION_GROUP,
)


class DocsOnlyScope(unittest.TestCase):
    def test_documentation_only(self):
        self.assertTrue(docs_only(["README.md", "docs/technical/a.md", "docs/b.md"]))

    def test_research_readmes_and_dated_reports_are_prose(self):
        self.assertTrue(docs_only([
            "research/README.md", "research/train_v2/README.md",
            "research/train_v2/FX_ORIGINAL_HEALTH_FIRST160_PHYSICAL_20261013.md",
        ]))

    def test_dated_observation_reports_do_not_rebuild_runtime(self):
        for path in (
            "research/historical_training/POCKET4_LAYA_CONVERGENCE_20261015.md",
            "research/train_v2/INPUT_LATENCY_OBSERVATIONS_20261025.md",
            "research/future_component/EXPERIMENT_REPORT_20261026.md",
            "research/train_v2/FX_RIDGE_FINITE_CAMPAIGN_20261012.md",
            "research/train_v2/FX_SOURCE_CACHE_COLD_PREWARM_20261014.md",
            "research/train_v2/FX_ORIGINAL_MULTI_HORIZON_ENVELOPE_20261012.md",
            "research/future_component/notes-without-a-category-or-date.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(docs_only([path]))
                self.assertEqual(scope_for_paths([path]),
                                 {"run_native": False, "run_python": False,
                                  "run_learning": False})

    def test_report_suffix_does_not_exempt_contracts_or_executable_changes(self):
        full = {"run_native": True, "run_python": True, "run_learning": True}
        report = "research/historical_training/POCKET4_LAYA_CONVERGENCE_20261015.md"
        for path in (
            "research/train_v2/SOURCE_CONTRACT_REPORT_20261025.md",
            "research/train_v2/INPUT_SCHEMA_AUDIT_20261025.md",
            "research/train_v2/CONFIG_OBSERVATIONS_20261025.md",
            "research/train_v2/PROTOCOL_CONVERGENCE_20261025.md",
            "research/train_v2/EXPERIMENT_REPORT_20261025.py",
            "research/train_v2/EXPERIMENT_REPORT_20261025.json",
            "research/train_v2/EXPERIMENT_REPORT_20261025.yaml",
            "research/train_v2/EXPERIMENT_REPORT_20261025.yml",
        ):
            with self.subTest(path=path):
                self.assertFalse(docs_only([path]))
                self.assertEqual(scope_for_paths([report, path]), full)

    def test_named_research_incident_report_is_prose(self):
        paths = ["research/historical_training/RESIDENT_RESOURCE_GUARD.md"]
        self.assertTrue(docs_only(paths))
        self.assertEqual(
            scope_for_paths(paths),
            {"run_native": False, "run_python": False, "run_learning": False})

    def test_research_contracts_and_configuration_keep_full_feedback(self):
        for path in ("research/MODELS.md", "research/CLIENT_PACKAGE.md",
                     "research/historical_training/RESIDENT_RESOURCE_GUARD.json",
                     "research/train_v2/contract.md",
                     "research/train_v2/SOURCE_CONTRACT_20261013.md",
                     "research/train_v2/CONFIG_20261013.md",
                     "research/contracts/ordinary-note.md",
                     "research/train_v2/source-contract.md",
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

    def test_source_consumed_notes_keep_feedback_independent_of_report_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            research = root / "research/example"
            research.mkdir(parents=True)
            plain = "research/example/observations.md"
            renamed = "research/example/window_32.md"
            (root / plain).write_text("# prose or machine input depending on its consumer\n")
            (root / renamed).write_text("# dynamically selected machine input\n")
            (root / "README.md").write_text("[Observations](" + plain + ")\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            self.assertTrue(docs_only([plain, renamed], root))
            consumer = root / "read_input.py"
            consumer.write_text("from pathlib import Path\nPath('" + plain + "').read_text()\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            self.assertFalse(docs_only([plain], root))
            self.assertTrue(docs_only([renamed], root))
            consumer.write_text('from pathlib import Path\nPath(f"window_{count}.md").read_text()\n')
            self.assertFalse(docs_only([renamed], root))
            # Deleted data inputs are still dependencies of their consumer.
            (root / renamed).unlink()
            self.assertEqual(scope_for_paths([renamed], root),
                             {"run_native": True, "run_python": True, "run_learning": True})
            consumer.write_text('from pathlib import Path\nlist(Path("research").rglob("*.md"))\n')
            self.assertFalse(docs_only([plain], root))

    def test_missing_dependency_index_or_retyped_note_keeps_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = "research/example/notes.md"
            target = root / path
            target.parent.mkdir(parents=True)
            target.write_text("# notes\n")
            self.assertFalse(docs_only([path], root))
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            self.assertTrue(docs_only([path], root))
            target.unlink()
            target.mkdir()
            self.assertFalse(docs_only([path], root))
            target.rmdir()
            target.symlink_to(root / "consumer.py")
            self.assertFalse(docs_only([path], root))

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
    NATIVE_PYTHON = {"run_native": True, "run_python": True, "run_learning": False}
    LAYA = "research/neuron_learning/laya_cell"
    HISTORY = "research/historical_training"

    def test_artifact_builder_inputs_keep_native_and_packaging_feedback(self):
        # Packaging CI follows run_native conservatively: all build/controller,
        # verifier and fixture edits must retain it, even with a research edit.
        for path in ("scripts/build_ib_candidate_artifact.sh",
                     "scripts/verify_ib_candidate_artifact.py",
                     "tests/python/test_ib_candidate_builder.py",
                     "tests/ib_connection_probe_behavior.py"):
            self.assertEqual(scope_for_paths([path]), self.FULL)

    def setUp(self):
        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        self.root = Path(box.name)
        groups = VERIFIED_PYTHON_GROUPS + VERIFIED_PYTHON_LEARNING_GROUPS + (RELEASE_VALIDATION_GROUP,)
        fixture_paths = set(frozenset.union(*groups))
        for _, support in VERIFIED_HELPER_DEPENDENCIES.values():
            fixture_paths.update(support)
        for support in PYTHON_LEARNING_PACKAGES.values():
            fixture_paths.update(support)
        for path in fixture_paths:
            self.write(path)
        # These names are intentionally not taken from a CI module/test list.
        for package in (self.LAYA, self.HISTORY):
            self.write(package + "/new_readout.py", "value = 1\n")
            self.write(package + "/tests/test_new_readout.py", "import unittest\n")

    def write(self, path, source="pass\n"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
        return target

    def test_actual_release_validation_keeps_native_package_and_python_feedback(self):
        root = Path(__file__).resolve().parents[2]
        paths = ["scripts/accept_core_release.py", "scripts/run_release_simulator_smoke.py",
                 "tests/python/test_core_release_acceptance.py", "tests/python/test_release_simulator_smoke.py"]
        self.assertEqual(set(paths), RELEASE_VALIDATION_GROUP)
        for changed in [[path] for path in paths] + [paths, paths + ["docs/operations/release-package.md"]]:
            with self.subTest(changed=changed):
                self.assertEqual(scope_for_paths(changed, root), self.NATIVE_PYTHON)

    def test_release_scope_text_and_github_outputs_keep_all_three_flags_honest(self):
        for format_name, expected_return in (("text", 1), ("github", 0)):
            output = io.StringIO()
            with self.subTest(format=format_name), redirect_stdout(output), \
                    patch("ci_scope.diff_paths", return_value=list(RELEASE_VALIDATION_GROUP)):
                self.assertEqual(main(["--base", "a" * 40, "--format", format_name]), expected_return)
            if format_name == "text":
                self.assertEqual(output.getvalue(), "NATIVE_AND_PYTHON: native/package and all core/source tests.\n")
            else:
                self.assertEqual(output.getvalue().splitlines(),
                                 ["run_native=true", "run_python=true", "run_learning=false"])

    def test_release_closure_unknown_imports_and_syntax_errors_restore_full(self):
        support = VERIFIED_HELPER_DEPENDENCIES["scripts/accept_core_release.py"][1]
        closure = RELEASE_VALIDATION_GROUP | (support & VERIFIED_HELPER_DEPENDENCIES.keys())
        for path in closure:
            for source in ("import torch\n", "from laya_cell import resident\n",
                           "from . import unknown\n", "def invalid(\n"):
                with self.subTest(path=path, source=source):
                    self.write(path, source)
                    self.assertEqual(scope_for_paths(["scripts/accept_core_release.py"], self.root), self.FULL)
            self.write(path)
        self.assertEqual(scope_for_paths(list(RELEASE_VALIDATION_GROUP), self.root), self.NATIVE_PYTHON)

    def test_missing_or_retyped_release_closure_restores_full(self):
        needed = RELEASE_VALIDATION_GROUP | VERIFIED_HELPER_DEPENDENCIES["scripts/accept_core_release.py"][1]
        for path in needed:
            with self.subTest(missing=path):
                (self.root / path).unlink()
                self.assertEqual(scope_for_paths(["tests/python/test_release_simulator_smoke.py"], self.root), self.FULL)
                self.write(path)
        for path in ("scripts/accept_core_release.py", "scripts/source_json.py"):
            target = self.root / path
            target.unlink()
            target.symlink_to(self.root / "scripts/run_release_simulator_smoke.py")
            with self.subTest(link=path):
                self.assertEqual(scope_for_paths(["scripts/run_release_simulator_smoke.py"], self.root), self.FULL)
            target.unlink(); target.mkdir()
            with self.subTest(directory=path):
                self.assertEqual(scope_for_paths(["scripts/run_release_simulator_smoke.py"], self.root), self.FULL)
            target.rmdir(); self.write(path)

    def test_release_support_and_mixed_changes_are_not_exempt(self):
        support = VERIFIED_HELPER_DEPENDENCIES["scripts/accept_core_release.py"][1]
        others = support | {"scripts/hepta_inactive_owner.py", "scripts/hepta_oms_checkpoint.py",
            "scripts/hepta_oms_lifecycle.py", "tests/python/test_new_release_behavior.py",
            "scripts/new_release_helper.py", "scripts/hepta_progress.py",
            self.LAYA + "/resident.py", "HeptaTrade/execution/new.cpp",
            "cmake/HeptaInstall.cmake", ".github/workflows/core-ci.yml"}
        for path in others:
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths(["scripts/accept_core_release.py", path], self.root), self.FULL)
        for path in support:
            with self.subTest(support_alone=path):
                self.assertEqual(scope_for_paths([path], self.root), self.FULL)

    def test_real_git_release_diff_keeps_native_then_restores_full_for_support_edit(self):
        source = Path(__file__).resolve().parents[2] / "scripts/ci_scope.py"
        script = self.root / "scripts/ci_scope.py"
        shutil.copyfile(source, script)
        def git(*args):
            return subprocess.check_output(
                ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Scope Test",
                 "-c", "user.email=scope-test@example.invalid", *args],
                cwd=self.root, stderr=subprocess.PIPE, text=True, timeout=15).strip()
        def outputs(base):
            result = subprocess.run([sys.executable, "-B", str(script), "--base", base, "--format", "github"],
                                    cwd=self.root, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return {key: value == "true" for key, value in
                    (line.split("=", 1) for line in result.stdout.splitlines())}
        git("init", "-q"); git("add", "."); git("commit", "-qm", "initial release fixture")
        base = git("rev-parse", "HEAD")
        self.write("scripts/run_release_simulator_smoke.py", "# release-only edit\n")
        git("add", "."); git("commit", "-qm", "edit installed simulator validation")
        self.assertEqual(outputs(base), self.NATIVE_PYTHON)
        self.write("scripts/source_json.py", "# generic source interface edit\n")
        git("add", "."); git("commit", "-qm", "also edit shared source support")
        self.assertEqual(outputs(base), self.FULL)

    def test_new_package_modules_and_tests_need_no_filename_registration(self):
        for package in (self.LAYA, self.HISTORY):
            source = package + "/new_readout.py"
            test = package + "/tests/test_new_readout.py"
            for paths in ([source], [test], [source, test, "docs/technical/new.md"]):
                with self.subTest(paths=paths):
                    self.assertEqual(scope_for_paths(paths, self.root), self.PYTHON_LEARNING)

    def test_actual_prediction_reuse_test_does_not_rebuild_cpp(self):
        # A newly added regression must inherit the package's existing lanes.
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(scope_for_paths([
            self.LAYA + "/tests/test_resident_prediction_reuse.py"], root), self.PYTHON_LEARNING)
        for path in (
            self.LAYA + "/resident_training_v8.py", self.LAYA + "/resident_evaluate.py",
            self.LAYA + "/resident_price_input.py", self.LAYA + "/frozen_price_numpy.py",
            self.LAYA + "/tests/test_resident_training_v6_integration.py",
            self.HISTORY + "/resident_live_source.py", self.HISTORY + "/value_timing_reconcile.py",
        ):
            with self.subTest(path=path):
                self.assertTrue((root / path).is_file())
                self.assertEqual(scope_for_paths([path], root), self.PYTHON_LEARNING)

    def test_actual_report_and_accounting_imports_keep_python_behavior_feedback(self):
        root = Path(__file__).resolve().parents[2]
        for path in ("scripts/hepta_oms_report.py", "scripts/hepta_account_pnl.py",
                     "scripts/hepta_pnl_accounting.py",
                     "tests/python/test_oms_operational_report.py",
                     "tests/python/test_account_pnl.py", "tests/python/test_pnl_accounting.py"):
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([path], root), self.PYTHON)
                self.assertEqual(scope_for_paths([path, "HeptaTrade/oms_journal.cpp"], root), self.FULL)

    def test_reporting_unknown_import_or_missing_consumer_test_restores_full(self):
        reports = ("scripts/hepta_oms_report.py", "scripts/hepta_account_pnl.py",
                   "scripts/hepta_pnl_accounting.py")
        for report in reports:
            for source in ("import torch\n", "import new_native_adapter\n",
                           "from . import unknown\n", "def invalid(\n"):
                self.write(report, source)
                with self.subTest(report=report, source=source):
                    self.assertEqual(scope_for_paths([report], self.root), self.FULL)
            self.write(report)
            self.assertEqual(scope_for_paths([report], self.root), self.PYTHON)
        self.write("scripts/hepta_account_pnl.py", "from hepta_pnl_accounting import amount\n")
        self.assertEqual(scope_for_paths(["scripts/hepta_account_pnl.py"], self.root), self.PYTHON)
        (self.root / "tests/python/test_paper_driver.py").unlink()
        for report in reports:
            self.assertEqual(scope_for_paths([report], self.root), self.FULL)

    def test_actual_gateway_maintenance_changes_keep_python_lifecycle_feedback(self):
        root = Path(__file__).resolve().parents[2]
        controllers = ("scripts/activate_prepared_gateway.py",
                       "scripts/native_execution_maintenance.py")
        fixtures = ("tests/python/test_activate_prepared_gateway.py",
                    "tests/python/test_native_execution_maintenance.py")
        # The actual #483 change set: two controllers, the new lifecycle suite,
        # and documentation. The entire core/source lane remains enabled.
        changes = [*controllers, fixtures[1],
                   "docs/technical/native-execution-maintenance.md"]
        for paths in ([path] for path in (*controllers, *fixtures)):
            with self.subTest(paths=paths):
                self.assertEqual(scope_for_paths(paths, root), self.PYTHON)
        self.assertEqual(scope_for_paths(changes, root), self.PYTHON)

    def test_maintenance_unknown_imports_or_missing_lifecycle_support_restore_full(self):
        controllers = ("scripts/activate_prepared_gateway.py",
                       "scripts/native_execution_maintenance.py")
        fixtures = ("tests/python/test_activate_prepared_gateway.py",
                    "tests/python/test_native_execution_maintenance.py")
        for controller in controllers:
            for source in ("import torch\n", "import new_native_adapter\n",
                           "from . import runtime\n", "def invalid(\n"):
                self.write(controller, source)
                for changed in (*controllers, *fixtures):
                    with self.subTest(controller=controller, source=source, changed=changed):
                        self.assertEqual(scope_for_paths([changed], self.root), self.FULL)
            self.write(controller)
        for missing in (*controllers, *fixtures, "scripts/activate_prepared_driver.py",
                        "scripts/hepta_decision_attribution.py",
                        "tests/python/test_activate_prepared_driver.py"):
            (self.root / missing).unlink()
            for changed in (*controllers, *fixtures):
                with self.subTest(missing=missing, changed=changed):
                    self.assertEqual(scope_for_paths([changed], self.root), self.FULL)
            self.write(missing)
        for changed in (*controllers, *fixtures):
            self.assertEqual(scope_for_paths([changed], self.root), self.PYTHON)

    def test_maintenance_mixed_native_build_install_and_unknown_changes_stay_full(self):
        for path in (
            "HeptaTrade/execution/execution_service_protocol.h",
            "scripts/build_ib_candidate_artifact.sh", "cmake/HeptaInstall.cmake",
            "scripts/hepta_decision_attribution.py", "scripts/unreviewed_maintenance.py",
            "systemd/user/heptatrader-execution.service", ".github/workflows/core-ci.yml",
        ):
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([
                    "scripts/activate_prepared_gateway.py", path], self.root), self.FULL)

    def test_native_shared_and_install_boundaries_still_run_every_lane(self):
        for path in (
            self.LAYA + "/native.py", self.LAYA + "/integrity.py",
            self.LAYA + "/install_resident.py", self.LAYA + "/build_financial_owner.py",
            self.LAYA + "/financial_forward_protocol.py", self.LAYA + "/resident_owner.py",
            self.LAYA + "/tests/test_contracts.py", self.LAYA + "/__init__.py",
            self.HISTORY + "/common.py", self.HISTORY + "/egress.py",
            self.HISTORY + "/install_consumer.py", self.HISTORY + "/campaign_integrity.py",
            self.LAYA + "/new_native_adapter.py", self.LAYA + "/new_schema.py",
            "HeptaTrade/execution/new.cpp", self.LAYA + "/resident_proposal.rs",
            "CMakeLists.txt", "systemd/heptatrader-laya-trainer.service",
            self.LAYA + "/requirements.lock.txt", "scripts/source_json.py",
            "scripts/ci_scope.py", "scripts/run_python_tests.py",
            ".github/workflows/core-ci.yml", "tests/python/test_historical_training.py",
        ):
            self.write(path)
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([path], self.root), self.FULL)
                self.assertEqual(scope_for_paths([self.LAYA + "/new_readout.py", path],
                                                self.root), self.FULL)

    def test_unknown_packages_nested_modules_and_nonpython_inputs_stay_full(self):
        for path in (
            "research/new_package/worker.py", self.LAYA + "/new_package/worker.py",
            self.LAYA + "/config.json", self.HISTORY + "/data.csv",
            self.LAYA + "/tests/fixture.py", "scripts/new_readout.py",
            "other/research/neuron_learning/laya_cell/new_readout.py",
        ):
            self.write(path)
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([path], self.root), self.FULL)

    def test_deleted_or_retyped_package_file_cannot_suppress_native_feedback(self):
        for relative in ("new_readout.py", "tests/test_new_readout.py"):
            path = self.LAYA + "/" + relative
            target = self.root / path
            target.unlink()
            self.assertEqual(scope_for_paths([path], self.root), self.FULL)
            target.symlink_to(self.root / self.HISTORY / "new_readout.py")
            self.assertEqual(scope_for_paths([path], self.root), self.FULL)
            target.unlink()
            target.mkdir()
            self.assertEqual(scope_for_paths([path], self.root), self.FULL)
            target.rmdir()
            self.write(path)

    def test_package_test_discovery_and_support_must_remain_available(self):
        source = self.LAYA + "/new_readout.py"
        test = self.root / self.LAYA / "tests/test_new_readout.py"
        test.unlink()
        self.assertEqual(scope_for_paths([source], self.root), self.FULL)
        self.write(self.LAYA + "/tests/test_new_readout.py")
        (self.root / "scripts/run_python_tests.py").unlink()
        self.assertEqual(scope_for_paths([source], self.root), self.FULL)

    def test_linked_test_directory_and_syntax_errors_restore_full(self):
        path = self.LAYA + "/new_readout.py"
        self.write(path, "def invalid(\n")
        self.assertEqual(scope_for_paths([path], self.root), self.FULL)
        self.write(path)
        tests = self.root / self.LAYA / "tests"
        shutil.rmtree(tests)
        tests.symlink_to(self.root / self.HISTORY / "tests", target_is_directory=True)
        self.assertEqual(scope_for_paths([path], self.root), self.FULL)

    def test_verified_operators_and_mixed_package_changes_keep_existing_feedback(self):
        for path in (
            "scripts/hepta_nav_assurance.py", "scripts/hepta_verified_loader.py",
            "scripts/install_decision_attribution.py", "scripts/hepta_progress.py",
            "scripts/activate_prepared_driver.py", "tests/python/test_runtime_identity.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([path], self.root), self.PYTHON)
                self.assertEqual(scope_for_paths([path, self.LAYA + "/new_readout.py"], self.root),
                                 self.PYTHON_LEARNING)
        for path in ("scripts/hepta_pnl_watch.py", "scripts/hepta_change_watch.py",
                     "tests/python/test_pnl_append_ledger.py", "tests/python/test_laya_training_window.py"):
            with self.subTest(path=path):
                self.assertEqual(scope_for_paths([path], self.root), self.PYTHON_LEARNING)

    def test_helper_support_does_not_exempt_the_production_driver(self):
        for path in ("scripts/heptatrader_paper_driver.py", "scripts/hepta_decision_attribution.py",
                     "research/cost_aware/scripts/hepta_cost_driver.py", "cmake/HeptaInstall.cmake",
                     "scripts/heptatrader-user"):
            self.assertEqual(scope_for_paths([path], self.root), self.FULL)
        support = self.root / "scripts/heptatrader_paper_driver.py"
        support.unlink()
        for helper in ("scripts/hepta_verified_loader.py", "scripts/install_decision_attribution.py"):
            self.assertEqual(scope_for_paths([helper], self.root), self.FULL)
        (self.root / "scripts/heptatrader-user").unlink()
        self.assertEqual(scope_for_paths(["tests/python/test_hepta_progress.py"], self.root), self.FULL)

    def test_helper_new_imports_invalid_source_and_missing_tests_restore_full(self):
        for helper in ("scripts/hepta_verified_loader.py", "scripts/install_decision_attribution.py",
                       "scripts/hepta_progress.py"):
            for source in ("import torch\n", "import unregistered_runtime\n",
                           "from . import runtime\n", "def invalid(\n"):
                self.write(helper, source)
                with self.subTest(helper=helper, source=source):
                    self.assertEqual(scope_for_paths([helper], self.root), self.FULL)
            self.write(helper, "from pathlib import Path\nimport hashlib\n")
            self.assertEqual(scope_for_paths([helper], self.root), self.PYTHON)
        installer = "scripts/install_decision_attribution.py"
        self.write(installer, "import hepta_verified_loader\nimport hepta_decision_attribution\n"
                             "import activate_prepared_driver\n")
        self.assertEqual(scope_for_paths([installer], self.root), self.PYTHON)
        (self.root / "tests/python/test_nav_assurance.py").unlink()
        self.assertEqual(scope_for_paths(["scripts/hepta_nav_assurance.py"], self.root), self.FULL)

    def test_progress_marker_reader_keeps_verified_python_scope(self):
        helper = "scripts/hepta_progress.py"
        # Use the actual entry point so a newly integrated local import cannot
        # silently make ordinary progress edits run unrelated native builds.
        source = Path(__file__).resolve().parents[2] / helper
        self.write(helper, source.read_text())
        for path in (helper, "tests/python/test_hepta_progress.py"):
            self.assertEqual(scope_for_paths([path], self.root), self.PYTHON)
        for dependency in ("scripts/hepta_maintenance_latch.py",
                           "tests/python/test_hepta_maintenance_latch.py"):
            (self.root / dependency).unlink()
            self.assertEqual(scope_for_paths([helper], self.root), self.FULL)
            self.write(dependency)

    def test_shared_change_watcher_requires_both_consumer_test_sets(self):
        for path in ("tests/python/test_pnl_journal_cache.py", "tests/python/test_source_original_proof_cache.py"):
            target = self.root / path
            target.unlink()
            self.assertEqual(scope_for_paths(["scripts/hepta_change_watch.py"], self.root), self.FULL)
            self.write(path)

    def test_invalid_empty_and_traversing_paths_never_select_less(self):
        for paths in (None, [], [""], [self.LAYA + "/../new_readout.py"],
                      [self.LAYA + "/tests/../new_readout.py"], [self.LAYA + "/new_readout.py\0"],
                      ["/" + self.LAYA + "/new_readout.py"], ["docs/../scripts/a.md"]):
            with self.subTest(paths=paths):
                self.assertEqual(scope_for_paths(paths, self.root), self.FULL)

    def test_real_git_diff_new_module_rename_and_mixed_native_change(self):
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
        self.write(self.LAYA + "/tests/test_added_readout.py", "import unittest\n")
        git("add", ".")
        git("commit", "-qm", "add independent Python regression")
        self.assertEqual(outputs(base), self.PYTHON_LEARNING)
        previous = git("rev-parse", "HEAD")
        git("mv", self.LAYA + "/new_readout.py", "outside.py")
        git("commit", "-qm", "move out of the known package")
        self.assertEqual(outputs(previous), self.FULL)
        self.write("HeptaTrade/new.cpp", "int changed;\n")
        git("add", ".")
        git("commit", "-qm", "mixed native change")
        self.assertEqual(outputs(base), self.FULL)
        self.assertEqual(outputs("0" * 40), self.FULL)


if __name__ == "__main__":
    unittest.main()
