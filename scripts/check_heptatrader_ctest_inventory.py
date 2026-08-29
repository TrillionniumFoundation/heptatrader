#!/usr/bin/env python3
"""Fail-closed validator for a configured CTest inventory.

The expected inventory is selected from the configured source tree.  A Git
checkout uses the repository profile; a packaged Agent OS source tree (which
has no ``.git`` directory but carries the signed source marker) uses the
no-Git profile.  This keeps callers from having to duplicate the profile and
inventory path at every gate while retaining an explicit override for
offline fixtures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any


INVENTORY_BY_PROFILE = {
    "repository": "tests/heptatrader-repository-ctest-inventory-v1.json",
    "agent-os-no-git": "tests/heptatrader-agent-os-ctest-inventory-v1.json",
}
SOURCE_MARKER = ".hepta/agent-os-source-manifest.json"
EXPECTED_FIELDS = {
    "schema", "version", "profile", "test_count",
    "test_names_sha256", "test_names",
}


class CTestInventoryError(RuntimeError):
    pass


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    if len(data) > 1_048_576:
        raise CTestInventoryError(f"{label} exceeds the size limit")
    try:
        document = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CTestInventoryError(
            f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise CTestInventoryError(f"{label} root must be an object")
    return document


def _protected_read(path: Path, label: str) -> bytes:
    metadata = path.lstat()
    if (
            stat.S_ISLNK(metadata.st_mode) or
            not stat.S_ISREG(metadata.st_mode) or
            metadata.st_nlink != 1 or
            metadata.st_mode & 0o022 or
            metadata.st_size < 2 or
            metadata.st_size > 1_048_576):
        raise CTestInventoryError(f"{label} metadata is unsafe")
    return path.read_bytes()


def _build_read(path: Path, label: str) -> bytes:
    """Read a generated build input without accepting links or devices."""
    metadata = path.lstat()
    if (
            stat.S_ISLNK(metadata.st_mode) or
            not stat.S_ISREG(metadata.st_mode) or
            metadata.st_nlink != 1 or
            metadata.st_size < 2 or
            metadata.st_size > 1_048_576):
        raise CTestInventoryError(f"{label} metadata is unsafe")
    return path.read_bytes()


def _configured_source_root(build_dir: Path) -> Path:
    """Return the canonical source directory recorded by CMake.

    CMake's cache is the only source-of-truth for a build directory.  Do not
    infer the source from the validator's own location: gates also validate
    extracted no-Git bundles whose scripts run from a different tree.
    """
    cache = build_dir / "CMakeCache.txt"
    data = _build_read(cache, "CMake cache")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CTestInventoryError(
            "CMake cache is not strict UTF-8") from error
    values: list[str] = []
    for line in text.splitlines():
        if line.startswith("CMAKE_HOME_DIRECTORY:") and "=" in line:
            key, value = line.split("=", 1)
            if key == "CMAKE_HOME_DIRECTORY:INTERNAL":
                values.append(value)
    if len(values) != 1 or not values[0]:
        raise CTestInventoryError(
            "CMake cache has no unique CMAKE_HOME_DIRECTORY")
    source = Path(values[0])
    if not source.is_absolute():
        raise CTestInventoryError(
            "CMAKE_HOME_DIRECTORY must be an absolute path")
    try:
        source = source.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise CTestInventoryError(
            "configured CMAKE_HOME_DIRECTORY is unavailable") from error
    if not source.is_dir():
        raise CTestInventoryError(
            "configured CMAKE_HOME_DIRECTORY is not a directory")
    return source


def _optional_entry(path: Path, label: str) -> os.stat_result | None:
    """Read an optional profile marker without following symlinks."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CTestInventoryError(f"{label} metadata is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise CTestInventoryError(f"{label} must not be a symlink")
    return metadata


def detect_source_profile(source_root: Path) -> str:
    """Determine the CTest profile from the source tree's immutable markers."""
    git_metadata = _optional_entry(source_root / ".git", "source .git")
    marker_path = source_root / SOURCE_MARKER
    marker_metadata = _optional_entry(marker_path, "Agent OS source marker")
    if git_metadata is not None and marker_metadata is not None:
        raise CTestInventoryError(
            "source profile is ambiguous: both .git and Agent OS marker exist")
    if git_metadata is not None:
        if not (
                stat.S_ISDIR(git_metadata.st_mode) or
                stat.S_ISREG(git_metadata.st_mode)):
            raise CTestInventoryError("source .git has an invalid type")
        return "repository"

    if marker_metadata is None:
        raise CTestInventoryError(
            "cannot determine source profile: neither .git nor Agent OS "
            "source marker is present")
    if not stat.S_ISREG(marker_metadata.st_mode):
        raise CTestInventoryError("Agent OS source marker is not a file")
    marker = _load_json_bytes(
        _protected_read(marker_path, "Agent OS source marker"),
        "Agent OS source marker")
    if (
            marker.get("schema") != "hepta.agent-os-source-bundle.v1" or
            marker.get("bundle_class") != "agent-os-source-only" or
            marker.get("paper_authorized") is not False or
            marker.get("live_authorized") is not False):
        raise CTestInventoryError(
            "Agent OS source marker does not identify a safe no-Git bundle")
    return "agent-os-no-git"


def expected_path_for_profile(source_root: Path, profile: str) -> Path:
    """Return the canonical inventory path for ``profile``."""
    relative = INVENTORY_BY_PROFILE.get(profile)
    if relative is None:
        raise CTestInventoryError(f"unsupported CTest inventory profile: {profile}")
    return source_root / relative


