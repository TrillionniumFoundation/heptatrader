#!/usr/bin/env python3

"""Root-owned fail-closed lifecycle custodian for SHADOW WATCH leases.

The production CLI is the single durable wrapper around the reviewed WATCH
bootstrap: it records provision/rotation intent before invoking that bootstrap,
then commits only from exact root-owned receipt and fence evidence. It also
monitors the registered owner and lease expiry and performs an exact-generation
revoke plus local cleanup when continuity is lost. Raw bearer material remains
runtime-only under /run and is never copied into durable custodian state.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hepta_agent_trust_domain import (  # noqa: E402
    TrustDomainRuntimeError,
    load_runtime_config,
)


ROOT_UID = 0
ROOT_GID = 0
STATE_ROOT = Path("/var/lib/hepta-shadow-watch-custodian")
WATCH_STATE_ROOT = Path("/var/lib")
EXPORT_RUNTIME_ROOT = Path("/run")
WATCH_ENV_ROOT = Path("/etc/heptatrader/trust-domains")
SESSIONCTL = "/usr/bin/hepta-sessionctl"
BOOTSTRAP = "/usr/libexec/hepta-agent-session-bootstrap"
TOKEN_NAME = "session.token"
FENCE_TOKEN_NAME = ".session-fence.token"
LEASE_RECEIPT_NAME = "shadow-watch-lease-receipt.json"
TRANSACTION_NAME = "transaction.json"
LOCK_NAME = ".custodian.lock"
CLOSURES_NAME = "closures"
MAX_JSON_BYTES = 65_536
MAX_EXPORT_BYTES = 256 * 1024
MAX_TOKEN_BYTES = 512
MIN_TOKEN_BYTES = 24
START_GRACE_MS = 15_000
ROTATION_HANDOFF_GRACE_MS = 15_000
PROVISION_HANDOFF_GRACE_MS = 15_000
POLL_SECONDS = 1.0
COMMAND_TIMEOUT_SECONDS = 15

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
DOMAIN_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
BOOT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$")

TRANSACTION_FIELDS = frozenset({
    "schema", "version", "phase", "domain_id", "campaign_id",
    "config_sha256", "watch_environment_sha256",
    "token_directory", "supervisor_socket",
    "agent_uid", "agent_gid", "gateway_uid", "execution_uid",
    "owner_pid", "owner_uid", "owner_gid",
    "owner_start_ticks", "owner_boot_id", "lease_generation",
    "lease_receipt_body_sha256", "fence_token_sha256",
    "lease_expires_at_ms", "registered_at_ms",
    "monitor_pid", "monitor_start_ticks", "monitor_boot_id",
    "provision_ttl_seconds",
    "rotation_expected_generation", "rotation_started_at_ms",
    "close_reason", "close_started_at_ms",
    "authoritative_revoke_outcome", "paper_authorized",
    "live_authorized", "mutation_authorized", "direct_broker_access",
    "body_sha256",
})
LEASE_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_id", "agent_uid", "boundary",
    "operation", "lease_generation", "previous_lease_generation",
    "previous_receipt_body_sha256", "accepted", "reason_code",
    "accepted_at_ms", "ttl_seconds", "expires_at_ms", "paper_authorized",
    "live_authorized", "mutation_authorized", "body_sha256",
})
CLOSURE_FIELDS = frozenset({
    "schema", "version", "domain_id", "campaign_id", "config_sha256",
    "watch_environment_sha256",
    "token_directory", "supervisor_socket",
    "agent_uid", "agent_gid", "gateway_uid", "execution_uid",
    "owner_pid", "owner_uid", "owner_gid", "owner_start_ticks",
    "owner_boot_id", "lease_generation", "lease_receipt_body_sha256",
    "fence_token_sha256",
    "lease_expires_at_ms", "registered_at_ms", "close_started_at_ms",
    "closed_at_ms", "close_reason", "authoritative_revoke_outcome",
    "local_authority_removed", "export_evidence_removed",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})
CLOSE_REASONS = frozenset({
    "owner-dead", "lease-expired", "service-stop", "service-stop-post",
    "custodian-restart", "custodian-missing", "registration-recovery",
    "configuration-drift", "rotation-recovery", "operator-request",
})
ROTATION_TOKEN_PATTERN = re.compile(
    r"^\.session-token-rotate-([1-9][0-9]*)-[0-9a-f]{16}$")
ROTATION_FENCE_PATTERN = re.compile(
    r"^\.session-fence-rotate-([1-9][0-9]*)-[0-9a-f]{16}$")
PROVISION_TOKEN_PATTERN = re.compile(
    r"^\.session-token-provision-([1-9][0-9]*)-[0-9a-f]{16}$")
PROVISION_FENCE_PATTERN = re.compile(
    r"^\.session-fence-provision-([1-9][0-9]*)-[0-9a-f]{16}$")
ZERO_DIGEST = "sha256:" + "0" * 64
SNAPSHOT_V1_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_uid", "generated_at_ms",
    "instrument", "catalog_sha256", "descriptor_sha256", "reads",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
SNAPSHOT_V2_FIELDS = SNAPSHOT_V1_FIELDS | frozenset({
    "collection_started_at_ms", "collection_finished_at_ms",
    "read_finished_at_ms",
})
EXPORT_FILES = (
    "snapshot.json",
    "shadow-watch-lease-receipt.json",
    "shadow-watch-export-receipt.json",
)
EXPORT_COMMIT_NAME = "current.json"
EXPORT_GENERATIONS_NAME = "generations"
EXPORT_GENERATION = re.compile(
    r"^generation-([0-9]{20})-([A-Za-z0-9_-]{8,64})$")
EXPORT_STAGING = re.compile(r"^\.staging-[A-Za-z0-9_-]{8,64}$")
EXPORT_COMMIT_FIELDS = frozenset({
    "schema", "version", "authority_status", "authority_changed_at_ms",
    "close_reason", "commit_sequence", "generation", "domain_id",
    "agent_uid", "reader_uid", "reader_gid", "lease_generation",
    "snapshot_body_sha256", "snapshot_file_sha256",
    "lease_receipt_body_sha256", "lease_receipt_file_sha256",
    "export_receipt_body_sha256", "export_receipt_file_sha256",
    "committed_at_ms", "paper_authorized", "live_authorized",
    "mutation_attempted", "direct_broker_access", "body_sha256",
})


class CustodianError(RuntimeError):
    """A stable fail-closed custodian rejection."""


class SessionNotFound(CustodianError):
    """The exact supervisor lease is already absent."""


class LiveFenceMissing(CustodianError):
    """The runtime-only exact revoke bearer is unavailable."""


def _fault(stage: str) -> None:
    """In-process fault seam; production has no CLI/environment switch."""
    del stage


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise CustodianError(reason)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        _require(key not in result, "CUSTODIAN_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _canonical(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise CustodianError("CUSTODIAN_CANONICALIZATION_FAILED") from error


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _body_document(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "body_sha256": _digest(_canonical(body))}


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_ISREG(left.st_mode) and stat.S_ISREG(right.st_mode) and
        left.st_dev == right.st_dev and left.st_ino == right.st_ino
    )


def _safe_directory(
    path: Path,
    *,
    mode: int,
    create: bool,
) -> os.stat_result:
    created = False
    if create:
        try:
            path.mkdir(mode=mode)
            created = True
        except FileExistsError:
            pass
    before = path.lstat()
    before_mode = stat.S_IMODE(before.st_mode)
    _require(
        stat.S_ISDIR(before.st_mode) and
        not stat.S_ISLNK(before.st_mode) and
        before.st_uid == ROOT_UID and before.st_gid == ROOT_GID and
        (before_mode == mode or
         (created and before_mode & ~mode == 0)),
        "CUSTODIAN_STATE_DIRECTORY_UNSAFE",
    )
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        after = os.fstat(descriptor)
        _require(
            _identity(before) == _identity(after),
            "CUSTODIAN_STATE_DIRECTORY_CHANGED",
        )
        if created:
            os.fchmod(descriptor, mode)
            after = os.fstat(descriptor)
        _require(
            after.st_uid == ROOT_UID and after.st_gid == ROOT_GID and
            stat.S_IMODE(after.st_mode) == mode,
            "CUSTODIAN_STATE_DIRECTORY_UNSAFE",
        )
        named = path.lstat()
        _require(
            _identity(named) == _identity(after),
            "CUSTODIAN_STATE_DIRECTORY_CHANGED",
        )
    finally:
        os.close(descriptor)
    return after


def _state_directory(domain_id: str, *, create: bool) -> Path:
    _require(
        DOMAIN_ID.fullmatch(domain_id) is not None,
        "CUSTODIAN_DOMAIN_ID_INVALID",
    )
    _safe_directory(STATE_ROOT, mode=0o700, create=create)
    directory = STATE_ROOT / domain_id
    _safe_directory(directory, mode=0o700, create=create)
    closures = directory / CLOSURES_NAME
    _safe_directory(closures, mode=0o700, create=create)
    return directory


@contextmanager
def _locked(directory: Path) -> Iterator[None]:
    descriptor = os.open(
        directory / LOCK_NAME,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
            stat.S_IMODE(metadata.st_mode) == 0o600,
            "CUSTODIAN_LOCK_UNSAFE",
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _read_small_regular(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
    minimum: int,
    maximum: int,
) -> tuple[bytes, os.stat_result]:
    before = path.lstat()
    _require(
        stat.S_ISREG(before.st_mode) and
        not stat.S_ISLNK(before.st_mode) and before.st_nlink == 1 and
        before.st_uid == uid and before.st_gid == gid and
        stat.S_IMODE(before.st_mode) == mode and
        minimum <= before.st_size <= maximum,
        "CUSTODIAN_FILE_METADATA_UNSAFE",
    )
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(descriptor, min(8192, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        path_after = path.lstat()
        _require(
            len(data) == before.st_size and
            _identity(before) == _identity(opened) ==
            _identity(after) == _identity(path_after),
            "CUSTODIAN_FILE_CHANGED",
        )
        return bytes(data), after
    finally:
        os.close(descriptor)


def _strict_json_bytes(data: bytes, reason: str) -> dict[str, Any]:
    try:
        document = json.loads(
            data.decode("ascii", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                CustodianError(reason)),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise CustodianError(reason) from error
    _require(
        isinstance(document, dict) and data == _canonical(document),
        reason,
    )
    return document


def _atomic_write(
    path: Path,
    document: dict[str, Any],
    *,
    replace: bool,
) -> None:
    payload = _canonical(document)
    _require(
        len(payload) <= MAX_JSON_BYTES,
        "CUSTODIAN_STATE_TOO_LARGE",
    )
    directory = path.parent
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=directory)
    temporary = Path(temporary_name)
    try:
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            _require(written > 0, "CUSTODIAN_STATE_SHORT_WRITE")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
            stat.S_IMODE(metadata.st_mode) == 0o600 and
            metadata.st_size == len(payload),
            "CUSTODIAN_STATE_TEMPORARY_UNSAFE",
        )
        os.close(descriptor)
        descriptor = -1
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as error:
                raise CustodianError(
                    "CUSTODIAN_STATE_ALREADY_EXISTS") from error
            os.unlink(temporary)
        directory_fd = os.open(
            directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _stable_config_digest(path: Path) -> str:
    data, _metadata = _read_small_regular(
        path,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o600,
        minimum=2,
        maximum=MAX_JSON_BYTES,
    )
    return _digest(data)


def _load_config(path: Path) -> tuple[dict[str, Any], str]:
    config = load_runtime_config(path)
    _require(
        config.get("paper_authorized") is False and
        config.get("live_authorized") is False,
        "CUSTODIAN_TRADING_AUTHORITY_FORBIDDEN",
    )
    return config, _stable_config_digest(path)


def _load_watch_reader_identity(
    config: dict[str, Any],
) -> tuple[int, int, str]:
    path = WATCH_ENV_ROOT / f"{config['domain_id']}.shadow-watch.env"
    data, _metadata = _read_small_regular(
        path,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o600,
        minimum=2,
        maximum=4096,
    )
    try:
        lines = data.decode("ascii", errors="strict").splitlines()
    except UnicodeError as error:
        raise CustodianError(
            "CUSTODIAN_WATCH_ENVIRONMENT_INVALID") from error
    values: dict[str, int] = {}
    expected = {
        "HEPTA_SHADOW_AGENT_UID",
        "HEPTA_SHADOW_AGENT_GID",
        "HEPTA_SHADOW_READER_UID",
        "HEPTA_SHADOW_READER_GID",
    }
    for raw in lines:
        _require(
            raw and not raw.startswith("#") and
            raw.count("=") == 1 and
            raw == raw.strip(),
            "CUSTODIAN_WATCH_ENVIRONMENT_INVALID",
        )
        key, value = raw.split("=", 1)
        _require(
            key in expected and key not in values and
            re.fullmatch(r"[1-9][0-9]{0,9}", value) is not None,
            "CUSTODIAN_WATCH_ENVIRONMENT_INVALID",
        )
        parsed = int(value)
        _require(
            1 <= parsed <= 4_294_967_295,
            "CUSTODIAN_WATCH_ENVIRONMENT_INVALID",
        )
        values[key] = parsed
    _require(
        set(values) == expected and
        values["HEPTA_SHADOW_AGENT_UID"] == config["agent_uid"] and
        values["HEPTA_SHADOW_AGENT_GID"] == config["agent_gid"],
        "CUSTODIAN_WATCH_ENVIRONMENT_BINDING_INVALID",
    )
    reader_uid = values["HEPTA_SHADOW_READER_UID"]
    reader_gid = values["HEPTA_SHADOW_READER_GID"]
    _require(
        reader_uid not in {
            ROOT_UID,
            int(config["gateway_uid"]),
            int(config["agent_uid"]),
            int(config["execution_uid"]),
        },
        "CUSTODIAN_WATCH_READER_ROLE_FORBIDDEN",
    )
    return reader_uid, reader_gid, _digest(data)


def _validate_digest_field(value: object, reason: str) -> str:
    _require(
        isinstance(value, str) and DIGEST.fullmatch(value) is not None,
        reason,
    )
    return value


def _validate_absolute_path_field(value: object, reason: str) -> str:
    _require(
        isinstance(value, str) and 1 <= len(value.encode("utf-8")) <= 4096 and
        "\0" not in value and Path(value).is_absolute() and
        Path(value).as_posix() == value,
        reason,
    )
    return value


def _validate_lease_receipt(
    path: Path,
    config: dict[str, Any],
    generation: int,
    *,
    allow_expired: bool = False,
) -> dict[str, Any]:
    data, _metadata = _read_small_regular(
        path,
        uid=ROOT_UID,
        gid=int(config["agent_gid"]),
        mode=0o440,
        minimum=2,
        maximum=MAX_JSON_BYTES,
    )
    receipt = _strict_json_bytes(data, "CUSTODIAN_LEASE_RECEIPT_INVALID")
    _require(
        set(receipt) == set(LEASE_FIELDS) and
        receipt.get("schema") ==
        "hepta.shadow-watch-lease-receipt.v1" and
        receipt.get("version") == 1 and
        receipt.get("domain_id") == config["domain_id"] and
        receipt.get("agent_id") == config["domain_id"] and
        receipt.get("agent_uid") == config["agent_uid"] and
        receipt.get("boundary") == "WATCH" and
        receipt.get("operation") in {"PROVISION", "ROTATE"} and
        receipt.get("lease_generation") == generation and
        receipt.get("accepted") is True and
        receipt.get("reason_code") == "OK" and
        receipt.get("paper_authorized") is False and
        receipt.get("live_authorized") is False and
        receipt.get("mutation_authorized") is False,
        "CUSTODIAN_LEASE_RECEIPT_BINDING_INVALID",
    )
    accepted_at = receipt.get("accepted_at_ms")
    ttl = receipt.get("ttl_seconds")
    expires = receipt.get("expires_at_ms")
    _require(
        type(generation) is int and 1 <= generation <= (1 << 64) - 1 and
        type(accepted_at) is int and accepted_at >= 0 and
        type(ttl) is int and 60 <= ttl <= 3600 and
        type(expires) is int and
        expires == accepted_at + ttl * 1000 and
        (allow_expired or expires > _now_ms()),
        "CUSTODIAN_LEASE_RECEIPT_TIME_INVALID",
    )
    previous_generation = receipt.get("previous_lease_generation")
    previous_digest = receipt.get("previous_receipt_body_sha256")
    if receipt["operation"] == "PROVISION":
        _require(
            previous_generation is None and previous_digest is None and
            generation == 1,
            "CUSTODIAN_LEASE_RECEIPT_CHAIN_INVALID",
        )
    else:
        _require(
            type(previous_generation) is int and
            previous_generation == generation - 1 and
            isinstance(previous_digest, str) and
            DIGEST.fullmatch(previous_digest) is not None,
            "CUSTODIAN_LEASE_RECEIPT_CHAIN_INVALID",
        )
    body = dict(receipt)
    claimed = body.pop("body_sha256", None)
    _require(
        isinstance(claimed, str) and
        claimed == _digest(_canonical(body)),
        "CUSTODIAN_LEASE_RECEIPT_DIGEST_INVALID",
    )
    return receipt


def _read_boot_id() -> str:
    data = Path("/proc/sys/kernel/random/boot_id").read_text(
        encoding="ascii", errors="strict").strip()
    _require(
        BOOT_ID.fullmatch(data) is not None,
        "CUSTODIAN_BOOT_ID_INVALID",
    )
    return data


def _process_start_ticks(document: str) -> int:
    close = document.rfind(")")
    _require(close >= 2, "CUSTODIAN_OWNER_STAT_INVALID")
    fields = document[close + 2:].split()
    _require(
        len(fields) >= 20 and fields[0] not in {"Z", "X", "x"} and
        fields[19].isdigit(),
        "CUSTODIAN_OWNER_STAT_INVALID",
    )
    return int(fields[19])


def _process_identity(pid: int) -> dict[str, int | str]:
    _require(
        type(pid) is int and 2 <= pid <= 4_194_304,
        "CUSTODIAN_OWNER_PID_INVALID",
    )
    stat_path = Path(f"/proc/{pid}/stat")
    status_path = Path(f"/proc/{pid}/status")
    before = stat_path.read_text(encoding="ascii", errors="strict")
    status = status_path.read_text(encoding="ascii", errors="strict")
    after = stat_path.read_text(encoding="ascii", errors="strict")
    before_start_ticks = _process_start_ticks(before)
    after_start_ticks = _process_start_ticks(after)
    _require(
        before_start_ticks == after_start_ticks,
        "CUSTODIAN_OWNER_PROCESS_CHANGED",
    )
    uid_line = next(
        (line for line in status.splitlines() if line.startswith("Uid:")),
        None,
    )
    gid_line = next(
        (line for line in status.splitlines() if line.startswith("Gid:")),
        None,
    )
    _require(
        uid_line is not None and gid_line is not None,
        "CUSTODIAN_OWNER_STATUS_INVALID",
    )
    uids = uid_line.split()[1:]
    gids = gid_line.split()[1:]
    _require(
        len(uids) == 4 and len(set(uids)) == 1 and uids[0].isdigit() and
        len(gids) == 4 and len(set(gids)) == 1 and gids[0].isdigit(),
        "CUSTODIAN_OWNER_IDENTITY_UNSAFE",
    )
    return {
        "pid": pid,
        "uid": int(uids[0]),
        "gid": int(gids[0]),
        "start_ticks": before_start_ticks,
        "boot_id": _read_boot_id(),
    }


def _process_matches(
    pid: int,
    uid: int,
    gid: int,
    start_ticks: int,
    boot_id: str,
) -> bool:
    try:
        current = _process_identity(pid)
    except (CustodianError, OSError, UnicodeError, ValueError):
        return False
    return (
        current["uid"] == uid and current["gid"] == gid and
        current["start_ticks"] == start_ticks and
        current["boot_id"] == boot_id
    )


def _transaction_path(directory: Path) -> Path:
    return directory / TRANSACTION_NAME


def _read_transaction(directory: Path) -> dict[str, Any] | None:
    path = _transaction_path(directory)
    try:
        data, _metadata = _read_small_regular(
            path,
            uid=ROOT_UID,
            gid=ROOT_GID,
            mode=0o600,
            minimum=2,
            maximum=MAX_JSON_BYTES,
        )
    except FileNotFoundError:
        return None
    document = _strict_json_bytes(
        data, "CUSTODIAN_TRANSACTION_INVALID")
    _validate_transaction(document)
    return document


def _validate_transaction(document: dict[str, Any]) -> None:
    _require(
        set(document) == set(TRANSACTION_FIELDS) and
        document.get("schema") == "hepta.shadow-watch-custodian-state.v1" and
        document.get("version") == 1 and
        document.get("phase") in {
            "PREPARING", "PROVISION_PREPARING",
            "ACTIVE", "ROTATION_PREPARING",
            "CLOSING", "CLEANING"} and
        isinstance(document.get("domain_id"), str) and
        DOMAIN_ID.fullmatch(document["domain_id"]) is not None and
        isinstance(document.get("campaign_id"), str) and
        IDENTIFIER.fullmatch(document["campaign_id"]) is not None and
        type(document.get("owner_pid")) is int and
        type(document.get("owner_uid")) is int and
        type(document.get("owner_gid")) is int and
        type(document.get("owner_start_ticks")) is int and
        isinstance(document.get("owner_boot_id"), str) and
        BOOT_ID.fullmatch(document["owner_boot_id"]) is not None and
        type(document.get("lease_generation")) is int and
        1 <= document["lease_generation"] <= (1 << 64) - 1 and
        type(document.get("lease_expires_at_ms")) is int and
        type(document.get("registered_at_ms")) is int and
        document.get("paper_authorized") is False and
        document.get("live_authorized") is False and
        document.get("mutation_authorized") is False and
        document.get("direct_broker_access") is False,
        "CUSTODIAN_TRANSACTION_CONTRACT_INVALID",
    )
    _validate_digest_field(
        document.get("config_sha256"),
        "CUSTODIAN_TRANSACTION_DIGEST_INVALID",
    )
    _validate_digest_field(
        document.get("watch_environment_sha256"),
        "CUSTODIAN_TRANSACTION_DIGEST_INVALID",
    )
    _validate_digest_field(
        document.get("lease_receipt_body_sha256"),
        "CUSTODIAN_TRANSACTION_DIGEST_INVALID",
    )
    _validate_digest_field(
        document.get("fence_token_sha256"),
        "CUSTODIAN_TRANSACTION_DIGEST_INVALID",
    )
    _validate_absolute_path_field(
        document.get("token_directory"),
        "CUSTODIAN_TRANSACTION_RECOVERY_CONFIG_INVALID",
    )
    _validate_absolute_path_field(
        document.get("supervisor_socket"),
        "CUSTODIAN_TRANSACTION_RECOVERY_CONFIG_INVALID",
    )
    frozen_uids = (
        document.get("agent_uid"), document.get("gateway_uid"),
        document.get("execution_uid"), document.get("owner_uid"),
    )
    _require(
        all(
            type(value) is int and 1 <= value <= 4_294_967_295
            for value in frozen_uids
        ) and len(set(frozen_uids)) == len(frozen_uids) and
        type(document.get("agent_gid")) is int and
        1 <= document["agent_gid"] <= 4_294_967_295,
        "CUSTODIAN_TRANSACTION_RECOVERY_CONFIG_INVALID",
    )
    monitor_values = (
        document.get("monitor_pid"),
        document.get("monitor_start_ticks"),
        document.get("monitor_boot_id"),
    )
    _require(
        monitor_values == (None, None, None) or (
            type(monitor_values[0]) is int and
            type(monitor_values[1]) is int and
            isinstance(monitor_values[2], str) and
            BOOT_ID.fullmatch(monitor_values[2]) is not None
        ),
        "CUSTODIAN_TRANSACTION_MONITOR_INVALID",
    )
    if document["phase"] in {"CLOSING", "CLEANING"}:
        _require(
            document.get("close_reason") in CLOSE_REASONS and
            type(document.get("close_started_at_ms")) is int,
            "CUSTODIAN_TRANSACTION_CLOSE_INVALID",
        )
    else:
        _require(
            document.get("close_reason") is None and
            document.get("close_started_at_ms") is None,
            "CUSTODIAN_TRANSACTION_CLOSE_INVALID",
        )
    rotation_values = (
        document.get("rotation_expected_generation"),
        document.get("rotation_started_at_ms"),
    )
    if document["phase"] == "ROTATION_PREPARING":
        _require(
            type(rotation_values[0]) is int and
            rotation_values[0] == document["lease_generation"] + 1 and
            rotation_values[0] <= (1 << 64) - 1 and
            type(rotation_values[1]) is int and
            rotation_values[1] >= document["registered_at_ms"],
            "CUSTODIAN_TRANSACTION_ROTATION_INVALID",
        )
    else:
        _require(
            rotation_values == (None, None),
            "CUSTODIAN_TRANSACTION_ROTATION_INVALID",
        )
    provision_ttl = document.get("provision_ttl_seconds")
    if document["phase"] == "PROVISION_PREPARING":
        _require(
            type(provision_ttl) is int and 60 <= provision_ttl <= 3600 and
            document["lease_generation"] == 1,
            "CUSTODIAN_TRANSACTION_PROVISION_INVALID",
        )
    else:
        _require(
            provision_ttl is None,
            "CUSTODIAN_TRANSACTION_PROVISION_INVALID",
        )
    if document["phase"] == "CLEANING":
        _require(
            document.get("authoritative_revoke_outcome") in {
                "ACCEPTED", "ALREADY_ABSENT", "EXPIRED"},
            "CUSTODIAN_TRANSACTION_OUTCOME_INVALID",
        )
    else:
        _require(
            document.get("authoritative_revoke_outcome") is None,
            "CUSTODIAN_TRANSACTION_OUTCOME_INVALID",
        )
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    _require(
        claimed == _digest(_canonical(body)),
        "CUSTODIAN_TRANSACTION_DIGEST_INVALID",
    )


def _replace_transaction(
    directory: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    body = dict(state)
    body.pop("body_sha256", None)
    document = _body_document(body)
    _validate_transaction(document)
    _atomic_write(_transaction_path(directory), document, replace=True)
    return document


def _validate_active_pair(
    config: dict[str, Any],
) -> tuple[Path, Path, Path, str]:
    runtime = Path(str(config["token_directory"]))
    metadata = runtime.lstat()
    _require(
        stat.S_ISDIR(metadata.st_mode) and
        not stat.S_ISLNK(metadata.st_mode) and
        metadata.st_uid == ROOT_UID and metadata.st_gid == ROOT_GID and
        stat.S_IMODE(metadata.st_mode) == 0o711,
        "CUSTODIAN_RUNTIME_DIRECTORY_UNSAFE",
    )
    token = runtime / TOKEN_NAME
    fence = runtime / FENCE_TOKEN_NAME
    receipt = runtime / LEASE_RECEIPT_NAME
    token_data, token_metadata = _read_small_regular(
        token,
        uid=int(config["agent_uid"]),
        gid=int(config["agent_gid"]),
        mode=0o600,
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
    )
    fence_data, fence_metadata = _read_small_regular(
        fence,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o600,
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
    )
    _require(
        not _same_inode(token_metadata, fence_metadata) and
        hmac.compare_digest(token_data, fence_data),
        "CUSTODIAN_ACTIVE_BEARER_MISMATCH",
    )
    return token, fence, receipt, _digest(fence_data)


def _ensure_empty_runtime_directory(config: dict[str, Any]) -> Path:
    """Create only the final systemd-owned session directory when absent."""
    runtime = Path(str(config["token_directory"]))
    try:
        _safe_directory(runtime, mode=0o711, create=False)
    except FileNotFoundError:
        # The templated Gateway socket creates the exact root-owned parent.
        # Refuse to create through a missing or metadata-drifted parent.
        _safe_directory(runtime.parent, mode=0o711, create=False)
        _safe_directory(runtime, mode=0o711, create=True)
    return runtime


def _rotation_residue_candidate(
    config: dict[str, Any],
) -> tuple[
    Path, os.stat_result, Path, os.stat_result, bytes,
] | None:
    runtime = Path(str(config["token_directory"]))
    names = sorted(os.listdir(runtime))
    managed = [
        name for name in names
        if name.startswith(".session-token-") or
        name.startswith(".session-fence-")
    ]
    if not managed:
        return None
    token_matches = [
        (name, ROTATION_TOKEN_PATTERN.fullmatch(name))
        for name in managed
    ]
    fence_matches = [
        (name, ROTATION_FENCE_PATTERN.fullmatch(name))
        for name in managed
    ]
    tokens = [
        (name, match) for name, match in token_matches
        if match is not None
    ]
    fences = [
        (name, match) for name, match in fence_matches
        if match is not None
    ]
    _require(
        len(tokens) == 1 and len(fences) in {0, 1} and
        len(managed) == len(tokens) + len(fences) and
        (
            not fences or
            tokens[0][1].group(1) == fences[0][1].group(1)
        ),
        "CUSTODIAN_ROTATION_RESIDUE_INVENTORY_INVALID",
    )
    token_path = runtime / tokens[0][0]
    token_data, token_metadata = _read_small_regular(
        token_path,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o600,
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
    )
    fence_path = (
        runtime / fences[0][0]
        if fences else runtime / FENCE_TOKEN_NAME
    )
    fence_data, fence_metadata = _read_small_regular(
        fence_path,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o600,
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
    )
    _require(
        not _same_inode(token_metadata, fence_metadata) and
        hmac.compare_digest(token_data, fence_data),
        "CUSTODIAN_ROTATION_RESIDUE_BEARER_MISMATCH",
    )
    return (
        token_path, token_metadata,
        fence_path, fence_metadata, fence_data,
    )


def _provision_residue_candidate(
    config: dict[str, Any],
) -> tuple[
    Path, os.stat_result, Path, os.stat_result, bytes,
] | None:
    runtime = Path(str(config["token_directory"]))
    managed = sorted(
        name for name in os.listdir(runtime)
        if name.startswith(".session-token-") or
        name.startswith(".session-fence-")
    )
    if not managed:
        return None
    tokens = [
        (name, PROVISION_TOKEN_PATTERN.fullmatch(name))
        for name in managed
        if PROVISION_TOKEN_PATTERN.fullmatch(name) is not None
    ]
    fences = [
        (name, PROVISION_FENCE_PATTERN.fullmatch(name))
        for name in managed
        if PROVISION_FENCE_PATTERN.fullmatch(name) is not None
    ]
    _require(
        len(managed) == 2 and len(tokens) == 1 and len(fences) == 1 and
        tokens[0][1].group(1) == fences[0][1].group(1),
        "CUSTODIAN_PROVISION_RESIDUE_INVENTORY_INVALID",
    )
    token_path = runtime / tokens[0][0]
    fence_path = runtime / fences[0][0]
    token_data, token_metadata = _read_small_regular(
        token_path,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o600,
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
    )
    fence_data, fence_metadata = _read_small_regular(
        fence_path,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o600,
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
    )
    _require(
        not _same_inode(token_metadata, fence_metadata) and
        hmac.compare_digest(token_data, fence_data),
        "CUSTODIAN_PROVISION_RESIDUE_BEARER_MISMATCH",
    )
    return (
        token_path, token_metadata,
        fence_path, fence_metadata, fence_data,
    )


def _cleanup_rotation_residue(
    config: dict[str, Any],
    candidate: tuple[
        Path, os.stat_result, Path, os.stat_result, bytes,
    ],
) -> None:
    runtime = Path(str(config["token_directory"]))
    token_path, _token_metadata, fence_path, _fence_metadata, data = (
        candidate)
    _unlink_stable(
        token_path,
        allowed=((ROOT_UID, ROOT_GID, 0o600),),
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
        expected_contents=data,
    )
    if fence_path.name != FENCE_TOKEN_NAME:
        _unlink_stable(
            fence_path,
            allowed=((ROOT_UID, ROOT_GID, 0o600),),
            minimum=MIN_TOKEN_BYTES,
            maximum=MAX_TOKEN_BYTES,
            expected_contents=data,
        )
    directory_fd = os.open(
        runtime,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _rotation_fixed_pair(
    config: dict[str, Any],
) -> tuple[Path, bytes, os.stat_result, Path, bytes, os.stat_result]:
    runtime = Path(str(config["token_directory"]))
    token = runtime / TOKEN_NAME
    fence = runtime / FENCE_TOKEN_NAME
    token_before = token.lstat()
    token_owner = (
        token_before.st_uid, token_before.st_gid,
        stat.S_IMODE(token_before.st_mode),
    )
    allowed_token_owners = {
        (int(config["agent_uid"]), int(config["agent_gid"]), 0o600),
        (ROOT_UID, ROOT_GID, 0o600),
        (ROOT_UID, ROOT_GID, 0o400),
    }
    _require(
        token_owner in allowed_token_owners,
        "CUSTODIAN_ROTATION_FIXED_TOKEN_UNSAFE",
    )
    token_data, token_metadata = _read_small_regular(
        token,
        uid=token_before.st_uid,
        gid=token_before.st_gid,
        mode=stat.S_IMODE(token_before.st_mode),
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
    )
    fence_data, fence_metadata = _read_small_regular(
        fence,
        uid=ROOT_UID,
        gid=ROOT_GID,
        mode=0o600,
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
    )
    return (
        token, token_data, token_metadata,
        fence, fence_data, fence_metadata,
    )


def register(
    config_path: Path,
    campaign_id: str,
    owner_pid: int,
    owner_uid: int,
    generation: int,
) -> dict[str, Any]:
    _require(
        os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
        "CUSTODIAN_ROOT_REQUIRED",
    )
    _require(
        IDENTIFIER.fullmatch(campaign_id) is not None,
        "CUSTODIAN_CAMPAIGN_ID_INVALID",
    )
    _require(
        type(owner_uid) is int and 1 <= owner_uid <= 4_294_967_295,
        "CUSTODIAN_OWNER_UID_INVALID",
    )
    _require(
        type(generation) is int and 1 <= generation <= (1 << 64) - 1,
        "CUSTODIAN_GENERATION_INVALID",
    )
    config, config_sha256 = _load_config(config_path)
    reader_uid, reader_gid, watch_environment_sha256 = (
        _load_watch_reader_identity(config))
    _require(
        owner_uid not in {
            int(config["gateway_uid"]),
            int(config["agent_uid"]),
            int(config["execution_uid"]),
        },
        "CUSTODIAN_OWNER_ROLE_FORBIDDEN",
    )
    owner = _process_identity(owner_pid)
    _require(
        owner["uid"] == owner_uid and owner_uid == reader_uid and
        owner["gid"] == reader_gid and owner_uid != ROOT_UID,
        "CUSTODIAN_OWNER_IDENTITY_MISMATCH",
    )
    token, fence, receipt_path, fence_token_sha256 = (
        _validate_active_pair(config))
    del token
    lease = _validate_lease_receipt(receipt_path, config, generation)
    directory = _state_directory(str(config["domain_id"]), create=True)
    with _locked(directory):
        _require(
            _read_transaction(directory) is None,
            "CUSTODIAN_TRANSACTION_ALREADY_ACTIVE",
        )
        _cleanup_closed_export_tombstone(
            directory, str(config["domain_id"]))
        closure_path = (
            directory / CLOSURES_NAME / f"{campaign_id}.json")
        _require(
            not closure_path.exists(),
            "CUSTODIAN_CAMPAIGN_ID_REUSED",
        )
        _ensure_private_snapshot_directory(config)
        now = _now_ms()
        body: dict[str, Any] = {
            "schema": "hepta.shadow-watch-custodian-state.v1",
            "version": 1,
            "phase": "PREPARING",
            "domain_id": config["domain_id"],
            "campaign_id": campaign_id,
            "config_sha256": config_sha256,
            "watch_environment_sha256": watch_environment_sha256,
            "token_directory": str(config["token_directory"]),
            "supervisor_socket": str(config["supervisor_socket"]),
            "agent_uid": int(config["agent_uid"]),
            "agent_gid": int(config["agent_gid"]),
            "gateway_uid": int(config["gateway_uid"]),
            "execution_uid": int(config["execution_uid"]),
            "owner_pid": owner_pid,
            "owner_uid": owner_uid,
            "owner_gid": owner["gid"],
            "owner_start_ticks": owner["start_ticks"],
            "owner_boot_id": owner["boot_id"],
            "lease_generation": generation,
            "lease_receipt_body_sha256": lease["body_sha256"],
            "fence_token_sha256": fence_token_sha256,
            "lease_expires_at_ms": lease["expires_at_ms"],
            "registered_at_ms": now,
            "monitor_pid": None,
            "monitor_start_ticks": None,
            "monitor_boot_id": None,
            "provision_ttl_seconds": None,
            "rotation_expected_generation": None,
            "rotation_started_at_ms": None,
            "close_reason": None,
            "close_started_at_ms": None,
            "authoritative_revoke_outcome": None,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }
        preparing = _body_document(body)
        _validate_transaction(preparing)
        _atomic_write(
            _transaction_path(directory), preparing, replace=False)
        _fault("register.after_preparing_publish")
        # Bearer material deliberately remains runtime-only at the fixed
        # /run path. Durable state contains identity and receipt bindings only.
        del fence
        body["phase"] = "ACTIVE"
        active = _replace_transaction(directory, body)
        _fault("register.after_active_publish")
    return {
        "schema": "hepta.shadow-watch-custodian-registration.v1",
        "status": "REGISTERED",
        "domain_id": config["domain_id"],
        "campaign_id": campaign_id,
        "lease_generation": generation,
        "lease_expires_at_ms": lease["expires_at_ms"],
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
        "state_body_sha256": active["body_sha256"],
    }


def _provision_preparing_state(
    config_path: Path,
    campaign_id: str,
    owner_pid: int,
    owner_uid: int,
    ttl_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(
        IDENTIFIER.fullmatch(campaign_id) is not None and
        type(owner_uid) is int and 1 <= owner_uid <= 4_294_967_295 and
        type(ttl_seconds) is int and 60 <= ttl_seconds <= 3600,
        "CUSTODIAN_PROVISION_INPUT_INVALID",
    )
    config, config_sha256 = _load_config(config_path)
    reader_uid, reader_gid, watch_environment_sha256 = (
        _load_watch_reader_identity(config))
    owner = _process_identity(owner_pid)
    _require(
        owner_uid not in {
            int(config["gateway_uid"]),
            int(config["agent_uid"]),
            int(config["execution_uid"]),
        } and
        owner["uid"] == owner_uid and owner_uid == reader_uid and
        owner["gid"] == reader_gid and owner_uid != ROOT_UID,
        "CUSTODIAN_OWNER_IDENTITY_MISMATCH",
    )
    directory = _state_directory(str(config["domain_id"]), create=True)
    with _locked(directory):
        _require(
            _read_transaction(directory) is None,
            "CUSTODIAN_TRANSACTION_ALREADY_ACTIVE",
        )
        _cleanup_closed_export_tombstone(
            directory, str(config["domain_id"]))
        closure_path = (
            directory / CLOSURES_NAME / f"{campaign_id}.json")
        _require(
            not closure_path.exists(),
            "CUSTODIAN_CAMPAIGN_ID_REUSED",
        )
        _ensure_private_snapshot_directory(config)
        runtime = _ensure_empty_runtime_directory(config)
        _require(
            not any(
                (runtime / name).exists()
                for name in (
                    TOKEN_NAME, FENCE_TOKEN_NAME, LEASE_RECEIPT_NAME,
                )
            ) and
            not any(
                name.startswith(".session-token-") or
                name.startswith(".session-fence-")
                for name in os.listdir(runtime)
            ),
            "CUSTODIAN_PROVISION_RUNTIME_NOT_EMPTY",
        )
        now = _now_ms()
        body: dict[str, Any] = {
            "schema": "hepta.shadow-watch-custodian-state.v1",
            "version": 1,
            "phase": "PROVISION_PREPARING",
            "domain_id": config["domain_id"],
            "campaign_id": campaign_id,
            "config_sha256": config_sha256,
            "watch_environment_sha256": watch_environment_sha256,
            "token_directory": str(config["token_directory"]),
            "supervisor_socket": str(config["supervisor_socket"]),
            "agent_uid": int(config["agent_uid"]),
            "agent_gid": int(config["agent_gid"]),
            "gateway_uid": int(config["gateway_uid"]),
            "execution_uid": int(config["execution_uid"]),
            "owner_pid": owner_pid,
            "owner_uid": owner_uid,
            "owner_gid": owner["gid"],
            "owner_start_ticks": owner["start_ticks"],
            "owner_boot_id": owner["boot_id"],
            "lease_generation": 1,
            "lease_receipt_body_sha256": ZERO_DIGEST,
            "fence_token_sha256": ZERO_DIGEST,
            "lease_expires_at_ms": now + ttl_seconds * 1000,
            "registered_at_ms": now,
            "monitor_pid": None,
            "monitor_start_ticks": None,
            "monitor_boot_id": None,
            "provision_ttl_seconds": ttl_seconds,
            "rotation_expected_generation": None,
            "rotation_started_at_ms": None,
            "close_reason": None,
            "close_started_at_ms": None,
            "authoritative_revoke_outcome": None,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }
        preparing = _body_document(body)
        _validate_transaction(preparing)
        _atomic_write(
            _transaction_path(directory), preparing, replace=False)
        _fault("provision.after_preparing_publish")
    return config, preparing


def _invoke_bootstrap(
    config_path: Path,
    arguments: list[str],
) -> dict[str, Any]:
    _validate_root_executable(BOOTSTRAP)
    completed = subprocess.run(
        [BOOTSTRAP, "--domain-config", str(config_path), *arguments],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env={
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        },
        close_fds=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    _require(
        completed.returncode == 0 and
        len(completed.stdout) <= 4096 and
        len(completed.stderr) <= 4096,
        "CUSTODIAN_BOOTSTRAP_FAILED",
    )
    result = _strict_json_bytes(
        completed.stdout.encode("ascii"),
        "CUSTODIAN_BOOTSTRAP_RESULT_INVALID",
    )
    _require(
        set(result) == {
            "schema", "accepted", "operation", "trust_domain",
            "peer_uid", "lease_generation", "paper_authorized",
            "live_authorized",
        } and
        result.get("schema") == "hepta.agent-session-bootstrap.v1" and
        result.get("accepted") is True and
        result.get("paper_authorized") is False and
        result.get("live_authorized") is False,
        "CUSTODIAN_BOOTSTRAP_RESULT_INVALID",
    )
    return result


def _load_live_fence(
    config: dict[str, Any],
    expected_sha256: str,
) -> tuple[bytes, os.stat_result, Path]:
    path = Path(str(config["token_directory"])) / FENCE_TOKEN_NAME
    try:
        data, metadata = _read_small_regular(
            path,
            uid=ROOT_UID,
            gid=ROOT_GID,
            mode=0o600,
            minimum=MIN_TOKEN_BYTES,
            maximum=MAX_TOKEN_BYTES,
        )
    except FileNotFoundError as error:
        raise LiveFenceMissing("CUSTODIAN_LIVE_FENCE_MISSING") from error
    _require(
        _digest(data) == expected_sha256,
        "CUSTODIAN_LIVE_FENCE_DIGEST_MISMATCH",
    )
    return data, metadata, path


def _load_live_fence_optional(
    config: dict[str, Any],
    expected_sha256: str,
) -> tuple[bytes | None, os.stat_result | None, Path]:
    path = Path(str(config["token_directory"])) / FENCE_TOKEN_NAME
    try:
        data, metadata, _path = _load_live_fence(
            config, expected_sha256)
        return data, metadata, path
    except LiveFenceMissing:
        return None, None, path


def _quarantine_agent_token(
    config: dict[str, Any],
    live_fence: bytes | None,
) -> None:
    path = Path(str(config["token_directory"])) / TOKEN_NAME
    try:
        before = path.lstat()
    except FileNotFoundError:
        return
    safe_active = (
        stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode) and
        before.st_nlink == 1 and
        before.st_uid == int(config["agent_uid"]) and
        before.st_gid == int(config["agent_gid"]) and
        stat.S_IMODE(before.st_mode) == 0o600 and
        MIN_TOKEN_BYTES <= before.st_size <= MAX_TOKEN_BYTES
    )
    safe_quarantined = (
        stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode) and
        before.st_nlink == 1 and
        before.st_uid == ROOT_UID and before.st_gid == ROOT_GID and
        stat.S_IMODE(before.st_mode) == 0o400 and
        MIN_TOKEN_BYTES <= before.st_size <= MAX_TOKEN_BYTES
    )
    _require(
        safe_active or safe_quarantined,
        "CUSTODIAN_AGENT_TOKEN_UNSAFE",
    )
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        _require(
            _identity(opened) == _identity(before),
            "CUSTODIAN_AGENT_TOKEN_CHANGED",
        )
        data = bytearray()
        while len(data) <= MAX_TOKEN_BYTES:
            chunk = os.read(
                descriptor,
                min(256, MAX_TOKEN_BYTES + 1 - len(data)),
            )
            if not chunk:
                break
            data.extend(chunk)
        _require(
            len(data) == before.st_size and (
                live_fence is None or
                hmac.compare_digest(bytes(data), live_fence)
            ),
            "CUSTODIAN_AGENT_TOKEN_MISMATCH",
        )
        if safe_active:
            os.fchown(descriptor, ROOT_UID, ROOT_GID)
            os.fchmod(descriptor, 0o400)
            os.fsync(descriptor)
        after = os.fstat(descriptor)
        _require(
            _same_inode(before, after) and
            after.st_uid == ROOT_UID and after.st_gid == ROOT_GID and
            stat.S_IMODE(after.st_mode) == 0o400,
            "CUSTODIAN_AGENT_TOKEN_QUARANTINE_FAILED",
        )
    finally:
        os.close(descriptor)


def _require_state_bindings(
    config: dict[str, Any],
    config_sha256: str,
    state: dict[str, Any],
) -> None:
    _require(
        state["domain_id"] == config["domain_id"] and
        state["config_sha256"] == config_sha256,
        "CUSTODIAN_CONFIG_BINDING_DRIFT",
    )
    reader_uid, reader_gid, watch_environment_sha256 = (
        _load_watch_reader_identity(config))
    _require(
        state["owner_uid"] == reader_uid and
        state["owner_gid"] == reader_gid and
        state["watch_environment_sha256"] ==
        watch_environment_sha256,
        "CUSTODIAN_WATCH_ENVIRONMENT_BINDING_DRIFT",
    )


def _frozen_config(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain_id": state["domain_id"],
        "token_directory": state["token_directory"],
        "supervisor_socket": state["supervisor_socket"],
        "agent_uid": state["agent_uid"],
        "agent_gid": state["agent_gid"],
        "gateway_uid": state["gateway_uid"],
        "execution_uid": state["execution_uid"],
        "paper_authorized": False,
        "live_authorized": False,
    }


def _live_context_matches_state(
    config: dict[str, Any],
    config_sha256: str,
    state: dict[str, Any],
) -> bool:
    try:
        _require_state_bindings(config, config_sha256, state)
    except (
        CustodianError, TrustDomainRuntimeError, OSError, UnicodeError,
        ValueError,
    ):
        return False
    return all(
        config.get(field) == state[field]
        for field in (
            "domain_id", "token_directory", "supervisor_socket",
            "agent_uid", "agent_gid", "gateway_uid", "execution_uid",
        )
    )


def _domain_hint(config_path: Path) -> str:
    _require(
        config_path.name.endswith(".json"),
        "CUSTODIAN_DOMAIN_CONFIG_NAME_INVALID",
    )
    domain_id = config_path.name[:-5]
    _require(
        DOMAIN_ID.fullmatch(domain_id) is not None,
        "CUSTODIAN_DOMAIN_CONFIG_NAME_INVALID",
    )
    return domain_id


def _abort_rotation_locked(
    directory: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    _require(
        state["phase"] == "ROTATION_PREPARING",
        "CUSTODIAN_ROTATION_PHASE_INVALID",
    )
    body = dict(state)
    body.pop("body_sha256", None)
    body["phase"] = "ACTIVE"
    body["rotation_expected_generation"] = None
    body["rotation_started_at_ms"] = None
    return _replace_transaction(directory, body)


def _resolve_rotation_locked(
    config: dict[str, Any],
    directory: Path,
    state: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Recover or commit a prepared handoff from root-owned runtime evidence."""
    _require(
        state["phase"] == "ROTATION_PREPARING" and
        state["rotation_expected_generation"] ==
        state["lease_generation"] + 1,
        "CUSTODIAN_ROTATION_PHASE_INVALID",
    )
    expected_generation = int(state["rotation_expected_generation"])
    candidate_revoke_outcome: str | None = None
    residue = _rotation_residue_candidate(config)
    if residue is not None:
        try:
            candidate_revoke_outcome = _exact_revoke_path(
                config, expected_generation, residue[2], residue[3])
        except SessionNotFound:
            candidate_revoke_outcome = "ALREADY_ABSENT"
        _fault("rotation.after_residue_revoke")
        _cleanup_rotation_residue(config, residue)
        _fault("rotation.after_residue_cleanup")

    try:
        (
            _token_path, token_data, token_metadata,
            fence_path, fence_data, fence_metadata,
        ) = _rotation_fixed_pair(config)
        fence_sha256 = _digest(fence_data)
        pair_matches = (
            not _same_inode(token_metadata, fence_metadata) and
            hmac.compare_digest(token_data, fence_data)
        )
        receipt_path = (
            Path(str(config["token_directory"])) / LEASE_RECEIPT_NAME)
        if (
            pair_matches and
            fence_sha256 == state["fence_token_sha256"]
        ):
            lease = _validate_lease_receipt(
                receipt_path,
                config,
                int(state["lease_generation"]),
                allow_expired=True,
            )
            _require(
                lease["body_sha256"] ==
                state["lease_receipt_body_sha256"],
                "CUSTODIAN_ROTATION_OLD_RECEIPT_MISMATCH",
            )
            return state, "OLD_ACTIVE"

        lease: dict[str, Any] | None = None
        if candidate_revoke_outcome is None and pair_matches:
            try:
                lease = _validate_lease_receipt(
                    receipt_path, config, expected_generation)
            except (CustodianError, FileNotFoundError):
                lease = None
            if lease is not None:
                chain_matches = (
                    lease["operation"] == "ROTATE" and
                    lease["previous_lease_generation"] ==
                    state["lease_generation"] and
                    lease["previous_receipt_body_sha256"] ==
                    state["lease_receipt_body_sha256"]
                )
                if chain_matches:
                    # Complete fixed publication plus an exact root-owned
                    # chain receipt is the only continuation path.
                    _fault("rotation.after_candidate_validation")
                    body = dict(state)
                    body.pop("body_sha256", None)
                    body["phase"] = "ACTIVE"
                    body["lease_generation"] = expected_generation
                    body["lease_receipt_body_sha256"] = (
                        lease["body_sha256"])
                    body["fence_token_sha256"] = fence_sha256
                    body["lease_expires_at_ms"] = lease["expires_at_ms"]
                    body["rotation_expected_generation"] = None
                    body["rotation_started_at_ms"] = None
                    active = _replace_transaction(directory, body)
                    _fault("rotation.after_active_publish")
                    return active, "NEW_COMMITTED"

        # The supervisor may have accepted N+1 before fixed publication or
        # before its chain receipt. The matching candidate fence (residue or
        # fixed) must authoritatively revoke N+1 before any terminal cleanup.
        if candidate_revoke_outcome is None:
            try:
                candidate_revoke_outcome = _exact_revoke_path(
                    config,
                    expected_generation,
                    fence_path,
                    fence_metadata,
                )
            except SessionNotFound:
                candidate_revoke_outcome = "ALREADY_ABSENT"
        _fault("rotation.after_incomplete_candidate_revoke")
        _quarantine_agent_token(config, None)
    except (
        CustodianError, OSError, UnicodeError, ValueError,
    ):
        _quarantine_agent_token(config, None)
        raise
    _require(
        candidate_revoke_outcome in {"ACCEPTED", "ALREADY_ABSENT"},
        "CUSTODIAN_ROTATION_RECOVERY_OUTCOME_INVALID",
    )
    body = dict(state)
    body.pop("body_sha256", None)
    body["phase"] = "CLEANING"
    body["lease_generation"] = expected_generation
    body["fence_token_sha256"] = fence_sha256
    body["rotation_expected_generation"] = None
    body["rotation_started_at_ms"] = None
    body["close_reason"] = "rotation-recovery"
    body["close_started_at_ms"] = max(
        _now_ms(), int(state["registered_at_ms"]))
    body["authoritative_revoke_outcome"] = candidate_revoke_outcome
    cleaning = _replace_transaction(directory, body)
    _fault("rotation.after_recovery_commit")
    return cleaning, "NEW_REVOKED_INCOMPLETE"


