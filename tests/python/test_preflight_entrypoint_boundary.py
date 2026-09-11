from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_hepta_preflight as fixtures
import test_preflight_release_identity as identity

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_MESSAGE = "private implementation module; use hepta-preflight"


def _arguments(artifact: Path, digest: str, *, probe: bool = True) -> list[str]:
    result = [
        "--artifact",
        str(artifact),
        "--expected-sha256",
        digest,
        "--profile",
        "ib-paper",
        "--policy",
        str(fixtures.POLICY),
        "--artifact-only",
    ]
    if probe:
        result.extend(
            [
                "--probe-broker",
                "--broker-host",
                "127.0.0.1",
                "--broker-port",
                "4002",
            ]
        )
    return result


def _hostile_artifacts(work: Path) -> list[tuple[str, Path, str]]:
    source, _ = identity._build_valid_package(work)
    cases: list[tuple[str, Path, str]] = []
    roots = {
        "wrong-version": "heptatrader-0.1.0-beta.2-ib-paper",
        "wrong-profile": f"heptatrader-{fixtures.VERSION}-core",
        "missing-profile-suffix": f"heptatrader-{fixtures.VERSION}",
        "extra-suffix": f"heptatrader-{fixtures.VERSION}-ib-paper-extra",
    }
    for label, root_name in roots.items():
        artifact = work / f"direct-{label}.tar.gz"
        digest = identity._rewrite_archive(
            source,
            artifact,
            root_name=root_name,
        )
        cases.append((label, artifact, digest))
    versions = {
        "overlong": "a" * 65,
        "leading-punctuation": ".release",
        "slash": "release/beta",
        "control": "release\n",
        "non-ascii": "release-β",
    }
    for label, version in versions.items():
        artifact = work / f"direct-version-{label}.tar.gz"
        digest = identity._rewrite_archive(
            source,
            artifact,
            manifest_version=version,
        )
        cases.append((f"version-{label}", artifact, digest))
    return cases


def _assert_private_rejection(
    testcase: unittest.TestCase,
    executable: Path,
    artifact: Path,
    digest: str,
) -> None:
    result = subprocess.run(
        [sys.executable, str(executable), *_arguments(artifact, digest)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    testcase.assertEqual(result.returncode, 2, result)
    testcase.assertEqual(result.stdout, "")
    testcase.assertIn(PRIVATE_MESSAGE, result.stderr)
    testcase.assertNotIn('"result":"PASS"', result.stderr)


class PreflightEntrypointBoundaryTests(unittest.TestCase):
    def test_source_core_direct_invocation_is_fail_closed_for_hostile_matrix(
        self,
    ) -> None:
        core = ROOT / "scripts/hepta_preflight_core.py"
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for label, artifact, digest in _hostile_artifacts(work):
                with self.subTest(label=label):
                    _assert_private_rejection(
                        self,
                        core,
                        artifact,
                        digest,
                    )

    def test_staged_private_core_rejects_direct_use_and_public_wrapper_passes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            prefix = work / "prefix"
            public = prefix / "bin/hepta-preflight"
            private = (
                prefix
                / "libexec/heptatrader/hepta-preflight-core.py"
            )
            public.parent.mkdir(parents=True)
            private.parent.mkdir(parents=True)
            shutil.copy2(ROOT / "scripts/hepta_preflight.py", public)
            shutil.copy2(ROOT / "scripts/hepta_preflight_core.py", private)
            public.chmod(0o755)
            private.chmod(0o644)

            self.assertFalse((prefix / "bin/hepta-preflight-core.py").exists())
            hostile = _hostile_artifacts(work / "hostile")
            for label, artifact, digest in hostile:
                with self.subTest(label=label):
                    _assert_private_rejection(
                        self,
                        private,
                        artifact,
                        digest,
                    )

            artifact, digest = identity._build_valid_package(work / "positive")
            accepted = subprocess.run(
                [str(public), *_arguments(artifact, digest, probe=False)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )
            self.assertEqual(accepted.returncode, 0, accepted)
            self.assertEqual(accepted.stderr, "")
            receipt = json.loads(accepted.stdout)
            self.assertEqual(receipt["result"], "PASS")
            self.assertEqual(receipt["authorization_effect"], "NONE")
            self.assertIs(receipt["paper_authorized"], False)
            self.assertIs(receipt["live_authorized"], False)
            self.assertEqual(receipt["artifact"]["sha256"], digest)

    def test_wrapper_contains_no_security_semantic_overrides(self) -> None:
        wrapper = (ROOT / "scripts/hepta_preflight.py").read_text(
            encoding="utf-8"
        )
        core = (ROOT / "scripts/hepta_preflight_core.py").read_text(
            encoding="utf-8"
        )
        install = (ROOT / "cmake/HeptaInstall.cmake").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "_ORIGINAL_CHECK_MANIFEST_SHAPE",
            "_ORIGINAL_INSPECT_ARCHIVE",
            "_read_admitted_archive_root",
        ):
            self.assertNotIn(forbidden, wrapper)
        for required in (
            "RELEASE_LABEL_RE",
            "_validate_complete_archive_namespace",
            'expected_root = f"heptatrader-{manifest[\'version\']}-{profile}"',
            PRIVATE_MESSAGE,
        ):
            self.assertIn(required, core)
        self.assertIn(
            'DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}/heptatrader"',
            install,
        )
        self.assertNotIn(
            'DESTINATION "${CMAKE_INSTALL_BINDIR}"\n'
            '    RENAME hepta-preflight-core.py',
            install,
        )


if __name__ == "__main__":
    unittest.main()
