#!/usr/bin/env python3
"""Build and self-verify a manifest-defined HeptaTrader evidence set."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Callable

import build_heptatrader_evidence_index as index_builder
import verify_heptatrader_evidence_index as index_verifier
import verify_heptatrader_evidence_set as set_verifier


MAX_INDEX_BYTES = index_builder.MAX_POLICY_BYTES * 16


class EvidenceSetBuildError(RuntimeError):
    pass


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
    )


def _publication_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
    )


def _read_index(path: Path) -> tuple[bytes, Any]:
    try:
        _, data = index_builder.stable_bytes(path, MAX_INDEX_BYTES)
        return data, index_builder.strict_json(data, "evidence index")
    except index_builder.EvidenceIndexError as error:
        raise EvidenceSetBuildError(str(error)) from error


def build_manifest(
        index_path: Path, evidence_root: Path, policy_path: Path,
        round_number: int, release_version: str,
        profile: str = set_verifier.PROFILE) -> dict[str, Any]:
    try:
        identity = set_verifier._release_identity(
            round_number, release_version, "evidence-set manifest")
    except set_verifier.EvidenceSetError as error:
        raise EvidenceSetBuildError(str(error)) from error
    if profile not in set_verifier.ROLE_PROFILES:
        raise EvidenceSetBuildError("unsupported evidence-set profile")

    index_bytes, captured_index = _read_index(index_path)
    try:
        verified_index = index_verifier.verify(
            index_path, evidence_root, policy_path, verify_files=True)
    except index_builder.EvidenceIndexError as error:
        raise EvidenceSetBuildError(str(error)) from error
    confirmed_bytes, confirmed_index = _read_index(index_path)
    if (captured_index != verified_index or
            confirmed_index != verified_index or
            confirmed_bytes != index_bytes):
        raise EvidenceSetBuildError(
            "evidence index changed across manifest construction")

    artifacts_by_role: dict[str, dict[str, Any]] = {}
    if profile == set_verifier.ENGINEERING_PROFILE:
        try:
            roles_by_path, _directory, _closure, _closure_bytes = (
                set_verifier._engineering_index_roles(
                    verified_index, evidence_root,
                    round_number, release_version))
        except set_verifier.EvidenceSetError as error:
            raise EvidenceSetBuildError(str(error)) from error
        for record in verified_index["files"]:
            role = roles_by_path[record["path"]]
            artifacts_by_role[role] = {
                "role": role,
                "path": record["path"],
                "sha256": record["sha256"],
                "size": record["size"],
                "mode": record["mode"],
                "tier": record["tier"],
            }
        required_roles = sorted(artifacts_by_role)
        try:
            set_verifier._engineering_required_roles(required_roles)
        except set_verifier.EvidenceSetError as error:
            raise EvidenceSetBuildError(str(error)) from error
    elif profile == set_verifier.RELEASE_PROFILE:
        try:
            roles_by_path, _directory, _local, _manifest_bytes = (
                set_verifier._release_index_roles(
                    verified_index, evidence_root,
                    round_number, release_version))
        except set_verifier.EvidenceSetError as error:
            raise EvidenceSetBuildError(str(error)) from error
        for record in verified_index["files"]:
            role = roles_by_path[record["path"]]
            artifacts_by_role[role] = {
                "role": role,
                "path": record["path"],
                "sha256": record["sha256"],
                "size": record["size"],
                "mode": record["mode"],
                "tier": record["tier"],
            }
        required_roles = sorted(artifacts_by_role)
        try:
            set_verifier._release_required_roles(required_roles)
        except set_verifier.EvidenceSetError as error:
            raise EvidenceSetBuildError(str(error)) from error
    else:
        role_contracts = set_verifier.ROLE_PROFILES[profile]
        for record in verified_index["files"]:
            matches = []
            for role, contract in role_contracts.items():
                path_match = contract["path_pattern"].fullmatch(
                    record["path"])
                if (path_match is not None and
                        int(path_match.group("round")) == round_number and
                        record["tier"] == contract["tier"]):
                    matches.append(role)
            if len(matches) != 1:
                raise EvidenceSetBuildError(
                    "every indexed artifact must satisfy exactly one trusted "
                    "evidence role")
            role = matches[0]
            if role in artifacts_by_role:
                raise EvidenceSetBuildError(
                    f"evidence role is not unique in the index: {role}")
            artifacts_by_role[role] = {
                "role": role,
                "path": record["path"],
                "sha256": record["sha256"],
                "size": record["size"],
                "mode": record["mode"],
                "tier": record["tier"],
            }
        required_roles = sorted(role_contracts)
        if sorted(artifacts_by_role) != required_roles:
            raise EvidenceSetBuildError(
                "evidence index does not satisfy the complete trusted "
                "role set")

    final_bytes, final_index = _read_index(index_path)
    if final_bytes != index_bytes or final_index != verified_index:
        raise EvidenceSetBuildError(
            "evidence index changed before manifest construction completed")
    return {
        "schema": set_verifier.MANIFEST_SCHEMA,
        "version": 2,
        "project_id": set_verifier.PROJECT_ID,
        "round": identity[0],
        "release_version": identity[1],
        "evidence_set_id": f"round{round_number}-certification",
        "profile": profile,
        "coverage": (
            "manifest-defined"
            if verified_index["selection_mode"] == "explicit"
            else "full-index-eligible-tree"),
        "index": {
            "sha256": hashlib.sha256(index_bytes).hexdigest(),
            "records_sha256": verified_index["records_sha256"],
            "selection_mode": verified_index["selection_mode"],
        },
        "required_roles": required_roles,
        "source_files_deleted": False,
        "source_removal_authorized": False,
        "paper_authorized": False,
        "live_authorized": False,
        "artifacts": [
            artifacts_by_role[role] for role in required_roles
        ],
    }


def _atomic_validated_write(
        output: Path, payload: bytes,
        validate: Callable[[Path], None]) -> None:
    absolute = Path(os.path.abspath(output))
    if output != absolute or not absolute.name:
        raise EvidenceSetBuildError(
            "evidence-set output must be canonical and absolute")
    parts = absolute.parent.parts
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    components: list[tuple[str, tuple[int, ...]]] = []
    temporary_name = (
        f".{absolute.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    parent_descriptor = os.open("/", directory_flags)
    descriptors.append(parent_descriptor)
    published = False
    published_identity: tuple[int, ...] | None = None
    published_inode: tuple[int, int] | None = None

    def anchored_bytes(name: str) -> tuple[os.stat_result, bytes]:
        before = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or
                before.st_nlink != 1 or
                before.st_uid != os.geteuid() or
                stat.S_IMODE(before.st_mode) != 0o600):
            raise EvidenceSetBuildError(
                "evidence-set output destination is unsafe")
        read_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(name, read_flags, dir_fd=parent_descriptor)
        try:
            opened = os.fstat(descriptor)
            chunks: list[bytes] = []
            remaining = set_verifier.MAX_MANIFEST_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after_descriptor = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (_identity(before) != _identity(opened) or
                _identity(opened) != _identity(after_descriptor) or
                _identity(after_descriptor) != _identity(after_path)):
            raise EvidenceSetBuildError(
                "evidence-set output changed during stable read")
        contents = b"".join(chunks)
        if len(contents) != opened.st_size:
            raise EvidenceSetBuildError(
                "evidence-set output exceeds the size limit")
        return opened, contents

    def revalidate_parents(label: str) -> None:
        for index, (component, expected) in enumerate(components):
            current = os.stat(
                component, dir_fd=descriptors[index],
                follow_symlinks=False)
            if _directory_identity(current) != expected:
                raise EvidenceSetBuildError(
                    f"evidence-set output parent changed {label}")

    try:
        for component in parts[1:]:
            before = os.stat(
                component, dir_fd=parent_descriptor,
                follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise EvidenceSetBuildError(
                    "evidence-set output contains an unsafe parent")
            child = os.open(
                component, directory_flags, dir_fd=parent_descriptor)
            if (_directory_identity(before) !=
                    _directory_identity(os.fstat(child))):
                os.close(child)
                raise EvidenceSetBuildError(
                    "evidence-set output parent changed while opening")
            components.append(
                (component, _directory_identity(before)))
            descriptors.append(child)
            parent_descriptor = child
        parent = os.fstat(parent_descriptor)
        if (parent.st_uid != os.geteuid() or
                parent.st_mode & 0o022):
            raise EvidenceSetBuildError(
                "evidence-set output parent must be caller-owned and protected")
        try:
            existing = os.stat(
                absolute.name, dir_fd=parent_descriptor,
                follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            _metadata, existing_payload = anchored_bytes(absolute.name)
            if existing_payload != payload:
                raise EvidenceSetBuildError(
                    "existing evidence-set manifest is immutable and differs")
            revalidate_parents("during idempotent read")
            validate(absolute)
            _metadata, confirmed_payload = anchored_bytes(absolute.name)
            if confirmed_payload != payload:
                raise EvidenceSetBuildError(
                    "existing evidence-set manifest changed during validation")
            revalidate_parents("during idempotent validation")
            return

        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(
            temporary_name, flags, 0o600, dir_fd=parent_descriptor)
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
            temporary_identity = _identity(os.fstat(descriptor))
        finally:
            os.close(descriptor)

        revalidate_parents("during publication")
        validate(absolute.parent / temporary_name)
        current_temporary = os.stat(
            temporary_name, dir_fd=parent_descriptor,
            follow_symlinks=False)
        if (_identity(current_temporary) != temporary_identity or
                not stat.S_ISREG(current_temporary.st_mode) or
                stat.S_IMODE(current_temporary.st_mode) != 0o600):
            raise EvidenceSetBuildError(
                "temporary evidence-set manifest changed during validation")
        try:
            os.link(
                temporary_name, absolute.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False)
        except FileExistsError as error:
            raise EvidenceSetBuildError(
                "evidence-set manifest appeared concurrently and was not "
                "overwritten") from error
        published = True
        published_inode = (
            current_temporary.st_dev, current_temporary.st_ino)
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        temporary_name = ""
        published_metadata = os.stat(
            absolute.name, dir_fd=parent_descriptor,
            follow_symlinks=False)
        published_identity = _publication_identity(published_metadata)
        if (published_identity !=
                _publication_identity(current_temporary) or
                not stat.S_ISREG(published_metadata.st_mode) or
                published_metadata.st_nlink != 1 or
                published_metadata.st_uid != os.geteuid() or
                stat.S_IMODE(published_metadata.st_mode) != 0o600):
            raise EvidenceSetBuildError(
                "published evidence-set manifest identity drift")
        os.fsync(parent_descriptor)
        validate(absolute)
        revalidate_parents("after publication")
        final_metadata, published_bytes = anchored_bytes(absolute.name)
        if published_bytes != payload:
            raise EvidenceSetBuildError(
                "published evidence-set manifest content drift")
        if (_publication_identity(final_metadata) != published_identity):
            raise EvidenceSetBuildError(
                "published evidence-set manifest identity drift")
    except BaseException as error:
        if published:
            try:
                current = os.stat(
                    absolute.name, dir_fd=parent_descriptor,
                    follow_symlinks=False)
                if (published_inode is not None and
                        (current.st_dev, current.st_ino) == published_inode):
                    os.unlink(absolute.name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
            except (FileNotFoundError, OSError):
                pass
        if isinstance(error, EvidenceSetBuildError):
            raise
        if isinstance(error, (OSError, index_builder.EvidenceIndexError)):
            raise EvidenceSetBuildError(
                "evidence-set manifest publication failed") from error
        raise
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def build_and_publish(
        repository_root: Path, index_path: Path, evidence_root: Path,
        policy_path: Path, output: Path, round_number: int,
        release_version: str,
        profile: str = set_verifier.PROFILE) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    evidence = Path(os.path.abspath(evidence_root))
    destination = Path(os.path.abspath(output))
    if (root != root.resolve(strict=True) or
            evidence != evidence.resolve(strict=True)):
        raise EvidenceSetBuildError(
            "repository and evidence roots must be canonical")
    expected_name = (
        f"heptatrader-round{round_number}-"
        "evidence-set-manifest-v2.json")
    if destination.name != expected_name:
        raise EvidenceSetBuildError(
            f"evidence-set output must be named {expected_name}")
    index_output_root = root / "evidence-indexes"
    if (destination.parent != evidence and
            destination.parent != index_output_root):
        raise EvidenceSetBuildError(
            "evidence-set output must be in the evidence root or "
            "evidence-indexes root")

    manifest = build_manifest(
        index_path, evidence, policy_path,
        round_number, release_version, profile)
    if (manifest["index"]["selection_mode"] == "complete-tree" and
            destination.parent == evidence):
        raise EvidenceSetBuildError(
            "a complete-tree manifest must be published outside its "
            "indexed evidence root")
    payload = index_builder.canonical_json(manifest)
    if len(payload) > set_verifier.MAX_MANIFEST_BYTES:
        raise EvidenceSetBuildError("evidence-set manifest exceeds size limit")

    def validate(path: Path) -> None:
        try:
            set_verifier.verify(
                path, index_path, evidence, policy_path)
        except set_verifier.EvidenceSetError as error:
            raise EvidenceSetBuildError(
                "generated evidence-set manifest failed self-verification"
            ) from error

    _atomic_validated_write(destination, payload, validate)
    try:
        return set_verifier.verify(
            destination, index_path, evidence, policy_path)
    except set_verifier.EvidenceSetError as error:
        raise EvidenceSetBuildError(
            "published evidence-set manifest failed self-verification"
        ) from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--round", dest="round_number", type=int, required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument(
        "--profile", default=set_verifier.PROFILE,
        choices=sorted(set_verifier.ROLE_PROFILES))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    resolve = lambda value: value if value.is_absolute() else root / value
    evidence_root = resolve(
        args.evidence_root or Path("runtime-logs")).resolve(strict=True)
    output = (
        resolve(args.output)
        if args.output is not None
        else evidence_root / (
            f"heptatrader-round{args.round_number}-"
            "evidence-set-manifest-v2.json"))
    report = build_and_publish(
        root, resolve(args.index), evidence_root,
        root / "policies/heptatrader-evidence-retention-v1.json",
        output, args.round_number, args.release_version, args.profile)
    print(
        f"PASS: evidence set {report['evidence_set_id']} "
        f"coverage={report['coverage']} roles={report['role_count']} "
        f"manifest_sha256={report['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvidenceSetBuildError, OSError) as error:
        print(f"evidence-set-build: {error}", file=os.sys.stderr)
        raise SystemExit(78)
