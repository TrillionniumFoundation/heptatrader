#!/usr/bin/env python3

"""Narrow root-owned passive installer for the Hepta SHADOW runtime."""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any


MANIFEST_SCHEMA = "hepta.shadow-runtime-install-manifest.v2"
MANIFEST_VERSION = 2
RECEIPT_SCHEMA = "hepta.shadow-runtime-install-receipt.v4"
RECEIPT_VERSION = 4
CONSUMPTION_EVIDENCE_SCHEMA = (
    "hepta.shadow-runtime-install-consumption-evidence.v3")
CONSUMPTION_EVIDENCE_VERSION = 3
CURRENT_INSTALL_POINTER_SCHEMA = "hepta.shadow-runtime-current-install.v1"
CURRENT_INSTALL_POINTER_VERSION = 1
RECEIPT_READER_GID = 1000
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILES = 256
MAX_CONSUMER_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_INSTALL_GENERATION = (1 << 63) - 1
EXPECTED_SHADOW_FILE_COUNT = 128
SHA256_IDENTITY = re.compile(r"sha256:[0-9a-f]{64}")
TRANSACTION_LOCK_PATH = Path("/var/lib/hepta/.shadow-runtime-install.lock")
CURRENT_INSTALL_POINTER_PATH = Path(
    "/var/lib/hepta/shadow-runtime-install-state/current-install-v1.json")
INSTALL_RECEIPT_FIELDS = frozenset({
    "schema", "version", "finished_at_ms", "domain", "archive_sha256",
    "source_baseline_sha256", "installer_sha256", "installed_file_count",
    "installed_paths_sha256", "backup_root", "replaced_file_count",
    "new_file_count", "default_deny_identity_manifest", "reader_gid",
    "install_generation", "predecessor_install_generation",
    "predecessor_current_install_pointer_file_sha256",
    "transaction_lock", "preflight_before", "preflight_after",
    "preflight_continuity_claimed", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "services_started",
    "services_enabled", "status", "body_sha256",
})
INSTALL_CONSUMPTION_EVIDENCE_FIELDS = frozenset({
    "schema", "version", "receipt_path", "receipt_file_sha256",
    "receipt_body_sha256", "manifest_path", "manifest_file_sha256",
    "archive_sha256", "source_baseline_sha256", "installer_sha256",
    "installed_file_count", "installed_paths_sha256", "closure_sha256",
    "transaction_lock", "default_deny_identity_sha256", "lock_mode",
    "verified_under_lock", "domain", "backup_root", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "current_install_pointer_path", "current_install_pointer_file_sha256",
    "install_generation", "predecessor_install_generation",
    "predecessor_current_install_pointer_file_sha256",
})
CURRENT_INSTALL_POINTER_FIELDS = frozenset({
    "schema", "version", "generation", "domain", "backup_root",
    "manifest_path", "manifest_file_sha256", "receipt_path",
    "receipt_file_sha256", "archive_sha256", "source_baseline_sha256",
    "installer_sha256", "installed_file_count", "installed_paths_sha256",
    "transaction_lock_path", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
})
PAPER_UNITS = (
    "hepta-execution-ib-paper.service",
    "hepta-execution-ib-paper.socket",
    "hepta-execution-events-ib-paper.socket",
    "hepta-execution-ib-paper@alpha.service",
    "hepta-execution-ib-paper@alpha.socket",
    "hepta-execution-events-ib-paper@alpha.socket",
    "hepta-ib-paper-domain-preflight@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.service",
    "hepta-ib-paper-campaign-operator@alpha.socket",
)
INSTALLATION_BLOCKING_UNITS = (
    "hepta-tool-gateway@alpha.service",
    "hepta-tool-gateway@alpha.socket",
    "hepta-tool-session-supervisor@alpha.socket",
    "hepta-p1-watch-activation.service",
    "hepta-p1-watch-activation-reconcile.service",
    "hepta-p1-watch-activation-reconcile.timer",
    "hepta-shadow-watch-custodian@alpha.service",
    "hepta-shadow-watch-custodian-reconcile@alpha.service",
    "hepta-shadow-watch-custodian-reconcile@alpha.timer",
    "hepta-shadow-watch-collector@alpha.service",
    "hepta-shadow-watch-collector@alpha.timer",
    "hepta-shadow-watch-export@alpha.service",
)
ALLOWED_TOP = PurePosixPath("usr")
PAPER_IDENTITY_MANIFEST = PurePosixPath(
    "etc/heptatrader/"
    "hepta-agent-trust-domain-paper-identities-v1.json")
INSTALLER_MEMBER = PurePosixPath("usr/libexec/hepta-shadow-host-installer")
PAPER_IDENTITY_MANIFEST_SHA256 = (
    "sha256:4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435")
PAPER_IDENTITY_MANIFEST_BYTES = b"""{
  "identities": [],
  "live_authorized": false,
  "paper_authorized": false,
  "schema": "hepta.agent-trust-domain-paper-identities.v1",
  "source_policy_sha256": "sha256:08d430d53e4813cd0a43a23beeb92344af2130dca425814cbf7285059d90f90c",
  "version": 1
}
"""
ALLOWED_PREFIXES = (
    PurePosixPath("usr/bin"),
    PurePosixPath("usr/libexec"),
    PurePosixPath("usr/lib/systemd/system"),
    PurePosixPath("usr/lib/tmpfiles.d"),
    PurePosixPath("usr/share/heptatrader"),
    PurePosixPath("usr/share/doc/heptatrader"),
)
FORBIDDEN_MEMBER_TOKENS = (
    "hepta-ib-executiond",
    "hepta-executiond",
    "hepta-execution-ib-paper",
    "hepta-ib-paper-domain-authority",
    "hepta-ib-paper-campaign-operator",
)


class InstallError(RuntimeError):
    """Fail-closed passive-install error."""


class TransactionLockCompromisedError(InstallError):
    """The persistent transaction lock no longer names the held inode."""


TransactionLock = tuple[
    Path, int, int, str, tuple[int, ...], bool, int, int, bool]


@dataclass
class VerifiedInstallation:
    """A verified passive installation whose persistent lock remains held."""

    transaction_lock: TransactionLock
    receipt_path: Path
    receipt_payload: bytes
    receipt_identity: tuple[int, ...]
    receipt: dict[str, Any]
    manifest_path: Path
    manifest_payload: bytes
    manifest_identity: tuple[int, ...]
    manifest: dict[str, Any]
    current_install_pointer_path: Path
    current_install_pointer_payload: bytes
    current_install_pointer_identity: tuple[int, ...]
    current_install_pointer: dict[str, Any]
    filesystem_root: Path
    runtime_uid: int
    runtime_gid: int
    expected_domain: str
    expected_backup_root: Path
    receipt_reader_gid: int
    lock_path: Path
    lock_owner_uid: int
    lock_owner_gid: int
    strict_ancestors: bool
    expected_file_count: int
    closure_identities: dict[str, tuple[int, ...]]
    evidence: dict[str, Any]


@dataclass(frozen=True)
class CurrentInstallPointerState:
    """The fixed current-install pointer observed under the install lock."""

    payload: bytes
    metadata: os.stat_result
    document: dict[str, Any]


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_file(path: Path, maximum: int = MAX_ARCHIVE_BYTES) -> str:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise InstallError("INSTALL_INPUT_NOT_SINGLE_LINK_REGULAR")
    if metadata.st_size > maximum:
        raise InstallError("INSTALL_INPUT_TOO_LARGE")
    return digest_bytes(path.read_bytes())


def strict_json(path: Path) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise InstallError("INSTALL_MANIFEST_DUPLICATE_KEY")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                InstallError("INSTALL_MANIFEST_NON_FINITE")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallError("INSTALL_MANIFEST_INVALID") from error
    if not isinstance(value, dict):
        raise InstallError("INSTALL_MANIFEST_INVALID")
    return value


def strict_json_bytes(payload: bytes, reason: str) -> dict[str, Any]:
    """Decode one canonical-security JSON object without duplicate keys."""

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise InstallError(reason)
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                InstallError(reason)),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise InstallError(reason) from error
    if not isinstance(value, dict):
        raise InstallError(reason)
    return value


def normalized_member(name: str) -> PurePosixPath:
    if (
            type(name) is not str or not name or name.startswith("/") or
            "\\" in name):
        raise InstallError("INSTALL_ARCHIVE_PATH_INVALID")
    path = PurePosixPath(name.rstrip("/"))
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise InstallError("INSTALL_ARCHIVE_PATH_INVALID")
    if path == ALLOWED_TOP:
        return path
    prefix_allowed = any(
        path == prefix or path.is_relative_to(prefix) or
        prefix.is_relative_to(path)
        for prefix in ALLOWED_PREFIXES)
    exact_file_allowed = (
        path == PAPER_IDENTITY_MANIFEST or
        PAPER_IDENTITY_MANIFEST.is_relative_to(path))
    if not prefix_allowed and not exact_file_allowed:
        raise InstallError("INSTALL_ARCHIVE_PATH_NOT_ALLOWED")
    if any(token in path.name for token in FORBIDDEN_MEMBER_TOKENS):
        raise InstallError("INSTALL_ARCHIVE_MUTATION_SURFACE_FORBIDDEN")
    return path


def allowed_file_member(path: PurePosixPath) -> bool:
    return (
        path == PAPER_IDENTITY_MANIFEST or
        any(path == prefix or path.is_relative_to(prefix)
            for prefix in ALLOWED_PREFIXES)
    )


def allowed_file_mode(path: PurePosixPath, mode: int) -> bool:
    if path == PAPER_IDENTITY_MANIFEST:
        return mode == 0o600
    return mode in {0o644, 0o755}


def validate_manifest_document(document: Any) -> dict[str, Any]:
    expected = {
        "schema", "version", "archive_sha256", "source_baseline_sha256",
        "installer_sha256", "files", "paper_authorized",
        "live_authorized", "mutation_attempted", "direct_broker_access",
    }
    if (
            not isinstance(document, dict) or set(document) != expected or
            document.get("schema") != MANIFEST_SCHEMA):
        raise InstallError("INSTALL_MANIFEST_FIELDS_INVALID")
    if (
            type(document.get("version")) is not int or
            document["version"] != MANIFEST_VERSION):
        raise InstallError("INSTALL_MANIFEST_VERSION_INVALID")
    for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access"):
        if document.get(field) is not False:
            raise InstallError("INSTALL_MANIFEST_BOUNDARY_INVALID")
    for field in ("archive_sha256", "source_baseline_sha256",
                  "installer_sha256"):
        value = document.get(field)
        if type(value) is not str or SHA256_IDENTITY.fullmatch(value) is None:
            raise InstallError("INSTALL_MANIFEST_DIGEST_INVALID")
    files = document.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise InstallError("INSTALL_MANIFEST_FILES_INVALID")
    seen: set[str] = set()
    ordered_paths: list[str] = []
    installer_record: dict[str, Any] | None = None
    for record in files:
        if not isinstance(record, dict) or set(record) != {
                "path", "mode", "size", "sha256"}:
            raise InstallError("INSTALL_MANIFEST_FILE_INVALID")
        path = record.get("path")
        if type(path) is not str:
            raise InstallError("INSTALL_MANIFEST_FILE_INVALID")
        relative = normalized_member(path)
        if not allowed_file_member(relative):
            raise InstallError("INSTALL_MANIFEST_FILE_PATH_NOT_ALLOWED")
        if relative.as_posix() in seen:
            raise InstallError("INSTALL_MANIFEST_DUPLICATE_PATH")
        seen.add(relative.as_posix())
        ordered_paths.append(relative.as_posix())
        mode = record.get("mode")
        if (
                not isinstance(mode, str) or
                mode not in {"0600", "0644", "0755"} or
                not allowed_file_mode(relative, int(mode, 8))):
            raise InstallError("INSTALL_MANIFEST_MODE_INVALID")
        if (type(record.get("size")) is not int or
                record["size"] < 0 or record["size"] > MAX_ARCHIVE_BYTES):
            raise InstallError("INSTALL_MANIFEST_SIZE_INVALID")
        digest = record.get("sha256")
        if type(digest) is not str or SHA256_IDENTITY.fullmatch(digest) is None:
            raise InstallError("INSTALL_MANIFEST_DIGEST_INVALID")
        if (
                relative == PAPER_IDENTITY_MANIFEST and
                (record["size"] != len(PAPER_IDENTITY_MANIFEST_BYTES) or
                 digest != PAPER_IDENTITY_MANIFEST_SHA256)):
            raise InstallError("INSTALL_MANIFEST_PAPER_IDENTITY_DRIFT")
        if relative == INSTALLER_MEMBER:
            installer_record = record
    if PAPER_IDENTITY_MANIFEST.as_posix() not in seen:
        raise InstallError("INSTALL_MANIFEST_PAPER_IDENTITY_MISSING")
    if ordered_paths != sorted(ordered_paths):
        raise InstallError("INSTALL_MANIFEST_PATH_ORDER_INVALID")
    if ordered_paths[0] != PAPER_IDENTITY_MANIFEST.as_posix():
        raise InstallError("INSTALL_MANIFEST_PAPER_IDENTITY_ORDER_INVALID")
    if (
            installer_record is None or installer_record["mode"] != "0755" or
            installer_record["sha256"] != document["installer_sha256"]):
        raise InstallError("INSTALL_MANIFEST_INSTALLER_BINDING_INVALID")
    return document


def load_manifest(path: Path) -> dict[str, Any]:
    return validate_manifest_document(strict_json(path))


def _archive_records_from_handle(
    handle: tarfile.TarFile,
) -> tuple[dict[str, tarfile.TarInfo], set[str]]:
    records: dict[str, tarfile.TarInfo] = {}
    directories: set[str] = set()
    for member in handle.getmembers():
        relative = normalized_member(member.name).as_posix()
        if relative in records or relative in directories:
            raise InstallError("INSTALL_ARCHIVE_DUPLICATE_PATH")
        if member.uid != 0 or member.gid != 0:
            raise InstallError("INSTALL_ARCHIVE_OWNER_INVALID")
        if member.mtime != 0:
            raise InstallError("INSTALL_ARCHIVE_MTIME_INVALID")
        if member.isdir():
            if stat.S_IMODE(member.mode) != 0o755:
                raise InstallError("INSTALL_ARCHIVE_DIRECTORY_MODE_INVALID")
            directories.add(relative)
        elif member.isfile():
            if not allowed_file_member(PurePosixPath(relative)):
                raise InstallError("INSTALL_ARCHIVE_FILE_PATH_NOT_ALLOWED")
            if not allowed_file_mode(
                    PurePosixPath(relative), stat.S_IMODE(member.mode)):
                raise InstallError("INSTALL_ARCHIVE_FILE_MODE_INVALID")
            records[relative] = member
        else:
            raise InstallError("INSTALL_ARCHIVE_TYPE_INVALID")
    return records, directories


def archive_records(archive: Path) -> tuple[dict[str, tarfile.TarInfo], set[str]]:
    try:
        handle = tarfile.open(archive, "r:gz")
    except (OSError, tarfile.TarError) as error:
        raise InstallError("INSTALL_ARCHIVE_INVALID") from error
    with handle:
        return _archive_records_from_handle(handle)


def archive_records_bytes(
    payload: bytes,
) -> tuple[dict[str, tarfile.TarInfo], set[str]]:
    if type(payload) is not bytes or len(payload) > MAX_ARCHIVE_BYTES:
        raise InstallError("INSTALL_ARCHIVE_INVALID")
    try:
        handle = tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz")
    except (OSError, tarfile.TarError) as error:
        raise InstallError("INSTALL_ARCHIVE_INVALID") from error
    with handle:
        return _archive_records_from_handle(handle)


