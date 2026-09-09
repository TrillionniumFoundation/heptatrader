from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ib-paper-qualification.yml"


class IbWorkflowInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_trusted_scripts_drive_build_and_qualification(self) -> None:
        for token in (
            "trusted/scripts/build_ib_candidate_artifact.sh",
            "python3 trusted/scripts/verify_ib_candidate_artifact.py",
            "trusted/scripts/run_ib_paper_artifact_qualification.sh",
            "python3 trusted/scripts/verify_ib_paper_qualification.py",
        ):
            self.assertIn(token, self.workflow)
        self.assertNotIn("candidate/scripts/", self.workflow)

    def test_exact_current_main_is_rechecked(self) -> None:
        self.assertEqual(
            self.workflow.count("inputs.candidate_sha == github.sha"), 2
        )
        self.assertGreaterEqual(self.workflow.count("git ls-remote --exit-code"), 3)
        self.assertIn(
            "Reverify unchanged remote main after Broker campaign", self.workflow
        )

    def test_repository_governance_is_not_an_interface(self) -> None:
        for token in (
            "pull_number",
            "merge_group",
            "CODEOWNERS",
            "repository-governance",
            "environment: ib-paper",
        ):
            self.assertNotIn(token, self.workflow)


if __name__ == "__main__":
    unittest.main()
