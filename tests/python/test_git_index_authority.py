from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import verify_exact_git_index as authority  # noqa: E402


class ExactGitIndexAuthorityTests(unittest.TestCase):
    def _run(self, root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            (completed.stdout + completed.stderr).decode("utf-8", errors="replace"),
        )
        return completed.stdout

    def _repository(self, directory: str) -> Path:
        root = Path(directory)
        self._run(root, "init", "-q", "--initial-branch=main")
        self._run(root, "config", "user.name", "Exact Index Test")
        self._run(root, "config", "user.email", "exact-index@example.invalid")
        control = root / "control.txt"
        control.write_text("trusted\n", encoding="utf-8")
        ignored = root / ".gitignore"
        ignored.write_text("ignored.bin\n", encoding="utf-8")
        self._run(root, "add", "control.txt", ".gitignore")
        self._run(root, "commit", "-q", "-m", "seed")
        return root

    def _validate(self, root: Path) -> list[str]:
        return authority.validate(root, critical_paths=("control.txt",))

    def test_clean_exact_checkout_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            self.assertEqual(self._validate(root), [])

    def test_no_git_export_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "control.txt").write_text("trusted\n", encoding="utf-8")
            self.assertTrue(any(".git directory" in error for error in self._validate(root)))

    def test_git_routing_environment_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root = self._repository(directory)
            attacker = self._repository(other)
            redirected_index = attacker / "redirected-index"
            shutil.copyfile(attacker / ".git" / "index", redirected_index)
            with mock.patch.dict(
                os.environ,
                {
                    "GIT_DIR": str(attacker / ".git"),
                    "GIT_WORK_TREE": str(attacker),
                    "GIT_INDEX_FILE": str(redirected_index),
                    "GIT_OBJECT_DIRECTORY": str(attacker / ".git" / "objects"),
                },
                clear=False,
            ):
                self.assertEqual(self._validate(root), [])

    def test_staged_index_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            (root / "control.txt").write_text("staged drift\n", encoding="utf-8")
            self._run(root, "add", "control.txt")
            errors = self._validate(root)
            self.assertTrue(any("entries differ from HEAD" in error for error in errors), errors)

    def test_unstaged_worktree_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            (root / "control.txt").write_text("unstaged drift\n", encoding="utf-8")
            errors = self._validate(root)
            self.assertTrue(any("bytes differ from indexed blob" in error for error in errors), errors)

    def test_symlink_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            target = root / "backing.txt"
            target.write_text("trusted\n", encoding="utf-8")
            control = root / "control.txt"
            control.unlink()
            control.symlink_to(target.name)
            errors = self._validate(root)
            self.assertTrue(any("replaced by another type" in error for error in errors), errors)

    def test_hardlink_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            control = root / "control.txt"
            duplicate = root / "duplicate.txt"
            os.link(control, duplicate)
            errors = self._validate(root)
            self.assertTrue(any("one hard link" in error for error in errors), errors)

    def test_ignored_untracked_file_is_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            (root / "ignored.bin").write_bytes(b"hostile")
            errors = self._validate(root)
            self.assertTrue(any("untracked work-tree content" in error for error in errors), errors)

    def test_missing_critical_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            errors = authority.validate(root, critical_paths=("missing-control.txt",))
            self.assertTrue(any("critical trust-boundary path is absent" in error for error in errors), errors)

    def test_unmerged_index_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            first = self._run(root, "hash-object", "-w", "--stdin", input_bytes=b"one\n").decode().strip()
            second = self._run(root, "hash-object", "-w", "--stdin", input_bytes=b"two\n").decode().strip()
            third = self._run(root, "hash-object", "-w", "--stdin", input_bytes=b"three\n").decode().strip()
            self._run(root, "update-index", "--force-remove", "control.txt")
            index_info = (
                f"100644 {first} 1\tcontrol.txt\n"
                f"100644 {second} 2\tcontrol.txt\n"
                f"100644 {third} 3\tcontrol.txt\n"
            ).encode("utf-8")
            self._run(root, "update-index", "--index-info", input_bytes=index_info)
            errors = self._validate(root)
            self.assertTrue(any("unmerged git index entry" in error for error in errors), errors)

    def test_committed_gitlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            head = self._run(root, "rev-parse", "HEAD").decode().strip()
            self._run(root, "update-index", "--add", "--cacheinfo", f"160000,{head},vendor")
            self._run(root, "commit", "-q", "-m", "add gitlink")
            errors = self._validate(root)
            self.assertTrue(any("gitlink/submodule" in error for error in errors), errors)

    def test_executable_mode_drift_is_rejected(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX executable modes are required")
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            (root / "control.txt").chmod(0o755)
            errors = self._validate(root)
            self.assertTrue(any("executable mode differs" in error for error in errors), errors)

    def test_cli_fails_closed_and_never_prints_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._repository(directory)
            (root / "control.txt").write_text("drift\n", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(authority, "CRITICAL_PATHS", ("control.txt",)),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = authority.main(["--root", str(root)])
            self.assertEqual(code, 1)
            self.assertNotIn("PASS", stdout.getvalue())
            self.assertIn("[EXACT-GIT-INDEX]", stderr.getvalue())

    def test_repository_workflows_cover_every_main_pull_request(self) -> None:
        for relative in (
            Path(".github/workflows/github-governance-qualification.yml"),
            Path(".github/workflows/ib-paper-qualification.yml"),
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            pull_block = text.split("  workflow_dispatch:", 1)[0]
            self.assertIn("  pull_request:\n    branches: [main]", pull_block)
            self.assertNotIn("    paths:", pull_block)
            self.assertIn(
                "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
                text,
            )

    def test_repository_workflows_execute_exact_index_boundary(self) -> None:
        governance = (
            ROOT / ".github/workflows/github-governance-qualification.yml"
        ).read_text(encoding="utf-8")
        ib = (ROOT / ".github/workflows/ib-paper-qualification.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python3 scripts/verify_exact_git_index.py --root .", governance)
        self.assertIn(
            "python3 trusted/scripts/verify_exact_git_index.py --root trusted",
            governance,
        )
        self.assertIn("test_git_index_authority.py", governance)
        self.assertIn("python3 scripts/verify_exact_git_index.py --root .", ib)
        self.assertGreaterEqual(
            ib.count(
                "python3 trusted/scripts/verify_exact_git_index.py --root trusted"
            ),
            2,
        )
        self.assertIn("test_git_index_authority.py", ib)


if __name__ == "__main__":
    unittest.main()
