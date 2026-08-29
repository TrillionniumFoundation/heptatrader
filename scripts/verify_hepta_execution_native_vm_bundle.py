#!/usr/bin/env python3

"""Independently verify one broker-free native-VM rootfs bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tarfile
from typing import Any, Optional


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))
import build_hepta_execution_native_vm_bundle as contract  # noqa: E402
import run_hepta_execution_rootful_systemd_gate as shared  # noqa: E402
import verify_heptatrader_clean_source_bundle as clean_source  # noqa: E402


SCHEMA = "hepta.execution-native-vm-bundle-verification.v7"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_MEMBERS = 256
HEX_64 = re.compile(r"[0-9a-f]{64}")
IMAGE_MANIFEST = (
    "usr/local/share/hepta-rootful-systemd-gate/image-manifest.json")
IMAGE_DIGEST = (
    "usr/local/share/hepta-rootful-systemd-gate/image-manifest.sha256")
PROVISIONING_MANIFEST = (
    "usr/local/share/hepta-rootful-systemd-gate/provisioning-manifest.json")
PLATFORM_POLICY = (
    "usr/local/share/hepta-rootful-systemd-gate/platform-policy.json")
CLEAN_SOURCE_PROVENANCE = (
    "usr/local/share/hepta-rootful-systemd-gate/clean-source-provenance.json")
CLEAN_SOURCE_MANIFEST = (
    "usr/local/share/hepta-rootful-systemd-gate/source-bundle-manifest.json")
SOURCE_BUILD_LINEAGE = (
    "usr/local/share/hepta-rootful-systemd-gate/source-build-lineage.json")
AGENT_OS_INSTALLATION_MANIFEST = (
    "usr/local/share/hepta-rootful-systemd-gate/"
    "agent-os-installation-manifest.json")
AGENT_OS_RUNTIME_INPUT_MANIFEST = (
    "usr/local/share/hepta-rootful-systemd-gate/"
    "agent-os-runtime-input-manifest.json")
VARIANT_FILE = "usr/local/share/hepta-rootful-systemd-gate/variant"
FORMAL_DIGEST = (
    "usr/local/share/hepta-rootful-systemd-gate/formal-ibapi.sha256")
CANONICAL_IB = "usr/libexec/hepta-ib-executiond"
DISABLED_IB = "usr/local/libexec/hepta-ib-executiond-disabled"
SANDBOX_IB = "usr/local/libexec/hepta_execution_systemd_sandbox_probe"
FORBIDDEN = {
    "etc/heptatrader/hepta-native-systemd-gate.disposable",
    "run/hepta-rootful-systemd-gate.disposable",
    "run/docker.sock",
    "usr/libexec/hepta-ib-executiond-formal",
} | {path.as_posix() for path in contract.AGENT_OS_RUNTIME_PATHS}
REQUIRED = {
    "usr/libexec/hepta-executiond",
    CANONICAL_IB,
    "usr/local/libexec/check_hepta_execution_provisioned_host.py",
    "usr/local/libexec/run_hepta_execution_rootful_systemd_gate.py",
    "usr/local/libexec/run_hepta_execution_native_systemd_gate.py",
    "usr/local/libexec/hepta_execution_rootful_inner_gate.py",
    "usr/local/libexec/hepta_execution_systemd_client_probe",
    SANDBOX_IB,
    DISABLED_IB,
    FORMAL_DIGEST,
    CLEAN_SOURCE_PROVENANCE,
    CLEAN_SOURCE_MANIFEST,
    SOURCE_BUILD_LINEAGE,
    AGENT_OS_INSTALLATION_MANIFEST,
    AGENT_OS_RUNTIME_INPUT_MANIFEST,
    PLATFORM_POLICY,
    PROVISIONING_MANIFEST,
    VARIANT_FILE,
    IMAGE_MANIFEST,
    IMAGE_DIGEST,
    *{
        path.as_posix()
        for paths in contract.BUILD_EVIDENCE_PATHS.values()
        for path in paths.values()
    },
} | {
    path.as_posix()
    for path in set(contract.AGENT_OS_STATIC_PATHS) |
    set(contract.AGENT_OS_RUNTIME_GATE_PATHS)
}


class VerificationError(RuntimeError):
    """A fail-closed native-VM bundle verification error."""


def fail(message: str) -> None:
    raise VerificationError(message)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) +
        "\n").encode("utf-8")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key in {key!r}")
        result[key] = value
    return result


def parse_json(contents: bytes, label: str, maximum: int) -> dict[str, Any]:
    if len(contents) > maximum:
        fail(f"{label} exceeds the size limit")
    try:
        value = json.loads(
            contents.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        fail(f"{label} is not valid UTF-8 JSON")
    if not isinstance(value, dict):
        fail(f"{label} is not a JSON object")
    return value


def safe_file(path: Path, maximum: int) -> tuple[os.stat_result, bytes, str]:
    metadata, contents, digest = shared.read_regular_file(path, maximum=maximum)
    if (metadata.st_nlink != 1 or
            stat.S_IMODE(metadata.st_mode) & 0o077):
        fail(f"bundle input is not private and single-link: {path}")
    return metadata, contents, digest


def validate_bundle_report(report: dict[str, Any]) -> None:
    expected = {
        "schema", "passed", "variant", "platform_policy",
        "platform_policy_sha256", "provisioning_manifest_sha256",
        "clean_source_provenance_sha256", "clean_source",
        "clean_source_manifest_sha256", "source_build_lineage_sha256",
        "agent_os_installation_manifest_sha256",
        "agent_os_runtime_input_manifest_sha256",
        "agent_os_runtime_input_file_count",
        "agent_os_installation_file_count",
        "vm_image_manifest_sha256", "rootfs_file_count", "archive",
        "boundary",
    }
    boundary = {
        "formal_ibapi_elf_staged": False,
        "instance_identity_staged": False,
        "agent_os_installation_preflight_staged": True,
        "agent_os_runtime_preflight_executed": False,
        "agent_os_runtime_preflight_required": True,
        "agent_os_runtime_gate_inputs_staged": True,
        "agent_os_runtime_state_provisioned": False,
        "agent_os_runtime_sentinel_staged": False,
        "agent_os_runtime_artifacts_staged": False,
        "paper_authorized": False,
        "live_enabled": False,
        "broker_connections": 0,
        "orders": 0,
    }
    if (set(report) != expected or report.get("schema") != contract.SCHEMA or
            report.get("passed") is not True or
            report.get("variant") not in contract.VARIANTS or
            report.get("boundary") != boundary or
            type(report.get("rootfs_file_count")) is not int or
            report["rootfs_file_count"] <= 0 or
            type(report.get("agent_os_installation_file_count")) is not int or
            report["agent_os_installation_file_count"] !=
            len(contract.AGENT_OS_STATIC_PATHS) or
            type(report.get("agent_os_runtime_input_file_count")) is not int or
            report["agent_os_runtime_input_file_count"] !=
            len(contract.AGENT_OS_RUNTIME_GATE_PATHS) or
            any(HEX_64.fullmatch(report.get(key, "")) is None for key in (
                "platform_policy_sha256", "provisioning_manifest_sha256",
                "vm_image_manifest_sha256",
                "clean_source_provenance_sha256",
                "clean_source_manifest_sha256",
                "source_build_lineage_sha256",
                "agent_os_installation_manifest_sha256",
                "agent_os_runtime_input_manifest_sha256")) or
            not isinstance(report.get("clean_source"), dict) or
            not isinstance(report.get("archive"), dict)):
        fail("native VM bundle report contract mismatch")
    archive = report["archive"]
    if (set(archive) != {
            "path", "device", "inode", "mode", "size", "sha256"} or
            archive.get("mode") != "0600" or
            type(archive.get("size")) is not int or archive["size"] <= 0 or
            archive["size"] > MAX_ARCHIVE_BYTES or
            HEX_64.fullmatch(archive.get("sha256", "")) is None):
        fail("native VM bundle archive record mismatch")
    contract.validate_platform_policy(json_bytes(report["platform_policy"]))


def read_archive(path: Path) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    safe_file(path, MAX_ARCHIVE_BYTES)
    files: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    entries: set[str] = set()
    directories: set[str] = set()
    with tarfile.open(path, mode="r:") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            fail("native VM archive member count is invalid")
        for member in members:
            name = member.name
            if (not name or name.startswith("/") or
                    ".." in Path(name).parts or name in entries or
                    member.uid != 0 or member.gid != 0 or
                    member.uname != "root" or member.gname != "root" or
                    member.mtime != 0 or member.issym() or member.islnk()):
                fail("native VM archive metadata contract mismatch")
            entries.add(name)
            if member.isdir():
                expected_mode = (
                    0o700 if name == "etc/heptatrader/credentials" else 0o755)
                if member.mode != expected_mode:
                    fail("native VM archive directory mode mismatch")
                directories.add(name)
                continue
            if (not member.isfile() or member.size < 0 or
                    member.size > MAX_FILE_BYTES or name in FORBIDDEN):
                fail("native VM archive file contract mismatch")
            source = archive.extractfile(member)
            if source is None:
                fail("native VM archive file is unreadable")
            contents = source.read(MAX_FILE_BYTES + 1)
            if len(contents) != member.size:
                fail("native VM archive file size mismatch")
            files[name] = contents
            records.append({
                "path": name,
                "mode": format(member.mode, "04o"),
                "uid": 0,
                "gid": 0,
                "size": member.size,
                "sha256": hashlib.sha256(contents).hexdigest(),
            })
    if not REQUIRED.issubset(files):
        fail("native VM archive is missing a required payload file")
    expected_directories = {
        parent.as_posix()
        for name in files
        for parent in Path(name).parents
        if parent != Path(".")
    }
    if directories != expected_directories:
        fail("native VM archive directory closure mismatch")
    return files, records


def parse_json_value(contents: bytes, label: str, maximum: int) -> Any:
    if len(contents) > maximum:
        fail(f"{label} exceeds the size limit")
    try:
        return json.loads(
            contents.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        fail(f"{label} is not strict UTF-8 JSON")


def parse_cmake_cache(contents: bytes, label: str) -> dict[str, str]:
    try:
        text = contents.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        fail(f"{label} is not valid UTF-8")
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw.startswith(("#", "//")) or "=" not in raw:
            continue
        left, value = raw.split("=", 1)
        if ":" not in left:
            continue
        key, _kind = left.split(":", 1)
        if key in values:
            fail(f"{label} contains a duplicate CMake cache key")
        values[key] = value
    return values


def canonical_source_manifest_index(
        contents: bytes,
        *,
        external_contents: bytes,
        source: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if contents != external_contents:
        fail("embedded and external clean-source manifests differ")
    manifest = parse_json(
        contents, "embedded clean-source manifest",
        clean_source.MAX_MANIFEST_BYTES)
    entries = manifest.get("files")
    if (manifest.get("schema") != "hepta.clean-source-bundle.v2" or
            manifest.get("bundle_class") != "strict-source-only" or
            manifest.get("version") != source["version"] or
            manifest.get("git_head") != source["git_head"] or
            manifest.get("file_count") != source["file_count"] or
            manifest.get("files_sha256") != source["files_sha256"] or
            manifest.get("root") !=
            f"heptatrader-{source['version']}" or
            hashlib.sha256(contents).hexdigest() !=
            source["manifest_sha256"] or not isinstance(entries, list) or
            len(entries) != source["file_count"]):
        fail("embedded clean-source manifest identity mismatch")
    canonical = json.dumps(
        entries, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != source["files_sha256"]:
        fail("embedded clean-source file closure digest mismatch")
    index: dict[str, dict[str, Any]] = {}
    for record in entries:
        if (not isinstance(record, dict) or set(record) != {
                "path", "mode", "size", "sha256"} or
                not isinstance(record.get("path"), str) or
                not record["path"] or record["path"].startswith("/") or
                "\\" in record["path"] or
                Path(record["path"]).as_posix() != record["path"] or
                ".." in Path(record["path"]).parts or
                record["path"] in index or
                record.get("mode") not in {"0644", "0755"} or
                type(record.get("size")) is not int or record["size"] < 0 or
                HEX_64.fullmatch(record.get("sha256", "")) is None):
            fail("embedded clean-source file record mismatch")
        index[record["path"]] = record
    if len(index) != source["file_count"]:
        fail("embedded clean-source file path closure mismatch")
    return manifest, index


def normalize_compile_path(
        value: Any, directory: Path, label: str,
) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        fail(f"{label} path is invalid")
    path = Path(value)
    if not path.is_absolute():
        path = directory / path
    return Path(os.path.normpath(path))


def validate_ibapi_source_manifest(
        value: Any, *, ibapi_root: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    if not isinstance(value, dict) or set(value) != {
            "schema", "root", "file_count", "files_sha256", "files"}:
        fail("embedded IBAPI SDK source manifest contract mismatch")
    entries = value.get("files")
    if (value.get("schema") != contract.IBAPI_SOURCE_MANIFEST_SCHEMA or
            not isinstance(ibapi_root, str) or
            not ibapi_root.startswith("/") or
            value.get("root") != Path(os.path.normpath(ibapi_root)).name or
            type(value.get("file_count")) is not int or
            value["file_count"] <= 0 or
            value["file_count"] > contract.MAX_IBAPI_SOURCE_FILES or
            HEX_64.fullmatch(value.get("files_sha256", "")) is None or
            not isinstance(entries, list) or
            len(entries) != value["file_count"]):
        fail("embedded IBAPI SDK source manifest identity mismatch")
    index: dict[str, dict[str, Any]] = {}
    total_size = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
                "path", "mode", "size", "sha256"}:
            fail("embedded IBAPI SDK source file record mismatch")
        relative = entry.get("path")
        if (not isinstance(relative, str) or not relative or
                "\0" in relative or "\\" in relative or
                Path(relative).is_absolute() or
                Path(relative).as_posix() != relative or
                any(part in {"", ".", ".."}
                    for part in Path(relative).parts) or
                relative in index or
                not isinstance(entry.get("mode"), str) or
                re.fullmatch(r"[0-7]{4}", entry["mode"]) is None or
                int(entry["mode"], 8) & 0o7022 or
                not int(entry["mode"], 8) & 0o400 or
                type(entry.get("size")) is not int or entry["size"] < 0 or
                HEX_64.fullmatch(entry.get("sha256", "")) is None):
            fail("embedded IBAPI SDK source file metadata mismatch")
        total_size += entry["size"]
        if total_size > contract.MAX_IBAPI_SOURCE_BYTES:
            fail("embedded IBAPI SDK source manifest exceeds size limit")
        index[relative] = entry
    if list(index) != sorted(index):
        fail("embedded IBAPI SDK source manifest is not path ordered")
    canonical_entries = json.dumps(
        entries, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode("utf-8")
    if hashlib.sha256(canonical_entries).hexdigest() != value["files_sha256"]:
        fail("embedded IBAPI SDK source aggregate mismatch")
    return value, index, hashlib.sha256(json_bytes(value)).hexdigest()


def verify_causal_build_receipt(
        value: Any,
        *,
        build_key: str,
        ibapi: bool,
        source_manifest_sha256: str,
        ibapi_source_manifest_sha256: Optional[str],
        generator: str,
) -> dict[str, dict[str, Any]]:
    expected_fields = {
        "schema", "fresh_build_directory_created_empty",
        "prebuilt_artifacts_exactly_matched", "source_manifest_sha256",
        "ibapi_source_manifest_sha256", "configure_argv", "build_argv",
        "environment", "toolchain", "configure_log_size",
        "configure_log_sha256", "build_log_size", "build_log_sha256",
        "outputs",
    }
    expected_environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": "$CAUSAL_ROOT/.home",
        "TMPDIR": "$CAUSAL_ROOT/.tmp",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "0",
        "CFLAGS": "",
        "CXXFLAGS": "",
        "LDFLAGS": "",
    }
    if (not isinstance(value, dict) or set(value) != expected_fields or
            value.get("schema") != contract.CAUSAL_BUILD_RECEIPT_SCHEMA or
            value.get("fresh_build_directory_created_empty") is not True or
            value.get("prebuilt_artifacts_exactly_matched") is not True or
            value.get("source_manifest_sha256") !=
            source_manifest_sha256 or
            value.get("ibapi_source_manifest_sha256") !=
            ibapi_source_manifest_sha256 or
            value.get("configure_argv") !=
            contract._normalized_configure_argv(generator, ibapi=ibapi) or
            value.get("build_argv") != [
                "$CMAKE", "--build", "$BUILD_ROOT", "--config", "Release",
                "--parallel", "1", "--target",
                *contract.CAUSAL_BUILD_TARGETS] or
            value.get("environment") != expected_environment or
            type(value.get("configure_log_size")) is not int or
            value["configure_log_size"] < 0 or
            HEX_64.fullmatch(value.get("configure_log_sha256", "")) is None or
            type(value.get("build_log_size")) is not int or
            value["build_log_size"] < 0 or
            HEX_64.fullmatch(value.get("build_log_sha256", "")) is None):
        fail("fresh causal build receipt contract mismatch")

    tools = value.get("toolchain")
    allowed_roles = {
        "cmake", "c_compiler", "cxx_compiler", "build_program",
        "ar", "linker", "nm", "objcopy", "objdump", "ranlib", "strip",
    }
    required_roles = {"cmake", "c_compiler", "cxx_compiler", "build_program"}
    by_role: dict[str, dict[str, Any]] = {}
    if not isinstance(tools, list):
        fail("fresh causal build toolchain is invalid")
    for record in tools:
        if (not isinstance(record, dict) or set(record) != {
                "role", "path", "mode", "size", "sha256"} or
                record.get("role") not in allowed_roles or
                record["role"] in by_role or
                not isinstance(record.get("path"), str) or
                not record["path"].startswith("/") or
                "\0" in record["path"] or "\\" in record["path"] or
                Path(record["path"]).as_posix() != record["path"] or
                os.path.normpath(record["path"]) != record["path"] or
                record.get("mode") not in {"0500", "0550", "0555", "0700",
                                                   "0750", "0755"} or
                type(record.get("size")) is not int or record["size"] <= 0 or
                HEX_64.fullmatch(record.get("sha256", "")) is None):
            fail("fresh causal build tool record mismatch")
        by_role[record["role"]] = record
    if (not required_roles.issubset(by_role) or
            list(by_role) != sorted(by_role)):
        fail("fresh causal build tool closure mismatch")

    outputs = value.get("outputs")
    expected_artifacts = set(contract.CAUSAL_BUILD_OUTPUTS[build_key])
    by_artifact: dict[str, dict[str, Any]] = {}
    if not isinstance(outputs, list):
        fail("fresh causal build outputs are invalid")
    for record in outputs:
        if (not isinstance(record, dict) or set(record) != {
                "artifact", "build_path", "mode", "size", "sha256"} or
                record.get("artifact") not in expected_artifacts or
                record["artifact"] in by_artifact or
                not isinstance(record.get("build_path"), str) or
                not record["build_path"] or
                "\0" in record["build_path"] or
                "\\" in record["build_path"] or
                Path(record["build_path"]).is_absolute() or
                Path(record["build_path"]).as_posix() !=
                record["build_path"] or
                any(part in {"", ".", ".."}
                    for part in Path(record["build_path"]).parts) or
                record.get("mode") != "0755" or
                type(record.get("size")) is not int or record["size"] <= 0 or
                HEX_64.fullmatch(record.get("sha256", "")) is None):
            fail("fresh causal build output record mismatch")
        by_artifact[record["artifact"]] = record
    if (set(by_artifact) != expected_artifacts or
            list(by_artifact) != sorted(by_artifact)):
        fail("fresh causal build output closure mismatch")
    return by_artifact


def verify_compile_evidence(
        contents: bytes,
        *,
        cache: dict[str, str],
        source_index: dict[str, dict[str, Any]],
        ibapi: bool,
        ibapi_source_index: Optional[dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    commands = parse_json_value(
        contents, "embedded compile_commands.json", 32 * 1024 * 1024)
    if not isinstance(commands, list) or not commands:
        fail("embedded compile_commands.json must be a non-empty array")
    source_root = Path(cache["CMAKE_HOME_DIRECTORY"])
    build_root = Path(cache["CMAKE_CACHEFILE_DIR"])
    ibapi_root = Path(cache["IBAPI_ROOT"]) if ibapi else None
    if ((ibapi and not ibapi_source_index) or
            (not ibapi and ibapi_source_index is not None)):
        fail("embedded IBAPI compile manifest profile mismatch")
    records: list[dict[str, str]] = []
    clean_count = 0
    ibapi_count = 0
    for item in commands:
        if (not isinstance(item, dict) or
                not {"directory", "file"}.issubset(item) or
                ("command" in item) == ("arguments" in item)):
            fail("embedded compile command entry mismatch")
        directory = normalize_compile_path(
            item["directory"], build_root, "compile directory")
        try:
            directory.relative_to(build_root)
        except ValueError:
            fail("embedded compile directory escapes its build tree")
        source_path = normalize_compile_path(
            item["file"], directory, "compile source")
        try:
            relative = source_path.relative_to(source_root).as_posix()
            source_record = source_index.get(relative)
            if source_record is None:
                fail(f"embedded compile source is outside source closure: "
                     f"{relative}")
            source_id = "source/" + relative
            source_sha256 = source_record["sha256"]
            clean_count += 1
        except ValueError:
            if not ibapi or ibapi_root is None:
                fail("embedded compile source is outside clean source")
            try:
                relative = source_path.relative_to(ibapi_root).as_posix()
            except ValueError:
                fail("embedded compile source escapes IBAPI_ROOT")
            source_record = ibapi_source_index.get(relative)
            if source_record is None:
                fail("embedded IBAPI compile source is absent from SDK "
                     "manifest")
            source_id = "ibapi/" + relative
            # SDK bytes are not redistributed, but their independently
            # manifested byte digest is part of the compile closure.
            source_sha256 = source_record["sha256"]
            ibapi_count += 1
        if "arguments" in item:
            if (not isinstance(item["arguments"], list) or
                    not all(isinstance(token, str)
                            for token in item["arguments"])):
                fail("embedded compile arguments are invalid")
            tokens = item["arguments"]
        else:
            try:
                tokens = shlex.split(item["command"], posix=True)
            except (TypeError, ValueError):
                fail("embedded compile command is invalid")
        token_paths: set[Path] = set()
        for token in tokens:
            candidate = Path(token)
            if not candidate.is_absolute() and not token.startswith("."):
                continue
            token_paths.add(normalize_compile_path(
                token, directory, "compile token"))
        if source_path not in token_paths:
            fail("embedded command does not compile its declared source")
        records.append({"path": source_id, "sha256": source_sha256})
    if clean_count <= 0 or (ibapi and ibapi_count <= 0) or (
            not ibapi and ibapi_count != 0):
        fail("embedded compile source counts mismatch")
    canonical = json.dumps(
        sorted(records, key=lambda record: (
            record["path"], record["sha256"])),
        ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return {
        "translation_unit_count": len(commands),
        "clean_source_translation_unit_count": clean_count,
        "ibapi_translation_unit_count": ibapi_count,
        "compile_sources_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def expected_staged_source_records(
        source_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    expected: list[dict[str, Any]] = []
    for relative, destinations in sorted(
            contract.SOURCE_STAGE_BINDINGS.items()):
        source = source_index.get(relative)
        if source is None:
            fail(f"required staged source absent from clean manifest: "
                 f"{relative}")
        expected.append({
            "path": relative,
            "mode": source["mode"],
            "size": source["size"],
            "sha256": source["sha256"],
            "destinations": [
                {"path": path, "mode": mode}
                for path, mode in destinations
            ],
        })
    return expected


def verify_source_build_lineage(
        files: dict[str, bytes],
        *,
        archive_records: list[dict[str, Any]],
        report: dict[str, Any],
        provisioning: dict[str, Any],
        source: dict[str, Any],
        external_source: dict[str, Any],
        external_manifest_bytes: bytes,
) -> dict[str, Any]:
    if source != external_source:
        fail("bundle clean-source provenance differs from verified inputs")
    manifest, source_index = canonical_source_manifest_index(
        files[CLEAN_SOURCE_MANIFEST],
        external_contents=external_manifest_bytes, source=source)
    lineage = parse_json(
        files[SOURCE_BUILD_LINEAGE], "source/build lineage manifest",
        8 * 1024 * 1024)
    if json_bytes(lineage) != files[SOURCE_BUILD_LINEAGE]:
        fail("source/build lineage manifest is not canonical JSON")
    lineage_sha256 = hashlib.sha256(
        files[SOURCE_BUILD_LINEAGE]).hexdigest()
    if (lineage_sha256 != report["source_build_lineage_sha256"] or
            source["manifest_sha256"] !=
            report["clean_source_manifest_sha256"]):
        fail("bundle report source/build lineage digest mismatch")
    expected_fields = {
        "schema", "variant", "clean_source", "source_manifest",
        "source_tree", "builds", "reviewed_build_sources",
        "staged_sources", "staged_binaries", "formal_ibapi", "boundary",
    }
    if (set(lineage) != expected_fields or
            lineage.get("schema") != contract.SOURCE_BUILD_LINEAGE_SCHEMA or
            lineage.get("variant") != report["variant"] or
            lineage.get("clean_source") != source or
            lineage.get("boundary") != {
                "source_tree_exact": True,
                "source_tree_git_metadata_present": False,
                "build_source_tree_shared": True,
                "repository_staged_sources_match_clean_source": True,
                "formal_ibapi_elf_staged": False,
                "paper_authorized": False,
                "live_enabled": False,
            }):
        fail("source/build lineage manifest contract mismatch")
    source_manifest_record = {
        "path": CLEAN_SOURCE_MANIFEST,
        "schema": manifest["schema"],
        "bundle_class": manifest["bundle_class"],
        "root": manifest["root"],
        "version": source["version"],
        "git_head": source["git_head"],
        "file_count": source["file_count"],
        "files_sha256": source["files_sha256"],
        "bundle_sha256": source["bundle_sha256"],
        "manifest_sha256": source["manifest_sha256"],
    }
    source_tree_record = {
        "root": manifest["root"],
        "file_count": source["file_count"],
        "files_sha256": source["files_sha256"],
        "manifest_sha256": source["manifest_sha256"],
        "internal_manifest_path": ".hepta/source-bundle-manifest.json",
        "internal_manifest_mode": "0644",
        "git_metadata_present": False,
        "exact_file_closure": True,
    }
    if (lineage.get("source_manifest") != source_manifest_record or
            lineage.get("source_tree") != source_tree_record):
        fail("source/build lineage source-tree identity mismatch")
    expected_staged = expected_staged_source_records(source_index)
    if lineage.get("staged_sources") != expected_staged:
        fail("source/build lineage staged-source closure mismatch")
    archive_by_path = {
        record["path"]: record for record in archive_records}
    for record in expected_staged:
        for destination in record["destinations"]:
            payload = files.get(destination["path"])
            if (payload is None or len(payload) != record["size"] or
                    hashlib.sha256(payload).hexdigest() != record["sha256"] or
                    archive_by_path.get(
                        destination["path"], {}).get("mode") !=
                    destination["mode"]):
                fail("staged source bytes differ from clean-source manifest")
    expected_reviewed = [
        source_index[path] for path in contract.REVIEWED_BUILD_SOURCE_PATHS]
    if lineage.get("reviewed_build_sources") != expected_reviewed:
        fail("reviewed build-source lineage mismatch")

    builds = lineage.get("builds")
    if (not isinstance(builds, dict) or
            set(builds) != {"ibapi_on", "ibapi_off"} or
            provisioning.get("builds") != builds):
        fail("source/build lineage build closure mismatch")
    observed_source_roots: set[str] = set()
    ibapi_manifest_binding: Optional[dict[str, Any]] = None
    causal_outputs: dict[str, dict[str, dict[str, Any]]] = {}
    for build_key, ibapi in (("ibapi_on", True), ("ibapi_off", False)):
        record = builds[build_key]
        if not isinstance(record, dict):
            fail("source/build lineage build record is invalid")
        cache_path = contract.BUILD_EVIDENCE_PATHS[
            build_key]["cmake_cache"].as_posix()
        compile_path = contract.BUILD_EVIDENCE_PATHS[
            build_key]["compile_commands"].as_posix()
        cache_bytes = files[cache_path]
        compile_bytes = files[compile_path]
        cache = parse_cmake_cache(cache_bytes, cache_path)
        required_cache = {
            "CMAKE_BUILD_TYPE": "Release",
            "BUILD_TESTING": "ON",
            "CMAKE_EXPORT_COMPILE_COMMANDS": "ON",
            "HEPTA_ENABLE_LEGACY_0DTE_BRIDGE": "OFF",
            "HEPTA_ENABLE_IBAPI": "ON" if ibapi else "OFF",
        }
        if any(cache.get(key, "").upper() != value.upper()
               for key, value in required_cache.items()):
            fail("embedded CMake cache configuration mismatch")
        source_root = cache.get("CMAKE_HOME_DIRECTORY", "")
        build_root = cache.get("CMAKE_CACHEFILE_DIR", "")
        ibapi_root = cache.get("IBAPI_ROOT", "")
        if (not source_root.startswith("/") or
                Path(source_root).name != manifest["root"] or
                not build_root.startswith("/") or
                Path(build_root).name != record.get("path") or
                (ibapi and not ibapi_root.startswith("/")) or
                (not ibapi and ibapi_root)):
            fail("embedded CMake path lineage mismatch")
        observed_source_roots.add(os.path.normpath(source_root))
        ibapi_source_index: Optional[dict[str, dict[str, Any]]] = None
        if ibapi:
            sdk_manifest, ibapi_source_index, sdk_manifest_sha256 = (
                validate_ibapi_source_manifest(
                    record.get("ibapi_source_manifest"),
                    ibapi_root=ibapi_root))
            if (record.get("ibapi_source_manifest_sha256") !=
                    sdk_manifest_sha256 or
                    record.get("ibapi_source_file_count") !=
                    sdk_manifest["file_count"] or
                    record.get("ibapi_source_files_sha256") !=
                    sdk_manifest["files_sha256"]):
                fail("IBAPI SDK source manifest build binding mismatch")
            ibapi_manifest_binding = {
                "ibapi_source_manifest_sha256": sdk_manifest_sha256,
                "ibapi_source_file_count": sdk_manifest["file_count"],
                "ibapi_source_files_sha256": sdk_manifest["files_sha256"],
            }
        elif (record.get("ibapi_source_manifest") is not None or
              record.get("ibapi_source_manifest_sha256") is not None or
              record.get("ibapi_source_file_count") != 0 or
              record.get("ibapi_source_files_sha256") is not None):
            fail("IBAPI-off build must have an empty SDK source binding")
        compile_record = verify_compile_evidence(
            compile_bytes, cache=cache, source_index=source_index,
            ibapi=ibapi, ibapi_source_index=ibapi_source_index)
        expected_keys = {
            "path", "source_root", "source_manifest_sha256",
            "source_files_sha256", "source_file_count",
            "cmake_cache_path", "cmake_cache_sha256",
            "compile_commands_path", "compile_commands_sha256",
            "build_type", "ibapi_enabled", "generator", "compiler",
            "ibapi_source_manifest", "ibapi_source_manifest_sha256",
            "ibapi_source_file_count", "ibapi_source_files_sha256",
            "translation_unit_count",
            "clean_source_translation_unit_count",
            "ibapi_translation_unit_count", "compile_sources_sha256",
            "causal_build",
        }
        if (set(record) != expected_keys or
                record.get("source_root") != manifest["root"] or
                record.get("source_manifest_sha256") !=
                source["manifest_sha256"] or
                record.get("source_files_sha256") != source["files_sha256"] or
                record.get("source_file_count") != source["file_count"] or
                record.get("cmake_cache_path") != cache_path or
                record.get("cmake_cache_sha256") !=
                hashlib.sha256(cache_bytes).hexdigest() or
                record.get("compile_commands_path") != compile_path or
                record.get("compile_commands_sha256") !=
                hashlib.sha256(compile_bytes).hexdigest() or
                record.get("build_type") != "Release" or
                record.get("ibapi_enabled") is not ibapi or
                not isinstance(record.get("generator"), str) or
                not record["generator"] or
                not isinstance(record.get("compiler"), str) or
                not record["compiler"] or
                any(record.get(key) != value
                    for key, value in compile_record.items())):
            fail("source/build lineage build evidence mismatch")
        causal_outputs[build_key] = verify_causal_build_receipt(
            record.get("causal_build"), build_key=build_key, ibapi=ibapi,
            source_manifest_sha256=source["manifest_sha256"],
            ibapi_source_manifest_sha256=(
                record.get("ibapi_source_manifest_sha256") if ibapi
                else None),
            generator=record["generator"])
    if len(observed_source_roots) != 1:
        fail("IBAPI-on/off caches do not share one exact source tree")
    if ibapi_manifest_binding is None:
        fail("IBAPI SDK source manifest binding is missing")

    binaries = lineage.get("staged_binaries")
    if not isinstance(binaries, list) or len(binaries) != 7:
        fail("source/build lineage binary closure mismatch")
    by_artifact: dict[str, dict[str, Any]] = {}
    for record in binaries:
        if (not isinstance(record, dict) or set(record) != {
                "artifact", "build", "build_path", "destinations",
                "mode", "size", "sha256", "cross_build",
                "cross_build_path"} or
                record.get("artifact") in by_artifact or
                record.get("build") not in {"ibapi_on", "ibapi_off"} or
                not isinstance(record.get("build_path"), str) or
                record.get("mode") != "0755" or
                type(record.get("size")) is not int or record["size"] <= 0 or
                HEX_64.fullmatch(record.get("sha256", "")) is None or
                not isinstance(record.get("destinations"), list) or
                not record["destinations"]):
            fail("source/build lineage binary record mismatch")
        by_artifact[record["artifact"]] = record
        for destination in record["destinations"]:
            payload = files.get(destination)
            if (payload is None or
                    archive_by_path.get(destination, {}).get("mode") !=
                    "0755" or
                    len(payload) != record["size"] or
                    hashlib.sha256(payload).hexdigest() != record["sha256"]):
                fail("staged binary bytes differ from build lineage")
    expected_binary_destinations = {
        "hepta-executiond": ["usr/libexec/hepta-executiond"],
        "hepta-ib-executiond-disabled": sorted([
            "usr/local/libexec/hepta-ib-executiond-disabled",
            *(["usr/libexec/hepta-ib-executiond"]
              if report["variant"] != "sandbox" else [])]),
        "hepta_execution_systemd_client_probe": [
            "usr/local/libexec/hepta_execution_systemd_client_probe"],
        "hepta_execution_systemd_sandbox_probe": sorted([
            "usr/local/libexec/hepta_execution_systemd_sandbox_probe",
            *(["usr/libexec/hepta-ib-executiond"]
              if report["variant"] == "sandbox" else [])]),
        "hepta-tool-gatewayd": ["usr/libexec/hepta-tool-gatewayd"],
        "hepta-sessionctl": ["usr/bin/hepta-sessionctl"],
        "heptactl": ["usr/bin/heptactl"],
    }
    if (set(by_artifact) != set(expected_binary_destinations) or
            any(record["destinations"] !=
                expected_binary_destinations[artifact]
                for artifact, record in by_artifact.items())):
        fail("source/build lineage binary destination mapping mismatch")
    for artifact in (
            "hepta-tool-gatewayd", "hepta-sessionctl", "heptactl"):
        record = by_artifact[artifact]
        if (record.get("cross_build") != "ibapi_off" or
                not isinstance(record.get("cross_build_path"), str) or
                not record["cross_build_path"]):
            fail("venue-neutral binary cross-build lineage is missing")
    for artifact, record in by_artifact.items():
        if artifact not in {
                "hepta-tool-gatewayd", "hepta-sessionctl", "heptactl"} and (
                record.get("cross_build") is not None or
                record.get("cross_build_path") is not None):
            fail("unexpected binary cross-build lineage")
        causal = causal_outputs[record["build"]].get(artifact)
        if (causal is None or
                causal["build_path"] != record["build_path"] or
                causal["mode"] != record["mode"] or
                causal["size"] != record["size"] or
                causal["sha256"] != record["sha256"]):
            fail("staged binary is not an exact fresh causal build output")
        if record.get("cross_build") is not None:
            cross = causal_outputs[record["cross_build"]].get(artifact)
            if (cross is None or
                    cross["build_path"] != record["cross_build_path"] or
                    cross["mode"] != record["mode"] or
                    cross["size"] != record["size"] or
                    cross["sha256"] != record["sha256"]):
                fail("cross-build binary lacks exact fresh causal agreement")
    formal = lineage.get("formal_ibapi")
    if (not isinstance(formal, dict) or set(formal) != {
            "artifact", "build", "build_path", "size", "sha256",
            "digest_path", "elf_staged"} or
            formal.get("artifact") != "hepta-ib-executiond" or
            formal.get("build") != "ibapi_on" or
            not isinstance(formal.get("build_path"), str) or
            type(formal.get("size")) is not int or formal["size"] <= 0 or
            HEX_64.fullmatch(formal.get("sha256", "")) is None or
            formal.get("digest_path") != FORMAL_DIGEST or
            formal.get("elf_staged") is not False or
            files[FORMAL_DIGEST] !=
            (formal["sha256"] + "\n").encode("ascii") or
            provisioning.get("formal_ibapi_sha256") != formal["sha256"]):
        fail("formal IBAPI unstaged lineage mismatch")
    formal_causal = causal_outputs["ibapi_on"].get("hepta-ib-executiond")
    if (formal_causal is None or
            formal_causal["build_path"] != formal["build_path"] or
            formal_causal["size"] != formal["size"] or
            formal_causal["sha256"] != formal["sha256"]):
        fail("formal IBAPI ELF lacks exact fresh causal build binding")
    return {
        "source_build_lineage_sha256": lineage_sha256,
        "source_manifest_sha256": source["manifest_sha256"],
        "source_root": manifest["root"],
        "source_file_count": source["file_count"],
        "staged_source_count": len(expected_staged),
        "staged_binary_count": len(binaries),
        "ibapi_on_compile_commands_sha256":
            builds["ibapi_on"]["compile_commands_sha256"],
        "ibapi_off_compile_commands_sha256":
            builds["ibapi_off"]["compile_commands_sha256"],
        **ibapi_manifest_binding,
    }


def validate_manifests(
        files: dict[str, bytes], report: dict[str, Any],
        archive_record: list[dict[str, Any]],
        external_source: dict[str, Any],
        external_manifest_bytes: bytes) -> dict[str, Any]:
    variant = report["variant"]
    image = parse_json(files[IMAGE_MANIFEST], "image manifest", 4 * 1024 * 1024)
    provisioning = parse_json(
        files[PROVISIONING_MANIFEST], "provisioning manifest", 4 * 1024 * 1024)
    policy = parse_json(files[PLATFORM_POLICY], "platform policy", 1024 * 1024)
    source = parse_json(
        files[CLEAN_SOURCE_PROVENANCE], "clean source provenance", 1024 * 1024)
    agent_os = parse_json(
        files[AGENT_OS_INSTALLATION_MANIFEST],
        "Agent OS installation manifest", 4 * 1024 * 1024)
    runtime_inputs = parse_json(
        files[AGENT_OS_RUNTIME_INPUT_MANIFEST],
        "Agent OS runtime input manifest", 4 * 1024 * 1024)
    image_sha256 = hashlib.sha256(files[IMAGE_MANIFEST]).hexdigest()
    provisioning_sha256 = hashlib.sha256(files[PROVISIONING_MANIFEST]).hexdigest()
    policy_sha256 = hashlib.sha256(files[PLATFORM_POLICY]).hexdigest()
    source_sha256 = hashlib.sha256(
        files[CLEAN_SOURCE_PROVENANCE]).hexdigest()
    agent_os_sha256 = hashlib.sha256(
        files[AGENT_OS_INSTALLATION_MANIFEST]).hexdigest()
    runtime_inputs_sha256 = hashlib.sha256(
        files[AGENT_OS_RUNTIME_INPUT_MANIFEST]).hexdigest()
    digest_text = files[IMAGE_DIGEST].decode("ascii", errors="strict")
    variant_text = files[VARIANT_FILE].decode("ascii", errors="strict")
    formal_text = files[FORMAL_DIGEST].decode("ascii", errors="strict")
    if (digest_text != image_sha256 + "\n" or variant_text != variant + "\n" or
            formal_text != provisioning.get("formal_ibapi_sha256", "") + "\n"):
        fail("native VM archive baked metadata mismatch")
    if (image_sha256 != report["vm_image_manifest_sha256"] or
            provisioning_sha256 != report["provisioning_manifest_sha256"] or
            policy_sha256 != report["platform_policy_sha256"] or
            source_sha256 != report["clean_source_provenance_sha256"] or
            agent_os_sha256 !=
            report["agent_os_installation_manifest_sha256"] or
            runtime_inputs_sha256 !=
            report["agent_os_runtime_input_manifest_sha256"] or
            source != report["clean_source"] or
            policy != report["platform_policy"]):
        fail("native VM bundle report manifest binding mismatch")
    contract.validate_platform_policy(files[PLATFORM_POLICY])
    source_expected = {
        "version", "git_head", "file_count", "files_sha256",
        "bundle_sha256", "manifest_sha256", "paper_authorized",
        "live_authorized", "bundle_class",
        "security_manifest_sha256",
        "nonredistributable_vendor_payload_included",
        "prebuilt_payload_included",
    }
    if (set(source) != source_expected or
            type(source.get("file_count")) is not int or
            source["file_count"] <= 0 or
            not isinstance(source.get("version"), str) or
            not source["version"] or
            not isinstance(source.get("git_head"), str) or
            not source["git_head"] or
            source.get("bundle_class") != "strict-source-only" or
            source.get("nonredistributable_vendor_payload_included") is not
            False or source.get("prebuilt_payload_included") is not False or
            any(HEX_64.fullmatch(source.get(key, "")) is None for key in (
                "files_sha256", "bundle_sha256", "manifest_sha256")) or
            not isinstance(source.get("security_manifest_sha256"), str) or
            not source["security_manifest_sha256"].startswith("sha256:") or
            HEX_64.fullmatch(source["security_manifest_sha256"][7:]) is None or
            source.get("paper_authorized") is not False or
            source.get("live_authorized") is not False):
        fail("native VM clean source provenance contract mismatch")

    image_expected = {
        "schema", "variant", "platform_policy_sha256",
        "clean_source_provenance_sha256", "clean_source",
        "provisioning_manifest_sha256",
        "agent_os_installation_manifest_sha256",
        "agent_os_runtime_input_manifest_sha256",
        "agent_os_installation_preflight_staged",
        "agent_os_runtime_gate_inputs_staged",
        "agent_os_runtime_preflight_required",
        "agent_os_runtime_artifacts_staged", "files",
        "formal_ibapi_elf_staged", "instance_identity_staged",
        "paper_authorized", "live_enabled",
    }
    if (set(image) != image_expected or image.get("schema") != contract.IMAGE_SCHEMA or
            image.get("variant") != variant or
            image.get("platform_policy_sha256") != policy_sha256 or
            image.get("clean_source_provenance_sha256") != source_sha256 or
            image.get("clean_source") != source or
            image.get("provisioning_manifest_sha256") != provisioning_sha256 or
            image.get("agent_os_installation_manifest_sha256") !=
            agent_os_sha256 or
            image.get("agent_os_runtime_input_manifest_sha256") !=
            runtime_inputs_sha256 or
            image.get("agent_os_installation_preflight_staged") is not True or
            image.get("agent_os_runtime_gate_inputs_staged") is not True or
            image.get("agent_os_runtime_preflight_required") is not True or
            image.get("agent_os_runtime_artifacts_staged") is not False or
            any(image.get(key) is not False for key in (
                "formal_ibapi_elf_staged", "instance_identity_staged",
                "paper_authorized", "live_enabled")) or
            not isinstance(image.get("files"), list)):
        fail("native VM image manifest contract mismatch")
    expected_records = {
        record["path"]: record for record in archive_record
        if record["path"] not in {IMAGE_MANIFEST, IMAGE_DIGEST}
    }
    manifest_records: dict[str, dict[str, Any]] = {}
    for record in image["files"]:
        if (not isinstance(record, dict) or set(record) != {
                "path", "mode", "uid", "gid", "size", "sha256"} or
                not isinstance(record.get("path"), str) or
                record["path"] in manifest_records):
            fail("native VM image manifest file record mismatch")
        manifest_records[record["path"]] = record
    if manifest_records != expected_records:
        fail("native VM image manifest does not exactly close the archive")

    agent_os_expected = {
        "schema", "profile", "preflight", "files", "runtime",
        "paper_authorized", "live_enabled",
    }
    if (set(agent_os) != agent_os_expected or
            agent_os.get("schema") != contract.AGENT_OS_INSTALLATION_SCHEMA or
            agent_os.get("profile") != "static-installation-only" or
            agent_os.get("preflight") != {
                "path": "/" +
                contract.AGENT_OS_INSTALLATION_PREFLIGHT.as_posix(),
                "arguments": ["--root", "/", "--installation-only"],
            } or
            agent_os.get("runtime") != {
                "tool_socket_staged": False,
                "session_token_staged": False,
                "supervisor_socket_staged": False,
                "runtime_preflight_executed": False,
                "runtime_preflight_required": True,
                "runtime_gate_inputs_staged": True,
                "runtime_input_manifest_sha256": runtime_inputs_sha256,
                "runtime_state_provisioned_by_bundle": False,
                "runtime_sentinel_staged": False,
                "supervisor_credential":
                    "unprovisioned-non-authorizing-placeholder",
            } or
            agent_os.get("paper_authorized") is not False or
            agent_os.get("live_enabled") is not False or
            not isinstance(agent_os.get("files"), list) or
            len(agent_os["files"]) != len(contract.AGENT_OS_STATIC_PATHS)):
        fail("native VM Agent OS installation manifest contract mismatch")

    runtime_expected = {
        "schema", "profile", "inputs", "identities", "watch_tools",
        "read_probes", "lifecycle", "runtime", "paper_authorized",
        "live_enabled", "ib_adapter_runtime_authorized",
    }
    if (set(runtime_inputs) != runtime_expected or
            runtime_inputs.get("schema") !=
            contract.AGENT_OS_RUNTIME_INPUT_SCHEMA or
            runtime_inputs.get("profile") !=
            "native-vm-four-uid-watch-runtime-required" or
            runtime_inputs.get("identities") != {
                "gateway_uid": 2001,
                "simulator_execution_uid": 2002,
                "ib_execution_uid_reserved_not_started": 2003,
                "agent_uid": 2004,
            } or
            runtime_inputs.get("watch_tools") !=
            list(contract.AGENT_OS_WATCH_TOOLS) or
            runtime_inputs.get("read_probes") !=
            list(contract.AGENT_OS_READ_PROBES) or
            runtime_inputs.get("lifecycle") != {
                "service_restart_required": True,
                "socket_restart_required": True,
                "watch_revoke_required": True,
                "runtime_cleanup_required": True,
            } or
            runtime_inputs.get("runtime") != {
                "inner_gate_path":
                    "/" + contract.AGENT_OS_RUNTIME_INNER_GATE.as_posix(),
                "runtime_preflight_executed": False,
                "runtime_preflight_required": True,
                "runtime_state_provisioned_by_bundle": False,
                "runtime_sentinel_staged": False,
                "runtime_artifacts_staged": False,
            } or
            runtime_inputs.get("paper_authorized") is not False or
            runtime_inputs.get("live_enabled") is not False or
            runtime_inputs.get("ib_adapter_runtime_authorized") is not False or
            not isinstance(runtime_inputs.get("inputs"), list) or
            len(runtime_inputs["inputs"]) !=
            len(contract.AGENT_OS_RUNTIME_GATE_PATHS)):
        fail("native VM Agent OS runtime input manifest contract mismatch")
    runtime_records: dict[str, dict[str, Any]] = {}
    for record in runtime_inputs["inputs"]:
        if (not isinstance(record, dict) or set(record) != {
                "path", "mode", "uid", "gid", "size", "sha256"} or
                not isinstance(record.get("path"), str) or
                record["path"] in runtime_records):
            fail("native VM Agent OS runtime input record mismatch")
        runtime_records[record["path"]] = record
    expected_runtime_paths = {
        path.as_posix() for path in contract.AGENT_OS_RUNTIME_GATE_PATHS}
    if (set(runtime_records) != expected_runtime_paths or
            not expected_runtime_paths.issubset(expected_records) or
            any(runtime_records[path] != expected_records[path]
                for path in expected_runtime_paths)):
        fail("native VM Agent OS runtime input closure mismatch")
    agent_os_records: dict[str, dict[str, Any]] = {}
    for record in agent_os["files"]:
        if (not isinstance(record, dict) or set(record) != {
                "path", "mode", "uid", "gid", "size", "sha256"} or
                not isinstance(record.get("path"), str) or
                record["path"] in agent_os_records):
            fail("native VM Agent OS installation file record mismatch")
        agent_os_records[record["path"]] = record
    expected_agent_paths = {
        path.as_posix() for path in contract.AGENT_OS_STATIC_PATHS}
    if (set(agent_os_records) != expected_agent_paths or
            not expected_agent_paths.issubset(expected_records) or
            any(agent_os_records[path] != expected_records[path]
                for path in expected_agent_paths)):
        fail("native VM Agent OS installation file closure mismatch")
    if (files["etc/heptatrader/hepta-supervisor-lease.key"] !=
            contract.agent_os_contract.UNPROVISIONED_SUPERVISOR_LEASE or
            files[contract.AGENT_OS_INSTALLED_PREFLIGHT.as_posix()] !=
            files[contract.AGENT_OS_INSTALLATION_PREFLIGHT.as_posix()] or
            any(not files[path].startswith(b"\x7fELF") for path in (
                "usr/libexec/hepta-tool-gatewayd",
                "usr/bin/hepta-sessionctl", "usr/bin/heptactl"))):
        fail("native VM Agent OS staged payload identity mismatch")

    provisioning_expected = {
        "schema", "variant", "builds", "platform_policy_sha256",
        "clean_source_provenance_sha256", "clean_source",
        "formal_ibapi_sha256", "agent_os_installation_manifest_sha256",
        "agent_os_runtime_input_manifest_sha256",
        "agent_os_installation_preflight_staged",
        "agent_os_runtime_gate_inputs_staged",
        "agent_os_runtime_preflight_required",
        "agent_os_runtime_artifacts_staged", "formal_ibapi_elf_staged",
        "instance_identity_staged", "paper_authorized", "live_enabled",
    }
    if (set(provisioning) != provisioning_expected or
            provisioning.get("schema") != contract.PROVISIONING_SCHEMA or
            provisioning.get("variant") != variant or
            provisioning.get("platform_policy_sha256") != policy_sha256 or
            provisioning.get("clean_source_provenance_sha256") !=
            source_sha256 or provisioning.get("clean_source") != source or
            provisioning.get("agent_os_installation_manifest_sha256") !=
            agent_os_sha256 or
            provisioning.get("agent_os_runtime_input_manifest_sha256") !=
            runtime_inputs_sha256 or
            provisioning.get("agent_os_installation_preflight_staged") is not
            True or
            provisioning.get("agent_os_runtime_gate_inputs_staged") is not
            True or provisioning.get(
                "agent_os_runtime_preflight_required") is not True or
            provisioning.get("agent_os_runtime_artifacts_staged") is not False or
            HEX_64.fullmatch(provisioning.get("formal_ibapi_sha256", "")) is None or
            not isinstance(provisioning.get("builds"), dict) or
            set(provisioning["builds"]) != {"ibapi_on", "ibapi_off"} or
            provisioning["builds"]["ibapi_on"].get("ibapi_enabled") is not True or
            provisioning["builds"]["ibapi_off"].get("ibapi_enabled") is not False or
            any(provisioning.get(key) is not False for key in (
                "formal_ibapi_elf_staged", "instance_identity_staged",
                "paper_authorized", "live_enabled"))):
        fail("native VM provisioning manifest contract mismatch")

    lineage_bindings = verify_source_build_lineage(
        files, archive_records=archive_record, report=report,
        provisioning=provisioning, source=source,
        external_source=external_source,
        external_manifest_bytes=external_manifest_bytes)

    canonical_sha = hashlib.sha256(files[CANONICAL_IB]).hexdigest()
    disabled_sha = hashlib.sha256(files[DISABLED_IB]).hexdigest()
    sandbox_sha = hashlib.sha256(files[SANDBOX_IB]).hexdigest()
    if ((variant == "sandbox" and
         (canonical_sha != sandbox_sha or canonical_sha == disabled_sha)) or
            (variant != "sandbox" and canonical_sha != disabled_sha)):
        fail("native VM canonical IB payload variant closure mismatch")
    return {
        "image_manifest_sha256": image_sha256,
        "provisioning_manifest_sha256": provisioning_sha256,
        "platform_policy_sha256": policy_sha256,
        "clean_source_provenance_sha256": source_sha256,
        "clean_source": source,
        "agent_os_installation_manifest_sha256": agent_os_sha256,
        "agent_os_installation_file_count": len(agent_os_records),
        "agent_os_runtime_input_manifest_sha256": runtime_inputs_sha256,
        "agent_os_runtime_input_file_count": len(runtime_records),
        "agent_os_gateway_sha256": hashlib.sha256(
            files["usr/libexec/hepta-tool-gatewayd"]).hexdigest(),
        "agent_os_sessionctl_sha256": hashlib.sha256(
            files["usr/bin/hepta-sessionctl"]).hexdigest(),
        "formal_ibapi_sha256": provisioning["formal_ibapi_sha256"],
        "canonical_ib_sha256": canonical_sha,
        **lineage_bindings,
    }


def validate_report_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    parent = absolute.parent.resolve(strict=True)
    metadata = os.lstat(parent)
    if (absolute.parent != parent or
            not stat.S_ISDIR(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) & 0o022 or
            re.fullmatch(
                r"hepta-native-vm-(real|sandbox|stub)\.verification\.json",
                absolute.name) is None):
        fail("verification report path is unsafe")
    try:
        target = os.lstat(absolute)
    except FileNotFoundError:
        target = None
    if target is not None and (
            not stat.S_ISREG(target.st_mode) or target.st_nlink != 1 or
            stat.S_IMODE(target.st_mode) != 0o600):
        fail("existing verification report is unsafe")
    return absolute


def verify(args: argparse.Namespace) -> dict[str, Any]:
    try:
        external_source = clean_source.verify_bundle(
            args.clean_source_bundle, args.clean_source_manifest)
        external_manifest_bytes = clean_source.stable_private_bytes(
            args.clean_source_manifest, "external source bundle manifest",
            clean_source.MAX_MANIFEST_BYTES)
    except SystemExit as error:
        fail(str(error))
    report_metadata, report_contents, report_sha256 = safe_file(
        args.bundle_report, 4 * 1024 * 1024)
    report = parse_json(report_contents, "bundle report", 4 * 1024 * 1024)
    validate_bundle_report(report)
    archive_metadata, _archive_contents, archive_sha256 = safe_file(
        args.archive, MAX_ARCHIVE_BYTES)
    if (archive_metadata.st_size != report["archive"]["size"] or
            archive_sha256 != report["archive"]["sha256"]):
        fail("native VM archive does not match the bundle report")
    files, archive_records = read_archive(args.archive)
    if (len(files) != report["rootfs_file_count"]):
        fail("native VM archive file count does not match the bundle report")
    bindings = validate_manifests(
        files, report, archive_records, external_source,
        external_manifest_bytes)
    try:
        final_external_source = clean_source.verify_bundle(
            args.clean_source_bundle, args.clean_source_manifest)
        final_external_manifest = clean_source.stable_private_bytes(
            args.clean_source_manifest, "external source bundle manifest",
            clean_source.MAX_MANIFEST_BYTES)
    except SystemExit as error:
        fail(str(error))
    if (final_external_source != external_source or
            final_external_manifest != external_manifest_bytes):
        fail("clean-source verification inputs changed during verification")
    return {
        "schema": SCHEMA,
        "passed": True,
        "variant": report["variant"],
        "bundle_report": {
            "size": report_metadata.st_size, "sha256": report_sha256},
        "archive": {
            "size": archive_metadata.st_size, "sha256": archive_sha256,
            "file_count": len(files)},
        "bindings": bindings,
        "boundary": report["boundary"],
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="independently verify a native VM rootfs bundle")
    parser.add_argument("--bundle-report", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--clean-source-bundle", type=Path, required=True)
    parser.add_argument("--clean-source-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report_path = validate_report_path(args.report)
        result = verify(args)
        if report_path.name != (
                f"hepta-native-vm-{result['variant']}.verification.json"):
            fail("verification report variant mismatch")
        shared.atomic_report(report_path, result)
    except Exception as error:
        print(f"hepta_native_vm_bundle_verification: FAIL {error}", file=sys.stderr)
        return 1
    print("hepta_native_vm_bundle_verification: PASS "
          f"variant={result['variant']} files={result['archive']['file_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
