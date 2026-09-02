#!/usr/bin/env python3
"""Register the executable debugging guide verification and closed gap."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK_ID = "debugging-guide-executable"
GAP_ID = "G-DOC-005"


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def write(path: str, value) -> None:
    (ROOT / path).write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def update_manifest() -> None:
    path = "docs/modules/manifests/hepta-documentation-control.json"
    manifest = load(path)
    if manifest.get("version") != "1.1.0":
        raise SystemExit(f"unexpected documentation-control version: {manifest.get('version')}")
    if CHECK_ID in manifest["verification"]:
        raise SystemExit(f"duplicate manifest verification: {CHECK_ID}")
    manifest["version"] = "1.2.0"
    manifest["verification"].append(CHECK_ID)
    manifest["verification"].sort()
    write(path, manifest)


def update_test_matrix() -> None:
    path = "docs/verification/test-matrix-v2.json"
    document = load(path)
    if any(item.get("id") == CHECK_ID for item in document["checks"]):
        raise SystemExit(f"duplicate test-matrix check: {CHECK_ID}")
    document["checks"].append(
        {
            "id": CHECK_ID,
            "lane": "A-module-fast",
            "state": "implemented",
            "evidence": (
                "python3 tests/python/test_debugging_guide.py validates the "
                "normative fault-isolation sequence, executable repository "
                "commands, authority fields, evidence controls and prohibited "
                "unsafe repairs"
            ),
        }
    )
    write(path, document)


def update_gap_registry() -> None:
    path = "docs/program/gap-registry-v2.json"
    document = load(path)
    if any(item.get("id") == GAP_ID for item in document["gaps"]):
        raise SystemExit(f"duplicate gap: {GAP_ID}")
    document["gaps"].append(
        {
            "id": GAP_ID,
            "priority": "P1",
            "title": (
                "Debugging guidance lacks executable authority-first triage, "
                "deterministic reproduction and evidence-preservation controls"
            ),
            "workstream": "WS-DOC",
            "milestone": "M1",
            "state": "closed",
            "evidence": [
                "debugging-guide-executable",
                "docs-control",
                "repository-contracts",
            ],
        }
    )
    write(path, document)


def main() -> int:
    update_manifest()
    update_test_matrix()
    update_gap_registry()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
