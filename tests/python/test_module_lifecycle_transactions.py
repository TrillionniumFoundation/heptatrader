from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ModuleLifecycleTransactionsTests(unittest.TestCase):
    def test_generation_updates_are_exception_atomic(self) -> None:
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "g++ is required for lifecycle verification")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "module-lifecycle-transactions"
            compiled = subprocess.run(
                [str(compiler), "-std=c++17", "-O0", "-g0", "-DNDEBUG",
                 "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                 "-fno-elide-constructors", "-pthread", "-I", str(ROOT / "HeptaTrade"),
                 str(ROOT / "tests/module_lifecycle_transaction_tests.cpp"),
                 str(ROOT / "HeptaTrade/management/module_lifecycle.cpp"),
                 "-o", str(binary)],
                cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            executed = subprocess.run(
                [str(binary)], cwd=ROOT, capture_output=True, text=True,
                timeout=30, check=False,
            )
            self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
            self.assertIn("lifecycle_transaction_assertions=", executed.stdout)


if __name__ == "__main__":
    unittest.main()
