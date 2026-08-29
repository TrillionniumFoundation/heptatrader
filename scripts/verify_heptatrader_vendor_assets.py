#!/usr/bin/env python3
"""Fail-closed verification for reviewed disabled-experimental vendor assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
from typing import Any

import converge_ctp_vendor_headers as convergence


class VendorVerificationError(RuntimeError):
    pass


PLATFORM_ASSETS = {
    "Interface/CTPTradeApi32": (
        "error.dtd",
        "error.xml",
        "thostmduserapi_se.dll",
        "thostmduserapi_se.lib",
        "thosttraderapi_se.dll",
        "thosttraderapi_se.lib",
        "版本.txt",
    ),
    "Interface/CTPTradeApi64": (
        "error.dtd",
        "error.xml",
        "thostmduserapi_se.dll",
        "thostmduserapi_se.lib",
        "thosttraderapi_se.dll",
        "thosttraderapi_se.lib",
        "版本.txt",
    ),
    "Interface/CTPTradeApiLinux": (
        "error.dtd",
        "error.xml",
        "thostmduserapi_se.so",
        "thosttraderapi_se.so",
        "version.txt",
    ),
}
PLATFORM_NAMES = {
    "Interface/CTPTradeApi32": "windows-x86",
    "Interface/CTPTradeApi64": "windows-x86_64",
    "Interface/CTPTradeApiLinux": "linux-x86_64",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


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


def stable_asset(
        root: Path, relative: str) -> tuple[int, str, bytes]:
    _, data = convergence.stable_relative_asset(root, relative)
    return len(data), hashlib.sha256(data).hexdigest(), data


def stable_digest(root: Path, relative: str) -> tuple[int, str]:
    size, digest, _ = stable_asset(root, relative)
    return size, digest


def _safe_relative(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or "\0" in value or
            "\\" in value):
        raise VendorVerificationError(f"invalid {label} path")
    path = Path(value)
    if (path.is_absolute() or ".." in path.parts or "." in path.parts or
            path.as_posix() != value):
        raise VendorVerificationError(f"invalid {label} path")
    return value


def _verify_binary_architecture(path: Path, platform: str, data: bytes) -> None:
    suffix = path.suffix.lower()
    if suffix == ".dll":
        if len(data) < 0x40 or data[:2] != b"MZ":
            raise VendorVerificationError("vendor DLL lacks PE header")
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if offset + 6 > len(data) or data[offset:offset + 4] != b"PE\0\0":
            raise VendorVerificationError("vendor DLL PE header is invalid")
        machine = struct.unpack_from("<H", data, offset + 4)[0]
        expected = 0x014C if platform == "windows-x86" else 0x8664
        if machine != expected:
            raise VendorVerificationError("vendor DLL architecture mismatch")
    elif suffix == ".so":
        if (len(data) < 20 or data[:4] != b"\x7fELF" or
                data[4] != 2 or data[5] != 1 or
                struct.unpack_from("<H", data, 18)[0] != 62):
            raise VendorVerificationError("vendor SO architecture mismatch")


def validate_manifest_document(manifest: Any) -> dict[str, Any]:
    """Validate the complete metadata-only CTP 6.7.7 provenance contract."""
    required = {
        "schema", "vendor", "version", "distribution_origin", "origin_url",
        "license_file", "license_review_required", "distribution_authorized",
        "capability_status", "paper_authorized", "live_authorized",
        "canonical_headers", "compatibility_include_directories",
        "platform_assets",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise VendorVerificationError(
            "vendor manifest fields do not exactly match schema")
    if (manifest["schema"] != "hepta.vendor-asset-manifest.v1" or
            manifest["vendor"] != "CTP" or manifest["version"] != "6.7.7"):
        raise VendorVerificationError("unsupported vendor manifest")
    if (manifest["distribution_origin"] != "legacy repository import" or
            manifest["origin_url"] is not None or
            manifest["license_file"] is not None or
            manifest["capability_status"] != "disabled-experimental" or
            manifest["distribution_authorized"] is not False or
            manifest["license_review_required"] is not True or
            manifest["paper_authorized"] is not False or
            manifest["live_authorized"] is not False):
        raise VendorVerificationError("vendor authorization boundary drift")
    if (not isinstance(manifest["canonical_headers"], list) or
            not isinstance(manifest["platform_assets"], list) or
            not isinstance(
                manifest["compatibility_include_directories"], list)):
        raise VendorVerificationError("vendor manifest arrays are invalid")

    expected_headers = tuple(
        f"{convergence.CANONICAL_DIRECTORY}/{name}"
        for name in convergence.HEADER_NAMES)
    header_paths = []
    for record in manifest["canonical_headers"]:
        if not isinstance(record, dict) or set(record) != {
                "path", "size", "sha256"}:
            raise VendorVerificationError("invalid canonical header record")
        relative = _safe_relative(record["path"], "canonical header")
        if (not isinstance(record["size"], int) or
                isinstance(record["size"], bool) or record["size"] <= 0 or
                not isinstance(record["sha256"], str) or
                HEX64.fullmatch(record["sha256"]) is None):
            raise VendorVerificationError(
                "invalid canonical header metadata")
        header_paths.append(relative)
    if tuple(header_paths) != expected_headers:
        raise VendorVerificationError("canonical header set drift")
    if tuple(manifest["compatibility_include_directories"]) != (
            convergence.PLATFORM_DIRECTORIES):
        raise VendorVerificationError("compatibility include matrix drift")

    expected_asset_paths = tuple(
        f"{directory}/{name}"
        for directory in convergence.PLATFORM_DIRECTORIES
        for name in PLATFORM_ASSETS[directory])
    manifest_asset_paths = []
    for record in manifest["platform_assets"]:
        if not isinstance(record, dict) or set(record) != {
                "path", "platform", "size", "sha256"}:
            raise VendorVerificationError("invalid platform asset record")
        relative = _safe_relative(record["path"], "platform asset")
        manifest_asset_paths.append(relative)
        parent = Path(relative).parent.as_posix()
        if (parent not in PLATFORM_ASSETS or
                record["platform"] != PLATFORM_NAMES[parent] or
                not isinstance(record["size"], int) or
                isinstance(record["size"], bool) or record["size"] <= 0 or
                not isinstance(record["sha256"], str) or
                HEX64.fullmatch(record["sha256"]) is None):
            raise VendorVerificationError("invalid platform asset metadata")
        if "x64/Release" in relative:
            raise VendorVerificationError("IDE output entered vendor manifest")
    if tuple(manifest_asset_paths) != expected_asset_paths:
        raise VendorVerificationError(
            "vendor platform asset closure is incomplete")
    return manifest


def load_manifest(root: Path, manifest_relative: str) -> dict[str, Any]:
    try:
        _, _, manifest_bytes = stable_asset(root, manifest_relative)
        manifest = json.loads(manifest_bytes.decode(
            "utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise VendorVerificationError(
            "vendor manifest is not strict UTF-8 JSON") from error
    return validate_manifest_document(manifest)


def verify(
        root: Path, manifest_path: Path,
        require_payload: bool = True) -> dict[str, Any]:
    root = Path(os.path.abspath(root))
    manifest_absolute = Path(os.path.abspath(manifest_path))
    try:
        manifest_relative = manifest_absolute.relative_to(root).as_posix()
    except ValueError as error:
        raise VendorVerificationError(
            "vendor manifest escapes repository root") from error
    manifest_relative = _safe_relative(
        manifest_relative, "vendor manifest")
    manifest = load_manifest(root, manifest_relative)
    expected_headers = tuple(
        f"{convergence.CANONICAL_DIRECTORY}/{name}"
        for name in convergence.HEADER_NAMES)
    header_names = []
    header_paths = []
    for record in manifest["canonical_headers"]:
        if set(record) != {"path", "size", "sha256"}:
            raise VendorVerificationError("invalid canonical header record")
        relative = _safe_relative(record["path"], "canonical header")
        if (not isinstance(record["size"], int) or
                isinstance(record["size"], bool) or record["size"] <= 0 or
                not isinstance(record["sha256"], str) or
                HEX64.fullmatch(record["sha256"]) is None):
            raise VendorVerificationError(
                "invalid canonical header metadata")
        header_paths.append(relative)
        header_names.append(Path(relative).name)
        if require_payload:
            size, digest = stable_digest(root, relative)
            if (size, digest) != (record["size"], record["sha256"]):
                raise VendorVerificationError("canonical header digest drift")
    if (tuple(header_names) != convergence.HEADER_NAMES or
            tuple(header_paths) != expected_headers):
        raise VendorVerificationError("canonical header set drift")
    if tuple(manifest["compatibility_include_directories"]) != (
            convergence.PLATFORM_DIRECTORIES):
        raise VendorVerificationError("compatibility include matrix drift")
    if require_payload:
        convergence.converge(root, apply=False)
        canonical_entries = convergence.stable_relative_directory(
            root, convergence.CANONICAL_DIRECTORY)
        if set(canonical_entries) != set(convergence.HEADER_NAMES):
            raise VendorVerificationError(
                "vendor canonical include directory closure drift")
        for name, metadata in canonical_entries.items():
            if not convergence.safe_regular_asset(metadata):
                raise VendorVerificationError(
                    f"vendor canonical header is unsafe: {name}")

    expected_asset_paths = tuple(
        f"{directory}/{name}"
        for directory in convergence.PLATFORM_DIRECTORIES
        for name in PLATFORM_ASSETS[directory])
    manifest_asset_paths = []
    for record in manifest["platform_assets"]:
        if set(record) != {"path", "platform", "size", "sha256"}:
            raise VendorVerificationError("invalid platform asset record")
        relative = _safe_relative(record["path"], "platform asset")
        manifest_asset_paths.append(relative)
        parent = Path(relative).parent.as_posix()
        if (parent not in PLATFORM_ASSETS or
                record["platform"] != PLATFORM_NAMES[parent] or
                not isinstance(record["size"], int) or
                isinstance(record["size"], bool) or record["size"] <= 0 or
                not isinstance(record["sha256"], str) or
                HEX64.fullmatch(record["sha256"]) is None):
            raise VendorVerificationError("invalid platform asset metadata")
        if "x64/Release" in relative:
            raise VendorVerificationError("IDE output entered vendor manifest")
        if require_payload:
            path = Path(relative)
            size, digest, data = stable_asset(root, relative)
            if (size, digest) != (record["size"], record["sha256"]):
                raise VendorVerificationError("platform asset digest drift")
            _verify_binary_architecture(path, record["platform"], data)
            if path.name in {"version.txt", "版本.txt"} and data != b"6.7.7":
                raise VendorVerificationError(
                    "vendor version evidence mismatch")
    if tuple(manifest_asset_paths) != expected_asset_paths:
        raise VendorVerificationError(
            "vendor platform asset closure is incomplete")

    if require_payload:
        expected_files = {
            *(f"{directory}/{name}"
              for directory in convergence.PLATFORM_DIRECTORIES
              for name in convergence.HEADER_NAMES),
            *expected_asset_paths,
        }
        discovered = set()
        for directory in convergence.PLATFORM_DIRECTORIES:
            for name, metadata in convergence.stable_relative_directory(
                    root, directory).items():
                if (stat.S_ISLNK(metadata.st_mode) or
                        not stat.S_ISREG(metadata.st_mode)):
                    raise VendorVerificationError(
                        f"unexpected vendor directory entry: "
                        f"{directory}/{name}")
                discovered.add(f"{directory}/{name}")
        if discovered != expected_files:
            raise VendorVerificationError(
                "vendor platform directory closure drift")

    version_directory = "third_party/ctp/6.7.7"
    version_entries = convergence.stable_relative_directory(
        root, version_directory)
    expected_version_entries = {
        "README.md", "manifest-v1.json",
        *(("include",) if require_payload else ()),
    }
    if set(version_entries) != expected_version_entries:
        raise VendorVerificationError(
            "vendor version directory closure drift")
    for name, metadata in version_entries.items():
        if name == "include":
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise VendorVerificationError(
                    "vendor canonical include directory is unsafe")
        elif not convergence.safe_regular_asset(metadata):
            raise VendorVerificationError(
                f"vendor provenance metadata is unsafe: {name}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path,
        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-only", action="store_true")
    parser.add_argument(
        "--payload-mode", choices=("present", "absent", "auto"),
        default=None)
    args = parser.parse_args()
    if args.manifest_only and args.payload_mode is not None:
        raise SystemExit(
            "--manifest-only and --payload-mode are mutually exclusive")
    root = Path(os.path.abspath(args.root))
    manifest = args.manifest or root / "third_party/ctp/6.7.7/manifest-v1.json"
    if not manifest.is_absolute():
        manifest = root / manifest
    payload_mode = (
        "absent" if args.manifest_only else
        (args.payload_mode or "present"))
    if payload_mode == "auto":
        try:
            (root / convergence.CANONICAL_DIRECTORY).lstat()
            payload_mode = "present"
        except FileNotFoundError:
            payload_mode = "absent"
    report = verify(root, manifest, payload_mode == "present")
    print(
        f"PASS: CTP {report['version']} "
        f"{len(report['canonical_headers'])} canonical headers "
        f"{len(report['platform_assets'])} platform assets "
        f"{'manifest-only' if payload_mode == 'absent' else 'payload-verified'} "
        "disabled-experimental")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VendorVerificationError, convergence.ConvergenceError, OSError) as error:
        print(f"vendor-assets: {error}", file=os.sys.stderr)
        raise SystemExit(78)
