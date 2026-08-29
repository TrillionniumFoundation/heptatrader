#!/usr/bin/env python3
"""Build a deterministic metadata-only vendor overlay set from strict source."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import tarfile
from typing import Any

import verify_heptatrader_clean_source_bundle as source_verifier
import verify_heptatrader_prebuilt_assets as prebuilt_verifier
import verify_heptatrader_vendor_assets as vendor_verifier
import heptatrader_secure_artifacts as secure_artifacts


SCHEMA = "hepta.vendor-overlay-set.v1"
ARTIFACT_CLASS = "metadata-only-vendor-overlay-set"
MAX_BUNDLE_BYTES = source_verifier.MAX_BUNDLE_BYTES
MAX_MANIFEST_BYTES = source_verifier.MAX_MANIFEST_BYTES
MAX_VENDOR_MANIFEST_BYTES = 4 * 1024 * 1024
HEX64 = frozenset("0123456789abcdef")
RELEASE_VERSION_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-")

OVERLAY_SPECS = (
    {
        "overlay_id": "ctp-6.5.1-tools",
        "target": "third_party/ctp/6.5.1-tools",
        "manifest_path": prebuilt_verifier.LEGACY_CTP_MANIFEST,
        "manifest_schema": "hepta.ctp-tools-overlay-manifest.v1",
        "identity": {"vendor": "CTP", "version": "6.5.1"},
        "capability_status": "archived-legacy-runtime",
    },
    {
        "overlay_id": "ctp-6.7.7",
        "target": "third_party/ctp/6.7.7",
        "manifest_path": prebuilt_verifier.CURRENT_CTP_MANIFEST,
        "manifest_schema": "hepta.vendor-asset-manifest.v1",
        "identity": {"vendor": "CTP", "version": "6.7.7"},
        "capability_status": "disabled-experimental",
    },
    {
        "overlay_id": "prebuilt-dependencies",
        "target": "third_party/prebuilt-dependencies",
        "manifest_path": prebuilt_verifier.PREBUILT_MANIFEST,
        "manifest_schema": "hepta.prebuilt-asset-manifest.v1",
        "identity": {
            "vendor": "mixed-legacy",
            "version": "per-asset",
        },
        "capability_status": "archived-legacy-prebuilt-inventory",
    },
)


class VendorOverlaySetError(RuntimeError):
    """The source bundle or overlay-set boundary is invalid."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_ref(data: bytes) -> str:
    return "sha256:" + sha256(data)


def _reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VendorOverlaySetError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise VendorOverlaySetError(f"non-finite JSON number: {value}")


def strict_json(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VendorOverlaySetError(
            f"{label} is not strict UTF-8 JSON") from error


def canonical_json(document: Any) -> bytes:
    try:
        return secure_artifacts.canonical_json(
            document, pretty=True, trailing_newline=True)
    except secure_artifacts.SecureArtifactError as error:
        raise VendorOverlaySetError(str(error)) from error


def canonical_relative(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or "\0" in value or
            "\\" in value):
        raise VendorOverlaySetError(f"{label} is unsafe")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in path.parts)):
        raise VendorOverlaySetError(f"{label} is unsafe")
    return value


def valid_sha256(value: Any, *, prefixed: bool) -> bool:
    if not isinstance(value, str):
        return False
    digest = value[7:] if prefixed and value.startswith("sha256:") else value
    if prefixed and not value.startswith("sha256:"):
        return False
    return len(digest) == 64 and all(character in HEX64 for character in digest)


def valid_git_head(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 40 and
        all(character in HEX64 for character in value)
    )


def _stable_read(path: Path, label: str, maximum: int) -> bytes:
    try:
        return source_verifier.stable_private_bytes(path, label, maximum)
    except SystemExit as error:
        raise VendorOverlaySetError(str(error)) from error


