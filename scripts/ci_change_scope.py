#!/usr/bin/env python3
"""Classify only known offline/documentation PRs for costly host acceptance.

Native tests and source checks are not waived. Main, merge candidates, unknown
paths, malformed/failed Git input and mixed changes retain full acceptance.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import sys

OFFLINE_PATHS = frozenset(
    f"research/{prefix}/{name}{suffix}"
    for name in ("market_data", "analytics", "replay", "strategy")
    for prefix, suffix in (("src", ".cpp"), ("include/hepta/research", ".h"))
)

# These tests link only the existing offline Data/Analytics/Replay/Strategy
# libraries. Do not exempt tests/research as a directory: it also contains real
# Gateway/Execution, process-recovery and installed-package acceptance.
OFFLINE_TEST_PATHS = frozenset({
    "tests/research/market_data_tests.cpp",
    "tests/research/analytics_tests.cpp",
    "tests/research/replay_tests.cpp",
    "tests/research/replay_model_cases.h",
})

def needs_acceptance(paths: list[str], event: str) -> bool:
    if event != "pull_request" or not paths:
        return True
    def safe(path: str) -> bool:
        return (path in OFFLINE_PATHS or path in OFFLINE_TEST_PATHS or
                path == "README.md" or
                (path.startswith("docs/") and path.endswith(".md")) or
                path.startswith(("doc/", "pic/")))
    return not all(safe(path) for path in paths)

def main() -> int:
    event = os.environ.get("EVENT_NAME", "")
    if event != "pull_request":
        print("required=true")
        return 0
    base = os.environ.get("BASE_SHA", "")
    repository = os.environ.get("BASE_REPOSITORY", "")
    server = os.environ.get("SERVER_URL", "")
    if not re.fullmatch(r"[a-f0-9]{40}", base) or not repository or not server:
        raise ValueError("complete exact PR base identity is required")
    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], check=check, capture_output=True, timeout=120)
    if git("cat-file", "-e", f"{base}^{{commit}}", check=False).returncode:
        git("fetch", "--no-tags", "--depth=1", f"{server}/{repository}.git", base)
    raw = git("diff", "--no-renames", "--name-only", "-z", base, "HEAD", "--").stdout
    if raw and not raw.endswith(b"\0"):
        raise ValueError("incomplete NUL-delimited Git diff")
    paths = [p.decode("utf-8", "strict") for p in raw.split(b"\0") if p]
    print("required=" + str(needs_acceptance(paths, event)).lower())
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"CI scope failed closed: {error}", file=sys.stderr)
        raise SystemExit(1)
