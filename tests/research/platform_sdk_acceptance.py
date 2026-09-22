#!/usr/bin/env python3
"""Exact-source, offline-only platform build/package acceptance. Never grants authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import xml.etree.ElementTree as ET

PROFILES = {
    "linux-gcc": ("Linux", "x86_64", "GNU", "g++"),
    "macos-arm64": ("Darwin", "arm64", "AppleClang", "clang++"),
    "macos-x86_64": ("Darwin", "x86_64", "AppleClang", "clang++"),
    "windows-msvc": ("Windows", "AMD64", "MSVC", "cl"),
}
REQUIRED_TESTS = {
    "hepta_research_market_data_tests", "hepta_research_analytics_tests",
    "hepta_research_replay_tests", "hepta_research_replay_example",
    "hepta_research_cli_behavior", "hepta_research_model_cli",
    "hepta_research_model_cli_install", "hepta_research_sdk_install",
}


def run(command: list[str], root: Path, *, capture: bool = False) -> str:
    print("+", repr(command), flush=True)
    result = subprocess.run(command, cwd=root, check=True, text=True, timeout=600,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def source_identity(root: Path, expected: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("explicit exact source SHA required")
    if run(["git", "rev-parse", "HEAD"], root, capture=True) != expected:
        raise ValueError("source HEAD moved")
    if run(["git", "status", "--porcelain", "--untracked-files=normal"], root, capture=True):
        raise ValueError("source is not clean")
    return run(["git", "rev-parse", "HEAD^{tree}"], root, capture=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    system, architecture, compiler_id, compiler = PROFILES[args.profile]
    if (platform.system(), platform.machine()) != (system, architecture):
        raise ValueError("host does not match the declared platform profile")
    if os.environ.get("DESTDIR"):
        raise ValueError("DESTDIR is not accepted")
    tree = source_identity(root, args.source_sha)
    build = root / "build" / ("offline-" + args.profile)
    output = root / "build" / ("platform-artifacts-" + args.profile)
    # Do not reuse stale packages or a different compiler/configuration receipt.
    if build.exists() or output.exists():
        raise ValueError("fresh platform build and output directories required")
    output.mkdir(parents=True)
    (output / "SOURCE_COMMIT").write_text(args.source_sha + "\n", encoding="ascii")
    (output / "SOURCE_TREE").write_text(tree + "\n", encoding="ascii")
    flags = "/W4 /WX /permissive- /EHsc" if system == "Windows" else "-Wall -Wextra -Wpedantic -Werror"
    run(["cmake", "-S", "research", "-B", str(build), "-G", "Ninja",
         "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON", "-DHEPTA_RESEARCH_INSTALL_SDK=ON",
         "-DCMAKE_CXX_COMPILER=" + compiler, "-DCMAKE_CXX_FLAGS=" + flags], root)
    run(["cmake", "--build", str(build), "--parallel", "2"], root)
    run(["ctest", "--test-dir", str(build), "--no-tests=error", "--output-on-failure",
         "--output-junit", str(output / "results.xml")], root)
    cases = ET.parse(output / "results.xml").getroot().findall(".//testcase")
    names = [case.attrib["name"] for case in cases]
    required = set(REQUIRED_TESTS)
    if system != "Windows":
        required.add("hepta_research_legacy_bundle_behavior")
    if not required.issubset(names) or len(names) != len(set(names)):
        raise ValueError("missing/duplicate platform acceptance cases")
    if any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise ValueError("failed or skipped acceptance is not supported-platform evidence")
    stage = build / "sdk-stage"
    run(["cmake", "--install", str(build), "--prefix", str(stage), "--component", "ResearchSDK"], root)
    info = dict(line.split("=", 1) for line in
                (stage / "share/HeptaResearch/sdk-build-info.txt").read_text(encoding="utf-8").splitlines())
    for key, wanted in {"source_sha": args.source_sha, "source_tree_state": "clean",
                        "compiler_id": compiler_id, "components": "Data,Analytics,Replay,Strategy",
                        "native_gateway_or_broker_authority": "none"}.items():
        if info.get(key) != wanted:
            raise ValueError("unexpected SDK metadata: " + key)
    headers = sorted(path.name for path in stage.rglob("*.h"))
    suffix = ".lib" if system == "Windows" else ".a"
    archives = sorted(path.name for path in stage.rglob("*" + suffix))
    if headers != ["analytics.h", "market_data.h", "replay.h", "strategy.h"] or len(archives) != 4:
        raise ValueError("offline SDK header/archive boundary changed")
    if source_identity(root, args.source_sha) != tree:
        raise ValueError("source changed during acceptance")
    package = output / f"hepta-research-sdk-{args.profile}-{args.source_sha}.tar.gz"
    with tarfile.open(package, "w:gz") as archive:
        archive.add(stage, arcname=".")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (output / (package.name + ".sha256")).write_text(digest + "  " + package.name + "\n", encoding="ascii")
    receipt = {"schema": "hepta.offline-platform-acceptance.v1", "result": "PASS",
               "source_commit": args.source_sha, "source_tree": tree, "profile": args.profile,
               "host_system": system, "host_architecture": architecture, "host_version": platform.version(),
               "sdk_build_info": info, "test_cases": names, "failures": 0, "skipped": 0,
               "headers": headers, "archives": archives, "package": package.name, "sha256": digest,
               "scope": "offline source/installed SDK; not legacy ABI, native client, server, or broker qualification"}
    (output / "acceptance.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
