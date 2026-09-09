#!/usr/bin/env python3
"""Retarget exact-index protection to the owner-operated trust boundary."""
from __future__ import annotations

from pathlib import Path
import re
import textwrap

ROOT = Path.cwd()


def update_validator() -> None:
    path = ROOT / "scripts/verify_exact_git_index.py"
    text = path.read_text(encoding="utf-8")
    replacement = '''CRITICAL_PATHS = (
    ".github/required-check-contexts-v1.json",
    ".github/workflows/ib-paper-qualification.yml",
    ".github/workflows/qualification-source-audit.yml",
    "docs/capabilities.json",
    "docs/gap-register.json",
    "docs/ib-paper-profile-policy-v1.json",
    "scripts/build_ib_candidate_artifact.sh",
    "scripts/check_qualification_trust_boundary.py",
    "scripts/run_ib_paper_artifact_qualification.sh",
    "scripts/verify_exact_git_index.py",
    "scripts/verify_ib_candidate_artifact.py",
    "scripts/verify_ib_paper_qualification.py",
    "tests/python/test_gap_register.py",
    "tests/python/test_git_index_authority.py",
    "tests/python/test_ib_paper_qualification.py",
    "tests/python/test_qualification_trust_boundary.py",
)


def _env'''
    text, count = re.subn(
        r"CRITICAL_PATHS = \(.*?\n\)\n\n\ndef _env",
        replacement,
        text,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise SystemExit("verify_exact_git_index.py: critical path block changed")
    path.write_text(text, encoding="utf-8")


def trim_generic_cases() -> None:
    path = ROOT / "tests/python/exact_git_index_authority_cases.py"
    text = path.read_text(encoding="utf-8")
    start_marker = (
        "\n    def test_all_main_pull_requests_run_the_governance_boundary"
    )
    end_marker = '\n\nif __name__ == "__main__":'
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        raise SystemExit("exact_git_index_authority_cases.py: legacy tail changed")
    path.write_text(text[:start] + text[end:] + "\n", encoding="utf-8")


def write_repository_cases() -> None:
    path = ROOT / "tests/python/test_git_index_authority.py"
    path.write_text(
        textwrap.dedent(
            '''\
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

                def test_source_audit_is_exact_head_bound(self) -> None:
                    relative = Path(
                        ".github/workflows/qualification-source-audit.yml"
                    )
                    workflow = (ROOT / relative).read_text(encoding="utf-8")
                    trigger = workflow.split("\\npermissions:", 1)[0]
                    self.assertIn(
                        "  pull_request:\\n    branches: [main]", trigger
                    )
                    self.assertIn("  push:\\n    branches: [main]", trigger)
                    self.assertNotIn("    paths:", trigger)
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

                def test_ib_workflow_is_exact_current_main_only(self) -> None:
                    relative = ".github/workflows/ib-paper-qualification.yml"
                    workflow = (ROOT / relative).read_text(encoding="utf-8")
                    trigger = workflow.split("\\npermissions:", 1)[0]
                    self.assertIn("on:\\n  workflow_dispatch:", trigger)
                    self.assertNotIn("pull_request", trigger)
                    self.assertIn("cancel-in-progress: false", workflow)
                    condition = (
                        "github.event_name == 'workflow_dispatch' && "
                        "github.ref == 'refs/heads/main' && "
                        "inputs.mutation_mode == true && "
                        "inputs.candidate_sha == github.sha"
                    )
                    self.assertEqual(workflow.count(condition), 2)
                    self.assertGreaterEqual(
                        workflow.count("git ls-remote --exit-code"), 3
                    )
                    self.assertIn(relative, authority.CRITICAL_PATHS)
                    self.assertNotIn("CODEOWNERS", workflow)
                    self.assertNotIn("merge_queue", workflow)

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
            '''
        ),
        encoding="utf-8",
    )


def main() -> None:
    update_validator()
    trim_generic_cases()
    write_repository_cases()
    print("[OWNER-OPERATED-EXACT-INDEX] PASS")


if __name__ == "__main__":
    main()
