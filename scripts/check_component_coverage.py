#!/usr/bin/env python3
"""Validate build/Git-discovered production components against module ownership."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
from typing import Any

from source_json import SourceJsonError, load_source_json

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path("docs/module-catalog.json")
BUILD_INVENTORY = Path("docs/build-targets.json")

# Only translation-unit candidates can be proven reachable from a CMake
# target.  Headers are pulled in by those units and are therefore covered by
# the compiler/build checks rather than by this source reachability check.
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".c++"}

# Documentation, tests and images have their own checks. Anything else tracked
# is a component unless explicitly designated as repository prose/metadata.
# Explicit catalog entries below override this exclusion (for policy JSON etc.).
SUPPORT_PREFIXES = ("docs/", "doc/", "tests/", "pic/")
SUPPORT_EXACT = {".gitignore", "README.md", "SECURITY-HARDENING.md",
                 "LICENSE", "LICENSE.md", "NOTICE", "NOTICE.md"}


class CoverageError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return load_source_json(path)
    except SourceJsonError as error:
        raise CoverageError(f"cannot load {path}: {error}") from error


def _tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise CoverageError(
            "component coverage requires a Git worktree: "
            + result.stderr.decode("utf-8", "replace").strip()
        )
    files: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            value = raw.decode("utf-8", "strict")
        except UnicodeError as error:
            raise CoverageError("tracked path is not valid UTF-8") from error
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.as_posix() != value
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise CoverageError(f"tracked path is not canonical: {value!r}")
        files.append(value)
    if files != sorted(files):
        files.sort()
    return files


def _is_production(path: str) -> bool:
    return path not in SUPPORT_EXACT and not any(path.startswith(prefix) for prefix in SUPPORT_PREFIXES)


def _matches(implementation: str, path: str) -> bool:
    normalized = implementation.rstrip("/")
    return path == normalized or path.startswith(normalized + "/")


def _module_ownership(
    root: Path,
    tracked: list[str],
    catalog: dict[str, Any],
) -> tuple[dict[str, str], dict[str, list[str]], set[str]]:
    modules = catalog.get("modules")
    if not isinstance(modules, list) or not modules:
        raise CoverageError("module catalog has no modules")

    entries: list[tuple[str, str]] = []
    documents: dict[str, str] = {}
    module_paths: dict[str, list[str]] = {}
    unbuilt_paths: set[str] = set()
    unbuilt_by_module: dict[str, set[str]] = {}
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            raise CoverageError(f"module[{index}] is not an object")
        module_id = module.get("id")
        document = module.get("document")
        implementations = module.get("implementation")
        if (
            not isinstance(module_id, str)
            or not module_id
            or not isinstance(document, str)
            or not document
            or not isinstance(implementations, list)
            or not implementations
            or any(not isinstance(item, str) or not item for item in implementations)
        ):
            raise CoverageError(f"module[{index}] ownership fields are invalid")
        if module_id in documents:
            raise CoverageError(f"duplicate module id: {module_id}")
        documents[module_id] = document
        module_paths[module_id] = []
        unbuilt_by_module[module_id] = set()
        unbuilt = module.get("unbuilt", [])
        if not isinstance(unbuilt, list) or any(
            not isinstance(item, str) or not item for item in unbuilt
        ):
            raise CoverageError(f"{module_id}: unbuilt must be a list of paths")
        for implementation in implementations:
            relative = PurePosixPath(implementation)
            if (
                relative.is_absolute()
                or relative.as_posix() != implementation
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise CoverageError(
                    f"{module_id}: non-canonical implementation path {implementation!r}"
                )
            candidate = root / implementation
            if not candidate.exists() or candidate.is_symlink():
                raise CoverageError(
                    f"{module_id}: implementation path is missing or a symlink: "
                    f"{implementation}"
                )
            entries.append((implementation.rstrip("/"), module_id))
        for path in unbuilt:
            relative = PurePosixPath(path)
            if (
                relative.is_absolute()
                or relative.as_posix() != path
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise CoverageError(
                    f"{module_id}: non-canonical unbuilt path {path!r}"
                )
            if path not in tracked:
                raise CoverageError(
                    f"{module_id}: unbuilt path is not Git-tracked: {path}"
                )
            if not any(_matches(implementation, path) for implementation in implementations):
                raise CoverageError(
                    f"{module_id}: unbuilt path is outside implementation boundary: {path}"
                )
            if Path(path).suffix.lower() not in SOURCE_SUFFIXES:
                raise CoverageError(
                    f"{module_id}: unbuilt path is not a C/C++ source: {path}"
                )
            unbuilt_paths.add(path)
            unbuilt_by_module[module_id].add(path)

    production = [path for path in tracked if _is_production(path) or any(
        _matches(implementation, path) for implementation, _ in entries)]
    owners: dict[str, str] = {}
    for path in production:
        matches = [
            (len(implementation), implementation, module_id)
            for implementation, module_id in entries
            if _matches(implementation, path)
        ]
        if not matches:
            raise CoverageError(f"unowned production path: {path}")
        longest = max(item[0] for item in matches)
        selected = {(item[1], item[2]) for item in matches if item[0] == longest}
        selected_modules = {item[1] for item in selected}
        if len(selected_modules) != 1:
            detail = ", ".join(
                f"{implementation}->{module_id}"
                for implementation, module_id in sorted(selected)
            )
            raise CoverageError(f"ambiguous production ownership for {path}: {detail}")
        owner = next(iter(selected_modules))
        owners[path] = owner
        module_paths[owner].append(path)

    for module_id, paths in unbuilt_by_module.items():
        wrong_owner = sorted(path for path in paths if owners.get(path) != module_id)
        if wrong_owner:
            raise CoverageError(
                f"{module_id}: unbuilt path is owned by another module: "
                + ", ".join(wrong_owner)
            )

    for module_id, paths in module_paths.items():
        if not paths:
            raise CoverageError(
                f"{module_id}: no Git-tracked production path is owned by this module"
            )
        document = root / documents[module_id]
        try:
            metadata = document.lstat()
        except OSError as error:
            raise CoverageError(f"{module_id}: missing module document: {error}") from error
        if document.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise CoverageError(f"{module_id}: module document must be a regular file")

    return owners, module_paths, unbuilt_paths


def _validate_source_reachability(
    tracked: set[str],
    owners: dict[str, str],
    unbuilt_paths: set[str],
    inventory: dict[str, Any],
) -> None:
    """Ensure every tracked production C/C++ source reaches a CMake target.

    ``docs/build-targets.json`` is produced from the CMake File API by
    ``verify_build_ownership.py``.  This check deliberately consumes that
    reviewed inventory without invoking CMake, so unit tests and source-only
    checks remain lightweight.  The dedicated build-ownership check verifies
    that the inventory itself is fresh.  A source may be omitted only when its
    owning catalog module explicitly lists it under ``unbuilt``.
    """
    built: set[str] = set()
    profiles = inventory.get("profiles", {})
    for profile in profiles.values():
        for target in profile.get("targets", []):
            for unit in target.get("translation_units", []):
                if isinstance(unit, dict) and unit.get("kind") == "implementation":
                    path = unit.get("path")
                    if isinstance(path, str) and path in tracked:
                        built.add(path)

    candidates = {
        path
        for path, _owner in owners.items()
        if Path(path).suffix.lower() in SOURCE_SUFFIXES
    }
    missing = sorted(candidates - built - unbuilt_paths)
    if missing:
        rendered = ", ".join(missing)
        raise CoverageError(
            "production C/C++ source is not reachable from the reviewed CMake "
            f"build inventory (add a target or explicitly mark it unbuilt): {rendered}"
        )


def _validate_build_ownership(
    tracked: set[str],
    owners: dict[str, str],
    catalog: dict[str, Any],
    inventory: dict[str, Any],
) -> None:
    modules = {
        item.get("id")
        for item in catalog.get("modules", [])
        if isinstance(item, dict)
    }
    profiles = inventory.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise CoverageError("build target inventory has no profiles")
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise CoverageError(f"build profile is invalid: {profile_name}")
        targets = profile.get("targets")
        if not isinstance(targets, list):
            raise CoverageError(f"build profile has no target list: {profile_name}")
        for target in targets:
            if not isinstance(target, dict):
                raise CoverageError(f"{profile_name}: target is not an object")
            target_name = target.get("name", "<unknown>")
            units = target.get("translation_units", [])
            if not isinstance(units, list):
                raise CoverageError(
                    f"{profile_name}/{target_name}: translation_units is invalid"
                )
            for unit in units:
                if not isinstance(unit, dict) or unit.get("kind") != "implementation":
                    continue
                path = unit.get("path")
                declared = unit.get("owner")
                if not isinstance(path, str):
                    raise CoverageError(
                        f"{profile_name}/{target_name}: implementation path is invalid"
                    )
                # External SDK translation units are intentionally outside the
                # repository and have no repository module owner.
                if path not in tracked:
                    if declared not in {None, ""}:
                        raise CoverageError(
                            f"{profile_name}/{target_name}: external translation unit "
                            f"claims repository owner {declared!r}: {path}"
                        )
                    continue
                inferred = owners.get(path)
                if inferred is None:
                    raise CoverageError(
                        f"{profile_name}/{target_name}: tracked implementation is outside "
                        f"the production ownership model: {path}"
                    )
                if declared not in modules:
                    raise CoverageError(
                        f"{profile_name}/{target_name}: unknown declared owner "
                        f"{declared!r} for {path}"
                    )
                if declared != inferred:
                    raise CoverageError(
                        f"{profile_name}/{target_name}: owner drift for {path}: "
                        f"catalog={inferred}, build_inventory={declared}"
                    )



def validate(root: Path | str = ROOT) -> list[str]:
    root = Path(root).resolve()
    try:
        tracked = _tracked_files(root)
        catalog = _load_json(root / CATALOG)
        inventory = _load_json(root / BUILD_INVENTORY)
        if (
            not isinstance(catalog, dict)
            or catalog.get("schema") != "heptatrader.module-catalog.v1"
        ):
            raise CoverageError("unsupported module catalog")
        if (
            not isinstance(inventory, dict)
            or inventory.get("schema") != "heptatrader.build-targets.v1"
        ):
            raise CoverageError("unsupported build target inventory")
        owners, _, unbuilt_paths = _module_ownership(root, tracked, catalog)
        _validate_build_ownership(set(tracked), owners, catalog, inventory)
        _validate_source_reachability(set(tracked), owners, unbuilt_paths, inventory)
        return []
    except CoverageError as error:
        return [str(error)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    errors = validate(args.root)
    for error in errors:
        print(f"[COMPONENT-COVERAGE] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[COMPONENT-COVERAGE] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
