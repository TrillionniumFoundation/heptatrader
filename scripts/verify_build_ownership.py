#!/usr/bin/env python3
"""Validate the live CMake build graph against repository ownership.

The CMake File API is the build truth.  No checked-in expansion of every target,
dependency and translation unit is required: ordinary target/source refactors
must not create a second graph-maintenance ceremony.  The module catalog remains
the reviewed ownership policy, and every tracked C/C++ implementation source
owned by that catalog must either be present in the selected live build graph or
be explicitly listed as ``unbuilt`` by its owner.

The default SDK-free core profile only configures CMake.  The IB profile requires
an explicitly supplied SDK and BID archive because its real configure-time ABI
probe is part of that build.  This validates source/build ownership, not Broker
qualification or trading authorization.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import platform
import subprocess
import sys
import tempfile
from typing import Any

from source_json import SourceJsonError, load_source_json

ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = "heptatrader.build-observation.v2"
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".c++"}
OPTIONS = {
    "CMAKE_BUILD_TYPE": "Release",
    "BUILD_TESTING": "ON",
    "BUILD_IB_PROBE": "OFF",
    "HEPTA_BUILD_LEGACY_MONOLITH": "OFF",
    "HEPTA_BUILD_LEGACY_SIMULATOR": "OFF",
    "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE": "OFF",
}
COVERAGE = {
    "platform": "Linux",
    "configuration": "Release",
    "scope": (
        "Live targets and translation units exposed by the selected CMake File "
        "API codemodel. Imported/interface targets are not generally exposed; "
        "legacy monolith/simulator/bridge and the optional IB probe are excluded."
    ),
    "ownership": (
        "Repository implementation TUs use the most-specific module-catalog "
        "boundary; tests/generated PCH TUs belong to their compiling target; "
        "external IB SDK TUs have no repository owner."
    ),
}


class OwnershipError(ValueError):
    pass


def load_json(path: Path) -> Any:
    try:
        return load_source_json(path)
    except SourceJsonError as error:
        raise OwnershipError(f"cannot read {path}: {error}") from error


def canonical_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise OwnershipError(f"invalid repository-relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(
            part in ("", ".", "..") for part in path.parts):
        raise OwnershipError(f"non-canonical path: {value!r}")
    return value


def regular_repository_path(root: Path, value: Any) -> str:
    value = canonical_path(value)
    path = root / value
    try:
        if path.resolve() != path or not path.is_file():
            raise OwnershipError(f"missing or symlinked repository file: {value}")
    except OSError as error:
        raise OwnershipError(f"cannot inspect repository file {value}: {error}") from error
    return value


def module_model(root: Path) -> tuple[dict[str, list[str]], set[str]]:
    catalog = load_json(root / "docs/module-catalog.json")
    if not isinstance(catalog, dict) or catalog.get("schema") != "heptatrader.module-catalog.v1":
        raise OwnershipError("unsupported module catalog")
    modules: dict[str, list[str]] = {}
    unbuilt: set[str] = set()
    for module in catalog.get("modules", []):
        if not isinstance(module, dict):
            raise OwnershipError("invalid module entry")
        name = module.get("id")
        if not isinstance(name, str) or not name or name in modules:
            raise OwnershipError(f"invalid or duplicate module id: {name!r}")
        regular_repository_path(root, module.get("document"))
        implementations = module.get("implementation")
        if not isinstance(implementations, list) or not implementations:
            raise OwnershipError(f"{name}: missing implementation paths")
        modules[name] = [canonical_path(item) for item in implementations]
        declared_unbuilt = module.get("unbuilt", [])
        if not isinstance(declared_unbuilt, list):
            raise OwnershipError(f"{name}: unbuilt must be a list")
        for item in declared_unbuilt:
            item = canonical_path(item)
            if Path(item).suffix.lower() not in SOURCE_SUFFIXES:
                raise OwnershipError(f"{name}: unbuilt path is not C/C++ source: {item}")
            if item in unbuilt:
                raise OwnershipError(f"duplicate unbuilt source: {item}")
            if not any(item == prefix or item.startswith(prefix.rstrip("/") + "/")
                       for prefix in modules[name]):
                raise OwnershipError(f"{name}: unbuilt source is outside its implementation boundary: {item}")
            regular_repository_path(root, item)
            unbuilt.add(item)
    if not modules:
        raise OwnershipError("module catalog is empty")
    return modules, unbuilt


def module_paths(root: Path) -> dict[str, list[str]]:
    """Compatibility helper used by focused unit tests."""
    return module_model(root)[0]


def owner_for(path: str, modules: dict[str, list[str]]) -> str:
    candidates = [(len(prefix.rstrip("/")), module)
                  for module, paths in modules.items()
                  for prefix in paths
                  if path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/")]
    longest = max((length for length, _ in candidates), default=-1)
    owners = sorted({module for length, module in candidates if length == longest})
    if len(owners) != 1:
        raise OwnershipError(f"{path}: expected one canonical module owner, found {owners}")
    return owners[0]


def profile_options(profile: str) -> dict[str, str]:
    return dict(OPTIONS, HEPTA_ENABLE_IBAPI="ON" if profile == "ib" else "OFF")


def configure_model(root: Path, build: Path, profile: str,
                    sdk: Path | None, decimal: Path | None) -> Path:
    if profile not in ("core", "ib"):
        raise OwnershipError(f"unknown profile: {profile}")
    if platform.system() != "Linux":
        raise OwnershipError("live build-ownership verification currently covers Linux only")
    query = build / ".cmake/api/v1/query"
    query.mkdir(parents=True)
    (query / "codemodel-v2").touch()
    command = ["cmake", "-S", str(root), "-B", str(build), "-G", "Unix Makefiles"]
    command += [f"-D{key}={value}" for key, value in sorted(profile_options(profile).items())]
    if profile == "ib":
        if sdk is None or decimal is None or not sdk.is_dir() or not decimal.is_file():
            raise OwnershipError("IB profile requires --ib-sdk and --ib-decimal-library")
        command += [f"-DIBAPI_ROOT={sdk.resolve()}", f"-DIBAPI_DECIMAL_LIBRARY={decimal.resolve()}"]
    try:
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OwnershipError(f"CMake configure failed: {error}") from error
    if result.returncode:
        raise OwnershipError(f"CMake configure failed ({result.returncode}):\n{result.stdout[-6000:]}")
    return build / ".cmake/api/v1/reply"


def read_model(root: Path, reply: Path, profile: str,
               modules: dict[str, list[str]], sdk: Path | None) -> dict[str, Any]:
    indexes = list(reply.glob("index-*.json"))
    if len(indexes) != 1:
        raise OwnershipError("expected exactly one fresh CMake File API reply")
    index = load_json(indexes[0])
    try:
        model = load_json(reply / index["reply"]["codemodel-v2"]["jsonFile"])
        configs = model["configurations"]
        if len(configs) != 1 or configs[0]["name"] != "Release":
            raise OwnershipError("expected one Release configuration")
        if Path(model["paths"]["source"]).resolve() != root:
            raise OwnershipError("CMake source directory mismatch")
        build_root = Path(model["paths"]["build"]).resolve()
        entries = configs[0]["targets"]
        names = {entry["id"]: entry["name"] for entry in entries}
        if len(names) != len(entries) or len(set(names.values())) != len(entries):
            raise OwnershipError("duplicate CMake target identity")
        targets = []
        cmake_root = Path(index["cmake"]["paths"]["root"]).resolve()
        for entry in entries:
            data = load_json(reply / entry["jsonFile"])
            graph = data["backtraceGraph"]
            declaration = graph["nodes"][data["backtrace"]]
            declared = Path(graph["files"][declaration["file"]])
            if declared.is_absolute():
                if (data["type"] == "UTILITY" and
                    data.get("folder", {}).get("name") == "CTestDashboardTargets" and
                    declared.resolve() == cmake_root / "Modules/CTestTargets.cmake"):
                    continue
                if not declared.is_relative_to(root):
                    raise OwnershipError(f"{data['name']}: target declared outside repository")
                declared = declared.relative_to(root)
            declared_in = regular_repository_path(root, declared.as_posix())
            sources = []
            precompiled_headers = set()
            for group in data.get("compileGroups", []):
                for item in group.get("precompileHeaders", []):
                    header = Path(item["header"])
                    if not header.is_absolute() or not header.is_relative_to(root):
                        raise OwnershipError(f"{data['name']}: unclassified precompiled header")
                    precompiled_headers.add(regular_repository_path(
                        root, header.relative_to(root).as_posix()))
            for source in data.get("sources", []):
                if "compileGroupIndex" not in source:
                    continue
                path = Path(source["path"])
                expected_pch = (build_root / data["paths"]["build"] / "CMakeFiles" /
                                (data["name"] + ".dir") / "cmake_pch.hxx.cxx")
                if path == expected_pch and precompiled_headers:
                    normalized = canonical_path(path.relative_to(build_root).as_posix())
                    kind, owner = "generated_cmake_pch", data["name"]
                elif source.get("isGenerated") or (
                        path.is_absolute() and path.is_relative_to(build_root)):
                    raise OwnershipError(f"{data['name']}: unclassified generated translation unit")
                elif path.is_absolute() and not path.is_relative_to(root):
                    if profile != "ib" or sdk is None or not path.is_relative_to(sdk.resolve()):
                        raise OwnershipError(f"{data['name']}: unclassified external translation unit {path}")
                    normalized = canonical_path(path.relative_to(sdk.resolve()).as_posix())
                    kind, owner = "external_ib_sdk", None
                else:
                    normalized = regular_repository_path(
                        root, path.relative_to(root).as_posix() if path.is_absolute() else path.as_posix())
                    if normalized.startswith("tests/"):
                        kind, owner = "test", data["name"]
                    else:
                        kind, owner = "implementation", owner_for(normalized, modules)
                group = data["compileGroups"][source["compileGroupIndex"]]
                sources.append({"path": normalized, "kind": kind, "owner": owner,
                                "language": group["language"],
                                "standard": group.get("languageStandard", {}).get("standard")})
            if len({(s["kind"], s["path"]) for s in sources}) != len(sources):
                raise OwnershipError(f"{data['name']}: duplicate translation unit")
            dependencies = sorted(names[item["id"]] for item in data.get("dependencies", []))
            targets.append({"name": data["name"], "type": data["type"],
                            "declared_in": declared_in,
                            "precompiled_headers": sorted(precompiled_headers),
                            "dependencies": dependencies,
                            "translation_units": sorted(
                                sources, key=lambda item: (item["kind"], item["path"]))})
    except (KeyError, TypeError, IndexError) as error:
        raise OwnershipError(f"incomplete CMake File API model: {error}") from error
    return {"requires_ib_sdk": profile == "ib", "options": profile_options(profile),
            "targets": sorted(targets, key=lambda target: target["name"])}


def observe(root: Path, profile: str, sdk: Path | None = None,
            decimal: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    modules, _ = module_model(root)
    with tempfile.TemporaryDirectory(prefix="hepta-build-ownership-") as temporary:
        reply = configure_model(root, Path(temporary) / "build", profile, sdk, decimal)
        return read_model(root, reply, profile, modules, sdk)


def tracked_files(root: Path) -> set[str]:
    try:
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OwnershipError(f"cannot enumerate tracked source: {error}") from error
    if result.returncode:
        raise OwnershipError("build ownership requires a Git worktree: " +
                             result.stderr.decode("utf-8", "replace").strip())
    tracked: set[str] = set()
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            tracked.add(canonical_path(raw.decode("utf-8", "strict")))
        except UnicodeError as error:
            raise OwnershipError("tracked path is not valid UTF-8") from error
    return tracked


def verify_source_reachability(root: Path, observed: dict[str, Any]) -> None:
    modules, unbuilt = module_model(root)
    tracked = tracked_files(root)
    built = {
        source["path"]
        for target in observed["targets"]
        for source in target["translation_units"]
        if source["kind"] == "implementation"
    }
    candidates: set[str] = set()
    for path in tracked:
        if Path(path).suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if not any(path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/")
                   for paths in modules.values() for prefix in paths):
            continue
        # Once a tracked source enters any implementation boundary, ambiguity
        # is an error rather than a reason to skip reachability validation.
        owner_for(path, modules)
        regular_repository_path(root, path)
        candidates.add(path)
    missing = sorted(candidates - built - unbuilt)
    if missing:
        raise OwnershipError(
            "tracked production C/C++ source is absent from the selected live CMake graph "
            "and is not explicitly unbuilt: " + ", ".join(missing)
        )
    stale_unbuilt = sorted(unbuilt & built)
    if stale_unbuilt:
        raise OwnershipError(
            "module catalog marks live CMake sources as unbuilt: " + ", ".join(stale_unbuilt)
        )


def verify(root: Path, profile: str = "core", sdk: Path | None = None,
           decimal: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    observed = observe(root, profile, sdk, decimal)
    verify_source_reachability(root, observed)
    return observed


def render_report(profiles: dict[str, dict[str, Any]]) -> str:
    report = {"schema": REPORT_SCHEMA, "coverage": COVERAGE, "profiles": profiles}
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def write_report(path: Path, profiles: dict[str, dict[str, Any]]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = render_report(profiles)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(contents)
            stream.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--profile", choices=("core", "ib", "all"), default="core")
    parser.add_argument("--ib-sdk", type=Path)
    parser.add_argument("--ib-decimal-library", type=Path)
    parser.add_argument("--report", type=Path,
                        help="optional generated observation report; never a source-of-truth input")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    selected = ("core", "ib") if args.profile == "all" else (args.profile,)
    try:
        observed: dict[str, dict[str, Any]] = {}
        for profile in selected:
            observed[profile] = verify(
                root, profile, args.ib_sdk, args.ib_decimal_library)
            print(f"[BUILD OWNERSHIP] PASS {profile}: live CMake membership, canonical ownership and source reachability")
        if args.report is not None:
            write_report(args.report, observed)
            print(f"[BUILD OWNERSHIP] wrote diagnostic report {args.report}")
        return 0
    except (OwnershipError, OSError) as error:
        print(f"[BUILD OWNERSHIP] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
