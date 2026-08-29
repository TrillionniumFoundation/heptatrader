#!/usr/bin/env python3

"""Bounded, one-shot orchestration for zero-authority SHADOW observation.

An external trusted seam owns WATCH lease and snapshot production.  This
module only consumes those immutable inputs, appends receipt-bound history,
materializes evidence, and calls the deterministic SHADOW strategy runner.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import time
from typing import Any, Iterator

import hepta_market_evidence_normalizer as evidence_normalizer
import hepta_shadow_market_history as market_history
import hepta_strategy_shadow_runner as shadow_runner
from hepta_strategy_contracts import (
    ContractError,
    atomic_write_json,
    canonical_bytes,
    digest_bytes,
    digest_document,
    load_document,
    require_bool,
    require_digest,
    require_exact_fields,
    require_int,
    require_text,
)


STATE_SCHEMA = "hepta.bounded-shadow-observer-state.v1"
COLLECTION_CADENCE_MS = 10_000
MAXIMUM_COLLECTION_JITTER_MS = 1_000
MAXIMUM_SNAPSHOT_AGE_MS = 15_000
MAXIMUM_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
MINIMUM_FREE_BYTES = 128 * 1024 * 1024
MUTABLE_ARTIFACT_RESERVE_BYTES = 32 * 1024 * 1024
MAXIMUM_AUDIT_EVENTS = 16_384
ZERO_ACCUMULATOR = "sha256:" + "0" * 64

STATE_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "policy_sha256", "policy_body_sha256", "strategy_id",
    "strategy_version", "strategy_sha256", "status",
    "collection_cadence_ms", "maximum_collection_jitter_ms",
    "valid_after_ms", "expires_at_ms", "slot_interval_ms",
    "maximum_iterations", "maximum_lateness_ms", "segment_index",
    "segment_status", "segment_record_count",
    "segment_history_head_sha256", "last_collection_started_at_ms",
    "last_generated_at_ms", "last_snapshot_body_sha256",
    "last_watch_generation", "last_lease_receipt_body_sha256",
    "last_lease_receipt_file_sha256", "completed_iterations",
    "last_receipt_sha256", "missed_sample_count",
    "missed_decision_count", "sample_count",
    "accounted_payload_bytes", "accounted_payload_files",
    "accounted_payload_accumulator", "last_storage_audit_sample_count",
    "last_storage_audit_accumulator", "final_audit_receipt_sha256",
    "final_audit_segment_count", "audit_events", "paper_authorized",
    "live_authorized", "mutation_attempted", "direct_broker_access",
    "body_sha256",
})

class ObserverError(RuntimeError):
    """Stable fail-closed observer error."""


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _state_body(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key != "body_sha256"
    }


def _seal_state(state: dict[str, Any]) -> dict[str, Any]:
    body = _state_body(state)
    return {**body, "body_sha256": digest_document(body)}


def _append_event(
    state: dict[str, Any],
    event: str,
    at_ms: int,
    *,
    reason: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    events = state["audit_events"]
    if not isinstance(events, list) or len(events) >= MAXIMUM_AUDIT_EVENTS:
        raise ObserverError("BOUNDED_SHADOW_AUDIT_LIMIT_EXCEEDED")
    entry: dict[str, Any] = {
        "sequence": len(events) + 1,
        "event": event,
        "at_ms": at_ms,
        "reason": reason,
        "detail": {} if detail is None else detail,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    events.append(entry)


def _initial_state(
    policy: dict[str, Any],
    policy_sha256: str,
) -> dict[str, Any]:
    state = {
        "schema": STATE_SCHEMA,
        "version": 1,
        "campaign_id": policy["campaign_id"],
        "campaign_sha256": policy["campaign_sha256"],
        "policy_sha256": policy_sha256,
        "policy_body_sha256": policy["body_sha256"],
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "status": "RUNNING",
        "collection_cadence_ms": COLLECTION_CADENCE_MS,
        "maximum_collection_jitter_ms": MAXIMUM_COLLECTION_JITTER_MS,
        "valid_after_ms": policy["valid_after_ms"],
        "expires_at_ms": policy["expires_at_ms"],
        "slot_interval_ms": policy["slot_interval_ms"],
        "maximum_iterations": policy["maximum_iterations"],
        "maximum_lateness_ms": policy["maximum_lateness_ms"],
        "segment_index": 1,
        "segment_status": "OPEN",
        "segment_record_count": 0,
        "segment_history_head_sha256": None,
        "last_collection_started_at_ms": None,
        "last_generated_at_ms": None,
        "last_snapshot_body_sha256": None,
        "last_watch_generation": None,
        "last_lease_receipt_body_sha256": None,
        "last_lease_receipt_file_sha256": None,
        "completed_iterations": 0,
        "last_receipt_sha256": None,
        "missed_sample_count": 0,
        "missed_decision_count": 0,
        "sample_count": 0,
        "accounted_payload_bytes": 0,
        "accounted_payload_files": 0,
        "accounted_payload_accumulator": ZERO_ACCUMULATOR,
        "last_storage_audit_sample_count": 0,
        "last_storage_audit_accumulator": ZERO_ACCUMULATOR,
        "final_audit_receipt_sha256": None,
        "final_audit_segment_count": 0,
        "audit_events": [],
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    _append_event(
        state,
        "OBSERVATION_STARTED",
        policy["valid_after_ms"],
        detail={"segment_index": 1},
    )
    return _seal_state(state)


def _validate_state(
    state: dict[str, Any],
    *,
    policy: dict[str, Any],
    policy_sha256: str,
) -> dict[str, Any]:
    require_exact_fields(
        state, STATE_FIELDS, "BOUNDED_SHADOW_STATE_FIELDS_INVALID")
    if state["schema"] != STATE_SCHEMA or state["version"] != 1:
        raise ObserverError("BOUNDED_SHADOW_STATE_SCHEMA_INVALID")
    expected = {
        "campaign_id": policy["campaign_id"],
        "campaign_sha256": policy["campaign_sha256"],
        "policy_sha256": policy_sha256,
        "policy_body_sha256": policy["body_sha256"],
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "collection_cadence_ms": COLLECTION_CADENCE_MS,
        "maximum_collection_jitter_ms": MAXIMUM_COLLECTION_JITTER_MS,
        "valid_after_ms": policy["valid_after_ms"],
        "expires_at_ms": policy["expires_at_ms"],
        "slot_interval_ms": policy["slot_interval_ms"],
        "maximum_iterations": policy["maximum_iterations"],
        "maximum_lateness_ms": policy["maximum_lateness_ms"],
    }
    if any(state[field] != value for field, value in expected.items()):
        raise ObserverError("BOUNDED_SHADOW_STATE_BINDING_INVALID")
    if state["status"] not in {
            "RUNNING", "COMPLETE", "EXPIRED", "STOPPED",
            "FINAL_AUDIT_REQUIRED"}:
        raise ObserverError("BOUNDED_SHADOW_STATE_STATUS_INVALID")
    if state["segment_status"] not in {"OPEN", "CLOSED"}:
        raise ObserverError("BOUNDED_SHADOW_SEGMENT_STATUS_INVALID")
    for field in (
            "paper_authorized", "live_authorized",
            "mutation_attempted", "direct_broker_access"):
        require_bool(
            state[field], False, "BOUNDED_SHADOW_STATE_BOUNDARY_INVALID")
    require_int(
        state["segment_index"],
        "BOUNDED_SHADOW_SEGMENT_INDEX_INVALID",
        minimum=1,
    )
    require_int(
        state["segment_record_count"],
        "BOUNDED_SHADOW_SEGMENT_COUNT_INVALID",
        minimum=0,
    )
    completed = require_int(
        state["completed_iterations"],
        "BOUNDED_SHADOW_ITERATION_INVALID",
        minimum=0,
        maximum=policy["maximum_iterations"],
    )
    for field in (
            "missed_sample_count", "missed_decision_count", "sample_count",
            "accounted_payload_bytes", "accounted_payload_files",
            "last_storage_audit_sample_count",
            "final_audit_segment_count"):
        require_int(
            state[field], "BOUNDED_SHADOW_MISSED_COUNT_INVALID", minimum=0)
    for field in (
            "accounted_payload_accumulator",
            "last_storage_audit_accumulator"):
        require_digest(
            state[field], "BOUNDED_SHADOW_STORAGE_DIGEST_INVALID")
    if (
            state["accounted_payload_bytes"] >
            MAXIMUM_ARTIFACT_BYTES - MUTABLE_ARTIFACT_RESERVE_BYTES or
            state["last_storage_audit_sample_count"] > state["sample_count"]):
        raise ObserverError("BOUNDED_SHADOW_STORAGE_ACCOUNTING_INVALID")
    if (
            state["accounted_payload_files"] == 0 and
            (
                state["accounted_payload_bytes"] != 0 or
                state["accounted_payload_accumulator"] != ZERO_ACCUMULATOR
            )):
        raise ObserverError("BOUNDED_SHADOW_STORAGE_ACCOUNTING_INVALID")
    if state["final_audit_receipt_sha256"] is not None:
        require_digest(
            state["final_audit_receipt_sha256"],
            "BOUNDED_SHADOW_FINAL_AUDIT_DIGEST_INVALID",
        )
    if state["status"] == "COMPLETE":
        if (
                state["final_audit_receipt_sha256"] is None or
                state["final_audit_segment_count"] < 1):
            raise ObserverError("BOUNDED_SHADOW_FINAL_AUDIT_INVALID")
    if completed == 0:
        if state["last_receipt_sha256"] is not None:
            raise ObserverError("BOUNDED_SHADOW_EMPTY_RECEIPT_INVALID")
    else:
        require_digest(
            state["last_receipt_sha256"],
            "BOUNDED_SHADOW_RECEIPT_DIGEST_INVALID",
        )
    audit = state["audit_events"]
    if not isinstance(audit, list) or len(audit) > MAXIMUM_AUDIT_EVENTS:
        raise ObserverError("BOUNDED_SHADOW_AUDIT_INVALID")
    if any(
            not isinstance(event, dict) or
            event.get("sequence") != index
            for index, event in enumerate(audit, start=1)):
        raise ObserverError("BOUNDED_SHADOW_AUDIT_INVALID")
    claimed = require_digest(
        state["body_sha256"], "BOUNDED_SHADOW_STATE_DIGEST_INVALID")
    if claimed != digest_document(_state_body(state)):
        raise ObserverError("BOUNDED_SHADOW_STATE_DIGEST_INVALID")
    return state


def _load_state(
    path: Path,
    *,
    policy: dict[str, Any],
    policy_sha256: str,
) -> dict[str, Any]:
    if not path.exists():
        return _initial_state(policy, policy_sha256)
    return _validate_state(
        load_document(path, "BOUNDED_SHADOW_STATE", maximum_bytes=8 << 20),
        policy=policy,
        policy_sha256=policy_sha256,
    )


@contextmanager
def _state_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ObserverError("BOUNDED_SHADOW_STATE_LOCKED") from error
        yield
    finally:
        os.close(descriptor)


def _prepare_artifact_root(root: Path) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ObserverError("BOUNDED_SHADOW_ARTIFACT_ROOT_INVALID")
    try:
        free = shutil.disk_usage(root).free
    except OSError as error:
        raise ObserverError("BOUNDED_SHADOW_FREE_SPACE_CHECK_FAILED") from error
    if free < MINIMUM_FREE_BYTES + MUTABLE_ARTIFACT_RESERVE_BYTES:
        raise ObserverError("BOUNDED_SHADOW_FREE_SPACE_GUARD")


def _excluded_mutable_payload(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ObserverError(
            "BOUNDED_SHADOW_ARTIFACT_PATH_INVALID") from error
    return (
        relative == Path("observer-state.json") or
        relative == Path(".observer-state.json.lock") or
        relative == Path("strategy-state.json") or
        relative == Path(".strategy-state.json.lock") or
        path.name == "history-head.json"
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError as error:
        raise ObserverError("BOUNDED_SHADOW_ARTIFACT_READ_FAILED") from error
    return "sha256:" + digest.hexdigest()


def _accumulator_add(accumulator: str, entry_digest: str) -> str:
    require_digest(
        accumulator, "BOUNDED_SHADOW_STORAGE_DIGEST_INVALID")
    require_digest(
        entry_digest, "BOUNDED_SHADOW_STORAGE_DIGEST_INVALID")
    modulus = 1 << 256
    value = (
        int(accumulator.removeprefix("sha256:"), 16) +
        int(entry_digest.removeprefix("sha256:"), 16)
    ) % modulus
    return "sha256:" + format(value, "064x")


def _payload_entry(
    root: Path,
    path: Path,
) -> tuple[int, str]:
    try:
        relative = path.relative_to(root)
        metadata = os.lstat(path)
    except (OSError, ValueError) as error:
        raise ObserverError("BOUNDED_SHADOW_ARTIFACT_METADATA_INVALID") from error
    if (
            path.is_symlink() or
            not stat.S_ISREG(metadata.st_mode) or
            metadata.st_nlink != 1 or
            stat.S_IMODE(metadata.st_mode) != 0o600):
        raise ObserverError("BOUNDED_SHADOW_ARTIFACT_METADATA_INVALID")
    file_sha256 = _file_sha256(path)
    entry_digest = digest_document({
        "schema": "hepta.bounded-shadow-payload-entry.v1",
        "path": relative.as_posix(),
        "size_bytes": metadata.st_size,
        "file_sha256": file_sha256,
    })
    return metadata.st_size, entry_digest


def _account_payload_paths(
    state: dict[str, Any],
    root: Path,
    paths: list[Path],
) -> None:
    seen: set[Path] = set()
    entries: list[tuple[int, str]] = []
    for path in paths:
        absolute = Path(os.path.abspath(path))
        if absolute in seen or _excluded_mutable_payload(root, absolute):
            continue
        seen.add(absolute)
        entries.append(_payload_entry(root, absolute))
    for size_bytes, entry_digest in entries:
        state["accounted_payload_bytes"] += size_bytes
        state["accounted_payload_files"] += 1
        state["accounted_payload_accumulator"] = _accumulator_add(
            state["accounted_payload_accumulator"], entry_digest)


def _full_payload_usage(root: Path) -> dict[str, Any]:
    total_bytes = 0
    file_count = 0
    accumulator = ZERO_ACCUMULATOR
    for directory, names, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in names:
            path = directory_path / name
            if path.is_symlink():
                raise ObserverError("BOUNDED_SHADOW_ARTIFACT_SYMLINK")
        for name in files:
            path = directory_path / name
            if _excluded_mutable_payload(root, path):
                continue
            size_bytes, entry_digest = _payload_entry(root, path)
            total_bytes += size_bytes
            file_count += 1
            accumulator = _accumulator_add(accumulator, entry_digest)
    return {
        "bytes": total_bytes,
        "files": file_count,
        "accumulator": accumulator,
    }


def _tracked_storage_guard(root: Path, state: dict[str, Any]) -> None:
    if (
            state["accounted_payload_bytes"] +
            MUTABLE_ARTIFACT_RESERVE_BYTES >
            MAXIMUM_ARTIFACT_BYTES):
        raise ObserverError("BOUNDED_SHADOW_ARTIFACT_QUOTA_EXCEEDED")
    try:
        free = shutil.disk_usage(root).free
    except OSError as error:
        raise ObserverError("BOUNDED_SHADOW_FREE_SPACE_CHECK_FAILED") from error
    if free < MINIMUM_FREE_BYTES + MUTABLE_ARTIFACT_RESERVE_BYTES:
        raise ObserverError("BOUNDED_SHADOW_FREE_SPACE_GUARD")


def _storage_audit_due(sample_count: int) -> bool:
    return sample_count > 0 and (sample_count & (sample_count - 1)) == 0


def _reconcile_storage(
    root: Path,
    state: dict[str, Any],
    *,
    force: bool = False,
) -> None:
    sample_count = state["sample_count"]
    if not force and not _storage_audit_due(sample_count):
        return
    actual = _full_payload_usage(root)
    expected = {
        "bytes": state["accounted_payload_bytes"],
        "files": state["accounted_payload_files"],
        "accumulator": state["accounted_payload_accumulator"],
    }
    if actual != expected:
        raise ObserverError("BOUNDED_SHADOW_STORAGE_RECONCILIATION_FAILED")
    state["last_storage_audit_sample_count"] = sample_count
    state["last_storage_audit_accumulator"] = actual["accumulator"]


def _snapshot_times(
    snapshot_path: Path,
    *,
    observed_now_ms: int,
) -> tuple[dict[str, Any], int, int, str]:
    try:
        snapshot, snapshot_contents, _metadata = (
            market_history._load_root_canonical(
                snapshot_path,
                "BOUNDED_SHADOW_SNAPSHOT",
            )
        )
    except market_history.HistoryError as error:
        raise ObserverError(str(error)) from error
    if (
            snapshot.get("schema") != "hepta.shadow-watch-snapshot.v2" or
            snapshot.get("version") != 2):
        raise ObserverError("BOUNDED_SHADOW_SNAPSHOT_SCHEMA_INVALID")
    for field in (
            "paper_authorized", "live_authorized",
            "mutation_attempted", "direct_broker_access"):
        require_bool(
            snapshot.get(field), False,
            "BOUNDED_SHADOW_SNAPSHOT_BOUNDARY_INVALID",
        )
    started = require_int(
        snapshot.get("collection_started_at_ms"),
        "BOUNDED_SHADOW_SNAPSHOT_TIME_INVALID",
        minimum=0,
    )
    generated = require_int(
        snapshot.get("generated_at_ms"),
        "BOUNDED_SHADOW_SNAPSHOT_TIME_INVALID",
        minimum=started,
        maximum=observed_now_ms,
    )
    if observed_now_ms - generated > MAXIMUM_SNAPSHOT_AGE_MS:
        raise ObserverError("BOUNDED_SHADOW_SNAPSHOT_STALE")
    require_digest(
        snapshot.get("body_sha256"),
        "BOUNDED_SHADOW_SNAPSHOT_DIGEST_INVALID",
    )
    return snapshot, started, generated, digest_bytes(snapshot_contents)


def _segment_directory(root: Path, index: int) -> Path:
    return root / "segments" / f"segment-{index:06d}"


def _history_record_path(
    history_directory: Path,
    append_result: dict[str, Any],
) -> Path:
    sequence = require_int(
        append_result.get("sequence"),
        "BOUNDED_SHADOW_HISTORY_SEQUENCE_INVALID",
        minimum=1,
    )
    record_sha256 = require_digest(
        append_result.get("record_sha256"),
        "BOUNDED_SHADOW_HISTORY_DIGEST_INVALID",
    )
    candidates = (
        history_directory / f"record-{sequence:020d}.json",
        history_directory / (
            f"record-{sequence:020d}-"
            f"{record_sha256.removeprefix('sha256:')}.json"),
    )
    existing = [path for path in candidates if path.exists()]
    if len(existing) != 1:
        raise ObserverError("BOUNDED_SHADOW_HISTORY_RECORD_PATH_INVALID")
    return existing[0]


def _close_segment(
    state: dict[str, Any],
    *,
    at_ms: int,
    reason: str,
    stop: bool,
) -> None:
    state["segment_status"] = "CLOSED"
    if stop:
        state["status"] = "STOPPED"
    _append_event(
        state,
        "SEGMENT_CLOSED",
        at_ms,
        reason=reason,
        detail={
            "segment_index": state["segment_index"],
            "record_count": state["segment_record_count"],
            "observation_stopped": stop,
        },
    )


def _reject_sample(
    state: dict[str, Any],
    *,
    at_ms: int,
    reason: str,
    skipped_capture_slots: int = 0,
    detail: dict[str, Any] | None = None,
) -> None:
    """Account one rejected capture and enter one terminal continuity break."""

    if (
            isinstance(skipped_capture_slots, bool) or
            not isinstance(skipped_capture_slots, int) or
            skipped_capture_slots < 0):
        raise ObserverError("BOUNDED_SHADOW_REJECTED_SAMPLE_COUNT_INVALID")
    increment = skipped_capture_slots + 1
    state["missed_sample_count"] += increment
    rejection_detail = {
        "rejection_type": reason,
        "rejected_sample_count": 1,
        "skipped_capture_slots": skipped_capture_slots,
        "missed_sample_count_increment": increment,
        "missed_sample_count": state["missed_sample_count"],
    }
    if detail is not None:
        rejection_detail.update(detail)
    _append_event(
        state,
        "SAMPLE_REJECTED",
        at_ms,
        reason=reason,
        detail=rejection_detail,
    )
    _close_segment(
        state,
        at_ms=at_ms,
        reason=reason,
        stop=True,
    )


def _write_state(path: Path, state: dict[str, Any]) -> dict[str, Any]:
    sealed = _seal_state(state)
    atomic_write_json(path, sealed, mode=0o600)
    return sealed


def _warmup_ready(
    *,
    strategy: dict[str, Any],
    quote_history_path: Path,
    bar_history_path: Path,
) -> tuple[bool, dict[str, int]]:
    quote_history = load_document(
        quote_history_path, "BOUNDED_SHADOW_QUOTES", maximum_bytes=64 << 20)
    bar_history = load_document(
        bar_history_path, "BOUNDED_SHADOW_BARS", maximum_bytes=64 << 20)
    quotes = quote_history.get("quotes")
    bars = bar_history.get("bars")
    if (
            quote_history.get("schema") !=
            "hepta.authoritative-quote-history.v3" or
            quote_history.get("version") != 3 or
            not isinstance(quotes, list) or
            not isinstance(bars, list)):
        raise ObserverError("BOUNDED_SHADOW_WARMUP_DOCUMENT_INVALID")
    independent_quotes: list[dict[str, Any]] = []
    for quote in quotes:
        if (
                not isinstance(quote, dict) or
                not isinstance(quote.get("quote_changed"), bool)):
            raise ObserverError("BOUNDED_SHADOW_WARMUP_DOCUMENT_INVALID")
        if quote["quote_changed"] is True:
            independent_quotes.append(quote)
    if not independent_quotes:
        span_ms = 0
    else:
        first = require_int(
            independent_quotes[0].get("observed_at_ms"),
            "BOUNDED_SHADOW_WARMUP_DOCUMENT_INVALID",
            minimum=0,
        )
        last = require_int(
            independent_quotes[-1].get("observed_at_ms"),
            "BOUNDED_SHADOW_WARMUP_DOCUMENT_INVALID",
            minimum=first,
        )
        span_ms = last - first
    requirements = strategy["evidence_requirements"]
    required_quote_count = requirements["minimum_raw_quote_observations"]
    required_span_ms = requirements["minimum_history_span_seconds"] * 1000
    required_bar_count = requirements["minimum_bar_observations"]
    observed = {
        "quote_count": len(independent_quotes),
        "quote_capture_count": len(quotes),
        "quote_span_ms": span_ms,
        "bar_count": len(bars),
        "required_quote_count": required_quote_count,
        "required_quote_span_ms": required_span_ms,
        "required_bar_count": required_bar_count,
    }
    return (
        len(independent_quotes) >= required_quote_count and
        span_ms >= required_span_ms and
        len(bars) >= required_bar_count,
        observed,
    )


def _normalize_evidence(
    source_bundle_path: Path,
    *,
    evaluated_at_ms: int,
    calendar_path: Path,
    information_path: Path,
) -> str:
    bundle = load_document(
        source_bundle_path, "BOUNDED_SHADOW_EVIDENCE", maximum_bytes=4 << 20)
    observed = require_int(
        bundle.get("observed_at_ms"),
        "BOUNDED_SHADOW_EVIDENCE_TIME_INVALID",
        minimum=0,
        maximum=evaluated_at_ms,
    )
    if evaluated_at_ms - observed > evidence_normalizer.MAX_SOURCE_AGE_MS:
        raise ObserverError("BOUNDED_SHADOW_EVIDENCE_STALE")
    calendar, information = evidence_normalizer.normalize(bundle)
    atomic_write_json(calendar_path, calendar, mode=0o600)
    atomic_write_json(information_path, information, mode=0o600)
    return digest_document(bundle)


def _source_window_manifest(
    *,
    policy: dict[str, Any],
    policy_sha256: str,
    state: dict[str, Any],
    iteration: int,
    scheduled_at_ms: int,
    evaluated_at_ms: int,
    snapshot: dict[str, Any],
    sampled: dict[str, Any],
    quote_history_path: Path,
    bar_history_path: Path,
    calendar_path: Path,
    information_path: Path,
    source_bundle_sha256: str,
    watch_export_receipt_body_sha256: str,
    watch_export_receipt_file_sha256: str,
    packet: dict[str, Any],
    packet_path: Path,
    receipt: dict[str, Any],
    receipt_path: Path,
) -> dict[str, Any]:
    body = {
        "schema": "hepta.bounded-shadow-source-window-manifest.v1",
        "version": 1,
        "campaign_id": policy["campaign_id"],
        "campaign_sha256": policy["campaign_sha256"],
        "policy_sha256": policy_sha256,
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "iteration": iteration,
        "scheduled_at_ms": scheduled_at_ms,
        "evaluated_at_ms": evaluated_at_ms,
        "segment_index": state["segment_index"],
        "source_first_sequence": sampled["source_first_sequence"],
        "source_last_sequence": sampled["source_last_sequence"],
        "source_total_record_count":
            sampled["source_total_record_count"],
        "source_window_truncated":
            sampled["source_window_truncated"],
        "source_predecessor_record_sha256":
            sampled["source_predecessor_record_sha256"],
        "source_history_head_sha256":
            sampled["source_history_head_sha256"],
        "source_history_index_body_sha256":
            sampled["source_history_index_body_sha256"],
        "source_history_index_file_sha256":
            sampled["source_history_index_file_sha256"],
        "source_records_sha256": sampled["source_sha256"],
        "materialization_window_ms":
            sampled["materialization_window_ms"],
        "materialization_maximum_records":
            sampled["materialization_maximum_records"],
        "snapshot_body_sha256": snapshot["body_sha256"],
        "snapshot_file_sha256":
            packet["source_snapshot"]["file_sha256"],
        "watch_export_receipt_body_sha256":
            watch_export_receipt_body_sha256,
        "watch_export_receipt_file_sha256":
            watch_export_receipt_file_sha256,
        "quote_history_body_sha256": load_document(
            quote_history_path,
            "BOUNDED_SHADOW_QUOTE_MANIFEST",
            maximum_bytes=64 << 20,
        )["body_sha256"],
        "quote_history_file_sha256": _file_sha256(quote_history_path),
        "bar_history_body_sha256": load_document(
            bar_history_path,
            "BOUNDED_SHADOW_BAR_MANIFEST",
            maximum_bytes=64 << 20,
        )["body_sha256"],
        "bar_history_file_sha256": _file_sha256(bar_history_path),
        "calendar_file_sha256": _file_sha256(calendar_path),
        "information_file_sha256": _file_sha256(information_path),
        "source_bundle_sha256": source_bundle_sha256,
        "information_packet_body_sha256": packet["body_sha256"],
        "information_packet_file_sha256": _file_sha256(packet_path),
        "decision_receipt_file_sha256": _file_sha256(receipt_path),
        "decision_receipt_sha256":
            digest_bytes(canonical_bytes(receipt)),
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return {**body, "body_sha256": digest_document(body)}


def _remove_ephemeral_files(
    artifact_root: Path,
    iteration_directory: Path,
    paths: list[Path],
) -> None:
    directories: set[Path] = set()
    for path in paths:
        absolute = Path(os.path.abspath(path))
        try:
            absolute.relative_to(iteration_directory)
            absolute.relative_to(artifact_root)
        except ValueError as error:
            raise ObserverError(
                "BOUNDED_SHADOW_EPHEMERAL_PATH_INVALID") from error
        if not absolute.exists():
            continue
        metadata = os.lstat(absolute)
        if (
                absolute.is_symlink() or
                not stat.S_ISREG(metadata.st_mode) or
                metadata.st_nlink != 1 or
                stat.S_IMODE(metadata.st_mode) != 0o600):
            raise ObserverError(
                "BOUNDED_SHADOW_EPHEMERAL_METADATA_INVALID")
        absolute.unlink()
        directories.add(absolute.parent)
    for directory_path in directories:
        descriptor = os.open(
            directory_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _finalize_observation(
    *,
    artifact_root: Path,
    state: dict[str, Any],
    policy: dict[str, Any],
    at_ms: int,
) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    for segment_index in range(1, state["segment_index"] + 1):
        history_directory = (
            _segment_directory(artifact_root, segment_index) / "history")
        if not history_directory.is_dir():
            raise ObserverError(
                "BOUNDED_SHADOW_FINAL_AUDIT_SEGMENT_MISSING")
        audit = market_history.audit_history(
            history_directory,
            cadence_ms=COLLECTION_CADENCE_MS,
            maximum_jitter_ms=MAXIMUM_COLLECTION_JITTER_MS,
            previous_segment_history_directory=(
                None if segment_index == 1 else
                _segment_directory(
                    artifact_root, segment_index - 1) / "history"
            ),
        )
        audit_sha256 = digest_document(audit)
        segments.append({
            "segment_index": segment_index,
            "record_count": audit["record_count"],
            "history_head_sha256": audit["history_head_sha256"],
            "source_sha256": audit["source_sha256"],
            "history_record_bytes": audit["history_record_bytes"],
            "history_index_bytes": audit["history_index_bytes"],
            "history_storage_bytes": audit["history_storage_bytes"],
            "audit_sha256": audit_sha256,
        })
    if not segments:
        raise ObserverError("BOUNDED_SHADOW_FINAL_AUDIT_EMPTY")
    sample_count = require_int(
        state["sample_count"],
        "BOUNDED_SHADOW_FINAL_AUDIT_SAMPLE_COUNT_INVALID",
        minimum=1,
    )
    missed_sample_count = require_int(
        state["missed_sample_count"],
        "BOUNDED_SHADOW_FINAL_AUDIT_MISSED_SAMPLE_COUNT_INVALID",
        minimum=0,
    )
    missed_decision_count = require_int(
        state["missed_decision_count"],
        "BOUNDED_SHADOW_FINAL_AUDIT_MISSED_DECISION_COUNT_INVALID",
        minimum=0,
    )
    if missed_sample_count != 0 or missed_decision_count != 0:
        raise ObserverError(
            "BOUNDED_SHADOW_FINAL_AUDIT_MISSED_COUNT_NONZERO")
    if sample_count != sum(segment["record_count"] for segment in segments):
        raise ObserverError(
            "BOUNDED_SHADOW_FINAL_AUDIT_SAMPLE_COUNT_DRIFT")
    body = {
        "schema": "hepta.bounded-shadow-final-audit-receipt.v2",
        "version": 2,
        "campaign_id": policy["campaign_id"],
        "campaign_sha256": policy["campaign_sha256"],
        "policy_sha256": state["policy_sha256"],
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "completed_iterations": state["completed_iterations"],
        "maximum_iterations": policy["maximum_iterations"],
        "finalized_at_ms": at_ms,
        "segment_count": len(segments),
        "segments": segments,
        "sample_count": sample_count,
        "missed_sample_count": missed_sample_count,
        "missed_decision_count": missed_decision_count,
        "payload_bytes_before_final_receipt":
            state["accounted_payload_bytes"],
        "payload_files_before_final_receipt":
            state["accounted_payload_files"],
        "payload_accumulator_before_final_receipt":
            state["accounted_payload_accumulator"],
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    receipt = {**body, "body_sha256": digest_document(body)}
    receipt_path = artifact_root / "final-audit-receipt.json"
    prior_digest = state["final_audit_receipt_sha256"]
    if prior_digest is None:
        atomic_write_json(receipt_path, receipt, mode=0o600)
        _account_payload_paths(
            state, artifact_root, [receipt_path])
    else:
        existing = load_document(
            receipt_path,
            "BOUNDED_SHADOW_FINAL_AUDIT",
            maximum_bytes=4 << 20,
        )
        existing_body = dict(existing)
        existing_body_sha256 = existing_body.pop("body_sha256", None)
        if (
                existing.get("schema") !=
                "hepta.bounded-shadow-final-audit-receipt.v2" or
                existing.get("campaign_id") != policy["campaign_id"] or
                existing.get("completed_iterations") !=
                state["completed_iterations"] or
                existing.get("sample_count") != sample_count or
                existing.get("missed_sample_count") !=
                missed_sample_count or
                existing.get("missed_decision_count") !=
                missed_decision_count or
                existing.get("segments") != segments or
                existing_body_sha256 != digest_document(existing_body) or
                prior_digest != digest_bytes(canonical_bytes(existing))):
            raise ObserverError(
                "BOUNDED_SHADOW_FINAL_AUDIT_REPLAY_MISMATCH")
        receipt = existing
    receipt_sha256 = digest_bytes(canonical_bytes(receipt))
    state["final_audit_receipt_sha256"] = receipt_sha256
    state["final_audit_segment_count"] = len(segments)
    _tracked_storage_guard(artifact_root, state)
    _reconcile_storage(artifact_root, state, force=True)
    state["status"] = "COMPLETE"
    _append_event(
        state,
        "FINAL_HISTORY_AUDIT_COMMITTED",
        at_ms,
        detail={
            "receipt_sha256": receipt_sha256,
            "segment_count": len(segments),
            "sample_count": sample_count,
            "missed_sample_count": missed_sample_count,
            "missed_decision_count": missed_decision_count,
            "segments": segments,
        },
    )
    return receipt


def _result(
    state: dict[str, Any],
    *,
    outcome: str,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "status": state["status"],
        "segment_index": state["segment_index"],
        "segment_status": state["segment_status"],
        "segment_record_count": state["segment_record_count"],
        "completed_iterations": state["completed_iterations"],
        "receipt_path": None if receipt_path is None else str(receipt_path),
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }


def observe_once(
    *,
    campaign_id: str,
    policy_path: Path,
    strategy_path: Path,
    snapshot_path: Path,
    watch_lease_receipt_path: Path,
    watch_export_receipt_path: Path,
    source_bundle_path: Path,
    artifact_root: Path,
    observed_now_ms: int | None = None,
) -> dict[str, Any]:
    """Consume one exported sample and advance bounded SHADOW state once."""

    require_text(
        campaign_id, "BOUNDED_SHADOW_CAMPAIGN_ID_INVALID", identifier=True)
    observed_now_ms = (
        _now_ms() if observed_now_ms is None else
        require_int(
            observed_now_ms,
            "BOUNDED_SHADOW_NOW_INVALID",
            minimum=0,
        )
    )
    policy, policy_sha256 = shadow_runner.load_observation_policy(
        policy_path,
        campaign_id=campaign_id,
    )
    strategy, _strategy_sha256 = (
        shadow_runner.validate_policy_strategy_binding(
            policy, strategy_path))
    artifact_root = Path(os.path.abspath(artifact_root))
    state_path = artifact_root / "observer-state.json"
    _prepare_artifact_root(artifact_root)
    with _state_lock(state_path):
        state_existed = state_path.exists()
        state = _load_state(
            state_path,
            policy=policy,
            policy_sha256=policy_sha256,
        )
        if not state_existed:
            actual = _full_payload_usage(artifact_root)
            if actual != {
                    "bytes": 0,
                    "files": 0,
                    "accumulator": ZERO_ACCUMULATOR}:
                raise ObserverError(
                    "BOUNDED_SHADOW_UNTRACKED_ARTIFACTS")
            state = _write_state(state_path, state)
        _tracked_storage_guard(artifact_root, state)
        if state["status"] == "FINAL_AUDIT_REQUIRED":
            try:
                _finalize_observation(
                    artifact_root=artifact_root,
                    state=state,
                    policy=policy,
                    at_ms=observed_now_ms,
                )
            except (
                    ObserverError,
                    market_history.HistoryError,
                    OSError,
                    ValueError,
            ) as error:
                _append_event(
                    state,
                    "FINAL_HISTORY_AUDIT_RETRY_FAILED",
                    observed_now_ms,
                    reason=str(error),
                )
                state = _write_state(state_path, state)
                return _result(
                    state, outcome="FINAL_AUDIT_REQUIRED")
            state = _write_state(state_path, state)
            return _result(state, outcome="COMPLETE")
        if state["status"] != "RUNNING":
            return _result(state, outcome=state["status"])
        try:
            (
                snapshot,
                started_at_ms,
                generated_at_ms,
                snapshot_file_sha256,
            ) = _snapshot_times(
                snapshot_path,
                observed_now_ms=observed_now_ms,
            )
        except (ObserverError, ContractError, OSError, ValueError) as error:
            _reject_sample(
                state,
                at_ms=observed_now_ms,
                reason=str(error),
                detail={"phase": "snapshot_read"},
            )
            state = _write_state(state_path, state)
            return _result(state, outcome="STOPPED")
        if generated_at_ms >= policy["expires_at_ms"]:
            state["status"] = "EXPIRED"
            _append_event(
                state,
                "POLICY_EXPIRED",
                generated_at_ms,
                reason="BOUNDED_SHADOW_POLICY_EXPIRED",
            )
            state = _write_state(state_path, state)
            return _result(state, outcome="EXPIRED")
        snapshot_body_sha256 = snapshot["body_sha256"]
        state_duplicate = (
                state["last_collection_started_at_ms"] == started_at_ms and
                state["last_snapshot_body_sha256"] == snapshot_body_sha256)
        previous_started = state["last_collection_started_at_ms"]
        previous_generated = state["last_generated_at_ms"]
        if previous_started is not None:
            if started_at_ms < previous_started or (
                    started_at_ms == previous_started and
                    not state_duplicate) or (
                    previous_generated is not None and
                    generated_at_ms <= previous_generated and
                    not state_duplicate):
                _reject_sample(
                    state,
                    at_ms=generated_at_ms,
                    reason="BOUNDED_SHADOW_COLLECTION_NOT_MONOTONIC",
                    detail={
                        "previous_collection_started_at_ms":
                            previous_started,
                        "candidate_collection_started_at_ms": started_at_ms,
                        "previous_generated_at_ms": previous_generated,
                        "candidate_generated_at_ms": generated_at_ms,
                    },
                )
                state = _write_state(state_path, state)
                return _result(state, outcome="STOPPED")

        segment = _segment_directory(
            artifact_root, state["segment_index"])
        history_directory = segment / "history"
        prior_record_count = state["segment_record_count"]
        try:
            append_result = market_history.append_snapshot(
                history_directory,
                snapshot_path,
                cadence_ms=COLLECTION_CADENCE_MS,
                watch_lease_receipt_path=watch_lease_receipt_path,
                watch_export_receipt_path=watch_export_receipt_path,
                maximum_jitter_ms=MAXIMUM_COLLECTION_JITTER_MS,
                maximum_history_bytes=MAXIMUM_ARTIFACT_BYTES,
                minimum_free_bytes=MINIMUM_FREE_BYTES,
                previous_segment_history_directory=(
                    None if state["segment_index"] == 1 else
                    _segment_directory(
                        artifact_root, state["segment_index"] - 1) /
                    "history"
                ),
            )
        except (market_history.HistoryError, OSError, ValueError) as error:
            reason = str(error)
            delta_ms = (
                0 if previous_started is None else
                started_at_ms - previous_started
            )
            skipped_capture_slots = (
                max(0, delta_ms // COLLECTION_CADENCE_MS - 1)
                if delta_ms > 0 else
                0
            )
            _reject_sample(
                state,
                at_ms=generated_at_ms,
                reason=reason,
                skipped_capture_slots=skipped_capture_slots,
                detail={
                    "phase": "history_append",
                    "candidate_collection_started_at_ms": started_at_ms,
                    "previous_collection_started_at_ms": previous_started,
                    "capture_delta_ms": delta_ms,
                },
            )
            state = _write_state(state_path, state)
            return _result(state, outcome="STOPPED")
        if (
                append_result["snapshot_body_sha256"] !=
                snapshot["body_sha256"] or
                append_result["snapshot_file_sha256"] !=
                snapshot_file_sha256):
            _reject_sample(
                state,
                at_ms=generated_at_ms,
                reason="BOUNDED_SHADOW_SNAPSHOT_CHANGED_DURING_READ",
                detail={"phase": "post_append_binding"},
            )
            state = _write_state(state_path, state)
            return _result(state, outcome="STOPPED")
        if append_result["status"] == "duplicate" and state_duplicate:
            return _result(state, outcome="DUPLICATE")
        record_count = require_int(
            append_result.get("record_count"),
            "BOUNDED_SHADOW_HISTORY_COUNT_INVALID",
            minimum=1,
        )
        if record_count != prior_record_count + 1:
            _reject_sample(
                state,
                at_ms=generated_at_ms,
                reason="BOUNDED_SHADOW_HISTORY_COUNT_DRIFT",
                detail={
                    "phase": "post_append_accounting",
                    "prior_record_count": prior_record_count,
                    "record_count": record_count,
                },
            )
            state = _write_state(state_path, state)
            return _result(state, outcome="STOPPED")
        try:
            _account_payload_paths(
                state,
                artifact_root,
                [_history_record_path(history_directory, append_result)],
            )
            state["sample_count"] += 1
            _tracked_storage_guard(artifact_root, state)
            _reconcile_storage(artifact_root, state)
        except ObserverError as error:
            state["status"] = "STOPPED"
            _append_event(
                state,
                "STORAGE_GUARD_FAILED",
                generated_at_ms,
                reason=str(error),
                detail={"phase": "history_append"},
            )
            state = _write_state(state_path, state)
            return _result(state, outcome="STOPPED")

        generation = require_int(
            append_result.get("watch_generation"),
            "BOUNDED_SHADOW_WATCH_GENERATION_INVALID",
            minimum=1,
        )
        previous_generation = state["last_watch_generation"]
        if (
                previous_generation is not None and
                generation != previous_generation):
            if generation != previous_generation + 1:
                _reject_sample(
                    state,
                    at_ms=generated_at_ms,
                    reason="BOUNDED_SHADOW_ROTATION_UNCERTAIN",
                    detail={
                        "previous_generation": previous_generation,
                        "candidate_generation": generation,
                    },
                )
                state = _write_state(state_path, state)
                return _result(state, outcome="STOPPED")
            _append_event(
                state,
                "WATCH_LEASE_ROTATED",
                generated_at_ms,
                detail={
                    "previous_generation": previous_generation,
                    "generation": generation,
                    "lease_receipt_body_sha256":
                        append_result[
                            "watch_lease_receipt_body_sha256"],
                },
            )
        state["segment_record_count"] = record_count
        state["segment_history_head_sha256"] = (
            append_result["history_head_sha256"])
        state["last_collection_started_at_ms"] = started_at_ms
        state["last_generated_at_ms"] = generated_at_ms
        state["last_snapshot_body_sha256"] = snapshot_body_sha256
        state["last_watch_generation"] = generation
        state["last_lease_receipt_body_sha256"] = (
            append_result["watch_lease_receipt_body_sha256"])
        state["last_lease_receipt_file_sha256"] = (
            append_result["watch_lease_receipt_file_sha256"])

        if generated_at_ms < policy["valid_after_ms"]:
            state = _write_state(state_path, state)
            return _result(state, outcome="WARMUP")

        iteration = state["completed_iterations"] + 1
        if iteration > policy["maximum_iterations"]:
            state["status"] = "FINAL_AUDIT_REQUIRED"
            _append_event(
                state,
                "MAXIMUM_ITERATIONS_REACHED",
                generated_at_ms,
            )
            try:
                _finalize_observation(
                    artifact_root=artifact_root,
                    state=state,
                    policy=policy,
                    at_ms=generated_at_ms,
                )
            except (
                    ObserverError,
                    market_history.HistoryError,
                    OSError,
                    ValueError,
            ) as error:
                state["status"] = "FINAL_AUDIT_REQUIRED"
                _append_event(
                    state,
                    "FINAL_HISTORY_AUDIT_REQUIRED",
                    generated_at_ms,
                    reason=str(error),
                )
                state = _write_state(state_path, state)
                return _result(
                    state, outcome="FINAL_AUDIT_REQUIRED")
            state = _write_state(state_path, state)
            return _result(state, outcome="COMPLETE")
        scheduled_at_ms = (
            policy["valid_after_ms"] +
            (iteration - 1) * policy["slot_interval_ms"])
        if generated_at_ms < scheduled_at_ms:
            state = _write_state(state_path, state)
            return _result(state, outcome="COLLECTED")
        if generated_at_ms > scheduled_at_ms + policy["maximum_lateness_ms"]:
            state["missed_decision_count"] += 1
            state["status"] = "STOPPED"
            _append_event(
                state,
                "MISSED_DECISION_SLOT",
                generated_at_ms,
                reason="BOUNDED_SHADOW_DECISION_SLOT_LATE",
                detail={
                    "iteration": iteration,
                    "scheduled_at_ms": scheduled_at_ms,
                    "evaluated_at_ms": generated_at_ms,
                },
            )
            state = _write_state(state_path, state)
            return _result(state, outcome="STOPPED")

        iteration_directory = (
            segment / "iterations" / f"iteration-{iteration:06d}")
        sampled_path = iteration_directory / "sampled-bars.json"
        quote_history_path = iteration_directory / "quote-history.json"
        bar_history_path = iteration_directory / "bar-history.json"
        calendar_path = iteration_directory / "calendar.json"
        information_path = iteration_directory / "information.json"
        packet_path = iteration_directory / "information-packet.json"
        manifest_path = iteration_directory / "source-window-manifest.json"
        receipt_path = (
            artifact_root / "receipts" /
            f"decision-{iteration:06d}.json")
        strategy_state_path = artifact_root / "strategy-state.json"
        iteration_payload_paths = [
            calendar_path,
            information_path,
            packet_path,
            manifest_path,
            receipt_path,
        ]
        ephemeral_paths = [
            sampled_path,
            quote_history_path,
            bar_history_path,
        ]
        try:
            sampled = market_history.materialize_bars(
                history_directory,
                sampled_path,
                cadence_ms=COLLECTION_CADENCE_MS,
                maximum_jitter_ms=MAXIMUM_COLLECTION_JITTER_MS,
                quote_history_output=quote_history_path,
                bar_history_output=bar_history_path,
            )
            ready, warmup = _warmup_ready(
                strategy=strategy,
                quote_history_path=quote_history_path,
                bar_history_path=bar_history_path,
            )
            if not ready:
                try:
                    _remove_ephemeral_files(
                        artifact_root,
                        iteration_directory,
                        ephemeral_paths,
                    )
                    _tracked_storage_guard(artifact_root, state)
                    _reconcile_storage(
                        artifact_root, state, force=True)
                except ObserverError as error:
                    state["status"] = "STOPPED"
                    _append_event(
                        state,
                        "STORAGE_GUARD_FAILED",
                        generated_at_ms,
                        reason=str(error),
                        detail={
                            "phase": "warmup_materialization",
                            "iteration": iteration,
                        },
                    )
                    state = _write_state(state_path, state)
                    return _result(state, outcome="STOPPED")
                state["status"] = "STOPPED"
                _append_event(
                    state,
                    "WARMUP_NOT_READY",
                    generated_at_ms,
                    reason="BOUNDED_SHADOW_WARMUP_INCOMPLETE",
                    detail=warmup,
                )
                state = _write_state(state_path, state)
                return _result(state, outcome="STOPPED")
            source_bundle_sha256 = _normalize_evidence(
                source_bundle_path,
                evaluated_at_ms=generated_at_ms,
                calendar_path=calendar_path,
                information_path=information_path,
            )
            runner_result = shadow_runner.run_shadow_iteration(
                campaign_id=campaign_id,
                iteration=iteration,
                evaluated_at_ms=generated_at_ms,
                policy_path=policy_path,
                strategy_path=strategy_path,
                snapshot_path=snapshot_path,
                quote_history_path=quote_history_path,
                bar_history_path=bar_history_path,
                calendar_path=calendar_path,
                information_path=information_path,
                receipt_path=receipt_path,
                state_path=strategy_state_path,
            )
            packet = runner_result["packet"]
            receipt = runner_result["receipt"]
            atomic_write_json(packet_path, packet, mode=0o600)
            manifest = _source_window_manifest(
                policy=policy,
                policy_sha256=policy_sha256,
                state=state,
                iteration=iteration,
                scheduled_at_ms=scheduled_at_ms,
                evaluated_at_ms=generated_at_ms,
                snapshot=snapshot,
                sampled=sampled,
                quote_history_path=quote_history_path,
                bar_history_path=bar_history_path,
                calendar_path=calendar_path,
                information_path=information_path,
                source_bundle_sha256=source_bundle_sha256,
                watch_export_receipt_body_sha256=append_result[
                    "watch_export_receipt_body_sha256"],
                watch_export_receipt_file_sha256=append_result[
                    "watch_export_receipt_file_sha256"],
                packet=packet,
                packet_path=packet_path,
                receipt=receipt,
                receipt_path=receipt_path,
            )
            atomic_write_json(manifest_path, manifest, mode=0o600)
            _remove_ephemeral_files(
                artifact_root,
                iteration_directory,
                ephemeral_paths,
            )
        except (
                ContractError,
                ObserverError,
                market_history.HistoryError,
                OSError,
                ValueError,
        ) as error:
            existing_payloads = [
                path for path in iteration_payload_paths if path.exists()]
            for path in ephemeral_paths:
                if path.exists():
                    existing_payloads.append(path)
            try:
                _account_payload_paths(
                    state, artifact_root, existing_payloads)
                _tracked_storage_guard(artifact_root, state)
                _reconcile_storage(artifact_root, state, force=True)
            except ObserverError as storage_error:
                error = ObserverError(
                    f"{error};{storage_error}")
            state["status"] = "STOPPED"
            _append_event(
                state,
                "DECISION_PIPELINE_FAILED",
                generated_at_ms,
                reason=str(error),
                detail={"iteration": iteration},
            )
            state = _write_state(state_path, state)
            return _result(state, outcome="STOPPED")

        receipt = runner_result["receipt"]
        receipt_sha256 = digest_bytes(canonical_bytes(receipt))
        try:
            _account_payload_paths(
                state, artifact_root, iteration_payload_paths)
            _tracked_storage_guard(artifact_root, state)
        except ObserverError as error:
            state["status"] = "STOPPED"
            _append_event(
                state,
                "STORAGE_GUARD_FAILED",
                generated_at_ms,
                reason=str(error),
                detail={
                    "phase": "decision_commit",
                    "iteration": iteration,
                },
            )
            state = _write_state(state_path, state)
            return _result(state, outcome="STOPPED")
        state["completed_iterations"] = iteration
        state["last_receipt_sha256"] = receipt_sha256
        _append_event(
            state,
            "DECISION_RECEIPT_COMMITTED",
            generated_at_ms,
            detail={
                "iteration": iteration,
                "scheduled_at_ms": scheduled_at_ms,
                "decision": receipt["decision"],
                "final_outcome": receipt["final_outcome"],
                "receipt_sha256": receipt_sha256,
                "information_packet_sha256": packet["body_sha256"],
                "source_window_manifest_sha256":
                    manifest["body_sha256"],
            },
        )
        if iteration == policy["maximum_iterations"]:
            state["status"] = "FINAL_AUDIT_REQUIRED"
            _append_event(
                state,
                "MAXIMUM_ITERATIONS_REACHED",
                generated_at_ms,
            )
            try:
                _finalize_observation(
                    artifact_root=artifact_root,
                    state=state,
                    policy=policy,
                    at_ms=generated_at_ms,
                )
            except (
                    ObserverError,
                    market_history.HistoryError,
                    OSError,
                    ValueError,
            ) as error:
                state["status"] = "FINAL_AUDIT_REQUIRED"
                _append_event(
                    state,
                    "FINAL_HISTORY_AUDIT_REQUIRED",
                    generated_at_ms,
                    reason=str(error),
                    detail={"iteration": iteration},
                )
                state = _write_state(state_path, state)
                return _result(
                    state,
                    outcome="FINAL_AUDIT_REQUIRED",
                    receipt_path=receipt_path,
                )
            state = _write_state(state_path, state)
            return _result(
                state,
                outcome="COMPLETE",
                receipt_path=receipt_path,
            )
        try:
            _tracked_storage_guard(artifact_root, state)
            _reconcile_storage(artifact_root, state)
        except ObserverError as error:
            state["status"] = "STOPPED"
            _append_event(
                state,
                "STORAGE_GUARD_FAILED",
                generated_at_ms,
                reason=str(error),
                detail={"iteration": iteration},
            )
            state = _write_state(state_path, state)
            return _result(state, outcome="STOPPED")
        state = _write_state(state_path, state)
        return _result(
            state,
            outcome=receipt["final_outcome"],
            receipt_path=receipt_path,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--watch-lease-receipt", type=Path, required=True)
    parser.add_argument("--watch-export-receipt", type=Path, required=True)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = observe_once(
            campaign_id=arguments.campaign_id,
            policy_path=arguments.policy,
            strategy_path=arguments.strategy,
            snapshot_path=arguments.snapshot,
            watch_lease_receipt_path=arguments.watch_lease_receipt,
            watch_export_receipt_path=arguments.watch_export_receipt,
            source_bundle_path=arguments.source_bundle,
            artifact_root=arguments.artifact_root,
        )
    except (
            ContractError,
            ObserverError,
            market_history.HistoryError,
            OSError,
            ValueError,
    ) as error:
        print(
            "hepta_bounded_shadow_observer: FAIL " + str(error),
            file=sys.stderr,
        )
        return 78
    print(
        "hepta_bounded_shadow_observer: PASS "
        f"outcome={result['outcome']} "
        f"status={result['status']} "
        f"iterations={result['completed_iterations']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
