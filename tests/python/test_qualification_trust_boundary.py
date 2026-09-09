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
            "merge_group",
            "repository-governance",
            "verify_qualification_candidate.py",
        ):
            self.assertNotIn(token, workflow)
        for relative in boundary.RETIRED:
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_owner_identity_gates_both_jobs_before_runner_allocation(self) -> None:
        workflow = (ROOT / boundary.WORKFLOW).read_text(encoding="utf-8")
        build, qualify = workflow.split("\n  qualify:\n", 1)
        for block in (build, qualify):
            condition = block.split("\n    name:", 1)[0]
            self.assertIn("github.actor == 'ProfHepta'", condition)
            self.assertIn("github.actor_id == 102159240", condition)
            self.assertIn("github.triggering_actor == 'ProfHepta'", condition)
            self.assertLess(condition.index("github.actor"), block.index("runs-on:"))
            self.assertIn(
                "Bind dispatch authority to immutable owner identity", block
            )

    def test_unauthorized_owner_dispatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / boundary.WORKFLOW
            path.parent.mkdir(parents=True)
            shutil.copy2(ROOT / boundary.WORKFLOW, path)
            text = path.read_text(encoding="utf-8")
            path.write_text(
                text.replace(
                    "github.actor == 'ProfHepta'",
                    "github.actor != 'ProfHepta'",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(
                any(
                    "immutable owner dispatch authority" in item
                    for item in boundary.validate(root)
                )
            )

    def test_exact_tree_verification_brackets_build_and_campaign(self) -> None:
        workflow = (ROOT / boundary.WORKFLOW).read_text(encoding="utf-8")
        build, qualify = workflow.split("\n  qualify:\n", 1)
        trusted = (
            "python3 trusted/scripts/verify_exact_git_index.py --root trusted"
        )
        candidate = (
            "python3 trusted/scripts/verify_exact_git_index.py --root candidate"
        )
        self.assertEqual(build.count(candidate), 2)
        self.assertGreaterEqual(build.count(trusted), 2)
        self.assertLess(build.index(candidate), build.index("Build content-addressed binary"))
        self.assertGreater(
            build.rindex(candidate), build.index("Build content-addressed binary")
        )
        self.assertGreaterEqual(qualify.count(trusted), 2)
        self.assertLess(
            qualify.index(trusted),
            qualify.index("Run controlled PAPER campaign"),
        )
        self.assertGreater(
            qualify.rindex(trusted),
            qualify.index("Run controlled PAPER campaign"),
        )

    def test_non_current_candidate_is_rejected(self) -> None:
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
                any("exact current main" in item for item in boundary.validate(root))
            )

    def test_builder_and_paper_runners_are_distinct(self) -> None:
        workflow = (ROOT / boundary.WORKFLOW).read_text(encoding="utf-8")
        build, qualify = workflow.split("\n  qualify:\n", 1)
        self.assertIn(
            "labels: [self-hosted, linux, x64, heptatrader-ib-builder]", build
        )
        self.assertNotIn(
            "labels: [self-hosted, linux, x64, heptatrader-ib-paper]", build
        )
        self.assertIn(
            "labels: [self-hosted, linux, x64, heptatrader-ib-paper]", qualify
        )
        self.assertNotIn(
            "labels: [self-hosted, linux, x64, heptatrader-ib-builder]", qualify
        )
        self.assertNotIn("secrets.", build)


if __name__ == "__main__":
    unittest.main()