def verify_archive(
    archive: Path,
    manifest: dict[str, Any],
) -> dict[str, bytes]:
    if digest_file(archive) != manifest["archive_sha256"]:
        raise InstallError("INSTALL_ARCHIVE_DIGEST_MISMATCH")
    records, _directories = archive_records(archive)
    expected = {record["path"]: record for record in manifest["files"]}
    if set(records) != set(expected):
        raise InstallError("INSTALL_ARCHIVE_INVENTORY_MISMATCH")
    payloads: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as handle:
        for relative, record in expected.items():
            member = records[relative]
            stream = handle.extractfile(member)
            if stream is None:
                raise InstallError("INSTALL_ARCHIVE_MEMBER_READ_FAILED")
            payload = stream.read(MAX_ARCHIVE_BYTES + 1)
            if (len(payload) != record["size"] or
                    len(payload) != member.size or
                    digest_bytes(payload) != record["sha256"] or
                    stat.S_IMODE(member.mode) != int(record["mode"], 8)):
                raise InstallError("INSTALL_ARCHIVE_MEMBER_MISMATCH")
            if (
                    PurePosixPath(relative) == PAPER_IDENTITY_MANIFEST and
                    payload != PAPER_IDENTITY_MANIFEST_BYTES):
                raise InstallError("INSTALL_ARCHIVE_PAPER_IDENTITY_DRIFT")
            if (
                    PurePosixPath(relative) == INSTALLER_MEMBER and
                    digest_bytes(payload) != manifest["installer_sha256"]):
                raise InstallError("INSTALL_ARCHIVE_INSTALLER_BINDING_INVALID")
            payloads[relative] = payload
    return payloads


def command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
    }
    return subprocess.run(
        arguments, text=True, capture_output=True, timeout=20,
        env=environment, check=False)


def safety_preflight(domain: str) -> dict[str, Any]:
    if domain != "alpha":
        raise InstallError("INSTALL_DOMAIN_INVALID")
    unit_states: dict[str, str] = {}
    for unit in PAPER_UNITS:
        result = command(["/usr/bin/systemctl", "is-active", unit])
        state = result.stdout.strip()
        if state not in {"inactive", "failed", "unknown"}:
            raise InstallError("INSTALL_PAPER_UNIT_ACTIVE")
        unit_states[unit] = state
    blocking_unit_states: dict[str, str] = {}
    for unit in INSTALLATION_BLOCKING_UNITS:
        result = command(["/usr/bin/systemctl", "is-active", unit])
        state = result.stdout.strip()
        if state not in {"inactive", "failed", "unknown"}:
            raise InstallError("INSTALL_SHADOW_AUTHORITY_ACTIVE")
        blocking_unit_states[unit] = state
    policy_root = Path("/etc/heptatrader/paper-campaigns")
    if policy_root.exists() and any(path.is_file() for path in policy_root.rglob("*")):
        raise InstallError("INSTALL_CAMPAIGN_POLICY_ACTIVE")
    marker = Path(f"/run/hepta/ib-paper-control-{domain}/kill-switch")
    metadata = marker.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or
            stat.S_IMODE(metadata.st_mode) != 0o440 or metadata.st_nlink != 1):
        raise InstallError("INSTALL_KILL_SWITCH_INVALID")
    if marker.read_text(encoding="ascii", errors="strict").strip() != "engaged":
        raise InstallError("INSTALL_KILL_SWITCH_NOT_ENGAGED")
    egress = command(["/usr/libexec/hepta-broker-egress-policy", "--check-deny-all"])
    if egress.returncode != 0:
        raise InstallError("INSTALL_BROKER_EGRESS_NOT_DENY_ALL")
    return {
        "domain": domain,
        "paper_units": unit_states,
        "installation_blocking_units": blocking_unit_states,
        "campaign_policy_count": 0,
        "kill_switch_engaged": True,
        "broker_egress_deny_all": True,
    }


def validate_preflight_evidence(
        value: Any, domain: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
            "domain", "paper_units", "installation_blocking_units",
            "campaign_policy_count",
            "kill_switch_engaged", "broker_egress_deny_all"}:
        raise InstallError("INSTALL_PREFLIGHT_EVIDENCE_INVALID")
    unit_states = value.get("paper_units")
    blocking_unit_states = value.get("installation_blocking_units")
    if (
            value.get("domain") != domain or
            not isinstance(unit_states, dict) or
            set(unit_states) != set(PAPER_UNITS) or
            any(type(state) is not str or state not in {
                "inactive", "failed", "unknown"}
                for state in unit_states.values()) or
            not isinstance(blocking_unit_states, dict) or
            set(blocking_unit_states) != set(INSTALLATION_BLOCKING_UNITS) or
            any(type(state) is not str or state not in {
                "inactive", "failed", "unknown"}
                for state in blocking_unit_states.values()) or
            type(value.get("campaign_policy_count")) is not int or
            value["campaign_policy_count"] != 0 or
            value.get("kill_switch_engaged") is not True or
            value.get("broker_egress_deny_all") is not True):
        raise InstallError("INSTALL_PREFLIGHT_EVIDENCE_INVALID")
    return value


def validate_transaction_lock_evidence(
    value: Any,
    expected_path: Path = TRANSACTION_LOCK_PATH,
) -> dict[str, Any]:
    expected = {
        "path", "device", "inode", "nlink", "uid", "gid", "mode", "size",
        "mtime_ns", "ctime_ns", "created_during_transaction", "persistent",
        "held_during_transaction"}
    if not isinstance(value, dict) or set(value) != expected:
        raise InstallError("INSTALL_TRANSACTION_LOCK_EVIDENCE_INVALID")
    integer_fields = ("device", "inode", "nlink", "uid", "gid", "size",
                      "mtime_ns", "ctime_ns")
    if (
            value.get("path") != str(expected_path) or
            any(type(value.get(field)) is not int for field in integer_fields) or
            value["device"] < 0 or value["inode"] <= 0 or
            value["nlink"] != 1 or value["uid"] != 0 or value["gid"] != 0 or
            value.get("mode") != "0600" or value["size"] != 0 or
            value["mtime_ns"] < 0 or value["ctime_ns"] < 0 or
            type(value.get("created_during_transaction")) is not bool or
            value.get("persistent") is not True or
            value.get("held_during_transaction") is not True):
        raise InstallError("INSTALL_TRANSACTION_LOCK_EVIDENCE_INVALID")
    return value


def validate_receipt_reader_gid(value: int) -> int:
    if type(value) is not int or value != RECEIPT_READER_GID:
        raise InstallError("INSTALL_RECEIPT_READER_GID_INVALID")
    return value


def receipt_reader_gid_argument(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "receipt reader gid must be 1000") from error
    try:
        return validate_receipt_reader_gid(parsed)
    except InstallError as error:
        raise argparse.ArgumentTypeError(
            "receipt reader gid must be 1000") from error


def install_generation_argument(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "current install generation must be a non-negative integer") \
            from error
    if parsed < 0 or parsed > MAX_INSTALL_GENERATION:
        raise argparse.ArgumentTypeError(
            "current install generation must be a non-negative integer")
    return parsed


def atomic_write(
    path: Path,
    payload: bytes,
    mode: int,
    *,
    owner_uid: int = 0,
    owner_gid: int = 0,
) -> None:
    absolute = Path(os.path.abspath(path))
    parent = _open_anchored_directory(
        absolute.parent, create=True, owner_uid=owner_uid,
        owner_gid=owner_gid, strict_ancestors=False)
    try:
        expected = _stat_optional(parent, absolute.name)
        expected_payload: bytes | None = None
        if expected is not None and (
                not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1):
            raise InstallError("INSTALL_DESTINATION_TYPE_INVALID")
        if expected is not None:
            expected_payload, expected = _read_at(parent, absolute.name)
        _atomic_write_at(
            absolute.parent, parent, absolute.name, payload, mode,
            owner_uid=owner_uid, owner_gid=owner_gid,
            expected=expected, expected_payload=expected_payload,
            reason="INSTALL_ATOMIC_WRITE_FAILED")
    finally:
        os.close(parent)


DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) |
    getattr(os, "O_NOFOLLOW", 0))
READ_FLAGS = (
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
    getattr(os, "O_NOFOLLOW", 0))
CREATE_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) |
    getattr(os, "O_NOFOLLOW", 0))
LOCK_OPEN_FLAGS = (
    os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
LOCK_CREATE_FLAGS = LOCK_OPEN_FLAGS | os.O_CREAT | os.O_EXCL


def _validate_lock_metadata(
        metadata: os.stat_result, owner_uid: int, owner_gid: int) -> None:
    if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != owner_uid or metadata.st_gid != owner_gid or
            stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size != 0):
        raise InstallError("INSTALL_TRANSACTION_LOCK_INVALID")


def _validate_transaction_lock(
    lock: TransactionLock,
    *, owner_uid: int | None = None, owner_gid: int | None = None,
    strict_ancestors: bool | None = None,
) -> None:
    (parent_path, parent, descriptor, name, identity, _created,
     locked_uid, locked_gid, locked_strict) = lock
    if owner_uid is None:
        owner_uid = locked_uid
    if owner_gid is None:
        owner_gid = locked_gid
    if strict_ancestors is None:
        strict_ancestors = locked_strict
    if (
            owner_uid != locked_uid or owner_gid != locked_gid or
            strict_ancestors != locked_strict):
        raise InstallError("INSTALL_TRANSACTION_LOCK_INVALID")
    opened = os.fstat(descriptor)
    try:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        raise InstallError("INSTALL_TRANSACTION_LOCK_REBOUND") from error
    _validate_lock_metadata(opened, owner_uid, owner_gid)
    _validate_lock_metadata(named, owner_uid, owner_gid)
    if (
            _stable_identity(opened) != identity or
            _stable_identity(named) != identity):
        raise InstallError("INSTALL_TRANSACTION_LOCK_REBOUND")
    _rebind_directory(
        parent_path, parent, owner_uid=owner_uid, owner_gid=owner_gid,
        strict_ancestors=strict_ancestors)


def _acquire_transaction_lock(
    path: Path = TRANSACTION_LOCK_PATH,
    *, owner_uid: int = 0, owner_gid: int = 0,
    strict_ancestors: bool = True,
) -> TransactionLock:
    if not path.is_absolute():
        raise InstallError("INSTALL_TRANSACTION_LOCK_PATH_INVALID")
    absolute = Path(os.path.normpath(os.path.abspath(os.fspath(path))))
    parent = _open_anchored_directory(
        absolute.parent, create=False, owner_uid=owner_uid,
        owner_gid=owner_gid, strict_ancestors=strict_ancestors)
    descriptor = -1
    created = False
    try:
        before = _stat_optional(parent, absolute.name)
        if before is None:
            try:
                descriptor = os.open(
                    absolute.name, LOCK_CREATE_FLAGS, 0o600, dir_fd=parent)
                created = True
            except FileExistsError:
                descriptor = -1
                before = os.stat(
                    absolute.name, dir_fd=parent, follow_symlinks=False)
        if descriptor < 0:
            try:
                descriptor = os.open(
                    absolute.name, LOCK_OPEN_FLAGS, dir_fd=parent)
            except OSError as error:
                raise InstallError("INSTALL_TRANSACTION_LOCK_INVALID") from error
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallError("INSTALL_TRANSACTION_BUSY") from error
        except OSError as error:
            raise InstallError("INSTALL_TRANSACTION_LOCK_FAILED") from error
        if created:
            opened = os.fstat(descriptor)
            if opened.st_uid != owner_uid or opened.st_gid != owner_gid:
                os.fchown(descriptor, owner_uid, owner_gid)
            if stat.S_IMODE(opened.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            os.fsync(parent)
            before = os.fstat(descriptor)
        if before is None:
            raise InstallError("INSTALL_TRANSACTION_LOCK_INVALID")
        opened = os.fstat(descriptor)
        final = os.stat(
            absolute.name, dir_fd=parent, follow_symlinks=False)
        for metadata in (before, opened, final):
            _validate_lock_metadata(metadata, owner_uid, owner_gid)
        if not (
                _stable_identity(before) == _stable_identity(opened) ==
                _stable_identity(final)):
            raise InstallError("INSTALL_TRANSACTION_LOCK_REBOUND")
        lock = (
            absolute.parent, parent, descriptor, absolute.name,
            _stable_identity(opened), created, owner_uid, owner_gid,
            strict_ancestors)
        _validate_transaction_lock(
            lock, owner_uid=owner_uid, owner_gid=owner_gid,
            strict_ancestors=strict_ancestors)
        return lock
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)
        raise


def _acquire_existing_transaction_lock(
    path: Path = TRANSACTION_LOCK_PATH,
    *,
    owner_uid: int = 0,
    owner_gid: int = 0,
    strict_ancestors: bool = True,
) -> TransactionLock:
    """Acquire the persistent installer lock without ever creating it."""

    if not path.is_absolute():
        raise InstallError("INSTALL_CONSUMER_LOCK_PATH_INVALID")
    absolute = Path(os.path.normpath(os.path.abspath(os.fspath(path))))
    parent = _open_anchored_directory(
        absolute.parent, create=False, owner_uid=owner_uid,
        owner_gid=owner_gid, strict_ancestors=strict_ancestors)
    descriptor = -1
    try:
        before = _stat_optional(parent, absolute.name)
        if before is None:
            raise InstallError("INSTALL_CONSUMER_LOCK_MISSING")
        _validate_lock_metadata(before, owner_uid, owner_gid)
        try:
            descriptor = os.open(absolute.name, READ_FLAGS, dir_fd=parent)
        except OSError as error:
            raise InstallError("INSTALL_CONSUMER_LOCK_INVALID") from error
        opened = os.fstat(descriptor)
        _validate_lock_metadata(opened, owner_uid, owner_gid)
        if _stable_identity(before) != _stable_identity(opened):
            raise InstallError("INSTALL_CONSUMER_LOCK_REBOUND")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallError("INSTALL_CONSUMER_LOCK_BUSY") from error
        except OSError as error:
            raise InstallError("INSTALL_CONSUMER_LOCK_FAILED") from error
        final_opened = os.fstat(descriptor)
        final_named = os.stat(
            absolute.name, dir_fd=parent, follow_symlinks=False)
        for metadata in (final_opened, final_named):
            _validate_lock_metadata(metadata, owner_uid, owner_gid)
        if not (
                _stable_identity(opened) == _stable_identity(final_opened) ==
                _stable_identity(final_named)):
            raise InstallError("INSTALL_CONSUMER_LOCK_REBOUND")
        lock = (
            absolute.parent, parent, descriptor, absolute.name,
            _stable_identity(final_opened), False, owner_uid, owner_gid,
            strict_ancestors)
        _validate_transaction_lock(lock)
        return lock
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)
        raise


def _release_transaction_lock(
        lock: TransactionLock) -> None:
    (_parent_path, parent, descriptor, _name, _identity, _created,
     _owner_uid, _owner_gid, _strict) = lock
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
        os.close(parent)


def _transaction_lock_evidence(
        lock: TransactionLock
) -> dict[str, Any]:
    _validate_transaction_lock(lock)
    (parent_path, _parent, descriptor, name, _identity, created,
     _owner_uid, _owner_gid, _strict) = lock
    metadata = os.fstat(descriptor)
    return {
        "path": str(parent_path / name),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "nlink": metadata.st_nlink,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
        "created_during_transaction": created,
        "persistent": True,
        "held_during_transaction": True,
    }


def _validate_optional_transaction_lock(
        lock: TransactionLock | None
) -> None:
    if lock is not None:
        try:
            _validate_transaction_lock(lock)
        except TransactionLockCompromisedError:
            raise
        except (InstallError, OSError) as error:
            raise TransactionLockCompromisedError(
                "INSTALL_TRANSACTION_LOCK_COMPROMISED") from error


def _stable_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_uid, metadata.st_gid)


def _absolute_parts(path: Path) -> tuple[str, ...]:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute() or any(
            part in {"", ".", ".."} or "/" in part
            for part in absolute.parts[1:]):
        raise InstallError("INSTALL_ANCHORED_PATH_INVALID")
    return absolute.parts[1:]


def _validate_directory(
        metadata: os.stat_result, *, strict_ancestors: bool) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise InstallError("INSTALL_ANCHORED_DIRECTORY_INVALID")
    if strict_ancestors and (
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            stat.S_IMODE(metadata.st_mode) & 0o022):
        raise InstallError("INSTALL_ANCHORED_DIRECTORY_INVALID")