def _verify_source(
        bundle: Path, manifest_path: Path,
) -> tuple[bytes, bytes, dict[str, Any], dict[str, Any]]:
    """Capture, verify, and re-confirm a single strict-source lineage."""
    bundle_bytes = _stable_read(bundle, "strict-source bundle", MAX_BUNDLE_BYTES)
    manifest_bytes = _stable_read(
        manifest_path, "strict-source manifest", MAX_MANIFEST_BYTES)
    manifest = strict_json(manifest_bytes, "strict-source manifest")
    if not isinstance(manifest, dict):
        raise VendorOverlaySetError(
            "strict-source manifest must be a JSON object")
    try:
        report = source_verifier.verify_bundle(bundle, manifest_path)
    except (SystemExit, json.JSONDecodeError, UnicodeDecodeError,
            tarfile.TarError) as error:
        raise VendorOverlaySetError(
            "strict-source bundle verification failed") from error
    if not isinstance(report, dict):
        raise VendorOverlaySetError(
            "strict-source verifier returned an invalid report")
    expected_report = {
        "bundle_sha256": sha256(bundle_bytes),
        "manifest_sha256": sha256(manifest_bytes),
        "files_sha256": manifest.get("files_sha256"),
        "git_head": manifest.get("git_head"),
    }
    if any(report.get(key) != value for key, value in expected_report.items()):
        raise VendorOverlaySetError(
            "strict-source verification crossed a source lineage")
    if (_stable_read(bundle, "strict-source bundle", MAX_BUNDLE_BYTES) !=
            bundle_bytes or
            _stable_read(
                manifest_path, "strict-source manifest",
                MAX_MANIFEST_BYTES) != manifest_bytes):
        raise VendorOverlaySetError(
            "strict-source inputs changed during verification")
    return bundle_bytes, manifest_bytes, manifest, report


def _validate_source_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    version = manifest.get("version")
    if (not isinstance(version, str) or not version or len(version) > 192 or
            version[0] not in RELEASE_VERSION_CHARACTERS or
            any(character not in RELEASE_VERSION_CHARACTERS
                for character in version)):
        raise VendorOverlaySetError("strict-source release version is invalid")
    root = canonical_relative(manifest.get("root"), "strict-source root")
    if "/" in root or root != f"heptatrader-{version}":
        raise VendorOverlaySetError(
            "strict-source release/root binding drift")
    if (not valid_sha256(manifest.get("files_sha256"), prefixed=False) or
            not valid_sha256(
                manifest.get("security_manifest_sha256"), prefixed=True) or
            not valid_git_head(manifest.get("git_head"))):
        raise VendorOverlaySetError(
            "strict-source source reference is invalid")
    if (manifest.get("bundle_class") != "strict-source-only" or
            manifest.get("prebuilt_payload_included") is not False or
            manifest.get(
                "nonredistributable_vendor_payload_included") is not False or
            manifest.get("paper_authorized") is not False or
            manifest.get("live_authorized") is not False):
        raise VendorOverlaySetError(
            "strict-source distribution boundary drift")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise VendorOverlaySetError(
            "strict-source file closure is invalid")
    records: dict[str, dict[str, Any]] = {}
    for record in files:
        if not isinstance(record, dict) or set(record) != {
                "mode", "path", "sha256", "size"}:
            raise VendorOverlaySetError(
                "strict-source file record is invalid")
        relative = canonical_relative(
            record["path"], "strict-source file path")
        if relative in records:
            raise VendorOverlaySetError(
                "strict-source file closure has duplicate paths")
        if (record["mode"] not in {"0644", "0755"} or
                not isinstance(record["size"], int) or
                isinstance(record["size"], bool) or record["size"] < 0 or
                not valid_sha256(record["sha256"], prefixed=False)):
            raise VendorOverlaySetError(
                "strict-source file metadata is invalid")
        records[relative] = record
    return {
        "version": version,
        "root": root,
        "records": records,
        "files_sha256": manifest["files_sha256"],
        "security_manifest_sha256": manifest["security_manifest_sha256"],
        "git_head": manifest["git_head"],
    }


