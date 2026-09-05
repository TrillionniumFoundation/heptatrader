from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PerformanceBudgetRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(
            (ROOT / "docs/verification/performance-budgets-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.budgets = {item["id"]: item for item in self.document["budgets"]}

    def _assert_baseline(self, budget: dict, prefix: str = "") -> None:
        baseline_key = prefix + "baseline"
        fixture_key = prefix + "fixture"
        source_key = prefix + "fixture_source"
        target_key = prefix + "test_target"
        baseline_path = ROOT / budget[baseline_key]
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        self.assertEqual("heptatrader.latency-baseline.v1", baseline["schema"])
        self.assertEqual(budget[fixture_key], baseline["fixture"])
        self.assertGreater(baseline["p99_microseconds"], 0)
        self.assertGreaterEqual(baseline["maximum_regression_percent"], 0)
        self.assertLessEqual(baseline["maximum_regression_percent"], 100)
        self.assertEqual(
            self.document["policy"]["distribution_required"],
            baseline["required_distribution"],
        )
        for field in (
            "operation_scope",
            "scope",
            "build_type",
            "runner_class",
            "toolchain_binding",
            "claim_ceiling",
        ):
            self.assertTrue(str(baseline[field]).strip(), baseline_path)
        self.assertEqual("repository-ci", baseline["scope"])
        self.assertGreater(baseline["warmup_iterations"], 0)
        self.assertGreater(baseline["sample_count"], 0)
        source = ROOT / budget[source_key]
        self.assertTrue(source.is_file(), source)
        source_text = source.read_text(encoding="utf-8")
        self.assertIn(baseline["fixture"], source_text)
        for percentile in ("p50_us", "p95_us", "p99_us", "p999_us", "max_us"):
            if source.name == "risk_latency_fixture_tests.cpp":
                self.assertIn(percentile, source_text)
            else:
                self.assertIn("ReportAndCheck", source_text)
        self.assertTrue(budget[target_key].strip())

    def test_budget_ids_are_unique_and_states_have_truthful_claim_ceiling(self) -> None:
        self.assertEqual(
            "heptatrader.performance-budgets.v1", self.document["schema"]
        )
        self.assertEqual(len(self.budgets), len(self.document["budgets"]))
        allowed = set(self.document["policy"]["allowed_states"])
        scopes = set(self.document["policy"]["implemented_scopes"])
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
                self.assertIn(budget["scope"], scopes, budget_id)
                if budget["scope"] == "repository-ci":
                    self.assertIn("baseline", budget, budget_id)

    def test_repository_ci_budgets_use_canonical_baselines(self) -> None:
        expected = {
            "gateway-control-v1",
            "snapshot-v1",
            "risk-policy-v1",
            "portfolio-compiler-v1",
        }
        observed = {
            budget_id
            for budget_id, budget in self.budgets.items()
            if budget.get("scope") == "repository-ci" and
            budget.get("state") == "implemented"
        }
        self.assertEqual(expected, observed)
        for budget_id in sorted(expected):
            budget = self.budgets[budget_id]
            self._assert_baseline(budget)
            baseline = json.loads(
                (ROOT / budget["baseline"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                budget["regression_percent"],
                baseline["maximum_regression_percent"],
            )

    def test_execution_target_host_budget_is_not_promoted_by_ci_storage(self) -> None:
        budget = self.budgets["execution-authority-v1"]
        self.assertEqual("declared", budget["state"])
        self.assertEqual("target-host", budget["scope"])
        self.assertNotIn("baseline", budget)
        self.assertIn("PAPER host", budget["missing_evidence"])
        auxiliary = {
            "repository_ci_baseline": budget["repository_ci_baseline"],
            "repository_ci_fixture": budget["repository_ci_fixture"],
            "repository_ci_fixture_source": budget[
                "repository_ci_fixture_source"
            ],
            "repository_ci_test_target": budget[
                "repository_ci_test_target"
            ],
        }
        self._assert_baseline(auxiliary, "repository_ci_")

    def test_cmake_loads_every_repository_latency_baseline(self) -> None:
        cmake = (ROOT / "tests/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("hepta_load_latency_baseline", cmake)
        for budget_id, budget in self.budgets.items():
            if (budget.get("scope") == "repository-ci" and
                    budget_id != "risk-policy-v1"):
                self.assertIn(Path(budget["baseline"]).name, cmake)
                self.assertIn(budget["test_target"], cmake)
        execution = self.budgets["execution-authority-v1"]
        self.assertIn(Path(execution["repository_ci_baseline"]).name, cmake)
        self.assertIn(execution["repository_ci_test_target"], cmake)

        root_cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("core-latency-baseline-v1.json", root_cmake)
        self.assertIn("string(JSON HEPTA_RISK_P99_BASELINE_US", root_cmake)
        fixture = (ROOT / "tests/risk_latency_fixture_tests.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn('#include "heptatrader_performance_budget.h"', fixture)
        self.assertNotRegex(fixture, r"#define\s+HEPTA_RISK_P99_BASELINE_US")

    def test_implemented_and_auxiliary_test_targets_exist(self) -> None:
        cmake_text = "\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in (ROOT / "tests").rglob("CMakeLists.txt")
        )
        targets = {
            budget["test_target"]
            for budget in self.budgets.values()
            if budget["state"] == "implemented"
        }
        targets.add(
            self.budgets["execution-authority-v1"][
                "repository_ci_test_target"
            ]
        )
        for target in sorted(targets):
            self.assertRegex(
                cmake_text,
                rf"add_executable\s*\(\s*{re.escape(target)}\b",
                target,
            )

    def test_documentation_does_not_promote_repository_ci_or_declared_budgets(self) -> None:
        documentation = (
            ROOT / "docs/operations/PERFORMANCE-QUALIFICATION.md"
        ).read_text(encoding="utf-8")
        self.assertIn("declared", documentation)
        self.assertIn("implemented", documentation)
        self.assertIn("repository-ci", documentation)
        self.assertIn("不能支持", documentation)
        self.assertIn("不能成为 PAPER host SLA", documentation)


if __name__ == "__main__":
    unittest.main()
