#!/usr/bin/env python3
"""Validate a staged HeptaTrader install tree and emit a hash manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys

CORE_EXECUTABLES = (
    "bin/heptactl",
    "bin/hepta-sessionctl",
    "libexec/hepta-tool-gatewayd",
    "libexec/hepta-executiond",
    "libexec/hepta-mcp-server",
    "libexec/hepta-agent-mcp-launcher",
    "libexec/hepta-observability",
)
CORE_FILES = (
    "libexec/hepta-agent-trust-domain.py",
    "lib/systemd/system/hepta-tool-gateway.service",
    "lib/systemd/system/hepta-tool-gateway.socket",
    "lib/systemd/system/hepta-execution-simulator.service",
    "lib/systemd/system/hepta-execution-simulator.socket",
    "lib/systemd/system/hepta-observability-simulator.service",
    "lib/systemd/system/hepta-observability-simulator.timer",
    "lib/tmpfiles.d/heptatrader-agent-os.conf",
    "share/heptatrader/hepta-agent-trust-domain-policy-v1.json",
    "share/heptatrader/hepta-service-identities-v1.json",
    "share/doc/heptatrader/README.md",
    "share/doc/heptatrader/VERSION",
    "share/doc/heptatrader/LICENSE",
    "share/doc/heptatrader/CAPABILITY-MATRIX.md",
    "share/doc/heptatrader/RUNBOOK-STARTUP.md",
)
IB_EXECUTABLES = (
    "libexec/hepta-ib-executiond",
    "libexec/hepta-broker-egress-policy",
)
IB_FILES = (
    "lib/systemd/system/hepta-broker-egress-policy.service",
    "lib/systemd/system/hepta-execution-ib-paper.service",
    "lib/systemd/system/hepta-execution-ib-paper.socket",
    "lib/systemd/system/hepta-execution-events-ib-paper.socket",
    "lib/systemd/system/hepta-observability-ib-paper.service",
    "lib/systemd/system/hepta-observability-ib-paper.timer",
    "lib/tmpfiles.d/heptatrader-ib-paper.conf",
    "share/heptatrader/hepta-broker-network-policy-v1.json",
)

ABSOLUTE_RUNTIME_PATH = re.compile(
    r"(?:ExecStart|ExecStop)=.*?(/usr/(?:bin|libexec|share)/[^\s]+)"
)
DOCUMENTATION_PATH = re.compile(r"Documentation=file:(/usr/share/doc/heptatrader/[^\s]+)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def map_usr_path(root: Path, absolute: str) -> Path:
    if not absolute.startswith("/usr/"):
        raise ValueError(absolute)
    return root / absolute[len("/usr/") :]


def validate_file(path: Path, executable: bool, errors: list[str]) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        errors.append(f"missing install artifact: {path}")
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        errors.append(f"install artifact is not a regular non-symlink file: {path}")
        return
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o022:
        errors.append(f"install artifact is group/world writable: {path} mode={mode:o}")
    if executable and mode & 0o111 == 0:
        errors.append(f"install executable lacks execute bits: {path} mode={mode:o}")


def validate_unit_references(root: Path, errors: list[str]) -> None:
    units = root / "lib/systemd/system"
    if not units.is_dir():
        return
    for unit in units.glob("*.service"):
        text = unit.read_text(encoding="utf-8")
        for absolute in ABSOLUTE_RUNTIME_PATH.findall(text):
            candidate = map_usr_path(root, absolute)
            if not candidate.exists():
                if absolute.startswith("/usr/bin/"):
                    continue
                errors.append(f"{unit.name} references missing installed path: {absolute}")
        for absolute in DOCUMENTATION_PATH.findall(text):
            candidate = map_usr_path(root, absolute)
            if not candidate.is_file():
                errors.append(f"{unit.name} references missing installed documentation: {absolute}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ib-enabled", action="store_true")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()

    errors: list[str] = []
    executables = list(CORE_EXECUTABLES)
    files = list(CORE_FILES)
    if args.ib_enabled:
        executables.extend(IB_EXECUTABLES)
        files.extend(IB_FILES)

    for item in executables:
        validate_file(root / item, True, errors)
    for item in files:
        validate_file(root / item, False, errors)

    for path in root.rglob("*"):
        if path.is_symlink():
            errors.append(f"symlink is forbidden in release install tree: {path}")
        elif path.is_file() and stat.S_IMODE(path.stat().st_mode) & 0o022:
            errors.append(f"writable release artifact: {path}")

    validate_unit_references(root, errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    entries = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256(path),
                "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
                "size": path.stat().st_size,
            }
        )
    payload = {"schema_version": 1, "root": str(root), "files": entries}
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(f"install tree PASS: {len(entries)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
