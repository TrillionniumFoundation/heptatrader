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

    def test_future_test_defaults_to_core_and_install_is_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / "tests/python"
            tests.mkdir(parents=True)
            for name in runner.SOURCE_TESTS | runner.INSTALL_TESTS | runner.PROCESS_TESTS | {"test_new_behavior.py"}:
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


if __name__ == "__main__":
    unittest.main()
