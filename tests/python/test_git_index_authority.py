from __future__ import annotations

from pathlib import Path
import unittest

import exact_git_index_authority_cases as cases

ROOT = Path(__file__).resolve().parents[2]
authority = cases.authority


class ExactGitIndexAuthorityTests(
    cases.ExactGitIndexAuthorityTests
):
    """Run generic exact-index tests under the owner-operated model."""

    def test_source_audit_is_exact_head_bound_and_path_scoped(self) -> None:
        relative = Path(
            ".github/workflows/qualification-source-audit.yml"
        )
        workflow = (ROOT / relative).read_text(encoding="utf-8")
        trigger = workflow.split("\npermissions:", 1)[0]
        self.assertIn(
            "  pull_request:\n    branches: [main]", trigger
        )
        self.assertIn("  push:\n    branches: [main]", trigger)
        self.assertIn("    paths:", trigger)
        self.assertIn("scripts/run_ib_paper_artifact_rollout.sh", trigger)
        self.assertIn("scripts/verify_ib_paper_rollout.py", trigger)
        self.assertNotIn("pull_request_target", trigger)
        self.assertIn(
            "cancel-in-progress: "
            "${{ github.event_name == 'pull_request' }}",
            workflow,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha "
            "|| github.sha }}",
            workflow,
        )
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn(
            "- name: Reassert immutable event subject",
            workflow,
        )
        self.assertIn("git diff --cached --exit-code -- .", workflow)
        self.assertGreaterEqual(
            workflow.count("python3 scripts/verify_exact_git_index.py --root ."),
            2,
        )

    def test_ib_workflow_builds_once_then_uses_exact_artifact(self) -> None:
        relative = ".github/workflows/ib-paper-qualification.yml"
        workflow = (ROOT / relative).read_text(encoding="utf-8")
        trigger = workflow.split("\npermissions:", 1)[0]
        self.assertIn("on:\n  workflow_dispatch:", trigger)
        self.assertNotIn("pull_request", trigger)
        self.assertIn("cancel-in-progress: false", workflow)
        condition = (
            "github.event_name == 'workflow_dispatch' && "
            "github.ref == 'refs/heads/main' && "
            "github.repository == 'TrillionniumFoundation/heptatrader' && "
            "github.actor == 'ProfHepta' && "
            "github.actor_id == 102159240 && "
            "github.triggering_actor == 'ProfHepta' && "
            "inputs.mutation_mode == true && "
            "inputs.candidate_sha == github.sha"
        )
        self.assertEqual(workflow.count(condition), 6)
        self.assertNotIn("git ls-remote --exit-code", workflow)
        self.assertNotIn("Issue final exact-current-main Broker receipt", workflow)
        self.assertIn(relative, authority.CRITICAL_PATHS)
        self.assertNotIn("CODEOWNERS", workflow)
        self.assertNotIn("merge_queue", workflow)
        self.assertEqual(
            workflow.count(
                "python3 trusted/scripts/verify_exact_git_index.py "
                "--root candidate"
            ),
            2,
        )
        self.assertEqual(
            workflow.count("trusted/scripts/build_ib_candidate_artifact.sh"), 1
        )
        self.assertEqual(workflow.count("path: candidate"), 1)
        self.assertEqual(workflow.count("run_ib_paper_artifact_rollout.sh"), 3)
        self.assertEqual(workflow.count("run_ib_paper_artifact_qualification.sh"), 1)

    def test_critical_paths_match_current_model(self) -> None:
        paths = set(authority.CRITICAL_PATHS)
        self.assertIn("docs/gap-register.json", paths)
        self.assertIn(
            "scripts/check_qualification_trust_boundary.py", paths
        )
        self.assertIn(
            ".github/workflows/ib-paper-qualification.yml", paths
        )
        self.assertTrue(
            all("CODEOWNERS" not in item for item in paths), paths
        )
        for relative in paths:
            self.assertTrue((ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
