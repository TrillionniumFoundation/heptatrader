#!/usr/bin/env python3
"""Verify reviewed local prebuilt overlays without granting distribution rights."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Iterator


PREBUILT_MANIFEST = "third_party/prebuilt-dependencies/manifest-v1.json"
LEGACY_CTP_MANIFEST = "third_party/ctp/6.5.1-tools/manifest-v1.json"
CURRENT_CTP_MANIFEST = "third_party/ctp/6.7.7/manifest-v1.json"
HEX64 = frozenset("0123456789abcdef")

PREBUILT_ASSET_FIELDS = {
    "architecture", "build_role", "component", "distribution_authorized",
    "format", "license_evidence", "license_id", "origin", "origin_url",
    "path", "provenance_status", "repository_import_commit",
    "required_for_agent_os_core", "sha256", "size", "version",
    "version_evidence",
}
EXPECTED_PREBUILT_IDENTITIES = {
    "Interface/IBApi/bin/CSharpAPI.dll": (
        "interactive-brokers-tws-api", "10.30.01.0",
        "LicenseRef-IBKR-TWS-API", "windows-x86-pe32-managed",
        "pe32-dotnet-dll",
    ),
    "Interface/IBApi/bin/TWSLib.dll": (
        "interactive-brokers-tws-api", "10.30.01.0",
        "LicenseRef-IBKR-TWS-API", "windows-x86-pe32-managed",
        "pe32-dotnet-dll",
    ),
    "Interface/IBApi/bin/TwsRtdServer.dll": (
        "interactive-brokers-tws-api", "1.0.0.0",
        "LicenseRef-IBKR-TWS-API", "windows-x86-pe32-managed",
        "pe32-dotnet-dll",
    ),
    "Interface/IBApi/lib/libbid.lib": (
        "intel-bid-decimal-math", "unverified", "NOASSERTION",
        "windows-x86_64", "coff-static-archive",
    ),
    "Interface/lib/Ubuntu/Release/libTinyXml_Linux.a": (
        "tinyxml", "2.6.2", "Zlib", "linux-x86_64",
        "elf-static-archive",
    ),
    "Interface/lib/Ubuntu/Release/libheptaHeptaDLL_Linux.a": (
        "heptaHeptaDLL", "6.5.1_20230109",
        "LicenseRef-HeptaTrader-Repository", "linux-x86_64",
        "elf-static-archive",
    ),
    "Interface/lib/X64/Release/heptaHeptaDLL.lib": (
        "heptaHeptaDLL", "6.5.1_20230109",
        "LicenseRef-HeptaTrader-Repository", "windows-x86_64",
        "coff-static-archive",
    ),
    "Interface/lib/X64/Release/tinyxml.lib": (
        "tinyxml", "2.6.2", "Zlib", "windows-x86_64",
        "coff-static-archive",
    ),
}
EXPECTED_CTP_TOOLS_IDENTITIES = {
    "Tools/thostmduserapi_se.dll": ("windows-x86", "pe32-dll"),
    "Tools/thosttraderapi_se.dll": ("windows-x86", "pe32-dll"),
    "Tools/Centos/thostmduserapi_se.so": (
        "linux-x86_64", "elf64-shared-object"),
    "Tools/Centos/thosttraderapi_se.so": (
        "linux-x86_64", "elf64-shared-object"),
}


class PrebuiltVerificationError(RuntimeError):
    pass


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _unique_json_object(
        pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_relative(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or "\0" in value or
            "\\" in value):
        raise PrebuiltVerificationError(f"{label} is unsafe")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in path.parts)):
        raise PrebuiltVerificationError(f"{label} is unsafe")
    return value


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and
        all(character in HEX64 for character in value)
    )


def _stable_bytes(root: Path, relative: str, limit: int = 64 << 20) -> bytes:
    relative = _canonical_relative(relative, "asset path")
    current = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise PrebuiltVerificationError(
                f"asset is missing: {relative}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise PrebuiltVerificationError(
                f"asset path contains a symlink: {relative}")
        if index + 1 < len(parts):
            if not stat.S_ISDIR(metadata.st_mode):
                raise PrebuiltVerificationError(
                    f"asset parent is not a directory: {relative}")
        elif not stat.S_ISREG(metadata.st_mode):
            raise PrebuiltVerificationError(
                f"asset is not a regular file: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(current, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise PrebuiltVerificationError(
                f"asset size/type is unsafe: {relative}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1 << 20, remaining))
            if not chunk:
                raise PrebuiltVerificationError(
                    f"asset was truncated while reading: {relative}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise PrebuiltVerificationError(
                f"asset grew while reading: {relative}")
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns)
        if identity_before != identity_after:
            raise PrebuiltVerificationError(
                f"asset changed while reading: {relative}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _load_manifest(root: Path, relative: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            _stable_bytes(root, relative, 1 << 20).decode(
                "utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PrebuiltVerificationError(
            f"manifest is not strict UTF-8 JSON: {relative}") from error
    if not isinstance(payload, dict):
        raise PrebuiltVerificationError(f"manifest must be an object: {relative}")
    return payload


def _ar_members(data: bytes) -> Iterator[bytes]:
    if not data.startswith(b"!<arch>\n"):
        raise PrebuiltVerificationError("static archive magic is invalid")
    offset = 8
    while offset < len(data):
        if offset + 60 > len(data):
            raise PrebuiltVerificationError("static archive header is truncated")
        header = data[offset:offset + 60]
        if header[58:60] != b"`\n":
            raise PrebuiltVerificationError("static archive member is invalid")
        try:
            size = int(header[48:58].decode("ascii").strip())
        except (UnicodeDecodeError, ValueError) as error:
            raise PrebuiltVerificationError(
                "static archive member size is invalid") from error
        start = offset + 60
        end = start + size
        if size < 0 or end > len(data):
            raise PrebuiltVerificationError(
                "static archive member escapes archive")
        yield data[start:end]
        offset = end + (end & 1)
    if offset != len(data):
        raise PrebuiltVerificationError("static archive alignment is invalid")


def _require_pe(data: bytes, machine: int) -> None:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise PrebuiltVerificationError("PE payload lacks DOS header")
    pe_offset = int.from_bytes(data[0x3c:0x40], "little")
    if (pe_offset < 0x40 or pe_offset + 26 > len(data) or
            data[pe_offset:pe_offset + 4] != b"PE\0\0" or
            int.from_bytes(data[pe_offset + 4:pe_offset + 6], "little") !=
            machine):
        raise PrebuiltVerificationError("PE architecture/header mismatch")


def _is_elf_x86_64(data: bytes) -> bool:
    return (
        len(data) >= 20 and data[:4] == b"\x7fELF" and data[4] == 2 and
        data[5] == 1 and int.from_bytes(data[18:20], "little") == 62
    )


def _verify_format(data: bytes, format_name: str, version: str) -> None:
    if format_name in {"pe32-dotnet-dll", "pe32-dll"}:
        _require_pe(data, 0x14c)
        if (format_name == "pe32-dotnet-dll" and
                version.encode("utf-16le") not in data):
            raise PrebuiltVerificationError(
                "managed PE version evidence is absent")
        return
    if format_name == "elf64-shared-object":
        if not _is_elf_x86_64(data):
            raise PrebuiltVerificationError("ELF architecture mismatch")
        return
    members = tuple(_ar_members(data))
    if not members:
        raise PrebuiltVerificationError("static archive has no members")
    if format_name == "elf-static-archive":
        if not any(_is_elf_x86_64(member) for member in members):
            raise PrebuiltVerificationError(
                "ELF archive has no x86-64 object")
        return
    if format_name == "coff-static-archive":
        if not any(
                len(member) >= 2 and
                int.from_bytes(member[:2], "little") == 0x8664
                for member in members):
            raise PrebuiltVerificationError(
                "COFF archive has no x86-64 object")
        return
    raise PrebuiltVerificationError("asset format is unsupported")


def _verify_asset_record(
        record: Any,
        expected_identity: tuple[str, ...],
        expected_path: str,
) -> None:
    if not isinstance(record, dict):
        raise PrebuiltVerificationError("asset record must be an object")
    if set(record) != PREBUILT_ASSET_FIELDS:
        raise PrebuiltVerificationError(
            "prebuilt asset record fields do not match schema")
    path = _canonical_relative(record["path"], "prebuilt asset path")
    if path != expected_path:
        raise PrebuiltVerificationError("prebuilt asset order/path drift")
    actual_identity = (
        record["component"], record["version"], record["license_id"],
        record["architecture"], record["format"])
    if actual_identity != expected_identity:
        raise PrebuiltVerificationError(
            f"prebuilt asset identity drift: {path}")
    if (record["distribution_authorized"] is not False or
            record["required_for_agent_os_core"] is not False or
            record["origin"] not in {
                "legacy-repository-import",
                "legacy-heptadll-build-import"} or
            record["origin_url"] is not None or
            record["provenance_status"] not in {
                "upstream-package-unverified",
                "upstream-package-and-version-unverified",
                "binary-rebuild-provenance-unverified"}):
        raise PrebuiltVerificationError(
            f"prebuilt authorization/provenance drift: {path}")
    if (not isinstance(record["repository_import_commit"], str) or
            len(record["repository_import_commit"]) != 40 or
            any(character not in HEX64
                for character in record["repository_import_commit"])):
        raise PrebuiltVerificationError(
            f"prebuilt import identity is invalid: {path}")
    if (not isinstance(record["size"], int) or
            isinstance(record["size"], bool) or record["size"] <= 0 or
            not _valid_digest(record["sha256"])):
        raise PrebuiltVerificationError(
            f"prebuilt content identity is invalid: {path}")
    for field in ("build_role", "component", "license_id", "origin",
                  "provenance_status", "version"):
        if not isinstance(record[field], str) or not record[field]:
            raise PrebuiltVerificationError(
                f"prebuilt {field} is invalid: {path}")
    for field in ("license_evidence", "version_evidence"):
        value = record[field]
        if value is not None:
            _canonical_relative(value, f"prebuilt {field}")


def _verify_prebuilt_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if set(manifest) != {
            "assets", "default_source_distribution", "manifest_role",
            "payload_count", "payload_distribution_authorized", "schema"}:
        raise PrebuiltVerificationError(
            "prebuilt manifest fields do not match schema")
    if (manifest["schema"] != "hepta.prebuilt-asset-manifest.v1" or
            manifest["manifest_role"] !=
            "reviewed-local-nonredistributable-overlay" or
            manifest["default_source_distribution"] != "metadata-only" or
            manifest["payload_distribution_authorized"] is not False):
        raise PrebuiltVerificationError("prebuilt manifest policy drift")
    assets = manifest["assets"]
    expected_paths = tuple(EXPECTED_PREBUILT_IDENTITIES)
    if (not isinstance(assets, list) or
            manifest["payload_count"] != len(expected_paths) or
            len(assets) != len(expected_paths)):
        raise PrebuiltVerificationError("prebuilt asset closure is incomplete")
    for record, path in zip(assets, expected_paths):
        _verify_asset_record(
            record, EXPECTED_PREBUILT_IDENTITIES[path], path)
    return assets


def _verify_legacy_ctp_manifest(
        manifest: dict[str, Any],
        current_manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    expected_fields = {
        "assets", "capability_status", "distribution_authorized",
        "license_file", "license_review_required", "origin", "origin_url",
        "payload_count", "repository_import_commit", "schema",
        "separate_from", "vendor", "version", "version_marker",
    }
    if set(manifest) != expected_fields:
        raise PrebuiltVerificationError(
            "legacy CTP manifest fields do not match schema")
    if (manifest["schema"] != "hepta.ctp-tools-overlay-manifest.v1" or
            manifest["vendor"] != "CTP" or manifest["version"] != "6.5.1" or
            manifest["version_marker"] !=
            "v6.5.1_20200908 10:25:08" or
            manifest["capability_status"] != "archived-legacy-runtime" or
            manifest["distribution_authorized"] is not False or
            manifest["license_file"] is not None or
            manifest["license_review_required"] is not True or
            manifest["origin"] != "legacy-repository-import" or
            manifest["origin_url"] is not None or
            manifest["separate_from"] != CURRENT_CTP_MANIFEST):
        raise PrebuiltVerificationError("legacy CTP policy/version drift")
    if (current_manifest.get("schema") != "hepta.vendor-asset-manifest.v1" or
            current_manifest.get("vendor") != "CTP" or
            current_manifest.get("version") != "6.7.7"):
        raise PrebuiltVerificationError("current CTP manifest identity drift")
    current_paths = {
        record.get("path")
        for record in (
            current_manifest.get("canonical_headers", []) +
            current_manifest.get("platform_assets", []))
        if isinstance(record, dict)
    }
    if any(
            isinstance(path, str) and path.startswith("Tools/")
            for path in current_paths):
        raise PrebuiltVerificationError(
            "CTP 6.5.1 Tools payload entered 6.7.7 manifest")
    assets = manifest["assets"]
    expected_paths = tuple(EXPECTED_CTP_TOOLS_IDENTITIES)
    if (not isinstance(assets, list) or
            manifest["payload_count"] != len(expected_paths) or
            len(assets) != len(expected_paths)):
        raise PrebuiltVerificationError(
            "legacy CTP asset closure is incomplete")
    for record, path in zip(assets, expected_paths):
        if (not isinstance(record, dict) or set(record) != {
                "architecture", "format", "path", "sha256", "size"} or
                record["path"] != path or
                (record["architecture"], record["format"]) !=
                EXPECTED_CTP_TOOLS_IDENTITIES[path] or
                not _valid_digest(record["sha256"]) or
                not isinstance(record["size"], int) or
                isinstance(record["size"], bool) or record["size"] <= 0):
            raise PrebuiltVerificationError(
                f"legacy CTP asset identity drift: {path}")
        _canonical_relative(path, "legacy CTP asset path")
        if path in current_paths:
            raise PrebuiltVerificationError(
                "CTP 6.5.1 and 6.7.7 asset closures overlap")
    return assets


def _path_lexists(root: Path, relative: str) -> bool:
    return os.path.lexists(root / relative)


def verify(root: Path, payload_mode: str = "auto") -> dict[str, Any]:
    root = root.resolve(strict=True)
    if payload_mode not in {"auto", "present", "absent"}:
        raise PrebuiltVerificationError("unsupported payload mode")
    prebuilt_manifest = _load_manifest(root, PREBUILT_MANIFEST)
    ctp_tools_manifest = _load_manifest(root, LEGACY_CTP_MANIFEST)
    current_ctp_manifest = _load_manifest(root, CURRENT_CTP_MANIFEST)
    prebuilt_assets = _verify_prebuilt_manifest(prebuilt_manifest)
    ctp_tools_assets = _verify_legacy_ctp_manifest(
        ctp_tools_manifest, current_ctp_manifest)
    all_records = prebuilt_assets + ctp_tools_assets
    present = [
        _path_lexists(root, record["path"])
        for record in all_records
    ]
    effective_mode = payload_mode
    if payload_mode == "auto":
        if all(present):
            effective_mode = "present"
        elif not any(present):
            effective_mode = "absent"
        else:
            raise PrebuiltVerificationError(
                "prebuilt overlay is only partially present")
    if effective_mode == "present" and not all(present):
        raise PrebuiltVerificationError(
            "required local prebuilt overlay is incomplete")
    if effective_mode == "absent" and any(present):
        raise PrebuiltVerificationError(
            "prebuilt payload entered metadata-only distribution")
    if effective_mode == "present":
        version_marker = ctp_tools_manifest["version_marker"].encode()
        for record in all_records:
            data = _stable_bytes(root, record["path"])
            if (len(data) != record["size"] or
                    hashlib.sha256(data).hexdigest() != record["sha256"]):
                raise PrebuiltVerificationError(
                    f"prebuilt content drift: {record['path']}")
            version = (
                record.get("version", ctp_tools_manifest["version"]))
            _verify_format(data, record["format"], version)
            if (record in ctp_tools_assets and version_marker not in data):
                raise PrebuiltVerificationError(
                    f"legacy CTP version marker drift: {record['path']}")
        tinyxml_header = _stable_bytes(root, "Interface/include/tinyxml.h")
        if not all(marker in tinyxml_header for marker in (
                b"TIXML_MAJOR_VERSION = 2",
                b"TIXML_MINOR_VERSION = 6",
                b"TIXML_PATCH_VERSION = 2")):
            raise PrebuiltVerificationError(
                "TinyXML source version evidence drift")
        hepta_version = _stable_bytes(
            root, "Interface/include/heptaVersion.h")
        if b'"6.5.1_20230109"' not in hepta_version:
            raise PrebuiltVerificationError(
                "heptaHeptaDLL source version evidence drift")
    return {
        "schema": "hepta.prebuilt-verification.v1",
        "payload_mode": effective_mode,
        "prebuilt_asset_count": len(prebuilt_assets),
        "legacy_ctp_asset_count": len(ctp_tools_assets),
        "distribution_authorized": False,
        "agent_os_core_requires_prebuilt": False,
        "ctp_versions_separate": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--payload-mode", choices=("auto", "present", "absent"),
        default="auto")
    args = parser.parse_args()
    try:
        result = verify(args.root, args.payload_mode)
    except PrebuiltVerificationError as error:
        print(f"prebuilt-assets: {error}", file=os.sys.stderr)
        return 1
    print(
        "PASS: prebuilt-assets "
        f"mode={result['payload_mode']} "
        f"assets={result['prebuilt_asset_count']} "
        f"legacy_ctp={result['legacy_ctp_asset_count']} "
        "distribution_authorized=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
