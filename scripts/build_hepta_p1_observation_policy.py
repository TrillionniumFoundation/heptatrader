#!/usr/bin/env python3

"""Build one canonical, non-authorizing P1 SHADOW observation policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

from hepta_agent_trust_domain import (
    TrustDomainRuntimeError,
    read_alpha_gateway_process_identity,
    read_alpha_gateway_process_profile,
    read_alpha_gateway_profile,
    read_alpha_gateway_socket,
)


SLOT_INTERVAL_MS = 2 * 60 * 1000
# One fresh formal segment must cover the production materializer's complete
# 210-minute rolling window.  The preceding load probe has its own launcher
# dispatch budget and is never counted as strategy history.
MINIMUM_WARMUP_MS = 210 * 60 * 1000
MAXIMUM_ITERATIONS = 241
MAXIMUM_LATENESS_MS = 60_000
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
BOOT_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}")
MAXIMUM_JSON_BYTES = 4 * 1024 * 1024
ADMISSION_MAXIMUM_AGE_MS = 60_000
LOAD_PROBE_REQUIRED_RUNS = 91
LOAD_PROBE_CADENCE_MS = 10_000
LOAD_PROBE_MAXIMUM_JITTER_MS = 1_000
LOAD_PROBE_MAXIMUM_EXPORT_COMMIT_MS = 9_000
LOAD_PROBE_MARKER_LIFETIME_MS = 20 * 60 * 1000
ROOT_UID = 0
ROOT_GID = 0
SYSTEMCTL = "/usr/bin/systemctl"
GATEWAY_UNIT = "hepta-tool-gateway@alpha.service"
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
BOOT_ID_MAXIMUM_BYTES = 128
PERMISSION_FIELDS = frozenset({
    "paper_authorized", "live_authorized", "mutation_authorized",
    "mutation_attempted", "direct_broker_access",
})
ADMISSION_FIELDS = frozenset({
    "schema", "version", "status", "campaign_id",
    "prospective_campaign_id", "prospective_policy_path",
    "authority_marker_path", "validated_at_ms",
    "host_receipt_body_sha256",
    "observer_controller_status_body_sha256",
    "observer_state_body_sha256", "history_head_body_sha256",
    "probe_execution_service_epoch",
    "probe_execution_service_fencing_generation",
    "probe_first_collection_started_at_ms", "probe_first_exported_at_ms",
    "probe_first_record_sha256", "probe_first_snapshot_body_sha256",
    "probe_last_collection_started_at_ms", "probe_last_exported_at_ms",
    "probe_last_record_sha256", "probe_last_snapshot_body_sha256",
    "probe_history_record_bytes", "probe_audit_cursor_sequence",
    "probe_audit_expected_previous_sha256", "sample_count",
    "collection_cadence_ms", "maximum_collection_jitter_ms",
    "missed_sample_count", "missed_decision_count", "environment",
    "paper_authorized", "live_authorized", "mutation_authorized",
    "direct_broker_access", "body_sha256",
})


class PolicyBuildError(RuntimeError):
    """Stable policy-build failure."""


def canonical_bytes(document: Any) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def digest_bytes(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(_secure_read(
        path, "P1_POLICY_RUNTIME_FILE_READ_FAILED", 64 * 1024 * 1024))


def _secure_read(
    path: Path,
    reason: str,
    maximum_bytes: int,
    *,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    allowed_modes: frozenset[int] | None = None,
) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PolicyBuildError(reason) from error
    try:
        metadata = os.fstat(descriptor)
        if (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                not 1 <= metadata.st_size <= maximum_bytes or
                (expected_uid is not None and metadata.st_uid != expected_uid) or
                (expected_gid is not None and metadata.st_gid != expected_gid) or
                (allowed_modes is not None and
                 stat.S_IMODE(metadata.st_mode) not in allowed_modes)):
            raise PolicyBuildError(reason)
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise PolicyBuildError(reason)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1) != b"":
            raise PolicyBuildError(reason)
        after = os.fstat(descriptor)
        if (
                metadata.st_dev, metadata.st_ino, metadata.st_size,
                metadata.st_mtime_ns, metadata.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns):
            raise PolicyBuildError(reason)
        return b"".join(chunks)
    except OSError as error:
        raise PolicyBuildError(reason) from error
    finally:
        os.close(descriptor)


def _read_boot_id(path: Path, reason: str) -> str:
    """Read the canonical Linux boot ID without trusting procfs st_size."""

    if path != BOOT_ID_PATH:
        raise PolicyBuildError(reason)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise PolicyBuildError(reason)
    directory_flags |= no_follow
    file_flags |= no_follow

    def stable(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_nlink, metadata.st_uid, metadata.st_gid,
            metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
        )

    def validate_directory(metadata: os.stat_result) -> None:
        if not (
                stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == ROOT_UID and
                metadata.st_gid == ROOT_GID and
                stat.S_IMODE(metadata.st_mode) & 0o022 == 0):
            raise PolicyBuildError(reason)

    def open_parent() -> int:
        current_fd: int | None = None
        try:
            current_fd = os.open("/", directory_flags)
            for component in path.parent.parts[1:]:
                validate_directory(os.fstat(current_fd))
                next_fd = os.open(component, directory_flags, dir_fd=current_fd)
                previous_fd = current_fd
                current_fd = next_fd
                os.close(previous_fd)
            validate_directory(os.fstat(current_fd))
            return current_fd
        except Exception:
            if current_fd is not None:
                try:
                    os.close(current_fd)
                except OSError:
                    pass
            raise

    def read_bounded(descriptor: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(BOOT_ID_MAXIMUM_BYTES - total + 1, 1024 * 1024),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > BOOT_ID_MAXIMUM_BYTES:
                raise PolicyBuildError(reason)
        return b"".join(chunks)

    parent_fd: int | None = None
    descriptor: int | None = None
    rebound_parent_fd: int | None = None
    rebound_fd: int | None = None
    completed = False
    try:
        parent_fd = open_parent()
        parent_identity = stable(os.fstat(parent_fd))
        before_entry = os.stat(
            path.name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(path.name, file_flags, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if not (
                stable(before_entry) == stable(opened) and
                stat.S_ISREG(opened.st_mode) and opened.st_uid == ROOT_UID and
                opened.st_gid == ROOT_GID and
                stat.S_IMODE(opened.st_mode) == 0o444 and
                opened.st_nlink == 1 and opened.st_size == 0 and
                opened.st_dev == os.fstat(parent_fd).st_dev):
            raise PolicyBuildError(reason)
        contents = read_bounded(descriptor)
        after = os.fstat(descriptor)
        after_entry = os.stat(
            path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not (
                stable(opened) == stable(after) == stable(after_entry) and
                parent_identity == stable(os.fstat(parent_fd))):
            raise PolicyBuildError(reason)

        rebound_parent_fd = open_parent()
        rebound_parent_identity = stable(os.fstat(rebound_parent_fd))
        rebound_entry = os.stat(
            path.name, dir_fd=rebound_parent_fd, follow_symlinks=False)
        rebound_fd = os.open(
            path.name, file_flags, dir_fd=rebound_parent_fd)
        rebound_opened = os.fstat(rebound_fd)
        if not (
                parent_identity == rebound_parent_identity and
                stable(opened) == stable(rebound_entry) ==
                stable(rebound_opened)):
            raise PolicyBuildError(reason)
        rebound_contents = read_bounded(rebound_fd)
        rebound_after = os.fstat(rebound_fd)
        rebound_after_entry = os.stat(
            path.name, dir_fd=rebound_parent_fd, follow_symlinks=False)
        if not (
                contents == rebound_contents and
                stable(rebound_opened) == stable(rebound_after) ==
                stable(rebound_after_entry) and
                rebound_parent_identity == stable(os.fstat(rebound_parent_fd))):
            raise PolicyBuildError(reason)
        if len(contents) != 37 or not contents.endswith(b"\n"):
            raise PolicyBuildError(reason)
        boot_id = contents[:-1].decode("ascii")
        if BOOT_ID.fullmatch(boot_id) is None:
            raise PolicyBuildError(reason)
        completed = True
        return boot_id
    except PolicyBuildError:
        raise
    except (OSError, UnicodeError) as error:
        raise PolicyBuildError(reason) from error
    finally:
        cleanup_error: OSError | None = None
        for opened_fd in (rebound_fd, rebound_parent_fd, descriptor, parent_fd):
            if opened_fd is not None:
                try:
                    os.close(opened_fd)
                except OSError as error:
                    if cleanup_error is None:
                        cleanup_error = error
        if cleanup_error is not None and completed:
            raise PolicyBuildError(reason) from cleanup_error


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        contents = _secure_read(path, f"{label}_INVALID", MAXIMUM_JSON_BYTES)
        document = json.loads(contents, object_pairs_hook=_pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolicyBuildError(f"{label}_INVALID") from error
    if not isinstance(document, dict):
        raise PolicyBuildError(f"{label}_INVALID")
    return document


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyBuildError("P1_POLICY_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _sealed_document(
    path: Path,
    label: str,
    *,
    root_owned: bool = False,
) -> tuple[dict[str, Any], bytes]:
    contents = _secure_read(
        path,
        f"{label}_FILE_INVALID",
        MAXIMUM_JSON_BYTES,
        expected_uid=ROOT_UID if root_owned else None,
        expected_gid=ROOT_GID if root_owned else None,
        allowed_modes=(frozenset({0o600, 0o640, 0o644})
                       if root_owned else None),
    )
    try:
        document = json.loads(contents, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PolicyBuildError(f"{label}_JSON_INVALID") from error
    if not isinstance(document, dict) or canonical_bytes(document) != contents:
        raise PolicyBuildError(f"{label}_CANONICAL_INVALID")
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    if (
            not isinstance(claimed, str) or DIGEST.fullmatch(claimed) is None or
            claimed != digest_bytes(canonical_bytes(body))):
        raise PolicyBuildError(f"{label}_DIGEST_INVALID")
    return document, contents


def _reject_permission_surface(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PERMISSION_FIELDS and child is not False:
                raise PolicyBuildError("P1_POLICY_PERMISSION_NOT_FALSE")
            _reject_permission_surface(child)
    elif isinstance(value, list):
        for child in value:
            _reject_permission_surface(child)


def current_environment_binding(
    *,
    boot_id_path: Path,
    audit_journal: Path,
    collector: Path,
    exporter: Path,
    heptactl: Path,
    gateway: Path,
    custodian: Path,
    observer: Path,
    host_controller: Path,
    domain_config: Path,
    gateway_profile: Path,
    gateway_socket: Path,
) -> dict[str, Any]:
    try:
        boot_id = _read_boot_id(
            boot_id_path, "P1_POLICY_BOOT_ID_INVALID")
        audit_flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            audit_flags |= os.O_NOFOLLOW
        audit_fd = os.open(audit_journal, audit_flags)
        try:
            audit = os.fstat(audit_fd)
        finally:
            os.close(audit_fd)
    except (OSError, UnicodeError) as error:
        raise PolicyBuildError("P1_POLICY_ENVIRONMENT_INVALID") from error
    if (
            BOOT_ID.fullmatch(boot_id) is None or
            not stat.S_ISREG(audit.st_mode) or audit.st_nlink != 1):
        raise PolicyBuildError("P1_POLICY_ENVIRONMENT_INVALID")
    binding = {
        "boot_id": boot_id,
        "audit_journal_device": audit.st_dev,
        "audit_journal_inode": audit.st_ino,
        "collector_sha256": digest_file(collector),
        "exporter_sha256": digest_file(exporter),
        "heptactl_sha256": digest_file(heptactl),
        "gateway_sha256": digest_file(gateway),
        "custodian_sha256": digest_file(custodian),
        "observer_sha256": digest_file(observer),
        "host_controller_sha256": digest_file(host_controller),
        "domain_config_sha256": digest_file(domain_config),
    }
    try:
        profile_before = read_alpha_gateway_profile(gateway_profile)
    except TrustDomainRuntimeError as error:
        raise PolicyBuildError("P1_POLICY_GATEWAY_PROFILE_INVALID") from error
    binding["gateway_profile_sha256"] = digest_bytes(profile_before.raw)
    binding.update(_live_gateway_identity(
        gateway_socket, gateway_profile, profile_before))
    return binding


def _live_gateway_identity(
        gateway_socket: Path, gateway_profile: Path,
        profile_before: Any) -> dict[str, Any]:
    def status() -> dict[str, str]:
        try:
            completed = subprocess.run(
                [
                    SYSTEMCTL, "show", "--no-pager",
                    "--property=ActiveState", "--property=SubState",
                    "--property=InvocationID", "--property=MainPID",
                    "--property=ExecMainStartTimestampMonotonic", GATEWAY_UNIT,
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=5,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            raise PolicyBuildError(
                "P1_POLICY_GATEWAY_IDENTITY_INVALID") from error
        parsed: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator != "=" or key in parsed:
                raise PolicyBuildError("P1_POLICY_GATEWAY_IDENTITY_INVALID")
            parsed[key] = value
        if not (
                completed.returncode == 0 and completed.stderr == "" and
                parsed.get("ActiveState") == "active" and
                parsed.get("SubState") == "running" and
                re.fullmatch(
                    r"[0-9a-f]{32}", parsed.get("InvocationID", "")) and
                parsed.get("MainPID", "").isdigit() and
                int(parsed["MainPID"]) > 1 and
                parsed.get("ExecMainStartTimestampMonotonic", "").isdigit() and
                int(parsed["ExecMainStartTimestampMonotonic"]) > 0):
            raise PolicyBuildError("P1_POLICY_GATEWAY_IDENTITY_INVALID")
        return parsed

    parsed = status()
    try:
        process_profile = read_alpha_gateway_process_profile(
            int(parsed["MainPID"]))
    except TrustDomainRuntimeError as error:
        raise PolicyBuildError(
            "P1_POLICY_GATEWAY_PROCESS_PROFILE_INVALID") from error
    try:
        socket_before = read_alpha_gateway_socket(gateway_socket)
        profile_after = read_alpha_gateway_profile(gateway_profile)
        parsed_after = status()
        process_after = read_alpha_gateway_process_identity(
            int(parsed["MainPID"]))
        socket_after = read_alpha_gateway_socket(gateway_socket)
    except TrustDomainRuntimeError as error:
        raise PolicyBuildError("P1_POLICY_GATEWAY_REBIND_INVALID") from error
    if (profile_before != profile_after or parsed_after != parsed or
            process_profile.pid_directory_metadata !=
            process_after.pid_directory_metadata or
            process_profile.starttime_ticks != process_after.starttime_ticks or
            socket_before != socket_after):
        raise PolicyBuildError("P1_POLICY_GATEWAY_IDENTITY_CHANGED")
    return {
        "gateway_invocation_id": parsed["InvocationID"],
        "gateway_main_pid": int(parsed["MainPID"]),
        "gateway_exec_main_start_timestamp_monotonic_us":
            int(parsed["ExecMainStartTimestampMonotonic"]),
        "gateway_socket_device": socket_before.metadata[0],
        "gateway_socket_inode": socket_before.metadata[1],
        "gateway_process_profile_sha256": digest_bytes(
            process_profile.canonical_projection),
    }


def derive_strategy_binding(
    strategy_path: Path,
    runtime_directory: Path,
) -> tuple[str, str, str]:
    """Derive the v3 package digest from the exact installed runtime files."""

    strategy = load_json(strategy_path, "P1_POLICY_STRATEGY")
    strategy_id = strategy.get("strategy_id")
    strategy_version = strategy.get("strategy_version")
    if (
            not isinstance(strategy_id, str) or
            IDENTIFIER.fullmatch(strategy_id) is None or
            not isinstance(strategy_version, str) or
            IDENTIFIER.fullmatch(strategy_version) is None or
            strategy.get("schema") !=
            "hepta.confirmed-momentum-strategy.v2" or
            strategy.get("paper_only") is not True or
            strategy.get("live_authorized") is not False):
        raise PolicyBuildError("P1_POLICY_STRATEGY_BOUNDARY_INVALID")
    evaluator = (
        runtime_directory / "hepta_eurusd_confirmed_momentum_strategy.py")
    builder = runtime_directory / "hepta_market_context_builder.py"
    normalizer = runtime_directory / "hepta_market_evidence_normalizer.py"
    contracts = runtime_directory / "hepta_strategy_contracts.py"
    for path in (strategy_path, evaluator, builder, normalizer, contracts):
        _secure_read(path, "P1_POLICY_RUNTIME_FILE_INVALID", 64 * 1024 * 1024)
    package = {
        "schema": "hepta.strategy-package-binding.v3",
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "config_sha256": digest_file(strategy_path),
        "evaluator_sha256": digest_file(evaluator),
        "builder_sha256": digest_file(builder),
        "normalizer_sha256": digest_file(normalizer),
        "contracts_sha256": digest_file(contracts),
    }
    return strategy_id, strategy_version, digest_bytes(canonical_bytes(package))


def aligned_valid_after(start_ms: int) -> int:
    if isinstance(start_ms, bool) or not isinstance(start_ms, int) or start_ms < 0:
        raise PolicyBuildError("P1_POLICY_START_INVALID")
    earliest = start_ms + MINIMUM_WARMUP_MS
    return (
        (earliest + SLOT_INTERVAL_MS - 1) // SLOT_INTERVAL_MS
    ) * SLOT_INTERVAL_MS


def build_policy(
    *,
    campaign_id: str,
    start_ms: int,
    strategy_path: Path,
    runtime_directory: Path,
    expected_strategy_sha256: str | None = None,
) -> dict[str, Any]:
    if IDENTIFIER.fullmatch(campaign_id) is None:
        raise PolicyBuildError("P1_POLICY_CAMPAIGN_ID_INVALID")
    strategy_id, strategy_version, strategy_sha256 = derive_strategy_binding(
        strategy_path.resolve(strict=True),
        runtime_directory.resolve(strict=True),
    )
    if (
            expected_strategy_sha256 is not None and
            (DIGEST.fullmatch(expected_strategy_sha256) is None or
             expected_strategy_sha256 != strategy_sha256)):
        raise PolicyBuildError("P1_POLICY_STRATEGY_DIGEST_MISMATCH")
    valid_after_ms = aligned_valid_after(start_ms)
    expires_at_ms = (
        valid_after_ms + MAXIMUM_ITERATIONS * SLOT_INTERVAL_MS)
    campaign_binding = {
        "schema": "hepta.strategy-shadow-observation-campaign.v1",
        "campaign_id": campaign_id,
        "valid_after_ms": valid_after_ms,
        "expires_at_ms": expires_at_ms,
        "slot_interval_ms": SLOT_INTERVAL_MS,
        "maximum_iterations": MAXIMUM_ITERATIONS,
        "maximum_lateness_ms": MAXIMUM_LATENESS_MS,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    body = {
        "schema": "hepta.strategy-shadow-observation-policy.v1",
        "version": 1,
        "campaign_id": campaign_id,
        "campaign_sha256": digest_bytes(canonical_bytes(campaign_binding)),
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_sha256": strategy_sha256,
        "valid_after_ms": valid_after_ms,
        "expires_at_ms": expires_at_ms,
        "slot_interval_ms": SLOT_INTERVAL_MS,
        "maximum_iterations": MAXIMUM_ITERATIONS,
        "maximum_lateness_ms": MAXIMUM_LATENESS_MS,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return {**body, "body_sha256": digest_bytes(canonical_bytes(body))}


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def _validate_admission_contract(
    admission: dict[str, Any],
    *,
    campaign_id: str,
    policy_path: Path,
    marker_path: Path,
    environment: dict[str, Any],
    now_ms: int,
) -> tuple[int, str, int]:
    """Validate every field emitted by the authoritative probe validator."""

    validated_at_ms = admission.get("validated_at_ms")
    epoch = admission.get("probe_execution_service_epoch")
    fencing = admission.get("probe_execution_service_fencing_generation")
    first_started = admission.get("probe_first_collection_started_at_ms")
    first_exported = admission.get("probe_first_exported_at_ms")
    last_started = admission.get("probe_last_collection_started_at_ms")
    last_exported = admission.get("probe_last_exported_at_ms")
    digest_fields = (
        "host_receipt_body_sha256",
        "observer_controller_status_body_sha256",
        "observer_state_body_sha256",
        "history_head_body_sha256",
        "probe_first_record_sha256",
        "probe_first_snapshot_body_sha256",
        "probe_last_record_sha256",
        "probe_last_snapshot_body_sha256",
        "probe_audit_expected_previous_sha256",
    )
    if not (
            set(admission) == ADMISSION_FIELDS and
            admission.get("schema") ==
            "hepta.p1-shadow-load-probe-admission-receipt.v1" and
            type(admission.get("version")) is int and
            admission.get("version") == 1 and admission.get("status") == "GO" and
            isinstance(admission.get("campaign_id"), str) and
            IDENTIFIER.fullmatch(admission["campaign_id"]) is not None and
            admission["campaign_id"] != campaign_id and
            admission.get("prospective_campaign_id") == campaign_id and
            admission.get("prospective_policy_path") == str(policy_path) and
            admission.get("authority_marker_path") == str(marker_path) and
            admission.get("environment") == environment and
            type(validated_at_ms) is int and
            0 <= now_ms - validated_at_ms <= ADMISSION_MAXIMUM_AGE_MS and
            isinstance(epoch, str) and 1 <= len(epoch) <= 256 and
            type(fencing) is int and 1 <= fencing < (1 << 64) and
            all(_is_digest(admission.get(field)) for field in digest_fields) and
            admission.get("probe_audit_expected_previous_sha256") ==
            admission.get("probe_last_record_sha256") and
            type(first_started) is int and type(first_exported) is int and
            type(last_started) is int and type(last_exported) is int and
            0 < first_started <= first_exported < last_started <= last_exported and
            first_exported - first_started <=
            LOAD_PROBE_MAXIMUM_EXPORT_COMMIT_MS and
            last_exported - last_started <=
            LOAD_PROBE_MAXIMUM_EXPORT_COMMIT_MS and
            last_exported <= validated_at_ms and
            abs(last_started - (
                first_started +
                (LOAD_PROBE_REQUIRED_RUNS - 1) * LOAD_PROBE_CADENCE_MS)) <=
            LOAD_PROBE_MAXIMUM_JITTER_MS and
            last_started <= last_exported and
            admission.get("sample_count") == LOAD_PROBE_REQUIRED_RUNS and
            admission.get("collection_cadence_ms") == LOAD_PROBE_CADENCE_MS and
            type(admission.get("maximum_collection_jitter_ms")) is int and
            0 <= admission["maximum_collection_jitter_ms"] <=
            LOAD_PROBE_MAXIMUM_JITTER_MS and
            type(admission.get("missed_sample_count")) is int and
            admission.get("missed_sample_count") == 0 and
            type(admission.get("missed_decision_count")) is int and
            admission.get("missed_decision_count") == 0 and
            type(admission.get("probe_history_record_bytes")) is int and
            admission["probe_history_record_bytes"] > 0 and
            admission.get("probe_audit_cursor_sequence") ==
            LOAD_PROBE_REQUIRED_RUNS and
            admission.get("paper_authorized") is False and
            admission.get("live_authorized") is False and
            admission.get("mutation_authorized") is False and
            admission.get("direct_broker_access") is False):
        raise PolicyBuildError("P1_POLICY_ADMISSION_BINDING_INVALID")
    return validated_at_ms, epoch, fencing


def _snapshot_execution_binding(snapshot_path: Path) -> tuple[str, int]:
    snapshot, _ = _sealed_document(
        snapshot_path, "P1_POLICY_WATCH_SNAPSHOT", root_owned=True)
    _reject_permission_surface(snapshot)
    reads = snapshot.get("reads")
    health = reads.get("system.get_health") if isinstance(reads, dict) else None
    epoch = health.get("execution_service_epoch") if isinstance(health, dict) else None
    fencing = (
        health.get("execution_service_fencing_generation")
        if isinstance(health, dict) else None)
    if not (
            isinstance(epoch, str) and 1 <= len(epoch) <= 256 and
            type(fencing) is int and 1 <= fencing < (1 << 64)):
        raise PolicyBuildError("P1_POLICY_WATCH_SNAPSHOT_BINDING_INVALID")
    return epoch, fencing


def build_admitted_policy(
    *,
    campaign_id: str,
    start_ms: int,
    strategy_path: Path,
    runtime_directory: Path,
    expected_strategy_sha256: str | None,
    admission_receipt_path: Path,
    policy_path: Path,
    marker_path: Path,
    environment: dict[str, Any],
    now_ms: int,
    _expected_root_uid: int = ROOT_UID,
    _expected_root_gid: int = ROOT_GID,
    _require_root_identity: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
            _require_root_identity and
            (os.geteuid() != ROOT_UID or os.getegid() != ROOT_GID)):
        raise PolicyBuildError("P1_POLICY_ROOT_REQUIRED")
    admission_contents = _secure_read(
        admission_receipt_path,
        "P1_POLICY_ADMISSION_FILE_INVALID",
        MAXIMUM_JSON_BYTES,
        expected_uid=_expected_root_uid,
        expected_gid=_expected_root_gid,
        allowed_modes=frozenset({0o600, 0o640, 0o644}),
    )
    try:
        admission = json.loads(admission_contents, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PolicyBuildError("P1_POLICY_ADMISSION_JSON_INVALID") from error
    if not isinstance(admission, dict) or canonical_bytes(admission) != admission_contents:
        raise PolicyBuildError("P1_POLICY_ADMISSION_CANONICAL_INVALID")
    admission_body = dict(admission)
    admission_claimed = admission_body.pop("body_sha256", None)
    if (
            not isinstance(admission_claimed, str) or
            DIGEST.fullmatch(admission_claimed) is None or
            admission_claimed != digest_bytes(canonical_bytes(admission_body))):
        raise PolicyBuildError("P1_POLICY_ADMISSION_DIGEST_INVALID")
    _reject_permission_surface(admission)
    validated_at_ms, epoch, fencing = _validate_admission_contract(
        admission,
        campaign_id=campaign_id,
        policy_path=policy_path,
        marker_path=marker_path,
        environment=environment,
        now_ms=now_ms,
    )
    policy = build_policy(
        campaign_id=campaign_id,
        start_ms=start_ms,
        strategy_path=strategy_path,
        runtime_directory=runtime_directory,
        expected_strategy_sha256=expected_strategy_sha256,
    )
    if not (
            type(now_ms) is int and validated_at_ms <= now_ms and
            now_ms - validated_at_ms <= ADMISSION_MAXIMUM_AGE_MS and
            now_ms < policy["expires_at_ms"]):
        raise PolicyBuildError("P1_POLICY_ADMISSION_BINDING_INVALID")
    policy_contents = canonical_bytes(policy)
    marker_body = {
        "schema": "hepta.p1-shadow-admission-authority-marker.v1",
        "version": 1,
        "status": "ACTIVE",
        "campaign_id": campaign_id,
        "policy_path": str(policy_path),
        "policy_file_sha256": digest_bytes(policy_contents),
        "policy_body_sha256": policy["body_sha256"],
        "admission_receipt_path": str(admission_receipt_path),
        "admission_receipt_file_sha256": digest_bytes(admission_contents),
        "admission_receipt_body_sha256": admission["body_sha256"],
        "admitted_at_ms": validated_at_ms,
        "marker_created_at_ms": now_ms,
        "expires_at_ms": policy["expires_at_ms"],
        "execution_service_epoch": epoch,
        "execution_service_fencing_generation": fencing,
        "environment": environment,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }
    marker = {
        **marker_body,
        "body_sha256": digest_bytes(canonical_bytes(marker_body)),
    }
    return policy, marker


def build_load_probe_policy(
    *,
    campaign_id: str,
    start_ms: int,
    strategy_path: Path,
    runtime_directory: Path,
    expected_strategy_sha256: str | None,
    policy_path: Path,
    marker_path: Path,
    environment: dict[str, Any],
    now_ms: int,
    _require_root_identity: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the independent root-only admission probe authority pair."""

    if (
            _require_root_identity and
            (os.geteuid() != ROOT_UID or os.getegid() != ROOT_GID)):
        raise PolicyBuildError("P1_POLICY_ROOT_REQUIRED")
    if not (type(now_ms) is int and now_ms > 0):
        raise PolicyBuildError("P1_LOAD_PROBE_EXECUTION_BINDING_INVALID")
    policy = build_policy(
        campaign_id=campaign_id,
        start_ms=start_ms,
        strategy_path=strategy_path,
        runtime_directory=runtime_directory,
        expected_strategy_sha256=expected_strategy_sha256,
    )
    policy_contents = canonical_bytes(policy)
    marker_body = {
        "schema": "hepta.p1-shadow-load-probe-authority-marker.v1",
        "version": 1,
        "status": "ACTIVE",
        "scope": "LOAD_PROBE",
        "mode": "LOAD_PROBE",
        "campaign_id": campaign_id,
        "policy_path": str(policy_path),
        "policy_file_sha256": digest_bytes(policy_contents),
        "policy_body_sha256": policy["body_sha256"],
        "marker_created_at_ms": now_ms,
        "expires_at_ms": now_ms + LOAD_PROBE_MARKER_LIFETIME_MS,
        "execution_binding_status": "PENDING_FIRST_SNAPSHOT",
        "execution_service_epoch": None,
        "execution_service_fencing_generation": None,
        "environment": environment,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
        "direct_broker_access": False,
    }
    return policy, {
        **marker_body,
        "body_sha256": digest_bytes(canonical_bytes(marker_body)),
    }


