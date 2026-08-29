#!/usr/bin/env python3
"""Independently verify deterministic clean-source bundle content and metadata."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import stat
import tarfile
from typing import Any

import verify_heptatrader_prebuilt_assets as prebuilt_verifier
import verify_heptatrader_vendor_assets as vendor_verifier


NONREDISTRIBUTABLE_VENDOR_PREFIXES = (
    "Interface/CTPTradeApi32/",
    "Interface/CTPTradeApi64/",
    "Interface/CTPTradeApiLinux/",
    "third_party/ctp/6.5.1-tools/",
    "third_party/ctp/6.7.7/",
    "third_party/prebuilt-dependencies/",
)
REDISTRIBUTABLE_VENDOR_METADATA = frozenset({
    "third_party/ctp/6.5.1-tools/README.md",
    "third_party/ctp/6.5.1-tools/manifest-v1.json",
    "third_party/ctp/6.7.7/README.md",
    "third_party/ctp/6.7.7/manifest-v1.json",
    "third_party/prebuilt-dependencies/README.md",
    "third_party/prebuilt-dependencies/manifest-v1.json",
})
PREBUILT_PAYLOAD_PATHS = frozenset({
    "Interface/IBApi/bin/CSharpAPI.dll",
    "Interface/IBApi/bin/TWSLib.dll",
    "Interface/IBApi/bin/TwsRtdServer.dll",
    "Interface/IBApi/lib/libbid.lib",
    "Interface/lib/Ubuntu/Release/libTinyXml_Linux.a",
    "Interface/lib/Ubuntu/Release/libheptaHeptaDLL_Linux.a",
    "Interface/lib/X64/Release/heptaHeptaDLL.lib",
    "Interface/lib/X64/Release/tinyxml.lib",
})
PREBUILT_OVERLAY_PREFIXES = (
    "Interface/IBApi/bin/",
    "Interface/IBApi/lib/",
    "Interface/lib/Ubuntu/Release/",
    "Interface/lib/X64/Release/",
)
COMPILED_PAYLOAD_SUFFIXES = frozenset({
    ".a", ".apk", ".bc", ".class", ".dll", ".dylib", ".ear", ".exe",
    ".jar", ".lib", ".node", ".o", ".obj", ".pdb", ".pyd", ".rlib",
    ".so", ".wasm", ".war",
})
PERMITTED_SOURCE_SUFFIXES = frozenset({
    ".apparmor", ".cmake", ".conf", ".cpp", ".example", ".filters", ".h", ".in", ".json", ".md",
    ".ps1", ".py", ".service", ".sh", ".socket", ".target", ".timer", ".txt",
    ".vcxproj", ".xml", ".yml",
})
PERMITTED_EXTENSIONLESS_SOURCE_PATHS = frozenset({
    ".gitignore",
    "VERSION",
    "tests/agent_os_rootful_systemd/Dockerfile",
    "tests/agent_os_rootful_systemd/hepta-agent-os-systemd-entrypoint",
    "tests/broker_network_rootful/Dockerfile",
    "tests/paper_domain_rootful_systemd/Dockerfile",
    "tests/paper_domain_rootful_systemd/"
    "hepta-paper-domain-systemd-entrypoint",
    "tests/p1_campaign_rootful_liveness_systemd/Dockerfile",
    "tests/p1_campaign_rootful_liveness_systemd/"
    "hepta-p1-liveness-systemd-entrypoint",
    "tests/p1_dual_domain_rootful_systemd/Dockerfile",
    "tests/p1_dual_domain_rootful_systemd/"
    "hepta-p1-dual-domain-systemd-entrypoint",
    "tests/rootful_systemd/Dockerfile",
    "tests/rootful_systemd/hepta-systemd-entrypoint",
    "tests/rootful_systemd_base/Dockerfile",
})
COMPILED_PAYLOAD_MAGIC = (
    ("elf", b"\x7fELF"),
    ("archive", b"!<arch>\n"),
    ("thin-archive", b"!<thin>\n"),
    ("llvm-bitcode", b"BC\xc0\xde"),
    ("wasm", b"\x00asm"),
    ("zip", b"PK\x03\x04"),
    ("zip-empty", b"PK\x05\x06"),
    ("zip-spanned", b"PK\x07\x08"),
    ("gzip", b"\x1f\x8b"),
    ("bzip2", b"BZh"),
    ("xz", b"\xfd7zXZ\x00"),
    ("zstd", b"\x28\xb5\x2f\xfd"),
    ("7zip", b"7z\xbc\xaf\x27\x1c"),
    ("rar4", b"Rar!\x1a\x07\x00"),
    ("rar5", b"Rar!\x1a\x07\x01\x00"),
    ("java-or-macho-fat", b"\xca\xfe\xba\xbe"),
    ("macho-fat-reverse", b"\xbe\xba\xfe\xca"),
    ("macho-32-be", b"\xfe\xed\xfa\xce"),
    ("macho-32-le", b"\xce\xfa\xed\xfe"),
    ("macho-64-be", b"\xfe\xed\xfa\xcf"),
    ("macho-64-le", b"\xcf\xfa\xed\xfe"),
)
VERSIONED_SHARED_LIBRARY = re.compile(
    r"[^/]+\.so(?:\.[0-9]+)+$", re.IGNORECASE)
COFF_MACHINES = frozenset({
    0x014C, 0x0162, 0x0166, 0x0168, 0x0169, 0x01C0, 0x01C2, 0x01C4,
    0x01F0, 0x01F1, 0x0200, 0x8664, 0xAA64,
})
COMPILED_PAYLOAD_POLICY_VERSION = "hepta.strict-source-payload-policy.v1"
PERMITTED_SOURCE_ENCODINGS = ("utf-8", "gb18030")
HEX64 = frozenset("0123456789abcdef")
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
READ_CHUNK = 1024 * 1024


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(f"{label} is not strict UTF-8 JSON") from error


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_compiled_payload(relative: str) -> bool:
    return (
        pathlib.PurePosixPath(relative).suffix.lower() in
        COMPILED_PAYLOAD_SUFFIXES or
        VERSIONED_SHARED_LIBRARY.fullmatch(
            pathlib.PurePosixPath(relative).name) is not None
    )


def compiled_payload_magic(data: bytes) -> str | None:
    if len(data) >= 64 and data.startswith(b"MZ"):
        pe_offset = int.from_bytes(data[60:64], "little")
        if pe_offset <= len(data) - 4 and data[pe_offset:pe_offset + 4] == b"PE\0\0":
            return "pe"
    if len(data) >= 20:
        machine = int.from_bytes(data[0:2], "little")
        sections = int.from_bytes(data[2:4], "little")
        if machine in COFF_MACHINES and 0 < sections <= 96:
            return "coff"
    if len(data) >= 262 and data[257:262] == b"ustar":
        return "tar"
    for label, magic in COMPILED_PAYLOAD_MAGIC:
        if data.startswith(magic):
            return label
    return None


def reject_compiled_payload(relative: str, data: bytes | None = None) -> None:
    suffix = pathlib.PurePosixPath(relative).suffix.lower()
    if (suffix not in PERMITTED_SOURCE_SUFFIXES and
            relative not in PERMITTED_EXTENSIONLESS_SOURCE_PATHS):
        raise SystemExit(
            "unsupported strict-source path entered bundle: "
            f"{relative}")
    if is_compiled_payload(relative):
        raise SystemExit(
            "compiled payload path entered strict source-only bundle: "
            f"{relative}")
    if data is None:
        return
    payload_type = compiled_payload_magic(data)
    if payload_type is not None:
        raise SystemExit(
            "compiled payload content entered strict source-only bundle: "
            f"{relative} ({payload_type})")
    if b"\0" in data:
        raise SystemExit(
            "binary or non-text payload entered strict source-only bundle: "
            f"{relative}")
    decoded = None
    for encoding in PERMITTED_SOURCE_ENCODINGS:
        try:
            decoded = data.decode(encoding, errors="strict")
            break
        except UnicodeDecodeError:
            continue
    if decoded is None or any(
            (ord(character) < 0x20 and character not in "\t\n\f\r") or
            ord(character) == 0x7f
            for character in decoded):
        raise SystemExit(
            "binary or non-text payload entered strict source-only bundle: "
            f"{relative}")


def compiled_payload_policy_sha256() -> str:
    policy = {
        "schema": COMPILED_PAYLOAD_POLICY_VERSION,
        "permitted_source_suffixes": sorted(PERMITTED_SOURCE_SUFFIXES),
        "permitted_extensionless_source_paths":
            sorted(PERMITTED_EXTENSIONLESS_SOURCE_PATHS),
        "compiled_payload_suffixes": sorted(COMPILED_PAYLOAD_SUFFIXES),
        "versioned_shared_library_pattern": VERSIONED_SHARED_LIBRARY.pattern,
        "magic_prefixes": [
            {"label": label, "hex": magic.hex()}
            for label, magic in COMPILED_PAYLOAD_MAGIC
        ],
        "structured_detectors": ["coff", "pe", "tar-ustar"],
        "coff_machines": sorted(COFF_MACHINES),
        "permitted_text_encodings": list(PERMITTED_SOURCE_ENCODINGS),
        "text_control_policy": "deny-nul-del-and-c0-except-tab-lf-ff-cr",
    }
    canonical = json.dumps(
        policy, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode()
    return "sha256:" + digest(canonical)


def canonical_relative(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or "\0" in value or
            "\\" in value):
        raise SystemExit(f"unsafe {label}")
    path = pathlib.PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in path.parts)):
        raise SystemExit(f"unsafe {label}")
    return value


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_uid, metadata.st_gid, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _directory_flags() -> int:
    return (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )


def stable_private_bytes(
        path: pathlib.Path, label: str, maximum: int) -> bytes:
    """Read one private verifier input through a no-follow anchored path."""
    absolute = pathlib.Path(os.path.abspath(path))
    parts = absolute.parts
    if len(parts) < 2 or not absolute.name:
        raise SystemExit(f"{label} path is invalid")
    parent_descriptor = os.open("/", _directory_flags())
    descriptor = -1
    try:
        for component in parts[1:-1]:
            child = os.open(
                component, _directory_flags(), dir_fd=parent_descriptor)
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                raise SystemExit(f"{label} parent is not a directory")
            os.close(parent_descriptor)
            parent_descriptor = child
        before = os.stat(
            parts[-1], dir_fd=parent_descriptor, follow_symlinks=False)
        if (stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                stat.S_IMODE(before.st_mode) != 0o600 or
                before.st_size < 0 or before.st_size > maximum):
            raise SystemExit(
                f"{label} must be a regular single-link 0600 file")
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        if _identity(before) != _identity(opened):
            raise SystemExit(f"{label} changed before open")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(READ_CHUNK, remaining))
            if not chunk:
                raise SystemExit(f"{label} was truncated while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise SystemExit(f"{label} grew while reading")
        after = os.fstat(descriptor)
        final = os.stat(
            parts[-1], dir_fd=parent_descriptor, follow_symlinks=False)
        if (_identity(opened) != _identity(after) or
                _identity(after) != _identity(final)):
            raise SystemExit(f"{label} changed while reading")
        payload = b"".join(chunks)
        if len(payload) != opened.st_size:
            raise SystemExit(f"{label} size changed while reading")
        return payload
    except OSError as error:
        raise SystemExit(f"{label} path is unsafe or unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def is_nonredistributable_vendor_artifact(relative: str) -> bool:
    return (
        relative not in REDISTRIBUTABLE_VENDOR_METADATA and
        relative.startswith(NONREDISTRIBUTABLE_VENDOR_PREFIXES)
    )


def verify_provenance_manifests(
        provenance_bytes: dict[str, bytes]) -> None:
    try:
        prebuilt_manifest = strict_json(
            provenance_bytes[prebuilt_verifier.PREBUILT_MANIFEST],
            "prebuilt provenance manifest")
        legacy_ctp_manifest = strict_json(
            provenance_bytes[prebuilt_verifier.LEGACY_CTP_MANIFEST],
            "legacy CTP provenance manifest")
        current_ctp_manifest = strict_json(
            provenance_bytes[prebuilt_verifier.CURRENT_CTP_MANIFEST],
            "current CTP provenance manifest")
        vendor_verifier.validate_manifest_document(current_ctp_manifest)
        prebuilt_verifier._verify_prebuilt_manifest(prebuilt_manifest)
        prebuilt_verifier._verify_legacy_ctp_manifest(
            legacy_ctp_manifest, current_ctp_manifest)
    except (
            KeyError,
            prebuilt_verifier.PrebuiltVerificationError,
            vendor_verifier.VendorVerificationError) as error:
        raise SystemExit(
            "source bundle supply-chain provenance is invalid") from error


def verify_security_baseline(
        baseline_bytes: bytes, expected: dict[str, dict[str, Any]],
        *, version: str, git_head: str, security_sha256: str,
        security_file_count: int) -> None:
    baseline = strict_json(baseline_bytes, "source baseline")
    source_manifest = (
        baseline.get("source_manifest") if isinstance(baseline, dict) else None)
    if (not isinstance(source_manifest, dict) or
            set(source_manifest) != {"file_count", "files", "sha256"} or
            baseline.get("git_head") != git_head or
            baseline.get("version") != version):
        raise SystemExit("source baseline/security manifest binding drift")
    records = source_manifest.get("files")
    if (not isinstance(records, list) or
            source_manifest.get("file_count") != security_file_count or
            len(records) != security_file_count):
        raise SystemExit("source baseline/security manifest closure drift")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {
                "mode", "path", "sha256", "size"}:
            raise SystemExit("source baseline file record is invalid")
        relative = canonical_relative(record["path"], "source baseline path")
        if relative in seen:
            raise SystemExit("source baseline contains duplicate file paths")
        seen.add(relative)
        if (record["mode"] not in {"0644", "0755"} or
                not isinstance(record["size"], int) or
                isinstance(record["size"], bool) or record["size"] < 0 or
                not isinstance(record["sha256"], str) or
                not record["sha256"].startswith("sha256:") or
                len(record["sha256"]) != 71 or
                any(character not in HEX64
                    for character in record["sha256"][7:])):
            raise SystemExit("source baseline file record metadata is invalid")
        bundled = expected.get(relative)
        if bundled is None or bundled != {
                "path": relative,
                "mode": record["mode"],
                "size": record["size"],
                "sha256": record["sha256"][7:],
        }:
            raise SystemExit(
                f"source baseline file record drift: {relative}")
    canonical = json.dumps(
        records, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode()
    if (source_manifest.get("sha256") !=
            "sha256:" + digest(canonical) or
            source_manifest.get("sha256") != security_sha256):
        raise SystemExit("source baseline/security manifest digest drift")


def verify_bundle(bundle: pathlib.Path, manifest_path: pathlib.Path) -> dict[str, Any]:
    manifest_bytes = stable_private_bytes(
        manifest_path, "external source bundle manifest", MAX_MANIFEST_BYTES)
    bundle_bytes = stable_private_bytes(
        bundle, "source bundle", MAX_BUNDLE_BYTES)
    manifest = strict_json(
        manifest_bytes, "external source bundle manifest")
    expected_fields = {
        "schema", "bundle_class", "version", "git_head", "root", "file_count",
        "files_sha256", "security_manifest_sha256",
        "security_manifest_file_count", "excluded_unsafe_tree",
        "excluded_legacy_runtime_tree",
        "excluded_nonredistributable_vendor_prefixes",
        "redistributable_vendor_metadata_allowlist",
        "nonredistributable_vendor_payload_included",
        "excluded_prebuilt_payload_paths",
        "excluded_prebuilt_overlay_prefixes",
        "compiled_payload_suffixes_denied",
        "compiled_payload_policy_version",
        "compiled_payload_policy_sha256",
        "prebuilt_payload_included",
        "paper_authorized", "live_authorized", "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_fields:
        raise SystemExit("source bundle manifest fields do not match schema")
    if (manifest.get("schema") != "hepta.clean-source-bundle.v2" or
            manifest.get("bundle_class") != "strict-source-only"):
        raise SystemExit("unsupported clean-source bundle schema")
    git_head = manifest.get("git_head")
    security_sha256 = manifest.get("security_manifest_sha256")
    security_file_count = manifest.get("security_manifest_file_count")
    if (not isinstance(git_head, str) or len(git_head) != 40 or
            any(character not in HEX64 for character in git_head)):
        raise SystemExit("source bundle Git identity is invalid")
    if (not isinstance(security_sha256, str) or
            not security_sha256.startswith("sha256:") or
            len(security_sha256) != 71 or
            any(character not in HEX64 for character in security_sha256[7:]) or
            not isinstance(security_file_count, int) or
            isinstance(security_file_count, bool) or
            security_file_count <= 0):
        raise SystemExit("source bundle security manifest identity is invalid")
    if manifest.get("excluded_unsafe_tree") != "compat/unsafe-direct-broker":
        raise SystemExit("source bundle unsafe-tree boundary drift")
    if manifest.get("excluded_legacy_runtime_tree") != "Tools":
        raise SystemExit("source bundle legacy runtime boundary drift")
    if manifest.get("paper_authorized") is not False or manifest.get("live_authorized") is not False:
        raise SystemExit("source bundle must not grant trading authorization")
    if (manifest.get("nonredistributable_vendor_payload_included") is not False or
            tuple(manifest.get(
                "excluded_nonredistributable_vendor_prefixes", ())) !=
            NONREDISTRIBUTABLE_VENDOR_PREFIXES or
            manifest.get("redistributable_vendor_metadata_allowlist") !=
            sorted(REDISTRIBUTABLE_VENDOR_METADATA) or
            manifest.get("excluded_prebuilt_payload_paths") !=
            sorted(PREBUILT_PAYLOAD_PATHS) or
            tuple(manifest.get(
                "excluded_prebuilt_overlay_prefixes", ())) !=
            PREBUILT_OVERLAY_PREFIXES or
            manifest.get("compiled_payload_suffixes_denied") !=
            sorted(COMPILED_PAYLOAD_SUFFIXES) or
            manifest.get("compiled_payload_policy_version") !=
            COMPILED_PAYLOAD_POLICY_VERSION or
            manifest.get("compiled_payload_policy_sha256") !=
            compiled_payload_policy_sha256() or
            manifest.get("prebuilt_payload_included") is not False):
        raise SystemExit("source bundle vendor distribution boundary drift")
    version = manifest.get("version")
    root = canonical_relative(manifest.get("root"), "bundle root")
    if "/" in root or not isinstance(version, str) or root != f"heptatrader-{version}":
        raise SystemExit("source bundle root/version boundary drift")
    entries = manifest.get("files", [])
    if not isinstance(entries, list):
        raise SystemExit("manifest files must be an array")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
                "path", "mode", "size", "sha256"}:
            raise SystemExit("manifest file record is invalid")
        relative = canonical_relative(entry["path"], "manifest path")
        if relative == ".hepta/source-bundle-manifest.json":
            raise SystemExit("internal bundle manifest entered file closure")
        if entry["mode"] not in {"0644", "0755"}:
            raise SystemExit("manifest file mode is invalid")
        if (not isinstance(entry["size"], int) or
                isinstance(entry["size"], bool) or entry["size"] < 0):
            raise SystemExit("manifest file size is invalid")
        if (not isinstance(entry["sha256"], str) or
                len(entry["sha256"]) != 64 or
                any(character not in HEX64 for character in entry["sha256"])):
            raise SystemExit("manifest file digest is invalid")
    canonical = json.dumps(entries, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    if digest(canonical) != manifest.get("files_sha256"):
        raise SystemExit("manifest file closure digest mismatch")
    expected = {entry["path"]: entry for entry in entries}
    file_count = manifest.get("file_count")
    if (not isinstance(file_count, int) or isinstance(file_count, bool) or
            file_count <= 0 or len(expected) != len(entries) or
            len(entries) != file_count):
        raise SystemExit("manifest contains duplicate or inconsistent file entries")
    baseline_path = (
        f"release-manifests/heptatrader-agent-os-v{version}/manifest.json")
    required_provenance = {
        "README.md", baseline_path, *REDISTRIBUTABLE_VENDOR_METADATA}
    if not required_provenance.issubset(expected):
        raise SystemExit("source bundle provenance closure is incomplete")
    if any(path.startswith("compat/unsafe-direct-broker/") for path in expected):
        raise SystemExit("unsafe direct-broker tree entered source bundle")
    if any(path.startswith("Tools/") for path in expected):
        raise SystemExit("legacy Tools runtime entered source bundle")
    if any(
            is_nonredistributable_vendor_artifact(path)
            for path in expected):
        raise SystemExit(
            "nonredistributable vendor payload entered source bundle")
    if any(path in PREBUILT_PAYLOAD_PATHS for path in expected):
        raise SystemExit("reviewed prebuilt payload entered source bundle")
    if any(
            path.startswith(PREBUILT_OVERLAY_PREFIXES)
            for path in expected):
        raise SystemExit("prebuilt overlay entered source bundle")
    for path in expected:
        reject_compiled_payload(path)

    prefix = root + "/"
    internal_manifest = prefix + ".hepta/source-bundle-manifest.json"
    seen = set()
    internal_manifest_count = 0
    baseline_bytes: bytes | None = None
    provenance_bytes: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:") as archive:
        for member in archive.getmembers():
            canonical_relative(member.name, "tar member path")
            if (not member.isfile() or member.uid != 0 or member.gid != 0 or
                    member.uname != "root" or member.gname != "root" or
                    member.mtime != 0):
                raise SystemExit(f"unsafe tar metadata: {member.name}")
            source = archive.extractfile(member)
            if source is None:
                raise SystemExit(f"unreadable tar member: {member.name}")
            data = source.read()
            if member.name == internal_manifest:
                internal_manifest_count += 1
                if member.mode != 0o644:
                    raise SystemExit("internal bundle manifest mode is unsafe")
                if data != manifest_bytes:
                    raise SystemExit("internal and external manifests differ")
                continue
            if not member.name.startswith(prefix):
                raise SystemExit(f"tar member escapes canonical root: {member.name}")
            relative = member.name[len(prefix):]
            entry = expected.get(relative)
            if entry is None or relative in seen:
                raise SystemExit(f"unregistered or duplicate tar member: {relative}")
            if member.mode != int(entry["mode"], 8) or member.size != entry["size"] or digest(data) != entry["sha256"]:
                raise SystemExit(f"tar content or metadata mismatch: {relative}")
            reject_compiled_payload(relative, data)
            if relative == baseline_path:
                baseline_bytes = data
            if relative in {
                    prebuilt_verifier.PREBUILT_MANIFEST,
                    prebuilt_verifier.LEGACY_CTP_MANIFEST,
                    prebuilt_verifier.CURRENT_CTP_MANIFEST}:
                provenance_bytes[relative] = data
            seen.add(relative)
    if internal_manifest_count != 1:
        raise SystemExit("bundle must contain exactly one internal manifest")
    if seen != set(expected):
        raise SystemExit("tar file closure is incomplete")
    if baseline_bytes is None:
        raise SystemExit("source baseline is missing from bundle")
    verify_provenance_manifests(provenance_bytes)
    verify_security_baseline(
        baseline_bytes, expected, version=version, git_head=git_head,
        security_sha256=security_sha256,
        security_file_count=security_file_count)
    return {
        "version": manifest["version"],
        "git_head": manifest["git_head"],
        "file_count": len(seen),
        "files_sha256": manifest["files_sha256"],
        "security_manifest_sha256": manifest["security_manifest_sha256"],
        "bundle_sha256": digest(bundle_bytes),
        "manifest_sha256": digest(manifest_bytes),
        "paper_authorized": False,
        "live_authorized": False,
        "nonredistributable_vendor_payload_included": False,
        "prebuilt_payload_included": False,
        "bundle_class": "strict-source-only",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    args = parser.parse_args()
    result = verify_bundle(args.bundle, args.manifest)
    print(f"PASS: {result['version']} {result['file_count']} files "
          f"bundle_sha256={result['bundle_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