def _extract_vendor_manifests(
        bundle_bytes: bytes, source: dict[str, Any],
) -> dict[str, tuple[bytes, dict[str, Any]]]:
    wanted = {
        spec["manifest_path"]: spec
        for spec in OVERLAY_SPECS
    }
    extracted: dict[str, tuple[bytes, dict[str, Any]]] = {}
    prefix = source["root"] + "/"
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:") as archive:
            for member in archive.getmembers():
                if not member.name.startswith(prefix):
                    continue
                relative = member.name[len(prefix):]
                if relative not in wanted:
                    continue
                if relative in extracted:
                    raise VendorOverlaySetError(
                        f"duplicate vendor manifest in bundle: {relative}")
                if (not member.isfile() or member.size < 0 or
                        member.size > MAX_VENDOR_MANIFEST_BYTES):
                    raise VendorOverlaySetError(
                        f"unsafe vendor manifest member: {relative}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise VendorOverlaySetError(
                        f"unreadable vendor manifest: {relative}")
                payload = stream.read()
                if len(payload) != member.size:
                    raise VendorOverlaySetError(
                        f"truncated vendor manifest: {relative}")
                record = source["records"].get(relative)
                if record != {
                        "mode": format(member.mode, "04o"),
                        "path": relative,
                        "sha256": sha256(payload),
                        "size": len(payload)}:
                    raise VendorOverlaySetError(
                        f"vendor manifest crossed source closure: {relative}")
                document = strict_json(payload, f"vendor manifest {relative}")
                if not isinstance(document, dict):
                    raise VendorOverlaySetError(
                        f"vendor manifest must be an object: {relative}")
                extracted[relative] = (payload, document)
    except (tarfile.TarError, OSError) as error:
        raise VendorOverlaySetError(
            "strict-source bundle tar is invalid") from error
    if set(extracted) != set(wanted):
        raise VendorOverlaySetError(
            "strict-source vendor manifest closure is incomplete")
    return extracted


def _overlay_records(
        manifests: dict[str, tuple[bytes, dict[str, Any]]],
) -> list[dict[str, Any]]:
    current = manifests[prebuilt_verifier.CURRENT_CTP_MANIFEST][1]
    legacy = manifests[prebuilt_verifier.LEGACY_CTP_MANIFEST][1]
    prebuilt = manifests[prebuilt_verifier.PREBUILT_MANIFEST][1]
    try:
        vendor_verifier.validate_manifest_document(current)
        legacy_assets = prebuilt_verifier._verify_legacy_ctp_manifest(
            legacy, current)
        prebuilt_assets = prebuilt_verifier._verify_prebuilt_manifest(prebuilt)
    except (vendor_verifier.VendorVerificationError,
            prebuilt_verifier.PrebuiltVerificationError) as error:
        raise VendorOverlaySetError(
            "vendor provenance manifest validation failed") from error

    counts = {
        prebuilt_verifier.LEGACY_CTP_MANIFEST: (
            len(legacy_assets), legacy.get("payload_count")),
        prebuilt_verifier.CURRENT_CTP_MANIFEST: (
            len(current["canonical_headers"]) +
            len(current["platform_assets"]),
            len(current["canonical_headers"]) +
            len(current["platform_assets"])),
        prebuilt_verifier.PREBUILT_MANIFEST: (
            len(prebuilt_assets), prebuilt.get("payload_count")),
    }
    authorization = {
        prebuilt_verifier.LEGACY_CTP_MANIFEST:
            legacy.get("distribution_authorized"),
        prebuilt_verifier.CURRENT_CTP_MANIFEST:
            current.get("distribution_authorized"),
        prebuilt_verifier.PREBUILT_MANIFEST:
            prebuilt.get("payload_distribution_authorized"),
    }
    overlays = []
    for spec in OVERLAY_SPECS:
        path = spec["manifest_path"]
        payload, document = manifests[path]
        asset_count, payload_count = counts[path]
        if (not isinstance(asset_count, int) or asset_count <= 0 or
                not isinstance(payload_count, int) or
                isinstance(payload_count, bool) or
                payload_count != asset_count or
                authorization[path] is not False or
                document.get("schema") != spec["manifest_schema"]):
            raise VendorOverlaySetError(
                f"vendor overlay count/policy drift: {spec['overlay_id']}")
        overlays.append({
            "overlay_id": spec["overlay_id"],
            "target": spec["target"],
            "manifest_path": path,
            "manifest_schema": spec["manifest_schema"],
            "manifest_sha256": sha256_ref(payload),
            "identity": spec["identity"],
            "capability_status": spec["capability_status"],
            "declared_asset_count": asset_count,
            "declared_payload_count": payload_count,
            "payload_included": False,
            "distribution_authorized": False,
            "required_by_runtime_package_ids": [],
        })
    overlays.sort(key=lambda record: record["overlay_id"])
    if [record["overlay_id"] for record in overlays] != sorted(
            spec["overlay_id"] for spec in OVERLAY_SPECS):
        raise VendorOverlaySetError("vendor overlay canonical order drift")
    return overlays


