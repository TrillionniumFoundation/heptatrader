"""Execute allocator failures against the real coordinator and journal, no SDK."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RecoveryProjectionTests(unittest.TestCase):
    def test_actual_recovery_allocation_failures_are_atomic(self):
        units = [
            "execution_coordinator.cpp", "execution_coordinator_cancel.cpp",
            "execution_coordinator_recovery.cpp", "execution_coordinator_reconnect.cpp",
            "execution_coordinator_terminal.cpp", "execution_generation_support.cpp",
            "paper_terminal_mutation_manifest.cpp",
            "execution_place_order_dispatch.cpp", "execution_authoritative_flatten.cpp",
            "execution_authoritative_flatten_dispatch.cpp",
        ]
        with tempfile.TemporaryDirectory(prefix="hepta-recovery-faults-") as private:
            executable = Path(private) / "faults"
            command = [os.environ.get("CXX", "g++"), "-std=c++11", "-O0", "-g",
                       "-pthread", "-I", str(ROOT / "HeptaTrade"),
                       str(ROOT / "tests/recovery_projection_faults.cpp")]
            command += [str(ROOT / "HeptaTrade/execution" / unit) for unit in units]
            command += [str(ROOT / "HeptaTrade/oms_journal.cpp"), "-lcrypto", "-lz",
                        "-o", str(executable)]
            built = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=180)
            self.assertEqual(built.returncode, 0, built.stdout)
            result = subprocess.run([str(executable), private], text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("recovery_projection_faults: PASS", result.stdout)
            print(result.stdout, end="", flush=True)


if __name__ == "__main__":
    unittest.main()
