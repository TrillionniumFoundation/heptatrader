from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_bootstrap_postflight as postflight  # noqa: E402
import verify_exact_git_index as exact  # noqa: E402


class HostedAuditPostflightTests(unittest.TestCase):
    def fixture(self, directory: str) -> Path:
        root = Path(directory)
        for relative in postflight.WORKFLOWS:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        return root

    def mutate_once(
        self, root: Path, relative: Path, old: str, new: str
    ) -> list[str]:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count(old), 1, old)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return postflight.validate(root)

    def test_repository_hosted_audits_have_immutable_postflight(self) -> None:
        self.assertEqual(postflight.validate(ROOT), [])

    def test_final_postflight_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                postflight.WORKFLOWS[0],
                f"- name: {postflight.FINAL_NAME}",
                "- name: Removed immutable postflight",
            )
            self.assertTrue(errors)

    def test_final_postflight_must_run_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                postflight.WORKFLOWS[1],
                f"- name: {postflight.FINAL_NAME}\n        if: always()",
                f"- name: {postflight.FINAL_NAME}\n        if: success()",
            )
            self.assertTrue(any("always()" in error for error in errors), errors)

    def test_checkout_must_remain_pinned_and_credential_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                postflight.WORKFLOWS[0],
                postflight.CHECKOUT_ACTION,
                "uses: actions/checkout@v4",
            )
            self.assertTrue(any("pinned checkout" in error for error in errors), errors)

        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                postflight.WORKFLOWS[1],
                "persist-credentials: false",
                "persist-credentials: true",
            )
            self.assertTrue(any("disable credentials" in error for error in errors), errors)

    def test_exact_event_subject_binding_is_mandatory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                postflight.WORKFLOWS[0],
                f"ref: {postflight.EXACT_SHA_EXPR}",
                "ref: ${{ github.sha }}",
            )
            self.assertTrue(errors)

    def test_validation_must_precede_final_postflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            relative = postflight.WORKFLOWS[1]
            errors = self.mutate_once(
                root,
                relative,
                postflight.VALIDATION_NAMES[relative],
                "Removed hosted source validation",
            )
            self.assertTrue(any("validation" in error for error in errors), errors)

    def test_final_postflight_must_be_last_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / postflight.WORKFLOWS[0]
            path.write_text(
                path.read_text(encoding="utf-8")
                + "\n      - name: Late untrusted step\n        run: true\n",
                encoding="utf-8",
            )
            errors = postflight.validate(root)
            self.assertTrue(any("last explicit job step" in error for error in errors), errors)

    def test_cleanup_cannot_erase_mutation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            errors = self.mutate_once(
                root,
                postflight.WORKFLOWS[1],
                "set -euo pipefail",
                "set -euo pipefail\n          git clean -fdx",
            )
            self.assertTrue(any("cleanup" in error for error in errors), errors)

    def test_symlinked_hosted_audit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / postflight.WORKFLOWS[0]
            backup = path.with_suffix(".real")
            path.rename(backup)
            path.symlink_to(backup.name)
            errors = postflight.validate(root)
            self.assertTrue(any("regular single-link" in error for error in errors), errors)

    def git(self, root: Path, *args: str) -> None:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def exact_fixture(self, directory: str) -> Path:
        root = Path(directory)
        (root / "scripts").mkdir(parents=True)
        (root / "scripts/check_bootstrap_postflight.py").write_text(
            "print('reviewed')\n", encoding="utf-8"
        )
        (root / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
        self.git(root, "init", "-q")
        self.git(root, "config", "user.email", "tests@example.invalid")
        self.git(root, "config", "user.name", "tests")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-qm", "fixture")
        return root

    def test_exact_index_rejects_silent_tracked_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.exact_fixture(directory)
            (root / "scripts/check_bootstrap_postflight.py").write_text(
                "print('mutated')\n", encoding="utf-8"
            )
            errors = exact.validate(
                root, critical_paths=("scripts/check_bootstrap_postflight.py",)
            )
            self.assertTrue(
                any("bytes differ from indexed blob" in error for error in errors),
                errors,
            )

    def test_exact_index_rejects_ignored_and_untracked_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.exact_fixture(directory)
            (root / "silent.ignored").write_text("ignored\n", encoding="utf-8")
            (root / "visible.untracked").write_text("untracked\n", encoding="utf-8")
            errors = exact.validate(
                root, critical_paths=("scripts/check_bootstrap_postflight.py",)
            )
            self.assertTrue(any("silent.ignored" in error for error in errors), errors)
            self.assertTrue(any("visible.untracked" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
