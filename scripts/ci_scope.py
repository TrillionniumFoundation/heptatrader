#!/usr/bin/env python3
"""Scope routine CI to the changed components; uncertain changes run every lane.

Repository docs, research READMEs and dated observation reports are prose.
Other research contracts/configuration keep full feedback. Verified Python tools and their tests
run every core/source Python test without a native rebuild or model dependency.
Any unknown, shared, CI, missing or renamed-outside-group path restores full CI.
"""
from __future__ import annotations
import argparse
import re
import subprocess
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")

ROOT = Path(__file__).resolve().parents[1]
VERIFIED_PYTHON_GROUPS = (
    frozenset({"scripts/hepta_nav_assurance.py", "tests/python/test_nav_assurance.py",
               "tests/python/test_account_observation.py"}),
    frozenset({"scripts/hepta_pnl_report.py", "tests/python/test_pnl_cli.py",
               "tests/python/test_account_observation.py"}),
    frozenset({"scripts/hepta_maintenance_latch.py", "tests/python/test_hepta_maintenance_latch.py"}),
    # These operators inspect/control existing release identities; they do not
    # build native code or import the learning/model runtime.
    frozenset({"scripts/activate_prepared_driver.py", "tests/python/test_activate_prepared_driver.py"}),
    frozenset({"scripts/hepta_runtime_identity.py", "tests/python/test_runtime_identity.py",
               "scripts/activate_prepared_driver.py", "tests/python/test_activate_prepared_driver.py"}),
)


def docs_only(paths: list[str]) -> bool:
    if not paths:
        return False
    for path in paths:
        components = path.split("/")
        if (path.startswith("/") or "\\" in path or
                any(not p or p in (".", "..") for p in components)):
            return False
        if path == "README.md":
            continue
        if len(components) >= 2 and components[0] == "research":
            name = components[-1]
            # Reports are prose; unnamed research contracts retain full CI.
            if name == "README.md" or re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*_(?:PHYSICAL|AUDIT|CLOSEOUT|FORENSICS)_20[0-9]{6}\.md", name):
                continue
            return False
        if len(components) < 2 or components[0] != "docs" or not path.endswith(".md"):
            return False
    return True


def scope_for_paths(paths: list[str] | None, root: Path = ROOT) -> dict[str, bool]:
    full = {"run_native": True, "run_python": True, "run_learning": True}
    if not paths:
        return full
    if docs_only(paths):
        return {key: False for key in full}
    changed = [path for path in paths if not docs_only([path])]
    allowed = frozenset.union(*VERIFIED_PYTHON_GROUPS)
    if not changed or any(path not in allowed for path in changed):
        return full
    # A deleted/retyped tool or missing paired test must not qualify for exemption.
    needed = set.union(*(set(group) for group in VERIFIED_PYTHON_GROUPS
                         if group.intersection(changed)))
    try:
        if any((root / path).is_symlink() or not (root / path).is_file() for path in needed):
            return full
    except OSError:
        return full
    return {"run_native": False, "run_python": True, "run_learning": False}


def diff_paths(base: str) -> list[str] | None:
    if not SHA.fullmatch(base) or base == "0" * 40:
        return None
    try:
        data = subprocess.check_output(
            ["git", "diff", "--no-renames", "--name-only", "-z",
             "--diff-filter=ACDMRTUXB", base, "HEAD", "--"], timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if not data or not data.endswith(b"\0"):
        return None
    try:
        return [part.decode("utf-8", errors="strict") for part in data[:-1].split(b"\0")]
    except UnicodeDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="40-hex event base/previous SHA")
    parser.add_argument("--format", choices=("text", "github"), default="text")
    args = parser.parse_args(argv)
    names = diff_paths(args.base)
    scope = scope_for_paths(names)
    if args.format == "github":
        for name, enabled in scope.items():
            print(f"{name}={str(enabled).lower()}")
        return 0
    if not scope["run_python"]:
        print("DOCS_ONLY: preserve documentation checks.")
        return 0
    print("PYTHON_TOOLS: all core/source tests." if not scope["run_native"]
          else "FULL_SOURCE_TESTS: source, config, unknown or unverified diff.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
