from __future__ import annotations
from pathlib import Path
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_python_tests as runner


class PythonTestPartitionTests(unittest.TestCase):
    def test_disjoint_union_is_the_entire_discovered_suite(self) -> None:
        groups = runner.partitions(ROOT)
        self.assertEqual(set.union(*groups.values()),
                         {p.name for p in (ROOT / "tests/python").glob("test_*.py")})
        self.assertEqual(sum(map(len, groups.values())), len(set.union(*groups.values())))
        required = {"test_laya_frozen_price_numpy.py",
                    "test_laya_resident_action_v2_research.py",
                    "test_neuron256_archived_kernel_learning.py",
                    "test_research_contract_smoke.py"}
        self.assertTrue(required <= groups["learning"])
        self.assertFalse(groups["learning"] & groups["core"])
        self.assertEqual(groups["legacy"], {
            "test_ib_workflow_interfaces.py", "test_qualification_trust_boundary.py",
            "test_heptadll_migration_state.py", "test_heptadll_source_disposition.py"})
        self.assertFalse(groups["legacy"] & groups["core"])
        self.assertTrue({
            "test_protocol_reference.py", "test_build_ownership.py",
            "test_risk_legacy_compatibility_boundary.py", "test_legacy_runtime_boundary.py",
        } <= groups["source"])

    def test_future_test_defaults_to_core_and_install_is_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / "tests/python"
            tests.mkdir(parents=True)
            for name in runner.SOURCE_TESTS | runner.LEARNING_TESTS | runner.INSTALL_TESTS | runner.PROCESS_TESTS | runner.EXTERNAL_TESTS | runner.LEGACY_TESTS | {"test_new_behavior.py"}:
                (tests / name).touch()
            groups = runner.partitions(root)
            self.assertIn("test_new_behavior.py", groups["core"])
            self.assertNotIn("test_cmake_install_integration.py", groups["core"])

    def test_missing_explicit_test_and_overlapping_ownership_fail(self) -> None:
        with mock.patch.object(runner, "INSTALL_TESTS", runner.SOURCE_TESTS):
            with self.assertRaisesRegex(ValueError, "overlap"):
                runner.partitions(ROOT)
        with mock.patch.object(runner, "INSTALL_TESTS", frozenset({"test_missing.py"})):
            with self.assertRaisesRegex(ValueError, "missing"):
                runner.partitions(ROOT)


    def test_real_cli_refuses_unconfigured_install_or_all_instead_of_skipping(self):
        environment = dict(os.environ)
        environment.pop("HEPTA_ISOLATED_PROCESS_TESTS", None)
        environment.pop("HEPTA_RELEASE_INTEGRATION_BUILD_DIR", None)
        for lane in ("install", "process", "all"):
            with self.subTest(lane=lane):
                result = subprocess.run([sys.executable, str(ROOT / "scripts/run_python_tests.py"),
                                         "--lane", lane], env=environment,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Ran ", result.stderr)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/run_python_tests.py"),
                                 "--lane", "all", "--list"], env=environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("test_installed_runtime_processes.py", result.stdout)

    def test_source_lane_reports_missing_build_tools_before_importing_tests(self):
        with mock.patch.object(runner.shutil, "which", side_effect=lambda name: "/fixture/cmake" if name == "cmake" else None), \
             mock.patch.object(runner, "partitions", side_effect=AssertionError("tests must not load")), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as error:
                runner.main(["--lane", "source"])
            self.assertEqual(error.exception.code, 2)
            self.assertIn("source/all requires development tools on PATH: ctest", stderr.getvalue())

    def test_listing_source_lane_needs_no_build_tools(self):
        with mock.patch.object(runner.shutil, "which", return_value=None), \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(runner.main(["--lane", "source", "--list"]), 0)
            self.assertIn("test_build_ownership.py", stdout.getvalue())

    def test_opt_in_is_not_a_substitute_for_isolated_fixture_identity(self):
        with mock.patch.dict(os.environ, {"HEPTA_ISOLATED_PROCESS_TESTS": "1"}), \
             mock.patch.object(runner.os, "geteuid", return_value=1000), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                runner.main(["--lane", "process"])
            self.assertEqual(error.exception.code, 2)

    def test_selected_skip_cannot_return_success(self):
        class Fixture(unittest.TestCase):
            @unittest.skip("fixture dependency missing")
            def test_dependency(self):
                self.fail("a skipped test must not execute")
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Fixture)
        groups = {"core": {"test_fixture.py"}, "source": set(), "install": set(), "process": set()}
        with mock.patch.object(runner, "partitions", return_value=groups), \
             mock.patch.object(unittest.TestLoader, "loadTestsFromName", return_value=suite), \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(["--lane", "core"]), 1)

    def test_learning_listing_includes_nested_cpu_suites_without_imports(self):
        expected={"laya_cell.tests." + path.stem for path in
                  (ROOT / "research/neuron_learning/laya_cell/tests").glob("test_*.py")}
        self.assertIn("laya_cell.tests.test_resident_plan_admission", expected)
        self.assertIn("laya_cell.tests.test_resident_plan_calendar", expected)
        for lane in ("learning", "core"):
            with self.subTest(lane=lane), mock.patch.object(runner, "configure_import_paths",
                    side_effect=AssertionError("listing must not import")), \
                    contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(runner.main(["--lane", lane, "--list"]), 0)
                listed=set(stdout.getvalue().splitlines())
                self.assertEqual(expected & listed, expected if lane=="learning" else set())

    def test_native_nested_selectors_exist_and_are_only_listed_as_external(self):
        import ast
        for selector in runner.NESTED_EXTERNAL_TESTS:
            parts = selector.split(".")
            path = ROOT / "research/neuron_learning" / Path(*parts[:3]).with_suffix(".py")
            node = ast.parse(path.read_text())
            for name in parts[3:]:
                matches = [item for item in node.body
                           if isinstance(item, (ast.ClassDef, ast.FunctionDef)) and item.name == name]
                self.assertEqual(len(matches), 1, selector)
                node = matches[0]
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(runner.main(["--lane", "external", "--list"]), 0)
        self.assertTrue(runner.NESTED_EXTERNAL_TESTS <= set(stdout.getvalue().splitlines()))

    def test_new_nested_fixture_defaults_to_learning_without_a_second_allowlist(self):
        import shutil
        with tempfile.TemporaryDirectory(prefix="hepta-nested-discovery-") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            script = root / "scripts/run_python_tests.py"
            shutil.copyfile(ROOT / "scripts/run_python_tests.py", script)
            tests = root / "tests/python"
            tests.mkdir(parents=True)
            for name in set.union(*(set(group) for group in runner.partitions().values())):
                (tests / name).touch()
            nested = root / "research/neuron_learning/laya_cell/tests/test_new_fixture.py"
            nested.parent.mkdir(parents=True)
            nested.write_text("raise AssertionError('listing must not import fixtures')\n")
            for lane in ("learning", "core"):
                result = subprocess.run([sys.executable, str(script), "--lane", lane, "--list"],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("laya_cell.tests.test_new_fixture" in result.stdout.splitlines(),
                                 lane == "learning")

    def test_learning_executes_cpu_fixture_without_running_any_native_fixture(self):
        seen = []
        class Fixture(unittest.TestCase):
            def __init__(self, name):
                super().__init__()
                self.name = name
            def id(self):
                return self.name
            def runTest(self):
                seen.append(self.name)
        cpu = "laya_cell.tests.fixture.CpuTests.test_runs"
        external = [name + ".test_requires_native" if name.endswith(("NativeConformance", "NativeResidentTests"))
                    else name for name in runner.NESTED_EXTERNAL_TESTS]
        suite = unittest.TestSuite([Fixture(cpu),
                                  unittest.TestSuite(Fixture(name) for name in external)])
        with mock.patch.object(runner, "partitions", return_value={"learning": set()}), \
             mock.patch.object(runner, "NESTED_LEARNING_MODULES", frozenset({"fixture"})), \
             mock.patch.object(unittest.TestLoader, "loadTestsFromName", return_value=suite), \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(["--lane", "learning"]), 0)
        self.assertEqual(seen, [cpu])

    def test_external_lane_executes_native_selectors_without_cpu_module_discovery(self):
        loaded = []
        class Fixture(unittest.TestCase):
            def runTest(self):
                pass
        def load(name):
            loaded.append(name)
            return unittest.TestSuite([Fixture()])
        with mock.patch.object(runner, "partitions", return_value={"external": set()}), \
             mock.patch.object(unittest.TestLoader, "loadTestsFromName", side_effect=load), \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(["--lane", "external"]), 0)
        self.assertEqual(set(loaded), runner.NESTED_EXTERNAL_TESTS)

    def test_import_failure_does_not_suppress_valid_modules_in_the_lane(self):
        with tempfile.TemporaryDirectory(prefix="hepta-import-lane-") as directory:
            root = Path(directory)
            marker = root / "valid-ran"
            broken, valid = "a_hepta_fixture_broken", "z_hepta_fixture_valid"
            (root / (broken + ".py")).write_text("raise ImportError('fixture missing dependency')\n")
            (root / (valid + ".py")).write_text(
                "import unittest\nfrom pathlib import Path\n"
                "class Valid(unittest.TestCase):\n"
                "    def test_runs(self):\n"
                f"        Path({str(marker)!r}).write_text('ran')\n")
            groups = {"core": {broken + ".py", valid + ".py"}}
            with mock.patch.object(runner, "partitions", return_value=groups), \
                 mock.patch.object(runner, "configure_import_paths"), \
                 mock.patch.object(sys, "path", [str(root), *sys.path]), \
                 mock.patch.dict(sys.modules), \
                 contextlib.redirect_stderr(io.StringIO()) as stderr, \
                 contextlib.redirect_stdout(io.StringIO()):
                result = runner.main(["--lane", "core"])
            self.assertEqual(result, 1)
            self.assertEqual(marker.read_text(), "ran")
            self.assertIn("fixture missing dependency", stderr.getvalue())
            self.assertIn("Ran 2 tests", stderr.getvalue())

    def test_nested_learning_runs_in_same_suite_after_an_ordinary_failure(self):
        seen=[]
        class Failed(unittest.TestCase):
            def runTest(self):
                seen.append("ordinary")
                self.fail("fixture")
        class Nested(unittest.TestCase):
            def runTest(self):
                seen.append("nested")
        def load(name):
            return unittest.TestSuite([Failed() if name=="a_ordinary" else Nested()])
        with mock.patch.object(runner, "partitions", return_value={"learning":{"a_ordinary.py"}}), \
             mock.patch.object(runner, "NESTED_LEARNING_MODULES", frozenset({"z_nested"})), \
             mock.patch.object(unittest.TestLoader, "loadTestsFromName", side_effect=load), \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(["--lane", "learning"]), 1)
        self.assertEqual(seen, ["ordinary", "nested"])


