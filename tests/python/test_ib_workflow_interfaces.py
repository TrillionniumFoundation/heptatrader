from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ib-paper-qualification.yml"
QUALIFICATION_SCRIPT = ROOT / "scripts/run_ib_paper_artifact_qualification.sh"


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

    def test_dispatch_main_is_converted_to_immutable_artifact_identity(self) -> None:
        self.assertEqual(
            self.workflow.count("inputs.candidate_sha == github.sha"), 2
        )
        self.assertIn(
            "Require exact dispatch-main candidate identity", self.workflow
        )
        self.assertIn("Issue final exact-artifact Broker receipt", self.workflow)
        self.assertNotIn("git ls-remote --exit-code", self.workflow)
        self.assertNotIn(
            "Reverify unchanged remote main after Broker campaign", self.workflow
        )
        self.assertNotIn("main-before-campaign.txt", self.workflow)
        self.assertNotIn("main-after-campaign.txt", self.workflow)

    def test_candidate_input_cannot_select_candidate_control_code(self) -> None:
        self.assertEqual(self.workflow.count("ref: ${{ github.sha }}"), 3)
        self.assertEqual(self.workflow.count("${{ inputs.candidate_sha }}"), 1)
        self.assertNotIn("ref: ${{ inputs.candidate_sha }}", self.workflow)

    def test_only_immutable_owner_can_allocate_qualification_runners(self) -> None:
        build, qualify = self.workflow.split("\n  qualify:\n", 1)
        for block in (build, qualify):
            condition = block.split("\n    name:", 1)[0]
            self.assertIn("github.actor == 'ProfHepta'", condition)
            self.assertIn("github.actor_id == 102159240", condition)
            self.assertIn("github.triggering_actor == 'ProfHepta'", condition)
            self.assertIn(
                "Bind dispatch authority to immutable owner identity", block
            )

    def test_real_paper_job_uses_protected_environment(self) -> None:
        build, qualify = self.workflow.split("\n  qualify:\n", 1)
        self.assertNotIn("\n    environment: ib-paper\n", build)
        self.assertEqual(qualify.count("\n    environment: ib-paper\n"), 1)

    def test_exact_tree_verifier_guards_build_and_campaign(self) -> None:
        self.assertEqual(
            self.workflow.count(
                "python3 trusted/scripts/verify_exact_git_index.py "
                "--root candidate"
            ),
            2,
        )
        self.assertGreaterEqual(
            self.workflow.count(
                "python3 trusted/scripts/verify_exact_git_index.py "
                "--root trusted"
            ),
            4,
        )

    def test_repository_governance_is_not_an_interface(self) -> None:
        for token in (
            "pull_number",
            "merge_group",
            "CODEOWNERS",
            "repository-governance",
        ):
            self.assertNotIn(token, self.workflow)

    def test_external_qualification_harness_is_bounded_and_cleaned(self) -> None:
        script = QUALIFICATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("QUALIFICATION_TIMEOUT_SECONDS=900", script)
        self.assertIn(
            "timeout --foreground --signal=TERM --kill-after=30s",
            script,
        )
        self.assertIn("trap cleanup EXIT INT TERM HUP", script)
        self.assertIn("external PAPER harness exceeded", script)


if __name__ == "__main__":
    unittest.main()
