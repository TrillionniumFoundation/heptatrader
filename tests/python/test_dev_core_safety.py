from __future__ import annotations

from pathlib import Path
import os
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/dev_core.sh"


class DevCoreSafetyTests(unittest.TestCase):
    def _run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(overrides)
        environment["HEPTA_RUN_PYTHON_TESTS"] = "0"
        return subprocess.run(
            [str(SCRIPT)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_broad_build_directories_are_rejected_before_cmake(self) -> None:
        for path in ("/", str(ROOT), str(ROOT / "build"), "/tmp"):
            with self.subTest(path=path):
                result = self._run(HEPTA_BUILD_DIR=path)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("HEPTA_BUILD_DIR", result.stderr)
                self.assertNotIn("cmake", result.stderr.lower())

    def test_build_type_cannot_escape_the_build_subtree(self) -> None:
        result = self._run(HEPTA_BUILD_TYPE="../outside")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("HEPTA_BUILD_TYPE", result.stderr)
        self.assertNotIn("cmake", result.stderr.lower())

    def test_source_sibling_is_not_a_build_target(self) -> None:
        result = self._run(HEPTA_BUILD_DIR=str(ROOT / "tmp-build"))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("under", result.stderr)
        self.assertNotIn("cmake", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
