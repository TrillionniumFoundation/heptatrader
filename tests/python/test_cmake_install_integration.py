from __future__ import annotations

import json
import os
from pathlib import Path
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
        if (
            not raw
            or raw.startswith(("#", "//"))
            or "=" not in raw
            or ":" not in raw.split("=", 1)[0]
        ):
            continue
        key_and_type, value = raw.split("=", 1)
        key, _ = key_and_type.split(":", 1)
        values[key] = value
    return values


class CMakeInstallIntegrationTests(unittest.TestCase):
    def test_readme_is_optional_but_docs_remain_installed(self) -> None:
        install_module = (ROOT / "cmake/HeptaInstall.cmake").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'if(EXISTS "${PROJECT_SOURCE_DIR}/README.md")',
            install_module,
        )
        self.assertIn(
            'install(DIRECTORY "${PROJECT_SOURCE_DIR}/docs/"',
            install_module,
        )

    def test_preflight_core_uses_private_install_namespace(self) -> None:
        install_module = (ROOT / "cmake/HeptaInstall.cmake").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}/heptatrader"\n'
            '    RENAME hepta-preflight-core.py',
            install_module,
        )
        self.assertNotIn(
            'DESTINATION "${CMAKE_INSTALL_BINDIR}"\n'
            '    RENAME hepta-preflight-core.py',
            install_module,
        )

    def test_tested_release_registers_installed_simulator_smoke(self) -> None:
        top = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        install_module = (ROOT / "cmake/HeptaInstall.cmake").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            top.index("if(BUILD_TESTING)"),
            top.index("include(cmake/HeptaInstall.cmake)"),
        )
        self.assertIn("if(TARGET hepta_agent_simulator_e2e_tests)", install_module)
        self.assertIn(
            'RUNTIME DESTINATION "${CMAKE_INSTALL_LIBEXECDIR}/heptatrader"',
            install_module,
        )

    @unittest.skipUnless(os.environ.get(BUILD_ENV), f"{BUILD_ENV} is not set")
    def test_registered_core_install_has_closed_inventory(self) -> None:
        build = Path(os.environ[BUILD_ENV]).resolve()
        self.assertTrue(build.is_dir(), build)
        cache_path = build / "CMakeCache.txt"
        self.assertTrue(cache_path.is_file(), cache_path)
        cache = cmake_cache(cache_path)
        expected = {
            "CMAKE_BUILD_TYPE": "Release",
            "BUILD_TESTING": "ON",
            "BUILD_IB_PROBE": "OFF",
            "HEPTA_ENABLE_IBAPI": "OFF",
            "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE": "OFF",
            "HEPTA_BUILD_LEGACY_MONOLITH": "OFF",
            "HEPTA_BUILD_LEGACY_SIMULATOR": "OFF",
            "HEPTA_RELEASE_LABEL": "0.1.0-beta.1",
        }
        for key, value in expected.items():
            self.assertEqual(
                cache.get(key), value, f"unexpected CMake profile field {key}"
            )

        policy = json.loads(
            (ROOT / "docs/preflight-policy-v1.json").read_text(encoding="utf-8")
        )
        required = policy["profiles"]["core"]["required_package_paths"]
        self.assertEqual(
            required, sorted(required), "required install inventory must be sorted"
        )
        self.assertEqual(
            len(required), len(set(required)), "required install inventory must be unique"
        )

        with tempfile.TemporaryDirectory(
            prefix="heptatrader-install-integration-"
        ) as directory:
            stage = Path(directory) / "stage"
            environment = dict(os.environ)
            environment["DESTDIR"] = str(stage)
            result = subprocess.run(
                ["cmake", "--install", str(build), "--prefix", "/usr"],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=300,
                check=False,
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
                    self.fail(
                        f"missing required installed path {relative}: {error}\n"
                        f"{result.stdout}"
                    )
                self.assertFalse(target.is_symlink(), relative)
                self.assertTrue(stat.S_ISREG(metadata.st_mode), relative)
                self.assertEqual(metadata.st_nlink, 1, relative)

            public_entrypoint = install_root / "bin/hepta-preflight"
            private_core = (
                install_root
                / "libexec/heptatrader/hepta-preflight-core.py"
            )
            self.assertTrue(public_entrypoint.is_file())
            self.assertFalse(
                (install_root / "bin/hepta-preflight-core.py").exists(),
                "private preflight implementation must not be installed in bin",
            )
            private_metadata = private_core.lstat()
            self.assertTrue(stat.S_ISREG(private_metadata.st_mode))
            self.assertEqual(private_metadata.st_nlink, 1)
            self.assertEqual(stat.S_IMODE(private_metadata.st_mode), 0o644)
            direct = subprocess.run(
                [sys.executable, str(private_core), "--help"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            self.assertEqual(direct.returncode, 2, direct)
            self.assertEqual(direct.stdout, "")
            self.assertIn("private implementation module", direct.stderr)
            self.assertIn("use hepta-preflight", direct.stderr)

            smoke = (
                install_root
                / "libexec/heptatrader/hepta_agent_simulator_e2e_tests"
            )
            smoke_metadata = smoke.lstat()
            self.assertTrue(stat.S_ISREG(smoke_metadata.st_mode), smoke)
            self.assertEqual(smoke_metadata.st_nlink, 1, smoke)
            self.assertTrue(smoke_metadata.st_mode & 0o111, smoke)

            self.assertFalse(
                (install_root / "bin/hepta-ib-executiond").exists(),
                "SDK-free core install must not contain a Broker-enabled daemon",
            )
            build_info = json.loads(
                (
                    install_root
                    / "share/heptatrader/heptatrader-build-info.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                build_info["schema"], "heptatrader.installed-build.v1"
            )
            self.assertEqual(build_info["project_version"], "0.1.0")
            self.assertEqual(build_info["release_label"], "0.1.0-beta.1")
            self.assertIs(build_info["ib_api_compiled"], False)
            self.assertIs(build_info["paper_authorized"], False)
            self.assertIs(build_info["live_authorized"], False)


if __name__ == "__main__":
    unittest.main()
