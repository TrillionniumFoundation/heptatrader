from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PerformanceBudgetRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(
            (
                ROOT / "docs/verification/performance-budgets-v1.json"
            ).read_text(encoding="utf-8")
        )
        self.budgets = {
            item["id"]: item for item in self.document["budgets"]
        }

    def test_budget_ids_are_unique_and_states_have_truthful_claim_ceiling(self) -> None:
        self.assertEqual(
            "heptatrader.performance-budgets.v1", self.document["schema"]
        )
        self.assertEqual(len(self.budgets), len(self.document["budgets"]))
        allowed = set(self.document["policy"]["allowed_states"])
        for budget_id, budget in self.budgets.items():
            self.assertIn(budget["state"], allowed, budget_id)
            self.assertGreaterEqual(budget["regression_percent"], 0, budget_id)
            self.assertLessEqual(budget["regression_percent"], 100, budget_id)
            if budget["state"] == "declared":
                self.assertTrue(budget["missing_evidence"].strip(), budget_id)
                self.assertNotIn("baseline", budget, budget_id)
            else:
                self.assertTrue(budget["fixture"].strip(), budget_id)
                self.assertTrue(budget["test_target"].strip(), budget_id)

    def test_risk_budget_uses_one_canonical_baseline_file(self) -> None:
        budget = self.budgets["risk-policy-v1"]
        baseline_path = ROOT / budget["baseline"]
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        self.assertEqual("implemented", budget["state"])
        self.assertEqual(budget["fixture"], baseline["fixture"])
        self.assertEqual(
            budget["regression_percent"],
            baseline["maximum_regression_percent"],
        )
        self.assertGreater(baseline["p99_microseconds"], 0)
        self.assertEqual(
            self.document["policy"]["distribution_required"],
            baseline["required_distribution"],
        )

        cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("core-latency-baseline-v1.json", cmake)
        self.assertIn("string(JSON HEPTA_RISK_P99_BASELINE_US", cmake)
        self.assertIn("string(JSON HEPTA_RISK_MAX_REGRESSION_PERCENT", cmake)
        self.assertIn("heptatrader_performance_budget.h", cmake)

        fixture = (ROOT / "tests/risk_latency_fixture_tests.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn('#include "heptatrader_performance_budget.h"', fixture)
        self.assertNotRegex(fixture, r"#define\s+HEPTA_RISK_P99_BASELINE_US")
        self.assertNotRegex(
            fixture, r"#define\s+HEPTA_RISK_MAX_REGRESSION_PERCENT"
        )
        for percentile in ("p50_us", "p95_us", "p99_us", "p999_us", "max_us"):
            self.assertIn(percentile, fixture)

    def test_implemented_test_targets_exist(self) -> None:
        cmake_text = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in (ROOT / "tests").rglob("CMakeLists.txt")
        )
        for budget_id, budget in self.budgets.items():
            if budget["state"] != "implemented":
                continue
            target = budget["test_target"]
            self.assertRegex(
                cmake_text,
                rf"add_executable\s*\(\s*{re.escape(target)}\b",
                budget_id,
            )

    def test_documentation_does_not_promote_declared_budgets(self) -> None:
        documentation = (
            ROOT / "docs/operations/PERFORMANCE-QUALIFICATION.md"
        ).read_text(encoding="utf-8")
        self.assertIn("declared", documentation)
        self.assertIn("implemented", documentation)
        self.assertIn("不能支持性能", documentation)


if __name__ == "__main__":
    unittest.main()