def _resolve_provision_locked(
    config: dict[str, Any],
    directory: Path,
    state: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    _require(
        state["phase"] == "PROVISION_PREPARING" and
        state["lease_generation"] == 1,
        "CUSTODIAN_PROVISION_PHASE_INVALID",
    )
    try:
        _token, _fence, receipt_path, fence_sha256 = (
            _validate_active_pair(config))
        lease = _validate_lease_receipt(receipt_path, config, 1)
        _require(
            lease["operation"] == "PROVISION",
            "CUSTODIAN_PROVISION_RECEIPT_INVALID",
        )
        _fault("provision.after_candidate_validation")
        body = dict(state)
        body.pop("body_sha256", None)
        body["phase"] = "ACTIVE"
        body["lease_receipt_body_sha256"] = lease["body_sha256"]
        body["fence_token_sha256"] = fence_sha256
        body["lease_expires_at_ms"] = lease["expires_at_ms"]
        body["provision_ttl_seconds"] = None
        active = _replace_transaction(directory, body)
        _fault("provision.after_active_publish")
        return active, "ACTIVE_COMMITTED"
    except (
        CustodianError, FileNotFoundError, OSError,
        UnicodeError, ValueError,
    ):
        pass

    candidate_outcome: str | None = None
    candidate_digest = ZERO_DIGEST
    residue = _provision_residue_candidate(config)
    if residue is not None:
        candidate_digest = _digest(residue[4])
        try:
            candidate_outcome = _exact_revoke_path(
                config, 1, residue[2], residue[3])
        except SessionNotFound:
            candidate_outcome = "ALREADY_ABSENT"
        _fault("provision.after_residue_revoke")
        _cleanup_rotation_residue(config, residue)
        _fault("provision.after_residue_cleanup")

    runtime = Path(str(config["token_directory"]))
    fixed_presence = (
        (runtime / TOKEN_NAME).exists(),
        (runtime / FENCE_TOKEN_NAME).exists(),
    )
    _require(
        fixed_presence in {(False, False), (True, True)},
        "CUSTODIAN_PROVISION_FIXED_PAIR_INCOMPLETE",
    )
    if fixed_presence == (True, True):
        (
            _token_path, _token_data, _token_metadata,
            fence_path, fence_data, fence_metadata,
        ) = _rotation_fixed_pair(config)
        candidate_digest = _digest(fence_data)
        if candidate_outcome is None:
            try:
                candidate_outcome = _exact_revoke_path(
                    config, 1, fence_path, fence_metadata)
            except SessionNotFound:
                candidate_outcome = "ALREADY_ABSENT"
        _quarantine_agent_token(config, None)
    elif candidate_outcome is None:
        candidate_outcome = "ALREADY_ABSENT"

    _require(
        candidate_outcome in {"ACCEPTED", "ALREADY_ABSENT"},
        "CUSTODIAN_PROVISION_RECOVERY_OUTCOME_INVALID",
    )
    body = dict(state)
    body.pop("body_sha256", None)
    body["phase"] = "CLEANING"
    body["fence_token_sha256"] = candidate_digest
    body["provision_ttl_seconds"] = None
    body["close_reason"] = "registration-recovery"
    body["close_started_at_ms"] = max(
        _now_ms(), int(state["registered_at_ms"]))
    body["authoritative_revoke_outcome"] = candidate_outcome
    cleaning = _replace_transaction(directory, body)
    _fault("provision.after_recovery_commit")
    return cleaning, "PROVISION_REVOKED_INCOMPLETE"


def _commit_provision(
    config_path: Path,
    campaign_id: str,
) -> dict[str, Any]:
    config, config_sha256 = _load_config(config_path)
    directory = _state_directory(str(config["domain_id"]), create=True)
    with _locked(directory):
        state = _read_transaction(directory)
        _require(
            state is not None and
            state["phase"] == "PROVISION_PREPARING" and
            state["campaign_id"] == campaign_id,
            "CUSTODIAN_PROVISION_PHASE_INVALID",
        )
        _require_state_bindings(config, config_sha256, state)
        resolved, outcome = _resolve_provision_locked(
            config, directory, state)
        if outcome == "PROVISION_REVOKED_INCOMPLETE":
            _close_locked(
                config, config_sha256, directory, resolved,
                "registration-recovery")
            raise CustodianError("CUSTODIAN_PROVISION_NOT_COMMITTED")
        return resolved


def provision(
    config_path: Path,
    campaign_id: str,
    owner_pid: int,
    owner_uid: int,
    ttl_seconds: int,
) -> dict[str, Any]:
    _require(
        os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
        "CUSTODIAN_ROOT_REQUIRED",
    )
    config, preparing = _provision_preparing_state(
        config_path, campaign_id, owner_pid, owner_uid, ttl_seconds)
    _fault("provision.before_bootstrap")
    try:
        result = _invoke_bootstrap(
            config_path,
            [
                "provision-watch",
                "--agent-id", str(config["domain_id"]),
                "--session-id", campaign_id,
                "--ttl-sec", str(ttl_seconds),
            ],
        )
    except (
        CustodianError, OSError, UnicodeError, ValueError,
        subprocess.SubprocessError,
    ):
        directory = _state_directory(
            str(config["domain_id"]), create=True)
        with _locked(directory):
            state = _read_transaction(directory)
            if state is not None and state["phase"] == "PROVISION_PREPARING":
                recovered, outcome = _resolve_provision_locked(
                    config, directory, state)
                if outcome == "PROVISION_REVOKED_INCOMPLETE":
                    _close_locked(
                        config, str(state["config_sha256"]),
                        directory, recovered, "registration-recovery")
        raise
    _require(
        result["operation"] == "provision-watch" and
        result["trust_domain"] == config["domain_id"] and
        result["peer_uid"] == config["agent_uid"] and
        result["lease_generation"] == 1,
        "CUSTODIAN_BOOTSTRAP_RESULT_BINDING_INVALID",
    )
    _fault("provision.after_bootstrap")
    active = _commit_provision(config_path, campaign_id)
    return {
        "schema": "hepta.shadow-watch-custodian-registration.v1",
        "status": "REGISTERED",
        "domain_id": config["domain_id"],
        "campaign_id": campaign_id,
        "lease_generation": 1,
        "lease_expires_at_ms": active["lease_expires_at_ms"],
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
        "state_body_sha256": active["body_sha256"],
        "preparing_state_body_sha256": preparing["body_sha256"],
    }


def prepare_rotation(
    config_path: Path,
    campaign_id: str,
    current_generation: int,
) -> dict[str, Any]:
    _require(
        os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
        "CUSTODIAN_ROOT_REQUIRED",
    )
    _require(
        isinstance(campaign_id, str) and
        IDENTIFIER.fullmatch(campaign_id) is not None,
        "CUSTODIAN_CAMPAIGN_ID_INVALID",
    )
    _require(
        type(current_generation) is int and
        1 <= current_generation < (1 << 64) - 1,
        "CUSTODIAN_GENERATION_INVALID",
    )
    config, config_sha256 = _load_config(config_path)
    directory = _state_directory(str(config["domain_id"]), create=True)
    with _locked(directory):
        state = _read_transaction(directory)
        _require(
            state is not None and state["phase"] == "ACTIVE",
            "CUSTODIAN_ROTATION_PHASE_INVALID",
        )
        _require_state_bindings(config, config_sha256, state)
        _require(
            state["campaign_id"] == campaign_id and
            state["lease_generation"] == current_generation,
            "CUSTODIAN_ROTATION_OLD_TRANSACTION_MISMATCH",
        )
        _require(
            _process_matches(
                int(state["owner_pid"]),
                int(state["owner_uid"]),
                int(state["owner_gid"]),
                int(state["owner_start_ticks"]),
                str(state["owner_boot_id"]),
            ),
            "CUSTODIAN_ROTATION_OWNER_NOT_ACTIVE",
        )
        _token, _fence, receipt_path, fence_sha256 = (
            _validate_active_pair(config))
        lease = _validate_lease_receipt(
            receipt_path, config, current_generation)
        _require(
            fence_sha256 == state["fence_token_sha256"] and
            lease["body_sha256"] ==
            state["lease_receipt_body_sha256"],
            "CUSTODIAN_ROTATION_OLD_AUTHORITY_MISMATCH",
        )
        body = dict(state)
        body.pop("body_sha256", None)
        body["phase"] = "ROTATION_PREPARING"
        body["rotation_expected_generation"] = current_generation + 1
        body["rotation_started_at_ms"] = max(
            _now_ms(), int(state["registered_at_ms"]))
        preparing = _replace_transaction(directory, body)
        _fault("rotation.after_preparing_publish")
    return {
        "schema": "hepta.shadow-watch-custodian-rotation.v1",
        "status": "ROTATION_PREPARED",
        "domain_id": state["domain_id"],
        "campaign_id": campaign_id,
        "previous_lease_generation": current_generation,
        "expected_lease_generation": current_generation + 1,
        "previous_receipt_body_sha256":
        state["lease_receipt_body_sha256"],
        "state_body_sha256": preparing["body_sha256"],
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }


def commit_rotation(
    config_path: Path,
    campaign_id: str,
    new_generation: int,
) -> dict[str, Any]:
    _require(
        os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
        "CUSTODIAN_ROOT_REQUIRED",
    )
    _require(
        isinstance(campaign_id, str) and
        IDENTIFIER.fullmatch(campaign_id) is not None,
        "CUSTODIAN_CAMPAIGN_ID_INVALID",
    )
    _require(
        type(new_generation) is int and
        2 <= new_generation <= (1 << 64) - 1,
        "CUSTODIAN_GENERATION_INVALID",
    )
    config, config_sha256 = _load_config(config_path)
    directory = _state_directory(str(config["domain_id"]), create=True)
    with _locked(directory):
        state = _read_transaction(directory)
        _require(
            state is not None and
            state["phase"] == "ROTATION_PREPARING",
            "CUSTODIAN_ROTATION_PHASE_INVALID",
        )
        _require_state_bindings(config, config_sha256, state)
        _require(
            state["campaign_id"] == campaign_id and
            state["rotation_expected_generation"] == new_generation,
            "CUSTODIAN_ROTATION_NEW_TRANSACTION_MISMATCH",
        )
        _require(
            _process_matches(
                int(state["owner_pid"]),
                int(state["owner_uid"]),
                int(state["owner_gid"]),
                int(state["owner_start_ticks"]),
                str(state["owner_boot_id"]),
            ),
            "CUSTODIAN_ROTATION_OWNER_NOT_ACTIVE",
        )
        active, outcome = _resolve_rotation_locked(
            config, directory, state)
        _require(
            outcome == "NEW_COMMITTED",
            "CUSTODIAN_ROTATION_NOT_PUBLISHED",
        )
    return {
        "schema": "hepta.shadow-watch-custodian-rotation.v1",
        "status": "ROTATED",
        "domain_id": active["domain_id"],
        "campaign_id": campaign_id,
        "previous_lease_generation": new_generation - 1,
        "lease_generation": new_generation,
        "lease_expires_at_ms": active["lease_expires_at_ms"],
        "lease_receipt_body_sha256":
        active["lease_receipt_body_sha256"],
        "state_body_sha256": active["body_sha256"],
        "previous_authority_outcome": "ROTATED",
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }


def rotate(
    config_path: Path,
    campaign_id: str,
    current_generation: int,
    ttl_seconds: int,
) -> dict[str, Any]:
    _require(
        os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID and
        type(ttl_seconds) is int and 60 <= ttl_seconds <= 3600,
        "CUSTODIAN_ROTATION_INPUT_INVALID",
    )
    prepared = prepare_rotation(
        config_path, campaign_id, current_generation)
    _fault("rotation.before_bootstrap")
    result = _invoke_bootstrap(
        config_path,
        [
            "rotate",
            "--generation", str(current_generation),
            "--ttl-sec", str(ttl_seconds),
        ],
    )
    _require(
        result["operation"] == "rotate" and
        result["trust_domain"] == prepared["domain_id"] and
        result["lease_generation"] == current_generation + 1,
        "CUSTODIAN_BOOTSTRAP_RESULT_BINDING_INVALID",
    )
    _fault("rotation.after_bootstrap")
    return commit_rotation(
        config_path, campaign_id, current_generation + 1)


def _validate_root_executable(path: str) -> None:
    before = os.lstat(path)
    _require(
        stat.S_ISREG(before.st_mode) and
        not stat.S_ISLNK(before.st_mode) and before.st_nlink == 1 and
        before.st_uid == ROOT_UID and before.st_gid == ROOT_GID and
        stat.S_IMODE(before.st_mode) == 0o755 and
        1 <= before.st_size <= 64 * 1024 * 1024,
        "CUSTODIAN_SESSIONCTL_UNSAFE",
    )
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        _require(
            _identity(before) == _identity(os.fstat(descriptor)),
            "CUSTODIAN_SESSIONCTL_CHANGED",
        )
    finally:
        os.close(descriptor)


def _exact_revoke_path(
    config: dict[str, Any],
    generation: int,
    fence_path: Path,
    fence_metadata: os.stat_result,
) -> str:
    _validate_root_executable(SESSIONCTL)
    completed = subprocess.run(
        [
            SESSIONCTL,
            "--socket", str(config["supervisor_socket"]),
            "revoke",
            "--token-file", str(fence_path),
            "--token-owner-uid", str(fence_metadata.st_uid),
            "--generation", str(generation),
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        env={
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        },
        close_fds=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    _require(
        len(completed.stdout) <= 4096 and len(completed.stderr) <= 4096,
        "CUSTODIAN_REVOKE_RESULT_TOO_LARGE",
    )
    try:
        result = json.loads(
            completed.stdout,
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                CustodianError("CUSTODIAN_REVOKE_RESULT_INVALID")),
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise CustodianError(
            "CUSTODIAN_REVOKE_RESULT_INVALID") from error
    if (
            completed.returncode == 4 and isinstance(result, dict) and
            set(result) == {
                "accepted", "reason_code", "lease_generation"} and
            result.get("accepted") is False and
            result.get("reason_code") in {
                "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND"} and
            result.get("lease_generation") == generation):
        raise SessionNotFound(str(result["reason_code"]))
    _require(
        completed.returncode == 0 and isinstance(result, dict) and
        set(result) == {
            "accepted", "reason_code", "lease_generation"} and
        result.get("accepted") is True and
        result.get("reason_code") == "OK" and
        result.get("lease_generation") == generation,
        "CUSTODIAN_REVOKE_RESULT_INVALID",
    )
    return "ACCEPTED"


def _exact_revoke(
    config: dict[str, Any],
    generation: int,
    expected_fence_sha256: str,
) -> str:
    _fence, fence_metadata, fence_path = _load_live_fence(
        config, expected_fence_sha256)
    return _exact_revoke_path(
        config, generation, fence_path, fence_metadata)


def _unlink_stable(
    path: Path,
    *,
    allowed: tuple[tuple[int, int, int], ...],
    minimum: int,
    maximum: int,
    expected_contents: bytes | None = None,
) -> bool:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return False
    _require(
        stat.S_ISREG(before.st_mode) and
        not stat.S_ISLNK(before.st_mode) and before.st_nlink == 1 and
        (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
        in allowed and minimum <= before.st_size <= maximum,
        "CUSTODIAN_CLEANUP_FILE_UNSAFE",
    )
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(
                descriptor, min(8192, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        opened = os.fstat(descriptor)
        path_after = path.lstat()
        _require(
            len(data) == before.st_size and
            _identity(before) == _identity(opened) ==
            _identity(path_after) and
            (
                expected_contents is None or
                hmac.compare_digest(bytes(data), expected_contents)
            ),
            "CUSTODIAN_CLEANUP_FILE_CHANGED",
        )
        os.unlink(path)
    finally:
        os.close(descriptor)
    return True


def _cleanup_local_authority(
    config: dict[str, Any],
    state: dict[str, Any],
    live_fence: bytes | None,
) -> None:
    runtime = Path(str(config["token_directory"]))
    token = runtime / TOKEN_NAME
    fence = runtime / FENCE_TOKEN_NAME
    receipt = runtime / LEASE_RECEIPT_NAME
    _unlink_stable(
        token,
        allowed=(
            (ROOT_UID, ROOT_GID, 0o400),
            (ROOT_UID, ROOT_GID, 0o600),
            (int(config["agent_uid"]), int(config["agent_gid"]), 0o600),
        ),
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
        expected_contents=(
            None if state.get("close_reason") == "rotation-recovery"
            else live_fence),
    )
    _unlink_stable(
        fence,
        allowed=((ROOT_UID, ROOT_GID, 0o600),),
        minimum=MIN_TOKEN_BYTES,
        maximum=MAX_TOKEN_BYTES,
        expected_contents=live_fence,
    )
    try:
        receipt_data, _receipt_metadata = _read_small_regular(
            receipt,
            uid=ROOT_UID,
            gid=int(config["agent_gid"]),
            mode=0o440,
            minimum=2,
            maximum=MAX_JSON_BYTES,
        )
        receipt_document = _strict_json_bytes(
            receipt_data, "CUSTODIAN_LEASE_RECEIPT_INVALID")
        receipt_generation = receipt_document.get("lease_generation")
        _require(
            type(receipt_generation) is int,
            "CUSTODIAN_LEASE_RECEIPT_BINDING_INVALID",
        )
        lease = _validate_lease_receipt(
            receipt,
            config,
            receipt_generation,
            allow_expired=True,
        )
    except FileNotFoundError:
        lease = None
    if lease is not None:
        _require(
            lease["body_sha256"] ==
            state["lease_receipt_body_sha256"],
            "CUSTODIAN_LEASE_RECEIPT_STATE_MISMATCH",
        )
        _unlink_stable(
            receipt,
            allowed=((ROOT_UID, int(config["agent_gid"]), 0o440),),
            minimum=2,
            maximum=MAX_JSON_BYTES,
        )
    try:
        directory_fd = os.open(
            runtime,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        # /run is ephemeral. Once every exact managed path above is absent,
        # an absent session directory is already complete local cleanup.
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _private_state_name(domain_id: str) -> str:
    _require(
        DOMAIN_ID.fullmatch(domain_id) is not None,
        "CUSTODIAN_DOMAIN_ID_INVALID",
    )
    return f"hepta-shadow-watch-{domain_id}"


def _directory_anchor_matches(
    descriptor_metadata: os.stat_result,
    path_metadata: os.stat_result,
    *,
    uid: int,
    gid: int,
) -> bool:
    return (
        stat.S_ISDIR(descriptor_metadata.st_mode) and
        stat.S_ISDIR(path_metadata.st_mode) and
        descriptor_metadata.st_dev == path_metadata.st_dev and
        descriptor_metadata.st_ino == path_metadata.st_ino and
        descriptor_metadata.st_uid == path_metadata.st_uid == uid and
        descriptor_metadata.st_gid == path_metadata.st_gid == gid and
        stat.S_IMODE(descriptor_metadata.st_mode) ==
        stat.S_IMODE(path_metadata.st_mode) == 0o700
    )


def _ensure_private_snapshot_directory(config: dict[str, Any]) -> None:
    """Create the empty collector directory before the custodian unit starts."""

    domain_id = str(config["domain_id"])
    agent_uid = int(config["agent_uid"])
    agent_gid = int(config["agent_gid"])
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0)
    )
    root_fd = os.open(WATCH_STATE_ROOT, flags)
    state_fd = -1
    private_fd = -1
    try:
        root_metadata = os.fstat(root_fd)
        _require(
            stat.S_ISDIR(root_metadata.st_mode) and
            root_metadata.st_uid == ROOT_UID and
            root_metadata.st_gid == ROOT_GID and
            stat.S_IMODE(root_metadata.st_mode) & 0o022 == 0,
            "CUSTODIAN_PRIVATE_ROOT_UNSAFE",
        )
        state_name = _private_state_name(domain_id)
        try:
            state_fd = os.open(state_name, flags, dir_fd=root_fd)
        except FileNotFoundError:
            os.mkdir(state_name, 0o700, dir_fd=root_fd)
            state_fd = os.open(state_name, flags, dir_fd=root_fd)
            os.fchown(state_fd, agent_uid, agent_gid)
            os.fchmod(state_fd, 0o700)
            os.fsync(root_fd)
        state_metadata = os.fstat(state_fd)
        state_path_metadata = os.stat(
            state_name, dir_fd=root_fd, follow_symlinks=False)
        _require(
            _directory_anchor_matches(
                state_metadata, state_path_metadata,
                uid=agent_uid, gid=agent_gid),
            "CUSTODIAN_PRIVATE_DIRECTORY_UNSAFE",
        )
        try:
            private_fd = os.open("private", flags, dir_fd=state_fd)
        except FileNotFoundError:
            os.mkdir("private", 0o700, dir_fd=state_fd)
            private_fd = os.open("private", flags, dir_fd=state_fd)
            os.fchown(private_fd, agent_uid, agent_gid)
            os.fchmod(private_fd, 0o700)
            os.fsync(state_fd)
        private_metadata = os.fstat(private_fd)
        private_path_metadata = os.stat(
            "private", dir_fd=state_fd, follow_symlinks=False)
        _require(
            _directory_anchor_matches(
                private_metadata, private_path_metadata,
                uid=agent_uid, gid=agent_gid) and
            not os.listdir(private_fd),
            "CUSTODIAN_PRIVATE_DIRECTORY_UNSAFE",
        )
    finally:
        if private_fd >= 0:
            os.close(private_fd)
        if state_fd >= 0:
            os.close(state_fd)
        os.close(root_fd)


@contextmanager
def _opened_private_snapshot(
    domain_id: str,
    allowed_identities: set[tuple[int, int]],
) -> Iterator[
    tuple[
        int, int, os.stat_result, bytes, dict[str, Any], int, int,
    ] | None
]:
    flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0)
    )
    root_fd = os.open(WATCH_STATE_ROOT, flags)
    state_fd = -1
    private_fd = -1
    snapshot_fd = -1
    try:
        root_metadata = os.fstat(root_fd)
        _require(
            stat.S_ISDIR(root_metadata.st_mode) and
            root_metadata.st_uid == ROOT_UID and
            root_metadata.st_gid == ROOT_GID and
            stat.S_IMODE(root_metadata.st_mode) & 0o022 == 0,
            "CUSTODIAN_PRIVATE_ROOT_UNSAFE",
        )
        state_name = _private_state_name(domain_id)
        try:
            state_fd = os.open(state_name, flags, dir_fd=root_fd)
            private_fd = os.open("private", flags, dir_fd=state_fd)
        except FileNotFoundError:
            yield None
            return
        state_metadata = os.fstat(state_fd)
        state_path_metadata = os.stat(
            state_name, dir_fd=root_fd, follow_symlinks=False)
        private_metadata = os.fstat(private_fd)
        private_path_metadata = os.stat(
            "private", dir_fd=state_fd, follow_symlinks=False)
        agent_identity = (
            int(private_metadata.st_uid), int(private_metadata.st_gid))
        _require(
            _directory_anchor_matches(
                state_metadata, state_path_metadata,
                uid=agent_identity[0], gid=agent_identity[1]) and
            _directory_anchor_matches(
                private_metadata, private_path_metadata,
                uid=agent_identity[0], gid=agent_identity[1]),
            "CUSTODIAN_PRIVATE_DIRECTORY_UNSAFE",
        )
        names = sorted(os.listdir(private_fd))
        _require(
            set(names).issubset({"snapshot.json"}),
            "CUSTODIAN_PRIVATE_INVENTORY_UNSAFE",
        )
        if not names:
            yield None
            return
        _require(
            agent_identity in allowed_identities,
            "CUSTODIAN_PRIVATE_SNAPSHOT_UNBOUND",
        )
        before = os.stat(
            "snapshot.json", dir_fd=private_fd, follow_symlinks=False)
        _require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
            before.st_uid == agent_identity[0] and
            before.st_gid == agent_identity[1] and
            stat.S_IMODE(before.st_mode) == 0o600 and
            2 <= before.st_size <= MAX_EXPORT_BYTES,
            "CUSTODIAN_FILE_METADATA_UNSAFE",
        )
        snapshot_fd = os.open(
            "snapshot.json",
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=private_fd,
        )
        opened = os.fstat(snapshot_fd)
        data = bytearray()
        while len(data) <= MAX_EXPORT_BYTES:
            chunk = os.read(
                snapshot_fd,
                min(8192, MAX_EXPORT_BYTES + 1 - len(data)),
            )
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(snapshot_fd)
        path_after = os.stat(
            "snapshot.json", dir_fd=private_fd, follow_symlinks=False)
        _require(
            len(data) == before.st_size and
            _identity(before) == _identity(opened) ==
            _identity(after) == _identity(path_after),
            "CUSTODIAN_FILE_CHANGED",
        )
        contents = bytes(data)
        snapshot = _strict_json_bytes(
            contents, "CUSTODIAN_PRIVATE_SNAPSHOT_INVALID")
        schema = snapshot.get("schema")
        version = snapshot.get("version")
        fields_valid = (
            schema == "hepta.shadow-watch-snapshot.v1" and
            version == 1 and set(snapshot) == set(SNAPSHOT_V1_FIELDS)
        ) or (
            schema == "hepta.shadow-watch-snapshot.v2" and
            version == 2 and set(snapshot) == set(SNAPSHOT_V2_FIELDS)
        )
        generated_at_ms = snapshot.get("generated_at_ms")
        body = dict(snapshot)
        claimed = body.pop("body_sha256", None)
        _require(
            fields_valid and snapshot.get("domain_id") == domain_id and
            snapshot.get("agent_uid") == agent_identity[0] and
            type(generated_at_ms) is int and generated_at_ms >= 0 and
            snapshot.get("paper_authorized") is False and
            snapshot.get("live_authorized") is False and
            snapshot.get("mutation_attempted") is False and
            snapshot.get("direct_broker_access") is False and
            isinstance(claimed, str) and
            DIGEST.fullmatch(claimed) is not None and
            claimed == _digest(_canonical(body)),
            "CUSTODIAN_PRIVATE_SNAPSHOT_INVALID",
        )
        yield (
            state_fd, private_fd, before, contents, snapshot,
            agent_identity[0], agent_identity[1],
        )
    finally:
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        if private_fd >= 0:
            os.close(private_fd)
        if state_fd >= 0:
            os.close(state_fd)
        os.close(root_fd)


def _remove_opened_private_snapshot(
    state_fd: int,
    private_fd: int,
    snapshot_metadata: os.stat_result,
    *,
    agent_uid: int,
    agent_gid: int,
) -> None:
    private_metadata = os.fstat(private_fd)
    bound_private = os.stat(
        "private", dir_fd=state_fd, follow_symlinks=False)
    current = os.stat(
        "snapshot.json", dir_fd=private_fd, follow_symlinks=False)
    _require(
        _directory_anchor_matches(
            private_metadata, bound_private,
            uid=agent_uid, gid=agent_gid) and
        _identity(current) == _identity(snapshot_metadata),
        "CUSTODIAN_PRIVATE_SNAPSHOT_CHANGED",
    )
    os.unlink("snapshot.json", dir_fd=private_fd)
    os.fsync(private_fd)
    rebound_private = os.stat(
        "private", dir_fd=state_fd, follow_symlinks=False)
    _require(
        _directory_anchor_matches(
            os.fstat(private_fd), rebound_private,
            uid=agent_uid, gid=agent_gid) and
        not os.listdir(private_fd),
        "CUSTODIAN_PRIVATE_DIRECTORY_CHANGED",
    )


def _cleanup_private_snapshot_for_state(state: dict[str, Any]) -> bool:
    identity = (int(state["agent_uid"]), int(state["agent_gid"]))
    with _opened_private_snapshot(
            str(state["domain_id"]), {identity}) as candidate:
        if candidate is None:
            return True
        (state_fd, private_fd, metadata, _data, snapshot,
         agent_uid, agent_gid) = candidate
        _require(
            int(state["registered_at_ms"]) <=
            int(snapshot["generated_at_ms"]) <=
            int(state["close_started_at_ms"]),
            "CUSTODIAN_PRIVATE_SNAPSHOT_UNBOUND",
        )
        _remove_opened_private_snapshot(
            state_fd,
            private_fd,
            metadata,
            agent_uid=agent_uid,
            agent_gid=agent_gid,
        )
    return True


def _cleanup_closed_private_snapshot(
    directory: Path,
    domain_id: str,
) -> bool:
    closure_directory = directory / CLOSURES_NAME
    names = sorted(os.listdir(closure_directory))
    _require(
        len(names) <= 4096 and all(
            name.endswith(".json") and len(name) > len(".json")
            for name in names
        ),
        "CUSTODIAN_CLOSURE_INVENTORY_INVALID",
    )
    closures: list[dict[str, Any]] = []
    for name in names:
        closure = _read_closure(closure_directory / name)
        _require(
            closure is not None and
            name == f"{closure['campaign_id']}.json",
            "CUSTODIAN_CLOSURE_INVENTORY_INVALID",
        )
        closures.append(closure)
    identities = {
        (int(closure["agent_uid"]), int(closure["agent_gid"]))
        for closure in closures if closure["domain_id"] == domain_id
    }
    with _opened_private_snapshot(domain_id, identities) as candidate:
        if candidate is None:
            return True
        (state_fd, private_fd, metadata, _data, snapshot,
         agent_uid, agent_gid) = candidate
        generated_at_ms = int(snapshot["generated_at_ms"])
        matches = [
            closure for closure in closures
            if (
                closure["domain_id"] == domain_id and
                closure["agent_uid"] == agent_uid and
                closure["agent_gid"] == agent_gid and
                closure["registered_at_ms"] <= generated_at_ms <=
                closure["close_started_at_ms"] and
                closure["local_authority_removed"] is True and
                closure["export_evidence_removed"] is True
            )
        ]
        _require(
            len(matches) == 1,
            "CUSTODIAN_PRIVATE_SNAPSHOT_UNBOUND",
        )
        _remove_opened_private_snapshot(
            state_fd,
            private_fd,
            metadata,
            agent_uid=agent_uid,
            agent_gid=agent_gid,
        )
    return True


def _validate_export_commit(
        document: dict[str, Any],
        config: dict[str, Any],
        state: dict[str, Any],
) -> None:
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    status_value = document.get("authority_status")
    sequence = document.get("commit_sequence")
    generation = document.get("generation")
    binding_fields = (
        "lease_generation", "snapshot_body_sha256", "snapshot_file_sha256",
        "lease_receipt_body_sha256", "lease_receipt_file_sha256",
        "export_receipt_body_sha256", "export_receipt_file_sha256",
        "committed_at_ms",
    )
    common = (
        set(document) == EXPORT_COMMIT_FIELDS and
        document.get("schema") == "hepta.shadow-watch-export-commit.v1" and
        document.get("version") == 1 and
        status_value in {"ACTIVE", "CLOSING", "CLOSED"} and
        type(document.get("authority_changed_at_ms")) is int and
        document["authority_changed_at_ms"] >= 0 and
        type(sequence) is int and 1 <= sequence < (1 << 64) and
        document.get("domain_id") == config["domain_id"] and
        document.get("agent_uid") == config["agent_uid"] and
        document.get("reader_uid") == state["owner_uid"] and
        document.get("reader_gid") == state["owner_gid"] and
        document.get("paper_authorized") is False and
        document.get("live_authorized") is False and
        document.get("mutation_attempted") is False and
        document.get("direct_broker_access") is False and
        isinstance(claimed, str) and DIGEST.fullmatch(claimed) is not None and
        hmac.compare_digest(claimed, _digest(_canonical(body)))
    )
    if status_value == "ACTIVE":
        active = (
            isinstance(generation, str) and
            EXPORT_GENERATION.fullmatch(generation) is not None and
            int(EXPORT_GENERATION.fullmatch(generation).group(1)) == sequence and
            document.get("close_reason") is None and
            type(document.get("lease_generation")) is int and
            1 <= document["lease_generation"] <= state["lease_generation"] and
            (
                document["lease_generation"] != state["lease_generation"] or
                document.get("lease_receipt_body_sha256") ==
                state["lease_receipt_body_sha256"]
            ) and
            all(
                isinstance(document.get(field), str) and
                DIGEST.fullmatch(document[field]) is not None
                for field in binding_fields[1:7]) and
            type(document.get("committed_at_ms")) is int and
            document["committed_at_ms"] >= 0
        )
    else:
        active = (
            generation is None and
            type(document.get("lease_generation")) is int and
            document["lease_generation"] == state["lease_generation"] and
            document.get("lease_receipt_body_sha256") ==
                state["lease_receipt_body_sha256"] and
            all(
                document.get(field) is None
                for field in (
                    "snapshot_body_sha256", "snapshot_file_sha256",
                    "lease_receipt_file_sha256",
                    "export_receipt_body_sha256",
                    "export_receipt_file_sha256", "committed_at_ms",
                )) and
            document.get("close_reason") in CLOSE_REASONS and
            (
                status_value == "CLOSED" or
                document.get("close_reason") == state["close_reason"]
            )
        )
    _require(common and active, "CUSTODIAN_EXPORT_COMMIT_INVALID")


def _atomic_export_commit(
        path: Path,
        document: dict[str, Any],
        reader_gid: int,
) -> None:
    payload = _canonical(document)
    _require(
        2 <= len(payload) <= MAX_JSON_BYTES,
        "CUSTODIAN_EXPORT_COMMIT_INVALID",
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".current-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchown(descriptor, ROOT_UID, reader_gid)
        os.fchmod(descriptor, 0o440)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            _require(written > 0, "CUSTODIAN_EXPORT_COMMIT_SHORT_WRITE")
            offset += written
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and
            metadata.st_uid == ROOT_UID and metadata.st_gid == reader_gid and
            stat.S_IMODE(metadata.st_mode) == 0o440 and
            metadata.st_size == len(payload),
            "CUSTODIAN_EXPORT_COMMIT_TEMPORARY_UNSAFE",
        )
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _ended_export_commit(
        previous: dict[str, Any],
        state: dict[str, Any],
        status_value: str,
) -> dict[str, Any]:
    _require(
        status_value in {"CLOSING", "CLOSED"} and
        int(previous["commit_sequence"]) < (1 << 64) - 1,
        "CUSTODIAN_EXPORT_COMMIT_SEQUENCE_EXHAUSTED",
    )
    body = {
        "schema": "hepta.shadow-watch-export-commit.v1",
        "version": 1,
        "authority_status": status_value,
        "authority_changed_at_ms": max(
            _now_ms(), int(previous["authority_changed_at_ms"])),
        "close_reason": state["close_reason"],
        "commit_sequence": int(previous["commit_sequence"]) + 1,
        "generation": None,
        "domain_id": previous["domain_id"],
        "agent_uid": previous["agent_uid"],
        "reader_uid": previous["reader_uid"],
        "reader_gid": previous["reader_gid"],
        "lease_generation": state["lease_generation"],
        "snapshot_body_sha256": None,
        "snapshot_file_sha256": None,
        "lease_receipt_body_sha256":
            state["lease_receipt_body_sha256"],
        "lease_receipt_file_sha256": None,
        "export_receipt_body_sha256": None,
        "export_receipt_file_sha256": None,
        "committed_at_ms": None,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return _body_document(body)


def _remove_export_generation(
        path: Path,
        reader_gid: int,
) -> None:
    metadata = path.lstat()
    _require(
        stat.S_ISDIR(metadata.st_mode) and
        not stat.S_ISLNK(metadata.st_mode) and
        metadata.st_uid == ROOT_UID and metadata.st_gid == reader_gid and
        stat.S_IMODE(metadata.st_mode) in {0o700, 0o750},
        "CUSTODIAN_EXPORT_GENERATION_UNSAFE",
    )
    names = sorted(os.listdir(path))
    _require(
        set(names).issubset(set(EXPORT_FILES)),
        "CUSTODIAN_EXPORT_GENERATION_INVENTORY_UNSAFE",
    )
    for name in names:
        _unlink_stable(
            path / name,
            allowed=((ROOT_UID, reader_gid, 0o440),),
            minimum=2,
            maximum=MAX_EXPORT_BYTES,
        )
    os.rmdir(path)


def _cleanup_export_evidence(
    config: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    export = (
        EXPORT_RUNTIME_ROOT /
        f"hepta-shadow-watch-export-{config['domain_id']}")
    try:
        metadata = export.lstat()
    except FileNotFoundError:
        return True
    reader_gid = int(state["owner_gid"])
    _require(
        stat.S_ISDIR(metadata.st_mode) and
        not stat.S_ISLNK(metadata.st_mode) and
        metadata.st_uid == ROOT_UID and metadata.st_gid == reader_gid and
        stat.S_IMODE(metadata.st_mode) == 0o750,
        "CUSTODIAN_EXPORT_DIRECTORY_UNSAFE",
    )
    descriptor = os.open(
        export,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        opened = os.fstat(descriptor)
        named = export.lstat()
        _require(
            opened.st_dev == named.st_dev and opened.st_ino == named.st_ino,
            "CUSTODIAN_EXPORT_DIRECTORY_CHANGED",
        )
        names = sorted(os.listdir(export))
        allowed = {
            EXPORT_COMMIT_NAME,
            EXPORT_GENERATIONS_NAME,
            *EXPORT_FILES,
        }
        _require(
            set(names).issubset(allowed),
            "CUSTODIAN_EXPORT_INVENTORY_UNSAFE",
        )
        current_path = export / EXPORT_COMMIT_NAME
        current: dict[str, Any] | None = None
        if current_path.exists():
            data, _current_metadata = _read_small_regular(
                current_path,
                uid=ROOT_UID,
                gid=reader_gid,
                mode=0o440,
                minimum=2,
                maximum=MAX_JSON_BYTES,
            )
            current = _strict_json_bytes(
                data, "CUSTODIAN_EXPORT_COMMIT_INVALID")
            _validate_export_commit(current, config, state)
            if current["authority_status"] == "ACTIVE":
                current = _ended_export_commit(current, state, "CLOSING")
                _validate_export_commit(current, config, state)
                _atomic_export_commit(current_path, current, reader_gid)
                _fault("close.after_export_closing")
        generations = export / EXPORT_GENERATIONS_NAME
        if generations.exists():
            generation_metadata = generations.lstat()
            _require(
                stat.S_ISDIR(generation_metadata.st_mode) and
                not stat.S_ISLNK(generation_metadata.st_mode) and
                generation_metadata.st_uid == ROOT_UID and
                generation_metadata.st_gid == reader_gid and
                stat.S_IMODE(generation_metadata.st_mode) == 0o750,
                "CUSTODIAN_EXPORT_GENERATIONS_UNSAFE",
            )
            for name in sorted(os.listdir(generations)):
                _require(
                    EXPORT_GENERATION.fullmatch(name) is not None or
                    EXPORT_STAGING.fullmatch(name) is not None,
                    "CUSTODIAN_EXPORT_GENERATION_INVENTORY_UNSAFE",
                )
                _remove_export_generation(generations / name, reader_gid)
            os.rmdir(generations)
        for name in EXPORT_FILES:
            legacy = export / name
            if os.path.lexists(legacy):
                _unlink_stable(
                    legacy,
                    allowed=((ROOT_UID, reader_gid, 0o440),),
                    minimum=2,
                    maximum=MAX_EXPORT_BYTES,
                )
        _fault("close.after_export_generation_cleanup")
        if current is None:
            os.rmdir(export)
            return True
        if current["authority_status"] == "CLOSING":
            current = _ended_export_commit(current, state, "CLOSED")
            _validate_export_commit(current, config, state)
            _atomic_export_commit(current_path, current, reader_gid)
        _fault("close.after_export_closed")
        remaining = sorted(os.listdir(export))
        _require(
            remaining == [EXPORT_COMMIT_NAME],
            "CUSTODIAN_EXPORT_INVENTORY_UNSAFE",
        )
        os.fsync(descriptor)
        return True
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _cleanup_closed_export_tombstone(
        directory: Path,
        domain_id: str,
) -> bool:
    """Remove only a CLOSED marker bound to one durable closure."""

    export = (
        EXPORT_RUNTIME_ROOT / f"hepta-shadow-watch-export-{domain_id}")
    try:
        metadata = export.lstat()
    except FileNotFoundError:
        return True
    closure_directory = directory / CLOSURES_NAME
    closure_names = sorted(os.listdir(closure_directory))
    _require(
        len(closure_names) <= 4096 and all(
            name.endswith(".json") and len(name) > len(".json")
            for name in closure_names),
        "CUSTODIAN_CLOSURE_INVENTORY_INVALID",
    )
    closures: list[dict[str, Any]] = []
    for name in closure_names:
        closure = _read_closure(closure_directory / name)
        _require(
            closure is not None and
            name == f"{closure['campaign_id']}.json",
            "CUSTODIAN_CLOSURE_INVENTORY_INVALID",
        )
        closures.append(closure)
    _require(
        stat.S_ISDIR(metadata.st_mode) and
        not stat.S_ISLNK(metadata.st_mode) and
        metadata.st_uid == ROOT_UID and
        stat.S_IMODE(metadata.st_mode) == 0o750,
        "CUSTODIAN_EXPORT_DIRECTORY_UNSAFE",
    )
    descriptor = os.open(
        export,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        named = export.lstat()
        opened = os.fstat(descriptor)
        _require(
            opened.st_dev == named.st_dev and opened.st_ino == named.st_ino and
            sorted(os.listdir(export)) == [EXPORT_COMMIT_NAME],
            "CUSTODIAN_EXPORT_TOMBSTONE_INVENTORY_UNSAFE",
        )
        current_path = export / EXPORT_COMMIT_NAME
        data, _current_metadata = _read_small_regular(
            current_path,
            uid=ROOT_UID,
            gid=opened.st_gid,
            mode=0o440,
            minimum=2,
            maximum=MAX_JSON_BYTES,
        )
        current = _strict_json_bytes(
            data, "CUSTODIAN_EXPORT_COMMIT_INVALID")
        matches = [
            closure for closure in closures
            if (
                closure["domain_id"] == domain_id and
                closure["agent_uid"] == current.get("agent_uid") and
                closure["owner_uid"] == current.get("reader_uid") and
                closure["owner_gid"] == current.get("reader_gid") and
                closure["lease_generation"] ==
                current.get("lease_generation") and
                closure["lease_receipt_body_sha256"] ==
                current.get("lease_receipt_body_sha256") and
                closure["close_reason"] == current.get("close_reason")
            )
        ]
        _require(
            len(matches) == 1 and
            current.get("authority_status") == "CLOSED",
            "CUSTODIAN_EXPORT_TOMBSTONE_UNBOUND",
        )
        closure = matches[0]
        _validate_export_commit(
            current, _frozen_config(closure), closure)
        _unlink_stable(
            current_path,
            allowed=((ROOT_UID, int(closure["owner_gid"]), 0o440),),
            minimum=2,
            maximum=MAX_JSON_BYTES,
            expected_contents=data,
        )
        os.rmdir(export)
        parent_fd = os.open(
            export.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return True
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_closure(document: dict[str, Any]) -> None:
    _require(
        set(document) == set(CLOSURE_FIELDS) and
        document.get("schema") ==
        "hepta.shadow-watch-custodian-closure.v1" and
        document.get("version") == 1 and
        isinstance(document.get("domain_id"), str) and
        DOMAIN_ID.fullmatch(document["domain_id"]) is not None and
        isinstance(document.get("campaign_id"), str) and
        IDENTIFIER.fullmatch(document["campaign_id"]) is not None and
        type(document.get("owner_pid")) is int and
        type(document.get("owner_uid")) is int and
        type(document.get("owner_gid")) is int and
        type(document.get("owner_start_ticks")) is int and
        isinstance(document.get("owner_boot_id"), str) and
        BOOT_ID.fullmatch(document["owner_boot_id"]) is not None and
        type(document.get("lease_generation")) is int and
        1 <= document["lease_generation"] <= (1 << 64) - 1 and
        type(document.get("lease_expires_at_ms")) is int and
        type(document.get("registered_at_ms")) is int and
        type(document.get("close_started_at_ms")) is int and
        type(document.get("closed_at_ms")) is int and
        0 <= document["registered_at_ms"] <=
        document["close_started_at_ms"] <= document["closed_at_ms"] and
        document.get("close_reason") in CLOSE_REASONS and
        document.get("authoritative_revoke_outcome") in {
            "ACCEPTED", "ALREADY_ABSENT", "EXPIRED"} and
        document.get("local_authority_removed") is True and
        document.get("export_evidence_removed") is True and
        document.get("paper_authorized") is False and
        document.get("live_authorized") is False and
        document.get("mutation_authorized") is False and
        document.get("direct_broker_access") is False,
        "CUSTODIAN_CLOSURE_CONTRACT_INVALID",
    )
    for field in (
        "config_sha256", "watch_environment_sha256",
        "lease_receipt_body_sha256", "fence_token_sha256",
    ):
        _validate_digest_field(
            document.get(field), "CUSTODIAN_CLOSURE_DIGEST_INVALID")
    for field in ("token_directory", "supervisor_socket"):
        _validate_absolute_path_field(
            document.get(field),
            "CUSTODIAN_CLOSURE_RECOVERY_CONFIG_INVALID",
        )
    frozen_uids = (
        document.get("agent_uid"), document.get("gateway_uid"),
        document.get("execution_uid"), document.get("owner_uid"),
    )
    _require(
        all(
            type(value) is int and 1 <= value <= 4_294_967_295
            for value in frozen_uids
        ) and len(set(frozen_uids)) == len(frozen_uids) and
        type(document.get("agent_gid")) is int and
        1 <= document["agent_gid"] <= 4_294_967_295,
        "CUSTODIAN_CLOSURE_RECOVERY_CONFIG_INVALID",
    )
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    _require(
        claimed == _digest(_canonical(body)),
        "CUSTODIAN_CLOSURE_DIGEST_INVALID",
    )


def _read_closure(path: Path) -> dict[str, Any] | None:
    try:
        data, _metadata = _read_small_regular(
            path,
            uid=ROOT_UID,
            gid=ROOT_GID,
            mode=0o600,
            minimum=2,
            maximum=MAX_JSON_BYTES,
        )
    except FileNotFoundError:
        return None
    document = _strict_json_bytes(
        data, "CUSTODIAN_CLOSURE_INVALID")
    _validate_closure(document)
    return document


def _remove_exact(path: Path, *, mode: int, minimum: int, maximum: int) -> None:
    _unlink_stable(
        path,
        allowed=((ROOT_UID, ROOT_GID, mode),),
        minimum=minimum,
        maximum=maximum,
    )
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _close_locked(
    config: dict[str, Any],
    config_sha256: str,
    directory: Path,
    state: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    _require(reason in CLOSE_REASONS, "CUSTODIAN_CLOSE_REASON_INVALID")
    _require(
        config_sha256 == state["config_sha256"] and
        all(
            config.get(field) == state[field]
            for field in (
                "domain_id", "token_directory", "supervisor_socket",
                "agent_uid", "agent_gid", "gateway_uid", "execution_uid",
            )
        ) and
        config.get("paper_authorized") is False and
        config.get("live_authorized") is False,
        "CUSTODIAN_FROZEN_RECOVERY_CONFIG_INVALID",
    )
    if state["phase"] == "PROVISION_PREPARING":
        state, _provision_outcome = _resolve_provision_locked(
            config, directory, state)
    if state["phase"] == "ROTATION_PREPARING":
        state, rotation_outcome = _resolve_rotation_locked(
            config, directory, state)
        if rotation_outcome == "OLD_ACTIVE":
            state = _abort_rotation_locked(directory, state)
    closure_path = (
        directory / CLOSURES_NAME / f"{state['campaign_id']}.json")
    existing = _read_closure(closure_path)
    if existing is not None:
        _require(
            all(
                existing[field] == state[field]
                for field in (
                    "domain_id", "campaign_id", "config_sha256",
                    "watch_environment_sha256",
                    "token_directory", "supervisor_socket",
                    "agent_uid", "agent_gid", "gateway_uid",
                    "execution_uid",
                    "owner_pid", "owner_uid", "owner_gid",
                    "owner_start_ticks", "owner_boot_id",
                    "lease_generation", "lease_receipt_body_sha256",
                    "fence_token_sha256",
                    "lease_expires_at_ms", "registered_at_ms",
                )
            ),
            "CUSTODIAN_CLOSURE_STATE_MISMATCH",
        )
        _cleanup_private_snapshot_for_state(state)
        _cleanup_closed_export_tombstone(
            directory, str(state["domain_id"]))
        _remove_exact(
            _transaction_path(directory),
            mode=0o600,
            minimum=2,
            maximum=MAX_JSON_BYTES,
        )
        return existing

    if state["phase"] not in {"CLOSING", "CLEANING"}:
        body = dict(state)
        body.pop("body_sha256", None)
        body["phase"] = "CLOSING"
        body["close_reason"] = reason
        body["close_started_at_ms"] = max(
            _now_ms(), int(state["registered_at_ms"]))
        state = _replace_transaction(directory, body)
    elif state["phase"] == "CLOSING":
        reason = str(state["close_reason"])

    live_fence, _fence_metadata, _fence_path = (
        _load_live_fence_optional(
            config, str(state["fence_token_sha256"])))
    if state["phase"] != "CLEANING":
        _quarantine_agent_token(config, live_fence)
        _fault("close.after_quarantine")
        if live_fence is None:
            if _now_ms() < int(state["lease_expires_at_ms"]):
                return {
                    "schema":
                    "hepta.shadow-watch-custodian-status.v1",
                    "status": "PENDING_EXPIRY",
                    "domain_id": state["domain_id"],
                    "campaign_id": state["campaign_id"],
                    "lease_generation": state["lease_generation"],
                    "lease_expires_at_ms":
                    state["lease_expires_at_ms"],
                    "close_reason": state["close_reason"],
                    "local_agent_access_quarantined": True,
                    "paper_authorized": False,
                    "live_authorized": False,
                    "mutation_authorized": False,
                    "direct_broker_access": False,
                }
            outcome = "EXPIRED"
        else:
            try:
                outcome = _exact_revoke(
                    config,
                    int(state["lease_generation"]),
                    str(state["fence_token_sha256"]),
                )
            except SessionNotFound:
                # CLOSING was durable before the exact request. An absent
                # generation is authoritative no-session proof, including a
                # Gateway restart-fence tombstone.
                outcome = "ALREADY_ABSENT"
        _fault("close.after_revoke_before_commit")
        cleaning_body = dict(state)
        cleaning_body.pop("body_sha256", None)
        cleaning_body["phase"] = "CLEANING"
        cleaning_body["authoritative_revoke_outcome"] = outcome
        state = _replace_transaction(directory, cleaning_body)
        _fault("close.after_authoritative_revoke")
    else:
        outcome = str(state["authoritative_revoke_outcome"])

    _cleanup_local_authority(config, state, live_fence)
    _fault("close.after_local_cleanup")
    _cleanup_private_snapshot_for_state(state)
    _fault("close.after_private_snapshot_cleanup")
    export_removed = _cleanup_export_evidence(config, state)
    closed_at = max(
        _now_ms(), int(state["close_started_at_ms"]))
    body = {
        "schema": "hepta.shadow-watch-custodian-closure.v1",
        "version": 1,
        "domain_id": state["domain_id"],
        "campaign_id": state["campaign_id"],
        "config_sha256": state["config_sha256"],
        "watch_environment_sha256":
        state["watch_environment_sha256"],
        "token_directory": state["token_directory"],
        "supervisor_socket": state["supervisor_socket"],
        "agent_uid": state["agent_uid"],
        "agent_gid": state["agent_gid"],
        "gateway_uid": state["gateway_uid"],
        "execution_uid": state["execution_uid"],
        "owner_pid": state["owner_pid"],
        "owner_uid": state["owner_uid"],
        "owner_gid": state["owner_gid"],
        "owner_start_ticks": state["owner_start_ticks"],
        "owner_boot_id": state["owner_boot_id"],
        "lease_generation": state["lease_generation"],
        "lease_receipt_body_sha256":
        state["lease_receipt_body_sha256"],
        "fence_token_sha256": state["fence_token_sha256"],
        "lease_expires_at_ms": state["lease_expires_at_ms"],
        "registered_at_ms": state["registered_at_ms"],
        "close_started_at_ms": state["close_started_at_ms"],
        "closed_at_ms": closed_at,
        "close_reason": state["close_reason"],
        "authoritative_revoke_outcome": outcome,
        "local_authority_removed": True,
        "export_evidence_removed": export_removed,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }
    closure = _body_document(body)
    _validate_closure(closure)
    _atomic_write(closure_path, closure, replace=False)
    _fault("close.after_closure_publish")
    _remove_exact(
        _transaction_path(directory),
        mode=0o600,
        minimum=2,
        maximum=MAX_JSON_BYTES,
    )
    return closure


def close(config_path: Path, reason: str) -> dict[str, Any]:
    _require(
        os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
        "CUSTODIAN_ROOT_REQUIRED",
    )
    domain_id = _domain_hint(config_path)
    directory = _state_directory(domain_id, create=True)
    with _locked(directory):
        state = _read_transaction(directory)
        if state is None:
            _cleanup_closed_private_snapshot(directory, domain_id)
            _cleanup_closed_export_tombstone(directory, domain_id)
            return {
                "schema": "hepta.shadow-watch-custodian-status.v1",
                "status": "NO_ACTIVE_TRANSACTION",
                "domain_id": domain_id,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }
        config = _frozen_config(state)
        return _close_locked(
            config, str(state["config_sha256"]), directory, state, reason)


def _reconcile_locked(
    config: dict[str, Any],
    config_sha256: str,
    directory: Path,
    *,
    supervisor_identity: dict[str, int | str] | None,
) -> tuple[dict[str, Any], bool]:
    def close_state(
        current: dict[str, Any],
        close_reason: str,
    ) -> tuple[dict[str, Any], bool]:
        result = _close_locked(
            config, config_sha256, directory, current, close_reason)
        return result, result.get("status") != "PENDING_EXPIRY"

    state = _read_transaction(directory)
    if state is None:
        _cleanup_closed_private_snapshot(
            directory, str(config["domain_id"]))
        _cleanup_closed_export_tombstone(
            directory, str(config["domain_id"]))
        return ({
            "schema": "hepta.shadow-watch-custodian-status.v1",
            "status": "NO_ACTIVE_TRANSACTION",
            "domain_id": config["domain_id"],
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
            "direct_broker_access": False,
        }, True)
    _require_state_bindings(config, config_sha256, state)
    if state["phase"] == "PREPARING":
        return close_state(state, "registration-recovery")
    if state["phase"] in {"CLOSING", "CLEANING"}:
        return close_state(state, str(state["close_reason"]))
    owner_active = _process_matches(
        int(state["owner_pid"]),
        int(state["owner_uid"]),
        int(state["owner_gid"]),
        int(state["owner_start_ticks"]),
        str(state["owner_boot_id"]),
    )

    monitor_values = (
        state["monitor_pid"],
        state["monitor_start_ticks"],
        state["monitor_boot_id"],
    )
    monitor_close_reason: str | None = None
    if supervisor_identity is not None:
        if monitor_values == (None, None, None):
            body = dict(state)
            body.pop("body_sha256", None)
            body["monitor_pid"] = supervisor_identity["pid"]
            body["monitor_start_ticks"] = supervisor_identity["start_ticks"]
            body["monitor_boot_id"] = supervisor_identity["boot_id"]
            state = _replace_transaction(directory, body)
        elif not (
            monitor_values[0] == supervisor_identity["pid"] and
            monitor_values[1] == supervisor_identity["start_ticks"] and
            monitor_values[2] == supervisor_identity["boot_id"]
        ):
            monitor_close_reason = "custodian-restart"
    elif monitor_values != (None, None, None):
        if not _process_matches(
            int(monitor_values[0]),
            ROOT_UID,
            ROOT_GID,
            int(monitor_values[1]),
            str(monitor_values[2]),
        ):
            monitor_close_reason = "custodian-missing"
    elif (
        _now_ms() - int(state["registered_at_ms"]) >= START_GRACE_MS
    ):
        monitor_close_reason = "custodian-missing"

    now = _now_ms()
    lease_expired = now >= int(state["lease_expires_at_ms"])
    if state["phase"] == "PROVISION_PREPARING":
        must_resolve_provision = (
            not owner_active or lease_expired or
            monitor_close_reason is not None or
            now - int(state["registered_at_ms"]) >=
            PROVISION_HANDOFF_GRACE_MS
        )
        if not must_resolve_provision:
            return ({
                "schema": "hepta.shadow-watch-custodian-status.v1",
                "status": "PROVISIONING",
                "domain_id": state["domain_id"],
                "campaign_id": state["campaign_id"],
                "expected_lease_generation": 1,
                "lease_expires_at_ms": state["lease_expires_at_ms"],
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }, False)
        state, provision_outcome = _resolve_provision_locked(
            config, directory, state)
        if provision_outcome == "PROVISION_REVOKED_INCOMPLETE":
            return close_state(state, "registration-recovery")
        lease_expired = now >= int(state["lease_expires_at_ms"])
    if state["phase"] == "ROTATION_PREPARING":
        must_resolve = (
            not owner_active or lease_expired or
            monitor_close_reason is not None or
            now - int(state["rotation_started_at_ms"]) >=
            ROTATION_HANDOFF_GRACE_MS
        )
        if not must_resolve:
            return ({
                "schema": "hepta.shadow-watch-custodian-status.v1",
                "status": "ROTATION_PREPARED",
                "domain_id": state["domain_id"],
                "campaign_id": state["campaign_id"],
                "previous_lease_generation": state["lease_generation"],
                "expected_lease_generation":
                state["rotation_expected_generation"],
                "lease_expires_at_ms": state["lease_expires_at_ms"],
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }, False)
        state, rotation_outcome = _resolve_rotation_locked(
            config, directory, state)
        if rotation_outcome == "NEW_REVOKED_INCOMPLETE":
            return close_state(state, "rotation-recovery")
        if rotation_outcome == "OLD_ACTIVE":
            state = _abort_rotation_locked(directory, state)
        else:
            lease_expired = now >= int(state["lease_expires_at_ms"])

    if not owner_active:
        return close_state(state, "owner-dead")
    if lease_expired:
        return close_state(state, "lease-expired")
    if monitor_close_reason is not None:
        return close_state(state, monitor_close_reason)

    return ({
        "schema": "hepta.shadow-watch-custodian-status.v1",
        "status": "MONITORING",
        "domain_id": state["domain_id"],
        "campaign_id": state["campaign_id"],
        "lease_generation": state["lease_generation"],
        "lease_expires_at_ms": state["lease_expires_at_ms"],
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }, False)


def _reconcile_from_path(
    config_path: Path,
    *,
    supervisor_identity: dict[str, int | str] | None,
) -> dict[str, Any]:
    domain_id = _domain_hint(config_path)
    directory = _state_directory(domain_id, create=True)
    try:
        live_config, live_config_sha256 = _load_config(config_path)
        if live_config.get("domain_id") != domain_id:
            live_config = None
            live_config_sha256 = ""
    except (
        CustodianError, TrustDomainRuntimeError, OSError, UnicodeError,
        ValueError,
    ):
        live_config = None
        live_config_sha256 = ""
    with _locked(directory):
        state = _read_transaction(directory)
        if state is None:
            _cleanup_closed_private_snapshot(directory, domain_id)
            _cleanup_closed_export_tombstone(directory, domain_id)
            return {
                "schema": "hepta.shadow-watch-custodian-status.v1",
                "status": "NO_ACTIVE_TRANSACTION",
                "domain_id": domain_id,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_authorized": False,
                "direct_broker_access": False,
            }
        if (
            live_config is None or
            not _live_context_matches_state(
                live_config, live_config_sha256, state)
        ):
            return _close_locked(
                _frozen_config(state),
                str(state["config_sha256"]),
                directory,
                state,
                "configuration-drift",
            )
        result, _terminal = _reconcile_locked(
            live_config,
            live_config_sha256,
            directory,
            supervisor_identity=supervisor_identity,
        )
        return result


def reconcile(config_path: Path) -> dict[str, Any]:
    _require(
        os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
        "CUSTODIAN_ROOT_REQUIRED",
    )
    return _reconcile_from_path(
        config_path, supervisor_identity=None)


def supervise(config_path: Path) -> dict[str, Any]:
    _require(
        os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID,
        "CUSTODIAN_ROOT_REQUIRED",
    )
    _domain_hint(config_path)
    supervisor_identity = _process_identity(os.getpid())
    stopping = False

    def request_stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    try:
        while True:
            if stopping:
                return close(config_path, "service-stop")
            result = _reconcile_from_path(
                config_path,
                supervisor_identity=supervisor_identity,
            )
            if (
                result.get("status") == "NO_ACTIVE_TRANSACTION" or
                result.get("schema") ==
                "hepta.shadow-watch-custodian-closure.v1"
            ):
                return result
            time.sleep(POLL_SECONDS)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Root-owned fail-closed lifecycle custodian for WATCH leases"))
    parser.add_argument("--domain-config", required=True, type=Path)
    commands = parser.add_subparsers(dest="operation", required=True)
    provision_parser = commands.add_parser("provision")
    provision_parser.add_argument("--campaign-id", required=True)
    provision_parser.add_argument("--owner-pid", required=True, type=int)
    provision_parser.add_argument("--owner-uid", required=True, type=int)
    provision_parser.add_argument(
        "--ttl-sec", required=True, type=int)
    rotation = commands.add_parser("rotate")
    rotation.add_argument("--campaign-id", required=True)
    rotation.add_argument(
        "--current-generation", required=True, type=int)
    rotation.add_argument("--ttl-sec", required=True, type=int)
    commands.add_parser("supervise")
    commands.add_parser("reconcile")
    close_parser = commands.add_parser("close")
    close_parser.add_argument(
        "--reason",
        required=True,
        choices=tuple(sorted(CLOSE_REASONS)),
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.operation == "provision":
            result = provision(
                arguments.domain_config,
                arguments.campaign_id,
                arguments.owner_pid,
                arguments.owner_uid,
                arguments.ttl_sec,
            )
        elif arguments.operation == "rotate":
            result = rotate(
                arguments.domain_config,
                arguments.campaign_id,
                arguments.current_generation,
                arguments.ttl_sec,
            )
        elif arguments.operation == "supervise":
            result = supervise(arguments.domain_config)
        elif arguments.operation == "reconcile":
            result = reconcile(arguments.domain_config)
        else:
            result = close(arguments.domain_config, arguments.reason)
    except (
        CustodianError, TrustDomainRuntimeError, OSError, UnicodeError,
        ValueError, subprocess.SubprocessError,
    ) as error:
        message = str(error)
        if not re.fullmatch(r"[A-Z0-9_]{3,96}", message):
            message = "CUSTODIAN_FAILED"
        print(message, file=sys.stderr)
        return 78
    sys.stdout.buffer.write(_canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
