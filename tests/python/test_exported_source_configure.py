"""Exercise the real top-level CMake configure from a Git archive without .git.

This is broker-disabled and performs no network or credential access.  It proves
that the exported-source path used by the IB candidate builder can render the
installed documentation when the already-verified source SHA is supplied.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts/build_ib_candidate_artifact.sh"


class ExportedSourceConfigureTests(unittest.TestCase):
    def test_candidate_builder_binds_documentation_to_verified_sha(self) -> None:
        text = BUILDER.read_text(encoding="utf-8")
        configure = re.search(
            r"cmake -S /src -B /build/work -G Ninja \\\n(?P<body>(?:\s+-D[^\n]+\\\n)+)",
            text,
        )
        self.assertIsNotNone(configure)
        self.assertIn('-DHEPTA_DOCUMENTATION_SOURCE_SHA="$EXPECTED_SHA"', configure.group("body"))

    def test_real_exported_source_configures_without_git_metadata(self) -> None:
        source_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        self.assertRegex(source_sha, r"^[0-9a-f]{40}$")
        with tempfile.TemporaryDirectory(prefix="hepta-exported-configure-") as folder:
            root = Path(folder)
            archive = root / "source.tar"
            source = root / "source"
            build = root / "build"
            with archive.open("wb") as stream:
                subprocess.run(
                    ["git", "archive", "--format=tar", source_sha], cwd=ROOT,
                    check=True, stdout=stream, timeout=30,
                )
            source.mkdir()
            with tarfile.open(archive, "r:") as bundle:
                bundle.extractall(source)
            self.assertFalse((source / ".git").exists())
            result = subprocess.run(
                [
                    "cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
                    "-DCMAKE_BUILD_TYPE=Release",
                    "-DBUILD_TESTING=OFF",
                    "-DHEPTA_ENABLE_IBAPI=OFF",
                    f"-DHEPTA_DOCUMENTATION_SOURCE_SHA={source_sha}",
                ],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rendered = build / "installed-documentation"
            self.assertTrue((rendered / "index.md").is_file())
            self.assertIn(source_sha, (rendered / "index.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
