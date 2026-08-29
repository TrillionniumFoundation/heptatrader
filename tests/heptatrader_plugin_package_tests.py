#!/usr/bin/env python3

import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE_PATH = ROOT / ".agents/plugins/marketplace.json"
PLUGIN_ROOT = ROOT / "plugins/heptatrader-agent-os"

EXPECTED_MARKETPLACE = {
    "name": "heptatrader",
    "interface": {
        "displayName": "HeptaTrader",
    },
    "plugins": [{
        "name": "heptatrader-agent-os",
        "source": {
            "source": "local",
            "path": "./plugins/heptatrader-agent-os",
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Developer Tools",
    }],
}

EXPECTED_MCP = {
    "mcpServers": {
        "heptatrader": {
            "command": "/usr/libexec/hepta-agent-mcp-launcher",
            "env": {},
        },
    },
}
EXPECTED_CODEX_TRANSPORT = {
    "type": "stdio",
    "command": "/usr/libexec/hepta-agent-mcp-launcher",
    "args": [],
    "env": EXPECTED_MCP["mcpServers"]["heptatrader"]["env"],
    "env_vars": [],
    "cwd": None,
}

EXPECTED_PLUGIN_FILES = {
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "README.md",
}
EXPECTED_PLUGIN_ENTRIES = EXPECTED_PLUGIN_FILES | {".codex-plugin"}


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_non_finite_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number: {value}")


def load_json_text(value: str, label: str) -> object:
    try:
        return json.loads(
            value,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite_json_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label}: invalid JSON: {error}") from error


def load_json(path: Path) -> object:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise AssertionError(f"package JSON is not a regular file: {path}")
    return load_json_text(
        path.read_text(encoding="utf-8", errors="strict"), str(path))


def assert_relative_package_path(test: unittest.TestCase, value: str) -> None:
    test.assertTrue(value.startswith("./"))
    parsed = PurePosixPath(value)
    test.assertFalse(parsed.is_absolute())
    test.assertNotIn("..", parsed.parts)
    test.assertNotIn("", parsed.parts)


class HeptaTraderPluginPackageTests(unittest.TestCase):
    def test_strict_json_loader_rejects_ambiguous_values(self) -> None:
        for name, value in {
                "duplicate": '{"name":"attacker","name":"heptatrader"}',
                "nan": '{"value":NaN}',
                "positive-infinity": '{"value":Infinity}',
                "negative-infinity": '{"value":-Infinity}',
                }.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "invalid JSON"):
                    load_json_text(value, name)

    def test_repo_marketplace_is_exact(self) -> None:
        marketplace = load_json(MARKETPLACE_PATH)
        self.assertEqual(marketplace, EXPECTED_MARKETPLACE)

        source = marketplace["plugins"][0]["source"]["path"]
        assert_relative_package_path(self, source)
        resolved = (ROOT / source.removeprefix("./")).resolve(strict=True)
        self.assertEqual(resolved, PLUGIN_ROOT.resolve(strict=True))

        current = ROOT
        for component in PurePosixPath(source).parts:
            current = current / component
            metadata = current.lstat()
            self.assertFalse(stat.S_ISLNK(metadata.st_mode))
        self.assertTrue(current.is_dir())

    def test_codex_manifest_schema_and_paths(self) -> None:
        plugin = load_json(
            PLUGIN_ROOT / ".codex-plugin/plugin.json")
        self.assertEqual(set(plugin), {
            "name", "version", "description", "author", "interface",
            "mcpServers",
        })
        self.assertEqual(plugin["name"], "heptatrader-agent-os")
        self.assertRegex(
            plugin["version"],
            re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\."
                       r"(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?"
                       r"(?:\+[0-9A-Za-z.-]+)?$"))
        self.assertIsInstance(plugin["description"], str)
        self.assertTrue(plugin["description"])
        self.assertEqual(plugin["author"], {"name": "HeptaTrader"})

        interface = plugin["interface"]
        self.assertEqual(set(interface), {
            "displayName", "shortDescription", "longDescription",
            "developerName", "category", "capabilities", "defaultPrompt",
        })
        self.assertEqual(interface["category"], "Developer Tools")
        prompts = interface["defaultPrompt"]
        self.assertIsInstance(prompts, list)
        self.assertGreaterEqual(len(prompts), 1)
        self.assertLessEqual(len(prompts), 3)
        for prompt in prompts:
            self.assertIsInstance(prompt, str)
            self.assertTrue(prompt)
            self.assertLessEqual(len(prompt), 128)

        self.assertEqual(plugin["mcpServers"], "./.mcp.json")
        assert_relative_package_path(self, plugin["mcpServers"])
        mcp_path = PLUGIN_ROOT / plugin["mcpServers"].removeprefix("./")
        self.assertTrue(mcp_path.is_file())
        self.assertFalse(mcp_path.is_symlink())

    def test_openclaw_bundle_mcp_closure_is_exact(self) -> None:
        self.assertEqual(load_json(PLUGIN_ROOT / ".mcp.json"), EXPECTED_MCP)
        entries = list(PLUGIN_ROOT.rglob("*"))
        actual_entries = {
            path.relative_to(PLUGIN_ROOT).as_posix() for path in entries
        }
        self.assertEqual(actual_entries, EXPECTED_PLUGIN_ENTRIES)
        actual_files: set[str] = set()
        for path in entries:
            relative = path.relative_to(PLUGIN_ROOT).as_posix()
            metadata = path.lstat()
            self.assertFalse(stat.S_ISLNK(metadata.st_mode))
            if relative == ".codex-plugin":
                self.assertTrue(stat.S_ISDIR(metadata.st_mode))
            else:
                self.assertTrue(stat.S_ISREG(metadata.st_mode))
                actual_files.add(relative)
        self.assertEqual(actual_files, EXPECTED_PLUGIN_FILES)

    def test_codex_loader_accepts_mcp_companion_when_available(self) -> None:
        codex = shutil.which("codex")
        if codex is None:
            self.skipTest("Codex CLI is not installed in this environment")
        with tempfile.TemporaryDirectory(
                prefix="hepta-codex-plugin-loader-") as temporary:
            root = Path(temporary)
            home = root / "home"
            codex_home = root / "codex-home"
            home.mkdir(mode=0o700)
            codex_home.mkdir(mode=0o700)
            environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(home),
                "CODEX_HOME": str(codex_home),
                "LANG": "C",
                "LC_ALL": "C",
            }

            def run(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [codex, *arguments],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    check=False,
                    timeout=30,
                )

            marketplace = run(
                "plugin", "marketplace", "add", str(ROOT), "--json")
            self.assertEqual(
                marketplace.returncode, 0,
                marketplace.stdout + marketplace.stderr)
            install = run(
                "plugin", "add",
                "heptatrader-agent-os@heptatrader", "--json")
            self.assertEqual(
                install.returncode, 0, install.stdout + install.stderr)
            servers = run("mcp", "list", "--json")
            self.assertEqual(
                servers.returncode, 0, servers.stdout + servers.stderr)
            loaded = load_json_text(
                servers.stdout, "Codex MCP server inventory")
            self.assertIsInstance(loaded, list)
            self.assertEqual(
                len(loaded), 1,
                "Codex MCP inventory must contain only heptatrader")
            heptatrader = loaded[0]
            self.assertIsInstance(heptatrader, dict)
            self.assertEqual(heptatrader.get("name"), "heptatrader")
            self.assertIs(heptatrader.get("enabled"), True)
            self.assertIsNone(heptatrader.get("disabled_reason"))
            self.assertEqual(
                heptatrader.get("transport"), EXPECTED_CODEX_TRANSPORT)

    def test_documented_install_flows_preserve_authority_boundary(self) -> None:
        readme = (PLUGIN_ROOT / "README.md").read_text(
            encoding="utf-8", errors="strict")
        for required in (
            "codex plugin marketplace add /usr/share/heptatrader",
            "codex plugin marketplace add /path/to/HeptaTrader-master",
            "codex plugin add heptatrader-agent-os@heptatrader",
            "codex mcp list --json",
            "openclaw plugins install "
            "/usr/share/heptatrader/plugins/heptatrader-agent-os",
            "openclaw plugins inspect heptatrader-agent-os --runtime --json",
            "enabled=true",
            "explicitlyEnabled=true",
            "activated=true",
            "run_heptatrader_openclaw_loader_gate.py --run --require",
            "UID/GID 2004",
            "WATCH-only",
            "does not grant PAPER or LIVE authority",
        ):
            self.assertIn(required, readme)


if __name__ == "__main__":
    unittest.main()
