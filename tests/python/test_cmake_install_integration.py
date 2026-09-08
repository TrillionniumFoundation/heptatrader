from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BUILD_ENV = "HEPTA_RELEASE_INTEGRATION_BUILD_DIR"


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
            self.assertEqual(cache.get(key), value, f"unexpected CMake profile field {key}")

        policy = json.loads(
            (ROOT / "docs/preflight-policy-v1.json").read_text(encoding="utf-8")
        )
        required = policy["profiles"]["core"]["required_package_paths"]
        self.assertEqual(required, sorted(required), "required install inventory must be sorted")
        self.assertEqual(len(required), len(set(required)), "required install inventory must be unique")

        with tempfile.TemporaryDirectory(prefix="heptatrader-install-integration-") as directory:
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

            for relative in required:
                target = install_root / relative
                try:
                    metadata = target.lstat()
                except OSError as error:
                    self.fail(f"missing required installed path {relative}: {error}\n{result.stdout}")
                self.assertFalse(target.is_symlink(), relative)
                self.assertTrue(stat.S_ISREG(metadata.st_mode), relative)
                self.assertEqual(metadata.st_nlink, 1, relative)

            self.assertFalse(
                (install_root / "bin/hepta-ib-executiond").exists(),
                "SDK-free core install must not contain a Broker-enabled daemon",
            )
            build_info = json.loads(
                (install_root / "share/heptatrader/heptatrader-build-info.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(build_info["schema"], "heptatrader.installed-build.v1")
            self.assertEqual(build_info["project_version"], "0.1.0")
            self.assertEqual(build_info["release_label"], "0.1.0-beta.1")
            self.assertIs(build_info["ib_api_compiled"], False)
            self.assertIs(build_info["paper_authorized"], False)
            self.assertIs(build_info["live_authorized"], False)


if __name__ == "__main__":
    unittest.main()
