from __future__ import annotations
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
