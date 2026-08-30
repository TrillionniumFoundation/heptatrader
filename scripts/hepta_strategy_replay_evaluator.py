#!/usr/bin/env python3

"""Seal and conservatively replay zero-authority SHADOW strategy evidence."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable

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
    require_number,
    require_text,
)
import validate_hepta_strategy_decision_receipt as receipt_validator


DECISION_SET_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "policy_sha256", "policy_body_sha256", "policy",
    "final_audit_receipt_sha256", "final_audit_body_sha256",
    "final_audit_receipt", "strategy_id", "strategy_version",
    "strategy_sha256", "generated_at_ms", "receipt_count", "receipts",
    "mutation_attempted", "direct_broker_access", "live_authorized",
    "body_sha256",
})
RECEIPT_ENVELOPE_FIELDS = frozenset({
    "receipt_sha256", "receipt_file_sha256", "receipt",
})
MARK_SET_FIELDS = frozenset({
    "schema", "version", "campaign_id", "campaign_sha256",
    "policy_sha256", "policy_body_sha256", "policy",
    "final_audit_receipt_sha256", "final_audit_body_sha256",
    "final_audit_receipt", "strategy_id", "strategy_version",
    "strategy_sha256", "instrument", "provider", "source_ref",
    "observed_at_ms", "cadence_ms", "maximum_jitter_ms", "domain_id",
    "agent_uid", "catalog_sha256", "descriptor_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "segment_count", "record_count", "segments", "marks",
    "mutation_attempted", "direct_broker_access", "live_authorized",
    "body_sha256",
})
MARK_FIELDS = frozenset({
    "segment_index", "sequence", "cadence_ms", "maximum_jitter_ms",
    "previous_record_sha256", "record_body_sha256",
    "snapshot_body_sha256", "snapshot_file_sha256", "domain_id",
    "agent_uid", "catalog_sha256", "descriptor_sha256",
    "execution_service_epoch", "execution_service_fencing_generation",
    "watch_generation", "collection_started_at_ms",
    "collection_finished_at_ms", "quote_read_finished_at_ms",
    "observed_at_ms", "stale_after_ms", "bid", "ask", "quote_changed",
    "record", "mark_body_sha256",
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
    "paper_authorized", "live_authorized", "mutation_attempted",
    "direct_broker_access", "body_sha256",
})
FINAL_AUDIT_SEGMENT_FIELDS = frozenset({
    "segment_index", "record_count", "history_head_sha256",
    "source_sha256", "history_record_bytes", "history_index_bytes",
    "history_storage_bytes", "audit_sha256",
})
RESULT_BOUNDARY_FIELDS = (
    "mutation_attempted", "direct_broker_access", "live_authorized",
)
MAXIMUM_REPLAY_RECORDS = 2_000_000
MAXIMUM_RECEIPTS = shadow_runner.MAXIMUM_POLICY_ITERATIONS
MARK_PROVIDER = "HEPTA_AUDITED_IMMUTABLE_SHADOW_HISTORY"
FLOAT_TOLERANCE = 1e-12


def _canonical_document(
    path: Path,
    label: str,
    *,
    maximum_bytes: int,
) -> tuple[dict[str, Any], bytes]:
    document = load_document(path, label, maximum_bytes=maximum_bytes)
    try:
        contents = path.read_bytes()
    except OSError as error:
        raise ContractError(f"{label}_READ_FAILED") from error
    if contents != canonical_bytes(document):
        raise ContractError(f"{label}_CANONICAL_INVALID")
    return document, contents


def _validate_policy(
    policy: dict[str, Any],
    *,
    expected_file_sha256: str,
) -> None:
    require_exact_fields(
        policy,
        shadow_runner.POLICY_FIELDS,
        "REPLAY_POLICY_FIELDS_INVALID",
    )
    if (
            policy["schema"] != shadow_runner.POLICY_SCHEMA or
            policy["version"] != 1):
        raise ContractError("REPLAY_POLICY_SCHEMA_INVALID")
    for field in ("campaign_id", "strategy_id", "strategy_version"):
        require_text(
            policy[field],
            "REPLAY_POLICY_BINDING_INVALID",
            identifier=True,
        )
    for field in ("campaign_sha256", "strategy_sha256", "body_sha256"):
        require_digest(policy[field], "REPLAY_POLICY_DIGEST_INVALID")
    valid_after_ms = require_int(
        policy["valid_after_ms"], "REPLAY_POLICY_TIME_INVALID", minimum=0)
    expires_at_ms = require_int(
        policy["expires_at_ms"],
        "REPLAY_POLICY_TIME_INVALID",
        minimum=valid_after_ms + 1,
    )
    slot_interval_ms = require_int(
        policy["slot_interval_ms"],
        "REPLAY_POLICY_CADENCE_INVALID",
        minimum=shadow_runner.SLOT_INTERVAL_MS,
        maximum=shadow_runner.SLOT_INTERVAL_MS,
    )
    maximum_iterations = require_int(
        policy["maximum_iterations"],
        "REPLAY_POLICY_ITERATIONS_INVALID",
        minimum=1,
        maximum=MAXIMUM_RECEIPTS,
    )
    maximum_lateness_ms = require_int(
        policy["maximum_lateness_ms"],
        "REPLAY_POLICY_LATENESS_INVALID",
        minimum=0,
        maximum=slot_interval_ms - 1,
    )
    require_bool(
        policy["shadow_only"], True, "REPLAY_POLICY_BOUNDARY_INVALID")
    for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access"):
        require_bool(
            policy[field], False, "REPLAY_POLICY_BOUNDARY_INVALID")
    final_slot_ms = (
        valid_after_ms + (maximum_iterations - 1) * slot_interval_ms)
    if final_slot_ms + maximum_lateness_ms >= expires_at_ms:
        raise ContractError("REPLAY_POLICY_WINDOW_INVALID")
    campaign_binding = {
        "schema": shadow_runner.CAMPAIGN_BINDING_SCHEMA,
        "campaign_id": policy["campaign_id"],
        "valid_after_ms": valid_after_ms,
        "expires_at_ms": expires_at_ms,
        "slot_interval_ms": slot_interval_ms,
        "maximum_iterations": maximum_iterations,
        "maximum_lateness_ms": maximum_lateness_ms,
        "shadow_only": True,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    if policy["campaign_sha256"] != digest_document(campaign_binding):
        raise ContractError("REPLAY_POLICY_CAMPAIGN_DIGEST_MISMATCH")
    body = dict(policy)
    claimed_body_sha256 = body.pop("body_sha256")
    if claimed_body_sha256 != digest_document(body):
        raise ContractError("REPLAY_POLICY_BODY_DIGEST_MISMATCH")
    if expected_file_sha256 != digest_bytes(canonical_bytes(policy)):
        raise ContractError("REPLAY_POLICY_FILE_DIGEST_MISMATCH")


def _validate_final_audit(
    audit: dict[str, Any],
    *,
    policy: dict[str, Any],
    policy_sha256: str,
    expected_file_sha256: str,
) -> list[dict[str, Any]]:
    require_exact_fields(
        audit, FINAL_AUDIT_FIELDS, "REPLAY_FINAL_AUDIT_FIELDS_INVALID")
    if (
            audit["schema"] !=
            "hepta.bounded-shadow-final-audit-receipt.v2" or
            audit["version"] != 2):
        raise ContractError("REPLAY_FINAL_AUDIT_SCHEMA_INVALID")
    bindings = {
        "campaign_id": policy["campaign_id"],
        "campaign_sha256": policy["campaign_sha256"],
        "policy_sha256": policy_sha256,
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
        "maximum_iterations": policy["maximum_iterations"],
    }
    if any(audit[field] != value for field, value in bindings.items()):
        raise ContractError("REPLAY_FINAL_AUDIT_BINDING_INVALID")
    completed = require_int(
        audit["completed_iterations"],
        "REPLAY_FINAL_AUDIT_ITERATIONS_INVALID",
        minimum=1,
        maximum=MAXIMUM_RECEIPTS,
    )
    if completed != policy["maximum_iterations"]:
        raise ContractError("REPLAY_FINAL_AUDIT_INCOMPLETE")
    require_int(
        audit["finalized_at_ms"],
        "REPLAY_FINAL_AUDIT_TIME_INVALID",
        minimum=policy["valid_after_ms"],
    )
    segment_count = require_int(
        audit["segment_count"],
        "REPLAY_FINAL_AUDIT_SEGMENTS_INVALID",
        minimum=1,
        maximum=MAXIMUM_REPLAY_RECORDS,
    )
    segments_value = audit["segments"]
    if (
            not isinstance(segments_value, list) or
            len(segments_value) != segment_count):
        raise ContractError("REPLAY_FINAL_AUDIT_SEGMENTS_INVALID")
    segments: list[dict[str, Any]] = []
    retained_sample_count = 0
    for expected_index, value in enumerate(segments_value, start=1):
        segment = require_exact_fields(
            value,
            FINAL_AUDIT_SEGMENT_FIELDS,
            "REPLAY_FINAL_AUDIT_SEGMENT_FIELDS_INVALID",
        )
        if require_int(
                segment["segment_index"],
                "REPLAY_FINAL_AUDIT_SEGMENT_INDEX_INVALID",
                minimum=expected_index,
                maximum=expected_index) != expected_index:
            raise ContractError("REPLAY_FINAL_AUDIT_SEGMENT_INDEX_INVALID")
        record_count = require_int(
            segment["record_count"],
            "REPLAY_FINAL_AUDIT_RECORD_COUNT_INVALID",
            minimum=1,
            maximum=MAXIMUM_REPLAY_RECORDS,
        )
        retained_sample_count += record_count
        for field in (
                "history_head_sha256", "source_sha256", "audit_sha256"):
            require_digest(
                segment[field], "REPLAY_FINAL_AUDIT_DIGEST_INVALID")
        record_bytes = require_int(
            segment["history_record_bytes"],
            "REPLAY_FINAL_AUDIT_SIZE_INVALID",
            minimum=1,
        )
        index_bytes = require_int(
            segment["history_index_bytes"],
            "REPLAY_FINAL_AUDIT_SIZE_INVALID",
            minimum=1,
        )
        storage_bytes = require_int(
            segment["history_storage_bytes"],
            "REPLAY_FINAL_AUDIT_SIZE_INVALID",
            minimum=record_bytes + index_bytes,
            maximum=record_bytes + index_bytes,
        )
        del storage_bytes
        segments.append(segment)
    sample_count = require_int(
        audit["sample_count"],
        "REPLAY_FINAL_AUDIT_SAMPLE_COUNT_INVALID",
        minimum=1,
        maximum=MAXIMUM_REPLAY_RECORDS,
    )
    if sample_count != retained_sample_count:
        raise ContractError("REPLAY_FINAL_AUDIT_SAMPLE_COUNT_DRIFT")
    if (
            require_int(
                audit["missed_sample_count"],
                "REPLAY_FINAL_AUDIT_MISSED_SAMPLE_COUNT_INVALID",
                minimum=0,
            ) != 0 or
            require_int(
                audit["missed_decision_count"],
                "REPLAY_FINAL_AUDIT_MISSED_DECISION_COUNT_INVALID",
                minimum=0,
            ) != 0):
        raise ContractError("REPLAY_FINAL_AUDIT_MISSED_COUNT_NONZERO")
    require_int(
        audit["payload_bytes_before_final_receipt"],
        "REPLAY_FINAL_AUDIT_PAYLOAD_INVALID",
        minimum=0,
    )
    require_int(
        audit["payload_files_before_final_receipt"],
        "REPLAY_FINAL_AUDIT_PAYLOAD_INVALID",
        minimum=0,
    )
    require_digest(
        audit["payload_accumulator_before_final_receipt"],
        "REPLAY_FINAL_AUDIT_PAYLOAD_INVALID",
    )
    for field in (
            "paper_authorized", "live_authorized", "mutation_attempted",
            "direct_broker_access"):
        require_bool(
            audit[field], False, "REPLAY_FINAL_AUDIT_BOUNDARY_INVALID")
    body = dict(audit)
    claimed_body_sha256 = require_digest(
        body.pop("body_sha256"), "REPLAY_FINAL_AUDIT_DIGEST_INVALID")
    if claimed_body_sha256 != digest_document(body):
        raise ContractError("REPLAY_FINAL_AUDIT_BODY_DIGEST_MISMATCH")
    if expected_file_sha256 != digest_bytes(canonical_bytes(audit)):
        raise ContractError("REPLAY_FINAL_AUDIT_FILE_DIGEST_MISMATCH")
    return segments


def _binding_documents(
    policy: dict[str, Any],
    policy_sha256: str,
    audit: dict[str, Any],
    audit_sha256: str,
) -> dict[str, Any]:
    return {
        "campaign_id": policy["campaign_id"],
        "campaign_sha256": policy["campaign_sha256"],
        "policy_sha256": policy_sha256,
        "policy_body_sha256": policy["body_sha256"],
        "policy": policy,
        "final_audit_receipt_sha256": audit_sha256,
        "final_audit_body_sha256": audit["body_sha256"],
        "final_audit_receipt": audit,
        "strategy_id": policy["strategy_id"],
        "strategy_version": policy["strategy_version"],
        "strategy_sha256": policy["strategy_sha256"],
    }


def _load_binding_documents(
    document: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = document["policy"]
    policy_sha256 = require_digest(
        document["policy_sha256"], "REPLAY_POLICY_DIGEST_INVALID")
    _validate_policy(policy, expected_file_sha256=policy_sha256)
    if (
            document["policy_body_sha256"] != policy["body_sha256"] or
            document["campaign_id"] != policy["campaign_id"] or
            document["campaign_sha256"] != policy["campaign_sha256"] or
            document["strategy_id"] != policy["strategy_id"] or
            document["strategy_version"] != policy["strategy_version"] or
            document["strategy_sha256"] != policy["strategy_sha256"]):
        raise ContractError("REPLAY_POLICY_BINDING_INVALID")
    audit = document["final_audit_receipt"]
    audit_sha256 = require_digest(
        document["final_audit_receipt_sha256"],
        "REPLAY_FINAL_AUDIT_DIGEST_INVALID",
    )
    _validate_final_audit(
        audit,
        policy=policy,
        policy_sha256=policy_sha256,
        expected_file_sha256=audit_sha256,
    )
    if document["final_audit_body_sha256"] != audit["body_sha256"]:
        raise ContractError("REPLAY_FINAL_AUDIT_BINDING_INVALID")
    return policy, audit


def seal_decision_set(
    policy_path: Path,
    final_audit_path: Path,
    receipt_paths: Iterable[Path],
) -> dict[str, Any]:
    policy, policy_contents = _canonical_document(
        policy_path, "REPLAY_POLICY", maximum_bytes=65536)
    policy_sha256 = digest_bytes(policy_contents)
    _validate_policy(policy, expected_file_sha256=policy_sha256)
    audit, audit_contents = _canonical_document(
        final_audit_path, "REPLAY_FINAL_AUDIT", maximum_bytes=4 << 20)
    audit_sha256 = digest_bytes(audit_contents)
    _validate_final_audit(
        audit,
        policy=policy,
        policy_sha256=policy_sha256,
        expected_file_sha256=audit_sha256,
    )
    paths = list(receipt_paths)
    if len(paths) != audit["completed_iterations"]:
        raise ContractError("REPLAY_RECEIPT_SET_INCOMPLETE")
    envelopes: list[dict[str, Any]] = []
    for path in paths:
        receipt, contents = _canonical_document(
            path, "REPLAY_DECISION_RECEIPT", maximum_bytes=262144)
        receipt_validator.validate_observation_policy_binding(
            receipt,
            policy_sha256=policy_sha256,
            campaign_sha256=policy["campaign_sha256"],
        )
        if (
                receipt["campaign_id"] != policy["campaign_id"] or
                receipt["strategy_id"] != policy["strategy_id"] or
                receipt["strategy_version"] != policy["strategy_version"] or
                receipt["strategy_sha256"] != policy["strategy_sha256"]):
            raise ContractError("REPLAY_RECEIPT_POLICY_BINDING_INVALID")
        envelopes.append({
            "receipt_sha256": digest_document(receipt),
            "receipt_file_sha256": digest_bytes(contents),
            "receipt": receipt,
        })
    envelopes.sort(
        key=lambda envelope: (
            envelope["receipt"]["started_at_ms"],
            envelope["receipt"]["decision_id"],
        )
    )
    _validate_receipt_envelopes(
        envelopes, policy=policy, audit=audit, policy_sha256=policy_sha256)
    body = {
        "schema": "hepta.strategy-replay-decision-set.v2",
        "version": 2,
        **_binding_documents(
            policy, policy_sha256, audit, audit_sha256),
        "generated_at_ms": audit["finalized_at_ms"],
        "receipt_count": len(envelopes),
        "receipts": envelopes,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "live_authorized": False,
    }
    return {**body, "body_sha256": digest_document(body)}


def _validate_receipt_envelopes(
    envelopes: Any,
    *,
    policy: dict[str, Any],
    audit: dict[str, Any],
    policy_sha256: str,
) -> list[dict[str, Any]]:
    if (
            not isinstance(envelopes, list) or
            len(envelopes) != audit["completed_iterations"]):
        raise ContractError("REPLAY_RECEIPT_SET_INCOMPLETE")
    receipts: list[dict[str, Any]] = []
    seen_decisions: set[str] = set()
    seen_receipts: set[str] = set()
    previous_finished = -1
    for iteration, value in enumerate(envelopes, start=1):
        envelope = require_exact_fields(
            value,
            RECEIPT_ENVELOPE_FIELDS,
            "REPLAY_RECEIPT_ENVELOPE_FIELDS_INVALID",
        )
        receipt = envelope["receipt"]
        receipt_sha256 = require_digest(
            envelope["receipt_sha256"], "REPLAY_RECEIPT_DIGEST_INVALID")
        file_sha256 = require_digest(
            envelope["receipt_file_sha256"],
            "REPLAY_RECEIPT_DIGEST_INVALID",
        )
        canonical_sha256 = digest_bytes(canonical_bytes(receipt))
        if (
                receipt_sha256 != digest_document(receipt) or
                file_sha256 != canonical_sha256):
            raise ContractError("REPLAY_RECEIPT_DIGEST_MISMATCH")
        if receipt_sha256 in seen_receipts:
            raise ContractError("REPLAY_RECEIPT_DUPLICATE")
        seen_receipts.add(receipt_sha256)
        receipt_validator.validate_observation_policy_binding(
            receipt,
            policy_sha256=policy_sha256,
            campaign_sha256=policy["campaign_sha256"],
        )
        if (
                receipt["campaign_id"] != policy["campaign_id"] or
                receipt["strategy_id"] != policy["strategy_id"] or
                receipt["strategy_version"] != policy["strategy_version"] or
                receipt["strategy_sha256"] != policy["strategy_sha256"]):
            raise ContractError("REPLAY_RECEIPT_POLICY_BINDING_INVALID")
        decision_id = receipt["decision_id"]
        if decision_id in seen_decisions:
            raise ContractError("REPLAY_DECISION_ID_DUPLICATE")
        seen_decisions.add(decision_id)
        scheduled_at_ms = (
            policy["valid_after_ms"] +
            (iteration - 1) * policy["slot_interval_ms"])
        if not (
                scheduled_at_ms <= receipt["started_at_ms"] <=
                scheduled_at_ms + policy["maximum_lateness_ms"]):
            raise ContractError("REPLAY_RECEIPT_SCHEDULE_BINDING_INVALID")
        if (
                receipt["finished_at_ms"] < receipt["started_at_ms"] or
                receipt["started_at_ms"] <= previous_finished or
                receipt["finished_at_ms"] > audit["finalized_at_ms"]):
            raise ContractError("REPLAY_RECEIPT_TIME_INVALID")
        previous_finished = receipt["finished_at_ms"]
        receipts.append(receipt)
    return receipts


def _validate_decision_set(
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    require_exact_fields(
        document, DECISION_SET_FIELDS, "REPLAY_DECISION_SET_FIELDS_INVALID")
    if (
            document["schema"] !=
            "hepta.strategy-replay-decision-set.v2" or
            document["version"] != 2):
        raise ContractError("REPLAY_DECISION_SET_SCHEMA_INVALID")
    for field in RESULT_BOUNDARY_FIELDS:
        require_bool(
            document[field], False, "REPLAY_DECISION_SET_BOUNDARY_INVALID")
    body = dict(document)
    claimed = require_digest(
        body.pop("body_sha256"), "REPLAY_DECISION_SET_DIGEST_INVALID")
    if claimed != digest_document(body):
        raise ContractError("REPLAY_DECISION_SET_DIGEST_MISMATCH")
    policy, audit = _load_binding_documents(document)
    require_int(
        document["generated_at_ms"],
        "REPLAY_DECISION_SET_TIME_INVALID",
        minimum=audit["finalized_at_ms"],
        maximum=audit["finalized_at_ms"],
    )
    if document["receipt_count"] != audit["completed_iterations"]:
        raise ContractError("REPLAY_RECEIPT_SET_INCOMPLETE")
    return _validate_receipt_envelopes(
        document["receipts"],
        policy=policy,
        audit=audit,
        policy_sha256=document["policy_sha256"],
    )


def _history_source_digest(digests: list[str]) -> str:
    return digest_document({
        "schema": "hepta.shadow-market-bar-source.v1",
        "source_kind": "SHADOW_HISTORY_RECORDS",
        "digests": digests,
    })


def _mark_from_record(
    record: dict[str, Any],
    *,
    segment_index: int,
) -> dict[str, Any]:
    require_exact_fields(
        record,
        market_history.RECORD_FIELDS,
        "REPLAY_MARK_RECORD_FIELDS_INVALID",
    )
    if (
            record.get("schema") !=
            "hepta.shadow-market-history-record.v3" or
            record.get("version") != 3):
        raise ContractError("REPLAY_MARK_RECORD_SCHEMA_INVALID")
    quote = record["quote"]
    body = {
        "segment_index": segment_index,
        "sequence": record["sequence"],
        "cadence_ms": record["cadence_ms"],
        "maximum_jitter_ms": record["maximum_jitter_ms"],
        "previous_record_sha256": record["previous_record_sha256"],
        "record_body_sha256": record["record_sha256"],
        "snapshot_body_sha256": record["snapshot_body_sha256"],
        "snapshot_file_sha256": record["snapshot_file_sha256"],
        "domain_id": record["domain_id"],
        "agent_uid": record["agent_uid"],
        "catalog_sha256": record["catalog_sha256"],
        "descriptor_sha256": record["descriptor_sha256"],
        "execution_service_epoch": record["execution_service_epoch"],
        "execution_service_fencing_generation":
            record["execution_service_fencing_generation"],
        "watch_generation": record["watch_generation"],
        "collection_started_at_ms": record["collection_started_at_ms"],
        "collection_finished_at_ms": record["collection_finished_at_ms"],
        "quote_read_finished_at_ms": record["quote_read_finished_at_ms"],
        "quote_changed": record["quote_changed"],
        "observed_at_ms": quote["observed_at_ms"],
        "stale_after_ms": quote["stale_after_ms"],
        "bid": quote["bid"],
        "ask": quote["ask"],
        "record": record,
    }
    return {**body, "mark_body_sha256": digest_document(body)}


def _audit_matches_segment(
    audit: dict[str, Any],
    segment: dict[str, Any],
) -> bool:
    return (
        audit.get("status") == "valid" and
        segment["record_count"] == audit.get("record_count") and
        segment["history_head_sha256"] ==
        audit.get("history_head_sha256") and
        segment["source_sha256"] == audit.get("source_sha256") and
        segment["history_record_bytes"] ==
        audit.get("history_record_bytes") and
        segment["history_index_bytes"] ==
        audit.get("history_index_bytes") and
        segment["history_storage_bytes"] ==
        audit.get("history_storage_bytes") and
        segment["audit_sha256"] == digest_document(audit)
    )


def seal_mark_set(
    policy_path: Path,
    final_audit_path: Path,
    history_directories: Iterable[Path],
    *,
    cadence_ms: int,
    maximum_jitter_ms: int,
) -> dict[str, Any]:
    cadence = require_int(
        cadence_ms,
        "REPLAY_MARK_CADENCE_INVALID",
        minimum=market_history.MIN_CADENCE_MS,
        maximum=market_history.MAX_CADENCE_MS,
    )
    jitter = require_int(
        maximum_jitter_ms,
        "REPLAY_MARK_JITTER_INVALID",
        minimum=0,
        maximum=market_history.MAXIMUM_JITTER_MS,
    )
    if jitter >= cadence:
        raise ContractError("REPLAY_MARK_JITTER_INVALID")
    policy, policy_contents = _canonical_document(
        policy_path, "REPLAY_POLICY", maximum_bytes=65536)
    policy_sha256 = digest_bytes(policy_contents)
    _validate_policy(policy, expected_file_sha256=policy_sha256)
    final_audit, final_contents = _canonical_document(
        final_audit_path, "REPLAY_FINAL_AUDIT", maximum_bytes=4 << 20)
    final_sha256 = digest_bytes(final_contents)
    expected_segments = _validate_final_audit(
        final_audit,
        policy=policy,
        policy_sha256=policy_sha256,
        expected_file_sha256=final_sha256,
    )
    directories = list(history_directories)
    if len(directories) != len(expected_segments):
        raise ContractError("REPLAY_MARK_HISTORY_SEGMENT_SET_INVALID")
    marks: list[dict[str, Any]] = []
    for expected_segment, directory in zip(expected_segments, directories):
        try:
            full_audit = market_history.audit_history(
                directory,
                cadence_ms=cadence,
                maximum_jitter_ms=jitter,
            )
            records = market_history.load_history(
                directory,
                cadence_ms=cadence,
                maximum_jitter_ms=jitter,
            )
        except market_history.HistoryError as error:
            raise ContractError("REPLAY_MARK_HISTORY_AUDIT_FAILED") from error
        if not _audit_matches_segment(full_audit, expected_segment):
            raise ContractError("REPLAY_MARK_HISTORY_AUDIT_BINDING_INVALID")
        if len(records) != expected_segment["record_count"]:
            raise ContractError("REPLAY_MARK_HISTORY_RECORD_SET_INVALID")
        marks.extend(
            _mark_from_record(
                record, segment_index=expected_segment["segment_index"])
            for record in records
        )
    if not marks:
        raise ContractError("REPLAY_MARKS_EMPTY")
    first = marks[0]
    source_ref = digest_document({
        "schema": "hepta.strategy-replay-market-source.v1",
        "segments": [{
            "segment_index": segment["segment_index"],
            "source_sha256": segment["source_sha256"],
        } for segment in expected_segments],
    })
    body = {
        "schema": "hepta.authoritative-replay-marks.v3",
        "version": 3,
        **_binding_documents(
            policy, policy_sha256, final_audit, final_sha256),
        "instrument": "EUR.USD",
        "provider": MARK_PROVIDER,
        "source_ref": source_ref,
        "observed_at_ms": marks[-1]["observed_at_ms"],
        "cadence_ms": cadence,
        "maximum_jitter_ms": jitter,
        "domain_id": first["domain_id"],
        "agent_uid": first["agent_uid"],
        "catalog_sha256": first["catalog_sha256"],
        "descriptor_sha256": first["descriptor_sha256"],
        "execution_service_epoch": first["execution_service_epoch"],
        "execution_service_fencing_generation":
            first["execution_service_fencing_generation"],
        "segment_count": len(expected_segments),
        "record_count": len(marks),
        "segments": expected_segments,
        "marks": marks,
        "mutation_attempted": False,
        "direct_broker_access": False,
        "live_authorized": False,
    }
    document = {**body, "body_sha256": digest_document(body)}
    _validate_marks(document)
    return document


def _validate_mark(
    value: Any,
    *,
    document: dict[str, Any],
) -> dict[str, float | int | str | None]:
    mark = require_exact_fields(
        value, MARK_FIELDS, "REPLAY_MARK_FIELDS_INVALID")
    body = dict(mark)
    claimed = require_digest(
        body.pop("mark_body_sha256"), "REPLAY_MARK_BODY_DIGEST_INVALID")
    if claimed != digest_document(body):
        raise ContractError("REPLAY_MARK_BODY_DIGEST_MISMATCH")
    record = require_exact_fields(
        mark["record"],
        market_history.RECORD_FIELDS,
        "REPLAY_MARK_RECORD_FIELDS_INVALID",
    )
    if (
            record["schema"] !=
            "hepta.shadow-market-history-record.v3" or
            record["version"] != 3):
        raise ContractError("REPLAY_MARK_RECORD_SCHEMA_INVALID")
    record_body = dict(record)
    record_claimed = require_digest(
        record_body.pop("record_sha256"),
        "REPLAY_MARK_RECORD_DIGEST_INVALID",
    )
    if record_claimed != digest_document(record_body):
        raise ContractError("REPLAY_MARK_RECORD_DIGEST_MISMATCH")
    quote = require_exact_fields(
        record["quote"],
        market_history.QUOTE_FIELDS,
        "REPLAY_MARK_RECORD_QUOTE_FIELDS_INVALID",
    )
    if (
            quote["source"] != "SIMULATOR" or
            quote["authoritative"] is not True or
            quote["stale"] is not False):
        raise ContractError("REPLAY_MARK_RECORD_QUOTE_INVALID")
    record_bindings = {
        "sequence": "sequence",
        "cadence_ms": "cadence_ms",
        "maximum_jitter_ms": "maximum_jitter_ms",
        "previous_record_sha256": "previous_record_sha256",
        "record_body_sha256": "record_sha256",
        "snapshot_body_sha256": "snapshot_body_sha256",
        "snapshot_file_sha256": "snapshot_file_sha256",
        "domain_id": "domain_id",
        "agent_uid": "agent_uid",
        "catalog_sha256": "catalog_sha256",
        "descriptor_sha256": "descriptor_sha256",
        "execution_service_epoch": "execution_service_epoch",
        "execution_service_fencing_generation":
            "execution_service_fencing_generation",
        "watch_generation": "watch_generation",
        "collection_started_at_ms": "collection_started_at_ms",
        "collection_finished_at_ms": "collection_finished_at_ms",
        "quote_read_finished_at_ms": "quote_read_finished_at_ms",
        "quote_changed": "quote_changed",
    }
    if any(
            mark[mark_field] != record[record_field]
            for mark_field, record_field in record_bindings.items()):
        raise ContractError("REPLAY_MARK_RECORD_BINDING_INVALID")
    quote_bindings = {
        "observed_at_ms": "observed_at_ms",
        "stale_after_ms": "stale_after_ms",
        "bid": "bid",
        "ask": "ask",
    }
    if any(
            mark[mark_field] != quote[quote_field]
            for mark_field, quote_field in quote_bindings.items()):
        raise ContractError("REPLAY_MARK_RECORD_BINDING_INVALID")
    for field in (
            "record_body_sha256", "snapshot_body_sha256",
            "snapshot_file_sha256", "catalog_sha256"):
        require_digest(mark[field], "REPLAY_MARK_SOURCE_DIGEST_INVALID")
    previous = mark["previous_record_sha256"]
    if previous is not None:
        require_digest(previous, "REPLAY_MARK_SOURCE_DIGEST_INVALID")
    descriptors = mark["descriptor_sha256"]
    if (
            not isinstance(descriptors, dict) or
            set(descriptors) != set(market_history.READ_ORDER)):
        raise ContractError("REPLAY_MARK_DESCRIPTOR_SET_INVALID")
    for digest in descriptors.values():
        require_digest(digest, "REPLAY_MARK_SOURCE_DIGEST_INVALID")
    for field in ("domain_id", "execution_service_epoch"):
        require_text(mark[field], "REPLAY_MARK_AUTHORITY_INVALID",
                     identifier=True)
    require_int(
        mark["agent_uid"], "REPLAY_MARK_AUTHORITY_INVALID", minimum=1)
    require_int(
        mark["execution_service_fencing_generation"],
        "REPLAY_MARK_AUTHORITY_INVALID",
        minimum=1,
    )
    require_int(
        mark["watch_generation"],
        "REPLAY_MARK_WATCH_GENERATION_INVALID",
        minimum=1,
    )
    require_int(
        mark["cadence_ms"],
        "REPLAY_MARK_CADENCE_INVALID",
        minimum=document["cadence_ms"],
        maximum=document["cadence_ms"],
    )
    require_int(
        mark["maximum_jitter_ms"],
        "REPLAY_MARK_JITTER_INVALID",
        minimum=document["maximum_jitter_ms"],
        maximum=document["maximum_jitter_ms"],
    )
    started = require_int(
        mark["collection_started_at_ms"],
        "REPLAY_MARK_TIME_INVALID",
        minimum=0,
    )
    finished = require_int(
        mark["collection_finished_at_ms"],
        "REPLAY_MARK_TIME_INVALID",
        minimum=started,
    )
    quote_read = require_int(
        mark["quote_read_finished_at_ms"],
        "REPLAY_MARK_TIME_INVALID",
        minimum=started,
        maximum=finished,
    )
    observed = require_int(
        mark["observed_at_ms"],
        "REPLAY_MARK_TIME_INVALID",
        minimum=0,
        maximum=quote_read,
    )
    require_int(
        mark["stale_after_ms"],
        "REPLAY_MARK_TIME_INVALID",
        minimum=quote_read,
        maximum=observed + 60_000,
    )
    bid = require_number(
        mark["bid"], "REPLAY_MARK_QUOTE_INVALID", positive=True)
    ask = require_number(
        mark["ask"], "REPLAY_MARK_QUOTE_INVALID", positive=True)
    if bid > ask:
        raise ContractError("REPLAY_MARK_QUOTE_INVALID")
    if not isinstance(mark["quote_changed"], bool):
        raise ContractError("REPLAY_MARK_QUOTE_CHANGE_FLAG_INVALID")
    authority_fields = (
        "domain_id", "agent_uid", "catalog_sha256", "descriptor_sha256",
        "execution_service_epoch", "execution_service_fencing_generation",
    )
    if any(mark[field] != document[field] for field in authority_fields):
        raise ContractError("REPLAY_MARK_AUTHORITY_CONTINUITY_INVALID")
    return {
        **mark,
        "bid": bid,
        "ask": ask,
        "mid": (bid + ask) / 2.0,
        "observed_at_ms": observed,
        "collection_started_at_ms": started,
    }


def _validate_marks(
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    require_exact_fields(
        document, MARK_SET_FIELDS, "REPLAY_MARK_SET_FIELDS_INVALID")
    if (
            document["schema"] != "hepta.authoritative-replay-marks.v3" or
            document["version"] != 3 or
            document["instrument"] != "EUR.USD" or
            document["provider"] != MARK_PROVIDER):
        raise ContractError("REPLAY_MARK_SET_SCHEMA_INVALID")
    for field in RESULT_BOUNDARY_FIELDS:
        require_bool(
            document[field], False, "REPLAY_MARK_SET_BOUNDARY_INVALID")
    top_body = dict(document)
    top_claimed = require_digest(
        top_body.pop("body_sha256"), "REPLAY_MARK_SET_DIGEST_INVALID")
    if top_claimed != digest_document(top_body):
        raise ContractError("REPLAY_MARK_SET_DIGEST_MISMATCH")
    policy, final_audit = _load_binding_documents(document)
    del policy
    cadence = require_int(
        document["cadence_ms"],
        "REPLAY_MARK_CADENCE_INVALID",
        minimum=market_history.MIN_CADENCE_MS,
        maximum=market_history.MAX_CADENCE_MS,
    )
    jitter = require_int(
        document["maximum_jitter_ms"],
        "REPLAY_MARK_JITTER_INVALID",
        minimum=0,
        maximum=market_history.MAXIMUM_JITTER_MS,
    )
    if jitter >= cadence:
        raise ContractError("REPLAY_MARK_JITTER_INVALID")
    for field in ("domain_id", "execution_service_epoch"):
        require_text(
            document[field], "REPLAY_MARK_AUTHORITY_INVALID",
            identifier=True,
        )
    require_int(
        document["agent_uid"], "REPLAY_MARK_AUTHORITY_INVALID", minimum=1)
    require_digest(
        document["catalog_sha256"], "REPLAY_MARK_SOURCE_DIGEST_INVALID")
    descriptors = document["descriptor_sha256"]
    if (
            not isinstance(descriptors, dict) or
            set(descriptors) != set(market_history.READ_ORDER)):
        raise ContractError("REPLAY_MARK_DESCRIPTOR_SET_INVALID")
    for digest in descriptors.values():
        require_digest(digest, "REPLAY_MARK_SOURCE_DIGEST_INVALID")
    require_int(
        document["execution_service_fencing_generation"],
        "REPLAY_MARK_AUTHORITY_INVALID",
        minimum=1,
    )
    segment_count = require_int(
        document["segment_count"],
        "REPLAY_MARK_SEGMENT_COUNT_INVALID",
        minimum=1,
    )
    if (
            segment_count != final_audit["segment_count"] or
            document["segments"] != final_audit["segments"]):
        raise ContractError("REPLAY_MARK_FINAL_AUDIT_BINDING_INVALID")
    record_count = require_int(
        document["record_count"],
        "REPLAY_MARK_RECORD_COUNT_INVALID",
        minimum=1,
        maximum=MAXIMUM_REPLAY_RECORDS,
    )
    values = document["marks"]
    if not isinstance(values, list) or len(values) != record_count:
        raise ContractError("REPLAY_MARK_RECORD_COUNT_INVALID")

    marks: list[dict[str, Any]] = []
    by_segment: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(1, segment_count + 1)}
    previous_global: dict[str, Any] | None = None
    lower_gap = cadence - jitter
    upper_gap = cadence + jitter
    maximum_quote_gap = market_history.BUILDER_MAXIMUM_QUOTE_GAP_MS
    for value in values:
        mark = _validate_mark(value, document=document)
        segment_index = require_int(
            mark["segment_index"],
            "REPLAY_MARK_SEGMENT_INDEX_INVALID",
            minimum=1,
            maximum=segment_count,
        )
        sequence = require_int(
            mark["sequence"],
            "REPLAY_MARK_SEQUENCE_INVALID",
            minimum=1,
            maximum=MAXIMUM_REPLAY_RECORDS,
        )
        segment_marks = by_segment[segment_index]
        expected_sequence = len(segment_marks) + 1
        if sequence != expected_sequence:
            raise ContractError("REPLAY_MARK_SEQUENCE_INVALID")
        expected_previous = (
            None if not segment_marks else
            segment_marks[-1]["record_body_sha256"])
        if mark["previous_record_sha256"] != expected_previous:
            raise ContractError("REPLAY_MARK_CHAIN_INVALID")
        if previous_global is None:
            if mark["quote_changed"] is not True:
                raise ContractError(
                    "REPLAY_MARK_QUOTE_CHANGE_FLAG_INVALID")
        else:
            if segment_index < previous_global["segment_index"]:
                raise ContractError("REPLAY_MARK_SEGMENT_ORDER_INVALID")
            capture_gap = (
                mark["collection_started_at_ms"] -
                previous_global["collection_started_at_ms"])
            quote_gap = (
                mark["observed_at_ms"] -
                previous_global["observed_at_ms"])
            if (
                    not lower_gap <= capture_gap <= upper_gap or
                    (
                        mark["quote_changed"] is True and
                        not 1 <= quote_gap <= maximum_quote_gap
                    ) or
                    (
                        mark["quote_changed"] is False and
                        (
                            quote_gap != 0 or
                            any(
                                mark[field] != previous_global[field]
                                for field in (
                                    "observed_at_ms", "stale_after_ms",
                                    "bid", "ask",
                                )
                            )
                        )
                    )):
                raise ContractError("REPLAY_MARK_CADENCE_CONTINUITY_INVALID")
        segment_marks.append(mark)
        marks.append(mark)
        previous_global = mark

    segments = final_audit["segments"]
    for segment in segments:
        segment_marks = by_segment[segment["segment_index"]]
        if len(segment_marks) != segment["record_count"]:
            raise ContractError("REPLAY_MARK_SEGMENT_RECORD_COUNT_INVALID")
        record_digests = [
            mark["record_body_sha256"] for mark in segment_marks]
        if (
                segment["history_head_sha256"] != record_digests[-1] or
                segment["source_sha256"] !=
                _history_source_digest(record_digests)):
            raise ContractError("REPLAY_MARK_SEGMENT_SOURCE_MISMATCH")
    expected_source_ref = digest_document({
        "schema": "hepta.strategy-replay-market-source.v1",
        "segments": [{
            "segment_index": segment["segment_index"],
            "source_sha256": segment["source_sha256"],
        } for segment in segments],
    })
    if document["source_ref"] != expected_source_ref:
        raise ContractError("REPLAY_MARK_SOURCE_REF_MISMATCH")
    if document["observed_at_ms"] != marks[-1]["observed_at_ms"]:
        raise ContractError("REPLAY_MARK_SET_TIME_INVALID")
    return marks


def _maximum_drawdown(values: list[float]) -> float:
    peak = 0.0
    cumulative = 0.0
    maximum = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        maximum = max(maximum, peak - cumulative)
    return maximum


def _effective_holding_ms(
    intent: dict[str, Any],
    configured_seconds: int | None,
) -> int:
    intent_holding = require_int(
        intent["max_holding_ms"],
        "REPLAY_INTENT_HOLDING_INVALID",
        minimum=1,
        maximum=86_400_000,
    )
    if configured_seconds is None:
        return intent_holding
    configured = require_int(
        configured_seconds,
        "REPLAY_MAXIMUM_HOLDING_INVALID",
        minimum=1,
        maximum=86_400,
    ) * 1000
    return min(intent_holding, configured)


def _reject_overlapping_intents(
    receipts: list[dict[str, Any]],
    *,
    entry_latency_ms: int,
    maximum_holding_seconds: int | None,
) -> None:
    windows: list[tuple[int, int, str]] = []
    for receipt in receipts:
        if receipt["decision"] != "TRADE":
            continue
        intent = receipt["trade_intent"]
        earliest_entry = intent["observed_at_ms"] + entry_latency_ms
        if earliest_entry >= intent["expires_at_ms"]:
            continue
        latest_exit = (
            intent["expires_at_ms"] +
            _effective_holding_ms(intent, maximum_holding_seconds))
        windows.append((earliest_entry, latest_exit, receipt["decision_id"]))
    windows.sort()
    for previous, candidate in zip(windows, windows[1:]):
        if candidate[0] <= previous[1]:
            raise ContractError(
                "REPLAY_OVERLAPPING_INTENTS_REJECTED:"
                f"{previous[2]}:{candidate[2]}")


def _entry_price(
    mark: dict[str, Any],
    intent: dict[str, Any],
    *,
    entry_slippage_bps: float,
) -> tuple[float, float] | None:
    side = intent["side"]
    touch = float(mark["ask"] if side == "BUY" else mark["bid"])
    configured_slippage = touch * entry_slippage_bps / 10_000.0
    intent_slippage = require_number(
        intent["expected_slippage"],
        "REPLAY_INTENT_SLIPPAGE_INVALID",
        minimum=0.0,
    )
    slippage = max(configured_slippage, intent_slippage)
    price = touch + slippage if side == "BUY" else touch - slippage
    limit = float(intent["limit_price"])
    if (
            (side == "BUY" and price > limit + FLOAT_TOLERANCE) or
            (side == "SELL" and price < limit - FLOAT_TOLERANCE)):
        return None
    return price, slippage


def _exit_price(
    mark: dict[str, Any],
    side: str,
    *,
    exit_slippage_bps: float,
) -> tuple[float, float]:
    touch = float(mark["bid"] if side == "BUY" else mark["ask"])
    slippage = touch * exit_slippage_bps / 10_000.0
    price = touch - slippage if side == "BUY" else touch + slippage
    if price <= 0.0:
        raise ContractError("REPLAY_EXIT_PRICE_INVALID")
    return price, slippage


def _unfilled_result(
    receipt: dict[str, Any],
    *,
    status: str,
    eligible_entry_at_ms: int,
    entry_expiry_at_ms: int,
    evidence_complete: bool,
) -> dict[str, Any]:
    intent = receipt["trade_intent"]
    return {
        "decision_id": receipt["decision_id"],
        "intent_id": intent["intent_id"],
        "side": intent["side"],
        "status": status,
        "eligible_entry_at_ms": eligible_entry_at_ms,
        "entry_expiry_at_ms": entry_expiry_at_ms,
        "entry_at_ms": None,
        "entry_price": None,
        "entry_touch_price": None,
        "entry_slippage": None,
        "limit_price": intent["limit_price"],
        "target_at_ms": None,
        "maximum_holding_at_ms": None,
        "maximum_exit_at_ms": None,
        "exit_at_ms": None,
        "exit_price": None,
        "exit_touch_price": None,
        "exit_slippage": None,
        "exit_reason": None,
        "holding_ms": None,
        "gross_return_bps": None,
        "net_return_bps": None,
        "mae_bps": None,
        "mfe_bps": None,
        "filled": False,
        "resolved": False,
        "evidence_complete": evidence_complete,
    }


def evaluate_replay(
    decisions_document: dict[str, Any],
    marks_document: dict[str, Any],
    *,
    horizon_seconds: int,
    round_trip_cost_bps: float,
    maximum_exit_delay_seconds: int = 30,
    entry_latency_ms: int = 1_000,
    entry_slippage_bps: float = 0.2,
    exit_slippage_bps: float = 0.2,
    maximum_holding_seconds: int | None = None,
    allow_overlapping_intents: bool = False,
) -> dict[str, Any]:
    horizon = require_int(
        horizon_seconds, "REPLAY_HORIZON_INVALID",
        minimum=1, maximum=86_400)
    cost = require_number(
        round_trip_cost_bps, "REPLAY_COST_INVALID",
        minimum=0.0, maximum=1000.0)
    exit_delay_seconds = require_int(
        maximum_exit_delay_seconds, "REPLAY_EXIT_DELAY_INVALID",
        minimum=0, maximum=3600)
    latency = require_int(
        entry_latency_ms, "REPLAY_ENTRY_LATENCY_INVALID",
        minimum=0, maximum=3_600_000)
    entry_slippage = require_number(
        entry_slippage_bps, "REPLAY_ENTRY_SLIPPAGE_INVALID",
        minimum=0.0, maximum=1000.0)
    exit_slippage = require_number(
        exit_slippage_bps, "REPLAY_EXIT_SLIPPAGE_INVALID",
        minimum=0.0, maximum=1000.0)
    if not isinstance(allow_overlapping_intents, bool):
        raise ContractError("REPLAY_OVERLAP_POLICY_INVALID")
    if maximum_holding_seconds is not None:
        require_int(
            maximum_holding_seconds,
            "REPLAY_MAXIMUM_HOLDING_INVALID",
            minimum=1,
            maximum=86_400,
        )
    receipts = _validate_decision_set(decisions_document)
    marks = _validate_marks(marks_document)
    binding_fields = (
        "campaign_id", "campaign_sha256", "policy_sha256",
        "policy_body_sha256", "final_audit_receipt_sha256",
        "final_audit_body_sha256", "strategy_id", "strategy_version",
        "strategy_sha256",
    )
    if any(
            decisions_document[field] != marks_document[field]
            for field in binding_fields):
        raise ContractError("REPLAY_EVIDENCE_SET_BINDING_MISMATCH")
    if not allow_overlapping_intents:
        _reject_overlapping_intents(
            receipts,
            entry_latency_ms=latency,
            maximum_holding_seconds=maximum_holding_seconds,
        )
    mark_times = [int(mark["observed_at_ms"]) for mark in marks]

    results: list[dict[str, Any]] = []
    buy_count = 0
    sell_count = 0
    for receipt in receipts:
        if receipt["decision"] != "TRADE":
            continue
        intent = receipt["trade_intent"]
        side = intent["side"]
        buy_count += int(side == "BUY")
        sell_count += int(side == "SELL")
        eligible_at_ms = int(intent["observed_at_ms"]) + latency
        expires_at_ms = int(intent["expires_at_ms"])
        if eligible_at_ms >= expires_at_ms:
            results.append(_unfilled_result(
                receipt,
                status="NOT_FILLED_LATENCY_EXCEEDED_EXPIRY",
                eligible_entry_at_ms=eligible_at_ms,
                entry_expiry_at_ms=expires_at_ms,
                evidence_complete=True,
            ))
            continue
        entry_index = bisect_left(mark_times, eligible_at_ms)
        fill: tuple[int, float, float] | None = None
        cursor = entry_index
        while cursor < len(marks) and mark_times[cursor] < expires_at_ms:
            candidate = _entry_price(
                marks[cursor],
                intent,
                entry_slippage_bps=entry_slippage,
            )
            if candidate is not None:
                fill = (cursor, candidate[0], candidate[1])
                break
            cursor += 1
        if fill is None:
            evidence_complete = (
                bool(mark_times) and mark_times[-1] >= expires_at_ms)
            results.append(_unfilled_result(
                receipt,
                status=(
                    "NOT_FILLED_EXPIRED" if evidence_complete else
                    "ENTRY_EVIDENCE_INCOMPLETE"
                ),
                eligible_entry_at_ms=eligible_at_ms,
                entry_expiry_at_ms=expires_at_ms,
                evidence_complete=evidence_complete,
            ))
            continue

        entry_index, entry_price, entry_slippage_amount = fill
        entry_mark = marks[entry_index]
        entry_at_ms = mark_times[entry_index]
        effective_holding_ms = _effective_holding_ms(
            intent, maximum_holding_seconds)
        horizon_at_ms = entry_at_ms + horizon * 1000
        hard_holding_at_ms = entry_at_ms + effective_holding_ms
        target_at_ms = min(horizon_at_ms, hard_holding_at_ms)
        target_reason = (
            "MAXIMUM_HOLDING" if hard_holding_at_ms <= horizon_at_ms else
            "HORIZON"
        )
        maximum_exit_at_ms = min(
            target_at_ms + exit_delay_seconds * 1000,
            hard_holding_at_ms,
        )
        stop_distance = require_number(
            intent["max_adverse_move"],
            "REPLAY_INTENT_STOP_INVALID",
            minimum=0.0,
        )
        stop_price = (
            entry_price - stop_distance
            if side == "BUY" else entry_price + stop_distance)
        selected: tuple[int, float, float, str] | None = None
        path_prices: list[tuple[int, float]] = []
        cursor = entry_index
        while cursor < len(marks):
            mark_at_ms = mark_times[cursor]
            if mark_at_ms > maximum_exit_at_ms:
                break
            executable, exit_slip_amount = _exit_price(
                marks[cursor], side, exit_slippage_bps=exit_slippage)
            path_prices.append((cursor, executable))
            stop_hit = (
                executable <= stop_price
                if side == "BUY" else executable >= stop_price)
            if stop_hit:
                selected = (
                    cursor, executable, exit_slip_amount,
                    "STOP_INVALIDATION",
                )
                break
            if mark_at_ms >= target_at_ms:
                selected = (
                    cursor, executable, exit_slip_amount, target_reason)
                break
            cursor += 1

        if (
                selected is None and
                target_reason == "MAXIMUM_HOLDING"):
            coverage_index = bisect_left(mark_times, hard_holding_at_ms)
            if coverage_index < len(marks):
                last_at_or_before = bisect_right(
                    mark_times, hard_holding_at_ms) - 1
                if last_at_or_before >= entry_index:
                    candidate_mark = marks[last_at_or_before]
                    candidate_price, candidate_slip = _exit_price(
                        candidate_mark,
                        side,
                        exit_slippage_bps=exit_slippage,
                    )
                    if (
                            hard_holding_at_ms -
                            mark_times[last_at_or_before] <=
                            marks_document["cadence_ms"] +
                            marks_document["maximum_jitter_ms"]):
                        selected = (
                            last_at_or_before,
                            candidate_price,
                            candidate_slip,
                            "MAXIMUM_HOLDING",
                        )
                        if not any(
                                item[0] == last_at_or_before
                                for item in path_prices):
                            path_prices.append(
                                (last_at_or_before, candidate_price))

        entry_touch = float(
            entry_mark["ask"] if side == "BUY" else entry_mark["bid"])
        base_result = {
            "decision_id": receipt["decision_id"],
            "intent_id": intent["intent_id"],
            "side": side,
            "eligible_entry_at_ms": eligible_at_ms,
            "entry_expiry_at_ms": expires_at_ms,
            "entry_at_ms": entry_at_ms,
            "entry_price": entry_price,
            "entry_touch_price": entry_touch,
            "entry_slippage": entry_slippage_amount,
            "limit_price": intent["limit_price"],
            "target_at_ms": target_at_ms,
            "maximum_holding_at_ms": hard_holding_at_ms,
            "maximum_exit_at_ms": maximum_exit_at_ms,
            "filled": True,
        }
        if selected is None:
            results.append({
                **base_result,
                "status": "EXIT_EVIDENCE_INCOMPLETE",
                "exit_at_ms": None,
                "exit_price": None,
                "exit_touch_price": None,
                "exit_slippage": None,
                "exit_reason": None,
                "holding_ms": None,
                "gross_return_bps": None,
                "net_return_bps": None,
                "mae_bps": None,
                "mfe_bps": None,
                "resolved": False,
                "evidence_complete": False,
            })
            continue

        exit_index, exit_price, exit_slip_amount, exit_reason = selected
        exit_mark = marks[exit_index]
        exit_at_ms = mark_times[exit_index]
        direction = 1.0 if side == "BUY" else -1.0
        gross_return = (
            (exit_price - entry_price) / entry_price *
            10_000.0 * direction
        )
        path_returns = [
            (price - entry_price) / entry_price * 10_000.0 * direction
            for _, price in path_prices
            if _ <= exit_index
        ]
        if not path_returns:
            raise ContractError("REPLAY_PATH_EVIDENCE_MISSING")
        exit_touch = float(
            exit_mark["bid"] if side == "BUY" else exit_mark["ask"])
        results.append({
            **base_result,
            "status": "CLOSED",
            "exit_at_ms": exit_at_ms,
            "exit_price": exit_price,
            "exit_touch_price": exit_touch,
            "exit_slippage": exit_slip_amount,
            "exit_reason": exit_reason,
            "holding_ms": exit_at_ms - entry_at_ms,
            "gross_return_bps": gross_return,
            "net_return_bps": gross_return - cost,
            "mae_bps": min(path_returns),
            "mfe_bps": max(path_returns),
            "resolved": True,
            "evidence_complete": True,
        })

    resolved_results = [
        result for result in results if result["resolved"]]
    chronological = sorted(
        resolved_results,
        key=lambda result: (
            result["exit_at_ms"], result["decision_id"]))
    net_values = [
        float(result["net_return_bps"]) for result in chronological]
    gross_values = [
        float(result["gross_return_bps"]) for result in chronological]
    trade_candidates = len(results)
    filled_count = sum(bool(result["filled"]) for result in results)
    expired_unfilled = sum(
        result["status"].startswith("NOT_FILLED")
        for result in results)
    unresolved = sum(
        not result["evidence_complete"] for result in results)
    no_trade_count = len(receipts) - trade_candidates
    execution_model = {
        "entry_latency_ms": latency,
        "entry_slippage_bps": entry_slippage,
        "exit_slippage_bps": exit_slippage,
        "round_trip_cost_bps": cost,
        "horizon_seconds": horizon,
        "maximum_exit_delay_seconds": exit_delay_seconds,
        "maximum_holding_seconds": maximum_holding_seconds,
        "allow_overlapping_intents": allow_overlapping_intents,
        "entry_model":
            "FIRST_POST_LATENCY_EXECUTABLE_LIMIT_WITH_ADVERSE_SLIPPAGE",
        "exit_model":
            "EXECUTABLE_BID_ASK_STOP_HORIZON_OR_MAXIMUM_HOLDING",
        "invalidation_model": "ABSOLUTE_MAX_ADVERSE_MOVE",
    }
    body = {
        "schema": "hepta.strategy-replay-report.v3",
        "campaign_id": decisions_document["campaign_id"],
        "strategy_id": decisions_document["strategy_id"],
        "strategy_version": decisions_document["strategy_version"],
        "strategy_sha256": decisions_document["strategy_sha256"],
        "instrument": "EUR.USD",
        "execution_model": execution_model,
        "decision_count": len(receipts),
        "no_trade_count": no_trade_count,
        "trade_candidate_count": trade_candidates,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "filled_count": filled_count,
        "expired_unfilled_count": expired_unfilled,
        "resolved_count": len(resolved_results),
        "unresolved_count": unresolved,
        "average_gross_return_bps": (
            None if not gross_values else statistics.fmean(gross_values)),
        "average_net_return_bps": (
            None if not net_values else statistics.fmean(net_values)),
        "median_net_return_bps": (
            None if not net_values else statistics.median(net_values)),
        "cost_adjusted_hit_rate": (
            None if not net_values else
            sum(value > 0.0 for value in net_values) / len(net_values)),
        "cumulative_net_return_bps": sum(net_values),
        "maximum_drawdown_bps": _maximum_drawdown(net_values),
        "results": results,
        "evidence": {
            "decision_set_body_sha256":
                decisions_document["body_sha256"],
            "mark_set_body_sha256": marks_document["body_sha256"],
            "policy_sha256": decisions_document["policy_sha256"],
            "final_audit_receipt_sha256":
                decisions_document["final_audit_receipt_sha256"],
        },
        "mutation_attempted": False,
        "direct_broker_access": False,
        "live_authorized": False,
    }
    return {**body, "body_sha256": digest_document(body)}


def _evaluate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--marks", type=Path, required=True)
    parser.add_argument("--horizon-seconds", type=int, default=900)
    parser.add_argument("--round-trip-cost-bps", type=float, default=0.8)
    parser.add_argument("--maximum-exit-delay-seconds", type=int, default=30)
    parser.add_argument("--entry-latency-ms", type=int, default=1_000)
    parser.add_argument("--entry-slippage-bps", type=float, default=0.2)
    parser.add_argument("--exit-slippage-bps", type=float, default=0.2)
    parser.add_argument("--maximum-holding-seconds", type=int)
    parser.add_argument(
        "--allow-overlapping-intents", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _seal_decisions_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--final-audit", type=Path, required=True)
    parser.add_argument(
        "--receipt", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _seal_marks_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--final-audit", type=Path, required=True)
    parser.add_argument(
        "--history-directory", type=Path, action="append", required=True)
    parser.add_argument("--cadence-ms", type=int, required=True)
    parser.add_argument(
        "--maximum-jitter-ms",
        type=int,
        default=market_history.DEFAULT_MAXIMUM_JITTER_MS,
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    operation = (
        sys.argv[1] if len(sys.argv) > 1 and
        sys.argv[1] in {"evaluate", "seal-decisions", "seal-marks"}
        else "evaluate"
    )
    arguments_list = (
        sys.argv[2:] if operation != "evaluate" or
        (len(sys.argv) > 1 and sys.argv[1] == "evaluate")
        else sys.argv[1:]
    )
    try:
        if operation == "seal-decisions":
            arguments = _seal_decisions_parser().parse_args(arguments_list)
            document = seal_decision_set(
                arguments.policy,
                arguments.final_audit,
                arguments.receipt,
            )
            atomic_write_json(arguments.output, document)
            print(
                "hepta_strategy_replay_evaluator: PASS "
                f"sealed_receipts={document['receipt_count']}")
            return 0
        if operation == "seal-marks":
            arguments = _seal_marks_parser().parse_args(arguments_list)
            document = seal_mark_set(
                arguments.policy,
                arguments.final_audit,
                arguments.history_directory,
                cadence_ms=arguments.cadence_ms,
                maximum_jitter_ms=arguments.maximum_jitter_ms,
            )
            atomic_write_json(arguments.output, document)
            print(
                "hepta_strategy_replay_evaluator: PASS "
                f"sealed_marks={document['record_count']}")
            return 0
        arguments = _evaluate_parser().parse_args(arguments_list)
        decisions = load_document(
            arguments.decisions, "REPLAY_DECISIONS", maximum_bytes=64 << 20)
        marks = load_document(
            arguments.marks, "REPLAY_MARKS", maximum_bytes=512 << 20)
        report = evaluate_replay(
            decisions,
            marks,
            horizon_seconds=arguments.horizon_seconds,
            round_trip_cost_bps=arguments.round_trip_cost_bps,
            maximum_exit_delay_seconds=
                arguments.maximum_exit_delay_seconds,
            entry_latency_ms=arguments.entry_latency_ms,
            entry_slippage_bps=arguments.entry_slippage_bps,
            exit_slippage_bps=arguments.exit_slippage_bps,
            maximum_holding_seconds=arguments.maximum_holding_seconds,
            allow_overlapping_intents=
                arguments.allow_overlapping_intents,
        )
        atomic_write_json(arguments.output, report)
    except (ContractError, OSError, ValueError) as error:
        print(
            "hepta_strategy_replay_evaluator: FAIL: " + str(error),
            file=sys.stderr,
        )
        return 78
    print(
        "hepta_strategy_replay_evaluator: PASS "
        f"resolved={report['resolved_count']} "
        f"candidates={report['trade_candidate_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
