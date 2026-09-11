from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ib-paper-qualification.yml"


class IbWorkflowInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_candidate_is_built_once_and_reused_everywhere(self) -> None:
        self.assertEqual(
            self.workflow.count("trusted/scripts/build_ib_candidate_artifact.sh"), 1
        )
        self.assertEqual(
            self.workflow.count("Upload the single immutable no-secret candidate"), 1
        )
        artifact = (
            "ib-paper-candidate-${{ github.sha }}-${{ github.run_id }}-"
            "${{ github.run_attempt }}"
        )
        self.assertGreaterEqual(self.workflow.count(artifact), 6)
        self.assertEqual(self.workflow.count("path: candidate"), 1)
        self.assertNotIn("ref: ${{ inputs.candidate_sha }}", self.workflow)
        self.assertNotIn("candidate/scripts/", self.workflow)

    def test_exact_dispatch_main_becomes_immutable_artifact_identity(self) -> None:
        self.assertGreaterEqual(
            self.workflow.count("inputs.candidate_sha == github.sha"), 6
        )
        self.assertIn("Require exact dispatch-main candidate identity", self.workflow)
        self.assertNotIn("git ls-remote --exit-code", self.workflow)
        self.assertNotIn("main-before-campaign.txt", self.workflow)
        self.assertNotIn("main-after-campaign.txt", self.workflow)

    def test_lightweight_host_preflight_precedes_mutation(self) -> None:
        preflight, canary = self.workflow.split("\n  preflight:\n", 1)[1].split(
            "\n  canary:\n", 1
        )
        self.assertIn("ib-paper-lightweight-host-preflight", preflight)
        self.assertIn("/usr/libexec/hepta-ib-paper-host-probe", preflight)
        self.assertIn("HEPTA_IB_PAPER_HOST_PROBE_SHA256", preflight)
        self.assertNotIn("environment: ib-paper", preflight)
        self.assertIn("needs: preflight", canary)
        self.assertIn("environment: ib-paper", canary)

    def test_progressive_stages_do_not_widen_p1_exposure(self) -> None:
        self.assertEqual(self.workflow.count("run_ib_paper_artifact_rollout.sh"), 3)
        self.assertEqual(self.workflow.count("verify_ib_paper_rollout.py"), 3)
        self.assertIn("--expected-stage canary", self.workflow)
        self.assertIn("--expected-stage pilot", self.workflow)
        self.assertIn("--expected-stage extended", self.workflow)
        self.assertIn("Run one minimal terminal PAPER-V4 round trip", self.workflow)
        self.assertIn("Run three independently flat PAPER-V4 round trips", self.workflow)
        self.assertIn("Run ten independently flat PAPER-V4 round trips", self.workflow)

    def test_heavy_certification_is_explicit_and_last(self) -> None:
        qualify = self.workflow.split("\n  qualify:\n", 1)[1]
        self.assertIn("inputs.rollout_stage == 'certify'", qualify)
        self.assertIn("needs: extended", qualify)
        self.assertEqual(qualify.count("run_ib_paper_artifact_qualification.sh"), 1)
        self.assertEqual(qualify.count("verify_ib_paper_qualification.py"), 1)
        self.assertIn("Run explicit heavy twelve-scenario PAPER certification", qualify)

    def test_mutation_jobs_use_protected_environment(self) -> None:
        self.assertEqual(self.workflow.count("\n    environment: ib-paper\n"), 4)
        for job, next_job in (
            ("canary", "pilot"),
            ("pilot", "extended"),
            ("extended", "qualify"),
        ):
            block = self.workflow.split(f"\n  {job}:\n", 1)[1].split(
                f"\n  {next_job}:\n", 1
            )[0]
            self.assertIn("environment: ib-paper", block)
        self.assertIn("environment: ib-paper", self.workflow.split("\n  qualify:\n", 1)[1])

    def test_only_immutable_owner_can_allocate_self_hosted_jobs(self) -> None:
        for token in (
            "github.actor == 'ProfHepta'",
            "github.actor_id == 102159240",
            "github.triggering_actor == 'ProfHepta'",
            "inputs.mutation_mode == true",
            "inputs.candidate_sha == github.sha",
        ):
            self.assertGreaterEqual(self.workflow.count(token), 6)

    def test_full_qualification_and_progressive_rollout_share_trusted_harness(self) -> None:
        for token in (
            "HEPTA_IB_PAPER_QUALIFIER",
            "HEPTA_IB_PAPER_QUALIFIER_SHA256",
            "HEPTA_QUALIFICATION_MUTATIONS: '1'",
            "trusted/scripts/run_ib_paper_artifact_rollout.sh",
            "trusted/scripts/run_ib_paper_artifact_qualification.sh",
        ):
            self.assertIn(token, self.workflow)

    def test_repository_governance_is_not_broker_authority(self) -> None:
        for token in (
            "pull_number",
            "CODEOWNERS",
            "repository-governance",
            "merge_queue",
        ):
            self.assertNotIn(token, self.workflow)


if __name__ == "__main__":
    unittest.main()
