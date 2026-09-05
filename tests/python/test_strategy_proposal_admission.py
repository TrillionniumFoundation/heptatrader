from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class StrategyProposalAdmissionTests(unittest.TestCase):
    def test_pre_copy_bounds_and_half_open_lifetime(self) -> None:
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "g++ is required for proposal admission verification")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "proposal-admission-tests"
            compiled = subprocess.run(
                [str(compiler), "-std=c++17", "-O1", "-g0", "-DNDEBUG", "-Wall",
                 "-Wextra", "-Wpedantic", "-Werror", "-fno-elide-constructors",
                 "-I", str(ROOT / "HeptaTrade"),
                 str(ROOT / "tests/strategy_proposal_admission_tests.cpp"),
                 str(ROOT / "HeptaTrade/strategy_runtime/strategy_proposal.cpp"),
                 "-lcrypto", "-o", str(binary)],
                cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            executed = subprocess.run([str(binary)], cwd=ROOT, capture_output=True,
                                      text=True, timeout=60, check=False)
            self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
            self.assertIn("proposal_admission_assertions=", executed.stdout)


if __name__ == "__main__":
    unittest.main()
