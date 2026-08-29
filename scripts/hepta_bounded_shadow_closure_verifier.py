#!/usr/bin/env python3

"""Produce a sealed, zero-authority closure for a completed SHADOW run."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any

import hepta_bounded_shadow_observer as observer
import hepta_market_context_builder as context_builder
import hepta_market_evidence_normalizer as evidence_normalizer
import hepta_shadow_market_history as market_history
import hepta_strategy_shadow_runner as shadow_runner
from hepta_strategy_contracts import (
    ContractError,
    atomic_write_json,
    canonical_bytes,
    digest_bytes,
    digest_document,
    digest_file,
    require_bool,
    require_digest,
    require_exact_fields,
    require_int,
    require_text,
)
import validate_hepta_strategy_decision_receipt as receipt_validator


MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
CLOSURE_SCHEMA = "hepta.bounded-shadow-campaign-closure.v1"
ZERO_AUTHORITY_FIELDS = (
    "paper_authorized",
    "live_authorized",
    "mutation_attempted",
    "direct_broker_access",
)
PERMISSION_SURFACE_FIELDS = frozenset({
    *ZERO_AUTHORITY_FIELDS,
    "mutation_authorized",
})
FINAL_AUDIT_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "policy_sha256", "strategy_id", "strategy_version",
    "strategy_sha256", "completed_iterations", "maximum_iterations",
    "finalized_at_ms", "segment_count", "segments",
    "sample_count", "missed_sample_count", "missed_decision_count",
    "payload_bytes_before_final_receipt",
    "payload_files_before_final_receipt",
    "payload_accumulator_before_final_receipt",
    *ZERO_AUTHORITY_FIELDS, "body_sha256",
})
FINAL_SEGMENT_FIELDS = frozenset({
    "segment_index", "record_count", "history_head_sha256",
    "source_sha256", "history_record_bytes", "history_index_bytes",
    "history_storage_bytes", "audit_sha256",
})
MANIFEST_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "policy_sha256", "strategy_id", "strategy_version",
    "strategy_sha256", "iteration", "scheduled_at_ms",
    "evaluated_at_ms", "segment_index", "source_first_sequence",
    "source_last_sequence", "source_total_record_count",
    "source_window_truncated", "source_predecessor_record_sha256",
    "source_history_head_sha256",
    "source_history_index_body_sha256",
    "source_history_index_file_sha256", "source_records_sha256",
    "materialization_window_ms", "materialization_maximum_records",
    "snapshot_body_sha256", "snapshot_file_sha256",
    "watch_export_receipt_body_sha256",
    "watch_export_receipt_file_sha256",
    "quote_history_body_sha256", "quote_history_file_sha256",
    "bar_history_body_sha256", "bar_history_file_sha256",
    "calendar_file_sha256", "information_file_sha256",
    "source_bundle_sha256", "information_packet_body_sha256",
    "information_packet_file_sha256",
    "decision_receipt_file_sha256", "decision_receipt_sha256",
    *ZERO_AUTHORITY_FIELDS, "body_sha256",
})
PACKET_FIELDS = frozenset({
    "schema", "packet_id", "campaign_id", "iteration", "mode",
    "created_at_ms", "evaluated_at_ms", "instrument", "strategy",
    "context_builder", "source_snapshot", "authority", "freshness",
    "provenance", "market", "session", "history", "features",
    "economic_calendar", "information", "portfolio", "service",
    "privacy", "evidence_refs", "body_sha256",
})
PACKET_STRATEGY_FIELDS = frozenset({
    "strategy_id", "strategy_version", "pinned_sha256",
    "config_sha256", "evaluator_sha256", "builder_sha256",
    "normalizer_sha256", "contracts_sha256", "sha256_verified",
})
PACKET_BUILDER_FIELDS = frozenset({
    "schema", "builder_sha256", "normalizer_sha256",
    "contracts_sha256", "feature_calculation_version",
})
PACKET_SNAPSHOT_FIELDS = frozenset({
    "schema", "file_sha256", "body_sha256", "catalog_sha256",
    "generated_at_ms", "domain_id", "agent_uid",
    "mutation_attempted", "direct_broker_access",
})
PACKET_AUTHORITY_FIELDS = frozenset({
    "health_authoritative", "quote_authoritative",
    "account_authoritative", "positions_authoritative",
    "orders_authoritative", "risk_authoritative",
    "paper_authorized", "live_authorized",
})
PACKET_ZERO_AUTHORITY_PATHS = frozenset({
    ("authority", "paper_authorized"),
    ("authority", "live_authorized"),
    ("source_snapshot", "mutation_attempted"),
    ("source_snapshot", "direct_broker_access"),
})
CALENDAR_FIELDS = frozenset({
    "schema", "provider", "source_ref", "observed_at_ms",
    "sources", "events", "attestation", "body_sha256",
})
INFORMATION_FIELDS = frozenset({
    "schema", "provider", "source_ref", "observed_at_ms",
    "sources", "items", "attestation", "body_sha256",
})
ITERATION_RETAINED_FILES = frozenset({
    "calendar.json",
    "information.json",
    "information-packet.json",
    "source-window-manifest.json",
})
EPHEMERAL_RESIDUALS = (
    "EPHEMERAL_BAR_HISTORY_NOT_RETAINED",
    "EPHEMERAL_QUOTE_HISTORY_NOT_RETAINED",
    "EPHEMERAL_SAMPLED_BARS_NOT_RETAINED",
    "ROOT_WATCH_EXPORT_RECEIPT_METADATA_NOT_REPLAYABLE",
    "ROOT_WATCH_LEASE_RECEIPT_METADATA_NOT_REPLAYABLE",
    "ROOT_WATCH_SNAPSHOT_METADATA_NOT_REPLAYABLE",
)


class ClosureError(RuntimeError):
    """Stable fail-closed campaign closure error."""


def _identity(metadata: os.stat_result) -> tuple[Any, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _stable_document(
    path: Path,
    label: str,
    *,
    artifact_uid: int | None = None,
    artifact_mode: int | None = None,
    maximum_bytes: int = MAX_DOCUMENT_BYTES,
) -> tuple[dict[str, Any], bytes]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ClosureError(f"{label}_READ_FAILED") from error
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
                not stat.S_ISREG(before.st_mode) or
                before.st_nlink != 1 or
                before.st_size > maximum_bytes or
                (
                    artifact_uid is not None and
                    before.st_uid != artifact_uid
                ) or
                (
                    artifact_mode is not None and
                    mode != artifact_mode
                ) or
                (
                    artifact_mode is None and
                    mode & 0o022
                )):
            raise ClosureError(f"{label}_METADATA_INVALID")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1 << 20, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        contents = b"".join(chunks)
        after = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise ClosureError(f"{label}_READ_FAILED") from error
    finally:
        os.close(descriptor)
    if (
            len(contents) > maximum_bytes or
            len(contents) != before.st_size or
            _identity(before) != _identity(after) or
            _identity(after) != _identity(current)):
        raise ClosureError(f"{label}_CHANGED_DURING_READ")
    try:
        document = observer.load_document(
            path, label, maximum_bytes=maximum_bytes)
    except ContractError as error:
        raise ClosureError(str(error)) from error
    if contents != canonical_bytes(document):
        raise ClosureError(f"{label}_NOT_CANONICAL")
    return document, contents


def _zero_authority(document: dict[str, Any], label: str) -> None:
    for field in ZERO_AUTHORITY_FIELDS:
        require_bool(
            document.get(field),
            False,
            f"{label}_AUTHORITY_INVALID",
        )


def _permission_surface_name(field: str) -> bool:
    """Identify permission-shaped fields without confusing authoritative data."""

    normalized = field.lower()
    return (
        normalized in PERMISSION_SURFACE_FIELDS or
        "permission" in normalized or
        normalized.endswith("_authorized") or
        normalized.endswith("_authority") or
        normalized.startswith("paper_") or
        normalized.startswith("live_") or
        normalized.startswith("mutation_") or
        normalized.startswith("direct_broker_")
    )


def _validate_packet_zero_authority(packet: dict[str, Any]) -> None:
    """Require the one exact permission layout emitted by the SHADOW builder.

    Packet subdocuments contain many provenance booleans ending in
    ``_authoritative``.  Those describe evidence quality and are deliberately
    distinct from permission fields.  Every permission-shaped key, at every
    nesting depth, must instead occur at one of the four pinned paths below
    and must be the JSON boolean ``false``.
    """

    authority = packet.get("authority")
    if (
            not isinstance(authority, dict) or
            set(authority) != PACKET_AUTHORITY_FIELDS):
        raise ClosureError("CLOSURE_PACKET_AUTHORITY_FIELDS_INVALID")
    source_snapshot = packet.get("source_snapshot")
    if (
            not isinstance(source_snapshot, dict) or
            set(source_snapshot) != PACKET_SNAPSHOT_FIELDS):
        raise ClosureError("CLOSURE_PACKET_SNAPSHOT_FIELDS_INVALID")
    for field in PACKET_AUTHORITY_FIELDS - {
            "paper_authorized", "live_authorized"}:
        if type(authority[field]) is not bool:
            raise ClosureError("CLOSURE_PACKET_AUTHORITY_INVALID")

    observed: set[tuple[str, ...]] = set()

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for field, nested in value.items():
                current = (*path, field)
                if field == "authority":
                    if current != ("authority",) or not isinstance(
                            nested, dict):
                        raise ClosureError(
                            "CLOSURE_PACKET_PERMISSION_SCHEMA_INVALID")
                elif _permission_surface_name(field):
                    if current not in PACKET_ZERO_AUTHORITY_PATHS:
                        raise ClosureError(
                            "CLOSURE_PACKET_PERMISSION_SCHEMA_INVALID")
                    if nested is not False:
                        raise ClosureError(
                            "CLOSURE_PACKET_AUTHORITY_INVALID")
                    observed.add(current)
                visit(nested, current)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                visit(nested, (*path, f"[{index}]"))

    visit(packet, ())
    if observed != set(PACKET_ZERO_AUTHORITY_PATHS):
        raise ClosureError("CLOSURE_PACKET_PERMISSION_SCHEMA_INVALID")


def _validate_segment_authority_transition(
    *,
    segment_index: int,
    records: list[dict[str, Any]],
    previous_records: list[dict[str, Any]] | None,
) -> None:
    """Pin campaign authority to one PROVISION then exact ROTATE bridges."""

    if not records:
        raise ClosureError("CLOSURE_SEGMENT_AUTHORITY_CHAIN_INVALID")
    first = records[0]
    if segment_index == 1:
        if (
                previous_records is not None or
                first["watch_lease_operation"] != "PROVISION" or
                first["watch_generation"] != 1 or
                first["watch_lease_previous_generation"] is not None or
                first[
                    "watch_lease_previous_receipt_body_sha256"] is not None):
            raise ClosureError("CLOSURE_SEGMENT_AUTHORITY_CHAIN_INVALID")
        return
    if not previous_records:
        raise ClosureError("CLOSURE_SEGMENT_AUTHORITY_CHAIN_INVALID")
    previous = previous_records[-1]
    if (
            first["watch_lease_operation"] != "ROTATE" or
            first["watch_generation"] !=
            previous["watch_generation"] + 1 or
            first["watch_lease_previous_generation"] !=
            previous["watch_generation"] or
            first["watch_lease_previous_receipt_body_sha256"] !=
            previous["watch_lease_receipt_body_sha256"]):
        raise ClosureError("CLOSURE_SEGMENT_AUTHORITY_CHAIN_INVALID")


def _sealed_body(
    document: dict[str, Any],
    label: str,
) -> str:
    claimed = require_digest(
        document.get("body_sha256"), f"{label}_DIGEST_INVALID")
    body = dict(document)
    body.pop("body_sha256")
    if claimed != digest_document(body):
        raise ClosureError(f"{label}_DIGEST_MISMATCH")
    return claimed


def _source_digest(records: list[dict[str, Any]]) -> str:
    return digest_document({
        "schema": "hepta.shadow-market-bar-source.v1",
        "source_kind": "SHADOW_HISTORY_RECORDS",
        "digests": [record["record_sha256"] for record in records],
    })


def _relative_directory_names(path: Path, prefix: str) -> list[str]:
    try:
        entries = list(path.iterdir())
    except OSError as error:
        raise ClosureError("CLOSURE_DIRECTORY_READ_FAILED") from error
    if any(entry.is_symlink() for entry in entries):
        raise ClosureError("CLOSURE_ARTIFACT_SYMLINK")
    if any(
            not entry.is_dir() or not entry.name.startswith(prefix)
            for entry in entries):
        raise ClosureError("CLOSURE_DIRECTORY_SET_INVALID")
    return sorted(
        entry.name
        for entry in entries
    )


def _validate_final_audit(
    final_audit: dict[str, Any],
    final_contents: bytes,
    *,
    policy: dict[str, Any],
    policy_sha256: str,
    state: dict[str, Any],
) -> None:
    require_exact_fields(
        final_audit,
        FINAL_AUDIT_FIELDS,
        "CLOSURE_FINAL_AUDIT_FIELDS_INVALID",
    )
    if (
            final_audit["schema"] !=
            "hepta.bounded-shadow-final-audit-receipt.v2" or
            final_audit["version"] != 2 or
            final_audit["campaign_id"] != policy["campaign_id"] or
            final_audit["campaign_sha256"] != policy["campaign_sha256"] or
            final_audit["policy_sha256"] != policy_sha256 or
            final_audit["strategy_id"] != policy["strategy_id"] or
            final_audit["strategy_version"] !=
            policy["strategy_version"] or
            final_audit["strategy_sha256"] !=
            policy["strategy_sha256"] or
            final_audit["completed_iterations"] !=
            state["completed_iterations"] or
            final_audit["maximum_iterations"] !=
            policy["maximum_iterations"] or
            final_audit["segment_count"] !=
            state["final_audit_segment_count"] or
            final_audit["sample_count"] != state["sample_count"] or
            final_audit["missed_sample_count"] !=
            state["missed_sample_count"] or
            final_audit["missed_decision_count"] !=
            state["missed_decision_count"] or
            state["final_audit_receipt_sha256"] !=
            digest_bytes(final_contents)):
        raise ClosureError("CLOSURE_FINAL_AUDIT_BINDING_INVALID")
    _sealed_body(final_audit, "CLOSURE_FINAL_AUDIT")
    _zero_authority(final_audit, "CLOSURE_FINAL_AUDIT")
    require_int(
        final_audit["finalized_at_ms"],
        "CLOSURE_FINAL_AUDIT_TIME_INVALID",
        minimum=0,
    )
    segments = final_audit["segments"]
    if (
            not isinstance(segments, list) or
            len(segments) != final_audit["segment_count"] or
            not segments):
        raise ClosureError("CLOSURE_FINAL_AUDIT_SEGMENTS_INVALID")
    sample_count = require_int(
        final_audit["sample_count"],
        "CLOSURE_FINAL_AUDIT_SAMPLE_COUNT_INVALID",
        minimum=1,
    )
    if (
            require_int(
                final_audit["missed_sample_count"],
                "CLOSURE_FINAL_AUDIT_MISSED_SAMPLE_COUNT_INVALID",
                minimum=0,
            ) != 0 or
            require_int(
                final_audit["missed_decision_count"],
                "CLOSURE_FINAL_AUDIT_MISSED_DECISION_COUNT_INVALID",
                minimum=0,
            ) != 0):
        raise ClosureError("CLOSURE_FINAL_AUDIT_MISSED_COUNT_NONZERO")
    retained_sample_count = 0
    for segment in segments:
        require_exact_fields(
            segment,
            FINAL_SEGMENT_FIELDS,
            "CLOSURE_FINAL_SEGMENT_FIELDS_INVALID",
        )
        retained_sample_count += require_int(
            segment["record_count"],
            "CLOSURE_FINAL_AUDIT_RECORD_COUNT_INVALID",
            minimum=1,
        )
    if sample_count != retained_sample_count:
        raise ClosureError("CLOSURE_FINAL_AUDIT_SAMPLE_COUNT_DRIFT")


def _validate_packet(
    packet: dict[str, Any],
    *,
    policy: dict[str, Any],
    strategy_path: Path,
    iteration: int,
    manifest: dict[str, Any],
    record: dict[str, Any],
    calendar: dict[str, Any],
    calendar_file_sha256: str,
    information: dict[str, Any],
    information_file_sha256: str,
) -> None:
    _validate_packet_zero_authority(packet)
    require_exact_fields(
        packet, PACKET_FIELDS, "CLOSURE_PACKET_FIELDS_INVALID")
    packet_body_sha256 = _sealed_body(packet, "CLOSURE_PACKET")
    if (
            packet["schema"] != "hepta.market-information-packet.v1" or
            packet["campaign_id"] != policy["campaign_id"] or
            packet["iteration"] != iteration or
            packet["mode"] != "SHADOW" or
            packet["created_at_ms"] != manifest["evaluated_at_ms"] or
            packet["evaluated_at_ms"] != manifest["evaluated_at_ms"] or
            packet["instrument"] != "EUR.USD" or
            packet_body_sha256 !=
            manifest["information_packet_body_sha256"]):
        raise ClosureError("CLOSURE_PACKET_BINDING_INVALID")
    strategy = require_exact_fields(
        packet["strategy"],
        PACKET_STRATEGY_FIELDS,
        "CLOSURE_PACKET_STRATEGY_FIELDS_INVALID",
    )
    builder = require_exact_fields(
        packet["context_builder"],
        PACKET_BUILDER_FIELDS,
        "CLOSURE_PACKET_BUILDER_FIELDS_INVALID",
    )
    script_root = Path(__file__).resolve().parent
    expected_code = {
        "config_sha256": digest_file(strategy_path),
        "evaluator_sha256": digest_file(
            script_root /
            "hepta_eurusd_confirmed_momentum_strategy.py"),
        "builder_sha256": digest_file(
            script_root / "hepta_market_context_builder.py"),
        "normalizer_sha256": digest_file(
            script_root / "hepta_market_evidence_normalizer.py"),
        "contracts_sha256": digest_file(
            script_root / "hepta_strategy_contracts.py"),
    }
    if (
            strategy["strategy_id"] != policy["strategy_id"] or
            strategy["strategy_version"] != policy["strategy_version"] or
            strategy["pinned_sha256"] != policy["strategy_sha256"] or
            strategy["sha256_verified"] is not True or
            any(strategy[key] != value for key, value in expected_code.items())
            or
            builder["schema"] != "hepta.market-context-builder.v3" or
            builder["builder_sha256"] != expected_code["builder_sha256"] or
            builder["normalizer_sha256"] !=
            expected_code["normalizer_sha256"] or
            builder["contracts_sha256"] !=
            expected_code["contracts_sha256"]):
        raise ClosureError("CLOSURE_PACKET_STRATEGY_BINDING_INVALID")
    source_snapshot = require_exact_fields(
        packet["source_snapshot"],
        PACKET_SNAPSHOT_FIELDS,
        "CLOSURE_PACKET_SNAPSHOT_FIELDS_INVALID",
    )
    if (
            source_snapshot["schema"] !=
            "hepta.shadow-watch-snapshot.v2" or
            source_snapshot["file_sha256"] !=
            manifest["snapshot_file_sha256"] or
            source_snapshot["body_sha256"] !=
            record["snapshot_body_sha256"] or
            source_snapshot["catalog_sha256"] !=
            record["catalog_sha256"] or
            source_snapshot["generated_at_ms"] !=
            record["generated_at_ms"] or
            source_snapshot["domain_id"] != record["domain_id"] or
            source_snapshot["agent_uid"] != record["agent_uid"] or
            source_snapshot["mutation_attempted"] is not False or
            source_snapshot["direct_broker_access"] is not False):
        raise ClosureError("CLOSURE_PACKET_SNAPSHOT_BINDING_INVALID")
    calendar_summary = packet["economic_calendar"]
    information_summary = packet["information"]
    if (
            not isinstance(calendar_summary, dict) or
            not isinstance(information_summary, dict) or
            calendar_summary.get("schema") != calendar["schema"] or
            calendar_summary.get("provider") != calendar["provider"] or
            calendar_summary.get("source_ref") != calendar["source_ref"] or
            calendar_summary.get("observed_at_ms") !=
            calendar["observed_at_ms"] or
            calendar_summary.get("sources") != calendar["sources"] or
            calendar_summary.get("events") != calendar["events"] or
            calendar_summary.get("file_sha256") !=
            calendar_file_sha256 or
            information_summary.get("schema") != information["schema"] or
            information_summary.get("provider") !=
            information["provider"] or
            information_summary.get("source_ref") !=
            information["source_ref"] or
            information_summary.get("observed_at_ms") !=
            information["observed_at_ms"] or
            information_summary.get("sources") !=
            information["sources"] or
            information_summary.get("items") != information["items"] or
            information_summary.get("file_sha256") !=
            information_file_sha256):
        raise ClosureError("CLOSURE_PACKET_EVIDENCE_BINDING_INVALID")
    evidence_refs = packet["evidence_refs"]
    if (
            not isinstance(evidence_refs, list) or
            len(evidence_refs) != len(set(evidence_refs)) or
            any(
                not isinstance(value, str)
                for value in evidence_refs
            )):
        raise ClosureError("CLOSURE_PACKET_EVIDENCE_REFS_INVALID")
    required_evidence = {
        manifest["snapshot_file_sha256"],
        manifest["quote_history_file_sha256"],
        manifest["bar_history_file_sha256"],
        manifest["calendar_file_sha256"],
        manifest["information_file_sha256"],
        expected_code["config_sha256"],
    }
    if not required_evidence.issubset(set(evidence_refs)):
        raise ClosureError("CLOSURE_PACKET_EVIDENCE_REFS_INVALID")


def _validate_attested_evidence(
    calendar: dict[str, Any],
    information: dict[str, Any],
    *,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    require_exact_fields(
        calendar, CALENDAR_FIELDS, "CLOSURE_CALENDAR_FIELDS_INVALID")
    require_exact_fields(
        information,
        INFORMATION_FIELDS,
        "CLOSURE_INFORMATION_FIELDS_INVALID",
    )
    _sealed_body(calendar, "CLOSURE_CALENDAR")
    _sealed_body(information, "CLOSURE_INFORMATION")
    expected_source_ref = "bundle:" + manifest["source_bundle_sha256"]
    if (
            calendar["schema"] != "hepta.economic-calendar.v3" or
            information["schema"] !=
            "hepta.market-information-items.v3" or
            calendar["source_ref"] != expected_source_ref or
            information["source_ref"] != expected_source_ref or
            calendar["provider"] !=
            "HEPTA_ATTESTED_OFFICIAL_SOURCE_BUNDLE" or
            information["provider"] !=
            "HEPTA_ATTESTED_OFFICIAL_SOURCE_BUNDLE" or
            calendar["observed_at_ms"] !=
            information["observed_at_ms"] or
            calendar["sources"] != information["sources"] or
            calendar["attestation"] != information["attestation"]):
        raise ClosureError("CLOSURE_EVIDENCE_PAIR_BINDING_INVALID")
    try:
        evidence_normalizer.validate_output_attestation(
            calendar,
            semantic_field="events",
            evaluated_at_ms=manifest["evaluated_at_ms"],
        )
        evidence_normalizer.validate_output_attestation(
            information,
            semantic_field="items",
            evaluated_at_ms=manifest["evaluated_at_ms"],
        )
    except ContractError as error:
        raise ClosureError("CLOSURE_SOURCE_ATTESTATION_INVALID") from error
    attestation = calendar["attestation"]
    return {
        "receipt_body_sha256": attestation["receipt_body_sha256"],
        "receipt_file_sha256": attestation["receipt_file_sha256"],
        "extractor_code_sha256": attestation["extractor_code_sha256"],
        "semantic_output_sha256":
            attestation["semantic_output_sha256"],
        "completeness_sha256": attestation["completeness_sha256"],
        "raw_payloads_verified": True,
    }


def _iteration_directories(
    artifact_root: Path,
    segment_count: int,
) -> dict[int, tuple[int, Path]]:
    result: dict[int, tuple[int, Path]] = {}
    for segment_index in range(1, segment_count + 1):
        iterations_root = (
            artifact_root / "segments" /
            f"segment-{segment_index:06d}" / "iterations"
        )
        if not iterations_root.exists():
            continue
        if iterations_root.is_symlink() or not iterations_root.is_dir():
            raise ClosureError("CLOSURE_ITERATION_ROOT_INVALID")
        for name in _relative_directory_names(
                iterations_root, "iteration-"):
            try:
                iteration = int(name.removeprefix("iteration-"))
            except ValueError as error:
                raise ClosureError(
                    "CLOSURE_ITERATION_DIRECTORY_INVALID") from error
            if (
                    name != f"iteration-{iteration:06d}" or
                    iteration < 1 or
                    iteration in result):
                raise ClosureError(
                    "CLOSURE_ITERATION_DIRECTORY_INVALID")
            result[iteration] = (
                segment_index, iterations_root / name)
    return result


def _scheduled_at_ms(
    policy: dict[str, Any],
    iteration: int,
) -> int:
    if (
            isinstance(iteration, bool) or
            not isinstance(iteration, int) or
            not 1 <= iteration <= policy["maximum_iterations"]):
        raise ClosureError("CLOSURE_ITERATION_NUMBER_INVALID")
    return (
        policy["valid_after_ms"] +
        (iteration - 1) * policy["slot_interval_ms"]
    )


def _validate_iteration(
    *,
    artifact_root: Path,
    artifact_uid: int,
    policy: dict[str, Any],
    policy_sha256: str,
    strategy_path: Path,
    iteration: int,
    segment_index: int,
    iteration_directory: Path,
    receipt_path: Path,
    records: list[dict[str, Any]],
    decision_event: dict[str, Any],
) -> dict[str, Any]:
    entries = list(iteration_directory.iterdir())
    if (
            any(entry.is_symlink() or not entry.is_file() for entry in entries)
            or
            {entry.name for entry in entries} != ITERATION_RETAINED_FILES):
        raise ClosureError("CLOSURE_ITERATION_ARTIFACT_SET_INVALID")
    manifest, manifest_contents = _stable_document(
        iteration_directory / "source-window-manifest.json",
        "CLOSURE_MANIFEST",
        artifact_uid=artifact_uid,
        artifact_mode=0o600,
    )
    require_exact_fields(
        manifest, MANIFEST_FIELDS, "CLOSURE_MANIFEST_FIELDS_INVALID")
    manifest_body_sha256 = _sealed_body(
        manifest, "CLOSURE_MANIFEST")
    _zero_authority(manifest, "CLOSURE_MANIFEST")
    scheduled = _scheduled_at_ms(policy, iteration)
    if (
            manifest["schema"] !=
            "hepta.bounded-shadow-source-window-manifest.v1" or
            manifest["version"] != 1 or
            manifest["campaign_id"] != policy["campaign_id"] or
            manifest["campaign_sha256"] != policy["campaign_sha256"] or
            manifest["policy_sha256"] != policy_sha256 or
            manifest["strategy_id"] != policy["strategy_id"] or
            manifest["strategy_version"] !=
            policy["strategy_version"] or
            manifest["strategy_sha256"] !=
            policy["strategy_sha256"] or
            manifest["iteration"] != iteration or
            manifest["segment_index"] != segment_index or
            manifest["scheduled_at_ms"] != scheduled or
            not scheduled <= manifest["evaluated_at_ms"] <=
            scheduled + policy["maximum_lateness_ms"]):
        raise ClosureError("CLOSURE_MANIFEST_BINDING_INVALID")
    first_sequence = require_int(
        manifest["source_first_sequence"],
        "CLOSURE_SOURCE_WINDOW_INVALID",
        minimum=1,
    )
    last_sequence = require_int(
        manifest["source_last_sequence"],
        "CLOSURE_SOURCE_WINDOW_INVALID",
        minimum=first_sequence,
        maximum=len(records),
    )
    if (
            manifest["source_total_record_count"] != last_sequence or
            manifest["source_window_truncated"] is not
            (first_sequence > 1)):
        raise ClosureError("CLOSURE_SOURCE_WINDOW_INVALID")
    selected = records[first_sequence - 1:last_sequence]
    predecessor = (
        None if first_sequence == 1 else
        records[first_sequence - 2]["record_sha256"]
    )
    last_record = selected[-1]
    if (
            manifest["source_predecessor_record_sha256"] != predecessor or
            manifest["source_history_head_sha256"] !=
            last_record["record_sha256"] or
            manifest["source_records_sha256"] !=
            _source_digest(selected) or
            manifest["snapshot_body_sha256"] !=
            last_record["snapshot_body_sha256"] or
            manifest["snapshot_file_sha256"] !=
            last_record["snapshot_file_sha256"] or
            manifest["watch_export_receipt_body_sha256"] !=
            last_record["watch_export_receipt_body_sha256"] or
            manifest["watch_export_receipt_file_sha256"] !=
            last_record["watch_export_receipt_file_sha256"]):
        raise ClosureError("CLOSURE_SOURCE_WINDOW_BINDING_INVALID")
    for field in (
            "source_history_index_body_sha256",
            "source_history_index_file_sha256",
            "quote_history_body_sha256", "quote_history_file_sha256",
            "bar_history_body_sha256", "bar_history_file_sha256"):
        require_digest(
            manifest[field], "CLOSURE_MANIFEST_DIGEST_INVALID")
    for ephemeral in (
            "sampled-bars.json", "quote-history.json", "bar-history.json"):
        if (iteration_directory / ephemeral).exists():
            raise ClosureError("CLOSURE_EPHEMERAL_ARTIFACT_PRESENT")
    if last_sequence == len(records):
        head, head_contents = _stable_document(
            iteration_directory.parents[1] / "history" /
            market_history.HEAD_NAME,
            "CLOSURE_HISTORY_HEAD",
            artifact_uid=artifact_uid,
            artifact_mode=0o600,
        )
        if (
                head.get("last_record_sha256") !=
                manifest["source_history_head_sha256"] or
                head.get("body_sha256") !=
                manifest["source_history_index_body_sha256"] or
                digest_bytes(head_contents) !=
                manifest["source_history_index_file_sha256"]):
            raise ClosureError("CLOSURE_HISTORY_HEAD_BINDING_INVALID")

    calendar, calendar_contents = _stable_document(
        iteration_directory / "calendar.json",
        "CLOSURE_CALENDAR",
        artifact_uid=artifact_uid,
        artifact_mode=0o600,
    )
    information, information_contents = _stable_document(
        iteration_directory / "information.json",
        "CLOSURE_INFORMATION",
        artifact_uid=artifact_uid,
        artifact_mode=0o600,
    )
    calendar_file_sha256 = digest_bytes(calendar_contents)
    information_file_sha256 = digest_bytes(information_contents)
    if (
            calendar_file_sha256 != manifest["calendar_file_sha256"] or
            information_file_sha256 !=
            manifest["information_file_sha256"]):
        raise ClosureError("CLOSURE_EVIDENCE_FILE_BINDING_INVALID")
    attestation = _validate_attested_evidence(
        calendar, information, manifest=manifest)

    packet, packet_contents = _stable_document(
        iteration_directory / "information-packet.json",
        "CLOSURE_PACKET",
        artifact_uid=artifact_uid,
        artifact_mode=0o600,
    )
    packet_file_sha256 = digest_bytes(packet_contents)
    if packet_file_sha256 != manifest["information_packet_file_sha256"]:
        raise ClosureError("CLOSURE_PACKET_FILE_BINDING_INVALID")
    _validate_packet(
        packet,
        policy=policy,
        strategy_path=strategy_path,
        iteration=iteration,
        manifest=manifest,
        record=last_record,
        calendar=calendar,
        calendar_file_sha256=calendar_file_sha256,
        information=information,
        information_file_sha256=information_file_sha256,
    )

    receipt, receipt_contents = _stable_document(
        receipt_path,
        "CLOSURE_DECISION_RECEIPT",
        artifact_uid=artifact_uid,
        artifact_mode=0o600,
        maximum_bytes=262_144,
    )
    receipt_file_sha256 = digest_bytes(receipt_contents)
    try:
        receipt_validator.validate_observation_policy_binding(
            receipt,
            policy_sha256=policy_sha256,
            campaign_sha256=policy["campaign_sha256"],
        )
    except ContractError as error:
        raise ClosureError("CLOSURE_DECISION_RECEIPT_INVALID") from error
    if (
            receipt["campaign_id"] != policy["campaign_id"] or
            receipt["strategy_id"] != policy["strategy_id"] or
            receipt["strategy_version"] != policy["strategy_version"] or
            receipt["strategy_sha256"] != policy["strategy_sha256"] or
            receipt["information_packet_sha256"] !=
            digest_document(packet) or
            receipt["catalog_sha256"] != last_record["catalog_sha256"] or
            receipt["descriptor_sha256"] !=
            digest_document(last_record["descriptor_sha256"]) or
            receipt_file_sha256 !=
            manifest["decision_receipt_file_sha256"] or
            receipt_file_sha256 != manifest["decision_receipt_sha256"]):
        raise ClosureError("CLOSURE_DECISION_RECEIPT_BINDING_INVALID")
    if not {
            policy_sha256,
            policy["campaign_sha256"],
            *packet["evidence_refs"],
    }.issubset(set(receipt["evidence_refs"])):
        raise ClosureError("CLOSURE_DECISION_EVIDENCE_INVALID")
    detail = decision_event.get("detail")
    if (
            not isinstance(detail, dict) or
            detail.get("iteration") != iteration or
            detail.get("scheduled_at_ms") != scheduled or
            detail.get("decision") != receipt["decision"] or
            detail.get("final_outcome") != receipt["final_outcome"] or
            detail.get("receipt_sha256") != receipt_file_sha256 or
            detail.get("information_packet_sha256") !=
            packet["body_sha256"] or
            detail.get("source_window_manifest_sha256") !=
            manifest_body_sha256):
        raise ClosureError("CLOSURE_STATE_DECISION_EVENT_INVALID")
    residuals = list(EPHEMERAL_RESIDUALS)
    if last_sequence < len(records):
        residuals.append("HISTORICAL_HISTORY_HEAD_NOT_RETAINED")
    return {
        "iteration": iteration,
        "segment_index": segment_index,
        "scheduled_at_ms": scheduled,
        "evaluated_at_ms": manifest["evaluated_at_ms"],
        "source_first_sequence": first_sequence,
        "source_last_sequence": last_sequence,
        "source_record_count": len(selected),
        "source_total_record_count":
            manifest["source_total_record_count"],
        "source_window_truncated":
            manifest["source_window_truncated"],
        "source_predecessor_record_sha256":
            manifest["source_predecessor_record_sha256"],
        "source_records_sha256": manifest["source_records_sha256"],
        "source_history_head_sha256":
            manifest["source_history_head_sha256"],
        "source_history_index_body_sha256":
            manifest["source_history_index_body_sha256"],
        "source_history_index_file_sha256":
            manifest["source_history_index_file_sha256"],
        "materialization_window_ms":
            manifest["materialization_window_ms"],
        "materialization_maximum_records":
            manifest["materialization_maximum_records"],
        "snapshot_body_sha256": manifest["snapshot_body_sha256"],
        "snapshot_file_sha256": manifest["snapshot_file_sha256"],
        "watch_lease_receipt_body_sha256":
            last_record["watch_lease_receipt_body_sha256"],
        "watch_lease_receipt_file_sha256":
            last_record["watch_lease_receipt_file_sha256"],
        "watch_export_receipt_body_sha256":
            manifest["watch_export_receipt_body_sha256"],
        "watch_export_receipt_file_sha256":
            manifest["watch_export_receipt_file_sha256"],
        "quote_history_body_sha256":
            manifest["quote_history_body_sha256"],
        "quote_history_file_sha256":
            manifest["quote_history_file_sha256"],
        "bar_history_body_sha256":
            manifest["bar_history_body_sha256"],
        "bar_history_file_sha256":
            manifest["bar_history_file_sha256"],
        "calendar_body_sha256": calendar["body_sha256"],
        "calendar_file_sha256": calendar_file_sha256,
        "information_body_sha256": information["body_sha256"],
        "information_file_sha256": information_file_sha256,
        "source_attestation": attestation,
        "information_packet_body_sha256": packet["body_sha256"],
        "information_packet_file_sha256": packet_file_sha256,
        "decision_receipt_file_sha256": receipt_file_sha256,
        "source_window_manifest_body_sha256": manifest_body_sha256,
        "source_window_manifest_file_sha256":
            digest_bytes(manifest_contents),
        "final_outcome": receipt["final_outcome"],
        "residual_evidence": sorted(residuals),
    }


def verify_closure(
    *,
    artifact_root: Path,
    policy_path: Path,
    strategy_path: Path,
    output_path: Path,
    verified_at_ms: int | None = None,
) -> dict[str, Any]:
    """Fully revalidate retained evidence and seal one closure receipt."""

    artifact_root = Path(os.path.abspath(artifact_root))
    try:
        root_metadata = os.lstat(artifact_root)
    except OSError as error:
        raise ClosureError("CLOSURE_ARTIFACT_ROOT_READ_FAILED") from error
    if (
            not stat.S_ISDIR(root_metadata.st_mode) or
            stat.S_ISLNK(root_metadata.st_mode) or
            root_metadata.st_nlink < 1 or
            root_metadata.st_mode & 0o022):
        raise ClosureError("CLOSURE_ARTIFACT_ROOT_METADATA_INVALID")
    artifact_uid = root_metadata.st_uid
    output_absolute = Path(os.path.abspath(output_path))
    try:
        output_absolute.relative_to(artifact_root)
    except ValueError:
        pass
    else:
        raise ClosureError("CLOSURE_OUTPUT_INSIDE_ARTIFACT_ROOT")

    policy_seed, _policy_seed_contents = _stable_document(
        policy_path, "CLOSURE_POLICY", maximum_bytes=65_536)
    campaign_id = require_text(
        policy_seed.get("campaign_id"),
        "CLOSURE_CAMPAIGN_ID_INVALID",
        identifier=True,
    )
    policy, policy_sha256 = shadow_runner.load_observation_policy(
        policy_path, campaign_id=campaign_id)
    if policy != policy_seed:
        raise ClosureError("CLOSURE_POLICY_READ_DRIFT")
    shadow_runner.validate_policy_strategy_binding(
        policy, strategy_path)
    strategy_document, _strategy_contents = _stable_document(
        strategy_path, "CLOSURE_STRATEGY")
    if (
            strategy_document["strategy_id"] != policy["strategy_id"] or
            strategy_document["strategy_version"] !=
            policy["strategy_version"]):
        raise ClosureError("CLOSURE_STRATEGY_BINDING_INVALID")

    state_path = artifact_root / "observer-state.json"
    state, state_contents = _stable_document(
        state_path,
        "CLOSURE_OBSERVER_STATE",
        artifact_uid=artifact_uid,
        artifact_mode=0o600,
        maximum_bytes=8 << 20,
    )
    try:
        observer._validate_state(
            state, policy=policy, policy_sha256=policy_sha256)
    except (ContractError, observer.ObserverError) as error:
        raise ClosureError("CLOSURE_OBSERVER_STATE_INVALID") from error
    if (
            state["status"] != "COMPLETE" or
            state["completed_iterations"] !=
            policy["maximum_iterations"] or
            state["missed_sample_count"] != 0 or
            state["missed_decision_count"] != 0):
        raise ClosureError("CLOSURE_OBSERVER_NOT_COMPLETE")

    final_path = artifact_root / "final-audit-receipt.json"
    final_audit, final_contents = _stable_document(
        final_path,
        "CLOSURE_FINAL_AUDIT",
        artifact_uid=artifact_uid,
        artifact_mode=0o600,
    )
    _validate_final_audit(
        final_audit,
        final_contents,
        policy=policy,
        policy_sha256=policy_sha256,
        state=state,
    )

    expected_segment_names = [
        f"segment-{index:06d}"
        for index in range(1, final_audit["segment_count"] + 1)
    ]
    segments_root = artifact_root / "segments"
    if _relative_directory_names(
            segments_root, "segment-") != expected_segment_names:
        raise ClosureError("CLOSURE_SEGMENT_SET_INVALID")
    records_by_segment: dict[int, list[dict[str, Any]]] = {}
    closure_segments: list[dict[str, Any]] = []
    if len(final_audit["segments"]) != final_audit["segment_count"]:
        raise ClosureError("CLOSURE_FINAL_AUDIT_SEGMENTS_INVALID")
    for index, retained in enumerate(final_audit["segments"], start=1):
        require_exact_fields(
            retained,
            FINAL_SEGMENT_FIELDS,
            "CLOSURE_FINAL_SEGMENT_FIELDS_INVALID",
        )
        history_directory = (
            segments_root / f"segment-{index:06d}" / "history")
        previous_history_directory = (
            None if index == 1 else
            segments_root / f"segment-{index - 1:06d}" / "history"
        )
        try:
            audit = market_history.audit_history(
                history_directory,
                cadence_ms=state["collection_cadence_ms"],
                maximum_jitter_ms=state[
                    "maximum_collection_jitter_ms"],
                previous_segment_history_directory=
                    previous_history_directory,
            )
            records = market_history.load_history(
                history_directory,
                cadence_ms=state["collection_cadence_ms"],
                maximum_jitter_ms=state[
                    "maximum_collection_jitter_ms"],
                previous_segment_history_directory=
                    previous_history_directory,
            )
        except market_history.HistoryError as error:
            raise ClosureError("CLOSURE_HISTORY_AUDIT_INVALID") from error
        expected = {
            "segment_index": index,
            "record_count": audit["record_count"],
            "history_head_sha256": audit["history_head_sha256"],
            "source_sha256": audit["source_sha256"],
            "history_record_bytes": audit["history_record_bytes"],
            "history_index_bytes": audit["history_index_bytes"],
            "history_storage_bytes": audit["history_storage_bytes"],
            "audit_sha256": digest_document(audit),
        }
        if retained != expected:
            raise ClosureError("CLOSURE_FINAL_SEGMENT_BINDING_INVALID")
        records_by_segment[index] = records
        _validate_segment_authority_transition(
            segment_index=index,
            records=records,
            previous_records=(
                None if index == 1 else records_by_segment[index - 1]),
        )
        closure_segments.append(expected)
    if (
            sum(segment["record_count"] for segment in closure_segments) !=
            final_audit["sample_count"]):
        raise ClosureError("CLOSURE_FINAL_AUDIT_SAMPLE_COUNT_DRIFT")

    completed = state["completed_iterations"]
    iteration_directories = _iteration_directories(
        artifact_root, final_audit["segment_count"])
    if sorted(iteration_directories) != list(range(1, completed + 1)):
        raise ClosureError("CLOSURE_ITERATION_SET_INVALID")
    receipts_root = artifact_root / "receipts"
    expected_receipt_names = {
        f"decision-{iteration:06d}.json"
        for iteration in range(1, completed + 1)
    }
    try:
        receipt_entries = list(receipts_root.iterdir())
    except OSError as error:
        raise ClosureError("CLOSURE_RECEIPT_DIRECTORY_READ_FAILED") from error
    if (
            any(entry.is_symlink() or not entry.is_file()
                for entry in receipt_entries) or
            {entry.name for entry in receipt_entries} !=
            expected_receipt_names):
        raise ClosureError("CLOSURE_RECEIPT_SET_INVALID")
    decision_events = [
        event for event in state["audit_events"]
        if event.get("event") == "DECISION_RECEIPT_COMMITTED"
    ]
    if len(decision_events) != completed:
        raise ClosureError("CLOSURE_STATE_DECISION_COUNT_INVALID")

    closure_iterations: list[dict[str, Any]] = []
    for iteration in range(1, completed + 1):
        segment_index, directory = iteration_directories[iteration]
        closure_iterations.append(_validate_iteration(
            artifact_root=artifact_root,
            artifact_uid=artifact_uid,
            policy=policy,
            policy_sha256=policy_sha256,
            strategy_path=strategy_path,
            iteration=iteration,
            segment_index=segment_index,
            iteration_directory=directory,
            receipt_path=(
                receipts_root / f"decision-{iteration:06d}.json"),
            records=records_by_segment[segment_index],
            decision_event=decision_events[iteration - 1],
        ))

    strategy_state_path = artifact_root / "strategy-state.json"
    strategy_state_document, strategy_state_contents = _stable_document(
        strategy_state_path,
        "CLOSURE_STRATEGY_STATE",
        artifact_uid=artifact_uid,
        artifact_mode=0o600,
    )
    try:
        validated_strategy_state = shadow_runner._load_state(
            strategy_state_path,
            policy=policy,
            policy_sha256=policy_sha256,
        )
    except ContractError as error:
        raise ClosureError("CLOSURE_STRATEGY_STATE_INVALID") from error
    last_iteration = closure_iterations[-1]
    if (
            validated_strategy_state != strategy_state_document or
            validated_strategy_state["completed_iterations"] != completed or
            validated_strategy_state["last_information_packet_sha256"] !=
            last_iteration["information_packet_file_sha256"] or
            validated_strategy_state["last_receipt_sha256"] !=
            last_iteration["decision_receipt_file_sha256"] or
            state["last_receipt_sha256"] !=
            last_iteration["decision_receipt_file_sha256"]):
        raise ClosureError("CLOSURE_STRATEGY_STATE_BINDING_INVALID")

    try:
        payload_usage = observer._full_payload_usage(artifact_root)
        final_size, final_entry_digest = observer._payload_entry(
            artifact_root, final_path)
    except observer.ObserverError as error:
        raise ClosureError("CLOSURE_PAYLOAD_ACCOUNTING_INVALID") from error
    expected_usage = {
        "bytes": state["accounted_payload_bytes"],
        "files": state["accounted_payload_files"],
        "accumulator": state["accounted_payload_accumulator"],
    }
    if payload_usage != expected_usage:
        raise ClosureError("CLOSURE_PAYLOAD_ACCOUNTING_INVALID")
    if (
            final_audit["payload_bytes_before_final_receipt"] +
            final_size != payload_usage["bytes"] or
            final_audit["payload_files_before_final_receipt"] + 1 !=
            payload_usage["files"] or
            observer._accumulator_add(
                final_audit["payload_accumulator_before_final_receipt"],
                final_entry_digest,
            ) != payload_usage["accumulator"]):
        raise ClosureError("CLOSURE_FINAL_PAYLOAD_BINDING_INVALID")
    final_events = [
        event for event in state["audit_events"]
        if event.get("event") == "FINAL_HISTORY_AUDIT_COMMITTED"
    ]
    if (
            len(final_events) != 1 or
            state["audit_events"][-1] != final_events[0] or
            final_events[0].get("detail", {}).get("receipt_sha256") !=
            digest_bytes(final_contents) or
            final_events[0].get("detail", {}).get("segment_count") !=
            final_audit["segment_count"] or
            final_events[0].get("detail", {}).get("segments") !=
            final_audit["segments"]):
        raise ClosureError("CLOSURE_FINAL_STATE_EVENT_INVALID")
    for event in state["audit_events"]:
        _zero_authority(event, "CLOSURE_STATE_EVENT")

    verified_candidate = (
        time.time_ns() // 1_000_000
        if verified_at_ms is None else
        verified_at_ms
    )
    verified = require_int(
        verified_candidate,
        "CLOSURE_VERIFIED_TIME_INVALID",
        minimum=final_audit["finalized_at_ms"],
    )
    residuals = sorted({
        residual
        for iteration in closure_iterations
        for residual in iteration["residual_evidence"]
    })
    body = {
        "schema": CLOSURE_SCHEMA,
        "version": 1,
        "campaign_id": policy["campaign_id"],
        "campaign_sha256": policy["campaign_sha256"],
        "policy_body_sha256": policy["body_sha256"],
        "policy_file_sha256": policy_sha256,
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "strategy_file_sha256": digest_file(strategy_path),
        "observer_state_body_sha256": state["body_sha256"],
        "observer_state_file_sha256": digest_bytes(state_contents),
        "strategy_state_file_sha256":
            digest_bytes(strategy_state_contents),
        "final_audit_body_sha256": final_audit["body_sha256"],
        "final_audit_file_sha256": digest_bytes(final_contents),
        "verified_at_ms": verified,
        "completed_iterations": completed,
        "maximum_iterations": policy["maximum_iterations"],
        "segment_count": len(closure_segments),
        "segments": closure_segments,
        "iteration_count": len(closure_iterations),
        "iterations": closure_iterations,
        "residual_evidence": residuals,
        "complete_revalidation": False,
        "closure_status": "VERIFIED_WITH_RETAINED_EVIDENCE_RESIDUALS",
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    closure = {**body, "body_sha256": digest_document(body)}
    atomic_write_json(output_absolute, closure, mode=0o600)
    published, published_contents = _stable_document(
        output_absolute,
        "CLOSURE_OUTPUT",
        artifact_uid=os.geteuid(),
        artifact_mode=0o600,
    )
    if (
            published != closure or
            published_contents != canonical_bytes(closure)):
        raise ClosureError("CLOSURE_OUTPUT_PUBLICATION_INVALID")
    return closure


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verified-at-ms", type=int)
    arguments = parser.parse_args()
    try:
        closure = verify_closure(
            artifact_root=arguments.artifact_root,
            policy_path=arguments.policy,
            strategy_path=arguments.strategy,
            output_path=arguments.output,
            verified_at_ms=arguments.verified_at_ms,
        )
    except (
            ClosureError,
            ContractError,
            OSError,
            ValueError,
    ) as error:
        print(
            "hepta_bounded_shadow_closure_verifier: FAIL " + str(error),
            file=sys.stderr,
        )
        return 78
    print(
        "hepta_bounded_shadow_closure_verifier: PASS "
        f"campaign={closure['campaign_id']} "
        f"iterations={closure['completed_iterations']} "
        f"status={closure['closure_status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
