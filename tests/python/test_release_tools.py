from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load_repo_contracts_module():
    path = ROOT / "scripts/check_repo_contracts.py"
    specification = importlib.util.spec_from_file_location(
        "hepta_check_repo_contracts", path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class ReleaseToolTests(unittest.TestCase):
    def test_sbom_contains_every_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            install = workspace / "usr"
            (install / "bin").mkdir(parents=True)
            binary = install / "bin/heptactl"
            binary.write_bytes(b"test-binary")
            binary.chmod(0o755)
            version = workspace / "VERSION"
            version.write_text("0.1.0-beta.1\n", encoding="utf-8")
            output = workspace / "sbom.spdx.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/generate_sbom.py"),
                    "--root",
                    str(install),
                    "--version-file",
                    str(version),
                    "--git-sha",
                    "a" * 40,
                    "--output",
                    str(output),
                ],
                check=True,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["spdxVersion"], "SPDX-2.3")
            self.assertEqual(payload["packages"][0]["versionInfo"], "0.1.0-beta.1")
            self.assertEqual([item["fileName"] for item in payload["files"]], ["./bin/heptactl"])

    def test_install_verifier_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir(parents=True)
            target = root / "bin/target"
            target.write_text("x", encoding="utf-8")
            (root / "bin/link").symlink_to(target)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/verify_install_tree.py"),
                    "--root",
                    str(root),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr)

    def test_workflow_actions_require_immutable_revisions(self) -> None:
        contracts = load_repo_contracts_module()
        self.assertTrue(
            contracts.action_use_is_immutable(
                "actions/checkout@" + "a" * 40
            )
        )
        self.assertTrue(
            contracts.action_use_is_immutable(
                "docker://example.invalid/image@sha256:" + "b" * 64
            )
        )
        self.assertTrue(contracts.action_use_is_immutable("./.github/actions/local"))
        self.assertFalse(contracts.action_use_is_immutable("actions/checkout@v4"))
        self.assertFalse(
            contracts.action_use_is_immutable("docker://example.invalid/image:latest")
        )
        self.assertFalse(contracts.action_use_is_immutable("owner/action"))


if __name__ == "__main__":
    unittest.main()
