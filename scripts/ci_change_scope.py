#!/usr/bin/env python3
"""Classify exact pull-request paths for repository acceptance scopes.

Native tests and source checks are not waived. Main, merge candidates, unknown
paths, malformed/failed Git input and mixed changes retain full acceptance.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

SHA_RE = re.compile(r"[a-f0-9]{40}")
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


def documentation_only_path(path: str) -> bool:
    return (
        path == "README.md"
        or (path.startswith("docs/") and path.endswith(".md"))
        or path.startswith("doc/")
        or path.startswith("pic/")
    )


def is_documentation_only_pull_request(paths: list[str], event: str) -> bool:
    return event == "pull_request" and bool(paths) and all(
        documentation_only_path(path) for path in paths
    )


def needs_acceptance(paths: list[str], event: str) -> bool:
    if event != "pull_request" or not paths:
        return True

    def safe(path: str) -> bool:
        return (
            path in OFFLINE_PATHS
            or path in OFFLINE_TEST_PATHS
            or documentation_only_path(path)
        )

    return not all(safe(path) for path in paths)


def _git(
    root: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=check, capture_output=True, timeout=120
    )


def changed_paths(
    root: Path,
    event: str,
    base_sha: str,
    base_repository: str,
    server_url: str,
) -> list[str]:
    if event != "pull_request":
        return []
    if not SHA_RE.fullmatch(base_sha) or not base_repository or not server_url:
        raise ValueError("complete exact PR base identity is required")
    if _git(root, "cat-file", "-e", f"{base_sha}^{{commit}}", check=False).returncode:
        _git(
            root,
            "fetch",
            "--no-tags",
            "--depth=1",
            f"{server_url}/{base_repository}.git",
            base_sha,
        )
    raw = _git(
        root, "diff", "--no-renames", "--name-only", "-z", base_sha, "HEAD", "--"
    ).stdout
    if raw and not raw.endswith(b"\0"):
        raise ValueError("incomplete NUL-delimited Git diff")
    return [item.decode("utf-8", "strict") for item in raw.split(b"\0") if item]


def main() -> int:
    event = os.environ.get("EVENT_NAME", "")
    paths = changed_paths(
        Path.cwd(),
        event,
        os.environ.get("BASE_SHA", ""),
        os.environ.get("BASE_REPOSITORY", ""),
        os.environ.get("SERVER_URL", ""),
    )
    print("required=" + str(needs_acceptance(paths, event)).lower())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as error:
        print(f"CI scope failed closed: {error}", file=sys.stderr)
        raise SystemExit(1)
