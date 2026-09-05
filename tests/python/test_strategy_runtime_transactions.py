"""Compile the production header and deterministic transaction regression tests."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class StrategyRuntimeTransactionTests(unittest.TestCase):
    def test_transactional_state_and_failure_paths(self) -> None:
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "g++ is required for runtime verification")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "strategy-runtime-transactions"
            built = subprocess.run(
                [compiler, "-std=c++11", "-O0", "-Wall", "-Wextra", "-Werror",
                 "-pthread", "-I", str(ROOT / "HeptaTrade"),
                 str(ROOT / "tests/strategy_runtime_transaction_tests.cpp"),
                 "-o", str(binary)],
                text=True, capture_output=True, timeout=120, check=False,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            tested = subprocess.run(
                [str(binary)], text=True, capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
            self.assertIn("allocation_faults=", tested.stdout)
            self.assertIn("strategy_transaction_assertions=", tested.stdout)


if __name__ == "__main__":
    unittest.main()
