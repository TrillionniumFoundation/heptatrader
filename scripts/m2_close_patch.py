#!/usr/bin/env python3
"""Close M2 physical ownership and compilation exceptions in one tested change."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CMAKE = ROOT / "HeptaTrade/CMakeLists.txt"
MODULE_DIR = ROOT / "docs/modules/manifests"
MODULE_REGISTRY = ROOT / "docs/modules/module-registry-v2.json"
OWNERSHIP = ROOT / "docs/modules/source-ownership-registry-v1.json"
WORKFLOW = ROOT / ".github/workflows/gap-closure-stage.yml"
SELF = Path(__file__)
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp"}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: expected JSON object")
    return value


def write_json(path: Path, value: dict, *, compact: bool = False) -> None:
    if compact:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new)


def rewrite_cmake_command_sources(
    text: str, target: str, remove: set[str]
) -> str:
    candidates = (f"add_library({target}", f"add_executable({target}")
    starts = [text.find(candidate) for candidate in candidates]
    starts = [value for value in starts if value >= 0]
    if not starts:
        raise SystemExit(f"CMake target not found: {target}")
    start = min(starts)
    opening = text.find("(", start)
    depth = 0
    end = -1
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        raise SystemExit(f"unbalanced CMake command: {target}")
    command = text[start : end + 1]
    newline = command.find("\n")
    if newline < 0:
        raise SystemExit(f"single-line source command unsupported: {target}")
    header = command[: newline + 1]
    lines = command[newline + 1 : -1].splitlines()
    filtered = [line for line in lines if line.strip() not in remove]
    if len(filtered) == len(lines):
        missing = ", ".join(sorted(remove))
        raise SystemExit(f"{target}: no requested source removed: {missing}")
    return text[:start] + header + "\n".join(filtered) + ")" + text[end + 1 :]


def add_target_link(text: str, target: str, library: str) -> str:
    marker = f"target_link_libraries({target}"
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"target_link_libraries missing: {target}")
    opening = text.find("(", start)
    depth = 0
    end = -1
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        raise SystemExit(f"unbalanced link command: {target}")
    command = text[start : end + 1]
    if re.search(rf"\b{re.escape(library)}\b", command):
        return text
    command = command[:-1] + f"\n    {library})"
    return text[:start] + command + text[end + 1 :]


def patch_cmake() -> None:
    text = CMAKE.read_text(encoding="utf-8")
    marker = "add_library(hepta_agent_os_core STATIC\n"
    session_target = """add_library(hepta_session_core STATIC
    tool_host/session_supervisor_lease_store.cpp
    tool_host/session_supervisor_protocol.cpp)
hepta_runtime_target(hepta_session_core)
target_link_libraries(hepta_session_core PUBLIC
    Threads::Threads
    OpenSSL::Crypto)

