#!/usr/bin/env python3
"""Build an offline-only request for external evidence ingestion.

This module deliberately cannot assert that an upload happened.  A production
ingestion receipt must be produced and signed by an independently controlled
external service after remote read-back and retention enforcement.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

import build_heptatrader_evidence_index as index_builder
import verify_heptatrader_evidence_index as index_verifier
import verify_heptatrader_evidence_set as set_verifier


LEGACY_REQUEST_SCHEMA = "hepta.evidence-ingestion-request.v1"
REQUEST_SCHEMA = "hepta.evidence-ingestion-request.v2"
PROJECT_ID = "heptatrader-agent-os"
MAX_JSON_BYTES = 16 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RFC3339 = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-"
    r"(?:0[1-9]|[12][0-9]|3[01])T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$")


class IngestionRequestError(RuntimeError):
    pass


def canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        allow_nan=False,
    ).encode("ascii")


def _reject_constant(value: str) -> None:
    raise IngestionRequestError(f"non-finite JSON value is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IngestionRequestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
        payload = json.loads(
            text, object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IngestionRequestError(f"{label} is not strict JSON") from error
    return payload


def require_rfc3339(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value or not value.isascii():
        raise IngestionRequestError(f"{label} must be an ASCII RFC3339 string")
    if RFC3339.fullmatch(value) is None or value.endswith("-00:00"):
        raise IngestionRequestError(f"{label} is not RFC3339")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise IngestionRequestError(f"{label} is not RFC3339") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IngestionRequestError(f"{label} must include a timezone")
    return parsed


def stable_file(
        path: Path, limit: int = MAX_JSON_BYTES, *,
        allowed_owner_uids: frozenset[int] | None = None,
        trusted_parent_owner_uid: int | None = None,
) -> tuple[os.stat_result, bytes]:
    """Read a caller-owned regular file through no-follow directory fds."""
    if allowed_owner_uids is None:
        allowed_owner_uids = frozenset({os.geteuid()})
    if not allowed_owner_uids or any(
            not isinstance(uid, int) or isinstance(uid, bool) or uid < 0
            for uid in allowed_owner_uids):
        raise IngestionRequestError("stable file owner policy is invalid")
    if (trusted_parent_owner_uid is not None and
            (not isinstance(trusted_parent_owner_uid, int) or
             isinstance(trusted_parent_owner_uid, bool) or
             trusted_parent_owner_uid < 0)):
        raise IngestionRequestError(
            "stable file parent-owner policy is invalid")
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute():
        raise IngestionRequestError("stable file path must be absolute")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    components: list[tuple[str, tuple[int, ...]]] = []
    current = os.open("/", directory_flags)
    descriptors.append(current)
    directory_identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
    )
    file_identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
        item.st_uid, item.st_gid, item.st_size, item.st_mtime_ns,
        item.st_ctime_ns,
    )
    try:
        for component in absolute.parent.parts[1:]:
            metadata = os.stat(component, dir_fd=current, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise IngestionRequestError(
                    f"unsafe parent component for {path}")
            if (trusted_parent_owner_uid is not None and
                    (metadata.st_uid != trusted_parent_owner_uid or
                     metadata.st_mode & 0o022)):
                raise IngestionRequestError(
                    f"untrusted parent ownership or mode for {path}")
            child = os.open(component, directory_flags, dir_fd=current)
            if directory_identity(metadata) != directory_identity(os.fstat(child)):
                os.close(child)
                raise IngestionRequestError(
                    f"unstable parent component for {path}")
            components.append((component, directory_identity(metadata)))
            descriptors.append(child)
            current = child
        before = os.stat(
            absolute.name, dir_fd=current, follow_symlinks=False)
        if (stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
            raise IngestionRequestError(f"{path} is not a regular file")
        if before.st_uid not in allowed_owner_uids or before.st_mode & 0o022:
            raise IngestionRequestError(f"{path} has unsafe ownership or mode")
        if before.st_size > limit:
            raise IngestionRequestError(f"{path} exceeds its size limit")
        flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(absolute.name, flags, dir_fd=current)
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
                    raise IngestionRequestError(
                        f"{path} exceeds its size limit")
                chunks.append(chunk)
            after_descriptor = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = os.stat(
            absolute.name, dir_fd=current, follow_symlinks=False)
        if (file_identity(before) != file_identity(opened) or
                file_identity(opened) != file_identity(after_descriptor) or
                file_identity(after_descriptor) != file_identity(after_path) or
                size != opened.st_size):
            raise IngestionRequestError(f"{path} changed during read")
        for index, (component, expected) in enumerate(components):
            current_metadata = os.stat(
                component, dir_fd=descriptors[index],
                follow_symlinks=False)
            if directory_identity(current_metadata) != expected:
                raise IngestionRequestError(
                    f"parent component changed during read: {path}")
        return opened, b"".join(chunks)
    except OSError as error:
        raise IngestionRequestError(f"unsafe or unstable path: {path}") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _required_retention(records: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [record["retention_days"] for record in records]
    if any(duration is None for duration in durations):
        return {"kind": "indefinite", "days": None}
    return {"kind": "finite-days", "days": max(durations)}


def _objects_from_records(
        records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        digest = record["sha256"]
        current = grouped.setdefault(
            digest,
            {
                "sha256": digest,
                "size": record["size"],
                "object_key": record["object_key"],
                "records": [],
            },
        )
        if (current["size"] != record["size"] or
                current["object_key"] != record["object_key"]):
            raise IngestionRequestError(
                "one digest maps to conflicting object metadata")
        current["records"].append({
            "path": record["path"],
            "tier": record["tier"],
            "retention_days": record["retention_days"],
        })
    objects = []
    for digest in sorted(grouped):
        item = grouped[digest]
        item["records"].sort(key=lambda record: record["path"])
        item["required_retention"] = _required_retention(item["records"])
        objects.append(item)
    return objects


def _objects_from_index(index: dict[str, Any]) -> list[dict[str, Any]]:
    return _objects_from_records(index["files"])


def _evidence_set_binding(
        manifest_path: Path, index_path: Path, evidence_root: Path,
        policy_path: Path, verified_index: dict[str, Any],
        expected_index_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    root = evidence_root.resolve(strict=True)
    absolute_manifest = Path(os.path.abspath(manifest_path))
    try:
        relative = absolute_manifest.relative_to(root).as_posix()
    except ValueError as error:
        raise IngestionRequestError(
            "evidence-set manifest must be inside the evidence root") from error
    metadata, manifest_bytes = stable_file(
        absolute_manifest, set_verifier.MAX_MANIFEST_BYTES)
    try:
        report = set_verifier.verify(
            absolute_manifest, index_path, root, policy_path)
        policy, policy_sha256 = index_builder.load_policy(policy_path)
        rule, tier = index_builder.classify(relative, policy)
    except (set_verifier.EvidenceSetError,
            index_builder.EvidenceIndexError, OSError) as error:
        raise IngestionRequestError(
            "evidence-set manifest verification failed") from error
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if report["manifest_sha256"] != manifest_sha256:
        raise IngestionRequestError(
            "evidence-set verifier did not verify the stable manifest bytes")
    if (report["index_sha256"] != expected_index_sha256 or
            policy_sha256 != verified_index["policy_sha256"]):
        raise IngestionRequestError(
            "evidence-set manifest disagrees with its verified index")
    if tier != "certification":
        raise IngestionRequestError(
            "evidence-set manifest requires certification retention")
    manifest_record = {
        "path": relative,
        "size": len(manifest_bytes),
        "mode": f"0{stat.S_IMODE(metadata.st_mode):03o}",
        "sha256": manifest_sha256,
        "object_key": f"sha256/{manifest_sha256}",
        "classification_rule": rule,
        "tier": tier,
        "retention_days": policy["tiers"][tier]["retention_days"],
    }
    binding = {
        "schema": set_verifier.MANIFEST_SCHEMA,
        "version": 2,
        "manifest_sha256": manifest_sha256,
        "manifest_size": len(manifest_bytes),
        "manifest_object_key": manifest_record["object_key"],
        "evidence_set_id": report["evidence_set_id"],
        "profile": report["profile"],
        "round": report["round"],
        "release_version": report["release_version"],
        "coverage": report["coverage"],
        "role_count": report["role_count"],
        "roles": report["roles"],
        "source_baseline": report["source_baseline"],
    }
    return binding, manifest_record, manifest_bytes


def build_request(
        index_path: Path,
        evidence_root: Path,
        policy_path: Path,
        *,
        project_id: str = PROJECT_ID,
        request_nonce: str,
        created_at: str,
        evidence_set_manifest_path: Path | None = None,
) -> dict[str, Any]:
    if project_id != PROJECT_ID:
        raise IngestionRequestError("unsupported ingestion project")
    if not isinstance(request_nonce, str) or not HEX64.fullmatch(request_nonce):
        raise IngestionRequestError(
            "request_nonce must be 32 bytes of lowercase hexadecimal")
    created = require_rfc3339(created_at, "created_at")
    _, index_bytes = stable_file(index_path)
    index_sha256 = hashlib.sha256(index_bytes).hexdigest()
    parsed_index = strict_json(index_bytes, "evidence index")
    try:
        verified_index = index_verifier.verify(
            index_path, evidence_root, policy_path, verify_files=True)
    except (index_builder.EvidenceIndexError, OSError) as error:
        raise IngestionRequestError("evidence index verification failed") from error
    if parsed_index != verified_index:
        raise IngestionRequestError("evidence index parser disagreement")
    index_generated = require_rfc3339(
        verified_index["generated_at"], "index generated_at")
    if created < index_generated:
        raise IngestionRequestError(
            "ingestion request cannot predate its evidence index")
    _, index_bytes_after = stable_file(index_path)
    if index_bytes_after != index_bytes:
        raise IngestionRequestError(
            "evidence index changed across request construction")
    evidence_set = None
    manifest_bytes = None
    object_records = list(verified_index["files"])
    if evidence_set_manifest_path is not None:
        evidence_set, manifest_record, manifest_bytes = _evidence_set_binding(
            evidence_set_manifest_path, index_path, evidence_root,
            policy_path, verified_index, index_sha256)
        object_records.append(manifest_record)
    objects = _objects_from_records(object_records)
    if evidence_set is not None:
        for item in objects:
            item["required_retention"] = {
                "kind": "indefinite",
                "days": None,
            }
    request = {
        "schema": (
            REQUEST_SCHEMA if evidence_set is not None
            else LEGACY_REQUEST_SCHEMA),
        "version": 2 if evidence_set is not None else 1,
        "project_id": project_id,
        "created_at": created_at,
        "request_nonce": request_nonce,
        "index": {
            "sha256": index_sha256,
            "size": len(index_bytes),
            "schema": verified_index["schema"],
            "version": verified_index["version"],
            "policy_sha256": verified_index["policy_sha256"],
            "records_sha256": verified_index["records_sha256"],
            "file_count": verified_index["file_count"],
            "total_bytes": verified_index["total_bytes"],
        },
        "object_key_template": "sha256/{sha256}",
        "object_count": len(objects),
        "objects_sha256": hashlib.sha256(canonical_json(objects)).hexdigest(),
        "upload_status": "pending-external",
        "retention_anchor_status": "pending-external-ingestion-receipt",
        "source_files_deleted": False,
        "source_removal_authorized": False,
        "paper_authorized": False,
        "live_authorized": False,
        "objects": objects,
    }
    if evidence_set is not None:
        request["evidence_set"] = evidence_set
        _, confirmed_index = stable_file(index_path)
        _, confirmed_manifest = stable_file(
            evidence_set_manifest_path, set_verifier.MAX_MANIFEST_BYTES)
        if (confirmed_index != index_bytes or
                confirmed_manifest != manifest_bytes):
            raise IngestionRequestError(
                "evidence-set manifest or index changed across "
                "request construction")
    return request


def write_request(output_root: Path, request: dict[str, Any]) -> Path:
    payload = canonical_json(request) + b"\n"
    digest = hashlib.sha256(payload).hexdigest()
    name = f"sha256-{digest}.request.json"
    root = Path(os.path.abspath(output_root))
    if root == Path("/"):
        raise IngestionRequestError(
            "filesystem root cannot be a request output directory")
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    directory = os.open("/", directory_flags)
    descriptors.append(directory)
    directory_identities: list[tuple[str, tuple[int, ...]]] = []
    directory_identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
    )
    file_identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
        item.st_uid, item.st_gid, item.st_size, item.st_mtime_ns,
        item.st_ctime_ns,
    )
    temporary_name = ""
    descriptor = -1
    published = False
    published_identity: tuple[int, ...] | None = None
    try:
        for position, component in enumerate(root.parts[1:]):
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            metadata = os.stat(
                component, dir_fd=directory, follow_symlinks=False)
            if (stat.S_ISLNK(metadata.st_mode) or
                    not stat.S_ISDIR(metadata.st_mode)):
                raise IngestionRequestError(
                    "request output path contains an unsafe component")
            child = os.open(
                component, directory_flags, dir_fd=directory)
            opened = os.fstat(child)
            if directory_identity(metadata) != directory_identity(opened):
                os.close(child)
                raise IngestionRequestError(
                    "request output path changed while opening")
            directory_identities.append(
                (component, directory_identity(metadata)))
            descriptors.append(child)
            directory = child
            if position == len(root.parts[1:]) - 1:
                opened = os.fstat(directory)
                if (opened.st_uid != os.geteuid() or opened.st_mode & 0o022):
                    raise IngestionRequestError(
                        "request output root is unsafe")
        try:
            existing_metadata = os.stat(
                name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            existing_metadata = None
        if existing_metadata is not None:
            _, existing = stable_file(root / name)
            if existing != payload:
                raise IngestionRequestError(
                    "content-addressed request path contains different bytes")
            return root / name
        for _ in range(32):
            temporary_name = (
                f".{name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
            try:
                descriptor = os.open(
                    temporary_name, flags, 0o600, dir_fd=directory)
                break
            except FileExistsError:
                temporary_name = ""
        else:
            raise IngestionRequestError(
                "could not allocate private request temporary file")
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            descriptor = -1
        try:
            os.link(
                temporary_name, name,
                src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        except FileExistsError as error:
            raise IngestionRequestError(
                "content-addressed request appeared during publication") from error
        published = True
        os.unlink(temporary_name, dir_fd=directory)
        temporary_name = ""
        os.fsync(directory)
        published_metadata = os.stat(
            name, dir_fd=directory, follow_symlinks=False)
        if (not stat.S_ISREG(published_metadata.st_mode) or
                published_metadata.st_nlink != 1 or
                stat.S_IMODE(published_metadata.st_mode) != 0o600 or
                published_metadata.st_uid != os.geteuid()):
            raise IngestionRequestError(
                "published request metadata is unsafe")
        published_identity = file_identity(published_metadata)
        _, verified = stable_file(root / name)
        if verified != payload:
            raise IngestionRequestError(
                "published request content drift")
        final_metadata = os.stat(
            name, dir_fd=directory, follow_symlinks=False)
        if file_identity(final_metadata) != published_identity:
            raise IngestionRequestError(
                "published request identity drift")
        for index, (component, expected) in enumerate(directory_identities):
            current = os.stat(
                component, dir_fd=descriptors[index],
                follow_symlinks=False)
            if directory_identity(current) != expected:
                raise IngestionRequestError(
                    "request output path changed after publication")
    finally:
        failed = os.sys.exc_info()[0] is not None
        if descriptor >= 0:
            os.close(descriptor)
        if published and failed:
            try:
                current = os.stat(
                    name, dir_fd=directory, follow_symlinks=False)
                if (published_identity is not None and
                        current.st_dev == published_identity[0] and
                        current.st_ino == published_identity[1]):
                    os.unlink(name, dir_fd=directory)
                    os.fsync(directory)
            except FileNotFoundError:
                pass
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except FileNotFoundError:
                pass
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return root / name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument(
        "--evidence-set-manifest", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--request-nonce", default="")
    parser.add_argument("--created-at", default="")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    index = args.index if args.index.is_absolute() else root / args.index
    evidence_root = (
        args.evidence_root or root / "runtime-logs").resolve(strict=True)
    policy = root / "policies/heptatrader-evidence-retention-v1.json"
    output_root = root / "evidence-requests"
    if (evidence_root == output_root or
            evidence_root in output_root.parents or
            output_root in evidence_root.parents):
        raise IngestionRequestError(
            "request output root overlaps evidence payloads")
    request = build_request(
        index, evidence_root, policy,
        request_nonce=args.request_nonce or os.urandom(32).hex(),
        created_at=args.created_at or datetime.now(timezone.utc).isoformat(),
        evidence_set_manifest_path=(
            args.evidence_set_manifest
            if args.evidence_set_manifest.is_absolute()
            else root / args.evidence_set_manifest),
    )
    output = write_request(output_root, request)
    print(
        f"PASS: pending external ingestion request {output} "
        f"objects={request['object_count']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IngestionRequestError, OSError) as error:
        print(f"evidence-ingestion-request: {error}", file=os.sys.stderr)
        raise SystemExit(78)
