#!/usr/bin/env python3
"""Run and seal the fixed native coverage lane and its raw gcov inputs."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile

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
        return subprocess.CompletedProcess(
            command, 124, error.stdout or b"")


def _raw_coverage(build: Path) -> tuple[dict[str, object], bytes]:
    records: list[dict[str, object]] = []
    captured: list[tuple[dict[str, object], bytes]] = []
    for path in sorted(
            (item for item in build.rglob("*")
             if item.suffix in {".gcda", ".gcno"}),
            key=lambda item: item.relative_to(build).as_posix()):
        relative = common.normalized_relative_path(
            path.relative_to(build).as_posix(), "coverage raw path")
        snapshot = common.stable_read(
            path, limit=evidence.MAX_INPUT_BYTES, capture=True,
            require_trusted_parent=False)
        assert snapshot.data is not None
        record: dict[str, object] = {
            "path": relative,
            "mode": snapshot.mode,
            "size": snapshot.size,
            "sha256": snapshot.sha256,
        }
        records.append(record)
        captured.append((record, snapshot.data))
    if not records:
        raise evidence.EvidenceError("coverage produced no gcda/gcno inputs")
    manifest = {
        "schema": "hepta.coverage-raw-inputs.v1",
        "version": 1,
        "build_directory": build.as_posix(),
        "file_count": len(records),
        "files_sha256": hashlib.sha256(
            common.canonical_json(records)).hexdigest(),
        "files": records,
    }
    payload = io.BytesIO()
    with tarfile.open(
            fileobj=payload, mode="w",
            format=tarfile.USTAR_FORMAT) as archive:
        for record, data in captured:
            info = tarfile.TarInfo(str(record["path"]))
            info.size = len(data)
            info.mode = int(str(record["mode"]), 8)
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return manifest, payload.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--strict-source-manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--coverage-stdout", required=True)
    parser.add_argument("--coverage-xml", required=True)
    parser.add_argument("--cache-output", required=True)
    parser.add_argument("--source-manifest-output", required=True)
    parser.add_argument("--policy-output", required=True)
    parser.add_argument("--raw-manifest", required=True)
    parser.add_argument("--raw-archive", required=True)
    parser.add_argument("--sidecar", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    arguments = parser.parse_args()
    if arguments.timeout_seconds <= 0:
        raise evidence.EvidenceError("coverage timeout is invalid")
    root = evidence._protected_root(arguments.artifact_root)
    requested_outputs = {
        "stdout_path": evidence._relative(arguments.stdout),
        "inventory_path": evidence._relative(arguments.inventory),
        "coverage_stdout_path": evidence._relative(
            arguments.coverage_stdout),
        "coverage_xml_path": evidence._relative(arguments.coverage_xml),
        "cache_path": evidence._relative(arguments.cache_output),
        "strict_source_manifest_path": evidence._relative(
            arguments.source_manifest_output),
        "policy_path": evidence._relative(arguments.policy_output),
        "raw_manifest_path": evidence._relative(arguments.raw_manifest),
        "raw_archive_path": evidence._relative(arguments.raw_archive),
        "sidecar_path": evidence._relative(arguments.sidecar),
    }
    if len(set(requested_outputs.values())) != len(requested_outputs):
        raise evidence.EvidenceError("coverage output paths are duplicated")
    for relative in requested_outputs.values():
        output = root.joinpath(*Path(relative).parts)
        if os.path.lexists(output):
            raise evidence.EvidenceError(
                f"coverage output already exists: {relative}")
    build = Path(os.path.abspath(arguments.build_dir))
    if build.resolve(strict=True) != build:
        raise evidence.EvidenceError("coverage build contains a symlink")
    cache = build / "CMakeCache.txt"
    if not cache.is_file() or cache.is_symlink():
        raise evidence.EvidenceError("coverage build has no safe CMakeCache")
    cache_snapshot = common.stable_read(
        cache, limit=evidence.MAX_INPUT_BYTES, capture=True,
        require_trusted_parent=False)
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
        cache_snapshot.data, "coverage")
    evidence._validate_lane_cache("coverage", cache_values)
    if cache_values["CMAKE_CACHEFILE_DIR"] != build.as_posix():
        raise evidence.EvidenceError("coverage cache/build identity drift")
    source = Path(cache_values["CMAKE_HOME_DIRECTORY"])
    if (not source.is_absolute() or
            source.resolve(strict=True) != source):
        raise evidence.EvidenceError("coverage source contains a symlink")
    strict_manifest = Path(os.path.abspath(
        arguments.strict_source_manifest))
    if strict_manifest.resolve(strict=True) != strict_manifest:
        raise evidence.EvidenceError(
            "coverage strict manifest contains a symlink")
    strict_snapshot = common.stable_read(
        strict_manifest, limit=evidence.MAX_INPUT_BYTES, capture=True,
        require_trusted_parent=True)
    assert strict_snapshot.data is not None
    strict_identity = evidence._strict_source_identity(
        strict_snapshot.data, "coverage")
    if source.name != strict_identity["root"]:
        raise evidence.EvidenceError(
            "coverage must execute from the strict source root")
    source_tree_attestation = evidence.attest_source_tree(
        source, strict_snapshot.data, "coverage")
    policy_source = source / "policies/heptatrader-code-quality-v1.json"
    policy_snapshot = common.stable_read(
        policy_source, limit=evidence.MAX_INPUT_BYTES, capture=True,
        require_trusted_parent=False)
    assert policy_snapshot.data is not None
    policy = evidence._strict_json(policy_snapshot.data, "coverage policy")
    minimum_percent = policy.get("coverage", {}).get(
        "line_minimum_percent")
    if type(minimum_percent) is not int or not 0 <= minimum_percent <= 100:
        raise evidence.EvidenceError("coverage policy floor is invalid")
    minimum_line_rate = minimum_percent / 100.0
    toolchain = evidence.coverage_toolchain_identity(policy)
    ctest, ctest_before = evidence._configured_ctest(
        cache_values, "coverage")
    python = Path(toolchain["python"]["configured_path"])
    gcov = Path(toolchain["gcov"]["configured_path"])
    xml_relative = requested_outputs["coverage_xml_path"]
    xml_path = root.joinpath(*Path(xml_relative).parts)
    ctest_argv, inventory_argv, coverage_argv = (
        evidence._expected_coverage_commands(
            python.as_posix(), gcov.as_posix(), ctest.as_posix(),
            build.as_posix(), source.as_posix(), xml_path,
            minimum_line_rate))
    environment = evidence.execution_environment("coverage")
    inventory_run = _run(
        inventory_argv, arguments.timeout_seconds, environment)
    if inventory_run.returncode != 0:
        raise evidence.EvidenceError("coverage CTest inventory failed")
    inventory_document, test_names = evidence._ctest_inventory_document(
        inventory_run.stdout, "coverage")
    attestations = evidence.inventory_attestations(
        inventory_document, "coverage")
    ctest_run = _run(ctest_argv, arguments.timeout_seconds, environment)
    coverage_run = _run(
        coverage_argv, arguments.timeout_seconds, environment)
    cache_after = common.stable_read(
        cache, limit=evidence.MAX_INPUT_BYTES, capture=True,
        require_trusted_parent=False)
    if (cache_after.sha256 != cache_snapshot.sha256 or
            cache_after.size != cache_snapshot.size or
            cache_after.mode != cache_snapshot.mode):
        raise evidence.EvidenceError(
            "coverage CMakeCache changed during execution")
    if (evidence.attest_source_tree(
            source, strict_snapshot.data, "coverage") !=
            source_tree_attestation):
        raise evidence.EvidenceError(
            "strict source changed during coverage execution")
    helper_after = common.stable_read(
        helper_path, limit=evidence.MAX_INPUT_BYTES, capture=False,
        require_trusted_parent=False)
    if (helper_after.sha256 != helper_snapshot.sha256 or
            helper_after.size != helper_snapshot.size or
            helper_after.mode != helper_snapshot.mode):
        raise evidence.EvidenceError(
            "verification helper changed during coverage")
    _ctest_path_after, ctest_after = evidence._configured_ctest(
        cache_values, "coverage")
    if ctest_after != ctest_before:
        raise evidence.EvidenceError(
            "configured CTest changed during coverage")
    if evidence.coverage_toolchain_identity(policy) != toolchain:
        raise evidence.EvidenceError(
            "coverage toolchain changed during execution")
    if not xml_path.is_file() or xml_path.is_symlink():
        raise evidence.EvidenceError("gcovr did not produce safe XML")
    raw_manifest, raw_archive = _raw_coverage(build)
    evidence._verify_raw_coverage(
        common.canonical_json(raw_manifest),
        raw_archive,
        build,
    )
    runner_snapshot = common.stable_read(
        Path(__file__).resolve(strict=True),
        limit=evidence.MAX_INPUT_BYTES, capture=False,
        require_trusted_parent=False)
    outputs = {
        key: value for key, value in requested_outputs.items()
        if key not in {"coverage_xml_path", "sidecar_path"}
    }
    evidence._write_private(
        root, Path(outputs["stdout_path"]), ctest_run.stdout)
    evidence._write_private(
        root, Path(outputs["inventory_path"]), inventory_run.stdout)
    evidence._write_private(
        root, Path(outputs["coverage_stdout_path"]), coverage_run.stdout)
    evidence._write_private(
        root, Path(outputs["cache_path"]), cache_snapshot.data)
    evidence._write_private(
        root, Path(outputs["strict_source_manifest_path"]),
        strict_snapshot.data)
    evidence._write_private(
        root, Path(outputs["policy_path"]), policy_snapshot.data)
    evidence._write_private(
        root, Path(outputs["raw_manifest_path"]),
        common.canonical_json(raw_manifest) + b"\n")
    evidence._write_private(
        root, Path(outputs["raw_archive_path"]), raw_archive)
    sidecar = {
        "schema": evidence.COVERAGE_SIDECAR_SCHEMA,
        "version": 2,
        "label": "coverage",
        "runner_source_path": evidence.COVERAGE_RUNNER_SOURCE,
        "runner_sha256": runner_snapshot.sha256,
        "helper_source_path": evidence.VERIFICATION_HELPER_SOURCE,
        "helper_sha256": helper_snapshot.sha256,
        "ctest_path": ctest.as_posix(),
        "ctest_sha256": ctest_before["sha256"],
        "toolchain_identity": toolchain,
        "build_directory": build.as_posix(),
        "source_directory": source.as_posix(),
        "selection": ["-E", evidence.COVERAGE_EXCLUDE],
        "environment": environment,
        "ctest_argv": ctest_argv,
        "inventory_argv": inventory_argv,
        "coverage_argv": coverage_argv,
        "inventory_returncode": inventory_run.returncode,
        "ctest_returncode": ctest_run.returncode,
        "coverage_returncode": coverage_run.returncode,
        "expected_count": len(test_names),
        "minimum_line_rate": minimum_line_rate,
        "coverage_xml_path": xml_relative,
        "test_attestations": attestations,
        "source_tree_attestation": source_tree_attestation,
        **outputs,
    }
    sidecar_relative = requested_outputs["sidecar_path"]
    evidence._write_private(
        root, Path(sidecar_relative),
        common.canonical_json(sidecar) + b"\n")
    evidence.build_coverage(
        root, sidecar_relative, minimum_line_rate,
        "1970-01-01T00:00:00Z")
    print(
        f"PASS: {evidence.COVERAGE_SIDECAR_SCHEMA} "
        f"tests={len(test_names)} raw_files={raw_manifest['file_count']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
            evidence.EvidenceError, common.DeliveryClosureError,
            OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
