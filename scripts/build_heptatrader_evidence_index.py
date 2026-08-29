#!/usr/bin/env python3
"""Build a small content-addressed index without moving evidence payloads."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any


POLICY_SCHEMA = "hepta.evidence-retention-policy.v1"
INDEX_SCHEMA = "hepta.evidence-index.v2"
MAX_POLICY_BYTES = 1024 * 1024
HASH_CHUNK = 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_TIERS = frozenset(
    {"certification", "forensic", "latest", "ephemeral"})


class EvidenceIndexError(RuntimeError):
    pass


def strict_json(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceIndexError(
            f"{label} is not strict UTF-8 JSON") from error

    def reject_constant(value: str) -> None:
        raise EvidenceIndexError(
            f"{label} contains a non-finite JSON number: {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceIndexError(
                    f"{label} contains a duplicate object key")
            result[key] = value
        return result

    try:
        return json.loads(
            text, object_pairs_hook=unique_object,
            parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise EvidenceIndexError(
            f"{label} is not strict UTF-8 JSON") from error


def canonical_json(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"),
                   sort_keys=True) + "\n"
    ).encode("utf-8")


def _stable_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    """Identity fields that remain stable while unrelated children change."""
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
    )


def _stable_read(
        path: Path, limit: int | None,
        *, capture: bool) -> tuple[os.stat_result, int, str, bytes]:
    """Read one file through an anchored, no-follow pathname walk.

    Keeping every directory descriptor open and revalidating each pathname
    component closes the parent-symlink and directory-replacement gap that a
    terminal-only O_NOFOLLOW check leaves behind.
    """
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if not absolute.is_absolute() or len(parts) < 2:
        raise EvidenceIndexError(f"evidence path is invalid: {path}")
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
    file_descriptor = -1
    try:
        root_descriptor = os.open("/", directory_flags)
        descriptors.append(root_descriptor)
        parent_descriptor = root_descriptor
        for component in parts[1:-1]:
            before = os.stat(
                component, dir_fd=parent_descriptor,
                follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise EvidenceIndexError(
                    f"evidence path ancestor is unsafe: {path}")
            child_descriptor = os.open(
                component, directory_flags, dir_fd=parent_descriptor)
            opened = os.fstat(child_descriptor)
            if _directory_identity(before) != _directory_identity(opened):
                os.close(child_descriptor)
                raise EvidenceIndexError(
                    f"evidence path ancestor changed while opening: {path}")
            components.append((component, _directory_identity(before)))
            descriptors.append(child_descriptor)
            parent_descriptor = child_descriptor

        name = parts[-1]
        before = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise EvidenceIndexError(
                f"evidence is not a regular file: {path}")
        if before.st_nlink != 1:
            raise EvidenceIndexError(
                f"evidence must have exactly one link: {path}")
        if before.st_mode & 0o022:
            raise EvidenceIndexError(
                f"evidence is group/world writable: {path}")
        if limit is not None and before.st_size > limit:
            raise EvidenceIndexError(f"file exceeds size limit: {path}")
        file_descriptor = os.open(
            name, file_flags, dir_fd=parent_descriptor)
        opened = os.fstat(file_descriptor)
        if _stable_identity(before) != _stable_identity(opened):
            raise EvidenceIndexError(
                f"evidence changed while opening: {path}")

        digest = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(file_descriptor, HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if capture:
                chunks.append(chunk)
            if limit is not None and size > limit:
                raise EvidenceIndexError(f"file exceeds size limit: {path}")

        after_descriptor = os.fstat(file_descriptor)
        after_path = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (_stable_identity(opened) != _stable_identity(after_descriptor) or
                _stable_identity(after_descriptor) !=
                _stable_identity(after_path)):
            raise EvidenceIndexError(
                f"evidence changed during read: {path}")
        if size != opened.st_size:
            raise EvidenceIndexError(
                f"evidence size changed during read: {path}")

        for index, (component, expected) in enumerate(components):
            current = os.stat(
                component, dir_fd=descriptors[index],
                follow_symlinks=False)
            if _directory_identity(current) != expected:
                raise EvidenceIndexError(
                    f"evidence path ancestor changed during read: {path}")
        return opened, size, digest.hexdigest(), b"".join(chunks)
    except OSError as error:
        raise EvidenceIndexError(
            f"evidence path is unstable or unsafe: {path}") from error
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def stable_bytes(
        path: Path, limit: int | None = None) -> tuple[os.stat_result, bytes]:
    metadata, _size, _digest, contents = _stable_read(
        path, limit, capture=True)
    return metadata, contents


def stable_digest(
        path: Path, limit: int | None = None) -> tuple[os.stat_result, int, str]:
    metadata, size, digest, _contents = _stable_read(
        path, limit, capture=False)
    return metadata, size, digest


def load_policy(path: Path) -> tuple[dict[str, Any], str]:
    _, data = stable_bytes(path, MAX_POLICY_BYTES)
    policy = strict_json(data, "policy")
    if (not isinstance(policy, dict) or set(policy) != {
            "schema", "version", "object_store", "tiers", "rules"} or
            policy.get("schema") != POLICY_SCHEMA):
        raise EvidenceIndexError("unsupported evidence policy")
    if type(policy.get("version")) is not int or policy["version"] != 1:
        raise EvidenceIndexError("unsupported evidence policy version")
    tiers = policy.get("tiers")
    rules = policy.get("rules")
    object_store = policy.get("object_store")
    if (not isinstance(tiers, dict) or set(tiers) != EXPECTED_TIERS or
            not isinstance(rules, list) or not rules):
        raise EvidenceIndexError("policy tiers/rules are invalid")
    if (not isinstance(object_store, dict) or set(object_store) != {
            "key_template", "upload_required_before_source_removal",
            "source_removal_implemented"} or
            object_store.get("key_template") != "sha256/{sha256}" or
            object_store.get("upload_required_before_source_removal") is not True):
        raise EvidenceIndexError("policy object_store is invalid")
    if object_store.get("source_removal_implemented") is not False:
        raise EvidenceIndexError("Round33 policy must not delete source evidence")
    for name, tier in tiers.items():
        if (not isinstance(name, str) or not isinstance(tier, dict) or
                set(tier) != {
                    "retention_days", "git_payload_allowed",
                    "git_index_allowed"}):
            raise EvidenceIndexError("invalid retention tier")
        retention_days = tier["retention_days"]
        if not (
                (name == "certification" and retention_days is None) or
                (name != "certification" and
                 isinstance(retention_days, int) and
                 not isinstance(retention_days, bool) and
                 retention_days > 0)):
            raise EvidenceIndexError("invalid retention duration")
        if tier.get("git_payload_allowed") is not False:
            raise EvidenceIndexError("evidence payloads must be excluded from Git")
        if not isinstance(tier.get("git_index_allowed"), bool):
            raise EvidenceIndexError("git_index_allowed must be boolean")
    if tiers["ephemeral"]["git_index_allowed"] is not False:
        raise EvidenceIndexError("ephemeral evidence cannot enter a Git index")
    if any(
            tiers[name]["git_index_allowed"] is not True
            for name in ("certification", "forensic", "latest")):
        raise EvidenceIndexError("durable evidence tiers must allow small indexes")
    names: set[str] = set()
    patterns: set[str] = set()
    priorities: set[int] = set()
    for rule in rules:
        if (not isinstance(rule, dict) or set(rule) != {
                "name", "priority", "tier", "globs"} or
                not isinstance(rule["tier"], str) or
                rule["tier"] not in tiers or
                not isinstance(rule["name"], str) or not rule["name"] or
                not isinstance(rule["priority"], int) or
                isinstance(rule["priority"], bool) or rule["priority"] <= 0 or
                not isinstance(rule["globs"], list) or not rule["globs"]):
            raise EvidenceIndexError("invalid evidence classification rule")
        if rule["name"] in names:
            raise EvidenceIndexError("duplicate evidence classification rule")
        names.add(rule["name"])
        if rule["priority"] in priorities:
            raise EvidenceIndexError("duplicate evidence classification priority")
        priorities.add(rule["priority"])
        for pattern in rule["globs"]:
            if (not isinstance(pattern, str) or not pattern or "\0" in pattern or
                    "\\" in pattern or Path(pattern).is_absolute() or
                    ".." in Path(pattern).parts):
                raise EvidenceIndexError("unsafe evidence classification glob")
            if pattern in patterns:
                raise EvidenceIndexError("duplicate evidence classification glob")
            patterns.add(pattern)
    return policy, hashlib.sha256(data).hexdigest()


def classify(relative: str, policy: dict[str, Any]) -> tuple[str, str]:
    matches = []
    for rule in policy["rules"]:
        if any(fnmatch.fnmatchcase(relative, pattern)
               for pattern in rule["globs"]):
            matches.append(
                (rule["priority"], rule["name"], rule["tier"]))
    if not matches:
        raise EvidenceIndexError(
            f"evidence path is not classified by policy: {relative}")
    matches.sort(reverse=True)
    return matches[0][1], matches[0][2]


def selected_paths(
        evidence_root: Path, policy: dict[str, Any],
        explicit_paths: list[str]) -> tuple[list[Path], list[str], str]:
    root = evidence_root.resolve(strict=True)
    if explicit_paths:
        candidates = []
        for relative in explicit_paths:
            candidate = Path(relative)
            if (not relative or "\0" in relative or "\\" in relative or
                    candidate.is_absolute() or ".." in candidate.parts or
                    candidate.as_posix() != relative):
                raise EvidenceIndexError("evidence paths must be normalized relative paths")
            candidates.append(evidence_root / candidate)
        selection_mode = "explicit"
    else:
        candidates = list(evidence_root.rglob("*"))
        selection_mode = "complete-tree"
    unique: dict[str, Path] = {}
    excluded_local_only: list[str] = []
    for candidate in candidates:
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise EvidenceIndexError(
                f"evidence is not a regular file: {candidate}")
        resolved = candidate.resolve(strict=True)
        if root not in resolved.parents:
            raise EvidenceIndexError("evidence path escapes evidence root")
        relative = resolved.relative_to(root).as_posix()
        _, tier = classify(relative, policy)
        if not policy["tiers"][tier]["git_index_allowed"]:
            if explicit_paths:
                raise EvidenceIndexError(
                    f"evidence tier cannot enter a Git index: {relative}")
            excluded_local_only.append(relative)
            continue
        unique[relative] = candidate
    if not unique:
        raise EvidenceIndexError("evidence index selection is empty")
    return (
        [unique[relative] for relative in sorted(unique)],
        sorted(set(excluded_local_only)),
        selection_mode,
    )


def build_index(
        evidence_root: Path, policy_path: Path, explicit_paths: list[str],
        generated_at: str) -> dict[str, Any]:
    policy, policy_sha256 = load_policy(policy_path)
    try:
        generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceIndexError("generated_at must be RFC3339") from error
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise EvidenceIndexError("generated_at must include a timezone")
    records = []
    total_bytes = 0
    paths, excluded_local_only, selection_mode = selected_paths(
        evidence_root, policy, explicit_paths)
    for path in paths:
        relative = path.resolve(strict=True).relative_to(
            evidence_root.resolve(strict=True)).as_posix()
        rule, tier = classify(relative, policy)
        metadata, size, digest = stable_digest(path)
        retention = policy["tiers"][tier]
        total_bytes += size
        records.append({
            "path": relative,
            "rule": rule,
            "tier": tier,
            "retention_days": retention["retention_days"],
            "git_index_allowed": retention["git_index_allowed"],
            "size": size,
            "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
            "sha256": digest,
            "object_key": policy["object_store"]["key_template"].format(
                sha256=digest),
        })
    records_sha256 = hashlib.sha256(canonical_json(records)).hexdigest()
    return {
        "schema": INDEX_SCHEMA,
        "version": 2,
        "generated_at": generated_at,
        "policy_sha256": policy_sha256,
        "evidence_root_label": evidence_root.name,
        "selection_mode": selection_mode,
        "excluded_local_only_count": len(excluded_local_only),
        "excluded_local_only_sha256": hashlib.sha256(
            canonical_json(excluded_local_only)).hexdigest(),
        "file_count": len(records),
        "total_bytes": total_bytes,
        "records_sha256": records_sha256,
        "git_index_eligible": True,
        "object_store_upload_status": "pending-external",
        "retention_anchor_status": "pending-external-ingestion-receipt",
        "source_files_deleted": False,
        "paper_authorized": False,
        "live_authorized": False,
        "files": records,
    }


def write_index(
        output: Path, repository_root: Path, evidence_root: Path,
        payload: bytes) -> None:
    absolute_repository_root = Path(os.path.abspath(repository_root))
    if (not absolute_repository_root.is_dir() or
            absolute_repository_root !=
            absolute_repository_root.resolve(strict=True)):
        raise EvidenceIndexError("repository root is unsafe")
    index_root = absolute_repository_root / "evidence-indexes"
    absolute_index_root = Path(os.path.abspath(index_root))
    absolute_output = Path(os.path.abspath(output))
    if (absolute_output.parent != absolute_index_root and
            absolute_index_root not in absolute_output.parent.parents):
        raise EvidenceIndexError("evidence index output escapes approved index root")
    probe = index_root
    while not probe.exists() and not probe.is_symlink():
        if probe == probe.parent:
            break
        probe = probe.parent
    if (probe.is_symlink() or
            Path(os.path.abspath(probe)) != probe.resolve(strict=True)):
        raise EvidenceIndexError("evidence index root contains a symlink")
    if index_root.exists() and index_root.is_symlink():
        raise EvidenceIndexError("evidence index root must not be a symlink")
    parent_metadata = index_root.parent.lstat()
    if (stat.S_ISLNK(parent_metadata.st_mode) or
            not stat.S_ISDIR(parent_metadata.st_mode) or
            parent_metadata.st_uid != os.geteuid() or
            parent_metadata.st_mode & 0o022):
        raise EvidenceIndexError("evidence index root parent is unsafe")
    index_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    index_root = index_root.resolve(strict=True)
    if absolute_index_root != index_root:
        raise EvidenceIndexError("evidence index root contains a symlink")
    if (evidence_root == index_root or
            evidence_root in index_root.parents or
            index_root in evidence_root.parents):
        raise EvidenceIndexError("evidence index root overlaps evidence payloads")
    if evidence_root == absolute_output or evidence_root in absolute_output.parents:
        raise EvidenceIndexError("evidence index output overlaps evidence payloads")

    def directory_identity(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_gid,
        )

    def file_identity(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns,
        )

    def require_private_directory(
            metadata: os.stat_result, label: str) -> None:
        if not stat.S_ISDIR(metadata.st_mode):
            raise EvidenceIndexError(f"{label} is not a directory")
        if metadata.st_uid != os.geteuid():
            raise EvidenceIndexError(f"{label} is not owned by the caller")
        if metadata.st_mode & 0o022:
            raise EvidenceIndexError(
                f"{label} is group/world writable")

    root_before = index_root.lstat()
    require_private_directory(root_before, "evidence index root")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    root_descriptor = os.open(index_root, directory_flags)
    descriptors = [root_descriptor]
    component_identities: list[tuple[str, tuple[int, ...]]] = []
    temporary_name = ""
    output_name = absolute_output.name
    parent_descriptor = root_descriptor
    file_descriptor = -1
    published = False
    published_identity: tuple[int, ...] | None = None

    def verify_published_path() -> None:
        verification_descriptors: list[int] = []
        published_descriptor = -1
        try:
            current_root = index_root.lstat()
            if directory_identity(current_root) != directory_identity(root_before):
                raise EvidenceIndexError(
                    "evidence index root changed after publication")
            verified_root = os.open(index_root, directory_flags)
            verification_descriptors.append(verified_root)
            if (directory_identity(os.fstat(verified_root)) !=
                    directory_identity(root_before)):
                raise EvidenceIndexError(
                    "evidence index root changed after publication")
            verified_parent = verified_root
            for component, expected_identity in component_identities:
                metadata = os.stat(
                    component, dir_fd=verified_parent,
                    follow_symlinks=False)
                if directory_identity(metadata) != expected_identity:
                    raise EvidenceIndexError(
                        "evidence index output path changed after publication")
                child = os.open(
                    component, directory_flags, dir_fd=verified_parent)
                verification_descriptors.append(child)
                if directory_identity(os.fstat(child)) != expected_identity:
                    raise EvidenceIndexError(
                        "evidence index output path changed after publication")
                verified_parent = child

            before = os.stat(
                output_name, dir_fd=verified_parent,
                follow_symlinks=False)
            if (published_identity is None or
                    file_identity(before) != published_identity or
                    not stat.S_ISREG(before.st_mode) or
                    before.st_nlink != 1 or
                    stat.S_IMODE(before.st_mode) != 0o600 or
                    before.st_uid != os.geteuid()):
                raise EvidenceIndexError(
                    "published evidence index identity drift")
            read_flags = (
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                getattr(os, "O_NOFOLLOW", 0)
            )
            published_descriptor = os.open(
                output_name, read_flags, dir_fd=verified_parent)
            if (file_identity(os.fstat(published_descriptor)) !=
                    published_identity):
                raise EvidenceIndexError(
                    "published evidence index identity drift")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(published_descriptor, HASH_CHUNK)
                if not chunk:
                    break
                chunks.append(chunk)
            after_descriptor = os.fstat(published_descriptor)
            after_path = os.stat(
                output_name, dir_fd=verified_parent,
                follow_symlinks=False)
            if (file_identity(after_descriptor) != published_identity or
                    file_identity(after_path) != published_identity or
                    b"".join(chunks) != payload):
                raise EvidenceIndexError(
                    "published evidence index content drift")

            # Recheck the pathname entries after reading the payload. A
            # component replaced after it was opened must not let this call
            # return a path that resolves to a different index.
            if (directory_identity(index_root.lstat()) !=
                    directory_identity(root_before)):
                raise EvidenceIndexError(
                    "evidence index root changed after publication")
            for position, (component, expected_identity) in enumerate(
                    component_identities):
                metadata = os.stat(
                    component,
                    dir_fd=verification_descriptors[position],
                    follow_symlinks=False)
                if directory_identity(metadata) != expected_identity:
                    raise EvidenceIndexError(
                        "evidence index output path changed after publication")
            final_output = os.stat(
                output_name, dir_fd=verified_parent,
                follow_symlinks=False)
            if file_identity(final_output) != published_identity:
                raise EvidenceIndexError(
                    "published evidence index identity drift")
        except OSError as error:
            raise EvidenceIndexError(
                "published evidence index path is unstable") from error
        finally:
            if published_descriptor >= 0:
                os.close(published_descriptor)
            for verification_descriptor in reversed(
                    verification_descriptors):
                os.close(verification_descriptor)

    try:
        if (directory_identity(root_before) !=
                directory_identity(os.fstat(root_descriptor))):
            raise EvidenceIndexError(
                "evidence index root changed while opening")
        relative_parent = absolute_output.parent.relative_to(
            absolute_index_root)
        for component in relative_parent.parts:
            try:
                os.mkdir(component, mode=0o700, dir_fd=parent_descriptor)
            except FileExistsError:
                pass
            before = os.stat(
                component, dir_fd=parent_descriptor, follow_symlinks=False)
            require_private_directory(
                before, "evidence index output directory")
            child_descriptor = os.open(
                component, directory_flags, dir_fd=parent_descriptor)
            descriptors.append(child_descriptor)
            if (directory_identity(before) !=
                    directory_identity(os.fstat(child_descriptor))):
                raise EvidenceIndexError(
                    "evidence index output directory changed while opening")
            component_identities.append(
                (component, directory_identity(before)))
            parent_descriptor = child_descriptor

        if not output_name or output_name in {".", ".."}:
            raise EvidenceIndexError("evidence index output name is invalid")
        try:
            metadata = os.stat(
                output_name, dir_fd=parent_descriptor,
                follow_symlinks=False)
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            if stat.S_ISLNK(metadata.st_mode):
                raise EvidenceIndexError(
                    "existing evidence index output must not be a symlink")
            raise EvidenceIndexError(
                "existing evidence index output must not be overwritten")

        file_flags = (
            os.O_RDWR | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        for _ in range(32):
            temporary_name = (
                f".{output_name}.{os.getpid()}."
                f"{secrets.token_hex(8)}.tmp")
            try:
                file_descriptor = os.open(
                    temporary_name, file_flags, 0o600,
                    dir_fd=parent_descriptor)
                break
            except FileExistsError:
                temporary_name = ""
        else:
            raise EvidenceIndexError(
                "could not allocate private evidence index temporary file")
        os.fchmod(file_descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(file_descriptor, payload[offset:])
        os.fsync(file_descriptor)
        try:
            os.link(
                temporary_name, output_name,
                src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor,
                follow_symlinks=False)
        except FileExistsError as error:
            raise EvidenceIndexError(
                "existing evidence index output must not be overwritten") from error
        published = True
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        temporary_name = ""
        os.fsync(parent_descriptor)
        published_metadata = os.stat(
            output_name, dir_fd=parent_descriptor,
            follow_symlinks=False)
        if (not stat.S_ISREG(published_metadata.st_mode) or
                published_metadata.st_nlink != 1):
            raise EvidenceIndexError(
                "published evidence index identity drift")
        published_identity = file_identity(published_metadata)
        verify_published_path()
    finally:
        failed = os.sys.exc_info()[0] is not None
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if published and failed:
            try:
                current = os.stat(
                    output_name, dir_fd=parent_descriptor,
                    follow_symlinks=False)
                if (published_identity is not None and
                        current.st_dev == published_identity[0] and
                        current.st_ino == published_identity[1]):
                    os.unlink(output_name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
            except FileNotFoundError:
                pass
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--generated-at", default="")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence_root = (
        args.evidence_root or root / "runtime-logs").resolve(strict=True)
    approved_index_root = root / "evidence-indexes"
    if (evidence_root == approved_index_root or
            evidence_root in approved_index_root.parents or
            approved_index_root in evidence_root.parents):
        raise EvidenceIndexError(
            "evidence index root overlaps evidence payloads")
    policy = root / "policies" / "heptatrader-evidence-retention-v1.json"
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat()
    index = build_index(evidence_root, policy, args.path, generated_at)
    output_relative = args.output
    if (output_relative.is_absolute() or
            output_relative.as_posix() != str(output_relative) or
            "\\" in str(output_relative) or "\0" in str(output_relative) or
            ".." in output_relative.parts or
            not output_relative.parts or
            output_relative.parts[0] != "evidence-indexes"):
        raise EvidenceIndexError(
            "evidence index output must be a normalized relative path "
            "under evidence-indexes")
    output = root / output_relative
    write_index(
        output, root, evidence_root,
        json.dumps(index, indent=2, sort_keys=True).encode() + b"\n")
    print(
        f"PASS: {index['file_count']} files {index['total_bytes']} bytes "
        f"records_sha256={index['records_sha256']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvidenceIndexError, OSError) as error:
        print(f"evidence-index: {error}", file=os.sys.stderr)
        raise SystemExit(78)
