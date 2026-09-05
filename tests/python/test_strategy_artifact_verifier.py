from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class StrategyArtifactVerifierTests(unittest.TestCase):
    def test_signed_bundle_loading_and_verified_metadata_entry(self) -> None:
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "g++ is required for artifact verification")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "strategy-artifact-tests"
            compiled = subprocess.run(
                [str(compiler), "-std=c++17", "-O0", "-g0", "-DNDEBUG",
                 "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-fno-elide-constructors",
                 "-pthread", "-I", str(ROOT / "HeptaTrade"),
                 str(ROOT / "tests/strategy_artifact_verifier_tests.cpp"),
                 str(ROOT / "HeptaTrade/strategy_runtime/strategy_artifact_verifier.cpp"),
                 str(ROOT / "HeptaTrade/strategy_runtime/strategy_checkpoint_store.cpp"),
                 "-Wl,--wrap=pread,--wrap=close,--wrap=EVP_DigestVerifyInit", "-lcrypto",
                 "-o", str(binary)],
                cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            executed = subprocess.run([str(binary)], cwd=ROOT, capture_output=True,
                                      text=True, timeout=60, check=False)
            self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
            self.assertIn("artifact_verifier_assertions=", executed.stdout)


if __name__ == "__main__":
    unittest.main()
