#!/usr/bin/env python3
"""Independently verify a metadata-only vendor overlay set and source lineage."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
from typing import Any

import verify_heptatrader_clean_source_bundle as source_verifier
import verify_heptatrader_prebuilt_assets as prebuilt_verifier
import verify_heptatrader_vendor_assets as vendor_verifier
import heptatrader_secure_artifacts as secure_artifacts


SCHEMA = "hepta.vendor-overlay-set.v1"
ARTIFACT_CLASS = "metadata-only-vendor-overlay-set"
MAX_BUNDLE_BYTES = source_verifier.MAX_BUNDLE_BYTES
MAX_SOURCE_MANIFEST_BYTES = source_verifier.MAX_MANIFEST_BYTES
MAX_OVERLAY_SET_BYTES = 4 * 1024 * 1024
MAX_VENDOR_MANIFEST_BYTES = 4 * 1024 * 1024
HEX64 = frozenset("0123456789abcdef")
RELEASE_VERSION_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-")

OVERLAY_SPECS = (
    (
        "ctp-6.5.1-tools",
        "third_party/ctp/6.5.1-tools",
        prebuilt_verifier.LEGACY_CTP_MANIFEST,
        "hepta.ctp-tools-overlay-manifest.v1",
        {"vendor": "CTP", "version": "6.5.1"},
        "archived-legacy-runtime",
    ),
    (
        "ctp-6.7.7",
        "third_party/ctp/6.7.7",
        prebuilt_verifier.CURRENT_CTP_MANIFEST,
        "hepta.vendor-asset-manifest.v1",
        {"vendor": "CTP", "version": "6.7.7"},
        "disabled-experimental",
    ),
    (
        "prebuilt-dependencies",
        "third_party/prebuilt-dependencies",
        prebuilt_verifier.PREBUILT_MANIFEST,
        "hepta.prebuilt-asset-manifest.v1",
        {"vendor": "mixed-legacy", "version": "per-asset"},
        "archived-legacy-prebuilt-inventory",
    ),
)


class VendorOverlaySetVerificationError(RuntimeError):
    """The vendor overlay set does not match its strict-source lineage."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_ref(data: bytes) -> str:
    return "sha256:" + sha256(data)


def _reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VendorOverlaySetVerificationError(
                f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise VendorOverlaySetVerificationError(
        f"non-finite JSON number: {value}")


def strict_json(payload: bytes, label: str) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VendorOverlaySetVerificationError(
            f"{label} is not strict UTF-8 JSON") from error


def canonical_json(document: Any) -> bytes:
    try:
        return secure_artifacts.canonical_json(
            document, pretty=True, trailing_newline=True)
    except secure_artifacts.SecureArtifactError as error:
        raise VendorOverlaySetVerificationError(str(error)) from error


def canonical_relative(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or "\0" in value or
            "\\" in value):
        raise VendorOverlaySetVerificationError(f"{label} is unsafe")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in path.parts)):
        raise VendorOverlaySetVerificationError(f"{label} is unsafe")
    return value


def _valid_sha256(value: Any, *, prefixed: bool) -> bool:
    if not isinstance(value, str):
        return False
    if prefixed:
        if not value.startswith("sha256:"):
            return False
        value = value[7:]
    return (
        len(value) == 64 and
        all(character in HEX64 for character in value)
    )


def _stable_read(path: Path, label: str, maximum: int) -> bytes:
    try:
        return source_verifier.stable_private_bytes(path, label, maximum)
    except SystemExit as error:
        raise VendorOverlaySetVerificationError(str(error)) from error


def _capture_and_verify_source(
        bundle: Path, manifest_path: Path,
) -> tuple[bytes, bytes, dict[str, Any]]:
    bundle_bytes = _stable_read(bundle, "strict-source bundle", MAX_BUNDLE_BYTES)
    manifest_bytes = _stable_read(
        manifest_path, "strict-source manifest",
        MAX_SOURCE_MANIFEST_BYTES)
    manifest = strict_json(manifest_bytes, "strict-source manifest")
    if not isinstance(manifest, dict):
        raise VendorOverlaySetVerificationError(
            "strict-source manifest must be an object")
    try:
        report = source_verifier.verify_bundle(bundle, manifest_path)
    except (SystemExit, json.JSONDecodeError, UnicodeDecodeError,
            tarfile.TarError) as error:
        raise VendorOverlaySetVerificationError(
            "strict-source bundle verification failed") from error
    if (not isinstance(report, dict) or
            report.get("bundle_sha256") != sha256(bundle_bytes) or
            report.get("manifest_sha256") != sha256(manifest_bytes) or
            report.get("files_sha256") != manifest.get("files_sha256") or
            report.get("git_head") != manifest.get("git_head")):
        raise VendorOverlaySetVerificationError(
            "strict-source verification crossed a source lineage")
    if (_stable_read(bundle, "strict-source bundle", MAX_BUNDLE_BYTES) !=
            bundle_bytes or
            _stable_read(
                manifest_path, "strict-source manifest",
                MAX_SOURCE_MANIFEST_BYTES) != manifest_bytes):
        raise VendorOverlaySetVerificationError(
            "strict-source inputs changed during verification")
    return bundle_bytes, manifest_bytes, manifest


