from __future__ import annotations

from pathlib import Path
import unittest

import exact_git_index_authority_cases as cases

ROOT = Path(__file__).resolve().parents[2]
authority = cases.authority


class ExactGitIndexAuthorityTests(cases.ExactGitIndexAuthorityTests):
    """Run the full exact-index suite under the split hosted-audit topology."""

    def test_all_main_pull_requests_run_the_governance_boundary(self) -> None:
        for relative in (
            Path(".github/workflows/governance-bootstrap-admission.yml"),
            Path(".github/workflows/qualification-source-audit.yml"),
        ):
            with self.subTest(workflow=relative.as_posix()):
                workflow = (ROOT / relative).read_text(encoding="utf-8")
                trigger_header = workflow.split("\npermissions:", 1)[0]
                self.assertIn("  pull_request:\n    branches: [main]", trigger_header)
                self.assertIn(
                    "  merge_group:\n    types: [checks_requested]", trigger_header
                )
                self.assertNotIn("    paths:", trigger_header)
                self.assertNotIn("pull_request_target", trigger_header)
                self.assertIn(
                    "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
                    workflow,
                )
                self.assertIn(
                    "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
                    workflow,
                )
                self.assertIn("persist-credentials: false", workflow)
                self.assertIn(
                    "- name: Reassert immutable event subject\n        if: always()",
                    workflow,
                )
                self.assertIn("git diff --cached --exit-code -- .", workflow)
                self.assertIn("--untracked-files=all --ignored=matching", workflow)

        governance = (
            ROOT / ".github/workflows/github-governance-qualification.yml"
        ).read_text(encoding="utf-8")
        governance_trigger = governance.split("\npermissions:", 1)[0]
        self.assertIn("on:\n  workflow_dispatch:", governance_trigger)
        self.assertNotIn("pull_request", governance_trigger)
        self.assertIn("cancel-in-progress: false", governance)
        self.assertIn(
            ".github/workflows/github-governance-qualification.yml",
            authority.CRITICAL_PATHS,
        )

    def test_ib_workflow_is_part_of_the_exact_index_boundary(self) -> None:
        relative = ".github/workflows/ib-paper-qualification.yml"
        workflow = (ROOT / relative).read_text(encoding="utf-8")
        trigger_header = workflow.split("\npermissions:", 1)[0]
        self.assertIn("on:\n  workflow_dispatch:", trigger_header)
        self.assertNotIn("pull_request", trigger_header)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn(relative, authority.CRITICAL_PATHS)

        admission = (
            ROOT / ".github/workflows/governance-bootstrap-admission.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(relative, admission)
        self.assertIn("verify_dispatch_workflow", admission)

        source_audit = (
            ROOT / ".github/workflows/qualification-source-audit.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("test_ib_paper_qualification.py", source_audit)
        self.assertIn("test_ib_workflow_interfaces.py", source_audit)


if __name__ == "__main__":
    unittest.main()
