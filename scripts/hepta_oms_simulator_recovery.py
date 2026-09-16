#!/usr/bin/env python3
"""Bounded simulator recovery projection for segmented OMS generations.

The projection carries only cumulative admitted-order count, next order-id
watermark, non-zero positions, and simulator orders that may still become a
fill.  It never replaces command identity or broker state.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Iterator

import hepta_oms_checkpoint as v1
import hepta_oms_lifecycle_core as core
from verify_oms_journal_replay import validated_raw_records

HEADER = "HEPTA_SIMULATOR_RECOVERY_V1"
FIELD = "simulator_recovery_hex"
SCHEMA_FIELD = "simulator_recovery_schema"
MAX_PAYLOAD = 8 * 1024 * 1024


def new_state() -> dict[str, Any]:
    return {
        "admitted_order_count": 0,
        "next_order_id": 1_000_000,
        "positions": {},
        "active_orders": {},
    }


def _finite_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and \
        math.isfinite(float(value)) and float(value) > 0.0


def apply_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    order_id = event.get("order_id", -1)
    if not isinstance(order_id, int) or isinstance(order_id, bool):
        order_id = -1
    kind = event.get("event", "")
    fill = kind == "status" and event.get("status", "") == "Filled"
    terminal = kind == "status" and event.get("status", "") in {
        "Cancelled", "ApiCancelled", "Inactive", "Rejected"
    }
    simulator = event.get("venue", "") == "SIMULATOR" and event.get("account", "") == "SIM"
    prior_watermark = int(state["next_order_id"]) - 1

    # A generation is shared infrastructure and may contain PAPER/other venue
    # records.  Those records are irrelevant to simulator risk restoration;
    # their existence is not a simulator corruption signal.
    if simulator and (kind == "place_sent" or fill):
        instrument = event.get("instrument", "")
        side = event.get("side", "")
        qty = event.get("qty", 0.0)
        if (order_id < 0 or not isinstance(instrument, str) or not instrument or
                side not in {"BUY", "SELL"} or not _finite_positive(qty)):
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_EVENT_INVALID")
        qty = float(qty)
        active: dict[int, dict[str, Any]] = state["active_orders"]
        if kind == "place_sent":
            observed = {
                "instrument": instrument,
                "side": side,
                "qty": qty,
                "req_id": event.get("req_id", "") if isinstance(event.get("req_id", ""), str) else "",
                "request_hash": event.get("request_hash", "") if isinstance(event.get("request_hash", ""), str) else "",
            }
            prior = active.get(order_id)
            if prior is not None:
                if prior != observed:
                    raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_ADMISSION_CONFLICT")
            else:
                if order_id <= prior_watermark:
                    raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_ORDER_ID_REUSE")
                active[order_id] = observed
                state["admitted_order_count"] = int(state["admitted_order_count"]) + 1
        else:
            owner = active.get(order_id)
            price = event.get("price", 0.0)
            if (owner is None or owner["instrument"] != instrument or
                    owner["side"] != side or float(owner["qty"]) != qty or
                    not _finite_positive(price)):
                raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_FILL_CONFLICT")
            positions: dict[str, float] = state["positions"]
            value = float(positions.get(instrument, 0.0)) + (qty if side == "BUY" else -qty)
            if not math.isfinite(value):
                raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_POSITION_OVERFLOW")
            if value == 0.0:
                positions.pop(instrument, None)
            else:
                positions[instrument] = value
            del active[order_id]
    elif simulator and terminal and order_id >= 0:
        state["active_orders"].pop(order_id, None)

    # Preserve the old simulator service's global journal watermark semantics.
    if order_id >= 0:
        if order_id >= (1 << 63) - 1:
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_ORDER_ID_EXHAUSTED")
        state["next_order_id"] = max(int(state["next_order_id"]), order_id + 1)


def iter_segment_events(path: Path, expected_records: int) -> Iterator[dict[str, Any]]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not v1._private_regular(info):
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_SEGMENT_UNSAFE")
        observed = 0
        for _raw, event in validated_raw_records(
                fd, info.st_size, max_bytes=max(info.st_size, 1),
                max_records=max(expected_records, 1), max_record_bytes=1024 * 1024):
            observed += 1
            yield event
        if observed != expected_records:
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_SEGMENT_COUNT_MISMATCH")
    finally:
        os.close(fd)


def encode_state(state: dict[str, Any]) -> bytes:
    positions = state["positions"]
    active = state["active_orders"]
    lines = [
        HEADER,
        f"admitted_order_count={int(state['admitted_order_count'])}",
        f"next_order_id={int(state['next_order_id'])}",
        f"position_count={len(positions)}",
        f"active_order_count={len(active)}",
    ]
    for instrument, quantity in sorted(positions.items()):
        if not isinstance(instrument, str) or not instrument or not math.isfinite(float(quantity)):
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
        lines.append(f"P\t{v1._hex(instrument)}\t{float(quantity).hex()}")
    for order_id, order in sorted(active.items()):
        quantity = float(order["qty"])
        if (not isinstance(order_id, int) or order_id < 0 or
                order["side"] not in {"BUY", "SELL"} or not _finite_positive(quantity)):
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
        lines.append("\t".join([
            "A", str(order_id), v1._hex(order["instrument"]), order["side"],
            quantity.hex(), v1._hex(order.get("req_id", "")),
            v1._hex(order.get("request_hash", "")),
        ]))
    payload = ("\n".join(lines) + "\n").encode("ascii")
    if len(payload) > MAX_PAYLOAD:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_LIMIT")
    return payload


def decode_state(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_PAYLOAD:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_LIMIT")
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeError as error:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID") from error
    if len(lines) < 5 or lines[0] != HEADER:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
    fields: dict[str, str] = {}
    for line in lines[1:5]:
        key, separator, value = line.partition("=")
        if not separator or not key or key in fields:
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
        fields[key] = value
    if set(fields) != {"admitted_order_count", "next_order_id", "position_count", "active_order_count"}:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
    try:
        admitted = int(fields["admitted_order_count"])
        next_order = int(fields["next_order_id"])
        position_count = int(fields["position_count"])
        active_count = int(fields["active_order_count"])
    except ValueError as error:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID") from error
    if admitted < 0 or next_order < 1 or position_count < 0 or active_count < 0:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
    state = new_state()
    state["admitted_order_count"] = admitted
    state["next_order_id"] = next_order
    cursor = 5
    positions: dict[str, float] = {}
    for _ in range(position_count):
        if cursor >= len(lines):
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
        parts = lines[cursor].split("\t")
        cursor += 1
        if len(parts) != 3 or parts[0] != "P":
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
        try:
            instrument = v1._unhex(parts[1])
            quantity = float.fromhex(parts[2])
        except (ValueError, v1.GenerationError) as error:
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID") from error
        if not instrument or not math.isfinite(quantity) or instrument in positions:
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
        positions[instrument] = quantity
    active: dict[int, dict[str, Any]] = {}
    for _ in range(active_count):
        if cursor >= len(lines):
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
        parts = lines[cursor].split("\t")
        cursor += 1
        if len(parts) != 7 or parts[0] != "A":
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
        try:
            order_id = int(parts[1])
            instrument = v1._unhex(parts[2])
            quantity = float.fromhex(parts[4])
            req_id = v1._unhex(parts[5])
            request_hash = v1._unhex(parts[6])
        except (ValueError, v1.GenerationError) as error:
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID") from error
        if (order_id < 0 or order_id in active or not instrument or
                parts[3] not in {"BUY", "SELL"} or not _finite_positive(quantity)):
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
        active[order_id] = {"instrument": instrument, "side": parts[3], "qty": quantity,
                            "req_id": req_id, "request_hash": request_hash}
    if cursor != len(lines) or admitted < len(active):
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID")
    state["positions"] = positions
    state["active_orders"] = active
    return state


def checkpoint_state(root: Path) -> dict[str, Any] | None:
    checkpoint = v1._load_json_private(root / "checkpoint.json")
    encoded = checkpoint.get(FIELD) if isinstance(checkpoint, dict) else None
    schema = checkpoint.get(SCHEMA_FIELD) if isinstance(checkpoint, dict) else None
    if encoded is None and schema is None:
        return None
    if schema != HEADER or not isinstance(encoded, str) or len(encoded) % 2:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_CHECKPOINT_INVALID")
    try:
        payload = bytes.fromhex(encoded)
    except ValueError as error:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_CHECKPOINT_INVALID") from error
    return decode_state(payload)


def rebuild_parent_state(store: Path, generation: str) -> dict[str, Any]:
    state = new_state()
    chain = core._generation_chain(store, generation)
    base_index = 0
    for index, (_name, manifest) in enumerate(chain):
        if manifest.get("schema") == v1.SCHEMA:
            base_index = index
    for name, manifest in chain[base_index:]:
        if manifest.get("schema") == v1.SCHEMA:
            expected = int(manifest.get("journal_records", 0))
        elif manifest.get("schema") == core.SCHEMA:
            expected = int(manifest.get("segment_records", 0))
        else:
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_PARENT_SCHEMA_UNSUPPORTED")
        for event in iter_segment_events(store / name / "segment-000001.jsonl", expected):
            apply_event(state, event)
    return state


def augment_generation(store: Path, root: Path) -> None:
    manifest = v1._load_json_private(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != core.SCHEMA:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_GENERATION_INVALID")
    parent = manifest.get("parent_generation") or ""
    state = checkpoint_state(store / parent) if parent else None
    if state is None and parent:
        state = rebuild_parent_state(store, parent)
    if state is None:
        state = new_state()
    expected = int(manifest.get("segment_records", 0))
    for event in iter_segment_events(root / "segment-000001.jsonl", expected):
        apply_event(state, event)

    payload = encode_state(state)
    checkpoint = v1._load_json_private(root / "checkpoint.json")
    if not isinstance(checkpoint, dict):
        raise v1.GenerationError("OMS_GENERATION_CHECKPOINT_MISMATCH")
    checkpoint[SCHEMA_FIELD] = HEADER
    checkpoint[FIELD] = payload.hex()
    v1._atomic_json(root / "checkpoint.json", checkpoint)
    checkpoint_size, checkpoint_digest = v1._sha256_file(root / "checkpoint.json")

    runtime_raw = v1._read_private_bytes(root / "runtime-manifest.txt")
    try:
        runtime_lines = runtime_raw.decode("ascii").splitlines()
    except UnicodeError as error:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_METADATA_INVALID") from error
    replaced = 0
    for index, line in enumerate(runtime_lines):
        if line.startswith("checkpoint_sha256="):
            runtime_lines[index] = f"checkpoint_sha256={checkpoint_digest}"
            replaced += 1
    if replaced != 1:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_INVALID")
    v1._atomic_bytes(root / "runtime-manifest.txt",
                     ("\n".join(runtime_lines) + "\n").encode("ascii"))
    runtime_size, runtime_digest = v1._sha256_file(root / "runtime-manifest.txt")

    files = manifest.get("files")
    if not isinstance(files, dict) or "checkpoint.json" not in files or "runtime-manifest.txt" not in files:
        raise v1.GenerationError("OMS_GENERATION_FILE_INVENTORY_INVALID")
    files["checkpoint.json"] = {"bytes": checkpoint_size, "sha256": checkpoint_digest}
    files["runtime-manifest.txt"] = {"bytes": runtime_size, "sha256": runtime_digest}
    manifest["runtime_manifest_sha256"] = runtime_digest
    v1._atomic_json(root / "manifest.json", manifest)
    v1._durable_directory(root)
