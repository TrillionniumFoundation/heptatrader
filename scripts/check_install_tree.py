#!/usr/bin/env python3
"""Verify the minimal Simulator/Agent install tree and canonical docs only."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
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
    "share/doc/HeptaTrader/VERSION",
    "share/doc/HeptaTrader/LICENSE",
    "share/doc/HeptaTrader/NOTICE",
    "share/doc/HeptaTrader/README.md",
    "share/doc/HeptaTrader/document-registry-v2.json",
    "share/doc/HeptaTrader/governance/CONSTITUTION.md",
    "share/doc/HeptaTrader/architecture/PLANE-ARCHITECTURE.md",
    "share/doc/HeptaTrader/contracts/contract-registry-v2.json",
    "share/doc/HeptaTrader/modules/module-registry-v2.json",
    "share/doc/HeptaTrader/product/capability-registry-v2.json",
    "share/doc/HeptaTrader/verification/test-matrix-v2.json",
)
REQUIRED_NAMES = {Path(path).name for path in REQUIRED_RELATIVE_PATHS}
FORBIDDEN_NAMES = {
    "hepta-ib-executiond",
    "HeptaTrader.sln",
    "Instrument.xml",
    ".env.hepta.example",
    "AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md",
    "PLAN.md",
    "TEST-STRATEGY.md",
    "AGENT-INTENT-CONTRACT.md",
    "MODULE-OWNERSHIP.md",
    "module-ownership-v1.json",
    "EXACT-HEAD-CI.md",
    "EXACT-HEAD-FINAL.md",
    "EXACT-HEAD-RESULTS.md",
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
    candidates = (root / "usr/local", root / "usr")
    existing = [path for path in candidates if path.is_dir()]
    if not existing:
        return None
    scored = [
        (
            sum((path / relative).is_file() for relative in REQUIRED_RELATIVE_PATHS),
            -index,
            path,
        )
        for index, path in enumerate(existing)
    ]
    return max(scored, key=lambda item: (item[0], item[1]))[2]


def _version_errors(prefix: Path) -> list[str]:
    errors: list[str] = []
    try:
        expected = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
        installed = (
            prefix / "share/doc/HeptaTrader/VERSION"
        ).read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        return [f"cannot read source/installed VERSION: {exc}"]
    if installed != expected:
        errors.append(
            f"installed VERSION mismatch: expected={expected!r}, got={installed!r}"
        )

    heptactl = prefix / "bin/heptactl"
    if heptactl.is_file():
        try:
            completed = subprocess.run(
                [str(heptactl), "--version"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"installed heptactl --version failed to execute: {exc}")
        else:
            if completed.returncode != 0:
                errors.append(
                    "installed heptactl --version returned "
                    f"{completed.returncode}: {completed.stderr.strip()}"
                )
            elif completed.stdout.strip() != expected:
                errors.append(
                    "installed heptactl version mismatch: "
                    f"expected={expected!r}, got={completed.stdout.strip()!r}"
                )
    return errors


def validate(root: Path) -> list[str]:
    prefix = _prefix(root)
    if prefix is None:
        return ["installation prefix missing"]
    errors: list[str] = []
    present = {path.name for path in prefix.rglob("*") if path.is_file()}
    missing = sorted(REQUIRED_NAMES - present)
    if missing:
        errors.append(f"required files missing: {missing}")
    missing_paths = sorted(
        relative
        for relative in REQUIRED_RELATIVE_PATHS
        if not (prefix / relative).is_file()
    )
    if missing_paths:
        errors.append(f"required install paths missing: {missing_paths}")
    forbidden = sorted(FORBIDDEN_NAMES & present)
    if forbidden:
        errors.append(f"forbidden legacy files installed: {forbidden}")

    docs = prefix / "share/doc/HeptaTrader"
    if docs.is_dir():
        for name in ("legacy", "proposals"):
            if (docs / name).exists():
                errors.append(f"historical docs directory installed: {docs / name}")

    for path in prefix.rglob("*"):
        if not path.is_file():
            continue
        if "examples" in path.parts:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                errors.append(f"{path}: invalid UTF-8 or unreadable example: {exc}")
                continue
            for token in FORBIDDEN_TOKENS:
                if token in text:
                    errors.append(f"{path}: forbidden token {token}")
        if path.suffix == ".service":
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                errors.append(f"{path}: invalid UTF-8 or unreadable service: {exc}")
                continue
            if re.search(r"@[A-Z][A-Z0-9_]*@", text):
                errors.append(f"{path}: unresolved CMake service placeholder")

    for path in prefix.rglob("legacy"):
        if path.exists():
            errors.append(f"legacy surface installed: {path}")

    if not missing_paths:
        errors.extend(_version_errors(prefix))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    errors = validate(args.root.resolve())
    for error in errors:
        print(f"[INSTALL-TREE] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[INSTALL-TREE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
