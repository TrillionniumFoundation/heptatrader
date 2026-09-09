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
sys.path.insert(0, str(ROOT / "scripts"))
import verify_exact_git_index as authority  # noqa: E402


class ExactGitIndexAuthorityTests(unittest.TestCase):
    def git(self, root: Path, *args: str, data: bytes | None = None) -> bytes:
        run = subprocess.run(
            ["git", "-C", str(root), *args], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr).decode("utf-8", "replace"))
        return run.stdout

    def repo(self, directory: str) -> Path:
        root = Path(directory)
        self.git(root, "init", "-q", "--initial-branch=main")
        self.git(root, "config", "user.name", "Exact Index Test")
        self.git(root, "config", "user.email", "exact-index@example.invalid")
        (root / "control.txt").write_text("trusted\n", encoding="utf-8")
        (root / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
        self.git(root, "add", "control.txt", ".gitignore")
        self.git(root, "commit", "-q", "-m", "seed")
        return root

    def validate(self, root: Path) -> list[str]:
        return authority.validate(root, critical_paths=("control.txt",))

    def test_clean_exact_checkout_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.validate(self.repo(directory)), [])

    def test_no_git_export_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "control.txt").write_text("trusted\n", encoding="utf-8")
            self.assertTrue(any(".git directory" in item for item in self.validate(root)))

    def test_inherited_git_routing_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root, attacker = self.repo(directory), self.repo(other)
            redirected = attacker / "index-copy"
            shutil.copyfile(attacker / ".git" / "index", redirected)
            with mock.patch.dict(os.environ, {
                "GIT_DIR": str(attacker / ".git"),
                "GIT_WORK_TREE": str(attacker),
                "GIT_INDEX_FILE": str(redirected),
                "GIT_OBJECT_DIRECTORY": str(attacker / ".git" / "objects"),
            }, clear=False):
                self.assertEqual(self.validate(root), [])

    def test_staged_and_unstaged_drift_are_rejected(self) -> None:
        for staged in (False, True):
            with self.subTest(staged=staged), tempfile.TemporaryDirectory() as directory:
                root = self.repo(directory)
                (root / "control.txt").write_text("drift\n", encoding="utf-8")
                if staged:
                    self.git(root, "add", "control.txt")
                errors = self.validate(root)
                needle = "entries differ from HEAD" if staged else "bytes differ from indexed blob"
                self.assertTrue(any(needle in item for item in errors), errors)

    def test_symlink_hardlink_and_mode_substitution_are_rejected(self) -> None:
        for kind in ("symlink", "hardlink", "mode"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = self.repo(directory)
                control = root / "control.txt"
                if kind == "symlink":
                    (root / "backing.txt").write_text("trusted\n", encoding="utf-8")
                    control.unlink()
                    control.symlink_to("backing.txt")
                    needle = "replaced by another type"
                elif kind == "hardlink":
                    os.link(control, root / "duplicate.txt")
                    needle = "one hard link"
                else:
                    if os.name == "nt":
                        self.skipTest("POSIX mode required")
                    control.chmod(0o755)
                    needle = "executable mode differs"
                self.assertTrue(any(needle in item for item in self.validate(root)))

    def test_ignored_untracked_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.repo(directory)
            (root / "ignored.bin").write_bytes(b"hostile")
            self.assertTrue(any("untracked work-tree content" in item for item in self.validate(root)))

    def test_missing_critical_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.repo(directory)
            errors = authority.validate(root, critical_paths=("missing.txt",))
            self.assertTrue(any("critical trust-boundary path is absent" in item for item in errors))

    def test_unmerged_index_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.repo(directory)
            blobs = [self.git(root, "hash-object", "-w", "--stdin", data=f"{n}\n".encode()).decode().strip() for n in range(3)]
            self.git(root, "update-index", "--force-remove", "control.txt")
            info = "".join(f"100644 {oid} {stage}\tcontrol.txt\n" for stage, oid in enumerate(blobs, 1)).encode()
            self.git(root, "update-index", "--index-info", data=info)
            self.assertTrue(any("unmerged git index entry" in item for item in self.validate(root)))

    def test_committed_gitlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.repo(directory)
            head = self.git(root, "rev-parse", "HEAD").decode().strip()
            self.git(root, "update-index", "--add", "--cacheinfo", f"160000,{head},vendor")
            self.git(root, "commit", "-q", "-m", "gitlink")
            self.assertTrue(any("gitlink/submodule" in item for item in self.validate(root)))

    def test_cli_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.repo(directory)
            (root / "control.txt").write_text("drift\n", encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(authority, "CRITICAL_PATHS", ("control.txt",)), redirect_stdout(stdout), redirect_stderr(stderr):
                code = authority.main(["--root", str(root)])
            self.assertEqual(code, 1)
            self.assertNotIn("PASS", stdout.getvalue())
            self.assertIn("[EXACT-GIT-INDEX]", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
