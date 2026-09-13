from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILD_ENV = "HEPTA_RELEASE_INTEGRATION_BUILD_DIR"
sys.path.insert(0, str(ROOT / "scripts"))
import check_systemd_units


def cmake_cache(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith(("#", "//")) or "=" not in raw or ":" not in raw.split("=", 1)[0]:
            continue
        key_and_type, value = raw.split("=", 1)
        key, _ = key_and_type.split(":", 1)
        values[key] = value
    return values


class CMakeInstallIntegrationTests(unittest.TestCase):
    def test_documentation_install_with_and_without_optional_readme(self) -> None:
        """Execute the real install rules; comments cannot satisfy this test."""
        with tempfile.TemporaryDirectory(prefix="hepta-doc-install-") as directory:
            work = Path(directory)
            source = work / "source"
            shutil.copytree(
                ROOT, source,
                ignore=shutil.ignore_patterns(".git", "build", "dist", "__pycache__", "*.pyc"),
            )
            build = work / "build"
            # No runtime compilation or privileged installation is needed for
            # the existing documentation component. Configure the real project.
            for present in (True, False):
                with self.subTest(readme_present=present):
                    if not present:
                        (source / "README.md").unlink()
                    configured = subprocess.run(
                        ["cmake", "-S", str(source), "-B", str(build),
                         "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON",
                         "-DBUILD_IB_PROBE=OFF", "-DHEPTA_ENABLE_IBAPI=OFF",
                         "-DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF",
                         "-DHEPTA_BUILD_LEGACY_MONOLITH=OFF",
                         "-DHEPTA_BUILD_LEGACY_SIMULATOR=OFF"],
                        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        timeout=120, check=False,
                    )
                    self.assertEqual(configured.returncode, 0, configured.stdout)
                    stage = work / ("present" if present else "absent")
                    installed = subprocess.run(
                        ["cmake", "--install", str(build), "--prefix", "/usr",
                         "--component", "documentation"],
                        env={**os.environ, "DESTDIR": str(stage)},
                        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        timeout=120, check=False,
                    )
                    self.assertEqual(installed.returncode, 0, installed.stdout)
                    docs = stage / "usr/share/doc/heptatrader"
                    self.assertEqual((docs / "README.md").exists(), present)
                    for relative in ("index.md", "modules/execution-service.md", "capabilities.json"):
                        self.assertEqual((docs / relative).read_bytes(), (ROOT / "docs" / relative).read_bytes())
                    self.assertFalse((stage / "usr/bin/hepta-executiond").exists())

    @unittest.skipUnless(os.environ.get(BUILD_ENV), f"{BUILD_ENV} is not set")
    def test_registered_core_install_has_closed_inventory(self) -> None:
        build = Path(os.environ[BUILD_ENV]).resolve()
        self.assertTrue(build.is_dir(), build)
        cache_path = build / "CMakeCache.txt"
        self.assertTrue(cache_path.is_file(), cache_path)
        cache = cmake_cache(cache_path)
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        expected = {
            "CMAKE_BUILD_TYPE": "Release", "BUILD_TESTING": "ON",
            "BUILD_IB_PROBE": "OFF", "HEPTA_ENABLE_IBAPI": "OFF",
            "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE": "OFF",
            "HEPTA_BUILD_LEGACY_MONOLITH": "OFF", "HEPTA_BUILD_LEGACY_SIMULATOR": "OFF",
            "HEPTA_RELEASE_LABEL": version,
        }
        for key, value in expected.items():
            self.assertEqual(cache.get(key), value, f"unexpected CMake profile field {key}")
        policy = json.loads((ROOT / "docs/preflight-policy-v1.json").read_text(encoding="utf-8"))
        required = policy["profiles"]["core"]["required_package_paths"]
        self.assertEqual(required, sorted(required), "required install inventory must be sorted")
        self.assertEqual(len(required), len(set(required)), "required install inventory must be unique")

        with tempfile.TemporaryDirectory(prefix="heptatrader-install-integration-") as directory:
            stage = Path(directory) / "stage"
            result = subprocess.run(
                ["cmake", "--install", str(build), "--prefix", "/usr"],
                env={**os.environ, "DESTDIR": str(stage)}, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            install_root = stage / "usr"
            self.assertTrue(install_root.is_dir(), result.stdout)
            self.assertEqual(check_systemd_units.validate_installed(install_root, "core"), [])
            self.assertFalse((install_root / "lib/tmpfiles.d/heptatrader-ib-paper.conf").exists())
            for relative in required:
                target = install_root / relative
                try:
                    metadata = target.lstat()
                except OSError as error:
                    self.fail(f"missing required installed path {relative}: {error}\n{result.stdout}")
                self.assertFalse(target.is_symlink(), relative)
                self.assertTrue(stat.S_ISREG(metadata.st_mode), relative)
                self.assertEqual(metadata.st_nlink, 1, relative)

            public_entrypoint = install_root / "bin/hepta-preflight"
            private_core = install_root / "libexec/heptatrader/hepta-preflight-core.py"
            self.assertTrue(public_entrypoint.is_file())
            self.assertFalse((install_root / "bin/hepta-preflight-core.py").exists())
            metadata = private_core.lstat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertEqual(metadata.st_nlink, 1)
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o644)
            direct = subprocess.run(
                [sys.executable, str(private_core), "--help"], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
            )
            self.assertEqual(direct.returncode, 2, direct)
            self.assertEqual(direct.stdout, "")
            self.assertIn("private implementation module", direct.stderr)
            self.assertIn("use hepta-preflight", direct.stderr)
            smoke = install_root / "libexec/heptatrader/hepta_agent_simulator_e2e_tests"
            metadata = smoke.lstat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode), smoke)
            self.assertEqual(metadata.st_nlink, 1, smoke)
            self.assertTrue(metadata.st_mode & 0o111, smoke)
            self.assertFalse((install_root / "bin/hepta-ib-executiond").exists())
            build_info = json.loads((install_root / "share/heptatrader/heptatrader-build-info.json").read_text(encoding="utf-8"))
            self.assertEqual(build_info["schema"], "heptatrader.installed-build.v1")
            self.assertEqual(build_info["project_version"], cache["CMAKE_PROJECT_VERSION"])
            self.assertEqual(build_info["release_label"], version)
            self.assertIs(build_info["ib_api_compiled"], False)
            self.assertIs(build_info["paper_authorized"], False)
            self.assertIs(build_info["live_authorized"], False)


if __name__ == "__main__":
    unittest.main()
