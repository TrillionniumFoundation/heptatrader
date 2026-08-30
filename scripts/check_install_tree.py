#!/usr/bin/env python3
"""Verify the minimal Simulator/Agent install tree and denied surfaces."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


REQUIRED_NAMES = {
    "heptactl",
    "hepta-sessionctl",
    "hepta-agent-mcp-launcher",
    "hepta-tool-gatewayd",
    "hepta-executiond",
    "hepta-mcp-server",
    "tool-catalog-v1.json",
    "execution-wire-v1.json",
    "research-run-v1.json",
    "run_protocol.py",
    "protocol_support.py",
}
# Basenames alone are not enough for an install contract: a file with the
# right name under an arbitrary directory could make a stale/unsafe tree look
# valid.  These are the paths emitted by RuntimeInstall.cmake, relative to
# the selected `/usr/local` or `/usr` prefix.
REQUIRED_RELATIVE_PATHS = (
    "bin/heptactl",
    "bin/hepta-sessionctl",
    "bin/hepta-agent-mcp-launcher",
    "libexec/heptatrader/hepta-tool-gatewayd",
    "libexec/heptatrader/hepta-executiond",
    "libexec/heptatrader/hepta-mcp-server",
    "share/heptatrader/research/manifest-v1.json",
    "share/heptatrader/research/run_protocol.py",
    "share/heptatrader/research/protocol_support.py",
    "share/heptatrader/schemas/tool-catalog-v1.json",
    "share/heptatrader/schemas/tool-catalog-v1.sha256",
    "share/heptatrader/schemas/execution-wire-v1.json",
    "share/heptatrader/schemas/research-run-v1.json",
)
FORBIDDEN_NAMES = {
    "hepta-ib-executiond",
    "HeptaTrader.sln",
    "Instrument.xml",
    ".env.hepta.example",
}
FORBIDDEN_TOKENS = (
    "AllowLiveTrading",
    "LIVE_CAPPED",
    "LIVE_REDUCE_ONLY",
    "HEPTA_MD_FRONT",
    "HEPTA_TD_FRONT",
    "D:\\quant",
)


def _prefix(root: Path) -> Path | None:
    # DESTDIR installs commonly use /usr/local; permit /usr for distro builds.
    candidates = (root / "usr/local", root / "usr")
    existing = [candidate for candidate in candidates if candidate.is_dir()]
    if not existing:
        return None
    # A reused staging root may contain an empty or partially stale
    # ``usr/local`` alongside a complete distro-style ``usr`` tree.  Choose
    # the candidate with the greatest number of install-contract paths; use
    # /usr/local as the deterministic tie-breaker documented above.  Looking
    # only for one matching basename would let a stale partial prefix mask a
    # valid complete install and produce a misleading failure.
    scored = [
        (
            sum((candidate / relative).is_file()
                for relative in REQUIRED_RELATIVE_PATHS),
            -index,
            candidate,
        )
        for index, candidate in enumerate(existing)
    ]
    return max(scored, key=lambda item: (item[0], item[1]))[2]


def validate(root: Path) -> list[str]:
    prefix = _prefix(root)
    if prefix is None:
        return ["installation prefix missing"]
    present = {path.name for path in prefix.rglob("*") if path.is_file()}
    errors: list[str] = []
    missing = sorted(REQUIRED_NAMES - present)
    if missing:
        errors.append(f"required files missing: {missing}")
    missing_paths = sorted(
        relative for relative in REQUIRED_RELATIVE_PATHS
        if not (prefix / relative).is_file()
    )
    if missing_paths:
        errors.append(f"required install paths missing: {missing_paths}")
    forbidden = sorted(FORBIDDEN_NAMES & present)
    if forbidden:
        errors.append(f"forbidden files installed: {forbidden}")
    for path in prefix.rglob("*"):
        if not path.is_file() or "examples" not in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            errors.append(f"{path}: invalid UTF-8 or unreadable example: {error}")
            continue
        for token in FORBIDDEN_TOKENS:
            if token in text:
                errors.append(f"{path}: forbidden token {token}")
    # CMake-configured systemd units must not leak an unresolved variable into
    # the installed deployment.  Lower-case systemd specifiers such as
    # ``@system-service`` are runtime syntax and intentionally remain valid;
    # only our uppercase ``@VAR@`` placeholders are rejected.
    for path in prefix.rglob("*.service"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            errors.append(f"{path}: invalid UTF-8 or unreadable service: {error}")
            continue
        if re.search(r"@[A-Z][A-Z0-9_]*@", text):
            errors.append(f"{path}: unresolved CMake service placeholder")
    for path in prefix.rglob("legacy"):
        if path.exists():
            errors.append(f"legacy surface installed: {path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    arguments = parser.parse_args()
    errors = validate(arguments.root.resolve())
    for error in errors:
        print(f"[INSTALL-TREE] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[INSTALL-TREE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
