"""Compile/run the actual secondary index against the old vector semantics.

The existing coordinator tests remain the composition/restart authority. This
adds boundary, clock-regression and randomized index-specific behavior only.
"""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class SendAttemptTimeIndexTests(unittest.TestCase):
    def test_exact_clock_scope_and_randomized_parity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-send-index-") as folder:
            directory = Path(folder)
            main = directory / "main.cpp"
            main.write_text('#include "send_attempt_time_index_cases.h"\n'
                            'int main() { hepta_send_index_test::Run(); }\n', encoding="utf-8")
            compiler = shlex.split(os.environ.get("CXX", "g++"))
            binary = directory / "cases"
            subprocess.run(compiler + ["-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT / "tests"), str(main), "-o", str(binary)],
                check=True, capture_output=True, text=True, timeout=60)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=30)

if __name__ == "__main__":
    unittest.main()
