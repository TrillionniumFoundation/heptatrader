from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "self-hosted-ib-availability.yml"
REASON_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,80}\Z")


class SelfHostedIbAvailabilityWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_dispatch_input_is_only_transferred_through_environment(self) -> None:
        expression = "${{ inputs.reason }}"
        self.assertEqual(self.workflow.count(expression), 1)
        self.assertIn(f"PROBE_REASON: {expression}", self.workflow)
        self.assertNotIn(f"'{expression}'", self.workflow)
        self.assertNotIn(f'"{expression}"', self.workflow)

    def test_broker_host_job_is_dispatch_only_and_tokenless(self) -> None:
        section = self.workflow.split("  ib-runner-probe:\n", 1)[1]
        self.assertIn("if: github.event_name == 'workflow_dispatch'", section)
        self.assertIn("permissions: {}", section)
        self.assertIn('test "$GITHUB_REF" = refs/heads/main', section)
        self.assertIn('test "$RUNNER_NAME" = x230', section)
        self.assertNotIn("actions/checkout", section)
        self.assertNotIn("secrets.", section)

    def test_reason_is_quoted_and_allowlisted_before_output(self) -> None:
        self.assertIn(
            '[[ "$PROBE_REASON" =~ ^[A-Za-z0-9._:-]{1,80}$ ]]',
            self.workflow,
        )
        self.assertIn('"$PROBE_REASON"', self.workflow)

        for value in (
            "operator-check",
            "current-readiness-20260906",
            "ops.probe:v1_2",
        ):
            with self.subTest(value=value):
                self.assertIsNotNone(REASON_PATTERN.fullmatch(value))

        for value in (
            "",
            "contains space",
            "ok'; id; #",
            "line1\nline2",
            "$(id)",
            "`id`",
            "a" * 81,
        ):
            with self.subTest(value=value):
                self.assertIsNone(REASON_PATTERN.fullmatch(value))

    def test_pull_request_path_runs_only_hosted_static_audit(self) -> None:
        section = self.workflow.split("  bootstrap-audit:\n", 1)[1].split(
            "  ib-runner-probe:\n", 1
        )[0]
        self.assertIn("if: github.event_name == 'pull_request'", section)
        self.assertIn("runs-on: ubuntu-24.04", section)
        self.assertIn("persist-credentials: false", section)
        self.assertIn("test_self_hosted_ib_availability.py", section)


if __name__ == "__main__":
    unittest.main()
