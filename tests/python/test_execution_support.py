from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ExecutionSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.work = tempfile.TemporaryDirectory(prefix="hepta-send-index-")
        cls.addClassCleanup(cls.work.cleanup)
        cls.binary = Path(cls.work.name) / "send-index-vectors"
        subprocess.run(
            [os.environ.get("CXX", "g++"), "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
             str(ROOT / "tests/execution_support_vectors.cpp"), "-o", str(cls.binary)],
            check=True, capture_output=True, timeout=60,
        )

    def test_scope_cutoff_duplicate_clock_and_recovery_parity(self) -> None:
        subprocess.run([str(self.binary)], check=True, capture_output=True, timeout=20)

    def test_typed_submission_preserves_ambiguity_without_retry(self) -> None:
        subprocess.run([str(self.binary), "--outcomes"], check=True, capture_output=True, timeout=20)

    def test_history_growth_measurements_preserve_all_identities(self) -> None:
        result = subprocess.run(
            [str(self.binary), "--measure"], check=True, capture_output=True, text=True, timeout=30,
        )
        measurements = json.loads(result.stdout)
        self.assertEqual([row["retained_attempts"] for row in measurements], [1000, 10000, 200000])
        for row in measurements:
            self.assertEqual(row["window_results"], 6)
            self.assertEqual(row["samples"], 1000)
            self.assertLessEqual(0, row["p50_ns"])
            self.assertLessEqual(row["p50_ns"], row["p95_ns"])
            self.assertLessEqual(row["p95_ns"], row["p99_ns"])
            self.assertLessEqual(row["p99_ns"], row["max_ns"])
        # Actual observed timing, not a CI latency threshold or a production SLO.
        print("SEND_ATTEMPT_INDEX_MEASUREMENTS " + result.stdout.strip(), flush=True)


if __name__ == "__main__":
    unittest.main()
