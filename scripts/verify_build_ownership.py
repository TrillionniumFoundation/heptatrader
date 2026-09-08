#!/usr/bin/env python3
"""Compare reviewed Linux build ownership with fresh CMake file-api models.

Default verification configures the SDK-free core profile without compiling.
IB verification and inventory generation require an explicitly supplied SDK
and BID archive. This checks build membership/ownership, not functional gap
closure, broker qualification, or authorization.
"""
from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "heptatrader.build-targets.v1"
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
    "scope": "Targets and translation units exposed by CMake file-api codemodel for canonical core and IB SDK profiles. Imported/interface targets are not generally exposed; legacy monolith, legacy simulator, legacy bridge and IB probe are excluded.",
    "ownership": "Implementation TUs use the most specific module-catalog implementation path; test TUs and generated CMake PCH TUs belong to their compiling target; external IB SDK TUs have no repository module owner.",
}


class OwnershipError(ValueError):
    pass


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OwnershipError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        if path.is_symlink() or not path.is_file():
            raise OwnershipError(f"expected regular JSON file: {path}")
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              OwnershipError(f"non-finite JSON: {value}")))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OwnershipError(f"cannot read {path}: {error}") from error


def canonical_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise OwnershipError(f"invalid repository-relative path: {value!r}")
    path = Path(value)
    if path.is_absolute() or path.as_posix() != value or any(
            part in ("", ".", "..") for part in value.split("/")):
        raise OwnershipError(f"non-canonical path: {value!r}")
    return value


def regular_repository_path(root: Path, value: Any) -> str:
    value = canonical_path(value)
    path = root / value
    if path.resolve() != path or not path.is_file():
        raise OwnershipError(f"missing or symlinked repository file: {value}")
    return value


def module_paths(root: Path) -> dict[str, list[str]]:
    catalog = load_json(root / "docs/module-catalog.json")
    if not isinstance(catalog, dict) or catalog.get("schema") != "heptatrader.module-catalog.v1":
        raise OwnershipError("unsupported module catalog")
    result: dict[str, list[str]] = {}
    for module in catalog.get("modules", []):
        if not isinstance(module, dict):
            raise OwnershipError("invalid module entry")
        name = module.get("id")
        if not isinstance(name, str) or not name or name in result:
            raise OwnershipError(f"invalid or duplicate module id: {name!r}")
        regular_repository_path(root, module.get("document"))
        implementations = module.get("implementation")
        if not isinstance(implementations, list) or not implementations:
            raise OwnershipError(f"{name}: missing implementation paths")
        result[name] = [canonical_path(item) for item in implementations]
    if not result:
        raise OwnershipError("module catalog is empty")
    return result


def owner_for(path: str, modules: dict[str, list[str]]) -> str:
    candidates = [(len(prefix), module) for module, paths in modules.items()
                  for prefix in paths if path == prefix or path.startswith(prefix + "/")]
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
        raise OwnershipError("the reviewed inventory covers Linux only")
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
        raise OwnershipError("expected exactly one fresh CMake file-api reply")
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
                # Only CMake's own generated dashboard utilities are outside
                # repository ownership. An external user-defined target is an error.
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
                    precompiled_headers.add(regular_repository_path(root, header.relative_to(root).as_posix()))
            for source in data.get("sources", []):
                if "compileGroupIndex" not in source:
                    continue  # inventory covers translation units, not headers/rules
                path = Path(source["path"])
                expected_pch = build_root / data["paths"]["build"] / "CMakeFiles" / (data["name"] + ".dir") / "cmake_pch.hxx.cxx"
                if path == expected_pch and precompiled_headers:
                    normalized = canonical_path(path.relative_to(build_root).as_posix())
                    kind, owner = "generated_cmake_pch", data["name"]
                elif source.get("isGenerated"):
                    raise OwnershipError(f"{data['name']}: unclassified generated translation unit")
                elif path.is_absolute() and not path.is_relative_to(root):
                    if profile != "ib" or sdk is None or not path.is_relative_to(sdk.resolve()):
                        raise OwnershipError(f"{data['name']}: unclassified external translation unit {path}")
                    normalized = canonical_path(path.relative_to(sdk.resolve()).as_posix())
                    kind, owner = "external_ib_sdk", None
                else:
                    normalized = regular_repository_path(root,
                        path.relative_to(root).as_posix() if path.is_absolute() else path.as_posix())
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
            targets.append({"name": data["name"], "type": data["type"],
                            "declared_in": declared_in,
                            "precompiled_headers": sorted(precompiled_headers),
                            "dependencies": sorted(names[item["id"]] for item in data.get("dependencies", [])),
                            "translation_units": sorted(sources, key=lambda item: (item["kind"], item["path"]))})
    except (KeyError, TypeError, IndexError) as error:
        raise OwnershipError(f"incomplete CMake file-api model: {error}") from error
    return {"requires_ib_sdk": profile == "ib", "options": profile_options(profile),
            "targets": sorted(targets, key=lambda target: target["name"])}


