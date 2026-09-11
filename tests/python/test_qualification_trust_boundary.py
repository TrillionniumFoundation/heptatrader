from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_qualification_trust_boundary as boundary  # noqa: E402


class QualificationTrustBoundaryTests(unittest.TestCase):
    def test_repository_boundary_passes(self) -> None:
        self.assertEqual(boundary.validate(ROOT), [])

    def test_governance_formalism_is_absent(self) -> None:
        workflow = (ROOT / boundary.WORKFLOW).read_text(encoding="utf-8")
        for token in (
            "pull_number",
            "CODEOWNERS",
            "repository-governance",
            "verify_qualification_candidate.py",
            "git ls-remote --exit-code",
        ):
            self.assertNotIn(token, workflow)
        for relative in boundary.RETIRED:
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_owner_identity_gates_every_self_hosted_job_before_runner_allocation(self) -> None:
        workflow = (ROOT / boundary.WORKFLOW).read_text(encoding="utf-8")
        self.assertEqual(workflow.count(boundary.OWNER_GATE), 6)
        for job in (
            "build-candidate",
            "preflight",
            "canary",
            "pilot",
            "extended",
            "qualify",
        ):
            block = workflow.split(f"\n  {job}:\n", 1)[1]
            condition = block.split("\n    name:", 1)[0]
            self.assertIn("github.actor == 'ProfHepta'", condition)
            self.assertIn("github.actor_id == 102159240", condition)
            self.assertIn("github.triggering_actor == 'ProfHepta'", condition)
            self.assertIn("inputs.mutation_mode == true", condition)
            self.assertIn("inputs.candidate_sha == github.sha", condition)
            self.assertLess(condition.index("github.actor"), block.index("runs-on:"))

    def test_build_and_preflight_do_not_request_mutation_environment(self) -> None:
        workflow = (ROOT / boundary.WORKFLOW).read_text(encoding="utf-8")
        build = workflow.split("\n  build-candidate:\n", 1)[1].split("\n  preflight:\n", 1)[0]
        preflight = workflow.split("\n  preflight:\n", 1)[1].split("\n  canary:\n", 1)[0]
        self.assertNotIn("environment: ib-paper", build)
        self.assertNotIn("environment: ib-paper", preflight)
        self.assertIn("/usr/libexec/hepta-ib-paper-host-probe", preflight)

    def test_all_mutation_stages_are_environment_gated(self) -> None:
        workflow = (ROOT / boundary.WORKFLOW).read_text(encoding="utf-8")
        self.assertEqual(workflow.count("\n    environment: ib-paper\n"), 4)
        for job, next_job in (
            ("canary", "pilot"),
            ("pilot", "extended"),
            ("extended", "qualify"),
        ):
            block = workflow.split(f"\n  {job}:\n", 1)[1].split(f"\n  {next_job}:\n", 1)[0]
            self.assertIn("environment: ib-paper", block)
        self.assertIn("environment: ib-paper", workflow.split("\n  qualify:\n", 1)[1])

    def test_candidate_exact_tree_verification_brackets_the_only_build(self) -> None:
        workflow = (ROOT / boundary.WORKFLOW).read_text(encoding="utf-8")
        build = workflow.split("\n  build-candidate:\n", 1)[1].split("\n  preflight:\n", 1)[0]
        candidate = "python3 trusted/scripts/verify_exact_git_index.py --root candidate"
        self.assertEqual(build.count(candidate), 2)
        self.assertEqual(workflow.count("trusted/scripts/build_ib_candidate_artifact.sh"), 1)
        self.assertEqual(workflow.count("path: candidate"), 1)
        self.assertLess(build.index(candidate), build.index("Build the single immutable candidate artifact"))
        self.assertGreater(build.rindex(candidate), build.index("Build the single immutable candidate artifact"))

    def test_non_dispatch_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / boundary.WORKFLOW
            path.parent.mkdir(parents=True)
            shutil.copy2(ROOT / boundary.WORKFLOW, path)
            text = path.read_text(encoding="utf-8")
            path.write_text(
                text.replace(
                    "inputs.candidate_sha == github.sha",
                    "inputs.candidate_sha != github.sha",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any("six self-hosted jobs" in item for item in boundary.validate(root))
            )

    def test_mutable_main_rechecks_are_forbidden_after_artifact_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / boundary.WORKFLOW
            path.parent.mkdir(parents=True)
            shutil.copy2(ROOT / boundary.WORKFLOW, path)
            text = path.read_text(encoding="utf-8")
            marker = "      - name: Run explicit heavy twelve-scenario PAPER certification\n"
            path.write_text(
                text.replace(
                    marker,
                    "      - name: Obsolete mutable-main proof\n"
                    "        run: git ls-remote --exit-code "
                    "https://github.com/example/example refs/heads/main\n\n"
                    + marker,
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any("must not depend on mutable main" in item for item in boundary.validate(root))
            )

    def test_progressive_rollout_precedes_explicit_heavy_certification(self) -> None:
        workflow = (ROOT / boundary.WORKFLOW).read_text(encoding="utf-8")
        self.assertEqual(workflow.count("run_ib_paper_artifact_rollout.sh"), 3)
        qualify = workflow.split("\n  qualify:\n", 1)[1]
        self.assertIn("needs: extended", qualify)
        self.assertIn("inputs.rollout_stage == 'certify'", qualify)
        self.assertEqual(qualify.count("run_ib_paper_artifact_qualification.sh"), 1)

    def test_builder_and_paper_runners_are_distinct(self) -> None:
        workflow = (ROOT / boundary.WORKFLOW).read_text(encoding="utf-8")
        build = workflow.split("\n  build-candidate:\n", 1)[1].split("\n  preflight:\n", 1)[0]
        rest = workflow.split("\n  preflight:\n", 1)[1]
        self.assertIn(
            "labels: [self-hosted, linux, x64, heptatrader-ib-builder]", build
        )
        self.assertNotIn(
            "labels: [self-hosted, linux, x64, heptatrader-ib-paper]", build
        )
        self.assertIn(
            "labels: [self-hosted, linux, x64, heptatrader-ib-paper]", rest
        )
        self.assertNotIn("secrets.", build)


if __name__ == "__main__":
    unittest.main()
