#!/usr/bin/env python3
"""Build a deterministic clean-source archive from an explicit positive closure."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import secrets
import stat
import sys
import tarfile
import tempfile


BUILD_TREES = (
    "HeptaStrategy",
    "HeptaTrade",
    "HeptaSimulator",
    "Interface",
    "tests",
    "hepta_ops",
    "ops",
    "policies",
    "compat/hepta-ops-generated",
    "third_party/prebuilt-dependencies",
    "third_party/ctp/6.5.1-tools",
    "third_party/ctp/6.7.7",
)
FORBIDDEN_PREFIXES = (
    "Tools/", "compat/unsafe-direct-broker/", "runtime-logs/", "build", "bin/")
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
GIT_HEAD = re.compile(r"^[0-9a-f]{40}$")
COFF_MACHINES = frozenset({
    0x014C,  # i386
    0x0162, 0x0166, 0x0168, 0x0169,  # MIPS
    0x01C0, 0x01C2, 0x01C4,  # ARM
    0x01F0, 0x01F1,  # PowerPC
    0x0200,  # IA64
    0x8664,  # AMD64
    0xAA64,  # ARM64
})
COMPILED_PAYLOAD_POLICY_VERSION = "hepta.strict-source-payload-policy.v1"
PERMITTED_SOURCE_ENCODINGS = ("utf-8", "gb18030")
MAX_SOURCE_BYTES = 256 * 1024 * 1024
READ_CHUNK = 1024 * 1024
OUTPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
FORBIDDEN_IDE_OUTPUT_PREFIXES = (
    "HeptaSimulator/x64/",
    "HeptaStrategy/x64/",
    "HeptaTrade/x64/",
    "HeptaTrade/HeptaTrader/x64/",
)
FORBIDDEN_LOCAL_FILES = frozenset({
    "HeptaTrade/HeptaTraderConfig.xml",
})
FORBIDDEN_LOCAL_SUFFIXES = (
    ".user",
    ".suo",
    ".VC.db",
    ".opendb",
)


def is_forbidden_local_artifact(relative: str) -> bool:
    return (
        relative in FORBIDDEN_LOCAL_FILES or
        relative.endswith(FORBIDDEN_LOCAL_SUFFIXES)
    )


def is_nonredistributable_vendor_artifact(relative: str) -> bool:
    return (
        relative not in REDISTRIBUTABLE_VENDOR_METADATA and
        relative.startswith(NONREDISTRIBUTABLE_VENDOR_PREFIXES)
    )


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


def reject_compiled_payload(relative: str, data: bytes) -> None:
    suffix = pathlib.PurePosixPath(relative).suffix.lower()
    if (suffix not in PERMITTED_SOURCE_SUFFIXES and
            relative not in PERMITTED_EXTENSIONLESS_SOURCE_PATHS):
        raise RuntimeError(
            f"unsupported strict-source path entered closure: {relative}")
    if is_compiled_payload(relative):
        raise RuntimeError(
            f"compiled payload path entered source closure: {relative}")
    payload_type = compiled_payload_magic(data)
    if payload_type is not None:
        raise RuntimeError(
            "compiled payload content entered source closure: "
            f"{relative} ({payload_type})")
    if b"\0" in data:
        raise RuntimeError(
            f"binary or non-text payload entered source closure: {relative}")
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
        raise RuntimeError(
            f"binary or non-text payload entered source closure: {relative}")


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
    return "sha256:" + sha256(canonical)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class CapturedSource:
    path: str
    mode: int
    metadata: os.stat_result
    data: bytes


@dataclass
class OutputTarget:
    path: pathlib.Path
    parent_descriptor: int
    name: str
    existing_identity: tuple[int, ...] | None


def canonical_relative(value: str) -> str:
    if not value or "\0" in value or "\\" in value:
        raise RuntimeError("source path is unsafe")
    relative = pathlib.PurePosixPath(value)
    if (relative.is_absolute() or relative.as_posix() != value or
            any(part in {"", ".", ".."} for part in relative.parts)):
        raise RuntimeError(f"source path is unsafe: {value}")
    return value


def file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_uid, metadata.st_gid, metadata.st_size,
        metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def path_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_uid, metadata.st_gid,
    )


def directory_flags() -> int:
    return (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_relative_parent(
        root_descriptor: int, relative: str) -> tuple[int, str]:
    parts = pathlib.PurePosixPath(canonical_relative(relative)).parts
    parent_descriptor = os.dup(root_descriptor)
    try:
        for component in parts[:-1]:
            child = os.open(
                component, directory_flags(), dir_fd=parent_descriptor)
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                raise RuntimeError(
                    f"source parent is not a directory: {relative}")
            os.close(parent_descriptor)
            parent_descriptor = child
        return parent_descriptor, parts[-1]
    except BaseException:
        os.close(parent_descriptor)
        raise


def stable_source_bytes(
        root_descriptor: int, relative: str,
        maximum: int = MAX_SOURCE_BYTES) -> tuple[os.stat_result, bytes]:
    """Read a source file once through an anchored no-follow descriptor."""
    parent_descriptor, name = _open_relative_parent(root_descriptor, relative)
    descriptor = -1
    try:
        before = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or
                before.st_size < 0 or before.st_size > maximum):
            raise RuntimeError(f"source file metadata is unsafe: {relative}")
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        if file_identity(before) != file_identity(opened):
            raise RuntimeError(f"source file changed before open: {relative}")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(READ_CHUNK, remaining))
            if not chunk:
                raise RuntimeError(
                    f"source file was truncated while reading: {relative}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RuntimeError(f"source file grew while reading: {relative}")
        after = os.fstat(descriptor)
        if file_identity(opened) != file_identity(after):
            raise RuntimeError(f"source file changed while reading: {relative}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)

    # Re-walk from the anchored root after the read. This catches a parent or
    # leaf pathname replaced while an already-open descriptor remained valid.
    verification_parent, verification_name = _open_relative_parent(
        root_descriptor, relative)
    try:
        final = os.stat(
            verification_name, dir_fd=verification_parent,
            follow_symlinks=False)
    finally:
        os.close(verification_parent)
    if file_identity(after) != file_identity(final):
        raise RuntimeError(f"source path changed while reading: {relative}")
    data = b"".join(chunks)
    if len(data) != opened.st_size:
        raise RuntimeError(f"source file size changed while reading: {relative}")
    return opened, data


def load_security_manifest(
        root: pathlib.Path) -> tuple[dict, dict[str, CapturedSource]]:
    path = root / "scripts" / "run_execution_gateway_soak.py"
    spec = importlib.util.spec_from_file_location("hepta_bundle_sources", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("source manifest loader is unavailable")
    module = importlib.util.module_from_spec(spec)
    # ``exec_module`` does not register a temporary module automatically.
    # The soak runner contains dataclass declarations whose decorator resolves
    # ``cls.__module__`` through ``sys.modules`` on Python 3.12.
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
    root_descriptor = os.open(root, directory_flags())
    captured: dict[str, CapturedSource] = {}
    try:
        def snapshot(
                _repository: pathlib.Path,
                relative: str) -> dict[str, object]:
            metadata, data = stable_source_bytes(root_descriptor, relative)
            reject_compiled_payload(relative, data)
            source = CapturedSource(
                path=relative,
                mode=0o755 if metadata.st_mode & stat.S_IXUSR else 0o644,
                metadata=metadata,
                data=data,
            )
            if relative in captured:
                raise RuntimeError(
                    "security source manifest contains duplicate paths")
            captured[relative] = source
            return {
                "path": relative,
                "mode": format(source.mode, "04o"),
                "size": len(data),
                "sha256": "sha256:" + sha256(data),
            }

        source_manifest = module.source_manifest(root, snapshot=snapshot)
    finally:
        os.close(root_descriptor)
    return source_manifest, captured


def collect_paths(root: pathlib.Path, security_manifest: dict) -> list[pathlib.Path]:
    relative_paths = {entry["path"] for entry in security_manifest["files"]}
    for tree_name in BUILD_TREES:
        tree = root / tree_name
        if not tree.is_dir() or tree.is_symlink():
            raise RuntimeError(f"required source tree is missing or unsafe: {tree_name}")
        for path in tree.rglob("*"):
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_file() or path.is_symlink():
                relative = path.relative_to(root).as_posix()
                if (relative.startswith(FORBIDDEN_IDE_OUTPUT_PREFIXES) or
                        is_nonredistributable_vendor_artifact(relative) or
                        relative.startswith(PREBUILT_OVERLAY_PREFIXES) or
                        relative in PREBUILT_PAYLOAD_PATHS or
                        is_forbidden_local_artifact(relative)):
                    continue
                if is_compiled_payload(relative):
                    raise RuntimeError(
                        "unreviewed compiled payload entered source tree: "
                        f"{relative}")
                relative_paths.add(relative)
    result = []
    for relative in sorted(relative_paths):
        if relative.startswith(FORBIDDEN_PREFIXES):
            raise RuntimeError(f"forbidden source path entered closure: {relative}")
        if is_nonredistributable_vendor_artifact(relative):
            raise RuntimeError(
                f"nonredistributable vendor payload entered closure: {relative}")
        if relative in PREBUILT_PAYLOAD_PATHS:
            raise RuntimeError(
                f"reviewed prebuilt payload entered source closure: {relative}")
        if relative.startswith(PREBUILT_OVERLAY_PREFIXES):
            raise RuntimeError(
                f"prebuilt overlay entered source closure: {relative}")
        if is_compiled_payload(relative):
            raise RuntimeError(
                f"compiled payload entered source closure: {relative}")
        if relative.startswith(FORBIDDEN_IDE_OUTPUT_PREFIXES):
            raise RuntimeError(f"IDE output path entered source closure: {relative}")
        if is_forbidden_local_artifact(relative):
            raise RuntimeError(f"local configuration entered source closure: {relative}")
        path = root / relative
        if path.is_symlink():
            raise RuntimeError(f"source symlink is forbidden: {relative}")
        if not path.is_file():
            raise RuntimeError(f"source file is missing: {relative}")
        result.append(path)
    return result


def capture_sources(
        root: pathlib.Path,
        paths: list[pathlib.Path],
        existing: dict[str, CapturedSource] | None = None) -> list[CapturedSource]:
    root_descriptor = os.open(root, directory_flags())
    captures = []
    captured = {} if existing is None else dict(existing)
    try:
        for path in paths:
            relative = path.relative_to(root).as_posix()
            source = captured.get(relative)
            if source is None:
                metadata, data = stable_source_bytes(root_descriptor, relative)
                reject_compiled_payload(relative, data)
                source = CapturedSource(
                    path=relative,
                    mode=0o755 if metadata.st_mode & stat.S_IXUSR else 0o644,
                    metadata=metadata,
                    data=data,
                )
                captured[relative] = source
            else:
                reject_compiled_payload(relative, source.data)
            captures.append(source)
    finally:
        os.close(root_descriptor)
    return captures


def validate_security_manifest(
        security_manifest: dict, captures: list[CapturedSource]) -> None:
    if not isinstance(security_manifest, dict) or set(security_manifest) != {
            "file_count", "sha256", "files"}:
        raise RuntimeError("security source manifest schema is invalid")
    records = security_manifest["files"]
    if (not isinstance(records, list) or
            security_manifest["file_count"] != len(records)):
        raise RuntimeError("security source manifest closure is invalid")
    canonical = json.dumps(
        records, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode()
    if security_manifest["sha256"] != "sha256:" + sha256(canonical):
        raise RuntimeError("security source manifest digest is invalid")
    captured = {entry.path: entry for entry in captures}
    if len(captured) != len(captures):
        raise RuntimeError("captured source closure contains duplicate paths")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {
                "mode", "path", "sha256", "size"}:
            raise RuntimeError("security source manifest record is invalid")
        relative = canonical_relative(record["path"])
        if relative in seen:
            raise RuntimeError(
                "security source manifest contains duplicate paths")
        seen.add(relative)
        source = captured.get(relative)
        if source is None or record != {
                "path": relative,
                "mode": format(source.mode, "04o"),
                "size": len(source.data),
                "sha256": "sha256:" + sha256(source.data),
        }:
            raise RuntimeError(
                f"security source manifest record drift: {relative}")


def baseline_git_head(
        version: str, captures: list[CapturedSource]) -> str:
    relative = (
        f"release-manifests/heptatrader-agent-os-v{version}/manifest.json")
    captured = {source.path: source for source in captures}
    source = captured.get(relative)
    if source is None:
        raise RuntimeError("versioned source baseline is absent from closure")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON value is forbidden: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        document = json.loads(
            source.data.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(
            "versioned source baseline is not strict UTF-8 JSON") from error
    head = document.get("git_head") if isinstance(document, dict) else None
    if (not isinstance(document, dict) or
            document.get("schema") != "hepta.versioned-source-baseline.v1" or
            document.get("version") != version or
            not isinstance(head, str) or GIT_HEAD.fullmatch(head) is None):
        raise RuntimeError("versioned source baseline identity is invalid")
    return head


def build_manifest(
        version: str, captures: list[CapturedSource],
        security_manifest: dict) -> dict:
    entries = []
    for source in captures:
        entries.append({
            "path": source.path,
            "mode": format(source.mode, "04o"),
            "size": len(source.data),
            "sha256": sha256(source.data),
        })
    canonical = json.dumps(entries, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return {
        "schema": "hepta.clean-source-bundle.v2",
        "bundle_class": "strict-source-only",
        "version": version,
        "git_head": baseline_git_head(version, captures),
        "root": f"heptatrader-{version}",
        "file_count": len(entries),
        "files_sha256": sha256(canonical),
        "security_manifest_sha256": security_manifest["sha256"],
        "security_manifest_file_count": security_manifest["file_count"],
        "excluded_unsafe_tree": "compat/unsafe-direct-broker",
        "excluded_legacy_runtime_tree": "Tools",
        "excluded_nonredistributable_vendor_prefixes":
            list(NONREDISTRIBUTABLE_VENDOR_PREFIXES),
        "redistributable_vendor_metadata_allowlist":
            sorted(REDISTRIBUTABLE_VENDOR_METADATA),
        "nonredistributable_vendor_payload_included": False,
        "excluded_prebuilt_payload_paths": sorted(PREBUILT_PAYLOAD_PATHS),
        "excluded_prebuilt_overlay_prefixes":
            list(PREBUILT_OVERLAY_PREFIXES),
        "compiled_payload_suffixes_denied":
            sorted(COMPILED_PAYLOAD_SUFFIXES),
        "compiled_payload_policy_version":
            COMPILED_PAYLOAD_POLICY_VERSION,
        "compiled_payload_policy_sha256":
            compiled_payload_policy_sha256(),
        "prebuilt_payload_included": False,
        "paper_authorized": False,
        "live_authorized": False,
        "files": entries,
    }


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = 0
    with tempfile.SpooledTemporaryFile() as source:
        source.write(data)
        source.seek(0)
        archive.addfile(info, source)


def _open_absolute_directory(path: pathlib.Path) -> int:
    absolute = pathlib.Path(os.path.abspath(path))
    descriptor = os.open("/", directory_flags())
    try:
        for component in absolute.parts[1:]:
            child = os.open(component, directory_flags(), dir_fd=descriptor)
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                raise RuntimeError(f"output parent is not a directory: {path}")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def prepare_output_target(
        path: pathlib.Path,
        source_paths: set[pathlib.Path],
        source_identities: set[tuple[int, int]]) -> OutputTarget:
    absolute = pathlib.Path(os.path.abspath(path))
    if OUTPUT_NAME.fullmatch(absolute.name) is None:
        raise RuntimeError("output filename is invalid")
    if absolute in source_paths:
        raise RuntimeError("output collides with a source file")
    parent_descriptor = _open_absolute_directory(absolute.parent)
    try:
        parent_metadata = os.fstat(parent_descriptor)
        if (not stat.S_ISDIR(parent_metadata.st_mode) or
                parent_metadata.st_uid != os.geteuid() or
                parent_metadata.st_mode & 0o022):
            raise RuntimeError(
                "output parent must be caller-owned and not group/world writable")
        try:
            existing = os.stat(
                absolute.name, dir_fd=parent_descriptor,
                follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if (stat.S_ISLNK(existing.st_mode) or
                    not stat.S_ISREG(existing.st_mode) or
                    existing.st_nlink != 1 or
                    stat.S_IMODE(existing.st_mode) != 0o600 or
                    existing.st_uid != os.geteuid()):
                raise RuntimeError("existing output metadata is unsafe")
            if (existing.st_dev, existing.st_ino) in source_identities:
                raise RuntimeError("output hardlink collides with a source file")
        return OutputTarget(
            path=absolute,
            parent_descriptor=parent_descriptor,
            name=absolute.name,
            existing_identity=(
                None if existing is None else path_identity(existing)),
        )
    except BaseException:
        os.close(parent_descriptor)
        raise


def _destination_unchanged(target: OutputTarget) -> None:
    try:
        current = os.stat(
            target.name, dir_fd=target.parent_descriptor,
            follow_symlinks=False)
    except FileNotFoundError:
        current_identity = None
    else:
        current_identity = path_identity(current)
    if current_identity != target.existing_identity:
        raise RuntimeError(f"output destination changed: {target.path}")


def _create_private_temporary(target: OutputTarget) -> tuple[str, int]:
    flags = (
        os.O_RDWR | os.O_CREAT | os.O_EXCL |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    for _attempt in range(32):
        name = (
            f".{target.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
        try:
            descriptor = os.open(
                name, flags, 0o600, dir_fd=target.parent_descriptor)
            os.fchmod(descriptor, 0o600)
            return name, descriptor
        except FileExistsError:
            continue
    raise RuntimeError("could not allocate private output temporary file")


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise RuntimeError("short write while producing clean-source output")
        offset += written


def _descriptor_sha256(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digestor = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, READ_CHUNK)
        if not chunk:
            break
        digestor.update(chunk)
    return digestor.hexdigest()


def publish_bundle(
        output: pathlib.Path, manifest_path: pathlib.Path,
        manifest: dict, captures: list[CapturedSource],
        root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, str]:
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    source_paths = {
        pathlib.Path(os.path.abspath(root / source.path))
        for source in captures
    }
    source_identities = {
        (source.metadata.st_dev, source.metadata.st_ino)
        for source in captures
    }
    absolute_output = pathlib.Path(os.path.abspath(output))
    absolute_manifest = pathlib.Path(os.path.abspath(manifest_path))
    if absolute_output == absolute_manifest:
        raise RuntimeError("bundle and manifest outputs must be distinct")
    bundle_target = prepare_output_target(
        absolute_output, source_paths, source_identities)
    manifest_target = prepare_output_target(
        absolute_manifest, source_paths, source_identities)
    bundle_temporary = manifest_temporary = ""
    bundle_descriptor = manifest_descriptor = -1
    try:
        if (
                bundle_target.existing_identity is not None and
                bundle_target.existing_identity ==
                manifest_target.existing_identity):
            raise RuntimeError("bundle and manifest outputs collide")
        bundle_temporary, bundle_descriptor = _create_private_temporary(
            bundle_target)
        manifest_temporary, manifest_descriptor = _create_private_temporary(
            manifest_target)

        with os.fdopen(os.dup(bundle_descriptor), "wb") as destination:
            with tarfile.open(
                    fileobj=destination, mode="w",
                    format=tarfile.GNU_FORMAT) as archive:
                prefix = manifest["root"]
                add_bytes(
                    archive,
                    f"{prefix}/.hepta/source-bundle-manifest.json",
                    manifest_bytes, 0o644)
                for source in captures:
                    add_bytes(
                        archive, f"{prefix}/{source.path}", source.data,
                        source.mode)
            destination.flush()
        os.fsync(bundle_descriptor)
        bundle_sha256 = _descriptor_sha256(bundle_descriptor)

        _write_all(manifest_descriptor, manifest_bytes)
        os.fsync(manifest_descriptor)
        if _descriptor_sha256(manifest_descriptor) != sha256(manifest_bytes):
            raise RuntimeError("manifest temporary content drift")

        _destination_unchanged(bundle_target)
        _destination_unchanged(manifest_target)
        bundle_identity = path_identity(os.fstat(bundle_descriptor))
        manifest_identity = path_identity(os.fstat(manifest_descriptor))
        os.replace(
            bundle_temporary, bundle_target.name,
            src_dir_fd=bundle_target.parent_descriptor,
            dst_dir_fd=bundle_target.parent_descriptor)
        bundle_temporary = ""
        os.replace(
            manifest_temporary, manifest_target.name,
            src_dir_fd=manifest_target.parent_descriptor,
            dst_dir_fd=manifest_target.parent_descriptor)
        manifest_temporary = ""
        os.fsync(bundle_target.parent_descriptor)
        if (manifest_target.parent_descriptor !=
                bundle_target.parent_descriptor):
            os.fsync(manifest_target.parent_descriptor)
        for target, expected_identity in (
                (bundle_target, bundle_identity),
                (manifest_target, manifest_identity)):
            published = os.stat(
                target.name, dir_fd=target.parent_descriptor,
                follow_symlinks=False)
            if (path_identity(published) != expected_identity or
                    not stat.S_ISREG(published.st_mode) or
                    published.st_nlink != 1 or
                    stat.S_IMODE(published.st_mode) != 0o600):
                raise RuntimeError(f"published output identity drift: {target.path}")
        return absolute_output, absolute_manifest, bundle_sha256
    finally:
        if bundle_descriptor >= 0:
            os.close(bundle_descriptor)
        if manifest_descriptor >= 0:
            os.close(manifest_descriptor)
        for target, temporary in (
                (bundle_target, bundle_temporary),
                (manifest_target, manifest_temporary)):
            if temporary:
                try:
                    os.unlink(temporary, dir_fd=target.parent_descriptor)
                except FileNotFoundError:
                    pass
        os.close(bundle_target.parent_descriptor)
        os.close(manifest_target.parent_descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    security_manifest, security_captures = load_security_manifest(root)
    paths = collect_paths(root, security_manifest)
    baseline = root / "release-manifests" / f"heptatrader-agent-os-v{args.version}" / "manifest.json"
    if not baseline.is_file() or baseline.is_symlink():
        raise SystemExit(f"versioned source baseline is missing or unsafe: {baseline}")
    if baseline not in paths:
        paths.append(baseline)
        paths.sort(key=lambda path: path.relative_to(root).as_posix())
    captures = capture_sources(root, paths, security_captures)
    validate_security_manifest(security_manifest, captures)
    manifest = build_manifest(args.version, captures, security_manifest)
    output = args.output if args.output.is_absolute() else root / args.output
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    output, manifest_path, bundle_sha256 = publish_bundle(
        output, manifest_path, manifest, captures, root)
    print(f"BUNDLE={output}")
    print(f"BUNDLE_SHA256={bundle_sha256}")
    print(f"FILES={manifest['file_count']} FILES_SHA256={manifest['files_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
