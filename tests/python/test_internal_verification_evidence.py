from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class InternalVerificationEvidenceTests(unittest.TestCase):
    def test_event_ordering_is_behavioral(self) -> None:
        corpus = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tests").glob("*.cpp")
        ).lower()
        self.assertRegex(corpus, r"duplicate|idempotent")
        self.assertRegex(corpus, r"out.?of.?order|sequence.?gap")
        market = read("tests/sharded_market_data_tests.cpp")
        self.assertIn("producerEpoch", market)
        self.assertIn("sequenceGap", market)

    def test_reconciliation_has_fault_and_recovery_evidence(self) -> None:
        corpus = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tests").glob("*.cpp")
        ).lower()
        self.assertRegex(corpus, r"reconcil")
        self.assertRegex(corpus, r"diverg|mismatch|uncertain")

    def test_strategy_shadow_and_quarantine_are_excluded(self) -> None:
        evidence = read("tests/multi_agent_allocation_tests.cpp")
        self.assertIn("TestActiveCycleAndIgnoredShadow", evidence)
        self.assertIn("TestQuarantineFaultIsolation", evidence)
        self.assertIn("ignoredModules", evidence)

    def test_all_nonimplemented_checks_are_external_lane_d(self) -> None:
        matrix = json.loads(read("docs/verification/test-matrix-v2.json"))
        for check in matrix["checks"]:
            if check.get("state") != "implemented":
                self.assertEqual(check.get("state"), "external")
                self.assertEqual(check.get("lane"), "D-external-qualification")

    def test_only_real_platform_and_paper_gaps_remain(self) -> None:
        registry = json.loads(read("docs/program/gap-registry-v2.json"))
        remaining = {
            gap["id"]: gap["state"]
            for gap in registry["gaps"]
            if gap.get("state") != "closed"
        }
        self.assertTrue(
            set(remaining).issubset({"G-IB-001", "G-TEAM-001"})
        )
        for state in remaining.values():
            self.assertEqual(state, "in-progress")


if __name__ == "__main__":
    unittest.main()
