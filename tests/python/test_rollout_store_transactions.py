"""Real local-filesystem tests with deterministic fsync/write/rename failures."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RolloutStoreTransactionTests(unittest.TestCase):
    def test_file_identity_durability_and_readiness(self) -> None:
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "g++ is required for rollout verification")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "rollout-transactions"
            built = subprocess.run(
                [compiler, "-std=c++17", "-O0", "-Wall", "-Wextra", "-Werror", "-pthread",
                 "-I", str(ROOT / "HeptaTrade"),
                 str(ROOT / "tests/rollout_store_transaction_tests.cpp"),
                 "-Wl,--wrap=fsync", "-Wl,--wrap=renameat", "-Wl,--wrap=write", "-o", str(binary)],
                text=True, capture_output=True, timeout=120, check=False,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run(
                [str(binary)], text=True, capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("rollout_transaction_assertions=", result.stdout)


if __name__ == "__main__":
    unittest.main()
