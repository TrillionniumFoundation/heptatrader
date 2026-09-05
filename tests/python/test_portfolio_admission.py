from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PortfolioAdmissionTests(unittest.TestCase):
    def test_complete_policy_and_preallocation_bounds(self) -> None:
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "g++ is required for portfolio admission tests")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "portfolio-admission"
            built = subprocess.run(
                [str(compiler), "-std=c++17", "-O1", "-g0", "-DNDEBUG",
                 "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-fno-elide-constructors",
                 "-pthread", "-I", str(ROOT / "HeptaTrade"),
                 str(ROOT / "tests/portfolio_admission_tests.cpp"),
                 str(ROOT / "HeptaTrade/portfolio/portfolio_compiler.cpp"),
                 "-o", str(binary)],
                cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], cwd=ROOT, capture_output=True,
                                 text=True, timeout=60, check=False)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("portfolio_admission_assertions=", run.stdout)
            self.assertIn("portfolio_oracle_valid_fixtures=500", run.stdout)


if __name__ == "__main__":
    unittest.main()