if __name__ == "__main__":
    unittest.main()


class ExternalResearchPartitionTests(unittest.TestCase):
    def test_native_fixtures_are_not_implicit_core_requirements(self):
        groups = runner.partitions(ROOT)
        self.assertEqual(groups["external"], set(runner.EXTERNAL_TESTS))
        self.assertFalse(groups["external"] & groups["core"])
        self.assertIn("test_train_v2_native_wire_semantics.py", groups["core"])
        self.assertTrue(all((ROOT / "tests/python" / name).is_file()
                            for name in groups["external"]))


class ChildImportTests(unittest.TestCase):
    def test_clean_environment_subprocess_uses_exact_checkout_modules(self):
        import os, subprocess, sys, tempfile
        from unittest.mock import patch
        prior = list(sys.path)
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('PYTHONPATH', None)
                runner.configure_import_paths()
                with tempfile.TemporaryDirectory() as directory:
                    child = subprocess.run([sys.executable, '-c',
                        'import historical_training, train_v2; print(train_v2.__file__)'],
                        cwd=directory, text=True, capture_output=True, timeout=15)
                self.assertEqual(child.returncode, 0, child.stderr)
                self.assertIn(str(ROOT / 'research/train_v2'), child.stdout)
        finally:
            sys.path[:] = prior
