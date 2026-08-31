#!/usr/bin/env python3
"""Validate a staged HeptaTrader install tree and emit a hash manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
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
    "share/doc/heptatrader/IB-PAPER-QUALIFICATION.md",
    "share/doc/heptatrader/RELEASE-PROCESS.md",
    "share/doc/heptatrader/RUNBOOK-STARTUP.md",
    "share/doc/heptatrader/SUPPLY-CHAIN.md",
    "share/doc/heptatrader/ci/actions.lock.json",
    "share/doc/heptatrader/ci/hosted-toolchain.lock.json",
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


def canonical_logical_root(value: str) -> str:
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        raise ValueError("logical root must be a canonical absolute POSIX path")
    parsed = PurePosixPath(value)
    if any(part in (".", "..") for part in parsed.parts):
        raise ValueError("logical root cannot contain dot components")
    canonical = parsed.as_posix()
    if canonical != value.rstrip("/") and not (canonical == "/" and value == "/"):
        raise ValueError("logical root must not contain duplicate or trailing separators")
    return canonical


def map_usr_path(root: Path, absolute: str) -> Path:
    if not absolute.startswith("/usr/"):
        raise ValueError(absolute)
    return root / absolute[len("/usr/") :]


def validate_directory(path: Path, label: str, errors: list[str]) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        errors.append(f"missing {label}: {path}")
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        errors.append(f"{label} is not a non-symlink directory: {path}")
        return False
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o022:
        errors.append(f"{label} is group/world writable: {path} mode={mode:04o}")
        return False
    return True


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
        errors.append(f"install artifact is group/world writable: {path} mode={mode:04o}")
    if executable and mode & 0o111 == 0:
        errors.append(f"install executable lacks execute bits: {path} mode={mode:04o}")


def validate_tree_entry(path: Path, errors: list[str]) -> None:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        errors.append(f"symlink is forbidden in release install tree: {path}")
    elif stat.S_ISDIR(metadata.st_mode):
        if mode & 0o022:
            errors.append(
                f"replaceable release directory: {path} mode={mode:04o}"
            )
    elif stat.S_ISREG(metadata.st_mode):
        if mode & 0o022:
            errors.append(f"writable release artifact: {path} mode={mode:04o}")
    else:
        errors.append(f"unsupported special file in release install tree: {path}")


def validate_unit_references(root: Path, errors: list[str]) -> None:
    units = root / "lib/systemd/system"
    if not units.is_dir():
        return
    for unit in sorted(units.glob("*.service"), key=lambda item: item.name):
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
    parser.add_argument("--logical-root", default="/usr")
    parser.add_argument("--ib-enabled", action="store_true")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    # abspath normalizes the lexical path without following a possibly hostile
    # final symlink. resolve() would hide precisely the root replacement this
    # verifier is required to detect.
    root = Path(os.path.abspath(os.fspath(args.root)))
    try:
        logical_root = canonical_logical_root(args.logical_root)
    except ValueError as error:
        print(f"ERROR: invalid logical root: {error}", file=sys.stderr)
        return 2
    errors: list[str] = []
    root_valid = validate_directory(root, "install root", errors)

    executables = list(CORE_EXECUTABLES)
    files = list(CORE_FILES)
    if args.ib_enabled:
        executables.extend(IB_EXECUTABLES)
        files.extend(IB_FILES)

    if root_valid:
        for item in executables:
            validate_file(root / item, True, errors)
        for item in files:
            validate_file(root / item, False, errors)

        for path in sorted(
            root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
        ):
            validate_tree_entry(path, errors)

        validate_unit_references(root, errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    file_entries = []
    directory_entries = [
        {
            "path": logical_root,
            "mode": f"{stat.S_IMODE(root.lstat().st_mode):04o}",
        }
    ]
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        logical_path = str(PurePosixPath(logical_root) / relative)
        if stat.S_ISDIR(metadata.st_mode):
            directory_entries.append(
                {
                    "path": logical_path,
                    "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
                }
            )
        elif stat.S_ISREG(metadata.st_mode):
            file_entries.append(
                {
                    "path": logical_path,
                    "sha256": sha256(path),
                    "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
                    "size": metadata.st_size,
                }
            )
    payload = {
        "schema_version": 2,
        "logical_root": logical_root,
        "directories": directory_entries,
        "files": file_entries,
    }
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        temporary = args.manifest.with_name(args.manifest.name + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, args.manifest)
    print(f"install tree PASS: {len(file_entries)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
