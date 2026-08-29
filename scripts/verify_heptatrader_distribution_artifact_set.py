#!/usr/bin/env python3
"""Verify one deterministic, passive HeptaTrader distribution artifact set.

The set is deliberately an offline packaging statement.  It does not grant
PAPER or LIVE authority and it does not make vendor, prebuilt, or broker
payload redistributable.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tarfile
from typing import Any

import heptatrader_secure_artifacts as secure_artifacts

import verify_heptatrader_clean_source_bundle as source_verifier
import verify_heptatrader_runtime_package as runtime_verifier
import verify_heptatrader_vendor_overlay_set as vendor_verifier


SCHEMA = "hepta.distribution-artifact-set.v1"
SCOPE = "local-offline-passive-simulator-runtime"
SOURCE_SCHEMA = "hepta.clean-source-bundle.v2"
VENDOR_SCHEMA = "hepta.vendor-overlay-set.v1"
RUNTIME_SCHEMA = "hepta.runtime-package.v1"
ROLE_ORDER = (
    "strict-source-tar",
    "source-manifest",
    "vendor-overlay-set",
    "runtime-tar",
    "runtime-manifest",
)
ROLE_SUFFIXES = {
    "strict-source-tar": ".tar",
    "source-manifest": ".json",
    "vendor-overlay-set": ".json",
    "runtime-tar": ".tar",
    "runtime-manifest": ".json",
}
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_TAR_BYTES = 1024 * 1024 * 1024
HEX40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,126}$")
FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,254}$")
OVERLAY_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
EXPECTED_OVERLAYS = {
    "ctp-6.5.1-tools": (
        "third_party/ctp/6.5.1-tools",
        "hepta.ctp-tools-overlay-manifest.v1",
    ),
    "ctp-6.7.7": (
        "third_party/ctp/6.7.7",
        "hepta.vendor-asset-manifest.v1",
    ),
    "prebuilt-dependencies": (
        "third_party/prebuilt-dependencies",
        "hepta.prebuilt-asset-manifest.v1",
    ),
}
ARTIFACT_FIELDS = {"role", "filename", "mode", "size", "sha256"}
SET_FIELDS = {
    "schema", "scope", "release_version", "target", "source_ref",
    "vendor_ref", "boundary", "artifacts",
}
SOURCE_REF_FIELDS = {
    "schema", "bundle_sha256", "manifest_sha256", "files_sha256",
    "security_manifest_sha256", "git_head", "root",
}
VENDOR_SOURCE_REF_FIELDS = {
    "bundle_sha256", "manifest_sha256", "files_sha256",
    "security_manifest_sha256", "git_head",
}
VENDOR_FIELDS = {
    "schema", "release_version", "artifact_class", "source_ref",
    "overlay_count", "overlays", "payload_included",
    "distribution_authorized", "required_by_runtime_package_ids",
    "paper_authorized", "live_authorized",
}
OVERLAY_REQUIRED_FIELDS = {
    "overlay_id", "target", "manifest_path", "manifest_schema",
    "manifest_sha256", "declared_asset_count", "declared_payload_count",
    "payload_included", "distribution_authorized",
    "required_by_runtime_package_ids",
}
RUNTIME_FIELDS = {
    "schema", "package_class", "release_version", "root", "source_ref",
    "vendor_ref", "target", "boundary", "file_count", "files_sha256",
    "files",
}
RUNTIME_VENDOR_REF_FIELDS = {
    "schema", "descriptor_sha256", "release_version", "overlay_count",
    "required_overlay_ids",
}
TARGET_FIELDS = {"os", "elf_class", "endian", "machine"}
RUNTIME_BOUNDARY_FIELDS = {
    "components", "build_type", "ibapi_enabled",
    "legacy_0dte_bridge_enabled", "legacy_monolith_enabled",
    "legacy_simulator_enabled", "passive_provisioning",
    "paper_authorized", "live_authorized", "sdk_included",
    "vendor_payload_included", "prebuilt_payload_included",
    "host_state_paths_included",
}
RUNTIME_FILE_FIELDS = {"path", "mode", "size", "sha256", "payload"}
RUNTIME_PRODUCT_FILES: dict[str, int] = dict(runtime_verifier.PRODUCT_FILES)
SET_BOUNDARY = {
    "vendor_payload_included": False,
    "prebuilt_payload_included": False,
    "broker_payload_included": False,
    "paper_authorized": False,
    "live_authorized": False,
}
DENIED_RUNTIME_PARTS = frozenset({
    "third_party", "vendor", "prebuilt", "interface", "tools",
})
DENIED_RUNTIME_BASENAME_FRAGMENTS = (
    "ibapi", "twsapi", "thost", "ctp", "xtp", "xtquote", "xttrader",
)


class DistributionArtifactSetError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    try:
        return secure_artifacts.canonical_json(value)
    except secure_artifacts.SecureArtifactError as error:
        raise DistributionArtifactSetError(
            "value cannot be represented as canonical JSON") from error


def _reject_constant(value: str) -> None:
    raise DistributionArtifactSetError(
        f"non-finite JSON value is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DistributionArtifactSetError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text, object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DistributionArtifactSetError(
            f"{label} is not strict JSON") from error


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid,
    )


def stable_bytes(path: Path, limit: int) -> tuple[os.stat_result, bytes]:
    """Read a private regular file without following path-component links."""
    absolute = Path(os.path.abspath(path))
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
        getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    components: list[tuple[str, tuple[int, ...]]] = []
    try:
        current = os.open("/", directory_flags)
        descriptors.append(current)
        for component in absolute.parent.parts[1:]:
            before = os.stat(
                component, dir_fd=current, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise DistributionArtifactSetError(
                    f"unsafe parent component: {path}")
            child = os.open(component, directory_flags, dir_fd=current)
            if _directory_identity(before) != _directory_identity(
                    os.fstat(child)):
                os.close(child)
                raise DistributionArtifactSetError(
                    f"unstable parent component: {path}")
            components.append((component, _directory_identity(before)))
            descriptors.append(child)
            current = child

        before = os.stat(
            absolute.name, dir_fd=current, follow_symlinks=False)
        mode = stat.S_IMODE(before.st_mode)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid != os.geteuid() or mode & 0o7022):
            raise DistributionArtifactSetError(
                f"unsafe artifact ownership, type, link count, or mode: {path}")
        if before.st_size < 0 or before.st_size > limit:
            raise DistributionArtifactSetError(
                f"artifact exceeds size limit: {path}")

        descriptor = os.open(absolute.name, file_flags, dir_fd=current)
        try:
            opened = os.fstat(descriptor)
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise DistributionArtifactSetError(
                        f"artifact exceeds size limit: {path}")
                chunks.append(chunk)
            after_descriptor = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = os.stat(
            absolute.name, dir_fd=current, follow_symlinks=False)
        identity = _file_identity(before)
        if (identity != _file_identity(opened) or
                identity != _file_identity(after_descriptor) or
                identity != _file_identity(after_path) or
                size != before.st_size):
            raise DistributionArtifactSetError(
                f"artifact changed during read: {path}")
        for index, (component, expected) in enumerate(components):
            current_metadata = os.stat(
                component, dir_fd=descriptors[index],
                follow_symlinks=False)
            if _directory_identity(current_metadata) != expected:
                raise DistributionArtifactSetError(
                    f"parent component changed during read: {path}")
        return opened, b"".join(chunks)
    except DistributionArtifactSetError:
        raise
    except OSError as error:
        raise DistributionArtifactSetError(
            f"unsafe or unstable artifact path: {path}") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _relative_path(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or not value.isascii() or
            "\0" in value or "\\" in value):
        raise DistributionArtifactSetError(
            f"{label} is not a normalized relative path")
    candidate = PurePosixPath(value)
    if (candidate.is_absolute() or ".." in candidate.parts or
            "." in candidate.parts or candidate.as_posix() != value):
        raise DistributionArtifactSetError(
            f"{label} is not a normalized relative path")
    return value


def _token(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value.isascii() or
            TOKEN.fullmatch(value) is None):
        raise DistributionArtifactSetError(f"{label} is invalid")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise DistributionArtifactSetError(f"{label} is not SHA-256")
    return value


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    if (type(value) is not int or value < (1 if positive else 0)):
        raise DistributionArtifactSetError(f"{label} is invalid")
    return value


def _capture_inputs(paths: dict[str, Path]) -> dict[str, tuple[
        Path, os.stat_result, bytes]]:
    if tuple(paths) != ROLE_ORDER:
        raise DistributionArtifactSetError(
            "input roles must exactly match canonical role order")
    absolute_names: set[str] = set()
    filenames: set[str] = set()
    identities: set[tuple[int, int]] = set()
    captured: dict[str, tuple[Path, os.stat_result, bytes]] = {}
    for role in ROLE_ORDER:
        path = Path(os.path.abspath(paths[role]))
        if (FILENAME.fullmatch(path.name) is None or
                not path.name.endswith(ROLE_SUFFIXES[role])):
            raise DistributionArtifactSetError(
                f"{role} filename is unsafe or has the wrong suffix")
        if str(path) in absolute_names or path.name in filenames:
            raise DistributionArtifactSetError(
                "artifact paths and filenames must be unique")
        metadata, data = stable_bytes(
            path, MAX_TAR_BYTES if role.endswith("-tar") else MAX_JSON_BYTES)
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in identities:
            raise DistributionArtifactSetError(
                "artifact inputs must not alias one another")
        absolute_names.add(str(path))
        filenames.add(path.name)
        identities.add(identity)
        captured[role] = (path, metadata, data)
    return captured


def _source_reference(
        manifest: dict[str, Any], bundle_bytes: bytes,
        manifest_bytes: bytes) -> dict[str, Any]:
    version = _token(manifest.get("version"), "source release version")
    root = _relative_path(manifest.get("root"), "source root")
    if "/" in root or root != f"heptatrader-{version}":
        raise DistributionArtifactSetError(
            "source root and release version are inconsistent")
    git_head = manifest.get("git_head")
    if not isinstance(git_head, str) or HEX40.fullmatch(git_head) is None:
        raise DistributionArtifactSetError("source Git identity is invalid")
    files_sha256 = manifest.get("files_sha256")
    security_sha256 = manifest.get("security_manifest_sha256")
    if (not isinstance(files_sha256, str) or
            re.fullmatch(r"[0-9a-f]{64}", files_sha256) is None or
            not isinstance(security_sha256, str) or
            SHA256.fullmatch(security_sha256) is None):
        raise DistributionArtifactSetError(
            "source manifest lineage digests are invalid")
    return {
        "schema": SOURCE_SCHEMA,
        "bundle_sha256": _sha256(bundle_bytes),
        "manifest_sha256": _sha256(manifest_bytes),
        "files_sha256": "sha256:" + files_sha256,
        "security_manifest_sha256": security_sha256,
        "git_head": git_head,
        "root": root,
    }


def _inspect_source(
        bundle_path: Path, manifest_path: Path, bundle_bytes: bytes,
        manifest_bytes: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        report = source_verifier.verify_bundle(bundle_path, manifest_path)
    except (SystemExit, Exception) as error:
        if isinstance(error, DistributionArtifactSetError):
            raise
        raise DistributionArtifactSetError(
            f"strict-source verification failed: {error}") from error
    manifest = strict_json(manifest_bytes, "source manifest")
    if not isinstance(manifest, dict):
        raise DistributionArtifactSetError(
            "source manifest must be an object")
    if (manifest.get("schema") != SOURCE_SCHEMA or
            manifest.get("bundle_class") != "strict-source-only"):
        raise DistributionArtifactSetError(
            "unsupported strict-source manifest")
    source_ref = _source_reference(
        manifest, bundle_bytes, manifest_bytes)
    expected_report = {
        "version": manifest["version"],
        "git_head": manifest["git_head"],
        "files_sha256": manifest["files_sha256"],
        "bundle_sha256": source_ref["bundle_sha256"][7:],
        "manifest_sha256": source_ref["manifest_sha256"][7:],
    }
    if any(report.get(key) != value for key, value in expected_report.items()):
        raise DistributionArtifactSetError(
            "strict-source verifier result does not bind captured inputs")
    for key in (
            "paper_authorized", "live_authorized",
            "nonredistributable_vendor_payload_included",
            "prebuilt_payload_included"):
        if report.get(key) is not False:
            raise DistributionArtifactSetError(
                "strict-source verifier crossed the distribution boundary")
    if (manifest.get("paper_authorized") is not False or
            manifest.get("live_authorized") is not False or
            manifest.get(
                "nonredistributable_vendor_payload_included") is not False or
            manifest.get("prebuilt_payload_included") is not False):
        raise DistributionArtifactSetError(
            "strict-source manifest crossed the distribution boundary")
    records = manifest.get("files")
    if not isinstance(records, list):
        raise DistributionArtifactSetError(
            "source manifest file closure is invalid")
    paths: set[str] = set()
    for record in records:
        if (not isinstance(record, dict) or
                set(record) != {"path", "mode", "size", "sha256"}):
            raise DistributionArtifactSetError(
                "source manifest file record is invalid")
        path = _relative_path(record["path"], "source file path")
        if (path in paths or
                not isinstance(record["sha256"], str) or
                re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None or
                type(record["size"]) is not int or record["size"] < 0 or
                record["mode"] not in {"0644", "0755"}):
            raise DistributionArtifactSetError(
                "source manifest file record metadata is invalid")
        paths.add(path)
    return manifest, source_ref


def _vendor_manifest_payloads(
        bundle_bytes: bytes, root: str,
        overlays: list[dict[str, Any]]) -> dict[str, bytes]:
    wanted = {
        f"{root}/{overlay['manifest_path']}": overlay["manifest_path"]
        for overlay in overlays
    }
    found: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:") as archive:
            for member in archive.getmembers():
                relative = wanted.get(member.name)
                if relative is None:
                    continue
                if (relative in found or not member.isfile() or
                        member.uid != 0 or member.gid != 0 or
                        member.uname != "root" or member.gname != "root" or
                        member.mtime != 0):
                    raise DistributionArtifactSetError(
                        "unsafe or duplicate vendor metadata tar member")
                source = archive.extractfile(member)
                if source is None:
                    raise DistributionArtifactSetError(
                        "vendor metadata tar member is unreadable")
                found[relative] = source.read()
    except (tarfile.TarError, OSError) as error:
        raise DistributionArtifactSetError(
            "strict-source tar cannot supply vendor metadata") from error
    if set(found) != {overlay["manifest_path"] for overlay in overlays}:
        raise DistributionArtifactSetError(
            "vendor metadata is missing from strict-source tar")
    return found


def _declared_counts(document: Any, schema: str) -> tuple[int, int]:
    if not isinstance(document, dict) or document.get("schema") != schema:
        raise DistributionArtifactSetError(
            "vendor metadata manifest schema drift")
    if isinstance(document.get("assets"), list):
        assets = document["assets"]
    elif (isinstance(document.get("canonical_headers"), list) and
          isinstance(document.get("platform_assets"), list)):
        assets = document["canonical_headers"] + document["platform_assets"]
    else:
        raise DistributionArtifactSetError(
            "vendor metadata manifest has no closed asset inventory")
    if any(not isinstance(item, dict) for item in assets):
        raise DistributionArtifactSetError(
            "vendor metadata asset inventory is invalid")
    payload_count = document.get("payload_count", len(assets))
    if type(payload_count) is not int or payload_count < 0:
        raise DistributionArtifactSetError(
            "vendor metadata payload count is invalid")
    for key in (
            "distribution_authorized", "payload_distribution_authorized",
            "paper_authorized", "live_authorized"):
        if key in document and document[key] is not False:
            raise DistributionArtifactSetError(
                "vendor metadata grants forbidden distribution or trading")
    if any(
            item.get("distribution_authorized") is not False
            for item in assets if "distribution_authorized" in item):
        raise DistributionArtifactSetError(
            "vendor asset grants forbidden distribution")
    return len(assets), payload_count


def _inspect_vendor(
        value: Any, data: bytes, source_manifest: dict[str, Any],
        source_ref: dict[str, Any], source_tar: bytes,
        source_tar_path: Path, source_manifest_path: Path,
        vendor_path: Path) -> tuple[
            dict[str, Any], dict[str, Any]]:
    try:
        report = vendor_verifier.verify_vendor_overlay_set(
            source_tar_path, source_manifest_path, vendor_path)
    except Exception as error:
        raise DistributionArtifactSetError(
            f"vendor-overlay-set verification failed: {error}") from error
    if not isinstance(value, dict) or set(value) != VENDOR_FIELDS:
        raise DistributionArtifactSetError(
            "vendor-overlay-set fields do not exactly match schema")
    expected_vendor_source = {
        key: source_ref[key] for key in VENDOR_SOURCE_REF_FIELDS
    }
    if (value["schema"] != VENDOR_SCHEMA or
            value["artifact_class"] != "metadata-only-vendor-overlay-set" or
            value["release_version"] != source_manifest["version"] or
            not isinstance(value["source_ref"], dict) or
            set(value["source_ref"]) != VENDOR_SOURCE_REF_FIELDS or
            value["source_ref"] != expected_vendor_source):
        raise DistributionArtifactSetError(
            "vendor-overlay-set source or release lineage drift")
    if (value["payload_included"] is not False or
            value["distribution_authorized"] is not False or
            value["required_by_runtime_package_ids"] != [] or
            value["paper_authorized"] is not False or
            value["live_authorized"] is not False):
        raise DistributionArtifactSetError(
            "vendor-overlay-set crossed the passive metadata boundary")
    if (not isinstance(report, dict) or
            report.get("schema") != VENDOR_SCHEMA or
            report.get("release_version") != value["release_version"] or
            report.get("overlay_count") != value["overlay_count"] or
            report.get("overlay_set_sha256") != _sha256(data)[7:] or
            report.get("source_ref") != value["source_ref"] or
            report.get("payload_included") is not False or
            report.get("distribution_authorized") is not False or
            report.get("paper_authorized") is not False or
            report.get("live_authorized") is not False):
        raise DistributionArtifactSetError(
            "vendor verifier result does not bind captured descriptor")
    overlays = value["overlays"]
    count = _integer(value["overlay_count"], "vendor overlay count")
    if (not isinstance(overlays, list) or len(overlays) != count or
            count != len(EXPECTED_OVERLAYS)):
        raise DistributionArtifactSetError(
            "vendor-overlay-set closure is incomplete")
    overlay_ids: list[str] = []
    manifest_paths: set[str] = set()
    source_records = {
        record["path"]: record for record in source_manifest["files"]
    }
    for overlay in overlays:
        if (not isinstance(overlay, dict) or
                not OVERLAY_REQUIRED_FIELDS.issubset(overlay)):
            raise DistributionArtifactSetError(
                "vendor overlay record fields are invalid")
        overlay_id = overlay["overlay_id"]
        if (not isinstance(overlay_id, str) or
                OVERLAY_ID.fullmatch(overlay_id) is None or
                overlay_id not in EXPECTED_OVERLAYS):
            raise DistributionArtifactSetError(
                "vendor overlay identity is invalid")
        expected_target, expected_schema = EXPECTED_OVERLAYS[overlay_id]
        target = _relative_path(overlay["target"], "vendor overlay target")
        manifest_path = _relative_path(
            overlay["manifest_path"], "vendor overlay manifest path")
        if (target != expected_target or
                manifest_path != f"{target}/manifest-v1.json" or
                overlay["manifest_schema"] != expected_schema or
                manifest_path in manifest_paths):
            raise DistributionArtifactSetError(
                "vendor overlay target or manifest lineage drift")
        _sha(overlay["manifest_sha256"], "vendor manifest digest")
        _integer(
            overlay["declared_asset_count"], "vendor declared asset count",
            positive=True)
        _integer(
            overlay["declared_payload_count"],
            "vendor declared payload count", positive=True)
        if (overlay["payload_included"] is not False or
                overlay["distribution_authorized"] is not False or
                overlay["required_by_runtime_package_ids"] != []):
            raise DistributionArtifactSetError(
                "vendor overlay crossed the metadata-only boundary")
        source_record = source_records.get(manifest_path)
        if (source_record is None or
                "sha256:" + source_record["sha256"] !=
                overlay["manifest_sha256"]):
            raise DistributionArtifactSetError(
                "vendor overlay is not bound to strict-source metadata")
        overlay_ids.append(overlay_id)
        manifest_paths.add(manifest_path)
    if overlay_ids != sorted(EXPECTED_OVERLAYS):
        raise DistributionArtifactSetError(
            "vendor overlays are not in canonical order")
    payloads = _vendor_manifest_payloads(
        source_tar, source_ref["root"], overlays)
    for overlay in overlays:
        payload = payloads[overlay["manifest_path"]]
        if _sha256(payload) != overlay["manifest_sha256"]:
            raise DistributionArtifactSetError(
                "vendor metadata bytes do not match descriptor")
        document = strict_json(payload, "embedded vendor metadata")
        asset_count, payload_count = _declared_counts(
            document, overlay["manifest_schema"])
        if (asset_count != overlay["declared_asset_count"] or
                payload_count != overlay["declared_payload_count"]):
            raise DistributionArtifactSetError(
                "vendor metadata declared counts drift")
    vendor_ref = {
        "schema": VENDOR_SCHEMA,
        "descriptor_sha256": _sha256(data),
        "release_version": value["release_version"],
        "overlay_count": count,
        "required_overlay_ids": [],
    }
    return value, vendor_ref


def _validate_target(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != TARGET_FIELDS:
        raise DistributionArtifactSetError(
            "runtime target fields are invalid")
    if (value["os"] != "linux" or value["elf_class"] != "ELF64" or
            value["endian"] != "little"):
        raise DistributionArtifactSetError(
            "runtime target is not the supported passive Linux target")
    machine = _token(value["machine"], "runtime target machine")
    if machine not in {"x86_64", "aarch64"}:
        raise DistributionArtifactSetError(
            "runtime target machine is unsupported")
    return {
        "os": "linux", "elf_class": "ELF64",
        "endian": "little", "machine": machine,
    }


def _validate_runtime_boundary(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != RUNTIME_BOUNDARY_FIELDS:
        raise DistributionArtifactSetError(
            "runtime boundary fields do not exactly match schema")
    expected = {
        "components": [
            "hepta-agent-os-runtime", "hepta-execution-runtime",
        ],
        "build_type": "Release",
        "ibapi_enabled": False,
        "legacy_0dte_bridge_enabled": False,
        "legacy_monolith_enabled": False,
        "legacy_simulator_enabled": False,
        "passive_provisioning": True,
        "paper_authorized": False,
        "live_authorized": False,
        "sdk_included": False,
        "vendor_payload_included": False,
        "prebuilt_payload_included": False,
        "host_state_paths_included": False,
    }
    if value != expected:
        raise DistributionArtifactSetError(
            "runtime crossed the passive simulator distribution boundary")


def _validate_runtime_payload(
        payload: Any, path: str, target: dict[str, str]) -> str:
    if not isinstance(payload, dict) or "kind" not in payload:
        raise DistributionArtifactSetError(
            "runtime payload descriptor is invalid")
    kind = payload["kind"]
    if kind == "data":
        if set(payload) != {"kind"}:
            raise DistributionArtifactSetError(
                "runtime data payload fields are invalid")
    elif kind == "python":
        if (set(payload) != {"kind", "shebang"} or
                not isinstance(payload["shebang"], str) or
                not payload["shebang"].isascii() or
                not payload["shebang"].startswith("#!") or
                "\n" in payload["shebang"]):
            raise DistributionArtifactSetError(
                "runtime Python payload descriptor is invalid")
    elif kind == "python-module":
        if set(payload) != {"kind"}:
            raise DistributionArtifactSetError(
                "runtime Python module descriptor is invalid")
    elif kind == "elf":
        expected = {
            "kind", "class", "endian", "machine", "interpreter",
            "needed", "build_id",
        }
        if set(payload) != expected:
            raise DistributionArtifactSetError(
                "runtime ELF payload fields are invalid")
        if (payload["class"] != target["elf_class"] or
                payload["endian"] != target["endian"] or
                payload["machine"] != target["machine"] or
                not isinstance(payload["interpreter"], str) or
                not payload["interpreter"].startswith("/") or
                "\0" in payload["interpreter"] or
                not isinstance(payload["needed"], list) or
                payload["needed"] != sorted(set(payload["needed"])) or
                any(not isinstance(item, str) or not item or "/" in item or
                    "\0" in item for item in payload["needed"]) or
                not isinstance(payload["build_id"], str) or
                re.fullmatch(r"[0-9a-f]{8,128}", payload["build_id"]) is None):
            raise DistributionArtifactSetError(
                "runtime ELF payload lineage is invalid")
        if any(
                fragment in item.lower()
                for item in payload["needed"]
                for fragment in DENIED_RUNTIME_BASENAME_FRAGMENTS):
            raise DistributionArtifactSetError(
                "runtime links a forbidden broker or vendor SDK payload")
    else:
        raise DistributionArtifactSetError(
            "runtime payload kind is unsupported")
    if kind == "elf" and not path.startswith(("usr/bin/", "usr/libexec/")):
        raise DistributionArtifactSetError(
            "runtime ELF payload is outside the executable closure")
    return kind


def _check_payload_bytes(
        data: bytes, payload: dict[str, Any], target: dict[str, str],
        path: str) -> None:
    if (
            path == runtime_verifier.PAPER_IDENTITY_SOURCE_PATH and
            data != runtime_verifier.PAPER_IDENTITY_SOURCE_BYTES):
        raise DistributionArtifactSetError(
            "runtime PAPER identity source is not the exact deny-all default")
    kind = payload["kind"]
    if kind == "python":
        expected = payload["shebang"].encode("ascii") + b"\n"
        if not data.startswith(expected):
            raise DistributionArtifactSetError(
                "runtime Python shebang metadata drift")
    elif kind == "python-module":
        if data.startswith(b"#!"):
            raise DistributionArtifactSetError(
                "runtime Python module became executable source")
        try:
            compile(data, path, "exec")
        except (SyntaxError, UnicodeDecodeError) as error:
            raise DistributionArtifactSetError(
                "runtime Python module is not valid source") from error
    elif kind == "elf":
        if len(data) < 20 or not data.startswith(b"\x7fELF"):
            raise DistributionArtifactSetError(
                "runtime ELF payload is malformed")
        if data[4] != 2 or data[5] != 1:
            raise DistributionArtifactSetError(
                "runtime ELF class or endian drift")
        machine = struct.unpack("<H", data[18:20])[0]
        expected_machine = {"x86_64": 62, "aarch64": 183}[target["machine"]]
        if machine != expected_machine:
            raise DistributionArtifactSetError(
                "runtime ELF machine drift")


def _verify_runtime_tar(
        tar_bytes: bytes, manifest_bytes: bytes, manifest: dict[str, Any],
        target: dict[str, str]) -> None:
    root = manifest["root"]
    internal = f"{root}/.hepta/runtime-package-manifest.json"
    records = {record["path"]: record for record in manifest["files"]}
    seen: set[str] = set()
    internal_count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
            for member in archive.getmembers():
                if (not member.isfile() or member.uid != 0 or member.gid != 0 or
                        member.uname != "root" or member.gname != "root" or
                        member.mtime != 0):
                    raise DistributionArtifactSetError(
                        f"unsafe runtime tar member metadata: {member.name}")
                source = archive.extractfile(member)
                if source is None:
                    raise DistributionArtifactSetError(
                        f"unreadable runtime tar member: {member.name}")
                data = source.read()
                if member.name == internal:
                    internal_count += 1
                    if member.mode != 0o644 or data != manifest_bytes:
                        raise DistributionArtifactSetError(
                            "runtime internal manifest drift")
                    continue
                prefix = root + "/"
                if not member.name.startswith(prefix):
                    raise DistributionArtifactSetError(
                        "runtime tar member escapes canonical root")
                path = _relative_path(
                    member.name[len(prefix):], "runtime tar member")
                record = records.get(path)
                if (record is None or path in seen or
                        member.mode != int(record["mode"], 8) or
                        len(data) != record["size"] or
                        _sha256(data) != record["sha256"]):
                    raise DistributionArtifactSetError(
                        f"runtime tar closure drift: {path}")
                _check_payload_bytes(data, record["payload"], target, path)
                seen.add(path)
    except (tarfile.TarError, OSError) as error:
        raise DistributionArtifactSetError(
            "runtime tar is malformed") from error
    if internal_count != 1 or seen != set(records):
        raise DistributionArtifactSetError(
            "runtime tar closure is incomplete")


def _inspect_runtime(
        value: Any, data: bytes, tar_bytes: bytes,
        source_manifest: dict[str, Any], source_ref: dict[str, Any],
        vendor_value: dict[str, Any], vendor_ref: dict[str, Any]) -> tuple[
            dict[str, str], dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != RUNTIME_FIELDS:
        raise DistributionArtifactSetError(
            "runtime manifest fields do not exactly match schema")
    if data != runtime_verifier.canonical_json(value) + b"\n":
        raise DistributionArtifactSetError(
            "runtime manifest is not canonical JSON")
    if (value["schema"] != RUNTIME_SCHEMA or
            value["package_class"] != "passive-agent-simulator-runtime" or
            value["release_version"] != source_manifest["version"] or
            not isinstance(value["source_ref"], dict) or
            set(value["source_ref"]) != SOURCE_REF_FIELDS or
            value["source_ref"] != source_ref or
            not isinstance(value["vendor_ref"], dict) or
            set(value["vendor_ref"]) != RUNTIME_VENDOR_REF_FIELDS or
            value["vendor_ref"] != vendor_ref or
            value["vendor_ref"]["release_version"] !=
            vendor_value["release_version"]):
        raise DistributionArtifactSetError(
            "runtime source, vendor, or release lineage drift")
    target = _validate_target(value["target"])
    release = value["release_version"]
    expected_root = (
        f"heptatrader-runtime-{release}-linux-{target['machine']}")
    if value["root"] != expected_root:
        raise DistributionArtifactSetError(
            "runtime root/release/target lineage drift")
    _validate_runtime_boundary(value["boundary"])
    files = value["files"]
    count = _integer(value["file_count"], "runtime file count", positive=True)
    if (not isinstance(files, list) or len(files) != count or
            count != len(RUNTIME_PRODUCT_FILES)):
        raise DistributionArtifactSetError(
            "runtime file closure is not the exact "
            f"{len(RUNTIME_PRODUCT_FILES)}-file product surface")
    paths: list[str] = []
    for record in files:
        if not isinstance(record, dict) or set(record) != RUNTIME_FILE_FIELDS:
            raise DistributionArtifactSetError(
                "runtime file record fields are invalid")
        path = _relative_path(record["path"], "runtime file path")
        if (path not in RUNTIME_PRODUCT_FILES or
                record["mode"] !=
                f"{RUNTIME_PRODUCT_FILES.get(path, 0):04o}"):
            raise DistributionArtifactSetError(
                f"runtime contains an unapproved payload path or mode: {path}")
        parts = {part.lower() for part in PurePosixPath(path).parts}
        basename = PurePosixPath(path).name.lower()
        reviewed_validation_helper = (
            path in runtime_verifier.RELEASE_VALIDATION_COMPANION_FILES)
        if (parts & DENIED_RUNTIME_PARTS or
                (not reviewed_validation_helper and
                 any(fragment in basename
                     for fragment in DENIED_RUNTIME_BASENAME_FRAGMENTS))):
            raise DistributionArtifactSetError(
                "vendor, prebuilt, or broker payload path entered runtime")
        if type(record["size"]) is not int or record["size"] < 0:
            raise DistributionArtifactSetError(
                "runtime file mode or size is invalid")
        _sha(record["sha256"], "runtime file digest")
        _validate_runtime_payload(record["payload"], path, target)
        paths.append(path)
    if paths != sorted(set(paths)):
        raise DistributionArtifactSetError(
            "runtime files are not in canonical unique order")
    if set(paths) != set(RUNTIME_PRODUCT_FILES):
        raise DistributionArtifactSetError(
            "runtime product file closure is incomplete")
    expected_files_sha = _sha256(canonical_json(files))
    if value["files_sha256"] != expected_files_sha:
        raise DistributionArtifactSetError(
            "runtime file closure digest drift")
    _verify_runtime_tar(tar_bytes, data, value, target)
    return target, value


def _bind_standalone_runtime_verifier(
        runtime_tar: Path, runtime_manifest: Path,
        tar_bytes: bytes, manifest_bytes: bytes,
        manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        report = runtime_verifier.verify_package(
            runtime_tar, runtime_manifest)
    except runtime_verifier.RuntimePackageError as error:
        raise DistributionArtifactSetError(
            f"standalone runtime verification failed: {error}") from error
    expected = {
        "schema": RUNTIME_SCHEMA,
        "release_version": manifest["release_version"],
        "root": manifest["root"],
        "file_count": len(RUNTIME_PRODUCT_FILES),
        "package_sha256": _sha256(tar_bytes),
        "manifest_sha256": _sha256(manifest_bytes),
        "source_ref": manifest["source_ref"],
        "vendor_ref": manifest["vendor_ref"],
        "target": manifest["target"],
        "boundary": manifest["boundary"],
    }
    if report != expected:
        raise DistributionArtifactSetError(
            "standalone runtime verifier result does not bind captured inputs")
    return report


def _artifact_record(
        role: str, path: Path, metadata: os.stat_result,
        data: bytes) -> dict[str, Any]:
    return {
        "role": role,
        "filename": path.name,
        "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
        "size": len(data),
        "sha256": _sha256(data),
    }


def _confirm_unchanged(
        captured: dict[str, tuple[Path, os.stat_result, bytes]]) -> None:
    for role in ROLE_ORDER:
        path, metadata, expected = captured[role]
        current_metadata, current = stable_bytes(
            path, MAX_TAR_BYTES if role.endswith("-tar") else MAX_JSON_BYTES)
        if (_file_identity(current_metadata) != _file_identity(metadata) or
                current != expected):
            raise DistributionArtifactSetError(
                f"{role} changed during artifact-set verification")


def build_artifact_set(
        strict_source_tar: Path, source_manifest: Path,
        vendor_overlay_set: Path, runtime_tar: Path,
        runtime_manifest: Path) -> dict[str, Any]:
    paths = {
        "strict-source-tar": strict_source_tar,
        "source-manifest": source_manifest,
        "vendor-overlay-set": vendor_overlay_set,
        "runtime-tar": runtime_tar,
        "runtime-manifest": runtime_manifest,
    }
    captured = _capture_inputs(paths)
    source_tar_bytes = captured["strict-source-tar"][2]
    source_manifest_bytes = captured["source-manifest"][2]
    source_value, source_ref = _inspect_source(
        captured["strict-source-tar"][0],
        captured["source-manifest"][0],
        source_tar_bytes, source_manifest_bytes)
    vendor_bytes = captured["vendor-overlay-set"][2]
    vendor_value = strict_json(vendor_bytes, "vendor-overlay-set")
    vendor_value, vendor_ref = _inspect_vendor(
        vendor_value, vendor_bytes, source_value, source_ref,
        source_tar_bytes, captured["strict-source-tar"][0],
        captured["source-manifest"][0],
        captured["vendor-overlay-set"][0])
    runtime_manifest_bytes = captured["runtime-manifest"][2]
    runtime_value = strict_json(
        runtime_manifest_bytes, "runtime manifest")
    target, runtime_value = _inspect_runtime(
        runtime_value, runtime_manifest_bytes,
        captured["runtime-tar"][2],
        source_value, source_ref, vendor_value, vendor_ref)
    _bind_standalone_runtime_verifier(
        captured["runtime-tar"][0],
        captured["runtime-manifest"][0],
        captured["runtime-tar"][2],
        runtime_manifest_bytes, runtime_value)
    artifacts = [
        _artifact_record(role, *captured[role])
        for role in ROLE_ORDER
    ]
    result = {
        "schema": SCHEMA,
        "scope": SCOPE,
        "release_version": source_value["version"],
        "target": target,
        "source_ref": deepcopy(source_ref),
        "vendor_ref": deepcopy(vendor_ref),
        "boundary": deepcopy(SET_BOUNDARY),
        "artifacts": artifacts,
    }
    _confirm_unchanged(captured)
    return result


def verify(
        artifact_set: Path, strict_source_tar: Path, source_manifest: Path,
        vendor_overlay_set: Path, runtime_tar: Path,
        runtime_manifest: Path) -> dict[str, Any]:
    _, data = stable_bytes(artifact_set, MAX_JSON_BYTES)
    value = strict_json(data, "distribution artifact set")
    if not isinstance(value, dict) or set(value) != SET_FIELDS:
        raise DistributionArtifactSetError(
            "distribution artifact-set fields do not exactly match schema")
    if value.get("schema") != SCHEMA or value.get("scope") != SCOPE:
        raise DistributionArtifactSetError(
            "unsupported distribution artifact-set schema or scope")
    artifacts = value.get("artifacts")
    if (not isinstance(artifacts, list) or len(artifacts) != len(ROLE_ORDER) or
            any(not isinstance(item, dict) or set(item) != ARTIFACT_FIELDS
                for item in artifacts) or
            [item["role"] for item in artifacts] != list(ROLE_ORDER)):
        raise DistributionArtifactSetError(
            "distribution artifact roles are not exact and canonical")
    if data != canonical_json(value) + b"\n":
        raise DistributionArtifactSetError(
            "distribution artifact set is not canonical JSON")
    expected = build_artifact_set(
        strict_source_tar, source_manifest, vendor_overlay_set,
        runtime_tar, runtime_manifest)
    if value != expected:
        raise DistributionArtifactSetError(
            "distribution artifact set does not bind the verified inputs")
    return {
        "schema": SCHEMA,
        "scope": SCOPE,
        "release_version": expected["release_version"],
        "target": deepcopy(expected["target"]),
        "artifact_count": len(expected["artifacts"]),
        "artifact_set_sha256": _sha256(data),
        "paper_authorized": False,
        "live_authorized": False,
        "vendor_payload_included": False,
        "prebuilt_payload_included": False,
        "broker_payload_included": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a deterministic HeptaTrader distribution set")
    parser.add_argument("--artifact-set", type=Path, required=True)
    parser.add_argument("--strict-source-tar", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--vendor-overlay-set", type=Path, required=True)
    parser.add_argument("--runtime-tar", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    args = parser.parse_args()
    report = verify(
        args.artifact_set, args.strict_source_tar, args.source_manifest,
        args.vendor_overlay_set, args.runtime_tar, args.runtime_manifest)
    print(
        "PASS: "
        f"{report['release_version']} {report['artifact_count']} artifacts "
        f"artifact_set_sha256={report['artifact_set_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
