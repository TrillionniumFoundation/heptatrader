#!/usr/bin/env python3

from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

import audit_heptatrader_workspace_layout as layout  # noqa: E402


POLICY = REPOSITORY / "policies/heptatrader-workspace-layout-v1.json"
SOURCE_POLICY = (
    REPOSITORY / "policies/heptatrader-agent-os-source-v2.json")


class WorkspaceLayoutTests(unittest.TestCase):
    @staticmethod
    def copy_source_policy(root: Path) -> Path:
        target = (
            root / "policies/heptatrader-agent-os-source-v2.json")
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(SOURCE_POLICY, target)
        return target

    def test_current_workspace_respects_no_growth_contract(self) -> None:
        with mock.patch.object(layout, "_tree_bytes", return_value=0):
            report = layout.audit(REPOSITORY, POLICY)
        self.assertTrue(report["passed"], report["violations"])
        self.assertTrue(report["scan_complete"], report["scan_errors"])
        self.assertEqual(
            set(report["layers"]),
            {
                "agent-os-product",
                "legacy-compat",
                "ops-evidence-tooling",
                "external-evidence-store",
                "external-vendor-build-cache",
            })
        self.assertGreater(
            report["wrapper_inventory"]["generated_compatibility"], 0)
        self.assertEqual(
            set(report["volatile_storage"]["nested_cache_breakdown"]),
            set(layout.NESTED_CACHE_PATHS))

    def test_nested_cache_budget_covers_known_non_root_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-layout-") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "policies").mkdir()
            policy = root / "policies/layout.json"
            document = json.loads(POLICY.read_text(encoding="utf-8"))
            document["budgets"]["nested_cache_warning_bytes"] = 3
            policy.write_text(
                json.dumps(document, sort_keys=True) + "\n",
                encoding="utf-8")
            policy.chmod(0o600)
            self.copy_source_policy(root)
            cache = root / "HeptaTrade/x64"
            cache.mkdir(parents=True)
            (cache / "payload.bin").write_bytes(b"cache")
            report = layout.audit(root, policy)
            self.assertFalse(report["passed"])
            self.assertFalse(report["externalization_complete"])
            self.assertEqual(
                report["volatile_storage"]["nested_cache_bytes"], 5)
            self.assertIn(
                "nested caches exceed externalization budget",
                report["warnings"])

    def test_new_root_wrapper_breaks_strict_budget(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-layout-") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "policies").mkdir()
            policy = root / "policies/layout.json"
            document = json.loads(POLICY.read_text(encoding="utf-8"))
            document["budgets"]["tracked_root_legacy_wrappers"] = 0
            document["budgets"]["untracked_root_legacy_wrappers"] = 0
            policy.write_text(
                json.dumps(document, sort_keys=True) + "\n",
                encoding="utf-8")
            policy.chmod(0o600)
            self.copy_source_policy(root)
            (root / "run_new_bypass.sh").write_text(
                "#!/bin/sh\nexit 0\n", encoding="utf-8")
            report = layout.audit(root, policy)
            self.assertFalse(report["passed"])
            self.assertIn(
                "untracked root legacy wrapper budget grew",
                report["violations"])

    def test_tracked_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-layout-") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "policies").mkdir()
            policy = root / "policies/layout.json"
            shutil.copy2(POLICY, policy)
            policy.chmod(0o600)
            self.copy_source_policy(root)
            evidence = root / "runtime-logs/result.json"
            evidence.parent.mkdir()
            evidence.write_text("{}\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "runtime-logs/result.json"],
                cwd=root, check=True)
            report = layout.audit(root, policy)
            self.assertFalse(report["passed"])
            self.assertIn("volatile files entered Git", report["violations"])

    def test_no_git_source_bundle_uses_embedded_inventory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-layout-") as temporary:
            root = Path(temporary)
            (root / "policies").mkdir()
            policy = root / "policies/layout.json"
            shutil.copy2(POLICY, policy)
            policy.chmod(0o600)
            self.copy_source_policy(root)
            marker = root / ".hepta/agent-os-source-manifest.json"
            marker.parent.mkdir()
            marker.write_text(json.dumps({
                "schema": "hepta.agent-os-source-bundle.v1",
                "files": [
                    {"path": "HeptaTrade/client/native.cpp"},
                    {"path": "compat/hepta-ops-generated/status.sh"},
                ],
            }) + "\n", encoding="utf-8")
            marker.chmod(0o644)
            report = layout.audit(root, policy)
            self.assertTrue(report["passed"], report["violations"])
            self.assertEqual(
                report["layers"]["agent-os-product"]["tracked"], 1)

    def test_systemd_layer_reuses_agent_os_source_allowlist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-layout-") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "policies").mkdir()
            policy = root / "policies/layout.json"
            shutil.copy2(POLICY, policy)
            policy.chmod(0o600)
            self.copy_source_policy(root)
            systemd = root / "systemd"
            systemd.mkdir()
            product = systemd / "hepta-tool-gateway.service"
            legacy = systemd / "hepta-openclaw-shadow.service"
            product.write_text("[Service]\n", encoding="utf-8")
            legacy.write_text("[Service]\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", product.relative_to(root).as_posix(),
                 legacy.relative_to(root).as_posix()],
                cwd=root, check=True)
            report = layout.audit(root, policy)
            self.assertTrue(report["passed"], report["violations"])
            self.assertEqual(
                report["layers"]["agent-os-product"]["tracked"], 1)
            self.assertEqual(
                report["layers"]["legacy-compat"]["tracked"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
