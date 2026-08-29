#!/usr/bin/env python3

"""Explicit, WATCH-only bootstrap for the fixed HeptaTrader Agent identity.

The runtime package is passive. This helper must be invoked by a root operator
after the Gateway supervisor is available. It never accepts or emits a token
and it cannot request PAPER or LIVE capabilities.
"""

from __future__ import annotations

import argparse
import fcntl
import grp
import hashlib
import hmac
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hepta_agent_trust_domain import (
    TrustDomainRuntimeError, load_runtime_config,
)

DOMAIN_ID = "default"
AGENT_NAME = "hepta-agent"
AGENT_UID = 2004
AGENT_GID = 2004
GATEWAY_NAME = "hepta-gateway"
GATEWAY_UID = 2001
GATEWAY_GID = 2001
GATEWAY_SUPPLEMENTARY_GROUPS: tuple[tuple[str, int], ...] = ()
RUNTIME_PARENT = Path("/run/hepta-agent")
TOKEN_NAME = "session.token"
FENCE_TOKEN_NAME = ".session-fence.token"
LOCK_NAME = ".session-bootstrap.lock"
WATCH_LEASE_RECEIPT_NAME = "shadow-watch-lease-receipt.json"
SUPERVISOR_SOCKET = "/run/hepta-tool-gateway/session-supervisor.sock"
SESSIONCTL = "/usr/bin/hepta-sessionctl"
SYSTEMCTL = "/usr/bin/systemctl"
BROKER_EGRESS_POLICY = "/usr/libexec/hepta-broker-egress-policy"
PAPER_CAMPAIGN_ROOT = Path("/etc/heptatrader/paper-campaigns")
PAPER_CONTROL_ROOT = Path("/run/hepta")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MIN_TTL_SEC = 60
MAX_TTL_SEC = 86400
MAX_SHADOW_WATCH_TTL_SEC = 3600
TOKEN_BYTES = 48
ROOT_UID = 0
BOUNDARY_AUDIT_HELPER_MAX_BYTES = 16 * 1024 * 1024
BOUNDARY_AUDIT_MANAGED_PREFIXES = (
    ".session-token-provision-",
    ".session-token-rotate-",
    ".session-fence-provision-",
    ".session-fence-rotate-",
    ".watch-lease-receipt-",
)
WATCH_LEASE_RECEIPT_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_id", "agent_uid", "boundary",
    "operation", "lease_generation", "previous_lease_generation",
    "previous_receipt_body_sha256", "accepted", "reason_code",
    "accepted_at_ms", "ttl_seconds", "expires_at_ms", "paper_authorized",
    "live_authorized", "mutation_authorized", "body_sha256",
})


class BootstrapError(RuntimeError):
    pass


class SessionNotFoundError(BootstrapError):
    pass


def _fault(stage: str) -> None:
    """Test seam for deterministic local-commit failure injection.

    Production callers cannot enable this through the environment or CLI.
    Tests replace the function in-process so no deployable fault switch exists.
    """
    del stage


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BootstrapError(message)


def _canonical_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise BootstrapError("WATCH_LEASE_RECEIPT_CANONICALIZATION_FAILED") from error


