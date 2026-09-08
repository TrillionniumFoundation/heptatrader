from __future__ import annotations

from pathlib import Path
import unittest

import team_codeowners_activation_cases as cases


ROOT = Path(__file__).resolve().parents[2]


class TeamCodeownersActivationTests(cases.TeamCodeownersActivationTests):
    """Run the complete activation suite under the current workflow topology."""

    def test_repository_workflow_concurrency_cancels_only_obsolete_pr_audits(self) -> None:
        cancel_obsolete_pr = (
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}"
        )
        for relative in (
            Path(".github/workflows/governance-bootstrap-admission.yml"),
            Path(".github/workflows/qualification-source-audit.yml"),
        ):
            with self.subTest(workflow=relative.as_posix(), role="pr-audit"):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn(cancel_obsolete_pr, text)
                self.assertNotIn("cancel-in-progress: true", text)

        for relative in (
            Path(".github/workflows/github-governance-qualification.yml"),
            Path(".github/workflows/ib-paper-qualification.yml"),
            Path(".github/workflows/self-hosted-ib-availability.yml"),
        ):
            with self.subTest(workflow=relative.as_posix(), role="dispatch-only"):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("cancel-in-progress: false", text)
                self.assertNotIn(cancel_obsolete_pr, text)
                self.assertNotIn("cancel-in-progress: true", text)


if __name__ == "__main__":
    unittest.main()