def write_policy(path: Path, document: dict[str, Any], *, mode: int = 0o644) -> None:
    contents = canonical_bytes(document)
    if not path.is_absolute():
        raise PolicyBuildError("P1_POLICY_OUTPUT_PATH_INVALID")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(path.parent, flags)
        parent = os.fstat(directory_fd)
    except OSError as error:
        raise PolicyBuildError("P1_POLICY_OUTPUT_DIRECTORY_INVALID") from error
    try:
        if (
                not stat.S_ISDIR(parent.st_mode) or parent.st_uid != ROOT_UID or
                parent.st_gid != ROOT_GID or
                stat.S_IMODE(parent.st_mode) & 0o022):
            raise PolicyBuildError("P1_POLICY_OUTPUT_DIRECTORY_INVALID")
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            create_flags |= os.O_NOFOLLOW
        descriptor = os.open(path.name, create_flags, mode, dir_fd=directory_fd)
        try:
            os.fchmod(descriptor, mode)
            offset = 0
            while offset < len(contents):
                offset += os.write(descriptor, contents[offset:])
            os.fsync(descriptor)
            written = os.fstat(descriptor)
            if not (
                    stat.S_ISREG(written.st_mode) and written.st_nlink == 1 and
                    written.st_uid == ROOT_UID and written.st_gid == ROOT_GID and
                    stat.S_IMODE(written.st_mode) == mode and
                    written.st_size == len(contents)):
                raise PolicyBuildError("P1_POLICY_OUTPUT_FILE_INVALID")
        finally:
            os.close(descriptor)
        os.fsync(directory_fd)
    except FileExistsError as error:
        raise PolicyBuildError("P1_POLICY_OUTPUT_EXISTS") from error
    except OSError as error:
        raise PolicyBuildError("P1_POLICY_OUTPUT_WRITE_FAILED") from error
    finally:
        os.close(directory_fd)


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--mode", choices=("load-probe", "formal"), required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--start-ms", required=True, type=int)
    parser.add_argument(
        "--strategy", type=Path,
        default=Path(
            "/usr/share/heptatrader/strategies/"
            "eurusd-confirmed-momentum-shadow-v2.json"))
    parser.add_argument(
        "--runtime-directory", type=Path, default=Path("/usr/libexec"))
    parser.add_argument("--strategy-package-sha256")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--admission-receipt", type=Path)
    parser.add_argument("--authority-marker", type=Path, required=True)
    parser.add_argument(
        "--boot-id", type=Path,
        default=Path("/proc/sys/kernel/random/boot_id"))
    parser.add_argument("--audit-journal", type=Path, required=True)
    parser.add_argument("--collector", type=Path, required=True)
    parser.add_argument("--exporter", type=Path, required=True)
    parser.add_argument("--heptactl", type=Path, required=True)
    parser.add_argument("--gateway", type=Path, required=True)
    parser.add_argument("--custodian", type=Path, required=True)
    parser.add_argument("--observer", type=Path, required=True)
    parser.add_argument("--host-controller", type=Path, required=True)
    parser.add_argument("--domain-config", type=Path, required=True)
    parser.add_argument("--gateway-profile", type=Path, required=True)
    parser.add_argument(
        "--gateway-socket", type=Path,
        default=Path("/run/hepta-agent-alpha/tools.sock"))
    arguments = parser.parse_args()
    try:
        if not (
                arguments.output.is_absolute() and
                arguments.authority_marker.is_absolute() and
                arguments.output != arguments.authority_marker and
                ((arguments.mode == "formal" and
                  arguments.admission_receipt is not None) or
                 (arguments.mode == "load-probe" and
                  arguments.admission_receipt is None))):
            raise PolicyBuildError("P1_POLICY_MODE_ARGUMENTS_INVALID")
        now_ms = time.time_ns() // 1_000_000
        environment = current_environment_binding(
            boot_id_path=arguments.boot_id,
            audit_journal=arguments.audit_journal,
            collector=arguments.collector,
            exporter=arguments.exporter,
            heptactl=arguments.heptactl,
            gateway=arguments.gateway,
            custodian=arguments.custodian,
            observer=arguments.observer,
            host_controller=arguments.host_controller,
            domain_config=arguments.domain_config,
            gateway_profile=arguments.gateway_profile,
            gateway_socket=arguments.gateway_socket,
        )
        common = {
            "campaign_id": arguments.campaign_id,
            "start_ms": arguments.start_ms,
            "strategy_path": arguments.strategy,
            "runtime_directory": arguments.runtime_directory,
            "expected_strategy_sha256": arguments.strategy_package_sha256,
            "policy_path": arguments.output,
            "marker_path": arguments.authority_marker,
            "environment": environment,
            "now_ms": now_ms,
        }
        if arguments.mode == "formal":
            assert arguments.admission_receipt is not None
            policy, marker = build_admitted_policy(
                **common,
                admission_receipt_path=arguments.admission_receipt,
            )
        else:
            policy, marker = build_load_probe_policy(**common)
        write_policy(arguments.output, policy)
        # The uid-1000 observer must read the immutable root-owned marker.
        write_policy(arguments.authority_marker, marker, mode=0o644)
    except (OSError, PolicyBuildError, ValueError) as error:
        print(f"build_hepta_p1_observation_policy: FAIL {error}", file=sys.stderr)
        return 78
    print(
        "build_hepta_p1_observation_policy: PASS "
        f"mode={arguments.mode} "
        f"policy_sha256={digest_file(arguments.output)} "
        f"valid_after_ms={policy['valid_after_ms']} "
        f"expires_at_ms={policy['expires_at_ms']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