def _names_digest(names: list[str]) -> str:
    return hashlib.sha256(
        ("\n".join(names) + "\n").encode("utf-8")).hexdigest()


def expected_inventory(
        document: dict[str, Any], expected_profile: str) -> list[str]:
    if set(document) != EXPECTED_FIELDS:
        raise CTestInventoryError("expected inventory fields drifted")
    if (
            document["schema"] != "hepta.ctest-inventory.v1" or
            document["version"] != 1 or
            document["profile"] != expected_profile):
        raise CTestInventoryError(
            "expected inventory identity drifted")
    count = document["test_count"]
    names = document["test_names"]
    digest = document["test_names_sha256"]
    if (
            isinstance(count, bool) or not isinstance(count, int) or
            count < 1 or
            not isinstance(names, list) or
            len(names) != count or
            not isinstance(digest, str) or
            re.fullmatch(r"[0-9a-f]{64}", digest) is None):
        raise CTestInventoryError(
            "expected inventory values are invalid")
    if any(
            not isinstance(name, str) or
            not name or
            len(name.encode("utf-8")) > 255
            for name in names):
        raise CTestInventoryError(
            "expected inventory contains an invalid test name")
    if len(names) != len(set(names)):
        raise CTestInventoryError(
            "expected inventory contains duplicate test names")
    if _names_digest(names) != digest:
        raise CTestInventoryError(
            "expected inventory digest mismatch")
    return names


def observed_inventory(document: dict[str, Any]) -> list[str]:
    if (
            document.get("kind") != "ctestInfo" or
            document.get("version") != {"major": 1, "minor": 0} or
            not isinstance(document.get("tests"), list)):
        raise CTestInventoryError(
            "CTest inventory schema is unsupported")
    names: list[str] = []
    for test in document["tests"]:
        if not isinstance(test, dict):
            raise CTestInventoryError("CTest inventory test is invalid")
        name = test.get("name")
        if (
                not isinstance(name, str) or
                not name or
                len(name.encode("utf-8")) > 255):
            raise CTestInventoryError(
                "CTest inventory contains an invalid test name")
        names.append(name)
    if len(names) != len(set(names)):
        raise CTestInventoryError(
            "CTest inventory contains duplicate test names")
    return names


def validate_documents(
        observed: dict[str, Any],
        expected: dict[str, Any],
        expected_profile: str) -> dict[str, Any]:
    expected_names = expected_inventory(expected, expected_profile)
    observed_names = observed_inventory(observed)
    if observed_names != expected_names:
        missing = sorted(set(expected_names) - set(observed_names))
        unexpected = sorted(set(observed_names) - set(expected_names))
        raise CTestInventoryError(
            "CTest inventory drifted: "
            f"missing={missing} unexpected={unexpected} "
            f"order_match={set(observed_names) == set(expected_names)}")
    return {
        "passed": True,
        "profile": expected_profile,
        "test_count": len(observed_names),
        "test_names_sha256": _names_digest(observed_names),
    }


def validate(
        build_dir: Path,
        expected_path: Path | None = None,
        expected_profile: str | None = None) -> dict[str, Any]:
    build_dir = build_dir.resolve(strict=True)
    for relative in ("CMakeCache.txt", "CTestTestfile.cmake"):
        path = build_dir / relative
        metadata = path.lstat()
        if (
                stat.S_ISLNK(metadata.st_mode) or
                not stat.S_ISREG(metadata.st_mode)):
            raise CTestInventoryError(
                f"configured build input is unsafe: {relative}")
    source_root = _configured_source_root(build_dir)
    detected_profile = detect_source_profile(source_root)
    if expected_profile is not None and expected_profile != detected_profile:
        raise CTestInventoryError(
            "requested CTest inventory profile does not match configured "
            f"source: requested={expected_profile} detected={detected_profile}")
    profile = expected_profile or detected_profile
    if expected_path is None:
        expected_path = expected_path_for_profile(source_root, profile)
    ctest = shutil.which("ctest")
    if ctest is None:
        raise CTestInventoryError("ctest is unavailable")
    completed = subprocess.run(
        [ctest, "--test-dir", str(build_dir), "--show-only=json-v1"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        })
    if completed.returncode != 0:
        raise CTestInventoryError(
            "ctest inventory command failed: " +
            completed.stderr.decode(
                "utf-8", errors="backslashreplace")[:4096])
    observed = _load_json_bytes(
        completed.stdout, "CTest inventory")
    expected_path = expected_path.absolute()
    expected = _load_json_bytes(
        _protected_read(expected_path, "expected inventory"),
        "expected inventory")
    result = validate_documents(observed, expected, profile)
    result["source_root"] = str(source_root)
    result["expected_path"] = str(expected_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument(
        "--expected", type=Path,
        help="optional inventory override; defaults to the configured source "
        "profile's canonical inventory")
    parser.add_argument(
        "--expected-profile", choices=tuple(INVENTORY_BY_PROFILE),
        help="optional profile override; defaults to the configured source "
        "profile (must match it)")
    arguments = parser.parse_args()
    result = validate(
        arguments.build_dir,
        arguments.expected,
        arguments.expected_profile)
    print(
        "heptatrader_ctest_inventory: PASS "
        f"profile={result['profile']} "
        f"tests={result['test_count']} "
        f"sha256={result['test_names_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