def _open_anchored_directory(
    path: Path,
    *,
    create: bool,
    owner_uid: int,
    owner_gid: int,
    strict_ancestors: bool,
    leaf_mode: int | None = None,
    transaction_lock: TransactionLock | None = None,
) -> int:
    absolute = Path(os.path.abspath(path))
    parts = _absolute_parts(absolute)
    try:
        descriptor = os.open("/", DIRECTORY_FLAGS)
    except OSError as error:
        raise InstallError("INSTALL_ANCHOR_ROOT_INVALID") from error
    try:
        _validate_directory(
            os.fstat(descriptor), strict_ancestors=strict_ancestors)
        for index, component in enumerate(parts):
            created = False
            try:
                before = os.stat(
                    component, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise InstallError(
                        "INSTALL_ANCHORED_DIRECTORY_MISSING") from None
                mode = leaf_mode if index == len(parts) - 1 and leaf_mode else 0o755
                try:
                    _validate_optional_transaction_lock(transaction_lock)
                    os.mkdir(component, mode, dir_fd=descriptor)
                    _validate_optional_transaction_lock(transaction_lock)
                except OSError as error:
                    raise InstallError(
                        "INSTALL_ANCHORED_DIRECTORY_CREATE_FAILED") from error
                before = os.stat(
                    component, dir_fd=descriptor, follow_symlinks=False)
                created = True
            try:
                child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
                opened = os.fstat(child)
                final = os.stat(
                    component, dir_fd=descriptor, follow_symlinks=False)
            except OSError as error:
                raise InstallError(
                    "INSTALL_ANCHORED_DIRECTORY_INVALID") from error
            try:
                if created:
                    intended_mode = (
                        leaf_mode
                        if index == len(parts) - 1 and leaf_mode else 0o755)
                    if opened.st_uid != owner_uid or opened.st_gid != owner_gid:
                        _validate_optional_transaction_lock(transaction_lock)
                        os.fchown(child, owner_uid, owner_gid)
                        _validate_optional_transaction_lock(transaction_lock)
                    if stat.S_IMODE(opened.st_mode) != intended_mode:
                        _validate_optional_transaction_lock(transaction_lock)
                        os.fchmod(child, intended_mode)
                        _validate_optional_transaction_lock(transaction_lock)
                    _validate_optional_transaction_lock(transaction_lock)
                    os.fsync(child)
                    _validate_optional_transaction_lock(transaction_lock)
                    os.fsync(descriptor)
                    _validate_optional_transaction_lock(transaction_lock)
                    opened = os.fstat(child)
                    final = os.stat(
                        component, dir_fd=descriptor,
                        follow_symlinks=False)
                _validate_directory(
                    opened, strict_ancestors=strict_ancestors)
                # A directory's link count, size and timestamps legitimately
                # change when unrelated children are created or removed.  The
                # anchored descriptor is still the same object when its stable
                # device/inode/mode/owner identity is unchanged.
                if _directory_identity(opened) != _directory_identity(final):
                    raise InstallError(
                        "INSTALL_ANCHORED_DIRECTORY_REBOUND")
                if not created and (
                        _directory_identity(before) !=
                        _directory_identity(opened)):
                    raise InstallError(
                        "INSTALL_ANCHORED_DIRECTORY_REBOUND")
                if (
                        index == len(parts) - 1 and leaf_mode is not None and
                        (opened.st_uid != owner_uid or
                         opened.st_gid != owner_gid or
                         stat.S_IMODE(opened.st_mode) != leaf_mode)):
                    raise InstallError(
                        "INSTALL_ANCHORED_DIRECTORY_INVALID")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _rebind_directory(
    path: Path,
    descriptor: int,
    *,
    owner_uid: int,
    owner_gid: int,
    strict_ancestors: bool,
    leaf_mode: int | None = None,
) -> None:
    current = os.fstat(descriptor)
    rebound = _open_anchored_directory(
        path, create=False, owner_uid=owner_uid, owner_gid=owner_gid,
        strict_ancestors=strict_ancestors, leaf_mode=leaf_mode)
    try:
        if _directory_identity(current) != _directory_identity(
                os.fstat(rebound)):
            raise InstallError("INSTALL_ANCHORED_DIRECTORY_REBOUND")
    finally:
        os.close(rebound)


def _stat_optional(parent: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise InstallError("INSTALL_DESTINATION_STAT_FAILED") from error


def _read_at(
    parent: int,
    name: str,
    *,
    maximum: int = MAX_ARCHIVE_BYTES,
) -> tuple[bytes, os.stat_result]:
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(name, READ_FLAGS, dir_fd=parent)
    except OSError as error:
        raise InstallError("INSTALL_ANCHORED_FILE_INVALID") from error
    try:
        opened = os.fstat(descriptor)
        if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_size < 0 or opened.st_size > maximum or
                _stable_identity(before) != _stable_identity(opened)):
            raise InstallError("INSTALL_ANCHORED_FILE_INVALID")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                raise InstallError("INSTALL_ANCHORED_FILE_INVALID")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise InstallError("INSTALL_ANCHORED_FILE_INVALID")
        after = os.fstat(descriptor)
        final = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not (
                _stable_identity(opened) == _stable_identity(after) ==
                _stable_identity(final)):
            raise InstallError("INSTALL_ANCHORED_FILE_REBOUND")
        return b"".join(chunks), opened
    finally:
        os.close(descriptor)


RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2
_LIBC = ctypes.CDLL(None, use_errno=True)


def _renameat2(
    old_parent: int, old_name: str,
    new_parent: int, new_name: str, flags: int,
) -> None:
    function = getattr(_LIBC, "renameat2", None)
    if function is None:
        raise InstallError("INSTALL_RENAMEAT2_UNAVAILABLE")
    function.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint)
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = function(
        old_parent, os.fsencode(old_name),
        new_parent, os.fsencode(new_name), flags)
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _rename_exchange(parent: int, left: str, right: str) -> None:
    _renameat2(parent, left, parent, right, RENAME_EXCHANGE)


def _rename_noreplace(
    old_parent: int, old_name: str, new_parent: int, new_name: str,
) -> None:
    _renameat2(
        old_parent, old_name, new_parent, new_name, RENAME_NOREPLACE)


def _exact_file(
        payload: bytes, metadata: os.stat_result,
        expected_payload: bytes, expected_metadata: os.stat_result) -> bool:
    return (
        payload == expected_payload and
        (
            metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_nlink, metadata.st_uid, metadata.st_gid,
            metadata.st_size, metadata.st_mtime_ns
        ) == (
            expected_metadata.st_dev, expected_metadata.st_ino,
            expected_metadata.st_mode, expected_metadata.st_nlink,
            expected_metadata.st_uid, expected_metadata.st_gid,
            expected_metadata.st_size, expected_metadata.st_mtime_ns
        ) and
        metadata.st_ctime_ns >= expected_metadata.st_ctime_ns
    )


