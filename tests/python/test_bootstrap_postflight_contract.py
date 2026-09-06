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

import verify_bootstrap_postflight_contract as contract  # noqa: E402


class HostedAuditFinalPostflightContractTests(unittest.TestCase):
    def fixture(self, directory: str) -> Path:
        root = Path(directory)
        for relative in contract.WORKFLOWS:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        return root

    def mutate_once(
        self, root: Path, relative: Path, old: str, new: str
    ) -> list[str]:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count(old), 1, old)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return contract.validate(root)

    def test_repository_contract_passes(self) -> None:
        self.assertEqual(contract.validate(ROOT), [])

    def test_hostile_self_test_passes(self) -> None:
        contract.self_test()

    def test_job_environment_does_not_reference_runner_context(self) -> None:
        for relative in contract.WORKFLOWS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            errors: list[str] = []
            block = contract._job_block(
                text, contract.JOB_IDS[relative], errors, relative.as_posix()
            )
            self.assertEqual(errors, [])
            job_header = block.split("    steps:\n", 1)[0]
            self.assertNotIn("${{ runner.", job_header)

    def test_exact_event_sha_is_bound_at_three_stages(self) -> None:
        for relative in contract.WORKFLOWS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            errors: list[str] = []
            block = contract._job_block(
                text, contract.JOB_IDS[relative], errors, relative.as_posix()
            )
            self.assertEqual(errors, [])
            self.assertEqual(block.count(contract.EXACT_SHA_EXPR), 3)

    def test_final_postflight_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                contract.WORKFLOWS[0],
                f"- name: {contract.FINAL_NAME}",
                "- name: Removed immutable postflight",
            )
            self.assertTrue(errors)

    def test_final_postflight_must_use_always(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                contract.WORKFLOWS[1],
                f"- name: {contract.FINAL_NAME}\n        if: always()",
                f"- name: {contract.FINAL_NAME}\n        if: failure()",
            )
            self.assertTrue(errors)

    def test_checkout_credentials_cannot_be_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                contract.WORKFLOWS[0],
                "persist-credentials: false",
                "persist-credentials: true",
            )
            self.assertTrue(errors)

    def test_untrusted_checkout_ref_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                contract.WORKFLOWS[1],
                f"ref: {contract.EXACT_SHA_EXPR}",
                "ref: ${{ github.sha }}",
            )
            self.assertTrue(errors)

    def test_self_hosted_runner_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                contract.WORKFLOWS[0],
                "runs-on: ubuntu-24.04",
                "runs-on: self-hosted",
            )
            self.assertTrue(errors)

    def test_pull_request_target_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                contract.WORKFLOWS[1],
                "  pull_request:",
                "  pull_request_target:",
            )
            self.assertTrue(errors)

    def test_ignored_artifact_check_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                contract.WORKFLOWS[0],
                "--ignored=matching",
                "--ignored=no",
            )
            self.assertTrue(any("ignored" in error for error in errors), errors)

    def test_cleanup_cannot_erase_mutation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                contract.WORKFLOWS[1],
                "set -euo pipefail",
                "set -euo pipefail\n          git reset --hard",
            )
            self.assertTrue(any("cleanup" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
