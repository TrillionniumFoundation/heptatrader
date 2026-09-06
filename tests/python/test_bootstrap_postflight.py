from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_bootstrap_postflight as postflight  # noqa: E402
import verify_exact_git_index as exact  # noqa: E402


class BootstrapPostflightTests(unittest.TestCase):
    def fixture(self, directory: str) -> Path:
        root = Path(directory)
        for relative in postflight.WORKFLOWS:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        return root

    def test_repository_bootstraps_have_independent_postflight(self) -> None:
        self.assertEqual(postflight.validate(ROOT), [])

    def test_candidate_verifier_cannot_replace_pristine_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / postflight.WORKFLOWS[0]
            path.write_text(path.read_text().replace(
                'python3 postflight/scripts/verify_exact_git_index.py --root candidate',
                'python3 candidate/scripts/verify_exact_git_index.py --root candidate', 1),
                encoding='utf-8')
            self.assertTrue(postflight.validate(root))

    def test_postflight_before_candidate_tests_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / postflight.WORKFLOWS[1]
            text = path.read_text()
            validate = text.index(postflight.VALIDATE[postflight.WORKFLOWS[1]])
            start = text.index(postflight.POST)
            end = text.index(postflight.SUMMARY)
            moved = text[start:end]
            path.write_text(text[:validate] + moved + text[validate:start] + text[end:])
            self.assertTrue(any('order is invalid' in e for e in postflight.validate(root)))

    def test_missing_second_checkout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(directory)
            path = root / postflight.WORKFLOWS[0]
            text = path.read_text()
            start = text.index('      - name: ' + postflight.POST)
            end = text.index('      - name: ' + postflight.REBIND)
            path.write_text(text[:start] + text[end:])
            self.assertTrue(any('exactly two pinned checkouts' in e for e in postflight.validate(root)))

    def git(self, root: Path, *args: str) -> None:
        result = subprocess.run(['git', '-C', str(root), *args], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr)

    def exact_fixture(self, directory: str) -> Path:
        root = Path(directory)
        (root / 'scripts').mkdir(parents=True)
        (root / 'scripts/check_bootstrap_postflight.py').write_text("print('reviewed')\n")
        (root / '.gitignore').write_text('*.ignored\n')
        self.git(root, 'init', '-q')
        self.git(root, 'config', 'user.email', 'tests@example.invalid')
        self.git(root, 'config', 'user.name', 'tests')
        self.git(root, 'add', '-A')
        self.git(root, 'commit', '-qm', 'fixture')
        return root

    def test_silent_tracked_rewrite_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.exact_fixture(directory)
            (root / 'scripts/check_bootstrap_postflight.py').write_text("print('mutated')\n")
            errors = exact.validate(root, critical_paths=('scripts/check_bootstrap_postflight.py',))
            self.assertTrue(any('bytes differ from indexed blob' in e for e in errors), errors)

    def test_ignored_and_untracked_content_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.exact_fixture(directory)
            (root / 'silent.ignored').write_text('ignored\n')
            (root / 'visible.untracked').write_text('untracked\n')
            errors = exact.validate(root, critical_paths=('scripts/check_bootstrap_postflight.py',))
            self.assertTrue(any('silent.ignored' in e for e in errors), errors)
            self.assertTrue(any('visible.untracked' in e for e in errors), errors)


if __name__ == '__main__':
    unittest.main()
