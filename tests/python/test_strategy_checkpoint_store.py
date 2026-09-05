from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class StrategyCheckpointStoreTests(unittest.TestCase):
    def test_real_payload_persistence_and_fenced_restore(self) -> None:
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "g++ is required for checkpoint verification")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "strategy-checkpoint-tests"
            compiled = subprocess.run(
                [str(compiler), "-std=c++17", "-O0", "-g0", "-DNDEBUG",
                 "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-fno-elide-constructors",
                 "-pthread", "-I", str(ROOT / "HeptaTrade"),
                 str(ROOT / "tests/strategy_checkpoint_store_tests.cpp"),
                 str(ROOT / "HeptaTrade/strategy_runtime/strategy_checkpoint_store.cpp"),
                 "-Wl,--wrap=write,--wrap=pread,--wrap=fsync,--wrap=renameat", "-lcrypto",
                 "-o", str(binary)],
                cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            executed = subprocess.run([str(binary)], cwd=ROOT, capture_output=True,
                                      text=True, timeout=60, check=False)
            self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
            self.assertIn("checkpoint_assertions=", executed.stdout)


if __name__ == "__main__":
    unittest.main()
