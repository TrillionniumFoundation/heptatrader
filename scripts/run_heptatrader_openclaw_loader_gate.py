#!/usr/bin/env python3

"""Run an isolated, opt-in OpenClaw real-loader gate for the Agent OS plugin."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any


SCHEMA = "hepta.openclaw-plugin-loader-gate.v1"
REPOSITORY = Path(__file__).resolve(strict=True).parents[1]
PLUGIN = REPOSITORY / "plugins/heptatrader-agent-os"
PLUGIN_ID = "heptatrader-agent-os"
MCP_NAME = "heptatrader"
DEFAULT_EXPECTED_VERSION = "2026.7.1-2"
VERSION_PATTERN = re.compile(r"^OpenClaw ([0-9]{4}\.[0-9]+\.[0-9]+-[0-9]+)")


class GateError(RuntimeError):
    """Fail-closed OpenClaw loader-gate error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_non_finite(value: str) -> object:
    raise GateError(f"non-finite JSON number: {value}")


def strict_json(value: str, label: str) -> object:
    try:
        return json.loads(
            value, object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite)
    except (json.JSONDecodeError, GateError) as error:
        raise GateError(f"{label} is invalid JSON: {error}") from error


def validate_version(output: str, expected: str) -> str:
    first_line = output.splitlines()[0] if output.splitlines() else ""
    match = VERSION_PATTERN.match(first_line)
    require(match is not None, "OpenClaw version output is unrecognized")
    version = match.group(1)
    require(version == expected,
            f"OpenClaw version {version} is not reviewed version {expected}")
    return version


def validate_config(value: object, plugin: Path) -> None:
    require(isinstance(value, dict), "OpenClaw config root is not an object")
    plugins = value.get("plugins")
    require(isinstance(plugins, dict),
            "OpenClaw config misses plugins object")
    load = plugins.get("load")
    entries = plugins.get("entries")
    require(isinstance(load, dict) and
            load.get("paths") == [str(plugin)],
            "OpenClaw config does not contain exactly the isolated plugin path")
    require(entries == {PLUGIN_ID: {"enabled": True}},
            "OpenClaw install did not write enabled=true exactly once")


def validate_inspection(value: object, plugin: Path) -> dict[str, bool]:
    require(isinstance(value, dict),
            "OpenClaw runtime inspection root is not an object")
    details = value.get("plugin")
    require(isinstance(details, dict),
            "OpenClaw runtime inspection misses plugin details")
    expected_details = {
        "id": PLUGIN_ID,
        "format": "bundle",
        "bundleFormat": "codex",
        "enabled": True,
        "explicitlyEnabled": True,
        "activated": True,
        "activationSource": "explicit",
        "status": "loaded",
        "source": str(plugin),
        "rootDir": str(plugin),
    }
    for key, expected in expected_details.items():
        require(details.get(key) == expected,
                f"OpenClaw runtime plugin field {key} is not {expected!r}")
    require(details.get("bundleCapabilities") == ["mcpServers"],
            "OpenClaw plugin bundle capability closure is not MCP-only")
    require(value.get("mcpServers") == [{
        "name": MCP_NAME,
        "hasStdioTransport": True,
    }], "OpenClaw runtime did not expose exactly one stdio heptatrader MCP")
    require(value.get("diagnostics") == [],
            "OpenClaw runtime inspection reported diagnostics")
    require(value.get("bundleCapabilities") == ["mcpServers"],
            "OpenClaw inspection bundle capability closure is not MCP-only")
    return {
        "install_wrote_enabled_true": True,
        "plugin_explicitly_activated": True,
        "exactly_one_heptatrader_mcp": True,
        "mcp_has_stdio_transport": True,
        "runtime_diagnostics_empty": True,
    }


def _command(
        executable: str, environment: dict[str, str], *arguments: str,
        timeout: int = 120) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [executable, *arguments], check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, timeout=timeout)
    if completed.returncode != 0:
        raise GateError(
            "openclaw " + " ".join(arguments) + " failed: " +
            (completed.stdout + completed.stderr).strip()[:3000])
    return completed


def _read_config(path: Path) -> object:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                "isolated OpenClaw config is not a single-link regular file")
        require(0 < before.st_size <= 1024 * 1024,
                "isolated OpenClaw config size is outside the reviewed bound")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, 1024 * 1024 + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            require(total <= 1024 * 1024,
                    "isolated OpenClaw config exceeded its reviewed bound")
        after = os.fstat(descriptor)
        stable_fields = (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
            "st_size", "st_mtime_ns", "st_ctime_ns",
        )
        require(all(
            getattr(before, field) == getattr(after, field)
            for field in stable_fields),
                "isolated OpenClaw config changed while reading")
        require(total == before.st_size,
                "isolated OpenClaw config size changed while reading")
        content = b"".join(chunks)
    finally:
        os.close(descriptor)
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise GateError("isolated OpenClaw config is not UTF-8") from error
    return strict_json(text, "isolated OpenClaw config")


def run_gate(executable: str, expected_version: str) -> dict[str, Any]:
    plugin = PLUGIN.resolve(strict=True)
    version_result = _command(executable, {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "NO_COLOR": "1",
    }, "--version", timeout=30)
    version = validate_version(
        version_result.stdout + version_result.stderr, expected_version)
    with tempfile.TemporaryDirectory(
            prefix="hepta-openclaw-loader-gate-") as directory:
        root = Path(directory)
        home = root / "home"
        state = root / "state"
        home.mkdir(mode=0o700)
        state.mkdir(mode=0o700)
        config = state / "openclaw.json"
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "OPENCLAW_STATE_DIR": str(state),
            "OPENCLAW_CONFIG_PATH": str(config),
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "CI": "1",
        }
        _command(
            executable, environment, "plugins", "install", "--link",
            str(plugin))
        validate_config(_read_config(config), plugin)
        inspection = _command(
            executable, environment, "plugins", "inspect", PLUGIN_ID,
            "--runtime", "--json")
        checks = validate_inspection(
            strict_json(inspection.stdout, "OpenClaw runtime inspection"),
            plugin)
    return {
        "schema": SCHEMA,
        "passed": True,
        "openclaw_version": version,
        "scope": "isolated-real-loader",
        "checks": checks,
        "boundary": {
            "mcp_transport_probe_performed": False,
            "tool_socket_connected": False,
            "sessions_provisioned": 0,
            "broker_connections": 0,
            "paper_enabled": False,
            "live_enabled": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the opt-in isolated OpenClaw plugin loader gate")
    parser.add_argument(
        "--run", action="store_true",
        help="explicitly run OpenClaw install and runtime inspection")
    parser.add_argument(
        "--require", action="store_true",
        help="fail instead of skipping when OpenClaw is unavailable")
    parser.add_argument(
        "--expected-version", default=DEFAULT_EXPECTED_VERSION,
        help="exact reviewed OpenClaw version")
    arguments = parser.parse_args()
    if arguments.require and not arguments.run:
        parser.error("--require requires --run")
    if not arguments.run:
        print(
            "heptatrader_openclaw_loader_gate: SKIP "
            "(opt in with --run)")
        return 0
    executable = shutil.which("openclaw")
    if executable is None:
        if arguments.require:
            raise GateError("OpenClaw CLI is unavailable")
        print(
            "heptatrader_openclaw_loader_gate: SKIP "
            "(OpenClaw CLI is unavailable)")
        return 0
    result = run_gate(executable, arguments.expected_version)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GateError, OSError, subprocess.TimeoutExpired) as error:
        print(
            "heptatrader_openclaw_loader_gate: FAIL: " + str(error),
            file=sys.stderr)
        raise SystemExit(1)
