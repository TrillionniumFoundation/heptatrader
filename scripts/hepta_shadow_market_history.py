#!/usr/bin/env python3

"""Append-only SHADOW quote history and deterministic sampled bar materializer.

The module consumes only canonical, exported ``hepta.shadow-watch-snapshot.v2``
documents.  Snapshot, WATCH lease receipt, and export-binding receipt are each
safe-read as single-link, root-owned canonical files with mode 0400 or 0440.
The lease receipt schema is ``hepta.shadow-watch-lease-receipt.v1`` with these
exact fields::

    schema, version, domain_id, agent_id, agent_uid, boundary, operation,
    lease_generation, previous_lease_generation,
    previous_receipt_body_sha256, accepted, reason_code, accepted_at_ms,
    ttl_seconds, expires_at_ms, paper_authorized, live_authorized,
    mutation_authorized, body_sha256

``boundary`` is ``WATCH``.  ``operation`` is ``PROVISION`` for a new segment
or ``ROTATE`` for an in-segment generation change.  A rotate receipt names the
immediately previous generation and its receipt body digest.  There are no
bearer-token fields or opaque extension fields.  The file must be a single-link
regular file owned by UID 0 with mode 0400 or 0440.

The exact ``hepta.shadow-watch-export-receipt.v1`` contract binds domain,
agent/reader identity, lease generation and lease body/file digests to the
snapshot body/file digests and export time.  It is itself root-owned and
canonical, contains no bearer data, and is the commit point proving that the
reader did not fabricate or substitute authoritative reads.

An accepted rotation may bridge at most 15 seconds of actual capture and quote
time.  A larger gap fails the current segment with
``MARKET_HISTORY_ROTATION_SEGMENT_REQUIRED``; a caller must reprovision into a
new history directory.  This module never stitches such a gap or upgrades an
incomplete sampled bar to complete.

Append uses the atomic mutable ``history-head.json`` checkpoint and fixed-name
immutable ``record-%020d.json`` records.  The checkpoint makes normal append
O(1), validates the current tail, and advances a constant-work historical
tamper-audit cursor.  ``load_history`` and explicit ``recover`` still perform a
complete chain audit.  A crash after record publication but before checkpoint
commit fails subsequent append with ``MARKET_HISTORY_HEAD_RECOVERY_REQUIRED``;
``recover`` scans and validates the immutable chain before rebuilding the head.
Legacy hash-in-filename/v1 directories are never silently migrated: start a new
PROVISION segment after preserving the legacy directory as audit evidence.

The module has no broker, Gateway, session, network, systemd, or mutation
interface.

History record v3 separates capture cadence from authoritative quote cadence.
Capture and generated timestamps remain strictly increasing.  An unchanged,
still-fresh authoritative quote may retain its timestamp across adjacent
captures, but the complete normalized quote identity must remain identical and
the record is marked ``quote_changed=false``.  A new authoritative timestamp
is marked ``quote_changed=true``; same-timestamp mutation fails closed.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any


MIN_CADENCE_MS = 5_000
MAX_CADENCE_MS = 15_000
DEFAULT_MAXIMUM_JITTER_MS = 1_000
MAXIMUM_JITTER_MS = 2_000
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_HISTORY_RECORDS = 1_000_000
BUILDER_MAXIMUM_QUOTE_GAP_MS = 15_000
MINIMUM_COVERAGE_NUMERATOR = 4
MINIMUM_COVERAGE_DENOMINATOR = 5
MAXIMUM_WATCH_TTL_SECONDS = 3_600
DEFAULT_MAXIMUM_HISTORY_BYTES = 1024 * 1024 * 1024
DEFAULT_MINIMUM_FREE_BYTES = 128 * 1024 * 1024
MAXIMUM_STORAGE_GUARD_BYTES = (1 << 63) - 1
MINIMUM_MATERIALIZATION_WINDOW_MS = 200 * 60_000
DEFAULT_MATERIALIZATION_WINDOW_MS = 210 * 60_000
MAXIMUM_MATERIALIZATION_WINDOW_MS = 12 * 60 * 60_000
MAXIMUM_MATERIALIZATION_RECORDS = 9_000
INCREMENTAL_AUDIT_RECORDS = 2
ROOT_TRUST_UID = 0
HEAD_NAME = "history-head.json"
HEAD_PENDING_NAME = ".history-head.pending"

READ_ORDER = (
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "market.get_quote",
    "system.get_health",
)
SNAPSHOT_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_uid",
    "collection_started_at_ms", "collection_finished_at_ms",
    "read_finished_at_ms", "generated_at_ms", "instrument",
    "catalog_sha256", "descriptor_sha256", "reads",
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
RECORD_FIELDS = frozenset({
    "schema", "version", "sequence", "cadence_ms", "maximum_jitter_ms",
    "previous_record_sha256", "snapshot_body_sha256",
    "snapshot_file_sha256", "domain_id", "agent_uid", "instrument",
    "catalog_sha256", "descriptor_sha256", "execution_service_epoch",
    "execution_service_fencing_generation", "watch_generation",
    "watch_lease_receipt_body_sha256",
    "watch_lease_receipt_file_sha256", "watch_lease_operation",
    "watch_lease_previous_generation",
    "watch_lease_previous_receipt_body_sha256",
    "watch_lease_accepted_at_ms", "watch_lease_ttl_seconds",
    "watch_lease_expires_at_ms",
    "watch_export_receipt_body_sha256",
    "watch_export_receipt_file_sha256", "watch_exported_at_ms",
    "watch_export_reader_uid", "watch_export_reader_gid",
    "collection_started_at_ms",
    "collection_finished_at_ms", "generated_at_ms",
    "quote_read_finished_at_ms", "quote_changed", "quote", "record_sha256",
})
LEASE_RECEIPT_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_id", "agent_uid", "boundary",
    "operation", "lease_generation", "previous_lease_generation",
    "previous_receipt_body_sha256", "accepted", "reason_code",
    "accepted_at_ms", "ttl_seconds", "expires_at_ms", "paper_authorized",
    "live_authorized", "mutation_authorized", "body_sha256",
})
EXPORT_RECEIPT_FIELDS = frozenset({
    "schema", "version", "domain_id", "agent_uid", "reader_uid",
    "reader_gid", "boundary", "lease_generation",
    "lease_receipt_body_sha256", "lease_receipt_file_sha256",
    "snapshot_body_sha256", "snapshot_file_sha256",
    "snapshot_generated_at_ms", "exported_at_ms", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "body_sha256",
})
HEAD_FIELDS = frozenset({
    "schema", "version", "record_schema", "record_count",
    "history_record_bytes", "first_record_sha256", "last_record_sha256",
    "last_record_file_sha256", "last_record_name",
    "last_previous_record_sha256", "last_snapshot_body_sha256",
    "last_snapshot_file_sha256", "cadence_ms", "maximum_jitter_ms",
    "audit_cursor_sequence", "audit_expected_previous_sha256",
    "body_sha256",
})
QUOTE_FIELDS = frozenset({
    "source", "authoritative", "stale", "bid", "ask", "observed_at_ms",
    "stale_after_ms",
})
BAR_FIELDS = frozenset({
    "interval_ms", "started_at_ms", "finished_at_ms", "price_basis",
    "open", "high", "low", "close", "sample_count",
    "expected_sample_count", "coverage_ppm", "maximum_capture_gap_ms",
    "first_sequence", "last_sequence", "complete", "reason_codes",
    "source_kind", "source_count", "source_sha256", "bar_sha256",
})
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
RECORD_NAME_PATTERN = re.compile(r"record-([0-9]{20})\.json")
LEGACY_RECORD_NAME_PATTERN = re.compile(
    r"record-[0-9]{20}-[0-9a-f]{64}\.json")


class HistoryError(RuntimeError):
    """A stable, fail-closed market-history contract error."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HistoryError("MARKET_HISTORY_JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise HistoryError("MARKET_HISTORY_JSON_NON_FINITE")


def canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise HistoryError("MARKET_HISTORY_CANONICALIZATION_FAILED") from error


