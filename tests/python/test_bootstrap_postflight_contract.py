from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_bootstrap_postflight_contract as contract  # noqa: E402


class BootstrapFinalPostflightContractTests(unittest.TestCase):
    def fixture(self, directory: str) -> Path:
        root = Path(directory)
        for relative in contract.WORKFLOWS:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        return root

    def mutate_once(self, root: Path, relative: Path, old: str, new: str) -> list[str]:
        path = root / relative
        text = path.read_text(encoding='utf-8')
        self.assertGreaterEqual(text.count(old), 1, old)
        path.write_text(text.replace(old, new, 1), encoding='utf-8')
        return contract.validate(root)

    def test_repository_contract_passes(self) -> None:
        self.assertEqual(contract.validate(ROOT), [])

    def test_job_environment_does_not_reference_runner_context(self) -> None:
        for relative in contract.WORKFLOWS:
            text = (ROOT / relative).read_text(encoding='utf-8')
            block = contract._job_block(text, 'bootstrap-audit', [], relative.as_posix())
            job_header = block.split('    steps:\n', 1)[0]
            self.assertNotIn('${{ runner.', job_header)

    def test_validation_redirects_explicit_bytecode_output(self) -> None:
        for relative in contract.WORKFLOWS:
            text = (ROOT / relative).read_text(encoding='utf-8')
            block = contract._job_block(text, 'bootstrap-audit', [], relative.as_posix())
            prefix = 'github-governance' if relative == contract.WORKFLOWS[0] else 'ib-paper'
            token = 'PYTHONPYCACHEPREFIX: ${{ runner.temp }}/' + prefix + '-bootstrap-pycache'
            self.assertEqual(block.count(token), 1)

    def test_final_postflight_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root, contract.WORKFLOWS[0],
                '- name: ' + contract.FINAL_NAME,
                '- name: Removed immutable postflight',
            )
            self.assertTrue(errors)

    def test_independent_checkout_must_run_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            old = f'- name: {contract.CHECKOUT_NAME}\n        if: always()'
            new = f'- name: {contract.CHECKOUT_NAME}\n        if: success()'
            errors = self.mutate_once(root, contract.WORKFLOWS[0], old, new)
            self.assertTrue(any('always()' in error for error in errors), errors)

    def test_rebind_must_run_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            old = f'- name: {contract.REBIND_NAME}\n        if: always()'
            new = f'- name: {contract.REBIND_NAME}\n        if: success()'
            errors = self.mutate_once(root, contract.WORKFLOWS[1], old, new)
            self.assertTrue(any('always()' in error for error in errors), errors)

    def test_final_postflight_must_run_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            old = f'- name: {contract.FINAL_NAME}\n        if: always()'
            new = f'- name: {contract.FINAL_NAME}\n        if: success()'
            errors = self.mutate_once(root, contract.WORKFLOWS[1], old, new)
            self.assertTrue(any('always()' in error for error in errors), errors)

    def test_candidate_python_cannot_certify_final_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root, contract.WORKFLOWS[0],
                '/usr/bin/python3 "$POSTFLIGHT_VERIFIER" --root "$GITHUB_WORKSPACE/candidate"',
                'python3 candidate/scripts/verify_exact_git_index.py --root candidate',
            )
            self.assertTrue(errors)

    def test_both_ignored_artifact_checks_are_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(root, contract.WORKFLOWS[1], '--ignored=matching', '--ignored=no')
            self.assertTrue(any('both expose ignored' in error for error in errors), errors)

    def test_cleanup_cannot_erase_mutation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root, contract.WORKFLOWS[0],
                'set -euo pipefail',
                'set -euo pipefail\n          git clean -fdx',
            )
            self.assertTrue(any('cleanup' in error for error in errors), errors)

    def test_interpreter_environment_is_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root, contract.WORKFLOWS[1],
                'PATH: /usr/bin:/bin',
                'PATH: ${{ env.PATH }}',
            )
            self.assertTrue(errors)

    def test_all_four_postflight_commands_use_empty_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root, contract.WORKFLOWS[0],
                '/usr/bin/env -i',
                '/usr/bin/env',
            )
            self.assertTrue(any('two verifier and two status' in error for error in errors), errors)


if __name__ == '__main__':
    unittest.main()