def _document_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _epoch_ms() -> int:
    return time.time_ns() // 1_000_000


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in result, "WATCH_LEASE_RECEIPT_DUPLICATE_KEY")
        result[key] = value
    return result


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev and left.st_ino == right.st_ino and
        stat.S_ISREG(left.st_mode) and stat.S_ISREG(right.st_mode)
    )


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns",
    )
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def _metadata_at(
        directory_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _require_no_managed_token_residue(directory_fd: int) -> None:
    """Refuse a new transaction while a prior private pair is unreconciled."""
    try:
        names = os.listdir(directory_fd)
    except OSError as error:
        raise BootstrapError("SESSION_TOKEN_RESIDUE_SCAN_FAILED") from error
    _require(
        not any(
            name.startswith(".session-token-") or
            name.startswith(".session-fence-")
            for name in names
        ),
        "SESSION_TOKEN_RESIDUE_PRESENT",
    )


def _recover_uncertain_provision_residue(
        directory_fd: int, generation: int) -> int | None:
    """Fence and remove one exact private provision pair after a crash.

    An ambiguous supervisor result deliberately preserves a root-only fence
    and quarantines its delivery copy.  A later generation-bound revoke must
    be able to reconcile that pair; otherwise the fail-closed residue becomes
    a permanent host outage.  Only the initial provision generation and one
    same-process pair are accepted.  Rotation residue and every mixed or
    malformed layout still require separate review.
    """
    try:
        names = os.listdir(directory_fd)
    except OSError as error:
        raise BootstrapError("SESSION_TOKEN_RESIDUE_SCAN_FAILED") from error
    managed = sorted(
        name for name in names
        if name.startswith(".session-token-") or
        name.startswith(".session-fence-")
    )
    if not managed:
        return None

    if (
            _metadata_at(directory_fd, TOKEN_NAME) is not None or
            _metadata_at(directory_fd, FENCE_TOKEN_NAME) is not None):
        return None
    _require(
        generation == 1,
        "SESSION_PROVISION_RESIDUE_GENERATION_INVALID",
    )
    token_pattern = re.compile(
        r"^\.session-token-provision-([1-9][0-9]*)-[0-9a-f]{16}$")
    fence_pattern = re.compile(
        r"^\.session-fence-provision-([1-9][0-9]*)-[0-9a-f]{16}$")
    token_matches = [
        (name, token_pattern.fullmatch(name)) for name in managed]
    fence_matches = [
        (name, fence_pattern.fullmatch(name)) for name in managed]
    tokens = [
        (name, match) for name, match in token_matches if match is not None]
    fences = [
        (name, match) for name, match in fence_matches if match is not None]
    _require(
        len(managed) == 2 and len(tokens) == 1 and len(fences) == 1 and
        tokens[0][1].group(1) == fences[0][1].group(1),
        "SESSION_PROVISION_RESIDUE_LAYOUT_INVALID",
    )
    token_name = tokens[0][0]
    fence_name = fences[0][0]
    token_metadata = _metadata_at(directory_fd, token_name)
    fence_metadata = _metadata_at(directory_fd, fence_name)
    _require(
        token_metadata is not None and
        stat.S_ISREG(token_metadata.st_mode) and
        token_metadata.st_uid == os.geteuid() and
        token_metadata.st_gid == os.getegid() and
        token_metadata.st_nlink == 1 and
        stat.S_IMODE(token_metadata.st_mode) in {0o000, 0o600} and
        24 <= token_metadata.st_size <= 512,
        "SESSION_PROVISION_RESIDUE_TOKEN_UNSAFE",
    )
    _require(
        fence_metadata is not None and
        stat.S_ISREG(fence_metadata.st_mode) and
        fence_metadata.st_uid == os.geteuid() and
        fence_metadata.st_gid == os.getegid() and
        fence_metadata.st_nlink == 1 and
        stat.S_IMODE(fence_metadata.st_mode) == 0o600 and
        24 <= fence_metadata.st_size <= 512 and
        not _same_inode(token_metadata, fence_metadata),
        "SESSION_PROVISION_RESIDUE_FENCE_UNSAFE",
    )
    try:
        _revoke_exact_fence(
            directory_fd,
            generation,
            [fence_name],
            fence_metadata,
            allow_not_found=True,
        )
    except BaseException as revoke_error:
        try:
            _secure_unconfirmed_material(
                directory_fd,
                token_names=[token_name],
                token_metadata=token_metadata,
                fence_names=[fence_name],
                fence_metadata=fence_metadata,
            )
        except BaseException as safety_error:
            raise BootstrapError(
                "SESSION_PROVISION_RESIDUE_SAFETY_FAILED"
            ) from safety_error
        raise BootstrapError(
            "SESSION_PROVISION_RESIDUE_RECOVERY_REQUIRED"
        ) from revoke_error
    try:
        _cleanup_exact(directory_fd, [token_name], token_metadata)
        _cleanup_exact(directory_fd, [fence_name], fence_metadata)
        _remove_watch_lease_receipt(directory_fd)
        os.fsync(directory_fd)
    except BaseException as cleanup_error:
        try:
            _secure_compensation_material(
                directory_fd,
                token_names=[token_name],
                token_metadata=token_metadata,
                fence_names=[fence_name],
                fence_metadata=fence_metadata,
            )
        except BaseException as safety_error:
            raise BootstrapError(
                "SESSION_PROVISION_RESIDUE_LOCAL_SAFETY_FAILED_REVOKED"
            ) from safety_error
        raise BootstrapError(
            "SESSION_PROVISION_RESIDUE_LOCAL_CLEANUP_FAILED_REVOKED"
        ) from cleanup_error
    return generation


def _read_watch_lease_receipt(
        directory_fd: int,
) -> tuple[dict[str, object], bytes, os.stat_result]:
    flags = (
        os.O_RDONLY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0) |
        (getattr(os, "O_NOATIME", 0) if os.geteuid() == 0 else 0)
    )
    try:
        descriptor = os.open(
            WATCH_LEASE_RECEIPT_NAME, flags, dir_fd=directory_fd)
    except OSError as error:
        raise BootstrapError("WATCH_LEASE_RECEIPT_MISSING_OR_UNSAFE") from error
    try:
        before = os.fstat(descriptor)
        _require(
            stat.S_ISREG(before.st_mode) and
            before.st_uid == ROOT_UID and before.st_gid == AGENT_GID and
            before.st_nlink == 1 and
            stat.S_IMODE(before.st_mode) == 0o440 and
            2 <= before.st_size <= 65_536,
            "WATCH_LEASE_RECEIPT_METADATA_UNSAFE",
        )
        contents = bytearray()
        while len(contents) <= 65_536:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            contents.extend(chunk)
        after = os.fstat(descriptor)
        path_after = os.stat(
            WATCH_LEASE_RECEIPT_NAME,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        _require(
            len(contents) == before.st_size and
            _same_file(before, after) and _same_file(after, path_after),
            "WATCH_LEASE_RECEIPT_CHANGED",
        )
    finally:
        os.close(descriptor)
    try:
        document = json.loads(
            bytes(contents).decode("ascii", errors="strict"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BootstrapError("WATCH_LEASE_RECEIPT_JSON_INVALID") from error
    _require(
        isinstance(document, dict) and
        bytes(contents) == _canonical_bytes(document),
        "WATCH_LEASE_RECEIPT_NOT_CANONICAL",
    )
    return document, bytes(contents), after


def _validate_watch_lease_receipt(
        receipt: dict[str, object], *,
        expected_generation: int,
) -> None:
    _require(
        set(receipt) == set(WATCH_LEASE_RECEIPT_FIELDS) and
        receipt.get("schema") == "hepta.shadow-watch-lease-receipt.v1" and
        receipt.get("version") == 1,
        "WATCH_LEASE_RECEIPT_CONTRACT_INVALID",
    )
    generation = receipt.get("lease_generation")
    accepted_at_ms = receipt.get("accepted_at_ms")
    ttl_seconds = receipt.get("ttl_seconds")
    expires_at_ms = receipt.get("expires_at_ms")
    _require(
        receipt.get("domain_id") == DOMAIN_ID and
        receipt.get("agent_id") == DOMAIN_ID and
        receipt.get("agent_uid") == AGENT_UID and
        receipt.get("boundary") == "WATCH" and
        receipt.get("operation") in {"PROVISION", "ROTATE"} and
        isinstance(generation, int) and not isinstance(generation, bool) and
        generation == expected_generation and
        receipt.get("accepted") is True and
        receipt.get("reason_code") == "OK" and
        isinstance(accepted_at_ms, int) and
        not isinstance(accepted_at_ms, bool) and accepted_at_ms >= 0 and
        isinstance(ttl_seconds, int) and not isinstance(ttl_seconds, bool) and
        MIN_TTL_SEC <= ttl_seconds <= MAX_SHADOW_WATCH_TTL_SEC and
        isinstance(expires_at_ms, int) and
        not isinstance(expires_at_ms, bool) and
        expires_at_ms == accepted_at_ms + ttl_seconds * 1000 and
        receipt.get("paper_authorized") is False and
        receipt.get("live_authorized") is False and
        receipt.get("mutation_authorized") is False,
        "WATCH_LEASE_RECEIPT_BINDING_INVALID",
    )
    previous_generation = receipt.get("previous_lease_generation")
    previous_digest = receipt.get("previous_receipt_body_sha256")
    if receipt["operation"] == "PROVISION":
        _require(
            previous_generation is None and previous_digest is None,
            "WATCH_LEASE_RECEIPT_CHAIN_INVALID",
        )
    else:
        _require(
            isinstance(previous_generation, int) and
            not isinstance(previous_generation, bool) and
            previous_generation == generation - 1 and
            isinstance(previous_digest, str) and
            DIGEST.fullmatch(previous_digest) is not None,
            "WATCH_LEASE_RECEIPT_CHAIN_INVALID",
        )
    claimed_digest = receipt.get("body_sha256")
    body = dict(receipt)
    body.pop("body_sha256", None)
    _require(
        isinstance(claimed_digest, str) and
        DIGEST.fullmatch(claimed_digest) is not None and
        claimed_digest == _document_digest(body),
        "WATCH_LEASE_RECEIPT_DIGEST_INVALID",
    )


def _watch_lease_receipt_preflight(
        directory_fd: int, operation: str, generation: int | None = None,
) -> tuple[dict[str, object], bytes, os.stat_result] | None:
    if operation == "PROVISION":
        _require(
            _metadata_at(directory_fd, WATCH_LEASE_RECEIPT_NAME) is None,
            "WATCH_LEASE_RECEIPT_ALREADY_EXISTS",
        )
        return None
    _require(
        operation == "ROTATE" and generation is not None,
        "WATCH_LEASE_RECEIPT_OPERATION_INVALID",
    )
    prior = _read_watch_lease_receipt(directory_fd)
    _validate_watch_lease_receipt(
        prior[0], expected_generation=generation)
    return prior


def _write_all(descriptor: int, contents: bytes, reason: str) -> None:
    offset = 0
    while offset < len(contents):
        written = os.write(descriptor, contents[offset:])
        _require(written > 0, reason)
        offset += written


def _publish_watch_lease_receipt(
        directory_fd: int, *,
        operation: str,
        lease_generation: int,
        agent_id: str,
        ttl_seconds: int,
        accepted_at_ms: int,
        previous: tuple[
            dict[str, object], bytes, os.stat_result] | None,
) -> dict[str, object]:
    _require(
        operation in {"PROVISION", "ROTATE"} and
        agent_id == DOMAIN_ID and
        1 <= lease_generation <= (1 << 64) - 1 and
        MIN_TTL_SEC <= ttl_seconds <= MAX_SHADOW_WATCH_TTL_SEC and
        isinstance(accepted_at_ms, int) and accepted_at_ms >= 0,
        "WATCH_LEASE_RECEIPT_INPUT_INVALID",
    )
    if operation == "PROVISION":
        _require(previous is None, "WATCH_LEASE_RECEIPT_CHAIN_INVALID")
        previous_generation = None
        previous_digest = None
    else:
        _require(previous is not None, "WATCH_LEASE_RECEIPT_CHAIN_INVALID")
        _validate_watch_lease_receipt(
            previous[0], expected_generation=lease_generation - 1)
        previous_generation = lease_generation - 1
        previous_digest = previous[0]["body_sha256"]
    body: dict[str, object] = {
        "schema": "hepta.shadow-watch-lease-receipt.v1",
        "version": 1,
        "domain_id": DOMAIN_ID,
        "agent_id": agent_id,
        "agent_uid": AGENT_UID,
        "boundary": "WATCH",
        "operation": operation,
        "lease_generation": lease_generation,
        "previous_lease_generation": previous_generation,
        "previous_receipt_body_sha256": previous_digest,
        "accepted": True,
        "reason_code": "OK",
        "accepted_at_ms": accepted_at_ms,
        "ttl_seconds": ttl_seconds,
        "expires_at_ms": accepted_at_ms + ttl_seconds * 1000,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
    }
    receipt = {**body, "body_sha256": _document_digest(body)}
    contents = _canonical_bytes(receipt)
    temporary = (
        f".watch-lease-receipt-{os.getpid()}-{secrets.token_hex(8)}")
    descriptor = -1
    metadata: os.stat_result | None = None
    try:
        _require(
            os.geteuid() == ROOT_UID,
            "WATCH_LEASE_RECEIPT_ROOT_REQUIRED",
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            0o440,
            dir_fd=directory_fd,
        )
        os.fchown(descriptor, ROOT_UID, AGENT_GID)
        os.fchmod(descriptor, 0o440)
        _write_all(
            descriptor, contents, "WATCH_LEASE_RECEIPT_SHORT_WRITE")
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and
            metadata.st_uid == ROOT_UID and metadata.st_gid == AGENT_GID and
            metadata.st_nlink == 1 and
            stat.S_IMODE(metadata.st_mode) == 0o440 and
            metadata.st_size == len(contents),
            "WATCH_LEASE_RECEIPT_TEMPORARY_UNSAFE",
        )
        _fault("receipt.before_publish")
        if operation == "PROVISION":
            os.link(
                temporary,
                WATCH_LEASE_RECEIPT_NAME,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            _require(
                _unlink_exact(directory_fd, temporary, metadata),
                "WATCH_LEASE_RECEIPT_TEMPORARY_CHANGED",
            )
        else:
            assert previous is not None
            current = _metadata_at(directory_fd, WATCH_LEASE_RECEIPT_NAME)
            _require(
                current is not None and _same_file(current, previous[2]),
                "WATCH_LEASE_RECEIPT_CHANGED",
            )
            os.replace(
                temporary,
                WATCH_LEASE_RECEIPT_NAME,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        _fault("receipt.after_publish")
        os.fsync(directory_fd)
        _fault("receipt.after_publish_fsync")
        published = os.stat(
            WATCH_LEASE_RECEIPT_NAME,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        _require(
            metadata is not None and _same_inode(metadata, published) and
            published.st_uid == ROOT_UID and
            published.st_gid == AGENT_GID and published.st_nlink == 1 and
            stat.S_IMODE(published.st_mode) == 0o440 and
            published.st_size == len(contents),
            "WATCH_LEASE_RECEIPT_PUBLISH_UNSAFE",
        )
        return receipt
    except FileExistsError as error:
        raise BootstrapError(
            "WATCH_LEASE_RECEIPT_ALREADY_EXISTS") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if metadata is not None:
            try:
                _unlink_exact(directory_fd, temporary, metadata)
            except OSError:
                pass


def _remove_watch_lease_receipt(directory_fd: int) -> None:
    metadata = _metadata_at(directory_fd, WATCH_LEASE_RECEIPT_NAME)
    if metadata is None:
        return
    _require(
        stat.S_ISREG(metadata.st_mode) and
        metadata.st_uid == ROOT_UID and metadata.st_gid == AGENT_GID and
        metadata.st_nlink == 1 and
        stat.S_IMODE(metadata.st_mode) == 0o440,
        "WATCH_LEASE_RECEIPT_METADATA_UNSAFE",
    )
    _require(
        _unlink_exact(
            directory_fd, WATCH_LEASE_RECEIPT_NAME, metadata),
        "WATCH_LEASE_RECEIPT_CHANGED",
    )
    os.fsync(directory_fd)


def _unlink_exact(
        directory_fd: int, name: str, expected: os.stat_result) -> bool:
    """Unlink only a regular file with the exact inode we created/validated."""
    current = _metadata_at(directory_fd, name)
    if current is None:
        return False
    if not _same_inode(current, expected):
        return False
    os.unlink(name, dir_fd=directory_fd)
    return True


def _unlink_unchanged(
        directory_fd: int, name: str, expected: os.stat_result) -> bool:
    """Unlink a pre-existing file only if its complete metadata is unchanged."""
    current = _metadata_at(directory_fd, name)
    if current is None or not _same_file(current, expected):
        return False
    os.unlink(name, dir_fd=directory_fd)
    return True


def _exact_token_path(
        directory_fd: int, names: list[str],
        expected: os.stat_result) -> str | None:
    for name in names:
        current = _metadata_at(directory_fd, name)
        if current is not None and _same_inode(current, expected):
            return name
    return None


def _quarantine_exact(
        directory_fd: int, names: list[str],
        expected: os.stat_result) -> None:
    """Make an unrevoked token inaccessible to the Agent, without unlinking it."""
    handled: set[tuple[int, int]] = set()
    quarantine_uid = 0 if os.geteuid() == 0 else os.geteuid()
    quarantine_gid = 0 if os.getegid() == 0 else os.getegid()
    for name in names:
        current = _metadata_at(directory_fd, name)
        if current is None or not _same_inode(current, expected):
            continue
        identity = (current.st_dev, current.st_ino)
        if identity in handled:
            continue
        handled.add(identity)
        if (
                current.st_uid == quarantine_uid and
                current.st_gid == quarantine_gid and
                stat.S_IMODE(current.st_mode) == 0):
            # A prior fallback already established the exact quarantine state.
            # This also keeps rootless lifecycle tests honest without requiring
            # them to reopen a mode-000 file.
            continue
        _fault("quarantine.before_open")
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            opened = os.fstat(descriptor)
            if not _same_inode(opened, expected):
                continue
            _fault("quarantine.before_chown")
            os.fchown(descriptor, quarantine_uid, quarantine_gid)
            _fault("quarantine.before_chmod")
            os.fchmod(descriptor, 0o000)
            _fault("quarantine.before_fsync")
            os.fsync(descriptor)
            verified = os.fstat(descriptor)
            _require(
                _same_inode(verified, expected) and
                verified.st_uid == quarantine_uid and
                verified.st_gid == quarantine_gid and
                stat.S_IMODE(verified.st_mode) == 0,
                "SESSION_TOKEN_QUARANTINE_VERIFY_FAILED",
            )
        finally:
            os.close(descriptor)


def _cleanup_exact(
        directory_fd: int, names: list[str],
        expected: os.stat_result) -> None:
    for name in names:
        _unlink_exact(directory_fd, name, expected)


def _force_runtime_quarantine(directory_fd: int) -> None:
    """Deny all path traversal if exact-token quarantine and unlink both fail."""
    quarantine_uid = 0 if os.geteuid() == 0 else os.geteuid()
    quarantine_gid = 0 if os.getegid() == 0 else os.getegid()
    os.fchown(directory_fd, quarantine_uid, quarantine_gid)
    os.fchmod(directory_fd, 0o000)
    os.fsync(directory_fd)
    verified = os.fstat(directory_fd)
    _require(
        stat.S_ISDIR(verified.st_mode) and
        verified.st_uid == quarantine_uid and
        verified.st_gid == quarantine_gid and
        stat.S_IMODE(verified.st_mode) == 0,
        "SESSION_RUNTIME_QUARANTINE_VERIFY_FAILED",
    )


def _secure_unrevoked_token(
        directory_fd: int, names: list[str],
        expected: os.stat_result) -> None:
    """Quarantine, exact-unlink, or finally seal the runtime directory."""
    try:
        _quarantine_exact(directory_fd, names, expected)
        return
    except BaseException:
        try:
            _cleanup_exact(directory_fd, names, expected)
            os.fsync(directory_fd)
            _require(
                _exact_token_path(directory_fd, names, expected) is None,
                "SESSION_TOKEN_EXACT_UNLINK_VERIFY_FAILED",
            )
            return
        except BaseException:
            try:
                _force_runtime_quarantine(directory_fd)
                return
            except BaseException as runtime_error:
                raise BootstrapError(
                    "SESSION_TOKEN_SAFETY_FALLBACK_FAILED") from runtime_error


def _preserve_recovery_fence(
        directory_fd: int, names: list[str],
        expected: os.stat_result) -> None:
    """Keep the exact root recovery bearer; this path must never unlink it."""
    try:
        name = _exact_token_path(directory_fd, names, expected)
        _require(name is not None, "SESSION_FENCE_RECOVERY_BEARER_MISSING")
        current = _metadata_at(directory_fd, name)
        _require(
            current is not None and _same_inode(current, expected) and
            current.st_uid == os.geteuid() and
            current.st_gid == os.getegid() and current.st_nlink == 1 and
            stat.S_IMODE(current.st_mode) in {0o000, 0o600} and
            24 <= current.st_size <= 512,
            "SESSION_FENCE_RECOVERY_BEARER_UNSAFE",
        )
        return
    except BaseException as fence_error:
        # Never trade the only authoritative recovery bearer for local
        # tidiness. Seal the containing directory, but leave the exact inode
        # in place for a root operator to retry the generation-bound revoke.
        try:
            _force_runtime_quarantine(directory_fd)
        except BaseException as runtime_error:
            raise BootstrapError(
                "SESSION_FENCE_RECOVERY_PRESERVATION_FAILED"
            ) from runtime_error
        try:
            _require(
                _exact_token_path(directory_fd, names, expected) is not None,
                "SESSION_FENCE_RECOVERY_BEARER_MISSING",
            )
        except BaseException as missing_error:
            raise BootstrapError(
                "SESSION_FENCE_RECOVERY_PRESERVATION_FAILED"
            ) from missing_error
        raise BootstrapError(
            "SESSION_FENCE_RECOVERY_BEARER_UNSAFE") from fence_error


def _secure_unconfirmed_material(
        directory_fd: int, *,
        token_names: list[str],
        token_metadata: os.stat_result | None,
        fence_names: list[str],
        fence_metadata: os.stat_result,
) -> None:
    """Deny Agent access while retaining a root-only revoke bearer."""
    if token_metadata is not None:
        _require(
            not _same_inode(token_metadata, fence_metadata),
            "SESSION_FENCE_TOKEN_INODE_REUSED",
        )
    _preserve_recovery_fence(
        directory_fd, fence_names, fence_metadata)
    if token_metadata is None:
        _force_runtime_quarantine(directory_fd)
    else:
        _secure_unrevoked_token(
            directory_fd, token_names, token_metadata)
    _preserve_recovery_fence(
        directory_fd, fence_names, fence_metadata)


def _revoke_exact_fence(
        directory_fd: int, generation: int,
        fence_names: list[str], fence_metadata: os.stat_result, *,
        allow_not_found: bool = False,
) -> bool:
    """Revoke with the immutable root-controlled copy of the accepted bearer."""
    fence_name = _exact_token_path(
        directory_fd, fence_names, fence_metadata)
    _require(fence_name is not None, "SESSION_FENCE_TOKEN_MISSING")
    try:
        result = _sessionctl([
            "revoke", "--token-file", _token_path(fence_name),
            "--token-owner-uid", str(fence_metadata.st_uid),
            "--generation", str(generation),
        ])
    except SessionNotFoundError:
        if allow_not_found:
            return False
        raise
    _require(
        int(result["lease_generation"]) == generation,
        "SESSION_LEASE_COMPENSATION_GENERATION_MISMATCH",
    )
    return True


def _secure_compensation_material(
        directory_fd: int, *,
        token_names: list[str],
        token_metadata: os.stat_result | None,
        fence_names: list[str],
        fence_metadata: os.stat_result,
) -> None:
    """Remove Agent access while preserving no usable local bearer."""
    if token_metadata is None:
        _force_runtime_quarantine(directory_fd)
    else:
        _secure_unrevoked_token(
            directory_fd, token_names, token_metadata)
    _secure_unrevoked_token(
        directory_fd, fence_names, fence_metadata)


def _compensate_accepted_lease(
        directory_fd: int, operation: str, generation: int,
        token_names: list[str], token_metadata: os.stat_result | None,
        fence_names: list[str], fence_metadata: os.stat_result,
        stale_metadata: os.stat_result | None = None,
        stale_fence_metadata: os.stat_result | None = None) -> None:
    """Fence an accepted generation using a separate root-owned bearer."""
    compensation_failed = False
    try:
        _revoke_exact_fence(
            directory_fd, generation, fence_names, fence_metadata)
    except BaseException:
        compensation_failed = True

    if compensation_failed:
        # The supervisor could not confirm the revoke. Keep recovery material
        # for a root operator, but remove Agent read permission fail-closed.
        try:
            _secure_unconfirmed_material(
                directory_fd,
                token_names=token_names,
                token_metadata=token_metadata,
                fence_names=fence_names,
                fence_metadata=fence_metadata,
            )
        except BaseException as error:
            raise BootstrapError(
                f"SESSION_{operation}_COMPENSATION_SAFETY_FAILED") from error
        if stale_metadata is not None:
            try:
                _unlink_unchanged(directory_fd, TOKEN_NAME, stale_metadata)
                os.fsync(directory_fd)
            except BaseException:
                pass
        raise BootstrapError(
            f"SESSION_{operation}_COMPENSATION_FAILED")

    try:
        if token_metadata is not None:
            _cleanup_exact(directory_fd, token_names, token_metadata)
        _cleanup_exact(directory_fd, fence_names, fence_metadata)
        if stale_metadata is not None:
            _unlink_unchanged(directory_fd, TOKEN_NAME, stale_metadata)
        if stale_fence_metadata is not None:
            _unlink_unchanged(
                directory_fd, FENCE_TOKEN_NAME, stale_fence_metadata)
    except BaseException:
        # The authoritative revoke already won. An exact residual token is
        # unusable after the fence, but quarantine it to keep disk fail-closed.
        try:
            _secure_compensation_material(
                directory_fd,
                token_names=token_names,
                token_metadata=token_metadata,
                fence_names=fence_names,
                fence_metadata=fence_metadata,
            )
        except BaseException as error:
            raise BootstrapError(
                f"SESSION_{operation}_LOCAL_CLEANUP_SAFETY_FAILED_REVOKED"
            ) from error
        raise BootstrapError(
            f"SESSION_{operation}_LOCAL_CLEANUP_FAILED_REVOKED")
    try:
        os.fsync(directory_fd)
    except OSError:
        # The authoritative lease is already revoked. A later boot cannot
        # resurrect it; failure here must not obscure the confirmed fence.
        pass
    raise BootstrapError(
        f"SESSION_{operation}_LOCAL_COMMIT_FAILED_REVOKED")


def _resolve_uncertain_acceptance(
        directory_fd: int, operation: str, candidate_generation: int,
        token_names: list[str], token_metadata: os.stat_result,
        fence_names: list[str], fence_metadata: os.stat_result,
) -> bool:
    """Reconcile an ambiguous supervisor result without assuming no commit."""
    try:
        revoked = _revoke_exact_fence(
            directory_fd,
            candidate_generation,
            fence_names,
            fence_metadata,
            allow_not_found=True,
        )
    except BaseException as error:
        try:
            _secure_unconfirmed_material(
                directory_fd,
                token_names=token_names,
                token_metadata=token_metadata,
                fence_names=fence_names,
                fence_metadata=fence_metadata,
            )
        except BaseException as safety_error:
            raise BootstrapError(
                f"SESSION_{operation}_UNCERTAIN_SAFETY_FAILED"
            ) from safety_error
        raise BootstrapError(
            f"SESSION_{operation}_UNCERTAIN_RECOVERY_REQUIRED") from error
    try:
        _cleanup_exact(directory_fd, token_names, token_metadata)
        _cleanup_exact(directory_fd, fence_names, fence_metadata)
        os.fsync(directory_fd)
    except BaseException as error:
        raise BootstrapError(
            f"SESSION_{operation}_UNCERTAIN_LOCAL_CLEANUP_FAILED"
        ) from error
    return revoked


def _publish_watch_lease_receipt_or_compensate(
        directory_fd: int, *,
        operation: str,
        generation: int,
        agent_id: str,
        ttl_seconds: int,
        accepted_at_ms: int,
        previous: tuple[
            dict[str, object], bytes, os.stat_result] | None,
) -> dict[str, object]:
    try:
        return _publish_watch_lease_receipt(
            directory_fd,
            operation=operation,
            lease_generation=generation,
            agent_id=agent_id,
            ttl_seconds=ttl_seconds,
            accepted_at_ms=accepted_at_ms,
            previous=previous,
        )
    except BaseException as publication_error:
        # The exact generation is known accepted and the bearer is committed.
        # Fence it authoritatively; if that cannot be confirmed, remove all
        # Agent access to the exact bearer (or finally the whole runtime tree).
        try:
            _remove_watch_lease_receipt(directory_fd)
        except BaseException:
            # A receipt is evidence, never authority.  Do not let an unsafe or
            # concurrently replaced evidence path prevent bearer fencing.
            pass
        try:
            fence_metadata = _fence_token_metadata(directory_fd)
        except BaseException as fence_error:
            try:
                _force_runtime_quarantine(directory_fd)
            except BaseException as quarantine_error:
                raise BootstrapError(
                    f"SESSION_{operation}_RECEIPT_SAFETY_FAILED"
                ) from quarantine_error
            raise BootstrapError(
                f"SESSION_{operation}_RECEIPT_RECOVERY_REQUIRED"
            ) from fence_error
        try:
            token_metadata = _token_metadata(
                directory_fd, TOKEN_NAME, owner_uid=AGENT_UID)
        except BaseException:
            token_metadata = None
        try:
            _compensate_accepted_lease(
                directory_fd,
                f"{operation}_RECEIPT",
                generation,
                [TOKEN_NAME],
                token_metadata,
                [FENCE_TOKEN_NAME],
                fence_metadata,
            )
        except BootstrapError as compensation_error:
            raise compensation_error from publication_error
        raise AssertionError("lease receipt compensation unexpectedly returned")


def _validate_identity(
        name: str, uid: int, gid: int,
        supplementary_groups: tuple[tuple[str, int], ...] = ()) -> None:
    try:
        passwd = pwd.getpwnam(name)
        group = grp.getgrnam(name)
        supplementary = tuple(
            (group_name, grp.getgrnam(group_name).gr_gid)
            for group_name, _group_gid in supplementary_groups)
    except KeyError as error:
        raise BootstrapError("FIXED_IDENTITY_MISSING") from error
    _require(
        supplementary == supplementary_groups,
        "FIXED_IDENTITY_SUPPLEMENTARY_GROUP_MISMATCH",
    )
    _require(
        passwd.pw_uid == uid and passwd.pw_gid == gid and
        group.gr_gid == gid and passwd.pw_name == name and group.gr_name == name,
        "FIXED_IDENTITY_MISMATCH",
    )
    _require(
        sorted(member for member in group.gr_mem if member != name) == [],
        "FIXED_IDENTITY_GROUP_MEMBERSHIP_UNSAFE",
    )
    _require(
        sorted(set(os.getgrouplist(name, gid))) ==
        sorted({gid, *(item[1] for item in supplementary_groups)}),
        "FIXED_IDENTITY_SUPPLEMENTARY_GROUP_UNSAFE",
    )


def _configure_domain(domain: dict[str, object]) -> None:
    global DOMAIN_ID, AGENT_NAME, AGENT_UID, AGENT_GID
    global GATEWAY_NAME, GATEWAY_UID, GATEWAY_GID
    global GATEWAY_SUPPLEMENTARY_GROUPS
    global RUNTIME_PARENT, SUPERVISOR_SOCKET
    DOMAIN_ID = str(domain["domain_id"])
    AGENT_NAME = str(domain["agent_name"])
    AGENT_UID = int(domain["agent_uid"])
    AGENT_GID = int(domain["agent_gid"])
    GATEWAY_NAME = str(domain["gateway_name"])
    GATEWAY_UID = int(domain["gateway_uid"])
    GATEWAY_GID = int(domain["gateway_gid"])
    GATEWAY_SUPPLEMENTARY_GROUPS = ()
    RUNTIME_PARENT = Path(str(domain["token_directory"]))
    SUPERVISOR_SOCKET = str(domain["supervisor_socket"])


def _validate_sessionctl() -> None:
    try:
        before = os.lstat(SESSIONCTL)
    except OSError as error:
        raise BootstrapError("SESSIONCTL_MISSING") from error
    _require(
        stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode) and
        before.st_uid == 0 and before.st_gid == 0 and before.st_nlink == 1 and
        stat.S_IMODE(before.st_mode) == 0o755,
        "SESSIONCTL_METADATA_UNSAFE",
    )
    descriptor = os.open(
        SESSIONCTL, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        _require(_same_file(before, opened), "SESSIONCTL_CHANGED")
    finally:
        os.close(descriptor)


def _runtime_directory() -> int:
    try:
        relative = RUNTIME_PARENT.relative_to("/run")
    except ValueError as error:
        raise BootstrapError("AGENT_RUNTIME_DIRECTORY_INVALID") from error
    _require(
        relative.parts and all(
            part not in {"", ".", ".."} for part in relative.parts),
        "AGENT_RUNTIME_DIRECTORY_INVALID",
    )
    run_fd = os.open(
        "/run", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0))
    try:
        current_fd = run_fd
        for index, part in enumerate(relative.parts):
            created = False
            try:
                os.mkdir(part, 0o711, dir_fd=current_fd)
                created = True
            except FileExistsError:
                pass
            descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
                getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current_fd,
            )
            if current_fd != run_fd:
                os.close(current_fd)
            current_fd = descriptor
            if created:
                os.fchmod(current_fd, 0o711)
            metadata = os.fstat(current_fd)
            if not (
                    stat.S_ISDIR(metadata.st_mode) and
                    metadata.st_uid == 0 and metadata.st_gid == 0 and
                    stat.S_IMODE(metadata.st_mode) == 0o711):
                raise BootstrapError("AGENT_RUNTIME_DIRECTORY_UNSAFE")
        return current_fd
    except BaseException:
        if "current_fd" in locals() and current_fd != run_fd:
            os.close(current_fd)
        raise
    finally:
        os.close(run_fd)


def _lock(directory_fd: int) -> int:
    flags = (os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
             getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(LOCK_NAME, flags, 0o600, dir_fd=directory_fd)
    metadata = os.fstat(descriptor)
    _require(
        stat.S_ISREG(metadata.st_mode) and
        metadata.st_uid == os.geteuid() and
        metadata.st_gid == os.getegid() and metadata.st_nlink == 1 and
        stat.S_IMODE(metadata.st_mode) == 0o600,
        "BOOTSTRAP_LOCK_UNSAFE",
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise BootstrapError("BOOTSTRAP_ALREADY_RUNNING") from error
    return descriptor


def _token_metadata(
        directory_fd: int, name: str, *, owner_uid: int) -> os.stat_result:
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as error:
        raise BootstrapError("SESSION_TOKEN_MISSING_OR_UNSAFE") from error
    _require(
        stat.S_ISREG(before.st_mode) and before.st_uid == owner_uid and
        before.st_gid == AGENT_GID and before.st_nlink == 1 and
        stat.S_IMODE(before.st_mode) == 0o600 and
        24 <= before.st_size <= 512,
        "SESSION_TOKEN_METADATA_UNSAFE",
    )
    descriptor = os.open(
        name, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        _require(_same_file(before, opened), "SESSION_TOKEN_CHANGED")
    finally:
        os.close(descriptor)
    return before


def _quarantined_token_metadata(
        directory_fd: int, name: str = TOKEN_NAME) -> os.stat_result:
    """Validate the fixed root-only bearer retained after uncertain revoke."""
    metadata = _metadata_at(directory_fd, name)
    _require(
        metadata is not None and stat.S_ISREG(metadata.st_mode) and
        metadata.st_uid == os.geteuid() and
        metadata.st_gid == os.getegid() and metadata.st_nlink == 1 and
        stat.S_IMODE(metadata.st_mode) == 0o000 and
        24 <= metadata.st_size <= 512,
        "SESSION_QUARANTINED_TOKEN_METADATA_UNSAFE",
    )
    return metadata


def _create_private_token_file(
        directory_fd: int, name: str, token: bytes) -> tuple[int, os.stat_result]:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(token):
            written = os.write(descriptor, token[offset:])
            _require(written > 0, "SESSION_TOKEN_SHORT_WRITE")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and
            metadata.st_uid == os.geteuid() and
            metadata.st_gid == os.getegid() and metadata.st_nlink == 1 and
            stat.S_IMODE(metadata.st_mode) == 0o600 and
            metadata.st_size == len(token),
            "TEMPORARY_TOKEN_UNSAFE",
        )
        return descriptor, metadata
    except BaseException:
        try:
            metadata = os.fstat(descriptor)
        except OSError:
            metadata = None
        os.close(descriptor)
        if metadata is not None:
            try:
                _unlink_exact(directory_fd, name, metadata)
            except OSError:
                pass
        raise


def _create_token_pair(
        directory_fd: int, purpose: str,
) -> tuple[str, int, os.stat_result, str, os.stat_result]:
    """Create separate delivery and root-fence inodes with one bearer."""
    token = (secrets.token_hex(TOKEN_BYTES) + "\n").encode("ascii")
    for _attempt in range(32):
        name = f".session-token-{purpose}-{os.getpid()}-{secrets.token_hex(8)}"
        try:
            descriptor, metadata = _create_private_token_file(
                directory_fd, name, token)
            break
        except FileExistsError:
            continue
    else:
        raise BootstrapError("TEMPORARY_TOKEN_NAME_EXHAUSTED")

    fence_name = (
        f".session-fence-{purpose}-{os.getpid()}-{secrets.token_hex(8)}")
    fence_metadata: os.stat_result | None = None
    try:
        fence_descriptor, fence_metadata = _create_private_token_file(
            directory_fd, fence_name, token)
        os.close(fence_descriptor)
        _require(
            not _same_inode(metadata, fence_metadata),
            "SESSION_FENCE_TOKEN_INODE_REUSED",
        )
        # Both names must be durable within this boot before the supervisor can
        # accept either bearer. Gateway restart fences active WATCH records;
        # host reboot clears /run and therefore relies on that non-restoration
        # rule rather than pretending the runtime fence itself is persistent.
        os.fsync(directory_fd)
    except BaseException:
        os.close(descriptor)
        try:
            _unlink_exact(directory_fd, name, metadata)
        except OSError:
            pass
        if fence_metadata is not None:
            try:
                _unlink_exact(directory_fd, fence_name, fence_metadata)
            except OSError:
                pass
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        raise
    assert fence_metadata is not None
    return name, descriptor, metadata, fence_name, fence_metadata


def _fence_token_metadata(
        directory_fd: int, name: str = FENCE_TOKEN_NAME,
) -> os.stat_result:
    metadata = _metadata_at(directory_fd, name)
    _require(
        metadata is not None and stat.S_ISREG(metadata.st_mode) and
        metadata.st_uid == os.geteuid() and
        metadata.st_gid == os.getegid() and metadata.st_nlink == 1 and
        stat.S_IMODE(metadata.st_mode) == 0o600 and
        24 <= metadata.st_size <= 512,
        "SESSION_FENCE_TOKEN_METADATA_UNSAFE",
    )
    assert metadata is not None
    try:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd)
    except OSError as error:
        raise BootstrapError("SESSION_FENCE_TOKEN_CHANGED") from error
    try:
        try:
            opened = os.fstat(descriptor)
            published = os.stat(
                name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as error:
            raise BootstrapError("SESSION_FENCE_TOKEN_CHANGED") from error
        _require(
            _same_file(metadata, opened) and
            _same_file(opened, published),
            "SESSION_FENCE_TOKEN_CHANGED",
        )
    finally:
        os.close(descriptor)
    return metadata


def _sessionctl(arguments: list[str]) -> dict[str, object]:
    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    completed = subprocess.run(
        [SESSIONCTL, "--socket", SUPERVISOR_SOCKET, *arguments],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=environment,
        close_fds=True,
        timeout=15,
    )
    _require(
        len(completed.stdout) <= 4096 and len(completed.stderr) <= 4096,
        "SESSION_SUPERVISOR_REJECTED",
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise BootstrapError("SESSIONCTL_OUTPUT_INVALID") from error
    if (
            completed.returncode == 4 and isinstance(result, dict) and
            set(result) == {"accepted", "reason_code", "lease_generation"} and
            result.get("accepted") is False and
            result.get("reason_code") in {
                "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND"} and
            isinstance(result.get("lease_generation"), int) and
            not isinstance(result.get("lease_generation"), bool) and
            result["lease_generation"] >= 0):
        raise SessionNotFoundError(str(result["reason_code"]))
    _require(
        completed.returncode == 0 and
        isinstance(result, dict) and set(result) == {
            "accepted", "reason_code", "lease_generation"} and
        result.get("accepted") is True and result.get("reason_code") == "OK" and
        isinstance(result.get("lease_generation"), int) and
        not isinstance(result.get("lease_generation"), bool) and
        result["lease_generation"] >= 1,
        "SESSIONCTL_RESULT_INVALID",
    )
    return result


def _token_path(name: str) -> str:
    return str(RUNTIME_PARENT / name)


def _stage_private_token(descriptor: int) -> None:
    """Verify the accepted bearer remains bootstrap-owned before publication."""
    metadata = os.fstat(descriptor)
    _require(
        stat.S_ISREG(metadata.st_mode) and
        metadata.st_uid == os.geteuid() and
        metadata.st_gid == os.getegid() and metadata.st_nlink == 1 and
        stat.S_IMODE(metadata.st_mode) == 0o600,
        "SESSION_TOKEN_PRIVATE_STAGE_UNSAFE",
    )
    os.fsync(descriptor)
    staged = os.fstat(descriptor)
    _require(
        _same_file(metadata, staged) and
        staged.st_uid == os.geteuid() and staged.st_gid == os.getegid() and
        staged.st_nlink == 1 and stat.S_IMODE(staged.st_mode) == 0o600,
        "SESSION_TOKEN_PRIVATE_STAGE_VERIFY_FAILED",
    )


def _agent_commit_visible(
        directory_fd: int, descriptor: int,
        expected: os.stat_result) -> bool:
    """Return true only when the exact fixed-path inode is Agent-readable."""
    try:
        opened = os.fstat(descriptor)
        published = os.stat(
            TOKEN_NAME, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return False
    return (
        _same_inode(opened, expected) and
        _same_inode(published, expected) and
        opened.st_uid == AGENT_UID and opened.st_gid == AGENT_GID and
        published.st_uid == AGENT_UID and published.st_gid == AGENT_GID and
        opened.st_nlink == 1 and published.st_nlink == 1 and
        stat.S_IMODE(opened.st_mode) == 0o600 and
        stat.S_IMODE(published.st_mode) == 0o600
    )


def _agent_commit_private(
        directory_fd: int, descriptor: int,
        expected: os.stat_result) -> bool:
    """Prove the exact fixed-path bearer is still root-bootstrap private."""
    try:
        metadata = os.fstat(descriptor)
        published = os.stat(
            TOKEN_NAME, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return False
    return (
        _same_inode(metadata, expected) and
        _same_inode(published, expected) and
        metadata.st_uid == os.geteuid() and
        metadata.st_gid == os.getegid() and metadata.st_nlink == 1 and
        published.st_uid == os.geteuid() and
        published.st_gid == os.getegid() and published.st_nlink == 1 and
        stat.S_IMODE(metadata.st_mode) == 0o600 and
        stat.S_IMODE(published.st_mode) == 0o600
    )


def _commit_agent_ownership(
        directory_fd: int, descriptor: int,
        expected: os.stat_result) -> bool:
    """Atomically grant Agent readability, or prove the grant did not occur."""
    # This check occurs before the only ownership transition. A failed or
    # unavailable private-state proof is therefore a compensable pre-commit
    # result, not commit-uncertain. The caller must revoke the accepted lease.
    if not _agent_commit_private(directory_fd, descriptor, expected):
        return False
    try:
        os.fchown(descriptor, AGENT_UID, AGENT_GID)
    except BaseException as error:
        # The ownership transition itself is the only readability
        # linearization point. If neither postcondition can be proven, the
        # caller must fence the exact generation authoritatively. Merely
        # taking back the local bearer is insufficient because the Agent may
        # already have copied it.
        if _agent_commit_visible(directory_fd, descriptor, expected):
            return True
        if _agent_commit_private(directory_fd, descriptor, expected):
            return False
        raise BootstrapError(
            "SESSION_TOKEN_AGENT_COMMIT_UNCERTAIN") from error
    return True


def _commit_agent_ownership_or_compensate(
        directory_fd: int, descriptor: int,
        expected: os.stat_result, *,
        operation: str,
        generation: int,
        token_names: list[str],
        fence_names: list[str],
        fence_metadata: os.stat_result,
        stale_metadata: os.stat_result | None = None,
        stale_fence_metadata: os.stat_result | None = None) -> None:
    """Commit Agent ownership or revoke the exact accepted generation."""
    try:
        committed = _commit_agent_ownership(
            directory_fd, descriptor, expected)
    except BaseException as commit_error:
        # fchown(2) may have taken effect even when it reports an error. The
        # independent root fence is unaffected by Agent ownership, content,
        # mode, path, or fstat uncertainty and remains authoritative.
        try:
            _compensate_accepted_lease(
                directory_fd,
                f"{operation}_AGENT_COMMIT_UNCERTAIN",
                generation,
                token_names,
                expected,
                fence_names,
                fence_metadata,
                stale_metadata=stale_metadata,
                stale_fence_metadata=stale_fence_metadata,
            )
        except BootstrapError as compensation_error:
            raise compensation_error from commit_error
        raise AssertionError(
            "agent ownership uncertainty compensation unexpectedly returned")
    if not committed:
        _compensate_accepted_lease(
            directory_fd,
            operation,
            generation,
            token_names,
            expected,
            fence_names,
            fence_metadata,
            stale_metadata=stale_metadata,
            stale_fence_metadata=stale_fence_metadata,
        )


def provision_watch(
        directory_fd: int, agent_id: str, session_id: str,
        ttl_sec: int) -> int:
    if _metadata_at(directory_fd, TOKEN_NAME) is not None:
        raise BootstrapError("SESSION_TOKEN_ALREADY_EXISTS")
    if _metadata_at(directory_fd, FENCE_TOKEN_NAME) is not None:
        raise BootstrapError("SESSION_FENCE_TOKEN_ALREADY_EXISTS")
    _require_no_managed_token_residue(directory_fd)

    (
        temporary,
        descriptor,
        temporary_metadata,
        fence_temporary,
        fence_metadata,
    ) = _create_token_pair(directory_fd, "provision")
    generation = 0
    try:
        try:
            result = _sessionctl([
                "provision", "--template", "watch",
                "--token-file", _token_path(fence_temporary),
                "--agent-id", agent_id, "--session-id", session_id,
                "--peer-uid", str(AGENT_UID), "--ttl-sec", str(ttl_sec),
            ])
        except BaseException as command_error:
            try:
                revoked = _resolve_uncertain_acceptance(
                    directory_fd,
                    "PROVISION",
                    1,
                    [temporary, TOKEN_NAME],
                    temporary_metadata,
                    [fence_temporary, FENCE_TOKEN_NAME],
                    fence_metadata,
                )
            except BootstrapError as recovery_error:
                raise recovery_error from command_error
            reason = (
                "SESSION_PROVISION_RESULT_UNCERTAIN_REVOKED"
                if revoked else "SESSION_PROVISION_NOT_COMMITTED")
            raise BootstrapError(reason) from command_error
        generation = int(result["lease_generation"])
        if generation != 1:
            _compensate_accepted_lease(
                directory_fd,
                "PROVISION_GENERATION_MISMATCH",
                generation,
                [temporary, TOKEN_NAME],
                temporary_metadata,
                [fence_temporary, FENCE_TOKEN_NAME],
                fence_metadata,
            )
        try:
            _fault("provision.before_private_stage")
            _stage_private_token(descriptor)
            temporary_metadata = os.fstat(descriptor)
            _fault("provision.after_private_stage")
            os.replace(
                fence_temporary, FENCE_TOKEN_NAME,
                src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
            )
            published_fence = _fence_token_metadata(directory_fd)
            _require(
                _same_inode(fence_metadata, published_fence),
                "SESSION_FENCE_TOKEN_PUBLISH_UNSAFE",
            )
            os.replace(
                temporary, TOKEN_NAME,
                src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            _fault("provision.after_publish")
            published = os.stat(
                TOKEN_NAME, dir_fd=directory_fd, follow_symlinks=False)
            _require(
                _same_inode(temporary_metadata, published) and
                published.st_uid == os.geteuid() and
                published.st_gid == os.getegid() and published.st_nlink == 1 and
                stat.S_IMODE(published.st_mode) == 0o600,
                "SESSION_TOKEN_PUBLISH_UNSAFE",
            )
            os.fsync(directory_fd)
            _fault("provision.after_publish_fsync")
            _fault("provision.after_temporary_unlink")
            _fault("provision.before_agent_commit")
            _fault("agent_commit.before_chown")
        except BaseException:
            _compensate_accepted_lease(
                directory_fd, "PROVISION", generation,
                [temporary, TOKEN_NAME], temporary_metadata,
                [fence_temporary, FENCE_TOKEN_NAME], fence_metadata)
        _commit_agent_ownership_or_compensate(
            directory_fd,
            descriptor,
            temporary_metadata,
            operation="PROVISION",
            generation=generation,
            token_names=[temporary, TOKEN_NAME],
            fence_names=[fence_temporary, FENCE_TOKEN_NAME],
            fence_metadata=fence_metadata,
        )
        return generation
    finally:
        try:
            os.close(descriptor)
        except OSError:
            # File contents and directory publication were already fsynced.
            pass


def rotate(directory_fd: int, generation: int, ttl_sec: int) -> int:
    _require_no_managed_token_residue(directory_fd)
    original_fence = _fence_token_metadata(directory_fd)
    try:
        original = _token_metadata(
            directory_fd, TOKEN_NAME, owner_uid=AGENT_UID)
    except BaseException as token_error:
        try:
            _revoke_exact_fence(
                directory_fd,
                generation,
                [FENCE_TOKEN_NAME],
                original_fence,
            )
            _cleanup_exact(
                directory_fd, [FENCE_TOKEN_NAME], original_fence)
            _remove_watch_lease_receipt(directory_fd)
            os.fsync(directory_fd)
        except BaseException as revoke_error:
            raise BootstrapError(
                "SESSION_ROTATE_PRECHECK_RECOVERY_REQUIRED"
            ) from revoke_error
        raise BootstrapError(
            "SESSION_ROTATE_PRECHECK_FAILED_REVOKED") from token_error
    (
        temporary,
        descriptor,
        replacement_metadata,
        fence_temporary,
        replacement_fence_metadata,
    ) = _create_token_pair(directory_fd, "rotate")
    accepted_generation = 0
    try:
        try:
            result = _sessionctl([
                "rotate", "--token-file", _token_path(FENCE_TOKEN_NAME),
                "--replacement-token-file", _token_path(fence_temporary),
                "--token-owner-uid", str(original_fence.st_uid),
                "--generation", str(generation), "--ttl-sec", str(ttl_sec),
            ])
        except BaseException as command_error:
            try:
                replacement_committed = _resolve_uncertain_acceptance(
                    directory_fd,
                    "ROTATE",
                    generation + 1,
                    [temporary, TOKEN_NAME],
                    replacement_metadata,
                    [fence_temporary],
                    replacement_fence_metadata,
                )
                if not replacement_committed:
                    _revoke_exact_fence(
                        directory_fd,
                        generation,
                        [FENCE_TOKEN_NAME],
                        original_fence,
                    )
                _cleanup_exact(
                    directory_fd, [TOKEN_NAME], original)
                _cleanup_exact(
                    directory_fd, [FENCE_TOKEN_NAME], original_fence)
                _remove_watch_lease_receipt(directory_fd)
                os.fsync(directory_fd)
            except BaseException as recovery_error:
                raise BootstrapError(
                    "SESSION_ROTATE_UNCERTAIN_RECOVERY_REQUIRED"
                ) from recovery_error
            reason = (
                "SESSION_ROTATE_RESULT_UNCERTAIN_REVOKED"
                if replacement_committed
                else "SESSION_ROTATE_NOT_COMMITTED_OLD_REVOKED")
            raise BootstrapError(reason) from command_error
        accepted_generation = int(result["lease_generation"])
        if accepted_generation != generation + 1:
            _compensate_accepted_lease(
                directory_fd,
                "ROTATE_GENERATION_MISMATCH",
                accepted_generation,
                [temporary, TOKEN_NAME],
                replacement_metadata,
                [fence_temporary],
                replacement_fence_metadata,
                stale_metadata=original,
                stale_fence_metadata=original_fence,
            )
        try:
            _fault("rotate.before_private_stage")
            _stage_private_token(descriptor)
            replacement_metadata = os.fstat(descriptor)
            _fault("rotate.after_private_stage")
            _fault("rotate.after_recovery_link")
            _require(
                _same_file(
                    original,
                    os.stat(
                        TOKEN_NAME, dir_fd=directory_fd,
                        follow_symlinks=False)),
                "SESSION_TOKEN_CHANGED_DURING_ROTATE",
            )
            _fault("rotate.before_publish")
            os.replace(
                fence_temporary, FENCE_TOKEN_NAME,
                src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            published_fence = _fence_token_metadata(directory_fd)
            _require(
                _same_inode(
                    replacement_fence_metadata, published_fence),
                "SESSION_FENCE_TOKEN_PUBLISH_UNSAFE",
            )
            os.replace(
                temporary, TOKEN_NAME,
                src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            _fault("rotate.after_publish")
            published = os.stat(
                TOKEN_NAME, dir_fd=directory_fd, follow_symlinks=False)
            _require(
                _same_inode(replacement_metadata, published) and
                published.st_uid == os.geteuid() and
                published.st_gid == os.getegid() and published.st_nlink == 1 and
                stat.S_IMODE(published.st_mode) == 0o600,
                "SESSION_TOKEN_PUBLISH_UNSAFE",
            )
            os.fsync(directory_fd)
            _fault("rotate.after_publish_fsync")
            _fault("rotate.after_recovery_unlink")
            _fault("rotate.before_agent_commit")
            _fault("agent_commit.before_chown")
        except BaseException:
            _compensate_accepted_lease(
                directory_fd, "ROTATE", accepted_generation,
                [temporary, TOKEN_NAME], replacement_metadata,
                [fence_temporary, FENCE_TOKEN_NAME],
                replacement_fence_metadata,
                stale_metadata=original,
                stale_fence_metadata=original_fence)
        _commit_agent_ownership_or_compensate(
            directory_fd,
            descriptor,
            replacement_metadata,
            operation="ROTATE",
            generation=accepted_generation,
            token_names=[temporary, TOKEN_NAME],
            fence_names=[fence_temporary, FENCE_TOKEN_NAME],
            fence_metadata=replacement_fence_metadata,
            stale_metadata=original,
            stale_fence_metadata=original_fence,
        )
        return accepted_generation
    finally:
        try:
            os.close(descriptor)
        except OSError:
            # File contents and directory publication were already fsynced.
            pass


def revoke(directory_fd: int, generation: int) -> int:
    recovered_generation = _recover_uncertain_provision_residue(
        directory_fd, generation)
    if recovered_generation is not None:
        return recovered_generation
    _require_no_managed_token_residue(directory_fd)
    try:
        fence_metadata = _fence_token_metadata(directory_fd)
    except BootstrapError as fence_error:
        try:
            quarantined = _quarantined_token_metadata(directory_fd)
        except BootstrapError:
            raise fence_error
        try:
            result = _sessionctl([
                "revoke", "--token-file", _token_path(TOKEN_NAME),
                "--token-owner-uid", str(quarantined.st_uid),
                "--generation", str(generation),
            ])
        except SessionNotFoundError:
            result = {
                "accepted": True,
                "reason_code": "OK",
                "lease_generation": generation,
            }
        except BaseException as revoke_error:
            _secure_unrevoked_token(
                directory_fd, [TOKEN_NAME], quarantined)
            raise BootstrapError(
                "SESSION_REVOKE_UNCERTAIN_RECOVERY_REQUIRED"
            ) from revoke_error
        _require(
            int(result["lease_generation"]) == generation,
            "SESSION_REVOKE_GENERATION_MISMATCH",
        )
        _require(
            _unlink_unchanged(directory_fd, TOKEN_NAME, quarantined),
            "SESSION_QUARANTINED_TOKEN_CHANGED",
        )
        os.fsync(directory_fd)
        _remove_watch_lease_receipt(directory_fd)
        return int(result["lease_generation"])

    original: os.stat_result | None = None
    quarantined: os.stat_result | None = None
    try:
        original = _token_metadata(
            directory_fd, TOKEN_NAME, owner_uid=AGENT_UID)
    except BaseException:
        if _metadata_at(directory_fd, TOKEN_NAME) is not None:
            quarantined = _quarantined_token_metadata(directory_fd)
    try:
        result = _sessionctl([
            "revoke", "--token-file", _token_path(FENCE_TOKEN_NAME),
            "--token-owner-uid", str(fence_metadata.st_uid),
            "--generation", str(generation),
        ])
    except SessionNotFoundError:
        if quarantined is None:
            receipt, _contents, _metadata = _read_watch_lease_receipt(
                directory_fd)
            _validate_watch_lease_receipt(
                receipt, expected_generation=generation)
            _require(
                isinstance(receipt.get("expires_at_ms"), int) and
                receipt["expires_at_ms"] <= _epoch_ms(),
                "SESSION_NOT_FOUND_BEFORE_WATCH_LEASE_EXPIRY",
            )
        if original is not None:
            _cleanup_exact(directory_fd, [TOKEN_NAME], original)
        elif quarantined is not None:
            _require(
                _unlink_unchanged(
                    directory_fd, TOKEN_NAME, quarantined),
                "SESSION_QUARANTINED_TOKEN_CHANGED",
            )
        _cleanup_exact(
            directory_fd, [FENCE_TOKEN_NAME], fence_metadata)
        os.fsync(directory_fd)
        _remove_watch_lease_receipt(directory_fd)
        return generation
    except BaseException as revoke_error:
        # The supervisor may have committed the fence even though the local
        # result is unavailable. Stop further Agent reads immediately, retain
        # the independent root bearer for an exact retry, and never report
        # revoke success from an ambiguous transport/result.
        try:
            _secure_unconfirmed_material(
                directory_fd,
                token_names=[TOKEN_NAME],
                token_metadata=(
                    original if original is not None else quarantined),
                fence_names=[FENCE_TOKEN_NAME],
                fence_metadata=fence_metadata,
            )
        except BaseException as safety_error:
            raise BootstrapError(
                "SESSION_REVOKE_UNCERTAIN_SAFETY_FAILED"
            ) from safety_error
        raise BootstrapError(
            "SESSION_REVOKE_UNCERTAIN_RECOVERY_REQUIRED") from revoke_error
    _require(
        int(result["lease_generation"]) == generation,
        "SESSION_REVOKE_GENERATION_MISMATCH",
    )
    if original is not None:
        _cleanup_exact(directory_fd, [TOKEN_NAME], original)
    elif quarantined is not None:
        _require(
            _unlink_unchanged(directory_fd, TOKEN_NAME, quarantined),
            "SESSION_QUARANTINED_TOKEN_CHANGED",
        )
    _cleanup_exact(
        directory_fd, [FENCE_TOKEN_NAME], fence_metadata)
    os.fsync(directory_fd)
    _remove_watch_lease_receipt(directory_fd)
    return int(result["lease_generation"])


def _audit_require_directory_ancestor_safe(
        metadata: os.stat_result,
) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    # ROOT_UID is patched only by rootless tests. In production this
    # collapses to the exact root:root requirement.
    trusted_owner_ids = {0, ROOT_UID}
    _require(
        stat.S_ISDIR(metadata.st_mode) and
        metadata.st_uid in trusted_owner_ids and
        metadata.st_gid in trusted_owner_ids and
        mode & 0o7022 == 0,
        "BOUNDARY_AUDIT_DIRECTORY_ANCESTOR_UNSAFE",
    )


def _audit_require_directory_final_safe(
        metadata: os.stat_result, *,
        expected_uid: int,
        expected_gid: int,
        allowed_modes: frozenset[int],
        expected_nlink: int | None,
) -> None:
    _require(
        stat.S_ISDIR(metadata.st_mode) and
        metadata.st_uid == expected_uid and
        metadata.st_gid == expected_gid and
        stat.S_IMODE(metadata.st_mode) in allowed_modes and
        (expected_nlink is None or metadata.st_nlink == expected_nlink),
        "BOUNDARY_AUDIT_DIRECTORY_METADATA_UNSAFE",
    )


def _audit_open_directory(
        path: Path, *,
        expected_uid: int,
        expected_gid: int,
        allowed_modes: frozenset[int],
        expected_nlink: int | None = None,
        missing_ok: bool = False,
) -> tuple[int, os.stat_result] | None:
    """Open one absolute directory without following any path-component link."""
    _require(
        path.is_absolute() and Path(os.path.normpath(str(path))) == path,
        "BOUNDARY_AUDIT_DIRECTORY_PATH_INVALID",
    )
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0) |
        (getattr(os, "O_NOATIME", 0) if os.geteuid() == 0 else 0)
    )
    descriptor = os.open("/", flags)
    try:
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            parent = descriptor
            parent_before = os.fstat(parent)
            try:
                descriptor = os.open(part, flags, dir_fd=parent)
            except FileNotFoundError:
                try:
                    os.stat(part, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise BootstrapError(
                        "BOUNDARY_AUDIT_DIRECTORY_CHANGED")
                _require(
                    _same_file(parent_before, os.fstat(parent)),
                    "BOUNDARY_AUDIT_DIRECTORY_CHANGED",
                )
                if missing_ok:
                    return None
                raise BootstrapError("BOUNDARY_AUDIT_DIRECTORY_MISSING")
            except OSError as error:
                raise BootstrapError(
                    "BOUNDARY_AUDIT_DIRECTORY_UNSAFE") from error
            finally:
                os.close(parent)
            if index + 1 < len(parts):
                _audit_require_directory_ancestor_safe(
                    os.fstat(descriptor))
        metadata = os.fstat(descriptor)
        _audit_require_directory_final_safe(
            metadata,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            allowed_modes=allowed_modes,
            expected_nlink=expected_nlink,
        )
        return descriptor, metadata
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _audit_require_directory_stable(
        path: Path,
        descriptor: int,
        before: os.stat_result, *,
        expected_uid: int,
        expected_gid: int,
        allowed_modes: frozenset[int],
        expected_nlink: int | None = None,
) -> None:
    after = os.fstat(descriptor)
    reopened = _audit_open_directory(
        path,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        allowed_modes=allowed_modes,
        expected_nlink=expected_nlink,
    )
    assert reopened is not None
    reopened_fd, path_after = reopened
    try:
        _require(
            _same_file(before, after) and
            _same_file(after, path_after),
            "BOUNDARY_AUDIT_DIRECTORY_CHANGED",
        )
    finally:
        os.close(reopened_fd)


def _audit_stable_file_at(
        directory_fd: int,
        name: str, *,
        expected_uid: int,
        expected_gid: int,
        expected_mode: int,
        minimum_size: int,
        maximum_size: int,
        read_contents: bool,
) -> tuple[bytearray | None, os.stat_result]:
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as error:
        raise BootstrapError("BOUNDARY_AUDIT_FILE_MISSING_OR_UNSAFE") from error
    _require(
        stat.S_ISREG(before.st_mode) and
        before.st_uid == expected_uid and before.st_gid == expected_gid and
        before.st_nlink == 1 and
        stat.S_IMODE(before.st_mode) == expected_mode and
        minimum_size <= before.st_size <= maximum_size,
        "BOUNDARY_AUDIT_FILE_METADATA_UNSAFE",
    )
    flags = (
        os.O_RDONLY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0) |
        (getattr(os, "O_NOATIME", 0) if os.geteuid() == 0 else 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise BootstrapError("BOUNDARY_AUDIT_FILE_CHANGED") from error
    contents: bytearray | None = bytearray() if read_contents else None
    try:
        opened = os.fstat(descriptor)
        if contents is not None:
            while len(contents) <= maximum_size:
                chunk = os.read(
                    descriptor,
                    min(8192, maximum_size + 1 - len(contents)),
                )
                if not chunk:
                    break
                contents.extend(chunk)
        after = os.fstat(descriptor)
        path_after = os.stat(
            name, dir_fd=directory_fd, follow_symlinks=False)
        _require(
            _same_file(before, opened) and
            _same_file(opened, after) and
            _same_file(after, path_after) and
            (contents is None or len(contents) == before.st_size),
            "BOUNDARY_AUDIT_FILE_CHANGED",
        )
    finally:
        os.close(descriptor)
    return contents, before


def _audit_watch_boundary(
        expected_state: str,
        expected_generation: int | None,
) -> dict[str, object]:
    opened = _audit_open_directory(
        RUNTIME_PARENT,
        expected_uid=ROOT_UID,
        expected_gid=ROOT_UID,
        allowed_modes=frozenset({0o711}),
        missing_ok=True,
    )
    if opened is None:
        _require(
            expected_state == "revoked",
            "BOUNDARY_AUDIT_ACTIVE_DIRECTORY_MISSING",
        )
        return {
            "token_directory_present": False,
            "lock_file_count": 0,
            "fixed_token_count": 0,
            "fixed_fence_count": 0,
            "authority_receipt_count": 0,
            "managed_temporary_count": 0,
            "unknown_entry_count": 0,
            "credential_pair_state": "ABSENT",
            "lease_generation": None,
            "expires_at_ms": None,
        }

    directory_fd, directory_before = opened
    try:
        try:
            names_before = sorted(os.listdir(directory_fd))
        except OSError as error:
            raise BootstrapError(
                "BOUNDARY_AUDIT_INVENTORY_SCAN_FAILED") from error
        _require(
            len(names_before) <= 64 and
            all(isinstance(name, str) and name not in {"", ".", ".."}
                for name in names_before),
            "BOUNDARY_AUDIT_INVENTORY_UNSAFE",
        )
        fixed_token_count = names_before.count(TOKEN_NAME)
        fixed_fence_count = names_before.count(FENCE_TOKEN_NAME)
        authority_receipt_count = names_before.count(
            WATCH_LEASE_RECEIPT_NAME)
        lock_file_count = names_before.count(LOCK_NAME)
        managed_temporary_count = sum(
            any(name.startswith(prefix)
                for prefix in BOUNDARY_AUDIT_MANAGED_PREFIXES)
            for name in names_before
        )
        allowed_names = {
            TOKEN_NAME, FENCE_TOKEN_NAME, WATCH_LEASE_RECEIPT_NAME, LOCK_NAME,
        }
        unknown_entry_count = sum(
            name not in allowed_names and
            not any(name.startswith(prefix)
                    for prefix in BOUNDARY_AUDIT_MANAGED_PREFIXES)
            for name in names_before
        )
        _require(
            managed_temporary_count == 0,
            "BOUNDARY_AUDIT_MANAGED_TEMPORARY_PRESENT",
        )
        _require(
            unknown_entry_count == 0,
            "BOUNDARY_AUDIT_UNKNOWN_ENTRY_PRESENT",
        )
        _require(
            lock_file_count in {0, 1},
            "BOUNDARY_AUDIT_LOCK_INVENTORY_INVALID",
        )
        if lock_file_count == 1:
            _audit_stable_file_at(
                directory_fd,
                LOCK_NAME,
                expected_uid=ROOT_UID,
                expected_gid=ROOT_UID,
                expected_mode=0o600,
                minimum_size=0,
                maximum_size=0,
                read_contents=False,
            )

        lease_generation: int | None = None
        expires_at_ms: int | None = None
        if expected_state == "revoked":
            _require(
                fixed_token_count == 0 and fixed_fence_count == 0 and
                authority_receipt_count == 0,
                "BOUNDARY_AUDIT_REVOKED_AUTHORITY_PRESENT",
            )
            credential_pair_state = "ABSENT"
        else:
            _require(
                fixed_token_count == 1 and fixed_fence_count == 1 and
                authority_receipt_count == 1 and
                expected_generation is not None,
                "BOUNDARY_AUDIT_ACTIVE_AUTHORITY_INCOMPLETE",
            )
            token, token_metadata = _audit_stable_file_at(
                directory_fd,
                TOKEN_NAME,
                expected_uid=AGENT_UID,
                expected_gid=AGENT_GID,
                expected_mode=0o600,
                minimum_size=24,
                maximum_size=512,
                read_contents=True,
            )
            fence, fence_metadata = _audit_stable_file_at(
                directory_fd,
                FENCE_TOKEN_NAME,
                expected_uid=ROOT_UID,
                expected_gid=ROOT_UID,
                expected_mode=0o600,
                minimum_size=24,
                maximum_size=512,
                read_contents=True,
            )
            assert token is not None and fence is not None
            try:
                _require(
                    not _same_inode(token_metadata, fence_metadata),
                    "BOUNDARY_AUDIT_CREDENTIAL_INODE_REUSED",
                )
                _require(
                    hmac.compare_digest(token, fence),
                    "BOUNDARY_AUDIT_CREDENTIAL_MISMATCH",
                )
            finally:
                token[:] = b"\0" * len(token)
                fence[:] = b"\0" * len(fence)
            receipt, _receipt_contents, _receipt_metadata = (
                _read_watch_lease_receipt(directory_fd))
            _validate_watch_lease_receipt(
                receipt, expected_generation=expected_generation)
            now_ms = _epoch_ms()
            _require(
                isinstance(receipt.get("expires_at_ms"), int) and
                not isinstance(receipt.get("expires_at_ms"), bool) and
                receipt["expires_at_ms"] > now_ms,
                "BOUNDARY_AUDIT_ACTIVE_LEASE_EXPIRED",
            )
            lease_generation = expected_generation
            expires_at_ms = int(receipt["expires_at_ms"])
            credential_pair_state = "MATCHING_DISTINCT"

        try:
            names_after = sorted(os.listdir(directory_fd))
        except OSError as error:
            raise BootstrapError(
                "BOUNDARY_AUDIT_INVENTORY_SCAN_FAILED") from error
        _require(
            names_after == names_before,
            "BOUNDARY_AUDIT_INVENTORY_CHANGED",
        )
        _audit_require_directory_stable(
            RUNTIME_PARENT,
            directory_fd,
            directory_before,
            expected_uid=ROOT_UID,
            expected_gid=ROOT_UID,
            allowed_modes=frozenset({0o711}),
        )
        return {
            "token_directory_present": True,
            "lock_file_count": lock_file_count,
            "fixed_token_count": fixed_token_count,
            "fixed_fence_count": fixed_fence_count,
            "authority_receipt_count": authority_receipt_count,
            "managed_temporary_count": managed_temporary_count,
            "unknown_entry_count": unknown_entry_count,
            "credential_pair_state": credential_pair_state,
            "lease_generation": lease_generation,
            "expires_at_ms": expires_at_ms,
        }
    finally:
        os.close(directory_fd)


def _audit_paper_execution_identity() -> tuple[int, int]:
    identity = f"hepta-ib-exec-{DOMAIN_ID}"
    try:
        passwd = pwd.getpwnam(identity)
        group = grp.getgrnam(identity)
        supplementary = sorted(set(os.getgrouplist(
            identity, passwd.pw_gid)))
    except KeyError as error:
        raise BootstrapError(
            "BOUNDARY_AUDIT_PAPER_IDENTITY_MISSING") from error
    _require(
        passwd.pw_name == identity and group.gr_name == identity and
        1 <= passwd.pw_uid <= 4_294_967_295 and
        1 <= passwd.pw_gid <= 4_294_967_295 and
        passwd.pw_gid == group.gr_gid and
        sorted(member for member in group.gr_mem if member != identity) == []
        and supplementary == [group.gr_gid],
        "BOUNDARY_AUDIT_PAPER_IDENTITY_UNSAFE",
    )
    return passwd.pw_uid, group.gr_gid


def _audit_kill_switch() -> bool:
    _paper_uid, paper_gid = _audit_paper_execution_identity()
    control = PAPER_CONTROL_ROOT / f"ib-paper-control-{DOMAIN_ID}"
    opened = _audit_open_directory(
        control,
        expected_uid=ROOT_UID,
        expected_gid=paper_gid,
        allowed_modes=frozenset({0o750}),
        expected_nlink=2,
    )
    assert opened is not None
    directory_fd, directory_before = opened
    try:
        contents, _metadata = _audit_stable_file_at(
            directory_fd,
            "kill-switch",
            expected_uid=ROOT_UID,
            expected_gid=paper_gid,
            expected_mode=0o440,
            minimum_size=7,
            maximum_size=7,
            read_contents=True,
        )
        assert contents is not None
        try:
            _require(
                hmac.compare_digest(contents, b"engaged"),
                "BOUNDARY_AUDIT_KILL_SWITCH_NOT_ENGAGED",
            )
        finally:
            contents[:] = b"\0" * len(contents)
        _audit_require_directory_stable(
            control,
            directory_fd,
            directory_before,
            expected_uid=ROOT_UID,
            expected_gid=paper_gid,
            allowed_modes=frozenset({0o750}),
            expected_nlink=2,
        )
    finally:
        os.close(directory_fd)
    return True


def _audit_campaign_policy_inventory() -> tuple[bool, int]:
    opened = _audit_open_directory(
        PAPER_CAMPAIGN_ROOT,
        expected_uid=ROOT_UID,
        expected_gid=ROOT_UID,
        allowed_modes=frozenset({0o700, 0o711, 0o750, 0o755}),
        missing_ok=True,
    )
    if opened is None:
        return False, 0
    directory_fd, directory_before = opened
    try:
        try:
            names_before = sorted(os.listdir(directory_fd))
            names_after = sorted(os.listdir(directory_fd))
        except OSError as error:
            raise BootstrapError(
                "BOUNDARY_AUDIT_CAMPAIGN_SCAN_FAILED") from error
        _require(
            names_before == [] and names_after == [],
            "BOUNDARY_AUDIT_CAMPAIGN_POLICY_PRESENT",
        )
        _audit_require_directory_stable(
            PAPER_CAMPAIGN_ROOT,
            directory_fd,
            directory_before,
            expected_uid=ROOT_UID,
            expected_gid=ROOT_UID,
            allowed_modes=frozenset({0o700, 0o711, 0o750, 0o755}),
        )
    finally:
        os.close(directory_fd)
    return True, 0


def _audit_stable_root_executable(
        path: str,
) -> tuple[str, os.stat_result]:
    try:
        before = os.lstat(path)
    except OSError as error:
        raise BootstrapError("BOUNDARY_AUDIT_HELPER_MISSING") from error
    _require(
        stat.S_ISREG(before.st_mode) and
        not stat.S_ISLNK(before.st_mode) and
        before.st_uid == ROOT_UID and before.st_gid == ROOT_UID and
        before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o755 and
        1 <= before.st_size <= BOUNDARY_AUDIT_HELPER_MAX_BYTES,
        "BOUNDARY_AUDIT_HELPER_METADATA_UNSAFE",
    )
    flags = (
        os.O_RDONLY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0) |
        (getattr(os, "O_NOATIME", 0) if os.geteuid() == 0 else 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise BootstrapError("BOUNDARY_AUDIT_HELPER_CHANGED") from error
    digest = hashlib.sha256()
    total = 0
    try:
        opened = os.fstat(descriptor)
        while total <= BOUNDARY_AUDIT_HELPER_MAX_BYTES:
            chunk = os.read(
                descriptor,
                min(65_536, BOUNDARY_AUDIT_HELPER_MAX_BYTES + 1 - total),
            )
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        path_after = os.lstat(path)
        _require(
            total == before.st_size and
            _same_file(before, opened) and
            _same_file(opened, after) and
            _same_file(after, path_after),
            "BOUNDARY_AUDIT_HELPER_CHANGED",
        )
    finally:
        os.close(descriptor)
    return "sha256:" + digest.hexdigest(), before


def _audit_read_only_command(
        arguments: list[str],
) -> subprocess.CompletedProcess[bytes]:
    environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
    }
    return subprocess.run(
        arguments,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        close_fds=True,
        timeout=15,
    )


def _audit_paper_boundary() -> tuple[dict[str, object], dict[str, object]]:
    systemctl_digest, systemctl_metadata = _audit_stable_root_executable(
        SYSTEMCTL)
    egress_digest, egress_metadata = _audit_stable_root_executable(
        BROKER_EGRESS_POLICY)
    paper_units = (
        "hepta-execution-ib-paper.service",
        "hepta-execution-ib-paper.socket",
        "hepta-execution-events-ib-paper.socket",
        f"hepta-execution-ib-paper@{DOMAIN_ID}.service",
        f"hepta-execution-ib-paper@{DOMAIN_ID}.socket",
        f"hepta-execution-events-ib-paper@{DOMAIN_ID}.socket",
        f"hepta-ib-paper-domain-preflight@{DOMAIN_ID}.service",
        f"hepta-ib-paper-campaign-operator@{DOMAIN_ID}.service",
        f"hepta-ib-paper-campaign-operator@{DOMAIN_ID}.socket",
    )
    unit_states: dict[str, str] = {}
    for unit in paper_units:
        completed = _audit_read_only_command(
            [SYSTEMCTL, "is-active", unit])
        _require(
            isinstance(completed.stdout, bytes) and
            isinstance(completed.stderr, bytes) and
            len(completed.stdout) <= 4096 and
            len(completed.stderr) <= 4096 and
            completed.returncode == 3 and
            completed.stdout == b"inactive\n" and
            completed.stderr == b"",
            "BOUNDARY_AUDIT_PAPER_UNIT_NOT_INACTIVE",
        )
        unit_states[unit] = "inactive"

    policy_directory_present, policy_file_count = (
        _audit_campaign_policy_inventory())
    kill_switch_engaged = _audit_kill_switch()
    egress = _audit_read_only_command(
        [BROKER_EGRESS_POLICY, "--check-deny-all"])
    _require(
        isinstance(egress.stdout, bytes) and
        isinstance(egress.stderr, bytes) and
        len(egress.stdout) <= 4096 and len(egress.stderr) <= 4096 and
        egress.returncode == 0,
        "BOUNDARY_AUDIT_BROKER_EGRESS_NOT_DENY_ALL",
    )

    systemctl_digest_after, systemctl_metadata_after = (
        _audit_stable_root_executable(SYSTEMCTL))
    egress_digest_after, egress_metadata_after = (
        _audit_stable_root_executable(BROKER_EGRESS_POLICY))
    _require(
        systemctl_digest_after == systemctl_digest and
        egress_digest_after == egress_digest and
        _same_file(systemctl_metadata, systemctl_metadata_after) and
        _same_file(egress_metadata, egress_metadata_after),
        "BOUNDARY_AUDIT_HELPER_CHANGED",
    )
    paper = {
        "unit_count": len(paper_units),
        "inactive_unit_count": len(unit_states),
        "unit_states": unit_states,
        "campaign_policy_directory_present": policy_directory_present,
        "campaign_policy_file_count": policy_file_count,
        "kill_switch_engaged": kill_switch_engaged,
        "broker_egress_deny_all": True,
    }
    helpers = {
        "systemctl": {
            "path": SYSTEMCTL,
            "sha256": systemctl_digest,
            "metadata_safe": True,
        },
        "broker_egress_policy": {
            "path": BROKER_EGRESS_POLICY,
            "sha256": egress_digest,
            "metadata_safe": True,
        },
    }
    return paper, helpers


def audit_boundary(
        expected_state: str,
        expected_generation: int | None,
) -> dict[str, object]:
    """Prove the local WATCH and deny-all boundary without mutating it."""
    _require(
        os.geteuid() == ROOT_UID and os.getegid() == ROOT_UID,
        "ROOT_REQUIRED",
    )
    _require(
        expected_state in {"active", "revoked"},
        "BOUNDARY_AUDIT_EXPECTED_STATE_INVALID",
    )
    if expected_generation is not None:
        _require(
            isinstance(expected_generation, int) and
            not isinstance(expected_generation, bool) and
            1 <= expected_generation <= (1 << 64) - 1,
            "SESSION_GENERATION_INVALID",
        )
    _require(
        expected_state != "active" or expected_generation is not None,
        "BOUNDARY_AUDIT_ACTIVE_GENERATION_REQUIRED",
    )
    watch = _audit_watch_boundary(expected_state, expected_generation)
    paper, helpers = _audit_paper_boundary()
    body: dict[str, object] = {
        "schema": "hepta.agent-session-boundary-audit.v1",
        "version": 1,
        "audited_at_ms": _epoch_ms(),
        "domain_id": DOMAIN_ID,
        "agent_uid": AGENT_UID,
        "expected_state": expected_state,
        "expected_generation": expected_generation,
        "observed_state": expected_state,
        "watch": watch,
        "paper": paper,
        "helpers": helpers,
        "boundary_intact": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }
    return {**body, "body_sha256": _document_digest(body)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicit WATCH-only HeptaTrader Agent session bootstrap")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--domain-config", type=Path)
    mode.add_argument("--single-domain-compat", action="store_true")
    subcommands = parser.add_subparsers(dest="operation", required=True)
    provision = subcommands.add_parser("provision-watch")
    provision.add_argument("--agent-id", required=True)
    provision.add_argument("--session-id", required=True)
    provision.add_argument(
        "--ttl-sec", type=int, default=3600,
        choices=range(MIN_TTL_SEC, MAX_TTL_SEC + 1),
        metavar=f"{MIN_TTL_SEC}..{MAX_TTL_SEC}")
    rotate_parser = subcommands.add_parser("rotate")
    rotate_parser.add_argument("--generation", required=True, type=int)
    rotate_parser.add_argument(
        "--ttl-sec", type=int, default=3600,
        choices=range(MIN_TTL_SEC, MAX_TTL_SEC + 1),
        metavar=f"{MIN_TTL_SEC}..{MAX_TTL_SEC}")
    revoke_parser = subcommands.add_parser("revoke")
    revoke_parser.add_argument("--generation", required=True, type=int)
    audit_parser = subcommands.add_parser("audit-boundary")
    audit_parser.add_argument(
        "--expected-state", required=True, choices=("active", "revoked"))
    audit_parser.add_argument("--generation", type=int)
    return parser


def main() -> int:
    global DOMAIN_ID, AGENT_NAME, AGENT_UID, AGENT_GID
    global GATEWAY_NAME, GATEWAY_UID, GATEWAY_GID
    global GATEWAY_SUPPLEMENTARY_GROUPS
    global RUNTIME_PARENT, SUPERVISOR_SOCKET
    arguments = _parser().parse_args()
    try:
        _require(os.geteuid() == 0 and os.getegid() == 0, "ROOT_REQUIRED")
        publish_watch_receipt = arguments.domain_config is not None
        if arguments.domain_config is not None:
            domain = load_runtime_config(arguments.domain_config)
            _configure_domain(domain)
        else:
            _require(
                arguments.single_domain_compat is True,
                "EXPLICIT_TRUST_DOMAIN_MODE_REQUIRED")
        if arguments.operation == "audit-boundary":
            _require(
                arguments.domain_config is not None,
                "BOUNDARY_AUDIT_DOMAIN_CONFIG_REQUIRED",
            )
            audit = audit_boundary(
                arguments.expected_state, arguments.generation)
            sys.stdout.buffer.write(_canonical_bytes(audit))
            return 0
        _validate_identity(AGENT_NAME, AGENT_UID, AGENT_GID)
        _validate_identity(
            GATEWAY_NAME, GATEWAY_UID, GATEWAY_GID,
            GATEWAY_SUPPLEMENTARY_GROUPS)
        _validate_sessionctl()
        if arguments.operation == "provision-watch":
            _require(
                IDENTIFIER.fullmatch(arguments.agent_id) is not None and
                IDENTIFIER.fullmatch(arguments.session_id) is not None,
                "SESSION_IDENTIFIER_INVALID",
            )
            if publish_watch_receipt:
                _require(
                    arguments.agent_id == DOMAIN_ID,
                    "WATCH_LEASE_RECEIPT_AGENT_BINDING_INVALID",
                )
        else:
            _require(
                1 <= arguments.generation <= (1 << 64) - 1,
                "SESSION_GENERATION_INVALID",
            )
        if (
                publish_watch_receipt and
                arguments.operation in {"provision-watch", "rotate"}):
            _require(
                arguments.ttl_sec <= MAX_SHADOW_WATCH_TTL_SEC,
                "WATCH_LEASE_RECEIPT_TTL_INVALID",
            )
        directory_fd = _runtime_directory()
        try:
            lock_fd = _lock(directory_fd)
            try:
                if arguments.operation == "provision-watch":
                    previous = (
                        _watch_lease_receipt_preflight(
                            directory_fd, "PROVISION")
                        if publish_watch_receipt else None
                    )
                    accepted_at_ms = _epoch_ms()
                    generation = provision_watch(
                        directory_fd, arguments.agent_id,
                        arguments.session_id, arguments.ttl_sec)
                    if publish_watch_receipt:
                        _publish_watch_lease_receipt_or_compensate(
                            directory_fd,
                            operation="PROVISION",
                            generation=generation,
                            agent_id=arguments.agent_id,
                            ttl_seconds=arguments.ttl_sec,
                            accepted_at_ms=accepted_at_ms,
                            previous=previous,
                        )
                elif arguments.operation == "rotate":
                    previous = (
                        _watch_lease_receipt_preflight(
                            directory_fd, "ROTATE",
                            arguments.generation)
                        if publish_watch_receipt else None
                    )
                    accepted_at_ms = _epoch_ms()
                    generation = rotate(
                        directory_fd, arguments.generation, arguments.ttl_sec)
                    if publish_watch_receipt:
                        _publish_watch_lease_receipt_or_compensate(
                            directory_fd,
                            operation="ROTATE",
                            generation=generation,
                            agent_id=DOMAIN_ID,
                            ttl_seconds=arguments.ttl_sec,
                            accepted_at_ms=accepted_at_ms,
                            previous=previous,
                        )
                else:
                    generation = revoke(directory_fd, arguments.generation)
            finally:
                os.close(lock_fd)
        finally:
            os.close(directory_fd)
    except (
            BootstrapError, TrustDomainRuntimeError, OSError,
            subprocess.SubprocessError,
    ) as error:
        message = str(error)
        if not re.fullmatch(r"[A-Z0-9_]{3,96}", message):
            message = "AGENT_SESSION_BOOTSTRAP_FAILED"
        print(message, file=sys.stderr)
        return 78
    print(json.dumps({
        "schema": "hepta.agent-session-bootstrap.v1",
        "accepted": True,
        "operation": arguments.operation,
        "trust_domain": DOMAIN_ID,
        "peer_uid": AGENT_UID,
        "lease_generation": generation,
        "paper_authorized": False,
        "live_authorized": False,
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