def build_vendor_overlay_set(
        bundle: Path, manifest_path: Path,
) -> dict[str, Any]:
    bundle = Path(os.path.abspath(bundle))
    manifest_path = Path(os.path.abspath(manifest_path))
    bundle_bytes, manifest_bytes, manifest, _ = _verify_source(
        bundle, manifest_path)
    source = _validate_source_manifest(manifest)
    vendor_manifests = _extract_vendor_manifests(bundle_bytes, source)
    overlays = _overlay_records(vendor_manifests)
    return {
        "schema": SCHEMA,
        "release_version": source["version"],
        "artifact_class": ARTIFACT_CLASS,
        "source_ref": {
            "bundle_sha256": sha256_ref(bundle_bytes),
            "manifest_sha256": sha256_ref(manifest_bytes),
            "files_sha256": "sha256:" + source["files_sha256"],
            "security_manifest_sha256":
                source["security_manifest_sha256"],
            "git_head": source["git_head"],
        },
        "overlay_count": len(overlays),
        "overlays": overlays,
        "payload_included": False,
        "distribution_authorized": False,
        "required_by_runtime_package_ids": [],
        "paper_authorized": False,
        "live_authorized": False,
    }


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_uid, metadata.st_gid, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _path_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_uid, metadata.st_gid,
    )


def _directory_flags() -> int:
    return (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_parent(path: Path) -> tuple[int, str]:
    absolute = Path(os.path.abspath(path))
    if len(absolute.parts) < 2 or not absolute.name:
        raise VendorOverlaySetError("output path is invalid")
    descriptor = os.open("/", _directory_flags())
    try:
        for component in absolute.parts[1:-1]:
            child = os.open(
                component, _directory_flags(), dir_fd=descriptor)
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                raise VendorOverlaySetError(
                    "output parent is not a directory")
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if (metadata.st_uid != os.geteuid() or
                metadata.st_mode & 0o022):
            raise VendorOverlaySetError(
                "output parent must be caller-owned and not "
                "group/world writable")
        return descriptor, absolute.name
    except OSError as error:
        os.close(descriptor)
        raise VendorOverlaySetError(
            "output parent path is unsafe or unavailable") from error
    except BaseException:
        os.close(descriptor)
        raise


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise VendorOverlaySetError("output write failed")
        offset += written


def _descriptor_sha256(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digestor = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return digestor.hexdigest()
        digestor.update(chunk)


def publish_vendor_overlay_set(
        output: Path, document: dict[str, Any],
) -> tuple[Path, str]:
    payload = canonical_json(document)
    parent, name = _open_parent(output)
    temporary = f".{name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = -1
    existing: tuple[int, ...] | None = None
    try:
        try:
            metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            if (not stat.S_ISREG(metadata.st_mode) or
                    stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1 or
                    stat.S_IMODE(metadata.st_mode) != 0o600 or
                    metadata.st_uid != os.geteuid()):
                raise VendorOverlaySetError(
                    "existing output is not a private regular file")
            existing = _identity(metadata)
        flags = (
            os.O_RDWR | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        if _descriptor_sha256(descriptor) != sha256(payload):
            raise VendorOverlaySetError(
                "vendor overlay set temporary content drift")
        temporary_identity = _path_identity(os.fstat(descriptor))
        try:
            current_metadata = os.stat(
                name, dir_fd=parent, follow_symlinks=False)
            current = _identity(current_metadata)
        except FileNotFoundError:
            current = None
        if current != existing:
            raise VendorOverlaySetError(
                "output destination changed during publication")
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        temporary = ""
        os.fsync(parent)
        if _descriptor_sha256(descriptor) != sha256(payload):
            raise VendorOverlaySetError(
                "published vendor overlay set content drift")
        published = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (_path_identity(published) != temporary_identity or
                not stat.S_ISREG(published.st_mode) or
                published.st_nlink != 1 or
                stat.S_IMODE(published.st_mode) != 0o600):
            raise VendorOverlaySetError(
                "published vendor overlay set identity drift")
        return Path(os.path.abspath(output)), sha256(payload)
    except OSError as error:
        raise VendorOverlaySetError(
            "vendor overlay set publication failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
        os.close(parent)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = build_vendor_overlay_set(args.bundle, args.manifest)
    output, digest = publish_vendor_overlay_set(args.output, document)
    print(f"VENDOR_OVERLAY_SET={output}")
    print(f"VENDOR_OVERLAY_SET_SHA256={digest}")
    print(f"OVERLAYS={document['overlay_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
