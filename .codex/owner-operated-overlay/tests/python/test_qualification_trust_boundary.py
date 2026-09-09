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
            "environment: ib-paper",
            "verify_qualification_candidate.py",
        ):
            self.assertNotIn(token, workflow)
        for relative in boundary.RETIRED:
            self.assertFalse((ROOT / relative).exists(), relative)

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
        self.assertIn("heptatrader-ib-builder", build)
        self.assertNotIn("heptatrader-ib-paper", build)
        self.assertIn("heptatrader-ib-paper", qualify)
        self.assertNotIn("heptatrader-ib-builder", qualify)
        self.assertNotIn("secrets.", build)


if __name__ == "__main__":
    unittest.main()