def _remove_quarantine_directory(
        parent: int, quarantine_name: str, quarantine: int,
        transaction_lock: TransactionLock | None = None) -> None:
    _validate_optional_transaction_lock(transaction_lock)
    try:
        entries = os.listdir(quarantine)
    except OSError as error:
        raise InstallError("INSTALL_QUARANTINE_VERIFY_FAILED") from error
    if entries:
        return
    opened = os.fstat(quarantine)
    try:
        named = os.stat(
            quarantine_name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        raise InstallError("INSTALL_QUARANTINE_VERIFY_FAILED") from error
    if _directory_identity(opened) != _directory_identity(named):
        raise InstallError("INSTALL_QUARANTINE_REBOUND")
    _validate_optional_transaction_lock(transaction_lock)
    try:
        os.rmdir(quarantine_name, dir_fd=parent)
        os.fsync(parent)
    except OSError as error:
        raise InstallError("INSTALL_QUARANTINE_CLEANUP_FAILED") from error
    _validate_optional_transaction_lock(transaction_lock)


def _unlink_exact_at(
    parent_path: Path, parent: int, name: str,
    expected_payload: bytes, expected_metadata: os.stat_result,
    *, owner_uid: int, owner_gid: int, strict_ancestors: bool, reason: str,
    transaction_lock: TransactionLock | None = None,
    retain_in_quarantine: bool = False,
) -> None:
    if type(retain_in_quarantine) is not bool:
        raise InstallError("INSTALL_QUARANTINE_POLICY_INVALID")
    _validate_optional_transaction_lock(transaction_lock)
    observed, metadata = _read_at(
        parent, name, maximum=len(expected_payload))
    if not _exact_file(
            observed, metadata, expected_payload, expected_metadata):
        raise InstallError(reason)
    _rebind_directory(
        parent_path, parent, owner_uid=owner_uid, owner_gid=owner_gid,
        strict_ancestors=strict_ancestors)
    quarantine_name = f".{name}.hepta-quarantine-{secrets.token_hex(12)}"
    _validate_optional_transaction_lock(transaction_lock)
    try:
        os.mkdir(quarantine_name, 0o700, dir_fd=parent)
        quarantine = os.open(
            quarantine_name, DIRECTORY_FLAGS, dir_fd=parent)
    except OSError as error:
        raise InstallError("INSTALL_QUARANTINE_CREATE_FAILED") from error
    moved = False
    lock_compromised = False
    cleanup_error: Exception | None = None
    try:
        os.fchown(quarantine, owner_uid, owner_gid)
        os.fchmod(quarantine, 0o700)
        os.fsync(quarantine)
        os.fsync(parent)
        quarantine_opened = os.fstat(quarantine)
        quarantine_named = os.stat(
            quarantine_name, dir_fd=parent, follow_symlinks=False)
        if (
                not stat.S_ISDIR(quarantine_opened.st_mode) or
                quarantine_opened.st_uid != owner_uid or
                quarantine_opened.st_gid != owner_gid or
                stat.S_IMODE(quarantine_opened.st_mode) != 0o700 or
                _stable_identity(quarantine_opened) !=
                _stable_identity(quarantine_named)):
            raise InstallError("INSTALL_QUARANTINE_REBOUND")
        _validate_optional_transaction_lock(transaction_lock)
        _rename_noreplace(parent, name, quarantine, "payload")
        moved = True
        _validate_optional_transaction_lock(transaction_lock)
        os.fsync(parent)
        os.fsync(quarantine)
        try:
            moved_payload, moved_metadata = _read_at(
                quarantine, "payload", maximum=len(expected_payload))
            if not _exact_file(
                    moved_payload, moved_metadata,
                    expected_payload, expected_metadata):
                raise InstallError(reason)
            _validate_optional_transaction_lock(transaction_lock)
            if not retain_in_quarantine:
                os.unlink("payload", dir_fd=quarantine)
                moved = False
                _validate_optional_transaction_lock(transaction_lock)
            os.fsync(quarantine)
            os.fsync(parent)
        except TransactionLockCompromisedError:
            lock_compromised = True
            raise
        except (InstallError, OSError) as error:
            if moved and not retain_in_quarantine:
                try:
                    _validate_optional_transaction_lock(transaction_lock)
                    _rename_noreplace(quarantine, "payload", parent, name)
                    moved = False
                    _validate_optional_transaction_lock(transaction_lock)
                    os.fsync(quarantine)
                    os.fsync(parent)
                    restored_payload, restored_metadata = _read_at(
                        parent, name, maximum=MAX_ARCHIVE_BYTES)
                    if (
                            "moved_payload" in locals() and
                            "moved_metadata" in locals() and
                            not _exact_file(
                                restored_payload, restored_metadata,
                                moved_payload, moved_metadata)):
                        raise InstallError(
                            "INSTALL_CONDITIONAL_UNLINK_RESTORE_FAILED")
                    _rebind_directory(
                        parent_path, parent, owner_uid=owner_uid,
                        owner_gid=owner_gid,
                        strict_ancestors=strict_ancestors)
                    _validate_optional_transaction_lock(transaction_lock)
                except TransactionLockCompromisedError:
                    lock_compromised = True
                    raise
                except (InstallError, OSError) as restore_error:
                    raise InstallError(
                        "INSTALL_CONDITIONAL_UNLINK_RESTORE_FAILED") from (
                            restore_error)
            raise InstallError(reason) from error
    except TransactionLockCompromisedError:
        lock_compromised = True
        raise
    except (InstallError, OSError):
        raise
    finally:
        if not moved and not lock_compromised:
            try:
                _remove_quarantine_directory(
                    parent, quarantine_name, quarantine, transaction_lock)
            except TransactionLockCompromisedError:
                lock_compromised = True
                cleanup_error = TransactionLockCompromisedError(
                    "INSTALL_TRANSACTION_LOCK_COMPROMISED")
            except Exception as error:
                cleanup_error = error
        os.close(quarantine)
        if cleanup_error is not None:
            raise cleanup_error
    _rebind_directory(
        parent_path, parent, owner_uid=owner_uid, owner_gid=owner_gid,
        strict_ancestors=strict_ancestors)
    if _stat_optional(parent, name) is not None:
        raise InstallError(reason)
    _validate_optional_transaction_lock(transaction_lock)


def _restore_exchange(
    parent_path: Path, parent: int, name: str, temporary_name: str,
    candidate_payload: bytes, candidate_metadata: os.stat_result,
    displaced_payload: bytes, displaced_metadata: os.stat_result,
    *, owner_uid: int, owner_gid: int, strict_ancestors: bool,
    transaction_lock: TransactionLock | None = None,
) -> None:
    _validate_optional_transaction_lock(transaction_lock)
    installed_payload, installed_metadata = _read_at(
        parent, name, maximum=len(candidate_payload))
    temporary_payload, temporary_metadata = _read_at(
        parent, temporary_name, maximum=len(displaced_payload))
    if (
            not _exact_file(
                installed_payload, installed_metadata,
                candidate_payload, candidate_metadata) or
            not _exact_file(
                temporary_payload, temporary_metadata,
                displaced_payload, displaced_metadata)):
        raise InstallError("INSTALL_DESTINATION_CAS_ROLLBACK_FAILED")
    _validate_optional_transaction_lock(transaction_lock)
    try:
        _rename_exchange(parent, temporary_name, name)
        os.fsync(parent)
    except TransactionLockCompromisedError:
        raise
    except (InstallError, OSError) as error:
        raise InstallError("INSTALL_DESTINATION_CAS_ROLLBACK_FAILED") from error
    _validate_optional_transaction_lock(transaction_lock)
    _rebind_directory(
        parent_path, parent, owner_uid=owner_uid, owner_gid=owner_gid,
        strict_ancestors=strict_ancestors)
    restored_payload, restored_metadata = _read_at(
        parent, name, maximum=len(displaced_payload))
    candidate_after, candidate_after_metadata = _read_at(
        parent, temporary_name, maximum=len(candidate_payload))
    if (
            not _exact_file(
                restored_payload, restored_metadata,
                displaced_payload, displaced_metadata) or
            not _exact_file(
                candidate_after, candidate_after_metadata,
                candidate_payload, candidate_metadata)):
        raise InstallError("INSTALL_DESTINATION_CAS_ROLLBACK_FAILED")
    _validate_optional_transaction_lock(transaction_lock)


def _atomic_write_at(
    parent_path: Path,
    parent: int,
    name: str,
    payload: bytes,
    mode: int,
    *,
    owner_uid: int,
    owner_gid: int,
    expected: os.stat_result | None,
    expected_payload: bytes | None,
    reason: str,
    strict_ancestors: bool = False,
    transaction_lock: TransactionLock | None = None,
) -> os.stat_result:
    temporary_name = f".{name}.hepta-{secrets.token_hex(12)}.tmp"
    descriptor = -1
    temporary_metadata: os.stat_result | None = None
    candidate_at_temporary = False
    exchange_active = False
    displaced_payload: bytes | None = None
    displaced_metadata: os.stat_result | None = None
    try:
        _validate_optional_transaction_lock(transaction_lock)
        descriptor = os.open(
            temporary_name, CREATE_FLAGS, 0o600, dir_fd=parent)
        os.fchown(descriptor, owner_uid, owner_gid)
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        temporary = os.fstat(descriptor)
        temporary_metadata = temporary
        candidate_at_temporary = True
        entry = os.stat(
            temporary_name, dir_fd=parent, follow_symlinks=False)
        if (
                not stat.S_ISREG(temporary.st_mode) or temporary.st_nlink != 1 or
                temporary.st_uid != owner_uid or temporary.st_gid != owner_gid or
                stat.S_IMODE(temporary.st_mode) != mode or
                temporary.st_size != len(payload) or
                _stable_identity(temporary) != _stable_identity(entry)):
            raise InstallError(reason)
        _validate_optional_transaction_lock(transaction_lock)
        _rebind_directory(
            parent_path, parent, owner_uid=owner_uid, owner_gid=owner_gid,
            strict_ancestors=strict_ancestors)
        current = _stat_optional(parent, name)
        if expected is None:
            if current is not None or expected_payload is not None:
                raise InstallError("INSTALL_DESTINATION_REBOUND")
        else:
            if expected_payload is None or current is None:
                raise InstallError("INSTALL_DESTINATION_REBOUND")
            current_payload, current_metadata = _read_at(
                parent, name, maximum=len(expected_payload))
            if not _exact_file(
                    current_payload, current_metadata,
                    expected_payload, expected):
                raise InstallError("INSTALL_DESTINATION_REBOUND")
        if expected is None:
            try:
                _validate_optional_transaction_lock(transaction_lock)
                _rename_noreplace(parent, temporary_name, parent, name)
                candidate_at_temporary = False
                _validate_optional_transaction_lock(transaction_lock)
            except (InstallError, OSError) as error:
                raise InstallError("INSTALL_DESTINATION_REBOUND") from error
        else:
            try:
                _validate_optional_transaction_lock(transaction_lock)
                _rename_exchange(parent, temporary_name, name)
            except (InstallError, OSError) as error:
                raise InstallError(reason) from error
            exchange_active = True
            candidate_at_temporary = False
            _validate_optional_transaction_lock(transaction_lock)
            installed_payload, installed_metadata = _read_at(
                parent, name, maximum=len(payload))
            displaced_payload, displaced_metadata = _read_at(
                parent, temporary_name, maximum=len(expected_payload))
            if (
                    not _exact_file(
                        installed_payload, installed_metadata,
                        payload, temporary) or
                    not _exact_file(
                        displaced_payload, displaced_metadata,
                        expected_payload, expected)):
                _validate_optional_transaction_lock(transaction_lock)
                _restore_exchange(
                    parent_path, parent, name, temporary_name,
                    installed_payload, installed_metadata,
                    displaced_payload, displaced_metadata,
                    owner_uid=owner_uid, owner_gid=owner_gid,
                    strict_ancestors=strict_ancestors,
                    transaction_lock=transaction_lock)
                exchange_active = False
                candidate_at_temporary = True
                _validate_optional_transaction_lock(transaction_lock)
                raise InstallError("INSTALL_DESTINATION_REBOUND")
        os.fsync(parent)
        _rebind_directory(
            parent_path, parent, owner_uid=owner_uid, owner_gid=owner_gid,
            strict_ancestors=strict_ancestors)
        observed, final = _read_at(parent, name, maximum=len(payload))
        if (
                observed != payload or final.st_uid != owner_uid or
                final.st_gid != owner_gid or
                stat.S_IMODE(final.st_mode) != mode):
            raise InstallError(reason)
        if exchange_active:
            assert displaced_payload is not None
            assert displaced_metadata is not None
            _unlink_exact_at(
                parent_path, parent, temporary_name,
                displaced_payload, displaced_metadata,
                owner_uid=owner_uid, owner_gid=owner_gid,
                strict_ancestors=strict_ancestors,
                reason="INSTALL_DISPLACED_TARGET_REBOUND",
                transaction_lock=transaction_lock)
            exchange_active = False
        observed, final = _read_at(parent, name, maximum=len(payload))
        if (
                observed != payload or final.st_uid != owner_uid or
                final.st_gid != owner_gid or
                stat.S_IMODE(final.st_mode) != mode or final.st_nlink != 1):
            raise InstallError(reason)
        return final
    except (InstallError, OSError) as error:
        # A detected lock rebound is terminal.  Do not restore an exchanged
        # target or remove any transaction residue after this point; a fresh
        # holder will reconcile it in a new transaction.
        _validate_optional_transaction_lock(transaction_lock)
        if exchange_active:
            try:
                if displaced_payload is None or displaced_metadata is None:
                    installed_payload, installed_metadata = _read_at(
                        parent, name, maximum=len(payload))
                    displaced_payload, displaced_metadata = _read_at(
                        parent, temporary_name, maximum=MAX_ARCHIVE_BYTES)
                    restore_candidate_payload = installed_payload
                    restore_candidate_metadata = installed_metadata
                else:
                    restore_candidate_payload = payload
                    restore_candidate_metadata = temporary_metadata
                _restore_exchange(
                    parent_path, parent, name, temporary_name,
                    restore_candidate_payload, restore_candidate_metadata,
                    displaced_payload, displaced_metadata,
                    owner_uid=owner_uid, owner_gid=owner_gid,
                    strict_ancestors=strict_ancestors,
                    transaction_lock=transaction_lock)
                exchange_active = False
                candidate_at_temporary = True
                _validate_optional_transaction_lock(transaction_lock)
            except TransactionLockCompromisedError:
                raise
            except Exception as rollback_error:
                raise InstallError(
                    "INSTALL_DESTINATION_CAS_ROLLBACK_FAILED") from rollback_error
        if candidate_at_temporary and temporary_metadata is not None:
            try:
                _unlink_exact_at(
                    parent_path, parent, temporary_name,
                    payload, temporary_metadata,
                    owner_uid=owner_uid, owner_gid=owner_gid,
                    strict_ancestors=strict_ancestors,
                    reason="INSTALL_TEMPORARY_REBOUND",
                    transaction_lock=transaction_lock)
                candidate_at_temporary = False
            except TransactionLockCompromisedError:
                raise
            except Exception as cleanup_error:
                raise InstallError(
                    "INSTALL_TEMPORARY_CLEANUP_FAILED") from cleanup_error
        if isinstance(error, InstallError):
            raise
        raise InstallError(reason) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def stable_verify_file(
    path: Path,
    expected_payload: bytes,
    expected_mode: int,
    *,
    expected_uid: int,
    expected_gid: int,
) -> str:
    absolute = Path(os.path.abspath(path))
    try:
        parent = _open_anchored_directory(
            absolute.parent, create=False, owner_uid=expected_uid,
            owner_gid=expected_gid, strict_ancestors=False)
    except InstallError as error:
        raise InstallError("INSTALL_RECEIPT_POST_VERIFY_FAILED") from error
    try:
        payload, metadata = _read_at(
            parent, absolute.name, maximum=len(expected_payload))
        _rebind_directory(
            absolute.parent, parent, owner_uid=expected_uid,
            owner_gid=expected_gid, strict_ancestors=False)
        expected_digest = digest_bytes(expected_payload)
        if (
                payload != expected_payload or metadata.st_uid != expected_uid or
                metadata.st_gid != expected_gid or
                stat.S_IMODE(metadata.st_mode) != expected_mode or
                digest_bytes(payload) != expected_digest):
            raise InstallError("INSTALL_RECEIPT_POST_VERIFY_FAILED")
        return expected_digest
    except InstallError as error:
        if str(error) == "INSTALL_RECEIPT_POST_VERIFY_FAILED":
            raise
        raise InstallError("INSTALL_RECEIPT_POST_VERIFY_FAILED") from error
    finally:
        os.close(parent)


def _read_consumer_document(
    path: Path,
    *,
    expected_mode: int,
    expected_uid: int,
    expected_gid: int,
    strict_ancestors: bool,
    transaction_lock: TransactionLock,
) -> tuple[bytes, os.stat_result]:
    absolute = Path(os.path.normpath(os.path.abspath(os.fspath(path))))
    if not absolute.is_absolute():
        raise InstallError("INSTALL_CONSUMER_DOCUMENT_PATH_INVALID")
    _validate_transaction_lock(transaction_lock)
    parent = _open_anchored_directory(
        absolute.parent, create=False, owner_uid=expected_uid,
        owner_gid=expected_gid, strict_ancestors=strict_ancestors)
    try:
        _validate_transaction_lock(transaction_lock)
        payload, metadata = _read_at(
            parent, absolute.name, maximum=MAX_CONSUMER_DOCUMENT_BYTES)
        _validate_transaction_lock(transaction_lock)
        _rebind_directory(
            absolute.parent, parent, owner_uid=expected_uid,
            owner_gid=expected_gid, strict_ancestors=strict_ancestors)
        _validate_transaction_lock(transaction_lock)
        if (
                metadata.st_uid != expected_uid or
                metadata.st_gid != expected_gid or
                stat.S_IMODE(metadata.st_mode) != expected_mode):
            raise InstallError("INSTALL_CONSUMER_DOCUMENT_METADATA_INVALID")
        _validate_transaction_lock(transaction_lock)
        return payload, metadata
    except InstallError:
        raise
    except OSError as error:
        raise InstallError("INSTALL_CONSUMER_DOCUMENT_INVALID") from error
    finally:
        os.close(parent)


def _current_lock_matches_receipt(
    lock: TransactionLock,
    evidence: dict[str, Any],
    *,
    lock_path: Path,
) -> None:
    _validate_transaction_lock(lock)
    metadata = os.fstat(lock[2])
    expected = {
        "path": str(lock_path),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "nlink": metadata.st_nlink,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }
    if any(evidence.get(field) != value for field, value in expected.items()):
        raise InstallError("INSTALL_CONSUMER_LOCK_RECEIPT_MISMATCH")


def validate_install_receipt_lineage(
    install_generation: Any,
    predecessor_install_generation: Any,
    predecessor_current_install_pointer_file_sha256: Any,
) -> None:
    """Validate one exact, gap-free predecessor edge sealed by a receipt."""

    if (
            type(install_generation) is not int or
            type(predecessor_install_generation) is not int or
            not 0 <= predecessor_install_generation < install_generation <=
                MAX_INSTALL_GENERATION or
            install_generation != predecessor_install_generation + 1 or
            type(predecessor_current_install_pointer_file_sha256) is not str or
            (predecessor_install_generation == 0) !=
                (predecessor_current_install_pointer_file_sha256 == "absent") or
            (predecessor_install_generation > 0 and
             SHA256_IDENTITY.fullmatch(
                 predecessor_current_install_pointer_file_sha256) is None)):
        raise InstallError("INSTALL_RECEIPT_LINEAGE_INVALID")


def validate_install_receipt_document(
    document: Any,
    manifest: dict[str, Any],
    *,
    expected_domain: str,
    expected_backup_root: Path,
    receipt_reader_gid: int,
    lock_path: Path,
    expected_file_count: int = EXPECTED_SHADOW_FILE_COUNT,
) -> dict[str, Any]:
    reason = "INSTALL_CONSUMER_RECEIPT_INVALID"
    if (
            not isinstance(document, dict) or
            set(document) != INSTALL_RECEIPT_FIELDS or
            document.get("schema") != RECEIPT_SCHEMA or
            document.get("version") != RECEIPT_VERSION or
            type(document.get("finished_at_ms")) is not int or
            document["finished_at_ms"] < 0 or
            document.get("domain") != expected_domain or
            document.get("backup_root") != str(expected_backup_root) or
            document.get("reader_gid") != receipt_reader_gid or
            document.get("status") != "PASSIVE_INSTALL_COMPLETE"):
        raise InstallError(reason)
    validate_receipt_reader_gid(receipt_reader_gid)
    validate_install_receipt_lineage(
        document.get("install_generation"),
        document.get("predecessor_install_generation"),
        document.get(
            "predecessor_current_install_pointer_file_sha256"))
    for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access", "services_started", "services_enabled",
            "preflight_continuity_claimed"):
        if document.get(field) is not False:
            raise InstallError(reason)
    for field in (
            "archive_sha256", "source_baseline_sha256", "installer_sha256",
            "installed_paths_sha256", "body_sha256"):
        value = document.get(field)
        if type(value) is not str or SHA256_IDENTITY.fullmatch(value) is None:
            raise InstallError(reason)
    if any(
            document[field] != manifest[field]
            for field in (
                "archive_sha256", "source_baseline_sha256",
                "installer_sha256")):
        raise InstallError("INSTALL_CONSUMER_LINEAGE_MISMATCH")
    paths = [record["path"] for record in manifest["files"]]
    installed_file_count = document.get("installed_file_count")
    replaced_file_count = document.get("replaced_file_count")
    new_file_count = document.get("new_file_count")
    if (
            type(expected_file_count) is not int or expected_file_count <= 0 or
            len(paths) != expected_file_count or
            type(installed_file_count) is not int or
            installed_file_count != len(paths) or
            type(replaced_file_count) is not int or replaced_file_count < 0 or
            type(new_file_count) is not int or new_file_count < 0 or
            replaced_file_count + new_file_count != len(paths) or
            document["installed_paths_sha256"] !=
                digest_bytes(canonical_bytes(paths))):
        raise InstallError(reason)
    identity = document.get("default_deny_identity_manifest")
    expected_identity = {
        "destination": "/" + PAPER_IDENTITY_MANIFEST.as_posix(),
        "archive_path": PAPER_IDENTITY_MANIFEST.as_posix(),
        "uid": 0,
        "gid": 0,
        "mode": "0600",
        "size": len(PAPER_IDENTITY_MANIFEST_BYTES),
        "sha256": PAPER_IDENTITY_MANIFEST_SHA256,
        "installed": True,
    }
    if (
            not isinstance(identity, dict) or
            set(identity) != set(expected_identity) | {
                "preexisting_backed_up", "new_file"} or
            any(identity.get(field) != value
                for field, value in expected_identity.items()) or
            type(identity.get("preexisting_backed_up")) is not bool or
            type(identity.get("new_file")) is not bool or
            identity["preexisting_backed_up"] == identity["new_file"]):
        raise InstallError(reason)
    validate_transaction_lock_evidence(
        document.get("transaction_lock"), lock_path)
    before = validate_preflight_evidence(
        document.get("preflight_before"), expected_domain)
    after = validate_preflight_evidence(
        document.get("preflight_after"), expected_domain)
    if before != after:
        raise InstallError(reason)
    body = dict(document)
    body_sha256 = body.pop("body_sha256")
    if body_sha256 != digest_bytes(canonical_bytes(body)):
        raise InstallError(reason)
    return document


def _canonical_absolute_document_path(value: Any, reason: str) -> Path:
    if type(value) is not str or not value.startswith("/"):
        raise InstallError(reason)
    normalized = Path(os.path.normpath(value))
    if not normalized.is_absolute() or str(normalized) != value:
        raise InstallError(reason)
    return normalized


def validate_current_install_pointer_document(
    document: Any,
    *,
    current_pointer_path: Path = CURRENT_INSTALL_POINTER_PATH,
    lock_path: Path = TRANSACTION_LOCK_PATH,
) -> dict[str, Any]:
    """Validate the fixed generation marker without trusting another file."""

    reason = "INSTALL_CURRENT_POINTER_INVALID"
    if (
            not isinstance(document, dict) or
            set(document) != CURRENT_INSTALL_POINTER_FIELDS or
            document.get("schema") != CURRENT_INSTALL_POINTER_SCHEMA or
            type(document.get("version")) is not int or
            document["version"] != CURRENT_INSTALL_POINTER_VERSION or
            type(document.get("generation")) is not int or
            not 0 < document["generation"] <= MAX_INSTALL_GENERATION or
            type(document.get("domain")) is not str or
            re.fullmatch(r"[a-z][a-z0-9-]{0,31}", document["domain"]) is None or
            type(document.get("installed_file_count")) is not int or
            document["installed_file_count"] <= 0 or
            document.get("transaction_lock_path") != str(lock_path)):
        raise InstallError(reason)
    manifest_path = _canonical_absolute_document_path(
        document.get("manifest_path"), reason)
    receipt_path = _canonical_absolute_document_path(
        document.get("receipt_path"), reason)
    backup_root = _canonical_absolute_document_path(
        document.get("backup_root"), reason)
    pointer = Path(os.path.normpath(os.path.abspath(
        os.fspath(current_pointer_path))))
    if (
            not pointer.is_absolute() or manifest_path == pointer or
            receipt_path == pointer or backup_root == pointer or
            pointer in backup_root.parents or backup_root in pointer.parents):
        raise InstallError(reason)
    for field in (
            "manifest_file_sha256", "receipt_file_sha256",
            "archive_sha256", "source_baseline_sha256", "installer_sha256",
            "installed_paths_sha256", "body_sha256"):
        value = document.get(field)
        if type(value) is not str or SHA256_IDENTITY.fullmatch(value) is None:
            raise InstallError(reason)
    for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access"):
        if document.get(field) is not False:
            raise InstallError(reason)
    body = dict(document)
    claimed = body.pop("body_sha256")
    if claimed != digest_bytes(canonical_bytes(body)):
        raise InstallError(reason)
    return document


