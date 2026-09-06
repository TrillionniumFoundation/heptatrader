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

LINE_CONTINUATION = chr(92) + "\n"
BUILDER_CALL = (
    'trusted/scripts/build_ib_candidate_artifact.sh candidate "$CANDIDATE_SHA" "$artifact"'
)
PAPER_RUNNER_CALL = (
    "trusted/scripts/run_ib_paper_artifact_qualification.sh "
    + LINE_CONTINUATION
    + '            "$artifact_dir" "$CANDIDATE_SHA" "$evidence"'
)
DIRECT_QUALIFIER_CALL = (
    '"$HEPTA_IB_PAPER_QUALIFIER" '
    + LINE_CONTINUATION
    + '            --account-id "$HEPTA_IB_ACCOUNT_ID" '
    + '--gateway-host "$HEPTA_IB_GATEWAY_HOST"'
)
VERIFIER_PREFIX = (
    "python3 trusted/scripts/verify_ib_paper_qualification.py " + chr(92)
)
CANDIDATE_VERIFIER_PREFIX = (
    "python3 candidate/scripts/verify_ib_paper_qualification.py " + chr(92)
)


def interface_errors(root: Path) -> list[str]:
    errors = boundary.validate(root)
    workflow = (root / boundary.IB).read_text(encoding="utf-8")
    build = boundary._job(workflow, "build-candidate", boundary.IB.as_posix(), errors)
    qualify = boundary._job(workflow, "qualify", boundary.IB.as_posix(), errors)

    required_build = (
        'ref: ${{ github.sha }}\n          path: trusted',
        'ref: ${{ inputs.candidate_sha }}\n          path: candidate',
        BUILDER_CALL,
    )
    for token in required_build:
        if build.count(token) != 1:
            errors.append(
                f"IB candidate build: exact interface token count is not one: {token}"
            )

    required_qualify = (
        'ref: ${{ github.sha }}\n          path: trusted',
        PAPER_RUNNER_CALL,
        VERIFIER_PREFIX,
        '--result "$evidence/qualification-result.json"',
        '--evidence-root "$evidence"',
        '--expected-git-sha "$CANDIDATE_SHA"',
        '--expected-binary "$artifact_dir/hepta-ib-executiond"',
        '--expected-harness "$HEPTA_IB_PAPER_QUALIFIER"',
        '--receipt "$evidence/qualification-verification.json"',
    )
    for token in required_qualify:
        if qualify.count(token) != 1:
            errors.append(
                f"IB PAPER qualification: exact interface token count is not one: {token}"
            )

    forbidden = (
        "candidate/scripts/",
        "--candidate-root",
        "--expected-head-sha",
        "--observation",
        'ref: ${{ inputs.candidate_sha }}\n          path: trusted',
        DIRECT_QUALIFIER_CALL,
    )
    for token in forbidden:
        if token in workflow:
            errors.append(f"IB workflow contains forbidden interface token: {token}")
    return errors


class IbWorkflowInterfaceTests(unittest.TestCase):
    def fixture(self, directory: str) -> Path:
        root = Path(directory)
        for relative in (*boundary.TRUSTED_FILES, boundary.GOVERNANCE, boundary.IB):
            source = ROOT / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return root

    def mutate_once(self, root: Path, old: str, new: str) -> list[str]:
        path = root / boundary.IB
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, old)
        mutated = text.replace(old, new, 1)
        self.assertNotEqual(mutated, text)
        path.write_text(mutated, encoding="utf-8")
        return interface_errors(root)

    def test_repository_workflow_uses_real_trusted_script_interfaces(self) -> None:
        self.assertEqual(interface_errors(ROOT), [])

    def test_named_argument_or_candidate_builder_regression_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                BUILDER_CALL,
                'candidate/scripts/build_ib_candidate_artifact.sh '
                '--candidate-root candidate --expected-head-sha "$CANDIDATE_SHA"',
            )
            self.assertTrue(any("IB candidate build" in item for item in errors), errors)
            self.assertTrue(any("candidate/scripts" in item for item in errors), errors)

    def test_direct_qualifier_and_secret_argv_regression_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                PAPER_RUNNER_CALL,
                DIRECT_QUALIFIER_CALL,
            )
            self.assertTrue(any("IB PAPER qualification" in item for item in errors), errors)
            self.assertTrue(
                any("--account-id" in item or "run_ib_paper" in item for item in errors),
                errors,
            )

    def test_obsolete_receipt_cli_regression_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                '--result "$evidence/qualification-result.json"',
                '--observation "$evidence/qualification-result.json"',
            )
            self.assertTrue(any("--result" in item for item in errors), errors)
            self.assertTrue(any("--observation" in item for item in errors), errors)

    def test_candidate_receipt_verifier_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                VERIFIER_PREFIX,
                CANDIDATE_VERIFIER_PREFIX,
            )
            self.assertTrue(any("IB PAPER qualification" in item for item in errors), errors)
            self.assertTrue(any("candidate/scripts" in item for item in errors), errors)

    def test_untrusted_sha_cannot_select_privileged_checkout(self) -> None:
        old = (
            "      - name: Checkout only the trusted default-branch qualification harness\n"
            "        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262\n"
            "        with:\n"
            "          repository: ${{ github.repository }}\n"
            "          ref: ${{ github.sha }}\n"
            "          path: trusted"
        )
        new = old.replace(
            "ref: ${{ github.sha }}", "ref: ${{ inputs.candidate_sha }}"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(root, old, new)
            self.assertTrue(any("input SHA" in item for item in errors), errors)
            self.assertTrue(any("path: trusted" in item for item in errors), errors)


if __name__ == "__main__":
    unittest.main()
