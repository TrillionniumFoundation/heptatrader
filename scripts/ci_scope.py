#!/usr/bin/env python3
"""Conservative change scope for routine CI; unknown changes run full tests.

Only markdown-only diffs can skip expensive native/torch jobs. No synthetic
success or unsupported source-path allowlists. A missing Git base always runs
the full suite; a rename is treated as both delete and add.
"""
from __future__ import annotations
import argparse
import re
import subprocess

SHA = re.compile(r"^[0-9a-f]{40}$")


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
        if len(components) < 2 or components[0] != "docs" or not path.endswith(".md"):
            return False
    return True


def diff_paths(base: str) -> list[str] | None:
    if not SHA.fullmatch(base):
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="40-hex event base/previous SHA")
    args = parser.parse_args()
    names = diff_paths(args.base)
    if names is not None and docs_only(names):
        print("DOCS_ONLY: README/docs markdown changed; preserve documentation checks.")
        return 0
    print("FULL_SOURCE_TESTS: source, binary, config, unknown or unverified diff.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
