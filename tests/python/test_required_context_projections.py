from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import check_required_context_projections as projections  # noqa: E402


class RequiredContextProjectionTests(unittest.TestCase):
    def _fixture(self, directory: str) -> Path:
        root = Path(directory)
        target = root / ".github" / "required-check-contexts-v1.json"
        target.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / projections.REGISTRY_REL, target)
        return root

    def test_repository_projections_match_canonical_contexts(self) -> None:
        self.assertEqual(projections.validate(ROOT), [])

    def test_pull_request_projection_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            path = root / projections.REGISTRY_REL
            document = json.loads(path.read_text(encoding="utf-8"))
            document["required_pull_request_contexts"] = ["different"]
            path.write_text(json.dumps(document), encoding="utf-8")
            errors = projections.validate(root)
            self.assertIn(
                f"{projections.REGISTRY_REL}: required_pull_request_contexts "
                "must exactly equal required_branch_contexts",
                errors,
            )

    def test_merge_group_projection_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._fixture(directory)
            path = root / projections.REGISTRY_REL
            document = json.loads(path.read_text(encoding="utf-8"))
            document["required_merge_group_contexts"].pop()
            path.write_text(json.dumps(document), encoding="utf-8")
            errors = projections.validate(root)
            self.assertIn(
                f"{projections.REGISTRY_REL}: required_merge_group_contexts "
                "must exactly equal required_branch_contexts",
                errors,
            )


if __name__ == "__main__":
    unittest.main()