def observe(root: Path, profile: str, sdk: Path | None = None,
            decimal: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    modules = module_paths(root)
    with tempfile.TemporaryDirectory(prefix="hepta-build-ownership-") as temporary:
        reply = configure_model(root, Path(temporary) / "build", profile, sdk, decimal)
        return read_model(root, reply, profile, modules, sdk)


def validate_inventory(root: Path, inventory: Any) -> None:
    if (not isinstance(inventory, dict) or set(inventory) != {"schema", "coverage", "profiles"} or
        inventory["schema"] != SCHEMA or inventory["coverage"] != COVERAGE or
        not isinstance(inventory["profiles"], dict) or set(inventory["profiles"]) != {"core", "ib"}):
        raise OwnershipError("unsupported inventory schema or profile coverage")
    modules = module_paths(root)
    for profile, content in inventory["profiles"].items():
        if (not isinstance(content, dict) or set(content) != {"requires_ib_sdk", "options", "targets"} or
            content["requires_ib_sdk"] is not (profile == "ib") or content["options"] != profile_options(profile)):
            raise OwnershipError(f"{profile}: profile options or SDK scope changed")
        targets = content["targets"]
        if not isinstance(targets, list) or not targets:
            raise OwnershipError(f"{profile}: empty target inventory")
        names = [target.get("name") for target in targets if isinstance(target, dict)]
        if len(names) != len(targets) or any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
            raise OwnershipError(f"{profile}: invalid or duplicate target name")
        for target in targets:
            if set(target) != {"name", "type", "declared_in", "precompiled_headers", "dependencies", "translation_units"}:
                raise OwnershipError(f"{profile}: unexpected target fields")
            regular_repository_path(root, target["declared_in"])
            if target["type"] not in {"EXECUTABLE", "STATIC_LIBRARY", "SHARED_LIBRARY", "MODULE_LIBRARY", "OBJECT_LIBRARY", "UTILITY"}:
                raise OwnershipError(f"{target['name']}: unsupported target type")
            dependencies = target["dependencies"]
            if not isinstance(dependencies, list) or len(set(dependencies)) != len(dependencies) or any(item not in names for item in dependencies):
                raise OwnershipError(f"{target['name']}: unknown or duplicate dependency")
            headers = target["precompiled_headers"]
            if not isinstance(headers, list) or len(set(headers)) != len(headers):
                raise OwnershipError(f"{target['name']}: invalid precompiled headers")
            for header in headers:
                regular_repository_path(root, header)
            seen = set()
            if not isinstance(target["translation_units"], list):
                raise OwnershipError(f"{target['name']}: invalid translation units")
            for source in target["translation_units"]:
                if not isinstance(source, dict) or set(source) != {"path", "kind", "owner", "language", "standard"}:
                    raise OwnershipError(f"{target['name']}: invalid translation unit fields")
                path = canonical_path(source["path"])
                identity = (source["kind"], path)
                if identity in seen:
                    raise OwnershipError(f"{target['name']}: duplicate translation unit")
                seen.add(identity)
                if source["kind"] == "external_ib_sdk":
                    if profile != "ib" or source["owner"] is not None:
                        raise OwnershipError("external SDK translation unit cannot have a repository owner")
                elif source["kind"] == "test":
                    regular_repository_path(root, path)
                    if not path.startswith("tests/") or source["owner"] != target["name"]:
                        raise OwnershipError("test translation unit ownership mismatch")
                elif source["kind"] == "generated_cmake_pch":
                    if source["owner"] != target["name"] or not headers or not path.endswith(
                            f"CMakeFiles/{target['name']}.dir/cmake_pch.hxx.cxx"):
                        raise OwnershipError("generated CMake PCH ownership mismatch")
                elif source["kind"] == "implementation":
                    regular_repository_path(root, path)
                    if source["owner"] not in modules or source["owner"] != owner_for(path, modules):
                        raise OwnershipError(f"{path}: canonical module owner mismatch")
                else:
                    raise OwnershipError(f"{path}: unknown ownership kind")


def verify(root: Path, inventory: Any, profile: str = "core",
           sdk: Path | None = None, decimal: Path | None = None) -> None:
    root = root.resolve()
    validate_inventory(root, inventory)
    actual = observe(root, profile, sdk, decimal)
    expected = inventory["profiles"][profile]
    if actual != expected:
        difference = list(difflib.unified_diff(
            json.dumps(expected, indent=2, sort_keys=True).splitlines(),
            json.dumps(actual, indent=2, sort_keys=True).splitlines(),
            fromfile="reviewed inventory", tofile="fresh CMake file-api", lineterm=""))
        raise OwnershipError(f"{profile}: build inventory drift\n" + "\n".join(difference[:100]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--profile", choices=("core", "ib"), default="core")
    parser.add_argument("--ib-sdk", type=Path)
    parser.add_argument("--ib-decimal-library", type=Path)
    parser.add_argument("--generate", action="store_true", help="regenerate both profiles; requires explicit IB SDK/archive")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    path = args.inventory or root / "docs/build-targets.json"
    try:
        if args.generate:
            inventory = {"schema": SCHEMA, "coverage": COVERAGE,
                         "profiles": {profile: observe(root, profile, args.ib_sdk, args.ib_decimal_library)
                                      for profile in ("core", "ib")}}
            validate_inventory(root, inventory)
            path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
            print("[BUILD OWNERSHIP] generated both reviewed-profile candidates; review changes before committing")
        else:
            verify(root, load_json(path), args.profile, args.ib_sdk, args.ib_decimal_library)
            print(f"[BUILD OWNERSHIP] PASS {args.profile}: fresh CMake target/TU membership and canonical ownership match")
        return 0
    except (OwnershipError, OSError) as error:
        print(f"[BUILD OWNERSHIP] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