def build_current_install_pointer(
    *,
    generation: int,
    domain: str,
    backup_root: Path,
    manifest_path: Path,
    manifest_payload: bytes,
    manifest: dict[str, Any],
    receipt_path: Path,
    receipt_payload: bytes,
    receipt: dict[str, Any],
    current_pointer_path: Path = CURRENT_INSTALL_POINTER_PATH,
    lock_path: Path = TRANSACTION_LOCK_PATH,
) -> dict[str, Any]:
    """Build the sole current-install generation marker."""

    manifest_absolute = Path(os.path.normpath(os.path.abspath(
        os.fspath(manifest_path))))
    receipt_absolute = Path(os.path.normpath(os.path.abspath(
        os.fspath(receipt_path))))
    backup_absolute = Path(os.path.normpath(os.path.abspath(
        os.fspath(backup_root))))
    body = {
        "schema": CURRENT_INSTALL_POINTER_SCHEMA,
        "version": CURRENT_INSTALL_POINTER_VERSION,
        "generation": generation,
        "domain": domain,
        "backup_root": str(backup_absolute),
        "manifest_path": str(manifest_absolute),
        "manifest_file_sha256": digest_bytes(manifest_payload),
        "receipt_path": str(receipt_absolute),
        "receipt_file_sha256": digest_bytes(receipt_payload),
        "archive_sha256": manifest["archive_sha256"],
        "source_baseline_sha256": manifest["source_baseline_sha256"],
        "installer_sha256": manifest["installer_sha256"],
        "installed_file_count": len(manifest["files"]),
        "installed_paths_sha256": receipt["installed_paths_sha256"],
        "transaction_lock_path": str(lock_path),
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    document = {**body, "body_sha256": digest_bytes(canonical_bytes(body))}
    return validate_current_install_pointer_document(
        document, current_pointer_path=current_pointer_path,
        lock_path=lock_path)


def _validate_current_install_pointer_binding(
    document: dict[str, Any],
    *,
    manifest_path: Path,
    manifest_payload: bytes,
    manifest: dict[str, Any],
    receipt_path: Path,
    receipt_payload: bytes,
    receipt: dict[str, Any],
    expected_domain: str,
    expected_backup_root: Path,
    current_pointer_path: Path,
    lock_path: Path,
) -> None:
    validate_current_install_pointer_document(
        document, current_pointer_path=current_pointer_path,
        lock_path=lock_path)
    expected = {
        "generation": receipt["install_generation"],
        "domain": expected_domain,
        "backup_root": str(Path(os.path.normpath(os.path.abspath(
            os.fspath(expected_backup_root))))),
        "manifest_path": str(Path(os.path.normpath(os.path.abspath(
            os.fspath(manifest_path))))),
        "manifest_file_sha256": digest_bytes(manifest_payload),
        "receipt_path": str(Path(os.path.normpath(os.path.abspath(
            os.fspath(receipt_path))))),
        "receipt_file_sha256": digest_bytes(receipt_payload),
        "archive_sha256": manifest["archive_sha256"],
        "source_baseline_sha256": manifest["source_baseline_sha256"],
        "installer_sha256": manifest["installer_sha256"],
        "installed_file_count": len(manifest["files"]),
        "installed_paths_sha256": receipt["installed_paths_sha256"],
        "transaction_lock_path": str(lock_path),
    }
    if any(document.get(field) != value for field, value in expected.items()):
        raise InstallError("INSTALL_CURRENT_POINTER_MISMATCH")


def _read_current_install_pointer_state(
    current_pointer_path: Path,
    transaction_lock: TransactionLock,
) -> CurrentInstallPointerState | None:
    """Read the optional fixed marker while preserving its exact inode."""

    absolute = Path(os.path.normpath(os.path.abspath(
        os.fspath(current_pointer_path))))
    _validate_transaction_lock(transaction_lock)
    ancestor = _open_anchored_directory(
        absolute.parent.parent, create=False, owner_uid=0, owner_gid=0,
        strict_ancestors=True, transaction_lock=transaction_lock)
    try:
        parent_entry = _stat_optional(ancestor, absolute.parent.name)
        _rebind_directory(
            absolute.parent.parent, ancestor, owner_uid=0, owner_gid=0,
            strict_ancestors=True)
        _validate_transaction_lock(transaction_lock)
        if parent_entry is None:
            return None
    finally:
        os.close(ancestor)
    parent = _open_anchored_directory(
        absolute.parent, create=False, owner_uid=0, owner_gid=0,
        strict_ancestors=True, transaction_lock=transaction_lock)
    try:
        metadata = _stat_optional(parent, absolute.name)
        if metadata is None:
            _rebind_directory(
                absolute.parent, parent, owner_uid=0, owner_gid=0,
                strict_ancestors=True)
            _validate_transaction_lock(transaction_lock)
            return None
        payload, metadata = _read_at(
            parent, absolute.name, maximum=MAX_CONSUMER_DOCUMENT_BYTES)
        _rebind_directory(
            absolute.parent, parent, owner_uid=0, owner_gid=0,
            strict_ancestors=True)
        _validate_transaction_lock(transaction_lock)
        if (
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise InstallError("INSTALL_CURRENT_POINTER_INVALID")
        document = validate_current_install_pointer_document(
            strict_json_bytes(payload, "INSTALL_CURRENT_POINTER_INVALID"),
            current_pointer_path=absolute,
            lock_path=Path(transaction_lock[0]) / transaction_lock[3])
        if payload != canonical_bytes(document):
            raise InstallError("INSTALL_CURRENT_POINTER_INVALID")
        return CurrentInstallPointerState(
            payload=payload, metadata=metadata, document=document)
    finally:
        os.close(parent)


def _publish_current_install_pointer(
    current_pointer_path: Path,
    document: dict[str, Any],
    previous: CurrentInstallPointerState | None,
    transaction_lock: TransactionLock,
) -> CurrentInstallPointerState:
    absolute = Path(os.path.normpath(os.path.abspath(
        os.fspath(current_pointer_path))))
    payload = canonical_bytes(document)
    parent = _open_anchored_directory(
        absolute.parent, create=True, owner_uid=0, owner_gid=0,
        strict_ancestors=True, transaction_lock=transaction_lock)
    try:
        current = _stat_optional(parent, absolute.name)
        if previous is None:
            if current is not None:
                raise InstallError("INSTALL_CURRENT_POINTER_REBOUND")
            expected_payload = None
        else:
            if current is None:
                raise InstallError("INSTALL_CURRENT_POINTER_REBOUND")
            current_payload, current = _read_at(
                parent, absolute.name, maximum=MAX_CONSUMER_DOCUMENT_BYTES)
            if (
                    current_payload != previous.payload or
                    _stable_identity(current) !=
                        _stable_identity(previous.metadata)):
                raise InstallError("INSTALL_CURRENT_POINTER_REBOUND")
            expected_payload = previous.payload
        metadata = _atomic_write_at(
            absolute.parent, parent, absolute.name, payload, 0o600,
            owner_uid=0, owner_gid=0, expected=current,
            expected_payload=expected_payload,
            reason="INSTALL_CURRENT_POINTER_PUBLISH_FAILED",
            strict_ancestors=True, transaction_lock=transaction_lock)
        observed, final = _read_at(
            parent, absolute.name, maximum=len(payload))
        _rebind_directory(
            absolute.parent, parent, owner_uid=0, owner_gid=0,
            strict_ancestors=True)
        _validate_transaction_lock(transaction_lock)
        if (
                observed != payload or _stable_identity(final) !=
                    _stable_identity(metadata) or
                final.st_uid != 0 or final.st_gid != 0 or
                stat.S_IMODE(final.st_mode) != 0o600):
            raise InstallError("INSTALL_CURRENT_POINTER_PUBLISH_FAILED")
        return CurrentInstallPointerState(
            payload=payload, metadata=final, document=document)
    finally:
        os.close(parent)


def _restore_current_install_pointer(
    current_pointer_path: Path,
    candidate_payload: bytes,
    previous: CurrentInstallPointerState | None,
    transaction_lock: TransactionLock,
) -> None:
    """Restore the prior generation iff the candidate still owns the leaf."""

    absolute = Path(os.path.normpath(os.path.abspath(
        os.fspath(current_pointer_path))))
    parent = _open_anchored_directory(
        absolute.parent, create=False, owner_uid=0, owner_gid=0,
        strict_ancestors=True, transaction_lock=transaction_lock)
    try:
        current = _stat_optional(parent, absolute.name)
        if current is None:
            if previous is None:
                return
            raise InstallError("INSTALL_CURRENT_POINTER_ROLLBACK_REBOUND")
        current_payload, current = _read_at(
            parent, absolute.name, maximum=MAX_CONSUMER_DOCUMENT_BYTES)
        if previous is not None and (
                current_payload == previous.payload and
                _stable_identity(current) ==
                    _stable_identity(previous.metadata)):
            return
        if current_payload != candidate_payload:
            raise InstallError("INSTALL_CURRENT_POINTER_ROLLBACK_REBOUND")
        if previous is None:
            _unlink_exact_at(
                absolute.parent, parent, absolute.name,
                current_payload, current, owner_uid=0, owner_gid=0,
                strict_ancestors=True,
                reason="INSTALL_CURRENT_POINTER_ROLLBACK_REBOUND",
                transaction_lock=transaction_lock)
        else:
            _atomic_write_at(
                absolute.parent, parent, absolute.name,
                previous.payload, 0o600, owner_uid=0, owner_gid=0,
                expected=current, expected_payload=current_payload,
                reason="INSTALL_CURRENT_POINTER_ROLLBACK_FAILED",
                strict_ancestors=True, transaction_lock=transaction_lock)
            observed, restored = _read_at(
                parent, absolute.name, maximum=len(previous.payload))
            if observed != previous.payload:
                raise InstallError("INSTALL_CURRENT_POINTER_ROLLBACK_FAILED")
            validate_current_install_pointer_document(
                previous.document, current_pointer_path=absolute,
                lock_path=Path(transaction_lock[0]) / transaction_lock[3])
            if (
                    restored.st_uid != 0 or restored.st_gid != 0 or
                    stat.S_IMODE(restored.st_mode) != 0o600):
                raise InstallError("INSTALL_CURRENT_POINTER_ROLLBACK_FAILED")
        _rebind_directory(
            absolute.parent, parent, owner_uid=0, owner_gid=0,
            strict_ancestors=True)
        _validate_transaction_lock(transaction_lock)
    finally:
        os.close(parent)


def _runtime_path(filesystem_root: Path, archive_path: str) -> Path:
    root = Path(os.path.normpath(os.path.abspath(os.fspath(filesystem_root))))
    if not root.is_absolute():
        raise InstallError("INSTALL_CONSUMER_FILESYSTEM_ROOT_INVALID")
    relative = normalized_member(archive_path)
    candidate = root.joinpath(*relative.parts)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise InstallError("INSTALL_CONSUMER_RUNTIME_PATH_INVALID") from error
    return candidate


def _verify_installed_closure(
    manifest: dict[str, Any],
    *,
    filesystem_root: Path,
    runtime_uid: int,
    runtime_gid: int,
    strict_ancestors: bool,
    transaction_lock: TransactionLock,
) -> tuple[str, dict[str, tuple[int, ...]]]:
    identities: dict[str, tuple[int, ...]] = {}
    for record in manifest["files"]:
        _validate_transaction_lock(transaction_lock)
        target = _runtime_path(filesystem_root, record["path"])
        parent = _open_anchored_directory(
            target.parent, create=False, owner_uid=runtime_uid,
            owner_gid=runtime_gid, strict_ancestors=strict_ancestors)
        try:
            _validate_transaction_lock(transaction_lock)
            payload, metadata = _read_at(
                parent, target.name, maximum=MAX_ARCHIVE_BYTES)
            _validate_transaction_lock(transaction_lock)
            _rebind_directory(
                target.parent, parent, owner_uid=runtime_uid,
                owner_gid=runtime_gid, strict_ancestors=strict_ancestors)
            _validate_transaction_lock(transaction_lock)
        except InstallError as error:
            raise InstallError("INSTALL_CONSUMER_CLOSURE_INVALID") from error
        finally:
            os.close(parent)
        if (
                metadata.st_uid != runtime_uid or
                metadata.st_gid != runtime_gid or
                stat.S_IMODE(metadata.st_mode) != int(record["mode"], 8) or
                len(payload) != record["size"] or
                digest_bytes(payload) != record["sha256"]):
            raise InstallError("INSTALL_CONSUMER_CLOSURE_INVALID")
        if (
                record["path"] == PAPER_IDENTITY_MANIFEST.as_posix() and
                payload != PAPER_IDENTITY_MANIFEST_BYTES):
            raise InstallError("INSTALL_CONSUMER_DEFAULT_DENY_INVALID")
        identities[record["path"]] = _stable_identity(metadata)
    _validate_transaction_lock(transaction_lock)
    return digest_bytes(canonical_bytes(manifest["files"])), identities


def _build_install_consumption_evidence(
    *,
    receipt_path: Path,
    receipt_payload: bytes,
    receipt: dict[str, Any],
    manifest_path: Path,
    manifest_payload: bytes,
    manifest: dict[str, Any],
    current_install_pointer_path: Path,
    current_install_pointer_payload: bytes,
    current_install_pointer: dict[str, Any],
    closure_sha256: str,
) -> dict[str, Any]:
    evidence = {
        "schema": CONSUMPTION_EVIDENCE_SCHEMA,
        "version": CONSUMPTION_EVIDENCE_VERSION,
        "receipt_path": str(receipt_path),
        "receipt_file_sha256": digest_bytes(receipt_payload),
        "receipt_body_sha256": receipt["body_sha256"],
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": digest_bytes(manifest_payload),
        "archive_sha256": manifest["archive_sha256"],
        "source_baseline_sha256": manifest["source_baseline_sha256"],
        "installer_sha256": manifest["installer_sha256"],
        "installed_file_count": len(manifest["files"]),
        "installed_paths_sha256": receipt["installed_paths_sha256"],
        "closure_sha256": closure_sha256,
        "transaction_lock": receipt["transaction_lock"],
        "default_deny_identity_sha256": PAPER_IDENTITY_MANIFEST_SHA256,
        "lock_mode": "exclusive",
        "verified_under_lock": True,
        "domain": receipt["domain"],
        "backup_root": receipt["backup_root"],
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "current_install_pointer_path": str(current_install_pointer_path),
        "current_install_pointer_file_sha256":
            digest_bytes(current_install_pointer_payload),
        "install_generation": receipt["install_generation"],
        "predecessor_install_generation":
            receipt["predecessor_install_generation"],
        "predecessor_current_install_pointer_file_sha256":
            receipt[
                "predecessor_current_install_pointer_file_sha256"],
    }
    if set(evidence) != INSTALL_CONSUMPTION_EVIDENCE_FIELDS:
        raise InstallError("INSTALL_CONSUMER_EVIDENCE_INVALID")
    return evidence


def acquire_verified_installation(
    *,
    receipt_path: Path,
    manifest_path: Path,
    expected_domain: str,
    expected_backup_root: Path,
    expected_manifest_sha256: str,
    expected_receipt_sha256: str,
    filesystem_root: Path = Path("/"),
    lock_path: Path = TRANSACTION_LOCK_PATH,
    current_install_pointer_path: Path = CURRENT_INSTALL_POINTER_PATH,
    lock_owner_uid: int = 0,
    lock_owner_gid: int = 0,
    runtime_uid: int = 0,
    runtime_gid: int = 0,
    receipt_reader_gid: int = RECEIPT_READER_GID,
    strict_ancestors: bool = True,
    expected_file_count: int = EXPECTED_SHADOW_FILE_COUNT,
) -> VerifiedInstallation:
    """Hold the installer lock and verify its receipt plus current closure."""

    transaction_lock = _acquire_existing_transaction_lock(
        lock_path, owner_uid=lock_owner_uid, owner_gid=lock_owner_gid,
        strict_ancestors=strict_ancestors)
    try:
        receipt_payload, receipt_metadata = _read_consumer_document(
            receipt_path, expected_mode=0o440, expected_uid=lock_owner_uid,
            expected_gid=receipt_reader_gid,
            strict_ancestors=strict_ancestors,
            transaction_lock=transaction_lock)
        manifest_payload, manifest_metadata = _read_consumer_document(
            manifest_path, expected_mode=0o600, expected_uid=lock_owner_uid,
            expected_gid=lock_owner_gid,
            strict_ancestors=strict_ancestors,
            transaction_lock=transaction_lock)
        for expected in (
                expected_manifest_sha256, expected_receipt_sha256):
            if type(expected) is not str or SHA256_IDENTITY.fullmatch(
                    expected) is None:
                raise InstallError("INSTALL_CONSUMER_EXPECTED_DIGEST_INVALID")
        if (
                digest_bytes(manifest_payload) != expected_manifest_sha256 or
                digest_bytes(receipt_payload) != expected_receipt_sha256):
            raise InstallError("INSTALL_CONSUMER_EXPECTED_DIGEST_MISMATCH")
        manifest = validate_manifest_document(strict_json_bytes(
            manifest_payload, "INSTALL_CONSUMER_MANIFEST_INVALID"))
        if manifest_payload != canonical_bytes(manifest):
            raise InstallError("INSTALL_CONSUMER_MANIFEST_NONCANONICAL")
        receipt = validate_install_receipt_document(
            strict_json_bytes(
                receipt_payload, "INSTALL_CONSUMER_RECEIPT_INVALID"),
            manifest, expected_domain=expected_domain,
            expected_backup_root=expected_backup_root,
            receipt_reader_gid=receipt_reader_gid, lock_path=lock_path,
            expected_file_count=expected_file_count)
        if receipt_payload != canonical_bytes(receipt):
            raise InstallError("INSTALL_CONSUMER_RECEIPT_NONCANONICAL")
        current_install_pointer_payload, current_install_pointer_metadata = (
            _read_consumer_document(
                current_install_pointer_path, expected_mode=0o600,
                expected_uid=lock_owner_uid, expected_gid=lock_owner_gid,
                strict_ancestors=strict_ancestors,
                transaction_lock=transaction_lock))
        current_install_pointer = validate_current_install_pointer_document(
            strict_json_bytes(
                current_install_pointer_payload,
                "INSTALL_CURRENT_POINTER_INVALID"),
            current_pointer_path=current_install_pointer_path,
            lock_path=lock_path)
        if current_install_pointer_payload != canonical_bytes(
                current_install_pointer):
            raise InstallError("INSTALL_CURRENT_POINTER_INVALID")
        _validate_current_install_pointer_binding(
            current_install_pointer,
            manifest_path=manifest_path, manifest_payload=manifest_payload,
            manifest=manifest, receipt_path=receipt_path,
            receipt_payload=receipt_payload, receipt=receipt,
            expected_domain=expected_domain,
            expected_backup_root=expected_backup_root,
            current_pointer_path=current_install_pointer_path,
            lock_path=lock_path)
        _current_lock_matches_receipt(
            transaction_lock, receipt["transaction_lock"],
            lock_path=lock_path)
        closure_sha256, closure_identities = _verify_installed_closure(
            manifest, filesystem_root=filesystem_root,
            runtime_uid=runtime_uid, runtime_gid=runtime_gid,
            strict_ancestors=strict_ancestors,
            transaction_lock=transaction_lock)
        _validate_transaction_lock(transaction_lock)
        evidence = _build_install_consumption_evidence(
            receipt_path=receipt_path, receipt_payload=receipt_payload,
            receipt=receipt, manifest_path=manifest_path,
            manifest_payload=manifest_payload, manifest=manifest,
            current_install_pointer_path=current_install_pointer_path,
            current_install_pointer_payload=current_install_pointer_payload,
            current_install_pointer=current_install_pointer,
            closure_sha256=closure_sha256)
        _validate_transaction_lock(transaction_lock)
        return VerifiedInstallation(
            transaction_lock=transaction_lock,
            receipt_path=receipt_path, receipt_payload=receipt_payload,
            receipt_identity=_stable_identity(receipt_metadata),
            receipt=receipt, manifest_path=manifest_path,
            manifest_payload=manifest_payload,
            manifest_identity=_stable_identity(manifest_metadata),
            manifest=manifest, filesystem_root=filesystem_root,
            current_install_pointer_path=current_install_pointer_path,
            current_install_pointer_payload=current_install_pointer_payload,
            current_install_pointer_identity=
                _stable_identity(current_install_pointer_metadata),
            current_install_pointer=current_install_pointer,
            runtime_uid=runtime_uid, runtime_gid=runtime_gid,
            expected_domain=expected_domain,
            expected_backup_root=expected_backup_root,
            receipt_reader_gid=receipt_reader_gid, lock_path=lock_path,
            lock_owner_uid=lock_owner_uid, lock_owner_gid=lock_owner_gid,
            strict_ancestors=strict_ancestors,
            expected_file_count=expected_file_count,
            closure_identities=closure_identities, evidence=evidence)
    except BaseException:
        _release_transaction_lock(transaction_lock)
        raise


def validate_verified_installation(
        verified: VerifiedInstallation) -> dict[str, Any]:
    """Rebind every proof while the same persistent lock remains held."""

    _validate_transaction_lock(verified.transaction_lock)
    receipt_payload, receipt_metadata = _read_consumer_document(
        verified.receipt_path, expected_mode=0o440,
        expected_uid=verified.lock_owner_uid,
        expected_gid=verified.receipt_reader_gid,
        strict_ancestors=verified.strict_ancestors,
        transaction_lock=verified.transaction_lock)
    manifest_payload, manifest_metadata = _read_consumer_document(
        verified.manifest_path, expected_mode=0o600,
        expected_uid=verified.lock_owner_uid,
        expected_gid=verified.lock_owner_gid,
        strict_ancestors=verified.strict_ancestors,
        transaction_lock=verified.transaction_lock)
    current_install_pointer_payload, current_install_pointer_metadata = (
        _read_consumer_document(
            verified.current_install_pointer_path, expected_mode=0o600,
            expected_uid=verified.lock_owner_uid,
            expected_gid=verified.lock_owner_gid,
            strict_ancestors=verified.strict_ancestors,
            transaction_lock=verified.transaction_lock))
    if (
            receipt_payload != verified.receipt_payload or
            _stable_identity(receipt_metadata) != verified.receipt_identity or
            manifest_payload != verified.manifest_payload or
            _stable_identity(manifest_metadata) != verified.manifest_identity or
            current_install_pointer_payload !=
                verified.current_install_pointer_payload or
            _stable_identity(current_install_pointer_metadata) !=
                verified.current_install_pointer_identity):
        raise InstallError("INSTALL_CONSUMER_DOCUMENT_REBOUND")
    manifest = validate_manifest_document(strict_json_bytes(
        manifest_payload, "INSTALL_CONSUMER_MANIFEST_INVALID"))
    receipt = validate_install_receipt_document(
        strict_json_bytes(
            receipt_payload, "INSTALL_CONSUMER_RECEIPT_INVALID"),
        manifest, expected_domain=verified.expected_domain,
        expected_backup_root=verified.expected_backup_root,
        receipt_reader_gid=verified.receipt_reader_gid,
        lock_path=verified.lock_path,
        expected_file_count=verified.expected_file_count)
    current_install_pointer = validate_current_install_pointer_document(
        strict_json_bytes(
            current_install_pointer_payload,
            "INSTALL_CURRENT_POINTER_INVALID"),
        current_pointer_path=verified.current_install_pointer_path,
        lock_path=verified.lock_path)
    if current_install_pointer_payload != canonical_bytes(
            current_install_pointer):
        raise InstallError("INSTALL_CURRENT_POINTER_INVALID")
    _validate_current_install_pointer_binding(
        current_install_pointer,
        manifest_path=verified.manifest_path,
        manifest_payload=manifest_payload, manifest=manifest,
        receipt_path=verified.receipt_path,
        receipt_payload=receipt_payload, receipt=receipt,
        expected_domain=verified.expected_domain,
        expected_backup_root=verified.expected_backup_root,
        current_pointer_path=verified.current_install_pointer_path,
        lock_path=verified.lock_path)
    _current_lock_matches_receipt(
        verified.transaction_lock, receipt["transaction_lock"],
        lock_path=verified.lock_path)
    closure_sha256, closure_identities = _verify_installed_closure(
        manifest, filesystem_root=verified.filesystem_root,
        runtime_uid=verified.runtime_uid, runtime_gid=verified.runtime_gid,
        strict_ancestors=verified.strict_ancestors,
        transaction_lock=verified.transaction_lock)
    _validate_transaction_lock(verified.transaction_lock)
    evidence = _build_install_consumption_evidence(
        receipt_path=verified.receipt_path,
        receipt_payload=receipt_payload, receipt=receipt,
        manifest_path=verified.manifest_path,
        manifest_payload=manifest_payload, manifest=manifest,
        current_install_pointer_path=verified.current_install_pointer_path,
        current_install_pointer_payload=current_install_pointer_payload,
        current_install_pointer=current_install_pointer,
        closure_sha256=closure_sha256)
    _validate_transaction_lock(verified.transaction_lock)
    if (
            closure_identities != verified.closure_identities or
            evidence != verified.evidence):
        raise InstallError("INSTALL_CONSUMER_CLOSURE_REBOUND")
    return evidence


def require_verified_runtime_member(
    verified: VerifiedInstallation,
    archive_path: str,
    payload: bytes,
) -> None:
    """Bind a credential/current caller payload to one verified member."""

    _validate_transaction_lock(verified.transaction_lock)
    if type(payload) is not bytes:
        raise InstallError("INSTALL_CONSUMER_MEMBER_INVALID")
    matches = [
        record for record in verified.manifest["files"]
        if record["path"] == archive_path]
    if (
            len(matches) != 1 or matches[0]["size"] != len(payload) or
            matches[0]["sha256"] != digest_bytes(payload)):
        raise InstallError("INSTALL_CONSUMER_MEMBER_INVALID")
    _validate_transaction_lock(verified.transaction_lock)


def release_verified_installation(verified: VerifiedInstallation) -> None:
    try:
        _validate_transaction_lock(verified.transaction_lock)
    finally:
        _release_transaction_lock(verified.transaction_lock)


def acquire_installation_quarantine_guard(
    *,
    lock_path: Path = TRANSACTION_LOCK_PATH,
    lock_owner_uid: int = 0,
    lock_owner_gid: int = 0,
    strict_ancestors: bool = True,
) -> TransactionLock:
    """Hold only the persistent lock for a fail-closed quarantine path.

    This guard carries no installation-validity claim.  It exists solely so a
    consumer that rejected receipt/manifest/closure evidence can serialize a
    stop-and-mask quarantine without reversing the global lock order.
    """

    lock = _acquire_existing_transaction_lock(
        lock_path, owner_uid=lock_owner_uid, owner_gid=lock_owner_gid,
        strict_ancestors=strict_ancestors)
    _validate_transaction_lock(lock)
    return lock


def validate_installation_quarantine_guard(lock: TransactionLock) -> None:
    _validate_transaction_lock(lock)


def release_installation_quarantine_guard(lock: TransactionLock) -> None:
    try:
        _validate_transaction_lock(lock)
    finally:
        _release_transaction_lock(lock)


def build_install_receipt(
    *,
    finished_at_ms: int,
    domain: str,
    expected_archive_sha256: str,
    expected_baseline_sha256: str,
    expected_installer_sha256: str,
    installed: list[str],
    backup_root: Path,
    replaced: list[str],
    absent: list[str],
    preflight_before: dict[str, Any],
    preflight_after: dict[str, Any],
    transaction_lock_evidence: dict[str, Any],
    receipt_reader_gid: int,
    install_generation: int,
    predecessor_install_generation: int,
    predecessor_current_install_pointer_file_sha256: str,
) -> dict[str, Any]:
    reader_gid = validate_receipt_reader_gid(receipt_reader_gid)
    validate_preflight_evidence(preflight_before, domain)
    validate_preflight_evidence(preflight_after, domain)
    if preflight_after != preflight_before:
        raise InstallError("INSTALL_PREFLIGHT_DRIFT")
    validate_transaction_lock_evidence(transaction_lock_evidence)
    validate_install_receipt_lineage(
        install_generation, predecessor_install_generation,
        predecessor_current_install_pointer_file_sha256)
    identity_path = PAPER_IDENTITY_MANIFEST.as_posix()
    if (
            type(installed) is not list or type(replaced) is not list or
            type(absent) is not list or
            any(type(path) is not str for path in installed + replaced + absent) or
            installed != sorted(installed) or
            len(installed) != len(set(installed)) or
            len(replaced) != len(set(replaced)) or
            len(absent) != len(set(absent)) or
            set(replaced) & set(absent) or
            set(replaced) | set(absent) != set(installed)):
        raise InstallError("INSTALL_RECEIPT_PATH_PARTITION_INVALID")
    if not installed or installed[0] != identity_path:
        raise InstallError("INSTALL_RECEIPT_PAPER_IDENTITY_MISSING")
    identity_replaced = identity_path in replaced
    identity_absent = identity_path in absent
    if identity_replaced == identity_absent:
        raise InstallError("INSTALL_RECEIPT_PATH_PARTITION_INVALID")
    body = {
        "schema": RECEIPT_SCHEMA,
        "version": RECEIPT_VERSION,
        "finished_at_ms": finished_at_ms,
        "domain": domain,
        "archive_sha256": expected_archive_sha256,
        "source_baseline_sha256": expected_baseline_sha256,
        "installer_sha256": expected_installer_sha256,
        "installed_file_count": len(installed),
        "installed_paths_sha256": digest_bytes(canonical_bytes(installed)),
        "backup_root": str(backup_root),
        "replaced_file_count": len(replaced),
        "new_file_count": len(absent),
        "install_generation": install_generation,
        "predecessor_install_generation": predecessor_install_generation,
        "predecessor_current_install_pointer_file_sha256":
            predecessor_current_install_pointer_file_sha256,
        "default_deny_identity_manifest": {
            "destination": "/" + identity_path,
            "archive_path": identity_path,
            "uid": 0,
            "gid": 0,
            "mode": "0600",
            "size": len(PAPER_IDENTITY_MANIFEST_BYTES),
            "sha256": PAPER_IDENTITY_MANIFEST_SHA256,
            "installed": True,
            "preexisting_backed_up": identity_replaced,
            "new_file": identity_absent,
        },
        "reader_gid": reader_gid,
        "transaction_lock": transaction_lock_evidence,
        "preflight_before": preflight_before,
        "preflight_after": preflight_after,
        "preflight_continuity_claimed": False,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "services_started": False,
        "services_enabled": False,
        "status": "PASSIVE_INSTALL_COMPLETE",
    }
    return {**body, "body_sha256": digest_bytes(canonical_bytes(body))}


def publish_install_receipt(
    receipt_output: Path,
    receipt: dict[str, Any],
    receipt_reader_gid: int,
    transaction_lock: TransactionLock | None = None,
) -> str:
    _validate_optional_transaction_lock(transaction_lock)
    reader_gid = validate_receipt_reader_gid(receipt_reader_gid)
    payload = canonical_bytes(receipt)
    absolute = Path(os.path.abspath(receipt_output))
    parent = _open_anchored_directory(
        absolute.parent, create=True, owner_uid=0, owner_gid=0,
        strict_ancestors=True, transaction_lock=transaction_lock)
    try:
        if _stat_optional(parent, absolute.name) is not None:
            raise InstallError("INSTALL_RECEIPT_OUTPUT_EXISTS")
        _atomic_write_at(
            absolute.parent, parent, absolute.name, payload, 0o440,
            owner_uid=0, owner_gid=reader_gid, expected=None,
            expected_payload=None,
            reason="INSTALL_RECEIPT_PUBLISH_FAILED", strict_ancestors=True,
            transaction_lock=transaction_lock)
    finally:
        os.close(parent)
    result = stable_verify_file(
        receipt_output,
        payload,
        0o440,
        expected_uid=0,
        expected_gid=reader_gid,
    )
    _validate_optional_transaction_lock(transaction_lock)
    return result


def _install_payloads(
    manifest: dict[str, Any],
    payloads: dict[str, bytes],
    backup_root: Path,
    *,
    destination_root: Path = Path("/"),
    owner_uid: int = 0,
    owner_gid: int = 0,
    strict_ancestors: bool = True,
    transaction_lock: TransactionLock | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Apply the verified manifest loop and roll back its exact write set."""
    installed: list[str] = []
    replaced: list[str] = []
    absent: list[str] = []
    try:
        for record in manifest["files"]:
            relative = record["path"]
            destination = Path(os.path.abspath(destination_root / relative))
            backup = Path(os.path.abspath(backup_root / relative))
            destination_parent = _open_anchored_directory(
                destination.parent, create=True, owner_uid=owner_uid,
                owner_gid=owner_gid, strict_ancestors=strict_ancestors,
                transaction_lock=transaction_lock)
            try:
                _rebind_directory(
                    destination.parent, destination_parent,
                    owner_uid=owner_uid, owner_gid=owner_gid,
                    strict_ancestors=strict_ancestors)
                existing = _stat_optional(destination_parent, destination.name)
                if existing is not None:
                    old_payload, old_metadata = _read_at(
                        destination_parent, destination.name)
                    if (
                            old_metadata.st_uid != owner_uid or
                            old_metadata.st_gid != owner_gid):
                        raise InstallError("INSTALL_DESTINATION_OWNER_INVALID")
                    backup_parent = _open_anchored_directory(
                        backup.parent, create=True, owner_uid=owner_uid,
                        owner_gid=owner_gid,
                        strict_ancestors=strict_ancestors,
                        transaction_lock=transaction_lock)
                    try:
                        _rebind_directory(
                            backup.parent, backup_parent,
                            owner_uid=owner_uid, owner_gid=owner_gid,
                            strict_ancestors=strict_ancestors)
                        if _stat_optional(backup_parent, backup.name) is not None:
                            raise InstallError("INSTALL_BACKUP_PATH_EXISTS")
                        _atomic_write_at(
                            backup.parent, backup_parent, backup.name,
                            old_payload, stat.S_IMODE(old_metadata.st_mode),
                            owner_uid=owner_uid, owner_gid=owner_gid,
                            expected=None, expected_payload=None,
                            reason="INSTALL_BACKUP_WRITE_FAILED",
                            strict_ancestors=strict_ancestors,
                            transaction_lock=transaction_lock)
                    finally:
                        os.close(backup_parent)
                    replaced.append(relative)
                else:
                    absent.append(relative)
                installed.append(relative)
                _atomic_write_at(
                    destination.parent, destination_parent, destination.name,
                    payloads[relative], int(record["mode"], 8),
                    owner_uid=owner_uid, owner_gid=owner_gid,
                    expected=existing, expected_payload=(
                        old_payload if existing is not None else None),
                    reason="INSTALL_DESTINATION_WRITE_FAILED",
                    strict_ancestors=strict_ancestors,
                    transaction_lock=transaction_lock)
            finally:
                os.close(destination_parent)
        for record in manifest["files"]:
            destination = Path(os.path.abspath(
                destination_root / record["path"]))
            parent = _open_anchored_directory(
                destination.parent, create=False, owner_uid=owner_uid,
                owner_gid=owner_gid, strict_ancestors=strict_ancestors)
            try:
                payload, metadata = _read_at(
                    parent, destination.name,
                    maximum=int(record["size"]))
                _rebind_directory(
                    destination.parent, parent, owner_uid=owner_uid,
                    owner_gid=owner_gid,
                    strict_ancestors=strict_ancestors)
                if (
                        payload != payloads[record["path"]] or
                        metadata.st_uid != owner_uid or
                        metadata.st_gid != owner_gid or
                        stat.S_IMODE(metadata.st_mode) !=
                        int(record["mode"], 8) or
                        digest_bytes(payload) != record["sha256"]):
                    raise InstallError("INSTALL_POST_VERIFY_FAILED")
            finally:
                os.close(parent)
    except Exception as error:
        if transaction_lock is not None:
            try:
                _validate_transaction_lock(transaction_lock)
            except InstallError as lock_error:
                try:
                    _reassert_default_deny_only(
                        destination_root=destination_root,
                        owner_uid=owner_uid, owner_gid=owner_gid,
                        strict_ancestors=strict_ancestors)
                except Exception as deny_error:
                    raise InstallError(
                        "INSTALL_TRANSACTION_LOCK_COMPROMISED") from deny_error
                raise InstallError(
                    "INSTALL_TRANSACTION_LOCK_COMPROMISED") from lock_error
        try:
            _rollback_payloads(
                manifest, payloads, backup_root, installed, replaced, absent,
                destination_root=destination_root, owner_uid=owner_uid,
                owner_gid=owner_gid, strict_ancestors=strict_ancestors,
                transaction_lock=transaction_lock)
        except Exception as rollback_error:
            raise InstallError("INSTALL_ROLLBACK_FAILED") from rollback_error
        raise error
    return installed, replaced, absent


def _reassert_default_deny_only(
    *, destination_root: Path, owner_uid: int, owner_gid: int,
    strict_ancestors: bool,
) -> None:
    destination = Path(os.path.abspath(
        destination_root / PAPER_IDENTITY_MANIFEST.as_posix()))
    parent = _open_anchored_directory(
        destination.parent, create=True, owner_uid=owner_uid,
        owner_gid=owner_gid, strict_ancestors=strict_ancestors)
    try:
        current = _stat_optional(parent, destination.name)
        current_payload: bytes | None = None
        if current is not None:
            current_payload, current = _read_at(parent, destination.name)
            if (
                    current_payload == PAPER_IDENTITY_MANIFEST_BYTES and
                    current.st_uid == owner_uid and
                    current.st_gid == owner_gid and
                    stat.S_IMODE(current.st_mode) == 0o600):
                return
        _atomic_write_at(
            destination.parent, parent, destination.name,
            PAPER_IDENTITY_MANIFEST_BYTES, 0o600,
            owner_uid=owner_uid, owner_gid=owner_gid,
            expected=current, expected_payload=current_payload,
            reason="INSTALL_DENY_ALL_REASSERT_FAILED",
            strict_ancestors=strict_ancestors, transaction_lock=None)
        payload, metadata = _read_at(
            parent, destination.name, maximum=len(PAPER_IDENTITY_MANIFEST_BYTES))
        _rebind_directory(
            destination.parent, parent, owner_uid=owner_uid,
            owner_gid=owner_gid, strict_ancestors=strict_ancestors)
        if (
                payload != PAPER_IDENTITY_MANIFEST_BYTES or
                metadata.st_uid != owner_uid or metadata.st_gid != owner_gid or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise InstallError("INSTALL_DENY_ALL_REASSERT_FAILED")
    finally:
        os.close(parent)


def _rollback_payloads(
    manifest: dict[str, Any],
    payloads: dict[str, bytes],
    backup_root: Path,
    installed: list[str],
    replaced: list[str],
    absent: list[str],
    *,
    destination_root: Path,
    owner_uid: int,
    owner_gid: int,
    strict_ancestors: bool,
    transaction_lock: TransactionLock | None = None,
) -> None:
    records = {record["path"]: record for record in manifest["files"]}
    replaced_set = set(replaced)
    absent_set = set(absent)
    for relative in reversed(installed):
        record = records[relative]
        destination = Path(os.path.abspath(destination_root / relative))
        parent = _open_anchored_directory(
            destination.parent, create=False, owner_uid=owner_uid,
            owner_gid=owner_gid, strict_ancestors=strict_ancestors)
        try:
            current = _stat_optional(parent, destination.name)
            if relative == PAPER_IDENTITY_MANIFEST.as_posix():
                if current is not None:
                    current_payload, current_metadata = _read_at(
                        parent, destination.name)
                    if (
                            current_payload == PAPER_IDENTITY_MANIFEST_BYTES and
                            current_metadata.st_uid == owner_uid and
                            current_metadata.st_gid == owner_gid and
                            stat.S_IMODE(current_metadata.st_mode) == 0o600):
                        continue
                _atomic_write_at(
                    destination.parent, parent, destination.name,
                    PAPER_IDENTITY_MANIFEST_BYTES, 0o600,
                    owner_uid=owner_uid, owner_gid=owner_gid,
                    expected=current,
                    expected_payload=(
                        current_payload if current is not None else None),
                    reason="INSTALL_DENY_ALL_REASSERT_FAILED",
                    strict_ancestors=strict_ancestors,
                    transaction_lock=transaction_lock)
                continue
            if relative in replaced_set:
                backup = Path(os.path.abspath(backup_root / relative))
                backup_parent = _open_anchored_directory(
                    backup.parent, create=False, owner_uid=owner_uid,
                    owner_gid=owner_gid,
                    strict_ancestors=strict_ancestors)
                try:
                    old_payload, old_metadata = _read_at(
                        backup_parent, backup.name)
                    _rebind_directory(
                        backup.parent, backup_parent, owner_uid=owner_uid,
                        owner_gid=owner_gid,
                        strict_ancestors=strict_ancestors)
                finally:
                    os.close(backup_parent)
                if current is not None:
                    current_payload, current_metadata = _read_at(
                        parent, destination.name)
                    if (
                            current_payload == old_payload and
                            current_metadata.st_uid == owner_uid and
                            current_metadata.st_gid == owner_gid and
                            stat.S_IMODE(current_metadata.st_mode) ==
                            stat.S_IMODE(old_metadata.st_mode)):
                        continue
                    if (
                            current_payload != payloads[relative] or
                            current_metadata.st_uid != owner_uid or
                            current_metadata.st_gid != owner_gid or
                            stat.S_IMODE(current_metadata.st_mode) !=
                            int(record["mode"], 8)):
                        raise InstallError("INSTALL_ROLLBACK_TARGET_REBOUND")
                _atomic_write_at(
                    destination.parent, parent, destination.name,
                    old_payload, stat.S_IMODE(old_metadata.st_mode),
                    owner_uid=owner_uid, owner_gid=owner_gid,
                    expected=current, expected_payload=(
                        current_payload if current is not None else None),
                    reason="INSTALL_ROLLBACK_WRITE_FAILED",
                    strict_ancestors=strict_ancestors,
                    transaction_lock=transaction_lock)
            elif relative in absent_set:
                if current is None:
                    continue
                current_payload, current_metadata = _read_at(
                    parent, destination.name)
                if (
                        current_payload != payloads[relative] or
                        current_metadata.st_uid != owner_uid or
                        current_metadata.st_gid != owner_gid or
                        stat.S_IMODE(current_metadata.st_mode) !=
                        int(record["mode"], 8)):
                    raise InstallError("INSTALL_ROLLBACK_TARGET_REBOUND")
                _unlink_exact_at(
                    destination.parent, parent, destination.name,
                    current_payload, current_metadata,
                    owner_uid=owner_uid, owner_gid=owner_gid,
                    strict_ancestors=strict_ancestors,
                    reason="INSTALL_ROLLBACK_TARGET_REBOUND",
                    transaction_lock=transaction_lock)
            else:
                raise InstallError("INSTALL_ROLLBACK_SET_INVALID")
        finally:
            os.close(parent)


def _prepare_install_outputs(
        backup_root: Path, receipt_output: Path,
        manifest: dict[str, Any],
        transaction_lock: TransactionLock | None = None,
) -> tuple[Path, Path]:
    _validate_optional_transaction_lock(transaction_lock)
    if not backup_root.is_absolute() or not receipt_output.is_absolute():
        raise InstallError("INSTALL_OUTPUT_PATH_INVALID")
    backup_root = Path(os.path.normpath(os.path.abspath(os.fspath(backup_root))))
    receipt_output = Path(
        os.path.normpath(os.path.abspath(os.fspath(receipt_output))))
    if (
            backup_root == receipt_output or
            backup_root in receipt_output.parents or
            receipt_output in backup_root.parents):
        raise InstallError("INSTALL_OUTPUT_PATH_INVALID")
    state_root = Path("/var/lib/hepta")
    if (
            not backup_root.is_relative_to(state_root) or
            not receipt_output.is_relative_to(state_root)):
        raise InstallError("INSTALL_OUTPUT_PATH_INVALID")
    destinations = {Path(os.path.normpath(os.path.abspath(
        os.fspath(Path("/") / record["path"])))) for record in manifest["files"]}
    for output in (backup_root, receipt_output):
        if (
                output == TRANSACTION_LOCK_PATH or
                output in TRANSACTION_LOCK_PATH.parents or
                TRANSACTION_LOCK_PATH in output.parents or
                output == CURRENT_INSTALL_POINTER_PATH or
                output in CURRENT_INSTALL_POINTER_PATH.parents or
                CURRENT_INSTALL_POINTER_PATH in output.parents):
            raise InstallError("INSTALL_OUTPUT_PATH_INVALID")
        if any(
                output == destination or output in destination.parents or
                destination in output.parents
                for destination in destinations):
            raise InstallError("INSTALL_OUTPUT_PATH_INVALID")
    backup_parent = _open_anchored_directory(
        backup_root.parent, create=True, owner_uid=0, owner_gid=0,
        strict_ancestors=True, transaction_lock=transaction_lock)
    receipt_parent = -1
    try:
        receipt_parent = _open_anchored_directory(
            receipt_output.parent, create=True, owner_uid=0, owner_gid=0,
            strict_ancestors=True, transaction_lock=transaction_lock)
        if _stat_optional(backup_parent, backup_root.name) is not None:
            raise InstallError("INSTALL_OUTPUT_EXISTS")
        if _stat_optional(receipt_parent, receipt_output.name) is not None:
            raise InstallError("INSTALL_OUTPUT_EXISTS")
        _rebind_directory(
            backup_root.parent, backup_parent, owner_uid=0, owner_gid=0,
            strict_ancestors=True)
        _rebind_directory(
            receipt_output.parent, receipt_parent, owner_uid=0, owner_gid=0,
            strict_ancestors=True)
        _validate_optional_transaction_lock(transaction_lock)
        try:
            os.mkdir(backup_root.name, 0o700, dir_fd=backup_parent)
            _validate_optional_transaction_lock(transaction_lock)
            child = os.open(
                backup_root.name, DIRECTORY_FLAGS, dir_fd=backup_parent)
        except OSError as error:
            raise InstallError("INSTALL_BACKUP_CREATE_FAILED") from error
        try:
            os.fchown(child, 0, 0)
            os.fchmod(child, 0o700)
            os.fsync(child)
        finally:
            os.close(child)
        os.fsync(backup_parent)
        _rebind_directory(
            backup_root.parent, backup_parent, owner_uid=0, owner_gid=0,
            strict_ancestors=True)
    finally:
        if receipt_parent >= 0:
            os.close(receipt_parent)
        os.close(backup_parent)
    backup = _open_anchored_directory(
        backup_root, create=False, owner_uid=0, owner_gid=0,
        strict_ancestors=True, leaf_mode=0o700)
    os.close(backup)
    _validate_optional_transaction_lock(transaction_lock)
    return backup_root, receipt_output


def _remove_exact_receipt(
        path: Path, payload: bytes, reader_gid: int,
        transaction_lock: TransactionLock | None = None) -> None:
    _validate_optional_transaction_lock(transaction_lock)
    parent = _open_anchored_directory(
        path.parent, create=False, owner_uid=0, owner_gid=0,
        strict_ancestors=True)
    try:
        current = _stat_optional(parent, path.name)
        if current is None:
            return
        observed, metadata = _read_at(parent, path.name, maximum=len(payload))
        if (
                observed != payload or metadata.st_uid != 0 or
                metadata.st_gid != reader_gid or
                stat.S_IMODE(metadata.st_mode) != 0o440):
            raise InstallError("INSTALL_RECEIPT_ROLLBACK_REBOUND")
        _unlink_exact_at(
            path.parent, parent, path.name, observed, metadata,
            owner_uid=0, owner_gid=0, strict_ancestors=True,
            reason="INSTALL_RECEIPT_ROLLBACK_REBOUND",
            transaction_lock=transaction_lock,
            retain_in_quarantine=transaction_lock is None)
    finally:
        os.close(parent)


def _rollback_after_receipt_failure(
    error: Exception,
    *,
    receipt_output: Path,
    receipt_payload: bytes | None,
    reader_gid: int,
    manifest: dict[str, Any],
    payloads: dict[str, bytes],
    backup_root: Path,
    installed: list[str],
    replaced: list[str],
    absent: list[str],
    transaction_lock: TransactionLock | None = None,
    current_pointer_path: Path = CURRENT_INSTALL_POINTER_PATH,
    current_pointer_payload: bytes | None = None,
    previous_current_pointer: CurrentInstallPointerState | None = None,
) -> None:
    cleanup_error: Exception | None = None
    rollback_error: Exception | None = None
    lock_error: Exception | None = None
    if transaction_lock is not None:
        try:
            _validate_transaction_lock(transaction_lock)
        except Exception as caught:
            lock_error = caught
    if lock_error is not None:
        if receipt_payload is not None:
            try:
                _remove_exact_receipt(
                    receipt_output, receipt_payload, reader_gid, None)
            except Exception as caught:
                cleanup_error = caught
        try:
            _reassert_default_deny_only(
                destination_root=Path("/"), owner_uid=0, owner_gid=0,
                strict_ancestors=True)
        except Exception as caught:
            rollback_error = caught
        raise InstallError("INSTALL_TRANSACTION_LOCK_COMPROMISED") from (
            rollback_error if rollback_error is not None else
                    cleanup_error if cleanup_error is not None else lock_error)
    if receipt_payload is not None:
        try:
            _remove_exact_receipt(
                receipt_output, receipt_payload, reader_gid, transaction_lock)
        except Exception as caught:
            cleanup_error = caught
    try:
        _rollback_payloads(
            manifest, payloads, backup_root, installed, replaced, absent,
            destination_root=Path("/"), owner_uid=0, owner_gid=0,
            strict_ancestors=True, transaction_lock=transaction_lock)
    except Exception as caught:
        rollback_error = caught
    # The old generation becomes current only after both the new receipt and
    # the full payload write set have been rolled back successfully.  If
    # either step is uncertain, retain the candidate pointer so neither the
    # old nor new externally pinned generation can be accepted.
    if (
            cleanup_error is None and rollback_error is None and
            current_pointer_payload is not None):
        try:
            assert transaction_lock is not None
            _restore_current_install_pointer(
                current_pointer_path, current_pointer_payload,
                previous_current_pointer, transaction_lock)
        except Exception as caught:
            cleanup_error = caught
    if transaction_lock is not None:
        try:
            _validate_transaction_lock(transaction_lock)
        except Exception as caught:
            if receipt_payload is not None:
                try:
                    _remove_exact_receipt(
                        receipt_output, receipt_payload, reader_gid, None)
                except Exception as cleanup_caught:
                    cleanup_error = cleanup_caught
            try:
                _reassert_default_deny_only(
                    destination_root=Path("/"), owner_uid=0, owner_gid=0,
                    strict_ancestors=True)
            except Exception as deny_caught:
                rollback_error = deny_caught
            raise InstallError(
                "INSTALL_TRANSACTION_LOCK_COMPROMISED") from (
                    rollback_error if rollback_error is not None else
                    cleanup_error if cleanup_error is not None else caught)
    if (
            cleanup_error is not None or rollback_error is not None):
        raise InstallError("INSTALL_RECEIPT_ROLLBACK_FAILED") from (
            rollback_error if rollback_error is not None else
            cleanup_error)
    raise error


def _validate_current_install_path_set(
    previous: CurrentInstallPointerState | None,
    manifest: dict[str, Any],
    transaction_lock: TransactionLock | None = None,
) -> None:
    """Allow only an authenticated append-only install path migration."""

    if previous is None:
        return
    installed_paths = [record["path"] for record in manifest["files"]]
    if (
            previous.document["installed_file_count"] !=
                len(installed_paths) or
            previous.document["installed_paths_sha256"] !=
                digest_bytes(canonical_bytes(installed_paths))):
        if (
                previous.document["installed_file_count"] >=
                    len(installed_paths) or
                transaction_lock is None):
            raise InstallError("INSTALL_CURRENT_PATH_SET_DRIFT")
        previous_manifest_path = _canonical_absolute_document_path(
            previous.document.get("manifest_path"),
            "INSTALL_CURRENT_PATH_SET_DRIFT")
        previous_manifest_payload, _ = _read_consumer_document(
            previous_manifest_path,
            expected_mode=0o600,
            expected_uid=transaction_lock[6],
            expected_gid=transaction_lock[7],
            strict_ancestors=True,
            transaction_lock=transaction_lock)
        if (
                digest_bytes(previous_manifest_payload) !=
                    previous.document.get("manifest_file_sha256")):
            raise InstallError("INSTALL_CURRENT_PATH_SET_DRIFT")
        previous_manifest = validate_manifest_document(strict_json_bytes(
            previous_manifest_payload, "INSTALL_CURRENT_PATH_SET_DRIFT"))
        previous_paths = [
            record["path"] for record in previous_manifest["files"]]
        if (
                len(previous_paths) !=
                    previous.document["installed_file_count"] or
                digest_bytes(canonical_bytes(previous_paths)) !=
                    previous.document["installed_paths_sha256"] or
                not set(previous_paths).issubset(installed_paths)):
            raise InstallError("INSTALL_CURRENT_PATH_SET_DRIFT")


def _validate_expected_current_install_lineage(
    previous: CurrentInstallPointerState | None,
    expected_generation: int,
    expected_pointer_file_sha256: str,
) -> None:
    """Bind one invocation to the exact predecessor observed by its caller."""

    if (
            type(expected_generation) is not int or
            not 0 <= expected_generation <= MAX_INSTALL_GENERATION or
            type(expected_pointer_file_sha256) is not str or
            (expected_generation == 0) !=
                (expected_pointer_file_sha256 == "absent") or
            (expected_generation > 0 and
             SHA256_IDENTITY.fullmatch(
                 expected_pointer_file_sha256) is None)):
        raise InstallError("INSTALL_EXPECTED_CURRENT_LINEAGE_INVALID")
    if previous is None:
        if expected_generation != 0:
            raise InstallError("INSTALL_CURRENT_LINEAGE_MISMATCH")
        return
    if (
            expected_generation == 0 or
            previous.document["generation"] != expected_generation or
            digest_bytes(previous.payload) !=
                expected_pointer_file_sha256):
        raise InstallError("INSTALL_CURRENT_LINEAGE_MISMATCH")
    if previous.document["generation"] >= MAX_INSTALL_GENERATION:
        raise InstallError("INSTALL_GENERATION_EXHAUSTED")


def install(
    archive: Path,
    manifest_path: Path,
    expected_archive_sha256: str,
    expected_baseline_sha256: str,
    expected_installer_sha256: str,
    expected_current_install_generation: int,
    expected_current_install_pointer_sha256: str,
    backup_root: Path,
    receipt_output: Path,
    domain: str,
    receipt_reader_gid: int,
) -> dict[str, Any]:
    reader_gid = validate_receipt_reader_gid(receipt_reader_gid)
    if os.geteuid() != 0:
        raise InstallError("INSTALL_ROOT_REQUIRED")
    if not manifest_path.is_absolute():
        raise InstallError("INSTALL_MANIFEST_PATH_INVALID")
    manifest_path = Path(os.path.normpath(os.path.abspath(
        os.fspath(manifest_path))))
    if manifest_path == CURRENT_INSTALL_POINTER_PATH:
        raise InstallError("INSTALL_MANIFEST_PATH_INVALID")
    manifest = load_manifest(manifest_path)
    if (manifest["archive_sha256"] != expected_archive_sha256 or
            manifest["source_baseline_sha256"] != expected_baseline_sha256 or
            manifest["installer_sha256"] != expected_installer_sha256):
        raise InstallError("INSTALL_EXPECTED_BINDING_MISMATCH")
    if digest_file(Path(__file__)) != expected_installer_sha256:
        raise InstallError("INSTALL_INSTALLER_DIGEST_MISMATCH")
    payloads = verify_archive(archive, manifest)
    if digest_bytes(payloads[INSTALLER_MEMBER.as_posix()]) != (
            expected_installer_sha256):
        raise InstallError("INSTALL_ARCHIVE_INSTALLER_BINDING_INVALID")
    lock = _acquire_transaction_lock()
    receipt_payload: bytes | None = None
    current_pointer_payload: bytes | None = None
    previous_current_pointer: CurrentInstallPointerState | None = None
    try:
        preflight_before = validate_preflight_evidence(
            safety_preflight(domain), domain)
        _validate_transaction_lock(lock)
        manifest_payload, manifest_metadata = _read_consumer_document(
            manifest_path, expected_mode=0o600, expected_uid=0,
            expected_gid=0, strict_ancestors=True,
            transaction_lock=lock)
        locked_manifest = validate_manifest_document(strict_json_bytes(
            manifest_payload, "INSTALL_MANIFEST_INVALID"))
        if (
                manifest_payload != canonical_bytes(locked_manifest) or
                locked_manifest != manifest or
                manifest_metadata.st_uid != 0 or
                manifest_metadata.st_gid != 0 or
                stat.S_IMODE(manifest_metadata.st_mode) != 0o600):
            raise InstallError("INSTALL_MANIFEST_REBOUND")
        manifest = locked_manifest
        previous_current_pointer = _read_current_install_pointer_state(
            CURRENT_INSTALL_POINTER_PATH, lock)
        _validate_expected_current_install_lineage(
            previous_current_pointer,
            expected_current_install_generation,
            expected_current_install_pointer_sha256)
        _validate_current_install_path_set(
            previous_current_pointer, manifest, lock)
        install_generation = (
            1 if previous_current_pointer is None else
            previous_current_pointer.document["generation"] + 1)
        predecessor_install_generation = (
            0 if previous_current_pointer is None else
            previous_current_pointer.document["generation"])
        predecessor_pointer_sha256 = (
            "absent" if previous_current_pointer is None else
            digest_bytes(previous_current_pointer.payload))
        _validate_transaction_lock(lock)
        backup_root, receipt_output = _prepare_install_outputs(
            backup_root, receipt_output, manifest, lock)
        _validate_transaction_lock(lock)
        installed, replaced, absent = _install_payloads(
            manifest, payloads, backup_root, transaction_lock=lock)
        try:
            _validate_transaction_lock(lock)
            preflight_after = validate_preflight_evidence(
                safety_preflight(domain), domain)
            if preflight_after != preflight_before:
                raise InstallError("INSTALL_PREFLIGHT_DRIFT")
            _validate_transaction_lock(lock)
            receipt = build_install_receipt(
                finished_at_ms=int(time.time() * 1000),
                domain=domain,
                expected_archive_sha256=expected_archive_sha256,
                expected_baseline_sha256=expected_baseline_sha256,
                expected_installer_sha256=expected_installer_sha256,
                installed=installed,
                backup_root=backup_root,
                replaced=replaced,
                absent=absent,
                preflight_before=preflight_before,
                preflight_after=preflight_after,
                transaction_lock_evidence=_transaction_lock_evidence(lock),
                receipt_reader_gid=reader_gid,
                install_generation=install_generation,
                predecessor_install_generation=
                    predecessor_install_generation,
                predecessor_current_install_pointer_file_sha256=
                    predecessor_pointer_sha256,
            )
            receipt_payload = canonical_bytes(receipt)
            publish_install_receipt(
                receipt_output, receipt, reader_gid, transaction_lock=lock)
            _validate_transaction_lock(lock)
            current_pointer = build_current_install_pointer(
                generation=install_generation, domain=domain,
                backup_root=backup_root, manifest_path=manifest_path,
                manifest_payload=manifest_payload, manifest=manifest,
                receipt_path=receipt_output, receipt_payload=receipt_payload,
                receipt=receipt,
                current_pointer_path=CURRENT_INSTALL_POINTER_PATH,
                lock_path=TRANSACTION_LOCK_PATH)
            current_pointer_payload = canonical_bytes(current_pointer)
            published_pointer = _publish_current_install_pointer(
                CURRENT_INSTALL_POINTER_PATH, current_pointer,
                previous_current_pointer, lock)
            if published_pointer.payload != current_pointer_payload:
                raise InstallError("INSTALL_CURRENT_POINTER_PUBLISH_FAILED")
            _validate_current_install_pointer_binding(
                published_pointer.document,
                manifest_path=manifest_path, manifest_payload=manifest_payload,
                manifest=manifest, receipt_path=receipt_output,
                receipt_payload=receipt_payload, receipt=receipt,
                expected_domain=domain, expected_backup_root=backup_root,
                current_pointer_path=CURRENT_INSTALL_POINTER_PATH,
                lock_path=TRANSACTION_LOCK_PATH)
            _validate_transaction_lock(lock)
        except Exception as error:
            _rollback_after_receipt_failure(
                error, receipt_output=receipt_output,
                receipt_payload=receipt_payload, reader_gid=reader_gid,
                manifest=manifest, payloads=payloads, backup_root=backup_root,
                installed=installed, replaced=replaced, absent=absent,
                transaction_lock=lock,
                current_pointer_path=CURRENT_INSTALL_POINTER_PATH,
                current_pointer_payload=current_pointer_payload,
                previous_current_pointer=previous_current_pointer)
        return receipt
    except Exception as error:
        try:
            _validate_transaction_lock(lock)
        except Exception as lock_error:
            cleanup_error: Exception | None = None
            deny_error: Exception | None = None
            if receipt_payload is not None:
                try:
                    _remove_exact_receipt(
                        receipt_output, receipt_payload, reader_gid, None)
                except Exception as caught:
                    cleanup_error = caught
            try:
                _reassert_default_deny_only(
                    destination_root=Path("/"), owner_uid=0, owner_gid=0,
                    strict_ancestors=True)
            except Exception as caught:
                deny_error = caught
            raise InstallError("INSTALL_TRANSACTION_LOCK_COMPROMISED") from (
                deny_error if deny_error is not None else
                cleanup_error if cleanup_error is not None else lock_error)
        raise error
    finally:
        _release_transaction_lock(lock)


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--source-baseline-sha256", required=True)
    parser.add_argument("--installer-sha256", required=True)
    parser.add_argument(
        "--expected-current-install-generation", required=True,
        type=install_generation_argument)
    parser.add_argument(
        "--expected-current-install-pointer-sha256", required=True)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--receipt-output", required=True, type=Path)
    parser.add_argument(
        "--receipt-reader-gid",
        required=True,
        type=receipt_reader_gid_argument,
    )
    parser.add_argument("--domain", required=True)
    return parser


def main() -> int:
    parser = argument_parser()
    arguments = parser.parse_args()
    try:
        receipt = install(
            arguments.archive.resolve(strict=True),
            arguments.manifest.resolve(strict=True),
            arguments.archive_sha256,
            arguments.source_baseline_sha256,
            arguments.installer_sha256,
            arguments.expected_current_install_generation,
            arguments.expected_current_install_pointer_sha256,
            arguments.backup_root,
            arguments.receipt_output,
            arguments.domain,
            arguments.receipt_reader_gid,
        )
    except (InstallError, OSError, subprocess.SubprocessError) as error:
        print(f"hepta-shadow-host-installer: FAIL: {error}", file=sys.stderr)
        return 2
    print(
        "hepta-shadow-host-installer: PASS "
        f"files={receipt['installed_file_count']} "
        f"receipt={digest_bytes(canonical_bytes(receipt))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