"""
    text = replace_once(text, marker, session_target + marker, "session target insertion")
    text = rewrite_cmake_command_sources(
        text,
        "hepta_agent_os_core",
        {
            "tool_host/session_supervisor_lease_store.cpp",
            "tool_host/session_supervisor_protocol.cpp",
        },
    )
    text = add_target_link(text, "hepta_agent_os_core", "hepta_session_core")
    text = rewrite_cmake_command_sources(
        text,
        "hepta_sessionctl",
        {
            "tool_host/session_supervisor_lease_store.cpp",
            "tool_host/session_supervisor_protocol.cpp",
        },
    )
    text = add_target_link(text, "hepta_sessionctl", "hepta_session_core")
    CMAKE.write_text(text, encoding="utf-8")


def add_specific_rule(
    rules: list[dict], rule_id: str, path: str, owner: str
) -> None:
    if any(item.get("id") == rule_id for item in rules):
        raise SystemExit(f"duplicate ownership rule: {rule_id}")
    rules.append(
        {
            "id": rule_id,
            "selector": {"kind": "prefix", "path": path},
            "physical_owner": owner,
            "priority": 350,
        }
    )


def selector_matches(relative: str, selector: dict) -> bool:
    kind = selector["kind"]
    path = selector["path"]
    if kind == "file":
        return relative == path
    if kind == "directory":
        base = path.rstrip("/")
        return relative == base or relative.startswith(base + "/")
    prefix = PurePosixPath(path)
    candidate = PurePosixPath(relative)
    return candidate.parent == prefix.parent and candidate.name.startswith(prefix.name)


def specificity(selector: dict) -> tuple[int, int]:
    return {"directory": 1, "prefix": 2, "file": 3}[selector["kind"]], len(selector["path"])


def physical_owner(relative: str, rules: list[dict]) -> str:
    matching = [item for item in rules if selector_matches(relative, item["selector"])]
    if not matching:
        raise SystemExit(f"no physical owner: {relative}")
    best_key = max(
        (item["priority"], *specificity(item["selector"])) for item in matching
    )
    best = [
        item
        for item in matching
        if (item["priority"], *specificity(item["selector"])) == best_key
    ]
    owners = {item["physical_owner"] for item in best}
    if len(owners) != 1:
        raise SystemExit(f"ambiguous physical owner for {relative}: {sorted(owners)}")
    return next(iter(owners))


def active_sources() -> list[str]:
    result = []
    for path in sorted((ROOT / "HeptaTrade").rglob("*")):
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES:
            result.append(path.relative_to(ROOT).as_posix())
    if not result:
        raise SystemExit("active source set is empty")
    return result


def prefix_candidates(parent: str, files: set[str], all_files: set[str]) -> list[tuple[set[str], str]]:
    names = {PurePosixPath(path).name for path in files if PurePosixPath(path).parent.as_posix() == parent}
    all_in_parent = {
        path for path in all_files if PurePosixPath(path).parent.as_posix() == parent
    }
    candidates: dict[str, set[str]] = {}
    for name in names:
        boundaries = {index + 1 for index, char in enumerate(name) if char in {"_", "-"}}
        stem = PurePosixPath(name).stem
        boundaries.add(len(stem))
        for length in sorted(boundaries):
            if length < 3:
                continue
            prefix_name = name[:length]
            matched = {
                path
                for path in all_in_parent
                if PurePosixPath(path).name.startswith(prefix_name)
            }
            if len(matched) >= 2 and matched <= files:
                candidates[f"{parent}/{prefix_name}"] = matched
    return [(matched, path) for path, matched in candidates.items()]


def compress_roots(owner_files: set[str], all_files: set[str]) -> list[str]:
    remaining = set(owner_files)
    roots: list[str] = []

    directories: list[tuple[int, int, str, set[str]]] = []
    candidate_dirs = {
        PurePosixPath(path).parent.as_posix()
        for path in owner_files
        if PurePosixPath(path).parent.as_posix() != "HeptaTrade"
    }
    for directory in candidate_dirs:
        under = {
            path
            for path in all_files
            if path.startswith(directory.rstrip("/") + "/")
        }
        if len(under) >= 2 and under <= owner_files:
            directories.append((len(under), -len(directory), directory, under))
    for _, _, directory, matched in sorted(directories, reverse=True):
        current = matched & remaining
        if len(current) < 2 or current != matched:
            continue
        roots.append(directory.rstrip("/") + "/")
        remaining -= matched

    while remaining:
        candidates: list[tuple[int, int, str, set[str]]] = []
        parents = {PurePosixPath(path).parent.as_posix() for path in remaining}
        for parent in parents:
            for matched, path in prefix_candidates(parent, remaining, all_files):
                current = matched & remaining
                if len(current) >= 2 and current == matched:
                    candidates.append((len(current), -len(path), path, matched))
        if not candidates:
            break
        _, _, path, matched = max(candidates)
        roots.append(path)
        remaining -= matched

    roots.extend(sorted(remaining))
    roots = sorted(dict.fromkeys(roots))
    if len(roots) > 64:
        raise SystemExit(f"module {next(iter(owner_files))}: generated {len(roots)} roots")
    return roots


def add_session_manifest() -> None:
    path = MODULE_DIR / "hepta-session-runtime.json"
    if path.exists():
        raise SystemExit("session manifest already exists")
    manifest = {
        "schema": "heptatrader.module-manifest.v2",
        "id": "hepta.session.runtime",
        "version": "1.0.0",
        "lifecycle": "current",
        "kind": "stateful-library",
        "trust_domain": "agent-gateway",
        "authority": "durable session lease and supervisor protocol state",
        "ownership_mode": "exclusive",
        "source_roots": [],
        "build_targets": ["hepta_session_core"],
        "provides": [],
        "consumes": [],
        "allowed_dependencies": [],
        "forbidden_dependencies": ["hepta.venue.*", "broker.credentials"],
        "state": {
            "model": "durable-generation-fenced",
            "persistence": "module-declared",
            "writer": "single-owner",
        },
        "concurrency": {
            "model": "supervisor-serialized",
            "shard_key": "module-declared",
            "blocking_io": "declared-only",
            "cross_module_lock": "forbidden",
        },
        "backpressure": {"class": "control-path", "overflow": "typed-failure"},
        "failure": {"risk_increase": "fail-closed", "safe_exit": "never-weaken"},
        "resource_budget": "session-control-v1",
        "owners": {
            "dri": "@hepta/session-control",
            "backup": "@hepta/gateway",
            "reviewers": ["@hepta/security-runtime"],
        },
        "verification": ["session-boundary"],
    }
    write_json(path, manifest)


def patch_registry_and_manifests(rules: list[dict], files: list[str]) -> None:
    registry = read_json(MODULE_REGISTRY)
    session_path = "modules/manifests/hepta-session-runtime.json"
    paths = registry.get("manifest_paths")
    if not isinstance(paths, list):
        raise SystemExit("module registry manifest_paths invalid")
    if session_path not in paths:
        paths.append(session_path)
        paths.sort()
    write_json(MODULE_REGISTRY, registry)

    manifests: dict[str, tuple[Path, dict]] = {}
    for relative in paths:
        path = ROOT / "docs" / relative
        data = read_json(path)
        manifests[data["id"]] = (path, data)

    owned: dict[str, set[str]] = defaultdict(set)
    for relative in files:
        owned[physical_owner(relative, rules)].add(relative)

    all_files = set(files)
    for module_id, (path, data) in manifests.items():
        if module_id in owned:
            preserved = [
                root
                for root in data.get("source_roots", [])
                if not isinstance(root, str) or not root.startswith("HeptaTrade/")
            ]
            generated = compress_roots(owned[module_id], all_files)
            data["source_roots"] = sorted(dict.fromkeys(generated + preserved))
        if data.get("ownership_mode") == "shared-migration":
            data["ownership_mode"] = "exclusive"
            data.pop("migration_gap", None)
        if module_id in {"hepta.gateway.runtime", "hepta.client.runtime"}:
            deps = data.setdefault("allowed_dependencies", [])
            if "hepta.session.runtime" not in deps:
                deps.append("hepta.session.runtime")
                deps.sort()
        write_json(path, data)


def patch_ownership() -> None:
    data = read_json(OWNERSHIP)
    rules = data.get("physical_ownership_rules")
    if not isinstance(rules, list):
        raise SystemExit("physical_ownership_rules invalid")
    add_specific_rule(
        rules,
        "execution-event-feed-client",
        "HeptaTrade/execution/execution_event_feed_client",
        "hepta.execution.runtime",
    )
    add_specific_rule(
        rules,
        "execution-event-feed-server",
        "HeptaTrade/execution/execution_event_feed_server",
        "hepta.execution.runtime",
    )
    add_specific_rule(
        rules,
        "client-unix-tool",
        "HeptaTrade/tool_host/unix_tool_client",
        "hepta.client.runtime",
    )
    add_specific_rule(
        rules,
        "client-unix-session-supervisor",
        "HeptaTrade/tool_host/unix_session_supervisor_client",
        "hepta.client.runtime",
    )
    add_specific_rule(
        rules,
        "session-lease-store",
        "HeptaTrade/tool_host/session_supervisor_lease_store",
        "hepta.session.runtime",
    )
    add_specific_rule(
        rules,
        "session-protocol",
        "HeptaTrade/tool_host/session_supervisor_protocol",
        "hepta.session.runtime",
    )
    data["source_overlap_exceptions"] = []
    data["compilation_exceptions"] = []
    files = active_sources()
    add_session_manifest()
    patch_registry_and_manifests(rules, files)
    write_json(OWNERSHIP, data, compact=True)


def main() -> None:
    patch_cmake()
    patch_ownership()
    subprocess.run(
        ["python3", "scripts/generate_documentation_views.py", "--write"],
        cwd=ROOT,
        check=True,
    )
    WORKFLOW.unlink()
    SELF.unlink()


if __name__ == "__main__":
    main()
