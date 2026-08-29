#!/usr/bin/env python3

"""Project a verified combined runtime into a passive host install archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
from typing import Any

import build_heptatrader_runtime_package as runtime_builder
import hepta_shadow_host_installer as installer
import verify_heptatrader_runtime_package as runtime_verifier


class ShadowArchiveBuildError(RuntimeError):
    """The verified runtime could not be projected into the host boundary."""


IDENTITY_SOURCE_PATH = (
    "usr/share/doc/heptatrader/examples/"
    "hepta-agent-trust-domain-paper-identities-v1.json.example")
IDENTITY_ARCHIVE_PATH = (
    "etc/heptatrader/"
    "hepta-agent-trust-domain-paper-identities-v1.json")
IDENTITY_SIZE = 257
IDENTITY_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
IDENTITY_BYTES = b"""{
  "identities": [],
  "live_authorized": false,
  "paper_authorized": false,
  "schema": "hepta.agent-trust-domain-paper-identities.v1",
  "source_policy_sha256": "sha256:08d430d53e4813cd0a43a23beeb92344af2130dca425814cbf7285059d90f90c",
  "version": 1
}
"""


SHADOW_AGENT_EXCLUSIONS = frozenset({
    "usr/libexec/hepta-paper-receipt-contracts-v2-compat",
    "usr/libexec/hepta-p1-paper-canary-backend-adapter",
    "usr/libexec/hepta-p1-paper-canary-crash-emergency-closer",
    "usr/libexec/hepta-p1-paper-canary-executor",
    "usr/libexec/hepta-p1-paper-canary-handoff-producer",
    "usr/libexec/hepta-p1-paper-canary-launch-joiner",
    "usr/libexec/hepta-p1-paper-canary-owner-provisioner",
    "usr/libexec/hepta-p1-paper-canary-root-coordinator",
    "usr/libexec/hepta-p1-paper-canary-terminal-prover",
    "usr/lib/systemd/system/hepta-p1-paper-canary-capture.service",
    "usr/lib/systemd/system/hepta-p1-paper-canary-executor.service",
    "usr/lib/systemd/system/hepta-p1-paper-canary-root-coordinator.service",
    # Root recovery-only terminal witness tooling is part of the full Agent OS
    # closure but must never be shipped into the passive SHADOW archive.
    "usr/libexec/hepta-p1-paper-terminal-witness-verifier",
    "usr/lib/systemd/system/hepta-local-paper-fail-close@.service",
    "usr/lib/systemd/system/hepta-p1-paper-terminal-cutoff@.service",
    "usr/lib/systemd/system/hepta-p1-paper-terminal-witness-verifier@.service",
})
SHADOW_AGENT_FILES: dict[str, int] = {
    path: mode
    for path, mode in runtime_verifier.AGENT_FILES.items()
    if path not in SHADOW_AGENT_EXCLUSIONS
}
SHADOW_FILES: dict[str, int] = dict(SHADOW_AGENT_FILES)
SHADOW_FILES[IDENTITY_ARCHIVE_PATH] = 0o600


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _agent_payloads(
    package_bytes: bytes,
    manifest: dict[str, Any],
) -> dict[str, tuple[int, bytes]]:
    root = manifest["root"]
    prefix = root + "/"
    internal_path = prefix + runtime_verifier.INTERNAL_MANIFEST
    expected = SHADOW_AGENT_FILES
    payloads: dict[str, tuple[int, bytes]] = {}
    try:
        archive = tarfile.open(fileobj=io.BytesIO(package_bytes), mode="r:")
    except tarfile.TarError as error:
        raise ShadowArchiveBuildError(
            "verified runtime package is not a plain tar") from error
    with archive:
        for member in archive.getmembers():
            if member.name == internal_path:
                continue
            if not member.name.startswith(prefix):
                raise ShadowArchiveBuildError(
                    "verified runtime member escaped the frozen root")
            relative = member.name[len(prefix):]
            if relative not in expected:
                continue
            if relative in payloads:
                raise ShadowArchiveBuildError(
                    "duplicate Agent member in verified runtime")
            stream = archive.extractfile(member)
            if stream is None:
                raise ShadowArchiveBuildError(
                    "Agent member in verified runtime is unreadable")
            if member.size < 0 or member.size > installer.MAX_ARCHIVE_BYTES:
                raise ShadowArchiveBuildError(
                    "Agent member exceeds the passive installer bound")
            payload = stream.read(installer.MAX_ARCHIVE_BYTES + 1)
            mode = stat.S_IMODE(member.mode)
            if len(payload) != member.size or mode != expected[relative]:
                raise ShadowArchiveBuildError(
                    "Agent member metadata drifted after runtime verification")
            installer.normalized_member(relative)
            if not installer.allowed_file_member(PurePosixPath(relative)):
                raise ShadowArchiveBuildError(
                    "Agent member is outside the passive installer boundary")
            payloads[relative] = (mode, payload)
    if set(payloads) != set(expected):
        raise ShadowArchiveBuildError(
            "verified runtime did not yield the exact Agent file closure")
    return payloads


def _projection_payloads(
    agent_payloads: dict[str, tuple[int, bytes]],
) -> dict[str, tuple[int, bytes]]:
    source = IDENTITY_SOURCE_PATH
    source_record = agent_payloads.get(source)
    if source_record is None:
        raise ShadowArchiveBuildError(
            "verified runtime omitted the PAPER identity projection source")
    source_mode, source_payload = source_record
    if (
            source_mode != 0o644 or
            source_payload != IDENTITY_BYTES or
            len(source_payload) != IDENTITY_SIZE or
            _digest(source_payload) != IDENTITY_SHA256 or
            source_payload != installer.PAPER_IDENTITY_MANIFEST_BYTES or
            IDENTITY_ARCHIVE_PATH !=
            installer.PAPER_IDENTITY_MANIFEST.as_posix()):
        raise ShadowArchiveBuildError(
            "PAPER identity projection source is not the exact deny-all default")
    projected = dict(agent_payloads)
    projected[IDENTITY_ARCHIVE_PATH] = (
        0o600, source_payload)
    if set(projected) != set(SHADOW_FILES):
        raise ShadowArchiveBuildError(
            "install projection is not the exact SHADOW file closure")
    return projected


def _directories(paths: set[str]) -> list[str]:
    directories: set[str] = set()
    for value in paths:
        path = PurePosixPath(value)
        for parent in path.parents:
            if parent == PurePosixPath("."):
                continue
            normalized = installer.normalized_member(parent.as_posix())
            directories.add(normalized.as_posix())
    return sorted(directories)


def _tar_info(name: str, mode: int, kind: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    info.linkname = ""
    info.devmajor = 0
    info.devminor = 0
    info.pax_headers = {}
    return info


def archive_bytes(payloads: dict[str, tuple[int, bytes]]) -> bytes:
    if set(payloads) != set(SHADOW_FILES):
        raise ShadowArchiveBuildError(
            "install projection is not the exact SHADOW file closure")
    identity_mode, identity_payload = payloads[IDENTITY_ARCHIVE_PATH]
    if (
            identity_mode != 0o600 or identity_payload != IDENTITY_BYTES or
            len(identity_payload) != IDENTITY_SIZE or
            _digest(identity_payload) != IDENTITY_SHA256):
        raise ShadowArchiveBuildError(
            "install projection identity is not the exact deny-all default")
    plain = io.BytesIO()
    with tarfile.open(
            fileobj=plain, mode="w:", format=tarfile.USTAR_FORMAT) as archive:
        for directory in _directories(set(payloads)):
            info = _tar_info(directory, 0o755, tarfile.DIRTYPE)
            info.size = 0
            archive.addfile(info)
        for relative in sorted(payloads):
            mode, payload = payloads[relative]
            if mode != SHADOW_FILES[relative]:
                raise ShadowArchiveBuildError(
                    "install projection mode differs from SHADOW closure")
            info = _tar_info(relative, mode, tarfile.REGTYPE)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    compressed = io.BytesIO()
    with gzip.GzipFile(
            filename="", mode="wb", compresslevel=9,
            fileobj=compressed, mtime=0) as output:
        output.write(plain.getvalue())
    result = compressed.getvalue()
    if len(result) > installer.MAX_ARCHIVE_BYTES:
        raise ShadowArchiveBuildError(
            "projected archive exceeds the passive installer bound")
    return result


def build(
    runtime_package: Path,
    runtime_manifest: Path,
    output: Path,
) -> dict[str, Any]:
    verification = runtime_verifier.verify_package(
        runtime_package, runtime_manifest)
    package_bytes = runtime_verifier.stable_private_bytes(
        runtime_package, "runtime package",
        runtime_verifier.MAX_PACKAGE_BYTES)
    manifest_bytes = runtime_verifier.stable_private_bytes(
        runtime_manifest, "external runtime manifest",
        runtime_verifier.MAX_MANIFEST_BYTES)
    if (_digest(package_bytes) != verification["package_sha256"] or
            _digest(manifest_bytes) != verification["manifest_sha256"]):
        raise ShadowArchiveBuildError(
            "runtime inputs changed after verification")
    manifest = runtime_verifier.validate_manifest(
        runtime_verifier.strict_json(
            manifest_bytes, "external runtime manifest"))
    if manifest_bytes != runtime_verifier.canonical_json(manifest) + b"\n":
        raise ShadowArchiveBuildError("runtime manifest is not canonical")
    agent_payloads = _agent_payloads(package_bytes, manifest)
    payloads = _projection_payloads(agent_payloads)
    projected = archive_bytes(payloads)
    records, _directory_records = installer.archive_records_bytes(projected)
    if set(records) != set(SHADOW_FILES):
        raise ShadowArchiveBuildError(
            "projected archive failed the exact SHADOW inventory check")
    try:
        runtime_builder._write_new_private(
            output, projected, "shadow install archive")
    except runtime_builder.RuntimeBuildError as error:
        raise ShadowArchiveBuildError(str(error)) from error
    return {
        "schema": "hepta.shadow-runtime-projection.v2",
        "release_version": verification["release_version"],
        "runtime_package_sha256": verification["package_sha256"],
        "runtime_manifest_sha256": verification["manifest_sha256"],
        "archive_sha256": _digest(projected),
        "file_count": len(payloads),
        "host_state_paths_included": True,
        "host_state_path_count": 1,
        "default_deny_identity_included": True,
        "default_deny_identity_archive_path": IDENTITY_ARCHIVE_PATH,
        "default_deny_identity_sha256": IDENTITY_SHA256,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--runtime-package", required=True, type=Path)
    parser.add_argument("--runtime-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        report = build(
            arguments.runtime_package,
            arguments.runtime_manifest,
            arguments.output,
        )
    except (
            ShadowArchiveBuildError,
            runtime_verifier.RuntimePackageError,
            installer.InstallError,
            OSError,
            tarfile.TarError) as error:
        print(f"build-hepta-shadow-runtime-archive: FAIL: {error}", file=sys.stderr)
        return 2
    print(
        "build-hepta-shadow-runtime-archive: PASS "
        f"release={report['release_version']} "
        f"files={report['file_count']} "
        f"archive={report['archive_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
