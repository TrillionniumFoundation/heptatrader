from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_qualification_trust_boundary as boundary  # noqa: E402


class IbWorkflowInterfaceTests(unittest.TestCase):
    def fixture(self, directory: str) -> Path:
        root = Path(directory)
        for relative in (*boundary.TRUSTED_FILES, boundary.GOVERNANCE, boundary.IB):
            source = ROOT / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return root

    def test_repository_workflow_uses_real_trusted_script_interfaces(self) -> None:
        self.assertEqual(boundary.validate(ROOT), [])

    def test_named_argument_nested_builder_regression_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / boundary.IB
            text = path.read_text(encoding="utf-8")
            text = text.replace(
                'trusted/scripts/build_ib_candidate_artifact.sh \\\n            candidate "$EXPECTED_HEAD_SHA" "$artifact"',
                '/work/trusted/scripts/build_ib_candidate_artifact.sh \\\n            --candidate-root /work/candidate --expected-head-sha "$EXPECTED_HEAD_SHA"',
                1,
            )
            path.write_text(text, encoding="utf-8")
            errors = boundary.validate(root)
            self.assertTrue(any("candidate build" in item for item in errors), errors)
            self.assertTrue(any("--candidate-root" in item for item in errors), errors)

    def test_direct_qualifier_and_secret_argv_regression_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / boundary.IB
            text = path.read_text(encoding="utf-8")
            text = text.replace(
                'trusted/scripts/run_ib_paper_artifact_qualification.sh \\\n            "$artifact_dir" "$EXPECTED_HEAD_SHA" "$evidence"',
                '"$HEPTA_IB_QUALIFIER_COMMAND" \\\n            --account-id "$HEPTA_IB_ACCOUNT_ID" --gateway-host "$HEPTA_IB_GATEWAY_HOST"',
                1,
            )
            path.write_text(text, encoding="utf-8")
            errors = boundary.validate(root)
            self.assertTrue(any("IB PAPER qualification" in item for item in errors), errors)
            self.assertTrue(
                any("HEPTA_IB_ACCOUNT_ID" in item or "run_ib_paper_artifact_qualification" in item for item in errors),
                errors,
            )

    def test_obsolete_receipt_cli_regression_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / boundary.IB
            text = path.read_text(encoding="utf-8").replace(
                '--result "$evidence/qualification-result.json"',
                '--observation "$evidence/qualification-result.json"',
                1,
            )
            path.write_text(text, encoding="utf-8")
            errors = boundary.validate(root)
            self.assertTrue(any("--result" in item or "--observation" in item for item in errors), errors)

    def test_untrusted_sha_cannot_select_privileged_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / boundary.IB
            text = path.read_text(encoding="utf-8").replace(
                'ref: ${{ github.sha }}\n          path: trusted',
                'ref: ${{ inputs.expected_head_sha }}\n          path: candidate',
                1,
            )
            path.write_text(text, encoding="utf-8")
            self.assertTrue(
                any("input SHA controls a trusted checkout" in item for item in boundary.validate(root))
            )


if __name__ == "__main__":
    unittest.main()
