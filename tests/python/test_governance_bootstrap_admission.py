from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governance-bootstrap-admission.yml"


class GovernanceBootstrapAdmissionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_context_is_explicit_and_reachable_on_pr_and_merge_group(self) -> None:
        self.assertIn("name: Governance Bootstrap Admission", self.workflow)
        self.assertIn("name: governance-bootstrap-admission", self.workflow)
        self.assertIn("  pull_request:\n    branches: [main]", self.workflow)
        self.assertIn("  merge_group:\n    types: [checks_requested]", self.workflow)
        self.assertNotIn("paths:", self.workflow)

    def test_merge_group_runs_are_not_cancelled_by_later_events(self) -> None:
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            self.workflow,
        )
        self.assertNotIn("cancel-in-progress: true", self.workflow)

    def test_checkout_is_exact_and_credential_free(self) -> None:
        self.assertIn(
            "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            self.workflow,
        )
        self.assertIn(
            "repository: ${{ github.event.pull_request.head.repo.full_name || github.repository }}",
            self.workflow,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            self.workflow,
        )
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', self.workflow)

    def test_job_is_read_only_and_contains_no_secret_or_dispatch_surface(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertNotIn("workflow_dispatch", self.workflow)
        self.assertNotIn("pull_request_target", self.workflow)
        self.assertNotIn("repository_dispatch", self.workflow)
        self.assertNotIn("curl ", self.workflow)
        self.assertNotIn("gh ", self.workflow)

    def test_merge_queue_identity_and_clean_postflight_are_fail_closed(self) -> None:
        self.assertIn(
            '[[ "$EVENT_REF" == refs/heads/gh-readonly-queue/main/pr-* ]]',
            self.workflow,
        )
        self.assertIn("if: always()", self.workflow)
        self.assertIn("git diff --exit-code -- .", self.workflow)
        self.assertIn("git diff --cached --exit-code -- .", self.workflow)
        self.assertGreaterEqual(
            self.workflow.count(
                'test -z "$(git status --porcelain=v1 --untracked-files=all)"'
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
