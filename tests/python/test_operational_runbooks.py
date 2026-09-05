from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS = {
    "STARTUP.md": (
        "## Preconditions",
        "## Phase 1 — OS identities, paths and sockets",
        "## Phase 4 — Readiness decision",
        "## Controlled stop/restart",
    ),
    "INCIDENT-RESPONSE.md": (
        "## Severity",
        "## Immediate containment",
        "## Triage decision tree",
        "## Recovery gates",
    ),
    "RECONCILIATION.md": (
        "## Trigger conditions",
        "## Canonical algorithm",
        "## Outcome states",
        "## Recovery and gate reopening",
    ),
    "KILL-SWITCH.md": (
        "## Canonical PAPER control object",
        "## Engage",
        "## Safe-exit while engaged",
        "## Disarm",
    ),
    "ROLLBACK.md": (
        "## Preconditions",
        "## Verify target artifact before install",
        "## State and contract compatibility",
        "## Acceptance",
    ),
}
UNIT_PATTERN = re.compile(r"\bhepta-[a-z0-9@.-]+\.(?:service|socket)\b")
TOOL_PATTERN = re.compile(r"\bheptactl\s+call\s+([a-z][a-z0-9._-]+)\b")
SECRET_LITERAL = re.compile(
    r"HEPTA_TOOL_SESSION_TOKEN\s*=\s*(?!['\"]?<controlled-injection>|"
    r"['\"]?<injected-by-controlled-session-path>)([^\s\n]+)"
)


class OperationalRunbookTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = json.loads(
            (ROOT / "schemas/tool-catalog-v1.json").read_text(encoding="utf-8")
        )
        self.tools = {item["name"] for item in catalog["tools"]}
        units: set[str] = set()
        for path in (ROOT / "systemd").iterdir():
            name = path.name
            if name.endswith(".service.in"):
                units.add(name.removesuffix(".in"))
            elif name.endswith(".service") or name.endswith(".socket"):
                units.add(name)
        self.units = units

    def test_runbooks_have_executable_structure_and_no_placeholders(self) -> None:
        for name, headings in RUNBOOKS.items():
            text = (ROOT / "docs/operations" / name).read_text(encoding="utf-8")
            self.assertGreater(len(text), 2500, name)
            self.assertIn("```bash", text, name)
            for heading in headings:
                self.assertIn(heading, text, f"{name}: {heading}")
            self.assertNotRegex(text, r"\b(?:TODO|TBD|FIXME)\b", name)
            self.assertIsNone(SECRET_LITERAL.search(text), name)

    def test_every_referenced_systemd_unit_exists_in_repository(self) -> None:
        for name in RUNBOOKS:
            text = (ROOT / "docs/operations" / name).read_text(encoding="utf-8")
            referenced = set(UNIT_PATTERN.findall(text))
            unknown = sorted(referenced - self.units)
            self.assertEqual([], unknown, f"{name}: unknown units {unknown}")

    def test_every_heptactl_call_uses_registered_tool(self) -> None:
        for name in RUNBOOKS:
            text = (ROOT / "docs/operations" / name).read_text(encoding="utf-8")
            referenced = set(TOOL_PATTERN.findall(text))
            unknown = sorted(referenced - self.tools)
            self.assertEqual([], unknown, f"{name}: unknown tools {unknown}")

    def test_runbooks_never_enable_live_or_unsupported_venues(self) -> None:
        forbidden_commands = (
            "systemctl start hepta-execution-live",
            "systemctl enable hepta-execution-live",
            "HEPTA_PROFILE=live",
            "Venue=CTP",
            "Venue=XT",
        )
        for name in RUNBOOKS:
            text = (ROOT / "docs/operations" / name).read_text(encoding="utf-8")
            for command in forbidden_commands:
                self.assertNotIn(command, text, name)


if __name__ == "__main__":
    unittest.main()
