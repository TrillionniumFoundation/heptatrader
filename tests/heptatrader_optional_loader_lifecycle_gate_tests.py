#!/usr/bin/env python3

"""Offline contract tests for the two explicit Agent OS loader/lifecycle gates."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import run_hepta_agent_os_systemd_lifecycle_gate as lifecycle  # noqa: E402
import run_heptatrader_openclaw_loader_gate as loader  # noqa: E402


def valid_inspection(plugin: Path) -> dict[str, object]:
    return {
        "plugin": {
            "id": "heptatrader-agent-os",
            "format": "bundle",
            "bundleFormat": "codex",
            "enabled": True,
            "explicitlyEnabled": True,
            "activated": True,
            "activationSource": "explicit",
            "status": "loaded",
            "source": str(plugin),
            "rootDir": str(plugin),
            "bundleCapabilities": ["mcpServers"],
        },
        "mcpServers": [{
            "name": "heptatrader",
            "hasStdioTransport": True,
        }],
        "bundleCapabilities": ["mcpServers"],
        "diagnostics": [],
    }


class OptionalLoaderLifecycleGateTests(unittest.TestCase):
    def test_openclaw_reviewed_version_is_exact(self) -> None:
        self.assertEqual(
            loader.validate_version(
                "OpenClaw 2026.7.1-2 (fixture)\n",
                loader.DEFAULT_EXPECTED_VERSION),
            "2026.7.1-2")
        with self.assertRaisesRegex(loader.GateError, "not reviewed"):
            loader.validate_version(
                "OpenClaw 2026.7.1-3 (fixture)\n",
                loader.DEFAULT_EXPECTED_VERSION)

    def test_openclaw_install_config_requires_enabled_true(self) -> None:
        plugin = loader.PLUGIN.resolve(strict=True)
        config = {
            "plugins": {
                "load": {"paths": [str(plugin)]},
                "entries": {
                    "heptatrader-agent-os": {"enabled": True},
                },
            },
        }
        loader.validate_config(config, plugin)
        disabled = copy.deepcopy(config)
        disabled["plugins"]["entries"]["heptatrader-agent-os"][
            "enabled"] = False
        with self.assertRaisesRegex(loader.GateError, "enabled=true"):
            loader.validate_config(disabled, plugin)

    def test_openclaw_runtime_requires_one_active_stdio_mcp(self) -> None:
        plugin = loader.PLUGIN.resolve(strict=True)
        checks = loader.validate_inspection(valid_inspection(plugin), plugin)
        self.assertTrue(all(checks.values()))
        for name, mutation in {
                "inactive": lambda value: value["plugin"].update(
                    {"activated": False}),
                "duplicate": lambda value: value["mcpServers"].append({
                    "name": "attacker", "hasStdioTransport": True}),
                "not-stdio": lambda value: value["mcpServers"][0].update(
                    {"hasStdioTransport": False}),
                "diagnostic": lambda value: value["diagnostics"].append(
                    {"level": "error"}),
                }.items():
            with self.subTest(name=name):
                invalid = valid_inspection(plugin)
                mutation(invalid)
                with self.assertRaises(loader.GateError):
                    loader.validate_inspection(invalid, plugin)

    def test_systemd_fixture_mirrors_production_lifecycle_contract(self) -> None:
        self.assertTrue(all(lifecycle.production_contract().values()))
        units, paths = lifecycle.build_fixture_units(
            "hepta-agent-os-lifecycle-0123456789abcdef",
            Path("/run/user/1234"))
        self.assertEqual(len(units), 3)
        service = units[
            "hepta-agent-os-lifecycle-0123456789abcdef.service"]
        tool = units[
            "hepta-agent-os-lifecycle-0123456789abcdef-tool.socket"]
        supervisor = units[
            "hepta-agent-os-lifecycle-0123456789abcdef-supervisor.socket"]
        for required in (
            "Requires=hepta-agent-os-lifecycle-0123456789abcdef-tool.socket "
            "hepta-agent-os-lifecycle-0123456789abcdef-supervisor.socket",
            "RuntimeDirectoryPreserve=yes",
            "Restart=on-failure",
        ):
            self.assertIn(required, service)
        self.assertIn("FileDescriptorName=hepta-tool", tool)
        self.assertIn("FileDescriptorName=hepta-supervisor", supervisor)
        self.assertIn("RemoveOnStop=yes", tool)
        self.assertIn("RemoveOnStop=yes", supervisor)
        self.assertEqual(
            paths["tool"],
            "/run/user/1234/"
            "hepta-agent-os-lifecycle-0123456789abcdef-agent/tools.sock")

    def test_runbook_distinguishes_restart_stop_and_full_shutdown(self) -> None:
        runbook = (REPOSITORY / "docs/RUNBOOK-STARTUP.md").read_text(
            encoding="utf-8", errors="strict")
        for required in (
            "systemctl restart hepta-tool-gateway.service",
            "systemctl stop hepta-tool-gateway.service",
            "hepta-tool-gateway.socket hepta-tool-session-supervisor.socket",
            "RuntimeDirectoryPreserve=yes",
            "空目录不是",
            "run_hepta_agent_os_systemd_lifecycle_gate.py --run --require",
        ):
            self.assertIn(required, runbook)

    def test_runtime_link_cleanup_refuses_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-lifecycle-cleanup-test-") as directory:
            root = Path(directory)
            unit_root = root / "systemd/user"
            unit_root.mkdir(parents=True)
            target = root / "fixture.service"
            target.write_text("[Service]\nExecStart=/bin/true\n",
                              encoding="utf-8")
            link = unit_root / target.name
            link.symlink_to(target)
            lifecycle._remove_runtime_unit_links(root, [target])
            self.assertFalse(link.exists())
            link.write_text("unrelated\n", encoding="utf-8")
            with self.assertRaisesRegex(
                    lifecycle.GateError, "non-symlink"):
                lifecycle._remove_runtime_unit_links(root, [target])

    def test_real_gates_are_noop_without_explicit_opt_in(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for script, marker in (
            ("run_hepta_agent_os_systemd_lifecycle_gate.py",
             "SKIP (opt in with --run)"),
            ("run_heptatrader_openclaw_loader_gate.py",
             "SKIP (opt in with --run)"),
        ):
            with self.subTest(script=script):
                completed = subprocess.run(
                    [sys.executable, str(REPOSITORY / "scripts" / script)],
                    check=False, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=10, env=environment)
                self.assertEqual(
                    completed.returncode, 0,
                    completed.stdout + completed.stderr)
                self.assertIn(marker, completed.stdout)


if __name__ == "__main__":
    unittest.main()