def _source_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    expected_boundary_fields = {
        "bundle_class": "strict-source-only",
        "prebuilt_payload_included": False,
        "nonredistributable_vendor_payload_included": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    if any(manifest.get(key) != value
           for key, value in expected_boundary_fields.items()):
        raise VendorOverlaySetVerificationError(
            "strict-source distribution boundary drift")
    version = manifest.get("version")
    if (not isinstance(version, str) or not version or len(version) > 192 or
            any(character not in RELEASE_VERSION_CHARACTERS
                for character in version)):
        raise VendorOverlaySetVerificationError(
            "strict-source release version is invalid")
    root = canonical_relative(manifest.get("root"), "strict-source root")
    if "/" in root or root != f"heptatrader-{version}":
        raise VendorOverlaySetVerificationError(
            "strict-source release/root binding drift")
    files_sha256 = manifest.get("files_sha256")
    security_manifest_sha256 = manifest.get("security_manifest_sha256")
    git_head = manifest.get("git_head")
    if (not _valid_sha256(files_sha256, prefixed=False) or
            not _valid_sha256(security_manifest_sha256, prefixed=True) or
            not isinstance(git_head, str) or len(git_head) != 40 or
            any(character not in HEX64 for character in git_head)):
        raise VendorOverlaySetVerificationError(
            "strict-source source identity is invalid")
    records: dict[str, dict[str, Any]] = {}
    files = manifest.get("files")
    if not isinstance(files, list):
        raise VendorOverlaySetVerificationError(
            "strict-source file closure is invalid")
    for record in files:
        if not isinstance(record, dict) or set(record) != {
                "mode", "path", "sha256", "size"}:
            raise VendorOverlaySetVerificationError(
                "strict-source file record is invalid")
        relative = canonical_relative(
            record["path"], "strict-source file path")
        if relative in records:
            raise VendorOverlaySetVerificationError(
                "strict-source file closure has duplicate paths")
        if (record["mode"] not in {"0644", "0755"} or
                not isinstance(record["size"], int) or
                isinstance(record["size"], bool) or record["size"] < 0 or
                not _valid_sha256(record["sha256"], prefixed=False)):
            raise VendorOverlaySetVerificationError(
                "strict-source file record metadata is invalid")
        records[relative] = record
    return {
        "release_version": version,
        "root": root,
        "files_sha256": files_sha256,
        "security_manifest_sha256": security_manifest_sha256,
        "git_head": git_head,
        "records": records,
    }


def _read_vendor_manifests(
        bundle_bytes: bytes, source: dict[str, Any],
) -> dict[str, tuple[bytes, dict[str, Any]]]:
    wanted = {spec[2] for spec in OVERLAY_SPECS}
    result: dict[str, tuple[bytes, dict[str, Any]]] = {}
    prefix = source["root"] + "/"
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:") as archive:
            for member in archive.getmembers():
                if not member.name.startswith(prefix):
                    continue
                relative = member.name[len(prefix):]
                if relative not in wanted:
                    continue
                if relative in result:
                    raise VendorOverlaySetVerificationError(
                        f"duplicate vendor manifest in bundle: {relative}")
                if (not member.isfile() or member.size < 0 or
                        member.size > MAX_VENDOR_MANIFEST_BYTES):
                    raise VendorOverlaySetVerificationError(
                        f"unsafe vendor manifest member: {relative}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise VendorOverlaySetVerificationError(
                        f"unreadable vendor manifest: {relative}")
                payload = stream.read()
                record = source["records"].get(relative)
                if (len(payload) != member.size or record != {
                        "mode": format(member.mode, "04o"),
                        "path": relative,
                        "sha256": sha256(payload),
                        "size": len(payload)}):
                    raise VendorOverlaySetVerificationError(
                        f"vendor manifest crossed source closure: {relative}")
                document = strict_json(payload, f"vendor manifest {relative}")
                if not isinstance(document, dict):
                    raise VendorOverlaySetVerificationError(
                        f"vendor manifest must be an object: {relative}")
                result[relative] = payload, document
    except (tarfile.TarError, OSError) as error:
        raise VendorOverlaySetVerificationError(
            "strict-source tar is invalid") from error
    if set(result) != wanted:
        raise VendorOverlaySetVerificationError(
            "strict-source vendor manifest closure is incomplete")
    return result


def _expected_overlays(
        manifests: dict[str, tuple[bytes, dict[str, Any]]],
) -> list[dict[str, Any]]:
    legacy = manifests[prebuilt_verifier.LEGACY_CTP_MANIFEST][1]
    current = manifests[prebuilt_verifier.CURRENT_CTP_MANIFEST][1]
    prebuilt = manifests[prebuilt_verifier.PREBUILT_MANIFEST][1]
    try:
        vendor_verifier.validate_manifest_document(current)
        legacy_assets = prebuilt_verifier._verify_legacy_ctp_manifest(
            legacy, current)
        prebuilt_assets = prebuilt_verifier._verify_prebuilt_manifest(prebuilt)
    except (vendor_verifier.VendorVerificationError,
            prebuilt_verifier.PrebuiltVerificationError) as error:
        raise VendorOverlaySetVerificationError(
            "vendor provenance manifest validation failed") from error
    identities = {
        prebuilt_verifier.LEGACY_CTP_MANIFEST: (
            len(legacy_assets), legacy.get("payload_count"),
            legacy.get("distribution_authorized")),
        prebuilt_verifier.CURRENT_CTP_MANIFEST: (
            len(current["canonical_headers"]) +
            len(current["platform_assets"]),
            len(current["canonical_headers"]) +
            len(current["platform_assets"]),
            current.get("distribution_authorized")),
        prebuilt_verifier.PREBUILT_MANIFEST: (
            len(prebuilt_assets), prebuilt.get("payload_count"),
            prebuilt.get("payload_distribution_authorized")),
    }
    result = []
    for (overlay_id, target, manifest_path, manifest_schema, identity,
         capability_status) in OVERLAY_SPECS:
        asset_count, payload_count, authorized = identities[manifest_path]
        payload, document = manifests[manifest_path]
        if (asset_count <= 0 or payload_count != asset_count or
                authorized is not False or
                document.get("schema") != manifest_schema):
            raise VendorOverlaySetVerificationError(
                f"vendor overlay count/policy drift: {overlay_id}")
        result.append({
            "overlay_id": overlay_id,
            "target": target,
            "manifest_path": manifest_path,
            "manifest_schema": manifest_schema,
            "manifest_sha256": sha256_ref(payload),
            "identity": identity,
            "capability_status": capability_status,
            "declared_asset_count": asset_count,
            "declared_payload_count": payload_count,
            "payload_included": False,
            "distribution_authorized": False,
            "required_by_runtime_package_ids": [],
        })
    result.sort(key=lambda record: record["overlay_id"])
    return result


def verify_vendor_overlay_set(
        bundle: Path, manifest_path: Path, overlay_set_path: Path,
) -> dict[str, Any]:
    bundle = Path(os.path.abspath(bundle))
    manifest_path = Path(os.path.abspath(manifest_path))
    overlay_set_path = Path(os.path.abspath(overlay_set_path))
    overlay_bytes = _stable_read(
        overlay_set_path, "vendor overlay set", MAX_OVERLAY_SET_BYTES)
    overlay_set = strict_json(overlay_bytes, "vendor overlay set")
    if not isinstance(overlay_set, dict):
        raise VendorOverlaySetVerificationError(
            "vendor overlay set must be an object")
    bundle_bytes, manifest_bytes, manifest = _capture_and_verify_source(
        bundle, manifest_path)
    source = _source_identity(manifest)
    vendor_manifests = _read_vendor_manifests(bundle_bytes, source)
    expected_overlays = _expected_overlays(vendor_manifests)
    expected = {
        "schema": SCHEMA,
        "release_version": source["release_version"],
        "artifact_class": ARTIFACT_CLASS,
        "source_ref": {
            "bundle_sha256": sha256_ref(bundle_bytes),
            "manifest_sha256": sha256_ref(manifest_bytes),
            "files_sha256": "sha256:" + source["files_sha256"],
            "security_manifest_sha256":
                source["security_manifest_sha256"],
            "git_head": source["git_head"],
        },
        "overlay_count": len(expected_overlays),
        "overlays": expected_overlays,
        "payload_included": False,
        "distribution_authorized": False,
        "required_by_runtime_package_ids": [],
        "paper_authorized": False,
        "live_authorized": False,
    }
    if overlay_set != expected:
        raise VendorOverlaySetVerificationError(
            "vendor overlay set source, count, or boundary drift")
    if overlay_bytes != canonical_json(expected):
        raise VendorOverlaySetVerificationError(
            "vendor overlay set is not canonical deterministic JSON")
    if (_stable_read(
            overlay_set_path, "vendor overlay set",
            MAX_OVERLAY_SET_BYTES) != overlay_bytes or
            _stable_read(bundle, "strict-source bundle", MAX_BUNDLE_BYTES) !=
            bundle_bytes or
            _stable_read(
                manifest_path, "strict-source manifest",
                MAX_SOURCE_MANIFEST_BYTES) != manifest_bytes):
        raise VendorOverlaySetVerificationError(
            "vendor overlay verification inputs changed")
    return {
        "schema": SCHEMA,
        "release_version": source["release_version"],
        "artifact_class": ARTIFACT_CLASS,
        "overlay_count": len(expected_overlays),
        "overlay_set_sha256": sha256(overlay_bytes),
        "source_ref": expected["source_ref"],
        "payload_included": False,
        "distribution_authorized": False,
        "paper_authorized": False,
        "live_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--overlay-set", type=Path, required=True)
    args = parser.parse_args()
    result = verify_vendor_overlay_set(
        args.bundle, args.manifest, args.overlay_set)
    print(
        f"PASS: {result['release_version']} "
        f"{result['overlay_count']} metadata-only overlays "
        f"overlay_set_sha256={result['overlay_set_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
