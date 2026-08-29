#!/usr/bin/env python3
"""Run one sealed CTest lane and publish raw output plus a sidecar."""

from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_heptatrader_delivery_closure as common  # noqa: E402
import build_heptatrader_verification_evidence as evidence  # noqa: E402


def _run(
    command: list[str],
    timeout: int,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        return subprocess.CompletedProcess(command, 124, output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--label",
        choices=sorted(evidence.MATRIX_LABELS | evidence.SANITIZER_LABELS),
        required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--sidecar", required=True)
    parser.add_argument("--cache-output", required=True)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--source-manifest-output")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    arguments = parser.parse_args()
    if arguments.expected_count <= 0 or arguments.timeout_seconds <= 0:
        raise evidence.EvidenceError("CTest count or timeout is invalid")
    root = evidence._protected_root(arguments.artifact_root)
    requested_outputs = {
        "stdout": evidence._relative(arguments.stdout),
        "inventory": evidence._relative(arguments.inventory),
        "sidecar": evidence._relative(arguments.sidecar),
        "cache": evidence._relative(arguments.cache_output),
    }
    if arguments.source_manifest_output is not None:
        requested_outputs["source"] = evidence._relative(
            arguments.source_manifest_output)
    if len(set(requested_outputs.values())) != len(requested_outputs):
        raise evidence.EvidenceError("CTest output paths are duplicated")
    for relative in requested_outputs.values():
        if os.path.lexists(root.joinpath(*Path(relative).parts)):
            raise evidence.EvidenceError(
                f"CTest output already exists: {relative}")
    build = evidence._live_directory(
        Path(os.path.abspath(arguments.build_dir)).as_posix(),
        f"{arguments.label} build")
    cache = build / "CMakeCache.txt"
    if not cache.is_file() or cache.is_symlink():
        raise evidence.EvidenceError("CTest build has no safe CMakeCache")
    try:
        cache_snapshot = common.stable_read(
            cache, limit=evidence.MAX_INPUT_BYTES, capture=True,
            require_trusted_parent=False)
    except common.DeliveryClosureError as error:
        raise evidence.EvidenceError("CMakeCache identity is unsafe") from error
    assert cache_snapshot.data is not None
    helper_path = Path(evidence.__file__).resolve(strict=True)
    expected_helper = (
        Path(__file__).resolve(strict=True).parents[1] /
        evidence.VERIFICATION_HELPER_SOURCE)
    if helper_path != expected_helper:
        raise evidence.EvidenceError(
            "verification helper import path drift")
    helper_snapshot = common.stable_read(
        helper_path, limit=evidence.MAX_INPUT_BYTES, capture=False,
        require_trusted_parent=False)
    cache_values = evidence._cache_values(
        cache_snapshot.data, arguments.label)
    evidence._validate_lane_cache(arguments.label, cache_values)
    if cache_values["CMAKE_CACHEFILE_DIR"] != build.as_posix():
        raise evidence.EvidenceError("CTest cache/build identity drift")
    source_manifest_relative = None
    source_tree_attestation = None
    source_snapshot = None
    if arguments.label in evidence.SOURCE_ATTESTATION_LABELS:
        if (arguments.source_manifest is None or
                arguments.source_manifest_output is None):
            raise evidence.EvidenceError(
                "CTest lane requires a source manifest attestation")
        manifest_path = Path(os.path.abspath(arguments.source_manifest))
        if manifest_path.resolve(strict=True) != manifest_path:
            raise evidence.EvidenceError(
                "no-Git source manifest contains a symlink")
        source_snapshot = common.stable_read(
            manifest_path, limit=evidence.MAX_INPUT_BYTES, capture=True,
            require_trusted_parent=True)
        assert source_snapshot.data is not None
        source_root = evidence._live_directory(
            cache_values["CMAKE_HOME_DIRECTORY"],
            f"{arguments.label} source")
        evidence._require_disjoint_source_build(
            source_root, build, arguments.label)
        agent_source = evidence._agent_source_label(arguments.label)
        source_tree_attestation = evidence.attest_source_tree(
            source_root, source_snapshot.data, arguments.label,
            agent_source=agent_source)
        source_manifest_relative = requested_outputs["source"]
    elif (arguments.source_manifest is not None or
          arguments.source_manifest_output is not None):
        raise evidence.EvidenceError(
            "CTest lane cannot claim an unsupported source manifest")
    ctest_path, ctest_before = evidence._configured_ctest(
        cache_values, arguments.label)
    executable = ctest_path.as_posix()
    host_arch = platform.machine()
    argv, inventory_argv, selection = evidence._expected_ctest_commands(
        arguments.label, executable, build.as_posix(), host_arch)
    environment = evidence.execution_environment(arguments.label)
    inventory_run = _run(
        inventory_argv, arguments.timeout_seconds, environment)
    if inventory_run.returncode != 0:
        raise evidence.EvidenceError("CTest inventory command failed")
    inventory_document, test_names = evidence._ctest_inventory_document(
        inventory_run.stdout, arguments.label)
    if len(test_names) != arguments.expected_count:
        raise evidence.EvidenceError("CTest inventory count is not expected")
    attestations = evidence.inventory_attestations(
        inventory_document, arguments.label)
    test_run = _run(argv, arguments.timeout_seconds, environment)
    cache_after = common.stable_read(
        cache, limit=evidence.MAX_INPUT_BYTES, capture=True,
        require_trusted_parent=False)
    if (cache_after.sha256 != cache_snapshot.sha256 or
            cache_after.size != cache_snapshot.size or
            cache_after.mode != cache_snapshot.mode):
        raise evidence.EvidenceError("CMakeCache changed during CTest")
    if source_snapshot is not None:
        source_after = evidence.attest_source_tree(
            Path(cache_values["CMAKE_HOME_DIRECTORY"]),
            source_snapshot.data, arguments.label,
            agent_source=evidence._agent_source_label(arguments.label))
        if source_after != source_tree_attestation:
            raise evidence.EvidenceError(
                "source changed during CTest")
    helper_after = common.stable_read(
        helper_path, limit=evidence.MAX_INPUT_BYTES, capture=False,
        require_trusted_parent=False)
    if (helper_after.sha256 != helper_snapshot.sha256 or
            helper_after.size != helper_snapshot.size or
            helper_after.mode != helper_snapshot.mode):
        raise evidence.EvidenceError(
            "verification helper changed during CTest")
    _ctest_path_after, ctest_after = evidence._configured_ctest(
        cache_values, arguments.label)
    if ctest_after != ctest_before:
        raise evidence.EvidenceError(
            "configured CTest changed during execution")
    inventory_relative = requested_outputs["inventory"]
    stdout_relative = requested_outputs["stdout"]
    sidecar_relative = requested_outputs["sidecar"]
    cache_relative = requested_outputs["cache"]
    runner_snapshot = common.stable_read(
        Path(__file__).resolve(strict=True),
        limit=evidence.MAX_INPUT_BYTES, capture=False,
        require_trusted_parent=False)
    evidence._write_private(root, Path(inventory_relative), inventory_run.stdout)
    evidence._write_private(root, Path(stdout_relative), test_run.stdout)
    evidence._write_private(root, Path(cache_relative), cache_snapshot.data)
    if source_snapshot is not None:
        assert source_manifest_relative is not None
        assert source_snapshot.data is not None
        evidence._write_private(
            root, Path(source_manifest_relative), source_snapshot.data)
    sidecar = {
        "schema": evidence.CTEST_SIDECAR_SCHEMA,
        "version": 2,
        "label": arguments.label,
        "runner_source_path": evidence.CTEST_RUNNER_SOURCE,
        "runner_sha256": runner_snapshot.sha256,
        "helper_source_path": evidence.VERIFICATION_HELPER_SOURCE,
        "helper_sha256": helper_snapshot.sha256,
        "ctest_path": executable,
        "ctest_sha256": ctest_before["sha256"],
        "build_directory": build.as_posix(),
        "host_arch": host_arch,
        "selection": selection,
        "environment": environment,
        "argv": argv,
        "inventory_argv": inventory_argv,
        "inventory_returncode": inventory_run.returncode,
        "returncode": test_run.returncode,
        "stdout_path": stdout_relative,
        "inventory_path": inventory_relative,
        "cache_path": cache_relative,
        "test_attestations": attestations,
        "source_manifest_path": source_manifest_relative,
        "source_tree_attestation": source_tree_attestation,
    }
    evidence._write_private(
        root, Path(sidecar_relative),
        common.canonical_json(sidecar) + b"\n")
    evidence._ctest_sidecar(
        root, arguments.label, arguments.expected_count, sidecar_relative)
    print(
        f"PASS: {evidence.CTEST_SIDECAR_SCHEMA} "
        f"label={arguments.label} tests={arguments.expected_count}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
            evidence.EvidenceError, common.DeliveryClosureError,
            OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
