from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
from typing import Any


POLICY_SCHEMA = "hepta.agent-os-source-policy.v2"
MANIFEST_SCHEMA = "hepta.agent-os-source-bundle.v1"
INTERNAL_MANIFEST = ".hepta/agent-os-source-manifest.json"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


class AgentOsSourceError(RuntimeError):
    pass


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(data: bytes, label: str) -> dict[str, Any]:
    if len(data) > MAX_MANIFEST_BYTES:
        raise AgentOsSourceError(f"{label} exceeds the size limit")
    try:
        document = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AgentOsSourceError(
            f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise AgentOsSourceError(f"{label} root must be an object")
    return document


def canonical_json(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def canonical_relative(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or "\0" in value or
            "\\" in value):
        raise AgentOsSourceError(f"{label} is invalid")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in path.parts)):
        raise AgentOsSourceError(f"{label} is not canonical")
    return value


def _string_array(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AgentOsSourceError(f"{label} must be an array")
    normalized = tuple(
        canonical_relative(item, label) for item in value)
    if len(normalized) != len(set(normalized)):
        raise AgentOsSourceError(f"{label} contains duplicates")
    return normalized


def _prefix_array(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AgentOsSourceError(f"{label} must be an array")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.endswith("/"):
            raise AgentOsSourceError(f"{label} entries must end with slash")
        canonical_relative(item[:-1], label)
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise AgentOsSourceError(f"{label} contains duplicates")
    return tuple(normalized)


@dataclass(frozen=True)
class SourcePolicy:
    include_prefixes: tuple[str, ...]
    include_files: frozenset[str]
    required_files: frozenset[str]
    exclude_prefixes: tuple[str, ...]
    exclude_files: frozenset[str]
    forbidden_prefixes: tuple[str, ...]
    forbidden_files: frozenset[str]
    sha256: str

    def selects(self, path: str) -> bool:
        included = (
            path in self.include_files or
            path.startswith(self.include_prefixes)
        )
        excluded = (
            path in self.exclude_files or
            path.startswith(self.exclude_prefixes)
        )
        forbidden = (
            path in self.forbidden_files or
            path.startswith(self.forbidden_prefixes)
        )
        return included and not excluded and not forbidden


def load_policy(path: Path) -> SourcePolicy:
    metadata = path.lstat()
    if (stat.S_ISLNK(metadata.st_mode) or
            not stat.S_ISREG(metadata.st_mode) or
            metadata.st_nlink != 1 or metadata.st_mode & 0o022):
        raise AgentOsSourceError("source policy must be a protected file")
    data = path.read_bytes()
    document = strict_json(data, "source policy")
    if set(document) != {
            "schema", "version", "include_prefixes", "include_files",
            "required_files", "exclude_prefixes", "exclude_files",
            "forbidden_prefixes", "forbidden_files"}:
        raise AgentOsSourceError(
            "source policy fields do not exactly match schema")
    if document["schema"] != POLICY_SCHEMA or document["version"] != 2:
        raise AgentOsSourceError("unsupported source policy")
    include_prefixes = _prefix_array(
        document["include_prefixes"], "include prefixes")
    exclude_prefixes = _prefix_array(
        document["exclude_prefixes"], "exclude prefixes")
    forbidden_prefixes = _prefix_array(
        document["forbidden_prefixes"], "forbidden prefixes")
    include_files = frozenset(
        _string_array(document["include_files"], "include files"))
    required_files = frozenset(
        _string_array(document["required_files"], "required files"))
    exclude_files = frozenset(
        _string_array(document["exclude_files"], "exclude files"))
    forbidden_files = frozenset(
        _string_array(document["forbidden_files"], "forbidden files"))
    if required_files - (
            include_files | {
                path for path in required_files
                if path.startswith(include_prefixes)}):
        raise AgentOsSourceError("required files are outside the allowlist")
    if (required_files & exclude_files or
            any(path.startswith(exclude_prefixes)
                for path in required_files)):
        raise AgentOsSourceError("required files are excluded as non-product")
    if (include_files & forbidden_files or
            any(path.startswith(forbidden_prefixes)
                for path in include_files | required_files)):
        raise AgentOsSourceError("source policy include/forbid sets overlap")
    if exclude_files & forbidden_files:
        raise AgentOsSourceError(
            "non-product and forbidden source sets overlap")
    return SourcePolicy(
        include_prefixes=include_prefixes,
        include_files=include_files,
        required_files=required_files,
        exclude_prefixes=exclude_prefixes,
        exclude_files=exclude_files,
        forbidden_prefixes=forbidden_prefixes,
        forbidden_files=forbidden_files,
        sha256=hashlib.sha256(data).hexdigest())


def selected_records(
        strict_manifest: dict[str, Any],
        policy: SourcePolicy) -> list[dict[str, Any]]:
    if (strict_manifest.get("schema") != "hepta.clean-source-bundle.v2" or
            not isinstance(strict_manifest.get("files"), list)):
        raise AgentOsSourceError("strict source manifest schema is unsupported")
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in strict_manifest["files"]:
        if not isinstance(raw, dict) or set(raw) != {
                "path", "mode", "size", "sha256"}:
            raise AgentOsSourceError("strict source file record is invalid")
        path = canonical_relative(raw["path"], "strict source path")
        if path in seen:
            raise AgentOsSourceError("strict source paths are duplicated")
        seen.add(path)
        if policy.selects(path):
            selected.append(dict(raw))
    selected.sort(key=lambda item: item["path"])
    selected_paths = {item["path"] for item in selected}
    missing = sorted(policy.required_files - selected_paths)
    if missing:
        raise AgentOsSourceError(
            f"required Agent OS source files are missing: {missing}")
    if any(
            path in policy.exclude_files or
            path.startswith(policy.exclude_prefixes) or
            path in policy.forbidden_files or
            path.startswith(policy.forbidden_prefixes)
            for path in selected_paths):
        raise AgentOsSourceError(
            "excluded non-product or forbidden legacy source entered closure")
    return selected


def manifest_document(
        release_version: str,
        strict_manifest: dict[str, Any],
        strict_bundle_sha256: str,
        strict_manifest_sha256: str,
        policy: SourcePolicy,
        records: list[dict[str, Any]]) -> dict[str, Any]:
    canonical_relative(
        f"heptatrader-agent-os-{release_version}", "bundle root")
    canonical = json.dumps(
        records, ensure_ascii=True, separators=(",", ":"),
        sort_keys=True).encode("utf-8")
    return {
        "schema": MANIFEST_SCHEMA,
        "version": 1,
        "bundle_class": "agent-os-source-only",
        "release_version": release_version,
        "root": f"heptatrader-agent-os-{release_version}",
        "file_count": len(records),
        "files_sha256": hashlib.sha256(canonical).hexdigest(),
        "policy_sha256": policy.sha256,
        "parent_strict_source": {
            "schema": strict_manifest["schema"],
            "git_head": strict_manifest["git_head"],
            "root": strict_manifest["root"],
            "file_count": strict_manifest["file_count"],
            "files_sha256": strict_manifest["files_sha256"],
            "bundle_sha256": strict_bundle_sha256,
            "manifest_sha256": strict_manifest_sha256,
        },
        "excluded_non_product_prefixes": list(policy.exclude_prefixes),
        "excluded_non_product_files": sorted(policy.exclude_files),
        "excluded_legacy_prefixes": list(policy.forbidden_prefixes),
        "excluded_legacy_files": sorted(policy.forbidden_files),
        "paper_authorized": False,
        "live_authorized": False,
        "files": records,
    }


def extract_selected(
        strict_bundle: Path,
        strict_manifest: dict[str, Any],
        records: list[dict[str, Any]]) -> dict[str, bytes]:
    root = canonical_relative(strict_manifest["root"], "strict source root")
    expected = {item["path"]: item for item in records}
    captured: dict[str, bytes] = {}
    with tarfile.open(strict_bundle, "r:*") as archive:
        for member in archive.getmembers():
            prefix = root + "/"
            if not member.name.startswith(prefix):
                continue
            relative = member.name[len(prefix):]
            if relative not in expected:
                continue
            if (not member.isfile() or member.issym() or member.islnk() or
                    member.size != expected[relative]["size"]):
                raise AgentOsSourceError(
                    f"strict source member is unsafe: {relative}")
            source = archive.extractfile(member)
            if source is None:
                raise AgentOsSourceError(
                    f"strict source member is unreadable: {relative}")
            data = source.read()
            if hashlib.sha256(data).hexdigest() != expected[relative]["sha256"]:
                raise AgentOsSourceError(
                    f"strict source member digest drift: {relative}")
            captured[relative] = data
    if set(captured) != set(expected):
        raise AgentOsSourceError("strict source selected closure is incomplete")
    return captured


def build_tar(
        manifest: dict[str, Any],
        files: dict[str, bytes]) -> bytes:
    manifest_bytes = canonical_json(manifest)
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        entries = [
            (INTERNAL_MANIFEST, 0o644, manifest_bytes),
            *[
                (
                    item["path"],
                    int(item["mode"], 8),
                    files[item["path"]],
                )
                for item in manifest["files"]
            ],
        ]
        for relative, mode, data in entries:
            info = tarfile.TarInfo(f"{manifest['root']}/{relative}")
            info.size = len(data)
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return payload.getvalue()


def verify_tar(
        bundle: Path,
        manifest: dict[str, Any],
        files: dict[str, bytes]) -> None:
    expected = build_tar(manifest, files)
    observed = bundle.read_bytes()
    if observed != expected:
        raise AgentOsSourceError(
            "Agent OS source bundle is not canonical or content drifted")


def publish_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise AgentOsSourceError("short write while publishing bundle")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
