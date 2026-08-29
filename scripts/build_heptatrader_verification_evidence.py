#!/usr/bin/env python3
"""Bind test, sanitizer, coverage, and runner evidence without authorizing trade."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ElementTree
from typing import Any

sys.dont_write_bytecode = True

SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import build_heptatrader_delivery_closure as common  # noqa: E402


SCHEMA = "hepta.verification-evidence.v2"
CTEST_SIDECAR_SCHEMA = "hepta.ctest-run-sidecar.v2"
COVERAGE_SIDECAR_SCHEMA = "hepta.coverage-run-sidecar.v2"
COVERAGE_TOOLCHAIN_SCHEMA = "hepta.coverage-toolchain.v1"
COVERAGE_TOOLCHAIN_DISTRIBUTIONS = (
    ("colorlog", "6.12.0"),
    ("gcovr", "7.2"),
    ("Jinja2", "3.1.6"),
    ("lxml", "6.1.1"),
    ("MarkupSafe", "3.0.3"),
    ("Pygments", "2.20.0"),
)
CTEST_RUNNER_SOURCE = "scripts/run_heptatrader_ctest_evidence.py"
COVERAGE_RUNNER_SOURCE = "scripts/run_heptatrader_coverage_evidence.py"
VERIFICATION_HELPER_SOURCE = \
    "scripts/build_heptatrader_verification_evidence.py"
LABEL = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
CTEST_PASS = re.compile(
    r"100% tests passed, 0 tests failed out of ([0-9]+)")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
MAX_INPUT_BYTES = 512 * 1024 * 1024
SHORT_SOAK_TEST = "hepta_execution_gateway_short_soak"
SHORT_SOAK_REPORT = "execution-gateway-short-soak.json"
MATRIX_LABELS = frozenset({
    "repository-ibapi-off",
    "repository-ibapi-on",
    "agent-no-git-ibapi-off",
    "agent-no-git-ibapi-on",
})
SANITIZER_LABELS = frozenset({"asan", "ubsan", "tsan"})
RUNNER_LABELS = frozenset({
    *MATRIX_LABELS,
    *SANITIZER_LABELS,
    "coverage",
})
NO_GIT_LABELS = frozenset({
    "agent-no-git-ibapi-off",
    "agent-no-git-ibapi-on",
})
STRICT_SOURCE_LABELS = SANITIZER_LABELS
SOURCE_ATTESTATION_LABELS = frozenset({
    *NO_GIT_LABELS,
    *STRICT_SOURCE_LABELS,
})
AGENT_SOURCE_INTERNAL_MANIFEST = \
    ".hepta/agent-os-source-manifest.json"
STRICT_SOURCE_INTERNAL_MANIFEST = \
    ".hepta/source-bundle-manifest.json"
IBAPI_ON_LABELS = frozenset({
    "repository-ibapi-on",
    "agent-no-git-ibapi-on",
})
LEGACY_CACHE_KEYS = (
    "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE",
    "HEPTA_BUILD_LEGACY_MONOLITH",
    "HEPTA_BUILD_LEGACY_SIMULATOR",
)
SANITIZER_FLAGS = {
    "asan": "-fsanitize=address",
    "ubsan": "-fsanitize=undefined",
    "tsan": "-fsanitize=thread",
}
COVERAGE_EXCLUDE = "hepta_agent_os_sdk_install_tree_tests"
BOUNDARY = {
    "broker_connection_performed": False,
    "live_authorized": False,
    "order_placement_performed": False,
    "paper_authorized": False,
}


class EvidenceError(RuntimeError):
    """Verification evidence is missing, unsafe, or does not pass."""


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and common.HEX64.fullmatch(value) is not None


def _is_hex40(value: Any) -> bool:
    return isinstance(value, str) and HEX40.fullmatch(value) is not None


def execution_environment(label: str) -> dict[str, str]:
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if label == "asan":
        environment["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1"
    elif label == "ubsan":
        environment["UBSAN_OPTIONS"] = "print_stacktrace=1:halt_on_error=1"
    elif label == "tsan":
        environment["TSAN_OPTIONS"] = "halt_on_error=1"
    return environment


def _distribution_identity(name: str) -> dict[str, Any]:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError as error:
        raise EvidenceError(
            f"coverage distribution is unavailable: {name}") from error
    root = Path(distribution.locate_file("")).resolve(strict=True)
    install_root = (
        root.parents[2] if len(root.parents) > 2 else root)
    records: list[dict[str, Any]] = []
    for entry in sorted(distribution.files or [], key=str):
        relative = str(entry)
        parts = Path(relative).parts
        if (not relative or Path(relative).is_absolute() or
                any(part in {"", "."} for part in parts)):
            raise EvidenceError(
                f"coverage distribution path is invalid: {name}")
        if "__pycache__" in parts or relative.endswith(".pyc"):
            continue
        path = Path(distribution.locate_file(entry)).resolve(strict=True)
        if (".." in parts and
                path != install_root and install_root not in path.parents):
            raise EvidenceError(
                f"coverage distribution path escapes tool root: {name}")
        if (".." not in parts and
                path != root and root not in path.parents):
            raise EvidenceError(
                f"coverage distribution path escapes package root: {name}")
        try:
            snapshot = common.stable_read(
                path, limit=MAX_INPUT_BYTES, capture=False,
                require_trusted_parent=False)
        except (OSError, common.DeliveryClosureError) as error:
            raise EvidenceError(
                f"coverage distribution file is unsafe: {name}") from error
        records.append({
            "path": relative,
            "sha256": snapshot.sha256,
            "size": snapshot.size,
            "mode": snapshot.mode,
        })
    if not records:
        raise EvidenceError(
            f"coverage distribution has no attestable files: {name}")
    return {
        "name": name,
        "version": distribution.version,
        "root": root.as_posix(),
        "file_count": len(records),
        "files_sha256": hashlib.sha256(
            common.canonical_json(records)).hexdigest(),
    }


def _tool_root_identity(
    root: Path,
    receipt_path: Path,
    *,
    require_root_owned: bool = True,
) -> dict[str, Any]:
    lexical = Path(os.path.abspath(root))
    try:
        metadata = lexical.lstat()
        resolved = lexical.resolve(strict=True)
    except OSError as error:
        raise EvidenceError(
            "coverage tool root is unavailable") from error
    if (resolved != lexical or not stat.S_ISDIR(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) & 0o022 or
            stat.S_IMODE(metadata.st_mode) & 0o7000 or
            (require_root_owned and
             (metadata.st_uid != 0 or metadata.st_gid != 0))):
        raise EvidenceError("coverage tool root is unsafe")
    if require_root_owned:
        for ancestor in (lexical, *lexical.parents):
            ancestor_metadata = ancestor.lstat()
            if (not stat.S_ISDIR(ancestor_metadata.st_mode) or
                    ancestor_metadata.st_uid != 0 or
                    ancestor_metadata.st_gid != 0 or
                    stat.S_IMODE(ancestor_metadata.st_mode) & 0o022 or
                    stat.S_IMODE(ancestor_metadata.st_mode) & 0o7000):
                raise EvidenceError(
                    "coverage tool-root parent chain is not root-owned")
    records: list[dict[str, Any]] = []
    total_size = 0
    for directory, directory_names, file_names in os.walk(
            lexical, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in directory_names:
            path = directory_path / name
            child = path.lstat()
            if (not stat.S_ISDIR(child.st_mode) or
                    stat.S_IMODE(child.st_mode) & 0o022 or
                    stat.S_IMODE(child.st_mode) & 0o7000 or
                    (require_root_owned and
                     (child.st_uid != 0 or child.st_gid != 0))):
                raise EvidenceError(
                    "coverage tool root contains an unsafe directory")
        for name in file_names:
            path = directory_path / name
            if path == receipt_path:
                continue
            try:
                before = path.lstat()
                snapshot = common.stable_read(
                    path, limit=MAX_INPUT_BYTES, capture=False,
                    require_trusted_parent=False)
                after = path.lstat()
            except (OSError, common.DeliveryClosureError) as error:
                raise EvidenceError(
                    "coverage tool root contains an unsafe file") from error
            identity = lambda value: (
                value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
                value.st_uid, value.st_gid, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns,
            )
            if (identity(before) != identity(after) or
                    (require_root_owned and
                     (before.st_uid != 0 or before.st_gid != 0))):
                raise EvidenceError(
                    "coverage tool-root file ownership drift")
            total_size += snapshot.size
            if len(records) >= 100_000 or total_size > 8 * 1024**3:
                raise EvidenceError(
                    "coverage tool root exceeds attestation bounds")
            records.append({
                "path": path.relative_to(lexical).as_posix(),
                "mode": snapshot.mode,
                "uid": before.st_uid,
                "gid": before.st_gid,
                "size": snapshot.size,
                "sha256": snapshot.sha256,
            })
    records.sort(key=lambda item: item["path"])
    if not records:
        raise EvidenceError("coverage tool root is empty")
    return {
        "root": lexical.as_posix(),
        "file_count": len(records),
        "files_sha256": hashlib.sha256(
            common.canonical_json(records)).hexdigest(),
    }


def _coverage_toolchain_contract(
    policy: dict[str, Any],
) -> dict[str, Any]:
    coverage = policy.get("coverage")
    if not isinstance(coverage, dict):
        raise EvidenceError("coverage policy is invalid")
    value = coverage.get("toolchain")
    expected_fields = {
        "schema", "version", "provisioned", "runner_labels",
        "python", "gcov", "immutable_tool_root_receipt",
        "distributions",
    }
    executable_fields = {
        "configured_path", "realpath", "sha256", "size", "mode",
    }
    distribution_fields = {
        "name", "version", "root", "file_count", "files_sha256",
    }
    if (not isinstance(value, dict) or set(value) != expected_fields or
            value["schema"] != COVERAGE_TOOLCHAIN_SCHEMA or
            value["version"] != 1 or
            type(value["provisioned"]) is not bool or
            value["runner_labels"] != [
                "self-hosted", "linux", "x64",
                "heptatrader-coverage-v1",
            ] or
            any(not isinstance(value[field], dict) or
                set(value[field]) != executable_fields
                for field in (
                    "python", "gcov",
                    "immutable_tool_root_receipt")) or
            not isinstance(value["distributions"], list) or
            len(value["distributions"]) !=
            len(COVERAGE_TOOLCHAIN_DISTRIBUTIONS)):
        raise EvidenceError("coverage toolchain policy is invalid")
    for label in ("python", "gcov", "immutable_tool_root_receipt"):
        executable = value[label]
        configured = executable["configured_path"]
        if (not isinstance(configured, str) or not configured or
                "\0" in configured or not Path(configured).is_absolute()):
            raise EvidenceError(
                f"coverage {label} policy path is invalid")
    for record, (expected_name, expected_version) in zip(
            value["distributions"],
            COVERAGE_TOOLCHAIN_DISTRIBUTIONS,
            strict=True):
        if (not isinstance(record, dict) or
                set(record) != distribution_fields or
                record["name"] != expected_name or
                record["version"] != expected_version):
            raise EvidenceError(
                "coverage distribution policy is invalid")
    identity_fields = ("realpath", "sha256", "size", "mode")
    distribution_identity_fields = (
        "root", "file_count", "files_sha256")
    if not value["provisioned"]:
        if (any(value[label][field] is not None
                for label in (
                    "python", "gcov",
                    "immutable_tool_root_receipt")
                for field in identity_fields) or
                any(record[field] is not None
                    for record in value["distributions"]
                    for field in distribution_identity_fields)):
            raise EvidenceError(
                "unprovisioned coverage toolchain must not claim identities")
        return value
    for label in ("python", "gcov"):
        executable = value[label]
        if (not isinstance(executable["realpath"], str) or
                not Path(executable["realpath"]).is_absolute() or
                not _is_hex64(executable["sha256"]) or
                type(executable["size"]) is not int or
                executable["size"] <= 0 or
                not isinstance(executable["mode"], str) or
                re.fullmatch(r"0[0-7]{3}", executable["mode"]) is None or
                int(executable["mode"], 8) & 0o022 or
                int(executable["mode"], 8) & 0o7000 or
                not int(executable["mode"], 8) & 0o111):
            raise EvidenceError(
                f"coverage {label} policy identity is invalid")
    receipt = value["immutable_tool_root_receipt"]
    if (not isinstance(receipt["realpath"], str) or
            not Path(receipt["realpath"]).is_absolute() or
            not _is_hex64(receipt["sha256"]) or
            type(receipt["size"]) is not int or
            receipt["size"] <= 0 or
            not isinstance(receipt["mode"], str) or
            re.fullmatch(r"0[0-7]{3}", receipt["mode"]) is None or
            int(receipt["mode"], 8) & 0o022 or
            int(receipt["mode"], 8) & 0o7000):
        raise EvidenceError(
            "coverage immutable tool-root receipt identity is invalid")
    for record in value["distributions"]:
        if (not isinstance(record["root"], str) or
                not Path(record["root"]).is_absolute() or
                type(record["file_count"]) is not int or
                record["file_count"] <= 0 or
                not _is_hex64(record["files_sha256"])):
            raise EvidenceError(
                "coverage distribution policy identity is invalid")
    return value


def coverage_toolchain_identity(
    policy: dict[str, Any],
) -> dict[str, Any]:
    contract = _coverage_toolchain_contract(policy)
    if not contract["provisioned"]:
        raise EvidenceError(
            "coverage toolchain is not provisioned; "
            "a reviewed controlled-runner identity is required")
    python = _regular_file_attestation(
        contract["python"]["configured_path"], "coverage Python")
    gcov = _regular_file_attestation(
        contract["gcov"]["configured_path"], "coverage gcov")
    receipt = _regular_file_attestation(
        contract["immutable_tool_root_receipt"]["configured_path"],
        "coverage immutable tool-root receipt")
    receipt_metadata = Path(receipt["realpath"]).lstat()
    if (python != contract["python"] or gcov != contract["gcov"] or
            receipt != contract["immutable_tool_root_receipt"] or
            not os.access(Path(python["realpath"]), os.X_OK) or
            not os.access(Path(gcov["realpath"]), os.X_OK) or
            receipt_metadata.st_uid != 0 or
            receipt_metadata.st_gid != 0):
        raise EvidenceError("coverage executable policy identity drift")
    try:
        receipt_snapshot = common.stable_read(
            receipt["realpath"], limit=MAX_INPUT_BYTES, capture=True,
            require_trusted_parent=False)
    except common.DeliveryClosureError as error:
        raise EvidenceError(
            "coverage immutable tool-root receipt is unsafe") from error
    assert receipt_snapshot.data is not None
    receipt_document = _strict_json(
        receipt_snapshot.data, "coverage immutable tool-root receipt")
    if (set(receipt_document) != {
            "schema", "version", "tool_root", "tool_root_file_count",
            "tool_root_tree_sha256",
            "controlled_runner_image_digest"} or
            receipt_document["schema"] !=
            "hepta.coverage-tool-root-receipt.v1" or
            receipt_document["version"] != 1 or
            not isinstance(receipt_document["tool_root"], str) or
            not Path(receipt_document["tool_root"]).is_absolute() or
            type(receipt_document["tool_root_file_count"]) is not int or
            receipt_document["tool_root_file_count"] <= 0 or
            not isinstance(
                receipt_document["controlled_runner_image_digest"], str) or
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                receipt_document[
                    "controlled_runner_image_digest"]) is None or
            not _is_hex64(receipt_document["tool_root_tree_sha256"])):
        raise EvidenceError(
            "coverage immutable tool-root receipt is invalid")
    tool_root = _tool_root_identity(
        Path(receipt_document["tool_root"]),
        Path(receipt["realpath"]))
    if (tool_root["root"] != receipt_document["tool_root"] or
            tool_root["file_count"] !=
            receipt_document["tool_root_file_count"] or
            tool_root["files_sha256"] !=
            receipt_document["tool_root_tree_sha256"]):
        raise EvidenceError(
            "coverage tool-root tree identity drift")
    if (Path(python["configured_path"]) != Path(tool_root["root"]) and
            Path(tool_root["root"]) not in
            Path(python["configured_path"]).parents):
        raise EvidenceError("coverage Python escapes the tool root")
    if (Path(gcov["configured_path"]) != Path(tool_root["root"]) and
            Path(tool_root["root"]) not in
            Path(gcov["configured_path"]).parents):
        raise EvidenceError("coverage gcov escapes the tool root")
    configured_python = Path(os.path.abspath(sys.executable)).as_posix()
    if configured_python != python["configured_path"]:
        raise EvidenceError(
            "coverage runner is not using the policy Python")
    distributions = [
        _distribution_identity(name)
        for name, _version in COVERAGE_TOOLCHAIN_DISTRIBUTIONS
    ]
    if distributions != contract["distributions"]:
        raise EvidenceError("coverage distribution policy identity drift")
    for distribution in distributions:
        distribution_root = Path(distribution["root"])
        if (distribution_root != Path(tool_root["root"]) and
                Path(tool_root["root"]) not in
                distribution_root.parents):
            raise EvidenceError(
                "coverage distribution escapes the tool root")
    return {
        "schema": COVERAGE_TOOLCHAIN_SCHEMA,
        "version": 1,
        "python": python,
        "gcov": gcov,
        "immutable_tool_root_receipt": receipt,
        "immutable_tool_root_claims": receipt_document,
        "tool_root_identity": tool_root,
        "distributions": distributions,
    }


def check_coverage_toolchain_policy(path: Path) -> dict[str, Any]:
    try:
        snapshot = common.stable_read(
            path.resolve(strict=True), limit=MAX_INPUT_BYTES, capture=True,
            require_trusted_parent=False)
    except (OSError, common.DeliveryClosureError) as error:
        raise EvidenceError(
            "coverage toolchain policy is unavailable") from error
    assert snapshot.data is not None
    policy = _strict_json(snapshot.data, "coverage toolchain policy")
    return coverage_toolchain_identity(policy)


def _validate_coverage_toolchain_identity(
    value: Any,
) -> dict[str, Any]:
    if (not isinstance(value, dict) or
            set(value) != {
                "schema", "version", "python", "gcov",
                "immutable_tool_root_receipt",
                "immutable_tool_root_claims", "tool_root_identity",
                "distributions",
            }):
        raise EvidenceError("coverage toolchain identity is invalid")
    _coverage_toolchain_contract({
        "coverage": {
            "toolchain": {
                "schema": value["schema"],
                "version": value["version"],
                "provisioned": True,
                "runner_labels": [
                    "self-hosted", "linux", "x64",
                    "heptatrader-coverage-v1",
                ],
                "python": value["python"],
                "gcov": value["gcov"],
                "immutable_tool_root_receipt":
                    value["immutable_tool_root_receipt"],
                "distributions": value["distributions"],
            },
        },
    })
    claims = value["immutable_tool_root_claims"]
    if (not isinstance(claims, dict) or set(claims) != {
            "schema", "version", "tool_root", "tool_root_file_count",
            "tool_root_tree_sha256",
            "controlled_runner_image_digest"} or
            claims["schema"] !=
            "hepta.coverage-tool-root-receipt.v1" or
            claims["version"] != 1 or
            not isinstance(claims["tool_root"], str) or
            not Path(claims["tool_root"]).is_absolute() or
            type(claims["tool_root_file_count"]) is not int or
            claims["tool_root_file_count"] <= 0 or
            not isinstance(
                claims["controlled_runner_image_digest"], str) or
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                claims["controlled_runner_image_digest"]) is None or
            not _is_hex64(claims["tool_root_tree_sha256"])):
        raise EvidenceError(
            "coverage immutable tool-root claims are invalid")
    tool_root = value["tool_root_identity"]
    if (not isinstance(tool_root, dict) or
            set(tool_root) != {"root", "file_count", "files_sha256"} or
            tool_root["root"] != claims["tool_root"] or
            tool_root["file_count"] != claims["tool_root_file_count"] or
            tool_root["files_sha256"] !=
            claims["tool_root_tree_sha256"]):
        raise EvidenceError("coverage tool-root identity is invalid")
    return value


def _relative(value: str) -> str:
    try:
        return common.normalized_relative_path(value, "evidence path")
    except common.DeliveryClosureError as error:
        raise EvidenceError(f"unsafe evidence path: {value}") from error


def _protected_root(root: Path) -> Path:
    lexical = Path(os.path.abspath(root))
    resolved = lexical.resolve(strict=True)
    if resolved != lexical:
        raise EvidenceError("artifact root contains a symlink")
    metadata = resolved.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) & 0o022 or
            stat.S_IMODE(metadata.st_mode) & 0o7000):
        raise EvidenceError("artifact root is not a protected directory")
    return resolved


def _binding(root: Path, name: str, relative: str) -> dict[str, Any]:
    if LABEL.fullmatch(name) is None:
        raise EvidenceError(f"invalid evidence label: {name}")
    relative = _relative(relative)
    path = root.joinpath(*PurePosixPath(relative).parts)
    try:
        snapshot = common.stable_read(
            path, limit=MAX_INPUT_BYTES, capture=True,
            require_trusted_parent=True)
    except common.DeliveryClosureError as error:
        raise EvidenceError(f"unsafe evidence input: {relative}") from error
    assert snapshot.data is not None
    data = snapshot.data
    return {
        "name": name,
        "path": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "mode": snapshot.mode,
        "_data": data,
    }


def _parse_named_path(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise EvidenceError("evidence input must be LABEL=PATH")
    name, relative = value.split("=", 1)
    return name, _relative(relative)


def _parse_ctest(value: str) -> tuple[str, int, str]:
    parts = value.split("=", 2)
    if len(parts) != 3:
        raise EvidenceError("CTest evidence must be LABEL=EXPECTED=PATH")
    name, expected_text, relative = parts
    if LABEL.fullmatch(name) is None:
        raise EvidenceError(f"invalid CTest label: {name}")
    try:
        expected = int(expected_text)
    except ValueError as error:
        raise EvidenceError("CTest expected count must be an integer") from error
    if expected <= 0:
        raise EvidenceError("CTest expected count must be positive")
    return name, expected, _relative(relative)


def _base(kind: str, generated_at: str) -> dict[str, Any]:
    try:
        generated_at = common._normalize_generated_at(generated_at)
    except common.DeliveryClosureError as error:
        raise EvidenceError("generated_at is not normalized UTC RFC3339") from error
    return {
        "schema": SCHEMA,
        "version": 2,
        "kind": kind,
        "generated_at": generated_at,
        "passed": True,
        "cases": [],
        "inputs": [],
        "boundary": dict(BOUNDARY),
    }


def _without_data(binding: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in binding.items() if key != "_data"}


def _strict_json(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = common.strict_json(data, label)
    except common.DeliveryClosureError as error:
        raise EvidenceError(str(error)) from error
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} root is not an object")
    return value


def _ctest_inventory_document(
    data: bytes, label: str,
) -> tuple[dict[str, Any], list[str]]:
    value = _strict_json(data, f"{label} CTest inventory")
    if (value.get("kind") != "ctestInfo" or
            not isinstance(value.get("version"), dict) or
            not isinstance(value.get("tests"), list)):
        raise EvidenceError(f"{label} CTest inventory is invalid")
    names: list[str] = []
    for record in value["tests"]:
        if (not isinstance(record, dict) or
                not isinstance(record.get("name"), str) or
                not record["name"] or
                not isinstance(record.get("command"), list) or
                not record["command"] or
                not all(isinstance(item, str) and item and "\0" not in item
                        for item in record["command"])):
            raise EvidenceError(f"{label} CTest inventory has an invalid test")
        names.append(record["name"])
    if len(names) != len(set(names)):
        raise EvidenceError(f"{label} CTest inventory has duplicate tests")
    return value, sorted(names)


def _ctest_inventory(data: bytes, label: str) -> list[str]:
    return _ctest_inventory_document(data, label)[1]


def _regular_file_attestation(path_text: str, label: str) -> dict[str, Any]:
    if "\0" in path_text or not Path(path_text).is_absolute():
        raise EvidenceError(f"{label} attested path is invalid")
    try:
        configured = Path(path_text)
        real = configured.resolve(strict=True)
        snapshot = common.stable_read(
            real, limit=512 * 1024 * 1024, capture=False,
            require_trusted_parent=False)
    except (OSError, common.DeliveryClosureError) as error:
        raise EvidenceError(f"{label} attested file is unsafe") from error
    return {
        "configured_path": configured.as_posix(),
        "realpath": real.as_posix(),
        "sha256": snapshot.sha256,
        "size": snapshot.size,
        "mode": snapshot.mode,
    }


def _working_directory(record: dict[str, Any]) -> str | None:
    properties = record.get("properties", [])
    if not isinstance(properties, list):
        raise EvidenceError("CTest inventory properties are invalid")
    values = [
        item.get("value")
        for item in properties
        if isinstance(item, dict) and
        item.get("name") == "WORKING_DIRECTORY"
    ]
    if len(values) > 1 or (
            values and (
                not isinstance(values[0], str) or
                not values[0] or "\0" in values[0])):
        raise EvidenceError("CTest working-directory identity is invalid")
    return values[0] if values else None


def _declared_ctest_outputs(
    record: dict[str, Any],
    command: list[str],
    label: str,
) -> tuple[set[int], list[dict[str, str]]]:
    if record["name"] != SHORT_SOAK_TEST:
        return set(), []
    option_indexes: dict[str, int] = {}
    for option in ("--build-dir", "--report"):
        matches = [
            index for index, argument in enumerate(command)
            if argument == option
        ]
        if (len(matches) != 1 or matches[0] + 1 >= len(command)):
            raise EvidenceError(
                f"{label} short-soak output contract is invalid")
        option_indexes[option] = matches[0]
    build = Path(command[option_indexes["--build-dir"] + 1])
    report = Path(command[option_indexes["--report"] + 1])
    if (not build.is_absolute() or not report.is_absolute() or
            report != build / SHORT_SOAK_REPORT):
        raise EvidenceError(
            f"{label} short-soak output contract is invalid")
    return {
        option_indexes["--report"] + 1,
    }, [{
        "option": "--report",
        "configured_path": report.as_posix(),
    }]


def inventory_attestations(
    document: dict[str, Any], label: str,
) -> list[dict[str, Any]]:
    attestations: list[dict[str, Any]] = []
    for record in sorted(document["tests"], key=lambda item: item["name"]):
        command = list(record["command"])
        working_directory = _working_directory(record)
        output_indexes, outputs = _declared_ctest_outputs(
            record, command, label)
        files: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, argument in enumerate(command):
            if index in output_indexes:
                continue
            candidate = Path(argument)
            suffix = candidate.suffix.lower()
            must_attest = (
                index == 0 or suffix in {
                    ".py", ".sh", ".ps1", ".cmake", ".pl", ".rb"})
            if not candidate.is_absolute() and must_attest:
                if working_directory is None:
                    raise EvidenceError(
                        f"{label} relative CTest executable/script "
                        "has no working directory")
                candidate = Path(working_directory) / candidate
            if not candidate.is_absolute():
                continue
            try:
                if (not candidate.exists() or
                        not candidate.resolve(strict=True).is_file()):
                    if must_attest:
                        raise EvidenceError(
                            f"{label} CTest executable/script is absent: "
                            f"{argument}")
                    continue
            except OSError as error:
                raise EvidenceError(
                    f"{label} CTest command path is unsafe") from error
            attestation = _regular_file_attestation(
                candidate.as_posix(), f"{label} CTest command")
            if attestation["configured_path"] in seen:
                continue
            seen.add(attestation["configured_path"])
            files.append(attestation)
        command_path = Path(command[0])
        if not command_path.is_absolute() and working_directory is not None:
            command_path = Path(working_directory) / command_path
        if not any(item["configured_path"] == command_path.as_posix()
                   for item in files):
            raise EvidenceError(
                f"{label} CTest executable/source is not attestable")
        attestations.append({
            "name": record["name"],
            "command": command,
            "working_directory": working_directory,
            "files": files,
            "outputs": outputs,
        })
    return attestations


def _validate_attestations(
    document: dict[str, Any],
    claimed: Any,
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(claimed, list):
        raise EvidenceError(f"{label} CTest attestations are invalid")
    observed = inventory_attestations(document, label)
    if claimed != observed:
        raise EvidenceError(f"{label} CTest attestation drift")
    return observed


def _live_directory(path_text: Any, label: str) -> Path:
    if (not isinstance(path_text, str) or not path_text or
            "\0" in path_text or not Path(path_text).is_absolute()):
        raise EvidenceError(f"{label} directory identity is invalid")
    lexical = Path(os.path.abspath(path_text))
    try:
        resolved = lexical.resolve(strict=True)
        metadata = lexical.lstat()
    except OSError as error:
        raise EvidenceError(f"{label} directory is unavailable") from error
    if (resolved != lexical or not stat.S_ISDIR(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) & 0o022 or
            stat.S_IMODE(metadata.st_mode) & 0o7000):
        raise EvidenceError(f"{label} directory is unsafe")
    return lexical


def _require_disjoint_source_build(
    source: Path, build: Path, label: str,
) -> None:
    if (source == build or source in build.parents or
            build in source.parents):
        raise EvidenceError(
            f"{label} CMake source/build directories overlap")


def _agent_source_label(label: str) -> bool:
    if label in NO_GIT_LABELS:
        return True
    if label in STRICT_SOURCE_LABELS:
        return False
    raise EvidenceError(
        f"{label} does not support a source manifest attestation")


def _live_cache_matches(
    build: Path, binding: dict[str, Any], label: str,
) -> None:
    try:
        snapshot = common.stable_read(
            build / "CMakeCache.txt", limit=MAX_INPUT_BYTES, capture=False,
            require_trusted_parent=False)
    except common.DeliveryClosureError as error:
        raise EvidenceError(f"{label} live CMakeCache is unsafe") from error
    if (snapshot.sha256 != binding["sha256"] or
            snapshot.size != binding["size"] or
            snapshot.mode != binding["mode"]):
        raise EvidenceError(f"{label} live CMakeCache drift")


def _inventory_execution_identity(
    document: dict[str, Any], label: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in sorted(document["tests"], key=lambda item: item["name"]):
        result.append({
            "name": record["name"],
            "command": record["command"],
            "working_directory": _working_directory(record),
        })
    if len(result) != len({item["name"] for item in result}):
        raise EvidenceError(f"{label} CTest inventory has duplicate tests")
    return result


def _replay_ctest_inventory(
    command: list[str],
    environment: dict[str, str],
    stored_document: dict[str, Any],
    stored_attestations: list[dict[str, Any]],
    label: str,
) -> list[str]:
    try:
        replay = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EvidenceError(
            f"{label} CTest inventory replay failed") from error
    if replay.returncode != 0:
        raise EvidenceError(f"{label} CTest inventory replay failed")
    replay_document, replay_names = _ctest_inventory_document(
        replay.stdout, f"{label} replay")
    if (_inventory_execution_identity(replay_document, label) !=
            _inventory_execution_identity(stored_document, label)):
        raise EvidenceError(f"{label} CTest inventory replay drift")
    replay_attestations = inventory_attestations(
        replay_document, f"{label} replay")
    if replay_attestations != stored_attestations:
        raise EvidenceError(f"{label} CTest inventory attestation replay drift")
    if inventory_attestations(stored_document, label) != stored_attestations:
        raise EvidenceError(f"{label} CTest command changed during replay")
    return replay_names


def _ctest_pass_count(data: bytes, expected: int, label: str) -> int:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceError(f"{label} CTest output is not UTF-8") from error
    matches = CTEST_PASS.findall(text)
    if (len(matches) != 1 or int(matches[0]) != expected or
            "The following tests FAILED:" in text or
            "Errors while running CTest" in text):
        raise EvidenceError(f"{label} CTest pass summary is invalid")
    return int(matches[0])


def _replay_ctest_execution(
    command: list[str],
    environment: dict[str, str],
    expected: int,
    label: str,
) -> int:
    try:
        replay = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
            timeout=3600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EvidenceError(f"{label} CTest replay failed") from error
    if replay.returncode != 0:
        raise EvidenceError(f"{label} CTest replay failed")
    return _ctest_pass_count(replay.stdout, expected, f"{label} replay")


def _expected_ctest_commands(
    name: str,
    ctest_path: str,
    build_directory: str,
    host_arch: str,
) -> tuple[list[str], list[str], list[str]]:
    if (not Path(ctest_path).is_absolute() or
            not Path(build_directory).is_absolute() or
            not host_arch or "\0" in host_arch or
            any(character.isspace() for character in host_arch)):
        raise EvidenceError(f"{name} CTest execution identity is invalid")
    selection: list[str] = []
    inventory = [
        ctest_path, "--test-dir", build_directory,
        "--show-only=json-v1",
    ]
    command = [
        ctest_path, "--test-dir", build_directory,
        "--output-on-failure",
    ]
    if name == "tsan":
        setarch = Path("/usr/bin/setarch")
        if not setarch.exists():
            raise EvidenceError("TSAN evidence requires /usr/bin/setarch")
        command = [
            setarch.as_posix(), host_arch, "-R", *command,
        ]
    return command, inventory, selection


def _runner_source_identity(
    source_path: str, claimed_sha256: Any, expected_path: str,
) -> dict[str, Any]:
    if (source_path != expected_path or
            not _is_hex64(claimed_sha256)):
        raise EvidenceError("evidence runner source identity is invalid")
    local = SCRIPT_DIRECTORY.parent.joinpath(*PurePosixPath(source_path).parts)
    try:
        snapshot = common.stable_read(
            local, limit=MAX_INPUT_BYTES, capture=False,
            require_trusted_parent=False)
    except common.DeliveryClosureError as error:
        raise EvidenceError("evidence runner source is unavailable") from error
    if snapshot.sha256 != claimed_sha256:
        raise EvidenceError("evidence runner source digest drift")
    return {
        "path": source_path,
        "sha256": claimed_sha256,
        "size": snapshot.size,
    }


def _ctest_sidecar(
    root: Path,
    name: str,
    expected: int,
    relative: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sidecar = _binding(root, f"{name}.sidecar", relative)
    value = _strict_json(sidecar["_data"], f"{name} CTest sidecar")
    expected_fields = {
        "schema", "version", "label", "runner_source_path",
        "runner_sha256", "helper_source_path", "helper_sha256",
        "ctest_path", "ctest_sha256",
        "build_directory", "host_arch", "selection", "environment", "argv",
        "inventory_argv", "inventory_returncode", "returncode",
        "stdout_path", "inventory_path", "cache_path",
        "test_attestations", "source_manifest_path",
        "source_tree_attestation",
    }
    if (set(value) != expected_fields or
            value["schema"] != CTEST_SIDECAR_SCHEMA or
            value["version"] != 2 or value["label"] != name or
            value["returncode"] != 0 or
            value["inventory_returncode"] != 0 or
            value["environment"] != execution_environment(name) or
            not isinstance(value["argv"], list) or not value["argv"] or
            not isinstance(value["inventory_argv"], list) or
            not value["inventory_argv"] or
            not all(isinstance(item, str) and item and "\0" not in item
                    for item in value["argv"] + value["inventory_argv"])):
        raise EvidenceError(f"{name} CTest sidecar is invalid")
    runner = _runner_source_identity(
        value["runner_source_path"], value["runner_sha256"],
        CTEST_RUNNER_SOURCE)
    helper = _runner_source_identity(
        value["helper_source_path"], value["helper_sha256"],
        VERIFICATION_HELPER_SOURCE)
    ctest = _regular_file_attestation(
        value["ctest_path"], f"{name} CTest")
    if (ctest["sha256"] != value["ctest_sha256"] or
            not os.access(Path(ctest["realpath"]), os.X_OK)):
        raise EvidenceError(f"{name} CTest binary identity drift")
    expected_argv, expected_inventory, expected_selection = (
        _expected_ctest_commands(
            name, value["ctest_path"], value["build_directory"],
            value["host_arch"]))
    if (value["argv"] != expected_argv or
            value["inventory_argv"] != expected_inventory or
            value["selection"] != expected_selection):
        raise EvidenceError(f"{name} CTest fixed command drift")
    stdout_path = _relative(value["stdout_path"])
    inventory_path = _relative(value["inventory_path"])
    cache_path = _relative(value["cache_path"])
    stdout = _binding(root, f"{name}.stdout", stdout_path)
    inventory = _binding(root, f"{name}.inventory", inventory_path)
    cache = _binding(root, f"{name}.cmake-cache", cache_path)
    cache_values = _cache_values(cache["_data"], name)
    _validate_lane_cache(name, cache_values)
    if cache_values["CMAKE_CACHEFILE_DIR"] != value["build_directory"]:
        raise EvidenceError(f"{name} CMakeCache build directory drift")
    configured_ctest, _configured_ctest_identity = _configured_ctest(
        cache_values, name)
    if configured_ctest.as_posix() != value["ctest_path"]:
        raise EvidenceError(f"{name} CTest/CMakeCache identity drift")
    build = _live_directory(value["build_directory"], f"{name} build")
    home = _live_directory(
        cache_values["CMAKE_HOME_DIRECTORY"], f"{name} source")
    _live_cache_matches(build, cache, name)
    source_binding = None
    source_attestation = None
    if name in SOURCE_ATTESTATION_LABELS:
        if (not isinstance(value["source_manifest_path"], str) or
                not isinstance(value["source_tree_attestation"], dict)):
            raise EvidenceError(
                f"{name} source attestation is absent")
        source_binding = _binding(
            root, f"{name}.source-manifest",
            _relative(value["source_manifest_path"]))
        agent_source = _agent_source_label(name)
        source_identity = (
            _source_identity(source_binding["_data"], name)
            if agent_source else
            _strict_source_identity(source_binding["_data"], name))
        source_attestation = {
            "root": source_identity["root"],
            "file_count": source_identity["file_count"],
            "files_sha256": source_identity["files_sha256"],
            "git_directory_absent": True,
        }
        _require_disjoint_source_build(home, build, name)
        if (home.name != source_identity["root"] or
                value["source_tree_attestation"] != source_attestation):
            raise EvidenceError(f"{name} source identity drift")
        observed = attest_source_tree(
            home, source_binding["_data"], name,
            agent_source=agent_source)
        if observed != source_attestation:
            raise EvidenceError(
                f"{name} live source identity drift")
    elif (value["source_manifest_path"] is not None or
          value["source_tree_attestation"] is not None):
        raise EvidenceError(
            f"{name} lane has an unsupported source claim")
    observed_count = _ctest_pass_count(stdout["_data"], expected, name)
    inventory_document, test_names = _ctest_inventory_document(
        inventory["_data"], name)
    if len(test_names) != expected:
        raise EvidenceError(
            f"CTest inventory count drift for {name}: "
            f"{len(test_names)} != {expected}")
    attestations = _validate_attestations(
        inventory_document, value["test_attestations"], name)
    replay_names = _replay_ctest_inventory(
        value["inventory_argv"], value["environment"],
        inventory_document, attestations, name)
    if replay_names != test_names:
        raise EvidenceError(f"{name} CTest inventory test-name drift")
    replay_count = _replay_ctest_execution(
        value["argv"], value["environment"], expected, name)
    if replay_count != observed_count:
        raise EvidenceError(f"{name} CTest execution replay drift")
    if inventory_attestations(inventory_document, name) != attestations:
        raise EvidenceError(f"{name} CTest command changed during execution")
    if (source_binding is not None and
            attest_source_tree(
                home, source_binding["_data"], name,
                agent_source=_agent_source_label(name)) !=
            source_attestation):
        raise EvidenceError(f"{name} source changed during execution")
    _live_cache_matches(build, cache, name)
    records = [
        _without_data(sidecar),
        _without_data(stdout),
        _without_data(inventory),
        _without_data(cache),
    ]
    if source_binding is not None:
        records.append(_without_data(source_binding))
    case = {
        "name": name,
        "expected": expected,
        "observed": observed_count,
        "returncode": 0,
        "argv": value["argv"],
        "inventory_argv": value["inventory_argv"],
        "selection": value["selection"],
        "environment": value["environment"],
        "build_directory": value["build_directory"],
        "runner_source": runner,
        "helper_source": helper,
        "ctest": ctest,
        "test_attestations_sha256": hashlib.sha256(
            common.canonical_json(attestations)).hexdigest(),
        "test_names_sha256": hashlib.sha256(
            common.canonical_json(test_names)).hexdigest(),
        "sidecar_input": f"{name}.sidecar",
        "stdout_input": f"{name}.stdout",
        "inventory_input": f"{name}.inventory",
        "cache_input": f"{name}.cmake-cache",
        "source_manifest_input": (
            f"{name}.source-manifest"
            if source_binding is not None else None),
        "source_tree_attestation": source_attestation,
        "passed": True,
    }
    return records, case


def build_ctest(
    kind: str,
    root: Path,
    values: list[str],
    generated_at: str,
) -> dict[str, Any]:
    if kind not in {"matrix", "sanitizer"} or not values:
        raise EvidenceError("CTest evidence kind or inputs are invalid")
    report = _base(kind, generated_at)
    names: set[str] = set()
    for value in values:
        name, expected, relative = _parse_ctest(value)
        if name in names:
            raise EvidenceError(f"duplicate CTest evidence label: {name}")
        names.add(name)
        bindings, case = _ctest_sidecar(root, name, expected, relative)
        report["inputs"].extend(bindings)
        report["cases"].append(case)
    required = MATRIX_LABELS if kind == "matrix" else SANITIZER_LABELS
    if names != required:
        raise EvidenceError(
            f"{kind} CTest labels are incomplete: "
            f"{sorted(names)} != {sorted(required)}")
    report["inputs"].sort(key=lambda item: item["name"])
    report["cases"].sort(key=lambda item: item["name"])
    return report


def _coverage_percent(value: float) -> str:
    percent = value * 100.0
    return f"{percent:.12g}"


def _expected_coverage_commands(
    python_path: str,
    gcov_path: str,
    ctest_path: str,
    build_directory: str,
    source_directory: str,
    coverage_xml: Path,
    minimum_line_rate: float,
) -> tuple[list[str], list[str], list[str]]:
    selection = ["-E", COVERAGE_EXCLUDE]
    inventory = [
        ctest_path, "--test-dir", build_directory,
        "--show-only=json-v1", *selection,
    ]
    ctest = [
        ctest_path, "--test-dir", build_directory,
        "--output-on-failure", *selection,
    ]
    gcovr = [
        python_path, "-m", "gcovr", build_directory,
        "--gcov-executable", gcov_path,
        "--root", source_directory,
        "--filter", "HeptaTrade/",
        "--exclude", ".*/tests/.*",
        "--gcov-ignore-errors", "no_working_dir_found",
        "--gcov-ignore-parse-errors",
        "negative_hits.warn_once_per_file",
        "--fail-under-line", _coverage_percent(minimum_line_rate),
        "--print-summary", "--xml-pretty", "--xml",
        coverage_xml.as_posix(),
    ]
    return ctest, inventory, gcovr


def _verify_raw_coverage(
    manifest_data: bytes, archive_data: bytes, build_directory: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _strict_json(manifest_data, "coverage raw manifest")
    if (set(manifest) != {
            "schema", "version", "build_directory", "file_count",
            "files_sha256", "files"} or
            manifest["schema"] != "hepta.coverage-raw-inputs.v1" or
            manifest["version"] != 1 or
            not isinstance(manifest["build_directory"], str) or
            not Path(manifest["build_directory"]).is_absolute()):
        raise EvidenceError("coverage raw manifest is invalid")
    records, digest = _manifest_file_records(
        manifest["files"], "coverage raw", portable_modes=False)
    if (manifest["file_count"] != len(records) or
            manifest["files_sha256"] != digest or not records or
            not all(record["path"].endswith((".gcda", ".gcno"))
                    for record in records)):
        raise EvidenceError("coverage raw file closure is invalid")
    build = _live_directory(build_directory.as_posix(), "coverage raw build")
    if manifest["build_directory"] != build.as_posix():
        raise EvidenceError("coverage raw build identity drift")
    gcno_stems = {
        record["path"][:-5]
        for record in records if record["path"].endswith(".gcno")
    }
    gcda_stems = {
        record["path"][:-5]
        for record in records if record["path"].endswith(".gcda")
    }
    if not gcda_stems or not gcda_stems.issubset(gcno_stems):
        raise EvidenceError("coverage raw inputs have no paired gcno/gcda data")
    expected = {record["path"]: record for record in records}
    observed: set[str] = set()
    try:
        archive = tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:")
    except tarfile.TarError as error:
        raise EvidenceError("coverage raw archive is not a plain tar") from error
    with archive:
        for member in archive.getmembers():
            relative = _relative(member.name)
            if (not member.isfile() or member.linkname or member.pax_headers or
                    member.uid != 0 or member.gid != 0 or member.mtime != 0 or
                    member.mode & (0o022 | 0o7000) or
                    relative in observed or relative not in expected):
                raise EvidenceError("coverage raw archive metadata is invalid")
            source = archive.extractfile(member)
            if source is None:
                raise EvidenceError("coverage raw archive member is unreadable")
            data = source.read()
            record = expected[relative]
            expected_magic = (
                {b"oncg", b"gcno"} if relative.endswith(".gcno") else
                {b"adcg", b"gcda"})
            if (len(data) != record["size"] or
                    hashlib.sha256(data).hexdigest() != record["sha256"] or
                    f"{member.mode:04o}" != record["mode"] or
                    len(data) < 12 or data[:4] not in expected_magic):
                raise EvidenceError("coverage raw archive content drift")
            live_path = build.joinpath(*PurePosixPath(relative).parts)
            try:
                live = common.stable_read(
                    live_path, limit=MAX_INPUT_BYTES, capture=False,
                    require_trusted_parent=False)
            except common.DeliveryClosureError as error:
                raise EvidenceError(
                    "coverage live raw input is unsafe") from error
            if (live.sha256 != record["sha256"] or
                    live.size != record["size"] or
                    live.mode != record["mode"]):
                raise EvidenceError("coverage live raw input drift")
            observed.add(relative)
    if observed != set(expected):
        raise EvidenceError("coverage raw archive closure is incomplete")
    return manifest, records


def _coverage_semantics(data: bytes) -> dict[str, Any]:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise EvidenceError("coverage XML is invalid") from error
    tag = root.tag.rsplit("}", 1)[-1]
    if tag != "coverage":
        raise EvidenceError("coverage XML root is invalid")
    try:
        line_rate = float(root.attrib["line-rate"])
        lines_covered = int(root.attrib["lines-covered"])
        lines_valid = int(root.attrib["lines-valid"])
    except (KeyError, ValueError) as error:
        raise EvidenceError("coverage XML summary is invalid") from error
    if (not 0.0 <= line_rate <= 1.0 or lines_valid <= 0 or
            not 0 <= lines_covered <= lines_valid or
            abs(line_rate - lines_covered / lines_valid) > 0.00005):
        raise EvidenceError("coverage XML summary is inconsistent")
    packages = [
        item for item in root.iter()
        if item.tag.rsplit("}", 1)[-1] == "package"
    ]
    classes = [
        item for item in root.iter()
        if item.tag.rsplit("}", 1)[-1] == "class"
    ]
    line_records: list[dict[str, Any]] = []
    for class_record in classes:
        filename = class_record.attrib.get("filename")
        class_name = class_record.attrib.get("name")
        if (not isinstance(filename, str) or not filename or
                not isinstance(class_name, str) or not class_name):
            raise EvidenceError("coverage XML class identity is invalid")
        class_lines = [
            item for item in class_record
            if item.tag.rsplit("}", 1)[-1] == "lines"
        ]
        if len(class_lines) != 1:
            raise EvidenceError("coverage XML class lines are invalid")
        for line in class_lines[0]:
            if line.tag.rsplit("}", 1)[-1] != "line":
                raise EvidenceError("coverage XML class lines are invalid")
            try:
                number = int(line.attrib["number"])
                hits = int(line.attrib["hits"])
            except (KeyError, ValueError) as error:
                raise EvidenceError(
                    "coverage XML line identity is invalid") from error
            if number <= 0 or hits < 0:
                raise EvidenceError("coverage XML line identity is invalid")
            line_records.append({
                "filename": filename,
                "class": class_name,
                "number": number,
                "hits": hits,
            })
    line_records.sort(
        key=lambda item: (
            item["filename"], item["class"], item["number"], item["hits"]))
    if (not packages or not classes or len(line_records) != lines_valid or
            sum(record["hits"] > 0 for record in line_records) !=
            lines_covered):
        raise EvidenceError("coverage XML executable-line closure is invalid")
    return {
        "line_rate": line_rate,
        "lines_covered": lines_covered,
        "lines_valid": lines_valid,
        "line_records_sha256": hashlib.sha256(
            common.canonical_json(line_records)).hexdigest(),
    }


def _replay_gcovr(
    value: dict[str, Any],
    stored_semantics: dict[str, Any],
    policy: dict[str, Any],
    expected_toolchain: dict[str, Any],
) -> None:
    if coverage_toolchain_identity(policy) != expected_toolchain:
        raise EvidenceError("coverage toolchain changed before replay")
    with tempfile.TemporaryDirectory(prefix="hepta-gcovr-replay-") as temporary:
        replay_xml = Path(temporary) / "coverage.xml"
        command = list(value["coverage_argv"])
        command[-1] = replay_xml.as_posix()
        try:
            replay = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=value["environment"],
                timeout=3600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise EvidenceError("coverage gcovr replay failed") from error
        if replay.returncode != 0:
            raise EvidenceError("coverage gcovr replay failed")
        try:
            replay_snapshot = common.stable_read(
                replay_xml, limit=MAX_INPUT_BYTES, capture=True,
                require_trusted_parent=False)
        except common.DeliveryClosureError as error:
            raise EvidenceError("coverage replay XML is unsafe") from error
        assert replay_snapshot.data is not None
        if _coverage_semantics(replay_snapshot.data) != stored_semantics:
            raise EvidenceError("coverage gcovr replay drift")
    if coverage_toolchain_identity(policy) != expected_toolchain:
        raise EvidenceError("coverage toolchain changed during replay")


def build_coverage(
    root: Path,
    relative: str,
    minimum_line_rate: float,
    generated_at: str,
) -> dict[str, Any]:
    if (not 0.0 <= minimum_line_rate <= 1.0 or
            minimum_line_rate != minimum_line_rate):
        raise EvidenceError("coverage minimum line rate is invalid")
    sidecar = _binding(root, "coverage.sidecar", _relative(relative))
    value = _strict_json(sidecar["_data"], "coverage sidecar")
    expected_fields = {
        "schema", "version", "label", "runner_source_path",
        "runner_sha256", "helper_source_path", "helper_sha256",
        "ctest_path", "ctest_sha256", "toolchain_identity",
        "build_directory",
        "source_directory", "selection", "environment", "ctest_argv",
        "inventory_argv",
        "coverage_argv", "inventory_returncode", "ctest_returncode",
        "coverage_returncode", "expected_count", "minimum_line_rate",
        "stdout_path", "inventory_path", "coverage_stdout_path",
        "cache_path", "coverage_xml_path", "strict_source_manifest_path",
        "policy_path", "raw_manifest_path", "raw_archive_path",
        "test_attestations", "source_tree_attestation",
    }
    if (set(value) != expected_fields or
            value["schema"] != COVERAGE_SIDECAR_SCHEMA or
            value["version"] != 2 or value["label"] != "coverage" or
            any(value[field] != 0 for field in (
                "inventory_returncode", "ctest_returncode",
                "coverage_returncode")) or
            type(value["expected_count"]) is not int or
            value["expected_count"] <= 0 or
            value["minimum_line_rate"] != minimum_line_rate or
            value["environment"] != execution_environment("coverage") or
            value["selection"] != ["-E", COVERAGE_EXCLUDE]):
        raise EvidenceError("coverage sidecar is invalid")
    toolchain = _validate_coverage_toolchain_identity(
        value["toolchain_identity"])
    runner = _runner_source_identity(
        value["runner_source_path"], value["runner_sha256"],
        COVERAGE_RUNNER_SOURCE)
    helper = _runner_source_identity(
        value["helper_source_path"], value["helper_sha256"],
        VERIFICATION_HELPER_SOURCE)
    ctest = _regular_file_attestation(value["ctest_path"], "coverage CTest")
    if (ctest["sha256"] != value["ctest_sha256"] or
            not os.access(Path(ctest["realpath"]), os.X_OK)):
        raise EvidenceError("coverage executable identity drift")
    xml_relative = _relative(value["coverage_xml_path"])
    expected_ctest, expected_inventory, expected_gcovr = (
        _expected_coverage_commands(
            toolchain["python"]["configured_path"],
            toolchain["gcov"]["configured_path"], value["ctest_path"],
            value["build_directory"], value["source_directory"],
            root.joinpath(*PurePosixPath(xml_relative).parts),
            minimum_line_rate))
    if (value["ctest_argv"] != expected_ctest or
            value["inventory_argv"] != expected_inventory or
            value["coverage_argv"] != expected_gcovr):
        raise EvidenceError("coverage fixed command drift")
    bindings = {
        "coverage.sidecar": sidecar,
        "coverage.stdout": _binding(
            root, "coverage.stdout", _relative(value["stdout_path"])),
        "coverage.inventory": _binding(
            root, "coverage.inventory", _relative(value["inventory_path"])),
        "coverage.gcovr-stdout": _binding(
            root, "coverage.gcovr-stdout",
            _relative(value["coverage_stdout_path"])),
        "coverage.cmake-cache": _binding(
            root, "coverage.cmake-cache", _relative(value["cache_path"])),
        "coverage.xml": _binding(root, "coverage.xml", xml_relative),
        "coverage.strict-source-manifest": _binding(
            root, "coverage.strict-source-manifest",
            _relative(value["strict_source_manifest_path"])),
        "coverage.policy": _binding(
            root, "coverage.policy", _relative(value["policy_path"])),
        "coverage.raw-manifest": _binding(
            root, "coverage.raw-manifest",
            _relative(value["raw_manifest_path"])),
        "coverage.raw-archive": _binding(
            root, "coverage.raw-archive",
            _relative(value["raw_archive_path"])),
    }
    cache_values = _cache_values(
        bindings["coverage.cmake-cache"]["_data"], "coverage")
    cache_policy = _validate_lane_cache("coverage", cache_values)
    if (cache_values["CMAKE_HOME_DIRECTORY"] != value["source_directory"] or
            cache_values["CMAKE_CACHEFILE_DIR"] !=
            value["build_directory"]):
        raise EvidenceError("coverage source/cache identity drift")
    configured_ctest, _configured_ctest_identity = _configured_ctest(
        cache_values, "coverage")
    if configured_ctest.as_posix() != value["ctest_path"]:
        raise EvidenceError("coverage CTest/CMakeCache identity drift")
    build_path = _live_directory(
        value["build_directory"], "coverage build")
    source_path = _live_directory(
        value["source_directory"], "coverage source")
    _live_cache_matches(
        build_path, bindings["coverage.cmake-cache"], "coverage")
    strict_source = _strict_source_identity(
        bindings["coverage.strict-source-manifest"]["_data"], "coverage")
    if Path(value["source_directory"]).name != strict_source["root"]:
        raise EvidenceError("coverage strict source root drift")
    expected_source_attestation = {
        "root": strict_source["root"],
        "file_count": strict_source["file_count"],
        "files_sha256": strict_source["files_sha256"],
        "git_directory_absent": True,
    }
    if value["source_tree_attestation"] != expected_source_attestation:
        raise EvidenceError("coverage source-tree attestation drift")
    observed_source_attestation = attest_source_tree(
        source_path,
        bindings["coverage.strict-source-manifest"]["_data"],
        "coverage")
    if observed_source_attestation != expected_source_attestation:
        raise EvidenceError("coverage live source-tree attestation drift")
    policy = _strict_json(
        bindings["coverage.policy"]["_data"], "coverage policy")
    coverage_policy = policy.get("coverage")
    if (not isinstance(coverage_policy, dict) or
            type(coverage_policy.get("line_minimum_percent")) is not int or
            coverage_policy["line_minimum_percent"] / 100.0 !=
            minimum_line_rate):
        raise EvidenceError("coverage policy floor drift")
    expected_toolchain = coverage_toolchain_identity(policy)
    if toolchain != expected_toolchain:
        raise EvidenceError("coverage toolchain policy identity drift")
    inventory_document, test_names = _ctest_inventory_document(
        bindings["coverage.inventory"]["_data"], "coverage")
    if len(test_names) != value["expected_count"]:
        raise EvidenceError("coverage CTest inventory count drift")
    attestations = _validate_attestations(
        inventory_document, value["test_attestations"], "coverage")
    replay_names = _replay_ctest_inventory(
        value["inventory_argv"], value["environment"],
        inventory_document, attestations, "coverage")
    if replay_names != test_names:
        raise EvidenceError("coverage CTest inventory test-name drift")
    _ctest_pass_count(
        bindings["coverage.stdout"]["_data"],
        value["expected_count"],
        "coverage",
    )
    coverage_semantics = _coverage_semantics(
        bindings["coverage.xml"]["_data"])
    observed = coverage_semantics["line_rate"]
    if not 0.0 <= observed <= 1.0 or observed < minimum_line_rate:
        raise EvidenceError("coverage line-rate is below the required floor")
    raw_manifest, raw_records = _verify_raw_coverage(
        bindings["coverage.raw-manifest"]["_data"],
        bindings["coverage.raw-archive"]["_data"],
        build_path)
    _replay_gcovr(
        value, coverage_semantics, policy, expected_toolchain)
    _verify_raw_coverage(
        bindings["coverage.raw-manifest"]["_data"],
        bindings["coverage.raw-archive"]["_data"],
        build_path)
    if (coverage_toolchain_identity(policy) != expected_toolchain or
            attest_source_tree(
                source_path,
                bindings["coverage.strict-source-manifest"]["_data"],
                "coverage") != expected_source_attestation):
        raise EvidenceError("coverage execution inputs changed during replay")
    _live_cache_matches(
        build_path, bindings["coverage.cmake-cache"], "coverage")
    report = _base("coverage", generated_at)
    report["inputs"] = sorted(
        (_without_data(binding) for binding in bindings.values()),
        key=lambda item: item["name"])
    report["cases"] = [{
        "name": "line-rate",
        "expected": minimum_line_rate,
        "observed": observed,
        "lines_covered": coverage_semantics["lines_covered"],
        "lines_valid": coverage_semantics["lines_valid"],
        "line_records_sha256": coverage_semantics[
            "line_records_sha256"],
        "expected_tests": value["expected_count"],
        "test_names_sha256": hashlib.sha256(
            common.canonical_json(test_names)).hexdigest(),
        "test_attestations_sha256": hashlib.sha256(
            common.canonical_json(attestations)).hexdigest(),
        "raw_files": len(raw_records),
        "raw_files_sha256": raw_manifest["files_sha256"],
        "runner_source": runner,
        "helper_source": helper,
        "ctest": ctest,
        "coverage_toolchain": expected_toolchain,
        "cache_policy": cache_policy,
        "strict_source": strict_source,
        "source_tree_attestation": expected_source_attestation,
        "passed": True,
    }]
    return report


def _cache_values(data: bytes, label: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceError(f"{label} CMakeCache is not UTF-8") from error
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith(("#", "//")) or "=" not in line:
            continue
        key_type, value = line.split("=", 1)
        if ":" not in key_type:
            continue
        key, _kind = key_type.split(":", 1)
        if key in result:
            raise EvidenceError(f"{label} CMakeCache key is duplicated: {key}")
        result[key] = value
    required = {
        "BUILD_TESTING",
        "CMAKE_C_COMPILER",
        "CMAKE_CACHEFILE_DIR",
        "CMAKE_C_FLAGS",
        "CMAKE_CTEST_COMMAND",
        "CMAKE_CXX_COMPILER",
        "CMAKE_GENERATOR",
        "CMAKE_BUILD_TYPE",
        "CMAKE_HOME_DIRECTORY",
        "CMAKE_CXX_FLAGS",
        "CMAKE_EXE_LINKER_FLAGS",
        "CMAKE_CXX_FLAGS_DEBUG",
        "CMAKE_CXX_FLAGS_RELEASE",
        "CMAKE_CXX_FLAGS_RELWITHDEBINFO",
        "CMAKE_CXX_FLAGS_MINSIZEREL",
        "HEPTA_ENABLE_IBAPI",
        "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE",
        "HEPTA_BUILD_LEGACY_MONOLITH",
        "HEPTA_BUILD_LEGACY_SIMULATOR",
    }
    if not required.issubset(result):
        raise EvidenceError(f"{label} CMakeCache identity is incomplete")
    return result


def _configured_ctest(
    values: dict[str, str], label: str,
) -> tuple[Path, dict[str, Any]]:
    configured = values.get("CMAKE_CTEST_COMMAND", "")
    if (not configured or "\0" in configured or
            not Path(configured).is_absolute()):
        raise EvidenceError(f"{label} configured CTest path is invalid")
    identity = _regular_file_attestation(
        configured, f"{label} configured CTest")
    resolved = Path(identity["realpath"])
    if not os.access(resolved, os.X_OK):
        raise EvidenceError(f"{label} configured CTest is not executable")
    return resolved, identity


def _validate_lane_cache(
    label: str, values: dict[str, str],
) -> dict[str, Any]:
    if label not in RUNNER_LABELS:
        raise EvidenceError(f"unsupported verification lane: {label}")
    if (not Path(values["CMAKE_CACHEFILE_DIR"]).is_absolute() or
            not Path(values["CMAKE_CTEST_COMMAND"]).is_absolute() or
            not Path(values["CMAKE_HOME_DIRECTORY"]).is_absolute()):
        raise EvidenceError(f"{label} CMake source/build identity is invalid")
    if values["BUILD_TESTING"] != "ON":
        raise EvidenceError(f"{label} did not enable the complete test graph")
    for key in LEGACY_CACHE_KEYS:
        if values[key] != "OFF":
            raise EvidenceError(f"{label} legacy build boundary is open: {key}")
    expected_ibapi = "ON" if label in IBAPI_ON_LABELS else "OFF"
    if values["HEPTA_ENABLE_IBAPI"] != expected_ibapi:
        raise EvidenceError(f"{label} IBAPI lane identity drift")
    expected_build_type = (
        "Release" if label in MATRIX_LABELS else "Debug")
    if values["CMAKE_BUILD_TYPE"] != expected_build_type:
        raise EvidenceError(f"{label} build type identity drift")
    flags = {
        "c": values["CMAKE_C_FLAGS"],
        "cxx": values["CMAKE_CXX_FLAGS"],
        "link": values["CMAKE_EXE_LINKER_FLAGS"],
    }
    joined = " ".join(flags.values())
    sanitizer_tokens = {
        token for token in SANITIZER_FLAGS.values()
        if token in joined
    }
    if label in SANITIZER_LABELS:
        expected = SANITIZER_FLAGS[label]
        if (sanitizer_tokens != {expected} or
                expected not in flags["cxx"] or
                expected not in flags["link"] or
                "--coverage" in joined):
            raise EvidenceError(
                f"{label} sanitizer compiler/linker identity drift")
    elif label == "coverage":
        if (sanitizer_tokens or
                "--coverage" not in flags["cxx"] or
                "--coverage" not in flags["link"]):
            raise EvidenceError("coverage compiler/linker identity drift")
    elif sanitizer_tokens or "--coverage" in joined:
        raise EvidenceError(f"{label} compiler flags contaminate the lane")
    return {
        "build_testing": True,
        "ibapi_enabled": expected_ibapi == "ON",
        "legacy_enabled": False,
        "build_type": expected_build_type,
        "sanitizer": label if label in SANITIZER_LABELS else None,
        "coverage": label == "coverage",
        "flags": flags,
    }


def _compiler_identity(configured: str, label: str) -> dict[str, Any]:
    if (not configured or "\0" in configured or
            not Path(configured).is_absolute()):
        raise EvidenceError(f"{label} compiler path is invalid")
    try:
        real = Path(configured).resolve(strict=True)
        snapshot = common.stable_read(
            real, limit=256 * 1024 * 1024, capture=False,
            require_trusted_parent=True)
    except (OSError, common.DeliveryClosureError) as error:
        raise EvidenceError(f"{label} compiler identity is unsafe") from error
    if not os.access(real, os.X_OK):
        raise EvidenceError(f"{label} compiler is not executable")
    return {
        "configured_path": configured,
        "realpath": real.as_posix(),
        "sha256": snapshot.sha256,
        "size": snapshot.size,
        "mode": snapshot.mode,
    }


def _manifest_file_records(
    value: Any, label: str, *, portable_modes: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(value, list) or not value:
        raise EvidenceError(f"{label} manifest has no files")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        mode_valid = (
            isinstance(raw, dict) and isinstance(raw.get("mode"), str) and
            (
                raw["mode"] in {"0644", "0755"}
                if portable_modes else
                re.fullmatch(r"0[0-7]{3}", raw["mode"]) is not None and
                int(raw["mode"], 8) & (0o022 | 0o7000) == 0
            )
        )
        if (not isinstance(raw, dict) or
                set(raw) != {"path", "mode", "size", "sha256"} or
                not isinstance(raw["path"], str) or
                _relative(raw["path"]) in seen or
                not mode_valid or
                type(raw["size"]) is not int or raw["size"] < 0 or
                not _is_hex64(raw["sha256"])):
            raise EvidenceError(f"{label} manifest file record is invalid")
        seen.add(raw["path"])
        records.append(dict(raw))
    if [record["path"] for record in records] != sorted(seen):
        raise EvidenceError(f"{label} manifest file order is invalid")
    digest = hashlib.sha256(common.canonical_json(records)).hexdigest()
    return records, digest


def _source_identity(data: bytes, label: str) -> dict[str, Any]:
    value = _strict_json(data, f"{label} source manifest")
    expected_fields = {
        "schema", "version", "bundle_class", "release_version", "root",
        "file_count", "files_sha256", "policy_sha256",
        "parent_strict_source", "excluded_non_product_prefixes",
        "excluded_non_product_files", "excluded_legacy_prefixes",
        "excluded_legacy_files", "paper_authorized", "live_authorized",
        "files",
    }
    if (set(value) != expected_fields or
            value["schema"] != "hepta.agent-os-source-bundle.v1" or
            value["version"] != 1 or
            value["bundle_class"] != "agent-os-source-only" or
            value["paper_authorized"] is not False or
            value["live_authorized"] is not False or
            not isinstance(value["release_version"], str) or
            not value["release_version"] or
            value["root"] !=
            f"heptatrader-agent-os-{value['release_version']}" or
            not _is_hex64(value["policy_sha256"]) or
            not all(isinstance(value[field], list)
                    for field in (
                        "excluded_non_product_prefixes",
                        "excluded_non_product_files",
                        "excluded_legacy_prefixes",
                        "excluded_legacy_files")) or
            not isinstance(value["parent_strict_source"], dict)):
        raise EvidenceError(f"{label} Agent source manifest is invalid")
    records, files_sha256 = _manifest_file_records(
        value["files"], f"{label} Agent source")
    if (value["file_count"] != len(records) or
            value["files_sha256"] != files_sha256):
        raise EvidenceError(
            f"{label} Agent source file closure is invalid")
    parent = value["parent_strict_source"]
    if (set(parent) != {
            "schema", "git_head", "root", "file_count", "files_sha256",
            "bundle_sha256", "manifest_sha256"} or
            parent["schema"] != "hepta.clean-source-bundle.v2" or
            not _is_hex40(parent["git_head"]) or
            not isinstance(parent["root"], str) or not parent["root"] or
            type(parent["file_count"]) is not int or
            parent["file_count"] < len(records) or
            any(not _is_hex64(parent[field])
                for field in (
                    "files_sha256", "bundle_sha256",
                    "manifest_sha256"))):
        raise EvidenceError(f"{label} Agent source parent identity is invalid")
    return {
        "schema": value["schema"],
        "release_version": value["release_version"],
        "root": value["root"],
        "file_count": value["file_count"],
        "git_head": parent["git_head"],
        "files_sha256": value["files_sha256"],
        "policy_sha256": value["policy_sha256"],
        "parent": dict(parent),
    }


def _strict_source_identity(data: bytes, label: str) -> dict[str, Any]:
    value = _strict_json(data, f"{label} strict source manifest")
    if (value.get("schema") != "hepta.clean-source-bundle.v2" or
            value.get("paper_authorized") is not False or
            value.get("live_authorized") is not False or
            not _is_hex40(value.get("git_head")) or
            not isinstance(value.get("version"), str) or
            not value["version"] or
            value.get("root") != f"heptatrader-{value['version']}"):
        raise EvidenceError(f"{label} strict source manifest is invalid")
    records, files_sha256 = _manifest_file_records(
        value.get("files"), f"{label} strict source")
    if (value.get("file_count") != len(records) or
            value.get("files_sha256") != files_sha256):
        raise EvidenceError(f"{label} strict source file closure is invalid")
    return {
        "schema": value["schema"],
        "release_version": value["version"],
        "root": value["root"],
        "file_count": len(records),
        "files_sha256": files_sha256,
        "git_head": value["git_head"],
    }


def attest_source_tree(
    source: Path,
    manifest_data: bytes,
    label: str,
    *,
    agent_source: bool = False,
) -> dict[str, Any]:
    source = Path(os.path.abspath(source))
    if (source.resolve(strict=True) != source or
            os.path.lexists(source / ".git")):
        raise EvidenceError(f"{label} source tree is not no-Git")
    identity = (
        _source_identity(manifest_data, label)
        if agent_source else _strict_source_identity(manifest_data, label))
    document = _strict_json(manifest_data, f"{label} source manifest")
    if source.name != identity["root"]:
        raise EvidenceError(f"{label} source tree root drift")
    expected_paths = {
        record["path"] for record in document["files"]
    }
    internal_manifest = (
        AGENT_SOURCE_INTERNAL_MANIFEST
        if agent_source else STRICT_SOURCE_INTERNAL_MANIFEST)
    expected_paths.add(internal_manifest)
    observed_paths: set[str] = set()
    for directory, directory_names, file_names in os.walk(
            source, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in directory_names:
            path = directory_path / name
            try:
                metadata = path.lstat()
            except OSError as error:
                raise EvidenceError(
                    f"{label} source directory is unavailable") from error
            if not stat.S_ISDIR(metadata.st_mode):
                raise EvidenceError(
                    f"{label} source tree contains a non-directory entry")
        for name in file_names:
            path = directory_path / name
            try:
                metadata = path.lstat()
            except OSError as error:
                raise EvidenceError(
                    f"{label} source file is unavailable") from error
            if not stat.S_ISREG(metadata.st_mode):
                raise EvidenceError(
                    f"{label} source tree contains a non-regular file")
            observed_paths.add(path.relative_to(source).as_posix())
    if observed_paths != expected_paths:
        raise EvidenceError(f"{label} source file closure drift")
    marker = source.joinpath(
        *PurePosixPath(internal_manifest).parts)
    try:
        marker_snapshot = common.stable_read(
            marker, limit=MAX_INPUT_BYTES, capture=True,
            require_trusted_parent=False)
    except common.DeliveryClosureError as error:
        raise EvidenceError(
            f"{label} internal source manifest is unsafe") from error
    if (marker_snapshot.mode != "0644" or
            marker_snapshot.data != manifest_data):
        raise EvidenceError(
            f"{label} internal source manifest drift")
    for record in document["files"]:
        path = source.joinpath(*PurePosixPath(record["path"]).parts)
        try:
            snapshot = common.stable_read(
                path, limit=MAX_INPUT_BYTES, capture=False,
                require_trusted_parent=False)
        except common.DeliveryClosureError as error:
            raise EvidenceError(
                f"{label} source file is unsafe: {record['path']}") from error
        if (snapshot.sha256 != record["sha256"] or
                snapshot.size != record["size"] or
                snapshot.mode != record["mode"]):
            raise EvidenceError(
                f"{label} source file drift: {record['path']}")
    return {
        "root": identity["root"],
        "file_count": identity["file_count"],
        "files_sha256": identity["files_sha256"],
        "git_directory_absent": True,
    }


def build_runner(
    root: Path,
    values: list[str],
    generated_at: str,
    source_values: list[str] | None = None,
) -> dict[str, Any]:
    report = _base("runner", generated_at)
    bindings: list[dict[str, Any]] = []
    cache_paths: dict[str, str] = {}
    for value in values:
        name, relative = _parse_named_path(value)
        if name in cache_paths:
            raise EvidenceError("runner cache labels are duplicated")
        cache_paths[name] = relative
        bindings.append(_binding(root, f"{name}.cmake-cache", relative))
    if set(cache_paths) != RUNNER_LABELS:
        raise EvidenceError("runner cache label closure is invalid")
    sources: dict[str, str] = {}
    for value in source_values or []:
        name, relative = _parse_named_path(value)
        if name in sources:
            raise EvidenceError("runner source labels are duplicated")
        sources[name] = relative
        bindings.append(_binding(root, f"{name}.source-manifest", relative))
    if set(sources) != SOURCE_ATTESTATION_LABELS:
        raise EvidenceError(
            "runner source attestation label closure is invalid")
    if len({item["name"] for item in bindings}) != len(bindings):
        raise EvidenceError("runner input labels are duplicated")
    report["inputs"] = sorted(
        (_without_data(binding) for binding in bindings),
        key=lambda item: item["name"])
    by_name = {binding["name"]: binding for binding in bindings}
    cases: list[dict[str, Any]] = []
    for name in sorted(RUNNER_LABELS):
        cache = by_name[f"{name}.cmake-cache"]
        values_by_key = _cache_values(cache["_data"], name)
        policy = _validate_lane_cache(name, values_by_key)
        build = _live_directory(
            values_by_key["CMAKE_CACHEFILE_DIR"], f"{name} build")
        home = _live_directory(
            values_by_key["CMAKE_HOME_DIRECTORY"], f"{name} source")
        _live_cache_matches(build, cache, name)
        source = None
        if name in SOURCE_ATTESTATION_LABELS:
            agent_source = _agent_source_label(name)
            source = (
                _source_identity(
                    by_name[f"{name}.source-manifest"]["_data"], name)
                if agent_source else
                _strict_source_identity(
                    by_name[f"{name}.source-manifest"]["_data"], name))
            if home.name != source["root"]:
                raise EvidenceError(
                    f"{name} CMake source root drift")
            _require_disjoint_source_build(home, build, name)
            attest_source_tree(
                home,
                by_name[f"{name}.source-manifest"]["_data"],
                name,
                agent_source=agent_source,
            )
        cases.append({
            "name": name,
            "cache_input": f"{name}.cmake-cache",
            "cxx_compiler": _compiler_identity(
                values_by_key["CMAKE_CXX_COMPILER"], name),
            "c_compiler": _compiler_identity(
                values_by_key["CMAKE_C_COMPILER"], name),
            "cmake": {
                "generator": values_by_key["CMAKE_GENERATOR"],
                "build_type": values_by_key["CMAKE_BUILD_TYPE"],
                "home_directory": values_by_key["CMAKE_HOME_DIRECTORY"],
                "cachefile_directory": values_by_key[
                    "CMAKE_CACHEFILE_DIR"],
                "policy": policy,
                "cxx_flags": {
                    key: values_by_key[key]
                    for key in sorted(values_by_key)
                    if key.startswith("CMAKE_CXX_FLAGS")
                },
                "c_flags": values_by_key["CMAKE_C_FLAGS"],
                "exe_linker_flags": values_by_key[
                    "CMAKE_EXE_LINKER_FLAGS"],
            },
            "source_manifest_input": (
                f"{name}.source-manifest" if source is not None else None),
            "source": source,
            "passed": True,
        })
    report["cases"] = cases
    return report


def _write_private(root: Path, path: Path, data: bytes) -> None:
    if not path.is_absolute():
        path = root / path
    lexical = Path(os.path.abspath(path))
    try:
        parent = lexical.parent.resolve(strict=True)
    except OSError as error:
        raise EvidenceError("verification evidence parent is unavailable") from error
    if ((parent != root and root not in parent.parents) or
            lexical != parent / lexical.name):
        raise EvidenceError("verification evidence output escapes artifact root")
    metadata = parent.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) & 0o022 or
            stat.S_IMODE(metadata.st_mode) & 0o7000):
        raise EvidenceError("verification evidence parent is not protected")
    descriptor = os.open(
        lexical,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise EvidenceError("failed to publish verification evidence")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kind", choices=(
            "matrix", "sanitizer", "coverage",
            "coverage-toolchain", "runner"),
        required=True)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--ctest-log", action="append", default=[])
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--source-manifest", action="append", default=[])
    parser.add_argument("--coverage-sidecar")
    parser.add_argument("--coverage-policy", type=Path)
    parser.add_argument("--minimum-line-rate", type=float, default=0.70)
    parser.add_argument("--generated-at")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.kind == "coverage-toolchain":
        if arguments.coverage_policy is None:
            raise EvidenceError(
                "coverage toolchain check requires --coverage-policy")
        identity = check_coverage_toolchain_policy(
            arguments.coverage_policy)
        print(
            "PASS: "
            f"{identity['schema']} controlled-runner identity matched")
        return 0
    if arguments.artifact_root is None or arguments.output is None:
        raise EvidenceError(
            "verification evidence requires --artifact-root and --output")
    root = _protected_root(arguments.artifact_root)
    generated_at = arguments.generated_at or datetime.now(
        timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if arguments.kind in {"matrix", "sanitizer"}:
        report = build_ctest(
            arguments.kind, root, arguments.ctest_log, generated_at)
    elif arguments.kind == "coverage":
        if arguments.coverage_sidecar is None:
            raise EvidenceError("coverage evidence requires --coverage-sidecar")
        report = build_coverage(
            root, arguments.coverage_sidecar,
            arguments.minimum_line_rate, generated_at)
    else:
        report = build_runner(
            root, arguments.input, generated_at, arguments.source_manifest)
    payload = (
        json.dumps(report, ensure_ascii=True, sort_keys=True,
                   separators=(",", ":")) + "\n").encode("ascii")
    _write_private(root, arguments.output, payload)
    print(f"PASS: {SCHEMA} kind={arguments.kind}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvidenceError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