def digest_bytes(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def digest_document(value: Any) -> str:
    return digest_bytes(canonical_bytes(value))


def _exact(value: Any, fields: frozenset[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise HistoryError(reason)
    return value


def _integer(
    value: Any,
    reason: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HistoryError(reason)
    if minimum is not None and value < minimum:
        raise HistoryError(reason)
    if maximum is not None and value > maximum:
        raise HistoryError(reason)
    return value


def _number(value: Any, reason: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HistoryError(reason)
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0.0):
        raise HistoryError(reason)
    return result


def _text(value: Any, reason: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise HistoryError(reason)
    if identifier and IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise HistoryError(reason)
    return value


def _digest(value: Any, reason: str) -> str:
    if not isinstance(value, str) or DIGEST_PATTERN.fullmatch(value) is None:
        raise HistoryError(reason)
    return value


def _load_canonical(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        metadata = os.lstat(path)
        if (
                not stat.S_ISREG(metadata.st_mode) or
                stat.S_ISLNK(metadata.st_mode) or
                metadata.st_nlink != 1 or
                metadata.st_size > MAX_DOCUMENT_BYTES):
            raise HistoryError(f"{label}_METADATA_INVALID")
        contents = path.read_bytes()
    except OSError as error:
        raise HistoryError(f"{label}_READ_FAILED") from error
    if len(contents) > MAX_DOCUMENT_BYTES:
        raise HistoryError(f"{label}_TOO_LARGE")
    try:
        value = json.loads(
            contents.decode("ascii", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise HistoryError(f"{label}_JSON_INVALID") from error
    if not isinstance(value, dict):
        raise HistoryError(f"{label}_ROOT_INVALID")
    if contents != canonical_bytes(value):
        raise HistoryError(f"{label}_NOT_CANONICAL")
    return value, contents


def _trusted_identity(metadata: os.stat_result) -> tuple[Any, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode,
        metadata.st_nlink, metadata.st_uid, metadata.st_gid,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _load_root_canonical(
    path: Path,
    label: str,
) -> tuple[dict[str, Any], bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise HistoryError(f"{label}_READ_FAILED") from error
    try:
        before = os.fstat(descriptor)
        if (
                not stat.S_ISREG(before.st_mode) or
                before.st_nlink != 1 or
                before.st_uid != ROOT_TRUST_UID or
                stat.S_IMODE(before.st_mode) not in {0o400, 0o440} or
                before.st_size > MAX_DOCUMENT_BYTES):
            raise HistoryError(f"{label}_METADATA_INVALID")
        chunks: list[bytes] = []
        remaining = MAX_DOCUMENT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        contents = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
                len(contents) > MAX_DOCUMENT_BYTES or
                len(contents) != before.st_size or
                not stat.S_ISREG(after.st_mode) or
                after.st_nlink != 1 or
                after.st_uid != ROOT_TRUST_UID or
                stat.S_IMODE(after.st_mode) not in {0o400, 0o440} or
                _trusted_identity(before) != _trusted_identity(after)):
            raise HistoryError(f"{label}_METADATA_INVALID")
        path_after = os.stat(path, follow_symlinks=False)
        if (
                _trusted_identity(after) !=
                _trusted_identity(path_after) or
                not stat.S_ISREG(path_after.st_mode) or
                path_after.st_nlink != 1 or
                stat.S_IMODE(path_after.st_mode) not in {0o400, 0o440} or
                path_after.st_uid != ROOT_TRUST_UID):
            raise HistoryError(f"{label}_METADATA_INVALID")
    except OSError as error:
        raise HistoryError(f"{label}_READ_FAILED") from error
    finally:
        os.close(descriptor)
    try:
        value = json.loads(
            contents.decode("ascii", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise HistoryError(f"{label}_JSON_INVALID") from error
    if not isinstance(value, dict):
        raise HistoryError(f"{label}_ROOT_INVALID")
    if contents != canonical_bytes(value):
        raise HistoryError(f"{label}_NOT_CANONICAL")
    return value, contents, after


def _validate_cadence(cadence_ms: int, maximum_jitter_ms: int) -> None:
    _integer(
        cadence_ms,
        "MARKET_HISTORY_CADENCE_INVALID",
        minimum=MIN_CADENCE_MS,
        maximum=MAX_CADENCE_MS,
    )
    _integer(
        maximum_jitter_ms,
        "MARKET_HISTORY_JITTER_INVALID",
        minimum=0,
        maximum=MAXIMUM_JITTER_MS,
    )
    if maximum_jitter_ms >= cadence_ms:
        raise HistoryError("MARKET_HISTORY_JITTER_INVALID")


def _validate_storage_guard(
    maximum_history_bytes: int,
    minimum_free_bytes: int,
) -> None:
    _integer(
        maximum_history_bytes,
        "MARKET_HISTORY_BYTE_QUOTA_INVALID",
        minimum=1,
        maximum=MAXIMUM_STORAGE_GUARD_BYTES,
    )
    _integer(
        minimum_free_bytes,
        "MARKET_HISTORY_FREE_SPACE_GUARD_INVALID",
        minimum=0,
        maximum=MAXIMUM_STORAGE_GUARD_BYTES,
    )


def _materialization_record_limit(
    cadence_ms: int,
    window_ms: int,
) -> int:
    _integer(
        window_ms,
        "MARKET_HISTORY_MATERIALIZATION_WINDOW_INVALID",
        minimum=MINIMUM_MATERIALIZATION_WINDOW_MS,
        maximum=MAXIMUM_MATERIALIZATION_WINDOW_MS,
    )
    result = math.ceil(window_ms / cadence_ms) + 2
    if result > MAXIMUM_MATERIALIZATION_RECORDS:
        raise HistoryError(
            "MARKET_HISTORY_MATERIALIZATION_WINDOW_INVALID")
    return result


def _validate_snapshot(
    snapshot: dict[str, Any],
    contents: bytes,
) -> dict[str, Any]:
    _exact(
        snapshot,
        SNAPSHOT_FIELDS,
        "MARKET_HISTORY_SNAPSHOT_FIELDS_INVALID",
    )
    if (
            snapshot["schema"] != "hepta.shadow-watch-snapshot.v2" or
            snapshot["version"] != 2):
        raise HistoryError("MARKET_HISTORY_SNAPSHOT_SCHEMA_INVALID")
    if (
            snapshot["paper_authorized"] is not False or
            snapshot["live_authorized"] is not False or
            snapshot["mutation_attempted"] is not False or
            snapshot["direct_broker_access"] is not False):
        raise HistoryError("MARKET_HISTORY_SNAPSHOT_BOUNDARY_INVALID")
    domain_id = _text(
        snapshot["domain_id"],
        "MARKET_HISTORY_DOMAIN_INVALID",
        identifier=True,
    )
    agent_uid = _integer(
        snapshot["agent_uid"],
        "MARKET_HISTORY_AGENT_UID_INVALID",
        minimum=1,
    )
    if snapshot["instrument"] != "EUR.USD":
        raise HistoryError("MARKET_HISTORY_INSTRUMENT_INVALID")
    catalog_sha256 = _digest(
        snapshot["catalog_sha256"],
        "MARKET_HISTORY_CATALOG_DIGEST_INVALID",
    )
    descriptors = snapshot["descriptor_sha256"]
    if not isinstance(descriptors, dict) or set(descriptors) != set(READ_ORDER):
        raise HistoryError("MARKET_HISTORY_DESCRIPTOR_SET_INVALID")
    normalized_descriptors: dict[str, str] = {}
    for tool in READ_ORDER:
        normalized_descriptors[tool] = _digest(
            descriptors[tool],
            "MARKET_HISTORY_DESCRIPTOR_DIGEST_INVALID",
        )

    started = _integer(
        snapshot["collection_started_at_ms"],
        "MARKET_HISTORY_COLLECTION_TIME_INVALID",
        minimum=0,
    )
    finished = _integer(
        snapshot["collection_finished_at_ms"],
        "MARKET_HISTORY_COLLECTION_TIME_INVALID",
        minimum=started,
    )
    generated = _integer(
        snapshot["generated_at_ms"],
        "MARKET_HISTORY_COLLECTION_TIME_INVALID",
        minimum=finished,
    )
    read_times = snapshot["read_finished_at_ms"]
    reads = snapshot["reads"]
    if (
            not isinstance(read_times, dict) or
            set(read_times) != set(READ_ORDER) or
            not isinstance(reads, dict) or
            set(reads) != set(READ_ORDER)):
        raise HistoryError("MARKET_HISTORY_READ_SET_INVALID")
    previous_time = started
    normalized_read_times: dict[str, int] = {}
    for tool in READ_ORDER:
        observed = _integer(
            read_times[tool],
            "MARKET_HISTORY_READ_TIME_INVALID",
            minimum=previous_time,
            maximum=finished,
        )
        normalized_read_times[tool] = observed
        previous_time = observed

    health = reads["system.get_health"]
    if (
            not isinstance(health, dict) or
            health.get("gateway_ready") is not True or
            health.get("remote_execution") is not True or
            health.get("remote_execution_configured") is not True or
            health.get("remote_execution_ready") is not True or
            health.get("execution_mode") != "SIMULATOR" or
            health.get("remote_execution_reason") != ""):
        raise HistoryError("MARKET_HISTORY_HEALTH_NOT_AUTHORITATIVE")
    execution_epoch = _text(
        health.get("execution_service_epoch"),
        "MARKET_HISTORY_EXECUTION_EPOCH_INVALID",
        identifier=True,
    )
    fencing_generation = _integer(
        health.get("execution_service_fencing_generation"),
        "MARKET_HISTORY_FENCING_GENERATION_INVALID",
        minimum=1,
    )

    quote = reads["market.get_quote"]
    required_quote_fields = {
        "source", "authoritative", "stale", "instrument", "bid", "ask",
        "observed_at_ms", "stale_after_ms",
    }
    if not isinstance(quote, dict) or not required_quote_fields.issubset(quote):
        raise HistoryError("MARKET_HISTORY_QUOTE_FIELDS_INVALID")
    if (
            quote["source"] != "SIMULATOR" or
            quote["authoritative"] is not True or
            quote["stale"] is not False or
            quote["instrument"] != "EUR.USD"):
        raise HistoryError("MARKET_HISTORY_QUOTE_NOT_AUTHORITATIVE")
    bid = _number(
        quote["bid"],
        "MARKET_HISTORY_QUOTE_PRICE_INVALID",
        positive=True,
    )
    ask = _number(
        quote["ask"],
        "MARKET_HISTORY_QUOTE_PRICE_INVALID",
        positive=True,
    )
    if bid > ask:
        raise HistoryError("MARKET_HISTORY_QUOTE_PRICE_INVALID")
    observed_at_ms = _integer(
        quote["observed_at_ms"],
        "MARKET_HISTORY_QUOTE_TIME_INVALID",
        minimum=0,
        maximum=normalized_read_times["market.get_quote"],
    )
    stale_after_ms = _integer(
        quote["stale_after_ms"],
        "MARKET_HISTORY_QUOTE_TIME_INVALID",
        minimum=observed_at_ms + 1,
        maximum=observed_at_ms + 60_000,
    )
    if normalized_read_times["market.get_quote"] > stale_after_ms:
        raise HistoryError("MARKET_HISTORY_QUOTE_STALE_AT_READ")

    body = dict(snapshot)
    claimed_body_sha256 = body.pop("body_sha256")
    _digest(
        claimed_body_sha256,
        "MARKET_HISTORY_SNAPSHOT_DIGEST_INVALID",
    )
    if claimed_body_sha256 != digest_document(body):
        raise HistoryError("MARKET_HISTORY_SNAPSHOT_DIGEST_INVALID")

    return {
        "snapshot_body_sha256": claimed_body_sha256,
        "snapshot_file_sha256": digest_bytes(contents),
        "domain_id": domain_id,
        "agent_uid": agent_uid,
        "instrument": "EUR.USD",
        "catalog_sha256": catalog_sha256,
        "descriptor_sha256": normalized_descriptors,
        "execution_service_epoch": execution_epoch,
        "execution_service_fencing_generation": fencing_generation,
        "collection_started_at_ms": started,
        "collection_finished_at_ms": finished,
        "generated_at_ms": generated,
        "quote_read_finished_at_ms":
            normalized_read_times["market.get_quote"],
        "quote": {
            "source": "SIMULATOR",
            "authoritative": True,
            "stale": False,
            "bid": bid,
            "ask": ask,
            "observed_at_ms": observed_at_ms,
            "stale_after_ms": stale_after_ms,
        },
    }


def _validate_lease_receipt(
    receipt: dict[str, Any],
    contents: bytes,
    normalized_snapshot: dict[str, Any],
) -> dict[str, Any]:
    _exact(
        receipt,
        LEASE_RECEIPT_FIELDS,
        "MARKET_HISTORY_LEASE_RECEIPT_FIELDS_INVALID",
    )
    if (
            receipt["schema"] != "hepta.shadow-watch-lease-receipt.v1" or
            receipt["version"] != 1):
        raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_SCHEMA_INVALID")
    domain_id = _text(
        receipt["domain_id"],
        "MARKET_HISTORY_LEASE_RECEIPT_DOMAIN_INVALID",
        identifier=True,
    )
    agent_id = _text(
        receipt["agent_id"],
        "MARKET_HISTORY_LEASE_RECEIPT_AGENT_INVALID",
        identifier=True,
    )
    agent_uid = _integer(
        receipt["agent_uid"],
        "MARKET_HISTORY_LEASE_RECEIPT_AGENT_INVALID",
        minimum=1,
    )
    if (
            domain_id != normalized_snapshot["domain_id"] or
            agent_id != domain_id or
            agent_uid != normalized_snapshot["agent_uid"]):
        raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_BINDING_INVALID")
    if (
            receipt["boundary"] != "WATCH" or
            receipt["accepted"] is not True or
            receipt["reason_code"] != "OK" or
            receipt["paper_authorized"] is not False or
            receipt["live_authorized"] is not False or
            receipt["mutation_authorized"] is not False):
        raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_BOUNDARY_INVALID")
    operation = receipt["operation"]
    if operation not in {"PROVISION", "ROTATE"}:
        raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_OPERATION_INVALID")
    generation = _integer(
        receipt["lease_generation"],
        "MARKET_HISTORY_WATCH_GENERATION_INVALID",
        minimum=1,
    )
    previous_generation = receipt["previous_lease_generation"]
    previous_receipt_digest = receipt["previous_receipt_body_sha256"]
    if operation == "PROVISION":
        if (
                previous_generation is not None or
                previous_receipt_digest is not None):
            raise HistoryError(
                "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID")
    else:
        if (
                _integer(
                    previous_generation,
                    "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID",
                    minimum=1,
                ) != generation - 1):
            raise HistoryError(
                "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID")
        _digest(
            previous_receipt_digest,
            "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID",
        )
    accepted_at_ms = _integer(
        receipt["accepted_at_ms"],
        "MARKET_HISTORY_LEASE_RECEIPT_TIME_INVALID",
        minimum=0,
    )
    ttl_seconds = _integer(
        receipt["ttl_seconds"],
        "MARKET_HISTORY_LEASE_RECEIPT_TTL_INVALID",
        minimum=1,
        maximum=MAXIMUM_WATCH_TTL_SECONDS,
    )
    expires_at_ms = _integer(
        receipt["expires_at_ms"],
        "MARKET_HISTORY_LEASE_RECEIPT_TIME_INVALID",
        minimum=accepted_at_ms + 1,
    )
    if expires_at_ms != accepted_at_ms + ttl_seconds * 1_000:
        raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_TIME_INVALID")
    if (
            accepted_at_ms >
            normalized_snapshot["collection_started_at_ms"] or
            normalized_snapshot["generated_at_ms"] > expires_at_ms or
            normalized_snapshot["quote"]["observed_at_ms"] < accepted_at_ms or
            normalized_snapshot["quote"]["observed_at_ms"] > expires_at_ms):
        raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_STALE")
    body = dict(receipt)
    claimed_body_sha256 = body.pop("body_sha256")
    _digest(
        claimed_body_sha256,
        "MARKET_HISTORY_LEASE_RECEIPT_DIGEST_INVALID",
    )
    if claimed_body_sha256 != digest_document(body):
        raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_DIGEST_INVALID")
    return {
        "watch_generation": generation,
        "watch_lease_receipt_body_sha256": claimed_body_sha256,
        "watch_lease_receipt_file_sha256": digest_bytes(contents),
        "watch_lease_operation": operation,
        "watch_lease_previous_generation": previous_generation,
        "watch_lease_previous_receipt_body_sha256":
            previous_receipt_digest,
        "watch_lease_accepted_at_ms": accepted_at_ms,
        "watch_lease_ttl_seconds": ttl_seconds,
        "watch_lease_expires_at_ms": expires_at_ms,
    }


def _validate_export_receipt(
    receipt: dict[str, Any],
    contents: bytes,
    normalized_snapshot: dict[str, Any],
    normalized_lease: dict[str, Any],
    *,
    snapshot_metadata: os.stat_result,
    lease_metadata: os.stat_result,
    export_metadata: os.stat_result,
) -> dict[str, Any]:
    _exact(
        receipt,
        EXPORT_RECEIPT_FIELDS,
        "MARKET_HISTORY_EXPORT_RECEIPT_FIELDS_INVALID",
    )
    if (
            receipt["schema"] != "hepta.shadow-watch-export-receipt.v1" or
            receipt["version"] != 1):
        raise HistoryError("MARKET_HISTORY_EXPORT_RECEIPT_SCHEMA_INVALID")
    reader_uid = _integer(
        receipt["reader_uid"],
        "MARKET_HISTORY_EXPORT_RECEIPT_READER_INVALID",
        minimum=1,
    )
    reader_gid = _integer(
        receipt["reader_gid"],
        "MARKET_HISTORY_EXPORT_RECEIPT_READER_INVALID",
        minimum=1,
    )
    if (
            receipt["domain_id"] != normalized_snapshot["domain_id"] or
            receipt["agent_uid"] != normalized_snapshot["agent_uid"] or
            reader_uid != os.geteuid() or
            snapshot_metadata.st_gid != reader_gid or
            lease_metadata.st_gid != reader_gid or
            export_metadata.st_gid != reader_gid):
        raise HistoryError("MARKET_HISTORY_EXPORT_RECEIPT_BINDING_INVALID")
    if (
            receipt["boundary"] != "WATCH_EXPORT" or
            receipt["paper_authorized"] is not False or
            receipt["live_authorized"] is not False or
            receipt["mutation_attempted"] is not False or
            receipt["direct_broker_access"] is not False):
        raise HistoryError("MARKET_HISTORY_EXPORT_RECEIPT_BOUNDARY_INVALID")
    if (
            receipt["lease_generation"] !=
            normalized_lease["watch_generation"] or
            receipt["lease_receipt_body_sha256"] !=
            normalized_lease["watch_lease_receipt_body_sha256"] or
            receipt["lease_receipt_file_sha256"] !=
            normalized_lease["watch_lease_receipt_file_sha256"] or
            receipt["snapshot_body_sha256"] !=
            normalized_snapshot["snapshot_body_sha256"] or
            receipt["snapshot_file_sha256"] !=
            normalized_snapshot["snapshot_file_sha256"] or
            receipt["snapshot_generated_at_ms"] !=
            normalized_snapshot["generated_at_ms"]):
        raise HistoryError("MARKET_HISTORY_EXPORT_RECEIPT_BINDING_INVALID")
    exported_at_ms = _integer(
        receipt["exported_at_ms"],
        "MARKET_HISTORY_EXPORT_RECEIPT_TIME_INVALID",
        minimum=normalized_snapshot["generated_at_ms"],
        maximum=normalized_lease["watch_lease_expires_at_ms"],
    )
    if exported_at_ms > normalized_snapshot["quote"]["stale_after_ms"]:
        raise HistoryError("MARKET_HISTORY_EXPORT_RECEIPT_STALE")
    for field in (
            "lease_receipt_body_sha256", "lease_receipt_file_sha256",
            "snapshot_body_sha256", "snapshot_file_sha256"):
        _digest(
            receipt[field],
            "MARKET_HISTORY_EXPORT_RECEIPT_DIGEST_INVALID",
        )
    body = dict(receipt)
    claimed = body.pop("body_sha256")
    _digest(claimed, "MARKET_HISTORY_EXPORT_RECEIPT_DIGEST_INVALID")
    if claimed != digest_document(body):
        raise HistoryError("MARKET_HISTORY_EXPORT_RECEIPT_DIGEST_INVALID")
    return {
        "watch_export_receipt_body_sha256": claimed,
        "watch_export_receipt_file_sha256": digest_bytes(contents),
        "watch_exported_at_ms": exported_at_ms,
        "watch_export_reader_uid": reader_uid,
        "watch_export_reader_gid": reader_gid,
    }


def _directory_metadata(path: Path, *, create: bool) -> os.stat_result:
    if create:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as error:
            raise HistoryError("MARKET_HISTORY_DIRECTORY_CREATE_FAILED") from error
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_DIRECTORY_READ_FAILED") from error
    if (
            not stat.S_ISDIR(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        raise HistoryError("MARKET_HISTORY_DIRECTORY_METADATA_INVALID")
    if create and (metadata.st_uid, metadata.st_gid) != (
            os.geteuid(), os.getegid()):
        raise HistoryError("MARKET_HISTORY_DIRECTORY_OWNER_INVALID")
    return metadata


def _record_body(
    normalized: dict[str, Any],
    lease: dict[str, Any],
    export: dict[str, Any],
    *,
    sequence: int,
    cadence_ms: int,
    maximum_jitter_ms: int,
    previous_record_sha256: str | None,
    quote_changed: bool,
) -> dict[str, Any]:
    return {
        "schema": "hepta.shadow-market-history-record.v3",
        "version": 3,
        "sequence": sequence,
        "cadence_ms": cadence_ms,
        "maximum_jitter_ms": maximum_jitter_ms,
        "previous_record_sha256": previous_record_sha256,
        "quote_changed": quote_changed,
        **normalized,
        **lease,
        **export,
    }


def _record_name(sequence: int) -> str:
    return f"record-{sequence:020d}.json"


@contextmanager
def _history_lock(path: Path, *, exclusive: bool):
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
        )
        fcntl.flock(
            descriptor,
            fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
        )
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_LOCK_FAILED") from error
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_record(
    record: dict[str, Any],
    *,
    expected_sequence: int,
    expected_previous: str | None,
    expected_owner: tuple[int, int],
    path: Path,
) -> dict[str, Any]:
    _exact(record, RECORD_FIELDS, "MARKET_HISTORY_RECORD_FIELDS_INVALID")
    if (
            record["schema"] != "hepta.shadow-market-history-record.v3" or
            record["version"] != 3 or
            record["sequence"] != expected_sequence or
            record["previous_record_sha256"] != expected_previous):
        raise HistoryError("MARKET_HISTORY_CHAIN_INVALID")
    _validate_cadence(record["cadence_ms"], record["maximum_jitter_ms"])
    body = dict(record)
    claimed = body.pop("record_sha256")
    _digest(claimed, "MARKET_HISTORY_RECORD_DIGEST_INVALID")
    if claimed != digest_document(body):
        raise HistoryError("MARKET_HISTORY_RECORD_DIGEST_INVALID")
    expected_name = _record_name(expected_sequence)
    if path.name != expected_name:
        raise HistoryError("MARKET_HISTORY_RECORD_NAME_INVALID")
    metadata = os.lstat(path)
    if (
            not stat.S_ISREG(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or
            metadata.st_nlink != 1 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            (metadata.st_uid, metadata.st_gid) != expected_owner):
        raise HistoryError("MARKET_HISTORY_RECORD_METADATA_INVALID")
    _digest(
        record["snapshot_body_sha256"],
        "MARKET_HISTORY_SNAPSHOT_DIGEST_INVALID",
    )
    _digest(
        record["snapshot_file_sha256"],
        "MARKET_HISTORY_SNAPSHOT_DIGEST_INVALID",
    )
    _digest(
        record["catalog_sha256"],
        "MARKET_HISTORY_CATALOG_DIGEST_INVALID",
    )
    descriptors = record["descriptor_sha256"]
    if not isinstance(descriptors, dict) or set(descriptors) != set(READ_ORDER):
        raise HistoryError("MARKET_HISTORY_DESCRIPTOR_SET_INVALID")
    for digest in descriptors.values():
        _digest(digest, "MARKET_HISTORY_DESCRIPTOR_DIGEST_INVALID")
    _text(record["domain_id"], "MARKET_HISTORY_DOMAIN_INVALID", identifier=True)
    _integer(
        record["agent_uid"],
        "MARKET_HISTORY_AGENT_UID_INVALID",
        minimum=1,
    )
    if record["instrument"] != "EUR.USD":
        raise HistoryError("MARKET_HISTORY_INSTRUMENT_INVALID")
    _text(
        record["execution_service_epoch"],
        "MARKET_HISTORY_EXECUTION_EPOCH_INVALID",
        identifier=True,
    )
    _integer(
        record["execution_service_fencing_generation"],
        "MARKET_HISTORY_FENCING_GENERATION_INVALID",
        minimum=1,
    )
    watch_generation = _integer(
        record["watch_generation"],
        "MARKET_HISTORY_WATCH_GENERATION_INVALID",
        minimum=1,
    )
    _digest(
        record["watch_lease_receipt_body_sha256"],
        "MARKET_HISTORY_LEASE_RECEIPT_DIGEST_INVALID",
    )
    _digest(
        record["watch_lease_receipt_file_sha256"],
        "MARKET_HISTORY_LEASE_RECEIPT_DIGEST_INVALID",
    )
    operation = record["watch_lease_operation"]
    if operation not in {"PROVISION", "ROTATE"}:
        raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_OPERATION_INVALID")
    previous_generation = record["watch_lease_previous_generation"]
    previous_receipt_digest = (
        record["watch_lease_previous_receipt_body_sha256"])
    if operation == "PROVISION":
        if (
                previous_generation is not None or
                previous_receipt_digest is not None):
            raise HistoryError(
                "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID")
    else:
        if (
                _integer(
                    previous_generation,
                    "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID",
                    minimum=1,
                ) != watch_generation - 1):
            raise HistoryError(
                "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID")
        _digest(
            previous_receipt_digest,
            "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID",
        )
    accepted_at_ms = _integer(
        record["watch_lease_accepted_at_ms"],
        "MARKET_HISTORY_LEASE_RECEIPT_TIME_INVALID",
        minimum=0,
    )
    ttl_seconds = _integer(
        record["watch_lease_ttl_seconds"],
        "MARKET_HISTORY_LEASE_RECEIPT_TTL_INVALID",
        minimum=1,
        maximum=MAXIMUM_WATCH_TTL_SECONDS,
    )
    expires_at_ms = _integer(
        record["watch_lease_expires_at_ms"],
        "MARKET_HISTORY_LEASE_RECEIPT_TIME_INVALID",
        minimum=accepted_at_ms + 1,
    )
    if expires_at_ms != accepted_at_ms + ttl_seconds * 1_000:
        raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_TIME_INVALID")
    _digest(
        record["watch_export_receipt_body_sha256"],
        "MARKET_HISTORY_EXPORT_RECEIPT_DIGEST_INVALID",
    )
    _digest(
        record["watch_export_receipt_file_sha256"],
        "MARKET_HISTORY_EXPORT_RECEIPT_DIGEST_INVALID",
    )
    _integer(
        record["watch_export_reader_uid"],
        "MARKET_HISTORY_EXPORT_RECEIPT_READER_INVALID",
        minimum=1,
    )
    _integer(
        record["watch_export_reader_gid"],
        "MARKET_HISTORY_EXPORT_RECEIPT_READER_INVALID",
        minimum=1,
    )
    started = _integer(
        record["collection_started_at_ms"],
        "MARKET_HISTORY_COLLECTION_TIME_INVALID",
        minimum=0,
    )
    finished = _integer(
        record["collection_finished_at_ms"],
        "MARKET_HISTORY_COLLECTION_TIME_INVALID",
        minimum=started,
    )
    generated = _integer(
        record["generated_at_ms"],
        "MARKET_HISTORY_COLLECTION_TIME_INVALID",
        minimum=finished,
    )
    quote_read = _integer(
        record["quote_read_finished_at_ms"],
        "MARKET_HISTORY_QUOTE_TIME_INVALID",
        minimum=started,
        maximum=finished,
    )
    quote = _exact(
        record["quote"],
        QUOTE_FIELDS,
        "MARKET_HISTORY_QUOTE_FIELDS_INVALID",
    )
    if not isinstance(record["quote_changed"], bool):
        raise HistoryError("MARKET_HISTORY_QUOTE_CHANGE_FLAG_INVALID")
    if (
            quote["source"] != "SIMULATOR" or
            quote["authoritative"] is not True or
            quote["stale"] is not False):
        raise HistoryError("MARKET_HISTORY_QUOTE_NOT_AUTHORITATIVE")
    bid = _number(
        quote["bid"],
        "MARKET_HISTORY_QUOTE_PRICE_INVALID",
        positive=True,
    )
    ask = _number(
        quote["ask"],
        "MARKET_HISTORY_QUOTE_PRICE_INVALID",
        positive=True,
    )
    if bid > ask:
        raise HistoryError("MARKET_HISTORY_QUOTE_PRICE_INVALID")
    observed = _integer(
        quote["observed_at_ms"],
        "MARKET_HISTORY_QUOTE_TIME_INVALID",
        minimum=0,
        maximum=quote_read,
    )
    stale_after = _integer(
        quote["stale_after_ms"],
        "MARKET_HISTORY_QUOTE_TIME_INVALID",
        minimum=observed + 1,
        maximum=observed + 60_000,
    )
    if quote_read > stale_after or generated < finished:
        raise HistoryError("MARKET_HISTORY_QUOTE_STALE_AT_READ")
    if (
            accepted_at_ms > started or
            generated > expires_at_ms or
            observed < accepted_at_ms or
            observed > expires_at_ms):
        raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_STALE")
    _integer(
        record["watch_exported_at_ms"],
        "MARKET_HISTORY_EXPORT_RECEIPT_TIME_INVALID",
        minimum=generated,
        maximum=min(expires_at_ms, stale_after),
    )
    return record


def _entry_exists(path: Path) -> bool:
    try:
        os.lstat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_DIRECTORY_READ_FAILED") from error


def _make_head(
    *,
    record_count: int,
    history_record_bytes: int,
    first_record_sha256: str,
    last_record: dict[str, Any],
    last_record_contents: bytes,
    audit_cursor_sequence: int,
    audit_expected_previous_sha256: str | None,
) -> dict[str, Any]:
    body = {
        "schema": "hepta.shadow-market-history-head.v1",
        "version": 1,
        "record_schema": "hepta.shadow-market-history-record.v3",
        "record_count": record_count,
        "history_record_bytes": history_record_bytes,
        "first_record_sha256": first_record_sha256,
        "last_record_sha256": last_record["record_sha256"],
        "last_record_file_sha256": digest_bytes(last_record_contents),
        "last_record_name": _record_name(record_count),
        "last_previous_record_sha256":
            last_record["previous_record_sha256"],
        "last_snapshot_body_sha256":
            last_record["snapshot_body_sha256"],
        "last_snapshot_file_sha256":
            last_record["snapshot_file_sha256"],
        "cadence_ms": last_record["cadence_ms"],
        "maximum_jitter_ms": last_record["maximum_jitter_ms"],
        "audit_cursor_sequence": audit_cursor_sequence,
        "audit_expected_previous_sha256":
            audit_expected_previous_sha256,
    }
    return {**body, "body_sha256": digest_document(body)}


def _validate_head(
    head: dict[str, Any],
    contents: bytes,
    *,
    path: Path,
    expected_owner: tuple[int, int],
) -> dict[str, Any]:
    _exact(head, HEAD_FIELDS, "MARKET_HISTORY_HEAD_FIELDS_INVALID")
    if (
            head["schema"] != "hepta.shadow-market-history-head.v1" or
            head["version"] != 1 or
            head["record_schema"] !=
            "hepta.shadow-market-history-record.v3"):
        raise HistoryError("MARKET_HISTORY_HEAD_SCHEMA_INVALID")
    count = _integer(
        head["record_count"],
        "MARKET_HISTORY_HEAD_COUNT_INVALID",
        minimum=1,
        maximum=MAX_HISTORY_RECORDS,
    )
    _integer(
        head["history_record_bytes"],
        "MARKET_HISTORY_HEAD_BYTES_INVALID",
        minimum=1,
        maximum=MAXIMUM_STORAGE_GUARD_BYTES,
    )
    for field in (
            "first_record_sha256", "last_record_sha256",
            "last_record_file_sha256", "last_snapshot_body_sha256",
            "last_snapshot_file_sha256"):
        _digest(head[field], "MARKET_HISTORY_HEAD_DIGEST_INVALID")
    previous = head["last_previous_record_sha256"]
    if count == 1:
        if previous is not None:
            raise HistoryError("MARKET_HISTORY_HEAD_CHAIN_INVALID")
    else:
        _digest(previous, "MARKET_HISTORY_HEAD_CHAIN_INVALID")
    if head["last_record_name"] != _record_name(count):
        raise HistoryError("MARKET_HISTORY_HEAD_CHAIN_INVALID")
    _validate_cadence(head["cadence_ms"], head["maximum_jitter_ms"])
    cursor = _integer(
        head["audit_cursor_sequence"],
        "MARKET_HISTORY_HEAD_AUDIT_CURSOR_INVALID",
        minimum=1,
        maximum=count,
    )
    audit_previous = head["audit_expected_previous_sha256"]
    if cursor == 1:
        if audit_previous is not None:
            raise HistoryError("MARKET_HISTORY_HEAD_AUDIT_CURSOR_INVALID")
    else:
        _digest(
            audit_previous,
            "MARKET_HISTORY_HEAD_AUDIT_CURSOR_INVALID",
        )
    body = dict(head)
    claimed = body.pop("body_sha256")
    _digest(claimed, "MARKET_HISTORY_HEAD_DIGEST_INVALID")
    if claimed != digest_document(body):
        raise HistoryError("MARKET_HISTORY_HEAD_DIGEST_INVALID")
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_HEAD_READ_FAILED") from error
    if (
            not stat.S_ISREG(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or
            metadata.st_nlink != 1 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            (metadata.st_uid, metadata.st_gid) != expected_owner or
            metadata.st_size != len(contents)):
        raise HistoryError("MARKET_HISTORY_HEAD_METADATA_INVALID")
    return head


def _load_head(
    history_directory: Path,
    *,
    expected_owner: tuple[int, int],
) -> tuple[dict[str, Any], bytes]:
    path = history_directory / HEAD_NAME
    head, contents = _load_canonical(path, "MARKET_HISTORY_HEAD")
    return (
        _validate_head(
            head,
            contents,
            path=path,
            expected_owner=expected_owner,
        ),
        contents,
    )


def _validate_head_tail(
    head: dict[str, Any],
    last_record: dict[str, Any],
    last_contents: bytes,
) -> None:
    if (
            head["last_record_sha256"] != last_record["record_sha256"] or
            head["last_record_file_sha256"] != digest_bytes(last_contents) or
            head["last_previous_record_sha256"] !=
            last_record["previous_record_sha256"] or
            head["last_snapshot_body_sha256"] !=
            last_record["snapshot_body_sha256"] or
            head["last_snapshot_file_sha256"] !=
            last_record["snapshot_file_sha256"] or
            head["cadence_ms"] != last_record["cadence_ms"] or
            head["maximum_jitter_ms"] !=
            last_record["maximum_jitter_ms"] or
            head["history_record_bytes"] < len(last_contents)):
        raise HistoryError("MARKET_HISTORY_HEAD_TAIL_MISMATCH")
    if (
            head["record_count"] == 1 and
            head["first_record_sha256"] != last_record["record_sha256"]):
        raise HistoryError("MARKET_HISTORY_HEAD_TAIL_MISMATCH")


def _load_record_direct(
    history_directory: Path,
    sequence: int,
    *,
    expected_previous: str | None,
    expected_owner: tuple[int, int],
) -> tuple[dict[str, Any], bytes]:
    path = history_directory / _record_name(sequence)
    record, contents = _load_canonical(path, "MARKET_HISTORY_RECORD")
    return (
        _validate_record(
            record,
            expected_sequence=sequence,
            expected_previous=expected_previous,
            expected_owner=expected_owner,
            path=path,
        ),
        contents,
    )


def _load_fast_state(
    history_directory: Path,
) -> tuple[
        dict[str, Any] | None,
        bytes,
        dict[str, Any] | None,
        bytes,
        tuple[int, int],
    ]:
    directory = _directory_metadata(history_directory, create=False)
    owner = (directory.st_uid, directory.st_gid)
    head_path = history_directory / HEAD_NAME
    pending_path = history_directory / HEAD_PENDING_NAME
    if _entry_exists(pending_path):
        raise HistoryError("MARKET_HISTORY_HEAD_RECOVERY_REQUIRED")
    if not _entry_exists(head_path):
        if _entry_exists(history_directory / _record_name(1)):
            raise HistoryError("MARKET_HISTORY_HEAD_RECOVERY_REQUIRED")
        return None, b"", None, b"", owner
    head, head_contents = _load_head(
        history_directory,
        expected_owner=owner,
    )
    next_path = history_directory / _record_name(head["record_count"] + 1)
    if _entry_exists(next_path):
        raise HistoryError("MARKET_HISTORY_HEAD_RECOVERY_REQUIRED")
    last_record, last_contents = _load_record_direct(
        history_directory,
        head["record_count"],
        expected_previous=head["last_previous_record_sha256"],
        expected_owner=owner,
    )
    _validate_head_tail(head, last_record, last_contents)
    return head, head_contents, last_record, last_contents, owner


def _scan_history_unlocked(
    history_directory: Path,
    *,
    cadence_ms: int | None,
    maximum_jitter_ms: int | None,
    require_head: bool,
    previous_segment_record: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[bytes]]:
    directory = _directory_metadata(history_directory, create=False)
    owner = (directory.st_uid, directory.st_gid)
    try:
        entries = list(history_directory.iterdir())
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_DIRECTORY_READ_FAILED") from error
    record_paths: list[tuple[int, Path]] = []
    for path in entries:
        if path.name in {HEAD_NAME, HEAD_PENDING_NAME}:
            continue
        if LEGACY_RECORD_NAME_PATTERN.fullmatch(path.name) is not None:
            raise HistoryError(
                "MARKET_HISTORY_LEGACY_SEGMENT_MIGRATION_REQUIRED")
        match = RECORD_NAME_PATTERN.fullmatch(path.name)
        if match is None:
            raise HistoryError("MARKET_HISTORY_DIRECTORY_ENTRY_INVALID")
        record_paths.append((int(match.group(1)), path))
    record_paths.sort(key=lambda item: item[0])
    if len(record_paths) > MAX_HISTORY_RECORDS:
        raise HistoryError("MARKET_HISTORY_RECORD_LIMIT_EXCEEDED")
    records: list[dict[str, Any]] = []
    contents_list: list[bytes] = []
    previous_digest: str | None = None
    for expected_sequence, (sequence, path) in enumerate(
            record_paths, start=1):
        if sequence != expected_sequence:
            raise HistoryError("MARKET_HISTORY_CHAIN_INVALID")
        record, contents = _load_canonical(path, "MARKET_HISTORY_RECORD")
        record = _validate_record(
            record,
            expected_sequence=expected_sequence,
            expected_previous=previous_digest,
            expected_owner=owner,
            path=path,
        )
        if cadence_ms is not None and record["cadence_ms"] != cadence_ms:
            raise HistoryError("MARKET_HISTORY_CADENCE_BINDING_DRIFT")
        if (
                maximum_jitter_ms is not None and
                record["maximum_jitter_ms"] != maximum_jitter_ms):
            raise HistoryError("MARKET_HISTORY_JITTER_BINDING_DRIFT")
        if records:
            _validate_record_transition(records[-1], record)
            _validate_authority_binding(records[0], record)
        elif record["watch_lease_operation"] != "PROVISION":
            if previous_segment_record is None:
                raise HistoryError(
                    "MARKET_HISTORY_SEGMENT_MUST_START_WITH_PROVISION")
            _validate_segment_transition(previous_segment_record, record)
        elif record["quote_changed"] is not True:
            raise HistoryError("MARKET_HISTORY_QUOTE_CHANGE_FLAG_INVALID")
        records.append(record)
        contents_list.append(contents)
        previous_digest = record["record_sha256"]
    if require_head:
        if _entry_exists(history_directory / HEAD_PENDING_NAME):
            raise HistoryError("MARKET_HISTORY_HEAD_RECOVERY_REQUIRED")
        if records:
            if not _entry_exists(history_directory / HEAD_NAME):
                raise HistoryError("MARKET_HISTORY_HEAD_RECOVERY_REQUIRED")
            head, _ = _load_head(
                history_directory,
                expected_owner=owner,
            )
            expected = _make_head(
                record_count=len(records),
                history_record_bytes=sum(map(len, contents_list)),
                first_record_sha256=records[0]["record_sha256"],
                last_record=records[-1],
                last_record_contents=contents_list[-1],
                audit_cursor_sequence=head["audit_cursor_sequence"],
                audit_expected_previous_sha256=
                    head["audit_expected_previous_sha256"],
            )
            if head != expected:
                raise HistoryError("MARKET_HISTORY_HEAD_CHAIN_INVALID")
            cursor = head["audit_cursor_sequence"]
            if (
                    records[cursor - 1]["previous_record_sha256"] !=
                    head["audit_expected_previous_sha256"]):
                raise HistoryError(
                    "MARKET_HISTORY_HEAD_AUDIT_CURSOR_INVALID")
        elif _entry_exists(history_directory / HEAD_NAME):
            raise HistoryError("MARKET_HISTORY_HEAD_CHAIN_INVALID")
    return records, contents_list


def load_history(
    history_directory: Path,
    *,
    cadence_ms: int | None = None,
    maximum_jitter_ms: int | None = None,
    previous_segment_history_directory: Path | None = None,
) -> list[dict[str, Any]]:
    """Perform a complete immutable chain and mutable-head audit."""

    previous_segment_record = _previous_segment_record(
        previous_segment_history_directory,
        cadence_ms=cadence_ms,
        maximum_jitter_ms=maximum_jitter_ms,
    )
    _directory_metadata(history_directory, create=False)
    with _history_lock(history_directory, exclusive=False):
        records, _ = _scan_history_unlocked(
            history_directory,
            cadence_ms=cadence_ms,
            maximum_jitter_ms=maximum_jitter_ms,
            require_head=True,
            previous_segment_record=previous_segment_record,
        )
        return records


def _recovery_unlink(
    path: Path,
    *,
    expected_owner: tuple[int, int],
) -> None:
    if not _entry_exists(path):
        return
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_HEAD_RECOVERY_FAILED") from error
    if (
            not stat.S_ISREG(metadata.st_mode) or
            stat.S_ISLNK(metadata.st_mode) or
            metadata.st_nlink != 1 or
            stat.S_IMODE(metadata.st_mode) != 0o600 or
            (metadata.st_uid, metadata.st_gid) != expected_owner):
        raise HistoryError("MARKET_HISTORY_HEAD_METADATA_INVALID")
    try:
        path.unlink()
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_HEAD_RECOVERY_FAILED") from error


def recover_history_head(
    history_directory: Path,
    *,
    cadence_ms: int,
    maximum_jitter_ms: int = DEFAULT_MAXIMUM_JITTER_MS,
    maximum_history_bytes: int = DEFAULT_MAXIMUM_HISTORY_BYTES,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
) -> dict[str, Any]:
    """Full-audit immutable records and rebuild only the mutable head."""

    _validate_cadence(cadence_ms, maximum_jitter_ms)
    _validate_storage_guard(maximum_history_bytes, minimum_free_bytes)
    directory = _directory_metadata(history_directory, create=False)
    owner = (directory.st_uid, directory.st_gid)
    with _history_lock(history_directory, exclusive=True):
        records, contents_list = _scan_history_unlocked(
            history_directory,
            cadence_ms=cadence_ms,
            maximum_jitter_ms=maximum_jitter_ms,
            require_head=False,
        )
        _recovery_unlink(
            history_directory / HEAD_PENDING_NAME,
            expected_owner=owner,
        )
        if not records:
            _recovery_unlink(
                history_directory / HEAD_NAME,
                expected_owner=owner,
            )
            return {
                "status": "empty",
                "record_count": 0,
                "history_record_bytes": 0,
                "history_index_bytes": 0,
                "history_storage_bytes": 0,
                "directory_entries_scanned": 0,
            }
        head = _make_head(
            record_count=len(records),
            history_record_bytes=sum(map(len, contents_list)),
            first_record_sha256=records[0]["record_sha256"],
            last_record=records[-1],
            last_record_contents=contents_list[-1],
            audit_cursor_sequence=1,
            audit_expected_previous_sha256=None,
        )
        head_contents = canonical_bytes(head)
        _guard_history_capacity(
            history_directory,
            current_record_bytes=sum(map(len, contents_list)),
            incoming_record_bytes=0,
            new_head_bytes=len(head_contents),
            maximum_history_bytes=maximum_history_bytes,
            minimum_free_bytes=minimum_free_bytes,
        )
        _atomic_replace_head(
            history_directory / HEAD_NAME,
            head_contents,
        )
        return {
            "status": "recovered",
            "record_count": len(records),
            "history_head_sha256": records[-1]["record_sha256"],
            "history_record_bytes": head["history_record_bytes"],
            "history_index_bytes": len(head_contents),
            "history_storage_bytes":
                head["history_record_bytes"] + len(head_contents),
            "history_index_body_sha256": head["body_sha256"],
            "history_index_file_sha256": digest_bytes(head_contents),
            "directory_entries_scanned":
                len(records) + 1,
        }


def audit_history(
    history_directory: Path,
    *,
    cadence_ms: int,
    maximum_jitter_ms: int = DEFAULT_MAXIMUM_JITTER_MS,
    previous_segment_history_directory: Path | None = None,
) -> dict[str, Any]:
    """Return a full-chain final-audit summary; never used by hot append."""

    records = load_history(
        history_directory,
        cadence_ms=cadence_ms,
        maximum_jitter_ms=maximum_jitter_ms,
        previous_segment_history_directory=
            previous_segment_history_directory,
    )
    if not records:
        raise HistoryError("MARKET_HISTORY_EMPTY")
    record_bytes = sum(
        os.lstat(history_directory / _record_name(record["sequence"])).st_size
        for record in records
    )
    head_contents = (history_directory / HEAD_NAME).read_bytes()
    return {
        "status": "valid",
        "record_count": len(records),
        "history_head_sha256": records[-1]["record_sha256"],
        "history_record_bytes": record_bytes,
        "history_index_bytes": len(head_contents),
        "history_storage_bytes": record_bytes + len(head_contents),
        "source_sha256": _source_digest(
            "SHADOW_HISTORY_RECORDS",
            [record["record_sha256"] for record in records],
        ),
        "directory_entries_scanned": len(records) + 1,
    }


def _load_record_self(
    history_directory: Path,
    sequence: int,
    *,
    expected_owner: tuple[int, int],
) -> tuple[dict[str, Any], bytes]:
    path = history_directory / _record_name(sequence)
    record, contents = _load_canonical(path, "MARKET_HISTORY_RECORD")
    previous = record.get("previous_record_sha256")
    return (
        _validate_record(
            record,
            expected_sequence=sequence,
            expected_previous=previous,
            expected_owner=expected_owner,
            path=path,
        ),
        contents,
    )


def _load_history_tail(
    history_directory: Path,
    *,
    cadence_ms: int,
    maximum_jitter_ms: int,
    maximum_records: int,
) -> tuple[
        list[dict[str, Any]],
        dict[str, Any],
        str | None,
        bytes,
    ]:
    _integer(
        maximum_records,
        "MARKET_HISTORY_MATERIALIZATION_WINDOW_INVALID",
        minimum=1,
        maximum=MAXIMUM_MATERIALIZATION_RECORDS,
    )
    directory = _directory_metadata(history_directory, create=False)
    owner = (directory.st_uid, directory.st_gid)
    with _history_lock(history_directory, exclusive=False):
        head, head_contents, last, last_contents, _ = _load_fast_state(
            history_directory)
        if head is None or last is None:
            raise HistoryError("MARKET_HISTORY_EMPTY")
        if (
                head["cadence_ms"] != cadence_ms or
                head["maximum_jitter_ms"] != maximum_jitter_ms):
            raise HistoryError("MARKET_HISTORY_CADENCE_BINDING_DRIFT")
        start = max(1, head["record_count"] - maximum_records + 1)
        predecessor_digest: str | None = None
        if start > 1:
            predecessor, _ = _load_record_self(
                history_directory,
                start - 1,
                expected_owner=owner,
            )
            predecessor_digest = predecessor["record_sha256"]
        records: list[dict[str, Any]] = []
        previous_digest = predecessor_digest
        for sequence in range(start, head["record_count"] + 1):
            if sequence == head["record_count"]:
                record = last
                contents = last_contents
                if record["previous_record_sha256"] != previous_digest:
                    raise HistoryError("MARKET_HISTORY_CHAIN_INVALID")
            else:
                record, contents = _load_record_direct(
                    history_directory,
                    sequence,
                    expected_previous=previous_digest,
                    expected_owner=owner,
                )
            if records:
                _validate_record_transition(records[-1], record)
                _validate_authority_binding(records[0], record)
            records.append(record)
            previous_digest = record["record_sha256"]
        if (
                records[-1]["record_sha256"] !=
                head["last_record_sha256"] or
                digest_bytes(last_contents) !=
                head["last_record_file_sha256"]):
            raise HistoryError("MARKET_HISTORY_HEAD_TAIL_MISMATCH")
        return records, head, predecessor_digest, head_contents


def _validate_authority_binding(
    first: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    fields = (
        "cadence_ms",
        "maximum_jitter_ms",
        "domain_id",
        "agent_uid",
        "instrument",
        "catalog_sha256",
        "descriptor_sha256",
        "execution_service_epoch",
        "execution_service_fencing_generation",
    )
    if any(candidate[field] != first[field] for field in fields):
        raise HistoryError("MARKET_HISTORY_AUTHORITY_BINDING_DRIFT")


def _quote_changed(
    previous_quote: dict[str, Any],
    candidate_quote: dict[str, Any],
) -> bool:
    """Classify one already-normalized authoritative quote transition."""

    previous_observed = previous_quote["observed_at_ms"]
    candidate_observed = candidate_quote["observed_at_ms"]
    if candidate_observed < previous_observed:
        raise HistoryError("MARKET_HISTORY_TIME_NOT_MONOTONIC")
    if candidate_observed == previous_observed:
        if candidate_quote != previous_quote:
            raise HistoryError("MARKET_HISTORY_QUOTE_MUTATION")
        return False
    return True


def _validate_quote_transition(
    previous: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    changed = _quote_changed(previous["quote"], candidate["quote"])
    if candidate["quote_changed"] is not changed:
        raise HistoryError("MARKET_HISTORY_QUOTE_CHANGE_FLAG_INVALID")


def _validate_record_transition(
    previous: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    if (
            candidate["collection_started_at_ms"] <=
            previous["collection_finished_at_ms"] or
            candidate["generated_at_ms"] <= previous["generated_at_ms"]):
        raise HistoryError("MARKET_HISTORY_TIME_NOT_MONOTONIC")
    _validate_quote_transition(previous, candidate)
    previous_generation = previous["watch_generation"]
    candidate_generation = candidate["watch_generation"]
    if candidate_generation not in {
            previous_generation,
            previous_generation + 1}:
        raise HistoryError("MARKET_HISTORY_WATCH_GENERATION_DRIFT")
    capture_delta = (
        candidate["collection_started_at_ms"] -
        previous["collection_started_at_ms"]
    )
    quote_delta = (
        candidate["quote"]["observed_at_ms"] -
        previous["quote"]["observed_at_ms"]
    )
    if candidate_generation == previous_generation:
        if (
                candidate["watch_lease_receipt_body_sha256"] !=
                previous["watch_lease_receipt_body_sha256"] or
                candidate["watch_lease_receipt_file_sha256"] !=
                previous["watch_lease_receipt_file_sha256"]):
            raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_DRIFT")
        lower = previous["cadence_ms"] - previous["maximum_jitter_ms"]
        upper = previous["cadence_ms"] + previous["maximum_jitter_ms"]
        if not lower <= capture_delta <= upper:
            raise HistoryError("MARKET_HISTORY_CADENCE_GAP")
        if (
                candidate["quote_changed"] is True and
                quote_delta > BUILDER_MAXIMUM_QUOTE_GAP_MS):
            raise HistoryError("MARKET_HISTORY_QUOTE_GAP")
        return
    if (
            candidate["watch_lease_operation"] != "ROTATE" or
            candidate["watch_lease_previous_generation"] !=
            previous_generation or
            candidate["watch_lease_previous_receipt_body_sha256"] !=
            previous["watch_lease_receipt_body_sha256"]):
        raise HistoryError(
            "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID")
    if (
            capture_delta > BUILDER_MAXIMUM_QUOTE_GAP_MS or
            (
                candidate["quote_changed"] is True and
                quote_delta > BUILDER_MAXIMUM_QUOTE_GAP_MS
            )):
        raise HistoryError("MARKET_HISTORY_ROTATION_SEGMENT_REQUIRED")


def _validate_segment_transition(
    previous: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    _validate_authority_binding(previous, candidate)
    if (
            candidate["collection_started_at_ms"] <=
            previous["collection_finished_at_ms"] or
            candidate["generated_at_ms"] <= previous["generated_at_ms"]):
        raise HistoryError("MARKET_HISTORY_TIME_NOT_MONOTONIC")
    _validate_quote_transition(previous, candidate)
    previous_generation = previous["watch_generation"]
    candidate_generation = candidate["watch_generation"]
    if candidate_generation == previous_generation:
        if (
                candidate["watch_lease_receipt_body_sha256"] !=
                previous["watch_lease_receipt_body_sha256"] or
                candidate["watch_lease_receipt_file_sha256"] !=
                previous["watch_lease_receipt_file_sha256"]):
            raise HistoryError("MARKET_HISTORY_LEASE_RECEIPT_DRIFT")
        return
    if (
            candidate_generation != previous_generation + 1 or
            candidate["watch_lease_operation"] != "ROTATE" or
            candidate["watch_lease_previous_generation"] !=
            previous_generation or
            candidate["watch_lease_previous_receipt_body_sha256"] !=
            previous["watch_lease_receipt_body_sha256"]):
        raise HistoryError(
            "MARKET_HISTORY_LEASE_RECEIPT_ROTATION_CHAIN_INVALID")


def _previous_segment_record(
    history_directory: Path | None,
    *,
    cadence_ms: int | None,
    maximum_jitter_ms: int | None,
) -> dict[str, Any] | None:
    if history_directory is None:
        return None
    directory = _directory_metadata(history_directory, create=False)
    owner = (directory.st_uid, directory.st_gid)
    with _history_lock(history_directory, exclusive=False):
        head, _, record, _, _ = _load_fast_state(history_directory)
        if head is None or record is None:
            raise HistoryError("MARKET_HISTORY_PREVIOUS_SEGMENT_EMPTY")
        if cadence_ms is not None and head["cadence_ms"] != cadence_ms:
            raise HistoryError("MARKET_HISTORY_CADENCE_BINDING_DRIFT")
        if (
                maximum_jitter_ms is not None and
                head["maximum_jitter_ms"] != maximum_jitter_ms):
            raise HistoryError("MARKET_HISTORY_JITTER_BINDING_DRIFT")
        _validate_record(
            record,
            expected_sequence=head["record_count"],
            expected_previous=head["last_previous_record_sha256"],
            expected_owner=owner,
            path=history_directory / _record_name(head["record_count"]),
        )
        return record


def _atomic_publish(path: Path, contents: bytes, *, mode: int) -> None:
    directory = path.parent
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=directory,
                prefix=f".{path.name}.",
                delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), mode)
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            raise HistoryError(
                "MARKET_HISTORY_ATOMIC_TARGET_EXISTS") from error
        temporary.unlink()
        temporary = None
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_ATOMIC_PUBLISH_FAILED") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _atomic_replace_head(path: Path, contents: bytes) -> None:
    pending = path.parent / HEAD_PENDING_NAME
    descriptor: int | None = None
    try:
        descriptor = os.open(
            pending,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        view = memoryview(contents)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short history-head write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(pending, path)
        directory = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as error:
        raise HistoryError("MARKET_HISTORY_HEAD_RECOVERY_REQUIRED") from error
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_HEAD_COMMIT_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _advance_incremental_audit(
    history_directory: Path,
    head: dict[str, Any],
    *,
    expected_owner: tuple[int, int],
    last_record: dict[str, Any],
) -> tuple[int, str | None, int, int]:
    cursor = head["audit_cursor_sequence"]
    expected_previous = head["audit_expected_previous_sha256"]
    count = head["record_count"]
    disk_reads = 0
    validated = 0
    cached = {last_record["sequence"]: last_record}
    for _index in range(min(INCREMENTAL_AUDIT_RECORDS, count)):
        record = cached.get(cursor)
        if record is None:
            record, _ = _load_record_direct(
                history_directory,
                cursor,
                expected_previous=expected_previous,
                expected_owner=expected_owner,
            )
            disk_reads += 1
        elif record["previous_record_sha256"] != expected_previous:
            raise HistoryError(
                "MARKET_HISTORY_HEAD_AUDIT_CURSOR_INVALID")
        if (
                cursor == 1 and
                record["record_sha256"] != head["first_record_sha256"]):
            raise HistoryError("MARKET_HISTORY_HEAD_AUDIT_CURSOR_INVALID")
        validated += 1
        if cursor == count:
            cursor = 1
            expected_previous = None
        else:
            cursor += 1
            expected_previous = record["record_sha256"]
    return cursor, expected_previous, disk_reads, validated


def _guard_history_capacity(
    history_directory: Path,
    *,
    current_record_bytes: int,
    incoming_record_bytes: int,
    new_head_bytes: int,
    maximum_history_bytes: int,
    minimum_free_bytes: int,
) -> None:
    projected = (
        current_record_bytes +
        incoming_record_bytes +
        new_head_bytes
    )
    if projected > maximum_history_bytes:
        raise HistoryError("MARKET_HISTORY_BYTE_QUOTA_EXCEEDED")
    try:
        file_system = os.statvfs(history_directory)
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_STORAGE_GUARD_FAILED") from error
    available_bytes = file_system.f_bavail * file_system.f_frsize
    required_allocation = incoming_record_bytes + new_head_bytes
    if available_bytes < minimum_free_bytes + required_allocation:
        raise HistoryError("MARKET_HISTORY_FREE_SPACE_GUARD")


def _append_result(
    *,
    status: str,
    record: dict[str, Any],
    record_contents: bytes,
    head: dict[str, Any],
    head_contents: bytes,
    bytes_appended: int,
    head_reads: int,
    record_reads: int,
    checkpoint_records_validated: int,
) -> dict[str, Any]:
    return {
        "status": status,
        "sequence": record["sequence"],
        "record_sha256": record["record_sha256"],
        "history_head_sha256": record["record_sha256"],
        "record_count": head["record_count"],
        "record_size_bytes": len(record_contents),
        "bytes_appended": bytes_appended,
        "history_record_bytes": head["history_record_bytes"],
        "history_index_bytes": len(head_contents),
        "history_storage_bytes":
            head["history_record_bytes"] + len(head_contents),
        "history_index_body_sha256": head["body_sha256"],
        "history_index_file_sha256": digest_bytes(head_contents),
        "snapshot_body_sha256": record["snapshot_body_sha256"],
        "snapshot_file_sha256": record["snapshot_file_sha256"],
        "quote_changed": record["quote_changed"],
        "watch_generation": record["watch_generation"],
        "watch_lease_receipt_body_sha256":
            record["watch_lease_receipt_body_sha256"],
        "watch_lease_receipt_file_sha256":
            record["watch_lease_receipt_file_sha256"],
        "watch_export_receipt_body_sha256":
            record["watch_export_receipt_body_sha256"],
        "watch_export_receipt_file_sha256":
            record["watch_export_receipt_file_sha256"],
        "complexity": {
            "directory_entries_scanned": 0,
            "history_head_reads": head_reads,
            "history_record_reads": record_reads,
            "checkpoint_records_validated":
                checkpoint_records_validated,
        },
    }


def append_snapshot(
    history_directory: Path,
    snapshot_path: Path,
    *,
    cadence_ms: int,
    watch_lease_receipt_path: Path,
    watch_export_receipt_path: Path,
    maximum_jitter_ms: int = DEFAULT_MAXIMUM_JITTER_MS,
    maximum_history_bytes: int = DEFAULT_MAXIMUM_HISTORY_BYTES,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    previous_segment_history_directory: Path | None = None,
) -> dict[str, Any]:
    """Validate and atomically append one receipt-bound WATCH snapshot."""

    _validate_cadence(cadence_ms, maximum_jitter_ms)
    _validate_storage_guard(
        maximum_history_bytes,
        minimum_free_bytes,
    )
    snapshot, contents, snapshot_metadata = _load_root_canonical(
        snapshot_path,
        "MARKET_HISTORY_SNAPSHOT",
    )
    normalized = _validate_snapshot(snapshot, contents)
    receipt, receipt_contents, lease_metadata = _load_root_canonical(
        watch_lease_receipt_path,
        "MARKET_HISTORY_LEASE_RECEIPT",
    )
    normalized_lease = _validate_lease_receipt(
        receipt,
        receipt_contents,
        normalized,
    )
    (
        export_receipt,
        export_receipt_contents,
        export_metadata,
    ) = _load_root_canonical(
        watch_export_receipt_path,
        "MARKET_HISTORY_EXPORT_RECEIPT",
    )
    normalized_export = _validate_export_receipt(
        export_receipt,
        export_receipt_contents,
        normalized,
        normalized_lease,
        snapshot_metadata=snapshot_metadata,
        lease_metadata=lease_metadata,
        export_metadata=export_metadata,
    )
    previous_segment_record = _previous_segment_record(
        previous_segment_history_directory,
        cadence_ms=cadence_ms,
        maximum_jitter_ms=maximum_jitter_ms,
    )
    _directory_metadata(history_directory, create=True)
    with _history_lock(history_directory, exclusive=True):
        (
            head,
            head_contents,
            last_record,
            last_contents,
            owner,
        ) = _load_fast_state(history_directory)
        head_reads = 0 if head is None else 1
        record_reads = 0 if last_record is None else 1
        checkpoint_validated = 0
        next_audit_cursor = 1
        next_audit_previous: str | None = None
        if head is not None and last_record is not None:
            if (
                    head["cadence_ms"] != cadence_ms or
                    head["maximum_jitter_ms"] != maximum_jitter_ms):
                raise HistoryError(
                    "MARKET_HISTORY_CADENCE_BINDING_DRIFT")
            (
                next_audit_cursor,
                next_audit_previous,
                audit_reads,
                checkpoint_validated,
            ) = _advance_incremental_audit(
                history_directory,
                head,
                expected_owner=owner,
                last_record=last_record,
            )
            record_reads += audit_reads
        if (
                last_record is not None and
                last_record["snapshot_body_sha256"] ==
                normalized["snapshot_body_sha256"]):
            if (
                    last_record["snapshot_file_sha256"] !=
                    normalized["snapshot_file_sha256"] or
                    last_record["watch_lease_receipt_body_sha256"] !=
                    normalized_lease["watch_lease_receipt_body_sha256"] or
                    last_record["watch_lease_receipt_file_sha256"] !=
                    normalized_lease["watch_lease_receipt_file_sha256"] or
                    last_record["watch_export_receipt_body_sha256"] !=
                    normalized_export["watch_export_receipt_body_sha256"] or
                    last_record["watch_export_receipt_file_sha256"] !=
                    normalized_export["watch_export_receipt_file_sha256"]):
                raise HistoryError(
                    "MARKET_HISTORY_DUPLICATE_BINDING_CONFLICT")
            assert head is not None
            return _append_result(
                status="duplicate",
                record=last_record,
                record_contents=last_contents,
                head=head,
                head_contents=head_contents,
                bytes_appended=0,
                head_reads=head_reads,
                record_reads=record_reads,
                checkpoint_records_validated=checkpoint_validated,
            )

        sequence = 1 if head is None else head["record_count"] + 1
        previous = (
            None if last_record is None else last_record["record_sha256"])
        quote_predecessor = (
            last_record
            if last_record is not None else
            previous_segment_record
        )
        quote_changed = (
            True
            if quote_predecessor is None else
            _quote_changed(quote_predecessor["quote"], normalized["quote"])
        )
        body = _record_body(
            normalized,
            normalized_lease,
            normalized_export,
            sequence=sequence,
            cadence_ms=cadence_ms,
            maximum_jitter_ms=maximum_jitter_ms,
            previous_record_sha256=previous,
            quote_changed=quote_changed,
        )
        record_sha256 = digest_document(body)
        record = {**body, "record_sha256": record_sha256}
        if last_record is not None:
            _validate_authority_binding(last_record, record)
            _validate_record_transition(last_record, record)
        elif record["watch_lease_operation"] != "PROVISION":
            if previous_segment_record is None:
                raise HistoryError(
                    "MARKET_HISTORY_SEGMENT_MUST_START_WITH_PROVISION")
            _validate_segment_transition(previous_segment_record, record)
        elif record["quote_changed"] is not True:
            raise HistoryError("MARKET_HISTORY_QUOTE_CHANGE_FLAG_INVALID")
        destination = history_directory / _record_name(sequence)
        record_contents = canonical_bytes(record)
        history_record_bytes = (
            len(record_contents) if head is None else
            head["history_record_bytes"] + len(record_contents)
        )
        first_record_sha256 = (
            record_sha256 if head is None else
            head["first_record_sha256"]
        )
        new_head = _make_head(
            record_count=sequence,
            history_record_bytes=history_record_bytes,
            first_record_sha256=first_record_sha256,
            last_record=record,
            last_record_contents=record_contents,
            audit_cursor_sequence=next_audit_cursor,
            audit_expected_previous_sha256=next_audit_previous,
        )
        new_head_contents = canonical_bytes(new_head)
        _guard_history_capacity(
            history_directory,
            current_record_bytes=(
                0 if head is None else head["history_record_bytes"]),
            incoming_record_bytes=len(record_contents),
            new_head_bytes=len(new_head_contents),
            maximum_history_bytes=maximum_history_bytes,
            minimum_free_bytes=minimum_free_bytes,
        )
        _atomic_publish(destination, record_contents, mode=0o600)
        _atomic_replace_head(
            history_directory / HEAD_NAME,
            new_head_contents,
        )
        return _append_result(
            status="appended",
            record=record,
            record_contents=record_contents,
            head=new_head,
            head_contents=new_head_contents,
            bytes_appended=len(record_contents),
            head_reads=head_reads,
            record_reads=record_reads,
            checkpoint_records_validated=checkpoint_validated,
        )


def _source_digest(kind: str, digests: list[str]) -> str:
    return digest_document({
        "schema": "hepta.shadow-market-bar-source.v1",
        "source_kind": kind,
        "digests": digests,
    })


def _bar_digest(bar: dict[str, Any]) -> str:
    return digest_document(bar)


def _quote_bar(
    records: list[dict[str, Any]],
    *,
    started_at_ms: int,
    interval_ms: int,
    cadence_ms: int,
    maximum_jitter_ms: int,
) -> dict[str, Any]:
    finished_exclusive = started_at_ms + interval_ms
    samples = [
        record for record in records
        if started_at_ms <= record["collection_started_at_ms"] <
        finished_exclusive
    ]
    expected = interval_ms // cadence_ms
    minimum_samples = math.ceil(
        expected * MINIMUM_COVERAGE_NUMERATOR /
        MINIMUM_COVERAGE_DENOMINATOR
    )
    reasons: list[str] = []
    maximum_gap = 0
    if samples:
        first_offset = (
            samples[0]["collection_started_at_ms"] - started_at_ms)
        end_offset = (
            finished_exclusive -
            samples[-1]["collection_started_at_ms"])
        gaps = [
            right["collection_started_at_ms"] -
            left["collection_started_at_ms"]
            for left, right in zip(samples, samples[1:])
        ]
        maximum_gap = max(gaps, default=0)
        if first_offset > cadence_ms + maximum_jitter_ms:
            reasons.append("START_BOUNDARY_UNCOVERED")
        if end_offset > cadence_ms + maximum_jitter_ms:
            reasons.append("END_BOUNDARY_UNCOVERED")
        if maximum_gap > cadence_ms + maximum_jitter_ms:
            reasons.append("CAPTURE_GAP_EXCEEDED")
    else:
        reasons.extend([
            "START_BOUNDARY_UNCOVERED",
            "END_BOUNDARY_UNCOVERED",
        ])
    if len(samples) < minimum_samples:
        reasons.append("INSUFFICIENT_SAMPLES")
    complete = not reasons
    mids = [
        round(
            (float(record["quote"]["bid"]) +
             float(record["quote"]["ask"])) / 2.0,
            12,
        )
        for record in samples
    ]
    source_digests = [record["record_sha256"] for record in samples]
    bar_body = {
        "interval_ms": interval_ms,
        "started_at_ms": started_at_ms,
        "finished_at_ms": finished_exclusive - 1,
        "price_basis": "SAMPLED_AUTHORITATIVE_BID_ASK_MIDPOINT",
        "open": None if not mids else mids[0],
        "high": None if not mids else max(mids),
        "low": None if not mids else min(mids),
        "close": None if not mids else mids[-1],
        "sample_count": len(samples),
        "expected_sample_count": expected,
        "coverage_ppm": (
            0 if expected == 0 else
            min(1_000_000, len(samples) * 1_000_000 // expected)
        ),
        "maximum_capture_gap_ms": maximum_gap,
        "first_sequence": None if not samples else samples[0]["sequence"],
        "last_sequence": None if not samples else samples[-1]["sequence"],
        "complete": complete,
        "reason_codes": reasons,
        "source_kind": "SHADOW_HISTORY_RECORDS",
        "source_count": len(source_digests),
        "source_sha256": _source_digest(
            "SHADOW_HISTORY_RECORDS",
            source_digests,
        ),
    }
    return {**bar_body, "bar_sha256": _bar_digest(bar_body)}


def _five_minute_bar(
    one_minute_bars: dict[int, dict[str, Any]],
    *,
    started_at_ms: int,
) -> dict[str, Any]:
    minute_starts = [started_at_ms + index * 60_000 for index in range(5)]
    sources = [
        one_minute_bars.get(minute_start)
        for minute_start in minute_starts
    ]
    reasons: list[str] = []
    if any(source is None for source in sources):
        reasons.append("MISSING_ONE_MINUTE_BAR")
    if any(
            source is not None and source["complete"] is not True
            for source in sources):
        reasons.append("INCOMPLETE_ONE_MINUTE_BAR")
    complete = not reasons
    present = [source for source in sources if source is not None]
    usable = (
        [source for source in present if source["open"] is not None]
        if complete else []
    )
    source_digests = [source["bar_sha256"] for source in present]
    expected = sum(source["expected_sample_count"] for source in present)
    sample_count = sum(source["sample_count"] for source in present)
    bar_body = {
        "interval_ms": 300_000,
        "started_at_ms": started_at_ms,
        "finished_at_ms": started_at_ms + 300_000 - 1,
        "price_basis": "SAMPLED_AUTHORITATIVE_BID_ASK_MIDPOINT",
        "open": None if not usable else usable[0]["open"],
        "high": None if not usable else max(source["high"] for source in usable),
        "low": None if not usable else min(source["low"] for source in usable),
        "close": None if not usable else usable[-1]["close"],
        "sample_count": sample_count,
        "expected_sample_count": expected,
        "coverage_ppm": (
            0 if expected == 0 else
            min(1_000_000, sample_count * 1_000_000 // expected)
        ),
        "maximum_capture_gap_ms": max(
            (source["maximum_capture_gap_ms"] for source in present),
            default=0,
        ),
        "first_sequence": (
            None if not present else present[0]["first_sequence"]),
        "last_sequence": (
            None if not present else present[-1]["last_sequence"]),
        "complete": complete,
        "reason_codes": reasons,
        "source_kind": "ONE_MINUTE_BARS",
        "source_count": len(source_digests),
        "source_sha256": _source_digest(
            "ONE_MINUTE_BARS",
            source_digests,
        ),
    }
    return {**bar_body, "bar_sha256": _bar_digest(bar_body)}


def _validate_materialized_bar(bar: dict[str, Any]) -> None:
    _exact(bar, BAR_FIELDS, "MARKET_HISTORY_BAR_FIELDS_INVALID")
    claimed = bar["bar_sha256"]
    _digest(claimed, "MARKET_HISTORY_BAR_DIGEST_INVALID")
    body = dict(bar)
    body.pop("bar_sha256")
    if claimed != digest_document(body):
        raise HistoryError("MARKET_HISTORY_BAR_DIGEST_INVALID")
    _digest(bar["source_sha256"], "MARKET_HISTORY_BAR_SOURCE_INVALID")
    if bar["complete"] is True and bar["reason_codes"]:
        raise HistoryError("MARKET_HISTORY_BAR_COMPLETENESS_INVALID")
    if bar["complete"] is False and not bar["reason_codes"]:
        raise HistoryError("MARKET_HISTORY_BAR_COMPLETENESS_INVALID")


def _builder_quote_history(
    records: list[dict[str, Any]],
    *,
    cadence_ms: int,
) -> dict[str, Any]:
    capture_gaps = [
        current["quote_read_finished_at_ms"] -
        previous["quote_read_finished_at_ms"]
        for previous, current in zip(records, records[1:])
    ]
    quote_gaps = [
        current["quote"]["observed_at_ms"] -
        previous["quote"]["observed_at_ms"]
        for previous, current in zip(records, records[1:])
        if current["quote_changed"] is True
    ]
    maximum_gap_ms = max([cadence_ms, *capture_gaps, *quote_gaps])
    if maximum_gap_ms > BUILDER_MAXIMUM_QUOTE_GAP_MS:
        raise HistoryError("MARKET_HISTORY_BUILDER_QUOTE_GAP")
    quotes = [{
        "bid": record["quote"]["bid"],
        "ask": record["quote"]["ask"],
        "observed_at_ms": record["quote"]["observed_at_ms"],
        "captured_at_ms": record["quote_read_finished_at_ms"],
        "stale_after_ms": record["quote"]["stale_after_ms"],
        "authoritative": True,
        "stale": False,
        "quote_changed": record["quote_changed"],
        "source_snapshot_body_sha256":
            record["snapshot_body_sha256"],
        "catalog_sha256": record["catalog_sha256"],
        "descriptor_sha256":
            record["descriptor_sha256"]["market.get_quote"],
        "execution_service_epoch":
            record["execution_service_epoch"],
        "execution_service_fencing_generation":
            record["execution_service_fencing_generation"],
        "watch_generation": record["watch_generation"],
    } for record in records]
    source_sha256 = _source_digest(
        "SHADOW_HISTORY_RECORDS",
        [record["record_sha256"] for record in records],
    )
    body = {
        "schema": "hepta.authoritative-quote-history.v3",
        "version": 3,
        "instrument": "EUR.USD",
        "provider": "HEPTA_IB_PAPER_WATCH",
        "source_ref": source_sha256,
        "observed_at_ms": quotes[-1]["observed_at_ms"],
        "window_started_at_ms": quotes[0]["captured_at_ms"],
        "window_finished_at_ms": quotes[-1]["captured_at_ms"],
        "cadence_ms": cadence_ms,
        "maximum_gap_ms": maximum_gap_ms,
        "source_window_truncated": records[0]["sequence"] > 1,
        "complete": True,
        "quotes": quotes,
    }
    return {**body, "body_sha256": digest_document(body)}


def _complete_five_minute_suffix(
    five_minute_bars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not five_minute_bars:
        raise HistoryError("MARKET_HISTORY_NO_CLOSED_FIVE_MINUTE_BARS")
    if five_minute_bars[-1]["complete"] is not True:
        raise HistoryError("MARKET_HISTORY_LATEST_FIVE_MINUTE_BAR_INCOMPLETE")
    first = len(five_minute_bars) - 1
    while (
            first > 0 and
            five_minute_bars[first - 1]["complete"] is True and
            five_minute_bars[first - 1]["finished_at_ms"] + 1 ==
            five_minute_bars[first]["started_at_ms"]):
        first -= 1
    return five_minute_bars[first:]


def _builder_bar_history(
    records: list[dict[str, Any]],
    five_minute_bars: list[dict[str, Any]],
) -> dict[str, Any]:
    complete_bars = _complete_five_minute_suffix(five_minute_bars)
    source_content_sha256 = _source_digest(
        "SHADOW_HISTORY_RECORDS",
        [record["record_sha256"] for record in records],
    )
    bars = [{
        "started_at_ms": bar["started_at_ms"],
        "finished_at_ms": bar["finished_at_ms"],
        "open": bar["open"],
        "high": bar["high"],
        "low": bar["low"],
        "close": bar["close"],
        "sample_count": bar["sample_count"],
        "complete": True,
        "source_content_sha256": source_content_sha256,
    } for bar in complete_bars]
    body = {
        "schema": "hepta.authoritative-bar-history.v2",
        "version": 2,
        "instrument": "EUR.USD",
        "provider": "HEPTA_IB_PAPER_WATCH_SAMPLED_MIDPOINT",
        "source_ref": source_content_sha256,
        "observed_at_ms": records[-1]["quote"]["observed_at_ms"],
        "source_content_sha256": source_content_sha256,
        "interval_ms": 300_000,
        "window_started_at_ms": bars[0]["started_at_ms"],
        "window_finished_at_ms": bars[-1]["finished_at_ms"],
        "expected_bar_count": len(bars),
        "complete": True,
        "bars": bars,
    }
    return {**body, "body_sha256": digest_document(body)}


def materialize_bars(
    history_directory: Path,
    output_path: Path,
    *,
    cadence_ms: int,
    maximum_jitter_ms: int = DEFAULT_MAXIMUM_JITTER_MS,
    quote_history_output: Path | None = None,
    bar_history_output: Path | None = None,
    window_ms: int = DEFAULT_MATERIALIZATION_WINDOW_MS,
) -> dict[str, Any]:
    """Materialize a bounded rolling window and immutable provenance edge."""

    _validate_cadence(cadence_ms, maximum_jitter_ms)
    maximum_records = _materialization_record_limit(cadence_ms, window_ms)
    if (quote_history_output is None) != (bar_history_output is None):
        raise HistoryError("MARKET_HISTORY_BUILDER_OUTPUT_PAIR_REQUIRED")
    records, head, predecessor_digest, head_contents = _load_history_tail(
        history_directory,
        cadence_ms=cadence_ms,
        maximum_jitter_ms=maximum_jitter_ms,
        maximum_records=maximum_records,
    )
    first = records[0]
    last = records[-1]
    first_minute = (
        first["collection_started_at_ms"] // 60_000) * 60_000
    closed_minute_end = (
        last["collection_started_at_ms"] // 60_000) * 60_000
    one_minute: list[dict[str, Any]] = []
    cursor = first_minute
    while cursor + 60_000 <= closed_minute_end:
        bar = _quote_bar(
            records,
            started_at_ms=cursor,
            interval_ms=60_000,
            cadence_ms=cadence_ms,
            maximum_jitter_ms=maximum_jitter_ms,
        )
        _validate_materialized_bar(bar)
        one_minute.append(bar)
        cursor += 60_000

    one_by_start = {bar["started_at_ms"]: bar for bar in one_minute}
    first_five = (
        first["collection_started_at_ms"] // 300_000) * 300_000
    closed_five_end = (
        last["collection_started_at_ms"] // 300_000) * 300_000
    five_minute: list[dict[str, Any]] = []
    cursor = first_five
    while cursor + 300_000 <= closed_five_end:
        bar = _five_minute_bar(one_by_start, started_at_ms=cursor)
        _validate_materialized_bar(bar)
        five_minute.append(bar)
        cursor += 300_000

    body = {
        "schema": "hepta.shadow-sampled-bar-history.v1",
        "version": 1,
        "instrument": first["instrument"],
        "price_basis": "SAMPLED_AUTHORITATIVE_BID_ASK_MIDPOINT",
        "cadence_ms": cadence_ms,
        "maximum_jitter_ms": maximum_jitter_ms,
        "domain_id": first["domain_id"],
        "agent_uid": first["agent_uid"],
        "catalog_sha256": first["catalog_sha256"],
        "descriptor_sha256": first["descriptor_sha256"],
        "execution_service_epoch": first["execution_service_epoch"],
        "execution_service_fencing_generation":
            first["execution_service_fencing_generation"],
        "materialization_window_ms": window_ms,
        "materialization_maximum_records": maximum_records,
        "source_total_record_count": head["record_count"],
        "source_record_count": len(records),
        "source_first_sequence": first["sequence"],
        "source_last_sequence": last["sequence"],
        "source_window_truncated": first["sequence"] > 1,
        "source_predecessor_record_sha256": predecessor_digest,
        "source_history_head_sha256": head["last_record_sha256"],
        "source_history_index_body_sha256": head["body_sha256"],
        "source_history_index_file_sha256": digest_bytes(head_contents),
        "source_sha256": _source_digest(
            "SHADOW_HISTORY_RECORDS",
            [record["record_sha256"] for record in records],
        ),
        "closed_through_ms": closed_minute_end,
        "one_minute_bars": one_minute,
        "five_minute_bars": five_minute,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    document = {**body, "body_sha256": digest_document(body)}
    quote_history: dict[str, Any] | None = None
    bar_history: dict[str, Any] | None = None
    if quote_history_output is not None and bar_history_output is not None:
        quote_history = _builder_quote_history(
            records,
            cadence_ms=cadence_ms,
        )
        bar_history = _builder_bar_history(records, five_minute)
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _atomic_replace(output_path, canonical_bytes(document), mode=0o600)
    if (
            quote_history_output is not None and
            bar_history_output is not None and
            quote_history is not None and
            bar_history is not None):
        for path, artifact in (
                (quote_history_output, quote_history),
                (bar_history_output, bar_history)):
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _atomic_replace(path, canonical_bytes(artifact), mode=0o600)
    return document


def _atomic_replace(path: Path, contents: bytes, *, mode: int) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), mode)
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise HistoryError("MARKET_HISTORY_ATOMIC_REPLACE_FAILED") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append and materialize read-only SHADOW WATCH history")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    append = subparsers.add_parser("append")
    append.add_argument("--history-directory", type=Path, required=True)
    append.add_argument("--snapshot", type=Path, required=True)
    append.add_argument("--cadence-ms", type=int, required=True)
    append.add_argument(
        "--watch-lease-receipt",
        type=Path,
        required=True,
    )
    append.add_argument(
        "--watch-export-receipt",
        type=Path,
        required=True,
    )
    append.add_argument(
        "--maximum-jitter-ms",
        type=int,
        default=DEFAULT_MAXIMUM_JITTER_MS,
    )
    append.add_argument(
        "--maximum-history-bytes",
        type=int,
        default=DEFAULT_MAXIMUM_HISTORY_BYTES,
    )
    append.add_argument(
        "--minimum-free-bytes",
        type=int,
        default=DEFAULT_MINIMUM_FREE_BYTES,
    )
    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("--history-directory", type=Path, required=True)
    materialize.add_argument("--output", type=Path, required=True)
    materialize.add_argument(
        "--quote-history-output",
        type=Path,
        required=True,
    )
    materialize.add_argument(
        "--bar-history-output",
        type=Path,
        required=True,
    )
    materialize.add_argument("--cadence-ms", type=int, required=True)
    materialize.add_argument(
        "--maximum-jitter-ms",
        type=int,
        default=DEFAULT_MAXIMUM_JITTER_MS,
    )
    materialize.add_argument(
        "--window-ms",
        type=int,
        default=DEFAULT_MATERIALIZATION_WINDOW_MS,
    )
    recover = subparsers.add_parser("recover")
    recover.add_argument("--history-directory", type=Path, required=True)
    recover.add_argument("--cadence-ms", type=int, required=True)
    recover.add_argument(
        "--maximum-jitter-ms",
        type=int,
        default=DEFAULT_MAXIMUM_JITTER_MS,
    )
    recover.add_argument(
        "--maximum-history-bytes",
        type=int,
        default=DEFAULT_MAXIMUM_HISTORY_BYTES,
    )
    recover.add_argument(
        "--minimum-free-bytes",
        type=int,
        default=DEFAULT_MINIMUM_FREE_BYTES,
    )
    audit = subparsers.add_parser("audit")
    audit.add_argument("--history-directory", type=Path, required=True)
    audit.add_argument("--cadence-ms", type=int, required=True)
    audit.add_argument(
        "--maximum-jitter-ms",
        type=int,
        default=DEFAULT_MAXIMUM_JITTER_MS,
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.operation == "append":
            result = append_snapshot(
                arguments.history_directory,
                arguments.snapshot,
                cadence_ms=arguments.cadence_ms,
                watch_lease_receipt_path=arguments.watch_lease_receipt,
                watch_export_receipt_path=arguments.watch_export_receipt,
                maximum_jitter_ms=arguments.maximum_jitter_ms,
                maximum_history_bytes=arguments.maximum_history_bytes,
                minimum_free_bytes=arguments.minimum_free_bytes,
            )
        elif arguments.operation == "materialize":
            result = materialize_bars(
                arguments.history_directory,
                arguments.output,
                cadence_ms=arguments.cadence_ms,
                maximum_jitter_ms=arguments.maximum_jitter_ms,
                quote_history_output=arguments.quote_history_output,
                bar_history_output=arguments.bar_history_output,
                window_ms=arguments.window_ms,
            )
        elif arguments.operation == "recover":
            result = recover_history_head(
                arguments.history_directory,
                cadence_ms=arguments.cadence_ms,
                maximum_jitter_ms=arguments.maximum_jitter_ms,
                maximum_history_bytes=arguments.maximum_history_bytes,
                minimum_free_bytes=arguments.minimum_free_bytes,
            )
        else:
            result = audit_history(
                arguments.history_directory,
                cadence_ms=arguments.cadence_ms,
                maximum_jitter_ms=arguments.maximum_jitter_ms,
            )
    except (HistoryError, OSError, ValueError) as error:
        print(
            "hepta_shadow_market_history: FAIL: " + str(error),
            file=sys.stderr,
        )
        return 78
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
